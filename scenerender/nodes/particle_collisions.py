"""Particle contacts with the rendered alpha of rigid-body nodes.

Masks are evaluated on the composition/symbol canvas at one pixel per scene
unit, independent of output resolution. Two samples per emitter are retained.
Matte particles are evaluated through their normal independent histories.
"""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass
import math

import numpy as np

from ..document import ln
from ..physics import get_sim

# Matrix cancellation through instance fit/rotation must not change which fixed
# step owns an exact alpha-boundary contact.
ALPHA_THRESHOLD = .5-1e-10


def transform(M, x, y):
    w = M[2, 0]*x + M[2, 1]*y + M[2, 2]
    with np.errstate(divide='ignore', invalid='ignore'):
        return ((M[0, 0]*x + M[0, 1]*y + M[0, 2])/w,
                (M[1, 0]*x + M[1, 1]*y + M[1, 2])/w)


def scene_location(em, t):
    from ..references import scene_space
    ctx = em.ctx_at(t)
    return scene_space(em.rc, em.el, ctx).node_location(em.el, ctx)


def collision_parent(em, t):
    """Emitter parent coordinates -> the canvas where collider alpha is sampled."""
    if not any(em.rc.is_threed(n) for n in (em.el, *em.el.iterancestors())
               if n is em.el or ln(n) in ('group', 'sequence')):
        return em.parent_doc(t)
    cache = em.collision_transforms
    if t in cache:
        cache.move_to_end(t)
        return cache[t]
    loc = scene_location(em, t)
    rc = copy(loc.rc)
    rc.frame_cache = {}
    M = rc.rendered_matrix(em.el, em.ctx_at(t))
    try:
        parent = None if M is None else M @ np.linalg.inv(em.L(t))
    except np.linalg.LinAlgError:
        parent = None
    cache[t] = parent
    while len(cache) > 8:
        cache.popitem(last=False)
    return parent


def velocity(M, x, y, vx, vy):
    """Apply a homography's Jacobian at the particle's current position."""
    X, Y = transform(M, x, y)
    w = M[2, 0]*x + M[2, 1]*y + M[2, 2]
    with np.errstate(divide='ignore', invalid='ignore'):
        return (((M[0, 0]-X*M[2, 0])*vx+(M[0, 1]-X*M[2, 1])*vy)/w,
                ((M[1, 0]-Y*M[2, 0])*vx+(M[1, 1]-Y*M[2, 1])*vy)/w)


def transport_velocity(P0, P, Pi, x, y, nx, ny, vx, vy, dt):
    """Move a scene-space tangent to a corrected contact position on the plane."""
    if (not len(vx) or (not np.any(P[2, :2]) and not np.any(P0[2, :2])
                       and np.array_equal(P[:2, :2], P0[:2, :2]))):
        return vx, vy
    lx, ly = transform(Pi, x, y)
    ox, oy = transform(P0, lx, ly)
    lvx, lvy = velocity(Pi, x, y, vx-(x-ox)/dt, vy-(y-oy)/dt)
    lx, ly = transform(Pi, nx, ny)
    ox, oy = transform(P0, lx, ly)
    vx, vy = velocity(P, lx, ly, lvx, lvy)
    return vx+(nx-ox)/dt, vy+(ny-oy)/dt


