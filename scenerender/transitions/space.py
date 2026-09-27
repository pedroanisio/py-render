"""Perspective transitions: flip, cube, carousel (planar homographies of rotated pictures seen
by a pinhole camera) and page-curl (cylindrical curl).

Directions snap to the nearest axis; up/down run the horizontal code on the
transposed frame.
"""
from __future__ import annotations

import math

import numpy as np

from ..registry import FULL, TRANSITIONS
from . import (_Transposed, arrays, axis_direction, axis_field, direction, extent, facing,
               out, over, param, plane_homography, sample, grid, warp_homography)


def _oriented(rc, tr, ctx, a, b, fn, *args):
    """Run fn(rc2, A, B, sign, *args) horizontally; sign=+1 when the incoming comes from the right."""
    A, B = arrays(rc, a, b)
    ad = axis_direction(rc, tr, ctx)
    sign = 1.0 if ad in ("left", "up") else -1.0
    if ad in ("left", "right"):
        return out(fn(rc, A, B, sign, *args))
    rt = _Transposed(rc)
    res = fn(rt, A.transpose(1, 0, 2), B.transpose(1, 0, 2), sign, *args)
    return out(np.ascontiguousarray(res.transpose(1, 0, 2)))


def _draw_planes(rc, planes, cam):
    """planes: [(px, yaw, pos)]; draws visible planes far to near."""
    w, h = rc.width, rc.height
    acc = np.zeros((h, w, 4), np.float32)
    for px, yaw, pos in sorted(planes, key=lambda t: -t[2][2]):
        if not facing(yaw, pos, cam):
            continue
        H = plane_homography(px.shape[1], px.shape[0], yaw, pos, cam, (w / 2, h / 2))
        acc = over(warp_homography(rc, px, H), acc)
    return acc


def _shade(px, yaw, k=0.35):
    """Darken a turned face a little (simple Lambert-ish term)."""
    f = 1 - k * (1 - abs(math.cos(math.radians(yaw))))
    res = px.copy()
    res[..., :3] *= f
    return res


def _cube(rc, A, B, sign, p):
    w = rc.width
    R = w / 2
    cam = 2.0 * w
    tz = math.sin(math.pi * p) * 0.35 * w
    planes = []
    for px, yaw in ((A, -sign * 90 * p), (B, sign * 90 * (1 - p))):
        r = math.radians(yaw)
        pos = (R * math.sin(r), 0.0, R - R * math.cos(r) + tz)
        planes.append((_shade(px, yaw), yaw, pos))
    return _draw_planes(rc, planes, cam)


@TRANSITIONS.register("cube", level=FULL, note="rotating cube in perspective (camera at 2x frame width)")
def cube(rc, tr, a, b, p, ctx):
    return _oriented(rc, tr, ctx, a, b, _cube, p)


def _flip(rc, A, B, sign, p):
    w = rc.width
    cam = 2.0 * w
    tz = math.sin(math.pi * p) * 0.4 * w
    yaw = -sign * 180 * p
    if p < 0.5:
        return _draw_planes(rc, [(_shade(A, yaw), yaw, (0.0, 0.0, tz))], cam)
    yb = yaw + sign * 180
    return _draw_planes(rc, [(_shade(B, yb), yb, (0.0, 0.0, tz))], cam)


@TRANSITIONS.register("flip", level=FULL, note="card flip about the centre line in perspective; the incoming picture is the back")
def flip(rc, tr, a, b, p, ctx):
    return _oriented(rc, tr, ctx, a, b, _flip, p)


def _carousel(rc, A, B, sign, p, n):
    w = rc.width
    step = 360.0 / n
    R = 1.15 * w / (2 * math.tan(math.pi / n))
    cam = 2.0 * w
    tz = math.sin(math.pi * p) * 0.6 * w
    planes = []
    for px, yaw in ((A, -sign * step * p), (B, sign * step * (1 - p))):
        r = math.radians(yaw)
        pos = (R * math.sin(r), 0.0, R - R * math.cos(r) + tz)
        planes.append((_shade(px, yaw, 0.5), yaw, pos))
    return _draw_planes(rc, planes, cam)


@TRANSITIONS.register("carousel", level=FULL,
                      note="pictures as panels of a rotating carousel (<param name='panels'>, default 6) that pulls back mid-turn")
def carousel(rc, tr, a, b, p, ctx):
    return _oriented(rc, tr, ctx, a, b, _carousel, p, max(3, int(param(tr, "panels", 6))))


@TRANSITIONS.register("page-curl", level=FULL,
                      note="orthographic cylindrical curl lifting the outgoing page from its trailing edge along @direction; <param name='radius'> (fraction of travel, default 0.1)")
