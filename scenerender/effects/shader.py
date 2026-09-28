"""GLSL shader effects (`effect type="shader"`) and the GLSL engine shared with
`transitions/shader.py`.

Source
  @src is a file resolved against the document (`rc.doc.resolve_path`) or an inline
  `data:` URI (`data:,<url-encoded GLSL>` or `data:<mime>;base64,<b64>`). Programs are
  compiled once per source text and GL context. A compile/link error is logged once
  with the driver's info log (line numbers are those of the user's file) and the
  effect passes its input through; so does a missing file or a missing GL context
  (`scenerender.gl`), with a warning. A render never fails because of a shader.

Conventions (pinned; detected from the source, GLSL 330 core — a `#version` >= 330
in the file is kept, anything else is replaced; the legacy spellings `texture2D`,
`varying` and `gl_FragColor` are accepted)
  1. Shadertoy — the file defines `void mainImage(out vec4 fragColor, in vec2 fragCoord)`.
     Provided: iResolution (tile w, h, 1), iTime, iTimeDelta, iFrame, iFrameRate,
     iDate (0, 0, 0, iTime), iMouse (0 unless given as a param), iSampleRate (48000),
     iChannel0 (the input), iChannel1 (the @source node, else transparent),
     iChannel2/3 (transparent), iChannelResolution[4], iChannelTime[4]; extras iOffset
     (tile origin in frame pixels, GL orientation) and iFrameResolution.
  2. ISF — the file starts with an ISF `/*{ JSON }*/` header. INPUTS become uniforms
     (float, bool, long, color, point2D, image, event) with their DEFAULT; the first
     image input (conventionally inputImage) is the effect input, the second the
     @source node. Named sampler params bind additional inputs to a node ID, image
     asset ID or image file; this also supports Shadertoy iChannel2/3. IMPORTED images
     load relative to the shader file. PASSES run in
     order with TARGET buffers (WIDTH/HEIGHT expressions over $WIDTH/$HEIGHT); PERSISTENT
     buffers preserve earlier frames. A seek replays prior composition frames with
     their complete upstream effects, providing deterministic feedback. Bounded
     checkpoints avoid replaying the entire prefix for consecutive output frames.
     Provided: isf_FragNormCoord, RENDERSIZE, TIME, TIMEDELTA, FRAMEINDEX, PASSINDEX,
     DATE, IMG_NORM_PIXEL, IMG_PIXEL, IMG_THIS_PIXEL, IMG_THIS_NORM_PIXEL, IMG_SIZE.
  3. scene-render effect — any other source: a plain `void main()`. These names are
     declared automatically when the code uses them without declaring them:
       in vec2 uv;                   // 0..1 over the tile, (0, 0) bottom-left
       out vec4 fragColor;           // unless the code declares its own `out`
       uniform sampler2D inputTexture, sourceTexture;
       uniform vec2 resolution;      // tile size in output pixels
       uniform vec2 frameResolution, tileOffset;   // frame size; tile origin (GL orientation)
       uniform float time, localTime, timeDelta, fps;   uniform int frame;
Time: iTime/TIME/time are composition time (ctx.comp_t) in seconds, frame counters
are ctx.frame, localTime is seconds since the node started; everything is a pure
function of the frame, so output is deterministic for a given machine/driver.

Tile and orientation: the shader runs once per output pixel of the input tile, GL
orientation ((0, 0) = bottom-left). `<param name="padding" value="t[,r[,b,l]]">`
(document pixels, CSS order) first grows the tile with transparent pixels so the
result may spill (glows, shadows, distortions).

Colour: the input is un-premultiplied and converted from the working space to @space
(default `srgb`: straight, display-referred sRGB-encoded colour, which is what
Shadertoy/ISF code expects); the output is read as straight colour in the same space,
converted back and premultiplied. Encoded spaces are clamped to 0..1, linear ones to
>= 0, alpha to 0..1. Spaces: srgb, linear-srgb, rec709 (BT.709 OETF), display-p3,
dci-p3 (DCI white, gamma 2.6), rec2020, acescg, aces2065-1, acescct, xyz-d65 (Bradford
adaptation between white points); `raw` hands the premultiplied working-space values
to the shader untouched and takes its output as premultiplied working-space values.

Uniform values (per active uniform; the first source that has one wins)
  1. built-ins of the convention (above);
  2. `<param name value>` children — animatable with `<animate property="NAME">` and
     overridable by instance overrides of property NAME;
  3. an effect attribute of the same name written on the element (or animated/overridden);
  4. the gl-transitions default comment of the declaration (`uniform float k; // = 0.5`)
     or the ISF DEFAULT;
  5. the schema default of an attribute of that name; otherwise the GL default (0).
  Values: numbers, lists "a,b,c" or constructors "vec3(a, b, c)", true/false, colours
  ("#RRGGBB[AA]" or tokens; converted to straight colour in @space, vec3 takes rgb).
  A scalar broadcasts over a vector, a vec4 given three numbers gets alpha 1, arrays
  take flattened lists, int/bool uniforms round. Attribute specifics: radius, offsetX
  and offsetY are output pixels (document pixels x render scale); centerX/centerY
  (frame document pixels) become fragCoord pixels of the tile and `center` (vec2) gives
  both (default: tile centre); enumerated attributes (falloff, channel, tonemapper,
  position, compositeOriginal, ...) give their index in the schema enumeration; `seed`
  is the attribute or a deterministic value from `rc.ev.seed_for`.
"""
from __future__ import annotations

import ast
import base64
import functools
import json
import math
import operator
import os
import re
import urllib.parse

import numpy as np

from .. import gl
from ..raster import Buf, color_to_working, linear_to_srgb, srgb_to_linear
from ..registry import EFFECTS, FULL, warn_once
from ..values import parse_color, parse_numbers
from . import premul, source_buf, straight

# ------------------------------------------------------------------ sources
_SRC_CACHE: dict[tuple, str] = {}


