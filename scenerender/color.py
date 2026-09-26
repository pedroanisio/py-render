"""Colour management: the "finish" hook (colorManagement + the output's colorSpace/transfer).

Pipeline on the whole frame (straight colour, linear light):
    working space -> exposure (x 2^exposure) -> looks in order -> tone mapping -> output encoding
The compositor always works in Rec.709/sRGB primaries (linear when project/@linearLight, the
default); a declared workingSpace / workingColorSpace with other primaries is reported and the
frame is still treated as linear sRGB. A look converts the frame into its @space (primaries +
encoding, default ACEScct), applies ASC CDL (slope, offset, power, saturation with Rec.709 luma),
then its .cube LUT (@src), converts back and blends by @mix. @looks lists the looks to apply; when
it is absent every <look> is applied in document order.

Tone mappers (scene-linear -> display-linear): aces = Stephen Hill's RRT+ODT fit, aces2 = the
same fit (PARTIAL), agx = the common polynomial AgX approximation, filmic = Hable, reinhard =
x/(1+x) per channel.

Outputs: output.py stores (colorSpace, transfer) in rc.cache["output_color"]; the hook converts
primaries (Bradford-adapted through XYZ) and applies the transfer function, then hands the core
a value it will sRGB-encode back to exactly that code value. Default srgb/auto is a no-op, and
without colorManagement the hook returns the frame untouched.
"""
from __future__ import annotations

import logging
import math
import os

import numpy as np

from .registry import FEATURES, FULL, NONE, PARTIAL, warn_once
from .render import hook_installer

log = logging.getLogger("scenerender")

for _t in ("reinhard", "filmic", "aces"):
    FEATURES.declare(f"toneMapping:{_t}", FULL)
FEATURES.declare("toneMapping:agx", PARTIAL, "polynomial approximation of Blender AgX (base look)")
FEATURES.declare("toneMapping:aces2", PARTIAL, "rendered with the ACES 1.x RRT+ODT fit")
FEATURES.declare("colorManagement:exposure", FULL)
FEATURES.declare("look:cdl", FULL, "ASC CDL in the look's space")
FEATURES.declare("look:lut", PARTIAL, ".cube 1D/3D (trilinear) only")
FEATURES.declare("colorManagement:ocio", NONE, "no OpenColorIO; the built-in transforms are used")
FEATURES.declare("colorManagement:workingSpace", PARTIAL, "compositing always uses Rec.709/sRGB primaries (linear when linearLight)")
for _s in ("srgb", "linear-srgb", "rec709", "display-p3", "dci-p3", "rec2020", "acescg", "aces2065-1", "acescct", "xyz-d65", "raw"):
    FEATURES.declare(f"colorSpace:{_s}", FULL)
for _s in ("srgb", "linear", "bt1886", "gamma22", "gamma26", "acescc", "acescct", "slog3", "logc3", "logc4", "vlog"):
    FEATURES.declare(f"transfer:{_s}", FULL)
FEATURES.declare("transfer:pq", PARTIAL, "ST 2084 with scene 1.0 = 203 cd/m2 (BT.2408); no HDR tone mapping")
FEATURES.declare("transfer:hlg", PARTIAL, "BT.2100 OETF with scene 1.0 = 75 % signal; no OOTF")
for _s in ("clog3", "redlog3g10", "flog2", "nlog"):
    FEATURES.declare(f"transfer:{_s}", NONE, "unknown curve; sRGB used")

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


def matrix(src: str, dst: str) -> np.ndarray:
    """Linear RGB in primaries src -> linear RGB in primaries dst (D65 connection space)."""
    if src == dst or src is None or dst is None:
        return np.eye(3)
    return np.linalg.inv(to_xyz_d65(dst)) @ to_xyz_d65(src)


