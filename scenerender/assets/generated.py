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
from . import draw_surface, generated_cache_path, load_mips, probe_media


def _dims(rc, asset, path):
    if asset.get("width") and asset.get("height"):
        return float(asset.get("width")), float(asset.get("height"))
    if path is None:
        return 0.0, 0.0
    if asset.get("kind") == "image":
        m = load_mips(rc, path)
        return (float(m.w), float(m.h)) if m else (0.0, 0.0)
    info = probe_media(path)
    return float(info.get("width", 0)), float(info.get("height", 0))


def _cached(rc, asset, ctx=None):
    # The hash helper memoises by file metadata; do not cache a successful check
    # for the lifetime of the renderer when the file may change between frames.
    return generated_cache_path(rc, asset, ctx)


@ASSETS.register("generated", level=FULL,
                 note="image/video drawn from the sha256-verified @cache; speech/music/sound-effect are audio only")
def render_generated(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    kind = asset.get("kind")
    if kind not in ("image", "video"):
        return None
    path = _cached(rc, asset, ctx)
    if path is None:
        return None
    aw, ah = _dims(rc, asset, path)
    if kind == "image":
        m = load_mips(rc, path)
        return draw_surface(rc, m, M, aw, ah, clip) if m else None
    from .video import layer_stabilize, render_video_file
    fps = parse_fps(asset.get("fps")) if asset.get("fps") else Fraction(30)
    blend = rc.ev.str(layer, "frameBlend", ctx, "none") if layer is not None else "none"
    return render_video_file(rc, path, M, aw, ah, fps=fps,
                             duration=float(asset.get("duration")) if asset.get("duration") else None,
                             src_t=src_t, clip=clip, frame_blend=blend or "none",
                             stabilize=layer_stabilize(rc, layer, ctx))


@ASSET_SIZES.register("generated")
def generated_size(rc, asset, ctx):
    if asset.get("kind") not in ("image", "video"):
        return 0.0, 0.0
    return _dims(rc, asset, _cached(rc, asset, ctx))
