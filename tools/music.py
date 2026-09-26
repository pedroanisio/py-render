import numpy as np
from scipy.signal import butter, sosfilt, fftconvolve
SR = 48000; DUR = 90.0; N = int(SR * DUR)
rng = np.random.default_rng(20260924)
BPM = 96; beat = 60 / BPM; bar = 4 * beat
t = np.arange(N) / SR
mid = lambda n: 440 * 2 ** ((n - 69) / 12)
# Dm9 - Bbmaj7 - Fadd9 - C(sus2), one bar each
CH = [[50, 53, 57, 60, 64], [46, 50, 53, 57, 62], [41, 48, 53, 57, 55 + 12], [48, 50, 55, 60, 62]]
BASS = [38, 34, 41, 36]
L = np.zeros(N); R = np.zeros(N)

def env_adsr(n, a, d, s, r, sus_len):
    e = np.concatenate([np.linspace(0, 1, max(1, int(a*SR))), np.linspace(1, s, max(1, int(d*SR))),
                        np.full(max(0, int(sus_len*SR)), s), np.linspace(s, 0, max(1, int(r*SR)))])
    return e[:n] if len(e) >= n else np.pad(e, (0, n - len(e)))

def lp(x, fc, order=2): return sosfilt(butter(order, fc, 'low', fs=SR, output='sos'), x)
def hp(x, fc, order=2): return sosfilt(butter(order, fc, 'high', fs=SR, output='sos'), x)

nbars = int(np.ceil(DUR / bar))
pad = np.zeros((2, N)); arp = np.zeros((2, N)); bass = np.zeros(N); drums = np.zeros((2, N))
for b in range(nbars):
    s0 = int(b * bar * SR); ch = CH[b % 4]
    n = min(int(bar * SR * 1.3), N - s0)
    if n <= 0: break
    tt = np.arange(n) / SR
    e = env_adsr(n, 0.6, 0.5, 0.8, 0.9, bar - 1.1)
    for j, m in enumerate(ch):
        for side, det in ((0, -0.08), (1, 0.08)):
            f = mid(m + det)
            w = 2 * (tt * f % 1) - 1                      # saw
            pad[side, s0:s0+n] += w * e * 0.035
    # bass: root on beats 1 and 3
    for k in (0, 2):
        s1 = s0 + int(k * beat * SR); m = int(beat * 1.8 * SR)
        if s1 + m > N: m = N - s1
        if m <= 0: continue
        tb = np.arange(m) / SR; f = mid(BASS[b % 4])
        bass[s1:s1+m] += (np.sin(2*np.pi*f*tb) + 0.3*np.sin(4*np.pi*f*tb)) * np.exp(-tb*1.6) * (1 - np.exp(-tb*200)) * 0.28
    # pluck arpeggio in 8ths from 3 s onwards
    if b * bar >= 3:
        seq = [ch[0]+12, ch[2]+12, ch[1]+12, ch[3]+12, ch[4]+12, ch[3]+12, ch[2]+12, ch[1]+12]
        for k, m_ in enumerate(seq):
            s1 = s0 + int(k * beat / 2 * SR); m = int(0.6 * SR)
            if s1 + m > N: m = N - s1
            if m <= 0: continue
            tp = np.arange(m) / SR; f = mid(m_)
            tone = (np.sin(2*np.pi*f*tp) + 0.25*np.sin(6*np.pi*f*tp)) * np.exp(-tp * 7) * (1 - np.exp(-tp*400))
            pan = 0.5 + 0.3 * np.sin(k * 1.3 + b)
            arp[0, s1:s1+m] += tone * (1 - pan) * 0.09; arp[1, s1:s1+m] += tone * pan * 0.09
    # soft kick beats 1 & 3 after 5 s, hats on offbeats
    if 5 <= b * bar < 85:
        for k in range(4):
            s1 = s0 + int(k * beat * SR)
            if k in (0, 2):
                m = min(int(0.35*SR), N - s1); tk = np.arange(m) / SR
                kick = np.sin(2*np.pi*(48*tk + 60*(1-np.exp(-tk*30))/30)) * np.exp(-tk*9) * 0.35
                drums[:, s1:s1+m] += kick
            s2 = s1 + int(beat / 2 * SR); m = min(int(0.06*SR), N - s2)
            if m > 0:
                hn = rng.standard_normal(m) * np.exp(-np.arange(m)/SR*70) * 0.05
                drums[0, s2:s2+m] += hn * 0.8; drums[1, s2:s2+m] += hn

pad = np.stack([lp(p, 1800) for p in pad])
bass = lp(bass, 400)
drums = np.stack([hp(d, 30) for d in drums])
for c in range(2): drums[c] = drums[c]  # noop placeholder for clarity
# chapter swells: gentle reverse-noise riser into each chapter boundary
for tb in (10, 20, 31, 42, 54, 65, 77, 85):
    m = int(1.2 * SR); s1 = int(tb * SR) - m
    sw = hp(lp(rng.standard_normal(m), 5000), 800) * np.linspace(0, 1, m) ** 3 * 0.05
    L[s1:s1+m] += sw; R[s1:s1+m] += sw
mixL = pad[0] + arp[0] + bass + drums[0] + L
mixR = pad[1] + arp[1] + bass + drums[1] + R
# simple plate-ish reverb send on pad + arp
ir_t = np.arange(int(1.8 * SR)) / SR
for c, (src, out) in enumerate(((pad[0] + arp[0], 'L'), (pad[1] + arp[1], 'R'))):
    ir = rng.standard_normal(len(ir_t)) * np.exp(-ir_t * 3.2); ir /= np.sqrt((ir**2).sum())
    wet = fftconvolve(src, ir)[:N] * 0.35
    if c == 0: mixL += wet
    else: mixR += wet
x = np.stack([mixL, mixR], 1)
x /= np.abs(x).max() * 1.05
from scipy.io import wavfile
wavfile.write('/tmp/sb/news-bed_f32.wav', SR, x.astype(np.float32))
print('ok', x.shape)
