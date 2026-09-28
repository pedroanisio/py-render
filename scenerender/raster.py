"""Raster buffers and colour conversion.

A Buf is a premultiplied RGBA float32 tile (h, w, 4) in the working space
placed at integer frame coordinates (x0, y0). Tiles keep per-node work
proportional to the node's on-screen size instead of the frame size.
Cairo draws in 8-bit sRGB-encoded premultiplied BGRA; to_working/from_working
convert between that and the float working space (linear when the project
uses linearLight, else sRGB-encoded).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cairo
import numpy as np

_SRGB_TO_LIN = np.array([(c / 255 / 12.92) if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4
                         for c in range(256)], np.float32)


# Working primaries: cairo, 8-bit images and colours arrive in sRGB/Rec.709 primaries. When the document
# composites in other primaries (colorManagement/@workingSpace), linear values are converted with _WM
# on the way in and _WM_INV on the way back out to display-referred sRGB. Set per RenderContext.
_WM: np.ndarray | None = None
_WM_INV: np.ndarray | None = None


def set_working_matrix(m: np.ndarray | None) -> None:
    """Install the sRGB-linear -> working-linear matrix (None = working primaries are Rec.709)."""
    global _WM, _WM_INV
    if m is None or np.allclose(m, np.eye(3), atol=1e-7):
        _WM = _WM_INV = None
    else:
        _WM = np.asarray(m, np.float32)
        _WM_INV = np.linalg.inv(m).astype(np.float32)


def to_working_primaries(rgb: np.ndarray) -> np.ndarray:
    """Linear sRGB-primaries RGB (last axis 3; premultiplied is fine) -> working primaries."""
    return rgb if _WM is None else rgb @ _WM.T


def from_working_primaries(rgb: np.ndarray) -> np.ndarray:
    """Linear working-primaries RGB -> linear sRGB primaries (before sRGB encoding for display ops)."""
    return rgb if _WM_INV is None else rgb @ _WM_INV.T


def srgb_to_linear(a: np.ndarray) -> np.ndarray:
    a = np.clip(a, 0.0, None)
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(a: np.ndarray) -> np.ndarray:
    a = np.clip(a, 0.0, None)
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1 / 2.4) - 0.055).astype(np.float32)


@dataclass
class Buf:
    px: np.ndarray          # (h, w, 4) float32, premultiplied
    x0: int = 0
    y0: int = 0

    @property
    def w(self) -> int:
        return self.px.shape[1]

    @property
    def h(self) -> int:
        return self.px.shape[0]

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.x0, self.y0, self.x0 + self.w, self.y0 + self.h

    @staticmethod
    def empty(x0: int, y0: int, w: int, h: int) -> "Buf":
        return Buf(np.zeros((max(1, h), max(1, w), 4), np.float32), x0, y0)

    @staticmethod
    def null() -> "Buf":
        """A zero-size tile: an empty accumulator that grows to exactly what is composited into it."""
        return Buf(np.zeros((0, 0, 4), np.float32), 0, 0)

    @property
    def is_null(self) -> bool:
        return self.px.size == 0

    def copy(self) -> "Buf":
        return Buf(self.px.copy(), self.x0, self.y0)

    def pad(self, n: int) -> "Buf":
        if n <= 0:
            return self
        return Buf(np.pad(self.px, ((n, n), (n, n), (0, 0))), self.x0 - n, self.y0 - n)

    def expand_to(self, rect: tuple[int, int, int, int]) -> "Buf":
        """Return a buffer covering the union of this tile and rect."""
        if self.is_null:
            return Buf.empty(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
        x0, y0 = min(self.x0, rect[0]), min(self.y0, rect[1])
        x1, y1 = max(self.x0 + self.w, rect[2]), max(self.y0 + self.h, rect[3])
        if (x0, y0, x1, y1) == self.rect:
            return self
        out = Buf.empty(x0, y0, x1 - x0, y1 - y0)
        out.px[self.y0 - y0:self.y0 - y0 + self.h, self.x0 - x0:self.x0 - x0 + self.w] = self.px
        return out

    def crop_to(self, rect: tuple[int, int, int, int]) -> "Buf":
        x0, y0 = max(self.x0, rect[0]), max(self.y0, rect[1])
        x1, y1 = min(self.x0 + self.w, rect[2]), min(self.y0 + self.h, rect[3])
        if x1 <= x0 or y1 <= y0:
            return Buf.empty(x0, y0, 1, 1)
        return Buf(self.px[y0 - self.y0:y1 - self.y0, x0 - self.x0:x1 - self.x0], x0, y0)

    def region(self, rect: tuple[int, int, int, int]) -> np.ndarray:
        """Pixels of rect (zeros outside the tile)."""
        x0, y0, x1, y1 = rect
        out = np.zeros((y1 - y0, x1 - x0, 4), np.float32)
        ix0, iy0 = max(x0, self.x0), max(y0, self.y0)
        ix1, iy1 = min(x1, self.x0 + self.w), min(y1, self.y0 + self.h)
        if ix1 > ix0 and iy1 > iy0:
            out[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = self.px[iy0 - self.y0:iy1 - self.y0, ix0 - self.x0:ix1 - self.x0]
        return out

    def scale_alpha(self, k: float | np.ndarray) -> None:
        self.px *= k if np.isscalar(k) else k[..., None]


def intersect(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def transformed_rect(M: np.ndarray, x0: float, y0: float, x1: float, y1: float, pad: float = 2.0):
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], np.float64) @ M.T
    return (int(math.floor(pts[:, 0].min() - pad)), int(math.floor(pts[:, 1].min() - pad)),
            int(math.ceil(pts[:, 0].max() + pad)), int(math.ceil(pts[:, 1].max() + pad)))


# ---------------------------------------------------------------- cairo <-> Buf
class Canvas:
    """A cairo ARGB32 surface covering a frame rectangle, ready to draw with frame-space matrices."""

    def __init__(self, rect: tuple[int, int, int, int]):
        self.rect = rect
        w, h = max(1, rect[2] - rect[0]), max(1, rect[3] - rect[1])
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        self.cr = cairo.Context(self.surface)
        self.cr.translate(-rect[0], -rect[1])

    def set_matrix(self, M: np.ndarray) -> None:
        self.cr.identity_matrix()
        self.cr.translate(-self.rect[0], -self.rect[1])
        self.cr.transform(cairo.Matrix(M[0, 0], M[1, 0], M[0, 1], M[1, 1], M[0, 2], M[1, 2]))

    def to_buf(self, linear: bool, crop: bool = False) -> Buf:
        """The canvas as a working-space Buf; crop: only its drawn pixels (for callers that place the
        result by its rect, e.g. node outputs: transparent pixels composite to nothing)."""
        self.surface.flush()
        h, w = self.surface.get_height(), self.surface.get_width()
        stride = self.surface.get_stride()
        raw = np.frombuffer(self.surface.get_data(), np.uint8).reshape(h, stride // 4, 4)[:, :w]
        if crop:
            drawn = raw[..., 3] != 0
            rows = np.flatnonzero(drawn.any(1))
            if rows.size:
                cols = np.flatnonzero(drawn[rows[0]:rows[-1] + 1].any(0))
                y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
                out = np.empty((y1 - y0, x1 - x0, 4), np.float32)
                _bgra_to_working(raw[y0:y1, x0:x1], linear, out)
                return Buf(out, self.rect[0] + x0, self.rect[1] + y0)
        return Buf(bgra_to_working(raw, linear), self.rect[0], self.rect[1])


def bgra_to_working(raw: np.ndarray, linear: bool) -> np.ndarray:
    """Premultiplied 8-bit BGRA (cairo) -> premultiplied float RGBA in the working space."""
    # Premultiplied pixels with zero alpha are zero in every channel, so only the bounding box of the
    # drawn pixels needs converting; canvases are often much larger than what was drawn on them.
    out = np.zeros(raw.shape[:2] + (4,), np.float32)
    drawn = raw[..., 3] != 0
    rows = np.flatnonzero(drawn.any(1))
    if not rows.size:
        return out
    cols = np.flatnonzero(drawn[rows[0]:rows[-1] + 1].any(0))
    box = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))
    _bgra_to_working(raw[box], linear, out[box])
    return out


def _bgra_to_working(raw: np.ndarray, linear: bool, out: np.ndarray) -> None:
    a = raw[..., 3].astype(np.float32) / 255.0
    if not linear:
        out[..., 0] = raw[..., 2] / 255.0
        out[..., 1] = raw[..., 1] / 255.0
        out[..., 2] = raw[..., 0] / 255.0
        out[..., 3] = a
        return
    from . import kernels
    if kernels.enabled() and _WM is None and raw.dtype == np.uint8:
        kernels.bgra_to_linear(raw, _PREMUL_LIN_FLAT, out)
        return
    # Flat (alpha << 8 | channel) indices into the 256x256 table gather faster than 2-D indexing.
    alpha = raw[..., 3].astype(np.intp) << 8
    for dst, src in ((0, 2), (1, 1), (2, 0)):
        out[..., dst] = _PREMUL_LIN_FLAT[alpha | raw[..., src]]
    out[..., 3] = a
    if _WM is not None:
        out[..., :3] = out[..., :3] @ _WM.T


def _premul_lin_table() -> np.ndarray:
    """T[a, c]: premultiplied 8-bit sRGB channel c at alpha a -> premultiplied linear float."""
    a = np.arange(256, dtype=np.float64)[:, None]
    c = np.arange(256, dtype=np.float64)[None, :]
    straight = np.clip(np.where(a > 0, c / np.maximum(a, 1), 0.0), 0, 1)
    lin = np.where(straight <= 0.04045, straight / 12.92, ((straight + 0.055) / 1.055) ** 2.4)
    return (lin * a / 255.0).astype(np.float32)


_PREMUL_LIN = _premul_lin_table()
_PREMUL_LIN_FLAT = _PREMUL_LIN.ravel()


def rgba8_to_working(rgba: np.ndarray, linear: bool, premultiplied: bool = False) -> np.ndarray:
    """Straight (or premultiplied) 8-bit RGBA image -> premultiplied float working space."""
    f = rgba.astype(np.float32) / 255.0
    if f.shape[-1] == 3:
        f = np.concatenate([f, np.ones(f.shape[:2] + (1,), np.float32)], -1)
    a = f[..., 3:4]
    if premultiplied:
        f[..., :3] = np.where(a > 0, f[..., :3] / np.where(a > 0, a, 1), 0)
    rgb = _SRGB_TO_LIN[rgba[..., :3]] if linear and rgba.dtype == np.uint8 else (srgb_to_linear(f[..., :3]) if linear else f[..., :3])
    out = np.empty(f.shape, np.float32)
    out[..., :3] = rgb * a
    out[..., 3:] = a
    if linear and _WM is not None:
        out[..., :3] = out[..., :3] @ _WM.T
    return out


def working_to_rgb8(px: np.ndarray, linear: bool, background=None) -> np.ndarray:
    """Premultiplied working-space frame -> straight 8-bit RGB (composited over background if given)."""
    from . import kernels
    if kernels.enabled() and px.dtype == np.float32:
        return kernels.to_rgb8(px, linear, background)
    a = px[..., 3:4]
    rgb = px[..., :3]
    if background is not None:
        rgb = rgb + np.asarray(background[:3], np.float32) * (1 - a)
        a = np.ones_like(a)
    straight = np.where(a > 1e-6, rgb / np.maximum(a, 1e-6), 0)
    if linear:
        straight = linear_to_srgb(straight)
    return (np.clip(straight, 0, 1) * 255 + 0.5).astype(np.uint8)


def to_straight(px: np.ndarray) -> np.ndarray:
    a = px[..., 3:4]
    out = px.copy()
    out[..., :3] = np.where(a > 1e-6, px[..., :3] / np.maximum(a, 1e-6), 0)
    return out


def to_premul(px: np.ndarray) -> np.ndarray:
    out = px.copy()
    out[..., :3] *= px[..., 3:4]
    return out


def color_to_working(c, linear: bool) -> tuple[float, float, float, float]:
    """Straight sRGB-encoded colour -> straight working-space colour."""
    r, g, b, a = c
    if linear:
        r, g, b = (float(srgb_to_linear(np.float32(v))) for v in (r, g, b))
        if _WM is not None:
            r, g, b = (float(v) for v in _WM @ np.array([r, g, b], np.float32))
    return r, g, b, a


# ---------------------------------------------------------------- perspective
def projected_scale(H: np.ndarray, w: float, h: float, limit: float = 4.0) -> float:
    """Resolution for drawing a w x h box flat so that warping it by H loses no detail:
    the largest local magnification of H over the box corners (capped)."""
    best = 0.0
    for x, y in ((0, 0), (w, 0), (0, h), (w, h), (w / 2, h / 2)):
        d = H[2, 0] * x + H[2, 1] * y + H[2, 2]
        if d <= 1e-9:
            continue
        px = (H[0, 0] * x + H[0, 1] * y + H[0, 2]) / d
        py = (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / d
        # Jacobian of the projective map at (x, y).
        jx = ((H[0, 0] - px * H[2, 0]) / d, (H[1, 0] - py * H[2, 0]) / d)
        jy = ((H[0, 1] - px * H[2, 1]) / d, (H[1, 1] - py * H[2, 1]) / d)
        best = max(best, math.hypot(*jx), math.hypot(*jy))
    return min(limit, best) if best > 0 else 0.0


def warp_projective(src: Buf, H: np.ndarray, frame_rect, margin: int = 64) -> Buf | None:
    """Warp a premultiplied tile by the projective map H (tile-space pixels -> frame pixels),
    bilinear, points behind the projection centre dropped."""
    x0, y0 = src.x0, src.y0
    corners = np.array([[x0, y0, 1], [x0 + src.w, y0, 1], [x0, y0 + src.h, 1], [x0 + src.w, y0 + src.h, 1]], np.float64)
    q = corners @ H.T
    if (q[:, 2] <= 1e-9).all():
        return None
    ok = q[:, 2] > 1e-9
    xs, ys = q[ok, 0] / q[ok, 2], q[ok, 1] / q[ok, 2]
    fr = (frame_rect[0] - margin, frame_rect[1] - margin, frame_rect[2] + margin, frame_rect[3] + margin)
    if not ok.all():
        rect = fr
    else:
        rect = (int(math.floor(xs.min())) - 1, int(math.floor(ys.min())) - 1, int(math.ceil(xs.max())) + 1, int(math.ceil(ys.max())) + 1)
        rect = intersect(rect, fr)
    if rect is None:
        return None
    Hi = np.linalg.inv(H)
    gy, gx = np.mgrid[rect[1]:rect[3], rect[0]:rect[2]].astype(np.float64)
    gx += 0.5
    gy += 0.5
    d = Hi[2, 0] * gx + Hi[2, 1] * gy + Hi[2, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        sx = (Hi[0, 0] * gx + Hi[0, 1] * gy + Hi[0, 2]) / d - x0 - 0.5
        sy = (Hi[1, 0] * gx + Hi[1, 1] * gy + Hi[1, 2]) / d - y0 - 0.5
    # A pixel is visible only if its source point projects in front of the camera.
    wsrc = H[2, 0] * (sx + x0 + 0.5) + H[2, 1] * (sy + y0 + 0.5) + H[2, 2]
    valid = np.isfinite(sx) & np.isfinite(sy) & (wsrc > 1e-9)
    sx = np.where(valid, sx, -10.0)
    sy = np.where(valid, sy, -10.0)
    ix, iy = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    fx, fy = (sx - ix).astype(np.float32), (sy - iy).astype(np.float32)
    out = np.zeros(gx.shape + (4,), np.float32)
    h, w = src.h, src.w
    for dx, dy, wt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        jx, jy = ix + dx, iy + dy
        inside = (jx >= 0) & (jx < w) & (jy >= 0) & (jy < h)
        v = src.px[np.clip(jy, 0, h - 1), np.clip(jx, 0, w - 1)]
        out += v * (wt * inside)[..., None]
    return Buf(out, rect[0], rect[1])
