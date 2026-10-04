"""Expression resampling uses the same clock, window and frame as normal evaluation."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.render import Renderer


def scene(tmp_path, clock, children, *, effects='', name='scene'):
    node = '<shape id="moving" shape="rect" width="4" height="4" y="8" fill="#ffffff">' + children + '</shape>'
    symbols = ''
    if clock == 'group':
        body = '<group id="g" timeScale="2">' + node + '</group>'
    elif clock == 'sequence':
        body = '<sequence id="q"><shape id="intro" shape="rect" width="1" height="1" end="1" opacity="0"/>' \
               + node.replace('id="moving"', 'id="moving" end="4"') + '</sequence>'
    else:
        symbols = '<symbols><symbol id="sym" width="64" height="32" duration="3">' + node + '</symbol></symbols>'
        attrs = {'speed': 'speed="2"', 'reverse': 'speed="2" reverse="true"',
                 'loop': 'speed="2" loop="3"', 'remap': '', 'freeze': ''}[clock]
        keys = {'remap': '<timeRemap><key time="0" value="0"/><key time="1" value="2"/>'
                         '<key time="2" value="2.5"/></timeRemap>',
                'freeze': '<timeRemap><key time="0" value="0"/><key time="1" value="1.6"/>'
                          '<key time="2" value="1.6"/><key time="3" value="3"/></timeRemap>'}.get(clock, '')
        body = f'<instance id="i" symbol="sym" end="6" {attrs}>{keys}</instance>'
    body += '<shape id="source" shape="rect" width="1" height="1" visible="false">' \
            '<expression property="x">10*time</expression><expression property="y">frame</expression></shape>'
    p = tmp_path / (name + '.xml')
    p.write_text('<scene version="1.1"><project width="64" height="32" fps="10" duration="6" '
                 f'linearLight="false"/>{symbols}<composition>{body}</composition>{effects}</scene>')
    r = Renderer.open(str(p), strict=True)
    return r, 'moving' if clock in ('group', 'sequence') else 'i/moving'


@pytest.mark.parametrize('clock,t,sample', [('group', .8, .6), ('speed', .8, .6),
    ('reverse', .8, 1.), ('loop', 1.8, 1.6), ('remap', 1.7, .975),
    ('freeze', 1.5, .75), ('sequence', 1.8, 1.4)])
@pytest.mark.parametrize('shared_effect', [False, True])
def test_value_at_time_maps_local_time_to_global_property(tmp_path, clock, t, sample, shared_effect):
    prop = 'amount' if shared_effect else 'x'
    drive = f'<link property="{prop}" source="source.x"/><expression property="{prop}">valueAtTime(time-.4)</expression>'
    fx = '<effects><effect id="fx" type="exposure">' + drive + '</effect></effects>' if shared_effect else ''
    r, ref = scene(tmp_path, clock, '' if shared_effect else drive, effects=fx)
    for current in (t, t + .01, t):
        node, ctx = r.rc.ev.reference(ref, None, Ctx(current, current, frame=round(current*10)))
        if current == t:
            value = r.rc.ev.num(r.doc.ids['fx'] if shared_effect else node, prop, ctx)
            assert value == pytest.approx(10*sample, abs=2e-8)
    if not shared_effect:
        control, _ = scene(tmp_path, clock, f'<animate property="x"><key time="0" value="{10*sample}"/></animate>', name='control')
        np.testing.assert_array_equal(r.frame_rgba(t), control.frame_rgba(t))


@pytest.mark.parametrize('clock', ['group', 'speed', 'reverse', 'loop', 'remap', 'sequence'])
@pytest.mark.parametrize('delay,smoothing', [(.3, 0), (.3, .2), (-.3, .2)])
def test_link_delay_and_smoothing_resample_source_frames(tmp_path, clock, delay, smoothing):
    drive = f'<link property="x" source="source.y" delay="{delay}" smoothing="{smoothing}"/>'
    r, ref = scene(tmp_path, clock, drive)
    for t in (1.813, 1.113, 1.813):
        node, ctx = r.rc.ev.reference(ref, None, Ctx(t, t, frame=round(t*10)))
        times = np.linspace(t-delay, t-delay-smoothing, 9) if smoothing else [t-delay]
        expected = np.mean([round(time*10) for time in times])
        assert r.rc.ev.num(node, 'x', ctx) == pytest.approx(expected)


def test_value_at_time_recomputes_normalized_window(tmp_path):
    p = tmp_path / 'window.xml'
    p.write_text('''<scene version="1.1"><project width="64" height="32" fps="10" duration="3"/>
      <composition><shape id="s" shape="rect" width="4" height="4" end="1">
      <animate property="end"><key time="0" value="1"/><key time="2" value="3"/></animate>
      <animate property="x" timeBase="normalized"><key time="0" value="0"/><key time="1" value="100"/></animate>
      <expression property="x">valueAtTime(.25)</expression></shape></composition></scene>''')
    r = Renderer.open(str(p), strict=True)
    for t in (.5, 1., .5):
        node, ctx = r.rc.ev.reference('s', None, Ctx(t, t))
        assert r.rc.ev.num(node, 'x', ctx) == pytest.approx(20.)  # 100 * .25 / 1.25.


@pytest.mark.parametrize('base,t,kind,expected', [('local', 4.5, 'loopOut', 20),
    ('normalized', 4.5, 'loopOut', 15), ('local', 1.5, 'loopIn', 20),
    ('normalized', 1.5, 'loopIn', 25)])
def test_loop_functions_use_animation_time_base_and_vector_component(tmp_path, base, t, kind, expected):
    p = tmp_path / 'loop.xml'
    p.write_text(f'''<scene version="1.1"><project width="64" height="32" fps="10" duration="6"/>
      <composition><shape id="s" shape="rect" width="4" height="4" start="2" end="4">
      <animate property="position" timeBase="{base}"><key time="0" value="0,10"/><key time="1" value="10,30"/></animate>
      <expression property="y">{kind}("cycle")</expression></shape></composition></scene>''')
    r = Renderer.open(str(p), strict=True)
    node, ctx = r.rc.ev.reference('s', None, Ctx(t, t))
    assert r.rc.ev.num(node, 'y', ctx) == pytest.approx(expected)


@pytest.mark.parametrize('sequence', [False, True])
def test_value_at_time_on_window_property_does_not_reenter_itself(tmp_path, sequence):
    p = tmp_path / 'timing-expression.xml'
    body = '''<shape id="s" shape="rect" width="4" height="4" end="1">
      <animate property="end"><key time="0" value="1"/><key time="1" value="2"/></animate>
      <expression property="end">valueAtTime(.25)</expression></shape>'''
    if sequence:
        body = '<sequence id="q">' + body + '<shape id="next" shape="rect" width="4" height="4" end="1"/></sequence>'
    p.write_text('<scene version="1.1"><project width="64" height="32" fps="10" duration="3"/>'
                 f'<composition>{body}</composition></scene>')
    r = Renderer.open(str(p), strict=True)
    for t in (.5, 1., .5):
        node, ctx = r.rc.ev.reference('s', None, Ctx(t, t))
        assert ctx.node_end == pytest.approx(1.25)
        assert r.rc.ev.num(node, 'end', ctx) == pytest.approx(1.25)
        if sequence:
            assert r.rc.ev.window(r.doc.ids['next'], Ctx(t, t))[0] == pytest.approx(1.25)
