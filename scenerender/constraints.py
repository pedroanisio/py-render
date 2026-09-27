"""transformConstraint on nodes: parent, look-at, follow-path, copy-*, distance, ik, track.

Constraints run after the node's keyframes, in document order, each on the matrix left by the
previous one; @influence blends the constrained matrix with the unconstrained one.

Pinned semantics (the schema leaves these open)
  * A node's point is its anchor point (the point its x/y place and it rotates about); a target's
    point is likewise its anchor point, in frame space.
  * space="world" (default): target values are world values, follow-path coordinates and tracking
    data are composition pixels, offsetX/offsetY are composition pixels. space="local": target values
    are the target's own (parent-space) transform values interpreted in this node's parent space;
    paths, tracking data and offsets are in this node's parent space.
  * parent: world: the node's whole transform becomes relative to the target's world transform (After
    Effects parenting); local: only the target's local transform is inserted above the node's.
  * look-at: rotates the node about its anchor so its +x axis points at the target (+offsetRotation).
  * copy-position/rotation/scale/transform: move the node's anchor onto the target point (+offset),
    set its world rotation (+offsetRotation), set its world scale; skew and anchor are preserved.
  * follow-path: the anchor rides @path at arc-length fraction @progress (subpaths are not bridged);
    autoOrient aligns +x with the tangent.
  * distance: keeps the anchor within [minDistance, maxDistance] px of the target point.
  * ik (on a node): the constrained node is the effector; its @parent links form the chain (the node
    named by @parent, that node's @parent, ... up to the first node without @parent). Each chain
    node's anchor is its joint; the bone runs to the next node's anchor. A two-joint chain is solved
    analytically (law of cosines; bendPositive=true bends clockwise on screen); longer chains by FABRIK
    (32 iterations, deterministic). The solved rotations are delivered to the chain nodes through the
    physics hook (`ik_pose`, x/y stay keyframed); unreachable targets straighten the chain toward them.
    The effector itself keeps its own transform (it moves with the chain). Skeleton bones use the same
    solvers (deform.skeleton_pose).
  * track: attaches the node to tracking data @target (tracking/trackData) at @point (see
    tracking.py for point names), sampled at composition time minus trackData/@timeOffset:
      - x/y: the anchor goes to the tracked point (+offset); tracked rotation adds to the node's
        rotation and tracked scale multiplies its scale;
      - transform data with an anchor (After Effects / mocha transform exports): the node's transform
        is replaced by T(position) R(rotation) S(scale) T(-anchor) in the data space;
      - planar data (corners) without a sub-point: the node's box is corner-pinned onto the four
        corners (a projective matrix; the compositor warps the tile);
      - mask/face data: the centroid of the points unless @point selects a vertex/landmark.
"""
from __future__ import annotations

import math

import numpy as np

from . import tracking
from .document import ln
from .registry import FEATURES, FULL, warn_once
from .values import parse_bool

for _t in ("parent", "look-at", "follow-path", "copy-position", "copy-rotation", "copy-scale",
           "copy-transform", "distance"):
    FEATURES.declare(f"constraint:{_t}", FULL, "space world|local")
FEATURES.declare("constraint:ik", FULL, "two-bone analytic IK (bendPositive) and FABRIK chains via @parent links")
FEATURES.declare("constraint:track", FULL, "point/planar/camera/mask/face tracking data (tracking.py)")

ROOTS = ("composition", "symbol", "symbols", "scene")


# ------------------------------------------------------------------ small matrix helpers
def _T(x, y):
    return np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], np.float64)


def _R(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float64)


def _S(sx, sy):
    return np.diag([sx, sy, 1.0])


def _ap(M, p) -> np.ndarray:
    v = M @ np.array([p[0], p[1], 1.0])
    return v[:2] / v[2]


def _angle(M) -> float:
    return math.degrees(math.atan2(M[1, 0], M[0, 0]))


def _about(p, A) -> np.ndarray:
    return _T(p[0], p[1]) @ A @ _T(-p[0], -p[1])


