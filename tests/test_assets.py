"""Asset handlers: video, vector, generator, chart, code, formula, audiogram, lottie, generated, mesh."""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
from fractions import Fraction

import numpy as np
import pytest

from scenerender import document
from scenerender.compositor import RenderContext
from scenerender.evaluator import Evaluator
from scenerender.raster import working_to_rgb8

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
MEDIA = os.path.join(FIX, "media")
DEMO = os.path.join(FIX, "assets_demo.xml")

# Import only the handler modules these tests need (other plugin modules may be in flux).
for _m in ("nodes.core", "assets.image", "assets.video", "assets.vector", "assets.generator", "assets.chart",
           "assets.code", "assets.formula", "assets.audiogram", "assets.lottie", "assets.generated", "assets.mesh",
           "blend"):
    importlib.import_module(f"scenerender.{_m}")

from scenerender.assets import chart, code, formula, vector, video  # noqa: E402
from scenerender.registry import ASSETS, FULL, NONE  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def media():
    need = ["testsrc.mp4", "index.mkv", "index_ntsc.mkv", "index_alpha.mkv", "tone.wav", "badge.svg", "gen.png",
            "chart.csv"]
    if not all(os.path.exists(os.path.join(MEDIA, n)) for n in need):
        subprocess.run([os.sys.executable, os.path.join(MEDIA, "make_media.py")], check=True)


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SCENERENDER_CACHE", str(tmp_path / "cache"))


def make_rc(xml_assets: str, xml_comp: str, tmp_path, *, w=64, h=36, fps=30, dur=4, scale=1.0, linear=True):
    src = f"""<?xml version="1.0"?>
<scene version="1.1">
  <project width="{w}" height="{h}" fps="{fps}" duration="{dur}" seed="7" background="#000000FF"
           linearLight="{'true' if linear else 'false'}"/>
  <assets>{xml_assets}</assets>
  <composition>{xml_comp}</composition>
</scene>"""
    p = tmp_path / "doc.xml"
    p.write_text(src)
    doc = document.load(str(p), base=MEDIA)
    return RenderContext(doc, Evaluator(doc), scale=scale)


def frame_rgb(rc, t):
    return working_to_rgb8(rc.render_frame(t).px, rc.linear)


def asset_buf(rc, aid, M=None, t=0.0, src_t=0.0):
    from scenerender.evaluator import Ctx
    a = rc.doc.ids[aid]
    fn = ASSETS.get(a.tag)
    return fn(rc, a, np.eye(3) if M is None else M, Ctx(t=t, comp_t=t), src_t=src_t)


# ---------------------------------------------------------------- registry
def test_support_levels():
    assert ASSETS.get("lottie") is not None
    for k in ("video", "vector", "generator", "chart", "code", "formula", "audiogram", "generated"):
        assert ASSETS.get(k) is not None, k
    assert ASSETS.level("generator") == FULL


# ---------------------------------------------------------------- video
def test_video_display_size():
    from lxml import etree
    a = etree.fromstring('<video width="64" height="36" pixelAspect="2" rotation="90"/>')
    assert video.display_size(a) == (36.0, 128.0)
    a = etree.fromstring('<video width="64" height="36"/>')
    assert video.display_size(a) == (64.0, 36.0)


def test_frame_index():
    assert video.frame_index(0.0, Fraction(12), 2.0) == 0
    assert video.frame_index(0.5, Fraction(12), 2.0) == 6
    assert video.frame_index(1 / 12, Fraction(12), 2.0) == 1          # exact boundaries land on the frame
    assert video.frame_index(99.0, Fraction(12), 2.0) == 23           # clamped to the last frame
    assert video.frame_index(-1.0, Fraction(12), 2.0) == 0


