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
from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

from . import color  # noqa: F401  (registers the colour-management finishing hook)
from .registry import CODECS, FEATURES, FULL, NONE, PARTIAL, warn_once
from .values import parse_fps

log = logging.getLogger("scenerender")

for _c in ("h264", "h265", "av1", "vp9", "ffv1", "apng", "webp", "png-sequence", "audio-only"):
    CODECS.declare(_c, FULL)
CODECS.declare("prores", FULL, "prores_ks; proresProfile proxy/lt/422/hq/4444/4444xq")
CODECS.declare("dnxhr", FULL, "dnxhd encoder, DNxHR profiles (profile attribute, default dnxhr_hq)")
CODECS.declare("gif", PARTIAL, "per-file 256-colour palette (palettegen/paletteuse); alpha is 1-bit")
CODECS.declare("jpeg-sequence", FULL, "no alpha")
CODECS.declare("tiff-sequence", PARTIAL, "8-bit RGB(A) TIFF")
CODECS.declare("exr-sequence", FULL, "float32 linear-light OpenEXR (ZIP16), premultiplied alpha when alpha=true")
FEATURES.declare("output:twoPass", FULL, "lossless FFV1 intermediate, then ffmpeg pass 1 + pass 2 (bitrate modes only)")
FEATURES.declare("output:maxFileSize", PARTIAL, "bitrate = 0.97 x size x 8 / duration - audio bitrate; not verified afterwards")
FEATURES.declare("output:hdrMetadata", PARTIAL, "maxCLL/maxFALL/masteringDisplay passed to x265 only")
FEATURES.declare("output:sphericalMetadata", NONE, "no spherical (360) metadata is written")
FEATURES.declare("output:embedMetadata", FULL)
FEATURES.declare("output:posters", FULL)
FEATURES.declare("output:destination:file", FULL, "copy")
for _k in ("s3", "gcs", "azure-blob", "http-put", "sftp", "webhook"):
    FEATURES.declare(f"output:destination:{_k}", NONE, "the renderer never uploads; skipped with a warning")

SEQUENCES = {"png-sequence": ".png", "jpeg-sequence": ".jpg", "tiff-sequence": ".tif", "exr-sequence": ".exr"}
_EXT_CODEC = {".mp4": ("h264", "mp4"), ".m4v": ("h264", "mp4"), ".mov": ("h264", "mov"), ".mkv": ("h264", "mkv"),
              ".webm": ("vp9", "webm"), ".mxf": ("dnxhr", "mxf"), ".gif": ("gif", None), ".apng": ("apng", None),
              ".webp": ("webp", None), ".png": ("png-sequence", None), ".jpg": ("jpeg-sequence", None),
              ".jpeg": ("jpeg-sequence", None), ".tif": ("tiff-sequence", None), ".tiff": ("tiff-sequence", None),
              ".exr": ("exr-sequence", None), ".wav": ("audio-only", "wav"), ".m4a": ("audio-only", "m4a"),
              ".mp3": ("audio-only", "mp3"), ".flac": ("audio-only", "flac"), ".aac": ("audio-only", "m4a"),
              ".ogg": ("audio-only", "ogg"), ".opus": ("audio-only", "ogg")}
_FORMATS = {"mp4": "mp4", "mov": "mov", "mkv": "matroska", "webm": "webm", "mxf": "mxf", "wav": "wav",
            "m4a": "ipod", "mp3": "mp3", "flac": "flac", "ogg": "ogg"}
_PRORES = {"proxy": 0, "lt": 1, "422": 2, "hq": 3, "4444": 4, "4444xq": 5}
_AV1_SPEED = {"ultrafast": 8, "superfast": 8, "veryfast": 7, "faster": 7, "fast": 6, "medium": 6, "slow": 4,
              "slower": 3, "veryslow": 2, "placebo": 1}
_VP9_SPEED = {"ultrafast": 8, "superfast": 7, "veryfast": 6, "faster": 5, "fast": 4, "medium": 3, "slow": 2,
              "slower": 1, "veryslow": 0, "placebo": 0}


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


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

    def a(self, name, default=None):
        v = self.attrs.get(name)
        return default if v is None else v


def _base_open_kwargs(args) -> dict:
    from .cli import _params
    return dict(path=args.scene, scale=float(getattr(args, "scale", 1.0) or 1.0), params=_params(args),
                variant=getattr(args, "variant", None), layout=getattr(args, "layout", None),
                strict=bool(getattr(args, "strict", False)), representation=getattr(args, "representation", None),
                assets_dir=getattr(args, "assets_dir", None))


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
                        t0=t0, t1=t1, open_kwargs=kw, alpha=o.get("alpha") == "true",
                        audio=o.get("audio", "true") != "false", attrs=attrs,
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


