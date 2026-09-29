"""Command line: scenerender {render,still,check,validate,coverage} SCENE [options].

Exit status of render: 0 ok, 1 an output failed, 2 nothing to render, 4 an output failed QA
(accessibility/safe-area checks at level error). check: 0 passed (warnings allowed), 4 errors."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("scene")
    p.add_argument("--param", action="append", default=[], metavar="ID=VALUE", help="override a parameter")
    p.add_argument("--variant")
    p.add_argument("--layout")
    p.add_argument("--scale", type=float, default=1.0, help="output pixels per document pixel")
    p.add_argument("--lenient", action="store_true",
                   help="render documents that fail schema or Schematron validation (with a warning) instead of refusing them")
    p.add_argument("--strict", action="store_true", help=argparse.SUPPRESS)   # the default; kept for old scripts
    p.add_argument("--representation", help="preferred asset representation (e.g. proxy)")
    p.add_argument("--assets-dir", help="directory asset paths resolve against (default: the scene's directory)")
    p.add_argument("-v", "--verbose", action="store_true")


def _params(args) -> dict[str, str]:
    out = {}
    for kv in args.param:
        k, _, v = kv.partition("=")
        out[k] = v
    return out


def _open(args):
    from .render import Renderer
    return Renderer.open(args.scene, scale=args.scale, params=_params(args), variant=args.variant,
                         layout=args.layout, strict=not args.lenient, representation=args.representation,
                         assets_dir=args.assets_dir)


def cmd_still(args) -> int:
    from PIL import Image
    r = _open(args)
    for spec in args.time.split(","):
        t = r.doc.markers[spec] if spec in r.doc.markers else float(spec)
        t0 = time.perf_counter()
        rgb = r.frame_rgb(t)
        out = args.out if "," not in args.time else f"{os.path.splitext(args.out)[0]}_{t:07.3f}{os.path.splitext(args.out)[1] or '.png'}"
        Image.fromarray(rgb).save(out)
        print(f"{out}  t={t:.3f}s  {time.perf_counter() - t0:.2f}s")
    return 0


def cmd_render(args) -> int:
    from .output import render_outputs
    if args.no_gpu:
        os.environ["SCENERENDER_GPU"] = "0"     # frame workers inherit it
    r = _open(args)
    return render_outputs(r, args)


def cmd_check(args) -> int:
    from .qa import EXIT_QA, check
    findings, text = check(args.scene, scale=args.scale, t0=args.t0, t1=args.t1,
                           open_kwargs=dict(params=_params(args), variant=args.variant, layout=args.layout,
                                            assets_dir=args.assets_dir, strict=not args.lenient))
    print(text)
    return EXIT_QA if any(f.level == "error" for f in findings) else 0


def cmd_validate(args) -> int:
    from . import document
    try:
        doc = document.load(args.scene, strict=False)
    except document.SceneError as e:
        print(e)
        return 3
    if doc.validation_errors:
        print(f"{args.scene}: INVALID ({len(doc.validation_errors)} errors)")
        for e in doc.validation_errors[: args.max_errors or None]:
            print("  line", e)
        return 3
    print(f"{args.scene}: valid scene-render {doc.root.get('version')}")
    return 0


def cmd_coverage(args) -> int:
    from .coverage import report
    print(report(args.scene, all_features=args.all))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scenerender", description="Render scene-render 1.1 documents.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("still", help="render stills at given times or marker ids")
    _common(p)
    p.add_argument("-t", "--time", required=True, help="seconds or marker id, comma-separated")
    p.add_argument("-o", "--out", default="still.png")
    p.set_defaults(fn=cmd_still)

    p = sub.add_parser("render", help="render the document's outputs, or one file with -o")
    _common(p)
    p.add_argument("-o", "--out", help="single output file (.mp4/.mov/.mkv/.webm/.gif, or a dir for PNG frames)")
    p.add_argument("--output", action="append", default=[], help="render only these output ids")
    p.add_argument("--fps", type=float, help="override frame rate")
    p.add_argument("--from", dest="t0", type=float, default=None)
    p.add_argument("--to", dest="t1", type=float, default=None)
    p.add_argument("--frames-dir", help="keep rendered frames here; rerunning resumes")
    p.add_argument("--jobs", type=int, default=1,
                   help="frame worker processes (default 1, the least CPU time; 0 = one per CPU, for wall time)")
    p.add_argument("--no-audio", action="store_true")
    p.add_argument("--no-motion-blur", action="store_true", help="ignore project motion blur (fast previews)")
    p.add_argument("--no-gpu", action="store_true", help="never use the GPU (NVENC encoding, GPU effects)")
    p.add_argument("--crf", type=int)
    p.add_argument("--publish", action="store_true",
                   help="upload to non-file <destination>s (s3, gcs, azure-blob, http-put, sftp, webhook)")
    p.add_argument("--no-qa", action="store_true", help="skip accessibility and safe-area checks")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("check", help="run the QA checks (flashes, contrast, safe areas, captions) on a quick low-res pass")
    _common(p)
    p.set_defaults(scale=0.25)
    p.add_argument("--from", dest="t0", type=float, default=None)
    p.add_argument("--to", dest="t1", type=float, default=None)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("validate", help="validate against the XSD and the Schematron")
    p.add_argument("scene")
    p.add_argument("--max-errors", type=int, default=0, help="list at most this many problems (default 0: all)")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("coverage", help="list which features of a document the Python renderer supports")
    p.add_argument("scene")
    p.add_argument("--all", action="store_true", help="also list supported features")
    p.set_defaults(fn=cmd_coverage)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
                        format="%(levelname)s %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
