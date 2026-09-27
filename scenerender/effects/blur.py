"""Spatial blur kernels in linear light, with padded support for spill.

radius is Gaussian sigma for blur/tilt-shift and aperture radius for lens-blur;
directional radius is half the streak length. Radial angle is in degrees; zoom
amount is a percentage. Samples controls spatial quadrature (at most 256). Lens aperture is a disc
unless param blades >= 3 specifies a regular polygon rotated by angle.
Lens threshold/intensity add scene-linear highlight energy before convolution.
Tilt-shift interpolates a stack of radii rather than mixing sharp/max-blur.
"""
from __future__ import annotations

import math

import numpy as np

from . import (Params, affine_sample, affine_sampler, center, gaussian, grid, linear_pixels, result, sample,
               smoothstep, working_pixels)
from ..registry import EFFECTS, FULL


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


@EFFECTS.register("radial-blur", level=FULL, note="uniform angular shutter integral; angle in degrees, samples quadrature, padded support")
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
    warp = affine_sampler(src)
    for a in ((np.arange(n)+.5)/n-.5)*angle:
        c, s = math.cos(a), math.sin(a)
        out += warp([[c,-s,cx-c*cx+s*cy],[s,c,cy-s*cx-c*cy]])/n
    return result(b, working_pixels(rc, out))


@EFFECTS.register("zoom-blur", level=FULL, note="uniform zoom shutter integral; amount percent, samples quadrature, padded support")
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
    warp = affine_sampler(src)
    for u in (np.arange(n)+.5)/n:
        factor = 1+u*amount
        inv = 1/factor
        out += warp([[inv,0,cx*(1-inv)],[0,inv,cy*(1-inv)]])/n
    return result(b, working_pixels(rc, out))


@EFFECTS.register("lens-blur", level=FULL, note="FFT disc/polygon aperture; blades param, angle rotation, threshold/intensity highlight bloom")
def lens_blur(rc, e, buf, ctx, node):
    """Circular-aperture (bokeh) blur: FFT convolution with an anti-aliased disc kernel."""
    p = Params(rc, e, ctx)
    r = max(0.0, p.d("radius", 4))
    if r < 0.5:
        return buf
    b = buf.pad(math.ceil(r))
    k = math.ceil(r)
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1].astype(np.float32)
    blades = round(p.param("blades", 0))
    distance = np.hypot(xx, yy)
    if blades >= 3:
        theta = np.arctan2(yy, xx)-math.radians(p.n("angle", 0))
        sector = (theta+math.pi/blades) % (2*math.pi/blades)-math.pi/blades
        boundary = r*math.cos(math.pi/blades)/np.cos(sector)
    else:
        boundary = r
    disc = np.clip(boundary + 0.5 - distance, 0, 1)
    disc /= disc.sum()
    src = linear_pixels(rc, b.px).copy()
    from . import luma, straight
    rgb, a = straight(src)
    bright = np.maximum(luma(rgb)-p.n("threshold", .7), 0)
    src[..., :3] += rgb*(bright/np.maximum(luma(rgb), 1e-7))[..., None]*a*max(0, p.n("intensity", 1))
    out = fft_convolve(src, disc)
    # FFT roundoff must not leave RGB in pixels with exactly zero coverage.
    out[..., 3] = np.clip(out[..., 3], 0, 1)
    out[..., :3] = np.maximum(out[..., :3], 0)*(out[..., 3:4] > 1e-8)
    return result(b, working_pixels(rc, out))


def fft_convolve(px, kernel):
    """Same-size 2D convolution of every channel of px with kernel (zero boundary)."""
    h, w = px.shape[:2]
    kh, kw = kernel.shape
    def fast(n):
        # Avoid prime-sized FFTs (Bluestein is costly and memory hungry).
        while True:
            m = n
            for factor in (2,3,5):
                while m % factor == 0:
                    m //= factor
            if m == 1:
                return n
            n += 1
    fh, fw = fast(h + kh - 1), fast(w + kw - 1)
    fk = np.fft.rfft2(kernel, (fh, fw))
    out = np.fft.irfft2(np.fft.rfft2(px, (fh, fw), axes=(0, 1)) * fk[..., None], (fh, fw), axes=(0, 1))
    oy, ox = kh // 2, kw // 2
    return out[oy:oy + h, ox:ox + w].astype(np.float32)


