"""formula assets: TeX math typeset by a real engine and drawn as vectors.

Engines, tried in order (the first that accepts the source wins; each failure warns once):
  1. LaTeX (`pdflatex` + `pdftocairo`, both from TeX Live / poppler) with librsvg: the source is set as
     display math, `$\\displaystyle <tex>$`, in `standalone` (preview, border 0) with amsmath + amssymb
     loaded, i.e. everything LaTeX math mode + AMS offers (\\frac, \\sqrt[n]{}, \\sum/\\int with limits,
     matrices/pmatrix/cases, \\left...\\right, \\mathbb/\\mathcal/\\mathfrak, \\text, \\operatorname, ...).
     The PDF becomes SVG glyph outlines (pdftocairo -svg) rendered by librsvg on the output context, so the
     result is resolution independent. TeX runs with -no-shell-escape and openin_any/openout_any=p
     (paranoid: no reading or writing outside the working directory). Results are cached on disk
     ($SCENERENDER_CACHE/formula, keyed by the source) so each formula compiles once per machine.
  2. matplotlib mathtext (no TeX installation): the formula is laid out by mathtext (Computer Modern
     fonts) and its glyph outlines (TextPath) are drawn as cairo paths. mathtext covers the common subset
     (fractions, radicals incl. \\sqrt[n], sub/superscripts, big operators with limits, accents, Greek,
     \\mathrm/\\mathbf/\\mathit/\\mathcal/\\mathbb, \\left/\\right delimiters, spacing). Not supported by
     mathtext: environments (matrix, pmatrix, cases, align, array), \\text (use \\mathrm), \\binom,
     \\operatorname, \\displaystyle/\\textstyle switches (ignored), \\color, \\boxed, \\overbrace/\\underbrace.
     Before layout those are rewritten where an equivalent exists (\\text/\\operatorname -> \\mathrm with its
     spaces kept, \\binom{a}{b} -> \\left(\\genfrac{}{}{0}{}{a}{b}\\right), top-level \\frac -> \\dfrac as in
     display style, \\tfrac -> \\frac, style switches dropped).
  3. Unicode + Pango markup (last resort when neither engine accepts the source).

Size and colour: @size is the font size in px: 1 em of the TeX font (10 pt of the standalone class,
= 9.9626 PDF units) maps to @size document pixels, as a text layer at that size. The formula is centred
in the @width x @height box and shrunk uniformly if it would not fit. @color is any paint (colour, token,
url(#gradient)); it fills the glyph coverage, gradients mapped to the asset box.
"""
from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import shutil
import subprocess
import tempfile

import cairo

from .. import paint as paintmod
from ..registry import ASSET_SIZES, ASSETS, FULL, PARTIAL, warn_once

log = logging.getLogger("scenerender")

_TEX_PT_PER_EM = 10.0 * 72.0 / 72.27                    # 10 TeX pt in PDF units (bp)
_TEMPLATE = (r"\documentclass[preview,border=0pt]{standalone}" "\n"
             r"\usepackage{amsmath,amssymb}" "\n"
             r"\begin{document}" "\n" r"$\displaystyle %s$" "\n" r"\end{document}" "\n")


def have_latex() -> bool:
    from .vector import HAVE_RSVG
    return bool(shutil.which("pdflatex") and shutil.which("pdftocairo") and HAVE_RSVG)


try:
    import matplotlib  # noqa: F401
    HAVE_MATHTEXT = True
except ImportError:  # pragma: no cover - optional
    HAVE_MATHTEXT = False


# ====================================================================== engine 1: LaTeX
def latex_svg(tex: str) -> str | None:
    """Path of the (cached) SVG for tex, or None when LaTeX rejects it."""
    from . import cache_dir
    src = _TEMPLATE % tex.strip().strip("$")
    key = hashlib.sha256(src.encode()).hexdigest()[:24]
    out = os.path.join(cache_dir("formula"), key + ".svg")
    bad = out + ".err"
    if os.path.exists(out):
        return out
    if os.path.exists(bad):
        return None
    env = dict(os.environ, openin_any="p", openout_any="p")
    with tempfile.TemporaryDirectory(prefix="sr-tex-") as d:
        with open(os.path.join(d, "f.tex"), "w") as f:
            f.write(src)
        try:
            r = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "f.tex"],
                               cwd=d, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
            if r.returncode != 0 or not os.path.exists(os.path.join(d, "f.pdf")):
                err = next((ln for ln in r.stdout.decode(errors="replace").splitlines() if ln.startswith("!")),
                           "LaTeX error")
                warn_once("formula", tex[:60], f"LaTeX rejected the formula ({err}); trying mathtext")
                open(bad, "w").write(err)
                return None
            subprocess.run(["pdftocairo", "-svg", "f.pdf", "f.svg"], cwd=d, check=True, capture_output=True, timeout=60)
            tmp = out + f".{os.getpid()}.tmp"
            shutil.copyfile(os.path.join(d, "f.svg"), tmp)
            os.replace(tmp, out)
        except (OSError, subprocess.SubprocessError) as e:
            warn_once("formula", "latex", f"LaTeX toolchain failed: {e}; trying mathtext")
            return None
    return out


