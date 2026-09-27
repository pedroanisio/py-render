"""Captions: burned-in caption rendering (a compositor hook) and sidecar subtitle files.

Sources of a captionTrack's cues, all merged: inline <cue> elements, the subtitle file @src
(srt, vtt, ass/ssa, ttml/itt/dfxp, scc; @format, else the file extension) and the
transcription cache @cache (see "Transcription cache"). Running a speech recogniser is not the
renderer's job (the schema: "Transcriptions are read from @cache"); @transcribe only names the
audio track the cache was made from.

Plain cues (inline, srt, vtt, cache) -- pinned rules:
  * x is the horizontal centre and y the top of the caption block; width is the wrap width.
    Lengths are relative to the output frame. The block is kept inside the track's safe area
    (else the project's) by moving it.
  * cue/@position (and WebVTT cue settings, which fill it) overrides the placement:
    "X Y" (two lengths: centre x and top y), a keyword top | middle | bottom (the block's
    top / centre / bottom at the top / centre / bottom of the safe area, else of the frame),
    or WebVTT settings "line:L position:P size:S align:A" -- line N% puts the block's top
    (",center": centre, ",end": bottom) at N% of the frame height, line n (integer) is line
    n from the top (n < 0: from the bottom) in caption line heights; position P% anchors the
    block horizontally (",line-left" left edge, ",center" centre, ",line-right" right edge;
    default from align: start/left -> left edge, end/right -> right edge, else centre);
    size S% is the block width; align start|left|center|middle|end|right aligns the text.
  * Lines are filled greedily with whole words up to maxCharsPerLine characters and
    maxWordsPerLine words; a cue needing more than maxLines lines is split into pages.
    Pages with word timings start at their first word; otherwise the cue duration is
    shared out by character count. Cues without <word> children get word timings the
    same way (so word-based presets work on plain cues).
  * Caption time is composition time. Cue windows are half-open [start, end).
  * Styles: the track @style (or the cue's @style), else 4.4% of the frame height in
    white DejaVu Sans; the active word takes @activeStyle and @activeColor
    (default #FFD400). emphasis="true" words are drawn at weight 800.
  * Presets:
      classic     text as styled; a soft drop shadow is added when the style has neither
                  shadow nor stroke
      boxed-line  a #000000B3 box behind each line (padding 0.25 em, radius 0.2 em)
      boxed-word  the active word sits on an activeColor box (padding 0.12 em)
      one-word    only the active word is shown
      karaoke     spoken words take activeColor; the current word blends in over its duration
      highlight   the active word takes activeColor
      pop         the active word takes activeColor and pops (scale 1.15, back-out, 0.25 s)
      fade        each page fades in and out over 0.15 s
      bounce      each word drops in (0.3 em, bounce-out over 0.35 s) when spoken
      slide       each page slides up 0.4 em and fades in over 0.25 s
      typewriter  words appear as they are spoken
      enlarge     the active word scales to 1.2 over 0.1 s and takes activeColor
      none        text as styled, nothing added

Styled sources (ass/ssa, ttml/itt/dfxp, scc) carry their own presentation: their cues render
with the source's fonts, colours, positions and effects; @preset, @style, @activeStyle,
@activeColor, @x, @y, @width, @maxCharsPerLine, @maxLines, @maxWordsPerLine and @safeArea
apply to plain cues only (profanityFilter and z apply to all). Several styled cues may be
on screen at once (they are drawn in ASS Layer order, then source order).

ASS / SSA (Advanced SubStation Alpha v4+, v4):
  * [Script Info] PlayResX/PlayResY (both missing: 384 x 288; one missing: derived at 4:3,
    with 1280 x 1024 special-cased) map script coordinates to the frame (x * W / PlayResX,
    y * H / PlayResY); font sizes, \\fs and \\pos scale by H / PlayResY. WrapStyle 0 / 3 =
    balanced lines, 1 = greedy word wrap, 2 = no wrap (only \\N breaks); \\q overrides it.
    ScaledBorderAndShadow yes scales Outline/Shadow/\\bord/\\shad/\\be/\\blur with the script;
    no (VSFilter default) keeps them in output pixels.
  * [V4+ Styles] / [V4 Styles]: Fontname, Fontsize (the font's ascent + descent, like
    libass/VSFilter, so the em is Fontsize / ((ascent + descent) / em)), Primary (fill),
    Secondary (karaoke before the syllable), Outline, Back (shadow) colours as &HAABBGGRR
    (AA 00 = opaque), Bold (-1/1 = 700, other > 1 values are weights), Italic, Underline,
    StrikeOut, ScaleX/ScaleY (% : the block scales about its anchor; a \\fscx/\\fscy run differing
    from the style scales its font size by fscy and takes the nearest font stretch for the
    fscx/fscy ratio), Spacing (px), Angle (deg, counter-clockwise about the
    anchor or \\org), BorderStyle (1: outline + drop shadow; 3: an opaque box per line in
    the Outline colour, padded by Outline, its shadow in the Back colour), Outline, Shadow
    (offset px), Alignment (numpad; v4 legacy values converted), MarginL/R/V.
  * Dialogue: Layer, Start, End, Style, Name (speaker), MarginL/R/V (non-zero overrides),
    Text. Override tags: \\b \\i \\u \\s \\fn \\fs \\fsp \\fscx \\fscy \\frz / \\fr \\c / \\1c
    \\2c \\3c \\4c \\alpha \\1a..\\4a \\bord \\shad \\be \\blur \\an \\a \\pos \\move \\org \\fad
    \\fade \\k \\K / \\kf \\ko \\q \\r[style]; \\N (break), \\n (break with WrapStyle 2, else a
    space), \\h (no-break space). Positioning: \\pos / \\move place the anchor point given by
    the alignment; otherwise the anchor is at the margins (left / centred between the side
    margins / right; bottom MarginV / middle / top MarginV) and bottom- or top-aligned cues
    without \\pos / \\move that are on screen together (same Layer) stack away from their
    margin in start order (the libass collision rule). The wrap width
    is the frame width minus the side margins. \\fad(in, out) ramps the alpha over the
    first/last ms; \\fade(a1, a2, a3, t1, t2, t3, t4) follows the libass alpha envelope.
    Karaoke: \\k syllables switch from Secondary to Primary colour at their start, \\kf/\\K
    wipe left to right over their duration, \\ko also hides the outline until the syllable.
    \\be / \\blur blur the shadow (and the whole text when it has neither outline nor
    shadow) with sigma = value / 2 px. Other tags (\\t, \\clip, \\p drawings, \\frx, \\fry,
    \\fax, \\fay, \\xbord, ...) are reported once and ignored.

TTML / IMSC / iTT / DFXP (TTML1/TTML2 core styling, the IMSC 1.1 text profile):
  * Timing: begin/end/dur on body/div/p/span; timeContainer par (children relative to the
    parent's begin) and seq (each child starts at the previous child's end). Clock times
    hh:mm:ss(.fraction), hh:mm:ss:frames(.subframes); offsets with h m s ms f t.
    ttp:frameRate (default 30) x ttp:frameRateMultiplier ("1000 1001"), ttp:subFrameRate,
    ttp:tickRate (default frameRate x subFrameRate, else 1); ttp:timeBase="smpte" with
    ttp:dropMode dropNTSC / dropPAL converts drop-frame labels to frame counts.
  * The document is resolved into intermediate synchronic documents: at every begin/end the
    set of active p / span content is recomputed (inactive spans vanish and the text
    reflows, as TTML requires), giving one cue per interval.
  * Styling: <styling><style> with style chaining, @style references, inline tts:*,
    inheritance body > div > p > span. color, backgroundColor (span: a box behind its text;
    p: behind each line; region: its rectangle), fontFamily (generic names: default,
    sansSerif, proportionalSansSerif -> DejaVu Sans, serif, proportionalSerif -> DejaVu
    Serif, monospace, monospaceSansSerif -> DejaVu Sans Mono, monospaceSerif -> DejaVu
    Serif, as DejaVu has no monospaced serif), fontSize (px, %, em, c cells of
    ttp:cellResolution (default 32 x 15), rh, rw; the vertical value of a pair; default 1c),
    (% and em relative to the parent's size), fontWeight, fontStyle, textDecoration, textOutline ("[colour] thickness [blur]": an
    outside stroke, the blur a glow of that colour), textShadow (TTML2), lineHeight
    (normal = 125%, IMSC), textAlign (left center right start end; default start),
    displayAlign (before center after), opacity, visibility, display none, wrapOption,
    direction, writingMode (tbrl/tb -> vertical-rl, tblr -> vertical-lr: the region's p blocks
    then progress as columns, displayAlign across them), padding, origin, extent,
    showBackground (always: a coloured region shows over the whole timeline).
  * Layout: regions from <layout> (origin/extent in px of the root tts:extent, else of the
    frame; % of the frame); content goes to its (inherited) @region; with no regions
    declared everything goes to one region covering the frame; content whose region does not
    exist is not presented. The p elements of a region stack in document order inside the
    region's padding box, positioned by displayAlign; each p wraps to that width.

SCC (Scenarist, CEA-608 / EIA-608 caption data, CC1 -- or CC2 when the file has no CC1):
  * "hh:mm:ss:ff" (";" or "." before ff = drop frame) stamps the first byte pair of the
    line; every pair takes one frame at 30000/1001 fps. Odd parity is stripped, 0x8080
    fillers skipped, a repeated control pair ignored once (the redundancy rule).
  * Pop-on (RCL ... EOC), roll-up (RU2/RU3/RU4 with CR scrolling a window above the base
    row set by PAC) and paint-on (RDC) are decoded with EDM, ENM, BS, DER, TO1-TO3, PACs
    (row, indent, colour, underline), mid-row codes (colour, italics, underline; each takes
    a cell as a space), special and extended (Spanish/French/Portuguese/German/Danish)
    characters and the 608 character set. Every change of the displayed memory starts a
    new cue; content still displayed at the end of the file lasts until the end of the
    document.
  * Presentation: the 32 x 15 grid fills the central 80% x 80% (title safe) of the frame's
    central 4:3 area; each row is a line of DejaVu Sans Mono at 85% of the cell height with letterSpacing
    making every character one cell wide, placed at its row and column, on an opaque black
    box per row (the standard 608 look); white, green, blue, cyan, red, yellow, magenta,
    italics and underline as coded.

Transcription cache (@cache, verified against @cacheSha256; a mismatch skips the cache):
  a subtitle file (srt, vtt, ass, ttml, scc by extension) or JSON in any of these shapes --
    [cue, ...]  or  {"segments" | "cues" | "utterances": [cue, ...]}: cue = {start, end,
      text | transcript, words?: [word, ...], speaker?}  (Whisper, faster-whisper, WhisperX,
      OpenAI verbose_json, generic)
    {"words" | "word_segments": [word, ...]}: word = {start, end, word | text |
      punctuated_word, speaker?}  (OpenAI word granularity, WhisperX)
    {"results": {"channels": [{"alternatives": [{"words": [...]}]}]}, "utterances"?}  (Deepgram)
    {"results": {"items": [{"start_time", "end_time", "type", "alternatives": [{"content"}]}]}}
      (AWS Transcribe; punctuation items attach to the previous word)
    {"results": [{"alternatives": [{"words": [{"startTime" | "startOffset": "1.5s", ...}]}]}]}
      (Google Speech-to-Text)
    {"monologues": [{"speaker", "elements": [{"type": "text", "value", "ts", "end_ts"}]}]}  (Rev.ai)
  Times are seconds, except integer milliseconds when the object has "audio_duration"
  (AssemblyAI) or {"timeUnit": "ms"}. Word lists become cues at sentence ends (. ? ! ...),
  speaker changes and pauses longer than 1 s.

Profanity filter (profanityFilter="true"): words whose letters match the list are masked
  by keeping the first character and replacing every further letter or digit with "*"
  (punctuation kept: "Shit!" -> "S***!"). Matching is case-insensitive on the word without
  its surrounding punctuation; list entries are exact words or prefixes ("fuck*"). The
  built-in English list can be extended by a UTF-8 file named by the environment variable
  SCENERENDER_PROFANITY_LIST: one entry per line, "#" comments, "!word" removes an entry,
  a line "@only" makes the file replace the built-in list. Applies to every source.

Other pinned rules:
  * output/@burnCaptions: when rc.cache["burn_captions"] holds a track id only that track
    is burned; otherwise every track with mode burn or both is burned.
  * Sidecars are written as <output stem>.<language>.<srt|vtt> (the track id replaces the
    language when two tracks share it); formats other than srt/vtt are written as vtt.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field

import numpy as np

from . import curves
from .document import ln
from .raster import Buf
from .registry import CAPTION_PRESETS, FEATURES, FULL, warn_once
from .render import hook_installer
from .values import parse_length

log = logging.getLogger("scenerender")

_PRESET_NOTES = {
    "classic": "styled text; soft shadow added when the style has no shadow/stroke",
    "boxed-line": "#000000B3 box per line", "boxed-word": "activeColor box behind the active word",
    "one-word": "only the active word", "karaoke": "spoken words take activeColor",
    "highlight": "active word takes activeColor", "pop": "active word pops (1.15x)",
    "fade": "pages fade 0.15 s", "bounce": "words drop in when spoken", "slide": "pages slide up",
    "typewriter": "words appear when spoken", "enlarge": "active word scales to 1.2x", "none": "styled text only",
}
for _p, _n in _PRESET_NOTES.items():
    CAPTION_PRESETS.register(_p, level=FULL, note=_n)(None)
FEATURES.declare("captions:burn", FULL)
FEATURES.declare("captions:sidecar", FULL, "srt and vtt")
FEATURES.declare("captions:src:srt", FULL)
FEATURES.declare("captions:src:vtt", FULL, "inline <hh:mm:ss.mmm> word timestamps and cue settings are read")
FEATURES.declare("captions:src:ass", FULL, "styles, colours, b/i/u/s, \\an, \\pos/\\move, \\fad/\\fade, karaoke, collisions")
FEATURES.declare("captions:src:ttml", FULL, "IMSC text profile: styling, regions, alignment, frame/tick timing")
FEATURES.declare("captions:src:itt", FULL, "as ttml")
FEATURES.declare("captions:src:scc", FULL, "CEA-608 CC1 pop-on, roll-up, paint-on")
FEATURES.declare("captions:transcribe", FULL, "reads the @cache (JSON shapes documented in captions.py); "
                 "running ASR is outside the renderer per the schema")
FEATURES.declare("captions:profanityFilter", FULL, "built-in list + SCENERENDER_PROFANITY_LIST; first letter kept")

DEFAULT_ACTIVE = "#FFD400FF"


# ====================================================================== data
@dataclass
class Word:
    start: float
    end: float
    text: str
    emphasis: bool = False


@dataclass
class Seg:
    """A run of styled caption text (styled sources)."""
    text: str
    st: dict                              # characterStyle keys (assets.text.RUN_KEYS)
    k: tuple | None = None                # karaoke (kind "k" | "kf" | "ko", t0, t1), absolute seconds
    k_color: str | None = None            # fill before the syllable (ASS Secondary colour)


@dataclass
class RichBlock:
    """One positioned block of styled caption text (frame document px)."""
    segs: list
    x: float = 0.0                        # anchor point
    y: float = 0.0
    an: int = 2                           # numpad anchor of the block at (x, y)
    width: float | None = None            # wrap width (None: no wrapping)
    align: str = "center"                 # left | center | right | start | end
    wrap: str = "word"                    # word | balance | none
    line_bg: str | None = None            # per-line box paint
    line_pad: float = 0.0
    fade: tuple | None = None             # (a1, a2, a3, t1, t2, t3, t4): alpha 0..1, absolute times
    move: tuple | None = None             # (x1, y1, x2, y2, t1, t2), absolute times
    rot: float = 0.0                      # degrees, counter-clockwise
    org: tuple | None = None              # rotation origin (default: the anchor point)
    sx: float = 1.0
    sy: float = 1.0
    blur: float = 0.0                     # whole-block blur sigma (px)
    opacity: float = 1.0
    layer: int = 0
    collide: bool = False                 # ASS: stack with overlapping cues (no \pos / \move)
    margin_v: float = 0.0
    flow: tuple | None = None             # TTML: (region key, x, y, w, h, displayAlign, padding tuple)
    region_bg: str | None = None
    direction: str | None = None
    writing: str | None = None
    line_len: float | None = None         # vertical writing: the column length (box height)
    language: str | None = None


@dataclass
class Cue:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: str | None = None
    style: str | None = None
    timed: bool = False          # words carry real timings
    position: str | None = None
    rich: list | None = None     # RichBlocks: styled sources draw these instead of pages
    order: int = 0


@dataclass
class Page:
    start: float
    end: float
    lines: list[list[Word]]
    cue: Cue


# ====================================================================== timestamps
_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _ts(s: str) -> float:
    m = _TS.search(s)
    if not m:
        raise ValueError(f"bad timestamp {s!r}")
    h, mi, se, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")) / 1000.0


def _fmt_ts(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


# ====================================================================== SRT / WebVTT
_TAG = re.compile(r"<[^>]*>")
_INLINE_TS = re.compile(r"<((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})>")


def _vtt_text(raw: str, start: float, end: float) -> tuple[str, list[Word], str | None]:
    speaker = None
    m = re.match(r"\s*<v(?:\.[^ >]*)?\s+([^>]*)>", raw)
    if m:
        speaker = m.group(1).strip()
    words: list[Word] = []
    if _INLINE_TS.search(raw):
        parts = _INLINE_TS.split(raw)
        t = start
        for i, part in enumerate(parts):
            if i % 2 == 1:
                t = _ts(part)
                continue
            for w in _TAG.sub("", part).split():
                words.append(Word(t, t, w))
        for i, w in enumerate(words):
            w.end = words[i + 1].start if i + 1 < len(words) and words[i + 1].start > w.start else end
    text = _unescape(_TAG.sub("", raw)).strip()
    return text, words, speaker


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", "\u00a0").replace("&amp;", "&")


def parse_srt(text: str) -> list[Cue]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip()):
        lines = block.split("\n")
        ti = next((i for i, x in enumerate(lines) if "-->" in x), None)
        if ti is None:
            continue
        a, b = lines[ti].split("-->")
        body = "\n".join(lines[ti + 1:]).strip()
        s, e = _ts(a), _ts(b)
        txt, words, speaker = _vtt_text(body, s, e)
        cues.append(Cue(s, e, txt, words, speaker, timed=bool(words)))
    return cues


def parse_vtt(text: str) -> list[Cue]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        if lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")) and not any("-->" in x for x in lines):
            continue
        ti = next((i for i, x in enumerate(lines) if "-->" in x), None)
        if ti is None:
            continue
        a, rest = lines[ti].split("-->", 1)
        parts = rest.strip().split()
        s, e = _ts(a), _ts(parts[0])
        txt, words, speaker = _vtt_text("\n".join(lines[ti + 1:]), s, e)
        settings = " ".join(p for p in parts[1:] if ":" in p)
        cues.append(Cue(s, e, txt, words, speaker, timed=bool(words), position=settings or None))
    return cues


# ====================================================================== ASS / SSA
def _ass_color(v: str | None, default: str = "#FFFFFFFF") -> str:
    """&HAABBGGRR (or decimal) -> #RRGGBBAA (ASS alpha 00 = opaque)."""
    if v is None:
        return default
    s = str(v).strip().strip("&").lstrip("Hh").rstrip("&")
    try:
        n = int(s, 16) if re.fullmatch(r"[0-9A-Fa-f]+", s) else int(v)
    except ValueError:
        return default
    a, b, g, r = (n >> 24) & 255, (n >> 16) & 255, (n >> 8) & 255, n & 255
    return f"#{r:02X}{g:02X}{b:02X}{255 - a:02X}"


