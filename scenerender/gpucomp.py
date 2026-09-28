"""GPU compositing (GL): the frame, its source-over composites, cached rasters and the motion-blur
accumulation stay in GPU textures, and a finished frame leaves the GPU once, as 8-bit RGB.

A Buf may hold a GpuTile instead of pixels (see raster.Buf). Reading Buf.px downloads the tile, so
every CPU code path keeps working unchanged; only the compositor's hot paths avoid downloads:
source-over onto the GPU frame (blend.composite), the raster cache (compositor._composite_cached),
shutter accumulation (render._accumulate) and the output conversion (output.render_frame).

Pixel rows map to texture rows directly (array row 0 = texture row 0 = framebuffer row 0), so nothing
is flipped. The arithmetic is float32 as on the CPU: source-over by fixed-function float blending,
warps by explicit bilinear fetches (hardware filtering interpolates with reduced-precision weights),
and the 8-bit output by the CPU kernel's threshold table.

The GPU frame covers exactly the frame rectangle: what the CPU frame would hold outside it is cropped
at the end of the frame anyway, unless an adjustment layer reads it first, so a document with
adjustment layers keeps its frame on the CPU.

SCENERENDER_GPU_COMPOSITE=0 (or SCENERENDER_GPU=0) keeps everything on the CPU.
"""
from __future__ import annotations

import os

import numpy as np

from . import gl

_OK: bool | None = None


def enabled() -> bool:
    global _OK
    if _OK is None:
        _OK = (os.environ.get("SCENERENDER_GPU_COMPOSITE", "1") != "0" and os.environ.get("SCENERENDER_GPU", "1") != "0"
               and gl.available())
    return _OK


# ---------------------------------------------------------------- tiles
_POOL: dict[tuple[int, int], list] = {}
_POOL_KEEP = 6          # free textures kept per size bucket


