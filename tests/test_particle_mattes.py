"""Collider mattes retain independent particle histories and reject feedback cycles."""
import numpy as np
import pytest

from scenerender.document import SceneError
from scenerender.evaluator import Ctx
from scenerender.nodes.particles import get_emitter, particles_at
from scenerender.nodes.particle_collisions import snapshots
from scenerender.render import Renderer


def scene(tmp_path, content, *, name='scene', instanced=False, assets=''):
    path = tmp_path/(name+'.xml')
    symbols = ''
    if instanced:
        symbols = '<symbols><symbol id="s" width="160" height="120" duration="4">'+content+'</symbol></symbols>'
        content = '<instance id="i" symbol="s" speed="2" x="5" scaleX=".8"/>'
    path.write_text('<scene version="1.1"><project width="160" height="120" duration="4" fps="10" '
                    'linearLight="false"/>'+assets+symbols+'<composition>'+content+'</composition>'
                    '<physics gravityY="0"/></scene>')
    return Renderer.open(str(path), strict=True)


def emitter(r, t, ref='p'):
    node, ctx = r.rc.ev.reference(ref, None, Ctx(t, t))
    loc = r.rc.node_location(node, ctx)
    return get_emitter(loc.rc, node, ctx), ctx


def state(r, t, ref='p'):
    em, ctx = emitter(r, t, ref)
    p = particles_at(em.rc, em.el, ctx)
    return np.stack([p[k] for k in ('x', 'y', 'vx', 'vy')], axis=-1)


def mover(attrs='', children=''):
    return f'<particleEmitter id="p" x="30" y="50" speed="60" direction="0" rate="0" lifetime="4" bounce="1" {attrs}>' \
           '<burst time="0" count="1"/>'+children+'</particleEmitter>'


def wall(mode='alpha', kind='shape'):
    attrs = f'id="wall" x="55" y="20" matte="matte" matteMode="{mode}"'
    if kind == 'layer':
        return f'<layer {attrs} asset="source"><rigidBody type="static"/></layer>'
    return f'<shape {attrs} shape="rect" width="10" height="60"><rigidBody type="static"/></shape>'


@pytest.mark.parametrize('grouped', [False, True])
@pytest.mark.parametrize('mode', ['alpha', 'alpha-inverted', 'luma', 'luma-inverted'])
@pytest.mark.parametrize('speed', [0, 10])
def test_particle_matte_collisions_match_analytic_shape_matte(tmp_path, grouped, mode, speed):
    attrs = f'id="matte" x="60" y="50" speed="{speed}" direction="0" rate="0" lifetime="4" size="20" shape="square" color="#80ff80"'
    mask = f'<particleEmitter {attrs}><burst time="0" count="1"/></particleEmitter>'
    control_mask = '<shape id="matte" shape="rect" x="50" y="40" width="20" height="20" fill="#80ff80">' \
                   f'<expression property="x">50+{speed}*time</expression></shape>'
    if grouped:
        mask = '<group id="matte">'+mask.replace('id="matte"', 'id="maskparticle"')+'</group>'
        control_mask = '<group id="matte">'+control_mask.replace('id="matte"', 'id="maskshape"')+'</group>'
    body = wall(mode)+mover('collide="true"')
    r, control = scene(tmp_path, mask+body), scene(tmp_path, control_mask+body, name='control')
    for t in (.8, .2, .6, .8):
        np.testing.assert_allclose(state(r, t), state(control, t), atol=1e-8)
        np.testing.assert_allclose(r.frame_linear(t), control.frame_linear(t), atol=1/255)
        fresh = Renderer.open(r.doc.path, strict=True)
        np.testing.assert_array_equal(state(r, t), state(fresh, t))
    if speed == 0:
        assert state(r, .8)[0, 2] == pytest.approx(60 if mode.endswith('inverted') else -60)


