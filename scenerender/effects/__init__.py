"""Effect handlers. Shared numeric helpers live here.

Effect contract: fn(rc, effect_el, buf, ctx, node) -> Buf. Buffers are
premultiplied float32 RGBA in the working space; distances in effect
attributes are document pixels, multiplied by rc.scale for the output.
Handlers may return a larger tile (e.g. blur or shadow spill) and must
not mutate buf.px in place unless they return buf.
"""
from __future__ import annotations

import math

import numpy as np

from ..raster import Buf, color_to_working, linear_to_srgb, srgb_to_linear
from ..registry import warn_once


def _kernel(sigma: float) -> np.ndarray:
    r = max(1, int(math.ceil(sigma * 3)))
    x = np.arange(-r, r + 1, dtype=np.float32)
    k = np.exp(-(x * x) / (2 * sigma * sigma))
    return (k / k.sum()).astype(np.float32)


def _blur_axis(a: np.ndarray, sigma: float, axis: int) -> np.ndarray:
    if sigma < 0.3:
        return a
    if sigma > 4:
        # Three box blurs approximate a Gaussian at large radii (much cheaper than a wide kernel).
        w = int(math.sqrt(12 * sigma * sigma / 3 + 1))
        w += (w + 1) % 2
        for _ in range(3):
            a = _box_axis(a, w, axis)
        return a
    k = _kernel(sigma)
    r = len(k) // 2
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r, r)
    p = np.pad(a, pad, mode="constant")
    out = np.zeros_like(a)
    n = a.shape[axis]
    for i, kv in enumerate(k):
        sl = [slice(None)] * a.ndim
        sl[axis] = slice(i, i + n)
        out += p[tuple(sl)] * kv
    return out


def _box_axis(a: np.ndarray, w: int, axis: int) -> np.ndarray:
    r = w // 2
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="constant"), axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = [slice(None)] * a.ndim
    lo = [slice(None)] * a.ndim
    hi[axis] = slice(w, w + n)
    lo[axis] = slice(0, n)
    return ((c[tuple(hi)] - c[tuple(lo)]) / w).astype(np.float32)


def gaussian(a: np.ndarray, sigma: float, sigma_y: float | None = None) -> np.ndarray:
    """Separable Gaussian blur of an (h, w, c) array; edges treated as transparent."""
    sy = sigma if sigma_y is None else sigma_y
    return _blur_axis(_blur_axis(a, sigma, 1), sy, 0)


def luma(px: np.ndarray) -> np.ndarray:
    return 0.2126 * px[..., 0] + 0.7152 * px[..., 1] + 0.0722 * px[..., 2]