def load_source(rc, uri: str, category: str) -> tuple[str, str] | None:
    """(GLSL text, base directory for relative imports) of a file path or data: URI."""
    uri = uri.strip()
    if uri.startswith("data:"):
        head, _, body = uri[5:].partition(",")
        try:
            text = base64.b64decode(body).decode("utf-8") if head.endswith(";base64") else urllib.parse.unquote(body)
        except (ValueError, UnicodeDecodeError) as e:
            warn_once(category, uri[:40], f"undecodable data: URI ({e}); skipped")
            return None
        return text, rc.doc.base
    path = rc.doc.resolve_path(uri[7:] if uri.startswith("file://") else uri)
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        warn_once(category, uri, f"shader file not found ({path}); skipped")
        return None
    text = _SRC_CACHE.get(key)
    if text is None:
        with open(path, encoding="utf-8") as f:
            text = _SRC_CACHE[key] = f.read()
    return text, os.path.dirname(path)


_VERSION = re.compile(r"^[ \t]*#[ \t]*version[ \t]+(\d+)[ \t]*(\w*)[^\n]*\n?", re.M)


def split_version(code: str) -> tuple[str, str]:
    """(#version line to use, code without its own #version line)."""
    m = _VERSION.search(code)
    if not m:
        return "#version 330", code
    keep = int(m.group(1)) >= 330 and m.group(2) in ("", "core", "compatibility")
    body = code[:m.start()] + "\n" + code[m.end():]      # keep the line count
    return (m.group(0).strip() if keep else "#version 330"), body


def modernize(code: str) -> str:
    code = re.sub(r"\btexture2D\b", "texture", code)
    code = re.sub(r"\bvarying\b", "in", code)
    return re.sub(r"\bgl_FragColor\b", "_sr_fragColor", code)


_DEFAULT_COMMENT = re.compile(
    r"^[ \t]*uniform[ \t]+(?:(?:lowp|mediump|highp)[ \t]+)?\w+[ \t]+(\w+)[ \t]*(?:\[[^\]]*\])?[ \t]*;"
    r"[ \t]*//[ \t]*=[ \t]*(.+?)[ \t]*;?[ \t]*$", re.M)


def comment_defaults(code: str) -> dict[str, str]:
    """gl-transitions style defaults: `uniform vec2 direction; // = vec2(1.0, -1.0)`."""
    return {m.group(1): m.group(2) for m in _DEFAULT_COMMENT.finditer(code)}


def declared(code: str, name: str) -> bool:
    return re.search(rf"^[ \t]*(?:layout[ \t]*\([^)]*\)[ \t]*)?(?:uniform|in|out|varying)\b[^;]*\b{name}\b",
                     code, re.M) is not None


def used(code: str, name: str) -> bool:
    return re.search(rf"\b{name}\b", code) is not None


