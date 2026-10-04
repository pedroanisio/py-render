"""glTF extension inputs: typed channels, independent sampling and rendered factors."""
import copy
import math

import numpy as np
import pygltflib as gt
import pytest

from scenerender.three.loaders import _Gltf
from scenerender.three.material_atlas import ATLAS_MAP_KEYS, META_STRIDE, pack_material_maps
from scenerender.three.materials import from_spec
from scenerender.raster import srgb_to_linear
from test_3d_loaders import Builder
from test_3d_render import doc, frame


# extension, texture field, internal key, factor field, sampled channel, sRGB
CASES = [
    ('clearcoat', 'clearcoatTexture', 'clearcoatMap', 'clearcoatFactor', 0, False),
    ('clearcoat', 'clearcoatRoughnessTexture', 'clearcoatRoughnessMap', 'clearcoatRoughnessFactor', 1, False),
    ('clearcoat', 'clearcoatNormalTexture', 'clearcoatNormalMap', None, None, False),
    ('transmission', 'transmissionTexture', 'transmissionMap', 'transmissionFactor', 0, False),
    ('volume', 'thicknessTexture', 'thicknessMap', 'thicknessFactor', 1, False),
    ('sheen', 'sheenColorTexture', 'sheenColorMap', 'sheenColorFactor', None, True),
    ('sheen', 'sheenRoughnessTexture', 'sheenRoughnessMap', 'sheenRoughnessFactor', 3, False),
    ('specular', 'specularTexture', 'specularMap', 'specularFactor', 3, False),
    ('specular', 'specularColorTexture', 'specularColorMap', 'specularColorFactor', None, True),
    ('iridescence', 'iridescenceTexture', 'iridescenceMap', 'iridescenceFactor', 0, False),
    ('iridescence', 'iridescenceThicknessTexture', 'iridescenceThicknessMap', None, 1, False),
    ('anisotropy', 'anisotropyTexture', 'anisotropyMap', 'anisotropyStrength', 2, False),
]
PIXEL = np.array([64, 128, 192, 160], np.uint8)


def held(sample):
    """What the loader keeps of 8-bit texels: float16 (11 significant bits; the sRGB decode happens
    before the rounding), so the reference carries the same stored values."""
    return np.asarray(sample, np.float32).astype(np.float16).astype(np.float64)


def material(extensions):
    return gt.Material(pbrMetallicRoughness=gt.PbrMetallicRoughness(
        baseColorFactor=[.35, .25, .15, 1.], metallicFactor=.25, roughnessFactor=.4),
        extensions={'KHR_materials_'+key: value for key, value in extensions.items()})


def plane(builder, tangent=(1, 0, 0, -1)):
    pos = builder.add([[-40, 30, 0], [40, 30, 0], [40, -30, 0], [-40, -30, 0]], 'VEC3')
    uv = builder.add([[0, 0], [1, 0], [1, 1], [0, 1]], 'VEC2')
    uv1 = builder.add([[1, 0], [1, 1], [0, 1], [0, 0]], 'VEC2')
    nrm = builder.add([[0, 0, 1]]*4, 'VEC3')
    tan = builder.add([tangent]*4, 'VEC4')
    idx = builder.add([0, 2, 1, 0, 3, 2], 'SCALAR', gt.UNSIGNED_SHORT)
    builder.g.meshes = [gt.Mesh(primitives=[gt.Primitive(indices=idx, material=0,
        attributes=gt.Attributes(POSITION=pos, NORMAL=nrm, TANGENT=tan, TEXCOORD_0=uv, TEXCOORD_1=uv1))])]
    builder.g.nodes, builder.g.scenes = [gt.Node(mesh=0)], [gt.Scene(nodes=[0])]


