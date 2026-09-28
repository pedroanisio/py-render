"""What a scene-render document reads and writes, from its XML alone (no engine import).

Attributes are classified by the scene-render 1.1 XSD: `xs:anyURI` attributes that an engine reads are inputs,
<output>/<poster>/<thumbnail>/<still> paths and <destination> URIs are outputs, and `cache` attributes are
optional inputs (bundled when present). Inputs are followed into the files they depend on: <include>d
documents, glTF buffers and images, OBJ material libraries and their maps, and MaterialX file inputs.
Image-sequence patterns (`frame_%04d.png`, `frame_####.png`) expand to the matching files.

Font families named by text (`font`, `fallback`, object3D `font`) and not declared by a <font> asset are
reported so the packer can bundle them.
"""
from __future__ import annotations

import json
import os
import re
import struct
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

MATERIAL_MAPS = ("baseColorMap", "displacementMap", "emissiveMap", "materialX", "metallicRoughnessMap",
                 "normalMap", "occlusionMap")
INPUTS = {
    "src": {"audio", "captionTrack", "chart", "data", "effect", "font", "image", "imageSequence", "include", "look",
            "lottie", "mesh", "representation", "trackData", "vector", "video"},
    "proxy": {"audio", "font", "image", "imageSequence", "lottie", "mesh", "vector", "video"},
    "fontFile": {"span", "text", "textStyle"},
    "environment": {"light"}, "ies": {"light"},
    "ocioConfig": {"colorManagement"},
    "shader": {"transition"},
    "weights": {"skeleton"},
    **{m: {"material"} for m in MATERIAL_MAPS},
}
OUTPUTS = {"path": {"output", "poster", "thumbnail", "still"}, "uri": {"destination"}}
CACHES = {"cache": {"captionTrack", "generated", "physics"}}
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")
SEQUENCE = re.compile(r"%0?(\d*)d|#+")


def local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


@dataclass
class Ref:
    path: str                       # absolute, normalised
    value: str                      # as written
    element: str
    attr: str
    source: str                     # absolute path of the file holding the reference
    kind: str = "input"             # input | output | cache | dependency


@dataclass
class Scan:
    inputs: dict = field(default_factory=dict)      # abs path -> [Ref]
    outputs: list = field(default_factory=list)     # [Ref]
    caches: list = field(default_factory=list)
    missing: list = field(default_factory=list)     # [Ref] inputs that do not exist
    remote: list = field(default_factory=list)      # [Ref] with a URL scheme (http, s3, ...)
    families: dict = field(default_factory=dict)    # family -> [source] used but not declared
    declared: set = field(default_factory=set)      # families declared by <font> assets
    output_ids: list = field(default_factory=list)  # (id, path) of <output> elements
    project: dict = field(default_factory=dict)     # <project> attributes
    audio: bool = False
    captions: list = field(default_factory=list)    # caption track languages
    errors: list = field(default_factory=list)

    def add(self, ref: Ref) -> None:
        self.inputs.setdefault(ref.path, []).append(ref)


def _value_path(value: str, base: str, pattern: bool = False):
    """(absolute path, None) for a local file reference, (None, scheme) for a URL, (None, None) to ignore.

    Image-sequence patterns are not percent-decoded: "%02d" is a frame number, not an escape."""
    value = value.strip()
    if not value:
        return None, None
    if SCHEME.match(value) and not re.match(r"^[A-Za-z]:[\\/]", value):
        scheme = value.split(":", 1)[0].lower()
        if scheme == "data":
            return None, None
        if scheme == "file":
            return os.path.normpath(urllib.parse.unquote(urllib.parse.urlparse(value).path)), None
        return None, scheme
    value = value if pattern else urllib.parse.unquote(value.split("#", 1)[0].split("?", 1)[0])
    return os.path.normpath(os.path.join(base, value)), None


def expand_sequence(path: str) -> list:
    """Files matching an image-sequence pattern (%04d, %d or ####); [path] when it is not a pattern."""
    name = os.path.basename(path)
    if not SEQUENCE.search(name):
        return [path]
    parts, last = [], 0
    for m in SEQUENCE.finditer(name):
        parts.append(re.escape(name[last:m.start()]))
        if m.group(0).startswith("%"):
            parts.append(r"\d{%s,}" % (m.group(1) or 1))
        else:
            parts.append(r"\d{%d}" % len(m.group(0)))
        last = m.end()
    parts.append(re.escape(name[last:]))
    rx, folder = re.compile("".join(parts) + "$"), os.path.dirname(path)
    try:
        return sorted(os.path.join(folder, f) for f in os.listdir(folder) if rx.match(f))
    except OSError:
        return []


