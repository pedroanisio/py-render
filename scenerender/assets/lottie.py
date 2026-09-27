"""lottie assets: Lottie animations (.json, .lottie) rendered with rlottie (rlottie-python).

Source (@src)
    A Lottie JSON file, or a dotLottie container (zip). In a .lottie, @animation names the animation id
    (manifest.json "animations"[].id; the file is animations/<id>.json (dotLottie 1) or a/<id>.json
    (dotLottie 2)); without @animation the manifest's activeAnimationId / initial.animation, else the first
    animation, is used. Image assets (external files next to the JSON, or images/ / i/ inside the container)
    are embedded as data URIs before the animation is handed to rlottie.

Geometry
    The Lottie canvas (its w x h) is fitted into the asset box (@width x @height) uniformly and centred
    ("xMidYMid meet", lottie-web's default). The raster is rendered at the projected size of the box
    (largest singular value of the frame matrix M, render scale included; capped at 8192 px per side), not
    at the Lottie's native size, then drawn under M with a good filter, so scaled-up Lotties stay sharp.

Time
    src_t (the layer's media time, seconds) maps to Lottie frame  first + src_t * fr, where [first, end) is
    the played range: the whole animation [ip, op), or @segment. @segment is a marker name (markers[].cm,
    range [tm, tm + dr)) or "startFrame,endFrame" (Lottie frames, half-open). Before the range the first
    frame shows, past it the last frame holds. The layer's loop / reverse / clipIn / clipOut use the range
    duration (end - first) / fr as the source duration (the asset has no @duration, so the handler applies
    the layer's loop/reverse itself when the layer gives no clipOut and no timeRemap — same formula as
    nodes.core.media_time). Sub-frame accuracy (lottie-web's default setSubframe(true)): all frame
    quantities of the JSON (fr, ip, op, st, keyframe t, markers) are multiplied by SUBFRAMES before loading,
    so rlottie's integer frame numbers resolve 1/(fr * SUBFRAMES) s.

Slots (<slot id value>), applied to the JSON before loading, in this order:
    1. Lottie slots: every property carrying "sid" == id (and the root "slots"[id].p) gets the value:
       colour properties take a colour (#RRGGBB[AA], var(--token), "r,g,b[,a]"), scalar properties a number,
       vector properties "x,y[,z]" (or space separated), text documents a string (the text), image assets
       (sid on an asset) a file path (resolved like any document path).
       The document's own root "slots" are resolved first (properties with a sid take slots[sid].p).
    2. Named layers (layer "nm" == id, any composition): text layer -> its text (every document keyframe);
       solid layer -> its colour (sc); shape layer -> every fill / stroke colour inside it;
       image layer -> the referenced image asset's file.
    3. Named shape items (fill "fl", stroke "st", gradient-free) with "nm" == id -> their colour.
    A slot that matches nothing warns once.

Text layers
    rlottie does not draw Lottie text layers (ty 5), so they are converted to shape layers before loading
    (same transform, parenting, in/out, masks, mattes, effects, so rlottie composites them in the right
    order): each line is shaped with Pango (font from the fonts list: fFamily + fStyle, font files with a
    local fPath are registered), outlines become Lottie paths, filled with fc and stroked with sc / sw
    (of: stroke over fill). Supported document properties: t (\\r / \\n line breaks), s, f, j (0 left,
    1 right, 2 centre, 3-5 justify with last line left/right/centre, 6 justify all), tr (1/1000 em),
    lh, ls, ca (1 all caps, 2 small caps), sz + ps (box text, word wrap), fc, sc, sw, of; document
    keyframes switch with hold interpolation. Text animators (t.a) with range selectors are baked per
    frame into per-character transforms / colours (lottie-web's TextSelectorProp: units r, basedOn b
    1-4, shapes sh 1-6, s / e / o / a, ease ne / xe, smoothness sm; randomize rn uses a seeded
    permutation, rlottie/lottie-web use an unseeded one); animated properties p, a, s, r, sk, sa, o,
    fc, sc, sw, fh, fs, fb, t, grouping m.g 1-4 and m.a. Text on a path (t.p with a mask) and
    expression selectors are not supported by any renderer path here and warn once (the text is
    drawn straight). Glyph "chars" embedded in the JSON are ignored; Pango shapes the text with the
    named font.

Everything else in the animation (shapes, precomps, mattes, masks, time remap, images, effects that
rlottie supports) is rlottie's rendering; expressions are not evaluated (no Lottie player without a JS
engine evaluates them).
"""
from __future__ import annotations

import base64
import colorsys
import copy
import json
import math
import mimetypes
import os
import random
import zipfile
from collections import OrderedDict

import cairo
import numpy as np

from ..registry import ASSET_SIZES, ASSETS, FULL, NONE, warn_once
from ..values import parse_color
from . import array_to_surface

try:
    from rlottie_python import LottieAnimation
except (ImportError, OSError):  # pragma: no cover - optional dependency
    LottieAnimation = None

SUBFRAMES = 8
_MAX_SIDE = 8192
_FRAME_LRU = 8


# ====================================================================== loading
def _read_source(path: str, animation: str | None) -> tuple[dict, dict[str, bytes], str] | None:
    """(Lottie JSON dict, {relative image path: bytes} from a container, base dir for external files)."""
    if not os.path.exists(path):
        warn_once("asset-file", path, "file not found")
        return None
    base = os.path.dirname(path)
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                manifest = json.loads(z.read("manifest.json")) if "manifest.json" in names else {}
                anims = [a.get("id") for a in manifest.get("animations", []) if a.get("id")]
                want = animation or manifest.get("activeAnimationId") or (manifest.get("initial") or {}).get("animation")
                if not want:
                    want = anims[0] if anims else None
                cands = [f"animations/{want}.json", f"a/{want}.json"] if want else []
                if not want:
                    cands = [n for n in names if n.endswith(".json") and n != "manifest.json"][:1]
                member = next((c for c in cands if c in names), None)
                if member is None:
                    warn_once("lottie", path, f"animation {want!r} not found in the container")
                    return None
                data = json.loads(z.read(member))
                files = {n: z.read(n) for n in names if not n.endswith("/") and not n.endswith(".json")}
                return data, files, base
        with open(path, "rb") as f:
            return json.loads(f.read()), {}, base
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
        warn_once("asset-file", path, f"cannot read Lottie: {e}")
        return None