# ------------------------------------------------------------------ programs
VS = """#version 330
in vec2 in_pos;
out vec2 uv;
out vec2 _sr_uv;
void main() { uv = in_pos * 0.5 + 0.5; _sr_uv = uv; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""
_PROGRAMS: dict[str, tuple | None] = {}
_PROGRAMS_CTX = None          # the context _PROGRAMS belongs to (a forked worker gets a new one)


def program(frag: str, category: str, label: str):
    """Compiled (program, vao) for a fragment source, or None after a logged compile error."""
    global _PROGRAMS_CTX
    ctx = gl.context()
    if ctx is not _PROGRAMS_CTX:
        _PROGRAMS.clear()
        _PROGRAMS_CTX = ctx
    key = frag
    if key not in _PROGRAMS:
        try:
            prog = ctx.program(vertex_shader=VS, fragment_shader=frag)
            _PROGRAMS[key] = (prog, gl.fullscreen_quad(prog))
        except Exception as e:  # noqa: BLE001 — moderngl raises plain Exception with the GL log
            _PROGRAMS[key] = None
            warn_once(category, f"{label}#{hash(frag) & 0xFFFFFF:06x}",
                      f"GLSL compile/link failed; falling back.\n{str(e).strip()}")
    return _PROGRAMS[key]


_SAMPLER_TYPES = {0x8B5D, 0x8B5E, 0x8B5F, 0x8B60, 0x8DC1, 0x8DCA, 0x8DD2}


def uniforms(prog):
    import moderngl
    return {n: prog[n] for n in prog if isinstance(prog[n], moderngl.Uniform)}


def is_sampler(u) -> bool:
    return u.gl_type in _SAMPLER_TYPES


def write_uniform(u, vals) -> None:
    v = [float(x) for x in np.ravel(np.asarray(vals, np.float64))]
    if not v:
        return
    n = u.dimension * u.array_length
    if len(v) == 1:
        v *= n
    elif len(v) < n:
        v += [1.0] if (u.dimension == 4 and len(v) == 3) else []
        v += [0.0] * (n - len(v))
    kind = (u.fmt or "f")[-1]
    dtype = {"f": np.float32, "i": np.int32, "I": np.uint32, "d": np.float64}.get(kind, np.float32)
    arr = np.asarray(v[:n], np.float64)
    if kind in "iI":
        arr = np.round(arr)
    u.write(np.nan_to_num(arr).astype(dtype).tobytes())


class PremulInput:
    """A sampler given as premultiplied working-space pixels: draw() converts it on the GPU to what
    to_shader(rc, px, space) returns; `code` is one of the IN_* conversions (see gpu_codes)."""

    def __init__(self, px: np.ndarray, code: int):
        self.px, self.code = px, code
        self.shape = px.shape


# to_shader / from_shader on the GPU for the spaces they handle without a matrix:
# in:  0 straight as is (srgb, non-linear pipeline)   1 sRGB-encode (srgb, linear pipeline)
#      2 clamp at 0 (linear-srgb, linear pipeline)     3 sRGB-decode (linear-srgb, non-linear pipeline)
# out: 0 clip [0, 1] (srgb, non-linear)                1 clip [0, 1] + sRGB-decode (srgb, linear)
#      2 clamp at 0 (linear-srgb, linear)              3 clip [0, 1] + sRGB-encode (linear-srgb, non-linear)
def gpu_codes(space: str, linear: bool):
    if space == "srgb":
        return (1, 1) if linear else (0, 0)
    if space == "linear-srgb":
        return (2, 2) if linear else (3, 3)
    return None


_CONV_FS = """
#version 330
uniform sampler2D src;
uniform int mode;          // 0: to_shader (premultiplied -> shader), 1: from_shader (shader -> premultiplied)
uniform int code;          // see gpu_codes
uniform int flip;          // 1: read rows bottom-up (NumPy row 0 on top <-> GL row 0 at the bottom)
out vec4 color;
float l2s(float x) { x = max(x, 0.0); return x <= 0.0031308 ? x * 12.92 : 1.055 * pow(x, 1.0 / 2.4) - 0.055; }
float s2l(float x) { x = max(x, 0.0); return x <= 0.04045 ? x / 12.92 : pow((x + 0.055) / 1.055, 2.4); }
void main() {
    ivec2 q = ivec2(gl_FragCoord.xy);
    if (flip == 1) q.y = textureSize(src, 0).y - 1 - q.y;
    vec4 p = texelFetch(src, q, 0);
    if (mode == 0) {
        vec3 c = p.a > 1e-6 ? p.rgb / p.a : vec3(0.0);
        if (code == 1) c = vec3(l2s(c.r), l2s(c.g), l2s(c.b));
        else if (code == 2) c = max(c, vec3(0.0));
        else if (code == 3) c = vec3(s2l(c.r), s2l(c.g), s2l(c.b));
        color = vec4(c, p.a);
    } else {
        // np.nan_to_num (NaN -> 0, +-inf -> +-max float), then from_shader.
        p = vec4(isnan(p.r) ? 0.0 : p.r, isnan(p.g) ? 0.0 : p.g, isnan(p.b) ? 0.0 : p.b, isnan(p.a) ? 0.0 : p.a);
        p = clamp(p, vec4(-3.4028235e38), vec4(3.4028235e38));
        float a = clamp(p.a, 0.0, 1.0);
        vec3 c;
        if (code == 0) c = clamp(p.rgb, 0.0, 1.0);
        else if (code == 1) { c = clamp(p.rgb, 0.0, 1.0); c = vec3(s2l(c.r), s2l(c.g), s2l(c.b)); }
        else if (code == 2) c = max(p.rgb, vec3(0.0));
        else { c = clamp(p.rgb, 0.0, 1.0); c = vec3(l2s(c.r), l2s(c.g), l2s(c.b)); }
        color = vec4(c * a, a);
    }
}
"""


_TARGETS: dict = {}     # (w, h) -> {"raw": texture, "conv" / "main" / "post": framebuffer}: reused per size


def _targets(w: int, h: int) -> dict:
    hit = _TARGETS.pop((w, h), None)
    if hit is None:
        ctx = gl.context()
        hit = {"raw": ctx.texture((w, h), 4, dtype="f4"),
               "conv": gl.framebuffer(w, h), "main": gl.framebuffer(w, h), "post": gl.framebuffer(w, h)}
        while len(_TARGETS) >= 6:
            old = _TARGETS.pop(next(iter(_TARGETS)))
            old["raw"].release()
            for k in ("conv", "main", "post"):
                _release_fbo(old[k])
    _TARGETS[(w, h)] = hit          # most recently used last
    return hit


def _convert_pass(tex, w: int, h: int, mode: int, code: int, fbo=None, flip: bool = False):
    """Run _CONV_FS over tex into fbo (or a new RGBA32F framebuffer, returned for the caller to release)."""
    import moderngl
    ctx = gl.context()
    prog, vao = program(_CONV_FS, CAT, "colour conversion")
    fbo = fbo if fbo is not None else gl.framebuffer(w, h)
    fbo.use()
    ctx.viewport = (0, 0, w, h)
    ctx.disable(moderngl.BLEND)
    tex.use(0)
    prog["src"].value = 0
    prog["mode"].value = mode
    prog["code"].value = int(code)
    prog["flip"].value = int(flip)
    vao.render(moderngl.TRIANGLE_STRIP)
    return fbo


def _release_fbo(fbo) -> None:
    for a in fbo.color_attachments:
        a.release()
    fbo.release()


def draw(prog_vao, w: int, h: int, samplers: dict[str, np.ndarray], premul_out: int | None = None) -> np.ndarray:
    """Run the program over a w x h target. Samplers are (h, w, 4) arrays, row 0 on top, or PremulInput
    (converted on the GPU). premul_out (an out code of gpu_codes) returns from_shader of the result,
    computed on the GPU, instead of the raw output."""
    import moderngl
    ctx = gl.context()
    prog, vao = prog_vao
    limit = int(ctx.info.get("GL_MAX_TEXTURE_SIZE", 16384))
    if max(w, h) > limit:
        raise gl.GLUnavailable(f"{w}x{h} exceeds GL_MAX_TEXTURE_SIZE {limit}")
    owned, fbos, unit, converted = [], [], 0, {}
    for name, u in uniforms(prog).items():
        if is_sampler(u):
            px = samplers.get(name)
            if isinstance(px, PremulInput):
                hit = converted.get(id(px))
                if hit is None:
                    # Uploaded as is (row 0 on top) into a reused texture; the conversion pass flips it.
                    ph, pw = px.shape[:2]
                    tg = _targets(pw, ph)
                    tg["raw"].write(np.ascontiguousarray(px.px, np.float32))
                    _convert_pass(tg["raw"], pw, ph, 0, px.code, fbo=tg["conv"], flip=True)
                    hit = converted[id(px)] = tg["conv"].color_attachments[0]
                    hit.filter = (moderngl.LINEAR, moderngl.LINEAR)
                    hit.repeat_x = hit.repeat_y = False
                tex = hit
            else:
                tex = gl.texture_from(np.ascontiguousarray((px if px is not None else np.zeros((1, 1, 4), np.float32))[::-1]))
                owned.append(tex)
            tex.use(unit)
            u.value = unit
            unit += 1
    tg = _targets(w, h) if premul_out is not None else None
    fbo = tg["main"] if tg is not None else gl.framebuffer(w, h)
    if tg is None:
        fbos.append(fbo)
    try:
        fbo.use()
        ctx.viewport = (0, 0, w, h)
        ctx.disable(moderngl.BLEND)
        fbo.clear(0.0, 0.0, 0.0, 0.0)
        vao.render(moderngl.TRIANGLE_STRIP)
        if premul_out is not None:
            # from_shader on the GPU, written top row first: read straight into the result array.
            _convert_pass(fbo.color_attachments[0], w, h, 1, premul_out, fbo=tg["post"], flip=True)
            out = np.empty((h, w, 4), np.float32)
            tg["post"].read_into(out, components=4, dtype="f4")
            return out
        return np.nan_to_num(gl.read_rgba(fbo, w, h))
    finally:
        for t in owned:
            t.release()
        for f in fbos:
            _release_fbo(f)


# ------------------------------------------------------------------ colour spaces
_XY = {"d65": (0.3127, 0.3290), "dci": (0.314, 0.351), "aces": (0.32168, 0.33767)}
_PRIMARIES = {"709": ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)),
              "p3": ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)),
              "2020": ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)),
              "ap1": ((0.713, 0.293), (0.165, 0.830), (0.128, 0.044)),
              "ap0": ((0.7347, 0.2653), (0.0, 1.0), (0.0001, -0.0770))}
SPACES = {"srgb": ("709", "d65", "srgb"), "linear-srgb": ("709", "d65", "linear"),
          "rec709": ("709", "d65", "bt709"), "display-p3": ("p3", "d65", "srgb"),
          "dci-p3": ("p3", "dci", "g26"), "rec2020": ("2020", "d65", "bt709"),
          "acescg": ("ap1", "aces", "linear"), "aces2065-1": ("ap0", "aces", "linear"),
          "acescct": ("ap1", "aces", "acescct"), "xyz-d65": ("xyz", "d65", "linear")}
_BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])


def _xyz(xy):
    x, y = xy
    return np.array([x / y, 1.0, (1 - x - y) / y])


def _npm(prim, white):
    P = np.array([_xyz(p) for p in _PRIMARIES[prim]]).T
    return P * np.linalg.solve(P, _xyz(_XY[white]))


def space_matrix(space: str) -> np.ndarray:
    """Linear Rec.709/D65 -> the space's linear RGB (or XYZ)."""
    prim, white, _ = SPACES[space]
    m = _npm("709", "d65")
    if white != "d65":
        s, d = _BRADFORD @ _xyz(_XY["d65"]), _BRADFORD @ _xyz(_XY[white])
        m = np.linalg.inv(_BRADFORD) @ np.diag(d / s) @ _BRADFORD @ m
    return m if prim == "xyz" else np.linalg.inv(_npm(prim, white)) @ m


