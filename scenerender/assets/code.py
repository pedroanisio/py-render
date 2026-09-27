"""code assets: QR (segno), Code 128 / EAN-13 / UPC-A (built-in encoders), Data Matrix / PDF417 (zxing-cpp).

Modules are drawn as crisp rectangles in @foreground over a @background box. 2D codes keep square modules,
centred and as large as fits (quietZone modules of background on every side). 1D codes stretch their bars to
the box width (quietZone modules on the left and right) and fill the box height; no human-readable text.
EAN-13 / UPC-A accept data with or without the check digit (a wrong check digit is replaced, with a warning).
Code 128 uses code set B, switching to C for runs of 4+ digits (and starting in C for all-digit data).
"""
from __future__ import annotations

import cairo
import numpy as np

from ..registry import ASSET_SIZES, ASSETS, FULL, NONE, PARTIAL, warn_once
from ..values import parse_color

try:
    import segno
except ImportError:  # pragma: no cover - optional
    segno = None
try:
    import zxingcpp
except ImportError:  # pragma: no cover - optional
    zxingcpp = None

# ---------------------------------------------------------------- Code 128
_C128 = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 122231 "
         "113222 123122 123221 223211 221132 221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
         "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 231113 231311 112133 112331 132131 "
         "113123 113321 133121 313121 211331 231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
         "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 112412 122114 122411 142112 142211 "
         "241211 221114 413111 241112 134111 111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
         "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 114131 311141 411131 211412 211214 "
         "211232 2331112").split()
_START_B, _START_C, _CODE_B, _CODE_C, _STOP = 104, 105, 100, 99, 106


def code128_values(data: str) -> list[int]:
    """Symbol values (start ... checksum, stop) for data (ASCII 32-126; other chars are dropped)."""
    data = "".join(ch for ch in data if 32 <= ord(ch) <= 126)
    vals: list[int] = []
    i, n = 0, len(data)

    def digit_run(k):
        j = k
        while j < n and data[j].isdigit():
            j += 1
        return j - k

    mode = None
    while i < n:
        run = digit_run(i)
        use_c = run >= 4 or (run == n - i and run >= 2 and run % 2 == 0 and mode == "C")
        if i == 0 and run == n and run % 2 == 0:
            use_c = True
        if use_c and run >= 2:
            if mode != "C":
                vals.append(_START_C if mode is None else _CODE_C)
                mode = "C"
            pairs = run // 2
            for _ in range(pairs):
                vals.append(int(data[i:i + 2]))
                i += 2
            continue
        if mode != "B":
            vals.append(_START_B if mode is None else _CODE_B)
            mode = "B"
        vals.append(ord(data[i]) - 32)
        i += 1
    if not vals:
        vals.append(_START_B)
    chk = vals[0] + sum(v * k for k, v in enumerate(vals[1:], 1))
    return vals + [chk % 103, _STOP]


def code128_modules(data: str) -> list[int]:
    bits: list[int] = []
    for v in code128_values(data):
        for k, wdt in enumerate(_C128[v]):
            bits += [1 if k % 2 == 0 else 0] * int(wdt)
    return bits


# ---------------------------------------------------------------- EAN-13 / UPC-A
_L = ("0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011")
_R = tuple("".join("1" if c == "0" else "0" for c in s) for s in _L)
_G = tuple(s[::-1] for s in _R)
_PARITY = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")


def ean_check(d12: str) -> int:
    s = sum(int(c) * (3 if k % 2 else 1) for k, c in enumerate(d12))
    return (10 - s % 10) % 10


def ean13_digits(data: str, asset_id: str | None = None, upc: bool = False) -> str | None:
    d = "".join(ch for ch in data if ch.isdigit())
    if upc:
        if len(d) not in (11, 12):
            warn_once("code", asset_id, "UPC-A needs 11 or 12 digits")
            return None
        d = "0" + d
    if len(d) == 12:
        return d + str(ean_check(d))
    if len(d) == 13:
        if int(d[12]) != ean_check(d[:12]):
            warn_once("code", asset_id, "wrong EAN/UPC check digit; recomputed")
            return d[:12] + str(ean_check(d[:12]))
        return d
    warn_once("code", asset_id, "EAN-13 needs 12 or 13 digits")
    return None


def ean13_modules(d13: str) -> list[int]:
    par = _PARITY[int(d13[0])]
    s = "101"
    for k, c in enumerate(d13[1:7]):
        s += (_L if par[k] == "L" else _G)[int(c)]
    s += "01010"
    for c in d13[7:]:
        s += _R[int(c)]
    s += "101"
    return [int(c) for c in s]


# ---------------------------------------------------------------- 2D matrices
def qr_matrix(data: str, ecc: str) -> np.ndarray | None:
    if segno is None:
        return None
    q = segno.make_qr(data, error=ecc.lower(), boost_error=False)
    return np.array([list(row) for row in q.matrix], np.uint8)