def _all_layer_lists(d: dict) -> list[list]:
    out = [d.get("layers") or []]
    for a in d.get("assets") or []:
        if isinstance(a, dict) and isinstance(a.get("layers"), list):
            out.append(a["layers"])
    return out


def _data_uri(raw: bytes, name: str) -> str:
    mt = mimetypes.guess_type(name)[0] or "image/png"
    return f"data:{mt};base64," + base64.b64encode(raw).decode()


def _embed_images(d: dict, files: dict[str, bytes], base: str) -> None:
    for a in d.get("assets") or []:
        if not isinstance(a, dict) or "p" not in a or "layers" in a or a.get("e") == 1:
            continue
        p = str(a.get("p", ""))
        if p.startswith("data:"):
            a["e"] = 1
            continue
        u = str(a.get("u", "")).lstrip("/")
        raw = None
        for key in (u + p, "images/" + p, "i/" + p, p):
            if key in files:
                raw = files[key]
                break
        if raw is None:
            for cand in (os.path.join(base, u, p), os.path.join(base, p), p):
                if os.path.isfile(cand):
                    with open(cand, "rb") as f:
                        raw = f.read()
                    break
        if raw is None:
            warn_once("lottie", p, "image asset not found")
            continue
        a.update({"u": "", "p": _data_uri(raw, p), "e": 1})


# ====================================================================== keyframe evaluation
def _bez_y(x1: float, y1: float, x2: float, y2: float, x: float) -> float:
    """cubic-bezier(x1, y1, x2, y2) easing at progress x."""
    if x <= 0 or x >= 1:
        return min(1.0, max(0.0, x))
    lo, hi, t = 0.0, 1.0, x
    for _ in range(40):
        bx = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3
        if abs(bx - x) < 1e-7:
            break
        lo, hi = (t, hi) if bx < x else (lo, t)
        t = (lo + hi) / 2
    return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3


def _pick(v, i: int, default: float) -> float:
    if isinstance(v, list):
        return float(v[min(i, len(v) - 1)]) if v else default
    return float(v) if v is not None else default


def _is_keyed(prop) -> bool:
    k = prop.get("k") if isinstance(prop, dict) else None
    return isinstance(k, list) and bool(k) and isinstance(k[0], dict) and "t" in k[0]


def lottie_value(prop, t: float):
    """Value of a Lottie property at frame t (numbers as lists; text documents as dicts)."""
    if not isinstance(prop, dict):
        return prop
    if not _is_keyed(prop):
        return prop.get("k")
    keys = prop["k"]
    if t <= keys[0]["t"] or len(keys) == 1:
        return keys[0].get("s", keys[0].get("e"))
    for k0, k1 in zip(keys, keys[1:]):
        if t < k1["t"]:
            s0 = k0.get("s")
            s1 = k0.get("e", k1.get("s", s0))
            if k0.get("h") or isinstance(s0, dict) or s1 is None or k1["t"] <= k0["t"]:
                return s0
            u = (t - k0["t"]) / (k1["t"] - k0["t"])
            o, i = k0.get("o") or {}, k0.get("i") or {}
            s0l, s1l = (s0 if isinstance(s0, list) else [s0]), (s1 if isinstance(s1, list) else [s1])
            out = []
            for d in range(len(s0l)):
                e = _bez_y(_pick(o.get("x"), d, 0), _pick(o.get("y"), d, 0), _pick(i.get("x"), d, 1),
                           _pick(i.get("y"), d, 1), u)
                b = s1l[d] if d < len(s1l) else s0l[d]
                out.append(s0l[d] + (b - s0l[d]) * e)
            return out
    last = keys[-1]
    return last["s"] if "s" in last else keys[-2].get("e", keys[-2].get("s"))


def _num(prop, t: float, default: float = 0.0) -> float:
    v = lottie_value(prop, t) if prop is not None else None
    if v is None:
        return default
    return float(v[0]) if isinstance(v, list) else float(v)


def _vec(prop, t: float, default) -> list[float]:
    v = lottie_value(prop, t) if prop is not None else None
    if v is None:
        return list(default)
    return [float(x) for x in (v if isinstance(v, list) else [v])]


# ====================================================================== time scaling (sub-frames)
def scale_time(d: dict, k: int) -> None:
    """Multiply every frame quantity of a Lottie by k (in place)."""
    def walk(node):
        if isinstance(node, dict):
            kv = node.get("k")
            if isinstance(kv, list) and kv and all(isinstance(x, dict) and isinstance(x.get("t"), (int, float))
                                                   for x in kv):
                for x in kv:
                    x["t"] = x["t"] * k
            for key, v in node.items():
                if key != "layers" and isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    for key in ("fr", "ip", "op"):
        if key in d:
            d[key] = d[key] * k
    for m in d.get("markers") or []:
        for key in ("tm", "dr"):
            if key in m:
                m[key] = m[key] * k
    for a in d.get("assets") or []:
        if isinstance(a, dict) and "fr" in a:
            a["fr"] = a["fr"] * k
    for layers in _all_layer_lists(d):
        for L in layers:
            for key in ("ip", "op", "st"):
                if key in L:
                    L[key] = L[key] * k
            walk({kk: vv for kk, vv in L.items()})
    for key, v in d.items():
        if key not in ("layers", "assets", "markers") and isinstance(v, (dict, list)):
            walk(v)