def _encode(x, tf):
    x = np.maximum(x, 0.0)
    if tf == "srgb":
        return linear_to_srgb(x)
    if tf == "bt709":
        return np.where(x < 0.018, 4.5 * x, 1.099 * np.power(x, 0.45) - 0.099)
    if tf == "g26":
        return np.power(x, 1 / 2.6)
    if tf == "acescct":
        return np.where(x <= 0.0078125, 10.5402377416545 * x + 0.0729055341958355,
                        (np.log2(np.maximum(x, 1e-10)) + 9.72) / 17.52)
    return x


def _decode(v, tf):
    if tf == "srgb":
        return srgb_to_linear(np.clip(v, 0, 1))
    if tf == "bt709":
        v = np.clip(v, 0, 1)
        return np.where(v < 0.081, v / 4.5, np.power((v + 0.099) / 1.099, 1 / 0.45))
    if tf == "g26":
        return np.power(np.clip(v, 0, 1), 2.6)
    if tf == "acescct":
        return np.maximum(np.where(v <= 0.155251141552511, (v - 0.0729055341958355) / 10.5402377416545,
                                   np.exp2(np.minimum(v, 1.468) * 17.52 - 9.72)), 0)
    return np.maximum(v, 0)


def _space(rc, space: str | None) -> str:
    if space in SPACES or space == "raw":
        return space
    if space:
        warn_once("shader-space", space, "unknown colour space; using srgb")
    return "srgb"


def to_shader(rc, px: np.ndarray, space: str) -> np.ndarray:
    """Premultiplied working-space pixels -> straight colour in `space` (raw: unchanged)."""
    if space == "raw":
        return np.ascontiguousarray(px, np.float32)
    rgb, a = straight(px)
    if space == "srgb":
        enc = linear_to_srgb(rgb) if rc.linear else rgb
    else:
        _, _, tf = SPACES[space]
        lin = rgb if rc.linear else srgb_to_linear(rgb)
        enc = _encode(lin @ space_matrix(space).T.astype(np.float32), tf)
    return np.concatenate([enc, a], -1).astype(np.float32)


def from_shader(rc, px: np.ndarray, space: str) -> np.ndarray:
    """Straight shader output in `space` -> premultiplied working-space pixels."""
    a = np.clip(px[..., 3:4], 0, 1)
    if space == "raw":
        return np.concatenate([np.clip(px[..., :3], 0, None), a], -1).astype(np.float32)
    _, _, tf = SPACES[space]
    if space == "srgb":
        enc = np.clip(px[..., :3], 0, 1)
        work = srgb_to_linear(enc) if rc.linear else enc
    else:
        lin = np.maximum(_decode(px[..., :3], tf) @ np.linalg.inv(space_matrix(space)).T.astype(np.float32), 0)
        work = lin if rc.linear else linear_to_srgb(np.clip(lin, 0, 1))
    return premul(np.asarray(work, np.float32), a)


def shader_color(rc, c, space: str) -> list[float]:
    """A straight sRGB colour tuple as the shader sees colours in `space`."""
    r, g, b, a = color_to_working(tuple(c) + ((1.0,) if len(c) == 3 else ()), rc.linear)
    px = premul(np.float32([[[r, g, b]]]), np.float32([[[a]]]))
    return [float(x) for x in to_shader(rc, px, space)[0, 0]]


# ------------------------------------------------------------------ values
_CTOR = re.compile(r"^\s*[A-Za-z_]\w*\s*\((.*)\)\s*$", re.S)


