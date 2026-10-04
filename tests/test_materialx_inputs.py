"""MaterialX node defaults, typed inputs and independently sampled textures."""
from pathlib import Path
from types import SimpleNamespace
import math

import MaterialX as mx
import numpy as np
import pytest

from scenerender import gl
from scenerender.evaluator import Ctx
from scenerender.render import Renderer
from scenerender.three.materials import evaluate, from_spec, load_materialx, map_uvs, sample_map
from scenerender.three.materialx_inputs import library


def material(root, category='standard_surface', inputs='', nodes='', *, name='test', version='', attrs=''):
    path = root / (name+'.mtlx')
    path.write_text(f'''<materialx version="1.39" {attrs}>
      {nodes}<{category} name="S" type="surfaceshader" {version}>{inputs}</{category}>
      <surfacematerial name="M" type="material">
        <input name="surfaceshader" type="surfaceshader" nodename="S"/>
      </surfacematerial></materialx>''')
    document = mx.createDocument()
    mx.readFromXmlFile(document, str(path)); document.setDataLibrary(library())
    valid, message = document.validate()
    assert valid, message
    return path


def read(path):
    spec = load_materialx(SimpleNamespace(cache={}), str(path))
    assert spec is not None
    return from_spec(spec)


def renderer(path, attrs='', object_attrs='', lighting='<light id="l" type="directional"/>', material_animation='', under=''):
    if not gl.available():
        pytest.skip('no GL')
    scene = path.with_suffix('.xml')
    scene.write_text(f'''<scene version="1.1">
      <project width="80" height="80" fps="24" duration="2"/>
      <materials><material id="m" materialX="{path}" {attrs}>{material_animation}</material></materials>
      <composition><camera id="c" z="200" projection="orthographic" orthoHeight="80"/>
      {under}
      <object3D id="o" primitive="plane" width="64" height="64" material="m" {object_attrs}/>
      </composition><lights>{lighting}</lights></scene>''')
    return Renderer.open(str(scene), strict=True)


def inp(name, kind, value):
    if isinstance(value, (tuple, list, np.ndarray)):
        value = ', '.join(str(v) for v in value)
    return f'<input name="{name}" type="{kind}" value="{value}"/>'


def texture(root, pixels, name='image', kind='color3', extra='', node='image'):
    np.save(root / (name+'.npy'), np.asarray(pixels, np.float32))
    return f'<{node} name="{name}" type="{kind}">{inp("file", "filename", name+".npy")}{extra}</{node}>'


@pytest.mark.parametrize('category,expected', [
    ('standard_surface', {'baseColor': (.8, .8, .8, 1.), 'roughness': .2, 'metallic': 0.,
                          'clearcoatRoughness': .1, 'emissiveStrength': 0.}),
    ('open_pbr_surface', {'baseColor': (.8, .8, .8, 1.), 'roughness': .3, 'metallic': 0.,
                          'sheenRoughness': .5, 'iridescenceIor': 1.4}),
    ('gltf_pbr', {'roughness': 1., 'metallic': 1., 'alphaMode': 'opaque'}),
    ('UsdPreviewSurface', {'baseColor': (.18, .18, .18, 1.), 'roughness': .5,
                          'clearcoatRoughness': .01, 'surfaceModel': 'usdPreviewSurface'}),
])
def test_shader_specific_defaults(tmp_path, category, expected):
    actual = read(material(tmp_path, category))
    for key, value in expected.items():
        assert actual.p[key] == (value if isinstance(value, str) else pytest.approx(value))


def test_node_version_and_authored_overrides_take_precedence(tmp_path):
    path = material(tmp_path, version='version="1.0.0"', inputs=inp('specular_roughness', 'float', .35))
    actual = read(path)
    assert actual.p['baseColor'] == (1., 1., 1., 1.)
    assert actual.p['roughness'] == pytest.approx(.35)


