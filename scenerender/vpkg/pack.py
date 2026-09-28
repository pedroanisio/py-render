"""Build a .vpkg.zip from a project directory and its vpkg.json spec.

What is bundled (paths relative to the project):
  1. the scenes with role primary/variant (draft/archive only with with_archive), and every file they read
     (refs.scan: assets, <include>s, glTF buffers/textures, sequence frames) -- always, even under an
     excluded directory;
  2. files matching `include` globs or the default includes (scripts, schemas, docs, data, shaders, sources/,
     fonts/), unless they match `exclude` or the default excludes (renders/, frames/, logs, zips, caches...);
     an explicit `include` wins over a default exclude;
  3. never: files listed in `fetch` (pinned by URL + SHA-256, downloaded by `unpack --fetch`), unless
     bundle_fetch.

Portability (everything but the engine travels with the package):
  - a scene input outside the project is copied to `_external/<path relative to the common ancestor>` and the
    packaged scene is rewritten to point at it;
  - font families a scene names but does not declare are resolved (project fonts first, then the system),
    copied to `_vpkg/fonts/`, declared as <font> assets in the packaged scene, and listed in
    `_vpkg/fonts.conf` (fontconfig, relative dir) for engines and scripts that resolve families by name;
  - scripts are linted (lint.py); an error fails the pack unless allow_nonportable;
  - a missing input fails the pack unless a pipeline step declares it as an output or `fetch` provides it.

The zip is reproducible: entries are sorted after vpkg.json, vpkg.schema.json and README.md, timestamps are
SOURCE_DATE_EPOCH (else 1980-01-01), modes are 0644 (0755 for executables).
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

from . import __version__, fonts as fontlib, lint, manifest as mf, refs
from .readme import readme

DEFAULT_INCLUDE = [
    "*.py", "*.sh", "*.bash", "*.mjs", "*.js", "*.cjs", "*.ts", "Makefile", "justfile",
    "*.xsd", "*.sch", "*.rng", "*.md", "*.txt", "*.rst", "LICENSE*", "COPYING*", "CREDITS*", "NOTICE*",
    "*.json", "*.csv", "*.tsv", "*.toml", "*.yaml", "*.yml",
    "*.glsl", "*.wgsl", "*.frag", "*.vert", "*.hlsl", "*.ocio", "*.cube", "*.spi1d", "*.spi3d",
    "sources/**", "fonts/**",
]
DEFAULT_EXCLUDE = [
    "renders/", "bench/", "stills/", "frames/", "map-frames/", "__pycache__/", "*.pyc", ".git/", ".hg/", ".svn/",
    "node_modules/", ".venv/", "venv/", ".ipynb_checkpoints/", "*.log", "*.err", "*.zip", ".DS_Store",
    "Thumbs.db", "*.tmp", "*.swp", "*~", "_vpkg/", "_external/", mf.MANIFEST,
]
STORED = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".mkv", ".webm", ".mp3", ".aac", ".m4a",
          ".ogg", ".opus", ".flac", ".zip", ".gz", ".xz", ".bz2", ".7z", ".glb", ".woff", ".woff2", ".exr", ".onnx")
ROOT_SEQUENCE = ("paints", "materials", "symbols", "scene360", "markers", "tracking", "composition")
FONT_DIR = "_vpkg/fonts"
FONTS_CONF = "_vpkg/fonts.conf"
EXTERNAL_DIR = "_external"


class PackError(Exception):
    def __init__(self, errors, warnings=()):
        self.errors, self.warnings = list(errors), list(warnings)
        super().__init__("cannot pack:\n  " + "\n  ".join(self.errors))


@dataclass
class Plan:
    project: str
    spec: dict
    files: dict = field(default_factory=dict)          # rel -> abs source path
    content: dict = field(default_factory=dict)        # rel -> bytes (rewritten scenes, fonts.conf)
    roles: dict = field(default_factory=dict)          # rel -> role
    referenced_by: dict = field(default_factory=dict)  # rel -> {scene rel}
    external: list = field(default_factory=list)
    fonts: list = field(default_factory=list)
    rewrites: list = field(default_factory=list)
    credits: dict = field(default_factory=dict)        # rel -> {license, credit}
    left_out: list = field(default_factory=list)       # (rel, reason)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    scans: dict = field(default_factory=dict)          # scene rel -> refs.Scan
    font_plan: dict = field(default_factory=dict)      # scene rel -> [(requested family, Resolution)]
    font_files: dict = field(default_factory=dict)     # (font path, face index) -> packaged rel


# ----------------------------------------------------------------------------------------------- globs

def _glob_rx(pattern: str) -> re.Pattern:
    """gitignore-like: a pattern without "/" matches a name at any depth; a leading "/" anchors it at the root."""
    anchored = "/" in pattern.rstrip("/")
    pattern = pattern.lstrip("/")
    if pattern.endswith("/"):
        pattern += "**"
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        out.append("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c))
        i += 1
    body = "".join(out)
    return re.compile(("^" if anchored else "^(?:.*/)?") + body + "$")


def matches(rel: str, patterns) -> bool:
    return any(_glob_rx(p).match(rel) for p in patterns)


# ----------------------------------------------------------------------------------------------- helpers

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _inside(path: str, root: str) -> bool:
    return lint._inside(path, root)


def _walk(project: str):
    for root, dirs, files in os.walk(project):
        dirs.sort()
        for name in sorted(files):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                yield _rel(p, project), p


def _role(rel: str, abs_path: str, plan: Plan) -> str:
    name, low = rel.rsplit("/", 1)[-1], rel.lower()
    if lint.is_script(abs_path):
        return "script"
    if low.endswith((".xsd", ".sch", ".rng")):
        return "schema"
    if low.startswith("sources/") or "/sources/" in low:
        return "source"
    if low.endswith(fontlib.FONT_EXTENSIONS):
        return "font"
    if low.endswith((".md", ".txt", ".rst", ".pdf")) or name.upper().startswith(("LICENSE", "COPYING", "CREDITS",
                                                                                    "NOTICE", "README")):
        return "doc"
    if low.endswith(".json") and re.search(r"(valid|verif|report|check|qa)", name.lower()):
        return "report"
    if low.endswith((".json", ".csv", ".tsv", ".toml", ".yaml", ".yml")):
        return "data"
    if low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "preview"
    if low.endswith((".glsl", ".wgsl", ".frag", ".vert", ".hlsl", ".ocio", ".cube")):
        return "asset"
    return "other"


def _step_outputs(spec: dict) -> list:
    out = []
    for s in spec.get("pipeline", {}).get("steps", []):
        out += s.get("outputs", [])
        r = s.get("render")
        if r and r.get("to"):
            out.append(r["to"])
    return out


def _is_generated(rel: str, patterns) -> bool:
    return any(rel == p or _glob_rx(re.sub(r"\{[^}]*\}", "*", p)).match(rel) for p in patterns)


# ----------------------------------------------------------------------------------------------- plan

def plan(project: str, spec: dict | None = None, *, with_archive: bool = False, bundle_fetch: bool = False,
         allow_nonportable: bool = False, allow_missing_fonts: bool = False, allow_remote: bool = False,
         use_system_fonts: bool = True) -> Plan:
    project = os.path.abspath(project)
    if spec is None:
        spec = mf.load(os.path.join(project, mf.MANIFEST))
    else:
        errors = mf.validate(spec)
        if errors:
            raise mf.ManifestError(errors)
    p = Plan(project, spec)
    fetch = {f["path"]: f for f in spec.get("fetch", [])}
    generated = _step_outputs(spec)
    packed_scenes = [s for s in spec["scenes"] if with_archive or s["role"] in ("primary", "variant")]
    skipped_scenes = {s["path"] for s in spec["scenes"]} - {s["path"] for s in packed_scenes}

    # 1. scenes and what they read
    external: dict = {}                                   # abs -> (packaged rel, [scene rels])
    for s in packed_scenes:
        path = os.path.join(project, s["path"])
        if not os.path.isfile(path):
            if _is_generated(s["path"], generated):
                p.warnings.append(f"{s['path']}: scene is produced by the pipeline and not bundled")
                continue
            p.errors.append(f"{s['path']}: scene listed in vpkg.json does not exist")
            continue
        p.files[s["path"]], p.roles[s["path"]] = path, "scene"
        scan = refs.scan(path)
        p.scans[s["path"]] = scan
        p.errors += [f"{s['path']}: {e}" for e in scan.errors]
        for ref in scan.missing:
            rel = _rel(ref.path, project) if _inside(ref.path, project) else ref.path
            if rel in fetch or _is_generated(rel, generated):
                continue
            p.errors.append(f"{_rel(ref.source, project)}: <{ref.element} {ref.attr}=\"{ref.value}\"> not found")
        for ref in scan.remote:
            (p.warnings if allow_remote else p.errors).append(
                f"{_rel(ref.source, project)}: <{ref.element} {ref.attr}=\"{ref.value}\"> is a remote reference "
                "(not portable; download it into the project or add a fetch entry)")
        for abs_path, rs in scan.inputs.items():
            if os.path.isdir(abs_path):
                continue
            if _inside(abs_path, project):
                rel = _rel(abs_path, project)
                if rel in fetch and not bundle_fetch:
                    continue
                p.files[rel] = abs_path
                role = "include" if any(r.element == "include" for r in rs) else "asset"
                if p.roles.get(rel) != "scene":
                    p.roles[rel] = role
                p.referenced_by.setdefault(rel, set()).add(s["path"])
            else:
                entry = external.setdefault(abs_path, [None, set(), rs])
                entry[1].add(s["path"])
    if external:
        anchor = os.path.commonpath([project] + list(external))
        for abs_path, entry in sorted(external.items()):
            rel = f"{EXTERNAL_DIR}/{_rel(abs_path, anchor)}"
            entry[0] = rel
            p.files[rel], p.roles[rel] = abs_path, "asset"
            for scene in entry[1]:
                p.referenced_by.setdefault(rel, set()).add(scene)
            p.external.append({"path": rel, "original": _rel(abs_path, project), "referencedBy": sorted(entry[1])})
            for r in entry[2]:
                if not r.source.endswith(".xml") and _inside(r.source, project):
                    p.errors.append(f"{_rel(r.source, project)}: references {r.value!r} outside the project; only "
                                    "scene documents can be rewritten -- move the file into the project")

    # 2. everything else in the project tree
    includes, excludes = spec.get("include", []), spec.get("exclude", [])
    for rel, abs_path in _walk(project):
        if rel in p.files:
            if matches(rel, excludes):
                p.warnings.append(f"{rel}: excluded by vpkg.json but a scene reads it; bundled")
            continue
        if rel in fetch and not bundle_fetch:
            p.left_out.append((rel, "fetch"))
            continue
        if rel in skipped_scenes and not matches(rel, includes):
            p.left_out.append((rel, "draft/archive scene"))
            continue
        if matches(rel, excludes):
            p.left_out.append((rel, "excluded"))
        elif matches(rel, includes):
            p.files[rel] = abs_path
            if rel in skipped_scenes:
                p.roles[rel] = "scene"
        elif matches(rel, DEFAULT_EXCLUDE):
            p.left_out.append((rel, "excluded by default"))
        elif matches(rel, DEFAULT_INCLUDE):
            p.files[rel] = abs_path
        elif rel.endswith(".xml") and _looks_like_scene(abs_path):
            p.left_out.append((rel, "scene not listed in vpkg.json"))
            p.warnings.append(f"{rel}: scene document not listed in vpkg.json scenes; left out")
        else:
            p.left_out.append((rel, "not referenced"))
    for rel, abs_path in p.files.items():
        p.roles.setdefault(rel, _role(rel, abs_path, p))
    for rel in sorted(r for r in p.files if p.roles[r] == "scene" and r not in p.scans):
        # a draft/archive scene bundled on purpose (a pipeline input): fonts and rewrites, no reference errors
        p.scans[rel] = refs.scan(p.files[rel])
    for rel in fetch:
        local = os.path.join(project, rel)
        if os.path.isfile(local) and sha256_file(local) != fetch[rel]["sha256"]:
            p.errors.append(f"{rel}: local file does not match the fetch sha256")

    # 3. portability lint
    for rel, abs_path in sorted(p.files.items()):
        if p.roles.get(rel) == "script" and _inside(abs_path, project):
            for finding in lint.lint_file(abs_path, project, rel):
                if finding.severity == "error" and not allow_nonportable:
                    p.errors.append(str(finding))
                else:
                    p.warnings.append(str(finding))

    # 4. fonts, credits, scene rewrites
    _fonts(p, allow_missing_fonts, use_system_fonts)
    _credits_from_asset_manifest(p)
    _rewrite_scenes(p, {abs_path: e[0] for abs_path, e in external.items()})
    if p.errors:
        raise PackError(p.errors, p.warnings)
    return p


def _looks_like_scene(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return re.search(rb"<scene[\s>]", f.read(2048)) is not None
    except OSError:
        return False


def _fonts(p: Plan, allow_missing: bool, use_system: bool) -> None:
    project_fonts = [a for rel, a in sorted(p.files.items()) if rel.lower().endswith(fontlib.FONT_EXTENSIONS)]
    index = fontlib.FontIndex(project_fonts, use_system=use_system)
    by_path: dict = {}                                    # (font path, index) -> fonts[] entry
    for scene_rel, scan in p.scans.items():
        for requested in sorted(scan.families):
            if requested.casefold() in fontlib.GENERIC:
                continue
            res = index.resolve(requested)
            if not res.found:
                msg = (f"{scene_rel}: font family {requested!r} is not declared and not installed here; add the font "
                       "files to the project (fonts/) or declare a <font> asset")
                (p.warnings if allow_missing else p.errors).append(msg)
                continue
            faces = list(res.faces)
            for face in faces:
                key = (face.path, face.index)
                if key not in by_path:
                    if _inside(face.path, p.project):
                        rel, origin = _rel(face.path, p.project), "project"
                    else:
                        slug = re.sub(r"[^a-z0-9]+", "-", face.family.lower()).strip("-")
                        rel, origin = f"{FONT_DIR}/{slug}/{os.path.basename(face.path)}", "system"
                        p.files[rel], p.roles[rel] = face.path, "font"
                    by_path[key] = {"family": res.family, "requested": [], "path": rel, "weight": face.weight,
                                    "style": face.style, "origin": origin, "declaredIn": [],
                                    **({"index": face.index} if face.index else {}),
                                    **({"copyright": face.copyright} if face.copyright else {}),
                                    **({"license": face.license} if face.license else {}),
                                    **({"licenseUrl": face.license_url} if face.license_url else {})}
                p.font_files[key] = by_path[key]["path"]
                entry = by_path[key]
                if requested not in entry["requested"]:
                    entry["requested"].append(requested)
                if scene_rel not in entry["declaredIn"]:
                    entry["declaredIn"].append(scene_rel)
            p.font_plan.setdefault(scene_rel, []).append((requested, res))
    p.fonts = sorted(by_path.values(), key=lambda e: (e["family"], e["weight"], e["style"], e["path"]))
    if any(e["origin"] == "system" for e in p.fonts):
        p.content[FONTS_CONF] = (
            '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
            "<!-- Fonts bundled by scenerender-vpkg. Use: FONTCONFIG_FILE=_vpkg/fonts.conf (vpkg run sets it). -->\n"
            "<fontconfig>\n  <include ignore_missing=\"yes\">/etc/fonts/fonts.conf</include>\n"
            "  <dir prefix=\"relative\">fonts</dir>\n</fontconfig>\n").encode()
        p.roles[FONTS_CONF] = "config"


def _credits_from_asset_manifest(p: Plan) -> None:
    """License and credit per file from the scene's own attributes and a project assets.manifest.json."""
    for scene_rel, scan in p.scans.items():
        for abs_path, rs in scan.inputs.items():
            if not _inside(abs_path, p.project):
                continue
            rel = _rel(abs_path, p.project)
            for r in rs:
                attrs = _element_attrs(r)
                for key in ("license", "credit"):
                    if attrs.get(key):
                        p.credits.setdefault(rel, {})[key] = attrs[key]
    path = os.path.join(p.project, "assets.manifest.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    for a in data.get("assets", []) if isinstance(data, dict) else []:
        if isinstance(a, dict) and isinstance(a.get("path"), str) and a["path"] in p.files:
            c = p.credits.setdefault(a["path"], {})
            if a.get("license"):
                c.setdefault("license", str(a["license"]))
            credit = a.get("attribution") or a.get("credit")
            if credit:
                c.setdefault("credit", str(credit))


_ATTR_CACHE: dict = {}


def _element_attrs(ref) -> dict:
    """Attributes of the element that made a reference (license, credit live beside src)."""
    key = ref.source
    if key not in _ATTR_CACHE:
        import xml.etree.ElementTree as ET
        table = {}
        try:
            for el in ET.parse(ref.source).getroot().iter():
                for attr in ("src", "environment", "fontFile", "proxy"):
                    if el.get(attr):
                        table[(refs.local(el.tag), attr, el.get(attr))] = dict(el.attrib)
        except (OSError, ET.ParseError):
            pass
        _ATTR_CACHE[key] = table
    return _ATTR_CACHE[key].get((ref.element, ref.attr, ref.value), {})


def _rewrite_scenes(p: Plan, external: dict) -> None:
    for scene_rel, scan in p.scans.items():
        path = p.files[scene_rel]
        with open(path, "rb") as f:
            data = f.read()
        text, changes = data.decode("utf-8"), []
        scene_dir = os.path.dirname(scene_rel)
        seen = set()
        for abs_path, rs in scan.inputs.items():
            if abs_path not in external:
                continue
            for r in rs:
                if r.source != path or (r.attr, r.value) in seen:
                    continue
                seen.add((r.attr, r.value))
                new = os.path.relpath(external[abs_path], scene_dir or ".").replace(os.sep, "/")
                rx = re.compile(r"(\b%s\s*=\s*)([\"'])%s\2" % (re.escape(r.attr), re.escape(escape(r.value))))
                text, n = rx.subn(lambda m: m.group(1) + quoteattr(new), text)
                if n:
                    changes.append(f"{r.attr}=\"{r.value}\" -> \"{new}\" ({n}x)")
        decls = []
        for requested, res in p.font_plan.get(scene_rel, []):
            for face in res.faces:
                decls.append((res.family, face))
            if requested != res.family:
                decls.append((requested, fontlib.closest(res.faces, res.weight, res.italic)))
        if decls:
            used = set(re.findall(r'\bid="(vpkg-font-[^"]*)"', text))
            lines = []
            for family, face in decls:
                rel = p.font_files[(face.path, face.index)]
                slug = re.sub(r"[^a-z0-9]+", "-", f"{family}-{face.weight}-{face.style}".lower()).strip("-")
                fid, n = f"vpkg-font-{slug}", 2
                while fid in used:
                    fid, n = f"vpkg-font-{slug}-{n}", n + 1
                used.add(fid)
                src = os.path.relpath(rel, scene_dir or ".").replace(os.sep, "/")
                extra = f' collectionIndex="{face.index}"' if face.index else ""
                lines.append(f'<font id="{fid}" src={quoteattr(src)} family={quoteattr(family)} '
                             f'weight="{face.weight}" fontStyle="{face.style}"{extra}/>')
            block = "\n".join(lines)
            m = re.search(r"<assets(\s[^>]*)?/>", text)
            if m:
                text = text[:m.start()] + f"<assets{m.group(1) or ''}>\n{block}\n</assets>" + text[m.end():]
            else:
                m = re.search(r"<assets(\s[^>]*)?>", text)
                if m:
                    text = text[:m.end()] + "\n<!-- fonts bundled by scenerender-vpkg -->\n" + block + text[m.end():]
                else:
                    m = re.search(r"<(%s)[\s>/]" % "|".join(ROOT_SEQUENCE), text)
                    if not m:
                        p.errors.append(f"{scene_rel}: cannot find where to declare bundled fonts")
                        continue
                    text = text[:m.start()] + f"<assets>\n{block}\n</assets>\n" + text[m.start():]
            changes.append(f"declared {len(lines)} bundled font face(s): "
                           + ", ".join(sorted({f for f, _ in decls})))
        if changes:
            p.content[scene_rel] = text.encode("utf-8")
            p.rewrites.append({"path": scene_rel, "changes": changes})


# ----------------------------------------------------------------------------------------------- manifest

def build_manifest(p: Plan, date: str | None = None) -> dict:
    spec = json.loads(json.dumps(p.spec))
    scan = p.scans.get(mf.primary(spec)["path"])
    if scan is not None:
        pr = scan.project

        def num(key, cast):
            try:
                return cast(pr[key])
            except (KeyError, ValueError):
                return None
        video = {"scene": mf.primary(spec)["path"], "schemaVersion": "1.1", "width": num("width", int),
                 "height": num("height", int), "fps": pr.get("fps"), "duration": num("duration", float),
                 "audio": scan.audio, "captions": sorted({c for c in scan.captions if c}),
                 "outputs": [{"id": i, **({"path": o} if o else {})} for i, o in scan.output_ids]}
        spec["video"] = {k: v for k, v in video.items() if v is not None}
    files = []
    for rel in sorted(set(p.files) | set(p.content)):
        if rel in p.content:
            data = p.content[rel]
            size, digest = len(data), hashlib.sha256(data).hexdigest()
        else:
            size, digest = os.path.getsize(p.files[rel]), sha256_file(p.files[rel])
        entry = {"path": rel, "size": size, "sha256": digest, "role": p.roles.get(rel, "other")}
        mt = mimetypes.guess_type(rel)[0]
        if mt:
            entry["mediaType"] = mt
        if rel in p.referenced_by:
            entry["referencedBy"] = sorted(p.referenced_by[rel])
        if rel in p.content and rel in p.files:
            entry["originalSha256"] = sha256_file(p.files[rel])
        entry.update({k: v for k, v in p.credits.get(rel, {}).items() if k in ("license", "credit")})
        files.append(entry)
    spec["files"] = files
    if p.fonts:
        spec["fonts"] = p.fonts
    if p.external:
        spec["external"] = p.external
    if p.rewrites:
        spec["rewrites"] = p.rewrites
    spec["packed"] = {"tool": "scenerender-vpkg", "toolVersion": __version__, "projectDir": os.path.basename(p.project),
                      "fileCount": len(files), "totalSize": sum(f["size"] for f in files)}
    if date:
        spec["packed"]["at"] = date
    return spec


# ----------------------------------------------------------------------------------------------- zip

def _date_time():
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        t = time.gmtime(max(int(epoch), 315532800))
        return t[:6], time.strftime("%Y-%m-%dT%H:%M:%SZ", t)
    return (1980, 1, 1, 0, 0, 0), None


def _info(name: str, dt, executable: bool = False) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, dt)
    zi.external_attr = (0o100755 if executable else 0o100644) << 16
    zi.create_system = 3
    zi.compress_type = zipfile.ZIP_STORED if name.lower().endswith(STORED) else zipfile.ZIP_DEFLATED
    return zi


