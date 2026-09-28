"""scenerender.vpkg: scene video packages -- references, fonts, lint, pack, verify, unpack/fetch, run.

Offline and engine-free: fonts are minimal sfnt files built here, downloads use file:// URLs, and the render
engine is a stand-in script selected through VPKG_ENGINE_<ID>.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import zipfile

import pytest

from scenerender.vpkg import archive, fonts, lint, manifest as mf, refs
from scenerender.vpkg.cli import main
from scenerender.vpkg.init import init, piper_url
from scenerender.vpkg.pack import PackError, matches, pack, plan
from scenerender.vpkg.run import render_commands, run


# ------------------------------------------------------------------------------------------------ fixtures

def sfnt(family: str, sub: str = "Regular", weight: int = 400, italic: bool = False) -> bytes:
    """A font file with just the tables vpkg reads: name (Windows UTF-16BE) and OS/2."""
    names = [(1, family), (2, sub), (0, f"(c) {family} authors"), (13, "SIL Open Font License 1.1")]
    strings, records, off = b"", b"", 0
    for nid, text in names:
        data = text.encode("utf-16-be")
        records += struct.pack(">6H", 3, 1, 0x409, nid, len(data), off)
        strings += data
        off += len(data)
    name = struct.pack(">3H", 0, len(names), 6 + 12 * len(names)) + records + strings
    os2 = bytearray(96)
    struct.pack_into(">H", os2, 4, weight)
    struct.pack_into(">H", os2, 62, 1 if italic else 0x40)
    tables = [(b"OS/2", bytes(os2)), (b"name", name)]
    head = struct.pack(">IHHHH", 0x00010000, len(tables), 0, 0, 0)
    offset, directory, body = 12 + 16 * len(tables), b"", b""
    for tag, data in tables:
        directory += struct.pack(">4sIII", tag, 0, offset + len(body), len(data))
        body += data + b"\0" * (-len(data) % 4)
    return head + directory + body


SCENE = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="320" height="180" fps="30" duration="2"/>
  <styles><textStyle id="t" font="Test Sans Bold" fallback="sans-serif"/></styles>
  <output id="main" path="renders/out.mp4" codec="h264"/>
  <assets>
    <image id="bg" src="assets/bg.png" license="CC0" credit="Test Author"/>
    <image id="shared" src="../shared/logo.png"/>
    <imageSequence id="seq" src="assets/seq/f_%02d.png"/>
    <mesh id="m" src="assets/model.gltf"/>
    <audio id="vo" src="assets/vo.wav"/>
    <effect id="fx" type="shader" src="data:text/plain;base64,AAAA"/>
  </assets>
  <composition id="c"><text id="x" style="t" text="hi"/></composition>
</scene>
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project with a sibling folder it reads from, a fetch file, a font and a stand-in engine."""
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790553600")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "videos"
    p = root / "proj"
    for d in ("assets/seq", "renders", "fonts", "sources/voice", "__pycache__"):
        (p / d).mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "shared" / "logo.png").write_bytes(b"logo")
    (p / "scene.xml").write_text(SCENE)
    (p / "scene.rust.xml").write_text(SCENE.replace('<output id="main"', '<output id="rs-main"'))
    (p / "scene.pre-3d.xml").write_text(SCENE)
    (p / "assets" / "bg.png").write_bytes(b"bg")
    for i in (1, 2, 3):
        (p / "assets" / "seq" / f"f_{i:02d}.png").write_bytes(b"%d" % i)
    (p / "assets" / "model.gltf").write_text(json.dumps({"buffers": [{"uri": "model.bin"}],
                                                        "images": [{"uri": "tex/a.png"}, {"uri": "data:image/png;base64,AA"}]}))
    (p / "assets" / "model.bin").write_bytes(b"\0" * 16)
    (p / "assets" / "tex").mkdir()
    (p / "assets" / "tex" / "a.png").write_bytes(b"tex")
    (p / "assets" / "unused.png").write_bytes(b"unused")
    (p / "fonts" / "TestSans-Regular.ttf").write_bytes(sfnt("Test Sans"))
    (p / "fonts" / "TestSans-Bold.ttf").write_bytes(sfnt("Test Sans", "Bold", 700))
    (p / "make_audio.py").write_text("import os\nopen(os.path.join('assets', 'vo.wav'), 'wb').write(b'vo')\n")
    (p / "renders" / "old.mp4").write_bytes(b"old")
    (p / "__pycache__" / "x.pyc").write_bytes(b"x")
    voice = b"voice-model"
    (p / "sources" / "voice" / "v.onnx").write_bytes(voice)
    engine = tmp_path / "engine.py"
    engine.write_text("import sys, os\nargs = sys.argv[1:]\nout = args[args.index('-o') + 1]\n"
                      "os.makedirs(os.path.dirname(out) or '.', exist_ok=True)\n"
                      "open(out, 'w').write(' '.join(args))\n")
    monkeypatch.setenv("VPKG_ENGINE_PY", f"{sys.executable} {engine}")
    spec = {
        "format": mf.FORMAT, "formatVersion": "1.0",
        "package": {"id": "proj", "version": "1.2.3", "title": "Test video", "languages": ["en"]},
        "scenes": [{"path": "scene.xml", "role": "primary"},
                   {"path": "scene.rust.xml", "role": "variant", "targets": ["rs"], "derivedFrom": "scene.xml"},
                   {"path": "scene.pre-3d.xml", "role": "archive"}],
        "fetch": [{"path": "sources/voice/v.onnx", "url": (p / "sources" / "voice" / "v.onnx").as_uri(),
                   "sha256": hashlib.sha256(voice).hexdigest(), "size": len(voice)}],
        "pipeline": {"steps": [
            {"id": "audio", "kind": "audio", "run": ["python3", "make_audio.py"], "inputs": ["make_audio.py"],
             "outputs": ["assets/vo.wav"]},
            {"id": "render", "kind": "render", "needs": ["audio"],
             "render": {"scene": "scene.xml", "output": "main", "to": "renders/out.mp4"}}]},
    }
    (p / "vpkg.json").write_text(json.dumps(spec))
    return p


