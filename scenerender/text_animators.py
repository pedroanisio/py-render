"""Text animators (range / wiggly / expression selectors, 24 presets) and text on a path.

Model
-----
Each `textAnimator` on a text layer selects *units* (character, character-no-space,
word, line or span; optionally only spans whose @role equals @span) and gives every
unit a selection amount s. Each property offset on the animator is applied to the
unit's clusters scaled by s; animators apply in document order.

Range selector (After Effects semantics). Units are ordered by @order (forward,
reverse, center-out, edges-in, random seeded by @seed); unit i covers [p_i, p_i + 1]
where p_i is its position in that order. The range is [start + offset, end + offset],
in percent of the unit count (rangeUnits=percent) or in unit indices (index).
  square     fractional overlap of the unit with the range; smoothness 0 selects a
             unit only when its centre is inside, 1 (default) uses the overlap
  ramp-up    0 before the range, rising linearly to 1 at its end, 1 after it
  ramp-down  the mirror of ramp-up
  triangle / round / smooth   0 outside; a peak / semicircle / raised cosine inside
easeHigh / easeLow (percent) flatten the curve towards 1 / towards 0. amount (percent)
multiplies the result. selector=wiggly multiplies it by smooth seeded noise in [-1, 1]
changing at @wiggleRate per second. selector=expression (or an <expression
property="selector">) evaluates the expression per unit with textIndex (1-based),
textTotal and value = the range amount in percent; the result is a percent.
An <expression> on an offset property (e.g. property="y") is evaluated per unit too.

Timed selection. When @stagger is set, or for presets, each unit gets its own window:
with M position slots, duration D = presetDuration (default 1 s) and overlap o, a unit
lasts d = D / (1 + (M - 1)(1 - o)) and starts (1 - o)·d after the previous one
(explicit @stagger sets that step and d = stagger / (1 - o)). The window starts at
@presetStart (default: the layer's start), on the layer's clock (composition time,
or symbol time inside an instance). Progress q in [0, 1] is eased; "in" animators
select s = 1 - ease(q) (units rest in the offset state until their window starts),
"out" animators s = ease(q).

Property offsets and combine
----------------------------
Additive (value · s): x, y, rotation, skew, strokeWidth, blur, baselineShift (px, up),
tracking (1/1000 em, accumulated along the line like AE tracking and re-aligned),
lineSpacing (px per line index), characterOffset (shifts letters within a-z / A-Z and
digits within 0-9), anchorX/anchorY (move the pivot), zDepth (px, > 0 away from the
viewer), rotationX / rotationY (degrees; > 0 tilts the unit's top edge / turns its right
edge away, like threeD layers).
Factors (1 + (value - 1) · s): scale, scaleX, scaleY, opacity.
fill / stroke mix the unit's colour towards the value by s; variation interpolates
font axes from the run's own values (wght from weight, wdth 100, opsz from the size,
others 0) to the value.

Variable-font variation: a line with animated axes is reshaped as one Pango layout with
per-range font variations (attributes), so every unit takes the advance its axes give it
and the following units move along (continuous advances, no gaps or overlaps); shaping
and kerning are kept inside every run of equal axes (HarfBuzz cannot kern across two font
instances). The line keeps its static line break and alignment (start / center / end of
the reshaped width inside the static line box; justified lines align to the start), as
After Effects never re-wraps paragraph text for animators.

Per-unit 3D (zDepth, rotationX, rotationY): as in After Effects, a text layer whose
animators use any of them is a 3D layer. Every unit is a plane in the camera space of
scenerender.camera (document px, origin at the frame centre, +Y up, +Z toward the viewer):
its 2D offsets (x, y, rotation, scale, skew) apply first, then the plane is pushed to
zDepth and rotated about the unit's pivot, X then Y, and projected through the active
camera (camera.camera_at; with no active camera the default 50 mm camera, which leaves the
zDepth 0 plane unchanged). Units without 3D offsets form the layer plane at zDepth 0,
projected the same way. When the layer is itself projected by the compositor (threeD, or
inside a threeD group) the units use the default camera, i.e. perspective within the
layer plane, and the compositor then projects that plane. If scenerender.camera offers no
camera API, a default perspective is used (focal length = frame diagonal, principal point
at the frame centre, eye at z = f). Each unit is drawn flat on its own tile (fill, stroke,
shadow, blur) and warped with the exact homography of its plane (raster.warp_projective);
units reaching behind the camera's near plane are culled. Tiles are composited back to
front by the camera depth of their pivot (painter's algorithm).
combine=add sums additive offsets and multiplies factors; multiply multiplies the
accumulated value of each property by 1 + (value - 1) · s (a mask when value is 0);
replace moves the accumulated value towards the value by s.
The pivot of a unit is the centre of its box on the line (logical line height).

Presets (expansions onto the same machinery; em = the text size after autoFit; an
explicit attribute, including @unit and @overlap, overrides the preset's value):
  preset            unit       mode    offsets at s=1                           ease        overlap
  typewriter        character  step    opacity 0                                -           -
  fade-in           character  in      opacity 0                                quad-out    0.6
  fade-out          character  out     opacity 0                                quad-in     0.6
  word-by-word      word       step    opacity 0                                -           -
  letter-by-letter  character  in      opacity 0, y +0.15em                     cubic-out   0.5
  line-by-line      line       in      opacity 0, y +0.4em                      cubic-out   0.3
  slide-up          word       in      opacity 0, y +0.8em                      cubic-out   0.5
  slide-down        word       in      opacity 0, y -0.8em                      cubic-out   0.5
  slide-left        word       in      opacity 0, x +1em                        cubic-out   0.5
  slide-right       word       in      opacity 0, x -1em                        cubic-out   0.5
  pop               word       in      scale 0 (back-out), opacity 0            back-out    0.5
  scale-in          word       in      scale 0, opacity 0                       cubic-out   0.5
  blur-in           word       in      blur 0.35em, opacity 0                   cubic-out   0.6
  wave              character  wave    y -0.25em * sin(2π(1.5·t - p/8)), faded in/out over 10% of D
  bounce            character  in      y -1em (bounce-out), opacity 0 (quad-out) bounce-out  0.6
  spin              character  in      rotation -180, scale 0.3, opacity 0      cubic-out   0.5
  ascend            character  in      y +0.5em, opacity 0                      expo-out    0.8
  shift             character  in      x -0.4em, opacity 0                      expo-out    0.7
  scramble          character  reveal  hidden before start; then unrevealed units show seeded random
                                       letters/digits (20 per second), unit p resolving at start + (p + 1)·D/M
  counter           -          text    every number in the text (or in @span) counts from 0, cubic-out over D
  karaoke           word       out     fill -> @fill (default #FFD400)          linear      0
  highlight         word       out     highlight box (paint @fill, default #FFD40059) wipes in   linear  0
  tracking-in       character  in      tracking +500 (1/1000 em), opacity 0     expo-out    0.85
  mask-reveal       line       in      y +1.1em inside a mask of the unit's line box  cubic-out  0.3
"step" shows unit p from start + p·D/M on (no fade).

Text path: the glyph clusters of each line are placed along `textPath/@path` (layer
coordinates = the text box) by their distance from the line start, following @align:
start from startOffset + firstMargin, center/end against the path length minus the
margins; forceAlignment spreads the line over [firstMargin, length - lastMargin].
reverse runs the path backwards; perpendicular rotates glyphs to the tangent. Beyond
the ends glyphs continue along the end tangents (closed paths wrap). Later lines keep
their offset below the first baseline.
"""
from __future__ import annotations

