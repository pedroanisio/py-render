"""Deform modifiers (<deform><modifier type=.../></deform> on layers and shapes).

`apply_deform(rc, el, buf, ctx, M, size) -> Buf` warps a node's rendered tile. Every modifier is a
forward map in node-local coordinates (the node box (0,0)-(w,h), same space as masks, pins,
warp points and corners); modifiers compose in document order. The output tile is resampled by
inverse mapping on a coarse grid (exact inverses where available, otherwise Newton iterations on
the composite map) and bilinear sampling of the premultiplied float tile, so precision and
linear light are preserved.

Pinned attribute meanings:
  bend        amount = total bend in degrees along @axis (y: the vertical centre line becomes an arc;
              x: the horizontal one), around centerX/centerY (default box centre).
  twist       amount deg at the centre falling to 0 at @radius (default half the box diagonal).
  wave        amount px, frequency = cycles across the box, phase deg; axis x = waves run along x
              (vertical displacement), axis y = along y (horizontal displacement).
  squash      amount percent shorter along @axis (volume preserving); stretch: percent longer.
  mesh-warp   rows x cols grid over the box; <point row col x y> are offsets (px) of grid points;
              displacement interpolated bilinearly.
  puppet      pins: restX/restY in local px, x/y displacement, rotation deg (bend pins), amount weight;
              inverse-distance weighted blend (not ARAP/MLS: PARTIAL); starch pins hold their area
              (zero displacement at half weight).
  skin        linear blend skinning by @skeleton: bones' rest pose = un-animated attribute values,
              pose = animated values + two-bone IK from the skeleton's transformConstraint type=ik;
              weights by inverse distance to the rest bone segments. Bones live in composition pixels.
  bulge/pinch amount percent (pinch = inward), @radius, centre; spherize amount 0..100 percent.
  ripple      amount px, frequency = rings per radius, phase deg, radius (default half diagonal).
  turbulence  amount px, frequency = noise cycles per 100 px, phase deg (evolution), seed.
  corner-pin  corners = 8 numbers x0 y0 .. x3 y3 (TL TR BR BL) in local px; exact homography.
"""
from __future__ import annotations

import math

import numpy as np

from .document import ln
from .physics import fbm
from .raster import Buf, intersect
from .registry import DEFORMERS, FEATURES, FULL, PARTIAL, warn_once

FEATURES.declare("deform", FULL, "node tiles are resampled through an inverse map (modifier levels vary)")
GRID = 4          # output pixels per inverse-map sample


def _c(rc, m, ctx, name, default):
    return rc.ev.num(m, name, ctx, default) if (m.get(name) is not None or _animated(m, name)) else default


def _animated(m, name) -> bool:
    return any(ln(a) in ("animate", "expression", "link") and a.get("property") == name for a in m)


def _common(rc, m, ctx, w, h):
    ev = rc.ev
    cx = _c(rc, m, ctx, "centerX", w / 2)
    cy = _c(rc, m, ctx, "centerY", h / 2)
    R = _c(rc, m, ctx, "radius", 0.0) or math.hypot(w, h) / 2
    return (ev.num(m, "amount", ctx, 0.0), ev.num(m, "frequency", ctx, 1.0), math.radians(ev.num(m, "phase", ctx, 0.0)),
            (m.get("axis") or "y").strip().lower(), cx, cy, max(R, 1e-6))


def _swap(fn):
    """Run a y-axis map along x by transposing coordinates."""
    return lambda x, y: tuple(reversed(fn(y, x)))


# ------------------------------------------------------------------ builders
@DEFORMERS.register("bend", level=FULL)
def build_bend(rc, m, ctx, w, h, node):
    amt, _, _, axis, cx, cy, _ = _common(rc, m, ctx, w, h)
    theta = math.radians(amt)
    if abs(theta) < 1e-6:
        return None
    along = h if axis != "x" else w
    R = along / abs(theta)
    s = 1.0 if theta > 0 else -1.0

    def fwd_y(x, y, cx=cx, cy=cy):
        dx = (x - cx) * s
        phi = (y - cy) / R
        dist = R - dx
        return cx + s * (R - dist * np.cos(phi)), cy + dist * np.sin(phi)

    def inv_y(x, y, cx=cx, cy=cy):
        vx, vy = (x - cx) * s - R, y - cy
        dist = np.hypot(vx, vy)
        phi = np.arctan2(vy, -vx)
        return cx + s * (R - dist), cy + phi * R

    if axis == "x":
        f = lambda x, y: tuple(reversed(fwd_y(y, x, cy, cx)))  # noqa: E731
        g = lambda x, y: tuple(reversed(inv_y(y, x, cy, cx)))  # noqa: E731
        return f, g
    return fwd_y, inv_y


