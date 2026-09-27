"""Text assets: style resolution, Pango layout, fitting and drawing.

A text asset is resolved into a `Spec` (hashable): a tuple of `Run`s (text + fully
resolved character style) and the block parameters. `get_block` lays a spec out
with Pango (cached) and exposes its geometry (lines, grapheme clusters, units) in
*layout coordinates*; `Block.O` maps layout coordinates to the asset box
(0,0)-(width,height). `render_block` draws a block, either in one pass (static
text) or as a list of `Piece`s (runs of clusters on one line with their own
transform, opacity and paint), which is how text animators, text paths and
burned captions draw.

Pinned rules (the schema leaves these open):
  * Style precedence: span attributes > span @style > asset attributes > asset @style >
    schema defaults (so a textStyle colour beats the asset's schema default colour).
  * size is in document pixels (the em). lineHeight is a multiple of each run's size
    (CSS-like absolute line boxes; the tallest run sets the line). tracking is 1/1000 em
    (After Effects); letterSpacing is pixels; both add after every character.
  * baselineShift is pixels, positive = up. strokePosition defaults to center (fill,
    then stroke on top); outside strokes are drawn under the fill at twice the width;
    inside strokes are clipped to the glyphs. shadowBlur is a blur radius (sigma = r/2).
  * verticalAlign positions the logical text block inside the box height.
    wrap=balance keeps the line count of word wrap at the smallest width, aligned
    within the box.
  * hyphenate=true is CSS `hyphens: auto`: every word of 5+ letters (hyphenate-limit-chars
    5 2 2) without an author soft hyphen gets U+00AD soft hyphens at the break points of
    the pyphen (Hunspell/TeX) dictionary for @language (BCP 47, e.g. de-CH -> de_CH, falling
    back to the base language; no @language = en_US; an unknown language warns and only
    the author soft hyphens remain). Pango breaks at a soft hyphen with a visible hyphen
    and prefers those breaks; a word that still cannot fit a line is broken between
    characters with a hyphen (overflow-wrap). hyphenate=false never inserts hyphens and
    hides author soft hyphens at breaks.
  * emoji (Unicode TR51 presentation): "color" appends U+FE0F to every emoji character
    whose default presentation is text (and replaces a U+FE0E), and sets emoji clusters in
    a colour emoji font (Noto Color Emoji, Apple Color Emoji, Segoe UI Emoji, Twemoji);
    "text" appends U+FE0E to every emoji character (replacing U+FE0F) and sets emoji
    clusters in the run's own fonts followed by monochrome symbol fonts (DejaVu Sans, Noto
    Sans Symbols 2, Symbola, Noto Emoji, FreeSerif). A cluster no monochrome font covers is
    drawn as a silhouette of its colour glyph in the text colour, so text presentation is
    always monochrome. Keycap bases (#, *, 0-9) are touched only before U+20E3; skin-tone
    modifiers and regional indicators never get a selector.
  * autoFit scales every run's size (and tracking/letterSpacing/lineHeight with it) by one
    factor: shrink searches [minSize/size, 1], grow [1, maxSize/size], fit both, so the text
    fits width x height, maxLines and (for word wrap) breaks no word.
  * maxLines always limits the visible lines; overflow=ellipsis adds an ellipsis,
    overflow=clip clips drawing to the box.
  * background: block = one box around all lines, line = one per line, word = one per word
    (logical line height, trimmed of trailing spaces), expanded by backgroundPadding and
    rounded by backgroundRadius. Paint gradients span the asset box.
  * writingMode vertical-rl / vertical-lr are CSS writing-mode with text-orientation: mixed.
    The text is laid out with Pango base gravity EAST and the NATURAL gravity hint: lines
    run top to bottom; characters whose Unicode Vertical_Orientation (UTR #50) is U or Tu
    (CJK ideographs, kana, Hangul, fullwidth forms, CJK punctuation) stand upright and are
    shaped top-to-bottom with the font's vertical metrics and 'vert' forms; R and Tr
    characters (Latin, digits, brackets, the long-vowel mark) are set sideways, rotated
    90 degrees clockwise. The box width is the block extent and the height the line
    length; columns progress right to left (rl) or left to right (lr). align runs along
    the column (start = top), verticalAlign across the columns (top = the first column's
    side).
  * Layouts are shaped once in document pixels (unhinted, unrounded, greyscale) and drawn
    under the device matrix without reshaping, so line breaks, fitting and glyph positions
    do not depend on the render scale.
  * Animated units that move independently are drawn as their own sub-layouts placed at
    the full layout's cluster positions (no ink bleeding between neighbours); a piece
    covering a whole line draws the line itself (keeps justification and shaping).
  * first_baseline(): distance from the top of the box to the first visible line's
    baseline, after autoFit and verticalAlign (document px, unscaled asset box). Vertical
    writing modes have no horizontal baseline; the box height is returned (the CSS
    synthesized baseline of a box without one).
"""
from __future__ import annotations

import bisect
import ctypes
import ctypes.util
import logging
import math
import re
from dataclasses import dataclass, field

import cairo
import numpy as np

from .. import paint as paints
from ..document import ln
from ..pango_bridge import Pango, _cairo_ptr, _gptr, _pc, new_layout, register_font_file, resolve_font_face
from ..raster import Buf, Canvas, intersect
from ..registry import ASSET_SIZES, ASSETS, FEATURES, FULL, warn_once
from ..values import paint_ref, parse_color

log = logging.getLogger("scenerender")

FEATURES.declare("text:spans", FULL)
FEATURES.declare("text:autoFit", FULL)
FEATURES.declare("text:background", FULL)
FEATURES.declare("text:writingMode", FULL, "Pango gravity EAST + NATURAL hint: UTR #50 upright CJK, sideways Latin; rl/lr columns")
FEATURES.declare("text:hyphenate", FULL, "pyphen dictionary for @language via soft hyphens; overflow-wrap fallback")
FEATURES.declare("text:emoji", FULL, "U+FE0E/U+FE0F presentation + colour or monochrome fonts; colour-only glyphs as silhouettes")

PS = Pango.SCALE
INF = 1e6

# ---------------------------------------------------------------- ctypes helpers
_pango = ctypes.CDLL(ctypes.util.find_library("pango-1.0") or "libpango-1.0.so.0")
_pango.pango_layout_get_line_readonly.restype = ctypes.c_void_p
_pango.pango_layout_get_line_readonly.argtypes = [ctypes.c_void_p, ctypes.c_int]
_pango.pango_font_description_set_weight.argtypes = [ctypes.c_void_p, ctypes.c_int]
_pango.pango_layout_get_log_attrs_readonly.restype = ctypes.POINTER(ctypes.c_uint32)
_pango.pango_layout_get_log_attrs_readonly.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
_pc.pango_cairo_show_layout_line.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_pc.pango_cairo_layout_line_path.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_pc.pango_cairo_context_set_font_options.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

# Unhinted, greyscale, unrounded: layouts are identical at every render scale.
_FONT_OPTIONS = cairo.FontOptions()
_FONT_OPTIONS.set_antialias(cairo.ANTIALIAS_GRAY)
_FONT_OPTIONS.set_hint_style(cairo.HINT_STYLE_NONE)
_FONT_OPTIONS.set_hint_metrics(cairo.HINT_METRICS_OFF)


def _boxed_ptr(obj) -> int:
    """Raw pointer of a PyGObject boxed wrapper or a pycairo object ({PyObject_HEAD; void *ptr})."""
    return ctypes.c_void_p.from_address(id(obj) + object.__basicsize__).value


def _new_layout() -> Pango.Layout:
    lay = new_layout()
    pctx = lay.get_context()
    _pc.pango_cairo_context_set_font_options(_gptr(pctx), _boxed_ptr(_FONT_OPTIONS))
    pctx.set_round_glyph_positions(False)
    lay.context_changed()
    return lay


def _update(cr: cairo.Context, layout) -> None:
    """Deliberately a no-op: layouts are shaped once in document space (identity matrix) and
    drawn under any device matrix. pango_cairo_update_layout would reshape them at the device
    size, where fontconfig may pick another face and hinting may change advances, so line
    breaks and glyph positions would depend on the render scale."""
    return None


def _show_line(cr: cairo.Context, layout, i: int) -> None:
    _pc.pango_cairo_show_layout_line(_cairo_ptr(cr), _pango.pango_layout_get_line_readonly(_gptr(layout), i))


def _line_path(cr: cairo.Context, layout, i: int) -> None:
    _pc.pango_cairo_layout_line_path(_cairo_ptr(cr), _pango.pango_layout_get_line_readonly(_gptr(layout), i))


class LogAttr:
    """PangoLogAttr decoded from its bitfield (PyGObject cannot read bitfield structs)."""
    __slots__ = ("is_line_break", "is_char_break", "is_white", "is_cursor_position", "is_word_start")

    def __init__(self, v: int):
        self.is_line_break = bool(v & 1)
        self.is_char_break = bool(v & 4)
        self.is_white = bool(v & 8)
        self.is_cursor_position = bool(v & 16)
        self.is_word_start = bool(v & 32)


def log_attrs(layout) -> list[LogAttr]:
    n = ctypes.c_int(0)
    ptr = _pango.pango_layout_get_log_attrs_readonly(_gptr(layout), ctypes.byref(n))
    return [LogAttr(ptr[i]) for i in range(n.value)]


def _show_layout(cr, layout) -> None:
    _pc.pango_cairo_show_layout(_cairo_ptr(cr), _gptr(layout))


