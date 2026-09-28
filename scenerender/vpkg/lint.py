"""Portability lint for the scripts a package carries.

Scene references outside the project are copied into the package and the packaged scene is rewritten, but a
script cannot be rewritten safely, so a script that reads a machine-specific path breaks on any other machine.
Reported per line:

  absolute-path   a literal absolute path into a home, mount, temp or source tree (/home/..., /Users/...,
                  /tmp/..., /mnt/..., /src, C:\\...), or into system font directories (bundle the font instead)
  outside-project a relative path that climbs out of the project ("../x", os.path.join(HERE, "..", ...),
                  Path(...).parent.parent / "other") -- only when it resolves outside the project directory

  temp-path       (warning) a literal /tmp path: works on Linux and macOS, not on Windows; prefer tempfile

Errors fail `pack`; warnings are listed. A line containing `vpkg: allow` is skipped. Shebangs, /dev, /proc and /usr/bin interpreters are fine.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

SCRIPT_EXTENSIONS = (".py", ".sh", ".bash", ".zsh", ".mjs", ".js", ".cjs", ".ts", ".rb", ".pl", ".r", ".jl",
                     ".ps1", ".bat", ".cmd", ".mk", ".makefile")
ABSOLUTE = re.compile(r"""(?<![\w.$}/])(/(?:home|Users|root|mnt|media|tmp|var/tmp|opt|srv|src|workspace|data|scratch)"""
                      r"""(?:/[^\s"'`;:,)\]}]*)?|/usr/(?:local/)?share/fonts[^\s"'`;:,)\]}]*|[A-Za-z]:\\\\?[^\s"'`;,)]+)""")
QUOTED = re.compile(r"""(["'`])((?:\.\./|\.\.\\)[^"'`]*)\1""")
JOIN_UP = re.compile(r"""\bjoin\(\s*(HERE|ROOT|BASE|DIR|SCRIPT_DIR|__dirname|os\.path\.dirname\([^)]*\))\s*,\s*["']\.\.["']""")


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    text: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.severity} {self.rule}: {self.text.strip()[:140]}"


def is_script(path: str) -> bool:
    name = os.path.basename(path).lower()
    return name.endswith(SCRIPT_EXTENSIONS) or name in ("makefile", "justfile", "dockerfile")


def lint_file(path: str, project: str, rel: str | None = None) -> list:
    rel = rel or os.path.relpath(path, project)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    out, here = [], os.path.dirname(os.path.abspath(path))
    project = os.path.abspath(project)
    for n, line in enumerate(lines, 1):
        if "vpkg: allow" in line or (n == 1 and line.startswith("#!")):
            continue
        for m in ABSOLUTE.finditer(line):
            value = m.group(1)
            if value.startswith(("/dev/", "/proc/")):
                continue
            temp = re.match(r"/(?:var/)?tmp(?:/|$)", value)
            out.append(Finding(rel, n, "temp-path" if temp else "absolute-path", line, "warning" if temp else "error"))
            break
        else:
            for m in QUOTED.finditer(line):
                target = os.path.normpath(os.path.join(here, m.group(2)))
                if not _inside(target, project):
                    out.append(Finding(rel, n, "outside-project", line))
                    break
            else:
                if JOIN_UP.search(line) and not _inside(os.path.dirname(here), project):
                    out.append(Finding(rel, n, "outside-project", line))
    return out


def _inside(path: str, root: str) -> bool:
    path, root = os.path.abspath(path), os.path.abspath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)
