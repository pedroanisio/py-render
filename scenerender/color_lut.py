"""The colour-management finish baked into a 3D LUT (fast path of color.finish).

color.process_rgb is a pure per-pixel function of straight working-space RGB, so the whole
per-document plan (exposure, looks, tone mapping, output primaries and transfer, OCIO path) is
sampled once on a lattice and each frame only interpolates:

* Basis. The lattice axes are the primaries of the plan's first clamp (Rec.709 in front of a tone
  mapper, the output primaries otherwise; identity under OCIO): each pixel is multiplied by that
  3x3 first, so the max(., 0) of out-of-gamut values falls on lattice planes instead of cutting
  through cells.
* Shaper. Every channel goes through s(x) = sign(x) log2(1 + |x| / C), C = 2^-10: linear around
  0 (black and small negatives are resolved like any other value), logarithmic above (a constant
  number of lattice steps per stop, as scene-linear tone mappers want). The lattice is uniform in
  s with x = 0 on a node and spans X_LO (3 nodes below 0, about -0.0008) .. X_HI = 2^8.
* Lattice. N^3 = 65^3 nodes of process_rgb, evaluated by the exact code. In linear projects the
  table stores sign(y) sRGB(|y|) and the lookup decodes it, so interpolation error is spread
  evenly over output code values instead of piling up in the shadows.
* Interpolation. Tetrahedral (the r=g, g=b, r=b planes of each cell are simplex faces, which is
  where the max-channel/hue kinks of AgX, HSV restoration and gamut compressors live).
* Rough cells. Kinks that are not on lattice planes (clamps after mixing matrices, gamut
  compression, guard rails) are found at bake time: cells whose second differences exceed PRE
  are probed at their 6 tetrahedron centroids and 12 edge midpoints with the exact function, and
  a cell whose interpolation misses any probe by more than TOL (half an 8-bit code value) is
  marked rough. Pixels in rough cells are recomputed exactly (typically 0-3 % of a frame).
* Out of range. A pixel with any channel outside X_LO..X_HI (HDR highlights beyond 2^8, larger
  negatives, NaN/inf) is not approximated either: the exact per-pixel path handles it, so the
  result stays correct for any input.
* Premultiplication is handled exactly as color.finish does: straight = px / max(a, 1e-6) where
  a > 1e-6 (else 0), sRGB-decoded in non-linear projects, mapped, multiplied by a again.

Executors: a moderngl fragment shader (frame uploaded as RGBA32F, texelFetch tetrahedral on an
RGBA32F 3D texture whose alpha holds the rough flag, read back; flagged pixels are redone on the
CPU), and the NumPy executor used without GL or with SCENERENDER_NO_GPU=1. With
SCENERENDER_EXACT_COLOR=1 color.finish does not use a LUT at all. Any GL failure disables the
GPU executor for the rest of the process.

Baking costs the exact evaluation of 65^3 nodes plus the probes (0.2-1 s). Tables are cached on
disk in $SCENERENDER_CACHE/colorlut keyed by the plan's fingerprint and this code's version, so
spawned frame workers (and later renders) load the table the main process baked (~10 ms).

Accuracy (tests/test_perf_color.py, random and real frames, every tone mapper, 8-bit sRGB):
max difference <= 2 code values, 99.9th percentile <= 1.
"""
from __future__ import annotations

import hashlib
import itertools
import logging
import os

import numpy as np

log = logging.getLogger("scenerender")

N = 65                        # lattice nodes per axis
C = 2.0 ** -10                # shaper knee: linear below ~C, log2 above
X_HI = 2.0 ** 8               # largest input covered by the lattice
NEG_NODES = 3                 # lattice nodes below 0
PRE = 2.0 / 255               # second difference (output code values) above which a cell is probed
TOL = 0.5 / 255               # largest interpolation error at a probe for a smooth cell
MIN_PIXELS = 1 << 12          # smaller buffers use the exact path
VERSION = 1                   # bump when the table layout or the bake changes