# ====================================================================== transfer functions
def encode(x: np.ndarray, tf: str) -> np.ndarray:
    """Linear -> code value."""
    if tf == "linear":
        return x
    if tf == "srgb":
        x = np.maximum(x, 0)
        return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    if tf == "bt1886":
        return np.power(np.maximum(x, 0), 1 / 2.4)
    if tf == "gamma22":
        return np.power(np.maximum(x, 0), 1 / 2.2)
    if tf == "gamma26":
        return np.power(np.maximum(x, 0), 1 / 2.6)
    if tf == "acescct":
        x = np.maximum(x, -1.0)
        return np.where(x <= 0.0078125, 10.5402377416545 * x + 0.0729055341958355,
                        (np.log2(np.maximum(x, 1e-10)) + 9.72) / 17.52)
    if tf == "acescc":
        x = np.maximum(x, 0)
        return np.where(x < 2 ** -15, (np.log2(2 ** -16 + x * 0.5) + 9.72) / 17.52,
                        (np.log2(np.maximum(x, 1e-10)) + 9.72) / 17.52)
    if tf == "slog3":
        return np.where(x >= 0.01125000, (420.0 + np.log10(np.maximum((x + 0.01) / 0.19, 1e-10)) * 261.5) / 1023.0,
                        (x * (171.2102946929 - 95.0) / 0.01125000 + 95.0) / 1023.0)
    if tf == "logc3":
        return np.where(x > 0.010591, 0.247190 * np.log10(np.maximum(5.555556 * x + 0.052272, 1e-10)) + 0.385537,
                        5.367655 * x + 0.092809)
    if tf == "logc4":
        a = (2 ** 18 - 16) / 117.45
        b = (1023 - 95) / 1023
        c = 95 / 1023
        s = (7 * math.log(2) * 2 ** (7 - 14 * c / b)) / (a * b)
        t = (2 ** (14 * (-c / b) + 6) - 64) / a
        return np.where(x >= t, (np.log2(np.maximum(a * x + 64, 1e-10)) - 6) / 14 * b + c, (x - t) / s)
    if tf == "vlog":
        return np.where(x < 0.01, 5.6 * x + 0.125, 0.241514 * np.log10(np.maximum(x + 0.00873, 1e-10)) + 0.598206)
    if tf == "pq":
        m1, m2, c1, c2, c3 = 2610 / 16384, 2523 / 32, 3424 / 4096, 2413 / 128, 2392 / 128
        y = np.clip(x * 203.0 / 10000.0, 0, 1)
        yp = np.power(y, m1)
        return np.power((c1 + c2 * yp) / (1 + c3 * yp), m2)
    if tf == "hlg":
        a, b, c = 0.17883277, 0.28466892, 0.55991073
        e = np.clip(x * 0.2647, 0, 1)
        return np.where(e <= 1 / 12, np.sqrt(3 * e), a * np.log(np.maximum(12 * e - b, 1e-10)) + c)
    warn_once("color", f"transfer:{tf}", "unknown transfer function; sRGB used")
    return encode(x, "srgb")


def decode(v: np.ndarray, tf: str) -> np.ndarray:
    """Code value -> linear (the encodings looks and the core need)."""
    if tf == "linear":
        return v
    if tf == "srgb":
        v = np.maximum(v, 0)
        return np.where(v <= 0.04045, v / 12.92, np.power((v + 0.055) / 1.055, 2.4))
    if tf == "bt1886":
        return np.power(np.maximum(v, 0), 2.4)
    if tf == "gamma22":
        return np.power(np.maximum(v, 0), 2.2)
    if tf == "gamma26":
        return np.power(np.maximum(v, 0), 2.6)
    if tf == "acescct":
        return np.where(v <= 0.155251141552511, (v - 0.0729055341958355) / 10.5402377416545,
                        np.power(2.0, v * 17.52 - 9.72))
    if tf == "acescc":
        return np.where(v < (9.72 - 15) / 17.52, (np.power(2.0, v * 17.52 - 9.72) - 2 ** -16) * 2,
                        np.power(2.0, v * 17.52 - 9.72))
    # numeric inverse for the remaining (monotonic) curves
    grid = np.linspace(-0.05, 64.0, 65536) if tf not in ("pq", "hlg") else np.linspace(0, 10000 / 203, 65536)
    code = encode(grid, tf)
    return np.interp(v, code, grid)