def _with_alpha(col: str, ass_alpha: int) -> str:
    return col[:7] + f"{255 - max(0, min(255, ass_alpha)):02X}"


def _ass_ts(s: str) -> float:
    h, m, rest = s.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


_V4_ALIGN = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9, 9: 4, 10: 5, 11: 6}


def _ass_bool(v) -> bool:
    try:
        return int(float(v)) != 0
    except (TypeError, ValueError):
        return False


def _ass_style(fields: dict, v4plus: bool) -> dict:
    g = fields.get
    bold = g("Bold", "0")
    try:
        b = int(float(bold))
    except ValueError:
        b = 0
    weight = 700 if b in (-1, 1) else (b if b > 1 else 400)
    align = int(float(g("Alignment", "2") or 2))
    if not v4plus:
        align = _V4_ALIGN.get(align, 2)
    return {
        "font": g("Fontname", "Arial"), "fs": float(g("Fontsize", "20") or 20),
        "c1": _ass_color(g("PrimaryColour"), "#FFFFFFFF"), "c2": _ass_color(g("SecondaryColour"), "#FF0000FF"),
        "c3": _ass_color(g("OutlineColour", g("TertiaryColour")), "#000000FF"),
        "c4": _ass_color(g("BackColour"), "#00000080"),
        "weight": weight, "italic": _ass_bool(g("Italic")), "underline": _ass_bool(g("Underline")),
        "strike": _ass_bool(g("StrikeOut")), "fscx": float(g("ScaleX", "100") or 100),
        "fscy": float(g("ScaleY", "100") or 100), "fsp": float(g("Spacing", "0") or 0),
        "frz": float(g("Angle", "0") or 0), "border_style": int(float(g("BorderStyle", "1") or 1)),
        "bord": float(g("Outline", "2") or 0), "shad": float(g("Shadow", "2") or 0), "an": align,
        "ml": float(g("MarginL", "10") or 0), "mr": float(g("MarginR", "10") or 0),
        "mv": float(g("MarginV", "10") or 0), "be": 0.0, "blur": 0.0,
    }


_DEFAULT_ASS_STYLE = _ass_style({}, True)


def parse_ass(text: str) -> list[Cue]:
    """ASS/SSA events as cues whose `rich` holds the script-space description ("ass" blocks are
    finalised against the frame in `ass_blocks`)."""
    info: dict[str, str] = {}
    styles: dict[str, dict] = {}
    section = ""
    sfmt = efmt = None
    events = []
    v4plus = True
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            if section == "v4 styles":
                v4plus = False
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if section == "script info":
            info[key.lower()] = val
        elif section in ("v4+ styles", "v4 styles"):
            if key == "Format":
                sfmt = [f.strip() for f in val.split(",")]
            elif key == "Style":
                fmt = sfmt or ["Name", "Fontname", "Fontsize", "PrimaryColour", "SecondaryColour", "OutlineColour",
                               "BackColour", "Bold", "Italic", "Underline", "StrikeOut", "ScaleX", "ScaleY",
                               "Spacing", "Angle", "BorderStyle", "Outline", "Shadow", "Alignment", "MarginL",
                               "MarginR", "MarginV", "Encoding"]
                vals = [v.strip() for v in val.split(",", len(fmt) - 1)]
                d = dict(zip(fmt, vals))
                styles[d.get("Name", "Default").lstrip("*")] = _ass_style(d, v4plus)
        elif section == "events":
            if key == "Format":
                efmt = [f.strip() for f in val.split(",")]
            elif key == "Dialogue":
                fmt = efmt or ["Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR", "MarginV", "Effect", "Text"]
                vals = val.split(",", len(fmt) - 1)
                events.append(dict(zip(fmt, (v.strip() if f != "Text" else v for f, v in zip(fmt, vals)))))
    px, py = info.get("playresx"), info.get("playresy")
    try:
        prx, pry = (float(px) if px else 0.0), (float(py) if py else 0.0)
    except ValueError:
        prx = pry = 0.0
    if not prx and not pry:
        prx, pry = 384.0, 288.0
    elif not pry:
        pry = 1024.0 if prx == 1280 else prx * 3 / 4
    elif not prx:
        prx = 1280.0 if pry == 1024 else pry * 4 / 3
    script = {"resx": prx, "resy": pry, "wrap": int(float(info.get("wrapstyle", "0") or 0)),
              "scaled": info.get("scaledborderandshadow", "no").strip().lower() == "yes", "styles": styles}
    cues = []
    for order, d in enumerate(events):
        try:
            s, e = _ass_ts(d.get("Start", "0:00:00.00")), _ass_ts(d.get("End", "0:00:00.00"))
        except ValueError:
            continue
        raw = d.get("Text", "")
        plain = re.sub(r"\{[^}]*\}", "", raw).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", "\u00a0")
        ev = {"layer": int(float(d.get("Layer", "0") or 0)), "style": d.get("Style", "Default").lstrip("*"),
              "ml": d.get("MarginL"), "mr": d.get("MarginR"), "mv": d.get("MarginV"), "text": raw, "order": order}
        c = Cue(s, e, plain.strip(), speaker=d.get("Name") or None, order=order)
        c.rich = [("ass", script, ev)]
        cues.append(c)
    return cues


_ASS_NAMES = sorted("""fscx fscy fsp fs fn frz frx fry fr fax fay fade fad fe 1c 2c 3c 4c 1a 2a 3a 4a alpha an a
    xbord ybord bord xshad yshad shad be blur b iclip clip i u s c kf ko k K q r pos move org pbo p t""".split(),
                    key=len, reverse=True)
_ASS_TAG = re.compile(r"\\(" + "|".join(_ASS_NAMES) + r"|[a-zA-Z]+)(\([^)]*\)|[^\\]*)")


def _ass_tags(block: str):
    """(name, argument) pairs of an override block; \\1c style names split into ("c", n)."""
    for m in _ASS_TAG.finditer(block):
        name, arg = m.group(1), m.group(2).strip()
        yield name, arg


def _floats(arg: str) -> list[float]:
    out = []
    for p in arg.strip("()").split(","):
        try:
            out.append(float(p.strip()))
        except ValueError:
            pass
    return out


def _font_ratio(rc, family: str, weight: int, italic: bool) -> float:
    """(ascent + descent) / em of a font, from Pango's metrics (libass sizes fonts by it)."""
    key = ("ass-ratio", family, weight, italic)
    hit = rc.cache.get(key)
    if hit is None:
        try:
            from .assets import text as T
            from .pango_bridge import Pango
            lay = T._new_layout()
            fd = T.font_description(rc, {"font": family, "size": 100.0, "weight": weight,
                                         "fontStyle": "italic" if italic else "normal"}, 1.0)
            m = lay.get_context().get_metrics(fd, None)
            hit = max(0.5, (m.get_ascent() + m.get_descent()) / Pango.SCALE / 100.0)
        except Exception:  # noqa: BLE001 -- metrics unavailable: DejaVu's ratio
            hit = 1.164
        rc.cache[key] = hit
    return hit