def _parent_box(rc, el, ctx):
    parent = el.getparent()
    box = (float(rc.doc.width), float(rc.doc.height))
    if parent is None or ln(parent) in ROOTS:
        if parent is not None and ln(parent) == "symbol" and parent.get("width"):
            return float(parent.get("width")), float(parent.get("height"))
        return box
    return rc.node_size(parent, rc.node_ctx(parent, ctx), box)


def _anchor(rc, el, ctx) -> tuple[float, float]:
    nctx = rc.node_ctx(el, ctx)
    w, h = rc.node_size(el, nctx, _parent_box(rc, el, ctx))
    return rc.ev.length(el, "anchorX", nctx, w), rc.ev.length(el, "anchorY", nctx, h)


def parent_frame(rc, el, ctx) -> np.ndarray:
    """Frame matrix of el's parent space (its @parent target, else its XML parent)."""
    pid = el.get("parent")
    if pid and pid in rc.doc.ids:
        return rc.world_matrix(rc.doc.ids[pid], ctx)
    p = el.getparent()
    if p is None or ln(p) in ROOTS:
        return rc.root_matrix
    return rc.world_matrix(p, ctx)


def local_matrix_of(rc, el, ctx) -> np.ndarray:
    nctx = rc.node_ctx(el, ctx)
    box = _parent_box(rc, el, ctx)
    return rc.local_matrix(el, nctx, box, rc.node_size(el, nctx, box))


def target_point(rc, target, ctx, space: str, PM) -> np.ndarray:
    """The target's anchor point in frame space (world) or its parent-space position mapped through
    this node's parent frame PM (local)."""
    a = _anchor(rc, target, ctx)
    if space == "local":
        return _ap(PM, _ap(local_matrix_of(rc, target, ctx), a))
    return _ap(rc.world_matrix(target, ctx), a)


