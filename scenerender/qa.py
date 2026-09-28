"""Delivery QA: metadata/accessibility checks and safe-area enforcement.

    FlashDetector(fps, width, height, space, transfer).feed(frame) ... .findings(mode)
    static_checks(renderer, job) -> list[Finding]          (captions, audio description, safe area, contrast)
    check(scene, ...) -> (findings, report text)            (`scenerender check SCENE`)

render_outputs (output.py) runs the static checks before encoding each output and the flash
detector on the frames as they stream to the encoder; any finding at level "error" fails that
output (nothing is delivered) and the command exits with status 4.

Pinned rules:

* flashCheck (WCAG 2.2 SC 2.3.1 "general flash and red flash thresholds", ITU-R BT.1702): frames
  are decoded to display light with the output's transfer (PQ/HLG relative to the 203 cd/m^2
  reference white, clipped to 1) and reduced by area averaging to <= 192 px wide. Per pixel a
  transition is an opposing change from the last extreme of >= 0.1 relative luminance
  (Rec.709/output-primaries Y) with the darker state below 0.8; red transitions use
  max(0, (R - G - B) x 320) changing by > 20 where the redder state is saturated red
  (R / (R + G + B) >= 0.8), on linear R, G, B. A frame carries a transition when those pixels
  cover >= 25 % of some window of the 10-degree field, 341 x 256 px of a 1024 x 768 view (scaled
  to the frame). Flashes = transitions // 2 in every one-second window; more than 3 fails.
* contrastCheck: every text layer (a layer whose asset is <text>) at the middle of its longest
  visible run inside the output range, and every burned caption page at its midpoint. Glyph
  pixels are the node/page rendered alone (alpha >= 0.5); the frame is rendered with and without
  the node (visible="false" for the pass; captions hook removed for pages); each glyph pixel gives
  a WCAG contrast ratio (sRGB relative luminance of the delivered 8-bit frame) and the median must
  reach @minContrast.
* safeArea enforce: the output's safe area (layout/@safeArea, else project/@safeArea) must contain
  the rendered extent (alpha > 2 %) of every text layer, caption page (captionTrack/@safeArea when
  given) and node tagged "cta" or "logo", sampled like contrastCheck, with a tolerance of
  max(1 px, 0.2 %) of the frame. Nodes inside <symbols> are checked through their instances'
  frames only (not individually).
* requireCaptions: an output passes when it burns a caption track or writes sidecars (its
  @captions or any track with mode sidecar/both).
* audioDescription: the IDREF must be an audioTrack or an audio asset. An unmuted audioTrack in
  the mix is delivered in the output's audio; otherwise (muted, audio="false", audio-only asset,
  a picture-only codec) the track is written as a sidecar WAV next to the output
  (<stem>.audio-description.wav: the asset from clipIn, delayed to its start, the output range).
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, replace

import numpy as np

from .registry import FEATURES, FULL
from .values import parse_bool

log = logging.getLogger("scenerender")

FEATURES.declare("accessibility:flashCheck", FULL, "WCAG 2.3.1 / BT.1702 general + red flash analysis on the delivered frames")
FEATURES.declare("accessibility:contrastCheck", FULL, "WCAG contrast of burned text and captions against their rendered background")
FEATURES.declare("accessibility:requireCaptions", FULL)
FEATURES.declare("accessibility:audioDescription", FULL, "track verified in the mix, else written as a sidecar WAV")
FEATURES.declare("safeArea:enforce", FULL, "text layers, caption pages and cta/logo-tagged nodes checked against the safe area")

EXIT_QA = 4


@dataclass
class Finding:
    check: str
    level: str               # "warn" | "error"
    message: str
    t: float | None = None

    def __str__(self) -> str:
        at = f" at {self.t:.2f}s" if self.t is not None else ""
        return f"[{self.level}] {self.check}{at}: {self.message}"


def report(name: str, findings: list[Finding]) -> str:
    if not findings:
        return f"{name}: QA passed"
    errs = sum(f.level == "error" for f in findings)
    head = f"{name}: QA {'FAILED' if errs else 'warnings'} ({errs} errors, {len(findings) - errs} warnings)"
    return "\n".join([head] + [f"  {f}" for f in findings])


def accessibility(doc) -> dict:
    """Effective settings of the (first) metadata/accessibility element (schema defaults otherwise)."""
    md = doc.section("metadata")
    acc = None
    if md is not None:
        acc = next((c for c in md if isinstance(c.tag, str) and c.tag == "accessibility"), None)
    g = (lambda k, d: acc.get(k, d)) if acc is not None else (lambda k, d: d)
    return {"present": acc is not None, "flash": g("flashCheck", "warn") if acc is not None else "off",
            "contrast": g("contrastCheck", "off"), "min_contrast": float(g("minContrast", "4.5")),
            "require_captions": parse_bool(g("requireCaptions", "false")), "audio_description": g("audioDescription", None)}


# ====================================================================== flash detection
def _srgb_lin(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


class FlashDetector:
    """Streaming WCAG 2.3.1 / BT.1702 flash analysis (see the module docstring)."""

    MAX_W = 192

    def __init__(self, fps: float, space: str = "srgb", transfer: str = "srgb"):
        from . import color
        self.fps = float(fps)
        self.tf = transfer if transfer not in (None, "auto") else color.SPACES.get(space or "srgb", (None, "srgb"))[1]
        prim = color.primaries_of(space or "srgb")
        self.wts = (color.to_xyz_d65(prim)[1] if prim else np.array([0.2126, 0.7152, 0.0722])).astype(np.float32)
        self.state: dict = {}
        self.events: dict[str, list[int]] = {"general": [], "red": []}
        self.n = 0

    def _linear(self, img: np.ndarray) -> np.ndarray:
        from . import color
        if img.dtype == np.uint8 and self.tf == "srgb":
            # 256 codes: the decode below as a table, the same values from one gather per channel.
            lut = FlashDetector.__dict__.get("_lut8")
            if lut is None:
                lut = np.clip(_srgb_lin(np.arange(256, dtype=np.float32) / 255.0), 0, 1).astype(np.float32)
                FlashDetector._lut8 = lut
            lin = lut[img[..., :3]]
            if img.shape[-1] == 4:
                lin = np.clip(lin * (img[..., 3:4].astype(np.float32) / 255.0), 0, 1).astype(np.float32)
            return lin
        if img.dtype == np.uint8:
            v = img[..., :3].astype(np.float32) / 255.0
        elif img.dtype == np.uint16:
            v = img[..., :3].astype(np.float32) / 65535.0
        else:
            return np.clip(img[..., :3].astype(np.float32), 0, 1)
        if self.tf == "srgb":
            lin = _srgb_lin(v)
        else:
            lin = color.decode(v, self.tf)
        if img.shape[-1] == 4 and img.dtype != np.float32:
            lin = lin * (img[..., 3:4].astype(np.float32) / (255.0 if img.dtype == np.uint8 else 65535.0))
        return np.clip(lin, 0, 1).astype(np.float32)

    def _reduce(self, lin: np.ndarray) -> np.ndarray:
        h, w = lin.shape[:2]
        f = max(1, -(-w // self.MAX_W))
        h2, w2 = h // f * f or h, w // f * f or w
        x = lin[:h2, :w2]
        if f > 1 and h2 >= f:
            x = x.reshape(h2 // f, f, w2 // f, f, 3).mean((1, 3))
        return x

    def feed(self, img: np.ndarray) -> None:
        x = self._reduce(self._linear(img))
        L = x @ self.wts
        s = x.sum(-1)
        red = np.maximum(0.0, (x[..., 0] - x[..., 1] - x[..., 2]) * 320.0)
        sat = np.where(s > 0, x[..., 0] / np.maximum(s, 1e-9), 0) >= 0.8
        h, w = L.shape
        self.ww, self.wh = max(1, round(w * 341 / 1024)), max(1, round(h * 256 / 768))
        for kind, v, th in (("general", L, 0.1), ("red", red, 20.0)):
            st = self.state.get(kind)
            if st is None:
                self.state[kind] = {"ref": v.copy(), "dir": np.zeros(v.shape, np.int8), "sat": sat.copy()}
                continue
            ref, d = st["ref"], st["dir"]
            diff = v - ref
            if kind == "general":
                up, down = diff >= th, -diff >= th
                ok = np.minimum(ref, v) < 0.8
            else:
                up, down = diff > th, -diff > th
                ok = np.where(v > ref, sat, st["sat"])
            rise = up & (d <= 0) & ok
            fall = down & (d >= 0) & ok
            event = rise | fall
            ext = (up & (d > 0)) | (down & (d < 0))          # further in the same direction: extend the extreme
            upd = event | ext
            ref[upd] = v[upd]
            st["sat"][upd] = sat[upd]
            d[rise] = 1
            d[fall] = -1
            if event.any() and self._area(event):
                self.events[kind].append(self.n)
        self.n += 1

    def _area(self, mask: np.ndarray) -> bool:
        need = 0.25 * self.ww * self.wh
        if mask.sum() < need:
            return False
        ii = np.pad(mask.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        wh, ww = min(self.wh, mask.shape[0]), min(self.ww, mask.shape[1])
        box = ii[wh:, ww:] - ii[:-wh, ww:] - ii[wh:, :-ww] + ii[:-wh, :-ww]
        return bool(box.max() >= 0.25 * wh * ww)

    def violations(self) -> list[tuple[str, float, float, int]]:
        """(kind, t_start, t_end, max flashes per second) for every run of failing one-second windows."""
        out = []
        win = max(1, int(round(self.fps)))
        for kind, ev in self.events.items():
            ev = np.asarray(ev)
            run = None
            for i, f0 in enumerate(ev):
                n = int(np.searchsorted(ev, f0 + win, "left") - i)
                flashes = n // 2
                if flashes > 3:
                    t0, t1 = f0 / self.fps, min(f0 + win, self.n) / self.fps
                    if run and t0 <= run[2]:
                        run = (kind, run[1], t1, max(run[3], flashes))
                    else:
                        if run:
                            out.append(run)
                        run = (kind, t0, t1, flashes)
            if run:
                out.append(run)
        return out

    def findings(self, mode: str, t0: float = 0.0) -> list[Finding]:
        if mode == "off":
            return []
        return [Finding("flashCheck", mode, f"{k} flashes: {n} per second (more than 3) from {a + t0:.2f}s to {b + t0:.2f}s", a + t0)
                for k, a, b, n in self.violations()]


# ====================================================================== static checks
def _is_text_layer(doc, el) -> bool:
    if el.tag != "layer" or not el.get("asset"):
        return False
    a = doc.ids.get(el.get("asset"))
    return a is not None and a.tag == "text"


def _in_symbols(el) -> bool:
    return any(isinstance(a.tag, str) and a.tag in ("symbols", "assets") for a in el.iterancestors())


def _node_ctx_at(rc, el, T: float):
    """Ctx for el's parent clock at composition time T, or None when an ancestor hides it."""
    from .evaluator import Ctx
    from .nodes.core import child_ctx, _repeat_vars
    ctx = Ctx(t=T, comp_t=T)
    chain = [a for a in el.iterancestors() if isinstance(a.tag, str) and a.tag not in ("composition", "scene")][::-1]
    for a in chain:
        if not rc.active(a, ctx):
            return None
        ctx = _repeat_vars(a, child_ctx(rc, a, rc.enter_node(a, ctx)))
    if not rc.active(el, ctx):
        return None
    return ctx


