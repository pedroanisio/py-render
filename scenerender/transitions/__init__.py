"""Transition handlers: fn(rc, tr_el, a, b, p, ctx) -> Buf.

a is the outgoing node rendered to a full frame (None for an in-transition),
b the incoming one (None for an out-transition); p is the eased progress 0..1.
For single-node transitions the missing side is transparent (D19: a fade in
or out).

Conventions (D19, CONVENTIONS 5.17; the types D19 does not define follow the
same geometry):
  * @direction is the direction of travel: "left" moves content leftwards, so
    the incoming picture enters from the right and a wipe edge travels right
    to left. "angle" uses @angle in degrees, clockwise from +x (screen right).
    3D-style transitions (cube, flip, carousel, ...) snap an angle to the
    nearest axis.
  * Masked types have a coordinate s in [0, 1] and show b where s < e - w, a
    beyond e, and blend across [e - w, e] (smoothstep), with e = p (1 + w)
    and w = @softness, the edge width as a fraction of the travel.
  * Optional <param> children tune individual types (count, cx, cy, radius,
    amount); they are listed with each handler. D19 does not define them, and
    their defaults are its geometry.

This package holds shared helpers; the handlers live in the sibling modules,
which registry.load_plugins() imports.
"""
from __future__ import annotations

import math

import numpy as np

from ..raster import Buf, color_to_working
from ..registry import FULL, TRANSITIONS


# ---------------------------------------------------------------- frames
def frame(rc) -> Buf:
    return Buf.empty(0, 0, rc.width, rc.height)


def sides(rc, a, b):
    return (a if a is not None else frame(rc)), (b if b is not None else frame(rc))


def arrays(rc, a, b) -> tuple[np.ndarray, np.ndarray]:
    """Full-frame premultiplied pixel arrays of both sides (transparent when missing)."""
    A, B = sides(rc, a, b)
    return _fit(rc, A), _fit(rc, B)


def _fit(rc, buf: Buf) -> np.ndarray:
    if buf.rect == (0, 0, rc.width, rc.height):
        return buf.px
    return buf.region((0, 0, rc.width, rc.height))


def matte_rgba(rc, tr, ctx) -> np.ndarray | None:
    """Premultiplied full-frame matte, evaluated on its own node or asset clock."""
    from ..compositor import scale as scale_m
    from ..document import ln
    from ..registry import ASSETS
    node, target_ctx = rc.ev.reference(rc.ev.str(tr, "matte", ctx, ""), tr, ctx)
    if node is None:
        return None
    parent = node.getparent()
    if parent is not None and ln(parent) == "assets":
        fn = ASSETS.get(ln(node))
        aw, ah = rc.asset_size(node, target_ctx) if fn else (0, 0)
        buf = fn(rc, node, scale_m(rc.width / aw, rc.height / ah), target_ctx,
                 src_t=target_ctx.t) if aw and ah else None
        return None if buf is None else buf.region(rc.frame_rect)
    o = rc.render_reference(node, target_ctx, force=True)
    return np.zeros((rc.height, rc.width, 4), np.float32) if o is None else o.buf.region(rc.frame_rect) * o.opacity


def out(px: np.ndarray) -> Buf:
    return Buf(np.ascontiguousarray(px, dtype=np.float32), 0, 0)


def mix(A: np.ndarray, B: np.ndarray, m) -> np.ndarray:
    """A where m=0, B where m=1; m is a scalar or an (h, w) coverage."""
    if np.isscalar(m):
        return A * (1 - m) + B * m
    m = m[..., None]
    return A + (B - A) * m


def over(front: np.ndarray, back: np.ndarray) -> np.ndarray:
    return front + back * (1 - front[..., 3:4])


def smoothstep(e0: float, e1: float, x):
    t = np.clip((np.asarray(x, np.float32) - e0) / max(1e-9, e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def sstep(e0: float, e1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - e0) / max(1e-9, e1 - e0)))
    return t * t * (3 - 2 * t)


def edge(v, p: float, soft: float):
    """Share of b at a pixel whose coordinate is v (D19): 1 - smoothstep(e - w, e, v) with
    e = p (1 + w) and w = soft, so p = 0 is all a and p = 1 all b."""
    soft = max(soft, 1e-4)
    e = p * (1 + soft)
    return (1 - smoothstep(e - soft, e, v)).astype(np.float32)


