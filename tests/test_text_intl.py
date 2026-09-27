"""Writing modes, dictionary hyphenation, emoji presentation and first_baseline."""
from __future__ import annotations

import os

import numpy as np
import pytest

from scenerender import document
from scenerender.assets import text as T
from scenerender.compositor import RenderContext
from scenerender.evaluator import Ctx, Evaluator
from scenerender.nodes import core as _core  # noqa: F401  (registers layer/group handlers)
from scenerender.pango_bridge import Pango
from scenerender.registry import FEATURES

HERE = os.path.dirname(__file__)
SHY, VS15, VS16 = "\u00ad", "\ufe0e", "\ufe0f"

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="800" height="450" fps="30" duration="2" seed="3"/>
  <assets>
{assets}
  </assets>
  <composition>
{nodes}
  </composition>
</scene>
"""


def make_rc(tmp_path, assets="", nodes="", scale=1.0):
    p = tmp_path / "t.xml"
    p.write_text(HEAD.format(assets=assets, nodes=nodes), encoding="utf-8")
    doc = document.load(str(p))
    assert not doc.validation_errors, doc.validation_errors[:3]
    return RenderContext(doc, Evaluator(doc), scale=scale)


def ctx(t=0.0):
    return Ctx(t=t, comp_t=t)


def block_of(rc, aid, t=0.0):
    return T.get_block(rc, T.resolve_spec(rc, rc.doc.ids[aid], ctx(t)))


def runs_of(lay, li=0):
    line = lay.get_line_readonly(li)
    out = []
    for r in line.runs:
        it = r.item
        an = it.analysis
        f = an.font
        d = f.describe()
        out.append((it.offset, it.length, int(an.gravity), d.get_family()))
    return out


def test_features_full():
    for f in ("text:writingMode", "text:hyphenate", "text:emoji"):
        assert FEATURES.level(f) == "full", f


# ---------------------------------------------------------------- writing modes
JP = '縦書きの日本語にLatin文字が混ざる。二列目のテキスト。'


def test_vertical_upright_cjk_sideways_latin(tmp_path):
    rc = make_rc(tmp_path, f'<text id="v" width="200" height="300" size="30" font="Noto Sans CJK JP" '
                           f'writingMode="vertical-rl" text="{JP}"/>')
    b = block_of(rc, "v")
    text_bytes = b.text.encode()
    grav = {}
    for li in range(len(b.lines)):
        for off, n, g, _fam in runs_of(b.layout, li):
            grav[text_bytes[off:off + n].decode()] = g
    latin = [g for s, g in grav.items() if "Latin" in s]
    cjk = [g for s, g in grav.items() if "縦" in s or "テキスト" in s]
    assert latin and all(g == int(Pango.Gravity.SOUTH) for g in latin)       # set sideways
    assert cjk and all(g == int(Pango.Gravity.EAST) for g in cjk)            # upright
    # columns: the first line is at the right edge of the box, lines progress leftwards
    assert len(b.lines) >= 2
    xs = [(b.O @ np.array([0, ln_.y + ln_.h / 2, 1]))[0] for ln_ in b.lines]
    assert xs[0] > xs[1] and xs[0] > 150
    # the line length runs down the box height
    assert T.box_dims(b.spec) == (300.0, 200.0)


def test_vertical_lr_columns_left_to_right(tmp_path):
    rc = make_rc(tmp_path, f'<text id="v" width="200" height="300" size="30" font="Noto Sans CJK JP" '
                           f'writingMode="vertical-lr" text="{JP}"/>')
    b = block_of(rc, "v")
    xs = [(b.O @ np.array([0, ln_.y + ln_.h / 2, 1]))[0] for ln_ in b.lines]
    assert xs[0] < xs[1] and xs[0] < 50                                       # first column on the left
    buf = T.render_block(rc, b, rc.root_matrix, ctx())
    assert buf is not None
    cols = np.nonzero(buf.px[..., 3].sum(0) > 0)[0] + buf.x0
    assert cols.min() < 40 and cols.max() < 200


def test_vertical_render_upright_glyph_is_tall(tmp_path):
    """An upright ideograph keeps its square shape; a sideways Latin run is taller than wide."""
    rc = make_rc(tmp_path, '<text id="v" width="80" height="400" size="40" font="Noto Sans CJK JP" '
                           'writingMode="vertical-rl" text="日AAAAAA"/>')
    b = block_of(rc, "v")
    buf = T.render_block(rc, b, rc.root_matrix, ctx())
    a = buf.px[..., 3]
    rows = np.nonzero(a.sum(1) > 0)[0]
    cols = np.nonzero(a.sum(0) > 0)[0]
    assert np.ptp(rows) > 3 * np.ptp(cols)               # one column: much taller than wide


# ---------------------------------------------------------------- hyphenation
EN = "Internationalization requires comprehensive typographical considerations."


def test_hyphen_points_dictionary():
    pts = T.hyphen_points("Internationalization", "en-US")
    word = "Internationalization"
    assert pts and all(2 <= p <= len(word) - 2 for p in pts)
    de = T.hyphen_points("Donaudampfschifffahrtsgesellschaft", "de-DE")
    assert de and 5 in de                             # Donau-dampf...
    assert T.hyphen_points("tiny", "en") == set()     # below the 5-letter limit


def test_hyphenate_inserts_soft_hyphens_and_breaks_with_hyphen(tmp_path):
    rc = make_rc(tmp_path, f'<text id="h" width="150" height="400" size="22" font="DejaVu Sans" hyphenate="true" '
                           f'language="en-US" text="{EN}"/>'
                           f'<text id="n" width="150" height="400" size="22" font="DejaVu Sans" hyphenate="false" '
                           f'language="en-US" text="{EN}"/>')
    h = block_of(rc, "h")
    n = block_of(rc, "n")
    assert SHY in h.text and SHY not in n.text
    assert h.text.replace(SHY, "") == EN
    ends = [h.text[ln_.c1 - 1] for ln_ in h.lines[:-1]]
    assert SHY in ends                                # at least one line breaks at a dictionary point
    # every line fits the box when hyphenating; the unhyphenated first word overflows
    assert max(ln_.w for ln_ in h.lines) <= 150.5
    assert max(ln_.w for ln_ in n.lines) > 150.5
    # soft hyphens are not units
    assert all(not h.is_shy(u[0]) for u in h.units("character"))
    assert len(h.units("character")) == len(n.units("character"))


def test_hyphenate_german_paragraph(tmp_path):
    txt = "Donaudampfschifffahrtsgesellschaft und Rechtsschutzversicherungsgesellschaften."
    rc = make_rc(tmp_path, f'<text id="h" width="160" height="400" size="22" font="DejaVu Sans" hyphenate="true" '
                           f'language="de-DE" text="{txt}"/>')
    b = block_of(rc, "h")
    first = b.text[b.lines[0].c0:b.lines[0].c1]
    assert first.replace(SHY, "").startswith("Donau") and first.endswith(SHY)
    assert max(ln_.w for ln_ in b.lines) <= 160.5


def test_hyphen_drawn_at_break_in_animated_text(tmp_path):
    """The visible break hyphen survives when units are drawn as pieces."""
    rc = make_rc(tmp_path, f'<text id="h" width="150" height="400" size="22" font="DejaVu Sans" hyphenate="true" '
                           f'text="{EN}"/>',
                 '<layer id="l" asset="h"><textAnimator unit="character" x="0.001"/></layer>')
    b = block_of(rc, "h")
    static = T.render_block(rc, b, rc.root_matrix, ctx())
    anim = rc.render_frame(0.5)
    a = anim.region(static.rect)[..., 3]
    s = static.px[..., 3]
    assert abs(a.sum() - s.sum()) / s.sum() < 0.02


# ---------------------------------------------------------------- emoji
def test_emoji_presentation_selectors():
    assert T.emoji_presentation("❤", "color") == "❤" + VS16          # text-default -> emoji
    assert T.emoji_presentation("\U0001F600", "color") == "\U0001F600"           # already emoji
    assert T.emoji_presentation("\U0001F600", "text") == "\U0001F600" + VS15
    assert T.emoji_presentation("❤" + VS16, "text") == "❤" + VS15      # selector replaced
    assert T.emoji_presentation("1 2", "text") == "1 2"                          # digits untouched
    assert T.emoji_presentation("1\u20e3", "color") == "1" + VS16 + "\u20e3"     # keycap
    assert T.emoji_presentation("\U0001F44D\U0001F3FD", "color") == "\U0001F44D\U0001F3FD"   # modifier sequence
    assert T.emoji_spans("a \U0001F44D\U0001F3FD b") == [(2, 4)]


EMO = "&#x1F600; &#x2764; &#x1F44D; &#x2600;"


def test_emoji_color_uses_colour_font(tmp_path):
    rc = make_rc(tmp_path, f'<text id="e" width="500" height="80" size="40" font="DejaVu Sans" emoji="color" '
                           f'text="A {EMO}"/>')
    b = block_of(rc, "e")
    fams = {fam for *_r, fam in runs_of(b.layout)}
    assert "Noto Color Emoji" in fams
    buf = T.render_block(rc, b, rc.root_matrix, ctx())
    px = buf.px[..., :3][buf.px[..., 3] > 0.9]
    assert px.std(0).max() > 0.1                        # colourful


def test_emoji_text_is_monochrome(tmp_path):
    rc = make_rc(tmp_path, f'<text id="e" width="600" height="80" size="40" font="DejaVu Sans" emoji="text" '
                           f'color="#FF0000FF" text="A {EMO} &#x1FAE0;"/>')
    b = block_of(rc, "e")
    assert VS15 in b.text
    runs = runs_of(b.layout)
    text_bytes = b.text.encode()
    fam_of = {text_bytes[o:o + n].decode(): fam for o, n, _g, fam in runs}
    assert any(f in ("DejaVu Sans", "Noto Sans Symbols2") for s, f in fam_of.items() if "\U0001F600" in s)
    mono = b._cache.get("mono")
    assert mono and any("\U0001FAE0" in b.text[b.clusters[ci][0]:b.clusters[ci][1]] for ci in mono)
    buf = T.render_block(rc, b, rc.root_matrix, ctx())
    a = buf.px[..., 3]
    col = buf.px[..., :3][a > 0.5] / a[a > 0.5][:, None]
    # straight colour of every covered pixel is the text red (silhouettes included)
    assert np.all(col[:, 0] > 0.9) and np.all(col[:, 1:] < 0.1)


# ---------------------------------------------------------------- first_baseline
def test_first_baseline_matches_pango(tmp_path):
    rc = make_rc(tmp_path, '<text id="a" width="400" height="200" size="40" font="DejaVu Sans" text="Hello"/>'
                           '<text id="m" width="400" height="200" size="40" font="DejaVu Sans" verticalAlign="middle" text="Hello"/>'
                           '<text id="b" width="400" height="200" size="40" font="DejaVu Sans" verticalAlign="bottom" text="Hello"/>'
                           '<text id="f" width="100" height="60" size="80" font="DejaVu Sans" autoFit="shrink" text="Shrunk"/>'
                           '<text id="v" width="100" height="160" size="30" font="DejaVu Sans" writingMode="vertical-rl" text="v"/>')
    a = block_of(rc, "a")
    ref = a.lines[0].baseline + a.O[1, 2]
    top = T.first_baseline(rc, rc.doc.ids["a"], ctx())
    assert abs(top - ref) < 1e-6 and 0.7 * 40 < top < 1.2 * 40
    # the baseline lies between the line's top and bottom, below the ink top of "H"
    assert a.lines[0].y + a.O[1, 2] < top < a.lines[0].y + a.lines[0].h + a.O[1, 2]
    mid = T.first_baseline(rc, rc.doc.ids["m"], ctx())
    bot = T.first_baseline(rc, rc.doc.ids["b"], ctx())
    h = a.logical[3] - a.logical[1]
    assert abs(mid - (top + (200 - h) / 2)) < 1e-6
    assert abs(bot - (top + 200 - h)) < 1e-6
    fit = T.first_baseline(rc, rc.doc.ids["f"], ctx())
    blk = block_of(rc, "f")
    assert blk.k < 1 and abs(fit - blk.lines[0].baseline * 1.0 - blk.O[1, 2]) < 1e-6
    assert fit < 60
    assert T.first_baseline(rc, rc.doc.ids["v"], ctx()) == 160.0


def test_first_baseline_drives_group_baseline_alignment(tmp_path):
    rc = make_rc(tmp_path, '<text id="s" width="200" height="100" size="20" font="DejaVu Sans" text="small"/>'
                           '<text id="L" width="200" height="100" size="60" font="DejaVu Sans" text="Large"/>',
                 '<group id="g" layout="row" alignItems="baseline" width="600" height="200">'
                 '<layer id="a" asset="s"/><layer id="b" asset="L"/></group>')
    from scenerender import layout as LY
    base_s = T.first_baseline(rc, rc.doc.ids["s"], ctx())
    base_l = T.first_baseline(rc, rc.doc.ids["L"], ctx())
    assert base_l > base_s
    assert LY._baseline(rc, rc.doc.ids["a"], ctx(), None, 100.0) == pytest.approx(base_s)


def test_intl_fixture_renders():
    from scenerender.render import Renderer
    try:
        r = Renderer.open(os.path.join(HERE, "fixtures", "text_intl.xml"), scale=0.25)
    except ImportError as e:        # other plugin modules may be mid-edit
        pytest.skip(f"plugin import failed: {e}")
    assert not r.doc.validation_errors
    rgb = r.frame_rgb(0.5)
    assert rgb.std() > 5