def _bucket(n: int) -> int:
    return max(128, -(-n // 128) * 128)


class GpuTile:
    """A w x h RGBA float32 tile: the (0, 0, w, h) corner of a pooled texture with its framebuffer."""

    __slots__ = ("tex", "fbo", "w", "h", "key", "shared", "fixed")

    def __init__(self, w: int, h: int, clear: bool = True):
        self.shared = False     # held by a cache: Bufs lending it never release it
        self.fixed = False      # the frame: composites onto it are clipped, never grow it
        self.w, self.h = int(w), int(h)
        self.key = (_bucket(self.w), _bucket(self.h))
        free = _POOL.get(self.key)
        if free:
            self.tex, self.fbo = free.pop()
        else:
            ctx = gl.context()
            self.tex = ctx.texture(self.key, 4, dtype="f4")
            self.fbo = ctx.framebuffer(color_attachments=[self.tex])
        if clear:
            self.fbo.clear(0.0, 0.0, 0.0, 0.0, viewport=(0, 0, self.w, self.h))

    @staticmethod
    def upload(px: np.ndarray) -> "GpuTile":
        h, w = px.shape[:2]
        t = GpuTile(w, h, clear=False)
        t.tex.write(np.ascontiguousarray(px, np.float32), viewport=(0, 0, w, h))
        return t

    def download(self) -> np.ndarray:
        out = np.empty((self.h, self.w, 4), np.float32)
        self.fbo.read_into(out, viewport=(0, 0, self.w, self.h), components=4, dtype="f4")
        return out

    def release(self) -> None:
        if not self.shared:
            self._free()

    def _free(self) -> None:
        if self.tex is None:
            return
        free = _POOL.setdefault(self.key, [])
        if len(free) < _POOL_KEEP:
            free.append((self.tex, self.fbo))
        else:
            self.fbo.release()
            self.tex.release()
        self.tex = self.fbo = None

    def __del__(self):
        try:
            self._free()
        except Exception:  # noqa: BLE001 — interpreter shutdown
            pass


# ---------------------------------------------------------------- programs
_VS = "#version 410\nin vec2 in_pos;\nvoid main() { gl_Position = vec4(in_pos, 0.0, 1.0); }\n"

_FS = {
    # src texel under this pixel, scaled (source-over and additive accumulation use blending)
    "copy": """
uniform sampler2D src; uniform ivec2 off; uniform float k;
out vec4 o;
void main() { o = texelFetch(src, ivec2(gl_FragCoord.xy) - off, 0) * k; }""",
    # src resampled through hi (frame -> src pixels) at pixel centres, texels outside src transparent,
    # as kernels._warp_affine
    "warp": """
uniform sampler2D src; uniform ivec2 org; uniform ivec2 size; uniform dvec3 r0; uniform dvec3 r1;
out vec4 o;
vec4 at(ivec2 p) {
    return (p.x < 0 || p.y < 0 || p.x >= size.x || p.y >= size.y) ? vec4(0.0) : texelFetch(src, p, 0);
}
void main() {
    dvec2 g = dvec2(org) + dvec2(gl_FragCoord.xy);
    double sx = r0.x * g.x + r0.y * g.y + r0.z - 0.5LF;
    double sy = r1.x * g.x + r1.y * g.y + r1.z - 0.5LF;
    double ix = floor(sx), iy = floor(sy);
    float fx = float(sx - ix), fy = float(sy - iy);
    ivec2 i = ivec2(int(ix), int(iy));
    o = at(i) * ((1.0 - fx) * (1.0 - fy)) + at(i + ivec2(1, 0)) * (fx * (1.0 - fy))
      + at(i + ivec2(0, 1)) * ((1.0 - fx) * fy) + at(i + ivec2(1, 1)) * (fx * fy);
}""",
    # a rect of a GL (bottom-up) texture as a top-down tile: row j = texture row y + h - 1 - j
    "flip": """
uniform sampler2D src; uniform ivec2 org; uniform int h;
out vec4 o;
void main() { ivec2 p = ivec2(gl_FragCoord.xy); o = texelFetch(src, ivec2(org.x + p.x, org.y + h - 1 - p.y), 0); }""",
    # (a + b) / d, or a / d without b
    "sum": """
uniform sampler2D a; uniform sampler2D b; uniform int has_b; uniform float d;
out vec4 o;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec4 s = texelFetch(a, p, 0);
    if (has_b != 0) s = s + texelFetch(b, p, 0);
    o = s / d;
}""",
    # max |a - b| over each 16 x 16 block
    "maxdiff": """
uniform sampler2D a; uniform sampler2D b; uniform ivec2 size;
out float o;
void main() {
    ivec2 p0 = ivec2(gl_FragCoord.xy) * 16;
    float m = 0.0;
    for (int y = 0; y < 16; y++) for (int x = 0; x < 16; x++) {
        ivec2 p = p0 + ivec2(x, y);
        if (p.x < size.x && p.y < size.y) {
            vec4 d = abs(texelFetch(a, p, 0) - texelFetch(b, p, 0));
            m = max(m, max(max(d.r, d.g), max(d.b, d.a)));
        }
    }
    o = m;
}""",
    # premultiplied working space -> straight 8-bit RGB over bg, as kernels._to_rgb8. pack3 = 0: one
    # texel per pixel, r | g << 8 | b << 16; pack3 = 1: texel i of a row holds bytes 4i..4i+3 of the
    # row's RGB byte stream (width 3w / 4), so the texture reads back as the (h, w, 3) image itself.
    "rgb8": """
uniform sampler2D src; uniform int linear; uniform int has_bg; uniform vec3 bg; uniform float q8[257];
uniform int pack3;
out uint o;
uint code(float x) {
    if (!(x > 0.0)) return 0u;
    if (x >= 1.0) return 255u;
    int lo = 0, hi = 255;
    while (lo < hi) { int mid = (lo + hi + 1) >> 1; if (q8[mid] <= x) lo = mid; else hi = mid - 1; }
    return uint(lo);
}
uint enc(float x) { return uint(clamp(x, 0.0, 1.0) * 255.0 + 0.5); }
uvec3 rgb(ivec2 at) {
    vec4 p = texelFetch(src, at, 0);
    float a = p.a; vec3 c = p.rgb;
    if (has_bg != 0) { c = c + bg * (1.0 - a); a = 1.0; }
    if (a > 1e-6) c = c / max(a, 1e-6); else c = vec3(0.0);
    return linear != 0 ? uvec3(code(c.r), code(c.g), code(c.b)) : uvec3(enc(c.r), enc(c.g), enc(c.b));
}
void main() {
    ivec2 f = ivec2(gl_FragCoord.xy);
    if (pack3 == 0) { uvec3 q = rgb(f); o = q.r | (q.g << 8) | (q.b << 16); return; }
    int n0 = f.x * 4, p0 = n0 / 3;
    uvec3 a = rgb(ivec2(p0, f.y)), b = rgb(ivec2(p0 + 1, f.y));
    uint bytes[6] = uint[6](a.r, a.g, a.b, b.r, b.g, b.b);
    int s = n0 - p0 * 3;
    o = bytes[s] | (bytes[s + 1] << 8) | (bytes[s + 2] << 16) | (bytes[s + 3] << 24);
}""",
}

_PROGS: dict = {}


def _prog(name: str):
    hit = _PROGS.get(name)
    if hit is None:
        ctx = gl.context()
        prog = ctx.program(vertex_shader=_VS, fragment_shader="#version 410\n" + _FS[name])
        vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], np.float32).tobytes())
        hit = _PROGS[name] = (prog, ctx.vertex_array(prog, [(vbo, "2f", "in_pos")]))
    return hit


