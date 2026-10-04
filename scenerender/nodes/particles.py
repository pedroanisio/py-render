"""particleEmitter: deterministic, time-seekable particle systems drawn with cairo.

Simulation
  * Fixed step (1/60 s) from the emitter's start minus @preroll. States are checkpointed in
    rc.cache (every 0.5 s) and the last computed state is kept, so sequential frames cost one
    step each and seeking anywhere gives identical results whatever was rendered before.
    Between steps positions are extrapolated linearly to the exact frame time.
  * Every per-particle random value is hash(seed, particle id, channel) with the seed from
    rc.ev.seed_for(emitter) at birth: a pure function of project seed, emitter id/@seed.
Spaces
  * Particles are born in emitter-local space (emitter shape, @direction rotated/scaled with the
    emitter) and then live in the emitter's *parent* space: moving the emitter leaves existing
    particles behind, moving the parent moves them all. Parent links/constraints define that
    inherited frame; other constraints, layout and dimensions are sampled at birth.
    Gravity and turbulence act in parent space
    (document pixels, +y down). Force fields and collisions use composition/symbol space,
    carrying the current parent transform in both directions.
  * Emitter shapes are centred on the emitter origin (x, y): rect/ellipse span
    emitterWidth x emitterHeight around it, line runs horizontally through it (emitterWidth),
    point is the origin, path uses @emitterPath coordinates as authored (local px) and samples it
    uniformly by exact arc length (subpaths are not bridged), asset-alpha samples opaque pixels
    (alpha > 0.5) of @emitterAsset (centred) rendered at the emission time (media time = time since
    the emitter's start). Asset caches include the full sample context and media time.
Units
  * rate particles/s; lifetime, variances: seconds / same units as the base (uniform +/-);
    speed px/s; direction deg (0 = +x, -90 = up); spread = full cone angle in degrees;
    gravityX/Y px/s^2; drag 1/s (v *= exp(-drag dt)); turbulence ~ RMS acceleration px/s^2 of a
    seeded value-noise field of period @turbulenceScale px; size = diameter px (disc/square/sprite
    width, streak thickness); angularVelocity deg/s; trail = seconds of each particle's actual
    trajectory (recorded every step) drawn behind it as a polyline fading from 0 to the particle's
    opacity (streaks: full width; other shapes: 0.7 x size under the particle).
  * Force fields (physics/forceField, listed in @forceFields, else every field with affects
    all|particles): physics.field_accel with px units (accelerations px/s^2, +y up for forceY like
    the physics section, distances for falloff in metres via pixelsPerMeter). Particle positions are
    converted from the effective parent space to scene px for the fields (and back).
    Field velocities include parent motion, so wind/drag respond to inherited movement.
  * collide: the composition/symbol floor and side walls, and the current rendered alpha
    (>= 0.5) of nonsensor rigid-body nodes. Swept segments sample alpha at most half a scene
    pixel apart and refine the first crossing. Contact velocity includes body and parent
    motion per emitter-local second; @bounce reflects its normal component and tangential
    speed is multiplied by 0.8. Deeply engulfed particles use the nearest exterior pixel.
    See particle_collisions.py for mask caching and compound-body limitations.
  * Sprites (shape="sprite") are tinted: the sprite's colour channels are multiplied by the particle
    colour (color -> colorEnd over life, or the preset palette) and its alpha by the particle opacity;
    a white colour leaves the sprite unchanged.
Presets supply defaults that explicit attributes override (see PRESETS below). Internal
preset-only look parameters: soft (edge softness 0..1 of discs), fade_in (fraction of life),
palette (per-particle colours when @color is not given), flutter (confetti tumbling), ring
(bubbles), twinkle (glitter).
"""
from __future__ import annotations

import math
from collections import OrderedDict
from copy import copy

import cairo
import numpy as np

from .. import curves
from ..compositor import RenderContext, rotate, translate
from ..document import SceneError, ln
from ..evaluator import ANIM_TAGS, Ctx
from ..physics import PathGeom, Seekable, fbm, field_accel, hash01
from ..raster import Canvas, intersect
from ..registry import ASSETS, FEATURES, FULL, NODES, PARTIAL, warn_once
from ..values import paint_ref, parse_color, resolve_var
from .particle_paints import ParticlePaint, mask_surface, trail_mask

FEATURES.declare("particles:collide", PARTIAL, "animated alpha and particle-backed mattes; cyclic dependencies are rejected (docs/PARTICLES.md)")
FEATURES.declare("particles:emitterShape:asset-alpha", FULL, "the asset's alpha rendered at the emission time")
FEATURES.declare("particles:emitterShape:path", FULL, "uniform by exact arc length")
FEATURES.declare("particles:trail", FULL, "recorded trajectories")
FEATURES.declare("particles:sprite", FULL, "sheets (cols/rows/fps) tinted by the particle colour")
DT = 1.0 / 60.0
CHECKPOINT_EVERY = 30