@pytest.mark.parametrize("name, fps, n", [("index.mkv", Fraction(12), 24), ("index_ntsc.mkv", Fraction(30000, 1001), 20)])
def test_decoder_frames_forward_jump_and_seek_back(name, fps, n):
    dec = video._Decoder(os.path.join(MEDIA, name), fps, 16, 8, False)
    try:
        seq = [int(dec.frame(i)[0, 0, 0]) for i in range(5)]
        assert seq == [0, 10, 20, 30, 40]
        assert dec.restarts == 1                                        # sequential reads reuse the process
        assert int(dec.frame(n - 3)[0, 0, 0]) == 10 * (n - 3)          # forward jump
        assert int(dec.frame(2)[0, 0, 0]) == 20                         # cached (LRU) or re-seek
        dec.frames.clear()
        r0 = dec.restarts
        assert int(dec.frame(1)[0, 0, 0]) == 10                         # backward: re-seek
        assert dec.restarts == r0 + 1
        assert int(dec.frame(n + 50)[0, 0, 0]) == 10 * (n - 1)          # past the end holds the last frame
    finally:
        dec.close()


def test_video_layer_pipeline(tmp_path):
    rc = make_rc('<video id="v" src="index.mkv" width="16" height="8" fps="12" duration="2"/>',
                 '<layer id="L" asset="v" boxWidth="64" boxHeight="36" fit="fill" clipIn="0.25"/>', tmp_path)
    for t, frame in ((0.0, 3), (0.5, 9), (1.0, 15), (0.1, 4)):          # includes a seek back
        px = frame_rgb(rc, t)
        assert abs(int(px[18, 32, 0]) - 10 * frame) <= 1, (t, px[18, 32])


def test_video_rotation_and_alpha(tmp_path):
    rc = make_rc('<video id="v" src="testsrc.mp4" width="64" height="36" fps="10" duration="1" rotation="90"/>'
                 '<video id="a" src="index_alpha.mkv" width="16" height="8" fps="4" duration="1"/>'
                 '<video id="n" src="index_alpha.mkv" width="16" height="8" fps="4" duration="1" alpha="none"/>',
                 '', tmp_path, w=64, h=64)
    b = asset_buf(rc, "v")
    assert rc.asset_size(rc.doc.ids["v"], None) == (36.0, 64.0)
    assert b.px.shape[0] > b.px.shape[1]                                # portrait after rotating
    a = asset_buf(rc, "a", src_t=0.6)
    assert abs(float(a.px[5, 5, 3]) - 128 / 255) < 0.02
    n = asset_buf(rc, "n", src_t=0.6)
    assert float(n.px[5, 5, 3]) > 0.99


def test_video_audio_helper(tmp_path):
    rc = make_rc('<video id="v" src="testsrc.mp4" width="64" height="36" fps="10" duration="1" hasAudio="true"/>'
                 '<video id="s" src="testsrc.mp4" width="64" height="36" fps="10" duration="1"/>', '', tmp_path)
    p = video.video_audio(rc, rc.doc.ids["v"])
    assert p and p.endswith(".wav") and os.path.getsize(p) > 1000
    assert video.video_audio(rc.doc, rc.doc.ids["v"]) == p              # cached; Document accepted too
    assert video.video_audio(rc, rc.doc.ids["s"]) is None               # hasAudio defaults to false
    import wave
    with wave.open(p) as f:
        assert f.getframerate() == 8000 and abs(f.getnframes() / 8000 - 1.0) < 0.1


# ---------------------------------------------------------------- vector
def _coverage(buf):
    return float(buf.px[..., 3].sum()) if buf is not None else 0.0


def test_vector_shapes_and_evenodd(tmp_path):
    ring = "M0 0 L100 0 L100 100 L0 100 Z M25 25 L75 25 L75 75 L25 75 Z"
    rc = make_rc(f'<vector id="p" shape="path" width="100" height="100" path="{ring}"/>'
                 f'<vector id="nz" shape="path" width="100" height="100" path="{ring}" fillRule="nonzero"/>'
                 '<vector id="s" shape="star" width="100" height="100" points="5" stroke="#FF0000FF" strokeWidth="4"/>',
                 '', tmp_path, w=100, h=100, linear=False)
    p, nz = asset_buf(rc, "p"), asset_buf(rc, "nz")
    assert p.region((50, 50, 51, 51))[0, 0, 3] == 0                     # evenodd (vector default) makes a hole
    assert nz.region((50, 50, 51, 51))[0, 0, 3] == pytest.approx(1.0)
    assert _coverage(asset_buf(rc, "s")) > 1000


