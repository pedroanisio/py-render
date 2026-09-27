"""Deform modifiers (<deform><modifier type=.../></deform> on layers and shapes) and soft-body warps.

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
  puppet      as-rigid-as-possible deformation (Sorkine & Alexa 2007, local/global iterations, fixed count
              32 so it is deterministic) on a triangulated grid of the node box (~24 cells across, uniform
              edge weights). Pins are at restX/restY (local px) with x/y displacement:
                position: holds its point at rest + (x, y) (exact barycentric constraint, weight 1e4 x amount);
                bend: the same, plus a fixed rotation of @rotation deg (clockwise) for the mesh around it
                  (1.5 cells);
                starch: no position constraint; stiffens the mesh around it (edge weights x (1 + 20 amount)
                  with a Gaussian falloff of two cells).
              Initial rotations come from the bend pins or a Procrustes fit of the position pins. A single
              pin therefore moves the whole tile rigidly, as in After Effects.
  skin        linear blend skinning of a mesh over the node box (~32 cells across). Bones live in the
              skeleton's space: the space of the skeleton element's parent node (composition px for a
              top-level skeleton). Rest pose = un-animated attribute values; pose = animated values plus
              IK (transformConstraint type=ik inside the skeleton: @point names the effector bone, whose
              tip reaches @target; default: the deepest leaf's origin with its ancestors as the chain, or
              the leaf's tip for a two-bone skeleton; two joints analytic with bendPositive, longer chains
              FABRIK). Weights: skeleton/@weights (JSON, below) or, without it, 1/(d^2 + 12^2)^2 of the
              distance d to each rest bone segment (normalised).
              Weights file (JSON):
                {"format": "scenerender-skin-weights", "version": 1,
                 "space": "node" | "skeleton",           coordinates of vertices/grid (default node px)
                 "bones": ["boneId", ...],
                 "grid": {"cols": C, "rows": R},          weights on a C x R lattice over the node box,
                                                          row-major, interpolated bilinearly; or
                 "vertices": [[x, y], ...],               scattered vertices, Shepard (1/d^2) interpolation
                 "weights": [[w per bone], ...] | [{"boneId": w, ...}, ...]}
              Negative weights are clipped, rows normalised; unknown bone ids are ignored (warned).
  bulge/pinch amount percent (pinch = inward), @radius, centre; spherize amount 0..100 percent.
  ripple      amount px, frequency = rings per radius, phase deg, radius (default half diagonal).
  turbulence  amount px, frequency = noise cycles per 100 px, phase deg (evolution), seed.
  corner-pin  corners = 8 numbers x0 y0 .. x3 y3 (TL TR BR BL) in local px; exact homography.
Soft bodies: a node with <softBody> is warped by its simulated grid (physics.soft_world) mapped into
node-local space, upsampled with Catmull-Rom, after its <deform> modifiers.
Mesh warps (puppet, skin, soft bodies) use `mesh_map`: piecewise-affine on the grid's triangles, with an
exact inverse (point location in the deformed triangles; later triangles win where folds overlap;
outside the mesh the nearest border triangle extrapolates).
"""
from __future__ import annotations

import math

import numpy as np

from .document import ln
from .physics import fbm
from .raster import Buf, intersect
from .registry import DEFORMERS, FEATURES, FULL, warn_once

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


# ------------------------------------------------------------------ triangle-mesh resampler
def grid_rest(w: float, h: float, nx: int, ny: int) -> np.ndarray:
    gx, gy = np.meshgrid(np.linspace(0, w, nx + 1), np.linspace(0, h, ny + 1))
    return np.stack([gx, gy], -1)


def grid_tris(ny: int, nx: int) -> np.ndarray:
    """Triangles (v00, v10, v11) and (v00, v11, v01) of every cell of a (ny+1) x (nx+1) vertex grid."""
    r, c = np.meshgrid(np.arange(ny), np.arange(nx), indexing="ij")
    v00 = (r * (nx + 1) + c).ravel()
    v10, v01 = v00 + 1, v00 + nx + 1
    v11 = v01 + 1
    return np.concatenate([np.stack([v00, v10, v11], 1), np.stack([v00, v11, v01], 1)])


