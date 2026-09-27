"""vector assets: built-in shape kinds and static SVG files.

Shape kinds reuse the shape node geometry (geometry.shape_commands) and painting (nodes.core.draw_paths),
so fill/stroke paints, strokeStyle (caps, joins, dash, strokePosition, paintOrder) behave like <shape>.
fillRule defaults to evenodd for vector assets (schema default), unlike shape nodes.

shape="svg" renders @src with librsvg (through ctypes on the pycairo context) when PyGObject has Rsvg 2.0;
the SVG is fitted into the asset box per its own preserveAspectRatio. Without librsvg a minimal built-in
parser draws path/rect/circle/ellipse/line/polyline/polygon/g with transforms and fill/stroke styling.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import math
import re

import cairo

from .. import geometry
from ..registry import ASSET_SIZES, ASSETS, FULL, PARTIAL, warn_once

log = logging.getLogger("scenerender")

_VEC_ATTRS = ("radius", "cornerRadii", "points", "innerRadius", "outerRadius", "innerRoundness", "outerRoundness", "path")

# ---------------------------------------------------------------- librsvg
_rsvg = None
try:
    import gi
    gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg  # noqa: F401

    class _RsvgRect(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("width", ctypes.c_double), ("height", ctypes.c_double)]

    _rsvg = ctypes.CDLL(ctypes.util.find_library("rsvg-2") or "librsvg-2.so.2")
    _rsvg.rsvg_handle_render_document.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RsvgRect), ctypes.c_void_p]
    _rsvg.rsvg_handle_render_document.restype = ctypes.c_int
except (ImportError, ValueError, OSError, AttributeError):  # no PyGObject Rsvg or no librsvg
    _rsvg = None

HAVE_RSVG = _rsvg is not None


def _svg_handle(rc, path: str):
    key = ("svg-handle", path)
    if key not in rc.cache:
        try:
            rc.cache[key] = Rsvg.Handle.new_from_file(path)
        except Exception as e:  # noqa: BLE001 — a broken file draws nothing
            warn_once("asset-file", path, f"cannot parse SVG: {e}")
            rc.cache[key] = None
    return rc.cache[key]


def _render_rsvg(rc, cr: cairo.Context, path: str, w: float, h: float) -> bool:
    from ..pango_bridge import _cairo_ptr, _gptr
    hd = _svg_handle(rc, path)
    if hd is None:
        return False
    vp = _RsvgRect(0, 0, w, h)
    cr.save()
    ok = _rsvg.rsvg_handle_render_document(_gptr(hd), _cairo_ptr(cr), ctypes.byref(vp), None)
    cr.restore()
    return bool(ok)


# ---------------------------------------------------------------- fallback SVG subset
_NAMED = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
          "yellow": "#ffff00", "cyan": "#00ffff", "magenta": "#ff00ff", "gray": "#808080", "grey": "#808080",
          "orange": "#ffa500", "purple": "#800080", "transparent": "#00000000"}


def _svg_color(v: str | None, current=(0, 0, 0, 1)):
    if v is None:
        return None
    v = v.strip().lower()
    if v in ("none", ""):
        return None
    if v == "currentcolor":
        return current
    v = _NAMED.get(v, v)
    if v.startswith("#"):
        hx = v[1:]
        if len(hx) in (3, 4):
            hx = "".join(c * 2 for c in hx)
        try:
            vals = [int(hx[i:i + 2], 16) / 255 for i in range(0, len(hx), 2)]
        except ValueError:
            return None
        return tuple(vals[:3]) + ((vals[3],) if len(vals) > 3 else (1.0,))
    m = re.match(r"rgba?\(([^)]*)\)", v)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        rgb = [float(p[:-1]) / 100 if p.endswith("%") else float(p) / 255 for p in parts[:3]]
        a = float(parts[3]) if len(parts) > 3 else 1.0
        return tuple(rgb) + (a,)
    return None


def _svg_transform(s: str | None) -> cairo.Matrix:
    m = cairo.Matrix()
    if not s:
        return m
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s):
        a = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        t = cairo.Matrix()
        if name == "matrix" and len(a) == 6:
            t = cairo.Matrix(*a)
        elif name == "translate":
            t = cairo.Matrix(x0=a[0], y0=a[1] if len(a) > 1 else 0)
        elif name == "scale":
            t = cairo.Matrix(a[0], 0, 0, a[1] if len(a) > 1 else a[0])
        elif name == "rotate":
            r = math.radians(a[0])
            t = cairo.Matrix(math.cos(r), math.sin(r), -math.sin(r), math.cos(r))
            if len(a) == 3:
                t = cairo.Matrix(x0=-a[1], y0=-a[2]).multiply(t).multiply(cairo.Matrix(x0=a[1], y0=a[2]))
        elif name == "skewX":
            t = cairo.Matrix(1, 0, math.tan(math.radians(a[0])), 1)
        elif name == "skewY":
            t = cairo.Matrix(1, math.tan(math.radians(a[0])), 0, 1)
        m = t.multiply(m)   # later transforms apply first (innermost)
    return m


def _num(v, d=0.0):
    try:
        return float(re.match(r"-?[\d.]+(?:e-?\d+)?", str(v)).group(0))
    except (TypeError, AttributeError, ValueError):
        return d


def _style(el, inherited: dict) -> dict:
    st = dict(inherited)
    for k in ("fill", "stroke", "stroke-width", "fill-rule", "fill-opacity", "stroke-opacity",
              "stroke-linecap", "stroke-linejoin", "color"):
        if el.get(k) is not None:
            st[k] = el.get(k)
    for decl in (el.get("style") or "").split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            st[k.strip()] = v.strip()
    own = el.get("opacity")
    m = re.search(r"(?:^|;)\s*opacity\s*:\s*([\d.]+)", el.get("style") or "")
    own = m.group(1) if m else own
    # Group opacity is approximated by multiplying it into descendants (no isolated compositing).
    st["opacity"] = float(inherited.get("opacity", 1.0)) * (float(own) if own is not None else 1.0)
    return st


def _shape_cmds(tag: str, el) -> list | None:
    g = lambda k: _num(el.get(k), 0.0)  # noqa: E731
    if tag == "path":
        return geometry.parse_svg_path(el.get("d"))
    if tag == "rect":
        x, y, w, h = g("x"), g("y"), g("width"), g("height")
        rx = _num(el.get("rx"), _num(el.get("ry"), 0.0))
        return geometry.rounded_rect(x, y, w, h, [min(rx, w / 2, h / 2)] * 4) if rx else geometry.rect(x, y, w, h)
    if tag == "circle":
        return geometry.ellipse(g("cx"), g("cy"), g("r"), g("r"))
    if tag == "ellipse":
        return geometry.ellipse(g("cx"), g("cy"), g("rx"), g("ry"))
    if tag == "line":
        return [("M", g("x1"), g("y1")), ("L", g("x2"), g("y2"))]
    if tag in ("polyline", "polygon"):
        v = [float(x) for x in re.split(r"[\s,]+", (el.get("points") or "").strip()) if x]
        pts = list(zip(v[0::2], v[1::2]))
        if not pts:
            return None
        cmds = [("M", *pts[0])] + [("L", *p) for p in pts[1:]]
        return cmds + [("Z",)] if tag == "polygon" else cmds
    return None


def _draw_svg_node(cr: cairo.Context, el, st: dict) -> None:
    tag = el.tag.split("}")[-1] if isinstance(el.tag, str) else ""
    if tag in ("defs", "title", "desc", "metadata", "style", "script", "clipPath", "mask", "symbol", ""):
        return
    st = _style(el, st)
    cr.save()
    cr.transform(_svg_transform(el.get("transform")))
    if tag in ("g", "svg", "a"):
        for c in el:
            _draw_svg_node(cr, c, st)
    else:
        cmds = _shape_cmds(tag, el)
        if cmds:
            current = _svg_color(st.get("color"), (0, 0, 0, 1)) or (0, 0, 0, 1)
            op = float(st.get("opacity", 1.0))
            fill = None if tag == "line" else _svg_color(st.get("fill", "black"), current)
            stroke = _svg_color(st.get("stroke"), current)
            cr.new_path()
            geometry.emit(cr, cmds)
            if fill:
                cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD if st.get("fill-rule") == "evenodd" else cairo.FILL_RULE_WINDING)
                cr.set_source_rgba(*fill[:3], fill[3] * op * float(st.get("fill-opacity", 1.0)))
                cr.fill_preserve()
            sw = _num(st.get("stroke-width"), 1.0)
            if stroke and sw > 0:
                cr.set_line_width(sw)
                cr.set_line_cap({"round": cairo.LINE_CAP_ROUND, "square": cairo.LINE_CAP_SQUARE}.get(st.get("stroke-linecap"), cairo.LINE_CAP_BUTT))
                cr.set_line_join({"round": cairo.LINE_JOIN_ROUND, "bevel": cairo.LINE_JOIN_BEVEL}.get(st.get("stroke-linejoin"), cairo.LINE_JOIN_MITER))
                cr.set_source_rgba(*stroke[:3], stroke[3] * op * float(st.get("stroke-opacity", 1.0)))
                cr.stroke_preserve()
            cr.new_path()
    cr.restore()


def render_svg_subset(rc, cr: cairo.Context, path: str, w: float, h: float) -> bool:
    """Minimal SVG renderer (used when librsvg is unavailable)."""
    from lxml import etree
    key = ("svg-tree", path)
    if key not in rc.cache:
        try:
            rc.cache[key] = etree.parse(path).getroot()
        except (OSError, etree.XMLSyntaxError) as e:
            warn_once("asset-file", path, f"cannot parse SVG: {e}")
            rc.cache[key] = None
    root = rc.cache[key]
    if root is None:
        return False
    vb = [float(x) for x in re.split(r"[\s,]+", (root.get("viewBox") or "").strip()) if x]
    if len(vb) != 4:
        vb = [0, 0, _num(root.get("width"), w), _num(root.get("height"), h)]
    k = min(w / max(vb[2], 1e-9), h / max(vb[3], 1e-9))       # xMidYMid meet
    cr.save()
    cr.translate((w - vb[2] * k) / 2, (h - vb[3] * k) / 2)
    cr.scale(k, k)
    cr.translate(-vb[0], -vb[1])
    for c in root:
        _draw_svg_node(cr, c, {})
    cr.restore()
    return True


# ---------------------------------------------------------------- handler
@ASSETS.register("vector", level=FULL if HAVE_RSVG else PARTIAL,
                 note="svg via librsvg" if HAVE_RSVG else "svg: minimal built-in subset (no librsvg): "
                      "path/rect/circle/ellipse/line/poly*/g, solid fill/stroke only")
def render_vector(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None, force_subset: bool = False):
    from ..nodes.core import draw_paths
    ev = rc.ev
    w, h = ev.num(asset, "width", ctx), ev.num(asset, "height", ctx)
    kind = ev.str(asset, "shape", ctx)
    if kind == "svg":
        if not ev.str(asset, "src", ctx):
            warn_once("vector", asset.get("id"), 'shape="svg" without @src')
            return None
        from .video import provenance_src
        path = provenance_src(rc, asset, ctx)
        c = rc.canvas_for(M, w, h, 2)
        if c is None:
            return None
        if clip:
            c.cr.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
            c.cr.clip()
        ok = (_render_rsvg(rc, c.cr, path, w, h) if HAVE_RSVG and not force_subset
              else render_svg_subset(rc, c.cr, path, w, h))
        return c.to_buf(rc.linear) if ok else None
    a = rc.eval_attrs(asset, ctx, _VEC_ATTRS)
    cmds = geometry.shape_commands(kind, w, h, a)
    if not cmds:
        return None
    x0, y0, x1, y1 = geometry.bounds(cmds)
    sw = ev.num(asset, "strokeWidth", ctx, 0.0) * (2 if ev.str(asset, "strokePosition", ctx) == "outside" else 1)
    pad = sw * max(2.0, ev.num(asset, "miterLimit", ctx, 4.0) / 2) + 2
    from ..raster import Canvas, intersect, transformed_rect
    r = transformed_rect(M, min(0, x0) - pad, min(0, y0) - pad, max(w, x1) + pad, max(h, y1) + pad, 1)
    r = intersect(r, (-64, -64, rc.width + 64, rc.height + 64))
    if r is None:
        return None
    c = Canvas(r)
    c.set_matrix(M)
    c.cr.set_antialias(cairo.ANTIALIAS_GOOD)
    if clip:
        c.cr.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
        c.cr.clip()
    draw_paths(rc, c.cr, asset, ctx, [cmds], w, h, default_rule="evenodd")
    return c.to_buf(rc.linear)


@ASSET_SIZES.register("vector")
def vector_size(rc, asset, ctx):
    return rc.ev.num(asset, "width", ctx), rc.ev.num(asset, "height", ctx)