_disabled: str | None = None
_programs: dict = {}
_frames: dict = {}


def shaper(x):
    """Input value -> lattice coordinate (odd, monotonic)."""
    return np.sign(x) * np.log2(1.0 + np.abs(x) / C)


def unshaper(s):
    return np.sign(s) * C * (np.exp2(np.abs(s)) - 1.0)


def _shaper32(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, np.float32)
    return np.copysign(np.log2(np.float32(1.0) + np.abs(x) * np.float32(1.0 / C)), x)


def _simplex_offsets(n: int):
    """Flat-index offsets of the 2nd and 3rd simplex vertices for each ordering case
    4 (fr >= fg) + 2 (fg >= fb) + (fr >= fb) (impossible cases are ties: any simplex)."""
    r, g, b = n * n, n, 1
    order = {7: "rgb", 6: "rgb", 5: "rbg", 4: "brg", 3: "grb", 2: "gbr", 1: "bgr", 0: "bgr"}
    off = {"r": r, "g": g, "b": b}
    v1 = np.array([off[order[c][0]] for c in range(8)], np.int32)
    v2 = np.array([off[order[c][0]] + off[order[c][1]] for c in range(8)], np.int32)
    return v1, v2


def exact_forced() -> bool:
    return os.environ.get("SCENERENDER_EXACT_COLOR", "") not in ("", "0")


def gpu_enabled() -> bool:
    if _disabled or os.environ.get("SCENERENDER_NO_GPU", "") not in ("", "0"):
        return False
    from . import gl
    return gl.available()


def _srgb_enc(y: np.ndarray) -> np.ndarray:
    """Odd extension of the sRGB encoding (also above 1)."""
    a = np.abs(y)
    e = np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1 / 2.4) - 0.055)
    return (np.sign(y) * e).astype(np.float32)


def _srgb_dec(e: np.ndarray) -> np.ndarray:
    a = np.abs(np.asarray(e, np.float32))
    y = np.where(a <= np.float32(0.04045), a * np.float32(1 / 12.92),
                 np.power((a + np.float32(0.055)) * np.float32(1 / 1.055), np.float32(2.4)))
    return np.copysign(y, e).astype(np.float32, copy=False)


def _probe_offsets() -> np.ndarray:
    """Cell-local probe points: the 6 tetrahedron centroids and the 12 cube-edge midpoints."""
    offs = []
    for perm in itertools.permutations(range(3)):
        v = np.zeros((4, 3))
        v[1, perm[0]] = 1
        v[2] = v[1]
        v[2, perm[1]] = 1
        v[3] = 1
        offs.append(v.mean(0))
    corners = [np.array(c, float) for c in itertools.product((0, 1), repeat=3)]
    for a, b in itertools.combinations(corners, 2):
        if np.abs(b - a).sum() == 1:
            offs.append((a + b) / 2)
    return np.array(offs)