@pytest.mark.parametrize('opacity', [(.5, .5, .5), (.1, .7, .3), (0., 0., 0.), (1., 1., 1.)])
def test_color_opacity_renders_luminance_coverage(tmp_path, opacity):
    path = material(tmp_path, inputs=inp('opacity', 'color3', opacity)+inp('base_color', 'color3', (.2, .4, .6)))
    r = renderer(path, 'unlit="true"')
    expected = float(np.dot(opacity, [.2126, .7152, .0722]))
    np.testing.assert_allclose(r.rc.render_frame(0).px[40, 40], np.r_[np.array([.2, .4, .6])*expected, expected], atol=2e-6)


@pytest.mark.parametrize('mode,alpha', [(0, 1.), (1, 0.), (2, .3)])
def test_gltf_alpha_modes_and_native_override(tmp_path, mode, alpha):
    path = material(tmp_path, 'gltf_pbr', inp('alpha', 'float', .3)+inp('alpha_mode', 'integer', mode))
    actual = renderer(path, 'unlit="true"').rc.render_frame(0).px[40, 40]
    np.testing.assert_allclose(actual, [alpha]*4, atol=1e-6)
    override = renderer(path, 'unlit="true" alphaMode="opaque"').rc.render_frame(0).px[40, 40]
    np.testing.assert_allclose(override, [1.]*4, atol=1e-6)


def test_different_roughness_metallic_images_and_coordinates_render_independently(tmp_path):
    rough = np.linspace(.25, .8, 15).reshape(3, 5, 1)
    metal = np.linspace(.1, .9, 14).reshape(7, 2, 1)
    nodes = texture(tmp_path, rough, 'R', 'float', inp('texcoord', 'vector2', (.3, .2)))
    nodes += texture(tmp_path, metal, 'Mtex', 'float', inp('texcoord', 'vector2', (.8, .7)))
    path = material(tmp_path, 'gltf_pbr', '''<input name="roughness" type="float" nodename="R"/>
        <input name="metallic" type="float" nodename="Mtex"/>''', nodes)
    actual = read(path)
    assert actual.maps['roughnessMap'].shape == (3, 5, 4)
    assert actual.maps['metallicMap'].shape == (7, 2, 4)
    r = sample_map(rough, np.array([[.3, .8]]))[0, 0]
    m = sample_map(metal, np.array([[.8, .3]]))[0, 0]
    control = material(tmp_path, 'gltf_pbr', inp('roughness', 'float', r)+inp('metallic', 'float', m), name='control')
    np.testing.assert_allclose(renderer(path).rc.render_frame(0).px,
                               renderer(control).rc.render_frame(0).px, atol=3e-6)


@pytest.mark.parametrize('s', ['periodic', 'mirror', 'clamp', 'constant'])
@pytest.mark.parametrize('t', ['periodic', 'mirror', 'clamp', 'constant'])
def test_image_wraps_use_their_own_default_border(tmp_path, s, t):
    pixels = np.random.default_rng(42).random((3, 5, 3))
    extra = inp('texcoord', 'vector2', (-.05, 1.1))+inp('uaddressmode', 'string', s)+inp('vaddressmode', 'string', t)
    extra += inp('default', 'color3', (.3, .4, .5))
    path = material(tmp_path, inputs='<input name="base_color" type="color3" nodename="image"/>',
                    nodes=texture(tmp_path, pixels, extra=extra))
    modes = {'periodic': 10497, 'mirror': 33648, 'clamp': 33071, 'constant': 33069}
    expected = sample_map(pixels, np.array([[-.05, -.1]]),
                          {'wrapS': modes[s], 'wrapT': modes[t], 'border': (.3, .4, .5)})[0]
    actual = renderer(path, 'unlit="true"').rc.render_frame(0).px[40, 40, :3]
    np.testing.assert_allclose(actual, expected, atol=2e-6)