import math
import random
import re

import numpy as np

from . import curves
from .assets import text as T
from .document import ln
from .registry import FEATURES, FULL, TEXT_ANIMATORS, warn_once
from .values import paint_ref, parse_color

ADD = {"x", "y", "zDepth", "rotation", "rotationX", "rotationY", "skew", "strokeWidth", "tracking", "lineSpacing",
       "blur", "baselineShift", "characterOffset", "anchorX", "anchorY"}
FACTOR = {"scale", "scaleX", "scaleY", "opacity"}
COLOR = {"fill", "stroke"}
PROPS = sorted(ADD | FACTOR | COLOR | {"variation"})
NEUTRAL = {**{p: 0.0 for p in ADD}, **{p: 1.0 for p in FACTOR}}


class P:
    """A preset expansion."""

    def __init__(self, unit, mode, props, ease="linear", overlap=0.0):
        self.unit, self.mode, self.props, self.ease, self.overlap = unit, mode, props, ease, overlap


def em(v):
    return ("em", v)


PRESETS = {
    "typewriter": P("character", "step", {"opacity": 0.0}),
    "fade-in": P("character", "in", {"opacity": 0.0}, "quad-out", 0.6),
    "fade-out": P("character", "out", {"opacity": 0.0}, "quad-in", 0.6),
    "word-by-word": P("word", "step", {"opacity": 0.0}),
    "letter-by-letter": P("character", "in", {"opacity": 0.0, "y": em(0.15)}, "cubic-out", 0.5),
    "line-by-line": P("line", "in", {"opacity": 0.0, "y": em(0.4)}, "cubic-out", 0.3),
    "slide-up": P("word", "in", {"opacity": 0.0, "y": em(0.8)}, "cubic-out", 0.5),
    "slide-down": P("word", "in", {"opacity": 0.0, "y": em(-0.8)}, "cubic-out", 0.5),
    "slide-left": P("word", "in", {"opacity": 0.0, "x": em(1.0)}, "cubic-out", 0.5),
    "slide-right": P("word", "in", {"opacity": 0.0, "x": em(-1.0)}, "cubic-out", 0.5),
    "pop": P("word", "in", {"scale": 0.0, "opacity": (0.0, "cubic-out")}, "back-out", 0.5),
    "scale-in": P("word", "in", {"scale": 0.0, "opacity": 0.0}, "cubic-out", 0.5),
    "blur-in": P("word", "in", {"blur": em(0.35), "opacity": 0.0}, "cubic-out", 0.6),
    "wave": P("character", "wave", {"y": em(-0.25)}),
    "bounce": P("character", "in", {"y": em(-1.0), "opacity": (0.0, "quad-out")}, "bounce-out", 0.6),
    "spin": P("character", "in", {"rotation": -180.0, "scale": 0.3, "opacity": 0.0}, "cubic-out", 0.5),
    "ascend": P("character", "in", {"y": em(0.5), "opacity": 0.0}, "expo-out", 0.8),
    "shift": P("character", "in", {"x": em(-0.4), "opacity": 0.0}, "expo-out", 0.7),
    "scramble": P("character", "scramble", {}),
    "counter": P("character", "counter", {}, "cubic-out"),
    "karaoke": P("word", "out", {"fill": "#FFD400FF"}),
    "highlight": P("word", "out", {"highlight": "#FFD40059"}),
    "tracking-in": P("character", "in", {"tracking": 500.0, "opacity": 0.0}, "expo-out", 0.85),
    "mask-reveal": P("line", "in", {"y": em(1.1), "mask": True}, "cubic-out", 0.3),
}