@pytest.mark.parametrize('kind', ['shape', 'layer'])
@pytest.mark.parametrize('instanced', [False, True])
def test_particle_matte_animation_matches_current_rendered_alpha(tmp_path, kind, instanced):
    mask = '<group id="matte"><particleEmitter id="maskparticle" x="60" y="50" speed="0" rate="0" lifetime="4" size="30" shape="square">' \
           '<burst time="0" count="1"/><expression property="opacityEnd">time &lt; .5 ? 1 : .2</expression>' \
           '</particleEmitter></group>'
    assets = '<assets><generator id="source" kind="solid" width="10" height="60" paint="#ffffffff"/></assets>' if kind == 'layer' else ''
    r = scene(tmp_path, mask+wall(kind=kind)+mover('collide="true"', '<expression property="rate">0</expression>'),
              instanced=instanced, assets=assets)
    ref = 'i/p' if instanced else 'p'
    xx, yy = np.meshgrid(np.arange(160)+.5, np.arange(120)+.5)
    for local in (.8, .2, .6, .8):
        t = local/2 if instanced else local
        em, ctx = emitter(r, t, ref)
        col = snapshots(em, ctx.t)[r.doc.ids['wall']]
        # Render only the wall on the untransformed symbol/composition canvas.
        # A separate native control uses the analytically known lifetime alpha.
        alpha = 1 if local < .5 else 1-.8*local/4
        control_mask = f'<shape id="matte" shape="rect" x="45" y="35" width="30" height="30" opacity="{alpha}"/>'
        control = scene(tmp_path, control_mask+wall(kind=kind), assets=assets, name='control')
        np.testing.assert_allclose(col.sample(xx, yy), control.frame_linear(local)[..., 3], atol=1/255)
        assert len(em.colliders) <= 2


@pytest.mark.parametrize('grouped', [False, True])
@pytest.mark.parametrize('instanced', [False, True])
def test_cyclic_particle_matte_is_reported_and_does_not_corrupt_earlier_history(tmp_path, grouped, instanced):
    p = mover(children='<expression property="collide">time &gt;= .2</expression>')
    body = wall().replace('matte="matte"', 'matte="matte"' if grouped else 'matte="p"')
    content = body+('<group id="matte">'+p+'</group>' if grouped else p)
    r = scene(tmp_path, content, instanced=instanced)
    factor = 2 if instanced else 1
    ref = 'i/p' if instanced else 'p'
    expected = state(r, .1/factor, ref).copy()
    for _ in range(2):
        with pytest.raises(SceneError, match='Cyclic particle collision/matte dependency'):
            state(r, .4/factor, ref)
        np.testing.assert_array_equal(state(r, .1/factor, ref), expected)
        em, _ = emitter(r, .1/factor, ref)
        assert not em._querying


