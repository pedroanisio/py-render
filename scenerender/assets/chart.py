"""chart assets: bar, column, line, area, pie, donut, scatter, counter, progress.

Data
    <series name values color> children (values animate like any numberList; color is any paint, default
    from an 8-colour categorical palette). Without series, @src names a data file:
      * CSV: header row = [category header, series names...], then one row per category (first cell = the
        label, other cells = values; blank/non-numeric cells are 0). For scatter the first column is x and
        every other column a y series.
      * JSON: {"labels": [...], "series": [{"name", "values", "color"?}], "categoryTitle"?, "valueTitle"?}
        (scatter values are x y pairs).
    @labels: category labels, comma-separated (CSV quoting allowed: "a, b",c). @labels beats the file's.
    scatter: each series' values are x y pairs. counter: one value counts from 0, two values from the first
    to the second. progress: every series' first value is a fraction (values > 1 are percentages).

Layout (pinned; the schema has no axis/legend attributes, so these follow spreadsheet defaults)
    Text size: the textStyle's size, else 4.5 % of the chart height (10..48 px). All text (tick labels,
    category labels, legend, value labels, axis titles, counter, percentages) is laid out and drawn by the
    text asset engine (scenerender.assets.text), so every textStyle attribute applies (font / fontFile /
    fontAsset / fallback, weight, fontStyle, stretch, variation, features, tracking, textTransform,
    decoration, highlight, stroke*, shadow*, baselineShift, colour or paint).
    Cartesian kinds (bar = horizontal bars, column, line, area): value axis with "nice" ticks (1-2-2.5-5
    steps, about one per 3.5 text heights, always including 0), gridlines at every tick, a stronger zero
    line, category labels under (column/line/area) or left of (bar) the plot; category labels that do not
    fit their band are rotated 45 degrees. Axis titles exist only when the data file names them (CSV:
    the header's first cell, unless empty or a generic "label"/"labels"/"category"/"x"; JSON:
    categoryTitle / valueTitle). @showAxes=false hides ticks, gridlines, category labels and axis titles.
    Legend (swatch + series name, wrapping rows) above the plot when there is more than one series
    (cartesian and scatter).
    Multiple series: bar/column grouped side by side; line overlaid; area stacked (cumulative, each band
    filled at 75 % opacity with its top line). @showValues labels bars at their end, line points above the
    point, area bands at the band top (the series' own value).
    scatter: x and y value axes with nice ticks and gridlines, circular markers; showValues labels "x, y".
    pie/donut: slices clockwise from 12 o'clock; a single series with several values gives one slice per
    value (palette colours, labels from @labels), several series one slice per series (series colour, name,
    first value). Category labels sit outside with leader lines; showValues puts the percentage inside the
    slice (the formatted value above it when @format is given); a donut with showValues shows the total in
    its hole.
    counter: the number, centred, as large as fits (the textStyle size when given and it fits); the first
    @labels entry is a caption under it. The first series' @color colours the number.
    progress: a pill bar (box aspect outside 0.75..1.33) or a ring (squarish box); several series stack
    bars top to bottom / nest rings outside in, each with its name as caption when there are several;
    showValues prints the percentage (or @format of the value) at the bar end / ring centre.

@progress (0..1, animatable) draws the chart on, for every kind: bars/columns grow with a small per-category
stagger, lines and areas reveal left to right, pie/donut sweep clockwise from 12 o'clock, scatter points pop
in in order, counters count, progress bars/rings fill; axes, legend and labels fade in over the first 20 %
and value labels appear with their mark. progress 0 draws nothing (except the counter value and the
progress track).

@format: Python format ("{:,.1f}"), printf ("%.1f%%"), or a spreadsheet/ICU pattern ("#,##0.0", "0%",
"$#,##0", "0.00 'kg'"). Without it, numbers use thousands separators and the data's decimal places
(ticks: the tick step's decimals). @format applies to value labels, value-axis ticks, counters and
progress values (pie percentages always use "%").
"""
from __future__ import annotations

import csv
import io
import json
import math
import re

import cairo
import numpy as np

from .. import paint as paintmod
from ..raster import Buf
from ..registry import ASSET_SIZES, ASSETS, FULL, warn_once
from ..values import parse_color

PALETTE = ("#3DD6D0FF", "#FFC23DFF", "#FF6B6BFF", "#7C8CFFFF", "#9BE564FF", "#F78FE0FF", "#5AB0FFFF", "#FF9F43FF")
_GENERIC_TITLES = {"", "label", "labels", "category", "categories", "x", "name"}


# ---------------------------------------------------------------- number format
_PRINTF = re.compile(r"%[-+ 0#]*\d*(?:\.\d+)?[dfFeEgGxXi]")


