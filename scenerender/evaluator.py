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
from .document import Document, ln, _param_list
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
    clock_node: Any = None      # node whose sequence offset has already been applied
    clock_offset: float = 0.0   # evaluated sequence offset applied at node entry
    sequence_clocks: tuple = () # (sequence, time before its warp, composition time, window start, end)

    def vars_key(self) -> tuple:
        """`vars` with dict values frozen, so it can sit in a cache key (an `item` may be a dict)."""
        def freeze(value):
            if isinstance(value, dict):
                return tuple(sorted((k, freeze(v)) for k, v in value.items()))
            if isinstance(value, (list, tuple)):
                return tuple(freeze(v) for v in value)
            return value
        return freeze(self.vars)

    def __hash__(self):
        return hash((self.t, self.comp_t, self.frame, self.scope, self.vars_key(),
                     self.node_start, self.node_end, self.clock_node, self.clock_offset, self.sequence_clocks))

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
    _statics: dict = field(default_factory=dict)   # (el, prop) -> (authored, value) of undriven properties
    _depth: int = 0

    # ------------------------------------------------------------ public
    def explicit(self, el, prop: str, ctx: Ctx) -> bool:
        """Whether a value is authored, driven, or supplied by this instance."""
        return (el.get(prop) is not None or ctx.scope.lookup(el.get("id"), prop) is not None
                or bool(self._anims(el, prop)))

    def window(self, el, ctx: Ctx) -> tuple[float, float | None]:
        """Active window on the parent clock, evaluated in this instance's scope."""
        from .scheduling import window
        return window(self, el, ctx)

    def node_ctx(self, el, ctx: Ctx) -> Ctx:
        s, e = self.window(el, ctx)
        if ctx.clock_node is el:
            shift = ctx.clock_offset
            s, e = s - shift, e - shift if e is not None else None
        return replace(ctx, node_start=s, node_end=e)

    def enter_node(self, el, ctx: Ctx) -> Ctx:
        if ctx.clock_node is not el:
            s, e = self.window(el, ctx)
            parent = el.getparent()
            shift = s if parent is not None and ln(parent) == "sequence" and ln(el) != "transition" else 0.
            return replace(ctx, t=ctx.t - shift, clock_node=el, clock_offset=shift,
                           node_start=s - shift, node_end=None if e is None else e - shift)
        return self.node_ctx(el, ctx)

    def child_ctx(self, el, ctx: Ctx) -> Ctx:
        scale = self.num(el, "timeScale", ctx, 1.)
        offset = self.num(el, "timeOffset", ctx, 0.)
        if ln(el) == "sequence":
            clocks = tuple(v for v in ctx.sequence_clocks if v[0] is not el)
            ctx = replace(ctx, sequence_clocks=(*clocks, (el, ctx.t, ctx.comp_t, ctx.node_start, ctx.node_end)))
        if scale == 1. and offset == 0.:
            return ctx
        start = self.node_ctx(el, ctx).node_start
        return replace(ctx, t=start + (ctx.t - start - offset) * scale)

    def instance_time(self, el, ctx: Ctx, sym) -> float | None:
        ctx = self.node_ctx(el, ctx)
        s = ctx.node_start
        remap = el.find("timeRemap")
        if remap is not None:
            return float(anim.sample(self.keys(el, remap, "timeRemap"), ctx.t - s,
                                     remap.get("defaultInterpolation", "linear")))
        clip_in = self.num(el, "clipIn", ctx, 0)
        lt = (ctx.t - s) * self.num(el, "speed", ctx, 1) + clip_in
        duration = self.num(sym, "duration", ctx, 0)
        clip_out = self.num(el, "clipOut", ctx, duration)
        span = clip_out - clip_in
        if span > 0:
            u = lt - clip_in
            loops = int(self.num(el, "loop", ctx, 0))
            if loops and (loops < 0 or u < span * (loops + 1)):
                u %= span
            elif u > span + 1e-9 and self.get(el, "end", ctx) is None:
                return None
            else:
                u = min(u, span)
            if self.bool(el, "reverse", ctx):
                u = span - u
            lt = clip_in + u
        return lt

    def enter_instance(self, el, ctx: Ctx, sym, *, keep_finished=False) -> Ctx | None:
        time = self.instance_time(el, ctx, sym)
        if time is None:
            if not keep_finished:
                return None
            time = self.num(el, "clipOut", ctx, self.num(sym, "duration", ctx, 0))
        overrides = {(o.get("target"), o.get("property")): o.get("value") for o in el if ln(o) == "override"}
        return replace(ctx, t=time, scope=ctx.scope.push(el.get("id", ""), overrides), clock_node=None, clock_offset=0.)

    def _within_context(self, node, boundary, ctx: Ctx) -> Ctx:
        """Follow group/sequence clocks and repeat bindings to a referenced descendant."""
        from .nodes.core import _repeat_vars
        ancestors = []
        for ancestor in node.iterancestors():
            if ancestor is boundary:
                break
            ancestors.append(ancestor)
        for ancestor in reversed(ancestors):
            if ln(ancestor) not in ("group", "sequence"):
                continue
            ctx = self.enter_node(ancestor, ctx)
            ctx = self.child_ctx(ancestor, ctx)
            ctx = _repeat_vars(ancestor, ctx)
        return self.enter_node(node, ctx)

    def context_at(self, node, ctx: Ctx, time: float) -> Ctx:
        """Rebuild a node's clock at composition time, retaining its instance binding."""
        boundary = self.doc.section("composition")
        current = Ctx(time, time, frame=round(time * float(self.doc.fps)), vars=ctx.vars)
        symbol = next((a for a in (node, *node.iterancestors()) if ln(a) == "symbol"), None)
        if symbol is not None:
            for iid in ctx.scope.path:
                instance = self.doc.ids.get(iid)
                if instance is None or boundary not in instance.iterancestors():
                    break
                entered = self._within_context(instance, boundary, current)
                boundary = self.doc.ids.get(self.str(instance, "symbol", entered))
                if boundary is None:
                    break
                current = self.enter_instance(instance, entered, boundary, keep_finished=True)
                if boundary is symbol:
                    break
            if boundary is not symbol:
                # Standalone symbol evaluation has no composition-to-local map.
                return replace(ctx, t=ctx.t + time - ctx.comp_t, comp_t=time, frame=current.frame)
        return current if node is boundary else self._within_context(node, boundary, current)

    def context_at_local(self, node, ctx: Ctx, time: float) -> Ctx:
        """Find a nearby composition sample for a requested node-local time.

        Follow the current branch for loops and reversed/remapped clocks. Held
        or discontinuous clocks may not contain the requested value; in that
        case preserve the closest composition sample and explicitly set the
        requested local clock (as required by posterize-time).
        """
        if abs(time - ctx.t) < 1e-12:
            return ctx
        anchor = ctx.comp_t
        cache = {}
        def sample(t):
            if t not in cache:
                cache[t] = self.context_at(node, ctx, t)
            c = cache[t]
            return c, c.t - time
        best, error = ctx, abs(ctx.t - time)
        t = anchor
        limit = max(1., self.doc.duration, abs(time - ctx.t))
        # Newton iteration handles affine clocks exactly and follows the nearby
        # smooth branch of remaps without searching other loop iterations.
        for _ in range(16):
            current, residual = sample(t)
            if abs(residual) < error:
                best, error = current, abs(residual)
            if abs(residual) < 1e-9:
                return replace(current, t=time)
            h = 1e-5 * max(1., abs(t))
            left, right = t-h, t+h
            # Use the actual representable interval and clocks. Subtracting the
            # requested time first, or dividing by a rounded nominal 2*h, adds
            # seek-dependent drift even to the identity clock.
            derivative = (sample(right)[0].t - sample(left)[0].t) / (right-left)
            if not math.isfinite(derivative) or abs(derivative) < 1e-9:
                break
            step = max(-limit, min(limit, residual / derivative))
            t -= step
        # A freeze can hide a nearby invertible section. Expand around the
        # original sample, then bisect sign changes; verify the residual so a
        # jump across a loop boundary is never mistaken for an inverse.
        radius = max(1e-4, min(abs(time - ctx.t), 1 / float(self.doc.fps)))
        for _ in range(18):
            points = sorted({anchor - radius, anchor, anchor + radius})
            candidates = []
            for left, right in zip(points, points[1:]):
                lc, lr = sample(left)
                rc, rr = sample(right)
                for c, r in ((lc, lr), (rc, rr)):
                    if abs(r) < error:
                        best, error = c, abs(r)
                    if abs(r) < 1e-9:
                        candidates.append(c)
                if lr * rr >= 0:
                    continue
                for _ in range(40):
                    mid = (left + right) / 2
                    mc, mr = sample(mid)
                    if abs(mr) < 1e-9:
                        candidates.append(mc)
                        break
                    if lr * mr < 0:
                        right = mid
                    else:
                        left, lr = mid, mr
            if candidates:
                return replace(min(candidates, key=lambda c: abs(c.comp_t - anchor)), t=time)
            if radius >= 2 * limit:
                break
            radius *= 2
        return replace(best, t=time)

    def history_origin(self, node, ctx: Ctx):
        """Identity of the current simulation replay branch on the composition clock."""
        origin = self.context_at_local(node, ctx, 0.)
        actual = self.context_at(node, ctx, origin.comp_t)
        if abs(actual.t) > 1e-8:
            # No preimage of local zero (for example a permanently frozen remap).
            # Such histories depend on the requested composition sample.
            return ("sample", ctx.comp_t)
        return ("origin", round(origin.comp_t, 8))

    def reference(self, ref: str, origin, ctx: Ctx):
        """Return (node, evaluation context), resolving effective instance/child IDs."""
        if origin is not None:
            ref = self.doc.resolve_id(origin, ref)
        node = self.doc.ids.get(ref)
        if node is not None:
            symbol = next((a for a in node.iterancestors() if ln(a) == "symbol"), None)
            if symbol is not None and ctx.scope.path:
                instance = self.doc.ids.get(ctx.scope.path[-1])
                if instance is not None and instance.get("symbol") == symbol.get("id"):
                    return self.reference("/".join((*ctx.scope.path, ref)), None, ctx)
            comp = self.doc.section("composition")
            if comp in node.iterancestors():
                return node, self._within_context(node, comp, Ctx(ctx.comp_t, ctx.comp_t, frame=ctx.frame))
            return node, self.node_ctx(node, ctx)
        if "/" not in ref:
            return None, ctx     # only an instance path (below) can name an unknown ID
        # Authored IDs can contain '/' after include expansion; match the longest
        # instance ID, then resolve the remaining path inside that instance only.
        boundary = self.doc.section("composition")
        current = Ctx(ctx.comp_t, ctx.comp_t, frame=ctx.frame)
        remaining = ref
        for _ in range(64):
            candidates = []
            for key, item in self.doc.ids.items():
                if ln(item) != "instance":
                    continue
                if boundary is not None and boundary not in item.iterancestors():
                    continue
                short = key
                if boundary is not None:
                    # Resolve local instance names in imported symbols.
                    aliases = [part for part in remaining.split("/") if self.doc.resolve_id(item, part) == key]
                    if aliases:
                        short = aliases[0]
                if remaining.startswith(key + "/"):
                    candidates.append((len(key), item, remaining[len(key)+1:]))
                elif remaining.startswith(short + "/"):
                    candidates.append((len(short), item, remaining[len(short)+1:]))
            if not candidates:
                break
            _, instance, remaining = max(candidates, key=lambda c: c[0])
            current = self._within_context(instance, boundary, current)
            sym = self.doc.ids.get(self.str(instance, "symbol", current))
            if sym is None:
                break
            current = self.enter_instance(instance, current, sym, keep_finished=True)
            boundary = sym
            name = self.doc.resolve_id(sym, remaining)
            node = self.doc.ids.get(name)
            if node is not None and sym in node.iterancestors():
                return node, self._within_context(node, sym, current)
        return None, ctx

    def get(self, el, prop: str, ctx: Ctx, default: Any = None) -> Any:
        anims = self._anims(el, prop)
        if not anims and not ctx.scope.overrides:
            # Undriven and not overridden: the authored (or schema default) value, parsed once per
            # attribute text (documents can be edited between evaluations).
            key = (el, prop)
            raw = el.get(prop)
            hit = self._statics.get(key)
            if hit is None or hit[0] != raw:
                marker = object()
                v = self.base(el, prop, ctx, marker)
                hit = self._statics[key] = (raw, False, None) if v is marker else \
                    (raw, True, self._resolve_value(el, prop, v))
            return hit[2] if hit[1] else default
        base = self.base(el, prop, ctx, default)
        if not anims:
            return self._resolve_value(el, prop, base)
        if self._depth > 64:
            log.warning("property recursion limit reached at %s.%s", el.get("id"), prop)
            return base
        self._depth += 1
        try:
            return self._resolve_value(el, prop, self._animate(el, prop, ctx, base, anims))
        finally:
            self._depth -= 1

    def _resolve_value(self, el, prop, value):
        if not isinstance(value, str):
            return value
        info = self.doc.type_info(el)
        attr = info.attrs.get(prop) if info else None
        if attr is not None and attr.type == "xs:IDREF":
            return self.doc.resolve_id(el, value.strip())
        if attr is not None and attr.type == "xs:IDREFS":
            return " ".join(self.doc.resolve_id(el, ref) for ref in value.split())
        if "var(--" in value:
            value = re.sub(r"var\(--([^)]*)\)",
                lambda m: "var(--" + self.doc.resolve_id(el, "var:" + m[1])[4:] + ")", value)
        if "url(#" in value:
            value = re.sub(r"url\(#([^)]*)\)", lambda m: "url(#" + self.doc.resolve_id(el, m[1]) + ")", value)
        return value

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
        from . import expr
        src = self.base(el, "condition", ctx)
        if not src:
            return True
        try:
            return expr.truthy(_jsval(self.run_expression(src, el, "condition", ctx, None)))
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
                if parse_bool(a.get("additive")) and isinstance(value, (float, tuple)):
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
            elif (tag == "expression" and parse_bool(a.get("enabled", "true"), True)
                  and (el, prop) not in self.__dict__.get("_resampling_properties", ())):
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
            k = anim.apply_roving(k)
            self._keys[a] = k
        return k

    def _sample_animate(self, el, a, prop: str, ctx: Ctx):
        keys = self.keys(el, a, prop)
        t = self._animation_time(a, ctx)
        return anim.sample(keys, t, a.get("defaultInterpolation", "linear"),
                           a.get("extrapolateBefore", "hold"), a.get("extrapolateAfter", "hold"))

    @staticmethod
    def _animation_time(a, ctx: Ctx):
        base = a.get("timeBase", "composition")
        t = ctx.t
        if base == "local":
            t = ctx.t - ctx.node_start
        elif base == "normalized":
            span = (ctx.node_end - ctx.node_start) if ctx.node_end is not None else 0.0
            t = (ctx.t - ctx.node_start) / span if span > 0 else 0.0
        return t

    def _link(self, el, a, ctx: Ctx):
        src = a.get("source", "")
        delay = float(a.get("delay", 0))
        smoothing = float(a.get("smoothing", 0))
        n = 1 if smoothing <= 0 else 9
        vals = []
        for i in range(n):
            dt = delay + (smoothing * i / (n - 1) if n > 1 else 0.0)
            sampled = replace(ctx, t=ctx.t - dt, comp_t=ctx.comp_t - dt)
            if dt:
                sampled = replace(sampled, frame=round(sampled.comp_t * float(self.doc.fps)))
            v = self._link_source(src, sampled, el)
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

    def _link_source(self, src: str, ctx: Ctx, origin=None) -> float | None:
        if src.startswith("param:"):
            return _to_float(self.parameter(src[6:], origin, ctx))
        if src.startswith("marker:"):
            # Seconds since the marker (negative before it); the schema leaves the value open.
            mid = self.doc.resolve_id(origin, src[7:]) if origin is not None else src[7:]
            m = self.doc.markers.get(mid)
            return None if m is None else ctx.comp_t - m
        if src.startswith("audio:"):
            parts = src.split(":")
            if self.audio_amplitude is None:
                return 0.0
            aid = self.doc.resolve_id(origin, parts[1]) if origin is not None else parts[1]
            return float(self.audio_amplitude(aid, parts[2] if len(parts) > 2 else None, ctx.comp_t))
        node_id, _, p = src.rpartition(".")
        node, source_ctx = self.reference(node_id, origin, ctx)
        if node is None:
            log.warning("link source %r not found", src)
            return None
        return _to_float(self.get(node, p, source_ctx))

    def parameter(self, name: str, origin, ctx: Ctx):
        missing = object()
        item = ctx.var("param:" + name, missing)
        if item is not missing:
            return item
        if origin is not None:
            name = self.doc.resolve_id(origin, name)
        return _typed_param(self.doc, name)

    def _motion_path(self, el, mp, ctx: Ctx):
        from .geometry import PathSampler
        sampler = self._keys.get(("mp", mp))
        if sampler is None:
            sampler = PathSampler(mp.get("path"), constant_speed=parse_bool(mp.get("constantSpeed", "true"), True))
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
            if ln(mp) == "motionPath" and parse_bool(mp.get("autoOrient")):
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
    def seed_for(self, el, extra: str | None = None, ctx: Ctx | None = None) -> int:
        value = el.get("seed", "") if ctx is None else self.str(el, "seed", ctx, "")
        return self.seed_for_value(el, extra, value)

    def seed_for_value(self, el, extra: str | None, value: str) -> int:
        key = (self.doc.seed, el.get("id", ""), value, extra or "")
        memo = self.__dict__.setdefault("_seed_memo", {})
        hit = memo.get(key)
        if hit is None:
            if len(memo) > 100000:
                memo.clear()
            hit = memo[key] = zlib.crc32(f"{key[0]}:{key[1]}:{key[2]}:{key[3]}".encode())
        return hit

    def expression_seed(self, seed: str | None) -> int:
        """D25 `seed`: the expression's @seed, else the project's seed."""
        try:
            return int(float(seed)) if seed not in (None, "") else int(self.doc.seed)
        except (TypeError, ValueError):
            return int(self.doc.seed)

    def run_expression(self, src: str, el, prop: str, ctx: Ctx, value: Any, seed: str | None = None) -> Any:
        from . import expr, noise
        fn = expr.compile_expr(src)
        s = self.expression_seed(seed)
        channel = noise.channel_of(prop)
        frame = math.floor(ctx.t * float(self.doc.fps) + 1e-9)       # D25 frame: of the node's time
        # The built-ins may be reused across evaluations (expr.builtin_functions): one set per
        # (seed, time, property, value), e.g. for every instance of an instanced object at one sample time.
        bkey = (s, ctx.t, channel, value if isinstance(value, (int, float, str, type(None))) else None)
        memo = self.__dict__.setdefault("_builtin_memo", {})
        base_env = memo.get(bkey) if bkey[3] is not None or value is None else None
        if base_env is None:
            base_env = expr.builtin_functions(s, ctx.t, value=value, channel=channel, frame=frame)
            if bkey[3] is not None or value is None:
                if len(memo) > 4096:
                    memo.clear()
                memo[bkey] = base_env
        env = dict(base_env)
        doc = self.doc

        def prop_fn(ref: str):
            node_id, _, p = ref.rpartition(".")
            node, source_ctx = self.reference(node_id, el, ctx)
            if node is None:
                raise expr.ExprError(f"prop(): no node {node_id!r}")
            return _jsval(self.get(node, p, source_ctx))

        def value_at(t: float):
            # Shared effects/assets borrow their consumer's clock. Reconstruct
            # that clock, including the sampled window and composition frame.
            owner = ctx.clock_node if ctx.clock_node is not None else el
            # Rebuilding a clock can inspect this very property (start/end,
            # speed, or a property referenced by a parent's timing expression).
            # valueAtTime samples its pre-expression value in that dependency
            # as well, rather than recursively running the expression again.
            busy = self.__dict__.setdefault("_resampling_properties", set())
            busy.add((el, prop))
            try:
                sampled = self.context_at_local(owner, ctx, float(t))
            finally:
                busy.remove((el, prop))
            return _jsval(self._pre_expression(el, prop, sampled))

        def loop(kind: str = "cycle", n: int = 0, *, after: bool):
            mode = {"cycle": "loop", "pingpong": "ping-pong", "offset": "offset", "continue": "linear"}.get(kind, "loop")
            for a in self._anims(el, prop):
                if ln(a) == "animate":
                    keys = self.keys(el, a, prop)
                    if n and len(keys) > n:
                        keys = keys[-(n + 1):] if after else keys[:n + 1]
                    result = anim.sample(keys, self._animation_time(a, ctx), a.get("defaultInterpolation", "linear"),
                                         mode if not after else "hold", mode if after else "hold")
                    if a.get("property") != prop and prop in ALIASES:
                        result = _component(result, ALIASES[prop][1])
                    return _jsval(result)
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
            "param": lambda name: self.parameter(name, el, ctx), "prop": prop_fn, "valueAtTime": value_at,
            "loopOut": lambda kind="cycle", n=0: loop(kind, int(n), after=True),
            "loopIn": lambda kind="cycle", n=0: loop(kind, int(n), after=False),
            "markerTime": lambda mid: doc.markers.get(doc.resolve_id(el, mid), float("nan")), "beat": beat,
            "audioAmplitude": lambda track, band=None: float(self.audio_amplitude(doc.resolve_id(el, track), band, ctx.comp_t))
            if self.audio_amplitude else 0.0,
        })
        for name, item in ctx.vars:
            if name.startswith("param:"):
                env[name[6:]] = item
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
        return parse_bool(v)
    if t == "list":
        return _param_list(v)
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
