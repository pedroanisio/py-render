"""Raster styles and temporal sampling.

Film grain is linear-light, separate emulsion channels with a bell-shaped
exposure response (params red/green/blue strength, response exponent).
Chromatic aberration: amount is lateral red/blue excursion at the far edge;
longitudinal param is defocus sigma, channelScaleR/G/B and focusR/G/B tune it.
Glitch: seeded block corruption, tearing, channel delay and quantisation.
VHS: YIQ chroma bandwidth/delay, timebase jitter, head switching and RF noise.
Halftone: screen=mono/cmyk, shape=round/line/square, channel angles C/M/Y/K
(default 15/75/0/45 degrees) plus angle; size is screen period.
Echo: samples copies including current, interval seconds (default 1/frequency),
decay (default exp(-amount)), operator over/under/add/maximum/minimum/screen.
Posterize-time samples all node pixels/properties at floor(t*frequency)/frequency.
Temporal samples skip the node effect stack, as specified by render_node_at.
They contain pre-opacity pixels: node opacity/visibility are compositor-level
operations. Holding node opacity or retaining echoes after current-time culling
requires the compositor changes described in the work-order return notes.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from . import (Params, affine_sample, center, display_rgb, from_display, gaussian, grid, luma,
               over, premul, result, sample, shifted, straight)
from .. import kernels
from ..registry import EFFECTS, FULL


@EFFECTS.register("noise", level=FULL, note="seeded per-frame uniform RGB or monochrome noise in encoded sRGB")
def noise(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    channels = 3 if p.s("channel", "rgb") == "rgb" else 1
    random = p.rng().uniform(-1, 1, rgb.shape[:2]+(channels,))
    return from_display(rc, buf, rgb+random*p.n("amount", 1), a)


def _grain_field(rc, p, shape, sigma):
    """Normalized grain for this frame; motion-blur samples of one frame share it, so it is kept."""
    from ..noise import Rng
    seed = rc.ev.seed_for(p.el, str(p.n("seed", 0)))
    key = (seed, p.ctx.frame, shape, sigma)
    memo = rc.cache.get("film-grain")
    if memo is not None and memo[0] == key:
        return memo[1]
    grain = Rng(seed, p.ctx.frame).standard_normal(shape).astype(np.float32)        # D24 draws, one per sample
    grain = gaussian(grain,sigma)
    # Analytic normalization avoids frame-content dependent grain strength.
    grain *= max(1,2*math.sqrt(math.pi)*sigma)
    grain.flags.writeable = False
    rc.cache["film-grain"] = (key, grain)
    return grain


@EFFECTS.register("film-grain", level=FULL, note="linear-light emulsion grain, per-channel exposure response, size and deterministic temporal seed")
def film_grain(rc, e, buf, ctx, node):
    from . import linear_pixels, working_pixels
    p = Params(rc, e, ctx)
    sigma = max(0,(p.d("size",1)-1)/2)
    if rc.linear and kernels.enabled():
        grain = _grain_field(rc, p, buf.px.shape[:2]+(3,), sigma)
        strength = np.array([p.param(c,1) for c in ("red","green","blue")],np.float32)
        return result(buf,kernels.grain(buf.px,grain,.05*p.n("amount",1),max(.01,p.param("response",.5)),strength))
    rgb, a = straight(linear_pixels(rc,buf.px))
    grain = _grain_field(rc, p, rgb.shape, sigma)
    v = np.clip(rgb,0,1)
    weight = np.maximum(4*v*(1-v),0)**max(.01,p.param("response",.5))
    strength = np.array([p.param(c,1) for c in ("red","green","blue")],np.float32)
    rgb = np.maximum(rgb+grain*.05*p.n("amount",1)*weight*strength,0)
    return result(buf,working_pixels(rc,premul(rgb,a)))


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


@EFFECTS.register("chromatic-aberration", level=FULL, note="lateral per-channel radial magnification and longitudinal wavelength defocus")
def chromatic_aberration(rc, e, buf, ctx, node):
    p = Params(rc,e,ctx)
    cx, cy = center(p,buf)
    reach = max(math.hypot(cx,cy),math.hypot(buf.w-cx,buf.h-cy),1)
    lateral = p.d("amount",1)/reach
    longitudinal = max(0,p.param("longitudinal",0)*rc.scale)
    scales = [p.param("channelScale"+c,1+sign*lateral) for c,sign in zip("RGB",(-1,0,1))]
    sigmas = [abs(p.param("focus"+c,f))*longitudinal for c,f in zip("RGB",(1,0,.7))]
    pad = math.ceil(max(abs(v-1) for v in scales)*reach+4*max(sigmas))
    b=buf.pad(pad)
    cx,cy=cx+pad,cy+pad
    channels=[]
    for i,(scale,sigma) in enumerate(zip(scales,sigmas)):
        # Each pass keeps only its own colour channel and alpha (channels blur and sample independently).
        # Radial magnification about the centre is affine: output (x, y) reads c + (x - c) / scale.
        src=gaussian(b.px[...,(i,3)],sigma)
        k=1/max(scale,.01)
        channels.append(affine_sample(src,[[k,0,cx*(1-k)],[0,k,cy*(1-k)],[0,0,1]]))
    return result(b,np.stack([c[...,0] for c in channels]+
                            [np.maximum.reduce([c[...,1] for c in channels])],-1))


def _glitch(p, buf):
    x, y = grid(buf)
    size = max(1, round(p.d("size", 1)))
    rng = p.rng()
    rows = rng.uniform(-1, 1, math.ceil(buf.h/size))
    rows *= rng.random(len(rows)) < .25
    shifts = np.repeat(rows, size)[:buf.h, None]*p.d("amount", 1)
    return sample(buf.px, x+shifts, y)


@EFFECTS.register("glitch", level=FULL, note="seeded block swaps, scanline tearing, channel delay and codec quantisation")
def glitch(rc, e, buf, ctx, node):
    p=Params(rc,e,ctx)
    amount=p.d("amount",1)
    if amount == 0:
        return buf.copy()
    px=_glitch(p,buf)
    rng=p.rng()
    count=max(1,round(p.param("blocks",8)))
    for _ in range(min(count,256)):
        w=max(1,min(buf.w,round(rng.uniform(.03,.2)*buf.w)))
        h=max(1,min(buf.h,round(p.d("size",1)*rng.uniform(1,4))))
        x,y=rng.integers(0,buf.w-w+1),rng.integers(0,buf.h-h+1)
        sx,sy=rng.integers(0,buf.w-w+1),rng.integers(0,buf.h-h+1)
        weight=min(1,abs(p.n("amount",1)))
        px[y:y+h,x:x+w]=(1-weight)*px[y:y+h,x:x+w]+weight*buf.px[sy:sy+h,sx:sx+w]
    dx=amount*p.param("channelDelay",.5)
    red,blue=shifted(px,dx,0),shifted(px,-dx,0)
    px=np.stack([red[...,0],px[...,1],blue[...,2],np.maximum.reduce([red[...,3],px[...,3],blue[...,3]])],-1)
    rgb,a=straight(px)
    levels=max(2,round(p.param("quantization",32)))
    return result(buf,premul(np.round(rgb*(levels-1))/(levels-1),a))


@EFFECTS.register("scanlines", level=FULL, note="frame-aligned periodic dark lines controlled by size/frequency/amount")
def scanlines(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    period = max(1, 2*p.d("size", 1))
    wave = .5+.5*np.cos((y+buf.y0)*2*math.pi/period*p.n("frequency", 1))
    px = buf.px.copy()
    px[..., :3] *= (1-wave*np.clip(p.n("amount", 1), 0, 1)*.5)[..., None]
    return result(buf, px)


@EFFECTS.register("vhs", level=FULL, note="YIQ chroma bandwidth/delay, timebase jitter, head-switching noise and scanlines")
def vhs(rc,e,buf,ctx,node):
    p=Params(rc,e,ctx)
    amount=max(0,p.n("amount",1))
    if amount == 0:
        return buf.copy()
    x,y=grid(buf)
    rng=p.rng()
    jitter=rng.normal(0,.4,(buf.h,1))*rc.scale*amount
    band=((y+ctx.t*p.d("speed",1)*12) % max(buf.h,1)) > .94*buf.h
    jitter=jitter+band*np.sin(y*1.9)*amount*6*rc.scale
    px=sample(buf.px,x+jitter,y)
    rgb,a=display_rgb(rc,px)
    m=np.array([[.299,.587,.114],[.596,-.274,-.322],[.211,-.523,.312]],np.float32)
    yiq=rgb@m.T
    sigma=max(.01,p.d("radius",4))*amount
    pad=math.ceil(4*sigma)
    bleed=gaussian(np.pad(yiq,((0,0),(pad,pad),(0,0)),mode="edge"),sigma,0)[:,pad:-pad]
    delay=p.param("chromaDelay",2)*rc.scale*amount
    bleed=sample(bleed,x-delay,y,"clamp")
    yiq[...,1:]=bleed[...,1:]
    yiq[...,0]+=rng.normal(0,.018,yiq.shape[:2])*amount*(1+3*band)
    yiq[...,0]*=1-.08*amount*(.5+.5*np.cos((y+buf.y0)*math.pi/max(rc.scale,1e-5)))
    return from_display(rc,buf,yiq@np.linalg.inv(m).T,a)


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


@EFFECTS.register("halftone", level=FULL, note="mono/CMYK screens with separate angles; round/line/square area-calibrated dots")
def halftone(rc,e,buf,ctx,node):
    p=Params(rc,e,ctx)
    rgb,a=display_rgb(rc,buf.px)
    x,y=grid(buf);x,y=x+buf.x0,y+buf.y0
    size=max(1,p.d("size",1))
    shape=p.param("shape","round")
    def screen(ink,angle):
        angle=math.radians(angle+p.n("angle",0))
        u=(x*math.cos(angle)+y*math.sin(angle))/size
        v=(-x*math.sin(angle)+y*math.cos(angle))/size
        # Sample tone at each rotated screen cell's centre.
        cu,cv=np.floor(u)+.5,np.floor(v)+.5
        sx=(cu*math.cos(angle)-cv*math.sin(angle))*size-buf.x0
        sy=(cu*math.sin(angle)+cv*math.cos(angle))*size-buf.y0
        tone=sample(ink[...,None],sx,sy,"clamp")[...,0]
        uu,vv=abs(u%1-.5),abs(v%1-.5)
        if shape == "line":
            distance=uu;radius=tone/2
        elif shape == "square":
            distance=np.maximum(uu,vv);radius=np.sqrt(tone)/2
        else:
            # High coverages invert the remaining round paper holes.
            distance=np.where(tone<=.5,np.hypot(uu,vv),np.hypot(.5-uu,.5-vv))
            radius=np.sqrt(np.minimum(tone,1-tone)/math.pi)
        coverage=np.clip((radius-distance)*size+.5,0,1)
        coverage = np.where(tone>.5,1-coverage,coverage) if shape == "round" else coverage
        return np.where(tone <= 1e-6, 0, np.where(tone >= 1-1e-6, 1, coverage))
    if p.param("screen","mono") == "cmyk":
        black=1-rgb.max(-1)
        cmy=(1-rgb-black[...,None])/np.maximum(1-black[...,None],1e-6)
        inks=[screen(cmy[...,i],p.param("angle"+c,d)) for i,(c,d) in enumerate(zip("CMY",(15,75,0)))]
        k=screen(black,p.param("angleK",45))
        out=(1-np.stack(inks,-1))*(1-k[...,None])
    else:
        out=np.repeat((1-screen(1-luma(rgb),0))[...,None],3,-1)
    return from_display(rc,buf,out,a)


@EFFECTS.register("emboss", level=FULL, note="directional luminance derivative about middle grey, retaining alpha")
def emboss(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    y = luma(rgb)[..., None]
    angle = math.radians(p.n("angle", 0))
    radius = p.d("radius", 4)
    diff = shifted(y, radius*math.cos(angle), radius*math.sin(angle))-shifted(y, -radius*math.cos(angle), -radius*math.sin(angle))
    return from_display(rc, buf, np.repeat(.5+diff*p.n("amount", 1), 3, -1), a)


@EFFECTS.register("echo", level=FULL, note="true pre-opacity temporal samples via render_node_at, interval/decay and six compositing operators")
def echo(rc,e,buf,ctx,node):
    from ..raster import Buf, union
    p=Params(rc,e,ctx)
    if node is None:
        return buf.copy()
    n=max(1,min(128,round(p.n("samples",16))))
    interval=p.param("interval",1/max(abs(p.n("frequency",1)),1e-6))
    decay=np.clip(p.param("decay",math.exp(-max(0,p.n("amount",1)))),0,1)
    operator=p.param("operator","over")
    copies=[(buf,1.)]
    rect=buf.rect
    for i in range(1,n):
        time=ctx.comp_t-i*interval
        sampled=rc.ev.context_at(node,ctx,time)
        if not rc.active(node,sampled):
            continue
        out=rc.render_node_at(node,time,sampled)
        if out is not None:
            copies.append((out.buf,decay**i))
            rect=union(rect,out.buf.rect)
    acc=np.zeros((rect[3]-rect[1],rect[2]-rect[0],4),np.float32)
    for b,weight in reversed(copies):
        px=b.region(rect)*weight
        if operator == "under":
            acc=over(acc,px)
        elif operator == "add":
            acc+=px;acc[...,3]=np.clip(acc[...,3],0,1)
        elif operator == "maximum":
            acc=np.maximum(acc,px)
        elif operator == "minimum":
            acc=px if b is copies[-1][0] else np.minimum(acc,px)
        elif operator == "screen":
            acc=acc+px-acc*px
        else:
            acc=over(px,acc)
    return Buf(acc,rect[0],rect[1])


@EFFECTS.register("posterize-time", level=FULL, note="render_node_at holds pixels/transforms/child properties at quantized local time; compositor opacity remains separate")
def posterize_time(rc,e,buf,ctx,node):
    from ..raster import Buf
    p=Params(rc,e,ctx)
    if node is None:
        return buf.copy()
    frequency=max(1e-6,p.n("frequency",1))
    held=math.floor(ctx.t*frequency)/frequency
    sampled=rc.ev.context_at_local(node,ctx,held)
    out=rc.render_node_at(node,sampled.comp_t,sampled,local_time=held)
    return out.buf.copy() if out is not None else Buf.empty(buf.x0,buf.y0,buf.w,buf.h)


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


def _echo_lookback(rc, e, ctx) -> float:
    """How long after a node's window ends its echo tail stays visible."""
    p = Params(rc, e, ctx)
    n = max(1, min(128, round(p.n("samples", 16))))
    return (n - 1) * p.param("interval", 1 / max(abs(p.n("frequency", 1)), 1e-6))


def _echo_ghost_active(rc, e, node, ctx) -> bool:
    p = Params(rc, e, ctx)
    n = max(1, min(128, round(p.n("samples", 16))))
    interval = p.param("interval", 1 / max(abs(p.n("frequency", 1)), 1e-6))
    return any(rc.active(node, rc.ev.context_at(node, ctx, ctx.comp_t - i * interval))
               for i in range(1, n))


echo.temporal = True
echo.lookback = _echo_lookback
echo.ghost_active = _echo_ghost_active
posterize_time.temporal = True
