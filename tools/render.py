import sys, math, subprocess, time as _time, textwrap, re
import xml.etree.ElementTree as ET
import numpy as np, cairo
XML = '/mnt/user-data/uploads/the-last-floor-episode-01_manga_scene.xml'
ROOT = ET.parse(XML).getroot()
PRJ = ROOT.find('project')
FW, FH, FPS, DUR = int(PRJ.get('width')), int(PRJ.get('height')), int(PRJ.get('fps')), float(PRJ.get('duration'))
OW, OH = 1920, 1080; S = OW / FW
BYID = {e.get('id'): e for e in ROOT.iter() if e.get('id')}
ASSETS = {e.get('id'): e for e in ROOT.find('assets')}
STY = {s.get('id'): s for s in ROOT.find('styles')}
FX = {e.get('id'): e for e in ROOT.find('effects')}
COMP = ROOT.find('composition')
SHOTS = [g for g in COMP if g.tag == 'group']
YEL = '#E3BC48'

def col(s, a=1.0):
    s = s.lstrip('#'); r, g, b = (int(s[i:i + 2], 16) / 255 for i in (0, 2, 4))
    if len(s) == 8: a *= int(s[6:8], 16) / 255
    return r, g, b, a

def ease(name, u):
    if name == 'ease-in-out': return 4 * u ** 3 if u < .5 else 1 - (-2 * u + 2) ** 3 / 2
    return u

_AN = {}
def prop(el, name, t, default=0.0):
    key = (id(el), name)
    if key not in _AN:
        an = [a for a in el.findall('animate') if a.get('property') == name]
        _AN[key] = None if not an else (an[0].get('defaultInterpolation', 'linear'), [(float(k.get('time')), float(k.get('value'))) for k in an[0].findall('key')])
    a = _AN[key]
    if a is None:
        v = el.get(name); return float(v) if v is not None else default
    interp, ks = a
    if t <= ks[0][0]: return ks[0][1]
    if t >= ks[-1][0]: return ks[-1][1]
    for (t0, v0), (t1, v1) in zip(ks, ks[1:]):
        if t0 <= t <= t1: return v0 + (v1 - v0) * ease(interp, (t - t0) / (t1 - t0))

def active(el, t):
    s, e = el.get('start'), el.get('end')
    return (s is None or t >= float(s)) and (e is None or t < float(e))

