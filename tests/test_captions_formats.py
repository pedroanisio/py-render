"""Caption sources: ASS/SSA, TTML/IMSC, SCC (CEA-608), transcription caches, profanity, cue position."""
from __future__ import annotations

import hashlib
import json
import os

import numpy as np
import pytest

from scenerender import captions as C
from scenerender import document
from scenerender.compositor import RenderContext
from scenerender.evaluator import Evaluator
from scenerender.nodes import core as _core  # noqa: F401  (registers layer/group handlers)
from scenerender.registry import FEATURES

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures", "captions")

DOC = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="800" height="450" fps="30" duration="8" seed="1"/>
  <composition/>
{captions}
</scene>
"""


def make_rc(tmp_path, tracks, scale=1.0, strict=True):
    p = tmp_path / "c.xml"
    p.write_text(DOC.format(captions=f"  <captions>{tracks}</captions>" if tracks else ""), encoding="utf-8")
    doc = document.load(str(p), strict=strict)
    assert not strict or not doc.validation_errors, doc.validation_errors[:3]
    rc = RenderContext(doc, Evaluator(doc), scale=scale)
    C.install(rc)
    return rc


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def test_levels_full():
    for f in ("captions:src:ass", "captions:src:ttml", "captions:src:itt", "captions:src:scc",
              "captions:transcribe", "captions:profanityFilter"):
        assert FEATURES.level(f) == "full", f


# ====================================================================== ASS
def _ass_blocks(rc, cue):
    return C.rich_blocks(rc, cue)


def test_ass_colour_and_times():
    assert C._ass_color("&H0000D7FF") == "#FFD700FF"
    assert C._ass_color("&H80000000") == "#0000007F"
    assert C._ass_color("&HFF") == "#FF0000FF"
    assert C._ass_ts("0:00:01.50") == 1.5


def test_ass_parse_styles_positions(tmp_path):
    rc = make_rc(tmp_path, "")
    cues = C.parse_ass(read("sample.ass"))
    assert [c.text.split("\n")[0][:5] for c in cues] == ["Plain", "Top s", "Boxed", "Karao", "Secon"]
    assert cues[0].speaker == "Alice"
    W, H = 800.0, 450.0
    plain = _ass_blocks(rc, cues[0])[0]
    # Default: alignment 2 = bottom centre, MarginV 20 of 360 -> 25 px of 450
    assert plain.an == 2 and plain.x == pytest.approx(W / 2) and plain.y == pytest.approx(H - 25)
    styles = [s.st for s in plain.segs]
    assert any(s["weight"] == 700 for s in styles) and any(s["fontStyle"] == "italic" for s in styles)
    assert any(s.get("decoration") == "underline" for s in styles)
    red = next(s for s in plain.segs if s.text == "red")
    assert red.st["color"] == "#FF0000FF"
    after = plain.segs[plain.segs.index(red) + 1]
    assert after.st["color"] == "#FFFFFFFF"                              # \r reset
    assert red.st["strokeColor"] == "#000000FF" and red.st["strokeWidth"] == pytest.approx(2 * H / 360)
    sign = _ass_blocks(rc, cues[1])[0]
    assert sign.an == 8 and sign.y == pytest.approx(15 * H / 360)       # top, MarginV 15
    assert sign.segs[0].st["color"] == "#FFD700FF" and sign.segs[0].st["fontStyle"] == "italic"
    box = _ass_blocks(rc, cues[2])[0]
    assert (box.x, box.y, box.an) == (pytest.approx(40 * W / 640), pytest.approx(120 * H / 360), 7)
    assert box.line_bg == "#0000005F" and not box.collide               # BorderStyle 3 box, \pos
    kar = _ass_blocks(rc, cues[3])[0]
    assert (kar.x, kar.y, kar.an) == (pytest.approx(600), pytest.approx(225), 5)
    assert [s.k[0] for s in kar.segs] == ["k", "k", "kf", "k"]
    assert kar.segs[2].k[1:] == (pytest.approx(1.8), pytest.approx(2.6))


def test_ass_font_size_uses_real_dimensions(tmp_path):
    rc = make_rc(tmp_path, "")
    cue = C.parse_ass(read("sample.ass"))[0]
    st = _ass_blocks(rc, cue)[0].segs[0].st
    ratio = C._font_ratio(rc, "DejaVu Sans", 400, False)
    assert 1.05 < ratio < 1.3
    assert st["size"] == pytest.approx(24 * 450 / 360 / ratio)


def test_ass_fades_and_moves(tmp_path):
    rc = make_rc(tmp_path, "")
    script = """[Script Info]
