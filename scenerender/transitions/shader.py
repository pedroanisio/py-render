"""GLSL transitions (`transition type="shader"`) in the gl-transitions.com convention.

@shader is a file resolved against the document or an inline `data:` URI. The code
defines `vec4 transition(vec2 uv)` and may use `getFromColor(uv)`, `getToColor(uv)`,
`progress` (the eased progress p), `ratio` (frame width / height), the samplers
`from`/`to`, and custom uniforms with gl-transitions defaults
(`uniform float smoothness; // = 0.5`). It is wrapped in the standard harness
(GLSL 330 core; `texture2D`/`varying`/`gl_FragColor` spellings accepted; errors are
reported with the user's line numbers). Both pictures are full frames, so
getFromColor/getToColor are plain lookups; uv is 0..1 with (0, 0) bottom-left.

Colour (pinned): a/b are premultiplied working-space (linear) frames; the shader sees
straight, display-referred sRGB-encoded colour as gl-transitions expect, and its
output is read the same way, then linearised and premultiplied again. A missing side
(in/out transition) is transparent; transparent texels of one side carry the other
side's colour with alpha 0, so straight-alpha mixes (mix(from, to, progress)) fade
coverage without darkening (standard colour bleeding for straight-alpha pipelines).
At p = 0 / 1 conforming shaders therefore return the inputs up to float rounding.

Uniform values follow the effect precedence (see effects/shader.py): built-ins
(progress, ratio, resolution (W, H), time (composition seconds), frame, the samplers
`from`, `to` and `matte` — the @matte node or image asset rendered full frame),
then `<param>` children (animatable, overridable), then transition attributes written
on the element, then `// = ...` defaults, then attribute schema defaults. Attribute
mapping: `direction` is the unit motion vector for a vec2 uniform (uv space, +y up;
"angle" uses @angle) or the angle in degrees clockwise from screen right for a float;
`angle`, `softness`, `duration` are numbers; `color` is a straight sRGB colour (vec3
takes rgb); enumerations give their index. @duration, @alignment and @curve shape p
(compositor); @audio is the mixer's.

@motionBlur (default true): when the Renderer is not already supersampling the frame,
the shader is evaluated at 2..16 progress values spread over a 180-degree shutter
(dp = dp/dt * 0.5 / fps; one sample per 2 px if content moved one frame extent per unit
progress) and averaged in premultiplied space; nothing happens while
progress is still.

Fallbacks: without @shader, with an unreadable file, a GLSL compile error (logged
once with the driver log) or no GL context, the transition renders as a crossfade.
"""
from __future__ import annotations

import functools
import math

import numpy as np

from .. import gl
from ..effects import shader as eng
from ..registry import FULL, TRANSITIONS, warn_once
from . import arrays, direction, out, velocity

CAT = "transition-shader"
HARNESS = """{version}
uniform sampler2D from, to;
uniform float progress, ratio;
in vec2 _sr_uv;
out vec4 _sr_out;
{extra}
vec4 getFromColor(vec2 uv) {{ return texture(from, uv); }}
vec4 getToColor(vec2 uv) {{ return texture(to, uv); }}
#line 1
{code}
void main() {{ _sr_out = transition(_sr_uv); }}
"""
_OPTIONAL = {"resolution": "uniform vec2 resolution;", "time": "uniform float time;",
             "frame": "uniform int frame;", "matte": "uniform sampler2D matte;"}


@functools.lru_cache(maxsize=64)
def build(code: str) -> tuple[str, dict[str, str]]:
    version, code = eng.split_version(code)
    legacy = eng.used(code, "gl_FragColor")
    code = eng.modernize(code)
    extra = [d for n, d in _OPTIONAL.items() if eng.used(code, n) and not eng.declared(code, n)]
    if legacy:
        extra.append("vec4 _sr_fragColor;")
    return HARNESS.format(version=version, code=code, extra="\n".join(extra)), eng.comment_defaults(code)


def matte_rgba(rc, tr, ctx) -> np.ndarray | None:
    """Premultiplied working-space full frame of the @matte node or image asset."""
    from ..compositor import scale as scale_m
    from ..document import ln
    from ..registry import ASSETS
    node = rc.doc.ids.get(tr.get("matte") or "")
    if node is None:
        return None
    parent = node.getparent()
    if parent is not None and ln(parent) == "assets":
        fn = ASSETS.get(ln(node))
        aw, ah = rc.asset_size(node, ctx) if fn else (0, 0)
        buf = fn(rc, node, scale_m(rc.width / aw, rc.height / ah), ctx) if aw and ah else None
        return None if buf is None else buf.region((0, 0, rc.width, rc.height))
    top = parent is None or ln(parent) in ("composition", "symbol", "symbols", "scene")
    PM = rc.root_matrix if top else rc.world_matrix(parent, ctx)
    box = (rc.doc.width, rc.doc.height) if top else rc.node_size(parent, ctx, (rc.doc.width, rc.doc.height))
    o = rc.render_node(node, ctx, PM, box, force=True)
    return np.zeros((rc.height, rc.width, 4), np.float32) if o is None else o.buf.region((0, 0, rc.width, rc.height)) * o.opacity


