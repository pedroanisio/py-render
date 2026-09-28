"""The bloom effect (effects.light._bloom, linear pipeline, no halation) as GL passes.

Same arithmetic as the NumPy reference, pass for pass: bright pass on straight-alpha luma, the
separable Gaussian of effects._blur_axis (three zero-edge box filters of the same odd width per axis
above sigma 4, the sampled kernel below; x first, then y), then source + tinted halo with the same
alpha rule. Pixels go up once and come back once; everything in between stays on the GPU.
"""
from __future__ import annotations

import math

import numpy as np

from .. import gl

_BRIGHT_FS = """
#version 330
uniform sampler2D src;
uniform float thr;
out vec4 o;
void main() {
    vec4 p = texelFetch(src, ivec2(gl_FragCoord.xy), 0);
    vec3 rgb = p.a > 1e-6 ? p.rgb / p.a : vec3(0.0);
    float l = 0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b;
    o = p * (max(l - thr, 0.0) / max(l, 1e-7));
}
"""

_FILTER_FS = """
#version 330
uniform sampler2D src;
uniform ivec2 dir;
uniform int radius;
uniform int box;            // 1: mean of 2 * radius + 1 taps; 0: weights k[]
uniform float k[64];
out vec4 o;
void main() {
    ivec2 q = ivec2(gl_FragCoord.xy);
    ivec2 size = textureSize(src, 0);
    vec4 acc = vec4(0.0);
    for (int i = -radius; i <= radius; ++i) {
        ivec2 t = q + dir * i;
        if (t.x >= 0 && t.y >= 0 && t.x < size.x && t.y < size.y)
            acc += texelFetch(src, t, 0) * (box == 1 ? 1.0 : k[i + radius]);
    }
    o = box == 1 ? acc / float(2 * radius + 1) : acc;
}
"""

_FINAL_FS = """
#version 330
uniform sampler2D src;
uniform sampler2D halo;
uniform vec4 tint;
uniform float intensity;
out vec4 o;
void main() {
    ivec2 q = ivec2(gl_FragCoord.xy);
    vec4 s = texelFetch(src, q, 0);
    vec4 h = texelFetch(halo, q, 0) * intensity;
    h.rgb *= tint.rgb;
    h *= tint.a;
    vec4 px = s + h;
    px.a = clamp(s.a + h.a * (1.0 - s.a), 0.0, 1.0);
    o = px;
}
"""

_PROGRAMS: dict = {}


def _program(name, fs):
    hit = _PROGRAMS.get(name)
    if hit is None:
        ctx = gl.context()
        prog = ctx.program(vertex_shader=gl.FULLSCREEN_VS, fragment_shader=fs)
        hit = _PROGRAMS[name] = (prog, gl.fullscreen_quad(prog))
    return hit


def _passes(sigma: float):
    """[(radius, box, weights)] per axis, as effects._blur_axis applies them."""
    from . import _kernel
    if sigma < 0.3:
        return []
    if sigma > 4:
        w = int(math.sqrt(12 * sigma * sigma / 3 + 1))
        w += (w + 1) % 2
        return [(w // 2, 1, None)] * 3
    k = _kernel(sigma)
    if len(k) > 64:
        return None
    return [(len(k) // 2, 0, k)]


def bloom(px: np.ndarray, sigma: float, threshold: float, intensity: float, tint) -> np.ndarray | None:
    """Bloomed copy of premultiplied linear px (h, w, 4), or None when the GL path cannot run it."""
    import moderngl
    passes = _passes(sigma)
    if passes is None:
        return None
    try:
        ctx = gl.context()
    except gl.GLUnavailable:
        return None
    h, w = px.shape[:2]
    if max(w, h) > int(ctx.info.get("GL_MAX_TEXTURE_SIZE", 16384)):
        return None
    owned = []

    def target():
        t = ctx.texture((w, h), 4, dtype="f4")
        t.filter = (moderngl.NEAREST, moderngl.NEAREST)
        f = ctx.framebuffer([t])
        owned.extend((t, f))
        return t, f

    def run(name, fs, out, textures, **uniforms):
        prog, vao = _program(name, fs)
        out[1].use()
        ctx.viewport = (0, 0, w, h)
        for unit, (uname, tex) in enumerate(textures.items()):
            tex.use(unit)
            prog[uname].value = unit
        for k_, v in uniforms.items():
            if k_ in prog:
                if k_ == "k":
                    prog[k_].write(np.pad(np.asarray(v, np.float32), (0, 64 - len(v))).tobytes())
                else:
                    prog[k_].value = v
        vao.render(moderngl.TRIANGLE_STRIP)

    try:
        ctx.disable(moderngl.BLEND)
        src = ctx.texture((w, h), 4, np.ascontiguousarray(px, np.float32).tobytes(), dtype="f4")
        src.filter = (moderngl.NEAREST, moderngl.NEAREST)
        owned.append(src)
        a, b = target(), target()
        run("bright", _BRIGHT_FS, a, {"src": src}, thr=float(threshold))
        cur, nxt = a, b
        for axis in ((1, 0), (0, 1)):                         # x (numpy axis 1) first, then y
            for radius, box, k in passes:
                run("filter", _FILTER_FS, nxt, {"src": cur[0]}, dir=axis, radius=int(radius), box=int(box),
                    **({"k": k} if k is not None else {}))
                cur, nxt = nxt, cur
        run("final", _FINAL_FS, nxt, {"src": src, "halo": cur[0]}, tint=tuple(float(x) for x in tint),
            intensity=float(intensity))
        data = np.frombuffer(nxt[1].read(components=4, dtype="f4"), np.float32).reshape(h, w, 4)
        return data.copy()
    finally:
        for o in owned:
            o.release()