def page_curl(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    d = direction(rc, tr, ctx)
    E = extent(rc, d)
    r = max(2.0, param(tr, "radius", 0.1) * E)
    xs, ys = grid(rc)
    u = E * (1 - axis_field(rc, d))           # 0 at the leading edge, E at the lifted edge
    c = E - p * (E + r)                        # curl axis position

    def at(usrc):
        du = usrc - u
        return sample(A, xs - du * d[0], ys - du * d[1])

    res = B.copy()
    # Shadow cast by the curl onto the incoming picture.
    beyond = np.clip((u - (c + r)) / (0.8 * r), 0, None)
    shadow = 1 - 0.45 * math.sqrt(math.sin(math.pi * p)) * np.exp(-beyond) * (u > c)
    res[..., :3] *= shadow[..., None]
    # Flat, still-unrolled part of the page.
    flat = np.clip(c - u + 0.5, 0, 1)
    if np.any(flat > 0):
        res = over(A * flat[..., None], res)
    band = (u >= c - 0.5) & (u <= c + r)
    if np.any(band):
        t = np.clip((u - c) / r, 0, 1)
        phi1 = np.arcsin(t)
        front = at(c + r * phi1)
        front[..., :3] *= (0.55 + 0.45 * np.cos(phi1))[..., None]
        res = over(front * band[..., None], res)
    # The back of the page: the upper half of the cylinder and the flap folded over the page.
    phi2 = np.pi - np.arcsin(np.clip((u - c) / r, 0, 1))
    u_back = np.where(u >= c, c + r * phi2, c + np.pi * r + (c - u))
    back = at(u_back)
    vis = (u <= c + r)[..., None].astype(np.float32)
    back_a = back[..., 3:4] * vis
    paper = np.array([0.82, 0.82, 0.8], np.float32)
    shade = np.where(u >= c, 0.75 + 0.25 * np.sin(np.clip(phi2, 0, np.pi)), 0.95)[..., None]
    back_px = np.concatenate([(back[..., :3] * 0.18 + paper * back_a * 0.82) * shade, back_a], -1)
    res = over(back_px * vis, res)
    return out(res)


# motion.py contains the historical flat film-roll registration. Import it first
# so this replacement wins regardless of plugin discovery/import order.
from . import motion as _motion  # noqa: E402,F401


@TRANSITIONS.register("film-roll", level=FULL,
                      note="cylindrical rolling film strip, sprocket holes/frame lines in color, direction and shutter blur")
def film_roll(rc,tr,a,b,p,ctx):
    """Orthographic cylinder unwrap with a traveling two-frame film strip.

    gap=.06 and border=.07 are fractions of frame dimensions; curvature=.85 is
    max cylinder bend (0 flat, <1 full visible semicylinder), holes=12 per frame.
    Strip expands to the full picture at endpoints. color supplies the emulsion
    border/frame lines; sprockets are transparent. Shutter matches motion helpers.
    """
    from ..effects import Params
    from . import color, shutter_px
    A,B=arrays(rc,a,b)
    if p <= 0:
        return out(A.copy())
    if p >= 1:
        return out(B.copy())
    q=Params(rc,tr,ctx)
    gap=max(0,q.param("gap",.06))
    border=np.clip(q.param("border",.07),0,.4)
    bend=np.clip(q.param("curvature",.85),0,.99)
    holes=max(2,round(q.param("holes",12)))
    ad=axis_direction(rc,tr,ctx)
    vertical=ad in ("up","down")
    sign=1 if ad in ("up","left") else -1
    if vertical:
        A,B=A.transpose(1,0,2),B.transpose(1,0,2)
    h,w=A.shape[:2]
    yy,xx=np.mgrid[:h,:w].astype(np.float32)
    xx+=.5;yy+=.5
    C=color(rc,tr,ctx)
    length=w*(1+gap)
    def render(t):
        strength=math.sin(math.pi*t)
        k=bend*strength
        v=(xx/w-.5)*2
        curved=w*(.5+.5*np.arcsin(np.clip(v*k,-1,1))/max(math.asin(k),1e-7)) if k>1e-7 else xx
        coord=curved+sign*t*length
        rail=border*h*strength
        sy=(yy-rail)/max(h-2*rail,1)*h
        movie=np.zeros_like(A)
        for px,origin in ((A,0),(B,sign*length)):
            u=coord-origin
            valid=(u>=0)&(u<w)&(yy>=rail)&(yy<h-rail)
            movie+=sample(px,u,sy)*valid[...,None]
        interior=(yy>=rail)&(yy<h-rail)
        in_frame=((coord>=0)&(coord<w))|((coord-sign*length>=0)&(coord-sign*length<w))
        stock=~(interior&in_frame)
        movie+=stock[...,None]*C
        pitch=length/holes
        hole_x=abs((coord+pitch/2)%pitch-pitch/2)<pitch*.22
        hole_y=(abs(yy-rail*.5)<rail*.24)|(abs(yy-(h-rail*.5))<rail*.24)
        movie*= (~(hole_x&hole_y))[...,None]
        shade=np.sqrt(np.maximum(1-(v*k)**2,0))
        movie[...,:3]*=(1-.3*strength*(1-shade))[...,None]
        return movie
    shutter=shutter_px(rc,tr,ctx,length)/max(length,1) if rc.ev.bool(tr,"motionBlur",ctx,True) else 0
    samples=max(1,min(32,math.ceil(shutter*length))) if shutter else 1
    result=np.zeros_like(A)
    for dt in ((np.arange(samples)+.5)/samples-.5)*shutter:
        result+=render(float(np.clip(p+dt,0,1)))/samples
    return out(result.transpose(1,0,2) if vertical else result)
