"""Audio: the audioMix renderer and audio-driven animation.

    from scenerender.audio import mixer_for
    m = mixer_for(renderer)             # cached per Renderer
    pcm = m.render(t0, t1)              # float32 (n, channels) at m.sr

Installs `rc.ev.audio_amplitude(id, band, t)` (link source="audio:id[:low|mid|high]" and the
expression audioAmplitude(id[, band])): the RMS envelope (100 Hz, 20 ms windows, linear
amplitude) of an audioTrack (post-fader, pre-bus), a bus, "master", or an asset id (the sum of
the tracks that play it, else the raw asset from t = 0). Bands: low < 250 Hz, mid 250-4000 Hz,
high > 4 kHz (Butterworth 24 dB/oct). The mix is computed lazily on first use.

Modules: mix (signal flow, fitToDuration, transition/layer audio), effects (audioEffect types),
dsp (filters, dynamics, BS.1770 / EBU R128 loudness, limiter), spectral (phase vocoder,
pitch-shift, formants, spectral gate), spatial (layouts, VBAP, ambisonics), io (decode, WAV).
"""
from __future__ import annotations

import re

import numpy as np

from ..document import ln
from ..registry import ASSETS, FULL
from ..render import hook_installer
from . import effects  # noqa: F401  (registers AUDIO_EFFECTS)
from .io import decode, write_wav  # noqa: F401
from .mix import ENV_HZ, Mixer  # noqa: F401


ASSETS.declare("audio", FULL, "played through audioMix (not drawable)")


def mixer_for(obj, sample_rate: int | None = None) -> Mixer:
    """The Mixer of a Renderer or RenderContext (created on first use, cached in rc.cache)."""
    rc = getattr(obj, "rc", obj)
    key = ("audio_mixer", sample_rate)
    m = rc.cache.get(key)
    if m is None:
        m = rc.cache[key] = Mixer(rc.doc, rc.ev, rc, sample_rate=sample_rate)
    return m


@hook_installer("audio")
def _install(rc) -> None:
    def amplitude(track_id, band, t):
        table = rc.cache.get("audio_envelopes")
        if table is not None:
            env = table.get((str(track_id), band or ""))
            if env is not None:
                x = t * ENV_HZ - 0.5
                return float(np.interp(x, np.arange(len(env)), env, left=0.0, right=0.0)) if len(env) else 0.0
        return mixer_for(rc).amplitude(str(track_id), band, t)
    rc.ev.audio_amplitude = amplitude


_EXPR = re.compile(r"audioAmplitude\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*['\"](\w+)['\"])?")


def referenced_amplitudes(doc) -> set[tuple[str, str]]:
    """(id, band) pairs the document reads through links, expressions or audiogram assets."""
    out: set[tuple[str, str]] = set()
    for el in doc.root.iter():
        if not isinstance(el.tag, str):
            continue
        tag = ln(el)
        if tag == "link" and (el.get("source") or "").startswith("audio:"):
            parts = el.get("source").split(":")
            out.add((parts[1], parts[2] if len(parts) > 2 else ""))
        elif tag == "expression" or el.get("condition"):
            for m in _EXPR.finditer((el.text or "") + " " + (el.get("condition") or "")):
                out.add((m.group(1), m.group(2) or ""))
        elif tag == "audiogram" and el.get("source"):
            for b in ("", "low", "mid", "high"):
                out.add((el.get("source"), b))
    return out


def envelope_table(renderer) -> dict:
    """Precomputed envelopes for every amplitude the document references (for worker processes)."""
    refs = referenced_amplitudes(renderer.doc)
    if not refs:
        return {}
    m = mixer_for(renderer)
    return {(i, b): m.envelope(i, b or None) for i, b in refs}


def install_envelopes(rc, table: dict) -> None:
    rc.cache["audio_envelopes"] = table
