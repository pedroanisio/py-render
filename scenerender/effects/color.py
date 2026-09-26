"""Straight-colour corrections; display-referred operators run in encoded sRGB.

The schema does not define tint/tritone palettes: tint uses black -> color,
tritone uses keyColor (black) -> color (grey) -> white. Gradient overlays use
paint/source stops along angle in frame pixels. Selective colour adjusts the
keyColor neighbourhood towards color, rather than implementing a CMYK UI.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from . import (Params, display_rgb, from_display, gradient_colors, grid, luma,
               premul, result, smoothstep, straight)
from ..raster import linear_to_srgb, srgb_to_linear
from ..registry import EFFECTS, FULL, PARTIAL, warn_once


def _saturate(rgb, amount):
    y = luma(rgb)[..., None]
    return y + (rgb-y)*amount


def _channel(rgb, a, channel, fn):
    if channel == "alpha":
        return rgb, fn(a)
    if channel in ("red", "green", "blue"):
        rgb = rgb.copy()
        i = ("red", "green", "blue").index(channel)
        rgb[..., i] = fn(rgb[..., i])
    elif channel == "luma":
        y = luma(rgb)[..., None]
        rgb = rgb * (fn(y) / np.maximum(y, 1e-7))
        rgb = np.where(y > 1e-7, rgb, fn(y))
    else:
        rgb = fn(rgb)
    return rgb, a


@EFFECTS.register("color-grade", level=FULL, note="encoded sRGB saturation, centred contrast, additive brightness")
def color_grade(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    rgb = (_saturate(rgb, p.n("saturation", 1))-.5)*p.n("contrast", 1)+.5+p.n("brightness", 0)
    return from_display(rc, buf, rgb, a)


@EFFECTS.register("lift-gamma-gain", level=FULL, note="encoded sRGB lift, inverse gamma, gain per channel")
def lift_gamma_gain(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    rgb = np.maximum(rgb + p.vec("lift", 0)*(1-rgb), 0)
    rgb = np.power(rgb, 1/np.maximum(p.vec("gamma", 1), 1e-4))*p.vec("gain", 1)
    return from_display(rc, buf, rgb, a)


@EFFECTS.register("cdl", level=FULL, note="ASC slope-offset-power then Rec.709 saturation in encoded sRGB")
def cdl(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    rgb = np.power(np.clip(rgb*p.vec("slope", 1)+p.vec("offset", 0), 0, 1),
                   np.maximum(p.vec("power", 1), 1e-4))
    return from_display(rc, buf, _saturate(rgb, p.n("saturation", 1)), a)


def _read_cube(path):
    sizes, rows = {}, []
    lo, hi = np.zeros(3), np.ones(3)
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        parts = line.split("#", 1)[0].split()
        if not parts or parts[0] == "TITLE":
            continue
        tag = parts[0]
        if tag in ("LUT_1D_SIZE", "LUT_3D_SIZE"):
            sizes[tag] = int(parts[1])
        elif tag in ("DOMAIN_MIN", "DOMAIN_MAX"):
            value = np.asarray(parts[1:], np.float32)
            if value.shape != (3,):
                raise ValueError("domain needs three values")
            if tag == "DOMAIN_MIN":
                lo = value
            else:
                hi = value
        elif tag[0].isalpha():
            raise ValueError(f"unsupported cube directive {tag}")
        else:
            rows.append([float(x) for x in parts])
    if len(sizes) != 1 or not np.isfinite([lo, hi]).all() or np.any(hi <= lo):
        raise ValueError("expected one 1D or 3D table and a nonempty domain")
    tag, size = next(iter(sizes.items()))
    if size < 2:
        raise ValueError("LUT size must be at least two")
    table = np.asarray(rows, np.float32)
    dim = 1 if tag == "LUT_1D_SIZE" else 3
    if table.shape != (size**dim, 3) or not np.isfinite(table).all():
        raise ValueError("invalid LUT data")
    # .cube lists red fastest, followed by green, then blue.
    if dim == 3:
        table = table.reshape(size, size, size, 3)
    return table, lo, hi, size, dim


@EFFECTS.register("lut", level=PARTIAL, note=".cube 1D/3D interpolation in sRGB; other formats/spaces and shaper+3D skipped")
def lut(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    src = p.s("src")
    if not src:
        return buf.copy()
    if Path(src).suffix.lower() != ".cube" or p.s("space", "srgb") != "srgb":
        warn_once("effect-lut", src, "only standalone sRGB .cube LUTs are supported; skipped")
        return buf.copy()
    path = rc.doc.resolve_path(src)
    try:
        stat = Path(path).stat()
        key = ("effect-cube", path, stat.st_mtime_ns, stat.st_size)
        data = rc.cache.get(key)
        if data is None:
            data = rc.cache[key] = _read_cube(path)
        table, lo, hi, size, dim = data
    except (OSError, UnicodeError, ValueError, IndexError) as exc:
        warn_once("effect-lut", src, f"cannot read LUT: {exc}; skipped")
        return buf.copy()
    rgb, a = display_rgb(rc, buf.px)
    pos = np.clip((rgb-lo)/(hi-lo), 0, 1)*(size-1)
    i = np.floor(pos).astype(int)
    j, f = np.minimum(i+1, size-1), pos-i
    out = np.zeros_like(rgb)
    if dim == 1:
        for c in range(3):
            out[..., c] = table[i[..., c], c]*(1-f[..., c])+table[j[..., c], c]*f[..., c]
    else:
        for r in range(2):
            for g in range(2):
                for b in range(2):
                    idx = [j[..., c] if k else i[..., c] for c, k in enumerate((r, g, b))]
                    weight = np.prod([f[..., c] if k else 1-f[..., c] for c, k in enumerate((r, g, b))], axis=0)
                    out += table[idx[2], idx[1], idx[0]]*weight[..., None]
    return from_display(rc, buf, out, a)


@EFFECTS.register("curves", level=FULL, note="piecewise linear x,y control points, selected colour/alpha/luma channel")
def curves(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    try:
        points = np.asarray([float(x) for x in p.s("curve", "0,0 1,1").replace(",", " ").split()]).reshape(-1, 2)
        points = points[np.argsort(points[:, 0], kind="stable")]
        if len(points) < 2 or not np.isfinite(points).all():
            raise ValueError("too few points")
    except ValueError:
        warn_once("effect", "curves-data", "curve must contain at least two x,y pairs; skipped")
        return buf.copy()
    rgb, a = display_rgb(rc, buf.px)
    rgb, a = _channel(rgb, a, p.s("channel", "rgb"), lambda v: np.interp(v, points[:, 0], points[:, 1]))
    return from_display(rc, buf, rgb, a)


@EFFECTS.register("levels", level=FULL, note="input/output endpoints and inverse gamma on selected channel")
def levels(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    ib, iw, ob, ow = [p.n(k, v) for k, v in (("inputBlack", 0), ("inputWhite", 1), ("outputBlack", 0), ("outputWhite", 1))]
    gamma = max(float(p.vec("gamma", 1)[0]), 1e-4)
    rgb, a = display_rgb(rc, buf.px)
    rgb, a = _channel(rgb, a, p.s("channel", "rgb"),
                      lambda v: ob + (ow-ob)*np.power(np.clip((v-ib)/max(iw-ib, 1e-7), 0, 1), 1/gamma))
    return from_display(rc, buf, rgb, a)


@EFFECTS.register("white-balance", level=PARTIAL, note="temperature delta in kelvin and tint via diagonal RGB adaptation")
def white_balance(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = straight(buf.px)
    t, m = p.n("temperature", 0)/6500, p.n("tint", 0)/100
    factors = np.exp2(np.clip([t+m/2, -m, -t+m/2], -16, 16))
    return result(buf, premul(np.clip(rgb*factors, 0, 1), a))


@EFFECTS.register("exposure", level=FULL, note="linear-light RGB multiplied by 2**exposure, preserving HDR values")
def exposure(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = straight(buf.px)
    rgb = rgb if rc.linear else srgb_to_linear(rgb)
    rgb = rgb * np.exp2(np.clip(p.n("exposure", 0), -64, 64))
    return result(buf, premul(rgb if rc.linear else linear_to_srgb(rgb), a))


def _hue_rotate(rgb, degrees):
    # Vectorized HSV hue rotation; preserve HSV saturation and value.
    hi, lo = rgb.max(-1), rgb.min(-1)
    delta = hi-lo
    safe = np.maximum(delta, 1e-7)
    r, g, b = np.moveaxis(rgb, -1, 0)
    hue = np.where(hi == r, (g-b)/safe, np.where(hi == g, (b-r)/safe+2, (r-g)/safe+4)) / 6
    hue = (hue + degrees/360) % 1
    chroma = delta
    x = chroma*(1-abs((hue*6) % 2-1))
    z = np.zeros_like(x)
    candidates = np.stack([np.stack(c, -1) for c in ((chroma,x,z), (x,chroma,z), (z,chroma,x),
                                                    (z,x,chroma), (x,z,chroma), (chroma,z,x))])
    yy, xx = np.indices(hi.shape)
    return candidates[np.floor(hue*6).astype(int) % 6, yy, xx] + lo[..., None]


@EFFECTS.register("hue-saturation", level=FULL, note="HSV hue rotation in degrees and luma-based saturation in encoded sRGB")
def hue_saturation(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    return from_display(rc, buf, _saturate(_hue_rotate(rgb, p.n("hue", 0)), p.n("saturation", 1)) + p.n("brightness", 0), a)


@EFFECTS.register("tonemap", level=PARTIAL, note="Reinhard, ACES fit, Hable/filmic; approximate AgX and PQ-to-SDR")
def tonemap(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = straight(buf.px)
    x = np.maximum(rgb if rc.linear else srgb_to_linear(rgb), 0).astype(np.float64)
    mode = p.s("tonemapper", "aces")
    if mode == "reinhard":
        y = x/(1+x)
    elif mode in ("hable", "filmic"):
        def h(v):
            return ((v*(.15*v+.05)+.004)/(v*(.15*v+.5)+.06))-.02/.3
        y = h(x)/h(11.2)
    elif mode == "agx":
        y = x/(1+x)
        y = y*y*(3-2*y)
        y = _saturate(y, .9)
    elif mode == "pq-to-sdr":
        # ST2084 inverse EOTF, normalized to a 100-nit SDR white, then Reinhard.
        v = np.clip(x, 0, 1)**(1/(2523/32))
        v = (np.maximum(v-3424/4096, 0)/np.maximum(2413/128-2392/128*v, 1e-7))**(1/(2610/16384))*100
        y = v/(1+v)
    else:
        y = x*(2.51*x+.03)/(x*(2.43*x+.59)+.14)
    y = np.clip(y, 0, 1)
    return result(buf, premul(y if rc.linear else linear_to_srgb(y), a))


@EFFECTS.register("grayscale", level=FULL, note="Rec.709 luminance in the working space")
def grayscale(rc, e, buf, ctx, node):
    rgb, a = straight(buf.px)
    return result(buf, premul(np.repeat(luma(rgb)[..., None], 3, -1), a))


@EFFECTS.register("invert", level=FULL, note="encoded sRGB complement on selected colour/alpha/luma channel")
def invert(rc, e, buf, ctx, node):
    rgb, a = display_rgb(rc, buf.px)
    rgb, a = _channel(rgb, a, Params(rc, e, ctx).s("channel", "rgb"), lambda v: 1-v)
    return from_display(rc, buf, rgb, a)


@EFFECTS.register("sepia", level=FULL, note="standard sepia matrix in encoded sRGB, scaled by amount")
def sepia(rc, e, buf, ctx, node):
    rgb, a = display_rgb(rc, buf.px)
    mapped = rgb @ np.asarray([[.393,.769,.189], [.349,.686,.168], [.272,.534,.131]], np.float32).T
    return from_display(rc, buf, rgb+(mapped-rgb)*Params(rc, e, ctx).n("amount", 1), a)


@EFFECTS.register("posterize", level=FULL, note="quantize encoded sRGB channels to levels, including endpoints")
def posterize(rc, e, buf, ctx, node):
    rgb, a = display_rgb(rc, buf.px)
    n = max(2, round(Params(rc, e, ctx).n("levels", 8)))
    return from_display(rc, buf, np.round(rgb*(n-1))/(n-1), a)


@EFFECTS.register("threshold", level=FULL, note="binary encoded-sRGB luminance threshold; preserves alpha")
def threshold(rc, e, buf, ctx, node):
    rgb, a = display_rgb(rc, buf.px)
    v = luma(rgb) >= Params(rc, e, ctx).n("threshold", .7)
    return from_display(rc, buf, np.repeat(v[..., None], 3, -1), a)


@EFFECTS.register("tint", level=FULL, note="working luminance mapped from black to color")
def tint(rc, e, buf, ctx, node):
    rgb, a = straight(buf.px)
    c = Params(rc, e, ctx).color()
    return result(buf, premul(luma(rgb)[..., None]*c[:3], a*c[3]))


@EFFECTS.register("tritone", level=FULL, note="luminance mapped through keyColor, color, and white")
def tritone(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = straight(buf.px)
    lo, mid = p.color("keyColor", (0,0,0,1)), p.color(default=(.5,.5,.5,1))
    u = np.clip(luma(rgb)[..., None], 0, 1)*2
    mapped = np.where(u < 1, lo+(mid-lo)*u, mid+(np.ones(4)-mid)*(u-1))
    return result(buf, premul(mapped[..., :3], a*mapped[..., 3:4]))


@EFFECTS.register("gradient-map", level=PARTIAL, note="luminance through paint stops or source node centre scanline, root placement")
def gradient_map(rc, e, buf, ctx, node):
    rgb, a = straight(buf.px)
    mapped = gradient_colors(rc, e, ctx, luma(rgb), node)
    if mapped is None:
        return buf.copy()
    return result(buf, premul(mapped[..., :3], a*mapped[..., 3:4]))


@EFFECTS.register("color-overlay", level=FULL, note="straight working-space color overlay clipped to input alpha")
def color_overlay(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = straight(buf.px)
    c = p.color()
    return result(buf, premul(rgb+(c[:3]-rgb)*np.clip(c[3]*p.n("intensity", 1), 0, 1), a))


@EFFECTS.register("gradient-overlay", level=PARTIAL, note="angled paint-stop ramp over original colour, clipped to input alpha")
def gradient_overlay(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    angle = math.radians(p.n("angle", 0))
    v = x*math.cos(angle)+y*math.sin(angle)
    mapped = gradient_colors(rc, e, ctx, (v-v.min())/max(float(np.ptp(v)), 1e-7), node)
    if mapped is None:
        return buf.copy()
    rgb, a = straight(buf.px)
    w = np.clip(mapped[..., 3:4]*p.n("intensity", 1), 0, 1)
    return result(buf, premul(rgb+(mapped[..., :3]-rgb)*w, a))


@EFFECTS.register("selective-color", level=PARTIAL, note="RGB-distance keyColor selection with soft colour replacement")
def selective_color(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = straight(buf.px)
    key, color = p.color("keyColor", (1,0,0,1)), p.color()
    d = np.linalg.norm(rgb-key[:3], axis=-1)/math.sqrt(3)
    tol, soft = p.n("tolerance", .2), p.n("softness", .1)
    w = (1-smoothstep(tol, tol+soft, d))[..., None]*np.clip(p.n("amount", 1)*color[3], 0, 1)
    return result(buf, premul(rgb+(color[:3]-rgb)*w, a))