# ====================================================================== slots
def _parse_vector(value: str) -> list[float]:
    return [float(x) for x in value.replace(",", " ").split()]


def _slot_prop_value(prop: dict, value: str, rc):
    """New static "k" for a property whose type is inferred from its current value."""
    cur = lottie_value(prop, 0.0)
    if isinstance(cur, dict) or (_is_keyed(prop) and isinstance(prop["k"][0].get("s"), dict)):
        return None                                                   # text document: handled by caller
    if isinstance(cur, list) and len(cur) in (3, 4) and all(0 <= float(c) <= 1 for c in cur) and not \
            value.replace(",", " ").replace(".", "").replace("-", "").replace(" ", "").isdigit():
        c = parse_color(value, rc.doc.tokens, (0, 0, 0, 1))
        return list(c[:len(cur)])
    try:
        v = _parse_vector(value)
    except ValueError:
        c = parse_color(value, rc.doc.tokens, None)
        return list(c[:4]) if c else None
    if isinstance(cur, list):
        if len(cur) in (3, 4) and value.strip().startswith("#"):
            return list(parse_color(value, rc.doc.tokens, (0, 0, 0, 1))[:len(cur)])
        return v if len(v) > 1 else v * len(cur)
    return v[0] if v else None


def _set_text(prop: dict, text: str) -> None:
    if _is_keyed(prop):
        for kf in prop["k"]:
            if isinstance(kf.get("s"), dict):
                kf["s"]["t"] = text
    elif isinstance(prop.get("k"), dict):
        prop["k"]["t"] = text


def _set_static(prop: dict, v) -> None:
    for key in list(prop):
        if key not in ("sid", "ix", "nm", "mn"):
            del prop[key]
    prop.update({"a": 0, "k": v})


def _is_text_doc(prop: dict) -> bool:
    k = prop.get("k")
    if _is_keyed(prop):
        return isinstance(k[0].get("s"), dict)
    return isinstance(k, dict) and "t" in k and ("s" in k or "f" in k)


def _walk_props(node, fn) -> None:
    if isinstance(node, dict):
        fn(node)
        for v in list(node.values()):
            if isinstance(v, (dict, list)):
                _walk_props(v, fn)
    elif isinstance(node, list):
        for v in node:
            _walk_props(v, fn)


def resolve_document_slots(d: dict) -> None:
    """Replace properties carrying a sid by the root slots[sid].p value (rlottie ignores slots)."""
    slots = d.get("slots") or {}
    if not slots:
        return

    def fn(node):
        sid = node.get("sid")
        if sid in slots and isinstance(slots[sid], dict) and isinstance(slots[sid].get("p"), dict):
            p = slots[sid]["p"]
            if "k" in p or "a" in p:
                keep = {k: node[k] for k in ("sid", "ix") if k in node}
                node.clear()
                node.update(copy.deepcopy(p))
                node.update(keep)
            elif "p" in p and "id" in node:                          # image slot: asset fields
                node.update({k: v for k, v in p.items() if k in ("p", "u", "e", "w", "h")})
    _walk_props(d.get("layers"), fn)
    _walk_props(d.get("assets"), fn)


def _shape_colour_items(shapes):
    for it in shapes or []:
        if not isinstance(it, dict):
            continue
        if it.get("ty") in ("fl", "st") and isinstance(it.get("c"), dict):
            yield it
        if it.get("ty") == "gr":
            yield from _shape_colour_items(it.get("it"))


def apply_slots(rc, d: dict, slots: list[tuple[str, str]], asset_id: str | None) -> None:
    """Apply <slot id value> overrides (see module docstring)."""
    for sid, value in slots:
        hit = False

        def by_sid(node, sid=sid, value=value):
            nonlocal hit
            if node.get("sid") != sid:
                return
            if "id" in node and ("p" in node or "u" in node) and "layers" not in node:
                path = rc.doc.resolve_path(value)
                if os.path.isfile(path):
                    with open(path, "rb") as f:
                        node.update({"u": "", "p": _data_uri(f.read(), path), "e": 1})
                    hit = True
                else:
                    warn_once("lottie", value, "slot image file not found")
                return
            if _is_text_doc(node):
                _set_text(node, value)
                hit = True
                return
            v = _slot_prop_value(node, value, rc)
            if v is not None:
                _set_static(node, v)
                hit = True
        _walk_props(d.get("layers"), by_sid)
        _walk_props(d.get("assets"), by_sid)
        for key, s in (d.get("slots") or {}).items():
            if key == sid and isinstance(s, dict) and isinstance(s.get("p"), dict):
                by_sid(dict(s["p"], sid=sid))
        if hit:
            continue
        col = None
        for layers in _all_layer_lists(d):
            for L in layers:
                if L.get("nm") == sid:
                    ty = L.get("ty")
                    if ty == 5 and isinstance(L.get("t"), dict):
                        _set_text(L["t"].get("d") or {}, value)
                        hit = True
                    elif ty == 1:
                        col = parse_color(value, rc.doc.tokens, (0, 0, 0, 1))
                        L["sc"] = "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in col[:3])
                        hit = True
                    elif ty == 4:
                        col = parse_color(value, rc.doc.tokens, (0, 0, 0, 1))
                        for it in _shape_colour_items(L.get("shapes")):
                            _set_static(it["c"], list(col))
                            hit = True
                    elif ty == 2:
                        ref = L.get("refId")
                        for a in d.get("assets") or []:
                            if a.get("id") == ref:
                                path = rc.doc.resolve_path(value)
                                if os.path.isfile(path):
                                    with open(path, "rb") as f:
                                        a.update({"u": "", "p": _data_uri(f.read(), path), "e": 1})
                                    hit = True
                if L.get("ty") == 4:
                    for it in _shape_colour_items(L.get("shapes")):
                        if it.get("nm") == sid:
                            col = parse_color(value, rc.doc.tokens, (0, 0, 0, 1))
                            _set_static(it["c"], list(col))
                            hit = True
        if not hit:
            warn_once("lottie", f"{asset_id}:{sid}", f"slot {sid!r} matches no Lottie slot, layer or shape item")