# ====================================================================== tone mapping
_ACES_IN = np.array([[0.59719, 0.35458, 0.04823], [0.07600, 0.90834, 0.01566], [0.02840, 0.13383, 0.83777]])
_ACES_OUT = np.array([[1.60475, -0.53108, -0.07367], [-0.10208, 1.10813, -0.00605], [-0.00327, -0.07276, 1.07602]])
_AGX_IN = np.array([[0.842479062253094, 0.0423282422610123, 0.0423756549057051],
                    [0.0784335999999992, 0.878468636469772, 0.0784336],
                    [0.0792237451477643, 0.0791661274605434, 0.879142973793104]])
_AGX_OUT = np.linalg.inv(_AGX_IN)


def tone_map(x: np.ndarray, mode: str) -> np.ndarray:
    """Scene-linear (Rec.709 primaries) -> display-linear [0, 1]."""
    x = np.maximum(x, 0)
    if mode == "reinhard":
        return x / (1 + x)
    if mode == "filmic":
        def h(v):
            A, B, C, D, E, F = 0.15, 0.50, 0.10, 0.20, 0.02, 0.30
            return ((v * (A * v + C * B) + D * E) / (v * (A * v + B) + D * F)) - E / F
        return np.clip(h(2.0 * x) / h(11.2), 0, 1)
    if mode in ("aces", "aces2"):
        if mode == "aces2":
            warn_once("color", "aces2", "aces2 tone mapping uses the ACES 1.x RRT+ODT fit")
        v = x @ _ACES_IN.T
        a = v * (v + 0.0245786) - 0.000090537
        b = v * (0.983729 * v + 0.4329510) + 0.238081
        v = (a / b) @ _ACES_OUT.T
        return np.clip(v, 0, 1)
    if mode == "agx":
        v = x @ _AGX_IN          # the GLSL constants are column-major
        lo, hi = -12.47393, 4.026069
        v = (np.clip(np.log2(np.maximum(v, 1e-10)), lo, hi) - lo) / (hi - lo)
        v2 = v * v
        v4 = v2 * v2
        v = (15.5 * v4 * v2 - 40.14 * v4 * v + 31.96 * v4 - 6.868 * v2 * v + 0.4298 * v2 + 0.1191 * v - 0.00232)
        v = v @ _AGX_OUT
        return np.clip(np.power(np.maximum(v, 0), 2.2), 0, 1)
    if mode != "none":
        warn_once("color", f"toneMapping:{mode}", "unknown tone mapper; skipped")
    return x


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


