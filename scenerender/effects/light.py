"""Linear-light bloom, procedural light artifacts, and 2D illumination.

Lighting uses alpha as a height field for relief. Point/spot positions and
range are in frame document pixels. A point at z=0 has height range/2.
All eight light types illuminate alpha-heightfield normals; area lights integrate
rect/disk/sphere emitter samples, dome integrates the upper hemisphere. This
is a diffuse 2D surface model (no material specular BRDF). Cast shadows march
against alpha*relief, with bias and softness. samples sets light/shadow quality.
Halation models light reflected from the film backing: broad red and narrower
green scatter, suppressed in the bright source, present in opaque dark regions.
Lens flare uses a core, aperture diffraction, colored ghosts and a halo;
light leaks use smooth seeded multi-lobe edge exposure. All additive light is linear.
"""
from __future__ import annotations

import math
import os

import numpy as np

from . import (Params, affine_sampler, center, colored, gaussian, grid, linear_pixels,
               luma, premul, result, sample, smoothstep, straight, working_pixels)
from .. import kernels
from ..raster import color_to_working
from ..registry import EFFECTS, FULL, warn_once


def _linear_color(p, default=(1,1,1,1)):
    return np.asarray(color_to_working(p.rc.ev.color(p.el, "color", p.ctx, default), True), np.float32)


@EFFECTS.register("glow", level=FULL, note="bright pass over threshold (working-space luminance), blurred and added in linear light")
def glow(rc, e, buf, ctx, node):
    """D9 / CONVENTIONS 5.6: the part of each pixel whose luminance exceeds threshold, blurred with
    standard deviation radius, tinted by color and scaled by intensity, is added to the content (as
    bloom, and as the C and Rust renderers do). compositeOriginal none returns the glow alone."""
    return _bloom(rc, e, buf, ctx, alone=Params(rc, e, ctx).s("compositeOriginal", "behind") == "none")


def _bloom(rc, e, buf, ctx, halation=False, alone=False):
    p = Params(rc, e, ctx)
    r = max(0, p.d("radius", 4))
    b = buf.pad(math.ceil(4*r))
    if not halation and not alone and rc.linear and os.environ.get("SCENERENDER_GPU", "1") != "0":
        from .gl_bloom import bloom as gl_bloom
        out = gl_bloom(b.px, r, max(0, p.n("threshold", .7)), max(0, p.n("intensity", 1)), _linear_color(p))
        if out is not None:
            return result(b, out)
    src = linear_pixels(rc, b.px)
    rgb, a = straight(src)
    threshold = max(0, p.n("threshold", .7))
    strength = np.maximum(luma(rgb)-threshold, 0)/np.maximum(luma(rgb), 1e-7)
    bright = src*strength[..., None]
    halo = gaussian(bright, r)*max(0, p.n("intensity", 1))
    tint = _linear_color(p, (1,.2,.04,1) if halation else (1,1,1,1))
    halo[..., :3] *= tint[:3]
    if halation:
        # Backing scatter is transmitted through the emulsion, not the alpha
        # matte: opaque photographs must still show halation around highlights.
        energy = bright[..., :3]
        red = gaussian(energy, max(.01,r))
        green = gaussian(energy, max(.01,r*.45))
        halo[...,0] = red[...,0]*tint[0]*max(0,p.n("intensity",1))
        halo[...,1] = green[...,1]*_linear_color(p,(1,.2,.04,1))[1]*max(0,p.n("intensity",1))
        halo[...,2] *= .25
        halo[..., :3] *= (1-strength)[...,None]
    halo *= tint[3]
    if alone:
        return result(b, working_pixels(rc, halo))
    px = src+halo
    px[..., 3] = np.clip(src[..., 3]+halo[..., 3]*(1-src[..., 3]), 0, 1)
    return result(b, working_pixels(rc, px))


@EFFECTS.register("bloom", level=FULL, note="threshold bright-pass Gaussian added in linear light, preserving HDR")
def bloom(rc, e, buf, ctx, node):
    return _bloom(rc, e, buf, ctx)


@EFFECTS.register("halation", level=FULL, note="bright-pass film-backing red/orange multi-radius scatter, including opaque images")
def halation(rc, e, buf, ctx, node):
    return _bloom(rc, e, buf, ctx, True)


