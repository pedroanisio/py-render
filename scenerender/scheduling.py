"""Evaluated node windows and sequence schedules on each container's clock.

Document.windows remains the load-time inspection snapshot. Rendering uses these
functions so instance overrides, animation and media trims can change a schedule.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
import math

from .document import ln
from .values import parse_fps, parse_float


def authored_window(ev, el, ctx, *, timing_ctx=None):
    """Authored window, before a sequence adds its accumulated offset."""
    doc = ev.doc
    s = doc.markers.get(el.get("startMarker"), parse_float(el.get("start"), 0.))
    e = doc.markers.get(el.get("endMarker"))
    if e is None and el.get("end") is not None:
        e = parse_float(el.get("end"))
    if not any(ctx.scope.lookup(el.get("id"), p) is not None or ev._anims(el, p)
               for p in ("start", "end", "startMarker", "endMarker")):
        return s, e
    seed = timing_ctx if timing_ctx is not None else replace(ctx, node_start=s, node_end=e)
    sm, em = ev.str(el, "startMarker", seed), ev.str(el, "endMarker", seed)
    s = doc.markers[sm] if sm in doc.markers else ev.num(el, "start", seed, 0.)
    value = ev.get(el, "end", seed)
    e = doc.markers[em] if em in doc.markers else (float(value) if value is not None else None)
    return s, e


def window(ev, el, ctx):
    # An entered child carries the offset used at entry. Never subtract a static
    # load-time offset from a schedule evaluated in an instance's scope.
    if ctx.clock_node is el:
        ctx = replace(ctx, t=ctx.t + ctx.clock_offset, clock_node=None, clock_offset=0.)
    parent = el.getparent()
    if parent is not None and ln(parent) == "sequence" and ln(el) != "transition":
        schedule = sequence_schedule(ev, parent, ctx)
        if el in schedule:
            return schedule[el]
    s, e = authored_window(ev, el, ctx)
    if (ln(el) == "sequence" or e is not None) and any(_property_depends_on_window(ev, el, p)
                                                     for p in ("start", "end")):
        return _place(ev, el, ctx, 0., scheduled=False)
    if ln(el) == "sequence" and e is None:
        e = s + natural_duration(ev, el, replace(ctx, node_start=s, node_end=e), scheduled=False)
    return s, e


def sequence_schedule(ev, seq, ctx, *, origin=None, owner_ctx=None):
    """Child windows on the sequence content clock; ctx already includes its warp."""
    saved = next((clock for clock in reversed(ctx.sequence_clocks)
                  if clock[0] is seq and clock[2] == ctx.comp_t), None)
    if origin is None:
        parent = seq.getparent()
        origin = (saved[3] if saved is not None else 0. if parent is not None and ln(parent) == "sequence"
                  else window(ev, seq, ctx)[0])
    if owner_ctx is None:
        time, end = (saved[1], saved[4]) if saved is not None else (ctx.t, None)
        owner_ctx = replace(ctx, t=time, node_start=origin, node_end=end, clock_node=seq, clock_offset=0.)
        if end is None:
            owner_ctx = replace(owner_ctx, node_end=origin + natural_duration(ev, seq, owner_ctx,
                                                                             scheduled=origin == 0.))
    owner_ctx = replace(owner_ctx, node_start=origin)
    # Contexts include scope and repeat bindings; retaining only recent schedules
    # bounds memory during long exports and audio sampling.
    canonical = replace(ctx, node_start=0., node_end=None, clock_node=None, clock_offset=0.)
    key = (seq, origin, canonical, owner_ctx.t, owner_ctx.node_end)
    cache = ev.__dict__.setdefault("_sequence_schedules", OrderedDict())
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    busy = ev.__dict__.setdefault("_scheduling", set())
    guard = (seq, ctx.scope, ctx.comp_t)
    if guard in busy:
        # A timing expression may inspect another property of its own sequence.
        # Use the prepared window only to break this property-evaluation cycle.
        return {c: ev.doc.window(c) for c in ev.doc.nodes(seq) if ln(c) != "transition"}
    busy.add(guard)
    try:
        gap = ev.num(seq, "timeGap", owner_ctx, 0.)
        result, cursor = {}, origin
        for child in ev.doc.nodes(seq):
            if ln(child) == "transition":
                continue
            start, end = _place(ev, child, ctx, cursor, scheduled=True)
            result[child] = start, end
            cursor = end + gap
        cache[key] = result
        while len(cache) > 128:
            cache.popitem(last=False)
        return result
    finally:
        busy.remove(guard)


def _place(ev, el, ctx, cursor, *, scheduled):
    """Resolve a child's own start and duration on one mutually consistent window."""
    seed_shift = ev.doc.clock_shift.get(el, cursor) if scheduled else 0.
    seed_ctx = replace(ctx, t=ctx.t - seed_shift, node_start=0., node_end=None,
                       clock_node=None, clock_offset=0.)
    offset, _ = authored_window(ev, el, seed_ctx)
    def at_start(offset):
        start = cursor + offset
        local = replace(ctx, t=ctx.t - start if scheduled else ctx.t, node_start=0. if scheduled else start,
                        node_end=None, clock_node=el, clock_offset=start if scheduled else 0.)
        duration = natural_duration(ev, el, local, scheduled=scheduled)
        return start, duration, replace(local, node_end=local.node_start + duration)
    if _property_depends_on_window(ev, el, "start"):
        def evaluate(value):
            _, _, local = at_start(value)
            return authored_window(ev, el, local, timing_ctx=local)[0]
        offset = _fixed_timing(evaluate, offset, el, nonnegative=False)
    start, duration, _ = at_start(offset)
    return start, start + duration


