"""Spatial particle paints compared with shape renders and pixel arithmetic."""
import numpy as np
import pytest
from PIL import Image

from scenerender.render import Renderer


STOPS = '<stop offset="0" color="#FF0000FF"/><stop offset="1" color="#0000FFFF"/>'
PAINTS = {
    'linear': '<linearGradient id="paint" interpolationSpace="srgb" dither="false">'+STOPS+'</linearGradient>',
    'radial': '<radialGradient id="paint" interpolationSpace="srgb" dither="false" fx=".3">'+STOPS+'</radialGradient>',
    'conic': '<conicGradient id="paint" interpolationSpace="srgb" dither="false">'+STOPS+'</conicGradient>',
    'mesh': '<meshGradient id="paint" rows="2" cols="2" interpolationSpace="srgb">'
            '<point row="0" col="0" color="#FF0000FF"/><point row="0" col="1" color="#00FF00FF"/>'
            '<point row="1" col="0" color="#0000FFFF"/><point row="1" col="1" color="#FFFFFFFF"/></meshGradient>',
    'pattern': '<pattern id="paint" asset="tile" tileWidth="10" tileHeight="10" rotation="20"/>',
}
TILE = '<assets><generator id="tile" kind="checkerboard" width="10" height="10" scale="5" paint="#FF0000FF" paint2="#0000FFFF"/></assets>'


def scene(tmp_path, name, body, paints='', assets='', symbols='', linear=False, styles=''):
    path = tmp_path/(name+'.xml')
    path.write_text('<scene version="1.1"><project width="96" height="96" duration="5" fps="10" '
                    f'linearLight="{str(linear).lower()}"/>'+assets+styles+'<paints>'+paints+'</paints>'+symbols+
                    '<composition>'+body+'</composition></scene>')
    return Renderer.open(str(path), strict=True)


def particle(color='url(#paint)', shape='square', extra='', children='', size=40):
    return (f'<particleEmitter id="p" x="48" y="48" rate="0" lifetime="4" speed="0" size="{size}" '
            f'shape="{shape}" color="{color}" {extra}><burst time="0" count="1"/>{children}</particleEmitter>')


@pytest.mark.parametrize('kind', PAINTS)
@pytest.mark.parametrize('shape', ['square', 'disc'])
@pytest.mark.parametrize('rotation', [0, 31])
def test_particle_paints_match_shape_sources(tmp_path, kind, shape, rotation):
    body = particle(shape=shape, extra=f'rotation0="{rotation}"')
    expected_shape = 'rect' if shape == 'square' else 'ellipse'
    control = (f'<shape id="control" shape="{expected_shape}" x="48" y="48" width="40" height="40" '
               f'anchorX="20" anchorY="20" rotation="{rotation}" fill="url(#paint)"/>')  # centre of the 40px box; % anchors refer to the parent (CONVENTIONS 1.2)
    r = scene(tmp_path, 'particle', body, PAINTS[kind], TILE)
    q = scene(tmp_path, 'control', control, PAINTS[kind], TILE)
    actual, expected = r.frame_linear(.2), q.frame_linear(.2)
    # Native shapes use flattened paths; particles use Cairo arcs/rectangles.
    # Compare paints where both cover a pixel and verify particle coverage with
    # its own opaque control, rather than equating different edge tessellations.
    interior = (expected[..., 3] > .999) & (actual[..., 3] > .999)
    # Cairo's ARGB32 and float pattern filters can differ by a few channel levels.
    np.testing.assert_allclose(actual[interior], expected[interior], atol=(5 if kind == 'pattern' else 2)/255)
    mask = scene(tmp_path, 'coverage', particle('#FFFFFFFF', shape, f'rotation0="{rotation}"'))
    if kind == 'mesh':
        # A mesh ends at its outer patch; it does not extend into edge pixels
        # whose centres lie outside the patch, even when shape coverage is nonzero.
        assert np.all(actual[..., 3] <= mask.frame_linear(.2)[..., 3]+1e-7)
    else:
        np.testing.assert_array_equal(actual[..., 3], mask.frame_linear(.2)[..., 3])
    assert np.std(actual[40:55, 40:55, :3]) > .05