PRESETS: dict[str, dict] = {
    "smoke": dict(rate=20, lifetime=4, lifetimeVariance=1, speed=50, speedVariance=20, direction=-90, spread=30,
                  gravityY=-10, drag=0.4, turbulence=25, turbulenceScale=200, size=40, sizeEnd=160,
                  sizeVariance=10, color="#9AA0AA66", opacityEnd=0, angularVelocityVariance=20, shape="disc",
                  soft=1.0, fade_in=0.15),
    "sparks": dict(rate=80, lifetime=1, lifetimeVariance=0.3, speed=400, speedVariance=120, direction=-90, spread=50,
                   gravityY=900, drag=0.5, size=3, sizeEnd=0, color="#FFD27AFF", colorEnd="#FF4A1A00",
                   shape="streak", trail=0.04),
    "dust": dict(rate=20, lifetime=6, lifetimeVariance=2, speed=10, speedVariance=5, spread=360, turbulence=8,
                 turbulenceScale=300, size=3, sizeVariance=1.5, color="#FFFFFF55", opacityEnd=0, soft=0.6,
                 fade_in=0.2),
    "rain": dict(rate=300, lifetime=1.5, speed=1200, speedVariance=150, direction=100, spread=3, gravityY=400,
                 size=2, color="#AFC6E6AA", shape="streak", trail=0.03),
    "snow": dict(rate=60, lifetime=8, lifetimeVariance=2, speed=60, speedVariance=20, direction=90, spread=30,
                 turbulence=30, turbulenceScale=250, drag=0.2, size=5, sizeVariance=2.5, color="#FFFFFFE6",
                 soft=0.5, fade_in=0.05),
    "confetti": dict(rate=40, lifetime=5, speed=300, speedVariance=100, direction=-90, spread=60, gravityY=400,
                     drag=1.0, size=10, sizeVariance=3, shape="square", rotationVariance=180, angularVelocity=360,
                     angularVelocityVariance=180, flutter=True,
                     palette=("#FF5A36", "#FFC23D", "#2EC4B6", "#6B4BFF", "#FF4FA3", "#FFFFFF")),
    "fire": dict(rate=60, lifetime=1.2, lifetimeVariance=0.3, speed=90, speedVariance=30, direction=-90, spread=20,
                 gravityY=-120, turbulence=40, turbulenceScale=80, size=28, sizeEnd=4, color="#FFC04DFF",
                 colorEnd="#FF300000", soft=1.0),
    "bubbles": dict(rate=8, lifetime=5, speed=60, direction=-90, spread=20, gravityY=-20, turbulence=20,
                    size=16, sizeVariance=8, color="#BFE6FF40", ring=True, fade_in=0.05),
    "bokeh": dict(rate=3, lifetime=6, lifetimeVariance=2, speed=8, spread=360, size=60, sizeVariance=30,
                  color="#FFE0A040", opacityEnd=0, soft=0.25, fade_in=0.3),
    "glitter": dict(rate=40, lifetime=1.5, speed=120, spread=360, gravityY=60, drag=1.5, size=4, sizeEnd=0,
                    color="#FFE9A8FF", twinkle=True),
}

NUM = ("rate", "lifetime", "lifetimeVariance", "speed", "speedVariance", "direction", "spread", "gravityX",
       "gravityY", "drag", "turbulence", "turbulenceScale", "size", "sizeEnd", "sizeVariance", "opacityEnd",
       "rotation0", "rotationVariance", "angularVelocity", "angularVelocityVariance", "maxParticles",
       "emitterWidth", "emitterHeight", "spriteCols", "spriteRows", "spriteFps", "trail", "bounce", "preroll")
STR = ("sizeCurve", "colorCurve", "color", "colorEnd", "emitterShape", "emitterPath", "emitterAsset", "shape",
       "sprite", "forceFields")
BOOL = ("orientToVelocity", "collide")
SPAWN = ("rate", "lifetime", "lifetimeVariance", "speed", "speedVariance", "direction", "spread", "size",
         "sizeEnd", "sizeVariance", "rotation0", "rotationVariance", "angularVelocity", "angularVelocityVariance",
         "emitterWidth", "emitterHeight", "maxParticles")
DYN = ("gravityX", "gravityY", "drag", "turbulence", "turbulenceScale", "bounce")

for _p in PRESETS:
    FEATURES.declare(f"particles:preset:{_p}", "full")


# ====================================================================== parameters
class Params:
    """Parameter access with preset defaults: explicit attribute/animation > preset > schema default."""

    def __init__(self, rc: RenderContext, el, ctx: Ctx):
        self.rc, self.el = rc, el
        preset = rc.ev.str(el, "preset", ctx)
        self.preset = PRESETS.get(preset or "", {})
        if preset and not self.preset:
            warn_once("particles", f"preset:{preset}", "unknown preset")
        self.animated = set()
        for a in el:
            if ln(a) in ANIM_TAGS:
                if ln(a) == "motionPath":
                    self.animated |= {"x", "y"}
                elif a.get("property"):
                    self.animated.add(a.get("property"))
        self.overrides = {p for (tid, p), _ in ctx.scope.overrides if tid == el.get("id")}
        self._const: dict = {}
        self.ctx0 = ctx

    def explicit(self, name: str) -> bool:
        return self.el.get(name) is not None or name in self.animated or name in self.overrides

    def get(self, name: str, ctx: Ctx | None = None):
        constant = name not in self.animated and "preset" not in self.animated
        if constant and name in self._const:
            return self._const[name]
        ctx = ctx or self.ctx0
        ev = self.rc.ev
        if self.explicit(name):
            if name in BOOL:
                v = ev.bool(self.el, name, ctx, False)
            elif name in NUM:
                v = ev.num(self.el, name, ctx, 0.0)
            else:
                v = ev.str(self.el, name, ctx)
        elif name in self.preset_at(ctx):
            v = self.preset_at(ctx)[name]
        elif name in BOOL:
            v = ev.bool(self.el, name, ctx, False)
        elif name in NUM:
            v = ev.num(self.el, name, ctx, 0.0)
        else:
            v = ev.str(self.el, name, ctx)
        if constant:
            self._const[name] = v
        return v

    def preset_at(self, ctx=None):
        if "preset" not in self.animated:
            return self.preset
        return PRESETS.get(self.rc.ev.str(self.el, "preset", ctx or self.ctx0) or "", {})

    def look(self, key, default=None, ctx=None):
        return self.preset_at(ctx).get(key, default)


def _birth_seed(rc: RenderContext, el, ctx: Ctx) -> int:
    """The emitter's seed with @seed evaluated at ctx (animated, scoped or overridden). The stream is a hash
    of the seed's text, so an evaluated number is written the way an author writes it: a literal that
    evaluates to the same number keeps its own text, and an integral value is "42", not the float format
    "42.0" (which would give every emitter with a plain numeric seed a different random stream)."""
    raw, value = el.get("seed", ""), rc.ev.str(el, "seed", ctx, "")
    try:
        number = float(value)
        if raw and float(raw) == number:
            value = raw
        elif number.is_integer():
            value = str(int(number))
    except ValueError:
        pass
    return rc.ev.seed_for_value(el, "particles", value)


