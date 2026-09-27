"""Rendered regressions for the second schema audit, including symbol scopes."""
import hashlib
import wave

import numpy as np
import pytest

from scenerender import gl
from scenerender.document import SceneError, load
from scenerender.render import Renderer


@pytest.mark.skipif(not gl.available(), reason="requires OpenGL")
@pytest.mark.parametrize("transform", ['x="25" y="15"', 'x="80" y="15" rotation="90"',
                                        'x="10" y="10" scaleX="1.5" scaleY="0.75"'])
def test_symbol_3d_uses_symbol_canvas_and_instance_transform(tmp_path, transform):
    common = '<scene version="1.1"><project width="120" height="100" fps="10" duration="1"/>' \
             '<materials><material id="m" unlit="true" baseColor="#ffffff"/></materials>' \
             '<symbols><symbol id="sym" width="60" height="40" duration="1">{child}</symbol></symbols>' \
             '<composition><instance id="i" symbol="sym" {transform}/></composition></scene>'
    children = ['<object3D id="o" primitive="plane" width="20" height="10" material="m"/>',
                '<shape id="s" shape="rect" x="20" y="15" width="20" height="10" fill="#ffffff"/>']
    frames = []
    for i, child in enumerate(children):
        path = tmp_path / f"symbol{i}.xml"
        path.write_text(common.format(child=child, transform=transform))
        frames.append(Renderer.open(str(path), strict=True).frame_rgba(0)[..., 3].astype(float) / 255)
    for px in frames:
        assert px.sum() > 100
    yy, xx = np.indices(frames[0].shape)
    centers = [[(px * axis).sum() / px.sum() for axis in (xx, yy)] for px in frames]
    np.testing.assert_allclose(centers[0], centers[1], atol=.2)
    assert np.abs(frames[0] - frames[1]).sum() < 10

def open_scene(tmp_path, body, before="", after="", name="scene.xml"):
    path = tmp_path / name
    path.write_text(f'<scene version="1.1"><project width="100" height="100" fps="10" '
                    f'duration="3" linearLight="false"/>{before}<composition>{body}</composition>{after}</scene>')
    return Renderer.open(str(path), strict=True)


def key(prop, value):
    return f'<animate property="{prop}"><key time="0" value="{value}"/></animate>'


def symbol(body):
    return f'<symbols><symbol id="sym" width="100" height="100" duration="3">{body}</symbol></symbols>'


@pytest.mark.parametrize("prop,value,visible", [("start", "1", False), ("end", ".25", False),
                                                ("start", "0", True)])
def test_instance_timing_overrides_gate_pixels(tmp_path, prop, value, visible):
    r = open_scene(tmp_path, f'<instance id="i" symbol="sym"><override target="s" property="{prop}" '
                   f'value="{value}"/></instance>', symbol('<shape id="s" shape="rect" width="20" height="20"/>'))
    assert bool(r.frame_rgba(.5)[5, 5, 3]) is visible


@pytest.mark.skipif(not gl.available(), reason="requires OpenGL")
@pytest.mark.parametrize("prop,base,value", [("primitive", "box", "sphere"), ("material", "red", "blue")])
def test_3d_evaluated_selections_match_static_content(tmp_path, prop, base, value):
    materials = '<materials><material id="red" baseColor="#ff0000" unlit="true"/>' \
                '<material id="blue" baseColor="#0000ff" unlit="true"/></materials>'
    def body(animated):
        attrs = dict(primitive="plane", radius="20", material="red")
        attrs[prop] = base if animated else value
        return '<object3D id="o" ' + ' '.join(f'{k}="{v}"' for k, v in attrs.items()) + '>' \
               + (key(prop, value) if animated else '') + '</object3D>'
    a = open_scene(tmp_path, body(True), materials)
    b = open_scene(tmp_path, body(False), materials, name="static.xml")
    np.testing.assert_array_equal(a.frame_rgba(.5), b.frame_rgba(.5))


@pytest.mark.skipif(not gl.available(), reason="requires OpenGL")
@pytest.mark.parametrize("in_symbol", [False, True])
def test_camera_height_without_base_and_symbol_camera(tmp_path, in_symbol):
    camera = '<camera id="cam" z="200" projection="orthographic">' + key("orthoHeight", "50") + '</camera>'
    body = '<object3D id="o" primitive="plane" width="20" height="20"/>'
    a = (open_scene(tmp_path, '<instance id="i" symbol="sym"/>', symbol(camera + body)) if in_symbol else
         open_scene(tmp_path, camera + body))
    b = open_scene(tmp_path, '<camera id="cam" z="200" projection="orthographic" orthoHeight="50"/>' + body,
                   name="static.xml")
    np.testing.assert_array_equal(a.frame_rgba(.5), b.frame_rgba(.5))
    assert np.count_nonzero(a.frame_rgba(.5)[..., 3]) == 1600


