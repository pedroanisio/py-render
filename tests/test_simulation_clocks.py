"""Simulation history uses the composition time corresponding to each local step."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.nodes.particles import particles_at
from scenerender.physics import get_sim
from scenerender.render import Renderer


def scene(tmp_path, kind, warp, *, control=False, name="scene", cycle=0):
    inverse = {'speed': '1+time/2', 'reverse': '1+(3-time)/2', 'loop': f'{1+1.5*cycle}+time/2',
               'remap': '1+(time &lt;= .25 ? 2*time : .5+(time-.25)/2)'}[warp]
    global_time = inverse if control else 'time'
    target = '<shape id="source" shape="rect" width="1" height="1" visible="false">' \
             f'<expression property="x">{global_time}</expression></shape>'
    if kind == 'particles':
        content = '<particleEmitter id="p" x="5" y="5" rate="12" lifetime="3" speed="0" size="3">' \
                  '<expression property="gravityX">10*prop("source.x")</expression></particleEmitter>'
        physics = ''
    else:
        content = '<shape id="p" shape="rect" y="5" width="5" height="5">' \
                  '<expression property="x">10*prop("source.x")</expression>' \
                  '<rigidBody linearDamping="0" angularDamping="0"/></shape>'
        physics = '<physics gravityY="0" pixelsPerMeter="10"><forceField id="f" type="directional">' \
                  f'<expression property="forceX">{global_time}</expression></forceField></physics>'
    if control:
        symbols, body = '', target + content
    else:
        symbols = '<symbols><symbol id="sym" width="64" height="32" duration="3">' + content + '</symbol></symbols>'
        mapping = {'speed': ' speed="2"', 'reverse': ' speed="2" reverse="true"',
                   'loop': ' speed="2" loop="3"', 'remap': ''}[warp]
        keys = '' if warp != 'remap' else '<timeRemap><key time="0" value="0"/><key time=".5" value=".25"/>' \
                                           '<key time="1" value="1.25"/></timeRemap>'
        body = target + f'<instance id="i" symbol="sym" start="1" end="4"{mapping}>{keys}</instance>'
    path = tmp_path / (name + '.xml')
    path.write_text('<scene version="1.1"><project width="64" height="32" fps="10" duration="4" '
                    f'linearLight="false"/>{symbols}<composition>{body}</composition>{physics}</scene>')
    return Renderer.open(str(path), strict=True)


def state(r, time, kind, instanced):
    if instanced:
        node, ctx = r.rc.ev.reference('i/p', None, Ctx(time, time))
        loc = r.rc.node_location(node, ctx)
        rc = loc.rc
    else:
        node = r.doc.ids['p']
        rc, ctx = r.rc, r.rc.enter_node(node, Ctx(time, time))
    if kind == 'particles':
        p = particles_at(rc, node, ctx)
        return np.stack([p[k] for k in ('x', 'y', 'vx', 'vy', 'age')], axis=-1)
    sim = get_sim(rc)
    b = sim.world_at(ctx.t).bodies[sim.index[node]]
    return np.r_[b.pos, b.vel]


@pytest.mark.parametrize('kind', ['particles', 'bodies'])
@pytest.mark.parametrize('warp', ['speed', 'remap', 'reverse'])
def test_history_matches_explicit_clock_control_and_is_seek_independent(tmp_path, kind, warp):
    r = scene(tmp_path, kind, warp)
    control = scene(tmp_path, kind, warp, control=True, name='control')
    inverse = {'speed': lambda t: 1+t/2, 'reverse': lambda t: 1+(3-t)/2,
               'remap': lambda t: 1+(2*t if t <= .25 else .5+(t-.25)/2)}[warp]
    for t in (.8, .2, .6, .8):
        expected = state(control, t, kind, False)
        actual = state(r, inverse(t), kind, True)
        np.testing.assert_allclose(actual, expected, atol=2e-8, rtol=2e-8)
        np.testing.assert_allclose(r.frame_linear(inverse(t)), control.frame_linear(t), atol=1/255)
        # A fresh renderer that first seeks here must reproduce the same history.
        fresh = Renderer.open(r.doc.path, strict=True)
        np.testing.assert_allclose(state(fresh, inverse(t), kind, True), expected, atol=2e-8, rtol=2e-8)


@pytest.mark.parametrize('kind', ['particles', 'bodies'])
def test_loop_histories_keep_composition_dependent_inputs_separate(tmp_path, kind):
    r = scene(tmp_path, kind, 'loop')
    for cycle, t in ((1, .8), (0, .6), (1, .2), (0, .6), (1, .8)):
        comp_t = 1 + 1.5*cycle + t/2
        control = scene(tmp_path, kind, 'loop', control=True, cycle=cycle, name='control')
        expected = state(control, t, kind, False)
        np.testing.assert_allclose(state(r, comp_t, kind, True), expected, atol=2e-8, rtol=2e-8)
        np.testing.assert_allclose(r.frame_linear(comp_t), control.frame_linear(t), atol=1/255)
