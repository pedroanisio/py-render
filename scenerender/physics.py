"""Deterministic, seekable 2D rigid-body physics (physics section, rigidBody on layers/shapes).

Also hosts the small simulation utilities shared by particles, shape modifiers and deformers:
`Seekable` (fixed-step state cache with random access), `hash01` (vectorised seeded hash) and
`vnoise` (seeded smooth value noise).

Conventions (pinned here, the schema leaves them open):
  * Simulation space is the composition: document pixels converted to metres with
    physics/@pixelsPerMeter, +y up (so gravityY=-9.8 pulls down the screen).
  * A body's shape is its node box (w, h) under the node's world transform at activation;
    `circle` uses rigidBody/@radius (metres) or min(w, h)/2. capsule/polygon/convex-hull/path are
    approximated by the box (PARTIAL).
  * Before physics/@start and before rigidBody/@activateAt a dynamic body follows its keyframes
    (as a kinematic body); from activateAt it is simulated with velocityX/Y (m/s, +y up) and
    angularVelocity (deg/s, clockwise on screen).
  * Static and kinematic bodies always follow their keyframes; `body_transform` returns None for
    them so the compositor keeps their keyframed transform.
  * Force fields with affects all|bodies: positions/radius in composition pixels, vectors
    (forceX/forceY, strength) in m/s^2 with +y up (accelerations, mass independent).
  * Constraints: pin (revolute joint to the world point x,y px), hinge (revolute a-b at x,y or b's
    centre), distance, rope (max distance), spring (Hooke + damping ratio), weld (PARTIAL: point +
    soft angle lock); slider and motor are not simulated. restLength is metres (default: initial
    distance). breakForce (N) removes the constraint for the rest of the simulation.
  * @cache/@cacheSha256 on physics are ignored: the simulation is recomputed (it is deterministic).
State is checkpointed in rc.cache, so frames can be rendered in any order with identical results.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import numpy as np

from .document import ln
from .registry import FEATURES, FULL, NONE, PARTIAL, warn_once

FEATURES.declare("physics", PARTIAL, "2D rigid bodies (box/circle), pin/hinge/distance/rope/spring constraints, "
                                     "force fields; simulated poses replace keyframed x/y/rotation")
FEATURES.declare("rigidBody", PARTIAL, "shapes box and circle; capsule/polygon/convex-hull/path approximated as box; "
                                       "collisionGroup/collidesWith/sensor honoured, bullet ignored")
FEATURES.declare("softBody", NONE, "soft bodies are not simulated; the node keeps its keyframed transform")
for _t, _lvl, _n in (("pin", FULL, ""), ("hinge", PARTIAL, "angle limits ignored"), ("distance", FULL, ""),
                     ("rope", FULL, ""), ("spring", FULL, ""), ("weld", PARTIAL, "soft angular lock"),
                     ("slider", NONE, "not simulated"), ("motor", NONE, "not simulated")):
    FEATURES.declare(f"physicsConstraint:{_t}", _lvl, _n)
for _t in ("directional", "radial", "vortex", "turbulence", "drag", "wind"):
    FEATURES.declare(f"forceField:{_t}", FULL)
FEATURES.declare("forceField:attractor-path", PARTIAL, "attracts toward the nearest sampled point of the path")


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


# ====================================================================== geometry helpers
def _rot(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


def _cross(a, b) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _cross_sv(s, v):
    return np.array([-s * v[1], s * v[0]])


@dataclass
class Body:
    el: object
    kind: str                      # static | kinematic | dynamic
    shape: str                     # box | circle
    hx: float = 0.5                # half extents (m) or radius in hx
    hy: float = 0.5
    pos: np.ndarray = field(default_factory=lambda: np.zeros(2))
    angle: float = 0.0             # radians, counter-clockwise (y up)
    vel: np.ndarray = field(default_factory=lambda: np.zeros(2))
    w: float = 0.0
    mass: float = 1.0
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
    v0: tuple = (0.0, 0.0, 0.0)
    fixed_rotation: bool = False

    def verts(self) -> np.ndarray:
        R = _rot(self.angle)
        loc = np.array([[-self.hx, -self.hy], [self.hx, -self.hy], [self.hx, self.hy], [-self.hx, self.hy]])
        return loc @ R.T + self.pos

    def normals(self) -> np.ndarray:
        R = _rot(self.angle)
        return np.array([[0, -1], [1, 0], [0, 1], [-1, 0]], np.float64) @ R.T

    def radius_bound(self) -> float:
        return self.hx if self.shape == "circle" else math.hypot(self.hx, self.hy)

    @property
    def movable(self) -> bool:
        return self.inv_m > 0 or self.inv_i > 0


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


# ---------------------------------------------------------------- narrow phase
def _poly_poly(A: Body, B: Body) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """Box2D-style polygon manifold. Returns [(normal a->b, point, depth)]."""
    va, na = A.verts(), A.normals()
    vb, nb = B.verts(), B.normals()

    def max_sep(v1, n1, v2):
        best, bi = -1e18, 0
        for i in range(4):
            s = np.min((v2 - v1[i]) @ n1[i])
            if s > best:
                best, bi = s, i
        return best, bi

    sa, ia = max_sep(va, na, vb)
    if sa > 0:
        return []
    sb, ib = max_sep(vb, nb, va)
    if sb > 0:
        return []
    if sb > sa + 1e-3:
        ref_v, ref_n, inc_v, inc_n, ri, flip = vb, nb, va, na, ib, True
    else:
        ref_v, ref_n, inc_v, inc_n, ri, flip = va, na, vb, nb, ia, False
    n = ref_n[ri]
    ii = int(np.argmin(inc_n @ n))
    inc = [inc_v[ii], inc_v[(ii + 1) % 4]]
    v1, v2 = ref_v[ri], ref_v[(ri + 1) % 4]
    tangent = (v2 - v1) / (np.linalg.norm(v2 - v1) or 1.0)

    def clip(pts, nrm, off):
        out = []
        d0, d1 = nrm @ pts[0] - off, nrm @ pts[1] - off
        if d0 <= 0:
            out.append(pts[0])
        if d1 <= 0:
            out.append(pts[1])
        if d0 * d1 < 0:
            out.append(pts[0] + (pts[1] - pts[0]) * (d0 / (d0 - d1)))
        return out

    pts = clip(inc, -tangent, -(tangent @ v1))
    if len(pts) < 2:
        return []
    pts = clip(pts, tangent, tangent @ v2)
    if len(pts) < 2:
        return []
    out = []
    front = n @ v1
    for p in pts:
        sep = n @ p - front
        if sep <= 0:
            out.append(((-n if flip else n), p - n * (sep / 2), -sep))
    return out


def _circle_poly(C: Body, P: Body) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """Normal from the polygon toward the circle's centre."""
    R = _rot(P.angle)
    c = (C.pos - P.pos) @ R        # circle centre in box-local
    r = C.hx
    q = np.clip(c, [-P.hx, -P.hy], [P.hx, P.hy])
    d = c - q
    dist = float(np.hypot(*d))
    if dist > r:
        return []
    if dist > 1e-9:
        n_loc = d / dist
        depth = r - dist
        p_loc = q
    else:                                   # centre inside: push out through the nearest face
        gaps = [P.hx - c[0], P.hx + c[0], P.hy - c[1], P.hy + c[1]]
        k = int(np.argmin(gaps))
        n_loc = [np.array([1.0, 0]), np.array([-1.0, 0]), np.array([0, 1.0]), np.array([0, -1.0])][k]
        depth = gaps[k] + r
        p_loc = c
    return [(R @ n_loc, R @ p_loc + P.pos, depth)]