def _pack(project, tmp_path, **kw):
    return pack(str(project), str(tmp_path / "out.vpkg.zip"), use_system_fonts=False, **kw)


# ------------------------------------------------------------------------------------------------ units

def test_globs_follow_gitignore_rules():
    assert matches("a/b/c.py", ["*.py"]) and matches("c.py", ["/*.py"]) and not matches("a/c.py", ["/*.py"])
    assert matches("renders/x/y.mp4", ["renders/"]) and not matches("assets/renders.png", ["renders/"])
    assert matches("models/a/license.txt", ["models/*/license.txt"]) and matches("x/y/z.md", ["**/*.md"])


def test_scan_classifies_references_and_follows_dependencies(project):
    s = refs.scan(str(project / "scene.xml"))
    rels = {os.path.relpath(k, project) for k in s.inputs}
    assert {"assets/bg.png", "assets/model.gltf", "assets/model.bin", "assets/tex/a.png",
            "assets/seq/f_01.png", "assets/seq/f_03.png", "../shared/logo.png"} <= rels
    assert [os.path.relpath(r.path, project) for r in s.missing] == ["assets/vo.wav"]
    assert [os.path.relpath(r.path, project) for r in s.outputs] == ["renders/out.mp4"]
    assert s.output_ids == [("main", "renders/out.mp4")] and s.audio
    assert set(s.families) == {"Test Sans Bold", "sans-serif"}
    assert s.project["width"] == "320"


def test_font_reader_and_style_stripping(tmp_path):
    (tmp_path / "a.ttf").write_bytes(sfnt("Test Sans"))
    (tmp_path / "b.ttf").write_bytes(sfnt("Test Sans", "Bold Italic", 700, True))
    face = fonts.read_faces(str(tmp_path / "b.ttf"))[0]
    assert (face.family, face.weight, face.italic, face.license) == ("Test Sans", 700, True, "SIL Open Font License 1.1")
    ix = fonts.FontIndex([str(tmp_path / "a.ttf"), str(tmp_path / "b.ttf")], use_system=False)
    r = ix.resolve("Test Sans Bold Italic")
    assert r.family == "Test Sans" and r.weight == 700 and r.italic and len(r.faces) == 2
    assert fonts.closest(r.faces, r.weight, r.italic).path.endswith("b.ttf")
    assert not ix.resolve("Missing Family").found
    assert fonts.read_faces(str(tmp_path)) == []


