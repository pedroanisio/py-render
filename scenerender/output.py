"""Outputs: `scenerender render`.

    render_outputs(renderer, args) -> exit code

With -o PATH one file is written; the extension picks codec and container (.mp4/.mov/.mkv ->
H.264, .webm -> VP9, .mxf -> DNxHR, .gif, .apng, .webp, .wav/.m4a/.mp3/.flac -> audio only);
a directory, a path ending in "/" or a printf pattern ("%04d") gives a PNG sequence
(.jpg/.tif/.exr patterns give those sequences).

Without -o every <output> is rendered (or those named with --output). Each output opens its own
Renderer for its layout, variant and representation, at render scale
    output width / (layout width, else project width) x --scale
(the height is matched by resizing when the aspect differs). --fps/--from/--to/--crf override
the output's fps/start/end/crf; the document's layout/variant are used where --layout/--variant
are not given on the command line.

Paths: output, poster, thumbnail and destination paths are relative to the current working
directory (not the scene file), like every other CLI path.

Frames are pure functions of t: they are streamed as raw RGB(A) to ffmpeg's stdin, rendered by
--jobs worker processes (each opens its own Renderer in a Pool initializer, results are
consumed in order). --frames-dir keeps every frame (PNG, or .npy for 16-bit/float frames) and a
rerun reuses the frames already there.

Pinned delivery rules:

* tiff-sequence: 16-bit RGB(A) TIFF (deflate) written by ffmpeg from the 16-bit frame.
* gif: frames are collected first; per-frame palettes (palettegen stats_mode=single, paletteuse
  new=1) are used when the frames' colours (4-bit bins covering 99 % of each frame's pixels) do
  not fit one palette (> 256 bins and > 1.5 x the largest frame's), else one global palette
  (stats_mode=full, rectangle diff). Bayer 4 colour dither (stable in animation). With alpha,
  coverage becomes 1-bit by ordered 8x8 Bayer dithering (soft edges and fades keep their mean
  coverage) and one palette entry is reserved for transparency.
* maxFileSize: rate-controlled codecs (h264/h265/av1/vp9) start at 0.97 x size x 8 / duration
  minus the audio bitrate (audio takes at most a quarter of the budget), frames go once into a
  lossless FFV1 intermediate, and the encode is repeated with the bitrate scaled by the measured
  overshoot (>= 10 % lower each time, 5 encodes at most) until the file fits; WebP bisects its
  quality; codecs without rate control (prores, dnxhr, ffv1, apng, gif) fail when over the cap.
* HDR metadata (maxCLL, maxFALL, masteringDisplay in x265's "G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min)"
  ST 2086 units): h265 also gets x265 SEI; every mp4/mov gets mdcv + clli boxes in the video
  sample entry (h264, h265, av1, vp9, prores, dnxhr); mkv/webm/mxf go through an MP4/MOV copy
  that ffmpeg's demuxer turns into stream side data, which the Matroska muxer writes as Colour
  MasteringMetadata/MaxCLL/MaxFALL. MXF content light levels are then added directly
  to its picture descriptors and primer packs (scenerender.mxf).
* Spherical metadata (project mode="equirectangular", output/@sphericalMetadata true): Google
  Spherical Video V2 st3d (stereo mode from scene360/@stereo) and sv3d (svhd, proj: prhd zero
  pose + equi full-sphere bounds, cbmp layout 0, or for eac / fisheye-180 a mshp mesh projection
  built by scenerender.spherical_mesh) boxes, injected in Python into the first video sample entry
  (sizes rewritten, stco/co64 shifted when moov precedes mdat); mkv/webm get Projection +
  StereoMode through the same side-data route, except the mesh (ffmpeg has no mesh side data):
  its Projection (ProjectionType 3, ProjectionPrivate = the mshp payload) is written into the
  video TrackEntry afterwards (EBML sizes, SeekHead/Cues positions and CRC-32 elements rewritten).
* Destinations other than "file" upload only with --publish (scenerender.publish); without it
  they are skipped with a warning. A failed upload fails the output.
* QA (scenerender.qa) runs per output unless --no-qa: static checks before encoding, flash
  analysis on the frames as they stream; error-level findings fail the output (exit status 4).
"""
from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

from . import color  # noqa: F401  (registers the colour-management finishing hook)
from .registry import CODECS, FEATURES, FULL, NONE, PARTIAL, warn_once
from .values import parse_bool, parse_fps

log = logging.getLogger("scenerender")

for _c in ("h264", "h265", "av1", "vp9", "ffv1", "apng", "webp", "png-sequence", "audio-only"):
    CODECS.declare(_c, FULL)
CODECS.declare("prores", FULL, "prores_ks; proresProfile proxy/lt/422/hq/4444/4444xq")
CODECS.declare("dnxhr", FULL, "dnxhd encoder, DNxHR profiles (profile attribute, default dnxhr_hq)")
CODECS.declare("gif", PARTIAL, "GIF format limits: <= 256 colours per frame (global or per-frame palettes chosen from "
               "the frames' colour spread) and 1-bit transparency (alpha ordered-dithered with an 8x8 Bayer matrix)")
CODECS.declare("jpeg-sequence", FULL, "no alpha")
CODECS.declare("tiff-sequence", FULL, "16-bit RGB(A) TIFF (deflate) from the float frame")
CODECS.declare("exr-sequence", FULL, "float32 linear-light OpenEXR (ZIP16), premultiplied alpha when alpha=true")
FEATURES.declare("output:twoPass", FULL, "lossless FFV1 intermediate, then ffmpeg pass 1 + pass 2 (bitrate modes only)")
FEATURES.declare("output:maxFileSize", FULL, "bitrate/quality derived from the budget, file size verified and re-encoded "
                 "from a lossless intermediate until it fits (5 tries); fixed-rate codecs fail when over")
FEATURES.declare("output:hdrMetadata", FULL, "maxCLL/maxFALL/masteringDisplay: x265 SEI for h265, mdcv/clli sample-entry "
                 "boxes in mp4/mov, Matroska/WebM Colour MasteringMetadata/MaxCLL/MaxFALL")
FEATURES.declare("output:hdrMetadata:mxf", FULL, "mastering display via ffmpeg; MaxCLL/MaxFALL in MXF picture descriptors")
FEATURES.declare("output:sphericalMetadata", FULL, "Spherical Video V2: st3d + sv3d (equi/cbmp) boxes in mp4/mov, "
                 "Matroska/WebM Projection + StereoMode; mono, top-bottom, left-right")
FEATURES.declare("output:sphericalMetadata:mesh", FULL, "eac / fisheye-180: Spherical V2 mesh projection (mshp, dfl8, "
                 "16x16 quads per EAC cell, 16 x 64 fisheye hemisphere grid) in sv3d for mp4/mov, Matroska/WebM "
                 "Projection type 3; one per-eye mesh, frame packing via st3d / StereoMode")
FEATURES.declare("output:embedMetadata", FULL)
FEATURES.declare("output:posters", FULL)
FEATURES.declare("output:destination:file", FULL, "copy")
try:
    from .publish import LEVELS as _PUB
except ImportError:          # pragma: no cover
    _PUB = {}
for _k in ("s3", "gcs", "azure-blob", "http-put", "sftp", "webhook"):
    _lv, _note = _PUB.get(_k, (NONE, "no uploader"))
    FEATURES.declare(f"output:destination:{_k}", _lv, f"with --publish only (else skipped): {_note}")

SEQUENCES = {"png-sequence": ".png", "jpeg-sequence": ".jpg", "tiff-sequence": ".tif", "exr-sequence": ".exr"}
_EXT_CODEC = {".mp4": ("h264", "mp4"), ".m4v": ("h264", "mp4"), ".mov": ("h264", "mov"), ".mkv": ("h264", "mkv"),
              ".webm": ("vp9", "webm"), ".mxf": ("dnxhr", "mxf"), ".gif": ("gif", None), ".apng": ("apng", None),
              ".webp": ("webp", None), ".png": ("png-sequence", None), ".jpg": ("jpeg-sequence", None),
              ".jpeg": ("jpeg-sequence", None), ".tif": ("tiff-sequence", None), ".tiff": ("tiff-sequence", None),
              ".exr": ("exr-sequence", None), ".wav": ("audio-only", "wav"), ".m4a": ("audio-only", "m4a"),
              ".mp3": ("audio-only", "mp3"), ".flac": ("audio-only", "flac"), ".aac": ("audio-only", "m4a"),
              ".ogg": ("audio-only", "ogg"), ".opus": ("audio-only", "ogg"),
              ".mka": ("audio-only", "mka")}
_FORMATS = {"mp4": "mp4", "mov": "mov", "mkv": "matroska", "webm": "webm", "mxf": "mxf", "wav": "wav",
            "m4a": "ipod", "mp3": "mp3", "flac": "flac", "ogg": "ogg", "mka": "matroska"}
_PRORES = {"proxy": 0, "lt": 1, "422": 2, "hq": 3, "4444": 4, "4444xq": 5}
_AV1_SPEED = {"ultrafast": 8, "superfast": 8, "veryfast": 7, "faster": 7, "fast": 6, "medium": 6, "slow": 4,
              "slower": 3, "veryslow": 2, "placebo": 1}
_VP9_SPEED = {"ultrafast": 8, "superfast": 7, "veryfast": 6, "faster": 5, "fast": 4, "medium": 3, "slow": 2,
              "slower": 1, "veryslow": 0, "placebo": 0}


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def gpu_enabled() -> bool:
    """GPU paths are used whenever they work, unless SCENERENDER_GPU=0 (render --no-gpu)."""
    return os.environ.get("SCENERENDER_GPU", "1") != "0"


_NVENC: dict = {}


