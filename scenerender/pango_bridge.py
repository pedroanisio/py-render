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
