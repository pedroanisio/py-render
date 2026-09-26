import sys, math, subprocess, time as _time
import xml.etree.ElementTree as ET
import numpy as np, cairo

import os
XML = os.environ.get('SCENE', '/tmp/sb/v2/starbucks-closures_scene_v2.xml'); BASE = os.path.dirname(XML)
ROOT = ET.parse(XML).getroot()
PRJ = ROOT.find('project')
W, H, FPS, DUR = int(PRJ.get('width')), int(PRJ.get('height')), int(PRJ.get('fps')), float(PRJ.get('duration'))
BYID = {e.get('id'): e for e in ROOT.iter() if e.get('id')}
FAM = {'font_regular': cairo.FONT_WEIGHT_NORMAL, 'font_bold': cairo.FONT_WEIGHT_BOLD}

def col(s, a=1.0):
    s = s.lstrip('#'); r, g, b = (int(s[i:i+2], 16) / 255 for i in (0, 2, 4))
    if len(s) == 8: a *= int(s[6:8], 16) / 255
    return r, g, b, a

def ease(name, u):
    if name == 'ease-in-out': return u * u * (3 - 2 * u) if False else (4*u**3 if u < .5 else 1 - (-2*u+2)**3/2)
    return u

def prop(el, name, t, default=0.0):
    an = [a for a in el.findall('animate') if a.get('property') == name]
    if not an:
        v = el.get(name); return float(v) if v is not None else default
    a = an[0]; ks = [(float(k.get('time')), float(k.get('value'))) for k in a.findall('key')]
    if t <= ks[0][0]: return ks[0][1]
    if t >= ks[-1][0]: return ks[-1][1]
    for (t0, v0), (t1, v1) in zip(ks, ks[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * ease(a.get('defaultInterpolation', 'linear'), (t - t0) / (t1 - t0))

def active(el, t):
    s, e = el.get('start'), el.get('end')
    return (s is None or t >= float(s)) and (e is None or t < float(e))

# ------------------------------------------------------------------ text
_M = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8))
def setfont(cr, fid, size):
    cr.select_font_face('DejaVu Sans', cairo.FONT_SLANT_NORMAL, FAM[fid]); cr.set_font_size(size)

def wrap(text, fid, size, width):
    setfont(_M, fid, size); lines = []
    for para in text.split('\n'):
        cur = ''
        for w in para.split(' '):
            trial = (cur + ' ' + w).strip()
            if cur and _M.text_extents(trial).x_advance > width:
                lines.append(cur); cur = w
            else: cur = trial
        lines.append(cur)
    return lines

_LAY = {}
def layout(a):
    k = a.get('id')
    if k in _LAY: return _LAY[k]
    fid, size = a.get('fontAsset'), float(a.get('size'))
    w, h, lh = float(a.get('width')), float(a.get('height')), float(a.get('lineHeight', 1.2))
    mn, ml = float(a.get('minSize', size)), int(a.get('maxLines', 99))
    while True:
        lines = wrap(a.get('text'), fid, size, w)
        setfont(_M, fid, size)
        widest = max(_M.text_extents(l).x_advance for l in lines)
        if (len(lines) * size * lh <= h + 0.5 and widest <= w and len(lines) <= ml) or size <= mn: break
        size = max(mn, size - 1)
    _LAY[k] = (fid, size, lines, lh, w, h)
    return _LAY[k]

def draw_text(cr, a):
    fid, size, lines, lh, w, h = layout(a)
    cr.save(); cr.rectangle(0, 0, w, h); cr.clip()
    setfont(cr, fid, size); asc = cr.font_extents()[0]
    cr.set_source_rgba(*col(a.get('color')))
    right = a.get('align') == 'right'
    for i, l in enumerate(lines):
        x0 = w - cr.text_extents(l).x_advance if right else 0
        cr.move_to(x0, i * size * lh + (size * lh - size) / 2 + asc)
        cr.show_text(l)
    cr.restore()