def render(builder, tmp_path, name, lighting='directional', tangent=(1, 0, 0, -1)):
    plane(builder, tangent)
    path = builder.save(tmp_path/(name+'.gltf'))
    light = f'<light id="key" type="{lighting}" intensity="2" yaw="25" pitch="-15"/>'
    r = doc(tmp_path, '<object3D id="o" primitive="mesh" mesh="m" x="80" y="60" scaleX="0.01" scaleY="0.01" scaleZ="0.01"/>', h=120,
            assets=f'<mesh id="m" src="{path}" format="gltf"/>', lights=light, name=name+'.xml')
    return frame(r)


@pytest.mark.parametrize('case', CASES, ids=[c[1] for c in CASES])
def test_imported_extension_image_channels_and_metadata(tmp_path, case):
    extension, field, key, _, channel, srgb = case
    b = Builder()
    image = np.broadcast_to(PIXEL, (3, 5, 4)).copy()
    texture = b.image_png(image)
    b.g.samplers = [gt.Sampler(wrapS=33648, wrapT=33071, minFilter=9985, magFilter=9728)]
    b.g.textures[texture].sampler = 0
    transform = {'texCoord': 2, 'offset': [.17, -.3], 'scale': [2., 3.], 'rotation': .7}
    info = {'index': texture, 'texCoord': 1, 'extensions': {'KHR_texture_transform': transform}}
    if field == 'clearcoatNormalTexture':
        info['scale'] = .35
    b.g.materials = [material({extension: {field: info}})]
    source = _Gltf(b.save(tmp_path/'material.gltf'))
    spec = source.material(source.g.materials[0])
    expected = image.astype(np.float32)/255
    if srgb:
        expected[..., :3] = srgb_to_linear(expected[..., :3])
    if key in ('clearcoatMap', 'clearcoatRoughnessMap'):
        expected = np.repeat(expected[..., channel:channel+1], 4, -1)
    assert spec.textures[key].dtype == np.float16
    np.testing.assert_allclose(spec.textures[key], held(expected), atol=1e-7)
    assert spec.params['mapUV'][key] == dict(texCoord=2, offset=(.17, -.3), scale=(2., 3.), rotation=.7)
    assert spec.params['mapSamplers'][key] == dict(wrapS=33648, wrapT=33071, minFilter=9985, magFilter=9728)
    if field == 'clearcoatNormalTexture':
        assert from_spec(spec).p['clearcoatNormalScale'] == .35


@pytest.mark.parametrize('limits', [{}, {'iridescenceThicknessMinimum': 650., 'iridescenceThicknessMaximum': 150.}])
def test_iridescence_thickness_limits_survive_import(tmp_path, limits):
    b = Builder()
    b.g.materials = [material({'iridescence': limits})]
    source = _Gltf(b.save(tmp_path/'limits.gltf'))
    result = from_spec(source.material(source.g.materials[0]))
    assert result.p['iridescenceThicknessMinimum'] == limits.get('iridescenceThicknessMinimum', 100.)
    assert result.p['iridescenceThicknessMaximum'] == limits.get('iridescenceThicknessMaximum', 400.)


def test_rgba_atlas_retains_channels_dimensions_and_independent_odd_mips():
    images = [np.random.default_rng(i).uniform(-.2, 2., (3+i%3, 5+i%5, 4)).astype(np.float32)
              for i in range(len(ATLAS_MAP_KEYS))]
    images[2] = None
    samplers = [dict(wrapS=33648, wrapT=33069, border=(.1, .2, .3, .4)) for _ in images]
    atlas = pack_material_maps(images, samplers, 256)
    flat = atlas.pixels.reshape(-1, 4)
    for i, source in enumerate(images):
        info = flat[i*META_STRIDE]
        if source is None:
            assert info[0] == 0
            continue
        start, width, height, _ = atlas.levels[i, 0]
        np.testing.assert_array_equal(flat[start:start+width*height].reshape(height, width, 4), source)
        start, width, height, _ = atlas.levels[i, int(info[2])]
        assert (width, height) == (1, 1)
        np.testing.assert_allclose(flat[start], source.mean((0, 1)), atol=3e-7)
        np.testing.assert_allclose(flat[i*META_STRIDE+2], samplers[i]['border'], atol=1e-7)
    with pytest.raises(ValueError, match='capacity'):
        pack_material_maps(images, samplers, 8)


