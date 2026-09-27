"""image and imageSequence assets."""
from __future__ import annotations

import math
import os
import re

from ..registry import ASSETS, ASSET_SIZES, FULL, warn_once
from ..values import parse_fps
from . import draw_surface, load_mips

ASSETS.declare("font", FULL, "registered with Pango when the document loads")

_PRINTF = re.compile(r"%0?(\d*)d")
_HASHES = re.compile(r"#+")


def _representation(rc, asset, ctx) -> str:
    pref = rc.cache.get("representation")
    if pref:
        for r in asset:
            if getattr(r, "tag", None) == "representation" and r.get("name") == pref:
                return rc.ev.str(r, "src", ctx)
    return rc.ev.str(asset, "src", ctx)


@ASSETS.register("image", level=FULL)
def render_image(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    m = load_mips(rc, rc.doc.resolve_path(_representation(rc, asset, ctx)))
    if m is None:
        return None
    aw, ah = _size(rc, asset, ctx, m)
    return draw_surface(rc, m, M, aw, ah, clip)


def _size(rc, asset, ctx, m):
    return rc.ev.num(asset, "width", ctx, m.w), rc.ev.num(asset, "height", ctx, m.h)


@ASSET_SIZES.register("image")
def image_size(rc, asset, ctx):
    if rc.ev.get(asset, "width", ctx) is not None and rc.ev.get(asset, "height", ctx) is not None:
        return rc.ev.num(asset, "width", ctx), rc.ev.num(asset, "height", ctx)
    m = load_mips(rc, rc.doc.resolve_path(_representation(rc, asset, ctx)))
    return _size(rc, asset, ctx, m) if m else (0.0, 0.0)


def frame_path(src: str, n: int) -> str:
    if _PRINTF.search(src):
        return _PRINTF.sub(lambda mm: f"{n:0{int(mm.group(1) or 1)}d}", src, count=1)
    return _HASHES.sub(lambda mm: f"{n:0{len(mm.group(0))}d}", src, count=1)


def sequence_frame_path(rc, asset, src_t: float, ctx=None) -> str | None:
    from ..evaluator import Ctx
    ctx = ctx or Ctx(src_t, src_t)
    ev = rc.ev
    fps = float(parse_fps(ev.str(asset, "fps", ctx)))
    first, last, step = int(ev.num(asset, "first", ctx)), int(ev.num(asset, "last", ctx)), max(1, int(ev.num(asset, "step", ctx, 1)))
    count = (last - first) // step + 1
    idx = int(math.floor(src_t * fps + 1e-6))
    idx = min(max(idx, 0), count - 1)
    n = first + idx * step
    src = ev.str(asset, "src", ctx)
    path = rc.doc.resolve_path(frame_path(src, n))
    if os.path.exists(path):
        return path
    mode = ev.str(asset, "missingFrame", ctx, "error")
    if mode == "hold":
        for j in range(idx - 1, -1, -1):
            p = rc.doc.resolve_path(frame_path(src, first + j * step))
            if os.path.exists(p):
                return p
    if mode == "error":
        warn_once("asset-file", path, "image sequence frame missing (missingFrame=error); drawing nothing")
    return None


@ASSETS.register("imageSequence", level=FULL)
def render_sequence(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    path = sequence_frame_path(rc, asset, src_t, ctx)
    aw, ah = rc.ev.num(asset, "width", ctx), rc.ev.num(asset, "height", ctx)
    if path is None:
        if rc.ev.str(asset, "missingFrame", ctx) == "black":
            c = rc.canvas_for(M, aw, ah, 1)
            if c is None:
                return None
            c.cr.rectangle(0, 0, aw, ah)
            c.cr.set_source_rgba(0, 0, 0, 1)
            c.cr.fill()
            return c.to_buf(rc.linear)
        return None
    m = load_mips(rc, path)
    buf = draw_surface(rc, m, M, aw, ah, clip) if m else None
    blend = rc.ev.str(layer, "frameBlend", ctx, "none") if layer is not None else "none"
    if buf is None or blend not in ("frame-mix", "optical-flow"):
        return buf
    # Frame blending between this frame and the next, by the fractional position in the sequence.
    fps = float(parse_fps(rc.ev.str(asset, "fps", ctx)))
    pos = src_t * fps
    u = pos - math.floor(pos + 1e-6)
    nxt = sequence_frame_path(rc, asset, (math.floor(pos + 1e-6) + 1) / fps + 1e-9, ctx)
    if u < 1e-6 or nxt is None or nxt == path:
        return buf
    m1 = load_mips(rc, nxt)
    other = draw_surface(rc, m1, M, aw, ah, clip) if m1 else None
    if other is None or other.rect != buf.rect:
        return buf
    from .video import flow_frames, mix_frames
    px = flow_frames(buf.px, other.px, u) if blend == "optical-flow" else mix_frames(buf.px, other.px, u)
    return type(buf)(px, buf.x0, buf.y0)