def test_svg_rsvg_and_subset(tmp_path):
    rc = make_rc('<vector id="svg" shape="svg" src="badge.svg" width="100" height="100"/>', '', tmp_path,
                 w=100, h=100, linear=False)
    a = rc.doc.ids["svg"]
    from scenerender.evaluator import Ctx
    sub = vector.render_vector(rc, a, np.eye(3), Ctx(t=0, comp_t=0), force_subset=True)
    reg = sub.region((0, 0, 100, 100))
    assert reg[50, 50, 3] > 0.99 and reg[50, 30, 1] > reg[50, 30, 0]    # teal circle (subset renderer)
    if vector.HAVE_RSVG:
        full = asset_buf(rc, "svg").region((0, 0, 100, 100))
        assert np.abs(full - reg).mean() < 0.02


# ---------------------------------------------------------------- generator
GEN = """
<generator id="n" kind="fractal-noise" width="64" height="36" scale="20" octaves="3" seed="4">
  <animate property="evolution"><key time="0" value="0"/><key time="1" value="1"/></animate>
</generator>
<generator id="n2" kind="fractal-noise" width="64" height="36" scale="20" octaves="3" seed="5"/>
<generator id="g" kind="film-grain" width="64" height="36"/>
<generator id="k" kind="checkerboard" width="64" height="36" scale="8"/>
<generator id="grad" kind="gradient" width="64" height="36" paint="#FF0000FF" paint2="#0000FFFF"/>
"""


@pytest.mark.parametrize("kind", ["solid", "gradient", "noise", "fractal-noise", "cells", "checkerboard", "grid",
                                  "stripes", "film-grain", "light-rays"])
def test_generator_kinds_draw(tmp_path, kind):
    rc = make_rc(f'<generator id="x" kind="{kind}" width="64" height="36" scale="10"/>', '', tmp_path)
    b = asset_buf(rc, "x")
    assert b is not None and _coverage(b) > 64 * 36 * 0.9
    if kind not in ("solid",):
        assert b.px[..., :3].std() > 0.01


def test_generator_deterministic_seeded_animated(tmp_path):
    a = asset_buf(make_rc(GEN, '', tmp_path), "n", t=0.5).px
    b = asset_buf(make_rc(GEN, '', tmp_path), "n", t=0.5).px
    assert np.array_equal(a, b)
    rc = make_rc(GEN, '', tmp_path)
    assert not np.array_equal(a, asset_buf(rc, "n", t=0.0).px)          # evolution animates
    assert not np.array_equal(asset_buf(rc, "n2").px, asset_buf(rc, "n", t=0.0).px)   # seed matters
    g0, g1 = asset_buf(rc, "g", t=0.0).px, asset_buf(rc, "g", t=1 / 30).px
    assert not np.array_equal(g0, g1) and np.array_equal(g0, asset_buf(rc, "g", t=0.0).px)


def test_generator_gradient_and_checker(tmp_path):
    rc = make_rc(GEN, '', tmp_path, linear=False)
    g = asset_buf(rc, "grad").region((0, 0, 64, 36))
    assert g[18, 1, 0] > 0.9 and g[18, 62, 2] > 0.9                     # paint at the start, paint2 at the end
    k = asset_buf(rc, "k").region((0, 0, 64, 36))
    assert k[4, 4, 0] > 0.9 and k[4, 12, 0] < 0.1 and k[12, 12, 0] > 0.9


def test_generator_resolution_follows_scale(tmp_path):
    from scenerender.compositor import scale as S
    rc = make_rc(GEN, '', tmp_path)
    asset_buf(rc, "n2", M=S(0.25, 0.25))
    keys = [k for k in rc.cache if isinstance(k, tuple) and k[0] == "gen"]
    rw, rh = keys[-1][7], keys[-1][8]
    assert rw <= 17 and rh <= 10                                        # computed at the drawn size