@pytest.mark.parametrize('axis', [0, 1])
@pytest.mark.parametrize('coordinate', [-.1, .01, .99, 1.1])
def test_nearest_constant_border_never_blends_with_edge_texels(tmp_path, axis, coordinate):
    uv = [.5, .5]; uv[axis] = coordinate
    extra = inp('texcoord', 'vector2', uv)+inp('filtertype', 'string', 'closest')
    extra += inp('uaddressmode', 'string', 'constant')+inp('vaddressmode', 'string', 'constant')
    extra += inp('default', 'color3', (.2, .2, .2))
    path = material(tmp_path, inputs='<input name="base_color" type="color3" nodename="image"/>',
                    nodes=texture(tmp_path, np.ones((4, 4, 3)), extra=extra))
    result = renderer(path, 'unlit="true"').rc.render_frame(0).px[40, 40]
    expected = .2 if coordinate < 0 or coordinate > 1 else 1.
    np.testing.assert_allclose(result, [expected, expected, expected, 1.], atol=2e-6)


@pytest.mark.parametrize('coordinate,alpha', [(-.1, .2), (.5, 1.)])
def test_nearest_opacity_border_matches_main_and_shadow_depth(tmp_path, coordinate, alpha):
    inputs = inp('alpha_mode', 'integer', 1)+inp('alpha_cutoff', 'float', .25)
    extra = inp('texcoord', 'vector2', (coordinate, .5))+inp('filtertype', 'string', 'closest')
    extra += inp('uaddressmode', 'string', 'constant')+inp('default', 'float', .2)
    actual = material(tmp_path, 'gltf_pbr', inputs+'<input name="alpha" type="float" nodename="image"/>',
                      texture(tmp_path, np.ones((4, 4, 1)), kind='float', extra=extra))
    control = material(tmp_path, 'gltf_pbr', inputs+inp('alpha', 'float', alpha), name='control')
    lights = '<light id="l" type="directional" yaw="20" castShadow="true" shadowMapSize="128"/>'
    back = '<object3D id="back" primitive="plane" width="80" height="80" z="-30"/>'
    a, b = [renderer(path, lighting=lights, under=back).rc.render_frame(0).px for path in (actual, control)]
    np.testing.assert_allclose(a, b, atol=3e-6)


@pytest.mark.parametrize('order', [0, 1])
def test_place2d_and_tiledimage_coordinates_reach_native_primitive(tmp_path, order):
    pixels = np.random.default_rng(23).random((5, 7, 3))
    nodes = '<texcoord name="uv" type="vector2"/>'
    nodes += '<place2d name="place" type="vector2"><input name="texcoord" type="vector2" nodename="uv"/>'
    nodes += inp('pivot', 'vector2', (.2, .3))+inp('scale', 'vector2', (2., .6))
    nodes += inp('rotate', 'float', 30)+inp('offset', 'vector2', (.1, -.2))+inp('operationorder', 'integer', order)+'</place2d>'
    extra = '<input name="texcoord" type="vector2" nodename="place"/>'
    extra += inp('uvtiling', 'vector2', (2., 3.))+inp('uvoffset', 'vector2', (.2, .3))
    extra += inp('realworldimagesize', 'vector2', (4., 2.))
    path = material(tmp_path, inputs='<input name="base_color" type="color3" nodename="image"/>',
                    nodes=nodes+texture(tmp_path, pixels, extra=extra, node='tiledimage'))
    result = renderer(path, 'unlit="true"').rc.render_frame(0).px
    rotation = np.array([[math.cos(math.pi/6), math.sin(math.pi/6)], [-math.sin(math.pi/6), math.cos(math.pi/6)]])
    for y, x in [(25, 32), (40, 40), (52, 45)]:
        uv = np.array([(x+.5-8)/64, 1-(y+.5-8)/64])
        uv -= [.2, .3]
        if order == 0:
            uv = rotation @ (uv/[2., .6]) - [.1, -.2]
        else:
            uv = (rotation @ (uv-[.1, -.2]))/[2., .6]
        uv += [.2, .3]
        uv = (uv*[2., 3.]-[.2, .3])/[4., 2.]
        uv[1] = 1-uv[1]
        expected = sample_map(pixels, uv[None])[0]
        np.testing.assert_allclose(result[y, x, :3], expected, atol=3e-6)


