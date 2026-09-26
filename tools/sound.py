import numpy as np, xml.etree.ElementTree as ET, os
from scipy.signal import butter, sosfilt, fftconvolve
from scipy.io import wavfile
SR = 48000
def bp(x, lo, hi, o=2): return sosfilt(butter(o, [lo, hi], 'band', fs=SR, output='sos'), x)
def lp(x, f, o=2): return sosfilt(butter(o, f, 'low', fs=SR, output='sos'), x)
def hp(x, f, o=2): return sosfilt(butter(o, f, 'high', fs=SR, output='sos'), x)
def smooth_noise(rng, n, rate):  # slowly varying 0..1 control
    k = max(2, int(n / SR * rate) + 2); pts = rng.random(k)
    return np.interp(np.linspace(0, k - 1, n), np.arange(k), pts)
def reverb_ir(rng, sec, bright=4000):
    t = np.arange(int(sec * SR)) / SR
    ir = [lp(rng.standard_normal(len(t)), bright) * np.exp(-t * 6.9 / sec) for _ in range(2)]
    return [i / np.sqrt((i ** 2).sum()) for i in ir]
def stereo_verb(x, rng, sec, mix):
    irL, irR = reverb_ir(rng, sec)
    xm = x.mean(0) if x.ndim == 2 else x
    wl, wr = fftconvolve(xm, irL)[:len(xm)], fftconvolve(xm, irR)[:len(xm)]
    d = np.stack([xm, xm]) if x.ndim == 1 else x
    return d * (1 - mix) + np.stack([wl, wr]) * mix * 1.5
def mid(n): return 440 * 2 ** ((n - 69) / 12)
def fade_tail(x, sec=1.5, head=0.0):
    n = int(sec * SR); x[..., -n:] *= np.linspace(1, 0, n) ** 1.5
    if head: h = int(head * SR); x[..., :h] *= np.linspace(0, 1, h)
    return x

# ------------------------------------------------------------------ ambience
def ambience(kind, dur, seed, silences=()):
    rng = np.random.default_rng(seed); n = int(dur * SR); t = np.arange(n) / SR
    out = np.zeros((2, n))
    air = [lp(hp(rng.standard_normal(n), 60), 900) * 0.05 for _ in range(2)]
    out += np.stack(air) * (0.6 + 0.4 * smooth_noise(rng, n, 0.1))
    if kind in ('elevator', 'lobby', 'corridor', 'kitchen'):
        hum = 0.018 * (np.sin(2 * np.pi * 120 * t) + 0.5 * np.sin(2 * np.pi * 240 * t) + 0.25 * np.sin(2 * np.pi * 360 * t + 1))
        buzz = bp(rng.standard_normal(n), 2800, 5200) * 0.006 * (0.5 + 0.5 * np.sin(2 * np.pi * 120 * t))
        g = {'elevator': 1.0, 'lobby': 0.6, 'corridor': 0.8, 'kitchen': 0.5}[kind]
        out += (hum + buzz) * g
    if kind == 'elevator':
        rum = lp(rng.standard_normal(n), 90, 4) * 0.35 * (0.7 + 0.3 * smooth_noise(rng, n, 0.3))
        out += np.stack([rum, rum])
    if kind in ('rain', 'lobby'):
        g = 1.0 if kind == 'rain' else 0.25
        for c in range(2):
            r = hp(rng.standard_normal(n), 500) * 0.06
            r += lp(rng.standard_normal(n), 300) * 0.05
            drops = (rng.random(n) < 40 / SR) * rng.random(n)
            r += bp(drops, 1500, 6000) * 1.4
            out[c] += r * g * (0.8 + 0.2 * smooth_noise(rng, n, 0.2))
    if kind == 'classroom':
        for c in range(2):
            b = np.zeros(n)
            for v in range(6):
                syl = smooth_noise(rng, n, 5 + v) ** 3 * smooth_noise(rng, n, 0.3)
                b += bp(rng.standard_normal(n), 250 + 90 * v, 900 + 150 * v) * syl
            out[c] += lp(b, 1800) * 0.05
    if kind in ('apartment', 'kitchen', 'night'):
        fr = 0.01 * np.sin(2 * np.pi * 50 * t) * (smooth_noise(rng, n, 0.02) > 0.35)
        out += fr
    if kind == 'night':
        for k in range(int(dur)):
            s = int((k + 0.02) * SR); m = int(0.012 * SR)
            if s + m < n: out[:, s:s + m] += bp(rng.standard_normal(m), 2500, 7000) * 0.05 * np.exp(-np.arange(m) / SR * 400)
    if kind == 'dawn':
        for k in range(int(dur / 3)):
            s = int(rng.uniform(0, dur - 1) * SR); m = int(rng.uniform(0.08, 0.25) * SR); tt = np.arange(m) / SR
            f0 = rng.uniform(2800, 4200); ch = np.sin(2 * np.pi * (f0 * tt + 400 * np.sin(2 * np.pi * rng.uniform(8, 20) * tt) / 20)) * np.sin(np.pi * tt / tt[-1])
            pan = rng.random(); out[0, s:s + m] += ch * 0.012 * (1 - pan); out[1, s:s + m] += ch * 0.012 * pan
        out += np.stack([lp(rng.standard_normal(n), 400) * 0.04 * smooth_noise(rng, n, 0.15) for _ in range(2)])
    if kind == 'corridor':
        out = stereo_verb(out, rng, 3.5, 0.6)
    for a, b in silences:
        s0, s1 = int(a * SR), int(b * SR); rmp = int(0.3 * SR)
        env = np.ones(n); env[s0:s1] = 0
        env[max(0, s0 - rmp):s0] = np.linspace(1, 0, min(rmp, s0)); env[s1:s1 + rmp] = np.linspace(0, 1, len(env[s1:s1 + rmp]))
        out *= env
    return fade_tail(out)

