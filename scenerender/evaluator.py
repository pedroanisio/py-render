"""Property evaluation: what is the value of el/@prop at this moment?

Resolution order for a property:
  1. base value: instance-scope override, else the attribute, else the schema default;
  2. animation children targeting the property, in document order:
     animate replaces the value (or adds to it when additive="true"), link
     replaces it, motionPath drives x/y, expression computes it with `value`
     bound to the result so far.
Values are typed by the property's schema simple type; properties the schema
does not declare (e.g. motionPath "progress") are typed from their key values.
"""
from __future__ import annotations

import logging
import math
import re
import zlib
from dataclasses import dataclass, field, replace
from typing import Any

from . import anim
from .document import Document, ln
from .values import parse_bool, parse_color, parse_length, parse_numbers, parse_point

log = logging.getLogger("scenerender")

ANIM_TAGS = ("animate", "expression", "link", "motionPath")
# Shorthands authored in the wild that are not schema attributes.
ALIASES = {"scaleX": ("scale", 0), "scaleY": ("scale", 1), "x": ("position", 0), "y": ("position", 1)}
_NUMERIC = re.compile(r"^\s*-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*$")


@dataclass(frozen=True)
class Scope:
    """Instance chain: overrides by (target id, property), outermost last."""
    overrides: tuple[tuple[tuple[str, str], str], ...] = ()
    path: tuple[str, ...] = ()

    def lookup(self, el_id: str | None, prop: str) -> str | None:
        if el_id is None:
            return None
        for (tid, p), v in self.overrides:
            if tid == el_id and p == prop:
                return v
        return None

    def push(self, instance_id: str, overrides: dict[tuple[str, str], str]) -> "Scope":
        return Scope(tuple(overrides.items()) + self.overrides, self.path + (instance_id,))


@dataclass(frozen=True)
class Ctx:
    t: float                    # time on the current clock (composition time, or symbol-local time)
    comp_t: float               # composition time
    frame: int = 0
    scope: Scope = Scope()
    vars: tuple = ()            # (name, value) pairs: index, count, item, textIndex, ...
    node_start: float = 0.0     # window of the node being evaluated (for timeBase local/normalized)
    node_end: float | None = None

    def at(self, t: float) -> "Ctx":
        return replace(self, t=t, comp_t=self.comp_t + (t - self.t))

    def var(self, name: str, default=None):
        for k, v in reversed(self.vars):
            if k == name:
                return v
        return default

    def with_vars(self, **kw) -> "Ctx":
        return replace(self, vars=self.vars + tuple(kw.items()))


