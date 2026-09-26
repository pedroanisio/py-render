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

    def copy(self) -> "Buf":
        return Buf(self.px.copy(), self.x0, self.y0)

    def pad(self, n: int) -> "Buf":
        if n <= 0:
            return self
        return Buf(np.pad(self.px, ((n, n), (n, n), (0, 0))), self.x0 - n, self.y0 - n)

    def expand_to(self, rect: tuple[int, int, int, int]) -> "Buf":
        """Return a buffer covering the union of this tile and rect."""
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

    def to_buf(self, linear: bool) -> Buf:
        self.surface.flush()
        h, w = self.surface.get_height(), self.surface.get_width()
        stride = self.surface.get_stride()
        raw = np.frombuffer(self.surface.get_data(), np.uint8).reshape(h, stride // 4, 4)[:, :w]
        return Buf(bgra_to_working(raw, linear), self.rect[0], self.rect[1])


def bgra_to_working(raw: np.ndarray, linear: bool) -> np.ndarray:
    """Premultiplied 8-bit BGRA (cairo) -> premultiplied float RGBA in the working space."""
    a = raw[..., 3].astype(np.float32) / 255.0
    out = np.empty(raw.shape[:2] + (4,), np.float32)
    if not linear:
        out[..., 0] = raw[..., 2] / 255.0
        out[..., 1] = raw[..., 1] / 255.0
        out[..., 2] = raw[..., 0] / 255.0
        out[..., 3] = a
        return out
    alpha = raw[..., 3]
    for dst, src in ((0, 2), (1, 1), (2, 0)):
        out[..., dst] = _PREMUL_LIN[alpha, raw[..., src]]
    out[..., 3] = a
    return out


def _premul_lin_table() -> np.ndarray:
    """T[a, c]: premultiplied 8-bit sRGB channel c at alpha a -> premultiplied linear float."""
    a = np.arange(256, dtype=np.float64)[:, None]
    c = np.arange(256, dtype=np.float64)[None, :]
    straight = np.clip(np.where(a > 0, c / np.maximum(a, 1), 0.0), 0, 1)
    lin = np.where(straight <= 0.04045, straight / 12.92, ((straight + 0.055) / 1.055) ** 2.4)
    return (lin * a / 255.0).astype(np.float32)


_PREMUL_LIN = _premul_lin_table()


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
    return out


def working_to_rgb8(px: np.ndarray, linear: bool, background=None) -> np.ndarray:
    """Premultiplied working-space frame -> straight 8-bit RGB (composited over background if given)."""
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
    return r, g, b, a
