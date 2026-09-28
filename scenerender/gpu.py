"""CUDA versions (CuPy) of the whole-frame kernels, used when an NVIDIA GPU is available.

The kernels mirror scenerender.kernels operation for operation in float32/float64 and are compiled
without fused multiply-add, so arithmetic rounds exactly as on the CPU; the sRGB curves read the same
tables. Effects keep their NumPy tiles: each call uploads its input, runs one kernel pass and
downloads the result (PCIe transfers cost a few milliseconds per 1080p RGBA float frame, the CPU
passes they replace tens to hundreds).

Used automatically for tiles of at least MIN_PIXELS; SCENERENDER_GPU=0 (render --no-gpu) keeps
everything on the CPU.
"""
from __future__ import annotations

import os

import numpy as np

MIN_PIXELS = 1 << 18          # smaller tiles are not worth a round trip
_CP = None                    # cupy module, False when unavailable
_MOD = None
_CONST: dict = {}

_SRC = r"""
#define LUT_N 65536
#define Q8_BINS 4096

__device__ __forceinline__ float lerp_lut(const float* t, float x) {
    float s = x * (float)LUT_N;
    int i = (int)s;
    return t[i] + (t[i + 1] - t[i]) * (s - (float)i);
}
__device__ __forceinline__ float l2s(const float* lut, float x) {
    if (x < 0.0f) x = 0.0f;
    if (x <= 0.0031308f) return x * 12.92f;
    if (x <= 1.0f) return lerp_lut(lut, x);
    return 1.055f * powf(x, (float)(1.0 / 2.4)) - 0.055f;
}
__device__ __forceinline__ float s2l(const float* lut, float x) {
    if (x < 0.0f) x = 0.0f;
    if (x <= 0.04045f) return x / 12.92f;
    if (x <= 1.0f) return lerp_lut(lut, x);
    return powf((x + 0.055f) / 1.055f, 2.4f);
}
__device__ __forceinline__ float clip01(float x) { return x < 0.0f ? 0.0f : (x > 1.0f ? 1.0f : x); }
__device__ __forceinline__ void mul3(const float* m, float& r, float& g, float& b) {
    float a = m[0] * r + m[1] * g + m[2] * b, c = m[3] * r + m[4] * g + m[5] * b, d = m[6] * r + m[7] * g + m[8] * b;
    r = a; g = c; b = d;
}

extern "C" __global__ void display_chain(const float* px, float* out, long n, const long* ops, const float* q,
                                         int nops, int linear, const float* minv, const float* m, int has_m,
                                         const float* l2s_t, const float* s2l_t) {
    long i = blockIdx.x * (long)blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float* p = px + 4 * i;
    float a = p[3], r, g, b;
    if (a > 1e-6f) { r = p[0] / a; g = p[1] / a; b = p[2] / a; } else { r = g = b = 0.0f; }
    if (linear) {
        if (has_m) mul3(minv, r, g, b);
        r = l2s(l2s_t, r); g = l2s(l2s_t, g); b = l2s(l2s_t, b);
    }
    for (int k = 0; k < nops; k++) {
        const float* c = q + 9 * k;
        if (ops[k] == 0) {          // lift-gamma-gain
            r = fmaxf(r + c[0] * (1.0f - r), 0.0f); g = fmaxf(g + c[1] * (1.0f - g), 0.0f); b = fmaxf(b + c[2] * (1.0f - b), 0.0f);
            r = (c[3] == 1.0f ? r : powf(r, c[3])) * c[6];
            g = (c[4] == 1.0f ? g : powf(g, c[4])) * c[7];
            b = (c[5] == 1.0f ? b : powf(b, c[5])) * c[8];
        } else {                    // colour grade
            float y = 0.2126f * r + 0.7152f * g + 0.0722f * b;
            r = y + (r - y) * c[0]; g = y + (g - y) * c[0]; b = y + (b - y) * c[0];
            r = (r - 0.5f) * c[1] + 0.5f + c[2]; g = (g - 0.5f) * c[1] + 0.5f + c[2]; b = (b - 0.5f) * c[1] + 0.5f + c[2];
        }
        r = clip01(r); g = clip01(g); b = clip01(b); a = clip01(a);
        if (!(a > 1e-6f)) r = g = b = 0.0f;
    }
    if (linear) {
        r = s2l(s2l_t, r); g = s2l(s2l_t, g); b = s2l(s2l_t, b);
        if (has_m) mul3(m, r, g, b);
    }
    float* o = out + 4 * i;
    o[0] = r * a; o[1] = g * a; o[2] = b * a; o[3] = a;
}

extern "C" __global__ void exposure(const float* px, float* out, long n, float k) {
    long i = blockIdx.x * (long)blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float* p = px + 4 * i; float* o = out + 4 * i;
    float a = p[3];
    for (int c = 0; c < 3; c++) o[c] = a > 1e-6f ? (p[c] / a * k) * a : 0.0f;
    o[3] = a;
}

extern "C" __global__ void grain(const float* px, const float* field, float* out, long n, float k, float response,
                                 const float* strength) {
    long i = blockIdx.x * (long)blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float* p = px + 4 * i; const float* gr = field + 3 * i; float* o = out + 4 * i;
    float a = p[3];
    for (int c = 0; c < 3; c++) {
        float s = a > 1e-6f ? p[c] / a : 0.0f;
        float v = clip01(s);
        float wt = fmaxf(4.0f * v * (1.0f - v), 0.0f);
        wt = response == 0.5f ? sqrtf(wt) : powf(wt, response);
        o[c] = fmaxf(s + gr[c] * k * wt * strength[c], 0.0f) * a;
    }
    o[3] = a;
}

extern "C" __global__ void vignette(const float* px, const float* wmap, float* out, long n, const float* col) {
    long i = blockIdx.x * (long)blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float* p = px + 4 * i; float* o = out + 4 * i;
    float a = p[3], f = wmap[i] * col[3];
    for (int c = 0; c < 3; c++) {
        float s = a > 1e-6f ? p[c] / a : 0.0f;
        o[c] = (s + (col[c] - s) * f) * a;
    }
    o[3] = a;
}

extern "C" __global__ void mix(const float* before, const float* after, float* out, long n, float opacity) {
    long i = blockIdx.x * (long)blockDim.x + threadIdx.x;
    if (i >= n) return;
    float b = before[i];
    out[i] = b + (after[i] - b) * opacity;
}

__device__ __forceinline__ unsigned char code8(const float* start, const unsigned char* base, float x) {
    if (!(x > 0.0f)) return 0;
    if (x >= 1.0f) return 255;
    int k = base[(int)(x * (float)Q8_BINS)];
    while (x >= start[k + 1]) k++;
    return (unsigned char)k;
}

extern "C" __global__ void to_rgb8(const float* px, unsigned char* out, long n, int linear, int has_bg,
                                   const float* bg, const float* start, const unsigned char* base) {
    long i = blockIdx.x * (long)blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float* p = px + 4 * i; unsigned char* o = out + 3 * i;
    float a = p[3], r = p[0], g = p[1], b = p[2];
    if (has_bg) { r = r + bg[0] * (1.0f - a); g = g + bg[1] * (1.0f - a); b = b + bg[2] * (1.0f - a); a = 1.0f; }
    if (a > 1e-6f) { float d = fmaxf(a, 1e-6f); r = r / d; g = g / d; b = b / d; } else { r = g = b = 0.0f; }
    if (linear) { o[0] = code8(start, base, r); o[1] = code8(start, base, g); o[2] = code8(start, base, b); }
    else {
        o[0] = (unsigned char)(clip01(r) * 255.0f + 0.5f);
        o[1] = (unsigned char)(clip01(g) * 255.0f + 0.5f);
        o[2] = (unsigned char)(clip01(b) * 255.0f + 0.5f);
    }
}

// One fractal octave from its two phase lattices (kernels._noise_octave).
extern "C" __global__ void noise_octave(float* out, const float* t0, const float* t1, int tw,
                                        const long* jx0, const long* jx1, const float* fxs,
                                        const long* jy0, const long* jy1, const float* fys, int h, int w,
                                        double f, double weight, int turbulent, int shaping) {
    long i = blockIdx.x * (long)blockDim.x + threadIdx.x;
    if (i >= (long)h * w) return;
    int y = i / w, x = i % w;
    long a0 = jy0[y] * tw, a1 = jy1[y] * tw, b0 = jx0[x], b1 = jx1[x];
    float fx = fxs[x], fy = fys[y];
    float a = t0[a0 + b0] * (1.0f - fx) + t0[a0 + b1] * fx;
    float b = t0[a1 + b0] * (1.0f - fx) + t0[a1 + b1] * fx;
    float z0 = a * (1.0f - fy) + b * fy;
    a = t1[a0 + b0] * (1.0f - fx) + t1[a0 + b1] * fx;
    b = t1[a1 + b0] * (1.0f - fx) + t1[a1 + b1] * fx;
    float z1 = a * (1.0f - fy) + b * fy;
    double z = (double)z0 * (1.0 - f) + (double)z1 * f;
    if (turbulent) z = fabs(2 * z - 1);
    if (shaping == 1) z = z * z;
    else if (shaping == 2) z = z * z * (3 - 2 * z);
    else if (shaping == 3) z = fmin(fmax(z * 3 - .5, 0.0), 1.0);
    else if (shaping == 4) z = pow(1 - z, 6.0);
    out[i] = (float)((double)out[i] + z * weight);
}

// _noise_warp's displacement and effects.sample (transparent edges) for the heat/horizontal/vertical/
// twist modes: fields are the two normalized fractals.
extern "C" __global__ void noise_warp(const float* px, const float* f1, const float* f2, float* out, int h, int w,
                                      float amount, int mode, float vertical, int heat, float falloff) {
    long i = blockIdx.x * (long)blockDim.x + threadIdx.x;
    if (i >= (long)h * w) return;
    int y = i / w, x = i % w;
    float dx = (f1[i] * 2.0f - 1.0f) * amount, dy = (f2[i] * 2.0f - 1.0f) * amount;
    if (mode == 1 || heat) dy = dy * vertical;          // horizontal (and heat haze)
    else if (mode == 2) dx = dx * 0.0f;                 // vertical
    else if (mode == 3) { float t = dx; dx = -dy; dy = t; }   // twist
    if (heat) {
        float e = clip01(((float)y + 0.5f) / (float)(h > 1 ? h : 1));
        if (falloff != 1.0f) e = powf(e, falloff);
        dx = dx * e; dy = dy * e;
    }
    float sx = (float)x + dx, sy = (float)y + dy;
    float flx = floorf(sx), fly = floorf(sy);
    int x0 = (int)flx, y0 = (int)fly;
    float fx = sx - flx, fy = sy - fly;
    float acc[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    const int ddx[4] = {0, 1, 0, 1}, ddy[4] = {0, 0, 1, 1};
    for (int t = 0; t < 4; t++) {
        float wgt = (ddx[t] ? fx : 1.0f - fx) * (ddy[t] ? fy : 1.0f - fy);
        int cx = x0 + ddx[t], cy = y0 + ddy[t];
        int in = cx >= 0 && cx < w && cy >= 0 && cy < h;
        wgt = wgt * (in ? 1.0f : 0.0f);
        int kx = cx < 0 ? 0 : (cx > w - 1 ? w - 1 : cx), ky = cy < 0 ? 0 : (cy > h - 1 ? h - 1 : cy);
        const float* v = px + 4 * ((long)ky * w + kx);
        for (int c = 0; c < 4; c++) acc[c] += v[c] * wgt;
    }
    float* o = out + 4 * i;
    for (int c = 0; c < 4; c++) o[c] = acc[c];
}
"""


