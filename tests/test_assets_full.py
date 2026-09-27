"""FULL-level asset kinds: lottie, formula, chart layout, colour-managed video, provenance, generated."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from fractions import Fraction

import numpy as np
import pytest

from test_assets import FIX, HERE, MEDIA, _coverage, asset_buf, frame_rgb, make_rc  # noqa: F401  (fixtures too)
from test_assets import _cache, media  # noqa: F401

from scenerender import document  # noqa: E402
from scenerender.assets import chart, formula, lottie, video  # noqa: E402
from scenerender.compositor import RenderContext  # noqa: E402
from scenerender.evaluator import Ctx, Evaluator  # noqa: E402
from scenerender.registry import ASSETS, FULL  # noqa: E402

FULL_DOC = os.path.join(FIX, "assets_full.xml")
NEED = ["lottie_demo.json", "lottie_demo.lottie", "log10.mkv", "hlg10.mkv", "chart_data.json"]


@pytest.fixture(scope="module", autouse=True)
def media_full():
    if not all(os.path.exists(os.path.join(MEDIA, n)) for n in NEED):
        subprocess.run([os.sys.executable, os.path.join(MEDIA, "make_media_assets.py")], check=True)


def _box_x(buf, row: int, colour: int) -> float | None:
    """Mean x (frame px) of pixels on a row whose channel `colour` dominates."""
    px = buf.px
    y = row - buf.y0
    if not 0 <= y < px.shape[0]:
        return None
    r = px[y]
    m = (r[:, colour] > 0.3) & (r[:, 3] > 0.5) & (r[:, colour] > r[:, (colour + 1) % 3] * 2)
    xs = np.nonzero(m)[0]
    return float(xs.mean() + buf.x0) if len(xs) else None


# ---------------------------------------------------------------- registry
def test_levels_full():
    for k in ("lottie", "chart", "video", "generated", "audiogram", "code", "vector"):
        assert ASSETS.level(k) == FULL, k
    assert ASSETS.level("formula") == (FULL if formula.have_latex() else "partial")


# ---------------------------------------------------------------- lottie
def _lottie_json(tmp_path, data=None):
    import importlib.util
    spec = importlib.util.spec_from_file_location("mm", os.path.join(MEDIA, "make_media_assets.py"))
    mm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mm)
    p = tmp_path / "a.json"
    p.write_text(json.dumps(data or mm.lottie()))
    return p, mm


def test_lottie_generated_in_test_position_text_and_slot(tmp_path):
    p, _ = _lottie_json(tmp_path)
    rc = make_rc(f'<lottie id="l" src="{p}" width="200" height="150"/>'
                 f'<lottie id="s" src="{p}" width="200" height="150"><slot id="accent" value="#0000FFFF"/>'
                 f'<slot id="title" value="WWWWWWWW"/></lottie>', '', tmp_path, w=200, h=150)
    xs = [_box_x(asset_buf(rc, "l", src_t=t), 100, 0) for t in (0.0, 0.5, 1.0, 1.9)]
    assert abs(xs[0] - 40) < 2 and abs(xs[2] - 160) < 2 and abs(xs[3] - 160) < 2
    assert 60 < xs[1] < 158                                        # eased motion in between
    b = asset_buf(rc, "s", src_t=1.0)
    assert _box_x(b, 100, 2) is not None and _box_x(b, 100, 0) is None    # slot recoloured the box blue
    # text layer drawn (rlottie alone draws nothing for ty 5), animator fades it in, slot changes it
    top = lambda buf: float(buf.region((0, 20, 200, 60))[..., 3].sum())  # noqa: E731
    assert top(asset_buf(rc, "l", src_t=0.0)) < 1.0
    full = top(asset_buf(rc, "l", src_t=1.9))
    assert full > 100
    assert top(asset_buf(rc, "s", src_t=1.9)) > full * 1.5            # "WWWWWWWW" is wider than "Hello"


def test_lottie_subframes_segments_and_loop(tmp_path):
    p, _ = _lottie_json(tmp_path)
    rc = make_rc(f'<lottie id="l" src="{p}" width="200" height="150"/>'
                 f'<lottie id="o" src="{p}" width="200" height="150" segment="outro"/>'
                 f'<lottie id="r" src="{p}" width="200" height="150" segment="0,30"/>',
                 '<layer id="L" asset="r" loop="3"/>', tmp_path, w=200, h=150)
    a = _box_x(asset_buf(rc, "l", src_t=10 / 30), 100, 0)
    b = _box_x(asset_buf(rc, "l", src_t=10.5 / 30), 100, 0)
    assert b - a > 0.5                                                   # half-frame times move the box
    assert abs(_box_x(asset_buf(rc, "o", src_t=0.0), 100, 0) - 160) < 2  # outro starts at frame 30
    assert lottie.segment_duration(rc, rc.doc.ids["r"]) == pytest.approx(1.0)
    # layer loop over the 1 s segment: t=1.25 plays like t=0.25
    x_loop = _box_x(Buf_frame(rc, 1.25), 100, 0)
    x_ref = _box_x(Buf_frame(rc, 0.25), 100, 0)
    assert abs(x_loop - x_ref) < 1


def Buf_frame(rc, t):
    from scenerender.raster import Buf
    return Buf(rc.render_frame(t).px, 0, 0)


def test_lottie_container_animation_choice_and_scaling(tmp_path):
    rc = make_rc('<lottie id="z" src="lottie_demo.lottie" width="200" height="150"/>'
                 '<lottie id="d" src="lottie_demo.lottie" width="200" height="150" animation="demo"/>'
                 '<lottie id="w" src="lottie_demo.json" width="400" height="150"/>', '', tmp_path, w=400, h=300)
    assert _box_x(asset_buf(rc, "z", src_t=1.0), 100, 1) is not None     # active animation "alt": green box
    assert _box_x(asset_buf(rc, "d", src_t=1.0), 100, 0) is not None     # @animation="demo": red box
    # wide box: canvas fitted with xMidYMid meet -> offset by (400 - 200) / 2
    assert abs(_box_x(asset_buf(rc, "w", src_t=1.0), 100, 0) - 260) < 2
    # raster follows the projected size: a 3x scale keeps the edge sharp (no 3-px blur ramp)
    M = np.diag([3.0, 3.0, 1.0])
    big = asset_buf(rc, "d", M=M, src_t=1.0).region((0, 0, 600, 450))
    row = big[300, :, 3]
    ramp = int(((row > 0.05) & (row < 0.95)).sum())
    assert ramp <= 4


def test_lottie_scale_time_and_values():
    d = {"fr": 30, "ip": 0, "op": 60, "markers": [{"tm": 30, "dr": 10, "cm": "m"}],
         "layers": [{"ip": 0, "op": 60, "st": 5, "ks": {"p": {"a": 1, "k": [{"t": 0, "s": [0]}, {"t": 10, "s": [10]}]}}}]}
    lottie.scale_time(d, 4)
    assert d["fr"] == 120 and d["op"] == 240 and d["markers"][0]["tm"] == 120 and d["layers"][0]["st"] == 20
    assert d["layers"][0]["ks"]["p"]["k"][1]["t"] == 40
    assert lottie.lottie_value(d["layers"][0]["ks"]["p"], 20)[0] == pytest.approx(5.0)
    sel = {"s": {"k": 0}, "e": {"k": 50}, "o": {"k": 0}, "r": 1, "sh": 1}
    assert lottie._selector_mult(sel, 0, 4, 0, None) == pytest.approx(1.0)
    assert lottie._selector_mult(sel, 3, 4, 0, None) == pytest.approx(0.0)


def test_lottie_missing_file_draws_nothing(tmp_path):
    rc = make_rc('<lottie id="l" src="nope.lottie" width="20" height="10"/>', '<layer id="L" asset="l"/>', tmp_path)
    assert rc.asset_size(rc.doc.ids["l"], None) == (20.0, 10.0)
    assert frame_rgb(rc, 0.0).max() == 0


# ---------------------------------------------------------------- formula
TEX = r"\frac{a+b}{\sqrt{x^2+1}} + \sum_{k=1}^{n} \frac{1}{k^2}"


def test_formula_latex_vector(tmp_path):
    if not formula.have_latex():
        pytest.skip("no pdflatex/pdftocairo/librsvg")
    rc = make_rc(f'<formula id="f" tex="{TEX}" width="400" height="120" size="32" color="#FF0000FF"/>', '',
                 tmp_path, w=1600, h=480)
    b = asset_buf(rc, "f")
    px = b.region((0, 0, 400, 120))
    ys, xs = np.nonzero(px[..., 3] > 0.5)
    assert ys.max() - ys.min() > 60                  # stacked fraction + limits: much taller than one line
    assert px[..., 0].max() > 0.9 and px[..., 1].max() < 0.05
    big = asset_buf(rc, "f", M=np.diag([4.0, 4.0, 1.0])).px[..., 3]
    assert big.sum() / px[..., 3].sum() == pytest.approx(16, rel=0.1)   # vector: area scales with the square


def test_formula_mathtext_fallback_and_rewrites(tmp_path, monkeypatch):
    monkeypatch.setattr(formula, "_E", "mathtext")
    assert formula.mathtext_source(r"\text{if } \binom{n}{k} \frac{a}{\frac{b}{c}}") == \
        r"\mathrm{if\ } \left(\genfrac{}{}{0}{}{n}{k}\right) \dfrac{a}{\frac{b}{c}}"
    rc = make_rc(f'<formula id="f" tex="{TEX}" width="400" height="120" size="32"/>', '', tmp_path, w=400, h=120)
    px = asset_buf(rc, "f").region((0, 0, 400, 120))
    ys, _ = np.nonzero(px[..., 3] > 0.5)
    assert ys.max() - ys.min() > 50
    assert formula.mathtext_path(r"\begin{pmatrix}a\end{pmatrix}", 20) is None     # unsupported -> None


def test_formula_fits_box_and_paint(tmp_path):
    rc = make_rc(f'<formula id="f" tex="{TEX}" width="120" height="40" size="48"/>', '', tmp_path, w=120, h=40)
    b = asset_buf(rc, "f")
    ys, xs = np.nonzero(b.px[..., 3] > 0.1)
    assert xs.min() + b.x0 >= -1 and xs.max() + b.x0 <= 121                     # shrunk to fit the width


# ---------------------------------------------------------------- chart
KINDS = ["bar", "column", "line", "area", "pie", "donut", "scatter", "counter", "progress"]


@pytest.mark.parametrize("kind", KINDS)
def test_chart_every_kind_draw_on(tmp_path, kind):
    vals = ("1 2 3 4 2 5" if kind == "scatter" else "1 2 3 4")
    rc = make_rc(f'<chart id="c" kind="{kind}" width="300" height="180" labels="alpha,beta,gamma,delta" showValues="true" '
                 f'progress="0"><series name="Series one" values="{vals}"/><series name="Series two" values="{vals}"/>'
                 '<animate property="progress"><key time="0" value="0"/><key time="1" value="1"/></animate></chart>',
                 '', tmp_path, w=300, h=180)
    cov = [_coverage(asset_buf(rc, "c", t=t)) for t in (0.0, 0.5, 1.0)]
    assert cov[2] > 200
    if kind != "counter":                                   # counter: same digit count at 0.5 and 1
        assert cov[1] < cov[2]
    if kind not in ("counter", "progress"):
        assert cov[0] == 0


def test_chart_titles_from_json_and_legend(tmp_path):
    rc = make_rc('<chart id="c" kind="column" width="400" height="240" src="chart_data.json"/>'
                 '<chart id="n" kind="column" width="400" height="240" src="chart_data.json" showAxes="false"/>',
                 '', tmp_path, w=400, h=240)
    series, labels, titles = chart._series(rc, rc.doc.ids["c"], Ctx(t=0, comp_t=0), "column")
    assert [s["name"] for s in series] == ["Scenes", "Templates"] and labels[0] == "2022"
    assert titles == ("Year", "Renders (k)") and series[1]["color"] == "#FF6B6BFF"
    a = asset_buf(rc, "c").region((0, 0, 400, 240))
    n = asset_buf(rc, "n").region((0, 0, 400, 240))
    assert a[:, :25, 3].sum() > n[:, :25, 3].sum() + 20          # rotated value-axis title + ticks on the left
    assert a[:18, :, 3].sum() > 30 and n[:18, :, 3].sum() > 30    # legend row on top in both


def test_chart_nice_ticks_and_general_numbers():
    assert chart._ticks(0, 22, 5) == [0, 5, 10, 15, 20, 25]
    assert chart._ticks(0, 22, 4) == [0, 10, 20, 30]
    assert chart._ticks(-2, 7, 4) == [-2.5, 0, 2.5, 5, 7.5]
    assert chart._tick_decimals([0, 2.5, 5]) == 1
    rc = type("R", (), {})()
    ch = chart._Chart.__new__(chart._Chart)
    ch.fmt = None
    assert ch.num(4.5, 2) == "4.5" and ch.num(7, 2) == "7" and ch.num(6.25, 2) == "6.25"
    ch.fmt = "0.0"
    assert ch.num(7, 2) == "7.0"
    del rc


def test_chart_text_style_applies(tmp_path):
    base = '<chart id="c" kind="counter" width="300" height="120" textStyle="{s}"><series name="n" values="1234"/></chart>'
    styles = ('<styles><textStyle id="a" font="DejaVu Sans" color="#FFFFFFFF"/>'
              '<textStyle id="b" font="DejaVu Sans" color="#FFFFFFFF" strokeColor="#FF0000FF" strokeWidth="4"/></styles>')

    def rc_for(sid):
        src = f"""<?xml version="1.0"?><scene version="1.1"><project width="300" height="120" fps="30" duration="1"/>
        {styles}<assets>{base.format(s=sid)}</assets><composition/></scene>"""
        p = tmp_path / f"{sid}.xml"
        p.write_text(src)
        doc = document.load(str(p), base=MEDIA)
        return RenderContext(doc, Evaluator(doc))
    a = asset_buf(rc_for("a"), "c").px
    b = asset_buf(rc_for("b"), "c").px
    red = lambda px: float(((px[..., 0] > 0.5) & (px[..., 1] < 0.2)).sum())  # noqa: E731
    assert red(a) == 0 and red(b) > 50                                         # textStyle stroke is drawn


def test_chart_progress_ring_vs_bar(tmp_path):
    rc = make_rc('<chart id="r" kind="progress" width="120" height="120"><series name="p" values="0.5"/></chart>'
                 '<chart id="b" kind="progress" width="300" height="60"><series name="p" values="0.5"/></chart>',
                 '', tmp_path, w=300, h=120)
    r = asset_buf(rc, "r").region((0, 0, 120, 120))
    assert r[60, 60, 3] < 0.05 and r[2:10, 55:65, 3].max() > 0.5             # hollow ring
    b = asset_buf(rc, "b").region((0, 0, 300, 60))
    row = b[30, :, :]
    left, right = row[20, :3].sum(), row[280, :3].sum()
    assert left > right * 2                                                    # filled half is brighter


# ---------------------------------------------------------------- video colour management
def test_video_10bit_log_decodes_to_linear(tmp_path):
    rc = make_rc('<video id="v" src="log10.mkv" width="64" height="16" fps="10" duration="0.5" '
                 'colorSpace="rec2020" transfer="slog3"/>', '', tmp_path, w=64, h=16)
    px = asset_buf(rc, "v").region((0, 0, 64, 16))
    assert np.allclose(px[8, 4, :3], 0.18, atol=0.002)                 # S-Log3 18 % grey -> 0.18 linear
    ramp = px[8, 32:, 0]
    assert np.all(np.diff(ramp[3:]) > 0) and ramp[-1] > 30             # HDR headroom kept (> 1.0)
    info = video.stream_format(os.path.join(MEDIA, "log10.mkv"))
    assert info["depth"] == 10 and not info["yuv"]


def test_video_precision_16bit_vs_8bit(tmp_path):
    rc = make_rc('<video id="v" src="log10.mkv" width="64" height="16" fps="10" duration="0.5" '
                 'colorSpace="rec2020" transfer="slog3"/>', '', tmp_path, w=64, h=16)
    dec = video.decoder(os.path.join(MEDIA, "log10.mkv"), Fraction(10), 64, 16, False, deep=True)
    fr = dec.frame(0)
    assert fr.dtype == np.dtype("<u2")
    levels = np.unique(fr[8, 32:, 0] >> 6)                              # 10-bit codes survive
    assert len(levels) == 32 and (levels[1] - levels[0]) in (32, 33)
    del rc


def test_video_hlg_tagged_and_transfer_funcs(tmp_path):
    info = video.stream_format(os.path.join(MEDIA, "hlg10.mkv"))
    assert info["depth"] == 10 and info["yuv"] and info["matrix"] == "bt2020nc" and info["trc"] == "arib-std-b67"
    rc = make_rc('<video id="v" src="hlg10.mkv" width="64" height="36" fps="10" duration="1" colorSpace="rec2020" transfer="hlg"/>'
                 '<video id="s" src="hlg10.mkv" width="64" height="36" fps="10" duration="1"/>', '', tmp_path)
    hdr = asset_buf(rc, "v").px[..., :3].max()
    sdr = asset_buf(rc, "s").px[..., :3].max()
    assert hdr > 1.5 and sdr <= 1.01                                   # HLG peak above diffuse white
    for tf, grey in (("clog3", 0.3434), ("redlog3g10", 1 / 3), ("flog2", 0.391), ("nlog", 0.3637)):
        code = float(video.encode_transfer(np.array(0.18), tf))
        assert code == pytest.approx(grey, abs=0.002), tf
        assert float(video.decode_transfer(np.array(code), tf)) == pytest.approx(0.18, rel=1e-3), tf


def test_video_frame_mix(tmp_path):
    rc = make_rc('<video id="v" src="index.mkv" width="16" height="8" fps="12" duration="2"/>',
                 '<layer id="L" asset="v" boxWidth="64" boxHeight="36" fit="fill" frameBlend="frame-mix"/>'
                 , tmp_path)
    n0, n1, u = video.mix_indices(0.5 / 12 + 3 / 12, Fraction(12), 2.0)
    assert (n0, n1) == (3, 4) and u == pytest.approx(0.5)
    px = frame_rgb(rc, 3.5 / 12)
    lin = lambda c: ((c / 255 + 0.055) / 1.055) ** 2.4  # noqa: E731
    want = (lin(30) + lin(40)) / 2                                      # mixed in linear light
    got = px[18, 32, 0] / 255
    assert abs(((got + 0.055) / 1.055) ** 2.4 - want) < 0.004
    assert abs(int(frame_rgb(rc, 3 / 12)[18, 32, 0]) - 30) <= 1        # exact frame times are not blended


def test_source_timecode():
    from lxml import etree
    a = etree.fromstring('<video fps="25" timecodeStart="01:00:00:00"/>')
    assert video.source_timecode(a, 1.0) == "01:00:01:00"
    d = etree.fromstring('<video fps="30000/1001" timecodeStart="00:00:59;28"/>')
    assert video.source_timecode(d, 2 / (30000 / 1001)) == "00:01:00;02"          # frames ;00 ;01 dropped


def test_provenance_sha_and_proxy(tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(video, "warn_once", lambda cat, name, msg=None: warned.append(msg or ""))
    import hashlib
    sha = hashlib.sha256(open(os.path.join(MEDIA, "index.mkv"), "rb").read()).hexdigest()
    rc = make_rc(f'<video id="v" src="index.mkv" width="16" height="8" fps="12" duration="2" sha256="{sha}" proxy="index_ntsc.mkv"/>'
                 f'<video id="b" src="index.mkv" width="16" height="8" fps="12" duration="2" sha256="{"1" * 64}"/>',
                 '', tmp_path)
    assert video.provenance_src(rc, rc.doc.ids["v"]).endswith("index.mkv")
    video.provenance_src(rc, rc.doc.ids["b"])
    assert any("does not match" in m for m in warned)
    rc.cache["representation"] = "proxy"
    assert video.provenance_src(rc, rc.doc.ids["v"]).endswith("index_ntsc.mkv")


# ---------------------------------------------------------------- the fixture document
def test_full_document_valid_and_renders():
    xmllint = shutil.which("xmllint")
    if xmllint:
        r = subprocess.run([xmllint, "--noout", "--schema", os.path.join(HERE, "..", "schema", "scene-render-1.1.xsd"),
                            FULL_DOC], capture_output=True)
        assert r.returncode == 0, r.stderr.decode()
    doc = document.load(FULL_DOC)
    assert not doc.validation_errors
    rc = RenderContext(doc, Evaluator(doc), scale=0.5)
    for t in (0.4, 1.7):
        assert frame_rgb(rc, t).std() > 10, t


def test_optical_flow_frame_blend_moves_instead_of_ghosting():
    from scenerender.assets.video import flow_frames, mix_frames
    a = np.zeros((40, 80, 4), np.float32)
    b = np.zeros_like(a)
    a[15:25, 10:20] = 1.0
    b[15:25, 30:40] = 1.0
    mid = flow_frames(a, b, 0.5)
    col = mid[20, :, 3]
    assert col[25] > 0.8                                  # the square sits halfway (x≈20..30) ...
    assert col[12] < 0.3 and col[35] < 0.3                # ... rather than two half-strength ghosts
    ghost = mix_frames(a, b, 0.5)[20, :, 3]
    assert ghost[12] == pytest.approx(0.5) and ghost[25] == 0.0