@DEFORMERS.register("twist", level=FULL)
def build_twist(rc, m, ctx, w, h, node):
    amt, _, _, _, cx, cy, R = _common(rc, m, ctx, w, h)
    a0 = math.radians(amt)
    if a0 == 0:
        return None

    def rot(x, y, sign):
        dx, dy = x - cx, y - cy
        r = np.hypot(dx, dy)
        a = sign * a0 * np.clip(1 - r / R, 0, 1) ** 2
        c, s = np.cos(a), np.sin(a)
        return cx + dx * c - dy * s, cy + dx * s + dy * c
    return (lambda x, y: rot(x, y, 1)), (lambda x, y: rot(x, y, -1))


@DEFORMERS.register("wave", level=FULL)
def build_wave(rc, m, ctx, w, h, node):
    amt, freq, ph, axis, *_ = _common(rc, m, ctx, w, h)
    if amt == 0:
        return None
    if axis == "x":
        d = lambda x: amt * np.sin(2 * math.pi * freq * x / max(w, 1e-6) + ph)  # noqa: E731
        return (lambda x, y: (x, y + d(x))), (lambda x, y: (x, y - d(x)))
    d = lambda y: amt * np.sin(2 * math.pi * freq * y / max(h, 1e-6) + ph)  # noqa: E731
    return (lambda x, y: (x + d(y), y)), (lambda x, y: (x - d(y), y))


def _axis_scale(rc, m, ctx, w, h, sign):
    amt, _, _, axis, cx, cy, _ = _common(rc, m, ctx, w, h)
    s = 1 + sign * amt / 100.0
    if amt == 0 or s <= 1e-6:
        return None
    if axis == "x":
        f = lambda x, y: (cx + (x - cx) * s, cy + (y - cy) / s)  # noqa: E731
        g = lambda x, y: (cx + (x - cx) / s, cy + (y - cy) * s)  # noqa: E731
    else:
        f = lambda x, y: (cx + (x - cx) / s, cy + (y - cy) * s)  # noqa: E731
        g = lambda x, y: (cx + (x - cx) * s, cy + (y - cy) / s)  # noqa: E731
    return f, g


DEFORMERS.register("squash", level=FULL)(lambda rc, m, ctx, w, h, node: _axis_scale(rc, m, ctx, w, h, -1))
DEFORMERS.register("stretch", level=FULL)(lambda rc, m, ctx, w, h, node: _axis_scale(rc, m, ctx, w, h, 1))


def _radial(cx, cy, g):
    def f(x, y):
        dx, dy = x - cx, y - cy
        r = np.hypot(dx, dy)
        rs = np.where(r > 1e-9, r, 1.0)
        k = np.where(r > 1e-9, g(r) / rs, 1.0)
        return cx + dx * k, cy + dy * k
    return f


def _bulge(rc, m, ctx, w, h, sign):
    amt, _, _, _, cx, cy, R = _common(rc, m, ctx, w, h)
    a = sign * amt / 100.0
    if a == 0:
        return None
    R = _c(rc, m, ctx, "radius", 0.0) or min(w, h) / 2 or R
    return _radial(cx, cy, lambda r: np.where(r < R, r * (1 + a * (1 - (r / R) ** 2) ** 2), r)), None


DEFORMERS.register("bulge", level=FULL)(lambda rc, m, ctx, w, h, node: _bulge(rc, m, ctx, w, h, 1))
DEFORMERS.register("pinch", level=FULL)(lambda rc, m, ctx, w, h, node: _bulge(rc, m, ctx, w, h, -1))


