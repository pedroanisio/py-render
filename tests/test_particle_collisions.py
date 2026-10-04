"""Rendered collider silhouettes, relative motion and independent clock controls."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.nodes.particles import get_emitter, particles_at
from scenerender.nodes.particle_collisions import snapshots
from scenerender.render import Renderer


def scene(tmp_path, body, *, name='scene', symbols='', extra='', scale=1):
    path = tmp_path/(name+'.xml')
    path.write_text('<scene version="1.1"><project width="128" height="96" fps="10" duration="6" '
                    'linearLight="false"/>'+symbols+'<composition>'+body+'</composition>'+extra+
                    '<physics gravityY="0"/></scene>')
    return Renderer.open(str(path), strict=True, scale=scale)


def emitter(r, t, ref='p'):
    el, ctx = r.rc.ev.reference(ref, None, Ctx(t, t))
    loc = r.rc.node_location(el, ctx)
    return get_emitter(loc.rc, el, ctx), ctx


def live(r, t, ref='p'):
    em, ctx = emitter(r, t, ref)
    p = particles_at(em.rc, em.el, ctx)
    return np.stack([p[k] for k in ('x', 'y', 'vx', 'vy')], axis=-1)


def particle(attrs='', burst='.3'):
    return f'<particleEmitter id="p" rate="0" lifetime="5" collide="true" {attrs}>' \
           f'<burst time="{burst}" count="1"/></particleEmitter>'


@pytest.mark.parametrize('prop,initial,final', [
    ('fill', '#FFFFFF00', '#FFFFFFFF'), ('fill', '#FFFFFFFF', '#FFFFFF00'),
    ('width', '20', '128'), ('width', '128', '20'),
    ('opacity', '0', '1'), ('opacity', '1', '0'),
    ('visible', 'false', 'true'), ('visible', 'true', 'false'),
])
def test_current_silhouette_matches_static_control_after_change(tmp_path, prop, initial, final):
    values = dict(shape='rect', width='128', height='8', y='60', fill='#FFFFFFFF')
    values[prop] = initial
    attributes = ' '.join(f'{k}="{v}"' for k, v in values.items())
    animation = f'<expression property="{prop}">time &lt; .25 ? \'{initial}\' : \'{final}\'</expression>'
    body = f'<shape id="wall" {attributes}>{animation}<rigidBody type="static"/></shape>'
    p = particle('x="80" y="20" speed="80" direction="90" bounce="1"')
    r = scene(tmp_path, body+p)
    control = scene(tmp_path, body.replace(animation, '').replace(f'{prop}="{initial}"', f'{prop}="{final}"')+p,
                    name='control')
    for t in (1., .2, .7, 1.):
        np.testing.assert_allclose(live(r, t), live(control, t), atol=1e-9)
        np.testing.assert_array_equal(live(r, t), live(Renderer.open(r.doc.path, strict=True), t))
    result = live(r, 1.)[0]
    solid = final not in ('#FFFFFF00', '20', '0', 'false')
    assert result[3] == pytest.approx(-80 if solid else 80)


@pytest.mark.parametrize('kind', ['mask', 'effect', 'matte', 'path', 'rotation', 'parent', 'window'])
def test_snapshot_matches_rendered_alpha_at_each_time(tmp_path, kind):
    attrs, content, other, extra = 'shape="rect" width="50" height="40" x="30" y="20"', '', '', ''
    if kind == 'mask':
        content = '<mask type="rect" width="10" height="40"><expression property="width">10+30*time</expression></mask>'
    elif kind == 'effect':
        attrs += ' effects="fx"'
        extra = '<effects><effect id="fx" type="blur"><expression property="radius">1+3*time</expression></effect></effects>'
    elif kind == 'matte':
        attrs += ' matte="m"'
        other = '<shape id="m" shape="rect" x="30" y="20" width="10" height="40"><expression property="width">10+30*time</expression></shape>'
    elif kind == 'path':
        attrs = 'shape="path" path="M0 0 L50 0 L0 40 Z" x="30" y="20" width="50" height="40"'
        content = '<expression property="path">time &lt; .5 ? "M0 0 L50 0 L0 40 Z" : "M0 40 L50 40 L50 0 Z"</expression>'
    elif kind == 'rotation':
        content = '<expression property="rotation">20*time</expression>'
    elif kind == 'parent':
        attrs += ' parent="anchor"'
        other = '<shape id="anchor" shape="rect" width="1" height="1" visible="false"><expression property="x">10*time</expression></shape>'
    elif kind == 'window':
        attrs += ' start=".5" end="1"'
    body = other+f'<shape id="wall" {attrs}>{content}<rigidBody type="static"/></shape>'+particle('speed="0"', burst='4')
    r = scene(tmp_path, body, extra=extra)
    xx, yy = np.meshgrid(np.arange(128)+.5, np.arange(96)+.5)
    for t in (.2, .8, 1.2, .2):
        em, ctx = emitter(r, t)
        cols = snapshots(em, ctx.t)
        actual = cols[r.doc.ids['wall']].sample(xx, yy) if cols else np.zeros_like(xx)
        expected = np.clip(r.frame_linear(t)[..., 3], 0, 1)
        np.testing.assert_allclose(actual, expected, atol=1e-7)
        assert len(em.colliders) <= 3


@pytest.mark.parametrize('kind', ['static', 'kinematic', 'dynamic'])
def test_thin_moving_body_contact_uses_relative_velocity(tmp_path, kind):
    # Wall moves right at 30 px/s. The particle approaches at 300 px/s;
    # an elastic collision therefore leaves it at 2*30-300 = -240 px/s.
    motion = '<expression property="x">60+30*time</expression>' if kind != 'dynamic' else ''
    rb = 'velocityX=".3" linearDamping="0" angularDamping="0"' if kind == 'dynamic' else ''
    wall = f'<shape id="wall" shape="rect" x="60" y="10" width="1" height="80">{motion}' \
           f'<rigidBody type="{kind}" {rb}/></shape>'
    r = scene(tmp_path, wall+particle('x="30" y="50" speed="300" direction="0" bounce="1"', burst='0'))
    p = live(r, .15)[0]
    assert p[0] < 60+30*.15
    assert p[2] == pytest.approx(-240, abs=1e-7)


def test_engulfed_particle_is_ejected_from_deep_flat_interior(tmp_path):
    wall = '<shape id="wall" shape="rect" x="10" y="10" width="100" height="75">' \
           '<expression property="fill">time &lt; .25 ? "#FFFFFF00" : "#FFFFFFFF"</expression>' \
           '<rigidBody type="static"/></shape>'
    r = scene(tmp_path, wall+particle('x="55" y="47" speed="0"', burst='0'))
    p = live(r, .3)[0]
    assert p[0] < 10 or p[0] > 110 or p[1] < 10 or p[1] > 85


@pytest.mark.parametrize('mode', ['sensor', 'hidden_parent'])
def test_noncolliding_bodies_allow_passage(tmp_path, mode):
    wall = '<shape id="wall" shape="rect" x="60" y="10" width="4" height="80">' \
           f'<rigidBody type="static" sensor="{str(mode == "sensor").lower()}"/></shape>'
    if mode == 'hidden_parent':
        wall = '<group id="g" visible="false">'+wall+'</group>'
    r = scene(tmp_path, wall+particle('x="30" y="50" speed="60" direction="0"', burst='0'))
    assert live(r, .8)[0, 0] == pytest.approx(78)


@pytest.mark.parametrize('kind', ['static', 'kinematic', 'dynamic'])
@pytest.mark.parametrize('warp', ['speed', 'reverse', 'remap', 'loop'])
def test_instance_collision_clock_matches_explicit_control(tmp_path, warp, kind):
    content = '<shape id="wall" shape="rect" width="3" height="80" y="5">' \
              '<expression property="x">60+5*prop("source.x")</expression>' \
              f'<rigidBody type="{kind}" velocityX=".05" linearDamping="0"/></shape>' \
              +particle('x="30" y="50" speed="80" direction="0" bounce="1"', burst='0')
    source = '<shape id="source" shape="rect" width="1" height="1" visible="false"><expression property="x">{clock}</expression></shape>'
    attrs = {'speed': 'speed="2"', 'reverse': 'speed="2" reverse="true"',
             'remap': '', 'loop': 'speed="2" loop="3"'}[warp]
    remap = '<timeRemap><key time="0" value="0"/><key time="1" value="2"/></timeRemap>' if warp == 'remap' else ''
    symbols = '<symbols><symbol id="s" width="128" height="96" duration="2">'+content+'</symbol></symbols>'
    r = scene(tmp_path, source.format(clock='time')+f'<instance id="i" symbol="s" start="1" {attrs}>{remap}</instance>', symbols=symbols)
    for cycle in ([1, 0, 1] if warp == 'loop' else [0]):
        inverse = f'{1+cycle}+'+('(2-time)/2' if warp == 'reverse' else 'time/2')
        control = scene(tmp_path, source.format(clock=inverse)+content, name='control')
        for t in (.8, .2, .6, .8):
            comp = 1+cycle+((2-t)/2 if warp == 'reverse' else t/2)
            actual = live(r, comp, 'i/p')
            expected = live(control, t)
            np.testing.assert_allclose(actual, expected, atol=1e-7)
            np.testing.assert_allclose(actual, live(Renderer.open(r.doc.path, strict=True), comp, 'i/p'), atol=1e-7)


@pytest.mark.parametrize('scale', [.5, 1., 2.])
def test_floor_uses_symbol_canvas_and_parent_transform(tmp_path, scale):
    content = '<group id="g" y="10" scaleY="2">'+particle('x="20" y="5" speed="30" direction="90" bounce="1"', burst='0')+'</group>'
    symbols = '<symbols><symbol id="s" width="64" height="64" duration="3">'+content+'</symbol></symbols>'
    r = scene(tmp_path, '<instance id="i" symbol="s" x="15" y="7" scaleX=".8"/>', symbols=symbols, scale=scale)
    p = live(r, .9, 'i/p')[0]
    # Parent-local bottom is (64-10)/2 = 27, reached after 22/30 s.
    assert p[1] == pytest.approx(27-(.9-22/30)*30, abs=1e-8)
    assert p[3] == pytest.approx(-30)


def test_translating_parent_contributes_contact_velocity(tmp_path):
    wall = '<shape id="wall" shape="rect" x="60" y="10" width="3" height="80"><rigidBody type="static"/></shape>'
    group = '<group id="g"><expression property="x">30*time</expression>'+particle(
        'x="40" y="50" speed="0" bounce="1"', burst='0')+'</group>'
    r = scene(tmp_path, wall+group)
    p = live(r, .8)[0]
    assert p[2] == pytest.approx(-60, abs=1e-7)
    assert p[0]+30*.8 < 60


@pytest.mark.parametrize('scale', [.5, 1., 2.])
def test_instance_collider_ignores_output_fit_and_keeps_overrides_separate(tmp_path, scale):
    content = '<shape id="wall" shape="rect" x="60" y="10" width="3" height="80">' \
              '<rigidBody type="static"/></shape>'+particle('x="30" y="50" speed="60" direction="0" bounce="1"', burst='0')
    symbols = '<symbols><symbol id="s" width="128" height="96" duration="3">'+content+'</symbol></symbols>'
    instances = '<instance id="a" symbol="s" x="30" rotation="25" scaleX=".5" scaleY=".7"/>' \
                '<instance id="b" symbol="s" x="60" boxWidth="50" boxHeight="30" fit="contain">' \
                '<override target="wall" property="x" value="100"/></instance>'
    r = scene(tmp_path, instances, symbols=symbols, scale=scale)
    control = scene(tmp_path, content, name='control')
    for t in (.8, .2, .8):
        np.testing.assert_allclose(live(r, t, 'a/p'), live(control, t), atol=1e-8)
        np.testing.assert_allclose(live(r, t, 'b/p')[0], [30+60*t, 50, 60, 0], atol=1e-8)


WALL = '<shape id="wall" shape="rect" x="{x}" y="10" width="4" height="80"{attrs}><rigidBody type="static"/></shape>'
FLIGHT = '<particleEmitter id="p" rate="0" lifetime="5" collide="true" x="{x}" y="50" speed="300" direction="0" bounce="1">' \
         '<burst time="0" count="1"/></particleEmitter>'


def test_adjustment_layers_are_not_part_of_a_collider(tmp_path):
    """Collision alpha is the body node's own render. An adjustment acts on the composite of the siblings
    below it, so a finishing chromatic aberration (which magnifies the alpha of every channel about the frame
    centre) must not move the contact."""
    effects = '<effects><effect id="fx" type="chromatic-aberration" amount="30"/></effects>'
    adjusted = scene(tmp_path, WALL.format(x=40, attrs='')+'<adjustment id="finish" effects="fx"/>'+FLIGHT.format(x=10), extra=effects)
    control = scene(tmp_path, WALL.format(x=40, attrs='')+FLIGHT.format(x=10), name='control')
    for t in (.2, .3, .2):
        np.testing.assert_array_equal(live(adjusted, t), live(control, t))
    assert live(control, .2)[0][2] == pytest.approx(-300)       # it did bounce off the wall


def test_a_bodys_own_effect_still_shapes_the_collider(tmp_path):
    """The body's own effects, masks and mattes are part of its render: a stroke grows its alpha by 8 px each
    side, so the particle meets it 8 px earlier (a straight flight at 300 px/s and an elastic bounce)."""
    effects = '<effects><effect id="fx" type="stroke" radius="8" color="#FFFFFFFF"/></effects>'
    stroked = scene(tmp_path, WALL.format(x=70, attrs=' effects="fx"')+FLIGHT.format(x=40), extra=effects)
    plain = scene(tmp_path, WALL.format(x=70, attrs='')+FLIGHT.format(x=40), name='plain')
    em, ctx = emitter(stroked, .1)
    assert snapshots(em, ctx.t)[stroked.doc.ids['wall']].alpha.shape[1] >= 4 + 16
    # elastic bounce at 300 px/s: the stroked wall is met 8 px earlier, so the particle is 16 px further back
    # along its return path (up to the fixed step of the simulation)
    gap = live(plain, .2)[0][0]-live(stroked, .2)[0][0]
    assert gap > 10          # the 0.5 alpha contour of an 8 px stroke lies about 6.5 px out: 2 x 6.5 = 13


def test_each_step_renders_one_snapshot_in_steady_state(tmp_path, monkeypatch):
    """A step needs the snapshot at its end and the one at its start (the previous step's end): three entries
    keep both, so after the first step every step renders one (a few step boundaries differ by float noise,
    0.1 against 0.09999999999999999, and cost an extra render). Two entries rendered every step twice."""
    from scenerender.compositor import RenderContext
    from scenerender.nodes import particle_collisions as pc
    renders, steps = [], []
    render, collide = RenderContext.render_contribution, pc.collide
    monkeypatch.setattr(RenderContext, 'render_contribution', lambda self, *a: renders.append(1) or render(self, *a))
    monkeypatch.setattr(pc, 'collide', lambda *a: steps.append(1) or collide(*a))
    r = scene(tmp_path, WALL.format(x=70, attrs='')+FLIGHT.format(x=40))
    live(r, .5)
    assert len(steps) >= 4
    assert len(steps)+1 <= len(renders) < 1.25*len(steps)