for _name in PRESETS:
    TEXT_ANIMATORS.register(_name, level=FULL, note="expansion documented in scenerender/text_animators.py")(None)
for _sel in ("range", "wiggly", "expression"):
    TEXT_ANIMATORS.register(f"selector:{_sel}", level=FULL)(None)
TEXT_ANIMATORS.register("property:variation", level=FULL,
                        note="line reshaped with per-range font variations; continuous advances")(None)
TEXT_ANIMATORS.register("property:zDepth", "property:rotationX", "property:rotationY", level=FULL,
                        note="per-unit planes projected by the active camera, drawn with warp_projective")(None)
FEATURES.declare("textAnimator", FULL)
FEATURES.declare("textPath", FULL)


# ---------------------------------------------------------------- selection
def _explicit(an, k):
    return an.get(k) is not None


def _positions(rc, an, n: int) -> np.ndarray:
    order = an.get("order", "forward")
    i = np.arange(n, dtype=np.float64)
    if order == "reverse":
        return (n - 1) - i
    if order == "center-out":
        return np.abs(i - (n - 1) / 2.0) * 2.0
    if order == "edges-in":
        return (n - 1) - np.abs(i - (n - 1) / 2.0) * 2.0
    if order == "random":
        perm = np.random.RandomState(rc.ev.seed_for(an, "order") % (2 ** 32)).permutation(n)
        return perm.astype(np.float64)
    return i


def _ease_hl(v: np.ndarray, eh: float, el: float) -> np.ndarray:
    if eh:
        v = v + eh * ((1 - (1 - v) ** 2) - v)
    if el:
        v = v + el * (v * v - v)
    return v


def range_selection(rc, an, ctx, pos: np.ndarray, n: int) -> np.ndarray:
    ev = rc.ev
    start = ev.num(an, "start", ctx, 0.0)
    end = ev.num(an, "end", ctx, 100.0)
    off = ev.num(an, "offset", ctx, 0.0)
    if an.get("rangeUnits", "percent") == "percent":
        k = n / 100.0
        s0, s1 = (start + off) * k, (end + off) * k
    else:
        s0, s1 = start + off, end + off
    s0, s1 = min(s0, s1), max(s0, s1)
    shape = an.get("shape", "square")
    if shape == "square":
        cov = np.clip(np.minimum(pos + 1, s1) - np.maximum(pos, s0), 0, 1)
        hard = ((pos + 0.5 >= s0) & (pos + 0.5 < s1)).astype(np.float64)
        sm = min(1.0, max(0.0, ev.num(an, "smoothness", ctx, 1.0)))
        v = hard + (cov - hard) * sm
    else:
        span = max(1e-9, s1 - s0)
        u = (pos + 0.5 - s0) / span
        inside = (u >= 0) & (u <= 1)
        uc = np.clip(u, 0, 1)
        if shape == "ramp-up":
            v = np.where(u < 0, 0.0, np.where(u > 1, 1.0, uc))
        elif shape == "ramp-down":
            v = np.where(u < 0, 1.0, np.where(u > 1, 0.0, 1 - uc))
        elif shape == "triangle":
            v = np.where(inside, 1 - np.abs(2 * uc - 1), 0.0)
        elif shape == "round":
            v = np.where(inside, np.sqrt(np.maximum(0, 1 - (2 * uc - 1) ** 2)), 0.0)
        elif shape == "smooth":
            v = np.where(inside, 0.5 - 0.5 * np.cos(2 * math.pi * uc), 0.0)
        else:
            warn_once("textAnimator", f"shape:{shape}", "unknown shape; using square")
            v = np.clip(np.minimum(pos + 1, s1) - np.maximum(pos, s0), 0, 1)
    eh = ev.num(an, "easeHigh", ctx, 0.0) / 100.0
    el = ev.num(an, "easeLow", ctx, 0.0) / 100.0
    return _ease_hl(v, eh, el)


def _noise1(seed: int, x: np.ndarray) -> np.ndarray:
    """Smooth value noise in [-1, 1]."""
    i0 = np.floor(x).astype(np.int64)
    f = x - i0

    def h(i):
        v = (i * 374761393 + seed * 668265263) & 0xFFFFFFFF
        v = ((v ^ (v >> 13)) * 1274126177) & 0xFFFFFFFF
        return (v & 0xFFFF) / 32767.5 - 1.0
    u = f * f * (3 - 2 * f)
    return h(i0) * (1 - u) + h(i0 + 1) * u


def _timing(rc, an, ctx, preset: P | None, pos: np.ndarray):
    ev = rc.ev
    t0 = ev.num(an, "presetStart", ctx, ctx.node_start) if _explicit(an, "presetStart") else ctx.node_start
    D = ev.num(an, "presetDuration", ctx, 1.0) if _explicit(an, "presetDuration") else 1.0
    o = ev.num(an, "overlap", ctx, 0.0) if _explicit(an, "overlap") else (preset.overlap if preset else 0.0)
    o = min(1.0, max(0.0, o))
    slots = float(pos.max()) + 1 if len(pos) else 1.0
    if _explicit(an, "stagger"):
        step = ev.num(an, "stagger", ctx, 0.0)
        d = step / (1 - o) if o < 1 else D
    else:
        d = D / (1 + (slots - 1) * (1 - o))
        step = d * (1 - o)
    return t0, D, d, step, slots


