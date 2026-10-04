"""Raster references remain defined across a collapsed scene canvas."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.masks import matte_coverage
from scenerender.nodes.particles import get_emitter, particles_at
from scenerender.nodes.particle_collisions import snapshots
from scenerender.raster import Buf, sample_projective
from scenerender.references import scene_space
from scenerender.render import Renderer


WALL = '<shape id="wall" shape="rect" x="55" y="20" width="10" height="60"><rigidBody type="static"/></shape>'
PARTICLE = '<particleEmitter id="p" x="30" y="50" speed="100" direction="0" rate="0" lifetime="4" collide="true" bounce="1"><burst time="0" count="1"/></particleEmitter>'


def write_scene(tmp_path, symbols, body, *, name='scene', extra='', assets='', scale=1):
    path = tmp_path/(name+'.xml')
    path.write_text('<scene version="1.1"><project width="160" height="120" duration="4" fps="10" linearLight="false"/>'
                    +assets+('<symbols>'+symbols+'</symbols>' if symbols else '')+'<composition>'+body+'</composition>'+extra+'<physics gravityY="0"/></scene>')
    return Renderer.open(str(path), strict=True, scale=scale)


def state(r, t, ref='i/p'):
    node, ctx = r.rc.ev.reference(ref, None, Ctx(t, t))
    loc = r.rc.node_location(node, ctx)
    em = get_emitter(loc.rc, node, ctx)
    p = particles_at(loc.rc, node, ctx)
    assert len(em.colliders) <= 3 and len(em.transforms) <= 8
    return np.stack([p[k] for k in ('x', 'y', 'vx', 'vy')], axis=-1)


@pytest.mark.parametrize('mode', ['alpha', 'luma', 'alpha-inverted', 'luma-inverted'])
@pytest.mark.parametrize('axis', ['x', 'y', 'both'])
@pytest.mark.parametrize('shared', [False, True])
def test_external_matte_samples_collapsed_axis_with_color_opacity_and_clock(tmp_path, mode, axis, shared):
    # An integer-aligned one-pixel strip in the source scene becomes a whole
    # band (or the whole canvas) when its centre is sampled by a collapsed axis.
    attrs = {'x': 'x="40.5" y="10" scaleX="0"',
             'y': 'x="10" y="40.5" scaleY="0"',
             'both': 'x="40.5" y="40.5" scaleX="0" scaleY="0"'}[axis]
    strip = {'x': 'x="40" y="20" width="1" height="60"',
             'y': 'x="20" y="40" width="60" height="1"',
             'both': 'x="40" y="40" width="1" height="1"'}[axis]
    mask = f'<shape id="mask" shape="rect" {strip} fill="#80ff80"><expression property="opacity">.6+.2*time</expression></shape>'
    ref = 'i/mask' if shared else 'mask'
    wall = f'<shape id="wall" shape="rect" width="160" height="120" matteMode="{mode}"><expression property="matte">"{ref}"</expression></shape>'
    symbols = '<symbol id="s" width="160" height="120" duration="4">'+wall+'</symbol>'
    if shared:
        symbols += f'<symbol id="outer" width="160" height="120" duration="4">{mask}<instance id="a" symbol="s" speed="2" {attrs}/></symbol>'
        body, consumer = '<instance id="i" symbol="outer" speed="3" rotation="20" scaleY="0"/>', 'i/a/wall'
    else:
        body, consumer = mask+f'<instance id="i" symbol="s" speed="2" {attrs}/>', 'i/wall'
    r = write_scene(tmp_path, symbols, body, scale=2 if shared else .5)
    rect = (-3, -2, 163, 122)
    yy, xx = np.mgrid[rect[1]:rect[3], rect[0]:rect[2]]
    band = ((yy >= 10) & (yy < 70) if axis == 'x' else
            (xx >= 10) & (xx < 70) if axis == 'y' else np.ones_like(xx, bool))
    for time in (.2, .1, .3, .2):
        node, ctx = r.rc.ev.reference(consumer, None, Ctx(time, time))
        rc = scene_space(r.rc, node, ctx)
        alpha = .6+.2*time*(3 if shared else 1)
        value = alpha if mode.startswith('alpha') else alpha*(.2126*128/255+.7152+.0722*128/255)
        expected = band*value
        if mode.endswith('inverted'):
            expected = 1-expected
        np.testing.assert_allclose(matte_coverage(rc, node, rect, ctx), expected, atol=1e-7)


@pytest.mark.parametrize('kind', ['matte', 'group_matte', 'effect_source', 'particle_matte'])
@pytest.mark.parametrize('scale', [.5, 2])
def test_reappearing_instance_matches_independent_collision_window(tmp_path, kind, scale):
    mask = '<shape id="mask" shape="rect" width="160" height="120"/>'
    extra = ''
    if kind == 'particle_matte':
        mask = '<particleEmitter id="mask" x="80" y="60" speed="0" rate="0" lifetime="4" size="160" shape="square"><burst time="0" count="1"/></particleEmitter>'
    if kind == 'effect_source':
        # A white difference plate removes the white wall where it is present.
        mask = mask.replace('/>', ' fill="#ffffff"/>')
        wall = WALL.replace('id="wall"', 'id="wall" effects="key"')
        extra = '<effects><effect id="key" type="difference-key" source="mask" tolerance=".1" softness="0"/></effects>'
        clock_opacity = 'time &lt; .5 ? 1 : 0'
    else:
        wall = WALL.replace('id="wall"', 'id="wall" matte="mask"')
        if kind == 'group_matte':
            wall = '<group id="g" matte="mask">'+WALL+'</group>'
        clock_opacity = 'time &lt; .5 ? 0 : 1'
    instance = '<instance id="i" symbol="s" speed="2" x="12" rotation="15"><expression property="scaleX">time &lt; .25 ? 0 : .8</expression></instance>'
    if kind == 'effect_source':
        # Keep the entire preappearance wall away from the plate's edge, so
        # this timing control has a flat contact normal even after smoothing.
        instance = instance.replace('x="12"', 'x="0"')
    symbols = '<symbol id="s" width="160" height="120" duration="4">'+wall+PARTICLE+'</symbol>'
    r = write_scene(tmp_path, symbols, mask+instance, extra=extra, scale=scale)
    control_wall = WALL.replace('<rigidBody', f'<expression property="opacity">{clock_opacity}</expression><rigidBody')
    control_symbols = '<symbol id="s" width="160" height="120" duration="4">'+control_wall+PARTICLE+'</symbol>'
    control = write_scene(tmp_path, control_symbols, instance, name='control', scale=scale)
    r.frame_linear(.3)  # replay starts from a visible frame
    for t in (.3, .1, .4, .3):
        np.testing.assert_allclose(state(r, t), state(control, t), atol=1e-7)
        fresh = Renderer.open(r.doc.path, strict=True, scale=scale)
        np.testing.assert_allclose(state(r, t), state(fresh, t), atol=1e-7)
    if kind != 'effect_source':
        np.testing.assert_allclose(state(r, .3), [[90, 50, 100, 0]], atol=1e-7)


@pytest.mark.parametrize('offset', [-400, 400])
@pytest.mark.parametrize('kind', ['shape', 'particle', 'layer'])
def test_reference_can_sample_source_outside_the_composition_frame(tmp_path, offset, kind):
    wall = '<shape id="wall" shape="rect" width="160" height="120" matte="mask"/>'
    symbols = '<symbol id="s" width="160" height="120" duration="4">'+wall+'</symbol>'
    source = f'<shape id="mask" shape="rect" x="{offset}" width="1" height="120"/>'
    assets = ''
    if kind == 'particle':
        source = f'<particleEmitter id="mask" x="{offset+60}" y="60" size="120" shape="square" rate="0" speed="0" lifetime="4"><burst time="0" count="1"/></particleEmitter>'
    elif kind == 'layer':
        source = f'<layer id="mask" asset="image" x="{offset}"/>'
        assets = '<assets><generator id="image" kind="solid" width="1" height="120" paint="#ffffff"/></assets>'
    body = source+f'<instance id="i" symbol="s" x="{offset+.5}" scaleX="0"/>'
    r = write_scene(tmp_path, symbols, body, assets=assets)
    node, ctx = r.rc.ev.reference('i/wall', None, Ctx(.2, .2))
    actual = matte_coverage(scene_space(r.rc, node, ctx), node, (0, 0, 160, 120), ctx)
    np.testing.assert_array_equal(actual, np.ones((120, 160)))


@pytest.mark.parametrize('kind', ['difference-key', 'displacement-map'])
def test_effect_source_pullback_covers_padded_pixels_outside_the_frame(tmp_path, kind):
    wall = '<shape id="wall" shape="rect" x="-1" y="10" width="10" height="20" effects="fx"/>'
    symbols = '<symbol id="s" width="160" height="120" duration="4">'+wall+'</symbol>'
    body = '<shape id="mask" shape="rect" x="40" y="-30" width="1" height="200"/>' \
           '<instance id="i" symbol="s" x="40.5" scaleX="0"/>'
    extra = f'<effects><effect id="fx" type="{kind}" source="mask" amount="2" tolerance=".1" softness="0"/></effects>'
    r = write_scene(tmp_path, symbols, body, extra=extra)
    node, ctx = r.rc.ev.reference('i/wall', None, Ctx(.2, .2))
    out = scene_space(r.rc, node, ctx).render_reference(node, ctx, force=True)
    if kind == 'difference-key':
        np.testing.assert_allclose(out.buf.px, 0, atol=1e-7)
    else:
        # White displacement samples two pixels down/right, shifting the
        # visible rectangle two pixels up/left, including its negative x part.
        control = write_scene(tmp_path, '', wall.replace(' effects="fx"', '').replace('x="-1"', 'x="-3"').replace('y="10"', 'y="8"'), name='control')
        expected = control.rc.render_reference(control.doc.ids['wall'], Ctx(.2, .2), force=True)
        np.testing.assert_allclose(out.buf.px, expected.buf.region(out.buf.rect), atol=1e-7)


@pytest.mark.parametrize('invert', [False, True])
def test_transition_matte_pullback_preserves_full_canvas_band(tmp_path, invert):
    a = '<shape id="a" shape="rect" width="160" height="120" fill="#ff0000"/>'
    b = '<shape id="b" shape="rect" width="160" height="120" fill="#0000ff"/>'
    tr = '<transition id="tr" type="luma" from="a" to="b" duration="1" matte="mask" softness="0">' \
         f'<param name="invert" value="{int(invert)}"/></transition>'
    symbols = '<symbol id="s" width="160" height="120" duration="4">'+a+b+tr+'</symbol>'
    body = '<shape id="mask" shape="rect" x="40" y="20" width="1" height="60"/>' \
           '<instance id="i" symbol="s" x="40.5" scaleX="0"/>'
    r = write_scene(tmp_path, symbols, body)
    node, ctx = r.rc.ev.reference('i/a', None, Ctx(.2, .2))
    rc = scene_space(r.rc, node, ctx)
    from scenerender.transitions.wipes import luma
    red = Buf(np.broadcast_to([1, 0, 0, 1], (120, 160, 4)).astype(np.float32).copy())
    blue = Buf(np.broadcast_to([0, 0, 1, 1], (120, 160, 4)).astype(np.float32).copy())
    actual = luma(rc, r.doc.ids['tr'], red, blue, .5, ctx).px
    band = np.zeros((120, 160), bool); band[20:80] = True
    if invert:
        band = ~band
    expected = np.where(band[..., None], red.px, blue.px)
    np.testing.assert_allclose(actual, expected, atol=1e-7)


@pytest.mark.parametrize('angle', [-35, 25])
@pytest.mark.parametrize('placement', ['leaf', 'group'])
@pytest.mark.parametrize('collapsed', [False, True])
def test_external_matte_is_sampled_in_projected_frame_coordinates(tmp_path, angle, placement, collapsed):
    # Scene space (CONVENTIONS 5.x): the camera sits at the frame centre,
    # 160 px in front of the z=0 plane (negative z), and rotationY signs flip.
    camera = '<camera id="camera" projection="perspective" fov="90" x="80" y="60" z="-160"/>'
    attrs = f'threeD="true" rotationY="{-angle}"'
    if placement == 'leaf':
        wall = WALL.replace('id="wall"', f'id="wall" {attrs} matte="mask"')
    else:
        wall = f'<group id="g" {attrs} matte="mask">'+WALL+'</group>'
    probe = PARTICLE.replace('<burst time="0"', '<burst time="3"')
    symbols = '<symbol id="s" width="160" height="120" duration="4">'+camera+wall+probe+'</symbol>'
    body = '<shape id="mask" shape="rect" x="-1000" y="40" width="2000" height="10"/>' \
           f'<instance id="i" symbol="s" x="40.5" scaleX="{0 if collapsed else .8}"/>'
    r = write_scene(tmp_path, symbols, body)
    # The outer x map stays inside the source's wide opaque band. Its y map
    # is identity, so in the scene the result is the projected wall gated
    # by y=40..50. Check interiors separately from the resampled band edges.
    control = write_scene(tmp_path, '', camera+wall.replace(' matte="mask"', ''), name='control')
    expected = control.frame_linear(.2)[..., 3]
    node, ctx = r.rc.ev.reference('i/p', None, Ctx(.2, .2))
    loc = r.rc.node_location(node, ctx)
    em = get_emitter(loc.rc, node, ctx)
    col = snapshots(em, ctx.t)[r.doc.ids['wall']]
    yy, xx = np.mgrid[0:120, 0:160]+.5
    actual = col.sample(xx, yy)
    interior = (yy > 42) & (yy < 48)
    exterior = (yy < 38) | (yy > 52)
    assert actual.sum() > 5
    np.testing.assert_allclose(actual[interior], expected[interior], atol=1/255)
    np.testing.assert_allclose(actual[exterior], 0, atol=1/255)


@pytest.mark.parametrize('rank', [0, 1, 2])
@pytest.mark.parametrize('projective', [False, True])
def test_forward_sampler_matches_analytic_affine_color_field(rank, projective):
    # Every channel is an affine function of source coordinates. Bilinear
    # interpolation must reproduce that function at each mapped pixel centre.
    yy, xx = np.mgrid[-4:20, -3:24]+.5
    channels = np.stack([.1+.005*xx, .2+.004*yy, .1+.002*(xx+yy), np.full_like(xx, .8)], -1)
    src = Buf(channels.astype(np.float32), -3, -4)
    M = np.array([[.7 if rank == 2 else 0, 0, 2.25], [.1 if rank else 0, .6 if rank else 0, 1.75],
                  [.003 if projective else 0, .002 if projective else 0, 1.]])
    y, x = np.mgrid[0:10, 0:12]+.5
    w = M[2, 0]*x+M[2, 1]*y+1
    u, v = (M[0, 0]*x+2.25)/w, (M[1, 0]*x+M[1, 1]*y+1.75)/w
    expected = np.stack([.1+.005*u, .2+.004*v, .1+.002*(u+v), np.full_like(u, .8)], -1)
    np.testing.assert_allclose(sample_projective(src, M, (0, 0, 12, 10)).px, expected, atol=1e-7)


@pytest.mark.parametrize('lookup', [np.diag([1, 1, -1]), np.zeros((3, 3)),
                                  np.array([[0, 0, 1e100], [0, 0, 1e100], [0, 0, 1]])])
def test_forward_sampler_transparently_excludes_poles_and_outside_coordinates(lookup):
    with np.errstate(all='raise'):
        buf = sample_projective(Buf(np.ones((2, 2, 4), np.float32)), lookup, (0, 0, 4, 3))
    np.testing.assert_array_equal(buf.px, 0.)