def _layout_path(cr, layout) -> None:
    _pc.pango_cairo_layout_path(_cairo_ptr(cr), _gptr(layout))


# ---------------------------------------------------------------- style resolution
CHAR_KEYS = ("font", "fontFile", "fontAsset", "fallback", "weight", "fontStyle", "stretch", "variation", "features",
             "strokeColor", "strokeWidth", "strokePosition", "shadowColor", "shadowOffsetX", "shadowOffsetY",
             "shadowBlur", "baselineShift", "tracking", "textTransform", "decoration", "highlight")
RUN_KEYS = CHAR_KEYS + ("size", "color", "lineHeight")
BLOCK_KEYS = ("width", "height", "size", "align", "lineHeight", "letterSpacing", "direction", "language",
              "verticalAlign", "writingMode", "wrap", "hyphenate", "autoFit", "minSize", "maxSize", "maxLines",
              "overflow", "background", "backgroundMode", "backgroundPadding", "backgroundRadius", "emoji")
BLOCK_DEFAULTS = {"color": "#FFFFFFFF", "align": "start", "lineHeight": 1.2, "letterSpacing": 0.0, "direction": "auto",
                  "verticalAlign": "top", "writingMode": "horizontal-tb", "wrap": "word", "hyphenate": False,
                  "autoFit": "none", "overflow": "visible", "backgroundMode": "block", "backgroundPadding": 0.0,
                  "backgroundRadius": 0.0, "emoji": "color"}


class Run:
    """A run of text with its resolved character style (a plain dict, frozen into `key`)."""
    __slots__ = ("text", "st", "role", "key")

    def __init__(self, text: str, st: dict, role: str | None = None):
        self.text = text
        self.st = st
        self.role = role
        self.key = (text, tuple(sorted((k, v) for k, v in st.items() if v is not None)), role)

    def with_text(self, text: str) -> "Run":
        return Run(text, self.st, self.role)

    def with_style(self, **kw) -> "Run":
        return Run(self.text, {**self.st, **kw}, self.role)


@dataclass(frozen=True)
class Spec:
    runs: tuple
    block: tuple        # sorted (key, value) pairs

    @property
    def key(self):
        return (tuple(r.key for r in self.runs), self.block)

    def __hash__(self):
        return hash(self.key)

    def __eq__(self, other):
        return isinstance(other, Spec) and self.key == other.key

    def b(self, k, default=None):
        for kk, v in self.block:
            if kk == k:
                return v
        return BLOCK_DEFAULTS.get(k, default) if default is None else default

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    def with_runs(self, runs) -> "Spec":
        return Spec(tuple(runs), self.block)


def make_spec(runs, **block) -> Spec:
    """Build a spec directly (used by captions); runs are prepared like an asset's."""
    runs = prepare_runs(runs, bool(block.get("hyphenate")), block.get("language"), block.get("emoji", "color"))
    return Spec(tuple(runs), tuple(sorted((k, v) for k, v in block.items() if v is not None)))


def _explicit(rc, el, keys, ctx) -> dict:
    out = {}
    scoped = bool(ctx.scope.overrides)
    eid = el.get("id")
    for k in keys:
        if el.get(k) is not None or (scoped and ctx.scope.lookup(eid, k) is not None) or rc.ev._anims(el, k):
            v = rc.ev.get(el, k, ctx, None)
            if v is not None:
                out[k] = v
    return out


def _style(rc, sid) -> dict:
    if not sid:
        return {}
    st = rc.doc.text_styles.get(sid)
    if st is None:
        warn_once("textStyle", sid, "text style not found")
        return {}
    return {k: v for k, v in st.items() if k in RUN_KEYS}


def resolve_spec(rc, asset, ctx) -> Spec:
    """Spec of a text asset at ctx (cached when nothing can vary)."""
    static = not ctx.scope.overrides
    key = ("text-spec", asset)
    if static:
        hit = rc.cache.get(key)
        if hit is not None:
            return hit
    ev = rc.ev
    base = {**_style(rc, asset.get("style")), **_explicit(rc, asset, RUN_KEYS, ctx)}
    base.setdefault("color", BLOCK_DEFAULTS["color"])
    base.setdefault("lineHeight", BLOCK_DEFAULTS["lineHeight"])
    base["size"] = ev.num(asset, "size", ctx, float(base.get("size", 32) or 32))
    runs = []
    text = ev.str(asset, "text", ctx)
    spans = [s for s in asset if ln(s) == "span"]
    if text is not None and (not spans or asset.get("text") is not None):
        runs.append(Run(text, dict(base)))
    else:
        for sp in spans:
            st = {**base, **_style(rc, sp.get("style")), **_explicit(rc, sp, RUN_KEYS, ctx)}
            t = ev.str(sp, "text", ctx) if (ctx.scope.overrides and ctx.scope.lookup(sp.get("id"), "text")) else (sp.text or "")
            if t:
                runs.append(Run(t, st, sp.get("role")))
    blk = {}
    for k in BLOCK_KEYS:
        if k in ("size", "lineHeight"):
            continue
        v = ev.get(asset, k, ctx, None)
        if v is not None:
            blk[k] = v
    blk["size"] = base["size"]
    blk["width"] = ev.num(asset, "width", ctx, 100.0)
    blk["height"] = ev.num(asset, "height", ctx, 100.0)
    for k in ("color", "lineHeight"):
        blk.pop(k, None)
    runs = prepare_runs(runs, bool(blk.get("hyphenate")), blk.get("language"), blk.get("emoji", "color"))
    spec = Spec(tuple(runs), tuple(sorted(blk.items())))
    if static:
        rc.cache[key] = spec
    return spec


# ---------------------------------------------------------------- font helpers
_FILE_FAMILY: dict[str, str | None] = {}


def _file_family(rc, src: str) -> str | None:
    path = rc.doc.resolve_path(src)
    if path in _FILE_FAMILY:
        return _FILE_FAMILY[path]
    fam = None
    try:
        if register_font_file(path):
            from PIL import ImageFont
            fam = ImageFont.truetype(path, 12).getname()[0]
    except Exception as e:  # noqa: BLE001 — a bad font file means the fallback family
        warn_once("fontFile", src, f"cannot load: {e}")
    _FILE_FAMILY[path] = fam
    return fam


_STRETCH = [(0.5, "ULTRA_CONDENSED"), (0.625, "EXTRA_CONDENSED"), (0.75, "CONDENSED"), (0.875, "SEMI_CONDENSED"),
            (1.0, "NORMAL"), (1.125, "SEMI_EXPANDED"), (1.25, "EXPANDED"), (1.5, "EXTRA_EXPANDED"), (2.0, "ULTRA_EXPANDED")]


def _f(v, default=0.0) -> float:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, tuple) and v:
        return float(v[0])
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_axes(s) -> dict[str, float]:
    out = {}
    for part in re.split(r"[,;]", str(s or "")):
        m = re.match(r"\s*['\"]?([A-Za-z0-9]{4})['\"]?\s*[= ]?\s*(-?[\d.]+)", part)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def _features(s: str) -> str:
    out = []
    for part in str(s).split(","):
        p = part.strip().strip("'\"")
        if not p:
            continue
        m = re.match(r"^([+-]?)['\"]?([A-Za-z0-9]{4})['\"]?\s*(?:[= ]\s*(\w+))?$", p)
        if not m:
            continue
        sign, tag, val = m.groups()
        if val in (None, "on"):
            val = "0" if sign == "-" else "1"
        elif val == "off":
            val = "0"
        out.append(f"{tag}={val}")
    return ",".join(out)


def _asset_face(rc, fa: str, info: dict) -> tuple[str | None, int, str]:
    """(family, weight, style) selecting font/@collectionIndex of the asset's file exactly."""
    key = ("fontface", fa)
    if key not in rc.cache:
        w, s = info.get("weight", 400), info.get("style", "normal")
        r = (info.get("family"), w, s)
        if info.get("ok"):
            el = rc.doc.ids.get(fa)
            idx = info.get("collectionIndex")
            if idx is None:
                idx = int(el.get("collectionIndex", 0)) if el is not None else 0
            if el is not None:  # an omitted @weight/@fontStyle defers to the face without a warning
                w = w if el.get("weight") is not None else None
                s = s if el.get("fontStyle") is not None else None
            r = resolve_font_face(info["path"], int(idx), info.get("family"), w, s)
        rc.cache[key] = r
    return rc.cache[key]


def families(rc, st: dict) -> list[str]:
    """The run's font families in priority order (fontAsset, fontFile, font, fallback)."""
    fams: list[str] = []
    fa = st.get("fontAsset")
    if fa:
        info = rc.doc.fonts.get(fa)
        if info is None:
            warn_once("fontAsset", fa, "font asset not found")
        else:
            fams.append(_asset_face(rc, fa, info)[0] or info["family"])
    if st.get("fontFile"):
        fam = _file_family(rc, st["fontFile"])
        if fam:
            fams.append(fam)
    for key in ("font", "fallback"):
        if st.get(key):
            fams.extend(x.strip().strip("'\"") for x in str(st[key]).split(",") if x.strip())
    return fams


def _unique(fams) -> list[str]:
    seen, out = set(), []
    for fam in fams:
        if fam.lower() not in seen:
            seen.add(fam.lower())
            out.append(fam)
    return out


