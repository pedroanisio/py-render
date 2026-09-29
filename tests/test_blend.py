"""Blend modes: W3C Compositing Level 1 reference values, alpha compositing, special operators."""
from __future__ import annotations

import numpy as np
import pytest

from scenerender import blend
from scenerender.raster import Buf
from scenerender.registry import BLENDS, FULL

SCHEMA_MODES = [
    "normal", "dissolve", "add", "plus-lighter", "multiply", "screen", "overlay", "difference",
    "exclusion", "subtract", "divide", "darken", "lighten", "darker-color", "lighter-color",
    "color-dodge", "color-burn", "linear-dodge", "linear-burn", "soft-light", "hard-light",
    "linear-light", "vivid-light", "pin-light", "hard-mix", "hue", "saturation", "color",
    "luminosity", "stencil-alpha", "stencil-luma", "silhouette-alpha", "silhouette-luma",
    "alpha-add", "behind",
]


def px(*rgba):
    """One premultiplied pixel from straight rgba."""
    r, g, b, a = rgba
    return np.array([[[r * a, g * a, b * a, a]]], np.float32)


def op(mode, dst, src):
    return BLENDS.get(mode)(dst.copy(), src.copy())


def test_all_schema_modes_registered_full():
    assert len(SCHEMA_MODES) == 35
    for m in SCHEMA_MODES:
        assert BLENDS.get(m) is not None, m
        assert BLENDS.level(m) == FULL, m


# cb = 0.25, cs = 0.6 (both opaque): values worked by hand from the W3C / Photoshop formulas.
SEPARABLE_REF = {
    "multiply": 0.15,
    "screen": 0.7,
    "overlay": 0.3,           # HardLight(cs, cb): cb <= .5 -> 2 cb cs
    "hard-light": 0.4,        # Screen(cb, 2cs - 1)
    "darken": 0.25,
    "lighten": 0.6,
    "color-dodge": 0.625,     # cb / (1 - cs)
    "color-burn": 0.0,        # 1 - min(1, (1 - cb) / cs)
    "soft-light": 0.3,        # cb + (2cs-1)(D(cb)-cb), D(.25) = .5
    "difference": 0.35,
    "exclusion": 0.55,
    "subtract": 0.0,
    "divide": 0.25 / 0.6,
    "linear-burn": 0.0,
    "linear-light": 0.45,
    "vivid-light": 0.3125,    # ColorDodge(cb, 2cs - 1)
    "pin-light": 0.25,
    "hard-mix": 0.0,
}


@pytest.mark.parametrize("mode,expected", sorted(SEPARABLE_REF.items()))
def test_separable_reference(mode, expected):
    res = op(mode, px(0.25, 0.25, 0.25, 1), px(0.6, 0.6, 0.6, 1))
    np.testing.assert_allclose(res[0, 0, :3], expected, atol=1e-5)
    assert res[0, 0, 3] == pytest.approx(1.0)


@pytest.mark.parametrize("mode,cb,cs,expected", [
    ("subtract", 0.6, 0.25, 0.35),
    ("color-burn", 0.8, 0.5, 0.6),        # 1 - .2/.5
    ("color-dodge", 0.0, 0.9, 0.0),
    ("color-dodge", 0.5, 1.0, 1.0),
    ("soft-light", 0.64, 0.8, 0.64 + 0.6 * (0.8 - 0.64)),   # D = sqrt(cb)
    ("soft-light", 0.5, 0.25, 0.5 - 0.5 * 0.5 * 0.5),
    ("overlay", 0.75, 0.5, 0.75),         # Screen(cs, 2cb - 1) = .5 + .5 - .25
    ("hard-mix", 0.5, 0.6, 1.0),
    ("vivid-light", 0.5, 0.25, 0.0),      # ColorBurn(.5, .5) = 1 - min(1, .5/.5)
    ("pin-light", 0.9, 0.3, 0.6),
    ("linear-light", 0.9, 0.9, 1.0),
    ("divide", 0.3, 0.0, 1.0),
])
def test_separable_edge_cases(mode, cb, cs, expected):
    res = op(mode, px(cb, cb, cb, 1), px(cs, cs, cs, 1))
    np.testing.assert_allclose(res[0, 0, :3], expected, atol=1e-5)