def test_texcoord_set_index_is_retained(tmp_path):
    nodes = '<texcoord name="uv" type="vector2">'+inp('index', 'integer', 1)+'</texcoord>'
    nodes += texture(tmp_path, np.ones((2, 2, 3)), extra='<input name="texcoord" type="vector2" nodename="uv"/>')
    m = read(material(tmp_path, inputs='<input name="base_color" type="color3" nodename="image"/>', nodes=nodes))
    uv = np.array([[.1, .3], [.2, .4]], np.float32)
    item = SimpleNamespace(uv_sets={1: uv}, uvs=np.zeros_like(uv), positions=np.zeros((2, 3)))
    np.testing.assert_array_equal(map_uvs(item, m)['baseColorMap'], uv)


@pytest.mark.parametrize('space,expected', [('', .5), ('data', .5), ('lin_rec709', .5),
                                           ('srgb_texture', .21404114), ('srgb_rec709_scene', .21404114),
                                           ('g18_rec709_scene', .5**1.8), ('g24_rec709_scene', .5**2.4)])
def test_declared_color_space_controls_constants_and_images(tmp_path, space, expected):
    attrs = f'colorspace="{space}"' if space else ''
    constant = material(tmp_path, inputs=inp('base_color', 'color3', (.5,)*3), attrs=attrs)
    textured = material(tmp_path, inputs='<input name="base_color" type="color3" nodename="image"/>',
                        nodes=texture(tmp_path, np.full((3, 5, 3), .5)), attrs=attrs, name='tex')
    np.testing.assert_allclose(read(constant).p['baseColor'][:3], [expected]*3, atol=1e-7)
    np.testing.assert_allclose(read(textured).maps['baseColorMap'][..., :3], expected, atol=1e-7)


def test_missing_image_uses_default_in_its_own_color_space(tmp_path):
    nodes = '<image name="image" type="color3">'+inp('file', 'filename', 'missing.png')
    nodes += '<input name="default" type="color3" value=".5, .5, .5" colorspace="lin_rec709"/>'
    nodes += inp('texcoord', 'vector2', (3., -2.))+'</image>'
    path = material(tmp_path, inputs='<input name="base_color" type="color3" nodename="image"/>', nodes=nodes,
                    attrs='colorspace="srgb_texture"')
    np.testing.assert_allclose(renderer(path, 'unlit="true"').rc.render_frame(0).px[40, 40], [.5, .5, .5, 1.], atol=1e-6)


def test_sixteen_bit_channel_precision_and_missing_component_zero_fill(tmp_path):
    import png
    path = tmp_path / 'image.png'
    with path.open('wb') as stream:
        png.Writer(width=2, height=1, greyscale=True, bitdepth=16).write(stream, [[32768, 32769]])
    nodes = '<image name="image" type="color3">'+inp('file', 'filename', 'image.png')+'</image>'
    m = read(material(tmp_path, inputs='<input name="base_color" type="color3" nodename="image"/>', nodes=nodes))
    np.testing.assert_array_equal(m.maps['baseColorMap'][0, :, 0], np.array([32768, 32769], np.float32)/65535)
    np.testing.assert_array_equal(m.maps['baseColorMap'][..., 1:3], 0.)


def test_channel_extraction_nodegraph_interface_and_file_prefix(tmp_path):
    (tmp_path / 'maps').mkdir()
    np.save(tmp_path / 'maps/image.npy', np.array([[[.2, .4, .6, .8]]], np.float32))
    nodes = '''<nodegraph name="NG" fileprefix="maps/">
      <input name="file" type="filename" value="image.npy"/>
      <image name="image" type="color4"><input name="file" type="filename" interfacename="file"/></image>
      <extract name="alpha" type="float"><input name="in" type="color4" nodename="image"/>
        <input name="index" type="integer" value="3"/></extract>
      <output name="result" type="float" nodename="alpha"/></nodegraph>'''
    path = material(tmp_path, 'gltf_pbr', '<input name="roughness" type="float" nodegraph="NG" output="result"/>', nodes)
    np.testing.assert_allclose(read(path).maps['roughnessMap'], .8, atol=1e-7)


