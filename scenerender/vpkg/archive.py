"""Reading packages: verify, unpack, fetch.

Layout of a package (zip) and of an unpacked package (directory):
    vpkg.json  vpkg.schema.json  README.md  project/<every file in vpkg.json files[]>
A source project is also accepted where a directory is expected: <dir>/vpkg.json is the spec and <dir> is
the project.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field

from . import manifest as mf, refs
from .fonts import GENERIC
from .pack import _is_generated, _step_outputs

META = {mf.MANIFEST, mf.SCHEMA_NAME, "README.md"}


@dataclass
class Report:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    manifest: dict | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def locate(path: str) -> tuple:
    """(manifest path, project dir) for an unpacked package or a source project directory."""
    path = os.path.abspath(path)
    m = os.path.join(path, mf.MANIFEST)
    if not os.path.isfile(m):
        raise FileNotFoundError(f"{path}: no {mf.MANIFEST}")
    project = os.path.join(path, "project")
    return (m, project) if os.path.isdir(project) else (m, path)


def _safe(name: str) -> bool:
    parts = name.split("/")
    return not (name.startswith("/") or "\\" in name or ".." in parts or (len(name) > 1 and name[1] == ":")
                or "" in parts[:-1])


def verify(path: str, deep: bool = True, extract_to: str | None = None) -> Report:
    """Check a .vpkg.zip: zip integrity, manifest, every file's size and SHA-256, no stray entries, and (deep)
    that every packed scene's references resolve inside the package, a fetch entry or a pipeline output."""
    rep = Report()
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        rep.errors.append(f"{path}: not a readable zip ({e}); the file may be truncated")
        return rep
    with z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if not names or names[0] != mf.MANIFEST:
            rep.warnings.append(f"{mf.MANIFEST} is not the first entry")
        if len(set(names)) != len(names):
            rep.errors.append("duplicate entries in the zip")
        for i in infos:
            if not _safe(i.filename):
                rep.errors.append(f"unsafe entry name {i.filename!r}")
            if stat.S_ISLNK(i.external_attr >> 16):
                rep.errors.append(f"{i.filename}: symbolic links are not allowed")
        if mf.MANIFEST not in names:
            rep.errors.append(f"no {mf.MANIFEST}")
            return rep
        try:
            m = json.loads(z.read(mf.MANIFEST))
        except ValueError as e:
            rep.errors.append(f"{mf.MANIFEST}: {e}")
            return rep
        rep.manifest = m
        rep.errors += mf.validate(m)
        if "files" not in m:
            rep.errors.append(f"{mf.MANIFEST}: no files[] (an authoring spec, not a packed manifest)")
            return rep
        expected = {"project/" + f["path"]: f for f in m["files"]}
        for name in names:
            if name not in expected and name not in META and not name.endswith("/"):
                rep.errors.append(f"{name}: in the zip but not in {mf.MANIFEST}")
        tmp = None
        if deep and extract_to is None:
            tmp = tempfile.TemporaryDirectory(prefix="vpkg-verify-")
            extract_to = tmp.name
        try:
            for name, f in expected.items():
                if name not in names:
                    rep.errors.append(f"{f['path']}: listed but missing from the zip")
                    continue
                h, size = hashlib.sha256(), 0
                dst = None
                if extract_to:
                    target = os.path.join(extract_to, *name.split("/"))
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    dst = open(target, "wb")
                try:
                    with z.open(name) as src:
                        for chunk in iter(lambda: src.read(1 << 20), b""):
                            h.update(chunk)
                            size += len(chunk)
                            if dst:
                                dst.write(chunk)
                except (zipfile.BadZipFile, OSError) as e:
                    rep.errors.append(f"{f['path']}: {e}")
                    continue
                finally:
                    if dst:
                        dst.close()
                if size != f["size"] or h.hexdigest() != f["sha256"]:
                    rep.errors.append(f"{f['path']}: size or SHA-256 differs from {mf.MANIFEST}")
                elif dst and z.getinfo(name).external_attr >> 16 & 0o111:
                    os.chmod(target, 0o755)
            if extract_to:
                for name in META & set(names):
                    with open(os.path.join(extract_to, name), "wb") as out:
                        out.write(z.read(name))
                if deep and not rep.errors:
                    _check_references(m, os.path.join(extract_to, "project"), rep)
        finally:
            if tmp:
                tmp.cleanup()
    return rep


def _check_references(m: dict, project: str, rep: Report) -> None:
    fetch = {f["path"] for f in m.get("fetch", [])}
    generated = _step_outputs(m)
    packed = {f["path"] for f in m["files"]}
    for s in m["scenes"]:
        if s["path"] not in packed:
            continue
        scan = refs.scan(os.path.join(project, s["path"]))
        for r in scan.missing:
            rel = os.path.relpath(r.path, project).replace(os.sep, "/")
            if rel not in fetch and not _is_generated(rel, generated):
                rep.errors.append(f"{s['path']}: <{r.element} {r.attr}=\"{r.value}\"> is not in the package")
        for abs_path in scan.inputs:
            if not os.path.abspath(abs_path).startswith(os.path.abspath(project) + os.sep):
                rep.errors.append(f"{s['path']}: reads {abs_path} outside the package")
        for r in scan.remote:
            rep.warnings.append(f"{s['path']}: remote reference {r.value}")
        missing = [f for f in scan.families if f.casefold() not in GENERIC]
        if missing:
            rep.warnings.append(f"{s['path']}: font families resolved from the host: {', '.join(sorted(missing))}")


def unpack(path: str, dest: str, fetch: bool = False, force: bool = False) -> Report:
    dest = os.path.abspath(dest)
    if os.path.exists(dest) and os.listdir(dest) and not force:
        raise FileExistsError(f"{dest} exists and is not empty (use --force)")
    os.makedirs(dest, exist_ok=True)
    rep = verify(path, deep=True, extract_to=dest)
    # one mtime for every file, so `run` sees packaged outputs as up to date whatever the extraction order
    now = time.time()
    for root, _, files in os.walk(dest):
        for name in files:
            os.utime(os.path.join(root, name), (now, now))
    if rep.ok and fetch:
        rep.errors += fetch_all(dest)
    return rep


def fetch_all(root: str, only_missing: bool = True, log=print) -> list:
    """Download every fetch entry to its path under the project; returns errors."""
    manifest_path, project = locate(root)
    m = mf.load(manifest_path, check=False)
    errors = []
    for f in m.get("fetch", []):
        target = os.path.join(project, *f["path"].split("/"))
        if os.path.isfile(target) and only_missing:
            if _sha(target) == f["sha256"]:
                continue
            log(f"{f['path']}: present but SHA-256 differs; downloading again")
        log(f"fetch {f['url']} -> {f['path']}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        part = target + ".part"
        try:
            h = hashlib.sha256()
            with urllib.request.urlopen(f["url"], timeout=60) as r, open(part, "wb") as out:
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    h.update(chunk)
                    out.write(chunk)
            if h.hexdigest() != f["sha256"]:
                errors.append(f"{f['path']}: downloaded SHA-256 {h.hexdigest()} does not match {f['sha256']}")
                os.remove(part)
                continue
            os.replace(part, target)
        except OSError as e:
            errors.append(f"{f['path']}: download failed: {e}")
            if os.path.exists(part):
                os.remove(part)
    return errors


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(path: str) -> dict:
    if os.path.isdir(path):
        return mf.load(locate(path)[0], check=False)
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read(mf.MANIFEST))

