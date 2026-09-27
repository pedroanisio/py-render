"""generator assets: procedural images (all attributes animate; noise kinds are seeded).

Conventions (the schema names the parameters but leaves their meaning open):
  paint / paint2  foreground / background. gradient: paint at the start, paint2 at the end.
                  Noise-like kinds mix paint2 -> paint by the field value (0 -> paint2, 1 -> paint).
                  Any paint (colours or url(#...) paints over the asset box) is accepted.
  scale           feature size in asset pixels: noise cell, checker square, grid spacing, stripe period,
                  Worley cell. film-grain: grain size = scale / 100 px. light-rays: angular ray width.
  angle           degrees clockwise: gradient direction (0 = left -> right), pattern / noise rotation,
                  light-ray direction (0 = rays pointing down).
  evolution       noise: third noise dimension (animate it for boiling noise); cells: feature-point
                  drift; checkerboard/grid/stripes: scroll in periods; film-grain: extra seed offset;
                  light-rays: ray drift.
  octaves         fractal-noise octaves (lacunarity 2, gain 0.5).
  contrast        field contrast about 0.5 (film-grain: grain strength).
  seed            noise seed (combined with project/@seed and the asset id through ev.seed_for).
Resolution: the noise field is computed only for the visible part of the asset, at the effective output
resolution (capped at 2 samples per asset pixel), then drawn under M with bilinear filtering.
checkerboard / grid / stripes / solid are drawn as vector patterns directly under M.
"""
from __future__ import annotations

import math

import cairo
import numpy as np

from .. import paint as paintmod
from ..registry import ASSET_SIZES, ASSETS, FULL, warn_once
from . import array_to_surface

_NOISE_KINDS = ("gradient", "noise", "fractal-noise", "cells", "film-grain", "light-rays")

_GRAD3 = np.array([[1, 1, 0], [-1, 1, 0], [1, -1, 0], [-1, -1, 0], [1, 0, 1], [-1, 0, 1], [1, 0, -1], [-1, 0, -1],
                   [0, 1, 1], [0, -1, 1], [0, 1, -1], [0, -1, -1], [1, 1, 0], [0, -1, 1], [-1, 1, 0], [0, -1, -1]],
                  np.float32)


# ---------------------------------------------------------------- noise primitives
def _perm(seed: int) -> np.ndarray:
    p = np.random.default_rng(seed & 0xFFFFFFFF).permutation(256).astype(np.int32)
    return np.concatenate([p, p])


def perlin3(x: np.ndarray, y: np.ndarray, z: float | np.ndarray, perm: np.ndarray) -> np.ndarray:
    """Improved Perlin noise in [-1, 1] (vectorised)."""
    z = np.broadcast_to(np.asarray(z, np.float64), np.shape(x))
    xf, yf, zf = np.floor(x), np.floor(y), np.floor(z)
    X, Y, Z = xf.astype(np.int64) & 255, yf.astype(np.int64) & 255, zf.astype(np.int64) & 255
    x, y, z = x - xf, y - yf, z - zf
    fade = lambda t: t * t * t * (t * (t * 6 - 15) + 10)  # noqa: E731
    u, v, w = fade(x), fade(y), fade(z)
    A, B = perm[X] + Y, perm[X + 1] + Y
    AA, AB, BA, BB = perm[A] + Z, perm[A + 1] + Z, perm[B] + Z, perm[B + 1] + Z

    def g(h, dx, dy, dz):
        gv = _GRAD3[perm[h] & 15]
        return gv[..., 0] * dx + gv[..., 1] * dy + gv[..., 2] * dz

    lerp = lambda t, a, b: a + t * (b - a)  # noqa: E731
    x1 = lerp(u, g(AA, x, y, z), g(BA, x - 1, y, z))
    x2 = lerp(u, g(AB, x, y - 1, z), g(BB, x - 1, y - 1, z))
    y1 = lerp(v, x1, x2)
    x3 = lerp(u, g(AA + 1, x, y, z - 1), g(BA + 1, x - 1, y, z - 1))
    x4 = lerp(u, g(AB + 1, x, y - 1, z - 1), g(BB + 1, x - 1, y - 1, z - 1))
    y2 = lerp(v, x3, x4)
    return lerp(w, y1, y2)


