"""Evaluated sequence windows must agree with explicitly authored timelines."""
import numpy as np
import pytest
from PIL import Image

from scenerender.evaluator import Ctx
from scenerender.render import Renderer


def scene(tmp_path, body, *, before="", name="scene", duration=6):
    p = tmp_path / (name + ".xml")
    p.write_text(f'<scene version="1.1"><project width="64" height="32" fps="10" duration="{duration}" '
                 f'linearLight="false"/>{before}<composition>{body}</composition></scene>')
    return Renderer.open(str(p), strict=True)


def shape(id, color, attrs="", children=""):
    color = {"red": "#ff0000", "blue": "#0000ff", "green": "#008000", "white": "#ffffff"}.get(color, color)
    return f'<shape id="{id}" shape="rect" width="10" height="10" fill="{color}" {attrs}>{children}</shape>'


def animation(prop, value):
    return f'<animate property="{prop}"><key time="0" value="{value}"/></animate>'


def symbol(content):
    return f'<symbols><symbol id="sym" width="64" height="32" duration="6">{content}</symbol></symbols>'


@pytest.mark.parametrize("target,prop,base,value", [("q", "timeGap", "0", ".5"),
    ("q", "timeGap", "0", "-.25"), ("a", "end", "1", "2"), ("b", "start", "0", ".5"),
    ("q", "start", "0", ".75"), ("q", "timeScale", "1", "2"), ("q", "timeOffset", "0", ".5")])
def test_instance_schedule_overrides_match_static_and_survive_seeking(tmp_path, target, prop, base, value):
    def content(v):
        return '<sequence id="q" ' + (f'{prop}="{v}"' if target == "q" else '') + '>' \
            + shape("a", "#ff0000", f'end="{v if target == "a" else 1}"') \
            + shape("b", "#0000ff", f'end="1" start="{v if target == "b" else 0}"',
                    '<expression property="x">20*time</expression>') + '</sequence>'
    a = scene(tmp_path, f'<instance id="i" symbol="sym"><override target="{target}" property="{prop}" '
              f'value="{value}"/></instance>', before=symbol(content(base)))
    b = scene(tmp_path, '<instance id="i" symbol="sym"/>', before=symbol(content(value)), name="static")
    for t in (1.25, .25, 2.75, 1.6, .8, 1.25):
        np.testing.assert_array_equal(a.frame_rgba(t), b.frame_rgba(t), err_msg=f"t={t}")


def test_gap_override_changes_actual_window_and_child_clock(tmp_path):
    content = '<sequence id="q">' + shape("a", "red", 'end="1"') \
              + shape("b", "blue", 'end="1"', '<expression property="x">20*time</expression>') + '</sequence>'
    r = scene(tmp_path, '<instance id="i" symbol="sym"><override target="q" property="timeGap" value=".5"/></instance>',
              before=symbol(content))
    assert not r.frame_rgba(1.25)[..., 3].any()
    px = r.frame_rgba(1.75)
    np.testing.assert_array_equal(px[5, 6], [0, 0, 255, 255])
    assert px[5, 25, 3] == 0
    assert r.frame_rgba(2.25)[..., 3].any()  # The sequence's own inferred end must also move.
    assert not r.frame_rgba(2.6)[..., 3].any()


def test_animated_gap_uses_sequence_clock_before_time_scale(tmp_path):
    content = shape("a", "red", 'end="1"') + shape("b", "blue", 'end="1"')
    r = scene(tmp_path, '<sequence id="q" timeScale="2">' + animation("timeGap", ".5") + content + '</sequence>')
    for t in (.6, .7):
        assert not r.frame_rgba(t)[..., 3].any()
    assert r.frame_rgba(.8)[..., 3].any()
    driven = '<animate property="timeGap"><key time="0" value="0"/><key time="1" value="1"/></animate>'
    r = scene(tmp_path, '<sequence id="q" timeScale="2">' + driven + content + '</sequence>')
    # At .75, the content clock is 1.5 but the sequence's own gap is .75:
    # the second child starts at content time 1.75 and has not appeared yet.
    assert not r.frame_rgba(.75)[..., 3].any()