def test_nonseparable_reference():
    k = 0.5 + (0.2 - 0.5) * 0.5 / 0.7          # ClipColor of (1.2, .2, .2) with l = .5
    np.testing.assert_allclose(op("luminosity", px(1, 0, 0, 1), px(.5, .5, .5, 1))[0, 0, :3], [1, k, k], atol=1e-5)
    np.testing.assert_allclose(op("color", px(.5, .5, .5, 1), px(1, 0, 0, 1))[0, 0, :3], [1, k, k], atol=1e-5)
    lum_b = 0.3 * 0.2 + 0.59 * 0.4 + 0.11 * 0.6
    # Hue: SetLum(SetSat(cs, Sat(cb)), Lum(cb)) = (.4, 0, 0) shifted to lum .362
    d = lum_b - 0.3 * 0.4
    np.testing.assert_allclose(op("hue", px(.2, .4, .6, 1), px(1, 0, 0, 1))[0, 0, :3], [0.4 + d, d, d], atol=1e-5)
    np.testing.assert_allclose(op("saturation", px(.2, .4, .6, 1), px(.5, .5, .5, 1))[0, 0, :3], [lum_b] * 3, atol=1e-5)
    # Saturation with a saturated source keeps the backdrop's hue and luminosity.
    res = op("saturation", px(.2, .4, .6, 1), px(1, 0, 0, 1))[0, 0, :3]
    assert 0.3 * res[0] + 0.59 * res[1] + 0.11 * res[2] == pytest.approx(lum_b, abs=1e-5)
    assert res[2] > res[1] > res[0]
    np.testing.assert_allclose(op("darker-color", px(1, 0, 0, 1), px(0, 1, 0, 1))[0, 0, :3], [1, 0, 0])
    np.testing.assert_allclose(op("lighter-color", px(1, 0, 0, 1), px(0, 1, 0, 1))[0, 0, :3], [0, 1, 0])


def test_w3c_alpha_model():
    dst, src = px(0, 0, 0.5, 0.8), px(0.6, 0, 0, 0.5)
    # multiply: Cs(1-ab) + Cb(1-as) + as ab B = (.3*.2, 0, .4*.5); alpha .5 + .8*.5
    np.testing.assert_allclose(op("multiply", dst, src)[0, 0], [0.06, 0, 0.2, 0.9], atol=1e-6)
    # source over transparent backdrop is the source for every W3C mode
    for m in list(blend.BLEND_FUNCS):
        np.testing.assert_allclose(op(m, px(0, 0, 0, 0), src)[0, 0], src[0, 0], atol=1e-6, err_msg=m)
        np.testing.assert_allclose(op(m, dst, px(0, 0, 0, 0))[0, 0], dst[0, 0], atol=1e-6, err_msg=m)


def test_normal_equals_over():
    rng = np.random.default_rng(1)
    a = rng.random((8, 8, 1), np.float32)
    s = np.concatenate([rng.random((8, 8, 3), np.float32) * a, a], -1)
    b = rng.random((8, 8, 1), np.float32)
    d = np.concatenate([rng.random((8, 8, 3), np.float32) * b, b], -1)
    np.testing.assert_allclose(op("normal", d, s), s + d * (1 - s[..., 3:4]), atol=1e-6)
    # the W3C model with B(cb, cs) = cs reduces to source-over
    np.testing.assert_allclose(blend._separable(lambda cb, cs: cs)(d, s), s + d * (1 - s[..., 3:4]), atol=1e-5)


def test_add_family_clamps():
    for m in ("add", "linear-dodge", "plus-lighter"):
        res = op(m, px(0.7, 0.2, 0.1, 1), px(0.6, 0.3, 0.1, 1))[0, 0]
        np.testing.assert_allclose(res, [1.0, 0.5, 0.2, 1.0], atol=1e-6)
        res = op(m, px(0.5, 0.5, 0.5, 0.4), px(0.5, 0.5, 0.5, 0.4))[0, 0]
        np.testing.assert_allclose(res, [0.4, 0.4, 0.4, 0.8], atol=1e-6)


def test_alpha_add_and_behind():
    # complementary 50% edges of the same colour join seamlessly
    res = op("alpha-add", px(0.2, 0.4, 0.6, 0.5), px(0.2, 0.4, 0.6, 0.5))[0, 0]
    np.testing.assert_allclose(res, [0.2, 0.4, 0.6, 1.0], atol=1e-6)
    dst, src = px(1, 0, 0, 0.5), px(0, 0, 1, 1)
    np.testing.assert_allclose(op("behind", dst, src)[0, 0], [0.5, 0, 0.5, 1.0], atol=1e-6)
    np.testing.assert_allclose(op("behind", px(1, 0, 0, 1), src)[0, 0], [1, 0, 0, 1], atol=1e-6)


def test_dissolve_is_stable_and_proportional():
    dst = Buf(np.zeros((64, 64, 4), np.float32), 0, 0)
    src = Buf(np.tile(px(1, 1, 1, 0.3), (64, 64, 1)), 0, 0)
    r1 = blend.composite(dst.copy(), src, "dissolve").px
    r2 = blend.composite(dst.copy(), src, "dissolve").px
    np.testing.assert_array_equal(r1, r2)
    cov = r1[..., 3]
    assert set(np.unique(cov)) <= {0.0, 1.0}
    assert 0.25 < cov.mean() < 0.35
    # the pattern belongs to frame coordinates, not to the tile layout
    part = Buf(src.px[16:48, 16:48].copy(), 16, 16)
    r3 = blend.composite(Buf(np.zeros((64, 64, 4), np.float32), 0, 0), part, "dissolve").px
    np.testing.assert_array_equal(r3[16:48, 16:48], r1[16:48, 16:48])