@dataclass
class Collider:
    alpha: np.ndarray
    smooth: np.ndarray
    x0: float
    y0: float
    matrix: np.ndarray
    exits: np.ndarray | None = None

    def sample(self, x, y, smooth=False):
        A = self.smooth if smooth else self.alpha
        # The arrays include a transparent border, including at canvas edges.
        u, v = x-self.x0+.5, y-self.y0+.5
        j, i = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
        fx, fy = u-j, v-i
        h, w = A.shape
        def tap(ii, jj):
            return A[np.clip(ii, 0, h-1), np.clip(jj, 0, w-1)]
        return (tap(i, j)*(1-fx)*(1-fy) + tap(i, j+1)*fx*(1-fy)
                + tap(i+1, j)*(1-fx)*fy + tap(i+1, j+1)*fx*fy)

    def nearest_exit(self, x, y):
        if self.exits is None:
            inside = self.alpha >= ALPHA_THRESHOLD
            adjacent = np.zeros_like(inside)
            adjacent[1:] |= inside[:-1]
            adjacent[:-1] |= inside[1:]
            adjacent[:, 1:] |= inside[:, :-1]
            adjacent[:, :-1] |= inside[:, 1:]
            i, j = np.nonzero(adjacent & ~inside)
            self.exits = np.stack([j+self.x0-.5, i+self.y0-.5], axis=1)
        # Only particles born/engulfed inside a silhouette take this path.
        def nearest(p):
            distances = np.sum((self.exits-p)**2, axis=1)
            # Equivalent instance matrices can perturb an exact grid tie by a
            # few ulps. Preserve the array's stable order for those ties.
            return self.exits[np.flatnonzero(distances <= distances.min()+1e-9)[0]]
        return np.array([nearest(p) for p in zip(x, y)])

    def exit_point(self, x, y, nx, ny):
        """First exterior point along an outward ray, with subpixel refinement."""
        length = np.maximum(np.hypot(nx, ny), 1e-12)
        nx, ny = nx/length, ny/length
        lo, hi = np.zeros_like(x), np.zeros_like(x)
        found = self.sample(x, y) < ALPHA_THRESHOLD
        for step in range(1, math.ceil(math.hypot(*self.alpha.shape)*2)+3):
            idx = np.flatnonzero(~found)
            if not len(idx):
                break
            distance = step*.5
            outside = self.sample(x[idx]+nx[idx]*distance, y[idx]+ny[idx]*distance) < ALPHA_THRESHOLD
            hi[idx[outside]] = distance
            lo[idx[~outside]] = distance
            found[idx[outside]] = True
        for _ in range(24):
            mid = (lo+hi)/2
            outside = self.sample(x+nx*mid, y+ny*mid) < ALPHA_THRESHOLD
            hi = np.where(outside, mid, hi)
            lo = np.where(outside, lo, mid)
        return x+nx*hi, y+ny*hi


def snapshots(em, t):
    ctx = em.ctx_at(t)
    cache = em.colliders
    if ctx in cache:
        cache.move_to_end(ctx)
        return cache[ctx]
    loc = scene_location(em, t)
    rc = copy(loc.rc)
    rc.frame_cache = {}
    rc._flat_depth, rc._raster_to_frame = 0, None
    rc._skip_effects = set()
    rc.motion_blur, rc._node_mb = False, True
    sim = get_sim(rc)
    root = rc.scene_context[0] if rc.scene_context is not None else rc.doc.section('composition')
    clock = rc.scene_context[1] if rc.scene_context is not None else ctx
    rc.width = max(1, round(rc.ev.num(root, 'width', clock, rc.doc.width)))
    rc.height = max(1, round(rc.ev.num(root, 'height', clock, rc.doc.height)))
    rc.scale = 1.
    rc.frame_rect = (0, 0, rc.width, rc.height)
    out = {}
    for el, rb in (sim.body_els if sim is not None else []):
        if el in rc.exclude:
            continue
        pos = rc.node_location(el, ctx)
        c = pos.rc.enter_node(el, pos.ctx)
        if rc.ev.bool(rb, 'sensor', c, False):
            continue
        buf, M = rc.render_contribution(el, ctx, root)
        if buf.is_null:
            continue
        a = np.pad(np.clip(buf.px[..., 3].astype(np.float64), 0, 1), 1)
        if not (a >= ALPHA_THRESHOLD).any():
            continue
        k = np.pad(a, 1)
        sm = (k[:-2, :-2]+2*k[:-2, 1:-1]+k[:-2, 2:]+2*k[1:-1, :-2]
              +4*k[1:-1, 1:-1]+2*k[1:-1, 2:]+k[2:, :-2]+2*k[2:, 1:-1]+k[2:, 2:])/16
        if M is None:  # An echo tail can survive without drawing the body now.
            M = pos.rc.node_matrix(el, c, pos.matrix, pos.box, pos.layout)
        out[el] = Collider(a, sm, float(buf.x0), float(buf.y0), M)
    cache[ctx] = out
    while len(cache) > 2:
        cache.popitem(last=False)
    return out


