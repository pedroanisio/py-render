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
from .registry import FEATURES, FULL, load_plugins
from .values import parse_bool, paint_ref, parse_color

log = logging.getLogger("scenerender")
FEATURES.declare("motionBlur", FULL, "shutter supersampling (angle, phase, samples, adaptive skip of still frames); "
                                     "node motionBlur on/off/inherit honoured")


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
        mb = (parse_bool(p.get("motionBlur"))) if motion_blur is None else motion_blur
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
            from .raster import color_to_working
            self.rc.install_working_primaries()
            c = color_to_working(c, True)
        return c

    def frame_linear(self, t: float, frame: int = 0) -> np.ndarray:
        """Premultiplied working-space frame (h, w, 4) float32, before background."""
        rc = self.rc
        p = self.doc.project
        if not rc.motion_blur:
            return rc.render_frame(t, frame).px
        n = max(1, int(p.get("motionBlurSamples", 16)))
        base = float(p.get("shutterAngle", 180))
        sa = rc.hooks.get("shutter_angle")
        shutter = (sa(rc, t, base) if sa else base) / 360.0 / self.fps   # active camera may override
        phase = float(p.get("shutterPhase", -90)) / 360.0 / self.fps
        times = [t + phase + shutter * (i + 0.5) / n for i in range(n)]
        rc.mb_center = t
        try:
            if n > 2 and parse_bool(p.get("adaptiveMotionBlur", "true"), True):
                # Matching endpoints alone cannot establish a still frame: motion
                # may return to its starting position during the shutter.
                first, last = rc.render_frame(times[0], frame).px, rc.render_frame(times[-1], frame).px
                if (self._static_shutter(times[0], times[-1]) and first.shape == last.shape
                        and float(np.abs(first - last).max(initial=0.0)) < 0.5 / 255):
                    return (first + last) / 2
                acc = first + last
                for ts in times[1:-1]:
                    acc = acc + rc.render_frame(ts, frame).px
                return acc / n
            acc = None
            for ts in times:
                px = rc.render_frame(ts, frame).px
                acc = px if acc is None else acc + px
        finally:
            rc.mb_center = None
        return acc / n

    def _static_shutter(self, first: float, last: float) -> bool:
        """Conservatively prove the scene has no time-varying input in this interval.

        Unknown/procedural cases use all requested samples. This is an optimization
        only: a return motion, short flash or held-key change must never disappear.
        """
        from .document import NODE_TAGS, ln
        if self.doc.clock_shift:
            return False
        dynamic = {"expression", "link", "motionPath", "particleEmitter", "physics", "rigidBody", "softBody",
                   "deform", "shapeModifier", "shake", "textAnimator", "effect", "transition", "instance",
                   "video", "imageSequence", "lottie", "audiogram", "generator", "generated", "captions"}
        for el in self.doc.root.iter():
            tag = ln(el)
            if tag == "expression" and not parse_bool(el.get("enabled"), True):
                continue
            if tag in dynamic or el.get("condition"):
                return False
            if tag in NODE_TAGS:
                start, end = self.doc.window(el)
                if first < start <= last or (end is not None and first < end <= last):
                    return False
                if float(el.get("timeOffset", 0)) != 0 or float(el.get("timeScale", 1)) != 1:
                    return False
            if tag != "animate":
                continue
            owner = el.getparent()
            keys = self.rc.ev.keys(owner, el, el.get("property"))
            if not keys:
                continue
            lo, hi = first, last
            base = el.get("timeBase", "composition")
            start, end = self.doc.window(owner)
            if base in ("local", "normalized"):
                lo, hi = lo - start, hi - start
                if base == "normalized":
                    span = end - start if end is not None else 0
                    lo, hi = (lo / span, hi / span) if span > 0 else (0, 0)
            if hi <= keys[0].time and el.get("extrapolateBefore", "hold") == "hold":
                continue
            if lo >= keys[-1].time and el.get("extrapolateAfter", "hold") == "hold":
                continue
            # Identical scalar/colour keys are constant unless spatial handles
            # describe a loop between identical positions.
            if (all(k.value == keys[0].value for k in keys)
                    and not any(k.el.get("spatialIn") or k.el.get("spatialOut") for k in keys)):
                continue
            return False
        return True

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
