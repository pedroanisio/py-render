"""Parsers for the XSD simple types (colour, paint, length, point, fps, lists, timecode).

Colours are returned as straight-alpha RGBA float tuples in [0, 1].
"""
from __future__ import annotations

import math
import re
from fractions import Fraction
from typing import Callable, Mapping

RGBA = tuple[float, float, float, float]

_VAR = re.compile(r"var\(--([A-Za-z0-9_./#\-]+)\)")
_URL = re.compile(r"url\(#([A-Za-z_][A-Za-z0-9_.\-]*)\)")
_LEN = re.compile(r"^(-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))(%|vw|vh|vmin|vmax)?$")


def resolve_var(s: str, tokens: Mapping[str, str], depth: int = 0) -> str:
    """Replace var(--name) references with style token values (recursively)."""
    if "var(" not in s or depth > 16:
        return s
    return resolve_var(_VAR.sub(lambda m: tokens.get(m.group(1), m.group(0)), s), tokens, depth + 1)


def parse_color(s: str | None, tokens: Mapping[str, str] | None = None, default: RGBA = (0, 0, 0, 0)) -> RGBA:
    if s is None:
        return default
    s = resolve_var(s.strip(), tokens or {})
    if s.startswith("#"):
        h = s[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (r, g, b, a)
    try:
        parts = [float(p) for p in s.split(",")]
    except ValueError:
        return default
    if len(parts) == 3:
        parts.append(1.0)
    if len(parts) != 4:
        return default
    return tuple(min(1.0, max(0.0, p)) for p in parts)  # type: ignore[return-value]


def paint_ref(s: str | None) -> str | None:
    """Return the paints/* id for a url(#id) paint, else None."""
    if not s:
        return None
    m = _URL.fullmatch(s.strip())
    return m.group(1) if m else None


def parse_length(s, parent: float = 0.0, frame: tuple[float, float] = (0.0, 0.0), default: float = 0.0) -> float:
    """lengthType: pixels, or %, vw, vh, vmin, vmax (percent of the parent box)."""
    if s is None:
        return default
    if isinstance(s, (int, float)):
        return float(s)
    m = _LEN.match(str(s).strip())
    if not m:
        try:
            return float(s)
        except ValueError:
            return default
    v, unit = float(m.group(1)), m.group(2)
    fw, fh = frame
    return {None: v, "%": v / 100 * parent, "vw": v / 100 * fw, "vh": v / 100 * fh,
            "vmin": v / 100 * min(fw, fh), "vmax": v / 100 * max(fw, fh)}[unit]


def parse_point(s: str | None, default=(0.0, 0.0)) -> tuple[float, float]:
    if not s:
        return default
    a, _, b = s.partition(",")
    return float(a), float(b)


def parse_numbers(s: str | None) -> list[float]:
    if not s:
        return []
    return [float(v) for v in re.split(r"[\s,]+", s.strip()) if v]


def parse_fps(s) -> Fraction:
    return Fraction(str(s)) if s is not None else Fraction(30)


def parse_bool(s, default: bool = False) -> bool:
    if s is None:
        return default
    if isinstance(s, bool):
        return s
    return str(s).strip() in ("true", "1")


def parse_float(s, default: float = 0.0) -> float:
    if s is None:
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def parse_timecode(s: str, fps: Fraction) -> float:
    hh, mm, ss, ff = (int(v) for v in re.split(r"[:;]", s))
    return hh * 3600 + mm * 60 + ss + ff / float(fps)


def lerp_color(a: RGBA, b: RGBA, u: float) -> RGBA:
    return tuple(x + (y - x) * u for x, y in zip(a, b))  # type: ignore[return-value]


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def deg(v: float) -> float:
    return math.radians(v)


Parser = Callable[[str], object]
