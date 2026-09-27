"""audiogram assets: audio-reactive visualisation of another asset's sound.

@source may name an audio asset, a generated speech/music/sound-effect asset (verified cache) or a video
with audio. The audio is decoded once per asset (mono, cached in rc.cache); the picture at source time
src_t (the layer's media time) is a pure function of the samples, so renders are deterministic and frames
can be rendered in any order.

Analysis: a 2048-sample Hann-windowed FFT centred on the frame time, grouped into @bars log-spaced bands
(40 Hz .. min(16 kHz, Nyquist)), levels in dB mapped from -60..0 dBFS to 0..1. @smoothing s in 0..1 is a
stateless temporal smoothing: an exponential average over the previous 12 frame times with weights
(1 - s) * s^k (so it does not depend on which frames were rendered before).

Styles: bars (vertical bars rising from the bottom), line (spectrum polyline), spectrum (filled spectrum
area mirrored about the centre line), wave (oscilloscope of the waveform over one frame, lightly
low-passed by smoothing), circle (bars radiating from a circle). @color accepts colours and url() paints
(mapped to the asset box).
"""
from __future__ import annotations

import math

import cairo
import numpy as np

from .. import paint as paintmod
from ..registry import ASSET_SIZES, ASSETS, FULL, warn_once
from . import audio_source_path, decode_audio_mono

_N = 2048
_LOOKBACK = 12


def _audio(rc, asset):
    src_id = asset.get("source")
    key = ("audiogram-audio", src_id)
    if key in rc.cache:
        return rc.cache[key]
    src = rc.doc.ids.get(src_id)
    res = None
    if src is None:
        warn_once("audiogram", asset.get("id"), f"source {src_id!r} not found")
    else:
        path = audio_source_path(rc, src)
        if path:
            res = decode_audio_mono(path)
    rc.cache[key] = res
    return res


