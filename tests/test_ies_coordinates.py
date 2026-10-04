"""LM-63 coordinate frames, raw-knot interpolation and GPU profile addressing."""
from types import SimpleNamespace
from collections import OrderedDict

import numpy as np
import pytest

from scenerender.three.loaders import load_ies


def write_profile(tmp_path, kind, vertical, horizontal, candela, name='profile.ies'):
    p = tmp_path / name
    records = (vertical, horizontal, np.asarray(candela).ravel())
    p.write_text(f'IESNA:LM-63-2002\nTILT=NONE\n1 -1 1 {len(vertical)} {len(horizontal)} {kind} 1 0 0 0\n1 1 10\n'
                 + '\n'.join(' '.join(map(str, row)) for row in records) + '\n')
    return p


def direction(kind, vertical, horizontal):
    """Forward goniometer rotations; components along right, up, and nadir."""
    v, h = np.radians(np.broadcast_arrays(vertical, horizontal))
    if kind == 1:
        return np.stack((np.sin(v)*np.cos(h), np.sin(v)*np.sin(h), np.cos(v)), axis=-1)
    if kind == 3:
        return np.stack((np.cos(v)*np.cos(h), -np.cos(v)*np.sin(h), -np.sin(v)), axis=-1)
    return np.stack((np.sin(v), -np.cos(v)*np.sin(h), np.cos(v)*np.cos(h)), axis=-1)


def cpu_sample(profile, directions):
    theta = np.degrees(np.arccos(np.clip(directions[..., 2], -1., 1.)))
    phi = np.degrees(np.arctan2(directions[..., 1], directions[..., 0]))
    return profile.sample(theta, phi)


