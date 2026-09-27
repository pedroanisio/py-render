"""2D moves: slide, push, cover, reveal, whip-pan, zoom-in, zoom-out, spin, squash, shuffle, film-roll.

In one-sided transitions the picture that is present does the moving (an
out-transition of cover/slide slides the node away; an in-transition of reveal
slides it in). Moving pictures get a box motion blur along their path when @motionBlur is true
(the default), sized from the eased speed and a 180-degree shutter.
"""
from __future__ import annotations

import math

import numpy as np

from ..compositor import rotate, scale, translate as T
from ..registry import FULL, PARTIAL, TRANSITIONS
from . import (arrays, axis_field, center, color, direction, extent, moved, out, over, param,
               shutter_px, sstep, warp_affine)


def _move_setup(rc, tr, ctx):
    d = direction(rc, tr, ctx)
    E = extent(rc, d)
    return d, E, shutter_px(rc, tr, ctx, E)


@TRANSITIONS.register("push", level=FULL)
def push(rc, tr, a, b, p, ctx):
    """The incoming picture pushes the outgoing one off-screen along @direction."""
    A, B = arrays(rc, a, b)
    d, E, bl = _move_setup(rc, tr, ctx)
    A2 = moved(A, d[0] * E * p, d[1] * E * p, bl, d)
    B2 = moved(B, d[0] * E * (p - 1), d[1] * E * (p - 1), bl, d)
    return out(over(B2, A2))


@TRANSITIONS.register("cover", level=FULL)
def cover(rc, tr, a, b, p, ctx):
    """The incoming picture slides in over the stationary outgoing one."""
    if b is None:
        return reveal(rc, tr, a, b, p, ctx)
    A, B = arrays(rc, a, b)
    d, E, bl = _move_setup(rc, tr, ctx)
    return out(over(moved(B, d[0] * E * (p - 1), d[1] * E * (p - 1), bl, d), A))


@TRANSITIONS.register("slide", level=FULL,
                      note="incoming slides in over the outgoing, which drifts the same way at 30% speed (<param name='parallax'>)")
def slide(rc, tr, a, b, p, ctx):
    if b is None:
        return reveal(rc, tr, a, b, p, ctx)
    A, B = arrays(rc, a, b)
    d, E, bl = _move_setup(rc, tr, ctx)
    k = param(tr, "parallax", 0.3)
    A2 = moved(A, d[0] * E * p * k, d[1] * E * p * k, bl * k, d)
    return out(over(moved(B, d[0] * E * (p - 1), d[1] * E * (p - 1), bl, d), A2))


@TRANSITIONS.register("reveal", level=FULL)
def reveal(rc, tr, a, b, p, ctx):
    """The outgoing picture slides away, uncovering the stationary incoming one."""
    if a is None:
        return cover(rc, tr, a, b, p, ctx)
    A, B = arrays(rc, a, b)
    d, E, bl = _move_setup(rc, tr, ctx)
    return out(over(moved(A, d[0] * E * p, d[1] * E * p, bl, d), B))