# ====================================================================== text layers -> shape layers
def _font_desc(d: dict, fname: str, size: float, caps: int):
    from ..pango_bridge import Pango, register_font_file
    fam, style = fname, ""
    for f in (d.get("fonts") or {}).get("list") or []:
        if f.get("fName") == fname:
            fam, style = f.get("fFamily") or fname, f.get("fStyle") or ""
            fp = f.get("fPath")
            if fp and os.path.isfile(fp):
                register_font_file(fp)
            break
    fd = Pango.FontDescription.from_string(f"{fam}, {style}".strip(", ") if style else fam)
    fd.set_family(fam)
    fd.set_absolute_size(max(1.0, size) * Pango.SCALE)
    if caps == 2:
        fd.set_variant(Pango.Variant.SMALL_CAPS)
    return fd


def _layout(text: str, fd, tracking_px: float):
    from ..pango_bridge import Pango, new_layout
    lay = new_layout()
    lay.set_font_description(fd)
    lay.set_text(text, -1)
    if tracking_px:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_letter_spacing_new(int(round(tracking_px * Pango.SCALE))))
        lay.set_attributes(attrs)
    return lay


def _wrap(text: str, fd, tracking: float, width: float) -> list[str]:
    """Greedy word wrap of one paragraph with Pango's line breaking."""
    from ..pango_bridge import Pango
    lay = _layout(text, fd, tracking)
    lay.set_width(int(max(1.0, width) * Pango.SCALE))
    lay.set_wrap(Pango.WrapMode.WORD_CHAR)
    b = text.encode()
    out = []
    for line in lay.get_lines_readonly():
        out.append(b[line.start_index:line.start_index + line.length].decode(errors="ignore").rstrip("\n"))
    return out or [""]


def _line_glyphs(text: str, fd, tracking: float):
    """Outline subpaths, per-character (x0, x1) cells, width and baseline of one shaped line."""
    from ..pango_bridge import Pango, layout_path
    lay = _layout(text, fd, tracking)
    surf = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)
    cr = cairo.Context(surf)
    fo = cairo.FontOptions()
    fo.set_hint_style(cairo.HINT_STYLE_NONE)
    fo.set_hint_metrics(cairo.HINT_METRICS_OFF)
    cr.set_font_options(fo)
    layout_path(cr, lay)
    subs, cur = [], None
    for kind, pts in cr.copy_path():
        if kind == cairo.PATH_MOVE_TO:
            cur = [("M", pts)]
            subs.append(cur)
        elif cur is not None:
            cur.append(({cairo.PATH_LINE_TO: "L", cairo.PATH_CURVE_TO: "C", cairo.PATH_CLOSE_PATH: "Z"}[kind], pts))
    cells = []
    bi = 0
    for ch in text:
        r = lay.index_to_pos(bi)
        x0, x1 = r.x / Pango.SCALE, (r.x + r.width) / Pango.SCALE
        cells.append((min(x0, x1), max(x0, x1)))
        bi += len(ch.encode())
    width = lay.get_extents()[1].width / Pango.SCALE
    return subs, cells, width, lay.get_baseline() / Pango.SCALE


def _sub_to_shape(sub, dx: float, dy: float) -> dict:
    v, ii, oo = [], [], []
    closed = False
    for kind, pts in sub:
        if kind in ("M", "L"):
            v.append([pts[0] + dx, pts[1] + dy])
            ii.append([0.0, 0.0])
            oo.append([0.0, 0.0])
        elif kind == "C" and v:
            oo[-1] = [pts[0] + dx - v[-1][0], pts[1] + dy - v[-1][1]]
            p = [pts[4] + dx, pts[5] + dy]
            v.append(p)
            ii.append([pts[2] + dx - p[0], pts[3] + dy - p[1]])
            oo.append([0.0, 0.0])
        elif kind == "Z":
            closed = True
    if closed and len(v) > 1 and abs(v[0][0] - v[-1][0]) < 1e-6 and abs(v[0][1] - v[-1][1]) < 1e-6:
        ii[0] = ii[-1]
        v.pop(), ii.pop(), oo.pop()
    return {"ty": "sh", "ks": {"a": 0, "k": {"c": closed, "v": v, "i": ii, "o": oo}}}


def _static(v) -> dict:
    return {"a": 0, "k": v}


def _baked(times: list[float], values: list) -> dict:
    if all(v == values[0] for v in values):
        return _static(values[0])
    ks = []
    for t, v in zip(times, values):
        vv = v if isinstance(v, list) else [v]
        ks.append({"t": t, "s": vv, "o": {"x": [0.0], "y": [0.0]}, "i": {"x": [1.0], "y": [1.0]}})
    del ks[-1]["o"], ks[-1]["i"]
    return {"a": 1, "k": ks}


def _tr(p=None, a=None, s=None, r=None, o=None, sk=None, sa=None) -> dict:
    """Shape-group transform; each argument is a property dict, a static value, or None (default)."""
    def prop(v, default):
        return v if isinstance(v, dict) else _static(default if v is None else v)
    return {"ty": "tr", "p": prop(p, [0, 0]), "a": prop(a, [0, 0]), "s": prop(s, [100, 100]), "r": prop(r, 0),
            "o": prop(o, 100), "sk": prop(sk, 0), "sa": prop(sa, 0)}