def _draw(name: str, fbo, viewport, blend=None, textures=(), **uniforms) -> None:
    import moderngl
    ctx = gl.context()
    prog, vao = _prog(name)
    for unit, (uname, tex) in enumerate(textures):
        tex.use(unit)
        prog[uname].value = unit
    for k, v in uniforms.items():
        prog[k].value = v
    fbo.use()
    ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
    if blend is None:
        ctx.disable(moderngl.BLEND)
    else:
        ctx.enable(moderngl.BLEND)
        ctx.blend_equation = moderngl.FUNC_ADD
        ctx.blend_func = blend
    ctx.viewport = viewport
    ctx.scissor = None
    vao.render(moderngl.TRIANGLE_STRIP)
    if blend is not None:
        ctx.disable(moderngl.BLEND)


def _over():
    import moderngl
    return moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA


def _add():
    import moderngl
    return moderngl.ONE, moderngl.ONE


# ---------------------------------------------------------------- operations on Bufs
def frame(rect) -> "object":
    """A transparent GPU Buf covering rect."""
    from .raster import Buf
    x0, y0, x1, y1 = rect
    t = GpuTile(x1 - x0, y1 - y0)
    t.fixed = True
    return Buf(None, x0, y0, gpu=t)


def shared_upload(px: np.ndarray) -> GpuTile:
    """A tile for a cache to hold: Bufs lending it (lend) never release it."""
    t = GpuTile.upload(px)
    t.shared = True
    return t


def lend(tile: GpuTile, x0: int, y0: int):
    """A Buf showing a shared tile; reading its px downloads a private copy."""
    from .raster import Buf
    return Buf(None, x0, y0, gpu=tile)


def warp(tile: GpuTile, hi: np.ndarray, rect):
    """tile resampled through hi (frame -> tile pixels) as a new GPU Buf covering rect."""
    from .raster import Buf
    x0, y0, x1, y1 = rect
    out = GpuTile(x1 - x0, y1 - y0, clear=False)
    _draw("warp", out.fbo, (0, 0, x1 - x0, y1 - y0), None, [("src", tile.tex)], org=(x0, y0),
          size=(tile.w, tile.h), r0=tuple(float(v) for v in hi[0]), r1=tuple(float(v) for v in hi[1]))
    return Buf(None, x0, y0, gpu=out)


def from_gl(tex, x: int, y: int, w: int, h: int) -> GpuTile:
    """The w x h rect at (x, y) of a GL texture (rows bottom-up, as rendered) as a top-down tile."""
    out = GpuTile(w, h, clear=False)
    _draw("flip", out.fbo, (0, 0, w, h), None, [("src", tex)], org=(x, y), h=h)
    return out


def tile_of(buf) -> tuple[GpuTile, bool]:
    """(GPU tile holding buf's pixels, whether it is a temporary upload the caller releases)."""
    g = buf.gpu
    if g is not None:
        return g, False
    return GpuTile.upload(buf.px), True