# ---------------------------------------------------------------- chart
CHART = """
<chart id="c" kind="column" width="200" height="120" labels="a,b,c" progress="0">
  <series name="s" values="1 2 3"/>
  <animate property="progress"><key time="0" value="0"/><key time="1" value="1"/></animate>
</chart>
"""


def test_chart_progress_zero_draws_nothing(tmp_path):
    rc = make_rc(CHART, '', tmp_path, w=200, h=120)
    assert asset_buf(rc, "c", t=0.0) is None
    half, full = asset_buf(rc, "c", t=0.5), asset_buf(rc, "c", t=1.0)
    assert 0 < _coverage(half) < _coverage(full)


@pytest.mark.parametrize("kind", ["bar", "column", "line", "area", "pie", "donut", "scatter", "counter", "progress"])
def test_chart_kinds(tmp_path, kind):
    rc = make_rc(f'<chart id="c" kind="{kind}" width="200" height="120" labels="a,b,c,d" showValues="true">'
                 '<series name="s" values="1 2 3 4" color="#FF0000FF"/><series name="t" values="2 1 4 3"/></chart>',
                 '', tmp_path, w=200, h=120)
    assert _coverage(asset_buf(rc, "c")) > 50


@pytest.mark.parametrize("v, fmt, out", [
    (1234.5, None, "1,234"), (1234.5, "{:,.1f}", "1,234.5"), (0.256, "%.1f%%", "0.3%"), (42, "%d items", "42 items"),
    (5400, "#,##0", "5,400"), (3.14159, "0.00", "3.14"), (0.25, "0%", "25%"), (12.5, "$#,##0.0", "$12.5"),
    (7, "0 'kg'", "7 kg"), (2.5, "#,##0.##", "2.5"), (3.0, "0.0#", "3.0"),
])
def test_format_number(v, fmt, out):
    assert chart.format_number(v, fmt) == out


# ---------------------------------------------------------------- code
def test_code128_and_ean_encoders():
    widths = [sum(int(c) for c in p) for p in code._C128]
    assert widths[:106] == [11] * 106 and widths[106] == 13
    assert code.ean_check("400638133393") == 1
    assert code.ean13_digits("03600029145", upc=True) == "0036000291452"
    assert len(code.ean13_modules("4006381333931")) == 95
    vals = code.code128_values("123456")
    assert vals[0] == 105 and vals[1:4] == [12, 34, 56]                 # all digits: code set C


try:
    import zxingcpp
except ImportError:  # pragma: no cover - optional decoder
    zxingcpp = None


@pytest.mark.skipif(zxingcpp is None, reason="zxing-cpp not installed (needed to decode)")
@pytest.mark.parametrize("kind, data, expect", [
    ("qr", "https://example.com/x?y=1", None), ("code128", "SR-2026-000123", None), ("code128", "AB1234567", None),
    ("ean13", "400638133393", "4006381333931"), ("upc-a", "03600029145", "036000291452"),
    ("datamatrix", "SCENE-RENDER-1.1", None), ("pdf417", "scene-render", None),
])
def test_codes_decode(tmp_path, kind, data, expect):
    w, h = (240, 240) if kind in ("qr", "datamatrix") else (360, 140)
    rc = make_rc(f'<code id="c" kind="{kind}" data="{data}" width="{w}" height="{h}" quietZone="10"/>', '',
                 tmp_path, w=w, h=h, linear=False)
    px = working_to_rgb8(asset_buf(rc, "c").region((0, 0, w, h)), False)
    res = zxingcpp.read_barcode(px)
    assert res is not None and res.valid
    # UPC-A is bar-identical to EAN-13 with a leading 0; readers may report either
    assert res.text in (expect or data, "0" + (expect or data))


