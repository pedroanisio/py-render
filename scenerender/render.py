"""Public rendering API.

    from scenerender import render
    r = render.Renderer.open("scene.xml", scale=0.5)
    rgb = r.frame_rgb(3.0)          # (h, w, 3) uint8 at t = 3 s
"""
from __future__ import annotations

import logging
import os
import math
from dataclasses import dataclass

import numpy as np

from . import document
from .compositor import RenderContext
from .evaluator import Evaluator
from .raster import Buf, working_to_rgb8
from .registry import FEATURES, FULL, load_plugins
from .values import parse_bool, paint_ref, parse_color

log = logging.getLogger("scenerender")
FEATURES.declare("motionBlur", FULL, "shutter supersampling (angle, phase, samples, adaptive skip of still frames); "
                                     "node motionBlur on/off/inherit honoured")


@dataclass
class Renderer:
    doc: document.Document
    rc: RenderContext
    open_kwargs: dict | None = None      # how to reopen this document (shutter-sample worker processes)
    _hooks: frozenset = frozenset()      # hook names a freshly opened renderer has
    _pool: object = None
    _mem_workers_cap: int = 1 << 30
    _pool_key: object = None
    _shm: object = None
    _shm_finalizer: object = None

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
        kwargs = dict(path=path, scale=scale, params=params, variant=variant, layout=layout, strict=strict,
                      motion_blur=motion_blur, representation=representation, assets_dir=assets_dir)
        return cls(doc, rc, kwargs, frozenset(rc.hooks))

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

    def frame_linear(self, t: float, frame: int | None = None) -> np.ndarray:
        """Premultiplied working-space frame (h, w, 4) float32, before background."""
        return self.frame_buf(t, frame).px

    def frame_buf(self, t: float, frame: int | None = None):
        """frame_linear as a Buf, whose pixels may still be on the GPU (see gpucomp)."""
        frame = int(round(t * self.fps)) if frame is None else frame
        rc = self.rc
        p = self.doc.project
        if not rc.motion_blur:
            return rc.render_frame(t, frame)
        n = max(1, int(p.get("motionBlurSamples", 16)))
        base = float(p.get("shutterAngle", 180))
        sa = rc.hooks.get("shutter_angle")
        shutter = (sa(rc, t, base) if sa else base) / 360.0 / self.fps   # active camera may override
        phase = float(p.get("shutterPhase", -90)) / 360.0 / self.fps
        times = [t + phase + shutter * (i + 0.5) / n for i in range(n)]
        rc.mb_center = t
        # Nodes proven unchanged over the shutter render once and are reused by the other samples.
        rc.mb_interval, rc._mb_nodes, rc._mb_static = (times[0], times[-1]), {}, {}
        try:
            if n > 2 and parse_bool(p.get("adaptiveMotionBlur", "true"), True):
                # Matching endpoints alone cannot establish a still frame: motion
                # may return to its starting position during the shutter.
                if self._static_shutter(times[0], times[-1]):
                    first, last = rc.render_frame(times[0], frame), rc.render_frame(times[-1], frame)
                    if first.rect == last.rect and _max_abs_diff(first, last) < 0.5 / 255:
                        return _mean(_add(_add(None, first), last), 2)
                    acc = self._accumulate(times[1:-1], frame, _add(_add(None, first), last))
                    return _mean(acc, n)
                # acc = first + last, then the inner samples in order.
                return _mean(self._accumulate([times[0], times[-1], *times[1:-1]], frame), n)
            return _mean(self._accumulate(times, frame), n)
        finally:
            rc.mb_center = None
            rc.mb_interval, rc._mb_nodes, rc._mb_static = None, None, {}

    # ------------------------------------------------------------ shutter samples
    def _accumulate(self, times: list[float], frame: int, acc=None):
        """acc (a Buf) + the frames at times, summed in order. Samples are independent renders, so with
        SCENERENDER_SAMPLE_WORKERS=1 spare CPUs render them in worker processes; the sum is formed here
        in the same order either way."""
        rc = self.rc
        pool = self._sample_pool(times, frame) if times else None
        if pool is None:
            for ts in times:
                acc = _add(acc, rc.render_frame(ts, frame))
            return acc
        from . import thread_limit
        shape = (rc.height, rc.width, 4)
        shm = self._sample_buffer(len(times) * int(np.prod(shape)) * 4)
        try:
            out = np.ndarray((len(times),) + shape, np.float32, buffer=shm.buf)
            chunks = [c.tolist() for c in np.array_split(np.arange(len(times)), pool._processes + 1)]
            state = (frame, rc.mb_center, rc.mb_interval, shm.name, shape)
            pending = [(c, pool.apply_async(_sample_render, ([(i, times[i]) for i in c],) + state))
                       for c in chunks[1:] if c]
            with thread_limit(1):     # the other CPUs are rendering the workers' samples
                for i in chunks[0]:
                    out[i] = rc.render_frame(times[i], frame).px
            failed = False
            for c, res in pending:
                if not failed:
                    try:
                        res.get()
                        continue
                    except Exception as e:  # noqa: BLE001 — a failed worker's samples are rendered here
                        log.warning("shutter-sample worker failed (%s); rendering remaining samples serially", e)
                        # The terminated pool never completes the other pending chunks: render them here too.
                        # The buffer stays open: out still points into it (closing it unmaps it under out).
                        self._terminate_pool()
                        failed = True
                for i in c:
                    out[i] = rc.render_frame(times[i], frame).px
            for i in range(len(times)):
                acc = _add(acc, Buf(out[i].copy(), 0, 0))
            del out
            return acc
        except BaseException:
            self._release_buffer()
            raise

    def _sample_buffer(self, size: int):
        """Shared memory for one frame's samples, kept for later frames (fresh pages cost page faults)."""
        from multiprocessing import shared_memory
        if self._shm is not None and self._shm.size >= size:
            return self._shm
        self._release_buffer()
        import weakref
        self._shm = shared_memory.SharedMemory(create=True, size=size)
        self._shm_finalizer = weakref.finalize(self, _unlink_shm, self._shm)
        return self._shm

    def _release_buffer(self) -> None:
        if self._shm is not None:
            self._shm_finalizer()
            self._shm = None

    def _sample_pool(self, times: list[float], frame: int):
        """Worker processes for shutter samples, or None when rendering here is as fast or not equivalent."""
        from . import threads
        # Opt-in: every sample worker reopens the document and redraws what this process has cached,
        # which saves wall time on idle CPUs but costs CPU time (a 5 s map shot: 71.6 CPU-s with
        # sample workers, 40.4 without).
        if os.environ.get("SCENERENDER_SAMPLE_WORKERS", "0") != "1":
            return None
        n = len(times)
        workers = min(threads(), n, self._mem_workers_cap) - 1
        workers = self._memory_bounded_workers(workers)
        rc = self.rc
        # Workers reopen the document: anything changed on this context since open() keeps rendering here.
        if (workers < 1 or self.open_kwargs is None or frozenset(rc.hooks) != self._hooks or rc.exclude
                or rc._node_mb or rc.scale != self.open_kwargs["scale"] or "render_frame" in vars(rc)
                or any(k in rc.cache for k in ("pass360", "camera_override"))):
            return None
        state = {k: rc.cache[k] for k in _FORWARDED if k in rc.cache}
        key = (workers, repr(sorted(state.items(), key=lambda kv: kv[0])))
        if self._pool is not None and self._pool_key != key:
            self.close_sample_pool()
        if self._pool is None:
            import multiprocessing as mp
            import weakref
            if mp.current_process().daemon or os.environ.get("SCENERENDER_FRAME_WORKER") == "1":
                return None      # a frame worker: its share of CPUs is threads, samples render serially
            try:
                with _without_main_reimport():
                    self._pool = mp.get_context("spawn").Pool(workers, initializer=_sample_init,
                                                              initargs=(self.open_kwargs, state))
            except (OSError, AssertionError, ValueError) as e:
                log.warning("shutter samples render serially: no worker processes (%s)", e)
                self.open_kwargs = None
                return None
            self._pool_key = key
            weakref.finalize(self, self._pool.terminate)
        return self._pool

    def _memory_bounded_workers(self, workers: int) -> int:
        """Cap shutter-sample workers by memory. Every worker reopens the document and loads its own
        models, textures and GL resources, so a heavy (3D) scene multiplies its footprint by the
        worker count. Workers are allowed only while (workers + 1) copies of the largest process fit
        in half the memory still available (host or container cgroup, whichever is tighter), counting
        what the current workers already hold. The cap only ever decreases for this renderer, so a
        pool is not respawned back and forth."""
        if workers < 1:
            return workers
        procs = list(getattr(self._pool, "_pool", None) or []) if self._pool is not None else []
        held = [_rss(p.pid) for p in procs]
        # Size by high-water marks: a frame peaks well above what a process holds between frames.
        per = max([_rss("self", peak=True)] + [_rss(p.pid, peak=True) for p in procs] + [256 << 20])
        budget = _mem_available() * 0.5 + sum(held)
        fit = int(budget // per) - 1          # the main process renders samples too
        if fit < workers:
            capped = max(fit, 0)
            if capped + 1 < self._mem_workers_cap:
                log.info("shutter samples: %d worker(s) fit in memory (%.1f GB peak per process)", capped, per / 2 ** 30)
            self._mem_workers_cap = capped + 1
            workers = capped
            if self._pool is not None and len(procs) > capped:
                self._terminate_pool()          # release the idle workers' memory now
        return workers

    def close_sample_pool(self) -> None:
        self._terminate_pool()
        self._release_buffer()

    def _terminate_pool(self) -> None:
        if self._pool is not None:
            self._pool.terminate()
            self._pool = self._pool_key = None

    _SHUTTER_DYNAMIC = frozenset({
        "expression", "link", "motionPath", "particleEmitter", "physics", "rigidBody", "softBody", "deform",
        "shapeModifier", "shake", "textAnimator", "effect", "transition", "instance", "video", "imageSequence",
        "lottie", "audiogram", "generator", "generated", "captions"})

    def _has_dynamic(self) -> bool:
        """Whether the document holds any element _static_shutter rejects (effects, expressions, ...),
        remembered per element count so a document is not walked on every frame to find out."""
        from .document import ln
        count = int(self.doc.root.xpath("count(//*)"))
        hit = self.__dict__.get("_dynamic_memo")
        if hit is None or hit[0] != count:
            dyn = any(isinstance(el.tag, str) and (ln(el) in self._SHUTTER_DYNAMIC or el.get("condition"))
                      for el in self.doc.root.iter())
            hit = self.__dict__["_dynamic_memo"] = (count, dyn)
        return hit[1]

    def _static_shutter(self, first: float, last: float) -> bool:
        """Conservatively prove the scene has no time-varying input in this interval.

        Unknown/procedural cases use all requested samples. This is an optimization
        only: a return motion, short flash or held-key change must never disappear.
        """
        from .document import NODE_TAGS, ln
        if self.doc.clock_shift or self._has_dynamic():
            return False       # dynamic elements (effects, expressions, ...): never provably static
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
        buf = self.frame_buf(t, frame)
        if buf.gpu is not None:
            from . import gpucomp
            return gpucomp.to_rgb8(buf, self.rc.linear, self.graded_background())
        return working_to_rgb8(buf.px, self.rc.linear, self.graded_background())

    def frame_rgba(self, t: float, frame: int | None = None) -> np.ndarray:
        """Straight-alpha RGBA without the project background (for alpha outputs)."""
        frame = int(round(t * self.fps)) if frame is None else frame
        px = self.frame_linear(t, frame)
        rgb = working_to_rgb8(px, self.rc.linear)
        a = (np.clip(px[..., 3:4], 0, 1) * 255 + 0.5).astype(np.uint8)
        return np.concatenate([rgb, a], -1)


# Renderer state set after open() that shutter-sample workers must share.
_FORWARDED = ("output_color", "output_hdr", "burn_captions", "audio_envelopes", "representation")
_SW: dict = {}


class _without_main_reimport:
    """Spawned workers normally re-run the parent's __main__ script, which may render at import time;
    the worker functions live in this module, so the main module is hidden while workers start."""

    def __enter__(self):
        import sys
        main = sys.modules.get("__main__")
        self.saved = {k: main.__dict__.pop(k) for k in ("__file__",) if main is not None and k in main.__dict__}
        self.spec = getattr(main, "__spec__", None)
        if main is not None:
            main.__spec__ = None
        self.main = main

    def __exit__(self, *exc):
        if self.main is not None:
            self.main.__dict__.update(self.saved)
            self.main.__spec__ = self.spec


def _sample_init(open_kwargs: dict, state: dict) -> None:
    import os
    os.environ["SCENERENDER_THREADS"] = "1"
    logging.getLogger("scenerender").setLevel(logging.ERROR)
    kw = dict(open_kwargs)
    r = Renderer.open(kw.pop("path"), **kw)
    r.rc.cache.update(state)
    _SW["r"] = r


def _rss(pid, peak: bool = False) -> int:
    """Resident set size in bytes of a process, or its high-water mark with peak (0 when unknown)."""
    key = "VmHWM:" if peak else "VmRSS:"
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith(key):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _mem_available() -> float:
    """Bytes of memory still available to this process: host MemAvailable, tightened by a cgroup
    (v2 or v1) limit when one applies. Unknown platforms report plenty (no cap)."""
    avail = float("inf")
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
                    break
    except OSError:
        return avail
    for lim, use in (("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
                     ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes")):
        try:
            with open(lim) as a, open(use) as b:
                raw = a.read().strip()
                if raw != "max" and int(raw) < 1 << 60:
                    avail = min(avail, int(raw) - int(b.read().strip()))
                break
        except (OSError, ValueError):
            continue
    return max(avail, 0)


def _unlink_shm(shm) -> None:
    try:
        shm.close()
        shm.unlink()
    except (FileNotFoundError, BufferError):
        pass


def _sample_render(samples, frame, center, interval, shm_name, shape) -> None:
    from multiprocessing import shared_memory
    rc = _SW["r"].rc
    shm = shared_memory.SharedMemory(name=shm_name)
    rc.mb_center, rc.mb_interval, rc._mb_nodes, rc._mb_static = center, interval, {}, {}
    try:
        out = np.ndarray((len(shm.buf) // (int(np.prod(shape)) * 4),) + tuple(shape), np.float32, buffer=shm.buf)
        for i, ts in samples:
            out[i] = rc.render_frame(ts, frame).px
        del out
    finally:
        rc.mb_center, rc.mb_interval, rc._mb_nodes, rc._mb_static = None, None, None, {}
        shm.close()


# Optional modules add hooks (captions burn-in, finishing/colour management, camera, audio levels).
_HOOK_INSTALLERS: list = []


def hook_installer(name: str):
    def deco(fn):
        _HOOK_INSTALLERS.append((name, fn))
        return fn
    return deco


def _add(acc, buf):
    """acc + buf as a Buf (acc None: buf itself). On the GPU when either is (gpucomp.accumulate)."""
    if acc is None:
        return buf
    if acc.gpu is not None or buf.gpu is not None:
        from . import gpucomp
        return gpucomp.accumulate(gpucomp.accumulate(None, acc), buf)
    acc.px += buf.px            # in place: the same sums without a new frame per sample
    return acc


def _mean(acc, n: int):
    if n == 1:
        return acc
    if acc.gpu is not None:
        from . import gpucomp
        return gpucomp.combine(acc, None, n)
    acc.px /= n
    return acc


def _max_abs_diff(a, b) -> float:
    if a.gpu is not None or b.gpu is not None:
        from . import gpucomp
        return gpucomp.max_abs_diff(a, b)
    return float(np.abs(a.px - b.px).max(initial=0.0))
