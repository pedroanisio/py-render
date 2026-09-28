"""Asset handlers (layer content). Shared helpers: decoded-image cache as cairo surfaces."""
from __future__ import annotations

import logging
import math
import os

import cairo
import numpy as np

from ..document import ln
from ..raster import Buf
from ..registry import warn_once

log = logging.getLogger("scenerender")


def pil_to_surface(im) -> cairo.ImageSurface:
    """PIL image -> premultiplied BGRA cairo surface (sRGB-encoded)."""
    im = im.convert("RGBA")
    a = np.asarray(im, np.uint8)
    h, w = a.shape[:2]
    alpha = a[..., 3:4].astype(np.uint16)
    rgb = (a[..., :3].astype(np.uint16) * alpha + 127) // 255
    bgra = np.empty((h, w, 4), np.uint8)
    bgra[..., 0], bgra[..., 1], bgra[..., 2] = rgb[..., 2], rgb[..., 1], rgb[..., 0]
    bgra[..., 3] = a[..., 3]
    return array_to_surface(bgra)


def array_to_surface(bgra: np.ndarray) -> cairo.ImageSurface:
    h, w = bgra.shape[:2]
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
    buf = np.zeros((h, stride // 4, 4), np.uint8)
    buf[:, :w] = bgra
    return cairo.ImageSurface.create_for_data(memoryview(buf).cast("B"), cairo.FORMAT_ARGB32, w, h, stride)


def premul_float_to_surface(px: np.ndarray, linear: bool) -> cairo.ImageSurface:
    """Working-space premultiplied float tile -> cairo surface (for re-drawing rendered content)."""
    from ..raster import from_working_primaries, linear_to_srgb
    a = np.clip(px[..., 3:4], 0, 1)
    straight = np.where(a > 1e-6, px[..., :3] / np.maximum(a, 1e-6), 0)
    if linear:
        straight = linear_to_srgb(from_working_primaries(straight))
    rgb = np.clip(straight, 0, 1) * a
    bgra = np.empty(px.shape[:2] + (4,), np.uint8)
    bgra[..., 0] = (rgb[..., 2] * 255 + 0.5).astype(np.uint8)
    bgra[..., 1] = (rgb[..., 1] * 255 + 0.5).astype(np.uint8)
    bgra[..., 2] = (rgb[..., 0] * 255 + 0.5).astype(np.uint8)
    bgra[..., 3] = (a[..., 0] * 255 + 0.5).astype(np.uint8)
    return array_to_surface(bgra)


class Mips:
    """An image surface plus successively halved copies for alias-free downscaling."""

    def __init__(self, im):
        from PIL import Image
        self.levels = [pil_to_surface(im)]
        self.w, self.h = im.size
        cur = im.convert("RGBA")
        while min(cur.size) > 64 and len(self.levels) < 8:
            cur = cur.resize((max(1, cur.width // 2), max(1, cur.height // 2)), Image.BOX)
            self.levels.append(pil_to_surface(cur))

    def pick(self, px_scale: float) -> cairo.ImageSurface:
        """Level for drawing at px_scale output pixels per source pixel."""
        if px_scale <= 0:
            return self.levels[0]
        lvl = int(max(0, math.floor(math.log2(1 / px_scale)))) if px_scale < 1 else 0
        return self.levels[min(lvl, len(self.levels) - 1)]


def load_mips(rc, path: str) -> Mips | None:
    key = ("img", path)
    hit = rc.cache.get(key)
    if hit is None:
        if not os.path.exists(path):
            warn_once("asset-file", path, "file not found")
            rc.cache[key] = False
            return None
        from PIL import Image
        try:
            with Image.open(path) as im:
                im.load()
                hit = Mips(im)
        except Exception as e:  # noqa: BLE001 — any decode failure means a missing picture, not a crash
            warn_once("asset-file", path, f"cannot decode: {e}")
            rc.cache[key] = False
            return None
        _evict(rc)
        rc.cache[key] = hit
    return hit or None


def _evict(rc, limit: int = 256) -> None:
    keys = [k for k in rc.cache if isinstance(k, tuple) and k and k[0] == "img"]
    if len(keys) > limit:
        for k in keys[: len(keys) - limit]:
            del rc.cache[k]


def draw_surface(rc, mips: Mips, M, aw: float, ah: float, clip=None) -> Buf | None:
    """Draw an image (declared size aw x ah) under frame matrix M."""
    x0, y0, x1, y1 = clip if clip else (0, 0, aw, ah)
    c = rc.canvas_for(M, aw, ah, 2)
    if c is None:
        return None
    det = abs(np.linalg.det(M[:2, :2]))
    px_scale = math.sqrt(det) * aw / max(1, mips.w)
    surf = mips.pick(px_scale)
    cr = c.cr
    cr.rectangle(x0, y0, x1 - x0, y1 - y0)
    cr.clip()
    cr.scale(aw / surf.get_width(), ah / surf.get_height())
    cr.set_source_surface(surf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.get_source().set_extend(cairo.EXTEND_PAD)
    cr.paint()
    return c.to_buf(rc.linear)


def buffer_for_asset(rc, asset, ctx):
    """Render an asset at its declared size, independently of composition clipping."""
    from copy import copy
    from ..registry import ASSETS
    fn = ASSETS.get(ln(asset))
    if fn is None:
        warn_once("asset", ln(asset))
        return None
    w, h = rc.asset_size(asset, ctx)
    if w <= 0 or h <= 0:
        return None
    local = copy(rc)
    local._root = rc.base()
    local.width, local.height = max(1, math.ceil(w)), max(1, math.ceil(h))
    local.frame_rect = (0, 0, local.width, local.height)
    local.scale, local.root_matrix = 1., np.eye(3)
    local.frame_cache, local._flat_depth = {}, 0
    buf = fn(local, asset, np.eye(3), ctx, src_t=ctx.t)
    return None if buf is None else Buf(buf.region(local.frame_rect), 0, 0)


def surface_for_asset(rc, asset, ctx):
    """(cairo surface, width, height) for assets usable as pattern paint or texture sources."""
    kind = ln(asset)
    if kind in ("image", "imageSequence"):
        buf = buffer_for_asset(rc, asset, ctx)
        if buf is None:
            return None, 0, 0
        w, h = rc.asset_size(asset, ctx)
        return premul_float_to_surface(buf.px, rc.linear), w, h
    from ..registry import ASSETS
    fn = ASSETS.get(kind)
    if fn is None:
        warn_once("asset", kind)
        return None, 0, 0
    w, h = rc.asset_size(asset, ctx)
    buf = fn(rc, asset, np.eye(3), ctx, src_t=ctx.t)
    if buf is None:
        return None, 0, 0
    region = Buf(buf.region((0, 0, int(w), int(h))), 0, 0)
    return premul_float_to_surface(region.px, rc.linear), w, h


# ---------------------------------------------------------------- shared media helpers
def selected_representation(rc, asset, ctx=None):
    pref = rc.cache.get("representation")
    if pref:
        for r in asset:
            if getattr(r, "tag", None) != "representation":
                continue
            name = rc.ev.str(r, "name", ctx) if ctx is not None else r.get("name")
            if name == pref:
                return r
    return None


def representation_src(rc, asset, ctx=None) -> str | None:
    """@src of the representation named by the representation preference, else the asset's @src."""
    r = selected_representation(rc, asset, ctx)
    if r is not None:
        return rc.ev.str(r, "src", ctx) if ctx is not None else r.get("src")
    return rc.ev.str(asset, "src", ctx) if ctx is not None else asset.get("src")


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def cache_dir(sub: str) -> str:
    """Directory for derived media (extracted audio, ...): $SCENERENDER_CACHE or $XDG_CACHE_HOME/scenerender."""
    root = os.environ.get("SCENERENDER_CACHE") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "scenerender")
    d = os.path.join(root, sub)
    os.makedirs(d, exist_ok=True)
    return d


_SHA_CACHE: dict = {}


def file_sha256(path: str) -> str | None:
    """Hex SHA-256 of a file (memoised on path, size and mtime); None if it does not exist."""
    import hashlib
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    hit = _SHA_CACHE.get(key)
    if hit is None:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        hit = _SHA_CACHE[key] = h.hexdigest()
    return hit


def generated_cache_path(doc_or_rc, asset, ctx=None) -> str:
    """Return a verified generated cache, or fail as required by generatedAssetType.
    The renderer never calls providers: the cache is the only source of generated media."""
    doc = getattr(doc_or_rc, "doc", doc_or_rc)
    from ..document import SceneError
    def get(name):
        return doc_or_rc.ev.str(asset, name, ctx) if ctx is not None and hasattr(doc_or_rc, "ev") else asset.get(name)
    path = doc.resolve_path(get("cache"))
    digest = file_sha256(path)
    if digest is None:
        raise SceneError(f"generated {asset.get('id')!r}: cache file {path!r} not found")
    want = (get("cacheSha256") or "").lower()
    if digest != want:
        raise SceneError(f"generated {asset.get('id')!r}: cache {path!r} sha256 {digest} "
                         f"does not match cacheSha256 {want}")
    return path


def audio_source_path(doc_or_rc, asset) -> str | None:
    """Audio file behind an asset usable as a sound source: audio (@src / representation), generated
    speech/music/sound-effect (verified @cache), video with hasAudio (extracted stream, see video.video_audio)."""
    kind = ln(asset)
    doc = getattr(doc_or_rc, "doc", doc_or_rc)
    if kind == "audio":
        src = representation_src(doc_or_rc, asset) if hasattr(doc_or_rc, "cache") else asset.get("src")
        return doc.resolve_path(src)
    if kind == "generated":
        if asset.get("kind") == "video":
            from .video import video_audio
            return video_audio(doc_or_rc, asset)
        if asset.get("kind") in ("speech", "music", "sound-effect"):
            return generated_cache_path(doc, asset)
        return None
    if kind == "video":
        from .video import video_audio
        return video_audio(doc_or_rc, asset)
    return None


def decode_audio_mono(path: str, sample_rate: int | None = None) -> tuple[np.ndarray, int] | None:
    """(float32 mono samples in [-1, 1], sample rate). PCM WAV is read directly; anything else through ffmpeg."""
    import subprocess
    import wave
    if not os.path.exists(path):
        warn_once("asset-file", path, "file not found")
        return None
    if sample_rate is None:
        try:
            with wave.open(path, "rb") as f:
                n, ch, sw, sr = f.getnframes(), f.getnchannels(), f.getsampwidth(), f.getframerate()
                raw = f.readframes(n)
            if sw in (1, 2, 4):
                dt = {1: np.uint8, 2: "<i2", 4: "<i4"}[sw]
                x = np.frombuffer(raw, dt).astype(np.float32)
                x = (x - 128) / 128 if sw == 1 else x / float(2 ** (8 * sw - 1))
                return x.reshape(-1, ch).mean(axis=1), sr
        except (wave.Error, EOFError):
            pass
    cmd = [ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-i", path, "-vn", "-ac", "1"]
    if sample_rate:
        cmd += ["-ar", str(int(sample_rate))]
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        warn_once("asset-file", path, f"cannot decode audio: {e.stderr.decode(errors='replace').strip()[:200]}")
        return None
    sr = sample_rate or probe_media(path).get("sample_rate") or 48000
    return np.frombuffer(out, "<f4").astype(np.float32), int(sr)


_PROBE: dict = {}


def probe_media(path: str) -> dict:
    """Minimal ffmpeg probe: width, height, fps, sample_rate, has_audio, has_video (memoised)."""
    import re
    import subprocess
    if path in _PROBE:
        return _PROBE[path]
    info: dict = {}
    if os.path.exists(path):
        r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", path], capture_output=True)
        txt = r.stderr.decode(errors="replace")
        for line in txt.splitlines():
            if "Video:" in line and "has_video" not in info:
                info["has_video"] = True
                m = re.search(r", (\d{1,5})x(\d{1,5})\b", line)
                if m:
                    info["width"], info["height"] = int(m.group(1)), int(m.group(2))
                m = re.search(r"([\d.]+) fps", line)
                if m:
                    info["fps"] = float(m.group(1))
            elif "Audio:" in line and "has_audio" not in info:
                info["has_audio"] = True
                m = re.search(r"(\d+) Hz", line)
                if m:
                    info["sample_rate"] = int(m.group(1))
    _PROBE[path] = info
    return info