def _behind():
    import moderngl
    return moderngl.ONE_MINUS_DST_ALPHA, moderngl.ONE


_MODES = {"normal": _over, "behind": _behind}


def supports(mode: str) -> bool:
    return mode in _MODES


def composite(dst, src, mode: str, opacity: float, grow: bool = True):
    """dst with src blended onto it (dst a GPU Buf, changed in place unless it grows); None when mode
    needs the CPU (dst is then untouched). normal is source-over, behind the backdrop over the source
    (d + s (1 - da)), as blend._normal and _behind. The frame (see frame) never grows: source pixels
    outside it are dropped (see the module docstring); other GPU Bufs grow like CPU ones."""
    blend = _MODES.get(mode)
    if blend is None:
        return None
    if src.is_null or opacity <= 0:
        return dst
    from .raster import intersect, union
    if dst.gpu.shared:
        # A cache's tile is only ever read: draw into a copy.
        dst = _grown(dst, union(dst.rect, src.rect) if grow else dst.rect)
    elif grow and not dst.gpu.fixed and union(dst.rect, src.rect) != dst.rect:
        dst = _grown(dst, union(dst.rect, src.rect))
    r = intersect(dst.rect, src.rect)
    if r is None:
        return dst
    t, temp = tile_of(src)
    try:
        _draw("copy", dst.gpu.fbo, (r[0] - dst.x0, r[1] - dst.y0, r[2] - r[0], r[3] - r[1]), blend(),
              [("src", t.tex)], off=(src.x0 - dst.x0, src.y0 - dst.y0), k=float(opacity))
    finally:
        if temp:
            t.release()
    return dst


def _grown(buf, rect):
    """A GPU Buf covering rect holding buf's pixels (transparent elsewhere)."""
    from .raster import Buf
    x0, y0, x1, y1 = rect
    out = Buf(None, x0, y0, gpu=GpuTile(x1 - x0, y1 - y0))
    _draw("copy", out.gpu.fbo, (buf.x0 - x0, buf.y0 - y0, buf.w, buf.h), None, [("src", buf.gpu.tex)],
          off=(buf.x0 - x0, buf.y0 - y0), k=1.0)
    buf.gpu.release()
    return out


def scaled(buf, k: float):
    """buf's pixels times k as a new GPU Buf."""
    from .raster import Buf
    out = GpuTile(buf.w, buf.h, clear=False)
    t, temp = tile_of(buf)
    try:
        _draw("copy", out.fbo, (0, 0, buf.w, buf.h), None, [("src", t.tex)], off=(0, 0), k=float(k))
    finally:
        if temp:
            t.release()
    return Buf(None, buf.x0, buf.y0, gpu=out)


def upload(buf):
    """buf as a GPU Buf (itself when it already is one)."""
    from .raster import Buf
    if buf.gpu is not None:
        return buf
    return Buf(None, buf.x0, buf.y0, gpu=GpuTile.upload(buf.px))


def warp_over(dst, tile: GpuTile, hi: np.ndarray, rect) -> None:
    """tile (w x h pixels) resampled through hi (frame -> tile pixels) over the GPU Buf dst, within rect."""
    from .raster import intersect
    r = intersect(dst.rect, rect)
    if r is None:
        return
    _draw("warp", dst.gpu.fbo, (r[0] - dst.x0, r[1] - dst.y0, r[2] - r[0], r[3] - r[1]), _over(),
          [("src", tile.tex)], org=(dst.x0, dst.y0), size=(tile.w, tile.h),
          r0=tuple(float(v) for v in hi[0]), r1=tuple(float(v) for v in hi[1]))


def accumulate(acc, buf):
    """acc + buf (both covering the same rect); acc None starts the sum with buf. Returns the GPU sum."""
    from .raster import Buf
    if acc is None:
        if buf.gpu is not None:
            return _grown(buf, buf.rect) if buf.gpu.shared else buf
        return Buf(None, buf.x0, buf.y0, gpu=GpuTile.upload(buf.px))
    t, temp = tile_of(buf)
    try:
        _draw("copy", acc.gpu.fbo, (0, 0, acc.w, acc.h), _add(), [("src", t.tex)], off=(0, 0), k=1.0)
    finally:
        if temp:
            t.release()
    return acc