def _gltf_uris(doc: dict) -> list:
    return [x["uri"] for x in doc.get("buffers", []) + doc.get("images", []) if isinstance(x.get("uri"), str)]


def dependencies(path: str) -> list:
    """Relative references inside an input file: glTF/GLB uris, OBJ mtllib, MTL maps, MaterialX filenames."""
    low = path.lower()
    try:
        if low.endswith(".gltf"):
            with open(path, encoding="utf-8") as f:
                return _gltf_uris(json.load(f))
        if low.endswith(".glb"):
            with open(path, "rb") as f:
                head = f.read(20)
                if head[:4] != b"glTF":
                    return []
                length, ctype = struct.unpack_from("<II", head, 12)
                if ctype != 0x4E4F534A:
                    return []
                return _gltf_uris(json.loads(f.read(length)))
        if low.endswith(".obj"):
            with open(path, encoding="utf-8", errors="replace") as f:
                return [ln.split(None, 1)[1].strip() for ln in f if ln.startswith("mtllib ")]
        if low.endswith(".mtl"):
            with open(path, encoding="utf-8", errors="replace") as f:
                return [ln.split()[-1] for ln in f if ln.strip().lower().startswith(("map_", "bump", "disp", "norm"))
                        and len(ln.split()) > 1]
        if low.endswith(".mtlx"):
            root = ET.parse(path).getroot()
            return [e.get("value") for e in root.iter() if e.get("type") == "filename" and e.get("value")]
    except (OSError, ValueError, struct.error, ET.ParseError):
        return []
    return []


def scan(scene: str, result: Scan | None = None, _seen=None) -> Scan:
    """Scan one scene document (and every document it <include>s)."""
    scene = os.path.abspath(scene)
    result = Scan() if result is None else result
    seen = set() if _seen is None else _seen
    if scene in seen:
        return result
    seen.add(scene)
    base = os.path.dirname(scene)
    try:
        root = ET.parse(scene).getroot()
    except (OSError, ET.ParseError) as e:
        result.errors.append(f"{scene}: {e}")
        return result
    top = _seen is None
    for el in root.iter():
        tag = local(el.tag)
        if top and tag == "project" and not result.project:
            result.project = dict(el.attrib)
        if top and tag == "output" and el.get("id"):
            result.output_ids.append((el.get("id"), el.get("path", "")))
        if tag == "audio" and el.get("src"):
            result.audio = True
        if top and tag == "captionTrack":
            result.captions.append(el.get("language") or el.get("lang") or el.get("id") or "")
        if tag == "font" and el.get("family"):
            result.declared.add(el.get("family"))
        if tag != "font":
            for attr in ("font", "fallback"):
                if el.get(attr):
                    for fam in el.get(attr).split(","):
                        fam = fam.strip().strip("'\"")
                        if fam:
                            result.families.setdefault(fam, []).append(scene)
        for attr, value in el.attrib.items():
            attr = local(attr)
            kind = ("input" if tag in INPUTS.get(attr, ()) else "output" if tag in OUTPUTS.get(attr, ())
                    else "cache" if tag in CACHES.get(attr, ()) else None)
            if kind is None:
                continue
            path, scheme = _value_path(value, base, tag == "imageSequence")
            if scheme:
                result.remote.append(Ref(value, value, tag, attr, scene, kind))
                continue
            if path is None:
                continue
            ref = Ref(path, value, tag, attr, scene, kind)
            if kind == "output":
                result.outputs.append(ref)
            elif kind == "cache":
                result.caches.append(ref)
                if os.path.isfile(path):
                    result.add(ref)
            elif tag == "imageSequence" and SEQUENCE.search(os.path.basename(path)):
                frames = expand_sequence(path)
                if not frames:
                    result.missing.append(ref)
                for f in frames:
                    result.add(Ref(f, value, tag, attr, scene, "input"))
            else:
                _add_input(result, ref)
                if tag == "include" and os.path.isfile(path):
                    scan(path, result, seen)
    if top:
        for fam in list(result.families):
            if fam in result.declared or fam.casefold() in {d.casefold() for d in result.declared}:
                del result.families[fam]
    return result


def _add_input(result: Scan, ref: Ref, depth: int = 0) -> None:
    if not os.path.exists(ref.path):
        result.missing.append(ref)
        return
    result.add(ref)
    if depth > 8 or os.path.isdir(ref.path):
        return
    for value in dependencies(ref.path):
        path, scheme = _value_path(value, os.path.dirname(ref.path))
        if scheme:
            result.remote.append(Ref(value, value, os.path.basename(ref.path), "uri", ref.path, "dependency"))
        elif path and path not in result.inputs:
            _add_input(result, Ref(path, value, os.path.basename(ref.path), "uri", ref.path, "dependency"), depth + 1)