def _paint_items(fc, sc, sw, stroke_over: bool) -> list[dict]:
    items = []
    fill = None if fc is None else {"ty": "fl", "c": fc if isinstance(fc, dict) else _static(list(fc)[:3] + [1]),
                                    "o": _static(100), "r": 1}
    has_stroke = sc is not None and (isinstance(sw, dict) or (sw or 0) > 0)
    stroke = None if not has_stroke else {
        "ty": "st", "c": sc if isinstance(sc, dict) else _static(list(sc)[:3] + [1]), "o": _static(100),
        "w": sw if isinstance(sw, dict) else _static(float(sw)), "lc": 1, "lj": 1, "ml": 4}
    for it in ((stroke, fill) if stroke_over else (fill, stroke)):
        if it is not None:
            items.append(it)
    return items


def _selector_mult(sel: dict, ind: float, total: int, t: float, order: list[int] | None) -> float:
    """lottie-web TextSelectorProp.getMult for the character with index ind (in basedOn units)."""
    if order is not None and 0 <= int(ind) < len(order):
        ind = order[int(ind)]
    units_index = sel.get("r", 1) == 2
    div = 1.0 if units_index else 100.0 / max(1, total)
    o = _num(sel.get("o"), t, 0.0) / div
    s = _num(sel.get("s"), t, 0.0) / div + o
    e = _num(sel.get("e"), t, total if units_index else 100.0) / div + o
    if s > e:
        s, e = e, s
    ne, xe = _num(sel.get("ne"), t, 0.0), _num(sel.get("xe"), t, 0.0)
    x1, y1, x2, y2 = 0.0, 0.0, 1.0, 1.0
    if ne > 0:
        x1 = ne / 100
    else:
        y1 = -ne / 100
    if xe > 0:
        x2 = 1 - xe / 100
    else:
        y2 = 1 + xe / 100
    ease = lambda m: _bez_y(x1, y1, x2, y2, m) if (x1, y1, x2, y2) != (0, 0, 1, 1) else m  # noqa: E731
    sh = sel.get("sh", 1)
    mult = 0.0
    if sh == 2:
        mult = (1.0 if ind >= e else 0.0) if e == s else max(0.0, min(0.5 / (e - s) + (ind - s) / (e - s), 1.0))
        mult = ease(mult)
    elif sh == 3:
        mult = (0.0 if ind >= e else 1.0) if e == s else 1 - max(0.0, min(0.5 / (e - s) + (ind - s) / (e - s), 1.0))
        mult = ease(mult)
    elif sh == 4:
        if e != s:
            mult = max(0.0, min(0.5 / (e - s) + (ind - s) / (e - s), 1.0))
            mult = mult * 2 if mult < 0.5 else 1 - 2 * (mult - 0.5)
        mult = ease(mult)
    elif sh == 5:
        if e != s:
            tot = e - s
            i2 = min(max(0.0, ind + 0.5 - s), tot)
            x = -tot / 2 + i2
            a = tot / 2
            mult = math.sqrt(max(0.0, 1 - (x * x) / (a * a)))
        mult = ease(mult)
    elif sh == 6:
        if e != s:
            i2 = min(max(0.0, ind + 0.5 - s), e - s)
            mult = (1 + math.cos(math.pi + math.pi * 2 * i2 / (e - s))) / 2
        mult = ease(mult)
    else:
        if ind >= math.floor(s):
            mult = max(0.0, min(min(e, 1.0) - (s - ind), 1.0)) if ind - s < 0 else max(0.0, min(e - ind, 1.0))
        mult = ease(mult)
    sm = _num(sel.get("sm"), t, 100.0)
    if sm != 100:
        k = max(1e-8, sm * 0.01)
        th = 0.5 - k * 0.5
        mult = 0.0 if mult < th else min(1.0, (mult - th) / k)
    return mult * _num(sel.get("a"), t, 100.0) / 100.0


def _hsb_shift(c, dh: float, ds: float, db: float):
    h, s, v = colorsys.rgb_to_hsv(*c[:3])
    r, g, b = colorsys.hsv_to_rgb((h + dh / 360.0) % 1.0, min(1, max(0, s + ds)), min(1, max(0, v + db)))
    return [r, g, b]


def convert_text_layers(rc, asset, d: dict) -> None:
    """Replace every text layer (ty 5) by an equivalent shape layer (see module docstring)."""
    for layers in _all_layer_lists(d):
        for idx, L in enumerate(layers):
            if L.get("ty") == 5 and isinstance(L.get("t"), dict):
                try:
                    layers[idx] = _text_to_shape_layer(rc, asset, d, L)
                except Exception as e:  # noqa: BLE001 — a broken text layer must not lose the animation
                    warn_once("lottie", f"{asset.get('id')}:{L.get('nm')}", f"text layer not converted: {e}")


