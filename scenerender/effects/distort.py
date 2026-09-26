"""Inverse-coordinate image warps with bilinear premultiplied sampling.

Centres are frame-space document pixels. Distances (amount, radius, size and
offsets) are scaled except dimensionless radial deformation strengths. The
schema leaves warp details open: size is noise/wave wavelength, frequency
multiplies spatial frequency, speed advances phase in cycles per second.
Distortions retain tile bounds, except displacement which pads its support.
"""
from __future__ import annotations

import math

import numpy as np

from . import Params, center, grid, result, sample, source_buf, straight, value_noise
from ..registry import EFFECTS, FULL, PARTIAL


def _polar(p, buf):
    x, y = grid(buf)
    cx, cy = center(p, buf)
    return x, y, cx, cy, np.hypot(x-cx, y-cy), np.arctan2(y-cy, x-cx)


@EFFECTS.register("mirror", level=FULL, note="reflect one half-plane across angled line through centre")
def mirror(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    cx, cy = center(p, buf)
    angle = math.radians(p.n("angle", 0))
    nx, ny = math.cos(angle), math.sin(angle)
    d = np.maximum((x-cx)*nx+(y-cy)*ny, 0)
    return result(buf, sample(buf.px, x-2*d*nx, y-2*d*ny))


@EFFECTS.register("kaleidoscope", level=FULL, note="polar mirrored wedges; samples gives the number of sectors")
def kaleidoscope(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y, cx, cy, r, theta = _polar(p, buf)
    sectors = max(1, round(p.n("samples", 16)))
    wedge = 2*math.pi/sectors
    angle = math.radians(p.n("angle", 0))
    theta = abs((theta-angle+wedge/2) % wedge-wedge/2)+angle
    return result(buf, sample(buf.px, cx+r*np.cos(theta), cy+r*np.sin(theta)))


@EFFECTS.register("tile", level=FULL, note="repeat a square source tile of size document pixels, with offsets and angle")
def tile(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    cx, cy = center(p, buf)
    size = max(p.d("size", 1), .01)
    angle = math.radians(p.n("angle", 0))
    dx, dy = x-cx-p.d("offsetX", 8), y-cy-p.d("offsetY", 8)
    sx = ((dx*math.cos(angle)+dy*math.sin(angle))/size+.5) % 1*buf.w-.5
    sy = ((-dx*math.sin(angle)+dy*math.cos(angle))/size+.5) % 1*buf.h-.5
    return result(buf, sample(buf.px, sx, sy, "wrap"))


@EFFECTS.register("displacement-map", level=PARTIAL, note="root-rendered source channels around neutral 0.5 displace frame-aligned pixels")
def displacement_map(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    src = source_buf(rc, e, ctx, node)
    if src is None:
        return buf.copy()
    amount = p.d("amount", 1)
    b = buf.pad(math.ceil(abs(amount)))
    rgb, a = straight(src.region(b.rect))
    rgb = rgb*a+.5*(1-a)
    channel = p.s("channel", "rgb")
    if channel == "rgb":
        dx, dy = rgb[..., 0]-.5, rgb[..., 1]-.5
    else:
        from . import luma
        v = a[..., 0] if channel == "alpha" else (luma(rgb) if channel == "luma" else rgb[..., {"red":0, "green":1, "blue":2}.get(channel, 0)])
        dx = dy = v-.5
    x, y = grid(b)
    return result(b, sample(b.px, x+dx*2*amount, y+dy*2*amount))


def _noise_warp(rc, e, buf, ctx, heat=False):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    amount, size = p.d("amount", 1), max(.01, p.d("size", 1))
    frequency = p.n("frequency", 1)/size
    seed = rc.ev.seed_for(e, str(p.n("seed", 0)))
    t = ctx.t*p.n("speed", 1)
    nx, ny = (x+buf.x0)*frequency, (y+buf.y0)*frequency
    dx = (value_noise(nx, ny+t, seed)*2-1)*amount
    dy = (value_noise(nx+37, ny+t, seed+7)*2-1)*amount
    if heat:
        dy *= .25
        dx *= .5+.5*np.sin(ny*.3+t)
    return result(buf, sample(buf.px, x+dx, y+dy))


@EFFECTS.register("turbulent-displace", level=PARTIAL, note="two smooth seeded lattice-noise displacement fields")
def turbulent_displace(rc, e, buf, ctx, node):
    return _noise_warp(rc, e, buf, ctx)


@EFFECTS.register("heat-haze", level=PARTIAL, note="animated anisotropic noise refraction; no depth or temperature simulation")
def heat_haze(rc, e, buf, ctx, node):
    return _noise_warp(rc, e, buf, ctx, True)


@EFFECTS.register("wave-warp", level=FULL, note="sine displacement perpendicular to angle, with wavelength, frequency and speed")
def wave_warp(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    angle = math.radians(p.n("angle", 0))
    nx, ny = math.cos(angle), math.sin(angle)
    u = (x+buf.x0)*nx+(y+buf.y0)*ny
    phase = u/max(.01, p.d("size", 1))*p.n("frequency", 1)-ctx.t*p.n("speed", 1)
    d = p.d("amount", 1)*np.sin(2*math.pi*phase)
    return result(buf, sample(buf.px, x-d*ny, y+d*nx))


@EFFECTS.register("ripple", level=FULL, note="radial sine displacement with radius falloff and animated phase")
def ripple(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y, cx, cy, r, theta = _polar(p, buf)
    radius = max(.01, p.d("radius", 4))
    phase = r/max(.01, p.d("size", 1))*p.n("frequency", 1)-ctx.t*p.n("speed", 1)
    delta = p.d("amount", 1)*np.sin(2*math.pi*phase)*np.clip(1-r/radius, 0, 1)
    return result(buf, sample(buf.px, x+delta*np.cos(theta), y+delta*np.sin(theta)))


@EFFECTS.register("twirl", level=FULL, note="radius-limited polar rotation, angle times quadratic radial falloff")
def twirl(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y, cx, cy, r, theta = _polar(p, buf)
    radius = max(.01, p.d("radius", 4))
    theta += math.radians(p.n("angle", 0))*p.n("amount", 1)*np.clip(1-r/radius, 0, 1)**2
    return result(buf, sample(buf.px, cx+r*np.cos(theta), cy+r*np.sin(theta)))


@EFFECTS.register("spherize", level=FULL, note="spherical inverse radial projection within radius, blended by amount")
def spherize(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y, cx, cy, r, theta = _polar(p, buf)
    radius = max(.01, p.d("radius", 4))
    u = np.clip(r/radius, 0, 1)
    mapped = np.arcsin(u)/(math.pi/2)*radius
    rr = r+np.where(r < radius, mapped-r, 0)*np.clip(p.n("amount", 1), -1, 1)
    return result(buf, sample(buf.px, cx+rr*np.cos(theta), cy+rr*np.sin(theta)))


@EFFECTS.register("bulge", level=FULL, note="smooth radial magnification or pinch within radius")
def bulge(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y, cx, cy, r, theta = _polar(p, buf)
    radius = max(.01, p.d("radius", 4))
    scale = np.exp(-np.clip(p.n("amount", 1), -8, 8)*np.clip(1-(r/radius)**2, 0, 1)**2)
    return result(buf, sample(buf.px, cx+(x-cx)*scale, cy+(y-cy)*scale))


@EFFECTS.register("lens-distortion", level=PARTIAL, note="single-coefficient radial barrel/pincushion model normalized to tile size")
def lens_distortion(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y, cx, cy, r, theta = _polar(p, buf)
    k = p.n("amount", 1)*.01
    scale = 1+k*(r/max(buf.w, buf.h)*2)**2
    return result(buf, sample(buf.px, cx+(x-cx)*scale, cy+(y-cy)*scale))
