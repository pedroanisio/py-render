"""image and imageSequence assets."""
from __future__ import annotations

import math
import os
import re

from ..registry import ASSETS, ASSET_SIZES, FULL, warn_once
from ..values import parse_fps
from .image_pixels import draw_pixels, load_pixels
from .video import provenance_src

ASSETS.declare("font", FULL, "registered with Pango when the document loads")

_PRINTF = re.compile(r"%0?(\d*)d")
_HASHES = re.compile(r"#+")


@ASSETS.register("image", level=FULL,
                 note="float input color/alpha conversion; PNG/TIFF precision; PSD layers via psd-tools; "
                      "flat EXR parts/channels require OpenEXR; backend limits in docs/IMAGE-INPUTS.md")
def render_image(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    m = load_pixels(rc, asset, ctx, provenance_src(rc, asset, ctx))
    if m is None:
        return None
    aw, ah = _size(rc, asset, ctx, m)
    return draw_pixels(rc, m, M, aw, ah, clip)


def _size(rc, asset, ctx, m):
    return rc.ev.num(asset, "width", ctx, m.w), rc.ev.num(asset, "height", ctx, m.h)


@ASSET_SIZES.register("image")
def image_size(rc, asset, ctx):
    if rc.ev.get(asset, "width", ctx) is not None and rc.ev.get(asset, "height", ctx) is not None:
        return rc.ev.num(asset, "width", ctx), rc.ev.num(asset, "height", ctx)
    m = load_pixels(rc, asset, ctx, provenance_src(rc, asset, ctx))
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
    if count <= 0:
        warn_once("imageSequence", asset.get("id"), "last precedes first; drawing nothing")
        return None
    idx = int(math.floor(src_t * fps + 1e-6))
    idx = min(max(idx, 0), count - 1)
    n = first + idx * step
    src = ev.str(asset, "proxy", ctx) if rc.cache.get("representation") == "proxy" else None
    src = src or ev.str(asset, "src", ctx)
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


def _sequence_picture(rc, asset, ctx, path, M, aw, ah, clip):
    if path is None:
        c = rc.canvas_for(M, aw, ah, 2)
        if c is None:
            return None
        if rc.ev.str(asset, "missingFrame", ctx) == "black":
            x0, y0, x1, y1 = clip if clip is not None else (0, 0, aw, ah)
            c.cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            c.cr.set_source_rgba(0, 0, 0, 1)
            c.cr.fill()
        return c.to_buf(rc.linear)
    m = load_pixels(rc, asset, ctx, path)
    return draw_pixels(rc, m, M, aw, ah, clip) if m else None


@ASSETS.register("imageSequence", level=FULL)
def render_sequence(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    path = sequence_frame_path(rc, asset, src_t, ctx)
    aw, ah = rc.ev.num(asset, "width", ctx), rc.ev.num(asset, "height", ctx)
    buf = _sequence_picture(rc, asset, ctx, path, M, aw, ah, clip)
    blend = rc.ev.str(layer, "frameBlend", ctx, "none") if layer is not None else "none"
    if buf is None or blend not in ("frame-mix", "optical-flow"):
        return buf
    # Frame blending between this frame and the next, by the fractional position in the sequence.
    fps = float(parse_fps(rc.ev.str(asset, "fps", ctx)))
    pos = src_t * fps
    u = max(0., pos - math.floor(pos + 1e-6))
    nxt = sequence_frame_path(rc, asset, (math.floor(pos + 1e-6) + 1) / fps + 1e-9, ctx)
    if u < 1e-6 or nxt == path:
        return buf
    other = _sequence_picture(rc, asset, ctx, nxt, M, aw, ah, clip)
    if other is None or other.rect != buf.rect:
        return buf
    from .video import flow_frames, mix_frames
    px = flow_frames(buf.px, other.px, u) if blend == "optical-flow" else mix_frames(buf.px, other.px, u)
    return type(buf)(px, buf.x0, buf.y0)