@pytest.mark.parametrize("prop,value,duration", [("speed", "2", .5), ("timeStretch", "2", 2),
    ("clipIn", ".25", .75), ("clipOut", ".5", .5), ("loop", "2", 3)])
def test_evaluated_media_duration_reschedules_next_child(tmp_path, prop, value, duration):
    Image.new("RGB", (10, 10), "red").save(tmp_path / "frame0.png")
    assets = '<assets><imageSequence id="frames" src="frame%d.png" first="0" last="0" fps="1" width="10" height="10"/></assets>'
    r = scene(tmp_path, '<sequence id="q"><layer id="a" asset="frames">' + animation(prop, value) \
              + '</layer>' + shape("b", "blue", 'end="1"') + '</sequence>', before=assets)
    np.testing.assert_array_equal(r.frame_rgba(duration - .05)[5, 5], [255, 0, 0, 255])
    np.testing.assert_array_equal(r.frame_rgba(duration + .05)[5, 5], [0, 0, 255, 255])
    assert r.rc.ev.window(r.doc.ids["b"], Ctx(duration + .05, duration + .05)) == pytest.approx((duration, duration + 1))


def test_nested_sequences_include_offsets_and_own_time_scale(tmp_path):
    body = '<sequence id="outer" start=".5" timeGap=".25">' + shape("a", "red", 'end="1"') \
           + '<sequence id="inner" start=".25" timeScale="2" timeOffset=".25">' \
           + shape("b", "blue", 'end="1"') + shape("c", "green", 'end="1"') + '</sequence>' \
           + shape("d", "white", 'end="1"') + '</sequence>'
    r = scene(tmp_path, body)
    # a [.5,1.5), inner [2,3.25), d [3.5,4.5); inner has .25 seconds of delay.
    for t, expected in [(.75, [255, 0, 0, 255]), (1.75, [0, 0, 0, 0]), (2.1, [0, 0, 0, 0]),
                        (2.5, [0, 0, 255, 255]), (3., [0, 128, 0, 255]), (3.4, [0, 0, 0, 0]),
                        (4., [255, 255, 255, 255])]:
        np.testing.assert_array_equal(r.frame_rgba(t)[5, 5], expected, err_msg=str(t))


def test_two_instances_keep_different_schedules(tmp_path):
    content = '<sequence id="q">' + shape("a", "red", 'end="1"') + shape("b", "blue", 'end="1"') + '</sequence>'
    body = '<instance id="a1" symbol="sym"/><instance id="b1" symbol="sym" x="20">' \
           '<override target="q" property="timeGap" value=".5"/></instance>'
    r = scene(tmp_path, body, before=symbol(content))
    for t in (1.25, 2.25, 1.25):
        px = r.frame_rgba(t)
        assert bool(px[5, 5, 3]) == (t < 2)
        assert bool(px[5, 25, 3]) == (t > 2)


@pytest.mark.parametrize("type", ["crossfade", "additive-dissolve", "wipe"])
def test_instance_can_introduce_sequence_transition_and_duration(tmp_path, type):
    children = shape("a", "red", 'end="1"') + shape("b", "blue", 'end="1"')
    content = '<sequence id="q" timeGap=".5">' + children + '</sequence>'
    body = f'<instance id="i" symbol="sym"><override target="q" property="transition" value="{type}"/>' \
           '<override target="q" property="transitionDuration" value=".8"/></instance>'
    a = scene(tmp_path, body, before=symbol(content))
    control = f'<sequence id="q" timeGap=".5" transition="{type}" transitionDuration=".8">{children}</sequence>'
    b = scene(tmp_path, '<instance id="i" symbol="sym"/>', before=symbol(control), name="control")
    for t in (1.2, 1.4, 1.5, 1.75, 1.2):
        np.testing.assert_array_equal(a.frame_rgba(t), b.frame_rgba(t))
    assert a.frame_rgba(1.4)[..., 3].any()


@pytest.mark.parametrize("prop,base,value", [("duration", ".2", ".8"), ("alignment", "center", "end"),
    ("curve", "linear", "ease-in"), ("type", "crossfade", "additive-dissolve"), ("from", "a", "c"),
    ("to", "b", "c")])