# ====================================================================== simulation
class Emitter:
    def __init__(self, rc: RenderContext, el, ctx: Ctx, params=None):
        self.rc, self.el = rc, el
        self.P = params or Params(rc, el, ctx)
        self.seed = _birth_seed(rc, el, ctx)
        clock = rc.node_ctx(el, ctx)
        s, e = clock.node_start, clock.node_end
        self.start, self.end = s, e
        self.preroll = max(0.0, self.P.get("preroll"))
        self.t0 = s - self.preroll
        self.ctx_base = ctx
        self.transforms = OrderedDict()
        self.collision_transforms = OrderedDict()
        self.bursts = []
        for b in el:
            if ln(b) == "burst":
                bt = float(b.get("time", 0))
                cnt = int(float(b.get("count", 0)))
                rep = int(float(b.get("repeat", 0) or 0))
                iv = float(b.get("interval", 1) or 1)
                self.bursts += [(bt + k * iv, cnt) for k in range(rep + 1)]
        self.fields = self._fields()
        self.emit_pts: dict = {}
        self.path_samplers = OrderedDict()
        tr = self.P.get("trail") or 0.0
        self.hist = int(math.ceil(tr / DT)) + 2 if tr > 0 else 0
        self.colliders = OrderedDict()
        self.states = OrderedDict()
        self._querying = False
        ph = rc.doc.section("physics")
        self.ppm = float(ph.get("pixelsPerMeter", 100)) if ph is not None else 100.0
        self.sim = Seekable(self._init, self._step, every=CHECKPOINT_EVERY)

    # ------------------------------------------------------------ helpers
    def ctx_at(self, t: float) -> Ctx:
        return self.rc.ev.context_at_local(self.el, self.ctx_base, t)

    def seed_at(self, ctx: Ctx) -> int:
        return _birth_seed(self.rc, self.el, ctx) if "seed" in self.P.animated else self.seed

    def L(self, t: float) -> np.ndarray:
        return self._transforms(t)[0]

    def _transforms(self, t: float):
        """Birth transform and inherited parent frame on the current scene canvas.

        Layout/box changes and constraints belong to the emitter at birth.
        Explicit parent links and parent constraints move all living particles.
        Each history owns this small exact-time cache; referenced transforms are
        evaluated in a temporary frame cache, not retained for every replay step.
        """
        if t in self.transforms:
            self.transforms.move_to_end(t)
            return self.transforms[t]
        from ..references import scene_space
        ctx = self.ctx_at(t)
        rc = scene_space(self.rc, self.el, ctx)
        loc = rc.node_location(self.el, ctx)
        rc = loc.rc
        c = rc.enter_node(self.el, loc.ctx)
        size = rc.node_size(self.el, c, loc.box, loc.layout)
        L = rc.local_matrix(self.el, c, loc.box, size, loc.layout)
        parent = loc.matrix
        ref = rc.ev.str(self.el, "parent", c)
        if ref:
            target, target_ctx = rc.ev.reference(ref, self.el, c)
            if target is not None and (target is not self.el or target_ctx.scope != c.scope):
                parent = rc.world_matrix(target, target_ctx)
        for constraint in self.el:
            if ln(constraint) != "transformConstraint":
                continue
            from ..constraints import apply_constraint
            if rc.ev.str(constraint, "type", c) == "parent":
                # Parent constraints left-multiply the current matrix, including
                # influence blending, so apply that same map to the inherited frame.
                parent = apply_constraint(rc, constraint, self.el, parent, c)
            else:
                M = apply_constraint(rc, constraint, self.el, parent @ L, c)
                try:
                    L = np.linalg.solve(parent, M)
                except np.linalg.LinAlgError:
                    pass  # A collapsed parent has no invertible scene coordinates.
        canvas = rc.scene_matrix if rc.scene_matrix is not None else rc.root_matrix
        # Traversal starts at this scene boundary, independently of its outer
        # display transform (which can be singular during history replay).
        out = L, parent, canvas
        self.transforms[t] = out
        while len(self.transforms) > 8:
            self.transforms.popitem(last=False)
        return out

    def _fields(self):
        ph = self.rc.doc.section("physics")
        if ph is None:
            return []
        ids = self.P.get("forceFields")
        allf = [f for f in ph if ln(f) == "forceField"]
        if ids:
            want = set(ids.split())
            out = [f for f in allf if f.get("id") in want]
            for m in want - {f.get("id") for f in out}:
                warn_once("particles", f"forceField:{m}", "force field not found")
            # Keep all candidates: forceFields and affects can both change
            # during simulation and are selected at the individual step.
            return allf
        return allf

    def _init(self) -> dict:
        z = np.zeros(0)
        return dict(x=z, y=z, vx=z, vy=z, age=z, life=z, s0=z, s1=z, rot=z, av=z,
                    pid=np.zeros(0, np.int64), seed=np.zeros(0, np.uint32), acc=0.0, next_id=0,
                    hx=np.zeros((0, self.hist)), hy=np.zeros((0, self.hist)))

    # ------------------------------------------------------------ emission
    def _positions(self, ids: np.ndarray, t: float) -> tuple[np.ndarray, np.ndarray]:
        P = self.P
        c = self.ctx_at(t)
        shape = P.get("emitterShape", c) or "rect"
        w, h = P.get("emitterWidth", c), P.get("emitterHeight", c)
        seed = self.seed_at(c)
        r1, r2 = hash01(seed, ids, 1), hash01(seed, ids, 2)
        if shape == "point":
            return np.zeros(len(ids)), np.zeros(len(ids))
        if shape == "rect":
            return (r1 - 0.5) * w, (r2 - 0.5) * h
        if shape == "line":
            return (r1 - 0.5) * w, np.zeros(len(ids))
        if shape == "ellipse":
            rr = np.sqrt(r1)
            a = r2 * 2 * math.pi
            return np.cos(a) * rr * w / 2, np.sin(a) * rr * h / 2
        if shape == "path":
            path = P.get("emitterPath", c) or "M0 0"
            sampler = self.path_samplers.pop(path, None)
            if sampler is None:
                sampler = PathGeom(path)
            self.path_samplers[path] = sampler
            while len(self.path_samplers) > 8:
                self.path_samplers.popitem(last=False)
            x, y, _ = sampler.sample(r1)
            return x, y
        if shape == "asset-alpha":
            pts, aw, ah = self._asset_points(P.get("emitterAsset", c), t)
            if len(pts) == 0:
                return np.zeros(len(ids)), np.zeros(len(ids))
            k = np.minimum((r1 * len(pts)).astype(np.int64), len(pts) - 1)
            return pts[k, 0] + (r2 - 0.5) - aw / 2, pts[k, 1] + (hash01(seed, ids, 3) - 0.5) - ah / 2
        warn_once("particles", f"emitterShape:{shape}", "unknown emitter shape; using point")
        return np.zeros(len(ids)), np.zeros(len(ids))

    def _asset_points(self, aid, t: float):
        """Opaque pixel centres of the emitter asset rendered at emission time t (media time since the
        emitter's start), cached by the exact evaluation context and source time."""
        rc = self.rc
        asset = rc.doc.ids.get(aid) if aid else None
        fn = ASSETS.get(ln(asset)) if asset is not None else None
        if fn is None:
            warn_once("particles", f"emitterAsset:{aid}", "emitter asset not found")
            return np.zeros((0, 2)), 0.0, 0.0
        src_t = max(0.0, t - self.start)
        ctx = self.ctx_at(t)
        key = (aid, ctx, src_t)
        hit = self.emit_pts.get(key)
        if hit is not None:
            return hit
        aw, ah = rc.asset_size(asset, ctx)
        buf = fn(rc, asset, np.eye(3), ctx, src_t=src_t)
        if buf is None:
            out = (np.zeros((0, 2)), aw, ah)
        else:
            ys, xs = np.nonzero(buf.px[..., 3] > 0.5)
            out = (np.stack([xs + buf.x0 + 0.5, ys + buf.y0 + 0.5], 1).astype(np.float64), aw, ah)
        if len(self.emit_pts) > 4:
            self.emit_pts.pop(next(iter(self.emit_pts)))
        self.emit_pts[key] = out
        return out

    def _spawn(self, st: dict, times: np.ndarray, t_end: float) -> None:
        # Bursts and continuous emission can share a step while sampling different
        # paths, transforms, seeds or other birth properties within that step.
        for time, count in zip(*np.unique(times, return_counts=True)):
            self._spawn_batch(st, np.full(count, time), t_end)

    def _spawn_batch(self, st: dict, times: np.ndarray, t_end: float) -> None:
        n = len(times)
        if n == 0:
            return
        P = self.P
        tm = float(times[0])
        c = self.ctx_at(tm)
        g = lambda k: P.get(k, c)  # noqa: E731
        cap = int(max(1, g("maxParticles") or 10000))
        room = cap - len(st["x"])
        if room <= 0:
            return
        times = times[:room]
        n = len(times)
        ids = np.arange(st["next_id"], st["next_id"] + n, dtype=np.int64)
        st["next_id"] += n
        seed = self.seed_at(c)
        H = lambda ch: hash01(seed, ids, ch)  # noqa: E731
        sym = lambda ch: H(ch) * 2 - 1  # noqa: E731
        ex, ey = self._positions(ids, tm)
        L = self.L(tm)
        lin = L[:2, :2]
        kscale = math.sqrt(abs(float(np.linalg.det(lin)))) or 1.0
        px = L[0, 0] * ex + L[0, 1] * ey + L[0, 2]
        py = L[1, 0] * ex + L[1, 1] * ey + L[1, 2]
        ang = np.radians(g("direction") + g("spread") * (H(4) - 0.5))
        dx, dy = np.cos(ang), np.sin(ang)
        wx, wy = lin[0, 0] * dx + lin[0, 1] * dy, lin[1, 0] * dx + lin[1, 1] * dy
        norm = np.hypot(wx, wy)
        norm[norm == 0] = 1
        spd = (g("speed") + g("speedVariance") * sym(5)) * kscale
        vx, vy = wx / norm * spd, wy / norm * spd
        life = np.maximum(1e-3, g("lifetime") + g("lifetimeVariance") * sym(6))
        size = g("size")
        s_end = g("sizeEnd") if (P.explicit("sizeEnd") or "sizeEnd" in P.preset_at(c)) else size
        sv = g("sizeVariance") * sym(7)
        s0 = np.maximum(0.0, size + sv) * kscale
        ratio = np.where(size > 0, (size + sv) / size if size else 1.0, 1.0)
        s1 = np.maximum(0.0, s_end * ratio) * kscale
        rot = g("rotation0") + g("rotationVariance") * sym(8)
        av = g("angularVelocity") + g("angularVelocityVariance") * sym(9)
        age = np.maximum(0.0, t_end - times)
        px, py = px + vx * age, py + vy * age
        rot = rot + av * age
        bx, by = px - vx * age, py - vy * age          # birth points (history before birth)
        for k, v in (("x", px), ("y", py), ("vx", vx), ("vy", vy), ("age", age), ("life", life), ("s0", s0),
                     ("s1", s1), ("rot", np.broadcast_to(rot, (n,)).astype(np.float64)),
                     ("av", np.broadcast_to(av, (n,)).astype(np.float64)), ("pid", ids), ("seed", np.full(n, seed, np.uint32)),
                     ("hx", np.repeat(bx[:, None], self.hist, 1)), ("hy", np.repeat(by[:, None], self.hist, 1))):
            st[k] = np.concatenate([st[k], v])

    # ------------------------------------------------------------ step
    def parent_doc(self, t: float) -> np.ndarray:
        """Effective emitter parent space -> its composition or symbol canvas."""
        _, parent, canvas = self._transforms(t)
        return np.linalg.solve(canvas, parent)

    def _accel(self, st: dict, t: float, PD=None) -> tuple[np.ndarray, np.ndarray]:
        P = self.P
        c = self.ctx_at(t)
        x, y, vx, vy = st["x"], st["y"], st["vx"], st["vy"]
        ax = np.full_like(x, P.get("gravityX", c))
        ay = np.full_like(x, P.get("gravityY", c))
        turb = P.get("turbulence", c)
        if turb:
            sc = max(1e-3, P.get("turbulenceScale", c) or 100.0)
            seed = self.seed_at(c)
            ax = ax + fbm(seed + 11, x / sc, y / sc, t * 0.4) * turb * 3.0
            ay = ay + fbm(seed + 23, x / sc, y / sc, t * 0.4) * turb * 3.0
        if self.fields:
            if PD is not None:
                X, Y = PD[0, 0] * x + PD[0, 1] * y + PD[0, 2], PD[1, 0] * x + PD[1, 1] * y + PD[1, 2]
                VX, VY = PD[0, 0] * vx + PD[0, 1] * vy, PD[1, 0] * vx + PD[1, 1] * vy
                next_parent = self.parent_doc(t + DT)
                VX = VX + ((next_parent[0, 0]-PD[0, 0])*x + (next_parent[0, 1]-PD[0, 1])*y
                           + next_parent[0, 2]-PD[0, 2]) / DT
                VY = VY + ((next_parent[1, 0]-PD[1, 0])*x + (next_parent[1, 1]-PD[1, 1])*y
                           + next_parent[1, 2]-PD[1, 2]) / DT
            else:
                X, Y, VX, VY = x, y, vx, vy
            FX, FY = np.zeros_like(x), np.zeros_like(x)
            names = P.get("forceFields", c)
            selected = set(names.split()) if names else None
            for f in self.fields:
                if selected is not None and f.get("id") not in selected:
                    continue
                fx, fy = field_accel(self.rc, f, c.comp_t, X, Y, VX, VY, self.ppm, affects="particles")
                FX, FY = FX + fx, FY + fy
            if PD is not None:
                Li = np.linalg.inv(PD[:2, :2])
                FX, FY = Li[0, 0] * FX + Li[0, 1] * FY, Li[1, 0] * FX + Li[1, 1] * FY
            ax, ay = ax + FX, ay + FY
        return ax, ay

    def _step(self, st: dict, i: int) -> None:
        ts = self.t0 + i * DT
        te = ts + DT
        P = self.P
        # advance existing particles
        if len(st["x"]):
            c = self.ctx_at(ts)
            collide = P.get("collide", c)
            PD = self.parent_doc(ts) if (self.fields or collide) else None
            ax, ay = self._accel(st, ts, PD)
            damp = math.exp(-max(0.0, P.get("drag", c)) * DT)
            vx = (st["vx"] + ax * DT) * damp
            vy = (st["vy"] + ay * DT) * damp
            x = st["x"] + vx * DT
            y = st["y"] + vy * DT
            if collide:
                from .particle_collisions import collide as collide_particles
                x, y, vx, vy = collide_particles(self, st["x"], st["y"], x, y, vx, vy,
                                                  P.get("bounce", c), ts, te)
            st.update(x=x, y=y, vx=vx, vy=vy, age=st["age"] + DT, rot=st["rot"] + st["av"] * DT)
            if self.hist:
                st["hx"] = np.concatenate([x[:, None], st["hx"][:, :-1]], 1)
                st["hy"] = np.concatenate([y[:, None], st["hy"][:, :-1]], 1)
            keep = st["age"] < st["life"]
            if not keep.all():
                for k in ("x", "y", "vx", "vy", "age", "life", "s0", "s1", "rot", "av", "pid", "seed", "hx", "hy"):
                    st[k] = st[k][keep]
        # emit
        if self.end is not None and ts >= self.end:
            return
        c = self.ctx_at(ts)
        rate = max(0.0, P.get("rate", c))
        times = []
        if rate > 0:
            acc = st["acc"] + rate * DT
            k = int(math.floor(acc + 1e-9))
            st["acc"] = acc - k
            if k:
                times.append(ts + DT * (np.arange(k) + 1.0) / (k + 1))
        for bt, cnt in self.bursts:
            if ts - 1e-9 <= bt < te - 1e-9 and bt >= self.t0 - 1e-9:
                times.append(np.full(cnt, bt))
        if times:
            self._spawn(st, np.sort(np.concatenate(times), kind="stable"), te)

    # ------------------------------------------------------------ query
    def state_at(self, t: float) -> tuple[dict, float]:
        if t < self.t0:
            return self._init(), 0.0
        if self._querying:
            name = "/".join((*self.ctx_base.scope.path, self.el.get("id", "particleEmitter")))
            raise SceneError(f"Cyclic particle collision/matte dependency involving {name!r}")
        n = int(math.floor((t - self.t0) / DT + 1e-7))
        frac = t - (self.t0 + n * DT)
        if n in self.states:
            self.states.move_to_end(n)
            return self.states[n], frac
        self._querying = True
        try:
            st = self.sim.at(n)
        except Exception:
            # Seekable mutates its working state while advancing. Never retain
            # an interrupted step as if it were a completed checkpoint.
            self.sim = Seekable(self._init, self._step, every=CHECKPOINT_EVERY)
            self.colliders.clear()
            self.collision_transforms.clear()
            self.states.clear()
            raise
        finally:
            self._querying = False
        # Particle steps replace arrays rather than mutate them in place. A
        # shallow snapshot preserves a queried state while Seekable advances.
        # Alternating collider endpoints then avoid replaying a whole checkpoint.
        self.states[n] = st.copy()
        while len(self.states) > 2:
            self.states.popitem(last=False)
        return self.states[n], frac