@DEFORMERS.register("spherize", level=FULL)
def build_spherize(rc, m, ctx, w, h, node):
    amt, _, _, _, cx, cy, R = _common(rc, m, ctx, w, h)
    a = amt / 100.0
    if a == 0:
        return None
    R = _c(rc, m, ctx, "radius", 0.0) or min(w, h) / 2 or R
    return _radial(cx, cy, lambda r: np.where(r < R, r + a * (R * np.sin(np.pi / 2 * np.minimum(r / R, 1)) - r), r)), None


@DEFORMERS.register("ripple", level=FULL)
def build_ripple(rc, m, ctx, w, h, node):
    amt, freq, ph, _, cx, cy, R = _common(rc, m, ctx, w, h)
    if amt == 0:
        return None
    return _radial(cx, cy, lambda r: r + np.where(
        r < R, amt * np.sin(2 * math.pi * freq * r / R - ph) * (1 - np.minimum(r / R, 1)) ** 2, 0.0)), None


@DEFORMERS.register("turbulence", level=FULL)
def build_turbulence(rc, m, ctx, w, h, node):
    amt, freq, ph, *_ = _common(rc, m, ctx, w, h)
    if amt == 0:
        return None
    seed = int(m.get("seed")) ^ rc.doc.seed if m.get("seed") else rc.ev.seed_for(node, f"deform-turb:{list(m.getparent()).index(m)}")
    k = freq / 100.0
    z = ph / (2 * math.pi)
    return (lambda x, y: (x + amt * fbm(seed, x * k, y * k, z, 3), y + amt * fbm(seed + 5, x * k, y * k, z, 3))), None


@DEFORMERS.register("corner-pin", level=FULL)
def build_corner_pin(rc, m, ctx, w, h, node):
    v = rc.ev.get(m, "corners", ctx, None)
    vals = list(v) if isinstance(v, tuple) else [float(s) for s in str(v or "").replace(",", " ").split()]
    if len(vals) != 8 or w <= 0 or h <= 0:
        if vals:
            warn_once("deform", "corner-pin", "corners needs 8 numbers")
        return None
    from .camera import homography
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64)
    dst = np.array(vals, np.float64).reshape(4, 2)
    H = homography(src, dst)
    Hi = np.linalg.inv(H)

    def ap(Hm):
        def f(x, y):
            X = Hm[0, 0] * x + Hm[0, 1] * y + Hm[0, 2]
            Y = Hm[1, 0] * x + Hm[1, 1] * y + Hm[1, 2]
            Z = Hm[2, 0] * x + Hm[2, 1] * y + Hm[2, 2]
            Z = np.where(np.abs(Z) > 1e-9, Z, 1e-9)
            return X / Z, Y / Z
        return f
    return ap(H), ap(Hi)


@DEFORMERS.register("mesh-warp", level=FULL, note="bilinear interpolation of warp-point offsets")
def build_mesh_warp(rc, m, ctx, w, h, node):
    ev = rc.ev
    rows = max(2, int(ev.num(m, "rows", ctx, 4)))
    cols = max(2, int(ev.num(m, "cols", ctx, 4)))
    D = np.zeros((rows, cols, 2))
    any_ = False
    for p in m:
        if ln(p) != "point":
            continue
        r, c = int(float(p.get("row", 0))), int(float(p.get("col", 0)))
        if 0 <= r < rows and 0 <= c < cols:
            D[r, c] = (ev.num(p, "x", ctx, 0.0), ev.num(p, "y", ctx, 0.0))
            any_ = any_ or bool(D[r, c].any())
    if not any_ or w <= 0 or h <= 0:
        return None

    def f(x, y):
        gx = np.clip(x / w * (cols - 1), 0, cols - 1 - 1e-9)
        gy = np.clip(y / h * (rows - 1), 0, rows - 1 - 1e-9)
        i0, j0 = np.floor(gy).astype(int), np.floor(gx).astype(int)
        fy, fx = gy - i0, gx - j0
        d = (D[i0, j0] * ((1 - fx) * (1 - fy))[..., None] + D[i0, j0 + 1] * (fx * (1 - fy))[..., None]
             + D[i0 + 1, j0] * ((1 - fx) * fy)[..., None] + D[i0 + 1, j0 + 1] * (fx * fy)[..., None])
        return x + d[..., 0], y + d[..., 1]
    return f, None


