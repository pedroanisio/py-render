"""particleEmitter: deterministic, time-seekable particle systems drawn with cairo.

Simulation
  * Fixed step (1/60 s) from the emitter's start minus @preroll. States are checkpointed in
    rc.cache (every 0.5 s) and the last computed state is kept, so sequential frames cost one
    step each and seeking anywhere gives identical results whatever was rendered before.
    Between steps positions are extrapolated linearly to the exact frame time.
  * Every per-particle random value is hash(seed, particle id, channel) with the seed from
    rc.ev.seed_for(emitter): a pure function of project seed, emitter id/@seed.
Spaces
  * Particles are born in emitter-local space (emitter shape, @direction rotated/scaled with the
    emitter) and then live in the emitter's *parent* space: moving the emitter leaves existing
    particles behind, moving the parent moves them all. Gravity, turbulence, force fields and
    frame-bound collisions act in parent space (document pixels, +y down); for a top-level
    emitter that is composition space.
  * Emitter shapes are centred on the emitter origin (x, y): rect/ellipse span
    emitterWidth x emitterHeight around it, line runs horizontally through it (emitterWidth),
    point is the origin, path uses @emitterPath coordinates as authored (local px), asset-alpha
    samples opaque pixels of @emitterAsset (centred).
Units
  * rate particles/s; lifetime, variances: seconds / same units as the base (uniform +/-);
    speed px/s; direction deg (0 = +x, -90 = up); spread = full cone angle in degrees;
    gravityX/Y px/s^2; drag 1/s (v *= exp(-drag dt)); turbulence ~ RMS acceleration px/s^2 of a
    seeded value-noise field of period @turbulenceScale px; size = diameter px (disc/square/sprite
    width, streak thickness); angularVelocity deg/s; trail seconds of motion drawn behind.
  * Force fields (physics/forceField, listed in @forceFields, else every field with affects
    all|particles) act in px units: directional/wind forceX/Y px/s^2 (wind adds seeded gusts),
    radial/vortex strength px/s^2 around x,y with radius/falloff, turbulence strength px/s^2 with
    period scale * pixelsPerMeter px, drag strength 1/s, attractor-path strength px/s^2.
  * collide: floor and side walls of the frame in parent space, with @bounce (PARTIAL).
Presets supply defaults that explicit attributes override (see PRESETS below). Internal
preset-only look parameters: soft (edge softness 0..1 of discs), fade_in (fraction of life),
palette (per-particle colours when @color is not given), flutter (confetti tumbling), ring
(bubbles), twinkle (glitter).
"""
from __future__ import annotations

import math
from dataclasses import replace

import cairo
import numpy as np

from .. import curves, paint
from ..compositor import RenderContext, rotate, translate
from ..document import ln
from ..evaluator import ANIM_TAGS, Ctx
from ..physics import Seekable, fbm, hash01
from ..raster import Canvas, intersect
from ..registry import ASSETS, FEATURES, NODES, PARTIAL, warn_once
from ..values import parse_color

FEATURES.declare("particles:collide", PARTIAL, "frame floor and side walls in the emitter's parent space")
FEATURES.declare("particles:emitterShape:asset-alpha", PARTIAL, "samples the asset rendered at t=0")

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
TRANSFORM = {"x", "y", "rotation", "scaleX", "scaleY", "anchorX", "anchorY", "skewX", "skewY", "scale", "position"}

for _p in PRESETS:
    FEATURES.declare(f"particles:preset:{_p}", "full")


# ====================================================================== parameters
class Params:
    """Parameter access with preset defaults: explicit attribute/animation > preset > schema default."""

    def __init__(self, rc: RenderContext, el, ctx: Ctx):
        self.rc, self.el = rc, el
        self.preset = PRESETS.get(el.get("preset") or "", {})
        if el.get("preset") and not self.preset:
            warn_once("particles", f"preset:{el.get('preset')}", "unknown preset")
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
        if name not in self.animated and name in self._const:
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
        elif name in self.preset:
            v = self.preset[name]
        elif name in BOOL:
            v = ev.bool(self.el, name, ctx, False)
        elif name in NUM:
            v = ev.num(self.el, name, ctx, 0.0)
        else:
            v = ev.str(self.el, name, ctx)
        if name not in self.animated:
            self._const[name] = v
        return v

    def look(self, key, default=None):
        return self.preset.get(key, default)


