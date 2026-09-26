"""Linear-light bloom, procedural light artifacts, and 2D illumination.

Lighting uses alpha as a height field for relief. Point/spot positions and
range are in frame document pixels. A point at z=0 is given a height of half
its range for useful 2D lighting. Area/dome lights approximate point/ambient;
shadows, environment maps and specular transport are outside this 2D model.
"""
from __future__ import annotations

import math

import numpy as np

from . import (Params, center, colored, composite, gaussian, grid, linear_pixels,
               luma, premul, result, sample, smoothstep, straight, working_pixels)
from ..raster import color_to_working
from ..registry import EFFECTS, FULL, PARTIAL, warn_once


def _linear_color(p, default=(1,1,1,1)):
    return np.asarray(color_to_working(p.rc.ev.color(p.el, "color", p.ctx, default), True), np.float32)


@EFFECTS.register("glow", level=FULL, note="tinted Gaussian alpha glow, padded spill, original placement control")
def glow(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    r = max(0, p.d("radius", 4))
    b = buf.pad(math.ceil(4*r))
    alpha = gaussian(b.px[..., 3:4], r)[..., 0]*max(0, p.n("intensity", 1))
    px = composite(p, linear_pixels(rc, b.px), colored(alpha, _linear_color(p)))
    return result(b, working_pixels(rc, px))


def _bloom(rc, e, buf, ctx, halation=False):
    p = Params(rc, e, ctx)
    r = max(0, p.d("radius", 4))
    b = buf.pad(math.ceil(4*r))
    src = linear_pixels(rc, b.px)
    rgb, a = straight(src)
    threshold = max(0, p.n("threshold", .7))
    strength = np.maximum(luma(rgb)-threshold, 0)/np.maximum(luma(rgb), 1e-7)
    bright = src*strength[..., None]
    halo = gaussian(bright, r)*max(0, p.n("intensity", 1))
    halo[..., :3] *= _linear_color(p, (1,.2,.04,1) if halation else (1,1,1,1))[:3]
    if halation:
        halo *= 1-a
    px = src+halo
    px[..., 3] = np.clip(src[..., 3]+halo[..., 3]*(1-src[..., 3]), 0, 1)
    return result(b, working_pixels(rc, px))


@EFFECTS.register("bloom", level=FULL, note="threshold bright-pass Gaussian added in linear light, preserving HDR")
def bloom(rc, e, buf, ctx, node):
    return _bloom(rc, e, buf, ctx)


@EFFECTS.register("halation", level=PARTIAL, note="red-tinted bright-pass Gaussian outside the input matte")
def halation(rc, e, buf, ctx, node):
    return _bloom(rc, e, buf, ctx, True)


@EFFECTS.register("vignette", level=FULL, note="elliptical radial colour falloff, retaining the input matte")
def vignette(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    cx, cy = center(p, buf)
    d = np.hypot((x-cx)/max(buf.w/2, 1), (y-cy)/max(buf.h/2, 1))
    w = np.clip(smoothstep(np.clip(p.n("threshold", .7), 0, 1), 1.4, d)*p.n("intensity", 1), 0, 1)[..., None]
    rgb, a = straight(buf.px)
    c = p.color(default=(0,0,0,1))
    return result(buf, premul(rgb+(c[:3]-rgb)*w*c[3], a))


@EFFECTS.register("lighting", level=PARTIAL, note="2D ambient/point/spot/directional diffuse light and alpha relief; no cast shadows")
def lighting(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    ids = (p.s("lights", "") or "").split()
    if not ids:
        return buf.copy()
    x, y = grid(buf)
    x, y = x+buf.x0, y+buf.y0
    rgb, a = straight(linear_pixels(rc, buf.px))
    height = gaussian(a, 1)[..., 0]*p.d("relief", 0)
    # np.gradient needs two samples per axis; support 1x1 empty/small tiles.
    gx = np.gradient(height, axis=1) if buf.w > 1 else np.zeros_like(height)
    gy = np.gradient(height, axis=0) if buf.h > 1 else np.zeros_like(height)
    normal = np.stack([-gx, -gy, np.ones_like(gx)], -1)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    illumination = np.zeros_like(rgb)
    for lid in ids:
        light = rc.doc.ids.get(lid)
        if light is None:
            warn_once("effect-light", lid, "light not found")
            continue
        lp = Params(rc, light, ctx)
        kind = lp.s("type", "ambient")
        if not rc.ev.bool(light, "affectsDiffuse", ctx, True):
            continue
        c = np.asarray(color_to_working(rc.ev.color(light, "color", ctx, (1,1,1,1)), True), np.float32)
        power = max(0, lp.n("intensity", 1))*np.exp2(np.clip(lp.n("exposure", 0), -32, 32))*c[3]
        if kind in ("ambient", "dome"):
            strength = np.ones_like(x)
        else:
            yaw, pitch = math.radians(lp.n("yaw", 0)), math.radians(lp.n("pitch", 0))
            direction = np.asarray([math.sin(yaw)*math.cos(pitch), math.sin(pitch), math.cos(yaw)*math.cos(pitch)])
            if kind == "directional":
                strength = np.maximum(normal @ direction, 0)
            else:
                reach = max(lp.d("range", max(rc.doc.width, rc.doc.height)), 1e-5)
                z = lp.d("z", 0)
                z = z if abs(z) > 1e-5 else reach/2
                delta = np.stack([lp.d("x", 0)-x, lp.d("y", 0)-y, z-height], -1)
                dist = np.linalg.norm(delta, axis=-1)
                direction_to = delta/np.maximum(dist[..., None], 1e-7)
                flat_dist = np.hypot(delta[..., 0], delta[..., 1])
                u = np.clip(flat_dist/reach, 0, 1)
                falloff = p.s("falloff", "smooth")
                if falloff == "none":
                    atten = np.ones_like(u)
                elif falloff == "linear":
                    atten = 1-u
                elif falloff == "quadratic":
                    atten = (1-u)**2
                else:
                    atten = 1-smoothstep(0, 1, u)
                atten *= (1+(dist/reach)**2)**(-max(0, lp.n("falloff", 2))/2)
                strength = np.maximum(np.sum(normal*direction_to, -1), 0)*atten
                if kind == "spot":
                    outer = math.radians(lp.n("spotAngle", 45))/2
                    inner = math.radians(lp.n("innerConeAngle", lp.n("spotAngle", 45)*.8))/2
                    cos_angle = direction_to @ direction
                    strength *= smoothstep(math.cos(outer), math.cos(min(inner, outer)), cos_angle)
        illumination += strength[..., None]*c[:3]*power
    px = premul(np.clip(rgb*illumination*max(0, p.n("intensity", 1)), 0, 1), a)
    return result(buf, working_pixels(rc, px))


def _add_light(rc, buf, p, strength, default=(1,1,1,1)):
    src = linear_pixels(rc, buf.px)
    color = _linear_color(p, default)
    light = colored(np.clip(strength*max(0, p.n("intensity", 1)), 0, 1), color)
    out = src+light
    out[..., 3] = src[..., 3]+light[..., 3]*(1-src[..., 3])
    return result(buf, working_pixels(rc, out))


@EFFECTS.register("lens-flare", level=PARTIAL, note="procedural Gaussian core, streak, ring and three lens ghosts")
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
    for u in (.4, .8, 1.2):
        gx = cx + ((buf.w-1)/2-cx)*2*u + pad
        gy = cy + ((buf.h-1)/2-cy)*2*u + pad
        light += .12*np.exp(-((x-gx)**2+(y-gy)**2)/(2*r*r))
    return _add_light(rc, b, p, light)


@EFFECTS.register("light-leak", level=PARTIAL, note="seeded moving warm edge illumination; procedural film leak approximation")
def light_leak(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    phase = p.rng(False).uniform(0, 2*math.pi)
    angle = math.radians(p.n("angle", 0))+phase
    r = max(.1, p.d("radius", 4))
    edge = x*math.cos(angle)+y*math.sin(angle)
    edge -= edge.min()
    w = np.exp(-edge/(4*r))*(.65+.35*math.sin(ctx.t*p.n("speed", 1)+phase))
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


@EFFECTS.register("god-rays", level=PARTIAL, note="radial bright-pass accumulation towards centre, without scene occlusion")
def god_rays(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    r = max(0, p.d("radius", 4))
    b = buf.pad(math.ceil(r*4))
    cx, cy = center(p, buf)
    cx, cy = cx+buf.x0-b.x0, cy+buf.y0-b.y0
    x, y = grid(b)
    src = linear_pixels(rc, b.px)
    rgb, _ = straight(src)
    bright = src*np.maximum(luma(rgb)-p.n("threshold", .7), 0)[..., None]
    rays = np.zeros_like(src)
    n = max(1, min(256, round(p.n("samples", 16))))
    reach = min(1, r*4/max(b.w, b.h))
    for u in (np.arange(n)+1)/n:
        rays += sample(bright, x+(cx-x)*u*reach, y+(cy-y)*u*reach)*(1-u/2)/n
    rays *= max(0, p.n("intensity", 1))
    out = src+rays
    out[..., 3] = np.clip(src[..., 3]+rays[..., 3]*(1-src[..., 3]), 0, 1)
    return result(b, working_pixels(rc, out))