def write_zip(p: Plan, out: str, date: str | None = None) -> dict:
    dt, epoch_date = _date_time()
    m = build_manifest(p, date or epoch_date)
    tmp = out + ".part"
    with zipfile.ZipFile(tmp, "w", allowZip64=True) as z:
        z.writestr(_info(mf.MANIFEST, dt), mf.dump(m).encode())
        with open(mf.SCHEMA_PATH, "rb") as f:
            z.writestr(_info(mf.SCHEMA_NAME, dt), f.read())
        z.writestr(_info("README.md", dt), readme(m).encode())
        for entry in m["files"]:
            rel = entry["path"]
            name = "project/" + rel
            if rel in p.content:
                z.writestr(_info(name, dt), p.content[rel], compresslevel=6)
                continue
            src = p.files[rel]
            zi = _info(name, dt, os.access(src, os.X_OK) and p.roles.get(rel) == "script")
            with open(src, "rb") as f, z.open(zi, "w", force_zip64=entry["size"] > (1 << 31)) as w:
                shutil.copyfileobj(f, w, 1 << 20)
    os.replace(tmp, out)
    return m


def pack(project: str, out: str | None = None, **options) -> tuple:
    """Plan and write the package; returns (zip path, manifest, plan)."""
    date = options.pop("date", None)
    p = plan(project, **options)
    pkg = p.spec["package"]
    out = out or os.path.join(os.getcwd(), f"{pkg['id']}-{pkg['version']}.vpkg.zip")
    if os.path.isdir(out):
        out = os.path.join(out, f"{pkg['id']}-{pkg['version']}.vpkg.zip")
    m = write_zip(p, out, date)
    return out, m, p


def summary(m: dict) -> dict:
    by_role: dict = {}
    for f in m.get("files", []):
        n, s = by_role.get(f["role"], (0, 0))
        by_role[f["role"]] = (n + 1, s + f["size"])
    return by_role


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)