def format_number(v: float, fmt: str | None, decimals: int = 0) -> str:
    if not fmt:
        return f"{v:,.{decimals}f}"
    try:
        if "{" in fmt:
            return fmt.format(v)
        if _PRINTF.search(fmt):
            return _PRINTF.sub(lambda m: (m.group(0) % (int(round(v)) if m.group(0)[-1] in "dixX" else v)), fmt,
                               count=1).replace("%%", "%")
    except (ValueError, IndexError, KeyError):
        return f"{v:,.{decimals}f}"
    # spreadsheet / ICU pattern: literal prefix, numeric part [#,0.], literal suffix ('quoted' text allowed)
    m = re.search(r"[#0][#0,]*(?:\.[0#]+)?", fmt)
    if not m:
        return fmt
    num = m.group(0)
    pre, post = fmt[:m.start()], fmt[m.end():]
    lit = lambda s: re.sub(r"'([^']*)'", r"\1", s).replace('"', "")  # noqa: E731
    if "%" in pre or "%" in post:
        v *= 100
    frac = num.split(".")[1] if "." in num else ""
    dec = len(frac)
    s = f"{v:,.{dec}f}" if "," in num else f"{v:.{dec}f}"
    min_dec = frac.count("0")
    if dec > min_dec and "." in s:
        head, tail = s.split(".")
        tail = tail.rstrip("0")
        tail = tail + "0" * max(0, min_dec - len(tail))
        s = head + ("." + tail if tail else "")
    return lit(pre) + s + lit(post)


def _decimals(vals) -> int:
    d = 0
    for v in vals:
        s = f"{v:.6f}".rstrip("0")
        d = max(d, len(s.split(".")[1]) if "." in s else 0)
    return min(d, 3)


# ---------------------------------------------------------------- text (through the text asset engine)
class _Text:
    """Queues labels while the geometry is drawn; renders them with assets.text afterwards."""

    def __init__(self, rc, asset, ctx, h):
        from .text import RUN_KEYS
        self.rc, self.ctx = rc, ctx
        sid = asset.get("textStyle")
        st = rc.doc.text_styles.get(sid, {}) if sid else {}
        if sid and not st:
            warn_once("chart", asset.get("id"), f"textStyle {sid!r} not found")
        st = {k: v for k, v in st.items() if k in RUN_KEYS and v is not None}
        self.explicit = bool(st.get("size"))
        self.size = float(st["size"]) if st.get("size") else max(10.0, min(48.0, h * 0.045))
        st["size"] = self.size
        st.setdefault("color", "#F4F6FBFF")
        self.base = st
        self.color = parse_color(st["color"], rc.doc.tokens, (0.96, 0.96, 0.98, 1.0)) \
            if not paintmod.paint_ref(st["color"]) else (0.96, 0.96, 0.98, 1.0)
        self.queue: list = []

    def _spec(self, text, size=None, color=None, weight=None):
        from .text import Run, make_spec
        st = dict(self.base)
        if size:
            st["size"] = size
        if color is not None:
            st["color"] = color if isinstance(color, str) else "#%02X%02X%02X%02X" % tuple(
                int(round(max(0, min(1, c)) * 255)) for c in color)
        if weight:
            st["weight"] = weight
        return make_spec([Run(text, st)], width=100000.0, height=float(st["size"]) * 4, wrap="none",
                         size=float(st["size"]))

    def measure(self, text: str, size: float | None = None, weight=None) -> tuple[float, float]:
        from .text import get_block
        if not text:
            return 0.0, 0.0
        b = get_block(self.rc, self._spec(text, size, None, weight))
        x0, y0, x1, y1 = b.logical
        return x1 - x0, y1 - y0

    def draw(self, text, x, y, ha="l", va="t", size=None, alpha=1.0, color=None, weight=None, rotate=0.0):
        if alpha > 1e-4 and text:
            self.queue.append((text, x, y, ha, va, size, min(1.0, alpha), color, weight, rotate))

    def flush(self, base: Buf | None, M) -> Buf | None:
        from ..blend import composite
        from .text import get_block, render_block
        for text, x, y, ha, va, size, alpha, color, weight, rot in self.queue:
            b = get_block(self.rc, self._spec(text, size, color, weight))
            x0, y0, x1, y1 = b.logical
            tw, th = x1 - x0, y1 - y0
            fx, fy = {"l": 0.0, "c": 0.5, "r": 1.0}[ha], {"t": 0.0, "m": 0.5, "b": 1.0}[va]
            ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            T = np.array([[ca, -sa, x], [sa, ca, y], [0, 0, 1]], np.float64) @ \
                np.array([[1, 0, -fx * tw], [0, 1, -fy * th], [0, 0, 1]], np.float64)
            tb = render_block(self.rc, b, M @ T, self.ctx)
            if tb is None:
                continue
            if alpha < 1:
                tb.px *= alpha
            base = tb if base is None else composite(base, tb)
        self.queue.clear()
        return base


# ---------------------------------------------------------------- data
def _split_labels(s: str) -> list[str]:
    if not s:
        return []
    row = next(csv.reader(io.StringIO(s), skipinitialspace=True), [])
    return [x.strip() for x in row]


