"""Deterministic, seekable 2D physics: rigid bodies, soft bodies, joints and force fields.

Also hosts the small simulation utilities shared by particles, shape modifiers and deformers:
`Seekable` (fixed-step state cache with random access), `hash01` (vectorised seeded hash),
`vnoise`/`fbm` (seeded smooth value noise), `PathGeom` (exact closest-point / arc-length queries on
a path) and `field_accel` (every force-field type, vectorised, for bodies, soft bodies and particles).

World (pinned; the schema leaves these open)
  * Simulation space is the composition: document pixels -> metres with physics/@pixelsPerMeter,
    +y up (gravityY=-9.8 pulls down the screen). Angles on the XML side are degrees clockwise on
    screen; internally radians counter-clockwise (y up).
  * Fixed step physics/@fixedStep from physics/@start; states are checkpointed (Seekable), so any
    frame can be rendered in any order with identical results. Before physics/@start and before
    rigidBody/@activateAt a dynamic body follows its keyframes (kinematic); static and kinematic bodies
    always follow their keyframes.
  * Solver: sequential impulses with physics/@solverIterations iterations, Baumgarte factor 0.2, linear
    slop 5 mm and speculative contacts (4 slops), Coulomb friction sqrt(fa*fb), restitution max(ea, eb).
  * bounds: floor = the frame's bottom edge; frame = all four frame edges (friction 0.6, restitution 0).

Rigid bodies (rigidBody)
  * The body frame is the node box under the node's world transform at activation (skew and
    non-uniform scale are baked into the shape). mass is the total mass (kg); the centre of mass and
    the moment of inertia come from the shape's area (uniform density).
  * shape box: the node box.  circle: @radius (m) or min(w, h)/2 about the box centre.
    capsule: along the longer box axis, radius @radius or half the shorter side (Box2D capsule).
    polygon: the vertices of @path (a points list "x y x y ..." or path data, whose curve control
    points are ignored) in node-local px. path: @path path data flattened (0.25 px), or without @path
    the node's own outline (shape nodes, modifiers applied); both are filled nonzero and split into
    convex pieces (constrained Delaunay + Hertel-Mehlhorn), holes included. convex-hull: the convex hull
    of the node's rendered alpha (>= 0.5) at activation, pixel corners quantised to 1/16 px.
    Without a usable @path / alpha the box is used (warned).
  * collisionGroup g and collidesWith ("all", "none" or a list of groups): a pair collides iff each
    body's collidesWith accepts the other's group (Box2D category/mask, symmetric AND). sensor bodies
    never produce contact responses; their overlaps are recorded per step (`sensor_overlaps`).
    fixedRotation: infinite inertia. bullet: continuous collision (conservative advancement to the
    time of impact against every other non-sensor body and the bounds), so fast bodies never tunnel.
  * velocityX/Y (m/s, +y up) and angularVelocity (deg/s clockwise) apply at activation.
    linearDamping/angularDamping (1/s): v /= 1 + dt*damping every step.

Soft bodies (softBody)
  * The node box becomes a rows x cols grid of point masses (@mass split evenly) simulated in the same
    world. jelly: structural and shear springs of @stiffness (N/m each) plus bend springs (every
    second node) of stiffness/4. cloth: structural springs of @stiffness (threads), shear springs of
    0.15*stiffness and bend springs of stiffness/50, so a pinned cloth drapes by shearing without
    folding over itself. rope: one chain of max(rows, cols) masses
    along the box's longer centre line, stretch-only links plus bend springs of stiffness/10, drawn as
    a ribbon of the box thickness.
    Every spring has damping ratio @damping. @pressure (N/m, 2D gas pressure) pushes the outline along
    its outward normal by pressure*(A0/A - 1) per unit length (A0 = rest area), so jelly keeps its
    volume; it applies to grids of any kind.
  * pin top|bottom|left|right|corners holds those nodes (rope: the corresponding ends) on the node's
    keyframed pose, so pinned soft bodies follow their node's animation. Unpinned nodes are free from
    physics/@start (the node's keyframes after that only move the pins, like dynamic rigid bodies).
  * Nodes collide (restitution 0.2, friction 0.5, 1 cm skin) with the bounds and with every non-sensor
    rigid body (two-way: the body receives the opposite impulse). selfCollision keeps non-adjacent
    nodes at least half a grid spacing apart. A node with both rigidBody and softBody anchors every grid
    node to the rigid pose with a spring of @stiffness (it wobbles about the body).
  * Each fixed step is split into as many substeps as the springs need to stay stable
    (omega*h <= 0.25, at most 4096 as the schema's validation rule requires).
  * The rendered tile is warped by the simulated grid (smoothly upsampled) through deform.mesh_map.

Joints (physics/constraint); anchor = (x, y) px, else b's centre (a's centre without b)
  * pin: a to the world point, rigid; with @stiffness a zero-length spring (damping ratio @damping).
  * hinge: revolute joint a-b (or a-world); minAngle/maxAngle (deg, clockwise, of b relative to a,
    0 = the initial relative angle) are one-sided limits; @damping is viscous joint damping (N m s/rad);
    with @stiffness the anchor becomes a spring.
  * weld: rigid point + angle lock; with @stiffness a spring at the anchor plus an angular spring of
    stiffness*r^2 (r = radius of gyration of b).
  * slider: prismatic joint along axisAngle (deg clockwise from +x, fixed in a's frame or the world);
    rotation locked; @restLength limits the travel to +/- restLength m; motorSpeed (m/s) with maxForce
    (N, unlimited when absent) drives it; @damping is viscous (N s/m).
  * motor: a hinge whose relative angular velocity is driven to motorSpeed (deg/s clockwise) with at
    most maxForce (N m, unlimited when absent); honours minAngle/maxAngle.
  * distance: rigid rod of restLength (m, default the initial distance); rope: maximum distance;
    spring: Hooke k=@stiffness (default 50 N/m) with damping ratio @damping; distance with
    @stiffness behaves as a spring.
  * breakForce: when the constraint force of a step (|impulse|/dt, N; torque N m for purely angular
    joints) exceeds it, the constraint is removed for the rest of the simulation.

Force fields (physics/forceField, `field_accel`)
  * x, y, radius, path: composition px. Accelerations are m/s^2 for bodies and px/s^2 for particles,
    +y up for both (forceY > 0 pushes up). falloff: with radius, (1 - d/radius)^falloff inside the
    radius and nothing outside; without, 1/(1 + d)^falloff with d in metres. start/end: active window.
  * directional: (forceX, forceY). wind: the same vector as an air velocity with seeded gusts
    (+-25 %); with strength > 0 the acceleration is strength*(wind - v) (aerodynamic drag), otherwise
    the vector itself. radial: strength away from (x, y) (negative attracts). vortex: strength,
    clockwise on screen. turbulence: seeded fbm noise of period @scale metres (unit RMS) times
    strength. drag: -strength*v. attractor-path: strength toward the exact closest point of @path (the
    distance to the path drives the falloff) plus forceX along the path's tangent at that point
    ("tangent follow"; negative flows backwards).

Cache (physics/@cache, @cacheSha256)
  * When @cache names an existing file whose sha256 matches @cacheSha256 (if given) and whose
    fingerprint (sha256 of the physics section and of every body node's XML) matches the document, the
    recorded poses are replayed instead of simulating. When the file is absent it is written after
    simulating to the end of the project (npz: per-step rigid poses and soft-body nodes). A mismatching
    file is reported and ignored (never overwritten).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field

import numpy as np

from .document import ln
from .registry import FEATURES, FULL, log, warn_once
from .values import parse_bool

FEATURES.declare("physics", FULL, "2D rigid and soft bodies, joints, force fields, bounds, cache; see physics.py")
FEATURES.declare("rigidBody", FULL, "box/circle/capsule/polygon/path/convex-hull, groups, sensors, bullets (CCD)")
FEATURES.declare("softBody", FULL, "jelly/cloth/rope mass-spring grids with pressure, pins, self-collision; the "
                                   "node's tile is warped by the grid")
for _t in ("pin", "hinge", "distance", "rope", "spring", "weld", "slider", "motor"):
    FEATURES.declare(f"physicsConstraint:{_t}", FULL)
for _t in ("directional", "radial", "vortex", "turbulence", "drag", "wind", "attractor-path"):
    FEATURES.declare(f"forceField:{_t}", FULL)

SLOP = 0.005
SPEC = 4 * SLOP
BETA = 0.2
MAX_SUBSTEPS = 4096
CACHE_VERSION = 1


# ====================================================================== shared utilities
_M64 = (1 << 64) - 1


def _mix64(z: np.ndarray) -> np.ndarray:
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return z ^ (z >> np.uint64(31))


def hash01(seed: int, ids, channel: int = 0) -> np.ndarray:
    """Uniform [0, 1) per id: a pure function of (seed, id, channel). ids: int array."""
    with np.errstate(over="ignore"):
        a = np.asarray(ids, dtype=np.int64).astype(np.uint64)
        z = a * np.uint64(0x9E3779B97F4A7C15) + np.uint64((seed * 0x632BE59BD9B4E019 + channel * 0xD1B54A32D192ED03) & _M64)
        z = _mix64(_mix64(z))
    return (z >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def _lattice(seed: int, ix, iy, iz) -> np.ndarray:
    with np.errstate(over="ignore"):
        k = (ix.astype(np.int64).astype(np.uint64) * np.uint64(0x8DA6B343)
             ^ iy.astype(np.int64).astype(np.uint64) * np.uint64(0xD8163841)
             ^ iz.astype(np.int64).astype(np.uint64) * np.uint64(0xCB1AB31F))
        z = _mix64(k + np.uint64(seed & _M64) * np.uint64(0x9E3779B97F4A7C15))
    return (z >> np.uint64(11)).astype(np.float64) / float(1 << 52) - 1.0


def vnoise(seed: int, x, y, z=0.0) -> np.ndarray:
    """Seeded smooth value noise in [-1, 1] (quintic interpolation of hashed lattice values)."""
    x, y, z = np.broadcast_arrays(np.asarray(x, np.float64), np.asarray(y, np.float64), np.asarray(z, np.float64))
    x0, y0, z0 = np.floor(x), np.floor(y), np.floor(z)
    fx, fy, fz = x - x0, y - y0, z - z0
    q = lambda f: f * f * f * (f * (f * 6 - 15) + 10)  # noqa: E731
    ux, uy, uz = q(fx), q(fy), q(fz)
    out = 0.0
    for dz in (0, 1):
        wz = uz if dz else 1 - uz
        for dy in (0, 1):
            wy = uy if dy else 1 - uy
            for dx in (0, 1):
                wx = ux if dx else 1 - ux
                out = out + wx * wy * wz * _lattice(seed, x0 + dx, y0 + dy, z0 + dz)
    return out


def fbm(seed: int, x, y, z=0.0, octaves: int = 2) -> np.ndarray:
    out, amp, norm, f = 0.0, 1.0, 0.0, 1.0
    for k in range(max(1, int(octaves))):
        out = out + amp * vnoise(seed + 7919 * k, np.asarray(x) * f, np.asarray(y) * f, np.asarray(z) * f)
        norm += amp
        amp *= 0.5
        f *= 2.0
    return out / norm


class Seekable:
    """Random access to a fixed-step simulation: `at(n)` is the state after n steps and does not
    depend on which steps were requested before (every state comes from the same recurrence,
    resumed from a checkpoint or from the last computed state).

    init() -> state;  step(state, i) mutates state from step i to step i + 1.
    """

    def __init__(self, init, step, every: int = 60, max_steps: int = 10_000_000):
        self._step = step
        self.every = max(1, every)
        self.max_steps = max_steps
        s0 = init()
        self.checkpoints: dict[int, object] = {0: copy.deepcopy(s0)}
        self.last_n, self.last = 0, s0

    def at(self, n: int):
        n = max(0, min(int(n), self.max_steps))
        if self.last_n == n:
            return self.last
        base = max(k for k in self.checkpoints if k <= n)
        if base <= self.last_n <= n:
            i, s = self.last_n, self.last
        else:
            i, s = base, copy.deepcopy(self.checkpoints[base])
        while i < n:
            self._step(s, i)
            i += 1
            if i % self.every == 0 and i not in self.checkpoints:
                self.checkpoints[i] = copy.deepcopy(s)
        self.last_n, self.last = n, s
        return s


class _State:
    """deepcopy copies only the fields named in _dyn (mutable simulation state); static data such as
    XML elements and shapes is shared, so checkpoints stay cheap and elements keep their identity."""
    _dyn: tuple = ()

    def __deepcopy__(self, memo):
        new = copy.copy(self)
        memo[id(self)] = new
        for k in self._dyn:
            setattr(new, k, copy.deepcopy(getattr(self, k), memo))
        return new


# ====================================================================== exact path queries
class PathGeom:
    """A path flattened finely (0.02 px) with subpaths kept apart: closest points with tangents and
    arc-length sampling that never bridges the gap between subpaths."""

    def __init__(self, d, tol: float = 0.02):
        from . import geometry
        cmds = geometry.parse_svg_path(d) if isinstance(d, str) else d
        a, b = [], []
        for pts, _closed in geometry.flatten(cmds, tol):
            P = np.asarray(pts, np.float64)
            if len(P) == 1:
                P = np.vstack([P, P])
            a.append(P[:-1])
            b.append(P[1:])
        if not a:
            a, b = [np.zeros((1, 2))], [np.zeros((1, 2))]
        self.a, self.b = np.concatenate(a), np.concatenate(b)
        self.e = self.b - self.a
        self.len = np.hypot(self.e[:, 0], self.e[:, 1])
        self.cum = np.concatenate([[0.0], np.cumsum(self.len)])
        self.total = float(self.cum[-1])
        self.l2 = np.maximum(self.len ** 2, 1e-18)

    def closest(self, X, Y):
        """(qx, qy, tx, ty, dist) for points X, Y: exact closest point on the (finely flattened) path
        and the unit tangent there."""
        X, Y = np.asarray(X, np.float64).ravel(), np.asarray(Y, np.float64).ravel()
        n, m = len(X), len(self.a)
        qx, qy, tx, ty, dist = (np.zeros(n) for _ in range(5))
        step = max(1, 2_000_000 // max(m, 1))
        for s in range(0, n, step):
            x, y = X[s:s + step, None], Y[s:s + step, None]
            u = np.clip(((x - self.a[:, 0]) * self.e[:, 0] + (y - self.a[:, 1]) * self.e[:, 1]) / self.l2, 0, 1)
            px, py = self.a[:, 0] + u * self.e[:, 0], self.a[:, 1] + u * self.e[:, 1]
            d2 = (px - x) ** 2 + (py - y) ** 2
            k = np.argmin(d2, 1)
            r = np.arange(len(k))
            qx[s:s + step], qy[s:s + step] = px[r, k], py[r, k]
            dist[s:s + step] = np.sqrt(d2[r, k])
            L = np.where(self.len[k] > 0, self.len[k], 1.0)
            tx[s:s + step], ty[s:s + step] = self.e[k, 0] / L, self.e[k, 1] / L
        return qx, qy, tx, ty, dist

    def sample(self, u):
        """Points at arc-length fractions u in [0, 1]: (x, y, angle deg)."""
        u = np.clip(np.asarray(u, np.float64), 0, 1)
        d = u * self.total
        k = np.clip(np.searchsorted(self.cum, d, side="right") - 1, 0, len(self.a) - 1)
        f = np.where(self.len[k] > 0, (d - self.cum[k]) / np.where(self.len[k] > 0, self.len[k], 1.0), 0.0)
        return (self.a[k, 0] + self.e[k, 0] * f, self.a[k, 1] + self.e[k, 1] * f,
                np.degrees(np.arctan2(self.e[k, 1], self.e[k, 0])))


def path_geom(rc, d: str) -> PathGeom:
    key = ("path-geom", d)
    g = rc.cache.get(key)
    if g is None:
        g = rc.cache[key] = PathGeom(d)
    return g


# ====================================================================== force fields
def field_active(rc, f, t: float) -> bool:
    from .evaluator import Ctx
    c = Ctx(t=t, comp_t=t)
    s = rc.ev.num(f, "start", c, 0.0)
    return t >= s and (f.get("end") is None or t < rc.ev.num(f, "end", c, 0.0))


def field_accel(rc, f, t: float, X, Y, VX, VY, ppm: float):
    """Acceleration of force field f at composition time t on points X, Y (document px, +y down) with
    velocities VX, VY (the consumer's units, screen orientation +y down). Returns (AX, AY) in the
    consumer's units (m/s^2 for bodies, px/s^2 for particles), screen orientation."""
    from .evaluator import Ctx
    X, Y = np.asarray(X, np.float64), np.asarray(Y, np.float64)
    zero = np.zeros_like(X)
    if not field_active(rc, f, t):
        return zero, zero
    ev = rc.ev
    c = Ctx(t=t, comp_t=t)
    typ = f.get("type")
    strength = ev.num(f, "strength", c, 0.0)
    cx, cy = ev.num(f, "x", c, 0.0), ev.num(f, "y", c, 0.0)
    radius = ev.num(f, "radius", c, 0.0) if f.get("radius") is not None or _animated(f, "radius") else 0.0
    falloff = ev.num(f, "falloff", c, 0.0)

    def fall(d_px):
        if radius > 0:
            k = np.clip(1 - d_px / radius, 0, 1)
            return np.where(d_px < radius, k ** falloff if falloff > 0 else 1.0, 0.0)
        return 1.0 / (1.0 + d_px / ppm) ** falloff if falloff > 0 else np.ones_like(d_px)

    dx, dy = X - cx, Y - cy
    d = np.hypot(dx, dy)
    ds = np.where(d > 1e-9, d, 1.0)
    if typ in ("directional", "wind"):
        fx, fy = ev.num(f, "forceX", c, 0.0), -ev.num(f, "forceY", c, 0.0)
        k = fall(d) if radius > 0 else 1.0
        if typ == "directional":
            return zero + fx * k, zero + fy * k
        g = 1.0 + 0.25 * fbm(ev.seed_for(f, "gust"), t * 0.7 + zero, Y / (4 * ppm), 0.0)
        wx, wy = fx * g, fy * g
        if strength > 0:
            return strength * (wx - VX) * k, strength * (wy - VY) * k
        return wx * k, wy * k
    if typ == "radial":
        k = strength * fall(d)
        return np.where(d > 1e-9, dx / ds, 0.0) * k, np.where(d > 1e-9, dy / ds, 0.0) * k
    if typ == "vortex":
        k = strength * fall(d)
        return np.where(d > 1e-9, -dy / ds, 0.0) * k, np.where(d > 1e-9, dx / ds, 0.0) * k
    if typ == "turbulence":
        sc = max(1e-6, ev.num(f, "scale", c, 1.0)) * ppm
        seed = ev.seed_for(f, "turb")
        k = strength * 3.0 * (fall(d) if radius > 0 else 1.0)
        return fbm(seed, X / sc, Y / sc, t * 0.5) * k, fbm(seed + 1, X / sc, Y / sc, t * 0.5) * k
    if typ == "drag":
        k = strength * (fall(d) if radius > 0 else 1.0)
        return -np.asarray(VX) * k + zero, -np.asarray(VY) * k + zero
    if typ == "attractor-path":
        if not f.get("path"):
            return zero, zero
        pg = path_geom(rc, f.get("path"))
        qx, qy, tx, ty, dist = pg.closest(X, Y)
        qx, qy, tx, ty, dist = (v.reshape(X.shape) for v in (qx, qy, tx, ty, dist))
        k = fall(dist)
        dd = np.where(dist > 1e-9, dist, 1.0)
        along = ev.num(f, "forceX", c, 0.0)
        return ((qx - X) / dd * (dist > 1e-9) * strength + tx * along) * k, ((qy - Y) / dd * (dist > 1e-9) * strength + ty * along) * k
    return zero, zero


def _animated(el, name) -> bool:
    return any(ln(a) in ("animate", "expression", "link") and a.get("property") == name for a in el)


# ====================================================================== geometry helpers
def _rot(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


def _cross(a, b) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _cross_sv(s, v):
    return np.array([-s * v[1], s * v[0]])


def _signed_area(v: np.ndarray) -> float:
    return 0.5 * float(np.sum(v[:, 0] * np.roll(v[:, 1], -1) - np.roll(v[:, 0], -1) * v[:, 1]))


def _normals(v: np.ndarray) -> np.ndarray:
    e = np.roll(v, -1, axis=0) - v
    L = np.hypot(e[:, 0], e[:, 1])
    L = np.where(L > 1e-12, L, 1.0)
    return np.stack([e[:, 1] / L, -e[:, 0] / L], 1)


def convex_hull(pts: np.ndarray) -> np.ndarray:
    """Monotone chain; CCW (y up) without collinear points."""
    P = np.unique(np.asarray(pts, np.float64), axis=0)
    if len(P) < 3:
        return P
    P = P[np.lexsort((P[:, 1], P[:, 0]))]

    def half(seq):
        h: list = []
        for p in seq:
            while len(h) >= 2 and _cross(h[-1] - h[-2], p - h[-2]) <= 1e-12:
                h.pop()
            h.append(p)
        return h
    lo, hi = half(P), half(P[::-1])
    return np.array(lo[:-1] + hi[:-1])


def _is_convex(v: np.ndarray) -> bool:
    e = np.roll(v, -1, 0) - v
    cr = e[:, 0] * np.roll(e[:, 1], -1) - e[:, 1] * np.roll(e[:, 0], -1)
    return bool(np.all(cr >= -1e-9 * max(1.0, float(np.abs(v).max()) ** 2)))


def convex_pieces(poly) -> list[np.ndarray]:
    """Convex CCW pieces of a shapely (Multi)Polygon: constrained Delaunay triangles merged greedily
    across shared edges while the union stays convex (Hertel-Mehlhorn)."""
    import shapely
    geoms = list(getattr(poly, "geoms", [poly]))
    out: list[np.ndarray] = []
    for g in geoms:
        if g.is_empty or g.area <= 0:
            continue
        if not g.interiors and g.convex_hull.area - g.area <= 1e-9 * g.area:
            v = np.asarray(g.exterior.coords)[:-1]
            out.append(v if _signed_area(v) > 0 else v[::-1])
            continue
        tris = [np.asarray(t.exterior.coords)[:-1] for t in shapely.constrained_delaunay_triangles(g).geoms]
        pieces = [[tuple(p) for p in (t if _signed_area(t) > 0 else t[::-1])] for t in tris if abs(_signed_area(t)) > 1e-14]
        merged = True
        while merged:
            merged = False
            edges: dict = {}
            for i, pc in enumerate(pieces):
                for j in range(len(pc)):
                    edges.setdefault((pc[j], pc[(j + 1) % len(pc)]), i)
            for (a, b), i in list(edges.items()):
                k = edges.get((b, a))
                if k is None or k == i or pieces[i] is None or pieces[k] is None:
                    continue
                P, Q = pieces[i], pieces[k]
                ia, iq = P.index(b), Q.index(a)
                cand = [P[(ia + s) % len(P)] for s in range(len(P) - 1)] + [Q[(iq + s) % len(Q)] for s in range(len(Q) - 1)]
                if _is_convex(np.array(cand)):
                    pieces[i], pieces[k] = cand, None
                    merged = True
                    break
            pieces = [p for p in pieces if p is not None]
        out += [np.array(p) for p in pieces]
    return out


# ====================================================================== bodies
@dataclass
class Part:
    loc: np.ndarray                # (k, 2) core vertices, body frame (m, relative to the COM); CCW for k >= 3
    r: float = 0.0                 # rounding radius (circle: k=1, capsule: k=2)


@dataclass(eq=False)
class Body(_State):
    el: object
    kind: str                      # static | kinematic | dynamic
    shape: str
    parts: list = field(default_factory=list)
    c_off: np.ndarray = field(default_factory=lambda: np.zeros(2))   # box centre in the body frame
    hx: float = 0.5
    hy: float = 0.5
    pos: np.ndarray = field(default_factory=lambda: np.zeros(2))     # centre of mass (m, y up)
    angle: float = 0.0             # radians, counter-clockwise (y up)
    vel: np.ndarray = field(default_factory=lambda: np.zeros(2))
    w: float = 0.0
    mass: float = 1.0
    inertia: float = 1.0
    inv_m: float = 0.0
    inv_i: float = 0.0
    friction: float = 0.5
    restitution: float = 0.0
    lin_damp: float = 0.0
    ang_damp: float = 0.0
    activate_at: float = 0.0
    active: bool = False           # simulated (dynamic and past activateAt)
    group: int = 0
    collides: object = "all"
    sensor: bool = False
    bullet: bool = False
    v0: tuple = (0.0, 0.0, 0.0)
    fixed_rotation: bool = False
    bound: float = 1.0
    _dyn = ("pos", "vel")

    def world_parts(self):
        R = _rot(self.angle)
        for p in self.parts:
            v = p.loc @ R.T + self.pos
            yield v, (_normals(v) if len(v) >= 2 else None), p.r

    def box_centre(self) -> np.ndarray:
        return self.pos + _rot(self.angle) @ self.c_off

    def radius_bound(self) -> float:
        return self.bound

    @property
    def movable(self) -> bool:
        return self.inv_m > 0 or self.inv_i > 0


def _part_mass(p: Part) -> tuple[float, np.ndarray, float]:
    """(area, centroid, polar second moment about the origin) per unit density."""
    v, r = p.loc, p.r
    if len(v) == 1:
        A = math.pi * r * r
        return A, v[0].copy(), A * (0.5 * r * r + float(v[0] @ v[0]))
    if len(v) == 2:
        L = float(np.hypot(*(v[1] - v[0])))
        m = (v[0] + v[1]) / 2
        box, circ = 2 * r * L, math.pi * r * r
        h, lc = L / 2, 4 * r / (3 * math.pi)
        J = circ * (0.5 * r * r + h * h + 2 * h * lc) + box * (4 * r * r + L * L) / 12
        return box + circ, m, J + (box + circ) * float(m @ m)
    x, y = v[:, 0], v[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cr = x * y1 - x1 * y
    A = 0.5 * float(cr.sum())
    if abs(A) < 1e-15:
        return 0.0, v.mean(0), 0.0
    c = np.array([float(((x + x1) * cr).sum()), float(((y + y1) * cr).sum())]) / (6 * A)
    J = float((cr * (x * x + x * x1 + x1 * x1 + y * y + y * y1 + y1 * y1)).sum()) / 12
    return A, c, J


# ---------------------------------------------------------------- narrow phase
def _max_sep(v1, n1, v2):
    S = np.einsum("ijk,ik->ij", v2[None, :, :] - v1[:, None, :], n1)
    m = S.min(1)
    i = int(np.argmax(m))
    return float(m[i]), i


def _seg_closest(p, a, b):
    e = b - a
    L2 = float(e @ e)
    u = 0.0 if L2 < 1e-18 else min(1.0, max(0.0, float((p - a) @ e) / L2))
    return a + e * u


def _poly_distance(va, vb):
    """Closest points between two disjoint convex cores (k >= 2 each)."""
    best = (1e18, None, None)
    for P, Q, swap in ((va, vb, False), (vb, va, True)):
        k = len(Q)
        for p in P:
            for j in range(k if k > 2 else 1):
                q = _seg_closest(p, Q[j], Q[(j + 1) % k])
                d = float(np.hypot(*(p - q)))
                if d < best[0]:
                    best = (d, q, p) if swap else (d, p, q)
    return best


def _point_poly(c, rc_, v, n, rv):
    """Circle (centre c, radius rc_) against a rounded core v: [(normal poly->circle, point, depth)]."""
    R = rc_ + rv
    k = len(v)
    if k >= 3:
        s = np.einsum("ij,ij->i", c - v, n)
        i = int(np.argmax(s))
        if s[i] <= 0:                       # centre inside the core: out through the nearest face
            nn = n[i]
            return [(nn, (c - nn * rc_ + c - nn * s[i] + nn * rv) / 2, R - s[i])]
    best = None
    for j in range(k if k > 2 else 1):
        q = _seg_closest(c, v[j], v[(j + 1) % k])
        d = float(np.hypot(*(c - q)))
        if best is None or d < best[0]:
            best = (d, q)
    d, q = best
    if d > R + SPEC:
        return []
    if d > 1e-9:
        nn = (c - q) / d
    else:
        nn = n[0] if n is not None else np.array([0.0, 1.0])
    return [(nn, (q + nn * rv + c - nn * rc_) / 2, R - d)]


def _manifold(va, na, ra, vb, nb, rb):
    """Contacts between rounded convex cores: [(normal a->b, point, depth)] (depth < 0: speculative)."""
    R = ra + rb
    if len(va) == 1 and len(vb) == 1:
        d = vb[0] - va[0]
        dist = float(np.hypot(*d))
        if dist > R + SPEC:
            return []
        n = d / dist if dist > 1e-9 else np.array([0.0, 1.0])
        return [(n, (va[0] + n * ra + vb[0] - n * rb) / 2, R - dist)]
    if len(va) == 1:
        return [(-n, p, dep) for n, p, dep in _point_poly(va[0], ra, vb, nb, rb)]
    if len(vb) == 1:
        return _point_poly(vb[0], rb, va, na, ra)
    sa, ia = _max_sep(va, na, vb)
    if sa > R + SPEC:
        return []
    sb, ib = _max_sep(vb, nb, va)
    if sb > R + SPEC:
        return []
    if sb > sa + 1e-4:
        ref, rn, rr, inc, inn, ri_, ri, flip = vb, nb, rb, va, na, ra, ib, True
    else:
        ref, rn, rr, inc, inn, ri_, ri, flip = va, na, ra, vb, nb, rb, ia, False
    nref = rn[ri]
    if max(sa, sb) > 1e-4 and R > 0:
        d, pa, pb = _poly_distance(va, vb)
        if d > R + SPEC:
            return []
        n = (pb - pa) / d if d > 1e-9 else (-nref if flip else nref)
        if float(n @ (-nref if flip else nref)) < 0.999:
            return [(n, (pa + n * ra + pb - n * rb) / 2, R - d)]
    k_inc = len(inc)
    ii = int(np.argmin(inn @ nref))
    i1, i2 = inc[ii], inc[(ii + 1) % k_inc]
    v1, v2 = ref[ri], ref[(ri + 1) % len(ref)]
    tng = v2 - v1
    tl = float(np.hypot(*tng))
    if tl < 1e-12:
        return []
    tng = tng / tl

    def clip(pts, nrm, off):
        out = []
        d0, d1 = float(nrm @ pts[0]) - off, float(nrm @ pts[1]) - off
        if d0 <= 0:
            out.append(pts[0])
        if d1 <= 0:
            out.append(pts[1])
        if d0 * d1 < 0:
            out.append(pts[0] + (pts[1] - pts[0]) * (d0 / (d0 - d1)))
        return out
    pts = clip([i1, i2], -tng, -float(tng @ v1))
    if len(pts) < 2:
        return []
    pts = clip(pts, tng, float(tng @ v2))
    if len(pts) < 2:
        return []
    out = []
    for p in pts:
        s = float(nref @ (p - v1))
        if s <= R + SPEC:
            out.append((-nref if flip else nref, p + nref * (rr - ri_ - s) / 2, R - s))
    return out


def _core_distance(va, vb) -> float:
    """Distance between convex cores (0 when they overlap)."""
    ka, kb = len(va), len(vb)
    if ka == 1 and kb == 1:
        return float(np.hypot(*(va[0] - vb[0])))
    if ka == 1 or kb == 1:
        c, v = (va[0], vb) if ka == 1 else (vb[0], va)
        k = len(v)
        if k >= 3 and np.all(np.einsum("ij,ij->i", c - v, _normals(v)) <= 0):
            return 0.0
        return min(float(np.hypot(*(c - _seg_closest(c, v[j], v[(j + 1) % k])))) for j in range(k if k > 2 else 1))
    if max(_max_sep(va, _normals(va), vb)[0], _max_sep(vb, _normals(vb), va)[0]) <= 0:
        return 0.0
    return _poly_distance(va, vb)[0]


def body_distance(A: Body, B: Body) -> float:
    best = 1e18
    for va, _, ra in A.world_parts():
        for vb, _, rb in B.world_parts():
            best = min(best, _core_distance(va, vb) - ra - rb)
    return best


@dataclass
class Contact:
    a: Body
    b: Body | None                 # None = static world plane
    n: np.ndarray                  # from a to b
    p: np.ndarray
    depth: float
    pn: float = 0.0
    pt: float = 0.0
    bias: float = 0.0
    mn: float = 0.0
    mt: float = 0.0


def _collide(A: Body, B: Body) -> list[Contact]:
    if float(np.hypot(*(A.pos - B.pos))) > A.bound + B.bound + SPEC:
        return []
    out = []
    for va, na, ra in A.world_parts():
        for vb, nb, rb in B.world_parts():
            out += [Contact(A, B, n, p, d) for n, p, d in _manifold(va, na, ra, vb, nb, rb)]
    return out


def _collide_plane(A: Body, n: np.ndarray, off: float) -> list[Contact]:
    """Half-space {p : n.p >= off} is free; contacts where the body reaches below it."""
    out = []
    for v, _, r in A.world_parts():
        for p in v:
            d = float(n @ p) - off - r
            if d < SPEC:
                out.append(Contact(A, None, -n, p - n * (r + d / 2), -d))
    return out


# ---------------------------------------------------------------- joints
@dataclass(eq=False)
class Joint(_State):
    el: object
    kind: str
    P: Body | None                 # first body (None = the world)
    Q: Body
    lp: np.ndarray                 # anchor in P's frame, or the world point
    lq: np.ndarray                 # anchor in Q's frame
    rest: float = 0.0
    stiffness: float | None = None
    damping: float = 0.0
    break_force: float | None = None
    ref_angle: float = 0.0
    lower: float | None = None     # relative angle limits (rad, CCW)
    upper: float | None = None
    axis: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0]))   # slider axis in P's frame / world
    travel: float | None = None
    motor_speed: float = 0.0
    max_force: float | None = None
    motor: bool = False
    broken: bool = False
    acc_lin: np.ndarray = field(default_factory=lambda: np.zeros(2))
    acc_ang: float = 0.0
    acc_lo: float = 0.0
    acc_hi: float = 0.0
    acc_mot: float = 0.0
    acc_ax: float = 0.0
    _dyn = ("P", "Q", "acc_lin")