def _svg_handle(rc, path: str):
    from .vector import _svg_handle as h
    return h(rc, path)


def _svg_extent(rc, path: str) -> tuple[float, float] | None:
    hd = _svg_handle(rc, path)
    if hd is None:
        return None
    ok, w, h = hd.get_intrinsic_size_in_pixels()
    if not ok:
        dims = hd.get_dimensions()
        w, h = dims.width, dims.height
    return float(w), float(h)


# ====================================================================== engine 2: mathtext
_MT_REWRITES = [
    (re.compile(r"\\(?:displaystyle|textstyle|scriptstyle|limits|nolimits)\b\s*"), ""),
]
_MT_TEXT = {"text": r"\mathrm", "textrm": r"\mathrm", "mbox": r"\mathrm", "operatorname": r"\mathrm",
            "operatorname*": r"\mathrm", "textbf": r"\mathbf", "textit": r"\mathit"}


def _brace_arg(s: str, i: int) -> tuple[str, int]:
    while i < len(s) and s[i] == " ":
        i += 1
    if i < len(s) and s[i] == "{":
        depth, j = 0, i
        while j < len(s):
            depth += {"{": 1, "}": -1}.get(s[j], 0)
            if depth == 0:
                return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    return (s[i], i + 1) if i < len(s) else ("", i)


def mathtext_source(tex: str) -> str:
    s = tex.strip().strip("$")
    for rx, rep in _MT_REWRITES:
        s = rx.sub(rep, s)
    out, i, depth = [], 0, 0
    while i < len(s):
        m = re.match(r"\\([A-Za-z]+\*?)", s[i:])
        name = m.group(1) if m else None
        if name in ("binom", "dbinom", "tbinom"):
            a, j = _brace_arg(s, i + 1 + len(name))
            b, j = _brace_arg(s, j)
            out.append(r"\left(\genfrac{}{}{0}{}{%s}{%s}\right)" % (mathtext_source(a), mathtext_source(b)))
            i = j
            continue
        if name in _MT_TEXT:                              # text: keep its spaces (math mode drops them)
            a, j = _brace_arg(s, i + 1 + len(name))
            out.append(_MT_TEXT[name] + "{" + a.replace(" ", r"\ ") + "}")
            i = j
            continue
        if name in ("frac", "dfrac", "tfrac"):            # display style: top-level fractions at full size
            out.append(r"\dfrac" if (name == "dfrac" or (name == "frac" and depth == 0)) else r"\frac")
            i += 1 + len(name)
            continue
        depth += {"{": 1, "}": -1}.get(s[i], 0)
        out.append(s[i])
        i += 1
    return "".join(out)