def _bleed(A: np.ndarray, B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ea, eb = A[..., 3] <= 1e-6, B[..., 3] <= 1e-6
    A2, B2 = A.copy(), B.copy()
    A2[ea, :3] = B[ea, :3]
    B2[eb, :3] = A[eb, :3]
    return A2, B2


@TRANSITIONS.register("shader", level=FULL,
                      note="gl-transitions GLSL (transition(uv), getFromColor/getToColor, progress, ratio, "
                           "`// =` uniform defaults) via moderngl; params and attributes as uniforms, @matte "
                           "sampler, shutter-sampled motion blur; crossfade fallback on errors or without GL")
def shader_transition(rc, tr, a, b, p, ctx):
    fallback = TRANSITIONS.get("crossfade")
    uri = rc.ev.str(tr, "shader", ctx)
    if not uri:
        warn_once(CAT, tr.get("id") or "?", "transition type='shader' without @shader; crossfade")
        return fallback(rc, tr, a, b, p, ctx)
    loaded = eng.load_source(rc, uri, CAT)
    if loaded is None:
        return fallback(rc, tr, a, b, p, ctx)
    try:
        res = _run(rc, tr, a, b, p, ctx, loaded[0], uri if not uri.startswith("data:") else tr.get("id", "data"))
    except gl.GLUnavailable as err:
        warn_once(CAT, "gl", f"OpenGL unavailable ({err}); shader transitions render as crossfades")
        res = None
    return fallback(rc, tr, a, b, p, ctx) if res is None else res


def _progress_samples(rc, tr, ctx, p: float) -> list[float]:
    if rc.mb_center is not None or not rc.ev.bool(tr, "motionBlur", ctx, True):
        return [p]
    dp = abs(velocity(rc, tr, ctx)) * 0.5 / (float(rc.doc.fps) or 30.0)
    if dp < 1 / 512:
        return [p]
    n = min(16, max(2, math.ceil(dp * max(rc.width, rc.height) / 2)))
    return [min(1.0, max(0.0, p + dp * (k / (n - 1) - 0.5))) for k in range(n)]


def _run(rc, tr, a, b, p, ctx, code, label):
    frag, defaults = build(code)
    prog = eng.program(frag, CAT, label)
    if prog is None:
        return None
    A, B = arrays(rc, a, b)
    Fs, Ts = _bleed(eng.to_shader(rc, A, "srgb"), eng.to_shader(rc, B, "srgb"))
    W, H = rc.width, rc.height
    samplers = {"from": Fs, "to": Ts}
    if "matte" in eng.uniforms(prog[0]):
        m = matte_rgba(rc, tr, ctx)
        if m is None:
            warn_once(CAT, f"{label}:matte", "shader samples `matte` but @matte is missing; transparent")
        else:
            samplers["matte"] = eng.to_shader(rc, m, "srgb")

    def attr_value(name, only_explicit):
        if name == "direction":
            if only_explicit and not (eng.explicit(rc, tr, "direction", ctx) or eng.explicit(rc, tr, "angle", ctx)):
                return None
            dx, dy = direction(rc, tr, ctx)
            u = eng.uniforms(prog[0])[name]
            return [dx, -dy] if u.dimension >= 2 else [math.degrees(math.atan2(dy, dx)) % 360.0]
        return eng.attribute_uniform(rc, tr, ctx, name, "srgb", only_explicit)

    acc = None
    ps = _progress_samples(rc, tr, ctx, p)
    for q in ps:
        builtins = {"progress": q, "ratio": W / H, "resolution": (W, H), "time": float(ctx.comp_t),
                    "frame": ctx.frame}
        eng.resolve_uniforms(rc, tr, ctx, prog[0], builtins=builtins, defaults=defaults, space="srgb",
                             attr_value=attr_value)
        px = eng.from_shader(rc, eng.draw(prog, W, H, samplers), "srgb")
        acc = px if acc is None else acc + px
    return out(acc / len(ps))