def test_transition_choices_and_windows_use_evaluation(tmp_path, prop, base, value):
    def body(driven):
        attrs = dict(type="crossfade", duration=".8", alignment="center", curve="linear", **{"from": "a", "to": "b"})
        attrs[prop] = base if driven else value
        return shape("a", "red", 'end="1"') + shape("b", "blue", 'start="1" end="2"') \
               + shape("c", "green", 'start="1" end="2" x="20"') \
               + '<transition id="tr" ' + ' '.join(f'{k}="{v}"' for k, v in attrs.items()) + '>' \
               + (animation(prop, value) if driven else '') + '</transition>'
    a, b = scene(tmp_path, body(True)), scene(tmp_path, body(False), name="control")
    for t in (.7, .9, 1.1):
        np.testing.assert_array_equal(a.frame_rgba(t), b.frame_rgba(t))


def test_explicit_transition_replaces_evaluated_sequence_default(tmp_path):
    content = '<sequence id="q">' + shape("a", "red", 'end="1"') + shape("b", "blue", 'end="1"') \
              + '<transition type="cut" from="a" to="b"/></sequence>'
    r = scene(tmp_path, '<instance id="i" symbol="sym"><override target="q" property="transition" value="crossfade"/>'
              '<override target="q" property="transitionDuration" value="1"/></instance>', before=symbol(content))
    np.testing.assert_array_equal(r.frame_rgba(.9)[5, 5], [255, 0, 0, 255])
    np.testing.assert_array_equal(r.frame_rgba(1.1)[5, 5], [0, 0, 255, 255])


@pytest.mark.parametrize("scope", ["composition", "instance", "nested"])
def test_video_audio_follows_evaluated_sequence_gaps_and_local_playback(tmp_path, scope):
    from scenerender.audio import mixer_for
    assets = '<assets><video id="v" src="unused.mp4" width="10" height="10" fps="10" duration="1" hasAudio="true"/></assets>'
    # Decode is replaced by deterministic PCM, while the real mixer evaluates all
    # clocks, scheduled windows, gain and source addressing.
    layer = '<layer id="b" asset="v"/>'
    if scope == "nested":
        layer = '<group id="g">' + layer + '</group>'
    q = '<sequence id="q">' + animation("timeGap", ".5") + shape("a", "red", 'end="1"') + layer + '</sequence>'
    r = scene(tmp_path, q if scope == "composition" else '<instance id="i" symbol="sym" start=".5" speed="2"/>',
              before=assets + (symbol(q) if scope != "composition" else ''), duration=3)
    m = mixer_for(r, sample_rate=8000)
    src = np.concatenate([np.full((4000, 1), .2, np.float32), np.full((4000, 1), .4, np.float32)])
    m.decode_asset = lambda asset: src
    pcm = m.render()
    start = 1.5 if scope == "composition" else 1.25
    speed = 1 if scope == "composition" else 2
    assert not np.any(pcm[int((start - .2) * m.sr):int((start - .01) * m.sr)])
    first = np.mean(pcm[int((start + .1/speed) * m.sr):int((start + .3/speed) * m.sr)])
    second = np.mean(pcm[int((start + .6/speed) * m.sr):int((start + .8/speed) * m.sr)])
    assert first > .05 and second == pytest.approx(2 * first, rel=.02)
    assert not np.any(pcm[int((start + 1.05/speed) * m.sr):])


def test_sequence_without_transition_does_not_fade_video_audio(tmp_path):
    from scenerender.audio import mixer_for
    assets = '<assets><video id="v" src="unused.mp4" width="10" height="10" fps="10" duration="1" hasAudio="true"/></assets>'
    q = '<sequence id="q"><layer id="a" asset="v"/><layer id="b" asset="v"/></sequence>'
    r = scene(tmp_path, q, before=assets, duration=2)
    m = mixer_for(r, sample_rate=8000)
    m.decode_asset = lambda asset: np.full((8000, 1), .25, np.float32)
    pcm = m.render()
    np.testing.assert_allclose(pcm[8500:10000], pcm[1000:2500], atol=1e-7)


