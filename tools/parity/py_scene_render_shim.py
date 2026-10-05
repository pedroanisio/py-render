#!/usr/bin/env python3
"""A `scene-render`-compatible shim over the Python renderer, for the Rust evidence harness.

Implements the subset rs tools/evidence.py calls:

    scene-render render SCENE --strict --time T -o PNG
    scene-render --version

The scene is rendered with `python -m scenerender.cli still`. Rust's --strict fails when the
renderer reported a fallback; the equivalent here is any WARNING the Python renderer logged
(unsupported feature skipped, missing asset, approximation): the PNG is kept, the exit status
is 1 and stderr says "--strict", exactly the shape evidence.py classifies as `degraded`.
A failed load or render exits 2 without a PNG (`error`).

Environment: SCENERENDER_TREE (directory put on PYTHONPATH), SCENERENDER_PYTHON (interpreter).
"""
import os
import subprocess
import sys


def main(argv):
    if argv[:1] == ["--version"]:
        print("scenerender-py-shim")
        return 0
    if not argv or argv[0] != "render":
        print(f"error: shim implements only `render` and `--version`, got {argv[:1]}", file=sys.stderr)
        return 2
    scene, t, out = None, None, None
    it = iter(argv[1:])
    for a in it:
        if a == "--time":
            t = next(it)
        elif a == "-o":
            out = next(it)
        elif a == "--strict":
            pass
        elif not a.startswith("-") and scene is None:
            scene = a
        else:
            print(f"error: shim does not implement {a}", file=sys.stderr)
            return 2
    if not (scene and out):
        print("error: render needs SCENE and -o", file=sys.stderr)
        return 2
    env = dict(os.environ)
    tree = os.environ.get("SCENERENDER_TREE")
    if tree:
        env["PYTHONPATH"] = tree + os.pathsep + env.get("PYTHONPATH", "")
    py = os.environ.get("SCENERENDER_PYTHON", sys.executable)
    p = subprocess.run([py, "-m", "scenerender.cli", "still", scene, "-t", t or "0", "-o", out],
                       capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.abspath(scene)))
    if p.returncode != 0 or not os.path.isfile(out):
        tail = (p.stderr.strip().splitlines() or p.stdout.strip().splitlines() or ["(no output)"])[-1]
        print(f"error: {tail}", file=sys.stderr)
        return 2
    warns = [ln for ln in p.stderr.splitlines() if ln.startswith(("WARNING", "ERROR"))]
    for w in warns:
        print("warning: " + w.split(" ", 1)[1], file=sys.stderr)
    if warns:
        print("error: --strict: the renderer reported fallbacks", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
