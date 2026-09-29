"""Colour dips and mask-based wipes: cut, dip-to-color, wipe, barn-door, blinds, stripe,
circle-open, circle-close, iris, clock-wipe, radial-wipe, luma (D19 where it defines them)."""
from __future__ import annotations

import numpy as np

from ..registry import FULL, TRANSITIONS, warn_once
from . import (arrays, axis_field, center, color, direction, edge, grid, luma_working, max_radius,
               mix, out, param, softness)


@TRANSITIONS.register("cut", level=FULL, note="hard cut at the transition point (normally skipped by the compositor)")
def cut(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    return out(B if p >= 0.5 else A)


def _dip(A, B, C, p):
    """A -> C over the first half, C -> B over the second (D19; a missing side is transparent)."""
    if p < 0.5:
        return mix(A, np.broadcast_to(C, A.shape), 2 * p)
    return mix(np.broadcast_to(C, B.shape), B, 2 * p - 1)


@TRANSITIONS.register("dip-to-color", level=FULL)
def dip_to_color(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    return out(_dip(A, B, color(rc, tr, ctx), p))


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


@TRANSITIONS.register("circle-open", "iris", level=FULL, note="<param> cx, cy: centre as fractions of the frame")
def circle_open(rc, tr, a, b, p, ctx):
    """D19: s = the distance from the centre over half the diagonal (iris is an alias)."""
    return _masked(rc, tr, a, b, p, ctx, _radius_field(rc, tr))


@TRANSITIONS.register("circle-close", level=FULL, note="<param> cx, cy: centre as fractions of the frame")
def circle_close(rc, tr, a, b, p, ctx):
    """D19: circle-open's reverse on a: a shows inside a circle closing on the centre."""
    A, B = arrays(rc, a, b)
    return out(mix(A, B, 1 - edge(_radius_field(rc, tr), 1 - p, softness(rc, tr, ctx))))


def _angle_field(rc, tr, start: float):
    """Angle clockwise on screen from the ray at `start` degrees (clockwise from +x) around the
    centre, as a fraction of a turn in [0, 1)."""
    cx, cy = center(rc, tr)
    xs, ys = grid(rc)
    ang = np.degrees(np.arctan2(ys - cy, xs - cx))
    return np.mod(ang - start, 360.0) / 360.0


@TRANSITIONS.register("clock-wipe", level=FULL, note="clockwise sweep from 12 o'clock (D19)")
def clock_wipe(rc, tr, a, b, p, ctx):
    return _masked(rc, tr, a, b, p, ctx, _angle_field(rc, tr, -90.0))


@TRANSITIONS.register("radial-wipe", level=FULL, note="clockwise sweep from @angle (degrees clockwise from +x, D19)")
def radial_wipe(rc, tr, a, b, p, ctx):
    return _masked(rc, tr, a, b, p, ctx, _angle_field(rc, tr, rc.ev.num(tr, "angle", ctx, 0.0)))


def matte_luma(rc, tr, ctx) -> np.ndarray | None:
    """D19: the Rec. 709 luminance of the @matte node's (or image asset's) premultiplied working
    values, clamped to [0, 1], full frame."""
    from . import matte_rgba
    px = matte_rgba(rc, tr, ctx)
    return None if px is None else luma_working(px)


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