@TRANSITIONS.register("whip-pan", level=FULL, note="fast push with a strong directional streak blur around the cut")
def whip_pan(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    d, E, _ = _move_setup(rc, tr, ctx)
    k = 6.0
    q = 0.5 + 0.5 * math.tanh(k * (p - 0.5)) / math.tanh(k / 2)
    dq = 0.5 * k * (1 - math.tanh(k * (p - 0.5)) ** 2) / math.tanh(k / 2)
    dq0 = 0.5 * k * (1 - math.tanh(k / 2) ** 2) / math.tanh(k / 2)      # speed at the ends
    bl = E * 0.12 * max(0.0, dq - dq0)
    if not rc.ev.bool(tr, "motionBlur", ctx, True):
        bl *= 0.25
    A2 = moved(A, d[0] * E * q, d[1] * E * q, bl, d)
    B2 = moved(B, d[0] * E * (q - 1), d[1] * E * (q - 1), bl, d)
    return out(over(B2, A2))


def _about(cx, cy, M):
    return T(cx, cy) @ M @ T(-cx, -cy)


def _zoom(rc, tr, a, b, p, ctx, a_end, b_start):
    A, B = arrays(rc, a, b)
    cx, cy = center(rc, tr)
    ka = a_end ** p                       # geometric interpolation reads as constant zoom speed
    kb = b_start ** (1 - p)
    m = sstep(0.2, 0.8, p)
    wa = _outgoing_weight(b, m, p)
    A2 = warp_affine(rc, A, _about(cx, cy, scale(ka, ka))) * wa if wa > 0 else A * 0
    B2 = warp_affine(rc, B, _about(cx, cy, scale(kb, kb))) if m > 0 else B * 0
    return out(over(B2 * m, A2))


def _outgoing_weight(b, m, p):
    """The outgoing picture stays under the incoming one and fades late (or with m when alone)."""
    return (1 - m) if b is None else 1 - sstep(0.4, 1.0, p)


@TRANSITIONS.register("zoom-in", level=FULL, note="outgoing zooms towards the viewer and fades while the incoming grows into place")
def zoom_in(rc, tr, a, b, p, ctx):
    return _zoom(rc, tr, a, b, p, ctx, param(tr, "amount", 3.0), 1 / 1.8)


@TRANSITIONS.register("zoom-out", level=FULL, note="outgoing shrinks away and fades while the incoming settles from a close-up")
def zoom_out(rc, tr, a, b, p, ctx):
    k = param(tr, "amount", 3.0)
    return _zoom(rc, tr, a, b, p, ctx, 1 / k, 1.8)


@TRANSITIONS.register("spin", level=FULL,
                      note="both pictures rotate about the centre (counter-clockwise for left/up, @angle degrees total when direction=angle, default 180) while zooming and crossfading")
def spin(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    cx, cy = center(rc, tr)
    dname = rc.ev.str(tr, "direction", ctx, "left")
    total = 180.0
    if dname == "angle":
        total = rc.ev.num(tr, "angle", ctx, 180.0) or 180.0
    sign = -1.0 if dname in ("left", "up") else 1.0
    half = sign * total / 2
    m = sstep(0.25, 0.75, p)
    ka, kb = 1 + 0.6 * p, 0.4 + 0.6 * p
    wa = _outgoing_weight(b, m, p)
    A2 = warp_affine(rc, A, _about(cx, cy, rotate(half * p) @ scale(ka, ka))) * wa if wa > 0 else A * 0
    B2 = warp_affine(rc, B, _about(cx, cy, rotate(half * (p - 1)) @ scale(kb, kb))) if m > 0 else B * 0
    return out(over(B2 * m, A2))


def _dir_scale(d, k):
    """Scale by k along unit direction d (identity across it)."""
    dx, dy = d
    return np.array([[1 + (k - 1) * dx * dx, (k - 1) * dx * dy, 0],
                     [(k - 1) * dx * dy, 1 + (k - 1) * dy * dy, 0],
                     [0, 0, 1]], np.float64)


@TRANSITIONS.register("squash", level=FULL, note="outgoing squashes against the leading edge while the incoming stretches in from the trailing edge")
def squash(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    d = direction(rc, tr, ctx)
    E = extent(rc, d)
    cx, cy = rc.width / 2, rc.height / 2
    lead = (cx + d[0] * E / 2, cy + d[1] * E / 2)
    trail = (cx - d[0] * E / 2, cy - d[1] * E / 2)
    A2 = warp_affine(rc, A, _about(*lead, _dir_scale(d, 1 - p))) if p < 1 else A * 0
    B2 = warp_affine(rc, B, _about(*trail, _dir_scale(d, p))) if p > 0 else B * 0
    return out(over(B2, A2))


@TRANSITIONS.register("shuffle", level=FULL, note="card shuffle: the pictures part along @direction, swap stacking at the midpoint and close up")
def shuffle(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    d = direction(rc, tr, ctx)
    E = extent(rc, d)
    o = math.sin(math.pi * p) * 0.55 * E
    s = sstep(0.15, 0.85, p)
    ka, kb = 1 - 0.12 * s, 0.88 + 0.12 * s
    cx, cy = rc.width / 2, rc.height / 2
    A2 = warp_affine(rc, A, T(d[0] * o, d[1] * o) @ _about(cx, cy, scale(ka, ka)))
    B2 = warp_affine(rc, B, T(-d[0] * o, -d[1] * o) @ _about(cx, cy, scale(kb, kb)))
    return out(over(A2, B2) if p < 0.5 else over(B2, A2))


@TRANSITIONS.register("film-roll", level=PARTIAL,
                      note="flat film strip: both frames run along @direction separated by a frame line in @color, with motion blur; no sprockets or cylinder wrap")
def film_roll(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    d, E, bl = _move_setup(rc, tr, ctx)
    g = param(tr, "gap", 0.06)
    L = E * (1 + g)
    bl = max(bl, 0.08 * E * math.sin(math.pi * p))
    A2 = moved(A, d[0] * L * p, d[1] * L * p, bl, d)
    B2 = moved(B, d[0] * L * (p - 1), d[1] * L * (p - 1), bl, d)
    s = axis_field(rc, d)
    lo, hi = p * (1 + g) - g, p * (1 + g)
    px_norm = 1.0 / max(1.0, E)
    gap = np.clip((s - lo) / px_norm + 0.5, 0, 1) * np.clip((hi - s) / px_norm + 0.5, 0, 1)
    C = color(rc, tr, ctx, (0.05, 0.05, 0.05, 1.0))
    base = gap[..., None] * C
    return out(over(A2, over(B2, base)))