def test_lint_flags_machine_paths(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    (p / "s.py").write_text('#!/usr/bin/env python3\nA = "/home/me/x.onnx"\nB = os.path.join(HERE, "..", "other")\n'
                            'C = "/tmp/x.wav"\nD = "/home/me/ok"  # vpkg: allow\nE = "../other/f.png"\nF = "sub/../g"\n')
    found = {(f.line, f.rule, f.severity) for f in lint.lint_file(str(p / "s.py"), str(p))}
    assert found == {(2, "absolute-path", "error"), (3, "outside-project", "error"), (4, "temp-path", "warning"),
                     (6, "outside-project", "error")}


def test_manifest_semantics():
    m = {"format": mf.FORMAT, "formatVersion": "1.0", "package": {"id": "x", "version": "1.0.0", "title": "X"},
         "scenes": [{"path": "a.xml", "role": "primary"}, {"path": "b.xml", "role": "primary"}],
         "pipeline": {"steps": [{"id": "a", "kind": "command", "run": ["x"], "needs": ["b"]},
                                {"id": "b", "kind": "command", "run": ["y"], "needs": ["a"]},
                                {"id": "c", "kind": "render"}]}}
    errors = mf.validate(m)
    assert any("exactly one" in e for e in errors)
    assert any("cycle" in e for e in errors)
    assert any("render' block" in e for e in errors)
    assert mf.schema_errors({**m, "bogus": 1})[-1].endswith("unknown key 'bogus'")
    assert mf.schema_errors({**m, "scenes": [{"path": "../x.xml", "role": "primary"}]})


def test_init_detects_roles_voices_and_packages(project):
    (project / "vpkg.json").unlink()
    spec = init(str(project))
    roles = {s["path"]: s for s in spec["scenes"]}
    assert roles["scene.xml"]["role"] == "primary"
    assert roles["scene.rust.xml"]["targets"] == ["rs"] and roles["scene.rust.xml"]["derivedFrom"] == "scene.xml"
    assert roles["scene.pre-3d.xml"]["role"] == "archive"
    assert not mf.validate(spec)
    assert piper_url("pt_BR-faber-medium.onnx") == ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
                                                    "pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx")


# ------------------------------------------------------------------------------------------------ pack

def test_pack_bundles_rewrites_and_is_reproducible(project, tmp_path):
    out, m, p = _pack(project, tmp_path)
    paths = {f["path"]: f for f in m["files"]}
    assert "_external/shared/logo.png" in paths and "assets/model.bin" in paths and "assets/seq/f_02.png" in paths
    assert "make_audio.py" in paths and "fonts/TestSans-Bold.ttf" in paths
    for left in ("renders/old.mp4", "assets/unused.png", "sources/voice/v.onnx", "scene.pre-3d.xml", "vpkg.json"):
        assert left not in paths
    assert paths["assets/bg.png"]["license"] == "CC0" and paths["assets/bg.png"]["credit"] == "Test Author"
    assert paths["scene.xml"]["originalSha256"] != paths["scene.xml"]["sha256"]
    assert m["video"] == {"scene": "scene.xml", "schemaVersion": "1.1", "width": 320, "height": 180, "fps": "30",
                          "duration": 2.0, "audio": True, "captions": [], "outputs": [{"id": "main", "path": "renders/out.mp4"}]}
    assert m["packed"]["at"] == "2026-09-28T00:00:00Z" and not mf.validate(m)
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[:3] == ["vpkg.json", "vpkg.schema.json", "README.md"]
        scene = z.read("project/scene.xml").decode()
    assert 'src="_external/shared/logo.png"' in scene
    assert 'family="Test Sans" weight="700"' in scene and 'family="Test Sans Bold" weight="700"' in scene
    assert {e["original"] for e in m["external"]} == {"../shared/logo.png"}
    first = open(out, "rb").read()
    out2, _, _ = pack(str(project), str(tmp_path / "again.vpkg.zip"), use_system_fonts=False)
    assert open(out2, "rb").read() == first


def test_pack_refuses_nonportable_projects(project, tmp_path):
    (project / "bad.py").write_text('VOICE = "/home/someone/voice.onnx"\n')
    (project / "scene.xml").write_text(SCENE.replace("assets/bg.png", "assets/missing.png")
                                       .replace("Test Sans Bold", "Nowhere Grotesk"))
    with pytest.raises(PackError) as e:
        _pack(project, tmp_path)
    text = "\n".join(e.value.errors)
    assert "bad.py:1" in text and "assets/missing.png" in text and "Nowhere Grotesk" in text
    assert "assets/vo.wav" not in text                 # a pipeline output, not missing
    (project / "scene.xml").write_text(SCENE.replace("Test Sans Bold", "Nowhere Grotesk"))
    p = plan(str(project), allow_nonportable=True, allow_missing_fonts=True, use_system_fonts=False)
    assert any("bad.py" in w for w in p.warnings) and any("Nowhere Grotesk" in w for w in p.warnings)


