"""Colour management: the "finish" hook (colorManagement + each output's colorSpace/transfer).

Built-in pipeline on the whole frame (straight colour, linear light, working primaries):

    working space -> exposure (x 2^exposure) -> looks in order -> tone mapping -> output primaries
    -> output transfer (PQ: BT.2390 EETF; HLG: BT.2100 OOTF)

Pinned rules (the schema leaves these open):

* Working space. colorManagement/@workingSpace wins over project/@workingColorSpace (the latter is
  used when there is no colorManagement). Its primaries are the compositing primaries; the core
  converts every decoded image/video/colour into them with `to_working()` and tells this module by
  setting `rc.working_primaries` (see `working_primaries()`); until the core does, compositing
  stays in Rec.709/sRGB primaries and `colorManagement:workingSpace` reports PARTIAL. A non-linear
  project (linearLight=false) always works in sRGB-encoded Rec.709.
* Scene 1.0 = SDR reference white = 203 cd/m^2 (ITU-R BT.2408) for PQ and HLG outputs.
* PQ (SMPTE ST 2084) outputs map the signal range onto the target display with the ITU-R BT.2390
  EETF (Hermite knee, black lift), evaluated on max(R,G,B) so hue is preserved. The source range is
  0..10000 cd/m^2 without tone mapping; with a tone mapper the content is already display-referred
  (SDR: peak 203 cd/m^2, aces2 HDR: the view's own peak) and the EETF is an identity. The target
  peak/black come from output/@masteringDisplay L(max,min) (0.0001 cd/m^2 units), else @maxCLL,
  else 1000 cd/m^2 / 0.
* HLG (ARIB STD-B67 / BT.2100) outputs invert the full BT.2100 display OOTF for the nominal peak Lw
  of the target display (same source as above): gamma = 1.2 + 0.42 log10(Lw / 1000) inside
  400..2000 cd/m^2, the BT.2390 extended form 1.2 * 1.111^log2(Lw / 1000) outside; black lift beta
  from the target black level; luminance from the output primaries (BT.2020 weights for rec2020).
* Camera log encodings return the vendor's normalised code value: S-Log3/LogC3/LogC4/V-Log/N-Log
  CV/1023, Canon Log 3 and RED Log3G10 and F-Log2 their published 0..1 signal (as in the ACES/OCIO
  reference implementations). colorRange then applies to that signal like any other.
* Tone mappers take Rec.709-primaries scene-linear input (their published definitions) and return
  display-linear light in the output's display primaries:
    reinhard  x/(1+x) per channel;  filmic  Hable's Uncharted 2 curve, white 11.2;
    aces      Stephen Hill's fitted ACES 1.x RRT+ODT;
    aces2     the ACES 2.0 Output Transform of OCIO's builtin studio config (display and view picked
              per output: sRGB, Rec.1886, Gamma 2.2, Display P3, P3-D65, Rec.2100-PQ / ST2084-P3 at
              the 500/1000/2000/4000-nit view nearest the target peak; HLG uses the PQ view then the
              HLG OOTF above);
    agx       Blender's AgX (Blender 4.x, EaryChow/AgX_LUT_Gen AgXBaseRec2020.py, AgXBasesRGB.py,
              AgXBaseP3.py, luminance_compenstation_*.py; sigmoid.py from Troy Sobotka's
              SB2383-Configuration-Generation), evaluated analytically instead of through the baked
              57^3 LUT: Rec.709 -> FilmLight E-Gamut clamped to the LUT shaper domain (log2 -10..+15
              stops around 0.18), -> Rec.2020, lower guard rail, inset matrix (rotate
              [2.13976149, -1.22827335, -3.05174246], inset [0.32965205, 0.28051336, 0.12475368]),
              log2 -10..+6.5 stops, Jed Smith/Troy Sobotka tunable sigmoid (pivot (10/16.5,
              0.18^(1/2.4)), slope 2.4, powers 1.5/1.5), power 2.4, 40 % HSV hue restoration,
              outset matrix (inset [0.32317438, 0.28325605, 0.0374326]), then the target medium:
              Rec.709 or P3 with that medium's lower guard rail, or Rec.2020; encoded power 1/2.4,
              clipped, and decoded 2.4 as Blender's Rec.1886 display colourspace does.
* A look converts into its @space (primaries + encoding, default ACEScct), applies the ASC CDL
  (slope, offset, power, then saturation with Rec.709 luma), then its LUT file @src (any format
  OpenColorIO reads: .cube .3dl .clf .ctf .csp .spi1d .spi3d .spimtx .cdl .cc .ccc .lut .mga .m3d
  .vf .1dl .look .cub ...; tetrahedral/best interpolation), converts back and blends by @mix. @looks
  lists the looks to apply; when it is absent every <look> is applied in document order.
* @display names the output colour space used when an output declares none; @view is informational
  in the built-in pipeline ("raw" bypasses exposure, looks and tone mapping).

OpenColorIO (colorManagement/@ocioConfig: a config file, an "ocio://..." URI or a builtin config
name such as "studio-config-latest"): @workingSpace, @display, @view and look ids name OCIO
objects (our colour-space enum values are resolved through the config's names and aliases). The
frame is converted from the compositing space into the OCIO working space (directly, or through
the aces_interchange role), exposure is applied there, then looks in order (a <look> without
src/CDL whose id is an OCIO look runs as an OCIO LookTransform; others become ColorSpaceTransform
-> CDLTransform -> FileTransform -> back, mixed by @mix), then the DisplayViewTransform. An output
with its own colorSpace/transfer uses the config display that matches it (sRGB, Rec.1886, P3,
Rec.2100-PQ/HLG ...); a scene-referred output (no matching display, e.g. acescg or a camera log)
leaves OCIO at the working space and takes the built-in output encoding. @toneMapping is ignored
under OCIO (the view forms the image).

Outputs: output.py stores (colorSpace, transfer) in rc.cache["output_color"] and the HDR target in
rc.cache["output_hdr"] = {"peak": cd/m^2, "black": cd/m^2}; the hook hands the core a value it will
sRGB-encode back to exactly the output code value. Default srgb/auto without colorManagement is a
no-op.
"""
from __future__ import annotations

import logging
import math
import os

import numpy as np

from . import threads
from .registry import FEATURES, FULL, PARTIAL, warn_once
from .render import hook_installer
from .values import parse_bool

log = logging.getLogger("scenerender")

for _t in ("reinhard", "filmic", "aces"):
    FEATURES.declare(f"toneMapping:{_t}", FULL)
FEATURES.declare("toneMapping:agx", FULL, "Blender 4.x AgX (EaryChow/AgX_LUT_Gen) evaluated analytically; sRGB/P3/Rec.2020 media")
FEATURES.declare("toneMapping:aces2", FULL, "ACES 2.0 Output Transform from OCIO's builtin studio config")
FEATURES.declare("colorManagement:exposure", FULL)
FEATURES.declare("look:cdl", FULL, "ASC CDL in the look's space")
FEATURES.declare("look:lut", FULL, "every LUT format OpenColorIO reads (FileTransform, best interpolation)")
FEATURES.declare("colorManagement:ocio", FULL, "OpenColorIO 2 CPU processor: working space, looks, display/view per output")


def _core_converts() -> bool:
    try:
        from .compositor import RenderContext
        return "working_primaries" in getattr(RenderContext, "__dataclass_fields__", {}) or \
            hasattr(RenderContext, "working_primaries")
    except ImportError:
        return False


if _core_converts():
    FEATURES.declare("colorManagement:workingSpace", FULL, "compositing in the working primaries (core converts at decode)")
else:
    FEATURES.declare("colorManagement:workingSpace", PARTIAL,
                     "finish/looks run in the working primaries; the core still composites in Rec.709 primaries")
for _s in ("srgb", "linear-srgb", "rec709", "display-p3", "dci-p3", "rec2020", "acescg", "aces2065-1", "acescct", "xyz-d65", "raw"):
    FEATURES.declare(f"colorSpace:{_s}", FULL)