def _job_cache(job: Job) -> dict:
    c = {}
    if job.color != (None, None):
        c["output_color"] = job.color
    if job.burn:
        c["burn_captions"] = job.burn
    return c


# ====================================================================== frames
def _frame_kind(job: Job) -> str:
    if job.codec == "exr-sequence":
        return "floata" if job.alpha else "float"
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
    from .raster import linear_to_srgb, working_to_rgb8
    frame = int(round(t * float(r.doc.fps)))
    px = r.frame_linear(t, frame)
    lin = r.rc.linear
    if kind == "rgb8":
        img = working_to_rgb8(px, lin, _graded_background(r))
    elif kind == "rgba8":
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


def _winit(open_kwargs, cache, envelopes, kind, size, pad_even):
    logging.getLogger("scenerender").setLevel(logging.ERROR)
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


class FrameSource:
    """Frames for times[i] in order: from --frames-dir when present, else rendered (maybe in parallel)."""

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
            # render the first missing frame here (surfaces warnings once), the rest in workers
            first = missing[0]
            cached[first] = render_frame(self.r, self.times[first], self.kind, self.size, self.pad)
            self._save(first, cached[first])
            rest = missing[1:]
            import multiprocessing as mp
            envelopes = {}
            try:
                from .audio import envelope_table
                envelopes = envelope_table(self.r)
            except Exception as e:  # noqa: BLE001
                log.debug("no envelope table: %s", e)
            ctx = mp.get_context("spawn")
            pool = ctx.Pool(min(workers, len(rest)), initializer=_winit,
                            initargs=(self.job.open_kwargs, _job_cache(self.job), envelopes, self.kind, self.size, self.pad))
            results = iter(pool.imap(_wframe, [self.times[i] for i in rest], chunksize=1))
            rest_set = set(rest)
        else:
            rest_set = set()
        try:
            for i in range(n):
                if i in cached:
                    img = cached.pop(i)
                elif i in rest_set:
                    shape, dt, raw = next(results)
                    img = np.frombuffer(raw, np.dtype(dt)).reshape(shape)
                    self._save(i, img)
                else:
                    img = render_frame(self.r, self.times[i], self.kind, self.size, self.pad)
                    self._save(i, img)
                yield img
        finally:
            if pool is not None:
                pool.terminate()
                pool.join()


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


def _rate_args(job: Job, codec: str, duration: float, audio_bps: int) -> tuple[list[str], bool]:
    """Rate control. Returns (args, bitrate_mode)."""
    crf = int(job.a("crf", 18))
    br = job.a("bitrate")
    maxbr = job.a("maxBitrate")
    buf = job.a("bufferSize")
    if job.a("maxFileSize"):
        total = int(job.a("maxFileSize")) * 8 * 0.97 / max(duration, 1e-3)
        v = max(50_000, int(total - (audio_bps if job.audio else 0)))
        br = min(int(br), v) if br else v
        maxbr = min(int(maxbr), v) if maxbr else v
        buf = buf or 2 * v
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


def video_codec_args(job: Job, duration: float, audio_bps: int) -> tuple[list[str], str | None, bool, list[str]]:
    """-> (output args, -vf filter, bitrate_mode, extra filters for filter_complex)"""
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
    if c == "h264":
        a += ["-c:v", "libx264", "-preset", preset, "-pix_fmt", pf or "yuv420p"]
        ra, bitrate_mode = _rate_args(job, c, duration, audio_bps)
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
        ra, bitrate_mode = _rate_args(job, c, duration, audio_bps)
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
        ra, bitrate_mode = _rate_args(job, c, duration, audio_bps)
        a += ra
    elif c == "vp9":
        a += ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", str(_VP9_SPEED.get(preset, 3)), "-row-mt", "1",
              "-pix_fmt", ("yuva420p" if job.alpha else (pf or "yuv420p")), "-g", str(gop)]
        ra, bitrate_mode = _rate_args(job, c, duration, audio_bps)
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
        q = max(0, min(100, 100 - 2 * int(job.a("crf", 18)) + 16))
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
            return ["-c:a", "libopus", "-b:a", str(br)]
    name = {"aac": "aac", "opus": "libopus", "mp3": "libmp3lame", "flac": "flac", "pcm": "pcm_s24le",
            "vorbis": "libvorbis"}.get(ac, ac)
    if cont == "webm" and name not in ("libopus", "libvorbis"):
        warn_once("output", "webm-audio", f"{ac} is not allowed in WebM; using Opus")
        name = "libopus"
    if cont == "mxf" and not name.startswith("pcm"):
        name = "pcm_s24le"
    out = ["-c:a", name]
    if not name.startswith("pcm") and name != "flac":
        out += ["-b:a", str(br)]
    return out