@DEFORMERS.register("puppet", level=PARTIAL, note="inverse-distance weighted pins (no ARAP/MLS); starch pins hold")
def build_puppet(rc, m, ctx, w, h, node):
    ev = rc.ev
    pins = []
    for p in m:
        if ln(p) != "pin":
            continue
        kind = p.get("kind", "position")
        rest = np.array([ev.num(p, "restX", ctx, 0.0), ev.num(p, "restY", ctx, 0.0)])
        disp = np.array([ev.num(p, "x", ctx, 0.0), ev.num(p, "y", ctx, 0.0)])
        rot = math.radians(ev.num(p, "rotation", ctx, 0.0)) if kind == "bend" else 0.0
        wgt = ev.num(p, "amount", ctx, 1.0) * (0.5 if kind == "starch" else 1.0)
        if kind == "starch":
            disp[:] = 0
        pins.append((rest, disp, rot, max(0.0, wgt)))
    if not pins or all(not d.any() and r == 0 for _, d, r, _ in pins):
        return None
    eps2 = (0.05 * max(w, h, 1.0)) ** 2

    def f(x, y):
        num_x = np.zeros_like(x, dtype=np.float64)
        num_y = np.zeros_like(x, dtype=np.float64)
        den = np.zeros_like(x, dtype=np.float64)
        for rest, disp, rot, wgt in pins:
            dx, dy = x - rest[0], y - rest[1]
            wi = wgt / (dx * dx + dy * dy + eps2)
            ox, oy = disp[0], disp[1]
            if rot:
                c, s = math.cos(rot), math.sin(rot)
                ox = ox + dx * c - dy * s - dx
                oy = oy + dx * s + dy * c - dy
            num_x += wi * ox
            num_y += wi * oy
            den += wi
        den = np.where(den > 0, den, 1.0)
        return x + num_x / den, y + num_y / den
    return f, None


# ------------------------------------------------------------------ skin
def _bone_local(rc, b, ctx, rest: bool):
    ev = rc.ev
    def g(p, d):
        v = ev.base(b, p, ctx, d) if rest else ev.num(b, p, ctx, d)
        return float(v[0] if isinstance(v, tuple) else v)
    x, y, r = g("x", 0.0), g("y", 0.0), math.radians(g("rotation", 0.0))
    sx, sy = g("scaleX", 1.0), g("scaleY", 1.0)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c * sx, -s * sy, x], [s * sx, c * sy, y], [0, 0, 1]], np.float64)


def skeleton_pose(rc, skel, ctx, rest: bool = False) -> dict:
    """bone element -> 3x3 world matrix (composition px), parents first; IK applied unless rest."""
    bones = [b for b in skel if ln(b) == "bone"]
    by_id = {b.get("id"): b for b in bones}
    local = {b: _bone_local(rc, b, ctx, rest) for b in bones}
    world: dict = {}

    def solve(b, depth=0):
        if b in world:
            return world[b]
        par = by_id.get(b.get("parent")) if b.get("parent") else None
        W = (solve(par, depth + 1) if par is not None and depth < 64 else np.eye(3)) @ local[b]
        world[b] = W
        return W

    for b in bones:
        solve(b)
    if rest:
        return world
    for c in skel:
        if ln(c) == "transformConstraint" and c.get("type") == "ik":
            _two_bone_ik(rc, c, bones, by_id, local, world, ctx)
    return world


