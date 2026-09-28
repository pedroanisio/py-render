"""Fused per-pixel kernels (Numba) for the whole-frame effect chains.

Each NumPy effect chains ten or more full-frame passes (straight alpha, transfer functions, the
operation, premultiply), and at 1080p every pass writes a fresh ~33 MB array. A kernel here runs
the same per-pixel arithmetic in one pass over rows, in the threads threads() allows. The NumPy
implementations remain the reference and the fallback: results agree to float32 rounding (the
order of a few operations differs), which never moves an 8-bit value by more than one level.

SCENERENDER_KERNELS=0 disables the kernels (every effect takes its NumPy path).
"""
from __future__ import annotations

import os

import numpy as np

try:
    import numba as nb
except ImportError:  # optional: the NumPy paths render identically up to rounding
    nb = None


def enabled() -> bool:
    return nb is not None and os.environ.get("SCENERENDER_KERNELS", "1") != "0"


def _threads() -> None:
    from . import threads
    n = max(1, min(threads(), nb.config.NUMBA_NUM_THREADS))
    if nb.get_num_threads() != n:
        nb.set_num_threads(n)


def _matrix(m) -> tuple[np.ndarray, bool]:
    return (np.eye(3, dtype=np.float32), False) if m is None else (np.ascontiguousarray(m, np.float32), True)


# The sRGB curves over [0, 1], sampled finely enough that linear interpolation stays within 1e-7 of
# the power functions (a per-pixel powf costs ~20 ns; a table read a few). Values above 1 use pow.
_LUT_N = 1 << 16


def _curve_table(fn) -> np.ndarray:
    x = np.linspace(0.0, 1.0, _LUT_N + 1)
    t = fn(x).astype(np.float32)
    return np.append(t, t[-1])      # x == 1 reads entries N and N + 1


# 8-bit sRGB codes straight from linear values: code k starts where linear_to_srgb(x) * 255 + .5 reaches
# k. A coarse table gives the code at each bin start; a comparison or two finds the exact one.
_Q8_BINS = 4096


def _code_tables() -> tuple[np.ndarray, np.ndarray]:
    k = np.arange(256, dtype=np.float64)
    e = np.clip((k - 0.5) / 255, 0, None)
    start = np.where(e <= 0.04045, e / 12.92, ((e + 0.055) / 1.055) ** 2.4)
    start[0] = -np.inf
    start = np.append(start, np.inf).astype(np.float32)
    base = (np.searchsorted(start[1:256], np.linspace(0, 1, _Q8_BINS + 1, dtype=np.float32), side="right"))
    return start, base.astype(np.uint8)


_Q8_START, _Q8_BASE = _code_tables()
_L2S = _curve_table(lambda x: np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055))
_S2L = _curve_table(lambda x: np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4))