def _sample_time(rc, el, t0: float, t1: float, step: float = 0.25):
    """Middle of el's longest visible run in [t0, t1) (composition time)."""
    ts = np.arange(t0, t1, step) if t1 > t0 else np.array([t0])
    vis = [(_node_ctx_at(rc, el, float(t)) is not None) for t in ts]
    best, cur = None, None
    for t, v in zip(ts, vis):
        if v:
            cur = (cur[0], t) if cur else (t, t)
            if best is None or cur[1] - cur[0] > best[1] - best[0]:
                best = cur
        else:
            cur = None
    if best is None:
        return None
    return float((best[0] + best[1]) / 2)


def _node_alone(rc, el, T: float):
    """(alpha (h, w) in frame pixels) of el rendered alone at T, or None."""
    ctx = _node_ctx_at(rc, el, T)
    if ctx is None:
        return None
    out = rc.render_node_at(el, T, ctx, effects=True)
    if out is None:
        return None
    return _frame_alpha(rc, out.buf, out.opacity)


def _frame_alpha(rc, buf, opacity: float = 1.0) -> np.ndarray:
    fr = buf.crop_to(rc.frame_rect).expand_to(rc.frame_rect)
    return np.clip(fr.px[..., 3] * opacity, 0, 1)


def _wcag_lum(rgb8: np.ndarray) -> np.ndarray:
    lin = _srgb_lin(rgb8.astype(np.float32) / 255.0)
    return lin @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def contrast_ratio(a_lum, b_lum):
    hi, lo = np.maximum(a_lum, b_lum), np.minimum(a_lum, b_lum)
    return (hi + 0.05) / (lo + 0.05)


