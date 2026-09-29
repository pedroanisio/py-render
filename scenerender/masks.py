"""Masks (node-local geometry) and track mattes."""
from __future__ import annotations

import cairo
import numpy as np

from . import geometry
from .document import ln
from .raster import Buf, Canvas


def _mask_attrs(rc, m, ctx, size):
    ev = rc.ev
    w = ev.length(m, "width", ctx, size[0], size[0])
    h = ev.length(m, "height", ctx, size[1], size[1])
    a = rc.eval_attrs(m, ctx, ("radius", "points", "innerRadius", "path"))
    kind = ev.str(m, "type", ctx, "rect")
    if kind == "rect" and a.get("radius"):
        kind = "rounded-rect"
    if kind == "star":
        # A mask's innerRadius is a fraction of the outer ellipse, 0.5 when absent (D16; shapes use
        # units, D27): the shared star geometry takes it in the units of the outer radius, min(w, h) / 2.
        frac = a.get("innerRadius")
        a = {**a, "innerRadius": (0.5 if frac is None else float(frac)) * min(w, h) / 2}
    return kind, w, h, a


def mask_coverage(rc, masks, rect, ctx, M, size) -> np.ndarray:
    """Combined coverage (h, w) float32 of a node's masks over the frame rectangle rect."""
    ev = rc.ev
    h, w = rect[3] - rect[1], rect[2] - rect[0]
    total = None
    for m in masks:
        mode = ev.str(m, "mode", ctx, "intersect")
        if mode == "none":
            continue
        kind, mw, mh, a = _mask_attrs(rc, m, ctx, size)
        cmds = geometry.shape_commands(kind, mw, mh, a)
        mx, my = ev.length(m, "x", ctx, size[0]), ev.length(m, "y", ctx, size[1])
        # D16 / CONVENTIONS 5.15: feather is a Gaussian of standard deviation `feather`. The shape is
        # drawn 3 sigma beyond rect so the blur sees what lies just outside it.
        feather = ev.num(m, "feather", ctx, 0.0) * rc.scale
        pad = int(np.ceil(3 * feather)) if feather > 0 else 0
        c = Canvas((rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad))
        c.set_matrix(M)
        cr = c.cr
        cr.translate(mx, my)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD if ev.str(m, "fillRule", ctx) == "evenodd" else cairo.FILL_RULE_WINDING)
        cr.set_source_rgba(1, 1, 1, 1)
        geometry.emit(cr, cmds)
        exp = ev.num(m, "expansion", ctx, 0.0)
        if exp > 0:
            cr.fill_preserve()
            cr.set_line_width(2 * exp)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.stroke()
        elif exp < 0:
            cr.fill_preserve()
            cr.set_line_width(-2 * exp)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_operator(cairo.OPERATOR_CLEAR)
            cr.stroke()
        else:
            cr.fill()
        cov = c.to_buf(False).px[..., 3]
        if feather > 0:
            from .effects import gaussian
            cov = gaussian(cov[..., None], feather)[..., 0]
        if pad:
            cov = cov[pad:pad + h, pad:pad + w]
        if ev.bool(m, "invert", ctx):
            cov = 1 - cov
        cov = cov * ev.num(m, "opacity", ctx, 1.0)
        if total is None:
            total = cov if mode in ("intersect", "add", "lighten") else (1 - cov if mode == "subtract" else cov)
            continue
        if mode == "intersect":
            total = total * cov
        elif mode == "add":
            total = total + cov - total * cov
        elif mode == "subtract":
            total = total * (1 - cov)
        elif mode == "lighten":
            total = np.maximum(total, cov)
        elif mode == "darken":
            total = np.minimum(total, cov)
        elif mode == "difference":
            total = np.abs(total - cov)
    if total is None:
        return np.ones((h, w), np.float32)
    return np.clip(total, 0, 1).astype(np.float32)


def apply_masks(rc, masks, buf: Buf, ctx, M, size) -> Buf:
    cov = mask_coverage(rc, masks, buf.rect, ctx, M, size)
    buf.px *= cov[..., None]
    return buf


def _luma(px):
    return 0.2126 * px[..., 0] + 0.7152 * px[..., 1] + 0.0722 * px[..., 2]


def matte_coverage(rc, el, rect, ctx) -> np.ndarray:
    node, target_ctx = rc.ev.reference(rc.ev.str(el, "matte", ctx, ""), el, ctx)
    h, w = rect[3] - rect[1], rect[2] - rect[0]
    mode = rc.ev.str(el, "matteMode", ctx, "alpha")
    inverted = mode.endswith("inverted")
    if node is None:
        return np.ones((h, w), np.float32)
    loc = rc.node_location(node, target_ctx)
    out = loc.rc.render_node(node, loc.ctx, loc.matrix, loc.box, loc.layout, force=False)
    if out is None:
        cov = np.zeros((h, w), np.float32)
    else:
        region = out.buf.region(rect) * out.opacity
        cov = region[..., 3] if mode.startswith("alpha") else _luma(region)
    cov = np.clip(cov, 0, 1)
    return (1 - cov) if inverted else cov


def apply_matte(rc, el, buf: Buf, ctx) -> Buf:
    buf.px *= matte_coverage(rc, el, buf.rect, ctx)[..., None]
    return buf