def combine(a, b, d: float):
    """(a + b) / d, or a / d when b is None, as a new GPU Buf."""
    from .raster import Buf
    out = GpuTile(a.w, a.h, clear=False)
    ta, tmp_a = tile_of(a)
    tb, tmp_b = tile_of(b) if b is not None else (ta, False)
    try:
        _draw("sum", out.fbo, (0, 0, a.w, a.h), None, [("a", ta.tex), ("b", tb.tex)],
              has_b=int(b is not None), d=float(d))
    finally:
        if tmp_a:
            ta.release()
        if tmp_b:
            tb.release()
    return Buf(None, a.x0, a.y0, gpu=out)


def max_abs_diff(a, b) -> float:
    """max |a - b| over pixels and channels (a, b covering the same rect)."""
    ctx = gl.context()
    bw, bh = -(-a.w // 16), -(-a.h // 16)
    tex = ctx.texture((bw, bh), 1, dtype="f4")
    fbo = ctx.framebuffer(color_attachments=[tex])
    ta, tmp_a = tile_of(a)
    tb, tmp_b = tile_of(b)
    try:
        _draw("maxdiff", fbo, (0, 0, bw, bh), None, [("a", ta.tex), ("b", tb.tex)], size=(a.w, a.h))
        m = np.frombuffer(fbo.read(components=1, dtype="f4"), np.float32)
        return float(m.max(initial=0.0))
    finally:
        fbo.release()
        tex.release()
        if tmp_a:
            ta.release()
        if tmp_b:
            tb.release()


_RGB8: dict = {}
_Q8: tuple | None = None


def to_rgb8(buf, linear: bool, background=None) -> np.ndarray:
    """(h, w, 3) uint8 straight RGB of a premultiplied working-space Buf, as kernels.to_rgb8."""
    return to_rgb8_async(buf, linear, background)()


def to_rgb8_async(buf, linear: bool, background=None):
    """to_rgb8, started: returns a function that waits for the pixels and returns them. The GPU
    converts and copies the frame into a pixel buffer while the CPU goes on (with the next frame, see
    output.FrameSource), so collecting it later does not wait for the GPU. Two buffers alternate:
    collect each result before starting the second next one."""
    global _Q8
    from . import kernels
    ctx = gl.context()
    packed = buf.w % 4 == 0
    tw = buf.w * 3 // 4 if packed else buf.w
    key = (buf.w, buf.h)
    hit = _RGB8.get(key)
    if hit is None:
        _RGB8.clear()
        tex = ctx.texture((tw, buf.h), 1, dtype="u4")
        hit = _RGB8[key] = [tex, ctx.framebuffer(color_attachments=[tex]),
                            [ctx.buffer(reserve=tw * buf.h * 4) for _ in range(2)], 0]
    tex, fbo, pbos, turn = hit
    pbo = pbos[turn]
    hit[3] = 1 - turn
    if _Q8 is None:
        q8 = np.asarray(kernels._Q8_START, np.float64).copy()
        q8[0], q8[256] = -1e30, 1e30
        _Q8 = tuple(float(v) for v in q8)
    t, temp = tile_of(buf)
    try:
        prog, _ = _prog("rgb8")
        prog["q8"].value = _Q8
        bg = (0.0, 0.0, 0.0) if background is None else tuple(float(v) for v in background[:3])
        _draw("rgb8", fbo, (0, 0, tw, buf.h), None, [("src", t.tex)], linear=int(bool(linear)),
              has_bg=int(background is not None), bg=bg, pack3=int(packed))
        fbo.read_into(pbo, viewport=(0, 0, tw, buf.h), components=1, dtype="u4")
    finally:
        if temp:
            t.release()
    w, h = buf.w, buf.h

    def collect() -> np.ndarray:
        if packed:
            out = np.empty((h, w, 3), np.uint8)
            pbo.read_into(out)
            return out
        return np.frombuffer(pbo.read(), np.uint8).reshape(h, w, 4)[..., :3].copy()
    return collect