def ass_blocks(rc, script: dict, ev: dict, start: float, end: float) -> list[RichBlock]:
    """Resolve one ASS Dialogue into a RichBlock in frame document px."""
    W, H = float(rc.doc.width), float(rc.doc.height)
    kx, ky = W / script["resx"], H / script["resy"]
    kb = ky if script["scaled"] else 1.0
    styles = script["styles"]
    base = dict(styles.get(ev["style"]) or styles.get("Default") or _DEFAULT_ASS_STYLE)
    for k_, name in (("ml", "ml"), ("mr", "mr"), ("mv", "mv")):
        try:
            v = float(ev.get(name) or 0)
        except ValueError:
            v = 0.0
        if v:
            base[k_] = v
    cur = dict(base)
    a = {"an": base["an"], "pos": None, "move": None, "org": None, "fade": None, "wrap": script["wrap"]}
    segs: list[Seg] = []
    kt = start
    pending_k: tuple | None = None

    def run_style(c: dict) -> dict:
        ratio = _font_ratio(rc, c["font"], int(c["weight"]), c["italic"])
        size = max(1.0, c["fs"] * ky * (c["fscy"] / base["fscy"] if base["fscy"] else 1.0) / ratio)
        st = {"font": c["font"] + ", DejaVu Sans", "size": size, "weight": int(c["weight"]),
              "fontStyle": "italic" if c["italic"] else "normal", "color": c["c1"], "lineHeight": ratio,
              "tracking": c["fsp"] * kx / size * 1000.0 if c["fsp"] else 0.0}
        rx = c["fscx"] / base["fscx"] if base["fscx"] else 1.0
        ry = c["fscy"] / base["fscy"] if base["fscy"] else 1.0
        if abs(rx - ry) > 1e-6:                 # a run scaled unlike the block: nearest font stretch
            st["stretch"] = max(0.5, min(2.0, rx / max(1e-6, ry)))
        if c["underline"]:
            st["decoration"] = "underline"
        elif c["strike"]:
            st["decoration"] = "line-through"
        blur = max(c["be"], c["blur"]) * kb
        if c["border_style"] != 3 and c["bord"] > 0:
            st.update(strokeColor=c["c3"], strokeWidth=c["bord"] * kb, strokePosition="outside")
        if c["shad"] > 0:
            st.update(shadowColor=c["c4"], shadowOffsetX=c["shad"] * kb, shadowOffsetY=c["shad"] * kb,
                      shadowBlur=blur * 2)
        return st

    text = ev["text"]
    parts = re.split(r"(\{[^}]*\})", text)
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            for name, arg in _ass_tags(part[1:-1]):
                low = name.lower()
                nums = _floats(arg)
                if low in ("k", "kf", "ko") or name == "K":
                    kind = "kf" if name in ("K", "kf") else low
                    dur = (nums[0] if nums else 0.0) / 100.0
                    pending_k = (kind, kt, kt + dur)
                    kt += dur
                elif low == "b":
                    v = int(nums[0]) if nums else 0
                    cur["weight"] = 700 if v == 1 else (400 if v == 0 else v)
                elif low == "i":
                    cur["italic"] = bool(nums and nums[0])
                elif low == "u":
                    cur["underline"] = bool(nums and nums[0])
                elif low == "s":
                    cur["strike"] = bool(nums and nums[0])
                elif low == "fn":
                    cur["font"] = arg or base["font"]
                elif low == "fs":
                    if nums:
                        cur["fs"] = nums[0] if not arg.startswith(("+", "-")) else cur["fs"] * (1 + nums[0] / 10)
                elif low == "fsp":
                    cur["fsp"] = nums[0] if nums else 0.0
                elif low == "fscx":
                    cur["fscx"] = nums[0] if nums else base["fscx"]
                elif low == "fscy":
                    cur["fscy"] = nums[0] if nums else base["fscy"]
                elif low in ("frz", "fr"):
                    cur["frz"] = nums[0] if nums else 0.0
                elif low in ("c", "1c", "2c", "3c", "4c"):
                    idx = "c" + (low[0] if low != "c" else "1")
                    cur[idx] = _with_alpha(_ass_color(arg, base[idx]), 255 - int(cur[idx][7:9], 16))
                elif low in ("alpha", "1a", "2a", "3a", "4a"):
                    m = re.search(r"[0-9A-Fa-f]{1,2}", arg.replace("&H", "").replace("&h", ""))
                    av = int(m.group(0), 16) if m else 0
                    for idx in (("c1", "c2", "c3", "c4") if low == "alpha" else ("c" + low[0],)):
                        cur[idx] = _with_alpha(cur[idx], av)
                elif low == "bord":
                    cur["bord"] = nums[0] if nums else base["bord"]
                elif low == "shad":
                    cur["shad"] = nums[0] if nums else base["shad"]
                elif low == "be":
                    cur["be"] = nums[0] if nums else 0.0
                elif low == "blur":
                    cur["blur"] = nums[0] if nums else 0.0
                elif low == "an" and nums:
                    a["an"] = int(nums[0]) if 1 <= int(nums[0]) <= 9 else a["an"]
                elif low == "a" and nums:
                    a["an"] = _V4_ALIGN.get(int(nums[0]), a["an"])
                elif low == "pos" and len(nums) >= 2 and a["pos"] is None and a["move"] is None:
                    a["pos"] = (nums[0], nums[1])
                elif low == "move" and len(nums) >= 4 and a["pos"] is None and a["move"] is None:
                    a["move"] = tuple(nums[:6])
                elif low == "org" and len(nums) >= 2:
                    a["org"] = (nums[0], nums[1])
                elif low == "fad" and len(nums) >= 2:
                    a["fade"] = (0.0, 1.0, 0.0, 0.0, nums[0], (end - start) * 1000 - nums[1], (end - start) * 1000)
                elif low == "fade" and len(nums) >= 7:
                    a1, a2, a3 = (1 - nums[i] / 255.0 for i in range(3))
                    a["fade"] = (a1, a2, a3, *nums[3:7])
                elif low == "q" and nums:
                    a["wrap"] = int(nums[0])
                elif low == "r":
                    cur = dict(styles.get(arg, base)) if arg else dict(base)
                elif low in ("h",):
                    continue
                else:
                    warn_once("captions", f"ass:\\{name}", "ASS override tag not supported; ignored")
            continue
        if not part:
            continue
        t_ = part.replace("\\N", "\n").replace("\\h", "\u00a0")
        t_ = t_.replace("\\n", "\n" if a["wrap"] == 2 else " ")
        k = pending_k
        pending_k = None
        segs.append(Seg(t_, run_style(cur), k, cur["c2"] if k is not None else None))
    # block
    an = a["an"]
    col = (an - 1) % 3
    row = (an - 1) // 3
    ml, mr, mv = base["ml"] * kx, base["mr"] * kx, base["mv"] * ky
    if a["pos"] is not None:
        x, y = a["pos"][0] * kx, a["pos"][1] * ky
    elif a["move"] is not None:
        x, y = a["move"][0] * kx, a["move"][1] * ky
    else:
        x = (ml, (ml + W - mr) / 2, W - mr)[col]
        y = (H - mv, H / 2, mv)[row]
    bs3 = base["border_style"] == 3
    rb = RichBlock(segs, x, y, an, width=max(1.0, W - ml - mr) if a["wrap"] != 2 else None,
                   align=("left", "center", "right")[col], wrap="balance" if a["wrap"] in (0, 3) else
                   ("none" if a["wrap"] == 2 else "word"),
                   line_bg=base["c3"] if bs3 else None, line_pad=base["bord"] * kb if bs3 else 0.0,
                   rot=cur["frz"] if cur["frz"] else base["frz"], layer=ev["layer"],
                   collide=a["pos"] is None and a["move"] is None, margin_v=mv,
                   sx=base["fscx"] / 100.0, sy=base["fscy"] / 100.0)
    if a["org"] is not None:
        rb.org = (a["org"][0] * kx, a["org"][1] * ky)
    if a["move"] is not None:
        mv_ = a["move"]
        t1, t2 = (mv_[4], mv_[5]) if len(mv_) >= 6 else (0.0, (end - start) * 1000)
        rb.move = (mv_[0] * kx, mv_[1] * ky, mv_[2] * kx, mv_[3] * ky, start + t1 / 1000, start + t2 / 1000)
    if a["fade"] is not None:
        f = a["fade"]
        rb.fade = (f[0], f[1], f[2], start + f[3] / 1000, start + f[4] / 1000, start + f[5] / 1000, start + f[6] / 1000)
    if not any(s_.st.get("strokeColor") or s_.st.get("shadowColor") for s_ in segs):
        rb.blur = max(base["be"], base["blur"], cur["be"], cur["blur"]) * kb / 2
    return [rb]


# ====================================================================== TTML / IMSC
_TTS = ("http://www.w3.org/ns/ttml#styling", "http://www.w3.org/2006/10/ttaf1#styling",
        "http://www.w3.org/2006/04/ttaf1#styling", "http://www.w3.org/ns/ttml#style")
_TTP = ("http://www.w3.org/ns/ttml#parameter", "http://www.w3.org/2006/10/ttaf1#parameter",
        "http://www.w3.org/2006/04/ttaf1#parameter")
_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
_INHERIT = {"color", "fontFamily", "fontSize", "fontStyle", "fontWeight", "textDecoration", "textOutline",
            "textShadow", "lineHeight", "textAlign", "wrapOption", "direction", "visibility", "fontVariant",
            "multiRowAlign", "letterSpacing"}
_GENERIC = {"default": "DejaVu Sans", "sansserif": "DejaVu Sans", "proportionalsansserif": "DejaVu Sans",
            "serif": "DejaVu Serif", "proportionalserif": "DejaVu Serif", "monospace": "DejaVu Sans Mono",
            "monospacesansserif": "DejaVu Sans Mono", "monospaceserif": "DejaVu Serif"}
_NAMED = {"transparent": "#00000000", "black": "#000000FF", "silver": "#C0C0C0FF", "gray": "#808080FF",
          "white": "#FFFFFFFF", "maroon": "#800000FF", "red": "#FF0000FF", "purple": "#800080FF",
          "fuchsia": "#FF00FFFF", "magenta": "#FF00FFFF", "green": "#008000FF", "lime": "#00FF00FF",
          "olive": "#808000FF", "yellow": "#FFFF00FF", "navy": "#000080FF", "blue": "#0000FFFF",
          "teal": "#008080FF", "aqua": "#00FFFFFF", "cyan": "#00FFFFFF"}


