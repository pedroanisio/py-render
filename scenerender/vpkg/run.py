"""Run a package's pipeline in an unpacked package or a source project.

Steps run in dependency order from the project directory (or the step's `cwd`). A step is skipped when its
outputs exist and are newer than its inputs (--force re-runs); a step marked `idempotent: false` runs only
when named with --only. `python`/`python3` as the first word runs the interpreter given by --python (default:
this one). `requires` lists executables that must be on PATH; a missing one fails the step, or skips it when
the step is `optional` (its outputs are already in the package).

Render steps name a scene, not an engine: the engine is chosen here (--engine), and the scene's variant made
for that engine (scenes[].targets + derivedFrom) is used when there is one. Engine commands come from
ENGINES, overridden per id by ~/.config/scene-vpkg/engines.json ({"py": {"command": [...], "args": [...],
"env": {...}}}) or by VPKG_ENGINE_<ID> (a command prefix, e.g. "node ~/src/js-render-engine/bin/scene-render.js").
When _vpkg/fonts.conf exists, FONTCONFIG_FILE points at it (unless already set), so fontconfig-based engines
and scripts find the bundled fonts.
"""
from __future__ import annotations

import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
from fractions import Fraction

from . import manifest as mf, refs
from .archive import locate

# {scene} {to} {output} {time} {frame} are substituted; None = the engine cannot do it.
ENGINES = {
    "py": {"command": ["scenerender"], "to": ["render", "{scene}", "-o", "{to}"],
           "output": ["render", "{scene}", "--output", "{output}"], "all": ["render", "{scene}"],
           "still": ["still", "{scene}", "-t", "{time}", "-o", "{to}"]},
    "rs": {"command": ["scene-render-rs"], "to": ["encode", "{scene}", "-o", "{to}"],
           "output": ["encode", "{scene}", "--output", "{output}"], "all": ["encode", "{scene}"],
           "still": ["render", "{scene}", "--time", "{time}", "-o", "{to}"]},
    "c": {"command": ["scene-render-c"], "to": ["--scene", "{scene}", "--output", "{to}"], "output": None,
          "all": ["--scene", "{scene}"], "still": ["--scene", "{scene}", "--frame", "{frame}", "--output", "{to}"]},
    "js": {"command": ["scene-render-js"], "to": None, "output": ["render", "{scene}", "--output", "{output}"],
           "all": ["render", "{scene}"], "still": None},
}


class StepError(RuntimeError):
    pass


def engine_config(engine: str) -> dict:
    cfg = dict(ENGINES.get(engine, {"command": [engine], "to": None, "output": None, "all": None, "still": None}))
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, "scene-vpkg", "engines.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f).get(engine, {}))
    except (OSError, ValueError):
        pass
    env = os.environ.get(f"VPKG_ENGINE_{engine.upper().replace('-', '_')}")
    if env:
        cfg["command"] = [os.path.expanduser(x) for x in shlex.split(env)]
    cfg["command"] = [os.path.expanduser(x) for x in cfg["command"]]
    return cfg


def _fill(template: list, **values) -> list:
    return [x.format(**values) for x in template]


def _expand(project: str, patterns) -> list:
    out = []
    for p in patterns:
        p = p.replace("{time}", "*").replace("{frame}", "*")
        hits = glob.glob(os.path.join(project, p), recursive=True)
        out += hits if hits else [os.path.join(project, p)]
    return out


def render_commands(m: dict, project: str, step: dict, engine: str) -> list:
    """[(argv, env, move)] for a render step; move is (from, to) when the engine cannot write to `to`."""
    r, cfg = step["render"], engine_config(engine)
    scene = mf.scene_for(m, r["scene"], engine)
    extra = r.get("args", {}).get(engine, []) + cfg.get("args", [])
    env = cfg.get("env", {})
    out = []
    if r.get("stills"):
        if not cfg.get("still"):
            raise StepError(f"engine {engine!r} has no still command")
        fps = Fraction((refs.scan(os.path.join(project, scene)).project.get("fps") or "30"))
        for t in r["stills"]:
            to = r.get("to", "stills/{time}.png").format(time=f"{t:g}", frame=round(t * fps))
            argv = cfg["command"] + _fill(cfg["still"], scene=scene, to=to, time=f"{t:g}", frame=round(t * fps),
                                          output=r.get("output", ""))
            out.append((argv + extra, env, None, to))
        return out
    if r.get("to") and cfg.get("to"):
        argv = cfg["command"] + _fill(cfg["to"], scene=scene, to=r["to"], output=r.get("output", ""))
        return [(argv + extra, env, None, r["to"])]
    if r.get("output") and cfg.get("output"):
        argv = cfg["command"] + _fill(cfg["output"], scene=scene, output=r["output"])
    elif cfg.get("all"):
        argv = cfg["command"] + _fill(cfg["all"], scene=scene)
    else:
        raise StepError(f"engine {engine!r} cannot render {scene}")
    move = None
    if r.get("to"):
        ids = dict(refs.scan(os.path.join(project, scene)).output_ids)
        src = ids.get(r.get("output")) or next(iter(ids.values()), None)
        if not src:
            raise StepError(f"{scene}: no <output> to render to {r['to']}")
        move = (os.path.join(os.path.dirname(scene), src), r["to"])
    return [(argv + extra, env, move, r.get("to"))]