@EFFECTS.register("vignette", level=FULL, note="D9 radial darkening from the frame centre, retaining the input matte")
def vignette(rc, e, buf, ctx, node):
    """D9 / CONVENTIONS 5.8: colour moves toward `color` (black: darkening) by
    amount . smoothstep(r0, r0 + softness, r), with r the distance of the pixel centre from the frame
    centre over the half diagonal and r0 = radius (document pixels) / half diagonal. Defaults: amount
    0.5, radius half the half diagonal, softness 0.5. centerX / centerY (frame document pixels) move
    the centre."""
    p = Params(rc, e, ctx)
    s = rc.scale
    half = 0.5 * math.hypot(rc.doc.width, rc.doc.height)
    cx = p.n("centerX", rc.doc.width / 2) * s - buf.x0
    cy = p.n("centerY", rc.doc.height / 2) * s - buf.y0
    # CONVENTIONS 5.8 gives the vignette its own defaults, in place of the effectType ones the schema
    # declares for every effect (amount 1, radius 4, softness 0.1: a degenerate vignette).
    def own(name, default):
        return p.n(name, default) if rc.ev.explicit(e, name, ctx) else default
    amount, softness = own("amount", .5), max(0.0, own("softness", .5))
    r0 = own("radius", half / 2) / half
    # The falloff depends only on geometry and three numbers: motion-blur samples and still shots reuse it.
    key = (buf.w, buf.h, cx, cy, s, half, amount, r0, softness)
    memo = rc.cache.get("vignette")
    if memo is not None and memo[0] == key:
        w = memo[1]
    else:
        x, y = grid(buf)
        r = np.hypot(x + .5 - cx, y + .5 - cy) / (half * s)
        edge = smoothstep(r0, r0 + softness, r) if softness > 0 else (r >= r0).astype(np.float32)
        w = np.clip(amount * edge, -np.inf, 1).astype(np.float32)[..., None]
        w.flags.writeable = False
        rc.cache["vignette"] = (key, w)
    c = p.color(default=(0,0,0,1))
    if kernels.enabled():
        return result(buf, kernels.vignette(buf.px, w, c))
    rgb, a = straight(buf.px)
    return result(buf, premul(rgb+(c[:3]-rgb)*w*c[3], a))


def _shadow(height, xy, dx, dy, dz, steps, bias, softness):
    """March from each receiver towards the light through the alpha heightfield.

    xy() gives the receiver pixel grids. None means no step can shadow any pixel (visibility 1)."""
    visibility=None
    distance=np.sqrt(dx*dx+dy*dy)
    # Bilinear obstacles (transparent outside the tile) stay within [min(0, lo), max(0, hi)].
    lo,hi=float(np.min(height)),float(np.max(height))
    rise=np.min(dz) if np.ndim(dz) else dz
    # Ray step positions exclude the receiver and emitter themselves.
    for u in (np.arange(steps)+1)/(steps+1):
        # A step whose worst-case clearance is still a full penumbra above the obstacle clips to
        # visible 1 at every pixel: skipping it leaves the minimum unchanged.
        least=lo+rise*u+max(bias,1e-4)-max(0.,hi)
        if least >= max(max(softness,0)*u,1e-4)*(1+1e-6)+1e-9*(abs(rise)+abs(hi)+abs(lo)):
            continue
        if visibility is None:
            visibility=np.ones(height.shape,np.float32)
            x,y=xy()
        obstacle=sample(height[...,None],x+dx*u,y+dy*u)[...,0]
        clearance=height+dz*u+max(bias,1e-4)-obstacle
        penumbra=max(softness,0)*u
        visible=np.clip(clearance/max(penumbra,1e-4),0,1)
        visibility=np.minimum(visibility,visible)
    return visibility


def _light_power(rc, light, lp, ctx):
    """Working-space colour (rgb) times power, as the lighting loop computes them."""
    from .color_science import temperature_rgb
    c=np.asarray(color_to_working(rc.ev.color(light,"color",ctx,(1,1,1,1)),True),np.float32)
    kelvin=lp.n("colorTemperature",0)
    if kelvin:
        c[:3]*=temperature_rgb(kelvin)
    return c[:3]*(max(0,lp.n("intensity",1))*2**np.clip(lp.n("exposure",0),-32,32)*c[3])


