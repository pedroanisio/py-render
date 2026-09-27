"""The audio mix: audioTrack -> bus -> ... -> master, plus the audio of video layers.

Signal flow per audioTrack (all pinned here; the schema leaves the order open):
    source region (clipIn/clipOut) -> reverse -> speed (preservePitch: phase-locked phase-vocoder
        time-stretch, spectral.time_stretch; else band-limited resampling, pitch follows speed)
    -> loop (loop + 1 plays, sample-exact tiling) or fitToDuration (bar-aligned edit, below)
    -> fadeIn/fadeOut (fadeCurve)                 on the placed region
    -> audioEffect chain (document order)         in the source's channels, with room for tails
    -> fader: volume (unit) x gain (dB) x mute x transition gain (transitionAudio)
    -> pan / spatialisation into the audioMix layout (spatial.py)
    -> ducking (duckUnder sources vs duckThreshold, slewed at duckAmount/duckAttack|duckRelease dB/s)
    -> bus (or master)
Automation of volume/gain/pan/mute (the audio automation of the XSD) is sampled every 10 ms and
interpolated per sample; the other track attributes are read once at the track start.
A bus sums its tracks and child buses, then effects -> fader -> pan -> ducking -> its output bus.
The master sums everything unrouted, then effects -> volume -> normalize -> true-peak limiter at
@truePeak (4x oversampled; whenever normalizing or limiter="true").  Dither: io.write_wav.

normalize="integrated": ITU-R BS.1770-4 integrated loudness of the full mix, static gain to
@loudness, re-measured after the limiter (up to 3 passes, +6 dB) so the result meets the target.
normalize="dynamic": EBU R128 / broadcast AGC (dsp.dynamic_gain_db): the gain follows @loudness -
gated short-term loudness (3 s window centred on the time = offline look-ahead; only 100 ms blocks
above max(-70, loudness - 30) LUFS count, and windows with < 0.5 s of programme hold the gain, so
pauses are neither pumped up nor dilute the measurement), gain range -30..+20 dB, one-pole
smoothing in dB with 0.5 s attack (gain falling) / 2 s release (gain rising), then the true-peak
limiter at @truePeak.

fitToDuration (music edit, pinned): the bar length B = beatsPerBar x 60 / bpm (asset @bpm, else the
bpm of a beatGrid whose @source is the asset, else of the first beatGrid; beatsPerBar from that
grid, else 4), divided by speed.  Bars are counted from the start of the played region (clipIn is
a downbeat) and the output bar grid starts at the track start, so every edit lands on a bar line
of both the source and the output.  With M whole output bars before the project end, output bars
0..M-2 play source bars 0, 1, ..., nb-2 (the body; looping back to bar 0 when exhausted, or
dropping the bars after bar M-2 when the source is longer) and output bar M-1 plays the source's
last bar followed by its ring-out (the audio after the last whole bar), truncated at the project
end with a 10 ms fade when it is longer.  Every non-contiguous splice is a 20 ms equal-power
crossfade centred on the bar line.  The region always ends exactly on the project's last sample.
Without a tempo the whole region is looped with crossfaded seams and trimmed at the end.

transitionAudio for audioTracks (pinned ownership rule): a track follows node N when N is the
from/to of a transition, the track's start (startMarker/start) lies in N's window [start, end),
and the track's role is not "music" (music beds span scenes).  When several nodes qualify, the one
that starts latest owns it.  @audio crossfade / equal-power fade the owning track over the
transition window (from: out, to: in; a from-track is silent after the window, a to-track before
it), cut switches at the cut point, none leaves it alone.  The gain applies at the fader.

Video-layer audio (volume, mute, audioBus, transitions @audio) plays on the layer's own clock,
including layers inside symbols reached through instances: every ancestor's window, visibility,
condition, sequence clock shift, group timeOffset/timeScale and instance clock (speed, clipIn/
clipOut, loop, reverse, timeRemap, overrides) is applied as the compositor does, then the layer's
media_time (speed/loop/reverse/timeRemap/freezeAt) maps to the source (varispeed resampling).

Surround / ambisonics: see spatial.py (layouts, channel order, pan -> azimuth, VBAP, ACN/SN3D).
`Mixer.layout.ffmpeg` / `.wav_mask` give the ffmpeg layout name and the WAV channel mask.
"""
from __future__ import annotations

import logging
import math
import subprocess
import wave
from dataclasses import dataclass, replace

import numpy as np

from ..document import ln
from ..evaluator import ANIM_TAGS, Ctx
from ..registry import FEATURES, FULL, warn_once
from . import dsp, spectral
from .effects import FxContext, process_chain, tail_seconds
from .io import decode, ffmpeg_exe
from .spatial import Layout, balance_gains, pan_to_azimuth, rotate_ambisonic
from ..values import parse_bool

log = logging.getLogger("scenerender")

FEATURES.declare("audio:mix", FULL, "tracks, buses, master; volume/gain/pan/mute automation at 100 Hz")
FEATURES.declare("audio:ducking", FULL, "RMS (10 ms) key vs duckThreshold; gain slews duckAmount dB over duckAttack/duckRelease")
FEATURES.declare("audio:normalize-integrated", FULL, "ITU-R BS.1770-4 gated integrated loudness, static gain, 4x-oversampled true-peak limiter")
FEATURES.declare("audio:normalize-dynamic", FULL, "EBU R128 gated short-term (3 s, centred) loudness AGC toward @loudness, 0.5 s/2 s attack/release, pause hold, true-peak limiter")
FEATURES.declare("audio:fitToDuration", FULL, "bar-aligned edit (asset bpm / beatGrid): body bars looped or trimmed, the last bar + ring-out ends on the project's last sample; 20 ms equal-power seams")
FEATURES.declare("audio:preservePitch", FULL, "speed != 1 time-stretches with a phase-locked phase vocoder (pitch kept); preservePitch=false resamples")
FEATURES.declare("audio:surround", FULL, "mono/stereo/5.1/7.1/7.1.4 (2D VBAP, ffmpeg channel order) and ambisonic-1/3 (ACN/SN3D encoding); pan -> azimuth")
FEATURES.declare("audio:layerAudio", FULL, "video layer audio (volume, mute, audioBus, transitions) incl. layers in symbols on the instance clock")
FEATURES.declare("transitionAudio", FULL, "crossfade/equal-power/cut/none on video-layer audio and on the audioTracks the transitioned nodes own")

