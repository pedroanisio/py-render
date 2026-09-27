"""Group auto-layout: a flexbox subset (row, column, stack, grid).

The layout places each child's box; the child's own x/y then act as offsets
from that place (relative positioning). Children are taken in document order.
"""
from __future__ import annotations

import math

from .document import ln


def flex_layout(rc, group, ctx, box, mode: str) -> dict:
    ev = rc.ev
    kids = [c for c in rc.doc.nodes(group) if ln(c) not in ("transition", "adjustment")]
    if not kids:
        return {}
    gw, gh = box
    gap = ev.length(group, "gap", ctx, gw if mode != "column" else gh)
    pad = ev.length(group, "padding", ctx, min(gw, gh))
    justify = group.get("justify", "start")
    align = group.get("alignItems", "start")
    sizes = []
    for k in kids:
        w, h = rc.node_size(k, ctx, box)
        sx, sy = abs(ev.num(k, "scaleX", ctx, 1.0)), abs(ev.num(k, "scaleY", ctx, 1.0))
        sizes.append((w * sx, h * sy))
    inner_w, inner_h = max(0.0, gw - 2 * pad), max(0.0, gh - 2 * pad)
    out = {}
    if mode == "stack":
        for k, (w, h) in zip(kids, sizes):
            out[k] = (pad + _cross(align, inner_w, w), pad + _cross(align, inner_h, h))
        return out
    if mode == "grid":
        cols = max(1, int(group.get("gridColumns", 2)))
        rows = math.ceil(len(kids) / cols)
        cw = (inner_w - gap * (cols - 1)) / cols if inner_w else max(w for w, _ in sizes)
        rh = (inner_h - gap * (rows - 1)) / rows if inner_h else max(h for _, h in sizes)
        for i, (k, (w, h)) in enumerate(zip(kids, sizes)):
            r, c = divmod(i, cols)
            out[k] = (pad + c * (cw + gap) + _cross(align, cw, w), pad + r * (rh + gap) + _cross(align, rh, h))
        return out
    horizontal = mode == "row"
    main = [w if horizontal else h for w, h in sizes]
    cross = [h if horizontal else w for w, h in sizes]
    avail = inner_w if horizontal else inner_h
    cross_avail = inner_h if horizontal else inner_w
    n = len(kids)
    free = avail - sum(main) - gap * (n - 1)
    lead, between = 0.0, gap
    if avail > 0:
        if justify == "center":
            lead = free / 2
        elif justify == "end":
            lead = free
        elif justify == "space-between" and n > 1:
            between = gap + free / (n - 1)
        elif justify == "space-around":
            lead, between = free / n / 2, gap + free / n
        elif justify == "space-evenly":
            lead, between = free / (n + 1), gap + free / (n + 1)
    pos = pad + lead
    baselines = [_baseline(rc, k, ctx, box, c) for k, c in zip(kids, cross)] if align == "baseline" and horizontal else None
    top = max(baselines) if baselines else 0.0
    for i, (k, m, c) in enumerate(zip(kids, main, cross)):
        if baselines:
            cpos = pad + top - baselines[i]
        else:
            cpos = pad + (_cross(align, cross_avail, c) if cross_avail > 0 else 0.0)
        out[k] = (pos, cpos) if horizontal else (cpos, pos)
        pos += m + between
    return out


def _baseline(rc, node, ctx, box, height: float) -> float:
    """Distance from the top of a child's box to its first baseline: a text layer's first line,
    else the bottom edge (flexbox convention for boxes without text)."""
    if ln(node) == "layer":
        asset = rc.layer_asset(node, ctx)
        if asset is not None and ln(asset) == "text":
            from .assets import text as text_asset
            fn = getattr(text_asset, "first_baseline", None)
            if fn is not None:
                return fn(rc, asset, ctx) * abs(rc.ev.num(node, "scaleY", ctx, 1.0))
    return height


def _cross(align: str, avail: float, size: float) -> float:
    if align == "center":
        return (avail - size) / 2
    if align == "end":
        return avail - size
    return 0.0