@pytest.mark.parametrize('linear', [False, True])
def test_linear_particle_paint_matches_analytic_pixel_centres(tmp_path, linear):
    r = scene(tmp_path, 'linear', particle(), PAINTS['linear'], linear=linear)
    actual = r.frame_linear(.2)[48, 30:66]
    u = (np.arange(30, 66)+.5-28)/40
    expected = np.column_stack([1-u, np.zeros_like(u), u, np.ones_like(u)])
    if linear:
        v = expected[:, :3]
        expected[:, :3] = np.where(v <= .04045, v/12.92, ((v+.055)/1.055)**2.4)
    np.testing.assert_allclose(actual, expected, atol=2/255)


@pytest.mark.parametrize('style', ['disc', 'square', 'sprite', 'streak', 'soft', 'ring', 'flutter'])
def test_constant_paint_matches_flat_colour_for_all_particle_styles(tmp_path, style):
    extra = 'opacityEnd=".6"'
    shape = style
    if style in ('soft', 'ring', 'flutter'):
        shape = 'square' if style == 'flutter' else 'disc'
        extra += ' preset="'+{'soft': 'bokeh', 'ring': 'bubbles', 'flutter': 'confetti'}[style]+'"'
        extra += ' speedVariance="0" sizeVariance="0" lifetimeVariance="0" gravityY="0" drag="0"'
    if style == 'sprite':
        extra += ' sprite="tile"'
    children = '<expression property="speed">12</expression>' if style == 'streak' else ''
    paints = ('<linearGradient id="paint" interpolationSpace="srgb" dither="false">'
              '<stop offset="0" color="#80C04099"/><stop offset="1" color="#80C04099"/></linearGradient>')
    a = scene(tmp_path, 'spatial', particle(extra=extra, shape=shape, children=children, size=16), paints, TILE)
    b = scene(tmp_path, 'flat', particle('#80C04099', shape, extra, children, size=16), paints, TILE)
    for t in (.2, .8, .2):
        np.testing.assert_allclose(a.frame_linear(t), b.frame_linear(t), atol=2/255)


def test_spatial_sprite_tint_multiplies_premultiplied_pixels(tmp_path):
    pixels = np.zeros((40, 40, 4), np.uint8)
    pixels[..., :3] = [64, 180, 240]
    pixels[..., 3] = np.arange(40, dtype=np.uint8)[None, :]*5+50
    Image.fromarray(pixels).save(tmp_path/'sprite.png')
    assets = '<assets><image id="sprite" src="sprite.png" width="40" height="40"/></assets>'
    a = scene(tmp_path, 'sprite', particle(shape='sprite', extra='sprite="sprite"'), PAINTS['linear'], assets)
    field = scene(tmp_path, 'field', '<shape id="s" shape="rect" width="40" height="40" x="28" y="28" fill="url(#paint)"/>', PAINTS['linear'])
    texture = scene(tmp_path, 'texture', particle('#FFFFFFFF', 'sprite', 'sprite="sprite"'), assets=assets)
    expected = field.frame_linear(.2)*texture.frame_linear(.2)
    np.testing.assert_allclose(a.frame_linear(.2), expected, atol=2/255)


def test_paint_spans_the_requested_trail(tmp_path):
    extra = 'trail="1"'
    child = '<expression property="speed">20</expression><expression property="direction">0</expression>'
    a = scene(tmp_path, 'painted', particle(shape='streak', extra=extra, children=child, size=4), PAINTS['linear'])
    b = scene(tmp_path, 'mask', particle('#FFFFFFFF', 'streak', extra, child, size=4))
    t = 1.25
    # Head is at x=73, tail at x=53; the 4px stroke expands the box to [51,75].
    u = np.clip((np.arange(96)+.5-51)/24, 0, 1)
    rgba = np.stack([1-u, np.zeros_like(u), u, np.ones_like(u)], axis=-1)
    expected = b.frame_linear(t)*rgba[None, :, :]
    np.testing.assert_allclose(a.frame_linear(t), expected, atol=3/255)


