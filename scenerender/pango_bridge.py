"""Pango text on pycairo surfaces without python3-gi-cairo.

Layouts are built with the PyGObject Pango API; drawing goes through ctypes
calls into libpangocairo using the raw cairo_t pointer of a pycairo Context.
Font files declared by assets/font are registered with fontconfig at runtime.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

_pc = ctypes.CDLL(ctypes.util.find_library("pangocairo-1.0") or "libpangocairo-1.0.so.0")
_fc = ctypes.CDLL(ctypes.util.find_library("fontconfig") or "libfontconfig.so.1")
_api = ctypes.pythonapi
_api.PyCapsule_GetPointer.restype = ctypes.c_void_p
_api.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
for _fn in ("pango_cairo_show_layout", "pango_cairo_update_layout", "pango_cairo_layout_path"):
    getattr(_pc, _fn).argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_pc.pango_cairo_update_context.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_fc.FcConfigAppFontAddFile.restype = ctypes.c_int

# PycairoContext is {PyObject_HEAD; cairo_t *ctx; ...}.
_CR_OFFSET = object.__basicsize__


def _cairo_ptr(cr: cairo.Context) -> int:
    return ctypes.c_void_p.from_address(id(cr) + _CR_OFFSET).value


def _gptr(obj) -> int:
    return _api.PyCapsule_GetPointer(obj.__gpointer__, None)


_registered: set[str] = set()


def register_font_file(path: str) -> bool:
    """Make a font file visible to Pango. Returns False if fontconfig rejects it."""
    path = os.path.abspath(path)
    if path in _registered:
        return True
    ok = bool(_fc.FcConfigAppFontAddFile(None, path.encode()))
    if ok:
        _registered.add(path)
        # Pango caches the font map; a fresh map picks up files added after first use.
        PangoCairo.FontMap.set_default(PangoCairo.FontMap.new())
    return ok


def families() -> set[str]:
    return {f.get_name() for f in PangoCairo.FontMap.get_default().list_families()}


def new_layout() -> Pango.Layout:
    ctx = PangoCairo.FontMap.get_default().create_context()
    return Pango.Layout.new(ctx)


def show_layout(cr: cairo.Context, layout: Pango.Layout) -> None:
    p = _cairo_ptr(cr)
    _pc.pango_cairo_update_layout(p, _gptr(layout))
    _pc.pango_cairo_show_layout(p, _gptr(layout))


def layout_path(cr: cairo.Context, layout: Pango.Layout) -> None:
    """Append the glyph outlines to the current path (for strokes and text masks)."""
    p = _cairo_ptr(cr)
    _pc.pango_cairo_update_layout(p, _gptr(layout))
    _pc.pango_cairo_layout_path(p, _gptr(layout))


# ---------------------------------------------------------------- font faces (collections)
_fc.FcFreeTypeQuery.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
_fc.FcFreeTypeQuery.restype = ctypes.c_void_p
_fc.FcPatternGetString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
_fc.FcPatternGetString.restype = ctypes.c_int
_fc.FcPatternGetInteger.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
_fc.FcPatternGetInteger.restype = ctypes.c_int
_fc.FcPatternGetBool.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
_fc.FcPatternGetBool.restype = ctypes.c_int
_fc.FcPatternDestroy.argtypes = [ctypes.c_void_p]
_fc.FcWeightToOpenType.argtypes = [ctypes.c_int]
_fc.FcWeightToOpenType.restype = ctypes.c_int

_FACES: dict[tuple[str, int], dict | None] = {}
_face_warned: set[tuple] = set()


def _pat_strings(p, obj: bytes) -> list[str]:
    out, i, s = [], 0, ctypes.c_char_p()
    while _fc.FcPatternGetString(p, obj, i, ctypes.byref(s)) == 0:  # FcResultMatch
        out.append(s.value.decode("utf-8", "replace"))
        i += 1
    return out


def _pat_int(p, obj: bytes) -> int | None:
    v = ctypes.c_int()
    return v.value if _fc.FcPatternGetInteger(p, obj, 0, ctypes.byref(v)) == 0 else None


def face_info(path: str, index: int = 0) -> dict | None:
    """Family/style/weight/stretch of face `index` of a font file (a .ttc/.otc collection or a
    single font, index 0), read with fontconfig's FcFreeTypeQuery. None if there is no such face.

    Keys: family (first family name), families (all names), style (fontconfig style name),
    weight (CSS/OpenType 1..1000, or None for a variable-weight face), slant ("normal" |
    "italic" | "oblique"), stretch (percent, 100 = normal), variable (bool), count (faces in file).
    """
    key = (os.path.abspath(path), int(index))
    if key in _FACES:
        return _FACES[key]
    info = None
    count = ctypes.c_int(0)
    p = _fc.FcFreeTypeQuery(key[0].encode(), key[1], None, ctypes.byref(count)) if os.path.exists(key[0]) else None
    if p:
        try:
            fams = _pat_strings(p, b"family")
            styles = _pat_strings(p, b"style")
            fcw = _pat_int(p, b"weight")          # None when the face has a weight range
            slant = _pat_int(p, b"slant") or 0
            var = ctypes.c_int(0)
            _fc.FcPatternGetBool(p, b"variable", 0, ctypes.byref(var))
            info = {"family": fams[0] if fams else None, "families": fams,
                    "style": styles[0] if styles else None,
                    "weight": _fc.FcWeightToOpenType(fcw) if fcw is not None else None,
                    "slant": "italic" if slant == 100 else "oblique" if slant == 110 else "normal",
                    "stretch": _pat_int(p, b"width") or 100, "variable": bool(var.value),
                    "count": count.value}
        finally:
            _fc.FcPatternDestroy(p)
    _FACES[key] = info
    return info


def resolve_font_face(path: str, index: int, family: str | None, weight: int | None,
                      style: str | None) -> tuple[str | None, int, str]:
    """The Pango (family, weight, style) that selects face `index` of `path` (registered with
    register_font_file, which adds every face of a collection).

    The asset's @family is kept when it is one of the face's family names; otherwise the face's own
    family wins (with a warning once), since a family that names another face of the collection or
    nothing at all would select the wrong face. For a static face its own weight and slant win over
    the asset's @weight/@fontStyle (warning once when they differ); for a variable-weight face the
    asset's weight picks the instance. Returns the inputs unchanged when the face cannot be read.
    """
    import logging
    log = logging.getLogger("scenerender")
    w = int(weight) if weight is not None else 400
    s = style or "normal"
    info = face_info(path, index)
    if info is None:
        k = ("noface", path, index)
        if k not in _face_warned:
            _face_warned.add(k)
            log.warning("font %s: no face at collectionIndex %d; using @family as given", path, index)
        return family, w, s
    fam = family
    names = [n.lower() for n in info["families"]]
    if info["family"] and (not family or family.lower() not in names):
        k = ("family", path, index, family)
        if family and k not in _face_warned:
            _face_warned.add(k)
            log.warning("font %s[%d]: @family %r is not a family of that face; using %r",
                        path, index, family, info["family"])
        fam = info["family"]
    if info["weight"] is not None and not info["variable"]:
        if weight is not None and int(weight) != info["weight"]:
            k = ("weight", path, index, weight)
            if k not in _face_warned:
                _face_warned.add(k)
                log.warning("font %s[%d]: @weight %s differs from the face's weight %d; using the face's",
                            path, index, weight, info["weight"])
        w = info["weight"]
    if s != info["slant"]:
        k = ("style", path, index, s)
        if k not in _face_warned:
            _face_warned.add(k)
            log.warning("font %s[%d]: @fontStyle %s differs from the face's slant %s; using the face's",
                        path, index, s, info["slant"])
    s = info["slant"]
    return fam, w, s