def _frame(r, T: float) -> np.ndarray:
    return r.frame_rgb(T)


def _without(r, el, T: float) -> np.ndarray:
    """The frame at T with el left out (the compositor's exclude set; the document is not touched)."""
    old = r.rc.exclude
    r.rc.exclude = old | {el}
    try:
        return _frame(r, T)
    finally:
        r.rc.exclude = old


def _without_captions(r, T: float) -> np.ndarray:
    hook = r.rc.hooks.pop("captions", None)
    try:
        return _frame(r, T)
    finally:
        if hook is not None:
            r.rc.hooks["captions"] = hook


def _contrast(r, alpha, T, with_img, without_img, label, minc, mode) -> list[Finding]:
    mask = alpha >= 0.5
    if mask.sum() < 4:
        return []
    ratio = contrast_ratio(_wcag_lum(with_img[mask]), _wcag_lum(without_img[mask]))
    med = float(np.median(ratio))
    if med + 1e-6 < minc:
        return [Finding("contrastCheck", mode, f"{label}: contrast {med:.2f}:1 below {minc:g}:1", T)]
    return []


def _safe_rect_px(rc, sa_el):
    from .safe_areas import insets
    l, t, r_, b = insets(sa_el, rc.doc.width, rc.doc.height)
    W, H = rc.width, rc.height
    return l * W, t * H, W * (1 - r_), H * (1 - b)