@pytest.mark.parametrize('lighting', ['directional', 'ambient', 'dome'])
@pytest.mark.parametrize('case', [c for c in CASES if c[2] != 'clearcoatNormalMap'], ids=[c[1] for c in CASES if c[2] != 'clearcoatNormalMap'])
def test_constant_extension_texture_matches_factor(tmp_path, case, lighting):
    extension, field, _, factor, channel, srgb = case
    actual, control = Builder(), Builder()
    extensions = {
        'clearcoat': dict(clearcoatFactor=.8, clearcoatRoughnessFactor=.6),
        'transmission': dict(transmissionFactor=.65 if extension in ('volume', 'transmission') else 0.),
        'volume': dict(thicknessFactor=.05, attenuationDistance=.1, attenuationColor=[.3, .6, .8]),
        'sheen': dict(sheenColorFactor=[.3, .6, .8], sheenRoughnessFactor=.7),
        'specular': dict(specularFactor=.7, specularColorFactor=[.9, .6, .4]),
        'iridescence': dict(iridescenceFactor=.6, iridescenceThicknessMinimum=650., iridescenceThicknessMaximum=150.),
        'anisotropy': dict(anisotropyStrength=.65, anisotropyRotation=.3),
    }
    expected = copy.deepcopy(extensions)
    texel = PIXEL.astype(float)/255
    if srgb:
        texel[:3] = srgb_to_linear(texel[:3])
    texel = held(texel)
    if field == 'iridescenceThicknessTexture':
        expected[extension]['iridescenceThicknessMaximum'] = 650.+(150.-650.)*texel[1]
    elif field == 'anisotropyTexture':
        expected[extension][factor] *= texel[2]
        expected[extension]['anisotropyRotation'] += math.atan2(texel[1]*2-1, texel[0]*2-1)
    elif channel is None:
        expected[extension][factor] = (np.array(expected[extension][factor])*texel[:3]).tolist()
    else:
        expected[extension][factor] *= texel[channel]
    texture = actual.image_png(np.broadcast_to(PIXEL, (3, 5, 4)).copy())
    extensions[extension][field] = {'index': texture}
    actual.g.materials, control.g.materials = [material(extensions)], [material(expected)]
    a = render(actual, tmp_path, 'actual', lighting)
    b = render(control, tmp_path, 'control', lighting)
    assert np.max(b[..., :3]) > .01
    np.testing.assert_allclose(a, b, atol=6e-6)


def test_clearcoat_normal_zero_scale_preserves_unmapped_coat(tmp_path):
    actual, control = Builder(), Builder()
    texture = actual.image_png(np.broadcast_to([230, 40, 190, 255], (3, 5, 4)).copy())
    coat = dict(clearcoatFactor=.9, clearcoatRoughnessFactor=.3)
    actual.g.materials = [material({'clearcoat': dict(coat, clearcoatNormalTexture={'index': texture, 'scale': 0.})})]
    control.g.materials = [material({'clearcoat': coat})]
    np.testing.assert_allclose(render(actual, tmp_path, 'normal'), render(control, tmp_path, 'plain'), atol=6e-6)