# ====================================================================== simulation
def _parent_box(rc: RenderContext, el, ctx: Ctx) -> tuple[float, float]:
    parent = el.getparent()
    box = (float(rc.doc.width), float(rc.doc.height))
    if parent is None:
        return box
    tag = ln(parent)
    if tag == "symbol" and parent.get("width"):
        return float(parent.get("width")), float(parent.get("height"))
    if tag in ("group", "sequence", "repeat", "instance"):
        return rc.node_size(parent, rc.node_ctx(parent, ctx), box)
    return box


class Emitter:
    def __init__(self, rc: RenderContext, el, ctx: Ctx):
        self.rc, self.el = rc, el
        self.P = Params(rc, el, ctx)
        self.seed = rc.ev.seed_for(el, "particles")
        s, e = rc.doc.window(el)
        self.start, self.end = s, e
        self.preroll = max(0.0, self.P.get("preroll"))
        self.t0 = s - self.preroll
        self.ctx_base = ctx
        self.box = _parent_box(rc, el, ctx)
        self.static_L = not (self.P.animated & TRANSFORM) and el.get("parent") is None
        self._L = None
        self.bursts = []
        for b in el:
            if ln(b) == "burst":
                bt = float(b.get("time", 0))
                cnt = int(float(b.get("count", 0)))
                rep = int(float(b.get("repeat", 0) or 0))
                iv = float(b.get("interval", 1) or 1)
                self.bursts += [(bt + k * iv, cnt) for k in range(rep + 1)]
        self.fields = self._fields()
        self.emit_pts = None
        self.path_sampler = None
        self.sim = Seekable(self._init, self._step, every=CHECKPOINT_EVERY)

    # ------------------------------------------------------------ helpers
    def ctx_at(self, t: float) -> Ctx:
        c = self.ctx_base
        return replace(c, t=t, comp_t=c.comp_t + (t - c.t))

    def L(self, t: float) -> np.ndarray:
        if self.static_L and self._L is not None:
            return self._L
        rc = self.rc
        c = rc.node_ctx(self.el, self.ctx_at(t))
        size = rc.node_size(self.el, c, self.box)
        L = rc.local_matrix(self.el, c, self.box, size)
        if self.static_L:
            self._L = L
        return L

    def _fields(self):
        ph = self.rc.doc.section("physics")
        if ph is None:
            return []
        ids = self.P.get("forceFields")
        allf = [f for f in ph if ln(f) == "forceField"]
        if ids:
            want = set(ids.split())
            out = [f for f in allf if f.get("id") in want and f.get("affects", "all") != "bodies"]
            for m in want - {f.get("id") for f in out}:
                warn_once("particles", f"forceField:{m}", "force field not found (or affects bodies only)")
            return out
        return [f for f in allf if f.get("affects", "all") in ("all", "particles")]

    def _init(self) -> dict:
        z = np.zeros(0)
        return dict(x=z, y=z, vx=z, vy=z, age=z, life=z, s0=z, s1=z, rot=z, av=z,
                    pid=np.zeros(0, np.int64), acc=0.0, next_id=0)

    # ------------------------------------------------------------ emission
    def _positions(self, ids: np.ndarray, t: float) -> tuple[np.ndarray, np.ndarray]:
        P = self.P
        c = self.ctx_at(t)
        shape = P.get("emitterShape", c) or "rect"
        w, h = P.get("emitterWidth", c), P.get("emitterHeight", c)
        r1, r2 = hash01(self.seed, ids, 1), hash01(self.seed, ids, 2)
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
            if self.path_sampler is None:
                from ..geometry import PathSampler
                self.path_sampler = PathSampler(P.get("emitterPath", c) or "")
            pts = [self.path_sampler.at(float(u)) for u in r1]
            return np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
        if shape == "asset-alpha":
            if self.emit_pts is None:
                self.emit_pts = self._asset_points(P.get("emitterAsset", c))
            pts, aw, ah = self.emit_pts
            if len(pts) == 0:
                return np.zeros(len(ids)), np.zeros(len(ids))
            k = np.minimum((r1 * len(pts)).astype(np.int64), len(pts) - 1)
            return pts[k, 0] + (r2 - 0.5) - aw / 2, pts[k, 1] + (hash01(self.seed, ids, 3) - 0.5) - ah / 2
        warn_once("particles", f"emitterShape:{shape}", "unknown emitter shape; using point")
        return np.zeros(len(ids)), np.zeros(len(ids))

    def _asset_points(self, aid):
        rc = self.rc
        asset = rc.doc.ids.get(aid) if aid else None
        fn = ASSETS.get(ln(asset)) if asset is not None else None
        if fn is None:
            warn_once("particles", f"emitterAsset:{aid}", "emitter asset not found")
            return np.zeros((0, 2)), 0.0, 0.0
        aw, ah = rc.asset_size(asset, self.ctx_base)
        buf = fn(rc, asset, np.eye(3), self.ctx_at(0.0))
        if buf is None:
            return np.zeros((0, 2)), aw, ah
        ys, xs = np.nonzero(buf.px[..., 3] > 0.5)
        return np.stack([xs + buf.x0 + 0.5, ys + buf.y0 + 0.5], 1).astype(np.float64), aw, ah

    def _spawn(self, st: dict, times: np.ndarray, t_end: float) -> None:
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
        H = lambda ch: hash01(self.seed, ids, ch)  # noqa: E731
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
        spd = np.maximum(0.0, g("speed") + g("speedVariance") * sym(5)) * kscale
        vx, vy = wx / norm * spd, wy / norm * spd
        life = np.maximum(1e-3, g("lifetime") + g("lifetimeVariance") * sym(6))
        size = g("size")
        s_end = g("sizeEnd") if (P.explicit("sizeEnd") or "sizeEnd" in P.preset) else size
        sv = g("sizeVariance") * sym(7)
        s0 = np.maximum(0.0, size + sv) * kscale
        ratio = np.where(size > 0, (size + sv) / size if size else 1.0, 1.0)
        s1 = np.maximum(0.0, s_end * ratio) * kscale
        rot = g("rotation0") + g("rotationVariance") * sym(8)
        av = g("angularVelocity") + g("angularVelocityVariance") * sym(9)
        age = np.maximum(0.0, t_end - times)
        px, py = px + vx * age, py + vy * age
        rot = rot + av * age
        for k, v in (("x", px), ("y", py), ("vx", vx), ("vy", vy), ("age", age), ("life", life), ("s0", s0),
                     ("s1", s1), ("rot", np.broadcast_to(rot, (n,)).astype(np.float64)),
                     ("av", np.broadcast_to(av, (n,)).astype(np.float64)), ("pid", ids)):
            st[k] = np.concatenate([st[k], v])

    # ------------------------------------------------------------ step
    def _accel(self, st: dict, t: float) -> tuple[np.ndarray, np.ndarray]:
        P = self.P
        c = self.ctx_at(t)
        x, y, vx, vy = st["x"], st["y"], st["vx"], st["vy"]
        ax = np.full_like(x, P.get("gravityX", c))
        ay = np.full_like(x, P.get("gravityY", c))
        turb = P.get("turbulence", c)
        if turb:
            sc = max(1e-3, P.get("turbulenceScale", c) or 100.0)
            ax = ax + fbm(self.seed + 11, x / sc, y / sc, t * 0.4) * turb * 3.0
            ay = ay + fbm(self.seed + 23, x / sc, y / sc, t * 0.4) * turb * 3.0
        for f in self.fields:
            fx, fy = self._field(f, x, y, vx, vy, t)
            ax, ay = ax + fx, ay + fy
        return ax, ay

    def _field(self, f, x, y, vx, vy, t):
        ev = self.rc.ev
        c = self.ctx_at(t)
        zero = np.zeros_like(x)
        s = ev.num(f, "start", c, 0.0)
        e = ev.num(f, "end", c, 0.0) if f.get("end") is not None else None
        if t < s or (e is not None and t >= e):
            return zero, zero
        typ = f.get("type")
        strength = ev.num(f, "strength", c, 0.0)
        cx, cy = ev.num(f, "x", c, 0.0), ev.num(f, "y", c, 0.0)
        radius = ev.num(f, "radius", c, 0.0)
        falloff = ev.num(f, "falloff", c, 0.0)
        dx, dy = x - cx, y - cy
        d = np.hypot(dx, dy)
        dsafe = np.where(d > 1e-9, d, 1.0)
        if radius > 0:
            fall = np.clip(1 - d / radius, 0, 1) ** (falloff if falloff > 0 else 0)
            fall = np.where(d < radius, fall, 0.0)
        else:
            fall = 1.0 / (1.0 + d / 100.0) ** falloff if falloff > 0 else np.ones_like(d)
        if typ in ("directional", "wind"):
            fx, fy = ev.num(f, "forceX", c, 0.0), ev.num(f, "forceY", c, 0.0)
            if typ == "wind":
                gust = 1.0 + 0.25 * fbm(ev.seed_for(f, "gust"), t * 0.7, y / 400.0, 0.0)
                return fx * gust, fy * gust
            return zero + fx, zero + fy
        if typ == "radial":
            return dx / dsafe * strength * fall, dy / dsafe * strength * fall
        if typ == "vortex":
            return -dy / dsafe * strength * fall, dx / dsafe * strength * fall
        if typ == "turbulence":
            ppm = float((self.rc.doc.section("physics").get("pixelsPerMeter", 100)))
            sc = max(1e-6, ev.num(f, "scale", c, 1.0)) * ppm
            seed = ev.seed_for(f, "turb")
            return (fbm(seed, x / sc, y / sc, t * 0.5) * strength * 3.0,
                    fbm(seed + 1, x / sc, y / sc, t * 0.5) * strength * 3.0)
        if typ == "drag":
            return -vx * strength, -vy * strength
        if typ == "attractor-path" and f.get("path"):
            from ..geometry import PathSampler
            key = ("ff-path", f)
            ps = self.rc.cache.get(key) or PathSampler(f.get("path"))
            self.rc.cache[key] = ps
            pts = np.array(ps.pts[::max(1, len(ps.pts) // 256)])
            ddx = pts[None, :, 0] - x[:, None]
            ddy = pts[None, :, 1] - y[:, None]
            k = np.argmin(ddx * ddx + ddy * ddy, 1)
            gx, gy = ddx[np.arange(len(x)), k], ddy[np.arange(len(x)), k]
            n = np.hypot(gx, gy)
            n = np.where(n > 1e-9, n, 1.0)
            return gx / n * strength, gy / n * strength
        return zero, zero

    def _step(self, st: dict, i: int) -> None:
        ts = self.t0 + i * DT
        te = ts + DT
        P = self.P
        # advance existing particles
        if len(st["x"]):
            ax, ay = self._accel(st, ts)
            c = self.ctx_at(ts)
            damp = math.exp(-max(0.0, P.get("drag", c)) * DT)
            vx = (st["vx"] + ax * DT) * damp
            vy = (st["vy"] + ay * DT) * damp
            x = st["x"] + vx * DT
            y = st["y"] + vy * DT
            if P.get("collide", c):
                x, y, vx, vy = self._collide(x, y, vx, vy, P.get("bounce", c))
            st.update(x=x, y=y, vx=vx, vy=vy, age=st["age"] + DT, rot=st["rot"] + st["av"] * DT)
            keep = st["age"] < st["life"]
            if not keep.all():
                for k in ("x", "y", "vx", "vy", "age", "life", "s0", "s1", "rot", "av", "pid"):
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

    def _collide(self, x, y, vx, vy, bounce):
        W, H = float(self.rc.doc.width), float(self.rc.doc.height)
        hit = y > H
        y = np.where(hit, H - (y - H) * bounce, y)
        vy = np.where(hit, -np.abs(vy) * bounce, vy)
        vx = np.where(hit, vx * 0.8, vx)
        for side, sgn in ((x < 0, 1.0), (x > W, -1.0)):
            x = np.where(side, np.where(sgn > 0, -x * bounce, W - (x - W) * bounce), x)
            vx = np.where(side, sgn * np.abs(vx) * bounce, vx)
        return x, y, vx, vy

    # ------------------------------------------------------------ query
    def state_at(self, t: float) -> tuple[dict, float]:
        if t < self.t0:
            return self._init(), 0.0
        n = int(math.floor((t - self.t0) / DT + 1e-7))
        return self.sim.at(n), t - (self.t0 + n * DT)


def get_emitter(rc: RenderContext, el, ctx: Ctx) -> Emitter:
    key = ("particles", el, ctx.scope.path)
    em = rc.cache.get(key)
    if em is None:
        em = Emitter(rc, el, ctx)
        rc.cache[key] = em
    return em


def particles_at(rc: RenderContext, el, ctx: Ctx) -> dict:
    """Live particles at ctx.t in the emitter's parent space (arrays x, y, vx, vy, age, life, size, pid...)."""
    em = get_emitter(rc, el, ctx)
    st, frac = em.state_at(ctx.t)
    out = {k: st[k] for k in ("x", "y", "vx", "vy", "age", "life", "s0", "s1", "rot", "av", "pid")}
    if frac > 0 and len(out["x"]):
        out["x"] = out["x"] + out["vx"] * frac
        out["y"] = out["y"] + out["vy"] * frac
        out["age"] = out["age"] + frac
        out["rot"] = out["rot"] + out["av"] * frac
        keep = out["age"] < out["life"]
        out = {k: v[keep] for k, v in out.items()}
    return out


# ====================================================================== drawing
def _curve_table(name: str | None) -> np.ndarray:
    f = curves.get(name or "linear")
    return np.array([f(u) for u in np.linspace(0, 1, 257)], np.float64)


def _sprite_surface(rc: RenderContext, aid: str, ctx: Ctx):
    key = ("particle-sprite", aid)
    hit = rc.cache.get(key)
    if hit is not None:
        return hit
    asset = rc.doc.ids.get(aid)
    fn = ASSETS.get(ln(asset)) if asset is not None else None
    if fn is None:
        warn_once("particles", f"sprite:{aid}", "sprite asset not found; drawing discs")
        rc.cache[key] = False
        return False
    aw, ah = rc.asset_size(asset, ctx)
    buf = fn(rc, asset, np.eye(3), ctx)
    if buf is None or aw <= 0 or ah <= 0:
        rc.cache[key] = False
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
    rc.cache[key] = (surf, w, h)
    return rc.cache[key]


@NODES.register("particleEmitter", level=PARTIAL,
                note="all attributes and presets; collide is frame floor/walls only; sprites are not tinted; "
                     "blend/effects by the compositor")
def render_particles(rc: RenderContext, el, ctx: Ctx, M, size):
    em = get_emitter(rc, el, ctx)
    P = em.P
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
    sz = p["s0"] + (p["s1"] - p["s0"]) * _curve_table(P.get("sizeCurve"))[idx]
    col0 = paint.paint_color_estimate(rc, P.get("color") or "#FFFFFFFF", ctx)
    col1 = paint.paint_color_estimate(rc, P.get("colorEnd"), ctx) if (P.explicit("colorEnd") or "colorEnd" in P.preset) else col0
    ck = _curve_table(P.get("colorCurve"))[idx]
    palette = P.look("palette") if not P.explicit("color") else None
    if palette:
        pal = np.array([parse_color(c)[:3] for c in palette])
        base = pal[(hash01(em.seed, p["pid"], 20) * len(pal)).astype(np.int64) % len(pal)]
        rgb = base
        alpha = np.full(n, col0[3] if P.explicit("color") else 1.0)
    else:
        c0, c1 = np.array(col0), np.array(col1)
        cc = c0[None, :] + (c1 - c0)[None, :] * ck[:, None]
        rgb, alpha = cc[:, :3], cc[:, 3]
    o_end = P.get("opacityEnd") if (P.explicit("opacityEnd") or "opacityEnd" in P.preset) else 1.0
    alpha = alpha * (1 + (o_end - 1) * u)
    fade_in = P.look("fade_in", 0.0)
    if fade_in:
        alpha = alpha * np.clip(u / fade_in, 0, 1)
    if P.look("twinkle"):
        ph = hash01(em.seed, p["pid"], 21) * 2 * math.pi
        alpha = alpha * (0.35 + 0.65 * (0.5 + 0.5 * np.sin(ph + p["age"] * 14.0)))
    shape = P.get("shape") or "disc"
    trail = P.get("trail") or 0.0
    orient = P.get("orientToVelocity")
    # tile bounds in frame pixels
    fx = PM[0, 0] * p["x"] + PM[0, 1] * p["y"] + PM[0, 2]
    fy = PM[1, 0] * p["x"] + PM[1, 1] * p["y"] + PM[1, 2]
    k = math.sqrt(abs(float(np.linalg.det(PM[:2, :2])))) or 1.0
    reach = sz.max() * k
    if shape == "streak" or trail > 0:
        reach += float(np.max(np.hypot(p["vx"], p["vy"]))) * max(trail, 0.02) * k
    pad = reach + 4
    r = (int(math.floor(fx.min() - pad)), int(math.floor(fy.min() - pad)),
         int(math.ceil(fx.max() + pad)), int(math.ceil(fy.max() + pad)))
    r = intersect(r, (-64, -64, rc.width + 64, rc.height + 64))
    if r is None:
        return None
    cv = Canvas(r)
    cv.set_matrix(PM)
    cr = cv.cr
    cr.set_antialias(cairo.ANTIALIAS_GOOD)
    visible = (fx + reach >= r[0]) & (fx - reach <= r[2]) & (fy + reach >= r[1]) & (fy - reach <= r[3]) & (alpha > 1e-3) & (sz > 0)
    order = np.nonzero(visible)[0]
    sprite = _sprite_surface(rc, P.get("sprite"), ctx) if shape == "sprite" and P.get("sprite") else None
    soft = float(P.look("soft", 0.0))
    ring = P.look("ring")
    flutter = P.look("flutter")
    X, Y, VX, VY = p["x"], p["y"], p["vx"], p["vy"]
    ROT = p["rot"]
    for i in order:
        x, y, s, a = float(X[i]), float(Y[i]), float(sz[i]), float(alpha[i])
        cr_, cg, cb = (float(v) for v in rgb[i])
        rot = math.degrees(math.atan2(VY[i], VX[i])) if orient else float(ROT[i])
        if (trail > 0 and shape != "streak") or shape == "streak":
            ln_t = trail if trail > 0 else 0.0
            vx, vy = float(VX[i]), float(VY[i])
            if ln_t <= 0:
                spd = math.hypot(vx, vy) or 1.0
                vx, vy, ln_t = vx / spd * s * 4, vy / spd * s * 4, 1.0
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(s if shape == "streak" else s * 0.7)
            grad = cairo.LinearGradient(x - vx * ln_t, y - vy * ln_t, x, y)
            grad.add_color_stop_rgba(0, cr_, cg, cb, 0.0)
            grad.add_color_stop_rgba(1, cr_, cg, cb, a if shape == "streak" else a * 0.6)
            cr.set_source(grad)
            cr.move_to(x - vx * ln_t, y - vy * ln_t)
            cr.line_to(x, y)
            cr.stroke()
            if shape == "streak":
                continue
        if shape == "sprite" and sprite:
            surf, sw, sh = sprite
            cols = max(1, int(P.get("spriteCols") or 1))
            rows = max(1, int(P.get("spriteRows") or 1))
            cw, chh = sw / cols, sh / rows
            fps = P.get("spriteFps") or 0.0
            nfr = cols * rows
            fr = int(p["age"][i] * fps) % nfr if fps > 0 else int(hash01(em.seed, p["pid"][i:i + 1], 22)[0] * nfr) % nfr
            ccol, crow = fr % cols, fr // cols
            cr.save()
            cr.translate(x, y)
            cr.rotate(math.radians(rot))
            kk = s / cw
            cr.scale(kk, kk)
            cr.rectangle(-cw / 2, -chh / 2, cw, chh)
            cr.clip()
            cr.set_source_surface(surf, -cw / 2 - ccol * cw, -chh / 2 - crow * chh)
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
            cr.set_source_rgba(cr_, cg, cb, a)
            cr.fill()
            cr.restore()
            continue
        rad = s / 2
        if ring:
            cr.set_line_width(max(0.5, rad * 0.12))
            cr.arc(x, y, rad, 0, 2 * math.pi)
            cr.set_source_rgba(cr_, cg, cb, min(1.0, a * 2.0))
            cr.stroke()
            cr.arc(x - rad * 0.35, y - rad * 0.35, rad * 0.18, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, min(1.0, a * 2.0))
            cr.fill()
            continue
        if soft > 0:
            g = cairo.RadialGradient(x, y, 0, x, y, rad)
            g.add_color_stop_rgba(0, cr_, cg, cb, a)
            g.add_color_stop_rgba(max(0.0, 1 - soft), cr_, cg, cb, a)
            g.add_color_stop_rgba(1, cr_, cg, cb, 0.0)
            cr.set_source(g)
        else:
            cr.set_source_rgba(cr_, cg, cb, a)
        cr.arc(x, y, rad, 0, 2 * math.pi)
        cr.fill()
    return cv.to_buf(rc.linear)
