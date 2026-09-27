"""generated assets: provider-generated media read only from the verified @cache.

The renderer never calls @provider. @cache is used only when its SHA-256 equals @cacheSha256; otherwise
rendering fails with SceneError, including when the cache is missing.
kind image / video are drawn by the image / video handlers from the cache file (video uses @fps [30] and
@duration; without @duration frames past the end hold the last one). speech / music / sound-effect are
audio only: they have no picture (size 0 x 0); the audio mixer gets the verified file from
scenerender.assets.audio_source_path(rc, asset) (or generated_cache_path).
@width/@height set the drawn size (else the cached file's own size); a layer's frameBlend="frame-mix" blends
video frames as for video assets. Generated media carries no colour tags: it is treated as sRGB.
@provider, @model, @prompt, @voice, @language, @seed and @license describe how the separate resolve step
filled the cache (and its licence); they are metadata and never read while rendering, which is what keeps
renders deterministic.
"""
from __future__ import annotations

from fractions import Fraction

from ..registry import ASSET_SIZES, ASSETS, FULL
from ..values import parse_fps
from . import generated_cache_path, probe_media
from .image_pixels import draw_pixels, load_pixels


def _dims(rc, asset, path, ctx):
    if rc.ev.get(asset, "width", ctx) and rc.ev.get(asset, "height", ctx):
        return rc.ev.num(asset, "width", ctx), rc.ev.num(asset, "height", ctx)
    if path is None:
        return 0.0, 0.0
    if rc.ev.str(asset, "kind", ctx) == "image":
        m = load_pixels(rc, asset, ctx, path)
        return ((rc.ev.num(asset, "width", ctx, m.w), rc.ev.num(asset, "height", ctx, m.h))
                if m else (0.0, 0.0))
    info = probe_media(path)
    return float(info.get("width", 0)), float(info.get("height", 0))


def _cached(rc, asset, ctx=None):
    # The hash helper memoises by file metadata; do not cache a successful check
    # for the lifetime of the renderer when the file may change between frames.
    return generated_cache_path(rc, asset, ctx)


@ASSETS.register("generated", level=FULL,
                 note="image/video drawn from the sha256-verified @cache; speech/music/sound-effect are audio only")
def render_generated(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    kind = rc.ev.str(asset, "kind", ctx)
    if kind not in ("image", "video"):
        return None
    path = _cached(rc, asset, ctx)
    if path is None:
        return None
    aw, ah = _dims(rc, asset, path, ctx)
    if kind == "image":
        m = load_pixels(rc, asset, ctx, path)
        return draw_pixels(rc, m, M, aw, ah, clip) if m else None
    from .video import layer_stabilize, render_video_file
    fps = parse_fps(asset.get("fps")) if asset.get("fps") else Fraction(30)
    blend = rc.ev.str(layer, "frameBlend", ctx, "none") if layer is not None else "none"
    return render_video_file(rc, path, M, aw, ah, fps=fps,
                             duration=float(asset.get("duration")) if asset.get("duration") else None,
                             src_t=src_t, clip=clip, frame_blend=blend or "none",
                             stabilize=layer_stabilize(rc, layer, ctx))


@ASSET_SIZES.register("generated")
def generated_size(rc, asset, ctx):
    from ..evaluator import Ctx
    ctx = ctx or Ctx(0, 0)
    if rc.ev.str(asset, "kind", ctx) not in ("image", "video"):
        return 0.0, 0.0
    return _dims(rc, asset, _cached(rc, asset, ctx), ctx)