def _bpos(b):
    return b.pos if b is not None else np.zeros(2)


def _bang(b):
    return b.angle if b is not None else 0.0


def _bw(b):
    return b.w if b is not None else 0.0


def _bim(b):
    return (b.inv_m, b.inv_i) if b is not None else (0.0, 0.0)


def _apply_impulse(body: Body | None, P: np.ndarray, r: np.ndarray) -> None:
    if body is None:
        return
    body.vel = body.vel + P * body.inv_m
    body.w += body.inv_i * _cross(r, P)


def _vel_at(body: Body | None, r: np.ndarray) -> np.ndarray:
    if body is None:
        return np.zeros(2)
    return body.vel + _cross_sv(body.w, r)


def _anchors(j: Joint):
    rp = (_rot(j.P.angle) @ j.lp) if j.P is not None else np.zeros(2)
    pp = (j.P.pos + rp) if j.P is not None else j.lp
    rq = _rot(j.Q.angle) @ j.lq
    return rp, pp, rq, j.Q.pos + rq


def _apply_ang(P, Q, L):
    if P is not None:
        P.w -= P.inv_i * L
    Q.w += Q.inv_i * L


# ---------------------------------------------------------------- soft bodies
@dataclass(eq=False)
class Soft(_State):
    el: object
    kind: str
    rows: int
    cols: int
    rest: np.ndarray               # (n, 2) node-local px
    pos: np.ndarray                # (n, 2) world m
    vel: np.ndarray
    m: float
    k: float
    zeta: float
    pressure: float
    springs: np.ndarray            # (s, 2)
    rest_len: np.ndarray
    kspring: np.ndarray
    tension_only: np.ndarray       # (s,) springs that resist stretching only (cloth, rope)
    pinned: np.ndarray
    ring: np.ndarray
    area0: float
    self_coll: bool
    rigid: Body | None
    anchor_loc: np.ndarray | None  # (n, 2) rest positions in the rigid body frame
    substeps: int
    skin: float
    spacing: float
    thick: float = 0.0             # rope ribbon half thickness (node px)
    neighbours: np.ndarray | None = None
    _dyn = ("pos", "vel", "rigid")