def enabled() -> bool:
    """A CUDA device is usable and GPU use is not switched off."""
    global _CP
    if os.environ.get("SCENERENDER_GPU", "1") == "0":
        return False
    if _CP is None:
        try:
            import cupy
            _CP = cupy if cupy.cuda.runtime.getDeviceCount() > 0 else False
        except Exception:  # noqa: BLE001 — no CuPy, driver or device: CPU paths
            _CP = False
    return bool(_CP)


def worth(px) -> bool:
    return px.shape[0] * px.shape[1] >= MIN_PIXELS and enabled()


def _module():
    global _MOD
    if _MOD is None:
        _MOD = _CP.RawModule(code=_SRC, options=("--fmad=false", "-std=c++14"))
    return _MOD


def _const(name, make):
    """Device copies of fixed tables (uploaded once per process)."""
    v = _CONST.get(name)
    if v is None:
        v = _CONST[name] = _CP.asarray(make())
    return v


def _run(name, n, *args):
    block = 256
    _module().get_function(name)(((int(n) + block - 1) // block,), (block,), args)


def _up(a, dtype=np.float32):
    return _CP.asarray(np.ascontiguousarray(a, dtype))


def _down(d) -> np.ndarray:
    """The device array's contents, waiting for its kernels by sleeping: a plain copy would spin a
    CPU core while the GPU works (and frame workers share the CPUs, the GPU possibly other jobs)."""
    done = _CP.cuda.Event(block=True, disable_timing=True)
    done.record()
    done.synchronize()
    return d.get()


def display_chain(px, codes, params, linear, minv, m, has_m) -> np.ndarray:
    from . import kernels
    d = _up(px)
    out = _CP.empty_like(d)
    n = px.shape[0] * px.shape[1]
    _run("display_chain", n, d, out, np.int64(n), _up(codes, np.int64), _up(params), np.int32(len(codes)),
         np.int32(bool(linear)), _up(minv), _up(m), np.int32(bool(has_m)),
         _const("l2s", lambda: kernels._L2S), _const("s2l", lambda: kernels._S2L))
    return _down(out)


def exposure(px, k) -> np.ndarray:
    d = _up(px)
    out = _CP.empty_like(d)
    n = px.shape[0] * px.shape[1]
    _run("exposure", n, d, out, np.int64(n), np.float32(k))
    return _down(out)


def grain(px, field, k, response, strength) -> np.ndarray:
    d = _up(px)
    out = _CP.empty_like(d)
    n = px.shape[0] * px.shape[1]
    _run("grain", n, d, _up(field), out, np.int64(n), np.float32(k), np.float32(response), _up(strength))
    return _down(out)


def vignette(px, wmap, color) -> np.ndarray:
    d = _up(px)
    out = _CP.empty_like(d)
    n = px.shape[0] * px.shape[1]
    _run("vignette", n, d, _up(wmap), out, np.int64(n), _up(color))
    return _down(out)


def mix(before, after, opacity) -> np.ndarray:
    b = _up(before)
    out = _CP.empty_like(b)
    _run("mix", b.size, b, _up(after), out, np.int64(b.size), np.float32(opacity))
    return _down(out)


def to_rgb8(px, linear, background) -> np.ndarray:
    from . import kernels
    d = _up(px)
    n = px.shape[0] * px.shape[1]
    out = _CP.empty(px.shape[:2] + (3,), np.uint8)
    bg = np.zeros(3, np.float32) if background is None else np.asarray(background[:3], np.float32)
    _run("to_rgb8", n, d, out, np.int64(n), np.int32(bool(linear)), np.int32(background is not None), _up(bg),
         _const("q8start", lambda: kernels._Q8_START), _const("q8base", lambda: kernels._Q8_BASE))
    return _down(out)


class FractalField:
    """A fractal accumulated on the GPU from lattice tables the CPU evaluates (see fields.fractal_grid)."""

    def __init__(self, h, w):
        self.h, self.w = h, w
        self.out = _CP.zeros((h, w), np.float32)

    def octave(self, t0, t1, jx0, jx1, fxs, jy0, jy1, fys, f, weight, turbulent, shaping):
        n = self.h * self.w
        _run("noise_octave", n, self.out, _up(t0), _up(t1), np.int32(t0.shape[1]), _up(jx0, np.int64),
             _up(jx1, np.int64), _up(fxs), _up(jy0, np.int64), _up(jy1, np.int64), _up(fys), np.int32(self.h),
             np.int32(self.w), np.float64(f), np.float64(weight), np.int32(bool(turbulent)), np.int32(shaping))

    def normalized(self, total):
        return self.out / np.float32(max(total, 1e-7))


def noise_warp(px, f1, f2, amount, mode, vertical, heat, falloff) -> np.ndarray:
    """_noise_warp's displacement and bilinear transparent-edge sampling; f1/f2 are device fields."""
    h, w = px.shape[:2]
    d = _up(px)
    out = _CP.empty_like(d)
    _run("noise_warp", h * w, d, f1, f2, out, np.int32(h), np.int32(w), np.float32(amount), np.int32(mode),
         np.float32(vertical), np.int32(bool(heat)), np.float32(falloff))
    return _down(out)