@pytest.mark.parametrize('speed', [.5, 1, 2])
def test_matte_particle_can_collide_in_an_independent_symbol_world(tmp_path, speed):
    # The source particle settles on its own symbol floor; its collider never
    # depends on the consuming composition, so this dependency is acyclic.
    source = '<shape id="floor" shape="rect" y="60" width="160" height="10"><rigidBody type="static"/></shape>' \
             '<particleEmitter id="q" x="60" y="50" speed="40" direction="90" rate="0" lifetime="4" size="20" shape="square" collide="true" bounce="0">' \
             '<burst time="0" count="1"/></particleEmitter>'
    path = tmp_path/'independent.xml'
    main_wall = wall().replace(' matte="matte"', '').replace('<rigidBody', '<expression property="matte">"i/q"</expression><rigidBody')
    path.write_text('<scene version="1.1"><project width="160" height="120" duration="4" fps="10" linearLight="false"/>'
                    '<symbols><symbol id="s" width="160" height="120" duration="4">'+source+'</symbol></symbols>'
                    f'<composition><instance id="i" symbol="s" speed="{speed}"/>'+main_wall+mover('collide="true"')+
                    '</composition><physics gravityY="0"/></scene>')
    r = Renderer.open(str(path), strict=True)
    moving_mask = '<shape id="matte" shape="rect" x="50" width="20" height="20">' \
                  f'<expression property="y">time*{speed} &lt; .25 ? 40+40*time*{speed} : 49.75</expression></shape>'
    motion_control = scene(tmp_path, moving_mask+wall()+mover('collide="true"'), name='motion-control')
    xx, yy = np.meshgrid(np.arange(160)+.5, np.arange(120)+.5)
    for t in (.8, .2, .6, .8):
        local = t*speed
        centre = 50+40*local if local < .25 else 59.75
        mask = f'<shape id="matte" shape="rect" x="50" y="{centre-10}" width="20" height="20"/>'
        control = scene(tmp_path, mask+wall(), name='control')
        em, ctx = emitter(r, t)
        col = snapshots(em, ctx.t)[r.doc.ids['wall']]
        np.testing.assert_allclose(col.sample(xx, yy), control.frame_linear(t)[..., 3], atol=1/255)
        np.testing.assert_allclose(state(r, t, 'i/q')[0, 1], centre, atol=1e-7)
        fresh = Renderer.open(r.doc.path, strict=True)
        np.testing.assert_allclose(state(r, t), state(fresh, t), atol=1e-7)
        np.testing.assert_allclose(state(r, t), state(motion_control, t), atol=1e-7)
        assert len(em.states) <= 2
    assert state(r, .8)[0, 2] < 0  # The clipped matte corner can also deflect vertically.


def test_cross_world_feedback_reports_cycle_and_resets_both_histories(tmp_path):
    source = '<shape id="floor" shape="rect" y="60" width="160" height="10">' \
             '<expression property="matte">"p"</expression><rigidBody type="static"/></shape>' \
             '<particleEmitter id="q" x="60" y="50" speed="0" rate="0" lifetime="4" size="20" shape="square">' \
             '<expression property="collide">time &gt;= .2</expression><burst time="0" count="1"/></particleEmitter>'
    path = tmp_path/'feedback.xml'
    main_wall = wall().replace(' matte="matte"', '').replace('<rigidBody', '<expression property="matte">"i/q"</expression><rigidBody')
    path.write_text('<scene version="1.1"><project width="160" height="120" duration="4" fps="10" linearLight="false"/>'
                    '<symbols><symbol id="s" width="160" height="120" duration="4">'+source+'</symbol></symbols>'
                    '<composition><instance id="i" symbol="s"/>'+main_wall+
                    mover(children='<expression property="collide">time &gt;= .2</expression>')+
                    '</composition><physics gravityY="0"/></scene>')
    r = Renderer.open(str(path), strict=True)
    expected = {ref: state(r, .1, ref).copy() for ref in ('p', 'i/q')}
    for ref in ('p', 'i/q', 'p'):
        with pytest.raises(SceneError, match='Cyclic particle collision/matte dependency'):
            state(r, .4, ref)
        for key in expected:
            np.testing.assert_array_equal(state(r, .1, key), expected[key])


def test_repeat_bindings_do_not_duplicate_histories_or_hide_feedback_cycles(tmp_path):
    p = mover(children='<expression property="collide">time &gt;= .2</expression>')
    r = scene(tmp_path, '<repeat id="copies" count="2" offsetX="40">'+wall()+'<group id="matte">'+p+'</group></repeat>')
    for ref in ('p#0', 'p#1', 'p#0'):
        expected = state(r, .1, ref).copy()
        with pytest.raises(SceneError, match='Cyclic particle collision/matte dependency'):
            state(r, .4, ref)
        np.testing.assert_array_equal(state(r, .1, ref), expected)
    histories = [value for key, value in r.rc.cache.items() if isinstance(key, tuple) and key[0] == 'particles']
    assert len(histories) == 2
