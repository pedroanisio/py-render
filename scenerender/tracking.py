"""Tracking data (`<tracking><trackData .../></tracking>`): loading, parsing and sampling.

A `<trackData>` points at an external tracking export. `load_track` reads it into a `Track`
of named `Channel`s (one per tracked point / object); `Track.sample(point, t)` interpolates it
at COMPOSITION time. `transformConstraint type="track"` and track-driven masks consume this.

Common conventions (pinned)
---------------------------
* Output space: composition/footage pixels, origin top-left, +y DOWN, rotation in degrees
  CLOCKWISE on screen, scale as factors (1 = 100 %). Camera/3D data (FBX, Nuke .chan, AE 3D
  position) keeps the exporter's world units and Euler angles as given (no axis conversion).
* Keys a channel may carry (only those present): x y z rotation rx ry rz scaleX scaleY
  anchorX anchorY corners (N,4,2: TL,TR,BR,BL) focal (mm) fov (deg, vertical) zoom (AE camera
  zoom, px) points (N,K,2: mask vertices / face landmarks, fixed K).
* Time: channel times are TRACK time in seconds; track time = composition time - @timeOffset.
  Frame numbers become seconds as ``t = frame / fps`` with NO first-frame subtraction: AE and
  mocha frames are 0-based, Nuke frames are usually 1-based (frame 1 -> 1/fps); use @timeOffset
  to line things up. fps = the @footage asset's @fps if it has one, else the file's own rate
  (AE "Units Per Second", JSON "fps", Nuke Root "fps"), else the document fps.
* Footage size (only needed for Nuke's y-up flip and CornerPin2D defaults): @footage asset
  @width/@height, else the file's (AE "Source Width/Height", Nuke Root "format"), else document.
* Interpolation: formats that carry no interpolation data (json, csv, AE/mocha keyframe text,
  Nuke .chan) interpolate linearly between keys and hold before the first / after the last key.
  Nuke and FBX curves are evaluated exactly as their applications define them (below) and kept as
  evaluators in ``Channel.curves`` (``Channel.data`` holds their values at the key times).
  Values are used as given (no angle unwrapping). Tabulated properties keyed on different frames
  are merged onto the union of their key times by linear interpolation.
* Loading: @sha256 is verified against the file bytes (mismatch -> logged error, no track).
  Unreadable/unparseable files -> `registry.warn_once("trackData", id, msg)`, no track.
  ``Track.format`` is the format actually parsed (after json sniffing).
  Results are cached in ``rc.cache`` (or a module dict) keyed by path, mtime, size, the
  trackData attributes and the fps/size context.

Point selection (`Track.channel(point)`, the constraint's @point)
-----------------------------------------------------------------
``None``/``""`` -> first channel. ``"name"`` -> exact channel name, else the first channel whose
last ``/`` path component equals it (AE ``"Tracker #1/Track Point #1"`` matches
``"Track Point #1"``), else an integer string selects the n-th channel (0-based). ``"name:sub"``
or ``":sub"`` adds a sub-point: ``tl|tr|br|bl`` (planar corner), ``center`` (average of the 4
corners, or of the mask/face points; vertex average, not the perspective diagonal crossing), or
an integer vertex/landmark index into ``points`` (or ``corners``). A bare ``tl|tr|br|bl|center``
that names no channel is taken as ``":sub"``. `sample` then also sets ``x``/``y`` to that point.
Unknown channel or sub-point -> ``None``.

Formats
-------
json (default @format). Schema::

    {"version": 1, "fps": 30,            # fps optional (used for "frame" keys)
     "kind": "point",                     # optional, informational
     "tracks": {"<name>": {"keys": [KEY, ...]}, ...}}
    # "tracks" may also be a list [{"name": "...", "keys": [...]}, ...] or {"<name>": [KEY...]};
    # a top-level {"name":..., "keys": [...]} is a single track.
    KEY = {"t": seconds | "frame": n,
           "x","y","z","rotation","rx","ry","rz","anchorX","anchorY","fov","focal","zoom": number,
           "scale": s | [sx, sy]   (factor) | "scaleX","scaleY",
           "corners": [[x,y] x4]   (TL,TR,BR,BL, px),
           "points": [[x,y], ...]  (mask vertices / face landmarks, px, same count in every key)
           | "path": "M x y C ... Z"  (SVG path commands, absolute or relative, multiple
                                      subpaths; matching command topology between keys)}
  Every field is optional per key; each property is interpolated over the keys that carry it.
  Pixels, +y down, rotation clockwise. Face tracks: landmarks in "points" (+ optional x/y/
  rotation/scale for the head pose); ``:<index>`` selects a landmark.
  Sniffing: with @format="json" (or omitted) a file that does not start with ``{``/``[`` is
  dispatched by content/extension instead: FBX magic / "; FBX" -> fbx; "Adobe After Effects ...
  Keyframe Data" -> after-effects; .chan/.nk (or a Nuke node / numeric-column text in .txt) ->
  nuke; .csv/.txt -> csv.

csv. A header row is required; delimiter sniffed from it (``,`` ``;`` tab, else whitespace).
  Header names are matched case-insensitively ignoring spaces ``_ . - ( )``: time: ``time|t|
  seconds|sec`` (s) or ``frame|frames|f``; ``name|track|point|id`` (rows of several tracks may
  be interleaved; channel order = first appearance; without it a single channel "track");
  ``x y z rotation|rot|angle rx ry rz fov focal zoom anchorx anchory``; ``scale scalex scaley``
  are factors, ``scale% scale_percent`` (and the X/Y variants) are percent; corners ``x0 y0 ..
  x3 y3`` or ``tl_x tl_y tr_x tr_y br_x br_y bl_x bl_y``. Empty cells = no key for that
  property on that row. Lines starting with ``#`` are skipped. Same pixel conventions as json.

nuke. (a) ``.chan`` (Camera/Axis export): whitespace columns ``frame tx ty tz rx ry rz [vfov]``
  -> channel "camera" (x,y,z world units, rx,ry,rz degrees as written - Nuke's default ZXY order
  is not converted - fov; linear between rows). (b) ``.nk`` script: nodes ``Class { knob value
  ... }``. Curves follow the NDK "Curve serialisation format" grammar::

      {curve [i|I] KEY...}   KEY = [K|L|S|R|C] [k|l] [x<frame>] <y> [s<lslope>] [t<rslope>] [u<la>] [v<ra>]

  - Interpolation K constant, L linear, S smooth (the default), R Catmull-Rom, C cubic; k/l =
    constant/linear extrapolation. A letter applies to its key and the following keys until the
    next letter (Nuke writes a letter only when it changes). ``i``/``I`` (display flags) ignored.
  - Frames: ``x<frame>`` is explicit; otherwise the first key is at 0, the second at first + 1
    and later keys are extrapolated linearly from the previous two (NDK rule for the first/later
    keys; "first + 1" for the second key matches real Nuke files, e.g. ``K x1001 0 S 400 x1004``,
    where the NDK text says "doubles", which would be non-monotonic).
  - ``s`` = left slope, ``t`` = right slope (``s`` alone sets both), in value per frame; ``u``/``v``
    = left/right handle length, 1 = 1/3 of the distance to the neighbouring key (range 0..3).
  - Automatic slopes (keys without s/t), per Nuke's interpolation definitions: L: slope of the
    segment on each side; K: 0; R: (y[i+1]-y[i-1])/(x[i+1]-x[i-1]); S: the Catmull-Rom slope,
    0 when the key is not between its neighbours in y, and clamped to |slope| <= 3*|segment slope|
    on each side (the convex-hull condition the docs state: the curve never exceeds the keys);
    C: slopes making the second derivative continuous (C2 condition solved jointly for all C
    keys, natural end conditions). End keys: L/R use their single segment slope, S/K 0.
  - Segments: after a K key the value holds; otherwise the cubic Bezier from key i (right slope,
    handle v/3 of the segment) to key i+1 (left slope, handle u/3) - the cubic Hermite when u=v=1.
    Outside the keys: constant extrapolation holds, linear continues with the end key's slope.
  - Not supported (need Nuke's TCL/expression engine to evaluate): knob expressions
    (``{parent.Transform1.translate}``, ``[value ...]``) and ``curve(<expr>)`` time remaps; such a
    knob is skipped with ``warn_once``. Nuke is y-UP with origin bottom-left: every y becomes
  ``H - y`` (H = footage height, see above). Supported nodes (name from the ``name`` knob):
  - Tracker3: ``track1..track4 {x y}`` -> channels "<node>/track1".."<node>/track4" (x,y).
  - Tracker4: the ``tracks`` table; columns come from its header (``track_x``/``track_y``/
    ``name``), falling back to "first two curves after the quoted name"; channel
    "<node>/<track name>" (lookup by just the track name works).
  - Transform: channel "<node>" with anchorX/Y = center, x/y = center + translate (where the
    pivot ends up, like AE anchor+position), rotation = -rotate (Nuke rotate is counter-clockwise
    in y-up, i.e. counter-clockwise on screen), scaleX/Y = scale (single value or {sx sy}).
    Omitted knobs take Nuke's defaults: translate 0, rotate 0, scale 1, center (0,0) (Nuke writes
    center explicitly once set in the GUI; a missing center is taken as 0,0).
  - CornerPin2D: ``to1..to4`` = bottom-left, bottom-right, top-right, top-left in y-up, mapped to
    corners TL=to4, TR=to3, BR=to2, BL=to1 after the y flip; omitted knobs take the defaults
    (0,0) (W,0) (W,H) (0,H). ``from*`` is ignored. mocha's "Nuke Corner Pin" export is this node.
  - Root: ``format "W H ..."`` and ``fps`` feed the file size/rate (above). Other nodes ignored.

after-effects. The "Adobe After Effects X.Y Keyframe Data" clipboard text: a header block of
  ``\\tKey\\tValue`` lines (Units Per Second, Source Width/Height, pixel aspect - pixel aspect is
  recorded but not applied), then sections: a header line (tab-separated path), a column line
  (``\\tFrame\\tX pixels\\t...``), rows ``\\tframe\\tv1\\tv2...`` until a blank line; ends at "End of
  Keyframe Data". AE is already y-down / clockwise. Mapping:
  - "Motion Trackers <Tracker> <Track Point> Feature Center" -> channel "<Tracker>/<Track Point>"
    x,y ("Attach Point" is used only when Feature Center is absent; other tracker props ignored).
  - "Transform Position" -> channel "transform" x,y(,z); "Anchor Point" -> anchorX/Y; "Rotation"
    -> rotation; "X/Y/Z Rotation" -> rx/ry/rz; "Orientation" is added component-wise to rx/ry/rz
    (approximation when both are animated; exact when only one is present); "Scale" (percent)
    -> scaleX/Y factors; "Camera Options Zoom" -> zoom (same "transform" channel). Other
    properties (Opacity, Point of Interest, ...) are ignored.
  - Corner pins: "Effects <Corner Pin #1> <Upper Left #2>" (Upper/Lower Left/Right), CC Power
    Pin ("Top/Bottom Left/Right"), and match-name style "ADBE Corner Pin-0001..0004" (= UL, UR,
    LL, LR, AE's parameter order) / "CC Power Pin-0002..0005" (= TL, TR, BL, BR; assumed from the
    effect's parameter order) -> channel "cornerpin" (further effects: "cornerpin/<effect>")
    corners TL,TR,BR,BL. Corner pin values live in LAYER space: when the same paste also has
    Transform Position AND Anchor Point (mocha's "Corner Pin (supports motion blur)" export), the
    corners are mapped to composition space: ``P + R(rotation) * S(scale) * (c - A)``.
mocha. Mocha's "After Effects Transform Data" and "After Effects Corner Pin Data" exports are the
  AE keyframe text above (same parser); a text starting with a Nuke node/comment (mocha's "Nuke
  Corner Pin") goes to the nuke parser.

fbx. Autodesk FBX 6/7, ASCII ("; FBX 6/7.x.x project file") and binary ("Kaydara FBX Binary  \\0";
  32-bit record offsets below version 7500, 64-bit from 7500; property types Y C I F D L R S and
  arrays f d l i b, zlib when encoding=1). AnimationCurve (KeyTime in ticks of 1/46186158000 s,
  KeyValueFloat/KeyValueDouble) -> AnimationCurveNode (``d|X/Y/Z``, ``d|FocalLength``,
  ``d|FieldOfView``) -> Model (``Lcl Translation/Rotation/Scaling``) or the camera's
  NodeAttribute (``FocalLength``/``FieldOfView``; NodeAttribute -> Model by an OO connection).
  One channel per animated Model, named after it ("Model::Name" / "Name\\0\\1Model"): x,y,z (FBX
  scene units as-is - FBX carries 3D scene units, NOT pixels, even for kind="point"), rx,ry,rz
  (see rotation below), scaleX/scaleY, focal, fov. Components without a curve take the curve
  node's ``d|`` default, else the Model/attribute Properties70 value, else 0 (1 for scale). fov is
  FieldOfView verbatim (vertical in FBX's default ApertureMode) except ApertureMode=1
  (horizontal) with FilmWidth/FilmHeight -> converted to vertical.
  - FBX 6 Takes use ufbx (the optional 3d extra), evaluated at each requested time rather than
    baked to a fixed rate. The first Take is selected, raw scene units and local transforms are
    retained, and Properties60/channel defaults supply unkeyed components. Linear, constant
    previous/next and explicit weighted cubic keys have analytic regression controls. Older
    KeyVer 4002/4003/4004 default-weight spellings are translated when the backend needs it.
    Other legacy tangent modes inherit the backend's approximations or rejection; independent
    exporter/SDK evidence for those modes remains outstanding. See docs/TRACKING.md.
  - Key evaluation (FbxAnimCurveDef): KeyAttrFlags/KeyAttrDataFloat/KeyAttrRefCount are expanded
    per key (RefCount = number of consecutive keys sharing an attribute). Interpolation 0x2
    constant (holds key i; with 0x100 "constant next" holds key i+1 - the key itself keeps its own
    value), 0x4 linear, 0x8 cubic; no attributes -> linear. Cubic segment i uses key i's
    RightSlope and NextLeftSlope (data[0], data[1], value per second) for user/break tangents
    (0x400/0x800); auto (0x100 or none): (v[i+1]-v[i-1])/(t[i+1]-t[i-1]), the end keys the
    segment slope, set to 0 at extrema with clamp 0x1000 and additionally clamped to 3x the
    segment slopes with clamp-progressive 0x4000 (and 0 at the end keys with either clamp); TCB
    0x200: Kochanek-Bartels from data[0..2] = tension, continuity, bias applied to the adjacent
    segment slopes. Weights: data[2] packs RightWeight (low int16) and NextLeftWeight (high int16)
    as weight*9999 (raw float32 bits in binary files, the integer in ASCII; default 3333 = 1/3);
    they are used only when flagged 0x01000000 / 0x02000000, as Bezier handle lengths in
    fractions of the segment duration (unweighted = 1/3 = cubic Hermite). Velocity (data[3],
    flags 0x10000000/0x20000000) is not applied: the SDK does not publish how velocity changes
    evaluation (documented unsupported input). Outside the keys curves hold.
  - Animation stack: the first AnimationStack in file order (the scene format has no attribute to
    choose one); curve nodes connected to other stacks' layers are ignored, curve nodes attached to
    no layer are always used. Layers of that stack are combined in connection order: the first
    layer is the base; later layers with BlendMode 0 (additive) add Weight/100 x value (scale with
    ScaleAccumulationMode 0 multiplies by value^(Weight/100)), BlendMode 1/2 (override /
    override-passthrough) blend towards their value by Weight/100. Rotation is accumulated per
    Euler channel.
  - Rotation: rx, ry, rz are always XYZ-order Euler degrees (X applied first, R = Rz*Ry*Rx). When
    the model has RotationActive=1, its local rotation PreRotation * R(RotationOrder) *
    PostRotation^-1 (RotationOrder 0..5 = XYZ, XZY, YZX, YXZ, ZXY, ZYX, letters in application
    order; 6 = spheric XYZ treated as XYZ; Pre/Post always XYZ) is converted to XYZ angles
    (principal values, ry in [-90, 90]). Without RotationActive the SDK ignores order/pre/post.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import struct
import zlib
from dataclasses import astuple, dataclass, field
from typing import Callable

import numpy as np

from .registry import FEATURES, FULL, PARTIAL, warn_once

log = logging.getLogger("scenerender")

FORMATS = ("json", "csv", "nuke", "after-effects", "mocha", "fbx")
KINDS = ("point", "planar", "camera", "mask", "face")
FBX_TICKS = 46186158000
_CORNERS = {"tl": 0, "tr": 1, "br": 2, "bl": 3}


class TrackError(ValueError):
    """Malformed tracking file."""


# ====================================================================== data model
def _lerp(ts: np.ndarray, v: np.ndarray, tt: float) -> float | np.ndarray:
    n = len(ts)
    if n == 1 or tt <= ts[0]:
        a = v[0]
    elif tt >= ts[-1]:
        a = v[-1]
    else:
        j = int(np.searchsorted(ts, tt, side="right"))
        u = (tt - ts[j - 1]) / (ts[j] - ts[j - 1])
        a = v[j - 1] + (v[j] - v[j - 1]) * u
    return float(a) if np.ndim(a) == 0 else np.array(a, dtype=float)


class _Fn:
    """An exactly evaluable function of track time (seconds), with the key times it contributes."""

    def __init__(self, times, fn: Callable[[float], float] | None = None):
        self.times = np.asarray(times, float)
        self._fn = fn

    def __call__(self, t: float):
        return self._fn(t)


def _bezier(x0, y0, x1, y1, rs, ls, hr, hl, t) -> float:
    """Cubic Bezier segment from (x0,y0) leaving with slope rs to (x1,y1) arriving with slope ls; the
    handles reach hr / hl of the segment duration (1/3 each = the cubic Hermite). Solves x(s) = t."""
    h = x1 - x0
    hr, hl = min(max(hr, 0.0), 1.0), min(max(hl, 0.0), 1.0)
    xs = (x0, x0 + hr * h, x1 - hl * h, x1)
    ys = (y0, y0 + hr * h * rs, y1 - hl * h * ls, y1)

    def b(p, s):
        r = 1.0 - s
        return r * r * r * p[0] + 3 * r * r * s * p[1] + 3 * r * s * s * p[2] + s * s * s * p[3]

    if abs(hr - 1 / 3) < 1e-12 and abs(hl - 1 / 3) < 1e-12:
        s = (t - x0) / h
    else:
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if b(xs, mid) < t else (lo, mid)
        s = 0.5 * (lo + hi)
    return float(b(ys, s))


class _Piecewise(_Fn):
    """Keyed curve in seconds. Segment i (key i -> i+1) mode: 'K' hold key i, 'N' hold key i+1, 'L' linear,
    'B' cubic Bezier with key i's right slope rs[i] / handle hr[i] and key i+1's left slope ls[i+1] /
    handle hl[i+1]. Outside the keys: y + pre/post slope * dt (0 = hold)."""

    def __init__(self, x, y, mode, rs, ls, hr, hl, pre: float = 0.0, post: float = 0.0):
        super().__init__(x)
        self.y = np.asarray(y, float)
        self.mode, self.rs, self.ls, self.hr, self.hl = list(mode), rs, ls, hr, hl
        self.pre, self.post = float(pre), float(post)

    def __call__(self, t: float) -> float:
        x, y = self.times, self.y
        if t <= x[0]:
            return float(y[0] + self.pre * (t - x[0]))
        if t >= x[-1]:
            return float(y[-1] + self.post * (t - x[-1]))
        i = int(np.searchsorted(x, t, side="right")) - 1
        m = self.mode[i]
        if t == x[i]:
            return float(y[i])
        if m == "K":
            return float(y[i])
        if m == "N":
            return float(y[i + 1])
        if m == "L":
            return float(y[i] + (y[i + 1] - y[i]) * (t - x[i]) / (x[i + 1] - x[i]))
        return _bezier(x[i], y[i], x[i + 1], y[i + 1], self.rs[i], self.ls[i + 1], self.hr[i], self.hl[i + 1], t)


@dataclass
class Channel:
    name: str
    times: np.ndarray
    data: dict[str, np.ndarray]                  # values at `times` (exact for curve-driven keys)
    curves: dict[str, Callable] = field(default_factory=dict)   # exact evaluators (Nuke/FBX curves)
    path_codes: tuple[str, ...] = ()

    def fn(self, key: str) -> Callable:
        if key in self.curves:
            return self.curves[key]
        ts, v = self.times, self.data[key]
        return lambda t: _lerp(ts, v, t)

    def derive(self, key: str, f: Callable, *src: str) -> None:
        """data[key] = f(*data[src]) (f must be numpy-vectorised); stays exact if any source is a curve."""
        fns = [self.fn(s) for s in src] if any(s in self.curves for s in src) else None
        self.data[key] = np.asarray(f(*(self.data[s] for s in src)), float)
        if fns:
            self.curves[key] = lambda t: f(*(g(t) for g in fns))
        else:
            self.curves.pop(key, None)

    def drop(self, *keys: str) -> None:
        for k in keys:
            self.data.pop(k, None)
            self.curves.pop(k, None)

    def sample(self, tt: float) -> dict[str, object]:
        out: dict[str, object] = {}
        for k, v in self.data.items():
            if k in self.curves:
                a = self.curves[k](tt)
                out[k] = float(a) if np.ndim(a) == 0 else np.array(a, dtype=float)
            else:
                out[k] = _lerp(self.times, v, tt)
        if self.path_codes:
            coords = iter(out.pop("_path"))
            out["path"] = [(code, *(float(next(coords)) for _ in range({"M": 2, "L": 2, "C": 6, "Z": 0}[code])))
                           for code in self.path_codes]
        return out


@dataclass
class Track:
    id: str
    kind: str
    format: str
    time_offset: float
    channels: dict[str, Channel] = field(default_factory=dict)

    def channel(self, point: str | None) -> tuple[Channel | None, str | None]:
        if not self.channels:
            return None, None
        name, sub = (point or "").strip(), None
        if ":" in name:
            name, _, sub = name.rpartition(":")
            sub = sub.strip() or None
        first = next(iter(self.channels.values()))
        if not name:
            return first, sub
        if name in self.channels:
            return self.channels[name], sub
        for n, c in self.channels.items():
            if n.rsplit("/", 1)[-1] == name:
                return c, sub
        if re.fullmatch(r"\d+", name):
            i = int(name)
            return (list(self.channels.values())[i], sub) if i < len(self.channels) else (None, None)
        if sub is None and name.lower() in (*_CORNERS, "center"):
            return first, name
        return None, None

    def sample(self, point: str | None, t: float) -> dict[str, object] | None:
        ch, sub = self.channel(point)
        if ch is None:
            return None
        v = ch.sample(t - self.time_offset)
        if sub:
            s = sub.lower()
            quad = v.get("corners")
            pts = v.get("points", quad)
            if s in _CORNERS:
                if quad is None:
                    return None
                xy = quad[_CORNERS[s]]
            elif s == "center":
                if pts is None:
                    return None
                xy = (quad if quad is not None else pts).mean(axis=0)
            elif re.fullmatch(r"\d+", s) and pts is not None and int(s) < len(pts):
                xy = pts[int(s)]
            else:
                return None
            v["x"], v["y"] = float(xy[0]), float(xy[1])
        return v

    def times(self) -> np.ndarray:
        ts = [c.times for c in self.channels.values()]
        return np.unique(np.concatenate(ts)) if ts else np.zeros(0)


@dataclass
class Ctx:
    """What a parser needs from the document: rate and footage size, with precedence
    asset > file > document (see the module docstring)."""
    doc_fps: float = 30.0
    asset_fps: float | None = None
    doc_width: float = 0.0
    doc_height: float = 0.0
    asset_width: float | None = None
    asset_height: float | None = None
    kind: str = "point"
    name: str = ""

    def fps(self, file_fps: float | None = None) -> float:
        return float(self.asset_fps or file_fps or self.doc_fps)

    def width(self, file_w: float | None = None) -> float:
        return float(self.asset_width or file_w or self.doc_width)

    def height(self, file_h: float | None = None) -> float:
        return float(self.asset_height or file_h or self.doc_height)


# A series maps a key to (times or None for a constant, values with the key axis first), or to an
# exact curve (_Fn) that is kept for evaluation between keys.
Series = dict[str, "tuple[np.ndarray | None, np.ndarray] | _Fn"]


def _channel(name: str, series: Series) -> Channel:
    """Merge per-key series onto the union of their key times. Tabulated series are interpolated
    linearly (hold outside); curve series (_Fn) are evaluated exactly and kept in Channel.curves."""
    if not series:
        raise TrackError(f"track {name!r} has no data")
    timed = [e.times if isinstance(e, _Fn) else np.asarray(e[0], float) for e in series.values()
             if isinstance(e, _Fn) or (e[0] is not None and len(e[0]))]
    timed = [t for t in timed if len(t)]
    times = np.unique(np.concatenate(timed)) if timed else np.zeros(1)
    data: dict[str, np.ndarray] = {}
    curves: dict[str, Callable] = {}
    for k, e in series.items():
        if isinstance(e, _Fn):
            data[k] = np.stack([np.asarray(e(t), float) for t in times])
            curves[k] = e
            continue
        t, v = e
        v = np.asarray(v, float)
        if t is None:
            data[k] = np.broadcast_to(v, (len(times),) + v.shape).copy()
            continue
        t = np.asarray(t, float)
        if not len(t):
            continue
        order = np.argsort(t, kind="stable")
        t, v = t[order], v[order]
        keep = np.r_[t[1:] != t[:-1], True]           # duplicate times: last one wins
        t, v = t[keep], v[keep]
        flat = v.reshape(len(t), -1)
        cols = [np.interp(times, t, flat[:, c]) for c in range(flat.shape[1])]
        data[k] = np.stack(cols, axis=1).reshape((len(times),) + v.shape[1:])
    ch = Channel(name, times, data, curves)
    cs = [f"_c{i}{a}" for i in range(4) for a in "xy"]
    have = [c for c in cs if c in data]
    if have:
        if len(have) != 8:
            raise TrackError(f"track {name!r}: incomplete corners ({', '.join(sorted(set(cs) - set(have)))} missing)")
        data["corners"] = np.stack([np.stack([data[f"_c{i}x"], data[f"_c{i}y"]], -1) for i in range(4)], 1)
        if any(c in curves for c in cs):
            fns = [[ch.fn(f"_c{i}x"), ch.fn(f"_c{i}y")] for i in range(4)]
            curves["corners"] = lambda t: np.array([[fx(t), fy(t)] for fx, fy in fns])
        ch.drop(*cs)
    return ch


def _records(recs: list[tuple[float, dict]]) -> Series:
    """[(t, {key: value})] -> Series; keys may be sparse."""
    acc: dict[str, tuple[list, list]] = {}
    for t, vals in recs:
        for k, v in vals.items():
            a = acc.setdefault(k, ([], []))
            a[0].append(t)
            a[1].append(v)
    out: Series = {}
    for k, (ts, vs) in acc.items():
        shapes = {np.shape(v) for v in vs}
        if len(shapes) > 1:
            raise TrackError(f"{k!r} changes shape between keys ({sorted(shapes)}); vertex counts must be fixed")
        out[k] = (np.asarray(ts, float), np.asarray(vs, float))
    return out


def _text(data: str | bytes) -> str:
    if isinstance(data, str):
        return data
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _num(s) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# ====================================================================== json
_JSON_SCALARS = ("x", "y", "z", "rotation", "rx", "ry", "rz", "scaleX", "scaleY", "anchorX", "anchorY",
                 "fov", "focal", "zoom")
_PATH_TOK = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _mask_commands(d: str) -> list[tuple]:
    """Normalize SVG paths without flattening their curves or connecting subpaths."""
    from .geometry import parse_svg_path
    toks = _PATH_TOK.findall(d)
    if re.sub(r"[\s,]+", "", d) != "".join(toks):
        raise TrackError(f"mask path {d[:40]!r}: invalid path data")
    if not toks or toks[0] not in ("M", "m"):
        raise TrackError("mask path must start with a move command")
    # Validate counts before calling the shared geometry parser, which is also
    # used for forgiving native shape input.
    i = 0
    counts = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
    while i < len(toks):
        code = toks[i].upper()
        if code not in counts:
            raise TrackError("mask path: numbers without a command")
        j = i + 1
        while j < len(toks) and toks[j] not in "MmLlHhVvCcSsQqTtAaZz":
            j += 1
        n, need = j - i - 1, counts[code]
        if (need == 0 and n) or (need and (not n or n % need)):
            raise TrackError(f"mask path: bad argument count for {code}")
        i = j
    commands = parse_svg_path(d)
    out = []
    for command in commands:
        if command[0] == "M" and out and out[-1][0] != "Z":
            out.append(("Z",))
        out.append(command)
    if out[-1][0] != "Z":
        out.append(("Z",))
    return out


def _polygon_commands(points):
    return [("M" if i == 0 else "L", float(x), float(y)) for i, (x, y) in enumerate(points)] + [("Z",)]


def parse_json(data: str | bytes, ctx: Ctx | None = None) -> dict[str, Channel]:
    ctx = ctx or Ctx()
    root = json.loads(_text(data))
    if isinstance(root, list):
        root = {"tracks": root}
    fps = ctx.fps(_num(root.get("fps")))
    tracks = root.get("tracks")
    if tracks is None and "keys" in root:
        tracks = [{"name": root.get("name", "track"), "keys": root["keys"]}]
    if isinstance(tracks, dict):
        items = list(tracks.items())
    elif isinstance(tracks, list):
        items = [(t.get("name", str(i)), t) for i, t in enumerate(tracks)]
    else:
        raise TrackError('json: expected a "tracks" object or list')
    out: dict[str, Channel] = {}
    for name, spec in items:
        keys = spec.get("keys", []) if isinstance(spec, dict) else spec
        recs = []
        path_codes = None
        for k in keys:
            if "t" in k or "time" in k:
                t = float(k.get("t", k.get("time")))
            elif "frame" in k:
                t = float(k["frame"]) / fps
            else:
                raise TrackError(f'json track {name!r}: key without "t" or "frame"')
            vals: dict = {n: float(k[n]) for n in _JSON_SCALARS if k.get(n) is not None}
            if k.get("scale") is not None:
                s = k["scale"]
                sx, sy = (s, s) if isinstance(s, (int, float)) else s
                vals.setdefault("scaleX", float(sx))
                vals.setdefault("scaleY", float(sy))
            if k.get("corners") is not None:
                c = np.asarray(k["corners"], float)
                if c.shape != (4, 2):
                    raise TrackError(f"json track {name!r}: corners must be 4 [x, y] pairs")
                vals["corners"] = c
            if k.get("points") is not None:
                vals["points"] = np.asarray(k["points"], float).reshape(-1, 2)
                commands = _polygon_commands(vals["points"])
            elif k.get("path") is not None:
                commands = _mask_commands(k["path"])
                vals["points"] = np.asarray([c[-2:] for c in commands if c[0] != "Z"], float)
            else:
                commands = None
            if commands is not None:
                codes = tuple(c[0] for c in commands)
                if path_codes is not None and path_codes != codes:
                    raise TrackError(f"json track {name!r}: mask path topology changes between keys")
                path_codes = codes
                vals["_path"] = np.asarray([v for c in commands for v in c[1:]], float)
            recs.append((t, vals))
        channel = _channel(str(name), _records(recs))
        channel.path_codes = path_codes or ()
        out[str(name)] = channel
    return out


# ====================================================================== csv
_CSV_COLS = {
    **{a: "t" for a in ("time", "t", "seconds", "sec", "secs")},
    **{a: "frame" for a in ("frame", "frames", "f")},
    **{a: "name" for a in ("name", "track", "point", "id", "trackname")},
    **{a: a for a in ("x", "y", "z", "rx", "ry", "rz", "fov", "focal", "zoom")},
    "rotation": "rotation", "rot": "rotation", "angle": "rotation",
    "anchorx": "anchorX", "anchory": "anchorY",
    "scale": "scale", "scalex": "scaleX", "scaley": "scaleY",
    "scale%": "scale%", "scalepercent": "scale%", "scalex%": "scaleX%", "scalexpercent": "scaleX%",
    "scaley%": "scaleY%", "scaleypercent": "scaleY%",
    **{f"{a}{i}": f"_c{i}{a}" for i in range(4) for a in "xy"},
    **{f"{c}{a}": f"_c{i}{a}" for c, i in _CORNERS.items() for a in "xy"},
}


def parse_csv(data: str | bytes, ctx: Ctx | None = None) -> dict[str, Channel]:
    ctx = ctx or Ctx()
    lines = [ln for ln in _text(data).splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if len(lines) < 2:
        raise TrackError("csv: needs a header row and at least one data row")
    counts = {d: lines[0].count(d) for d in (",", ";", "\t")}
    delim = max(counts, key=counts.get)
    rows = (list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)) if counts[delim]
            else [ln.split() for ln in lines])
    cols = [_CSV_COLS.get(re.sub(r"[\s_.\-()]", "", h.lower())) for h in rows[0]]
    if "t" not in cols and "frame" not in cols:
        raise TrackError("csv: needs a time/t/seconds or frame column")
    fps = ctx.fps()
    groups: dict[str, list] = {}
    for ln, row in enumerate(rows[1:], 2):
        cell = {c: v.strip() for c, v in zip(cols, row) if c and v.strip()}
        name = cell.pop("name", "track")
        try:
            t = float(cell.pop("t")) if "t" in cell else float(cell.pop("frame")) / fps
            cell.pop("frame", None)
            vals = {k: float(v) for k, v in cell.items()}
        except (KeyError, ValueError) as e:
            raise TrackError(f"csv line {ln}: {e}") from None
        for src, dst in (("scale", None), ("scale%", None), ("scaleX%", "scaleX"), ("scaleY%", "scaleY")):
            if src in vals:
                v = vals.pop(src) / (100.0 if src.endswith("%") else 1.0)
                for d in ([dst] if dst else ["scaleX", "scaleY"]):
                    vals.setdefault(d, v)
        groups.setdefault(name, []).append((t, vals))
    return {n: _channel(n, _records(r)) for n, r in groups.items()}


# ====================================================================== nuke
_NUM_ROW = re.compile(r"\s*[-+]?[\d.]+(?:[eE][-+]?\d+)?(?:\s+[-+]?[\d.]+(?:[eE][-+]?\d+)?)*\s*")
_NK_TOK = re.compile(r'"(?:\\.|[^"\\])*"|[{}]|[^\s{}"]+')


def _looks_chan(text: str) -> bool:
    rows = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return bool(rows) and all(_NUM_ROW.fullmatch(r) and len(r.split()) >= 7 for r in rows)


def _looks_nuke(text: str) -> bool:
    return bool(re.search(r"(?m)^\s*(?:#!|[A-Z]\w*\s*\{)", text))


def parse_nuke(data: str | bytes, ctx: Ctx | None = None) -> dict[str, Channel]:
    ctx = ctx or Ctx()
    text = _text(data)
    return _parse_chan(text, ctx) if _looks_chan(text) else _parse_nk(text, ctx)


def _parse_chan(text: str, ctx: Ctx) -> dict[str, Channel]:
    rows = np.array([[float(v) for v in ln.split()] for ln in text.splitlines()
                     if ln.strip() and not ln.lstrip().startswith("#")], float)
    t = rows[:, 0] / ctx.fps()
    keys = ["x", "y", "z", "rx", "ry", "rz", "fov"][: rows.shape[1] - 1]
    return {"camera": _channel("camera", {k: (t, rows[:, i + 1]) for i, k in enumerate(keys)})}


class _Q(str):
    """A quoted .nk string token."""


def _nk_tree(text: str) -> list:
    text = re.sub(r"(?m)^\s*#.*$", "", text)
    stack: list[list] = [[]]
    for m in _NK_TOK.finditer(text):
        tok = m.group()
        if tok == "{":
            stack.append([])
        elif tok == "}":
            if len(stack) > 1:
                g = stack.pop()
                stack[-1].append(g)
        elif tok.startswith('"'):
            stack[-1].append(_Q(re.sub(r"\\(.)", r"\1", tok[1:-1])))
        else:
            stack[-1].append(tok)
    return stack[0]


class _NkKeys:
    """Keys of one Nuke `{curve ...}` (frames, per-frame slopes), built into a _Piecewise per fps."""

    def __init__(self, keys: list[dict]):
        self.keys = keys

    def build(self, fps: float) -> "tuple[None, np.ndarray] | _Piecewise":
        ks = self.keys
        if len(ks) == 1:
            return (None, np.asarray(ks[0]["y"]))
        x = np.array([k["x"] for k in ks])
        y = np.array([k["y"] for k in ks])
        if np.any(np.diff(x) <= 0):
            raise _Skip("curve key frames are not increasing")
        ls, rs = _nk_slopes(x, y, [k["i"] for k in ks], [k["s"] for k in ks], [k["t"] for k in ks])
        first, last = ks[0], ks[-1]
        pre = (first["s"] if first["s"] is not None else rs[0]) if first["e"] == "l" else 0.0
        post = (last["t"] if last["t"] is not None else last["s"] if last["s"] is not None else ls[-1]) \
            if last["e"] == "l" else 0.0
        mode = ["K" if k["i"] == "K" else "B" for k in ks[:-1]]
        return _Piecewise(x / fps, y, mode, rs * fps, ls * fps, np.array([k["v"] for k in ks]) / 3,
                          np.array([k["u"] for k in ks]) / 3, pre * fps, post * fps)


def _nk_slopes(x, y, interp, s_in, t_in) -> tuple[np.ndarray, np.ndarray]:
    """Left/right slopes (value per frame) of every key, following Nuke's interpolation rules; explicit
    s/t override. See the module docstring for the exact rules."""
    n = len(x)
    sec = np.diff(y) / np.diff(x)
    ls, rs = np.zeros(n), np.zeros(n)
    cubic: list[int] = []
    for i, it in enumerate(interp):
        L = sec[i - 1] if i > 0 else None
        R = sec[i] if i < n - 1 else None
        if it == "C" and s_in[i] is None and t_in[i] is None:
            cubic.append(i)
            continue
        if it == "L":
            ls[i], rs[i] = (L if L is not None else R), (R if R is not None else L)
        elif it in ("R", "S"):
            if L is None or R is None:
                c = (L if L is not None else R) if it == "R" else 0.0
            else:
                c = (y[i + 1] - y[i - 1]) / (x[i + 1] - x[i - 1])
                if it == "S":
                    c = 0.0 if L * R <= 0 else math.copysign(min(abs(c), 3 * abs(L), 3 * abs(R)), c)
            ls[i] = rs[i] = c
        if s_in[i] is not None:
            ls[i] = rs[i] = s_in[i]
        if t_in[i] is not None:
            rs[i] = t_in[i]
    if cubic:
        # C2 continuity at each unknown key (natural end conditions at the curve's ends):
        #   m[i-1]/h0 + 2 m[i] (1/h0 + 1/h1) + m[i+1]/h1 = 3 (d0/h0 + d1/h1)
        idx = {k: j for j, k in enumerate(cubic)}
        A, b = np.zeros((len(cubic), len(cubic))), np.zeros(len(cubic))

        def term(row, k, coef, side):
            if k in idx:
                A[row, idx[k]] += coef
            else:
                b[row] -= coef * (rs[k] if side == "r" else ls[k])

        for row, i in enumerate(cubic):
            if n == 1:
                A[row, row] = 1.0
            elif i == 0:
                A[row, row] += 2.0
                term(row, 1, 1.0, "l")
                b[row] += 3 * sec[0]
            elif i == n - 1:
                A[row, row] += 2.0
                term(row, n - 2, 1.0, "r")
                b[row] += 3 * sec[-1]
            else:
                h0, h1 = x[i] - x[i - 1], x[i + 1] - x[i]
                A[row, row] += 2 * (1 / h0 + 1 / h1)
                term(row, i - 1, 1 / h0, "r")
                term(row, i + 1, 1 / h1, "l")
                b[row] += 3 * (sec[i - 1] / h0 + sec[i] / h1)
        m = np.linalg.solve(A, b)
        for i, j in idx.items():
            ls[i] = rs[i] = m[j]
    return ls, rs


_NK_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _nk_curve(g: list) -> "Comp":
    """Parse `{curve [i|I] key...}`; key = [K|L|S|R|C] [k|l] [x<frame>] <y> [s<l>] [t<r>] [u<la>] [v<ra>]."""
    keys: list[dict] = []
    interp, extrap, pending = "S", "k", None
    for tok in g[1:]:
        if isinstance(tok, list) or tok in ("i", "I"):
            continue
        if tok in ("K", "L", "S", "R", "C"):
            interp = tok
            continue
        if tok in ("k", "l"):
            extrap = tok
            continue
        m = re.fullmatch(r"([xstuv])(" + _NK_NUM + ")", tok)
        if m:
            c, v = m[1], float(m[2])
            if c == "x":
                pending = v
            elif keys:
                keys[-1][c] = v
            continue
        v = _num(tok)
        if v is None:
            return None                       # not a plain curve (expression / unknown token)
        if pending is not None:
            xf = pending
        elif not keys:
            xf = 0.0
        elif len(keys) == 1:
            xf = keys[0]["x"] + 1.0
        else:
            xf = 2 * keys[-1]["x"] - keys[-2]["x"]
        keys.append({"x": xf, "y": v, "i": interp, "e": extrap, "s": None, "t": None, "u": 1.0, "v": 1.0})
        pending = None
    return _NkKeys(keys) if keys else None


# A Nuke knob component: a curve, a constant (None, value), or None for an unsupported expression.
Comp = "_NkKeys | tuple[None, np.ndarray] | None"


def _nk_comps(v) -> list:
    if isinstance(v, str):
        n = _num(v)
        return [(None, np.asarray(n)) if n is not None else None]
    if v and v[0] == "curve":
        return [_nk_curve(v)]
    out: list = []
    for e in v:
        if isinstance(e, str):
            out += _nk_comps(e)
        elif e and e[0] == "curve":
            out.append(_nk_curve(e))
        elif len(e) == 1:
            out += _nk_comps(e)
        else:
            out.append(None)
    return out


def _nk_nodes(tree: list) -> list[tuple[str, dict]]:
    nodes = []
    for i in range(len(tree) - 1):
        cls, body = tree[i], tree[i + 1]
        if isinstance(cls, str) and not isinstance(cls, _Q) and isinstance(body, list) and re.fullmatch(r"[A-Za-z]\w*", cls):
            if i > 0 and isinstance(tree[i - 1], str) and tree[i - 1] in ("set", "push"):
                continue
            knobs, j = {}, 0
            while j + 1 < len(body):
                if isinstance(body[j], str):
                    knobs[body[j]] = body[j + 1]
                j += 2
            nodes.append((cls, knobs))
    return nodes


def _parse_nk(text: str, ctx: Ctx) -> dict[str, Channel]:
    nodes = _nk_nodes(_nk_tree(text))
    if not nodes:
        raise TrackError("nuke: no nodes found")
    fw = fh = ffps = None
    for cls, k in nodes:
        if cls == "Root":
            fmt = str(k.get("format", "")).split()
            if len(fmt) >= 2 and _num(fmt[0]) and _num(fmt[1]):
                fw, fh = float(fmt[0]), float(fmt[1])
            ffps = _num(k.get("fps")) if isinstance(k.get("fps"), str) else None
    fps, W, H = ctx.fps(ffps), ctx.width(fw), ctx.height(fh)
    counts: dict[str, int] = {}

    def ser(c, key: str, what: str):
        if c is None:
            raise _Skip(f"{what}: expressions / non-curve values need Nuke's TCL expression engine")
        return c.build(fps) if isinstance(c, _NkKeys) else c

    def pair(knob, default, what) -> list:
        if knob is None:
            return [(None, np.asarray(float(d))) for d in default]
        cs = _nk_comps(knob)
        if len(cs) == 1:
            cs = cs * 2
        if len(cs) < 2:
            raise _Skip(f"{what}: expected two components")
        return cs[:2]

    out: dict[str, Channel] = {}
    for cls, k in nodes:
        counts[cls] = counts.get(cls, 0) + 1
        node = str(k.get("name", f"{cls}{counts[cls]}"))
        try:
            if cls == "Tracker3":
                for n in range(1, 5):
                    if f"track{n}" in k:
                        cx, cy = pair(k[f"track{n}"], (0, 0), f"{node}.track{n}")
                        ch = _channel(f"{node}/track{n}", {"x": ser(cx, "x", node), "y": ser(cy, "y", node)})
                        ch.derive("y", lambda v: H - v, "y")
                        out[ch.name] = ch
            elif cls == "Tracker4" and isinstance(k.get("tracks"), list):
                for name, cx, cy in _tracker4_rows(k["tracks"]):
                    ch = _channel(f"{node}/{name}", {"x": ser(cx, "x", node), "y": ser(cy, "y", node)})
                    ch.derive("y", lambda v: H - v, "y")
                    out[ch.name] = ch
            elif cls == "Transform":
                tx, ty = pair(k.get("translate"), (0, 0), "translate")
                cx, cy = pair(k.get("center"), (0, 0), "center")
                sx, sy = pair(k.get("scale"), (1, 1), "scale")
                (rot,) = _nk_comps(k["rotate"])[:1] if "rotate" in k else [(None, np.asarray(0.0))]
                s = {"_tx": tx, "_ty": ty, "anchorX": cx, "_cy": cy, "rotation": rot, "scaleX": sx, "scaleY": sy}
                ch = _channel(node, {key: ser(c, key, f"{node}.{key}") for key, c in s.items()})
                ch.derive("x", lambda c, t: c + t, "anchorX", "_tx")
                ch.derive("y", lambda c, t: H - (c + t), "_cy", "_ty")
                ch.derive("anchorY", lambda c: H - c, "_cy")
                ch.derive("rotation", lambda r: 0.0 - r, "rotation")
                ch.drop("_tx", "_ty", "_cy")
                out[node] = ch
            elif cls == "CornerPin2D":
                dflt = {1: (0, 0), 2: (W, 0), 3: (W, H), 4: (0, H)}
                s: Series = {}
                for i, n in enumerate((4, 3, 2, 1)):      # TL, TR, BR, BL <- to4, to3, to2, to1
                    px, py = pair(k.get(f"to{n}"), dflt[n], f"to{n}")
                    s[f"_c{i}x"], s[f"_c{i}y"] = ser(px, "x", node), ser(py, "y", node)
                ch = _channel(node, s)
                ch.derive("corners", lambda c: np.asarray(c) * [1.0, -1.0] + [0.0, H], "corners")
                out[node] = ch
        except _Skip as e:
            warn_once("trackData", f"{ctx.name}:{node}", f"nuke node {node}: {e}; skipped")
    if not out:
        raise TrackError("nuke: no Tracker3/Tracker4/Transform/CornerPin2D data found")
    return out


class _Skip(Exception):
    pass


def _tracker4_rows(tracks: list):
    groups = [g for g in tracks if isinstance(g, list)]
    if len(groups) < 3:
        return
    cols = [c[3] if len(c) > 3 and isinstance(c[3], str) else None for c in groups[1] if isinstance(c, list)]
    ni = cols.index("name") if "name" in cols else None
    xi = cols.index("track_x") if "track_x" in cols else None
    yi = cols.index("track_y") if "track_y" in cols else None
    for row in groups[2]:
        if not isinstance(row, list):
            continue
        n = ni if ni is not None and ni < len(row) else next((i for i, c in enumerate(row) if isinstance(c, _Q)), None)
        if n is None:
            continue
        a, b = (xi, yi) if xi is not None and yi is not None else (n + 1, n + 2)
        if b >= len(row):
            continue
        cx, cy = (_nk_comps(row[a])[0] if row[a] else None), (_nk_comps(row[b])[0] if row[b] else None)
        yield str(row[n]), cx, cy


# ====================================================================== after effects / mocha
_AE_HEADER = re.compile(r"Adobe After Effects\s+[\d.]+\s+Keyframe Data")
_PIN_NAMES = {"upper left": 0, "top left": 0, "upper right": 1, "top right": 1,
              "lower right": 2, "bottom right": 2, "lower left": 3, "bottom left": 3}
_PIN_MATCH = {"ADBE Corner Pin": {1: 0, 2: 1, 3: 3, 4: 2}, "CC Power Pin": {2: 0, 3: 1, 4: 3, 5: 2}}
_TRANSFORM = {
    "position": [("x", 1), ("y", 1), ("z", 1)], "anchor point": [("anchorX", 1), ("anchorY", 1)],
    "rotation": [("rotation", 1)], "x rotation": [("rx", 1)], "y rotation": [("ry", 1)],
    "z rotation": [("rz", 1)], "orientation": [("_ox", 1), ("_oy", 1), ("_oz", 1)],
    "scale": [("scaleX", .01), ("scaleY", .01)],
}


def _ae_corner(effect: str, param: str) -> int | None:
    i = _PIN_NAMES.get(re.sub(r"\s*#\d+$", "", param).strip().lower())
    if i is not None:
        return i
    for match, table in _PIN_MATCH.items():
        m = re.fullmatch(re.escape(match) + r"-(\d+)", param.strip())
        if m:
            return table.get(int(m[1]))
    return None


def parse_after_effects(data: str | bytes, ctx: Ctx | None = None) -> dict[str, Channel]:
    ctx = ctx or Ctx()
    lines = _text(data).splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if start is None or not _AE_HEADER.fullmatch(lines[start].strip()):
        raise TrackError('after-effects: missing "Adobe After Effects X.Y Keyframe Data" header')
    meta: dict[str, str] = {}
    sections: list[list] = []           # [path, columns, rows]
    cur = None
    for ln in lines[start + 1:]:
        if not ln.strip():
            cur = None
            continue
        if ln.strip() == "End of Keyframe Data":
            break
        cells = ln.split("\t")
        if not ln[0].isspace():
            cur = [[c.strip() for c in cells if c.strip()], None, []]
            sections.append(cur)
            continue
        vals = [c.strip() for c in cells[1:]]
        if cur is None:
            if len(vals) >= 2:
                meta[vals[0]] = vals[1]
        elif cur[1] is None:
            cur[1] = [v for v in vals if v]
        else:
            nums = [_num(v) for v in vals if v]
            if nums and None not in nums:
                cur[2].append(nums)
    fps = ctx.fps(_num(meta.get("Units Per Second")))
    chans: dict[str, Series] = {}
    pins: list[str] = []

    def put(ch: str, spec, rows):
        s = chans.setdefault(ch, {})
        rows = [r for r in rows if len(r) >= 2]
        if not rows:
            return
        t = np.array([r[0] for r in rows]) / fps
        for i, (key, f) in enumerate(spec):
            if all(len(r) > i + 1 for r in rows):
                s[key] = (t, np.array([r[i + 1] for r in rows]) * f)

    for path, _cols, rows in sections:
        if not path:
            continue
        head = path[0].lower()
        if head == "motion trackers" and len(path) >= 4:
            prop = path[3].lower()
            if prop in ("feature center", "attach point"):
                pre = "" if prop == "feature center" else "_at"
                put(f"{path[1]}/{path[2]}", [(pre + "x", 1), (pre + "y", 1)], rows)
        elif head == "transform" and len(path) >= 2 and path[1].lower() in _TRANSFORM:
            put("transform", _TRANSFORM[path[1].lower()], rows)
        elif head == "camera options" and len(path) >= 2 and path[1].lower() == "zoom":
            put("transform", [("zoom", 1)], rows)
        else:
            effect, param = (path[1], path[2]) if head == "effects" and len(path) >= 3 else (path[0], path[-1])
            if len(path) >= 2 and re.search(r"corner pin|power pin", effect, re.I):
                i = _ae_corner(effect, param)
                if i is not None:
                    if effect not in pins:
                        pins.append(effect)
                    name = "cornerpin" if pins.index(effect) == 0 else f"cornerpin/{effect}"
                    put(name, [(f"_c{i}x", 1), (f"_c{i}y", 1)], rows)
    tr = chans.get("transform", {})
    comp = all(k in tr for k in ("x", "y", "anchorX", "anchorY"))
    out: dict[str, Channel] = {}
    for name, s in chans.items():
        if not s:
            continue
        if name.startswith("cornerpin") and comp:
            s = dict(s, _px=tr["x"], _py=tr["y"], _ax=tr["anchorX"], _ay=tr["anchorY"])
            for a, b in (("rotation", "_rot"), ("scaleX", "_sx"), ("scaleY", "_sy")):
                if a in tr:
                    s[b] = tr[a]
        ch = _channel(name, s)
        d = ch.data
        if "_atx" in d:
            ax, ay = d.pop("_atx"), d.pop("_aty")
            d.setdefault("x", ax)
            d.setdefault("y", ay)
        for o, r in (("_ox", "rx"), ("_oy", "ry"), ("_oz", "rz")):
            if o in d:
                d[r] = d[r] + d[o] if r in d else d[o]
                del d[o]
        if "_px" in d:
            _pin_to_comp(d)
        out[name] = ch
    if not out:
        raise TrackError("after-effects: no tracker, transform or corner pin data found")
    return out


def _pin_to_comp(d: dict[str, np.ndarray]) -> None:
    n = len(d["_px"])
    px, py, ax, ay = (d.pop(k)[:, None] for k in ("_px", "_py", "_ax", "_ay"))
    rot = np.radians(d.pop("_rot", np.zeros(n)))[:, None]
    sx, sy = d.pop("_sx", np.ones(n))[:, None], d.pop("_sy", np.ones(n))[:, None]
    c = d["corners"]
    dx, dy = (c[..., 0] - ax) * sx, (c[..., 1] - ay) * sy
    cs, sn = np.cos(rot), np.sin(rot)
    c[..., 0] = px + dx * cs - dy * sn
    c[..., 1] = py + dx * sn + dy * cs


def parse_mocha(data: str | bytes, ctx: Ctx | None = None) -> dict[str, Channel]:
    text = _text(data)
    head = text.lstrip()
    if _AE_HEADER.match(head):
        return parse_after_effects(text, ctx)
    if _looks_nuke(head):
        return parse_nuke(text, ctx)
    raise TrackError("mocha: expected an After Effects keyframe-data or Nuke corner-pin export")


# ====================================================================== fbx
_FBX_MAGIC = b"Kaydara FBX Binary  \x00"


@dataclass
class _FNode:
    name: str
    props: list
    children: list

    def child(self, name: str) -> _FNode | None:
        return next((c for c in self.children if c.name == name), None)


def _fbx_binary(b: bytes) -> list[_FNode]:
    ver = struct.unpack_from("<I", b, 23)[0]
    hdr = struct.Struct("<QQQ" if ver >= 7500 else "<III")
    # Pre-7000 Takes use byte properties for bare interpolation letters too.
    scal = {"Y": "<h", "C": "<B" if ver < 7000 else "<?", "I": "<i", "F": "<f", "D": "<d", "L": "<q"}
    arrs = {"f": "<f4", "d": "<f8", "l": "<i8", "i": "<i4", "b": "u1"}

    def prop(pos: int):
        t = chr(b[pos])
        pos += 1
        if t in scal:
            v = struct.unpack_from(scal[t], b, pos)[0]
            return v, pos + struct.calcsize(scal[t])
        if t in "SR":
            (n,) = struct.unpack_from("<I", b, pos)
            raw = b[pos + 4:pos + 4 + n]
            return (raw.decode("utf-8", "replace") if t == "S" else raw), pos + 4 + n
        if t in arrs:
            n, enc, clen = struct.unpack_from("<III", b, pos)
            raw = b[pos + 12:pos + 12 + clen]
            if enc == 1:
                raw = zlib.decompress(raw)
            return np.frombuffer(raw, dtype=arrs[t], count=n).copy(), pos + 12 + clen
        raise TrackError(f"fbx: unknown property type {t!r} at byte {pos - 1}")

    def node(pos: int) -> tuple[_FNode | None, int]:
        end, nprops, plen = hdr.unpack_from(b, pos)
        pos += hdr.size
        if end == 0:
            return None, pos + 1
        if end <= pos or end > len(b):
            raise TrackError('fbx: invalid or truncated record offset')
        nl = b[pos]
        name = b[pos + 1:pos + 1 + nl].decode("ascii", "replace")
        pos += 1 + nl
        props_end = pos + plen
        if props_end > end:
            raise TrackError('fbx: truncated record properties')
        props = []
        for _ in range(nprops):
            if pos >= props_end:
                raise TrackError('fbx: truncated property list')
            v, pos = prop(pos)
            props.append(v)
        if pos != props_end:
            raise TrackError('fbx: inconsistent property list length')
        children = []
        while pos < end:
            c, pos = node(pos)
            if pos > end:
                raise TrackError('fbx: child outside parent record')
            if c is None:
                break
            children.append(c)
        return _FNode(name, props, children), end

    out, pos = [], 27
    while pos + hdr.size < len(b):
        n, pos = node(pos)
        if n is None:
            break
        out.append(n)
    return out


_FBXA_TOK = re.compile(r'(?P<ws>\s+|;[^\n]*)|"(?P<s>[^"]*)"|(?P<k>[A-Za-z_][\w|\-]*):'
                       r"|(?P<n>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)|\*(?P<star>\d+)"
                       r"|(?P<i>[A-Za-z_][\w|\-]*)|(?P<p>[{},])")


def _fbx_ascii(text: str) -> list[_FNode]:
    toks: list[tuple[str, object]] = []
    pos = 0
    while pos < len(text):
        m = _FBXA_TOK.match(text, pos)
        if not m:
            raise TrackError(f"fbx ascii: unexpected character {text[pos]!r} at offset {pos}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        v = m.group(kind)
        if kind == "n":
            v = float(v) if re.search(r"[.eE]", v) else int(v)
        toks.append((v, v) if kind == "p" else (kind, v))
    i = 0

    def value():
        nonlocal i
        kind, v = toks[i]
        i += 1
        if kind != "star":
            return v
        if toks[i][0] != "{" or toks[i + 1] != ("k", "a"):
            raise TrackError("fbx ascii: malformed array")
        i += 2
        vals = []
        while toks[i][0] != "}":
            if toks[i][0] == "n":
                vals.append(toks[i][1])
            i += 1
        i += 1
        return np.asarray(vals)

    def nodes() -> list[_FNode]:
        nonlocal i
        out = []
        while i < len(toks):
            kind, name = toks[i]
            if kind == "}":
                i += 1
                return out
            i += 1
            if kind != "k":
                continue
            props, want = [], True
            while i < len(toks):
                kind = toks[i][0]
                if want and kind in ("s", "n", "i", "star"):
                    props.append(value())
                    want = False
                elif kind == ",":
                    i += 1
                    want = True
                else:
                    break
            children = []
            if i < len(toks) and toks[i][0] == "{":
                i += 1
                children = nodes()
            out.append(_FNode(str(name), props, children))
        return out

    return nodes()


def _p70(n: _FNode | None) -> dict[str, list]:
    p = n.child("Properties70") if n else None
    return {c.props[0]: c.props[4:] for c in (p.children if p else []) if c.name == "P" and c.props}


def _fbx_name(s) -> str:
    s = s.decode("utf-8", "replace") if isinstance(s, bytes) else str(s)
    if "\x00\x01" in s:
        return s.split("\x00\x01")[0]
    return s.split("::", 1)[1] if "::" in s else s


# FbxAnimCurveDef key attribute flags (KeyAttrFlags)
FBX_CONSTANT, FBX_LINEAR, FBX_CUBIC = 0x2, 0x4, 0x8
FBX_CONSTANT_NEXT = 0x100                                   # with FBX_CONSTANT
FBX_TAN_AUTO, FBX_TAN_TCB, FBX_TAN_USER, FBX_TAN_BREAK = 0x100, 0x200, 0x400, 0x800
FBX_TAN_CLAMP, FBX_TAN_TIME_INDEPENDENT, FBX_TAN_CLAMP_PROGRESSIVE = 0x1000, 0x2000, 0x4000
FBX_WEIGHTED_RIGHT, FBX_WEIGHTED_NEXT_LEFT = 0x01000000, 0x02000000
FBX_VELOCITY_RIGHT, FBX_VELOCITY_NEXT_LEFT = 0x10000000, 0x20000000
_ROT_ORDERS = {0: "XYZ", 1: "XZY", 2: "YZX", 3: "YXZ", 4: "ZXY", 5: "ZYX", 6: "XYZ"}


def _fbx_curve(o: _FNode) -> _Piecewise | None:
    kt, kv = o.child("KeyTime"), o.child("KeyValueFloat") or o.child("KeyValueDouble")
    if not (kt and kv and kt.props and kv.props):
        return None
    t = np.asarray(kt.props[0], np.int64).astype(float) / FBX_TICKS
    v = np.asarray(kv.props[0], float)
    n = len(t)
    if not n or n != len(v):
        return None
    if n > 1 and np.any(np.diff(t) <= 0):
        raise TrackError(f"fbx: AnimationCurve {o.props[0]} key times are not increasing")
    fn, dn, rn = (o.child(k) for k in ("KeyAttrFlags", "KeyAttrDataFloat", "KeyAttrRefCount"))
    if fn is None or not fn.props:
        flags, data, bits = np.full(n, FBX_LINEAR, np.int64), np.zeros((n, 4)), np.zeros(n, np.int64)
    else:
        f = np.asarray(fn.props[0], np.int64) & 0xFFFFFFFF
        raw = np.asarray(dn.props[0]) if dn is not None and dn.props else np.zeros(4 * len(f), np.float32)
        # the 3rd float of each attribute packs two int16 weights (x 9999): its raw bits in binary
        # files (float32 array), the integer itself in ASCII files
        rbits = (raw.view(np.uint32).astype(np.int64) if raw.dtype == np.float32
                 else np.round(raw.astype(float)).astype(np.int64) & 0xFFFFFFFF)
        raw, rbits = raw.astype(float).reshape(-1, 4), rbits.reshape(-1, 4)[:, 2]
        ref = np.asarray(rn.props[0], np.int64) if rn is not None and rn.props else np.full(len(f), n)
        m = min(len(f), len(raw), len(ref))
        rep = np.repeat(np.arange(m), ref[:m])
        rep = np.r_[rep, np.full(max(0, n - len(rep)), m - 1)][:n]
        flags, data, bits = f[rep], raw[rep], rbits[rep]
    wr, wl = (bits & 0xFFFF) / 9999.0, ((bits >> 16) & 0xFFFF) / 9999.0
    sec = np.diff(v) / np.diff(t) if n > 1 else np.zeros(0)

    def auto(k: int) -> tuple[float, float]:
        """(left, right) slope of key k from its tangent mode when not user-set."""
        fl = int(flags[k])
        L = sec[k - 1] if k > 0 else None
        R = sec[k] if k < n - 1 else None
        if L is None or R is None:
            e = L if L is not None else (R if R is not None else 0.0)
            e = 0.0 if fl & (FBX_TAN_CLAMP | FBX_TAN_CLAMP_PROGRESSIVE) else e
            return e, e
        if fl & FBX_TAN_TCB:
            T, C, B = data[k, 0], data[k, 1], data[k, 2]
            return ((1 - T) * (1 - C) * (1 + B) / 2 * L + (1 - T) * (1 + C) * (1 - B) / 2 * R,
                    (1 - T) * (1 + C) * (1 + B) / 2 * L + (1 - T) * (1 - C) * (1 - B) / 2 * R)
        c = (v[k + 1] - v[k - 1]) / (t[k + 1] - t[k - 1])
        if fl & FBX_TAN_CLAMP and (L * R <= 0):
            c = 0.0
        if fl & FBX_TAN_CLAMP_PROGRESSIVE:
            c = 0.0 if L * R <= 0 else math.copysign(min(abs(c), 3 * abs(L), 3 * abs(R)), c)
        return c, c

    mode, rs, ls = [], np.zeros(n), np.zeros(n)
    hr, hl = np.full(n, 1 / 3), np.full(n, 1 / 3)
    for i in range(n - 1):
        fl = int(flags[i])
        if fl & FBX_CONSTANT:
            mode.append("N" if fl & FBX_CONSTANT_NEXT else "K")
            continue
        if not fl & FBX_CUBIC:
            mode.append("L")
            continue
        mode.append("B")
        user = fl & (FBX_TAN_USER | FBX_TAN_BREAK)
        rs[i] = data[i, 0] if user else auto(i)[1]
        nxt = int(flags[i + 1])
        ls[i + 1] = data[i, 1] if user or nxt & (FBX_TAN_USER | FBX_TAN_BREAK) else auto(i + 1)[0]
        if fl & FBX_WEIGHTED_RIGHT:
            hr[i] = wr[i]
        if fl & FBX_WEIGHTED_NEXT_LEFT:
            hl[i + 1] = wl[i]
    return _Piecewise(t, v, mode, rs, ls, hr, hl)


def _efn(e) -> Callable:
    if isinstance(e, _Fn):
        return e
    val = float(np.asarray(e[1]).reshape(-1)[0])
    return lambda t: val


def _rot(axis: str, deg) -> np.ndarray:
    a = np.radians(np.asarray(deg, float))
    c, s, o, z = np.cos(a), np.sin(a), np.ones_like(a), np.zeros_like(a)
    m = {"X": [[o, z, z], [z, c, -s], [z, s, c]], "Y": [[c, z, s], [z, o, z], [-s, z, c]],
         "Z": [[c, -s, z], [s, c, z], [z, z, o]]}[axis]
    return np.moveaxis(np.array(m), (0, 1), (-2, -1))


def _euler(rx, ry, rz, order: str) -> np.ndarray:
    """Rotation matrix applying the axes in `order` (first letter first): "XYZ" -> Rz @ Ry @ Rx."""
    rx, ry, rz = np.broadcast_arrays(*(np.asarray(v, float) for v in (rx, ry, rz)))
    parts = {"X": rx, "Y": ry, "Z": rz}
    m = np.broadcast_to(np.eye(3), rx.shape + (3, 3))
    for ax in order:
        m = _rot(ax, parts[ax]) @ m
    return m


def _to_xyz(rx, ry, rz, order: str, pre, post) -> np.ndarray:
    """FBX local rotation PreRotation * R(order) * PostRotation^-1 as XYZ-order Euler degrees."""
    m = _euler(pre[0], pre[1], pre[2], "XYZ") @ _euler(rx, ry, rz, order) @ \
        np.swapaxes(_euler(post[0], post[1], post[2], "XYZ"), -1, -2)
    sy = -m[..., 2, 0]
    b = np.arcsin(np.clip(sy, -1.0, 1.0))
    gimbal = np.abs(sy) > 1 - 1e-9
    a = np.where(gimbal, np.arctan2(-m[..., 1, 2], m[..., 1, 1]), np.arctan2(m[..., 2, 1], m[..., 2, 2]))
    c = np.where(gimbal, 0.0, np.arctan2(m[..., 1, 0], m[..., 0, 0]))
    return np.degrees(np.array([a, b, c]))


def parse_fbx(data: bytes | str, ctx: Ctx | None = None) -> dict[str, Channel]:
    if isinstance(data, str):
        data = data.encode("utf-8")
    if data.startswith(_FBX_MAGIC):
        try:
            if struct.unpack_from('<I', data, 23)[0] < 7000:
                from .tracking_fbx import parse_legacy
                return parse_legacy(data)
            tree = _fbx_binary(data)
        except (struct.error, IndexError) as e:
            raise TrackError(f"fbx: truncated or corrupt binary ({e})") from None
    else:
        text = _text(data)
        if re.match(r'\s*;\s*FBX\s+6(?:\.|\s)', text):
            from .tracking_fbx import parse_legacy
            return parse_legacy(text.encode('utf-8'))
        if not re.match(r"\s*;\s*FBX\s+7", text):
            raise TrackError("fbx: not an FBX 6/7 file (binary magic or '; FBX 6/7.x' ASCII header expected)")
        tree = _fbx_ascii(text)
    top = {n.name: n for n in tree}
    objs, conns = top.get("Objects"), top.get("Connections")
    if objs is None or conns is None:
        raise TrackError("fbx: no Objects/Connections")
    by_id = {o.props[0]: o for o in objs.children if o.props}
    curves = {o.props[0]: c for o in objs.children if o.name == "AnimationCurve" and (c := _fbx_curve(o))}
    stacks = [o.props[0] for o in objs.children if o.name == "AnimationStack" and o.props]
    comp_of: dict = {}            # curvenode -> {component: curve id}
    cn_of: dict = {}              # (model/attribute, property) -> [curvenode, ...]
    attr_of: dict = {}            # model -> node attribute
    layer_of: dict = {}           # curvenode -> layer
    layers: dict = {}             # stack -> [layer, ...] (connection order)
    for c in conns.children:
        if c.name != "C" or len(c.props) < 3:
            continue
        src, dst = c.props[1], c.props[2]
        prop = c.props[3] if len(c.props) > 3 else None
        s, d = by_id.get(src), by_id.get(dst)
        if s is None or d is None:
            continue
        if s.name == "AnimationCurve" and d.name == "AnimationCurveNode" and prop:
            comp_of.setdefault(dst, {})[str(prop).removeprefix("d|")] = src
        elif s.name == "AnimationCurveNode" and prop:
            cn_of.setdefault((dst, str(prop)), []).append(src)
        elif s.name == "AnimationCurveNode" and d.name == "AnimationLayer":
            layer_of[src] = dst
        elif s.name == "AnimationLayer" and d.name == "AnimationStack":
            layers.setdefault(dst, []).append(src)
        elif s.name == "NodeAttribute" and d.name == "Model":
            attr_of[dst] = src
    # the first AnimationStack in file order; curve nodes outside any layer are always used
    sel = layers.get(stacks[0], []) if stacks else None
    rank = {lid: i for i, lid in enumerate(sel or [])}

    def nodes_for(owner, prop) -> list:
        cns = cn_of.get((owner, prop), [])
        if sel is not None:
            cns = [cn for cn in cns if cn not in layer_of or layer_of[cn] in rank]
        return sorted(cns, key=lambda cn: rank.get(layer_of.get(cn), 0))

    out: dict[str, Channel] = {}
    for mid, m in by_id.items():
        if m.name != "Model":
            continue
        series: Series = {}
        animated = False

        def take(owner, prop, comps, kind="t"):
            nonlocal animated
            cns = nodes_for(owner, prop)
            own = _p70(by_id.get(owner))
            for idx, (fc, key, dflt) in enumerate(comps):
                entries = []                      # (entry, weight, blend mode, scale accumulation)
                for j, cn in enumerate(cns):
                    cid = comp_of.get(cn, {}).get(fc)
                    cdef = _p70(by_id.get(cn))
                    lp = _p70(by_id.get(layer_of.get(cn)))
                    e = curves.get(cid)
                    if e is None and cdef.get(f"d|{fc}"):
                        e = (None, np.asarray(float(cdef[f"d|{fc}"][0])))
                    if e is None and j == 0:
                        e = ((None, np.asarray(float(own[prop][idx]))) if prop in own and len(own[prop]) > idx
                             else (None, np.asarray(dflt)) if dflt is not None else None)
                    if e is not None:
                        animated |= isinstance(e, _Fn)
                        entries.append((e, float(lp.get("Weight", [100.0])[0]) / 100.0,
                                        int(lp.get("BlendMode", [0])[0]), int(lp.get("ScaleAccumulationMode", [0])[0])))
                if not entries:
                    if prop in own and len(own[prop]) > idx:
                        entries = [((None, np.asarray(float(own[prop][idx]))), 1.0, 0, 0)]
                    elif dflt is not None:
                        entries = [((None, np.asarray(dflt)), 1.0, 0, 0)]
                    else:
                        continue
                if len(entries) == 1:
                    series[key] = entries[0][0]
                    continue
                fns = [(_efn(e), w, bm, sm) for e, w, bm, sm in entries]

                def blend(t, fns=fns):
                    val = fns[0][0](t)
                    for f, w, bm, sm in fns[1:]:
                        lv = f(t)
                        if bm in (1, 2):                              # override (passthrough)
                            val = val + w * (lv - val)
                        elif kind == "s" and sm == 0:                 # additive scale: multiply
                            val = val * (lv ** w if lv > 0 else lv)
                        else:
                            val = val + w * lv
                    return val

                ts = [e.times for e, *_ in entries if isinstance(e, _Fn)]
                series[key] = _Fn(np.unique(np.concatenate(ts)), blend) if ts else (None, np.asarray(blend(0.0)))

        take(mid, "Lcl Translation", [("X", "x", 0.0), ("Y", "y", 0.0), ("Z", "z", 0.0)])
        take(mid, "Lcl Rotation", [("X", "rx", 0.0), ("Y", "ry", 0.0), ("Z", "rz", 0.0)])
        take(mid, "Lcl Scaling", [("X", "scaleX", 1.0), ("Y", "scaleY", 1.0)], kind="s")
        attr = attr_of.get(mid)
        ap = _p70(by_id.get(attr)) if attr is not None else {}
        if attr is not None:
            take(attr, "FocalLength", [("FocalLength", "focal", None)])
            take(attr, "FieldOfView", [("FieldOfView", "fov", None)])
        if not animated:
            continue
        name = _fbx_name(m.props[1] if len(m.props) > 1 else f"Model{mid}")
        base, k = name, 2
        while name in out:
            name, k = f"{base}#{k}", k + 1
        ch = _channel(name, series)
        mp = _p70(m)
        if mp.get("RotationActive", [0])[0]:
            order = _ROT_ORDERS.get(int(mp.get("RotationOrder", [0])[0]), "XYZ")
            pre = [float(v) for v in mp.get("PreRotation", [0, 0, 0])[:3]]
            post = [float(v) for v in mp.get("PostRotation", [0, 0, 0])[:3]]
            if order != "XYZ" or any(pre) or any(post):
                for i, a in enumerate(("_ex", "_ey", "_ez")):
                    ch.derive(a, lambda x, y, z, i=i: _to_xyz(x, y, z, order, pre, post)[i], "rx", "ry", "rz")
                for a, r in (("_ex", "rx"), ("_ey", "ry"), ("_ez", "rz")):
                    ch.derive(r, lambda v: v, a)
                ch.drop("_ex", "_ey", "_ez")
        mode = ap.get("ApertureMode", [None])[0]
        fw, fh = (ap.get(p, [None])[0] for p in ("FilmWidth", "FilmHeight"))
        if "fov" in ch.data and mode == 1 and fw and fh:
            ch.derive("fov", lambda f: np.degrees(2 * np.arctan(np.tan(np.radians(f) / 2) * fh / fw)), "fov")
        out[name] = ch
    if not out:
        raise TrackError("fbx: no animated models found")
    return out


# ====================================================================== loading
PARSERS: dict[str, Callable[[str | bytes, Ctx], dict[str, Channel]]] = {
    "json": parse_json, "csv": parse_csv, "nuke": parse_nuke, "after-effects": parse_after_effects,
    "mocha": parse_mocha, "fbx": parse_fbx,
}
_CACHE: dict = {}


def sniff_format(raw: bytes, path: str) -> str:
    """Format for a file declared (or defaulted) as json: see the module docstring."""
    head = raw.lstrip(b"\xef\xbb\xbf \t\r\n")
    if head[:1] in (b"{", b"["):
        return "json"
    if raw.startswith(_FBX_MAGIC) or re.match(rb"\s*;\s*FBX", raw):
        return "fbx"
    text = _text(raw[:65536])
    if _AE_HEADER.match(text.lstrip()):
        return "after-effects"
    ext = os.path.splitext(path)[1].lower()
    if ext in (".chan", ".nk"):
        return "nuke"
    if ext == ".txt" and (_looks_chan(text) or _looks_nuke(text)):
        return "nuke"
    if ext in (".csv", ".txt", ".tsv"):
        return "csv"
    return "json"


def find_track(doc, track_id: str):
    el = doc.ids.get(track_id)
    if el is not None and el.tag == "trackData":
        return el
    sec = doc.section("tracking")
    return next((t for t in (sec.iter("trackData") if sec is not None else []) if t.get("id") == track_id), None)


def _ctx(doc, td) -> Ctx:
    fe = doc.ids.get(td.get("footage")) if td.get("footage") else None
    g = (lambda a: _num(fe.get(a)) if fe is not None else None)
    afps = None
    if fe is not None and fe.get("fps"):
        try:
            from fractions import Fraction
            afps = float(Fraction(fe.get("fps")))
        except (ValueError, ZeroDivisionError):
            afps = None
    return Ctx(doc_fps=float(doc.fps), asset_fps=afps, doc_width=float(doc.width), doc_height=float(doc.height),
               asset_width=g("width"), asset_height=g("height"), kind=td.get("kind", "point"), name=td.get("id", ""))


def load_track(rc_or_doc, td_el) -> Track | None:
    if hasattr(rc_or_doc, "cache") and hasattr(rc_or_doc, "doc"):
        doc, cache = rc_or_doc.doc, rc_or_doc.cache
    else:
        doc, cache = rc_or_doc, _CACHE
    tid = td_el.get("id", "?")
    path = doc.resolve_path(td_el.get("src", ""))
    try:
        st = os.stat(path)
    except OSError as e:
        warn_once("trackData", tid, f"cannot read {path}: {e}")
        return None
    ctx = _ctx(doc, td_el)
    key = ("trackData", path, st.st_mtime_ns, st.st_size, tuple(sorted(td_el.attrib.items())), astuple(ctx))
    if key not in cache:
        cache[key] = _load(td_el, path, ctx)
    return cache[key]


def _load(td, path: str, ctx: Ctx) -> Track | None:
    tid = td.get("id", "?")
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        warn_once("trackData", tid, f"cannot read {path}: {e}")
        return None
    sha = td.get("sha256")
    if sha and hashlib.sha256(raw).hexdigest().lower() != sha.lower():
        log.error("trackData %s: %s does not match sha256; track skipped", tid, path)
        return None
    fmt = td.get("format", "json")
    real = sniff_format(raw, path) if fmt == "json" else fmt
    parser = PARSERS.get(real)
    if parser is None:
        warn_once("trackData", tid, f"unknown format {fmt!r}")
        return None
    try:
        chans = parser(raw, ctx)
    except (TrackError, ValueError, KeyError, TypeError, IndexError, AttributeError, struct.error, zlib.error) as e:
        warn_once("trackData", tid, f"cannot parse {path} as {real}: {e}")
        return None
    if not chans:
        warn_once("trackData", tid, f"{path}: no tracks")
        return None
    return Track(tid, ctx.kind, real, _num(td.get("timeOffset")) or 0.0, chans)


def mask_path(rc, td_el, point: str | None, t: float) -> list[tuple] | None:
    """Mask/planar track as a closed path at composition time t, retaining curves and subpaths."""
    tr = load_track(rc, td_el)
    if tr is None:
        return None
    ch, _sub = tr.channel(point)
    if ch is None:
        return None
    v = ch.sample(t - tr.time_offset)
    if "path" in v:
        return v["path"]
    pts = v.get("points", v.get("corners"))
    if pts is None or len(pts) < 2:
        return None
    return [("M", float(pts[0][0]), float(pts[0][1]))] + [("L", float(x), float(y)) for x, y in pts[1:]] + [("Z",)]


# ====================================================================== registry
_FMT_NOTES = {
    "json": (FULL, "scenerender tracking JSON (tracks/keys; t or frame; x/y/corners/points/path/camera keys)"),
    "csv": (FULL, "header-matched columns; interleaved tracks by name column"),
    "nuke": (FULL, "Tracker3/Tracker4/Transform/CornerPin2D/Root and .chan; curves evaluated with Nuke's "
                   "K/L/S/R/C interpolation, s/t slopes, u/v handles, k/l extrapolation. Knob expressions and "
                   "curve(expr) time remaps need Nuke's TCL engine: skipped with a warning"),
    "after-effects": (FULL, "keyframe-data paste: tracker feature center, transform, camera zoom, corner pins"),
    "mocha": (FULL, "AE transform / corner-pin keyframe data, Nuke corner pin"),
    "fbx": (PARTIAL, "FBX 6 Takes via ufbx and FBX 7 ASCII/binary; constant/linear/weighted cubic keys, "
                  "first AnimationStack with layer blending, RotationOrder/Pre/PostRotation -> XYZ Euler; "
                  "Lcl T/R/S + camera FocalLength/FieldOfView. Other legacy tangent modes retain backend "
                  "limits; FBX 7 key-velocity fidelity remains unverified. See docs/TRACKING.md"),
}
for _f, (_lvl, _note) in _FMT_NOTES.items():
    FEATURES.declare(f"trackData:{_f}", _lvl, _note)
for _k, _note in {"point": "x/y (+rotation/scale/anchor) per channel",
                  "planar": "corners TL,TR,BR,BL; :tl/:tr/:br/:bl/:center sub-points",
                  "camera": "x/y/z, rx/ry/rz, focal/fov/zoom (units as exported)",
                  "mask": "polygons and SVG curves/subpaths with fixed topology via mask_path",
                  "face": "landmarks as points (+ pose x/y/rotation/scale); :<index> selects a landmark"}.items():
    FEATURES.declare(f"trackData:kind:{_k}", FULL, _note)
