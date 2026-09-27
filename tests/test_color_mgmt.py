"""Colour management to FULL: vendor log curves, PQ/EETF and HLG/OOTF, AgX (Blender), ACES 2.0 and
OpenColorIO pipelines, LUT formats and the working-space API."""
from __future__ import annotations

import colorsys
import math
import os
import textwrap

import numpy as np
import pytest

from scenerender import color as C

O = pytest.importorskip("PyOpenColorIO")


# ---------------------------------------------------------------- vendor transfer curves
def test_vendor_log_known_points():
    x18 = np.array(0.18)
    # Canon Log 3 and RED Log3G10 agree with OCIO's builtin camera transforms (ACES reference IDTs)
    assert float(C.encode(x18, "clog3")) == pytest.approx(0.343389, abs=2e-6)
    assert float(C.encode(x18, "redlog3g10")) == pytest.approx(1 / 3, abs=2e-6)
    # FUJIFILM F-Log2 data sheet: 18 % grey -> 39.1 %; N-Log: 18 % -> 650 cbrt(0.1875) / 1023
    assert float(C.encode(x18, "flog2")) == pytest.approx(0.245281 * math.log10(5.555556 * 0.18 + 0.064829) + 0.384316)
    assert float(C.encode(x18, "flog2")) == pytest.approx(0.391, abs=5e-4)
    assert float(C.encode(x18, "nlog")) == pytest.approx(650 * 0.1875 ** (1 / 3) / 1023)
    # the published breakpoints are continuous
    assert float(C.encode(np.array(0.000889), "flog2")) == pytest.approx(0.100686685370811, abs=2e-6)
    # (Nikon's published constants meet within 0.2 code values of 1023)
    assert float(C.encode(np.array(0.328 - 1e-9), "nlog")) == pytest.approx(float(C.encode(np.array(0.328), "nlog")), abs=2e-4)
    assert float(C.encode(np.array(0.014 * 0.9 - 1e-12), "clog3")) == pytest.approx(
        float(C.encode(np.array(0.014 * 0.9 + 1e-12), "clog3")), abs=1e-6)


@pytest.mark.parametrize("tf", ["clog3", "redlog3g10", "flog2", "nlog", "slog3", "logc3", "logc4", "vlog", "pq", "hlg"])
def test_transfer_exact_inverse(tf):
    x = np.array([0.0, 0.0005, 0.005, 0.02, 0.18, 0.5, 1.0, 4.0, 16.0])
    if tf in ("pq", "hlg"):
        x = x[x <= 4.9]
    assert np.allclose(C.decode(C.encode(x, tf), tf), x, rtol=1e-6, atol=1e-7)


@pytest.mark.parametrize("tf,src,dst", [("clog3", "Linear CinemaGamut D55", "CanonLog3 CinemaGamut D55"),
                                        ("redlog3g10", "Linear REDWideGamutRGB", "Log3G10 REDWideGamutRGB")])
def test_log_curves_match_ocio(tf, src, dst):
    cfg = C.ocio_config(C.STUDIO)
    x = np.array([0.0, 0.001, 0.01, 0.18, 0.9, 10.0], np.float32)
    ref = C.ocio_apply(cfg.getProcessor(src, dst).getDefaultCPUProcessor(), np.repeat(x[:, None], 3, 1))[:, 0]
    assert np.allclose(C.encode(x.astype(np.float64), tf), ref, atol=2e-6)


# ---------------------------------------------------------------- PQ / BT.2390 EETF
def test_pq_known_points_and_eetf():
    assert float(C.pq_encode_nits(100.0)) == pytest.approx(0.508078, abs=1e-5)
    assert float(C.pq_encode_nits(1000.0)) == pytest.approx(0.751827, abs=1e-5)
    assert float(C.pq_encode_nits(10000.0)) == pytest.approx(1.0)
    assert float(C.encode(np.array(1.0), "pq")) == pytest.approx(0.580688, abs=1e-5)      # 203 cd/m2
    E = np.linspace(0, 1, 1001)
    out = C.eetf_bt2390(E, (0.0, 10000.0), (0.0, 1000.0))
    ks = 1.5 * float(C.pq_encode_nits(1000.0)) - 0.5
    assert np.allclose(out[E < ks], E[E < ks])                                             # identity below the knee
    assert out[-1] == pytest.approx(float(C.pq_encode_nits(1000.0)), abs=1e-6)             # source peak -> target peak
    assert np.all(np.diff(out) >= -1e-12)
    # black lift: E3 = E2 + minLum (1 - E2)^4
    lifted = C.eetf_bt2390(np.array([0.0]), (0.0, 10000.0), (0.05, 1000.0))
    assert float(lifted[0]) == pytest.approx(float(C.pq_encode_nits(0.05)), abs=1e-6)


