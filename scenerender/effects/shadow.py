"""Alpha-based layer styles.

compositeOriginal describes the original's placement relative to the style:
behind (default), on-top, or none. Position selects outside/inside/centred
rings for stroke/outline/bevel. Shadow offsets and radii are document pixels.
"""
from __future__ import annotations

import math

import numpy as np

from . import (Params, colored, composite, gaussian, morphology, result, shifted)
from ..registry import EFFECTS, FULL


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


@EFFECTS.register("bevel", level=FULL, note="inner/outer/emboss/pillow alpha-relief profile, size/soften, angle and relief")
def bevel(rc,e,buf,ctx,node):
    """position inside/outside/center = inner/outer/emboss; style=pillow reverses
    the inside slope. size (radius fallback via size param) is bevel width;
    soften param is Gaussian sigma, relief is height (0 selects unit slope).
    color/keyColor tint highlight/shadow; compositeOriginal locates the source.
    """
    p=Params(rc,e,ctx)
    r=max(.01,p.param("size",p.n("radius",4))*rc.scale)
    position=p.s("position","outside")
    style=p.param("style",{"inside":"inner","outside":"outer","center":"emboss"}[position])
    soften=max(0,p.param("soften",0)*rc.scale)
    b=buf.pad(math.ceil(r+4*soften+2))
    alpha=b.px[...,3]
    inner=alpha-morphology(alpha,r,False)
    outer=morphology(alpha,r)-alpha
    ring=inner if style == "inner" else outer if style == "outer" else np.maximum(inner,outer)
    height=gaussian(alpha[...,None],max(.3,r/2))[...,0]
    if style == "pillow":
        height=np.where(alpha>.5,1-height,height)
    if soften:
        height=gaussian(height[...,None],soften)[...,0]
    gy,gx=np.gradient(height)
    angle=math.radians(p.n("angle",0))
    relief=p.d("relief",0) or r
    v=(gx*math.cos(angle)+gy*math.sin(angle))*relief*p.n("intensity",1)
    light=colored(ring*np.clip(v,0,1),p.color())
    dark=colored(ring*np.clip(-v,0,1),p.color("keyColor",(0,0,0,1)))
    out=result(b,composite(p,b.px,light+dark))
    return out.crop_to(buf.rect) if style == "inner" else out


def _extrusion_offsets(dx,dy):
    """Exact supercover of a line on the pixel lattice (no skipped raster cells)."""
    times=[0.,1.]
    for d in (dx,dy):
        if abs(d)>1e-7:
            times.extend(((np.arange(math.floor(abs(d)+.5))+.5)/abs(d)).tolist())
    times=np.unique(np.clip(times,0,1))
    for t in np.r_[times,(times[:-1]+times[1:])/2]:
        yield round(dx*t),round(dy*t),t


@EFFECTS.register("long-shadow", level=FULL, note="exact raster-line alpha extrusion by angle/length, optional exponential fade")
def long_shadow(rc,e,buf,ctx,node):
    """radius is length, or param length; param fade is end-to-start log falloff.
    Raster semantics: max alpha over every pixel cell traversed by the extrusion
    segment. No samples cap: long shadows remain connected at any angle/length.
    """
    p=Params(rc,e,ctx)
    length=max(0,p.param("length",p.n("radius",4))*rc.scale)
    angle=math.radians(p.n("angle",0))
    dx,dy=length*math.cos(angle),length*math.sin(angle)
    b=buf.pad(math.ceil(max(abs(dx),abs(dy))))
    alpha=np.zeros(b.px.shape[:2],np.float32)
    source=b.px[...,3]
    fade=max(0,p.param("fade",0))
    for ix,iy,t in _extrusion_offsets(dx,dy):
        x0,x1=max(0,ix),min(b.w,b.w+ix)
        y0,y1=max(0,iy),min(b.h,b.h+iy)
        dest=alpha[y0:y1,x0:x1]
        np.maximum(dest,source[y0-iy:y1-iy,x0-ix:x1-ix]*math.exp(-fade*t),out=dest)
    return result(b,composite(p,b.px,colored(alpha*max(0,p.n("intensity",1)),p.color(default=(0,0,0,1)))))