def metadata_args(doc, job: Job) -> list[str]:
    if job.a("embedMetadata", "true") == "false":
        return []
    md = doc.section("metadata")
    if md is None:
        return []
    out = []
    keymap = {"title": ["title"], "author": ["artist", "author"], "description": ["description", "comment"],
              "copyright": ["copyright"], "keywords": ["keywords"], "created": ["creation_time", "date"],
              "generator": ["encoder_generator"], "revision": ["revision"], "language": ["language"]}
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
            (r.doc.ids.get(l.get("asset")) is not None and r.doc.ids.get(l.get("asset")).get("hasAudio") == "true")
            for l in r.doc.root.iter("layer")):
        return None, 24
    m = mixer_for(r)
    if not m.has_audio():
        return None, m.bits
    t1 = job.t0 + n_frames / float(job.fps) if job.codec != "audio-only" else job.t1
    t0p = time.perf_counter()
    pcm = m.render(job.t0, t1)
    dither = (m.master_el.get("dither", "true") != "false") if m.master_el is not None else True
    path = os.path.join(tmpdir, "mix.wav")
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


def run_job(r, job: Job, args) -> list[str]:
    """Render one job; returns the files written."""
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
    jobs_n = getattr(args, "jobs", 0)
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
            cmd = [ffmpeg_exe(), "-y", "-v", "error", "-nostdin", "-i", audio_path, *audio_codec_args(job, bits),
                   *metadata_args(doc, job)]
            fmt = _FORMATS.get(job.container or "")
            if fmt:
                cmd += ["-f", fmt]
            _run_ffmpeg(cmd + [job.path])
            written.append(job.path)
        elif job.codec in SEQUENCES:
            src = FrameSource(r, job, times, kind, size, False, jobs_n, frames_dir, first_index)
            written += _write_sequence(job, src, first_index, progress, n, tmp)
            if audio_path:
                prefix = os.path.basename(job.path.split("%")[0]).rstrip("_-.")
                wav = os.path.join(out_dir, (prefix if prefix and prefix != "frame" else "audio") + ".wav")
                shutil.copyfile(audio_path, wav)
                written.append(wav)
        else:
            src = FrameSource(r, job, times, kind, size, pad_even, jobs_n, frames_dir, first_index)
            written += _encode_video(r, job, src, times, kind, size, pad_even, audio_path, bits, progress, tmp)
    log.info("%s: %s in %.1fs", job.name, "audio" if job.codec == "audio-only" else f"{n} frames", time.perf_counter() - t_start)
    return written


def _frame_dims(r, size, pad_even):
    w, h = size if size else (r.rc.width, r.rc.height)
    if pad_even:
        w, h = w + w % 2, h + h % 2
    return w, h


