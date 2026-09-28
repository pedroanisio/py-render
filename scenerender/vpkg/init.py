"""`init`: a starter vpkg.json from what a project directory contains.

Detected: scene documents and their roles (engine variants from names such as *.rust.* or *.js-engine.*,
drafts whose references are missing, archives named pre-/test/old/backup), Piper voices under sources/ (as
fetch entries pinned to huggingface.co/rhasspy/piper-voices), Python packages imported by the scripts,
ffmpeg and piper use, and one render step per packed scene. The pipeline's generate steps are not guessed:
list the project's scripts in order (README commands are the usual source).
"""
from __future__ import annotations

import os
import re

from . import manifest as mf, refs
from .pack import DEFAULT_EXCLUDE, _looks_like_scene, _walk, matches, sha256_file

PIP = {"numpy": "numpy", "lxml": "lxml", "PIL": "Pillow", "scipy": "scipy", "trimesh": "trimesh",
       "shapely": "shapely", "fontTools": "fonttools", "pygltflib": "pygltflib", "cairo": "pycairo",
       "matplotlib": "matplotlib", "imageio": "imageio", "soundfile": "soundfile", "pyloudnorm": "pyloudnorm",
       "cv2": "opencv-python", "skimage": "scikit-image", "requests": "requests", "yaml": "PyYAML",
       "piper": "piper-tts", "onnxruntime": "onnxruntime"}
VARIANTS = [(re.compile(r"[._-]rust\b|[._-]rs\b"), "rs"), (re.compile(r"[._-]js(-engine)?\b"), "js"),
            (re.compile(r"[._-]c-engine\b"), "c"), (re.compile(r"[._-]py(-engine)?\b"), "py")]
ARCHIVE = re.compile(r"(^|[._-])(pre|old|bak|backup|test\d*|tmp|copy|orig(inal)?|3dtest\d*|3dred)([._-]|$)")
VOICE = re.compile(r"^([a-z]{2,3})_([A-Z]{2})-([a-z0-9_]+)-(x_low|low|medium|high)\.onnx(\.json)?$")


def piper_url(name: str) -> str | None:
    m = VOICE.match(name)
    if not m:
        return None
    lang, region, voice, quality = m.group(1), m.group(2), m.group(3), m.group(4)
    return ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
            f"{lang}/{lang}_{region}/{voice}/{quality}/{name}")


def _title(project: str) -> str:
    for name in ("README.md", "readme.md"):
        try:
            with open(os.path.join(project, name), encoding="utf-8") as f:
                for line in f:
                    if line.startswith("# "):
                        return line[2:].strip()
        except OSError:
            pass
    return os.path.basename(project).replace("-", " ")


def init(project: str) -> dict:
    project = os.path.abspath(project)
    files = [(rel, p) for rel, p in _walk(project) if not matches(rel, DEFAULT_EXCLUDE)]
    scenes = []
    for rel, p in files:
        if rel.endswith(".xml") and _looks_like_scene(p):
            scan = refs.scan(p)
            name = os.path.basename(rel)
            entry = {"path": rel, "role": "variant", "_scan": scan}
            for rx, engine in VARIANTS:
                if rx.search(name):
                    entry["targets"] = [engine]
                    base = rx.sub("", name)
                    cand = os.path.join(os.path.dirname(rel), base).lstrip("./")
                    if os.path.isfile(os.path.join(project, cand)):
                        entry["derivedFrom"] = cand
                    break
            if scan.missing:
                entry["note"] = "references missing files: " + ", ".join(sorted({r.value for r in scan.missing})[:4])
            if ARCHIVE.search(name.rsplit(".xml", 1)[0]):
                entry["role"] = "archive"
            elif "fragment" in name:
                entry["role"] = "archive"
                entry["note"] = "partial document produced and consumed by the pipeline"
            scenes.append(entry)
    # missing files that every scene lacks are likely pipeline outputs; a scene missing more is unfinished
    if any(not s["_scan"].missing for s in scenes if s["role"] == "variant"):
        for s in scenes:
            if s["role"] == "variant" and s["_scan"].missing:
                s["role"] = "draft"
    candidates = [s for s in scenes if s["role"] == "variant" and "targets" not in s]
    if candidates:
        best = max(candidates, key=lambda s: (os.path.basename(s["path"]) == "scene.xml",
                                              s["path"].endswith("scene.xml"), len(s["_scan"].output_ids),
                                              len(s["_scan"].inputs), -len(s["path"])))
        best["role"] = "primary"
    elif scenes:
        scenes[0]["role"] = "primary"
    for s in scenes:
        if s["role"] == "variant" and "targets" not in s:
            s["note"] = s.get("note", "another cut of the video")
    fetch, python, tools = [], set(), set()
    for rel, p in files:
        url = piper_url(os.path.basename(rel))
        if url and rel.endswith(".onnx"):
            fetch.append({"path": rel, "url": url, "sha256": sha256_file(p), "size": os.path.getsize(p),
                          "license": "see the voice's MODEL_CARD on huggingface.co/rhasspy/piper-voices",
                          "note": "Piper text-to-speech voice"})
        if rel.endswith(".py"):
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
            for mod in re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.M):
                if mod in PIP:
                    python.add(PIP[mod])
            if "ffmpeg" in text:
                tools.add("ffmpeg")
            if re.search(r"[\"']piper[\"']", text):
                tools.add("piper")
                python.add("piper-tts")
    primary = next((s for s in scenes if s["role"] == "primary"), None)
    languages = sorted({c for s in scenes for c in s["_scan"].captions if c and len(c) <= 8})
    steps = []
    for s in scenes:
        if s["role"] not in ("primary", "variant"):
            continue
        ids = s["_scan"].output_ids
        step = {"id": "render" if s is primary else "render-" + re.sub(r"[^a-z0-9]+", "-", s["path"].lower()).strip("-"),
                "kind": "render", "render": {"scene": s["path"]}}
        if ids:
            step["render"]["output"] = ids[0][0]
        steps.append(step)
    spec = {
        "$schema": "https://github.com/pedroanisio/py-render/scenerender/vpkg/schema/vpkg-1.0.schema.json",
        "format": mf.FORMAT, "formatVersion": mf.FORMAT_VERSION,
        "package": {"id": re.sub(r"[^a-z0-9._-]+", "-", os.path.basename(project).lower()).strip("-") or "video",
                    "version": "1.0.0", "title": _title(project), **({"languages": languages} if languages else {})},
        "scenes": [{k: v for k, v in s.items() if not k.startswith("_")} for s in scenes],
        "fetch": fetch,
        "requirements": {"tools": [{"name": "python", "version": ">=3.10"}]
                         + [{"name": t} for t in sorted(tools)], "python": sorted(python)},
        "engines": [],
        "pipeline": {"steps": steps},
    }
    if not fetch:
        del spec["fetch"]
    return spec
