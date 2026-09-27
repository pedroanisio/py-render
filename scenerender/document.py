"""Loading and preparing a scene-render document.

Preparation turns the authored XML into the tree the compositor walks:
validation, includes, parameters (default -> variant -> CLI), binds,
layout and variant overrides, {{param}} substitution, repeat expansion,
sequence scheduling, marker and beat-grid tables, style tokens, text-style
inheritance and font registration. Everything after this module treats the
tree as read-only.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from fractions import Fraction

from lxml import etree

from .schema import Schema, default_schema
from .values import parse_float, parse_fps

log = logging.getLogger("scenerender")

NODE_TAGS = frozenset({"group", "sequence", "layer", "shape", "object3D", "camera", "particleEmitter",
                       "instance", "include", "repeat", "adjustment", "transition", "skeleton"})


class SceneError(Exception):
    pass


def ln(el) -> str:
    return el.tag if isinstance(el.tag, str) else ""


@dataclass
class Document:
    path: str
    tree: etree._ElementTree
    schema: Schema
    base: str
    validation_errors: list[str] = field(default_factory=list)
    params: dict[str, str] = field(default_factory=dict)
    param_types: dict[str, str] = field(default_factory=dict)
    data: dict[str, list] = field(default_factory=dict)
    ids: dict[str, etree._Element] = field(default_factory=dict)
    markers: dict[str, float] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    text_styles: dict[str, dict[str, str]] = field(default_factory=dict)
    fonts: dict[str, dict] = field(default_factory=dict)
    windows: dict[etree._Element, tuple[float, float | None]] = field(default_factory=dict)
    clock_shift: dict[etree._Element, float] = field(default_factory=dict)
    width: int = 0              # composition space (project size, or the layout size when reflowing)
    height: int = 0
    frame_width: int = 0        # output frame (the layout size when a layout is selected)
    frame_height: int = 0
    reframe: str = "reflow"
    fps: Fraction = Fraction(30)
    duration: float = 0.0
    seed: int = 0
    layout: str | None = None
    variant: str | None = None
    beat_grids: list[tuple[float, float, int]] = field(default_factory=list)
    _paths: dict = field(default_factory=dict)

    # ------------------------------------------------------------ convenience
    @property
    def root(self) -> etree._Element:
        return self.tree.getroot()

    def section(self, name: str):
        return self.root.find(name)

    @property
    def project(self):
        return self.root.find("project")

    def resolve_path(self, src: str) -> str:
        return src if os.path.isabs(src) else os.path.normpath(os.path.join(self.base, src))

    def schema_path(self, el) -> tuple[str, ...]:
        p = self._paths.get(el)
        if p is None:
            p = tuple(ln(a) for a in reversed(list(el.iterancestors()))) + (ln(el),)
            self._paths[el] = p
        return p

    def type_info(self, el):
        return self.schema.type_of_path(self.schema_path(el))

    def window(self, el) -> tuple[float, float | None]:
        w = self.windows.get(el)
        if w is not None:
            return w
        s = self.markers.get(el.get("startMarker")) if el.get("startMarker") else None
        if s is None:
            s = parse_float(el.get("start"), 0.0)
        e = self.markers.get(el.get("endMarker")) if el.get("endMarker") else None
        if e is None and el.get("end") is not None:
            e = parse_float(el.get("end"))
        self.windows[el] = (s, e)
        return s, e

    def nodes(self, parent) -> list:
        return [c for c in parent if ln(c) in NODE_TAGS]


# ====================================================================== load
def load(path: str, *, params: dict[str, str] | None = None, variant: str | None = None,
         layout: str | None = None, strict: bool = False, schema: Schema | None = None,
         base: str | None = None) -> Document:
    """Load and prepare a document. `base` is the directory asset paths resolve against
    (default: the document's own directory)."""
    schema = schema or default_schema()
    parser = etree.XMLParser(remove_comments=True, remove_pis=True, huge_tree=True)
    tree = etree.parse(path, parser)
    doc = Document(path=path, tree=tree, schema=schema, base=os.path.abspath(base) if base else os.path.dirname(os.path.abspath(path)))
    v = schema.validator()
    if not v.validate(tree):
        doc.validation_errors = [f"{e.line}: {e.message}" for e in v.error_log]
        if strict:
            raise SceneError("schema validation failed:\n  " + "\n  ".join(doc.validation_errors[:20]))
        log.warning("%s: %d schema errors (lenient mode, rendering anyway); first: %s",
                    os.path.basename(path), len(doc.validation_errors), doc.validation_errors[0])
    _prepare(doc, params or {}, variant, layout)
    return doc


def _prepare(doc: Document, cli_params: dict[str, str], variant: str | None, layout: str | None) -> None:
    root = doc.root
    _expand_includes(doc)
    _index(doc)
    _params(doc, cli_params, variant)
    _project(doc, layout)
    _variant_overrides(doc, variant)
    _binds(doc)
    _substitute_text(doc)
    _styles(doc)
    _markers(doc)
    _expand_repeats(doc)
    _index(doc)
    _schedule_sequences(doc)
    _fonts(doc)
    doc._paths.clear()
    root.set("{urn:scenerender}prepared", "1")


def _index(doc: Document) -> None:
    doc.ids = {}
    for el in doc.root.iter():
        if isinstance(el.tag, str) and el.get("id") is not None:
            doc.ids.setdefault(el.get("id"), el)


# ------------------------------------------------------------ project / layout
def _project(doc: Document, layout: str | None) -> None:
    p = doc.project
    doc.width, doc.height = int(p.get("width")), int(p.get("height"))
    doc.fps = parse_fps(p.get("fps"))
    doc.duration = float(p.get("duration"))
    doc.seed = int(p.get("seed", 0))
    doc.frame_width, doc.frame_height = doc.width, doc.height
    if layout:
        lay = doc.ids.get(layout)
        if lay is None or ln(lay) != "layout":
            raise SceneError(f"unknown layout {layout!r}")
        doc.layout = layout
        doc.reframe = lay.get("reframe", "reflow")
        doc.frame_width, doc.frame_height = int(lay.get("width")), int(lay.get("height"))
        if doc.reframe == "reflow":
            # Relative lengths and align* reflow into the new frame.
            doc.width, doc.height = doc.frame_width, doc.frame_height
        for o in lay.iter("override"):
            _apply_override(doc, o.get("target"), o.get("property"), o.get("value"))


def _apply_override(doc: Document, target: str, prop: str, value: str) -> None:
    el = doc.ids.get(target)
    if el is None:
        log.warning("override target %r not found", target)
        return
    el.set(prop, value)


# ------------------------------------------------------------ parameters
def _params(doc: Document, cli: dict[str, str], variant: str | None) -> None:
    sec = doc.section("parameters")
    if sec is not None:
        for p in sec.iter("param"):
            doc.params[p.get("id")] = p.get("default", "")
            doc.param_types[p.get("id")] = p.get("type")
        for d in sec.iter("data"):
            doc.data[d.get("id")] = _load_data(doc, d)
        if variant:
            var = doc.ids.get(variant)
            if var is None or ln(var) != "variant":
                raise SceneError(f"unknown variant {variant!r}")
            doc.variant = variant
            for s in var.iter("set"):
                doc.params[s.get("param")] = s.get("value")
    supplied = {}
    if sec is not None and variant:
        supplied.update({s.get("param"): "variant " + variant for s in doc.ids[variant].iter("set")})
    for k, v in cli.items():
        if k not in doc.params:
            log.warning("--param %s is not declared in <parameters>", k)
        doc.params[k] = v
        supplied[k] = "--param"
    if sec is not None:
        for p in sec.iter("param"):
            _validate_param(doc, p, doc.params.get(p.get("id"), ""), supplied.get(p.get("id")))


def _validate_param(doc: Document, p, value: str, source: str | None) -> None:
    """Check a resolved parameter value against its declaration (type, min/max, maxLength, pattern,
    options, required). A value supplied by a variant or --param that breaks the template's contract
    stops the load (SceneError); a non-conforming declared default only warns."""
    pid, typ = p.get("id"), p.get("type")
    problems = []
    if p.get("required") == "true" and value == "":
        problems.append("is required")
    if value != "":
        if typ in ("number", "time"):
            try:
                x = float(value)
            except ValueError:
                problems.append(f"{value!r} is not a number")
            else:
                if p.get("min") is not None and x < float(p.get("min")):
                    problems.append(f"{x:g} is below min {p.get('min')}")
                if p.get("max") is not None and x > float(p.get("max")):
                    problems.append(f"{x:g} is above max {p.get('max')}")
        elif typ == "boolean" and value not in ("true", "false", "1", "0"):
            problems.append(f"{value!r} is not a boolean")
        elif typ == "color":
            from .values import parse_color
            if parse_color(value, doc.tokens, (-1, -1, -1, -1))[0] < 0:
                problems.append(f"{value!r} is not a colour")
        elif typ == "asset" and value not in doc.ids:
            problems.append(f"no asset with id {value!r}")
        if typ == "enum" or (p.get("options") and typ in ("string", "enum")):
            opts = [o.strip() for o in (p.get("options") or "").split(",") if o.strip()]
            if opts and value not in opts:
                problems.append(f"{value!r} is not one of {', '.join(opts)}")
        if p.get("maxLength") and len(value) > int(p.get("maxLength")):
            problems.append(f"is longer than maxLength {p.get('maxLength')}")
        if p.get("pattern") and re.fullmatch(p.get("pattern"), value) is None:
            problems.append(f"{value!r} does not match pattern {p.get('pattern')!r}")
    if not problems:
        return
    msg = f"parameter {pid!r} " + "; ".join(problems)
    if source:
        raise SceneError(f"{msg} (from {source})")
    log.warning("%s (declared default)", msg)


def _load_data(doc: Document, d) -> list:
    text = open(doc.resolve_path(d.get("src")), encoding="utf-8").read() if d.get("src") else (d.text or "")
    fmt = d.get("format", "json")
    if fmt == "json":
        rows = json.loads(text or "[]")
        return rows if isinstance(rows, list) else [rows]
    return list(csv.DictReader(io.StringIO(text.strip()), delimiter="\t" if fmt == "tsv" else ","))


def _variant_overrides(doc: Document, variant: str | None) -> None:
    if not variant:
        return
    for o in doc.ids[variant].iter("override"):
        _apply_override(doc, o.get("target"), o.get("property"), o.get("value"))


def _binds(doc: Document) -> None:
    sec = doc.section("parameters")
    if sec is None:
        return
    for b in sec.iter("bind"):
        value = doc.params.get(b.get("param"), "")
        if b.get("map"):
            table = dict(pair.split("=", 1) for pair in b.get("map").split(";") if "=" in pair)
            value = table.get(value, value)
        _apply_override(doc, b.get("target"), b.get("property"), value)


_TPL = re.compile(r"\{\{\s*([A-Za-z_][\w.\-]*)\s*\}\}")


def _substitute_text(doc: Document) -> None:
    sub = lambda s: _TPL.sub(lambda m: str(doc.params.get(m.group(1), m.group(0))), s)  # noqa: E731
    assets = doc.section("assets")
    if assets is None:
        return
    for t in assets.iter("text"):
        if t.get("text"):
            t.set("text", sub(t.get("text")))
        for sp in t.iter("span"):
            if sp.text:
                sp.text = sub(sp.text)


# ------------------------------------------------------------ styles / markers / fonts
def _styles(doc: Document) -> None:
    sec = doc.section("styles")
    if sec is None:
        return
    for t in sec.iter("token"):
        doc.tokens[t.get("name")] = t.get("value")
    raw = {s.get("id"): s for s in sec.iter("textStyle")}

    def resolved(sid: str, depth: int = 0) -> dict[str, str]:
        if sid in doc.text_styles:
            return doc.text_styles[sid]
        s = raw[sid]
        base = resolved(s.get("basedOn"), depth + 1) if s.get("basedOn") in raw and depth < 32 else {}
        out = {**base, **{k: v for k, v in s.attrib.items() if k not in ("id", "basedOn")}}
        doc.text_styles[sid] = out
        return out

    for sid in raw:
        resolved(sid)


def _markers(doc: Document) -> None:
    sec = doc.section("markers")
    if sec is None:
        return
    for m in sec:
        if ln(m) == "marker" and m.get("id"):
            doc.markers[m.get("id")] = float(m.get("time"))
        elif ln(m) == "beatGrid":
            bpm, off, per_bar = float(m.get("bpm")), float(m.get("offset", 0)), int(m.get("beatsPerBar", 4))
            doc.beat_grids.append((bpm, off, per_bar))
            step = 60.0 / bpm
            n = int(math.ceil((doc.duration - off) / step)) + 1
            # Numbering is 1-based, like bars and beats in a DAW (the schema leaves it open).
            for i in range(n):
                doc.markers.setdefault(f"beat.{i + 1}", off + i * step)
                if i % per_bar == 0:
                    doc.markers.setdefault(f"bar.{i // per_bar + 1}", off + i * step)


def _fonts(doc: Document) -> None:
    assets = doc.section("assets")
    if assets is None:
        return
    fonts = [f for f in assets if ln(f) == "font"]
    if not fonts:
        return
    from . import pango_bridge
    for f in fonts:
        path = doc.resolve_path(f.get("src"))
        ok = os.path.exists(path) and pango_bridge.register_font_file(path)
        if not ok:
            log.warning("font %s: cannot load %s; falling back to system fonts", f.get("id"), path)
        doc.fonts[f.get("id")] = {"family": f.get("family"), "weight": int(f.get("weight", 400)),
                                  "style": f.get("fontStyle", "normal"), "path": path, "ok": ok,
                                  "collectionIndex": int(f.get("collectionIndex", 0))}


# ------------------------------------------------------------ includes
def _expand_includes(doc: Document, depth: int = 0) -> None:
    for inc in list(doc.root.iter("include")):
        src = doc.resolve_path(inc.get("src"))
        if not os.path.exists(src) or depth > 8:
            log.warning("include %s: %s not found; skipped", inc.get("id"), inc.get("src"))
            inc.getparent().remove(inc)
            continue
        other = load(src, schema=doc.schema)
        ns = inc.get("id") + "/"
        oroot = copy.deepcopy(other.root)
        _namespace_ids(doc.schema, other, oroot, ns)
        for sec in ("assets", "paints", "effects", "symbols", "materials"):
            src_sec = oroot.find(sec)
            if src_sec is None:
                continue
            dst = doc.root.find(sec)
            if dst is None:
                dst = etree.SubElement(doc.root, sec)
            dst.extend(list(src_sec))
        styles = oroot.find("styles")
        if styles is not None:
            dst = doc.root.find("styles")
            if dst is None:
                dst = etree.Element("styles")
                doc.root.insert(1, dst)
            dst.extend(list(styles))
        if inc.get("symbol"):
            body = oroot.find(f"symbols/symbol[@id='{ns}{inc.get('symbol')}']")
        else:
            body = oroot.find("composition")
        grp = etree.Element("group", {k: v for k, v in inc.attrib.items() if k not in ("src", "symbol", "sha256")})
        grp.extend([c for c in inc if ln(c) != "override"])
        if body is not None:
            grp.extend(list(body))
        inc.getparent().replace(inc, grp)
        for o in inc.iter("override"):
            _index(doc)
            _apply_override(doc, ns + o.get("target"), o.get("property"), o.get("value"))


def _namespace_ids(schema: Schema, other: Document, root, ns: str) -> None:
    """Prefix every id and every reference to one (IDREF/IDREFS attributes, url(#id)) with ns."""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        info = schema.type_of_path(tuple(ln(a) for a in reversed(list(el.iterancestors()))) + (ln(el),))
        for k, v in list(el.attrib.items()):
            a = info.attrs.get(k) if info else None
            if k == "id" or (a and a.type == "xs:IDREF") or k in ("from", "to", "target", "symbol", "asset"):
                el.set(k, ns + v)
            elif a and a.type == "xs:IDREFS":
                el.set(k, " ".join(ns + x for x in v.split()))
            elif "url(#" in v:
                el.set(k, v.replace("url(#", "url(#" + ns))


# ------------------------------------------------------------ repeat
def _expand_repeats(doc: Document) -> None:
    """<repeat> becomes a group of per-copy groups. Copy i is offset by i*offsetX/Y,
    rotated by i*rotationStep, scaled by scaleStep**i, faded by i*opacityStep and
    delayed by i*timeStep (as a timeOffset). Copies carry index/count/item for expressions."""
    for rep in reversed(list(doc.root.iter("repeat"))):
        items: list = []
        if rep.get("over"):
            src = rep.get("over")
            items = doc.data.get(src) or _param_list(doc.params.get(src, ""))
        count = len(items) if rep.get("over") else int(rep.get("count", 1))
        start_i, step_i = parse_float(rep.get("from"), 0.0), parse_float(rep.get("step"), 1.0)
        f = lambda n, d=0.0: parse_float(rep.get(n), d)  # noqa: E731
        body = [c for c in rep if ln(c) in NODE_TAGS]
        behaviour = [c for c in rep if ln(c) not in NODE_TAGS]
        rid = rep.get("id")
        grp = etree.Element("group")
        for k, v in rep.attrib.items():
            if k in ("count", "over", "var", "from", "step", "offsetX", "offsetY", "rotationStep",
                     "scaleStep", "opacityStep", "timeStep"):
                continue
            grp.set(k, v)
        grp.extend(behaviour)
        for i in range(count):
            cp = etree.SubElement(grp, "group", id=f"{rid}#{i}")
            cp.set("x", repr(i * f("offsetX")))
            cp.set("y", repr(i * f("offsetY")))
            cp.set("rotation", repr(i * f("rotationStep")))
            s = f("scaleStep", 1.0) ** i
            cp.set("scaleX", repr(s))
            cp.set("scaleY", repr(s))
            cp.set("opacity", repr(max(0.0, 1.0 - i * f("opacityStep"))))
            cp.set("timeOffset", repr(i * f("timeStep")))
            cp.set("{urn:scenerender}index", repr(start_i + i * step_i))
            cp.set("{urn:scenerender}count", str(count))
            cp.set("{urn:scenerender}var", rep.get("var", "item"))
            if items:
                cp.set("{urn:scenerender}item", json.dumps(items[i]))
            for c in body:
                cc = copy.deepcopy(c)
                for e in cc.iter():
                    if isinstance(e.tag, str) and e.get("id"):
                        e.set("id", f"{e.get('id')}#{i}")
                cp.append(cc)
        rep.getparent().replace(rep, grp)


def _param_list(v: str) -> list:
    v = v.strip()
    if v.startswith("["):
        try:
            return json.loads(v)
        except ValueError:
            pass
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


# ------------------------------------------------------------ sequence
def natural_duration(doc: Document, el) -> float:
    """Duration a node occupies when scheduled by a sequence."""
    s, e = doc.window(el)
    if el.get("end") is not None or el.get("endMarker"):
        return max(0.0, (e or 0.0) - s)
    tag = ln(el)
    if tag == "instance":
        sym = doc.ids.get(el.get("symbol"))
        d = parse_float(sym.get("duration"), 0.0) if sym is not None else 0.0
        return d / max(1e-9, abs(parse_float(el.get("speed"), 1.0)))
    if tag == "layer":
        a = doc.ids.get(el.get("asset"))
        if a is not None and a.get("duration"):
            clip_out = parse_float(el.get("clipOut"), float(a.get("duration")))
            return (clip_out - parse_float(el.get("clipIn"), 0.0)) / max(1e-9, abs(parse_float(el.get("speed"), 1.0)))
        if a is not None and ln(a) == "imageSequence":
            n = (int(a.get("last")) - int(a.get("first"))) // int(a.get("step", 1)) + 1
            return n / float(parse_fps(a.get("fps")))
    if tag in ("group", "sequence"):
        ends = [doc.window(c)[1] for c in doc.nodes(el)]
        ends = [x for x in ends if x is not None]
        return max(ends) - s if ends else 0.0
    return 0.0


def _schedule_sequences(doc: Document) -> None:
    # Inner sequences first so a nested sequence has a known duration.
    for seq in reversed(list(doc.root.iter("sequence"))):
        cursor, _ = doc.window(seq)
        gap = parse_float(seq.get("timeGap"), 0.0)
        kids = [c for c in doc.nodes(seq) if ln(c) != "transition"]
        explicit = {(t.get("from"), t.get("to")) for t in seq if ln(t) == "transition"}
        prev = None
        for c in kids:
            dur = natural_duration(doc, c)
            start = cursor + parse_float(c.get("start"), 0.0)
            doc.windows[c] = (start, start + dur)
            # The child's subtree runs on a clock that reads 0 at its scheduled start.
            doc.clock_shift[c] = start
            cursor = start + dur + gap
            if prev is not None and seq.get("transition") and (prev.get("id"), c.get("id")) not in explicit:
                etree.SubElement(seq, "transition", type=seq.get("transition"), duration=seq.get("transitionDuration", "0.5"),
                                 **{"from": prev.get("id", ""), "to": c.get("id", "")})
            prev = c
        doc.windows[seq] = (doc.window(seq)[0], cursor - gap if kids else doc.window(seq)[0])