def test_effective_reference_follows_rescheduled_child_clock(tmp_path):
    content = '<sequence id="q">' + shape("a", "red", 'end="1"') \
              + shape("b", "blue", 'end="1"', '<expression property="x">20*time</expression>') + '</sequence>'
    body = '<instance id="i" symbol="sym"><override target="q" property="timeGap" value=".5"/></instance>' \
           + shape("follower", "white", 'y="15"', '<expression property="x">prop("i/b.x")</expression>')
    r = scene(tmp_path, body, before=symbol(content))
    for t in (1.75, 2.25, 1.75):
        px = r.frame_rgba(t)
        x = int(20 * (t - 1.5))
        assert px[5, x + 2, 2] == 255 and px[20, x + 2, 0] == 255
        assert px[5, x + 11, 3] == 0 and px[20, x + 11, 3] == 0


def test_time_remap_duration_moves_next_child_and_preserves_source_time(tmp_path):
    Image.new("RGB", (10, 10), "red").save(tmp_path / "frame0.png")
    Image.new("RGB", (10, 10), "blue").save(tmp_path / "frame1.png")
    assets = '<assets><imageSequence id="frames" src="frame%d.png" first="0" last="1" fps="2" width="10" height="10"/></assets>'
    body = '<sequence id="q">' + shape("intro", "white", 'end="1"') \
           + '<layer id="clip" asset="frames"><timeRemap><key time="0" value=".75"/>' \
           '<key time="2" value=".75"/></timeRemap></layer>' + shape("end", "red", 'end="1"') + '</sequence>'
    r = scene(tmp_path, body, before=assets)
    for t in (1.1, 2.9):
        np.testing.assert_array_equal(r.frame_rgba(t)[5, 5], [0, 0, 255, 255])
    np.testing.assert_array_equal(r.frame_rgba(3.1)[5, 5], [255, 0, 0, 255])


def normalized(prop, first, last):
    return f'<animate property="{prop}" timeBase="normalized"><key time="0" value="{first}"/>' \
           f'<key time="1" value="{last}"/></animate>'


@pytest.mark.parametrize("prop,first,last,expected", [
    ("speed", 1, 2, lambda t: max(.5, 1-t)),
    ("speed", 2, 1, lambda t: min(1., (1+t)/2)),
    ("timeStretch", 1, 2, lambda t: (1+np.sqrt(1+4*t))/2),
    ("clipOut", .5, 1, lambda t: min(1., (.5+np.sqrt(.25+2*t))/2)),
])
def test_normalized_media_duration_and_rendered_value_agree(tmp_path, prop, first, last, expected):
    Image.new("RGB", (10, 10), "red").save(tmp_path / "frame0.png")
    assets = '<assets><imageSequence id="frames" src="frame%d.png" first="0" last="0" fps="1" width="10" height="10"/></assets>'
    q = '<sequence id="q"><layer id="a" asset="frames">' + normalized(prop, first, last) \
        + '</layer>' + shape("b", "blue", 'end="1"') + '</sequence>'
    r = scene(tmp_path, q, before=assets)
    for t in (.1, .8, .25, .4, .1):
        ctx = r.rc.enter_node(r.doc.ids["a"], Ctx(t, t))
        d = ctx.node_end - ctx.node_start
        assert d == pytest.approx(expected(t), abs=2e-9)
        speed = r.rc.ev.num(r.doc.ids["a"], "speed", ctx, 1)
        stretch = r.rc.ev.num(r.doc.ids["a"], "timeStretch", ctx, 1)
        span = r.rc.ev.num(r.doc.ids["a"], "clipOut", ctx, 1)
        assert d * speed / stretch == pytest.approx(span, abs=2e-9)
        pixel = r.frame_rgba(t)[5, 5]
        np.testing.assert_array_equal(pixel, [255, 0, 0, 255] if t < d else [0, 0, 255, 255])


def test_normalized_sequence_gap_uses_resolved_sequence_window(tmp_path):
    q = '<sequence id="q">' + normalized("timeGap", 0, 1) + shape("a", "red", 'end="1"') \
        + shape("b", "blue", 'end="1"') + '</sequence>'
    r = scene(tmp_path, q)
    for t in (1., 1.7, .5, 1.5):
        parent = r.rc.enter_node(r.doc.ids["q"], Ctx(t, t))
        d = 1 + np.sqrt(1+t)  # D = 2 + t/D.
        assert parent.node_end == pytest.approx(d, abs=2e-9)
        child = r.rc.enter_node(r.doc.ids["b"], r.rc.ev.child_ctx(r.doc.ids["q"], parent))
        assert child.clock_offset == pytest.approx(1 + t/d, abs=2e-9)
    assert not r.frame_rgba(1.5)[..., 3].any()
    assert r.frame_rgba(1.7)[5, 5, 2] == 255