class ColorLUT:
    """A baked finish: `apply(px, linear)` maps a premultiplied RGBA frame like color.finish.

    fn: exact straight working-RGB (M, 3) -> (M, 3) function (color.process_rgb of the plan);
    encoded_out: fn returns linear light (the table then stores it sRGB-encoded);
    basis: 3x3 matrix from working RGB to the lattice axes;
    cache_key: fingerprint of everything fn depends on (enables the disk cache)."""

    def __init__(self, fn, encoded_out: bool, basis: np.ndarray | None = None, n: int = N,
                 cache_key: str | None = None):
        self.fn = fn
        self.basis = np.eye(3) if basis is None else np.asarray(basis, np.float64)
        self._bt = self.basis.T.astype(np.float32)
        self._inv_t = np.linalg.inv(self.basis).T
        self.n = n
        self.h = float(shaper(X_HI)) / (n - 1 - NEG_NODES)
        self.s_lo = -NEG_NODES * self.h
        self.x_lo = float(unshaper(self.s_lo))
        self.x_hi = float(X_HI)
        self.encoded_out = encoded_out
        self._tex = None
        self.cached = False
        path = _cache_path(cache_key, n, encoded_out, self.basis) if cache_key else None
        if path and self._load(path):
            self.cached = True
            return
        g = unshaper(self.s_lo + self.h * np.arange(n)).astype(np.float32)
        g[NEG_NODES] = 0.0
        rr, gg, bb = np.meshgrid(g, g, g, indexing="ij")
        y = self._exact(np.stack([rr, gg, bb], -1).reshape(-1, 3))
        self.table = np.ascontiguousarray(y.reshape(n, n, n, 3))                  # [r, g, b]
        self.rough = self._find_rough()
        if path:
            self._save(path)

    # ------------------------------------------------------------ bake
    def _exact(self, y: np.ndarray) -> np.ndarray:
        """Exact output for lattice-basis values, in table units."""
        x = (np.asarray(y, np.float64) @ self._inv_t).astype(np.float32)
        out = np.asarray(self.fn(x), np.float32).reshape(-1, 3)
        return _srgb_enc(out) if self.encoded_out else out

    def _find_rough(self) -> np.ndarray:
        n = self.n
        base = np.argwhere(_curved_cells(self.table, PRE)).astype(np.float64)
        bad = np.zeros(len(base), bool)
        if len(base):
            for o in _probe_offsets():
                y = unshaper(self.s_lo + self.h * (base + o)).astype(np.float32)
                err = np.abs(np.clip(self._exact(y), 0, 1) - np.clip(self._interp(y)[0], 0, 1)).max(-1)
                bad |= err > TOL
        rough = np.zeros((n, n, n), bool)
        b = base[bad].astype(np.int64)
        rough[b[:, 0], b[:, 1], b[:, 2]] = True
        return rough

    def _load(self, path: str) -> bool:
        try:
            with np.load(path) as z:
                table, rough = z["table"], z["rough"]
            if table.shape != (self.n,) * 3 + (3,) or rough.shape != (self.n,) * 3:
                return False
            self.table, self.rough = np.ascontiguousarray(table, np.float32), rough.astype(bool)
            return True
        except (OSError, ValueError, KeyError, EOFError):
            return False

    def _save(self, path: str) -> None:
        tmp = f"{path}.{os.getpid()}.tmp.npz"
        try:
            np.savez(tmp, table=self.table, rough=self.rough)
            os.replace(tmp, path)
        except OSError as e:
            log.debug("colour LUT not cached: %s", e)

    # ------------------------------------------------------------ CPU executor
    def _interp(self, y: np.ndarray):
        """Tetrahedral interpolation of lattice-basis (M, 3) values in table units -> (out, cell)."""
        n, n1 = self.n, self.n - 1
        with np.errstate(invalid="ignore"):
            f = (_shaper32(y) - np.float32(self.s_lo)) * np.float32(1.0 / self.h)
        f = np.clip(np.nan_to_num(f.T, nan=0.0), 0, n1)            # (3, M), channel planes
        b = np.minimum(np.floor(f), n1 - 1)
        f -= b
        bi = b.astype(np.int32)
        i0 = (bi[0] * n + bi[1]) * n + bi[2]
        fr, fg, fb = f
        # the cell's simplex: from i0 along the axis of the largest fraction, then the middle
        # one, to i0 + (1, 1, 1); the ordering case indexes the two intermediate vertices
        case = (fr >= fg).view(np.uint8) * 4 + (fg >= fb).view(np.uint8) * 2 + (fr >= fb).view(np.uint8)
        f_hi = np.maximum(np.maximum(fr, fg), fb)
        f_lo = np.minimum(np.minimum(fr, fg), fb)
        f_mid = fr + fg + fb - f_hi - f_lo
        v1, v2 = _simplex_offsets(n)
        i3 = i0 + (n * n + n + 1)
        T = self.table.reshape(-1, 3)
        out = np.take(T, i0, axis=0) * (1 - f_hi)[:, None]
        out += np.take(T, i0 + np.take(v1, case), axis=0) * (f_hi - f_mid)[:, None]
        out += np.take(T, i0 + np.take(v2, case), axis=0) * (f_mid - f_lo)[:, None]
        out += np.take(T, i3, axis=0) * f_lo[:, None]
        return out, i0

    def map_straight(self, rgb: np.ndarray) -> np.ndarray:
        """Straight linear working RGB (..., 3) -> finish output; exact where the LUT is not."""
        shape = rgb.shape
        x = np.asarray(rgb, np.float32).reshape(-1, 3)
        y = x @ self._bt
        out, cell = self._interp(y)
        if self.encoded_out:
            out = _srgb_dec(out)
        ok = ((y >= self.x_lo) & (y <= self.x_hi)).all(-1) & ~self.rough.reshape(-1)[cell]
        if not ok.all():
            bad = ~ok
            out[bad] = np.asarray(self.fn(x[bad]), np.float32).reshape(-1, 3)
        return out.reshape(shape)

    def apply_cpu(self, px: np.ndarray, linear: bool) -> np.ndarray:
        from .color import decode
        a = px[..., 3:4]
        rgb = np.where(a > 1e-6, px[..., :3] / np.maximum(a, 1e-6), 0).astype(np.float32)
        if not linear:
            rgb = decode(rgb, "srgb").astype(np.float32)
        res = np.empty_like(px)
        res[..., :3] = self.map_straight(rgb) * a
        res[..., 3:] = a
        return res

    # ------------------------------------------------------------ GPU executor
    def apply_gpu(self, px: np.ndarray, linear: bool) -> np.ndarray | None:
        """Same as apply_cpu on the shared GL context; None if the GPU failed."""
        global _disabled
        try:
            return self._gpu(px, linear)
        except Exception as e:  # noqa: BLE001 — any GL problem: the CPU executor takes over
            _disabled = str(e)
            log.warning("GPU colour LUT disabled, using the CPU: %s", e)
            return None

    def _gpu(self, px: np.ndarray, linear: bool) -> np.ndarray:
        import moderngl
        from . import gl
        ctx = gl.context()
        prog, vao = _program(ctx)
        if self._tex is None or self._tex[0] is not ctx:
            n = self.n
            data = np.zeros((n, n, n, 4), np.float32)
            data[..., :3] = self.table.transpose(2, 1, 0, 3)          # texel (r, g, b), r fastest
            data[..., 3] = self.rough.transpose(2, 1, 0)
            tex = ctx.texture3d((n, n, n), 4, data.tobytes(), dtype="f4")
            tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
            self._tex = (ctx, tex)
        h, w = px.shape[:2]
        src, fbo = _frame(ctx, w, h)
        src.write(np.ascontiguousarray(px, np.float32))
        src.use(0)
        self._tex[1].use(1)
        prog["frame"].value = 0
        prog["lut"].value = 1
        prog["basis"].write(np.ascontiguousarray(self.basis.T, np.float32).tobytes())   # column-major
        for k, v in (("s_lo", self.s_lo), ("inv_h", 1.0 / self.h), ("inv_c", 1.0 / C),
                     ("x_lo", self.x_lo), ("x_hi", self.x_hi)):
            prog[k].value = v
        prog["n1"].value = self.n - 1
        prog["decode_in"].value = 0 if linear else 1
        prog["decode_out"].value = 1 if self.encoded_out else 0
        fbo.use()
        fbo.viewport = (0, 0, w, h)
        vao.render(moderngl.TRIANGLE_STRIP)
        res = np.frombuffer(fbo.read(components=4, dtype="f4"), np.float32).reshape(h, w, 4).copy()
        bad = res[..., 3] < 0.5
        res[..., 3] = px[..., 3]
        if bad.any():
            from .color import decode
            p = px[bad]
            a = p[:, 3:4]
            rgb = np.where(a > 1e-6, p[:, :3] / np.maximum(a, 1e-6), 0).astype(np.float32)
            if not linear:
                rgb = decode(rgb, "srgb").astype(np.float32)
            res[bad, :3] = np.asarray(self.fn(rgb), np.float32).reshape(-1, 3) * a
        return res

    def apply(self, px: np.ndarray, linear: bool) -> np.ndarray:
        if gpu_enabled():
            out = self.apply_gpu(px, linear)
            if out is not None:
                return out
        return self.apply_cpu(px, linear)


