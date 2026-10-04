"""All authored lights and shadow maps reach the fragment shader."""
from types import SimpleNamespace

import numpy as np
import pytest

from scenerender import gl
from scenerender.three import renderer as gpu
from test_3d_render import doc, frame, FLOOR, WHITE


KINDS = ['directional', 'point', 'spot', 'rect-area', 'disk-area', 'sphere-area']


def image(tmp_path, lights, name, *, body=None, material=WHITE):
    return frame(doc(tmp_path, body or FLOOR.format(cs=''), materials=material, lights=lights,
                     w=96, h=64, name=name))


@pytest.mark.skipif(not gl.available(), reason='no GL')
@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('shadow', [False, True])
def test_all_direct_light_types_beyond_old_counts(tmp_path, kind, shadow):
    count = 19
    intensity = .1 if kind == 'directional' else 10
    attrs = f'type="{kind}" x="100" y="-440" pitch="-80" castShadow="{str(shadow).lower()}" shadowMapSize="64"'
    many = ''.join(f'<light id="l{i}" {attrs} intensity="{intensity}"/>' for i in range(count))
    expected = image(tmp_path, f'<light id="l" {attrs} intensity="{count*intensity}"/>', 'control.xml')
    actual = image(tmp_path, many, 'many.xml')
    assert expected[..., :3].max() > .01
    np.testing.assert_allclose(actual, expected, atol=2e-6)


@pytest.mark.skipif(not gl.available(), reason='no GL')
@pytest.mark.parametrize('count', [65, 257])
def test_light_records_cross_texture_rows(tmp_path, count):
    # Eight RGBA records per light, so 257 lights cross two 1024-texel rows.
    many = ''.join(f'<light id="l{i}" type="directional" pitch="-70" intensity=".01"/>' for i in range(count))
    expected = image(tmp_path, f'<light id="l" type="directional" pitch="-70" intensity="{count*.01}"/>', 'control.xml')
    np.testing.assert_allclose(image(tmp_path, many, 'many.xml'), expected, atol=4e-6)


@pytest.mark.skipif(not gl.available(), reason='no GL')
@pytest.mark.parametrize('material', [
    '<material id="w" baseColor="#A0C0F080" alphaMode="blend" doubleSided="true"/>',
    '<material id="w" baseColor="#A0C0F0FF" transmission=".8" thickness="10" roughness=".1"/>',
])
def test_many_lights_preserve_transparency_and_transmission(tmp_path, material):
    body = '<object3D id="s" x="48" y="32" primitive="sphere" radius="35" material="w"/>'
    many = ''.join(f'<light id="l{i}" type="directional" pitch="-30" intensity=".1"/>' for i in range(23))
    expected = image(tmp_path, '<light id="l" type="directional" pitch="-30" intensity="2.3"/>', 'control.xml', body=body, material=material)
    actual = image(tmp_path, many, 'many.xml', body=body, material=material)
    assert expected[..., :3].max() > .01
    np.testing.assert_allclose(actual, expected, atol=2e-6)


@pytest.mark.skipif(not gl.available(), reason='no GL')
def test_mixed_lights_and_ies_match_sum_of_individual_renders(tmp_path):
    from test_ies_coordinates import write_profile
    profile = write_profile(tmp_path, 1, [0, 90, 180], [0], [[1, 1, 2]])
    lights = []
    for i in range(24):
        kind = KINDS[i % len(KINDS)]
        intensity = .2 if kind == 'directional' else 20
        color = ['#FF8040FF', '#40FF80FF', '#8040FFFF'][i % 3]
        ies = f'ies="{profile}"' if i % 2 else ''
        flags = 'affectsDiffuse="false"' if i % 4 == 0 else 'affectsSpecular="false"' if i % 4 == 1 else ''
        lights.append(f'<light id="l{i}" type="{kind}" x="{25*i-200}" y="-440" pitch="-75" yaw="{i*3}" '
                      f'intensity="{intensity}" color="{color}" range="1500" falloff="1.7" {ies} {flags}/>')
    expected = sum(image(tmp_path, light, f'one{i}.xml')[..., :3] for i, light in enumerate(lights))
    actual = image(tmp_path, ''.join(lights), 'all.xml')[..., :3]
    np.testing.assert_allclose(actual, expected, atol=4e-6)


