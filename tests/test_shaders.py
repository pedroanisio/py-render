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