CTRL_HZ = 100.0      # automation control rate
ENV_HZ = 100.0       # audio_amplitude envelope rate
SEAM = 0.02          # fitToDuration crossfade (s)
END_FADE = 0.01      # fitToDuration truncation fade (s)


@dataclass
class Segment:
    i0: int
    data: np.ndarray          # (n, ch)

    @property
    def i1(self) -> int:
        return self.i0 + self.data.shape[0]


def _has_anim(el, prop: str) -> bool:
    return any(ln(c) in ANIM_TAGS and c.get("property") == prop for c in el)


def probe_channels(path: str, stream: int | None = None) -> int | None:
    """Channel count of a file's audio (WAV header, else ffmpeg's stream info)."""
    if stream is None and path.lower().endswith((".wav", ".wave")):
        try:
            with wave.open(path, "rb") as w:
                return w.getnchannels()
        except (wave.Error, EOFError, OSError):
            pass
    try:
        p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", path], capture_output=True, text=True)
    except OSError:
        return None
    k = 0
    for line in p.stderr.splitlines():
        if "Audio:" not in line:
            continue
        if stream is not None and k != stream:
            k += 1
            continue
        parts = [s.strip() for s in line.split("Audio:", 1)[1].split(",")]
        for s in parts[1:4]:
            s = s.split("(")[0].strip()
            if s == "mono":
                return 1
            if s == "stereo":
                return 2
            if s.endswith("channels") and s.split()[0].isdigit():
                return int(s.split()[0])
            try:
                return Layout(s).n
            except KeyError:
                continue
        return None
    return None


def transition_window(doc, tr) -> tuple[float, float, float] | None:
    """(start, end, cut) of a transition (the compositor's rule, usable without a RenderContext)."""
    from ..evaluator import Evaluator
    from ..scheduling import transition_window as evaluated_window
    return evaluated_window(Evaluator(doc), tr, Ctx(0, 0))


def transition_gain(tt: np.ndarray, trs) -> np.ndarray:
    """Gain over times tt for [(window, mode, role)] (role 'from' fades out, 'to' fades in)."""
    g = np.ones(len(tt))
    for (w0, w1, cut), mode, role in trs:
        if mode == "none":
            continue
        if mode == "cut":
            g[(tt >= cut) if role == "from" else (tt < cut)] = 0.0
            continue
        p = np.clip((tt - w0) / max(1e-9, w1 - w0), 0.0, 1.0)
        if mode == "equal-power":
            g *= np.where(p >= 1, 0.0, np.cos(p * math.pi / 2)) if role == "from" else np.sin(p * math.pi / 2)
        else:
            g *= (1 - p) if role == "from" else p
    return g


def _in_symbol(el) -> bool:
    return any(isinstance(a.tag, str) and ln(a) in ("symbol", "symbols") for a in el.iterancestors())