for _s in ("srgb", "linear", "bt1886", "gamma22", "gamma26", "acescc", "acescct", "slog3", "logc3", "logc4", "vlog",
           "clog3", "redlog3g10", "flog2", "nlog"):
    FEATURES.declare(f"transfer:{_s}", FULL)
FEATURES.declare("transfer:pq", FULL, "ST 2084, scene 1.0 = 203 cd/m2, BT.2390 EETF to the target display")
FEATURES.declare("transfer:hlg", FULL, "BT.2100 HLG with the inverse display OOTF for the nominal peak")

# ====================================================================== primaries
_WHITE = {"d65": (0.3127, 0.3290), "d60": (0.32168, 0.33767), "dci": (0.314, 0.351)}
_PRIMS = {
    "srgb": ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06), "d65"),
    "p3d65": ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060), "d65"),
    "dcip3": ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060), "dci"),
    "rec2020": ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046), "d65"),
    "ap1": ((0.713, 0.293), (0.165, 0.830), (0.128, 0.044), "d60"),
    "ap0": ((0.7347, 0.2653), (0.0, 1.0), (0.0001, -0.0770), "d60"),
}
# colorSpaceType -> (primaries, implied encoding)
SPACES = {
    "srgb": ("srgb", "srgb"), "linear-srgb": ("srgb", "linear"), "rec709": ("srgb", "bt1886"),
    "display-p3": ("p3d65", "srgb"), "dci-p3": ("dcip3", "gamma26"), "rec2020": ("rec2020", "bt1886"),
    "acescg": ("ap1", "linear"), "aces2065-1": ("ap0", "linear"), "acescct": ("ap1", "acescct"),
    "xyz-d65": ("xyz", "linear"), "raw": (None, "linear"),
}


def _xyz(xy):
    x, y = xy
    return np.array([x / y, 1.0, (1 - x - y) / y])


def _rgb_to_xyz(prim: str) -> np.ndarray:
    r, g, b, w = _PRIMS[prim]
    P = np.stack([_xyz(r), _xyz(g), _xyz(b)], 1)
    S = np.linalg.solve(P, _xyz(_WHITE[w]))
    return P * S[None, :]


_BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])


def _adapt(src_w: str, dst_w: str) -> np.ndarray:
    if src_w == dst_w:
        return np.eye(3)
    s = _BRADFORD @ _xyz(_WHITE[src_w])
    d = _BRADFORD @ _xyz(_WHITE[dst_w])
    return np.linalg.inv(_BRADFORD) @ np.diag(d / s) @ _BRADFORD


def to_xyz_d65(prim: str) -> np.ndarray:
    if prim == "xyz":
        return np.eye(3)
    return _adapt(_PRIMS[prim][3], "d65") @ _rgb_to_xyz(prim)


def matrix(src: str | None, dst: str | None) -> np.ndarray:
    """Linear RGB in primaries src -> linear RGB in primaries dst (D65 connection space)."""
    if src == dst or src is None or dst is None:
        return np.eye(3)
    return np.linalg.inv(to_xyz_d65(dst)) @ to_xyz_d65(src)


def _mul(x: np.ndarray, M: np.ndarray) -> np.ndarray:
    return (x @ M.T.astype(np.float32)).astype(np.float32) if x.dtype == np.float32 else x @ M.T


def primaries_of(space: str | None) -> str | None:
    """colorSpaceType (or a primaries key) -> primaries key ('srgb', 'ap1', ..., None for raw)."""
    if space in _PRIMS or space == "xyz":
        return space
    return SPACES.get(space or "srgb", ("srgb", "srgb"))[0]


# ====================================================================== transfer functions
_PQ = (2610 / 16384, 2523 / 32, 3424 / 4096, 2413 / 128, 2392 / 128)
REF_WHITE = 203.0                    # cd/m^2 of scene 1.0 (BT.2408)
_HLG = (0.17883277, 0.28466892, 0.55991073)
# Canon Log 3 (Canon "Canon Log Gamma Curves" white paper v1.2; ACES IDT / OCIO CANON_CLOG3 builtin).
_CL3 = (0.36726845, 14.98325, 0.12783901, 1.9754798, 0.12512219, 0.12240537)
# RED Log3G10 v3 (RED "REDWideGamutRGB and Log3G10" white paper, 2017): a, b, c, g.
_R3G10 = (0.224282, 155.975327, 0.01, 15.1927)
# FUJIFILM F-Log2 data sheet v1.0 (2022): a, b, c, d, e, f, cut1, cut2.
_FLOG2 = (5.555556, 0.064829, 0.245281, 0.384316, 8.799461, 0.092864, 0.000889, 0.100686685370811)
# Nikon N-Log specification v1.0.0 (2018): y = 650 (x + 0.0075)^(1/3) / 1023 below 0.328, else (150 ln x + 619) / 1023.


def pq_encode_nits(L: np.ndarray) -> np.ndarray:
    """cd/m^2 -> ST 2084 signal (inverse EOTF)."""
    m1, m2, c1, c2, c3 = _PQ
    y = np.power(np.clip(np.asarray(L) / 10000.0, 0, 1), m1)
    return np.power((c1 + c2 * y) / (1 + c3 * y), m2)


def pq_decode_nits(E: np.ndarray) -> np.ndarray:
    """ST 2084 signal -> cd/m^2 (EOTF)."""
    m1, m2, c1, c2, c3 = _PQ
    p = np.power(np.clip(np.asarray(E), 0, 1), 1 / m2)
    return 10000.0 * np.power(np.maximum(p - c1, 0) / (c2 - c3 * p), 1 / m1)


def hlg_oetf(E: np.ndarray) -> np.ndarray:
    a, b, c = _HLG
    E = np.clip(E, 0, None)
    return np.where(E <= 1 / 12, np.sqrt(3 * E), a * np.log(np.maximum(12 * E - b, 1e-10)) + c)


def hlg_inverse_oetf(V: np.ndarray) -> np.ndarray:
    a, b, c = _HLG
    V = np.clip(V, 0, None)
    return np.where(V <= 0.5, V * V / 3, (np.exp((V - c) / a) + b) / 12)


def hlg_gamma(Lw: float) -> float:
    """BT.2100 system gamma for nominal peak Lw (extended BT.2390 form outside 400..2000 cd/m^2)."""
    if 400 <= Lw <= 2000:
        return 1.2 + 0.42 * math.log10(Lw / 1000.0)
    return 1.2 * 1.111 ** math.log2(Lw / 1000.0)