# ---------------------------------------------------------------- evaluation
class Anim:
    """Per-cluster accumulated state for one frame."""

    def __init__(self, n: int):
        self.st: list[dict] = [dict() for _ in range(n)]
        self.owner: list[tuple] = [() for _ in range(n)]
        self.scramble: dict[int, int] = {}


def _prop_value(rc, block, an, prop, ctx, preset: P | None, per_unit_ctx=None):
    """(value, ease) of a property offset: explicit attribute (or animation) > preset."""
    has_anim = bool(rc.ev._anims(an, prop))
    if an.get(prop) is not None or has_anim:
        c = per_unit_ctx or ctx
        if prop in COLOR:
            return rc.ev.str(an, prop, c), None
        if prop == "variation":
            return rc.ev.str(an, prop, c), None
        return rc.ev.num(an, prop, c, 0.0), None
    if preset is None or prop not in preset.props:
        return None, None
    v = preset.props[prop]
    ease = None
    if isinstance(v, tuple) and len(v) == 2 and v[0] != "em":
        v, ease = v
    if isinstance(v, tuple) and v[0] == "em":
        v = v[1] * block.size
    return v, ease


def _mix(a, b, s):
    s = min(1.0, max(0.0, s))
    return tuple(x + (y - x) * s for x, y in zip(a, b))


def _base_color(rc, block, ci, key):
    st = block.run_style(ci)
    p = st.get("color") if key == "fill" else st.get("strokeColor")
    if p is None:
        return (1.0, 1.0, 1.0, 1.0) if key == "fill" else (0.0, 0.0, 0.0, 0.0)
    if paint_ref(str(p)):
        from .paint import paint_color_estimate
        return paint_color_estimate(rc, str(p), None)
    return parse_color(str(p), rc.doc.tokens, (1, 1, 1, 1))


def _base_axes(block, ci) -> dict:
    st = block.run_style(ci)
    axes = T.parse_axes(st.get("variation")) if st.get("variation") else {}
    return axes


def _apply(rc, block, acc: dict, ci: int, prop: str, v, s: float, mode: str):
    if prop in ADD or prop in FACTOR:
        a = acc.get(prop, NEUTRAL[prop])
        v = float(v)
        if mode == "replace":
            acc[prop] = a + (v - a) * s
        elif mode == "multiply" or prop in FACTOR:
            acc[prop] = a * (1 + (v - 1) * s)
        else:
            acc[prop] = a + v * s
    elif prop in COLOR:
        target = parse_color(str(v), rc.doc.tokens, (1, 1, 1, 1))
        a = acc.get(prop) or _base_color(rc, block, ci, prop)
        if mode == "multiply":
            acc[prop] = tuple(x * (1 + (y - 1) * min(1.0, max(0.0, s))) for x, y in zip(a, target))
        else:
            acc[prop] = _mix(a, target, s)
    elif prop == "variation":
        target = T.parse_axes(v)
        base = acc.get("variation") or {}
        own = _base_axes(block, ci)
        out = dict(base)
        for ax, tv in target.items():
            b0 = base.get(ax, own.get(ax))
            if b0 is None:
                if ax == "wght":
                    b0 = T._f(block.run_style(ci).get("weight"), 400.0)
                elif ax == "wdth":
                    b0 = 100.0
                elif ax == "opsz":
                    b0 = T._f(block.run_style(ci).get("size"), 32.0) * block.k
                else:
                    b0 = 0.0
            out[ax] = b0 + (tv - b0) * s
        acc["variation"] = out
    elif prop == "highlight":
        if s > 1e-4:
            acc["highlight"] = (str(v), min(1.0, s), 0.0, 0.0)
    elif prop == "mask":
        acc["mask"] = True