def _hash01(i: np.ndarray, j: np.ndarray, seed: int, k: int = 0) -> np.ndarray:
    """Deterministic integer-lattice hash in [0, 1)."""
    h = (i.astype(np.int64) * 73856093) ^ (j.astype(np.int64) * 19349663) ^ ((seed + k * 83492791) & 0x7FFFFFFF)
    h = h.astype(np.uint64)
    h ^= h >> np.uint64(13)
    h *= np.uint64(0x5BD1E995)
    h ^= h >> np.uint64(15)
    h *= np.uint64(0x27D4EB2D)
    h ^= h >> np.uint64(16)
    return (h & np.uint64(0xFFFFFF)).astype(np.float64) / float(0x1000000)


def fbm(x, y, z, perm, octaves: int) -> np.ndarray:
    total, amp, norm = np.zeros(np.shape(x)), 1.0, 0.0
    for o in range(max(1, octaves)):
        f = 2.0 ** o
        total += amp * perlin3(x * f + 17.3 * o, y * f + 5.1 * o, z * (1 + 0.5 * o), perm)
        norm += amp
        amp *= 0.5
    return total / norm


def worley(x, y, evo: float, seed: int) -> np.ndarray:
    """F1 distance (in cell units, ~0..1) to jittered feature points drifting with evo."""
    ci, cj = np.floor(x), np.floor(y)
    best = np.full(np.shape(x), 9.0)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            ii, jj = ci + di, cj + dj
            ph1, ph2 = _hash01(ii, jj, seed, 1), _hash01(ii, jj, seed, 2)
            px = ii + 0.5 + 0.4 * np.sin(2 * math.pi * (ph1 + evo * (0.5 + 0.5 * ph2)))
            py = jj + 0.5 + 0.4 * np.cos(2 * math.pi * (ph2 + evo * (0.5 + 0.5 * ph1)))
            best = np.minimum(best, np.hypot(x - px, y - py))
    return best


# ---------------------------------------------------------------- fields
def field(kind: str, X: np.ndarray, Y: np.ndarray, w: float, h: float, p: dict, seed: int, frame: int) -> np.ndarray:
    """Scalar field in [0, 1] at asset-space sample positions X, Y."""
    scl, ang, evo, con = p["scale"], math.radians(p["angle"]), p["evolution"], p["contrast"]
    ca, sa = math.cos(ang), math.sin(ang)
    cx, cy = w / 2, h / 2
    # rotate sample positions about the centre (pattern rotates clockwise by angle)
    RX = (X - cx) * ca + (Y - cy) * sa
    RY = -(X - cx) * sa + (Y - cy) * ca
    if kind == "gradient":
        half = abs(w / 2 * ca) + abs(h / 2 * sa)          # CSS-style gradient line length / 2
        return np.clip(RX / max(half * 2, 1e-9) + 0.5, 0, 1)
    if kind in ("noise", "fractal-noise"):
        perm = _perm(seed)
        oct_ = 1 if kind == "noise" else int(p["octaves"])
        n = fbm(RX / scl, RY / scl, evo, perm, oct_)
        v = 0.5 + n * (0.65 if oct_ == 1 else 0.85)
        return np.clip(0.5 + (v - 0.5) * con, 0, 1)
    if kind == "cells":
        d = worley(RX / scl, RY / scl, evo, seed)
        return np.clip(0.5 + (np.clip(d, 0, 1) - 0.5) * con, 0, 1)
    if kind == "film-grain":
        g = max(0.25, scl / 100.0)
        fs = seed + frame * 7919 + int(round(evo * 1000)) * 104729
        i, j = np.floor(X / g), np.floor(Y / g)
        u = (_hash01(i, j, fs, 1) + _hash01(i, j, fs, 2) + _hash01(i, j, fs, 3)) / 3.0   # ~gaussian
        return np.clip(0.5 + (u - 0.5) * 2.0 * con, 0, 1)
    if kind == "light-rays":
        diag = math.hypot(w, h)
        # direction the rays travel (0 deg = down), source outside the box against that direction
        dx, dy = -math.sin(ang), math.cos(ang)
        ox, oy = cx - dx * diag * 0.75, cy - dy * diag * 0.75
        vx, vy = X - ox, Y - oy
        dist = np.hypot(vx, vy) + 1e-9
        cos_t = (vx * dx + vy * dy) / dist
        theta = np.arctan2(vx * dy - vy * dx, vx * dx + vy * dy)
        freq = diag * 0.75 / max(scl, 1e-6)            # rays of ~scale px width at the box centre
        perm = _perm(seed)
        r = fbm(theta * freq, np.full_like(theta, 3.7), evo, perm, 3)
        rays = np.clip(0.5 + r * 1.6, 0, 1) ** 2
        cone = np.clip(cos_t, 0, 1) ** 6
        fall = np.clip(1.2 - (dist - diag * 0.25) / (diag * 1.3), 0, 1)
        return np.clip(rays * cone * fall * (0.6 + 0.8 * con), 0, 1)
    return np.zeros_like(X)


