"""Transition handlers: end states, single-sided behaviour, geometry and determinism."""
from __future__ import annotations

import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.raster import Buf
import scenerender.nodes.core  # noqa: F401  (shape handler for the compositor test)
import scenerender.transitions.fx  # noqa: F401
import scenerender.transitions.motion  # noqa: F401
import scenerender.transitions.space  # noqa: F401
import scenerender.transitions.wipes  # noqa: F401
import scenerender.transitions.shader  # noqa: F401
from scenerender import document
from scenerender.compositor import RenderContext
from scenerender.evaluator import Evaluator
from scenerender.registry import FULL, TRANSITIONS

SCHEMA_TYPES = [
    "cut", "crossfade", "additive-dissolve", "dip-to-color", "wipe", "slide", "push", "cover",
    "reveal", "zoom-in", "zoom-out", "spin", "whip-pan", "circle-open", "circle-close", "iris",
    "clock-wipe", "radial-wipe", "barn-door", "blinds", "luma", "blur", "glitch", "pixelize",
    "flip", "cube", "page-curl", "film-roll", "stripe", "squash", "shuffle", "carousel",
    "light-leak", "morph", "shader",
]
HANDLED = [t for t in SCHEMA_TYPES if t != "shader"]

SCENE = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="160" height="90" fps="24" duration="4" seed="11" background="#000000FF"/>
  <composition>
    <group id="matte" width="160" height="90">
      <shape id="m-light" shape="rect" width="80" height="90" fill="#FFFFFFFF"/>
      <shape id="m-dark" shape="rect" x="80" width="80" height="90" fill="#000000FF"/>
    </group>
    <shape id="s1" shape="rect" width="160" height="90" fill="#2050C0FF" end="2"/>
    <shape id="s2" shape="rect" width="160" height="90" fill="#E0A020FF" start="2"/>
    <transition id="t1" type="crossfade" from="s1" to="s2" duration="1" matte="matte"/>
  </composition>