def evaluate(rc, block, anims, ctx) -> Anim | None:
    n = len(block.clusters)
    A = Anim(n)
    any_active = False
    for ai, an in enumerate(anims):
        preset_name = an.get("preset")
        preset = PRESETS.get(preset_name) if preset_name else None
        if preset_name and preset is None:
            warn_once("textAnimator", preset_name)
            continue
        if preset is not None and preset.mode == "counter":
            continue
        unit = an.get("unit") if _explicit(an, "unit") else (preset.unit if preset else "character")
        units = block.units(unit, an.get("span"))
        N = len(units)
        if N == 0:
            continue
        pos = _positions(rc, an, N)
        amount = rc.ev.num(an, "amount", ctx, 100.0) / 100.0
        mode = an.get("combine", "add")
        timed = preset is not None or _explicit(an, "stagger")
        props = [p for p in PROPS + ["highlight", "mask"] if (preset and p in preset.props) or an.get(p) is not None
                 or (p in PROPS and rc.ev._anims(an, p))]
        # highlight preset: @fill names the highlight paint
        if preset_name == "highlight":
            props = [p for p in props if p != "fill"]
        sel_q = None
        if timed:
            t0, D, d, step, slots = _timing(rc, an, ctx, preset, pos)
            pm = preset.mode if preset else "in"
            t = ctx.t
            if pm == "step":
                st = D / max(1.0, slots)
                base_s = np.where(t >= t0 + pos * st - 1e-9, 0.0, 1.0)
            elif pm == "wave":
                if t < t0 or t > t0 + D:
                    continue
                env = min(1.0, (t - t0) / max(1e-9, 0.1 * D), (t0 + D - t) / max(1e-9, 0.1 * D))
                base_s = np.sin(2 * math.pi * (1.5 * (t - t0) - pos / 8.0)) * env
            elif pm == "scramble":
                st = D / max(1.0, slots)
                unrevealed = t < t0 + (pos + 1) * st
                if t < t0:
                    any_active = True
                    for u in units:
                        for ci in u:
                            A.st[ci]["opacity"] = 0.0
                    continue
                if unrevealed.any():
                    any_active = True
                    tick = int(math.floor(t * 20))
                    for ui, u in enumerate(units):
                        if unrevealed[ui]:
                            for ci in u:
                                A.scramble[ci] = tick
                continue
            else:
                q = np.clip((t - t0 - pos * step) / max(1e-9, d), 0.0, 1.0) if d > 0 else (t >= t0 + pos * step).astype(float)
                sel_q = (q, pm)
                base_s = None
        else:
            base_s = range_selection(rc, an, ctx, pos, N)
            if an.get("selector") == "wiggly":
                rate = rc.ev.num(an, "wiggleRate", ctx, 2.0)
                base_s = base_s * _noise1(rc.ev.seed_for(an, "wiggly") & 0xFFFF, ctx.t * rate + np.arange(N) * 7.31)
        sel_expr = next((c for c in an if ln(c) == "expression" and c.get("property") == "selector"
                         and c.get("enabled", "true") != "false"), None)
        if sel_expr is not None or an.get("selector") == "expression":
            if sel_expr is None:
                warn_once("textAnimator", "selector:expression", "expression selector without <expression property=\"selector\">")
            else:
                src = sel_expr.text or ""
                vals = np.zeros(N)
                for i in range(N):
                    c2 = ctx.with_vars(textIndex=float(i + 1), textTotal=float(N))
                    cur = (base_s[i] if base_s is not None else 1.0) * 100.0
                    try:
                        vals[i] = float(rc.ev.run_expression(src, an, "selector", c2, cur, seed=sel_expr.get("seed"))) / 100.0
                    except Exception as e:  # noqa: BLE001
                        warn_once("textAnimator", "selector-expression", f"failed: {e}")
                        vals[i] = cur / 100.0
                base_s = vals
                sel_q = None
        per_unit = {p for p in props if any(ln(c) == "expression" and c.get("property") == p for c in an)}
        static_vals = {p: _prop_value(rc, block, an, p, ctx, preset) for p in props if p not in per_unit}
        if preset_name == "highlight" and an.get("fill"):
            static_vals["highlight"] = (rc.ev.str(an, "fill", ctx), None)
        for ui, u in enumerate(units):
            for p in props:
                if p in per_unit:
                    c2 = ctx.with_vars(textIndex=float(ui + 1), textTotal=float(N))
                    v, ease = _prop_value(rc, block, an, p, ctx, preset, c2)
                else:
                    v, ease = static_vals[p]
                if v is None:
                    continue
                if sel_q is not None:
                    q, pm = sel_q
                    e = curves.get(ease or (preset.ease if preset else "linear"))(float(q[ui]))
                    s = (1.0 - e) if pm == "in" else e
                else:
                    s = float(base_s[ui])
                s *= amount
                if abs(s) < 1e-6:
                    continue
                any_active = True
                for ci in u:
                    _apply(rc, block, A.st[ci], ci, p, v, s, mode)
                    A.owner[ci] = A.owner[ci] + ((ai, ui),)
    return A if any_active else None


# ---------------------------------------------------------------- counter / character substitution
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def apply_counter(rc, spec, anims, ctx):
    for an in anims:
        if an.get("preset") != "counter":
            continue
        ev = rc.ev
        t0 = ev.num(an, "presetStart", ctx, ctx.node_start) if _explicit(an, "presetStart") else ctx.node_start
        D = ev.num(an, "presetDuration", ctx, 1.0) if _explicit(an, "presetDuration") else 1.0
        e = curves.get("cubic-out")(min(1.0, max(0.0, (ctx.t - t0) / max(1e-9, D))))
        e *= ev.num(an, "amount", ctx, 100.0) / 100.0
        if e >= 1.0:
            continue
        role = an.get("span")

        def fmt(m):
            s = m.group(0)
            dec = len(s.split(".")[1]) if "." in s else 0
            v = float(s.replace(",", "")) * e
            out = f"{v:,.{dec}f}" if "," in s else f"{v:.{dec}f}"
            return out
        runs = [r.with_text(_NUM.sub(fmt, r.text)) if (role is None or r.role == role) else r for r in spec.runs]
        spec = spec.with_runs(runs)
    return spec


_LOWER = "abcdefghijklmnopqrstuvwxyz"
_UPPER = _LOWER.upper()
_DIGITS = "0123456789"


def _shift_char(c: str, k: int) -> str:
    for alpha in (_LOWER, _UPPER, _DIGITS):
        i = alpha.find(c)
        if i >= 0:
            return alpha[(i + k) % len(alpha)]
    if c.isalpha() and k:
        o = ord(c) + k
        if 0 < o < 0x110000 and chr(o).isprintable():
            return chr(o)
    return c