def zxing_matrix(kind: str, data: str, ecc: str) -> np.ndarray | None:
    if zxingcpp is None:
        return None
    fmt = {"datamatrix": zxingcpp.BarcodeFormat.DataMatrix, "pdf417": zxingcpp.BarcodeFormat.PDF417}[kind]
    try:
        # PDF417 security level from errorCorrection; Data Matrix: square ECC 200 symbols only
        opts = {"ec_level": {"L": "2", "M": "4", "Q": "5", "H": "6"}[ecc]} if kind == "pdf417" else {"force_square": True}
        try:
            b = zxingcpp.create_barcode(data, fmt, **opts)
        except (TypeError, ValueError):
            b = zxingcpp.create_barcode(data, fmt)
        img = np.asarray(b.to_image(scale=1, add_quiet_zones=False))
    except Exception as e:  # noqa: BLE001 — encoder errors mean nothing to draw
        warn_once("code", kind, f"cannot encode: {e}")
        return None
    if img.ndim == 3:
        img = img[..., 0]
    return (img < 128).astype(np.uint8)


def modules(kind: str, data: str, ecc: str, asset_id: str | None = None):
    """(matrix (rows, cols) of 0/1 dark modules, is_2d) or (None, _)."""
    if kind == "qr":
        return qr_matrix(data, ecc), True
    if kind in ("datamatrix", "pdf417"):
        return zxing_matrix(kind, data, ecc), kind == "datamatrix"
    if kind == "code128":
        return np.array([code128_modules(data)], np.uint8), False
    if kind in ("ean13", "upc-a"):
        d = ean13_digits(data, asset_id, upc=kind == "upc-a")
        return (np.array([ean13_modules(d)], np.uint8) if d else None), False
    return None, False


# ---------------------------------------------------------------- handler
def _levels():
    return {"qr": (FULL if segno else NONE, "" if segno else "install segno"),
            "datamatrix": (FULL if zxingcpp else NONE, "via zxing-cpp" if zxingcpp else "install zxing-cpp"),
            "pdf417": (FULL if zxingcpp else NONE, "via zxing-cpp" if zxingcpp else "install zxing-cpp")}


_LV = _levels()
_LEVEL = PARTIAL if NONE in (v[0] for v in _LV.values()) else FULL


@ASSETS.register("code", level=_LEVEL,
                 note="; ".join(f"{k}: {v[1]}" for k, v in _LV.items() if v[1]) + "; 1D codes without human-readable text")
def render_code(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    ev = rc.ev
    kind = asset.get("kind")
    w, h = float(asset.get("width")), float(asset.get("height"))
    data = ev.str(asset, "data", ctx, "") or ""
    ecc = ev.str(asset, "errorCorrection", ctx, "M") or "M"
    qz = max(0, int(ev.num(asset, "quietZone", ctx, 4.0)))
    key = ("code", kind, data, ecc)
    if key not in rc.cache:
        m, is2d = modules(kind, data, ecc, asset.get("id"))
        if m is None and kind in ("qr", "datamatrix", "pdf417") and _LV[kind][0] == NONE:
            warn_once("code", kind, f"{kind} needs an optional encoder library ({_LV[kind][1]})")
        rc.cache[key] = (m, is2d)
    m, is2d = rc.cache[key]
    fg = parse_color(ev.str(asset, "foreground", ctx, "#000000FF"), rc.doc.tokens, (0, 0, 0, 1))
    bg = parse_color(ev.str(asset, "background", ctx, "#FFFFFFFF"), rc.doc.tokens, (1, 1, 1, 1))
    c = rc.canvas_for(M, w, h, 1)
    if c is None:
        return None
    cr = c.cr
    if clip:
        cr.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
        cr.clip()
    cr.rectangle(0, 0, w, h)
    cr.set_source_rgba(*bg)
    cr.fill()
    if m is None:
        return c.to_buf(rc.linear)
    rows, cols = m.shape
    if is2d:
        ms = min(w / (cols + 2 * qz), h / (rows + 2 * qz))
        mx, my = ms, ms
        ox, oy = (w - cols * ms) / 2, (h - rows * ms) / 2
    elif kind == "pdf417":
        # stacked code: zxing's bitmap already has the row height (several pixels per row); keep its aspect
        mx = my = min(w / (cols + 2 * qz), h / (rows + 2 * qz))
        ox, oy = (w - cols * mx) / 2, (h - rows * my) / 2
    else:
        mx = w / (cols + 2 * qz)
        my = h
        ox, oy = qz * mx, 0.0
    cr.set_antialias(cairo.ANTIALIAS_NONE if abs(mx - round(mx)) < 1e-6 else cairo.ANTIALIAS_DEFAULT)
    for r in range(rows):
        row = m[r]
        cidx = 0
        while cidx < cols:
            if row[cidx]:
                start = cidx
                while cidx < cols and row[cidx]:
                    cidx += 1
                # overlap by a hair so anti-aliased neighbours do not leave seams
                cr.rectangle(ox + start * mx, oy + r * my, (cidx - start) * mx, my + (1e-3 if is2d else 0))
            else:
                cidx += 1
    cr.set_source_rgba(*fg)
    cr.fill()
    return c.to_buf(rc.linear)


@ASSET_SIZES.register("code")
def code_size(rc, asset, ctx):
    return float(asset.get("width")), float(asset.get("height"))
