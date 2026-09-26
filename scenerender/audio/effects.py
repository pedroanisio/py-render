"""audioEffect handlers:  fn(fx: FxContext, x: (n, ch) float32) -> (n, ch) float32.

`enabled` and `mix` are handled by the caller (out = (1 - mix)*dry + mix*processed).
Time-based effects (delay, reverb, chorus) return dry + wet, so mix="1" keeps the dry signal.
Parameters are the audioEffect attributes, or <param name value> children, evaluated once
at the start of the processed segment (only `gain` follows automation sample-accurately).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from ..registry import AUDIO_EFFECTS, FULL, NONE, PARTIAL, warn_once
from . import dsp


@dataclass
class FxContext:
    el: Any
    sr: float
    t0: float                       # composition time of the first sample
    get: Callable                   # get(name, default) -> float (attribute/param, evaluated at t0)
    curve: Callable                 # curve(name, default, n) -> per-sample values (automation)
    key: Callable                   # key(id, n) -> sidechain signal aligned to x, or None
    seed: int = 0

    def num(self, name, default):
        v = self.get(name, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default


def _gain_db_curve(fx: FxContext, n: int):
    return fx.curve("gain", 0.0, n)


# ---------------------------------------------------------------- level
@AUDIO_EFFECTS.register("gain", level=FULL, note="static or automated gain in dB")
def fx_gain(fx: FxContext, x):
    g = dsp.db_to_lin(_gain_db_curve(fx, x.shape[0]))
    return (x * np.asarray(g, np.float32).reshape(-1, 1)).astype(np.float32)


# ---------------------------------------------------------------- filters
@AUDIO_EFFECTS.register("eq", level=FULL, note="RBJ biquads per band (peak, shelves, notch, high/low-pass)")
def fx_eq(fx: FxContext, x):
    sections = []
    bands = [b for b in fx.el if isinstance(b.tag, str) and b.tag == "band"]
    for b in bands:
        kind = b.get("kind", "peak")
        sections.append(dsp.biquad(kind, float(b.get("frequency")), fx.sr, float(b.get("q", 0.707)),
                                   float(b.get("gain", 0))))
    if not bands and fx.get("frequency", None) is not None:
        sections.append(dsp.biquad("peak", fx.num("frequency", 1000), fx.sr, fx.num("q", 0.707), fx.num("gain", 0)))
        return dsp.filter_sections(x, sections)
    y = dsp.filter_sections(x, sections)
    g = fx.num("gain", 0.0)
    return y * np.float32(10 ** (g / 20)) if g else y


def _pass(kind, default_f):
    def fn(fx: FxContext, x):
        f = fx.num("frequency", default_f)
        order = int(fx.num("order", 2))
        if fx.get("q", None) is not None:
            return dsp.filter_sections(x, [dsp.biquad(kind, f, fx.sr, fx.num("q", 0.7071))])
        return dsp.filter_sections(x, dsp.butter_sections(kind, f, fx.sr, order))
    return fn


AUDIO_EFFECTS.register("highpass", level=FULL, note="Butterworth 12 dB/oct (param order/q)")(_pass("highpass", 80.0))
AUDIO_EFFECTS.register("lowpass", level=FULL, note="Butterworth 12 dB/oct (param order/q)")(_pass("lowpass", 12000.0))


@AUDIO_EFFECTS.register("telephone", level=FULL, note="300-3400 Hz band-pass (24 dB/oct), light saturation, mono")
def fx_telephone(fx: FxContext, x):
    lo, hi = fx.num("frequency", 300.0), 3400.0
    y = dsp.filter_sections(x, dsp.butter_sections("highpass", lo, fx.sr, 4) + dsp.butter_sections("lowpass", hi, fx.sr, 4))
    drive = 1 + 4 * fx.num("amount", 0.5)
    y = np.tanh(drive * y) / math.tanh(drive)
    m = y.mean(axis=1, keepdims=True)
    return np.repeat(m, x.shape[1], axis=1).astype(np.float32)


# ---------------------------------------------------------------- dynamics
def _detector_db(sig, sr):
    return dsp.lin_to_db(np.sqrt(dsp.moving_average(dsp.block_ms(sig, sr), 5)))


def _apply_block_gain(x, g_db, sr):
    g = dsp.gain_to_samples(dsp.db_to_lin(g_db), x.shape[0], sr)
    return (x * g[:, None]).astype(np.float32)


@AUDIO_EFFECTS.register("compressor", level=FULL, note="RMS (5 ms) detector, soft knee, attack/release, gain = makeup dB, sidechain")
def fx_compressor(fx: FxContext, x):
    key = fx.key(fx.get("sidechain", None), x.shape[0]) if fx.get("sidechain", None) else None
    det = _detector_db(key if key is not None else x, fx.sr)
    gr = dsp.compressor_gain_db(det, fx.num("threshold", -18), fx.num("ratio", 4), fx.num("knee", 3))
    gr = dsp.smooth_gain_db(gr, fx.num("attack", 0.01), fx.num("release", 0.1))
    return _apply_block_gain(x, gr + fx.num("gain", 0.0), fx.sr)


@AUDIO_EFFECTS.register("limiter", level=FULL, note="5 ms look-ahead brickwall at threshold dB (sample peak), release")
def fx_limiter(fx: FxContext, x):
    y = x * np.float32(10 ** (fx.num("gain", 0.0) / 20))
    return dsp.limit(y, fx.sr, fx.num("threshold", -1.0) if fx.el.get("threshold") else -1.0,
                     release=max(0.005, fx.num("release", 0.1)), true_peak=False)


@AUDIO_EFFECTS.register("gate", level=FULL, note="noise gate: below threshold attenuates by `amount`*80 dB (default -40 dB); attack/release")
def fx_gate(fx: FxContext, x):
    key = fx.key(fx.get("sidechain", None), x.shape[0]) if fx.get("sidechain", None) else None
    det = _detector_db(key if key is not None else x, fx.sr)
    rng = -80.0 * fx.num("amount", 0.5)
    target = np.where(det >= fx.num("threshold", -18), 0.0, rng)
    g = dsp.smooth_gain_db(target, fx.num("release", 0.1), fx.num("attack", 0.01))   # closing = release
    return _apply_block_gain(x, g, fx.sr)


@AUDIO_EFFECTS.register("de-esser", level=FULL, note="split-band compressor above `frequency` (default 6 kHz)")
def fx_deesser(fx: FxContext, x):
    f = fx.num("frequency", 6000.0)
    high = dsp.filter_sections(x, dsp.butter_sections("highpass", f, fx.sr, 4))
    low = x - high                      # complementary (approximately, phase shifted) split
    det = _detector_db(high, fx.sr)
    gr = dsp.compressor_gain_db(det, fx.num("threshold", -30), max(fx.num("ratio", 4), 1), fx.num("knee", 3))
    gr = dsp.smooth_gain_db(gr, fx.num("attack", 0.002), fx.num("release", 0.05))
    return (low + _apply_block_gain(high, gr, fx.sr)).astype(np.float32)


# ---------------------------------------------------------------- time based
def _comb(x: np.ndarray, D: int, g: float) -> np.ndarray:
    """y[n] = x[n] + g*y[n-D], vectorised in blocks of D."""
    y = x.astype(np.float64).copy()
    for s in range(D, len(y), D):
        e = min(s + D, len(y))
        y[s:e] += g * y[s - D:e - D]
    return y


def _allpass(x: np.ndarray, D: int, g: float) -> np.ndarray:
    """Schroeder allpass y[n] = -g x[n] + x[n-D] + g y[n-D]."""
    xd = np.zeros_like(x)
    xd[D:] = x[:-D] if D < len(x) else 0
    y = -g * x + xd
    for s in range(D, len(y), D):
        e = min(s + D, len(y))
        y[s:e] += g * y[s - D:e - D]
    return y


@AUDIO_EFFECTS.register("delay", level=FULL, note="feedback echo: time (default 0.25 s), feedback; output dry + wet*amount*2")
def fx_delay(fx: FxContext, x):
    D = max(1, int(round(fx.num("time", 0.25) * fx.sr)))
    fb = min(0.98, fx.num("feedback", 0.3))
    wet = np.zeros_like(x, dtype=np.float64)
    if D < len(x):
        wet[D:] = x[:-D]
    wet = _comb(wet, D, fb)
    return (x + wet * (2 * fx.num("amount", 0.5))).astype(np.float32)


_COMBS = (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)
_ALLPASSES = (556, 441, 341, 225)


@AUDIO_EFFECTS.register("reverb", level=PARTIAL,
                        note="Freeverb topology (8 combs + 4 allpasses); damping is a one-pole low-pass on the wet signal, not inside the comb loops")
def fx_reverb(fx: FxContext, x):
    room = fx.num("roomSize", 0.5)
    fb = 0.7 + 0.28 * room
    k = fx.sr / 44100.0
    damp = fx.num("damping", 0.5)
    width = fx.num("width", 1.0)
    level = fx.num("amount", 0.5)
    mono = x.mean(axis=1).astype(np.float64) * 0.015 * 2
    outs = []
    for spread in (0, 23):
        acc = np.zeros_like(mono)
        for d in _COMBS:
            acc += _comb_delayed(mono, max(1, int((d + spread) * k)), fb)
        for d in _ALLPASSES:
            acc = _allpass(acc, max(1, int((d + spread) * k)), 0.5)
        outs.append(acc)
    wl, wr = outs
    if damp > 0:
        a = math.exp(-2 * math.pi * (20000 * (1 - damp) + 1500) / fx.sr)
        h = dsp.impulse_response([(np.array([1 - a, 0, 0]), np.array([1, -a, 0]))])
        w = dsp.fft_convolve(np.stack([wl, wr], 1).astype(np.float32), h)
        wl, wr = w[:, 0], w[:, 1]
    m, s = (wl + wr) / 2, (wl - wr) / 2 * width
    wet = np.stack([m + s, m - s], 1)
    if x.shape[1] == 1:
        wet = wet.mean(axis=1, keepdims=True)
    elif x.shape[1] > 2:
        wet = np.concatenate([wet, np.zeros((len(wet), x.shape[1] - 2))], 1)
    return (x + level * wet).astype(np.float32)


def _comb_delayed(x: np.ndarray, D: int, g: float) -> np.ndarray:
    """Feedback comb with the delay in the loop: y[n] = x[n-D] + g*y[n-D]."""
    xd = np.zeros_like(x)
    if D < len(x):
        xd[D:] = x[:-D]
    return _comb(xd, D, g)


@AUDIO_EFFECTS.register("chorus", level=FULL, note="3 voices, 7-25 ms LFO-modulated delays (frequency = LFO Hz, amount = depth)")
def fx_chorus(fx: FxContext, x):
    n = x.shape[0]
    rate = fx.num("frequency", 0.8) if fx.el.get("frequency") else 0.8
    depth = 0.008 * fx.num("amount", 0.5) * 2
    t = np.arange(n) / fx.sr
    wet = np.zeros_like(x, dtype=np.float32)
    for v in range(3):
        ph = 2 * math.pi * v / 3
        delay = (0.015 + depth * np.sin(2 * math.pi * rate * t + ph)) * fx.sr
        pos = np.arange(n) - delay
        i0 = np.floor(pos).astype(np.int64)
        f = (pos - i0)[:, None]
        a = x[np.clip(i0, 0, n - 1)] * (i0 >= 0)[:, None]
        b = x[np.clip(i0 + 1, 0, n - 1)] * (i0 + 1 >= 0)[:, None]
        wet += (a * (1 - f) + b * f).astype(np.float32)
    return ((x + wet / 3) * 0.7071).astype(np.float32)


@AUDIO_EFFECTS.register("pitch-shift", level=PARTIAL, note="two-tap delay-line shifter (40 ms grains, crossfaded); audible flanging on complex material")
def fx_pitch(fx: FxContext, x):
    semis = fx.num("semitones", 0.0)
    if abs(semis) < 1e-6:
        return x
    r = 2 ** (semis / 12)
    n = x.shape[0]
    W = int(0.04 * fx.sr)
    idx = np.arange(n, dtype=np.float64)
    out = np.zeros_like(x, dtype=np.float64)
    for off in (0.0, 0.5):
        phase = ((idx * (1 - r) / W) + off) % 1.0            # sawtooth delay 0..W
        delay = phase * W
        pos = idx - delay
        i0 = np.floor(pos).astype(np.int64)
        f = (pos - i0)[:, None]
        a = x[np.clip(i0, 0, n - 1)] * (i0 >= 0)[:, None]
        b = x[np.clip(i0 + 1, 0, n - 1)] * (i0 + 1 >= 0)[:, None]
        win = np.sin(math.pi * phase) ** 2                    # the two windows sum to 1
        out += (a * (1 - f) + b * f) * win[:, None]
    return out.astype(np.float32)


# ---------------------------------------------------------------- colour / image
@AUDIO_EFFECTS.register("distortion", level=FULL, note="tanh waveshaper, drive = 1 + 20*amount, gain = output dB")
def fx_distortion(fx: FxContext, x):
    drive = 1 + 20 * fx.num("amount", 0.5)
    y = np.tanh(drive * x) / math.tanh(drive)
    return (y * 10 ** (fx.num("gain", 0.0) / 20)).astype(np.float32)


@AUDIO_EFFECTS.register("stereo-width", level=FULL, note="mid/side: side *= width (0 = mono, 1 = unchanged)")
def fx_width(fx: FxContext, x):
    if x.shape[1] < 2:
        return x
    w = fx.num("width", 1.0)
    m = (x[:, 0] + x[:, 1]) / 2
    s = (x[:, 0] - x[:, 1]) / 2 * w
    y = x.copy()
    y[:, 0], y[:, 1] = m + s, m - s
    return y


AUDIO_EFFECTS.declare("noise-reduction", NONE, "no spectral denoiser; passed through unchanged")


def tail_seconds(fx_el) -> float:
    """How far past the end of its input an effect keeps producing sound."""
    t = fx_el.get("type")
    if t == "delay":
        fb = min(0.98, float(fx_el.get("feedback", 0.3)))
        d = float(fx_el.get("time", 0.25))
        return min(12.0, d * (1 + math.log(1e-3) / math.log(max(fb, 1e-3))))
    if t == "reverb":
        return 1.5 + 4.0 * float(fx_el.get("roomSize", 0.5))
    if t == "chorus":
        return 0.05
    return 0.0


def process_chain(effects: list, x: np.ndarray, make_ctx) -> np.ndarray:
    """Run audioEffect elements in document order."""
    for el in effects:
        if el.get("enabled", "true") == "false":
            continue
        typ = el.get("type")
        fn = AUDIO_EFFECTS.get(typ)
        if fn is None:
            warn_once("audioEffect", typ, "not supported by the Python renderer; passed through")
            continue
        fx = make_ctx(el)
        y = fn(fx, x)
        mix = fx.num("mix", 1.0)
        x = y if mix >= 1.0 else ((1 - mix) * x + mix * y).astype(np.float32)
    return x