# ---------------------------------------------------------------- attributes
def param(tr, name: str, default: float) -> float:
    for c in tr:
        if isinstance(c.tag, str) and c.tag.rsplit("}", 1)[-1] == "param" and c.get("name") == name:
            try:
                return float(c.get("value"))
            except (TypeError, ValueError):
                return default
    return default


def softness(rc, tr, ctx) -> float:
    return max(0.0, min(1.0, rc.ev.num(tr, "softness", ctx, 0.1)))


def direction(rc, tr, ctx) -> tuple[float, float]:
    """Unit motion vector (dx, dy) in screen space (+y down)."""
    d = rc.ev.str(tr, "direction", ctx, "left") or "left"
    if d == "angle":
        a = math.radians(rc.ev.num(tr, "angle", ctx, 0.0))
        return math.cos(a), math.sin(a)
    return {"left": (-1.0, 0.0), "right": (1.0, 0.0), "up": (0.0, -1.0), "down": (0.0, 1.0)}.get(d, (-1.0, 0.0))


def axis_direction(rc, tr, ctx) -> str:
    """Direction snapped to left/right/up/down (for 3D-style transitions)."""
    dx, dy = direction(rc, tr, ctx)
    if abs(dx) >= abs(dy):
        return "left" if dx < 0 else "right"
    return "up" if dy < 0 else "down"


def color(rc, tr, ctx, default=(0.0, 0.0, 0.0, 1.0)) -> np.ndarray:
    """@color as a premultiplied working-space RGBA vector."""
    c = rc.ev.color(tr, "color", ctx, default)
    r, g, b, a = color_to_working(tuple(c) + ((1.0,) if len(c) == 3 else ()), rc.linear)
    return np.array([r * a, g * a, b * a, a], np.float32)


def rng(rc, tr, ctx, extra: str = "", temporal: bool = True):
    """Seeded draws (D24 splitmix64, scenerender.noise.Rng); temporal: the composition frame is the
    channel."""
    from ..noise import Rng
    frame = int(round(ctx.comp_t * float(rc.doc.fps))) if temporal else 0
    return Rng(rc.ev.seed_for(tr, "transition:" + extra), frame)


def velocity(rc, tr, ctx) -> float:
    """d(eased progress)/dt in 1/s at the current time (0 when unknown)."""
    try:
        from .. import curves
        w = rc.transition_window(tr, ctx)
        if w is None:
            return 0.0
        s0, s1, _ = w
        dur = max(1e-6, s1 - s0)
        u = (ctx.t - s0) / dur
        f = curves.get(rc.ev.str(tr, "curve", ctx, "ease-in-out"))
        h = 1e-3
        lo, hi = max(0.0, u - h), min(1.0, u + h)
        if hi <= lo:
            return 0.0
        return (f(hi) - f(lo)) / (hi - lo) / dur
    except Exception:
        return 0.0


def shutter_px(rc, tr, ctx, travel_px: float) -> float:
    """Motion-blur streak length for a picture moving travel_px per unit progress (180° shutter)."""
    if not rc.ev.bool(tr, "motionBlur", ctx, True):
        return 0.0
    fps = float(rc.doc.fps) or 30.0
    return abs(velocity(rc, tr, ctx) * travel_px) * 0.5 / fps


# ---------------------------------------------------------------- geometry helpers
def grid(rc) -> tuple[np.ndarray, np.ndarray]:
    """Pixel-centre coordinates (xs, ys), each (h, w) float32, cached per frame size."""
    key = ("transition-grid", rc.width, rc.height)
    g = rc.cache.get(key)
    if g is None:
        ys, xs = np.mgrid[0:rc.height, 0:rc.width].astype(np.float32)
        g = (xs + 0.5, ys + 0.5)
        rc.cache[key] = g
    return g


def axis_field(rc, d: tuple[float, float]) -> np.ndarray:
    """Coordinate along d normalised so the frame spans [0, 1] (0 at the trailing edge)."""
    xs, ys = grid(rc)
    proj = xs * d[0] + ys * d[1]
    corners = [0 * d[0] + 0 * d[1], rc.width * d[0], rc.height * d[1], rc.width * d[0] + rc.height * d[1]]
    lo, hi = min(corners), max(corners)
    return (proj - lo) / max(1e-6, hi - lo)