def encode(x: np.ndarray, tf: str) -> np.ndarray:
    """Linear -> code value (per channel; pq/hlg here are the plain curves, see encode_output)."""
    if tf == "linear":
        return x
    if tf == "srgb":
        x = np.maximum(x, 0)
        return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    if tf in ("bt1886", "gamma22", "gamma26"):
        return np.power(np.maximum(x, 0), 1 / {"bt1886": 2.4, "gamma22": 2.2, "gamma26": 2.6}[tf])
    if tf == "acescct":
        x = np.maximum(x, -1.0)
        return np.where(x <= 0.0078125, 10.5402377416545 * x + 0.0729055341958355,
                        (np.log2(np.maximum(x, 1e-10)) + 9.72) / 17.52)
    if tf == "acescc":
        x = np.maximum(x, 0)
        return np.where(x < 2 ** -15, (np.log2(2 ** -16 + x * 0.5) + 9.72) / 17.52,
                        (np.log2(np.maximum(x, 1e-10)) + 9.72) / 17.52)
    if tf == "slog3":     # Sony "Technical Summary for S-Gamut3.Cine/S-Log3"
        return np.where(x >= 0.01125000, (420.0 + np.log10(np.maximum((x + 0.01) / 0.19, 1e-10)) * 261.5) / 1023.0,
                        (x * (171.2102946929 - 95.0) / 0.01125000 + 95.0) / 1023.0)
    if tf == "logc3":     # ARRI LogC3 EI 800
        return np.where(x > 0.010591, 0.247190 * np.log10(np.maximum(5.555556 * x + 0.052272, 1e-10)) + 0.385537,
                        5.367655 * x + 0.092809)
    if tf == "logc4":     # ARRI "LogC4 Logarithmic Color Space" specification
        a, b, c, s, t = _logc4()
        return np.where(x >= t, (np.log2(np.maximum(a * x + 64, 1e-10)) - 6) / 14 * b + c, (x - t) / s)
    if tf == "vlog":      # Panasonic V-Log/V-Gamut reference manual
        return np.where(x < 0.01, 5.6 * x + 0.125, 0.241514 * np.log10(np.maximum(x + 0.00873, 1e-10)) + 0.598206)
    if tf == "clog3":
        a, k, c0, s, c1, c2 = _CL3
        x = np.asarray(x) / 0.9
        return np.where(x < -0.014, -a * np.log10(np.maximum(-x * k + 1, 1e-10)) + c0,
                        np.where(x <= 0.014, s * x + c1, a * np.log10(np.maximum(x * k + 1, 1e-10)) + c2))
    if tf == "redlog3g10":
        a, b, c, g = _R3G10
        y = np.asarray(x) + c
        return np.where(y < 0, y * g, a * np.log10(np.maximum(y * b + 1, 1e-10)))
    if tf == "flog2":
        a, b, c, d, e, f, cut1, _ = _FLOG2
        return np.where(x >= cut1, c * np.log10(np.maximum(a * x + b, 1e-10)) + d, e * x + f)
    if tf == "nlog":
        x = np.asarray(x)
        return np.where(x < 0.328, 650.0 * np.cbrt(x + 0.0075) / 1023.0,
                        (150.0 * np.log(np.maximum(x, 1e-10)) + 619.0) / 1023.0)
    if tf == "pq":
        return pq_encode_nits(np.asarray(x) * REF_WHITE)
    if tf == "hlg":       # achromatic form of encode_output (Lw = 1000, gamma 1.2)
        return hlg_oetf(np.power(np.clip(np.asarray(x) * REF_WHITE / 1000.0, 0, None), 1 / 1.2))
    warn_once("color", f"transfer:{tf}", "unknown transfer function; sRGB used")
    return encode(x, "srgb")


def _logc4():
    a = (2 ** 18 - 16) / 117.45
    b = (1023 - 95) / 1023
    c = 95 / 1023
    s = (7 * math.log(2) * 2 ** (7 - 14 * c / b)) / (a * b)
    t = (2 ** (14 * (-c / b) + 6) - 64) / a
    return a, b, c, s, t


def decode(v: np.ndarray, tf: str) -> np.ndarray:
    """Code value -> linear (exact inverses of encode)."""
    v = np.asarray(v)
    if tf == "linear":
        return v
    if tf == "srgb":
        v = np.maximum(v, 0)
        return np.where(v <= 0.04045, v / 12.92, np.power((v + 0.055) / 1.055, 2.4))
    if tf in ("bt1886", "gamma22", "gamma26"):
        return np.power(np.maximum(v, 0), {"bt1886": 2.4, "gamma22": 2.2, "gamma26": 2.6}[tf])
    if tf == "acescct":
        return np.where(v <= 0.155251141552511, (v - 0.0729055341958355) / 10.5402377416545,
                        np.power(2.0, v * 17.52 - 9.72))
    if tf == "acescc":
        return np.where(v < (9.72 - 15) / 17.52, (np.power(2.0, v * 17.52 - 9.72) - 2 ** -16) * 2,
                        np.power(2.0, v * 17.52 - 9.72))
    if tf == "slog3":
        return np.where(v >= 171.2102946929 / 1023.0, np.power(10.0, (v * 1023.0 - 420.0) / 261.5) * 0.19 - 0.01,
                        (v * 1023.0 - 95.0) * 0.01125000 / (171.2102946929 - 95.0))
    if tf == "logc3":
        return np.where(v > 5.367655 * 0.010591 + 0.092809, (np.power(10.0, (v - 0.385537) / 0.247190) - 0.052272) / 5.555556,
                        (v - 0.092809) / 5.367655)
    if tf == "logc4":
        a, b, c, s, t = _logc4()
        return np.where(v >= 0, (np.power(2.0, 14 * (v - c) / b + 6) - 64) / a, v * s + t)
    if tf == "vlog":
        return np.where(v < 0.181, (v - 0.125) / 5.6, np.power(10.0, (v - 0.598206) / 0.241514) - 0.00873)
    if tf == "clog3":
        a, k, c0, s, c1, c2 = _CL3
        x = np.where(v < 0.097465473, -(np.power(10.0, (c0 - v) / a) - 1) / k,
                     np.where(v <= 0.15277891, (v - c1) / s, (np.power(10.0, (v - c2) / a) - 1) / k))
        return x * 0.9
    if tf == "redlog3g10":
        a, b, c, g = _R3G10
        return np.where(v < 0, v / g, (np.power(10.0, v / a) - 1) / b) - c
    if tf == "flog2":
        a, b, c, d, e, f, _, cut2 = _FLOG2
        return np.where(v >= cut2, (np.power(10.0, (v - d) / c) - b) / a, (v - f) / e)
    if tf == "nlog":
        return np.where(v < 452.0 / 1023.0, np.power(v * 1023.0 / 650.0, 3) - 0.0075, np.exp((v * 1023.0 - 619.0) / 150.0))
    if tf == "pq":
        return pq_decode_nits(v) / REF_WHITE
    if tf == "hlg":
        return np.power(hlg_inverse_oetf(v), 1.2) * 1000.0 / REF_WHITE
    warn_once("color", f"transfer:{tf}", "unknown transfer function; sRGB used")
    return decode(v, "srgb")


# ---------------------------------------------------------------- HDR output stages
def eetf_bt2390(E: np.ndarray, src: tuple[float, float], dst: tuple[float, float]) -> np.ndarray:
    """ITU-R BT.2390-10 §5.4 EETF in the PQ domain: source (black, white) -> target (min, max) in cd/m^2."""
    lb, lw = (float(pq_encode_nits(v)) for v in src)
    lmin, lmax = (float(pq_encode_nits(v)) for v in dst)
    span = max(lw - lb, 1e-9)
    e1 = (E - lb) / span
    min_lum, max_lum = (lmin - lb) / span, (lmax - lb) / span
    ks = 1.5 * max_lum - 0.5
    if max_lum >= 1.0 and min_lum <= 0:
        return E
    t = np.clip((e1 - ks) / max(1 - ks, 1e-9), 0, 1)
    t2, t3 = t * t, t * t * t
    p = (2 * t3 - 3 * t2 + 1) * ks + (t3 - 2 * t2 + t) * (1 - ks) + (-2 * t3 + 3 * t2) * max_lum
    e2 = np.where(e1 < ks, e1, p)
    e3 = e2 + max(min_lum, 0.0) * np.power(np.clip(1 - e2, 0, 1), 4)
    return e3 * span + lb


def encode_output(rgb: np.ndarray, tf: str, prims: str | None, hdr: dict | None = None,
                  src_peak: float = 10000.0) -> np.ndarray:
    """Display-linear RGB (1.0 = 203 cd/m^2 for PQ/HLG) in the output primaries -> output code values."""
    hdr = hdr or {}
    peak = float(hdr.get("peak") or 1000.0)
    black = float(hdr.get("black") or 0.0)
    if tf == "pq":
        L = np.clip(rgb, 0, None) * REF_WHITE
        if src_peak > peak or black > 0:
            m = np.max(L, -1, keepdims=True)
            E = pq_encode_nits(m)
            m2 = pq_decode_nits(eetf_bt2390(E, (0.0, max(src_peak, peak)), (black, peak)))
            L = L * np.where(m > 1e-9, m2 / np.maximum(m, 1e-9), 0) + np.where(m > 1e-9, 0, m2)
        return pq_encode_nits(L)
    if tf == "hlg":
        Lw = peak
        g = hlg_gamma(Lw)
        wts = to_xyz_d65(prims)[1] if prims else np.array([0.2627, 0.6780, 0.0593])
        Fd = np.clip(rgb, 0, None) * REF_WHITE / Lw                       # display light relative to Lw
        Yd = np.maximum(Fd @ wts.astype(Fd.dtype), 0)[..., None]
        E = np.where(Yd > 0, np.power(np.maximum(Yd, 1e-12), (1 - g) / g) * Fd, 0)   # inverse OOTF
        V = hlg_oetf(np.clip(E, 0, 1))
        beta = math.sqrt(3 * (black / Lw) ** (1 / g)) if black > 0 else 0.0
        return (V - beta) / (1 - beta) if beta else V
    return encode(rgb, tf)