def gpu_sample(profiles, directions):
    """Execute the production lookup and upload path, returning one sample per pixel."""
    from scenerender import gl
    from scenerender.three.renderer import _ies_texture
    from scenerender.three.shaders import IES_SAMPLE
    import moderngl
    if not gl.available():
        pytest.skip('no GL')
    ctx = gl.context()
    r = SimpleNamespace(ctx=ctx, ies_textures=OrderedDict())
    tex, offsets = _ies_texture(r, profiles)
    directions = np.asarray(directions, np.float32).reshape(len(profiles), 3)
    query = ctx.texture((len(profiles), 1), 4,
                        np.c_[directions, offsets].astype(np.float32).tobytes(), dtype='f4')
    output = ctx.texture((len(profiles), 1), 1, dtype='f4')
    fbo = ctx.framebuffer([output])
    program = ctx.program(vertex_shader=gl.FULLSCREEN_VS, fragment_shader='''#version 410
        uniform sampler2D t_ies; uniform sampler2D queries;
        out float result;
        ''' + IES_SAMPLE + '''
        void main() {
            vec4 q = texelFetch(queries, ivec2(gl_FragCoord.xy), 0);
            result = iesSample(int(q.w), q.xyz);
        }''')
    vertices = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], np.float32).tobytes())
    vao = ctx.vertex_array(program, [(vertices, '2f', 'in_pos')])
    try:
        fbo.use()
        ctx.viewport = (0, 0, len(profiles), 1)
        ctx.disable(moderngl.BLEND | moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        tex.use(0); query.use(1)
        program['t_ies'].value = 0
        program['queries'].value = 1
        vao.render(moderngl.TRIANGLE_STRIP)
        return np.frombuffer(output.read(), np.float32).copy()
    finally:
        for resource in (vao, vertices, program, fbo, output, query, tex):
            resource.release()


@pytest.mark.parametrize('kind', [1, 2, 3])
@pytest.mark.parametrize('gpu', [False, True])
def test_goniometer_coordinates_and_nonuniform_interpolation(tmp_path, kind, gpu):
    v = np.array([0, 24, 73, 180] if kind == 1 else [-90, -24, 13, 90])
    h = np.array([0, 13, 141, 180] if kind == 1 else [-90, -13, 41, 90])
    cd = 100 + .3*v[None, :] + .2*h[:, None] + .001*v[None, :]*h[:, None]
    p = load_ies(str(write_profile(tmp_path, kind, v, h, cd)))
    assert p.photometric_type == kind
    query_v = np.array([7, 24, 51, 89] if kind == 1 else [-73, -24, 13, 61])
    query_h = np.array([11, 47, 141, 177] if kind == 1 else [-67, -13, 41, 81])
    dv = direction(kind, query_v, query_h)
    expected = (100+.3*query_v+.2*query_h+.001*query_v*query_h)/cd.max()
    actual = gpu_sample([p]*len(dv), dv) if gpu else cpu_sample(p, dv)
    np.testing.assert_allclose(actual, expected, atol=2e-6)


@pytest.mark.parametrize('kind', [2, 3])
@pytest.mark.parametrize('gpu', [False, True])
def test_ab_symmetry_and_unmeasured_hemisphere(tmp_path, kind, gpu):
    v = np.array([0, 17, 90])
    h = np.array([0, 21, 90])
    cd = 100 + v[None, :] + h[:, None]
    p = load_ies(str(write_profile(tmp_path, kind, v, h, cd)))
    vv = np.array([30, 30, -30, -30, 30])
    hh = np.array([60, -60, 60, -60, 120])
    dv = direction(kind, vv, hh)
    expected = np.array([190, 190, 190 if kind == 2 else 0, 190 if kind == 2 else 0, 0])/280
    actual = gpu_sample([p]*len(dv), dv) if gpu else cpu_sample(p, dv)
    np.testing.assert_allclose(actual, expected, atol=2e-6)


@pytest.mark.parametrize('horizontal,queries,expected', [
    ([0], [-120, 0, 56, 299], [1, 1, 1, 1]),
    ([0, 90], [45, 135, 225, 315], [.75]*4),
    ([0, 180], [60, 300, 120, 240], [2/3, 2/3, 5/6, 5/6]),
    ([90, 270], [0, 180, 360, 90, 270], [.75, .75, .75, .5, 1]),
    ([0, 90, 180, 270, 360], [0, 45, 315, 359.9, -.1], [.5, .625, .875, .5008333333, .5008333333]),
    ([0, 90, 180, 270], [0, 45, 315, 359.9, -.1], [.5, .625, .875, .5008333333, .5008333333]),
])
@pytest.mark.parametrize('gpu', [False, True])
def test_type_c_meridian_symmetries_and_wrap(tmp_path, horizontal, queries, expected, gpu):
    # Deliberately varying meridians with a matching closing meridian, when present.
    values = {1: [1], 2: [.5, 1], 4: [.5, .75, 1, 1.25], 5: [.5, .75, 1, 1.25, .5]}[len(horizontal)]
    cd = np.tile(values, (3, 1)).T
    p = load_ies(str(write_profile(tmp_path, 1, [0, 45, 180], horizontal, cd)))
    dv = direction(1, np.full(len(queries), 45), queries)
    actual = gpu_sample([p]*len(dv), dv) if gpu else cpu_sample(p, dv)
    np.testing.assert_allclose(actual, np.asarray(expected)/max(values), atol=2e-6)


@pytest.mark.parametrize('gpu', [False, True])
@pytest.mark.parametrize('kind', [1, 2, 3])
@pytest.mark.parametrize('axis', ['vertical', 'horizontal'])
def test_narrow_beam_uses_original_angle_knots(tmp_path, gpu, kind, axis):
    if axis == 'vertical':
        knots = [0, 17.1, 17.2, 17.3, 90] if kind == 1 else [-90, 17.1, 17.2, 17.3, 90]
        p = load_ies(str(write_profile(tmp_path, kind, knots, [0, 90], [[0, 0, 1, 0, 0]]*2)))
        vv, hh = [0, 17.05, 17.15, 17.2, 17.25, 17.35, 90], np.zeros(7)
    else:
        p = load_ies(str(write_profile(tmp_path, kind, [0, 90], [0, 17.1, 17.2, 17.3, 90],
                                       [[0, 0], [0, 0], [1, 1], [0, 0], [0, 0]])))
        vv, hh = np.full(7, 30), [0, 17.05, 17.15, 17.2, 17.25, 17.35, 90]
    dv = direction(kind, vv, hh)
    actual = gpu_sample([p]*len(dv), dv) if gpu else cpu_sample(p, dv)
    np.testing.assert_allclose(actual, [0, 0, .5, 1, .5, 0, 0], atol=1e-4)


def test_more_than_four_distinct_profiles_and_multiple_texture_rows(tmp_path):
    v, h = np.linspace(0, 180, 151), np.array([0, 33, 70, 90])
    profiles, expected = [], []
    for i in range(9):
        cd = (1+i) + v[None, :]/180 + h[:, None]/90
        profiles.append(load_ies(str(write_profile(tmp_path, 1, v, h, cd, name=f'p{i}.ies'))))
        expected.append((1+i+.25+.5)/(3+i))
    profiles += [None, profiles[0]]  # Unprofiled and repeated lights share the same upload.
    expected += [1., expected[0]]
    np.testing.assert_allclose(gpu_sample(profiles, direction(1, np.full(11, 45), np.full(11, 45))), expected, atol=2e-6)


@pytest.mark.parametrize('kind', [0, 4, 1.5])
def test_invalid_photometric_type_rejected(tmp_path, kind):
    with pytest.raises(ValueError, match='photometric type'):
        load_ies(str(write_profile(tmp_path, kind, [0, 180], [0], [[1, 1]])))


@pytest.mark.parametrize('kind', [1, 2, 3])
def test_rendered_profiles_after_fourth_light(tmp_path, kind):
    from scenerender import gl
    from test_3d_render import doc, frame, FLOOR, WHITE
    if not gl.available():
        pytest.skip('no GL')
    # The lower hemisphere is a constant 0.5; an upper knot sets peak to 1.
    v = [0, 90, 180] if kind == 1 else [-90, 0, 90]
    h = [0] if kind == 1 else [-90, 0, 90]
    cd = [[.5, .5, 1]]*len(h)
    # Type B cannot have a constant lower hemisphere and an upper peak with
    # these knots, so use a complete uniform distribution for its render control.
    factor = .5
    if kind == 2:
        cd = np.ones((3, 3)); factor = 1.
    p = write_profile(tmp_path, kind, v, h, cd)
    # Keep the floor within the measured +/-90-degree A azimuth hemisphere.
    lights = ''.join(f'<light id="l{i}" type="point" x="{30*i-1000}" y="500" pitch="-90" intensity="200" ies="{p}"/>' for i in range(7))
    controls = ''.join(f'<light id="l{i}" type="point" x="{30*i-1000}" y="500" intensity="{200*factor}"/>' for i in range(7))
    scene = doc(tmp_path, FLOOR.format(cs='castShadow="false"'), lights=lights, materials=WHITE, w=96, h=64)
    control = doc(tmp_path, FLOOR.format(cs='castShadow="false"'), lights=controls, materials=WHITE, w=96, h=64, name='control.xml')
    expected = frame(control)
    assert expected[..., :3].max() > .01
    np.testing.assert_allclose(frame(scene), expected, atol=3e-6)


@pytest.mark.parametrize('kind', ['directional', 'point', 'spot', 'rect-area', 'disk-area', 'sphere-area'])
@pytest.mark.parametrize('flags', ['', 'affectsDiffuse="false"'])
def test_profile_scales_all_direct_light_lobes(tmp_path, kind, flags):
    from scenerender import gl
    from test_3d_render import doc, frame, FLOOR, WHITE
    if not gl.available():
        pytest.skip('no GL')
    p = write_profile(tmp_path, 1, [0, 90, 180], [0], [[.25, .25, 1]])
    intensity = 2 if kind == 'directional' else 200
    attrs = f'type="{kind}" y="500" pitch="-90" {flags}'
    scene = doc(tmp_path, FLOOR.format(cs='castShadow="false"'), materials=WHITE, w=96, h=64,
                lights=f'<light id="l" {attrs} intensity="{intensity}" ies="{p}"/>')
    control = doc(tmp_path, FLOOR.format(cs='castShadow="false"'), materials=WHITE, w=96, h=64,
                  lights=f'<light id="l" {attrs} intensity="{intensity*.25}"/>', name='control.xml')
    expected = frame(control)
    assert expected[..., :3].max() > 1e-5
    np.testing.assert_allclose(frame(scene), expected, atol=3e-6)
