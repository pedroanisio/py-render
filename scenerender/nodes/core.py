"""Core 2D node kinds: group, sequence, shape, layer, instance (adjustment and transition are
handled by the compositor itself)."""
from __future__ import annotations

import math
from dataclasses import replace

import cairo
import numpy as np

from .. import geometry, paint
from ..compositor import Out, RenderContext, scale, translate
from ..document import ln
from ..evaluator import Ctx
from ..raster import Buf
from ..registry import ASSETS, FEATURES, FULL, NODES, PARTIAL, SHAPE_MODIFIERS, warn_once
from ..values import parse_bool, parse_float

NODES.declare("adjustment", FULL)
NODES.declare("transition", FULL)
NODES.declare("include", FULL, "expanded at load time")
NODES.declare("repeat", FULL, "expanded at load time")
FEATURES.declare("strokePosition", FULL)
FEATURES.declare("trimPath", FULL)


# ================================================================ group / sequence
def is_isolated(rc: RenderContext, el, ctx: Ctx) -> bool:
    return (el.get("isolate") == "true" or bool(el.get("effects")) or el.get("blend", "normal") != "normal"
            or el.get("matte") is not None or any(ln(c) == "mask" for c in el)
            or el.get("clip") == "true" or el.get("threeD") == "true"
            or rc.ev.num(el, "opacity", ctx, 1.0) < 1.0)


def child_ctx(rc: RenderContext, el, ctx: Ctx) -> Ctx:
    ts = rc.ev.num(el, "timeScale", ctx, 1.0)
    off = rc.ev.num(el, "timeOffset", ctx, 0.0)
    if ts == 1.0 and off == 0.0:
        return ctx
    s = rc.doc.window(el)[0]
    return replace(ctx, t=s + (ctx.t - s - off) * ts)


def _repeat_vars(el, ctx: Ctx) -> Ctx:
    idx = el.get("{urn:scenerender}index")
    if idx is None:
        return ctx
    import json
    kw = {"index": float(idx), "count": float(el.get("{urn:scenerender}count")), "var": el.get("{urn:scenerender}var")}
    item = el.get("{urn:scenerender}item")
    if item is not None:
        kw["item"] = json.loads(item)
    return ctx.with_vars(**kw)


@NODES.register("group", "sequence", level=FULL)
def render_group(rc: RenderContext, el, ctx: Ctx, M, size) -> Buf | None:
    cctx = _repeat_vars(el, child_ctx(rc, el, ctx))
    dst = Buf.empty(0, 0, 1, 1)
    dst = rc.render_children(el, dst, cctx, M, size, 1.0)
    if el.get("clip") == "true":
        c = rc.canvas_for(M, size[0], size[1], 0)
        if c is None:
            return None
        c.cr.rectangle(0, 0, size[0], size[1])
        c.cr.set_source_rgba(1, 1, 1, 1)
        c.cr.fill()
        cov = c.to_buf(False)
        dst = dst.crop_to(cov.rect)
        dst.px *= cov.region(dst.rect)[..., 3:4]
    return dst


# ================================================================ shape
_SHAPE_ATTRS = ("radius", "cornerRadii", "points", "innerRadius", "outerRadius", "innerRoundness",
                "outerRoundness", "path")


def shape_paths(rc: RenderContext, el, ctx: Ctx, w: float, h: float) -> list[list]:
    a = rc.eval_attrs(el, ctx, _SHAPE_ATTRS)
    cmds = geometry.shape_commands(el.get("shape", "rect"), w, h, a)
    paths = [cmds]
    for m in el:
        if ln(m) != "shapeModifier":
            continue
        fn = SHAPE_MODIFIERS.get(m.get("type"))
        if fn is None:
            warn_once("shapeModifier", m.get("type"))
            continue
        multi = getattr(fn, "apply_all", None)
        paths = multi(rc, m, paths, ctx) if multi else [p for cmds in paths for p in fn(rc, m, cmds, ctx)]
    ev = rc.ev
    ts, te = ev.num(el, "trimStart", ctx, 0.0), ev.num(el, "trimEnd", ctx, 1.0)
    to = ev.num(el, "trimOffset", ctx, 0.0)
    if ts > 0 or te < 1 or to:
        mode = el.get("trimMode", "simultaneous")
        if mode == "sequential" and len(paths) > 1:
            joined = [c for p in paths for c in p]
            paths = [geometry.trim(joined, ts, te, to, mode)]
        else:
            paths = [geometry.trim(p, ts, te, to, mode) for p in paths]
    return paths