@pytest.mark.parametrize('mode', ['both', 'start', 'end'])
@pytest.mark.parametrize('time', [1., 2.])
def test_spatial_start_and_end_paints_blend_colour_and_opacity(tmp_path, mode, time):
    first = PAINTS['linear'].replace('#FF0000FF', '#FF000080').replace('#0000FFFF', '#0000FF80')
    second = '<linearGradient id="other" interpolationSpace="srgb" dither="false">' \
             '<stop offset="0" color="#00FF00C0"/><stop offset="1" color="#FFFF00C0"/></linearGradient>'
    start = '#FF000080' if mode == 'end' else 'url(#paint)'
    end = '#00FF00C0' if mode == 'start' else 'url(#other)'
    paints = first+second
    def control(name, spec):
        return scene(tmp_path, name, '<shape id="s" shape="rect" x="28" y="28" width="40" height="40" fill="'+spec+'"/>', paints)
    a = control('start', start).frame_linear(time)[30:66, 30:66]
    b = control('end', end).frame_linear(time)[30:66, 30:66]
    sa, sb = a.copy(), b.copy()
    sa[..., :3] /= a[..., 3:4]
    sb[..., :3] /= b[..., 3:4]
    expected = sa+(sb-sa)*(time/4)
    expected[..., :3] *= expected[..., 3:4]
    r = scene(tmp_path, 'blend', particle(start, extra=f'colorEnd="{end}"'), paints)
    np.testing.assert_allclose(r.frame_linear(time)[30:66, 30:66], expected, atol=2/255)


def test_conic_diagonals_match_analytic_angles(tmp_path):
    r = scene(tmp_path, 'conic', particle(), PAINTS['conic'])
    frame = r.frame_linear(.2)
    for y, x in ((35, 35), (35, 60), (60, 35), (60, 60)):
        u = ((np.arctan2(y+.5-48, x+.5-48)+np.pi/2)/(2*np.pi)) % 1
        np.testing.assert_allclose(frame[y, x], [1-u, 0, u, 1], atol=2/255)


@pytest.mark.parametrize('kind', ['linearGradient', 'radialGradient', 'conicGradient'])
def test_user_and_object_paint_coordinates_agree(tmp_path, kind):
    object_attrs = 'x2="1"' if kind == 'linearGradient' else 'cx=".5" cy=".5"'+(' r=".5"' if kind == 'radialGradient' else '')
    user_attrs = 'x2="40"' if kind == 'linearGradient' else 'cx="20" cy="20"'+(' r="20"' if kind == 'radialGradient' else '')
    def make(name, units, attrs):
        paint = f'<{kind} id="paint" units="{units}" {attrs} interpolationSpace="srgb" dither="false">{STOPS}</{kind}>'
        return scene(tmp_path, name, particle(), paint).frame_linear(.2)
    np.testing.assert_allclose(make('object', 'object', object_attrs), make('user', 'user', user_attrs), atol=1/255)


@pytest.mark.parametrize('kind', ['gradient', 'pattern'])
def test_animated_paint_and_assets_follow_each_instance_clock(tmp_path, kind):
    change = '<expression property="color">time &lt; .5 ? "#FF0000FF" : "#00FF00FF"</expression>'
    paints = '<linearGradient id="paint" interpolationSpace="srgb" dither="false"><stop offset="0" color="#FF0000FF">'+change+'</stop><stop offset="1" color="#0000FFFF"/></linearGradient>'
    assets = ''
    if kind == 'pattern':
        paints = '<pattern id="paint" asset="tile"/>'
        assets = '<assets><generator id="tile" kind="solid" width="8" height="8">'+change.replace('property="color"', 'property="paint"')+'</generator></assets>'
    p = '<particleEmitter id="p" x="16" y="16" size="24" rate="0" lifetime="4" speed="0" shape="square" color="url(#paint)"><burst time="0" count="1"/></particleEmitter>'
    symbols = '<symbols><symbol id="s" width="32" height="32" duration="4">'+p+'</symbol></symbols>'
    body = '<instance id="a" symbol="s"/><instance id="b" symbol="s" speed="2" x="48"/>'
    r = scene(tmp_path, 'instances', body, paints, assets, symbols)
    u = (10.5-4)/24 if kind == 'gradient' else 0
    for t in (.3, .7, .3):
        frame = r.frame_linear(t)
        np.testing.assert_allclose(frame[16, 10], [1-u, 0, u, 1] if t < .5 else [0, 1-u, u, 1], atol=1/255)
        np.testing.assert_allclose(frame[16, 58], [0, 1-u, u, 1], atol=1/255)