def soft_substeps(mass: float, n: int, k: float, zeta: float, dt: float, rigid: bool) -> float:
    m = mass / n
    c = 2.0 * zeta * math.sqrt(k * m)
    spn = 9.0 if rigid else 8.0
    omega = math.sqrt(spn * k / m)
    return max(1.0, math.ceil(omega * dt / 0.25), math.ceil(spn * c * dt / (m * 0.5)))


def _points_vs_part(P, v, n, r):
    """Signed distance of points P to a rounded convex core, the outward normal and closest point."""
    k = len(v)
    if k == 1:
        d = P - v[0]
        L = np.hypot(d[:, 0], d[:, 1])
        Ls = np.where(L > 1e-12, L, 1.0)
        nrm = np.where(L[:, None] > 1e-12, d / Ls[:, None], np.array([0.0, 1.0]))
        return L - r, nrm
    best_d = np.full(len(P), np.inf)
    best_q = np.zeros_like(P)
    for j in range(k if k > 2 else 1):
        a, b = v[j], v[(j + 1) % k]
        e = b - a
        u = np.clip(((P - a) @ e) / max(float(e @ e), 1e-18), 0, 1)
        q = a + u[:, None] * e
        d = np.hypot(*(P - q).T)
        m = d < best_d
        best_d = np.where(m, d, best_d)
        best_q[m] = q[m]
    diff = P - best_q
    Ls = np.where(best_d > 1e-12, best_d, 1.0)
    nrm = diff / Ls[:, None]
    sd = best_d - r
    if k >= 3:
        s = np.einsum("pkj,kj->pk", P[:, None, :] - v[None], n)
        inside = s.max(1) <= 0
        fi = np.argmax(s, 1)
        sd = np.where(inside, s.max(1) - r, sd)
        nrm = np.where(inside[:, None], n[fi], nrm)
    elif k == 2:
        on = best_d <= 1e-12
        nrm = np.where(on[:, None], n[0], nrm)
    return sd, nrm