def font_description(rc, st: dict, k: float, emoji: str = "color", variation_override: dict | None = None):
    weight, style = 400, "normal"
    fa = st.get("fontAsset")
    info = rc.doc.fonts.get(fa) if fa else None
    if info is not None:
        _fam, weight, style = _asset_face(rc, fa, info)
    # emoji clusters get their own family attribute (see _attrs_for); listing a colour font here
    # would make it win for digits and spaces (it has glyphs for those too).
    fl = _unique(families(rc, st) + ["DejaVu Sans"])
    fd = Pango.FontDescription()
    fd.set_family(",".join(fl))
    if st.get("weight") is not None:
        weight = int(_f(st["weight"], 400))
    _pango.pango_font_description_set_weight(_boxed_ptr(fd), max(1, min(1000, int(weight))))
    style = st.get("fontStyle") or style
    fd.set_style({"italic": Pango.Style.ITALIC, "oblique": Pango.Style.OBLIQUE}.get(style, Pango.Style.NORMAL))
    if st.get("stretch") is not None:
        v = _f(st["stretch"], 1.0)
        v = v / 100.0 if v > 4 else v
        fd.set_stretch(getattr(Pango.Stretch, min(_STRETCH, key=lambda p: abs(p[0] - v))[1]))
    size = max(0.01, _f(st.get("size"), 32.0) * k)
    fd.set_absolute_size(size * PS)
    axes = parse_axes(st.get("variation")) if st.get("variation") else {}
    if variation_override:
        axes.update(variation_override)
    if axes:
        fd.set_variations(",".join(f"{a}={v:g}" for a, v in axes.items()))
    return fd


# ---------------------------------------------------------------- emoji presentation (UTS #51)
def _ranges(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in spec.split():
        a, _, b = part.partition("-")
        out.append((int(a, 16), int(b or a, 16)))
    return sorted(out)


# Emoji=Yes, Emoji_Presentation=Yes (emoji-data.txt, Unicode 15.1)
_EMOJI_PRES = _ranges(
    "231A-231B 23E9-23EC 23F0 23F3 25FD-25FE 2614-2615 2648-2653 267F 2693 26A1 26AA-26AB 26BD-26BE 26C4-26C5 "
    "26CE 26D4 26EA 26F2-26F3 26F5 26FA 26FD 2705 270A-270B 2728 274C 274E 2753-2755 2757 2795-2797 27B0 27BF "
    "2B1B-2B1C 2B50 2B55 1F004 1F0CF 1F18E 1F191-1F19A 1F1E6-1F1FF 1F201 1F21A 1F22F 1F232-1F236 1F238-1F23A "
    "1F250-1F251 1F300-1F320 1F32D-1F335 1F337-1F37C 1F37E-1F393 1F3A0-1F3CA 1F3CF-1F3D3 1F3E0-1F3F0 1F3F4 "
    "1F3F8-1F43E 1F440 1F442-1F4FC 1F4FF-1F53D 1F54B-1F54E 1F550-1F567 1F57A 1F595-1F596 1F5A4 1F5FB-1F64F "
    "1F680-1F6C5 1F6CC 1F6D0-1F6D2 1F6D5-1F6D7 1F6DC-1F6DF 1F6EB-1F6EC 1F6F4-1F6FC 1F7E0-1F7EB 1F7F0 "
    "1F90C-1F93A 1F93C-1F945 1F947-1F9FF 1FA70-1FA7C 1FA80-1FA89 1FA8F-1FAC6 1FACE-1FADC 1FADF-1FAE9 1FAF0-1FAF8")
# Emoji=Yes with default text presentation
_EMOJI_TEXT = _ranges(
    "A9 AE 203C 2049 2122 2139 2194-2199 21A9-21AA 2328 23CF 23ED-23EF 23F1-23F2 23F8-23FA 24C2 25AA-25AB 25B6 "
    "25C0 25FB-25FC 2600-2604 260E 2611 2618 261D 2620 2622-2623 2626 262A 262E-262F 2638-263A 2640 2642 "
    "265F-2660 2663 2665-2666 2668 267B 267E 2692 2694-2697 2699 269B-269C 26A0 26A7 26B0-26B1 26C8 26CF 26D1 "
    "26D3 26E9 26F0-26F1 26F4 26F7-26F9 2702 2708-2709 270C-270D 270F 2712 2714 2716 271D 2721 2733-2734 2744 "
    "2747 2763-2764 27A1 2934-2935 2B05-2B07 3030 303D 3297 3299 1F170-1F171 1F17E-1F17F 1F202 1F237 1F321 "
    "1F324-1F32C 1F336 1F37D 1F396-1F397 1F399-1F39B 1F39E-1F39F 1F3CB-1F3CE 1F3D4-1F3DF 1F3F3 1F3F5 1F3F7 "
    "1F43F 1F441 1F4FD 1F549-1F54A 1F56F-1F570 1F573-1F579 1F587 1F58A-1F58D 1F590 1F5A5 1F5A8 1F5B1-1F5B2 "
    "1F5BC 1F5C2-1F5C4 1F5D1-1F5D3 1F5DC-1F5DE 1F5E1 1F5E3 1F5E8 1F5EF 1F5F3 1F5FA 1F6CB 1F6CD-1F6CF "
    "1F6E0-1F6E5 1F6E9 1F6F0 1F6F3")
_VS15, _VS16, _ZWJ, _KEYCAP, _SHY = "\ufe0e", "\ufe0f", "\u200d", "\u20e3", "\u00ad"
COLOR_EMOJI_FAMILIES = ("Noto Color Emoji", "Apple Color Emoji", "Segoe UI Emoji", "Twemoji", "EmojiOne Color",
                        "JoyPixels")
MONO_EMOJI_FAMILIES = ("DejaVu Sans", "Noto Sans Symbols2", "Noto Sans Symbols", "Symbola", "Noto Emoji",
                       "FreeSerif", "FreeSans")


def _in(ranges, cp: int) -> bool:
    i = bisect.bisect_right(ranges, (cp, 0x10FFFF)) - 1
    return i >= 0 and ranges[i][0] <= cp <= ranges[i][1]


def is_emoji(c: str) -> bool:
    cp = ord(c)
    return cp >= 0xA9 and (_in(_EMOJI_PRES, cp) or _in(_EMOJI_TEXT, cp))


def _modifier(c: str) -> bool:
    return 0x1F3FB <= ord(c) <= 0x1F3FF


def _emoji_joiner(c: str) -> bool:
    """Characters that continue an emoji cluster (selectors, modifiers, ZWJ, keycap, tags)."""
    cp = ord(c)
    return c in (_VS15, _VS16, _ZWJ, _KEYCAP) or _modifier(c) or 0xE0020 <= cp <= 0xE007F


def emoji_presentation(text: str, mode: str) -> str:
    """Force text ("text") or emoji ("color") presentation with variation selectors (UTS #51)."""
    vs = _VS16 if mode == "color" else _VS15
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c in "#*0123456789":
            if nxt in (_VS15, _VS16) and text[i + 2:i + 3] == _KEYCAP:
                out.append(c + vs)
                i += 2
                continue
            out.append(c + vs if nxt == _KEYCAP else c)
            i += 1
            continue
        cp = ord(c)
        if cp >= 0xA9 and not _modifier(c) and not 0x1F1E6 <= cp <= 0x1F1FF and is_emoji(c):
            if nxt in (_VS15, _VS16):
                out.append(c + vs)
                i += 2
                continue
            if mode == "color" and (_in(_EMOJI_PRES, cp) or _modifier(nxt[:1] or "a")):
                out.append(c)             # already emoji presentation / a modifier sequence
            else:
                out.append(c + vs)
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def emoji_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of emoji clusters (base + selectors/modifiers/ZWJ continuations)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        keycap = c in "#*0123456789" and (text[i + 1:i + 2] == _KEYCAP or text[i + 2:i + 3] == _KEYCAP)
        if keycap or is_emoji(c) or _modifier(c):
            j = i + 1
            while j < n and (_emoji_joiner(text[j]) or (text[j - 1] == _ZWJ and (is_emoji(text[j]) or _modifier(text[j])))
                             or (0x1F1E6 <= ord(c) <= 0x1F1FF and j == i + 1 and 0x1F1E6 <= ord(text[j]) <= 0x1F1FF)):
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


_COLOR_FAMS: set[str] | None = None
_INSTALLED: dict[tuple, tuple] = {}


def _installed(fams: tuple) -> tuple:
    """The families of fams that exist (an absent family can alias to fontconfig's generic
    'emoji' family, i.e. to a colour font)."""
    hit = _INSTALLED.get(fams)
    if hit is None:
        from ..pango_bridge import families as installed
        have = {f.lower() for f in installed()}
        hit = _INSTALLED[fams] = tuple(f for f in fams if f.lower() in have)
    return hit


def color_families() -> set[str]:
    """Lower-cased families of the colour (bitmap/COLR) fonts fontconfig knows."""
    global _COLOR_FAMS
    if _COLOR_FAMS is None:
        fams = {f.lower() for f in COLOR_EMOJI_FAMILIES}
        try:
            import subprocess
            r = subprocess.run(["fc-list", ":color=true", "family"], capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines():
                fams.update(x.strip().lower() for x in line.split(",") if x.strip())
        except (OSError, subprocess.SubprocessError):
            pass
        _COLOR_FAMS = fams
    return _COLOR_FAMS


# ---------------------------------------------------------------- hyphenation
_HYPH: dict[str, object] = {}
_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|\u00ad)*")


def _dictionary(language: str | None):
    import pyphen
    tag = str(language or "en-US").replace("-", "_")
    if tag not in _HYPH:
        name = pyphen.language_fallback(tag) or pyphen.language_fallback(tag.split("_")[0])
        if name is None:
            warn_once("text", f"hyphenate:{tag}", "no hyphenation dictionary for this language; only soft hyphens break")
        _HYPH[tag] = pyphen.Pyphen(lang=name, left=2, right=2) if name else None
    return _HYPH[tag]


def hyphen_points(text: str, language: str | None) -> set[int]:
    """Indices i where a soft hyphen goes before text[i] (words of 5+ letters, 2 2 limits)."""
    dic = _dictionary(language)
    if dic is None:
        return set()
    out = set()
    for m in _WORD.finditer(text):
        w = m.group(0)
        if len(w) < 5 or _SHY in w:
            continue
        out.update(m.start() + p for p in dic.positions(w))
    return out


def prepare_runs(runs, hyphenate: bool, language: str | None, emoji: str | None) -> list:
    """Runs with emoji variation selectors and dictionary soft hyphens inserted (spec level, so
    every later index refers to the prepared text)."""
    runs = list(runs)
    if emoji in ("color", "text"):
        for i, r in enumerate(runs):
            t = emoji_presentation(r.text, emoji)
            if t != r.text:
                runs[i] = r.with_text(t)
    if hyphenate:
        text = "".join(r.text for r in runs)
        pts = hyphen_points(text, language)
        if pts:
            out, acc = [], 0
            for r in runs:
                chars = [(_SHY + c) if (acc + i) in pts and i > 0 else c for i, c in enumerate(r.text)]
                if r.text and acc in pts and out:           # break point at a run boundary: end the previous run
                    out[-1] = out[-1].with_text(out[-1].text + _SHY)
                acc += len(r.text)
                t = "".join(chars)
                out.append(r if t == r.text else r.with_text(t))
            runs = out
    return runs


# ---------------------------------------------------------------- text transforms
def _transform(text: str, mode: str | None, prev_char: str) -> str:
    if mode == "uppercase":
        return "".join(c.upper() if len(c.upper()) == 1 else c for c in text)
    if mode == "lowercase":
        return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)
    if mode == "capitalize":
        out, prev = [], prev_char
        for c in text:
            if (not prev or not (prev.isalnum() or prev in "'’")) and c.isalpha() and len(c.upper()) == 1:
                out.append(c.upper())
            else:
                out.append(c)
            if c not in (_SHY, _VS15, _VS16, _ZWJ):
                prev = c
        return "".join(out)
    return text