@pytest.mark.skipif(not gl.available(), reason='no GL')
def test_mixed_shadow_sizes_and_multiple_array_pages(tmp_path, monkeypatch):
    atlas = gpu._shadow_atlas
    pages = []

    class SmallPages:
        def __init__(self, ctx):
            self.ctx = ctx
            self.info = dict(ctx.info, GL_MAX_TEXTURE_SIZE=128)

        def __getattr__(self, name):
            return getattr(self.ctx, name)

    def pack(ctx, shadows):
        texture, placements = atlas(SmallPages(ctx), shadows)
        if texture is not None:
            pages.append(texture.layers)
        return texture, placements

    monkeypatch.setattr(gpu, '_shadow_atlas', pack)
    lights = []
    for i in range(12):
        kind = ['directional', 'point', 'spot'][i % 3]
        intensity = .15 if kind == 'directional' else 12
        size = [24, 32, 48, 64][i % 4]
        color = ['#FF4020FF', '#20FF40FF', '#4020FFFF'][i % 3]
        lights.append(f'<light id="l{i}" type="{kind}" x="{i*30-80}" y="-440" pitch="-75" '
                      f'color="{color}" intensity="{intensity}" castShadow="true" '
                      f'shadowMapSize="{size}" shadowSoftness=".1"/>')
    expected = sum(image(tmp_path, light, f'one{i}.xml')[..., :3] for i, light in enumerate(lights))
    actual = image(tmp_path, ''.join(lights), 'all.xml')[..., :3]
    assert pages[-1] > 1
    np.testing.assert_allclose(actual, expected, atol=3e-6)


@pytest.mark.skipif(not gl.available(), reason='no GL')
def test_dome_shadow_with_many_direct_shadows(tmp_path):
    from test_3d_render import MEDIA
    dome = f'<light id="d" type="dome" environment="{MEDIA}/env_sky.npy" intensity=".2" castShadow="true" shadowMapSize="64"/>'
    attrs = 'type="directional" pitch="-60" castShadow="true" shadowMapSize="64"'
    many = ''.join(f'<light id="l{i}" {attrs} intensity=".1"/>' for i in range(19))
    expected = image(tmp_path, dome+f'<light id="l" {attrs} intensity="1.9"/>', 'control.xml')
    np.testing.assert_allclose(image(tmp_path, dome+many, 'many.xml'), expected, atol=3e-6)


@pytest.mark.skipif(not gl.available(), reason='no GL')
def test_animated_last_light_and_shadow_survive_backward_seeks(tmp_path):
    values = {'type': ['point', 'spot', 'directional'], 'x': ['0', '130', '190'],
              'color': ['#FF4000FF', '#00FF40FF', '#4000FFFF'], 'castShadow': ['true', 'false', 'true'],
              'shadowMapSize': ['64', '96', '48']}
    keys = ''.join(f'<animate property="{name}">'+''.join(f'<key time="{i}" value="{value}"/>' for i, value in enumerate(series))+'</animate>'
                   for name, series in values.items())
    prefix = ''.join(f'<light id="l{i}" type="directional" intensity="0"/>' for i in range(23))
    lights = prefix+f'<light id="last" type="point" x="100" y="-440" pitch="-80" intensity="30">{keys}</light>'
    scene = doc(tmp_path, FLOOR.format(cs=''), materials=WHITE, lights=lights, w=96, h=64)
    for t in [0, 1, 2, 1, 0]:
        attrs = ' '.join(f'{name}="{series[t]}"' for name, series in values.items())
        expected = image(tmp_path, f'<light id="l" {attrs} y="-440" pitch="-80" intensity="30"/>', f'control{t}.xml')
        assert expected[..., :3].max() > .01
        np.testing.assert_allclose(frame(scene, t), expected, atol=3e-6)


