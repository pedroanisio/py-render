"""Colour dips and mask-based wipes: cut, dip-to-color, wipe, barn-door, blinds, stripe,
circle-open, circle-close, iris, clock-wipe, radial-wipe, luma."""
from __future__ import annotations

import math

import numpy as np

from ..registry import FULL, TRANSITIONS, warn_once
from . import (arrays, axis_field, center, color, direction, edge, grid, luma_display, max_radius,
               mix, out, param, softness)


@TRANSITIONS.register("cut", level=FULL, note="hard cut at the transition point (normally skipped by the compositor)")
def cut(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    return out(B if p >= 0.5 else A)


def _dip(A, B, C, p, a_present, b_present):
    """A -> C -> B; one-sided transitions dip from or to C over the whole duration."""
    if not a_present:
        return mix(np.broadcast_to(C, B.shape), B, p)
    if not b_present:
        return mix(A, np.broadcast_to(C, A.shape), p)
    if p < 0.5:
        return mix(A, np.broadcast_to(C, A.shape), 2 * p)
    return mix(np.broadcast_to(C, B.shape), B, 2 * p - 1)


@TRANSITIONS.register("dip-to-color", level=FULL)
def dip_to_color(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    return out(_dip(A, B, color(rc, tr, ctx), p, a is not None, b is not None))


def _masked(rc, tr, a, b, p, ctx, field, soft=None):
    A, B = arrays(rc, a, b)
    s = softness(rc, tr, ctx) if soft is None else soft
    return out(mix(A, B, edge(field, p, s)))


@TRANSITIONS.register("wipe", level=FULL)
def wipe(rc, tr, a, b, p, ctx):
    """A straight edge travelling along @direction reveals the incoming picture."""
    return _masked(rc, tr, a, b, p, ctx, axis_field(rc, direction(rc, tr, ctx)))


@TRANSITIONS.register("barn-door", level=FULL)
def barn_door(rc, tr, a, b, p, ctx):
    """Two doors part from the centre line; the doors open along @direction's axis."""
    s = axis_field(rc, direction(rc, tr, ctx))
    return _masked(rc, tr, a, b, p, ctx, np.abs(s - 0.5) * 2)


@TRANSITIONS.register("blinds", level=FULL, note="<param name='count'> slats (default 8)")
def blinds(rc, tr, a, b, p, ctx):
    """Venetian blinds: the frame is cut into slats across @direction, each wiping along it."""
    n = max(1, int(param(tr, "count", 8)))
    s = axis_field(rc, direction(rc, tr, ctx)) * n
    return _masked(rc, tr, a, b, p, ctx, s - np.floor(s))


@TRANSITIONS.register("stripe", level=FULL, note="<param name='count'> stripes (default 10), <param name='stagger'> (default 0.4)")
def stripe(rc, tr, a, b, p, ctx):
    """Stripes parallel to @direction wipe in alternately from opposite sides, staggered."""
    d = direction(rc, tr, ctx)
    n = max(1, int(param(tr, "count", 10)))
    st = max(0.0, param(tr, "stagger", 0.4))
    s = axis_field(rc, d)
    k = np.minimum(np.floor(axis_field(rc, (-d[1], d[0])) * n), n - 1)
    s = np.where(k % 2 == 1, 1 - s, s)
    v = (s + st * k / max(1, n - 1)) / (1 + st)
    return _masked(rc, tr, a, b, p, ctx, v)


def _radius_field(rc, tr):
    cx, cy = center(rc, tr)
    xs, ys = grid(rc)
    return np.hypot(xs - cx, ys - cy) / max(1e-6, max_radius(rc, cx, cy))


@TRANSITIONS.register("circle-open", level=FULL, note="<param> cx, cy: centre as fractions of the frame")
def circle_open(rc, tr, a, b, p, ctx):
    return _masked(rc, tr, a, b, p, ctx, _radius_field(rc, tr))


@TRANSITIONS.register("circle-close", level=FULL, note="<param> cx, cy: centre as fractions of the frame")
def circle_close(rc, tr, a, b, p, ctx):
    return _masked(rc, tr, a, b, p, ctx, 1 - _radius_field(rc, tr))


@TRANSITIONS.register("iris", level=FULL,
                      note="six-blade aperture closes on the outgoing picture to @color, then opens on the incoming; <param name='blades'>")
def iris(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    C = color(rc, tr, ctx)
    n = max(3, int(param(tr, "blades", 6)))
    cx, cy = center(rc, tr)
    xs, ys = grid(rc)
    rot = math.radians(60.0 * p)
    th = rot + 2 * math.pi * np.arange(n) / n
    field = np.full(xs.shape, -np.inf, np.float32)
    for t in th:
        field = np.maximum(field, (xs - cx) * math.cos(t) + (ys - cy) * math.sin(t))
    corner = max(max((x - cx) * math.cos(t) + (y - cy) * math.sin(t) for t in th)
                 for x in (0, rc.width) for y in (0, rc.height))
    v = field / max(1e-6, corner)
    soft = softness(rc, tr, ctx) * 0.5
    Cf = np.broadcast_to(C, A.shape)
    if a is None:
        return out(mix(Cf, B, edge(v, p, soft)))
    if b is None:
        return out(mix(Cf, A, edge(v, 1 - p, soft)))
    if p < 0.5:
        return out(mix(Cf, A, edge(v, 1 - 2 * p, soft)))
    return out(mix(Cf, B, edge(v, 2 * p - 1, soft)))


def _angle_field(rc, tr, ctx):
    """Clockwise angle from 12 o'clock around the centre, as a fraction of a turn in [0, 1)."""
    cx, cy = center(rc, tr)
    xs, ys = grid(rc)
    ang = np.arctan2(xs - cx, -(ys - cy)) / (2 * math.pi)
    start = rc.ev.num(tr, "angle", ctx, 0.0) / 360.0 if rc.ev.str(tr, "direction", ctx, "left") == "angle" else 0.0
    f = np.mod(ang - start, 1.0)
    if rc.ev.str(tr, "direction", ctx, "left") in ("right", "down"):
        f = np.mod(1.0 - f, 1.0)
    return f


@TRANSITIONS.register("clock-wipe", level=FULL,
                      note="clockwise sweep from 12 o'clock (direction=angle sets the start angle, right/down sweep counter-clockwise)")
def clock_wipe(rc, tr, a, b, p, ctx):
    return _masked(rc, tr, a, b, p, ctx, _angle_field(rc, tr, ctx), softness(rc, tr, ctx) * 0.25)


@TRANSITIONS.register("radial-wipe", level=FULL,
                      note="sweep from 12 o'clock (or @angle) in both directions at once, meeting at the opposite side")
def radial_wipe(rc, tr, a, b, p, ctx):
    f = _angle_field(rc, tr, ctx)
    v = np.minimum(f, 1 - f) * 2
    return _masked(rc, tr, a, b, p, ctx, v, softness(rc, tr, ctx) * 0.5)


def matte_luma(rc, tr, ctx) -> np.ndarray | None:
    """Display-referred luma (0..1, times alpha) of the @matte node or image asset, full frame."""
    from ..compositor import scale as scale_m
    from ..document import ln
    from ..registry import ASSETS
    mid = tr.get("matte")
    node = rc.doc.ids.get(mid) if mid else None
    if node is None:
        return None
    parent = node.getparent()
    top = parent is None or ln(parent) in ("composition", "symbol", "symbols", "scene")
    if parent is not None and ln(parent) == "assets":
        fn = ASSETS.get(ln(node))
        if fn is None:
            return None
        aw, ah = rc.asset_size(node, ctx)
        if not aw or not ah:
            return None
        buf = fn(rc, node, scale_m(rc.width / aw, rc.height / ah), ctx)
        if buf is None:
            return None
        px = buf.region((0, 0, rc.width, rc.height))
    else:
        PM = rc.root_matrix if top else rc.world_matrix(parent, ctx)
        box = (rc.doc.width, rc.doc.height) if top else rc.node_size(parent, ctx, (rc.doc.width, rc.doc.height))
        o = rc.render_node(node, ctx, PM, box, force=True)
        if o is None:
            return np.zeros((rc.height, rc.width), np.float32)
        px = o.buf.region((0, 0, rc.width, rc.height)) * o.opacity
    return np.clip(luma_display(rc, px), 0.0, 1.0)


@TRANSITIONS.register("luma", level=FULL,
                      note="dark areas of the @matte node's luma switch first; <param name='invert' value='1'> reverses")
def luma(rc, tr, a, b, p, ctx):
    L = matte_luma(rc, tr, ctx)
    if L is None:
        warn_once("transition", "luma", "no usable @matte; falling back to a wipe")
        L = axis_field(rc, direction(rc, tr, ctx))
    elif param(tr, "invert", 0) >= 0.5:
        L = 1 - L
    return _masked(rc, tr, a, b, p, ctx, L)