def test_normalized_group_scale_resolves_duration_and_content_clock(tmp_path):
    q = '<sequence id="q"><group id="g">' + normalized("timeScale", 1, 2) \
        + shape("a", "red", 'end="2"', '<expression property="x">10*time</expression>') \
        + '</group>' + shape("b", "blue", 'end="1"') + '</sequence>'
    r = scene(tmp_path, q)
    for t in (.25, .5, .75):
        ctx = r.rc.enter_node(r.doc.ids["g"], Ctx(t, t))
        d = 2 - t
        assert ctx.node_end == pytest.approx(d, abs=2e-9)
        child = r.rc.ev.child_ctx(r.doc.ids["g"], ctx)
        assert child.t == pytest.approx(t * 2/d, abs=2e-9)
        x = int(10 * child.t)
        assert r.frame_rgba(t)[5, x + 2, 0] == 255


@pytest.mark.parametrize("in_sequence", [True, False])
def test_normalized_explicit_end_uses_its_resolved_window(tmp_path, in_sequence):
    a = shape("a", "red", 'end="1"', normalized("end", 1, 2))
    r = scene(tmp_path, '<sequence id="q">' + a + '</sequence>' if in_sequence else a)
    for t in (.5, .75, .1):
        ctx = r.rc.enter_node(r.doc.ids["a"], Ctx(t, t))
        assert ctx.node_end == pytest.approx((1 + np.sqrt(1 + 4*t))/2, abs=2e-9)
        assert r.rc.ev.num(r.doc.ids["a"], "end", ctx) == pytest.approx(ctx.node_end, abs=2e-9)


def test_normalized_start_and_duration_are_consistent(tmp_path):
    body = '<sequence id="q">' + shape("intro", "white", 'end="1"') \
           + shape("a", "red", 'end="1"', normalized("start", 0, .5)) + '</sequence>'
    r = scene(tmp_path, body)
    for t in (1.25, 1.4, 1.1):
        ctx = r.rc.enter_node(r.doc.ids["a"], Ctx(t, t))
        offset = (1.5 - np.sqrt(2.25 - 2*(t-1))) / 2
        assert ctx.clock_offset == pytest.approx(1 + offset, abs=2e-9)
        assert ctx.node_end == pytest.approx(1 - offset, abs=2e-9)
        assert r.rc.ev.num(r.doc.ids["a"], "start", ctx) == pytest.approx(offset, abs=2e-9)


def test_contradictory_normalized_duration_has_bounded_diagnostic(tmp_path, caplog):
    assets = '<assets><video id="v" src="unused.mp4" width="10" height="10" fps="10" duration="1"/></assets>'
    body = '<sequence id="q"><layer id="cyclic_duration" asset="v"><animate property="timeStretch" '
    body += 'timeBase="normalized" defaultInterpolation="hold"><key time="0" value=".5"/>'
    body += '<key time=".5" value="2"/></animate></layer></sequence>'
    r = scene(tmp_path, body, before=assets)
    ctx = r.rc.enter_node(r.doc.ids["cyclic_duration"], Ctx(.75, .75))
    assert np.isfinite(ctx.node_end)
    assert "timing-cycle" in caplog.text and "no converged window" in caplog.text


def test_group_duration_includes_resolved_normalized_descendant_end(tmp_path):
    child = shape("a", "red", 'end="1"', normalized("end", 1, 2))
    r = scene(tmp_path, '<sequence id="q"><group id="g">' + child + '</group>'
              + shape("b", "blue", 'end="1"') + '</sequence>')
    for t in (.1, .5, 1.7):
        ctx = r.rc.enter_node(r.doc.ids["g"], Ctx(t, t))
        duration = (1 + np.sqrt(1 + 4*t)) / 2
        assert ctx.node_end == pytest.approx(duration, abs=2e-9)
        assert r.rc.ev.window(r.doc.ids["b"], Ctx(t, t))[0] == pytest.approx(duration, abs=2e-9)