def _plan(rc):
    """Precompute everything per (renderer, output colour); None = nothing to do."""
    key = ("color_plan", rc.cache.get("output_color"))
    if key in rc.cache:
        return rc.cache[key]
    doc = rc.doc
    cm = doc.section("colorManagement")
    space, tf = _output_target(rc)
    plan = {"exposure": 0.0, "looks": [], "tone": "none", "space": space, "tf": tf}
    if cm is not None:
        if cm.get("ocioConfig"):
            warn_once("colorManagement", "ocio", "ocioConfig is not supported; using the built-in transforms")
        ws = cm.get("workingSpace", "linear-srgb")
        if SPACES.get(ws, ("srgb",))[0] not in ("srgb", None):
            warn_once("colorManagement", "workingSpace", f"workingSpace {ws}: compositing uses linear sRGB primaries")
        plan["exposure"] = float(cm.get("exposure", 0) or 0)
        plan["tone"] = cm.get("toneMapping", "none")
        looks = {lk.get("id"): lk for lk in cm if isinstance(lk.tag, str) and lk.tag == "look"}
        ids = cm.get("looks").split() if cm.get("looks") else list(looks)
        for lid in ids:
            lk = looks.get(lid)
            if lk is None:
                lk = doc.ids.get(lid)
            if lk is None:
                warn_once("look", lid, "look not found")
                continue
            entry = {"space": lk.get("space", "acescct"), "mix": float(lk.get("mix", 1) or 1), "cdl": None, "lut": None}
            if any(lk.get(k) for k in ("slope", "offset", "power")) or float(lk.get("saturation", 1) or 1) != 1:
                entry["cdl"] = (_vec(lk.get("slope"), 1.0), _vec(lk.get("offset"), 0.0), _vec(lk.get("power"), 1.0),
                                float(lk.get("saturation", 1) or 1))
            if lk.get("src"):
                path = doc.resolve_path(lk.get("src"))
                if not path.lower().endswith(".cube"):
                    warn_once("look", lid, f"{lk.get('src')}: only .cube LUTs are supported; LUT skipped")
                elif not os.path.exists(path):
                    warn_once("look", lid, f"{path} not found; LUT skipped")
                else:
                    try:
                        entry["lut"] = read_cube(path)
                    except (OSError, ValueError, StopIteration, IndexError) as e:
                        warn_once("look", lid, f"cannot read {path}: {e}; LUT skipped")
            plan["looks"].append(entry)
    work = SPACES.get(space, ("srgb", "srgb"))
    plan["out_matrix"] = matrix("srgb", work[0]) if work[0] else None
    trivial = (plan["exposure"] == 0 and not plan["looks"] and plan["tone"] == "none" and
               space in ("srgb",) and tf == "srgb")
    plan = None if trivial else plan
    rc.cache[key] = plan
    return plan


def process_rgb(rc, rgb: np.ndarray, plan) -> np.ndarray:
    """Straight linear sRGB-primaries RGB -> value the core expects (linear if rc.linear, else encoded)."""
    x = rgb.astype(np.float32)
    if plan["exposure"]:
        x = x * np.float32(2.0 ** plan["exposure"])
    for lk in plan["looks"]:
        prim, enc = SPACES.get(lk["space"], ("srgb", "srgb"))
        M = matrix("srgb", prim) if prim else np.eye(3)
        y = encode(x @ M.T.astype(np.float32), enc) if prim or enc != "linear" else x
        if lk["cdl"] is not None:
            y = cdl(y, *lk["cdl"])
        if lk["lut"] is not None:
            y = apply_cube(y.astype(np.float32), lk["lut"])
        y = decode(y, enc) @ np.linalg.inv(M).T.astype(np.float32)
        x = y.astype(np.float32) if lk["mix"] >= 1 else (x * (1 - lk["mix"]) + y * lk["mix"]).astype(np.float32)
    x = tone_map(x, plan["tone"])
    if plan["space"] != "srgb" or plan["tf"] != "srgb":
        if plan["out_matrix"] is not None:
            x = np.maximum(x @ plan["out_matrix"].T.astype(np.float32), 0)
        code = encode(x, plan["tf"])
        return decode(np.clip(code, 0, 1), "srgb").astype(np.float32) if rc.linear else code.astype(np.float32)
    return x.astype(np.float32) if rc.linear else encode(np.clip(x, 0, None), "srgb").astype(np.float32)


def finish(rc, buf, ctx):
    plan = _plan(rc)
    if plan is None:
        return buf
    px = buf.px
    a = px[..., 3:4]
    safe = np.maximum(a, 1e-6)
    rgb = np.where(a > 1e-6, px[..., :3] / safe, 0)
    if not rc.linear:
        rgb = decode(rgb, "srgb")
    out = process_rgb(rc, rgb, plan)
    from .raster import Buf
    res = np.empty_like(px)
    res[..., :3] = out * a
    res[..., 3:] = a
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
