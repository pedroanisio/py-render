"""Numpy DSP building blocks for the audio mix (no scipy).

Signals are float arrays shaped (n, channels) at sample rate `sr`.

Linear time-invariant IIR filters (biquads, K-weighting) run as FFT
convolutions with their impulse response, computed by direct recursion until
it has decayed below -140 dB (exact to float precision for stable filters).
Dynamics (compressors, gates, limiters, ducking) compute their gain at a
1 ms control rate and interpolate it to samples.
"""
from __future__ import annotations

import math

import numpy as np

CONTROL_HZ = 1000.0          # dynamics control rate


# ====================================================================== biquads (RBJ cookbook)
def biquad(kind: str, f0: float, sr: float, q: float = 0.7071, gain_db: float = 0.0):
    """RBJ Audio-EQ-Cookbook coefficients (b, a), normalised so a[0] == 1."""
    f0 = min(max(f0, 1.0), sr * 0.4999)
    w0 = 2 * math.pi * f0 / sr
    c, s = math.cos(w0), math.sin(w0)
    q = max(q, 1e-3)
    alpha = s / (2 * q)
    A = 10 ** (gain_db / 40)
    if kind == "lowpass":
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif kind == "highpass":
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif kind == "bandpass":           # constant 0 dB peak gain
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif kind == "notch":
        b = [1.0, -2 * c, 1.0]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif kind == "peak":
        b = [1 + alpha * A, -2 * c, 1 - alpha * A]
        a = [1 + alpha / A, -2 * c, 1 - alpha / A]
    elif kind == "low-shelf":
        sq = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) - (A - 1) * c + sq), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - sq)]
        a = [(A + 1) + (A - 1) * c + sq, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - sq]
    elif kind == "high-shelf":
        sq = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * c + sq), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sq)]
        a = [(A + 1) - (A - 1) * c + sq, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sq]
    else:
        raise ValueError(f"unknown biquad kind {kind!r}")
    a0 = a[0]
    return np.array(b, np.float64) / a0, np.array(a, np.float64) / a0


def impulse_response(sections, max_len: int = 1 << 19) -> np.ndarray:
    """Impulse response of a cascade of (b, a) biquads, truncated once it has decayed."""
    h = np.zeros(1, np.float64)
    h[0] = 1.0
    for b, a in sections:
        h = _ir_one(h, b, a, max_len)
    return h


def _ir_one(x: np.ndarray, b, a, max_len: int) -> np.ndarray:
    b0, b1, b2 = (float(v) for v in b)
    a1, a2 = float(a[1]), float(a[2])
    out = []
    x1 = x2 = y1 = y2 = 0.0
    energy = 0.0
    tail = 0.0
    n = 0
    xs = x.tolist()
    lx = len(xs)
    while n < max_len:
        xv = xs[n] if n < lx else 0.0
        y = b0 * xv + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, xv, y1, y
        out.append(y)
        e = y * y
        energy += e
        tail = tail * 0.999 + e
        n += 1
        if n > lx + 64 and n % 256 == 0 and tail < energy * 1e-14:
            break
    return np.array(out, np.float64)