@pytest.mark.skipif(not gl.available(), reason='no GL')
def test_cube_face_boundaries_do_not_sample_neighboring_atlas_tiles():
    import moderngl
    from scenerender.three.shaders import LIGHT_DATA, SHADOW_LOOKUP
    ctx = gl.context()
    yy, xx = np.mgrid[:8, :8]
    faces = [ctx.texture((8, 8), 1, (100*i+10*yy+xx).astype('f4').tobytes(), dtype='f4') for i in range(6)]
    neighbor = ctx.texture((16, 16), 1, np.full((16, 16), 999, np.float32).tobytes(), dtype='f4')
    atlas, placements = gpu._shadow_atlas(ctx, [SimpleNamespace(faces=(neighbor,)), SimpleNamespace(faces=faces)])
    records = np.zeros((1, 14, 4), np.float32)
    records[0, 8:] = placements[1]
    data = gpu._record_texture(ctx, records)
    directions = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1],
                           [1, 1, 0], [1, -1, 0], [1, 0, 1], [1, 0, -1], [0, 1, 1], [0, -1, 1],
                           [1, 1, 1], [-1, -1, -1]], np.float32)
    expected = [44, 144, 244, 344, 444, 544, 74, 4, 47, 40, 274, 304, 77, 107]
    query = ctx.texture((len(directions), 1), 3, directions.tobytes(), dtype='f4')
    output = ctx.texture((len(directions), 1), 1, dtype='f4')
    fbo = ctx.framebuffer([output])
    program = ctx.program(vertex_shader=gl.FULLSCREEN_VS, fragment_shader='''#version 410
        uniform sampler2D t_lights; uniform sampler2D t_shadowData;
        uniform sampler2DArray t_sh; uniform sampler2D queries;
        out float result;
        ''' + LIGHT_DATA + SHADOW_LOOKUP + '''
        void main() { result = shTapCube(0, texelFetch(queries, ivec2(gl_FragCoord.xy), 0).xyz); }
        ''')
    vertices = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], np.float32).tobytes())
    vao = ctx.vertex_array(program, [(vertices, '2f', 'in_pos')])
    try:
        fbo.use()
        ctx.viewport = 0, 0, len(directions), 1
        ctx.disable(moderngl.BLEND | moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        for unit, (name, tex) in enumerate([('t_sh', atlas), ('t_shadowData', data), ('queries', query)]):
            tex.use(unit)
            program[name].value = unit
        vao.render(moderngl.TRIANGLE_STRIP)
        np.testing.assert_array_equal(np.frombuffer(output.read(), np.float32), expected)
    finally:
        for resource in [vao, vertices, program, fbo, output, query, data, atlas, neighbor, *faces]:
            resource.release()


@pytest.mark.parametrize('kind,faces', [('directional', 1), ('spot', 1), ('point', 6), ('sphere-area', 6)])
def test_declared_maximum_shadow_resolution_is_not_clamped(monkeypatch, kind, faces):
    # Verify actual render-target allocation requests without allocating GiB of
    # GPU memory in CI. Small real maps are rendered in the integration tests.
    requested = []

    class Target:
        def release(self): pass
        def use(self): pass
        def clear(self, *args, **kwargs): pass

    def texture(size, *args, **kwargs):
        requested.append(size)
        return Target()

    ctx = SimpleNamespace(info={'GL_MAX_TEXTURE_SIZE': 16384}, texture=texture,
                          depth_renderbuffer=lambda size: Target(), framebuffer=lambda *args: Target(),
                          enable=lambda *args: None, disable=lambda *args: None)
    monkeypatch.setattr(gpu, 'draw_depth', lambda *args: None)
    from scenerender.three.lights import LightState
    light = LightState(None, kind, np.ones(3), np.array([0., 500., 0.]), np.array([1., 0., 0.]),
                       np.array([0., 0., -1.]), np.array([0., -1., 0.]), map_size=8192)
    shadow = gpu.render_shadow(SimpleNamespace(ctx=ctx), [SimpleNamespace(cast=True)], light,
                               (np.zeros(3), 100.), 1., {})
    assert requested == [(8192, 8192)]*faces
    assert shadow.q[2] == 8192
    assert len(shadow.faces) == faces
