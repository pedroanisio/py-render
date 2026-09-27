"""Image-space helpers for the 3D camera: bokeh depth of field and bilinear resampling.

Bokeh: a scatter blur of a premultiplied tile. Every pixel spreads its colour over its circle of
confusion (radius in pixels, capped at 128) with an aperture-shaped kernel: a disc for fewer than 3
blades, otherwise a regular polygon with `blades` sides (one vertex up), anti-aliased over one pixel
and normalised to unit energy. The GPU path evaluates this exactly as a gather over the maximum CoC;
without GL the CPU path splits the CoC linearly between fixed radius levels and FFT-convolves each
level (the same kernels, energy preserving). Both are deterministic.
"""
from __future__ import annotations

import math

import numpy as np

LEVELS = (0.0, 1.0, 2.0, 3.0, 4.5, 6.5, 9.0, 12.5, 17.0, 23.0, 31.0, 42.0, 56.0, 75.0, 100.0, 133.0, 177.0, 236.0)


def aperture_kernel(r: float, blades: int, rotation: float = 0.0) -> np.ndarray:
    """Normalised aperture kernel of circumradius r px (4x4 supersampled edges)."""
    n = int(math.ceil(r)) + 1
    ss = 4
    g = (np.arange(-(n) * ss, (n + 1) * ss) + 0.5) / ss - 0.5 / ss
    X, Y = np.meshgrid(g, g)
    if blades >= 3:
        ang = np.arctan2(X, -Y) - rotation            # 0 = straight up
        sector = 2 * math.pi / blades
        a = np.mod(ang, sector) - sector / 2
        apothem = r * math.cos(math.pi / blades)
        inside = np.hypot(X, Y) * np.cos(a) <= apothem
    else:
        inside = X * X + Y * Y <= r * r
    k = inside.reshape(2 * n + 1, ss, 2 * n + 1, ss).mean((1, 3)).astype(np.float64)
    s = k.sum()
    if s <= 0:
        k[n, n] = 1.0
        s = 1.0
    return k / s