def stroke_setup(rc: RenderContext, cr: cairo.Context, el, ctx: Ctx, width: float) -> None:
    ev = rc.ev
    cr.set_line_width(width)
    cr.set_line_cap({"round": cairo.LINE_CAP_ROUND, "square": cairo.LINE_CAP_SQUARE}.get(el.get("strokeCap"), cairo.LINE_CAP_BUTT))
    cr.set_line_join({"round": cairo.LINE_JOIN_ROUND, "bevel": cairo.LINE_JOIN_BEVEL}.get(el.get("strokeJoin"), cairo.LINE_JOIN_MITER))
    cr.set_miter_limit(ev.num(el, "miterLimit", ctx, 4.0))
    dash = ev.get(el, "dash", ctx, None)
    if dash:
        d = [max(0.0, float(v)) for v in (dash if isinstance(dash, tuple) else str(dash).split())]
        if sum(d) > 0:
            cr.set_dash(d, ev.num(el, "dashOffset", ctx, 0.0))


def draw_paths(rc: RenderContext, cr: cairo.Context, el, ctx: Ctx, paths, w, h, fill_attr="fill",
               stroke_attr="stroke", default_rule="nonzero") -> None:
    if any(getattr(p, "opacity", 1.0) != 1.0 for p in paths):
        # Per-path opacity (repeater copies): draw each path as its own group.
        for p in paths:
            cr.push_group()
            draw_paths(rc, cr, el, ctx, [list(p)], w, h, fill_attr, stroke_attr, default_rule)
            cr.pop_group_to_source()
            cr.paint_with_alpha(getattr(p, "opacity", 1.0))
        cr.new_path()
        return
    ev = rc.ev
    fill = ev.str(el, fill_attr, ctx)
    stroke = ev.str(el, stroke_attr, ctx)
    sw = ev.num(el, "strokeWidth", ctx, 0.0)
    rule = cairo.FILL_RULE_EVEN_ODD if el.get("fillRule", default_rule) == "evenodd" else cairo.FILL_RULE_WINDING
    pos = el.get("strokePosition", "center")
    order = ("stroke", "fill") if el.get("paintOrder") == "stroke-fill" else ("fill", "stroke")

    def outline():
        cr.new_path()
        for p in paths:
            geometry.emit(cr, p)

    for step in order:
        if step == "fill" and fill:
            outline()
            cr.set_fill_rule(rule)
            if paint.set_source(rc, cr, fill, w, h, ctx, el):
                cr.fill()
        elif step == "stroke" and stroke and sw > 0:
            cr.save()
            outline()
            if pos == "inside":
                cr.set_fill_rule(rule)
                cr.clip_preserve()
                stroke_setup(rc, cr, el, ctx, sw * 2)
            elif pos == "outside":
                cr.push_group()
                stroke_setup(rc, cr, el, ctx, sw * 2)
            else:
                stroke_setup(rc, cr, el, ctx, sw)
            if paint.set_source(rc, cr, stroke, w, h, ctx, el):
                cr.stroke()
            if pos == "outside":
                outline()
                cr.set_fill_rule(rule)
                cr.set_operator(cairo.OPERATOR_CLEAR)
                cr.fill()
                cr.pop_group_to_source()
                cr.set_operator(cairo.OPERATOR_OVER)
                cr.paint()
            cr.restore()
    cr.new_path()


@NODES.register("shape", level=FULL)
def render_shape(rc: RenderContext, el, ctx: Ctx, M, size) -> Buf | None:
    w, h = size
    paths = shape_paths(rc, el, ctx, w, h)
    if not any(paths):
        return None
    x0, y0, x1, y1 = geometry.bounds([c for p in paths for c in p]) if any(paths) else (0, 0, w, h)
    sw = rc.ev.num(el, "strokeWidth", ctx, 0.0) * (2 if el.get("strokePosition") == "outside" else 1)
    pad = sw * max(2.0, rc.ev.num(el, "miterLimit", ctx, 4.0) / 2) + 2
    from ..raster import Canvas, intersect, transformed_rect
    r = transformed_rect(M, min(0, x0) - pad, min(0, y0) - pad, max(w, x1) + pad, max(h, y1) + pad, 1)
    r = intersect(r, (-64, -64, rc.width + 64, rc.height + 64))
    if r is None:
        return None
    c = Canvas(r)
    c.set_matrix(M)
    c.cr.set_antialias(cairo.ANTIALIAS_GOOD)
    draw_paths(rc, c.cr, el, ctx, paths, w, h)
    return c.to_buf(rc.linear)


