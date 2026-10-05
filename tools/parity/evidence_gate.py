#!/usr/bin/env python3
"""Evidence gate: the Rust reference-clip evidence cases, run against the Python renderer.

Reuses rs-scene-render's tools/evidence.py UNCHANGED (its cases, pixel checks and
native/degraded/wrong/error/gap classification) twice: once with the Rust binary, once with a
`scene-render` shim (py_scene_render_shim.py) that serves its `render --strict --time T -o PNG`
calls from `python -m scenerender.cli still`. The report lists, per case, the Rust class
(from its own run, not the manifest) beside the Python class.

    python tools/parity/evidence_gate.py --rs-root /path/to/rs-scene-render --out OUT_DIR \
        [--tree DIR] [--case ID]... [--md docs/parity/EVIDENCE-BASELINE.md --json ...]

Run it with an interpreter that has numpy and Pillow (evidence.py needs them). --tree is the
scenerender checkout under test (default: this repo). --extra-path DIR is put on PATH (ffprobe,
which the Rust engine needs for video probing).

Verdict per case: SAME (equal classes), PY-BETTER / PY-WORSE (rank native > degraded > wrong >
error > gap), so a regression shows up as PY-WORSE; HOST-GATED when the Rust reference is a missing-tool fallback. Exit status is 0 always: this is a
measurement; the gate for a work package is the named cases' Python class.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RANK = {"native": 0, "degraded": 1, "wrong": 2, "error": 3, "gap": 4}

# Cases whose Rust reference depends on a host tool: with the tool missing the Rust engine falls back
# (ocio: built-in looks; AVIF: FFmpeg, which misreads identity-matrix AVIF), so its class is not the
# reference. Python mirrors Rust WITH its full toolchain (OCIO applied natively, AVIF decoded losslessly),
# never the host fallback. Such cases are HOST-GATED: they count neither for nor against Python.
HOST_TOOLS = {
    "ocio-pipeline": ("ociobakelut", "Rust falls back to built-in looks without ociobakelut (sr-gpu/src/ocio.rs:149)"),
    "still-formats": ("heif-convert", "Rust falls back to FFmpeg, which misreads identity-matrix AVIF, silently (sr-media/src/still.rs:85)"),
}


def host_gate(case_id: str, env: dict) -> str | None:
    """Why the Rust reference for this case is a host-tool fallback on this machine, or None."""
    tool = HOST_TOOLS.get(case_id)
    if tool and not shutil.which(tool[0], path=env.get("PATH")):
        return f"`{tool[0]}` not found on this host: {tool[1]}"
    return None


def write_shim(path: Path, tree: Path, python: str) -> None:
    path.write_text(f"#!/bin/sh\nexport SCENERENDER_TREE={tree} SCENERENDER_PYTHON={python}\n"
                    f'exec {python} {HERE / "py_scene_render_shim.py"} "$@"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_evidence(rs_root: Path, binary: str, out: Path, cases: list[str], env: dict) -> dict:
    cmd = [sys.executable, str(rs_root / "tools" / "evidence.py"), str(out), "--bin", binary]
    for c in cases:
        cmd += ["--case", c]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    report = out / "report.json"
    if not report.is_file():
        raise SystemExit(f"evidence.py produced no report ({binary}):\n{p.stdout}\n{p.stderr}")
    return json.loads(report.read_text())


def failed_checks(r: dict) -> list[str]:
    return [f"{c['why']} (got {c['measured']})" for c in r.get("checks", []) if not c["passed"]]


def short(r: dict) -> str:
    bits = failed_checks(r) or r.get("notes", [])
    return "; ".join(b.strip().splitlines()[-1][:160] for b in bits[:2])


def verdict(rs: str, py: str, gated: str | None = None) -> str:
    if gated:
        return "HOST-GATED"
    return "SAME" if rs == py else ("PY-BETTER" if RANK[py] < RANK[rs] else "PY-WORSE")


def markdown(rep: dict) -> str:
    L = ["# Evidence baseline: Python against Rust", "",
         f"{rep['date']} · Rust `{rep['rust']['renderer_version']}` commit `{rep['rust']['commit'][:12]}` · "
         f"Python tree `{rep['python_tree_commit'][:12]}`", "",
         "The Rust `tools/evidence.py` cases and pixel checks, run unchanged against both renderers "
         "(Python through `py_scene_render_shim.py`; `degraded` = the Python renderer logged a warning). "
         "Regenerate with `tools/parity/evidence_gate.py`.", "",
         "| Rust \\ Python | " + " | ".join(RANK) + " |", "|---|" + "---|" * len(RANK)]
    for rc in RANK:
        L.append(f"| **{rc}** | " + " | ".join(str(rep["matrix"][rc][pc]) for pc in RANK) + " |")
    L += ["", "| Case | Reference | Rust | Python | Verdict | Python evidence |", "|---|---|---|---|---|---|"]
    for c in rep["cases"]:
        L.append(f"| `{c['id']}` | {c['reference']} | {c['rust']} | **{c['python']}** | {c['verdict']} | "
                 + c["python_evidence"].replace("|", "\\|") + " |")
    s = rep["summary"]
    L += ["", f"Python native on {s['py_native']} of {s['n']} gated cases ({s['same']} same class as Rust, "
          f"{s['py_worse']} worse, {s['py_better']} better)."]
    gated = [c for c in rep["cases"] if c["verdict"] == "HOST-GATED"]
    if gated:
        L += ["", "**Host-gated cases** (excluded from the counts and the matrix above): the Rust reference class "
              "on this host is a fallback for a missing tool, not what the engine does with its full toolchain. "
              "Python mirrors Rust with the full toolchain (OCIO applied natively, AVIF decoded losslessly to the "
              "source RGB), never the fallback, so these cases count neither for nor against it.", ""]
        L += [f"- `{c['id']}`: Rust {c['rust']}, Python {c['python']}. {c['host_gate']}" for c in gated]
    return "\n".join(L) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rs-root", required=True, type=Path)
    ap.add_argument("--rs-bin", type=Path, help="default: RS_ROOT/target/release/scene-render")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--tree", type=Path, default=HERE.parent.parent)
    ap.add_argument("--python", default=sys.executable, help="interpreter running scenerender")
    ap.add_argument("--extra-path", action="append", default=[])
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument("--md", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    rs_bin = a.rs_bin or a.rs_root / "target" / "release" / "scene-render"
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([*a.extra_path, env["PATH"]])
    shim = out / "scene-render"
    write_shim(shim, a.tree.resolve(), a.python)
    rs = run_evidence(a.rs_root, str(rs_bin), out / "rust", a.case, env)
    py = run_evidence(a.rs_root, str(shim), out / "python", a.case, env)
    by_py = {r["id"]: r for r in py["results"]}
    cases, matrix = [], {r: {p: 0 for p in RANK} for r in RANK}
    for r in rs["results"]:
        p = by_py[r["id"]]
        gate = host_gate(r["id"], env)
        if not gate:
            matrix[r["outcome"]][p["outcome"]] += 1
        cases.append({"id": r["id"], "reference": r["reference"], "technique": r["technique"],
                      "manifest_expect": r["expect"], "rust": r["outcome"], "python": p["outcome"],
                      "verdict": verdict(r["outcome"], p["outcome"], gate),
                      "host_gate": gate,
                      "rust_evidence": short(r), "python_evidence": short(p),
                      "python_failed_checks": failed_checks(p)})
    vc = [c["verdict"] for c in cases if c["verdict"] != "HOST-GATED"]
    n = len(vc)
    gated = [c for c in cases if c["verdict"] == "HOST-GATED"]
    rep = {"date": time.strftime("%Y-%m-%d"),
           "rust": {k: rs[k] for k in ("renderer_version", "commit", "binary_sha256", "counts")},
           "python_tree_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=a.tree, capture_output=True,
                                                text=True).stdout.strip(),
           "python_counts": py["counts"], "matrix": matrix,
           "summary": {"n": n, "host_gated": [c["id"] for c in gated],
                       "py_native": sum(c["python"] == "native" for c in cases if c["verdict"] != "HOST-GATED"), "same": vc.count("SAME"),
                       "py_worse": vc.count("PY-WORSE"), "py_better": vc.count("PY-BETTER")},
           "cases": cases}
    (a.json or out / "evidence-baseline.json").write_text(json.dumps(rep, indent=2) + "\n")
    (a.md or out / "EVIDENCE-BASELINE.md").write_text(markdown(rep))
    print(f"rust {rs['counts']}\npython {py['counts']}\n" + "\n".join(
        f"{c['verdict']:9} {c['id']:24} rust={c['rust']:8} py={c['python']}" for c in cases))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