def _read_src(rc, asset, kind) -> dict:
    path = rc.doc.resolve_path(asset.get("src"))
    key = ("chart-src", path, kind)
    if key in rc.cache:
        return rc.cache[key]
    out: dict = {"series": [], "labels": [], "cat_title": "", "val_title": ""}
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            txt = f.read()
    except OSError as e:
        warn_once("asset-file", path, f"cannot read chart data: {e}")
        rc.cache[key] = out
        return out
    if path.lower().endswith(".json") or txt.lstrip().startswith(("{", "[")):
        try:
            d = json.loads(txt)
            d = {"series": d} if isinstance(d, list) else d
            out["labels"] = [str(x) for x in d.get("labels", [])]
            out["cat_title"], out["val_title"] = str(d.get("categoryTitle", "")), str(d.get("valueTitle", ""))
            for s in d.get("series", []):
                out["series"].append({"name": str(s.get("name", "")), "values": [float(v) for v in s.get("values", [])],
                                      "color": s.get("color")})
        except (ValueError, AttributeError, TypeError) as e:
            warn_once("asset-file", path, f"bad chart JSON: {e}")
    else:
        rows = [r for r in csv.reader(io.StringIO(txt)) if r]

        def num(r, j):
            try:
                return float(r[j])
            except (IndexError, ValueError):
                return 0.0
        if rows:
            head, body = rows[0], rows[1:]
            out["cat_title"] = head[0].strip() if head and head[0].strip().lower() not in _GENERIC_TITLES else ""
            if kind == "scatter":
                for j, name in enumerate(head[1:], 1):
                    vals = []
                    for r in body:
                        vals += [num(r, 0), num(r, j)]
                    out["series"].append({"name": name, "values": vals, "color": None})
            else:
                out["labels"] = [r[0] for r in body]
                for j, name in enumerate(head[1:], 1):
                    out["series"].append({"name": name, "values": [num(r, j) for r in body], "color": None})
    rc.cache[key] = out
    return out


def _series(rc, asset, ctx, kind):
    out = []
    for i, s in enumerate(c for c in asset if getattr(c, "tag", None) == "series"):
        vals = rc.ev.get(s, "values", ctx, ())
        if isinstance(vals, (int, float)):
            vals = (float(vals),)
        vals = [float(v) for v in (vals or ())]
        out.append({"name": rc.ev.str(s, "name", ctx, s.get("name")) or "", "values": vals,
                    "color": rc.ev.str(s, "color", ctx, None) or PALETTE[i % len(PALETTE)], "el": s,
                    "own_color": s.get("color") is not None})
    labels = _split_labels(rc.ev.str(asset, "labels", ctx, "") or "")
    titles = ("", "")
    if not out and asset.get("src"):
        d = _read_src(rc, asset, kind)
        for i, s in enumerate(d["series"]):
            out.append({"name": s["name"], "values": list(s["values"]), "color": s["color"] or PALETTE[i % len(PALETTE)],
                        "el": asset, "own_color": s["color"] is not None})
        labels = labels or list(d["labels"])
        titles = (d["cat_title"], d["val_title"])
    return out, labels, titles


# ---------------------------------------------------------------- scales
def _nice_step(x: float) -> float:
    e = 10 ** math.floor(math.log10(x))
    f = x / e
    return e * (1 if f <= 1 else 2 if f <= 2 else 2.5 if f <= 2.5 else 5 if f <= 5 else 10)


def _ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """Round tick values covering [lo, hi] (about n intervals)."""
    if hi - lo <= 1e-12:
        hi = lo + 1
    step = _nice_step((hi - lo) / max(1, n))
    b, t = math.floor(lo / step + 1e-9), math.ceil(hi / step - 1e-9)
    return [round(k * step, 10) for k in range(b, t + 1)]


def _tick_decimals(ticks) -> int:
    return _decimals([ticks[1] - ticks[0]] + ticks) if len(ticks) > 1 else _decimals(ticks)


def _stagger(progress: float, i: int, n: int, s: float = 0.12) -> float:
    if n <= 1:
        return max(0.0, min(1.0, progress))
    return max(0.0, min(1.0, progress * (1 + s * (n - 1)) - s * i))


def _src(rc, cr, spec, w, h, ctx, el, alpha=1.0) -> bool:
    ref = paintmod.paint_ref(spec)
    if ref is None:
        c = parse_color(spec, rc.doc.tokens, (1, 1, 1, 1))
        if c[3] * alpha <= 0:
            return False
        cr.set_source_rgba(c[0], c[1], c[2], c[3] * alpha)
        return True
    if alpha < 1:
        cr.push_group()
        ok = paintmod.set_source(rc, cr, spec, w, h, ctx, el)
        cr.pop_group_to_source()
        return ok
    return paintmod.set_source(rc, cr, spec, w, h, ctx, el)


def _fill(rc, cr, spec, w, h, ctx, el, alpha=1.0):
    """Fill the current path with a colour or paint at alpha (clears the path)."""
    if alpha >= 1 or paintmod.paint_ref(spec) is None:
        if _src(rc, cr, spec, w, h, ctx, el, alpha):
            cr.fill()
        cr.new_path()
        return
    path = cr.copy_path()
    cr.new_path()
    cr.push_group()
    cr.append_path(path)
    if paintmod.set_source(rc, cr, spec, w, h, ctx, el):
        cr.fill()
    cr.pop_group_to_source()
    cr.paint_with_alpha(alpha)


def _stroke(rc, cr, spec, w, h, ctx, el, alpha=1.0):
    if _src(rc, cr, spec, w, h, ctx, el, alpha):
        cr.stroke()
    cr.new_path()


def _rrect(cr, x, y, w, h, r):
    r = max(0.0, min(r, abs(w) / 2, abs(h) / 2))
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


