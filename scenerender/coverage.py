"""`scenerender coverage SCENE [--all]`: which concepts a document uses and how well they render.

Every used concept (node kinds, asset kinds, effect/transition types, blend modes, shape and
deform modifiers, text-animator presets, audio effects, caption presets, output codecs and
cross-cutting features) is looked up in the registries (registry.load_plugins()) and listed with
its support level, note and use count. Unsupported concepts come first, then partial ones;
fully supported ones are listed with --all.
"""
from __future__ import annotations

import os
from collections import Counter

from lxml import etree

from . import registry as R
from .document import NODE_TAGS, ln

_ORDER = {R.NONE: 0, R.PARTIAL: 1, R.FULL: 2}
_TITLE = {R.NONE: "NOT SUPPORTED (skipped)", R.PARTIAL: "PARTIAL (approximated)", R.FULL: "SUPPORTED"}


def _root(path: str):
    """The prepared document when it loads, else the raw XML (so broken documents still report)."""
    from . import document
    try:
        doc = document.load(path, strict=False)
        return doc.root, len(doc.validation_errors), None
    except Exception as e:  # noqa: BLE001 — coverage must report even on documents that fail to prepare
        parser = etree.XMLParser(remove_comments=True, huge_tree=True)
        return etree.parse(path, parser).getroot(), None, str(e)


def collect(root) -> Counter:
    """Counter of (registry, name) -> uses."""
    use: Counter = Counter()

    def add(reg, name):
        if name:
            use[(reg, name)] += 1

    F = R.FEATURES
    project = root.find("project")
    if project is not None:
        if project.get("motionBlur") == "true":
            add(F, "motionBlur")
        if project.get("mode") in ("equirectangular", "viewport"):
            add(F, f"project:mode:{project.get('mode')}")
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        tag = ln(el)
        parent = el.getparent()
        ptag = ln(parent) if parent is not None else ""
        # ---- nodes and their attributes
        if tag in NODE_TAGS and ptag not in ("assets",):
            add(R.NODES, tag)
            if el.get("blend"):
                add(R.BLENDS, el.get("blend"))
            if el.get("matte"):
                add(F, "trackMatte")
            if el.get("parent"):
                add(F, "parent")
            if el.get("alignX") or el.get("alignY"):
                add(F, "align")
            if el.get("threeD") == "true":
                add(F, "threeD")
            if el.get("motionBlur") == "true":
                add(F, "motionBlur")
            if tag == "group" and el.get("layout") not in (None, "none"):
                add(F, "groupLayout")
            if tag == "sequence" and el.get("transition"):
                add(R.TRANSITIONS, el.get("transition"))
            if tag == "transition":
                add(R.TRANSITIONS, el.get("type"))
                if el.get("audio"):
                    add(F, "transitionAudio")
            if tag == "shape":
                if el.get("strokePosition") not in (None, "center"):
                    add(F, "strokePosition")
                if any(el.get(k) for k in ("trimStart", "trimEnd", "trimOffset")):
                    add(F, "trimPath")
            if tag == "object3D":
                add(F, f"object3D:{el.get('primitive')}")
            if tag == "particleEmitter":
                if el.get("preset"):
                    add(F, f"particles:preset:{el.get('preset')}")
                if el.get("collide") == "true":
                    add(F, "particles:collide")
                if el.get("emitterShape") == "asset-alpha":
                    add(F, "particles:emitterShape:asset-alpha")
            if tag == "camera":
                if el.get("depthOfField") == "true":
                    add(F, "camera:depthOfField")
                if any(ln(c) == "shake" for c in el):
                    add(F, "camera:shake")
                if any(ln(c) == "transformConstraint" for c in el):
                    add(F, "camera:transformConstraint")
            if tag == "layer":
                a = root.find(f".//assets/*[@id='{el.get('asset')}']") if el.get("asset") else None
                if a is not None and ln(a) == "video" and a.get("hasAudio") == "true" and el.get("mute") != "true":
                    add(F, "audio:layerAudio")
        # ---- assets / resources
        elif ptag == "assets":
            add(R.ASSETS, tag)
        elif ptag == "paints":
            add(F, f"paint:{tag}")
        elif tag == "effect" and ptag == "effects":
            add(R.EFFECTS, el.get("type"))
        elif tag == "mask":
            add(F, "masks")
        elif tag == "shapeModifier":
            add(R.SHAPE_MODIFIERS, el.get("type"))
        elif tag == "modifier" and ptag == "deform":
            add(R.DEFORMERS, el.get("type"))
        elif tag == "deform":
            add(F, "deform")
        elif tag == "layer" and el.get("stabilize") == "true":
            add(F, "layer:stabilize")
        elif tag == "trackData":
            add(F, f"trackData:{el.get('format', 'json')}")
            add(F, f"trackData:kind:{el.get('kind')}")
        elif tag == "textAnimator":
            add(F, "textAnimator")
            if el.get("preset"):
                add(R.TEXT_ANIMATORS, el.get("preset"))
        elif tag == "transformConstraint":
            add(F, f"constraint:{el.get('type')}")
        elif tag == "physics":
            add(F, "physics")
        elif tag in ("rigidBody", "softBody"):
            add(F, tag)
        elif tag == "forceField":
            add(F, f"forceField:{el.get('type')}")
        elif tag == "constraint" and ptag in ("physics", "constraints") or (tag == "constraint" and el.get("bodyA")):
            add(F, f"physicsConstraint:{el.get('type')}")
        elif tag in ("lights", "materials") and ptag == "scene":
            add(F, tag)
        # ---- audio
        elif tag == "audioMix":
            add(F, "audio:mix")
            ch = int(el.get("channels", 2) or 2)
            if ch > 2 or el.get("channelLayout") not in (None, "auto", "mono", "stereo"):
                add(F, "audio:surround")
        elif tag == "audioEffect":
            add(R.AUDIO_EFFECTS, el.get("type"))
        elif tag in ("audioTrack", "bus"):
            if el.get("duckUnder"):
                add(F, "audio:ducking")
            if tag == "audioTrack":
                if el.get("fitToDuration") == "true":
                    add(F, "audio:fitToDuration")
                if el.get("speed") not in (None, "1", "1.0") and el.get("preservePitch", "true") != "false":
                    add(F, "audio:preservePitch")
        elif tag == "master":
            if el.get("normalize") in ("integrated", "dynamic"):
                add(F, f"audio:normalize-{el.get('normalize')}")
        # ---- captions
        elif tag == "captionTrack":
            if el.get("mode", "burn") in ("burn", "both"):
                add(R.CAPTION_PRESETS, el.get("preset", "classic"))
            if el.get("mode") in ("sidecar", "both"):
                add(F, "captions:sidecar")
            if el.get("transcribe"):
                add(F, "captions:transcribe")
        # ---- colour
        elif tag == "colorManagement":
            if el.get("ocioConfig"):
                add(F, "colorManagement:ocio")
            if el.get("toneMapping") not in (None, "none"):
                add(F, f"toneMapping:{el.get('toneMapping')}")
            if float(el.get("exposure", 0) or 0) != 0:
                add(F, "colorManagement:exposure")
            if el.get("workingSpace") not in (None, "linear-srgb", "srgb"):
                add(F, "colorManagement:workingSpace")
        elif tag == "look":
            if any(el.get(k) for k in ("slope", "offset", "power")) or el.get("saturation") not in (None, "1"):
                add(F, "look:cdl")
            if el.get("src"):
                add(F, "look:lut")
        # ---- outputs
        elif tag == "output":
            add(R.CODECS, el.get("codec"))
            if el.get("twoPass") == "true":
                add(F, "output:twoPass")
            if el.get("maxFileSize"):
                add(F, "output:maxFileSize")
            if any(el.get(k) for k in ("maxCLL", "maxFALL", "masteringDisplay")):
                add(F, "output:hdrMetadata")
                if el.get("container") == "mxf" or (el.get("path") or "").lower().endswith(".mxf"):
                    add(F, "output:hdrMetadata:mxf")
            if el.get("colorSpace") not in (None, "srgb"):
                add(F, f"colorSpace:{el.get('colorSpace')}")
            if el.get("transfer") not in (None, "auto"):
                add(F, f"transfer:{el.get('transfer')}")
            if el.get("embedMetadata", "true") != "false" and root.find("metadata") is not None:
                add(F, "output:embedMetadata")
            if project is not None and project.get("mode") == "equirectangular" and el.get("sphericalMetadata") != "false":
                s360 = root.find("scene360")
                mesh = s360 is not None and s360.get("layout") in ("eac", "fisheye-180")
                add(F, "output:sphericalMetadata:mesh" if mesh else "output:sphericalMetadata")
        elif tag in ("poster", "thumbnail"):
            add(F, "output:posters")
        # ---- delivery QA
        elif tag == "accessibility":
            if el.get("flashCheck", "warn") != "off":
                add(F, "accessibility:flashCheck")
            if el.get("contrastCheck", "off") != "off":
                add(F, "accessibility:contrastCheck")
            if el.get("requireCaptions") == "true":
                add(F, "accessibility:requireCaptions")
            if el.get("audioDescription"):
                add(F, "accessibility:audioDescription")
        elif tag == "safeArea" and el.get("enforce", "warn") != "off":
            add(F, "safeArea:enforce")
        elif tag == "destination":
            add(F, f"output:destination:{el.get('kind')}")
    return use