def test_pq_output_maps_scene_highlights_to_display_peak():
    rgb = np.array([[1.0, 1.0, 1.0], [40.0, 20.0, 5.0], [49.26, 49.26, 49.26]])
    code = C.encode_output(rgb, "pq", "rec2020", {"peak": 1000.0, "black": 0.0}, src_peak=10000.0)
    assert code[0, 0] == pytest.approx(0.580688, abs=1e-4)                 # reference white untouched
    assert code.max() <= float(C.pq_encode_nits(1000.0)) + 1e-6
    lin = C.pq_decode_nits(code[1])
    assert lin[0] / lin[1] == pytest.approx(2.0, rel=1e-4)                 # maxRGB mapping keeps hue ratios


# ---------------------------------------------------------------- HLG / BT.2100 OOTF
def test_hlg_system_gamma_and_reference_white():
    assert C.hlg_gamma(1000) == pytest.approx(1.2)
    assert C.hlg_gamma(2000) == pytest.approx(1.2 + 0.42 * math.log10(2))
    assert C.hlg_gamma(400) == pytest.approx(1.2 + 0.42 * math.log10(0.4))
    assert C.hlg_gamma(4000) == pytest.approx(1.2 * 1.111 ** 2)
    v = C.encode_output(np.array([[1.0, 1.0, 1.0]]), "hlg", "rec2020", {"peak": 1000.0})
    assert v[0, 0] == pytest.approx(0.75, abs=2e-3)                        # BT.2408: 203 cd/m2 -> 75 % HLG


def test_hlg_inverse_ootf_round_trip():
    rgb = np.array([[0.9, 0.2, 0.05], [0.1, 0.5, 1.2], [2.0, 2.0, 2.0]])
    for Lw, black in ((1000.0, 0.0), (2000.0, 0.0), (1000.0, 0.01)):
        V = C.encode_output(rgb, "hlg", "rec2020", {"peak": Lw, "black": black})
        g = C.hlg_gamma(Lw)
        beta = math.sqrt(3 * (black / Lw) ** (1 / g)) if black else 0.0
        E = C.hlg_inverse_oetf(np.clip((1 - beta) * V + beta, 0, None))           # BT.2100 display EOTF
        Ys = E @ np.array([0.2627, 0.6780, 0.0593])
        Fd = Lw * np.power(Ys, g - 1)[:, None] * E
        assert np.allclose(Fd / C.REF_WHITE, rgb, rtol=2e-3)


# ---------------------------------------------------------------- AgX: reference from the published scripts
def _ref_sigmoid(x):
    """Troy Sobotka's sigmoid.calculate_sigmoid (numpy.ma), transcribed for one scalar."""
    ma = np.ma
    pivots, slope, powers = np.array([10 / 16.5, 0.18 ** (1 / 2.4)]), 2.4, np.array([1.5, 1.5])
    limits = np.array([[0.0, 0.0], [1.0, 1.0]])

    def scale(lx, ly, tx, ty, p):
        a = ma.power(ma.multiply(slope, ma.subtract(lx, tx)), -p).filled(0.0)
        b = ma.subtract(ma.power(ma.divide(ma.multiply(slope, ma.subtract(lx, tx)), ma.subtract(ly, ty)), p).filled(0.0), 1.0)
        return ma.power(ma.multiply(a, b), -ma.divide(1.0, p)).filled(0.0)

    def expo(xi, p):
        return ma.divide(xi, ma.power(ma.add(1.0, ma.power(xi, p)), ma.divide(1.0, p)))

    def curve(xi, s, p, tx, ty):
        return ma.add(ma.multiply(s, expo(ma.divide(ma.multiply(slope, ma.subtract(xi, tx)), s), p)), ty)
    tx, ty = pivots
    s_toe = -scale(1 - limits[0, 0], 1 - limits[0, 1], 1 - tx, 1 - ty, powers[0])
    s_sh = scale(limits[1, 0], limits[1, 1], tx, ty, powers[1])
    return float(curve(x, s_toe, powers[0], tx, ty) if x < tx else curve(x, s_sh, powers[1], tx, ty))


