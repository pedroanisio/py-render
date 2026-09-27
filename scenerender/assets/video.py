"""video assets: frames decoded with an ffmpeg subprocess (rawvideo pipe), colour-managed into the working space.

Frame selection: the layer maps its clock to source seconds (clipIn / speed / loop / timeRemap);
frame index = floor(src_t * fps), clamped to [0, floor(duration * fps) - 1]. The decoder resamples the
stream to the asset's declared @fps (ffmpeg fps filter), so frame n is the picture shown at n / fps.
Frame mix: when the layer asks for frameBlend="frame-mix", the two frames around p = src_t * fps are
blended in the (linear) working space with weights (1 - frac(p), frac(p)) (After Effects "Frame Mix");
`mix_frames(a, b, u)` and `mix_indices(src_t, fps, duration)` expose the same rule to other callers.
frameBlend="optical-flow" warps both frames along dense optical flow to the in-between time (flow_frames).

Geometry (pinned here, the schema leaves it open): @width/@height are the stored (coded) frame size;
the displayed asset size is (width * pixelAspect, height), swapped for rotation 90/270. @rotation is the
clockwise rotation needed for display (ffmpeg auto-rotation is disabled so the attribute is authoritative).

Colour (asset or chosen <representation> colorSpace / transfer; transfer "auto" = the space's own
encoding, e.g. srgb -> sRGB curve, rec709 / rec2020 -> BT.1886, acescct -> ACEScct, linear-srgb -> linear):
  * YUV -> RGB uses the stream's tagged matrix and range; untagged streams use BT.2020 NCL when
    @colorSpace is rec2020, else BT.709 for HD (width >= 1280 or height > 576) and BT.601 for SD
    (the mpv / browser convention), limited range.
  * Sources deeper than 8 bits (10/12/16-bit YUV or RGB) are decoded as rgba64le (16 bits per channel),
    so HDR and log footage keeps its precision through the transfer decode.
  * Code values -> linear light with the transfer's inverse (scenerender.color.decode; Canon Log 3,
    RED Log3G10, F-Log2 and N-Log are implemented here from the vendors' published formulas), then the
    primaries are converted (Bradford-adapted through XYZ, scenerender.color.matrix) to the working
    primaries (Rec.709/sRGB), then to the working encoding (linear when project/@linearLight, else sRGB).
    Negative (out-of-gamut) components are clipped at 0; values above 1 (HDR highlights, log headroom)
    are kept. PQ is scaled so 203 cd/m2 = 1.0 and HLG so 75 % signal = 1.0 (BT.2408), as in
    scenerender.color.
  * 8-bit sRGB/auto sources with no conversion to do take a fast 8-bit path (identical result).
The float path draws the frame with cairo float surfaces (RGBA128F) under the frame matrix.

Alpha: auto/straight = straight alpha, premultiplied = premultiplied in the encoded space (divided out
before the transfer decode), none = alpha ignored (opaque).

timecodeStart (pinned): the source timecode of the first frame; metadata only. Clip addressing
(clipIn/clipOut/timeRemap) is in seconds from the first frame (clipIn defaults to 0 = the first frame);
`source_timecode(asset, src_t)` returns the source timecode of the frame shown at src_t.

Decoding is sequential: one decoder process per (file, decode size, format) reads forward; a backward jump
or a jump of more than ~2 s restarts it with an input seek (-ss). A small LRU keeps recent frames. When the
asset is drawn at less than half its size the decode itself is downscaled (area filter) by powers of two.

Stabilisation (layer/@stabilize, @stabilizeSmoothness; video and generated kind="video"):
  * Analysis (camera_path): the whole clip is decoded once as luma at <= 256 px (area downscale, resampled
    to @fps like drawing), and each frame is aligned to the previous one by a similarity (translation,
    rotation, uniform scale): FFT phase-correlation translation, then coarse-to-fine Gauss-Newton on the luma
    with Cauchy-weighted residuals. The pairwise maps chain into the camera path C_k (frame k -> frame 0).
    The result is memoised per (file, size, mtime, fps, stream) in rc.cache and on disk under
    cache_dir("stabilize"), so any frame's correction is independent of seek order and of worker processes.
  * Smoothing (smooth_path): tx, ty, rotation (unwrapped) and log scale are Gaussian-filtered with
    sigma = s / (1 - s) seconds (renormalised at the clip ends); s = 1 is the clip mean (locked off);
    s = 0 (or @stabilize false) skips stabilisation entirely (pixel-identical to an unstabilised layer).
  * Each frame is warped by S_k^-1 C_k (smoothed path after the actual path's inverse) in the stored
    orientation, before @rotation; frame-mix / optical-flow warp each of the two frames by its own correction.
  * No auto-crop or zoom (pinned): uncovered edges are transparent; crop or scale the layer to hide them.

Audio: `video_audio(rc_or_doc, asset)` extracts the audio stream to a cached WAV for the audio mixer.
"""
from __future__ import annotations

import atexit
import hashlib
import math
import os
import re
import subprocess
from collections import OrderedDict
from fractions import Fraction

import cairo
import numpy as np

from ..raster import Buf
from ..registry import ASSET_SIZES, ASSETS, FEATURES, FULL, warn_once
from ..values import parse_fps
from . import array_to_surface, cache_dir, ffmpeg_exe, generated_cache_path, probe_media, representation_src