if nb is not None:
    F = np.float32
    _jit = nb.njit(cache=True, inline="always")
    _par = nb.njit(cache=True, parallel=True)

    @_jit
    def _lerp(table, x):
        t = x * F(_LUT_N)
        i = int(t)
        return table[i] + (table[i + 1] - table[i]) * (t - F(i))

    @_jit
    def _l2s(x):
        """linear_to_srgb for one float32 value."""
        if x < F(0):
            x = F(0)
        if x <= F(0.0031308):
            return x * F(12.92)
        if x <= F(1):
            return _lerp(_L2S, x)
        return F(1.055) * x ** F(1 / 2.4) - F(0.055)

    @_jit
    def _s2l(x):
        """srgb_to_linear for one float32 value."""
        if x < F(0):
            x = F(0)
        if x <= F(0.04045):
            return x / F(12.92)
        if x <= F(1):
            return _lerp(_S2L, x)
        return ((x + F(0.055)) / F(1.055)) ** F(2.4)

    @_jit
    def _mul3(m, r, g, b):
        return (m[0, 0] * r + m[0, 1] * g + m[0, 2] * b,
                m[1, 0] * r + m[1, 1] * g + m[1, 2] * b,
                m[2, 0] * r + m[2, 1] * g + m[2, 2] * b)

    @_jit
    def _clip01(x):
        return F(0) if x < F(0) else F(1) if x > F(1) else x

    # Display-referred operations, applied in encoded sRGB between display_rgb and from_display.
    OP_LGG, OP_GRADE = 0, 1

    @_jit
    def _display_op(op, q, k, r, g, b):
        """Operation k of a chain; q[k] holds its parameters (indexed, not sliced: a view per pixel
        costs a reference count update)."""
        if op == OP_LGG:
            # q[k]: lift[3], 1/gamma[3], gain[3]
            r = max(r + q[k, 0] * (F(1) - r), F(0))
            g = max(g + q[k, 1] * (F(1) - g), F(0))
            b = max(b + q[k, 2] * (F(1) - b), F(0))
            r = (r if q[k, 3] == F(1) else r ** q[k, 3]) * q[k, 6]
            g = (g if q[k, 4] == F(1) else g ** q[k, 4]) * q[k, 7]
            b = (b if q[k, 5] == F(1) else b ** q[k, 5]) * q[k, 8]
            return r, g, b
        # OP_GRADE: q[k]: saturation, contrast, brightness
        s, c, o = q[k, 0], q[k, 1], q[k, 2]
        y = F(0.2126) * r + F(0.7152) * g + F(0.0722) * b
        r = y + (r - y) * s
        g = y + (g - y) * s
        b = y + (b - y) * s
        return (r - F(.5)) * c + F(.5) + o, (g - F(.5)) * c + F(.5) + o, (b - F(.5)) * c + F(.5) + o

    @_par
    def _display_chain(px, out, ops, params, linear, minv, m, has_m):
        h, w = px.shape[0], px.shape[1]
        for y in nb.prange(h):
            for x in range(w):
                a = px[y, x, 3]
                if a > F(1e-6):
                    r, g, b = px[y, x, 0] / a, px[y, x, 1] / a, px[y, x, 2] / a
                else:
                    r = g = b = F(0)
                # display_rgb
                if linear:
                    if has_m:
                        r, g, b = _mul3(minv, r, g, b)
                    r, g, b = _l2s(r), _l2s(g), _l2s(b)
                for k in range(len(ops)):
                    r, g, b = _display_op(ops[k], params, k, r, g, b)
                    # from_display clamps colour and alpha. Between operations the encoded colour
                    # goes straight on: decoding, premultiplying, dividing and re-encoding it
                    # again only rounds (a transparent pixel restarts from black).
                    r, g, b = _clip01(r), _clip01(g), _clip01(b)
                    a = _clip01(a)
                    if not a > F(1e-6):
                        r = g = b = F(0)
                # from_display
                if linear:
                    r, g, b = _s2l(r), _s2l(g), _s2l(b)
                    if has_m:
                        r, g, b = _mul3(m, r, g, b)
                out[y, x, 0], out[y, x, 1], out[y, x, 2], out[y, x, 3] = r * a, g * a, b * a, a

    @_par
    def _exposure(px, out, k):
        h, w = px.shape[0], px.shape[1]
        for y in nb.prange(h):
            for x in range(w):
                a = px[y, x, 3]
                for c in range(3):
                    out[y, x, c] = (px[y, x, c] / a * k) * a if a > F(1e-6) else F(0)
                out[y, x, 3] = a

    @_par
    def _grain(px, grain, out, k, response, strength):
        h, w = px.shape[0], px.shape[1]
        for y in nb.prange(h):
            for x in range(w):
                a = px[y, x, 3]
                for c in range(3):
                    s = px[y, x, c] / a if a > F(1e-6) else F(0)
                    v = _clip01(s)
                    wt = max(F(4) * v * (F(1) - v), F(0))
                    wt = np.sqrt(wt) if response == F(.5) else wt ** response
                    out[y, x, c] = max(s + grain[y, x, c] * k * wt * strength[c], F(0)) * a
                out[y, x, 3] = a

    @_par
    def _vignette(px, wmap, out, col):
        h, w = px.shape[0], px.shape[1]
        for y in nb.prange(h):
            for x in range(w):
                a = px[y, x, 3]
                f = wmap[y, x, 0] * col[3]
                for c in range(3):
                    s = px[y, x, c] / a if a > F(1e-6) else F(0)
                    out[y, x, c] = (s + (col[c] - s) * f) * a
                out[y, x, 3] = a

    @_par
    def _lighting(px, height, out, kinds, dirs, cols, vis, has_vis, intensity):
        """Ambient and directional lights over the alpha-relief normals (np.gradient differences)."""
        h, w = px.shape[0], px.shape[1]
        for y in nb.prange(h):
            for x in range(w):
                if x == 0:
                    gx = height[y, 1] - height[y, 0]
                elif x == w - 1:
                    gx = height[y, x] - height[y, x - 1]
                else:
                    gx = (height[y, x + 1] - height[y, x - 1]) / F(2)
                if y == 0:
                    gy = height[1, x] - height[0, x]
                elif y == h - 1:
                    gy = height[y, x] - height[y - 1, x]
                else:
                    gy = (height[y + 1, x] - height[y - 1, x]) / F(2)
                nx, ny = -gx, -gy
                norm = np.sqrt(nx * nx + ny * ny + F(1))
                nx, ny, nz = nx / norm, ny / norm, F(1) / norm
                ir = ig = ib = F(0)
                for k in range(len(kinds)):
                    if kinds[k] == 0:
                        ir += cols[k, 0]
                        ig += cols[k, 1]
                        ib += cols[k, 2]
                        continue
                    nd = max(nx * dirs[k, 0] + ny * dirs[k, 1] + nz * dirs[k, 2], 0.0)
                    if has_vis[k]:
                        nd *= vis[k, y, x]
                    s = F(nd)
                    ir += s * cols[k, 0]
                    ig += s * cols[k, 1]
                    ib += s * cols[k, 2]
                a = px[y, x, 3]
                if a > F(1e-6):
                    out[y, x, 0] = max(px[y, x, 0] / a * ir * intensity, F(0)) * a
                    out[y, x, 1] = max(px[y, x, 1] / a * ig * intensity, F(0)) * a
                    out[y, x, 2] = max(px[y, x, 2] / a * ib * intensity, F(0)) * a
                else:
                    out[y, x, 0] = out[y, x, 1] = out[y, x, 2] = F(0)
                out[y, x, 3] = a

    @_par
    def _mix(before, after, out, opacity):
        h, w = before.shape[0], before.shape[1]
        for y in nb.prange(h):
            for x in range(w):
                for c in range(4):
                    b = before[y, x, c]
                    out[y, x, c] = b + (after[y, x, c] - b) * opacity


    @_jit
    def _code(x):
        """uint8(clip(linear_to_srgb(x), 0, 1) * 255 + .5)."""
        if not x > F(0):
            return np.uint8(0)
        if x >= F(1):
            return np.uint8(255)
        k = _Q8_BASE[int(x * F(_Q8_BINS))]
        while x >= _Q8_START[k + 1]:
            k += 1
        return np.uint8(k)

    @_par
    def _to_rgb8(px, out, linear, has_bg, bg):
        h, w = px.shape[0], px.shape[1]
        for y in nb.prange(h):
            for x in range(w):
                a = px[y, x, 3]
                r, g, b = px[y, x, 0], px[y, x, 1], px[y, x, 2]
                if has_bg:
                    r, g, b, a = r + bg[0] * (F(1) - a), g + bg[1] * (F(1) - a), b + bg[2] * (F(1) - a), F(1)
                if a > F(1e-6):
                    d = max(a, F(1e-6))
                    r, g, b = r / d, g / d, b / d
                else:
                    r = g = b = F(0)
                if linear:
                    out[y, x, 0], out[y, x, 1], out[y, x, 2] = _code(r), _code(g), _code(b)
                else:
                    out[y, x, 0] = np.uint8(_clip01(r) * F(255) + F(.5))
                    out[y, x, 1] = np.uint8(_clip01(g) * F(255) + F(.5))
                    out[y, x, 2] = np.uint8(_clip01(b) * F(255) + F(.5))

    @_par
    def _warp_affine(src, hi, ox, oy, out):
        """out[y, x] = bilinear sample of src at hi . (ox + x + .5, oy + y + .5) - .5 (pixel centres;
        texels outside src are transparent), as raster.warp_projective does for an affine map. The
        source point advances by (hi00, hi10) per output pixel along a row."""
        h, w = src.shape[0], src.shape[1]
        oh, ow = out.shape[0], out.shape[1]
        a00, a01, a02 = hi[0, 0], hi[0, 1], hi[0, 2]
        a10, a11, a12 = hi[1, 0], hi[1, 1], hi[1, 2]
        for y in nb.prange(oh):
            gy = np.float64(oy + y) + 0.5
            gx0 = np.float64(ox) + 0.5
            sx = a00 * gx0 + a01 * gy + a02 - 0.5
            sy = a10 * gx0 + a11 * gy + a12 - 0.5
            for x in range(ow):
                ix = int(np.floor(sx))
                iy = int(np.floor(sy))
                fx = F(sx - ix)
                fy = F(sy - iy)
                if ix >= 0 and iy >= 0 and ix + 1 < w and iy + 1 < h:
                    w00 = (F(1) - fx) * (F(1) - fy)
                    w10 = fx * (F(1) - fy)
                    w01 = (F(1) - fx) * fy
                    w11 = fx * fy
                    for c in range(4):
                        out[y, x, c] = (src[iy, ix, c] * w00 + src[iy, ix + 1, c] * w10
                                        + src[iy + 1, ix, c] * w01 + src[iy + 1, ix + 1, c] * w11)
                elif ix < -1 or iy < -1 or ix >= w or iy >= h:
                    for c in range(4):
                        out[y, x, c] = F(0)
                else:
                    for c in range(4):
                        acc = F(0)
                        for dy in range(2):
                            jy = iy + dy
                            if jy < 0 or jy >= h:
                                continue
                            wy = fy if dy == 1 else F(1) - fy
                            for dx in range(2):
                                jx = ix + dx
                                if 0 <= jx < w:
                                    acc += src[jy, jx, c] * wy * (fx if dx == 1 else F(1) - fx)
                        out[y, x, c] = acc
                sx += a00
                sy += a10

    @_par
    def _warp_over(dst, src, hi, ox, oy):
        """Source-over of src warped as in _warp_affine onto dst (in place), in one pass."""
        h, w = src.shape[0], src.shape[1]
        oh, ow = dst.shape[0], dst.shape[1]
        a00, a01, a02 = hi[0, 0], hi[0, 1], hi[0, 2]
        a10, a11, a12 = hi[1, 0], hi[1, 1], hi[1, 2]
        for y in nb.prange(oh):
            gy = np.float64(oy + y) + 0.5
            gx0 = np.float64(ox) + 0.5
            sx = a00 * gx0 + a01 * gy + a02 - 0.5
            sy = a10 * gx0 + a11 * gy + a12 - 0.5
            for x in range(ow):
                ix = int(np.floor(sx))
                iy = int(np.floor(sy))
                if ix >= 0 and iy >= 0 and ix + 1 < w and iy + 1 < h:
                    fx = F(sx - ix)
                    fy = F(sy - iy)
                    w00 = (F(1) - fx) * (F(1) - fy)
                    w10 = fx * (F(1) - fy)
                    w01 = (F(1) - fx) * fy
                    w11 = fx * fy
                    s3 = (src[iy, ix, 3] * w00 + src[iy, ix + 1, 3] * w10
                          + src[iy + 1, ix, 3] * w01 + src[iy + 1, ix + 1, 3] * w11)
                    k = F(1) - s3
                    for c in range(3):
                        dst[y, x, c] = dst[y, x, c] * k + (src[iy, ix, c] * w00 + src[iy, ix + 1, c] * w10
                                                           + src[iy + 1, ix, c] * w01 + src[iy + 1, ix + 1, c] * w11)
                    dst[y, x, 3] = dst[y, x, 3] * k + s3
                elif not (ix < -1 or iy < -1 or ix >= w or iy >= h):
                    fx = F(sx - ix)
                    fy = F(sy - iy)
                    s0 = s1 = s2 = s3 = F(0)
                    for dy in range(2):
                        jy = iy + dy
                        if jy < 0 or jy >= h:
                            continue
                        wy = fy if dy == 1 else F(1) - fy
                        for dx in range(2):
                            jx = ix + dx
                            if jx < 0 or jx >= w:
                                continue
                            wt = wy * (fx if dx == 1 else F(1) - fx)
                            s0 += src[jy, jx, 0] * wt
                            s1 += src[jy, jx, 1] * wt
                            s2 += src[jy, jx, 2] * wt
                            s3 += src[jy, jx, 3] * wt
                    k = F(1) - s3
                    dst[y, x, 0] = dst[y, x, 0] * k + s0
                    dst[y, x, 1] = dst[y, x, 1] * k + s1
                    dst[y, x, 2] = dst[y, x, 2] * k + s2
                    dst[y, x, 3] = dst[y, x, 3] * k + s3
                sx += a00
                sy += a10

    @_par
    def _warp_affine_axis(src, ix, fx, iy, fy, out):
        """_warp_affine for an axis-aligned map (per-column / per-row source coordinates)."""
        h, w = src.shape[0], src.shape[1]
        oh, ow = out.shape[0], out.shape[1]
        for y in nb.prange(oh):
            y0 = iy[y]
            wy1 = fy[y]
            wy0 = F(1) - wy1
            for x in range(ow):
                x0 = ix[x]
                wx1 = fx[x]
                wx0 = F(1) - wx1
                if x0 >= 0 and y0 >= 0 and x0 + 1 < w and y0 + 1 < h:
                    w00, w10, w01, w11 = wx0 * wy0, wx1 * wy0, wx0 * wy1, wx1 * wy1
                    for c in range(4):
                        out[y, x, c] = (src[y0, x0, c] * w00 + src[y0, x0 + 1, c] * w10
                                        + src[y0 + 1, x0, c] * w01 + src[y0 + 1, x0 + 1, c] * w11)
                elif x0 < -1 or y0 < -1 or x0 >= w or y0 >= h:
                    for c in range(4):
                        out[y, x, c] = F(0)
                else:
                    for c in range(4):
                        acc = F(0)
                        for dy in range(2):
                            jy = y0 + dy
                            if jy < 0 or jy >= h:
                                continue
                            wy = wy1 if dy == 1 else wy0
                            for dx in range(2):
                                jx = x0 + dx
                                if 0 <= jx < w:
                                    acc += src[jy, jx, c] * wy * (wx1 if dx == 1 else wx0)
                        out[y, x, c] = acc

    @_par
    def _warp_over_axis(dst, src, ix, fx, iy, fy):
        """_warp_over for an axis-aligned map (scale + translation): per-column (ix, fx) and per-row
        (iy, fy) source coordinates computed once, with the same bilinear weights."""
        h, w = src.shape[0], src.shape[1]
        oh, ow = dst.shape[0], dst.shape[1]
        for y in nb.prange(oh):
            y0 = iy[y]
            if y0 < -1 or y0 >= h:
                continue
            wy1 = fy[y]
            wy0 = F(1) - wy1
            for x in range(ow):
                x0 = ix[x]
                if x0 < -1 or x0 >= w:
                    continue
                wx1 = fx[x]
                wx0 = F(1) - wx1
                s0 = s1 = s2 = s3 = F(0)
                if x0 >= 0 and y0 >= 0 and x0 + 1 < w and y0 + 1 < h:
                    w00, w10, w01, w11 = wx0 * wy0, wx1 * wy0, wx0 * wy1, wx1 * wy1
                    s0 = src[y0, x0, 0] * w00 + src[y0, x0 + 1, 0] * w10 + src[y0 + 1, x0, 0] * w01 + src[y0 + 1, x0 + 1, 0] * w11
                    s1 = src[y0, x0, 1] * w00 + src[y0, x0 + 1, 1] * w10 + src[y0 + 1, x0, 1] * w01 + src[y0 + 1, x0 + 1, 1] * w11
                    s2 = src[y0, x0, 2] * w00 + src[y0, x0 + 1, 2] * w10 + src[y0 + 1, x0, 2] * w01 + src[y0 + 1, x0 + 1, 2] * w11
                    s3 = src[y0, x0, 3] * w00 + src[y0, x0 + 1, 3] * w10 + src[y0 + 1, x0, 3] * w01 + src[y0 + 1, x0 + 1, 3] * w11
                else:
                    for dy in range(2):
                        jy = y0 + dy
                        if jy < 0 or jy >= h:
                            continue
                        wy = wy1 if dy == 1 else wy0
                        for dx in range(2):
                            jx = x0 + dx
                            if jx < 0 or jx >= w:
                                continue
                            wt = wy * (wx1 if dx == 1 else wx0)
                            s0 += src[jy, jx, 0] * wt
                            s1 += src[jy, jx, 1] * wt
                            s2 += src[jy, jx, 2] * wt
                            s3 += src[jy, jx, 3] * wt
                k = F(1) - s3
                dst[y, x, 0] = dst[y, x, 0] * k + s0
                dst[y, x, 1] = dst[y, x, 1] * k + s1
                dst[y, x, 2] = dst[y, x, 2] * k + s2
                dst[y, x, 3] = dst[y, x, 3] * k + s3

    @_par
    def _over_tiles(d, s, ty, tx, t):
        """_over restricted to the listed t x t tiles of s (the others are fully transparent)."""
        h, w = s.shape[0], s.shape[1]
        for i in nb.prange(len(ty)):
            y0, x0 = ty[i] * t, tx[i] * t
            for y in range(y0, min(y0 + t, h)):
                for x in range(x0, min(x0 + t, w)):
                    k = F(1) - s[y, x, 3]
                    for c in range(4):
                        d[y, x, c] = d[y, x, c] * k + s[y, x, c]

    @_par
    def _under(d, s):
        """Destination-over in place: d = d + s * (1 - d.alpha) (blend mode "behind")."""
        h, w = d.shape[0], d.shape[1]
        for y in nb.prange(h):
            for x in range(w):
                k = F(1) - d[y, x, 3]
                for c in range(4):
                    d[y, x, c] = d[y, x, c] + s[y, x, c] * k

    @_par
    def _over(d, s):
        """Source-over in place: d = d * (1 - s.alpha) + s (any memory layout)."""
        h, w = d.shape[0], d.shape[1]
        for y in nb.prange(h):
            for x in range(w):
                k = F(1) - s[y, x, 3]
                for c in range(4):
                    d[y, x, c] = d[y, x, c] * k + s[y, x, c]


    @_par
    def _noise_octave(out, t0, t1, jx0, jx1, fxs, jy0, jy1, fys, f, weight, turbulent, shaping):
        """One fractal octave from its two phase lattices (value_noise, the phase blend and the
        kind's shaping, in the float32/float64 steps fractal() takes); t0/t1[j, k] hold lattice
        values at compacted row/column indices."""
        h, w = out.shape
        for y in nb.prange(h):
            a0, a1, fy = jy0[y], jy1[y], fys[y]
            for x in range(w):
                b0, b1, fx = jx0[x], jx1[x], fxs[x]
                a = t0[a0, b0] * (F(1) - fx) + t0[a0, b1] * fx
                b = t0[a1, b0] * (F(1) - fx) + t0[a1, b1] * fx
                z0 = a * (F(1) - fy) + b * fy
                a = t1[a0, b0] * (F(1) - fx) + t1[a0, b1] * fx
                b = t1[a1, b0] * (F(1) - fx) + t1[a1, b1] * fx
                z1 = a * (F(1) - fy) + b * fy
                z = np.float64(z0) * (1.0 - f) + np.float64(z1) * f
                if turbulent:
                    z = abs(2 * z - 1)
                if shaping == 1:
                    z = z * z
                elif shaping == 2:
                    z = z * z * (3 - 2 * z)
                elif shaping == 3:
                    z = min(max(z * 3 - .5, 0.0), 1.0)
                elif shaping == 4:
                    z = (1 - z) ** 6
                out[y, x] = np.float32(np.float64(out[y, x]) + z * weight)


    @_par
    def _dither(pixels, tile, top, left, out):
        """paint._dither_surface's quantization: premultiplied float -> ordered-dithered BGRA8."""
        h, w = out.shape[0], out.shape[1]
        for y in nb.prange(h):
            ty = (y + top) % 8
            for x in range(w):
                n = tile[ty, (x + left) % 8]
                alpha = min(max(pixels[y, x, 3], F(0)), F(1))
                a8 = np.floor(alpha * F(255) + F(.5))
                for c in range(3):
                    v = pixels[y, x, c] * F(255)
                    v = v + F(.5)
                    v = v + n * alpha
                    v = min(max(np.floor(v), F(0)), a8)
                    out[y, x, 2 - c] = np.uint8(v)
                out[y, x, 3] = np.uint8(a8)

    @_par
    def _bgra_lin(raw, table, out):
        """raster._bgra_to_working (linear, no primaries matrix): premultiplied 8-bit -> linear float."""
        h, w = raw.shape[0], raw.shape[1]
        for y in nb.prange(h):
            for x in range(w):
                a = np.int64(raw[y, x, 3]) << 8
                out[y, x, 0] = table[a | raw[y, x, 2]]
                out[y, x, 1] = table[a | raw[y, x, 1]]
                out[y, x, 2] = table[a | raw[y, x, 0]]
                out[y, x, 3] = F(raw[y, x, 3]) / F(255)


    @_jit
    def _grad(grad3, perm, h, dx, dy, dz):
        k = perm[h] & 15
        return grad3[k, 0] * dx + grad3[k, 1] * dy + grad3[k, 2] * dz

    @_jit
    def _fade(t):
        return t * t * t * (t * (t * 6 - 15) + 10)

    @_jit
    def _perlin3(x, y, z, perm, grad3):
        """assets.generator.perlin3 for one float64 sample (the same operations in the same order)."""
        xf, yf, zf = np.floor(x), np.floor(y), np.floor(z)
        X, Y, Z = np.int64(xf) & 255, np.int64(yf) & 255, np.int64(zf) & 255
        x, y, z = x - xf, y - yf, z - zf
        u, v, w = _fade(x), _fade(y), _fade(z)
        A, B = perm[X] + Y, perm[X + 1] + Y
        AA, AB, BA, BB = perm[A] + Z, perm[A + 1] + Z, perm[B] + Z, perm[B + 1] + Z
        g0 = _grad(grad3, perm, AA, x, y, z)
        x1 = g0 + u * (_grad(grad3, perm, BA, x - 1, y, z) - g0)
        g0 = _grad(grad3, perm, AB, x, y - 1, z)
        x2 = g0 + u * (_grad(grad3, perm, BB, x - 1, y - 1, z) - g0)
        y1 = x1 + v * (x2 - x1)
        g0 = _grad(grad3, perm, AA + 1, x, y, z - 1)
        x3 = g0 + u * (_grad(grad3, perm, BA + 1, x - 1, y, z - 1) - g0)
        g0 = _grad(grad3, perm, AB + 1, x, y - 1, z - 1)
        x4 = g0 + u * (_grad(grad3, perm, BB + 1, x - 1, y - 1, z - 1) - g0)
        y2 = x3 + v * (x4 - x3)
        return y1 + w * (y2 - y1)

    @_par
    def _fbm(x, y, z, perm, grad3, octaves, out):
        h, w = x.shape
        for i in nb.prange(h):
            for j in range(w):
                total, amp = 0.0, 1.0
                for o in range(octaves):
                    f = 2.0 ** o
                    total += amp * _perlin3(x[i, j] * f + 17.3 * o, y[i, j] * f + 5.1 * o, z * (1 + 0.5 * o),
                                            perm, grad3)
                    amp *= 0.5
                out[i, j] = total