def test_constant_world_normal_is_not_rotated_with_the_primitive(tmp_path):
    path = material(tmp_path, inputs=inp('normal', 'vector3', (.3, 0., math.sqrt(.91))))
    r = renderer(path).rc.render_frame(0).px[40, 40]
    rotated = renderer(path, object_attrs='rotationY="45"').rc.render_frame(0).px[40, 40]
    np.testing.assert_allclose(rotated, r, atol=1e-6)


def test_native_texture_override_discards_imported_sampling_metadata(tmp_path):
    pixels = np.random.default_rng(12).random((3, 5, 3)).astype(np.float32)
    nodes = texture(tmp_path, pixels, extra=inp('texcoord', 'vector2', (.1, .2)))
    path = material(tmp_path, inputs='<input name="base_color" type="color3" nodename="image"/>', nodes=nodes)
    r = renderer(path, 'baseColorMap="image.npy" unlit="true"')
    m = evaluate(r.rc, r.doc.ids['m'], Ctx(0., 0.))
    assert 'baseColorMap' not in m.p['mapUV']
    assert 'baseColorMap' not in m.p['mapSamplers']


@pytest.mark.parametrize('scale', [(1., 1.), (.25, 2.)])
@pytest.mark.parametrize('encoded', [(.6, .7, .9), (0., 0., 0.)])
def test_normalmap_retains_geometric_frame_and_vector_scale(tmp_path, scale, encoded):
    nodes = '<texcoord name="uv" type="vector2"/>'
    nodes += '<place2d name="place" type="vector2"><input name="texcoord" type="vector2" nodename="uv"/>'
    nodes += inp('rotate', 'float', 90)+'</place2d>'
    nodes += texture(tmp_path, np.array(encoded)[None, None], kind='vector3',
                     extra='<input name="texcoord" type="vector2" nodename="place"/>')
    nodes += '<normalmap name="normal" type="vector3"><input name="in" type="vector3" nodename="image"/>'
    nodes += inp('scale', 'vector2', scale)+'</normalmap>'
    path = material(tmp_path, inputs='<input name="normal" type="vector3" nodename="normal"/>', nodes=nodes)
    normal = np.array(encoded)*2-1 if any(encoded) else np.array([0., 0., 1.])
    normal[:2] *= scale
    normal /= np.linalg.norm(normal)
    angle = math.radians(25)
    # The native primitive's Y rotation transforms its geometric tangent frame.
    normal = np.array([[math.cos(angle), 0., math.sin(angle)], [0., 1., 0.],
                       [-math.sin(angle), 0., math.cos(angle)]]) @ normal
    control = material(tmp_path, inputs=inp('normal', 'vector3', normal), name='control')
    actual = renderer(path, object_attrs='rotationY="25"').rc.render_frame(0).px[40, 40]
    expected = renderer(control, object_attrs='rotationY="25"').rc.render_frame(0).px[40, 40]
    np.testing.assert_allclose(actual, expected, atol=3e-6)


@pytest.mark.parametrize('mode', [0, 1])
@pytest.mark.parametrize('workflow', [0, 1])
def test_materialx_preview_surface_matches_usd_material(tmp_path, mode, workflow):
    from test_usd_materials import Scene
    from test_usd_shading import FRONT, set_input
    usd = Scene(tmp_path, 'usd')
    values = {'diffuseColor': (.4, .3, .2), 'roughness': .6, 'metallic': .3,
              'specularColor': (.2, .3, .4), 'useSpecularWorkflow': workflow,
              'emissiveColor': (.01, .02, .03), 'opacity': .4,
              'ior': 2.4, 'clearcoat': .5, 'clearcoatRoughness': .4}
    inputs, nodes = '', ''
    for name, value in values.items():
        set_input(usd, name, value)
        if name in ('ior', 'clearcoat', 'clearcoatRoughness'):
            nodes += texture(tmp_path, np.full((2, 3, 1), value), name, 'float')
            inputs += f'<input name="{name}" type="float" nodename="{name}"/>'
        else:
            kind = 'integer' if name == 'useSpecularWorkflow' else 'color3' if isinstance(value, tuple) else 'float'
            inputs += inp(name, kind, value)
    inputs += inp('opacityMode', 'integer', mode)
    set_input(usd, 'opacityMode', 'presence' if mode else 'transparent')
    path = material(tmp_path, 'UsdPreviewSurface', inputs, nodes)
    np.testing.assert_allclose(renderer(path).rc.render_frame(0).px,
                               usd.renderer(lights=FRONT).rc.render_frame(0).px, atol=3e-6)


