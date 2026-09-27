"""Spectral processing: phase-locked phase vocoder (time-stretch, pitch-shift), cepstral formant
correction and a spectral-gating noise reducer.  Signals are float (n, channels).

Time-stretch (`time_stretch`): phase vocoder with identity phase locking (Laroche & Dolson 1999).
Hann windows of ~46 ms (2048 samples at 44.1/48 kHz), synthesis hop N/4 (N/8 or less when the
analysis hop would exceed N/2).  Per frame, magnitude peaks of the channel sum are found; each
peak's synthesis phase advances by its instantaneous frequency x the synthesis hop, and every
bin in the peak's region of influence (bounded by the midpoints between peaks) receives the same
phase rotation as its peak, which keeps partials coherent (little "phasiness").  The rotation is
computed once from the channel sum and applied to every channel, so inter-channel phase (the
stereo image) is preserved.  Frames whose energy above N/16 bins jumps by more than +6 dB while
holding at least 1 % of the frame energy (onsets)
reset the rotation to zero (phase reset), keeping attacks sharp.  The output length is exactly
round(n / rate).

Pitch-shift (`pitch_shift`): stretch by r = 2^(semitones/12) and resample by 1/r (band-limited
sinc), so the duration is unchanged.  With `formants=True` the spectral envelope of the input is
restored: per STFT frame the cepstrally smoothed log-spectra (lifter at `lifter_ms`, default
1.0 ms) of input and shifted output give a correction filter (clamped to +-24 dB).

Noise reduction (`spectral_gate`): STFT (N ~ 2048, hop N/4).  The noise profile is the per-bin
mean and standard deviation (in dB) of either a given profile region or the 10 % quietest frames
(at least 0.25 s).  Levels are measured on the channel-linked power smoothed over frequency
(`freq_smooth_hz`, default 150 Hz) and 3 frames, so isolated noise spikes cannot open a bin.
A bin is "signal" when it exceeds the profile mean by `threshold_db` (default: 1.5 standard
deviations, at least 6 dB); the mask is a soft (logistic, 1 dB scale) decision, smoothed in time
with separate attack (opening) and release (closing) time constants.  Gated bins are attenuated by
`reduction_db`.  Smoothing in both directions is what avoids musical-noise artefacts.
"""
from __future__ import annotations

import math

import numpy as np

from .dsp import interp_positions


def fft_size(sr: float, seconds: float = 0.0464) -> int:
    return int(2 ** round(math.log2(max(64.0, sr * seconds))))


def _hann(N: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N) / N)).astype(np.float64)       # periodic