def _px(a) -> np.ndarray:
    return np.ascontiguousarray(a, np.float32)


def display_chain(px, ops: list[tuple[int, np.ndarray]], linear: bool) -> np.ndarray:
    """from_display(op_n(... display_rgb(px))) for consecutive display-referred operations."""
    from . import gpu, raster
    px = _px(px)
    minv, has_m = _matrix(raster._WM_INV)
    m, _ = _matrix(raster._WM)
    codes = np.array([o for o, _ in ops], np.int64)
    params = np.zeros((len(ops), 9), np.float32)
    for i, (_, q) in enumerate(ops):
        params[i, :len(q)] = q
    if gpu.worth(px):
        return gpu.display_chain(px, codes, params, linear, minv, m, has_m)
    _threads()
    out = np.empty_like(px)
    _display_chain(px, out, codes, params, linear, minv, m, has_m)
    return out


def exposure(px, k: float) -> np.ndarray:
    from . import gpu
    if gpu.worth(px):
        return gpu.exposure(_px(px), k)
    _threads()
    px = _px(px)
    out = np.empty_like(px)
    _exposure(px, out, np.float32(k))
    return out


def grain(px, field, k: float, response: float, strength) -> np.ndarray:
    from . import gpu
    if gpu.worth(px):
        return gpu.grain(_px(px), field, k, response, strength)
    _threads()
    px = _px(px)
    out = np.empty_like(px)
    _grain(px, _px(field), out, np.float32(k), np.float32(response), _px(strength))
    return out