@pytest.mark.skipif(not gl.available(), reason="requires OpenGL")
def test_3d_instance_geometry_and_world_caches_keep_scope(tmp_path):
    content = '<object3D id="o" primitive="plane" height="10"/>'
    instances = ''.join(f'<instance id="i{i}" symbol="sym"><override target="o" property="x" value="{x}"/>'
                        f'<override target="o" property="width" value="{width}"/></instance>'
                        for i, x, width in [(0, -20, 10), (1, 20, 20)])
    a = open_scene(tmp_path, instances, symbol(content))
    b = open_scene(tmp_path, '<object3D id="left" primitive="plane" x="-20" width="10" height="10"/>'
                   '<object3D id="right" primitive="plane" x="20" width="20" height="10"/>', name="direct.xml")
    for t in (.5, .2, .5):
        np.testing.assert_array_equal(a.frame_rgba(t), b.frame_rgba(t))


def test_particle_preset_instance_override(tmp_path):
    a = open_scene(tmp_path, '<instance id="i" symbol="sym"><override target="p" property="preset" '
                   'value="snow"/></instance>', symbol('<particleEmitter id="p" preset="rain" x="50" y="10"/>'))
    b = open_scene(tmp_path, '<instance id="i" symbol="sym"/>',
                   symbol('<particleEmitter id="p" preset="snow" x="50" y="10"/>'), name="static.xml")
    for t in (.5, .2, .5):
        np.testing.assert_array_equal(a.frame_rgba(t), b.frame_rgba(t))


@pytest.mark.parametrize("consumer", ["bodies", "particles"])
@pytest.mark.parametrize("prop,base,value", [("type", "radial", "directional"),
    ("affects", "bodies", "particles"), ("end", None, ".25"), ("path", "M 0 0 L 100 0", "M 0 80 L 100 80")])
def test_animated_force_field_choices_match_static(tmp_path, consumer, prop, base, value):
    body = ('<shape id="s" shape="rect" x="30" y="30" width="10" height="10"><rigidBody linearDamping="0"/></shape>'
            if consumer == "bodies" else '<particleEmitter id="p" x="30" y="30" rate="0" lifetime="3" '
            'speed="0" size="10" spread="0" emitterShape="point"><burst time="0" count="1"/></particleEmitter>')
    def fields(animated):
        attrs = dict(id="f", type="attractor-path" if prop == "path" else "directional", forceX=".3", strength=".4")
        if (v := base if animated else value) is not None:
            attrs[prop] = v
        return '<physics gravityY="0"><forceField ' + ' '.join(f'{k}="{v}"' for k, v in attrs.items()) \
               + '>' + (key(prop, value) if animated else '') + '</forceField></physics>'
    a = open_scene(tmp_path, body, after=fields(True))
    b = open_scene(tmp_path, body, after=fields(False), name="static.xml")
    for t in (.75, .25, .75):
        np.testing.assert_array_equal(a.frame_rgba(t), b.frame_rgba(t))


def test_particle_field_selection_changes_during_simulation(tmp_path):
    from scenerender.evaluator import Ctx
    from scenerender.nodes.particles import particles_at
    body = '<particleEmitter id="p" x="30" y="30" rate="0" lifetime="3" speed="0" spread="0" ' \
           'forceFields="a" emitterShape="point"><burst time="0" count="1"/>' \
           '<animate property="forceFields"><key time="0" value="a"/><key time=".25" value="b"/></animate></particleEmitter>'
    fields = '<physics><forceField id="a" type="directional" forceX="0"/><forceField id="b" type="directional" forceX="100"/></physics>'
    r = open_scene(tmp_path, body, after=fields)
    ctx = r.rc.node_ctx(r.doc.ids["p"], Ctx(.5, .5))
    state = particles_at(r.rc, r.doc.ids["p"], ctx)
    assert state["vx"][0] == pytest.approx(25, abs=1)
    # Semi-implicit integration advances position with the new velocity.
    assert state["x"][0] == pytest.approx(30 + .5 * 100 * .25 ** 2, abs=.25)