def nvenc_ffmpeg(codec: str = "h264_nvenc") -> str | None:
    """An ffmpeg that can encode with NVENC on this machine (SCENERENDER_FFMPEG, the bundled build or
    PATH's), found by a short test encode; None without a usable NVIDIA encoder. Probed once."""
    if codec in _NVENC:
        return _NVENC[codec]
    candidates = [os.environ.get("SCENERENDER_FFMPEG"), ffmpeg_exe(), shutil.which("ffmpeg")]
    candidates = list(dict.fromkeys(c for c in candidates if c))
    key = _probe_key(codec, candidates)
    known = _probe_cache().get(key) if key else None
    if known is not None:
        _NVENC[codec] = known or None
        return _NVENC[codec]
    found = None
    for exe in candidates:
        try:
            r = subprocess.run([exe, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=black:s=256x256:r=24:d=0.25",
                                "-c:v", codec, "-f", "null", "-"], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            found = exe
            break
    _NVENC[codec] = found
    if key:
        _probe_cache(key, found or "")
    return found


def _probe_key(codec: str, exes: list[str]) -> str | None:
    """What an NVENC probe result depends on: the ffmpeg builds (path, size, mtime), the codec and the
    NVIDIA driver loaded (none: no key, always probe)."""
    try:
        with open("/proc/driver/nvidia/version") as f:
            driver = f.readline().strip()
        builds = [f"{e}:{os.stat(e).st_size}:{os.stat(e).st_mtime_ns}" for e in exes]
    except OSError:
        return None
    return "|".join([codec, driver, *builds])


def _probe_cache(key: str | None = None, value: str | None = None) -> dict:
    """NVENC probe results kept across runs in the user cache directory (a test encode costs about a
    second of CPU); with key and value, records one."""
    import json
    path = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
                        "scenerender", "nvenc-probe.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if key is not None:
        data[key] = value
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except OSError:
            pass
    return data


# NVENC rejects small frames (the minimum depends on the GPU); below this x264 encodes them.
_NVENC_MIN = (256, 128)


def gpu_encoder(job: Job, two_pass: bool = False, dims: tuple[int, int] | None = None) -> str | None:
    """The ffmpeg to encode job on the GPU with, when that applies (single-pass H.264 of frames at
    least _NVENC_MIN)."""
    if job.codec != "h264" or two_pass or not gpu_enabled() or job.a("encoder") == "cpu":
        return None
    if dims is not None and (dims[0] < _NVENC_MIN[0] or dims[1] < _NVENC_MIN[1]):
        return None
    return nvenc_ffmpeg("h264_nvenc")


# ====================================================================== jobs
@dataclass
class Job:
    name: str
    path: str
    codec: str
    container: str | None = None
    el: object = None
    width: int | None = None          # final pixel size (None = renderer size)
    height: int | None = None
    fps: Fraction = Fraction(30)
    t0: float = 0.0
    t1: float = 0.0
    open_kwargs: dict = field(default_factory=dict)
    alpha: bool = False
    audio: bool = True
    attrs: dict = field(default_factory=dict)
    color: tuple = (None, None)
    burn: str | None = None
    captions: list | None = None
    ch_layout: str | None = None      # ffmpeg channel layout of an ambisonic mix ("ambisonic 1"), set by _audio_file

    def a(self, name, default=None):
        v = self.attrs.get(name)
        return default if v is None else v


def _base_open_kwargs(args) -> dict:
    from .cli import _params
    return dict(path=args.scene, scale=float(getattr(args, "scale", 1.0) or 1.0), params=_params(args),
                variant=getattr(args, "variant", None), layout=getattr(args, "layout", None),
                strict=bool(getattr(args, "strict", False)), representation=getattr(args, "representation", None),
                assets_dir=getattr(args, "assets_dir", None),
                motion_blur=False if getattr(args, "no_motion_blur", False) else None)


def _codec_for_path(path: str) -> tuple[str, str | None, str]:
    """-> (codec, container, path-or-pattern)"""
    if "%" in path:
        ext = os.path.splitext(path)[1].lower()
        codec = _EXT_CODEC.get(ext, ("png-sequence", None))[0]
        return (codec if codec in SEQUENCES else "png-sequence"), None, path
    ext = os.path.splitext(path)[1].lower()
    if path.endswith(os.sep) or os.path.isdir(path) or not ext:
        return "png-sequence", None, os.path.join(path, "frame_%06d.png")
    if ext not in _EXT_CODEC:
        raise SystemExit(f"cannot tell the codec from {path!r}; use .mp4/.mov/.mkv/.webm/.gif/.png/...")
    codec, container = _EXT_CODEC[ext]
    if codec in SEQUENCES:
        stem = os.path.splitext(path)[0]
        return codec, None, f"{stem}_%06d{ext}"
    return codec, container, path


def jobs_from_args(renderer, args) -> list[Job]:
    doc = renderer.doc
    base = _base_open_kwargs(args)
    fps_cli = Fraction(args.fps).limit_denominator(1001) if getattr(args, "fps", None) else None
    if getattr(args, "out", None):
        codec, container, path = _codec_for_path(args.out)
        fps = fps_cli or doc.fps
        t0 = args.t0 if args.t0 is not None else 0.0
        t1 = args.t1 if args.t1 is not None else doc.duration
        return [Job(name=os.path.basename(args.out.rstrip(os.sep)) or args.out, path=path, codec=codec, container=container, fps=fps,
                    t0=t0, t1=t1, open_kwargs=base, attrs={"crf": args.crf} if args.crf is not None else {})]
    outs = [o for o in doc.root.findall("output")]
    if not outs:
        return []
    wanted = set(getattr(args, "output", None) or [])
    jobs = []
    for i, o in enumerate(outs):
        oid = o.get("id") or f"output-{i + 1}"
        if wanted and oid not in wanted:
            continue
        attrs = dict(o.attrib)
        if args.crf is not None:
            attrs["crf"] = args.crf
        layout = getattr(args, "layout", None) or o.get("layout")
        variant = getattr(args, "variant", None) or o.get("variant")
        lay = doc.ids.get(layout) if layout else None
        if lay is not None:
            bw, bh = int(lay.get("width")), int(lay.get("height"))
        else:
            bw, bh = int(doc.project.get("width")), int(doc.project.get("height"))
        ow, oh = o.get("width"), o.get("height")
        if ow:
            s = int(ow) / bw
        elif oh:
            s = int(oh) / bh
        else:
            s = 1.0
        cli_scale = float(getattr(args, "scale", 1.0) or 1.0)
        W = round(int(ow) * cli_scale) if ow else round(bw * s * cli_scale)
        H = round(int(oh) * cli_scale) if oh else round(bh * s * cli_scale)
        kw = dict(base, scale=s * cli_scale, layout=layout, variant=variant,
                  representation=getattr(args, "representation", None) or o.get("representation"))
        fps = fps_cli or (parse_fps(o.get("fps")) if o.get("fps") else doc.fps)
        t0 = args.t0 if args.t0 is not None else float(o.get("start", 0) or 0)
        t1 = args.t1 if args.t1 is not None else (float(o.get("end")) if o.get("end") else doc.duration)
        codec = o.get("codec")
        path = o.get("path")
        if codec in SEQUENCES and "%" not in path:
            ext = os.path.splitext(path)[1]
            path = os.path.join(path, "frame_%06d" + SEQUENCES[codec]) if not ext else f"{os.path.splitext(path)[0]}_%06d{ext}"
        container = o.get("container") or _EXT_CODEC.get(os.path.splitext(path)[1].lower(), (None, None))[1]
        caps = o.get("captions").split() if o.get("captions") else None
        jobs.append(Job(name=oid, path=path, codec=codec, container=container, el=o, width=W, height=H, fps=fps,
                        t0=t0, t1=t1, open_kwargs=kw, alpha=parse_bool(o.get("alpha")),
                        audio=parse_bool(o.get("audio", "true"), True), attrs=attrs,
                        color=(o.get("colorSpace"), o.get("transfer")), burn=o.get("burnCaptions"), captions=caps))
    return jobs


# ====================================================================== renderers
def open_renderer(open_kwargs: dict, cache: dict | None = None):
    from .render import Renderer
    kw = dict(open_kwargs)
    path = kw.pop("path")
    r = Renderer.open(path, **kw)
    if cache:
        r.rc.cache.update(cache)
    return r


def hdr_target(job: Job) -> dict | None:
    """Target display for PQ/HLG: masteringDisplay L(max,min) (0.0001 cd/m2), else maxCLL, else None (1000)."""
    md = job.a("masteringDisplay")
    if md:
        try:
            L = parse_master_display(str(md))["L"]
            return {"peak": L[0] / 10000.0, "black": L[1] / 10000.0}
        except ValueError as e:
            warn_once("output", f"masteringDisplay-{job.name}", str(e))
    if job.a("maxCLL"):
        return {"peak": float(job.a("maxCLL")), "black": 0.0}
    return None


def _job_cache(job: Job) -> dict:
    c = {}
    if job.color != (None, None):
        c["output_color"] = job.color
    hdr = hdr_target(job)
    if hdr:
        c["output_hdr"] = hdr
    if job.burn:
        c["burn_captions"] = job.burn
    return c


# ====================================================================== frames
def _frame_kind(job: Job) -> str:
    if job.codec == "exr-sequence":
        return "floata" if job.alpha else "float"
    if job.codec == "tiff-sequence":
        return "rgba16" if job.alpha else "rgb16"
    pf = str(job.a("pixelFormat", "") or "")
    deep = bool(re.search(r"(10|12|16)(le|be)?$", pf)) or job.codec == "prores" and job.alpha
    if job.codec == "prores" or deep and job.codec in ("h264", "h265", "av1", "vp9", "ffv1", "dnxhr"):
        return "rgba16" if job.alpha else "rgb16"
    return "rgba8" if job.alpha else "rgb8"


def _graded_background(r):
    bg = r.background()
    if bg is None:
        bg = (0.0, 0.0, 0.0, 1.0)
    try:
        g = color.grade_color(r.rc, bg[:3])
        return tuple(g) + (bg[3],)
    except Exception:  # noqa: BLE001
        return bg


def render_frame(r, t: float, kind: str, size: tuple[int, int] | None, pad_even: bool) -> np.ndarray:
    return begin_frame(r, t, kind, size, pad_even)()


def begin_frame(r, t: float, kind: str, size: tuple[int, int] | None, pad_even: bool):
    """render_frame, started: returns a function that gives the image. A frame composited on the GPU
    is converted there and read back while the caller goes on with the next frame (see FrameSource),
    so collecting it does not wait for the GPU."""
    from .raster import linear_to_srgb, working_to_rgb8
    frame = int(round(t * float(r.doc.fps)))
    buf = r.frame_buf(t, frame)
    lin = r.rc.linear
    if kind == "rgb8" and buf.gpu is not None:
        from . import gpucomp
        collect = gpucomp.to_rgb8_async(buf, lin, _graded_background(r))    # 3 bytes a pixel leave the GPU
        return lambda: _fit(collect(), size, pad_even)
    if kind == "rgb8":
        img = working_to_rgb8(buf.px, lin, _graded_background(r))
    else:
        px = buf.px
        if kind == "rgba8":
            rgb = working_to_rgb8(px, lin)
            a = (np.clip(px[..., 3:4], 0, 1) * 255 + 0.5).astype(np.uint8)
            img = np.concatenate([rgb, a], -1)
        else:
            a = px[..., 3:4]
            if kind in ("rgb16", "rgba16"):
                if kind == "rgb16":
                    bg = np.asarray(_graded_background(r)[:3], np.float32)
                    rgb = px[..., :3] + bg * (1 - a)
                    straight = rgb
                else:
                    straight = np.where(a > 1e-6, px[..., :3] / np.maximum(a, 1e-6), 0)
                if lin:
                    straight = linear_to_srgb(straight)
                img = (np.clip(straight, 0, 1) * 65535 + 0.5).astype(np.uint16)
                if kind == "rgba16":
                    img = np.concatenate([img, (np.clip(a, 0, 1) * 65535 + 0.5).astype(np.uint16)], -1)
            else:   # float: linear light; floata = premultiplied RGBA, float = RGB over the background
                from .raster import srgb_to_linear
                rgb = px[..., :3] if lin else srgb_to_linear(np.where(a > 1e-6, px[..., :3] / np.maximum(a, 1e-6), 0)) * a
                if kind == "floata":
                    img = np.concatenate([rgb, a], -1).astype(np.float32)
                else:
                    bg = np.asarray(_graded_background(r)[:3], np.float32)
                    if not lin:
                        bg = srgb_to_linear(bg)
                    img = (rgb + bg * (1 - a)).astype(np.float32)
    img = _fit(img, size, pad_even)
    return lambda: img


def _fit(img: np.ndarray, size: tuple[int, int] | None, pad_even: bool) -> np.ndarray:
    """img at the output size, padded to even dimensions when pad_even."""
    if size and (img.shape[1], img.shape[0]) != size:
        img = _resize(img, size)
    if pad_even and (img.shape[0] % 2 or img.shape[1] % 2):
        img = np.pad(img, ((0, img.shape[0] % 2), (0, img.shape[1] % 2), (0, 0)), mode="edge")
    return np.ascontiguousarray(img)


def _resize(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    from PIL import Image
    w, h = size
    if img.dtype == np.uint8:
        return np.asarray(Image.fromarray(img).resize((w, h), Image.LANCZOS))
    chans = [np.asarray(Image.fromarray(img[..., c].astype(np.float32), "F").resize((w, h), Image.LANCZOS))
             for c in range(img.shape[2])]
    out = np.stack(chans, -1)
    if img.dtype == np.uint16:
        out = np.clip(out, 0, 65535).astype(np.uint16)
    return out.astype(img.dtype)


# --------------------------------------------------------------- worker pool
_W: dict = {}


def _winit(open_kwargs, cache, envelopes, kind, size, pad_even, threads=None):
    logging.getLogger("scenerender").setLevel(logging.ERROR)
    # A frame worker renders its frames' shutter samples itself: sample worker processes would reload
    # the document in every one of them and lose the reuse of unchanged nodes across samples.
    os.environ["SCENERENDER_FRAME_WORKER"] = "1"
    if threads:
        os.environ["SCENERENDER_THREADS"] = str(threads)
    try:        # never share a parent's live ffmpeg decoder pipes (matters if the pool ever forks)
        from .assets import video as _video
        if hasattr(_video, "_close_all"):
            _video._close_all()
    except ImportError:
        pass
    r = open_renderer(open_kwargs, cache)
    if envelopes:
        from .audio import install_envelopes
        install_envelopes(r.rc, envelopes)
    _W.update(r=r, kind=kind, size=size, pad=pad_even)


def _wframe(t: float):
    img = render_frame(_W["r"], t, _W["kind"], _W["size"], _W["pad"])
    return img.shape, img.dtype.str, img.tobytes()


def _wframes(ts: list[float]) -> list:
    return [_wframe(t) for t in ts]


def _bounded_results(pool, runs, ahead: int):
    """Frames of runs in order, keeping at most `ahead` runs submitted beyond the one being consumed,
    so frames finished out of order cannot pile up in this process."""
    from collections import deque
    pending, it = deque(), iter(runs)
    for run in it:
        pending.append(pool.submit(_wframes, run))
        if len(pending) >= ahead:
            break
    while pending:
        frames = pending.popleft().result()
        nxt = next(it, None)
        if nxt is not None:
            pending.append(pool.submit(_wframes, nxt))
        yield from frames


def _fit_frame_workers(procs: int) -> int:
    """procs, reduced so that many renderers fit in 60% of the memory still available (host or
    cgroup); each is budgeted twice this process's peak so far (it has just rendered a frame)."""
    from .render import _mem_available, _rss
    # The first frame understates later ones (3D shots load models, textures and environments):
    # budget twice its peak per renderer.
    per = max(2 * _rss("self", peak=True), 1 << 30)
    fit = int(_mem_available() * 0.6 // per)
    if fit < procs:
        log.info("frame workers: %d fit in memory (%.1f GB peak per renderer)", max(fit, 1), per / 2 ** 30)
    return max(1, min(procs, fit))


def _runs(n: int, procs: int, longest: int = 48, shortest: int = 4, times=None) -> list[tuple[int, int]]:
    """Guided runs of consecutive frames: long ones first (a worker reuses the previous frame's
    effect results, noise lattices and raster-cache windows within a run), shrinking so every worker
    finishes together. With the frame times, a run ends on a raster-cache window boundary (the nearer
    one), so no window is drawn by two workers."""
    from .compositor import RASTER_WINDOW
    runs, i = [], 0
    while i < n:
        size = max(shortest, min(longest, (n - i) // (procs * 2)))
        j = min(n, i + size)
        if times is not None and j < n:
            w = math.floor(times[j] / RASTER_WINDOW)
            start = j
            while start > i and math.floor(times[start - 1] / RASTER_WINDOW) == w:
                start -= 1
            end = j
            while end < n and math.floor(times[end] / RASTER_WINDOW) == w:
                end += 1
            if start > i and (j - start) <= (end - j):
                j = start
            else:
                j = end
        runs.append((i, j))
        i = j
    return runs


class FrameSource:
    """Frames for times[i] in order: from --frames-dir when present, else rendered (maybe in parallel).
    `tap(img)` (QA flash analysis) sees every frame as it streams."""

    tap = None

    def __init__(self, r, job: Job, times: list[float], kind: str, size, pad_even: bool, jobs: int,
                 frames_dir: str | None, first_index: int):
        self.r, self.job, self.times, self.kind = r, job, times, kind
        self.size, self.pad, self.jobs = size, pad_even, jobs
        self.dir = frames_dir
        self.first = first_index
        self.dims = _frame_dims(r, size, pad_even)
        if self.dir:
            os.makedirs(self.dir, exist_ok=True)

    def _fpath(self, i: int) -> str:
        ext = ".png" if self.kind in ("rgb8", "rgba8") else ".npy"
        return os.path.join(self.dir, f"frame_{self.first + i:06d}{ext}")

    def _load(self, i: int):
        if not self.dir:
            return None
        p = self._fpath(i)
        if not os.path.exists(p):
            return None
        try:
            if p.endswith(".npy"):
                return np.load(p)
            from PIL import Image
            return np.asarray(Image.open(p).convert("RGBA" if self.kind == "rgba8" else "RGB"))
        except Exception:  # noqa: BLE001 — a damaged frame is re-rendered
            return None

    def _save(self, i: int, img: np.ndarray) -> None:
        if not self.dir:
            return
        p = self._fpath(i)
        tmp = p + ".tmp" + os.path.splitext(p)[1]
        if p.endswith(".npy"):
            with open(tmp, "wb") as f:
                np.save(f, img)
        else:
            from PIL import Image
            Image.fromarray(img).save(tmp, format="PNG", compress_level=1)
        os.replace(tmp, p)

    def __iter__(self):
        n = len(self.times)
        cached = {}
        missing = []
        for i in range(n):
            img = self._load(i)
            if img is not None and img.shape[:2] == self.dims[::-1]:
                cached[i] = img
            else:
                missing.append(i)
        if cached:
            log.info("%s: reusing %d frames from %s", self.job.name, len(cached), self.dir)
        workers = min(self.jobs, len(missing) // 6) if self.jobs > 1 else 1
        pool = None
        results = None
        if workers > 1 and missing:
            first = missing[0]
            rest = missing[1:]
            # Render the first missing frame here before any worker starts: it surfaces warnings once,
            # and its peak memory sizes the pool (every worker holds a full renderer of this document).
            cached[first] = render_frame(self.r, self.times[first], self.kind, self.size, self.pad)
            self._save(first, cached[first])
            procs = _fit_frame_workers(min(workers, len(rest)))
        if workers > 1 and missing and procs > 1:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            envelopes = {}
            try:
                from .audio import envelope_table
                envelopes = envelope_table(self.r)
            except Exception as e:  # noqa: BLE001
                log.debug("no envelope table: %s", e)
            if hasattr(self.r, "close_sample_pool"):
                self.r.close_sample_pool()     # frame workers now use the CPUs
            from . import threads
            # An executor, not multiprocessing.Pool: a worker that dies (e.g. killed out of memory)
            # raises BrokenProcessPool here instead of leaving the render waiting forever.
            pool = ProcessPoolExecutor(procs, mp_context=mp.get_context("spawn"), initializer=_winit,
                                       initargs=(self.job.open_kwargs, _job_cache(self.job), envelopes, self.kind,
                                                 self.size, self.pad, max(1, threads() // procs)))
            runs = [[self.times[i] for i in rest[a:b]]
                    for a, b in _runs(len(rest), procs, times=[self.times[i] for i in rest])]
            results = _bounded_results(pool, runs, procs + 2)
            rest_set = set(rest)
        else:
            rest_set = set()
        # Frames rendered here are pipelined one deep: frame i + 1 starts before frame i is collected.
        serial = _pipelined(self, [i for i in range(n) if i not in cached and i not in rest_set])
        try:
            for i in range(n):
                if i in cached:
                    img = cached.pop(i)
                elif i in rest_set:
                    try:
                        shape, dt, raw = next(results)
                    except BrokenProcessPool as e:
                        raise RuntimeError(f"{self.job.name}: a frame worker process died (out of memory?); "
                                           "rerun with fewer --jobs") from e
                    img = np.frombuffer(raw, np.dtype(dt)).reshape(shape)
                    self._save(i, img)
                else:
                    img = next(serial)
                    self._save(i, img)
                if self.tap is not None:
                    self.tap(img)
                yield img
        finally:
            if pool is not None:
                for proc in list(getattr(pool, "_processes", {}).values()):
                    proc.terminate()
                pool.shutdown(wait=True, cancel_futures=True)


def _pipelined(src: "FrameSource", idx: list[int]):
    """The images of frames idx of src in order, each started before the previous one is collected."""
    pending = None
    for i in idx:
        started = begin_frame(src.r, src.times[i], src.kind, src.size, src.pad)
        if pending is not None:
            yield pending()
        pending = started
    if pending is not None:
        yield pending()


class Progress:
    def __init__(self, name: str, total: int):
        self.name, self.total = name, total
        self.t0 = time.perf_counter()
        self.last = self.t0
        self.tty = sys.stderr.isatty()

    def step(self, i: int) -> None:
        now = time.perf_counter()
        if i < self.total and now - self.last < (0.25 if self.tty else 5.0):
            return
        self.last = now
        el = now - self.t0
        rate = i / el if el > 0 else 0.0
        eta = (self.total - i) / rate if rate > 0 else 0.0
        msg = f"{self.name}: {i}/{self.total} frames  {rate:.1f} fps  ETA {eta:.0f}s"
        sys.stderr.write(("\r" + msg + " " * 8) if self.tty else msg + "\n")
        if i >= self.total and self.tty:
            sys.stderr.write("\n")
        sys.stderr.flush()


# ====================================================================== ffmpeg arguments
def _fps_str(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}"


_PRIM = {"srgb": "bt709", "linear-srgb": "bt709", "rec709": "bt709", "display-p3": "smpte432",
         "dci-p3": "smpte431", "rec2020": "bt2020"}
_MATRIX = {"rec2020": "bt2020nc"}
_TRC = {"srgb": "iec61966-2-1", "linear": "linear", "bt1886": "bt709", "gamma22": "gamma22", "pq": "smpte2084",
        "hlg": "arib-std-b67"}


def color_args(job: Job, yuv: bool) -> tuple[list[str], str | None]:
    """ffmpeg colour tags and the RGB->YUV conversion filter."""
    space = job.color[0] or "srgb"
    tf = job.color[1] or "auto"
    if tf == "auto":
        from .color import SPACES
        tf = SPACES.get(space, (None, "srgb"))[1]
    prim = _PRIM.get(space)
    trc = _TRC.get(tf)
    full = job.a("colorRange", "limited") == "full"
    out: list[str] = []
    if prim:
        out += ["-color_primaries", prim]
    elif space not in ("srgb",):
        warn_once("output-color", space, "no container tag for this colour space; pixels converted, stream untagged")
    if trc:
        out += ["-color_trc", trc]
    vf = None
    if yuv:
        mtx = _MATRIX.get(space, "bt709")
        out += ["-colorspace", mtx, "-color_range", "pc" if full else "tv"]
        vf = f"scale=out_color_matrix={'bt2020' if mtx.startswith('bt2020') else 'bt709'}:out_range={'full' if full else 'tv'}:flags=accurate_rnd+full_chroma_int"
    return out, vf


def audio_bitrate(job: Job, duration: float) -> int:
    """audioBitrate, reduced under maxFileSize so audio takes at most a quarter of the budget (>= 32 kb/s)."""
    br = int(job.a("audioBitrate", 192000))
    if job.a("maxFileSize"):
        total = int(job.a("maxFileSize")) * 8 * 0.97 / max(duration, 1e-3)
        br = min(br, max(32000, int(total * 0.25)))
    return br


def size_budget_bitrate(job: Job, duration: float, audio_bps: int) -> int:
    """Video bitrate that fits maxFileSize: 0.97 x size x 8 / duration - audio bitrate (>= 50 kb/s)."""
    total = int(job.a("maxFileSize")) * 8 * 0.97 / max(duration, 1e-3)
    return max(50_000, int(total - (audio_bps if job.audio else 0)))


def _rate_args(job: Job, codec: str, duration: float, audio_bps: int, force_bitrate: int | None = None) -> tuple[list[str], bool]:
    """Rate control. Returns (args, bitrate_mode). force_bitrate: the maxFileSize loop's corrected bitrate."""
    crf = int(job.a("crf", 18))
    br = job.a("bitrate")
    maxbr = job.a("maxBitrate")
    buf = job.a("bufferSize")
    if job.a("maxFileSize"):
        v = force_bitrate or size_budget_bitrate(job, duration, audio_bps)
        br = min(int(br), v) if br else v
        maxbr = min(int(maxbr), v) if maxbr else v
        buf = min(int(buf), 2 * v) if buf else 2 * v
    a: list[str] = []
    if br:
        a += ["-b:v", str(int(br))]
        if maxbr:
            a += ["-maxrate", str(int(maxbr))]
        if buf:
            a += ["-bufsize", str(int(buf))]
        return a, True
    if codec in ("h264", "h265"):
        a += ["-crf", str(crf)]
    elif codec in ("av1", "vp9"):
        a += ["-crf", str(min(63, crf)), "-b:v", "0"]
    if maxbr:
        a += ["-maxrate", str(int(maxbr)), "-bufsize", str(int(buf or 2 * int(maxbr)))]
    return a, False


def video_codec_args(job: Job, duration: float, audio_bps: int, force_bitrate: int | None = None,
                     webp_quality: int | None = None, gpu: bool = False) -> tuple[list[str], str | None, bool, list[str]]:
    """-> (output args, -vf filter, bitrate_mode, extra filters for filter_complex)

    gpu: encode H.264 with NVENC (see gpu_encoder); crf becomes its constant-quality target."""
    c = job.codec
    fps = float(job.fps)
    preset = str(job.a("preset", "medium"))
    pf = job.a("pixelFormat")
    gop = max(1, int(round(float(job.a("keyframeInterval", 2)) * fps)))
    a: list[str] = []
    yuv = c in ("h264", "h265", "av1", "vp9", "prores", "dnxhr", "webp") or (c == "ffv1" and not (pf or "").startswith(("rgb", "bgr", "gbr")))
    tags, vf = color_args(job, yuv)
    bitrate_mode = False
    if job.alpha and c in ("h264", "h265", "av1", "dnxhr"):
        warn_once("output", f"alpha-{c}", f"{c} has no alpha channel; rendered opaque")
    if c == "h264" and gpu:
        # p6 + hq tuning with lookahead and spatial AQ: NVENC's high-quality settings, at 300+ fps.
        a += ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc-lookahead", "32", "-spatial-aq", "1",
              "-pix_fmt", pf or "yuv420p"]
        ra, bitrate_mode = _rate_args(job, c, duration, audio_bps, force_bitrate)
        if "-crf" in ra:
            i = ra.index("-crf")
            ra[i:i + 2] = ["-rc", "vbr", "-cq", ra[i + 1], "-b:v", "0"]
        a += ra
        if job.a("profile"):
            a += ["-profile:v", job.a("profile")]
        if job.a("level"):
            a += ["-level:v", job.a("level")]
        a += ["-g", str(gop)]
        if job.a("bFrames") is not None:
            a += ["-bf", str(job.a("bFrames"))]
    elif c == "h264":
        a += ["-c:v", "libx264", "-preset", preset, "-pix_fmt", pf or "yuv420p"]
        ra, bitrate_mode = _rate_args(job, c, duration, audio_bps, force_bitrate)
        a += ra
        if job.a("profile"):
            a += ["-profile:v", job.a("profile")]
        if job.a("level"):
            a += ["-level:v", job.a("level")]
        a += ["-g", str(gop)]
        if job.a("bFrames") is not None:
            a += ["-bf", str(job.a("bFrames"))]
    elif c == "h265":
        a += ["-c:v", "libx265", "-preset", preset, "-pix_fmt", pf or "yuv420p"]
        ra, bitrate_mode = _rate_args(job, c, duration, audio_bps, force_bitrate)
        a += ra
        xp = ["log-level=error", f"keyint={gop}"]
        if job.a("bFrames") is not None:
            xp.append(f"bframes={job.a('bFrames')}")
        if job.a("maxCLL") or job.a("maxFALL"):
            xp.append(f"max-cll={job.a('maxCLL', 0)},{job.a('maxFALL', 0)}")
        if job.a("masteringDisplay"):
            xp.append(f"master-display={job.a('masteringDisplay')}")
            xp.append("hdr10=1")
        if job.a("profile"):
            a += ["-profile:v", job.a("profile")]
        a += ["-x265-params", ":".join(xp)]
        if (job.container or "") in ("mp4", "mov", ""):
            a += ["-tag:v", "hvc1"]
    elif c == "av1":
        a += ["-c:v", "libaom-av1", "-cpu-used", str(_AV1_SPEED.get(preset, 6)), "-row-mt", "1",
              "-pix_fmt", pf or "yuv420p", "-g", str(gop)]
        ra, bitrate_mode = _rate_args(job, c, duration, audio_bps, force_bitrate)
        a += ra
    elif c == "vp9":
        a += ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", str(_VP9_SPEED.get(preset, 3)), "-row-mt", "1",
              "-pix_fmt", ("yuva420p" if job.alpha else (pf or "yuv420p")), "-g", str(gop)]
        ra, bitrate_mode = _rate_args(job, c, duration, audio_bps, force_bitrate)
        a += ra
    elif c == "prores":
        prof = job.a("proresProfile") or ("4444" if job.alpha else "hq")
        a += ["-c:v", "prores_ks", "-profile:v", str(_PRORES.get(prof, 3)), "-vendor", "apl0",
              "-pix_fmt", "yuva444p10le" if (job.alpha or prof in ("4444", "4444xq")) else "yuv422p10le"]
    elif c == "dnxhr":
        prof = job.a("profile") or "dnxhr_hq"
        pix = {"dnxhr_hqx": "yuv422p10le", "dnxhr_444": "yuv444p10le"}.get(prof, "yuv422p")
        a += ["-c:v", "dnxhd", "-profile:v", prof, "-pix_fmt", pix]
    elif c == "ffv1":
        pix = ("yuva420p" if job.alpha else pf or "yuv420p")
        a += ["-c:v", "ffv1", "-level", "3", "-g", "1", "-pix_fmt", pix]
    elif c == "gif":
        loop = int(job.a("loopCount", 0))
        a += ["-loop", str(loop)]
        tags, vf = [], None
    elif c == "apng":
        a += ["-c:v", "apng", "-plays", str(int(job.a("loopCount", 0))), "-pix_fmt", "rgba" if job.alpha else "rgb24", "-f", "apng"]
        vf = None
    elif c == "webp":
        q = webp_quality if webp_quality is not None else max(0, min(100, 100 - 2 * int(job.a("crf", 18)) + 16))
        a += ["-c:v", "libwebp_anim", "-lossless", "0", "-quality", str(q), "-loop", str(int(job.a("loopCount", 0))),
              "-pix_fmt", "yuva420p" if job.alpha else "yuv420p", "-f", "webp"]
        tags, vf = [], None
    else:
        raise ValueError(f"not a video codec: {c}")
    return a + tags, vf, bitrate_mode, []


def audio_codec_args(job: Job, bits: int, duration: float = 0.0) -> list[str]:
    ac = str(job.a("audioCodec", "aac"))
    br = audio_bitrate(job, duration) if duration else int(job.a("audioBitrate", 192000))
    cont = job.container or ""
    if job.codec == "audio-only":
        cont = cont or os.path.splitext(job.path)[1].lstrip(".").lower()
        if cont == "wav":
            return ["-c:a", {16: "pcm_s16le", 32: "pcm_s32le"}.get(bits, "pcm_s24le")]
        if cont == "flac":
            return ["-c:a", "flac"]
        if cont == "mp3":
            return ["-c:a", "libmp3lame", "-b:a", str(br)]
        if cont == "ogg":
            return ["-c:a", "libopus", "-b:a", str(br)] + (["-mapping_family", "2"] if job.ch_layout else [])
    name = {"aac": "aac", "opus": "libopus", "mp3": "libmp3lame", "flac": "flac", "pcm": "pcm_s24le",
            "vorbis": "libvorbis"}.get(ac, ac)
    if cont == "webm" and name not in ("libopus", "libvorbis"):
        warn_once("output", "webm-audio", f"{ac} is not allowed in WebM; using Opus")
        name = "libopus"
    if cont == "mxf" and not name.startswith("pcm"):
        name = "pcm_s24le"
    if job.ch_layout and not name.startswith("pcm") and name != "flac":
        # ambisonics: only Opus (channel mapping family 2, ambisonic ACN/SN3D) carries it losslessly-ordered;
        # AAC/MP3/Vorbis would re-map the channels as speakers
        if name != "libopus":
            warn_once("output", f"ambisonic-{ac}", f"{ac} cannot carry ambisonics; using Opus (mapping family 2)")
        name = "libopus"
        if cont in ("mov", "mxf", "m4a", "mp3"):
            warn_once("output", f"ambisonic-{cont}", f"the {cont} container cannot hold Opus ambisonics; use mkv/mka/webm/mp4/ogg")
    out = ["-c:a", name]
    if not name.startswith("pcm") and name != "flac":
        out += ["-b:a", str(br)]
    if name == "libopus" and job.ch_layout:
        out += ["-mapping_family", "2"]
    return out


def metadata_args(doc, job: Job) -> list[str]:
    if not parse_bool(job.a("embedMetadata", "true"), True):
        return []
    md = doc.section("metadata")
    if md is None:
        return []
    out = []
    keymap = {"title": ["title"], "author": ["artist", "author"], "description": ["description", "comment"],
              "copyright": ["copyright"], "keywords": ["keywords"], "created": ["creation_time", "date"],
              "generator": ["encoder_generator"], "revision": ["revision"], "language": ["language"],
              "modified": ["modification_time"]}
    for attr, keys in keymap.items():
        v = md.get(attr)
        if v:
            for k in keys:
                out += ["-metadata", f"{k}={v}"]
    for m in md:
        if isinstance(m.tag, str) and m.tag == "meta" and m.get("name"):
            out += ["-metadata", f"{m.get('name')}={m.get('value', '')}"]
    return out


# ====================================================================== running a job
def _audio_file(r, job: Job, tmpdir: str, n_frames: int) -> tuple[str | None, int]:
    if not job.audio or job.codec in ("gif", "apng", "webp"):
        return None, 24
    try:
        from .audio import mixer_for, write_wav
    except ImportError:
        return None, 24
    if r.doc.section("audioMix") is None and not any(
            (r.doc.ids.get(l.get("asset")) is not None and parse_bool(r.doc.ids.get(l.get("asset")).get("hasAudio")))
            for l in r.doc.root.iter("layer")):
        return None, 24
    m = mixer_for(r)
    if not m.has_audio():
        return None, m.bits
    t1 = job.t0 + n_frames / float(job.fps) if job.codec != "audio-only" else job.t1
    t0p = time.perf_counter()
    pcm = m.render(job.t0, t1)
    dither = (parse_bool(m.master_el.get("dither", "true"), True)) if m.master_el is not None else True
    path = os.path.join(tmpdir, "mix.wav")
    lay = getattr(m, "layout", None)
    if lay is not None:
        write_wav(path, pcm, m.sr, m.bits, dither, seed=m.seed, channel_mask=lay.wav_mask)
        job.ch_layout = m.ffmpeg_layout if lay.ambisonic else None
    else:
        write_wav(path, pcm, m.sr, m.bits, dither, seed=m.seed)
    info = m.stats
    extra = ""
    if "final_lufs" in info:
        extra = f", normalised {info['measured_lufs']:.1f} -> {info['final_lufs']:.1f} LUFS"
    log.info("audio mix: %.1fs at %d Hz in %.1fs%s", pcm.shape[0] / m.sr, m.sr, time.perf_counter() - t0p, extra)
    return path, m.bits


def _run_ffmpeg(cmd: list[str], frames=None, progress: Progress | None = None) -> None:
    log.debug("ffmpeg: %s", " ".join(cmd))
    with tempfile.TemporaryFile() as errf:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE if frames is not None else subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=errf)
        broken = False
        try:
            if frames is not None:
                for i, img in enumerate(frames, 1):
                    try:
                        p.stdin.write(img.tobytes())
                    except BrokenPipeError:
                        broken = True
                        break
                    if progress:
                        progress.step(i)
                try:
                    p.stdin.close()
                except BrokenPipeError:
                    broken = True
            rc = p.wait()
        except BaseException:
            p.kill()
            raise
        if rc != 0 or broken:
            errf.seek(0)
            raise RuntimeError(f"ffmpeg failed ({rc}): {errf.read().decode(errors='replace').strip()[-2000:]}")


def _pix_in(kind: str) -> str:
    return {"rgb8": "rgb24", "rgba8": "rgba", "rgb16": "rgb48le", "rgba16": "rgba64le", "float": "gbrpf32le",
            "floata": "gbrapf32le"}[kind]


def run_job(r, job: Job, args, tap=None) -> list[str]:
    """Render one job; returns the files written. tap(frame) observes every delivered frame."""
    doc = r.doc
    fps = job.fps
    n = max(0, int(math.ceil((job.t1 - job.t0) * float(fps) - 1e-9)))
    times = [job.t0 + i / float(fps) for i in range(n)]
    first_index = int(round(job.t0 * float(fps)))
    kind = _frame_kind(job)
    if job.codec in ("jpeg-sequence",) and job.alpha:
        warn_once("output", "jpeg-alpha", "JPEG has no alpha; rendered opaque")
        kind = "rgb8"
    if job.codec == "gif" and job.alpha:
        kind = "rgba8"
    size = (job.width, job.height) if job.width and job.height else None
    if size and (abs(size[0] / size[1] - r.rc.width / r.rc.height) > 0.01):
        warn_once("output", job.name, f"{size[0]}x{size[1]} differs in aspect from the layout frame; resized")
    subsampled = job.codec in ("h264", "h265", "av1", "vp9", "webp") or (job.codec == "ffv1" and not job.alpha) \
        or job.codec in ("prores", "dnxhr")
    pad_even = subsampled
    # One process by default: every extra worker reopens the document and redraws the raster cache,
    # trading CPU time for wall time (--jobs 0: one per CPU).
    jobs_n = getattr(args, "jobs", 1)
    jobs_n = min(os.cpu_count() or 1, 8) if not jobs_n else max(1, jobs_n)
    frames_dir = getattr(args, "frames_dir", None)
    if frames_dir and len(getattr(args, "_jobs_list", [])) > 1:
        frames_dir = os.path.join(frames_dir, job.name)
    written: list[str] = []
    out_dir = os.path.dirname(os.path.abspath(job.path))
    os.makedirs(out_dir, exist_ok=True)
    progress = Progress(job.name, n)
    t_start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="scenerender-") as tmp:
        audio_path, bits = (None, 24) if getattr(args, "no_audio", False) else _audio_file(r, job, tmp, n)
        if job.codec == "audio-only":
            if audio_path is None:
                raise RuntimeError("audio-only output but the document has no audio")
            lay_in = ["-ch_layout", job.ch_layout] if job.ch_layout else []
            cmd = [ffmpeg_exe(), "-y", "-v", "error", "-nostdin", *lay_in, "-i", audio_path, *audio_codec_args(job, bits),
                   *metadata_args(doc, job)]
            fmt = _FORMATS.get(job.container or "")
            if fmt:
                cmd += ["-f", fmt]
            _run_ffmpeg(cmd + [job.path])
            written.append(job.path)
        elif job.codec in SEQUENCES:
            src = FrameSource(r, job, times, kind, size, False, jobs_n, frames_dir, first_index)
            src.tap = tap
            written += _write_sequence(job, src, first_index, progress, n, tmp)
            if audio_path:
                prefix = os.path.basename(job.path.split("%")[0]).rstrip("_-.")
                wav = os.path.join(out_dir, (prefix if prefix and prefix != "frame" else "audio") + ".wav")
                shutil.copyfile(audio_path, wav)
                written.append(wav)
        else:
            src = FrameSource(r, job, times, kind, size, pad_even, jobs_n, frames_dir, first_index)
            src.tap = tap
            written += _encode_video(r, job, src, times, kind, size, pad_even, audio_path, bits, progress, tmp)
    log.info("%s: %s in %.1fs", job.name, "audio" if job.codec == "audio-only" else f"{n} frames", time.perf_counter() - t_start)
    return written


def _frame_dims(r, size, pad_even):
    w, h = size if size else (r.rc.width, r.rc.height)
    if pad_even:
        w, h = w + w % 2, h + h % 2
    return w, h


_RATE_CODECS = ("h264", "h265", "av1", "vp9")


def fit_to_size(encode, cap: int, v0: int, fixed_bits: float, tries: int = 5, floor: int = 20_000) -> tuple[int, int]:
    """maxFileSize loop: encode(bitrate) -> bytes; lower the video bitrate by the measured overshoot until the
    file fits (each retry at least 10 % lower). -> (size, bitrate); RuntimeError when it cannot fit."""
    v = int(v0)
    size = 0
    for _ in range(tries):
        size = encode(v)
        if size <= cap:
            return size, v
        video_bits = max(size * 8 - fixed_bits, 1.0)
        target = cap * 8 * 0.97 - fixed_bits
        nv = max(floor, min(int(v * target / video_bits), int(v * 0.9)))
        if nv >= v:
            break
        v = nv
    raise RuntimeError(f"cannot fit maxFileSize {cap} bytes: {size} bytes at {v} b/s after {tries} encodes")


def fit_quality(encode, cap: int, q0: int, tries: int = 7) -> tuple[int, int]:
    """Quality bisection for encoders without bitrate control (WebP): highest quality <= q0 that fits."""
    lo, hi, q, best, last = 0, q0, q0, None, None
    for _ in range(tries):
        size, last = encode(q), q
        if size <= cap:
            best, lo = (size, q), q + 1
        else:
            hi = q - 1
        if lo > hi:
            break
        q = (lo + hi + 1) // 2 if best else (lo + hi) // 2
    if best is None:
        raise RuntimeError(f"cannot fit maxFileSize {cap} bytes even at quality {last}")
    if best[1] != last:
        encode(best[1])
    return best


_BAYER8 = (np.array([[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26], [12, 44, 4, 36, 14, 46, 6, 38],
                     [60, 28, 52, 20, 62, 30, 54, 22], [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
                     [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]], np.float32) + 0.5) / 64.0


def gif_alpha(img: np.ndarray) -> np.ndarray:
    """Straight RGBA8 -> 1-bit alpha by ordered (8x8 Bayer) dithering; transparent pixels get black colour."""
    h, w = img.shape[:2]
    thr = np.tile(_BAYER8, (h // 8 + 1, w // 8 + 1))[:h, :w]
    opaque = img[..., 3].astype(np.float32) / 255.0 > thr
    out = img.copy()
    out[..., 3] = np.where(opaque, 255, 0)
    out[..., :3] = np.where(opaque[..., None], img[..., :3], 0)
    return out


def _colour_bins(img: np.ndarray) -> np.ndarray:
    """4096-bin (4 bits per channel) set covering 99 % of the (opaque) pixels of a frame."""
    s = img[::2, ::2]
    q = (s[..., :3] >> 4).astype(np.int32)
    idx = (q[..., 0] << 8) | (q[..., 1] << 4) | q[..., 2]
    if s.shape[-1] == 4:
        idx = idx[s[..., 3] > 0]
    hist = np.bincount(idx.ravel(), minlength=4096)
    order = np.argsort(-hist, kind="stable")
    cum = np.cumsum(hist[order])
    k = int(np.searchsorted(cum, 0.99 * max(cum[-1], 1))) + 1
    used = np.zeros(4096, bool)
    used[order[:k]] = True
    return used


def gif_palette_mode(sets: list[np.ndarray]) -> str:
    """'frame' when a single 256-colour palette would have to merge colours the frames need, else 'global'."""
    if len(sets) < 2:
        return "global"
    union = np.logical_or.reduce(sets).sum()
    return "frame" if union > 256 and union > 1.5 * max(s.sum() for s in sets) else "global"


def _encode_gif(job, src, w, h, kind, progress, tmp) -> None:
    raw = os.path.join(tmp, "gif.raw")
    sets = []
    with open(raw, "wb") as f:
        for i, img in enumerate(src, 1):
            if job.alpha:
                img = gif_alpha(img)
            f.write(np.ascontiguousarray(img).tobytes())
            sets.append(_colour_bins(img))
            progress.step(i)
    mode = gif_palette_mode(sets)
    trans = ":reserve_transparent=1" if job.alpha else ""
    athr = ":alpha_threshold=128" if job.alpha else ""
    if mode == "frame":
        graph = f"[0:v]split[a][b];[a]palettegen=stats_mode=single{trans}[p];[b][p]paletteuse=new=1:dither=bayer:bayer_scale=4{athr}[v]"
    else:
        graph = f"[0:v]split[a][b];[a]palettegen=stats_mode=full{trans}[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle{athr}[v]"
    log.info("%s: GIF with %s palette%s", job.name, "per-frame" if mode == "frame" else "a global", "s" if mode == "frame" else "")
    _run_ffmpeg([ffmpeg_exe(), "-y", "-v", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", _pix_in(kind), "-s", f"{w}x{h}",
                 "-r", _fps_str(job.fps), "-i", raw, "-filter_complex", graph, "-map", "[v]",
                 "-loop", str(int(job.a("loopCount", 0))), "-f", "gif", job.path])


def _check_size(job) -> None:
    cap = int(job.a("maxFileSize") or 0)
    if cap and os.path.getsize(job.path) > cap:
        why = "the size loop could not meet it" if job.codec in _RATE_CODECS or job.codec == "webp" else "no rate control"
        raise RuntimeError(f"{os.path.getsize(job.path)} bytes exceed maxFileSize {cap} ({job.codec}: {why})")


def _encode_video(r, job, src, times, kind, size, pad_even, audio_path, bits, progress, tmp) -> list[str]:
    duration = len(times) / float(job.fps)
    w, h = _frame_dims(r, size, pad_even)
    if job.codec == "gif":
        _encode_gif(job, src, w, h, kind, progress, tmp)
        _check_size(job)
        return [job.path]
    abps = audio_bitrate(job, duration) if audio_path else 0
    vargs, vf, bitrate_mode, _ = video_codec_args(job, duration, abps)
    raw_in = ["-f", "rawvideo", "-pix_fmt", _pix_in(kind), "-s", f"{w}x{h}", "-r", _fps_str(job.fps), "-i", "-"]
    ain = ((["-ch_layout", job.ch_layout] if job.ch_layout else []) + ["-i", audio_path]) if audio_path else []
    maps = ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio_path else [])
    fargs = ["-vf", vf] if vf else []
    aargs = audio_codec_args(job, bits, duration) if audio_path else []
    cont = job.container
    fmt = ["-f", _FORMATS[cont]] if cont in _FORMATS and cont not in ("wav", "m4a", "mp3", "flac", "ogg") else []
    mov = (cont in ("mp4", "mov")) or (not cont and os.path.splitext(job.path)[1].lower() in (".mp4", ".mov", ".m4v"))
    movflags = []
    if mov:
        flags = []
        if parse_bool(job.a("faststart", "true"), True):
            flags.append("+faststart")
        md = r.doc.section("metadata")
        if md is not None and any(isinstance(m.tag, str) and m.tag == "meta" for m in md):
            flags.append("+use_metadata_tags")
        if flags:
            movflags = ["-movflags", "".join(flags)]
    meta = metadata_args(r.doc, job)
    tail = ["-shortest"] if audio_path and job.codec in ("apng", "webp") else []
    two_pass = parse_bool(job.a("twoPass")) and bitrate_mode and job.codec in _RATE_CODECS
    if parse_bool(job.a("twoPass")) and not two_pass:
        warn_once("output", f"twoPass-{job.name}", "twoPass needs a bitrate (bitrate/maxFileSize); encoded in one pass")
    cap = int(job.a("maxFileSize") or 0)
    fit = bool(cap) and (job.codec in _RATE_CODECS or job.codec == "webp")
    ff = ffmpeg_exe()
    if two_pass or fit:          # frames are rendered once into a lossless intermediate the encodes read
        inter = os.path.join(tmp, "intermediate.mkv")
        ipix = {"rgb8": "bgr0", "rgba8": "bgra", "rgb16": "rgb48le", "rgba16": "rgba64le"}.get(kind, "bgr0")
        _run_ffmpeg([ff, "-y", "-v", "error", "-nostdin", *raw_in, "-c:v", "ffv1", "-level", "3", "-pix_fmt", ipix, inter],
                    iter(src), progress)
        vin, frames = ["-i", inter], None
    else:
        vin, frames = raw_in, iter(src)

    gpu_ff = gpu_encoder(job, two_pass or fit, (w, h))
    if gpu_ff:
        log.info("%s: encoding on the GPU (h264_nvenc)", job.name)

    def encode(force_bitrate: int | None = None, webp_q: int | None = None) -> int:
        va, _, _, _ = video_codec_args(job, duration, abps, force_bitrate, webp_q, gpu=bool(gpu_ff))
        if not two_pass:
            cmd = [gpu_ff or ff, "-y", "-v", "error", "-nostdin", *vin, *ain, *fargs, *maps, *va, *aargs, *movflags, *meta,
                   *fmt, *tail]
            _run_ffmpeg(cmd + [job.path], frames, progress if frames is not None else None)
            return os.path.getsize(job.path)
        log_prefix = os.path.join(tmp, "passlog")
        base = [ff, "-y", "-v", "error", "-nostdin", *vin, *ain, *fargs, *maps, *va]
        if job.codec == "h265":
            i = base.index("-x265-params") + 1
            p1, p2 = base.copy(), base.copy()
            p1[i] = base[i] + f":pass=1:stats={log_prefix}.log"
            p2[i] = base[i] + f":pass=2:stats={log_prefix}.log"
            _run_ffmpeg(p1 + ["-an", "-f", "null", os.devnull])
            _run_ffmpeg(p2 + [*aargs, *movflags, *meta, *fmt, job.path])
        else:
            _run_ffmpeg(base + ["-pass", "1", "-passlogfile", log_prefix, "-an", "-f", "null", os.devnull])
            _run_ffmpeg(base + ["-pass", "2", "-passlogfile", log_prefix, *aargs, *movflags, *meta, *fmt, job.path])
        return os.path.getsize(job.path)

    def encode_final(**kw) -> int:
        """Encode plus container metadata injection, so maxFileSize measures the delivered file."""
        encode(**kw)
        post_metadata(r, job, tmp)
        return os.path.getsize(job.path)

    if fit and job.codec == "webp":
        q0 = max(0, min(100, 100 - 2 * int(job.a("crf", 18)) + 16))
        sz, q = fit_quality(lambda q: encode_final(webp_q=q), cap, q0)
        log.info("%s: %d bytes at WebP quality %d (maxFileSize %d)", job.name, sz, q, cap)
    elif fit:
        v0 = size_budget_bitrate(job, duration, abps)
        sz, v = fit_to_size(lambda v: encode_final(force_bitrate=v), cap, v0, abps * duration)
        log.info("%s: %d bytes at %d b/s video (maxFileSize %d)", job.name, sz, v, cap)
    else:
        encode_final()
    _check_size(job)
    return [job.path]


# ====================================================================== spherical / HDR container metadata
def spherical_params(doc, job: Job) -> dict | None:
    """(projection, stereo, eye size) for equirectangular projects unless output/@sphericalMetadata="false"."""
    if doc.project.get("mode") != "equirectangular" or not parse_bool(job.a("sphericalMetadata", "true"), True):
        return None
    s360 = doc.section("scene360")
    layout = s360.get("layout", "equirectangular") if s360 is not None else "equirectangular"
    stereo = s360.get("stereo", "mono") if s360 is not None else "mono"
    w = int(s360.get("width", 3840)) if s360 is not None else 3840
    h = int(s360.get("height", 1920)) if s360 is not None else 1920
    eye = (w // 2, h) if stereo == "left-right" else (w, h // 2) if stereo == "top-bottom" else (w, h)
    return {"projection": layout, "stereo": stereo, "eye": eye}


def post_metadata(r, job: Job, tmp: str) -> None:
    """Write spherical (st3d/sv3d) and HDR (mdcv/clli) metadata into the finished video file."""
    boxes: list[bytes] = []
    sph = spherical_params(r.doc, job)
    mesh = sph["projection"] in MESH_LAYOUTS if sph else False
    if sph:
        boxes += spherical_boxes(sph["projection"], sph["stereo"], eye=sph["eye"])
    if any(job.a(k) is not None for k in ("maxCLL", "maxFALL", "masteringDisplay")):
        try:
            boxes += hdr_boxes(job.a("masteringDisplay"), job.a("maxCLL"), job.a("maxFALL"))
        except ValueError as e:
            warn_once("output", f"hdr-{job.name}", str(e))
    if not boxes:
        return
    cont = job.container or os.path.splitext(job.path)[1].lstrip(".").lower()
    if job.codec in ("gif", "apng", "webp") or cont not in ("mp4", "m4v", "mov", "mkv", "webm", "mxf"):
        warn_once("output", f"meta-{job.name}", f"{job.codec}/{cont or '?'} cannot carry spherical/HDR metadata; not written")
        return
    if cont in ("mp4", "m4v", "mov"):
        inject_sample_entry_boxes(job.path, boxes)
        return
    if job.codec == "ffv1":
        warn_once("output", f"meta-{job.name}", "FFV1 cannot pass through an MP4/MOV intermediate; spherical/HDR metadata not written")
        return
    ff = ffmpeg_exe()
    v = os.path.join(tmp, "meta" + (".mov" if job.codec in ("prores", "dnxhr") else ".mp4"))
    _run_ffmpeg([ff, "-y", "-v", "error", "-nostdin", "-i", job.path, "-map", "0:v:0", "-c", "copy", v])
    if mesh:        # ffmpeg's demuxer has no mesh side data: keep st3d for StereoMode, write Projection below
        boxes = [b for b in boxes if b[4:8] != b"sv3d"]
    inject_sample_entry_boxes(v, boxes)
    out = os.path.join(tmp, "final." + cont)
    rate = ["-r", _fps_str(job.fps)] if cont == "mxf" else []
    _run_ffmpeg([ff, "-y", "-v", "error", "-nostdin", "-i", v, "-i", job.path, "-map", "0:v:0", "-map", "1", "-map", "-1:v",
                 "-c", "copy", "-map_metadata", "1", *rate, "-f", _FORMATS[cont], out])
    shutil.move(out, job.path)
    if cont == "mxf":
        from .mxf import inject_content_light
        inject_content_light(job.path, job.a("maxCLL"), job.a("maxFALL"))
    if mesh and cont in ("mkv", "webm"):
        from .spherical_mesh import layout_mshp
        inject_matroska_projection(job.path, 3, layout_mshp(sph["projection"], *sph["eye"]))
    elif mesh:
        warn_once("output", f"meta-{job.name}", f"{cont} cannot carry a mesh projection; only stereo metadata written")


def _write_sequence(job, src: FrameSource, first_index: int, progress: Progress, n: int, tmp) -> list[str]:
    from PIL import Image
    pattern = job.path
    os.makedirs(os.path.dirname(os.path.abspath(pattern)) or ".", exist_ok=True)
    written = []
    if job.codec == "exr-sequence":
        w, h = _frame_dims(src.r, src.size, False)
        cmd = [ffmpeg_exe(), "-y", "-v", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", "gbrapf32le" if job.alpha else "gbrpf32le",
               "-s", f"{w}x{h}", "-r", _fps_str(job.fps), "-i", "-", "-c:v", "exr", "-compression", "zip16",
               "-format", "float", "-start_number", str(first_index), "-f", "image2", pattern]

        def planar():
            for img in src:
                ch = (1, 2, 0, 3) if job.alpha else (1, 2, 0)          # G, B, R(, A)
                yield np.ascontiguousarray(np.stack([img[..., c] for c in ch], 0).astype("<f4"))
        _run_ffmpeg(cmd, planar(), progress)
        return [pattern % (first_index + i) for i in range(n)]
    if job.codec == "tiff-sequence":
        w, h = _frame_dims(src.r, src.size, False)
        pix = "rgba64le" if job.alpha else "rgb48le"
        cmd = [ffmpeg_exe(), "-y", "-v", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", pix, "-s", f"{w}x{h}",
               "-r", _fps_str(job.fps), "-i", "-", "-c:v", "tiff", "-pix_fmt", pix, "-compression_algo", "deflate",
               "-start_number", str(first_index), "-f", "image2", pattern]
        _run_ffmpeg(cmd, (np.ascontiguousarray(img.astype("<u2")) for img in src), progress)
        return [pattern % (first_index + i) for i in range(n)]
    quality = int(round(float(job.a("quality", 0.95)) * 100)) if job.a("quality") else 95
    for i, img in enumerate(src):
        p = pattern % (first_index + i)
        if job.codec == "jpeg-sequence":
            Image.fromarray(img[..., :3]).save(p, quality=quality)
        else:
            Image.fromarray(img).save(p, compress_level=4)
        written.append(p)
        progress.step(i + 1)
    return written


# ====================================================================== MP4/MOV box injection
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf", b"udta"}
_VISUAL_ENTRY = 86          # size of a VisualSampleEntry / QuickTime video sample description before child boxes


def _boxes(buf: bytes, start: int, end: int):
    """(type, start, header_len, end) for each box in buf[start:end]."""
    i = start
    while i + 8 <= end:
        size = int.from_bytes(buf[i:i + 4], "big")
        typ = bytes(buf[i + 4:i + 8])
        hdr = 8
        if size == 1:
            size = int.from_bytes(buf[i + 8:i + 16], "big")
            hdr = 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            raise ValueError(f"malformed box {typ!r} at {i}")
        yield typ, i, hdr, i + size
        i += size


def box(typ: bytes, payload: bytes, full: tuple[int, int] | None = None) -> bytes:
    if full is not None:
        payload = bytes([full[0]]) + full[1].to_bytes(3, "big") + payload
    return (8 + len(payload)).to_bytes(4, "big") + typ + payload


MESH_LAYOUTS = ("eac", "fisheye-180")


def spherical_boxes(projection: str = "equirectangular", stereo: str = "mono", yaw: float = 0.0,
                    pitch: float = 0.0, roll: float = 0.0, source: str = "scenerender",
                    eye: tuple[int, int] = (3840, 1920)) -> list[bytes]:
    """Spherical Video V2 (google/spatial-media docs/spherical-video-v2-rfc.md): st3d + sv3d(svhd, proj(prhd,
    equi|cbmp|mshp)); eye is the per-eye image size (the fisheye mesh depends on its aspect ratio)."""
    st3d = box(b"st3d", bytes([{"mono": 0, "top-bottom": 1, "left-right": 2}.get(stereo, 0)]), (0, 0))
    fx = lambda d: int(round(d * 65536)).to_bytes(4, "big", signed=True)   # noqa: E731 — 16.16 fixed point
    prhd = box(b"prhd", fx(yaw) + fx(pitch) + fx(roll), (0, 0))
    if projection in MESH_LAYOUTS:
        from .spherical_mesh import layout_mshp
        pbox = box(b"mshp", layout_mshp(projection, *eye))            # payload carries its FullBox header
    elif projection == "cubemap":
        pbox = box(b"cbmp", (0).to_bytes(4, "big") + (0).to_bytes(4, "big"), (0, 0))    # layout 0, padding 0
    else:
        pbox = box(b"equi", bytes(16), (0, 0))        # full-sphere bounds: top, bottom, left, right = 0
    sv3d = box(b"sv3d", box(b"svhd", source.encode() + b"\0", (0, 0)) + box(b"proj", prhd + pbox))
    return [st3d, sv3d]


def parse_master_display(s: str) -> dict:
    """x265/ST 2086 string "G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min)" (0.00002 and 0.0001 cd/m2 units)."""
    vals = {k: tuple(int(v) for v in re.findall(r"-?\d+", m)) for k, m in re.findall(r"(G|B|R|WP|L)\(([^)]*)\)", s)}
    missing = {"G", "B", "R", "WP", "L"} - set(vals)
    if missing:
        raise ValueError(f"masteringDisplay {s!r}: missing {', '.join(sorted(missing))}")
    return vals


def hdr_boxes(master: str | None, max_cll: int | None, max_fall: int | None) -> list[bytes]:
    """ISO/IEC 14496-12 mdcv (SMPTE ST 2086, primaries G, B, R) and clli (CTA-861.3) boxes."""
    out = []
    if master:
        m = parse_master_display(master)
        p = b"".join(v.to_bytes(2, "big") for k in ("G", "B", "R", "WP") for v in m[k][:2])
        out.append(box(b"mdcv", p + m["L"][0].to_bytes(4, "big") + m["L"][1].to_bytes(4, "big")))
    if max_cll is not None or max_fall is not None:
        out.append(box(b"clli", int(max_cll or 0).to_bytes(2, "big") + int(max_fall or 0).to_bytes(2, "big")))
    return out


def inject_sample_entry_boxes(path: str, new: list[bytes]) -> None:
    """Append (or replace same-type) child boxes of the first video sample entry of an MP4/MOV file in place.

    Box sizes up the moov chain are rewritten; when moov precedes mdat (faststart) every stco/co64
    chunk offset is shifted by the growth (stco is promoted to co64 if an offset would overflow)."""
    with open(path, "rb") as f:
        data = bytearray(f.read())
    top = list(_boxes(data, 0, len(data)))
    moov = next((b for b in top if b[0] == b"moov"), None)
    if moov is None:
        raise ValueError(f"{path}: no moov box")
    mdat_after = any(b[0] == b"mdat" and b[1] > moov[1] for b in top)
    mstart, mend = moov[1], moov[3]
    tree = bytes(data[mstart:mend])
    types = {b[4:8] for b in new}

    def rebuild(buf: bytes, start: int, end: int, path_: tuple, ctx: dict) -> bytes:
        out = bytearray()
        for typ, s, hdr, e in _boxes(buf, start, end):
            if typ == b"trak":
                ctx["video"] = _is_video(buf, s + hdr, e)
            if typ in _CONTAINERS:
                body = rebuild(buf, s + hdr, e, path_ + (typ,), ctx)
                out += (8 + len(body)).to_bytes(4, "big") + typ + body
            elif typ == b"stsd" and ctx.get("video") and not ctx.get("done"):
                n = int.from_bytes(buf[s + hdr + 4:s + hdr + 8], "big")
                entries = list(_boxes(buf, s + hdr + 8, e))
                body = bytearray(buf[s + hdr:s + hdr + 8])
                for k, (et, es, eh, ee) in enumerate(entries):
                    if k == 0:
                        base = bytes(buf[es + eh:es + eh + _VISUAL_ENTRY - 8])
                        kids = [bytes(buf[cs:ce]) for ct, cs, ch, ce in _boxes(buf, es + _VISUAL_ENTRY, ee) if ct not in types]
                        payload = base + b"".join(kids) + b"".join(new)
                        body += (8 + len(payload)).to_bytes(4, "big") + et + payload
                    else:
                        body += buf[es:ee]
                ctx["done"] = True
                assert n == len(entries)
                out += (8 + len(body)).to_bytes(4, "big") + typ + body
            else:
                out += buf[s:e]
        return bytes(out)

    ctx: dict = {}
    body = rebuild(tree, moov[2], len(tree), (), ctx)
    if not ctx.get("done"):
        raise ValueError(f"{path}: no video sample entry")
    moov_new = (8 + len(body)).to_bytes(4, "big") + b"moov" + body
    if mdat_after:
        d, wide = len(moov_new) - (mend - mstart), False
        while True:
            shifted = _shift_chunk_offsets(moov_new, d, wide)
            nd = len(shifted) - (mend - mstart)
            if nd == d:
                break
            d, wide = nd, True
        moov_new = shifted
    data[mstart:mend] = moov_new
    tmp = path + ".inject"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _is_video(buf: bytes, start: int, end: int) -> bool:
    for typ, s, hdr, e in _boxes(buf, start, end):
        if typ == b"mdia":
            for t2, s2, h2, e2 in _boxes(buf, s + hdr, e):
                if t2 == b"hdlr":
                    return bytes(buf[s2 + h2 + 8:s2 + h2 + 12]) == b"vide"
    return False


def _shift_chunk_offsets(moov: bytes, delta: int, force64: bool = False) -> bytes:
    def walk(buf: bytes, start: int, end: int) -> bytes:
        out = bytearray()
        for typ, s, hdr, e in _boxes(buf, start, end):
            if typ in _CONTAINERS:
                body = walk(buf, s + hdr, e)
                out += (8 + len(body)).to_bytes(4, "big") + typ + body
            elif typ in (b"stco", b"co64"):
                vf = buf[s + hdr:s + hdr + 4]
                n = int.from_bytes(buf[s + hdr + 4:s + hdr + 8], "big")
                w = 4 if typ == b"stco" else 8
                offs = [int.from_bytes(buf[s + hdr + 8 + i * w:s + hdr + 8 + (i + 1) * w], "big") + delta for i in range(n)]
                big = typ == b"co64" or force64 or (offs and max(offs) >= 2 ** 32)
                w2 = 8 if big else 4
                payload = vf + n.to_bytes(4, "big") + b"".join(o.to_bytes(w2, "big") for o in offs)
                out += (8 + len(payload)).to_bytes(4, "big") + (b"co64" if big else b"stco") + payload
            else:
                out += buf[s:e]
        return bytes(out)
    body = walk(moov, 8, len(moov))
    return (8 + len(body)).to_bytes(4, "big") + b"moov" + body


def read_sample_entry_boxes(path: str) -> dict[bytes, bytes]:
    """Child boxes (type -> payload) of the first video sample entry (for tests and verification)."""
    with open(path, "rb") as f:
        data = f.read()

    def find(start, end):
        video = False
        for typ, s, hdr, e in _boxes(data, start, end):
            if typ == b"trak":
                video = _is_video(data, s + hdr, e)
                if not video:
                    continue
            if typ in _CONTAINERS:
                r = find(s + hdr, e)
                if r is not None:
                    return r
            elif typ == b"stsd":
                es, ee = next((b[1], b[3]) for b in _boxes(data, s + hdr + 8, e))
                return {ct: data[cs + ch:ce] for ct, cs, ch, ce in _boxes(data, es + _VISUAL_ENTRY, ee)}
        return None
    return find(0, len(data)) or {}


# ====================================================================== Matroska projection injection
_EBML_MASTERS = {0x18538067, 0x114D9B74, 0x4DBB, 0x1654AE6B, 0xAE, 0xE0, 0x7670, 0x1C53BB6B, 0xBB, 0xB7}


def _ebml_elems(buf: bytes, start: int, end: int):
    """(id, start, header_len, end) of each EBML element in buf[start:end]; unknown sizes run to end."""
    i = start
    while i < end:
        n = 1
        while n <= 4 and not buf[i] & (0x80 >> (n - 1)):
            n += 1
        eid = int.from_bytes(buf[i:i + n], "big")
        m = 1
        while m <= 8 and not buf[i + n] & (0x80 >> (m - 1)):
            m += 1
        if n > 4 or m > 8:
            raise ValueError(f"malformed EBML element at {i}")
        size = int.from_bytes(buf[i + n:i + n + m], "big") & ((1 << (7 * m)) - 1)
        e = end if size == (1 << (7 * m)) - 1 else i + n + m + size
        if e > end:
            raise ValueError(f"EBML element {eid:X} at {i} overruns its parent")
        yield eid, i, n + m, e
        i = e


def _ebml_el(eid: int, payload: bytes) -> bytes:
    n = len(payload)
    m = next(k for k in range(1, 9) if n < (1 << (7 * k)) - 1)
    return eid.to_bytes((eid.bit_length() + 7) // 8, "big") + ((1 << (7 * m)) | n).to_bytes(m, "big") + payload


def _ebml_uint(eid: int, v: int, width: int = 1) -> bytes:
    return _ebml_el(eid, v.to_bytes(max(width, (v.bit_length() + 7) // 8, 1), "big"))


def _ebml_master(eid: int, kids: list[tuple[int, bytes]]) -> bytes:
    """Master element from (id, encoded child) pairs; a leading CRC-32 child is recomputed over the rest."""
    rest = b"".join(k for i, k in kids if i != 0xBF)
    crc = _ebml_el(0xBF, zlib.crc32(rest).to_bytes(4, "little")) if kids and kids[0][0] == 0xBF else b""
    return _ebml_el(eid, crc + rest)


def _ebml_rebuild(buf: bytes, s: int, h: int, e: int, edit) -> bytes:
    """Re-encode the master buf[s:e], each child replaced by edit(id, start, header, end) unless that is None."""
    eid = next(_ebml_elems(buf, s, e))[0]
    kids = []
    for cid, cs, ch, ce in _ebml_elems(buf, s + h, e):
        new = edit(cid, cs, ch, ce)
        kids.append((cid, bytes(buf[cs:ce]) if new is None else new))
    return _ebml_master(eid, kids)


def inject_matroska_projection(path: str, ptype: int, private: bytes | None) -> None:
    """Write Video/Projection (ProjectionType, ProjectionPrivate) into the first video TrackEntry of an
    mkv/webm file in place (an existing Projection is replaced).

    Everything after Tracks moves: the Segment size, SeekHead SeekPositions and Cues
    CueClusterPositions are rewritten (iterated until their widths settle) and the CRC-32 elements of
    rebuilt masters are recomputed."""
    import bisect
    with open(path, "rb") as f:
        data = f.read()
    seg = next((x for x in _ebml_elems(data, 0, len(data)) if x[0] == 0x18538067), None)
    if seg is None:
        raise ValueError(f"{path}: no Matroska Segment")
    s0, h0, e0 = seg[1], seg[2], seg[3]
    base = s0 + h0
    proj = _ebml_master(0x7670, [(0x7671, _ebml_uint(0x7671, ptype))] + ([(0x7672, _ebml_el(0x7672, private))] if private else []))
    done = []

    def track(cid, cs, ch, ce):
        if cid != 0xAE or done:
            return None
        kinds = [int.from_bytes(data[ks + kh:ke], "big") for k, ks, kh, ke in _ebml_elems(data, cs + ch, ce) if k == 0x83]
        if kinds != [1]:
            return None
        done.append(True)

        def video(vid, vs, vh, ve):
            if vid != 0xE0:
                return None
            kids = [(k, bytes(data[ks:ke])) for k, ks, kh, ke in _ebml_elems(data, vs + vh, ve) if k != 0x7670]
            return _ebml_master(0xE0, kids + [(0x7670, proj)])
        return _ebml_rebuild(data, cs, ch, ce, video)

    kids = list(_ebml_elems(data, base, e0))
    new = [bytes(data[s:e]) for _, s, _, e in kids]
    for k, (cid, s, h, e) in enumerate(kids):
        if cid == 0x1654AE6B:
            new[k] = _ebml_rebuild(data, s, h, e, track)
    if not done:
        raise ValueError(f"{path}: no video TrackEntry")
    old_starts = [s - base for _, s, _, _ in kids]
    for _ in range(8):
        starts = np.cumsum([0] + [len(b) for b in new[:-1]]).tolist()

        def moved(pos: int) -> int:
            k = max(0, bisect.bisect_right(old_starts, pos) - 1)
            return pos + starts[k] - old_starts[k]

        def positions(pid: int):
            def edit(cid, cs, ch, ce):
                if cid == pid:
                    return _ebml_uint(pid, moved(int.from_bytes(data[cs + ch:ce], "big")), ce - cs - ch)
                return _ebml_rebuild(data, cs, ch, ce, edit) if cid in _EBML_MASTERS else None
            return edit

        lens = [len(b) for b in new]
        for k, (cid, s, h, e) in enumerate(kids):
            if cid == 0x114D9B74:
                new[k] = _ebml_rebuild(data, s, h, e, positions(0x53AC))
            elif cid == 0x1C53BB6B:
                new[k] = _ebml_rebuild(data, s, h, e, positions(0xF1))
        if [len(b) for b in new] == lens:
            break
    body = b"".join(new)
    head, m = data[s0:s0 + 4], h0 - 4       # keep the Segment size field width (unknown size stays unknown)
    size = int.from_bytes(data[s0 + 4:base], "big") & ((1 << (7 * m)) - 1)
    if size != (1 << (7 * m)) - 1:
        if len(body) >= (1 << (7 * m)) - 1:
            raise ValueError(f"{path}: Segment size field too narrow")
        head += ((1 << (7 * m)) | len(body)).to_bytes(m, "big")
    else:
        head += data[s0 + 4:base]
    tmp = path + ".inject"
    with open(tmp, "wb") as f:
        f.write(data[:s0] + head + body + data[e0:])
    os.replace(tmp, path)


def read_matroska_projection(path: str) -> dict[int, bytes]:
    """Children (id -> payload) of the first video track's Projection element (for tests and verification)."""
    with open(path, "rb") as f:
        data = f.read()

    def find(s: int, e: int) -> dict[int, bytes] | None:
        for cid, cs, ch, ce in _ebml_elems(data, s, e):
            if cid == 0x7670:
                return {k: data[ks + kh:ke] for k, ks, kh, ke in _ebml_elems(data, cs + ch, ce)}
            if cid in _EBML_MASTERS and cid not in (0x114D9B74, 0x1C53BB6B):
                r = find(cs + ch, ce)
                if r is not None:
                    return r
        return None
    return find(0, len(data)) or {}


# ====================================================================== stills, captions, destinations
def write_still(r, el, job: Job) -> str | None:
    from PIL import Image
    doc = r.doc
    t = doc.markers.get(el.get("marker")) if el.get("marker") else None
    if t is None:
        t = float(el.get("time", 0) or 0)
    fmt = el.get("format", "jpeg")
    alpha = job.alpha and fmt in ("png", "webp", "avif")
    size = (job.width, job.height) if job.width and job.height else None
    img = render_frame(r, t, "rgba8" if alpha else "rgb8", size, False)
    im = Image.fromarray(img)
    if el.get("width"):
        w = int(el.get("width"))
        h = max(1, round(im.height * w / im.width))
        im = im.resize((w, h), Image.LANCZOS)
    path = el.get("path")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    q = int(round(float(el.get("quality", 0.9) or 0.9) * 100))
    try:
        if fmt == "jpeg":
            im.convert("RGB").save(path, format="JPEG", quality=q)
        elif fmt == "png":
            im.save(path, format="PNG")
        elif fmt == "webp":
            im.save(path, format="WEBP", quality=q)
        else:
            try:
                im.save(path, format="AVIF", quality=q)
            except (KeyError, ValueError, OSError):
                tmp = path + ".tmp.png"
                im.save(tmp)
                crf = max(0, min(63, int(63 * (1 - q / 100))))
                _run_ffmpeg([ffmpeg_exe(), "-y", "-v", "error", "-nostdin", "-i", tmp, "-c:v", "libaom-av1",
                             "-still-picture", "1", "-crf", str(crf), "-pix_fmt", "yuv420p", path])
                os.remove(tmp)
    except Exception as e:  # noqa: BLE001
        warn_once("output-still", path, f"could not write: {e}")
        return None
    return path


def write_captions(doc, job: Job) -> list[str]:
    try:
        from . import captions
    except ImportError:
        return []
    fn = getattr(captions, "write_sidecars", None)
    if fn is None or doc.section("captions") is None:
        return []
    target = job.path
    if "%" in target:          # sequences: sidecars sit next to the frames
        prefix = os.path.basename(target.split("%")[0]).rstrip("_-.")
        target = os.path.join(os.path.dirname(target), (prefix if prefix and prefix != "frame" else "captions") + ".seq")
    try:
        return list(fn(doc, target, job.captions) or [])
    except Exception as e:  # noqa: BLE001
        warn_once("captions", job.name, f"sidecar captions failed: {e}")
        return []


def deliver(job: Job, files: list[str], publish: bool = False, info: dict | None = None) -> bool:
    """Copy to file destinations; upload to the others only with --publish (else warn and skip). -> all ok."""
    if job.el is None:
        return True
    ok = True
    for d in job.el:
        if not isinstance(d.tag, str) or d.tag != "destination":
            continue
        kind, uri = d.get("kind"), d.get("uri", "")
        if kind != "file":
            if not publish:
                warn_once("destination", f"{job.name}:{kind}", f"{kind} destination: not uploaded (pass --publish to upload)")
                continue
            from .publish import PublishError, publish as _publish
            try:
                where = _publish(kind, uri, [f for f in files if os.path.exists(f)], credentials=d.get("credentials"),
                                 job=job.name, metadata=info or {})
                log.info("%s: published %d file(s) to %s", job.name, len(where), kind)
            except PublishError as e:
                print(f"{job.name}: {kind} destination failed: {e}", file=sys.stderr)
                ok = False
            continue
        dst = uri[7:] if uri.startswith("file://") else uri
        into_dir = dst.endswith("/") or os.path.isdir(dst) or len(files) > 1
        if into_dir:
            os.makedirs(dst, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
        for f in files:
            if os.path.exists(f):
                shutil.copy2(f, os.path.join(dst, os.path.basename(f)) if into_dir else dst)
        log.info("%s: copied %d file(s) to %s", job.name, len(files), dst)
    return ok


def _job_info(job: Job, r) -> dict:
    return {"output": job.name, "codec": job.codec, "container": job.container, "width": job.width or r.rc.width,
            "height": job.height or r.rc.height, "fps": float(job.fps), "start": job.t0, "end": job.t1,
            "colorSpace": job.color[0] or "srgb", "transfer": job.color[1] or "auto"}


# ====================================================================== entry point
def render_outputs(renderer, args) -> int:
    """Render every job; exit status 0, 1 (an output failed) or 4 (an output failed QA only)."""
    from . import qa
    jobs = jobs_from_args(renderer, args)
    if not jobs:
        if getattr(args, "output", None):
            print(f"no <output> with id {', '.join(args.output)}", file=sys.stderr)
        else:
            print("nothing to render: give -o PATH or add <output> elements", file=sys.stderr)
        return 2
    args._jobs_list = jobs
    given = _base_open_kwargs(args)
    renderers: dict = {}
    failed = qa_failed = 0
    run_qa = not getattr(args, "no_qa", False)
    for job in jobs:
        key = tuple(sorted((k, repr(v)) for k, v in job.open_kwargs.items()))
        r = renderers.get(key)
        if r is None:
            r = renderer if job.open_kwargs == given else open_renderer(job.open_kwargs)
            renderers = {key: r}           # keep one renderer alive at a time
        for k in ("output_color", "burn_captions", "output_hdr"):
            r.rc.cache.pop(k, None)
        r.rc.cache.update(_job_cache(job))
        findings: list = []
        sidecars: list[str] = []
        det = None
        if run_qa:
            picture_only = job.codec in ("gif", "apng", "webp") or job.codec in SEQUENCES
            audio_out = job.audio and not getattr(args, "no_audio", False)
            findings, sidecars = qa.static_checks(r, t0=job.t0, t1=job.t1, captions=job.captions, burn=job.burn,
                                                  audio=audio_out, out_path=job.path, picture_only=picture_only)
            if any(f.level == "error" for f in findings):
                print(qa.report(job.name, findings), file=sys.stderr)
                qa_failed += 1
                continue
            mode = qa.accessibility(r.doc)["flash"]
            if mode != "off" and job.codec != "audio-only":
                det = qa.FlashDetector(float(job.fps), *job.color)
        try:
            files = run_job(r, job, args, tap=det.feed if det is not None else None)
        except (RuntimeError, OSError, ValueError) as e:
            print(f"{job.name}: {e}", file=sys.stderr)
            failed += 1
            continue
        if det is not None:
            findings += det.findings(qa.accessibility(r.doc)["flash"], job.t0)
        if findings:
            print(qa.report(job.name, findings), file=sys.stderr)
            if any(f.level == "error" for f in findings):
                qa_failed += 1
                continue
        files += sidecars
        if job.el is not None:
            for st in job.el:
                if isinstance(st.tag, str) and st.tag in ("poster", "thumbnail"):
                    p = write_still(r, st, job)
                    if p:
                        files.append(p)
        files += write_captions(r.doc, job)
        if not deliver(job, files, publish=bool(getattr(args, "publish", False)), info=_job_info(job, r)):
            failed += 1
        shown = files[0] if len(files) == 1 else f"{files[0]} (+{len(files) - 1} files)" if files else "(nothing)"
        print(f"{job.name}: {shown}")
    return 1 if failed else (qa.EXIT_QA if qa_failed else 0)