def straight(px: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = px[..., 3:4]
    return np.where(a > 1e-6, px[..., :3] / np.maximum(a, 1e-6), 0), a


def premul(rgb: np.ndarray, a: np.ndarray) -> np.ndarray:
    return np.concatenate([rgb * a, a], -1).astype(np.float32)


class Params:
    """Short, animation-aware accessors; defaults are supplied at each call site."""

    def __init__(self, rc, el, ctx):
        self.rc, self.el, self.ctx = rc, el, ctx

    def n(self, name, default=0.0):
        return self.rc.ev.num(self.el, name, self.ctx, default)

    def d(self, name, default=0.0):
        return self.n(name, default) * self.rc.scale

    def s(self, name, default=None):
        return self.rc.ev.str(self.el, name, self.ctx, default)

    def color(self, name="color", default=(1, 1, 1, 1)):
        return np.asarray(color_to_working(self.rc.ev.color(self.el, name, self.ctx, default),
                                          self.rc.linear), np.float32)

    def vec(self, name, default):
        try:
            v = np.asarray([float(x) for x in self.s(name, str(default)).replace(",", " ").split()], np.float32)
            if v.size == 1:
                v = np.repeat(v, 3)
            if v.size == 3 and np.isfinite(v).all():
                return v
        except (ValueError, TypeError):
            pass
        warn_once("effect-value", name, "expected a scalar or three comma-separated numbers")
        return np.full(3, default, np.float32)

    def rng(self, temporal=True):
        extra = f"{self.n('seed', 0)}:{self.ctx.frame if temporal else ''}"
        return np.random.default_rng(self.rc.ev.seed_for(self.el, extra))

    def param(self, name, default=0.0):
        """Named param default, overridden/animated by a property on the effect.

        The XSD allows animation of arbitrary property names, but param children
        themselves have no animation children. Both paths use the evaluator.
        """
        value = default
        for child in self.el:
            if child.tag == "param" and self.rc.ev.str(child, "name", self.ctx) == name:
                value = self.rc.ev.str(child, "value", self.ctx, str(default))
        if isinstance(default, str):
            return self.rc.ev.str(self.el, name, self.ctx, value)
        try:
            value = float(value)
        except (ValueError, TypeError):
            value = default
        return self.rc.ev.num(self.el, name, self.ctx, value)


def display_rgb(rc, px):
    """Straight, display-referred sRGB colour of a working-space tile (for display-referred operations)."""
    from ..raster import from_working_primaries
    rgb, a = straight(px)
    return (linear_to_srgb(from_working_primaries(rgb)) if rc.linear else rgb), a


def from_display(rc, buf, rgb, a):
    from ..raster import to_working_primaries
    rgb = np.clip(rgb, 0, 1)
    return Buf(premul(to_working_primaries(srgb_to_linear(rgb)) if rc.linear else rgb, np.clip(a, 0, 1)), buf.x0, buf.y0)


def result(buf, px):
    return Buf(np.asarray(px, np.float32), buf.x0, buf.y0)


def linear_pixels(rc, px):
    if rc.linear:
        return px
    rgb, a = straight(px)
    return premul(srgb_to_linear(rgb), a)


def working_pixels(rc, px):
    if rc.linear:
        return px
    rgb, a = straight(px)
    return premul(linear_to_srgb(rgb), a)


def grid(buf):
    return np.meshgrid(np.arange(buf.w, dtype=np.float32), np.arange(buf.h, dtype=np.float32))


def center(p, buf):
    """Optional centres are frame-space document pixels; omitted means tile centre."""
    return (p.d("centerX", (buf.x0 + (buf.w - 1) / 2) / p.rc.scale) - buf.x0,
            p.d("centerY", (buf.y0 + (buf.h - 1) / 2) / p.rc.scale) - buf.y0)


def sample(px, x, y, mode="transparent"):
    """Bilinear sampling, preserving premultiplication, including at tile edges."""
    h, w = px.shape[:2]
    if mode == "wrap":
        x, y = np.mod(x, w), np.mod(y, h)
    elif mode == "clamp":
        x, y = np.clip(x, 0, w - 1), np.clip(y, 0, h - 1)
    x, y = np.asarray(x, np.float32), np.asarray(y, np.float32)
    x0, y0 = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
    fx, fy = x - x0.astype(np.float32), y - y0.astype(np.float32)
    out = np.zeros(np.broadcast_shapes(np.shape(x), np.shape(y)) + (px.shape[-1],), np.float32)
    for dx, dy, weight in ((0, 0, (1-fx)*(1-fy)), (1, 0, fx*(1-fy)),
                           (0, 1, (1-fx)*fy), (1, 1, fx*fy)):
        ix, iy = x0 + dx, y0 + dy
        if mode == "wrap":
            v = px[iy % h, ix % w]
        else:
            v = px[np.clip(iy, 0, h-1), np.clip(ix, 0, w-1)]
            if mode == "transparent":
                weight = weight * ((ix >= 0) & (ix < w) & (iy >= 0) & (iy < h))
        out += v * weight[..., None]
    return out


def affine_sample(px, matrix, shape=None):
    """Native Pillow float bilinear affine sampling, with transparent padding.

    matrix maps output pixel indices to input pixel indices (not pixel centres).
    Each float channel remains unquantized; this is the same premultiplied
    bilinear model as sample, but avoids four full-frame NumPy gathers per tap.
    """
    return affine_sampler(px)(matrix, shape)


def affine_sampler(px):
    """Prepare immutable float channels once for a multi-tap affine integral."""
    from PIL import Image
    channels = [Image.fromarray(np.pad(px[...,c],1)) for c in range(px.shape[-1])]

    def warp(matrix, shape=None):
        h, w = px.shape[:2] if shape is None else shape
        m = np.asarray(matrix, np.float64)[:2].copy()
        m[:, 2] += 1.5-m[:, :2] @ np.array([.5,.5])
        data = tuple(m.ravel())
        out = np.empty((h,w,len(channels)),np.float32)
        for c, channel in enumerate(channels):
            out[...,c] = np.asarray(channel.transform((w,h),Image.Transform.AFFINE,data,
                                                       resample=Image.Resampling.BILINEAR,fillcolor=0))
        return out
    return warp


def shifted(px, dx, dy):
    y, x = np.indices(px.shape[:2], dtype=np.float32)
    return sample(px, x - dx, y - dy)


def over(front, back):
    return front + back * (1 - front[..., 3:4])


def composite(p, original, effect):
    """compositeOriginal locates the ORIGINAL: behind/on-top of the effect, or absent."""
    mode = p.s("compositeOriginal", "behind")
    if mode == "none":
        return effect
    return over(original, effect) if mode == "on-top" else over(effect, original)


def colored(mask, color):
    a = np.clip(mask[..., None] * color[3], 0, 1)
    return premul(np.broadcast_to(color[:3], a.shape[:-1] + (3,)), a)


def morphology(a, radius, grow=True):
    """Circular grayscale dilation/erosion, fractional radii interpolate neighbours."""
    radius = max(0.0, float(radius))
    r = int(math.ceil(radius))
    if not r:
        return a.copy()
    out = a.copy()
    padded = np.pad(a, ((r, r), (r, r)), mode="constant")
    for dy in range(-r, r+1):
        for dx in range(-r, r+1):
            weight = min(1.0, max(0.0, radius + 1 - math.hypot(dx, dy)))
            if weight:
                v = padded[r+dy:r+dy+a.shape[0], r+dx:r+dx+a.shape[1]]
                v = a + (v - a) * weight
                out = np.maximum(out, v) if grow else np.minimum(out, v)
    return out


def smoothstep(lo, hi, x):
    u = np.clip((x - lo) / max(1e-7, hi - lo), 0, 1)
    return u * u * (3 - 2*u)


def value_noise(x, y, seed):
    """Coordinate-stable smooth lattice noise, independent of render order/tile size."""
    ix, iy = np.floor(x), np.floor(y)
    fx, fy = x - ix, y - iy
    fx, fy = fx*fx*(3-2*fx), fy*fy*(3-2*fy)

    def lattice(x, y):
        v = np.sin(x * 127.1 + y * 311.7 + seed % 65521) * 43758.5453
        return v - np.floor(v)

    a = lattice(ix, iy)*(1-fx) + lattice(ix+1, iy)*fx
    b = lattice(ix, iy+1)*(1-fx) + lattice(ix+1, iy+1)*fx
    return (a*(1-fy) + b*fy).astype(np.float32)


def source_buf(rc, e, ctx, node=None, *, source=None):
    """Render a second input in its own parent frame; guard recursive references."""
    sid = rc.ev.str(e, "source", ctx) if source is None else source
    src, source_ctx = rc.ev.reference(sid or "", e, ctx)
    if src is None:
        if sid:
            warn_once("effect-source", sid, "source node not found")
        return None
    active = rc.cache.setdefault("effect-source-active", set())
    key = (src, source_ctx.scope)
    if (src is node and source_ctx.scope == ctx.scope) or key in active:
        warn_once("effect-source", sid, "recursive source reference skipped")
        return None
    active.add(key)
    try:
        loc = rc.node_location(src, source_ctx)
        out = loc.rc.render_node(src, loc.ctx, loc.matrix, loc.box, loc.layout, force=True)
        return None if out is None else result(out.buf, out.buf.px * out.opacity)
    finally:
        active.remove(key)


def gradient_colors(rc, e, ctx, u, node=None):
    """Map 0..1 through paint stops, or a rendered node's horizontal centre row.

    Paint IDs can be supplied as source or paint=url(#id). Stop interpolation uses
    a 1024-entry ramp in the paint's space, including opacity and stop midpoints.
    Without a source, the ramp is black to effect/@color (white by default).
    """
    from ..paint import _stops, mix_color
    from ..values import paint_ref

    p = Params(rc, e, ctx)
    ref = p.s("source") or paint_ref(p.s("paint"))
    el = rc.doc.ids.get(ref)
    if el is not None and any(c.tag == "stop" for c in el):
        stops = _stops(rc, el, ctx)
        space = rc.ev.str(el, "interpolationSpace", ctx, "linear")
        ramp = []
        for v in np.linspace(0, 1, 1024):
            c = stops[0][1] if v <= stops[0][0] else stops[-1][1]
            for (o0, c0, mid), (o1, c1, _) in zip(stops, stops[1:]):
                if o0 <= v < o1:
                    f = (v-o0)/max(o1-o0, 1e-7)
                    f = f ** (math.log(.5)/math.log(np.clip(mid, .0001, .9999)))
                    c = mix_color(c0, c1, f, space)
                    break
            ramp.append(color_to_working(c, rc.linear))
        ramp = np.asarray(ramp, np.float32)
    elif ref:
        src = source_buf(rc, e, ctx, node)
        if src is None:
            return None
        rgb, a = straight(src.px)
        ramp = np.concatenate([rgb, a], -1)[src.h//2]
    else:
        ramp = np.asarray([(0, 0, 0, 1), p.color()], np.float32)
    pos = np.clip(u, 0, 1) * (len(ramp)-1)
    i = np.floor(pos).astype(int)
    return ramp[i] + (ramp[np.minimum(i+1, len(ramp)-1)]-ramp[i]) * (pos-i)[..., None]