# ------------------------------------------------------------------ text
_M = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8))
def setfont(cr, bold, size):
    cr.select_font_face('DejaVu Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(size)
def wrap(text, bold, size, width):
    setfont(_M, bold, size); lines = []
    for para in text.split('\n'):
        cur = ''
        for w in para.split(' '):
            tr = (cur + ' ' + w).strip()
            if cur and _M.text_extents(tr).x_advance > width: lines.append(cur); cur = w
            else: cur = tr
        lines.append(cur)
    return lines
_LAY = {}
def layout(a):
    k = a.get('id')
    if k in _LAY: return _LAY[k]
    st = STY[a.get('style')]; bold = ASSETS[st.get('fontAsset')].get('weight') == '700'
    size = float(a.get('maxSize', a.get('size'))); mn = float(a.get('minSize', size))
    w, h = float(a.get('width')), float(a.get('height')); lh = float(st.get('lineHeight', 1.15))
    while True:
        lines = wrap(a.get('text'), bold, size, w); setfont(_M, bold, size)
        widest = max(_M.text_extents(l).x_advance for l in lines)
        if (len(lines) * size * lh <= h and widest <= w) or size <= mn: break
        size -= 2
    _LAY[k] = (bold, size, lines, lh, w, h); return _LAY[k]
def draw_text(cr, a, alpha=1.0):
    bold, size, lines, lh, w, h = layout(a)
    cr.save(); cr.rectangle(0, 0, w, h); cr.clip(); setfont(cr, bold, size)
    asc = cr.font_extents()[0]; th = len(lines) * size * lh
    y0 = (h - th) / 2 if a.get('verticalAlign') == 'middle' else 0
    cr.set_source_rgba(*col(a.get('color') or STY[a.get('style')].get('color'), alpha))
    for i, l in enumerate(lines):
        tw = cr.text_extents(l).x_advance
        x0 = (w - tw) / 2 if a.get('align') == 'center' else 0
        cr.move_to(x0, y0 + i * size * lh + (size * lh - size) / 2 + asc); cr.show_text(l)
    cr.restore()

# ------------------------------------------------------------------ placeholder plates (4096x2304 plate space, drawn at output scale)
PW, PH = 4096, 2304
MARK = {}
for m in ROOT.find('markers'):
    MARK[m.get('id')] = m
CHAPTER = {}
for g in SHOTS:
    c = ROOT.find('composition')
import itertools
_chap = sorted([(float(m.get('time')), m.get('label')) for m in ROOT.find('markers') if m.get('kind') == 'chapter'])
def chapter_at(t):
    lab = ''
    for tt, l in _chap:
        if tt <= t + 1e-6: lab = l
    return lab
YWORDS = re.compile(r'(yellow|thirteen|13\b|seam)', re.I)

def plate_bg(shot_id, desc, seed):
    w, h = int(PW * S), int(PH * S)
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w * rng.uniform(0.35, 0.65), h * rng.uniform(0.3, 0.55)
    d = np.sqrt(((xx - cx) / w) ** 2 + ((yy - cy) / h) ** 2)
    base = 0.2 + 0.20 * np.exp(-d * 3.2)                      # paper-gray pool of light
    # coarse screentone shadow: dot pattern whose size grows with darkness
    per = 7; ang = math.radians(45)
    u = (xx * math.cos(ang) + yy * math.sin(ang)) / per; v = (-xx * math.sin(ang) + yy * math.cos(ang)) / per
    dots = np.sqrt((u - np.round(u)) ** 2 + (v - np.round(v)) ** 2)
    shade = np.clip(d * 1.6, 0, 1)
    base = base - 0.10 * (dots < 0.45 * shade)
    # hatching band across the plate
    hat = ((xx + yy * rng.uniform(0.6, 1.4)) % 11 < 1.2) & (np.sin(xx / w * 3 + rng.uniform(0, 6)) + yy / h > 0.9)
    base = base - 0.06 * hat
    img = np.clip(base, 0, 1)
    rgb = np.stack([img * 0.96, img * 0.95, img * 0.93], -1)
    if YWORDS.search(desc):
        band = np.exp(-((xx - w * 0.5) / (w * 0.012)) ** 2) * np.clip(1 - np.abs(yy / h - 0.45) * 2.2, 0, 1)
        y = np.array(col(YEL)[:3], np.float32)
        rgb = rgb * (1 - band[..., None] * 0.9) + y * band[..., None] * 0.9
    a = np.empty((h, w, 4), np.uint8)
    a[..., 0] = rgb[..., 2] * 255; a[..., 1] = rgb[..., 1] * 255; a[..., 2] = rgb[..., 0] * 255; a[..., 3] = 255
    s = cairo.ImageSurface.create_for_data(bytearray(a.tobytes()), cairo.FORMAT_ARGB32, w, h)
    c = cairo.Context(s); c.scale(S, S)
    # faint outline shot number
    num = shot_id.split('_')[1]
    setfont(c, True, 900); c.set_line_width(4)
    ext = c.text_extents(num); c.move_to(PW - ext.x_advance - 520, PH - 560)
    c.text_path(num); c.set_source_rgba(1, 1, 1, 0.07); c.stroke()
    return s

def plate_fg(shot_id, desc, t0, card=None):
    w, h = int(PW * S), int(PH * S)
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h); c = cairo.Context(s); c.scale(S, S)
    cw, ch = 2900, 760; x0, y0 = (PW - cw) / 2, 788 - ch / 2
    if card: cx_, cy_, cw, ch = card; x0, y0 = cx_ - cw / 2, cy_ - ch / 2
    c.rectangle(x0, y0, cw, ch); c.set_source_rgba(0.03, 0.03, 0.04, 0.78); c.fill_preserve()
    c.set_line_width(6); c.set_source_rgba(0.92, 0.9, 0.87, 0.55); c.stroke()
    head = f"{shot_id.replace('_', ' ').upper()}  ·  {chapter_at(t0)}" if not card else shot_id.replace('_', ' ').upper()
    setfont(c, True, 50); c.set_source_rgba(*col('#9C988F')); c.move_to(x0 + 70, y0 + 105); c.show_text(head)
    setfont(c, False, 44); tag = 'ART PENDING · PLACEHOLDER'
    c.set_source_rgba(*col(YEL, 0.9)); c.move_to(x0 + cw - 70 - c.text_extents(tag).x_advance, y0 + 105); c.show_text(tag)
    size = 84
    while True:
        lines = wrap(desc, False, size, cw - 140)
        if len(lines) * size * 1.2 <= ch - 190 or size <= 50: break
        size -= 4
    setfont(c, False, size); asc = c.font_extents()[0]
    ty = y0 + 150 + (ch - 190 - len(lines) * size * 1.2) / 2
    for i, l in enumerate(lines):
        x = x0 + 70; y = ty + i * size * 1.2 + asc
        for tok in re.split(r'(\s+)', l):
            if not tok: continue
            c.set_source_rgba(*col(YEL if YWORDS.fullmatch(tok.strip('.,;:')) else '#F4F1E9'))
            c.move_to(x, y); c.show_text(tok); x += c.text_extents(tok).x_advance
    return s

