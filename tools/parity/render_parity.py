#!/usr/bin/env python3
"""Rendering parity against the canonical engine (rs-scene-render).

Renders the same scenes at the same times with the Rust `scene-render` binary and with
this package, and compares the pixels.

    python tools/parity/render_parity.py --rs /path/to/scene-render --out OUT_DIR \
        SCENE.xml|DIR ... [--times 0,0.5,0.9] [--jobs 4] [--timeout 300] [--sheets]

Times are fractions of the project duration unless suffixed with "s" (e.g. 2.5s).
Each (scene, time) is classified:

    MATCH      PSNR >= 45 dB and < 0.5 % of pixels off by more than 2 levels
    CLOSE      PSNR >= 30 dB
    DIFFERENT  rendered by both, below 30 dB
    SIZE       both rendered, different dimensions
    PY-ERROR   the Python renderer failed (message kept)
    RS-ERROR   the Rust renderer failed (scene not usable as a reference)

Results: OUT/results.json, OUT/SUMMARY.md and, with --sheets, OUT/sheets/<scene>_<t>.png
(Rust | Python | difference x4). Run with PYTHONPATH pointing at the scenerender tree to test.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from lxml import etree
from PIL import Image

PY = sys.executable


def scenes_from(args: list[str]) -> list[Path]:
    out: list[Path] = []
    for a in args:
        p = Path(a)
        out.extend(sorted(p.glob("*.xml")) if p.is_dir() else [p])
    return out


def project_info(path: Path) -> tuple[float, float]:
    root = etree.parse(str(path)).getroot()
    proj = root.find("project")
    dur = float(proj.get("duration", "1"))
    fps = proj.get("fps", "30")
    num, _, den = fps.partition("/")
    return dur, float(num) / float(den or 1)


def sample_times(spec: str, duration: float, fps: float) -> list[float]:
    ts = []
    for part in spec.split(","):
        part = part.strip()
        t = float(part[:-1]) if part.endswith("s") else float(part) * duration
        # Snap to a frame so both engines see the same instant.
        ts.append(min(max(0.0, round(t * fps) / fps), max(0.0, duration - 1 / fps)))
    return sorted(set(ts))


def run(cmd: list[str], cwd: Path, timeout: float, env=None) -> tuple[bool, str, float]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout:.0f} s", time.perf_counter() - t0
    ok = p.returncode == 0
    msg = "" if ok else (p.stderr.strip().splitlines() or p.stdout.strip().splitlines() or ["(no output)"])[-1]
    return ok, msg[:400], time.perf_counter() - t0


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), np.int16)


def compare(a: np.ndarray, b: np.ndarray) -> dict:
    d = np.abs(a - b)
    mse = float((d.astype(np.float64) ** 2).mean())
    psnr = 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)
    return {"psnr": round(psnr, 2), "max": int(d.max()), "pct_off2": round(float((d.max(-1) > 2).mean() * 100), 3)}


def classify(m: dict) -> str:
    if m["psnr"] >= 45 and m["pct_off2"] < 0.5:
        return "MATCH"
    return "CLOSE" if m["psnr"] >= 30 else "DIFFERENT"


def sheet(a: np.ndarray, b: np.ndarray, path: Path) -> None:
    d = np.clip(np.abs(a - b) * 4, 0, 255).astype(np.uint8)
    w = 480
    ims = [Image.fromarray(x.astype(np.uint8)) for x in (a, b)] + [Image.fromarray(d)]
    h = max(1, round(w * a.shape[0] / a.shape[1]))
    out = Image.new("RGB", (w * 3, h))
    for i, im in enumerate(ims):
        out.paste(im.resize((w, h)), (i * w, 0))
    out.save(path)


def one(rs: str, scene: Path, t: float, out: Path, timeout: float, sheets: bool, env) -> dict:
    tag = f"{scene.stem}_{t:08.3f}"
    rs_png, py_png = out / "rs" / f"{tag}.png", out / "py" / f"{tag}.png"
    rec = {"scene": str(scene), "t": t}
    ok_rs, msg_rs, t_rs = run([rs, "render", scene.name, "-t", f"{t:.6f}", "-o", str(rs_png)], scene.parent, timeout)
    ok_py, msg_py, t_py = run([PY, "-m", "scenerender.cli", "still", scene.name, "-t", f"{t:.6f}", "-o", str(py_png),
                               "--lenient"], scene.parent, timeout, env)
    rec.update(rs_seconds=round(t_rs, 2), py_seconds=round(t_py, 2))
    if not ok_rs or not rs_png.exists():
        rec.update(status="RS-ERROR", error=msg_rs)
        return rec
    if not ok_py or not py_png.exists():
        rec.update(status="PY-ERROR", error=msg_py)
        return rec
    a, b = load_rgb(rs_png), load_rgb(py_png)
    if a.shape != b.shape:
        rec.update(status="SIZE", rs_size=list(a.shape[:2]), py_size=list(b.shape[:2]))
        return rec
    m = compare(a, b)
    rec.update(m, status=classify(m))
    if sheets and rec["status"] != "MATCH":
        sheet(a, b, out / "sheets" / f"{tag}.png")
    return rec


def summary(results: list[dict]) -> str:
    from collections import Counter
    c = Counter(r["status"] for r in results)
    lines = ["# Rendering parity vs rs-scene-render", "",
             "| status | count |", "|---|---:|"] + [f"| {k} | {v} |" for k, v in sorted(c.items())]
    lines += ["", "| scene | t | status | PSNR | max | % >2 | rs s | py s | note |", "|---|---:|---|---:|---:|---:|---:|---:|---|"]
    for r in sorted(results, key=lambda r: (r["status"], r["scene"], r["t"])):
        lines.append(f"| {Path(r['scene']).name} | {r['t']:.3f} | {r['status']} | {r.get('psnr', '')} | {r.get('max', '')} | "
                     f"{r.get('pct_off2', '')} | {r['rs_seconds']} | {r['py_seconds']} | {r.get('error', '')[:120]} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenes", nargs="+")
    ap.add_argument("--rs", required=True, help="path to the Rust scene-render binary")
    ap.add_argument("--out", required=True)
    ap.add_argument("--times", default="0.1,0.5,0.9")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--sheets", action="store_true")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    for sub in ("rs", "py", "sheets"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    jobs = []
    for s in scenes_from(a.scenes):
        s = s.resolve()
        try:
            dur, fps = project_info(s)
        except Exception as e:  # noqa: BLE001 — unparsable scenes are reported, not fatal
            print(f"skip {s.name}: {e}", file=sys.stderr)
            continue
        jobs += [(s, t) for t in sample_times(a.times, dur, fps)]
    results = []
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(one, a.rs, s, t, out, a.timeout, a.sheets, env): (s, t) for s, t in jobs}
        for f in cf.as_completed(futs):
            r = f.result()
            results.append(r)
            print(f"{r['status']:10s} {Path(r['scene']).name} t={r['t']:.3f} {r.get('psnr', '')} {r.get('error', '')[:100]}",
                  flush=True)
    (out / "results.json").write_text(json.dumps(results, indent=1))
    (out / "SUMMARY.md").write_text(summary(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