def _collide(A: Body, B: Body) -> list[Contact]:
    if float(np.hypot(*(A.pos - B.pos))) > A.radius_bound() + B.radius_bound():
        return []
    if A.shape == "circle" and B.shape == "circle":
        d = B.pos - A.pos
        dist = float(np.hypot(*d))
        if dist >= A.hx + B.hx:
            return []
        n = d / dist if dist > 1e-9 else np.array([0.0, 1.0])
        return [Contact(A, B, n, A.pos + n * (A.hx - (A.hx + B.hx - dist) / 2), A.hx + B.hx - dist)]
    if A.shape == "circle":
        return [Contact(A, B, -n, p, dep) for n, p, dep in _circle_poly(A, B)]
    if B.shape == "circle":
        return [Contact(A, B, n, p, dep) for n, p, dep in _circle_poly(B, A)]
    return [Contact(A, B, n, p, dep) for n, p, dep in _poly_poly(A, B)]


def _collide_plane(A: Body, n: np.ndarray, off: float) -> list[Contact]:
    """Half-space {p : n.p >= off} is solid-free; contacts where the body crosses below."""
    out = []
    if A.shape == "circle":
        d = n @ A.pos - off - A.hx
        if d < 0:
            out.append(Contact(A, None, -n, A.pos - n * A.hx, -d))
        return out
    for v in A.verts():
        d = n @ v - off
        if d < 0:
            out.append(Contact(A, None, -n, v, -d))
    return out


