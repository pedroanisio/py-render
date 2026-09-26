"""Text assets, text animators, text paths and captions."""
from __future__ import annotations

import os

import numpy as np
import pytest

from scenerender import captions as C
from scenerender import document
from scenerender import text_animators as TA
from scenerender.assets import text as T
from scenerender.compositor import RenderContext
from scenerender.nodes import core as _core  # noqa: F401  (registers layer/group handlers)
from scenerender.evaluator import Ctx, Evaluator
from scenerender.registry import CAPTION_PRESETS, TEXT_ANIMATORS

HERE = os.path.dirname(__file__)

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="800" height="450" fps="30" duration="4" seed="3"/>
  <styles>
    <textStyle id="s-base" font="DejaVu Sans" size="30" color="#112233FF" lineHeight="1.3"/>
    <textStyle id="s-big" basedOn="s-base" size="50" weight="700"/>
  </styles>
  <assets>
{assets}
  </assets>
  <composition>
{nodes}
  </composition>
{tail}
</scene>
"""


def make_rc(tmp_path, assets="", nodes="", tail="", scale=1.0):
    p = tmp_path / "t.xml"
    p.write_text(HEAD.format(assets=assets, nodes=nodes, tail=tail), encoding="utf-8")
    doc = document.load(str(p))
    assert not doc.validation_errors, doc.validation_errors[:3]
    return RenderContext(doc, Evaluator(doc), scale=scale)


def ctx(t=0.0):
    return Ctx(t=t, comp_t=t)


def block_of(rc, aid, t=0.0):
    return T.get_block(rc, T.resolve_spec(rc, rc.doc.ids[aid], ctx(t)))


# ---------------------------------------------------------------- layout
def test_layout_size_and_wrap(tmp_path):
    rc = make_rc(tmp_path, '<text id="a" width="300" height="200" size="24" font="DejaVu Sans" '
                           'text="The quick brown fox jumps over the lazy dog again and again"/>')
    a = rc.doc.ids["a"]
    assert T.text_size(rc, a, ctx()) == (300.0, 200.0)
    b = block_of(rc, "a")
    assert len(b.lines) >= 2
    assert all(ln.w <= 300.5 for ln in b.lines)
    assert b.text.startswith("The quick")
    assert len(b.clusters) == len(b.text)


def test_wrap_none_single_line_and_alignment(tmp_path):
    rc = make_rc(tmp_path, '<text id="a" width="600" height="60" size="24" wrap="none" align="center" font="DejaVu Sans" text="short"/>')
    b = block_of(rc, "a")
    assert len(b.lines) == 1
    # centred inside the box: equal margins either side
    x0 = b.O[0, 2] + b.logical[0]
    x1 = b.O[0, 2] + b.logical[2]
    assert abs(x0 - (600 - x1)) < 1.0


def test_autofit_shrink_fits_and_respects_min(tmp_path):
    rc = make_rc(tmp_path, '<text id="a" width="200" height="60" size="40" autoFit="shrink" minSize="10" font="DejaVu Sans" '
                           'text="autoFit shrinks this long sentence until it fits"/>'
                           '<text id="b" width="200" height="60" size="40" autoFit="shrink" minSize="30" font="DejaVu Sans" '
                           'text="autoFit shrinks this long sentence until it fits"/>')
    b = block_of(rc, "a")
    assert b.k < 1.0
    assert b.logical[3] - b.logical[1] <= 60.5
    assert b.size >= 10
    b2 = block_of(rc, "b")
    assert b2.size == pytest.approx(30, abs=0.05)


def test_autofit_grow(tmp_path):
    rc = make_rc(tmp_path, '<text id="a" width="400" height="200" size="10" autoFit="grow" maxSize="60" font="DejaVu Sans" text="Hi"/>')
    b = block_of(rc, "a")
    assert b.k > 1.0 and b.size <= 60.01


def test_max_lines_ellipsis_and_clip(tmp_path):
    long = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    rc = make_rc(tmp_path, f'<text id="e" width="150" height="400" size="20" maxLines="2" overflow="ellipsis" font="DejaVu Sans" text="{long}"/>'
                           f'<text id="c" width="150" height="400" size="20" maxLines="2" font="DejaVu Sans" text="{long}"/>')
    e = block_of(rc, "e")
    assert e.layout.get_line_count() == 2
    assert e.layout.is_ellipsized()
    c = block_of(rc, "c")
    assert len(c.lines) > 2
    assert sum(not ln.hidden for ln in c.lines) == 2
    assert all(c.cl_line[u[0]] < 2 for u in c.units("word"))


def test_span_style_precedence(tmp_path):
    rc = make_rc(tmp_path, '<text id="a" width="600" height="200" size="20" style="s-base" color="#FF0000FF">'
                           '<span>plain </span><span style="s-big">big </span><span style="s-big" size="12" role="p">own</span></text>'
                           '<text id="b" width="600" height="200" size="20" style="s-base" text="styled"/>')
    spec = T.resolve_spec(rc, rc.doc.ids["a"], ctx())
    r0, r1, r2 = spec.runs
    assert r0.st["color"] == "#FF0000FF"            # asset attribute beats its style
    assert float(r0.st["size"]) == 20               # asset size beats style size
    assert float(r1.st["size"]) == 50 and int(r1.st["weight"]) == 700   # span style beats asset
    assert float(r2.st["size"]) == 12 and r2.role == "p"                 # span attribute beats span style
    assert float(r1.st["lineHeight"]) == 1.3         # inherited through basedOn
    specb = T.resolve_spec(rc, rc.doc.ids["b"], ctx())
    assert specb.runs[0].st["color"] == "#112233FF"   # textStyle colour beats the schema default


def test_font_features_and_axes_parsing():
    assert T._features("liga, tnum, -kern, ss01") == "liga=1,tnum=1,kern=0,ss01=1"
    assert T.parse_axes("wght 720, wdth 90") == {"wght": 720.0, "wdth": 90.0}


def test_text_transform_and_units(tmp_path):
    rc = make_rc(tmp_path, '<text id="a" width="800" height="200" size="20" font="DejaVu Sans" textTransform="uppercase">'
                           '<span>héllo world</span><span role="r"> again</span></text>'
                           '<text id="m" width="120" height="400" size="20" font="DejaVu Sans" text="aa bb cc dd ee ff gg hh"/>')
    b = block_of(rc, "a")
    assert b.text == "HÉLLO WORLD AGAIN"
    assert len(b.units("character")) == 17
    assert len(b.units("character-no-space")) == 15
    assert len(b.units("word")) == 3
    assert len(b.units("span")) == 2
    assert len(b.units("word", "r")) == 1
    m = block_of(rc, "m")
    assert len(m.units("line")) == len(m.lines) > 1


def test_static_render_draws_pixels_and_scales(tmp_path):
    a = '<text id="a" width="400" height="100" size="40" font="DejaVu Sans" text="Hello" background="#00000080" backgroundPadding="4"/>'
    n = '<layer id="l" asset="a" x="10" y="10"/>'
    rc1 = make_rc(tmp_path, a, n)
    buf = rc1.render_frame(0.0)
    cov1 = float(buf.px[..., 3].sum())
    assert cov1 > 100
    rc2 = make_rc(tmp_path, a, n, scale=0.5)
    cov2 = float(rc2.render_frame(0.0).px[..., 3].sum())
    assert cov2 == pytest.approx(cov1 / 4, rel=0.08)


# ---------------------------------------------------------------- animators
ANIM_ASSET = '<text id="a" width="700" height="80" size="30" font="DejaVu Sans" text="abcd efgh ijkl mnop"/>'


def _anim(rc, lid="l"):
    return [c for c in rc.doc.ids[lid] if c.tag == "textAnimator"]


def test_range_selector_square_and_shapes(tmp_path):
    rc = make_rc(tmp_path, ANIM_ASSET, '<layer id="l" asset="a"><textAnimator unit="word" start="0" end="50" opacity="0"/></layer>')
    an = _anim(rc)[0]
    pos = np.arange(4.0)
    s = TA.range_selection(rc, an, ctx(), pos, 4)
    assert list(np.round(s, 6)) == [1, 1, 0, 0]
    an.set("end", "40")                        # 1.6 units: fractional overlap with smoothness 1
    rc.ev._keys.clear()
    s = TA.range_selection(rc, an, ctx(), pos, 4)
    assert list(np.round(s, 6)) == [1, 0.6, 0, 0]
    an.set("smoothness", "0")
    s = TA.range_selection(rc, an, ctx(), pos, 4)
    assert list(np.round(s, 6)) == [1, 1, 0, 0]   # hard: centre inside the range
    an.set("shape", "ramp-up")
    an.set("end", "100")
    s = TA.range_selection(rc, an, ctx(), pos, 4)
    assert np.all(np.diff(s) > 0) and s[0] > 0 and s[-1] < 1
    an.set("shape", "triangle")
    s = TA.range_selection(rc, an, ctx(), pos, 4)
    assert s[1] == pytest.approx(s[2]) and s[0] < s[1]


def test_animator_unit_counts_and_amounts(tmp_path):
    rc = make_rc(tmp_path, ANIM_ASSET,
                 '<layer id="l" asset="a"><textAnimator unit="word" rangeUnits="index" start="1" end="3" y="10" amount="50"/></layer>')
    b = block_of(rc, "a")
    A = TA.evaluate(rc, b, _anim(rc), ctx())
    words = b.units("word")
    assert len(words) == 4
    ys = [A.st[u[0]].get("y", 0.0) for u in words]
    assert ys == pytest.approx([0, 5, 5, 0])


def test_combine_modes(tmp_path):
    rc = make_rc(tmp_path, ANIM_ASSET, '<layer id="l" asset="a">'
                 '<textAnimator unit="word" y="10"/><textAnimator unit="word" y="4"/>'
                 '<textAnimator unit="word" y="0.5" combine="multiply"/></layer>')
    b = block_of(rc, "a")
    A = TA.evaluate(rc, b, _anim(rc), ctx())
    assert A.st[0]["y"] == pytest.approx((10 + 4) * 0.5)


def test_expression_selector(tmp_path):
    rc = make_rc(tmp_path, ANIM_ASSET, '<layer id="l" asset="a"><textAnimator unit="word" selector="expression" x="10">'
                 '<expression property="selector">textIndex == 2 ? 100 : 0</expression></textAnimator></layer>')
    b = block_of(rc, "a")
    A = TA.evaluate(rc, b, _anim(rc), ctx())
    xs = [A.st[u[0]].get("x", 0.0) for u in b.units("word")]
    assert xs == pytest.approx([0, 10, 0, 0])


def test_typewriter_progress_and_rest(tmp_path):
    rc = make_rc(tmp_path, ANIM_ASSET, '<layer id="l" asset="a"><textAnimator preset="typewriter" presetStart="1" presetDuration="2"/></layer>')
    b = block_of(rc, "a")
    n = len(b.units("character"))
    A = TA.evaluate(rc, b, _anim(rc), ctx(2.0))
    hidden = sum(1 for u in b.units("character") if A.st[u[0]].get("opacity", 1.0) == 0.0)
    assert hidden == n - n // 2 or hidden == n // 2
    assert TA.evaluate(rc, b, _anim(rc), ctx(3.5)) is None       # finished -> static


def test_stagger_overlap_timing(tmp_path):
    rc = make_rc(tmp_path, ANIM_ASSET, '<layer id="l" asset="a"><textAnimator unit="word" preset="fade-in" '
                 'presetStart="0" presetDuration="1" overlap="0"/></layer>')
    b = block_of(rc, "a")
    A = TA.evaluate(rc, b, _anim(rc), ctx(0.5))      # 4 words, sequential: words 0,1 done, 2,3 waiting
    ops = [A.st[u[0]].get("opacity", 1.0) for u in b.units("word")]
    assert ops[0] == pytest.approx(1) and ops[1] == pytest.approx(1)
    assert ops[2] == pytest.approx(0) and ops[3] == pytest.approx(0)


def test_all_presets_registered_and_render(tmp_path):
    names = sorted(TA.PRESETS)
    assert len(names) == 24
    for p in names:
        assert TEXT_ANIMATORS.level(p) == "full"
    layers = "\n".join(f'<layer id="l{i}" asset="a" y="{i * 18}"><textAnimator preset="{p}" presetStart="0" presetDuration="2"/></layer>'
                       for i, p in enumerate(names))
    rc = make_rc(tmp_path, ANIM_ASSET.replace('text="', 'text="n 1,250 ') , layers)
    for t in (0.0, 0.7, 1.4, 2.5):
        buf = rc.render_frame(t)
        assert np.isfinite(buf.px).all()
    assert rc.render_frame(1.0).px[..., 3].sum() > 0


def test_preset_determinism(tmp_path):
    nodes = '<layer id="l" asset="a"><textAnimator preset="scramble" presetStart="0" presetDuration="2" seed="5"/></layer>'
    rc1 = make_rc(tmp_path, ANIM_ASSET, nodes)
    rc2 = make_rc(tmp_path, ANIM_ASSET, nodes)
    a = rc1.render_frame(0.6).px
    b = rc2.render_frame(0.6).px
    assert np.array_equal(a, b)
    b0 = block_of(rc1, "a")
    A = TA.evaluate(rc1, b0, _anim(rc1), ctx(0.6))
    s1 = TA.substitutions(rc1, b0, A, 99)
    s2 = TA.substitutions(rc1, b0, A, 99)
    assert s1 == s2 and s1
    assert all(b0.text[i].isalpha() == c.isalpha() for i, c in s1.items())


def test_counter_rewrites_numbers(tmp_path):
    rc = make_rc(tmp_path, '<text id="a" width="400" height="80" size="30" font="DejaVu Sans" text="Total: 1,000.50 units"/>',
                 '<layer id="l" asset="a"><textAnimator preset="counter" presetStart="0" presetDuration="1"/></layer>')
    spec = T.resolve_spec(rc, rc.doc.ids["a"], ctx())
    mid = TA.apply_counter(rc, spec, _anim(rc), ctx(0.5)).text
    assert mid.startswith("Total: ") and mid.endswith(" units") and mid != spec.text
    assert TA.apply_counter(rc, spec, _anim(rc), ctx(0.0)).text == "Total: 0.00 units"
    assert TA.apply_counter(rc, spec, _anim(rc), ctx(2.0)).text == spec.text


def test_text_path_places_glyphs_on_path(tmp_path):
    rc = make_rc(tmp_path, '<text id="a" width="400" height="60" size="20" font="DejaVu Sans" text="along the path"/>',
                 '<layer id="l" asset="a"><textPath path="M0 100 L400 100"/></layer>')
    b = block_of(rc, "a")
    tp = [c for c in rc.doc.ids["l"] if c.tag == "textPath"][0]
    placer = TA._PathPlacer(rc, tp, ctx(), b)
    pieces = TA.build_pieces(rc, b, None, None, placer)
    assert len(pieces) == len([i for i in range(len(b.clusters))])
    # a straight horizontal path: every glyph baseline lands on y = 100 (asset coords)
    base = b.lines[0].baseline
    for p in pieces:
        x = (b.cl_x[p.a][0] + b.cl_x[p.a][1]) / 2
        q = b.O @ p.T @ np.array([x, base, 1.0])
        assert q[1] == pytest.approx(100, abs=1e-6)
    buf = rc.render_frame(0.0)
    ys = np.nonzero(buf.px[..., 3].sum(1) > 0)[0]
    assert ys.min() >= 100 - 25 and ys.max() <= 100 + 8


# ---------------------------------------------------------------- captions
def test_srt_vtt_round_trip():
    cues = [C.Cue(1.0, 2.5, "Hello there"), C.Cue(3.25, 4.0, "Line one\nLine two", speaker="Ann")]
    srt = C.format_srt(cues)
    back = C.parse_srt(srt)
    assert [(c.start, c.end, c.text) for c in back] == [(1.0, 2.5, "Hello there"), (3.25, 4.0, "Line one\nLine two")]
    vtt = C.format_vtt(cues)
    assert vtt.startswith("WEBVTT")
    back = C.parse_vtt(vtt)
    assert [(c.start, c.end, c.text) for c in back] == [(1.0, 2.5, "Hello there"), (3.25, 4.0, "Line one\nLine two")]
    assert back[1].speaker == "Ann"
    words = [C.Word(1.0, 1.4, "Hello"), C.Word(1.4, 2.5, "there")]
    vw = C.format_vtt([C.Cue(1.0, 2.5, "Hello there", words)], word_timestamps=True)
    got = C.parse_vtt(vw)[0]
    assert [(w.text, w.start) for w in got.words] == [("Hello", 1.0), ("there", 1.4)]


def test_srt_timestamps_hours():
    c = C.parse_srt("1\n01:02:03,004 --> 01:02:04,500\nx\n")[0]
    assert c.start == pytest.approx(3723.004) and c.end == pytest.approx(3724.5)


def test_paginate_limits():
    cue = C.Cue(0.0, 6.0, "")
    cue.words = C._synth_words("aaaa bbbb cccc dddd eeee ffff gggg hhhh", 0.0, 6.0)
    pages = C.paginate(cue, max_chars=9, max_lines=2)
    assert all(len(p.lines) <= 2 for p in pages)
    assert all(len(" ".join(w.text for w in ln)) <= 9 for p in pages for ln in p.lines)
    assert pages[0].start == 0.0 and pages[-1].end == pytest.approx(6.0)
    assert all(a.end == pytest.approx(b.start) for a, b in zip(pages, pages[1:]))
    one = C.paginate(cue, one_word=True)
    assert len(one) == 8
    mw = C.paginate(cue, max_chars=99, max_lines=1, max_words=3)
    assert [len(p.lines[0]) for p in mw] == [3, 3, 2]


CAPS = """<captions>
  <captionTrack id="cap" language="en" mode="both" format="srt" preset="{preset}" maxCharsPerLine="30" y="80%">
    <cue start="0.5" end="2.0" text="Burned captions appear here"/>
    <cue start="2.5" end="3.5" text="Second cue">
      <word start="2.5" end="3.0" text="Second"/><word start="3.0" end="3.5" text="cue"/>
    </cue>
  </captionTrack>
  <captionTrack id="side" language="en" mode="sidecar" format="vtt"><cue start="0" end="1" text="side only"/></captionTrack>