def display_runs(runs) -> list[Run]:
    out, prev = [], ""
    for r in runs:
        tt = r.st.get("textTransform")
        t = _transform(r.text, tt, prev) if tt in ("uppercase", "lowercase", "capitalize") else r.text
        out.append(r if t == r.text else r.with_text(t))
        prev = r.text[-1:] if r.text else prev
    return out


# ---------------------------------------------------------------- layout building
def _rgb16(c):
    return tuple(int(round(min(1.0, max(0.0, v)) * 65535)) for v in c[:3])


def _attrs_for(rc, runs, k: float, spec: Spec, no_fg: bool, var_over: dict | None = None):
    attrs = Pango.AttrList()
    b0 = 0
    ls_px = _f(spec.b("letterSpacing"), 0.0)
    emoji = spec.b("emoji")

    def add(a, s, e):
        a.start_index, a.end_index = s, e
        attrs.insert(a)

    for r in runs:
        nb = len(r.text.encode("utf-8"))
        if nb == 0:
            continue
        s, e = b0, b0 + nb
        st = r.st
        size = _f(st.get("size"), 32.0) * k
        add(Pango.attr_font_desc_new(font_description(rc, st, k, emoji, var_over)), s, e)
        col = st.get("color")
        if not no_fg and col is not None and paint_ref(str(col)) is None:
            c = parse_color(str(col), rc.doc.tokens, (1, 1, 1, 1))
            add(Pango.attr_foreground_new(*_rgb16(c)), s, e)
            if c[3] < 1:
                add(Pango.attr_foreground_alpha_new(max(1, int(round(c[3] * 65535)))), s, e)
        spacing = ls_px * k + _f(st.get("tracking"), 0.0) / 1000.0 * size
        if spacing:
            add(Pango.attr_letter_spacing_new(int(round(spacing * PS))), s, e)
        bs = _f(st.get("baselineShift"), 0.0) * k
        if bs:
            add(Pango.attr_rise_new(int(round(bs * PS))), s, e)
        lh = _f(st.get("lineHeight"), 1.2)
        add(Pango.attr_line_height_new_absolute(max(1, int(round(lh * size * PS)))), s, e)
        if st.get("textTransform") == "small-caps":
            add(Pango.attr_variant_new(Pango.Variant.SMALL_CAPS), s, e)
        dec = st.get("decoration")
        if dec == "underline":
            add(Pango.attr_underline_new(Pango.Underline.SINGLE), s, e)
        elif dec == "line-through":
            add(Pango.attr_strikethrough_new(True), s, e)
        elif dec == "overline":
            add(Pango.attr_overline_new(Pango.Overline.SINGLE), s, e)
        if st.get("features"):
            feats = _features(st["features"])
            if feats:
                add(Pango.attr_font_features_new(feats), s, e)
        if emoji in ("color", "text"):
            spans = emoji_spans(r.text)
            if spans:
                fams = (_installed(COLOR_EMOJI_FAMILIES) + tuple(families(rc, st)) if emoji == "color"
                        else tuple(families(rc, st)) + _installed(MONO_EMOJI_FAMILIES))
                fam = ",".join(_unique(fams))
                offs = _byte_to_char(r.text).offs
                for a, b in spans:
                    add(Pango.attr_family_new(fam), s + offs[a], s + offs[b])
        b0 = e
    if b0:
        if spec.b("language"):
            add(Pango.attr_language_new(Pango.Language.from_string(str(spec.b("language")))), 0, b0)
        add(Pango.attr_insert_hyphens_new(bool(spec.b("hyphenate"))), 0, b0)
    return attrs


def _is_vertical(spec: Spec) -> bool:
    return str(spec.b("writingMode")).startswith("vertical")


def box_dims(spec: Spec) -> tuple[float, float]:
    """(line length, block extent) of the box in layout coordinates."""
    w, h = _f(spec.b("width"), 100.0), _f(spec.b("height"), 100.0)
    return (h, w) if _is_vertical(spec) else (w, h)


def build_layout(rc, spec: Spec, k: float, *, limit: bool = True, no_fg: bool = False, width: float | None = None,
                 runs=None, nowrap: bool = False, var_over: dict | None = None, transformed: bool = False) -> Pango.Layout:
    runs = (runs if transformed else display_runs(spec.runs if runs is None else runs))
    lay = _new_layout()
    pctx = lay.get_context()
    direction = spec.b("direction")
    if direction in ("ltr", "rtl"):
        pctx.set_base_dir(Pango.Direction.LTR if direction == "ltr" else Pango.Direction.RTL)
        lay.set_auto_dir(False)
    if spec.b("language"):
        pctx.set_language(Pango.Language.from_string(str(spec.b("language"))))
    if _is_vertical(spec):
        pctx.set_base_gravity(Pango.Gravity.EAST)
        pctx.set_gravity_hint(Pango.GravityHint.NATURAL)
    lay.context_changed()
    base = runs[0].st if runs else {"size": spec.b("size")}
    lay.set_font_description(font_description(rc, base, k, spec.b("emoji")))
    lay.set_text("".join(r.text for r in runs), -1)
    lay.set_attributes(_attrs_for(rc, runs, k, spec, no_fg, var_over))
    bw, bh = box_dims(spec)
    wrap = spec.b("wrap")
    overflow = spec.b("overflow")
    if nowrap:
        lay.set_width(-1)
    elif wrap == "none":
        lay.set_width(int((width or bw) * PS) if (limit and overflow == "ellipsis") else -1)
    else:
        lay.set_width(max(1, int((width or bw) * PS)))
        mode = {"character": Pango.WrapMode.CHAR}.get(wrap, Pango.WrapMode.WORD_CHAR if spec.b("hyphenate") else Pango.WrapMode.WORD)
        lay.set_wrap(mode)
    align = spec.b("align")
    start, end = (Pango.Alignment.RIGHT, Pango.Alignment.LEFT) if direction == "rtl" else (Pango.Alignment.LEFT, Pango.Alignment.RIGHT)
    lay.set_alignment({"center": Pango.Alignment.CENTER, "end": end}.get(align, start))
    if align == "justify":
        lay.set_justify(True)
    if limit and overflow == "ellipsis" and not nowrap:
        lay.set_ellipsize(Pango.EllipsizeMode.END)
        ml = spec.b("maxLines")
        lay.set_height(-int(_f(ml)) if ml else int(bh * PS))
    return lay


# ---------------------------------------------------------------- fitting
def _fits(rc, spec: Spec, k: float) -> bool:
    lay = build_layout(rc, spec, k, limit=False)
    bw, bh = box_dims(spec)
    _ink, logical = lay.get_extents()
    if logical.height / PS > bh + 0.5:
        return False
    ml = spec.b("maxLines")
    if ml and lay.get_line_count() > int(_f(ml)):
        return False
    wrap = spec.b("wrap")
    if wrap == "none":
        return logical.width / PS <= bw + 0.5
    if wrap in ("word", "balance") and not spec.b("hyphenate"):
        text = lay.get_text()
        attrs = log_attrs(lay)
        bmap = _byte_to_char(text)
        for line in lay.get_lines_readonly()[1:]:
            ci = bmap(line.start_index)
            if 0 < ci < len(text) and text[ci - 1] not in "\n  " and not attrs[ci].is_line_break:
                return False
    return True