def extent(rc, d: tuple[float, float]) -> float:
    """Frame extent along d in pixels (distance to move a picture fully off-screen)."""
    return abs(d[0]) * rc.width + abs(d[1]) * rc.height


def center(rc, tr) -> tuple[float, float]:
    return param(tr, "cx", 0.5) * rc.width, param(tr, "cy", 0.5) * rc.height


def max_radius(rc, cx: float, cy: float) -> float:
    return max(math.hypot(x - cx, y - cy) for x in (0, rc.width) for y in (0, rc.height))


def translate(px: np.ndarray, ox: float, oy: float) -> np.ndarray:
    """Shift a picture by (ox, oy) pixels (sub-pixel bilinear, transparent fill)."""
    ix, iy = math.floor(ox), math.floor(oy)
    fx, fy = ox - ix, oy - iy
    if fx > 1 - 1e-3:
        ix, fx = ix + 1, 0.0
    if fy > 1 - 1e-3:
        iy, fy = iy + 1, 0.0

    def S(dx, dy):
        return _shift_int(px, ix + dx, iy + dy)
    if fx < 1e-3 and fy < 1e-3:
        return S(0, 0)
    if fy < 1e-3:
        return S(0, 0) * (1 - fx) + S(1, 0) * fx
    if fx < 1e-3:
        return S(0, 0) * (1 - fy) + S(0, 1) * fy
    return (S(0, 0) * (1 - fx) + S(1, 0) * fx) * (1 - fy) + (S(0, 1) * (1 - fx) + S(1, 1) * fx) * fy


def _shift_int(px: np.ndarray, dx: int, dy: int) -> np.ndarray:
    h, w = px.shape[:2]
    out = np.zeros_like(px)
    if abs(dx) >= w or abs(dy) >= h:
        return out
    sx0, sx1 = max(0, -dx), min(w, w - dx)
    sy0, sy1 = max(0, -dy), min(h, h - dy)
    out[sy0 + dy:sy1 + dy, sx0 + dx:sx1 + dx] = px[sy0:sy1, sx0:sx1]
    return out


def moved(px: np.ndarray, ox: float, oy: float, blur_len: float = 0.0, d=(0.0, 0.0)) -> np.ndarray:
    """translate() plus an optional box motion blur of blur_len pixels along d."""
    res = translate(px, ox, oy)
    return streak(res, blur_len, d) if blur_len >= 1.0 else res


def streak(px: np.ndarray, length: float, d) -> np.ndarray:
    """Box motion blur of `length` pixels along unit direction d (centred)."""
    n = int(round(length))
    if n < 2:
        return px
    if abs(d[0]) > 0.999 or abs(d[1]) > 0.999:
        return _box(px, n | 1, 1 if abs(d[0]) > 0.999 else 0)
    k = int(min(24, max(3, math.ceil(length / 3))))
    acc = np.zeros_like(px)
    for i in range(k):
        t = (i / (k - 1) - 0.5) * length
        acc += translate(px, d[0] * t, d[1] * t)
    return acc / k


def sample(px: np.ndarray, x: np.ndarray, y: np.ndarray, clamp: bool = False) -> np.ndarray:
    """Bilinear sampling at continuous coordinates (pixel centres at i + 0.5); transparent
    outside, or edge-extended with clamp=True."""
    h, w = px.shape[:2]
    if clamp:
        x = np.clip(x - 0.5, 0, w - 1)
        y = np.clip(y - 0.5, 0, h - 1)
        src, ox = px, 0
    else:
        # One transparent pixel of border (two on the far sides) makes clipping sample zeros.
        x = np.clip(x - 0.5, -1, w)
        y = np.clip(y - 0.5, -1, h)
        src, ox = np.pad(px, ((1, 2), (1, 2), (0, 0))), 1
    sh, sw = src.shape[:2]
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = (x - x0).astype(np.float32)[..., None]
    fy = (y - y0).astype(np.float32)[..., None]
    ix = x0.astype(np.int32) + ox
    iy = y0.astype(np.int32) + ox
    ix1 = np.minimum(ix + 1, sw - 1)
    iy1 = np.minimum(iy + 1, sh - 1)
    flat = src.reshape(-1, src.shape[-1])
    r0, r1 = iy * sw, iy1 * sw
    top = flat[r0 + ix] * (1 - fx) + flat[r0 + ix1] * fx
    bot = flat[r1 + ix] * (1 - fx) + flat[r1 + ix1] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32, copy=False)


