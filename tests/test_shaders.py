"""GLSL effects and gl-transitions shader transitions (scenerender/effects/shader.py, transitions/shader.py)."""
from __future__ import annotations

import base64
import logging
import urllib.parse
from pathlib import Path

import numpy as np
import pytest

from scenerender import document, gl
from scenerender.compositor import RenderContext
from scenerender.effects import premul, straight
from scenerender.effects import shader as eng
from scenerender.evaluator import Ctx, Evaluator
from scenerender.raster import Buf, linear_to_srgb, srgb_to_linear
from scenerender.registry import EFFECTS, FULL, TRANSITIONS, _warned, load_plugins
from scenerender.transitions import shader as tshader

load_plugins()
FIX = Path(__file__).parent / "fixtures"
SH = FIX / "shaders"
pytestmark = pytest.mark.skipif(not gl.available(), reason="no OpenGL context")


def make_doc(tmp_path, body="", effects="", linear=True, name="doc.xml"):
    path = tmp_path / name
    path.write_text(f'''<scene version="1.1">
      <project width="96" height="64" fps="24" duration="4" seed="5" linearLight="{str(linear).lower()}"/>
      <composition>
        <shape id="a" shape="rect" width="96" height="64" fill="#2050C0FF" end="2"/>
        <shape id="b" shape="rect" width="96" height="64" fill="#E0A020FF" start="2"/>
        {body}
      </composition>
      {f"<effects>{effects}</effects>" if effects else ""}</scene>''')
    doc = document.load(str(path))
    return RenderContext(doc, Evaluator(doc))


def pictures(rc):
    H, W = rc.height, rc.width
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    A = np.stack([xx / W, yy / H, 0.3 + 0 * xx, 1 + 0 * xx], -1)
    B = np.stack([0.9 - 0.8 * yy / H, 0.2 + 0 * xx, xx / W, 1 + 0 * xx], -1)
    return Buf(A.astype(np.float32), 0, 0), Buf(B.astype(np.float32), 0, 0)


def tr_el(rc, shader, params=(), **attrs):
    from lxml import etree
    el = etree.SubElement(rc.doc.root.find("composition"), "transition", id=f"tr{next(_IDS)}",
                          type="shader", shader=str(shader), motionBlur="false", **attrs)
    for k, v in params:
        etree.SubElement(el, "param", name=k, value=v)
    return el


_IDS = iter(range(10**6))
CTX = Ctx(t=1.0, comp_t=1.0, frame=24)
run_tr = TRANSITIONS.get("shader")
crossfade = TRANSITIONS.get("crossfade")


def test_registered_full():
    for reg in (EFFECTS, TRANSITIONS):
        e = reg.entries["shader"]
        assert e.level == FULL and e.fn is not None and e.note


# ---------------------------------------------------------------- transitions
@pytest.mark.parametrize("linear", [True, False], ids=["linear", "srgb"])
@pytest.mark.parametrize("name", ["fade", "directionalwipe", "circleopen", "doorway"])
def test_transition_endpoints_equal_inputs(tmp_path, name, linear):
    rc = make_doc(tmp_path, linear=linear)
    A, B = pictures(rc)
    tr = tr_el(rc, SH / f"{name}.glsl")
    np.testing.assert_allclose(run_tr(rc, tr, A, B, 0.0, CTX).px, A.px, atol=2e-3)
    np.testing.assert_allclose(run_tr(rc, tr, A, B, 1.0, CTX).px, B.px, atol=2e-3)


def test_fade_matches_display_space_mix(tmp_path):
    rc = make_doc(tmp_path)
    A, B = pictures(rc)
    got = run_tr(rc, tr_el(rc, SH / "fade.glsl"), A, B, 0.25, CTX).px
    want = srgb_to_linear(0.75 * linear_to_srgb(A.px[..., :3]) + 0.25 * linear_to_srgb(B.px[..., :3]))
    np.testing.assert_allclose(got[..., :3], want, atol=2e-3)


def test_missing_side_fades_coverage_without_darkening(tmp_path):
    rc = make_doc(tmp_path)
    _, B = pictures(rc)
    got = run_tr(rc, tr_el(rc, SH / "fade.glsl"), None, B, 0.5, CTX).px
    np.testing.assert_allclose(got, B.px * 0.5, atol=2e-3)