# ---------------------------------------------------------------- drawing helpers
def _params(rc, asset, ctx) -> dict:
    ev = rc.ev
    return {"scale": max(1e-6, ev.num(asset, "scale", ctx, 100.0)), "angle": ev.num(asset, "angle", ctx, 0.0),
            "evolution": ev.num(asset, "evolution", ctx, 0.0), "contrast": ev.num(asset, "contrast", ctx, 1.0),
            "octaves": max(1, int(ev.num(asset, "octaves", ctx, 4.0)))}


def _seed(rc, asset) -> int:
    return rc.ev.seed_for(asset, "generator")


def _visible_region(rc, M, w, h):
    """Asset-space rectangle (x0, y0, x1, y1) that can land inside the frame (plus a margin)."""
    try:
        Mi = np.linalg.inv(M)
    except np.linalg.LinAlgError:
        return None
    m = 4
    pts = np.array([[-m, -m, 1], [rc.width + m, -m, 1], [rc.width + m, rc.height + m, 1], [-m, rc.height + m, 1]], float) @ Mi.T
    x0, y0 = max(0.0, pts[:, 0].min()), max(0.0, pts[:, 1].min())
    x1, y1 = min(w, pts[:, 0].max()), min(h, pts[:, 1].max())
    if x1 <= x0 or y1 <= y0:
        return None
    return math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)