def substitutions(rc, block, A: Anim, seed: int) -> dict[int, str]:
    subs = {}
    for ci, (a, b) in enumerate(block.clusters):
        if b - a != 1:
            continue
        c = block.text[a]
        if c.isspace():
            continue
        k = int(round(A.st[ci].get("characterOffset", 0.0)))
        if ci in A.scramble:
            r = random.Random(hash((seed, ci, A.scramble[ci])) & 0xFFFFFFFF)
            pool = _UPPER if c.isupper() else _DIGITS if c.isdigit() else _LOWER if c.isalpha() else None
            if pool:
                subs[a] = r.choice(pool)
        elif k:
            n = _shift_char(c, k)
            if n != c:
                subs[a] = n
    return subs


def substituted_spec(spec, block, subs: dict[int, str]):
    runs, acc = [], 0
    for r in spec.runs:
        chars = list(r.text)
        for i in range(len(chars)):
            if acc + i in subs:
                chars[i] = subs[acc + i]
        runs.append(r.with_text("".join(chars)))
        acc += len(r.text)
    return spec.with_runs(runs)


# ---------------------------------------------------------------- text path
class _PathPlacer:
    def __init__(self, rc, tp, ctx, block):
        from .geometry import PathSampler
        d = rc.ev.str(tp, "path", ctx) or ""
        key = ("textpath", d)
        s = rc.cache.get(key)
        if s is None:
            s = PathSampler(d)
            rc.cache[key] = s
        self.s = s
        self.L = s.total
        self.closed = bool(re.search(r"[zZ]\s*$", d.strip()))
        ev = rc.ev
        self.start = ev.length(tp, "startOffset", ctx, self.L, 0.0)
        self.first = ev.num(tp, "firstMargin", ctx, 0.0)
        self.last = ev.num(tp, "lastMargin", ctx, 0.0)
        self.reverse = ev.bool(tp, "reverse", ctx, False)
        self.perp = ev.bool(tp, "perpendicular", ctx, True)
        self.force = ev.bool(tp, "forceAlignment", ctx, False)
        self.align = block.spec.b("align")
        self.Oinv = np.linalg.inv(block.O)
        if T._is_vertical(block.spec):
            warn_once("textPath", "vertical", "textPath with vertical writing modes places glyphs horizontally")

    def sample(self, d: float):
        L = self.L
        if L <= 0:
            x, y, a = self.s.at(0)
            return x, y, a
        if self.reverse:
            d = L - d
        if self.closed:
            d %= L
        if d < 0:
            x, y, a = self.s.at(0)
            r = math.radians(a)
            x, y = x + d * math.cos(r), y + d * math.sin(r)
        elif d > L:
            x, y, a = self.s.at(1)
            r = math.radians(a)
            x, y = x + (d - L) * math.cos(r), y + (d - L) * math.sin(r)
        else:
            x, y, a = self.s.at_distance(d)
        if self.reverse:
            a += 180.0
        return x, y, a

    def distance(self, line, cx: float) -> float:
        rel = cx - line.x
        L = self.L
        if self.force:
            span = max(1e-9, L - self.first - self.last)
            return self.first + rel / max(1e-9, line.w) * span
        if self.align == "center":
            return self.start + (L - line.w) / 2 + (self.first - self.last) / 2 + rel
        if self.align == "end":
            return self.start + L - self.last - line.w + rel
        return self.start + self.first + rel

    def matrix(self, line, cx: float, baseline0: float) -> np.ndarray:
        x, y, a = self.sample(self.distance(line, cx))
        p = self.Oinv @ np.array([x, y, 1.0])
        R = _rot(a if self.perp else 0.0)
        return _tr(p[0], p[1]) @ R @ _tr(-cx, -baseline0)


def _tr(x, y):
    return np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], np.float64)


def _rot(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float64)


def _skew(deg):
    return np.array([[1, math.tan(math.radians(deg)), 0], [0, 1, 0], [0, 0, 1]], np.float64)


def _sc(sx, sy):
    return np.array([[sx, 0, 0], [0, sy, 0], [0, 0, 1]], np.float64)


# ---------------------------------------------------------------- pieces
def _state_key(st: dict):
    out = []
    for k in sorted(st):
        v = st[k]
        if isinstance(v, dict):
            v = tuple(sorted((a, round(b, 3)) for a, b in v.items()))
        elif isinstance(v, float):
            v = round(v, 5)
        elif isinstance(v, tuple):
            v = tuple(round(x, 5) if isinstance(x, float) else x for x in v)
        out.append((k, v))
    return tuple(out)


