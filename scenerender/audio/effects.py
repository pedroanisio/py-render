"""audioEffect handlers:  fn(fx: FxContext, x: (n, ch) float32) -> (n, ch) float32.

The caller handles `enabled`, `mix` (out = (1 - mix) x dry + mix x processed, automated per sample)
and, for every type that does not give `gain` its own meaning (gain, eq, compressor, limiter,
distortion), `gain` as an output gain in dB (automated per sample).  Time-based effects (delay,
reverb, chorus) return dry + wet, so mix="1" keeps the dry signal.  Parameters are the audioEffect attributes or
<param name value> children (params win), evaluated at the start of the processed segment.

Per type (attributes; extra params in brackets):
  gain          gain dB (automated per sample)
  eq            band children: kind, frequency, gain (peak/shelf dB), q (RBJ cookbook: bandwidth Q for
                peak/notch/pass, shelf Q for shelves); without bands a single peak at @frequency/@gain
                [q]; with bands @gain is an output gain
  highpass/lowpass  frequency; Butterworth [order=2 (12 dB/oct), even orders]; [q] = one RBJ biquad
  compressor    threshold, ratio, knee (soft, dB), attack, release (one-pole on the gain in dB),
                gain = makeup, sidechain = key track/bus, frequency = key high-pass (sidechain filter);
                RMS detector over 5 ms [detector=rms|peak]
  limiter       threshold = ceiling dBFS (schema default -18), attack = look-ahead (min 0.5 ms),
                release, gain = input drive; sample-peak (param truePeak=true for 4x oversampled)
  gate          threshold opens, [hysteresis dB, default 0] closes below threshold - hysteresis,
                [hold s, default 0]; range = amount x 80 dB; attack = opening, release = closing time;
                an explicit @ratio turns it into a downward expander (gain below threshold falls
                (ratio - 1) dB per dB, soft knee @knee, floored at the range); sidechain, frequency
                = key high-pass
  de-esser      frequency (split, default 6 kHz), threshold, ratio, knee, attack, release, sidechain
                (key high band); perfect-reconstruction split (low = x - high)
  reverb        Freeverb (Jezar 2000): 8 damped feedback combs + 4 allpasses per side, stereo spread 23
                samples, delays scaled by sr/44100; roomSize -> feedback 0.7 + 0.28 x roomSize,
                [damping 0..1, default 0.5] -> one-pole low-pass inside every comb loop (damp1 =
                0.4 x damping), width (stereo width of the wet), amount = wet level (x 3, Freeverb's
                scalewet), time = pre-delay s [preDelay]
  delay         time s (default 0.25) [sync = note value "1/4", "1/8", "1/8d", "1/4t", ... at the
                track asset's bpm or the first beatGrid's], feedback, amount (wet = 2 x amount),
                frequency = one-pole low-pass inside the feedback loop (analogue/tape darkening)
  chorus        frequency = LFO rate (default 0.8 Hz), amount = depth (+-16 ms x amount), time =
                centre delay (default 15 ms), width = LFO phase offset between channels (x 90 deg)
  pitch-shift   semitones; phase-locked phase vocoder + resampling [formants=true: cepstral formant
                preservation; lifter ms, default 1.0]
  noise-reduction  spectral gating (spectral.spectral_gate): amount -> reduction = 40 x amount dB,
                threshold (only when given) = opening level in dB above the noise profile (else
                1.5 std, at least 6 dB), attack/release = mask opening/closing times,
                [profileStart, profileEnd] = noise-only region in composition seconds (else the
                quietest 10 %), [smoothing Hz, default 150], [sensitivity, default 1.5]
  stereo-width  width (mid/side; ambisonics: m < 0 components; surround: the FL/FR pair)
  distortion    amount -> drive 1 + 20 x amount (tanh), gain = output dB
  telephone     frequency = low cut (default 300 Hz) to 3.4 kHz (24 dB/oct), amount = saturation;
                mono (placed in FC / FL+FR / W for multichannel signals)
Multichannel (bus/master in surround or ambisonic layouts): filters and dynamics run per channel
(detectors linked); reverb takes the mono sum of the non-LFE channels (W for ambisonics) and returns
its left wet to the left-side speakers and its right wet to the right-side ones (ambisonics:
encoded at +-90 deg).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from ..registry import AUDIO_EFFECTS, FULL, warn_once
from . import dsp, spectral

GAIN_CONSUMERS = {"gain", "eq", "compressor", "limiter", "distortion"}


@dataclass
class FxContext:
    el: Any
    sr: float
    t0: float                       # composition time of the first sample
    get: Callable                   # get(name, default) -> float (attribute/param, evaluated at t0)
    curve: Callable                 # curve(name, default, n) -> per-sample values (automation)
    key: Callable                   # key(id, n) -> sidechain signal aligned to x, or None
    seed: int = 0
    bpm: float | None = None        # tempo for delay sync (track asset @bpm, else the first beatGrid)
    layout: Any = None              # spatial.Layout of a bus/master signal (None: source channels)

    def num(self, name, default):
        v = self.get(name, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def flag(self, name, default=False) -> bool:
        v = self.get(name, None)
        if v is None:
            return default
        return str(v).strip().lower() in ("1", "1.0", "true", "yes", "on")

    def given(self, name) -> bool:
        return self.el.get(name) is not None or any(
            isinstance(p.tag, str) and p.tag == "param" and p.get("name") == name for p in self.el)


def _gain_db_curve(fx: FxContext, n: int):
    return fx.curve("gain", 0.0, n)


# ---------------------------------------------------------------- level
@AUDIO_EFFECTS.register("gain", level=FULL, note="static or automated gain in dB")
def fx_gain(fx: FxContext, x):
    g = dsp.db_to_lin(_gain_db_curve(fx, x.shape[0]))
    return (x * np.asarray(g, np.float32).reshape(-1, 1)).astype(np.float32)


# ---------------------------------------------------------------- filters
@AUDIO_EFFECTS.register("eq", level=FULL, note="RBJ biquads per band (kind, frequency, gain, q); @gain output gain")
def fx_eq(fx: FxContext, x):
    bands = [b for b in fx.el if isinstance(b.tag, str) and b.tag == "band"]
    if not bands:
        if fx.get("frequency", None) is None:
            return x
        return dsp.filter_sections(x, [dsp.biquad("peak", fx.num("frequency", 1000), fx.sr, fx.num("q", 0.707),
                                                  fx.num("gain", 0))])
    sections = [dsp.biquad(b.get("kind", "peak"), float(b.get("frequency")), fx.sr, float(b.get("q", 0.707)),
                           float(b.get("gain", 0))) for b in bands]
    y = dsp.filter_sections(x, sections)
    g = _gain_db_curve(fx, x.shape[0])
    return (y * np.asarray(dsp.db_to_lin(g), np.float32).reshape(-1, 1)).astype(np.float32)


def _pass(kind, default_f):
    def fn(fx: FxContext, x):
        f = fx.num("frequency", default_f)
        if fx.get("q", None) is not None:
            return dsp.filter_sections(x, [dsp.biquad(kind, f, fx.sr, fx.num("q", 0.7071))])
        return dsp.filter_sections(x, dsp.butter_sections(kind, f, fx.sr, int(fx.num("order", 2))))
    return fn


AUDIO_EFFECTS.register("highpass", level=FULL, note="Butterworth 12 dB/oct (param order/q)")(_pass("highpass", 80.0))
AUDIO_EFFECTS.register("lowpass", level=FULL, note="Butterworth 12 dB/oct (param order/q)")(_pass("lowpass", 12000.0))


def _mono_out(fx: FxContext, m: np.ndarray, nch: int) -> np.ndarray:
    """A mono signal placed on a signal of nch channels (FC, else FL+FR at -3 dB; W for ambisonics)."""
    lay = fx.layout
    out = np.zeros((m.shape[0], nch), np.float32)
    if lay is None or nch <= 2:
        out[:] = m[:, None]
    elif lay.ambisonic:
        out[:, 0] = m
    elif "FC" in lay.channels:
        out[:, lay.channels.index("FC")] = m
    else:
        out[:, 0] = out[:, 1] = m * 0.7071
    return out


def _mono_in(fx: FxContext, x: np.ndarray) -> np.ndarray:
    lay = fx.layout
    if lay is None or x.shape[1] <= 2:
        return x.mean(axis=1)
    if lay.ambisonic:
        return x[:, 0].copy()
    use = [i for i, c in enumerate(lay.channels) if c != "LFE"]
    return x[:, use].sum(axis=1) / math.sqrt(len(use))


@AUDIO_EFFECTS.register("telephone", level=FULL, note="300-3400 Hz band-pass (24 dB/oct), saturation = amount, mono")
def fx_telephone(fx: FxContext, x):
    lo, hi = fx.num("frequency", 300.0), 3400.0
    m = _mono_in(fx, x)[:, None].astype(np.float32)
    y = dsp.filter_sections(m, dsp.butter_sections("highpass", lo, fx.sr, 4) + dsp.butter_sections("lowpass", hi, fx.sr, 4))
    drive = 1 + 4 * fx.num("amount", 0.5)
    y = np.tanh(drive * y) / math.tanh(drive)
    return _mono_out(fx, y[:, 0], x.shape[1])


# ---------------------------------------------------------------- dynamics
def _detector_db(sig, sr, peak: bool = False):
    if peak:
        return dsp.lin_to_db(dsp.block_peak(sig, sr))
    return dsp.lin_to_db(np.sqrt(dsp.moving_average(dsp.block_ms(sig, sr), 5)))


def _key_signal(fx: FxContext, x):
    """Detector input: the sidechain (if any), high-passed at @frequency when given."""
    sc = fx.get("sidechain", None)
    key = fx.key(sc, x.shape[0]) if sc else None
    sig = key if key is not None else x
    if fx.el.get("frequency") is not None or fx.given("frequency"):
        sig = dsp.filter_sections(sig, dsp.butter_sections("highpass", fx.num("frequency", 100.0), fx.sr, 2))
    return sig


def _apply_block_gain(x, g_db, sr):
    g = dsp.gain_to_samples(dsp.db_to_lin(g_db), x.shape[0], sr)
    return (x * g[:, None]).astype(np.float32)


@AUDIO_EFFECTS.register("compressor", level=FULL,
                        note="RMS 5 ms (or peak) detector, threshold/ratio/soft knee, attack/release, makeup gain, sidechain + key high-pass")
def fx_compressor(fx: FxContext, x):
    det = _detector_db(_key_signal(fx, x), fx.sr, str(fx.get("detector", "rms")) == "peak")
    gr = dsp.compressor_gain_db(det, fx.num("threshold", -18), fx.num("ratio", 4), fx.num("knee", 3))
    gr = dsp.smooth_gain_db(gr, fx.num("attack", 0.01), fx.num("release", 0.1))
    return _apply_block_gain(x, gr + fx.num("gain", 0.0), fx.sr)


@AUDIO_EFFECTS.register("limiter", level=FULL, note="look-ahead brickwall: ceiling = threshold dBFS, attack = look-ahead, release, gain = drive")
def fx_limiter(fx: FxContext, x):
    y = x * np.asarray(dsp.db_to_lin(_gain_db_curve(fx, x.shape[0])), np.float32).reshape(-1, 1)
    return dsp.limit(y.astype(np.float32), fx.sr, fx.num("threshold", -18.0), lookahead=max(0.0005, fx.num("attack", 0.01)),
                     release=max(0.005, fx.num("release", 0.1)), true_peak=fx.flag("truePeak"))


@AUDIO_EFFECTS.register("gate", level=FULL,
                        note="noise gate/expander: threshold + hysteresis + hold, range amount x 80 dB, attack opens / release closes, ratio (expander), knee, sidechain + key high-pass")
def fx_gate(fx: FxContext, x):
    det = _detector_db(_key_signal(fx, x), fx.sr)
    rng = -80.0 * fx.num("amount", 0.5)
    thr = fx.num("threshold", -18.0)
    if fx.given("ratio"):
        ratio = max(1.0, fx.num("ratio", 4.0))
        knee = fx.num("knee", 3.0)
        # downward expansion mirrors compression below the threshold
        under = thr - det
        if knee > 0:
            g = np.where(under >= knee / 2, -(ratio - 1) * under,
                         np.where(under > -knee / 2, -(ratio - 1) * (under + knee / 2) ** 2 / (2 * knee), 0.0))
        else:
            g = np.where(under > 0, -(ratio - 1) * under, 0.0)
        target = np.maximum(g, rng)
    else:
        hyst = max(0.0, fx.num("hysteresis", 0.0))
        hold = int(round(max(0.0, fx.num("hold", 0.0)) * dsp.CONTROL_HZ))
        target = np.empty(len(det))
        is_open, since = False, 0
        for i, v in enumerate(det.tolist()):
            if v >= thr:
                is_open, since = True, 0
            elif is_open:
                if v < thr - hyst:
                    since += 1
                    if since > hold:
                        is_open = False
                else:
                    since = 0
            target[i] = 0.0 if is_open else rng
    g = dsp.smooth_gain_db(target, fx.num("release", 0.1), fx.num("attack", 0.01))   # falling = closing
    return _apply_block_gain(x, g, fx.sr)


@AUDIO_EFFECTS.register("de-esser", level=FULL, note="split-band compressor above frequency (default 6 kHz), sidechain")
def fx_deesser(fx: FxContext, x):
    f = fx.num("frequency", 6000.0)
    split = dsp.butter_sections("highpass", f, fx.sr, 4)
    high = dsp.filter_sections(x, split)
    low = x - high                      # exact reconstruction when no reduction applies
    sc = fx.get("sidechain", None)
    key = fx.key(sc, x.shape[0]) if sc else None
    det = _detector_db(high if key is None else dsp.filter_sections(key, split), fx.sr)
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


def _delayed(x: np.ndarray, D: int) -> np.ndarray:
    y = np.zeros_like(x)
    if D < len(x):
        y[D:] = x[:len(x) - D]
    return y


def _damped_comb(x: np.ndarray, D: int, fb: float, d: float) -> np.ndarray:
    """Feedback comb with a one-pole low-pass in the loop (Freeverb comb / damped delay):
    out[n] = x[n-D] + fb * lp[n-D],  lp[n] = (1 - d) out[n] + d lp[n-1].  Exact, blocks of D."""
    n = len(x)
    out = np.zeros_like(x, dtype=np.float64)
    lp = np.zeros_like(out)
    state = None
    for s in range(0, n, D):
        e = min(s + D, n)
        if s >= D:
            out[s:e] = x[s - D:e - D] + fb * lp[s - D:e - D]
        lp[s:e], state = dsp.one_pole(out[s:e], d, state)
    return out


def _fv_allpass(x: np.ndarray, D: int, g: float = 0.5) -> np.ndarray:
    """Freeverb allpass: b[n] = x[n] + g b[n-D];  y[n] = b[n-D] - x[n]."""
    return _delayed(_comb(x, D, g), D) - x


_NOTE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*([dt]?)\s*$")


def note_seconds(sync: str, bpm: float) -> float | None:
    """Duration of a note value ('1/4' = one beat, '1/8d' dotted, '1/4t' triplet) at bpm."""
    m = _NOTE.match(str(sync))
    if not m or bpm <= 0:
        return None
    beats = 4.0 * int(m.group(1)) / max(1, int(m.group(2)))
    beats *= 1.5 if m.group(3) == "d" else (2.0 / 3.0 if m.group(3) == "t" else 1.0)
    return beats * 60.0 / bpm


def delay_time(get, bpm: float | None) -> float:
    sync = get("sync", None)
    if sync is not None and not isinstance(sync, float):
        if bpm:
            t = note_seconds(sync, bpm)
            if t is not None:
                return t
        warn_once("audioEffect", "delay-sync", f"sync={sync!r} needs a note value and a bpm (asset @bpm or beatGrid); using @time")
    v = get("time", None)
    try:
        return float(v) if v is not None else 0.25
    except (TypeError, ValueError):
        return 0.25


@AUDIO_EFFECTS.register("delay", level=FULL,
                        note="feedback echo: time (or param sync note value at bpm), feedback, amount (wet = 2 x amount), frequency = low-pass in the loop")
def fx_delay(fx: FxContext, x):
    D = max(1, int(round(delay_time(fx.get, fx.bpm) * fx.sr)))
    fb = min(0.98, fx.num("feedback", 0.3))
    d = math.exp(-2 * math.pi * fx.num("frequency", 1000.0) / fx.sr) if fx.given("frequency") else 0.0
    wet = np.stack([_damped_comb(x[:, c].astype(np.float64), D, fb, d) for c in range(x.shape[1])], 1) \
        if d > 0 else _comb(_delayed(x.astype(np.float64), D), D, fb)
    return (x + wet * (2 * fx.num("amount", 0.5))).astype(np.float32)


_COMBS = (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)
_ALLPASSES = (556, 441, 341, 225)
_SPREAD = 23


def _freeverb(mono: np.ndarray, sr: float, room: float, damp: float) -> tuple[np.ndarray, np.ndarray]:
    """Freeverb's two wet outputs for a mono input.  The network is linear and time-invariant, so
    long inputs are convolved (FFT) with its impulse response, computed by the same exact recursion
    until it has decayed by 130 dB; short inputs run through the recursion directly."""
    fb = 0.7 + 0.28 * room
    n = len(mono)
    # the loudest comb loop gain per pass is fb; -130 dB after log(10^-6.5)/log(fb) passes of <= 1617 samples
    L_ir = int(math.ceil(math.log(10 ** -6.5) / math.log(fb) * (1617 + _SPREAD) * sr / 44100.0)) + 1
    if n > 2 * L_ir:
        imp = np.zeros(L_ir)
        imp[0] = 1.0
        hl, hr = _freeverb_direct(imp, sr, fb, damp)
        x = mono.astype(np.float32)[:, None]
        return dsp.fft_convolve(x, hl)[:, 0].astype(np.float64), dsp.fft_convolve(x, hr)[:, 0].astype(np.float64)
    return _freeverb_direct(mono, sr, fb, damp)


def _freeverb_direct(mono: np.ndarray, sr: float, fb: float, damp: float) -> tuple[np.ndarray, np.ndarray]:
    d = 0.4 * damp
    k = sr / 44100.0
    inp = mono.astype(np.float64) * 0.015
    outs = []
    for spread in (0, _SPREAD):
        acc = np.zeros_like(inp)
        for dl in _COMBS:
            acc += _damped_comb(inp, max(1, int((dl + spread) * k)), fb, d)
        for dl in _ALLPASSES:
            acc = _fv_allpass(acc, max(1, int((dl + spread) * k)), 0.5)
        outs.append(acc)
    return outs[0], outs[1]


@AUDIO_EFFECTS.register("reverb", level=FULL,
                        note="Freeverb: damped combs (damping in the loops) + allpasses; roomSize, width, amount = wet, time = pre-delay, param damping")
def fx_reverb(fx: FxContext, x):
    room = min(1.0, max(0.0, fx.num("roomSize", 0.5)))
    damp = min(1.0, max(0.0, fx.num("damping", 0.5)))
    width = fx.num("width", 1.0)
    wet = 3.0 * fx.num("amount", 0.5)
    pre = fx.num("preDelay", None) if fx.given("preDelay") else (fx.num("time", 0.0) if fx.given("time") else 0.0)
    m = _mono_in(fx, x) if (fx.layout is not None and x.shape[1] > 2) else x.sum(axis=1)
    if pre and pre > 0:
        m = _delayed(m, int(round(pre * fx.sr)))
    wl, wr = _freeverb(m, fx.sr, room, damp)
    w1, w2 = wet * (width / 2 + 0.5), wet * ((1 - width) / 2)
    L, R = wl * w1 + wr * w2, wr * w1 + wl * w2
    ch = x.shape[1]
    lay = fx.layout
    if ch == 1:
        out = ((L + R) / 2)[:, None]
    elif ch == 2 or lay is None:
        out = np.zeros((len(L), ch))
        out[:, 0], out[:, 1] = L, R
    elif lay.ambisonic:
        from .spatial import sh_gains
        g = sh_gains(lay.order, np.radians([90.0, -90.0]), np.zeros(2))
        out = L[:, None] * g[0][None, :] + R[:, None] * g[1][None, :]
    else:
        left = [i for i, c in enumerate(lay.channels) if (lay.azimuth(c) or 0) > 1e-9 and lay.elevation(c) == 0 and abs(lay.azimuth(c)) < 179]
        right = [i for i, c in enumerate(lay.channels) if (lay.azimuth(c) or 0) < -1e-9 and lay.elevation(c) == 0 and abs(lay.azimuth(c)) < 179]
        out = np.zeros((len(L), ch))
        for i in left:
            out[:, i] = L / math.sqrt(len(left))
        for i in right:
            out[:, i] = R / math.sqrt(len(right))
    return (x + out).astype(np.float32)


@AUDIO_EFFECTS.register("chorus", level=FULL,
                        note="3 voices, LFO-modulated delays: frequency = rate, amount = depth, time = centre delay, width = stereo LFO offset")
def fx_chorus(fx: FxContext, x):
    n, ch = x.shape
    rate = fx.num("frequency", 0.8) if fx.given("frequency") else 0.8
    depth = 0.016 * fx.num("amount", 0.5)
    centre = max(depth + 0.001, fx.num("time", 0.015) if fx.given("time") else 0.015)
    width = fx.num("width", 1.0)
    t = np.arange(n) / fx.sr
    wet = np.zeros_like(x, dtype=np.float32)
    for c in range(ch):
        off = (math.pi / 2) * width * (c % 2)
        for v in range(3):
            ph = 2 * math.pi * v / 3 + off
            pos = np.arange(n) - (centre + depth * np.sin(2 * math.pi * rate * t + ph)) * fx.sr
            i0 = np.floor(pos).astype(np.int64)
            f = pos - i0
            a = x[np.clip(i0, 0, n - 1), c] * (i0 >= 0)
            b = x[np.clip(i0 + 1, 0, n - 1), c] * (i0 + 1 >= 0)
            wet[:, c] += (a * (1 - f) + b * f).astype(np.float32)
    return ((x + wet / 3) * 0.7071).astype(np.float32)


@AUDIO_EFFECTS.register("pitch-shift", level=FULL,
                        note="phase-locked phase vocoder + band-limited resampling; param formants=true keeps the spectral envelope")
def fx_pitch(fx: FxContext, x):
    semis = fx.num("semitones", 0.0)
    if abs(semis) < 1e-6:
        return x
    return spectral.pitch_shift(x, semis, fx.sr, formants=fx.flag("formants") or fx.flag("preserveFormants"),
                                lifter_ms=fx.num("lifter", 1.0))


@AUDIO_EFFECTS.register("noise-reduction", level=FULL,
                        note="spectral gating: noise profile from the quietest 10 % (or params profileStart/profileEnd), soft mask smoothed in frequency and time")
def fx_denoise(fx: FxContext, x):
    prof = None
    if fx.given("profileStart") and fx.given("profileEnd"):
        a = (fx.num("profileStart", 0.0) - fx.t0) * fx.sr
        b = (fx.num("profileEnd", 0.0) - fx.t0) * fx.sr
        if b > a and b > 0:
            prof = (max(0, int(a)), int(b))
    thr = fx.num("threshold", -18.0) if fx.given("threshold") else None
    return spectral.spectral_gate(x, fx.sr, reduction_db=40.0 * fx.num("amount", 0.5), threshold_db=thr,
                                  attack=fx.num("attack", 0.01), release=fx.num("release", 0.1), profile=prof,
                                  freq_smooth_hz=fx.num("smoothing", 150.0), sensitivity=fx.num("sensitivity", 1.5))


# ---------------------------------------------------------------- colour / image
@AUDIO_EFFECTS.register("distortion", level=FULL, note="tanh waveshaper, drive = 1 + 20*amount, gain = output dB")
def fx_distortion(fx: FxContext, x):
    drive = 1 + 20 * fx.num("amount", 0.5)
    y = np.tanh(drive * x) / math.tanh(drive)
    return (y * np.asarray(dsp.db_to_lin(_gain_db_curve(fx, x.shape[0])), np.float32).reshape(-1, 1)).astype(np.float32)


@AUDIO_EFFECTS.register("stereo-width", level=FULL, note="mid/side: side *= width (0 = mono, 1 = unchanged); surround FL/FR, ambisonic m<0 terms")
def fx_width(fx: FxContext, x):
    if x.shape[1] < 2:
        return x
    w = fx.num("width", 1.0)
    y = x.copy()
    lay = fx.layout
    if lay is not None and lay.ambisonic:
        for l in range(1, lay.order + 1):
            for m in range(-l, 0):
                y[:, l * l + l + m] *= w
        return y
    m = (x[:, 0] + x[:, 1]) / 2
    s = (x[:, 0] - x[:, 1]) / 2 * w
    y[:, 0], y[:, 1] = m + s, m - s
    return y


def _param(fx_el, name):
    for p in fx_el:
        if isinstance(p.tag, str) and p.tag == "param" and p.get("name") == name:
            return p.get("value")
    return fx_el.get(name)


def tail_seconds(fx_el, bpm: float | None = None) -> float:
    """How far past the end of its input an effect keeps producing sound (to -60 dB)."""
    t = fx_el.get("type")

    def num(name, default):
        try:
            v = _param(fx_el, name)
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default
    if t == "delay":
        fb = min(0.98, num("feedback", 0.3))
        d = delay_time(lambda k, dflt: (_param(fx_el, k) if _param(fx_el, k) is not None else dflt), bpm)
        return min(30.0, d * (1 + math.log(1e-3) / math.log(max(fb, 1e-3))))
    if t == "reverb":
        fb = 0.7 + 0.28 * min(1.0, max(0.0, num("roomSize", 0.5)))
        pre = num("preDelay", num("time", 0.0))
        return min(30.0, pre + 0.04 * math.log(1e-3) / math.log(fb) + 0.05)
    if t == "chorus":
        return 0.05
    if t == "pitch-shift":
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
        if typ not in GAIN_CONSUMERS:
            g = np.asarray(fx.curve("gain", 0.0, x.shape[0]), np.float64)
            if np.any(np.abs(g) > 1e-12):
                y = (y * np.asarray(dsp.db_to_lin(g), np.float32).reshape(-1, 1)).astype(np.float32)
        mix = np.clip(np.asarray(fx.curve("mix", 1.0, x.shape[0]), np.float64), 0.0, 1.0)
        if np.all(mix >= 1.0):
            x = y
        else:
            m = mix.reshape(-1, 1).astype(np.float32)
            x = ((1 - m) * x + m * y).astype(np.float32)
    return x