_PL = {}
def plates(shot):
    k = shot.get('id')
    if k not in _PL:
        _PL.clear()
        seed = int(k.split('_')[1]) * 7919 + 131804
        card = None
        art = shot.find('group')
        if art is not None and art.find('mask') is not None:
            m = art.find('mask'); card = (float(m.get('x')) + float(m.get('width')) / 2 + 224, float(m.get('y')) + float(m.get('height')) / 2 + 264, 1400, 700)
        _PL[k] = (plate_bg(k, shot.get('name'), seed), plate_fg(k, shot.get('name'), float(shot.get('start')), card))
    return _PL[k]

# ------------------------------------------------------------------ effect overlays (precomputed, output scale)
AW, AH = int(3648 * S), int(1776 * S)
def _surf(rgba):
    h, w = rgba.shape[:2]; b = np.empty((h, w, 4), np.uint8)
    a = rgba[..., 3:4].astype(np.float32) / 255
    b[..., 0] = (rgba[..., 2] * a[..., 0]); b[..., 1] = (rgba[..., 1] * a[..., 0]); b[..., 2] = (rgba[..., 0] * a[..., 0]); b[..., 3] = rgba[..., 3]
    return cairo.ImageSurface.create_for_data(bytearray(b.tobytes()), cairo.FORMAT_ARGB32, w, h)
_rng = np.random.default_rng(int(FX['fx_grain'].get('seed')))
g = FX['fx_grain']; gmix = float(g.get('mix')) * float(g.get('amount')) * 6
GRAIN = []
for i in range(8):
    n = _rng.standard_normal((AH, AW))
    v = np.clip(128 + n * 60, 0, 255)
    rgba = np.stack([v, v, v, np.full_like(v, 255 * min(1, gmix))], -1).astype(np.uint8); GRAIN.append(_surf(rgba))
ht = FX['fx_tone']; hs = float(ht.get('size')) * 2 * S * 2; ang = math.radians(float(ht.get('angle')))
yy, xx = np.mgrid[0:AH, 0:AW].astype(np.float32)
u = (xx * math.cos(ang) + yy * math.sin(ang)) / hs; v = (-xx * math.sin(ang) + yy * math.cos(ang)) / hs
dd = np.sqrt((u - np.round(u)) ** 2 + (v - np.round(v)) ** 2)
dots = (dd < 0.3).astype(np.float32)
TONE = _surf(np.stack([np.zeros_like(dots)] * 3 + [dots * 255 * float(ht.get('mix'))], -1).astype(np.uint8))
sc = FX['fx_cctv']; lines = ((yy % (float(sc.get('size')) * S * 2)) < float(sc.get('size')) * S).astype(np.float32)
SCAN = _surf(np.stack([np.zeros_like(lines)] * 3 + [lines * 255 * float(sc.get('intensity')) * float(sc.get('mix')) * 2.2], -1).astype(np.uint8))
del yy, xx, u, v, dd

# ------------------------------------------------------------------ drawing
def rrect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2); cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0); cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi); cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi); cr.close_path()