def _two_bone_ik(rc, c, bones, by_id, local, world, ctx):
    tgt = rc.doc.ids.get(c.get("target")) if c.get("target") else None
    if tgt is None:
        return
    infl = rc.ev.num(c, "influence", ctx, 1.0)
    T = rc.world_matrix(tgt, ctx)
    goal = np.array([T[0, 2], T[1, 2]]) / rc.scale
    parent_of = {b: by_id.get(b.get("parent")) for b in bones}
    children = {b: [k for k in bones if parent_of[k] is b] for b in bones}
    leaves = [b for b in bones if not children[b]]
    if not leaves:
        return

    def depth(b):
        d = 0
        while parent_of.get(b) is not None and d < 64:
            b, d = parent_of[b], d + 1
        return d
    leaf = max(leaves, key=depth)
    if parent_of.get(leaf) is not None and parent_of.get(parent_of[leaf]) is not None:
        A, B, eff_local = parent_of[parent_of[leaf]], parent_of[leaf], local[leaf][:2, 2]
    elif parent_of.get(leaf) is not None:
        A, B = parent_of[leaf], leaf
        L = float(leaf.get("length", 0) or 0)
        eff_local = np.array([L, 0.0])
    else:
        return
    WA, WB = world[A], world[B]
    pA = WA[:2, 2]
    pB = WB[:2, 2]
    pE = (WB @ np.array([*eff_local, 1.0]))[:2]
    l1, l2 = float(np.hypot(*(pB - pA))), float(np.hypot(*(pE - pB)))
    if l1 < 1e-6 or l2 < 1e-6:
        return
    d = goal - pA
    dist = float(np.clip(np.hypot(*d), abs(l1 - l2) + 1e-4, l1 + l2 - 1e-4))
    base = math.atan2(d[1], d[0])
    cos_a = (l1 * l1 + dist * dist - l2 * l2) / (2 * l1 * dist)
    alpha = math.acos(max(-1.0, min(1.0, cos_a)))
    sgn = 1.0 if c.get("bendPositive", "true") != "false" else -1.0
    ang_A = base - sgn * alpha                                  # world direction A -> B
    elbow = pA + l1 * np.array([math.cos(ang_A), math.sin(ang_A)])
    ang_B = math.atan2(goal[1] - elbow[1], goal[0] - elbow[0])  # world direction B -> effector

    def set_dir(bone, W, p_from, p_to, new_ang):
        cur = math.atan2(p_to[1] - p_from[1], p_to[0] - p_from[0])
        delta = (new_ang - cur) * infl
        local[bone] = local[bone] @ _rot3(delta)
    set_dir(A, WA, pA, pB, ang_A)
    # recompute A's subtree
    _refresh(bones, by_id, local, world)
    WB = world[B]
    pB = WB[:2, 2]
    pE = (WB @ np.array([*eff_local, 1.0]))[:2]
    set_dir(B, WB, pB, pE, ang_B)
    _refresh(bones, by_id, local, world)


def _rot3(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float64)


def _refresh(bones, by_id, local, world):
    world.clear()

    def solve(b, depth=0):
        if b in world:
            return world[b]
        par = by_id.get(b.get("parent")) if b.get("parent") else None
        W = (solve(par, depth + 1) if par is not None and depth < 64 else np.eye(3)) @ local[b]
        world[b] = W
        return W
    for b in bones:
        solve(b)


@DEFORMERS.register("skin", level=PARTIAL,
                    note="linear blend skinning with inverse-distance weights; two-bone IK; bones in composition px")