def warp_affine(rc, px: np.ndarray, M: np.ndarray) -> np.ndarray:
    """Resample px through the 3x3 affine M (source pixel coords -> output pixel coords).

    Shrinking axes are pre-filtered (separable blur along source x / y) against aliasing.
    """
    if abs(np.linalg.det(M[:2, :2])) < 1e-8:
        return np.zeros_like(px)
    kx, ky = float(np.hypot(M[0, 0], M[1, 0])), float(np.hypot(M[0, 1], M[1, 1]))
    src = px
    if kx < 0.6 or ky < 0.6:
        src = gaussian(px, 0.45 / kx if kx < 0.6 else 0.0, 0.45 / ky if ky < 0.6 else 0.0)
    Mi = np.linalg.inv(M)
    xs, ys = grid(rc)
    sx = Mi[0, 0] * xs + Mi[0, 1] * ys + Mi[0, 2]
    sy = Mi[1, 0] * xs + Mi[1, 1] * ys + Mi[1, 2]
    return sample(src, sx, sy)


def warp_homography(rc, px: np.ndarray, H: np.ndarray) -> np.ndarray:
    """Resample px through the projective map H (source pixel coords -> output pixel coords).

    Only output pixels inside the projected quad are evaluated; points behind the
    camera (non-positive homogeneous w) stay transparent.
    """
    h, w = px.shape[:2]
    out = np.zeros((rc.height, rc.width, 4), np.float32)
    corners = np.array([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]], np.float64) @ H.T
    if np.any(corners[:, 2] <= 1e-6):
        x0, y0, x1, y1 = 0, 0, rc.width, rc.height
    else:
        cx, cy = corners[:, 0] / corners[:, 2], corners[:, 1] / corners[:, 2]
        x0, y0 = max(0, int(math.floor(cx.min())) - 1), max(0, int(math.floor(cy.min())) - 1)
        x1, y1 = min(rc.width, int(math.ceil(cx.max())) + 1), min(rc.height, int(math.ceil(cy.max())) + 1)
    if x1 <= x0 or y1 <= y0:
        return out
    Hi = np.linalg.inv(H)
    xs, ys = grid(rc)
    xs, ys = xs[y0:y1, x0:x1], ys[y0:y1, x0:x1]
    den = Hi[2, 0] * xs + Hi[2, 1] * ys + Hi[2, 2]
    safe = np.where(np.abs(den) > 1e-9, den, 1e-9)
    sx = (Hi[0, 0] * xs + Hi[0, 1] * ys + Hi[0, 2]) / safe
    sy = (Hi[1, 0] * xs + Hi[1, 1] * ys + Hi[1, 2]) / safe
    # Keep only points whose forward projection lies in front of the camera.
    fw = H[2, 0] * sx + H[2, 1] * sy + H[2, 2]
    vals = sample(px, sx, sy) * (fw > 1e-6)[..., None]
    out[y0:y1, x0:x1] = vals
    return out


def plane_homography(w: float, h: float, yaw: float, pos: tuple[float, float, float],
                     cam: float, screen: tuple[float, float]) -> np.ndarray:
    """Projective map of a w x h picture rotated by yaw (degrees, about its vertical centre line)
    and placed with its centre at pos = (X, Y, Z) (Z into the screen), seen by a pinhole camera
    at distance cam in front of the screen plane; screen = (cx, cy) is the image centre."""
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    X0, Y0, Z0 = pos
    # local (u - w/2, v - h/2) -> world
    A = np.array([[c, 0, X0 - c * w / 2],
                  [0, 1, Y0 - h / 2],
                  [s, 0, Z0 - s * w / 2 + cam]], np.float64)     # third row = D + Z
    scx, scy = screen
    P = np.array([[cam, 0, scx], [0, cam, scy], [0, 0, 1]], np.float64)
    return P @ A


def facing(yaw: float, pos: tuple[float, float, float], cam: float) -> bool:
    """True when the front of a plane (yaw, pos) faces the camera."""
    s, c = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    n = (s, -c)                                  # normal (X, Z); the unrotated front faces -Z
    v = (-pos[0], -cam - pos[2])                 # plane centre -> camera
    return n[0] * v[0] + n[1] * v[1] > 1e-6