# ====================================================================== working space
def working_space_name(doc) -> str:
    """colorManagement/@workingSpace, else project/@workingColorSpace (pinned precedence)."""
    cm = doc.section("colorManagement")
    if cm is not None and cm.get("workingSpace"):
        return cm.get("workingSpace")
    ws = doc.project.get("workingColorSpace")
    if ws:
        return "linear-srgb" if ws == "srgb" else ws
    return "linear-srgb"


def working_primaries_for(doc) -> str:
    """Primaries the compositor should work in for this document (the core stores it in rc.working_primaries)."""
    if not parse_bool(doc.project.get("linearLight", "true"), True):
        return "srgb"
    p = primaries_of(working_space_name(doc))
    return p if p not in (None, "xyz") else "srgb"


def working_primaries(rc) -> str:
    """Primaries the frame actually arrives in: rc.working_primaries once the core converts, else Rec.709."""
    return getattr(rc, "working_primaries", None) or "srgb"


def to_working(rgb: np.ndarray, color_space: str | None = "srgb", transfer: str | None = "auto",
               working: str = "srgb") -> np.ndarray:
    """Straight RGB encoded in (color_space, transfer) -> linear light in the working primaries.

    `working` is a primaries key or colorSpaceType. transfer "auto" uses the space's own encoding."""
    space = color_space or "srgb"
    tf = transfer if transfer not in (None, "auto") else SPACES.get(space, (None, "srgb"))[1]
    x = decode(np.asarray(rgb, np.float32), tf).astype(np.float32)
    src, dst = primaries_of(space), primaries_of(working)
    return x if src is None or src == dst else _mul(x, matrix(src, dst))


def from_working(rgb: np.ndarray, color_space: str | None = "srgb", transfer: str | None = "auto",
                 working: str = "srgb") -> np.ndarray:
    """Inverse of to_working: linear working-primaries RGB -> (color_space, transfer) code values."""
    space = color_space or "srgb"
    tf = transfer if transfer not in (None, "auto") else SPACES.get(space, (None, "srgb"))[1]
    src, dst = primaries_of(working), primaries_of(space)
    x = np.asarray(rgb, np.float32)
    if dst is not None and src != dst:
        x = _mul(x, matrix(src, dst))
    return encode(x, tf).astype(np.float32)


# ====================================================================== AgX (Blender 4.x)
_AGX_E_TO_XYZ = np.array([[0.7053968501, 0.1640413283, 0.08101774865], [0.2801307241, 0.8202066415, -0.1003373656],
                          [-0.1037815116, -0.07290725703, 1.265746519]])
_AGX_XYZ_TO_2020 = np.array([[1.7166634277958805, -0.3556733197301399, -0.2533680878902478],
                             [-0.6666738361988869, 1.6164557398246981, 0.0157682970961337],
                             [0.0176424817849772, -0.0427769763827532, 0.9422432810184308]])
_AGX_2020_TO_XYZ = np.array([[0.6369535067850740, 0.1446191846692331, 0.1688558539228734],
                             [0.2626983389565560, 0.6780087657728165, 0.0592928952706273],
                             [0.0000000000000000, 0.0280731358475570, 1.0608272349505707]])
_AGX_XYZ_TO_709 = np.array([[3.24100323297635872776822907326277, -1.53739896948878551619088739244035, -0.49861588199636291962590917137277],
                            [-0.96922425220251640087809619217296, 1.87592998369517593992839010752505, 0.04155422634008471699518239006466],
                            [0.05563941985197547179797794569822, -0.20401120612390993835916219723003, 1.05714897718753331190555400098674]])
_AGX_XYZ_TO_P3 = np.array([[2.4935091239346101, -0.9313881794047790, -0.4027127567416516],
                           [-0.8294732139295544, 1.7626305796003032, 0.0236242371055886],
                           [0.0358512644339181, -0.0761839369220759, 0.9570295866943110]])
# Blender config.ocio "AgX Log" inset (Rec.2020); outset = inverse of the Rec.2020 inset by [0.32317438, 0.28325605, 0.0374326].
_AGX_INSET = np.array([[0.856627153315983, 0.0951212405381588, 0.0482516061458583],
                       [0.137318972929847, 0.761241990602591, 0.101439036467562],
                       [0.11189821299995, 0.0767994186031903, 0.811302368396859]])
_AGX_LUM2020 = np.array([0.2589235355689848, 0.6104985346066525, 0.13057792982436284])   # CIE 2015 CMFs, D65
_AGX_LOG = (-10.0, 6.5)          # formation range, stops around 0.18
_AGX_SHAPER = (-10.0, 15.0)      # Blender's LUT shaper domain (AllocationTransform lg2 -12.47393..12.5260688)


def _inset_primaries(scales) -> np.ndarray:
    """working_space.create_workingspace with zero rotation: each Rec.2020 primary moved towards D65."""
    r, g, b, _ = _PRIMS["rec2020"]
    w = np.array(_WHITE["d65"])
    prims = [w + (1 - s) * (np.array(p) - w) for p, s in zip((r, g, b), scales)]
    P = np.stack([_xyz(p) for p in prims], 1)
    return P * np.linalg.solve(P, _xyz(w))[None, :]


_AGX_OUTSET = np.linalg.inv(np.linalg.inv(_rgb_to_xyz("rec2020")) @ _inset_primaries((0.32317438, 0.28325605, 0.0374326)))


def _agx_sigmoid(x: np.ndarray) -> np.ndarray:
    """sigmoid.calculate_sigmoid with lengths 0, limits (0,0)-(1,1): pivots (10/16.5, 0.18^(1/2.4)), slope 2.4, powers 1.5."""
    px, py = -_AGX_LOG[0] / (_AGX_LOG[1] - _AGX_LOG[0]), 0.18 ** (1 / 2.4)
    slope, p = 2.4, 1.5

    def scale(lx, ly, tx, ty):
        a = (slope * (lx - tx)) ** -p
        b = ((slope * (lx - tx)) / (ly - ty)) ** p - 1.0
        return (a * b) ** (-1.0 / p)

    s_toe = -scale(1.0, 1.0, 1.0 - px, 1.0 - py)
    s_sh = scale(1.0, 1.0, px, py)

    def curve(s):
        u = slope * (x - px) / s
        with np.errstate(invalid="ignore", over="ignore"):
            e = u / np.power(1.0 + np.power(np.abs(u), p) * np.sign(u) ** 2, 1.0 / p)
        return s * e + py
    return np.where(x < px, curve(s_toe), curve(s_sh))


def _max3(c: np.ndarray) -> np.ndarray:
    """c.max(-1) for 3 channels: pairwise maxima avoid NumPy's slow reduction over a length-3 axis."""
    return np.maximum(np.maximum(c[..., 0], c[..., 1]), c[..., 2])


def _min3(c: np.ndarray) -> np.ndarray:
    return np.minimum(np.minimum(c[..., 0], c[..., 1]), c[..., 2])


def _rgb_to_hsv(c: np.ndarray):
    mx, mn = _max3(c), _min3(c)
    d = mx - mn
    with np.errstate(invalid="ignore", divide="ignore"):
        s = np.where(mx != 0, d / np.where(mx != 0, mx, 1), 0.0)
        dr, dg, db = (((mx - c[..., i]) / 6 + d / 2) / np.where(d != 0, d, 1) for i in range(3))
    h = np.where(mx == c[..., 0], db - dg, 0.0)
    h = np.where(mx == c[..., 1], 1 / 3 + dr - db, h)
    h = np.where(mx == c[..., 2], 2 / 3 + dg - dr, h)
    h = np.where(h < 0, h + 1, h)
    h = np.where(h > 1, h - 1, h)
    return np.where(d == 0, 0.0, h), s, mx