def _outside(alpha, rect, W, H) -> str | None:
    ys, xs = np.nonzero(alpha > 0.02)
    if not len(xs):
        return None
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    tol = max(1.0, 0.002 * max(W, H))
    sx0, sy0, sx1, sy1 = rect
    sides = [n for n, bad in (("left", x0 < sx0 - tol), ("top", y0 < sy0 - tol), ("right", x1 > sx1 + tol),
                              ("bottom", y1 > sy1 + tol)) if bad]
    if not sides:
        return None
    return (f"extent ({x0},{y0})-({x1},{y1}) px leaves the safe area "
            f"({sx0:.0f},{sy0:.0f})-({sx1:.0f},{sy1:.0f}) on the {'/'.join(sides)}")


def _effective_safe_area(doc):
    sa_id = doc.project.get("safeArea")
    lay = doc.ids.get(doc.layout) if doc.layout else None
    if lay is not None and lay.get("safeArea"):
        sa_id = lay.get("safeArea")
    return doc.ids.get(sa_id) if sa_id else None


def caption_tracks_for(doc, captions: list[str] | None, burn: str | None) -> tuple[list, list]:
    """(burned tracks, sidecar tracks) an output delivers."""
    sec = doc.section("captions")
    tracks = [t for t in sec if isinstance(t.tag, str) and t.tag == "captionTrack"] if sec is not None else []
    burned = [t for t in tracks if t.get("id") == burn] if burn else [t for t in tracks if t.get("mode", "burn") in ("burn", "both")]
    side = [t for t in tracks if t.get("mode") in ("sidecar", "both")]
    if captions:
        side = [t for t in tracks if t.get("id") in captions]
    return burned, side


