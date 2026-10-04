"""Retain MaterialX import counterexamples against the installed node definitions.

This checks imported values, not independent rendered BRDF conformance. A zero
exit code means the probe ran; inspect each observation for repaired or open cases.
"""
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import MaterialX as mx
import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from scenerender.three.materials import _MX_MAP, from_spec, load_materialx

ROOT = Path('/tmp/scene-render-materialx-inspection')
ROOT.mkdir(exist_ok=True)


def scene(name, category, inputs=''):
    path = ROOT / (name+'.mtlx')
    path.write_text(f'''<materialx version="1.39">
      <{category} name="shader" type="surfaceshader">{inputs}</{category}>
      <surfacematerial name="material" type="material">
        <input name="surfaceshader" type="surfaceshader" nodename="shader"/>
      </surfacematerial></materialx>''')
    return path


def read(path):
    return load_materialx(SimpleNamespace(cache={}), str(path))


def main():
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    results = {'materialx_version': mx.__version__, 'default_inputs': {}, 'unmapped_node_inputs': {},
               'mapping_evidence_limit': 'The missing-name list is a source-review lead. Recognized input names do not prove correct shader-model or graph semantics.'}
    for category, pairs in {
        'standard_surface': [('specular_roughness', 'roughness')],
        'open_pbr_surface': [('base_color', 'baseColor'), ('specular_roughness', 'roughness')],
        'gltf_pbr': [('metallic', 'metallic'), ('roughness', 'roughness')],
        'UsdPreviewSurface': [('diffuseColor', 'baseColor'), ('clearcoatRoughness', 'clearcoatRoughness')],
    }.items():
        path = scene(category, category)
        document = mx.createDocument()
        mx.readFromXmlFile(document, str(path))
        document.setDataLibrary(library)
        definition = document.getNode('shader').getNodeDef()
        results['unmapped_node_inputs'][category] = [i.getName() for i in definition.getActiveInputs()
            if i.getName() not in _MX_MAP and i.getName() not in ('normal', 'geometry_normal')]
        spec = read(path)
        material = from_spec(spec)
        rows = []
        for source, target in pairs:
            expected = definition.getActiveInput(source).getValue()
            if hasattr(expected, 'asTuple'):
                expected = list(expected.asTuple())
                actual = list(material.p[target][:len(expected)])
            else:
                actual = material.p[target]
            rows.append({'input': source, 'renderer_parameter': target, 'expected': expected,
                         'actual': actual, 'matches': bool(np.allclose(actual, expected))})
        results['default_inputs'][category] = {'definition': definition.getName(), 'values': rows}

    path = scene('color_opacity', 'standard_surface',
                 '<input name="opacity" type="color3" value="0.5, 0.5, 0.5"/>')
    opacity = read(path)
    results['color_opacity'] = {'material_loaded': opacity is not None,
                                'expected_material_loaded': True,
                                'opacity': opacity.params.get('opacity') if opacity else None,
                                'alpha_mode': opacity.params.get('alphaMode') if opacity else None,
                                'expected_opacity': .5, 'expected_alpha_mode': 'blend'}

    from PIL import Image
    Image.fromarray(np.full((3, 5, 3), 128, np.uint8)).save(ROOT / 'rough.png')
    Image.fromarray(np.full((7, 2, 3), 64, np.uint8)).save(ROOT / 'metal.png')
    path = scene('different_texture_dimensions', 'gltf_pbr', '''
      <input name="roughness" type="float" nodename="rough"/>
      <input name="metallic" type="float" nodename="metal"/>''')
    xml = path.read_text().replace('</materialx>', '''
      <image name="rough" type="float"><input name="file" type="filename" value="rough.png"/></image>
      <image name="metal" type="float"><input name="file" type="filename" value="metal.png"/></image>
      </materialx>''')
    path.write_text(xml)
    spec = read(path)
    results['different_texture_dimensions'] = {
        'material_loaded': spec is not None,
        'imported_texture_keys': sorted(spec.textures) if spec else [],
        'imported_texture_shapes': {k: list(v.shape) for k, v in spec.textures.items()} if spec else {},
        'expected': 'Both independently sized roughness and metallic textures retained',
    }
    results['validated_documents'] = []
    for name in [*results['default_inputs'], 'color_opacity', 'different_texture_dimensions']:
        document = mx.createDocument()
        mx.readFromXmlFile(document, str(ROOT / (name+'.mtlx')))
        document.setDataLibrary(library)
        valid, message = document.validate()
        assert valid, f'{name}: {message}'
        results['validated_documents'].append(name+'.mtlx')
    output = ROOT / 'results.json'
    output.write_text(json.dumps(results, indent=2)+'\n')
    print(output.read_text())


if __name__ == '__main__':
    main()