def stft(x: np.ndarray, N: int, H: int) -> np.ndarray:
    """Centred STFT of x (n, ch) -> (frames, N//2+1, ch); frame m is centred on sample m*H."""
    n, ch = x.shape
    m = int(math.ceil(n / H)) + 1
    xp = np.zeros((m * H + N, ch), np.float64)
    xp[N // 2:N // 2 + n] = x
    idx = np.arange(m)[:, None] * H + np.arange(N)[None, :]
    return np.fft.rfft(xp[idx] * _hann(N)[None, :, None], axis=1)


def istft(X: np.ndarray, N: int, H: int, n: int) -> np.ndarray:
    """Inverse of stft (weighted overlap-add, normalised by the summed squared window)."""
    m, _, ch = X.shape
    w = _hann(N)
    frames = np.fft.irfft(X, N, axis=1) * w[None, :, None]
    out = np.zeros((m * H + N, ch))
    norm = np.zeros(m * H + N)
    for i in range(m):
        out[i * H:i * H + N] += frames[i]
        norm[i * H:i * H + N] += w * w
    out /= np.maximum(norm, 1e-8)[:, None]
    return out[N // 2:N // 2 + n].astype(np.float32)


def _princarg(p: np.ndarray) -> np.ndarray:
    return p - 2 * np.pi * np.round(p / (2 * np.pi))


def time_stretch(x: np.ndarray, rate: float, sr: float) -> np.ndarray:
    """Play x `rate` times faster without changing its pitch; returns round(n / rate) samples."""
    x = np.asarray(x, np.float32)
    n, ch = x.shape
    n_out = int(round(n / rate))
    if n == 0 or n_out == 0:
        return np.zeros((n_out, ch), np.float32)
    if abs(rate - 1.0) < 1e-9:
        return x.copy()
    N = fft_size(sr)
    Hs = N // 4
    while Hs * rate > N / 2 and Hs > N // 32:
        Hs //= 2
    Ha = Hs * rate
    M = int(math.ceil(n_out / Hs)) + 2
    pa = np.round(np.arange(M) * Ha).astype(np.int64)                 # analysis frame centres
    pad = N // 2
    xp = np.zeros((int(pa[-1]) + N + 1, ch), np.float64)
    xp[pad:pad + n] = x
    w = _hann(N)
    bins = N // 2 + 1
    omega = 2 * np.pi * np.arange(bins) / N
    hf = np.arange(bins) > bins // 8                                   # onset detector band
    out = np.zeros(((M + 1) * Hs + N, ch))
    norm = np.zeros((M + 1) * Hs + N)
    phi_prev = phi_s = None
    e_prev = None
    peak_e = 0.0
    for m in range(M):
        seg = xp[pa[m]:pa[m] + N]
        if seg.shape[0] < N:
            seg = np.concatenate([seg, np.zeros((N - seg.shape[0], ch))])
        X = np.fft.rfft(seg * w[:, None], axis=0)                      # (bins, ch)
        ref = X.sum(axis=1)
        mag = np.abs(ref)
        phi = np.angle(ref)
        e = float(np.sum(mag[hf] ** 2))
        tot = float(np.sum(mag ** 2))
        peak_e = max(peak_e, tot)
        onset = (e_prev is not None and e > 4.0 * e_prev and e > 0.01 * tot and tot > 1e-6 * peak_e)
        e_prev = e
        if phi_prev is None or onset:
            rot = np.zeros(bins)
            phi_s = phi.copy()
        else:
            ha = float(pa[m] - pa[m - 1]) or 1.0
            inst = omega + _princarg(phi - phi_prev - omega * ha) / ha
            inner = (mag[1:-1] > mag[:-2]) & (mag[1:-1] >= mag[2:]) & (mag[1:-1] > mag.max() * 1e-5)
            peaks = np.nonzero(inner)[0] + 1
            if len(peaks) == 0:
                peaks = np.array([int(np.argmax(mag))])
            phi_sp = phi_s[peaks] + Hs * inst[peaks]
            rot_p = phi_sp - phi[peaks]
            bounds = (peaks[:-1] + peaks[1:]) / 2.0
            owner = np.searchsorted(bounds, np.arange(bins), side="right")
            rot = rot_p[owner]
            phi_s = phi + rot
        phi_prev = phi
        Y = X * np.exp(1j * rot)[:, None]
        frame = np.fft.irfft(Y, N, axis=0) * w[:, None]
        s = m * Hs
        out[s:s + N] += frame
        norm[s:s + N] += w * w
    out /= np.maximum(norm, 1e-3 * norm.max())[:, None]
    return out[pad:pad + n_out].astype(np.float32)


def _cepstral_envelope(logmag: np.ndarray, N: int, lifter: int) -> np.ndarray:
    """Cepstrally smoothed log magnitude (frames, bins) -> same shape."""
    c = np.fft.irfft(logmag, N, axis=1)
    c[:, lifter:N - lifter + 1] = 0.0
    return np.fft.rfft(c, N, axis=1).real


def formant_correct(orig: np.ndarray, shifted: np.ndarray, sr: float, lifter_ms: float = 1.0,
                    limit_db: float = 24.0) -> np.ndarray:
    """Impose the spectral envelope of `orig` on `shifted` (same length, time-aligned)."""
    n = min(orig.shape[0], shifted.shape[0])
    N = fft_size(sr)
    H = N // 4
    A = stft(orig[:n], N, H)
    B = stft(shifted[:n], N, H)
    lifter = max(4, int(round(lifter_ms * 1e-3 * sr)))
    la = np.log(np.abs(A.sum(axis=2)) + 1e-9)
    lb = np.log(np.abs(B.sum(axis=2)) + 1e-9)
    corr = _cepstral_envelope(la, N, lifter) - _cepstral_envelope(lb, N, lifter)
    lim = limit_db / 20 * math.log(10)
    g = np.exp(np.clip(corr, -lim, lim))
    return istft(B * g[:, :, None], N, H, n)


def pitch_shift(x: np.ndarray, semitones: float, sr: float, formants: bool = False,
                lifter_ms: float = 1.0) -> np.ndarray:
    """Shift pitch by `semitones` keeping the duration (phase vocoder + band-limited resampling)."""
    x = np.asarray(x, np.float32)
    if abs(semitones) < 1e-9 or x.shape[0] == 0:
        return x.copy()
    r = 2.0 ** (semitones / 12.0)
    y = time_stretch(x, 1.0 / r, sr)                   # r times longer, same pitch
    pos = np.arange(x.shape[0]) * (y.shape[0] / x.shape[0])
    z = interp_positions(y, pos, cutoff=min(1.0, 1.0 / r) * 0.98)
    if formants:
        z = formant_correct(x, z, sr, lifter_ms)
    return z


def _smooth_freq(m: np.ndarray, width: int) -> np.ndarray:
    if width <= 1:
        return m
    k = np.ones(width) / width
    pad = width // 2
    mp = np.pad(m, ((0, 0), (pad, width - 1 - pad)), mode="edge")
    c = np.cumsum(np.pad(mp, ((0, 0), (1, 0))), axis=1)
    return (c[:, width:] - c[:, :-width]) * k[0]


def spectral_gate(x: np.ndarray, sr: float, reduction_db: float = 20.0, threshold_db: float | None = None,
                  attack: float = 0.01, release: float = 0.1, profile: tuple[int, int] | None = None,
                  freq_smooth_hz: float = 150.0, sensitivity: float = 1.5) -> np.ndarray:
    """Spectral-gating noise reduction of x (n, ch); `profile` is a sample range holding only noise."""
    x = np.asarray(x, np.float32)
    n, ch = x.shape
    if n == 0 or reduction_db <= 0:
        return x.copy()
    N = fft_size(sr)
    H = N // 4
    X = stft(x, N, H)
    frames = X.shape[0]
    power = np.mean(np.abs(X) ** 2, axis=2)                            # linked across channels
    # decide on power smoothed over frequency (freq_smooth_hz) and 3 frames: isolated noise
    # spikes cannot open the gate (no musical noise) while partials keep their skirts
    width = max(1, int(round(freq_smooth_hz / (sr / N)))) | 1
    ps = _smooth_freq(power, width)
    ps = (np.pad(ps, ((1, 1), (0, 0)), mode="edge")[:-2] + ps + np.pad(ps, ((1, 1), (0, 0)), mode="edge")[2:]) / 3
    db = 10 * np.log10(ps + 1e-20)
    if profile is not None:
        a = max(0, int(profile[0] // H))
        b = min(frames, max(a + 1, int(math.ceil(profile[1] / H))))
        sel = np.arange(a, b)
    else:
        energy = np.sum(power, axis=1)
        k = max(int(math.ceil(0.1 * frames)), int(math.ceil(0.25 * sr / H)), 1)
        sel = np.argsort(energy, kind="stable")[:min(k, frames)]
    mu = db[sel].mean(axis=0)
    sd = db[sel].std(axis=0)
    thr = mu + (float(threshold_db) if threshold_db is not None else np.maximum(6.0, sensitivity * sd))
    over = db - thr[None, :]
    mask = 1.0 / (1.0 + np.exp(-over))                                 # ~4 dB wide soft decision
    # temporal smoothing: opening at the attack, closing at the release time constant
    fr = sr / H
    ca = math.exp(-1.0 / max(1e-6, attack * fr)) if attack > 0 else 0.0
    cr = math.exp(-1.0 / max(1e-6, release * fr)) if release > 0 else 0.0
    sm = np.empty_like(mask)
    g = mask[0].copy()
    for i in range(frames):
        t = mask[i]
        c = np.where(t > g, ca, cr)
        g = t + (g - t) * c
        sm[i] = g
    floor = 10 ** (-reduction_db / 20)
    gain = floor + (1.0 - floor) * sm
    return istft(X * gain[:, :, None], N, H, n)