def _fft_conv(img: np.ndarray, k: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    kh, kw = k.shape
    H, W = h + kh, w + kw
    F = np.fft.rfft2(img, s=(H, W), axes=(0, 1))
    K = np.fft.rfft2(k, s=(H, W))
    out = np.fft.irfft2(F * K[..., None], s=(H, W), axes=(0, 1))
    oy, ox = kh // 2, kw // 2
    return out[oy:oy + h, ox:ox + w]


def bokeh_blur(px: np.ndarray, coc: np.ndarray, blades: int = 0, rotation: float = 0.0) -> np.ndarray:
    """Scatter blur of premultiplied px (h, w, 4) by per-pixel CoC radius (h, w) in pixels: on the GPU
    (exact per-pixel scatter-as-gather) when available, else the FFT level approximation."""
    try:
        return bokeh_gpu(px, coc, blades, rotation)
    except Exception:  # noqa: BLE001 — no GL: CPU path
        return bokeh_fft(px, coc, blades, rotation)


def bokeh_fft(px: np.ndarray, coc: np.ndarray, blades: int = 0, rotation: float = 0.0) -> np.ndarray:
    """CPU bokeh: CoC split linearly between fixed radius levels, each level FFT-convolved."""
    c = np.clip(np.asarray(coc, np.float64), 0.0, LEVELS[-1])
    lv = np.asarray(LEVELS)
    i = np.clip(np.searchsorted(lv, c, side="right") - 1, 0, len(lv) - 2)
    t = (c - lv[i]) / (lv[i + 1] - lv[i])
    out = np.zeros(px.shape, np.float64)
    src = px.astype(np.float64)
    for j in range(len(lv)):
        wgt = np.where(i == j, 1 - t, 0.0) + np.where(i + 1 == j, t, 0.0)
        if not wgt.any():
            continue
        layer = src * wgt[..., None]
        if lv[j] < 0.5:
            out += layer
        else:
            out += _fft_conv(layer, aperture_kernel(lv[j], blades, rotation))
    return np.maximum(out, 0).astype(np.float32)


def sample_bilinear(px: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """Bilinear lookup of px (h, w, C) at pixel-centre coordinates (sx, sy); outside -> 0."""
    h, w = px.shape[:2]
    ix, iy = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    fx, fy = (sx - ix).astype(np.float32), (sy - iy).astype(np.float32)
    out = np.zeros(sx.shape + px.shape[2:], np.float32)
    for dx, dy, wt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        jx, jy = ix + dx, iy + dy
        ok = (jx >= 0) & (jx < w) & (jy >= 0) & (jy < h)
        v = px[np.clip(jy, 0, h - 1), np.clip(jx, 0, w - 1)]
        out += v * (wt * ok)[..., None]
    return out


BOKEH_FS = """
#version 410
#define PI 3.14159265358979
uniform sampler2D t_col; uniform sampler2D t_coc; uniform sampler2D t_norm;
uniform int u_R; uniform int u_blades; uniform float u_rot; uniform float u_normMax;
out vec4 o;
float polyDist(vec2 d) {
    if (u_blades < 3) return length(d);
    float sector = 2.0 * PI / float(u_blades);
    float ang = atan(d.x, d.y) - u_rot;
    float a = mod(ang, sector) - sector * 0.5;
    return length(d) * cos(a) / cos(PI / float(u_blades));
}
float norm(float r) {
    if (r <= u_normMax) return texture(t_norm, vec2(r / u_normMax, 0.5)).r;
    return u_blades < 3 ? PI * r * r : 0.5 * float(u_blades) * r * r * sin(2.0 * PI / float(u_blades));
}
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    ivec2 sz = textureSize(t_col, 0);
    vec4 acc = vec4(0.0);
    for (int y = -u_R; y <= u_R; ++y) {
        for (int x = -u_R; x <= u_R; ++x) {
            ivec2 q = p + ivec2(x, y);
            if (q.x < 0 || q.y < 0 || q.x >= sz.x || q.y >= sz.y) continue;
            float r = texelFetch(t_coc, q, 0).r;
            if (r < 0.5) { if (x == 0 && y == 0) acc += texelFetch(t_col, q, 0); continue; }
            float w = clamp(r - polyDist(vec2(-x, -y)) + 0.5, 0.0, 1.0);
            if (w > 0.0) acc += texelFetch(t_col, q, 0) * (w / norm(r));
        }
    }
    o = acc;
}
"""
_NORM_MAX = 16.0
_NORM: dict = {}
_PROG: dict = {}


def _norm_table(blades: int, rotation: float, n: int = 256) -> np.ndarray:
    """Sum of the anti-aliased kernel weights for radii 0.._NORM_MAX (exact normalisation)."""
    key = (blades if blades >= 3 else 0, round(rotation, 6))
    hit = _NORM.get(key)
    if hit is None:
        R = int(_NORM_MAX) + 2
        g = np.arange(-R, R + 1, dtype=np.float64)
        X, Y = np.meshgrid(g, g)
        d = np.hypot(X, Y)
        if blades >= 3:
            sector = 2 * math.pi / blades
            a = np.mod(np.arctan2(X, Y) - rotation, sector) - sector / 2
            d = d * np.cos(a) / math.cos(math.pi / blades)
        rs = np.linspace(0, _NORM_MAX, n)
        hit = np.array([max(np.clip(r - d + 0.5, 0, 1).sum(), 1.0) if r >= 0.5 else 1.0 for r in rs], np.float32)
        _NORM[key] = hit
    return hit


def bokeh_gpu(px: np.ndarray, coc: np.ndarray, blades: int = 0, rotation: float = 0.0) -> np.ndarray:
    import moderngl
    from .. import gl
    ctx = gl.context()
    if ctx.version_code < 410:
        raise gl.GLUnavailable("GL 4.1 needed")
    hit = _PROG.get(id(ctx))
    if hit is None:
        prog = ctx.program(vertex_shader=gl.FULLSCREEN_VS, fragment_shader=BOKEH_FS)
        hit = _PROG[id(ctx)] = (prog, gl.fullscreen_quad(prog))
    prog, vao = hit
    c = np.clip(np.asarray(coc, np.float32), 0, 128.0)
    h, w = c.shape
    R = int(math.ceil(float(c.max()))) + 1
    tc = ctx.texture((w, h), 4, np.ascontiguousarray(px[::-1], np.float32).tobytes(), dtype="f4")
    tr = ctx.texture((w, h), 1, np.ascontiguousarray(c[::-1], np.float32).tobytes(), dtype="f4")
    nt = _norm_table(blades, rotation)
    tn = ctx.texture((len(nt), 1), 1, nt.tobytes(), dtype="f4")
    tn.filter = (moderngl.LINEAR, moderngl.LINEAR)
    tn.repeat_x = tn.repeat_y = False
    out = ctx.texture((w, h), 4, dtype="f4")
    fbo = ctx.framebuffer([out])
    try:
        fbo.use()
        ctx.viewport = (0, 0, w, h)
        ctx.disable(moderngl.BLEND)
        ctx.disable(moderngl.DEPTH_TEST)
        tc.use(0)
        tr.use(1)
        tn.use(2)
        prog["t_col"].value, prog["t_coc"].value, prog["t_norm"].value = 0, 1, 2
        prog["u_R"].value = R
        prog["u_blades"].value = int(blades)
        prog["u_rot"].value = float(rotation)
        prog["u_normMax"].value = _NORM_MAX
        vao.render(moderngl.TRIANGLE_STRIP)
        res = np.frombuffer(out.read(), np.float32).reshape(h, w, 4)[::-1]
    finally:
        for o in (tc, tr, tn, out, fbo):
            o.release()
    return np.ascontiguousarray(np.maximum(res, 0))