def build_pieces(rc, block, states: list[dict] | None, owners=None, placer: _PathPlacer | None = None) -> list:
    """Pieces for per-cluster states (dicts of accumulated properties; None = neutral)."""
    n = len(block.clusters)
    states = list(states or [{} for _ in range(n)])
    owners = list(owners or [() for _ in range(n)])
    for i in range(1, n):
        if block.is_shy(i):          # the break hyphen moves with the letter before it
            states[i], owners[i] = states[i - 1], owners[i - 1]
    pieces = []
    baseline0 = next((l_.baseline for l_ in block.lines if not l_.hidden), 0.0)
    align = block.spec.b("align")
    for li, line in enumerate(block.lines):
        if line.hidden:
            continue
        cl = [i for i in range(n) if block.cl_line[i] == li and not block.is_newline(i)]
        if not cl:
            continue
        # tracking accumulates along the line
        shifts = {}
        tr_total = 0.0
        has_tr = any(states[i].get("tracking") for i in cl)
        if has_tr:
            for i in cl:
                shifts[i] = tr_total
                tr_total += states[i].get("tracking", 0.0) / 1000.0 * T._f(block.run_style(i).get("size"), 32.0) * block.k
            comp = {"center": -tr_total / 2, "end": -tr_total}.get(align, 0.0)
            for i in cl:
                shifts[i] += comp
        vx = (T.varied_positions(block, cl, [states[i].get("variation") for i in cl])
              if any(states[i].get("variation") for i in cl) else None)
        split = has_tr or placer is not None
        groups: list[list[int]] = []
        prev_key = None
        word = 0
        for i in cl:
            if align == "justify" and block.is_space(i):
                word += 1          # justified spaces stretch: never lay a gap out on its own
            key = (_state_key(states[i]), owners[i], word)
            if groups and not split and key == prev_key:
                groups[-1].append(i)
            else:
                groups.append([i])
            prev_key = key
        for gi, g in enumerate(groups):
            st = states[g[0]]
            a, b = g[0], g[-1]
            x0, x1 = block.span_x(a, b)
            move = 0.0
            if vx is not None:           # reshaped line: the unit's own advance and position
                v0, v1 = min(vx[i][0] for i in g), max(vx[i][1] for i in g)
                move, x0, x1 = v0 - x0, v0, v1
            px = (x0 + x1) / 2 + st.get("anchorX", 0.0)
            py = line.y + line.h / 2 + st.get("anchorY", 0.0)
            dx = st.get("x", 0.0) + shifts.get(a, 0.0)
            dy = st.get("y", 0.0) - st.get("baselineShift", 0.0) + st.get("lineSpacing", 0.0) * li
            sc = st.get("scale", 1.0)
            M = _tr(px + dx, py + dy) @ _rot(st.get("rotation", 0.0))
            if st.get("skew"):
                M = M @ _skew(st["skew"])
            M = M @ _sc(sc * st.get("scaleX", 1.0), sc * st.get("scaleY", 1.0)) @ _tr(-px, -py) @ _tr(move, 0.0)
            if placer is not None:
                cx = (x0 + x1) / 2 + shifts.get(a, 0.0)
                M = placer.matrix(line, cx, baseline0) @ _tr(-shifts.get(a, 0.0), 0) @ M
            var = st.get("variation")
            z, rxd, ryd = st.get("zDepth", 0.0), st.get("rotationX", 0.0), st.get("rotationY", 0.0)
            three = None
            if abs(z) > 1e-6 or abs(rxd) > 1e-6 or abs(ryd) > 1e-6:
                piv = M @ np.array([px - move, py, 1.0])        # the pivot where the unit is drawn
                three = (z, rxd, ryd, float(piv[0]), float(piv[1]))
            pieces.append(T.Piece(
                li, a, b, M, opacity=max(0.0, min(1.0, st.get("opacity", 1.0))),
                fill=st.get("fill"), stroke=st.get("stroke"), stroke_add=st.get("strokeWidth", 0.0),
                blur=max(0.0, st.get("blur", 0.0)), highlight=st.get("highlight"), mask=bool(st.get("mask")),
                variation=tuple(sorted(var.items())) if var else None,
                first=(gi == 0), last=(gi == len(groups) - 1), three_d=three))
    return pieces


# ---------------------------------------------------------------- per-unit 3D
def _in_plane(rc, layer) -> bool:
    """The layer is itself projected as a plane by the compositor (threeD, in a collapsed 3D group,
    or flattened into a threeD ancestor)."""
    try:
        from . import camera as CAM
        return bool(CAM.is_threed(rc, layer) or CAM._flattening_ancestor(layer))
    except (ImportError, AttributeError):
        p = layer
        while p is not None and isinstance(p.tag, str):
            if p.get("threeD") == "true":
                return True
            p = p.getparent()
        return False


def _projector(rc, t: float, in_plane: bool = False):
    """(frame_to_world(q_doc, z), project(P) -> (screen doc px, depth), near) for the camera filming
    the units: the active camera, or the default camera when the layer is projected as a plane."""
    try:
        from . import camera as CAM
        cam = CAM.build_camera(rc, None, t) if in_plane else CAM.camera_at(rc, t)
        f2w = CAM.frame_to_world

        def project(P):
            C = cam.to_cam(P)
            return cam.project_cam(C), (np.ones(C.shape[:-1]) if cam.ortho else C[..., 2])
        return (lambda q, z: f2w(rc, q, z)), project, (-np.inf if cam.ortho else cam.near)
    except (ImportError, AttributeError):
        W, H = float(rc.doc.width), float(rc.doc.height)
        f = math.hypot(W, H)

        def f2w_default(q, z):
            q = np.asarray(q, np.float64)
            return np.stack([q[..., 0] - W / 2, H / 2 - q[..., 1], np.broadcast_to(-float(z), q.shape[:-1])], -1)

        def project_default(P):
            d = f - P[..., 2]
            dd = np.maximum(d, 1e-9)
            return np.stack([W / 2 + f * P[..., 0] / dd, H / 2 - f * P[..., 1] / dd], -1), d
        return f2w_default, project_default, 0.1


def _plane_rot(rxd: float, ryd: float) -> np.ndarray:
    """rotationX (> 0: top edge away) then rotationY (> 0: right edge away), world axes."""
    a = math.radians(-rxd)
    ca, sa = math.cos(a), math.sin(a)
    b = math.radians(ryd)
    cb, sb = math.cos(b), math.sin(b)
    return np.array([[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]]) @ np.array([[1, 0, 0], [0, ca, -sa], [0, sa, ca]])


def homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """3x3 H with dst ~ H @ src for 4 point pairs (exact solve)."""
    A, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        A += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        rhs += [u, v]
    h = np.linalg.solve(np.array(A, np.float64), np.array(rhs, np.float64))
    return np.append(h, 1.0).reshape(3, 3)