class _Chart:
    def __init__(self, rc, asset, ctx, cr, w, h, prog, T: _Text, fmt, show_axes, show_values):
        self.rc, self.asset, self.ctx, self.cr = rc, asset, ctx, cr
        self.w, self.h, self.prog, self.T, self.fmt = w, h, prog, T, fmt
        self.show_axes, self.show_values = show_axes, show_values
        self.fs = T.size
        self.ax_a = max(0.0, min(1.0, prog / 0.2))

    def num(self, v: float, dec: int) -> str:
        """Value label: @format, else spreadsheet "General" (up to the data's decimals, trailing zeros dropped)."""
        if self.fmt:
            return format_number(v, self.fmt, dec)
        return format_number(v, None, _decimals([round(v, dec)]))

    def grid_colour(self, a):
        c = self.T.color
        return c[0], c[1], c[2], c[3] * a

    # ------------------------------------------------------------ legend
    def legend(self, series, top: float) -> float:
        """Draw the legend rows from y=top; returns the height used."""
        if len(series) < 2:
            return 0.0
        fs, T = self.fs, self.T
        sw, gap = fs * 0.75, fs * 1.1
        items = [(s, T.measure(s["name"])[0]) for s in series]
        rows, row, x = [], [], 0.0
        maxw = self.w - fs
        for it in items:
            iw = sw + fs * 0.4 + it[1]
            if row and x + iw > maxw:
                rows.append(row)
                row, x = [], 0.0
            row.append(it)
            x += iw + gap
        rows.append(row)
        lh = fs * 1.45
        for ri, row in enumerate(rows):
            tot = sum(sw + fs * 0.4 + tw for _, tw in row) + gap * (len(row) - 1)
            x = (self.w - tot) / 2
            y = top + ri * lh + lh / 2
            for s, tw in row:
                _rrect(self.cr, x, y - sw / 2, sw, sw, sw * 0.2)
                _fill(self.rc, self.cr, s["color"], self.w, self.h, self.ctx, s["el"], self.ax_a)
                T.draw(s["name"], x + sw + fs * 0.4, y, "l", "m", alpha=self.ax_a)
                x += sw + fs * 0.4 + tw + gap
        return len(rows) * lh + fs * 0.3

    # ------------------------------------------------------------ cartesian
    def cartesian(self, kind, series, labels, titles):
        rc, cr, T, fs, fmt = self.rc, self.cr, self.T, self.fs, self.fmt
        w, h, prog = self.w, self.h, self.prog
        n = max(len(labels), max((len(s["values"]) for s in series), default=0))
        if n == 0:
            return
        horizontal = kind == "bar"
        stacked = kind == "area"
        vals = [[(s["values"][i] if i < len(s["values"]) else 0.0) for i in range(n)] for s in series]
        if stacked:
            cum, tops = [0.0] * n, []
            for v in vals:
                cum = [a + b for a, b in zip(cum, v)]
                tops.append(list(cum))
            extent = [x for t in tops for x in t]
        else:
            tops = vals
            extent = [x for v in vals for x in v]
        extent = extent or [0.0]
        lo, hi = min(0.0, min(extent)), max(0.0, max(extent))
        dec = _decimals([x for v in vals for x in v])
        cat_title, val_title = titles if self.show_axes else ("", "")
        top = fs * 0.4 + self.legend(series, fs * 0.2)
        # provisional ticks to size the value-axis labels
        val_len = (w if horizontal else h) * 0.7
        nt = int(max(2, min(8, val_len / (fs * 3.5))))
        ticks = _ticks(lo, hi, nt)
        tdec = _tick_decimals(ticks)
        tick_txt = [format_number(t, fmt, tdec) for t in ticks]
        tick_w = max(T.measure(t)[0] for t in tick_txt) if self.show_axes else 0.0
        lab_w = max((T.measure(lab)[0] for lab in labels[:n]), default=0.0) if (self.show_axes and labels) else 0.0
        val_w = max((T.measure(self.num(v, dec), fs * 0.9)[0] for v in extent), default=0.0) \
            if self.show_values else 0.0
        neg = self.show_values and lo < 0
        title_h = fs * 1.4
        left, right, bottom = fs * 0.5, fs * 0.8, fs * 0.5
        if horizontal:
            left += (lab_w + fs * 0.6) if lab_w else 0.0
            left += title_h if cat_title else 0.0
            bottom += (fs * 1.6) if self.show_axes else 0.0
            bottom += title_h if val_title else 0.0
            right += val_w + fs * 0.4 if self.show_values else 0.0
            left += val_w + fs * 0.4 if neg else 0.0
            pw_guess = max(1.0, w - left - right)
            band_guess = (h - top - bottom) / n
            rot = False
        else:
            left += (tick_w + fs * 0.6) if self.show_axes else 0.0
            left += title_h if val_title else 0.0
            pw_guess = max(1.0, w - left - right)
            band_guess = pw_guess / n
            rot = bool(lab_w) and lab_w > band_guess * 0.92
            lab_h = fs * 1.2
            if lab_w:
                bottom += (lab_w * 0.7071 + lab_h * 0.7071 + fs * 0.4) if rot else (lab_h + fs * 0.4)
            bottom += title_h if cat_title else 0.0
            top += fs * 1.2 if self.show_values else 0.0
            bottom += fs * 1.2 if neg else 0.0
        x0, y0, x1, y1 = left, top, w - right, h - bottom
        pw, ph = max(1.0, x1 - x0), max(1.0, y1 - y0)
        bot_v, top_v = ticks[0], ticks[-1]

        def vpos(v):
            u = (v - bot_v) / (top_v - bot_v)
            return x0 + u * pw if horizontal else y1 - u * ph

        band = (ph if horizontal else pw) / n

        def cat_center(i):
            return (y0 + band * (i + 0.5)) if horizontal else (x0 + band * (i + 0.5))

        ax = self.ax_a
        if self.show_axes and ax > 0:
            cr.set_line_width(max(1.0, fs * 0.06))
            for t, txt in zip(ticks, tick_txt):
                p = vpos(t)
                cr.set_source_rgba(*self.grid_colour(ax * (0.18 if abs(t) > 1e-12 else 0.7)))
                if horizontal:
                    cr.move_to(p, y0)
                    cr.line_to(p, y1)
                else:
                    cr.move_to(x0, p)
                    cr.line_to(x1, p)
                cr.stroke()
                if horizontal:
                    T.draw(txt, p, y1 + fs * 0.35, "c", "t", alpha=ax * 0.85)
                else:
                    T.draw(txt, x0 - fs * 0.45, p, "r", "m", alpha=ax * 0.85)
            cr.set_source_rgba(*self.grid_colour(ax * 0.55))
            if horizontal:                                            # category axis line
                cr.move_to(x0, y0)
                cr.line_to(x0, y1)
            else:
                cr.move_to(x0, y1)
                cr.line_to(x1, y1)
            cr.stroke()
            for i, lab in enumerate(labels[:n]):
                if horizontal:
                    T.draw(lab, x0 - fs * 0.45 - (val_w + fs * 0.4 if neg else 0.0), cat_center(i), "r", "m", alpha=ax)
                elif rot:
                    T.draw(lab, cat_center(i), y1 + fs * (1.55 if neg else 0.35), "r", "t", alpha=ax, rotate=-45)
                else:
                    T.draw(lab, cat_center(i), y1 + fs * (1.55 if neg else 0.35), "c", "t", alpha=ax)
            if cat_title:
                if horizontal:
                    T.draw(cat_title, fs * 0.3, (y0 + y1) / 2, "c", "t", alpha=ax, rotate=-90, weight=700)
                else:
                    T.draw(cat_title, (x0 + x1) / 2, h - fs * 0.3, "c", "b", alpha=ax, weight=700)
            if val_title:
                if horizontal:
                    T.draw(val_title, (x0 + x1) / 2, h - fs * 0.3, "c", "b", alpha=ax, weight=700)
                else:
                    T.draw(val_title, fs * 0.3, (y0 + y1) / 2, "c", "t", alpha=ax, rotate=-90, weight=700)
        if kind in ("bar", "column"):
            ns = max(1, len(series))
            gw = band * 0.76
            bw = gw / ns
            for i in range(n):
                pi = _stagger(prog, i, n)
                if pi <= 0:
                    continue
                for j, s in enumerate(series):
                    if i >= len(s["values"]):
                        continue
                    v = s["values"][i] * pi
                    c0 = cat_center(i) - gw / 2 + j * bw
                    a, b = vpos(0), vpos(v)
                    r = min(bw * 0.12, fs * 0.25)
                    if horizontal:
                        _rrect(cr, min(a, b), c0 + bw * 0.06, abs(b - a), bw * 0.88, r)
                    else:
                        _rrect(cr, c0 + bw * 0.06, min(a, b), bw * 0.88, abs(b - a), r)
                    _fill(rc, cr, s["color"], w, h, self.ctx, s["el"])
                    if self.show_values:
                        txt = self.num(v, dec)
                        la = min(1.0, pi * 3)
                        if horizontal:
                            T.draw(txt, b + fs * 0.3 * (1 if v >= 0 else -1), c0 + bw / 2, "l" if v >= 0 else "r",
                                   "m", size=fs * 0.9, alpha=la)
                        else:
                            T.draw(txt, c0 + bw / 2, b - fs * 0.2 if v >= 0 else b + fs * 0.2, "c",
                                   "b" if v >= 0 else "t", size=fs * min(0.9, 1.8 * bw / max(fs, 1)), alpha=la)
            return
        # line / area: reveal left to right by clipping at xc
        xc = x0 + pw * prog
        base_line = [vpos(0)] * n
        cr.save()
        cr.rectangle(0, 0, xc, h)
        cr.clip()
        prev = [0.0] * n
        for si, s in enumerate(series):
            ys = [vpos(v) for v in tops[si]]
            xs = [cat_center(i) for i in range(n)]
            if stacked:
                below = [vpos(v) for v in prev] if si else base_line
                cr.move_to(xs[0], below[0])
                for x, y in zip(xs, ys):
                    cr.line_to(x, y)
                for x, y in zip(reversed(xs), reversed(below)):
                    cr.line_to(x, y)
                cr.close_path()
                _fill(rc, cr, s["color"], w, h, self.ctx, s["el"], 0.75)
                prev = tops[si]
            cr.move_to(xs[0], ys[0])
            for x, y in zip(xs[1:], ys[1:]):
                cr.line_to(x, y)
            cr.set_line_width(max(1.5, fs * 0.16))
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            _stroke(rc, cr, s["color"], w, h, self.ctx, s["el"])
        cr.restore()
        for si, s in enumerate(series):
            for i in range(n):
                x, y = cat_center(i), vpos(tops[si][i])
                if x > xc + 1e-6:
                    continue
                if kind == "line":
                    cr.arc(x, y, max(2.0, fs * 0.22), 0, 2 * math.pi)
                    _fill(rc, cr, s["color"], w, h, self.ctx, s["el"])
                if self.show_values:
                    T.draw(self.num(vals[si][i], dec), x, y - fs * 0.4, "c", "b", size=fs * 0.9)

    # ------------------------------------------------------------ scatter
    def scatter(self, series, titles):
        rc, cr, T, fs, fmt = self.rc, self.cr, self.T, self.fs, self.fmt
        w, h, prog = self.w, self.h, self.prog
        pts = [(s, s["values"][i], s["values"][i + 1]) for s in series for i in range(0, len(s["values"]) - 1, 2)]
        if not pts:
            return
        xs, ys = [p[1] for p in pts], [p[2] for p in pts]
        cat_title, val_title = titles if self.show_axes else ("", "")
        top = fs * 0.4 + self.legend(series, fs * 0.2)
        xt = _ticks(min(0.0, min(xs)), max(0.0, max(xs)), int(max(2, min(8, w * 0.7 / (fs * 5)))))
        yt = _ticks(min(0.0, min(ys)), max(0.0, max(ys)), int(max(2, min(8, h * 0.7 / (fs * 3.5)))))
        xd, yd = _tick_decimals(xt), _tick_decimals(yt)
        ytxt = [format_number(t, fmt, yd) for t in yt]
        xtxt = [format_number(t, fmt, xd) for t in xt]
        left = fs * 0.5 + ((max(T.measure(t)[0] for t in ytxt) + fs * 0.6) if self.show_axes else 0.0)
        left += fs * 1.4 if val_title else 0.0
        bottom = fs * 0.5 + (fs * 1.6 if self.show_axes else 0.0) + (fs * 1.4 if cat_title else 0.0)
        right = fs * 0.5 + T.measure(xtxt[-1])[0] / 2 if self.show_axes else fs * 0.5
        x0, y0, x1, y1 = left, top + (fs * 0.6 if self.show_axes else 0), w - right, h - bottom
        pw, ph = max(1.0, x1 - x0), max(1.0, y1 - y0)
        px = lambda v: x0 + (v - xt[0]) / (xt[-1] - xt[0]) * pw  # noqa: E731
        py = lambda v: y1 - (v - yt[0]) / (yt[-1] - yt[0]) * ph  # noqa: E731
        ax = self.ax_a
        if self.show_axes and ax > 0:
            cr.set_line_width(max(1.0, fs * 0.06))
            for t, txt in zip(yt, ytxt):
                cr.set_source_rgba(*self.grid_colour(ax * (0.18 if abs(t) > 1e-12 else 0.6)))
                cr.move_to(x0, py(t))
                cr.line_to(x1, py(t))
                cr.stroke()
                T.draw(txt, x0 - fs * 0.45, py(t), "r", "m", alpha=ax * 0.85)
            for t, txt in zip(xt, xtxt):
                cr.set_source_rgba(*self.grid_colour(ax * (0.18 if abs(t) > 1e-12 else 0.6)))
                cr.move_to(px(t), y0)
                cr.line_to(px(t), y1)
                cr.stroke()
                T.draw(txt, px(t), y1 + fs * 0.35, "c", "t", alpha=ax * 0.85)
            if cat_title:
                T.draw(cat_title, (x0 + x1) / 2, h - fs * 0.3, "c", "b", alpha=ax, weight=700)
            if val_title:
                T.draw(val_title, fs * 0.3, (y0 + y1) / 2, "c", "t", alpha=ax, rotate=-90, weight=700)
        n = len(pts)
        dec = _decimals(xs + ys)
        for k, (s, xv, yv) in enumerate(pts):
            pk = max(0.0, min(1.0, prog * n - k * 0.8)) if n > 1 else prog
            if pk <= 0:
                continue
            r = max(2.0, fs * 0.32) * (1 + 0.25 * math.sin(math.pi * pk)) * min(1.0, pk * 1.5)
            cr.arc(px(xv), py(yv), r, 0, 2 * math.pi)
            _fill(rc, cr, s["color"], w, h, self.ctx, s["el"], 0.88)
            if self.show_values and pk >= 1:
                T.draw(f"{self.num(xv, dec)}, {self.num(yv, dec)}", px(xv), py(yv) - fs * 0.5,
                       "c", "b", size=fs * 0.8)

    # ------------------------------------------------------------ pie / donut
    def pie(self, kind, series, labels):
        rc, cr, T, fs, fmt = self.rc, self.cr, self.T, self.fs, self.fmt
        w, h, prog = self.w, self.h, self.prog
        if len(series) == 1 or any(len(s["values"]) > 1 for s in series):
            s0 = series[0]
            slices = [(labels[i] if i < len(labels) else "", v, PALETTE[i % len(PALETTE)], s0["el"])
                      for i, v in enumerate(s0["values"])]
        else:
            slices = [(s["name"], s["values"][0] if s["values"] else 0.0, s["color"], s["el"]) for s in series]
        total = sum(max(0.0, v) for _, v, _, _ in slices)
        if total <= 0 or prog <= 0:
            return
        lab_w = max((T.measure(lab)[0] for lab, *_ in slices if lab), default=0.0)
        has_lab = lab_w > 0
        r = min(h / 2 - (fs * 1.6 if has_lab else fs * 0.3), w / 2 - (lab_w + fs * 1.6 if has_lab else fs * 0.3))
        r = max(r, min(w, h) * 0.2, 1.0)
        cx, cy = w / 2, h / 2
        inner = r * 0.6 if kind == "donut" else 0.0
        sweep_end = -math.pi / 2 + 2 * math.pi * min(1.0, prog)
        a = -math.pi / 2
        dec = _decimals([v for _, v, _, _ in slices])
        pct_dec = 0 if len(slices) <= 20 else 1
        for lab, v, col, el in slices:
            span = 2 * math.pi * max(0.0, v) / total
            b = min(a + span, sweep_end)
            if b > a:
                cr.new_path()
                cr.arc(cx, cy, r, a, b)
                if inner:
                    cr.arc_negative(cx, cy, inner, b, a)
                else:
                    cr.line_to(cx, cy)
                cr.close_path()
                _fill(rc, cr, col, w, h, self.ctx, el)
                # slice separator in the background-free sense: a thin gap drawn as clear
                cr.set_operator(cairo.OPERATOR_CLEAR)
                cr.set_line_width(max(1.0, fs * 0.08))
                for ang in (a, b) if span < 2 * math.pi - 1e-6 else ():
                    cr.move_to(cx + math.cos(ang) * inner, cy + math.sin(ang) * inner)
                    cr.line_to(cx + math.cos(ang) * (r + 1), cy + math.sin(ang) * (r + 1))
                    cr.stroke()
                cr.set_operator(cairo.OPERATOR_OVER)
                if b >= a + span - 1e-9 and span > 0:
                    mid = a + span / 2
                    ca, sa = math.cos(mid), math.sin(mid)
                    if lab:
                        p1 = (cx + ca * (r + fs * 0.2), cy + sa * (r + fs * 0.2))
                        p2 = (cx + ca * (r + fs * 0.7), cy + sa * (r + fs * 0.7))
                        p3 = (p2[0] + (fs * 0.5 if ca >= 0 else -fs * 0.5), p2[1])
                        cr.move_to(*p1)
                        cr.line_to(*p2)
                        cr.line_to(*p3)
                        cr.set_line_width(max(1.0, fs * 0.07))
                        cr.set_source_rgba(*self.grid_colour(0.6))
                        cr.stroke()
                        T.draw(lab, p3[0] + (fs * 0.25 if ca >= 0 else -fs * 0.25), p3[1], "l" if ca >= 0 else "r", "m")
                    if self.show_values:
                        rv = (r + inner) / 2 if inner else r * 0.64
                        pct = f"{100.0 * max(0.0, v) / total:.{pct_dec}f}%"
                        txt = f"{self.num(v, dec)}\n{pct}" if fmt else pct
                        vs = fs * 0.9 if not inner else min(fs * 0.9, (r - inner) * 0.42)
                        T.draw(txt, cx + ca * rv, cy + sa * rv, "c", "m", size=vs, color=(0.04, 0.06, 0.1, 1),
                               weight=700)
            a += span
        if inner and self.show_values and prog >= 1:
            T.draw(self.num(total, dec), cx, cy, "c", "m", size=min(inner * 0.6, fs * 1.8), weight=700)

    # ------------------------------------------------------------ counter
    def counter(self, series, labels):
        T, fmt, w, h = self.T, self.fmt, self.w, self.h
        vals = series[0]["values"] if series else [0.0]
        a, b = (vals[0], vals[1]) if len(vals) >= 2 else (0.0, vals[0] if vals else 0.0)
        v = a + (b - a) * max(0.0, min(1.0, self.prog))
        dec = _decimals([a, b])
        txt = format_number(v, fmt, dec)
        wide = format_number(b if abs(b) >= abs(a) else a, fmt, dec)            # stable size while counting
        cap = labels[0] if labels else ""
        cap_h = h * 0.22 if cap else 0.0
        size = T.size if T.explicit and T.size < (h - cap_h) else (h - cap_h) * 0.62
        tw, th = T.measure(wide, size)
        if tw > w * 0.96 and tw > 0:
            size *= w * 0.96 / tw
            th *= w * 0.96 / tw
        col = series[0]["color"] if series and series[0]["own_color"] else None
        cy = (h - cap_h) / 2
        T.draw(txt, w / 2, cy, "c", "m", size=size, color=col)
        if cap:
            T.draw(cap, w / 2, cy + th / 2 + cap_h * 0.1, "c", "t", size=max(8.0, min(cap_h * 0.7, size * 0.4)))

    # ------------------------------------------------------------ progress
    def progress(self, series, labels):
        rc, cr, T, fs, fmt, w, h = self.rc, self.cr, self.T, self.fs, self.fmt, self.w, self.h
        items = series or [{"name": "", "values": [1.0], "color": PALETTE[0], "el": self.asset, "own_color": False}]
        fracs = []
        for s in items:
            v = s["values"][0] if s["values"] else 0.0
            f = v / 100.0 if v > 1 else v
            fracs.append((max(0.0, min(1.0, f)), v))
        names = [s["name"] if len(items) > 1 else (labels[0] if labels else "") for s in items]
        track = self.grid_colour(0.14)
        p = max(0.0, min(1.0, self.prog))

        def value_text(f, v):
            if not fmt:
                return f"{round(f * 100)}%"
            if "{" not in fmt and not _PRINTF.search(fmt) and "%" in fmt:
                return format_number(f, fmt)          # spreadsheet percent pattern scales by 100 itself
            return format_number(f * 100 if v <= 1 else v, fmt)

        ring = 0.75 <= w / max(h, 1e-9) <= 1.33
        if ring:
            cx, cy = w / 2, h / 2
            R = min(w, h) / 2 - fs * 0.3
            n = len(items)
            thick = max(2.0, min(R * 0.18, R * 0.7 / max(1, n)))
            gap = thick * 0.35
            for k, (s, (f, v)) in enumerate(zip(items, fracs)):
                rr = R - thick / 2 - k * (thick + gap)
                if rr <= thick / 2:
                    break
                cr.set_line_width(thick)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.arc(cx, cy, rr, 0, 2 * math.pi)
                cr.set_source_rgba(*track)
                cr.stroke()
                ff = f * p
                if ff > 0:
                    cr.arc(cx, cy, rr, -math.pi / 2, -math.pi / 2 + 2 * math.pi * ff)
                    _stroke(rc, cr, s["color"], w, h, self.ctx, s["el"])
            if self.show_values:
                inner = R - n * (thick + gap)
                f, v = fracs[0]
                T.draw(value_text(f * p, v * p if v > 1 else v), cx, cy, "c", "m",
                       size=max(8.0, min(inner * 0.55, fs * 2.2)), weight=700)
                if names[0] and n == 1:
                    T.draw(names[0], cx, cy + min(inner * 0.35, fs * 1.4), "c", "t", size=fs * 0.8)
            return
        n = len(items)
        row_h = h / n
        for k, (s, (f, v)) in enumerate(zip(items, fracs)):
            name = names[k]
            cap_h = fs * 1.3 if (name or self.show_values) else 0.0
            bh = max(2.0, min(row_h - cap_h - fs * 0.4, max(8.0, fs * 1.4)))
            y = k * row_h + (row_h - bh - cap_h) / 2 + cap_h
            _rrect(cr, 0, y, w, bh, bh / 2)
            cr.set_source_rgba(*track)
            cr.fill()
            ff = f * p
            if ff > 0:
                _rrect(cr, 0, y, max(bh * 0.02, w * ff), bh, bh / 2)
                _fill(rc, cr, s["color"], w, h, self.ctx, s["el"])
            if name:
                T.draw(name, 0, y - fs * 0.25, "l", "b")
            if self.show_values:
                T.draw(value_text(ff, v * p if v > 1 else v), w, y - fs * 0.25, "r", "b", weight=700)