def _encode_video(r, job, src, times, kind, size, pad_even, audio_path, bits, progress, tmp) -> list[str]:
    duration = len(times) / float(job.fps)
    w, h = _frame_dims(r, size, pad_even)
    vargs, vf, bitrate_mode, _ = video_codec_args(job, duration, audio_bitrate(job, duration) if audio_path else 0)
    inp = ["-f", "rawvideo", "-pix_fmt", _pix_in(kind), "-s", f"{w}x{h}", "-r", _fps_str(job.fps), "-i", "-"]
    ain = ["-i", audio_path] if audio_path else []
    maps = ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio_path else [])
    fargs: list[str] = []
    if job.codec == "gif":
        trans = ":reserve_transparent=1" if job.alpha else ""
        fargs = ["-filter_complex", f"[0:v]split[a][b];[a]palettegen=stats_mode=diff{trans}[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle[v]"]
        maps = ["-map", "[v]"]
        ain = []
    elif vf:
        fargs = ["-vf", vf]
    aargs = audio_codec_args(job, bits, duration) if audio_path else []
    cont = job.container
    fmt = ["-f", _FORMATS[cont]] if cont in _FORMATS and cont not in ("wav", "m4a", "mp3", "flac", "ogg") else []
    mov = (cont in ("mp4", "mov")) or (not cont and os.path.splitext(job.path)[1].lower() in (".mp4", ".mov", ".m4v"))
    movflags = []
    if mov:
        flags = []
        if job.a("faststart", "true") != "false":
            flags.append("+faststart")
        md = r.doc.section("metadata")
        if md is not None and any(isinstance(m.tag, str) and m.tag == "meta" for m in md):
            flags.append("+use_metadata_tags")
        if flags:
            movflags = ["-movflags", "".join(flags)]
    meta = metadata_args(r.doc, job) if job.codec not in ("gif",) else []
    two_pass = job.a("twoPass", "false") in ("true", True) and bitrate_mode and job.codec in ("h264", "h265", "av1", "vp9")
    if job.a("twoPass", "false") in ("true", True) and not two_pass:
        warn_once("output", f"twoPass-{job.name}", "twoPass needs a bitrate (bitrate/maxFileSize); encoded in one pass")
    ff = ffmpeg_exe()
    if not two_pass:
        cmd = [ff, "-y", "-v", "error", "-nostdin", *inp, *ain, *fargs, *maps, *vargs, *aargs, *movflags, *meta, *fmt]
        if audio_path:
            cmd += ["-shortest"] if job.codec in ("gif", "apng", "webp") else []
        _run_ffmpeg(cmd + [job.path], iter(src), progress)
        return [job.path]
    # two-pass: lossless intermediate, then pass 1 and pass 2 from it
    inter = os.path.join(tmp, "intermediate.mkv")
    ipix = {"rgb8": "bgr0", "rgba8": "bgra", "rgb16": "rgb48le", "rgba16": "rgba64le"}.get(kind, "bgr0")
    _run_ffmpeg([ff, "-y", "-v", "error", "-nostdin", *inp, "-c:v", "ffv1", "-level", "3", "-pix_fmt", ipix, inter],
                iter(src), progress)
    log_prefix = os.path.join(tmp, "passlog")
    base = [ff, "-y", "-v", "error", "-nostdin", "-i", inter, *ain, *fargs, *maps, *vargs]
    if job.codec == "h265":
        i = base.index("-x265-params") + 1
        p1 = base.copy()
        p1[i] = base[i] + f":pass=1:stats={log_prefix}.log"
        p2 = base.copy()
        p2[i] = base[i] + f":pass=2:stats={log_prefix}.log"
        _run_ffmpeg(p1 + ["-an", "-f", "null", os.devnull])
        _run_ffmpeg(p2 + [*aargs, *movflags, *meta, *fmt, job.path])
    else:
        _run_ffmpeg(base + ["-pass", "1", "-passlogfile", log_prefix, "-an", "-f", "null", os.devnull])
        _run_ffmpeg(base + ["-pass", "2", "-passlogfile", log_prefix, *aargs, *movflags, *meta, *fmt, job.path])
    return [job.path]


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
    quality = int(round(float(job.a("quality", 0.95)) * 100)) if job.a("quality") else 95
    for i, img in enumerate(src):
        p = pattern % (first_index + i)
        if job.codec == "jpeg-sequence":
            Image.fromarray(img[..., :3]).save(p, quality=quality)
        elif job.codec == "tiff-sequence":
            Image.fromarray(img).save(p, compression="tiff_deflate")
        else:
            Image.fromarray(img).save(p, compress_level=4)
        written.append(p)
        progress.step(i + 1)
    return written


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


def deliver(job: Job, files: list[str]) -> None:
    if job.el is None:
        return
    for d in job.el:
        if not isinstance(d.tag, str) or d.tag != "destination":
            continue
        kind, uri = d.get("kind"), d.get("uri", "")
        if kind != "file":
            warn_once("destination", f"{job.name}:{kind}", f"{kind} destination {uri!r}: the renderer never uploads; skipped")
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


# ====================================================================== entry point
def render_outputs(renderer, args) -> int:
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
    failed = 0
    for job in jobs:
        key = tuple(sorted((k, repr(v)) for k, v in job.open_kwargs.items()))
        r = renderers.get(key)
        if r is None:
            r = renderer if job.open_kwargs == given else open_renderer(job.open_kwargs)
            renderers = {key: r}           # keep one renderer alive at a time
        r.rc.cache.pop("output_color", None)
        r.rc.cache.pop("burn_captions", None)
        r.rc.cache.update(_job_cache(job))
        try:
            files = run_job(r, job, args)
        except (RuntimeError, OSError, ValueError) as e:
            print(f"{job.name}: {e}", file=sys.stderr)
            failed += 1
            continue
        if job.el is not None:
            for st in job.el:
                if isinstance(st.tag, str) and st.tag in ("poster", "thumbnail"):
                    p = write_still(r, st, job)
                    if p:
                        files.append(p)
        files += write_captions(r.doc, job)
        deliver(job, files)
        shown = files[0] if len(files) == 1 else f"{files[0]} (+{len(files) - 1} files)" if files else "(nothing)"
        print(f"{job.name}: {shown}")
    return 1 if failed else 0
