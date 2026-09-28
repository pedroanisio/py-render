"""Command line: scenerender-vpkg {init,pack,verify,info,unpack,fetch,run} (also python -m scenerender.vpkg).

Exit status: 0 ok, 1 errors (pack refused, verify failed, a step failed), 2 usage errors.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__, archive, manifest as mf
from .pack import PackError, human, pack as do_pack, plan as do_plan, summary


def _print_list(title: str, items, limit: int = 40) -> None:
    items = list(items)
    if not items:
        return
    print(f"{title} ({len(items)}):")
    for x in items[:limit]:
        print(f"  {x}")
    if len(items) > limit:
        print(f"  ... {len(items) - limit} more")


def cmd_init(a) -> int:
    from .init import init
    target = os.path.join(a.project, mf.MANIFEST)
    if os.path.exists(target) and not a.force:
        print(f"{target} exists (use --force to replace it)", file=sys.stderr)
        return 1
    spec = init(a.project)
    text = mf.dump(spec)
    if a.stdout:
        sys.stdout.write(text)
        return 0
    with open(target, "w", encoding="utf-8") as f:
        f.write(text)
    roles = ", ".join(f"{s['path']}={s['role']}" for s in spec["scenes"])
    print(f"wrote {target}\n  scenes: {roles}\n  review the scene roles and write the pipeline steps before packing")
    return 0


def _options(a) -> dict:
    return dict(with_archive=a.with_archive, bundle_fetch=a.bundle_fetch, allow_nonportable=a.allow_nonportable,
                allow_missing_fonts=a.allow_missing_fonts, allow_remote=a.allow_remote,
                use_system_fonts=not a.no_system_fonts)


def cmd_pack(a) -> int:
    try:
        if a.dry_run:
            p = do_plan(a.project, **_options(a))
            m = None
        else:
            out, m, p = do_pack(a.project, a.output, date=a.date, **_options(a))
    except PackError as e:
        _print_list("warnings", e.warnings)
        _print_list("errors", e.errors, 200)
        print("pack refused", file=sys.stderr)
        return 1
    except mf.ManifestError as e:
        print(e, file=sys.stderr)
        return 1
    _print_list("warnings", p.warnings)
    if a.verbose:
        _print_list("left out", (f"{r}  ({why})" for r, why in p.left_out), 500)
    else:
        reasons: dict = {}
        for _, why in p.left_out:
            reasons[why] = reasons.get(why, 0) + 1
        if reasons:
            print("left out: " + ", ".join(f"{n} {why}" for why, n in sorted(reasons.items())) + " (-v lists them)")
    if p.fonts:
        fams = sorted({f"{f['family']} ({f['origin']})" for f in p.fonts})
        print("fonts bundled: " + ", ".join(fams))
    for e in p.external:
        print(f"external: {e['original']} -> {e['path']}")
    if m is None:
        print(f"plan ok: {len(p.files) + len(p.content)} files")
        return 0
    for role, (n, s) in sorted(summary(m).items()):
        print(f"  {role:<8} {n:>5} files {human(s):>10}")
    print(f"wrote {out} ({human(os.path.getsize(out))}, {m['packed']['fileCount']} files, "
          f"{human(m['packed']['totalSize'])} unpacked)")
    return 0


def _report(rep, label: str) -> int:
    _print_list("warnings", rep.warnings)
    _print_list("errors", rep.errors, 200)
    print(f"{label}: {'ok' if rep.ok else 'FAILED'}")
    return 0 if rep.ok else 1


def cmd_verify(a) -> int:
    return _report(archive.verify(a.package, deep=not a.shallow), a.package)


def cmd_info(a) -> int:
    m = archive.read_manifest(a.package)
    if a.json:
        print(mf.dump(m), end="")
        return 0
    pkg, v = m["package"], m.get("video", {})
    print(f"{pkg['title']}  ({pkg['id']} {pkg['version']})")
    if v:
        print(f"  {v.get('width')}x{v.get('height')} {v.get('fps')} fps {v.get('duration')} s, audio "
              f"{'yes' if v.get('audio') else 'no'}, captions {', '.join(v.get('captions', [])) or 'none'}")
    for s in m["scenes"]:
        print(f"  scene {s['path']}: {s['role']}" + (f" for {', '.join(s['targets'])}" if s.get("targets") else ""))
    for s in m.get("pipeline", {}).get("steps", []):
        print(f"  step {s['id']} ({s['kind']})" + (" optional" if s.get("optional") else ""))
    for f in m.get("fetch", []):
        print(f"  fetch {f['path']} ({human(f.get('size', 0))}) {f['url']}")
    if "files" in m:
        for role, (n, s) in sorted(summary(m).items()):
            print(f"  {role:<8} {n:>5} files {human(s):>10}")
    return 0


def cmd_unpack(a) -> int:
    try:
        rep = archive.unpack(a.package, a.dest, fetch=a.fetch, force=a.force)
    except FileExistsError as e:
        print(e, file=sys.stderr)
        return 1
    return _report(rep, f"unpacked to {os.path.abspath(a.dest)}")


def cmd_fetch(a) -> int:
    errors = archive.fetch_all(a.path)
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


def cmd_run(a) -> int:
    from .run import StepError, run
    path = a.path
    if os.path.isfile(path):
        dest = a.dir or os.path.splitext(os.path.splitext(os.path.abspath(path))[0])[0]
        if not os.path.isdir(dest) or not os.listdir(dest):
            rep = archive.unpack(path, dest, fetch=not a.no_fetch)
            if not rep.ok:
                return _report(rep, "unpack")
        path = dest
    elif not a.no_fetch and not a.list:
        errors = archive.fetch_all(path)
        if errors:
            for e in errors:
                print(e, file=sys.stderr)
            return 1
    try:
        return run(path, engine=a.engine, only=a.only, start=a.start, until=a.until, force=a.force, dry=a.list,
                   python=a.python)
    except (StepError, mf.ManifestError, FileNotFoundError) as e:
        print(e, file=sys.stderr)
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scenerender-vpkg", description="Scene video packages (.vpkg.zip).")
    ap.add_argument("--version", action="version", version=f"scenerender-vpkg {__version__} (format {mf.FORMAT_VERSION})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write a starter vpkg.json for a project directory")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--force", action="store_true")
    p.add_argument("--stdout", action="store_true", help="print instead of writing vpkg.json")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("pack", help="build <id>-<version>.vpkg.zip from a project and its vpkg.json")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("-o", "--output", help="zip path or directory (default: current directory)")
    p.add_argument("-n", "--dry-run", action="store_true", help="check and plan only")
    p.add_argument("-v", "--verbose", action="store_true", help="list every file left out")
    p.add_argument("--with-archive", action="store_true", help="also pack draft and archive scenes")
    p.add_argument("--bundle-fetch", action="store_true", help="bundle fetch files instead of pinning them")
    p.add_argument("--allow-nonportable", action="store_true", help="report script portability errors as warnings")
    p.add_argument("--allow-missing-fonts", action="store_true", help="pack even when a font family is not found")
    p.add_argument("--allow-remote", action="store_true", help="allow http(s) references in scenes")
    p.add_argument("--no-system-fonts", action="store_true", help="resolve families from project fonts only")
    p.add_argument("--date", help="record this pack time (default: SOURCE_DATE_EPOCH, else none)")
    p.set_defaults(fn=cmd_pack)

    p = sub.add_parser("verify", help="check a package: zip, manifest, hashes, references")
    p.add_argument("package")
    p.add_argument("--shallow", action="store_true", help="skip the reference check (no extraction)")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("info", help="summarise a package or project")
    p.add_argument("package")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("unpack", help="verify and extract a package")
    p.add_argument("package")
    p.add_argument("dest")
    p.add_argument("--fetch", action="store_true", help="download the fetch entries")
    p.add_argument("--force", action="store_true", help="extract into a non-empty directory")
    p.set_defaults(fn=cmd_unpack)

    p = sub.add_parser("fetch", help="download missing fetch entries of an unpacked package or project")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("run", help="run the pipeline (a .vpkg.zip is unpacked first)")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--engine", help="render engine: py, rs, c, js (or a configured id)")
    p.add_argument("--only", action="append", default=[], metavar="STEP", help="run only these steps")
    p.add_argument("--from", dest="start", metavar="STEP")
    p.add_argument("--until", metavar="STEP")
    p.add_argument("--force", action="store_true", help="re-run steps whose outputs are up to date")
    p.add_argument("--list", action="store_true", help="print the commands without running them")
    p.add_argument("--dir", help="where to unpack a .vpkg.zip (default: beside it)")
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--python", default=sys.executable, help="interpreter for python steps")
    p.set_defaults(fn=cmd_run)

    a = ap.parse_args(argv)
    return a.fn(a)

