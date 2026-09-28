"""README.md written into every package, generated from its manifest."""
from __future__ import annotations


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def readme(m: dict) -> str:
    pkg, video = m["package"], m.get("video", {})
    out = [f"# {pkg['title']}", ""]
    if pkg.get("description"):
        out += [pkg["description"], ""]
    facts = [f"package `{pkg['id']}` {pkg['version']}"]
    if video.get("width"):
        facts.append(f"{video['width']}×{video['height']}")
    if video.get("fps"):
        facts.append(f"{video['fps']} fps")
    if video.get("duration") is not None:
        facts.append(f"{video['duration']:g} s")
    if pkg.get("languages"):
        facts.append(", ".join(pkg["languages"]))
    if pkg.get("license"):
        facts.append(f"license {pkg['license']}")
    out += [" · ".join(facts), ""]
    out += ["This is a scene video package (format `scene-video-package` "
            f"{m['formatVersion']}). `vpkg.json` lists every file with its SHA-256, the scenes, the pipeline that "
            "rebuilds the video, and what must be installed or downloaded. Only the render engine is not included.",
            "", "## Use", "", "```bash",
            f"scenerender-vpkg verify {pkg['id']}-{pkg['version']}.vpkg.zip",
            f"scenerender-vpkg unpack {pkg['id']}-{pkg['version']}.vpkg.zip {pkg['id']} --fetch",
            f"scenerender-vpkg run {pkg['id']} --engine py        # or rs, c, js; --list shows the plan",
            "```", "",
            "Without the tool: unzip, download each `fetch` entry to its path (check the SHA-256), set "
            "`FONTCONFIG_FILE=project/_vpkg/fonts.conf` when present, and run the steps below from `project/`.", ""]
    out += ["## Scenes", ""]
    for s in m["scenes"]:
        extra = []
        if s.get("targets"):
            extra.append("for " + ", ".join(s["targets"]))
        if s.get("derivedFrom"):
            extra.append(f"from `{s['derivedFrom']}`")
        if s.get("note"):
            extra.append(s["note"])
        packed = "" if s["role"] in ("primary", "variant") or any(f["path"] == s["path"] for f in m.get("files", [])) \
            else " (not packed)"
        out.append(f"- `{s['path']}` — {s['role']}{packed}" + (f": {'; '.join(extra)}" if extra else ""))
    out.append("")
    steps = m.get("pipeline", {}).get("steps", [])
    if steps:
        out += ["## Pipeline", "", "Run from `project/`. Steps marked optional need a tool or network access "
                "that may be missing; their outputs are already in the package.", ""]
        for i, s in enumerate(steps, 1):
            if s["kind"] == "render":
                r = s["render"]
                what = f"render `{r['scene']}`" + (f" output `{r['output']}`" if r.get("output") else "") \
                    + (f" to `{r['to']}`" if r.get("to") else "") \
                    + (f" at {', '.join(f'{t:g}' for t in r['stills'])} s" if r.get("stills") else "") \
                    + " with the chosen engine"
            else:
                env = " ".join(f"{k}={v}" for k, v in s.get("env", {}).items())
                what = "`" + (env + " " if env else "") + " ".join(s["run"]) + "`" \
                    + (f" (in `{s['cwd']}`)" if s.get("cwd") else "")
            flags = [x for x in ("optional" if s.get("optional") else "",
                                 "not idempotent" if s.get("idempotent") is False else "") if x]
            desc = f" — {s['description']}" if s.get("description") else ""
            out.append(f"{i}. **{s['id']}** ({s['kind']}{', ' + ', '.join(flags) if flags else ''}): {what}{desc}")
        out.append("")
    req = m.get("requirements", {})
    if req or m.get("engines"):
        out += ["## Requirements", ""]
        for t in req.get("tools", []):
            bits = [t["name"] + (f" {t['version']}" if t.get("version") else "")]
            if t.get("features"):
                bits.append("with " + ", ".join(t["features"]))
            if t.get("for"):
                bits.append("for " + ", ".join(t["for"]))
            out.append("- " + " ".join(bits) + (f" — {t['note']}" if t.get("note") else ""))
        if req.get("python"):
            out.append("- Python packages: " + ", ".join(f"`{x}`" for x in req["python"]))
        for n in req.get("notes", []):
            out.append(f"- {n}")
        for e in m.get("engines", []):
            ref = " ".join(x for x in (e.get("impl", ""), e.get("version", ""),
                                       f"@{e['commit'][:12]}" if e.get("commit") else "") if x)
            out.append(f"- engine `{e['id']}` {ref}: tested {e.get('tested', 'untested')}"
                       + (f" — {e['note']}" if e.get("note") else ""))
        out.append("")
    if m.get("fetch"):
        out += ["## Downloads", "", "| Path | Size | Source |", "|---|---|---|"]
        for f in m["fetch"]:
            out.append(f"| `{f['path']}` | {_size(f['size']) if 'size' in f else ''} | {f['url']} |")
        out.append("")
    if m.get("fonts"):
        out += ["## Bundled fonts", "", "Families the scenes name without declaring them; the packaged scenes now "
                "declare these files.", ""]
        seen = set()
        for f in m["fonts"]:
            if f["family"] in seen:
                continue
            seen.add(f["family"])
            files = [x for x in m["fonts"] if x["family"] == f["family"]]
            lic = (f.get("license") or f.get("copyright") or "").split("\n")[0][:160]
            out.append(f"- {f['family']} ({len(files)} face{'s' if len(files) > 1 else ''}, {f['origin']})"
                       + (f": {lic}" if lic else ""))
        out.append("")
    credits = m.get("credits", [])
    licensed = [f for f in m.get("files", []) if f.get("license") or f.get("credit")]
    if credits or licensed:
        out += ["## Credits", ""]
        for c in credits:
            head = c.get("title") or ", ".join(c.get("paths", []))
            out.append("- " + " — ".join(x for x in (head, c.get("author"), c.get("license"), c.get("url")) if x))
        for f in licensed:
            out.append(f"- `{f['path']}`: " + "; ".join(x for x in (f.get("credit"), f.get("license")) if x))
        out.append("")
    if m.get("external") or m.get("rewrites"):
        out += ["## Changes made when packing", ""]
        for e in m.get("external", []):
            out.append(f"- `{e['original']}` (outside the project) copied to `{e['path']}`")
        for r in m.get("rewrites", []):
            out.append(f"- `{r['path']}`: " + "; ".join(r["changes"]))
        out.append("")
    files = m.get("files", [])
    if files:
        roles: dict = {}
        for f in files:
            n, s = roles.get(f["role"], (0, 0))
            roles[f["role"]] = (n + 1, s + f["size"])
        out += ["## Contents", "", "| Role | Files | Size |", "|---|---:|---:|"]
        for role, (n, s) in sorted(roles.items()):
            out.append(f"| {role} | {n} | {_size(s)} |")
        out.append(f"| **total** | {len(files)} | {_size(sum(f['size'] for f in files))} |")
        out.append("")
    return "\n".join(out)