@dataclass
class Evaluator:
    doc: Document
    audio_amplitude: Any = None        # callable(track_id, band, t) -> float, installed by the audio module
    _keys: dict = field(default_factory=dict)
    _depth: int = 0

    # ------------------------------------------------------------ public
    def get(self, el, prop: str, ctx: Ctx, default: Any = None) -> Any:
        base = self.base(el, prop, ctx, default)
        anims = self._anims(el, prop)
        if not anims:
            return base
        if self._depth > 64:
            log.warning("property recursion limit reached at %s.%s", el.get("id"), prop)
            return base
        self._depth += 1
        try:
            return self._animate(el, prop, ctx, base, anims)
        finally:
            self._depth -= 1

    def num(self, el, prop: str, ctx: Ctx, default: float = 0.0) -> float:
        v = self.get(el, prop, ctx, default)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, tuple) and v:
            return float(v[0])
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def length(self, el, prop: str, ctx: Ctx, parent: float, default: float = 0.0) -> float:
        v = self.get(el, prop, ctx, None)
        if v is None:
            return default
        return parse_length(v, parent, (self.doc.width, self.doc.height), default)

    def color(self, el, prop: str, ctx: Ctx, default=(0.0, 0.0, 0.0, 0.0)):
        v = self.get(el, prop, ctx, None)
        if v is None:
            return default
        if isinstance(v, tuple) and len(v) in (3, 4):
            return tuple(min(1.0, max(0.0, c)) for c in v) + ((1.0,) if len(v) == 3 else ())
        return parse_color(str(v), self.doc.tokens, default)

    def bool(self, el, prop: str, ctx: Ctx, default: bool = False) -> bool:
        v = self.get(el, prop, ctx, None)
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v != 0
        return parse_bool(v, default)

    def str(self, el, prop: str, ctx: Ctx, default: str | None = None) -> str | None:
        v = self.get(el, prop, ctx, default)
        return None if v is None else (v if isinstance(v, str) else _fmt(v))

    def base(self, el, prop: str, ctx: Ctx, default: Any = None) -> Any:
        raw = ctx.scope.lookup(el.get("id"), prop) if ctx.scope.overrides else None
        if raw is None:
            raw = el.get(prop)
        kind = self.kind(el, prop)
        if raw is None:
            info = self.doc.type_info(el)
            a = info.attrs.get(prop) if info else None
            if a is not None and a.default is not None:
                raw = a.default
            else:
                return default
        return self.parse(raw, kind)

    def kind(self, el, prop: str) -> str:
        info = self.doc.type_info(el)
        a = info.attrs.get(prop) if info else None
        return self.doc.schema.base_kind(a.type) if a else "unknown"

    def parse(self, raw: str, kind: str) -> Any:
        if kind in ("number", "integer"):
            try:
                return float(raw)
            except ValueError:
                return raw
        if kind == "bool":
            return parse_bool(raw)
        if kind == "list":
            return tuple(parse_numbers(raw))
        if kind == "point":
            return parse_point(raw)
        if kind == "length":
            return float(raw) if _NUMERIC.match(raw) else raw
        if kind == "unknown" and _NUMERIC.match(raw):
            return float(raw)
        return raw

    def condition(self, el, ctx: Ctx) -> bool:
        src = el.get("condition")
        if not src:
            return True
        try:
            return bool(self.run_expression(src, el, "condition", ctx, None))
        except Exception as e:  # noqa: BLE001 — a broken condition hides the node, loudly
            log.warning("condition on %s failed: %s", el.get("id"), e)
            return False

    # ------------------------------------------------------------ animation
    def _anims(self, el, prop: str) -> list:
        key = (el, prop)
        hit = self._keys.get(key)
        if hit is None:
            names = {prop}
            alias = ALIASES.get(prop)
            if alias:
                names.add(alias[0])
            hit = [a for a in el if ln(a) in ANIM_TAGS and (a.get("property") in names or
                                                            (ln(a) == "motionPath" and prop in ("x", "y")))]
            self._keys[key] = hit
        return hit

    def _animate(self, el, prop: str, ctx: Ctx, value: Any, anims: list) -> Any:
        for a in anims:
            tag = ln(a)
            comp = None
            if a.get("property") != prop and tag != "motionPath":
                comp = ALIASES[prop][1]
            if tag == "animate":
                v = self._sample_animate(el, a, prop, ctx)
                if comp is not None:
                    v = _component(v, comp)
                if v is None:
                    continue
                if a.get("additive") == "true" and isinstance(value, (float, tuple)):
                    v = anim._add(value, v)
                value = v
            elif tag == "link":
                v = self._link(el, a, ctx)
                if v is not None:
                    value = v if comp is None else _component(v, comp)
            elif tag == "motionPath":
                pt = self._motion_path(el, a, ctx)
                if pt is not None:
                    value = pt[0] if prop == "x" else pt[1] if prop == "y" else value
            elif tag == "expression" and a.get("enabled", "true") != "false":
                try:
                    v = self.run_expression(a.text or "", el, prop, ctx, value, seed=a.get("seed"))
                except Exception as e:  # noqa: BLE001
                    log.warning("expression %s.%s failed: %s", el.get("id"), prop, e)
                    continue
                value = v if comp is None else _component(v, comp)
        return value

    def keys(self, el, a, prop: str) -> list[anim.Key]:
        k = self._keys.get(a)
        if k is None:
            # Type keys by the animated property itself (an alias such as "scale" differs from "scaleX").
            kind = self.kind(el, a.get("property") or prop)
            raw = [kk for kk in a if ln(kk) == "key"]
            if kind in ("unknown", "string", "length", "paint") and raw:
                kind = _infer_kind(raw[0].get("value"), kind)
            kind = {"integer": "number", "point": "vector", "list": "vector", "length": "string"}.get(kind, kind)
            if kind == "paint":
                kind = "color" if all(not r.get("value", "").startswith("url(") for r in raw) else "string"
            k = []
            for r in raw:
                t = float(r.get("time"))
                if r.get("marker"):
                    t += self.doc.markers.get(r.get("marker"), 0.0)
                k.append(anim.Key(t, anim.parse_value(r.get("value"), kind, self.doc.tokens), r.get("value"),
                                  r.get("interpolation"), r))
            k.sort(key=lambda x: x.time)
            self._keys[a] = k
        return k

    def _sample_animate(self, el, a, prop: str, ctx: Ctx):
        keys = self.keys(el, a, prop)
        base = a.get("timeBase", "composition")
        t = ctx.t
        if base == "local":
            t = ctx.t - ctx.node_start
        elif base == "normalized":
            span = (ctx.node_end - ctx.node_start) if ctx.node_end is not None else 0.0
            t = (ctx.t - ctx.node_start) / span if span > 0 else 0.0
        return anim.sample(keys, t, a.get("defaultInterpolation", "linear"),
                           a.get("extrapolateBefore", "hold"), a.get("extrapolateAfter", "hold"))

    def _link(self, el, a, ctx: Ctx):
        src = a.get("source", "")
        delay = float(a.get("delay", 0))
        smoothing = float(a.get("smoothing", 0))
        n = 1 if smoothing <= 0 else 9
        vals = []
        for i in range(n):
            dt = delay + (smoothing * i / (n - 1) if n > 1 else 0.0)
            v = self._link_source(src, ctx.at(ctx.t - dt))
            if v is None:
                return None
            vals.append(v)
        v = sum(vals) / len(vals)
        v = v * float(a.get("scale", 1)) + float(a.get("offset", 0))
        if a.get("min") is not None:
            v = max(float(a.get("min")), v)
        if a.get("max") is not None:
            v = min(float(a.get("max")), v)
        return v

    def _link_source(self, src: str, ctx: Ctx) -> float | None:
        if src.startswith("param:"):
            return _to_float(self.doc.params.get(src[6:]))
        if src.startswith("marker:"):
            # Seconds since the marker (negative before it); the schema leaves the value open.
            m = self.doc.markers.get(src[7:])
            return None if m is None else ctx.comp_t - m
        if src.startswith("audio:"):
            parts = src.split(":")
            if self.audio_amplitude is None:
                return 0.0
            return float(self.audio_amplitude(parts[1], parts[2] if len(parts) > 2 else None, ctx.comp_t))
        node_id, _, p = src.partition(".")
        node = self.doc.ids.get(node_id)
        if node is None:
            log.warning("link source %r not found", src)
            return None
        return _to_float(self.get(node, p, ctx))

    def _motion_path(self, el, mp, ctx: Ctx):
        from .geometry import PathSampler
        sampler = self._keys.get(("mp", mp))
        if sampler is None:
            sampler = PathSampler(mp.get("path"), constant_speed=mp.get("constantSpeed", "true") != "false")
            self._keys[("mp", mp)] = sampler
        prog_anim = [c for c in mp if ln(c) == "animate" and c.get("property") == "progress"]
        if prog_anim:
            u = self._sample_animate(mp, prog_anim[0], "progress", ctx)
        else:
            s = float(mp.get("start", 0))
            e = float(mp.get("end")) if mp.get("end") is not None else (ctx.node_end if ctx.node_end is not None else s + 1)
            u = 0.0 if e <= s else min(1.0, max(0.0, (ctx.t - s) / (e - s)))
            from . import curves
            u = curves.get(mp.get("interpolation", "linear"))(u)
        x, y, _ = sampler.at(float(u))
        return x, y

    def motion_path_angle(self, el, ctx: Ctx) -> float | None:
        for mp in el:
            if ln(mp) == "motionPath" and mp.get("autoOrient") == "true":
                from .geometry import PathSampler
                sampler = self._keys.get(("mp", mp)) or PathSampler(mp.get("path"))
                self._keys[("mp", mp)] = sampler
                x0, y0 = self._motion_path(el, mp, ctx)
                x1, y1 = self._motion_path(el, mp, ctx.at(ctx.t + 1e-3))
                if abs(x1 - x0) + abs(y1 - y0) < 1e-9:
                    return None
                return math.degrees(math.atan2(y1 - y0, x1 - x0)) + float(mp.get("orientOffset", 0))
        return None

    # ------------------------------------------------------------ expressions
    def seed_for(self, el, extra: str | None = None) -> int:
        s = f"{self.doc.seed}:{el.get('id', '')}:{el.get('seed', '')}:{extra or ''}"
        return zlib.crc32(s.encode())

    def run_expression(self, src: str, el, prop: str, ctx: Ctx, value: Any, seed: str | None = None) -> Any:
        from . import expr
        fn = expr.compile_expr(src)
        s = self.seed_for(el, seed or prop)
        env = expr.builtin_functions(s, ctx.t, value=value)
        doc = self.doc

        def prop_fn(ref: str):
            node_id, _, p = ref.partition(".")
            node = doc.ids.get(node_id)
            if node is None:
                raise expr.ExprError(f"prop(): no node {node_id!r}")
            return _jsval(self.get(node, p, ctx))

        def value_at(t: float):
            return _jsval(self._pre_expression(el, prop, ctx.at(float(t))))

        def loop(kind: str = "cycle", n: int = 0, *, after: bool):
            mode = {"cycle": "loop", "pingpong": "ping-pong", "offset": "offset", "continue": "linear"}.get(kind, "loop")
            for a in self._anims(el, prop):
                if ln(a) == "animate":
                    keys = self.keys(el, a, prop)
                    if n and len(keys) > n:
                        keys = keys[-(n + 1):] if after else keys[:n + 1]
                    return _jsval(anim.sample(keys, ctx.t, a.get("defaultInterpolation", "linear"),
                                              mode if not after else "hold", mode if after else "hold"))
            return _jsval(value)

        def beat():
            if not doc.beat_grids:
                return 0.0
            bpm, off, _ = doc.beat_grids[0]
            return (ctx.comp_t - off) * bpm / 60.0

        env.update({
            "time": ctx.t, "frame": ctx.frame, "value": _jsval(value),
            "index": ctx.var("index", 0), "count": ctx.var("count", 1), "seed": s,
            "textIndex": ctx.var("textIndex", 0), "textTotal": ctx.var("textTotal", 1),
            "param": lambda name: _typed_param(doc, name), "prop": prop_fn, "valueAtTime": value_at,
            "loopOut": lambda kind="cycle", n=0: loop(kind, int(n), after=True),
            "loopIn": lambda kind="cycle", n=0: loop(kind, int(n), after=False),
            "markerTime": lambda mid: doc.markers.get(mid, float("nan")), "beat": beat,
            "audioAmplitude": lambda track, band=None: float(self.audio_amplitude(track, band, ctx.comp_t))
            if self.audio_amplitude else 0.0,
        })
        var = ctx.var("var")
        if var and ctx.var("item") is not None:
            env[var] = ctx.var("item")
        return _pyval(fn(env))

    def _pre_expression(self, el, prop: str, ctx: Ctx):
        value = self.base(el, prop, ctx)
        anims = [a for a in self._anims(el, prop) if ln(a) != "expression"]
        return self._animate(el, prop, ctx, value, anims) if anims else value