def _paint_layer(rc, spec: str, rw: int, rh: int, region, w, h, ctx, asset) -> np.ndarray:
    """Premultiplied 8-bit-range float BGRA of paint over the sampled region."""
    x0, y0, x1, y1 = region
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, rw, rh)
    cr = cairo.Context(surf)
    cr.scale(rw / (x1 - x0), rh / (y1 - y0))
    cr.translate(-x0, -y0)
    cr.rectangle(x0, y0, x1 - x0, y1 - y0)
    if paintmod.set_source(rc, cr, spec, w, h, ctx, asset):
        cr.fill()
    surf.flush()
    a = np.frombuffer(surf.get_data(), np.uint8).reshape(rh, surf.get_stride() // 4, 4)[:, :rw]
    return a.astype(np.float32)


def _max_spacing(kind: str, p: dict, w: float, h: float) -> float:
    """Largest sample spacing (asset px) that still resolves the field."""
    scl = p["scale"]
    if kind == "gradient":
        return max(1.0, max(w, h) / 128)
    if kind == "noise":
        return max(0.5, scl / 12)
    if kind == "fractal-noise":
        return max(0.5, scl / 2 ** (p["octaves"] - 1) / 6)
    if kind == "cells":
        return max(0.5, scl / 12)
    if kind == "light-rays":
        return max(0.5, scl / 10)
    return 0.5                                  # film-grain: per-pixel detail


def _draw_raster(rc, M, bgra: np.ndarray, region, clip):
    x0, y0, x1, y1 = region
    rh, rw = bgra.shape[:2]
    c = rc.canvas_for(M, x1, y1, 2)
    if c is None:
        return None
    cr = c.cr
    cr.rectangle(x0, y0, x1 - x0, y1 - y0)
    cr.clip()
    if clip:
        cr.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
        cr.clip()
    surf = array_to_surface(bgra)
    cr.translate(x0, y0)
    cr.scale((x1 - x0) / rw, (y1 - y0) / rh)
    cr.set_source_surface(surf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.get_source().set_extend(cairo.EXTEND_PAD)
    cr.paint()
    return c.to_buf(rc.linear)


def _tile_pattern(kind: str, n: int, p: dict):
    """A8 mask tile (one period) for checkerboard / grid / stripes, n pixels per cell."""
    if kind == "checkerboard":
        tw, th = 2 * n, 2 * n
        m = np.zeros((th, tw), np.uint8)
        m[:n, :n] = 255
        m[n:, n:] = 255
    elif kind == "grid":
        tw = th = n
        lw = max(1, int(round(n * 0.04)))
        m = np.zeros((th, tw), np.uint8)
        m[:lw, :] = 255
        m[:, :lw] = 255
    else:  # stripes (vertical at angle 0), 50 % duty
        tw, th = 2 * n, 1
        m = np.zeros((th, tw), np.uint8)
        m[:, :n] = 255
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_A8, tw)
    buf = np.zeros((th, stride), np.uint8)
    buf[:, :tw] = m
    surf = cairo.ImageSurface.create_for_data(memoryview(buf).cast("B"), cairo.FORMAT_A8, tw, th, stride)
    pat = cairo.SurfacePattern(surf)
    pat.set_extend(cairo.EXTEND_REPEAT)
    pat.set_filter(cairo.FILTER_GOOD)
    return pat, buf         # the caller keeps buf alive while the pattern is used


# ---------------------------------------------------------------- handler
@ASSETS.register("generator", level=FULL,
                 note="parameter semantics pinned in assets/generator.py (scale = feature size px, angle, evolution)")
def render_generator(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    kind = asset.get("kind")
    w, h = float(asset.get("width")), float(asset.get("height"))
    ev = rc.ev
    p1, p2 = ev.str(asset, "paint", ctx, "#FFFFFFFF"), ev.str(asset, "paint2", ctx, "#000000FF")
    p = _params(rc, asset, ctx)
    eff = math.sqrt(abs(np.linalg.det(M[:2, :2])))      # output px per asset px
    if kind in ("solid", "checkerboard", "grid", "stripes"):
        c = rc.canvas_for(M, w, h, 1)
        if c is None:
            return None
        cr = c.cr
        cr.rectangle(0, 0, w, h)
        cr.clip()
        if clip:
            cr.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
            cr.clip()
        if kind == "solid":
            if paintmod.set_source(rc, cr, p1, w, h, ctx, asset):
                cr.paint()
            return c.to_buf(rc.linear)
        if paintmod.set_source(rc, cr, p2, w, h, ctx, asset):
            cr.paint()
        cell = p["scale"]
        n = int(min(256, max(2, round(cell * eff))))
        pat, _mem = _tile_pattern(kind, n, p)
        # pattern space = tile pixels; user (asset) space -> rotate about centre, scroll by evolution
        pm = cairo.Matrix()
        pm.translate(w / 2, h / 2)
        pm.rotate(math.radians(p["angle"]))
        pm.translate(p["evolution"] * cell * (2 if kind != "grid" else 1), 0)
        pm.scale(cell / n, cell / n)
        pm.invert()
        pat.set_matrix(pm)
        if paintmod.set_source(rc, cr, p1, w, h, ctx, asset):
            cr.mask(pat)
        return c.to_buf(rc.linear)
    if kind not in _NOISE_KINDS:
        warn_once("generator", kind)
        return None
    region = _visible_region(rc, M, w, h)
    if region is None:
        return None
    x0, y0, x1, y1 = region
    # Sample no finer than the output (max 2 per asset px) and no finer than the field needs: smooth fields
    # are sampled a few times per feature and upscaled bilinearly (a 600 px fractal needs ~200x120 samples).
    k = min(2.0, max(eff, 1e-3), 1.0 / _max_spacing(kind, p, w, h))
    rw, rh = max(1, int(math.ceil((x1 - x0) * k))), max(1, int(math.ceil((y1 - y0) * k)))
    frame = int(math.floor(ctx.t * float(rc.doc.fps) + 1e-6))
    key = ("gen", asset, kind, tuple(sorted(p.items())), p1, p2, region, rw, rh,
           frame if kind == "film-grain" else None)
    bgra = rc.cache.get(key)
    if bgra is None:
        X = x0 + (np.arange(rw) + 0.5) * (x1 - x0) / rw
        Y = y0 + (np.arange(rh) + 0.5) * (y1 - y0) / rh
        XX, YY = np.meshgrid(X, Y)
        v = field(kind, XX, YY, w, h, p, _seed(rc, asset), frame).astype(np.float32)
        if kind == "gradient":
            v = 1.0 - v                                      # paint at the start, paint2 at the end
        A = _paint_layer(rc, p1, rw, rh, region, w, h, ctx, asset)
        B = _paint_layer(rc, p2, rw, rh, region, w, h, ctx, asset)
        out = B + (A - B) * v[..., None]
        bgra = np.clip(out + 0.5, 0, 255).astype(np.uint8)
        # keep only the latest few noise rasters per asset (animated fields change every frame)
        old = [kk for kk in rc.cache if isinstance(kk, tuple) and kk[:2] == ("gen", asset)]
        for kk in old[:-2]:
            del rc.cache[kk]
        rc.cache[key] = bgra
    return _draw_raster(rc, M, bgra, region, clip)


@ASSET_SIZES.register("generator")
def generator_size(rc, asset, ctx):
    return float(asset.get("width")), float(asset.get("height"))
