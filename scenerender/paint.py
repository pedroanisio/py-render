"""Paint -> cairo source: colours, url(#id) gradients (linear, radial, conic, mesh) and patterns.

Cairo interpolates gradient stops in sRGB-encoded space. For interpolationSpace
linear/oklab/oklch and for stop midpoints, each stop interval is subdivided and
the intermediate colours are computed in the requested space, so the result
matches the requested interpolation to within a few 8-bit levels.
"""
from __future__ import annotations

import math

import cairo
import numpy as np

from .document import ln
from .registry import FEATURES, FULL, PARTIAL, warn_once
from .values import linear_to_srgb, paint_ref, parse_color, srgb_to_linear

for _n in ("linearGradient", "radialGradient", "conicGradient", "pattern"):
    FEATURES.declare(f"paint:{_n}", FULL)
FEATURES.declare("paint:meshGradient", FULL, "bicubic Catmull-Rom surface for positions and colours in @interpolationSpace")

_SUBDIV = 12


# ---------------------------------------------------------------- colour spaces
def _to_oklab(c):
    r, g, b = (srgb_to_linear(v) for v in c[:3])
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def _from_oklab(L, a, b):
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    return tuple(min(1.0, max(0.0, linear_to_srgb(max(0.0, v)))) for v in rgb)


def mix_color(c0, c1, u: float, space: str):
    a = c0[3] + (c1[3] - c0[3]) * u
    if space == "srgb":
        return tuple(x + (y - x) * u for x, y in zip(c0[:3], c1[:3])) + (a,)
    if space == "linear":
        rgb = [linear_to_srgb(srgb_to_linear(x) + (srgb_to_linear(y) - srgb_to_linear(x)) * u) for x, y in zip(c0[:3], c1[:3])]
        return tuple(rgb) + (a,)
    L0, A0, B0 = _to_oklab(c0)
    L1, A1, B1 = _to_oklab(c1)
    if space == "oklch":
        C0, H0 = math.hypot(A0, B0), math.atan2(B0, A0)
        C1, H1 = math.hypot(A1, B1), math.atan2(B1, A1)
        dh = (H1 - H0 + math.pi) % (2 * math.pi) - math.pi
        C, H = C0 + (C1 - C0) * u, H0 + dh * u
        return _from_oklab(L0 + (L1 - L0) * u, C * math.cos(H), C * math.sin(H)) + (a,)
    return _from_oklab(L0 + (L1 - L0) * u, A0 + (A1 - A0) * u, B0 + (B1 - B0) * u) + (a,)


# ---------------------------------------------------------------- sources
def set_source(rc, cr: cairo.Context, paint: str | None, w: float, h: float, ctx, node=None) -> bool:
    """Set cr's source to paint over the node box (0,0)-(w,h) in the current user space.
    Returns False when the paint is fully transparent (nothing to draw)."""
    if paint is None:
        return False
    ref = paint_ref(paint)
    if ref is None:
        c = parse_color(paint, rc.doc.tokens)
        if c[3] <= 0:
            return False
        cr.set_source_rgba(*c)
        return True
    el = rc.doc.ids.get(ref)
    if el is None:
        warn_once("paint", ref, "paint reference not found")
        return False
    kind = ln(el)
    pat = (_conic(rc, el, w, h, ctx, cr.clip_extents()) if kind == "conicGradient"
           else _PAINTERS.get(kind, _unknown)(rc, el, w, h, ctx))
    if pat is None:
        return False
    if kind in ("linearGradient", "radialGradient", "conicGradient"):
        if rc.ev.bool(el, "dither", ctx, True):
            pat = _dither_pattern(cr, pat)
        elif hasattr(pat, "set_dither"):
            pat.set_dither(cairo.DITHER_NONE)
    cr.set_source(pat)
    return True