# ---------------------------------------------------------------- helpers
def _infer_kind(v: str | None, fallback: str) -> str:
    if v is None:
        return fallback
    v = v.strip()
    if v.startswith("#") or v.startswith("var("):
        return "color"
    parts = v.split(",")
    if len(parts) > 1 and all(_NUMERIC.match(p) for p in parts):
        return "vector"
    if _NUMERIC.match(v):
        return "number"
    return "string"


def _component(v, i: int):
    if isinstance(v, tuple):
        return v[i] if i < len(v) else v[-1]
    if isinstance(v, (list,)):
        return float(v[i]) if i < len(v) else float(v[-1])
    return v


def _to_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, tuple) and v:
        return float(v[0])
    try:
        return float(v)
    except (TypeError, ValueError):
        return 1.0 if str(v) == "true" else 0.0 if str(v) == "false" else None


def _typed_param(doc: Document, name: str):
    v = doc.params.get(name)
    t = doc.param_types.get(name)
    if v is None:
        return None
    if t in ("number", "time"):
        return _to_float(v)
    if t == "boolean":
        return v == "true"
    if t == "list":
        return [x.strip() for x in v.split(",")]
    return v


def _jsval(v):
    return list(v) if isinstance(v, tuple) else v


def _pyval(v):
    if isinstance(v, list):
        return tuple(float(x) if isinstance(x, (int, float)) else x for x in v)
    if isinstance(v, int) and not isinstance(v, bool):
        return float(v)
    return v


def _fmt(v) -> str:
    if isinstance(v, tuple):
        return ",".join(_fmt(x) for x in v)
    if isinstance(v, float):
        return repr(v)
    return str(v)