def test_particle_births_use_overridden_start(tmp_path):
    content = '<particleEmitter id="p" x="30" y="30" rate="40" lifetime="3" speed="20" seed="5"/>'
    a = open_scene(tmp_path, '<instance id="i" symbol="sym"><override target="p" property="start" value=".5"/></instance>',
                   symbol(content))
    b = open_scene(tmp_path, '<instance id="i" symbol="sym"/>', symbol(content.replace('id="p"', 'id="p" start=".5"')),
                   name="static.xml")
    for t in (.75, .25, .75):
        np.testing.assert_array_equal(a.frame_rgba(t), b.frame_rgba(t))


def test_evaluated_constraint_target(tmp_path):
    def body(animated):
        return '<group id="a" x="10"/><group id="b" x="60"/>' \
            '<shape id="s" shape="rect" width="10" height="10">' \
            f'<transformConstraint type="copy-position" target="{"a" if animated else "b"}">' \
            + (key("target", "b") if animated else '') + '</transformConstraint></shape>'
    a = open_scene(tmp_path, body(True))
    b = open_scene(tmp_path, body(False), name="static.xml")
    np.testing.assert_array_equal(a.frame_rgba(.5), b.frame_rgba(.5))


def test_symbol_matte_uses_fit_layout_and_each_ancestors_clock(tmp_path):
    content = '<shape id="fill" shape="rect" width="40" height="40" fill="#ff0000" matte="cut"/>' \
        '<group id="row" width="40" height="40" layout="row" timeScale="2">' \
        '<shape id="gap" shape="rect" width="10" height="40" opacity="0"/>' \
        '<shape id="cut" shape="rect" width="10" height="40">' \
        '<expression property="x">10*time</expression></shape></group>'
    symbols = '<symbols><symbol id="sym" width="40" height="40" duration="3">' + content + '</symbol></symbols>'
    a = open_scene(tmp_path, '<instance id="i" symbol="sym" x="90" y="10" rotation="90" '
                   'start=".5" speed="2" fit="fill" boxWidth="60" boxHeight="40"/>', symbols)
    # Symbol x = layout(10) + 10 * group speed(2) * instance speed(2) * (t-.5).
    # Fitting scales x by 1.5; rotating maps that x to frame y.
    for t in (.75, 1., .75):
        y = 10 + 1.5 * (10 + 40 * (t - .5))
        b = open_scene(tmp_path, f'<shape id="ref" shape="rect" x="50" y="{y}" width="40" '
                       'height="15" fill="#ff0000"/>', name="direct.xml")
        np.testing.assert_array_equal(a.frame_rgba(t), b.frame_rgba(t))


def test_symbol_matte_references_composition_clock_and_layout(tmp_path):
    target = '<group id="row" layout="row" timeScale="3">' \
        '<shape id="gap" shape="rect" width="10" height="100" opacity="0"/>' \
        '<shape id="cut" shape="rect" width="10" height="100">' \
        '<expression property="x">20*time</expression></shape></group>'
    content = '<shape id="fill" shape="rect" width="100" height="100" fill="#ff0000" matte="cut"/>'
    a = open_scene(tmp_path, target + '<instance id="i" symbol="sym" start=".25" speed="2"/>', symbol(content))
    for t in (.5, .75, .5):
        b = open_scene(tmp_path, f'<shape id="ref" shape="rect" x="{10 + 60*t}" width="10" '
                       'height="100" fill="#ff0000"/>', name="direct.xml")
        np.testing.assert_array_equal(a.frame_rgba(t), b.frame_rgba(t))


@pytest.mark.parametrize("space", ["world", "local"])
def test_symbol_constraint_uses_target_clock_and_parent_canvas(tmp_path, space):
    target = '<group id="clock" x="10" y="10" timeScale="2">' \
        '<group id="target" y="5"><expression property="x">20*time</expression></group></group>'
    content = '<shape id="copy" shape="rect" width="10" height="10" fill="#ff0000">' \
        f'<transformConstraint type="copy-position" target="target" space="{space}"/></shape>'
    a = open_scene(tmp_path, target + '<instance id="i" symbol="sym" x="20" y="30" speed="3"/>', symbol(content))
    for t in (.25, .5, .25):
        x, y = (10 + 40*t, 15) if space == "world" else (20 + 40*t, 35)
        b = open_scene(tmp_path, f'<shape id="ref" shape="rect" x="{x}" y="{y}" width="10" '
                       'height="10" fill="#ff0000"/>', name="direct.xml")
        np.testing.assert_array_equal(a.frame_rgba(t), b.frame_rgba(t))