def mesh_map(rest: np.ndarray, deformed: np.ndarray):
    """(forward, inverse) maps of a piecewise-affine warp of a regular grid.

    rest: (ny+1, nx+1, 2) regular grid positions (node-local), deformed: same shape. The forward map is
    affine on the two triangles of each cell (extrapolated from the border cells outside the grid).
    The inverse is exact: query points are bucketed and tested against every deformed triangle
    (where folds overlap, the later triangle in row-major order wins); points outside the deformed
    mesh use the affine map of the nearest border triangle, so spill outside the box follows."""
    ny, nx = rest.shape[0] - 1, rest.shape[1] - 1
    x0, y0 = rest[0, 0]
    dx = (rest[0, -1, 0] - x0) / nx if nx else 1.0
    dy = (rest[-1, 0, 1] - y0) / ny if ny else 1.0
    D = deformed

    def f(x, y):
        gx, gy = (x - x0) / dx, (y - y0) / dy
        j = np.clip(np.floor(gx).astype(np.int64), 0, nx - 1)
        i = np.clip(np.floor(gy).astype(np.int64), 0, ny - 1)
        fx, fy = gx - j, gy - i
        up = fx >= fy
        p00, p10, p11, p01 = D[i, j], D[i, j + 1], D[i + 1, j + 1], D[i + 1, j]
        w00 = np.where(up, 1 - fx, 1 - fy)[..., None]
        w10 = np.where(up, fx - fy, 0.0)[..., None]
        w11 = np.where(up, fy, fx)[..., None]
        w01 = np.where(up, 0.0, fy - fx)[..., None]
        out = p00 * w00 + p10 * w10 + p11 * w11 + p01 * w01
        return out[..., 0], out[..., 1]

    tris = grid_tris(ny, nx)
    Rv, Dv = rest.reshape(-1, 2), D.reshape(-1, 2)
    A, B, C = Dv[tris[:, 0]], Dv[tris[:, 1]], Dv[tris[:, 2]]
    det = (B[:, 0] - A[:, 0]) * (C[:, 1] - A[:, 1]) - (C[:, 0] - A[:, 0]) * (B[:, 1] - A[:, 1])
    ok = np.abs(det) > 1e-12
    lo, hi = np.minimum(np.minimum(A, B), C), np.maximum(np.maximum(A, B), C)
    cell = max(float(np.median(hi[ok] - lo[ok])) if ok.any() else 1.0, 1e-6)
    r_, c_ = np.divmod(np.arange(len(tris)) % (nx * ny), nx)
    border = np.nonzero((r_ == 0) | (r_ == ny - 1) | (c_ == 0) | (c_ == nx - 1))[0]
    border = border[ok[border]]

    def bary(t, X, Y):
        a, b, c = A[t], B[t], C[t]
        dt = det[t]
        l1 = ((b[..., 0] - X) * (c[..., 1] - Y) - (c[..., 0] - X) * (b[..., 1] - Y)) / dt
        l2 = ((c[..., 0] - X) * (a[..., 1] - Y) - (a[..., 0] - X) * (c[..., 1] - Y)) / dt
        return l1, l2, 1 - l1 - l2

    def g(qx, qy):
        shape = np.shape(qx)
        X, Y = np.ravel(qx).astype(np.float64), np.ravel(qy).astype(np.float64)
        n = len(X)
        tri_of = np.full(n, -1, np.int64)
        gx0, gy0 = float(lo[:, 0].min()), float(lo[:, 1].min())
        nbx = int((hi[:, 0].max() - gx0) // cell) + 1
        nby = int((hi[:, 1].max() - gy0) // cell) + 1
        bx = np.floor((X - gx0) / cell).astype(np.int64)
        by = np.floor((Y - gy0) / cell).astype(np.int64)
        inside_grid = (bx >= 0) & (bx < nbx) & (by >= 0) & (by < nby) & np.isfinite(X) & np.isfinite(Y)
        bid = np.where(inside_grid, by * nbx + bx, -1)
        order = np.argsort(bid, kind="stable")
        sb = bid[order]
        first = np.searchsorted(sb, 0)
        starts = np.searchsorted(sb, np.arange(nby * nbx + 1))
        tb0 = np.floor((lo - [gx0, gy0]) / cell).astype(np.int64)
        tb1 = np.floor((hi - [gx0, gy0]) / cell).astype(np.int64)
        for t in np.nonzero(ok)[0]:
            cand = [order[starts[yy * nbx + tb0[t, 0]]:starts[yy * nbx + tb1[t, 0] + 1]] for yy in range(tb0[t, 1], tb1[t, 1] + 1)]
            cand = np.concatenate(cand) if cand else np.zeros(0, np.int64)
            if len(cand) == 0:
                continue
            l1, l2, l3 = bary(t, X[cand], Y[cand])
            e = -1e-9
            hit = (l1 >= e) & (l2 >= e) & (l3 >= e)
            tri_of[cand[hit]] = t
        del first
        out = np.full((n, 2), -1e7)
        has = tri_of >= 0
        miss = np.nonzero(~has & np.isfinite(X) & np.isfinite(Y))[0]
        if len(miss) and len(border):
            cen = (A[border] + B[border] + C[border]) / 3
            step = max(1, 4_000_000 // len(border))
            for s0 in range(0, len(miss), step):
                mm = miss[s0:s0 + step]
                d2 = (X[mm, None] - cen[:, 0]) ** 2 + (Y[mm, None] - cen[:, 1]) ** 2
                tri_of[mm] = border[np.argmin(d2, 1)]
        sel = np.nonzero(tri_of >= 0)[0]
        if len(sel):
            t = tri_of[sel]
            l1, l2, l3 = bary(t, X[sel], Y[sel])
            out[sel] = (Rv[tris[t, 0]] * l1[:, None] + Rv[tris[t, 1]] * l2[:, None] + Rv[tris[t, 2]] * l3[:, None])
        return out[:, 0].reshape(shape), out[:, 1].reshape(shape)
    return f, g


def _cr_axis(G: np.ndarray, k: int, axis: int) -> np.ndarray:
    """Catmull-Rom upsampling of grid G by integer factor k along axis (end tangents clamped)."""
    if k <= 1 or G.shape[axis] < 2:
        return G
    G = np.moveaxis(G, axis, 0)
    n = G.shape[0]
    P = np.concatenate([2 * G[:1] - G[1:2], G, 2 * G[-1:] - G[-2:-1]])
    out = []
    for i in range(n - 1):
        p0, p1, p2, p3 = P[i], P[i + 1], P[i + 2], P[i + 3]
        for s in range(k):
            u = s / k
            out.append(0.5 * (2 * p1 + (p2 - p0) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u
                              + (3 * p1 - p0 - 3 * p2 + p3) * u ** 3))
    out.append(G[-1])
    return np.moveaxis(np.stack(out), 0, axis)


def smooth_grid(G: np.ndarray, target_cells: int = 32) -> np.ndarray:
    ny, nx = G.shape[0] - 1, G.shape[1] - 1
    ky = max(1, int(math.ceil(target_cells / max(ny, 1)))) if ny else 1
    kx = max(1, int(math.ceil(target_cells / max(nx, 1)))) if nx else 1
    k = max(1, min(ky, kx) if min(ny, nx) > 1 else max(ky, kx) // 4 + 1)
    return _cr_axis(_cr_axis(G, k, 0), k, 1)


def _cells(w, h, n: int = 24) -> tuple[int, int]:
    c = max(w, h, 1e-6) / n
    return max(2, int(round(w / c))), max(2, int(round(h / c)))


def _locate(rest_grid, p):
    """(triangle vertex indices, barycentric weights) of rest point p in a regular grid mesh."""
    ny, nx = rest_grid.shape[0] - 1, rest_grid.shape[1] - 1
    x0, y0 = rest_grid[0, 0]
    dx = (rest_grid[0, -1, 0] - x0) / nx
    dy = (rest_grid[-1, 0, 1] - y0) / ny
    gx, gy = (p[0] - x0) / dx, (p[1] - y0) / dy
    j, i = int(np.clip(math.floor(gx), 0, nx - 1)), int(np.clip(math.floor(gy), 0, ny - 1))
    fx, fy = gx - j, gy - i
    v00 = i * (nx + 1) + j
    v10, v01, v11 = v00 + 1, v00 + nx + 1, v00 + nx + 2
    if fx >= fy:
        return [v00, v10, v11], [1 - fx, fx - fy, fy]
    return [v00, v11, v01], [1 - fy, fx, fy - fx]


# ------------------------------------------------------------------ puppet (ARAP)
ARAP_ITERS = 32


@DEFORMERS.register("puppet", level=FULL,
                    note="as-rigid-as-possible (Sorkine-Alexa local/global, fixed iterations) on a triangulated "
                         "grid of the node box; position, bend and starch pins")
def build_puppet(rc, m, ctx, w, h, node):
    ev = rc.ev
    pins = []
    for p in m:
        if ln(p) != "pin":
            continue
        kind = p.get("kind", "position")
        rest = np.array([ev.num(p, "restX", ctx, 0.0), ev.num(p, "restY", ctx, 0.0)])
        disp = np.array([ev.num(p, "x", ctx, 0.0), ev.num(p, "y", ctx, 0.0)])
        rot = ev.num(p, "rotation", ctx, 0.0) if kind == "bend" else 0.0
        pins.append((kind, rest, disp, rot, max(0.0, ev.num(p, "amount", ctx, 1.0))))
    ctrl = [q for q in pins if q[0] != "starch" and q[4] > 0]
    if not ctrl or all(not q[2].any() and q[3] == 0 for q in ctrl) or w <= 0 or h <= 0:
        return None
    nx, ny = _cells(w, h)
    rest = grid_rest(w, h, nx, ny)
    Rv = rest.reshape(-1, 2)
    n = len(Rv)
    tris = grid_tris(ny, nx)
    E = np.unique(np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), 1), axis=0)
    cell = max(w / nx, h / ny)
    wts = np.ones(len(E))
    mid = (Rv[E[:, 0]] + Rv[E[:, 1]]) / 2
    for kind, prest, _, _, amt in pins:
        if kind == "starch" and amt > 0:
            wts *= 1 + 20 * amt * np.exp(-np.sum((mid - prest) ** 2, 1) / (2 * cell) ** 2)
    rows = []
    for kind, prest, disp, rot, amt in ctrl:
        vi, bw = _locate(rest, prest)
        rows.append((vi, np.array(bw), prest + disp, 1e4 * amt, kind, rot, prest))
    key = ("arap", m, w, h, tuple((tuple(r[0]), r[3]) for r in rows), tuple(np.round(wts[:8], 9)))
    Ainv = rc.cache.get(key)
    if Ainv is None:
        L = np.zeros((n, n))
        np.add.at(L, (E[:, 0], E[:, 1]), -wts)
        np.add.at(L, (E[:, 1], E[:, 0]), -wts)
        np.add.at(L, (E[:, 0], E[:, 0]), wts)
        np.add.at(L, (E[:, 1], E[:, 1]), wts)
        for vi, bw, _, lam, *_ in rows:
            L[np.ix_(vi, vi)] += lam * np.outer(bw, bw)
        Ainv = rc.cache[key] = np.linalg.inv(L)
    cons = np.zeros((n, 2))
    for vi, bw, tgt, lam, *_ in rows:
        cons[vi] += lam * bw[:, None] * tgt[None, :]
    fixed = np.full(n, np.nan)
    for vi, bw, tgt, lam, kind, rot, prest in rows:
        if kind == "bend":
            near = np.sum((Rv - prest) ** 2, 1) <= (1.5 * cell) ** 2
            fixed[near | np.isin(np.arange(n), vi)] = math.radians(rot)
    eR = Rv[E[:, 0]] - Rv[E[:, 1]]
    # initial rotations: the bend pins' mean rotation, else the best-fit (Procrustes) rotation of the
    # position pins, so large rigid motions converge within the fixed iteration count
    bends = [math.radians(r[5]) for r in rows if r[4] == "bend"]
    th0 = 0.0
    if bends:
        th0 = math.atan2(sum(math.sin(b) for b in bends), sum(math.cos(b) for b in bends))
    elif len(rows) >= 2:
        A0 = np.array([r[6] for r in rows])
        B0 = np.array([r[2] for r in rows])
        a0, b0 = A0 - A0.mean(0), B0 - B0.mean(0)
        th0 = math.atan2(float(np.sum(a0[:, 0] * b0[:, 1] - a0[:, 1] * b0[:, 0])), float(np.sum(a0 * b0)))
    theta = np.where(np.isnan(fixed), th0, fixed)

    def global_step(th):
        c, s = np.cos(th), np.sin(th)
        ci, si, cj, sj = c[E[:, 0]], s[E[:, 0]], c[E[:, 1]], s[E[:, 1]]
        rx = ((ci + cj) * eR[:, 0] - (si + sj) * eR[:, 1]) * wts / 2
        ry = ((si + sj) * eR[:, 0] + (ci + cj) * eR[:, 1]) * wts / 2
        b = np.zeros((n, 2))
        np.add.at(b, E[:, 0], np.stack([rx, ry], 1))
        np.add.at(b, E[:, 1], -np.stack([rx, ry], 1))
        return Ainv @ (b + cons)

    P = global_step(theta)
    for _ in range(ARAP_ITERS):
        eP = P[E[:, 0]] - P[E[:, 1]]
        S = np.zeros((n, 4))
        terms = np.stack([eR[:, 0] * eP[:, 0], eR[:, 0] * eP[:, 1], eR[:, 1] * eP[:, 0], eR[:, 1] * eP[:, 1]], 1) * wts[:, None]
        np.add.at(S, E[:, 0], terms)
        np.add.at(S, E[:, 1], terms)
        th = np.arctan2(S[:, 1] - S[:, 2], S[:, 0] + S[:, 3])
        theta = np.where(np.isnan(fixed), th, fixed)
        P = global_step(theta)
    return mesh_map(rest, P.reshape(rest.shape))


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


def skeleton_frame(rc, skel, ctx) -> np.ndarray:
    """Frame matrix of skeleton space: the space of the skeleton's parent node (composition px at the top)."""
    from .constraints import ROOTS
    p = skel.getparent()
    if p is None or ln(p) in ROOTS:
        return rc.root_matrix
    return rc.world_matrix(p, ctx)


def _world_of(bones, by_id, local) -> dict:
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
    return world


def skeleton_pose(rc, skel, ctx, rest: bool = False) -> dict:
    """bone element -> 3x3 matrix in skeleton space (parents first); IK applied unless rest."""
    bones = [b for b in skel if ln(b) == "bone"]
    by_id = {b.get("id"): b for b in bones}
    local = {b: _bone_local(rc, b, ctx, rest) for b in bones}
    world = _world_of(bones, by_id, local)
    if rest:
        return world
    for c in skel:
        if ln(c) == "transformConstraint" and c.get("type") == "ik":
            world = _skeleton_ik(rc, skel, c, bones, by_id, local, world, ctx)
        elif ln(c) == "transformConstraint":
            warn_once("skeleton", f"constraint:{c.get('type')}", "skeletons only use ik constraints")
    return world


def _skeleton_ik(rc, skel, c, bones, by_id, local, world, ctx):
    """Effector: the tip of the bone named by @point (chain: it and all its ancestors), else the origin
    of the deepest leaf bone (chain: its ancestors) or, for a two-bone skeleton, the leaf's tip."""
    from .constraints import chain_deltas, target_point
    tgt = rc.doc.ids.get(c.get("target")) if c.get("target") else None
    if tgt is None:
        warn_once("skeleton", f"ik:{c.get('target')}", "ik target not found")
        return world
    parent_of = {b: by_id.get(b.get("parent")) for b in bones}

    def ancestors(b):
        out = []
        while parent_of.get(b) is not None and len(out) < 64:
            b = parent_of[b]
            out.append(b)
        return out[::-1]
    eff = by_id.get(c.get("point")) if c.get("point") else None
    if eff is not None:
        chain, tip = ancestors(eff) + [eff], (eff, float(eff.get("length", 0) or 0))
    else:
        leaves = [b for b in bones if not any(parent_of[k] is b for k in bones)]
        if not leaves:
            return world
        leaf = max(leaves, key=lambda b: len(ancestors(b)))
        anc = ancestors(leaf)
        if len(anc) >= 2:
            chain, tip = anc, (leaf, 0.0)
        elif anc:
            chain, tip = anc + [leaf], (leaf, float(leaf.get("length", 0) or 0))
        else:
            return world
    F = skeleton_frame(rc, skel, ctx)
    space = c.get("space") or "world"
    goal_f = target_point(rc, tgt, ctx, space, F)
    goal = (np.linalg.inv(F) @ np.array([*goal_f, 1.0]))[:2]
    goal = goal + np.array([rc.ev.num(c, "offsetX", ctx, 0.0), rc.ev.num(c, "offsetY", ctx, 0.0)])
    infl = rc.ev.num(c, "influence", ctx, 1.0)
    pts = [world[b][:2, 2] for b in chain] + [(world[tip[0]] @ np.array([tip[1], 0.0, 1.0]))[:2]]
    deltas = chain_deltas(np.array(pts), goal, c.get("bendPositive", "true") not in ("false", "0"))
    for b, d in zip(chain, deltas):
        mirror = np.linalg.det(world[b][:2, :2]) < 0
        local[b] = local[b] @ _rot3(math.radians(d * infl) * (-1 if mirror else 1))
        world = _world_of(bones, by_id, local)
    return world


def _rot3(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float64)


def load_weights(rc, skel):
    """Parse skeleton/@weights (see module docstring) -> (kind, bones, data) or None."""
    src = skel.get("weights")
    if not src:
        return None
    key = ("skin-weights", src)
    if key in rc.cache:
        return rc.cache[key]
    import json
    out = None
    try:
        with open(rc.doc.resolve_path(src), encoding="utf-8") as fh:
            js = json.load(fh)
        bones = list(js["bones"])
        W = js["weights"]
        if W and isinstance(W[0], dict):
            W = [[float(row.get(b, 0.0)) for b in bones] for row in W]
        W = np.clip(np.asarray(W, np.float64), 0, None)
        space = js.get("space", "node")
        if "vertices" in js:
            V = np.asarray(js["vertices"], np.float64)
            if len(V) != len(W):
                raise ValueError("vertices and weights differ in length")
            out = ("points", bones, (V, W), space)
        else:
            g = js.get("grid", {})
            cols, rows = int(g["cols"]), int(g["rows"])
            if rows * cols != len(W) or rows < 1 or cols < 1:
                raise ValueError("grid rows*cols must equal the number of weight rows")
            out = ("grid", bones, (rows, cols, W.reshape(rows, cols, -1)), space)
    except (OSError, ValueError, KeyError, TypeError) as e:
        warn_once("skeleton", f"weights:{src}", f"cannot read skin weights: {e}; using automatic weights")
    rc.cache[key] = out
    return out


def _file_weights(spec, P_node, P_skel, w, h) -> np.ndarray:
    kind, _, data, space = spec
    P = P_skel if space == "skeleton" else P_node
    if kind == "grid":
        rows, cols, G = data
        gx = np.clip(P[:, 0] / max(w, 1e-9) * (cols - 1), 0, cols - 1) if cols > 1 else np.zeros(len(P))
        gy = np.clip(P[:, 1] / max(h, 1e-9) * (rows - 1), 0, rows - 1) if rows > 1 else np.zeros(len(P))
        j0 = np.minimum(np.floor(gx).astype(int), max(cols - 2, 0))
        i0 = np.minimum(np.floor(gy).astype(int), max(rows - 2, 0))
        j1, i1 = np.minimum(j0 + 1, cols - 1), np.minimum(i0 + 1, rows - 1)
        fx, fy = (gx - j0)[:, None], (gy - i0)[:, None]
        return (G[i0, j0] * (1 - fx) * (1 - fy) + G[i0, j1] * fx * (1 - fy) + G[i1, j0] * (1 - fx) * fy
                + G[i1, j1] * fx * fy)
    V, W = data
    d2 = np.sum((P[:, None, :] - V[None]) ** 2, -1)
    exact = d2 < 1e-12
    iw = np.where(exact, 1e12, 1.0 / np.maximum(d2, 1e-12))
    return (iw @ W) / iw.sum(1, keepdims=True)


@DEFORMERS.register("skin", level=FULL,
                    note="linear blend skinning on a mesh of the node box; skeleton/@weights JSON or bone-distance "
                         "weights; bones in the skeleton's parent space; IK two-bone/FABRIK")
def build_skin(rc, m, ctx, w, h, node, M=None):
    skel = rc.doc.ids.get(m.get("skeleton")) if m.get("skeleton") else None
    if skel is None or M is None:
        if skel is None:
            warn_once("deform", f"skin:{m.get('skeleton')}", "skeleton not found")
        return None
    rest = skeleton_pose(rc, skel, ctx, rest=True)
    pose = skeleton_pose(rc, skel, ctx)
    bones = list(rest)
    if not bones or w <= 0 or h <= 0:
        return None
    S = np.array([pose[b] @ np.linalg.inv(rest[b]) for b in bones])
    if all(np.allclose(s, np.eye(3), atol=1e-9) for s in S):
        return None
    K = np.linalg.inv(skeleton_frame(rc, skel, ctx)) @ M          # node-local -> skeleton space
    Ki = np.linalg.inv(K)
    nx, ny = _cells(w, h, 32)
    grid = grid_rest(w, h, nx, ny)
    Pn = grid.reshape(-1, 2)
    Ps = Pn @ K[:2, :2].T + K[:2, 2]
    spec = load_weights(rc, skel)
    Wt = None
    if spec is not None:
        idx = {b.get("id"): i for i, b in enumerate(bones)}
        cols = [idx.get(bid) for bid in spec[1]]
        for bid, ci in zip(spec[1], cols):
            if ci is None:
                warn_once("skeleton", f"weights-bone:{bid}", "weights file names an unknown bone; ignored")
        fw = _file_weights(spec, Pn, Ps, w, h)
        Wt = np.zeros((len(Pn), len(bones)))
        for k, ci in enumerate(cols):
            if ci is not None:
                Wt[:, ci] += fw[:, k]
        if not (Wt.sum(1) > 1e-12).all():
            warn_once("skeleton", f"weights-empty:{skel.get('id')}", "some vertices have no weight; they stay put")
    if Wt is None:
        Wt = np.zeros((len(Pn), len(bones)))
        for k, b in enumerate(bones):
            L = float(b.get("length", 0) or 0)
            p0 = rest[b][:2, 2]
            p1 = (rest[b] @ np.array([L, 0, 1.0]))[:2]
            d = p1 - p0
            L2 = float(d @ d)
            u = np.clip(((Ps - p0) @ d) / L2, 0, 1) if L2 > 0 else np.zeros(len(Ps))
            q = p0 + u[:, None] * d
            Wt[:, k] = 1.0 / (np.sum((Ps - q) ** 2, 1) + 12.0 ** 2) ** 2
    tot = Wt.sum(1, keepdims=True)
    Wt = np.where(tot > 1e-12, Wt / np.where(tot > 1e-12, tot, 1.0), 0.0)
    moved = np.einsum("bij,nj->nbi", S[:, :2, :], np.concatenate([Ps, np.ones((len(Ps), 1))], 1))
    out = np.einsum("nb,nbi->ni", Wt, moved)
    out = np.where(tot > 1e-12, out, Ps)
    local = out @ Ki[:2, :2].T + Ki[:2, 2]
    return mesh_map(grid, local.reshape(grid.shape))


# ------------------------------------------------------------------ soft bodies
def build_soft(rc, el, ctx, w, h, M):
    """Warp from the simulated soft-body grid of el (physics.soft_world) into node-local space."""
    from .physics import soft_world
    if abs(M[2, 0]) + abs(M[2, 1]) > 1e-12:
        return None
    res = soft_world(rc, el, ctx.comp_t)
    if res is None:
        return None
    s, pw = res
    K = np.linalg.inv(M) @ rc.root_matrix                       # document px -> node-local
    loc = pw @ K[:2, :2].T + K[:2, 2]
    if s.kind == "rope":
        n = len(loc)
        tng = np.gradient(loc, axis=0)
        tl = np.hypot(tng[:, 0], tng[:, 1])
        tng = tng / np.where(tl > 1e-12, tl, 1.0)[:, None]
        nrm = np.stack([-tng[:, 1], tng[:, 0]], 1)
        if s.rows == 1:                                           # horizontal rope: rows y=0 and y=h
            D = np.stack([loc - nrm * s.thick, loc + nrm * s.thick], 0)
            R = grid_rest(w, h, n - 1, 1)
        else:
            D = np.stack([loc + nrm * s.thick, loc - nrm * s.thick], 1)
            R = grid_rest(w, h, 1, n - 1)
    else:
        D = loc.reshape(s.rows, s.cols, 2)
        R = grid_rest(w, h, s.cols - 1, s.rows - 1)
    if np.allclose(D, R, atol=1e-6):
        return None
    D = smooth_grid(D)
    R = grid_rest(w, h, D.shape[1] - 1, D.shape[0] - 1)
    return mesh_map(R, D)


# ------------------------------------------------------------------ driver
def modifiers_of(el) -> list:
    return [m for d in el if ln(d) == "deform" for m in d if ln(m) == "modifier"]


def has_soft(el) -> bool:
    return any(ln(c) == "softBody" for c in el)


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
    if has_soft(el) and M is not None:
        res = build_soft(rc, el, ctx, w, h, M)
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
    tags = {ln(e) for e in comp.iter() if isinstance(e.tag, str)}
    if "deform" in tags or "softBody" in tags:
        rc.hooks["deform"] = apply_deform


try:
    from .render import hook_installer
    hook_installer("deform")(install)
except ImportError:      # pragma: no cover
    pass
