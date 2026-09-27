"""Shared headless OpenGL context (moderngl over EGL) and Buf <-> texture helpers.

One context per process, created lazily. Everything GPU-side (GLSL effects and
transitions, the 3D renderer, 360 projection) goes through `context()`.
Textures carry premultiplied float working-space pixels (RGBA32F); row 0 of a
texture is the top row of the Buf (callers flip v in shaders or not at all —
all helpers here keep the Buf orientation).
"""
from __future__ import annotations

import logging
import os

import numpy as np

from .raster import Buf

log = logging.getLogger("scenerender")
_ctx = None
_failed: str | None = None


class GLUnavailable(RuntimeError):
    pass


def context():
    """The process-wide moderngl context; raises GLUnavailable if no GL can be created."""
    global _ctx, _failed
    if _ctx is not None:
        return _ctx
    if _failed:
        raise GLUnavailable(_failed)
    import moderngl
    errors = []
    backends = [os.environ["SCENERENDER_GL_BACKEND"]] if os.environ.get("SCENERENDER_GL_BACKEND") else ["egl", None]
    for backend in backends:
        try:
            _ctx = moderngl.create_standalone_context(require=330, **({"backend": backend} if backend else {}))
            log.info("GL context: %s (%s)", _ctx.info.get("GL_RENDERER"), backend or "default")
            return _ctx
        except Exception as e:  # noqa: BLE001 — try the next backend
            errors.append(f"{backend or 'default'}: {e}")
    _failed = "; ".join(errors)
    raise GLUnavailable(_failed)


def available() -> bool:
    try:
        context()
        return True
    except GLUnavailable:
        return False


def reset_after_fork() -> None:
    """Drop the inherited context in a forked/spawned worker (contexts are not fork-safe)."""
    global _ctx, _failed
    _ctx, _failed = None, None


def texture_from(px: np.ndarray):
    """(h, w, 4) float32 -> RGBA32F texture, linear filtering, clamp to edge."""
    import moderngl
    ctx = context()
    h, w = px.shape[:2]
    tex = ctx.texture((w, h), 4, np.ascontiguousarray(px, np.float32).tobytes(), dtype="f4")
    tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
    tex.repeat_x = tex.repeat_y = False
    return tex


def framebuffer(w: int, h: int, samples: int = 0, depth: bool = False):
    ctx = context()
    color = ctx.renderbuffer((w, h), 4, samples=samples, dtype="f4") if samples else ctx.texture((w, h), 4, dtype="f4")
    db = ctx.depth_renderbuffer((w, h), samples=samples) if depth else None
    return ctx.framebuffer(color_attachments=[color], depth_attachment=db)


def read_rgba(fbo, w: int, h: int) -> np.ndarray:
    """Read an RGBA32F framebuffer into (h, w, 4) float32 with row 0 at the top."""
    data = np.frombuffer(fbo.read(components=4, dtype="f4"), np.float32).reshape(h, w, 4)
    return np.ascontiguousarray(data[::-1])


def to_texture_buf(buf: Buf):
    """Texture of a Buf with row 0 at the bottom (GL convention), so v=1 is the Buf's top row."""
    return texture_from(np.ascontiguousarray(buf.px[::-1]))


FULLSCREEN_VS = """
#version 330
in vec2 in_pos;
out vec2 uv;
void main() { uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""


def fullscreen_quad(prog):
    ctx = context()
    vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], np.float32).tobytes())
    return ctx.vertex_array(prog, [(vbo, "2f", "in_pos")])