def _cache_path(key: str, n: int, encoded_out: bool, basis: np.ndarray) -> str | None:
    try:
        from .assets import cache_dir
        d = cache_dir("colorlut")
    except (ImportError, OSError):
        return None
    h = hashlib.sha256(repr((VERSION, _code_version(), key, n, encoded_out, C, X_HI, NEG_NODES, PRE, TOL,
                             np.round(basis, 12).tolist())).encode()).hexdigest()[:32]
    return os.path.join(d, h + ".npz")


_CODE: list = []


def _code_version() -> str:
    """Hash of the modules whose code the table bakes (color.py and this file)."""
    if not _CODE:
        h = hashlib.sha256()
        here = os.path.dirname(os.path.abspath(__file__))
        for f in ("color.py", "color_lut.py"):
            try:
                with open(os.path.join(here, f), "rb") as fh:
                    h.update(fh.read())
            except OSError:
                h.update(f.encode())
        _CODE.append(h.hexdigest())
    return _CODE[0]


# Edge directions of the six tetrahedra of a cell: the axes, the (+,+) face diagonals and the
# main diagonal.
_EDGES = ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1), (1, 1, 1))


def _curved_cells(table: np.ndarray, pre: float) -> np.ndarray:
    """Cells worth probing: bool (n-1)^3, True where a corner's second difference exceeds `pre`.

    For every node the second difference f(p - d) - 2 f(p) + f(p + d) along each simplex edge
    direction measures the curvature the interpolation cannot follow (visible values: clipped
    to 0..1). The interpolation error inside a cell is at most about half of it (a kink), an
    eighth for smooth curvature, so cells below `pre` = 4 x TOL need no probing."""
    v = np.clip(table, 0.0, 1.0)
    n = v.shape[0]
    d2 = np.zeros((n, n, n), np.float32)
    for dr, dg, db in _EDGES:
        c = v[dr:n - dr, dg:n - dg, db:n - db]
        e = np.abs(v[:n - 2 * dr, :n - 2 * dg, :n - 2 * db] - 2 * c + v[2 * dr:, 2 * dg:, 2 * db:]).max(-1)
        view = d2[dr:n - dr, dg:n - dg, db:n - db]
        np.maximum(view, e, out=view)
    for ax in range(3):         # boundary nodes: borrow the inner neighbour's value
        for edge, inner in ((0, 1), (n - 1, n - 2)):
            a, b = [slice(None)] * 3, [slice(None)] * 3
            a[ax], b[ax] = edge, inner
            d2[tuple(a)] = np.maximum(d2[tuple(a)], d2[tuple(b)])
    corner = np.zeros((n - 1,) * 3, np.float32)
    for dr, dg, db in itertools.product((0, 1), repeat=3):
        np.maximum(corner, d2[dr:n - 1 + dr, dg:n - 1 + dg, db:n - 1 + db], out=corner)
    return corner > pre