def natural_duration(ev, el, ctx, *, scheduled=True):
    """Occupied duration, including evaluated trim, stretch, speed and loops."""
    if not _duration_depends_on_window(ev, el):
        return _duration_once(ev, el, ctx, scheduled=scheduled)
    origin = 0. if scheduled else ctx.node_start
    canonical = replace(ctx, node_start=origin, node_end=None)
    key = (el, canonical, scheduled)
    cache = ev.__dict__.setdefault("_duration_windows", OrderedDict())
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    start, end = ev.doc.window(el)
    seed = max(0., end - start) if end is not None else 0.
    if not seed:
        from .document import natural_duration as prepared_duration
        seed = prepared_duration(ev.doc, el)
    def evaluate(duration):
        current = replace(ctx, node_start=origin, node_end=origin + duration)
        return _duration_once(ev, el, current, scheduled=scheduled)
    busy = ev.__dict__.setdefault("_solving_duration", set())
    guard = (el, ctx.scope, ctx.comp_t)
    if guard in busy:
        return seed
    busy.add(guard)
    try:
        duration = _fixed_timing(evaluate, seed, el)
    finally:
        busy.remove(guard)
    cache[key] = duration
    while len(cache) > 128:
        cache.popitem(last=False)
    return duration


def _duration_depends_on_window(ev, el):
    key = ("duration-dependencies", el)
    if key not in ev._keys:
        props = {"start", "end", "speed", "clipIn", "clipOut", "timeStretch", "loop", "asset", "symbol",
                 "timeScale", "timeOffset", "timeGap"}
        ev._keys[key] = any(_property_depends_on_window(ev, el, p) for p in props)
    return ev._keys[key]


def _property_depends_on_window(ev, el, prop):
    return any(ln(c) in ("expression", "link") or
               ln(c) == "animate" and c.get("timeBase") == "normalized" for c in ev._anims(el, prop))


def _fixed_timing(evaluate, seed, el, *, nonnegative=True):
    """Solve D = inferred_duration(window=D), deterministically from the authored seed.

    A secant step resolves ordinary normalized timing dependencies quickly. A
    bounded iteration count and residual check also handle held/discontinuous
    keys: contradictory timing is reported instead of hanging or hiding a cycle.
    """
    value, previous = max(0., seed) if nonnegative else seed, None
    best, error = value, float("inf")
    bracket = None
    for _ in range(64):
        target = evaluate(value)
        if not math.isfinite(target):
            break
        residual = target - value
        if abs(residual) <= 1e-10 * max(1., abs(value), abs(target)):
            return max(0., target) if nonnegative else target
        if abs(residual) < error:
            best, error = value, abs(residual)
        if bracket is not None:
            (lo, lr), (hi, hr) = bracket
            if lo < value < hi:
                bracket = ((lo, lr), (value, residual)) if lr * residual < 0 else ((value, residual), (hi, hr))
        if previous is not None:
            p, r = previous
            if bracket is None and r * residual < 0:
                bracket = tuple(sorted(((p, r), (value, residual))))
            delta = residual - r
            candidate = value - residual * (value - p) / delta if abs(delta) > 1e-15 else target
        else:
            candidate = target
        if bracket is not None:
            lo, hi = bracket[0][0], bracket[1][0]
            if not lo < candidate < hi:
                candidate = (lo + hi) / 2
        if not math.isfinite(candidate) or (nonnegative and candidate < 0) or abs(candidate - value) > 16 * max(1., abs(seed), abs(value)):
            candidate = (value + target) / 2
            candidate = max(0., candidate) if nonnegative else candidate
        previous, value = (value, residual), candidate
    from .registry import warn_once
    warn_once("timing-cycle", el.get("id") or ln(el),
              "normalized timing has no converged window; using the closest finite window")
    return best