def parse_raw(rc, raw, space: str) -> list[float] | None:
    """A param/default string as a flat float list (numbers, constructors, booleans, colours)."""
    if isinstance(raw, bool):
        return [float(raw)]
    if isinstance(raw, (int, float)):
        return [float(raw)]
    if isinstance(raw, (tuple, list)):
        try:
            return [float(x) for x in np.ravel(np.asarray(raw, np.float64))]
        except (TypeError, ValueError):
            return None
    s = str(raw).strip()
    if s in ("true", "false"):
        return [1.0 if s == "true" else 0.0]
    m = _CTOR.match(s)
    body = m.group(1) if m else s
    try:
        return parse_numbers(re.sub(r"(?<=\d)[fF]\b", "", body.replace("(", " ").replace(")", " ")))
    except ValueError:
        pass
    try:
        c = parse_color(s, rc.doc.tokens, None)
    except ValueError:
        return None
    return shader_color(rc, c, space) if c is not None else None


def param_els(el) -> dict[str, str]:
    return {c.get("name"): c.get("value") for c in el
            if isinstance(c.tag, str) and c.tag.rsplit("}", 1)[-1] == "param" and c.get("name")}


def explicit(rc, el, name: str, ctx) -> bool:
    """Written on the element, animated, or overridden by an enclosing instance."""
    return (el.get(name) is not None or bool(rc.ev._anims(el, name))
            or (bool(ctx.scope.overrides) and ctx.scope.lookup(el.get("id"), name) is not None))


def resolve_uniforms(rc, el, ctx, prog, *, builtins: dict, defaults: dict[str, str], space: str,
                     attr_value, reserved=()) -> None:
    """Write every active non-sampler uniform from the precedence chain documented above."""
    params = param_els(el)
    for name, u in uniforms(prog).items():
        if is_sampler(u):
            continue
        vals = uniform_value(rc, el, ctx, name, params, builtins, defaults, space, attr_value, reserved)
        if vals is not None:
            write_uniform(u, vals)


def uniform_value(rc, el, ctx, name, params, builtins, defaults, space, attr_value, reserved=()):
    """Resolve inputs even when GLSL optimizes their uniforms out (ISF buffer sizes)."""
    vals = builtins.get(name)
    info = rc.doc.type_info(el)
    native = info and name in info.attrs
    if vals is None and name in params and name not in reserved:
        raw = params[name]
        vals = parse_raw(rc, raw if native else rc.ev.get(el, name, ctx, raw), space)
        if vals is None:
            warn_once("shader-uniform", name, f"cannot parse value {raw!r}; ignored")
    if vals is None:
        vals = attr_value(name, True)
    if vals is None and (name in defaults or not native):
        raw = defaults.get(name)
        vals = parse_raw(rc, raw if native else rc.ev.get(el, name, ctx, raw), space)
    return attr_value(name, False) if vals is None else vals


def attribute_uniform(rc, el, ctx, name: str, space: str, only_explicit: bool) -> list[float] | None:
    """Generic attribute -> uniform value (colours, enumerations, numbers, number lists)."""
    info = rc.doc.type_info(el)
    a = info.attrs.get(name) if info else None
    if a is None or (only_explicit and not explicit(rc, el, name, ctx)):
        return None
    if a.type == "colorType":
        c = rc.ev.color(el, name, ctx, None)
        return None if c is None else shader_color(rc, c, space)
    v = rc.ev.get(el, name, ctx, None)
    if v is None:
        return None
    if a.enum and isinstance(v, str):
        return [float(a.enum.index(v))] if v in a.enum else None
    try:
        return parse_raw(rc, v, space) if not isinstance(v, str) else parse_numbers(v)
    except ValueError:
        return None


# ------------------------------------------------------------------ effect harnesses
SHADERTOY = """{version}
uniform vec3 iResolution;
uniform float iTime, iTimeDelta, iFrameRate, iSampleRate;
uniform int iFrame;
uniform vec4 iMouse, iDate;
uniform vec3 iChannelResolution[4];
uniform float iChannelTime[4];
uniform sampler2D iChannel0, iChannel1, iChannel2, iChannel3;
uniform vec2 iOffset, iFrameResolution;
out vec4 _sr_out;
#line 1
{code}
void main() {{ vec4 c = vec4(0.0); mainImage(c, gl_FragCoord.xy); _sr_out = c; }}
"""
PLAIN_DECLS = {"uv": "in vec2 uv;", "inputTexture": "uniform sampler2D inputTexture;",
               "sourceTexture": "uniform sampler2D sourceTexture;", "resolution": "uniform vec2 resolution;",
               "frameResolution": "uniform vec2 frameResolution;", "tileOffset": "uniform vec2 tileOffset;",
               "time": "uniform float time;", "localTime": "uniform float localTime;",
               "timeDelta": "uniform float timeDelta;", "fps": "uniform float fps;", "frame": "uniform int frame;"}
ISF_PRELUDE = """in vec2 _sr_uv;
uniform vec2 RENDERSIZE;
uniform float TIME, TIMEDELTA;
uniform int FRAMEINDEX, PASSINDEX;
uniform vec4 DATE;
#define isf_FragNormCoord _sr_uv
#define vv_FragNormCoord _sr_uv
#define IMG_NORM_PIXEL(i, c) texture(i, c)
#define IMG_PIXEL(i, c) texture(i, (c) / vec2(textureSize(i, 0)))
#define IMG_THIS_PIXEL(i) texture(i, gl_FragCoord.xy / vec2(textureSize(i, 0)))
#define IMG_THIS_NORM_PIXEL(i) texture(i, _sr_uv)
#define IMG_NORM_THIS_PIXEL(i) texture(i, _sr_uv)
#define IMG_SIZE(i) vec2(textureSize(i, 0))
"""
_ISF_TYPES = {"float": "float", "bool": "bool", "event": "bool", "long": "int", "color": "vec4",
              "point2D": "vec2", "image": "sampler2D", "audio": "sampler2D", "audioFFT": "sampler2D"}
_ISF_HEADER = re.compile(r"^\s*/\*\s*(\{.*?\})\s*\*/", re.S)


