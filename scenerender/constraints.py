"""transformConstraint: parent, look-at, follow-path, copy-*, distance (ik and track are reported)."""
from __future__ import annotations

import math

import numpy as np

from .registry import FEATURES, FULL, PARTIAL, warn_once

for _t in ("parent", "look-at", "follow-path", "copy-position", "copy-rotation", "copy-scale",
           "copy-transform", "distance"):
    FEATURES.declare(f"constraint:{_t}", FULL)
FEATURES.declare("constraint:ik", PARTIAL, "not solved; the node keeps its keyframed transform")
FEATURES.declare("constraint:track", PARTIAL, "needs tracking data readers; ignored")


def _decompose(M):
    tx, ty = M[0, 2], M[1, 2]
    sx = math.hypot(M[0, 0], M[1, 0])
    sy = math.hypot(M[0, 1], M[1, 1])
    rot = math.degrees(math.atan2(M[1, 0], M[0, 0]))
    return tx, ty, rot, sx, sy


def _compose(tx, ty, rot, sx, sy):
    r = math.radians(rot)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c * sx, -s * sy, tx], [s * sx, c * sy, ty], [0, 0, 1]])


def apply_constraint(rc, c, el, M, ctx):
    typ = c.get("type")
    ev = rc.ev
    influence = ev.num(c, "influence", ctx, 1.0)
    if influence <= 0:
        return M
    target = rc.doc.ids.get(c.get("target")) if c.get("target") else None
    ox, oy, orot = ev.num(c, "offsetX", ctx, 0.0), ev.num(c, "offsetY", ctx, 0.0), ev.num(c, "offsetRotation", ctx, 0.0)
    tx, ty, rot, sx, sy = _decompose(M)
    T = rc.world_matrix(target, ctx) if target is not None and target is not el else None
    if typ == "parent" and T is not None:
        out = T @ np.linalg.inv(rc.root_matrix) @ M
    elif typ == "look-at" and T is not None:
        gx, gy = T[0, 2], T[1, 2]
        ang = math.degrees(math.atan2(gy - ty, gx - tx)) + orot
        out = _compose(tx, ty, ang, sx, sy) @ _linear_part_inv(M, rot, sx, sy)
    elif typ in ("copy-position", "copy-rotation", "copy-scale", "copy-transform") and T is not None:
        gx, gy, grot, gsx, gsy = _decompose(T)
        if typ in ("copy-position", "copy-transform"):
            tx, ty = gx + ox * rc.scale, gy + oy * rc.scale
        if typ in ("copy-rotation", "copy-transform"):
            rot = grot + orot
        if typ in ("copy-scale", "copy-transform"):
            sx, sy = gsx, gsy
        out = _compose(tx, ty, rot, sx, sy) @ _linear_part_inv(M, *_decompose(M)[2:])
    elif typ == "follow-path" and c.get("path"):
        from .geometry import PathSampler
        key = ("fp", c)
        s = rc.cache.get(key) or PathSampler(c.get("path"))
        rc.cache[key] = s
        x, y, ang = s.at(ev.num(c, "progress", ctx, 0.0))
        k = rc.scale
        out = _compose(x * k + ox * k, y * k + oy * k, (ang if c.get("autoOrient") == "true" else rot) + orot, sx, sy) \
            @ _linear_part_inv(M, rot, sx, sy)
    elif typ == "distance" and T is not None:
        gx, gy = T[0, 2], T[1, 2]
        d = math.hypot(tx - gx, ty - gy)
        lo = ev.num(c, "minDistance", ctx, 0.0) * rc.scale
        hi = ev.num(c, "maxDistance", ctx, float("inf")) * rc.scale
        nd = min(max(d, lo), hi)
        if d > 1e-9 and nd != d:
            tx, ty = gx + (tx - gx) * nd / d, gy + (ty - gy) * nd / d
        out = M.copy()
        out[0, 2], out[1, 2] = tx, ty
    else:
        if typ in ("ik", "track"):
            warn_once("transformConstraint", typ, "not solved by the Python renderer; ignored")
        return M
    return M + (out - M) * influence if influence < 1 else out


def _linear_part_inv(M, rot, sx, sy):
    """Residual linear part (skew, anchor offset) of M after removing translation, rotation and scale."""
    base = _compose(M[0, 2], M[1, 2], rot, sx, sy)
    return np.linalg.inv(base) @ M