# ====================================================================== the world
@dataclass
class World:
    bodies: list
    joints: list
    softs: list = field(default_factory=list)
    t: float = 0.0
    sensor_hits: tuple = ()


def _parse_groups(s: str):
    s = (s or "all").strip()
    if s in ("", "all"):
        return "all"
    if s == "none":
        return set()
    return {int(v) for v in s.replace(",", " ").split() if v.lstrip("-").isdigit()}


class PhysicsSim:
    def __init__(self, rc):
        self.rc = rc
        self.doc = rc.doc
        ph = self.doc.section("physics")
        self.ph = ph
        ev = rc.ev
        from .evaluator import Ctx
        c0 = Ctx(t=0.0, comp_t=0.0)
        self.dt = max(1e-4, float(ph.get("fixedStep", 1 / 120)) if ph is not None else 1 / 120)
        self.ppm = float(ph.get("pixelsPerMeter", 100)) if ph is not None else 100.0
        self.gx = ev.num(ph, "gravityX", c0, 0.0) if ph is not None else 0.0
        self.gy = ev.num(ph, "gravityY", c0, -9.80665) if ph is not None else -9.80665
        self.iters = int(ev.num(ph, "solverIterations", c0, 8)) if ph is not None else 8
        self.start = ev.num(ph, "start", c0, 0.0) if ph is not None else 0.0
        self.bounds = ph.get("bounds", "none") if ph is not None else "none"
        self.fields = [f for f in (ph if ph is not None else []) if ln(f) == "forceField"
                       and f.get("affects", "all") in ("all", "bodies")]
        self.body_els, self.soft_els = [], []
        comp = self.doc.section("composition")
        for el in comp.iter():
            if not isinstance(el.tag, str):
                continue
            rb = next((c for c in el if ln(c) == "rigidBody"), None)
            if rb is not None:
                self.body_els.append((el, rb))
            sb = next((c for c in el if ln(c) == "softBody"), None)
            if sb is not None:
                self.soft_els.append((el, sb))
        self.index = {el: i for i, (el, _) in enumerate(self.body_els)}
        self.soft_index = {el: i for i, (el, _) in enumerate(self.soft_els)}
        self.busy = False
        W_, H_ = self.doc.width / self.ppm, self.doc.height / self.ppm
        self.planes = []
        if self.bounds in ("floor", "frame"):
            self.planes.append((np.array([0.0, 1.0]), -H_))
        if self.bounds == "frame":
            self.planes += [(np.array([1.0, 0.0]), 0.0), (np.array([-1.0, 0.0]), -W_), (np.array([0.0, -1.0]), 0.0)]
        self.cached = None
        self.sim = Seekable(self._init, self._step, every=120)
        if ph is not None and ph.get("cache"):
            self._setup_cache(ph)

    # ------------------------------------------------------------ node poses (keyframed)
    def _ctx(self, t):
        from .evaluator import Ctx
        return Ctx(t=t, comp_t=t)

    def world_doc(self, el, t) -> np.ndarray:
        """Node-local -> composition document pixels at time t (keyframes only, no caches)."""
        rc = self.rc
        from .nodes.core import child_ctx, _repeat_vars
        chain = []
        p = el
        while p is not None and ln(p) not in ("composition", "symbol", "symbols", "scene"):
            chain.append(p)
            p = p.getparent()
        chain.reverse()
        ctx = self._ctx(t)
        M = np.eye(3)
        box = (float(self.doc.width), float(self.doc.height))
        for node in chain:
            nctx = rc.enter_node(node, ctx)
            prev, self.busy = self.busy, True
            try:
                M = rc.node_matrix(node, nctx, M, box, None)
            finally:
                self.busy = prev
            box = rc.node_size(node, rc.node_ctx(node, nctx), box)
            ctx = _repeat_vars(node, child_ctx(rc, node, nctx))
        return M

    def _size(self, el, t):
        rc = self.rc
        parent = el.getparent()
        box = (float(self.doc.width), float(self.doc.height))
        if parent is not None and ln(parent) in ("group", "sequence"):
            box = rc.node_size(parent, rc.node_ctx(parent, self._ctx(t)), box)
        return rc.node_size(el, rc.node_ctx(el, self._ctx(t)), box)

    def keyed_pose(self, el, t):
        """(box centre m, angle rad CCW, half extents m (hx, hy)) from keyframes."""
        w, h = self._size(el, t)
        W = self.world_doc(el, t)
        c = W @ np.array([w / 2, h / 2, 1.0])
        sx = math.hypot(W[0, 0], W[1, 0])
        sy = math.hypot(W[0, 1], W[1, 1])
        ang = math.atan2(W[1, 0], W[0, 0])
        return (np.array([c[0] / self.ppm, -c[1] / self.ppm]), -ang,
                (abs(w * sx) / 2 / self.ppm, abs(h * sy) / 2 / self.ppm))

    def to_body_frame(self, W, w, h, pts_px: np.ndarray) -> np.ndarray:
        """Node-local px points -> body frame metres about the box centre (pose from W)."""
        c = W @ np.array([w / 2, h / 2, 1.0])
        P = np.asarray(pts_px, np.float64)
        q = P @ W[:2, :2].T + W[:2, 2] - c[:2]
        m = np.stack([q[:, 0], -q[:, 1]], 1) / self.ppm
        a = -math.atan2(W[1, 0], W[0, 0])
        return m @ _rot(-a).T

    # ------------------------------------------------------------ shapes
    def _parts(self, el, rb, t0, hx, hy) -> tuple[str, list[Part]]:
        ev = self.rc.ev
        c0 = self._ctx(t0)
        shape = (rb.get("shape") or "box").strip()
        box = [Part(np.array([[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]], np.float64))]
        w, h = self._size(el, t0)
        W = self.world_doc(el, t0)
        if shape == "box":
            corners = self.to_body_frame(W, w, h, np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64))
            if abs(_signed_area(corners)) > 1e-12:
                return shape, [Part(corners if _signed_area(corners) > 0 else corners[::-1])]
            return shape, box
        if shape in ("circle", "capsule"):
            r = ev.num(rb, "radius", c0, 0.0)
            r = r if r > 0 else min(hx, hy)
            if shape == "circle":
                return shape, [Part(np.zeros((1, 2)), max(r, 1e-4))]
            L = max(hx, hy) - r
            if L <= 1e-6:
                return "circle", [Part(np.zeros((1, 2)), max(r, 1e-4))]
            seg = np.array([[-L, 0.0], [L, 0.0]]) if hx >= hy else np.array([[0.0, -L], [0.0, L]])
            return shape, [Part(seg, r)]
        if shape in ("polygon", "path"):
            polys = self._path_polys(el, rb, shape, c0, w, h)
            if polys is not None:
                parts = [Part(self._ccw(self.to_body_frame(W, w, h, v))) for v in polys]
                parts = [p for p in parts if abs(_signed_area(p.loc)) > 1e-12]
                if parts:
                    return shape, parts
            warn_once("rigidBody", f"{shape}:{el.get('id', '?')}", "no usable polygon; using the box")
            return shape, box
        if shape == "convex-hull":
            hull = self._alpha_hull(el, t0, w, h)
            if hull is not None and len(hull) >= 3:
                return shape, [Part(self._ccw(self.to_body_frame(W, w, h, hull)))]
            warn_once("rigidBody", f"convex-hull:{el.get('id', '?')}", "node has no alpha; using the box")
            return shape, box
        warn_once("rigidBody", f"shape:{shape}", "unknown shape; using the box")
        return "box", box

    @staticmethod
    def _ccw(v):
        return v if _signed_area(v) > 0 else v[::-1]

    def _path_polys(self, el, rb, shape, c0, w, h):
        from shapely.geometry import Polygon
        from . import geometry
        d = rb.get("path")
        if shape == "polygon":
            if not d:
                return None
            if re.search(r"[A-Za-z]", d):
                pts = [(c[-2], c[-1]) for c in geometry.parse_svg_path(d) if c[0] != "Z"]
            else:
                nums = [float(v) for v in d.replace(",", " ").split()]
                pts = list(zip(nums[0::2], nums[1::2]))
            if len(pts) < 3:
                return None
            poly = Polygon(pts)
            if not poly.is_valid:
                from shapely.validation import make_valid
                poly = make_valid(poly)
            return [np.asarray(p) for p in convex_pieces(poly)] or None
        if d:
            paths = [geometry.parse_svg_path(d)]
        elif ln(el) == "shape":
            from .nodes.core import shape_paths
            paths = shape_paths(self.rc, el, self.rc.node_ctx(el, c0), w, h)
        else:
            return None
        from .modifiers import region
        reg = region(paths, (el.get("fillRule") or "nonzero"), 0.25)
        if reg is None or reg.is_empty:
            return None
        return [np.asarray(p) for p in convex_pieces(reg)] or None

    def _alpha_hull(self, el, t0, w, h):
        from .registry import NODES
        rc = self.rc
        fn = NODES.get(ln(el))
        if fn is None:
            return None
        c = rc.node_ctx(el, self._ctx(t0))
        prev, self.busy = self.busy, True
        try:
            buf = fn(rc, el, c, np.eye(3), (w, h))
        finally:
            self.busy = prev
        buf = getattr(buf, "buf", buf)
        if buf is None:
            return None
        a = buf.px[..., 3] >= 0.5
        rows = np.nonzero(a.any(1))[0]
        if len(rows) == 0:
            return None
        first = np.argmax(a[rows], 1)
        last = a.shape[1] - 1 - np.argmax(a[rows, ::-1], 1)
        pts = []
        for y, x0, x1 in zip(rows, first, last):
            for x in (x0, x1 + 1):
                pts += [(x, y), (x, y + 1)]
        P = np.array(pts, np.float64) + np.array([buf.x0, buf.y0])
        P = np.round(P * 16) / 16
        return convex_hull(P)

    # ------------------------------------------------------------ setup
    def _init(self) -> World:
        ev = self.rc.ev
        bodies = []
        t0 = self.start
        for el, rb in self.body_els:
            c0 = self._ctx(t0)
            kind = rb.get("type", "dynamic")
            centre, ang, (hx, hy) = self.keyed_pose(el, t0)
            shape, parts = self._parts(el, rb, t0, max(hx, 1e-3), max(hy, 1e-3))
            b = Body(el, kind, shape, parts, np.zeros(2), max(hx, 1e-3), max(hy, 1e-3))
            b.mass = max(1e-6, ev.num(rb, "mass", c0, 1.0))
            A, J, C = 0.0, 0.0, np.zeros(2)
            for p in parts:
                a_, c_, j_ = _part_mass(p)
                A, C, J = A + a_, C + a_ * c_, J + j_
            C = C / A if A > 1e-15 else np.zeros(2)
            Jc = J - A * float(C @ C)
            b.inertia = max(1e-12, b.mass * Jc / A) if A > 1e-15 else b.mass * (hx * hx + hy * hy) / 3
            for p in parts:
                p.loc = p.loc - C
            b.c_off = -C
            b.pos = centre + _rot(ang) @ C
            b.angle = ang
            b.bound = max(float(np.max(np.hypot(*p.loc.T))) + p.r for p in parts)
            b.friction = ev.num(rb, "friction", c0, 0.5)
            b.restitution = ev.num(rb, "restitution", c0, 0.0)
            b.lin_damp = ev.num(rb, "linearDamping", c0, 0.01)
            b.ang_damp = ev.num(rb, "angularDamping", c0, 0.01)
            b.activate_at = ev.num(rb, "activateAt", c0, 0.0)
            b.group = int(ev.num(rb, "collisionGroup", c0, 0.0))
            b.collides = _parse_groups(rb.get("collidesWith", "all"))
            b.sensor = parse_bool(rb.get("sensor"))
            b.bullet = parse_bool(rb.get("bullet"))
            b.fixed_rotation = parse_bool(rb.get("fixedRotation"))
            b.v0 = (ev.num(rb, "velocityX", c0, 0.0), ev.num(rb, "velocityY", c0, 0.0),
                    -math.radians(ev.num(rb, "angularVelocity", c0, 0.0)))
            bodies.append(b)
        joints = [j for c in (self.ph if self.ph is not None else []) if ln(c) == "constraint"
                  for j in [self._joint(c, bodies, t0)] if j is not None]
        softs = [self._soft(el, sb, bodies, t0) for el, sb in self.soft_els]
        w = World(bodies, joints, [s for s in softs if s is not None], t0)
        self._refresh_mass(w)
        return w

    def _joint(self, c, bodies, t0) -> Joint | None:
        ev = self.rc.ev
        by_id = {el.get("id"): b for b, (el, _) in zip(bodies, self.body_els) if el.get("id")}
        kind = c.get("type")
        a = by_id.get(c.get("a"))
        b = by_id.get(c.get("b")) if c.get("b") else None
        if a is None or (c.get("b") and b is None):
            warn_once("physicsConstraint", c.get("id", "?"), "body a/b not found (needs a rigidBody)")
            return None
        c0 = self._ctx(t0)
        P, Q = (a, b) if b is not None else (None, a)
        if c.get("x") is not None or c.get("y") is not None:
            wp = np.array([ev.num(c, "x", c0, 0.0) / self.ppm, -ev.num(c, "y", c0, 0.0) / self.ppm])
        else:
            wp = Q.box_centre().copy()
        if kind in ("distance", "rope", "spring"):
            if b is not None:
                lp, lq = a.c_off.copy(), b.c_off.copy()
            else:
                lp, lq = wp, a.c_off.copy()
        else:
            lp = (_rot(-P.angle) @ (wp - P.pos)) if P is not None else wp
            lq = _rot(-Q.angle) @ (wp - Q.pos)
        j = Joint(c, kind, P, Q, lp, lq)
        _, pp, _, pq = _anchors(j)
        rest = ev.num(c, "restLength", c0, -1.0) if c.get("restLength") is not None else -1.0
        j.rest = rest if rest >= 0 else float(np.hypot(*(pq - pp)))
        stiff = ev.num(c, "stiffness", c0, 0.0) if c.get("stiffness") is not None else 0.0
        j.stiffness = stiff if stiff > 0 else None
        j.damping = ev.num(c, "damping", c0, 0.0) if c.get("damping") is not None else 0.0
        j.break_force = ev.num(c, "breakForce", c0, 0.0) if c.get("breakForce") is not None else None
        j.ref_angle = Q.angle - _bang(P)
        if c.get("maxAngle") is not None:
            j.lower = -math.radians(ev.num(c, "maxAngle", c0, 0.0))
        if c.get("minAngle") is not None:
            j.upper = -math.radians(ev.num(c, "minAngle", c0, 0.0))
        ax = -math.radians(ev.num(c, "axisAngle", c0, 0.0))
        axw = np.array([math.cos(ax), math.sin(ax)])
        j.axis = (_rot(-P.angle) @ axw) if P is not None else axw
        if kind == "slider" and c.get("restLength") is not None:
            j.travel = abs(rest)
            j.rest = 0.0
        j.motor = kind == "motor" or (kind == "slider" and c.get("motorSpeed") is not None)
        ms = ev.num(c, "motorSpeed", c0, 0.0)
        j.motor_speed = ms if kind == "slider" else -math.radians(ms)
        j.max_force = ev.num(c, "maxForce", c0, 0.0) if c.get("maxForce") is not None else None
        if kind == "pin" and b is not None:
            warn_once("physicsConstraint", f"pin-b:{c.get('id')}", "pin ignores b (schema: pin ties a to x, y)")
        return j

    def _soft(self, el, sb, bodies, t0) -> Soft | None:
        ev = self.rc.ev
        c0 = self._ctx(t0)
        kind = sb.get("kind", "jelly")
        rows = max(2, int(ev.num(sb, "rows", c0, 4)))
        cols = max(2, int(ev.num(sb, "cols", c0, 4)))
        w, h = self._size(el, t0)
        if w <= 0 or h <= 0:
            warn_once("softBody", el.get("id", "?"), "node has no box; not simulated")
            return None
        mass = max(1e-6, ev.num(sb, "mass", c0, 1.0))
        k = max(1e-6, ev.num(sb, "stiffness", c0, 20.0))
        zeta = ev.num(sb, "damping", c0, 0.1)
        pin = sb.get("pin", "none")
        thick = 0.0
        if kind == "rope":
            n = max(rows, cols)
            if w >= h:
                rest = np.stack([np.linspace(0, w, n), np.full(n, h / 2)], 1)
                thick = h / 2
                ends = {"left": [0], "right": [n - 1], "top": [0], "bottom": [n - 1]}
            else:
                rest = np.stack([np.full(n, w / 2), np.linspace(0, h, n)], 1)
                thick = w / 2
                ends = {"top": [0], "bottom": [n - 1], "left": [0], "right": [n - 1]}
            ends["corners"] = [0, n - 1]
            springs = [(i, i + 1, 1.0, True) for i in range(n - 1)] + [(i, i + 2, 0.1, False) for i in range(n - 2)]
            pinned = np.zeros(n, bool)
            pinned[ends.get(pin, [])] = True
            ring = np.zeros(0, np.int64)
            rows_, cols_ = (1, n) if w >= h else (n, 1)
            neigh = 1
        else:
            gx, gy = np.meshgrid(np.linspace(0, w, cols), np.linspace(0, h, rows))
            rest = np.stack([gx.ravel(), gy.ravel()], 1)
            idx = lambda r, c: r * cols + c  # noqa: E731
            springs = []
            cloth = kind == "cloth"
            kb, ksh = (0.02, 0.15) if cloth else (0.25, 1.0)
            for r in range(rows):
                for c in range(cols):
                    if c + 1 < cols:
                        springs.append((idx(r, c), idx(r, c + 1), 1.0, False))
                    if r + 1 < rows:
                        springs.append((idx(r, c), idx(r + 1, c), 1.0, False))
                    if r + 1 < rows and c + 1 < cols:
                        springs += [(idx(r, c), idx(r + 1, c + 1), ksh, False), (idx(r, c + 1), idx(r + 1, c), ksh, False)]
                    if c + 2 < cols:
                        springs.append((idx(r, c), idx(r, c + 2), kb, False))
                    if r + 2 < rows:
                        springs.append((idx(r, c), idx(r + 2, c), kb, False))
            n = rows * cols
            pinned = np.zeros((rows, cols), bool)
            if pin == "top":
                pinned[0, :] = True
            elif pin == "bottom":
                pinned[-1, :] = True
            elif pin == "left":
                pinned[:, 0] = True
            elif pin == "right":
                pinned[:, -1] = True
            elif pin == "corners":
                pinned[0, 0] = pinned[0, -1] = pinned[-1, 0] = pinned[-1, -1] = True
            pinned = pinned.ravel()
            ring = np.array([idx(0, c) for c in range(cols)] + [idx(r, cols - 1) for r in range(1, rows)]
                            + [idx(rows - 1, c) for c in range(cols - 2, -1, -1)]
                            + [idx(r, 0) for r in range(rows - 2, 0, -1)], np.int64)
            rows_, cols_ = rows, cols
            neigh = 1
        pos = self._grid_world(el, t0, rest)
        S = np.array([(a, b) for a, b, _, _ in springs], np.int64).reshape(-1, 2)
        ks = np.array([s for _, _, s, _ in springs], np.float64) * k
        tens = np.array([t for *_, t in springs], bool)
        L0 = np.hypot(*(pos[S[:, 1]] - pos[S[:, 0]]).T) if len(S) else np.zeros(0)
        area0 = _signed_area(pos[ring]) if len(ring) >= 3 else 0.0
        rigid = bodies[self.index[el]] if el in self.index else None
        anchor = None
        if rigid is not None:
            anchor = (pos - rigid.pos) @ _rot(-rigid.angle).T
        sub = soft_substeps(mass, n, k, zeta, self.dt, rigid is not None)
        if sub > MAX_SUBSTEPS:
            warn_once("softBody", el.get("id", "?"), f"needs {sub:.0f} substeps per fixedStep (> {MAX_SUBSTEPS}); "
                      "clamped (lower stiffness, raise mass or reduce fixedStep)")
        spacing = float(np.min(L0[ks >= k * 0.99])) if len(L0) else 0.01
        s = Soft(el, kind, rows_, cols_, rest, pos, np.zeros_like(pos), mass / n, k, zeta,
                 ev.num(sb, "pressure", c0, 0.0), S, L0, ks, tens, pinned, ring, area0,
                 parse_bool(sb.get("selfCollision")), rigid, anchor, int(min(sub, MAX_SUBSTEPS)),
                 0.01, max(spacing, 1e-4), thick)
        if s.self_coll:
            if kind == "rope":
                ii = np.arange(n)
                s.neighbours = np.abs(ii[:, None] - ii[None, :]) <= 2
            else:
                r_, c_ = np.divmod(np.arange(n), cols)
                s.neighbours = (np.abs(r_[:, None] - r_[None, :]) <= neigh) & (np.abs(c_[:, None] - c_[None, :]) <= neigh)
        return s

    def _grid_world(self, el, t, rest_px) -> np.ndarray:
        W = self.world_doc(el, t)
        q = rest_px @ W[:2, :2].T + W[:2, 2]
        return np.stack([q[:, 0], -q[:, 1]], 1) / self.ppm

    def _refresh_mass(self, w: World) -> None:
        for b in w.bodies:
            if b.active:
                b.inv_m = 1.0 / b.mass
                b.inv_i = 0.0 if b.fixed_rotation else 1.0 / b.inertia
            else:
                b.inv_m = b.inv_i = 0.0

    # ------------------------------------------------------------ step
    def _accel_bodies(self, pos_m: np.ndarray, vel_m: np.ndarray, t: float) -> np.ndarray:
        """Gravity plus force fields (m/s^2, y up) at world points."""
        acc = np.tile(np.array([self.gx, self.gy], np.float64), (len(pos_m), 1))
        if self.fields and len(pos_m):
            X, Y = pos_m[:, 0] * self.ppm, -pos_m[:, 1] * self.ppm
            for f in self.fields:
                ax, ay = field_accel(self.rc, f, t, X, Y, vel_m[:, 0], -vel_m[:, 1], self.ppm)
                acc[:, 0] += ax
                acc[:, 1] -= ay
        return acc

    def _step(self, w: World, i: int) -> None:
        dt = self.dt
        t = self.start + i * dt
        t1 = t + dt
        changed = False
        for b in w.bodies:
            if b.kind == "dynamic" and not b.active and t1 >= b.activate_at - 1e-9:
                self._set_keyed(b, max(t, b.activate_at))
                b.vel = np.array(b.v0[:2], np.float64)
                b.w = b.v0[2]
                b.active = True
                changed = True
            if not b.active and b.kind != "static":
                p0, a0 = b.pos.copy(), b.angle
                self._set_keyed(b, t1)
                b.vel = (b.pos - p0) / dt
                b.w = (b.angle - a0) / dt
        if changed:
            self._refresh_mass(w)
        act = [b for b in w.bodies if b.active]
        if act:
            acc = self._accel_bodies(np.array([b.pos for b in act]), np.array([b.vel for b in act]), t)
            for b, a in zip(act, acc):
                b.vel = (b.vel + a * dt) / (1.0 + dt * b.lin_damp)
                b.w = b.w / (1.0 + dt * b.ang_damp)
        live = [j for j in w.joints if not j.broken]
        for j in live:
            j.acc_lin = np.zeros(2)
            j.acc_ang = j.acc_lo = j.acc_hi = j.acc_mot = j.acc_ax = 0.0
            if self._soft_joint(j):
                self._spring(j, dt)
            elif j.damping > 0 and j.kind in ("hinge", "motor", "slider"):
                self._joint_damping(j, dt)
        contacts: list[Contact] = []
        hits = []
        bs = w.bodies
        for ia in range(len(bs)):
            A = bs[ia]
            for ib in range(ia + 1, len(bs)):
                B = bs[ib]
                if not (A.active or B.active) and not (A.sensor or B.sensor):
                    continue
                if not self._filter(A, B):
                    continue
                if A.sensor or B.sensor:
                    if any(c.depth > 0 for c in _collide(A, B)):
                        hits.append((A.el.get("id"), B.el.get("id")))
                    continue
                contacts += _collide(A, B)
        for b in bs:
            if b.active and not b.sensor:
                for n, off in self.planes:
                    contacts += _collide_plane(b, n, off)
        w.sensor_hits = tuple(hits)
        for b in bs:
            b.vel = np.array(b.vel, np.float64)
        self._prepare(contacts, dt)
        rigid_j = [j for j in live if not self._soft_joint(j)]
        for _ in range(max(1, self.iters)):
            for j in rigid_j:
                self._solve_joint(j, dt)
            for c in contacts:
                self._solve_contact(c)
        for j in live:
            if j.break_force is not None and not self._soft_joint(j):
                if float(np.hypot(*j.acc_lin)) / dt > j.break_force or abs(j.acc_ang) / dt > j.break_force:
                    j.broken = True
        self._ccd(w, dt)
        for b in bs:
            if b.active:
                b.pos = b.pos + b.vel * dt
                b.angle += b.w * dt
        for s in w.softs:
            self._soft_step(w, s, t, dt)
        w.t = t1

    def _set_keyed(self, b: Body, t: float) -> None:
        centre, ang, _ = self.keyed_pose(b.el, t)
        b.angle = ang
        b.pos = centre - _rot(ang) @ b.c_off

    def _filter(self, A: Body, B: Body) -> bool:
        ok = lambda X, Y: X.collides == "all" or Y.group in X.collides  # noqa: E731
        return ok(A, B) and ok(B, A)

    def _prepare(self, contacts, dt):
        for c in contacts:
            A, B = c.a, c.b
            ra = c.p - A.pos
            rb = c.p - B.pos if B is not None else np.zeros(2)
            ima, iia = A.inv_m, A.inv_i
            imb, iib = (B.inv_m, B.inv_i) if B is not None else (0.0, 0.0)
            rna, rnb = _cross(ra, c.n), _cross(rb, c.n)
            k = ima + imb + iia * rna * rna + iib * rnb * rnb
            c.mn = 1.0 / k if k > 0 else 0.0
            tng = np.array([c.n[1], -c.n[0]])
            rta, rtb = _cross(ra, tng), _cross(rb, tng)
            kt = ima + imb + iia * rta * rta + iib * rtb * rtb
            c.mt = 1.0 / kt if kt > 0 else 0.0
            dv = _vel_at(B, rb) - _vel_at(A, ra)
            vn = float(dv @ c.n)
            e = max(A.restitution, B.restitution if B is not None else 0.0)
            c.bias = BETA / dt * max(0.0, c.depth - SLOP) if c.depth >= 0 else c.depth / dt
            if vn < -1.0 and c.depth > -SLOP:
                c.bias = max(c.bias, -e * vn)
            fb = B.friction if B is not None else 0.6
            c.friction = math.sqrt(max(0.0, A.friction * fb))
            c.ra = (float(ra[0]), float(ra[1]))
            c.rb = (float(rb[0]), float(rb[1]))

    def _solve_contact(self, c: Contact) -> None:
        # scalar sequential-impulse update (hot loop: avoid small numpy temporaries)
        A, B = c.a, c.b
        nx, ny = float(c.n[0]), float(c.n[1])
        rax, ray = c.ra
        avx, avy, aw = float(A.vel[0]), float(A.vel[1]), A.w
        if B is not None:
            rbx, rby = c.rb
            bvx, bvy, bw = float(B.vel[0]), float(B.vel[1]), B.w
            imb, iib = B.inv_m, B.inv_i
        else:
            rbx = rby = bvx = bvy = bw = imb = iib = 0.0
        ima, iia = A.inv_m, A.inv_i

        def dv():
            return (bvx - bw * rby - avx + aw * ray, bvy + bw * rbx - avy - aw * rax)
        dx, dy = dv()
        vn = dx * nx + dy * ny
        pn0 = c.pn
        c.pn = max(pn0 + c.mn * (-vn + c.bias), 0.0)
        d = c.pn - pn0
        px, py = nx * d, ny * d
        avx, avy, aw = avx - px * ima, avy - py * ima, aw - iia * (rax * py - ray * px)
        bvx, bvy, bw = bvx + px * imb, bvy + py * imb, bw + iib * (rbx * py - rby * px)
        tx, ty = ny, -nx
        dx, dy = dv()
        vt = dx * tx + dy * ty
        mx = c.friction * c.pn
        pt0 = c.pt
        c.pt = min(max(pt0 - c.mt * vt, -mx), mx)
        d = c.pt - pt0
        px, py = tx * d, ty * d
        avx, avy, aw = avx - px * ima, avy - py * ima, aw - iia * (rax * py - ray * px)
        bvx, bvy, bw = bvx + px * imb, bvy + py * imb, bw + iib * (rbx * py - rby * px)
        A.vel[0], A.vel[1], A.w = avx, avy, aw
        if B is not None:
            B.vel[0], B.vel[1], B.w = bvx, bvy, bw

    # ------------------------------------------------------------ joints
    @staticmethod
    def _soft_joint(j: Joint) -> bool:
        return j.kind == "spring" or (j.stiffness is not None and j.kind in ("pin", "hinge", "weld", "distance"))

    def _spring(self, j: Joint, dt: float) -> None:
        P, Q = j.P, j.Q
        rp, pp, rq, pq = _anchors(j)
        d = pq - pp
        L = float(np.hypot(*d))
        k = j.stiffness if j.stiffness is not None else 50.0
        rest = j.rest if j.kind in ("spring", "distance") else 0.0
        mP = P.mass if P is not None and P.active else 0.0
        mQ = Q.mass if Q.active else 0.0
        meff = (mP * mQ / (mP + mQ)) if (mP > 0 and mQ > 0) else (mP or mQ or 1.0)
        cdamp = j.damping * 2.0 * math.sqrt(k * meff)
        F = np.zeros(2)
        if L > 1e-9:
            n = d / L
            rel = float((_vel_at(Q, rq) - _vel_at(P, rp)) @ n)
            f = k * (L - rest) + cdamp * rel
            F = n * f
        elif rest == 0.0:
            F = cdamp * (_vel_at(Q, rq) - _vel_at(P, rp))
        if j.break_force is not None and float(np.hypot(*F)) > j.break_force:
            j.broken = True
            return
        _apply_impulse(P, F * dt, rp)
        _apply_impulse(Q, -F * dt, rq)
        if j.kind == "weld":
            rg2 = Q.inertia / Q.mass
            ka = k * rg2
            ca = j.damping * 2.0 * math.sqrt(ka * Q.inertia)
            C = Q.angle - _bang(P) - j.ref_angle
            tau = -(ka * C + ca * (Q.w - _bw(P)))
            _apply_ang(P, Q, tau * dt)

    def _joint_damping(self, j: Joint, dt: float) -> None:
        P, Q = j.P, j.Q
        if j.kind == "slider":
            rp, pp, rq, pq = _anchors(j)
            u = (_rot(P.angle) @ j.axis) if P is not None else j.axis
            vrel = float((_vel_at(Q, rq) - _vel_at(P, pp - _bpos(P) if P is not None else np.zeros(2))) @ u)
            k = _bim(P)[0] + Q.inv_m
            if k > 0:
                imp = -min(j.damping * dt, 1.0 / k) * vrel
                _apply_impulse(P, -u * imp, pp - _bpos(P) if P is not None else np.zeros(2))
                _apply_impulse(Q, u * imp, rq)
            return
        k = _bim(P)[1] + Q.inv_i
        if k > 0:
            _apply_ang(P, Q, -min(j.damping * dt, 1.0 / k) * (Q.w - _bw(P)))

    def _solve_point(self, j, rp, pp, rq, pq, dt):
        P, Q = j.P, j.Q
        imp, iip = _bim(P)
        K = np.array([[imp + Q.inv_m + iip * rp[1] ** 2 + Q.inv_i * rq[1] ** 2,
                       -iip * rp[0] * rp[1] - Q.inv_i * rq[0] * rq[1]],
                      [-iip * rp[0] * rp[1] - Q.inv_i * rq[0] * rq[1],
                       imp + Q.inv_m + iip * rp[0] ** 2 + Q.inv_i * rq[0] ** 2]])
        dv = _vel_at(Q, rq) - _vel_at(P, rp)
        try:
            Pimp = -np.linalg.solve(K, dv + (pq - pp) * (BETA / dt))
        except np.linalg.LinAlgError:
            return
        _apply_impulse(P, -Pimp, rp)
        _apply_impulse(Q, Pimp, rq)
        j.acc_lin = j.acc_lin + Pimp

    def _solve_angle_limits(self, j, dt):
        P, Q = j.P, j.Q
        k = _bim(P)[1] + Q.inv_i
        if k <= 0:
            return
        m = 1.0 / k
        rel = Q.angle - _bang(P) - j.ref_angle
        if j.motor:
            cdot = Q.w - _bw(P) - j.motor_speed
            imp = -m * cdot
            old = j.acc_mot
            lim = j.max_force * dt if j.max_force is not None else math.inf
            j.acc_mot = min(max(old + imp, -lim), lim)
            _apply_ang(P, Q, j.acc_mot - old)
            j.acc_ang += j.acc_mot - old
        if j.lower is not None:
            C = rel - j.lower
            bias = C / dt if C > 0 else BETA * C / dt
            imp = -m * (Q.w - _bw(P) + bias)
            old = j.acc_lo
            j.acc_lo = max(old + imp, 0.0)
            _apply_ang(P, Q, j.acc_lo - old)
        if j.upper is not None:
            C = j.upper - rel
            bias = C / dt if C > 0 else BETA * C / dt
            imp = -m * (_bw(P) - Q.w + bias)
            old = j.acc_hi
            j.acc_hi = max(old + imp, 0.0)
            _apply_ang(P, Q, -(j.acc_hi - old))

    def _solve_axis(self, j, u, rp, rq, d, C, dt, lo=None, hi=None, target_v=None, attr=None, lim=None):
        """Scalar linear constraint along world axis u between P's anchor (+ d) and Q's anchor."""
        P, Q = j.P, j.Q
        imp, iip = _bim(P)
        rpd = rp + d
        a, b = _cross(rpd, u), _cross(rq, u)
        k = imp + Q.inv_m + iip * a * a + Q.inv_i * b * b
        if k <= 0:
            return
        vrel = float((_vel_at(Q, rq) - _vel_at(P, rp)) @ u) - (_bw(P) * float(_cross_sv(1.0, d) @ u) if P is not None else 0.0)
        if target_v is not None:
            lam = -(vrel - target_v) / k
        else:
            bias = (C / dt if C > 0 else BETA * C / dt) if (lo is not None or hi is not None) else BETA * C / dt
            lam = -(vrel + bias) / k
        if attr is not None:
            old = getattr(j, attr)
            new = old + lam
            if lo is not None:
                new = max(new, 0.0)
            if hi is not None:
                new = min(new, 0.0)
            if lim is not None:
                new = min(max(new, -lim), lim)
            setattr(j, attr, new)
            lam = new - old
        Pv = u * lam
        if P is not None:
            P.vel = P.vel - Pv * P.inv_m
            P.w -= P.inv_i * _cross(rpd, Pv)
        Q.vel = Q.vel + Pv * Q.inv_m
        Q.w += Q.inv_i * _cross(rq, Pv)
        if target_v is None and attr is None:
            j.acc_lin = j.acc_lin + Pv

    def _solve_joint(self, j: Joint, dt: float) -> None:
        P, Q = j.P, j.Q
        imp, iip = _bim(P)
        if imp + Q.inv_m == 0 and iip + Q.inv_i == 0:
            return
        rp, pp, rq, pq = _anchors(j)
        kind = j.kind
        if kind in ("pin", "hinge", "motor", "weld"):
            if kind in ("hinge", "motor"):
                self._solve_angle_limits(j, dt)
            if kind == "weld":
                k = iip + Q.inv_i
                if k > 0:
                    C = Q.angle - _bang(P) - j.ref_angle
                    L = -(Q.w - _bw(P) + BETA * C / dt) / k
                    _apply_ang(P, Q, L)
                    j.acc_ang += L
            self._solve_point(j, rp, pp, rq, pq, dt)
            return
        if kind == "slider":
            u = (_rot(P.angle) @ j.axis) if P is not None else j.axis
            perp = np.array([-u[1], u[0]])
            d = pq - pp
            k = iip + Q.inv_i
            if k > 0:
                C = Q.angle - _bang(P) - j.ref_angle
                L = -(Q.w - _bw(P) + BETA * C / dt) / k
                _apply_ang(P, Q, L)
                j.acc_ang += L
            self._solve_axis(j, perp, rp, rq, d, float(d @ perp), dt)
            s = float(d @ u)
            if j.motor:
                lim = j.max_force * dt if j.max_force is not None else math.inf
                self._solve_axis(j, u, rp, rq, d, 0.0, dt, target_v=j.motor_speed, attr="acc_mot", lim=lim)
            if j.travel is not None:
                self._solve_axis(j, u, rp, rq, d, s + j.travel, dt, lo=True, attr="acc_lo")
                self._solve_axis(j, -u, rp, rq, -d, j.travel - s, dt, lo=True, attr="acc_hi")
            return
        # distance / rope: 1D along the axis
        d = pq - pp
        Ld = float(np.hypot(*d))
        if Ld < 1e-9:
            return
        n = d / Ld
        C = Ld - j.rest
        if kind == "rope" and C <= 0:
            return
        rna, rnb = _cross(rp, n), _cross(rq, n)
        k = imp + Q.inv_m + iip * rna * rna + Q.inv_i * rnb * rnb
        if k <= 0:
            return
        vrel = float((_vel_at(Q, rq) - _vel_at(P, rp)) @ n)
        lam = -(vrel + BETA / dt * C) / k
        if kind == "rope":
            lam = min(lam, 0.0)
        Pv = n * lam
        _apply_impulse(P, -Pv, rp)
        _apply_impulse(Q, Pv, rq)
        j.acc_lin = j.acc_lin + Pv

    # ------------------------------------------------------------ continuous collision
    def _ccd(self, w: World, dt: float) -> None:
        bullets = [b for b in w.bodies if b.bullet and b.active and not b.sensor]
        for B in bullets:
            speed = float(np.hypot(*B.vel)) + abs(B.w) * B.bound
            if speed * dt < 1e-6:
                continue
            p0, a0 = B.pos.copy(), B.angle
            toi = dt
            for O in w.bodies:
                if O is B or O.sensor or not self._filter(B, O):
                    continue
                if float(np.hypot(*(O.pos - B.pos))) > B.bound + O.bound + (speed + float(np.hypot(*O.vel)) + abs(O.w) * O.bound) * dt:
                    continue
                toi = min(toi, self._toi(B, O, dt, toi))
            for n, off in self.planes:
                toi = min(toi, self._toi_plane(B, n, off, dt, toi))
            B.pos, B.angle = p0, a0
            if toi < dt:
                # advance to the time of impact now; the contact (speculative next step) takes over
                B.pos = p0 + B.vel * toi
                B.angle = a0 + B.w * toi
                B.pos = B.pos - B.vel * dt
                B.angle -= B.w * dt

    def _toi(self, B: Body, O: Body, dt: float, limit: float) -> float:
        p0, a0 = B.pos.copy(), B.angle
        q0, b0 = O.pos.copy(), O.angle
        ovel = O.vel if O.active or O.kind == "kinematic" else np.zeros(2)
        ow = O.w if O.active or O.kind == "kinematic" else 0.0
        t = 0.0
        target = SLOP
        hit = limit
        try:
            d0 = body_distance(B, O)
            if d0 <= target:
                return limit
            vb = float(np.hypot(*(B.vel - ovel))) + abs(B.w) * B.bound + abs(ow) * O.bound
            if vb < 1e-12:
                return limit
            for _ in range(40):
                B.pos, B.angle = p0 + B.vel * t, a0 + B.w * t
                O.pos, O.angle = q0 + ovel * t, b0 + ow * t
                d = body_distance(B, O)
                if d <= target:
                    hit = t
                    break
                t += (d - 0.5 * target) / vb
                if t >= limit:
                    break
        finally:
            B.pos, B.angle, O.pos, O.angle = p0, a0, q0, b0
        return hit

    def _toi_plane(self, B: Body, n, off, dt, limit):
        p0, a0 = B.pos.copy(), B.angle
        vb = max(0.0, -float(B.vel @ n)) + abs(B.w) * B.bound
        if vb < 1e-12:
            return limit

        def dist():
            return min(float(np.min(v @ n)) - off - r for v, _, r in B.world_parts())
        t, hit = 0.0, limit
        try:
            if dist() <= SLOP:
                return limit
            for _ in range(40):
                B.pos, B.angle = p0 + B.vel * t, a0 + B.w * t
                d = dist()
                if d <= SLOP:
                    hit = t
                    break
                t += (d - 0.5 * SLOP) / vb
                if t >= limit:
                    break
        finally:
            B.pos, B.angle = p0, a0
        return hit

    # ------------------------------------------------------------ soft bodies
    def _soft_step(self, w: World, s: Soft, t: float, dt: float) -> None:
        ns = s.substeps
        h = dt / ns
        m, k = s.m, s.k
        c = 2.0 * s.zeta * math.sqrt(k * m)
        cs = 2.0 * s.zeta * np.sqrt(s.kspring * m)
        pin_ix = np.nonzero(s.pinned)[0]
        if len(pin_ix):
            T0 = self._grid_world(s.el, t, s.rest[pin_ix])
            T1 = self._grid_world(s.el, t + dt, s.rest[pin_ix])
        ext = self._accel_bodies(s.pos, s.vel, t)
        decay = math.exp(-0.01 * h)
        A, B_ = (s.springs[:, 0], s.springs[:, 1]) if len(s.springs) else (None, None)
        colliders = [b for b in w.bodies if not b.sensor and b is not s.rigid]
        for sub in range(ns):
            F = ext * m
            if A is not None:
                d = s.pos[B_] - s.pos[A]
                L = np.hypot(d[:, 0], d[:, 1])
                Ls = np.where(L > 1e-12, L, 1.0)
                n = d / Ls[:, None]
                rel = np.einsum("ij,ij->i", s.vel[B_] - s.vel[A], n)
                f = (s.kspring * (L - s.rest_len) + cs * rel) * (L > 1e-12)
                f = np.where(s.tension_only & (L < s.rest_len), 0.0, f)
                fv = n * f[:, None]
                np.add.at(F, A, fv)
                np.add.at(F, B_, -fv)
            if s.pressure != 0.0 and len(s.ring) >= 3 and abs(s.area0) > 1e-12:
                ring = s.pos[s.ring]
                area = _signed_area(ring)
                ratio = s.area0 / area if area * s.area0 > 1e-18 else 4.0
                p = s.pressure * (min(max(ratio, 0.25), 4.0) - 1.0)
                e = np.roll(ring, -1, 0) - ring
                sign = 1.0 if s.area0 > 0 else -1.0
                o = np.stack([e[:, 1], -e[:, 0]], 1) * (sign * p * 0.5)
                np.add.at(F, s.ring, o)
                np.add.at(F, np.roll(s.ring, -1), o)
            if s.rigid is not None:
                R = _rot(s.rigid.angle)
                tgt = s.anchor_loc @ R.T + s.rigid.pos
                rv = s.rigid.vel + np.stack([-s.rigid.w * (tgt - s.rigid.pos)[:, 1], s.rigid.w * (tgt - s.rigid.pos)[:, 0]], 1)
                F += k * (tgt - s.pos) + c * (rv - s.vel)
            s.vel = (s.vel + F / m * h) * decay
            s.pos = s.pos + s.vel * h
            if len(pin_ix):
                u = (sub + 1) / ns
                s.pos[pin_ix] = T0 + (T1 - T0) * u
                s.vel[pin_ix] = (T1 - T0) / dt
            self._soft_collide(s, colliders, pin_ix, h)
        if s.self_coll:
            self._soft_self(s)

    def _soft_collide(self, s: Soft, colliders, pin_ix, h) -> None:
        free = np.ones(len(s.pos), bool)
        free[pin_ix] = False
        e, mu, r = 0.2, 0.5, s.skin
        for n, off in self.planes:
            d = s.pos @ n - off - r
            hit = (d < 0) & free
            if hit.any():
                s.pos[hit] -= np.outer(d[hit], n)
                self._respond(s, hit, np.tile(n, (int(hit.sum()), 1)), None, e, mu)
        for b in colliders:
            near = np.hypot(*(s.pos - b.pos).T) < b.bound + r + s.spacing
            near &= free
            if not near.any():
                continue
            idx = np.nonzero(near)[0]
            for v, nr, pr in b.world_parts():
                P = s.pos[idx]
                sd, nrm = _points_vs_part(P, v, nr, pr)
                hit = sd < r
                if not hit.any():
                    continue
                hi = idx[hit]
                s.pos[hi] += nrm[hit] * (r - sd[hit])[:, None]
                mask = np.zeros(len(s.pos), bool)
                mask[hi] = True
                self._respond(s, mask, nrm[hit], b, e, mu)

    def _respond(self, s: Soft, mask, nrm, body, e, mu):
        idx = np.nonzero(mask)[0]
        v = s.vel[idx]
        if body is not None:
            r = s.pos[idx] - body.pos
            bv = body.vel + np.stack([-body.w * r[:, 1], body.w * r[:, 0]], 1)
        else:
            bv = np.zeros_like(v)
        rel = v - bv
        vn = np.einsum("ij,ij->i", rel, nrm)
        app = vn < 0
        dvn = np.where(app, -(1 + e) * vn, 0.0)
        vt = rel - nrm * vn[:, None]
        vtl = np.hypot(vt[:, 0], vt[:, 1])
        fr = np.where(vtl > 1e-12, np.minimum(1.0, mu * dvn / np.where(vtl > 1e-12, vtl, 1.0)), 0.0)
        dv = nrm * dvn[:, None] - vt * fr[:, None]
        s.vel[idx] = v + dv
        if body is not None and body.active and body.inv_m > 0:
            J = -dv * s.m
            for Jk, rk in zip(J, s.pos[idx] - body.pos):
                _apply_impulse(body, Jk, rk)

    def _soft_self(self, s: Soft) -> None:
        rad = 0.5 * s.spacing
        d = s.pos[:, None, :] - s.pos[None, :, :]
        L = np.hypot(d[..., 0], d[..., 1])
        close = (L < rad) & ~s.neighbours
        close &= np.triu(np.ones_like(close), 1).astype(bool)
        if not close.any():
            return
        ii, jj = np.nonzero(close)
        dd = d[ii, jj]
        Ls = np.where(L[ii, jj] > 1e-12, L[ii, jj], 1.0)
        n = np.where(L[ii, jj][:, None] > 1e-12, dd / Ls[:, None], np.array([0.0, 1.0]))
        push = (rad - L[ii, jj]) / 2
        np.add.at(s.pos, ii, n * push[:, None])
        np.add.at(s.pos, jj, -n * push[:, None])
        vn = np.einsum("ij,ij->i", s.vel[ii] - s.vel[jj], n)
        app = np.minimum(vn, 0.0) / 2
        np.add.at(s.vel, ii, -n * app[:, None])
        np.add.at(s.vel, jj, n * app[:, None])

    # ------------------------------------------------------------ cache
    def fingerprint(self) -> str:
        from lxml import etree
        h = hashlib.sha256(f"scenerender-physics-{CACHE_VERSION}".encode())
        if self.ph is not None:
            ph = copy.deepcopy(self.ph)
            for k in ("cache", "cacheSha256"):
                ph.attrib.pop(k, None)
            h.update(etree.tostring(ph, method="c14n"))
        for el, _ in self.body_els + self.soft_els:
            h.update(etree.tostring(el, method="c14n"))
        h.update(repr((self.doc.width, self.doc.height, self.doc.duration)).encode())
        return h.hexdigest()

    def _setup_cache(self, ph) -> None:
        path = self.doc.resolve_path(ph.get("cache"))
        sha = ph.get("cacheSha256")
        fp = self.fingerprint()
        if os.path.exists(path):
            try:
                raw = open(path, "rb").read()
                if sha and hashlib.sha256(raw).hexdigest().lower() != sha.lower():
                    log.error("physics cache %s does not match cacheSha256; simulating instead", path)
                    return
                import io
                z = np.load(io.BytesIO(raw), allow_pickle=False)
                meta = json.loads(str(z["meta"]))
                if meta.get("fingerprint") != fp or abs(meta.get("fixedStep", 0) - self.dt) > 1e-12:
                    warn_once("physics", "cache", f"{path} was recorded for a different document; simulating instead")
                    return
                self.cached = (z["rigid"], z["soft"])
            except Exception as e:  # noqa: BLE001 - a broken cache must never stop a render
                warn_once("physics", "cache", f"cannot read {path}: {e}; simulating instead")
            return
        n = max(0, int(math.ceil((float(self.doc.duration) - self.start) / self.dt)) + 1)
        rig = np.zeros((n + 1, len(self.body_els), 4))
        soft_counts = [len(s.pos) for s in self.sim.at(0).softs]
        sof = np.zeros((n + 1, sum(soft_counts), 2))
        for i in range(n + 1):
            w = self.sim.at(i)
            for k, b in enumerate(w.bodies):
                rig[i, k] = (*b.pos, b.angle, 1.0 if b.active else 0.0)
            if w.softs:
                sof[i] = np.concatenate([s.pos for s in w.softs])
        meta = json.dumps({"version": CACHE_VERSION, "fingerprint": fp, "fixedStep": self.dt, "start": self.start,
                           "bodies": [el.get("id") for el, _ in self.body_els],
                           "soft": [[el.get("id"), c] for (el, _), c in zip(self.soft_els, soft_counts)]})
        tmp = f"{path}.tmp{os.getpid()}"
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(tmp, "wb") as fh:
                np.savez_compressed(fh, meta=np.array(meta), rigid=rig, soft=sof)
            os.replace(tmp, path)
            if sha and hashlib.sha256(open(path, "rb").read()).hexdigest().lower() != sha.lower():
                warn_once("physics", "cacheSha256", f"wrote {path}; its sha256 differs from cacheSha256")
        except OSError as e:
            warn_once("physics", "cache", f"cannot write {path}: {e}")
        self.cached = (rig, sof)

    def _from_cache(self, n: int) -> World:
        rig, sof = self.cached
        n = min(n, len(rig) - 1)
        w = self.sim.at(0) if self.sim.last_n == 0 else self.sim.checkpoints[0]
        for k, b in enumerate(w.bodies):
            x, y, a, act = rig[n, k]
            b.pos, b.angle, b.active = np.array([x, y]), float(a), bool(act)
        o = 0
        for s in w.softs:
            s.pos = sof[n, o:o + len(s.pos)].copy()
            o += len(s.pos)
        return w

    # ------------------------------------------------------------ query
    def world_at(self, t: float) -> World | None:
        if t < self.start:
            return None
        n = int(math.floor((t - self.start) / self.dt + 1e-6))
        if self.cached is not None:
            return self._from_cache(n)
        return self.sim.at(n)