@functools.lru_cache(maxsize=64)
def build_effect(code: str) -> tuple[str, str, dict, dict]:
    """(convention, fragment source, defaults, ISF header) for an effect source."""
    version, code = split_version(code)
    if re.search(r"\bmainImage\s*\(", code):
        code = modernize(code)
        return "shadertoy", SHADERTOY.format(version=version, code=code), comment_defaults(code), {}
    header, defaults, pre = {}, {}, []
    m = _ISF_HEADER.match(code)
    if m:
        try:
            header = json.loads(m.group(1))
        except ValueError:
            header = {}
    if header:
        code = code[:m.start()] + "\n" * m.group(0).count("\n") + code[m.end():]
        pre.append(ISF_PRELUDE)
        for inp in header.get("INPUTS", []):
            name, typ = inp.get("NAME"), _ISF_TYPES.get(inp.get("TYPE", ""), "float")
            if name and not declared(code, name):
                pre.append(f"uniform {typ} {name};")
            if name and "DEFAULT" in inp and typ != "sampler2D":
                d = inp["DEFAULT"]
                defaults[name] = ",".join(str(float(x)) for x in d) if isinstance(d, list) else str(float(d))
        for name in (header.get("IMPORTED") or {}):
            if not declared(code, name):
                pre.append(f"uniform sampler2D {name};")
        for ps in header.get("PASSES") or []:
            if ps.get("TARGET") and not declared(code, ps["TARGET"]):
                pre.append(f"uniform sampler2D {ps['TARGET']};")
    uses_legacy_out = used(code, "gl_FragColor")
    code = modernize(code)
    for name, decl in PLAIN_DECLS.items():
        if name == "uv" and header:
            continue
        if used(code, name) and not declared(code, name):
            pre.append(decl)
    if uses_legacy_out:
        pre.append("out vec4 _sr_fragColor;")
    elif not re.search(r"^[ \t]*(?:layout[ \t]*\([^)]*\)[ \t]*)?out\b", code, re.M):
        pre.append("out vec4 fragColor;")
    defaults = {**comment_defaults(code), **defaults}
    frag = f"{version}\n" + "\n".join(pre) + "\n#line 1\n" + code
    return ("isf" if header else "plain"), frag, defaults, header


_SAFE_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
             ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}
_SAFE_FNS = {"floor": math.floor, "ceil": math.ceil, "round": round, "abs": abs, "min": min, "max": max,
             "sqrt": math.sqrt, "pow": pow}


def isf_size(expr, names: dict[str, float], default: int) -> int:
    """Evaluate an ISF WIDTH/HEIGHT expression ("$WIDTH/16.0", "floor($HEIGHT*0.5)")."""
    if expr is None:
        return default
    src = re.sub(r"\$(\w+)", lambda m: repr(names.get(m.group(1), 0.0)), str(expr))

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _SAFE_OPS:
            return _SAFE_OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _SAFE_OPS:
            return _SAFE_OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _SAFE_FNS:
            return _SAFE_FNS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError(expr)
    try:
        return max(1, int(round(float(ev(ast.parse(src, mode="eval"))))))
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError):
        warn_once("effect-shader", f"isf-size:{expr}", "unreadable ISF pass size; using the tile size")
        return default


def _imported(rc, header, base: str, space: str) -> dict[str, np.ndarray]:
    from PIL import Image
    out = {}
    for name, spec in (header.get("IMPORTED") or {}).items():
        path = spec.get("PATH") if isinstance(spec, dict) else None
        try:
            img = np.asarray(Image.open(os.path.join(base, path)).convert("RGBA"), np.float32) / 255.0
        except (OSError, TypeError, AttributeError):
            warn_once("effect-shader", f"isf-import:{path}", "ISF IMPORTED image not readable; transparent")
            continue
        rgb = img[..., :3]
        px = premul(srgb_to_linear(rgb) if rc.linear else rgb, img[..., 3:4])
        out[name] = to_shader(rc, px, space)
    return out


def padding(rc, e, ctx) -> tuple[int, int, int, int]:
    """(top, right, bottom, left) output pixels from <param name="padding"> (CSS shorthand)."""
    raw = param_els(e).get("padding")
    if raw is None:
        return 0, 0, 0, 0
    v = parse_raw(rc, rc.ev.get(e, "padding", ctx, raw), "srgb") or [0.0]
    v = (v * 4 if len(v) == 1 else v * 2 if len(v) == 2 else v + v[1:2] if len(v) == 3 else v)[:4]
    t, r, b, l = v
    return tuple(max(0, int(math.ceil(x * rc.scale))) for x in (t, r, b, l))


# ------------------------------------------------------------------ the effect
CAT = "effect-shader"
PERSISTENT_CACHE_BYTES = 128 * 1024 * 1024


def _isf_audio_inputs(rc, e, ctx, header, params):
    """Deterministic centered 2048-sample PCM/FFT textures, one row per mix channel.

    Waveforms use .5 as zero; spectra use Hann-windowed linear magnitudes with
    a full-scale bin-centered sine at one. MAX reduces the sample/bin count;
    a one-column waveform carries RMS volume instead of a canceling mean.
    """
    from ..audio import mixer_for
    result = {}
    for entry in header.get("INPUTS", []):
        name, kind = entry.get("NAME"), entry.get("TYPE")
        if not name or kind not in ("audio", "audioFFT"):
            continue
        mixer = mixer_for(rc)
        source = rc.ev.str(e, name, ctx, params.get(name, "master"))
        source = rc.doc.resolve_id(e, source)
        key = ("shader-audio-signal", source)
        if key not in rc.cache:
            rc.cache[key] = mixer.signal(source)
        signal = rc.cache[key]
        count = 2048
        segment = np.zeros((count, signal.shape[1] if signal is not None else mixer.nch), np.float32)
        begin = round(ctx.comp_t * mixer.sr) - count // 2
        if signal is not None:
            lo, hi = max(0, begin), min(len(signal), begin + count)
            if hi > lo:
                segment[lo - begin:hi - begin] = signal[lo:hi]
        if kind == "audioFFT":
            window = np.hanning(count)
            values = np.abs(np.fft.rfft(segment * window[:, None], axis=0)) * (2. / window.sum())
            values[[0, -1]] *= .5
        else:
            values = segment
        width = max(1, min(len(values), int(entry.get("MAX", len(values)))))
        if width < len(values):
            edges = np.linspace(0, len(values), width + 1).astype(int)
            if kind == "audioFFT":
                values = np.stack([values[a:b].max(axis=0) for a, b in zip(edges[:-1], edges[1:])])
            elif width == 1:
                values = np.sqrt(np.mean(values * values, axis=0, keepdims=True))
            else:
                values = np.stack([values[a:b].mean(axis=0) for a, b in zip(edges[:-1], edges[1:])])
        if kind == "audio":
            values = .5 + .5 * values
        # draw() flips image rows for GL; channel zero belongs at texture y=0.
        values = np.clip(values.T[::-1], 0, 1).astype(np.float32)
        result[name] = np.concatenate([np.repeat(values[..., None], 3, axis=2), np.ones((*values.shape, 1), np.float32)], axis=2)
    return result


