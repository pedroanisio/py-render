"""Compositing of premultiplied tiles with blend modes.

Separable and non-separable modes follow the W3C Compositing and Blending
Level 1 model: result = Cs*(1-ab) + Cb*(1-as) + as*ab*B(cb, cs), computed on
straight colours, with source-over alpha. Modes register a B function
(straight cb, cs -> colour) in BLEND_FUNCS, or a whole-pixel operator in
BLENDS for modes that do not fit the model (add, dissolve, stencils, behind, ...).

Operators take premultiplied (dst, src) arrays of equal shape and return the
result. Two optional attributes change how composite() calls them:
  * ``whole_tile = True``: the operator gets the whole destination tile and the
    source resampled onto it (stencil/silhouette modes cut through everything
    below within the isolated parent, not just under the source).
  * ``with_origin = True``: the operator also gets ``origin=(x0, y0)``, the frame
    coordinates of the arrays' top-left pixel (dissolve's stable noise).

All arithmetic happens in the working space. With linearLight (the default)
the modes see linear-light values, so results differ from sRGB-space tools
exactly as After Effects' "linear working space" does; that is intended.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from .raster import Buf, intersect
from .registry import BLENDS, FULL, warn_once

BlendFunc = Callable[[np.ndarray, np.ndarray], np.ndarray]
BLEND_FUNCS: dict[str, BlendFunc] = {}

_EPS = 1e-6


def blend_func(*names: str):
    """Register a W3C-model blend function B(cb, cs) on straight colours for each name."""
    def deco(fn: BlendFunc):
        for n in names:
            BLEND_FUNCS[n] = fn
            BLENDS.register(n, level=FULL)(_separable(fn))
        return fn
    return deco


def _unpremul(px: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = px[..., 3:4]
    rgb = np.zeros(px.shape[:-1] + (3,), np.float32)
    np.divide(px[..., :3], a, out=rgb, where=a > _EPS, casting="same_kind")
    return rgb, a


def _separable(fn: BlendFunc):
    def op(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
        cs, sa = _unpremul(src)
        cb, da = _unpremul(dst)
        out = np.empty_like(dst)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            b = fn(cb, cs)
        out[..., :3] = src[..., :3] * (1 - da) + dst[..., :3] * (1 - sa) + sa * da * b
        out[..., 3:4] = sa + da * (1 - sa)
        return out
    return op


# ---------------------------------------------------------------- Porter-Duff style operators
@BLENDS.register("normal", level=FULL)
def _normal(dst, src):
    return src + dst * (1 - src[..., 3:4])


@BLENDS.register("add", "linear-dodge", "plus-lighter", level=FULL)
def _add(dst, src):
    """Premultiplied sum, clamped to 1 (W3C plus-lighter); stays a valid premultiplied colour."""
    return np.minimum(dst + src, 1.0)


@BLENDS.register("alpha-add", level=FULL)
def _alpha_add(dst, src):
    """Colours composite as normal, alpha channels add (seamless edges of complementary mattes)."""
    over = _normal(dst, src)
    a = np.minimum(src[..., 3:4] + dst[..., 3:4], 1.0)
    out = np.empty_like(dst)
    oa = over[..., 3:4]
    out[..., :3] = np.where(oa > _EPS, over[..., :3] / np.maximum(oa, _EPS), 0.0) * a
    out[..., 3:4] = a
    return out


@BLENDS.register("behind", level=FULL)
def _behind(dst, src):
    """The source is drawn beneath the destination (destination-over)."""
    return dst + src * (1 - dst[..., 3:4])


def _hash01(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Stable per-pixel noise in [0, 1) from integer frame coordinates."""
    h = (x.astype(np.uint32) * np.uint32(0x8DA6B343)) ^ (y.astype(np.uint32) * np.uint32(0xD8163841))
    h ^= h >> np.uint32(13)
    h *= np.uint32(0x5BD1E995)
    h ^= h >> np.uint32(15)
    return (h >> np.uint32(8)).astype(np.float32) / np.float32(1 << 24)


