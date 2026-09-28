"""Color-managed, premultiplied float image filtering and drawing."""
from __future__ import annotations

import math
import os
from collections import OrderedDict

import cairo
import numpy as np

from .. import color
from ..raster import Buf
from ..registry import warn_once
from .image_io import decode


class FloatMips:
    def __init__(self, px):
        from PIL import Image
        self.h, self.w = px.shape[:2]
        self.levels = [np.ascontiguousarray(px, np.float32)]
        while max(px.shape[:2]) > 1:
            h, w = px.shape[:2]
            size = (max(1, w // 2), max(1, h // 2))
            # Filter premultiplied working samples, never encoded straight RGB.
            px = np.stack([np.asarray(Image.fromarray(px[..., c]).resize(size, Image.Resampling.BOX))
                           for c in range(4)], -1)
            self.levels.append(px)
        self.nbytes = sum(p.nbytes for p in self.levels)

    def exponent(self, px) -> int:
        """Power of two that brings px's RGB within [-1, 1] (levels are never modified: kept)."""
        memo = self.__dict__.setdefault("_exponents", {})
        e = memo.get(id(px))
        if e is None:
            peak = max(1., float(np.max(np.abs(px[..., :3]))))
            e = memo[id(px)] = math.ceil(math.log2(peak)) if math.isfinite(peak) else 0
        return e

    def pick(self, scale):
        level = max(0, int(math.floor(math.log2(1 / scale)))) if 0 < scale < 1 else 0
        return self.levels[min(level, len(self.levels) - 1)]


def input_pixels(rc, decoded, cs, tf, alpha):
    rgba = decoded.rgba
    a = np.clip(rgba[..., 3:4], 0, 1)
    rgb = rgba[..., :3]
    alpha = decoded.alpha if alpha == "auto" else alpha
    tf = color.SPACES.get(cs, (None, "srgb"))[1] if tf in (None, "auto") else tf
    if alpha == "none":
        a = np.ones_like(a)
    premul_linear = alpha == "premultiplied" and tf == "linear" and rc.linear
    if alpha == "premultiplied" and not premul_linear:
        rgb = np.divide(rgb, a, out=np.zeros_like(rgb), where=a > 0)
    rgb = color.to_working(rgb, cs, tf, color.working_primaries(rc))
    if not rc.linear:
        rgb = color.encode(rgb, "srgb")
    if not premul_linear:
        rgb = rgb * a
    # Linear EXR RGB with A=0 can encode emission, including negative samples.
    return np.concatenate((rgb, a), -1).astype(np.float32)


def load_pixels(rc, asset, ctx, path):
    from .video import color_params
    cs, tf = color_params(rc, asset, ctx)
    selector = rc.ev.str(asset, "layer", ctx)
    alpha = rc.ev.str(asset, "alpha", ctx, "auto")
    try:
        st = os.stat(path)
    except OSError as e:
        warn_once("asset-file", path, str(e))
        return None
    key = (path, st.st_size, st.st_mtime_ns, selector, cs, tf, alpha, rc.linear, color.working_primaries(rc))
    cache = rc.cache.setdefault("image-float-cache", OrderedDict())
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    try:
        hit = FloatMips(input_pixels(rc, decode(path, selector), cs, tf, alpha))
    except Exception as e:  # Any unavailable decoder or malformed media is reported, not substituted.
        warn_once("asset-file", f"{path}:{selector or ''}", f"cannot decode: {e}")
        hit = None
    cache[key] = hit
    size = sum(m.nbytes for m in cache.values() if m is not None)
    while len(cache) > 1 and (size > 256 * 1024 * 1024 or len(cache) > 256):
        _, old = cache.popitem(last=False)
        size -= old.nbytes if old is not None else 0
    return hit


def _draw_translated(r, px, M, aw, ah, clip):
    """Unscaled pixels moved by whole pixels are only placed: every output pixel centre falls on a
    source pixel centre, so Cairo's filter reads that one sample (and the clip covers whole pixels)."""
    h, w = px.shape[:2]
    tx, ty = float(M[0, 2]), float(M[1, 2])
    x0, y0, x1, y1 = clip if clip is not None else (0, 0, aw, ah)
    if (M[0, 0] != 1 or M[1, 1] != 1 or M[0, 1] != 0 or M[1, 0] != 0 or tuple(M[2]) != (0, 0, 1)
            or aw != w or ah != h or not all(float(v).is_integer() for v in (tx, ty, x0, y0, x1, y1))):
        return None
    tx, ty = int(tx), int(ty)
    # Source columns/rows that are inside the clip, the source and the output rectangle.
    u0, u1 = max(0, int(x0), r[0] - tx), min(w, int(x1), r[2] - tx)
    v0, v1 = max(0, int(y0), r[1] - ty), min(h, int(y1), r[3] - ty)
    if not (u0 < u1 and v0 < v1):
        return Buf(np.zeros((r[3] - r[1], r[2] - r[0], 4), np.float32), r[0], r[1])
    out = np.empty((r[3] - r[1], r[2] - r[0], 4), np.float32)
    top, bottom, left, right = v0 + ty - r[1], v1 + ty - r[1], u0 + tx - r[0], u1 + tx - r[0]
    out[:top] = 0
    out[bottom:] = 0
    out[top:bottom, :left] = 0
    out[top:bottom, right:] = 0
    out[top:bottom, left:right] = px[v0:v1, u0:u1]
    return Buf(out, r[0], r[1])


def draw_pixels(rc, mips, M, aw, ah, clip=None):
    if aw <= 0 or ah <= 0:
        return None
    c = rc.canvas_for(M, aw, ah, 2)
    if c is None:
        return None
    # Largest footprint axis controls mip selection; Cairo filters the remaining
    # anisotropy. Keep magnified directions sharp instead of selecting by area.
    transform = M[:2, :2] @ np.diag([aw / mips.w, ah / mips.h])
    scale = float(np.linalg.svd(transform, compute_uv=False)[0])
    px = mips.pick(scale)
    placed = _draw_translated(c.rect, px, M, aw, ah, clip)
    if placed is not None:
        return placed
    # Cairo's output is a function of these inputs: a held or on-twos image redraws identically.
    key = (id(px), c.rect, np.asarray(M, np.float64).tobytes(), float(aw), float(ah), clip)
    memo = mips.__dict__.setdefault("_drawn", {})
    hit = memo.get(key)
    if hit is not None:
        return Buf(hit.copy(), c.rect[0], c.rect[1])
    out = _draw_cairo(c, px, mips, M, aw, ah, clip)
    memo.clear()      # the latest draw only: consecutive frames are what repeat
    memo[key] = out.px.copy()
    return out


def _draw_cairo(c, px, mips, M, aw, ah, clip):
    h, w = px.shape[:2]
    # Cairo/pixman saturates float components at 1. Scale RGB alone (alpha stays
    # coverage), then undo the scale after affine filtering and antialiased clipping.
    exponent = mips.exponent(px)
    if exponent or not (px.flags.c_contiguous and px.flags.writeable):
        samples = px.copy()
        samples[..., :3] = np.ldexp(samples[..., :3], -exponent)
    else:
        samples = px     # Cairo only reads the source surface
    source = cairo.ImageSurface.create_for_data(memoryview(samples).cast("B"), cairo.FORMAT_RGBA128F, w, h)
    r = c.rect
    dest = cairo.ImageSurface(cairo.FORMAT_RGBA128F, r[2] - r[0], r[3] - r[1])
    cr = cairo.Context(dest)
    cr.translate(-r[0], -r[1])
    cr.transform(cairo.Matrix(M[0, 0], M[1, 0], M[0, 1], M[1, 1], M[0, 2], M[1, 2]))
    x0, y0, x1, y1 = clip if clip is not None else (0, 0, aw, ah)
    cr.rectangle(x0, y0, x1 - x0, y1 - y0)
    cr.clip()
    cr.scale(aw / w, ah / h)
    cr.set_source_surface(source)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.get_source().set_extend(cairo.EXTEND_PAD)
    cr.paint()
    dest.flush()
    out = np.frombuffer(dest.get_data(), np.float32).reshape(dest.get_height(), dest.get_stride() // 16, 4)
    out = out[:, :dest.get_width()].copy()
    if exponent:
        out[..., :3] = np.ldexp(out[..., :3], exponent)
    return Buf(out, r[0], r[1])