def _store_feedback(checkpoints, frame, states):
    snapshot = dict(states)
    size = sum(px.nbytes for targets in snapshot.values() for px in targets.values())
    checkpoints.pop(frame, None)
    checkpoints[frame] = (snapshot, size)
    # Keep frame zero and the most recent complete snapshot, even when one
    # frame exceeds the budget. Discarding a checkpoint only increases replay.
    while len(checkpoints) > 2 and (sum(v[1] for v in checkpoints.values()) > PERSISTENT_CACHE_BYTES
                                    or len(checkpoints) > 33):
        del checkpoints[next(k for k in checkpoints if k not in (0, frame))]


def _persistent_inputs(rc, ctx):
    """Replay earlier composition frames to reconstruct ISF feedback on a seek.

    Replay renders the complete upstream scene, including animated inputs and
    earlier effects. All feedback reads within a frame share the same snapshot,
    so repeated evaluations and motion-blur samples cannot advance the history.
    """
    states = rc.cache.setdefault("isf-persistent-states", {})
    if rc.cache.get("isf-replaying"):
        return rc.frame_cache.setdefault("isf-persistent-inputs", dict(states))
    fps = float(rc.doc.fps)
    count = max(0, int(math.ceil(ctx.comp_t * fps - 1e-8)))
    key = ("isf-persistent-inputs", count)
    if key not in rc.frame_cache:
        checkpoints = rc.cache.setdefault("isf-checkpoints", {0: ({}, 0)})
        first = max(k for k in checkpoints if k <= count)
        saved_frame, saved_scene = rc.frame_cache, rc.scene_context
        saved_matrix = rc.scene_matrix
        saved_skip, saved_blur = rc._skip_effects, rc._node_mb
        active = rc.cache.get("effect-source-active")
        states.clear()
        states.update(checkpoints[first][0])
        rc.cache["isf-replaying"] = True
        rc.cache["effect-source-active"] = set()
        rc._skip_effects = set()
        rc.scene_context = None
        rc.scene_matrix = None
        try:
            interval = max(1, round(fps))
            for frame in range(first, count):
                rc.render_frame(frame / fps, frame)
                if (frame + 1) % interval == 0:
                    _store_feedback(checkpoints, frame + 1, states)
            _store_feedback(checkpoints, count, states)
            saved_frame[key] = dict(states)
        finally:
            rc.frame_cache, rc.scene_context = saved_frame, saved_scene
            rc.scene_matrix = saved_matrix
            rc._skip_effects, rc._node_mb = saved_skip, saved_blur
            rc.cache.pop("isf-replaying", None)
            if active is None:
                rc.cache.pop("effect-source-active", None)
            else:
                rc.cache["effect-source-active"] = active
    return rc.frame_cache[key]


@EFFECTS.register("shader", level=FULL,
                  note="GLSL via moderngl: Shadertoy mainImage, ISF (inputs, imports, passes) or plain "
                       "main() with inputTexture/uv/time; params, attributes and `// =` defaults as "
                       "uniforms; @source as second texture; @space colour handling; padding param; "
                       "compile errors and missing GL pass through with a warning")
def shader_effect(rc, e, buf, ctx, node=None):
    uri = rc.ev.str(e, "src", ctx)
    if not uri:
        warn_once(CAT, e.get("id") or "?", "effect type='shader' without @src; passed through")
        return buf
    loaded = load_source(rc, uri, CAT)
    if loaded is None:
        return buf
    try:
        out = _run_effect(rc, e, buf, ctx, node, *loaded, label=uri if not uri.startswith("data:") else e.get("id", "data"))
    except gl.GLUnavailable as err:
        warn_once(CAT, "gl", f"OpenGL unavailable ({err}); shader effects pass their input through")
        return buf
    return buf if out is None else out


