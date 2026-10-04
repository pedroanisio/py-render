"""Compare schema-valid repeated lights/shadows with equivalent summed intensity.

Run with .venv/bin/python from the repository. Results and validated XML are
written under /tmp/scene-render-light-inspection. Zero exit means probes ran;
pixel differences report remaining conformance gaps, not expected behavior.
"""
from pathlib import Path
import json
import sys

import numpy as np
from lxml import etree

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from scenerender.render import Renderer

ROOT = Path('/tmp/scene-render-light-inspection')
ROOT.mkdir(exist_ok=True)
XSD = etree.XMLSchema(etree.parse(str(REPO / 'schema/scene-render-1.1.xsd')))


def render(name, lights):
    xml = f'''<scene version="1.1">
    <project width="120" height="72" fps="10" duration="1"/>
    <materials><material id="w" baseColor="#C0C0C0FF" roughness=".9"/></materials>
    <composition>
      <camera id="c" y="300" z="500" target="t"/>
      <object3D id="t" primitive="sphere" radius="1" visible="false"/>
      <object3D id="floor" primitive="plane" width="1200" height="1200"
        rotationX="-90" material="w" castShadow="false"/>
      <object3D id="block" primitive="box" width="80" height="80" depth="80"
        y="120" material="w"/>
    </composition>
    <lights>{lights}</lights></scene>'''
    path = ROOT / name
    path.write_text(xml)
    XSD.assertValid(etree.parse(str(path)))
    renderer = Renderer.open(str(path), strict=True)
    return renderer.rc.render_frame(0.).px


def compare(count, shadow):
    attrs = f'type="directional" pitch="-70" castShadow="{str(shadow).lower()}" shadowMapSize="128"'
    actual = render(f'lights-{count}-shadow-{shadow}.xml', ''.join(
        f'<light id="l{i}" {attrs} intensity=".3"/>' for i in range(count)))
    expected = render(f'control-{count}-shadow-{shadow}.xml', f'<light id="l" {attrs} intensity="{count*.3}"/>')
    difference = np.abs(actual-expected)
    return {'count': count, 'shadows': shadow, 'max_abs_difference': float(difference.max()),
            'channels_different_over_1e-5': int((difference > 1e-5).sum()),
            'total_radiance_ratio': float(actual[..., :3].sum()/expected[..., :3].sum())}


def compare_dome_shadow_flags():
    environment = REPO / 'tests/fixtures/media/3d/env_sky.npy'
    attrs = f'type="dome" environment="{environment}" affectsSpecular="false" shadowMapSize="128"'
    red = f'<light id="red" {attrs} color="#FF0000FF" castShadow="true"/>'
    blue = f'<light id="blue" {attrs} color="#0000FFFF" castShadow="false"/>'
    actual = render('dome-shadow-flags.xml', red+blue)[..., :3]
    expected = render('dome-red-shadow.xml', red)[..., :3] + render('dome-blue-no-shadow.xml', blue)[..., :3]
    difference = np.abs(actual-expected)
    return {'max_abs_difference': float(difference.max()),
            'channels_different_over_1e-5': int((difference > 1e-5).sum()),
            'blue_max_abs_difference': float(difference[..., 2].max())}


if __name__ == '__main__':
    result = {'direct_light_count': compare(17, False), 'direct_shadow_count': compare(8, True),
              'individual_dome_shadow_flags': compare_dome_shadow_flags()}
    (ROOT / 'results.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