def _ref_agx_srgb(rgb709):
    """AgXBasesRGB.py per colour (python max/min, colorsys HSV), then Blender's Rec.1886 decode."""
    to709 = np.linalg.inv(C._AGX_XYZ_TO_709)
    eg = np.linalg.inv(C._AGX_E_TO_XYZ) @ (to709 @ np.asarray(rgb709, float))
    eg = np.clip(eg, 0.18 * 2 ** -10, 0.18 * 2 ** 15)                      # Blender's lg2 shaper domain
    col = C._AGX_XYZ_TO_2020 @ (C._AGX_E_TO_XYZ @ eg)
    L = C._AGX_LUM2020
    # lower guard rail, Rec.2020
    Y = col @ L
    inv = max(col) - col
    y_cn = max(inv) - inv @ L + Y
    off = col + max(-min(col), 0.0)
    inv2 = max(off) - off
    y_new = max(inv2) - inv2 @ L + off @ L
    col = (y_cn / y_new if y_new > y_cn else 1.0) * off
    col = C._AGX_INSET @ col
    h0 = colorsys.rgb_to_hsv(*col)[0]
    logc = [(math.log2(c / 0.18) + 10) / 16.5 if c > 0 else -math.inf for c in col]
    sig = [_ref_sigmoid(v) if math.isfinite(v) else -math.inf for v in logc]
    lin = [s ** 2.4 if s >= 0 else 0.0 for s in sig]
    h1, s1, v1 = colorsys.rgb_to_hsv(*lin)
    if h1 - h0 > 0.5:
        h1 -= 1
    elif h1 - h0 < -0.5:
        h1 += 1
    col = np.array(colorsys.hsv_to_rgb((h0 + 0.4 * (h1 - h0)) % 1.0, s1, v1))
    col = C._AGX_OUTSET @ col
    col = C._AGX_XYZ_TO_709 @ (C._AGX_2020_TO_XYZ @ col)
    # sRGB lower guard rail (luminance measured in Rec.2020)
    d2s = C._AGX_XYZ_TO_2020 @ np.linalg.inv(C._AGX_XYZ_TO_709)
    lum = lambda v: (d2s @ v) @ L        # noqa: E731
    lerp = lambda t, a, b: (1 - t) * a + t * b   # noqa: E731
    Y = lum(col)
    inv = max(col) - col
    with np.errstate(invalid="ignore"):
        Y = lerp(np.clip(np.power(Y, 0.08), 0, 1), max(inv) - lum(inv) + Y, Y)
        off = col + max(-min(col), 0.0)
        inv2 = max(off) - off
        yn = lum(off)
        yn = lerp(np.clip(np.power(yn, 0.08), 0, 1), max(inv2) - lum(inv2) + yn, yn)
    col = (Y / max(yn, 1e-100) if yn > Y else 1.0) * off
    col = np.clip(np.power(np.maximum(col, 0), 1 / 2.4), 0, 1)
    return col ** 2.4


def test_agx_matches_published_algorithm():
    rng = np.random.default_rng(7)
    samples = np.concatenate([[[0.18] * 3, [1, 0, 0], [0, 1, 0], [0, 0, 1], [0.5, 0.2, 0.05], [8, 4, 0.5], [0.001, 0.002, 0.004]],
                              rng.uniform(0, 1, (40, 3)) ** 2 * 6])
    ours = C.agx(samples)
    ref = np.array([_ref_agx_srgb(s) for s in samples])
    assert np.allclose(ours, ref, atol=1e-9)
    assert np.allclose(ours[0], 0.18, atol=1e-9)                           # the fulcrum maps grey to grey
    greys = C.agx(np.repeat(np.geomspace(1e-3, 64, 50)[:, None], 3, 1))[:, 0]
    assert np.all(np.diff(greys) >= 0) and greys[-1] == pytest.approx(1.0)      # +6.5 stops over grey is white
    assert np.all(np.diff(greys[greys < 0.99]) > 0)