def run(path: str, engine: str | None = None, only=(), start: str | None = None, until: str | None = None,
        force: bool = False, dry: bool = False, python: str = sys.executable, log=print) -> int:
    manifest_path, project = locate(path)
    m = mf.load(manifest_path)
    steps = mf.order(m.get("pipeline", {}).get("steps", []))
    ids = [s["id"] for s in steps]
    for name in list(only) + [x for x in (start, until) if x]:
        if name not in ids:
            raise StepError(f"unknown step {name!r} (steps: {', '.join(ids)})")
    if start:
        steps = steps[ids.index(start):]
    if until:
        steps = steps[:[s["id"] for s in steps].index(until) + 1]
    if only:
        steps = [s for s in steps if s["id"] in only]
    env = dict(os.environ)
    env["VPKG_PROJECT"] = project
    conf = os.path.join(project, "_vpkg", "fonts.conf")
    if os.path.isfile(conf) and "FONTCONFIG_FILE" not in os.environ:
        env["FONTCONFIG_FILE"] = conf
    failed = 0
    for s in steps:
        label = f"[{s['id']}]"
        cwd = os.path.join(project, s.get("cwd", ""))
        outputs = list(s.get("outputs", []))
        if s["kind"] == "render" and s["render"].get("to"):
            outputs.append(s["render"]["to"])
        out_files = _expand(project, outputs) if outputs else []
        have = bool(out_files) and all(os.path.exists(f) for f in out_files)
        if have and not force and s["id"] not in only:
            if s.get("idempotent") is False:
                log(f"{label} skipped: not idempotent and its outputs exist (run with --only {s['id']} to redo)")
                continue
            ins = [f for f in _expand(project, s.get("inputs", [])) if os.path.exists(f)]
            if not ins or max(map(os.path.getmtime, ins)) <= min(map(os.path.getmtime, out_files)):
                log(f"{label} up to date")
                continue
        missing = [t for t in s.get("requires", []) if not shutil.which(t)]
        if missing:
            msg = f"{label} needs {', '.join(missing)} on PATH"
            if s.get("optional"):
                log(msg + "; skipped (optional)")
                continue
            log(msg)
            return 1
        if s["kind"] == "render":
            if not engine:
                log(f"{label} a render step: choose an engine with --engine (py, rs, c, js)")
                return 2
            commands = render_commands(m, project, s, engine)
        else:
            argv = list(s["run"])
            if argv[0] in ("python", "python3"):
                argv[0] = python
            commands = [(argv, {}, None, None)]
        for argv, extra_env, move, to in commands:
            log(f"{label} $ {' '.join(shlex.quote(a) for a in argv)}" + (f"   (in {s['cwd']})" if s.get("cwd") else ""))
            if dry:
                continue
            if to:
                os.makedirs(os.path.dirname(os.path.join(project, to)) or project, exist_ok=True)
            code = subprocess.run(argv, cwd=cwd, env={**env, **s.get("env", {}), **extra_env}).returncode
            if code != 0:
                log(f"{label} failed with exit status {code}")
                if s.get("optional"):
                    break
                return code or 1
            if move and os.path.abspath(os.path.join(project, move[0])) != os.path.abspath(os.path.join(project, move[1])):
                os.makedirs(os.path.dirname(os.path.join(project, move[1])) or project, exist_ok=True)
                shutil.copy2(os.path.join(project, move[0]), os.path.join(project, move[1]))
    return failed