def get_emitter(rc: RenderContext, el, ctx: Ctx) -> Emitter:
    ctx = rc.node_ctx(el, ctx)
    # Reference traversal may carry the same repeat binding more than once.
    # Only the last value is observable (Ctx.var); duplicate entries must not
    # create a different simulation or evade the recursive-query guard.
    bindings = tuple(sorted(dict(ctx.vars).items()))
    key = ("particles", el, Ctx(0, 0, scope=ctx.scope, vars=bindings))
    histories = rc.cache.setdefault(key, OrderedDict())
    params = Params(rc, el, ctx)
    preroll = max(0., params.get("preroll"))
    trail = max(0., params.get("trail") or 0.)
    history = int(math.ceil(trail / DT)) + 2 if trail > 0 else 0
    branch = (rc.ev.history_origin(el, ctx), ctx.node_start, ctx.node_end, preroll, history)
    em = histories.get(branch)
    if em is None:
        em = Emitter(rc, el, ctx, params)
        histories[branch] = em
        while len(histories) > 8:
            histories.popitem(last=False)
    histories.move_to_end(branch)
    return em


def particles_at(rc: RenderContext, el, ctx: Ctx) -> dict:
    """Live particles at ctx.t in the emitter's parent space (arrays x, y, vx, vy, age, life, size, pid...)."""
    em = get_emitter(rc, el, ctx)
    st, frac = em.state_at(ctx.t)
    out = {k: st[k] for k in ("x", "y", "vx", "vy", "age", "life", "s0", "s1", "rot", "av", "pid", "seed", "hx", "hy")}
    out["frac"] = np.full(len(out["x"]), frac)
    if frac > 0 and len(out["x"]):
        out["x"] = out["x"] + out["vx"] * frac
        out["y"] = out["y"] + out["vy"] * frac
        out["age"] = out["age"] + frac
        out["rot"] = out["rot"] + out["av"] * frac
        keep = out["age"] < out["life"]
        out = {k: v[keep] for k, v in out.items()}
    return out