def get_sim(rc) -> PhysicsSim | None:
    key = ("physics-sim",)
    if key not in rc.cache:
        rc.cache[key] = None            # re-entrant calls while building see "no simulation"
        rc.cache[key] = PhysicsSim(rc) if rc.doc.section("physics") is not None or _has_bodies(rc) else None
    return rc.cache[key]


def _has_bodies(rc) -> bool:
    comp = rc.doc.section("composition")
    return comp is not None and any(ln(e) in ("rigidBody", "softBody") for e in comp.iter() if isinstance(e.tag, str))


def body_state(rc, el, t: float):
    """Simulated body (metres) or None."""
    sim = get_sim(rc)
    if sim is None or sim.busy or el not in sim.index:
        return None
    w = sim.world_at(t)
    if w is None:
        return None
    b = w.bodies[sim.index[el]]
    return b if (b.kind == "dynamic" and b.active) else None


def sensor_overlaps(rc, t: float) -> tuple:
    """Pairs of node ids (sensor, other) overlapping during the fixed step at time t."""
    sim = get_sim(rc)
    w = sim.world_at(t) if sim is not None else None
    return w.sensor_hits if w is not None else ()


def soft_world(rc, el, t: float):
    """Simulated soft body of node el at composition time t: (Soft, world points in document px), or None."""
    sim = get_sim(rc)
    if sim is None or sim.busy or el not in sim.soft_index:
        return None
    w = sim.world_at(t)
    if w is None:
        return None
    s = next((s for s in w.softs if s.el is el), None)
    if s is None:
        return None
    return s, np.stack([s.pos[:, 0] * sim.ppm, -s.pos[:, 1] * sim.ppm], 1)