def unit_homography(rc, block, M: np.ndarray, p, t: float, in_plane: bool = False):
    """(H: flat frame px -> projected frame px, camera depth of the pivot) of a 3D piece, or None
    when a corner is behind the near plane."""
    f2w, project, near = _projector(rc, t, in_plane)
    z, rxd, ryd, pvx, pvy = p.three_d
    ln_ = block.lines[p.line]
    x0, x1 = block.span_x(p.a, p.b)
    x1 = max(x1, x0 + 1.0)
    y0, y1 = ln_.y, ln_.y + max(ln_.h, 1.0)
    L = M @ block.O
    loc = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], np.float64)
    fr = loc @ (L @ p.T).T
    fr = fr[:, :2] / fr[:, 2:3]
    pv = L @ np.array([pvx, pvy, 1.0])
    Ri = np.linalg.inv(rc.root_matrix)

    def to_doc(q):
        d = np.c_[q, np.ones(len(q))] @ Ri.T
        return d[:, :2] / d[:, 2:3]
    Pw = f2w(to_doc(fr), z)
    pw = f2w(to_doc(pv[None, :2] / pv[2]), z)[0]
    Pw = pw + (Pw - pw) @ _plane_rot(rxd, ryd).T
    scr, depth = project(Pw)
    if np.any(depth <= near):
        return None
    dp = float(project(pw[None])[1][0])
    dst = np.c_[scr, np.ones(4)] @ rc.root_matrix.T
    try:
        return homography(fr, dst[:, :2] / dst[:, 2:3]), dp
    except np.linalg.LinAlgError:
        return None


def render_3d(rc, block, M, ctx, pieces, clip, in_plane: bool = False):
    """The flat pieces as the layer plane (zDepth 0, projected like the 3D units), the 3D pieces
    warped one by one; composited back to front."""
    from .blend import composite
    from .raster import warp_projective
    t = ctx.comp_t
    flat = [p for p in pieces if p.three_d is None]
    items = []
    base = T.render_block(rc, block, M, ctx, flat, clip)
    line0 = next((i for i, l_ in enumerate(block.lines) if not l_.hidden), 0)
    ref = T.Piece(line0, 0, max(0, len(block.clusters) - 1), np.eye(3), three_d=(0.0, 0.0, 0.0, 0.0, 0.0))
    hd0 = unit_homography(rc, block, M, ref, t, in_plane) if block.clusters else None
    if base is not None and hd0 is not None:
        H0 = hd0[0]
        if not np.allclose(H0, np.eye(3), atol=1e-7):
            base = warp_projective(base, H0, rc.frame_rect)
        items.append((hd0[1], 0, base))
    for i, p in enumerate(q for q in pieces if q.three_d is not None):
        if p.opacity <= 1e-4:
            continue
        hd = unit_homography(rc, block, M, p, t, in_plane)
        tile = None if hd is None else T.render_block(rc, block, M, ctx, [p], clip, decor=False)
        if tile is not None:
            items.append((hd[1], i + 1, warp_projective(tile, hd[0], rc.frame_rect)))
    out = None
    for _d, _i, buf in sorted((it for it in items if it[2] is not None), key=lambda it: (-it[0], it[1])):
        out = buf.copy() if out is None else composite(out, buf, "normal", 1.0)
    return out


def _has_3d(rc, anims) -> bool:
    return any(an.get(p) is not None or rc.ev._anims(an, p) for an in anims for p in ("zDepth", "rotationX", "rotationY"))


def _neutral(A: Anim) -> bool:
    for st in A.st:
        for k, v in st.items():
            if k in NEUTRAL:
                if abs(v - NEUTRAL[k]) > 1e-6:
                    return False
            else:
                return False
    return not A.scramble


# ---------------------------------------------------------------- entry point
def render_animated(rc, spec, M, ctx, layer, mods, clip):
    anims = [m for m in mods if ln(m) == "textAnimator"]
    tp = next((m for m in mods if ln(m) == "textPath"), None)
    spec = apply_counter(rc, spec, anims, ctx)
    block = T.get_block(rc, spec)
    A = evaluate(rc, block, anims, ctx) if anims else None
    if A is not None and _neutral(A):
        A = None
    if A is not None:
        seed = rc.ev.seed_for(layer, "scramble") ^ sum(int(a.get("seed", 0)) for a in anims)
        subs = substitutions(rc, block, A, seed)
        if subs:
            b2 = T.get_block(rc, substituted_spec(spec, block, subs), force=block)
            if len(b2.clusters) == len(block.clusters):
                block = b2
    unit3d = bool(anims) and _has_3d(rc, anims)
    if A is None and tp is None and not unit3d:
        key = ("text-tile", spec, np.round(M, 6).tobytes(), clip)
        hit = rc.cache.get(key)
        if hit is not None:
            return None if hit is False else hit.copy()
        buf = T.render_block(rc, block, M, ctx, None, clip)
        rc.cache[key] = buf if buf is not None else False
        return None if buf is None else buf.copy()
    placer = _PathPlacer(rc, tp, ctx, block) if tp is not None else None
    pieces = build_pieces(rc, block, A.st if A else None, A.owner if A else None, placer)
    if unit3d:
        return render_3d(rc, block, M, ctx, pieces, clip, _in_plane(rc, layer))
    return T.render_block(rc, block, M, ctx, pieces, clip)