# ================================================================ layer
def media_time(rc: RenderContext, el, ctx: Ctx, source_duration: float | None) -> float:
    """Layer-local time -> source time: timeRemap, else clipIn + speed/timeStretch, freezeAt, loop, reverse."""
    ev = rc.ev
    lt = ctx.t - rc.doc.window(el)[0]
    tr = next((c for c in el if ln(c) == "timeRemap"), None)
    if tr is not None:
        keys = rc.ev.keys(el, tr, "timeRemap")
        from .. import anim
        v = anim.sample(keys, lt, tr.get("defaultInterpolation", "linear"))
        return float(v) if isinstance(v, float) else lt
    if el.get("freezeAt") is not None:
        lt = ev.num(el, "freezeAt", ctx, 0.0)
    speed = ev.num(el, "speed", ctx, 1.0) / max(1e-9, ev.num(el, "timeStretch", ctx, 1.0))
    clip_in = ev.num(el, "clipIn", ctx, 0.0)
    clip_out = ev.num(el, "clipOut", ctx, source_duration if source_duration else 0.0) if (el.get("clipOut") or source_duration) else None
    span = (clip_out - clip_in) if clip_out is not None else None
    u = lt * speed
    loops = int(ev.num(el, "loop", ctx, 0.0))
    if span and span > 0:
        if loops != 0 and (loops < 0 or u < span * (loops + 1)):
            u = u % span
        else:
            u = min(u, span)
        if el.get("reverse") == "true":
            u = span - u
    return clip_in + u


def fit_matrix(rc: RenderContext, el, ctx: Ctx, aw: float, ah: float, box: tuple[float, float] | None, mode: str):
    if box is None or mode == "none" or aw <= 0 or ah <= 0:
        return np.eye(3)
    bw, bh = box
    fx, fy = rc.ev.num(el, "focusX", ctx, 0.5), rc.ev.num(el, "focusY", ctx, 0.5)
    if mode == "fill":
        return scale(bw / aw, bh / ah)
    k = {"contain": min(bw / aw, bh / ah), "contain-blur": min(bw / aw, bh / ah), "cover": max(bw / aw, bh / ah),
         "scale-down": min(1.0, bw / aw, bh / ah)}.get(mode, 1.0)
    return translate((bw - aw * k) * fx, (bh - ah * k) * fy) @ scale(k, k)


@NODES.register("layer", level=FULL)
def render_layer(rc: RenderContext, el, ctx: Ctx, M, size) -> Buf | None:
    asset = rc.layer_asset(el, ctx)
    if asset is None:
        warn_once("layer", el.get("id"), "asset not found")
        return None
    kind = ln(asset)
    fn = ASSETS.get(kind)
    if fn is None:
        warn_once("asset", kind)
        return None
    aw, ah = rc.asset_size(asset, ctx)
    ev = rc.ev
    has_box = bool(el.get("boxWidth") or el.get("width"))
    mode = el.get("fit", "none")
    box = size if has_box or mode != "none" else None
    if box is None and mode != "none":
        box = (rc.doc.width, rc.doc.height)
    F = fit_matrix(rc, el, ctx, aw, ah, box, mode)
    if el.get("flipX") == "true" or el.get("flipY") == "true":
        F = F @ translate(aw if el.get("flipX") == "true" else 0, ah if el.get("flipY") == "true" else 0) @ \
            scale(-1 if el.get("flipX") == "true" else 1, -1 if el.get("flipY") == "true" else 1)
    cl, ct = ev.num(el, "cropLeft", ctx, 0.0), ev.num(el, "cropTop", ctx, 0.0)
    cr_, cb = ev.num(el, "cropRight", ctx, 0.0), ev.num(el, "cropBottom", ctx, 0.0)
    clip = (cl * aw, ct * ah, aw * (1 - cr_), ah * (1 - cb)) if (cl or ct or cr_ or cb) else None
    if mode in ("cover", "fill") or (mode == "contain-blur"):
        box_clip = box
    else:
        box_clip = None
    src_t = media_time(rc, el, ctx, float(asset.get("duration")) if asset.get("duration") else None)
    buf = None
    if mode == "contain-blur":
        Fc = fit_matrix(rc, el, ctx, aw, ah, box, "cover")
        back = fn(rc, asset, M @ Fc, ctx, layer=el, src_t=src_t, clip=None)
        if back is not None:
            from ..effects import gaussian
            buf = Buf(gaussian(back.px, 24 * rc.scale), back.x0, back.y0)
    fg = fn(rc, asset, M @ F, ctx, layer=el, src_t=src_t, clip=clip)
    if fg is not None:
        if buf is not None:
            from ..blend import composite
            buf = composite(buf, fg)
        else:
            buf = fg
    if buf is not None and box_clip is not None:
        c = rc.canvas_for(M, box_clip[0], box_clip[1], 0)
        if c is None:
            return None
        c.cr.rectangle(0, 0, *box_clip)
        c.cr.set_source_rgba(1, 1, 1, 1)
        c.cr.fill()
        cov = c.to_buf(False)
        buf = buf.crop_to(cov.rect)
        buf.px *= cov.region(buf.rect)[..., 3:4]
    return buf