def _text_to_shape_layer(rc, asset, d: dict, L: dict) -> dict:
    tdata = L["t"]
    docs = tdata.get("d") or {}
    if _is_keyed(docs):
        keys = [(float(k["t"]), k["s"]) for k in docs["k"] if isinstance(k.get("s"), dict)]
    else:
        keys = [(0.0, docs.get("k") or {})]
    if (tdata.get("p") or {}).get("m") is not None:
        warn_once("lottie", f"{asset.get('id')}:{L.get('nm')}:path", "Lottie text on a path is drawn straight")
    animators = [a for a in tdata.get("a") or [] if isinstance(a, dict)]
    for a in animators:
        if (a.get("s") or {}).get("t") not in (None, 0):
            warn_once("lottie", f"{asset.get('id')}:{L.get('nm')}:expr", "expression text selectors are not evaluated")
    grouping = (tdata.get("m") or {}).get("g", 1)
    galign = _vec((tdata.get("m") or {}).get("a"), 0.0, [0.0, 0.0])
    sr = float(L.get("sr", 1) or 1)
    lt0, lt1 = (float(L.get("ip", 0)) - float(L.get("st", 0))) / sr, (float(L.get("op", 0)) - float(L.get("st", 0))) / sr
    times = [float(f) for f in range(int(math.floor(lt0)), int(math.ceil(lt1)) + 1)] if animators else [0.0]
    groups = []
    for ki, (kt, doc) in enumerate(keys):
        g = _text_doc_groups(rc, asset, d, L, doc, animators, grouping, galign, times)
        if len(keys) > 1:
            t_end = keys[ki + 1][0] if ki + 1 < len(keys) else None
            ks = [{"t": min(lt0, kt) - 1, "s": [0], "h": 1}, {"t": kt, "s": [100], "h": 1}]
            if ki == 0:
                ks = [{"t": min(lt0, kt) - 1, "s": [100], "h": 1}]
            if t_end is not None:
                ks.append({"t": t_end, "s": [0], "h": 1})
            g = [{"ty": "gr", "nm": f"doc{ki}", "it": g + [_tr(o={"a": 1, "k": ks})]}]
        groups.extend(g)
    out = {k: v for k, v in L.items() if k != "t"}
    out["ty"] = 4
    out["shapes"] = groups
    return out