class Mixer:
    def __init__(self, doc, ev, rc=None, representation: str | None = None, sample_rate: int | None = None):
        self.doc, self.ev, self.rc = doc, ev, rc
        self.representation = representation or (rc.cache.get("representation") if rc is not None else None)
        am = doc.section("audioMix")
        self.el = am
        sr = int(am.get("sampleRate", 48000)) if am is not None else 48000
        self.sr = int(sample_rate or sr)
        ch = int(am.get("channels", 2)) if am is not None else 2
        self.layout, warn = Layout.resolve(am.get("channelLayout", "auto") if am is not None else "auto", ch)
        if warn:
            warn_once("audio", "channelLayout", warn)
        self.nch = self.layout.n
        self.spatial = self.nch > 2
        self.bits = int(am.get("bitDepth", 24)) if am is not None else 24
        self.n = int(math.ceil(doc.duration * self.sr))
        kids = list(am) if am is not None else []
        self.tracks = {t.get("id"): t for t in kids if ln(t) == "audioTrack"}
        self.buses = {b.get("id"): b for b in kids if ln(b) == "bus"}
        self.master_el = next((m for m in kids if ln(m) == "master"), None)
        self._decoded: dict = {}
        self._track_out: dict = {}
        self._bus_out: dict = {}
        self._master = None
        self._layer_segs = None
        self._stack: set = set()
        self._env: dict = {}
        self.stats: dict = {}

    # ------------------------------------------------------------ helpers
    @property
    def seed(self) -> int:
        return int(self.doc.seed)

    @property
    def ffmpeg_layout(self) -> str:
        return self.layout.ffmpeg

    def ctx(self, t: float, s: float = 0.0, e: float | None = None) -> Ctx:
        return Ctx(t=t, comp_t=t, frame=int(t * float(self.doc.fps)), node_start=s, node_end=e)

    def value(self, el, prop: str, t: float, default: float, s: float = 0.0, e: float | None = None) -> float:
        return self.ev.num(el, prop, self.ctx(t, s, e), default)

    def curve(self, el, prop: str, default: float, i0: int, n: int, s: float = 0.0, e: float | None = None):
        """Per-sample values of el/@prop over samples [i0, i0 + n): a scalar when not animated."""
        if self.ev.kind(el, prop) == "bool":
            if not _has_anim(el, prop):
                return float(self.ev.bool(el, prop, self.ctx(i0 / self.sr, s, e), bool(default)))
            # Boolean switches are discrete; interpolating a control-rate curve
            # creates a fade that the document did not request.
            return np.fromiter((self.ev.bool(el, prop, self.ctx((i0 + i) / self.sr, s, e), bool(default))
                                for i in range(n)), dtype=np.float32, count=n)
        if not _has_anim(el, prop):
            return self.value(el, prop, i0 / self.sr, default, s, e)
        step = self.sr / CTRL_HZ
        k = int(math.ceil(n / step)) + 2
        ts = (i0 + np.arange(k) * step) / self.sr
        vals = np.array([self.value(el, prop, float(t), default, s, e) for t in ts])
        return np.interp(np.arange(n), np.arange(k) * step, vals)

    def asset_path(self, asset) -> str:
        src = asset.get("src")
        if self.representation:
            for r in asset:
                if ln(r) == "representation" and r.get("name") == self.representation:
                    src = r.get("src")
        return self.doc.resolve_path(src)

    def source_path(self, asset) -> tuple[str | None, int | None]:
        """(file, audio stream index) behind an audio/video/generated asset.
        Prefers scenerender.assets.audio_source_path (representations, verified generated caches,
        extracted video audio); falls back to the asset's own src."""
        try:
            from ..assets import audio_source_path
        except ImportError:
            audio_source_path = None
        if audio_source_path is not None:
            try:
                p = audio_source_path(self.rc if self.rc is not None else self.doc, asset)
            except Exception as e:  # noqa: BLE001
                log.debug("audio_source_path(%s): %s", asset.get("id"), e)
                p = None
            if p:
                return p, None
            if ln(asset) != "audio":
                return None, None
        if ln(asset) == "video":
            return self.asset_path(asset), int(asset.get("audioStream", 0))
        if ln(asset) == "audio":
            return self.asset_path(asset), None
        return None, None

    def source_channels(self, asset, path: str | None, stream: int | None) -> int:
        """Channels a source is processed in: the output's for mono/stereo mixes; for surround and
        ambisonic mixes 1 (mono), 2 (stereo, also the downmix of other layouts) or the output's own
        count (discrete pass-through)."""
        if not self.spatial:
            return self.nch
        native = int(asset.get("channels")) if asset.get("channels") else (probe_channels(path, stream) if path else None)
        if native == self.nch:
            return native
        return 1 if native == 1 else 2

    def decode_asset(self, asset, stream: int | None = None):
        key = asset.get("id")
        hit = self._decoded.get(key)
        if hit is None:
            path, stream = self.source_path(asset)
            ch = self.source_channels(asset, path, stream)
            if path is None:
                warn_once("audio-asset", asset.get("id"), "no audio source (missing file, no audio stream or unverified cache)")
                hit = np.zeros((0, ch), np.float32)
            else:
                try:
                    hit = decode(path, self.sr, ch, stream)
                except (OSError, ValueError) as e:
                    warn_once("audio-asset", asset.get("id"), f"cannot decode {path}: {e}")
                    hit = np.zeros((0, ch), np.float32)
            self._decoded[key] = hit
        return hit

    def tempo(self, asset) -> tuple[float | None, int]:
        """(bpm, beatsPerBar) for an asset: @bpm, else a beatGrid with @source = asset, else the first grid."""
        grids = []
        mk = self.doc.section("markers")
        if mk is not None:
            grids = [g for g in mk if ln(g) == "beatGrid"]
        own = next((g for g in grids if asset is not None and g.get("source") == asset.get("id")), None)
        grid = own if own is not None else (grids[0] if grids else None)
        bpm = float(asset.get("bpm")) if asset is not None and asset.get("bpm") else (float(grid.get("bpm")) if grid is not None else None)
        per_bar = int(grid.get("beatsPerBar", 4)) if grid is not None else 4
        return bpm, per_bar

    def fx_ctx_maker(self, t0: float, layout: Layout | None = None, bpm: float | None = None):
        if bpm is None:
            bpm = self.tempo(None)[0]

        def make(el):
            def get(name, default):
                for p in el:
                    if ln(p) == "param" and p.get("name") == name:
                        try:
                            return float(p.get("value"))
                        except (TypeError, ValueError):
                            return p.get("value")
                if not self.ev.explicit(el, name, self.ctx(t0)) and default is None:
                    return None
                return self.ev.get(el, name, self.ctx(t0), default)

            def curve(name, default, n):
                v = self.curve(el, name, default, int(round(t0 * self.sr)), n)
                return np.full(n, v) if np.isscalar(v) else v

            def key(kid, n):
                if not kid:
                    return None
                sig = self.node_full(kid)
                if sig is None:
                    return None
                i0 = int(round(t0 * self.sr))
                out = np.zeros((n, sig.shape[1]), np.float32)
                a, b = max(0, i0), min(self.n, i0 + n)
                if b > a:
                    out[a - i0:b - i0] = sig[a:b]
                return out
            return FxContext(el, self.sr, t0, get, curve, key, self.seed, bpm, layout)
        return make

    # ------------------------------------------------------------ tracks
    def track_window(self, el) -> tuple[float, float] | None:
        """Composition-time region (start, end) the track's source occupies (without effect tails)."""
        asset = self.doc.ids.get(el.get("asset"))
        if asset is None:
            return None
        info = self._track_geometry(el, asset)
        return None if info is None else (info["start"], info["start"] + info["dur"])

    def _track_geometry(self, el, asset):
        src = self.decode_asset(asset)
        src_len = src.shape[0] / self.sr
        start = self.doc.markers.get(el.get("startMarker")) if el.get("startMarker") else None
        if start is None:
            start = self.value(el, "start", 0.0, 0.0)
        clip_in = max(0.0, self.value(el, "clipIn", start, 0.0))
        clip_out = self.value(el, "clipOut", start, src_len) if el.get("clipOut") else src_len
        clip_out = min(clip_out, src_len)
        speed = self.value(el, "speed", start, 1.0)
        reverse = (parse_bool(el.get("reverse"))) ^ (speed < 0)
        speed = abs(speed) or 1.0
        span = clip_out - clip_in
        if span <= 1e-6:
            return None
        plays = int(self.value(el, "loop", start, 0.0)) + 1
        dur = plays * span / speed
        fit = parse_bool(el.get("fitToDuration"))
        if fit:
            dur = (self.n - int(round(start * self.sr))) / self.sr
            if dur <= 0:
                return None
        return {"src": src, "start": start, "clip_in": clip_in, "span": span, "speed": speed, "reverse": reverse,
                "plays": plays, "dur": dur, "fit": fit, "pitch": parse_bool(el.get("preservePitch", "true"), True)}

    def _one_play(self, g) -> np.ndarray:
        """One pass over the region: clipIn..clipOut, reversed, at speed (stretched or resampled)."""
        src, sr = g["src"], self.sr
        ci, span_n = g["clip_in"] * sr, g["span"] * sr
        if abs(ci - round(ci)) < 1e-6 and abs(span_n - round(span_n)) < 1e-6:
            a, b = int(round(ci)), int(round(ci + span_n))
            one = src[a:b]
            if one.shape[0] < b - a:
                one = np.concatenate([one, np.zeros((b - a - one.shape[0], src.shape[1]), np.float32)])
        else:
            one = dsp.interp_positions(src, ci + np.arange(int(round(span_n))), cutoff=1.0)
        if g["reverse"]:
            one = one[::-1]
        sp = g["speed"]
        if abs(sp - 1.0) > 1e-9:
            if g["pitch"]:
                one = spectral.time_stretch(one, sp, sr)
            else:
                m = int(round(one.shape[0] / sp))
                one = dsp.interp_positions(one, np.arange(m) * sp, cutoff=min(1.0, 1.0 / sp) * 0.98)
        return np.ascontiguousarray(one, np.float32)

    def _read_region(self, g, asset=None) -> np.ndarray:
        n = int(round(g["dur"] * self.sr))
        one = self._one_play(g)
        ch = one.shape[1]
        if n <= 0 or one.shape[0] == 0:
            return np.zeros((max(0, n), ch), np.float32)
        if g["fit"]:
            return self._fit(one, n, asset, g["speed"])
        reps = int(math.ceil(n / one.shape[0]))
        out = np.tile(one, (reps, 1))[:n] if reps > 1 else one[:n]
        if out.shape[0] < n:
            out = np.concatenate([out, np.zeros((n - out.shape[0], ch), np.float32)])
        return np.ascontiguousarray(out, np.float32)

    def _fit(self, P: np.ndarray, n: int, asset, speed: float) -> np.ndarray:
        """fitToDuration: assemble exactly n samples from bars of P (see the module docstring)."""
        sr = self.sr
        bpm, per_bar = self.tempo(asset)
        Bs = per_bar * 60.0 / bpm / speed * sr if bpm else 0.0
        L = P.shape[0]
        nb = int(math.floor(L / Bs + 1e-6)) if Bs > 0 else 0
        # pieces: (output start, source start, length) in samples
        pieces: list[list[int]] = []
        if nb < 1:
            if bpm is None:
                warn_once("audio", "fit-no-bpm", "fitToDuration without asset @bpm or beatGrid: region looped with crossfades and trimmed")
            o = 0
            while o < n:
                pieces.append([o, 0, min(L, n - o)])
                o += L
        else:
            bar = lambda k: int(round(k * Bs))  # noqa: E731
            M = int(math.floor(n / Bs + 1e-9))
            if M == 0:
                pieces.append([0, 0, min(L, n)])
            else:
                body = list(range(max(1, nb - 1)))
                seq = [body[j % len(body)] for j in range(M - 1)] + [nb - 1]
                for j, b in enumerate(seq):
                    o0, o1 = bar(j), bar(j + 1)
                    s0 = bar(b)
                    if j == M - 1:
                        o1 = n                       # the ending runs on into its ring-out
                    ln_ = min(o1 - o0, L - s0)
                    if pieces and pieces[-1][0] + pieces[-1][2] == o0 and pieces[-1][1] + pieces[-1][2] == s0:
                        pieces[-1][2] += ln_
                    else:
                        pieces.append([o0, s0, ln_])
        out = np.zeros((n, P.shape[1]), np.float32)
        X = max(2, int(round(SEAM * sr)))
        h = X // 2
        for i, (o, s, ln_) in enumerate(pieces):
            pre = h if i > 0 else 0                              # seam before this piece
            post = h if i + 1 < len(pieces) else 0               # seam after it
            a_src, b_src = s - pre, s + ln_ + post
            a_out = o - pre
            seg = np.zeros((b_src - a_src, P.shape[1]), np.float32)
            lo, hi = max(0, a_src), min(L, b_src)
            if hi > lo:
                seg[lo - a_src:hi - a_src] = P[lo:hi]
            w = np.ones(seg.shape[0])
            if pre:
                w[:X] = np.sin(np.linspace(0, 1, X, endpoint=False) * math.pi / 2 + math.pi / (4 * X))
            if post:
                w[-X:] = np.cos(np.linspace(0, 1, X, endpoint=False) * math.pi / 2 + math.pi / (4 * X))
            seg = seg * w[:, None].astype(np.float32)
            oa, ob = max(0, a_out), min(n, a_out + seg.shape[0])
            if ob > oa:
                out[oa:ob] += seg[oa - a_out:ob - a_out]
        # trimmed at the project end: short fade when source audio continues past it
        last_o, last_s, last_l = pieces[-1]
        if last_s + last_l < L:
            f = min(n, max(1, int(round(END_FADE * sr))))
            out[n - f:] *= (1 - (np.arange(f) + 1) / f)[:, None].astype(np.float32)
        self.stats.setdefault("fit", {})[asset.get("id") if asset is not None else "?"] = pieces
        return out

    def track(self, tid: str) -> Segment | None:
        if tid in self._track_out:
            return self._track_out[tid]
        el = self.tracks[tid]
        self._stack.add(tid)
        try:
            seg = self._track(el)
        finally:
            self._stack.discard(tid)
        self._track_out[tid] = seg
        return seg

    def _track(self, el) -> Segment | None:
        asset = self.doc.ids.get(el.get("asset"))
        if asset is None or ln(asset) not in ("audio", "video", "generated"):
            warn_once("audioTrack", el.get("id"), f"asset {el.get('asset')!r} is not an audio/video asset; skipped")
            return None
        g = self._track_geometry(el, asset)
        if g is None:
            return None
        sr = self.sr
        x = self._read_region(g, asset)
        n_region = x.shape[0]
        i0 = int(round(g["start"] * sr))
        s, e = g["start"], g["start"] + g["dur"]
        # fades on the placed region
        fi = self.value(el, "fadeIn", s, 0.0, s, e)
        fo = self.value(el, "fadeOut", s, 0.0, s, e)
        curve = el.get("fadeCurve", "linear")
        if (fi > 0 or fo > 0) and n_region:
            tt = np.arange(n_region) / sr
            f = np.ones(n_region)
            if fi > 0:
                f *= dsp.fade_curve(tt / fi, curve)
            if fo > 0:
                f *= dsp.fade_curve((g["dur"] - tt) / fo, curve)
            x = x * f[:, None].astype(np.float32)
        # effects, with room for their tails
        effects = [c for c in el if ln(c) == "audioEffect"]
        bpm = self.tempo(asset)[0]
        if effects:
            tail = sum(tail_seconds(fx, bpm) for fx in effects if parse_bool(fx.get("enabled", "true"), True))
            pad = max(0, min(int(tail * sr), self.n - (i0 + n_region)))
            if pad:
                x = np.concatenate([x, np.zeros((pad, x.shape[1]), np.float32)])
            x = process_chain(effects, x, self.fx_ctx_maker(g["start"], None, bpm))
        trs = self.track_transitions(el, s)
        seg = self._fader(el, Segment(i0, x), s, e, source=True, trs=trs)
        return self._clip(seg)

    def track_transitions(self, el, start: float) -> list:
        """[(window, mode, role)] of the transitions whose node owns this track (module docstring)."""
        if el.get("role") == "music":
            return []
        from ..scheduling import transitions_for, transition_window as evaluated_window
        ctx = Ctx(start, start)
        parents = dict.fromkeys(tr.getparent() for tr in self.doc.root.iter("transition") if not _in_symbol(tr))
        transitions = [tr for parent in parents for tr in transitions_for(self.ev, parent, ctx)]
        best = None
        for tr in transitions:
            for role in ("from", "to"):
                node = self.doc.ids.get(self.ev.str(tr, role, ctx))
                if node is None:
                    continue
                s, e = self.ev.window(node, ctx)
                e = self.doc.duration if e is None else e
                if s - 1e-9 <= start < e - 1e-9 and (best is None or s > best[0]):
                    best = (s, node)
        if best is None:
            return []
        node = best[1]
        out = []
        for tr in transitions:
            for role in ("from", "to"):
                if self.ev.str(tr, role, ctx) == node.get("id"):
                    w = evaluated_window(self.ev, tr, ctx)
                    if w is not None:
                        out.append((w, self.ev.str(tr, "audio", ctx, "crossfade"), role))
        return out

    def _clip(self, seg: Segment | None) -> Segment | None:
        if seg is None:
            return None
        a, b = max(0, seg.i0), min(self.n, seg.i1)
        if b <= a:
            return None
        return Segment(a, seg.data[a - seg.i0:b - seg.i0])

    # ------------------------------------------------------------ fader / pan / spatialisation
    def _fader(self, el, seg: Segment, s: float = 0.0, e: float | None = None, source: bool = False,
               trs=None) -> Segment | None:
        if parse_bool(el.get("mute")) and not _has_anim(el, "mute"):
            return None
        n = seg.data.shape[0]
        vol = self.curve(el, "volume", 1.0, seg.i0, n, s, e)
        gain = self.curve(el, "gain", 0.0, seg.i0, n, s, e)
        g = np.asarray(vol, np.float64) * dsp.db_to_lin(gain)
        if _has_anim(el, "mute"):
            m = self.curve(el, "mute", 0.0, seg.i0, n, s, e)
            g = g * (1.0 - (np.asarray(m) > 0.5))
        if trs:
            g = g * transition_gain((seg.i0 + np.arange(n)) / self.sr, trs)
        pan = self.curve(el, "pan", 0.0, seg.i0, n, s, e)
        x = self._spatialize(seg.data, pan, g) if source else self._bus_pan(seg.data, pan, g)
        x = self._duck(el, x, seg.i0)
        return Segment(seg.i0, x)

    @staticmethod
    def _gain(x: np.ndarray, g) -> np.ndarray:
        if np.ndim(g) == 0:
            return x if abs(float(g) - 1.0) < 1e-12 else (x * np.float32(g)).astype(np.float32)
        return (x * np.asarray(g, np.float32)[:, None]).astype(np.float32)

    def _stereo_balance(self, x, pan, g):
        if not np.isscalar(pan) or abs(pan) > 1e-9:
            pan = np.asarray(pan, np.float64)
            if np.max(np.abs(pan)) > 1.0 + 1e-9:
                warn_once("audio", "pan-range", "pan outside -1..1 clamped (stereo)")
            gl, gr = dsp.pan_gains(pan)
            n = x.shape[0]
            gm = np.stack([np.broadcast_to(gl, (n,)), np.broadcast_to(gr, (n,))], 1) * np.reshape(g, (-1, 1))
            return (x * gm).astype(np.float32)
        return self._gain(x, g)

    def _gains_over(self, fn, pan, n: int) -> np.ndarray:
        """Per-sample (n, nch) gains fn(pan) for a scalar or per-sample pan (evaluated at 100 Hz)."""
        if np.ndim(pan) == 0:
            return np.broadcast_to(fn(float(pan))[0], (n, self.nch))
        step = max(1, int(round(self.sr / CTRL_HZ)))
        idx = np.arange(0, n + step, step)
        pk = np.asarray(pan)[np.minimum(idx, n - 1)]
        G = fn(pk)
        return np.stack([np.interp(np.arange(n), idx, G[:, c]) for c in range(self.nch)], 1)

    def _spatialize(self, x: np.ndarray, pan, g) -> np.ndarray:
        """A track/layer signal (source channels) -> the output layout, with gain g."""
        if self.nch == 1:
            return self._gain(x, g)
        if self.nch == 2:
            return self._stereo_balance(x, pan, g)
        n = x.shape[0]
        if x.shape[1] == self.nch:
            if np.ndim(pan) or abs(float(pan)) > 1e-9:
                warn_once("audio", "pan-discrete", "pan is ignored for sources already in the output layout")
            return self._gain(x, g)
        lay = self.layout
        if x.shape[1] == 1:
            G = self._gains_over(lambda p: lay.pan_gains(pan_to_azimuth(p)), pan, n)
            y = x[:, :1] * G
        else:
            GL = self._gains_over(lambda p: lay.pan_gains(pan_to_azimuth(np.clip(np.asarray(p) - 1, -1, 1))), pan, n)
            GR = self._gains_over(lambda p: lay.pan_gains(pan_to_azimuth(np.clip(np.asarray(p) + 1, -1, 1))), pan, n)
            y = x[:, :1] * GL + x[:, 1:2] * GR
        return self._gain(y.astype(np.float32), g)

    def _bus_pan(self, x: np.ndarray, pan, g) -> np.ndarray:
        """Bus/master pan on a signal already in the output layout."""
        if self.nch == 1:
            return self._gain(x, g)
        if self.nch == 2:
            return self._stereo_balance(x, pan, g)
        if np.ndim(pan) == 0 and abs(float(pan)) < 1e-9:
            return self._gain(x, g)
        if self.layout.ambisonic:
            ang = np.radians(pan_to_azimuth(pan))
            return self._gain(rotate_ambisonic(x.astype(np.float64), self.layout.order, ang).astype(np.float32), g)
        G = self._gains_over(lambda p: balance_gains(self.layout, p), pan, x.shape[0])
        return self._gain((x * G).astype(np.float32), g)

    # ------------------------------------------------------------ ducking
    def _duck(self, el, x: np.ndarray, i0: int) -> np.ndarray:
        srcs = (el.get("duckUnder") or "").split()
        if not srcs or x.shape[0] == 0:
            return x
        n = x.shape[0]
        amount = self.value(el, "duckAmount", 0.0, -12.0)
        thr = self.value(el, "duckThreshold", 0.0, -40.0)
        attack = max(1e-3, self.value(el, "duckAttack", 0.0, 0.15))
        release = max(1e-3, self.value(el, "duckRelease", 0.0, 0.4))
        key = None
        a, b = max(0, i0), min(self.n, i0 + n)
        for sid in srcs:
            if sid in self._stack or sid == el.get("id"):
                warn_once("audio-duck", f"{el.get('id')}->{sid}", "ducking cycle; that source is ignored")
                continue
            sig = self.node_full(sid)
            if sig is None:
                continue
            seg = np.zeros((n, sig.shape[1]), np.float32)
            if b > a:
                seg[a - i0:b - i0] = sig[a:b]
            lv = dsp.rms_db_blocks(seg, self.sr, 0.01)
            key = lv if key is None else np.maximum(key, lv)
        if key is None:
            return x
        target = np.where(key > thr, min(0.0, amount), 0.0)
        depth = abs(min(0.0, amount)) or 1.0
        g_db = dsp.slew_gain_db(target, depth / attack, depth / release)
        self.stats.setdefault("duck", {})[el.get("id")] = float(g_db.min()) if len(g_db) else 0.0
        return (x * dsp.gain_to_samples(dsp.db_to_lin(g_db), n, self.sr)[:, None]).astype(np.float32)

    # ------------------------------------------------------------ routing
    def node_full(self, nid: str) -> np.ndarray | None:
        """Full-length output of a track, bus or the master (for keys and envelopes)."""
        if nid in self.tracks:
            seg = self.track(nid)
            out = np.zeros((self.n, self.nch), np.float32)
            if seg is not None:
                out[seg.i0:seg.i1] = seg.data
            return out
        if nid in self.buses:
            return self.bus(nid)
        if nid == "master" or (self.master_el is not None and nid == self.master_el.get("id")):
            return self.master()
        warn_once("audio-ref", nid, "not an audioTrack or bus id")
        return None

    def _target(self, ref: str | None, who: str) -> str | None:
        if not ref:
            return None
        if ref in self.buses:
            return ref
        warn_once("audio-route", f"{who}->{ref}", f"{ref!r} is not a bus; routed to master")
        return None

    def _inputs(self, bus_id: str | None):
        tracks = [t for t, el in self.tracks.items() if self._target(el.get("bus"), t) == bus_id]
        buses = [b for b, el in self.buses.items() if self._target(el.get("output"), b) == bus_id and b != bus_id]
        layers = [seg for bid, seg in self.layer_segments() if bid == bus_id]
        return tracks, buses, layers

    def _sum(self, bus_id: str | None) -> np.ndarray:
        acc = np.zeros((self.n, self.nch), np.float32)
        tracks, buses, layers = self._inputs(bus_id)
        for t in tracks:
            seg = self.track(t)
            if seg is not None:
                acc[seg.i0:seg.i1] += seg.data
        for b in buses:
            if b in self._stack:
                warn_once("audio-route", b, "bus routing cycle; ignored")
                continue
            acc += self.bus(b)
        for seg in layers:
            acc[seg.i0:seg.i1] += seg.data
        return acc

    def bus(self, bid: str) -> np.ndarray:
        hit = self._bus_out.get(bid)
        if hit is not None:
            return hit
        el = self.buses[bid]
        self._stack.add(bid)
        try:
            acc = self._sum(bid)
            effects = [c for c in el if ln(c) == "audioEffect"]
            if effects:
                acc = process_chain(effects, acc, self.fx_ctx_maker(0.0, self.layout))
            seg = self._fader(el, Segment(0, acc))
            out = seg.data if seg is not None else np.zeros_like(acc)
        finally:
            self._stack.discard(bid)
        self._bus_out[bid] = out
        return out

    def master(self) -> np.ndarray:
        if self._master is not None:
            return self._master
        self._stack.add("master")
        try:
            acc = self._sum(None)
        finally:
            self._stack.discard("master")
        m = self.master_el
        if m is not None:
            effects = [c for c in m if ln(c) == "audioEffect"]
            if effects:
                acc = process_chain(effects, acc, self.fx_ctx_maker(0.0, self.layout))
            vol = self.curve(m, "volume", 1.0, 0, self.n)
            acc = self._gain(acc, vol)
            norm = m.get("normalize", "none")
            tp = self.value(m, "truePeak", 0.0, -1.0)
            wts = self.layout.loudness_weights()
            limiting = norm != "none" or parse_bool(m.get("limiter"))
            target = self.value(m, "loudness", 0.0, -14.0)
            if norm == "integrated":
                measured = dsp.integrated_loudness(acc, self.sr, wts)
                self.stats["measured_lufs"] = measured
                if math.isfinite(measured):
                    # Static gain to the target, then the limiter; when limiting pulls the result
                    # below the target, raise the gain by the shortfall (up to 3 passes, 6 dB).
                    gain = target - measured
                    pre = acc
                    pk = dsp.true_peak_per_sample(pre)
                    got = measured
                    for _ in range(3):
                        g = 10 ** (gain / 20)
                        acc = dsp.limit(pre * np.float32(g), self.sr, tp, peaks=pk * g)
                        got = dsp.integrated_loudness(acc, self.sr, wts)
                        short = target - got
                        if not math.isfinite(got) or short < 0.1 or gain - (target - measured) > 6:
                            break
                        gain += short
                    self.stats["normalize_gain_db"] = gain
                    self.stats["final_lufs"] = got
                    limiting = False
            elif norm == "dynamic":
                self.stats["measured_lufs"] = dsp.integrated_loudness(acc, self.sr, wts)
                t, gdb = dsp.dynamic_gain_db(acc, self.sr, target, wts)
                gs = np.interp(np.arange(acc.shape[0]) / self.sr, t, dsp.db_to_lin(gdb))
                acc = self._gain(acc, gs)
                acc = dsp.limit(acc, self.sr, tp)
                self.stats["dynamic_gain_db"] = (t, gdb)
                self.stats["final_lufs"] = dsp.integrated_loudness(acc, self.sr, wts)
                limiting = False
            if limiting:
                acc = dsp.limit(acc, self.sr, tp)
        self._master = acc.astype(np.float32)
        return self._master

    def render(self, t0: float = 0.0, t1: float | None = None) -> np.ndarray:
        """The final mix for [t0, t1) as float32 (n, ch)."""
        full = self.master()
        t1 = self.doc.duration if t1 is None else t1
        a = int(round(t0 * self.sr))
        b = int(round(t1 * self.sr))
        out = np.zeros((max(0, b - a), self.nch), np.float32)
        lo, hi = max(a, 0), min(b, self.n)
        if hi > lo:
            out[lo - a:hi - a] = full[lo:hi]
        return out

    def has_audio(self) -> bool:
        return bool(self.tracks) or bool(self.layer_segments())

    # ------------------------------------------------------------ video layer audio
    def layer_segments(self) -> list:
        if self._layer_segs is not None:
            return self._layer_segs
        self._layer_segs = []
        comp = self.doc.section("composition")
        if comp is None or self.rc is None:
            return self._layer_segs
        for path in self._audible_layers(comp, [], 0):
            el = path[-1]
            asset = self.doc.ids.get(el.get("asset"))
            try:
                seg = self._layer_audio(path, asset)
            except Exception as e:  # noqa: BLE001 — never fail the render over layer audio
                warn_once("audio-layer", el.get("id"), f"layer audio skipped: {e}")
                seg = None
            if seg is not None:
                self._layer_segs.append((self._target(el.get("audioBus"), el.get("id") or "layer"), seg))
        return self._layer_segs

    def _audible_layers(self, parent, path: list, depth: int):
        """Paths (outermost node .. layer) to every video layer with audio, through instances."""
        if depth > 16:
            return
        for c in self.doc.nodes(parent):
            tag = ln(c)
            if tag == "layer":
                asset = self.doc.ids.get(c.get("asset"))
                if asset is not None and ln(asset) == "video" and parse_bool(asset.get("hasAudio")):
                    yield path + [c]
            elif tag == "instance":
                sym = self.doc.ids.get(c.get("symbol"))
                if sym is not None and sym not in path:
                    yield from self._audible_layers(sym, path + [c, sym], depth + 1)
            elif len(c):
                yield from self._audible_layers(c, path + [c], depth + 1)

    def _node_audio_context(self, el, parent_ctx):
        """Entered context and transition gain, or None outside the audible window."""
        rc, ev = self.rc, self.ev
        ctx = rc.enter_node(el, parent_ctx)
        if not ev.bool(el, "visible", ctx, True) or not ev.condition(el, ctx):
            return None
        start, end = ev.window(el, parent_ctx)
        end = float("inf") if end is None else end
        transitions = []
        for tr in rc.transitions_for(el.getparent(), parent_ctx):
            outgoing, incoming = ev.str(tr, "from", parent_ctx), ev.str(tr, "to", parent_ctx)
            if el.get("id") not in (outgoing, incoming):
                continue
            w = rc.transition_window(tr, parent_ctx)
            if w is None:
                continue
            mode = ev.str(tr, "audio", parent_ctx, "crossfade")
            role = "from" if outgoing == el.get("id") else "to"
            transitions.append((w, mode, role))
            if mode in ("crossfade", "equal-power"):
                if role == "from":
                    end = max(end, w[1])
                else:
                    start = min(start, w[0])
        if parent_ctx.t < start - 1e-9 or parent_ctx.t >= end - 1e-9:
            return None
        gain = float(transition_gain(np.array([parent_ctx.t]), transitions)[0]) if transitions else 1.
        return ctx, gain

    def _clock(self, path: list, tc: float):
        """Walk from composition time to (layer context, inherited transition gain),
        or None outside an audible window."""
        from ..nodes.core import _repeat_vars, child_ctx
        rc = self.rc
        ctx = Ctx(t=tc, comp_t=tc, frame=int(tc * float(self.doc.fps)))
        gain = 1.
        k = 0
        while k < len(path) - 1:
            el = path[k]
            state = self._node_audio_context(el, ctx)
            if state is None:
                return None
            ctx, weight = state
            gain *= weight
            nctx = ctx
            if ln(el) == "instance":
                sym = path[k + 1]
                ctx = self.ev.enter_instance(el, nctx, sym)
                if ctx is None:
                    return None
                k += 2
                continue
            if ln(el) in ("group", "sequence"):
                ctx = _repeat_vars(el, child_ctx(rc, el, nctx))
            k += 1
        lay = path[-1]
        state = self._node_audio_context(lay, ctx)
        if state is None:
            return None
        ctx, weight = state
        return ctx, gain * weight

    def _layer_audio(self, path: list, asset) -> Segment | None:
        rc = self.rc
        from ..nodes.core import media_time
        el = path[-1]
        src = self.decode_asset(asset)
        if src.shape[0] == 0:
            return None
        src_dur = src.shape[0] / self.sr
        hz = 1000.0
        # An evaluated schedule may move outside every prepared window. Walk
        # the full composition interval and apply each window on its own clock.
        t_lo, t_hi = 0., self.doc.duration
        if t_hi <= t_lo:
            return None
        k = int(math.ceil((t_hi - t_lo) * hz)) + 1
        ts = t_lo + np.arange(k) / hz
        gains = np.zeros(k)
        pos = np.zeros(k)
        vol_anim = _has_anim(el, "volume")
        vcache: dict = {}
        for i, tc in enumerate(ts):
            state = self._clock(path, float(tc))
            if state is None:
                continue
            ctx, transition_weight = state
            if vol_anim:
                g = self.ev.num(el, "volume", ctx, 1.0)
            else:
                g = vcache.get(ctx.scope)
                if g is None:
                    g = vcache[ctx.scope] = self.ev.num(el, "volume", ctx, 1.0)
            if self.ev.bool(el, "mute", ctx):
                g = 0.0
            gains[i] = g * transition_weight
            pos[i] = media_time(rc, el, ctx, src_dur)
        nz = np.nonzero(gains)[0]
        if len(nz) == 0:
            return None
        a, b = max(0, nz[0] - 1), min(k - 1, nz[-1] + 1)
        i0 = int(round(ts[a] * self.sr))
        i1 = min(self.n, int(round(ts[b] * self.sr)))
        if i1 <= i0:
            return None
        samp_t = np.arange(i0, i1) / self.sr
        p = np.interp(samp_t, ts, pos) * self.sr
        g = np.interp(samp_t, ts, gains)
        # anti-aliasing for the fastest playback rate (loop/remap jumps are discontinuities, not rates)
        rate = np.abs(np.diff(pos[a:b + 1])) * hz
        ok = (gains[a:b] > 0) & (rate < 16.0)
        top_rate = float(rate[ok].max()) if ok.any() else 1.0
        x = dsp.interp_positions(src, p, cutoff=min(1.0, 1.0 / max(1e-6, top_rate)) * 0.98)
        x = self._spatialize(x, 0.0, g)
        return Segment(i0, x)

    # ------------------------------------------------------------ envelopes
    def signal(self, nid: str) -> np.ndarray | None:
        """Composition-aligned PCM for a track, bus, master or audio-bearing asset."""
        sig = None
        if nid in self.tracks or nid in self.buses or nid == "master" or \
                (self.master_el is not None and nid == self.master_el.get("id")):
            sig = self.node_full(nid)
        else:
            asset = self.doc.ids.get(nid)
            if asset is not None and ln(asset) in ("audio", "video", "generated"):
                users = [t for t, el in self.tracks.items() if el.get("asset") == nid]
                if users:
                    sig = np.zeros((self.n, self.nch), np.float32)
                    for t in users:
                        seg = self.track(t)
                        if seg is not None:
                            sig[seg.i0:seg.i1] += seg.data
                else:
                    raw = self.decode_asset(asset)
                    sig = np.zeros((self.n, raw.shape[1]), np.float32)
                    m = min(self.n, raw.shape[0])
                    sig[:m] = raw[:m]
            else:
                warn_once("audio-amplitude", nid, "unknown track/bus/asset id; amplitude is 0")
        return sig

    def envelope(self, nid: str, band: str | None = None) -> np.ndarray:
        """RMS amplitude (linear, full-scale sine = 0.707) at ENV_HZ over 20 ms windows."""
        key = (nid, band or "")
        hit = self._env.get(key)
        if hit is not None:
            return hit
        sig = self.signal(nid)
        if sig is None:
            env = np.zeros(int(math.ceil(self.doc.duration * ENV_HZ)) + 1)
        else:
            if band in ("low", "mid", "high"):
                if band == "low":
                    secs = dsp.butter_sections("lowpass", 250.0, self.sr, 4)
                elif band == "high":
                    secs = dsp.butter_sections("highpass", 4000.0, self.sr, 4)
                else:
                    secs = dsp.butter_sections("highpass", 250.0, self.sr, 4) + dsp.butter_sections("lowpass", 4000.0, self.sr, 4)
                sig = dsp.filter_sections(sig, secs)
            ms = dsp.block_ms(sig, self.sr, ENV_HZ)
            env = np.sqrt(dsp.moving_average(ms, 2))
        self._env[key] = env.astype(np.float32)
        return self._env[key]

    def amplitude(self, nid: str, band: str | None, t: float) -> float:
        env = self.envelope(str(nid), band)
        if len(env) == 0:
            return 0.0
        x = t * ENV_HZ - 0.5
        return float(np.interp(x, np.arange(len(env)), env, left=0.0, right=0.0))

    def envelope_table(self) -> dict:
        ids = list(self.tracks) + list(self.buses) + ["master"]
        assets = self.doc.section("assets")
        if assets is not None:
            ids += [a.get("id") for a in assets if ln(a) in ("audio", "video") and a.get("id")]
        return {(i, b): self.envelope(i, b or None) for i in ids for b in ("", "low", "mid", "high")}
