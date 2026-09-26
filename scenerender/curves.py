"""Easing curves for curveType. Each maps u in [0,1] to eased progress.

catmull-rom and tcb need neighbouring keys and are handled in anim.py; spring
uses key stiffness/damping/mass; cubic-bezier uses key/@bezier or easeOut/easeIn handles.
"""
from __future__ import annotations

import math
from typing import Callable

Ease = Callable[[float], float]

_C1 = 1.70158
_C2 = _C1 * 1.525
_C3 = _C1 + 1
_C4 = 2 * math.pi / 3
_C5 = 2 * math.pi / 4.5


def _bounce_out(u: float) -> float:
    n, d = 7.5625, 2.75
    if u < 1 / d:
        return n * u * u
    if u < 2 / d:
        u -= 1.5 / d
        return n * u * u + 0.75
    if u < 2.5 / d:
        u -= 2.25 / d
        return n * u * u + 0.9375
    u -= 2.625 / d
    return n * u * u + 0.984375


def _in_out(f_in: Ease) -> Ease:
    return lambda u: f_in(2 * u) / 2 if u < 0.5 else 1 - f_in(2 - 2 * u) / 2


def _out(f_in: Ease) -> Ease:
    return lambda u: 1 - f_in(1 - u)


_IN: dict[str, Ease] = {
    "sine": lambda u: 1 - math.cos(u * math.pi / 2),
    "quad": lambda u: u ** 2,
    "cubic": lambda u: u ** 3,
    "quart": lambda u: u ** 4,
    "quint": lambda u: u ** 5,
    "expo": lambda u: 0.0 if u == 0 else 2 ** (10 * u - 10),
    "circ": lambda u: 1 - math.sqrt(max(0.0, 1 - u * u)),
    "back": lambda u: _C3 * u ** 3 - _C1 * u ** 2,
    "elastic": lambda u: u if u in (0, 1) else -(2 ** (10 * u - 10)) * math.sin((u * 10 - 10.75) * _C4),
    "bounce": lambda u: 1 - _bounce_out(1 - u),
}

EASES: dict[str, Ease] = {"linear": lambda u: u}
for _name, _f in _IN.items():
    EASES[f"{_name}-in"] = _f
    EASES[f"{_name}-out"] = _out(_f)
    EASES[f"{_name}-in-out"] = _in_out(_f)
# Penner in-out variants that differ from the symmetric construction.
EASES["back-in-out"] = lambda u: ((2 * u) ** 2 * ((_C2 + 1) * 2 * u - _C2)) / 2 if u < 0.5 else (
    (2 * u - 2) ** 2 * ((_C2 + 1) * (u * 2 - 2) + _C2) + 2) / 2
EASES["elastic-in-out"] = lambda u: u if u in (0, 1) else (
    -(2 ** (20 * u - 10) * math.sin((20 * u - 11.125) * _C5)) / 2 if u < 0.5
    else (2 ** (-20 * u + 10) * math.sin((20 * u - 11.125) * _C5)) / 2 + 1)
# CSS keyword curves.
CSS_BEZIER = {"ease-in": (0.42, 0, 1, 1), "ease-out": (0, 0, 0.58, 1), "ease-in-out": (0.42, 0, 0.58, 1)}


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Ease:
    """CSS cubic-bezier timing function solved for x by Newton + bisection."""
    def bx(t): return 3 * x1 * t * (1 - t) ** 2 + 3 * x2 * t * t * (1 - t) + t ** 3
    def by(t): return 3 * y1 * t * (1 - t) ** 2 + 3 * y2 * t * t * (1 - t) + t ** 3
    def dbx(t): return 3 * x1 * (1 - t) ** 2 + 6 * (x2 - x1) * t * (1 - t) + 3 * (1 - x2) * t * t

    def f(u: float) -> float:
        if u <= 0 or u >= 1:
            return u
        t = u
        for _ in range(8):
            d = dbx(t)
            if abs(d) < 1e-7:
                break
            t2 = t - (bx(t) - u) / d
            if not 0 <= t2 <= 1:
                break
            t = t2
        if abs(bx(t) - u) > 1e-6:
            lo, hi = 0.0, 1.0
            for _ in range(40):
                t = (lo + hi) / 2
                lo, hi = (t, hi) if bx(t) < u else (lo, t)
        return by(t)
    return f


for _k, _b in CSS_BEZIER.items():
    EASES[_k] = cubic_bezier(*_b)


def spring(stiffness: float = 100, damping: float = 10, mass: float = 1, duration: float = 1.0) -> Ease:
    """Damped harmonic oscillator from 0 to 1; u is scaled to real seconds by duration."""
    w0 = math.sqrt(stiffness / mass)
    zeta = damping / (2 * math.sqrt(stiffness * mass))

    def f(u: float) -> float:
        t = u * duration
        if zeta < 1:
            wd = w0 * math.sqrt(1 - zeta * zeta)
            x = math.exp(-zeta * w0 * t) * (math.cos(wd * t) + zeta * w0 / wd * math.sin(wd * t))
        else:
            x = math.exp(-w0 * t) * (1 + w0 * t)
        return 1 - x
    return f


def steps(n: int, position: str = "end") -> Ease:
    n = max(1, n)
    if position == "start":
        return lambda u: min(1.0, math.floor(u * n + 1) / n) if u > 0 else 0.0
    return lambda u: math.floor(u * n) / n if u < 1 else 1.0


def get(name: str | None) -> Ease:
    return EASES.get(name or "linear", EASES["linear"])