def build_skin(rc, m, ctx, w, h, node, M=None):
    skel = rc.doc.ids.get(m.get("skeleton")) if m.get("skeleton") else None
    if skel is None or M is None:
        if skel is None:
            warn_once("deform", f"skin:{m.get('skeleton')}", "skeleton not found")
        return None
    rest = skeleton_pose(rc, skel, ctx, rest=True)
    pose = skeleton_pose(rc, skel, ctx)
    bones = list(rest)
    if not bones:
        return None
    S = [pose[b] @ np.linalg.inv(rest[b]) for b in bones]
    if all(np.allclose(s, np.eye(3)) for s in S):
        return None
    segs = []
    for b in bones:
        L = float(b.get("length", 0) or 0)
        p0 = rest[b][:2, 2]
        p1 = (rest[b] @ np.array([L, 0, 1.0]))[:2]
        segs.append((p0, p1))
    toW = np.diag([1 / rc.scale, 1 / rc.scale, 1.0]) @ M     # local -> composition px
    toL = np.linalg.inv(toW)
    eps2 = 12.0 ** 2

    def f(x, y):
        X = toW[0, 0] * x + toW[0, 1] * y + toW[0, 2]
        Y = toW[1, 0] * x + toW[1, 1] * y + toW[1, 2]
        ws, xs, ys = [], [], []
        for (p0, p1), Sm in zip(segs, S):
            d = p1 - p0
            L2 = float(d @ d)
            u = np.clip(((X - p0[0]) * d[0] + (Y - p0[1]) * d[1]) / L2, 0, 1) if L2 > 0 else 0.0
            qx, qy = p0[0] + u * d[0], p0[1] + u * d[1]
            wi = 1.0 / ((X - qx) ** 2 + (Y - qy) ** 2 + eps2) ** 2
            ws.append(wi)
            xs.append(Sm[0, 0] * X + Sm[0, 1] * Y + Sm[0, 2])
            ys.append(Sm[1, 0] * X + Sm[1, 1] * Y + Sm[1, 2])
        wsum = sum(ws)
        X2 = sum(wi * v for wi, v in zip(ws, xs)) / wsum
        Y2 = sum(wi * v for wi, v in zip(ws, ys)) / wsum
        return toL[0, 0] * X2 + toL[0, 1] * Y2 + toL[0, 2], toL[1, 0] * X2 + toL[1, 1] * Y2 + toL[1, 2]
    return f, None


# ------------------------------------------------------------------ driver
def modifiers_of(el) -> list:
    return [m for d in el if ln(d) == "deform" for m in d if ln(m) == "modifier"]


def build_maps(rc, el, ctx, size, M):
    w, h = size
    maps = []
    for m in modifiers_of(el):
        typ = m.get("type")
        fn = DEFORMERS.get(typ)
        if fn is None:
            warn_once("deform", typ)
            continue
        res = fn(rc, m, ctx, w, h, el, M) if typ == "skin" else fn(rc, m, ctx, w, h, el)
        if res is not None:
            maps.append(res)
    return maps


def _compose(maps):
    def F(x, y):
        for f, _ in maps:
            x, y = f(x, y)
        return x, y
    return F


def invert(maps, qx, qy, iters: int = 12):
    """Local source points whose deformed position is (qx, qy)."""
    if all(g is not None for _, g in maps):
        x, y = qx, qy
        for _, g in reversed(maps):
            x, y = g(x, y)
        return x, y
    F = _compose(maps)
    x, y = qx.copy(), qy.copy()
    # start from the exact inverse of the trailing invertible maps when available
    e = 0.5
    for _ in range(iters):
        fx, fy = F(x, y)
        rx, ry = fx - qx, fy - qy
        ax, ay = F(x + e, y)
        bx, by = F(x, y + e)
        j00, j10 = (ax - fx) / e, (ay - fy) / e
        j01, j11 = (bx - fx) / e, (by - fy) / e
        det = j00 * j11 - j01 * j10
        ok = np.abs(det) > 1e-6
        det = np.where(ok, det, 1.0)
        dx = np.where(ok, (j11 * rx - j01 * ry) / det, rx)
        dy = np.where(ok, (-j10 * rx + j00 * ry) / det, ry)
        step = np.hypot(dx, dy)
        lim = np.where(step > 50, 50 / np.maximum(step, 1e-9), 1.0)
        x, y = x - dx * lim, y - dy * lim
        if float(np.max(np.abs(rx)) + np.max(np.abs(ry))) < 1e-3:
            break
    return x, y