def draw_shape(cr, el, t):
    if not active(el, t): return
    x, y, w, h = prop(el, 'x', t), prop(el, 'y', t), float(el.get('width')), float(el.get('height'))
    sx, sy = prop(el, 'scaleX', t, 1.0), prop(el, 'scaleY', t, 1.0)
    cr.save()
    if el.find('animate') is not None and (sx != 1 or sy != 1):
        cr.translate(x + w / 2, y + h / 2); cr.scale(sx, sy); cr.translate(-w / 2, -h / 2)
    else:
        cr.translate(x, y); cr.scale(sx, sy)
    if 'fx_yellow_glow' in (el.get('effects') or ''):
        gl = FX['fx_yellow_glow']; rad = float(gl.get('radius')) * 6; inten = float(gl.get('intensity'))
        for k in range(8, 0, -1):
            e = rad * k / 8
            rrect(cr, -e, -e * 0.5, w + 2 * e, h + e, e); cr.set_source_rgba(*col(gl.get('color'), inten * 0.18)); cr.fill()
    if el.get('shape') == 'rounded-rect': rrect(cr, 0, 0, w, h, float(el.get('radius', 0)))
    else: cr.rectangle(0, 0, w, h)
    fill = col(el.get('fill', '#00000000'))
    if fill[3] > 0: cr.set_source_rgba(*fill); cr.fill_preserve()
    if el.get('stroke'):
        cr.set_line_width(float(el.get('strokeWidth', 1))); cr.set_source_rgba(*col(el.get('stroke'))); cr.stroke()
    cr.new_path(); cr.restore()

def draw_layer(cr, el, t, shot):
    if not active(el, t): return
    a = ASSETS[el.get('asset')]
    op = prop(el, 'opacity', t, 1.0)
    if op <= 0: return
    cr.save()
    x, y = prop(el, 'x', t), prop(el, 'y', t)
    if a.tag == 'image':
        bg, fg = plates(shot)
        surf = fg if a.get('id').endswith('_fg') else bg
        ax, ay = el.get('anchorX'), el.get('anchorY')
        rot = prop(el, 'rotation', t)
        if ax is not None: cr.translate(x - float(ax), y - float(ay))
        else: cr.translate(x, y)
        cr.save(); cr.scale(1 / S, 1 / S); cr.set_source_surface(surf, 0, 0); cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.paint_with_alpha(op); cr.restore()
        if ax is not None and el.find("animate[@property='rotation']") is not None:
            # stand-in focal subject: a head silhouette turning on the authored neck pivot
            cr.translate(float(ax), float(ay) + 380); cr.rotate(math.radians(rot))
            cr.set_line_width(14); cr.set_source_rgba(*col('#EAE6DD', 0.9)); cr.move_to(0, 0); cr.line_to(0, -170); cr.stroke()
            cr.arc(0, -290, 120, 0, 2 * math.pi); cr.set_source_rgba(*col('#1A1A1E', 0.95)); cr.fill_preserve()
            cr.set_source_rgba(*col(YEL)); cr.set_line_width(22); cr.stroke()
            cr.arc(0, -290, 150, math.pi * 1.05, math.pi * 1.95); cr.set_line_width(40); cr.stroke()
    else:
        cr.translate(x, y); cr.scale(prop(el, 'scaleX', t, 1.0), prop(el, 'scaleY', t, 1.0))
        draw_text(cr, a, op)
    cr.restore()