def _lighting_fused(rc,p,ids,buf,ctx):
    """Ambient and single-sample directional lights in one kernel pass; None for other light types."""
    height=gaussian(buf.px[...,3:4],max(.3,p.param("soften",.5)*rc.scale))[...,0]*p.d("relief",0)
    count=max(1,min(64,round(p.n("samples",16))))
    lights=[]
    for lid in ids:
        light=rc.doc.ids.get(lid)
        if light is None:
            warn_once("effect-light",lid,"light not found")
            continue
        lp=Params(rc,light,ctx)
        if not rc.ev.bool(light,"affectsDiffuse",ctx,True):
            continue
        kind=lp.s("type","ambient")
        if kind == "ambient":
            lights.append((0,None,_light_power(rc,light,lp,ctx),None))
            continue
        if kind != "directional":
            return None
        yaw,pitch=[math.radians(lp.n(n,0)) for n in ("yaw","pitch")]
        d=np.array([math.sin(yaw)*math.cos(pitch),math.sin(pitch),math.cos(yaw)*math.cos(pitch)])
        visibility=None
        if rc.ev.bool(light,"castShadow",ctx,False) and np.max(height)>0:
            dx,dy,dz=d*max(buf.w,buf.h)*2
            visibility=_shadow(height,lambda:grid(buf),dx,dy,dz,count,
                               lp.d("shadowBias",.0005),lp.d("shadowSoftness",0))
        lights.append((1,d,_light_power(rc,light,lp,ctx),visibility))
    return result(buf,kernels.lighting(buf.px,height,lights,p.n("intensity",1)))