</scene>
"""


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    path = tmp_path_factory.mktemp("tr") / "scene.xml"
    path.write_text(SCENE)
    doc = document.load(str(path))
    rc = RenderContext(doc, Evaluator(doc))
    H, W = rc.height, rc.width
    yy, xx = np.mgrid[0:H, 0:W]
    chk = ((xx // 8 + yy // 8) % 2).astype(np.float32)[..., None]

    def pic(c1, c2):
        rgb = np.where(chk > 0, np.array(c1, np.float32), np.array(c2, np.float32))
        return Buf(np.concatenate([rgb, np.ones((H, W, 1), np.float32)], -1).astype(np.float32), 0, 0)
    A = pic((0.05, 0.1, 0.6), (0.02, 0.03, 0.2))
    B = pic((0.8, 0.5, 0.05), (0.4, 0.2, 0.01))
    return rc, rc.doc.ids["t1"], A, B


def run(rig, typ, p, a=True, b=True, t=0.0, **attrs):
    rc, tr, A, B = rig
    tr.set("type", typ)
    for k in list(tr.attrib):
        if k not in ("id", "type", "from", "to", "duration", "matte"):
            del tr.attrib[k]
    for k, v in attrs.items():
        tr.set(k, v)
    ctx = Ctx(t=t, comp_t=t, frame=int(t * 24))
    return TRANSITIONS.get(typ)(rc, tr, A if a else None, B if b else None, p, ctx)


def test_every_schema_type_is_known():
    assert len(SCHEMA_TYPES) == 35
    for t in HANDLED:
        assert TRANSITIONS.get(t) is not None, t
    assert TRANSITIONS.level("shader") == FULL and TRANSITIONS.get("shader") is not None  # tests/test_shaders.py


@pytest.mark.parametrize("typ", HANDLED)
def test_end_states_shape_and_dtype(rig, typ):
    rc, _, A, B = rig
    for p, want in ((0.0, A), (1.0, B)):
        res = run(rig, typ, p)
        assert isinstance(res, Buf)
        assert res.rect == (0, 0, rc.width, rc.height)
        assert res.px.dtype == np.float32
        err = np.abs(res.px - want.px).mean()
        assert err < 2e-3, f"{typ} p={p}: mean error {err}"
    mid = run(rig, typ, 0.5).px
    assert mid.shape == (rc.height, rc.width, 4) and np.all(np.isfinite(mid))
    assert mid[..., 3].max() <= 1 + 1e-4 and mid[..., 3].min() >= -1e-5
    assert np.all(mid[..., :3] <= mid[..., 3:4] + 1e-3)


@pytest.mark.parametrize("typ", HANDLED)
def test_midpoint_differs_and_is_deterministic(rig, typ):
    rc, _, A, B = rig
    m1 = run(rig, typ, 0.37, t=1.6).px.copy()
    m2 = run(rig, typ, 0.37, t=1.6).px
    np.testing.assert_array_equal(m1, m2)
    if typ != "cut":
        assert np.abs(m1 - A.px).mean() > 1e-3 or np.abs(m1 - B.px).mean() > 1e-3


@pytest.mark.parametrize("typ", ["wipe", "push", "circle-open", "clock-wipe", "blinds", "cube", "flip"])
def test_single_sided_over_transparency(rig, typ):
    rc, _, A, B = rig
    np.testing.assert_allclose(run(rig, typ, 1.0, a=False).px, B.px, atol=2e-3)
    assert run(rig, typ, 0.0, a=False).px[..., 3].max() < 2e-3
    np.testing.assert_allclose(run(rig, typ, 0.0, b=False).px, A.px, atol=2e-3)
    assert run(rig, typ, 1.0, b=False).px[..., 3].max() < 2e-3


def test_dip_to_color_two_and_one_sided(rig):
    """D19: a to colour over the first half, colour to b over the second; a missing side is
    transparent (a fade in or out through the colour)."""
    rc, _, A, B = rig
    red = np.array([1.0, 0, 0, 1.0], np.float32)          # #FF0000 is 1.0 red in linear light too
    mid = run(rig, "dip-to-color", 0.5, color="#FF0000FF").px
    np.testing.assert_allclose(mid[40, 40], red, atol=1e-5)
    assert run(rig, "dip-to-color", 0.0, a=False, color="#FF0000FF").px[..., 3].max() == 0
    np.testing.assert_allclose(run(rig, "dip-to-color", 0.5, a=False, color="#FF0000FF").px[5, 5], red, atol=1e-5)
    np.testing.assert_allclose(run(rig, "dip-to-color", 1.0, a=False, color="#FF0000FF").px, B.px, atol=1e-5)
    np.testing.assert_allclose(run(rig, "dip-to-color", 0.5, b=False, color="#FF0000FF").px[5, 5], red, atol=1e-5)
    assert run(rig, "dip-to-color", 1.0, b=False, color="#FF0000FF").px[..., 3].max() == 0
    q = run(rig, "dip-to-color", 0.25).px                  # halfway to black
    np.testing.assert_allclose(q, A.px * 0.5 + np.array([0, 0, 0, 0.5], np.float32), atol=1e-5)


def test_d19_aliases_and_one_sided_moves(rig):
    """D19: iris is circle-open and slide is cover; cover with only a leaves a in place, reveal with
    only b shows b from the start."""
    rc, _, A, B = rig
    for p in (0.3, 0.7):
        np.testing.assert_array_equal(run(rig, "iris", p).px, run(rig, "circle-open", p).px)
        np.testing.assert_array_equal(run(rig, "slide", p, motionBlur="false").px,
                                      run(rig, "cover", p, motionBlur="false").px)
        np.testing.assert_allclose(run(rig, "cover", p, b=False, motionBlur="false").px, A.px, atol=1e-6)
        np.testing.assert_allclose(run(rig, "reveal", p, a=False, motionBlur="false").px, B.px, atol=1e-6)


def _share_of_b(px, A, B):
    return (px[..., 0] - A.px[..., 0]) / (B.px[..., 0] - A.px[..., 0])


@pytest.mark.parametrize("typ", ["wipe", "barn-door", "circle-open", "circle-close", "clock-wipe", "radial-wipe"])
def test_d19_masked_coordinates(rig, typ):
    """D19: each masked type shows b where s < e - w, a beyond e, with e = p (1 + w), and blends by
    smoothstep across [e - w, e]."""
    rc, _, A, B = rig
    H, W = rc.height, rc.width
    y, x = np.mgrid[0:H, 0:W] + 0.5
    cx, cy, R = W / 2, H / 2, 0.5 * np.hypot(W, H)
    s = {"wipe": 1 - x / W,                                        # direction left: from the right edge
         "barn-door": np.abs(x - cx) / (W / 2),
         "circle-open": np.hypot(x - cx, y - cy) / R,
         "circle-close": np.hypot(x - cx, y - cy) / R,
         "clock-wipe": np.mod(np.degrees(np.arctan2(y - cy, x - cx)) + 90, 360) / 360,
         "radial-wipe": np.mod(np.degrees(np.arctan2(y - cy, x - cx)) - 30, 360) / 360}[typ]
    w, p = 0.2, 0.4

    def share(pp, ss):
        e = pp * (1 + w)
        u = np.clip((ss - (e - w)) / w, 0, 1)
        return 1 - u * u * (3 - 2 * u)
    want = 1 - share(1 - p, s) if typ == "circle-close" else share(p, s)
    got = _share_of_b(run(rig, typ, p, softness=str(w), angle="30").px, A, B)
    np.testing.assert_allclose(got, want, atol=2e-4)


def test_luma_uses_working_space_luminance(rig):
    """D19: luma's s is the Rec. 709 luminance of the matte's working values (here 1 left, 0 right)."""
    rc, _, A, B = rig
    got = _share_of_b(run(rig, "luma", 0.5, softness="0").px, A, B)
    assert np.allclose(got[:, :80], 0) and np.allclose(got[:, 80:], 1)


def test_wipe_direction(rig):
    rc, _, A, B = rig
    left = run(rig, "wipe", 0.5, direction="left", softness="0").px      # edge travels right -> left
    assert np.allclose(left[45, 150], B.px[45, 150]) and np.allclose(left[45, 10], A.px[45, 10])
    right = run(rig, "wipe", 0.5, direction="right", softness="0").px
    assert np.allclose(right[45, 10], B.px[45, 10]) and np.allclose(right[45, 150], A.px[45, 150])
    down = run(rig, "wipe", 0.5, direction="down", softness="0").px
    assert np.allclose(down[5, 80], B.px[5, 80]) and np.allclose(down[85, 80], A.px[85, 80])
    ang = run(rig, "wipe", 0.5, direction="angle", angle="0", softness="0").px
    np.testing.assert_allclose(ang, right, atol=1e-6)


def test_softness_feathers_the_edge(rig):
    hard = run(rig, "wipe", 0.5, softness="0").px
    soft = run(rig, "wipe", 0.5, softness="0.5").px
    rc, _, A, B = rig
    frac = lambda px: np.mean(~(np.all(np.isclose(px, A.px, atol=1e-4), -1) | np.all(np.isclose(px, B.px, atol=1e-4), -1)))  # noqa: E731
    assert frac(soft) > frac(hard) + 0.2


def test_push_moves_both_pictures(rig):
    rc, _, A, B = rig
    res = run(rig, "push", 0.5, direction="left", motionBlur="false").px
    np.testing.assert_allclose(res[:, :80], A.px[:, 80:], atol=1e-5)
    np.testing.assert_allclose(res[:, 80:], B.px[:, :80], atol=1e-5)


def test_luma_uses_matte(rig):
    rc, _, A, B = rig
    res = run(rig, "luma", 0.5, softness="0").px
    np.testing.assert_allclose(res[45, 120], B.px[45, 120], atol=1e-4)   # dark matte half switches first
    np.testing.assert_allclose(res[45, 20], A.px[45, 20], atol=1e-4)


def test_glitch_varies_per_frame(rig):
    f1 = run(rig, "glitch", 0.5, t=1.5).px.copy()
    f2 = run(rig, "glitch", 0.5, t=1.5 + 1 / 24).px
    assert np.abs(f1 - f2).mean() > 1e-4


def test_compositor_path_and_shader_fallback(rig):
    rc, tr, A, B = rig
    for typ in ("cube", "shader", "page-curl"):
        tr.set("type", typ)
        px = rc.render_frame(2.0).px                       # mid-transition through the compositor
        assert px.shape == (rc.height, rc.width, 4)
        assert px[..., 3].min() > 0.99 or typ == "cube"
    tr.set("type", "crossfade")