def fit_factor(rc, spec: Spec) -> float:
    mode = spec.b("autoFit")
    if mode not in ("shrink", "grow", "fit") or not spec.runs:
        return 1.0
    size = _f(spec.b("size"), 32.0)
    kmin = _f(spec.b("minSize"), 1.0) / size if spec.b("minSize") else 1.0 / size
    kmax = _f(spec.b("maxSize"), 4096.0) / size if spec.b("maxSize") else 4096.0 / size
    kmin = min(kmin, 1.0)
    kmax = max(kmax, 1.0)
    if mode == "shrink":
        if _fits(rc, spec, 1.0):
            return 1.0
        lo, hi = kmin, 1.0
        if not _fits(rc, spec, lo):
            return lo
    elif mode == "grow":
        if not _fits(rc, spec, 1.0):
            return 1.0
        lo, hi = 1.0, kmax
        if _fits(rc, spec, hi):
            return hi
    else:
        lo, hi = kmin, kmax
        if _fits(rc, spec, hi):
            return hi
        if not _fits(rc, spec, lo):
            return lo
    for _ in range(14):
        mid = (lo + hi) / 2
        if _fits(rc, spec, mid):
            lo = mid
        else:
            hi = mid
        if (hi - lo) * size < 0.05:
            break
    return lo


# ---------------------------------------------------------------- geometry
def _byte_to_char(text: str):
    offs = [0]
    for c in text:
        offs.append(offs[-1] + len(c.encode("utf-8")))

    def f(b: int) -> int:
        return bisect.bisect_left(offs, b)
    f.offs = offs  # type: ignore[attr-defined]
    return f


@dataclass
class Line:
    c0: int
    c1: int
    x: float
    y: float
    w: float
    h: float
    baseline: float
    hidden: bool = False


@dataclass
class Block:
    rc: object
    spec: Spec
    k: float
    layout: object
    text: str
    runs: list
    lines: list
    clusters: list          # (c0, c1)
    cl_x: list              # (x0, x1) logical, layout coords
    cl_line: list
    cl_run: list
    O: np.ndarray           # layout -> asset box
    size: float             # base em after fit
    ink: tuple              # (x0, y0, x1, y1) layout coords
    logical: tuple
    _cache: dict = field(default_factory=dict)

    # -------- helpers
    def byte(self, c: int) -> int:
        return self._offs[c]

    @property
    def _offs(self):
        o = self._cache.get("offs")
        if o is None:
            o = [0]
            for ch in self.text:
                o.append(o[-1] + len(ch.encode("utf-8")))
            self._cache["offs"] = o
        return o

    def nofg(self):
        lay = self._cache.get("nofg")
        if lay is None:
            lay = build_layout(self.rc, self.spec, self.k, no_fg=True, width=self._cache.get("width"))
            self._cache["nofg"] = lay
        return lay

    def run_style(self, c: int) -> dict:
        return self.runs[self.cl_run[c]].st

    def is_newline(self, ci: int) -> bool:
        a, b = self.clusters[ci]
        return self.text[a:b] in ("\n", "\r\n", "\r", " ", " ")

    def is_space(self, ci: int) -> bool:
        a, b = self.clusters[ci]
        return self.text[a:b].isspace()

    def is_shy(self, ci: int) -> bool:
        """A soft-hyphen cluster: not a unit, drawn (as the break hyphen) with its predecessor."""
        a, b = self.clusters[ci]
        return self.text[a:b] == _SHY

    def units(self, kind: str, role: str | None = None) -> list[list[int]]:
        """Cluster-index lists per unit (character, character-no-space, word, line, span)."""
        key = ("units", kind, role)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        n = len(self.clusters)
        vis_l = [i for i in range(n) if not self.is_newline(i) and not self.is_shy(i)
                 and not self.lines[self.cl_line[i]].hidden]
        vis = set(vis_l)
        out: list[list[int]] = []
        if kind == "character":
            out = [[i] for i in vis_l]
        elif kind == "character-no-space":
            out = [[i] for i in vis_l if not self.is_space(i)]
        elif kind == "word":
            cur: list[int] = []
            for i in range(n):
                if self.is_shy(i) and cur:
                    continue
                if i in vis and not self.is_space(i):
                    cur.append(i)
                else:
                    if cur:
                        out.append(cur)
                    cur = []
            if cur:
                out.append(cur)
        elif kind == "line":
            by: dict[int, list[int]] = {}
            for i in vis_l:
                by.setdefault(self.cl_line[i], []).append(i)
            out = [self._trim(by[k]) for k in sorted(by)]
        elif kind == "span":
            by = {}
            for i in vis_l:
                by.setdefault(self.cl_run[i], []).append(i)
            out = [by[k] for k in sorted(by)]
        else:
            warn_once("textAnimator", kind, "unknown unit; using character")
            out = [[i] for i in vis_l]
        if role:
            out = [u for u in out if self.runs[self.cl_run[u[0]]].role == role]
        self._cache[key] = out
        return out

    def span_x(self, a: int, b: int) -> tuple[float, float]:
        """Visual x extent of clusters a..b (inclusive; handles right-to-left runs)."""
        xs = self.cl_x[a:b + 1]
        return min(x[0] for x in xs), max(x[1] for x in xs)

    def _trim(self, u: list[int]) -> list[int]:
        """Drop leading and trailing whitespace clusters (keep the unit if it is all space)."""
        a, b = 0, len(u)
        while a < b and self.is_space(u[a]):
            a += 1
        while b > a and self.is_space(u[b - 1]):
            b -= 1
        return u[a:b] or u

    def cluster_rect(self, ci: int) -> tuple[float, float, float, float]:
        ln_ = self.lines[self.cl_line[ci]]
        x0, x1 = self.cl_x[ci]
        return x0, ln_.y, x1, ln_.y + ln_.h

    def line_ranges(self, clusters) -> list[tuple[int, float, float]]:
        """(line, x0, x1) spans covering clusters; runs of consecutive clusters on a line merge."""
        out: list[list] = []
        prev = None
        for ci in sorted(clusters):
            li = self.cl_line[ci]
            x0, x1 = self.cl_x[ci]
            if out and prev == ci - 1 and out[-1][0] == li:
                out[-1][1] = min(out[-1][1], x0)
                out[-1][2] = max(out[-1][2], x1)
            else:
                out.append([li, x0, x1])
            prev = ci
        return [tuple(r) for r in out]

    def range_rects(self, clusters) -> list[tuple[float, float, float, float]]:
        """Rectangles (x0, y0, x1, y1) covering clusters, merged per line."""
        return [(x0, self.lines[li].y, x1, self.lines[li].y + self.lines[li].h) for li, x0, x1 in self.line_ranges(clusters)]

    def line_extent(self, li: int) -> tuple[float, float]:
        ext = self._cache.get("line_ext")
        if ext is None:
            ext = {}
            for ci, l_ in enumerate(self.cl_line):
                x0, x1 = self.cl_x[ci]
                r = ext.get(l_)
                ext[l_] = (min(r[0], x0), max(r[1], x1)) if r else (x0, x1)
            self._cache["line_ext"] = ext
        return ext.get(li, (0.0, 0.0))


def get_block(rc, spec: Spec, force: Block | None = None) -> Block:
    """Lay out spec (cached). force: reuse another block's fit factor and balance width
    (so substituted characters do not re-fit the text)."""
    key = ("text-block", spec, None if force is None else (force.k, force._cache.get("width")))
    hit = rc.cache.get(key)
    if hit is not None:
        return hit
    if force is not None:
        k, width = force.k, force._cache.get("width")
    else:
        k = fit_factor(rc, spec)
        width = None
        if spec.b("wrap") == "balance" and spec.runs:
            width = _balance_width(rc, spec, k)
    lay = build_layout(rc, spec, k, width=width)
    blk = _geometry(rc, spec, k, lay, width)
    _evict(rc)
    rc.cache[key] = blk
    return blk


def _balance_width(rc, spec, k) -> float | None:
    bw, _ = box_dims(spec)
    n = build_layout(rc, spec, k, limit=False).get_line_count()
    if n <= 1:
        return None
    lo, hi = bw * 0.2, bw
    for _ in range(12):
        mid = (lo + hi) / 2
        if build_layout(rc, spec, k, limit=False, width=mid).get_line_count() <= n:
            hi = mid
        else:
            lo = mid
    return hi + 0.5