def test_symbol_parent_reference_keeps_instance_override_and_fit(tmp_path):
    content = '<group id="target" x="50%" y="5"/>' \
              '<shape id="child" parent="target" shape="rect" x="2" width="10" height="10" fill="#ff0000"/>'
    symbols = '<symbols><symbol id="sym" width="40" height="40" duration="3">' + content + '</symbol></symbols>'
    instances = '<instance id="one" symbol="sym" x="10" y="10" fit="fill" boxWidth="80" boxHeight="40"/>' \
        '<instance id="two" symbol="sym" x="10" y="50" fit="fill" boxWidth="80" boxHeight="40">' \
        '<override target="sym" property="width" value="80"/></instance>'
    a = open_scene(tmp_path, instances, symbols)
    b = open_scene(tmp_path, '<shape id="a" shape="rect" x="54" y="15" width="20" height="10" fill="#ff0000"/>'
                   '<shape id="b" shape="rect" x="52" y="55" width="10" height="10" fill="#ff0000"/>', name="direct.xml")
    np.testing.assert_array_equal(a.frame_rgba(.5), b.frame_rgba(.5))


@pytest.mark.skipif(not gl.available(), reason="requires OpenGL")
def test_symbol_shader_source_uses_global_node_clock_and_layout(tmp_path):
    import urllib.parse
    code = 'void main() { fragColor = texture(sourceTexture, uv); }'
    effects = f'<effects><effect id="sample" type="shader" source="source" space="raw" ' \
              f'src="data:,{urllib.parse.quote(code)}"/></effects>'
    source = '<group id="row" layout="row" timeScale="3">' \
        '<shape id="gap" shape="rect" width="10" height="100" opacity="0"/>' \
        '<shape id="source" shape="rect" width="10" height="100" fill="#ff0000">' \
        '<expression property="x">20*time</expression></shape></group>'
    content = '<shape id="fill" shape="rect" width="100" height="100" effects="sample"/>'
    a = open_scene(tmp_path, source + '<instance id="i" symbol="sym" start=".25" speed="2"/>',
                   symbol(content), effects)
    for t in (.5, .75, .5):
        b = open_scene(tmp_path, f'<shape id="ref" shape="rect" x="{10 + 60*t}" width="10" '
                       'height="100" fill="#ff0000"/>', name="direct.xml")
        np.testing.assert_allclose(a.frame_linear(t), b.frame_linear(t), atol=1e-5)


def test_transition_mattes_evaluate_references_and_asset_time(tmp_path):
    from PIL import Image
    from scenerender.evaluator import Ctx
    from scenerender.transitions import matte_rgba
    from scenerender.transitions.wipes import matte_luma
    Image.new("RGB", (2, 2), "black").save(tmp_path / "frame0.png")
    Image.new("RGB", (2, 2), "white").save(tmp_path / "frame1.png")
    assets = '<assets><imageSequence id="seq" src="frame%d.png" first="0" last="1" fps="1" width="2" height="2"/></assets>'
    body = '<group id="clock" timeScale="2"><shape id="mask" shape="rect" width="10" height="100">' \
        '<expression property="x">20*time</expression></shape></group>' \
        '<shape id="a" shape="rect" width="100" height="100" end="1"/>' \
        '<shape id="b" shape="rect" width="100" height="100" start="1"/>' \
        '<transition id="tr" type="luma" from="a" to="b" matte="mask">' \
        '<animate property="matte"><key time="0" value="mask"/><key time="1" value="seq"/></animate></transition>'
    r = open_scene(tmp_path, body, assets)
    for t in (.5, 1.1, .5):
        px = matte_rgba(r.rc, r.doc.ids["tr"], Ctx(t, t))
        luma = matte_luma(r.rc, r.doc.ids["tr"], Ctx(t, t))
        expected = np.ones((100, 100), np.float32) if t >= 1 else np.zeros((100, 100), np.float32)
        if t < 1:
            expected[:, 20:30] = 1
        # Float image filtering may differ by one float32 ulp when magnified.
        np.testing.assert_allclose(px[..., 3], expected, rtol=0, atol=1e-7)
        np.testing.assert_allclose(luma, expected, atol=1e-6)