def first_contact(col, x0, y0, x, y):
    """Bracket the first >= .5 alpha sample along each clipped segment."""
    dx, dy = x-x0, y-y0
    lo, hi = np.zeros_like(x), np.ones_like(x)
    h, w = col.alpha.shape
    for p, d, low, high in ((x0, dx, col.x0-1, col.x0+w-1),
                            (y0, dy, col.y0-1, col.y0+h-1)):
        moving = abs(d) > 1e-12
        den = np.where(moving, d, 1.)
        a, b = (low-p)/den, (high-p)/den
        lo = np.maximum(lo, np.where(moving, np.minimum(a, b), 0.))
        hi = np.minimum(hi, np.where(moving, np.maximum(a, b),
                                    np.where((p >= low) & (p <= high), 1., -1.)))
    valid = hi >= lo
    steps = np.maximum(1, np.ceil(np.hypot(dx, dy)*np.maximum(0, hi-lo)*2-1e-9).astype(int))
    found = np.zeros(len(x), bool)
    left, right = lo.copy(), lo.copy()
    for k in range(int(steps[valid].max())+1 if valid.any() else 0):
        idx = np.flatnonzero(valid & ~found & (k <= steps))
        if not len(idx):
            break
        f = lo[idx]+(hi[idx]-lo[idx])*k/steps[idx]
        hit = col.sample(x0[idx]+dx[idx]*f, y0[idx]+dy[idx]*f) >= ALPHA_THRESHOLD
        right[idx[hit]] = f[hit]
        left[idx[~hit]] = f[~hit]
        found[idx[hit]] = True
    idx = np.flatnonzero(found)
    if not len(idx):
        return idx, x[idx], y[idx]
    for _ in range(24):
        mid = (left[idx]+right[idx])/2
        hit = col.sample(x0[idx]+dx[idx]*mid, y0[idx]+dy[idx]*mid) >= ALPHA_THRESHOLD
        right[idx] = np.where(hit, mid, right[idx])
        left[idx] = np.where(hit, left[idx], mid)
    return idx, x0[idx]+dx[idx]*left[idx], y0[idx]+dy[idx]*left[idx]