def _dither_pattern(cr, pattern):
    """Quantize a floating gradient with an ordered, zero-mean pixel threshold.

    Cairo's pattern dither setting is backend-dependent (and has no effect on
    several ARGB32 paths). Quantizing explicitly makes the schema switch work
    consistently and keeps rendering independent of thread order/random state.
    The pattern is sampled in device space, so transforms retain pixel resolution.
    """
    matrix = cr.get_matrix()
    x0, y0, x1, y1 = cr.clip_extents()
    corners = [matrix.transform_point(x, y) for x in (x0, x1) for y in (y0, y1)]
    left = math.floor(min(x for x, _ in corners))
    top = math.floor(min(y for _, y in corners))
    width = max(1, math.ceil(max(x for x, _ in corners)) - left)
    height = max(1, math.ceil(max(y for _, y in corners)) - top)
    transform = cairo.Matrix(*matrix)
    transform.x0 -= left
    transform.y0 -= top
    key = _gradient_key(pattern, transform, left, top, width, height)
    cached = _DITHERED.get(key) if key is not None else None
    if cached is None:
        cached = _dither_surface(pattern, transform, left, top, width, height)
        if key is not None:
            if len(_DITHERED) >= 16:
                _DITHERED.pop(next(iter(_DITHERED)))
            _DITHERED[key] = cached
    out = cairo.SurfacePattern(cached)
    out.set_matrix(transform)
    out.set_filter(cairo.FILTER_NEAREST)
    return out


# Dithered gradient rasters by exact definition: motion-blur samples and still frames repaint the same ones.
_DITHERED: dict = {}


def _bayer() -> np.ndarray:
    # Bayer recursion produces every rank 0..63 exactly once in an 8x8 tile.
    ranks = np.array([[0.]], np.float32)
    for _ in range(3):
        ranks = np.block([[4*ranks, 4*ranks+2], [4*ranks+3, 4*ranks+1]])
    return (ranks + .5) / 64 - .5


_BAYER = _bayer()


def _gradient_key(pattern, transform, left, top, width, height):
    """Everything that determines a linear/radial gradient's device raster, or None (not cached)."""
    if isinstance(pattern, cairo.LinearGradient):
        geometry = ("linear", pattern.get_linear_points())
    elif isinstance(pattern, cairo.RadialGradient):
        geometry = ("radial", pattern.get_radial_circles())
    else:
        return None
    return (geometry, tuple(pattern.get_color_stops_rgba()), tuple(pattern.get_matrix()), pattern.get_extend(),
            pattern.get_filter(), tuple(transform), left, top, width, height)