def _duration_once(ev, el, ctx, *, scheduled):
    s, e = authored_window(ev, el, ctx, timing_ctx=ctx if ctx.node_end is not None else None)
    if e is not None:
        return max(0., e - s)
    tag = ln(el)
    if tag in ("layer", "instance"):
        remap = el.find("timeRemap")
        if remap is not None:
            keys = ev.keys(el, remap, "timeRemap")
            if keys:
                return max(0., max(k.time for k in keys))
        aid = ev.str(el, "asset" if tag == "layer" else "symbol", ctx)
        asset = ev.doc.ids.get(aid)
        duration = ev.num(asset, "duration", ctx, 0.) if asset is not None else 0.
        if asset is not None and ln(asset) == "imageSequence":
            step = max(1, int(ev.num(asset, "step", ctx, 1)))
            count = max(0, (int(ev.num(asset, "last", ctx)) - int(ev.num(asset, "first", ctx))) // step + 1)
            duration = count / float(parse_fps(ev.str(asset, "fps", ctx)))
        span = max(0., ev.num(el, "clipOut", ctx, duration) - ev.num(el, "clipIn", ctx, 0.))
        speed = abs(ev.num(el, "speed", ctx, 1.))
        if speed == 0:
            return max(0., ev.doc.duration - ctx.clock_offset - s)
        return span * ev.num(el, "timeStretch", ctx, 1.) * (1 + ev.num(el, "loop", ctx, 0.)) / speed
    if tag in ("group", "sequence"):
        origin = 0. if scheduled else ctx.node_start
        owner = replace(ctx, node_start=origin)
        scale = max(1e-9, ev.num(el, "timeScale", owner, 1.))
        offset = ev.num(el, "timeOffset", owner, 0.)
        content = replace(owner, t=origin + (ctx.t - origin - offset) * scale)
        if tag == "sequence":
            children = sequence_schedule(ev, el, content, origin=origin, owner_ctx=owner)
            ends = [e for _, e in children.values()]
        else:
            ends = []
            for child in ev.doc.nodes(el):
                if ln(child) == "transition":
                    continue
                cs, ce = window(ev, child, content)
                if ce is None:
                    ce = cs + natural_duration(ev, child, replace(content, node_start=cs, node_end=None), scheduled=False)
                ends.append(ce)
        return max(0., (max(ends) - origin) / scale + offset) if ends else 0.
    return 0.


def transition_value(ev, tr, prop, ctx, default=None):
    if tr.get("{urn:scenerender}sequenceTransition"):
        inherited = {"type": "transition", "duration": "transitionDuration"}.get(prop)
        if inherited:
            owner = tr.getparent()
            saved = next((clock for clock in reversed(ctx.sequence_clocks)
                          if clock[0] is owner and clock[2] == ctx.comp_t), None)
            current = (replace(ctx, t=saved[1], node_start=saved[3], node_end=saved[4])
                       if saved is not None else ctx)
            return ev.get(owner, inherited, current, default)
    return ev.get(tr, prop, ctx, default)


def transitions_for(ev, parent, ctx):
    """Evaluated junctions; explicit transitions replace sequence defaults."""
    if parent is None:
        return []
    all_tr = [t for t in parent if ln(t) == "transition"]
    explicit = {(ev.str(t, "from", ctx), ev.str(t, "to", ctx)) for t in all_tr
                if not t.get("{urn:scenerender}sequenceTransition")}
    return [t for t in all_tr if not t.get("{urn:scenerender}sequenceTransition") or
            (transition_value(ev, t, "type", ctx) and
             (ev.str(t, "from", ctx), ev.str(t, "to", ctx)) not in explicit)]


def transition_window(ev, tr, ctx):
    a = ev.doc.ids.get(ev.str(tr, "from", ctx))
    b = ev.doc.ids.get(ev.str(tr, "to", ctx))
    if a is None and b is None:
        return None
    duration = float(transition_value(ev, tr, "duration", ctx, .5))
    cut = ev.window(b, ctx)[0] if b is not None else ev.window(a, ctx)[1]
    cut = ev.doc.duration if cut is None else cut
    alignment = ev.str(tr, "alignment", ctx, "center")
    start = cut - duration / 2 if alignment == "center" else cut if alignment == "start" else cut - duration
    return start, start + duration, cut