# ------------------------------------------------------------------ music
def music(dur, seed, root, inten, silences=()):
    rng = np.random.default_rng(seed); n = int(dur * SR); out = np.zeros((2, n))
    # low string clusters
    tpos = rng.uniform(0, 4)
    while tpos < dur - 3:
        L = rng.uniform(8, 16) * (1.2 - inten * 0.4); s = int(tpos * SR); m = min(int(L * SR), n - s); tt = np.arange(m) / SR
        chord = [root - 12, root - 11] if rng.random() < 0.3 + inten * 0.5 else [root - 12, root - 5]
        env = np.sin(np.pi * np.clip(tt / L, 0, 1)) ** 2
        for k, note in enumerate(chord):
            for side, det in ((0, -0.1), (1, 0.1)):
                f = mid(note + det) * (1 + 0.003 * np.sin(2 * np.pi * 5.2 * tt + k))
                ph = np.cumsum(f) / SR
                w = 2 * (ph % 1) - 1
                out[side, s:s + m] += lp(w, 500 + 500 * inten) * env * 0.05 * (0.6 + inten * 0.6)
        tpos += L * rng.uniform(0.9, 1.6 - inten * 0.5)
    # bowed metal events
    tpos = rng.uniform(2, 8)
    ratios = [1, 2.32, 4.25, 6.63, 9.38]
    while tpos < dur - 4:
        L = rng.uniform(5, 10); s = int(tpos * SR); m = min(int(L * SR), n - s); tt = np.arange(m) / SR
        f0 = mid(root + 12 + rng.choice([0, 3, 6, 11])) * rng.uniform(0.99, 1.01)
        bow = 0.6 + 0.4 * lp(rng.standard_normal(m), 12) * 8
        env = (1 - np.exp(-tt * rng.uniform(0.4, 1.2))) * np.exp(-tt * 0.35)
        sig = np.zeros(m)
        for i, r_ in enumerate(ratios):
            sig += np.sin(2 * np.pi * f0 * r_ * tt * (1 + 0.0015 * np.sin(2 * np.pi * 0.3 * tt))) / (i + 1) ** 1.2
        sig += bp(rng.standard_normal(m), f0 * 0.9, f0 * 1.1) * 0.8
        sig *= env * bow * 0.045 * (0.5 + inten)
        pan = rng.uniform(0.2, 0.8)
        out[0, s:s + m] += sig * (1 - pan); out[1, s:s + m] += sig * pan
        tpos += rng.uniform(7, 18) * (1.3 - inten * 0.6)
    # heartbeat for high intensity
    if inten > 0.7:
        bpm = 58; k = 0
        while (k * 60 / bpm) < dur - 1:
            for off, a in ((0, 1), (0.28, 0.6)):
                s = int((k * 60 / bpm + off) * SR); m = int(0.25 * SR)
                if s + m < n:
                    tt = np.arange(m) / SR; out[:, s:s + m] += np.sin(2 * np.pi * 52 * tt) * np.exp(-tt * 18) * 0.12 * a * (inten - 0.6) * 2.5
            k += 1
    out = stereo_verb(out, rng, 4.0, 0.45)
    for a, b in silences:
        s0, s1 = int(a * SR), int(b * SR); out[:, s0:s1] *= 0.35
    return fade_tail(out, 1.5, 0.5)

