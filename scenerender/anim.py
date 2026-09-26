"""Keyframe evaluation (animateType / keyType / timeRemapType).

Values are parsed by kind: "number", "vector" (comma list such as "x,y"),
"color", or "string" (held, never interpolated; path data is held too unless
both keys have the same command structure, in which case numbers are lerped).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from . import curves
from .values import parse_color, parse_point

_NUM = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


@dataclass
class Key:
    time: float
    value: object
    raw: str
    interp: str | None
    el: object  # lxml element, for per-key parameters


def parse_value(raw: str, kind: str, tokens) -> object:
    if kind == "number":
        try:
            return float(raw)
        except ValueError:
            return raw
    if kind == "color":
        return parse_color(raw, tokens)
    if kind == "vector":
        try:
            return tuple(float(v) for v in raw.split(","))
        except ValueError:
            return raw
    return raw


def _mix(a, b, u: float):
    if isinstance(a, float) and isinstance(b, float):
        return a + (b - a) * u
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        return tuple(x + (y - x) * u for x, y in zip(a, b))
    if isinstance(a, str) and isinstance(b, str):
        na, nb = _NUM.findall(a), _NUM.findall(b)
        if na and len(na) == len(nb) and _NUM.sub("#", a) == _NUM.sub("#", b):
            it = iter(float(x) + (float(y) - float(x)) * u for x, y in zip(na, nb))
            return _NUM.sub(lambda m: f"{next(it):.4f}", a)
    return a if u < 1 else b


def _add(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return a + b
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        return tuple(x + y for x, y in zip(a, b))
    return a


def _sub(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return a - b
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        return tuple(x - y for x, y in zip(a, b))
    return a


def _scale(a, k: float):
    if isinstance(a, float):
        return a * k
    if isinstance(a, tuple):
        return tuple(x * k for x in a)
    return a


def _ease_for(k0: Key, k1: Key, default: str, seg_dur: float) -> Callable[[float], float]:
    kind = k0.interp or default
    el = k0.el
    if kind in ("step", "hold"):
        return lambda u: 0.0 if u < 1 else 1.0
    if kind == "steps":
        return curves.steps(int(el.get("steps", 1)), el.get("stepPosition", "end"))
    if kind == "spring":
        return curves.spring(float(el.get("stiffness", 100)), float(el.get("damping", 10)),
                             float(el.get("mass", 1)), seg_dur)
    if kind == "cubic-bezier":
        bz = el.get("bezier")
        if bz:
            return curves.cubic_bezier(*[float(v) for v in re.split(r"[\s,]+", bz.strip())][:4])
        oi, os_ = parse_point(el.get("easeOut"), (1 / 3, 0.0))
        ii, is_ = parse_point(k1.el.get("easeIn"), (1 / 3, 0.0))
        # After Effects-style influence/speed handles, speed normalised to [0,1].
        return curves.cubic_bezier(oi, oi * os_, 1 - ii, 1 - ii * is_)
    return curves.get(kind)


def _tcb_tangents(keys: Sequence[Key], i: int, catmull: bool):
    """Kochanek-Bartels outgoing tangent of key i and incoming tangent of key i+1 (values per second)."""
    def tan(j: int, outgoing: bool):
        k = keys[j]
        if catmull:
            t_, c_, b_ = 0.0, 0.0, 0.0
        else:
            t_, c_, b_ = (float(k.el.get(n, 0)) for n in ("tension", "continuity", "bias"))
        prev = keys[j - 1] if j > 0 else None
        nxt = keys[j + 1] if j + 1 < len(keys) else None
        d_in = _scale(_sub(k.value, prev.value), 1 / max(1e-9, k.time - prev.time)) if prev else None
        d_out = _scale(_sub(nxt.value, k.value), 1 / max(1e-9, nxt.time - k.time)) if nxt else None
        d_in = d_in if d_in is not None else d_out
        d_out = d_out if d_out is not None else d_in
        if outgoing:
            a, b = (1 - t_) * (1 + b_) * (1 + c_) / 2, (1 - t_) * (1 - b_) * (1 - c_) / 2
        else:
            a, b = (1 - t_) * (1 + b_) * (1 - c_) / 2, (1 - t_) * (1 - b_) * (1 + c_) / 2
        return _add(_scale(d_in, a), _scale(d_out, b))
    return tan(i, True), tan(i + 1, False)


def _hermite(p0, p1, m0, m1, u: float, dt: float):
    h00 = 2 * u ** 3 - 3 * u ** 2 + 1
    h10 = u ** 3 - 2 * u ** 2 + u
    h01 = -2 * u ** 3 + 3 * u ** 2
    h11 = u ** 3 - u ** 2
    return _add(_add(_scale(p0, h00), _scale(m0, h10 * dt)), _add(_scale(p1, h01), _scale(m1, h11 * dt)))


def sample(keys: Sequence[Key], t: float, default_interp: str = "linear",
           before: str = "hold", after: str = "hold"):
    """Evaluate a sorted key list at time t with extrapolation."""
    if not keys:
        return None
    if len(keys) == 1:
        return keys[0].value
    t0, t1 = keys[0].time, keys[-1].time
    span = t1 - t0
    cycle_offset = None
    if t < t0 or t > t1:
        mode = before if t < t0 else after
        if span <= 0 or mode == "hold":
            return keys[0].value if t < t0 else keys[-1].value
        if mode == "linear":
            a, b = (keys[0], keys[1]) if t < t0 else (keys[-2], keys[-1])
            slope = _scale(_sub(b.value, a.value), 1 / max(1e-9, b.time - a.time))
            ref = a if t < t0 else b
            if isinstance(ref.value, (float, tuple)):
                return _add(ref.value, _scale(slope, t - ref.time))
            return ref.value
        n = math.floor((t - t0) / span)
        local = t - n * span
        if mode == "ping-pong" and n % 2:
            local = t1 - (local - t0)
        if mode == "offset":
            cycle_offset = _scale(_sub(keys[-1].value, keys[0].value), n)
        t = local
    for i in range(len(keys) - 1):
        k0, k1 = keys[i], keys[i + 1]
        if k0.time <= t <= k1.time:
            dt = k1.time - k0.time
            if dt <= 0:
                v = k1.value
                break
            u = (t - k0.time) / dt
            kind = k0.interp or default_interp
            if kind in ("catmull-rom", "tcb") and isinstance(k0.value, (float, tuple)) and isinstance(k1.value, type(k0.value)):
                m0, m1 = _tcb_tangents(keys, i, kind == "catmull-rom")
                v = _hermite(k0.value, k1.value, m0, m1, u, dt)
            else:
                # Overshooting curves (back, elastic) may leave colours outside [0,1]; consumers clamp.
                v = _mix(k0.value, k1.value, _ease_for(k0, k1, default_interp, dt)(u))
            break
    else:
        v = keys[-1].value
    return _add(v, cycle_offset) if cycle_offset is not None else v