def _bands(sr: int, nbands: int):
    hi = min(16000.0, sr / 2 * 0.95)
    lo = min(40.0, hi / 4)
    edges = lo * (hi / lo) ** (np.arange(nbands + 1) / nbands)
    bins = np.clip(np.round(edges * _N / sr).astype(int), 1, _N // 2)
    for i in range(1, len(bins)):          # every band gets at least one FFT bin
        bins[i] = max(bins[i], bins[i - 1] + 1)
    return np.minimum(bins, _N // 2 + 1)


_WIN = np.hanning(_N).astype(np.float32)


def spectrum_levels(x: np.ndarray, sr: int, t: float, nbands: int) -> np.ndarray:
    """Band levels in 0..1 at time t (seconds)."""
    c = int(round(t * sr))
    seg = np.zeros(_N, np.float32)
    a, b = c - _N // 2, c + _N // 2
    ia, ib = max(0, a), min(len(x), b)
    if ib > ia:
        seg[ia - a:ib - a] = x[ia:ib]
    mag = np.abs(np.fft.rfft(seg * _WIN)) / (_N / 4)        # full-scale sine -> ~1
    edges = _bands(sr, nbands)
    lv = np.zeros(nbands, np.float32)
    for i in range(nbands):
        s, e = edges[i], max(edges[i] + 1, edges[i + 1])
        lv[i] = mag[s:e].max() if e <= len(mag) and s < len(mag) else 0.0
    db = 20 * np.log10(np.maximum(lv, 1e-6))
    return np.clip((db + 60) / 60, 0, 1)


def smoothed_levels(rc, asset, x, sr, t, nbands, smoothing, fps) -> np.ndarray:
    s = max(0.0, min(0.98, smoothing))
    frame = int(math.floor(t * fps + 1e-6))
    acc = np.zeros(nbands, np.float32)
    wsum = 0.0
    for k in range(_LOOKBACK if s > 0 else 1):
        fk = frame - k
        if fk < 0:
            break
        key = ("audiogram-lv", asset.get("source"), nbands, fk)
        lv = rc.cache.get(key)
        if lv is None:
            lv = spectrum_levels(x, sr, fk / fps, nbands)
            rc.cache[key] = lv
        wk = (1 - s) * s ** k if s > 0 else 1.0
        acc += wk * lv
        wsum += wk
    _trim_cache(rc)
    return acc / wsum if wsum > 0 else acc


def _trim_cache(rc, limit: int = 4096) -> None:
    keys = [k for k in rc.cache if isinstance(k, tuple) and k and k[0] == "audiogram-lv"]
    if len(keys) > limit:
        for k in keys[: len(keys) - limit]:
            del rc.cache[k]


@ASSETS.register("audiogram", level=FULL, note="analysis parameters pinned in assets/audiogram.py")
def render_audiogram(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    ev = rc.ev
    w, h = float(asset.get("width")), float(asset.get("height"))
    au = _audio(rc, asset)
    if au is None:
        return None
    x, sr = au
    style = ev.str(asset, "style", ctx, "bars") or "bars"
    nb = max(1, int(ev.num(asset, "bars", ctx, 48.0)))
    sm = ev.num(asset, "smoothing", ctx, 0.5)
    color = ev.str(asset, "color", ctx, "#FFFFFFFF")
    fps = float(rc.doc.fps)
    c = rc.canvas_for(M, w, h, 4)
    if c is None:
        return None
    cr = c.cr
    cr.set_antialias(cairo.ANTIALIAS_GOOD)
    if clip:
        cr.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
        cr.clip()
    if style == "wave":
        n = max(64, min(2048, int(w)))
        span = max(1.0 / fps, 0.02)
        c0 = int(round((src_t - span / 2) * sr))
        idx = c0 + np.arange(int(span * sr))
        seg = np.where((idx >= 0) & (idx < len(x)), x[np.clip(idx, 0, len(x) - 1)], 0.0)
        k = max(1, int(len(seg) * sm * 0.05))
        if k > 1:
            seg = np.convolve(seg, np.ones(k) / k, mode="same")
        ys = np.interp(np.linspace(0, len(seg) - 1, n), np.arange(len(seg)), seg) if len(seg) > 1 else np.zeros(n)
        cr.move_to(0, h / 2)
        for i, v in enumerate(ys):
            cr.line_to(w * i / (n - 1), h / 2 - float(v) * h / 2 * 0.95)
        cr.set_line_width(max(1.5, h * 0.02))
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        if paintmod.set_source(rc, cr, color, w, h, ctx, asset):
            cr.stroke()
        return c.to_buf(rc.linear)
    lv = smoothed_levels(rc, asset, x, sr, src_t, nb, sm, fps)
    if style == "bars":
        slot = w / nb
        bw = slot * 0.7
        r = min(bw / 2, h * 0.05)
        for i, v in enumerate(lv):
            bh = max(bw * 0.5, float(v) * h)
            _round_rect(cr, i * slot + (slot - bw) / 2, h - bh, bw, bh, r)
    elif style == "circle":
        cx, cy = w / 2, h / 2
        R0 = min(w, h) * 0.25
        L = min(w, h) * 0.24
        bw = max(1.0, 2 * math.pi * R0 / nb * 0.6)
        for i, v in enumerate(lv):
            a = -math.pi / 2 + 2 * math.pi * i / nb
            ln = max(bw * 0.5, float(v) * L)
            cr.save()
            cr.translate(cx, cy)
            cr.rotate(a)
            _round_rect(cr, R0, -bw / 2, ln, bw, bw / 2)
            cr.restore()
    elif style in ("line", "spectrum"):
        xs = [w * (i + 0.5) / nb for i in range(nb)]
        if style == "line":
            cr.move_to(0, h - float(lv[0]) * h)
            for xx, v in zip(xs, lv):
                cr.line_to(xx, h - float(v) * h * 0.95)
            cr.line_to(w, h - float(lv[-1]) * h * 0.95)
            cr.set_line_width(max(1.5, h * 0.02))
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            if paintmod.set_source(rc, cr, color, w, h, ctx, asset):
                cr.stroke()
            return c.to_buf(rc.linear)
        cr.move_to(0, h / 2)
        for xx, v in zip(xs, lv):
            cr.line_to(xx, h / 2 - float(v) * h / 2)
        cr.line_to(w, h / 2)
        for xx, v in zip(reversed(xs), reversed(lv)):
            cr.line_to(xx, h / 2 + float(v) * h / 2)
        cr.close_path()
    else:
        warn_once("audiogram", style)
        return None
    if paintmod.set_source(rc, cr, color, w, h, ctx, asset):
        cr.fill()
    return c.to_buf(rc.linear)


def _round_rect(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


@ASSET_SIZES.register("audiogram")
def audiogram_size(rc, asset, ctx):
    return float(asset.get("width")), float(asset.get("height"))
