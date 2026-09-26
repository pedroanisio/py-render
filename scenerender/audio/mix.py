"""The audio mix: audioTrack -> bus -> ... -> master, plus the audio of video layers.

Signal flow per audioTrack (all pinned here; the schema leaves the order open):
    source region (clipIn/clipOut, speed, reverse, loop, fitToDuration)
    -> fadeIn/fadeOut (fadeCurve)                 on the placed region
    -> audioEffect chain (document order)         with room for reverb/delay tails
    -> fader: volume (unit) x gain (dB)           automation sampled every 10 ms
    -> pan (constant power, unity at centre)
    -> ducking (duckUnder sources vs duckThreshold, slewed at duckAmount/duckAttack|duckRelease dB/s)
    -> bus (or master)
A bus sums its tracks and child buses, then effects -> fader -> pan -> ducking -> its output bus.
The master sums everything unrouted, then effects -> volume -> normalize (BS.1770-4 integrated,
static gain to @loudness) -> true-peak limiter at @truePeak (when normalizing or limiter="true").
Dither is applied when the mix is quantised (io.write_wav).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from ..document import ln
from ..evaluator import ANIM_TAGS, Ctx
from ..registry import FEATURES, FULL, PARTIAL, warn_once
from . import dsp
from .effects import FxContext, process_chain, tail_seconds
from .io import decode

log = logging.getLogger("scenerender")

FEATURES.declare("audio:mix", FULL, "tracks, buses, master; volume/gain/pan automation at 100 Hz")
FEATURES.declare("audio:ducking", FULL, "RMS (10 ms) key vs duckThreshold; gain slews duckAmount dB over duckAttack/duckRelease")
FEATURES.declare("audio:normalize-integrated", FULL, "ITU-R BS.1770-4 gated integrated loudness, static gain, 4x-oversampled true-peak limiter")
FEATURES.declare("audio:normalize-dynamic", PARTIAL, "treated as integrated normalisation plus the true-peak limiter")
FEATURES.declare("audio:fitToDuration", PARTIAL, "loops whole bars of the asset (bpm, 4/4 or the first beatGrid's beatsPerBar) and trims at the project end with a 50 ms fade")
FEATURES.declare("audio:preservePitch", PARTIAL, "speed changes resample (pitch follows speed); no time-stretching")
FEATURES.declare("audio:surround", PARTIAL, "channel layouts above stereo are mixed as stereo")
FEATURES.declare("audio:layerAudio", PARTIAL, "video layer audio (volume, mute, audioBus, transitions @audio); layers inside symbols/instances are skipped")
FEATURES.declare("transitionAudio", PARTIAL, "crossfade/equal-power/cut/none apply to video-layer audio; audioTrack elements are not affected")

CTRL_HZ = 100.0      # automation control rate
ENV_HZ = 100.0       # audio_amplitude envelope rate


@dataclass
class Segment:
    i0: int
    data: np.ndarray          # (n, ch)

    @property
    def i1(self) -> int:
        return self.i0 + self.data.shape[0]


def _has_anim(el, prop: str) -> bool:
    return any(ln(c) in ANIM_TAGS and c.get("property") == prop for c in el)


class Mixer:
    def __init__(self, doc, ev, rc=None, representation: str | None = None, sample_rate: int | None = None):
        self.doc, self.ev, self.rc = doc, ev, rc
        self.representation = representation or (rc.cache.get("representation") if rc is not None else None)
        am = doc.section("audioMix")
        self.el = am
        sr = int(am.get("sampleRate", 48000)) if am is not None else 48000
        self.sr = int(sample_rate or sr)
        ch = int(am.get("channels", 2)) if am is not None else 2
        layout = am.get("channelLayout", "auto") if am is not None else "auto"
        if ch > 2 or layout not in ("auto", "mono", "stereo"):
            warn_once("audio", "surround", f"channels={ch} channelLayout={layout}: mixed as stereo")
        self.nch = 1 if (ch == 1 or layout == "mono") else 2
        self.bits = int(am.get("bitDepth", 24)) if am is not None else 24
        self.n = int(math.ceil(doc.duration * self.sr))
        self.tracks = {t.get("id"): t for t in (am if am is not None else []) if ln(t) == "audioTrack"}
        self.buses = {b.get("id"): b for b in (am if am is not None else []) if ln(b) == "bus"}
        self.master_el = next((m for m in (am if am is not None else []) if ln(m) == "master"), None)
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

    def ctx(self, t: float, s: float = 0.0, e: float | None = None) -> Ctx:
        return Ctx(t=t, comp_t=t, frame=int(t * float(self.doc.fps)), node_start=s, node_end=e)

    def value(self, el, prop: str, t: float, default: float, s: float = 0.0, e: float | None = None) -> float:
        return self.ev.num(el, prop, self.ctx(t, s, e), default)

    def curve(self, el, prop: str, default: float, i0: int, n: int, s: float = 0.0, e: float | None = None):
        """Per-sample values of el/@prop over samples [i0, i0 + n): a scalar when not animated."""
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

    def decode_asset(self, asset, stream: int | None = None):
        key = asset.get("id")
        hit = self._decoded.get(key)
        if hit is None:
            path, stream = self.source_path(asset)
            if path is None:
                warn_once("audio-asset", asset.get("id"), "no audio source (missing file, no audio stream or unverified cache)")
                hit = np.zeros((0, self.nch), np.float32)
            else:
                try:
                    hit = decode(path, self.sr, self.nch, stream)
                except (OSError, ValueError) as e:
                    warn_once("audio-asset", asset.get("id"), f"cannot decode {path}: {e}")
                    hit = np.zeros((0, self.nch), np.float32)
            self._decoded[key] = hit
        return hit

    def fx_ctx_maker(self, t0: float):
        def make(el):
            def get(name, default):
                for p in el:
                    if ln(p) == "param" and p.get("name") == name:
                        try:
                            return float(p.get("value"))
                        except (TypeError, ValueError):
                            return p.get("value")
                if el.get(name) is None and default is None:
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
                out = np.zeros((n, self.nch), np.float32)
                a, b = max(0, i0), min(self.n, i0 + n)
                if b > a:
                    out[a - i0:b - i0] = sig[a:b]
                return out
            return FxContext(el, self.sr, t0, get, curve, key, self.seed)
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
        reverse = (el.get("reverse") == "true") ^ (speed < 0)
        speed = abs(speed) or 1.0
        span = clip_out - clip_in
        if span <= 1e-6:
            return None
        plays = int(self.value(el, "loop", start, 0.0)) + 1
        dur = plays * span / speed
        fit_fade = 0.0
        if el.get("fitToDuration") == "true":
            target = self.doc.duration - start
            bpm = float(asset.get("bpm", 0) or 0)
            per_bar = self.doc.beat_grids[0][2] if self.doc.beat_grids else 4
            if bpm > 0:
                bar = per_bar * 60.0 / bpm
                nb = max(1, int(math.floor(span * (1 + 1e-9) / bar)))
                span = min(span, nb * bar)
            plays = max(1, int(math.ceil(target * speed / span - 1e-9)))
            dur = target
            fit_fade = 0.05
        if el.get("preservePitch", "true") != "false" and abs(speed - 1.0) > 1e-9:
            warn_once("audio", "preservePitch", "speed != 1 resamples the track (pitch follows speed)")
        return {"src": src, "start": start, "clip_in": clip_in, "span": span, "speed": speed, "reverse": reverse,
                "plays": plays, "dur": dur, "fit_fade": fit_fade}

    def _read_region(self, g) -> np.ndarray:
        src, sr = g["src"], self.sr
        n = int(round(g["dur"] * sr))
        if n <= 0:
            return np.zeros((0, self.nch), np.float32)
        span_n = g["span"] * sr
        ci = g["clip_in"] * sr
        if abs(g["speed"] - 1.0) < 1e-9 and abs(ci - round(ci)) < 1e-6 and abs(span_n - round(span_n)) < 1e-6:
            ci, span_i = int(round(ci)), int(round(span_n))
            one = src[ci:ci + span_i]
            if g["reverse"]:
                one = one[::-1]
            reps = int(math.ceil(n / max(1, len(one))))
            out = np.tile(one, (reps, 1))[:n] if reps > 1 else one[:n]
            if out.shape[0] < n:
                out = np.concatenate([out, np.zeros((n - out.shape[0], self.nch), np.float32)])
            return np.ascontiguousarray(out, np.float32)
        u = (np.arange(n) / sr * g["speed"]) % g["span"]
        if g["reverse"]:
            u = g["span"] - u
        pos = (g["clip_in"] + u) * sr
        return dsp.interp_positions(src, pos, cutoff=min(1.0, 1.0 / g["speed"]) * 0.98)

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
        x = self._read_region(g)
        n_region = x.shape[0]
        i0 = int(round(g["start"] * sr))
        s, e = g["start"], g["start"] + g["dur"]
        # fades on the placed region
        fi = self.value(el, "fadeIn", s, 0.0, s, e)
        fo = max(self.value(el, "fadeOut", s, 0.0, s, e), g["fit_fade"])
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
        if effects:
            tail = sum(tail_seconds(fx) for fx in effects if fx.get("enabled", "true") != "false")
            pad = max(0, min(int(tail * sr), self.n - (i0 + n_region)))
            if pad:
                x = np.concatenate([x, np.zeros((pad, self.nch), np.float32)])
            x = process_chain(effects, x, self.fx_ctx_maker(g["start"]))
        seg = self._fader(el, Segment(i0, x), s, e)
        return self._clip(seg)

    def _clip(self, seg: Segment | None) -> Segment | None:
        if seg is None:
            return None
        a, b = max(0, seg.i0), min(self.n, seg.i1)
        if b <= a:
            return None
        return Segment(a, seg.data[a - seg.i0:b - seg.i0])

    def _fader(self, el, seg: Segment, s: float = 0.0, e: float | None = None) -> Segment | None:
        if el.get("mute") == "true" and not _has_anim(el, "mute"):
            return None
        n = seg.data.shape[0]
        vol = self.curve(el, "volume", 1.0, seg.i0, n, s, e)
        gain = self.curve(el, "gain", 0.0, seg.i0, n, s, e)
        g = np.asarray(vol, np.float64) * dsp.db_to_lin(gain)
        if _has_anim(el, "mute"):
            m = self.curve(el, "mute", 0.0, seg.i0, n, s, e)
            g = g * (1.0 - (np.asarray(m) > 0.5))
        x = seg.data
        pan = self.curve(el, "pan", 0.0, seg.i0, n, s, e)
        if self.nch == 2 and (not np.isscalar(pan) or abs(pan) > 1e-9):
            pan = np.asarray(pan, np.float64)
            if np.max(np.abs(pan)) > 1.0 + 1e-9:
                warn_once("audio", "pan-range", "pan outside -1..1 clamped")
            gl, gr = dsp.pan_gains(pan)
            gm = np.stack([np.broadcast_to(gl, (n,)), np.broadcast_to(gr, (n,))], 1) * np.reshape(g, (-1, 1))
            x = (x * gm).astype(np.float32)
        elif np.isscalar(g) or np.ndim(g) == 0:
            if abs(float(g) - 1.0) > 1e-12:
                x = (x * np.float32(g)).astype(np.float32)
        else:
            x = (x * g[:, None]).astype(np.float32)
        x = self._duck(el, x, seg.i0)
        return Segment(seg.i0, x)

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
            seg = np.zeros((n, self.nch), np.float32)
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
                acc = process_chain(effects, acc, self.fx_ctx_maker(0.0))
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
                acc = process_chain(effects, acc, self.fx_ctx_maker(0.0))
            vol = self.curve(m, "volume", 1.0, 0, self.n)
            if np.isscalar(vol):
                acc = acc * np.float32(vol)
            else:
                acc = (acc * vol[:, None]).astype(np.float32)
            norm = m.get("normalize", "none")
            tp = self.value(m, "truePeak", 0.0, -1.0)
            if norm == "dynamic":
                warn_once("audio", "normalize-dynamic", "normalize=dynamic is rendered as integrated normalisation + limiter")
            limiting = norm != "none" or m.get("limiter") == "true"
            if norm in ("integrated", "dynamic"):
                target = self.value(m, "loudness", 0.0, -14.0)
                measured = dsp.integrated_loudness(acc, self.sr)
                self.stats["measured_lufs"] = measured
                if math.isfinite(measured):
                    # Static gain to the target, then the limiter; when limiting pulls the result
                    # below the target, raise the gain by the shortfall (up to 3 passes, 6 dB).
                    gain = target - measured
                    pre = acc
                    pk = dsp.true_peak_per_sample(pre)
                    for _ in range(3):
                        g = 10 ** (gain / 20)
                        acc = dsp.limit(pre * np.float32(g), self.sr, tp, peaks=pk * g)
                        got = dsp.integrated_loudness(acc, self.sr)
                        short = target - got
                        if not math.isfinite(got) or short < 0.1 or gain - (target - measured) > 6:
                            break
                        gain += short
                    self.stats["normalize_gain_db"] = gain
                    self.stats["final_lufs"] = got
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
        if comp is None:
            return self._layer_segs
        for el in comp.iter("layer"):
            asset = self.doc.ids.get(el.get("asset"))
            if asset is None or ln(asset) != "video" or asset.get("hasAudio") != "true":
                continue
            if el.get("mute") == "true":
                continue
            try:
                seg = self._layer_audio(el, asset)
            except Exception as e:  # noqa: BLE001 — never fail the render over layer audio
                warn_once("audio-layer", el.get("id"), f"layer audio skipped: {e}")
                seg = None
            if seg is not None:
                self._layer_segs.append((self._target(el.get("audioBus"), el.get("id") or "layer"), seg))
        return self._layer_segs

    def _video_audio(self, asset):
        return self.decode_asset(asset)

    def _layer_audio(self, el, asset) -> Segment | None:
        rc = self.rc
        if rc is None:
            return None
        from ..nodes.core import media_time
        anc = [a for a in el.iterancestors() if isinstance(a.tag, str)]
        if any(ln(a) in ("symbol", "symbols") for a in anc):
            warn_once("audio-layer", el.get("id"), "layer audio inside symbols is not mixed")
            return None
        chain = [a for a in reversed(anc) if ln(a) in ("group", "sequence")]
        src = self._video_audio(asset)
        if src.shape[0] == 0:
            return None
        src_dur = src.shape[0] / self.sr
        s, e = self.doc.window(el)
        e = self.doc.duration if e is None else e
        # transitions extend the audible window and shape the gain
        trs = []
        for tr in rc.trans_members.get(el, ()):
            w = rc.transition_window(tr)
            if w is None:
                continue
            mode = tr.get("audio", "crossfade")
            role = "from" if tr.get("from") == el.get("id") else "to"
            trs.append((w, mode, role))
            if mode in ("crossfade", "equal-power"):
                if role == "from":
                    e = max(e, w[1])
                else:
                    s = min(s, w[0])

        def local(t):
            for g in chain:
                shift = self.doc.clock_shift.get(g)
                if shift:
                    t -= shift
                if ln(g) == "group":
                    gs = self.doc.window(g)[0]
                    off = float(g.get("timeOffset", 0) or 0)
                    sc = float(g.get("timeScale", 1) or 1)
                    t = gs + (t - gs - off) * sc
            return t

        def visible(tc):
            for g in chain:
                if g.get("visible") == "false":
                    return False
            return True

        hz = 1000.0
        # region in composition time: approximate by mapping the layer window through the clocks
        t_lo, t_hi = 0.0, self.doc.duration
        k = int(math.ceil((t_hi - t_lo) * hz)) + 1
        ts = t_lo + np.arange(k) / hz
        gains = np.zeros(k)
        pos = np.zeros(k)
        vol_anim = _has_anim(el, "volume")
        base_vol = self.value(el, "volume", 0.0, 1.0)
        for i, tc in enumerate(ts):
            tl = local(float(tc))
            if tl < s - 1e-9 or tl >= e - 1e-9 or not visible(tc):
                continue
            ctx = self.ctx(tl, s, e)
            g = self.ev.num(el, "volume", ctx, 1.0) if vol_anim else base_vol
            for (w0, w1, cut), mode, role in trs:
                if mode == "none":
                    continue
                if mode == "cut":
                    if (role == "from" and tl >= cut) or (role == "to" and tl < cut):
                        g = 0.0
                    continue
                if w0 <= tl < w1:
                    p = (tl - w0) / max(1e-9, w1 - w0)
                    if mode == "equal-power":
                        g *= math.cos(p * math.pi / 2) if role == "from" else math.sin(p * math.pi / 2)
                    else:
                        g *= (1 - p) if role == "from" else p
                elif role == "from" and tl >= w1:
                    g = 0.0
                elif role == "to" and tl < w0:
                    g = 0.0
            gains[i] = g
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
        x = dsp.interp_positions(src, p, cutoff=0.98)
        x = (x * g[:, None]).astype(np.float32)
        return Segment(i0, x)

    # ------------------------------------------------------------ envelopes
    def envelope(self, nid: str, band: str | None = None) -> np.ndarray:
        """RMS amplitude (linear, full-scale sine = 0.707) at ENV_HZ over 20 ms windows."""
        key = (nid, band or "")
        hit = self._env.get(key)
        if hit is not None:
            return hit
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
                    sig = np.zeros((self.n, self.nch), np.float32)
                    m = min(self.n, raw.shape[0])
                    sig[:m] = raw[:m]
            else:
                warn_once("audio-amplitude", nid, "unknown track/bus/asset id; amplitude is 0")
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
