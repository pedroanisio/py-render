"""Extension registries and the support matrix.

Every renderable concept of the schema (node kind, asset kind, effect type,
transition type, blend mode, shape modifier, deform modifier, audio effect,
caption preset, output codec, ...) is registered here with a support level.
Anything a document uses that has no handler is reported once and skipped,
and `scenerender coverage` reports the declared support level of the concepts
it recognizes. This is not a conformance proof for every attribute combination
or for features inside external asset files.

Handler signatures (see compositor.RenderContext for `rc`):
  asset(rc, asset_el, M, ctx, *, layer=None, src_t=0.0, clip=None) -> Buf | None
      M maps asset-local pixels (0..w, 0..h) to frame pixels.
  asset_size(rc, asset_el, ctx) -> (w, h)
  effect(rc, effect_el, buf, ctx, node=None) -> Buf        (mix/enabled handled by the caller)
  transition(rc, tr_el, a, b, p, ctx) -> Buf               (a/b: full-frame Buf or None, p eased 0..1)
  blend(dst_rgba, src_rgba) -> rgba                        (premultiplied float arrays of equal shape)
  shape_modifier(rc, mod_el, cmds, ctx) -> list[cmds]      (returns one or more paths)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("scenerender")

FULL, PARTIAL, NONE = "full", "partial", "none"


@dataclass
class Entry:
    fn: Callable | None
    level: str
    note: str = ""


class Registry:
    def __init__(self, category: str):
        self.category = category
        self.entries: dict[str, Entry] = {}

    def register(self, *names: str, level: str = FULL, note: str = ""):
        def deco(fn):
            for n in names:
                self.entries[n] = Entry(fn, level, note)
            return fn
        return deco

    def declare(self, name: str, level: str = NONE, note: str = "") -> None:
        """Record a known concept without a handler (level none) or one handled inline elsewhere."""
        self.entries.setdefault(name, Entry(None, level, note))

    def get(self, name: str | None) -> Callable | None:
        e = self.entries.get(name or "")
        return e.fn if e else None

    def level(self, name: str) -> str:
        e = self.entries.get(name)
        return e.level if e else NONE


NODES = Registry("node")
ASSETS = Registry("asset")
ASSET_SIZES = Registry("asset-size")
EFFECTS = Registry("effect")
TRANSITIONS = Registry("transition")
BLENDS = Registry("blend")
SHAPE_MODIFIERS = Registry("shapeModifier")
DEFORMERS = Registry("deform")
TEXT_ANIMATORS = Registry("textAnimator")
AUDIO_EFFECTS = Registry("audioEffect")
CAPTION_PRESETS = Registry("caption")
CODECS = Registry("codec")
FEATURES = Registry("feature")    # cross-cutting features: masks, mattes, threeD, physics, motionBlur, ...

ALL = [NODES, ASSETS, EFFECTS, TRANSITIONS, BLENDS, SHAPE_MODIFIERS, DEFORMERS, TEXT_ANIMATORS,
       AUDIO_EFFECTS, CAPTION_PRESETS, CODECS, FEATURES]

_warned: set[tuple[str, str]] = set()


def warn_once(category: str, name: str, msg: str | None = None) -> None:
    key = (category, name)
    if key not in _warned:
        _warned.add(key)
        log.warning("%s %r: %s", category, name, msg or "not supported by the Python renderer; skipped")


def load_plugins() -> None:
    """Import the modules that register handlers."""
    import importlib
    import pkgutil

    from . import assets, effects, nodes, transitions  # noqa: F401
    for pkg in (nodes, assets, effects, transitions):
        for m in pkgutil.iter_modules(pkg.__path__):
            importlib.import_module(f"{pkg.__name__}.{m.name}")
    from . import blend  # noqa: F401
    for opt in ("audio", "captions", "output", "color", "constraints", "text_animators", "camera",
                "physics", "deform", "modifiers"):
        try:
            importlib.import_module(f"scenerender.{opt}")
        except ModuleNotFoundError as e:
            if e.name != f"scenerender.{opt}":
                raise
