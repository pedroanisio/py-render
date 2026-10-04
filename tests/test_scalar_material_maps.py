"""Independent scalar textures: original samples, filtering, shading and animation."""
import math

import numpy as np
import pytest

from scenerender import gl
from scenerender.three import renderer as gpu, shaders
from scenerender.three.materials import Material, sample_map
from scenerender.three.texture_atlas import GLSL, SCALAR_MAP_KEYS, pack_scalar_maps
from test_usd_materials import Scene, ST, pattern
from test_usd_shading import FRONT, set_input


def rgba(values):
    return np.repeat(np.asarray(values, np.float32)[..., None], 4, -1)


def reference_pyramid(pixels):
    """Integrate cell overlap in float64, independently of the atlas reducer."""
    levels = [np.asarray(pixels, np.float64)]
    while levels[-1].shape != (1, 1):
        image = levels[-1]
        weights = []
        for size in image.shape:
            count = max(1, size//2)
            edges = np.linspace(0., size, count+1)
            cells = np.arange(size)
            overlap = np.maximum(0., np.minimum(edges[1:, None], cells+1)-np.maximum(edges[:-1, None], cells))
            weights.append(overlap/(size/count))
        levels.append(weights[0] @ image @ weights[1].T)
    return levels


def reference_level(image, uv, linear, wraps, border):
    """Sum the contributing texel weights in unbounded source coordinates."""
    size = np.array(image.shape[::-1])
    position = uv*size-(.5 if linear else 0.)
    origin = np.floor(position).astype(np.int64)
    fraction = position-origin
    result = np.zeros(uv.shape[:-1])
    for y in range(2 if linear else 1):
        for x in range(2 if linear else 1):
            indices = origin + [x, y]
            outside = np.zeros(uv.shape[:-1], bool)
            for axis, wrap in enumerate(wraps):
                index = indices[..., axis]
                if wrap == 10497:
                    indices[..., axis] = index % size[axis]
                elif wrap == 33648:
                    n = index % (2*size[axis])
                    indices[..., axis] = np.where(n < size[axis], n, 2*size[axis]-n-1)
                else:
                    if wrap == 33069:
                        outside |= (index < 0) | (index >= size[axis])
                    indices[..., axis] = np.clip(index, 0, size[axis]-1)
            weight = np.prod(np.where(np.array([x, y]), fraction, 1-fraction), axis=-1) if linear else 1.
            result += weight*np.where(outside, border, image[indices[..., 1], indices[..., 0]])
    return result


def reference_filter(pixels, scale, offset, lod, sampler):
    x, y = np.meshgrid((np.arange(32)+.5)/32, (np.arange(32)+.5)/32)
    uv = np.stack((x, y), -1)*scale+offset
    levels = reference_pyramid(pixels)
    wraps = [sampler.get(name, 10497) for name in ('wrapS', 'wrapT')]
    border = sampler.get('border', (0.,))[0]
    minimum, maximum = sampler.get('minFilter', 9987), sampler.get('magFilter', 9729)
    result = np.empty_like(lod, dtype=np.float64)
    for magnified, mode in [(True, maximum), (False, minimum)]:
        selected = (lod <= 0.) if magnified else (lod > 0.)
        if not np.any(selected):
            continue
        if mode in (9728, 9729):
            result[selected] = reference_level(levels[0], uv, mode == 9729, wraps, border)[selected]
            continue
        clamped = np.clip(lod, 0., len(levels)-1)
        linear = mode in (9985, 9987)
        for index, image in enumerate(levels):
            if mode in (9984, 9985):
                weight = (np.maximum(0, np.ceil(clamped+.5)-1) == index).astype(float)
            else:
                weight = np.maximum(0., 1-abs(clamped-index))
            contribution = weight*reference_level(image, uv, linear, wraps, border)
            if index == 0:
                result[selected] = 0.
            result[selected] += contribution[selected]
    return result


def test_atlas_preserves_original_samples_and_odd_sized_mip_averages():
    a = rgba(np.arange(35).reshape(5, 7)/65535+1.5)
    b = rgba([[.1, .9, .2]])
    atlas = pack_scalar_maps([a, None, b], 64)
    for i, source in [(0, a), (2, b)]:
        address, width, height, _ = atlas.levels[i, 0]
        np.testing.assert_array_equal(atlas.pixels.ravel()[address:address+width*height].reshape(height, width), source[..., 0])
        address, width, height, _ = atlas.levels[i, atlas.info[i, 2]]
        assert (width, height) == (1, 1)
        assert atlas.pixels.ravel()[address] == pytest.approx(source[..., 0].mean(), abs=2e-7)
    # The middle texel contributes half its area to each output on a 5->2 reduction.
    values = rgba([[0.], [0.], [1.], [0.], [0.]])
    small = pack_scalar_maps([values], 16)
    start = small.levels[0, 1, 0]
    np.testing.assert_allclose(small.pixels.ravel()[start:start+2], [.2, .2], atol=1e-7)
    assert atlas.info[1, 0] == 0


def test_atlas_reports_capacity_failure_without_resizing_sources():
    with pytest.raises(ValueError, match='exceeds GPU capacity'):
        pack_scalar_maps([rgba(np.ones((9, 9)))], 8)


@pytest.fixture(scope='module')
def sampler_probe():
    if not gl.available():
        pytest.skip('no GL')
    context = gpu.res().ctx
    program = context.program(vertex_shader='''#version 410
      out vec2 v_uv; uniform vec2 uvScale; uniform vec2 uvOffset;
      void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);
        v_uv=p*uvScale+uvOffset;gl_Position=vec4(p*2-1,0,1);}''',
      fragment_shader='''#version 410
      in vec2 v_uv; uniform sampler2D atlas; uniform sampler2D reference;
      uniform int map; uniform int explicitLod; uniform ivec4 settings; uniform vec4 border; out vec4 color;
      '''+GLSL+shaders.MAP_SAMPLE+'''
      void main(){float value=mapSample(reference,v_uv,settings,border).r;
        if(explicitLod==1)value=textureLod(reference,v_uv,textureQueryLod(reference,v_uv).y).r;
        vec2 size=vec2(u_scalarInfo[map].xy);
        float atlasLod=textureQueryLod(atlas,v_uv*size/vec2(textureSize(atlas,0))).y;
        color=vec4(scalarSample(atlas,map,v_uv),value,atlasLod,textureQueryLod(reference,v_uv).y);}''')
    color = context.texture((32, 32), 4, dtype='f4')
    target = context.framebuffer([color])
    vao = context.vertex_array(program, [])
    cache = {}
    def render(material, index, scale=(1.5, 1.5), offset=(-.25, -.25), explicit_lod=False):
        data, texture = gpu._scalar_material_texture(context, material, cache)
        gpu._scalar_uniforms(program, material, data)
        key = SCALAR_MAP_KEYS[index]
        reference = gpu._material_texture(context, material, key, cache)
        texture.use(0); reference.use(1)
        program['atlas'], program['reference'] = 0, 1
        program['map'], program['u_hasScalarMaps'] = index, 1
        program['explicitLod'] = int(explicit_lod)
        program['settings'] = gpu._map_sampler(material, key)
        program['border'] = gpu._map_border(material, key)
        program['uvScale'], program['uvOffset'] = scale, offset
        target.use(); context.viewport = (0, 0, 32, 32)
        context.disable(context.DEPTH_TEST | context.BLEND | context.CULL_FACE)
        vao.render(vertices=3)
        return np.frombuffer(color.read(), np.float32).reshape(32, 32, 4).copy()
    yield render
    vao.release(); program.release(); target.release(); color.release()
    for _, texture in cache.values():
        texture.release()
    gpu.restore_state(gpu.res())


@pytest.mark.parametrize('wrap_s', [10497, 33648, 33071, 33069])
@pytest.mark.parametrize('wrap_t', [10497, 33648, 33071, 33069])
def test_gpu_scalar_and_ordinary_maps_match_cpu_wraps(sampler_probe, wrap_s, wrap_t):
    maps, samplers = {}, {}
    for i, (height, width) in enumerate([(3, 7), (5, 4), (8, 9)]):
        key = SCALAR_MAP_KEYS[i]
        maps[key] = rgba(np.random.default_rng(i).uniform(-.2, 1.3, (height, width)))
        samplers[key] = {'wrapS': wrap_s, 'wrapT': wrap_t, 'border': (.2,)*4,
                         'minFilter': 9987, 'magFilter': 9729}
    material = Material({'mapSamplers': samplers}, maps)
    for i in range(3):
        result = sampler_probe(material, i)
        for channel in range(2):
            expected = reference_filter(maps[SCALAR_MAP_KEYS[i]][..., 0], (1.5, 1.5), (-.25, -.25),
                                        result[..., channel+2], samplers[SCALAR_MAP_KEYS[i]])
            np.testing.assert_allclose(result[..., channel], expected, atol=2e-6)


@pytest.mark.parametrize('minimum', [9728, 9729, 9984, 9985, 9986, 9987])
@pytest.mark.parametrize('maximum', [9728, 9729])
@pytest.mark.parametrize('scale', [.5, 1.2, 1.425, 2.45, 8.])
def test_gpu_scalar_and_ordinary_mip_filters_match_cpu(sampler_probe, minimum, maximum, scale):
    values = rgba(np.random.default_rng(42).uniform(.05, 3., (32, 32)))
    # Decimal-scale cases avoid exact nearest-neighbor ties, where CPU and
    # interpolated GLSL coordinates can land on opposite sides after rounding.
    # The binary-coordinate controls below exercise reproducible texel ties.
    offset = (.113, -.217)
    for wraps in [(10497, 10497), (33069, 33069)]:
        sampler = {'wrapS': wraps[0], 'wrapT': wraps[1], 'minFilter': minimum, 'magFilter': maximum, 'border': (.2,)*4}
        material = Material({'mapSamplers': {'iorMap': sampler}}, {'iorMap': values})
        result = sampler_probe(material, 0, (scale, scale), offset)
        for channel in range(2):
            expected = reference_filter(values[..., 0], (scale, scale), offset, result[..., channel+2], sampler)
            np.testing.assert_allclose(result[..., channel], expected, atol=3e-5)


@pytest.mark.parametrize('minimum', [9728, 9729, 9984, 9985, 9986, 9987])
@pytest.mark.parametrize('maximum', [9728, 9729])
@pytest.mark.parametrize('scale', [.5, 2., 8.])
def test_gpu_scalar_atlas_matches_native_filter_at_exact_fractions(sampler_probe, minimum, maximum, scale):
    # Binary coordinates and integer LODs give native samplers exactly
    # representable weights, including on devices with eight-bit fractions.
    values = rgba(np.random.default_rng(42).uniform(.05, 3., (32, 32)))
    sampler = {'wrapS': 10497, 'wrapT': 10497, 'minFilter': minimum, 'magFilter': maximum}
    material = Material({'mapSamplers': {'iorMap': sampler}}, {'iorMap': values})
    result = sampler_probe(material, 0, (scale, scale), (.125, -.25), explicit_lod=True)
    np.testing.assert_allclose(result[..., 0], result[..., 1], atol=3e-5)


def test_minified_odd_sized_maps_preserve_the_whole_image_average(sampler_probe):
    maps = {key: rgba(np.random.default_rng(i).uniform(.1, 3., shape))
            for i, (key, shape) in enumerate(zip(SCALAR_MAP_KEYS, [(5, 7), (1, 9), (8, 13)]))}
    material = Material({}, maps)
    for i, key in enumerate(SCALAR_MAP_KEYS):
        result = sampler_probe(material, i, scale=(128., 128.))
        np.testing.assert_allclose(result[..., 0], maps[key][..., 0].mean(), atol=3e-7)


@pytest.mark.parametrize('name,value,channel', [('ior', 2.4, 'g'), ('clearcoat', .6, 'b'),
                                               ('clearcoatRoughness', .3, 'a')])
@pytest.mark.parametrize('lighting', ['directional', 'ambient', 'dome'])
def test_constant_texture_matches_constant_surface_input(tmp_path, name, value, channel, lighting):
    actual, control = Scene(tmp_path), Scene(tmp_path, 'control')
    for scene in [actual, control]:
        set_input(scene, 'ior', 1.9)
        set_input(scene, 'clearcoat', .7)
        set_input(scene, 'clearcoatRoughness', .4)
    source = np.ones((3, 5, 4), np.float32)
    source[..., 'rgba'.index(channel)] = (value-.1)/2
    tex = actual.texture('Map', source, actual.reader(), wrap='clamp')
    from pxr import Sdf
    tex.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set((2.,)*4)
    tex.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set((.1,)*4)
    actual.connect(name, tex, channel)
    set_input(control, name, value)
    lights = f'<light id="l" type="{lighting}"/>'
    np.testing.assert_allclose(actual.renderer(lights=lights).rc.render_frame(0).px,
                               control.renderer(lights=lights).rc.render_frame(0).px, atol=4e-6)


def test_three_different_scalar_maps_reach_the_shading_equation(tmp_path):
    scene = Scene(tmp_path)
    set_input(scene, 'diffuseColor', (.3, .2, .1))
    maps = []
    for name, (height, width), low, high in [('ior', (7, 11), 1.2, 2.4),
            ('clearcoat', (9, 5), .2, .6), ('clearcoatRoughness', (6, 13), .3, .7)]:
        values = low+(high-low)*np.random.default_rng(height).random((height, width))
        image = rgba(values); maps.append(image)
        scene.connect(name, scene.texture(name, image, scene.reader(name+'Reader'), wrap='clamp'), 'r')
    actual = scene.renderer(lights=FRONT).rc.render_frame(0).px[40, 40, :3]
    uv = np.array([[.5078125, .5078125]])
    ior, coat, rough = [sample_map(a, uv, {'wrapS': 33071, 'wrapT': 33071})[0, 0] for a in maps]
    f0 = ((ior-1)/(ior+1))**2
    base = np.array([.3, .2, .1])*(1-f0)/math.pi+f0/(4*math.pi*.5**4)
    expected = base*(1-f0*coat)+f0*coat/(4*math.pi*rough**4)
    np.testing.assert_allclose(actual, expected, atol=4e-6)


def test_scalar_map_coordinates_animation_and_cache_reuse(tmp_path):
    scene = Scene(tmp_path)
    for i, name in enumerate(['ior', 'clearcoat', 'clearcoatRoughness']):
        scene.uv(name+'UV', ST*np.array([1., .8])+.1)
        transform = scene.transform(name+'Transform', scene.reader(name+'Reader', name+'UV'), angle=i*90)
        transform.GetInput('translation').Set((.1, .2), 0)
        transform.GetInput('translation').Set((.3, -.2), 24)
        pixels = rgba((pattern()[..., i]+.1)*(.6 if i else 2.))
        texture = scene.texture(name, pixels, transform, wrap='repeat')
        texture.GetInput('wrapS').Set('repeat', 0); texture.GetInput('wrapS').Set('mirror', 12)
        scene.connect(name, texture, 'r')
    renderer = scene.renderer(animation=True)
    for n, time in enumerate([.75, .25, 0., .75]):
        control = Scene(tmp_path, 'control'+str(n))
        for i, name in enumerate(['ior', 'clearcoat', 'clearcoatRoughness']):
            uv = ST*np.array([1., .8])+.1
            angle = math.radians(i*90)
            uv = uv@np.array([[math.cos(angle), math.sin(angle)], [-math.sin(angle), math.cos(angle)]])
            uv += np.array([.1, .2])+time*np.array([.2, -.4])
            control.uv(name+'UV', uv)
            pixels = rgba((pattern()[..., i]+.1)*(.6 if i else 2.))
            texture = control.texture(name, pixels, control.reader(name+'Reader', name+'UV'))
            texture.GetInput('wrapS').Set('mirror' if time >= .5 else 'repeat')
            control.connect(name, texture, 'r')
        np.testing.assert_allclose(renderer.rc.render_frame(time).px,
                                   control.renderer().rc.render_frame(0).px, atol=5e-6)
    atlases = [entry for key, entry in renderer.rc.cache['gl-tex'].items() if key[0] == 'scalar-atlas']
    assert len(atlases) == 1, 'UV and wrap changes must reuse the uploaded scalar data'


@pytest.mark.parametrize('workflow', [0, 1])
@pytest.mark.parametrize('mode', ['transparent', 'mask'])
def test_complete_texture_stack_matches_constant_inputs(tmp_path, workflow, mode):
    actual, control = Scene(tmp_path), Scene(tmp_path, 'control')
    values = {'diffuseColor': (.4, .2, .1), 'emissiveColor': (.05, .1, .2),
              'normal': (.2, 0., math.sqrt(.96)), 'roughness': .6,
              'occlusion': .7, 'opacity': .55, 'displacement': .3,
              'ior': 2.3, 'clearcoat': .4, 'clearcoatRoughness': .25}
    values.update({'specularColor': (.2, .3, .1)} if workflow else {'metallic': .4})
    for scene in [actual, control]:
        set_input(scene, 'useSpecularWorkflow', workflow)
        set_input(scene, 'opacityMode', 'transparent')
        if mode == 'mask':
            set_input(scene, 'opacityThreshold', .5)
    for i, (name, value) in enumerate(values.items()):
        set_input(control, name, value)
        pixels = np.ones((i+2, i+3, 4), np.float32)
        if isinstance(value, tuple):
            pixels[..., :3] = value
        else:
            pixels[..., 0] = value
        actual.uv(name+'UV', ST*np.array([.6, .8])+.1)
        texture = actual.texture(name, pixels, actual.reader(name+'Reader', name+'UV'), wrap='clamp')
        actual.connect(name, texture, 'rgb' if isinstance(value, tuple) else 'r')
    back = '<object3D id="back" primitive="plane" width="80" height="80" z="-10" material="red"/>'
    lights = FRONT+'<light id="dome" type="dome" intensity=".25"/>'
    result = actual.renderer(lights=lights, under=back).rc.render_frame(0).px
    expected = control.renderer(lights=lights, under=back).rc.render_frame(0).px
    np.testing.assert_allclose(result, expected, atol=5e-6)


def test_evicted_atlas_reuploads_original_pixels_and_retains_current_draw_maps():
    if not gl.available():
        pytest.skip('no GL')
    context = gpu.res().ctx
    cache = {}
    material = Material({}, {key: rgba(np.arange(15).reshape(3, 5)+i/10)
                             for i, key in enumerate(SCALAR_MAP_KEYS)})
    try:
        original, first = gpu._scalar_material_texture(context, material, cache)
        for i in range(140):
            ordinary = Material({}, {'baseColorMap': rgba([[i/140]])})
            gpu._material_texture(context, ordinary, 'baseColorMap', cache)
        restored, texture = gpu._scalar_material_texture(context, material, cache)
        assert texture is not first
        current = [texture]
        for i in range(7):
            ordinary = Material({}, {'baseColorMap': rgba([[i/7]])})
            current.append(gpu._material_texture(context, ordinary, 'baseColorMap', cache))
        # Read each current texture after all eight uploads; none may be released.
        assert all(t.read() for t in current)
        np.testing.assert_array_equal(np.frombuffer(texture.read(), np.float32), original.pixels.ravel())
        np.testing.assert_array_equal(restored.pixels, original.pixels)
        assert len(cache) <= 128
    finally:
        for _, texture in cache.values():
            texture.release()