def test_agx_inset_matrix_from_parameters():
    # _inset_primaries reproduces Blender's literal AgX Log inset when rotation is zero ... and the outset rows sum to 1
    assert np.allclose(C._AGX_OUTSET.sum(1), 1.0)
    assert np.allclose(C._AGX_INSET.sum(1), 1.0, atol=1e-9)


# ---------------------------------------------------------------- ACES 2.0 and OCIO
def test_aces2_output_transform():
    sdr, prim = C.aces2(np.array([[0.18, 0.18, 0.18], [100.0, 100.0, 100.0]]))
    assert prim == "srgb"
    assert sdr[0, 0] == pytest.approx(0.1, abs=2e-3)                       # ACES 2.0 SDR: mid grey at 10 nits
    assert sdr[1].max() <= 1.0 + 1e-4
    hdr, prim = C.aces2(np.array([[0.18, 0.18, 0.18], [1000.0, 1000.0, 1000.0]]), "rec2020", "pq", 1000)
    assert prim == "rec2020"
    assert hdr[0, 0] * C.REF_WHITE == pytest.approx(14.5, abs=1.5)
    assert hdr[1, 0] * C.REF_WHITE <= 1000.0 * 1.01


def test_ocio_matrices_round_trip():
    cfg = C.ocio_config(C.STUDIO)
    x = np.array([[0.18, 0.18, 0.18], [0.9, 0.1, 0.05], [0.02, 0.4, 0.8]], np.float32)
    ref = C.ocio_apply(cfg.getProcessor("Linear Rec.709 (sRGB)", "ACEScg").getDefaultCPUProcessor(), x)
    ours = C.to_working(x, "linear-srgb", "auto", "acescg")
    assert np.allclose(ours, ref, atol=2e-3)
    back = C.ocio_apply(cfg.getProcessor("ACEScg", "Linear Rec.709 (sRGB)").getDefaultCPUProcessor(), ref)
    assert np.allclose(back, x, atol=1e-5)
    assert np.allclose(C.from_working(ours, "linear-srgb", "auto", "acescg"), x, atol=1e-5)


def test_working_space_api():
    code = np.array([[0.5, 0.25, 0.75]], np.float32)
    w = C.to_working(code, "srgb", "auto", "acescg")
    assert np.allclose(w, C.decode(code, "srgb") @ C.matrix("srgb", "ap1").T, atol=1e-6)
    assert np.allclose(C.from_working(w, "srgb", "auto", "acescg"), code, atol=1e-5)
    assert np.allclose(C.from_working(C.to_working(code, "rec2020", "pq", "srgb"), "rec2020", "pq", "srgb"), code, atol=1e-5)