def test_pack_checks_local_fetch_hash(project, tmp_path):
    (project / "sources" / "voice" / "v.onnx").write_bytes(b"changed")
    with pytest.raises(PackError, match="does not match the fetch sha256"):
        _pack(project, tmp_path)


# ------------------------------------------------------------------------------------------------ read side

def test_verify_unpack_fetch_and_tampering(project, tmp_path):
    out, m, _ = _pack(project, tmp_path)
    assert archive.verify(out).ok
    rep = archive.unpack(out, str(tmp_path / "u"), fetch=True)
    assert rep.ok, rep.errors
    assert (tmp_path / "u" / "project" / "sources" / "voice" / "v.onnx").read_bytes() == b"voice-model"
    assert archive.locate(str(tmp_path / "u"))[1] == str(tmp_path / "u" / "project")
    with pytest.raises(FileExistsError):
        archive.unpack(out, str(tmp_path / "u"))

    bad = tmp_path / "bad.vpkg.zip"
    with zipfile.ZipFile(out) as src, zipfile.ZipFile(bad, "w") as dst:
        for i in src.infolist():
            data = src.read(i.filename)
            dst.writestr(i, data + b"!" if i.filename == "project/assets/bg.png" else data)
        dst.writestr("project/stray.txt", b"x")
    errors = "\n".join(archive.verify(str(bad)).errors)
    assert "assets/bg.png: size or SHA-256 differs" in errors and "stray.txt: in the zip but not in" in errors

    cut = tmp_path / "cut.vpkg.zip"
    cut.write_bytes(open(out, "rb").read()[:-200])
    assert "truncated" in archive.verify(str(cut)).errors[0]


def test_run_pipeline_with_stand_in_engine(project, tmp_path, capsys):
    out, _, _ = _pack(project, tmp_path)
    dest = tmp_path / "u"
    assert archive.unpack(out, str(dest), fetch=True).ok
    assert run(str(dest), engine="py", python=sys.executable) == 0
    proj = dest / "project"
    assert (proj / "assets" / "vo.wav").read_bytes() == b"vo"
    assert (proj / "renders" / "out.mp4").read_text().startswith("render scene.xml -o renders/out.mp4")
    log = capsys.readouterr().out
    assert run(str(dest), engine="py") == 0
    assert "[audio] up to date" in capsys.readouterr().out and "[audio] $" in log
    assert run(str(dest), force=True) == 2                    # a render step needs an engine


def test_render_commands_pick_variants_and_engine_forms(project):
    m = mf.load(str(project / "vpkg.json"))
    step = m["pipeline"]["steps"][1]
    (argv, _, move, to), = render_commands(m, str(project), step, "rs")
    assert argv[-4:] == ["encode", "scene.rust.xml", "-o", "renders/out.mp4"] and move is None
    (argv, _, move, _), = render_commands(m, str(project), step, "js")
    assert argv[-4:] == ["render", "scene.xml", "--output", "main"] and move == ("renders/out.mp4", "renders/out.mp4")
    stills = {"id": "s", "kind": "render", "render": {"scene": "scene.xml", "stills": [0.5, 1], "to": "f/{time}.png"}}
    cmds = render_commands(m, str(project), stills, "c")
    assert [c[0][-4:] for c in cmds] == [["--frame", "15", "--output", "f/0.5.png"], ["--frame", "30", "--output", "f/1.png"]]


def test_cli_round_trip(project, tmp_path, capsys):
    out = str(tmp_path / "cli.vpkg.zip")
    assert main(["pack", str(project), "-o", out, "--no-system-fonts"]) == 0
    assert main(["verify", out]) == 0
    assert main(["info", out]) == 0
    assert "Test video  (proj 1.2.3)" in capsys.readouterr().out
    assert main(["run", out, "--engine", "py", "--list", "--dir", str(tmp_path / "r")]) == 0
    assert "[render] $" in capsys.readouterr().out
    r = subprocess.run([sys.executable, "-m", "scenerender.vpkg", "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and "format 1.0" in r.stdout