def test_native_combined_map_override_keeps_usd_scalar_atlas(tmp_path):
    nodes = texture(tmp_path, np.full((3, 5, 1), 2.4), name='ior', kind='float')
    nodes += texture(tmp_path, np.full((2, 4, 1), .3), name='rough', kind='float')
    inputs = '<input name="ior" type="float" nodename="ior"/><input name="roughness" type="float" nodename="rough"/>'
    path = material(tmp_path, 'UsdPreviewSurface', inputs, nodes)
    np.save(tmp_path/'mr.npy', np.array([[[1., .4, .6, 1.]]], np.float32))
    r = renderer(path, 'metallicRoughnessMap="mr.npy" roughness="1" metallic="1"')
    m = evaluate(r.rc, r.doc.ids['m'], Ctx(0., 0.))
    assert 'metallicRoughnessMap' not in m.maps
    control = material(tmp_path, 'UsdPreviewSurface', inp('ior', 'float', 2.4)+inp('roughness', 'float', .4)
                       +inp('metallic', 'float', .6), name='control')
    np.testing.assert_allclose(r.rc.render_frame(0).px, renderer(control).rc.render_frame(0).px, atol=3e-6)


@pytest.mark.parametrize('filtering,height', [('closest', 0.), ('linear', 2.4)])
def test_displacement_uses_its_coordinates_and_filter(tmp_path, filtering, height):
    from scenerender.three.geometry import plane
    from scenerender.three.scene import _displace
    pixels = np.array([[[0.], [2.]], [[4.], [6.]]])
    extra = inp('texcoord', 'vector2', (.45, .55))+inp('filtertype', 'string', filtering)
    path = material(tmp_path, 'UsdPreviewSurface', '<input name="displacement" type="float" nodename="image"/>',
                    texture(tmp_path, pixels, kind='float', extra=extra))
    m = read(path)
    g = plane(64, 64, 1)
    uv = map_uvs(g, m)['displacementMap']
    positions, _ = _displace(g.positions, g.normals, uv, g.indices, m)
    np.testing.assert_allclose(positions[:, 2], height, atol=1e-6)


def test_animated_materialx_selector_updates_primitive_uvs_with_backward_seeks(tmp_path):
    pixels = np.random.default_rng(9).random((3, 5, 3))
    paths = []
    for name, uv in [('a', (.2, .3)), ('b', (.8, .7))]:
        nodes = texture(tmp_path, pixels, extra=inp('texcoord', 'vector2', uv))
        paths.append(material(tmp_path, inputs='<input name="base_color" type="color3" nodename="image"/>',
                              nodes=nodes, name=name))
    controls = [renderer(p, 'unlit="true"').rc.render_frame(0).px.copy() for p in paths]
    assert not np.allclose(*controls)
    keys = f'<animate property="materialX"><key time="0" value="{paths[0]}"/><key time="1" value="{paths[1]}"/></animate>'
    r = renderer(paths[0], 'unlit="true"', material_animation=keys)
    for time, index in [(1.25, 1), (.25, 0), (1.25, 1)]:
        np.testing.assert_allclose(r.rc.render_frame(time).px, controls[index], atol=2e-6)