def vignette(px, wmap, color) -> np.ndarray:
    from . import gpu
    if gpu.worth(px):
        return gpu.vignette(_px(px), wmap, color)
    _threads()
    px = _px(px)
    out = np.empty_like(px)
    _vignette(px, _px(wmap), out, _px(color))
    return out


def lighting(px, height, lights: list, intensity: float) -> np.ndarray:
    """lights: (kind, direction, rgb, visibility) with kind 0 ambient (rgb only) or 1 directional."""
    _threads()
    px, height = _px(px), _px(height)
    n = len(lights)
    kinds = np.array([k for k, *_ in lights], np.int64)
    dirs = np.zeros((n, 3), np.float64)
    cols = np.zeros((n, 3), np.float32)
    has_vis = np.zeros(n, np.bool_)
    vis = np.ones((n,) + height.shape if any(v is not None for *_, v in lights) else (n, 1, 1), np.float32)
    for i, (k, d, c, v) in enumerate(lights):
        cols[i] = c
        if d is not None:
            dirs[i] = d
        if v is not None:
            vis[i], has_vis[i] = v, True
    out = np.empty_like(px)
    _lighting(px, height, out, kinds, dirs, cols, vis, has_vis, np.float32(intensity))
    return out


def mix(before, after, opacity: float) -> np.ndarray:
    from . import gpu
    if gpu.worth(before):
        return gpu.mix(_px(before), after, opacity)
    _threads()
    before = _px(before)
    out = np.empty_like(before)
    _mix(before, _px(after), out, np.float32(opacity))
    return out


