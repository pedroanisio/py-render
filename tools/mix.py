import numpy as np, xml.etree.ElementTree as ET, re, subprocess, sys
from scipy.io import wavfile
sys.path.insert(0, '/tmp/lf'); from sound import *
r = ET.parse('/mnt/user-data/uploads/the-last-floor-episode-01_manga_scene.xml').getroot()
A = {e.get('id'): e for e in r.find('assets')}
M = r.find('audioMix'); DUR = 1320.0; N = int(DUR * SR)
CCTV = [(1166.625, 1233.208333)]
SCENE = {0: ('elevator', 37, 0.55), 1: ('title', 36, 0.3), 2: ('rain', 38, 0.3), 3: ('lobby', 36, 0.45), 4: ('elevator', 37, 0.6),
         5: ('apartment', 35, 0.5), 6: ('classroom', 38, 0.25), 7: ('kitchen', 36, 0.45), 8: ('night', 35, 0.8), 9: ('night', 34, 0.95),
         10: ('dawn', 38, 0.35), 11: ('corridor', 33, 0.5)}

def asset_buf(aid, start):
    a = A[aid]; d = float(a.get('duration', 0) or 0)
    if aid.startswith('dialogue'):
        _, x = wavfile.read(f'/tmp/lf/vo/{aid[:12]}.wav'); return np.stack([x, x]).astype(float)
    m = re.match(r'(ambience|music)_(\d+)', aid)
    if m:
        k = int(m.group(2)); kind, root, inten = SCENE[k]
        sil = [(max(0, a0 - start), b0 - start) for a0, b0 in CCTV if a0 < start + d and b0 > start]
        y = ambience(kind, d, 1000 + k, sil) if m.group(1) == 'ambience' else music(d, 2000 + k, root, inten, sil)
        tgt = -29 if m.group(1) == 'ambience' else -25
        rms = np.sqrt((y ** 2).mean()) + 1e-9
        return y * 10 ** (tgt / 20) / rms
    kind = aid.split('_')[-2] if re.search(r'_\d+$', aid) else aid.split('_')[-1]
    import zlib
    return fx(kind, d, zlib.crc32(aid.encode()) % 10000)

def render_bus(bid):
    buf = np.zeros((2, N), np.float32)
    for tr in M.findall('audioTrack'):
        if tr.get('bus') != bid: continue
        st = float(tr.get('start')); ci, co = float(tr.get('clipIn', 0)), float(tr.get('clipOut'))
        x = asset_buf(tr.get('asset'), st)
        if x.ndim == 1: x = np.stack([x, x])
        x = x[:, int(ci * SR):int(co * SR)].astype(np.float64)
        x *= 10 ** (float(tr.get('gain', 0)) / 20)
        pan = float(tr.get('pan', 0)); th = (pan + 1) * np.pi / 4
        x[0] *= np.cos(th) * np.sqrt(2); x[1] *= np.sin(th) * np.sqrt(2)
        for attr, side in (('fadeIn', 0), ('fadeOut', 1)):
            f = float(tr.get(attr, 0)); m = min(int(f * SR), x.shape[1])
            if m > 0:
                ramp = np.linspace(0, 1, m)
                if side == 0: x[:, :m] *= ramp
                else: x[:, -m:] *= ramp[::-1]
        s = int(st * SR); e = min(N, s + x.shape[1])
        buf[:, s:e] += x[:, :e - s].astype(np.float32)
    return buf
out = np.zeros((2, N), np.float32)
d = render_bus('bus_dialogue')
dia = np.abs(d).mean(0); win = int(0.02 * SR)
dec = 48; env = np.convolve(dia[::dec], np.ones(win // dec) / (win // dec), 'same')
active = (env > 10 ** (-40 / 20)).astype(float)
out += d; del d, dia
print('dialogue done', flush=True)
for b in M.findall('bus'):
    if b.get('id') == 'bus_dialogue': continue
    x = render_bus(b.get('id')); x *= 10 ** (float(b.get('gain', 0)) / 20)
    if b.get('duckUnder'):
        amt = float(b.get('duckAmount')); at = float(b.get('duckAttack', 0.05)); rl = float(b.get('duckRelease', 0.4))
        g = np.zeros_like(active); cur = 0.0
        ka, kr = 1 - np.exp(-dec / (at * SR)), 1 - np.exp(-dec / (rl * SR))
        for i, v in enumerate(active):
            cur += (v - cur) * (ka if v > cur else kr); g[i] = cur
        for c0 in range(0, N, SR * 60):
            c1 = min(N, c0 + SR * 60)
            gg = np.interp(np.arange(c0, c1), np.arange(len(g)) * dec, g)
            x[:, c0:c1] *= (10 ** (amt * gg / 20)).astype(np.float32)
    out += x; del x
    print(b.get('id'), 'done', flush=True)
wavfile.write('/tmp/lf/aud/premix.wav', SR, np.ascontiguousarray(out.T))
print('premix done', np.abs(out).max())