# ------------------------------------------------------------------ images
from PIL import Image
_IMG = {}
ZMAX = 1.08
def image_surface(el):
    k = el.get('id')
    if k in _IMG: return _IMG[k]
    a = BYID[el.get('asset')]
    im = Image.open(os.path.join(BASE, a.get('src'))).convert('RGB')
    bw, bh = float(el.get('width')), float(el.get('height'))
    fx, fy = float(el.get('focusX', .5)), float(el.get('focusY', .5))
    iw, ih = im.size; tr = bw / bh
    if iw / ih > tr: cw, ch = ih * tr, ih
    else: cw, ch = iw, iw / tr
    x0 = min(max(fx * iw - cw / 2, 0), iw - cw); y0 = min(max(fy * ih - ch / 2, 0), ih - ch)
    ow, oh = int(bw * ZMAX), int(bh * ZMAX)
    im = im.crop((x0, y0, x0 + cw, y0 + ch)).resize((ow, oh), Image.LANCZOS)
    x = np.asarray(im).astype(np.float32) / 255
    fx_ = el.find('effect')
    if fx_ is not None and fx_.get('type') == 'color-grade':
        lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
        lum = (lin * [0.2126, 0.7152, 0.0722]).sum(-1, keepdims=True)
        sat = float(fx_.get('saturation', 1)); lin = lum + (lin - lum) * sat
        lin = lin * float(fx_.get('brightness', 1)) ** 2.2
        tc = np.array(col(fx_.get('tint', '#000000'))[:3]); tc = np.where(tc <= 0.04045, tc / 12.92, ((tc + 0.055) / 1.055) ** 2.4)
        ta = float(fx_.get('tintAmount', 0)); lin = lin * (1 - ta) + tc * ta
        x = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.clip(lin, 0, None) ** (1 / 2.4) - 0.055)
    x = np.clip(x * 255 + np.random.default_rng(1).uniform(-.5, .5, x.shape), 0, 255).astype(np.uint8)
    b = np.empty((oh, ow, 4), np.uint8); b[..., 0] = x[..., 2]; b[..., 1] = x[..., 1]; b[..., 2] = x[..., 0]; b[..., 3] = 255
    surf = cairo.ImageSurface.create_for_data(bytearray(b.tobytes()), cairo.FORMAT_ARGB32, ow, oh)
    _IMG[k] = surf; return surf

def draw_image(cr, el, t):
    surf = image_surface(el)
    bw, bh = float(el.get('width')), float(el.get('height'))
    z = prop(el, 'scale', t, 1.0) / ZMAX
    cr.save(); cr.rectangle(prop(el, 'x', t), prop(el, 'y', t), bw, bh); cr.clip()
    cr.translate(prop(el, 'x', t) + bw / 2, prop(el, 'y', t) + bh / 2); cr.scale(z, z)
    cr.translate(-surf.get_width() / 2, -surf.get_height() / 2)
    cr.set_source_surface(surf, 0, 0); cr.get_source().set_filter(cairo.FILTER_BILINEAR)
    cr.paint_with_alpha(prop(el, 'opacity', t, 1.0)); cr.restore()

# ------------------------------------------------------------------ shapes
def path_d(cr, d):
    tok = d.replace(',', ' ').split(); i = 0; cmd = None
    while i < len(tok):
        if tok[i].isalpha(): cmd = tok[i]; i += 1
        if cmd == 'Z': cr.close_path(); continue
        x, y = float(tok[i]), float(tok[i+1]); i += 2
        (cr.move_to if cmd == 'M' else cr.line_to)(x, y)

def rrect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi/2, 0); cr.arc(x + w - r, y + h - r, r, 0, math.pi/2)
    cr.arc(x + r, y + h - r, r, math.pi/2, math.pi); cr.arc(x + r, y + r, r, math.pi, 1.5*math.pi); cr.close_path()

def draw_shape(cr, el, t, alpha=1.0):
    if el.get('id') == 'backdrop':
        cr.set_source_surface(BG, 0, 0); cr.paint(); return
    x, y = prop(el, 'x', t), prop(el, 'y', t)
    w, h = prop(el, 'width', t), prop(el, 'height', t)
    sx = prop(el, 'scaleX', t, 1.0)
    op = prop(el, 'opacity', t, 1.0) * alpha
    if sx <= 0 or op <= 0: return
    cr.save(); cr.translate(x, y); cr.scale(sx, 1)
    kind = el.get('shape')
    if kind == 'rect': cr.rectangle(0, 0, w, h)
    elif kind == 'rounded-rect': rrect(cr, 0, 0, w, h, float(el.get('radius', 0)))
    elif kind == 'path': path_d(cr, el.get('path'))
    fill = el.get('fill')
    if fill.startswith('url(#'):
        g = BYID[fill[5:-1]]
        lg = cairo.LinearGradient(float(g.get('x1')) * w, float(g.get('y1')) * h, float(g.get('x2')) * w, float(g.get('y2')) * h)
        for st in g.findall('stop'): lg.add_color_stop_rgba(float(st.get('offset')), *col(st.get('color'), op))
        cr.set_source(lg)
    else:
        cr.set_source_rgba(*col(fill, op))
    cr.fill(); cr.restore()

def zkey(el): return float(el.get('z', 0))

def draw_node(cr, el, t):
    tag = el.tag
    if not active(el, t): return
    if tag == 'shape': draw_shape(cr, el, t)
    elif tag == 'layer' and BYID[el.get('asset')].tag == 'image':
        draw_image(cr, el, t)
    elif tag == 'layer':
        cr.save(); cr.translate(prop(el, 'x', t), prop(el, 'y', t)); draw_text(cr, BYID[el.get('asset')]); cr.restore()
    elif tag == 'instance':
        sym = BYID[el.get('symbol')]; op = prop(el, 'opacity', t, 1.0)
        cr.save(); cr.translate(prop(el, 'x', t), prop(el, 'y', t))
        cr.scale(prop(el, 'scaleX', t, 1.0), prop(el, 'scaleY', t, 1.0))
        if op < 1: cr.push_group()
        for k in sym: draw_node(cr, k, t)
        if op < 1: cr.pop_group_to_source(); cr.paint_with_alpha(op)
        cr.restore()
    elif tag == 'group':
        op = prop(el, 'opacity', t, 1.0)
        if op <= 0: return
        cr.save(); cr.translate(prop(el, 'x', t), prop(el, 'y', t))
        m = el.find('mask')
        if m is not None:
            cr.rectangle(0, 0, prop(m, 'width', t), prop(m, 'height', t)); cr.clip()
        iso = op < 1
        if iso: cr.push_group()
        for k in sorted([k for k in el if k.tag in ('shape', 'layer', 'instance', 'group')], key=zkey):
            draw_node(cr, k, t)
        if iso: cr.pop_group_to_source(); cr.paint_with_alpha(op)
        cr.restore()