# ------------------------------------------------------------------ fx
def fx(kind, dur, seed):
    rng = np.random.default_rng(seed); n = int(dur * SR); t = np.arange(n) / SR; x = np.zeros(n)
    if kind == 'chime':
        for off, note in ((0, 88), (0.42, 84)):
            s = int(off * SR); tt = t[:n - s]
            x[s:] += (np.sin(2 * np.pi * mid(note) * tt) + 0.3 * np.sin(2 * np.pi * mid(note) * 2.76 * tt) * np.exp(-tt * 6)) * np.exp(-tt * 2.2) * 0.4
        return stereo_verb(x, rng, 1.2, 0.3)
    if kind == 'drag':
        wet = lp(rng.standard_normal(n), 700) * (0.4 + 0.6 * smooth_noise(rng, n, 6)) ** 2
        squ = bp(rng.standard_normal(n), 300, 1400) * (smooth_noise(rng, n, 9) > 0.7)
        x = (wet * 0.6 + squ * 0.3) * np.linspace(0.2, 1, n) ** 2
        return stereo_verb(x, rng, 1.5, 0.3)
    if kind == 'scream':
        f = 330 + 260 * np.clip(t / 0.25, 0, 1) - 80 * np.clip((t - 1.2) / 0.8, 0, 1)
        f = f * (1 + 0.02 * np.sin(2 * np.pi * 6 * t) + 0.01 * rng.standard_normal(n).cumsum() / np.sqrt(n))
        ph = np.cumsum(f) / SR; src = 2 * (ph % 1) - 1 + 0.25 * rng.standard_normal(n)
        y = sum(bp(src, fc * 0.85, fc * 1.15) * g for fc, g in ((800, 1), (1200, 0.7), (2600, 0.35)))
        env = np.clip(t / 0.08, 0, 1) * np.clip((dur - t) / 0.6, 0, 1)
        return stereo_verb(np.tanh(y * env * 2) * 0.5, rng, 2.2, 0.45)
    if kind == 'knock':
        m = int(0.18 * SR); tt = np.arange(m) / SR
        k = (np.sin(2 * np.pi * 110 * tt) * 0.8 + bp(rng.standard_normal(m), 150, 1800) * 0.6) * np.exp(-tt * 28)
        x[:m] += k; return stereo_verb(x, rng, 0.8, 0.25)
    if kind == 'breath':
        env = np.sin(np.pi * np.clip(t / 0.55, 0, 1)) ** 1.5
        x = bp(rng.standard_normal(n), 900, 4200) * env * 0.35; return np.stack([x, x])
    if kind == 'phone':
        for k in range(2):
            s = int(k * 0.9 * SR); m = int(0.45 * SR); tt = np.arange(m) / SR
            if s + m <= n: x[s:s + m] += np.sign(np.sin(2 * np.pi * 175 * tt)) * lp(np.ones(m), 1) * 0.15 * (0.6 + 0.4 * np.sin(2 * np.pi * 27 * tt))
        x = lp(x, 1600); return np.stack([x, x])
    if kind == 'slam':
        boom = np.sin(2 * np.pi * 48 * t) * np.exp(-t * 5) * 0.9
        clang = sum(np.sin(2 * np.pi * f * t) * np.exp(-t * d) for f, d in ((310, 7), (743, 9), (1190, 12))) * 0.25
        x = np.tanh((boom + clang + bp(rng.standard_normal(n), 80, 2000) * np.exp(-t * 30)) * 1.5) * 0.7
        return stereo_verb(x, rng, 1.8, 0.35)
    if kind == 'scrape':
        nails = bp(rng.standard_normal(n), 2500, 7000) * (smooth_noise(rng, n, 25) > 0.45) * 0.25
        fab = lp(rng.standard_normal(n), 900) * 0.3 * smooth_noise(rng, n, 2)
        return stereo_verb((nails + fab) * np.clip(t / 0.2, 0, 1) * np.clip((dur - t) / 0.4, 0, 1), rng, 1.0, 0.25)
    if kind == 'ring':
        on = ((t % 6) < 2.0).astype(float)
        x = (np.sin(2 * np.pi * 440 * t) + np.sin(2 * np.pi * 480 * t)) * 0.15 * on
        x = bp(x, 300, 3400); return stereo_verb(x, rng, 1.5, 0.3)
    raise ValueError(kind)