_VS = """
#version 330
in vec2 in_pos;
void main() { gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

# Straight colour exactly as color.finish derives it, the basis and shaper, tetrahedral
# interpolation of the lattice (texelFetch, no hardware filtering), premultiplied again. Output
# alpha is 1 for pixels the LUT covers and 0 for pixels the CPU must compute exactly.
_FS = """
#version 330
uniform sampler2D frame;
uniform sampler3D lut;
uniform mat3 basis;
uniform float s_lo, inv_h, inv_c, x_lo, x_hi;
uniform int n1;
uniform int decode_in, decode_out;
out vec4 o;

vec4 fetch(ivec3 i) { return texelFetch(lut, i, 0); }

vec3 srgb_dec(vec3 e) {
    vec3 a = abs(e);
    vec3 y = mix(pow((a + 0.055) / 1.055, vec3(2.4)), a / 12.92, lessThanEqual(a, vec3(0.04045)));
    return sign(e) * y;
}

void main() {
    vec4 p = texelFetch(frame, ivec2(gl_FragCoord.xy), 0);
    float a = p.a;
    vec3 x = a > 1e-6 ? p.rgb / max(a, 1e-6) : vec3(0.0);
    if (decode_in == 1) x = srgb_dec(max(x, vec3(0.0)));
    x = basis * x;
    if (!(all(greaterThanEqual(x, vec3(x_lo))) && all(lessThanEqual(x, vec3(x_hi))))) { o = vec4(0.0); return; }
    vec3 s = sign(x) * log2(1.0 + abs(x) * inv_c);
    vec3 f = clamp((s - s_lo) * inv_h, 0.0, float(n1));
    vec3 b = min(floor(f), float(n1 - 1));
    f -= b;
    ivec3 i0 = ivec3(b);
    vec4 c0 = fetch(i0);
    if (c0.a > 0.5) { o = vec4(0.0); return; }          // rough cell
    vec3 c3 = fetch(i0 + ivec3(1)).rgb;
    vec3 c1, c2, w;
    if (f.r >= f.g) {
        if (f.g >= f.b)      { c1 = fetch(i0 + ivec3(1, 0, 0)).rgb; c2 = fetch(i0 + ivec3(1, 1, 0)).rgb; w = f.rgb; }
        else if (f.r >= f.b) { c1 = fetch(i0 + ivec3(1, 0, 0)).rgb; c2 = fetch(i0 + ivec3(1, 0, 1)).rgb; w = f.rbg; }
        else                 { c1 = fetch(i0 + ivec3(0, 0, 1)).rgb; c2 = fetch(i0 + ivec3(1, 0, 1)).rgb; w = f.brg; }
    } else {
        if (f.b >= f.g)      { c1 = fetch(i0 + ivec3(0, 0, 1)).rgb; c2 = fetch(i0 + ivec3(0, 1, 1)).rgb; w = f.bgr; }
        else if (f.b >= f.r) { c1 = fetch(i0 + ivec3(0, 1, 0)).rgb; c2 = fetch(i0 + ivec3(0, 1, 1)).rgb; w = f.gbr; }
        else                 { c1 = fetch(i0 + ivec3(0, 1, 0)).rgb; c2 = fetch(i0 + ivec3(1, 1, 0)).rgb; w = f.grb; }
    }
    vec3 y = c0.rgb * (1.0 - w.x) + c1 * (w.x - w.y) + c2 * (w.y - w.z) + c3 * w.z;
    if (decode_out == 1) y = srgb_dec(y);
    o = vec4(y * a, 1.0);
}
"""


def _program(ctx):
    key = id(ctx)
    entry = _programs.get(key)
    if entry is None:
        prog = ctx.program(vertex_shader=_VS, fragment_shader=_FS)
        vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], np.float32).tobytes())
        entry = _programs[key] = (prog, ctx.vertex_array(prog, [(vbo, "2f", "in_pos")]))
    return entry


def _frame(ctx, w: int, h: int):
    """Reused (source texture, RGBA32F framebuffer) pair for the current frame size."""
    import moderngl
    key = (id(ctx), w, h)
    entry = _frames.get(key)
    if entry is None:
        for k in [k for k in _frames if k[0] == id(ctx)]:
            src, fbo = _frames.pop(k)
            for att in fbo.color_attachments:
                att.release()
            fbo.release()
            src.release()
        src = ctx.texture((w, h), 4, dtype="f4")
        src.filter = (moderngl.NEAREST, moderngl.NEAREST)
        fbo = ctx.framebuffer(color_attachments=[ctx.texture((w, h), 4, dtype="f4")])
        entry = _frames[key] = (src, fbo)
    return entry