def _geometry(rc, spec: Spec, k: float, lay, width) -> Block:
    runs = display_runs(spec.runs)
    text = "".join(r.text for r in runs)
    b2c = _byte_to_char(text)
    lines = []
    it = lay.get_iter()
    while True:
        _ink, logi = it.get_line_extents()
        line = it.get_line_readonly()
        c0 = b2c(line.start_index)
        c1 = b2c(line.start_index + line.length)
        lines.append(Line(c0, c1, logi.x / PS, logi.y / PS, logi.width / PS, logi.height / PS, it.get_baseline() / PS))
        if not it.next_line():
            break
    ml = spec.b("maxLines")
    if ml and spec.b("overflow") != "ellipsis":
        for ln_ in lines[int(_f(ml)):]:
            ln_.hidden = True
    attrs = log_attrs(lay)
    clusters = []
    start = 0
    for i in range(1, len(text) + 1):
        if i == len(text) or attrs[i].is_cursor_position:
            clusters.append((start, i))
            start = i
    starts = [ln_.c0 for ln_ in lines]
    run_starts = []
    acc = 0
    for r in runs:
        run_starts.append(acc)
        acc += len(r.text)
    cl_x, cl_line, cl_run = [], [], []
    offs = b2c.offs
    for a, b in clusters:
        li = max(0, bisect.bisect_right(starts, a) - 1)
        xs = []
        for c in range(a, b):
            p = lay.index_to_pos(offs[c])
            xs += [p.x / PS, (p.x + p.width) / PS]
        cl_x.append((min(xs), max(xs)))
        cl_line.append(li)
        cl_run.append(max(0, bisect.bisect_right(run_starts, a) - 1))
    ink, logical = lay.get_extents()
    bw, bh = box_dims(spec)
    content_top, content_h = logical.y / PS, logical.height / PS
    vis = [ln_ for ln_ in lines if not ln_.hidden]
    if vis and len(vis) < len(lines):
        content_h = vis[-1].y + vis[-1].h - content_top
    va = spec.b("verticalAlign")
    oy = {"middle": (bh - content_h) / 2, "bottom": bh - content_h}.get(va, 0.0) - content_top
    ox = 0.0
    fac = {"center": 0.5, "end": 1.0}.get(spec.b("align"), 0.0)
    if spec.b("direction") == "rtl" and spec.b("align") in ("start", "end", "justify", None):
        fac = 1.0 - fac
    if lay.get_width() < 0:
        # unwrapped text: Pango aligns lines against the widest one; align that inside the box
        ox = fac * (bw - logical.width / PS) - logical.x / PS
    elif width is not None:
        ox = fac * (bw - width)
    if spec.b("writingMode") == "vertical-lr":
        # columns left to right: reflect the line boxes across the block (glyphs keep their
        # orientation), so the first line sits on the left and verticalAlign=top means left.
        a = oy + content_top
        C = bh - 2 * a + 2 * content_top
        for ln_ in vis:
            ny = C - ln_.y - ln_.h
            ln_.baseline += ny - ln_.y
            ln_.y = ny
        ink = _reflect_rect(ink, C)
        logical = _reflect_rect(logical, C)
    O = np.array([[1, 0, ox], [0, 1, oy], [0, 0, 1]], np.float64)
    if _is_vertical(spec):
        W = _f(spec.b("width"), 100.0)
        O = np.array([[0, -1, W], [1, 0, 0], [0, 0, 1]], np.float64) @ O
    blk = Block(rc, spec, k, lay, text, runs, lines, clusters, cl_x, cl_line, cl_run, O,
                _f(spec.b("size"), 32.0) * k,
                (ink.x / PS, ink.y / PS, (ink.x + ink.width) / PS, (ink.y + ink.height) / PS),
                (logical.x / PS, logical.y / PS, (logical.x + logical.width) / PS, (logical.y + logical.height) / PS))
    blk._cache["width"] = width
    if spec.b("emoji") == "text":
        blk._cache["mono"] = _colour_clusters(blk)
    return blk


def _reflect_rect(r, C: float):
    """Pango rectangle mirrored across y = C/2 (y -> C - y)."""
    out = Pango.Rectangle()
    out.x, out.width, out.height = r.x, r.width, r.height
    out.y = int(round((C - (r.y + r.height) / PS) * PS))
    return out


def _colour_clusters(blk: "Block") -> list[int]:
    """Clusters drawn from a colour font (emoji=text found no monochrome glyph for them)."""
    cf = color_families()
    b2c = _byte_to_char(blk.text)
    chars: set[int] = set()
    for li in range(len(blk.lines)):
        line = blk.layout.get_line_readonly(li)
        runs = line.runs                      # keep every PyGObject wrapper alive while reading it
        for run in runs:
            it = run.item
            an = it.analysis
            font = an.font
            if font is not None and (font.describe().get_family() or "").lower() in cf:
                chars.update(range(b2c(it.offset), b2c(it.offset + it.length)))
    return [ci for ci, (a, b) in enumerate(blk.clusters) if any(c in chars for c in range(a, b))
            and not blk.text[a:b].isspace()]


def _evict(rc, limit: int = 512) -> None:
    keys = [kk for kk in rc.cache if isinstance(kk, tuple) and kk and kk[0] in ("text-block", "text-tile")]
    if len(keys) > limit:
        for kk in keys[: len(keys) - limit]:
            del rc.cache[kk]


# ---------------------------------------------------------------- pieces
@dataclass
class Piece:
    """Clusters [a, b] (inclusive indices, one line) drawn with their own transform and paint."""
    line: int
    a: int
    b: int
    T: np.ndarray = field(default_factory=lambda: np.eye(3))   # layout -> layout
    opacity: float = 1.0
    fill: tuple | None = None          # straight sRGB rgba override
    stroke: tuple | None = None
    stroke_add: float = 0.0
    blur: float = 0.0
    highlight: tuple | None = None     # (paint, fraction, pad, radius)
    mask: bool = False                 # clip to the static piece box before transforming
    variation: tuple | None = None     # ((axis, value), ...)
    first: bool = False                # first/last piece on its line: clip open on that side
    last: bool = False
    three_d: tuple | None = None       # (zDepth, rotationX, rotationY, pivot x, pivot y): see text_animators


def varied_positions(block: Block, clusters: list[int], variations: list) -> dict[int, tuple[float, float]]:
    """Layout x extents of the clusters of one line reshaped with per-cluster font variations
    ({axis: value} or None), aligned like the static line."""
    b = block
    runs: list[Run] = []
    keys: list = []
    for ci, var in zip(clusters, variations):
        a, e = b.clusters[ci]
        st = b.runs[b.cl_run[ci]].st
        if var:
            axes = parse_axes(st.get("variation")) if st.get("variation") else {}
            axes.update(var)
            st = {**st, "variation": ",".join(f"{k}={v:g}" for k, v in sorted(axes.items()))}
        key = (b.cl_run[ci], st.get("variation"))
        if keys and keys[-1] == key:
            runs[-1] = runs[-1].with_text(runs[-1].text + b.text[a:e])
        else:
            runs.append(Run(b.text[a:e], st, b.runs[b.cl_run[ci]].role))
            keys.append(key)
    lay = build_layout(b.rc, b.spec, b.k, runs=runs, nowrap=True, transformed=True)
    offs = _byte_to_char("".join(r.text for r in runs)).offs
    out, c = {}, 0
    for ci in clusters:
        a, e = b.clusters[ci]
        xs = []
        for k in range(c, c + e - a):
            pos = lay.index_to_pos(offs[k])
            xs += [pos.x / PS, (pos.x + pos.width) / PS]
        out[ci] = (min(xs), max(xs))
        c += e - a
    v0, v1 = min(x[0] for x in out.values()), max(x[1] for x in out.values())
    s0 = min(b.cl_x[ci][0] for ci in clusters)
    s1 = max(b.cl_x[ci][1] for ci in clusters)
    fac = {"center": 0.5, "end": 1.0}.get(b.spec.b("align"), 0.0)
    if b.spec.b("direction") == "rtl" and b.spec.b("align") in ("start", "end", "justify", None):
        fac = 1.0 - fac
    dx = s0 + fac * ((s1 - s0) - (v1 - v0)) - v0
    return {ci: (x0 + dx, x1 + dx) for ci, (x0, x1) in out.items()}


def line_pieces(block: Block) -> list[Piece]:
    out = []
    for li, ln_ in enumerate(block.lines):
        cl = [i for i in range(len(block.clusters)) if block.cl_line[i] == li]
        if cl and not ln_.hidden:
            out.append(Piece(li, cl[0], cl[-1], first=True, last=True))
    return out


# ---------------------------------------------------------------- drawing
def _mat(A: np.ndarray) -> cairo.Matrix:
    return cairo.Matrix(A[0, 0], A[1, 0], A[0, 1], A[1, 1], A[0, 2], A[1, 2])