@pytest.mark.parametrize("prop", ["matte", "matteVisible"])
def test_matte_visibility_uses_consumers_group_clock(tmp_path, prop):
    targets = '<shape id="a" shape="rect" width="20" height="20" fill="#ff0000"/>' \
              '<shape id="b" shape="rect" x="50" width="20" height="20" fill="#00ff00"/>'
    values = ('a', 'b') if prop == "matte" else ('false', 'true')
    consumer = '<group id="clock" timeScale="2"><shape id="user" shape="rect" width="100" height="100" ' \
        'opacity="0" matte="a"><animate property="' + prop + '"><key time="0" value="' + values[0] + '"/>' \
        '<key time="1" value="' + values[1] + '"/></animate></shape></group>'
    a = open_scene(tmp_path, targets + consumer)
    reference = targets.split('<shape id="b"')[0] if prop == "matte" else targets
    b = open_scene(tmp_path, reference, name="direct.xml")
    np.testing.assert_array_equal(a.frame_rgba(.75), b.frame_rgba(.75))


@pytest.mark.parametrize("show_second", [False, True])
def test_global_matte_visibility_combines_instantiated_overrides(tmp_path, show_second):
    targets = '<shape id="a" shape="rect" width="20" height="20" fill="#ff0000"/>' \
              '<shape id="b" shape="rect" x="50" width="20" height="20" fill="#00ff00"/>'
    content = '<shape id="user" shape="rect" width="100" height="100" opacity="0" matte="a"/>'
    instances = '<instance id="one" symbol="sym"/><instance id="two" symbol="sym">' \
        '<override target="user" property="matte" value="b"/>' \
        f'<override target="user" property="matteVisible" value="{str(show_second).lower()}"/></instance>'
    a = open_scene(tmp_path, targets + instances, symbol(content))
    b = open_scene(tmp_path, targets[targets.index('<shape id="b"'):] if show_second else '', name="direct.xml")
    np.testing.assert_array_equal(a.frame_rgba(.5), b.frame_rgba(.5))


def test_uninstantiated_symbol_does_not_hide_composition_matte(tmp_path):
    target = '<shape id="a" shape="rect" width="20" height="20" fill="#ff0000"/>'
    a = open_scene(tmp_path, target, symbol('<shape id="unused" shape="rect" width="20" height="20" matte="a"/>'))
    b = open_scene(tmp_path, target, name="direct.xml")
    np.testing.assert_array_equal(a.frame_rgba(.5), b.frame_rgba(.5))


def test_effective_matte_reference_hides_only_selected_instance(tmp_path):
    content = '<shape id="mask" shape="rect" width="20" height="20" fill="#ff0000"/>'
    body = '<instance id="one" symbol="sym"/><instance id="two" symbol="sym" x="50"/>' \
        '<shape id="user" shape="rect" width="100" height="100" opacity="0">' \
        '<expression property="matte">"one/mask"</expression></shape>'
    a = open_scene(tmp_path, body, symbol(content))
    b = open_scene(tmp_path, '<shape id="ref" shape="rect" x="50" width="20" height="20" fill="#ff0000"/>',
                   name="direct.xml")
    np.testing.assert_array_equal(a.frame_rgba(.5), b.frame_rgba(.5))


def test_parent_override_can_target_another_instance_of_same_symbol(tmp_path):
    content = '<shape id="child" shape="rect" x="2" width="20" height="20" fill="#0000ff"/>'
    body = '<instance id="two" symbol="sym" x="50"/><instance id="one" symbol="sym" x="10">' \
           '<override target="child" property="parent" value="two/child"/>' \
           '<override target="child" property="fill" value="#ff0000"/></instance>'
    a = open_scene(tmp_path, body, symbol(content))
    b = open_scene(tmp_path, '<shape id="blue" shape="rect" x="52" width="20" height="20" fill="#0000ff"/>'
                   '<shape id="red" shape="rect" x="54" width="20" height="20" fill="#ff0000"/>', name="direct.xml")
    np.testing.assert_array_equal(a.frame_rgba(.5), b.frame_rgba(.5))


def test_evaluated_deformer_selection(tmp_path):
    def body(animated):
        return '<shape id="s" shape="rect" width="30" height="30" x="35" y="35">' \
            f'<deform><modifier type="{"wave" if animated else "twist"}" amount="20">' \
            + (key("type", "twist") if animated else '') + '</modifier></deform></shape>'
    a = open_scene(tmp_path, body(True))
    b = open_scene(tmp_path, body(False), name="static.xml")
    np.testing.assert_array_equal(a.frame_rgba(.5), b.frame_rgba(.5))


