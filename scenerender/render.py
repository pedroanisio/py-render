"""Public rendering API.

    from scenerender import render
    r = render.Renderer.open("scene.xml", scale=0.5)
    rgb = r.frame_rgb(3.0)          # (h, w, 3) uint8 at t = 3 s
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from . import document
from .compositor import RenderContext
from .evaluator import Evaluator
from .raster import working_to_rgb8
from .registry import FEATURES, PARTIAL, load_plugins
from .values import paint_ref, parse_color

log = logging.getLogger("scenerender")
FEATURES.declare("motionBlur", PARTIAL, "temporal supersampling of the whole frame; per-node motionBlur flags are ignored")


@dataclass
class Renderer:
    doc: document.Document
    rc: RenderContext

    @classmethod
    def open(cls, path: str, *, scale: float = 1.0, params: dict | None = None, variant: str | None = None,
             layout: str | None = None, strict: bool = False, motion_blur: bool | None = None,
             representation: str | None = None, assets_dir: str | None = None) -> "Renderer":
        load_plugins()
        doc = document.load(path, params=params, variant=variant, layout=layout, strict=strict, base=assets_dir)
        ev = Evaluator(doc)
        p = doc.project
        mb = (p.get("motionBlur") == "true") if motion_blur is None else motion_blur
        rc = RenderContext(doc, ev, scale=scale, motion_blur=mb)
        if representation:
            rc.cache["representation"] = representation
        for name, install in _HOOK_INSTALLERS:
            install(rc)
        return cls(doc, rc)

    @property
    def fps(self) -> float:
        return float(self.doc.fps)

    def frame_count(self, t0: float = 0.0, t1: float | None = None, fps: float | None = None) -> int:
        t1 = self.doc.duration if t1 is None else t1
        return int(math.ceil((t1 - t0) * (fps or self.fps) - 1e-9))

    def background(self):
        bg = self.doc.project.get("background", "#000000FF")
        if paint_ref(bg):
            return None
        c = parse_color(bg, self.doc.tokens, (0, 0, 0, 1))
        if self.rc.linear:
            from .raster import srgb_to_linear
            c = tuple(float(v) for v in srgb_to_linear(np.array(c[:3], np.float32))) + (c[3],)
        return c

    def frame_linear(self, t: float, frame: int = 0) -> np.ndarray:
        """Premultiplied working-space frame (h, w, 4) float32, before background."""
        rc = self.rc
        p = self.doc.project
        if not rc.motion_blur:
            return rc.render_frame(t, frame).px
        n = max(1, int(p.get("motionBlurSamples", 16)))
        shutter = float(p.get("shutterAngle", 180)) / 360.0 / self.fps
        phase = float(p.get("shutterPhase", -90)) / 360.0 / self.fps
        acc = None
        for i in range(n):
            ts = t + phase + shutter * (i + 0.5) / n
            px = rc.render_frame(ts, frame).px
            acc = px if acc is None else acc + px
        return acc / n

    def graded_background(self):
        """Project background after the finishing transform (the finish hook only sees rendered pixels)."""
        bg = self.background() or (0.0, 0.0, 0.0, 1.0)
        try:
            from .color import grade_color
        except ImportError:
            return bg
        return tuple(grade_color(self.rc, bg[:3])) + (bg[3],)

    def frame_rgb(self, t: float, frame: int | None = None) -> np.ndarray:
        frame = int(round(t * self.fps)) if frame is None else frame
        px = self.frame_linear(t, frame)
        return working_to_rgb8(px, self.rc.linear, self.graded_background())

    def frame_rgba(self, t: float, frame: int | None = None) -> np.ndarray:
        """Straight-alpha RGBA without the project background (for alpha outputs)."""
        frame = int(round(t * self.fps)) if frame is None else frame
        px = self.frame_linear(t, frame)
        rgb = working_to_rgb8(px, self.rc.linear)
        a = (np.clip(px[..., 3:4], 0, 1) * 255 + 0.5).astype(np.uint8)
        return np.concatenate([rgb, a], -1)


# Optional modules add hooks (captions burn-in, finishing/colour management, camera, audio levels).
_HOOK_INSTALLERS: list = []


def hook_installer(name: str):
    def deco(fn):
        _HOOK_INSTALLERS.append((name, fn))
        return fn
    return deco