def static_checks(r, *, t0: float, t1: float, captions=None, burn=None, audio: bool = True,
                  out_path: str | None = None, picture_only: bool = False, write: bool = True) -> tuple[list[Finding], list[str]]:
    """-> (findings, sidecar files written)."""
    doc, rc = r.doc, r.rc
    acc = accessibility(doc)
    findings: list[Finding] = []
    written: list[str] = []
    burned, side = caption_tracks_for(doc, captions, burn)
    if acc["require_captions"] and not burned and not side:
        findings.append(Finding("requireCaptions", "error", "no caption track is burned in or written as a sidecar"))
    if acc["audio_description"]:
        f, w = _audio_description(doc, acc["audio_description"], t0, t1, audio and not picture_only, out_path, write)
        findings += f
        written += w
    sa = _effective_safe_area(doc)
    enforce = sa.get("enforce", "warn") if sa is not None else "off"
    contrast = acc["contrast"]
    if enforce == "off" and contrast == "off":
        return findings, written
    W, H = rc.width, rc.height
    rect = _safe_rect_px(rc, sa) if sa is not None else None
    minc = acc["min_contrast"]
    comp = doc.section("composition")
    nodes = [el for el in comp.iter() if isinstance(el.tag, str) and not _in_symbols(el)] if comp is not None else []
    for el in nodes:
        is_text = _is_text_layer(doc, el)
        tags = set((el.get("tags") or "").split())
        tagged = bool(tags & {"cta", "logo"})
        if not (is_text and (contrast != "off" or enforce != "off")) and not (tagged and enforce != "off"):
            continue
        T = _sample_time(rc, el, t0, t1)
        if T is None:
            continue
        alpha = _node_alone(rc, el, T)
        if alpha is None:
            continue
        label = f"{el.tag} {el.get('id') or el.get('asset') or ''}".strip()
        if enforce != "off" and rect is not None:
            msg = _outside(alpha, rect, W, H)
            if msg:
                findings.append(Finding("safeArea", enforce, f"{label}: {msg}", T))
        if is_text and contrast != "off":
            findings += _contrast(r, alpha, T, _frame(r, T), _without(r, el, T), label, minc, contrast)
    if burned:
        from . import captions as C
        for track in burned:
            pages = C.track_pages(doc, track)
            trect = rect
            if track.get("safeArea") and track.get("safeArea") in doc.ids:
                trect = _safe_rect_px(rc, doc.ids[track.get("safeArea")])
            for p in pages:
                if p.end <= t0 or p.start >= t1:
                    continue
                T = (max(p.start, t0) + min(p.end, t1)) / 2
                buf = C.render_page(rc, track, p, T)
                if buf is None:
                    continue
                alpha = _frame_alpha(rc, buf)
                label = f"caption {track.get('id')} page at {p.start:.2f}s"
                if enforce != "off" and trect is not None:
                    msg = _outside(alpha, trect, W, H)
                    if msg:
                        findings.append(Finding("safeArea", enforce, f"{label}: {msg}", T))
                if contrast != "off":
                    findings += _contrast(r, alpha, T, _frame(r, T), _without_captions(r, T), label, minc, contrast)
    return findings, written