def fft_convolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Causal convolution of every channel of x (n, ch) with h, output length n (overlap-add)."""
    n = x.shape[0]
    if n == 0:
        return x.copy()
    L = len(h)
    if L <= 16:
        out = np.zeros_like(x, dtype=np.float64)
        for k, hv in enumerate(h):
            if k >= n:
                break
            out[k:] += hv * x[:n - k]
        return out.astype(np.float32)
    nfft = max(1 << 16, 1 << int(math.ceil(math.log2(8 * L))))
    block = nfft - L + 1
    H = np.fft.rfft(h, nfft)
    out = np.zeros((n + L, x.shape[1]), np.float64)
    for s in range(0, n, block):
        seg = x[s:s + block].astype(np.float64)
        Y = np.fft.irfft(np.fft.rfft(seg, nfft, axis=0) * H[:, None], nfft, axis=0)
        m = min(len(seg) + L - 1, out.shape[0] - s)
        out[s:s + m] += Y[:m]
    return out[:n].astype(np.float32)


def filter_sections(x: np.ndarray, sections) -> np.ndarray:
    if not sections:
        return x
    return fft_convolve(x, impulse_response(sections))


_OP_CACHE: dict = {}


def _one_pole_tables(d: float, L: int, n: int):
    key = (d, L, n)
    hit = _OP_CACHE.get(key)
    if hit is None:
        if len(_OP_CACHE) > 256:
            _OP_CACHE.clear()
        hit = _OP_CACHE[key] = ((1 - d) * d ** np.arange(L), d ** np.arange(1, n + 1))
    return hit


def one_pole(x: np.ndarray, d: float, state=None):
    """Exact one-pole low-pass y[n] = (1 - d) x[n] + d y[n-1] over axis 0, starting from `state`.
    Returns (y, last_state).  Short responses (d^256 < 1e-12) use a truncated-IR convolution,
    long ones a chunked closed form (cumulative sums of x d^-n)."""
    x = np.asarray(x, np.float64)
    one = x.ndim == 1
    X = x[:, None] if one else x
    n, ch = X.shape
    s0 = np.zeros(ch) if state is None else np.broadcast_to(np.asarray(state, np.float64), (ch,)).copy()
    if n == 0:
        return (X[:, 0] if one else X).copy(), s0
    if d <= 0.0:
        y = X.copy()
    else:
        L = int(math.ceil(math.log(1e-12) / math.log(d))) if d < 1 else 1 << 30
        if L <= 256:
            h, dec = _one_pole_tables(d, L, n)
            y = np.empty_like(X)
            for c in range(ch):
                y[:, c] = np.convolve(X[:, c], h)[:n] + s0[c] * dec
        else:
            c_len = max(1, int(8.0 / -math.log10(d)))
            y = np.empty_like(X)
            st = s0.copy()
            for a in range(0, n, c_len):
                blk = X[a:a + c_len]
                k = np.arange(1, blk.shape[0] + 1)[:, None]
                S = st[None, :] + (1 - d) * np.cumsum(blk * d ** (-k), axis=0)
                y[a:a + blk.shape[0]] = S * d ** k
                st = y[a + blk.shape[0] - 1].copy()
    return (y[:, 0] if one else y), y[-1].copy()


def butter_sections(kind: str, f0: float, sr: float, order: int = 2):
    """Butterworth low/high-pass as a cascade of biquads (order even)."""
    order = max(2, order + (order % 2))
    out = []
    for k in range(order // 2):
        q = 1.0 / (2 * math.sin(math.pi * (2 * k + 1) / (2 * order)))
        out.append(biquad(kind, f0, sr, q))
    return out


# ====================================================================== sample-rate conversion
def interp_positions(x: np.ndarray, pos: np.ndarray, cutoff: float = 1.0, taps: int = 16) -> np.ndarray:
    """Band-limited (Kaiser-windowed sinc) read of x (n, ch) at fractional sample positions `pos`.
    cutoff < 1 low-passes (anti-aliasing when reading faster than real time)."""
    n = x.shape[0]
    out = np.zeros((len(pos), x.shape[1]), np.float32)
    if n == 0 or len(pos) == 0:
        return out
    cutoff = float(min(1.0, max(0.02, cutoff)))
    half = int(math.ceil(taps / cutoff / 2))
    half = min(half, 256)
    beta = 8.0
    # kernel table over d in [-(half+1), half+1] at 1/256-sample resolution
    res = 256
    grid = np.linspace(-(half + 1), half + 1, 2 * (half + 1) * res + 1)
    table = cutoff * np.sinc(cutoff * grid) * np.i0(beta * np.sqrt(np.clip(1 - (grid / (half + 1)) ** 2, 0, 1))) / np.i0(beta)
    xp = np.concatenate([np.zeros((half + 1, x.shape[1]), np.float32), x.astype(np.float32),
                         np.zeros((half + 2, x.shape[1]), np.float32)])
    chunk = max(1, (1 << 21) // (2 * half))
    offs = np.arange(-half + 1, half + 1)
    for s in range(0, len(pos), chunk):
        p = pos[s:s + chunk].astype(np.float64)
        i0 = np.floor(p).astype(np.int64)
        idx = i0[:, None] + offs[None, :]                      # (m, taps)
        d = idx - p[:, None]
        w = np.interp(d.ravel(), grid, table).reshape(d.shape).astype(np.float32)
        w[(idx < 0) | (idx >= n)] = 0.0
        idxc = np.clip(idx, -half - 1, n + half) + half + 1
        for c in range(x.shape[1]):
            out[s:s + chunk, c] = np.sum(w * xp[idxc, c], axis=1)
    return out


def resample(x: np.ndarray, sr_in: float, sr_out: float) -> np.ndarray:
    if abs(sr_in - sr_out) < 1e-9 or x.shape[0] == 0:
        return x
    n_out = int(round(x.shape[0] * sr_out / sr_in))
    pos = np.arange(n_out) * (sr_in / sr_out)
    return interp_positions(x, pos, cutoff=min(1.0, sr_out / sr_in) * 0.98)


# ====================================================================== gains, pan, fades
def db_to_lin(db):
    return np.power(10.0, np.asarray(db, np.float64) / 20.0)


def lin_to_db(v, floor: float = -150.0):
    v = np.asarray(v, np.float64)
    return np.maximum(20 * np.log10(np.maximum(v, 1e-30)), floor)


def pan_gains(pan):
    """Constant-power pan normalised to unity at centre: gL = sqrt2*cos(theta), gR = sqrt2*sin(theta),
    theta = (pan+1)*pi/4, so gL^2 + gR^2 == 2 for every pan (hard pan = +3 dB on one side).
    pan is -1 (left) .. +1 (right); values beyond +-1 are clamped."""
    p = np.clip(np.asarray(pan, np.float64), -1.0, 1.0)
    th = (p + 1) * math.pi / 4
    return math.sqrt(2) * np.cos(th), math.sqrt(2) * np.sin(th)


def fade_curve(p, curve: str):
    """Fade-in gain for progress p in [0, 1] (fade-outs use fade_curve(1 - p))."""
    p = np.clip(np.asarray(p, np.float64), 0.0, 1.0)
    if curve == "equal-power":
        return np.sin(p * math.pi / 2)
    if curve == "logarithmic":        # fast start, slow finish
        return np.log10(1 + 9 * p)
    if curve == "exponential":        # slow start, fast finish
        return (np.power(10.0, p) - 1) / 9
    if curve == "s-curve":
        return 0.5 - 0.5 * np.cos(math.pi * p)
    return p


# ====================================================================== levels and envelopes
def block_ms(x: np.ndarray, sr: float, hz: float = CONTROL_HZ) -> np.ndarray:
    """Mean square per control block, averaged over channels -> (nblocks,)."""
    step = max(1, int(round(sr / hz)))
    n = x.shape[0]
    nb = int(math.ceil(n / step))
    if nb == 0:
        return np.zeros(0)
    pad = nb * step - n
    sq = np.mean(np.square(x.astype(np.float64)), axis=1)
    if pad:
        sq = np.concatenate([sq, np.zeros(pad)])
    return sq.reshape(nb, step).mean(axis=1)


def block_peak(x: np.ndarray, sr: float, hz: float = CONTROL_HZ) -> np.ndarray:
    step = max(1, int(round(sr / hz)))
    n = x.shape[0]
    nb = int(math.ceil(n / step))
    if nb == 0:
        return np.zeros(0)
    a = np.max(np.abs(x), axis=1).astype(np.float64)
    pad = nb * step - n
    if pad:
        a = np.concatenate([a, np.zeros(pad)])
    return a.reshape(nb, step).max(axis=1)


def moving_average(v: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(v) == 0:
        return v
    c = np.cumsum(np.concatenate([[0.0], v]))
    half = n // 2
    idx = np.arange(len(v))
    lo = np.clip(idx - half, 0, len(v))
    hi = np.clip(idx - half + n, 0, len(v))
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)


def rms_db_blocks(x: np.ndarray, sr: float, window_s: float = 0.01) -> np.ndarray:
    ms = block_ms(x, sr)
    ms = moving_average(ms, max(1, int(round(window_s * CONTROL_HZ))))
    return lin_to_db(np.sqrt(ms))


def gain_to_samples(g_blocks: np.ndarray, n: int, sr: float, hz: float = CONTROL_HZ) -> np.ndarray:
    """Linear interpolation of per-block gains (block centres) to n samples."""
    if len(g_blocks) == 0:
        return np.ones(n)
    step = sr / hz
    centres = (np.arange(len(g_blocks)) + 0.5) * step
    return np.interp(np.arange(n), centres, g_blocks)


def smooth_gain_db(target_db: np.ndarray, attack: float, release: float, hz: float = CONTROL_HZ) -> np.ndarray:
    """One-pole smoothing of a gain curve in dB: falling uses the attack time constant, rising the release."""
    ca = math.exp(-1.0 / max(1e-6, attack * hz)) if attack > 0 else 0.0
    cr = math.exp(-1.0 / max(1e-6, release * hz)) if release > 0 else 0.0
    out = np.empty_like(target_db)
    g = 0.0 if len(target_db) == 0 else float(target_db[0])
    for i, t in enumerate(target_db.tolist()):
        c = ca if t < g else cr
        g = t + (g - t) * c
        out[i] = g
    return out


def slew_gain_db(target_db: np.ndarray, down_rate: float, up_rate: float, hz: float = CONTROL_HZ) -> np.ndarray:
    """Slew-limited gain curve (dB per second): reaches a step of `amount` dB in amount/rate seconds."""
    dn = down_rate / hz
    up = up_rate / hz
    out = np.empty_like(target_db)
    g = 0.0
    for i, t in enumerate(target_db.tolist()):
        if t < g:
            g = max(t, g - dn)
        else:
            g = min(t, g + up)
        out[i] = g
    return out


def compressor_gain_db(level_db: np.ndarray, threshold: float, ratio: float, knee: float) -> np.ndarray:
    """Static downward compression curve (gain reduction in dB, <= 0), soft knee of width `knee` dB."""
    ratio = max(1.0, ratio)
    over = level_db - threshold
    gr = np.zeros_like(level_db)
    if knee > 0:
        inknee = np.abs(over) <= knee / 2
        gr = np.where(inknee, (1 / ratio - 1) * (over + knee / 2) ** 2 / (2 * knee), gr)
        gr = np.where(over > knee / 2, (1 / ratio - 1) * over, gr)
    else:
        gr = np.where(over > 0, (1 / ratio - 1) * over, 0.0)
    return gr


# ====================================================================== running min / true peak / limiter
def running_min(v: np.ndarray, radius: int) -> np.ndarray:
    """min over [i - radius, i + radius] (van Herk / Gil-Werman, vectorised)."""
    if radius <= 0 or len(v) == 0:
        return v.copy()
    w = 2 * radius + 1
    n = len(v)
    padded = np.concatenate([np.full(radius, np.inf), v, np.full(radius + w, np.inf)])
    m = int(math.ceil(len(padded) / w)) * w
    padded = np.concatenate([padded, np.full(m - len(padded), np.inf)])
    blocks = padded.reshape(-1, w)
    pre = np.minimum.accumulate(blocks, axis=1).ravel()
    suf = np.minimum.accumulate(blocks[:, ::-1], axis=1)[:, ::-1].ravel()
    i = np.arange(n)
    return np.minimum(suf[i], pre[i + w - 1])


_OS_TAPS: dict = {}


def _oversample_filter(factor: int = 4, taps_per_phase: int = 48) -> np.ndarray:
    key = (factor, taps_per_phase)
    h = _OS_TAPS.get(key)
    if h is None:
        L = factor * taps_per_phase
        k = np.arange(L) - (L - 1) / 2
        h = np.sinc(k / factor) * np.kaiser(L, 10.0)
        h = h / h.sum() * factor
        _OS_TAPS[key] = h
    return h


def true_peak_per_sample(x: np.ndarray, factor: int = 4) -> np.ndarray:
    """Per-sample true-peak estimate: max |.| over channels, the sample itself and the `factor`
    band-limited interpolated points around it (4x oversampling, 192-tap Kaiser-windowed sinc;
    stricter than the 48-tap example filter of BS.1770-4 annex 2, so near-Nyquist overs are caught)."""
    h = _oversample_filter(factor)
    n = x.shape[0]
    out = np.max(np.abs(x), axis=1).astype(np.float64)
    if n == 0:
        return out
    L = len(h)
    delay = (L - 1) / 2 / factor                      # group delay in input samples
    for p in range(factor):
        hp = h[p::factor]
        y = fft_convolve(x.astype(np.float32), hp)   # y[i] ~ x(i - delay + p/factor)
        d = int(math.floor(delay - p / factor + 0.5))
        a = np.max(np.abs(y), axis=1).astype(np.float64)
        shifted = np.zeros(n)
        if d < n:
            shifted[:n - d] = a[d:]
        out = np.maximum(out, shifted)
    return out


def true_peak_db(x: np.ndarray) -> float:
    if x.shape[0] == 0:
        return -math.inf
    return float(lin_to_db(np.max(true_peak_per_sample(x))))


def limit(x: np.ndarray, sr: float, ceiling_db: float, lookahead: float = 0.005, release: float = 0.08,
          true_peak: bool = True, peaks: np.ndarray | None = None) -> np.ndarray:
    """Look-ahead brickwall limiter. The gain is a moving average (radius L) of a running minimum
    (radius L) of the required gain, which never exceeds the required gain at any sample; a release
    curve lets it recover smoothly. Peaks are estimated with 4x oversampling when true_peak."""
    n = x.shape[0]
    if n == 0:
        return x
    ceil = 10 ** (ceiling_db / 20)
    if peaks is not None:          # per-sample peaks of x, precomputed (they scale linearly with gain)
        pk = peaks
    else:
        pk = true_peak_per_sample(x) if true_peak else np.max(np.abs(x), axis=1).astype(np.float64)
    req = np.minimum(1.0, ceil / np.maximum(pk, 1e-12))
    if req.min() >= 1.0:
        return x
    L = max(1, int(round(lookahead * sr)))
    m = running_min(req, L)
    s = moving_average(m, 2 * L + 1)
    s = np.minimum(s, req)
    # release: recover at most by a one-pole towards 1, evaluated per control block
    step = max(1, int(round(sr / CONTROL_HZ)))
    nb = int(math.ceil(n / step))
    padded = np.concatenate([s, np.ones(nb * step - n)])
    gb = padded.reshape(nb, step).min(axis=1)
    cr = math.exp(-1.0 / max(1e-6, release * CONTROL_HZ))
    r = np.empty(nb)
    g = 1.0
    for i, v in enumerate(gb.tolist()):
        g = v if v < g else v + (g - v) * cr
        r[i] = g
    # block i's value holds over the block; interpolate between block starts, never above s
    starts = np.arange(nb) * step
    rr = np.interp(np.arange(n), starts, r)
    rr = np.minimum(rr, np.repeat(r, step)[:n])
    gain = np.minimum(s, rr)
    return (x * gain[:, None]).astype(np.float32)


# ====================================================================== BS.1770-4 loudness
def k_weighting_sections(sr: float):
    """BS.1770 K-weighting (pre-filter shelf + RLB high-pass), derived for any rate from the analogue
    prototype as in libebur128; reproduces the standard's 48 kHz coefficients."""
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    shelf = (np.array([(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]),
             np.array([1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]))
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a0 = 1 + K / Q + K * K
    hp = (np.array([1.0, -2.0, 1.0]), np.array([1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]))
    return [shelf, hp]


def _channel_weights(nch: int) -> np.ndarray:
    # L, R, C = 1.0; LFE excluded; surrounds 1.41 (5.1 order L R C LFE Ls Rs)
    if nch == 6:
        return np.array([1, 1, 1, 0, 1.41, 1.41])
    return np.ones(nch)


def integrated_loudness(x: np.ndarray, sr: float, weights: np.ndarray | None = None) -> float:
    """ITU-R BS.1770-4 integrated loudness (LUFS): K-weighting, 400 ms blocks with 75 % overlap,
    absolute gate -70 LUFS, relative gate -10 LU.  `weights`: per-channel G_i (default by count)."""
    x = np.asarray(x, np.float64)
    if x.ndim == 1:
        x = x[:, None]
    y = filter_sections(x.astype(np.float32), k_weighting_sections(sr)).astype(np.float64)
    blk = int(round(0.4 * sr))
    hop = int(round(0.1 * sr))
    if y.shape[0] < blk:
        return -math.inf
    G = _channel_weights(y.shape[1]) if weights is None else np.asarray(weights, np.float64)
    c = np.concatenate([np.zeros((1, y.shape[1])), np.cumsum(y * y, axis=0)])
    starts = np.arange(0, y.shape[0] - blk + 1, hop)
    z = (c[starts + blk] - c[starts]) / blk               # (nblocks, ch)
    zs = z @ G
    lj = -0.691 + 10 * np.log10(np.maximum(zs, 1e-30))
    abs_ok = lj > -70.0
    if not abs_ok.any():
        return -math.inf
    gamma_r = -0.691 + 10 * math.log10(max(float(np.mean(zs[abs_ok])), 1e-30)) - 10.0
    ok = abs_ok & (lj > gamma_r)
    if not ok.any():
        return -math.inf
    return float(-0.691 + 10 * math.log10(max(float(np.mean(zs[ok])), 1e-30)))


def short_term_loudness(x: np.ndarray, sr: float, hop: float = 0.1, window: float = 3.0,
                        weights: np.ndarray | None = None, centred: bool = False):
    """EBU Tech 3341 short-term loudness (3 s sliding window, K-weighted, LUFS) every `hop` seconds.
    Returns (times, lufs): the window ends at each time, or is centred on it when `centred`
    (partial windows at the edges are averaged over the samples they hold)."""
    x = np.asarray(x, np.float64)
    if x.ndim == 1:
        x = x[:, None]
    n = x.shape[0]
    y = filter_sections(x.astype(np.float32), k_weighting_sections(sr)).astype(np.float64)
    G = _channel_weights(y.shape[1]) if weights is None else np.asarray(weights, np.float64)
    e = np.concatenate([[0.0], np.cumsum((y * y) @ G)])
    k = int(math.floor(n / (hop * sr))) + 1
    t = np.arange(k) * hop
    c = np.round(t * sr).astype(np.int64)
    w = int(round(window * sr))
    if centred:
        a, b = np.clip(c - w // 2, 0, n), np.clip(c + w - w // 2, 0, n)
    else:
        a, b = np.clip(c - w, 0, n), np.clip(c, 0, n)
    ms = (e[b] - e[a]) / np.maximum(b - a, 1)
    ms = np.where(b - a > 0, ms, 0.0)
    return t, -0.691 + 10 * np.log10(np.maximum(ms, 1e-30))


DYN_ATTACK, DYN_RELEASE = 0.5, 2.0        # s: gain falling / rising (dynamic normalisation)
DYN_RANGE = (-30.0, 20.0)                 # dB of gain the AGC may apply
DYN_GATE = 30.0                           # LU under the target below which the gain holds


def dynamic_gain_db(x: np.ndarray, sr: float, target: float, weights: np.ndarray | None = None,
                    hop: float = 0.1) -> tuple[np.ndarray, np.ndarray]:
    """Broadcast-style loudness AGC.  Every `hop` (100 ms) the K-weighted programme is measured over a
    3 s window centred on that time (offline look-ahead) counting only the 100 ms blocks louder than
    the gate max(-70, target - DYN_GATE) LUFS (gated short-term loudness, so pauses neither pump the
    gain up nor dilute the measurement); gain = target - loudness, limited to DYN_RANGE.  Windows
    with less than 0.5 s of gated programme hold the previous gain.  The gain is smoothed in dB by a
    one-pole with DYN_ATTACK when falling and DYN_RELEASE when rising.  Returns (times, gain_db)."""
    x = np.asarray(x, np.float64)
    if x.ndim == 1:
        x = x[:, None]
    n = x.shape[0]
    y = filter_sections(x.astype(np.float32), k_weighting_sections(sr)).astype(np.float64)
    G = _channel_weights(y.shape[1]) if weights is None else np.asarray(weights, np.float64)
    step = max(1, int(round(hop * sr)))
    nb = int(math.ceil(n / step))
    z = (y * y) @ G
    z = np.concatenate([z, np.zeros(nb * step - n)]).reshape(nb, step).mean(axis=1)
    ok = (-0.691 + 10 * np.log10(np.maximum(z, 1e-30))) > max(-70.0, target - DYN_GATE)
    ce = np.concatenate([[0.0], np.cumsum(z * ok)])
    cn = np.concatenate([[0], np.cumsum(ok)])
    half = int(round(1.5 / hop))
    k = np.arange(nb + 1)
    lo, hi = np.clip(k - half, 0, nb), np.clip(k + half, 0, nb)
    cnt = cn[hi] - cn[lo]
    S = -0.691 + 10 * np.log10(np.maximum((ce[hi] - ce[lo]) / np.maximum(cnt, 1), 1e-30))
    t = k * step / sr
    gated = cnt < int(round(0.5 / hop))
    if gated.all():
        return t, np.zeros(len(t))
    raw = np.clip(target - S, *DYN_RANGE)
    held = np.empty_like(raw)
    g = raw[np.argmax(~gated)]
    for i in range(len(raw)):
        if not gated[i]:
            g = raw[i]
        held[i] = g
    return t, smooth_gain_db(held, DYN_ATTACK, DYN_RELEASE, hz=1.0 / hop)


# ====================================================================== dither / quantise
def quantize(x: np.ndarray, bits: int, dither: bool, seed: int) -> np.ndarray:
    """Float [-1, 1] -> integer PCM with optional TPDF dither (+-1 LSB, seeded)."""
    q = float(2 ** (bits - 1))
    v = x.astype(np.float64) * (q - 1)
    if dither:
        from ..noise import Rng
        rng = Rng(seed)                     # D24 draws (CONVENTIONS 5.19)
        v = v + rng.random(v.shape) - rng.random(v.shape)
    v = np.clip(np.round(v), -q, q - 1)
    return v.astype(np.int32 if bits > 16 else np.int16)