class _Transposed:
    """A view of rc with width and height swapped (for transposed processing)."""

    def __init__(self, rc):
        self._rc = rc
        self.width, self.height = rc.height, rc.width
        self.cache = rc.cache

    def __getattr__(self, k):
        return getattr(self._rc, k)


def gaussian(px: np.ndarray, sigma: float, sigma_y: float | None = None, clamp: bool = False) -> np.ndarray:
    """Approximate Gaussian blur (three box passes per axis); edges treated as transparent,
    or extended (clamp=True, so full-frame pictures do not darken at the border).
    Large isotropic blurs run on a downsampled copy."""
    sy = sigma if sigma_y is None else sigma_y
    if sigma_y is None and sigma > 2.5 and min(px.shape[:2]) > 64:
        f = int(min(8, max(2, sigma // 2.5)))
        return _upsample(gaussian(_downsample(px, f, clamp), sigma / f, clamp=clamp), f, px.shape, clamp)
    out = px
    for axis, sg in ((1, sigma), (0, sy)):
        if sg < 0.4:
            continue
        wbox = max(1, int(math.sqrt(12 * sg * sg / 3 + 1)))
        wbox += (wbox + 1) % 2
        for _ in range(3):
            out = _box(out, wbox, axis, clamp)
    return out


def _downsample(px: np.ndarray, f: int, clamp: bool) -> np.ndarray:
    h, w = px.shape[:2]
    ph, pw = -h % f, -w % f
    p = np.pad(px, ((0, ph), (0, pw), (0, 0)), mode="edge" if clamp else "constant")
    return p.reshape((h + ph) // f, f, (w + pw) // f, f, px.shape[-1]).mean(axis=(1, 3), dtype=np.float32)


def _upsample(small: np.ndarray, f: int, shape, clamp: bool) -> np.ndarray:
    """Separable bilinear upsampling by f (edge-clamped) to shape."""
    def axis_weights(n_out, n_in):
        c = np.clip((np.arange(n_out, dtype=np.float32) + 0.5) / f - 0.5, 0, n_in - 1)
        i0 = np.floor(c).astype(np.int32)
        return i0, np.minimum(i0 + 1, n_in - 1), (c - i0).astype(np.float32)
    h, w = shape[:2]
    i0, i1, fx = axis_weights(w, small.shape[1])
    fx = fx[None, :, None]
    rows = small[:, i0] * (1 - fx) + small[:, i1] * fx
    j0, j1, fy = axis_weights(h, small.shape[0])
    fy = fy[:, None, None]
    return rows[j0] * (1 - fy) + rows[j1] * fy


def _box(a: np.ndarray, w: int, axis: int, clamp: bool = False) -> np.ndarray:
    if w <= 1:
        return a
    r = w // 2
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    # float32 running sums are accurate to ~1e-4 here (values <= 1, rows of a few thousand pixels)
    c = np.cumsum(np.pad(a, pad, mode="edge" if clamp else "constant"), axis=axis, dtype=np.float32)
    n = a.shape[axis]
    hi = [slice(None)] * a.ndim
    lo = [slice(None)] * a.ndim
    hi[axis] = slice(w, w + n)
    lo[axis] = slice(0, n)
    return (c[tuple(hi)] - c[tuple(lo)]) * np.float32(1.0 / w)


def luma_working(px: np.ndarray) -> np.ndarray:
    """Rec. 709 luminance of premultiplied working-space values (D19 luma), clamped to [0, 1]."""
    return np.clip(0.2126 * px[..., 0] + 0.7152 * px[..., 1] + 0.0722 * px[..., 2], 0.0, 1.0)


# ---------------------------------------------------------------- basic transitions
@TRANSITIONS.register("crossfade", "additive-dissolve", level=FULL)
def crossfade(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    if rc.transition_value(tr, "type", ctx) == "additive-dissolve":
        # Both pictures at full strength around the midpoint, summed like plus-lighter.
        return out(np.minimum(A * min(1.0, 2 * (1 - p)) + B * min(1.0, 2 * p), 1.0))
    return out(A * (1 - p) + B * p)