def _audio_description(doc, ref: str, t0: float, t1: float, audio_out: bool, out_path, write) -> tuple[list, list]:
    el = doc.ids.get(ref)
    if el is None:
        return [Finding("audioDescription", "error", f"audioDescription {ref!r} does not exist")], []
    if el.tag == "audioTrack":
        mixed = not parse_bool(el.get("mute", "false")) and audio_out
        if mixed:
            return [], []
        asset = doc.ids.get(el.get("asset"))
        start, clip_in = float(el.get("start", 0) or 0), float(el.get("clipIn", 0) or 0)
    elif el.tag == "audio" or (el.tag == "video" and parse_bool(el.get("hasAudio"))):
        asset, start, clip_in = el, 0.0, 0.0
    else:
        return [Finding("audioDescription", "error", f"audioDescription {ref!r} is a <{el.tag}>, not an audio track")], []
    if asset is None or not asset.get("src"):
        return [Finding("audioDescription", "error", f"audioDescription {ref!r} has no audio source")], []
    src = doc.resolve_path(asset.get("src"))
    if not os.path.exists(src):
        return [Finding("audioDescription", "error", f"audioDescription source {src} not found")], []
    if not write or not out_path:
        return [], []
    stem = os.path.splitext(out_path.split("%")[0].rstrip("_-."))[0] or "output"
    dst = stem + ".audio-description.wav"
    from .output import ffmpeg_exe
    delay_ms = max(0, int(round((start - t0) * 1000)))
    skip = clip_in + max(0.0, t0 - start)
    cmd = [ffmpeg_exe(), "-y", "-v", "error", "-nostdin", "-ss", f"{skip:.6f}", "-i", src, "-vn",
           "-af", f"adelay={delay_ms}:all=1,apad", "-t", f"{max(t1 - t0, 0.01):.6f}", "-c:a", "pcm_s24le", dst]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return [Finding("audioDescription", "error", f"could not write the sidecar: {p.stderr.strip()[-300:]}")], []
    return [], [dst]


# ====================================================================== `scenerender check`
def check(path: str, *, scale: float = 0.25, t0: float | None = None, t1: float | None = None,
          open_kwargs: dict | None = None) -> tuple[list[Finding], str]:
    """Quick low-resolution QA pass over every output (or the default frame when none)."""
    from .output import Job, jobs_from_args, open_renderer, render_frame, _job_cache
    import argparse
    kw = dict(open_kwargs or {})
    kw.setdefault("path", path)
    args = argparse.Namespace(scene=path, scale=scale, param=[], variant=kw.get("variant"), layout=kw.get("layout"),
                              strict=False, representation=None, assets_dir=kw.get("assets_dir"), out=None, output=[],
                              fps=None, t0=t0, t1=t1, crf=None)
    if kw.get("params"):
        args.param = [f"{k}={v}" for k, v in kw["params"].items()]
    base = open_renderer(dict(path=path, scale=scale, params=kw.get("params") or {}, variant=kw.get("variant"),
                              layout=kw.get("layout"), strict=False, representation=None, assets_dir=kw.get("assets_dir")))
    jobs = jobs_from_args(base, args) or [Job(name="default", path="", codec="h264", fps=base.doc.fps,
                                              t0=t0 or 0.0, t1=t1 if t1 is not None else base.doc.duration,
                                              open_kwargs=dict(path=path, scale=scale))]
    all_f: list[Finding] = []
    lines = []
    for job in jobs:
        r = open_renderer(job.open_kwargs) if job.el is not None else base
        r.rc.cache.update(_job_cache(job))
        f, _ = static_checks(r, t0=job.t0, t1=job.t1, captions=job.captions, burn=job.burn, audio=job.audio,
                             out_path=None, picture_only=job.codec in ("gif", "apng", "webp"), write=False)
        acc = accessibility(r.doc)
        if acc["flash"] != "off":
            det = FlashDetector(float(job.fps), *job.color)
            n = int(np.ceil((job.t1 - job.t0) * float(job.fps) - 1e-9))
            for i in range(n):
                det.feed(render_frame(r, job.t0 + i / float(job.fps), "rgb8", None, False))
            f += det.findings(acc["flash"], job.t0)
        all_f += f
        lines.append(report(job.name, f))
    return all_f, "\n".join(lines)