def test_uniform_defaults_params_and_attributes(tmp_path):
    rc = make_doc(tmp_path)
    A, B = pictures(rc)
    W = rc.width
    default = run_tr(rc, tr_el(rc, SH / "directionalwipe.glsl"), A, B, 0.5, CTX).px
    explicit_default = run_tr(rc, tr_el(rc, SH / "directionalwipe.glsl",
                                        [("direction", "vec2(1.0, -1.0)"), ("smoothness", "0.5")]), A, B, 0.5, CTX).px
    np.testing.assert_array_equal(default, explicit_default)          # `// = ...` defaults were applied
    hard = run_tr(rc, tr_el(rc, SH / "directionalwipe.glsl", [("direction", "1,0"), ("smoothness", "0")]),
                  A, B, 0.5, CTX).px
    np.testing.assert_allclose(hard[:, : W // 2 - 2], B.px[:, : W // 2 - 2], atol=2e-3)   # moving right: B enters left
    np.testing.assert_allclose(hard[:, W // 2 + 2:], A.px[:, W // 2 + 2:], atol=2e-3)
    assert not np.allclose(hard, default, atol=1e-2)
    # An explicit @direction attribute (motion to the right) beats the comment default.
    attr = run_tr(rc, tr_el(rc, SH / "directionalwipe.glsl", [("smoothness", "0")], direction="right"), A, B, 0.5, CTX).px
    np.testing.assert_allclose(attr, hard, atol=1e-5)
    # bool uniform override
    opening = run_tr(rc, tr_el(rc, SH / "circleopen.glsl"), A, B, 0.3, CTX).px
    closing = run_tr(rc, tr_el(rc, SH / "circleopen.glsl", [("opening", "false")]), A, B, 0.3, CTX).px
    cy, cx = rc.height // 2, W // 2
    np.testing.assert_allclose(opening[cy, cx], B.px[cy, cx], atol=2e-3)     # the circle opens on B
    np.testing.assert_allclose(closing[cy, cx], A.px[cy, cx], atol=2e-3)


def test_transition_determinism_and_data_uri(tmp_path):
    rc = make_doc(tmp_path)
    A, B = pictures(rc)
    code = (SH / "circleopen.glsl").read_text()
    t1 = tr_el(rc, SH / "circleopen.glsl")
    t2 = tr_el(rc, "data:," + urllib.parse.quote(code))
    t3 = tr_el(rc, "data:text/plain;base64," + base64.b64encode(code.encode()).decode())
    r = [run_tr(rc, t, A, B, 0.4, CTX).px for t in (t1, t1, t2, t3)]
    for x in r[1:]:
        np.testing.assert_array_equal(r[0], x)


def test_transition_compile_error_and_missing_file_fall_back(tmp_path, caplog):
    rc = make_doc(tmp_path)
    A, B = pictures(rc)
    with caplog.at_level(logging.WARNING):
        got = run_tr(rc, tr_el(rc, SH / "broken.glsl"), A, B, 0.3, CTX).px
    assert "undeclared" in caplog.text
    ref = crossfade(rc, tr_el(rc, SH / "broken.glsl"), A, B, 0.3, CTX).px
    np.testing.assert_array_equal(got, ref)
    np.testing.assert_array_equal(run_tr(rc, tr_el(rc, SH / "nope.glsl"), A, B, 0.3, CTX).px, ref)


def test_gl_unavailable_falls_back(tmp_path, monkeypatch, caplog):
    rc = make_doc(tmp_path, effects=f'<effect id="fx" type="shader" src="{SH / "invert.glsl"}"/>')
    A, B = pictures(rc)

    def boom():
        raise gl.GLUnavailable("test: no GL")
    monkeypatch.setattr(gl, "context", boom)
    eng._PROGRAMS.clear()
    _warned.discard(("transition-shader", "gl"))
    _warned.discard(("effect-shader", "gl"))
    with caplog.at_level(logging.WARNING):
        tr = tr_el(rc, SH / "fade.glsl")
        np.testing.assert_array_equal(run_tr(rc, tr, A, B, 0.3, CTX).px, crossfade(rc, tr, A, B, 0.3, CTX).px)
        assert rc.apply_effects(["fx"], A, CTX, None).px is A.px or np.array_equal(
            rc.apply_effects(["fx"], A, CTX, None).px, A.px)
    assert "OpenGL unavailable" in caplog.text


def test_motion_blur_samples_progress(tmp_path):
    rc = make_doc(tmp_path, body='<transition id="t" type="shader" from="a" to="b" duration="1" '
                                 f'shader="{SH / "fade.glsl"}"/>')
    tr = rc.doc.ids["t"]
    mid = Ctx(t=2.0, comp_t=2.0, frame=48)
    qs = tshader._progress_samples(rc, tr, mid, 0.5)
    assert len(qs) > 1 and min(qs) < 0.5 < max(qs)
    tr.set("motionBlur", "false")
    assert tshader._progress_samples(rc, tr, mid, 0.5) == [0.5]
    tr.set("motionBlur", "true")
    px = rc.render_frame(2.0).px                   # through the compositor
    assert px[..., 3].min() > 0.99


# ---------------------------------------------------------------- effects
@pytest.fixture
def tile():
    y, x = np.indices((40, 56), dtype=np.float32)
    a = np.zeros((40, 56, 1), np.float32)
    a[6:34, 8:48] = 0.6
    a[12:28, 14:42] = 1
    return Buf(premul(np.stack([x / 55, y / 39, (x + y) / 95], -1), a), 5, 3)


def fx(rc, eid, buf, ctx=CTX):
    return rc.apply_effects([eid], buf, ctx, None)


def expected_invert(px, linear=True):
    rgb, a = straight(px)
    d = linear_to_srgb(rgb) if linear else rgb
    inv = 1 - d
    return premul(srgb_to_linear(inv) if linear else inv, a)


@pytest.mark.parametrize("linear", [True, False], ids=["linear", "srgb"])
def test_invert_both_conventions(tmp_path, tile, linear):
    rc = make_doc(tmp_path, linear=linear, effects=f'''
      <effect id="plain" type="shader" src="{SH / 'invert.glsl'}"/>
      <effect id="toy" type="shader" src="{SH / 'invert_shadertoy.glsl'}"/>''')
    want = expected_invert(tile.px, linear)
    for eid in ("plain", "toy"):
        out = fx(rc, eid, tile)
        assert out.rect == tile.rect
        np.testing.assert_allclose(out.px, want, atol=2e-3)


def test_effect_uniform_precedence_and_animation(tmp_path, tile):
    src = SH / "invert.glsl"
    rc = make_doc(tmp_path, effects=f'''
      <effect id="zero" type="shader" src="{src}"><param name="amount" value="0"/></effect>
      <effect id="attr" type="shader" src="{src}" amount="0.5"/>
      <effect id="param" type="shader" src="{src}" amount="0"><param name="amount" value="0.5"/></effect>
      <effect id="anim" type="shader" src="{src}">
        <animate property="amount"><key time="0" value="0"/><key time="2" value="1"/></animate>
      </effect>''')
    np.testing.assert_allclose(fx(rc, "zero", tile).px, tile.px, atol=2e-3)
    half = fx(rc, "attr", tile).px
    np.testing.assert_allclose(fx(rc, "param", tile).px, half, atol=1e-6)     # param beats the attribute
    assert not np.allclose(half, tile.px, atol=1e-2)
    np.testing.assert_allclose(fx(rc, "anim", tile, Ctx(t=0, comp_t=0)).px, tile.px, atol=2e-3)
    np.testing.assert_allclose(fx(rc, "anim", tile, Ctx(t=1, comp_t=1)).px, half, atol=2e-3)


def test_shadertoy_time_animation_is_deterministic(tmp_path, tile):
    rc = make_doc(tmp_path, effects=f'<effect id="rb" type="shader" src="{SH / "rainbow_shadertoy.glsl"}"/>')
    c0, c1 = Ctx(t=0, comp_t=0, frame=0), Ctx(t=1, comp_t=1, frame=24)
    a0, a0b, a1 = fx(rc, "rb", tile, c0).px, fx(rc, "rb", tile, c0).px, fx(rc, "rb", tile, c1).px
    np.testing.assert_array_equal(a0, a0b)
    assert np.abs(a0 - a1).mean() > 1e-3
    np.testing.assert_allclose(a0[..., 3], tile.px[..., 3], atol=1e-6)        # coverage kept


def test_padding_grows_tile_and_spills(tmp_path, tile):
    rc = make_doc(tmp_path, effects=f'''
      <effect id="g" type="shader" src="{SH / 'glow.glsl'}" radius="10" color="#FF00FFFF">
        <param name="padding" value="8"/></effect>
      <effect id="g4" type="shader" src="{SH / 'glow.glsl'}"><param name="padding" value="1,2,3,4"/></effect>''')
    out = fx(rc, "g", tile)
    assert out.rect == (tile.x0 - 8, tile.y0 - 8, tile.x0 + tile.w + 8, tile.y0 + tile.h + 8)
    assert out.px[5:8, 20:-20, 3].max() > 0.05                                     # glow outside the input rect
    assert fx(rc, "g4", tile).rect == (tile.x0 - 4, tile.y0 - 1, tile.x0 + tile.w + 2, tile.y0 + tile.h + 3)


def test_isf_multipass_and_defaults(tmp_path, tile):
    rc = make_doc(tmp_path, effects=f'''
      <effect id="duo" type="shader" src="{SH / 'duotone.fs'}"/>
      <effect id="off" type="shader" src="{SH / 'duotone.fs'}"><param name="amount" value="0"/></effect>''')
    out = fx(rc, "duo", tile).px
    assert np.abs(out - tile.px).mean() > 1e-2
    np.testing.assert_allclose(fx(rc, "off", tile).px, tile.px, atol=2e-3)


def test_isf_additional_named_image_inputs(tmp_path, tile):
    import json
    from PIL import Image
    image = tmp_path / "blue.png"
    Image.new("RGBA", (4, 4), (0, 0, 255, 255)).save(image)
    header = {"INPUTS": [{"NAME": n, "TYPE": "image"} for n in ("inputImage", "other", "third", "fourth")]}
    code = "/*" + json.dumps(header) + "*/\nvoid main() { gl_FragColor = " \
           "0.5 * IMG_THIS_NORM_PIXEL(third) + 0.5 * IMG_THIS_NORM_PIXEL(fourth); }"
    src = tmp_path / "many.fs"
    src.write_text(code)
    rc = make_doc(tmp_path, body='<shape id="red" shape="rect" width="96" height="64" fill="#ff0000"/>',
                  effects=f'<effect id="many" type="shader" space="raw" src="{src}">'
                          f'<param name="third" value="red"/><param name="fourth" value="{image}"/></effect>')
    got = fx(rc, "many", tile)
    np.testing.assert_allclose(got.px[..., :3], np.broadcast_to([.5, 0, .5], got.px[..., :3].shape), atol=1e-6)
    np.testing.assert_allclose(got.px[..., 3], 1, atol=1e-6)


@pytest.mark.parametrize("kind", ["generator", "imageSequence"])
@pytest.mark.parametrize("included", [False, True])
def test_shader_asset_inputs_render_at_source_size_and_time(tmp_path, kind, included):
    from PIL import Image
    from scenerender.render import Renderer
    sub = tmp_path / "sub"
    sub.mkdir()
    src = sub / "asset.fs"
    src.write_text('''/*{"INPUTS":[{"NAME":"assetImage","TYPE":"image"}]}*/
        void main() { vec4 sample = IMG_NORM_PIXEL(assetImage, vec2(.9, .5));
          gl_FragColor = vec4(sample.rg, IMG_SIZE(assetImage).x / 1024.0, sample.a); }''')
    if kind == "generator":
        asset = '<generator id="picture" kind="solid" width="512" height="128" paint="#ff0000">' \
                '<animate property="paint"><key time="0" value="#ff0000"/>' \
                '<key time="1" value="#00ff00"/></animate></generator>'
    else:
        Image.new("RGBA", (512, 128), (255, 0, 0, 255)).save(sub / "frame0.png")
        Image.new("RGBA", (512, 128), (0, 255, 0, 255)).save(sub / "frame1.png")
        asset = '<imageSequence id="picture" src="frame%d.png" first="0" last="1" fps="1" width="512" height="128"/>'
    project = '<project width="16" height="12" fps="24" duration="2"/>'
    child = sub / "child.xml"
    child.write_text('<scene version="1.1">' + project + f'<assets>{asset}</assets>'
        '<composition><shape id="node" shape="rect" width="16" height="12" effects="sample"/></composition>'
        '<effects><effect id="sample" type="shader" space="raw" src="asset.fs">'
        '<param name="assetImage" value="picture"/></effect></effects></scene>')
    path = child
    if included:
        path = tmp_path / "parent.xml"
        path.write_text('<scene version="1.1">' + project + '<composition>'
                        '<include id="child" src="sub/child.xml"/></composition></scene>')
    r = Renderer.open(str(path), strict=True)
    for t, expected in ((0., [1, 0, .5, 1]), (1.1, [0, 1, .5, 1]), (0., [1, 0, .5, 1])):
        px = r.frame_linear(t)
        np.testing.assert_allclose(px, np.broadcast_to(expected, px.shape), atol=1e-6)
        assert (r.rc.width, r.rc.height) == (16, 12)


def test_isf_persistent_feedback_replays_upstream_effects_and_seeks(tmp_path):
    import json
    header = {"INPUTS": [{"NAME": "inputImage", "TYPE": "image"}],
              "PASSES": [{"TARGET": "history", "PERSISTENT": True, "FLOAT": True}]}
    code = "/*" + json.dumps(header) + "*/\nvoid main() { gl_FragColor = vec4(" \
           "mix(IMG_THIS_NORM_PIXEL(inputImage).rgb, IMG_THIS_NORM_PIXEL(history).rgb, 0.5), 1.0); }"
    src = tmp_path / "feedback.fs"
    src.write_text(code)
    upstream = urllib.parse.quote("void main() { fragColor = vec4(time, 0.0, 0.0, 1.0); }")

    def renderer():
        return make_doc(tmp_path, body='<shape id="feedback" shape="rect" width="96" height="64" '
                                      'effects="clock feedbackfx"/>',
                        effects=f'<effect id="clock" type="shader" space="raw" src="data:,{upstream}"/>'
                                f'<effect id="feedbackfx" type="shader" space="raw" src="{src}"/>')
    rc = renderer()
    for n in (12, 3, 12, 0, 7):
        expected = 0.
        for k in range(n + 1):
            expected = .5 * (k / 24) + .5 * expected
        got = rc.render_frame(n / 24, n).px
        np.testing.assert_allclose(got[..., 0], expected, atol=1e-6)
        np.testing.assert_allclose(got, renderer().render_frame(n / 24, n).px, atol=1e-6)


def test_isf_feedback_is_separate_for_each_node(tmp_path):
    code = '''/*{"INPUTS":[{"NAME":"inputImage","TYPE":"image"}],
                "PASSES":[{"TARGET":"previous","PERSISTENT":true,"FLOAT":true}]}*/
        void main() { gl_FragColor = vec4(0.5 * (IMG_THIS_NORM_PIXEL(inputImage).rgb
                                          + IMG_THIS_NORM_PIXEL(previous).rgb), 1.0); }'''
    src = tmp_path / "shared.fs"
    src.write_text(code)
    rc = make_doc(tmp_path, body='<shape id="left" shape="rect" width="48" height="64" '
                                'fill="#ff0000" effects="shared"/>'
                                '<shape id="right" shape="rect" x="48" width="48" height="64" '
                                'fill="#00ff00" effects="shared" start="0.125"/>',
                  effects=f'<effect id="shared" type="shader" space="raw" src="{src}"/>')
    for frame in (6, 4, 6):
        got = rc.render_frame(frame / 24, frame).px
        np.testing.assert_allclose(got[20, 20, :3], [1 - .5 ** (frame + 1), 0, 0], atol=1e-6)
        np.testing.assert_allclose(got[20, 70, :3], [0, 1 - .5 ** (frame - 2), 0], atol=1e-6)


def test_isf_feedback_checkpoints_bound_replay_and_memory(tmp_path, monkeypatch):
    from scenerender.effects import shader
    src = tmp_path / "accumulate.fs"
    src.write_text('''/*{"PASSES":[{"TARGET":"previous","PERSISTENT":true,"FLOAT":true}]}*/
        void main() { gl_FragColor = vec4(IMG_THIS_NORM_PIXEL(previous).r + .001, 0, 0, 1); }''')
    rc = make_doc(tmp_path, body='<shape id="s" shape="rect" width="96" height="64" effects="acc"/>',
                  effects=f'<effect id="acc" type="shader" space="raw" src="{src}"/>')
    render, replayed = rc.render_frame, []
    def counted(t, frame=0):
        if rc.cache.get("isf-replaying"):
            replayed.append(frame)
        return render(t, frame)
    monkeypatch.setattr(rc, "render_frame", counted)
    monkeypatch.setattr(shader, "PERSISTENT_CACHE_BYTES", 96 * 64 * 4 * 4 * 2)
    for n in (24, 25, 26, 12, 27, 28):
        before = len(replayed)
        px = rc.render_frame(n / 24, n).px
        np.testing.assert_allclose(px[..., 0], .001 * (n + 1), atol=1e-7)
        if n in (25, 26, 28):
            assert len(replayed) - before == 1
        assert sum(size for _, size in rc.cache["isf-checkpoints"].values()) <= shader.PERSISTENT_CACHE_BYTES


@pytest.mark.parametrize("motion_blur", [False, True])
@pytest.mark.parametrize("convention", ["isf", "shadertoy", "plain"])
def test_shader_frame_index_is_derived_for_time_only_render_calls(tmp_path, motion_blur, convention):
    from scenerender.render import Renderer
    codes = {
        "isf": '/*{"ISFVSN":"2"}*/ void main() { gl_FragColor = vec4(float(FRAMEINDEX)/100., 0, 0, 1); }',
        "shadertoy": 'void mainImage(out vec4 color, in vec2 coord) { color = vec4(float(iFrame)/100., 0, 0, 1); }',
        "plain": 'void main() { fragColor = vec4(float(frame)/100., 0, 0, 1); }',
    }
    rc = make_doc(tmp_path, body='<shape id="s" shape="rect" width="96" height="64" effects="index"/>',
                  effects=f'<effect id="index" type="shader" space="raw" src="data:,{urllib.parse.quote(codes[convention])}"/>')
    rc.motion_blur = motion_blur
    r = Renderer(rc.doc, rc)
    for t in (.5, .25, .5):
        np.testing.assert_allclose(r.frame_linear(t)[..., 0], round(t * 24) / 100, atol=1e-6)
        np.testing.assert_allclose(rc.render_frame(t).px[..., 0], round(t * 24) / 100, atol=1e-6)
    # Explicit indices remain authoritative, including zero and all shutter samples.
    np.testing.assert_allclose(r.frame_linear(.5, 0)[..., 0], 0, atol=1e-6)
    np.testing.assert_allclose(r.frame_linear(.5, 7)[..., 0], .07, atol=1e-6)


def test_isf_final_target_size_uses_animated_input_without_live_uniform(tmp_path, tile):
    src = tmp_path / "size.fs"
    src.write_text('''/*{"INPUTS":[{"NAME":"columns","TYPE":"float","DEFAULT":12}],
        "PASSES":[{"TARGET":"small","WIDTH":"$columns","HEIGHT":"$HEIGHT/2","FLOAT":true}]}*/
        void main() { gl_FragColor = vec4(RENDERSIZE / 100.0, TIMEDELTA, 1.0); }''')
    rc = make_doc(tmp_path, effects=f'<effect id="size" type="shader" space="raw" src="{src}">'
        '<animate property="columns"><key time="0" value="12"/><key time="1" value="24"/></animate></effect>')
    for t in (0., .5, 1.):
        got = fx(rc, "size", tile, Ctx(t, t, frame=round(t * 24))).px
        assert got.shape == tile.px.shape
        # columns is used only by the JSON size expression, so GLSL removes it.
        np.testing.assert_allclose(got[..., 0], (.12 + .12 * t), atol=1e-6)
        np.testing.assert_allclose(got[..., 1], tile.h / 200., atol=1e-6)
        np.testing.assert_allclose(got[..., 2], 0. if t == 0 else 1 / 24, atol=1e-6)


def test_shader_default_input_can_be_animated_without_param(tmp_path, tile):
    src = tmp_path / "input.fs"
    src.write_text('''/*{"INPUTS":[{"NAME":"inputAmount","TYPE":"float","DEFAULT":0.1}]}*/
        void main() { gl_FragColor = vec4(inputAmount, 0, 0, 1); }''')
    rc = make_doc(tmp_path, effects=f'<effect id="v" type="shader" space="raw" src="{src}">'
        '<animate property="inputAmount"><key time="0" value=".2"/><key time="1" value=".8"/></animate></effect>')
    np.testing.assert_allclose(fx(rc, "v", tile, Ctx(.5, .5)).px[..., 0], .5, atol=1e-6)


@pytest.mark.parametrize("lookup,expected", [("IMG_THIS_PIXEL", [0, 0, 1, 1]),
                                            ("IMG_THIS_NORM_PIXEL", [.5, .5, .5, 1]),
                                            ("IMG_NORM_THIS_PIXEL", [.5, .5, .5, 1])])
def test_isf_pixel_and_normalized_lookups_differ_for_small_target(tmp_path, lookup, expected):
    src = tmp_path / "lookup.fs"
    src.write_text('''/*{"INPUTS":[{"NAME":"inputImage","TYPE":"image"}],
        "PASSES":[{"TARGET":"tiny","WIDTH":"1","HEIGHT":"1"}]}*/
        void main() { gl_FragColor = ''' + lookup + '(inputImage); }')
    rc = make_doc(tmp_path, effects=f'<effect id="v" type="shader" space="raw" src="{src}"/>')
    tile = Buf(np.array([[[1, 0, 0, 1], [0, 1, 0, 1]], [[0, 0, 1, 1], [1, 1, 1, 1]]], np.float32), 0, 0)
    np.testing.assert_allclose(fx(rc, "v", tile).px, np.broadcast_to(expected, tile.px.shape), atol=1e-6)


@pytest.mark.parametrize("kind,width,source", [("audio", 2048, "sound"), ("audio", 1, "sound"),
                                              ("audioFFT", 1025, "sound"), ("audioFFT", 1, "master")])
def test_isf_audio_inputs_bind_pcm_channels_and_fft(tmp_path, kind, width, source):
    import json
    import wave
    from scenerender.render import Renderer
    sr = 48000
    times = np.arange(sr) / sr
    samples = (np.stack([.5 * np.sin(2 * np.pi * 1500 * times), np.zeros(sr)], 1) if kind == "audioFFT"
               else np.tile([.5, -.25], (sr, 1)))
    with wave.open(str(tmp_path / "sound.wav"), "wb") as wav:
        wav.setparams((2, 2, sr, 0, "NONE", "not compressed"))
        wav.writeframes((samples * 32768).astype("<i2").tobytes())
    header = {"INPUTS": [{"NAME": "audioInput", "TYPE": kind, "MAX": width}]}
    pixel = .5 if width == 1 else (64.5 if kind == "audioFFT" else 1024.5)
    src = tmp_path / "audio.fs"
    src.write_text('/*' + json.dumps(header) + '*/\nvoid main() { gl_FragColor = vec4('
        f'IMG_PIXEL(audioInput, vec2({pixel}, .5)).r, IMG_PIXEL(audioInput, vec2({pixel}, 1.5)).r, '
        'IMG_SIZE(audioInput).x / 2048.0, 1.0); }')
    p = tmp_path / "audio.xml"
    p.write_text('<scene version="1.1"><project width="8" height="8" fps="24" duration="1"/>'
        '<assets><audio id="sound" src="sound.wav"/></assets>'
        '<composition><shape id="s" shape="rect" width="8" height="8" effects="fx"/></composition>'
        '<effects><effect id="fx" type="shader" space="raw" src="audio.fs">'
        + (f'<param name="audioInput" value="{source}"/>' if source != "master" else '') + '</effect></effects>'
        '<audioMix><audioTrack id="track" asset="sound"/></audioMix></scene>')
    r = Renderer.open(str(p), strict=True)
    expected = [.5, 0] if kind == "audioFFT" else ([.75, .625] if width == 1 else [.75, .375])
    for t in (.5, .25, .5):
        px = r.rc.render_frame(t, round(t * 24)).px
        np.testing.assert_allclose(px[..., :2], np.broadcast_to(expected, px[..., :2].shape), atol=1e-4)
        np.testing.assert_allclose(px[..., 2], width / 2048., atol=1e-6)


@pytest.mark.parametrize("space", ["srgb", "linear-srgb", "rec709", "display-p3", "rec2020", "acescg",
                                   "aces2065-1", "acescct", "xyz-d65", "raw"])
def test_identity_shader_round_trips_every_space(tmp_path, tile, space):
    code = "void main() { fragColor = texture(inputTexture, uv); }"
    rc = make_doc(tmp_path, effects=f'<effect id="id" type="shader" space="{space}" '
                                    f'src="data:,{urllib.parse.quote(code)}"/>')
    np.testing.assert_allclose(fx(rc, "id", tile).px, tile.px, atol=3e-3)


def test_space_changes_what_the_shader_sees(tmp_path):
    code = "void main() { fragColor = vec4(texture(inputTexture, uv).rgb * 0.5, 1.0); }"
    q = urllib.parse.quote(code)
    rc = make_doc(tmp_path, effects=f'''<effect id="s" type="shader" src="data:,{q}"/>
      <effect id="l" type="shader" space="linear-srgb" src="data:,{q}"/>''')
    g = Buf(np.full((4, 4, 4), 0.5, np.float32), 0, 0)
    g.px[..., 3] = 1
    np.testing.assert_allclose(fx(rc, "l", g).px[..., :3], 0.25, atol=1e-4)
    np.testing.assert_allclose(fx(rc, "s", g).px[..., :3], srgb_to_linear(0.5 * linear_to_srgb(np.float32(0.5))),
                               atol=1e-3)


def test_effect_attributes_and_source_channel(tmp_path, tile):
    code = ("uniform vec4 color; uniform vec2 center; uniform int falloff; uniform float radius;"
            "void main() { vec4 s = texture(sourceTexture, uv);"
            " fragColor = vec4(color.rgb * float(falloff == 1) + s.rgb * 0.0, 1.0);"
            " if (distance(gl_FragCoord.xy, center) < radius) fragColor = vec4(0.0, 1.0, 0.0, 1.0); }")
    rc = make_doc(tmp_path, body='<shape id="src" shape="rect" width="96" height="64" fill="#FFFFFFFF"/>',
                  effects=f'<effect id="e" type="shader" color="#FF0000FF" falloff="quadratic" radius="3" source="src" '
                          f'centerX="20" centerY="10" src="data:,{urllib.parse.quote(code)}"/>')
    out = fx(rc, "e", tile).px
    np.testing.assert_allclose(out[30, 40], [1, 0, 0, 1], atol=1e-4)     # colour + enum index (quadratic = 1)
    y, x = 10 - tile.y0, 20 - tile.x0                                    # centerX/Y are frame document px
    np.testing.assert_allclose(out[y, x], [0, 1, 0, 1], atol=1e-4)


def test_effect_compile_error_missing_file_and_no_src_pass_through(tmp_path, tile, caplog):
    rc = make_doc(tmp_path, effects=f'''
      <effect id="bad" type="shader" src="{SH / 'broken.glsl'}"/>
      <effect id="missing" type="shader" src="nope.glsl"/>
      <effect id="none" type="shader"/>''')
    with caplog.at_level(logging.WARNING):
        for eid in ("bad", "missing", "none"):
            np.testing.assert_array_equal(fx(rc, eid, tile).px, tile.px)
    assert "undeclared" in caplog.text and "not found" in caplog.text


def test_default_comment_parsing():
    d = eng.comment_defaults("uniform vec4 c; // = vec4(0., 0., 0., .6)\nuniform float k;// = 2\n"
                             "uniform ivec2 size; // = ivec2(4, 4);\nuniform float none;\n")
    assert d == {"c": "vec4(0., 0., 0., .6)", "k": "2", "size": "ivec2(4, 4)"}


def test_demo_fixture_renders(tmp_path):
    doc = document.load(str(FIX / "shaders_demo.xml"))
    rc = RenderContext(doc, Evaluator(doc))
    for t in (1.0, 3.0, 4.5, 6.0, 9.0):
        px = rc.render_frame(t).px
        assert np.isfinite(px).all() and px[..., 3].min() > 0.99