def draw_particles(cr, el, t):
    if not active(el, t): return
    rate, life = float(el.get('rate')), float(el.get('lifetime'))
    spd, dirn, spread = float(el.get('speed')), float(el.get('direction')), float(el.get('spread'))
    ew, eh = float(el.get('emitterWidth')), float(el.get('emitterHeight')); trail = float(el.get('trail'))
    seed = int(el.get('seed')); st = float(el.get('start')) - float(el.get('preroll', 0))
    i0, i1 = int(max(0, (t - st - life) * rate)), int((t - st) * rate) + 1
    idx = np.arange(i0, i1)
    if not len(idx): return
    h = (idx * 2654435761 + seed * 97) % 4294967296
    r1 = (h % 10007) / 10007; r2 = ((h // 10007) % 10009) / 10009; r3 = ((h // 7) % 997) / 997
    birth = st + idx / rate; age = t - birth
    ok = (age >= 0) & (age < life)
    ang = np.radians(dirn + (r3 - 0.5) * 2 * spread)
    vx, vy = np.cos(ang) * spd, np.sin(ang) * spd
    px = r1 * (ew + 400) - 200 + vx * age; py = r2 * eh - eh * 0.6 + vy * age
    cr.set_source_rgba(*col(el.get('color'))); cr.set_line_width(float(el.get('size')) * 1.5)
    for x, y, dx, dy in zip(px[ok], py[ok], vx[ok] * trail, vy[ok] * trail):
        cr.move_to(x, y); cr.line_to(x - dx, y - dy)
    cr.stroke()

def zkey(e): return float(e.get('z', 0))

def draw_art(cr, g, t, shot):
    x, y, w, h = float(g.get('x')), float(g.get('y')), float(g.get('width')), float(g.get('height'))
    cr.save(); cr.translate(x, y); cr.rectangle(0, 0, w, h); cr.clip()
    m = g.find('mask')
    if m is not None: cr.push_group()
    for k in sorted([k for k in g if k.tag in ('layer', 'shape', 'particleEmitter')], key=zkey):
        if k.tag == 'layer': draw_layer(cr, k, t, shot)
        elif k.tag == 'shape': draw_shape(cr, k, t)
        else: draw_particles(cr, k, t)
    effs = (g.get('effects') or '').split()
    cr.save(); cr.scale(1 / S, 1 / S)
    if 'fx_tone' in effs: cr.set_source_surface(TONE, 0, 0); cr.paint()
    if 'fx_cctv' in effs: cr.set_source_surface(SCAN, 0, 0); cr.paint()
    if 'fx_grain' in effs:
        cr.set_operator(cairo.OPERATOR_OVERLAY); cr.set_source_surface(GRAIN[int(t * FPS) % 8], 0, 0); cr.paint()
    cr.restore()
    if m is not None:
        cr.pop_group_to_source()
        mx, my, mw, mh, fe = (float(m.get(k)) for k in ('x', 'y', 'width', 'height', 'feather'))
        cx, cy, rr = mx + mw / 2, my + mh / 2, mw / 2
        rg = cairo.RadialGradient(cx, cy, max(0, rr - fe), cx, cy, rr)
        rg.add_color_stop_rgba(0, 0, 0, 0, 1); rg.add_color_stop_rgba(1, 0, 0, 0, 0)
        if mw != mh:
            pass
        cr.mask(rg)
    cr.restore()

def draw_group(cr, g, t, shot):
    if not active(g, t): return
    if g.get('id', '').endswith('_art'): return draw_art(cr, g, t, shot)
    cr.save(); cr.translate(prop(g, 'x', t), prop(g, 'y', t))
    for k in sorted(list(g), key=zkey):
        if k.tag == 'shape': draw_shape(cr, k, t)
        elif k.tag == 'layer': draw_layer(cr, k, t, shot)
        elif k.tag == 'group': draw_group(cr, k, t, shot)
    cr.restore()

SHOT_T = [(float(g.get('start')), float(g.get('end')), g) for g in SHOTS]
def render(f):
    t = f / FPS
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, OW, OH); cr = cairo.Context(s)
    cr.set_source_rgba(*col(PRJ.get('background'))); cr.paint(); cr.scale(S, S)
    for a, b, g in SHOT_T:
        if a <= t < b:
            cr.save(); cr.translate(0, 0)
            for k in sorted(list(g), key=zkey):
                if k.tag == 'shape': draw_shape(cr, k, t)
                elif k.tag == 'layer': draw_layer(cr, k, t, g)
                elif k.tag == 'group': draw_group(cr, k, t, g)
            cr.restore()
    s.flush(); return s

if __name__ == '__main__':
    if sys.argv[1] == 'png':
        for tt in sys.argv[2:]:
            t0 = _time.time(); render(round(float(tt) * FPS)).write_to_png(f'/tmp/lf/t_{tt}.png'); print(tt, f'{_time.time() - t0:.2f}s', flush=True)
    else:
        f0, f1, out = int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
        p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgra', '-s', f'{OW}x{OH}', '-r', str(FPS), '-i', '-',
                              '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-tune', 'animation', '-pix_fmt', 'yuv420p', '-g', '48',
                              '-color_primaries', 'bt709', '-color_trc', 'iec61966-2-1', '-colorspace', 'bt709', out], stdin=subprocess.PIPE)
        ts = _time.time()
        for f in range(f0, f1):
            p.stdin.write(bytes(render(f).get_data()))
            if f % 240 == 0: print(f, f'{(_time.time() - ts) / (f - f0 + 1):.3f}s/f', flush=True)
        p.stdin.close(); p.wait(); print('done', flush=True)