# ---------------------------------------------------------------- constraints
@dataclass
class Joint:
    el: object
    kind: str
    a: Body
    b: Body | None
    la: np.ndarray                  # local anchor on a
    lb: np.ndarray                  # local anchor on b, or world point when b is None
    rest: float = 0.0
    stiffness: float | None = None
    damping: float = 0.0
    break_force: float | None = None
    ref_angle: float = 0.0
    broken: bool = False
    impulse: float = 0.0


def _world_anchor(body: Body, local: np.ndarray) -> np.ndarray:
    return body.pos + _rot(body.angle) @ local


def _apply_impulse(body: Body | None, P: np.ndarray, r: np.ndarray) -> None:
    if body is None:
        return
    body.vel = body.vel + P * body.inv_m
    body.w += body.inv_i * _cross(r, P)


def _vel_at(body: Body | None, r: np.ndarray) -> np.ndarray:
    if body is None:
        return np.zeros(2)
    return body.vel + _cross_sv(body.w, r)


# ====================================================================== the world
@dataclass
class World:
    bodies: list
    joints: list
    t: float = 0.0


class PhysicsSim:
    def __init__(self, rc):
        self.rc = rc
        self.doc = rc.doc
        ph = self.doc.section("physics")
        self.ph = ph
        ev = rc.ev
        from .evaluator import Ctx
        c0 = Ctx(t=0.0, comp_t=0.0)
        self.dt = float(ph.get("fixedStep", 1 / 120)) if ph is not None else 1 / 120
        self.dt = max(1e-4, self.dt)
        self.ppm = float(ph.get("pixelsPerMeter", 100)) if ph is not None else 100.0
        self.gx = ev.num(ph, "gravityX", c0, 0.0) if ph is not None else 0.0
        self.gy = ev.num(ph, "gravityY", c0, -9.80665) if ph is not None else -9.80665
        self.iters = int(ev.num(ph, "solverIterations", c0, 8)) if ph is not None else 8
        self.start = ev.num(ph, "start", c0, 0.0) if ph is not None else 0.0
        self.bounds = ph.get("bounds", "none") if ph is not None else "none"
        if ph is not None and (ph.get("cache") or ph.get("cacheSha256")):
            warn_once("physics", "cache", "physics @cache is ignored; the simulation is recomputed deterministically")
        self.fields = [f for f in (ph if ph is not None else []) if ln(f) == "forceField"
                       and f.get("affects", "all") in ("all", "bodies")]
        self.body_els = []
        comp = self.doc.section("composition")
        for el in comp.iter():
            if not isinstance(el.tag, str):
                continue
            rb = next((c for c in el if ln(c) == "rigidBody"), None)
            if rb is not None:
                self.body_els.append((el, rb))
            if any(ln(c) == "softBody" for c in el):
                warn_once("softBody", el.get("id", "?"))
        self.index = {el: i for i, (el, _) in enumerate(self.body_els)}
        self.busy = False
        self.sim = Seekable(self._init, self._step, every=120)

    # ------------------------------------------------------------ node poses (keyframed)
    def _ctx(self, t):
        from .evaluator import Ctx
        return Ctx(t=t, comp_t=t)

    def world_doc(self, el, t) -> np.ndarray:
        """Node-local -> composition document pixels at time t (keyframes only, no caches)."""
        rc = self.rc
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
            shift = self.doc.clock_shift.get(node)
            nctx = ctx if not shift else ctx.at(ctx.t - shift)
            self.busy = True
            try:
                M = rc.node_matrix(node, nctx, M, box, None)
            finally:
                self.busy = False
            box = rc.node_size(node, rc.node_ctx(node, nctx), box)
        return M

    def _size(self, el, t):
        rc = self.rc
        parent = el.getparent()
        box = (float(self.doc.width), float(self.doc.height))
        if parent is not None and ln(parent) in ("group", "sequence"):
            box = rc.node_size(parent, rc.node_ctx(parent, self._ctx(t)), box)
        return rc.node_size(el, rc.node_ctx(el, self._ctx(t)), box)

    def keyed_pose(self, el, t):
        """(centre metres, angle rad CCW, half extents m (hx, hy)) from keyframes."""
        w, h = self._size(el, t)
        W = self.world_doc(el, t)
        c = W @ np.array([w / 2, h / 2, 1.0])
        sx = math.hypot(W[0, 0], W[1, 0])
        sy = math.hypot(W[0, 1], W[1, 1])
        ang = math.atan2(W[1, 0], W[0, 0])
        return (np.array([c[0] / self.ppm, -c[1] / self.ppm]), -ang,
                (abs(w * sx) / 2 / self.ppm, abs(h * sy) / 2 / self.ppm))

    # ------------------------------------------------------------ setup
    def _init(self) -> World:
        ev = self.rc.ev
        bodies = []
        t0 = self.start
        for el, rb in self.body_els:
            c0 = self._ctx(t0)
            kind = rb.get("type", "dynamic")
            pos, ang, (hx, hy) = self.keyed_pose(el, t0)
            shape = rb.get("shape", "box")
            if shape not in ("box", "circle"):
                warn_once("rigidBody", f"shape:{shape}", "approximated as box")
                shape = "box"
            if shape == "circle":
                r = ev.num(rb, "radius", c0, 0.0)
                hx = hy = r if r > 0 else min(hx, hy)
            b = Body(el, kind, shape, max(hx, 1e-3), max(hy, 1e-3), pos, ang)
            b.mass = max(1e-6, ev.num(rb, "mass", c0, 1.0))
            b.friction = ev.num(rb, "friction", c0, 0.5)
            b.restitution = ev.num(rb, "restitution", c0, 0.0)
            b.lin_damp = ev.num(rb, "linearDamping", c0, 0.01)
            b.ang_damp = ev.num(rb, "angularDamping", c0, 0.01)
            b.activate_at = ev.num(rb, "activateAt", c0, 0.0)
            b.group = int(ev.num(rb, "collisionGroup", c0, 0.0))
            cw = rb.get("collidesWith", "all")
            b.collides = "all" if cw.strip() in ("", "all") else {int(v) for v in cw.replace(",", " ").split() if v.lstrip("-").isdigit()}
            b.sensor = rb.get("sensor") == "true"
            b.fixed_rotation = rb.get("fixedRotation") == "true"
            b.v0 = (ev.num(rb, "velocityX", c0, 0.0), ev.num(rb, "velocityY", c0, 0.0),
                    -math.radians(ev.num(rb, "angularVelocity", c0, 0.0)))
            bodies.append(b)
        joints = []
        by_id = {el.get("id"): b for b, (el, _) in zip(bodies, self.body_els) if el.get("id")}
        for c in (self.ph if self.ph is not None else []):
            if ln(c) != "constraint":
                continue
            kind = c.get("type")
            a = by_id.get(c.get("a"))
            b = by_id.get(c.get("b")) if c.get("b") else None
            if a is None:
                warn_once("physicsConstraint", c.get("id", "?"), "body a not found")
                continue
            if kind in ("slider", "motor"):
                warn_once("physicsConstraint", kind)
                continue
            c0 = self._ctx(t0)
            if c.get("x") is not None or c.get("y") is not None:
                wp = np.array([ev.num(c, "x", c0, 0.0) / self.ppm, -ev.num(c, "y", c0, 0.0) / self.ppm])
            else:
                wp = b.pos.copy() if b is not None else a.pos.copy()
            if kind in ("pin", "hinge", "weld"):
                la = _rot(-a.angle) @ (wp - a.pos)
                lb = (_rot(-b.angle) @ (wp - b.pos)) if b is not None else wp
            else:
                la = np.zeros(2)
                lb = np.zeros(2) if b is not None else wp
            pa = _world_anchor(a, la)
            pb = _world_anchor(b, lb) if b is not None else lb
            rest = ev.num(c, "restLength", c0, -1.0)
            if rest < 0:
                rest = float(np.hypot(*(pb - pa)))
            stiff = ev.num(c, "stiffness", c0, -1.0)
            j = Joint(c, kind, a, b, la, lb, rest, stiff if stiff > 0 else None, ev.num(c, "damping", c0, 0.0),
                      ev.num(c, "breakForce", c0, -1.0) if c.get("breakForce") else None,
                      (b.angle if b is not None else 0.0) - a.angle)
            joints.append(j)
        w = World(bodies, joints, t0)
        self._refresh_mass(w)
        return w

    def _refresh_mass(self, w: World) -> None:
        for b in w.bodies:
            if b.active:
                b.inv_m = 1.0 / b.mass
                if b.fixed_rotation:
                    b.inv_i = 0.0
                elif b.shape == "circle":
                    b.inv_i = 1.0 / (0.5 * b.mass * b.hx * b.hx)
                else:
                    b.inv_i = 1.0 / (b.mass * ((2 * b.hx) ** 2 + (2 * b.hy) ** 2) / 12.0)
            else:
                b.inv_m = b.inv_i = 0.0

    # ------------------------------------------------------------ step
    def _field_accel(self, f, b: Body, t: float) -> np.ndarray:
        ev = self.rc.ev
        c = self._ctx(t)
        s, e = ev.num(f, "start", c, 0.0), (ev.num(f, "end", c, 0.0) if f.get("end") is not None else None)
        if t < s or (e is not None and t >= e):
            return np.zeros(2)
        typ = f.get("type")
        strength = ev.num(f, "strength", c, 0.0)
        centre = np.array([ev.num(f, "x", c, 0.0) / self.ppm, -ev.num(f, "y", c, 0.0) / self.ppm])
        radius = ev.num(f, "radius", c, 0.0) / self.ppm
        falloff = ev.num(f, "falloff", c, 0.0)
        d = b.pos - centre
        dist = float(np.hypot(*d))

        def fall():
            if radius > 0:
                if dist >= radius:
                    return 0.0
                return (1 - dist / radius) ** falloff if falloff > 0 else 1.0
            return 1.0 / (1.0 + dist) ** falloff if falloff > 0 else 1.0

        if typ in ("directional", "wind"):
            v = np.array([ev.num(f, "forceX", c, 0.0), ev.num(f, "forceY", c, 0.0)])
            if typ == "wind" and strength > 0:     # wind: pull velocity toward the wind vector
                return (v - b.vel) * strength
            return v
        if typ == "radial":
            return (d / dist if dist > 1e-9 else np.zeros(2)) * strength * fall()
        if typ == "vortex":
            tang = np.array([d[1], -d[0]]) / dist if dist > 1e-9 else np.zeros(2)   # clockwise on screen
            return tang * strength * fall()
        if typ == "turbulence":
            sc = max(1e-6, ev.num(f, "scale", c, 1.0))
            seed = self.rc.ev.seed_for(f, "turb")
            nx = float(fbm(seed, b.pos[0] / sc, b.pos[1] / sc, t * 0.5))
            ny = float(fbm(seed + 1, b.pos[0] / sc, b.pos[1] / sc, t * 0.5))
            return np.array([nx, ny]) * strength
        if typ == "drag":
            return -b.vel * strength
        if typ == "attractor-path" and f.get("path"):
            from .geometry import PathSampler
            key = ("ff-path", f)
            ps = self.rc.cache.get(key) or PathSampler(f.get("path"))
            self.rc.cache[key] = ps
            pts = np.array(ps.pts) / self.ppm * np.array([1, -1])
            k = int(np.argmin(np.hypot(*(pts - b.pos).T)))
            dd = pts[k] - b.pos
            n = float(np.hypot(*dd))
            return dd / n * strength if n > 1e-9 else np.zeros(2)
        return np.zeros(2)

    def _step(self, w: World, i: int) -> None:
        dt = self.dt
        t = self.start + i * dt
        t1 = t + dt
        # activation & kinematic poses
        changed = False
        for b in w.bodies:
            if b.kind == "dynamic" and not b.active and t1 >= b.activate_at - 1e-9:
                pos, ang, _ = self.keyed_pose(b.el, max(t, b.activate_at))
                b.pos, b.angle = pos, ang
                b.vel = np.array(b.v0[:2], np.float64)
                b.w = b.v0[2]
                b.active = True
                changed = True
            if not b.active and b.kind != "static":
                pos, ang, _ = self.keyed_pose(b.el, t1)
                b.vel = (pos - b.pos) / dt
                b.w = (ang - b.angle) / dt
                b.pos, b.angle = pos, ang
        if changed:
            self._refresh_mass(w)
        # forces
        g = np.array([self.gx, self.gy])
        for b in w.bodies:
            if not b.active:
                continue
            acc = g.copy()
            for f in self.fields:
                acc += self._field_accel(f, b, t)
            b.vel = b.vel + acc * dt
            b.vel = b.vel / (1.0 + dt * b.lin_damp)
            b.w = b.w / (1.0 + dt * b.ang_damp)
        for j in w.joints:
            if j.kind == "spring" and not j.broken:
                self._spring(j, dt)
        # contacts
        contacts: list[Contact] = []
        bs = w.bodies
        for ia in range(len(bs)):
            A = bs[ia]
            for ib in range(ia + 1, len(bs)):
                B = bs[ib]
                if not (A.active or B.active) or A.sensor or B.sensor:
                    continue
                if not self._filter(A, B):
                    continue
                contacts += _collide(A, B)
        W_, H_ = self.doc.width / self.ppm, self.doc.height / self.ppm
        planes = []
        if self.bounds in ("floor", "frame"):
            planes.append((np.array([0.0, 1.0]), -H_))            # floor: y >= -H
        if self.bounds == "frame":
            planes += [(np.array([1.0, 0.0]), 0.0), (np.array([-1.0, 0.0]), -W_), (np.array([0.0, -1.0]), 0.0)]
        for b in bs:
            if b.active and not b.sensor:
                for n, off in planes:
                    contacts += _collide_plane(b, n, off)
        for b in bs:
            b.vel = np.array(b.vel, np.float64)      # owned, writable (the scalar solver writes in place)
        self._prepare(contacts, dt)
        live = [j for j in w.joints if not j.broken and j.kind != "spring"]
        for _ in range(max(1, self.iters)):
            for j in live:
                self._solve_joint(j, dt)
            for c in contacts:
                self._solve_contact(c)
        for j in live:
            if j.break_force is not None and abs(j.impulse) / dt > j.break_force:
                j.broken = True
            j.impulse = 0.0
        for b in bs:
            if b.active:
                b.pos = b.pos + b.vel * dt
                b.angle += b.w * dt
        w.t = t1

    def _filter(self, A: Body, B: Body) -> bool:
        ok = lambda X, Y: X.collides == "all" or Y.group in X.collides  # noqa: E731
        return ok(A, B) and ok(B, A)

    def _prepare(self, contacts, dt):
        beta, slop = 0.2, 0.005
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
            c.bias = beta / dt * max(0.0, c.depth - slop)
            if vn < -1.0:
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

    def _spring(self, j: Joint, dt: float) -> None:
        A, B = j.a, j.b
        pa = _world_anchor(A, j.la)
        pb = _world_anchor(B, j.lb) if B is not None else j.lb
        d = pb - pa
        L = float(np.hypot(*d))
        if L < 1e-9:
            return
        n = d / L
        k = j.stiffness if j.stiffness is not None else 50.0
        mA = A.mass if A.active else 0.0
        mB = (B.mass if B is not None and B.active else 0.0)
        meff = (mA * mB / (mA + mB)) if (mA > 0 and mB > 0) else (mA or mB or 1.0)
        c = j.damping * 2.0 * math.sqrt(k * meff)
        rel = float((_vel_at(B, pb - B.pos) if B is not None else np.zeros(2)) @ n - _vel_at(A, pa - A.pos) @ n)
        F = k * (L - j.rest) + c * rel
        if j.break_force is not None and abs(F) > j.break_force:
            j.broken = True
            return
        P = n * F * dt
        _apply_impulse(A, P, pa - A.pos)
        if B is not None:
            _apply_impulse(B, -P, pb - B.pos)

    def _solve_joint(self, j: Joint, dt: float) -> None:
        A, B = j.a, j.b
        ra = _rot(A.angle) @ j.la
        pa = A.pos + ra
        if B is not None:
            rb = _rot(B.angle) @ j.lb
            pb = B.pos + rb
        else:
            rb = np.zeros(2)
            pb = j.lb
        imb, iib = (B.inv_m, B.inv_i) if B is not None else (0.0, 0.0)
        if A.inv_m + imb == 0 and A.inv_i + iib == 0:
            return
        if j.kind in ("pin", "hinge", "weld"):
            K = np.array([[A.inv_m + imb + A.inv_i * ra[1] ** 2 + iib * rb[1] ** 2,
                           -A.inv_i * ra[0] * ra[1] - iib * rb[0] * rb[1]],
                          [-A.inv_i * ra[0] * ra[1] - iib * rb[0] * rb[1],
                           A.inv_m + imb + A.inv_i * ra[0] ** 2 + iib * rb[0] ** 2]])
            soft = 1.0 if j.stiffness is None else min(1.0, j.stiffness * dt * dt * 10)
            err = pb - pa
            dv = _vel_at(B, rb) - _vel_at(A, ra)
            try:
                P = np.linalg.solve(K, dv + err * (0.2 / dt)) * soft
            except np.linalg.LinAlgError:
                return
            _apply_impulse(A, P, ra)
            _apply_impulse(B, -P, rb)
            j.impulse += float(np.hypot(*P))
            if j.kind == "weld":
                wb = B.w if B is not None else 0.0
                cerr = (B.angle if B is not None else 0.0) - A.angle - j.ref_angle
                ik = A.inv_i + iib
                if ik > 0:
                    L = ((wb - A.w) + cerr * 0.2 / dt) / ik * 0.5
                    A.w += A.inv_i * L
                    if B is not None:
                        B.w -= iib * L
            return
        # distance / rope: 1D along the axis
        d = pb - pa
        Ld = float(np.hypot(*d))
        if Ld < 1e-9:
            return
        n = d / Ld
        C = Ld - j.rest
        if j.kind == "rope" and C <= 0:
            return
        rna, rnb = _cross(ra, n), _cross(rb, n)
        k = A.inv_m + imb + A.inv_i * rna * rna + iib * rnb * rnb
        if k <= 0:
            return
        vrel = float((_vel_at(B, rb) - _vel_at(A, ra)) @ n)
        lam = -(vrel + 0.2 / dt * C) / k
        if j.kind == "rope":
            lam = min(lam, 0.0)
        P = n * lam
        _apply_impulse(A, -P, ra)
        _apply_impulse(B, P, rb)
        j.impulse += abs(lam)

    # ------------------------------------------------------------ query
    def world_at(self, t: float) -> World | None:
        if t < self.start:
            return None
        n = int(math.floor((t - self.start) / self.dt + 1e-6))
        return self.sim.at(n)


def get_sim(rc) -> PhysicsSim | None:
    key = ("physics-sim",)
    if key not in rc.cache:
        rc.cache[key] = None            # re-entrant calls while building see "no simulation"
        rc.cache[key] = PhysicsSim(rc) if rc.doc.section("physics") is not None or _has_bodies(rc) else None
    return rc.cache[key]


def _has_bodies(rc) -> bool:
    comp = rc.doc.section("composition")
    return comp is not None and any(ln(e) == "rigidBody" for e in comp.iter() if isinstance(e.tag, str))


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
    C = np.array([b.pos[0] * ppm, -b.pos[1] * ppm, 1.0])
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
    """Install rc.hooks['physics'](rc, el, ctx) -> (x, y, rotation) | None when the document has bodies."""
    comp = rc.doc.section("composition")
    if comp is None or not _has_bodies(rc):
        return
    rc.hooks["physics"] = lambda rc_, el, ctx: (body_transform(rc_, el, ctx.comp_t, ctx)
                                                 if any(ln(c) == "rigidBody" for c in el) else None)


try:
    from .render import hook_installer
    hook_installer("physics")(install)
except ImportError:      # pragma: no cover
    pass