@EFFECTS.register("tilt-shift", level=FULL, note="graduated Gaussian radius stack; size focus band, radius max sigma, softness ramp")
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
    width = max(r, p.n("softness", .1)*min(buf.w, buf.h), 1)
    radius = r*smoothstep(size/2, size/2+width, dist)
    src = linear_pixels(rc, b.px)
    levels = max(2, min(32, round(p.n("samples", 16))))
    radii = np.linspace(0, r, levels)
    out = np.zeros_like(src)
    for rad in radii:
        weight = np.maximum(1-abs(radius-rad)/max(r/(levels-1), 1e-7), 0)
        rows = np.flatnonzero(np.any(weight > 0,axis=1))
        if not len(rows):
            continue
        margin = math.ceil(4*rad)
        runs = np.split(rows,np.flatnonzero(np.diff(rows)>1)+1)
        for run in runs:
            start,end = int(run[0]),int(run[-1])+1
            cols = np.flatnonzero(np.any(weight[start:end] > 0,axis=0))
            x0,x1 = max(0,int(cols[0])-margin),min(b.w,int(cols[-1])+margin+1)
            y0,y1 = max(0,start-margin),min(b.h,end+margin)
            blurred = gaussian(src[y0:y1,x0:x1],rad)[start-y0:end-y0]
            out[start:end,x0:x1] += blurred*weight[start:end,x0:x1,None]
    return result(b, working_pixels(rc, src if r == 0 else out))


@EFFECTS.register("pixel-motion-blur", level=FULL,
                  note="full-matrix rigid trajectory blur (rotation/scale/skew/parents); amount shutter frames")
def pixel_motion_blur(rc, e, buf, ctx, node):
    """Uniform shutter integral of the current pixels under the node's full matrix.

    amount is shutter duration in frame periods, centered on ctx.t; samples
    controls quadrature. Exact rigid trajectories are evaluated at every sample,
    including parent transforms. Content/deformation changes are outside this
    vector-blur model. With no node offsetX/Y define the velocity in pixels/frame.
    """
    from ..raster import Buf
    from ..registry import warn_once
    p = Params(rc, e, ctx)
    amount, count = p.n("amount", 1), _count(p)
    if not amount:
        return buf.copy()
    if node is None:
        return _directional(rc, buf, p.d("offsetX", 8)*amount/2,
                            p.d("offsetY", 8)*amount/2, count)
    try:
        inv = np.linalg.inv(rc.world_matrix(node, ctx))
        times = ((np.arange(count)+.5)/count-.5)*amount/float(rc.doc.fps)
        matrices = [rc.world_matrix(node, ctx.at(ctx.t+dt)) @ inv for dt in times]
        corners = np.array([[buf.x0, buf.x0+buf.w, buf.x0+buf.w, buf.x0],
                            [buf.y0, buf.y0, buf.y0+buf.h, buf.y0+buf.h], [1,1,1,1]])
        points = np.concatenate([m @ corners for m in matrices], axis=1)
        points = points[:2]/points[2:]
        lo, hi = np.floor(points.min(1)).astype(int), np.ceil(points.max(1)).astype(int)
        b = Buf.empty(int(lo[0]), int(lo[1]), int(hi[0]-lo[0]), int(hi[1]-lo[1]))
        x, y = grid(b)
        x, y = x+b.x0+.5, y+b.y0+.5
        src = linear_pixels(rc, buf.px)
        for m in matrices:
            m = np.linalg.inv(m)
            if np.allclose(m[2], [0,0,1]):
                local = m.copy()
                local[:2,2] += m[:2,:2] @ np.array([b.x0+.5,b.y0+.5])-np.array([buf.x0+.5,buf.y0+.5])
                b.px += affine_sample(src,local,(b.h,b.w))/count
                continue
            z = m[2,0]*x+m[2,1]*y+m[2,2]
            sx = (m[0,0]*x+m[0,1]*y+m[0,2])/z-buf.x0-.5
            sy = (m[1,0]*x+m[1,1]*y+m[1,2])/z-buf.y0-.5
            b.px += sample(src, sx, sy)/count
        return result(b, working_pixels(rc, b.px))
    except np.linalg.LinAlgError:
        warn_once("effect", "pixel-motion-blur-singular", "singular node matrix; vector blur skipped")
        return buf.copy()


pixel_motion_blur.temporal = True