# ================================================================ instance
def instance_time(rc: RenderContext, el, ctx: Ctx, sym) -> float | None:
    ev = rc.ev
    s = rc.doc.window(el)[0]
    tr = next((c for c in el if ln(c) == "timeRemap"), None)
    dur = parse_float(sym.get("duration"), 0.0) if sym.get("duration") else None
    if tr is not None:
        from .. import anim
        v = anim.sample(ev.keys(el, tr, "timeRemap"), ctx.t - s, tr.get("defaultInterpolation", "linear"))
        return float(v)
    lt = (ctx.t - s) * ev.num(el, "speed", ctx, 1.0) + ev.num(el, "clipIn", ctx, 0.0)
    clip_out = ev.num(el, "clipOut", ctx, dur or 0.0) if (el.get("clipOut") or dur) else None
    loops = int(ev.num(el, "loop", ctx, 0.0))
    if clip_out:
        clip_in = ev.num(el, "clipIn", ctx, 0.0)
        span = clip_out - clip_in
        if span > 0:
            u = lt - clip_in
            if loops != 0 and (loops < 0 or u < span * (loops + 1)):
                u %= span
            elif u > span + 1e-9 and el.get("end") is None:
                return None
            else:
                u = min(u, span)
            if el.get("reverse") == "true":
                u = span - u
            lt = clip_in + u
    return lt


@NODES.register("instance", level=FULL)
def render_instance(rc: RenderContext, el, ctx: Ctx, M, size) -> Buf | None:
    sym = rc.doc.ids.get(el.get("symbol"))
    if sym is None:
        warn_once("instance", el.get("id"), "symbol not found")
        return None
    lt = instance_time(rc, el, ctx, sym)
    if lt is None:
        return None
    overrides = {(o.get("target"), o.get("property")): o.get("value") for o in el if ln(o) == "override"}
    sctx = replace(ctx, t=lt, scope=ctx.scope.push(el.get("id", ""), overrides) if overrides else ctx.scope)
    sw = float(sym.get("width")) if sym.get("width") else float(rc.doc.width)
    sh = float(sym.get("height")) if sym.get("height") else float(rc.doc.height)
    mode = el.get("fit", "none")
    SM = M
    if el.get("boxWidth") and mode != "none":
        SM = M @ fit_matrix(rc, el, ctx, sw, sh, size, mode)
    dst = Buf.empty(0, 0, 1, 1)
    bg = sym.get("background")
    if bg and paint.paint_ref(bg) is None and paint.parse_color(bg, rc.doc.tokens)[3] > 0 or (bg and paint.paint_ref(bg)):
        c = rc.canvas_for(SM, sw, sh, 1)
        if c is not None:
            c.cr.rectangle(0, 0, sw, sh)
            if paint.set_source(rc, c.cr, bg, sw, sh, sctx):
                c.cr.fill()
            dst = c.to_buf(rc.linear)
    return rc.render_children(sym, dst, sctx, SM, (sw, sh), 1.0)