# ------------------------------------------------------------------ background (linear-light gradient)
def make_bg():
    g = BYID['background_gradient']; st = g.findall('stop')
    c0 = np.array(col(st[0].get('color'))[:3]); c1 = np.array(col(st[1].get('color'))[:3])
    lin = lambda c: np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    enc = lambda c: np.where(c <= 0.0031308, c * 12.92, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    # objectBoundingBox (0,0)->(1,1): project onto the diagonal in unit space
    u = ((xx / W) + (yy / H)) / 2
    rgb = enc(lin(c0)[None, None] * (1 - u[..., None]) + lin(c1)[None, None] * u[..., None])
    rng = np.random.default_rng(int(PRJ.get('seed')))
    rgb = rgb * 255 + rng.uniform(-0.5, 0.5, rgb.shape)  # dither against banding
    a = np.empty((H, W, 4), np.uint8)
    a[..., 0] = np.clip(rgb[..., 2], 0, 255); a[..., 1] = np.clip(rgb[..., 1], 0, 255)
    a[..., 2] = np.clip(rgb[..., 0], 0, 255); a[..., 3] = 255
    s = cairo.ImageSurface.create_for_data(bytearray(a.tobytes()), cairo.FORMAT_ARGB32, W, H)
    return s
BG = make_bg()

# ------------------------------------------------------------------ captions (burned)
CT = ROOT.find('captions/captionTrack'); CS = BYID[CT.get('style')]
CUES = [(float(c.get('start')), float(c.get('end')), c.get('text')) for c in CT.findall('cue')]
def cap_lines(text):
    import textwrap
    return textwrap.wrap(text, int(CT.get('maxCharsPerLine')), break_on_hyphens=False)[:int(CT.get('maxLines'))]

def draw_caption(cr, t):
    cur = [c for c in CUES if c[0] <= t < c[1]]
    if not cur: return
    lines = cap_lines(cur[0][2]); size = float(CS.get('size')); lh = float(CS.get('lineHeight'))
    setfont(cr, CS.get('fontAsset'), size); asc = cr.font_extents()[0]
    cx = W * float(CT.get('x').rstrip('%')) / 100; y0 = float(CT.get('y'))
    # shadow via small blurred offset stack
    blur = float(CS.get('shadowBlur'))
    for pass_ in ('shadow', 'text'):
        for i, l in enumerate(lines):
            tw = cr.text_extents(l).x_advance
            bx, by = cx - tw / 2, y0 + i * size * lh + (size * lh - size) / 2 + asc
            if pass_ == 'shadow':
                cr.set_source_rgba(*col(CS.get('shadowColor'), 0.22))
                for dx, dy in ((-blur, 0), (blur, 0), (0, -blur), (0, blur), (blur*.7, blur*.7), (-blur*.7, blur*.7), (blur*.7, -blur*.7), (-blur*.7, -blur*.7)):
                    cr.move_to(bx + dx, by + dy + 2); cr.show_text(l)
            else:
                cr.set_source_rgba(*col(CS.get('color'))); cr.move_to(bx, by); cr.show_text(l)

COMP = ROOT.find('composition')
TOP = sorted(list(COMP), key=zkey)

def render(f):
    t = f / FPS
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H); cr = cairo.Context(s)
    cr.set_source_rgba(*col(PRJ.get('background'))); cr.paint()
    for el in TOP:
        draw_node(cr, el, t)
        if el.get('id') == 'caption_back': draw_caption(cr, t)
    s.flush(); return s

if __name__ == '__main__':
    if sys.argv[1] == 'png':
        for tt in sys.argv[2:]:
            t0 = _time.time(); render(round(float(tt) * FPS)).write_to_png(f'/tmp/sb/v2t_{tt}.png'); print(tt, f'{_time.time()-t0:.2f}s')
    else:
        f0, f1, out = int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
        p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgra', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                              '-c:v', 'libx264', '-preset', sys.argv[5], '-crf', '18', '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-g', '60',
                              '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', out], stdin=subprocess.PIPE)
        ts = _time.time()
        for f in range(f0, f1):
            p.stdin.write(bytes(render(f).get_data()))
            if f % 30 == 0: print(f, f'{(_time.time()-ts)/(f-f0+1):.3f}s/f', flush=True)
        p.stdin.close(); p.wait(); print('done', flush=True)