@pytest.fixture(scope='module')
def atlas_probe():
    from scenerender.three import renderer as gpu, shaders
    from scenerender.three.material_atlas import GLSL
    ctx = gpu.res().ctx
    program = ctx.program(vertex_shader='''#version 410
      out vec2 uv; uniform vec2 scale; uniform vec2 offset;
      void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);uv=p*scale+offset;gl_Position=vec4(p*2-1,0,1);}''',
        fragment_shader='''#version 410
      in vec2 uv; uniform sampler2D atlas; uniform int map;
      layout(location=0)out vec4 color; layout(location=1)out vec4 detail;
      '''+shaders.MAP_SAMPLE+GLSL+'''
      void main(){color=extensionSample(atlas,map,uv);
        vec2 size=extensionMeta(atlas,map,0).xy;
        detail=vec4(textureQueryLod(atlas,uv*size/vec2(textureSize(atlas,0))).y);}''')
    colors = [ctx.texture((32, 32), 4, dtype='f4') for _ in range(2)]
    target = ctx.framebuffer(colors)
    vao = ctx.vertex_array(program, [])
    cache = {}
    def probe(material, index, scale):
        _, texture = gpu._extension_material_texture(ctx, material, cache)
        texture.use(0)
        program['atlas'], program['map'], program['u_hasExtensionMaps'] = 0, index, 1
        program['scale'], program['offset'] = (scale, scale), (.113, -.217)
        target.use(); ctx.viewport = (0, 0, 32, 32)
        ctx.disable(ctx.DEPTH_TEST | ctx.BLEND | ctx.CULL_FACE)
        vao.render(vertices=3)
        return [np.frombuffer(color.read(), np.float32).reshape(32, 32, 4).copy() for color in colors]
    yield probe
    vao.release(); program.release(); target.release()
    for color in colors:
        color.release()
    for _, texture in cache.values():
        texture.release()
    gpu.restore_state(gpu.res())


@pytest.mark.parametrize('minimum', [9728, 9729, 9984, 9985, 9986, 9987])
@pytest.mark.parametrize('maximum', [9728, 9729])
@pytest.mark.parametrize('wraps', [(10497, 33648), (33069, 33071)])
def test_rgba_atlas_filters_match_independent_cpu_reference(atlas_probe, minimum, maximum, wraps):
    from scenerender.three.materials import Material
    from test_scalar_material_maps import reference_filter
    maps = {key: np.random.default_rng(i).uniform(-.1, 1.5, (32, 32, 4)).astype(np.float32)
            for i, key in enumerate(ATLAS_MAP_KEYS)}
    sampler = dict(wrapS=wraps[0], wrapT=wraps[1], minFilter=minimum, magFilter=maximum, border=(.2, .4, .6, .8))
    material = Material({'mapSamplers': {key: sampler for key in maps}}, maps)
    for scale in (.5, 1.425, 8.):
        actual, detail = atlas_probe(material, len(ATLAS_MAP_KEYS)-1, scale)
        for channel in range(4):
            scalar_sampler = dict(sampler, border=(sampler['border'][channel],))
            expected = reference_filter(maps[ATLAS_MAP_KEYS[-1]][..., channel], (scale, scale), (.113, -.217),
                                        detail[..., 0], scalar_sampler)
            np.testing.assert_allclose(actual[..., channel], expected, atol=3e-5)