_LRU = 6
_MAX_DECODERS = 8
_HDR_HEADROOM = 4096.0          # float surfaces clamp to [0, 1]: values are drawn scaled down by this (exact: power of 2)
_live: "OrderedDict[tuple, _Decoder]" = OrderedDict()


class _Decoder:
    """Sequential frame reader for one file at one output size and pixel format."""

    def __init__(self, path: str, fps: Fraction, out_w: int, out_h: int, scaled: bool, stream: int = 0,
                 deep: bool = False, scale_opts: str = ""):
        self.path, self.fps, self.w, self.h, self.scaled, self.stream = path, fps, out_w, out_h, scaled, stream
        self.deep, self.scale_opts = deep, scale_opts
        self.bpp = 8 if deep else 4
        self.proc: subprocess.Popen | None = None
        self.next_n = 0
        self.eof_at: int | None = None     # first index the stream could not deliver
        self.frames: "OrderedDict[int, np.ndarray]" = OrderedDict()
        self.restarts = 0                  # exposed for tests

    # -------------------------------------------------------- process
    def _start(self, n: int) -> None:
        self.close()
        cmd = [ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-noautorotate"]
        if n > 0:
            cmd += ["-ss", f"{float(Fraction(n) / self.fps):.6f}"]
        vf = f"fps={self.fps.numerator}/{self.fps.denominator}"
        if self.scaled or self.scale_opts or self.deep:
            size = f"{self.w}:{self.h}" if self.scaled else "iw:ih"
            vf += f",scale={size}:flags=area" + (f"+accurate_rnd+full_chroma_int{self.scale_opts}" if self.deep or
                                                   self.scale_opts else "")
        cmd += ["-i", self.path, "-map", f"0:v:{self.stream}", "-vf", vf, "-f", "rawvideo",
                "-pix_fmt", "rgba64le" if self.deep else "rgba", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                     bufsize=self.w * self.h * self.bpp * 2)
        self.next_n = n
        self.restarts += 1

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
                self.proc.stdout.close()
                self.proc.wait(timeout=2)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
            self.proc = None

    def _read_one(self) -> np.ndarray | None:
        size = self.w * self.h * self.bpp
        buf = bytearray(size)
        view = memoryview(buf)
        got = 0
        while got < size:
            k = self.proc.stdout.readinto(view[got:])
            if not k:
                return None
            got += k
        dt = "<u2" if self.deep else np.uint8
        return np.frombuffer(bytes(buf), dt).reshape(self.h, self.w, 4)

    # -------------------------------------------------------- access
    def frame(self, n: int) -> np.ndarray | None:
        if self.eof_at is not None and n >= self.eof_at:
            n = self.eof_at - 1
            if n < 0:
                return None
        hit = self.frames.get(n)
        if hit is not None:
            self.frames.move_to_end(n)
            return hit
        jump = n - self.next_n
        if self.proc is None or jump < 0 or jump > max(8, int(2 * self.fps)):
            self._start(n)
        last = None
        while True:
            fr = self._read_one()
            if fr is None:
                # Past the real end of the stream: hold the last frame it delivered.
                self.close()
                self.eof_at = self.next_n
                if last is not None:
                    self._keep(last[0], last[1])
                    return last[1]
                return self.frame(self.next_n - 1) if self.next_n > 0 else None
            idx = self.next_n
            self.next_n += 1
            if idx == n:
                self._keep(idx, fr)
                return fr
            last = (idx, fr)

    def _keep(self, idx: int, fr: np.ndarray) -> None:
        self.frames[idx] = fr
        while len(self.frames) > _LRU:
            self.frames.popitem(last=False)


@atexit.register
def _close_all() -> None:
    for d in _live.values():
        d.close()
    _live.clear()


def decoder(path: str, fps: Fraction, out_w: int, out_h: int, scaled: bool, stream: int = 0, deep: bool = False,
            scale_opts: str = "") -> _Decoder:
    key = (path, fps, out_w, out_h, stream, deep, scale_opts)
    d = _live.get(key)
    if d is None:
        d = _live[key] = _Decoder(path, fps, out_w, out_h, scaled, stream, deep, scale_opts)
        while len(_live) > _MAX_DECODERS:
            _live.popitem(last=False)[1].close()
    _live.move_to_end(key)
    return d


# ---------------------------------------------------------------- provenance (assetProvenance attributes)
def provenance_src(rc, asset) -> str:
    """Resolved media path of an asset with assetProvenance attributes (video, lottie, vector svg).

    Pinned: a <representation> named by the representation preference wins; else, when the preference is
    "proxy" and the asset has @proxy, the proxy file; else @src. @sha256 is the digest of @src: a file that
    does not match warns once and is still used (lenient, like schema errors; the proxy and representations
    are not checked against it). license / credit are metadata only.
    """
    pref = rc.cache.get("representation")
    src = representation_src(rc, asset)
    if src == asset.get("src") and pref == "proxy" and asset.get("proxy"):
        return rc.doc.resolve_path(asset.get("proxy"))
    path = rc.doc.resolve_path(src)
    want = (asset.get("sha256") or "").lower()
    if want and src == asset.get("src"):
        from . import file_sha256                        # memoised on path, size and mtime
        got = file_sha256(path)
        if got is not None and got != want:
            warn_once("asset-sha256", asset.get("id") or path,
                      f"{path}: sha256 {got[:12]}... does not match @sha256 {want[:12]}...; using the file anyway")
    return path


# ---------------------------------------------------------------- stream properties
_PROBE_FMT: dict = {}


def stream_format(path: str) -> dict:
    """pix_fmt, bit depth, yuv?, range, matrix, primaries, transfer tags of the first video stream (memoised)."""
    if path in _PROBE_FMT:
        return _PROBE_FMT[path]
    info = {"pix_fmt": "", "depth": 8, "yuv": False, "range": None, "matrix": None, "primaries": None, "trc": None}
    if os.path.exists(path):
        r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", path], capture_output=True)
        for line in r.stderr.decode(errors="replace").splitlines():
            if "Video:" not in line:
                continue
            rest = line.split("Video:", 1)[1]
            depth, i = 0, 0
            while i < len(rest):                      # skip the codec description (to the first top-level comma)
                depth += {"(": 1, ")": -1}.get(rest[i], 0)
                if rest[i] == "," and depth == 0:
                    break
                i += 1
            m = re.match(r"\s*([A-Za-z0-9_]+)(?:\(([^)]*)\))?", rest[i + 1:])
            if m:
                fmt = m.group(1)
                info["pix_fmt"] = fmt
                info["depth"] = pix_depth(fmt)
                info["yuv"] = bool(re.match(r"(yuv|yuva|nv|p0|p2|p4|y2|ayuv|v210|uyvy|yuyv|xv)", fmt))
                for part in (p.strip() for p in (m.group(2) or "").split(",")):
                    if part in ("tv", "pc"):
                        info["range"] = part
                    elif part not in ("progressive", "top first", "bottom first", "top coded first (swapped)") and part:
                        tags = part.split("/")
                        if len(tags) == 1:
                            tags = tags * 3
                        if len(tags) == 3:
                            info["matrix"], info["primaries"], info["trc"] = (None if t in ("unknown", "reserved") else t
                                                                              for t in tags)
            break
    _PROBE_FMT[path] = info
    return info


def pix_depth(fmt: str) -> int:
    """Bits per component of an ffmpeg pixel format name."""
    if re.search(r"(rgb|bgr)a?(48|64)", fmt) or fmt.startswith(("p016", "p216", "p416", "y216")):
        return 16
    if fmt.startswith(("p010", "p210", "p410", "y210", "x2rgb10", "x2bgr10", "v210", "xv30")):
        return 10
    if fmt.startswith(("p012", "y212", "xv36")):
        return 12
    m = re.search(r"p(9|10|12|14|16)(le|be)?$", fmt) or re.search(r"(9|10|12|14|16)(le|be)$", fmt)
    if m:
        return int(m.group(1))
    if "f32" in fmt or "f16" in fmt:
        return 16
    return 8


def _scale_opts(info: dict, color_space: str, w: int, h: int) -> str:
    """swscale options pinning the YUV matrix/range of untagged streams."""
    if not info.get("yuv"):
        return ""
    opts = ""
    if not info.get("matrix"):
        mat = "bt2020" if color_space == "rec2020" else ("bt709" if (w >= 1280 or h > 576) else "bt601")
        opts += f":in_color_matrix={mat}"
    if not info.get("range"):
        opts += ":in_range=tv"
    return opts


# ---------------------------------------------------------------- transfer functions (scenerender.color is the single home)
def decode_transfer(v: np.ndarray, tf: str) -> np.ndarray:
    """Code value -> linear light for every transferType value."""
    from .. import color
    return np.asarray(color.decode(v, tf), np.float32)


def encode_transfer(x: np.ndarray, tf: str) -> np.ndarray:
    from .. import color
    return np.asarray(color.encode(x, tf), np.float32)


def color_params(rc, asset) -> tuple[str, str]:
    """(colorSpace, transfer) of the asset, taken from the chosen representation when it declares them."""
    cs, tf = asset.get("colorSpace", "srgb"), asset.get("transfer", "auto")
    pref = rc.cache.get("representation") if hasattr(rc, "cache") else None
    if pref:
        for r in asset:
            if getattr(r, "tag", None) == "representation" and r.get("name") == pref:
                cs, tf = r.get("colorSpace") or cs, r.get("transfer") or tf
    return cs, tf


def _resolved_tf(cs: str, tf: str) -> str:
    from .. import color
    return color.SPACES.get(cs, ("srgb", "srgb"))[1] if tf in (None, "", "auto") else tf


def needs_conversion(cs: str, tf: str) -> bool:
    return not (cs == "srgb" and _resolved_tf(cs, tf) == "srgb")


def to_working(rc, rgba: np.ndarray, cs: str, tf: str, alpha_mode: str) -> np.ndarray:
    """Decoded frame (uint8 or uint16 RGBA code values) -> premultiplied float working-space pixels."""
    from .. import color
    scale = 65535.0 if rgba.dtype != np.uint8 else 255.0
    f = rgba.astype(np.float32) / np.float32(scale)
    rgb, a = f[..., :3], f[..., 3:4]
    if alpha_mode == "none":
        a = np.ones_like(a)
    elif alpha_mode == "premultiplied":
        rgb = np.where(a > 1e-6, rgb / np.maximum(a, 1e-6), 0.0)
    lin = decode_transfer(rgb, _resolved_tf(cs, tf))
    prim = color.SPACES.get(cs, ("srgb", "srgb"))[0]
    work_prim = color.working_primaries(rc)
    if prim not in (work_prim, None):
        lin = lin @ color.matrix(prim, work_prim).T.astype(np.float32)
    lin = np.maximum(lin, 0.0)
    work = lin if rc.linear else np.asarray(color.encode(lin, "srgb"), np.float32)
    out = np.empty(f.shape, np.float32)
    out[..., :3] = work * a
    out[..., 3:] = a
    return out


# ---------------------------------------------------------------- frame mix
def mix_indices(src_t: float, fps: Fraction, duration: float | None) -> tuple[int, int, float]:
    """(n0, n1, u): frames around src_t and the weight of n1 (After Effects Frame Mix)."""
    p = src_t * float(fps)
    n0 = int(math.floor(p + 1e-6))
    u = max(0.0, p - n0)
    if u < 1e-6:
        u = 0.0
    a, b = frame_index(n0 / float(fps) + 1e-9, fps, duration), frame_index((n0 + 1) / float(fps) + 1e-9, fps, duration)
    return a, b, (u if b != a else 0.0)


def flow_frames(a: np.ndarray, b: np.ndarray, u: float) -> np.ndarray:
    """Optical-flow frame blending (After Effects Pixel Motion): dense flow between the two frames,
    both warped to the in-between time u and crossfaded, so motion interpolates instead of ghosting."""
    if u <= 0:
        return a
    if u >= 1:
        return b
    from ..effects.fields import flow_warp, optical_flow
    size = max(32, min(1024, max(a.shape[:2])))
    ab = optical_flow(a, b, size, 4, 9)
    ba = optical_flow(b, a, size, 4, 9)
    return flow_warp(a, ab, u) * np.float32(1 - u) + flow_warp(b, ba, 1 - u) * np.float32(u)


def mix_frames(a: np.ndarray, b: np.ndarray, u: float) -> np.ndarray:
    """Blend two premultiplied working-space frames: (1 - u) a + u b."""
    if u <= 0:
        return a
    if u >= 1:
        return b
    return a * np.float32(1 - u) + b * np.float32(u)


# ---------------------------------------------------------------- drawing
def _frame_surface(rgba: np.ndarray, alpha_mode: str) -> cairo.ImageSurface:
    a = rgba[..., 3:4].astype(np.uint16)
    if alpha_mode == "none":
        rgb = rgba[..., :3]
        alpha = np.full(rgba.shape[:2], 255, np.uint8)
    elif alpha_mode == "premultiplied":
        rgb = np.minimum(rgba[..., :3], rgba[..., 3:4])
        alpha = rgba[..., 3]
    else:
        rgb = ((rgba[..., :3].astype(np.uint16) * a + 127) // 255).astype(np.uint8)
        alpha = rgba[..., 3]
    bgra = np.empty(rgba.shape, np.uint8)
    bgra[..., 0], bgra[..., 1], bgra[..., 2], bgra[..., 3] = rgb[..., 2], rgb[..., 1], rgb[..., 0], alpha
    return array_to_surface(bgra)


def _float_surface(px: np.ndarray) -> tuple[cairo.ImageSurface, np.ndarray]:
    h, w = px.shape[:2]
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_RGBA128F, w)
    buf = np.zeros((h, stride // 16, 4), np.float32)
    buf[:, :w] = np.clip(px / np.float32(_HDR_HEADROOM), 0.0, 1.0)
    surf = cairo.ImageSurface.create_for_data(memoryview(buf).cast("B"), cairo.FORMAT_RGBA128F, w, h, stride)
    return surf, buf


def display_size(asset) -> tuple[float, float]:
    w = float(asset.get("width") or 0) * float(asset.get("pixelAspect") or 1)
    h = float(asset.get("height") or 0)
    return (h, w) if int(asset.get("rotation") or 0) in (90, 270) else (w, h)


def frame_index(src_t: float, fps: Fraction, duration: float | None) -> int:
    n = int(math.floor(src_t * float(fps) + 1e-6))
    if duration:
        count = max(1, int(math.floor(duration * float(fps) + 1e-3)))
        n = min(n, count - 1)
    return max(0, n)


def _orient(cr, rotation: int, sw: float, sh: float) -> None:
    if rotation == 90:
        cr.translate(sh, 0)
        cr.rotate(math.pi / 2)
    elif rotation == 180:
        cr.translate(sw, sh)
        cr.rotate(math.pi)
    elif rotation == 270:
        cr.translate(0, sw)
        cr.rotate(-math.pi / 2)


# ---------------------------------------------------------------- stabilisation (layer/@stabilize)
_STAB_VERSION = "1"
_STAB_SIZE = 256                 # analysis frames: longest side downscaled (area) to at most this


def _bilinear(img: np.ndarray, X: np.ndarray, Y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sample img (h, w[, c]) at index coordinates (X, Y); zero and invalid outside."""
    h, w = img.shape[:2]
    valid = (X >= 0) & (Y >= 0) & (X <= w - 1) & (Y <= h - 1)
    x0 = np.clip(np.floor(X).astype(np.int64), 0, max(0, w - 2))
    y0 = np.clip(np.floor(Y).astype(np.int64), 0, max(0, h - 2))
    fx, fy = np.clip(X - x0, 0, 1), np.clip(Y - y0, 0, 1)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    if img.ndim == 3:
        fx, fy = fx[..., None], fy[..., None]
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bot = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    out = top * (1 - fy) + bot * fy
    return (out * (valid[..., None] if img.ndim == 3 else valid)), valid


def _sim(p) -> np.ndarray:
    return np.array([[1 + p[0], -p[1], p[2]], [p[1], 1 + p[0], p[3]], [0.0, 0.0, 1.0]])


def _phase_shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Translation t with b(x) ~ a(x + t) (phase correlation, parabolic sub-pixel peak)."""
    h, w = a.shape
    win = np.outer(np.hanning(h), np.hanning(w))
    A, B = np.fft.fft2((a - a.mean()) * win), np.fft.fft2((b - b.mean()) * win)
    R = B * np.conj(A)
    R /= np.maximum(np.abs(R), 1e-12)
    r = np.fft.ifft2(R).real
    iy, ix = np.unravel_index(int(np.argmax(r)), r.shape)

    def sub(c, n, get):
        l, m, rr = get((c - 1) % n), get(c), get((c + 1) % n)
        d = l - 2 * m + rr
        return 0.0 if abs(d) < 1e-12 else 0.5 * (l - rr) / d
    dy = iy + sub(iy, h, lambda k: r[k, ix])
    dx = ix + sub(ix, w, lambda k: r[iy, k])
    dy = dy - h if dy > h / 2 else dy
    dx = dx - w if dx > w / 2 else dx
    return -dx, -dy


def estimate_similarity(a: np.ndarray, b: np.ndarray, levels: int = 3, iters: int = 15) -> np.ndarray:
    """3x3 similarity W (translation, rotation, uniform scale) in centred pixel coordinates of b with
    b(x) ~ a(W x): phase-correlation translation, then coarse-to-fine Gauss-Newton on the luma with
    Cauchy-weighted residuals (moving foreground is down-weighted). Deterministic; identity when the
    frames carry no structure."""
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    tx, ty = _phase_shift(a, b)
    pa, pb = [a], [b]
    for _ in range(levels - 1):
        if min(pa[-1].shape) < 32:
            break
        for p in (pa, pb):
            m = p[-1]
            hh, ww = m.shape[0] // 2 * 2, m.shape[1] // 2 * 2
            p.append(m[:hh, :ww].reshape(hh // 2, 2, ww // 2, 2).mean((1, 3)))
    n = len(pa)
    p = np.array([0.0, 0.0, tx / 2 ** (n - 1), ty / 2 ** (n - 1)])
    for lvl in range(n - 1, -1, -1):
        A, B = pa[lvl], pb[lvl]
        h, w = A.shape
        gy, gx = np.gradient(A)
        cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
        v, u = np.mgrid[0:h, 0:w].astype(np.float64)
        u -= cx
        v -= cy
        norm = max(w, h) / 2.0                          # conditions the rotation/scale columns
        for _ in range(iters):
            X = (1 + p[0]) * u - p[1] * v + p[2] + cx
            Y = p[1] * u + (1 + p[0]) * v + p[3] + cy
            aw, ok = _bilinear(A, X, Y)
            ok &= (X >= 1) & (Y >= 1) & (X <= w - 2) & (Y <= h - 2)
            if ok.sum() < 16:
                break
            gxs, _ = _bilinear(gx, X, Y)
            gys, _ = _bilinear(gy, X, Y)
            r = (aw - B)[ok]
            J = np.stack([(gxs * u + gys * v)[ok] / norm, (-gxs * v + gys * u)[ok] / norm, gxs[ok], gys[ok]], 1)
            c = 2.5 * (np.median(np.abs(r)) + 1e-6) / 0.6745
            wt = 1.0 / (1.0 + (r / c) ** 2)
            H = (J * wt[:, None]).T @ J
            g = (J * wt[:, None]).T @ r
            try:
                dp = -np.linalg.solve(H + 1e-9 * np.trace(H) * np.eye(4) + 1e-12 * np.eye(4), g)
            except np.linalg.LinAlgError:
                break
            dp[:2] /= norm
            p += dp
            if abs(dp[2]) + abs(dp[3]) < 1e-3 and abs(dp[0]) + abs(dp[1]) < 1e-5:
                break
        if lvl > 0:
            p[2:] *= 2.0
    if not np.all(np.isfinite(p)) or abs(p[0]) > 0.5 or abs(p[1]) > 0.5:
        return np.eye(3)
    return _sim(p)


def _luma_frames(path: str, fps: Fraction, stream: int) -> tuple[np.ndarray, int, int] | None:
    info = probe_media(path)
    nw, nh = info.get("width") or 0, info.get("height") or 0
    if not nw or not nh:
        return None
    k = min(1.0, _STAB_SIZE / max(nw, nh))
    aw, ah = max(8, int(round(nw * k))), max(8, int(round(nh * k)))
    cmd = [ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-noautorotate", "-i", path, "-map", f"0:v:{stream}",
           "-vf", f"fps={fps.numerator}/{fps.denominator},scale={aw}:{ah}:flags=area", "-f", "rawvideo",
           "-pix_fmt", "gray", "-"]
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)
    n = len(r.stdout) // (aw * ah)
    if r.returncode != 0 or n == 0:
        return None
    return np.frombuffer(r.stdout[:n * aw * ah], np.uint8).reshape(n, ah, aw).astype(np.float64) / 255.0, aw, ah


def camera_path(rc, path: str, fps: Fraction, stream: int = 0) -> dict | None:
    """Whole-clip camera path of a video: {"params": (N, 4) [tx, ty, theta, log s] of C_k (frame-k centred
    analysis pixels -> frame-0 coordinates, C_k = C_{k-1} W_k), "size": (aw, ah) analysis size}.
    Analysed once per (file, size, mtime, fps, stream): memoised in rc.cache and on disk under
    cache_dir("stabilize"), so the result is independent of seek order and render parallelism."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = ("stabilize", os.path.abspath(path), st.st_size, st.st_mtime_ns, fps, stream)
    cache = rc.cache if hasattr(rc, "cache") else {}
    if key in cache:
        return cache[key]
    digest = hashlib.sha1(("|".join(map(str, key)) + "|" + _STAB_VERSION).encode()).hexdigest()[:20]
    disk = os.path.join(cache_dir("stabilize"), digest + ".npz")
    res = None
    if os.path.exists(disk):
        try:
            z = np.load(disk)
            res = {"params": z["params"], "size": (int(z["size"][0]), int(z["size"][1]))}
        except Exception:  # noqa: BLE001 — a corrupt cache entry is recomputed
            res = None
    if res is None:
        got = _luma_frames(path, fps, stream)
        if got is None:
            warn_once("stabilize", path, "cannot decode the clip for stabilisation analysis; drawn unstabilised")
            cache[key] = None
            return None
        frames, aw, ah = got
        C = np.eye(3)
        params = np.zeros((len(frames), 4))
        for i in range(1, len(frames)):
            C = C @ estimate_similarity(frames[i - 1], frames[i])
            params[i] = (C[0, 2], C[1, 2], math.atan2(C[1, 0], C[0, 0]), math.log(math.hypot(C[0, 0], C[1, 0])))
        params[:, 2] = np.unwrap(params[:, 2])
        res = {"params": params, "size": (aw, ah)}
        tmp = disk + f".{os.getpid()}.tmp.npz"
        try:
            np.savez(tmp, params=params, size=np.array([aw, ah]))
            os.replace(tmp, disk)
        except OSError:
            pass
    cache[key] = res
    return res


def smooth_path(params: np.ndarray, fps: float, smoothness: float) -> np.ndarray:
    """Smoothed camera path: each parameter filtered by a Gaussian of sigma = s / (1 - s) seconds
    (0.25 -> 1/3 s, 0.5 -> 1 s, 0.75 -> 3 s), truncated at the clip ends and renormalised, so it tends to
    the clip mean (a locked-off camera, exactly at s = 1); s = 0 returns the path unchanged."""
    s = min(1.0, max(0.0, float(smoothness)))
    if s <= 0 or len(params) < 2:
        return params.copy()
    if s >= 1:
        return np.repeat(params.mean(0, keepdims=True), len(params), 0)
    sigma = max(1e-6, s / (1 - s) * fps)
    i = np.arange(len(params), dtype=np.float64)
    wts = np.exp(-0.5 * ((i[:, None] - i[None, :]) / sigma) ** 2)
    wts /= wts.sum(1, keepdims=True)
    return wts @ params


def _param_matrix(q) -> np.ndarray:
    tx, ty, th, ls = q
    sc = math.exp(ls)
    return np.array([[sc * math.cos(th), -sc * math.sin(th), tx], [sc * math.sin(th), sc * math.cos(th), ty], [0, 0, 1.0]])


def stabilize_correction(rc, path: str, fps: Fraction, n: int, smoothness: float, stream: int = 0) -> tuple | None:
    """(W, (aw, ah)): the similarity S_n^-1 C_n taking frame-n centred analysis pixels to stabilised
    output coordinates (S = smoothed path), or None when there is nothing to correct."""
    if smoothness <= 0:
        return None
    cp = camera_path(rc, path, fps, stream)
    if cp is None or len(cp["params"]) < 2:
        return None
    key = ("stabilize-smooth", id(cp["params"]), round(float(smoothness), 6))
    cache = rc.cache if hasattr(rc, "cache") else {}
    sm = cache.get(key)
    if sm is None:
        sm = cache[key] = (cp["params"], smooth_path(cp["params"], float(fps), smoothness))
    params, smoothed = sm
    n = min(max(0, n), len(params) - 1)
    return np.linalg.inv(_param_matrix(smoothed[n])) @ _param_matrix(params[n]), cp["size"]


def _box_correction(W: np.ndarray, an: tuple[int, int], bw: float, bh: float) -> np.ndarray:
    """W (centred analysis pixels) expressed in a (0,0)-(bw,bh) box with the same framing."""
    D = np.diag([bw / an[0], bh / an[1], 1.0])
    T = np.array([[1, 0, bw / 2], [0, 1, bh / 2], [0, 0, 1.0]])
    return T @ D @ W @ np.linalg.inv(D) @ np.linalg.inv(T)


def warp_frame(px: np.ndarray, Wpx: np.ndarray) -> np.ndarray:
    """Premultiplied (h, w, 4) frame warped by Wpx (pixel-box coords -> pixel-box coords), bilinear,
    transparent outside the source."""
    h, w = px.shape[:2]
    Y, X = np.mgrid[0:h, 0:w].astype(np.float64)
    inv = np.linalg.inv(Wpx)
    xs = inv[0, 0] * (X + 0.5) + inv[0, 1] * (Y + 0.5) + inv[0, 2] - 0.5
    ys = inv[1, 0] * (X + 0.5) + inv[1, 1] * (Y + 0.5) + inv[1, 2] - 0.5
    out, _ = _bilinear(px, xs, ys)
    return out.astype(np.float32)


def layer_stabilize(rc, layer, ctx) -> float:
    """layer/@stabilize -> the smoothness to apply (0 = off)."""
    if layer is None or not rc.ev.bool(layer, "stabilize", ctx, False):
        return 0.0
    return min(1.0, max(0.0, rc.ev.num(layer, "stabilizeSmoothness", ctx, 0.5)))


def render_video_file(rc, path: str, M, aw: float, ah: float, *, fps: Fraction, duration: float | None,
                      src_t: float, clip=None, rotation: int = 0, alpha: str = "auto", stream: int = 0,
                      coded: tuple[int, int] | None = None, color_space: str = "srgb", transfer: str = "auto",
                      frame_blend: str = "none", stabilize: float = 0.0):
    """Draw the frame of `path` at source time src_t into the display box (0,0)-(aw,ah) under M.
    stabilize: layer/@stabilizeSmoothness when layer/@stabilize is on (0 = off)."""
    info = probe_media(path)
    if not os.path.exists(path) or not info.get("has_video"):
        warn_once("asset-file", path, "video file not found or has no video stream")
        return None
    fmt = stream_format(path)
    nw = info.get("width") or (coded or (int(aw), int(ah)))[0]
    nh = info.get("height") or (coded or (int(aw), int(ah)))[1]
    c = rc.canvas_for(M, aw, ah, 2)
    if c is None:
        return None
    sw, sh = (ah, aw) if rotation in (90, 270) else (aw, ah)          # stored-orientation box
    px_scale = math.sqrt(abs(np.linalg.det(M[:2, :2]))) * sw / max(1, nw)
    level = int(math.floor(math.log2(1 / px_scale))) if 0 < px_scale < 0.5 else 0
    level = min(level, 6)
    ow, oh = max(1, nw >> level), max(1, nh >> level)
    deep = fmt["depth"] > 8
    mix = frame_blend in ("frame-mix", "optical-flow")
    precise = deep or mix or needs_conversion(color_space, transfer)
    dec = decoder(path, fps, ow, oh, level > 0, stream, deep, _scale_opts(fmt, color_space, nw, nh) if precise else "")
    x0, y0, x1, y1 = clip if clip else (0, 0, aw, ah)
    def correction(n):
        return stabilize_correction(rc, path, fps, n, stabilize, stream) if stabilize > 0 else None
    if not precise:
        n = frame_index(src_t, fps, duration)
        fr = dec.frame(n)
        if fr is None:
            return None
        surf = _frame_surface(fr, alpha)
        cr = c.cr
        cr.rectangle(x0, y0, x1 - x0, y1 - y0)
        cr.clip()
        _orient(cr, rotation, sw, sh)
        corr = correction(n)
        if corr is not None:
            B = _box_correction(corr[0], corr[1], sw, sh)
            cr.transform(cairo.Matrix(B[0, 0], B[1, 0], B[0, 1], B[1, 1], B[0, 2], B[1, 2]))
        cr.scale(sw / ow, sh / oh)
        cr.set_source_surface(surf, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.get_source().set_extend(cairo.EXTEND_PAD if corr is None else cairo.EXTEND_NONE)
        cr.paint()
        return c.to_buf(rc.linear)
    # ---- float path: colour-managed, 16-bit sources, frame mix
    if mix:
        n0, n1, u = mix_indices(src_t, fps, duration)
    else:
        n0, n1, u = frame_index(src_t, fps, duration), 0, 0.0
    f0 = dec.frame(n0)
    if f0 is None:
        return None
    def stabilised(px, n):
        corr = correction(n)
        return px if corr is None else warp_frame(px, _box_correction(corr[0], corr[1], ow, oh))
    px = stabilised(to_working(rc, f0, color_space, transfer, alpha), n0)
    extend = cairo.EXTEND_PAD if stabilize <= 0 else cairo.EXTEND_NONE
    if u > 0:
        f1 = dec.frame(n1)
        if f1 is not None:
            other = stabilised(to_working(rc, f1, color_space, transfer, alpha), n1)
            px = flow_frames(px, other, u) if frame_blend == "optical-flow" else mix_frames(px, other, u)
    surf, _keep = _float_surface(px)
    r = c.rect
    dst = cairo.ImageSurface(cairo.FORMAT_RGBA128F, r[2] - r[0], r[3] - r[1])
    cr = cairo.Context(dst)
    cr.translate(-r[0], -r[1])
    cr.transform(cairo.Matrix(M[0, 0], M[1, 0], M[0, 1], M[1, 1], M[0, 2], M[1, 2]))
    cr.rectangle(x0, y0, x1 - x0, y1 - y0)
    cr.clip()
    _orient(cr, rotation, sw, sh)
    cr.scale(sw / ow, sh / oh)
    cr.set_source_surface(surf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.get_source().set_extend(extend)
    cr.paint()
    dst.flush()
    out = np.frombuffer(dst.get_data(), np.float32).reshape(dst.get_height(), dst.get_stride() // 16, 4)
    out = out[:, :dst.get_width()] * np.float32(_HDR_HEADROOM)
    return Buf(np.ascontiguousarray(out, np.float32), r[0], r[1])


def source_timecode(asset, src_t: float) -> str | None:
    """Source timecode (HH:MM:SS:FF, ';' for drop-frame) of the frame shown at src_t, from @timecodeStart."""
    tc = asset.get("timecodeStart")
    if not tc:
        return None
    fps = parse_fps(asset.get("fps"))
    nominal = int(round(float(fps)))
    m = re.match(r"(\d+):(\d+):(\d+)([:;])(\d+)", tc)
    if not m:
        return None
    hh, mm, ss, sep, ff = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), int(m.group(5))
    drop = sep == ";"
    total = ((hh * 60 + mm) * 60 + ss) * nominal + ff
    if drop:                                             # SMPTE drop-frame: 2 (or 4 at 59.94) numbers per minute
        d = 2 * max(1, nominal // 30)
        total_min = hh * 60 + mm
        total -= d * (total_min - total_min // 10)
    total += frame_index(src_t, fps, None)
    if drop:
        d = 2 * max(1, nominal // 30)
        per10 = nominal * 600 - d * 9
        per1 = nominal * 60 - d
        tens, rem = divmod(total, per10)
        total += d * 9 * tens + (d * ((rem - d) // per1) if rem > d else 0)
    ff = total % nominal
    s = total // nominal
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}{sep}{ff:02d}"


FEATURES.declare("layer:stabilize", FULL,
                 "video and generated kind=video layers: whole-clip similarity (translation, rotation, scale) "
                 "camera path from coarse-to-fine luma alignment, Gaussian-smoothed by stabilizeSmoothness "
                 "(1 = locked off); no auto-crop, uncovered edges are transparent")


@ASSETS.register("video", level=FULL,
                 note="ffmpeg decode resampled to @fps; colorSpace/transfer decoded into the working space "
                      "(16-bit decode for deep sources); frame-mix and optical-flow blending; rotation, pixelAspect, alpha modes, representations; stabilize")
def render_video(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    path = provenance_src(rc, asset)
    aw, ah = display_size(asset)
    cs, tf = color_params(rc, asset)
    blend = rc.ev.str(layer, "frameBlend", ctx, "none") if layer is not None else "none"
    return render_video_file(rc, path, M, aw, ah, fps=parse_fps(asset.get("fps")),
                             duration=float(asset.get("duration")) if asset.get("duration") else None,
                             src_t=src_t, clip=clip, rotation=int(asset.get("rotation") or 0),
                             alpha=asset.get("alpha", "auto"),
                             coded=(int(asset.get("width")), int(asset.get("height"))),
                             color_space=cs, transfer=tf, frame_blend=blend or "none",
                             stabilize=layer_stabilize(rc, layer, ctx))


@ASSET_SIZES.register("video")
def video_size(rc, asset, ctx):
    return display_size(asset)


# ---------------------------------------------------------------- audio for the mixer
def video_audio(rc_or_doc, asset, sample_rate: int | None = None, channels: int | None = None) -> str | None:
    """Extract the audio stream of a video (or generated kind="video") asset to a cached 16-bit PCM WAV.

    rc_or_doc: a RenderContext (honours the representation preference) or a Document.
    Uses asset/@audioStream (default 0). Keeps the source rate/channels unless sample_rate / channels are
    given. Returns the WAV path, or None when the asset has no audio (video @hasAudio="false", no audio
    stream in the file, missing file, or a generated cache that fails its sha256 check).
    Files live in $SCENERENDER_CACHE/audio (default ~/.cache/scenerender/audio), keyed by source path,
    size, mtime, stream and format, so repeated calls are free.
    """
    from ..document import ln
    doc = getattr(rc_or_doc, "doc", rc_or_doc)
    kind = ln(asset)
    if kind == "generated":
        if asset.get("kind") != "video":
            return None
        path = generated_cache_path(doc, asset)
        if path is None:
            return None
    elif kind == "video":
        if asset.get("hasAudio", "false") != "true":
            return None
        src = representation_src(rc_or_doc, asset) if hasattr(rc_or_doc, "cache") else asset.get("src")
        path = doc.resolve_path(src)
    else:
        return None
    if not os.path.exists(path) or not probe_media(path).get("has_audio"):
        warn_once("asset-file", path, "no audio stream")
        return None
    stream = int(asset.get("audioStream") or 0)
    st = os.stat(path)
    key = f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{stream}|{sample_rate}|{channels}"
    out = os.path.join(cache_dir("audio"), hashlib.sha1(key.encode()).hexdigest()[:20] + ".wav")
    if os.path.exists(out):
        return out
    tmp = out + f".{os.getpid()}.tmp.wav"
    cmd = [ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-y", "-i", path, "-map", f"0:a:{stream}", "-vn",
           "-c:a", "pcm_s16le"]
    if sample_rate:
        cmd += ["-ar", str(int(sample_rate))]
    if channels:
        cmd += ["-ac", str(int(channels))]
    try:
        subprocess.run(cmd + [tmp], check=True, capture_output=True)
        os.replace(tmp, out)
    except subprocess.CalledProcessError as e:
        warn_once("asset-file", path, f"audio extraction failed: {e.stderr.decode(errors='replace').strip()[:200]}")
        return None
    return out
