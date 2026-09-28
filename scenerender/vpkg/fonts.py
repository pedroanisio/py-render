"""Font discovery without fontconfig: an sfnt (TrueType/OpenType/collection) header reader and a family index.

A scene names fonts by family (`font="Nimbus Sans"`); unless the family is declared as a <font> asset, every
engine resolves it from the host's installed fonts, so the video changes (or silently falls back) on another
machine. The packer resolves such families here, copies the files into the package and declares them.

Only the `name` and `OS/2` tables are read. Families match the legacy family name (nameID 1) first, then the
typographic family (nameID 16), case-insensitively. A name that matches no family is retried with trailing
style words removed ("DejaVu Sans Bold" -> family "DejaVu Sans", weight 700), as Pango and CSS parsers do.
"""
from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass, field

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc")
GENERIC = {"serif", "sans-serif", "sans", "monospace", "mono", "cursive", "fantasy", "system-ui", "emoji", "math"}
STYLE_WORDS = {
    "thin": 100, "hairline": 100, "extralight": 200, "ultralight": 200, "light": 300, "book": 400,
    "regular": 400, "normal": 400, "roman": 400, "medium": 500, "semibold": 600, "demibold": 600,
    "bold": 700, "extrabold": 800, "ultrabold": 800, "black": 900, "heavy": 900,
    "italic": None, "oblique": None, "condensed": None,
}


@dataclass
class Face:
    path: str
    index: int                      # face index inside a collection, else 0
    family: str                     # nameID 1
    typographic: str                # nameID 16, or the family
    subfamily: str
    weight: int
    italic: bool
    copyright: str = ""
    license: str = ""
    license_url: str = ""

    @property
    def style(self) -> str:
        return "italic" if self.italic else "normal"


@dataclass
class Resolution:
    requested: str
    family: str = ""
    faces: list = field(default_factory=list)
    weight: int | None = None       # style implied by stripped words ("Bold" -> 700)
    italic: bool = False

    @property
    def found(self) -> bool:
        return bool(self.faces)


def _names(data: bytes, off: int) -> dict:
    count, string_off = struct.unpack_from(">HH", data, off + 2)
    best: dict = {}
    for i in range(count):
        pid, eid, lid, nid, length, noff = struct.unpack_from(">6H", data, off + 6 + 12 * i)
        raw = data[off + string_off + noff: off + string_off + noff + length]
        if pid == 3 or pid == 0:
            rank = 0 if (pid == 3 and lid == 0x409) else 1
            text = raw.decode("utf-16-be", "replace")
        elif pid == 1 and eid == 0:
            rank, text = 2, raw.decode("mac_roman", "replace")
        else:
            continue
        if nid not in best or rank < best[nid][0]:
            best[nid] = (rank, text.strip())
    return {k: v[1] for k, v in best.items()}


def _face(f, path: str, base: int, index: int) -> Face | None:
    f.seek(base)
    head = f.read(12)
    if len(head) < 12:
        return None
    ntables = struct.unpack_from(">H", head, 4)[0]
    records = f.read(16 * ntables)
    tables = {}
    for i in range(ntables):
        tag, _, off, length = struct.unpack_from(">4sIII", records, 16 * i)
        tables[tag] = (off, length)
    if b"name" not in tables:
        return None
    off, length = tables[b"name"]
    f.seek(off)
    names = _names(f.read(length), 0)
    weight, italic = 400, False
    if b"OS/2" in tables:
        off, length = tables[b"OS/2"]
        f.seek(off)
        os2 = f.read(min(length, 64))
        if len(os2) >= 64:
            weight = struct.unpack_from(">H", os2, 4)[0] or 400
            italic = bool(struct.unpack_from(">H", os2, 62)[0] & 0x201)
    family = names.get(1, "")
    if not family:
        return None
    sub = names.get(2, "")
    italic = italic or "italic" in sub.lower() or "oblique" in sub.lower()
    return Face(path, index, family, names.get(16, family), names.get(17, sub), weight, italic,
                names.get(0, ""), names.get(13, ""), names.get(14, ""))


def read_faces(path: str) -> list:
    """Every face in a font file; [] for anything that is not an sfnt font."""
    try:
        with open(path, "rb") as f:
            tag = f.read(12)
            if tag[:4] == b"ttcf":
                n = struct.unpack_from(">I", tag, 8)[0]
                offsets = struct.unpack(f">{n}I", f.read(4 * n))
                return [x for i, o in enumerate(offsets) if (x := _face(f, path, o, i))]
            if tag[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
                x = _face(f, path, 0, 0)
                return [x] if x else []
    except (OSError, struct.error, UnicodeError):
        pass
    return []


def system_font_dirs() -> list:
    home = os.path.expanduser("~")
    dirs = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.join(home, ".fonts"),
            os.path.join(home, ".local/share/fonts")]
    if sys.platform == "darwin":
        dirs += ["/System/Library/Fonts", "/Library/Fonts", os.path.join(home, "Library/Fonts")]
    if os.name == "nt":
        dirs += [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Fonts")]
    extra = os.environ.get("VPKG_FONT_DIRS")
    return (extra.split(os.pathsep) if extra else []) + dirs


class FontIndex:
    """Faces from project font files first, then the system font directories (scanned lazily)."""

    def __init__(self, project_files=(), system_dirs=None, use_system: bool = True):
        self.local = [x for p in project_files for x in read_faces(p)]
        self._system_dirs = system_font_dirs() if system_dirs is None else list(system_dirs)
        self._system = None if use_system else []

    @property
    def system(self) -> list:
        if self._system is None:
            faces, seen = [], set()
            for d in self._system_dirs:
                for root, _, files in os.walk(d):
                    for name in sorted(files):
                        p = os.path.join(root, name)
                        if name.lower().endswith(FONT_EXTENSIONS) and os.path.realpath(p) not in seen:
                            seen.add(os.path.realpath(p))
                            faces += read_faces(p)
            self._system = faces
        return self._system

    def _family(self, name: str) -> list:
        key = name.casefold()
        for pool in (self.local, None):
            pool = self.system if pool is None else pool
            for attr in ("family", "typographic"):
                hits = [x for x in pool if getattr(x, attr).casefold() == key]
                if hits:
                    return _dedupe(hits)
        return []

    def resolve(self, name: str) -> Resolution:
        res = Resolution(name)
        words = name.split()
        for cut in range(len(words), 0, -1):
            family, rest = " ".join(words[:cut]), [w.casefold().replace("-", "") for w in words[cut:]]
            if any(w not in STYLE_WORDS for w in rest):
                continue
            faces = self._family(family)
            if faces:
                res.family, res.faces = family, faces
                weights = [STYLE_WORDS[w] for w in rest if STYLE_WORDS[w]]
                res.weight = weights[-1] if weights else None
                res.italic = any(w in ("italic", "oblique") for w in rest)
                return res
        return res


def _dedupe(faces: list) -> list:
    """One face per (family, weight, italic): a variable or duplicate copy never shadows a static face."""
    out: dict = {}
    for x in faces:
        key = (x.family.casefold(), x.subfamily.casefold(), x.weight, x.italic)
        if key not in out:
            out[key] = x
    return sorted(out.values(), key=lambda x: (x.weight, x.italic, x.path))


def closest(faces: list, weight: int | None, italic: bool) -> Face:
    """The face CSS font matching would pick for a weight and style."""
    w = weight or 400
    return min(faces, key=lambda x: (x.italic != italic, abs(x.weight - w), x.weight < w, x.path))
