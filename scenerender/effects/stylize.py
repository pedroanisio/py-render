"""Texture, raster styles, and explicitly spatial temporal approximations.

Noise amount is a fraction of full scale (film grain uses 0.05*amount).
Pixelate/mosaic/halftone size is the cell diameter in document pixels.
Echo repeats the current tile at offsetX/Y with exponential decay; it never
reads previous frames. Posterize-time holds only node x/y at frequency Hz,
because the already-rendered pixels cannot be reconstructed at another time.
"""
from __future__ import annotations

import math

import numpy as np

from . import (Params, center, display_rgb, from_display, gaussian, grid, luma,
               over, premul, result, sample, shifted, straight)
from ..registry import EFFECTS, FULL, PARTIAL


@EFFECTS.register("noise", level=FULL, note="seeded per-frame uniform RGB or monochrome noise in encoded sRGB")
def noise(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    channels = 3 if p.s("channel", "rgb") == "rgb" else 1
    random = p.rng().uniform(-1, 1, rgb.shape[:2]+(channels,))
    return from_display(rc, buf, rgb+random*p.n("amount", 1), a)


@EFFECTS.register("film-grain", level=PARTIAL, note="seeded Gaussian grain, size-filtered and weighted by midtone luminance")
def film_grain(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    grain = p.rng().normal(0, 1, rgb.shape[:2]+(1,)).astype(np.float32)
    sigma = max(0, (p.d("size", 1)-1)/2)
    grain = gaussian(grain, sigma)
    grain /= max(float(grain.std()), 1e-6)
    weight = .25+.75*np.sqrt(np.clip(luma(rgb), 0, 1))[..., None]
    return from_display(rc, buf, rgb+grain*.05*p.n("amount", 1)*weight, a)


@EFFECTS.register("sharpen", level=FULL, note="Gaussian unsharp residual in straight colour, preserving alpha")
@EFFECTS.register("unsharp-mask", level=FULL, note="threshold-gated Gaussian unsharp residual in straight colour, preserving alpha")
def sharpen(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = straight(buf.px)
    r = max(0, p.d("radius", 4))
    blurred, _ = straight(gaussian(buf.px, r))
    diff = rgb-blurred
    if p.s("type") == "unsharp-mask":
        # Shared schema threshold default is 0.7, also for unsharp masking.
        diff *= (np.max(abs(diff), -1) >= p.n("threshold", .7))[..., None]
    return result(buf, premul(np.clip(rgb+diff*p.n("amount", 1), 0, 1), a))


@EFFECTS.register("rgb-split", level=FULL, note="opposing red/blue translations, green fixed, union alpha and padded spill")
def rgb_split(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    dx, dy = p.d("offsetX", 8)*p.n("amount", 1), p.d("offsetY", 8)*p.n("amount", 1)
    b = buf.pad(math.ceil(max(abs(dx), abs(dy))))
    red, blue = shifted(b.px, dx, dy), shifted(b.px, -dx, -dy)
    px = np.stack([red[..., 0], b.px[..., 1], blue[..., 2], np.maximum.reduce([red[..., 3], b.px[..., 3], blue[..., 3]])], -1)
    return result(b, px)


@EFFECTS.register("chromatic-aberration", level=PARTIAL, note="radial red/blue displacement with union alpha and padded support")
def chromatic_aberration(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    amount = p.d("amount", 1)
    pad = math.ceil(abs(amount))
    b = buf.pad(pad)
    x, y = grid(b)
    cx, cy = center(p, buf)
    dx, dy = x-cx-pad, y-cy-pad
    norm = np.maximum(np.hypot(dx, dy), 1)
    dx, dy = dx/norm*amount, dy/norm*amount
    red, blue = sample(b.px, x+dx, y+dy), sample(b.px, x-dx, y-dy)
    return result(b, np.stack([red[..., 0], b.px[..., 1], blue[..., 2], np.maximum.reduce([red[..., 3], b.px[..., 3], blue[..., 3]])], -1))


def _glitch(p, buf):
    x, y = grid(buf)
    size = max(1, round(p.d("size", 1)))
    rng = p.rng()
    rows = rng.uniform(-1, 1, math.ceil(buf.h/size))
    rows *= rng.random(len(rows)) < .25
    shifts = np.repeat(rows, size)[:buf.h, None]*p.d("amount", 1)
    return sample(buf.px, x+shifts, y)


@EFFECTS.register("glitch", level=PARTIAL, note="seeded horizontal block tearing, preserving premultiplied channels")
def glitch(rc, e, buf, ctx, node):
    return result(buf, _glitch(Params(rc, e, ctx), buf))


@EFFECTS.register("scanlines", level=FULL, note="frame-aligned periodic dark lines controlled by size/frequency/amount")
def scanlines(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    period = max(1, 2*p.d("size", 1))
    wave = .5+.5*np.cos((y+buf.y0)*2*math.pi/period*p.n("frequency", 1))
    px = buf.px.copy()
    px[..., :3] *= (1-wave*np.clip(p.n("amount", 1), 0, 1)*.5)[..., None]
    return result(buf, px)


@EFFECTS.register("vhs", level=PARTIAL, note="seeded row jitter, horizontal colour bleed, scanlines and low-amplitude noise")
def vhs(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    px = _glitch(p, buf)
    rgb, a = display_rgb(rc, px)
    blurred, _ = display_rgb(rc, gaussian(px, max(.3, p.d("radius", 4)), 0))
    rgb = rgb*.65+blurred*.35
    rgb += p.rng().normal(0, .025, rgb.shape)*p.n("amount", 1)
    b = from_display(rc, buf, rgb, a)
    return scanlines(rc, e, b, ctx, node)


@EFFECTS.register("pixelate", level=FULL, note="frame-aligned nearest cell-centre sampling with size in document pixels")
def pixelate(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    size = max(1, round(p.d("size", 1)))
    x, y = grid(buf)
    sx = np.floor((x+buf.x0)/size)*size+(size-1)/2-buf.x0
    sy = np.floor((y+buf.y0)/size)*size+(size-1)/2-buf.y0
    return result(buf, sample(buf.px, sx, sy, "clamp"))


@EFFECTS.register("mosaic", level=FULL, note="average premultiplied pixels into frame-aligned square cells")
def mosaic(rc, e, buf, ctx, node):
    size = max(1, round(Params(rc, e, ctx).d("size", 1)))
    left, top = buf.x0 % size, buf.y0 % size
    right, bottom = (-buf.w-left) % size, (-buf.h-top) % size
    px = np.pad(buf.px, ((top, bottom), (left, right), (0, 0)))
    h, w = px.shape[:2]
    means = px.reshape(h//size, size, w//size, size, 4).mean((1, 3))
    out = means.repeat(size, 0).repeat(size, 1)
    return result(buf, out[top:top+buf.h, left:left+buf.w])


@EFFECTS.register("halftone", level=PARTIAL, note="rotated monochrome dot screen with luminance-controlled dot area")
def halftone(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    x, y = grid(buf)
    size = max(1, p.d("size", 1))
    angle = math.radians(p.n("angle", 0))
    dx, dy = x+buf.x0, y+buf.y0
    u = (dx*math.cos(angle)+dy*math.sin(angle))/size
    v = (-dx*math.sin(angle)+dy*math.cos(angle))/size
    d = np.hypot(u % 1-.5, v % 1-.5)
    radius = np.sqrt(np.clip(1-luma(rgb), 0, 1)/math.pi)
    dots = (d > radius).astype(np.float32)
    return from_display(rc, buf, np.repeat(dots[..., None], 3, -1), a)


@EFFECTS.register("emboss", level=FULL, note="directional luminance derivative about middle grey, retaining alpha")
def emboss(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    y = luma(rgb)[..., None]
    angle = math.radians(p.n("angle", 0))
    radius = p.d("radius", 4)
    diff = shifted(y, radius*math.cos(angle), radius*math.sin(angle))-shifted(y, -radius*math.cos(angle), -radius*math.sin(angle))
    return from_display(rc, buf, np.repeat(.5+diff*p.n("amount", 1), 3, -1), a)


@EFFECTS.register("echo", level=PARTIAL, note="translated copies of current tile with exponential decay; no temporal history")
def echo(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    n = max(1, min(128, round(p.n("samples", 16))))
    dx, dy = p.d("offsetX", 8), p.d("offsetY", 8)
    b = buf.pad(math.ceil(max(abs(dx), abs(dy))*(n-1)))
    out = np.zeros_like(b.px)
    for i in range(n-1, -1, -1):
        fade = math.exp(-i*max(0, p.n("amount", 1))/max(1, n-1))
        out = over(shifted(b.px, dx*i, dy*i)*fade, out)
    return result(b, out)


@EFFECTS.register("posterize-time", level=PARTIAL, note="hold node x/y translation at frequency Hz; pixel content and other transforms stay current")
def posterize_time(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    frequency = max(1e-6, p.n("frequency", 1))
    if node is None:
        return buf.copy()
    held = ctx.at(math.floor(ctx.t*frequency)/frequency)
    dx, dy = [(rc.ev.num(node, key, held, 0)-rc.ev.num(node, key, ctx, 0))*rc.scale for key in ("x", "y")]
    b = buf.pad(math.ceil(max(abs(dx), abs(dy))))
    return result(b, shifted(b.px, dx, dy))


@EFFECTS.register("letterbox", level=FULL, note="colour bars at frame top/bottom, size in document pixels")
def letterbox(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    size = max(0, p.d("size", 1))
    bar = ((y+buf.y0 < size) | (y+buf.y0 >= rc.height-size))
    c = p.color(default=(0,0,0,1))
    px = buf.px.copy()
    px[bar] = np.r_[c[:3]*c[3], c[3]]
    return result(buf, px)