# ------------------------------------------------------------------ apply
def apply_constraint(rc, c, el, M, ctx):
    typ = c.get("type")
    ev = rc.ev
    influence = ev.num(c, "influence", ctx, 1.0)
    if influence <= 0:
        return M
    space = c.get("space") or "world"
    target = rc.doc.ids.get(c.get("target")) if c.get("target") else None
    if target is el:
        target = None
    ox, oy, orot = ev.num(c, "offsetX", ctx, 0.0), ev.num(c, "offsetY", ctx, 0.0), ev.num(c, "offsetRotation", ctx, 0.0)
    PM = parent_frame(rc, el, ctx) if space == "local" or typ in ("track", "follow-path", "distance") else None
    SP = PM if space == "local" else rc.root_matrix           # data space -> frame
    off = SP[:2, :2] @ np.array([ox, oy])
    a_loc = _anchor(rc, el, ctx)
    piv = _ap(M, a_loc)
    out = None
    node_target = target is not None and ln(target) != "trackData"
    if typ == "parent" and node_target:
        if space == "local":
            out = PM @ local_matrix_of(rc, target, ctx) @ np.linalg.inv(PM) @ M
        else:
            out = rc.world_matrix(target, ctx) @ np.linalg.inv(rc.root_matrix) @ M
    elif typ == "look-at" and node_target:
        g = target_point(rc, target, ctx, space, PM if PM is not None else parent_frame(rc, el, ctx)) + off
        ang = math.degrees(math.atan2(g[1] - piv[1], g[0] - piv[0])) + orot
        out = _about(piv, _R(ang - _angle(M))) @ M
    elif typ in ("copy-position", "copy-rotation", "copy-scale", "copy-transform") and node_target:
        PMx = PM if PM is not None else parent_frame(rc, el, ctx)
        out = M
        if typ in ("copy-scale", "copy-transform"):
            if space == "local":
                tc = rc.node_ctx(target, ctx)
                gsx = ev.num(target, "scaleX", tc, 1.0) * math.hypot(PMx[0, 0], PMx[1, 0])
                gsy = ev.num(target, "scaleY", tc, 1.0) * math.hypot(PMx[0, 1], PMx[1, 1])
            else:
                T = rc.world_matrix(target, ctx)
                gsx, gsy = math.hypot(T[0, 0], T[1, 0]), math.hypot(T[0, 1], T[1, 1])
            sx, sy = math.hypot(out[0, 0], out[1, 0]), math.hypot(out[0, 1], out[1, 1])
            if sx > 1e-12 and sy > 1e-12:
                out = out @ _about(a_loc, _S(gsx / sx, gsy / sy))
        if typ in ("copy-rotation", "copy-transform"):
            if space == "local":
                grot = ev.num(target, "rotation", rc.node_ctx(target, ctx), 0.0) + _angle(PMx)
            else:
                grot = _angle(rc.world_matrix(target, ctx))
            out = _about(piv, _R(grot + orot - _angle(out))) @ out
        if typ in ("copy-position", "copy-transform"):
            g = target_point(rc, target, ctx, space, PMx) + off
            p = _ap(out, a_loc)
            out = _T(*(g - p)) @ out
    elif typ == "follow-path" and c.get("path"):
        from .physics import path_geom
        pg = path_geom(rc, c.get("path"))
        x, y, ang = (float(v) for v in pg.sample(ev.num(c, "progress", ctx, 0.0)))
        g = _ap(SP, (x, y)) + off
        out = _T(*(g - piv)) @ M
        if parse_bool(c.get("autoOrient")):
            d = SP[:2, :2] @ np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
            want = math.degrees(math.atan2(d[1], d[0]))
            out = _about(g, _R(want + orot - _angle(out))) @ out
        elif orot:
            out = _about(g, _R(orot)) @ out
    elif typ == "distance" and node_target:
        g = target_point(rc, target, ctx, space, PM)
        k = math.sqrt(abs(np.linalg.det(SP[:2, :2])))
        d = float(np.hypot(*(piv - g)))
        lo = ev.num(c, "minDistance", ctx, 0.0) * k if c.get("minDistance") is not None else 0.0
        hi = ev.num(c, "maxDistance", ctx, 0.0) * k if c.get("maxDistance") is not None else math.inf
        nd = min(max(d, lo), hi)
        out = M
        if d > 1e-9 and nd != d:
            out = _T(*((g + (piv - g) * nd / d) - piv)) @ M
    elif typ == "ik":
        return M                    # the chain's rotations come through ik_pose; the effector rides along
    elif typ == "track":
        out = _track(rc, c, el, M, ctx, SP, off, orot, a_loc, piv)
    elif target is None and typ not in ("follow-path",):
        warn_once("transformConstraint", f"{typ}:{c.get('target')}", "target not found")
    if out is None:
        return M
    return M + (out - M) * influence if influence < 1 else out


def _track(rc, c, el, M, ctx, SP, off, orot, a_loc, piv):
    td = rc.doc.ids.get(c.get("target")) if c.get("target") else None
    if td is None or ln(td) != "trackData":
        warn_once("transformConstraint", f"track:{c.get('target')}", "target is not a trackData")
        return None
    tr = tracking.load_track(rc, td)
    if tr is None:
        return None
    point = c.get("point")
    data = tr.sample(point, ctx.comp_t)
    if data is None:
        warn_once("transformConstraint", f"track-point:{c.get('target')}:{point}", "tracking point not found")
        return None
    _, sub = tr.channel(point)
    if "corners" in data and not sub:
        from .camera import homography
        nctx = rc.node_ctx(el, ctx)
        w, h = rc.node_size(el, nctx, _parent_box(rc, el, ctx))
        corners = np.asarray(data["corners"], np.float64).reshape(4, 2)
        dst = np.array([_ap(SP, p) + off for p in corners])
        src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64)
        if w <= 0 or h <= 0:
            return None
        return homography(src, dst)
    if "x" not in data and "points" in data:
        pts = np.asarray(data["points"], np.float64).reshape(-1, 2)
        data = dict(data, x=float(pts[:, 0].mean()), y=float(pts[:, 1].mean()))
    if "x" not in data:
        return None
    rot = float(data.get("rotation", data.get("rz", 0.0)) or 0.0)
    sx = float(data.get("scaleX", 1.0))
    sy = float(data.get("scaleY", 1.0))
    if "anchorX" in data:
        A = _T(float(data["x"]), float(data["y"])) @ _R(rot + orot) @ _S(sx, sy) @ _T(-float(data["anchorX"]), -float(data["anchorY"]))
        return _T(*off) @ SP @ A
    g = _ap(SP, (float(data["x"]), float(data["y"]))) + off
    out = _T(*(g - piv)) @ M
    if rot or orot:
        out = _about(g, _R(rot + orot)) @ out
    if sx != 1.0 or sy != 1.0:
        out = out @ _about(a_loc, _S(sx, sy))
    return out