def _dissolve(dst, src, origin=(0, 0)):
    """Each pixel shows the source at full strength when noise < source alpha, else the backdrop."""
    h, w = src.shape[:2]
    ys, xs = np.mgrid[origin[1]:origin[1] + h, origin[0]:origin[0] + w]
    xs = xs + (1 << 20)   # keep coordinates positive for the unsigned hash
    ys = ys + (1 << 20)
    sa = src[..., 3:4]
    keep = (_hash01(xs, ys)[..., None] < sa) & (sa > _EPS)
    cs = np.where(sa > _EPS, src / np.maximum(sa, _EPS), 0.0)   # straight colour with alpha 1
    return np.where(keep, cs, dst).astype(np.float32)


_dissolve.with_origin = True
BLENDS.register("dissolve", level=FULL, note="stable per-pixel hash noise thresholded by source alpha")(_dissolve)


def _stencil_factor(src, mode: str) -> np.ndarray:
    if mode.endswith("alpha"):
        f = src[..., 3:4]
    else:
        f = luma(src[..., :3])[..., None]           # premultiplied luma = straight luma * alpha
    return np.clip(f, 0.0, 1.0)


def _stencil_op(mode: str):
    silhouette = mode.startswith("silhouette")

    def op(dst, src):
        f = _stencil_factor(src, mode)
        return dst * ((1 - f) if silhouette else f)
    op.whole_tile = True
    op.__name__ = f"_{mode.replace('-', '_')}"
    return op


for _m in ("stencil-alpha", "stencil-luma", "silhouette-alpha", "silhouette-luma"):
    BLENDS.register(_m, level=FULL, note="cuts through the whole isolated parent buffer")(_stencil_op(_m))


# ---------------------------------------------------------------- separable B(cb, cs)
@blend_func("multiply")
def _multiply(cb, cs):
    return cb * cs


@blend_func("screen")
def _screen(cb, cs):
    return cb + cs - cb * cs


def _hard_light_fn(cb, cs):
    return np.where(cs <= 0.5, cb * 2 * cs, _screen(cb, 2 * cs - 1))


@blend_func("hard-light")
def _hard_light(cb, cs):
    return _hard_light_fn(cb, cs)


@blend_func("overlay")
def _overlay(cb, cs):
    return _hard_light_fn(cs, cb)


@blend_func("darken")
def _darken(cb, cs):
    return np.minimum(cb, cs)


@blend_func("lighten")
def _lighten(cb, cs):
    return np.maximum(cb, cs)


def _dodge(cb, cs):
    return np.where(cb <= 0, 0.0, np.where(cs >= 1, 1.0, np.minimum(1.0, cb / np.maximum(1 - cs, _EPS))))


def _burn(cb, cs):
    return np.where(cb >= 1, 1.0, np.where(cs <= 0, 0.0, 1 - np.minimum(1.0, (1 - cb) / np.maximum(cs, _EPS))))


@blend_func("color-dodge")
def _color_dodge(cb, cs):
    return _dodge(cb, cs)


@blend_func("color-burn")
def _color_burn(cb, cs):
    return _burn(cb, cs)


@blend_func("soft-light")
def _soft_light(cb, cs):
    d = np.where(cb <= 0.25, ((16 * cb - 12) * cb + 4) * cb, np.sqrt(np.maximum(cb, 0)))
    return np.where(cs <= 0.5, cb - (1 - 2 * cs) * cb * (1 - cb), cb + (2 * cs - 1) * (d - cb))


@blend_func("difference")
def _difference(cb, cs):
    return np.abs(cb - cs)


@blend_func("exclusion")
def _exclusion(cb, cs):
    return cb + cs - 2 * cb * cs


@blend_func("subtract")
def _subtract(cb, cs):
    return np.maximum(cb - cs, 0.0)


@blend_func("divide")
def _divide(cb, cs):
    return np.where(cs <= _EPS, np.where(cb > 0, 1.0, 0.0), np.minimum(cb / np.maximum(cs, _EPS), 1.0))


@blend_func("linear-burn")
def _linear_burn(cb, cs):
    return np.maximum(cb + cs - 1, 0.0)


@blend_func("linear-light")
def _linear_light(cb, cs):
    return np.clip(cb + 2 * cs - 1, 0.0, 1.0)


