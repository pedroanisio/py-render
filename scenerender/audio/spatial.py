"""Channel layouts, surround panning (2D VBAP) and ambisonic encoding (ACN / SN3D).

Layouts (channel order = the ffmpeg layout = the WAVE_FORMAT_EXTENSIBLE mask order):
    mono      FC                                    stereo  FL FR
    5.1       FL FR FC LFE BL BR                    7.1     FL FR FC LFE BL BR SL SR
    7.1.4     FL FR FC LFE BL BR SL SR TFL TFR TBL TBR
    ambisonic-1 / ambisonic-3: ACN channel order, SN3D normalisation (AmbiX), 4 / 16 channels.
Speaker azimuths follow ITU-R BS.775 / BS.2051 (degrees, positive = left, counter-clockwise):
FL/FR +-30, FC 0, 5.1 surrounds +-110, 7.1 sides +-90 and backs +-150, heights at elevation 45
(TFL/TFR +-45, TBL/TBR +-135).  channelLayout="auto" picks the ffmpeg default layout for
@channels (1 mono, 2 stereo, 3 3.0, 4 quad, 5 5.0, 6 5.1, 7 6.1, 8 7.1, 12 7.1.4).

2D azimuth mapping of pan (pinned):  |pan| <= 1 sweeps the front stage between FL and FR,
theta = -30 deg x pan (pan -1 = FL, 0 = FC, +1 = FR); 1 < |pan| <= 2 continues round the listener,
theta = -sign(pan) x (30 + 150 x (|pan| - 1)) deg (pan +-2 = directly behind).  Values beyond +-2
are clamped.  In the stereo layout pan keeps its constant-power balance meaning (dsp.pan_gains).

Speaker layouts pan with 2D VBAP (Pulkki 1997) over the horizontal, non-LFE speakers: the source
lands on the pair of adjacent speakers enclosing theta with power-normalised gains (g1^2 + g2^2 = 1),
so a source exactly at a speaker plays from that speaker only.  LFE and height speakers receive no
panned signal (no bass management).  Ambisonic layouts encode at (theta, elevation 0) with real
spherical harmonics, SN3D, no Condon-Shortley phase: first order W = 1, Y = sin(theta),
Z = 0, X = cos(theta).

Source channels: mono sources are one virtual source at theta(pan); stereo sources are two
virtual sources, L at theta(clamp(pan - 1, -1, 1)) and R at theta(clamp(pan + 1, -1, 1)) (at pan 0,
L and R land on FL and FR, the industry default for stereo stems in surround).  A source whose
channel count equals the output layout's passes through discretely (pan ignored).
"""
from __future__ import annotations

import math

import numpy as np