@pytest.mark.parametrize("speed", [1, 2])
def test_symbol_physics_follows_instance_clock_and_seeking(tmp_path, speed):
    shape = '<shape id="s" shape="rect" x="10" y="10" width="10" height="10">' \
            '<rigidBody linearDamping="0"/></shape>'
    physics = '<physics gravityY="-1" pixelsPerMeter="100"/>'
    a = open_scene(tmp_path, f'<instance id="i" symbol="sym" start="1" speed="{speed}"/>', symbol(shape), physics)
    b = open_scene(tmp_path, shape, after=physics, name="direct.xml")
    for t in (.5, .2, .5):
        np.testing.assert_array_equal(a.frame_rgba(1 + t / speed), b.frame_rgba(t))


def test_symbol_physics_floor_uses_its_canvas(tmp_path):
    shape = '<shape id="s" shape="rect" x="20" y="0" width="10" height="10">' \
            '<rigidBody linearDamping="0"/></shape>'
    physics = '<physics bounds="floor" pixelsPerMeter="100"/>'
    symbols = '<symbols><symbol id="sym" width="60" height="40" duration="3">' + shape + '</symbol></symbols>'
    a = open_scene(tmp_path, '<instance id="i" symbol="sym" x="10" y="15"/>', symbols, physics)
    alpha = a.frame_rgba(2)[..., 3]
    yy, xx = np.nonzero(alpha)
    assert xx.min() == 30 and xx.max() == 39
    assert yy.min() == pytest.approx(45, abs=1)
    assert yy.max() == pytest.approx(54, abs=1)


@pytest.mark.parametrize("three_d", ['threeD="true"', ''])
def test_symbol_camera_2d_plane_uses_instance_transform(tmp_path, three_d):
    shape = '<shape id="s" shape="rect" x="20" y="15" width="20" height="10" fill="#ffffff" {}/>'
    symbols = '<symbols><symbol id="sym" width="60" height="40" duration="3">' + shape + '</symbol></symbols>'
    a = open_scene(tmp_path, '<instance id="i" symbol="sym" x="75" y="10" rotation="90"/>', symbols.format(three_d))
    b = open_scene(tmp_path, '<shape id="s" shape="rect" x="50" y="30" width="10" height="20" fill="#ffffff"/>',
                   name="direct.xml")
    np.testing.assert_array_equal(a.frame_rgba(.5), b.frame_rgba(.5))


def test_audio_effect_enabled_switch_is_sample_exact(tmp_path):
    from scenerender.audio.mix import Mixer
    sr = 48000
    signal = (np.sin(2 * np.pi * 440 * np.arange(sr) / sr) * 6000).astype('<i2')
    with wave.open(str(tmp_path / 'tone.wav'), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(signal.tobytes())
    assets = '<assets><audio id="a" src="tone.wav"/></assets>'
    def mix(fx, name):
        r = open_scene(tmp_path, '', assets, '<audioMix><audioTrack id="track" asset="a">'
                       + fx + '</audioTrack></audioMix>', name)
        return Mixer(r.doc, r.rc.ev).render(0, 1)
    # Switch between control-rate ticks: no interpolated fade and no delayed step.
    switch = 12345 / sr
    dynamic = mix('<audioEffect type="gain" gain="-20"><animate property="enabled">'
                  f'<key time="0" value="false"/><key time="{switch}" value="true"/>'
                  '</animate></audioEffect>', 'dynamic.xml')
    dry = mix('<audioEffect type="gain" gain="-20" enabled="false"/>', 'dry.xml')
    wet = mix('<audioEffect type="gain" gain="-20"/>', 'wet.xml')
    np.testing.assert_array_equal(dynamic[:12345], dry[:12345])
    np.testing.assert_array_equal(dynamic[12345:], wet[12345:])


def test_data_source_hash_checked_before_consuming_rows(tmp_path):
    raw = b'[10,40]'
    (tmp_path / 'rows.json').write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    r = open_scene(tmp_path, '', f'<parameters><data id="rows" src="rows.json" sha256="{digest}"/></parameters>')
    assert r.doc.data['rows'] == [10, 40]
    (tmp_path / 'rows.json').write_text('[99]')
    with pytest.raises(SceneError, match='sha256'):
        load(r.doc.path, strict=True)
