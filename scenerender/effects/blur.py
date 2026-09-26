"""Spatial blur kernels in linear light, with padded support for spill.

radius is Gaussian sigma for blur/tilt-shift and aperture radius for lens-blur;
directional radius is half the streak length. Radial angle is in degrees; zoom
amount is a percentage. Samples controls spatial quadrature (at most 256).
"""
from __future__ import annotations

import math

import numpy as np

from . import (Params, center, gaussian, grid, linear_pixels, result, sample,
               smoothstep, working_pixels)
from ..registry import EFFECTS, FULL, PARTIAL


def _count(p):
    return max(1, min(256, round(p.n("samples", 16))))


@EFFECTS.register("blur", level=FULL, note="padded separable Gaussian in linear light; box approximation at large radii")
def blur(rc, e, buf, ctx, node):
    radius = max(0, Params(rc, e, ctx).d("radius", 4))
    b = buf.pad(math.ceil(4*radius))
    return result(b, working_pixels(rc, gaussian(linear_pixels(rc, b.px), radius)))


def _directional(rc, buf, dx, dy, count):
    b = buf.pad(math.ceil(max(abs(dx), abs(dy))))
    x, y = grid(b)
    src = linear_pixels(rc, b.px)
    out = np.zeros_like(src)
    for u in (np.arange(count)+.5)/count*2-1:
        out += sample(src, x-u*dx, y-u*dy)/count
    return result(b, working_pixels(rc, out))


@EFFECTS.register("directional-blur", level=FULL, note="sampled line kernel with radius half-length and angle in degrees")
def directional_blur(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    r, angle = max(0, p.d("radius", 4)), math.radians(p.n("angle", 0))
    return _directional(rc, buf, r*math.cos(angle), r*math.sin(angle), _count(p))


@EFFECTS.register("radial-blur", level=PARTIAL, note="sampled rotational blur around frame-space centre; no adaptive integration")
def radial_blur(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    angle = math.radians(p.n("angle", 0))
    cx, cy = center(p, buf)
    reach = math.hypot(max(abs(cx), abs(buf.w-1-cx)), max(abs(cy), abs(buf.h-1-cy)))
    pad = math.ceil(2*reach*min(1, abs(angle)/4))
    b = buf.pad(pad)
    cx, cy = cx+pad, cy+pad
    x, y = grid(b)
    src = linear_pixels(rc, b.px)
    out = np.zeros_like(src)
    n = _count(p)
    for a in ((np.arange(n)+.5)/n-.5)*angle:
        dx, dy = x-cx, y-cy
        out += sample(src, cx+dx*math.cos(a)-dy*math.sin(a), cy+dx*math.sin(a)+dy*math.cos(a))/n
    return result(b, working_pixels(rc, out))


@EFFECTS.register("zoom-blur", level=PARTIAL, note="sampled radial scaling; amount is zoom percentage")
def zoom_blur(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    amount = np.clip(p.n("amount", 1)/100, -.95, 4)
    cx, cy = center(p, buf)
    reach = max(abs(cx), abs(cy), abs(buf.w-1-cx), abs(buf.h-1-cy))
    pad = math.ceil(reach*abs(amount))
    b = buf.pad(pad)
    x, y = grid(b)
    cx, cy = cx+pad, cy+pad
    src, n = linear_pixels(rc, b.px), _count(p)
    out = np.zeros_like(src)
    for u in (np.arange(n)+.5)/n:
        factor = 1+u*amount
        out += sample(src, cx+(x-cx)/factor, cy+(y-cy)/factor)/n
    return result(b, working_pixels(rc, out))


@EFFECTS.register("lens-blur", level=PARTIAL, note="uniform circular aperture sampled with symmetric sunflower pairs")
def lens_blur(rc, e, buf, ctx, node):
    """Circular-aperture (bokeh) blur: FFT convolution with an anti-aliased disc kernel."""
    p = Params(rc, e, ctx)
    r = max(0.0, p.d("radius", 4))
    if r < 0.5:
        return buf
    b = buf.pad(math.ceil(r))
    k = math.ceil(r)
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1].astype(np.float32)
    disc = np.clip(r + 0.5 - np.hypot(xx, yy), 0, 1)
    disc /= disc.sum()
    return result(b, working_pixels(rc, fft_convolve(linear_pixels(rc, b.px), disc)))


def fft_convolve(px, kernel):
    """Same-size 2D convolution of every channel of px with kernel (zero boundary)."""
    h, w = px.shape[:2]
    kh, kw = kernel.shape
    fh, fw = h + kh - 1, w + kw - 1
    fk = np.fft.rfft2(kernel, (fh, fw))
    out = np.fft.irfft2(np.fft.rfft2(px, (fh, fw), axes=(0, 1)) * fk[..., None], (fh, fw), axes=(0, 1))
    oy, ox = kh // 2, kw // 2
    return out[oy:oy + h, ox:ox + w].astype(np.float32)


@EFFECTS.register("tilt-shift", level=PARTIAL, note="blend sharp image and Gaussian using distance from angled focus strip")
def tilt_shift(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    r = max(0, p.d("radius", 4))
    pad = math.ceil(4*r)
    b = buf.pad(pad)
    cx, cy = center(p, buf)
    x, y = grid(b)
    angle = math.radians(p.n("angle", 0))
    dist = abs((x-cx-pad)*-math.sin(angle)+(y-cy-pad)*math.cos(angle))
    size = max(0, p.d("size", 1))
    w = smoothstep(size/2, size/2+max(r, 1), dist)[..., None]
    src = linear_pixels(rc, b.px)
    return result(b, working_pixels(rc, src+(gaussian(src, r)-src)*w))


@EFFECTS.register("pixel-motion-blur", level=PARTIAL, note="translation-velocity streak from node x/y, or offsetX/Y; no optical flow/history")
def pixel_motion_blur(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    if node is None:
        dx, dy = p.d("offsetX", 8), p.d("offsetY", 8)
    else:
        prev = ctx.at(ctx.t-1/float(rc.doc.fps))
        dx, dy = [(rc.ev.num(node, key, ctx, 0)-rc.ev.num(node, key, prev, 0))*rc.scale for key in ("x", "y")]
    amount = p.n("amount", 1)/2
    return _directional(rc, buf, dx*amount, dy*amount, _count(p))