def test_extension_uv_table_retains_each_transformed_set_and_vertex_index():
    from types import SimpleNamespace
    from scenerender.three import renderer as gpu, shaders
    from scenerender.three.material_atlas import EXTENSION_MAP_KEYS
    from scenerender.three.materials import Material, map_uvs
    ctx = gpu.res().ctx
    position = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], np.float32)
    base = (position[:, :2]+1)/2
    uv_sets = {i: base*[1+i*.1, 1-i*.03]+[i*.01, -i*.02] for i in range(10)}
    transforms = {key: dict(texCoord=i, scale=(.5+i*.1, .8), rotation=i*.2, offset=(i*.02, -.1))
                  for i, key in enumerate(EXTENSION_MAP_KEYS)}
    mat = Material({'mapUV': transforms}, {key: np.ones((1, 1, 4)) for key in EXTENSION_MAP_KEYS})
    source = SimpleNamespace(positions=position, uv_sets=uv_sets, uvs=base)
    item = gpu.upload(ctx, position, np.tile([0, 0, 1], (4, 1)), base, None, None,
                      [2, 3, 0, 2, 0, 1], map_uvs(source, mat))
    program = ctx.program(vertex_shader=shaders.MAIN_VS, fragment_shader='''#version 410
      in vec4 v_extensionUV[5]; uniform int selected; out vec4 color;
      void main(){vec4 uv=v_extensionUV[selected/2];color=vec4(selected%2==0?uv.xy:uv.zw,0,1);}''')
    identity = np.eye(4, dtype=np.float32)
    instance = ctx.buffer(identity.tobytes())
    target = ctx.simple_framebuffer((32, 32), components=4, dtype='f4')
    try:
        gpu._write(program, 'u_proj', identity); gpu._write(program, 'u_view', identity)
        program['u_uvScale'] = (1.3, .7)
        program['t_extensionUV'], program['u_hasExtensionUV'] = 0, 1
        item.extension_uv.use(0)
        target.use(); ctx.viewport = (0, 0, 32, 32)
        ctx.disable(ctx.DEPTH_TEST | ctx.BLEND | ctx.CULL_FACE)
        vao = gpu.item_vao(ctx, item, program, instance)
        x, y = np.meshgrid((np.arange(32)+.5)/32, (np.arange(32)+.5)/32)
        for i, key in enumerate(EXTENSION_MAP_KEYS):
            program['selected'] = i
            vao.render()
            actual = np.frombuffer(target.read(components=4, dtype='f4'), np.float32).reshape(32, 32, 4)
            u, v = (x*(1+i*.1)+i*.01)*(.5+i*.1), (y*(1-i*.03)-i*.02)*.8
            expected = np.stack((u*math.cos(i*.2)-v*math.sin(i*.2)+i*.02,
                                 u*math.sin(i*.2)+v*math.cos(i*.2)-.1), -1)*[1.3, .7]
            np.testing.assert_allclose(actual[..., :2], expected, atol=7e-7)
    finally:
        item.release(); instance.release(); target.release(); program.release()
        gpu.restore_state(gpu.res())


@pytest.mark.parametrize('scale', [-.5, 0., .5, 2.])
@pytest.mark.parametrize('tangent', [(1, 0, 0, -1), (0, 1, 0, 1), (0, 1, 0, -1)])
def test_clearcoat_normal_uses_its_scale_and_geometry_frame(tmp_path, monkeypatch, scale, tangent):
    from scenerender.three import renderer as gpu, shaders
    resources = gpu.res()
    # Inspect the actual surface input, before its BRDF is evaluated. The CPU
    # expectation uses the authored tangent frame, independently of the shader.
    fs = shaders.MAIN_FS.replace('    float f0d = sq((ior - 1.0) / (ior + 1.0));',
        '    o_color=vec4(s.Ng*.5+.5,1.0);o_depth=vec4(0.0);return;\n'
        '    float f0d = sq((ior - 1.0) / (ior + 1.0));')
    program = resources.ctx.program(vertex_shader=shaders.MAIN_VS, fragment_shader=fs)
    b = Builder()
    texel = np.array([230, 40, 190, 255], np.uint8)
    texture = b.image_png(np.broadcast_to(texel, (3, 5, 4)).copy())
    b.g.materials = [material({'clearcoat': dict(clearcoatFactor=1.,
        clearcoatNormalTexture={'index': texture, 'scale': scale})})]
    # A different base normal must not become the clearcoat's geometry frame.
    base_normal = b.image_png(np.broadcast_to([180, 220, 250, 255], (2, 4, 4)).copy())
    b.g.materials[0].normalTexture = gt.NormalMaterialTexture(index=base_normal,
        extensions={'KHR_texture_transform': {'rotation': .4}})
    try:
        with monkeypatch.context() as patch:
            patch.setattr(resources, 'main', program)
            actual = render(b, tmp_path, 'normal_input', tangent=tangent)
        normal = held(texel[:3]/255.)*2-1
        normal[:2] *= scale
        t = np.array(tangent[:3])
        normal = t*normal[0]+np.cross([0, 0, 1], t)*tangent[3]*normal[1]+np.array([0, 0, normal[2]])
        normal /= np.linalg.norm(normal)
        np.testing.assert_allclose(actual[45:75, 60:100, :3], np.broadcast_to(normal*.5+.5, (30, 40, 3)), atol=3e-6)
    finally:
        program.release()