def _ttml_color(v: str | None, opacity: float = 1.0) -> str | None:
    if not v:
        return None
    s = v.strip()
    out = None
    if s.lower() in _NAMED:
        out = _NAMED[s.lower()]
    elif s.startswith("#"):
        h = s[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        out = "#" + (h + "FF" if len(h) == 6 else h).upper()
    else:
        m = re.match(r"rgba?\(([^)]*)\)", s)
        if m:
            p = [float(x) for x in m.group(1).split(",")]
            aa = int(p[3]) if len(p) > 3 else 255
            out = "#%02X%02X%02X%02X" % (int(p[0]), int(p[1]), int(p[2]), aa)
    if out is None:
        return None
    if opacity < 1:
        out = out[:7] + "%02X" % int(round(int(out[7:9], 16) * max(0.0, opacity)))
    return out


class _TTMLTime:
    def __init__(self, root):
        def par(name, default=None):
            for ns in _TTP:
                v = root.get(f"{{{ns}}}{name}")
                if v is not None:
                    return v
            return default
        fr = par("frameRate")
        self.fr = float(fr) if fr else 30.0
        mult = par("frameRateMultiplier")
        if mult:
            n, d = (float(x) for x in mult.split())
            self.efr = self.fr * n / d
        else:
            self.efr = self.fr
        self.sfr = float(par("subFrameRate", "1"))
        tr = par("tickRate")
        self.tick = float(tr) if tr else (self.fr * self.sfr if fr else 1.0)
        self.smpte = par("timeBase", "media") == "smpte"
        self.drop = par("dropMode", "nonDrop")

    def __call__(self, s: str | None) -> float | None:
        if s is None or not s.strip():
            return None
        s = s.strip()
        m = re.fullmatch(r"([\d.]+)(h|m|s|ms|f|t)", s)
        if m:
            v = float(m.group(1))
            u = m.group(2)
            return v * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[u] if u in ("h", "m", "s", "ms") else \
                (v / self.efr if u == "f" else v / self.tick)
        m = re.fullmatch(r"(\d+):(\d{2}):(\d{2})(?:([.:;])(\d+)(?:\.(\d+))?)?", s)
        if not m:
            raise ValueError(f"bad TTML time {s!r}")
        h, mi, se, sep, fr, sub = m.groups()
        h, mi, se = int(h), int(mi), int(se)
        if sep in (":", ";") and fr is not None:
            frames = int(fr) + (int(sub) / self.sfr if sub else 0.0)
            if self.smpte and self.drop in ("dropNTSC", "dropPAL"):
                nominal = round(self.fr)
                total_min = h * 60 + mi
                dropped = (2 * (total_min - total_min // 10)) if self.drop == "dropNTSC" else \
                    (4 * (total_min - total_min // 10)) // 2 * 2
                count = (h * 3600 + mi * 60 + se) * nominal + frames - dropped
                return count / self.efr
            return h * 3600 + mi * 60 + se + frames / self.efr
        return h * 3600 + mi * 60 + se + (float("0." + fr) if fr else 0.0)


def _relative_size(parent: str, rel: str) -> str:
    """A % / em fontSize resolved against the parent's (vertical) fontSize expression."""
    m = re.fullmatch(r"\s*([\d.]+)\s*(%|em)\s*", rel.split()[-1])
    p = re.fullmatch(r"\s*([\d.]+)\s*(px|c|rh|rw|%|em)?\s*", parent.split()[-1])
    if not m or not p:
        return rel
    k = float(m.group(1)) / (100.0 if m.group(2) == "%" else 1.0)
    unit = p.group(2) or "px"
    if unit in ("%", "em"):
        unit, base = "c", float(p.group(1)) / (100.0 if unit == "%" else 1.0)
    else:
        base = float(p.group(1))
    return f"{base * k:g}{unit}"


def _ttml_len(v: str, ref: float, cell: float, font: float, frame: tuple, root: tuple) -> float:
    m = re.fullmatch(r"\s*([-+]?[\d.]+)\s*(px|%|em|c|rh|rw)?\s*", v)
    if not m:
        return 0.0
    n, u = float(m.group(1)), m.group(2) or "px"
    if u == "px":
        return n * frame[1] / root[1]
    if u == "%":
        return n / 100.0 * ref
    if u == "em":
        return n * font
    if u == "c":
        return n * cell
    if u == "rh":
        return n / 100.0 * frame[1]
    return n / 100.0 * frame[0]


def parse_ttml(text: str) -> list[Cue]:
    """TTML documents as cues whose `rich` holds ("ttml", doc-data, interval content), resolved
    against the frame in `ttml_blocks`."""
    from lxml import etree
    root = etree.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    time = _TTMLTime(root)

    def lname(e):
        return etree.QName(e).localname if isinstance(e.tag, str) else ""

    def tts(e) -> dict:
        out = {}
        for k, v in e.attrib.items():
            q = etree.QName(k)
            if q.namespace in _TTS:
                out[q.localname] = v
        return out

    styles: dict[str, dict] = {}
    style_refs: dict[str, list] = {}
    for st in root.iter():
        if lname(st) == "style" and st.get(_XML_ID):
            styles[st.get(_XML_ID)] = tts(st)
            style_refs[st.get(_XML_ID)] = (st.get("style") or "").split()

    def resolve_style(sid, seen=()) -> dict:
        if sid in seen or sid not in styles:
            return {}
        out = {}
        for r in style_refs.get(sid, []):
            out.update(resolve_style(r, seen + (sid,)))
        out.update(styles[sid])
        return out

    def specified(e) -> dict:
        out = {}
        for r in (e.get("style") or "").split():
            out.update(resolve_style(r))
        for c in e:                                   # TTML2 inline <style> children (animation-free)
            if lname(c) == "style" and not c.get(_XML_ID):
                out.update(tts(c))
        out.update(tts(e))
        return out

    regions = {}
    for rg in root.iter():
        if lname(rg) == "region" and rg.get(_XML_ID):
            regions[rg.get(_XML_ID)] = specified(rg)
    cell = (32, 15)
    for ns in _TTP:
        v = root.get(f"{{{ns}}}cellResolution")
        if v:
            cell = tuple(int(x) for x in v.split()[:2])
    extent = tts(root).get("extent")
    root_px = None
    if extent:
        m = re.findall(r"([\d.]+)px", extent)
        if len(m) == 2:
            root_px = (float(m[0]), float(m[1]))
    data = {"regions": regions, "cell": cell, "root_px": root_px, "has_regions": bool(regions)}
    body = next((e for e in root.iter() if lname(e) == "body"), None)
    if body is None:
        return []

    # ---- timing: (element, begin, end, inherited style, region) for p and spans
    items = []          # p entries: dict(begin, end, style, region, segs=[(text, style, begin, end)])

    def interval(e, pb, pe, container):
        b = time(e.get("begin"))
        en = time(e.get("end"))
        du = time(e.get("dur"))
        b = pb + (b or 0.0)
        if en is not None:
            e_ = pb + en
        elif du is not None:
            e_ = b + du
        else:
            e_ = pe
        return b, min(e_, pe) if pe is not None else e_

    def inherit(parent: dict, own: dict) -> dict:
        out = {k: v for k, v in parent.items() if k in _INHERIT}
        out.update(own)
        fs = own.get("fontSize")
        if fs and re.search(r"(%|em)\s*$", fs):
            out["fontSize"] = _relative_size(parent.get("fontSize", "1c"), fs)
        return out

    def walk(e, pb, pe, pst, preg, container="par"):
        tag = lname(e)
        if tag in ("metadata", "styling", "layout", "head", "set", "style", "animation", "image", "audio",
                   "chunk", "source", "data", "font", "resources", "initial"):
            return pe
        b, en = interval(e, pb, pe, container)
        if en is not None and en <= b:
            return en
        own = specified(e)
        st = inherit(pst, own)
        reg = e.get("region", preg)
        if own.get("display") == "none":
            return en
        tc = e.get("timeContainer", "par")
        if tag == "p":
            segs: list = []
            p_bg = own.get("backgroundColor")
            _collect(e, b, en, st, segs, tc)
            items.append({"begin": b, "end": en, "style": st, "region": reg, "segs": segs, "bg": p_bg,
                          "preserve": _preserve(e),
                          "opacity": float(own.get("opacity", "1") or 1)})
            return en
        t = b
        for c in e:
            if not isinstance(c.tag, str):
                continue
            if tc == "seq":
                end_c = walk(c, t, en, st, reg, "seq")
                t = end_c if end_c is not None else t
            else:
                walk(c, b, en, st, reg, "par")
        return en

    def _collect(e, pb, pe, pst, segs, container):
        if e.text:
            segs.append((e.text, pst, pb, pe))
        t = pb
        for c in e:
            if not isinstance(c.tag, str):
                continue
            tag = lname(c)
            if tag == "br":
                segs.append(("\n", pst, pb, pe))
            elif tag == "span":
                cb, ce = interval(c, t if container == "seq" else pb, pe, container)
                own = specified(c)
                if own.get("display") != "none" and (ce is None or ce > cb):
                    st = inherit(pst, own)
                    if own.get("backgroundColor"):
                        st = {**st, "__spanbg": own["backgroundColor"]}
                    _collect(c, cb, ce, st, segs, c.get("timeContainer", "par"))
                if container == "seq":
                    t = ce if ce is not None else t
            elif tag in ("metadata", "set", "animation"):
                pass
            if c.tail:
                segs.append((c.tail, pst, pb, pe))

    doc_end = time(body.get("end"))
    walk(body, 0.0, doc_end if doc_end is not None else math.inf, {}, None)
    # ---- intervals (intermediate synchronic documents)
    cuts = sorted({x for it in items for x in [it["begin"], it["end"]] + [s[2] for s in it["segs"]] +
                   [s[3] for s in it["segs"]] if x is not None and math.isfinite(x)})
    cues = []
    ends = [x for it in items for x in (it["end"],) if x is not None]
    if any(not math.isfinite(x) for x in ends) and cuts:
        cuts.append(math.inf)
    for i in range(len(cuts) - 1):
        t0, t1 = cuts[i], cuts[i + 1]
        mid = t0 + (min(t1, t0 + 1.0) - t0) / 2
        active = []
        for it in items:
            if it["begin"] <= mid < (it["end"] if it["end"] is not None else math.inf):
                segs = _ttml_space([(s[0], s[1]) for s in it["segs"]
                                    if s[2] <= mid < (s[3] if s[3] is not None else math.inf)], it["preserve"])
                if "".join(s[0] for s in segs).strip():
                    active.append({**it, "segs": segs})
        if not active:
            continue
        text = "\n".join("".join(s[0] for s in a["segs"]).strip() for a in active)
        cues.append(Cue(t0, t1, text, order=i, rich=[("ttml", data, active)]))
    # regions shown "always" with a background
    for rid, rs in regions.items():
        if rs.get("showBackground", "always") == "always" and _ttml_color(rs.get("backgroundColor")) not in (None, "#00000000"):
            cues.append(Cue(0.0, math.inf, "", order=-1, rich=[("ttml-region", data, rid)]))
    # merge text-identical consecutive intervals is not done: each interval is its own ISD
    return cues


def _preserve(e) -> bool:
    while e is not None:
        v = e.get("{http://www.w3.org/XML/1998/namespace}space")
        if v is not None:
            return v == "preserve"
        e = e.getparent()
    return False


def _ttml_space(segs: list, preserve: bool) -> list:
    """XML default whitespace handling: runs of spaces/newlines -> one space, none at line ends."""
    if preserve:
        return segs
    out = [(("\n" if t == "\n" else re.sub(r"[ \t\r\n]+", " ", t)), st) for t, st in segs]
    at_start = True
    for i, (t, st) in enumerate(out):
        if t == "\n":
            at_start = True
            if i and out[i - 1][0] != "\n":
                out[i - 1] = (out[i - 1][0].rstrip(" "), out[i - 1][1])
            continue
        if at_start:
            t = t.lstrip(" ")
        if t:
            at_start = False
        out[i] = (t, st)
    if out and out[-1][0] != "\n":
        out[-1] = (out[-1][0].rstrip(" "), out[-1][1])
    # a trailing space before a later line break inside a following empty run
    return [(t, st) for t, st in out if t]


def ttml_blocks(rc, data: dict, active: list) -> list[RichBlock]:
    W, H = float(rc.doc.width), float(rc.doc.height)
    root = data["root_px"] or (W, H)
    cols, rows = data["cell"]
    cell_h = H / rows
    blocks = []
    for pi, it in enumerate(active):
        rid = it["region"]
        if data["has_regions"]:
            if rid not in data["regions"]:
                continue
            rs = data["regions"][rid]
        else:
            rs = {}
            rid = "__default"
        x, y, w, h, pad, disp, rbg = _region_geom(rs, W, H, root, data["cell"])
        st = it["style"]
        segs = []
        size = None
        for text, sst in it["segs"]:
            full = {**{k: v for k, v in rs.items() if k in _INHERIT}, **sst}
            rst = _ttml_run_style(full, W, H, root, cell_h, h)
            size = size or rst["size"]
            segs.append(Seg(text, rst))
        if not segs:
            continue
        full_p = {**{k: v for k, v in rs.items() if k in _INHERIT}, **st}
        ta = full_p.get("textAlign", "start")
        wm = rs.get("writingMode", "lrtb")
        writing = "vertical-rl" if wm in ("tbrl", "tb") else ("vertical-lr" if wm == "tblr" else None)
        direction = "rtl" if (full_p.get("direction") == "rtl" or wm in ("rltb", "rl")) else None
        pbg = _ttml_color(it.get("bg"))
        rb = RichBlock(segs, x + pad[3], y + pad[0], 7, width=max(1.0, w - pad[1] - pad[3]),
                       align={"left": "left", "center": "center", "right": "right", "end": "end"}.get(ta, "start"),
                       wrap="none" if full_p.get("wrapOption") == "noWrap" else "word",
                       line_bg=pbg, line_pad=0.0, opacity=it.get("opacity", 1.0) * float(rs.get("opacity", "1") or 1),
                       flow=(rid, x, y, w, h, disp, pad), region_bg=rbg, direction=direction, writing=writing)
        if writing:
            rb.line_len = max(1.0, h - pad[0] - pad[2])
        rb.layer = pi
        blocks.append(rb)
    return blocks


def _axis_len(v: str, axis: float, root_axis: float, cell: float) -> float:
    """A region length along one axis: px of the root extent, % of the frame axis, c cells, rw/rh."""
    m = re.fullmatch(r"\s*([-+]?[\d.]+)\s*(px|%|c|rw|rh)?\s*", v)
    if not m:
        return 0.0
    n, u = float(m.group(1)), m.group(2) or "px"
    return {"px": n * axis / root_axis, "%": n / 100.0 * axis, "c": n * cell}.get(u, n / 100.0 * axis)


def _region_geom(rs: dict, W, H, root, cells):
    cell_w, cell_h = W / cells[0], H / cells[1]
    ox = oy = 0.0
    w, h = W, H
    if rs.get("origin") and rs["origin"] != "auto":
        a, b = rs["origin"].split()[:2]
        ox, oy = _axis_len(a, W, root[0], cell_w), _axis_len(b, H, root[1], cell_h)
    if rs.get("extent") and rs["extent"] != "auto":
        a, b = rs["extent"].split()[:2]
        w, h = _axis_len(a, W, root[0], cell_w), _axis_len(b, H, root[1], cell_h)
    pad = (0.0, 0.0, 0.0, 0.0)
    if rs.get("padding"):
        vals = rs["padding"].split()
        vv = [_axis_len(v, H if i % 2 == 0 else W, root[1] if i % 2 == 0 else root[0], cell_h if i % 2 == 0 else cell_w)
              for i, v in enumerate(vals[:4])]
        if len(vv) == 1:
            pad = (vv[0],) * 4
        elif len(vv) == 2:
            pad = (vv[0], vv[1], vv[0], vv[1])
        elif len(vv) == 3:
            pad = (vv[0], vv[1], vv[2], vv[1])
        else:
            pad = tuple(vv)
    disp = rs.get("displayAlign", "before")
    rbg = _ttml_color(rs.get("backgroundColor"))
    return ox, oy, w, h, pad, disp, None if rbg in (None, "#00000000") else rbg


def _ttml_run_style(s: dict, W, H, root, cell_h, region_h) -> dict:
    fsv = _relative_size("1c", s.get("fontSize", "1c")) if re.search(r"(%|em)\s*$", s.get("fontSize", "1c")) \
        else s.get("fontSize", "1c").split()[-1]
    size = _ttml_len(fsv, cell_h, cell_h, cell_h, (W, H), root)
    size = max(1.0, size)
    fams = []
    for f in (s.get("fontFamily") or "default").split(","):
        f = f.strip().strip("'\"")
        fams.append(_GENERIC.get(f.replace(" ", "").lower(), f))
    op = 1.0
    col = _ttml_color(s.get("color", "white"), op) or "#FFFFFFFF"
    st = {"font": ", ".join(fams + ["DejaVu Sans"]), "size": size, "color": col,
          "weight": 700 if s.get("fontWeight") == "bold" else 400,
          "fontStyle": {"italic": "italic", "oblique": "oblique"}.get(s.get("fontStyle", "normal"), "normal")}
    lh = s.get("lineHeight", "normal")
    if lh == "normal":
        st["lineHeight"] = 1.25
    else:
        st["lineHeight"] = (float(lh[:-1]) / 100.0 if lh.endswith("%") else
                            _ttml_len(lh, size, cell_h, size, (W, H), root) / size)
    dec = s.get("textDecoration", "")
    if "underline" in dec and "noUnderline" not in dec:
        st["decoration"] = "underline"
    elif "lineThrough" in dec and "noLineThrough" not in dec:
        st["decoration"] = "line-through"
    elif "overline" in dec and "noOverline" not in dec:
        st["decoration"] = "overline"
    to = s.get("textOutline", "none")
    if to and to != "none":
        parts = to.split()
        oc = _ttml_color(parts[0]) if parts and not re.match(r"[\d.]", parts[0]) else None
        nums = [p for p in parts if re.match(r"[\d.]", p)]
        if nums:
            thick = _ttml_len(nums[0], size, cell_h, size, (W, H), root)
            st.update(strokeColor=oc or col, strokeWidth=thick, strokePosition="outside")
            if len(nums) > 1:
                st.update(shadowColor=oc or col, shadowBlur=_ttml_len(nums[1], size, cell_h, size, (W, H), root))
    ts = s.get("textShadow", "none")
    if ts and ts != "none":
        first = ts.split(",")[0].split()
        nums = [p for p in first if re.match(r"[-\d.]", p)]
        cols = [p for p in first if not re.match(r"[-\d.]", p)]
        if len(nums) >= 2:
            st.update(shadowColor=_ttml_color(cols[0]) if cols else col,
                      shadowOffsetX=_ttml_len(nums[0], size, cell_h, size, (W, H), root),
                      shadowOffsetY=_ttml_len(nums[1], size, cell_h, size, (W, H), root),
                      shadowBlur=_ttml_len(nums[2], size, cell_h, size, (W, H), root) if len(nums) > 2 else 0.0)
    if s.get("__spanbg"):
        bg = _ttml_color(s["__spanbg"])
        if bg and bg != "#00000000":
            st["highlight"] = bg
    if s.get("visibility") == "hidden":
        st["color"] = "#00000000"
        st.pop("strokeColor", None)
        st.pop("shadowColor", None)
        st.pop("highlight", None)
    return st


# ====================================================================== SCC (CEA-608)
_SCC_FPS = 30000 / 1001
_SPECIAL = "®°½¿™¢£♪à èâêîôû"          # 0x30-0x3F (0x39 transparent space)
_EXT_12 = "ÁÉÓÚÜü‘¡*’—©℠•“”ÀÂÇÈÊËëÎÏïÔÙùÛ«»"
_EXT_13 = "ÃãÍÌìÒòÕõ{}\\^_|~ÄäÖöß¥¤¦ÅåØø┌┐└┘"
_STD = {0x27: "’", 0x2A: "á", 0x5C: "é", 0x5E: "í", 0x5F: "ó", 0x60: "ú", 0x7B: "ç", 0x7C: "÷", 0x7D: "Ñ",
        0x7E: "ñ", 0x7F: "█"}
_STYLES = [("white", False, False), ("white", False, True), ("green", False, False), ("green", False, True),
           ("blue", False, False), ("blue", False, True), ("cyan", False, False), ("cyan", False, True),
           ("red", False, False), ("red", False, True), ("yellow", False, False), ("yellow", False, True),
           ("magenta", False, False), ("magenta", False, True), ("white", True, False), ("white", True, True)]
_608_COLORS = {"white": "#FFFFFFFF", "green": "#00FF00FF", "blue": "#0000FFFF", "cyan": "#00FFFFFF",
               "red": "#FF0000FF", "yellow": "#FFFF00FF", "magenta": "#FF00FFFF"}
_PAC_ROWS = {0x11: (1, 2), 0x12: (3, 4), 0x15: (5, 6), 0x16: (7, 8), 0x17: (9, 10), 0x10: (11, 11),
             0x13: (12, 13), 0x14: (14, 15)}


def _scc_time(s: str) -> float:
    m = re.fullmatch(r"(\d+):(\d{2}):(\d{2})([:;.,])(\d{2})", s.strip())
    if not m:
        raise ValueError(f"bad SCC timecode {s!r}")
    h, mi, se, sep, fr = m.groups()
    h, mi, se, fr = int(h), int(mi), int(se), int(fr)
    if sep in (";", ".", ","):                    # drop frame
        total_min = h * 60 + mi
        frames = (h * 3600 + mi * 60 + se) * 30 + fr - 2 * (total_min - total_min // 10)
    else:
        frames = (h * 3600 + mi * 60 + se) * 30 + fr
    return frames / _SCC_FPS


class _CC608:
    ROWS, COLS = 15, 32

    def __init__(self):
        self.mode = None
        self.disp = self._blank()
        self.nond = self._blank()
        self.row, self.col = 15, 0
        self.style = ("white", False, False)
        self.rollup = 0
        self.events: list[tuple[float, list]] = []

    def _blank(self):
        return [[None] * self.COLS for _ in range(self.ROWS + 1)]

    def _target(self):
        return self.nond if self.mode == "pop" else self.disp

    def snap(self, t: float):
        g = [row[:] for row in self.disp]
        if self.events and abs(self.events[-1][0] - t) < 1e-9:
            self.events[-1] = (t, g)
        else:
            self.events.append((t, g))

    def put(self, ch: str, t: float):
        if self.mode is None:
            return
        mem = self._target()
        if self.col >= self.COLS:
            self.col = self.COLS - 1
        mem[self.row][self.col] = (ch, self.style)
        self.col = min(self.COLS, self.col + 1)
        if mem is self.disp:
            self.snap(t)

    def backspace(self, t: float):
        mem = self._target()
        if self.col > 0:
            self.col -= 1
            mem[self.row][self.col] = None
            if mem is self.disp:
                self.snap(t)

    def misc(self, b2: int, t: float):
        if b2 == 0x20:                       # RCL
            self.mode = "pop"
        elif b2 == 0x21:                     # BS
            self.backspace(t)
        elif b2 == 0x24:                     # DER
            mem = self._target()
            for c in range(self.col, self.COLS):
                mem[self.row][c] = None
            if mem is self.disp:
                self.snap(t)
        elif b2 in (0x25, 0x26, 0x27):       # RU2-4
            if self.mode != "roll":
                self.disp = self._blank()
                self.nond = self._blank()
                self.row = 15
                self.snap(t)
            self.mode = "roll"
            self.rollup = b2 - 0x23
            self.col = 0
        elif b2 == 0x29:                     # RDC
            self.mode = "paint"
        elif b2 == 0x2C:                     # EDM
            self.disp = self._blank()
            self.snap(t)
        elif b2 == 0x2D:                     # CR
            if self.mode == "roll":
                top = max(1, self.row - self.rollup + 1)
                for r in range(top, self.row):
                    self.disp[r] = self.disp[r + 1][:]
                for r in range(1, top):
                    self.disp[r] = [None] * self.COLS
                self.disp[self.row] = [None] * self.COLS
                self.col = 0
                self.snap(t)
        elif b2 == 0x2E:                     # ENM
            self.nond = self._blank()
        elif b2 == 0x2F:                     # EOC
            self.disp, self.nond = self.nond, self.disp
            self.mode = "pop"
            self.snap(t)
        # 0x22 AOF, 0x23 AON, 0x28 FON, 0x2A TR, 0x2B RTD: no caption-mode effect

    def pac(self, b1: int, b2: int, t: float):
        rows = _PAC_ROWS.get(b1 & 0xF7)
        if rows is None:
            return
        row = rows[1] if (b2 & 0x20) else rows[0]
        low = b2 & 0x1F
        if low < 0x10:
            self.style = _STYLES[low]
            col = 0
        else:
            col = ((low & 0x0E) >> 1) * 4
            self.style = ("white", False, bool(low & 1))
        if self.mode == "roll" and row != self.row:
            # move the roll-up window to the new base row
            n = self.rollup
            old = [self.disp[r][:] for r in range(max(1, self.row - n + 1), self.row + 1)]
            self.disp = self._blank()
            for i, content in enumerate(reversed(old)):
                r = row - i
                if r >= 1:
                    self.disp[r] = content
            self.snap(t)
        self.row, self.col = row, col


def parse_scc(text: str) -> list[Cue]:
    lines = [x for x in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if x.strip()]
    pairs: list[tuple[float, int, int]] = []
    for line in lines:
        if line.startswith("Scenarist_SCC"):
            continue
        parts = line.split("\t", 1) if "\t" in line else line.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            t0 = _scc_time(parts[0])
        except ValueError:
            continue
        for i, word in enumerate(parts[1].split()):
            if not re.fullmatch(r"[0-9A-Fa-f]{4}", word):
                continue
            v = int(word, 16)
            pairs.append((t0 + i / _SCC_FPS, (v >> 8) & 0x7F, v & 0x7F))
    has_cc1 = any(0x10 <= b1 <= 0x17 for _, b1, _ in pairs)
    want = 1 if has_cc1 else 2
    dec = _CC608()
    chan = 1
    prev_ctrl = None
    for t, b1, b2 in pairs:
        if b1 == 0 and b2 == 0:
            prev_ctrl = None
            continue
        if 0x10 <= b1 <= 0x1F:
            if prev_ctrl == (b1, b2):             # redundant repeat of a control code
                prev_ctrl = None
                continue
            prev_ctrl = (b1, b2)
            chan = 1 if b1 < 0x18 else 2
            if chan != want:
                continue
            c1 = b1 & 0xF7
            if 0x40 <= b2 <= 0x7F:
                dec.pac(b1, b2, t)
            elif c1 == 0x11 and 0x20 <= b2 <= 0x2F:           # mid-row
                colour, italic, underline = _STYLES[b2 - 0x20]
                if italic:
                    dec.style = (dec.style[0], True, underline)
                else:
                    dec.style = (colour, False, underline)
                dec.put(" ", t)
            elif c1 == 0x11 and 0x30 <= b2 <= 0x3F:           # special characters
                dec.put(_SPECIAL[b2 - 0x30] if b2 != 0x39 else "\u00a0", t)
            elif c1 in (0x12, 0x13) and 0x20 <= b2 <= 0x3F:   # extended characters replace the previous one
                dec.backspace(t)
                dec.put((_EXT_12 if c1 == 0x12 else _EXT_13)[b2 - 0x20], t)
            elif c1 in (0x14, 0x15) and 0x20 <= b2 <= 0x2F:
                dec.misc(b2, t)
            elif c1 == 0x17 and 0x21 <= b2 <= 0x23:           # tab offsets
                dec.col = min(dec.COLS - 1, dec.col + (b2 - 0x20))
            continue
        prev_ctrl = None
        if chan != want:
            continue
        for b in (b1, b2):
            if b >= 0x20:
                dec.put(_STD.get(b, chr(b)), t)
    cues = []
    ev = dec.events
    for i, (t, grid) in enumerate(ev):
        rows = [(r, grid[r]) for r in range(1, 16) if any(c is not None for c in grid[r])]
        if not rows:
            continue
        t1 = ev[i + 1][0] if i + 1 < len(ev) else math.inf
        if t1 <= t:
            continue
        text = "\n".join("".join((c[0] if c else " ") for c in cells).strip() for _, cells in rows)
        cues.append(Cue(t, t1, text, order=i, rich=[("scc", rows)]))
    return cues


def scc_blocks(rc, rows: list) -> list[RichBlock]:
    W, H = float(rc.doc.width), float(rc.doc.height)
    aw = min(W, H * 4 / 3)                      # the 608 grid lives in the central 4:3 picture
    gw, gh = aw * 0.8, H * 0.8
    gx, gy = (W - gw) / 2, H * 0.1
    cw, ch = gw / 32, gh / 15
    size = ch * 0.85
    adv = _mono_advance(rc, size)
    blocks = []
    for r, cells in rows:
        first = next(i for i, c in enumerate(cells) if c is not None)
        last = max(i for i, c in enumerate(cells) if c is not None)
        segs: list[Seg] = []
        for c in cells[first:last + 1]:
            chx, (colour, italic, underline) = (c if c is not None else (" ", ("white", False, False)))
            st = {"font": "DejaVu Sans Mono", "size": size, "color": _608_COLORS[colour], "lineHeight": ch / size,
                  "fontStyle": "italic" if italic else "normal", "tracking": (cw - adv) / size * 1000.0}
            if underline:
                st["decoration"] = "underline"
            if segs and segs[-1].st == st:
                segs[-1].text += chx
            else:
                segs.append(Seg(chx, st))
        blocks.append(RichBlock(segs, gx + first * cw, gy + (r - 1) * ch, 7, width=None, align="left", wrap="none",
                                line_bg="#000000FF", line_pad=0.0, layer=r))
    return blocks


def _mono_advance(rc, size: float) -> float:
    key = ("scc-adv", round(size, 3))
    hit = rc.cache.get(key)
    if hit is None:
        from .assets import text as T
        spec = T.make_spec([T.Run("MMMMMMMMMM", {"font": "DejaVu Sans Mono", "size": size})], width=1e5, height=1e4,
                           size=size, wrap="none", emoji="none")
        blk = T.get_block(rc, spec)
        hit = (blk.logical[2] - blk.logical[0]) / 10.0
        rc.cache[key] = hit
    return hit


# ====================================================================== dispatch / writers
def parse_subtitles(text: str, fmt: str) -> list[Cue]:
    fmt = (fmt or "").lower()
    if fmt == "srt":
        return parse_srt(text)
    if fmt == "vtt":
        return parse_vtt(text)
    if fmt in ("ass", "ssa"):
        return parse_ass(text)
    if fmt in ("ttml", "itt", "dfxp", "xml"):
        return parse_ttml(text)
    if fmt == "scc":
        return parse_scc(text)
    warn_once("captions", f"format:{fmt}", "subtitle format not supported; track skipped")
    return []


def format_srt(cues: list[Cue]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{_fmt_ts(c.start, ',')} --> {_fmt_ts(c.end, ',')}\n{c.text}\n")
    return "\n".join(out)


def format_vtt(cues: list[Cue], word_timestamps: bool = False) -> str:
    out = ["WEBVTT\n"]
    for c in cues:
        body = c.text
        if word_timestamps and c.words:
            body = " ".join((f"<{_fmt_ts(w.start, '.')}>" if i else "") + w.text for i, w in enumerate(c.words))
        body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") if not (word_timestamps and c.words) else body
        if c.speaker:
            body = f"<v {c.speaker}>{body}"
        out.append(f"{_fmt_ts(c.start, '.')} --> {_fmt_ts(c.end, '.')}\n{body}\n")
    return "\n".join(out)


# ====================================================================== profanity
_BUILTIN_PROFANITY = """
fuck* motherfuck* shit shits shitty shitting shithead* bullshit* horseshit bitch* bastard* asshole* arsehole*
cunt* dick dicks dickhead* cock cocks cocksuck* pussy pussies piss pissed pissing prick pricks twat* wanker*
wank wanking whore* slut* douche douchebag* bollocks bugger* jackass* dumbass* goddamn* skank* tosser*
nigger* nigga* faggot* fag fags retard retards spic spics kike* chink chinks tranny trannies
"""


def _profanity_list() -> tuple[set[str], tuple[str, ...]]:
    entries = set(_BUILTIN_PROFANITY.split())
    path = os.environ.get("SCENERENDER_PROFANITY_LIST")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                lines = [x.split("#", 1)[0].strip() for x in f]
            if "@only" in lines:
                entries = set()
            for x in lines:
                if not x or x == "@only":
                    continue
                if x.startswith("!"):
                    entries.discard(x[1:].lower())
                else:
                    entries.add(x.lower())
        except OSError as e:
            warn_once("captions", f"profanity:{path}", f"cannot read SCENERENDER_PROFANITY_LIST: {e}")
    exact = {e for e in entries if not e.endswith("*")}
    prefixes = tuple(e[:-1] for e in entries if e.endswith("*") and len(e) > 1)
    return exact, prefixes


_WORDS = re.compile(r"[^\W_](?:[^\W_]|['’][^\W_])*")


def mask_profanity(text: str, lists=None) -> str:
    exact, prefixes = lists or _profanity_list()

    def repl(m):
        w = m.group(0)
        low = w.lower().replace("’", "'")
        if low in exact or (prefixes and low.startswith(prefixes)):
            return w[0] + re.sub(r"[^\W_]", "*", w[1:])
        return w
    return _WORDS.sub(repl, text)


def _mask(word: str) -> str:
    return mask_profanity(word)


# ====================================================================== transcription cache
_SENTENCE_END = re.compile(r"[.?!…。？！]['\"”’)\]]*$")


def _num(v, ms: bool) -> float:
    if isinstance(v, str):
        v = v.strip().rstrip("s")
    f = float(v)
    return f / 1000.0 if ms else f


def _words_to_cues(words: list[tuple[float, float, str, str | None]]) -> list[Cue]:
    out: list[Cue] = []
    cur: list = []

    def flush():
        if cur:
            ws = [Word(s, e, t) for s, e, t, _ in cur]
            out.append(Cue(ws[0].start, ws[-1].end, " ".join(w.text for w in ws), ws, cur[0][3], timed=True))
            cur.clear()
    for w in words:
        if cur and (w[0] - cur[-1][1] > 1.0 or w[3] != cur[-1][3]):
            flush()
        cur.append(w)
        if _SENTENCE_END.search(w[2]):
            flush()
    flush()
    return out


def cache_json_cues(data) -> list[Cue]:
    """Cues from every documented transcription JSON shape (see the module docstring)."""
    ms = isinstance(data, dict) and ("audio_duration" in data or str(data.get("timeUnit", data.get("time_unit", "s"))).lower() == "ms")

    def word_of(w, default_speaker=None):
        s = w.get("start", w.get("start_time", w.get("startTime", w.get("startOffset", w.get("ts")))))
        e = w.get("end", w.get("end_time", w.get("endTime", w.get("endOffset", w.get("end_ts")))))
        t = w.get("punctuated_word", w.get("word", w.get("text", w.get("value", ""))))
        if s is None or e is None:
            return None
        spk = w.get("speaker", default_speaker)
        return (_num(s, ms), _num(e, ms), str(t).strip(), None if spk is None else str(spk))

    def cue_of(c) -> Cue | None:
        if "start" not in c and "start_time" not in c:
            return None
        words = [x for x in (word_of(w) for w in c.get("words", []) or []) if x and x[2]]
        s = _num(c.get("start", c.get("start_time")), ms)
        e = _num(c.get("end", c.get("end_time")), ms)
        text = str(c.get("text", c.get("transcript", "")) or "").strip() or " ".join(w[2] for w in words)
        spk = c.get("speaker")
        return Cue(s, e, text, [Word(a, b, t) for a, b, t, _ in words], None if spk is None else str(spk),
                   timed=bool(words))

    if isinstance(data, list):
        if data and isinstance(data[0], dict) and "text" not in data[0] and "transcript" not in data[0] and \
                any(k in data[0] for k in ("word", "punctuated_word")):
            return _words_to_cues([x for x in (word_of(w) for w in data) if x and x[2]])
        return [c for c in (cue_of(x) for x in data if isinstance(x, dict)) if c is not None]
    if not isinstance(data, dict):
        return []
    for key in ("segments", "cues", "utterances"):
        if isinstance(data.get(key), list) and data[key]:
            return [c for c in (cue_of(x) for x in data[key]) if c is not None]
    res = data.get("results")
    if isinstance(res, dict) and isinstance(res.get("channels"), list):                 # Deepgram
        alt = (res["channels"][0].get("alternatives") or [{}])[0]
        return _words_to_cues([x for x in (word_of(w) for w in alt.get("words", [])) if x and x[2]])
    if isinstance(res, dict) and isinstance(res.get("items"), list):                    # AWS Transcribe
        words: list = []
        for it in res["items"]:
            content = (it.get("alternatives") or [{}])[0].get("content", "")
            if it.get("type") == "punctuation":
                if words:
                    s, e, t, spk = words[-1]
                    words[-1] = (s, e, t + content, spk)
                continue
            if "start_time" in it:
                words.append((_num(it["start_time"], ms), _num(it["end_time"], ms), content, it.get("speaker_label")))
        return _words_to_cues(words)
    if isinstance(res, list):                                                            # Google STT
        words = []
        for r in res:
            alt = (r.get("alternatives") or [{}])[0]
            for w in alt.get("words", []):
                x = word_of(w, r.get("speakerTag"))
                if x and x[2]:
                    words.append(x)
        return _words_to_cues(words)
    if isinstance(data.get("monologues"), list):                                         # Rev.ai
        words = []
        for mono in data["monologues"]:
            spk = mono.get("speaker")
            for el in mono.get("elements", []):
                if el.get("type") == "text" and "ts" in el:
                    words.append((_num(el["ts"], ms), _num(el["end_ts"], ms), str(el.get("value", "")).strip(),
                                  None if spk is None else str(spk)))
                elif el.get("type") == "punct" and words and el.get("value", "").strip():
                    s, e, t, sp = words[-1]
                    words[-1] = (s, e, t + el["value"].strip(), sp)
        return _words_to_cues(words)
    for key in ("words", "word_segments"):
        if isinstance(data.get(key), list):
            return _words_to_cues([x for x in (word_of(w) for w in data[key]) if x and x[2]])
    warn_once("captions", "cache-shape", "transcription cache JSON shape not recognised")
    return []


def _cache_cues(doc, track) -> list[Cue]:
    path = doc.resolve_path(track.get("cache"))
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        warn_once("captions", path, f"cannot read transcription cache: {e}")
        return []
    sha = track.get("cacheSha256")
    if sha and hashlib.sha256(raw).hexdigest().lower() != sha.lower():
        log.error("captionTrack %s: cache %s does not match cacheSha256; track skipped", track.get("id"), path)
        return []
    text = raw.decode("utf-8-sig")
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in ("srt", "vtt", "ass", "ssa", "ttml", "itt", "dfxp", "scc"):
        return parse_subtitles(text, ext)
    try:
        return cache_json_cues(json.loads(text))
    except (ValueError, TypeError, KeyError) as e:
        warn_once("captions", path, f"bad transcription cache: {e}")
        return []


# ====================================================================== track cues
def _synth_words(text: str, start: float, end: float) -> list[Word]:
    toks = text.split()
    if not toks:
        return []
    if not math.isfinite(end):
        end = start + 0.4 * len(toks)
    total = sum(len(t) + 1 for t in toks)
    out, t = [], start
    for tok in toks:
        d = (end - start) * (len(tok) + 1) / total
        out.append(Word(t, t + d, tok))
        t += d
    return out


def _mask_rich(rich, lists):
    out = []
    for item in rich:
        if item[0] == "ass":
            _, script, ev = item
            parts = re.split(r"(\{[^}]*\})", ev["text"])
            ev = {**ev, "text": "".join(p if p.startswith("{") else mask_profanity(p, lists) for p in parts)}
            out.append(("ass", script, ev))
        elif item[0] == "ttml":
            _, data, active = item
            out.append(("ttml", data, [{**a, "segs": [(mask_profanity(t, lists), s) for t, s in a["segs"]]}
                                       for a in active]))
        elif item[0] == "scc":
            rows = []
            for r, cells in item[1]:
                s = "".join(c[0] if c else "\u0000" for c in cells)
                m = mask_profanity(s, lists)
                rows.append((r, [None if c is None else (m[i], c[1]) for i, c in enumerate(cells)]))
            out.append(("scc", rows))
        else:
            out.append(item)
    return out


def track_cues(doc, track) -> list[Cue]:
    cues: list[Cue] = []
    for c in track:
        if ln(c) != "cue":
            continue
        s, e = float(c.get("start")), float(c.get("end"))
        words = [Word(float(w.get("start")), float(w.get("end")), w.get("text"), w.get("emphasis") == "true")
                 for w in c if ln(w) == "word"]
        text = c.get("text")
        if text is None:
            text = " ".join(w.text for w in words)
        cues.append(Cue(s, e, text, words, c.get("speaker"), c.get("style"), timed=bool(words),
                        position=c.get("position")))
    if track.get("src"):
        path = doc.resolve_path(track.get("src"))
        fmt = track.get("format") or os.path.splitext(path)[1].lstrip(".")
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                cues += parse_subtitles(f.read(), fmt)
        except OSError as e:
            warn_once("captions", path, f"cannot read subtitle file: {e}")
        except (ValueError, SyntaxError) as e:
            warn_once("captions", path, f"cannot parse subtitle file: {e}")
    if track.get("cache"):
        cues += _cache_cues(doc, track)
    elif track.get("transcribe") and not cues:
        warn_once("captions", track.get("id"), "transcription requested without @cache; no captions")
    cues.sort(key=lambda c: (c.start, c.order))
    lists = _profanity_list() if track.get("profanityFilter") == "true" else None
    for c in cues:
        if not c.words and c.rich is None:
            c.words = _synth_words(c.text, c.start, c.end)
        if lists is not None:
            c.text = mask_profanity(c.text, lists)
            for w in c.words:
                w.text = mask_profanity(w.text, lists)
            if c.rich is not None:
                c.rich = _mask_rich(c.rich, lists)
    return cues


# ====================================================================== pagination
def paginate(cue: Cue, max_chars: int = 32, max_lines: int = 2, max_words: int | None = None,
             one_word: bool = False) -> list[Page]:
    words = [w for w in cue.words if w.text]
    if not words:
        return []
    if one_word:
        return [Page(max(cue.start, w.start) if i else cue.start, (words[i + 1].start if i + 1 < len(words) else cue.end),
                     [[w]], cue) for i, w in enumerate(words)]
    lines: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        n = sum(len(x.text) for x in cur) + len(cur) + len(w.text)
        if cur and (n > max_chars or (max_words and len(cur) >= max_words)):
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    groups = [lines[i:i + max_lines] for i in range(0, len(lines), max(1, max_lines))]
    if len(groups) == 1:
        return [Page(cue.start, cue.end, groups[0], cue)]
    pages = []
    if cue.timed:
        for i, g in enumerate(groups):
            s = cue.start if i == 0 else g[0][0].start
            e = groups[i + 1][0][0].start if i + 1 < len(groups) else cue.end
            pages.append(Page(s, max(s, e), g, cue))
    else:
        sizes = [sum(len(w.text) + 1 for ln_ in g for w in ln_) for g in groups]
        total = float(sum(sizes))
        t = cue.start
        for g, n in zip(groups, sizes):
            d = (cue.end - cue.start) * n / total
            pages.append(Page(t, t + d, g, cue))
            t += d
    return pages


def track_pages(doc, track) -> list[Page]:
    """Pages of the plain cues and one page per styled cue (lines empty; see cue.rich)."""
    mc = int(track.get("maxCharsPerLine", 32))
    ml = int(track.get("maxLines", 2))
    mw = int(track.get("maxWordsPerLine")) if track.get("maxWordsPerLine") else None
    one = track.get("preset") == "one-word"
    out = []
    for c in track_cues(doc, track):
        if c.rich is not None:
            out.append(Page(c.start, c.end, [], c))
        else:
            out.extend(paginate(c, mc, ml, mw, one))
    return out


# ====================================================================== sidecars
def sidecar_cues(doc, track) -> list[Cue]:
    out = []
    dur = float(getattr(doc, "duration", 0.0) or 0.0)
    for p in track_pages(doc, track):
        end = p.end if math.isfinite(p.end) else max(dur, p.start)
        if p.cue.rich is not None:
            if p.cue.text.strip():
                out.append(Cue(p.start, end, p.cue.text, [], p.cue.speaker))
            continue
        words = [w for line in p.lines for w in line]
        out.append(Cue(p.start, end, "\n".join(" ".join(w.text for w in line) for line in p.lines), words,
                       p.cue.speaker))
    return out


def write_sidecars(doc, out_path: str, track_ids=None) -> list[str]:
    """Write .srt/.vtt files next to out_path for the given tracks (default: mode sidecar|both)."""
    sec = doc.section("captions")
    if sec is None:
        return []
    tracks = [t for t in sec if ln(t) == "captionTrack"]
    if track_ids is not None:
        ids = set(track_ids.split() if isinstance(track_ids, str) else track_ids)
        tracks = [t for t in tracks if t.get("id") in ids]
    else:
        tracks = [t for t in tracks if t.get("mode", "burn") in ("sidecar", "both")]
    stem = os.path.splitext(out_path)[0]
    langs = [t.get("language") for t in tracks]
    written = []
    for t in tracks:
        fmt = t.get("format") or "vtt"
        if fmt not in ("srt", "vtt"):
            warn_once("captions", f"sidecar:{fmt}", "sidecar written as vtt")
            fmt = "vtt"
        tag = t.get("language") if langs.count(t.get("language")) == 1 else t.get("id")
        path = f"{stem}.{tag}.{fmt}"
        cues = sidecar_cues(doc, t)
        body = format_srt(cues) if fmt == "srt" else format_vtt(cues)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        written.append(path)
    return written


# ====================================================================== burn-in: plain pages
def _style(doc, sid) -> dict:
    return dict(doc.text_styles.get(sid, {})) if sid else {}


def _page_spec(rc, track, page: Page, active: int | None, width: float, align: str = "center"):
    from .assets.text import Run, make_spec
    doc = rc.doc
    preset = track.get("preset", "classic")
    base = _style(doc, page.cue.style or track.get("style"))
    base.setdefault("size", round(doc.height * 0.044, 2))
    base.setdefault("color", "#FFFFFFFF")
    base.setdefault("lineHeight", 1.2)
    base.setdefault("font", "DejaVu Sans")
    if preset == "classic" and not base.get("shadowColor") and not base.get("strokeColor"):
        size = float(base["size"])
        base.update(shadowColor="#000000CC", shadowBlur=size * 0.12, shadowOffsetY=size * 0.04)
    act = {**base, **_style(doc, track.get("activeStyle"))} if track.get("activeStyle") else None
    runs, word_of_run = [], []
    idx = 0
    for li, line in enumerate(page.lines):
        for wi, w in enumerate(line):
            st = act if (act is not None and idx == active) else base
            if w.emphasis:
                st = {**st, "weight": 800}
            runs.append(Run(w.text, st))
            word_of_run.append(idx)
            idx += 1
            last = wi == len(line) - 1
            if not (last and li == len(page.lines) - 1):
                runs.append(Run("\n" if last else " ", base))
                word_of_run.append(None)
    size = float(base["size"])
    block = dict(width=width, height=size * float(base["lineHeight"]) * (len(page.lines) + 1), size=size,
                 align=align, wrap="none", emoji="color")
    if preset == "boxed-line":
        block.update(background="#000000B3", backgroundMode="line", backgroundPadding=size * 0.25,
                     backgroundRadius=size * 0.2)
    return make_spec(runs, **block), word_of_run, size


def _active_index(words: list[Word], t: float) -> int | None:
    """The last word that has started (it stays active through the gap before the next)."""
    cur = None
    for i, w in enumerate(words):
        if w.start <= t + 1e-9:
            cur = i
    return cur


def _word_states(rc, track, page: Page, words, t: float, active, size: float):
    """Per-word state dicts (see text_animators.build_pieces) for the preset."""
    from .values import parse_color
    preset = track.get("preset", "classic")
    ac = parse_color(track.get("activeColor") or DEFAULT_ACTIVE, rc.doc.tokens, (1, 0.83, 0, 1))
    n = len(words)
    st = [dict() for _ in range(n)]
    for i, w in enumerate(words):
        started = t >= w.start - 1e-9
        is_active = i == active
        q = min(1.0, max(0.0, (t - w.start) / max(1e-3, w.end - w.start)))
        if preset in ("highlight", "pop", "enlarge") and is_active and not track.get("activeStyle"):
            st[i]["fill"] = ac
        if preset == "karaoke":
            if started:
                base = _base_rgba(rc, track, page)
                st[i]["fill"] = tuple(b + (a - b) * (1.0 if t >= w.end else q) for a, b in zip(ac, base))
        elif preset == "boxed-word" and is_active:
            st[i]["highlight"] = (track.get("activeColor") or DEFAULT_ACTIVE, 1.0, size * 0.12, size * 0.18)
        elif preset == "pop" and is_active:
            u = min(1.0, max(0.0, (t - w.start) / 0.25))
            st[i]["scale"] = 1.0 + 0.15 * math.sin(math.pi * u)
        elif preset == "enlarge" and is_active:
            st[i]["scale"] = 1.0 + 0.2 * curves.get("cubic-out")(min(1.0, (t - w.start) / 0.1))
        elif preset == "typewriter" and not started:
            st[i]["opacity"] = 0.0
        elif preset == "bounce":
            if not started:
                st[i]["opacity"] = 0.0
            else:
                u = min(1.0, (t - w.start) / 0.35)
                if u < 1:
                    st[i]["y"] = -0.3 * size * (1 - curves.get("bounce-out")(u))
    return st


def _base_rgba(rc, track, page):
    from .values import parse_color
    s = _style(rc.doc, page.cue.style or track.get("style"))
    return parse_color(str(s.get("color", "#FFFFFFFF")), rc.doc.tokens, (1, 1, 1, 1))


def _placement(rc, track, cue: Cue, width: float, content_h: float, line_h: float):
    """(left, top, width, align, clamp) of a plain page from the track and cue/@position."""
    doc = rc.doc
    fw, fh = doc.width, doc.height
    x = parse_length(track.get("x", "50%"), fw, (fw, fh))
    y = parse_length(track.get("y", "75%"), fh, (fw, fh))
    align = "center"
    left = x - width / 2
    pos = (cue.position or "").strip()
    if not pos:
        return left, y, width, align
    sa = _safe_rect(rc, track) or (0.0, 0.0, float(fw), float(fh))
    kw = pos.lower()
    if kw in ("top", "middle", "bottom"):
        top = {"top": sa[1], "middle": (sa[1] + sa[3] - content_h) / 2, "bottom": sa[3] - content_h}[kw]
        return left, top, width, align
    if ":" not in pos:
        parts = pos.replace(",", " ").split()
        if len(parts) >= 2:
            x = parse_length(parts[0], fw, (fw, fh))
            y = parse_length(parts[1], fh, (fw, fh))
            return x - width / 2, y, width, align
        return left, y, width, align
    st = dict(p.split(":", 1) for p in pos.split() if ":" in p)
    a = st.get("align", "center").lower()
    align = {"start": "start", "left": "start", "end": "end", "right": "end"}.get(a, "center")
    if "size" in st and st["size"].endswith("%"):
        width = float(st["size"][:-1]) / 100.0 * fw
    anchor = {"start": "line-left", "end": "line-right"}.get(align, "center")
    if "position" in st:
        pv, _, pa = st["position"].partition(",")
        px = float(pv.rstrip("%")) / 100.0 * fw
        anchor = pa or anchor
    else:
        px = {"line-left": 0.0, "line-right": float(fw)}.get(anchor, fw / 2)
        if anchor == "center":
            px = x
    left = {"line-left": px, "line-right": px - width}.get(anchor, px - width / 2)
    top = y
    if "line" in st:
        lv, _, la = st["line"].partition(",")
        if lv.endswith("%"):
            ly = float(lv[:-1]) / 100.0 * fh
            top = {"center": ly - content_h / 2, "end": ly - content_h}.get(la, ly)
        else:
            n = int(float(lv))
            top = n * line_h if n >= 0 else fh + n * line_h - (content_h - line_h)
    return left, top, width, align


def render_page(rc, track, page: Page, t: float) -> Buf | None:
    from . import text_animators as TA
    from .assets import text as T
    doc = rc.doc
    fw, fh = doc.width, doc.height
    width = parse_length(track.get("width", "85%"), fw, (fw, fh))
    words = [w for line in page.lines for w in line]
    active = _active_index(words, t)
    preset = track.get("preset", "classic")
    if preset == "one-word":
        active = 0
    spec_active = active if track.get("activeStyle") and preset not in ("classic", "none", "fade", "slide", "boxed-line") else None
    spec, word_of_run, size = _page_spec(rc, track, page, spec_active, width)
    block = T.get_block(rc, spec)
    content_h = block.logical[3] - block.logical[1] + (size * 0.5 if preset == "boxed-line" else 0)
    left, top, w2, align = _placement(rc, track, page.cue, width, content_h, block.lines[0].h if block.lines else size)
    if (w2, align) != (width, "center"):
        spec, word_of_run, size = _page_spec(rc, track, page, spec_active, w2, align)
        block = T.get_block(rc, spec)
        width = w2
    wstates = _word_states(rc, track, page, words, t, active, size)
    # page-level motion
    page_dy, page_alpha = 0.0, 1.0
    if preset == "fade":
        page_alpha = min(1.0, (t - page.start) / 0.15, (page.end - t) / 0.15)
    elif preset == "slide":
        u = min(1.0, (t - page.start) / 0.25)
        e = curves.get("cubic-out")(u)
        page_dy, page_alpha = 0.4 * size * (1 - e), e
    if page_alpha <= 0:
        return None
    states = []
    for ci in range(len(block.clusters)):
        wi = word_of_run[block.cl_run[ci]]
        states.append(dict(wstates[wi]) if wi is not None else {})
    if page_dy:
        for s in states:
            s["y"] = s.get("y", 0.0) + page_dy
    sa = _safe_rect(rc, track)
    if sa is not None:
        sx0, sy0, sx1, sy1 = sa
        top = min(max(top, sy0), sy1 - content_h)
        left = min(max(left, sx0), max(sx0, sx1 - width))
    from .compositor import translate
    M = rc.root_matrix @ translate(left, top)
    pieces = TA.build_pieces(rc, block, states) if any(states) else None
    from .evaluator import Ctx
    buf = T.render_block(rc, block, M, Ctx(t=t, comp_t=t), pieces)
    if buf is not None and page_alpha < 1:
        buf.px *= page_alpha
    return buf


def _safe_rect(rc, track):
    doc = rc.doc
    sa_id = track.get("safeArea")
    if sa_id and sa_id in doc.ids:
        from .safe_areas import insets
        l, t, r, b = insets(doc.ids[sa_id], doc.width, doc.height)
    else:
        l, t, r, b = rc.safe_insets()
        if not any((l, t, r, b)):
            return None
    return l * doc.width, t * doc.height, doc.width * (1 - r), doc.height * (1 - b)


# ====================================================================== burn-in: styled cues
def rich_blocks(rc, cue: Cue) -> list[RichBlock]:
    key = ("caption-rich", id(cue))
    hit = rc.cache.get(key)
    if hit is not None and hit[0] is cue:
        return hit[1]
    out: list[RichBlock] = []
    for item in cue.rich or ():
        if item[0] == "ass":
            out += ass_blocks(rc, item[1], item[2], cue.start, cue.end)
        elif item[0] == "ttml":
            out += ttml_blocks(rc, item[1], item[2])
        elif item[0] == "ttml-region":
            data, rid = item[1], item[2]
            W, H = float(rc.doc.width), float(rc.doc.height)
            x, y, w, h, pad, disp, rbg = _region_geom(data["regions"][rid], W, H, data["root_px"] or (W, H),
                                                     data["cell"])
            out.append(RichBlock([], x, y, 7, flow=(rid, x, y, w, h, disp, pad), region_bg=rbg, layer=-1))
        elif item[0] == "scc":
            out += scc_blocks(rc, item[1])
    rc.cache[key] = (cue, out)
    return out


def _alpha_at(rb: RichBlock, t: float) -> float:
    a = rb.opacity
    if rb.fade is not None:
        a1, a2, a3, t1, t2, t3, t4 = rb.fade
        if t < t1:
            f = a1
        elif t < t2:
            f = a1 + (a2 - a1) * (t - t1) / max(1e-9, t2 - t1)
        elif t < t3:
            f = a2
        elif t < t4:
            f = a2 + (a3 - a2) * (t - t3) / max(1e-9, t4 - t3)
        else:
            f = a3
        a *= max(0.0, min(1.0, f))
    return a


def _rich_spec(rc, rb: RichBlock, t: float, sung: bool | None):
    """Spec of a block at t. sung: None = karaoke by time, True/False = every partial \\kf
    syllable fully sung / unsung (the two layers of a wipe)."""
    from .assets.text import Run, make_spec
    runs = []
    partial = []
    for si, s in enumerate(rb.segs):
        st = dict(s.st)
        if s.k is not None:
            kind, t0, t1 = s.k
            if kind == "kf" and t0 < t < t1:
                partial.append(si)
                on = bool(sung)
            else:
                on = t >= t0
            if not on:
                st["color"] = s.k_color or st["color"]
                if kind == "ko":
                    st.pop("strokeColor", None)
                    st.pop("strokeWidth", None)
        runs.append(Run(s.text, st))
    size = max((float(s.st.get("size", 20)) for s in rb.segs), default=20.0)
    wrap = rb.wrap if rb.width is not None else "none"
    blk = dict(width=rb.width if rb.width is not None else 1e5, height=rb.line_len or 1e5, size=size,
               align=rb.align, wrap=wrap,
               emoji="color", direction=rb.direction, writingMode=rb.writing, language=rb.language)
    if rb.line_bg:
        blk.update(background=rb.line_bg, backgroundMode="line", backgroundPadding=rb.line_pad)
    return make_spec(runs, **blk), partial


def _asset_box(blk) -> tuple[float, float, float, float]:
    """The block's logical extent in its asset box coordinates."""
    x0, y0, x1, y1 = blk.logical
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], np.float64) @ blk.O.T
    return pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()


def _block_geom(rc, rb: RichBlock, t: float):
    """(block, w, h) of a RichBlock at t (frame document px)."""
    from .assets import text as T
    spec, _partial = _rich_spec(rc, rb, t, None)
    blk = T.get_block(rc, spec)
    ax0, ay0, ax1, ay1 = _asset_box(blk)
    return blk, (ax1 - ax0 if rb.width is None else rb.width), ay1 - ay0


def _block_matrix(rc, rb: RichBlock, blk, w: float, h: float, t: float, dy: float = 0.0):
    from .compositor import translate
    x, y = rb.x, rb.y
    if rb.move is not None:
        x1, y1, x2, y2, t1, t2 = rb.move
        u = 0.0 if t <= t1 else (1.0 if t >= t2 else (t - t1) / max(1e-9, t2 - t1))
        x, y = x1 + (x2 - x1) * u, y1 + (y2 - y1) * u
    col = (rb.an - 1) % 3
    row = (rb.an - 1) // 3
    left = x - col / 2.0 * w
    top = y - (1.0 - row / 2.0) * h + dy
    ax0, ay0, _ax1, _ay1 = _asset_box(blk)
    M = translate(left - (ax0 if rb.width is None else 0.0), top - ay0)
    if rb.rot or rb.sx != 1 or rb.sy != 1:
        ox, oy = rb.org if rb.org is not None else (x, y)
        r = math.radians(-rb.rot)
        R = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
        S = np.diag([rb.sx, rb.sy, 1.0])
        M = translate(ox, oy) @ R @ S @ translate(-ox, -oy) @ M
    return M


def _wipe_mask(rc, blk, M_frame, partial_clusters_x, rect):
    """Alpha mask (h, w) in rect: 1 where the sung layer shows."""
    import cairo
    from .raster import Canvas
    from .assets.text import INF
    c = Canvas(rect)
    c.set_matrix(M_frame @ blk.O)
    cr = c.cr
    cr.rectangle(-INF, -INF, 2 * INF, 2 * INF)
    last = len(blk.lines) - 1
    for li, xs, xe in partial_clusters_x:        # unsung parts: [xs, xe] on line li
        ln_ = blk.lines[li]
        y0 = -INF if li == 0 else ln_.y
        y1 = INF if li == last else ln_.y + ln_.h
        cr.rectangle(xs, y0, xe - xs, y1 - y0)
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    cr.set_source_rgba(1, 1, 1, 1)
    cr.fill()
    return c.to_buf(False).px[..., 3:4]


def render_rich_block(rc, rb: RichBlock, t: float, left_top=None) -> Buf | None:
    from .assets import text as T
    from .evaluator import Ctx
    alpha = _alpha_at(rb, t)
    if alpha <= 1e-4 or not rb.segs:
        return None
    ctx = Ctx(t=t, comp_t=t)
    spec, partial = _rich_spec(rc, rb, t, None if not any(s.k and s.k[0] == "kf" for s in rb.segs) else True)
    blk = T.get_block(rc, spec)
    ax0, ay0, ax1, ay1 = _asset_box(blk)
    w = rb.width if rb.width is not None else ax1 - ax0
    h = ay1 - ay0
    if left_top is not None:
        from .compositor import translate
        M = translate(left_top[0] - (ax0 if rb.width is None else 0.0), left_top[1] - ay0)
    else:
        M = _block_matrix(rc, rb, blk, w, h, t)
    Mf = rc.root_matrix @ M
    buf = T.render_block(rc, blk, Mf, ctx, None)
    if partial and buf is not None:
        spec_b, _ = _rich_spec(rc, rb, t, False)
        blk_b = T.get_block(rc, spec_b)
        buf_b = T.render_block(rc, blk_b, Mf, ctx, None)
        # unsung x ranges of the partial syllables (clusters of those runs), wiping left to right
        cuts = []
        for si in partial:
            _kind, t0, t1 = rb.segs[si].k
            q = (t - t0) / max(1e-9, t1 - t0)
            cl = [ci for ci in range(len(blk.clusters)) if blk.cl_run[ci] == si]
            for li, x0, x1 in blk.line_ranges(cl):
                cuts.append((li, x0 + (x1 - x0) * q, x1))
        if buf_b is not None:
            from .raster import union
            rect = union(buf.rect, buf_b.rect)
            a = buf.region(rect)
            b = buf_b.region(rect)
            m = _wipe_mask(rc, blk, Mf, cuts, rect)
            buf = Buf(a * m + b * (1 - m), rect[0], rect[1])
    if buf is None:
        return None
    if rb.blur > 0.05:
        from .effects import gaussian
        pad = int(math.ceil(rb.blur * rc.scale * 3))
        b2 = buf.pad(pad)
        buf = Buf(gaussian(b2.px, rb.blur * rc.scale), b2.x0, b2.y0)
    if alpha < 1:
        buf.px *= alpha
    return buf


def _region_bg(rc, flow, colour: str) -> Buf | None:
    from .raster import Canvas
    from .values import parse_color
    _rid, x, y, w, h, _disp, _pad = flow
    R = rc.root_matrix
    p0 = R @ np.array([x, y, 1.0])
    p1 = R @ np.array([x + w, y + h, 1.0])
    rect = (int(math.floor(p0[0])), int(math.floor(p0[1])), int(math.ceil(p1[0])), int(math.ceil(p1[1])))
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        return None
    c = Canvas(rect)
    c.set_matrix(R)
    c.cr.rectangle(x, y, w, h)
    c.cr.set_source_rgba(*parse_color(colour, rc.doc.tokens, (0, 0, 0, 0)))
    c.cr.fill()
    return c.to_buf(rc.linear)


def render_rich(rc, cues: list[Cue], t: float) -> list[Buf]:
    """Buffers (bottom first) of all active styled cues at t."""
    blocks = [(c, rb) for c in cues for rb in rich_blocks(rc, c)]
    blocks.sort(key=lambda it: (it[1].layer if it[1].flow is None else 0, it[0].start, it[0].order))
    out: list[Buf] = []
    # TTML flows: stack the p blocks of each region
    flows: dict = {}
    for _c, rb in blocks:
        if rb.flow is not None:
            flows.setdefault(rb.flow[0], []).append(rb)
    for rbs in flows.values():
        flow = rbs[0].flow
        _r, x, y, w, h, disp, pad = flow
        bg = next((rb.region_bg for rb in rbs if rb.region_bg), None)
        if bg:
            b = _region_bg(rc, flow, bg)
            if b is not None:
                out.append(b)
        inner = (x + pad[3], y + pad[0], w - pad[1] - pad[3], h - pad[0] - pad[2])
        sized = []
        for rb in rbs:
            if rb.segs:
                blk, _w, hh = _block_geom(rc, rb, t)
                if rb.writing:                    # columns: the extent used across the box width
                    ax0, _ay0, ax1, _ay1 = _asset_box(blk)
                    hh = (rb.width - ax0) if rb.writing == "vertical-rl" else ax1
                sized.append((rb, hh))
        total = sum(hh for _, hh in sized)
        vertical = bool(sized) and bool(sized[0][0].writing)
        span = inner[2] if vertical else inner[3]
        off = {"center": (span - total) / 2, "after": span - total}.get(disp, 0.0)
        for rb, hh in sized:
            if not vertical:
                lt = (inner[0], inner[1] + off)
            elif rb.writing == "vertical-rl":     # the first p is the rightmost column block
                lt = (inner[0] - off, inner[1])
            else:
                lt = (inner[0] + off, inner[1])
            b = render_rich_block(rc, rb, t, lt)
            if b is not None:
                out.append(b)
            off += hh
    # ASS / SCC blocks; ASS collisions stack away from the margin
    stacks: dict[tuple, float] = {}
    for _c, rb in blocks:
        if rb.flow is not None:
            continue
        if rb.collide:
            row = (rb.an - 1) // 3
            if row in (0, 2):
                blk, w, h = _block_geom(rc, rb, t)
                key = (row, rb.layer)
                used = stacks.get(key, 0.0)
                rb2 = RichBlock(**{**rb.__dict__, "y": rb.y - used if row == 0 else rb.y + used})
                stacks[key] = used + h
                rb = rb2
        b = render_rich_block(rc, rb, t)
        if b is not None:
            out.append(b)
    return out


def burn_tracks(rc) -> list:
    sec = rc.doc.section("captions")
    if sec is None:
        return []
    tracks = [t for t in sec if ln(t) == "captionTrack"]
    only = rc.cache.get("burn_captions")
    if only:
        tracks = [t for t in tracks if t.get("id") == only]
    else:
        tracks = [t for t in tracks if t.get("mode", "burn") in ("burn", "both")]
    return sorted(tracks, key=lambda t: int(t.get("z", 1000000)))


def burn(rc, out: Buf, ctx) -> Buf:
    from .blend import composite
    t = ctx.comp_t
    for track in burn_tracks(rc):
        key = ("caption-pages", track)
        pages = rc.cache.get(key)
        if pages is None:
            pages = track_pages(rc.doc, track)
            rc.cache[key] = pages
        rich = []
        shown_plain = False
        for p in pages:
            if not (p.start - 1e-9 <= t < p.end - 1e-9):
                continue
            if p.cue.rich is not None:
                rich.append(p.cue)
            elif not shown_plain:
                shown_plain = True
                buf = render_page(rc, track, p, t)
                if buf is not None:
                    out = composite(out, buf, "normal", 1.0)
        for buf in render_rich(rc, rich, t) if rich else ():
            out = composite(out, buf, "normal", 1.0)
    return out


@hook_installer("captions")
def install(rc) -> None:
    sec = rc.doc.section("captions")
    if sec is not None and any(ln(t) == "captionTrack" for t in sec):
        rc.hooks["captions"] = burn