# ------------------------------------------------------------------ IK solvers (shared with deform)
def two_bone(pA, pB, pE, goal, bend_positive: bool = True):
    """New points (elbow, effector) of a two-bone chain reaching for goal (y-down frame/screen space)."""
    l1, l2 = float(np.hypot(*(pB - pA))), float(np.hypot(*(pE - pB)))
    if l1 < 1e-9 or l2 < 1e-9:
        return pB, pE
    d = goal - pA
    dist = float(np.clip(np.hypot(*d), abs(l1 - l2) + 1e-9, l1 + l2 - 1e-9))
    base = math.atan2(d[1], d[0])
    cos_a = (l1 * l1 + dist * dist - l2 * l2) / (2 * l1 * dist)
    alpha = math.acos(max(-1.0, min(1.0, cos_a)))
    sgn = 1.0 if bend_positive else -1.0
    aA = base - sgn * alpha
    elbow = pA + l1 * np.array([math.cos(aA), math.sin(aA)])
    g = goal - elbow
    gn = float(np.hypot(*g)) or 1.0
    return elbow, elbow + g / gn * l2


def fabrik(pts: np.ndarray, goal: np.ndarray, iters: int = 32, tol: float = 1e-4) -> np.ndarray:
    """FABRIK (Aristidou & Lasenby 2011) on joint points pts (root first, effector last)."""
    P = np.array(pts, np.float64)
    L = np.hypot(*np.diff(P, axis=0).T)
    root = P[0].copy()
    if float(np.hypot(*(goal - root))) >= L.sum():
        d = (goal - root) / (float(np.hypot(*(goal - root))) or 1.0)
        return root + np.concatenate([[0.0], np.cumsum(L)])[:, None] * d
    for _ in range(iters):
        P[-1] = goal
        for i in range(len(P) - 2, -1, -1):
            v = P[i] - P[i + 1]
            P[i] = P[i + 1] + v / (float(np.hypot(*v)) or 1.0) * L[i]
        P[0] = root
        for i in range(len(P) - 1):
            v = P[i + 1] - P[i]
            P[i + 1] = P[i] + v / (float(np.hypot(*v)) or 1.0) * L[i]
        if float(np.hypot(*(P[-1] - goal))) < tol:
            break
    return P


def chain_deltas(pts: np.ndarray, goal: np.ndarray, bend_positive: bool = True) -> list[float]:
    """Rotation deltas (deg, screen) of each joint so the chain's last point reaches goal."""
    pts = np.asarray(pts, np.float64)
    if len(pts) == 3:
        e, f = two_bone(pts[0], pts[1], pts[2], goal, bend_positive)
        sol = np.array([pts[0], e, f])
    else:
        sol = fabrik(pts, goal)
    cur = pts.copy()
    out = []
    for k in range(len(pts) - 1):
        a_cur = math.atan2(*(cur[k + 1] - cur[k])[::-1])
        a_new = math.atan2(*(sol[k + 1] - sol[k])[::-1])
        d = (a_new - a_cur + math.pi) % (2 * math.pi) - math.pi
        c, s = math.cos(d), math.sin(d)
        R = np.array([[c, -s], [s, c]])
        cur[k + 1:] = (cur[k + 1:] - cur[k]) @ R.T + cur[k]
        out.append(math.degrees(d))
    return out