@pytest.mark.parametrize('via_normalmap', [False, True])
def test_preview_surface_uses_geometry_frame_for_its_normal_graph(tmp_path, via_normalmap):
    normal = np.array([.2, .4, .8])
    pixels = normal*.5+.5 if via_normalmap else normal
    nodes = '<texcoord name="uv" type="vector2"/>'
    nodes += '<place2d name="place" type="vector2"><input name="texcoord" type="vector2" nodename="uv"/>'
    nodes += inp('rotate', 'float', 90)+'</place2d>'
    nodes += texture(tmp_path, pixels[None, None], kind='vector3',
                     extra='<input name="texcoord" type="vector2" nodename="place"/>')
    source = 'image'
    if via_normalmap:
        nodes += '<normalmap name="normal" type="vector3"><input name="in" type="vector3" nodename="image"/>'
        nodes += inp('scale', 'vector2', (.25, 2.))+'</normalmap>'
        source = 'normal'
        normal[:2] *= [.25, 2.]
        normal /= np.linalg.norm(normal)
        c, s = math.cos(math.radians(25)), math.sin(math.radians(25))
        normal = np.array([[c, 0., s], [0., 1., 0.], [-s, 0., c]]) @ normal
    path = material(tmp_path, 'UsdPreviewSurface', f'<input name="normal" type="vector3" nodename="{source}"/>', nodes)
    control = material(tmp_path, 'UsdPreviewSurface', inp('normal', 'vector3', normal), name='control')
    expected = renderer(control, object_attrs='rotationY="25"').rc.render_frame(0).px[40, 40]
    assert expected[:3].sum() > .01
    np.testing.assert_allclose(renderer(path, object_attrs='rotationY="25"').rc.render_frame(0).px[40, 40], expected, atol=3e-6)


@pytest.mark.parametrize('handedness', [-1., 1.])
def test_normalmap_preserves_authored_mesh_tangent(tmp_path, handedness):
    import pygltflib as gt
    from test_3d_loaders import Builder
    from test_3d_render import doc
    b = Builder()
    positions = b.add([[-32, -32, 0], [32, -32, 0], [32, 32, 0], [-32, 32, 0]], 'VEC3')
    normals = b.add([[0, 0, 1]]*4, 'VEC3')
    tangents = b.add([[0, 1, 0, handedness]]*4, 'VEC4')
    uv = b.add([[0, 1], [1, 1], [1, 0], [0, 0]], 'VEC2')
    indices = b.add([0, 1, 2, 0, 2, 3], 'SCALAR', gt.UNSIGNED_SHORT)
    b.g.meshes = [gt.Mesh(primitives=[gt.Primitive(indices=indices,
        attributes=gt.Attributes(POSITION=positions, NORMAL=normals, TANGENT=tangents, TEXCOORD_0=uv))])]
    b.g.nodes, b.g.scenes = [gt.Node(mesh=0)], [gt.Scene(nodes=[0])]
    mesh = b.save(tmp_path/'mesh.gltf')
    nodes = texture(tmp_path, np.array([[[.6, .7, .9]]]), kind='vector3')
    nodes += '<normalmap name="normal" type="vector3"><input name="in" type="vector3" nodename="image"/></normalmap>'
    path = material(tmp_path, inputs='<input name="normal" type="vector3" nodename="normal"/>', nodes=nodes)
    lights = '<light id="l" type="directional" yaw="-35" pitch="20"/>'
    body = '<camera id="c" z="200" projection="orthographic" orthoHeight="80"/><object3D id="o" primitive="mesh" mesh="mesh" material="m"/>'
    actual = doc(tmp_path, body, w=80, h=80, materials=f'<material id="m" materialX="{path}"/>',
                 assets=f'<mesh id="mesh" src="{mesh}" format="gltf"/>', lights=lights).rc.render_frame(0).px[40, 40]
    normal = np.array([.4*handedness, .2, .8]); normal /= np.linalg.norm(normal)
    control = material(tmp_path, inputs=inp('normal', 'vector3', normal), name='control')
    expected = renderer(control, lighting=lights).rc.render_frame(0).px[40, 40]
    assert expected[:3].sum() > .01
    np.testing.assert_allclose(actual, expected, atol=3e-6)