@pytest.mark.parametrize("mode", ["stencil-alpha", "stencil-luma", "silhouette-alpha", "silhouette-luma"])
def test_stencil_silhouette_cut_whole_parent(mode):
    base = np.tile(px(0.2, 0.4, 0.6, 1), (40, 40, 1))
    dst = Buf(base.copy(), 0, 0)
    src = Buf(np.tile(px(1, 1, 1, 1), (10, 10, 1)), 10, 10)       # white opaque square
    res = blend.composite(dst, src, mode)
    assert res.rect == (0, 0, 40, 40)
    inside, outside = res.px[15, 15], res.px[2, 2]
    if mode.startswith("stencil"):
        np.testing.assert_allclose(inside, base[15, 15], atol=1e-6)
        np.testing.assert_allclose(outside, 0, atol=1e-6)            # cut outside the stencil
    else:
        np.testing.assert_allclose(inside, 0, atol=1e-6)
        np.testing.assert_allclose(outside, base[2, 2], atol=1e-6)


def test_stencil_luma_uses_luminance():
    dst = Buf(np.tile(px(1, 1, 1, 1), (4, 4, 1)), 0, 0)
    src = Buf(np.tile(px(0.5, 0.5, 0.5, 1), (4, 4, 1)), 0, 0)
    np.testing.assert_allclose(blend.composite(dst.copy(), src, "stencil-luma").px[0, 0], [0.5] * 4, atol=1e-6)
    np.testing.assert_allclose(blend.composite(dst.copy(), src, "silhouette-luma").px[0, 0], [0.5] * 4, atol=1e-6)
    np.testing.assert_allclose(blend.composite(dst.copy(), src, "stencil-alpha").px[0, 0], [1] * 4, atol=1e-6)


def test_composite_overlap_grow_and_opacity():
    dst = Buf(np.tile(px(1, 0, 0, 1), (10, 10, 1)), 0, 0)
    src = Buf(np.tile(px(0, 0, 1, 1), (4, 4, 1)), 8, 8)
    res = blend.composite(dst, src, "multiply")
    assert res.rect == (0, 0, 12, 12)
    np.testing.assert_allclose(res.px[9, 9], [0, 0, 0, 1])           # red x blue
    np.testing.assert_allclose(res.px[11, 11], [0, 0, 1, 1])         # source over nothing
    np.testing.assert_allclose(res.px[0, 0], [1, 0, 0, 1])           # untouched
    dst = Buf(np.tile(px(1, 0, 0, 1), (4, 4, 1)), 0, 0)
    res = blend.composite(dst, Buf(np.tile(px(0, 0, 1, 1), (4, 4, 1)), 0, 0), "normal", 0.25)
    np.testing.assert_allclose(res.px[0, 0], [0.75, 0, 0.25, 1], atol=1e-6)
    no_grow = blend.composite(Buf(np.zeros((4, 4, 4), np.float32), 0, 0), src, "normal", grow=False)
    assert no_grow.rect == (0, 0, 4, 4)


def test_unknown_mode_falls_back_to_normal():
    dst = Buf(np.tile(px(1, 0, 0, 1), (2, 2, 1)), 0, 0)
    res = blend.composite(dst, Buf(np.tile(px(0, 1, 0, 0.5), (2, 2, 1)), 0, 0), "no-such-mode")
    np.testing.assert_allclose(res.px[0, 0], [0.5, 0.5, 0, 1], atol=1e-6)


def test_results_stay_valid_premultiplied():
    rng = np.random.default_rng(3)
    a = rng.random((16, 16, 1), np.float32)
    s = np.concatenate([rng.random((16, 16, 3), np.float32) * a, a], -1)
    b = rng.random((16, 16, 1), np.float32)
    d = np.concatenate([rng.random((16, 16, 3), np.float32) * b, b], -1)
    for m in SCHEMA_MODES:
        fn = BLENDS.get(m)
        res = fn(d.copy(), s.copy(), origin=(0, 0)) if getattr(fn, "with_origin", False) else fn(d.copy(), s.copy())
        assert res.shape == d.shape and res.dtype == np.float32, m
        assert np.all(np.isfinite(res)), m
        assert np.all(res[..., 3] <= 1 + 1e-5) and np.all(res[..., 3] >= -1e-6), m
        assert np.all(res[..., :3] <= res[..., 3:4] + 1e-4), m


def test_dissolve_hash_is_d14():
    """D14: a stateless hash of (x, y, scene seed), as the C renderer's dissolve_noise computes it."""
    xs, ys = np.array([0, 5, -3, 1919]), np.array([0, 7, 2, 1079])
    np.testing.assert_allclose(blend._hash01(xs, ys, 42), [0.504035532, 0.65266186, 0.462154925, 0.571911156],
                               rtol=0, atol=1e-8)