# ---------------------------------------------------------------- formula
def test_tex_to_markup():
    m = formula.tex_to_markup(r"\alpha^{2} + \mathrm{sin}\,x_i \leq \frac{a}{b}")
    assert "α" in m and "<sup>2</sup>" in m and "≤" in m and "<sub><i>i</i></sub>" in m and "⁄" in m
    rc_markup = formula.tex_to_markup(r"\mathrm{frame}")
    assert rc_markup == "frame"


def test_formula_draws(tmp_path):
    rc = make_rc('<formula id="f" tex="E = mc^2" width="200" height="60" size="30"/>', '', tmp_path, w=200, h=60)
    assert _coverage(asset_buf(rc, "f")) > 50


# ---------------------------------------------------------------- audiogram
AG = """
<audio id="au" src="tone.wav" duration="1"/>
<audiogram id="ag" source="au" width="200" height="100" style="bars" bars="16" smoothing="0.5"/>
<audiogram id="w" source="au" width="200" height="100" style="wave"/>
"""


def test_audiogram_deterministic_and_reactive(tmp_path):
    rc1 = make_rc(AG, '', tmp_path, w=200, h=100)
    a = asset_buf(rc1, "ag", src_t=0.8).px
    rc2 = make_rc(AG, '', tmp_path, w=200, h=100)
    asset_buf(rc2, "ag", src_t=0.3)                                     # different render history
    b = asset_buf(rc2, "ag", src_t=0.8).px
    assert np.array_equal(a, b)
    quiet = _coverage(asset_buf(rc1, "ag", src_t=0.25))
    loud = _coverage(asset_buf(rc1, "ag", src_t=0.8))
    assert loud > quiet * 1.5
    for style in ("line", "wave", "circle", "spectrum"):
        rc = make_rc(AG.replace('style="bars"', f'style="{style}"'), '', tmp_path, w=200, h=100)
        assert _coverage(asset_buf(rc, "ag", src_t=0.8)) > 20, style


# ---------------------------------------------------------------- generated / lottie / mesh
def _sha(p):
    import hashlib
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def test_generated_cache_verification(tmp_path):
    good = _sha(os.path.join(MEDIA, "gen.png"))
    rc = make_rc(f'<generated id="ok" kind="image" provider="p" model="m" cache="gen.png" cacheSha256="{good}"/>'
                 f'<generated id="bad" kind="image" provider="p" model="m" cache="gen.png" cacheSha256="{"0" * 64}"/>'
                 f'<generated id="sp" kind="speech" provider="p" model="m" cache="tone.wav" cacheSha256="{_sha(os.path.join(MEDIA, "tone.wav"))}"/>',
                 '', tmp_path)
    assert rc.asset_size(rc.doc.ids["ok"], None) == (32.0, 32.0)
    assert _coverage(asset_buf(rc, "ok")) > 32 * 32 * 0.9
    assert asset_buf(rc, "bad") is None
    assert rc.asset_size(rc.doc.ids["sp"], None) == (0.0, 0.0)
    from scenerender.assets import audio_source_path
    assert audio_source_path(rc, rc.doc.ids["sp"]).endswith("tone.wav")
    assert audio_source_path(rc, rc.doc.ids["bad"]) is None


def test_lottie_missing_file_skips(tmp_path):
    rc = make_rc('<lottie id="l" src="nope.lottie" width="20" height="10"/>', '<layer id="L" asset="l"/>', tmp_path)
    assert rc.asset_size(rc.doc.ids["l"], None) == (20.0, 10.0)
    assert frame_rgb(rc, 0.0).max() == 0


# ---------------------------------------------------------------- demo document
def test_demo_document_valid_and_renders():
    xmllint = shutil.which("xmllint")
    if xmllint:
        r = subprocess.run([xmllint, "--noout", "--schema", os.path.join(HERE, "..", "schema", "scene-render-1.1.xsd"), DEMO],
                           capture_output=True)
        assert r.returncode == 0, r.stderr.decode()
    doc = document.load(DEMO)
    assert not doc.validation_errors
    rc = RenderContext(doc, Evaluator(doc), scale=0.5)
    for t in (0.5, 1.9, 2.5, 3.5):
        px = frame_rgb(rc, t)
        assert px.std() > 10, t