def collide(em, x0, y0, x, y, vx, vy, bounce, ts, te):
    """Contacts and velocities in scene units per emitter-local second."""
    if not len(x):
        return x, y, vx, vy
    dt = te-ts
    P0, P = collision_parent(em, ts), collision_parent(em, te)
    if P0 is None or P is None:
        return x, y, vx, vy
    try:
        Pi = np.linalg.inv(P)
    except np.linalg.LinAlgError:
        return x, y, vx, vy
    cols = snapshots(em, te)
    previous = snapshots(em, ts) if cols else {}
    X0, Y0 = transform(P0, x0, y0)
    X, Y = transform(P, x, y)
    # Include parent motion in the velocity entering a scene-space contact.
    ox, oy = transform(P0, x, y)
    with np.errstate(invalid='ignore'):
        pvx, pvy = (X-ox)/dt, (Y-oy)/dt
    VX, VY = velocity(P, x, y, vx, vy)
    VX, VY = VX+pvx, VY+pvy
    valid = (np.isfinite(X0) & np.isfinite(Y0) & np.isfinite(X) & np.isfinite(Y)
             & np.isfinite(VX) & np.isfinite(VY)
             & (P0[2, 0]*x0+P0[2, 1]*y0+P0[2, 2] > 0)
             & (P[2, 0]*x+P[2, 1]*y+P[2, 2] > 0))
    X0, Y0, X, Y, VX, VY = (np.where(valid, a, 0.) for a in (X0, Y0, X, Y, VX, VY))
    loc = scene_location(em, te)
    root, clock = (loc.rc.scene_context if loc.rc.scene_context is not None
                   else (em.rc.doc.section('composition'), em.ctx_at(te)))
    width = em.rc.ev.num(root, 'width', clock, em.rc.doc.width)
    height = em.rc.ev.num(root, 'height', clock, em.rc.doc.height)
    hit = valid & (Y > height)
    nextY = np.where(hit, height-(Y-height)*bounce, Y)
    VX[hit], VY[hit] = transport_velocity(P0, P, Pi, X[hit], Y[hit], X[hit], nextY[hit], VX[hit], VY[hit], dt)
    Y = nextY
    VY = np.where(hit, -np.abs(VY)*bounce, VY)
    VX = np.where(hit, VX*.8, VX)
    for side, sign in ((valid & (X < 0), 1.), (valid & (X > width), -1.)):
        nextX = np.where(side, np.where(sign > 0, -X*bounce, width-(X-width)*bounce), X)
        VX[side], VY[side] = transport_velocity(P0, P, Pi, X[side], Y[side], nextX[side], Y[side], VX[side], VY[side], dt)
        X = nextX
        VX = np.where(side, sign*np.abs(VX)*bounce, VX)
    for el, col in cols.items():
        old = previous.get(el)
        try:
            previous_from_current = (old.matrix @ np.linalg.inv(col.matrix)
                                     if old is not None else np.eye(3))
            current_from_previous = np.linalg.inv(previous_from_current)
        except np.linalg.LinAlgError:
            continue
        # Sweep relative to the body's moving coordinate system. This catches
        # fast particles and bodies even when both endpoints are outside.
        sx, sy = transform(current_from_previous, X0, Y0)
        finite = np.flatnonzero(valid & np.isfinite(sx) & np.isfinite(sy))
        idx, cx, cy = first_contact(col, sx[finite], sy[finite], X[finite], Y[finite])
        idx = finite[idx]
        if not len(idx):
            continue
        nx = col.sample(cx-1, cy, True)-col.sample(cx+1, cy, True)
        ny = col.sample(cx, cy-1, True)-col.sample(cx, cy+1, True)
        engulfed = col.sample(sx[idx], sy[idx]) >= ALPHA_THRESHOLD
        flat = np.hypot(nx, ny) < 1e-9
        repair = engulfed | flat
        if repair.any():
            if flat.any():
                q = col.nearest_exit(cx[flat], cy[flat])
                nx[flat], ny[flat] = q[:, 0]-cx[flat], q[:, 1]-cy[flat]
            cx[repair], cy[repair] = col.exit_point(cx[repair], cy[repair], nx[repair], ny[repair])
            # Contact response follows the surface normal at the exit, not a
            # diagonal direction to a neighbouring exterior pixel centre.
            gx = col.sample(cx-1, cy, True)-col.sample(cx+1, cy, True)
            gy = col.sample(cx, cy-1, True)-col.sample(cx, cy+1, True)
            gradient = repair & (np.hypot(gx, gy) >= 1e-9)
            nx[gradient], ny[gradient] = gx[gradient], gy[gradient]
        length = np.maximum(np.hypot(nx, ny), 1e-12)
        nx, ny = nx/length, ny/length
        cx, cy = cx+.25*nx, cy+.25*ny
        bx, by = transform(previous_from_current, cx, cy)
        ivx, ivy = transport_velocity(P0, P, Pi, X[idx], Y[idx], cx, cy, VX[idx], VY[idx], dt)
        finite = np.isfinite(bx) & np.isfinite(by) & np.isfinite(ivx) & np.isfinite(ivy)
        idx, cx, cy, bx, by = idx[finite], cx[finite], cy[finite], bx[finite], by[finite]
        nx, ny = nx[finite], ny[finite]
        bvx, bvy = (cx-bx)/dt, (cy-by)/dt
        rvx, rvy = ivx[finite]-bvx, ivy[finite]-bvy
        vn = rvx*nx+rvy*ny
        approaching = vn < 0
        normal = np.where(approaching, -bounce*vn, vn)
        friction = np.where(approaching, .8, 1.)
        VX[idx] = bvx+(rvx-vn*nx)*friction+normal*nx
        VY[idx] = bvy+(rvy-vn*ny)*friction+normal*ny
        X[idx], Y[idx] = cx, cy
    lx, ly = transform(Pi, X, Y)
    ox, oy = transform(P0, lx, ly)
    VX, VY = VX-(X-ox)/dt, VY-(Y-oy)/dt
    lvx, lvy = velocity(Pi, X, Y, VX, VY)
    valid &= np.isfinite(lx) & np.isfinite(ly) & np.isfinite(lvx) & np.isfinite(lvy)
    return tuple(np.where(valid, a, b) for a, b in zip((lx, ly, lvx, lvy), (x, y, vx, vy)))