@EFFECTS.register("lighting", level=FULL, note="alpha-relief diffuse normals; all 8 light types, area integration, ray-marched cast shadows, kelvin/color/cone/range")
def lighting(rc,e,buf,ctx,node):
    from .color_science import temperature_rgb
    p=Params(rc,e,ctx)
    ids=(p.s("lights","") or "").split()
    if not ids:
        return buf.copy()
    if rc.linear and kernels.enabled() and buf.w>1 and buf.h>1:
        out=_lighting_fused(rc,p,ids,buf,ctx)
        if out is not None:
            return out
    xx,yy=grid(buf);x,y=xx+buf.x0,yy+buf.y0
    rgb,a=straight(linear_pixels(rc,buf.px))
    height=gaussian(a,max(.3,p.param("soften",.5)*rc.scale))[...,0]*p.d("relief",0)
    gx=np.gradient(height,axis=1) if buf.w>1 else np.zeros_like(height)
    gy=np.gradient(height,axis=0) if buf.h>1 else np.zeros_like(height)
    normal=np.stack([-gx,-gy,np.ones_like(gx)],-1)
    normal/=np.linalg.norm(normal,axis=-1,keepdims=True)
    illumination=np.zeros_like(rgb)
    count=max(1,min(64,round(p.n("samples",16))))
    for lid in ids:
        light=rc.doc.ids.get(lid)
        if light is None:
            warn_once("effect-light",lid,"light not found")
            continue
        lp=Params(rc,light,ctx)
        if not rc.ev.bool(light,"affectsDiffuse",ctx,True):
            continue
        kind=lp.s("type","ambient")
        c=np.asarray(color_to_working(rc.ev.color(light,"color",ctx,(1,1,1,1)),True),np.float32)
        kelvin=lp.n("colorTemperature",0)
        if kelvin:
            c[:3]*=temperature_rgb(kelvin)
        power=max(0,lp.n("intensity",1))*2**np.clip(lp.n("exposure",0),-32,32)*c[3]
        if kind == "ambient":
            illumination+=c[:3]*power
            continue
        yaw,pitch,roll=[math.radians(lp.n(n,0)) for n in ("yaw","pitch","roll")]
        direction=np.array([math.sin(yaw)*math.cos(pitch),math.sin(pitch),math.cos(yaw)*math.cos(pitch)])
        reach=max(lp.d("range",max(rc.doc.width,rc.doc.height)),1e-5)
        z=lp.d("z",0) or reach/2
        samples=[]
        if kind in ("rect-area","disk-area","sphere-area","dome"):
            for i in range(count):
                u=(i+.5)/count;theta=i*math.pi*(3-math.sqrt(5))
                if kind == "rect-area":
                    v=(i*.61803398875) % 1
                    offset=np.array([(u-.5)*lp.d("width",10),(v-.5)*lp.d("height",10),0])
                elif kind == "dome":
                    samples.append(np.array([math.sqrt(1-u*u)*math.cos(theta),math.sqrt(1-u*u)*math.sin(theta),u]))
                    continue
                else:
                    zz=2*u-1 if kind == "sphere-area" else 0
                    rr=math.sqrt(1-zz*zz) if kind == "sphere-area" else math.sqrt(u)
                    offset=np.array([rr*math.cos(theta),rr*math.sin(theta),zz])*lp.d("radius",5)
                # Rotate emitter plane by yaw/pitch/roll.
                cr,sr=math.cos(roll),math.sin(roll)
                cp,sp=math.cos(pitch),math.sin(pitch)
                cy,sy=math.cos(yaw),math.sin(yaw)
                rotation=np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]]) @ np.array([[1,0,0],[0,cp,-sp],[0,sp,cp]]) @ np.array([[cr,-sr,0],[sr,cr,0],[0,0,1]])
                samples.append(rotation @ offset)
        else:
            samples=[np.zeros(3)]
        strength=np.zeros_like(x)
        for offset in samples:
            if kind in ("directional","dome"):
                d=offset if kind == "dome" else direction
                nd=np.maximum(normal @ d,0)
                dx,dy,dz=d*max(buf.w,buf.h)*2
                atten=1
            else:
                dx,dy=lp.d("x",0)+offset[0]-x,lp.d("y",0)+offset[1]-y
                dz=z+offset[2]-height
                dist=np.sqrt(dx*dx+dy*dy+dz*dz)
                to=np.stack([dx,dy,np.broadcast_to(dz,x.shape)],-1)/np.maximum(dist[...,None],1e-7)
                nd=np.maximum(np.sum(normal*to,-1),0)
                u=np.clip(np.hypot(dx,dy)/reach,0,1)
                falloff=p.s("falloff","smooth")
                atten=np.ones_like(u) if falloff == "none" else 1-u if falloff == "linear" else (1-u)**2 if falloff == "quadratic" else 1-smoothstep(0,1,u)
                atten*= (1+(dist/reach)**2)**(-max(0,lp.n("falloff",2))/2)
                if kind == "spot":
                    outer=math.radians(lp.n("spotAngle",45))/2
                    inner=math.radians(lp.n("innerConeAngle",lp.n("spotAngle",45)*.8))/2
                    nd*=smoothstep(math.cos(outer),math.cos(min(inner,outer)),to @ direction)
            visibility=1
            if rc.ev.bool(light,"castShadow",ctx,False) and np.max(height)>0:
                visibility=_shadow(height,lambda:(xx,yy),dx,dy,dz,count,
                                   lp.d("shadowBias",.0005),lp.d("shadowSoftness",0))
                visibility=1 if visibility is None else visibility
            strength+=nd*atten*visibility/len(samples)
        illumination+=strength[...,None]*c[:3]*power
    return result(buf,working_pixels(rc,premul(np.maximum(rgb*illumination*p.n("intensity",1),0),a)))


def _add_light(rc, buf, p, strength, default=(1,1,1,1)):
    src = linear_pixels(rc, buf.px)
    color = _linear_color(p, default)
    light = colored(np.clip(strength*max(0, p.n("intensity", 1)), 0, 1), color)
    out = src+light
    out[..., 3] = src[..., 3]+light[..., 3]*(1-src[..., 3])
    return result(buf, working_pixels(rc, out))