def to_rgb8(px, linear: bool, background=None) -> np.ndarray:
    from . import gpu
    if gpu.worth(px):
        return gpu.to_rgb8(_px(px), linear, background)
    _threads()
    px = _px(px)
    out = np.empty(px.shape[:2] + (3,), np.uint8)
    bg = np.zeros(3, np.float32) if background is None else np.asarray(background[:3], np.float32)
    _to_rgb8(px, out, linear, background is not None, bg)
    return out


def warp_affine(src: np.ndarray, hi: np.ndarray, rect) -> np.ndarray:
    """(rect h, rect w, 4) float32: src resampled through the inverse affine map hi (frame -> src pixels)."""
    _threads()
    x0, y0, x1, y1 = rect
    out = np.empty((y1 - y0, x1 - x0, 4), np.float32)
    hi = np.ascontiguousarray(hi, np.float64)
    if hi[0, 1] == 0 and hi[1, 0] == 0:
        ix, fx, iy, fy = _axis_coords(hi, x1 - x0, y1 - y0, (x0, y0))
        _warp_affine_axis(_px(src), ix, fx, iy, fy, out)
        return out
    _warp_affine(_px(src), hi, int(x0), int(y0), out)
    return out


def _axis_coords(hi, ow: int, oh: int, origin):
    """Per-column and per-row (integer, fraction) source coordinates of an axis-aligned map."""
    sx = hi[0, 0] * (np.arange(ow, dtype=np.float64) + origin[0] + 0.5) + hi[0, 2] - 0.5
    sy = hi[1, 1] * (np.arange(oh, dtype=np.float64) + origin[1] + 0.5) + hi[1, 2] - 0.5
    ix, iy = np.floor(sx), np.floor(sy)
    return ix.astype(np.int64), (sx - ix).astype(np.float32), iy.astype(np.int64), (sy - iy).astype(np.float32)