# concepts handled inline by the core that no module declares

_OPTIONAL = ("constraints", "text_animators", "camera", "physics", "deform", "modifiers", "masks", "layout",
             "captions", "audio", "output", "color", "safe_areas", "qa", "publish")


def _load_all() -> None:
    import importlib
    R.load_plugins()
    for m in _OPTIONAL:
        try:
            importlib.import_module(f"scenerender.{m}")
        except ImportError:
            pass


def report(path: str, all_features: bool = False) -> str:
    _load_all()
    root, n_err, load_error = _root(path)
    use = collect(root)
    rows = []
    for (reg, name), count in use.items():
        e = reg.entries.get(name)
        if e is None:
            level, note = R.NONE, "no handler registered"
        else:
            level, note = e.level, e.note
        rows.append((_ORDER.get(level, 0), reg.category, name, count, level, note))
    rows.sort(key=lambda r: (r[0], r[1].lower(), r[2]))
    counts = Counter(r[4] for r in rows)
    head = f"coverage of {os.path.basename(path)}"
    if load_error:
        head += f"  (could not prepare: {load_error}; counted the raw XML)"
    elif n_err:
        head += f"  ({n_err} schema errors; lenient)"
    else:
        head += "  (valid)"
    lines = [head,
             f"  {len(rows)} concepts: {counts.get(R.NONE, 0)} not supported, {counts.get(R.PARTIAL, 0)} partial, "
             f"{counts.get(R.FULL, 0)} supported" + ("" if all_features else "  (--all lists the supported ones)")]
    wc = max([len(r[1]) for r in rows] + [8])
    wn = max([len(r[2]) for r in rows] + [8])
    current = None
    for order, cat, name, count, level, note in rows:
        if level == R.FULL and not all_features:
            continue
        if level != current:
            current = level
            lines.append("")
            lines.append(_TITLE.get(level, level.upper()))
        lines.append(f"  {cat:<{wc}}  {name:<{wn}}  x{count:<4} {note}".rstrip())
    return "\n".join(lines)