def _run_effect(rc, e, buf, ctx, node, code, base, *, label):
    _conv, frag, defaults, header = build_effect(code)
    prog = program(frag, CAT, label)
    if prog is None:
        return None
    passes = header.get("PASSES") or [{}]
    persistent = {p["TARGET"] for p in passes if p.get("PERSISTENT") and p.get("TARGET")}
    history_key = (e, node, ctx.scope, repr(ctx.vars), code)
    previous = _persistent_inputs(rc, ctx).get(history_key, {}) if persistent else {}
    space = _space(rc, rc.ev.str(e, "space", ctx, None))
    t, r, b, l = padding(rc, e, ctx)
    work = Buf(buf.region((buf.x0 - l, buf.y0 - t, buf.x0 + buf.w + r, buf.y0 + buf.h + b)),
               buf.x0 - l, buf.y0 - t) if (t or r or b or l) else buf
    w, h = work.w, work.h
    # Single-pass shaders in the sRGB space (the common case) convert on the GPU: the pixels go up and
    # come back premultiplied, with the same arithmetic as to_shader / from_shader.
    codes = gpu_codes(space, bool(rc.linear)) if not header else None
    gpu_conv = codes is not None
    inp = PremulInput(np.ascontiguousarray(work.px, np.float32), codes[0]) if gpu_conv else to_shader(rc, work.px, space)
    src = None
    if rc.ev.str(e, "source", ctx):
        sb = source_buf(rc, e, ctx, node)
        if sb is not None:
            src = to_shader(rc, sb.region(work.rect), space)
    fps = float(rc.doc.fps) or 30.0
    now = float(ctx.comp_t)
    seed = float(rc.ev.num(e, "seed", ctx, 0)) if e.get("seed") is not None else float(rc.ev.seed_for(e, "shader") % 65536)
    B = {"iResolution": (w, h, 1.0), "iTime": now, "iTimeDelta": 1 / fps, "iFrame": ctx.frame,
         "iFrameRate": fps, "iDate": (0, 0, 0, now), "iOffset": (work.x0, rc.height - work.y0 - h),
         "iFrameResolution": (rc.width, rc.height),
         "iChannelResolution": (w, h, 1, *((w, h, 1) if src is not None else (1, 1, 1)), 1, 1, 1, 1, 1, 1),
         "iChannelTime": (now, now, 0, 0),
         "resolution": (w, h), "frameResolution": (rc.width, rc.height),
         "tileOffset": (work.x0, rc.height - work.y0 - h), "time": now,
         "localTime": float(ctx.t - ctx.node_start), "timeDelta": 1 / fps, "fps": fps, "frame": ctx.frame,
         "TIME": now, "TIMEDELTA": 0. if now == 0 else 1 / fps, "FRAMEINDEX": ctx.frame, "DATE": (0, 0, 0, now)}
    defaults = {"iMouse": "0", "iSampleRate": "48000", **defaults}
    samplers = {"iChannel0": inp, "inputTexture": inp, "iChannel1": src, "sourceTexture": src}
    images = [i["NAME"] for i in header.get("INPUTS", []) if i.get("TYPE") == "image" and i.get("NAME")]
    audio_names = {i["NAME"] for i in header.get("INPUTS", []) if i.get("TYPE") in ("audio", "audioFFT") and i.get("NAME")}
    for name, px in zip(images, (inp, src)):
        samplers[name] = px
    params = param_els(e)
    for name in set(images) | {n for n, u in uniforms(prog[0]).items() if is_sampler(u)}:
        if name not in params or name in audio_names:
            continue
        value = rc.ev.str(e, name, ctx, params[name])
        sid = rc.doc.resolve_id(e, value)
        source, _ = rc.ev.reference(sid, e, ctx)
        if source is not None and source.getparent().tag != "assets":
            sb = source_buf(rc, e, ctx, node, source=sid)
            if sb is not None:
                samplers[name] = to_shader(rc, sb.region(work.rect), space)
        elif source is not None:
            from ..assets import buffer_for_asset
            sb = buffer_for_asset(rc, source, ctx)
            if sb is not None:
                samplers[name] = to_shader(rc, sb.px, space)
        else:
            from ..three.loaders import decode_image
            if value:
                path = rc.doc.resolve_path(value)
                try:
                    img = decode_image(path, srgb=rc.linear)
                except (OSError, ValueError):
                    warn_once(CAT, f"{label}:{name}", f"sampler image {value!r} is not readable")
                    continue
                if img is not None:
                    samplers[name] = to_shader(rc, premul(img[..., :3], img[..., 3:4]), space)
    if header:
        samplers.update(_imported(rc, header, base, space))
        samplers.update(_isf_audio_inputs(rc, e, ctx, header, params))
    samplers.update(previous)
    B["iChannelResolution"] = tuple(component for i in range(4)
        for component in ((samplers[f"iChannel{i}"].shape[1], samplers[f"iChannel{i}"].shape[0], 1)
                          if samplers.get(f"iChannel{i}") is not None else (1, 1, 1)))
    B["iChannelTime"] = tuple(now if samplers.get(f"iChannel{i}") is not None else 0. for i in range(4))
    sx, sy = rc.scale, rc.scale

    def attr_value(name, only_explicit):
        if name in ("centerX", "centerY", "center"):
            if only_explicit and not any(explicit(rc, e, n, ctx) for n in ("centerX", "centerY")):
                return None
            cx = rc.ev.num(e, "centerX", ctx, (work.x0 + w / 2) / sx) * sx - work.x0
            cy = h - (rc.ev.num(e, "centerY", ctx, (work.y0 + h / 2) / sy) * sy - work.y0)
            return {"centerX": [cx], "centerY": [cy], "center": [cx, cy]}[name]
        if name == "seed":
            return [seed]
        v = attribute_uniform(rc, e, ctx, name, space, only_explicit)
        if v is not None and name in ("radius", "offsetX", "offsetY"):
            v = [x * rc.scale for x in v]
        return v

    names = {"WIDTH": w, "HEIGHT": h}
    for input_ in header.get("INPUTS", []):
        name = input_.get("NAME")
        if name and input_.get("TYPE") in ("float", "long", "bool", "event"):
            value = uniform_value(rc, e, ctx, name, params, B, defaults, space, attr_value)
            if value is not None:
                names[name] = float(np.ravel(value)[0])
    out = None
    for i, ps in enumerate(passes):
        pw = isf_size(ps.get("WIDTH"), names, w)
        ph = isf_size(ps.get("HEIGHT"), names, h)
        bi = {**B, "RENDERSIZE": (pw, ph), "PASSINDEX": i}
        resolve_uniforms(rc, e, ctx, prog[0], builtins=bi, defaults=defaults, space=space,
                         attr_value=attr_value, reserved=("padding",))
        last = i == len(passes) - 1
        out = draw(prog, pw, ph, samplers, premul_out=codes[1] if gpu_conv and last and (pw, ph) == (w, h) else None)
        if ps.get("TARGET"):
            samplers[ps["TARGET"]] = out
    if persistent:
        rc.cache["isf-persistent-states"][history_key] = {name: samplers[name] for name in persistent}
    if out.shape[:2] != (h, w):
        resize = program('''#version 330
            in vec2 uv; uniform sampler2D image; out vec4 color;
            void main() { color = texture(image, uv); }''', CAT, "ISF output resize")
        out = draw(resize, w, h, {"image": out}, premul_out=codes[1] if gpu_conv else None)
    return Buf(out if gpu_conv else from_shader(rc, out, space), work.x0, work.y0)