def body_transform(rc, el, t: float, ctx=None):
    """(x, y, rotation) for el in its parent's space (document px, degrees) from the simulation at
    composition time t, or None when the node should keep its keyframed transform."""
    sim = get_sim(rc)
    if sim is None or sim.busy or el not in sim.index:
        return None
    b = body_state(rc, el, t)
    if b is None:
        return None
    ppm = sim.ppm
    bc = b.box_centre()
    C = np.array([bc[0] * ppm, -bc[1] * ppm, 1.0])
    theta = -math.degrees(b.angle)                   # screen degrees, clockwise
    parent = el.getparent()
    P = sim.world_doc(parent, t) if parent is not None and ln(parent) not in ("composition", "symbol") else np.eye(3)
    pang = math.degrees(math.atan2(P[1, 0], P[0, 0]))
    CL = np.linalg.solve(P, C)
    if ctx is None:
        ctx = sim._ctx(t)
    w, h = sim._size(el, t)
    nctx = rc.node_ctx(el, ctx)
    ax, ay = rc.ev.length(el, "anchorX", nctx, w), rc.ev.length(el, "anchorY", nctx, h)
    sx, sy = rc.ev.num(el, "scaleX", nctx, 1.0), rc.ev.num(el, "scaleY", nctx, 1.0)
    rot = theta - pang
    off = _rot(math.radians(rot)) @ np.array([(w / 2 - ax) * sx, (h / 2 - ay) * sy])
    return float(CL[0] - off[0]), float(CL[1] - off[1]), float(rot)


def install(rc) -> None:
    """Install rc.hooks['physics'](rc, el, ctx) -> (x, y, rotation) | None: simulated rigid-body poses and
    the rotations solved by node IK chains (constraints.ik_pose)."""
    comp = rc.doc.section("composition")
    if comp is None:
        return
    from . import constraints
    bodies = _has_bodies(rc)
    ik = constraints.has_node_ik(rc)
    if not bodies and not ik:
        return

    def hook(rc_, el, ctx):
        if bodies and any(ln(c) == "rigidBody" for c in el):
            pose = body_transform(rc_, el, ctx.comp_t, ctx)
            if pose is not None:
                return pose
        return constraints.ik_pose(rc_, el, ctx) if ik else None
    rc.hooks["physics"] = hook


try:
    from .render import hook_installer
    hook_installer("physics")(install)
except ImportError:      # pragma: no cover
    pass