# ====================================================================== drawing
def _trail_points(x, y, hx, hy, frac, trail):
    """Head plus recorded positions back to `trail` seconds: ([(x, y)], [seconds back])."""
    pts, taus = [(x, y)], [0.0]
    for k in range(len(hx)):
        tau = frac + k * DT
        if tau > trail:
            px_, py_ = pts[-1]
            t0 = taus[-1]
            u = (trail - t0) / max(tau - t0, 1e-12)
            pts.append((px_ + (hx[k] - px_) * u, py_ + (hy[k] - py_) * u))
            taus.append(trail)
            break
        pts.append((float(hx[k]), float(hy[k])))
        taus.append(tau)
    return pts, taus


def _curve_table(name: str | None) -> np.ndarray:
    f = curves.get(name or "linear")
    return np.array([f(u) for u in np.linspace(0, 1, 257)], np.float64)


def _particle_random(p, channel):
    result = np.empty(len(p["pid"]))
    for seed in np.unique(p["seed"]):
        selected = p["seed"] == seed
        result[selected] = hash01(int(seed), p["pid"][selected], channel)
    return result


def _sprite_surface(rc: RenderContext, aid: str, ctx: Ctx, src_t=0.):
    cache = rc.cache.setdefault("particle-sprites", OrderedDict())
    key = (aid, ctx, src_t)
    hit = cache.pop(key, None)
    if hit is not None:
        cache[key] = hit
        return hit
    asset = rc.doc.ids.get(aid)
    fn = ASSETS.get(ln(asset)) if asset is not None else None
    if fn is None:
        warn_once("particles", f"sprite:{aid}", "sprite asset not found; drawing discs")
        return False
    aw, ah = rc.asset_size(asset, ctx)
    buf = fn(rc, asset, np.eye(3), ctx, src_t=src_t)
    if buf is None or aw <= 0 or ah <= 0:
        return False
    buf = buf.crop_to((0, 0, int(math.ceil(aw)), int(math.ceil(ah)))).expand_to((0, 0, int(math.ceil(aw)), int(math.ceil(ah))))
    from ..raster import working_to_rgb8
    rgb = working_to_rgb8(buf.px, rc.linear).astype(np.float32)
    a = np.clip(buf.px[..., 3], 0, 1)
    h, w = a.shape
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    stride = surf.get_stride()
    arr = np.frombuffer(surf.get_data(), np.uint8).reshape(h, stride // 4, 4)
    arr[:, :w, 0] = (rgb[..., 2] * a + 0.5).astype(np.uint8)
    arr[:, :w, 1] = (rgb[..., 1] * a + 0.5).astype(np.uint8)
    arr[:, :w, 2] = (rgb[..., 0] * a + 0.5).astype(np.uint8)
    arr[:, :w, 3] = (a * 255 + 0.5).astype(np.uint8)
    surf.mark_dirty()
    planes = []
    for ch in (None, 0, 1, 2):
        pl = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        pa = np.frombuffer(pl.get_data(), np.uint8).reshape(h, pl.get_stride() // 4, 4)
        if ch is None:
            pa[:, :w, 3] = arr[:, :w, 3]
        else:
            pa[:, :w, 2 - ch] = arr[:, :w, 2 - ch]
        pl.mark_dirty()
        planes.append(pl)
    cache[key] = (surf, w, h, planes)
    while len(cache) > 8:
        cache.popitem(last=False)
    return cache[key]


@NODES.register("particleEmitter", level=PARTIAL,
                note="evaluated inputs, spatial paints and particle-backed collider mattes; cyclic dependencies are rejected (docs/PARTICLES.md)")
def render_particles(rc: RenderContext, el, ctx: Ctx, M, size):
    em = get_emitter(rc, el, ctx)
    P = em.P
    g = lambda name: P.get(name, ctx)  # noqa: E731
    L_now = em.L(ctx.t)
    try:
        PM = M @ np.linalg.inv(L_now)
    except np.linalg.LinAlgError:
        return None
    p = particles_at(rc, el, ctx)
    n = len(p["x"])
    if n == 0:
        return None
    u = np.clip(p["age"] / p["life"], 0, 1)
    idx = np.minimum((u * 256).astype(np.int64), 256)
    sz = p["s0"] + (p["s1"] - p["s0"]) * _curve_table(g("sizeCurve"))[idx]
    color_start = resolve_var(g("color") or "#FFFFFFFF", rc.doc.tokens)
    has_end = P.explicit("colorEnd") or "colorEnd" in P.preset_at(ctx)
    color_end = g("colorEnd") if has_end else color_start
    if color_end is not None:
        color_end = resolve_var(color_end, rc.doc.tokens)
    spatial = paint_ref(color_start) is not None or paint_ref(color_end) is not None
    ck = _curve_table(g("colorCurve"))[idx]
    palette = P.look("palette", ctx=ctx) if not P.explicit("color") else None
    palette_colors = None
    if palette:
        pal = np.array([parse_color(c) for c in palette])
        palette_colors = pal[(_particle_random(p, 20) * len(pal)).astype(np.int64) % len(pal)]
    if spatial:
        rgb, alpha = np.ones((n, 3)), np.ones(n)
    else:
        c0 = palette_colors if palette_colors is not None else np.array(parse_color(color_start, rc.doc.tokens))[None, :]
        c1 = np.array(parse_color(color_end, rc.doc.tokens))[None, :] if has_end else c0
        cc = c0 + (c1-c0)*ck[:, None]
        rgb, alpha = cc[:, :3], cc[:, 3]
    o_end = g("opacityEnd") if (P.explicit("opacityEnd") or "opacityEnd" in P.preset_at(ctx)) else 1.0
    alpha = alpha * (1 + (o_end - 1) * u)
    fade_in = P.look("fade_in", 0.0, ctx)
    if fade_in:
        alpha = alpha * np.clip(u / fade_in, 0, 1)
    if P.look("twinkle", ctx=ctx):
        ph = _particle_random(p, 21) * 2 * math.pi
        alpha = alpha * (0.35 + 0.65 * (0.5 + 0.5 * np.sin(ph + p["age"] * 14.0)))
    shape = g("shape") or "disc"
    trail = g("trail") or 0.0
    orient = g("orientToVelocity")
    sprite = _sprite_surface(rc, g("sprite"), ctx, max(0., ctx.t-em.start)) if shape == "sprite" and g("sprite") else None
    # tile bounds in frame pixels
    fx = PM[0, 0] * p["x"] + PM[0, 1] * p["y"] + PM[0, 2]
    fy = PM[1, 0] * p["x"] + PM[1, 1] * p["y"] + PM[1, 2]
    k = float(np.linalg.svd(PM[:2, :2], compute_uv=False)[0])
    reach = sz.max() * k
    if sprite:
        _, sw, sh, _ = sprite
        aspect = (sh/max(1, int(g("spriteRows") or 1))) / (sw/max(1, int(g("spriteCols") or 1)))
        reach *= max(1., math.hypot(1., aspect)/2)
    left, right, top, bottom = fx.copy(), fx.copy(), fy.copy(), fy.copy()
    if trail > 0 and p["hx"].shape[1]:
        hx = PM[0, 0]*p["hx"] + PM[0, 1]*p["hy"] + PM[0, 2]
        hy = PM[1, 0]*p["hx"] + PM[1, 1]*p["hy"] + PM[1, 2]
        left, right = np.minimum(left, hx.min(axis=1)), np.maximum(right, hx.max(axis=1))
        top, bottom = np.minimum(top, hy.min(axis=1)), np.maximum(bottom, hy.max(axis=1))
    elif shape == "streak":
        reach *= 4.5
    pad = reach + 4
    r = (int(math.floor(left.min() - pad)), int(math.floor(top.min() - pad)),
         int(math.ceil(right.max() + pad)), int(math.ceil(bottom.max() + pad)))
    r = intersect(r, rc.raster_bounds())
    if r is None:
        return None
    cv = Canvas(r)
    cv.set_matrix(PM)
    cr = cv.cr
    cr.set_antialias(cairo.ANTIALIAS_GOOD)
    visible = (right + reach >= r[0]) & (left - reach <= r[2]) & (bottom + reach >= r[1]) & (top - reach <= r[3]) & (alpha > 1e-3) & (sz > 0)
    order = np.nonzero(visible)[0]
    soft = float(P.look("soft", 0.0, ctx))
    ring = P.look("ring", ctx=ctx)
    flutter = P.look("flutter", ctx=ctx)
    X, Y, VX, VY = p["x"], p["y"], p["vx"], p["vy"]
    HX, HY, FR = p.get("hx"), p.get("hy"), p.get("frac")
    ROT = p["rot"]
    sprite_frames = _particle_random(p, 22) if shape == "sprite" else None
    for i in order:
        x, y, s, a = float(X[i]), float(Y[i]), float(sz[i]), float(alpha[i])
        cr_, cg, cb = (float(v) for v in rgb[i])
        rot = math.degrees(math.atan2(VY[i], VX[i])) if orient else float(ROT[i])
        rect = intersect((math.floor(left[i]-pad), math.floor(top[i]-pad),
                          math.ceil(right[i]+pad), math.ceil(bottom[i]+pad)), r)
        if rect is None:
            continue
        field = None
        if spatial:
            width, height = s, s
            matrix = PM @ translate(x, y) @ rotate(rot)
            if trail > 0 or shape == "streak":
                if trail > 0 and HX is not None and HX.shape[1]:
                    points, _ = _trail_points(x, y, HX[i], HY[i], float(FR[i]), trail)
                    points = np.asarray(points)
                    bx0, by0 = points.min(axis=0)
                    bx1, by1 = points.max(axis=0)
                else:
                    speed = math.hypot(VX[i], VY[i]) or 1.
                    tx, ty = x-VX[i]/speed*s*4, y-VY[i]/speed*s*4
                    bx0, bx1, by0, by1 = min(x, tx), max(x, tx), min(y, ty), max(y, ty)
                width, height = bx1-bx0+s, by1-by0+s
                matrix = PM @ translate(bx0-s/2, by0-s/2)
            elif shape == "sprite" and sprite:
                height = s*aspect
                matrix = matrix @ translate(-s/2, -height/2)
            else:
                if shape == "square" and flutter:
                    stretch = 0.25+0.75*abs(math.cos(math.radians(rot)*1.7+float(p["pid"][i] % 7)))
                    matrix = matrix @ np.diag([1., stretch, 1.])
                    height = s*.6
                matrix = matrix @ translate(-s/2, -s/2)
            start = tuple(palette_colors[i]) if palette_colors is not None else color_start
            end = color_end if has_end else start
            field = ParticlePaint(rc, ctx, rect, r, matrix, width, height, start, end, float(ck[i]), a)
            a = 1.0  # The paint carries opacity; geometry below supplies coverage.
        if shape == "streak" or trail > 0:
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(s if shape == "streak" else s * 0.7)
            a_head = a if shape == "streak" else a * 0.6
            if trail > 0 and HX is not None and HX.shape[1]:
                pts, taus = _trail_points(x, y, HX[i], HY[i], float(FR[i]), trail)
                mask = trail_mask(cr, rect, r, pts, taus, trail, cr.get_line_width())
                if field is not None:
                    field.source(cr, gain=1. if shape == "streak" else .6)
                else:
                    cr.set_source_rgba(cr_, cg, cb, a_head)
                mask_surface(cr, mask, rect, r)
            elif shape == "streak":
                vx, vy = float(VX[i]), float(VY[i])
                spd = math.hypot(vx, vy) or 1.0
                vx, vy = vx / spd * s * 4, vy / spd * s * 4
                grad = cairo.LinearGradient(x - vx, y - vy, x, y)
                grad.add_color_stop_rgba(0, cr_, cg, cb, 0.0)
                grad.add_color_stop_rgba(1, cr_, cg, cb, a_head)
                cr.move_to(x - vx, y - vy)
                cr.line_to(x, y)
                if field is not None:
                    field.stroke_mask(cr, grad)
                else:
                    cr.set_source(grad)
                    cr.stroke()
            if shape == "streak":
                continue
        if shape == "sprite" and sprite:
            surf, sw, sh, chans = sprite
            cols = max(1, int(g("spriteCols") or 1))
            rows = max(1, int(g("spriteRows") or 1))
            cw, chh = sw / cols, sh / rows
            fps = g("spriteFps") or 0.0
            nfr = cols * rows
            fr = int(p["age"][i] * fps) % nfr if fps > 0 else int(sprite_frames[i] * nfr) % nfr
            ccol, crow = fr % cols, fr // cols
            cr.save()
            cr.translate(x, y)
            cr.rotate(math.radians(rot))
            kk = s / cw
            cr.scale(kk, kk)
            cr.rectangle(-cw / 2, -chh / 2, cw, chh)
            cr.clip()
            ox, oy = -cw / 2 - ccol * cw, -chh / 2 - crow * chh
            if field is not None:
                field.sprite(cr, surf, ox, oy)
            elif abs(cr_ - 1) + abs(cg - 1) + abs(cb - 1) < 1e-6:
                cr.set_source_surface(surf, ox, oy)
                cr.paint_with_alpha(a)
            else:
                # tint: sum of the per-channel sprite planes scaled by the colour (+ the alpha plane)
                cr.push_group()
                cr.set_operator(cairo.OPERATOR_ADD)
                for plane, k_ in zip(chans, (1.0, cr_, cg, cb)):
                    if k_ > 1e-6:
                        cr.set_source_surface(plane, ox, oy)
                        cr.paint_with_alpha(min(1.0, k_))
                cr.pop_group_to_source()
                cr.paint_with_alpha(a)
            cr.restore()
            continue
        if shape == "square":
            cr.save()
            cr.translate(x, y)
            cr.rotate(math.radians(rot))
            if flutter:
                cr.scale(1.0, 0.25 + 0.75 * abs(math.cos(math.radians(rot) * 1.7 + float(p["pid"][i] % 7))))
            cr.rectangle(-s / 2, -s / 2, s, s * (0.6 if flutter else 1.0))
            if field is not None:
                field.source(cr)
            else:
                cr.set_source_rgba(cr_, cg, cb, a)
            cr.fill()
            cr.restore()
            continue
        rad = s / 2
        if ring:
            cr.set_line_width(max(0.5, rad * 0.12))
            cr.arc(x, y, rad, 0, 2 * math.pi)
            if field is not None:
                field.source(cr, gain=2.)
            else:
                cr.set_source_rgba(cr_, cg, cb, min(1.0, a * 2.0))
            cr.stroke()
            cr.arc(x - rad * 0.35, y - rad * 0.35, rad * 0.18, 0, 2 * math.pi)
            if field is not None:
                field.source(cr, gain=2., white=True)
            else:
                cr.set_source_rgba(1, 1, 1, min(1.0, a * 2.0))
            cr.fill()
            continue
        if soft > 0:
            gradient = cairo.RadialGradient(x, y, 0, x, y, rad)
            gradient.add_color_stop_rgba(0, cr_, cg, cb, a)
            gradient.add_color_stop_rgba(max(0.0, 1 - soft), cr_, cg, cb, a)
            gradient.add_color_stop_rgba(1, cr_, cg, cb, 0.0)
            if field is not None:
                field.source(cr)
                cr.save()
                cr.arc(x, y, rad, 0, 2*math.pi)
                cr.clip()
                cr.mask(gradient)
                cr.restore()
                continue
            cr.set_source(gradient)
        elif field is not None:
            field.source(cr)
        else:
            cr.set_source_rgba(cr_, cg, cb, a)
        cr.arc(x, y, rad, 0, 2 * math.pi)
        cr.fill()
    return cv.to_buf(rc.linear)