LAYOUTS: dict[str, tuple[str, ...]] = {
    "mono": ("FC",),
    "stereo": ("FL", "FR"),
    "3.0": ("FL", "FR", "FC"),
    "quad": ("FL", "FR", "BL", "BR"),
    "5.0": ("FL", "FR", "FC", "BL", "BR"),
    "5.1": ("FL", "FR", "FC", "LFE", "BL", "BR"),
    "6.1": ("FL", "FR", "FC", "LFE", "BC", "SL", "SR"),
    "7.1": ("FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"),
    "7.1.4": ("FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"),
}
AUTO = {1: "mono", 2: "stereo", 3: "3.0", 4: "quad", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1", 12: "7.1.4"}
AMBI_ORDER = {"ambisonic-1": 1, "ambisonic-3": 3}

# (azimuth deg, elevation deg); LFE has none
_POS = {"FL": (30, 0), "FR": (-30, 0), "FC": (0, 0), "BC": (180, 0), "SL": (90, 0), "SR": (-90, 0),
        "TFL": (45, 45), "TFR": (-45, 45), "TBL": (135, 45), "TBR": (-135, 45)}
_MASK = {"FL": 0x1, "FR": 0x2, "FC": 0x4, "LFE": 0x8, "BL": 0x10, "BR": 0x20, "BC": 0x100, "SL": 0x200,
         "SR": 0x400, "TFL": 0x1000, "TFR": 0x4000, "TBL": 0x8000, "TBR": 0x20000}


class Layout:
    """An output channel layout: channel names/azimuths, or an ambisonic order."""

    def __init__(self, name: str):
        self.name = name
        self.order = AMBI_ORDER.get(name)
        if self.order is not None:
            self.channels: tuple[str, ...] = tuple(f"ACN{i}" for i in range((self.order + 1) ** 2))
        else:
            self.channels = LAYOUTS[name]
        self.n = len(self.channels)

    @classmethod
    def resolve(cls, layout: str, channels: int) -> tuple["Layout", str | None]:
        """Layout for audioMix @channelLayout/@channels, plus a warning when they disagree."""
        if layout in ("", "auto", None):
            name = AUTO.get(int(channels))
            if name is None:
                return cls("stereo"), f"channels={channels} has no standard layout; mixed as stereo"
            return cls(name), None
        lay = cls(layout)
        warn = None
        if channels not in (None, lay.n) and not (layout == "stereo" and channels == 2):
            warn = f"channels={channels} disagrees with channelLayout={layout} ({lay.n} channels); the layout wins"
        return lay, warn

    @property
    def ambisonic(self) -> bool:
        return self.order is not None

    @property
    def ffmpeg(self) -> str:
        """ffmpeg channel layout string (-ch_layout / -channel_layout)."""
        return f"ambisonic {self.order}" if self.order is not None else self.name

    @property
    def wav_mask(self) -> int:
        """WAVE_FORMAT_EXTENSIBLE dwChannelMask (0 = unassigned, as for ambisonics)."""
        return 0 if self.order is not None else sum(_MASK[c] for c in self.channels)

    def azimuth(self, ch: str) -> float | None:
        if ch == "BL":
            return 110.0 if self.name in ("5.1", "5.0", "quad") else 150.0
        if ch == "BR":
            return -110.0 if self.name in ("5.1", "5.0", "quad") else -150.0
        p = _POS.get(ch)
        return None if p is None else float(p[0])

    def elevation(self, ch: str) -> float:
        p = _POS.get(ch)
        return float(p[1]) if p else 0.0

    def loudness_weights(self) -> np.ndarray:
        """BS.1770-4 channel weights: LFE 0, 1.41 for 60 <= |azimuth| <= 120 at elevation < 30, else 1.
        Ambisonics are measured on W (SN3D W carries the mono sum)."""
        if self.order is not None:
            w = np.zeros(self.n)
            w[0] = 1.0
            return w
        out = []
        for c in self.channels:
            az = self.azimuth(c)
            if az is None:
                out.append(0.0)
            elif 60 <= abs(az) <= 120 and self.elevation(c) < 30:
                out.append(1.41)
            else:
                out.append(1.0)
        return np.array(out)

    # ------------------------------------------------------------ panning
    def pan_gains(self, theta_deg) -> np.ndarray:
        """Gains (k, nch) for sources at azimuths theta (deg, array)."""
        th = np.atleast_1d(np.asarray(theta_deg, np.float64))
        if self.order is not None:
            return sh_gains(self.order, np.radians(th), np.zeros_like(th))
        g = np.zeros((len(th), self.n))
        if self.name == "mono":
            g[:, 0] = 1.0
            return g
        ring = [(i, self.azimuth(c)) for i, c in enumerate(self.channels)
                if self.azimuth(c) is not None and self.elevation(c) == 0]
        ring.sort(key=lambda p: p[1] % 360)
        idx = np.array([i for i, _ in ring])
        az = np.array([a % 360 for _, a in ring])
        t = th % 360
        for j in range(len(ring)):
            a1, a2 = az[j], az[(j + 1) % len(ring)]
            span = (a2 - a1) % 360 or 360
            rel = (t - a1) % 360
            sel = rel <= span + 1e-9
            if not sel.any():
                continue
            # 2D VBAP: solve p = g1 l1 + g2 l2, then power-normalise
            r1, r2 = math.radians(a1), math.radians(a1 + span)
            L = np.array([[math.cos(r1), math.sin(r1)], [math.cos(r2), math.sin(r2)]])
            tr = np.radians(t[sel])
            p = np.stack([np.cos(tr), np.sin(tr)], 1)
            if abs(np.linalg.det(L)) < 1e-9:           # speakers 180 deg apart: constant-power blend
                u = rel[sel] / span
                gg = np.stack([np.cos(u * math.pi / 2), np.sin(u * math.pi / 2)], 1)
            else:
                gg = np.clip(p @ np.linalg.inv(L), 0, None)
            gg /= np.maximum(np.linalg.norm(gg, axis=1, keepdims=True), 1e-12)
            rows = np.nonzero(sel)[0]
            fresh = g[rows].sum(axis=1) == 0
            rows, gg = rows[fresh], gg[fresh]
            g[rows, idx[j]] += gg[:, 0]
            g[rows, idx[(j + 1) % len(ring)]] += gg[:, 1]
        return g


def pan_to_azimuth(pan) -> np.ndarray:
    """The pinned 2D azimuth mapping (degrees, positive = left)."""
    p = np.clip(np.asarray(pan, np.float64), -2.0, 2.0)
    a = np.abs(p)
    return -np.sign(p) * np.where(a <= 1.0, 30.0 * a, 30.0 + 150.0 * (a - 1.0))


def _assoc_legendre(l: int, m: int, x: np.ndarray) -> np.ndarray:
    """P_l^m(x) without the Condon-Shortley phase."""
    pmm = np.ones_like(x)
    if m > 0:
        s = np.sqrt(np.maximum(0.0, 1 - x * x))
        f = 1.0
        for _ in range(m):
            pmm = pmm * f * s
            f += 2.0
    if l == m:
        return pmm
    pm1 = x * (2 * m + 1) * pmm
    if l == m + 1:
        return pm1
    for ll in range(m + 2, l + 1):
        pll = ((2 * ll - 1) * x * pm1 - (ll + m - 1) * pmm) / (ll - m)
        pmm, pm1 = pm1, pll
    return pm1


def sh_gains(order: int, az: np.ndarray, el: np.ndarray) -> np.ndarray:
    """Real SN3D spherical harmonics in ACN order: (k, (order+1)^2) for azimuths/elevations (rad)."""
    out = np.zeros((len(az), (order + 1) ** 2))
    sin_el = np.sin(el)
    for l in range(order + 1):
        for m in range(-l, l + 1):
            # N3D -> SN3D: N = sqrt((2 - delta_m0) (l-|m|)! / (l+|m|)!)
            am = abs(m)
            norm = math.sqrt((2.0 - (am == 0)) * math.factorial(l - am) / math.factorial(l + am))
            trig = np.cos(am * az) if m >= 0 else np.sin(am * az)
            out[:, l * l + l + m] = norm * _assoc_legendre(l, am, sin_el) * trig
    return out


def rotate_ambisonic(x: np.ndarray, order: int, angle) -> np.ndarray:
    """Rotate an ACN/SN3D sound field about the vertical axis by `angle` radians (scalar or per sample)."""
    y = x.copy()
    a = np.asarray(angle, np.float64)
    for l in range(1, order + 1):
        for m in range(1, l + 1):
            ip, im = l * l + l + m, l * l + l - m
            c, s = np.cos(m * a), np.sin(m * a)
            y[:, ip] = c * x[:, ip] - s * x[:, im]
            y[:, im] = s * x[:, ip] + c * x[:, im]
    return y


def balance_gains(layout: Layout, pan) -> np.ndarray:
    """Bus pan in speaker layouts: constant-power balance (dsp.pan_gains law) applied to the left
    (azimuth > 0) and right (azimuth < 0) speakers; centre, rear-centre and LFE unchanged.
    Returns (k, nch)."""
    from .dsp import pan_gains
    gl, gr = pan_gains(pan)
    gl, gr = np.atleast_1d(gl), np.atleast_1d(gr)
    g = np.ones((len(gl), layout.n))
    for i, c in enumerate(layout.channels):
        az = layout.azimuth(c)
        if az is None or abs(az) < 1e-9 or abs(abs(az) - 180) < 1e-9:
            continue
        g[:, i] = gl if az > 0 else gr
    return g