def warp_over(dst: np.ndarray, src: np.ndarray, hi: np.ndarray, origin) -> None:
    """dst (a view of the frame at origin) = src warped through hi (frame -> src pixels) over dst."""
    _threads()
    hi = np.ascontiguousarray(hi, np.float64)
    if hi[0, 1] == 0 and hi[1, 0] == 0:
        # Axis-aligned: the source column depends on x only and the row on y only (same arithmetic).
        _warp_over_axis(dst, _px(src), *_axis_coords(hi, dst.shape[1], dst.shape[0], origin))
        return
    _warp_over(dst, _px(src), hi, int(origin[0]), int(origin[1]))


TILE = 32


def occupied_tiles(px: np.ndarray):
    """(tile rows, tile cols) of the TILE x TILE tiles of px holding any non-zero value, or None when
    most tiles are occupied (a plain source-over is then as cheap)."""
    h, w = px.shape[:2]
    th, tw = -(-h // TILE), -(-w // TILE)
    nz = np.zeros((th * TILE, tw * TILE), bool)
    nz[:h, :w] = (px != 0).any(-1)
    occ = nz.reshape(th, TILE, tw, TILE).any((1, 3))
    if occ.mean() > 0.7:
        return None
    ty, tx = np.nonzero(occ)
    return ty.astype(np.int64), tx.astype(np.int64)


def over_tiles(d: np.ndarray, s: np.ndarray, tiles) -> None:
    """In-place source-over of s onto d over the occupied tiles only (see occupied_tiles)."""
    _threads()
    _over_tiles(d, s, tiles[0], tiles[1], TILE)


def under(d: np.ndarray, s: np.ndarray) -> None:
    """In-place destination-over of float32 (h, w, 4) views that do not overlap."""
    _threads()
    _under(d, s)


def over(d: np.ndarray, s: np.ndarray) -> None:
    """In-place source-over of float32 (h, w, 4) views that do not overlap."""
    _threads()
    _over(d, s)


def noise_octave(out, t0, t1, jx0, jx1, fxs, jy0, jy1, fys, f: float, weight: float, turbulent: bool,
                 shaping: int) -> None:
    _threads()
    _noise_octave(out, t0, t1, jx0, jx1, fxs, jy0, jy1, fys, float(f), float(weight), bool(turbulent), int(shaping))


def dither(pixels, tile, top: int, left: int) -> np.ndarray:
    """(h, w, 4) BGRA8 whose rows are ARGB32-stride aligned (width * 4 bytes)."""
    _threads()
    out = np.empty(pixels.shape[:2] + (4,), np.uint8)
    _dither(pixels, np.ascontiguousarray(tile, np.float32), int(top), int(left), out)
    return out


def bgra_to_linear(raw, table, out) -> None:
    _threads()
    _bgra_lin(raw, table, out)


def fbm(x, y, z: float, perm, grad3, octaves: int) -> np.ndarray:
    """The octave sum of assets.generator.fbm (before its normalization)."""
    _threads()
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    out = np.empty(x.shape, np.float64)
    _fbm(x, y, float(z), np.ascontiguousarray(perm, np.int64), np.ascontiguousarray(grad3, np.float32),
         max(1, int(octaves)), out)
    return out