def _rrect(cr, x0, y0, x1, y1, r):
    w, h = x1 - x0, y1 - y0
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0:
        cr.rectangle(x0, y0, w, h)
        return
    cr.new_sub_path()
    cr.arc(x1 - r, y0 + r, r, -math.pi / 2, 0)
    cr.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
    cr.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
    cr.arc(x0 + r, y0 + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _groups(block: Block, keyfn):
    """{key: [cluster indices]} for clusters whose run gives a non-None key."""
    out: dict = {}
    for ci in range(len(block.clusters)):
        kk = keyfn(block.runs[block.cl_run[ci]].st)
        if kk is not None:
            out.setdefault(kk, []).append(ci)
    return out


def _clip_rects(cr, block: Block, clusters, exclude: bool = False):
    """Clip to the line boxes of clusters (open-ended at line ends and at the outer lines)."""
    last_li = len(block.lines) - 1
    if exclude:
        cr.rectangle(-INF, -INF, 2 * INF, 2 * INF)
    for li, x0, x1 in block.line_ranges(clusters):
        ln_ = block.lines[li]
        y0, y1 = ln_.y, ln_.y + ln_.h
        ex0, ex1 = block.line_extent(li)
        if x0 <= ex0 + 0.01:
            x0 = -INF
        if x1 >= ex1 - 0.01:
            x1 = INF
        if li == 0:
            y0 = -INF
        if li == last_li:
            y1 = INF
        cr.rectangle(x0, y0, x1 - x0, y1 - y0)
    if exclude:
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    cr.clip()
    cr.set_fill_rule(cairo.FILL_RULE_WINDING)


class _Draw:
    """Draws a block onto a canvas whose cairo matrix maps asset-box coordinates."""

    def __init__(self, rc, block: Block, canvas: Canvas, M: np.ndarray, ctx):
        self.rc, self.b, self.c, self.cr, self.M, self.ctx = rc, block, canvas, canvas.cr, M, ctx
        self.L = M @ block.O
        g = block._cache.get("groups")
        if g is None:
            g = (_groups(block, lambda s: s.get("color") if s.get("color") is not None and paint_ref(str(s.get("color"))) else None),
                 _groups(block, lambda s: (str(s.get("strokeColor")), _f(s.get("strokeWidth")) * block.k,
                                           s.get("strokePosition") or "center")
                         if s.get("strokeColor") and _f(s.get("strokeWidth")) > 0 else None),
                 _groups(block, lambda s: str(s.get("highlight")) if s.get("highlight") else None))
            block._cache["groups"] = g
        self.grad, self.strokes, self.highlights = g
        self.n = len(block.clusters)
        self._updated: set = set()

    def lay(self, nofg: bool = False):
        lay = self.b.nofg() if nofg else self.b.layout
        if id(lay) not in self._updated:
            self._updated.add(id(lay))
            _update(self.cr, lay)
        return lay

    # ---- coordinate helpers
    def to_layout(self, extra: np.ndarray | None = None):
        self.c.set_matrix(self.L if extra is None else self.L @ extra)

    def source(self, p: str, extra=None) -> bool:
        """Set a paint source spanning the asset box (patterns lock to the box space)."""
        self.c.set_matrix(self.M)
        W, H = _f(self.b.spec.b("width"), 100), _f(self.b.spec.b("height"), 100)
        ok = paints.set_source(self.rc, self.cr, p, W, H, self.ctx)
        self.to_layout(extra)
        return ok

    # ---- static decorations
    def backgrounds(self):
        b = self.b
        bg = b.spec.b("background")
        if not bg:
            return
        pad = _f(b.spec.b("backgroundPadding")) * b.k
        rad = _f(b.spec.b("backgroundRadius")) * b.k
        mode = b.spec.b("backgroundMode")
        rects = []
        if mode == "word":
            rects = [r for u in b.units("word") for r in b.range_rects(u)]
        else:
            per_line = [r for u in b.units("line") for r in b.range_rects(u)]
            if mode == "line":
                rects = per_line
            elif per_line:
                rects = [(min(r[0] for r in per_line), min(r[1] for r in per_line),
                          max(r[2] for r in per_line), max(r[3] for r in per_line))]
        if not rects:
            return
        cr = self.cr
        if not self.source(str(bg)):
            return
        for x0, y0, x1, y1 in rects:
            _rrect(cr, x0 - pad, y0 - pad, x1 + pad, y1 + pad, rad)
        cr.fill()

    def run_highlights(self, piece_clusters=None):
        cr = self.cr
        for p, cl in self.highlights.items():
            if piece_clusters is not None:
                cl = [c for c in cl if c in piece_clusters]
            if not cl:
                continue
            m = cr.get_matrix()
            if not self.source(p):
                cr.set_matrix(m)
                continue
            cr.set_matrix(m)
            for x0, y0, x1, y1 in self.b.range_rects(cl):
                cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.fill()

    # ---- glyph passes (target: None = whole layout, int = line index)
    def _glyph_path(self, lay, target):
        cr = self.cr
        cr.new_path()
        if isinstance(target, tuple):
            cr.move_to(target[2], target[3])
            _layout_path(cr, target[1])
        elif target is None:
            cr.move_to(0, 0)
            _layout_path(cr, lay)
        else:
            ln_ = self.b.lines[target]
            cr.move_to(ln_.x, ln_.baseline)
            _line_path(cr, lay, target)

    def _show_raw(self, lay, target):
        cr = self.cr
        if isinstance(target, tuple):
            cr.move_to(target[2], target[3])
            _show_layout(cr, lay)
        elif target is None:
            cr.move_to(0, 0)
            _show_layout(cr, lay)
        else:
            ln_ = self.b.lines[target]
            cr.move_to(ln_.x, ln_.baseline)
            _show_line(cr, lay, target)

    def _show(self, lay, target, rgba=None):
        """Show glyphs; with emoji=text, colour-font clusters become silhouettes in rgba (or
        the run colour; a paint-referenced run keeps the current source)."""
        mono = self.b._cache.get("mono")
        if not mono:
            self._show_raw(lay, target)
            return
        cr = self.cr
        cr.save()
        _clip_rects(cr, self.b, mono, exclude=True)
        self._show_raw(lay, target)
        cr.restore()
        for ci in mono:
            cr.save()
            _clip_rects(cr, self.b, [ci])
            src = cr.get_source()
            cr.push_group()
            self._show_raw(lay, target)
            pat = cr.pop_group()
            col = rgba
            if col is None:
                c = self.b.run_style(ci).get("color")
                col = None if (c is not None and paint_ref(str(c))) else parse_color(str(c or "#FFFFFFFF"), self.rc.doc.tokens, (1, 1, 1, 1))
            if col is None:
                cr.set_source(src)
            else:
                cr.set_source_rgba(*col)
            cr.mask(pat)
            cr.restore()

    def _strokes(self, target, when: str, shadow=None, override=None, add_w=0.0, extra=None):
        cr = self.cr
        b = self.b
        groups = self.strokes
        if override is not None and not groups:
            groups = {("", 0.0, "center"): list(range(self.n))}
        for (col, w, pos), cl in groups.items():
            w += add_w
            if w <= 0:
                continue
            if when == "under" and pos != "outside":
                continue
            if when == "over" and pos == "outside":
                continue
            cr.save()
            if len(cl) < self.n:
                _clip_rects(cr, b, cl)
            lay = self.lay()
            if pos == "inside":
                self._glyph_path(lay, target)
                cr.clip()
            self._glyph_path(lay, target)
            cr.set_line_width(w * (1 if pos == "center" else 2))
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            if shadow is not None:
                cr.set_source_rgba(*shadow)
            elif override is not None:
                cr.set_source_rgba(*override)
            elif not self.source(col, extra):
                cr.restore()
                continue
            cr.stroke()
            cr.restore()

    def _fill(self, target, shadow=None, override=None, extra=None):
        cr = self.cr
        b = self.b
        sub = isinstance(target, tuple)
        if shadow is not None or override is not None:
            rgba = shadow if shadow is not None else override
            cr.set_source_rgba(*rgba)
            self._show(target[4] if sub else self.lay(True), target, rgba)
            return
        lay = target[1] if sub else self.lay()
        if not self.grad:
            cr.set_source_rgba(1, 1, 1, 1)
            self._show(lay, target)
            return
        cr.save()
        allg = [c for cl in self.grad.values() for c in cl]
        if len(allg) < self.n:
            cr.save()
            _clip_rects(cr, b, allg, exclude=True)
            cr.set_source_rgba(1, 1, 1, 1)
            self._show(lay, target)
            cr.restore()
        for p, cl in self.grad.items():
            cr.save()
            if len(cl) < self.n:
                _clip_rects(cr, b, cl)
            if self.source(p, extra):
                self._show(lay, target)
            cr.restore()
        cr.restore()

    def glyphs(self, target=None, shadow=None, override_fill=None, override_stroke=None, add_w=0.0, extra=None):
        if shadow is None:
            self._strokes(target, "under", override=override_stroke, add_w=add_w, extra=extra)
        else:
            self._strokes(target, "under", shadow=shadow, add_w=add_w)
        self._fill(target, shadow=shadow, override=override_fill, extra=extra)
        self._strokes(target, "over", shadow=shadow, override=override_stroke, add_w=add_w, extra=extra)

    # ---- pieces
    def piece_rect(self, p: Piece):
        b = self.b
        ln_ = b.lines[p.line]
        x0, x1 = b.span_x(p.a, p.b)
        return x0, ln_.y, x1, ln_.y + ln_.h

    def piece(self, p: Piece, shadow=None):
        if p.opacity <= 1e-4:
            return
        b, cr = self.b, self.cr
        cr.save()
        x0, y0, x1, y1 = self.piece_rect(p)
        self.to_layout()
        if p.mask:
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.clip()
        self.to_layout(p.T)
        whole = p.first and p.last and p.variation is None
        group = p.opacity < 0.999
        if group:
            cr.push_group()
        if p.highlight and shadow is None:
            hp, frac, pad, rad = p.highlight
            if frac > 0 and self.source(str(hp), p.T):
                rtl = b.spec.b("direction") == "rtl"
                w = (x1 - x0 + 2 * pad) * min(1.0, frac)
                hx0 = x1 + pad - w if rtl else x0 - pad
                _rrect(cr, hx0, y0 - pad * 0.5, hx0 + w, y1 + pad * 0.5, rad)
                cr.fill()
        if shadow is None and self.highlights:
            self.run_highlights(set(range(p.a, p.b + 1)))
        target = p.line if whole else self._sub_target(p)
        fill = None if shadow is not None else p.fill
        self.glyphs(target, shadow=shadow, override_fill=fill, override_stroke=p.stroke, add_w=p.stroke_add, extra=p.T)
        if group:
            cr.pop_group_to_source()
            cr.paint_with_alpha(max(0.0, min(1.0, p.opacity)))
        cr.restore()

    def _sub_target(self, p: Piece):
        """Clusters a..b laid out on their own (so neighbours moving differently never bleed in),
        placed at their position in the full layout."""
        b = self.b
        c0, c1 = b.clusters[p.a][0], b.clusters[p.b][1]
        key = ("sub", c0, c1, p.variation)
        hit = b._cache.get(key)
        if hit is None:
            runs = []
            acc = 0
            for r in b.runs:
                s_, e_ = max(c0, acc), min(c1, acc + len(r.text))
                if e_ > s_:
                    runs.append(r.with_text(r.text[s_ - acc:e_ - acc]))
                acc += len(r.text)
            ln_ = b.lines[p.line]
            if runs and c1 == ln_.c1 and runs[-1].text.endswith(_SHY) and p.line + 1 < len(b.lines):
                runs[-1] = runs[-1].with_text(runs[-1].text[:-1] + "-")     # the line breaks at this soft hyphen
            var = dict(p.variation) if p.variation else None
            lay = build_layout(self.rc, b.spec, b.k, runs=runs, nowrap=True, var_over=var, transformed=True)
            nofg = build_layout(self.rc, b.spec, b.k, runs=runs, nowrap=True, var_over=var, transformed=True, no_fg=True)
            hit = (lay, nofg)
            b._cache[key] = hit
        lay, nofg = hit
        for x in hit:
            if id(x) not in self._updated:
                self._updated.add(id(x))
                _update(self.cr, x)
        ln_ = b.lines[p.line]
        _ink, logi = lay.get_extents()
        x = b.span_x(p.a, p.b)[0] - logi.x / PS
        return ("sub", lay, x, ln_.baseline - lay.get_baseline() / PS, nofg)


def _shadow_groups(block: Block):
    return _groups(block, lambda s: (str(s.get("shadowColor")), _f(s.get("shadowOffsetX")) * block.k,
                                     _f(s.get("shadowOffsetY")) * block.k, _f(s.get("shadowBlur")) * block.k)
                   if s.get("shadowColor") else None)


def _over(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    return src + dst * (1.0 - src[..., 3:4])


def block_bounds(block: Block, pieces=None, decor: bool = True) -> tuple[float, float, float, float]:
    """Local (asset-box) bounds of everything the block may draw (decor=False: the pieces only)."""
    b = block
    pad = b.size * 0.6 + 2
    for s in (r.st for r in b.runs):
        pad = max(pad, _f(s.get("strokeWidth")) * b.k * 2 + b.size * 0.6,
                  abs(_f(s.get("shadowOffsetX"))) + abs(_f(s.get("shadowOffsetY"))) + _f(s.get("shadowBlur")) * 2 + b.size * 0.6)
    pad += (_f(b.spec.b("backgroundPadding")) * b.k if b.spec.b("background") else 0)
    if pieces is None:
        rects = [(b.ink[0], b.ink[1], b.ink[2], b.ink[3]), b.logical]
    else:
        rects = []
        for p in pieces:
            ln_ = b.lines[p.line]
            x0, x1 = b.span_x(p.a, p.b)
            pts = np.array([[x0, ln_.y, 1], [x1, ln_.y, 1], [x1, ln_.y + ln_.h, 1], [x0, ln_.y + ln_.h, 1]]) @ p.T.T
            ex = p.blur * 3 + p.stroke_add
            rects.append((pts[:, 0].min() - ex, pts[:, 1].min() - ex, pts[:, 0].max() + ex, pts[:, 1].max() + ex))
        if decor or not rects:
            rects.append(b.logical)
    x0 = min(r[0] for r in rects) - pad
    y0 = min(r[1] for r in rects) - pad
    x1 = max(r[2] for r in rects) + pad
    y1 = max(r[3] for r in rects) + pad
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]]) @ b.O.T
    return pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()