@EFFECTS.register("lens-flare", level=FULL, note="optical core, aperture diffraction star, spectral ghosts and halo; radius/angle/samples/color")
def lens_flare(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    r = max(.1, p.d("radius", 4))
    cx, cy = center(p, buf)
    pad = math.ceil(6*r)
    b = buf.pad(pad)
    x, y = grid(b)
    dx, dy = x-cx-pad, y-cy-pad
    dist = np.hypot(dx, dy)
    light = np.exp(-dist**2/(2*r*r))+.25*np.exp(-(dy/r)**2-(dx/(6*r))**2)
    light += .15*np.exp(-((dist-3*r)/max(.5, r/4))**2)
    theta=np.arctan2(dy,dx)-math.radians(p.n("angle",0))
    blades=max(2,round(p.param("blades",6)))
    light+=.3*np.maximum(np.cos(theta*blades),0)**32*np.exp(-dist/max(r*5,.1))
    for u in np.linspace(.3,1.4,max(1,min(32,round(p.n("samples",16))))):
        gx = cx + ((buf.w-1)/2-cx)*2*u + pad
        gy = cy + ((buf.h-1)/2-cy)*2*u + pad
        light += .12*np.exp(-((x-gx)**2+(y-gy)**2)/(2*r*r))
    return _add_light(rc, b, p, light)


@EFFECTS.register("light-leak", level=FULL, note="seeded evolving multi-lobe warm edge exposure with angle/radius/speed and linear addition")
def light_leak(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    phase = p.rng(False).uniform(0, 2*math.pi)
    angle = math.radians(p.n("angle", 0))+phase
    r = max(.1, p.d("radius", 4))
    edge = x*math.cos(angle)+y*math.sin(angle)
    edge -= edge.min()
    from .fields import fractal
    time=ctx.t*p.n("speed",1)
    along=-x*math.sin(angle)+y*math.cos(angle)
    seed=rc.ev.seed_for(e,str(p.n("seed",0)))
    lobes=fractal(along/max(r*4,1),edge/max(r*8,1),seed,4,time*.2)
    w=np.exp(-edge/(r*(1+5*lobes)))*(.65+.35*math.sin(time+phase))
    return _add_light(rc, buf, p, w, (1,.35,.08,1))


@EFFECTS.register("light-sweep", level=FULL, note="moving angled Gaussian light band, clipped to the input alpha")
def light_sweep(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    cx, cy = center(p, buf)
    angle = math.radians(p.n("angle", 0))
    d = (x-cx)*math.cos(angle)+(y-cy)*math.sin(angle)-ctx.t*p.d("speed", 1)
    strength = np.exp(-.5*(d/max(.1, p.d("radius", 4)))**2)*buf.px[..., 3]
    return _add_light(rc, buf, p, strength)


@EFFECTS.register("god-rays", level=FULL, note="occlusion-aware radial scattering from center; bright emitters, alpha/dark blockers, exponential extinction")
def god_rays(rc,e,buf,ctx,node):
    """Integrate radiance towards centerX/Y. Dark covered pixels block light;
    bright covered pixels emit. radius is mean scattering length in document
    pixels, amount is optical density, samples controls integration quality.
    Optional source is an explicit blocker matte in its own frame position.
    """
    from . import source_buf
    p=Params(rc,e,ctx)
    radius=max(.01,p.d("radius",4))
    b=buf.pad(math.ceil(radius*4))
    cx,cy=center(p,buf);cx,cy=cx+buf.x0-b.x0,cy+buf.y0-b.y0
    x,y=grid(b)
    src=linear_pixels(rc,b.px)
    rgb,a=straight(src)
    emission=np.clip((luma(rgb)-p.n("threshold",.7))/max(1-p.n("threshold",.7),1e-5),0,None)*a[...,0]
    blocker=a[...,0]*(1-np.clip(emission,0,1))
    plate=source_buf(rc,e,ctx,node) if p.s("source") else None
    if plate is not None:
        blocker=plate.region(b.rect)[...,3]
    n=max(1,min(256,round(p.n("samples",16))))
    distance=np.hypot(cx-x,cy-y)
    step=distance/n
    transmission=np.ones_like(x)
    energy=np.zeros_like(x)
    density=max(0,p.n("amount",1))
    field=np.stack([blocker,emission],-1)
    warp=affine_sampler(field)
    for i in range(1,n+1):
        u=i/n
        fields=warp([[1-u,0,cx*u],[0,1-u,cy*u]])
        occ=fields[...,0]
        transmission*=np.exp(-occ*step*density/rc.scale)
        light=fields[...,1]
        energy+=transmission*light*np.exp(-distance*u/(radius*4))/n
    return _add_light(rc,b,p,energy)