# ---------------------------------------------------------------- handler
def _apply_clip(rc, buf: Buf, M, clip) -> Buf:
    c = rc.canvas_for(M, clip[2], clip[3], 0)
    if c is None:
        return buf
    c.cr.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
    c.cr.set_source_rgba(1, 1, 1, 1)
    c.cr.fill()
    cov = c.to_buf(False)
    out = buf.crop_to(cov.rect)
    out.px = out.px * cov.region(out.rect)[..., 3:4]
    return out


@ASSETS.register("chart", level=FULL,
                 note="all kinds; nice-tick axes, gridlines, legend, value labels, axis titles from the data file, "
                      "text via the text engine (full textStyle); layout pinned in assets/chart.py")
def render_chart(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    ev = rc.ev
    kind = asset.get("kind")
    w, h = float(asset.get("width")), float(asset.get("height"))
    prog = max(0.0, min(1.0, ev.num(asset, "progress", ctx, 1.0)))
    series, labels, titles = _series(rc, asset, ctx, kind)
    if not series and kind not in ("progress", "counter"):
        return None
    if prog <= 0 and kind not in ("counter", "progress"):
        return None
    c = rc.canvas_for(M, w, h, 4)
    if c is None:
        return None
    cr = c.cr
    cr.set_antialias(cairo.ANTIALIAS_GOOD)
    T = _Text(rc, asset, ctx, h)
    ch = _Chart(rc, asset, ctx, cr, w, h, prog, T, ev.str(asset, "format", ctx, None),
                ev.bool(asset, "showAxes", ctx, True), ev.bool(asset, "showValues", ctx, False))
    if kind in ("bar", "column", "line", "area"):
        ch.cartesian(kind, series, labels, titles)
    elif kind == "scatter":
        ch.scatter(series, titles)
    elif kind in ("pie", "donut"):
        ch.pie(kind, series, labels)
    elif kind == "counter":
        ch.counter(series, labels)
    elif kind == "progress":
        ch.progress(series, labels)
    else:
        warn_once("chart", kind)
        return None
    buf = T.flush(c.to_buf(rc.linear), M)
    if clip and buf is not None:
        buf = _apply_clip(rc, buf, M, clip)
    return buf


@ASSET_SIZES.register("chart")
def chart_size(rc, asset, ctx):
    return float(asset.get("width")), float(asset.get("height"))