def _frame_rect(rc, M, bounds, clip=None):
    from ..raster import transformed_rect
    x0, y0, x1, y1 = bounds
    if clip is not None:
        x0, y0, x1, y1 = max(x0, clip[0]), max(y0, clip[1]), min(x1, clip[2]), min(y1, clip[3])
        if x1 <= x0 or y1 <= y0:
            return None
    r = transformed_rect(M, x0, y0, x1, y1, 2)
    return intersect(r, (-64, -64, rc.width + 64, rc.height + 64))


def render_block(rc, block: Block, M: np.ndarray, ctx, pieces: list[Piece] | None = None, clip=None,
                 decor: bool = True) -> Buf | None:
    """Draw a laid-out block under M (asset box -> frame). pieces=None draws the static text;
    decor=False skips the block background (pieces drawn on their own, e.g. 3D units)."""
    b = block
    if not b.text.strip() and not (decor and b.spec.b("background")):
        return None
    W, H = _f(b.spec.b("width"), 100), _f(b.spec.b("height"), 100)
    if b.spec.b("overflow") == "clip":
        clip = (max(0, clip[0]), max(0, clip[1]), min(W, clip[2]), min(H, clip[3])) if clip else (0, 0, W, H)
    lr = b.spec.b("writingMode") == "vertical-lr"
    if pieces is None and lr:
        pieces = line_pieces(b)             # reflected line boxes: draw line by line
    rect = _frame_rect(rc, M, block_bounds(b, pieces, decor), clip)
    if rect is None:
        return None
    det = abs(float(np.linalg.det(M[:2, :2]))) ** 0.5
    shadows = _shadow_groups(b)

    def canvas():
        c = Canvas(rect)
        c.cr.set_antialias(cairo.ANTIALIAS_GRAY)
        if clip is not None:
            c.set_matrix(M)
            c.cr.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
            c.cr.clip()
        hidden = [ln_ for ln_ in b.lines if ln_.hidden]
        if hidden and not lr:
            c.set_matrix(M @ b.O)
            c.cr.rectangle(-INF, -INF, 2 * INF, hidden[0].y + INF)
            c.cr.clip()
        c.set_matrix(M @ b.O)
        return c

    blurred = [p for p in pieces or () if p.blur > 0.05 and p.opacity > 1e-4]
    sharp = [p for p in pieces or () if not (p.blur > 0.05 and p.opacity > 1e-4)]
    layers = []        # under the glyphs, bottom first: background, then shadows
    if decor and b.spec.b("background"):
        bc = canvas()
        bd = _Draw(rc, b, bc, M, ctx)
        bd.backgrounds()
        layers.append(bc.to_buf(rc.linear).px)
    for (col, ox, oy, blur), cl in shadows.items():
        c = parse_color(col, rc.doc.tokens, (0, 0, 0, 1))
        if c[3] <= 0:
            continue
        sc = canvas()
        sd = _Draw(rc, b, sc, M @ np.array([[1, 0, ox], [0, 1, oy], [0, 0, 1]], np.float64), ctx)
        sd.to_layout()
        if len(cl) < len(b.clusters):
            _clip_rects(sc.cr, b, cl)
        if pieces is None:
            sd.glyphs(None, shadow=c)
        else:
            for p in pieces:
                sd.piece(p, shadow=c)
        px = sc.to_buf(rc.linear).px
        if blur > 0:
            from ..effects import gaussian
            px = gaussian(px, blur / 2 * det)
        layers.append(px)
    main = canvas()
    d = _Draw(rc, b, main, M, ctx)
    d.to_layout()
    if pieces is None:
        if d.highlights:
            d.run_highlights()
        d.glyphs(None)
    else:
        for p in sharp:
            d.piece(p)
    out = main.to_buf(rc.linear)
    if blurred:
        from ..effects import gaussian
        for p in blurred:
            bc = canvas()
            _Draw(rc, b, bc, M, ctx).piece(p)
            out.px = _over(out.px, gaussian(bc.to_buf(rc.linear).px, p.blur / 2 * det))
    for px in reversed(layers):
        out.px = _over(px, out.px)
    return out


# ---------------------------------------------------------------- asset handler
@ASSET_SIZES.register("text")
def text_size(rc, asset, ctx) -> tuple[float, float]:
    return rc.ev.num(asset, "width", ctx, 100.0), rc.ev.num(asset, "height", ctx, 100.0)


@ASSETS.register("text", level=FULL, note="Pango layout; see scenerender/assets/text.py for pinned rules")
def render_text(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None) -> Buf | None:
    spec = resolve_spec(rc, asset, ctx)
    mods = [c for c in layer if ln(c) in ("textAnimator", "textPath")] if layer is not None else []
    if mods:
        from .. import text_animators
        return text_animators.render_animated(rc, spec, M, ctx, layer, mods, clip)
    key = ("text-tile", spec, np.round(M, 6).tobytes(), clip)
    hit = rc.cache.get(key)
    if hit is not None:
        return None if hit is False else hit.copy()
    block = get_block(rc, spec)
    buf = render_block(rc, block, M, ctx, None, clip)
    _evict(rc)
    rc.cache[key] = buf if buf is not None else False
    return None if buf is None else buf.copy()


def first_baseline(rc, asset, ctx) -> float:
    """Distance from the top of the text box to the first visible line's baseline, in document px
    (after autoFit and verticalAlign); the box height for vertical writing modes."""
    spec = resolve_spec(rc, asset, ctx)
    if _is_vertical(spec):
        return _f(spec.b("height"), 100.0)
    blk = get_block(rc, spec)
    ln_ = next((l_ for l_ in blk.lines if not l_.hidden), None)
    if ln_ is None:
        return _f(spec.b("height"), 100.0)
    return float((blk.O @ np.array([ln_.x, ln_.baseline, 1.0]))[1])