def test_all_extension_maps_coexist_with_core_texture_stack(tmp_path):
    actual, control = Builder(), Builder()
    values = {
        'clearcoat': dict(clearcoatFactor=.8, clearcoatRoughnessFactor=.6),
        'transmission': dict(transmissionFactor=.65),
        'volume': dict(thicknessFactor=.05, attenuationDistance=.1, attenuationColor=[.3, .6, .8]),
        'sheen': dict(sheenColorFactor=[.3, .6, .8], sheenRoughnessFactor=.7),
        'specular': dict(specularFactor=.7, specularColorFactor=[.9, .6, .4]),
        'iridescence': dict(iridescenceFactor=.6, iridescenceThicknessMinimum=650., iridescenceThicknessMaximum=150.),
        'anisotropy': dict(anisotropyStrength=.65, anisotropyRotation=.3),
    }
    expected = copy.deepcopy(values)
    for i, (extension, field, _, factor, channel, srgb) in enumerate(CASES):
        pixel = (PIXEL.astype(int)+i*7)%256
        sample = pixel/255.
        if srgb:
            sample[:3] = srgb_to_linear(sample[:3])
        sample = held(sample)
        texture = actual.image_png(np.broadcast_to(pixel, (3+i, 5+i, 4)).copy())
        info = {'index': texture}
        if field == 'clearcoatNormalTexture':
            info['scale'] = 0.
        elif field == 'iridescenceThicknessTexture':
            expected[extension]['iridescenceThicknessMaximum'] = 650.+(150.-650.)*sample[1]
        elif field == 'anisotropyTexture':
            expected[extension][factor] *= sample[2]
            expected[extension]['anisotropyRotation'] += math.atan2(sample[1]*2-1, sample[0]*2-1)
        elif channel is None:
            expected[extension][factor] = (np.array(expected[extension][factor])*sample[:3]).tolist()
        else:
            expected[extension][factor] *= sample[channel]
        values[extension][field] = info
    actual.g.materials, control.g.materials = [material(values)], [material(expected)]
    for builder in (actual, control):
        m = builder.g.materials[0]
        m.pbrMetallicRoughness.metallicRoughnessTexture = gt.TextureInfo(index=builder.image_png(
            np.broadcast_to([20, 190, 110, 255], (7, 9, 4)).copy()))
        m.pbrMetallicRoughness.baseColorTexture = gt.TextureInfo(index=builder.image_png(
            np.broadcast_to([170, 230, 190, 255], (2, 5, 4)).copy()))
        m.occlusionTexture = gt.OcclusionTextureInfo(index=builder.image_png(
            np.broadcast_to([170, 20, 80, 255], (3, 4, 4)).copy()), strength=.4)
        m.emissiveTexture = gt.TextureInfo(index=builder.image_png(
            np.broadcast_to([10, 230, 30, 255], (5, 3, 4)).copy()))
        m.emissiveFactor = [.15, .1, .2]
        m.normalTexture = gt.NormalMaterialTexture(index=builder.image_png(
            np.broadcast_to([230, 20, 250, 255], (4, 3, 4)).copy()), scale=0.)
    np.testing.assert_allclose(render(actual, tmp_path, 'all_maps'), render(control, tmp_path, 'all_factors'), atol=6e-6)