def _dither_surface(pattern, transform, left, top, width, height):
    from .assets import array_to_surface
    surf = cairo.ImageSurface(cairo.FORMAT_RGBA128F, width, height)
    paint = cairo.Context(surf)
    paint.set_matrix(transform)
    paint.set_source(pattern)
    paint.paint()
    surf.flush()
    pixels = np.frombuffer(surf.get_data(), np.float32).reshape(height, surf.get_stride() // 4)[:, :4*width].reshape(height, width, 4)
    from . import kernels
    if kernels.enabled():
        bgra = kernels.dither(pixels, _BAYER, top, left)
        return cairo.ImageSurface.create_for_data(memoryview(bgra).cast("B"), cairo.FORMAT_ARGB32, width, height,
                                                  width * 4)
    # The threshold tile repeats every 8 device pixels: roll it to the surface origin and tile it.
    tile = np.roll(_BAYER, (-(top % 8), -(left % 8)), axis=(0, 1))
    noise = np.tile(tile, (-(-height // 8), -(-width // 8)))[:height, :width, None]
    alpha = np.clip(pixels[..., 3:4], 0, 1)
    a8 = np.floor(alpha * 255 + .5)
    rgb = pixels[..., :3] * 255
    rgb += .5
    rgb += noise * alpha
    np.floor(rgb, out=rgb)
    np.clip(rgb, 0, a8, out=rgb)
    bgra = np.empty((height, width, 4), np.uint8)
    bgra[..., :3] = rgb[..., ::-1]
    bgra[..., 3:] = a8
    return array_to_surface(bgra)


def _unknown(rc, el, w, h, ctx):
    warn_once("paint", ln(el))
    return None


def _stops(rc, el, ctx):
    ev = rc.ev
    out = []
    for s in el:
        if ln(s) != "stop":
            continue
        c = ev.color(s, "color", ctx, (0, 0, 0, 1))
        op = ev.num(s, "opacity", ctx, 1.0)
        out.append((min(1.0, max(0.0, ev.num(s, "offset", ctx, 0.0))), (c[0], c[1], c[2], c[3] * op),
                    ev.num(s, "midpoint", ctx, 0.5)))
    out.sort(key=lambda s: s[0])
    return out or [(0.0, (0, 0, 0, 0), 0.5), (1.0, (0, 0, 0, 0), 0.5)]


def _add_stops(rc, pat, el, ctx):
    space = rc.ev.str(el, "interpolationSpace", ctx, "linear")
    stops = _stops(rc, el, ctx)
    pat.add_color_stop_rgba(stops[0][0], *stops[0][1])
    for (o0, c0, mid), (o1, c1, _) in zip(stops, stops[1:]):
        simple = space == "srgb" and abs(mid - 0.5) < 1e-6
        n = 1 if simple else _SUBDIV
        for k in range(1, n + 1):
            u = k / n
            # Midpoint: the colour halfway between the stops sits at `mid` of the interval.
            e = u if abs(mid - 0.5) < 1e-6 else u ** (math.log(0.5) / math.log(max(1e-4, min(0.9999, mid))))
            pat.add_color_stop_rgba(o0 + (o1 - o0) * u, *mix_color(c0, c1, e, space))


def _spread(rc, pat, el, ctx):
    pat.set_extend({"pad": cairo.EXTEND_PAD, "reflect": cairo.EXTEND_REFLECT,
                    "repeat": cairo.EXTEND_REPEAT}.get(rc.ev.str(el, "spread", ctx, "pad"), cairo.EXTEND_PAD))


def _units_matrix(rc, el, w, h, ctx):
    """Matrix mapping gradient space to user space (object units = fractions of the box)."""
    rot = rc.ev.num(el, "rotation", ctx, 0.0)
    base = cairo.Matrix(xx=w, yy=h) if rc.ev.str(el, "units", ctx, "object") == "object" else cairo.Matrix()
    if rot:
        # Rotate about the centre of the painted box.
        cx, cy = (w / 2, h / 2)
        r = cairo.Matrix(x0=-cx, y0=-cy).multiply(cairo.Matrix.init_rotate(math.radians(rot))).multiply(cairo.Matrix(x0=cx, y0=cy))
        return base.multiply(r)
    return base


def _with_matrix(pat, gm: cairo.Matrix):
    inv = cairo.Matrix(*gm)
    inv.invert()
    pat.set_matrix(inv)
    return pat


def _linear(rc, el, w, h, ctx):
    ev = rc.ev
    pat = cairo.LinearGradient(ev.num(el, "x1", ctx, 0), ev.num(el, "y1", ctx, 0),
                               ev.num(el, "x2", ctx, 1), ev.num(el, "y2", ctx, 0))
    _add_stops(rc, pat, el, ctx)
    _spread(rc, pat, el, ctx)
    return _with_matrix(pat, _units_matrix(rc, el, w, h, ctx))


def _radial(rc, el, w, h, ctx):
    ev = rc.ev
    cx, cy, r = ev.num(el, "cx", ctx, 0.5), ev.num(el, "cy", ctx, 0.5), ev.num(el, "r", ctx, 0.5)
    fx = ev.num(el, "fx", ctx, cx)
    fy = ev.num(el, "fy", ctx, cy)
    aspect = ev.num(el, "aspect", ctx, 1.0)
    pat = cairo.RadialGradient(fx, fy, ev.num(el, "fr", ctx, 0.0), cx, cy, r)
    _add_stops(rc, pat, el, ctx)
    _spread(rc, pat, el, ctx)
    gm = _units_matrix(rc, el, w, h, ctx)
    if aspect != 1:
        gm = cairo.Matrix(x0=-cx, y0=-cy).multiply(cairo.Matrix(xx=aspect)).multiply(cairo.Matrix(x0=cx, y0=cy)).multiply(gm)
    return _with_matrix(pat, gm)


def _conic(rc, el, w, h, ctx, extent=None):
    ev = rc.ev
    cx, cy = ev.num(el, "cx", ctx, 0.5), ev.num(el, "cy", ctx, 0.5)
    start = math.radians(ev.num(el, "angle", ctx, 0.0) - 90)
    space = ev.str(el, "interpolationSpace", ctx, "linear")
    stops = _stops(rc, el, ctx)
    pat = cairo.MeshPattern()
    n = 96
    gm = _units_matrix(rc, el, w, h, ctx)
    inv = cairo.Matrix(*gm)
    inv.invert()
    x0, y0, x1, y1 = extent or (0, 0, w, h)
    corners = [inv.transform_point(x, y) for x in (x0, x1) for y in (y0, y1)]
    R = 1.01 * max(math.hypot(x - cx, y - cy) for x, y in corners) + 1
    def color_at(u):
        if u <= stops[0][0]:
            return stops[0][1]
        for (o0, c0, midpoint), (o1, c1, _) in zip(stops, stops[1:]):
            if o0 <= u <= o1:
                fraction = (u - o0) / max(1e-9, o1 - o0)
                if abs(midpoint - .5) > 1e-6:
                    fraction **= math.log(.5) / math.log(max(1e-4, min(.9999, midpoint)))
                return mix_color(c0, c1, fraction, space)
        return stops[-1][1]
    for i in range(n):
        a0, a1 = start + 2 * math.pi * i / n, start + 2 * math.pi * (i + 1) / n
        c0, c1 = color_at(i / n), color_at((i + 1) / n)
        pat.begin_patch()
        pat.move_to(cx, cy)
        pat.line_to(cx + R * math.cos(a0), cy + R * math.sin(a0))
        pat.line_to(cx + R * math.cos(a1), cy + R * math.sin(a1))
        pat.line_to(cx, cy)
        for k, c in enumerate((c0, c0, c1, c1)):
            pat.set_corner_color_rgba(k, *c)
        pat.end_patch()
    return _with_matrix(pat, gm)


def _mesh(rc, el, w, h, ctx):
    """Freeform mesh gradient: a bicubic (Catmull-Rom tensor-product) surface through the grid points,
    for both positions and colours, colours interpolated in @interpolationSpace. Each grid cell is drawn
    as SUB x SUB cairo patches, so colour is C1-smooth across cell edges. A missing grid point takes
    its default position (the regular grid) and the colour of the nearest defined point."""
    ev = rc.ev
    rows, cols = int(ev.num(el, "rows", ctx, 2)), int(ev.num(el, "cols", ctx, 2))
    space = ev.str(el, "interpolationSpace", ctx, "oklab")
    pts = np.zeros((rows, cols, 2), np.float64)
    col = np.zeros((rows, cols, 4), np.float64)
    known = np.zeros((rows, cols), bool)
    for r in range(rows):
        for c in range(cols):
            pts[r, c] = (c / (cols - 1), r / (rows - 1))
    for p in el:
        if ln(p) != "point":
            continue
        r, c = int(ev.num(p, "row", ctx)), int(ev.num(p, "col", ctx))
        if not (0 <= r < rows and 0 <= c < cols):
            continue
        if p.get("x") is not None or _animated(p, "x"):
            pts[r, c, 0] = ev.num(p, "x", ctx, pts[r, c, 0])
        if p.get("y") is not None or _animated(p, "y"):
            pts[r, c, 1] = ev.num(p, "y", ctx, pts[r, c, 1])
        col[r, c] = _to_space(ev.color(p, "color", ctx, (0, 0, 0, 1)), space)
        known[r, c] = True
    if not known.any():
        return None
    kr, kc = np.nonzero(known)
    for r in range(rows):
        for c in range(cols):
            if not known[r, c]:
                k = int(np.argmin((kr - r) ** 2 + (kc - c) ** 2))
                col[r, c] = col[kr[k], kc[k]]
    SUB = 8
    fr = np.linspace(0, rows - 1, (rows - 1) * SUB + 1)
    fc = np.linspace(0, cols - 1, (cols - 1) * SUB + 1)
    P = _catmull_grid(pts, fr, fc)
    C = _catmull_grid(col, fr, fc)
    pat = cairo.MeshPattern()
    for i in range(len(fr) - 1):
        for j in range(len(fc) - 1):
            quad = ((i, j), (i, j + 1), (i + 1, j + 1), (i + 1, j))
            pat.begin_patch()
            pat.move_to(*P[quad[0]])
            for q in quad[1:]:
                pat.line_to(*P[q])
            for k, q in enumerate(quad):
                pat.set_corner_color_rgba(k, *_from_space(C[q], space))
            pat.end_patch()
    return _with_matrix(pat, cairo.Matrix(xx=w, yy=h))


def _catmull_grid(g: np.ndarray, fr: np.ndarray, fc: np.ndarray) -> np.ndarray:
    """Evaluate a Catmull-Rom tensor-product surface through grid g (rows, cols, k) at fractional
    row/column coordinates (edge rows/cols are repeated as end tangents)."""
    def weights(t: np.ndarray, n: int):
        i = np.clip(np.floor(t).astype(int), 0, n - 2)
        u = t - i
        w = np.stack([(-u ** 3 + 2 * u ** 2 - u) / 2, (3 * u ** 3 - 5 * u ** 2 + 2) / 2,
                      (-3 * u ** 3 + 4 * u ** 2 + u) / 2, (u ** 3 - u ** 2) / 2], -1)
        idx = np.clip(i[:, None] + np.arange(-1, 3), 0, n - 1)
        return idx, w
    ri, rw = weights(fr, g.shape[0])
    ci, cw = weights(fc, g.shape[1])
    rows = np.einsum("ak,akcd->acd", rw, g[ri])                 # interpolate along rows
    return np.einsum("bk,abkd->abd", cw, rows[:, ci])            # then along columns


def _to_space(c, space: str) -> np.ndarray:
    if space == "oklab":
        return np.array([*_to_oklab(c), c[3]])
    if space == "linear":
        return np.array([srgb_to_linear(v) for v in c[:3]] + [c[3]])
    return np.array(c, np.float64)


def _from_space(v: np.ndarray, space: str):
    a = float(min(1.0, max(0.0, v[3])))
    if space == "oklab":
        return (*_from_oklab(*v[:3]), a)
    if space == "linear":
        return tuple(min(1.0, max(0.0, linear_to_srgb(max(0.0, float(x))))) for x in v[:3]) + (a,)
    return tuple(min(1.0, max(0.0, float(x))) for x in v[:3]) + (a,)


def _animated(el, prop):
    return any(ln(a) in ("animate", "expression", "link") and a.get("property") == prop for a in el)


def _pattern(rc, el, w, h, ctx):
    from .assets import surface_for_asset
    ev = rc.ev
    asset = rc.doc.ids.get(ev.str(el, "asset", ctx))
    if asset is None:
        warn_once("paint", el.get("id"), "pattern asset not found")
        return None
    surf, aw, ah = surface_for_asset(rc, asset, ctx)
    if surf is None:
        return None
    tw, th = ev.num(el, "tileWidth", ctx, aw), ev.num(el, "tileHeight", ctx, ah)
    pat = cairo.SurfacePattern(surf)
    pat.set_extend(cairo.EXTEND_REPEAT)
    pat.set_filter(cairo.FILTER_GOOD)
    s = ev.num(el, "scale", ctx, 1.0)
    gm = cairo.Matrix(xx=tw / surf.get_width() * s, yy=th / surf.get_height() * s)
    gm = gm.multiply(cairo.Matrix.init_rotate(math.radians(ev.num(el, "rotation", ctx, 0.0))))
    gm = gm.multiply(cairo.Matrix(x0=ev.num(el, "offsetX", ctx, 0.0), y0=ev.num(el, "offsetY", ctx, 0.0)))
    return _with_matrix(pat, gm)


_PAINTERS = {"linearGradient": _linear, "radialGradient": _radial, "conicGradient": _conic,
             "meshGradient": _mesh, "pattern": _pattern}


def paint_color_estimate(rc, paint: str | None, ctx) -> tuple[float, float, float, float]:
    """A representative colour for a paint (used where a flat colour is needed, e.g. particle tint)."""
    if paint is None:
        return (0, 0, 0, 0)
    ref = paint_ref(paint)
    if ref is None:
        return parse_color(paint, rc.doc.tokens)
    el = rc.doc.ids.get(ref)
    if el is None:
        return (0, 0, 0, 0)
    stops = _stops(rc, el, ctx) if ln(el) != "pattern" else [(0, (0.5, 0.5, 0.5, 1), 0.5)]
    return tuple(float(np.mean([s[1][i] for s in stops])) for i in range(4))
