"""Captions: burned-in caption rendering (a compositor hook) and sidecar subtitle files.

Sources of a captionTrack's cues, in this order: inline <cue> elements, the subtitle
file @src (srt, vtt, ass/ssa, ttml/itt; scc is not supported), and a transcription
cache @cache (JSON: a list of cues or {"segments": [...]}, each with start/end/text
and optional words; verified against @cacheSha256 when given).

Pinned rules (the schema leaves these open):
  * x is the horizontal centre and y the top of the caption block; width is the wrap
    width. Lengths are relative to the output frame. The block is kept inside the track's
    safe area (else the project's) by moving it.
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


from . import curves
from .document import ln
from .raster import Buf
from .registry import CAPTION_PRESETS, FEATURES, FULL, NONE, PARTIAL, warn_once
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
FEATURES.declare("captions:src:vtt", FULL, "inline <hh:mm:ss.mmm> word timestamps are read")
FEATURES.declare("captions:src:ass", PARTIAL, "Dialogue text and timing only; styling ignored")
FEATURES.declare("captions:src:ttml", PARTIAL, "<p begin/end> text only")
FEATURES.declare("captions:src:itt", PARTIAL, "as ttml")
FEATURES.declare("captions:src:scc", NONE, "CEA-608 not decoded")
FEATURES.declare("captions:transcribe", PARTIAL, "read from @cache JSON; no transcription is run")
FEATURES.declare("captions:profanityFilter", PARTIAL, "small built-in word list")

DEFAULT_ACTIVE = "#FFD400FF"


# ====================================================================== data
@dataclass
class Word:
    start: float
    end: float
    text: str
    emphasis: bool = False


@dataclass
class Cue:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: str | None = None
    style: str | None = None
    timed: bool = False          # words carry real timings


@dataclass
class Page:
    start: float
    end: float
    lines: list[list[Word]]
    cue: Cue


# ====================================================================== parsing
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
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ").replace("&amp;", "&")


def parse_srt(text: str) -> list[Cue]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip()):
        lines = [x for x in block.split("\n")]
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
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        if lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")) and not any("-->" in x for x in lines):
            continue
        ti = next((i for i, x in enumerate(lines) if "-->" in x), None)
        if ti is None:
            continue
        a, rest = lines[ti].split("-->", 1)
        b = rest.strip().split()[0]
        s, e = _ts(a), _ts(b)
        txt, words, speaker = _vtt_text("\n".join(lines[ti + 1:]), s, e)
        cues.append(Cue(s, e, txt, words, speaker, timed=bool(words)))
    return cues


def parse_ass(text: str) -> list[Cue]:
    cues, fmt = [], None
    for line in text.splitlines():
        if line.startswith("Format:") and fmt is None or (line.startswith("Format:") and "Text" in line):
            fmt = [f.strip() for f in line[7:].split(",")]
        elif line.startswith("Dialogue:") and fmt:
            vals = line[9:].split(",", len(fmt) - 1)
            d = dict(zip(fmt, (v.strip() for v in vals)))
            t = re.sub(r"\{[^}]*\}", "", d.get("Text", "")).replace("\\N", "\n").replace("\\n", "\n")
            s = _ass_ts(d.get("Start", "0:00:00.00"))
            e = _ass_ts(d.get("End", "0:00:00.00"))
            cues.append(Cue(s, e, t.strip(), speaker=d.get("Name") or None))
    return cues


def _ass_ts(s: str) -> float:
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def _ttml_time(s: str | None) -> float:
    if not s:
        return 0.0
    s = s.strip()
    m = re.fullmatch(r"([\d.]+)(h|m|s|ms)", s)
    if m:
        v = float(m.group(1))
        return v * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[m.group(2)]
    parts = s.split(":")
    if len(parts) == 4:          # hh:mm:ss:frames (assume 30 fps)
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + int(parts[3]) / 30.0
    return _ts(s if "." in s or "," in s else s + ".000")


def parse_ttml(text: str) -> list[Cue]:
    from lxml import etree
    root = etree.fromstring(text.encode("utf-8"))
    cues = []
    for p in root.iter():
        if isinstance(p.tag, str) and etree.QName(p).localname == "p":
            parts = [p.text or ""]
            for c in p:
                parts.append("\n" if etree.QName(c).localname == "br" else "".join(c.itertext()))
                parts.append(c.tail or "")
            cues.append(Cue(_ttml_time(p.get("begin")), _ttml_time(p.get("end")), re.sub(r"[ \t]+", " ", "".join(parts)).strip()))
    return cues


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


# ====================================================================== track cues
def _synth_words(text: str, start: float, end: float) -> list[Word]:
    toks = text.split()
    if not toks:
        return []
    total = sum(len(t) + 1 for t in toks)
    out, t = [], start
    for tok in toks:
        d = (end - start) * (len(tok) + 1) / total
        out.append(Word(t, t + d, tok))
        t += d
    return out


_PROFANE = {"fuck", "fucking", "shit", "bitch", "cunt", "asshole", "bastard", "dick", "piss", "motherfucker"}


def _mask(word: str) -> str:
    core = re.sub(r"\W", "", word).lower()
    if core in _PROFANE:
        return re.sub(r"(?<=\w)\w", "*", word)
    return word


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
        cues.append(Cue(s, e, text, words, c.get("speaker"), c.get("style"), timed=bool(words)))
    if track.get("src"):
        path = doc.resolve_path(track.get("src"))
        fmt = track.get("format") or os.path.splitext(path)[1].lstrip(".")
        try:
            with open(path, encoding="utf-8-sig") as f:
                cues += parse_subtitles(f.read(), fmt)
        except OSError as e:
            warn_once("captions", path, f"cannot read subtitle file: {e}")
    if track.get("cache"):
        cues += _cache_cues(doc, track)
    elif track.get("transcribe") and not cues:
        warn_once("captions", track.get("id"), "transcription requested without @cache; no captions")
    cues.sort(key=lambda c: c.start)
    profane = track.get("profanityFilter") == "true"
    for c in cues:
        if not c.words:
            c.words = _synth_words(c.text, c.start, c.end)
        if profane:
            c.text = " ".join(_mask(w) for w in c.text.split(" "))
            for w in c.words:
                w.text = _mask(w.text)
    return cues


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
    if ext in ("srt", "vtt", "ass", "ssa", "ttml", "itt"):
        return parse_subtitles(text, ext)
    data = json.loads(text)
    segs = data.get("segments", data.get("cues", [])) if isinstance(data, dict) else data
    out = []
    for s in segs:
        words = [Word(float(w["start"]), float(w["end"]), str(w.get("text", w.get("word", ""))).strip())
                 for w in s.get("words", [])]
        out.append(Cue(float(s["start"]), float(s["end"]), str(s.get("text", "")).strip() or " ".join(w.text for w in words),
                       words, s.get("speaker"), timed=bool(words)))
    return out


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
    mc = int(track.get("maxCharsPerLine", 32))
    ml = int(track.get("maxLines", 2))
    mw = int(track.get("maxWordsPerLine")) if track.get("maxWordsPerLine") else None
    one = track.get("preset") == "one-word"
    return [p for c in track_cues(doc, track) for p in paginate(c, mc, ml, mw, one)]


# ====================================================================== sidecars
def sidecar_cues(doc, track) -> list[Cue]:
    out = []
    for p in track_pages(doc, track):
        words = [w for line in p.lines for w in line]
        out.append(Cue(p.start, p.end, "\n".join(" ".join(w.text for w in line) for line in p.lines), words,
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


# ====================================================================== burn-in
def _style(doc, sid) -> dict:
    return dict(doc.text_styles.get(sid, {})) if sid else {}


def _page_spec(rc, track, page: Page, active: int | None, width: float):
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
                 align="center", wrap="none", emoji="color")
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
    x = parse_length(track.get("x", "50%"), fw, (fw, fh))
    y = parse_length(track.get("y", "75%"), fh, (fw, fh))
    left, top = x - width / 2, y
    content_h = block.logical[3] - block.logical[1] + (size * 0.5 if preset == "boxed-line" else 0)
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
        for p in pages:
            if p.start - 1e-9 <= t < p.end - 1e-9:
                buf = render_page(rc, track, p, t)
                if buf is not None:
                    out = composite(out, buf, "normal", 1.0)
                break
    return out


@hook_installer("captions")
def install(rc) -> None:
    sec = rc.doc.section("captions")
    if sec is not None and any(ln(t) == "captionTrack" for t in sec):
        rc.hooks["captions"] = burn