@pytest.mark.parametrize('case', CASES, ids=[c[1] for c in CASES])
def test_each_extension_samples_its_transformed_uv_set(tmp_path, case):
    extension, field, *_ = case
    factors = {
        'clearcoat': dict(clearcoatFactor=.9, clearcoatRoughnessFactor=.7),
        'transmission': dict(transmissionFactor=.7),
        'volume': dict(thicknessFactor=.05, attenuationDistance=.1, attenuationColor=[.3, .6, .8]),
        'sheen': dict(sheenColorFactor=[.3, .6, .8], sheenRoughnessFactor=.7),
        'specular': dict(specularFactor=.7, specularColorFactor=[.9, .6, .4]),
        'iridescence': dict(iridescenceFactor=.6, iridescenceThicknessMinimum=650., iridescenceThicknessMaximum=150.),
        'anisotropy': dict(anisotropyStrength=.65, anisotropyRotation=.3),
    }
    pixels = np.array([[[80, 100, 200, 60], [180, 160, 230, 120]],
                       [[120, 200, 180, 180], [200, 50, 210, 230]]], np.uint8)
    actual = Builder()
    texture = actual.image_png(pixels)
    actual.g.samplers = [gt.Sampler(wrapS=33648, wrapT=10497, minFilter=9728, magFilter=9728)]
    actual.g.textures[texture].sampler = 0
    values = copy.deepcopy(factors)
    values[extension][field] = {'index': texture, 'texCoord': 0, 'extensions': {
        'KHR_texture_transform': {'texCoord': 1, 'offset': [.17, -.23], 'scale': [2.1, .7], 'rotation': .6}}}
    actual.g.materials = [material(values)]
    image = render(actual, tmp_path, 'spatial')
    controls = []
    for i, pixel in enumerate(pixels.reshape(-1, 4)):
        builder = Builder()
        texture = builder.image_png(pixel.reshape(1, 1, 4))
        values = copy.deepcopy(factors)
        values[extension][field] = {'index': texture}
        builder.g.materials = [material(values)]
        controls.append(render(builder, tmp_path, 'texel'+str(i)))
    seen = set()
    for y in (34, 44, 54, 64, 74, 84):
        for x in (45, 59, 71, 85, 99, 111):
            u, v = (1-(y+.5-30)/60)*2.1, (x+.5-40)/80*.7
            s = u*math.cos(.6)-v*math.sin(.6)+.17
            t = u*math.sin(.6)+v*math.cos(.6)-.23
            s, t = 1-abs(s%2-1), t%1
            index = min(1, int(t*2))*2+min(1, int(s*2))
            seen.add(index)
            np.testing.assert_allclose(image[y, x], controls[index][y, x], atol=6e-6)
    assert seen == {0, 1, 2, 3}
    assert max(np.max(abs(c-controls[0])) for c in controls[1:]) > 1e-4


def test_extension_atlas_cache_refreshes_sampler_metadata_and_survives_eviction():
    from scenerender.three import renderer as gpu
    from scenerender.three.materials import Material
    ctx, cache = gpu.res().ctx, {}
    material = Material({'mapSamplers': {'sheenColorMap': {'wrapS': 10497}}},
                        {'sheenColorMap': np.random.default_rng(9).uniform(0, 1, (3, 5, 4)).astype(np.float32)})
    try:
        initial, first = gpu._extension_material_texture(ctx, material, cache)
        material.p['mapSamplers']['sheenColorMap']['wrapS'] = 33648
        changed, second = gpu._extension_material_texture(ctx, material, cache)
        assert first is not second
        assert not np.array_equal(initial.pixels, changed.pixels)
        for i in range(140):
            ordinary = Material({}, {'baseColorMap': np.full((1, 1, 4), i/140, np.float32)})
            gpu._material_texture(ctx, ordinary, 'baseColorMap', cache)
        restored, texture = gpu._extension_material_texture(ctx, material, cache)
        assert texture is not second
        live = [texture]
        for i in range(7):
            ordinary = Material({}, {'baseColorMap': np.full((1, 1, 4), i/7, np.float32)})
            live.append(gpu._material_texture(ctx, ordinary, 'baseColorMap', cache))
        assert all(t.read() for t in live)
        np.testing.assert_array_equal(restored.pixels, changed.pixels)
        np.testing.assert_array_equal(np.frombuffer(texture.read(), np.float32), changed.pixels.ravel())
    finally:
        for _, texture in cache.values():
            texture.release()