PlayResX: 800
PlayResY: 450

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\fad(500,1000)}fade
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\fade(255,0,128,0,500,1500,2000)\\move(100,100,300,200,0,1000)}complex
"""
    a, b = C.parse_ass(script)
    fa = _ass_blocks(rc, a)[0]
    assert C._alpha_at(fa, 1.0) == pytest.approx(0.0)
    assert C._alpha_at(fa, 1.25) == pytest.approx(0.5)
    assert C._alpha_at(fa, 2.0) == pytest.approx(1.0)
    assert C._alpha_at(fa, 2.5) == pytest.approx(0.5)
    fb = _ass_blocks(rc, b)[0]
    assert C._alpha_at(fb, 1.25) == pytest.approx(0.5)
    assert C._alpha_at(fb, 2.25) == pytest.approx(1.0)
    assert C._alpha_at(fb, 2.75) == pytest.approx((1 + (1 - 128 / 255)) / 2, abs=0.01)
    assert C._alpha_at(fb, 3.0) == pytest.approx(1 - 128 / 255)
    assert fb.move[:4] == (100, 100, 300, 200) and fb.move[4:] == (pytest.approx(1.0), pytest.approx(2.0))
    # default style when the script has none: 20 px Arial -> DejaVu fallback, alignment 2
    assert fa.an == 2


def test_ass_default_playres_and_wrapstyle(tmp_path):
    rc = make_rc(tmp_path, "")
    script = "[Script Info]\nWrapStyle: 2\n\n[Events]\nFormat: Layer, Start, End, Style, Text\n" \
             "Dialogue: 0,0:00:00.00,0:00:01.00,Default,{\\pos(192,144)\\an5}a\\nb\n"
    cue = C.parse_ass(script)[0]
    blk = _ass_blocks(rc, cue)[0]
    assert (blk.x, blk.y) == (pytest.approx(400), pytest.approx(225))     # 384 x 288 script space
    assert blk.wrap == "none" and blk.width is None and blk.segs[0].text == "a\nb"


def test_ass_burn_karaoke_wipe_and_collisions(tmp_path):
    (tmp_path / "s.ass").write_text(read("sample.ass"), encoding="utf-8")
    rc = make_rc(tmp_path, '<captionTrack id="a" language="en" src="s.ass" format="ass"/>')
    px = rc.render_frame(2.2).px
    assert px[..., 3].sum() > 0
    cues = C.track_cues(rc.doc, rc.doc.ids["a"])
    kar = next(c for c in cues if c.text == "Karaoke")
    rb = C.rich_blocks(rc, kar)[0]
    buf = C.render_rich_block(rc, rb, 2.2)
    a = buf.px[..., 3]
    straight = buf.px[..., :3] / np.maximum(a[..., None], 1e-6)
    fill = (a > 0.95) & (straight[..., 0] > 0.8)                       # white or yellow glyph fill
    col = np.where(fill[..., None], straight, np.nan)
    # linear-light yellow (1, 1, 0) vs white (1, 1, 1): the blue channel tells sung from unsung
    has = fill.any(axis=0)
    blue = np.full(fill.shape[1], np.nan)
    blue[has] = np.nanmean(col[:, has, 2], axis=0)
    xs = np.nonzero(has)[0]
    left, right = blue[xs[: len(xs) // 3]], blue[xs[-len(xs) // 3:]]
    assert np.nanmean(left) > 0.8 and np.nanmean(right) < 0.2
    # two bottom-aligned cues on screen: the later one sits above the earlier one
    t = 2.0
    plain = next(c for c in cues if c.text.startswith("Plain"))
    second = next(c for c in cues if c.text.startswith("Second"))
    bufs = C.render_rich(rc, [plain, second], t)
    assert len(bufs) == 2
    y_plain = bufs[0].y0
    y_second = bufs[1].y0
    assert y_second < y_plain


def test_ass_sidecar_text(tmp_path):
    (tmp_path / "s.ass").write_text(read("sample.ass"), encoding="utf-8")
    rc = make_rc(tmp_path, '<captionTrack id="a" language="en" src="s.ass" format="ass" mode="both"/>')
    cues = C.sidecar_cues(rc.doc, rc.doc.ids["a"])
    assert cues[0].text.startswith("Plain bold, italic") and cues[0].start == pytest.approx(0.2)


# ====================================================================== TTML
def test_ttml_time_expressions():
    from lxml import etree
    root = etree.fromstring(b'<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" '
                            b'ttp:frameRate="30" ttp:frameRateMultiplier="1000 1001" ttp:tickRate="90000" '
                            b'ttp:subFrameRate="2"/>')
    t = C._TTMLTime(root)
    assert t("00:00:01:15") == pytest.approx(1 + 15 / (30000 / 1001))
    assert t("00:00:01:15.1") == pytest.approx(1 + 15.5 / (30000 / 1001))
    assert t("90000t") == pytest.approx(1.0)
    assert t("1.5s") == 1.5 and t("250ms") == 0.25 and t("2m") == 120 and t("30f") == pytest.approx(30 / (30000 / 1001))
    assert t("00:01:02.5") == pytest.approx(62.5)
    smpte = C._TTMLTime(etree.fromstring(
        b'<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" ttp:frameRate="30" '
        b'ttp:frameRateMultiplier="1000 1001" ttp:timeBase="smpte" ttp:dropMode="dropNTSC"/>'))
    # 00:01:00;02 is the first label after the drop at minute 1: frame 1800
    assert smpte("00:01:00:02") == pytest.approx(1800 / (30000 / 1001))
    assert smpte("00:10:00:00") == pytest.approx(17982 / (30000 / 1001))


def test_ttml_intervals_styles_and_regions(tmp_path):
    rc = make_rc(tmp_path, "")
    cues = C.parse_ttml(read("sample.ttml"))
    spans = [(round(c.start, 3), round(c.end, 3)) for c in cues]
    assert spans == [(0.2, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, 3.5)]
    assert cues[0].text == "First line in bold\nand a yellow italic word"
    assert cues[2].text.endswith("Words") and cues[3].text.endswith("Words appear")     # spans appear later
    blocks = C.rich_blocks(rc, cues[1])
    bottom = next(b for b in blocks if b.flow[0] == "bottom")
    top = next(b for b in blocks if b.flow[0] == "top")
    assert bottom.flow[5] == "after" and bottom.align == "center"
    assert top.align == "left" and top.region_bg == "#00000080"
    assert top.flow[6] == pytest.approx((8 * 450 / 720, 8 * 800 / 1280, 8 * 450 / 720, 8 * 800 / 1280))
    segs = {s.text.strip(): s.st for s in bottom.segs if s.text.strip()}
    assert segs["bold"]["weight"] == 700
    yel = segs["yellow italic"]
    assert yel["color"] == "#FFFF00FF" and yel["fontStyle"] == "italic"
    assert yel["strokeColor"] == "#000000FF"                                 # inherited from "base"
    # "yellow" chains "base" (fontSize 80%), which applies again relative to the parent's 0.8c
    assert yel["size"] == pytest.approx(0.64 * 450 / 15)
    assert segs["bold"]["size"] == pytest.approx(0.8 * 450 / 15)
    boxed = next(s for s in top.segs if s.text == "boxed")
    assert boxed.st["highlight"] == "#C00000FF"
    # whitespace: no blank line around <br/>
    assert [s.text for s in bottom.segs].count("\n") == 1
    texts = [s.text for s in bottom.segs]
    br = texts.index("\n")
    assert not texts[br + 1].startswith(" ") and not texts[br - 1].endswith(" ")


def test_ttml_burn_positions(tmp_path):
    (tmp_path / "s.ttml").write_text(read("sample.ttml"), encoding="utf-8")
    rc = make_rc(tmp_path, '<captionTrack id="t" language="en" src="s.ttml" format="ttml"/>')
    px = rc.render_frame(1.7).px
    a = px[..., 3]
    rows = np.nonzero(a.sum(1) > 0)[0]
    # top region background (5%..20% of the height) and bottom-aligned text in 75%..95%
    assert rows.min() == pytest.approx(0.05 * 450, abs=2)
    assert a[int(0.97 * 450):].sum() == 0 and a[int(0.8 * 450):int(0.95 * 450)].sum() > 0
    assert a[int(0.3 * 450):int(0.7 * 450)].sum() == 0


def test_ttml_seq_container_and_default_region(tmp_path):
    rc = make_rc(tmp_path, "")
    ttml = """<tt xmlns="http://www.w3.org/ns/ttml"><body><div timeContainer="seq">
      <p dur="1s">one</p><p dur="2s">two</p><p begin="0.5s" dur="1s">three</p></div></body></tt>"""
    cues = C.parse_ttml(ttml)
    assert [(c.start, c.end, c.text) for c in cues] == [(0.0, 1.0, "one"), (1.0, 3.0, "two"), (3.5, 4.5, "three")]
    b = C.rich_blocks(rc, cues[0])[0]
    # no regions: the root region, displayAlign before (top), textAlign start
    assert b.flow[1:5] == (0.0, 0.0, 800.0, 450.0) and b.flow[5] == "before" and b.align == "start"


def test_ttml_missing_region_not_presented(tmp_path):
    rc = make_rc(tmp_path, "")
    ttml = """<tt xmlns="http://www.w3.org/ns/ttml"><head><layout><region xml:id="r1"/></layout></head>
      <body><p begin="0s" end="1s" region="nope">hidden</p><p begin="0s" end="1s" region="r1">shown</p></body></tt>"""
    cues = C.parse_ttml(ttml)
    blocks = C.rich_blocks(rc, cues[0])
    assert ["".join(s.text for s in b.segs) for b in blocks] == ["shown"]


# ====================================================================== SCC
def test_scc_decodes_pop_roll_paint():
    cues = C.parse_scc(read("sample.scc"))
    fps = 30000 / 1001
    pop = cues[0]
    assert pop.start == pytest.approx(C._scc_time("00:00:01:00") + 27 / fps)     # EOC: the 28th pair
    assert pop.end == pytest.approx(C._scc_time("00:00:02:15"))  # EDM
    assert pop.text == "POP-ON CAPTION\nSeñor  GRÜN♪"
    rows = pop.rich[0][1]
    assert [r for r, _ in rows] == [14, 15]
    first = next(i for i, c in enumerate(rows[0][1]) if c)
    assert first == 4                                             # PAC indent 4
    styles = {c[0]: c[1] for c in rows[1][1] if c}
    assert styles["S"][0] == "yellow" and styles["G"][1] is True  # yellow row, italics after the mid-row code
    roll = [c for c in cues if c.start >= 3.0 and c.start < 5.0]
    assert roll[0].text == "RO" and roll[0].start == pytest.approx(C._scc_time("00:00:03:00") + 4 / fps)
    texts = [c.text for c in roll]
    assert "ROLL UP ONE\nLINE TWO" in texts
    last_roll = roll[-1]
    assert last_roll.text == "LINE TWO\nLINE THREE"               # RU2 keeps two rows
    paint = [c for c in cues if c.start >= 5.0]
    assert paint[-1].text == "PAINT ON" and paint[-1].end == pytest.approx(C._scc_time("00:00:06:00"))
    assert paint[0].text == "PA"                                   # paint-on shows characters as they arrive
    assert paint[-1].rich[0][1][0][0] == 2                          # row 2


def test_scc_parity_redundancy_and_drop_frame():
    assert C._scc_time("00:01:00;02") == pytest.approx(1800 / (30000 / 1001))
    assert C._scc_time("00:00:01:00") == pytest.approx(30 / (30000 / 1001))
    # "Hi", then one backspace sent twice (a redundant pair executes once), paint-on mode
    scc = "Scenarist_SCC V1.0\n\n00:00:00:00\t9429 9429 94d0 94d0 c8e9 9421 9421 6180\n"
    cues = C.parse_scc(scc)
    assert cues[-1].text == "Ha"


def test_scc_burn_grid(tmp_path):
    (tmp_path / "s.scc").write_text(read("sample.scc"), encoding="utf-8")
    rc = make_rc(tmp_path, '<captionTrack id="s" language="en" src="s.scc" format="scc"/>')
    px = rc.render_frame(2.0).px
    a = px[..., 3]
    rows = np.nonzero(a.sum(1) > 0.5)[0]
    cell = 450 * 0.8 / 15
    assert rows.min() >= 0.1 * 450 + 13 * cell - 2 and rows.max() <= 0.1 * 450 + 15 * cell + 2
    # opaque black boxes behind the rows
    box = px[int(0.1 * 450 + 13.5 * cell), :, :]
    assert (box[..., 3] > 0.99).sum() > 50


# ====================================================================== transcription caches
SHAPES = {
    "whisper": {"text": "Hello world.", "segments": [{"start": 0.0, "end": 1.2, "text": " Hello world.",
                                                      "words": [{"word": " Hello", "start": 0.0, "end": 0.5},
                                                                {"word": " world.", "start": 0.6, "end": 1.2}]}]},
    "list": [{"start": 0.0, "end": 1.2, "text": "Hello world."}],
    "openai-words": {"words": [{"word": "Hello", "start": 0.0, "end": 0.5}, {"word": "world.", "start": 0.6, "end": 1.2}]},
    "whisperx": {"word_segments": [{"word": "Hello", "start": 0.0, "end": 0.5}, {"word": "world.", "start": 0.6, "end": 1.2}]},
    "deepgram": {"results": {"channels": [{"alternatives": [{"transcript": "hello world", "words": [
        {"word": "hello", "punctuated_word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "world", "punctuated_word": "world.", "start": 0.6, "end": 1.2}]}]}]}},
    "aws": {"results": {"items": [
        {"start_time": "0.0", "end_time": "0.5", "type": "pronunciation", "alternatives": [{"content": "Hello"}]},
        {"start_time": "0.6", "end_time": "1.2", "type": "pronunciation", "alternatives": [{"content": "world"}]},
        {"type": "punctuation", "alternatives": [{"content": "."}]}]}},
    "google": {"results": [{"alternatives": [{"transcript": "Hello world.", "words": [
        {"startTime": "0s", "endTime": "0.500s", "word": "Hello"},
        {"startTime": "0.600s", "endTime": "1.200s", "word": "world."}]}]}]},
    "rev": {"monologues": [{"speaker": 0, "elements": [
        {"type": "text", "value": "Hello", "ts": 0.0, "end_ts": 0.5}, {"type": "punct", "value": " "},
        {"type": "text", "value": "world", "ts": 0.6, "end_ts": 1.2}, {"type": "punct", "value": "."}]}]},
    "assemblyai": {"audio_duration": 2, "words": [{"text": "Hello", "start": 0, "end": 500},
                                                  {"text": "world.", "start": 600, "end": 1200}]},
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_cache_json_shapes(shape):
    cues = C.cache_json_cues(SHAPES[shape])
    assert len(cues) == 1
    c = cues[0]
    assert c.text == "Hello world." and c.start == pytest.approx(0.0) and c.end == pytest.approx(1.2)
    if shape != "list":
        assert [w.text for w in c.words] == ["Hello", "world."]
        assert c.words[1].start == pytest.approx(0.6) and c.timed


def test_cache_word_grouping_and_sha(tmp_path):
    words = [{"word": w, "start": s, "end": s + 0.3} for w, s in
             [("One", 0.0), ("two.", 0.4), ("Three", 0.8), ("four", 2.5), ("five", 2.9)]]
    cues = C.cache_json_cues({"words": words})
    assert [c.text for c in cues] == ["One two.", "Three", "four five"]      # sentence end, 1 s pause
    raw = json.dumps(SHAPES["whisper"]).encode()
    (tmp_path / "cache.json").write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    rc = make_rc(tmp_path, f'<captionTrack id="c" language="en" cache="cache.json" cacheSha256="{sha}"/>'
                           f'<captionTrack id="bad" language="en" cache="cache.json" cacheSha256="{"0" * 64}"/>', strict=False)      # a cache without @transcribe has no source (C31)
    assert [c.text for c in C.track_cues(rc.doc, rc.doc.ids["c"])] == ["Hello world."]
    assert C.track_cues(rc.doc, rc.doc.ids["bad"]) == []
    assert rc.render_frame(0.5).px[..., 3].sum() > 0


# ====================================================================== profanity
def test_profanity_mask_policy():
    assert C.mask_profanity("Oh shit! You fucking bastard.") == "Oh s***! You f****** b******."
    assert C.mask_profanity("Scunthorpe classic assessment") == "Scunthorpe classic assessment"
    assert C.mask_profanity("SHIT") == "S***"


def test_profanity_env_list(tmp_path, monkeypatch):
    lst = tmp_path / "words.txt"
    lst.write_text("# custom\nfrack*\n!shit\n", encoding="utf-8")
    monkeypatch.setenv("SCENERENDER_PROFANITY_LIST", str(lst))
    assert C.mask_profanity("fracking shit fuck") == "f******* shit f***"
    lst.write_text("@only\nbanana\n", encoding="utf-8")
    assert C.mask_profanity("banana fuck") == "b***** fuck"


def test_profanity_filter_applies_to_every_source(tmp_path):
    (tmp_path / "s.ass").write_text("[Events]\nFormat: Layer, Start, End, Style, Text\n"
                                    "Dialogue: 0,0:00:00.00,0:00:02.00,Default,{\\b1}what the fuck{\\b0}\n",
                                    encoding="utf-8")
    rc = make_rc(tmp_path, '<captionTrack id="a" language="en" src="s.ass" format="ass" profanityFilter="true"/>'
                           '<captionTrack id="i" language="en" profanityFilter="true">'
                           '<cue start="0" end="1" text="no shit"/></captionTrack>')
    a = C.track_cues(rc.doc, rc.doc.ids["a"])[0]
    assert a.text == "what the f***"
    blk = C.rich_blocks(rc, a)[0]
    assert "".join(s.text for s in blk.segs) == "what the f***"
    i = C.track_cues(rc.doc, rc.doc.ids["i"])[0]
    assert i.text == "no s***" and [w.text for w in i.words] == ["no", "s***"]


# ====================================================================== cue position
def test_cue_position_forms(tmp_path):
    rc = make_rc(tmp_path, '<captionTrack id="p" language="en"><cue start="0" end="1" text="x"/></captionTrack>')
    tr = rc.doc.ids["p"]
    cue = C.Cue(0, 1, "x")
    base = C._placement(rc, tr, cue, 400.0, 40.0, 40.0)
    assert base == (200.0, pytest.approx(0.75 * 450), 400.0, "center")
    cue.position = "top"
    assert C._placement(rc, tr, cue, 400.0, 40.0, 40.0)[1] == 0.0
    cue.position = "bottom"
    assert C._placement(rc, tr, cue, 400.0, 40.0, 40.0)[1] == 410.0
    cue.position = "25% 10%"
    assert C._placement(rc, tr, cue, 400.0, 40.0, 40.0)[:2] == (0.0, 45.0)
    cue.position = "line:10% position:20% align:start size:50%"
    left, top, width, align = C._placement(rc, tr, cue, 400.0, 40.0, 40.0)
    assert (left, top, width, align) == (pytest.approx(160.0), pytest.approx(45.0), 400.0, "start")
    cue.position = "line:-1"
    assert C._placement(rc, tr, cue, 400.0, 40.0, 40.0)[1] == pytest.approx(410.0)


def test_vtt_settings_fill_position(tmp_path):
    cues = C.parse_vtt("WEBVTT\n\n00:00:00.000 --> 00:00:01.000 line:0 align:start\nTop left\n")
    assert cues[0].position == "line:0 align:start"
    (tmp_path / "s.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000 line:0\nAt the top\n", encoding="utf-8")
    rc = make_rc(tmp_path, '<captionTrack id="v" language="en" src="s.vtt" format="vtt"/>')
    a = rc.render_frame(0.5).px[..., 3]
    rows = np.nonzero(a.sum(1) > 0)[0]
    assert rows.min() < 60


@pytest.mark.parametrize("fmt", ["ass", "ttml", "scc"])
def test_burn_fixtures(fmt):
    from scenerender.render import Renderer
    try:
        r = Renderer.open(os.path.join(FIX, f"burn_{fmt}.xml"), scale=0.25)
    except ImportError as e:        # other plugin modules may be mid-edit
        pytest.skip(f"plugin import failed: {e}")
    assert not r.doc.validation_errors
    lit = [r.frame_rgb(t).astype(float) for t in (0.0, 2.0)]
    assert np.abs(lit[1] - lit[0]).max() > 50            # captions burned at 2 s


def test_ttml_vertical_region_columns(tmp_path):
    (tmp_path / "v.ttml").write_text(read("vertical.ttml"), encoding="utf-8")
    rc = make_rc(tmp_path, '<captionTrack id="v" language="ja" src="v.ttml" format="ttml"/>')
    cues = C.track_cues(rc.doc, rc.doc.ids["v"])
    region = next(c for c in cues if not c.text)            # showBackground="always": the whole timeline
    assert region.start == 0 and region.end == float("inf")
    cue = next(c for c in cues if c.text)
    blocks = C.rich_blocks(rc, cue)
    assert all(b.writing == "vertical-rl" for b in blocks) and blocks[0].line_len == pytest.approx(0.8 * 450)
    bufs = C.render_rich(rc, [region, cue], 1.0)
    assert len(bufs) == 3
    def ink(b):
        cols = np.nonzero(b.px[..., 3].sum(0) > 0)[0] + b.x0
        rows = np.nonzero(b.px[..., 3].sum(1) > 0)[0] + b.y0
        return cols.min(), cols.max(), rows.min(), rows.max()
    f, s2 = ink(bufs[1]), ink(bufs[2])                     # bufs[0] is the region background
    assert f[0] > s2[1]                                    # the first p is the rightmost column
    assert f[1] <= 0.95 * 800 and s2[0] >= 0.8 * 800
    assert (f[3] - f[2]) > 4 * (f[1] - f[0])               # a tall column


def test_ttml_relative_font_sizes():
    assert C._relative_size("2c", "50%") == "1c"
    assert C._relative_size("30px", "1.5em") == "45px"
    assert C._relative_size("1c 2c", "50%") == "1c"