def sample_bilinear(buf: Buf, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """Sample buf (premultiplied) at frame-pixel positions (pixel centres at +0.5); zeros outside."""
    u = X - buf.x0 - 0.5
    v = Y - buf.y0 - 0.5
    x0 = np.floor(u).astype(np.int64)
    y0 = np.floor(v).astype(np.int64)
    fx = (u - x0).astype(np.float32)[..., None]
    fy = (v - y0).astype(np.float32)[..., None]
    H, W = buf.px.shape[:2]
    P = np.pad(buf.px, ((1, 1), (1, 1), (0, 0)))

    def tap(yy, xx):
        yy = np.clip(yy + 1, 0, H + 1)
        xx = np.clip(xx + 1, 0, W + 1)
        return P[yy, xx]
    out = (tap(y0, x0) * (1 - fx) * (1 - fy) + tap(y0, x0 + 1) * fx * (1 - fy)
           + tap(y0 + 1, x0) * (1 - fx) * fy + tap(y0 + 1, x0 + 1) * fx * fy)
    outside = (u < -1) | (v < -1) | (u > W) | (v > H)
    out[outside] = 0
    return out


def apply_deform(rc, el, buf: Buf, ctx, M: np.ndarray, size) -> Buf:
    """Warp a node's rendered tile by its <deform> modifiers (see module docstring)."""
    if buf is None:
        return buf
    w, h = size
    if w <= 0 or h <= 0:
        Mi = np.linalg.inv(M)
        c = np.array([[buf.x0, buf.y0, 1], [buf.x0 + buf.w, buf.y0 + buf.h, 1]], np.float64) @ Mi.T
        w, h = float(abs(c[1, 0] - c[0, 0])) or 1.0, float(abs(c[1, 1] - c[0, 1])) or 1.0
    maps = build_maps(rc, el, ctx, (w, h), M)
    if not maps:
        return buf
    Mi = np.linalg.inv(M)
    F = _compose(maps)
    # output extent: forward-map a grid over the input tile
    gx, gy = np.meshgrid(np.linspace(buf.x0, buf.x0 + buf.w, 25), np.linspace(buf.y0, buf.y0 + buf.h, 25))
    lx = Mi[0, 0] * gx + Mi[0, 1] * gy + Mi[0, 2]
    ly = Mi[1, 0] * gx + Mi[1, 1] * gy + Mi[1, 2]
    fx, fy = F(lx, ly)
    ox = M[0, 0] * fx + M[0, 1] * fy + M[0, 2]
    oy = M[1, 0] * fx + M[1, 1] * fy + M[1, 2]
    r = (int(math.floor(np.nanmin(ox))) - 2, int(math.floor(np.nanmin(oy))) - 2,
         int(math.ceil(np.nanmax(ox))) + 2, int(math.ceil(np.nanmax(oy))) + 2)
    r = intersect(r, (-64, -64, rc.width + 64, rc.height + 64))
    if r is None:
        return Buf.empty(buf.x0, buf.y0, 1, 1)
    W, H = r[2] - r[0], r[3] - r[1]
    # inverse map on a coarse grid of output pixel centres, then bilinear upsampling of coordinates
    nx, ny = W // GRID + 2, H // GRID + 2
    cxs = r[0] + 0.5 + np.arange(nx) * GRID
    cys = r[1] + 0.5 + np.arange(ny) * GRID
    CX, CY = np.meshgrid(cxs, cys)
    qx = Mi[0, 0] * CX + Mi[0, 1] * CY + Mi[0, 2]
    qy = Mi[1, 0] * CX + Mi[1, 1] * CY + Mi[1, 2]
    sx, sy = invert(maps, qx, qy)
    SX = M[0, 0] * sx + M[0, 1] * sy + M[0, 2]
    SY = M[1, 0] * sx + M[1, 1] * sy + M[1, 2]
    px = np.arange(W) / GRID
    py = np.arange(H) / GRID
    i0 = np.minimum(np.floor(py).astype(int), ny - 2)
    j0 = np.minimum(np.floor(px).astype(int), nx - 2)
    fy_ = (py - i0)[:, None]
    fx_ = (px - j0)[None, :]

    def up(G):
        a = G[i0][:, j0]
        b = G[i0][:, j0 + 1]
        c = G[i0 + 1][:, j0]
        d = G[i0 + 1][:, j0 + 1]
        return a * (1 - fx_) * (1 - fy_) + b * fx_ * (1 - fy_) + c * (1 - fx_) * fy_ + d * fx_ * fy_
    out = sample_bilinear(buf, up(SX), up(SY))
    return Buf(out.astype(np.float32), r[0], r[1])


def install(rc) -> None:
    comp = rc.doc.root
    if any(isinstance(e.tag, str) and ln(e) == "deform" for e in comp.iter()):
        rc.hooks["deform"] = apply_deform


try:
    from .render import hook_installer
    hook_installer("deform")(install)
except ImportError:      # pragma: no cover
    pass