def _doc(tmp_path, cm: str, extra: str = "") -> str:
    p = tmp_path / "cm.xml"
    p.write_text(textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <scene version="1.1">
          <project width="32" height="16" fps="10" duration="1" background="#000000FF"/>
          {cm}
          {extra}
          <composition>
            <shape id="grey" shape="rect" x="0" y="0" width="32" height="16" fill="#767676FF"/>
          </composition>
        </scene>"""))
    return str(p)


def _pixel(path, **kw):
    from scenerender.render import Renderer
    return Renderer.open(path, **kw).frame_rgb(0.0)[8, 16].astype(int)


def test_ocio_pipeline_untonemapped_is_identity(tmp_path):
    grey = _pixel(_doc(tmp_path, ""))
    doc = _doc(tmp_path, '<colorManagement ocioConfig="studio-config-latest" workingSpace="acescg" '
                         'display="sRGB - Display" view="Un-tone-mapped"/>')
    assert np.abs(_pixel(doc) - grey).max() <= 1


def test_ocio_pipeline_aces_view_and_look(tmp_path):
    doc = _doc(tmp_path, '<colorManagement ocioConfig="ocio://studio-config-latest" workingSpace="acescg" '
                         'display="sRGB - Display" view="ACES 2.0 - SDR 100 nits (Rec.709)"/>')
    v = _pixel(doc)
    lin = C.decode(np.array(0.4627), "srgb") * 1.0                       # #76 grey is ~0.18 linear
    expect = C.encode(C.aces2(np.full((1, 3), float(lin)))[0], "srgb")[0, 0] * 255
    assert abs(v[0] - expect) <= 2
    # an OCIO look (the ACES gamut compression) and a CDL look applied in ACEScct
    doc2 = _doc(tmp_path, '<colorManagement ocioConfig="studio-config-latest" workingSpace="acescg" display="sRGB - Display" '
                          'view="Un-tone-mapped"><look id="warm" slope="1.2,1,0.8"/></colorManagement>')
    w = _pixel(doc2)
    assert w[0] > w[1] > w[2]


def test_builtin_pipeline_aces2_and_agx(tmp_path):
    grey = _pixel(_doc(tmp_path, ""))
    a2 = _pixel(_doc(tmp_path, '<colorManagement toneMapping="aces2"/>'))
    agx = _pixel(_doc(tmp_path, '<colorManagement toneMapping="agx"/>'))
    lin = float(C.decode(np.array(grey[0] / 255), "srgb"))
    assert abs(agx[0] - C.encode(C.agx(np.full((1, 3), lin)), "srgb")[0, 0] * 255) <= 1
    assert abs(a2[0] - C.encode(C.aces2(np.full((1, 3), lin))[0], "srgb")[0, 0] * 255) <= 1


# ---------------------------------------------------------------- LUT formats through OCIO
def test_lut_formats(tmp_path):
    spi = tmp_path / "half.spi1d"
    vals = "\n".join(f"{0.5 * i / 15:.6f}" for i in range(16))
    spi.write_text(f"Version 1\nFrom 0.0 1.0\nLength 16\nComponents 1\n{{\n{vals}\n}}\n")
    clf = tmp_path / "swap.clf"
    clf.write_text(textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <ProcessList compCLFversion="3" id="swap">
          <Matrix inBitDepth="32f" outBitDepth="32f">
            <Array dim="3 3">0 1 0
        1 0 0
        0 0 1</Array>
          </Matrix>
        </ProcessList>"""))
    cube = tmp_path / "id.cube"
    n = 3
    rows = [f"{r / (n - 1)} {g / (n - 1)} {b / (n - 1)}" for b in range(n) for g in range(n) for r in range(n)]
    cube.write_text(f"LUT_3D_SIZE {n}\n" + "\n".join(rows) + "\n")
    x = np.array([[0.2, 0.6, 0.9]], np.float32)
    assert np.allclose(C.load_lut(str(spi))(x), x * 0.5, atol=1e-4)
    assert np.allclose(C.load_lut(str(clf))(x), [[0.6, 0.2, 0.9]], atol=1e-6)
    assert np.allclose(C.load_lut(str(cube))(x), x, atol=1e-5)


def test_look_with_spi1d_in_document(tmp_path):
    spi = tmp_path / "half.spi1d"
    vals = "\n".join(f"{0.5 * i / 15:.6f}" for i in range(16))
    spi.write_text(f"Version 1\nFrom 0.0 1.0\nLength 16\nComponents 1\n{{\n{vals}\n}}\n")
    base = _pixel(_doc(tmp_path, ""))
    v = _pixel(_doc(tmp_path, '<colorManagement><look id="lk" src="half.spi1d" space="srgb"/></colorManagement>'))
    assert abs(v[0] - base[0] * 0.5) <= 2


def test_output_transfers_every_enum_value(tmp_path):
    from scenerender.render import Renderer
    path = _doc(tmp_path, "")
    for tf in ("srgb", "linear", "bt1886", "gamma22", "gamma26", "pq", "hlg", "slog3", "logc3", "logc4", "vlog", "clog3",
               "redlog3g10", "flog2", "nlog", "acescc", "acescct"):
        r = Renderer.open(path)
        r.rc.cache["output_color"] = ("rec2020", tf)
        v = r.frame_rgb(0.0)[8, 16, 0] / 255
        lin = float(C.decode(np.array(0x76 / 255), "srgb"))
        expect = C.encode_output(np.full((1, 3), lin) @ C.matrix("srgb", "rec2020").T, tf, "rec2020", {}, 10000.0)[0, 0]
        assert v == pytest.approx(float(np.clip(expect, 0, 1)), abs=1.5 / 255), tf
    assert os.path.exists(path)