def mathtext_path(tex: str, size: float):
    """(list of cairo path ops, (x0, y0, x1, y1) bounds) in px with y down, or None if unsupported."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.path import Path
    from matplotlib.textpath import TextPath
    src = mathtext_source(tex)
    try:
        tp = TextPath((0, 0), f"${src}$", size=size, prop=FontProperties(family="serif", math_fontfamily="cm"))
        verts, codes = tp.vertices, tp.codes
    except (ValueError, RuntimeError) as e:
        warn_once("formula", tex[:60], f"mathtext cannot typeset the formula: {str(e).splitlines()[0][:120]}")
        return None
    if codes is None or len(verts) == 0:
        return [], (0.0, 0.0, 0.0, 0.0)
    ops, k = [], 0
    while k < len(codes):
        c = codes[k]
        if c == Path.MOVETO:
            ops.append(("M", verts[k][0], -verts[k][1]))
            k += 1
        elif c == Path.LINETO:
            ops.append(("L", verts[k][0], -verts[k][1]))
            k += 1
        elif c == Path.CURVE3:                                    # quadratic -> cubic
            (qx, qy), (ex, ey) = verts[k], verts[k + 1]
            qy, ey = -qy, -ey
            sx, sy = ops[-1][-2:] if ops else (0.0, 0.0)
            ops.append(("C", sx + 2 / 3 * (qx - sx), sy + 2 / 3 * (qy - sy), ex + 2 / 3 * (qx - ex),
                        ey + 2 / 3 * (qy - ey), ex, ey))
            k += 2
        elif c == Path.CURVE4:
            (ax, ay), (bx, by), (ex, ey) = verts[k], verts[k + 1], verts[k + 2]
            ops.append(("C", ax, -ay, bx, -by, ex, -ey))
            k += 3
        elif c == Path.CLOSEPOLY:
            ops.append(("Z",))
            k += 1
        else:
            k += 1
    xs, ys = verts[:, 0], -verts[:, 1]
    return ops, (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))


def _emit(cr, ops) -> None:
    for op in ops:
        if op[0] == "M":
            cr.move_to(op[1], op[2])
        elif op[0] == "L":
            cr.line_to(op[1], op[2])
        elif op[0] == "C":
            cr.curve_to(*op[1:])
        else:
            cr.close_path()


# ====================================================================== engine 3: Unicode + Pango markup
_SYM = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "ϕ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "approx": "≈", "equiv": "≡", "sim": "∼", "propto": "∝", "infty": "∞", "sum": "∑",
    "prod": "∏", "int": "∫", "oint": "∮", "partial": "∂", "nabla": "∇", "to": "→", "rightarrow": "→",
    "leftarrow": "←", "Rightarrow": "⇒", "Leftarrow": "⇐", "leftrightarrow": "↔", "Leftrightarrow": "⇔",
    "mapsto": "↦", "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "supset": "⊃", "cup": "∪",
    "cap": "∩", "forall": "∀", "exists": "∃", "emptyset": "∅", "ldots": "…", "cdots": "⋯", "dots": "…",
    "degree": "°", "circ": "∘", "ast": "∗", "star": "⋆", "prime": "′", "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ",
    "Im": "ℑ", "angle": "∠", "perp": "⊥", "parallel": "∥", "wedge": "∧", "vee": "∨", "neg": "¬", "oplus": "⊕",
    "otimes": "⊗", "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "{": "{", "}": "}", "%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "|": "‖",
    ",": " ", ";": " ", ":": " ", "!": "", " ": " ", "quad": " ", "qquad": "  ",
    "left": "", "right": "", "big": "", "Big": "", "displaystyle": "", "limits": "",
    "sin": "sin", "cos": "cos", "tan": "tan", "log": "log", "ln": "ln", "exp": "exp", "lim": "lim",
    "max": "max", "min": "min", "det": "det",
}


class _P:
    def __init__(self, s: str):
        self.s, self.i = s, 0
        self.script = 0          # nesting depth of ^ / _ (no relation spacing inside scripts)

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def group(self, upright: bool) -> str:
        while self.peek() == " ":
            self.i += 1
        if self.peek() == "{":
            self.i += 1
            out = self.seq(upright, "}")
            self.i += 1
            return out
        return self.atom(upright)

    def seq(self, upright: bool, stop: str = "") -> str:
        out = []
        while self.i < len(self.s) and self.peek() != stop:
            out.append(self.atom(upright))
        return "".join(out)

    def atom(self, upright: bool) -> str:
        c = self.peek()
        self.i += 1
        if c == "\\":
            j = self.i
            if j < len(self.s) and not self.s[j].isalpha():
                self.i += 1
                return html.escape(_SYM.get(self.s[j], self.s[j]))
            while self.i < len(self.s) and self.s[self.i].isalpha():
                self.i += 1
            name = self.s[j:self.i]
            if name in ("mathrm", "text", "textrm", "operatorname", "mbox"):
                return self.group(True)
            if name in ("mathbf", "textbf", "boldsymbol"):
                return f"<b>{self.group(upright)}</b>"
            if name in ("mathit", "textit"):
                return f"<i>{self.group(True)}</i>"
            if name in ("frac", "dfrac", "tfrac"):
                a, b = self.group(upright), self.group(upright)
                return f"<sup>{a}</sup>⁄<sub>{b}</sub>"
            if name == "sqrt":
                a = self.group(upright)
                return f"√<span underline='none' overline='single'>{a}</span>"
            if name in ("hat", "bar", "vec", "dot", "tilde", "overline"):
                a = self.group(upright)
                mark = {"hat": "̂", "bar": "̄", "vec": "⃗", "dot": "̇", "tilde": "̃", "overline": "̅"}[name]
                return a + mark
            if name in _SYM:
                return html.escape(_SYM[name])
            warn_once("formula", name, "unknown TeX command; shown literally")
            return html.escape("\\" + name)
        if c in "^_":
            self.script += 1
            inner = self.group(upright)
            self.script -= 1
            tag = "sup" if c == "^" else "sub"
            return f"<{tag}>{inner}</{tag}>"
        if c == "{":
            out = self.seq(upright, "}")
            self.i += 1
            return out
        if c == "}":
            return ""
        if c == "~":
            return " "
        if c == " ":
            return ""
        if c == "-":
            return "−"
        if c.isalpha() and c.isascii() and not upright:
            return f"<i>{c}</i>"
        if c in "=<>+" and not self.script:
            return f" {html.escape(c)} "
        return html.escape(c)


def tex_to_markup(tex: str) -> str:
    """Last-resort approximation: TeX -> Pango markup with Unicode symbols."""
    return _P(tex.strip().strip("$")).seq(False)


# ====================================================================== handler
def _engine() -> str:
    return "latex" if have_latex() else ("mathtext" if HAVE_MATHTEXT else "markup")


_E = _engine()


def _prepared(rc, tex: str, size: float):
    """('svg', path, w, h) in px at size | ('path', ops, bounds) | ('markup', layout) — cached per (tex, size)."""
    key = ("formula", tex, size)
    if key in rc.cache:
        return rc.cache[key]
    res = None
    if _E == "latex":
        p = latex_svg(tex)
        ext = _svg_extent(rc, p) if p else None
        if ext:
            k = size / _TEX_PT_PER_EM
            res = ("svg", p, ext[0], ext[1], k)
    if res is None and HAVE_MATHTEXT:
        mp = mathtext_path(tex, size)
        if mp is not None:
            res = ("path", mp[0], mp[1])
    if res is None:
        from ..pango_bridge import Pango, new_layout
        warn_once("formula", "markup:" + tex[:60], "formula approximated as Unicode text")
        lay = new_layout()
        fd = Pango.FontDescription.from_string("Serif")
        fd.set_absolute_size(size * Pango.SCALE)
        lay.set_font_description(fd)
        try:
            lay.set_markup(tex_to_markup(tex), -1)
        except Exception:  # noqa: BLE001 — malformed markup: fall back to the raw source
            lay.set_text(tex, -1)
        res = ("markup", lay)
    rc.cache[key] = res
    return res


@ASSETS.register("formula", level=FULL if _E == "latex" else PARTIAL,
                 note={"latex": "pdflatex + amsmath/amssymb, vector glyphs via pdftocairo/librsvg",
                       "mathtext": "matplotlib mathtext (no LaTeX): no environments/\\color/\\boxed; see assets/formula.py",
                       "markup": "no TeX engine or matplotlib: Unicode + Pango markup approximation"}[_E])
def render_formula(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    ev = rc.ev
    w, h = float(asset.get("width")), float(asset.get("height"))
    tex = ev.str(asset, "tex", ctx, "") or ""
    size = ev.num(asset, "size", ctx, 48.0)
    color = ev.str(asset, "color", ctx, "#FFFFFFFF")
    if not tex.strip():
        return None
    prep = _prepared(rc, tex, size)
    c = rc.canvas_for(M, w, h, 4)
    if c is None:
        return None
    cr = c.cr
    if clip:
        cr.rectangle(clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1])
        cr.clip()
    cr.push_group_with_content(cairo.CONTENT_ALPHA)
    kind = prep[0]
    if kind == "svg":
        _, path, sw, sh, k = prep
        fw, fh = sw * k, sh * k
        f = min(1.0, w / max(fw, 1e-9), h / max(fh, 1e-9))
        cr.translate((w - fw * f) / 2, (h - fh * f) / 2)
        from .vector import _render_rsvg
        _render_rsvg(rc, cr, path, fw * f, fh * f)
    elif kind == "path":
        _, ops, (x0, y0, x1, y1) = prep
        fw, fh = x1 - x0, y1 - y0
        f = min(1.0, w / max(fw, 1e-9), h / max(fh, 1e-9))
        cr.translate(w / 2, h / 2)
        cr.scale(f, f)
        cr.translate(-(x0 + x1) / 2, -(y0 + y1) / 2)
        _emit(cr, ops)
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)
        cr.set_source_rgba(0, 0, 0, 1)
        cr.fill()
    else:
        from ..pango_bridge import show_layout
        lay = prep[1]
        _, lg = lay.get_pixel_extents()
        f = min(1.0, w / max(1, lg.width), h / max(1, lg.height))
        cr.translate(w / 2, h / 2)
        cr.scale(f, f)
        cr.translate(-lg.x - lg.width / 2, -lg.y - lg.height / 2)
        cr.set_source_rgba(0, 0, 0, 1)
        show_layout(cr, lay)
    mask = cr.pop_group()                   # restores the asset-space CTM
    if not paintmod.set_source(rc, cr, color, w, h, ctx, asset):     # paints map to the asset box
        return None
    cr.mask(mask)
    return c.to_buf(rc.linear)


@ASSET_SIZES.register("formula")
def formula_size(rc, asset, ctx):
    return float(asset.get("width")), float(asset.get("height"))