def _text_doc_groups(rc, asset, d, L, doc, animators, grouping, galign, times) -> list[dict]:
    size = float(doc.get("s", 12) or 12)
    caps = int(doc.get("ca", 0) or 0)
    text = str(doc.get("t", "")).replace("\r\n", "\r").replace("\n", "\r").replace("\u0003", "\r")
    if caps == 1:
        text = text.upper()
    fd = _font_desc(d, str(doc.get("f", "Sans")), size, caps)
    tracking = float(doc.get("tr", 0) or 0) / 1000.0 * size
    lh = float(doc.get("lh", size * 1.2) or size * 1.2)
    ls = float(doc.get("ls", 0) or 0)
    j = int(doc.get("j", 0) or 0)
    fc = doc.get("fc")
    sc = doc.get("sc")
    sw = float(doc.get("sw", 0) or 0)
    of = bool(doc.get("of", False))
    box = doc.get("sz")
    ps = doc.get("ps") or [0, 0]
    paras = text.split("\r")
    lines: list[tuple[str, bool]] = []                       # (text, last line of its paragraph)
    for p in paras:
        wrapped = _wrap(p, fd, tracking, float(box[0])) if box else [p]
        lines += [(w, k == len(wrapped) - 1) for k, w in enumerate(wrapped)]
    # glyphs per line
    shaped = [(*_line_glyphs(t, fd, tracking), t, last) for t, last in lines]
    first_base = None
    chars = []                                              # per character: dict
    word_i = 0
    idx_nospace = 0
    for li, (subs, cells, width, base, t, last) in enumerate(shaped):
        if box:
            if first_base is None:
                first_base = float(ps[1]) + base
            x0 = float(ps[0])
            free = float(box[0]) - width
            align = j % 3 if j < 6 else 0
            if j >= 3 and not (last and j < 6):
                align = -1                                  # justified line
            x0 += {0: 0.0, 1: free, 2: free / 2, -1: 0.0}[align]
        else:
            first_base = 0.0
            x0 = {0: 0.0, 1: -width, 2: -width / 2}.get(j % 3 if j < 3 else 0, 0.0)
            align = j % 3 if j < 3 else 0
            free = 0.0
        y = first_base + li * lh - ls
        dx = x0
        # justification: spread the free width over the spaces of the line
        spaces = [i for i, ch in enumerate(t) if ch == " "]
        extra = [0.0] * len(t)
        if box and align == -1 and spaces and free > 0 and t.strip():
            per = free / len(spaces)
            acc = 0.0
            for i, ch in enumerate(t):
                extra[i] = acc
                if ch == " ":
                    acc += per
        # assign subpaths to character cells
        per_char: list[list] = [[] for _ in t]
        for sub in subs:
            xs = [p for kind, pts in sub for p in pts[0::2]]
            if not xs or not t:
                continue
            cx = (min(xs) + max(xs)) / 2
            best = min(range(len(cells)), key=lambda i: 0 if cells[i][0] <= cx < cells[i][1] else
                       min(abs(cx - cells[i][0]), abs(cx - cells[i][1])))
            per_char[best].append(sub)
        for i, ch in enumerate(t):
            if ch == " " and i > 0 and t[i - 1] != " ":
                word_i += 1
            c0, c1 = cells[i]
            chars.append({"ch": ch, "subs": per_char[i], "x0": c0 + dx + extra[i], "x1": c1 + dx + extra[i],
                          "base": base, "y": y, "line": li, "word": word_i, "dx": dx + extra[i],
                          "nospace": idx_nospace})
            if not ch.isspace():
                idx_nospace += 1
        word_i += 1
    if not chars:
        return []
    fcol = [float(c) for c in fc] if fc is not None else None
    scol = [float(c) for c in sc] if sc is not None else None
    if not animators:
        out = []
        for li in sorted({c["line"] for c in chars}):
            shapes = [_sub_to_shape(s, c["dx"], c["y"] - c["base"]) for c in chars if c["line"] == li for s in c["subs"]]
            if shapes:
                out.append({"ty": "gr", "nm": f"line{li}", "it": shapes + _paint_items(fcol, scol, sw, of) + [_tr()]})
        return out
    # ------------------------------------------------ animated: one group per character, baked per frame
    based = {1: "idx", 2: "nospace", 3: "word", 4: "line"}
    counts = {"idx": len(chars), "nospace": sum(1 for c in chars if not c["ch"].isspace()),
              "word": len({c["word"] for c in chars}), "line": len({c["line"] for c in chars})}
    words = sorted({c["word"] for c in chars})
    word_rank = {w: k for k, w in enumerate(words)}
    for k, c in enumerate(chars):
        c["idx"] = k
        c["word"] = word_rank[c["word"]]
    # group pivots (at the baseline)
    def span(key):
        res = {}
        for c in chars:
            g = c[key]
            lo, hi = res.get(g, (c["x0"], c["x1"]))
            res[g] = (min(lo, c["x0"]), max(hi, c["x1"]))
        return res
    piv = {2: span("word"), 3: span("line"), 4: {0: (min(c["x0"] for c in chars), max(c["x1"] for c in chars))}}
    orders = []
    for ai, an in enumerate(animators):
        sel = an.get("s") or {}
        if sel.get("rn"):
            key = based.get(sel.get("b", 1), "idx")
            order = list(range(counts[key]))
            random.Random(rc.ev.seed_for(asset, f"lottie:{L.get('nm')}:{ai}")).shuffle(order)
            orders.append(order)
        else:
            orders.append(None)
    out = []
    for c in chars:
        if not c["subs"] and not any((an.get("a") or {}).get("t") for an in animators):
            continue
        if grouping == 1:
            gx0, gx1 = c["x0"], c["x1"]
        else:
            gk = {2: c["word"], 3: c["line"], 4: 0}[grouping]
            gx0, gx1 = piv[grouping][gk]
        adv = gx1 - gx0
        pivot = [(gx0 + gx1) / 2 + galign[0] / 100.0 * adv / 2, c["y"] + galign[1] / 100.0 * size]
        P, A, S, R, O, SK, SA, FC, SC, SW = ([] for _ in range(10))
        for t in times:
            pos, anc, scl, rot, op, sk, sa = [0.0, 0.0], [0.0, 0.0], [100.0, 100.0], 0.0, 1.0, 0.0, 0.0
            f = list(fcol[:3]) if fcol else None
            s_ = list(scol[:3]) if scol else None
            w_ = sw
            track = 0.0
            for an, order in zip(animators, orders):
                sel, props = an.get("s") or {}, an.get("a") or {}
                key = based.get(sel.get("b", 1), "idx")
                m = _selector_mult(sel, float(c[key]), counts[key], t, order)
                if m == 0:
                    continue
                if "p" in props:
                    v = _vec(props["p"], t, [0, 0])
                    pos = [pos[0] + v[0] * m, pos[1] + v[1] * m]
                if "a" in props:
                    v = _vec(props["a"], t, [0, 0])
                    anc = [anc[0] + v[0] * m, anc[1] + v[1] * m]
                if "s" in props:
                    v = _vec(props["s"], t, [100, 100])
                    scl = [scl[k] * (1 + (v[k] / 100 - 1) * m) for k in (0, 1)]
                if "r" in props:
                    rot += _num(props["r"], t) * m
                if "sk" in props:
                    sk += _num(props["sk"], t) * m
                if "sa" in props:
                    sa = _num(props["sa"], t)
                if "o" in props:
                    op *= 1 + (_num(props["o"], t, 100) / 100 - 1) * m
                if "fc" in props and f is not None:
                    v = _vec(props["fc"], t, f)
                    f = [f[k] + (v[k] - f[k]) * m for k in range(3)]
                if f is not None and any(k in props for k in ("fh", "fs", "fb")):
                    f = _hsb_shift(f, _num(props.get("fh"), t) * m, _num(props.get("fs"), t) / 100 * m,
                                   _num(props.get("fb"), t) / 100 * m)
                if "sc" in props and s_ is not None:
                    v = _vec(props["sc"], t, s_)
                    s_ = [s_[k] + (v[k] - s_[k]) * m for k in range(3)]
                if "sw" in props:
                    w_ = w_ + _num(props["sw"], t) * m
                if "t" in props:
                    track += _num(props["t"], t) / 1000.0 * size * m
            P.append([pivot[0] + pos[0], pivot[1] + pos[1]])
            A.append([pivot[0] + anc[0], pivot[1] + anc[1]])
            S.append(scl)
            R.append(rot)
            O.append(max(0.0, min(100.0, op * 100)))
            SK.append(sk)
            SA.append(sa)
            FC.append((f or [0, 0, 0]) + [1])
            SC.append((s_ or [0, 0, 0]) + [1])
            SW.append(max(0.0, w_))
            c.setdefault("track", []).append(track)
        # tracking animators shift every later character of the line by the accumulated spacing
        c["P"], c["A"], c["S"], c["R"], c["O"], c["SK"], c["SA"], c["FC"], c["SC"], c["SW"] = P, A, S, R, O, SK, SA, FC, SC, SW
    acc: dict[int, list[float]] = {}
    for c in chars:
        if "P" not in c:
            continue
        shift = acc.get(c["line"], [0.0] * len(times))
        c["P"] = [[p[0] + s, p[1]] for p, s in zip(c["P"], shift)]
        acc[c["line"]] = [s + tr for s, tr in zip(shift, c["track"])]
        if not c["subs"]:
            continue
        shapes = [_sub_to_shape(s, c["dx"], c["y"] - c["base"]) for s in c["subs"]]
        paints = _paint_items(_baked(times, c["FC"]) if fcol else None, _baked(times, c["SC"]) if scol else None,
                              _baked(times, c["SW"]) if (scol and (sw > 0 or any(c["SW"]))) else 0, of)
        tr = _tr(p=_baked(times, c["P"]), a=_baked(times, c["A"]), s=_baked(times, c["S"]), r=_baked(times, c["R"]),
                 o=_baked(times, c["O"]), sk=_baked(times, c["SK"]), sa=_baked(times, c["SA"]))
        out.append({"ty": "gr", "nm": f"ch{c['idx']}", "it": shapes + paints + [tr]})
    return out