def _hsv_to_rgb(h, s, v) -> np.ndarray:
    h6 = (h % 1.0) * 6.0
    i = np.floor(h6)
    f = h6 - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    i = i.astype(np.int64) % 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], -1)


def _guard_2020(rgb: np.ndarray) -> np.ndarray:
    """luminance_compenstation_bt2020.compensate_low_side."""
    L = _AGX_LUM2020
    Y = rgb @ L
    inv = _max3(rgb)[..., None] - rgb
    y_comp = _max3(inv) - inv @ L + Y
    off = rgb + np.maximum(-_min3(rgb)[..., None], 0.0)
    inv2 = _max3(off)[..., None] - off
    y_new = _max3(inv2) - inv2 @ L + off @ L
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(y_new > y_comp, y_comp / np.where(y_new != 0, y_new, 1), 1.0)
    return ratio[..., None] * off


def _guard_display(rgb: np.ndarray, to_2020: np.ndarray) -> np.ndarray:
    """luminance_compenstation_srgb/_p3.compensate_low_side (luminance measured in Rec.2020)."""
    L = _AGX_LUM2020 @ to_2020

    def lerp_y(y, yc):
        with np.errstate(invalid="ignore"):
            t = np.clip(np.power(y, 0.08), 0, 1)
        return (1 - t) * yc + t * y
    Y = rgb @ L
    inv = _max3(rgb)[..., None] - rgb
    Y = lerp_y(Y, _max3(inv) - inv @ L + Y)
    off = rgb + np.maximum(-_min3(rgb)[..., None], 0.0)
    inv2 = _max3(off)[..., None] - off
    yn = off @ L
    yn = lerp_y(yn, _max3(inv2) - inv2 @ L + yn)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(yn > Y, Y / np.clip(yn, 1e-100, None), 1.0)
    return ratio[..., None] * off


def agx(x709: np.ndarray, target: str = "srgb") -> np.ndarray:
    """Blender AgX Base: scene-linear Rec.709 -> display-linear [0, 1] in `target` primaries (srgb|p3d65|rec2020)."""
    x = np.asarray(x709, np.float64)
    shape = x.shape
    x = x.reshape(-1, 3)
    to_709 = np.linalg.inv(_AGX_XYZ_TO_709)
    eg = x @ (np.linalg.inv(_AGX_E_TO_XYZ) @ to_709).T
    lo, hi = (0.18 * 2.0 ** v for v in _AGX_SHAPER)
    eg = np.clip(eg, lo, hi)
    c = eg @ (_AGX_XYZ_TO_2020 @ _AGX_E_TO_XYZ).T
    c = _guard_2020(c)
    c = c @ _AGX_INSET.T
    h0, _, _ = _rgb_to_hsv(c)
    with np.errstate(divide="ignore"):
        lg = (np.log2(c / 0.18) - _AGX_LOG[0]) / (_AGX_LOG[1] - _AGX_LOG[0])
    y = _agx_sigmoid(np.where(np.isfinite(lg), lg, -1e9))
    y = np.where(y >= 0, np.power(np.maximum(y, 0), 2.4), 0.0)
    h1, s1, v1 = _rgb_to_hsv(y)
    d = h1 - h0
    h1 = np.where(d > 0.5, h1 - 1.0, np.where(d < -0.5, h1 + 1.0, h1))
    h = (h0 + 0.4 * (h1 - h0)) % 1.0
    y = _hsv_to_rgb(h, s1, v1) @ _AGX_OUTSET.T
    if target in ("srgb", "p3d65"):
        M = _AGX_XYZ_TO_709 if target == "srgb" else _AGX_XYZ_TO_P3
        y = y @ (M @ _AGX_2020_TO_XYZ).T
        y = _guard_display(y, _AGX_XYZ_TO_2020 @ np.linalg.inv(M))
    y = np.clip(np.power(np.maximum(y, 0), 1 / 2.4), 0, 1)
    return np.power(y, 2.4).reshape(shape)


# ====================================================================== OpenColorIO
_OCIO_CFG: dict = {}
_OCIO_PROC: dict = {}
STUDIO = "ocio://studio-config-latest"
# our colorSpaceType -> candidate OCIO names/aliases (the first that resolves wins)
_OCIO_NAMES = {
    "linear-srgb": ["lin_rec709_srgb", "Linear Rec.709 (sRGB)", "lin_srgb", "Utility - Linear - sRGB", "Linear"],
    "srgb": ["srgb_tx", "sRGB Encoded Rec.709 (sRGB)", "srgb_texture", "sRGB - Texture", "sRGB"],
    "rec709": ["g24_rec709_tx", "Gamma 2.4 Encoded Rec.709", "rec709_display", "Rec.709"],
    "display-p3": ["srgb_encoded_p3d65_tx", "sRGB Encoded P3-D65", "srgb_displayp3", "Display P3"],
    "dci-p3": ["g26_dcip3", "P3-DCI", "DCI-P3"],
    "rec2020": ["g24_rec2020", "Rec.2020", "rec2020"],
    "acescg": ["ACEScg", "acescg", "lin_ap1"], "aces2065-1": ["ACES2065-1", "aces2065_1", "lin_ap0"],
    "acescct": ["ACEScct", "acescct", "acescct_ap1"], "xyz-d65": ["lin_ciexyz_d65", "CIE XYZ-D65 - Scene-referred", "XYZ"],
    "raw": ["Raw", "raw"],
}
_LIN_OF = {"srgb": "linear-srgb", "ap1": "acescg", "ap0": "aces2065-1", "rec2020": "lin_rec2020", "p3d65": "lin_p3d65"}
# (primaries, transfer) -> candidate display names, then substrings
_DISPLAYS = {
    "pq": (["Rec.2100-PQ - Display", "ST2084-P3-D65 - Display"], ["2100-pq", "pq", "2084"]),
    "hlg": (["Rec.2100-HLG - Display"], ["hlg"]),
    "p3-srgb": (["Display P3 - Display", "Display P3"], ["display p3"]),
    "p3": (["P3-D65 - Display", "P3-DCI - Display", "DCI-P3"], ["p3"]),
    "bt1886": (["Rec.1886 Rec.709 - Display", "Rec.1886", "Rec.709"], ["1886", "rec.709"]),
    "gamma22": (["Gamma 2.2 Rec.709 - Display"], ["2.2"]),
    "srgb": (["sRGB - Display", "sRGB"], ["srgb"]),
}


def ocio():
    import PyOpenColorIO as O
    return O


def ocio_config(uri: str, doc=None):
    """A config from a file path (resolved against the document), an ocio:// URI or a builtin name."""
    O = ocio()
    key = uri
    if not uri.startswith("ocio://"):
        names = {n for n in O.BuiltinConfigRegistry()}
        if uri in names or uri.endswith("-latest"):
            key = "ocio://" + uri
        elif doc is not None:
            key = doc.resolve_path(uri)
    cfg = _OCIO_CFG.get(key)
    if cfg is None:
        cfg = O.Config.CreateFromFile(key)
        _OCIO_CFG[key] = cfg
    return cfg


def ocio_space(cfg, name: str | None) -> str | None:
    """Resolve our enum value or an OCIO name/alias/role to a colour space name of cfg."""
    if not name:
        return None
    for cand in [name] + _OCIO_NAMES.get(name, []):
        cs = cfg.getColorSpace(cand)
        if cs is not None:
            return cs.getName()
        if cfg.hasRole(cand):
            return cfg.getRoleColorSpace(cand)
    return None


def _cpu(cfg, transform, key):
    p = _OCIO_PROC.get(key)
    if p is None:
        p = cfg.getProcessor(transform).getDefaultCPUProcessor()
        _OCIO_PROC[key] = p
    return p