# ------------------------------------------------------------------ node IK chains
def _node_chains(rc) -> dict:
    key = ("node-ik",)
    hit = rc.cache.get(key)
    if hit is not None:
        return hit
    out: dict = {}
    ids = rc.doc.ids
    for c in rc.doc.root.iter():
        if not isinstance(c.tag, str) or ln(c) != "transformConstraint" or c.get("type") != "ik":
            continue
        E = c.getparent()
        if E is None or ln(E) == "skeleton":
            continue
        chain, p, seen = [], E, {E}
        while p.get("parent") and p.get("parent") in ids and len(chain) < 64:
            p = ids[p.get("parent")]
            if p in seen:
                break
            seen.add(p)
            chain.append(p)
        chain.reverse()
        if not chain:
            warn_once("transformConstraint", f"ik:{E.get('id')}", "ik needs a chain of @parent links; ignored")
            continue
        for j in chain:
            out.setdefault(j, []).append((c, tuple(chain), E))
    rc.cache[key] = out
    return out


def has_node_ik(rc) -> bool:
    return bool(_node_chains(rc))


def _own_constraints(rc, el, M, ctx):
    for c in el:
        if ln(c) == "transformConstraint" and c.get("type") != "ik":
            M = apply_constraint(rc, c, el, M, rc.node_ctx(el, ctx))
    return M


def _solve_chain(rc, c, chain, E, ctx) -> dict:
    key = ("ik-sol", c, ctx.t, ctx.comp_t, ctx.scope)
    hit = rc.frame_cache.get(key)
    if hit is not None:
        return hit
    rc.cache[("ik-busy",)] = True
    try:
        A = chain[0]
        base = parent_frame(rc, A, ctx)
        W = base @ local_matrix_of(rc, A, ctx)
        W = _own_constraints(rc, A, W, ctx)
        mats = [W]
        for J in list(chain[1:]) + [E]:
            W = _own_constraints(rc, J, mats[-1] @ local_matrix_of(rc, J, ctx), ctx)
            mats.append(W)
        pts = np.array([_ap(Mj, _anchor(rc, J, ctx)) for Mj, J in zip(mats, list(chain) + [E])])
        tgt = rc.doc.ids.get(c.get("target")) if c.get("target") else None
        if tgt is None:
            warn_once("transformConstraint", f"ik-target:{c.get('target')}", "ik target not found")
            sol = {}
        else:
            space = c.get("space") or "world"
            PM = base if space == "local" else None
            goal = target_point(rc, tgt, ctx, space, PM)
            SP = base if space == "local" else rc.root_matrix
            goal = goal + SP[:2, :2] @ np.array([rc.ev.num(c, "offsetX", ctx, 0.0), rc.ev.num(c, "offsetY", ctx, 0.0)])
            infl = rc.ev.num(c, "influence", ctx, 1.0)
            deltas = chain_deltas(pts, goal, parse_bool(c.get("bendPositive"), True))
            sol = {}
            for J, Mj, d in zip(chain, mats, deltas):
                P = Mj @ np.linalg.inv(local_matrix_of(rc, J, ctx))
                mirror = np.linalg.det(P[:2, :2]) < 0
                sol[J] = (-d if mirror else d) * infl
    finally:
        rc.cache[("ik-busy",)] = False
    rc.frame_cache[key] = sol
    return sol


def ik_pose(rc, el, ctx):
    """(x, y, rotation) in el's parent space for a node in an IK chain, else None."""
    info = _node_chains(rc).get(el)
    if not info or rc.cache.get(("ik-busy",)):
        return None
    delta = 0.0
    for c, chain, E in info:
        delta += _solve_chain(rc, c, chain, E, ctx).get(el, 0.0)
    ev = rc.ev
    box = _parent_box(rc, el, ctx)
    x, y = ev.length(el, "x", ctx, box[0]), ev.length(el, "y", ctx, box[1])
    rot = ev.num(el, "rotation", ctx, 0.0)
    orient = ev.motion_path_angle(el, ctx)
    return x, y, rot + (orient or 0.0) + delta