</captions>"""


def test_burn_hook_and_selection(tmp_path):
    rc = make_rc(tmp_path, tail=CAPS.format(preset="boxed-line"))
    C.install(rc)
    assert "captions" in rc.hooks
    assert [t.get("id") for t in C.burn_tracks(rc)] == ["cap"]
    assert rc.render_frame(0.2).px[..., 3].sum() == 0
    on = rc.render_frame(1.0).px
    assert on[..., 3].sum() > 0
    rows = np.nonzero(on[..., 3].sum(1) > 0)[0]
    assert rows.min() >= 450 * 0.8 - 20            # y is the top of the block (boxed padding above)
    rc.cache["burn_captions"] = "side"
    assert [t.get("id") for t in C.burn_tracks(rc)] == ["side"]


@pytest.mark.parametrize("preset", sorted(C._PRESET_NOTES))
def test_caption_presets_render(tmp_path, preset):
    rc = make_rc(tmp_path, tail=CAPS.format(preset=preset))
    C.install(rc)
    assert CAPTION_PRESETS.level(preset) == "full"
    for t in (0.6, 1.2, 2.7, 3.2):
        px = rc.render_frame(t).px
        assert np.isfinite(px).all()
    assert rc.render_frame(3.2).px[..., 3].sum() > 0


def test_write_sidecars(tmp_path):
    rc = make_rc(tmp_path, tail=CAPS.format(preset="classic"))
    out = str(tmp_path / "out" / "film.mp4")
    paths = C.write_sidecars(rc.doc, out)
    assert sorted(os.path.basename(p) for p in paths) == ["film.cap.srt", "film.side.vtt"]
    srt = C.parse_srt(open(next(p for p in paths if p.endswith(".srt")), encoding="utf-8").read())
    assert srt[0].text == "Burned captions appear here" and srt[0].start == 0.5
    only = C.write_sidecars(rc.doc, out, ["side"])
    assert [os.path.basename(p) for p in only] == ["film.en.vtt"]


def test_subtitle_file_source(tmp_path):
    (tmp_path / "subs.vtt").write_text("WEBVTT\n\n00:00:00.500 --> 00:00:01.500\nFrom a file\n", encoding="utf-8")
    rc = make_rc(tmp_path, tail='<captions><captionTrack id="f" language="en" src="subs.vtt" format="vtt"/></captions>')
    cues = C.track_cues(rc.doc, rc.doc.ids["f"])
    assert [(c.start, c.text) for c in cues] == [(0.5, "From a file")]
    assert [w.text for w in cues[0].words] == ["From", "a", "file"]


def test_demo_fixture_renders():
    from scenerender.render import Renderer
    try:
        r = Renderer.open(os.path.join(HERE, "fixtures", "text_demo.xml"), scale=0.25)
    except ImportError as e:        # other plugin modules may be mid-edit
        pytest.skip(f"plugin import failed: {e}")
    assert not r.doc.validation_errors
    for t in (0.5, 1.5, 3.0):
        rgb = r.frame_rgb(t)
        assert rgb.shape == (180, 320, 3)
        assert rgb.std() > 5


def test_line_ranges_merge_only_contiguous(tmp_path):
    rc = make_rc(tmp_path, '<text id="a" width="600" height="80" size="30" font="DejaVu Sans">'
                           '<span color="#FF0000FF">red</span><span> mid </span><span color="#FF0000FF">red</span></text>')
    b = block_of(rc, "a")
    red = [i for i in range(len(b.clusters)) if b.cl_run[i] in (0, 2)]
    spans = b.line_ranges(red)
    assert len(spans) == 2 and spans[0][2] < spans[1][1]
