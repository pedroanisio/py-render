"""Alpha-based layer styles.

compositeOriginal describes the original's placement relative to the style:
behind (default), on-top, or none. Position selects outside/inside/centred
rings for stroke/outline/bevel. Shadow offsets and radii are document pixels.
"""
from __future__ import annotations

import math

import numpy as np

from . import (Params, colored, composite, gaussian, morphology, result, shifted)
from ..registry import EFFECTS, FULL, PARTIAL


@EFFECTS.register("drop-shadow", level=FULL, note="offset blurred alpha, tinted and composited with padded spill")
def drop_shadow(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    r = max(0, p.d("radius", 4))
    dx, dy = p.d("offsetX", 8), p.d("offsetY", 8)
    b = buf.pad(math.ceil(4*r+max(abs(dx), abs(dy))))
    alpha = gaussian(b.px[..., 3:4], r)
    alpha = shifted(alpha, dx, dy)[..., 0]*max(0, p.n("intensity", 1))
    return result(b, composite(p, b.px, colored(alpha, p.color(default=(0,0,0,1)))))


@EFFECTS.register("inner-shadow", level=FULL, note="blurred offset inverse alpha clipped to the original matte")
def inner_shadow(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    r = max(0, p.d("radius", 4))
    dx, dy = p.d("offsetX", 8), p.d("offsetY", 8)
    b = buf.pad(math.ceil(4*r+max(abs(dx), abs(dy))))
    blurred = shifted(gaussian(b.px[..., 3:4], r), dx, dy)[..., 0]
    alpha = b.px[..., 3]*(1-blurred)*max(0, p.n("intensity", 1))
    return result(b, composite(p, b.px, colored(alpha, p.color(default=(0,0,0,1))))).crop_to(buf.rect)


@EFFECTS.register("inner-glow", level=FULL, note="inverse blurred matte creates an interior edge glow")
def inner_glow(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    r = max(0, p.d("radius", 4))
    b = buf.pad(math.ceil(4*r))
    alpha = b.px[..., 3]*(1-gaussian(b.px[..., 3:4], r)[..., 0])*max(0, p.n("intensity", 1))
    return result(b, composite(p, b.px, colored(alpha, p.color()))).crop_to(buf.rect)


def _ring(alpha, r, position):
    if position == "inside":
        return alpha-morphology(alpha, r, False)
    if position == "center":
        return morphology(alpha, r/2)-morphology(alpha, r/2, False)
    return morphology(alpha, r)-alpha


@EFFECTS.register("stroke", "outline", level=FULL, note="circular alpha morphology ring with outside/inside/centre placement")
def stroke(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    r, position = max(0, p.d("radius", 4)), p.s("position", "outside")
    b = buf.pad(0 if position == "inside" else math.ceil(r+1))
    alpha = _ring(b.px[..., 3], r, position)*max(0, p.n("intensity", 1))
    return result(b, composite(p, b.px, colored(alpha, p.color())))


@EFFECTS.register("bevel", level=PARTIAL, note="alpha-gradient highlight/shadow on a morphology ring; 2D relief approximation")
def bevel(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    r, position = max(0, p.d("radius", 4)), p.s("position", "outside")
    b = buf.pad(max(1, math.ceil(r+1)))
    alpha = gaussian(b.px[..., 3:4], max(.3, r/2))[..., 0]
    gy, gx = np.gradient(alpha)
    angle = math.radians(p.n("angle", 0))
    v = (gx*math.cos(angle)+gy*math.sin(angle))*max(1, r)*p.n("intensity", 1)
    ring = _ring(b.px[..., 3], r, position)
    light = colored(ring*np.clip(v, 0, 1), p.color())
    dark = colored(ring*np.clip(-v, 0, 1), p.color("keyColor", (0,0,0,1)))
    out = result(b, composite(p, b.px, light+dark))
    return out.crop_to(buf.rect) if position == "inside" else out


@EFFECTS.register("long-shadow", level=PARTIAL, note="sampled alpha extrusion; radius is length, angle sets direction")
def long_shadow(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    length = max(0, p.d("radius", 4))
    angle = math.radians(p.n("angle", 0))
    dx, dy = length*math.cos(angle), length*math.sin(angle)
    b = buf.pad(math.ceil(max(abs(dx), abs(dy))))
    n = max(1, min(512, max(math.ceil(length), round(p.n("samples", 16)))))
    alpha = np.zeros(b.px.shape[:2], np.float32)
    for u in np.linspace(0, 1, n+1):
        alpha = np.maximum(alpha, shifted(b.px[..., 3:4], dx*u, dy*u)[..., 0])
    return result(b, composite(p, b.px, colored(alpha*max(0, p.n("intensity", 1)), p.color(default=(0,0,0,1)))))