@blend_func("vivid-light")
def _vivid_light(cb, cs):
    return np.where(cs <= 0.5, _burn(cb, 2 * cs), _dodge(cb, 2 * cs - 1))


@blend_func("pin-light")
def _pin_light(cb, cs):
    return np.where(cs <= 0.5, np.minimum(cb, 2 * cs), np.maximum(cb, 2 * cs - 1))


@blend_func("hard-mix")
def _hard_mix(cb, cs):
    return np.where(cb + cs >= 1 - 1e-6, 1.0, 0.0)


# ---------------------------------------------------------------- non-separable (W3C)
def lum(c: np.ndarray) -> np.ndarray:
    return 0.3 * c[..., 0:1] + 0.59 * c[..., 1:2] + 0.11 * c[..., 2:3]


def luma(c: np.ndarray) -> np.ndarray:
    """Rec. 709 luma of an (..., 3) array (used by the luma stencils)."""
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]


def _clip_color(c):
    lm = lum(c)
    n = c.min(axis=-1, keepdims=True)
    x = c.max(axis=-1, keepdims=True)
    c = np.where(n < 0, lm + (c - lm) * lm / np.maximum(lm - n, _EPS), c)
    c = np.where(x > 1, lm + (c - lm) * (1 - lm) / np.maximum(x - lm, _EPS), c)
    return c


def _set_lum(c, lm):
    return _clip_color(c + (lm - lum(c)))


def _sat(c):
    return c.max(axis=-1, keepdims=True) - c.min(axis=-1, keepdims=True)


def _set_sat(c, s):
    cmax = c.max(axis=-1, keepdims=True)
    cmin = c.min(axis=-1, keepdims=True)
    rng = cmax - cmin
    out = np.where(rng > _EPS, (c - cmin) * s / np.maximum(rng, _EPS), 0.0)
    return out


@blend_func("hue")
def _hue(cb, cs):
    return _set_lum(_set_sat(cs, _sat(cb)), lum(cb))


@blend_func("saturation")
def _saturation(cb, cs):
    return _set_lum(_set_sat(cb, _sat(cs)), lum(cb))


@blend_func("color")
def _color(cb, cs):
    return _set_lum(cs, lum(cb))


@blend_func("luminosity")
def _luminosity(cb, cs):
    return _set_lum(cb, lum(cs))


@blend_func("darker-color")
def _darker_color(cb, cs):
    return np.where(lum(cs) < lum(cb), cs, cb)


@blend_func("lighter-color")
def _lighter_color(cb, cs):
    return np.where(lum(cs) > lum(cb), cs, cb)


# ---------------------------------------------------------------- compositing
def composite(dst: Buf, src: Buf, mode: str = "normal", opacity: float = 1.0, grow: bool = True) -> Buf:
    """Blend src onto dst; returns dst (grown to cover src when grow is True)."""
    if opacity <= 0:
        return dst
    op = BLENDS.get(mode)
    if op is None:
        warn_once("blend", mode)
        op = _normal
    if getattr(op, "whole_tile", False):
        # Stencil/silhouette modes affect the whole backdrop, not just the overlap.
        s = src.region(dst.rect)
        if opacity < 1:
            s *= opacity
        dst.px[:] = op(dst.px, s)
        return dst
    if grow:
        dst = dst.expand_to(src.rect)
    r = intersect(dst.rect, src.rect)
    if r is None:
        return dst
    s = src.px[r[1] - src.y0:r[3] - src.y0, r[0] - src.x0:r[2] - src.x0]
    if opacity < 1:
        s = s * opacity
    d = dst.px[r[1] - dst.y0:r[3] - dst.y0, r[0] - dst.x0:r[2] - dst.x0]
    if op is _normal and not np.may_share_memory(d, s):
        # In-place source-over: the same arithmetic as _normal without full-tile temporaries;
        # a fully opaque source simply replaces a finite backdrop.
        if (s[..., 3] == 1).all() and np.isfinite(d).all():
            d[:] = s
        else:
            d *= 1 - s[..., 3:4]
            d += s
    elif getattr(op, "with_origin", False):
        d[:] = op(d, s, origin=(r[0], r[1]))
    else:
        d[:] = op(d, s)
    return dst
