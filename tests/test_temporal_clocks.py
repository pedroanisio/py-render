"""Temporal samples agree with independent complete renders of warped scenes."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx, Scope
from scenerender.render import Renderer


def scene(tmp_path, kind, *, effects="", node_attrs="", project="", name="scene"):
    shape = f'''<shape id="moving" shape="rect" width="4" height="5" y="8" fill="#ffffff" {node_attrs}>
      <expression property="x">8*time+prop("global.x")</expression></shape>'''
    before = ""
    scope = Scope()
    if kind == "group":
        body = '<group id="g" timeScale="2"><expression property="x">3*time</expression>' + shape + '</group>'
    elif kind == "sequence":
        body = '<sequence id="q"><shape id="intro" shape="rect" width="1" height="1" end="1" opacity="0"/>' \
               + shape.replace('id="moving"', 'id="moving" end="4"') + '</sequence>'
    else:
        before += f'<symbols><symbol id="sym" width="64" height="32" duration="3">{shape}</symbol></symbols>'
        attrs = {"speed": 'speed="2"', "reverse": 'speed="2" reverse="true"', "loop": 'speed="2" loop="3"',
                 "remap": '', "freeze": ''}[kind]
        remap = {'remap': '<timeRemap><key time="0" value="0"/><key time="1" value="2"/>'
                          '<key time="2" value="2.5"/></timeRemap>',
                 'freeze': '<timeRemap><key time="0" value="0"/><key time="1" value="1.6"/>'
                           '<key time="2" value="1.6"/><key time="3" value="3"/></timeRemap>'}.get(kind, '')
        body = f'<instance id="i" symbol="sym" end="6" {attrs}>{remap}</instance>'
        scope = Scope(path=("i",))
    body += '<shape id="global" shape="rect" width="1" height="1" visible="false">' \
            '<expression property="x">2*time</expression></shape>'
    path = tmp_path / (name + '.xml')
    path.write_text(f'<scene version="1.1"><project width="64" height="32" fps="10" duration="6" '
                    f'linearLight="false" {project}/>{before}<composition>{body}</composition>{effects}</scene>')
    return Renderer.open(str(path), strict=True), scope


@pytest.mark.parametrize("kind", ["group", "speed", "reverse", "loop", "remap", "freeze", "sequence"])
def test_render_node_at_replays_ancestor_clocks(tmp_path, kind):
    r, scope = scene(tmp_path, kind)
    ctx = Ctx(2.7, 2.7, scope=scope)
    node = r.doc.ids['moving']
    for t in (1.2, 1.8, 1.1, 1.2):
        sampled = r.rc.render_node_at(node, t, ctx).buf.region((0, 0, 64, 32))
        np.testing.assert_allclose(sampled, r.frame_linear(t), atol=1e-7)


@pytest.mark.parametrize("kind", ["group", "speed", "reverse", "loop", "remap", "sequence"])
def test_echo_samples_composition_seconds_through_warp(tmp_path, kind):
    fx = '<effects><effect id="fx" type="echo" samples="2"><param name="interval" value=".5"/>' \
         '<param name="decay" value=".5"/></effect></effects>'
    r, _ = scene(tmp_path, kind, effects=fx, node_attrs='effects="fx"')
    control, _ = scene(tmp_path, kind, name="control")
    for t in (1.8, 1.1, 1.8):
        current, past = control.frame_linear(t), control.frame_linear(t - .5) * .5
        expected = current + past * (1 - current[..., 3:])
        np.testing.assert_allclose(r.frame_linear(t), expected, atol=1e-7)


@pytest.mark.parametrize("kind,t,held_comp", [("group", .8, .75), ("speed", .8, .75),
    ("reverse", .8, 1.), ("loop", 1.8, 1.75), ("remap", 1.7, 1.),
    ("freeze", 1.5, .9375), ("sequence", 1.8, 1.5)])
def test_posterize_inverts_local_clock_and_global_references(tmp_path, kind, t, held_comp):
    fx = '<effects><effect id="fx" type="posterize-time" frequency="2"/></effects>'
    r, _ = scene(tmp_path, kind, effects=fx, node_attrs='effects="fx"')
    control, _ = scene(tmp_path, kind, name="control")
    for current in (t, t - .01, t):
        np.testing.assert_allclose(r.frame_linear(current), control.frame_linear(held_comp), atol=1e-7)


@pytest.mark.parametrize("kind", ["group", "speed", "reverse", "loop", "remap", "sequence"])
def test_node_motion_blur_matches_full_scene_shutter(tmp_path, kind):
    settings = 'motionBlurSamples="8" shutterAngle="360"'
    local, _ = scene(tmp_path, kind, node_attrs='motionBlur="on"', project=settings)
    global_, _ = scene(tmp_path, kind, project=settings + ' motionBlur="true"', name="global")
    for t in (1.8, 1.1):
        np.testing.assert_allclose(local.frame_linear(t), global_.frame_linear(t), atol=1e-7)


@pytest.mark.parametrize("kind", ["group", "speed", "reverse", "loop", "remap", "sequence"])
def test_node_excluded_from_shutter_holds_ancestor_transforms(tmp_path, kind):
    r, _ = scene(tmp_path, kind, node_attrs='motionBlur="off"',
                 project='motionBlur="true" motionBlurSamples="8" shutterAngle="360"')
    control, _ = scene(tmp_path, kind, name="control")
    np.testing.assert_allclose(r.frame_linear(1.8), control.frame_linear(1.8), atol=1e-7)


@pytest.mark.parametrize("scale", [.5, 2.])
def test_echo_tail_window_uses_composition_seconds(tmp_path, scale):
    fx = '<effects><effect id="fx" type="echo" samples="2"><param name="interval" value=".5"/>' \
         '<param name="decay" value=".5"/></effect></effects>'
    r, _ = scene(tmp_path, 'group', effects=fx, node_attrs='end="1" effects="fx"')
    control, _ = scene(tmp_path, 'group', node_attrs='end="1"', name='control')
    r.doc.ids['g'].set('timeScale', str(scale))
    control.doc.ids['g'].set('timeScale', str(scale))
    t = 1 / scale + .4
    np.testing.assert_allclose(r.frame_linear(t), control.frame_linear(t - .5) * .5, atol=1e-7)
    assert r.frame_linear(t)[..., 3].any()
    assert not r.frame_linear(1 / scale + .6)[..., 3].any()