# ====================================================================== prepared animation
class Prepared:
    """A loaded, slot-applied, sub-frame-scaled Lottie ready for rlottie."""

    def __init__(self, data: dict, anim):
        self.data, self.anim = data, anim
        self.fr = float(data.get("fr", 30)) / SUBFRAMES
        self.ip, self.op = float(data.get("ip", 0)) / SUBFRAMES, float(data.get("op", 0)) / SUBFRAMES
        self.w, self.h = float(data.get("w", 1)), float(data.get("h", 1))
        self.markers = {m.get("cm"): (float(m.get("tm", 0)) / SUBFRAMES, float(m.get("dr", 0)) / SUBFRAMES)
                        for m in data.get("markers") or [] if isinstance(m, dict)}
        self.frames: "OrderedDict[tuple, cairo.ImageSurface]" = OrderedDict()

    def segment(self, seg: str | None, asset_id: str | None) -> tuple[float, float]:
        """Played frame range [first, end) in (original) Lottie frames."""
        if seg:
            seg = seg.strip()
            if seg in self.markers:
                tm, dr = self.markers[seg]
                return tm, tm + max(dr, 0.0)
            try:
                a, b = (float(x) for x in seg.replace(" ", "").split(","))
                return min(a, b), max(a, b)
            except ValueError:
                warn_once("lottie", f"{asset_id}:segment", f"segment {seg!r} is neither a marker nor 'start,end'")
        return self.ip, self.op

    def render(self, frame: float, rw: int, rh: int) -> cairo.ImageSurface:
        fi = int(math.floor(frame * SUBFRAMES + 1e-6))
        key = (fi, rw, rh)
        hit = self.frames.get(key)
        if hit is not None:
            self.frames.move_to_end(key)
            return hit
        rel = max(0, fi - int(round(self.ip * SUBFRAMES)))
        raw = self.anim.lottie_animation_render(frame_num=rel, width=rw, height=rh)
        bgra = np.frombuffer(raw, np.uint8).reshape(rh, rw, 4)
        surf = array_to_surface(bgra)
        self.frames[key] = surf
        while len(self.frames) > _FRAME_LRU:
            self.frames.popitem(last=False)
        return surf


def _slots(rc, asset, ctx) -> list[tuple[str, str]]:
    out = []
    for s in asset:
        if getattr(s, "tag", None) == "slot":
            out.append((s.get("id"), rc.ev.str(s, "value", ctx, s.get("value")) or ""))
    return out


def prepare(rc, asset, ctx) -> Prepared | None:
    slots = _slots(rc, asset, ctx)
    from .video import provenance_src
    path = provenance_src(rc, asset)
    key = ("lottie", path, asset.get("animation"), tuple(slots), asset.get("id"))
    if key in rc.cache:
        return rc.cache[key]
    res = None
    src = _read_source(path, asset.get("animation"))
    if src is not None and LottieAnimation is not None:
        data, files, base = src
        data = copy.deepcopy(data)
        resolve_document_slots(data)
        if slots:
            apply_slots(rc, data, slots, asset.get("id"))
        convert_text_layers(rc, asset, data)
        _embed_images(data, files, base)
        scale_time(data, SUBFRAMES)
        try:
            anim = LottieAnimation.from_data(json.dumps(data))
            res = Prepared(data, anim)
        except Exception as e:  # noqa: BLE001 — rlottie refuses the file
            warn_once("lottie", path, f"rlottie cannot load the animation: {e}")
    elif LottieAnimation is None:
        warn_once("lottie", "rlottie", "rlottie-python is not installed; Lottie layers draw nothing")
    rc.cache[key] = res
    return res


def segment_duration(rc, asset, ctx=None) -> float | None:
    """Duration (s) of the played range: the source duration used for layer loop/reverse/clipOut."""
    p = prepare(rc, asset, ctx)
    if p is None:
        return None
    a, b = p.segment(asset.get("segment"), asset.get("id"))
    return max(0.0, b - a) / p.fr if p.fr > 0 else None


@ASSETS.register("lottie", level=FULL if LottieAnimation is not None else NONE,
                 note="rlottie: .json/.lottie, @animation, @segment, sub-frame timing, slots, text layers as outlines"
                 if LottieAnimation is not None else "rlottie-python is not installed; Lottie layers draw nothing")
def render_lottie(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    p = prepare(rc, asset, ctx)
    if p is None:
        return None
    w, h = float(asset.get("width")), float(asset.get("height"))
    first, end = p.segment(asset.get("segment"), asset.get("id"))
    dur = (end - first) / p.fr if p.fr > 0 else 0.0
    last = max(first, end - 1.0 / SUBFRAMES)
    frame = min(max(first + src_t * p.fr, first), last)
    k = min(w / p.w, h / p.h)
    sv = np.linalg.svd(np.asarray(M, float)[:2, :2], compute_uv=False)
    dens = float(sv.max()) if sv.size else 1.0
    rw = int(min(_MAX_SIDE, max(1, math.ceil(p.w * k * dens))))
    rh = int(min(_MAX_SIDE, max(1, math.ceil(p.h * k * dens))))
    c = rc.canvas_for(M, w, h, 2)
    if c is None:
        return None
    surf = p.render(frame, rw, rh)
    cr = c.cr
    if clip:
        cr.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
        cr.clip()
    cr.translate((w - p.w * k) / 2, (h - p.h * k) / 2)
    cr.scale(p.w * k / rw, p.h * k / rh)
    cr.set_source_surface(surf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.paint()
    return c.to_buf(rc.linear)


@ASSET_SIZES.register("lottie")
def lottie_size(rc, asset, ctx):
    return float(asset.get("width")), float(asset.get("height"))