def ocio_apply(cpu, rgb: np.ndarray) -> np.ndarray:
    a = np.ascontiguousarray(rgb, np.float32).copy()
    flat = a.reshape(-1, 3)
    n = min(threads(), len(flat) // 65536)
    if n > 1:
        # CPU processors are thread-safe and release the GIL: apply to row bands in parallel.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(n) as pool:
            list(pool.map(cpu.applyRGB, np.array_split(flat, n)))
    else:
        cpu.applyRGB(flat)
    return flat.reshape(rgb.shape)


def _m44(M: np.ndarray) -> list[float]:
    out = np.eye(4)
    out[:3, :3] = M
    return [float(v) for v in out.ravel()]


def _match_display(cfg, space: str, tf: str) -> str | None:
    prim = primaries_of(space)
    if tf in ("pq", "hlg"):
        key = tf
    elif prim in ("p3d65", "dcip3"):
        key = "p3-srgb" if tf == "srgb" else "p3"
    elif tf in ("bt1886", "gamma22", "srgb") and prim in ("srgb", "rec2020"):
        key = tf
    else:
        return None
    displays = list(cfg.getDisplays())
    names, subs = _DISPLAYS[key]
    for n in names:
        if n in displays:
            if key == "pq" and prim in ("p3d65", "dcip3") and "ST2084-P3-D65 - Display" in displays:
                return "ST2084-P3-D65 - Display"
            return n
    for s in subs:
        for d in displays:
            if s in d.lower():
                return d
    return None


# ---------------------------------------------------------------- ACES 2.0 through the studio config
def _aces2_target(space: str, tf: str, peak: float) -> tuple[str, str, str, str]:
    """-> (display, view, display primaries, display encoding) in the studio config."""
    prim = primaries_of(space)
    if tf in ("pq", "hlg"):
        nits = min((500, 1000, 2000, 4000), key=lambda n: (abs(math.log(n / max(peak, 1))), n))
        if prim in ("p3d65", "dcip3") and tf == "pq":
            return "ST2084-P3-D65 - Display", f"ACES 2.0 - HDR {nits} nits (P3 D65)", "p3d65", "pq"
        return "Rec.2100-PQ - Display", f"ACES 2.0 - HDR {nits} nits (Rec.2020)", "rec2020", "pq"
    if prim == "p3d65" and tf == "srgb":
        return "Display P3 - Display", "ACES 2.0 - SDR 100 nits (P3 D65)", "p3d65", "srgb"
    if prim in ("p3d65", "dcip3"):
        return "P3-D65 - Display", "ACES 2.0 - SDR 100 nits (P3 D65)", "p3d65", "gamma26"
    if tf == "bt1886":
        return "Rec.1886 Rec.709 - Display", "ACES 2.0 - SDR 100 nits (Rec.709)", "srgb", "bt1886"
    if tf == "gamma22":
        return "Gamma 2.2 Rec.709 - Display", "ACES 2.0 - SDR 100 nits (Rec.709)", "srgb", "gamma22"
    return "sRGB - Display", "ACES 2.0 - SDR 100 nits (Rec.709)", "srgb", "srgb"


def aces2(x709: np.ndarray, space: str = "srgb", tf: str = "srgb", peak: float = 1000.0) -> tuple[np.ndarray, str]:
    """ACES 2.0 Output Transform: scene-linear Rec.709 -> (display-linear, its primaries); PQ -> 1.0 = 203 cd/m^2."""
    O = ocio()
    cfg = ocio_config(STUDIO)
    display, view, prim, enc = _aces2_target(space, tf, peak)
    t = O.DisplayViewTransform(src="Linear Rec.709 (sRGB)", display=display, view=view)
    code = ocio_apply(_cpu(cfg, t, ("aces2", display, view)), x709)
    if enc == "pq":
        return (pq_decode_nits(code) / REF_WHITE).astype(np.float32), prim
    return decode(np.clip(code, 0, 1), enc).astype(np.float32), prim


# ====================================================================== tone mapping
_ACES_IN = np.array([[0.59719, 0.35458, 0.04823], [0.07600, 0.90834, 0.01566], [0.02840, 0.13383, 0.83777]])
_ACES_OUT = np.array([[1.60475, -0.53108, -0.07367], [-0.10208, 1.10813, -0.00605], [-0.00327, -0.07276, 1.07602]])


def tone_map(x: np.ndarray, mode: str, space: str = "srgb", tf: str = "srgb", peak: float = 1000.0) -> np.ndarray:
    """Scene-linear Rec.709 -> display-linear Rec.709 (see tone_map_to for other display primaries)."""
    y, prim = tone_map_to(x, mode, space, tf, peak)
    return np.clip(_mul(y, matrix(prim, "srgb")), 0, None) if prim != "srgb" else y


def tone_map_to(x: np.ndarray, mode: str, space: str = "srgb", tf: str = "srgb",
                peak: float = 1000.0) -> tuple[np.ndarray, str]:
    """Scene-linear Rec.709 -> (display-linear, primaries), rendering for the output (space, tf)."""
    x = np.maximum(x, 0)
    if mode == "reinhard":
        return x / (1 + x), "srgb"
    if mode == "filmic":
        def h(v):
            A, B, C, D, E, F = 0.15, 0.50, 0.10, 0.20, 0.02, 0.30
            return ((v * (A * v + C * B) + D * E) / (v * (A * v + B) + D * F)) - E / F
        return np.clip(h(2.0 * x) / h(11.2), 0, 1), "srgb"
    if mode == "aces":
        v = x @ _ACES_IN.T
        a = v * (v + 0.0245786) - 0.000090537
        b = v * (0.983729 * v + 0.4329510) + 0.238081
        return np.clip((a / b) @ _ACES_OUT.T, 0, 1), "srgb"
    if mode == "aces2":
        try:
            return aces2(x, space, tf, peak)
        except Exception as e:  # noqa: BLE001 — OCIO missing or broken: the ACES 1.x fit
            warn_once("color", "aces2", f"OpenColorIO unavailable ({e}); ACES 1.x fit used")
            return tone_map_to(x, "aces", space, tf, peak)
    if mode == "agx":
        prim = primaries_of(space)
        target = "rec2020" if prim == "rec2020" else "p3d65" if prim in ("p3d65", "dcip3") else "srgb"
        return agx(x, target).astype(np.float32), target
    if mode != "none":
        warn_once("color", f"toneMapping:{mode}", "unknown tone mapper; skipped")
    return x, "srgb"


# ====================================================================== looks
def _vec(s: str | None, default: float) -> np.ndarray:
    if not s:
        return np.full(3, default)
    v = [float(p) for p in s.replace(",", " ").split()]
    return np.array((v * 3)[:3] if len(v) == 1 else v[:3], np.float64)


def cdl(x: np.ndarray, slope, offset, power, sat: float) -> np.ndarray:
    y = x * slope + offset
    y = np.power(np.maximum(y, 0), power)
    if abs(sat - 1.0) > 1e-9:
        luma = y @ np.array([0.2126, 0.7152, 0.0722])
        y = luma[..., None] + sat * (y - luma[..., None])
    return y


def read_cube(path: str):
    """-> (table, lo, hi, size, dim); reuses the effects module's reader when present."""
    try:
        from .effects.color import _read_cube
        return _read_cube(path)
    except ImportError:
        pass
    sizes, rows = {}, []
    lo, hi = np.zeros(3), np.ones(3)
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            parts = line.split("#", 1)[0].split()
            if not parts or parts[0] == "TITLE":
                continue
            if parts[0] in ("LUT_1D_SIZE", "LUT_3D_SIZE"):
                sizes[parts[0]] = int(parts[1])
            elif parts[0] == "DOMAIN_MIN":
                lo = np.array(parts[1:4], np.float32)
            elif parts[0] == "DOMAIN_MAX":
                hi = np.array(parts[1:4], np.float32)
            elif parts[0][0].isalpha():
                continue
            else:
                rows.append([float(p) for p in parts[:3]])
    tag, size = next(iter(sizes.items()))
    table = np.asarray(rows, np.float32)
    dim = 1 if tag == "LUT_1D_SIZE" else 3
    if dim == 3:
        table = table.reshape(size, size, size, 3)
    return table, lo, hi, size, dim


def apply_cube(x: np.ndarray, data) -> np.ndarray:
    table, lo, hi, size, dim = data
    pos = np.clip((x - lo) / (hi - lo), 0, 1) * (size - 1)
    i = np.minimum(np.floor(pos).astype(np.int64), size - 2)
    f = (pos - i).astype(np.float32)
    if dim == 1:
        out = np.empty_like(x, dtype=np.float32)
        for c in range(3):
            out[..., c] = table[i[..., c], c] * (1 - f[..., c]) + table[i[..., c] + 1, c] * f[..., c]
        return out
    out = np.zeros(x.shape, np.float32)
    ir, ig, ib = i[..., 0], i[..., 1], i[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    for dr in (0, 1):
        wr = fr if dr else 1 - fr
        for dg in (0, 1):
            wg = fg if dg else 1 - fg
            for db in (0, 1):
                wb = fb if db else 1 - fb
                out += table[ib + db, ig + dg, ir + dr] * (wr * wg * wb)   # table[b][g][r] (red fastest)
    return out


def lut_processor(path: str):
    """OCIO CPU processor applying one LUT file (any format OCIO reads), best interpolation."""
    O = ocio()
    key = ("lut", path, os.path.getmtime(path))
    p = _OCIO_PROC.get(key)
    if p is None:
        t = O.FileTransform(src=path, interpolation=O.INTERP_BEST, direction=O.TRANSFORM_DIR_FORWARD)
        p = O.Config.CreateRaw().getProcessor(t).getDefaultCPUProcessor()
        _OCIO_PROC[key] = p
    return p


def load_lut(path: str):
    """-> callable(rgb) -> rgb; OCIO for every format, the built-in .cube reader as fallback."""
    try:
        cpu = lut_processor(path)
        return lambda x: ocio_apply(cpu, x)
    except Exception as e:  # noqa: BLE001
        if path.lower().endswith(".cube"):
            data = read_cube(path)
            return lambda x: apply_cube(np.asarray(x, np.float32), data)
        raise ValueError(str(e)) from e


# ====================================================================== the hook
def _output_target(rc):
    oc = rc.cache.get("output_color")
    cm = rc.doc.section("colorManagement")
    space, tf = (oc or (None, None))
    if not space:
        disp = cm.get("display") if cm is not None else None
        space = disp if disp in SPACES else "srgb"
    if not tf or tf == "auto":
        tf = SPACES.get(space, ("srgb", "srgb"))[1]
    return space, tf


def _looks(doc, cm) -> list:
    looks = {lk.get("id"): lk for lk in cm if isinstance(lk.tag, str) and lk.tag == "look"}
    ids = cm.get("looks").split() if cm.get("looks") else list(looks)
    out = []
    for lid in ids:
        lk = looks.get(lid)
        if lk is None:
            lk = doc.ids.get(lid)
        if lk is None:
            warn_once("look", lid, "look not found")
            continue
        out.append(lk)
    return out


def _look_entry(doc, lk) -> dict:
    lid = lk.get("id")
    e = {"id": lid, "space": lk.get("space", "acescct"), "mix": float(lk.get("mix", 1) or 1), "cdl": None,
         "lut": None, "src": None}
    if any(lk.get(k) for k in ("slope", "offset", "power")) or float(lk.get("saturation", 1) or 1) != 1:
        e["cdl"] = (_vec(lk.get("slope"), 1.0), _vec(lk.get("offset"), 0.0), _vec(lk.get("power"), 1.0),
                    float(lk.get("saturation", 1) or 1))
    if lk.get("src"):
        path = doc.resolve_path(lk.get("src"))
        if not os.path.exists(path):
            warn_once("look", lid, f"{path} not found; LUT skipped")
        else:
            e["src"] = path
            try:
                e["lut"] = load_lut(path)
            except (OSError, ValueError, StopIteration, IndexError) as err:
                warn_once("look", lid, f"cannot read {path}: {err}; LUT skipped")
                e["src"] = None
    return e


def _hdr_key(rc):
    h = rc.cache.get("output_hdr") or {}
    return (h.get("peak"), h.get("black"))


def _plan(rc):
    """Precompute everything per (renderer, output colour); None = nothing to do."""
    key = ("color_plan", rc.cache.get("output_color"), _hdr_key(rc), working_primaries(rc))
    if key in rc.cache:
        return rc.cache[key]
    doc = rc.doc
    cm = doc.section("colorManagement")
    space, tf = _output_target(rc)
    wp = working_primaries(rc)
    hdr = dict(rc.cache.get("output_hdr") or {})
    plan = {"exposure": 0.0, "looks": [], "tone": "none", "space": space, "tf": tf, "wp": wp, "hdr": hdr,
            "ocio": None, "raw_view": False}
    if cm is not None:
        plan["exposure"] = float(cm.get("exposure", 0) or 0)
        plan["tone"] = cm.get("toneMapping", "none")
        plan["raw_view"] = (cm.get("view") or "").lower() == "raw" and not cm.get("ocioConfig")
        ws = working_space_name(doc)
        wsp = primaries_of(ws)
        if wsp not in (None, wp, "xyz") and not cm.get("ocioConfig"):
            if not _core_converts():
                warn_once("colorManagement", "workingSpace",
                          f"workingSpace {ws}: finishing runs in its primaries; compositing still uses Rec.709 primaries")
        if cm.get("ocioConfig"):
            try:
                plan["ocio"] = _ocio_plan(rc, cm, space, tf)
            except Exception as e:  # noqa: BLE001 — a broken config must not stop the render
                warn_once("colorManagement", "ocio", f"OpenColorIO config {cm.get('ocioConfig')!r} failed ({e}); built-in transforms used")
        if plan["ocio"] is None:
            plan["looks"] = [_look_entry(doc, lk) for lk in _looks(doc, cm)]
    trivial = (plan["ocio"] is None and plan["exposure"] == 0 and not plan["looks"] and plan["tone"] == "none"
               and space == "srgb" and tf == "srgb" and wp == "srgb")
    plan = None if trivial else plan
    rc.cache[key] = plan
    return plan


def _ocio_plan(rc, cm, space, tf) -> dict:
    O = ocio()
    doc = rc.doc
    cfg = ocio_config(cm.get("ocioConfig"), doc)
    wp = working_primaries(rc)
    ws_attr = cm.get("workingSpace") or "linear-srgb"
    ws = ocio_space(cfg, ws_attr) or cfg.getRoleColorSpace("scene_linear")
    if ws is None:
        raise ValueError(f"working space {ws_attr!r} not in the config")
    comp = ocio_space(cfg, _LIN_OF.get(wp, "linear-srgb"))
    cid = id(cfg)
    if comp is not None:
        t_in = O.ColorSpaceTransform(src=comp, dst=ws)
    elif cfg.hasRole("aces_interchange"):
        t_in = O.GroupTransform([O.MatrixTransform(_m44(matrix(wp, "ap0"))),
                                 O.ColorSpaceTransform(src=cfg.getRoleColorSpace("aces_interchange"), dst=ws)])
    else:
        warn_once("colorManagement", "ocio-input", "the config has no linear Rec.709 space nor aces_interchange role; "
                  "frames are taken as already in the working space")
        t_in = None
    looks = []
    for lk in _looks(doc, cm):
        lid = lk.get("id")
        has_own = lk.get("src") or any(lk.get(k) for k in ("slope", "offset", "power")) or \
            float(lk.get("saturation", 1) or 1) != 1
        mix = float(lk.get("mix", 1) or 1)
        if not has_own and cfg.getLook(lid) is not None:
            t = O.LookTransform(src=ws, dst=ws, looks=lid)
        else:
            e = _look_entry(doc, lk)
            lsp = ocio_space(cfg, e["space"]) or (cfg.getRoleColorSpace("color_timing") if cfg.hasRole("color_timing") else ws)
            kids = [O.ColorSpaceTransform(src=ws, dst=lsp)]
            if e["cdl"] is not None:
                s, o, p, sat = e["cdl"]
                kids.append(O.CDLTransform(slope=[float(v) for v in s], offset=[float(v) for v in o],
                                           power=[float(v) for v in p], sat=sat))
            if e["src"]:
                kids.append(O.FileTransform(src=e["src"], interpolation=O.INTERP_BEST))
            kids.append(O.ColorSpaceTransform(src=lsp, dst=ws))
            t = O.GroupTransform(kids)
        looks.append((_cpu(cfg, t, ("look", cid, ws, lid, lk.get("src"))), mix))
    oc = rc.cache.get("output_color") or (None, None)
    explicit = (oc[0] not in (None, "srgb")) or (oc[1] not in (None, "auto", "srgb"))
    displays = list(cfg.getDisplays())
    display = cm.get("display") or cfg.getDefaultDisplay()
    if display not in displays:
        display = _match_display(cfg, display if display in SPACES else "srgb", SPACES.get(display, (None, "srgb"))[1]) \
            or cfg.getDefaultDisplay()
    out_mode = "display"
    if explicit:
        d = _match_display(cfg, space, tf)
        if d is not None:
            display = d
        else:
            out_mode = "scene"          # scene-referred output: built-in encoding from the working space
    view = cm.get("view") or ""
    views = list(cfg.getViews(display))
    if view not in views:
        view = cfg.getDefaultView(display)
    t_out = O.DisplayViewTransform(src=ws, display=display, view=view)
    back = O.ColorSpaceTransform(src=ws, dst=comp) if comp is not None else \
        (O.GroupTransform([O.ColorSpaceTransform(src=ws, dst=cfg.getRoleColorSpace("aces_interchange")),
                           O.MatrixTransform(_m44(matrix("ap0", wp)))]) if cfg.hasRole("aces_interchange") else None)
    return {"cfg": cfg, "in": _cpu(cfg, t_in, ("in", cid, wp, ws)) if t_in is not None else None, "looks": looks,
            "out": _cpu(cfg, t_out, ("out", cid, ws, display, view)) if out_mode == "display" else None,
            "back": _cpu(cfg, back, ("back", cid, ws, wp)) if (out_mode == "scene" and back is not None) else None,
            "display": display, "view": view, "mode": out_mode}


def _process_ocio(rc, x: np.ndarray, plan) -> np.ndarray:
    oc = plan["ocio"]
    if oc["in"] is not None:
        x = ocio_apply(oc["in"], x)
    if plan["exposure"]:
        x = x * np.float32(2.0 ** plan["exposure"])
    for cpu, mix in oc["looks"]:
        y = ocio_apply(cpu, x)
        x = y if mix >= 1 else (x * (1 - mix) + y * mix).astype(np.float32)
    if oc["mode"] == "display":
        code = ocio_apply(oc["out"], x)
        return decode(np.clip(code, 0, 1), "srgb").astype(np.float32) if rc.linear else code.astype(np.float32)
    if oc["back"] is not None:
        x = ocio_apply(oc["back"], x)
    return _encode_final(rc, x, plan["wp"], plan, src_peak=10000.0)


def _encode_final(rc, x: np.ndarray, prims: str, plan, src_peak: float) -> np.ndarray:
    space, tf = plan["space"], plan["tf"]
    out_p = primaries_of(space)
    if out_p is not None and out_p != prims:
        x = _mul(x, matrix(prims, out_p))
    if tf not in ("linear",) and tf in ("srgb", "bt1886", "gamma22", "gamma26", "pq", "hlg"):
        x = np.maximum(x, 0)
    if space == "srgb" and tf == "srgb":
        return x.astype(np.float32) if rc.linear else encode(np.clip(x, 0, None), "srgb").astype(np.float32)
    code = encode_output(x, tf, out_p, plan["hdr"], src_peak)
    return decode(np.clip(code, 0, 1), "srgb").astype(np.float32) if rc.linear else np.asarray(code, np.float32)


def process_rgb(rc, rgb: np.ndarray, plan) -> np.ndarray:
    """Straight linear working-primaries RGB -> value the core expects (linear if rc.linear, else encoded)."""
    x = rgb.astype(np.float32)
    if plan["ocio"] is not None:
        return _process_ocio(rc, x, plan)
    wp = plan["wp"]
    if not plan["raw_view"]:
        if plan["exposure"]:
            x = x * np.float32(2.0 ** plan["exposure"])
        for lk in plan["looks"]:
            prim, enc = SPACES.get(lk["space"], ("srgb", "srgb"))
            M = matrix(wp, prim) if prim else np.eye(3)
            y = encode(_mul(x, M), enc).astype(np.float32)
            if lk["cdl"] is not None:
                y = cdl(y, *lk["cdl"]).astype(np.float32)
            if lk["lut"] is not None:
                y = np.asarray(lk["lut"](y), np.float32)
            y = _mul(decode(y, enc).astype(np.float32), np.linalg.inv(M))
            x = y if lk["mix"] >= 1 else (x * (1 - lk["mix"]) + y * lk["mix"]).astype(np.float32)
    tone = "none" if plan["raw_view"] else plan["tone"]
    if tone != "none":
        peak = float(plan["hdr"].get("peak") or 1000.0)
        y, prims = tone_map_to(_mul(x, matrix(wp, "srgb")), tone, plan["space"], plan["tf"], peak)
        hdr_peak = peak if tone == "aces2" and plan["tf"] in ("pq", "hlg") else REF_WHITE
        return _encode_final(rc, np.asarray(y, np.float32), prims, plan, hdr_peak)
    return _encode_final(rc, x, wp, plan, 10000.0)


def _finish_px(rc, px: np.ndarray, plan) -> np.ndarray:
    a = px[..., 3:4]
    safe = np.maximum(a, 1e-6)
    rgb = np.where(a > 1e-6, px[..., :3] / safe, 0)
    if not rc.linear:
        rgb = decode(rgb, "srgb")
    out = process_rgb(rc, rgb, plan)
    res = np.empty_like(px)
    res[..., :3] = out * a
    res[..., 3:] = a
    return res


def finish(rc, buf, ctx):
    plan = _plan(rc)
    if plan is None:
        return buf
    from .raster import Buf
    px = buf.px
    # The finishing transform is per pixel, so successive renders (motion-blur samples, frames of a
    # mostly still shot) only need to grade the pixels whose premultiplied value changed.
    memo = rc.cache.get("finish-memo")
    if memo is not None and memo[0] is plan and memo[1].shape == px.shape and memo[1].dtype == px.dtype:
        _, prev_in, prev_out = memo
        changed = (px != prev_in).any(-1)
        n = np.count_nonzero(changed)
        if n <= changed.size // 2:
            if n:
                prev_in[changed] = px[changed]
                prev_out[changed] = _finish_px(rc, prev_in[changed], plan)
            return Buf(prev_out.copy(), buf.x0, buf.y0)
    from . import banded
    res = banded(lambda band: _finish_px(rc, band, plan), np.empty_like(px), px)
    rc.cache["finish-memo"] = (plan, px.copy(), res.copy())
    return Buf(res, buf.x0, buf.y0)


def grade_color(rc, rgb_straight) -> tuple:
    """Apply the finishing transform to one straight working-space colour (e.g. the background)."""
    plan = _plan(rc)
    if plan is None:
        return tuple(rgb_straight)
    v = np.asarray(rgb_straight, np.float32).reshape(1, 1, 3)
    if not rc.linear:
        v = decode(v, "srgb")
    return tuple(float(c) for c in process_rgb(rc, v, plan).reshape(3))


@hook_installer("color")
def _install(rc) -> None:
    prev = rc.hooks.get("finish")
    if prev is None:
        rc.hooks["finish"] = finish
    else:
        rc.hooks["finish"] = lambda rc_, buf, ctx: finish(rc_, prev(rc_, buf, ctx), ctx)