def test_particle_gradient_dither_is_effective_and_seek_independent(tmp_path):
    paint = '<linearGradient id="paint" interpolationSpace="srgb" dither="true"><stop offset="0" color="#333333FF"/><stop offset="1" color="#444444FF"/></linearGradient>'
    a = scene(tmp_path, 'on', particle(), paint)
    b = scene(tmp_path, 'off', particle(), paint.replace('dither="true"', 'dither="false"'))
    first = a.frame_linear(.2)
    a.frame_linear(.8)
    np.testing.assert_array_equal(first, a.frame_linear(.2))
    assert np.count_nonzero(first != b.frame_linear(.2)) > 100


def test_palette_particles_can_fade_to_a_spatial_end_paint(tmp_path):
    extra = ('preset="confetti" speedVariance="0" sizeVariance="0" lifetimeVariance="0" '
             'gravityY="0" drag="0" rotationVariance="0" angularVelocity="0"')
    body = particle(extra=extra).replace(' color="url(#paint)"', '')
    base = scene(tmp_path, 'palette', body).frame_linear(1)[40:56, 40:56]
    end = scene(tmp_path, 'paint', particle(extra=extra), PAINTS['linear']).frame_linear(1)[40:56, 40:56]
    r = scene(tmp_path, 'fade', body.replace('preset="', 'colorEnd="url(#paint)" preset="'), PAINTS['linear'])
    actual = r.frame_linear(1)[40:56, 40:56]
    # Fully covered pixels avoid confetti's flattened bottom edge.
    interior = (base[..., 3] > .999) & (end[..., 3] > .999)
    assert interior.sum() > 10
    np.testing.assert_allclose(actual[interior], .75*base[interior]+.25*end[interior], atol=2/255)


def test_trail_paint_opacity_is_applied_once_at_segment_joins(tmp_path):
    paint = '<linearGradient id="paint" interpolationSpace="srgb" dither="false"><stop offset="0" color="#80C04080"/><stop offset="1" color="#80C04080"/></linearGradient>'
    children = '<expression property="speed">20</expression><expression property="direction">0</expression>'
    a = scene(tmp_path, 'paint', particle(shape='streak', extra='trail="1"', children=children, size=4), paint)
    b = scene(tmp_path, 'flat', particle('#80C04080', 'streak', 'trail="1"', children=children, size=4))
    np.testing.assert_allclose(a.frame_linear(1.2), b.frame_linear(1.2), atol=1/255)


@pytest.mark.parametrize('consumer', ['particle', 'shape'])
def test_style_tokens_resolve_spatial_paints(tmp_path, consumer):
    styles = '<styles><token name="source" value="url(#paint)"/><token name="shade" value="var(--source)"/></styles>'
    body = particle('var(--shade)') if consumer == 'particle' else '<shape id="s" shape="rect" x="28" y="28" width="40" height="40" fill="var(--shade)"/>'
    a = scene(tmp_path, 'token', body, PAINTS['linear'], styles=styles)
    b = scene(tmp_path, 'direct', body.replace('var(--shade)', 'url(#paint)'), PAINTS['linear'])
    np.testing.assert_array_equal(a.frame_linear(.2), b.frame_linear(.2))
    assert a.frame_linear(.2)[48, 32, 0] > .8


@pytest.mark.parametrize('space', ['srgb', 'linear', 'oklab', 'oklch'])
@pytest.mark.parametrize('midpoint', [.2, .5, .8])
def test_conic_device_samples_match_scalar_stop_interpolation(tmp_path, space, midpoint):
    import math
    from scenerender.paint import mix_color
    from scenerender.values import parse_color
    c0, c1 = '#C0206080', '#10E080C0'
    paints = (f'<conicGradient id="paint" interpolationSpace="{space}" dither="false">'
              f'<stop offset="0" color="{c0}" midpoint="{midpoint}"/><stop offset="1" color="{c1}"/></conicGradient>')
    frame = scene(tmp_path, 'conic-samples', particle(), paints).frame_linear(.2)
    for y in range(32, 65, 4):
        for x in range(32, 65, 4):
            angle = ((math.atan2(y+.5-48, x+.5-48)+math.pi/2)/(2*math.pi)) % 1
            fraction = angle**(math.log(.5)/math.log(midpoint))
            expected = np.array(mix_color(parse_color(c0), parse_color(c1), fraction, space))
            expected[:3] *= expected[3]
            np.testing.assert_allclose(frame[y, x], expected, atol=1/255)
