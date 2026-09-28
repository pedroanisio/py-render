"""Inverse-coordinate image warps with bilinear premultiplied sampling.

Centres are frame-space document pixels. Distances (amount, radius, size and
offsets) are scaled except dimensionless radial deformation strengths. The
schema leaves warp details open: size is noise/wave wavelength, frequency
multiplies spatial frequency, speed advances phase in cycles per second.
Distortions retain tile bounds, except displacement which pads its support.
Fractal controls: noiseType basic/turbulent/smooth/sharp/rocky/strings,
octaves 1..12, scale (size fallback), evolution degrees plus speed turns/sec.
Displacement param: turbulent/horizontal/vertical/twist/bulge; pinning 0..1.
Heat-haze uses vertical anisotropy (.15) and ground falloff power (1).
"""
from __future__ import annotations

import math

import numpy as np

from . import Params, center, grid, result, sample, source_buf, straight, value_noise
from ..registry import EFFECTS, FULL


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


@EFFECTS.register("displacement-map", level=FULL, note="source in its real parent frame, channels around 0.5, transparent map neutral")
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
        v = np.where(a[..., 0] > 0, a[..., 0], .5) if channel == "alpha" else (luma(rgb) if channel == "luma" else rgb[..., {"red":0, "green":1, "blue":2}.get(channel, 0)])
        dx = dy = v-.5
    x, y = grid(b)
    return result(b, sample(b.px, x+dx*2*amount, y+dy*2*amount))


def _noise_warp(rc, e, buf, ctx, heat=False):
    from .fields import fractal, fractal_grid
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    amount = p.d("amount", 1)
    if amount == 0:
        return buf.copy()
    size = max(.01, p.param("scale", p.n("size", 1))*rc.scale)
    frequency = p.n("frequency", 1)/size
    seed = rc.ev.seed_for(e, str(p.n("seed", 0)))
    time = ctx.t*p.n("speed", 1)
    evolution = p.param("evolution", 0)/360+time
    nx, ny = (x+buf.x0)*frequency, (y+buf.y0)*frequency
    kind = p.param("noiseType", "basic")
    octaves = p.param("octaves", 6)
    if heat:
        ny -= time  # rising refractive cells; independent boiling evolution
    from .. import gpu, kernels
    mode = p.param("displacement", "turbulent")
    pin = np.clip(p.param("pinning", 0),0,1)
    if kernels.enabled() and gpu.worth(buf.px) and not pin and mode != "bulge":
        # Fields, displacement and sampling on the GPU (the lattice tables are evaluated here).
        xs, ys = nx[0], ny[:, 0]
        memo = rc.cache.setdefault(("noise-warp-rows", e), ({}, {}))
        f1 = fractal_grid(xs, ys, seed, octaves, evolution, kind, memo=memo[0], device=True)
        f2 = fractal_grid(xs+37, ys+17, seed+7, octaves, evolution, kind, memo=memo[1], device=True)
        code = {"horizontal": 1, "vertical": 2, "twist": 3}.get(mode, 0)
        vertical = p.param("vertical", .15 if heat else 0) if (mode == "horizontal" or heat) else 0
        falloff = max(0, p.param("falloff", 1)) if heat else 1
        return result(buf, gpu.noise_warp(buf.px, f1, f2, amount, code, vertical, heat, falloff))
    if kernels.enabled():
        # The coordinates vary by column (x) and by row (y) only: the separable, memoized path.
        xs, ys = nx[0], ny[:, 0]
        memo = rc.cache.setdefault(("noise-warp-rows", e), ({}, {}))
        dx = (fractal_grid(xs, ys, seed, octaves, evolution, kind, memo=memo[0])*2-1)*amount
        dy = (fractal_grid(xs+37, ys+17, seed+7, octaves, evolution, kind, memo=memo[1])*2-1)*amount
    else:
        dx = (fractal(nx, ny, seed, octaves, evolution, kind)*2-1)*amount
        dy = (fractal(nx+37, ny+17, seed+7, octaves, evolution, kind)*2-1)*amount
    mode = p.param("displacement", "turbulent")
    if mode == "horizontal" or heat:
        dy *= p.param("vertical", .15 if heat else 0)
    elif mode == "vertical":
        dx *= 0
    elif mode == "twist":
        dx, dy = -dy, dx
    elif mode == "bulge":
        cx, cy = center(p, buf)
        r = np.maximum(np.hypot(x-cx,y-cy),1)
        dx, dy = dx*(x-cx)/r, dx*(y-cy)/r
    if heat:
        # Refraction grows towards the hot ground at the bottom of the tile.
        envelope = np.clip((y+.5)/max(buf.h,1),0,1)**max(0,p.param("falloff", 1))
        dx, dy = dx*envelope, dy*envelope
    pin = np.clip(p.param("pinning", 0),0,1)
    if pin:
        edge = np.minimum.reduce([x, y, buf.w-1-x, buf.h-1-y])
        fade = np.clip(edge/max(size,1),0,1)
        dx, dy = dx*(1-pin+pin*fade), dy*(1-pin+pin*fade)
    return result(buf, sample(buf.px, x+dx, y+dy))


@EFFECTS.register("turbulent-displace", level=FULL, note="evolving multi-octave fractal displacement; type/octaves/scale/evolution/pinning params")
def turbulent_displace(rc, e, buf, ctx, node):
    return _noise_warp(rc, e, buf, ctx)


@EFFECTS.register("heat-haze", level=FULL, note="rising evolving fractal refraction, anisotropy and ground-distance falloff")
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


@EFFECTS.register("lens-distortion", level=FULL, note="Brown-Conrady k1/k2/p1/p2; amount/100 default k1; cylindrical param")
def lens_distortion(rc, e, buf, ctx, node):
    """Inverse Brown-Conrady sampling; params k1=amount/100, k2=p1=p2=0.

    Coordinates normalized by half max tile extent. cylindrical=1 limits radial
    bending to x, cylindrical=2 to y. Zero coefficients are exactly identity.
    """
    p = Params(rc, e, ctx)
    x, y, cx, cy, _, _ = _polar(p, buf)
    norm = max(buf.w,buf.h)/2
    u, v = (x-cx)/norm, (y-cy)/norm
    k1, k2 = p.param("k1", p.n("amount",1)*.01), p.param("k2",0)
    p1, p2 = p.param("p1",0), p.param("p2",0)
    cylinder = round(p.param("cylindrical",0))
    r2 = u*u if cylinder == 1 else v*v if cylinder == 2 else u*u+v*v
    radial = k1*r2+k2*r2*r2
    du = u*radial+2*p1*u*v+p2*(r2+2*u*u)
    dv = v*radial+p1*(r2+2*v*v)+2*p2*u*v
    if cylinder == 1:
        dv = 0
    elif cylinder == 2:
        du = 0
    return result(buf, sample(buf.px, x+norm*du, y+norm*dv))
