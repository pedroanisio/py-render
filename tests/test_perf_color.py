"""Colour-management finish through the baked 3D LUT (scenerender/color_lut.py) against the exact
per-pixel path: every tone mapper, looks, OCIO, HDR outputs, non-linear projects, premultiplied
alpha, out-of-range values, GPU vs CPU executors, bake-once and the disk cache.

Gate (8-bit sRGB output code values): max difference <= 2, 99.9th percentile <= 1."""
from __future__ import annotations

import os
import textwrap

import numpy as np
import pytest

from scenerender import color as C
from scenerender import color_lut as L
from scenerender.raster import Buf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DF_ASSETS = "/home/admin/codebases/py-render/digital-front"


@pytest.fixture(autouse=True)
def _lut_cache(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("SCENERENDER_CACHE", str(tmp_path_factory.getbasetemp() / "cache"))
    monkeypatch.delenv("SCENERENDER_EXACT_COLOR", raising=False)


def _doc(tmp_path, cm: str, project: str = "") -> str:
    p = tmp_path / "cm.xml"
    p.write_text(textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <scene version="1.1">
          <project width="32" height="16" fps="10" duration="1" background="#000000FF" {project}/>
          {cm}
          <composition>
            <shape id="grey" shape="rect" x="0" y="0" width="32" height="16" fill="#767676FF"/>
          </composition>
        </scene>"""))
    return str(p)


def _rc(path, output=None, hdr=None, **kw):
    from scenerender.render import Renderer
    rc = Renderer.open(path, **kw).rc
    if output:
        rc.cache["output_color"] = output
    if hdr:
        rc.cache["output_hdr"] = hdr
    return rc


def _random_frame(wp: str, h: int = 384, w: int = 512, seed: int = 1) -> np.ndarray:
    """Straight working-space content: sRGB-uniform colours (3 % of channels 0, 3 % 1), a fifth
    of the pixels pushed to -3..+5 stops (HDR highlights), then premultiplied by alpha 1."""
    rng = np.random.default_rng(seed)
    v = rng.random((h * w, 3))
    v[rng.random((h * w, 3)) < 0.03] = 0
    v[rng.random((h * w, 3)) < 0.03] = 1
    x = C.decode(v, "srgb")
    k = rng.random(h * w) < 0.2
    x[k] *= 2.0 ** rng.uniform(-3, 5, (k.sum(), 1))
    x = x @ C.matrix("srgb", wp).T
    px = np.ones((h, w, 4), np.float32)
    px[..., :3] = x.reshape(h, w, 3)
    return px


def _q8(rc, rgb: np.ndarray) -> np.ndarray:
    v = C.encode(np.clip(rgb, 0, None), "srgb") if rc.linear else rgb
    return np.round(np.clip(v, 0, 1) * 255).astype(np.int16)


def _exact(rc, px: np.ndarray, monkeypatch) -> np.ndarray:
    monkeypatch.setenv("SCENERENDER_EXACT_COLOR", "1")
    out = C.finish(rc, Buf(px, 0, 0), None).px
    monkeypatch.delenv("SCENERENDER_EXACT_COLOR")
    return out


def _gate(rc, exact: np.ndarray, fast: np.ndarray) -> None:
    d = np.abs(_q8(rc, exact[..., :3]) - _q8(rc, fast[..., :3]))
    assert d.max() <= 2, f"max {d.max()}"
    assert np.percentile(d, 99.9) <= 1
    assert np.array_equal(exact[..., 3], fast[..., 3])


def _both_executors(rc, px, monkeypatch):
    """Finish through the LUT on the GPU (when available) and on the CPU, both checked."""
    exact = _exact(rc, px, monkeypatch)
    outs = []
    if L.gpu_enabled():
        outs.append(C.finish(rc, Buf(px, 0, 0), None).px)
    monkeypatch.setenv("SCENERENDER_NO_GPU", "1")
    outs.append(C.finish(rc, Buf(px, 0, 0), None).px)
    monkeypatch.delenv("SCENERENDER_NO_GPU")
    for out in outs:
        _gate(rc, exact, out)
    return exact, outs


# ---------------------------------------------------------------- plans
@pytest.mark.parametrize("tone", ["none", "reinhard", "filmic", "aces", "aces2", "agx"])
@pytest.mark.parametrize("ws", ["linear-srgb", "acescg"])
def test_lut_matches_exact_every_tone_mapper(tmp_path, monkeypatch, tone, ws):
    rc = _rc(_doc(tmp_path, f'<colorManagement workingSpace="{ws}" toneMapping="{tone}" exposure="0.5"/>'))
    _both_executors(rc, _random_frame(C.working_primaries(rc)), monkeypatch)


def test_explainer_plan_aces2_with_acescct_look(tmp_path, monkeypatch):
    cm = ('<colorManagement workingSpace="acescg" looks="lk-warm" display="srgb" view="standard" toneMapping="aces2">'
          '<look id="lk-warm" space="acescct" slope="1.02,1.0,0.97" offset="0.005,0,-0.004" power="1,1,1.02" '
          'saturation="1.05" mix="0.8"/></colorManagement>')
    rc = _rc(_doc(tmp_path, cm))
    assert C._plan(rc)["looks"] and C._plan(rc)["wp"] == "ap1"
    _both_executors(rc, _random_frame("ap1"), monkeypatch)


@pytest.mark.parametrize("output,hdr,tone", [(("rec709", "bt1886"), None, "agx"),
                                             (("rec2020", "pq"), {"peak": 1000.0, "black": 0.005}, "aces2"),
                                             (("rec2020", "hlg"), {"peak": 1000.0}, "none"),
                                             (("display-p3", "srgb"), None, "agx"),
                                             (("acescct", "auto"), None, "none")])
def test_lut_output_encodings(tmp_path, monkeypatch, output, hdr, tone):
    rc = _rc(_doc(tmp_path, f'<colorManagement workingSpace="acescg" toneMapping="{tone}"/>'), output, hdr)
    _both_executors(rc, _random_frame(C.working_primaries(rc)), monkeypatch)


def test_lut_ocio_pipeline(tmp_path, monkeypatch):
    pytest.importorskip("PyOpenColorIO")
    rc = _rc(_doc(tmp_path, '<colorManagement ocioConfig="studio-config-latest" workingSpace="acescg" '
                            'display="sRGB - Display" view="ACES 2.0 - SDR 100 nits (Rec.709)">'
                            '<look id="warm" slope="1.1,1,0.9"/></colorManagement>'))
    assert C._plan(rc)["ocio"] is not None
    _both_executors(rc, _random_frame(C.working_primaries(rc)), monkeypatch)


def test_lut_non_linear_project(tmp_path, monkeypatch):
    rc = _rc(_doc(tmp_path, '<colorManagement toneMapping="agx" exposure="1"/>', 'linearLight="false"'))
    assert not rc.linear
    px = _random_frame("srgb")
    px[..., :3] = np.clip(C.encode(np.clip(px[..., :3], 0, None), "srgb"), 0, 1)   # sRGB-encoded frame
    _both_executors(rc, px, monkeypatch)


# ---------------------------------------------------------------- pixels
def test_premultiplied_alpha_and_out_of_range(tmp_path, monkeypatch):
    rc = _rc(_doc(tmp_path, '<colorManagement workingSpace="acescg" toneMapping="agx"/>'))
    px = _random_frame("ap1", 128, 256)
    rng = np.random.default_rng(3)
    a = rng.choice([0.0, 1e-7, 0.01, 0.5, 1.0], (128, 256, 1)).astype(np.float32)
    px[..., :3] *= a
    px[..., 3:] = a
    px[0, :8, :3] = [[1000, 1, 1], [-0.5, 0.2, 0.2], [300, 400, 500], [0.2, -0.01, 0.2],
                     [np.inf, 0, 0], [1, 1, 1], [0, 0, 0], [1e6, 1e6, 1e6]]
    px[0, :8, 3] = 1
    exact, outs = _both_executors(rc, px, monkeypatch)
    far = [0, 1, 2, 3, 4, 7]
    for out in outs:        # pixels outside the lattice go through the exact path
        np.testing.assert_allclose(out[0, far], exact[0, far], rtol=1e-5, atol=1e-6, equal_nan=True)


def test_gpu_and_cpu_executors_agree(tmp_path):
    if not L.gpu_enabled():
        pytest.skip("no GL")
    rc = _rc(_doc(tmp_path, '<colorManagement workingSpace="acescg" toneMapping="aces2"/>'))
    lut = C._lut(rc, C._plan(rc))
    px = _random_frame("ap1")
    g, c = lut.apply_gpu(px, True), lut.apply_cpu(px, True)
    # same lattice and simplexes; only float rounding differs (and, on a cell boundary, which
    # cell a pixel lands in: a rough one is exact, a smooth one interpolated)
    assert np.percentile(np.abs(g - c), 99.9) < 1e-5
    assert np.abs(_q8(rc, g[..., :3]) - _q8(rc, c[..., :3])).max() <= 1


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "static/fixtures/bench-explainer.xml")), reason="fixture")
def test_real_frames_explainer(monkeypatch):
    from scenerender.render import Renderer
    r = Renderer.open(os.path.join(ROOT, "static/fixtures/bench-explainer.xml"), scale=0.25, strict=False)
    frames = []
    prev = r.rc.hooks["finish"]
    r.rc.hooks["finish"] = lambda rc, buf, ctx: (frames.append(buf.px.copy()), prev(rc, buf, ctx))[1]
    for t in (20.0, 125.0):
        r.frame_rgb(t)
    assert frames and C._plan(r.rc)["tone"] == "aces2"
    for px in frames:
        _both_executors(r.rc, px, monkeypatch)


@pytest.mark.skipif(not os.path.isdir(DF_ASSETS), reason="digital-front assets")
def test_real_frames_digital_front(monkeypatch):
    from scenerender.render import Renderer
    r = Renderer.open(os.path.join(ROOT, "static/fixtures/scene.xml"), scale=0.25, assets_dir=DF_ASSETS, strict=False)
    frames = []
    prev = r.rc.hooks["finish"]
    r.rc.hooks["finish"] = lambda rc, buf, ctx: (frames.append(buf.px.copy()), prev(rc, buf, ctx))[1]
    r.frame_rgb(3.0)
    assert frames and C._plan(r.rc)["tone"] == "agx"
    for px in frames:
        _both_executors(r.rc, px, monkeypatch)


# ---------------------------------------------------------------- plumbing
def test_identity_plan_short_circuits(tmp_path):
    rc = _rc(_doc(tmp_path, ""))
    buf = Buf(_random_frame("srgb", 128, 128), 0, 0)
    assert C._plan(rc) is None
    assert C.finish(rc, buf, None) is buf
    assert not any(isinstance(k, tuple) and k and k[0] == "color_lut" for k in rc.cache)


def test_exact_env_bypasses_the_lut(tmp_path, monkeypatch):
    rc = _rc(_doc(tmp_path, '<colorManagement toneMapping="filmic"/>'))
    px = _random_frame("srgb", 128, 128)
    monkeypatch.setenv("SCENERENDER_EXACT_COLOR", "1")
    out = C.finish(rc, Buf(px, 0, 0), None).px
    a = px[..., 3:4]
    ref = C.process_rgb(rc, px[..., :3] / a, C._plan(rc)) * a
    np.testing.assert_array_equal(out[..., :3], ref)
    assert not any(isinstance(k, tuple) and k and k[0] == "color_lut" for k in rc.cache)


def test_baked_once_per_document_and_cached_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("SCENERENDER_CACHE", str(tmp_path / "cache"))
    path = _doc(tmp_path, '<colorManagement workingSpace="acescg" toneMapping="agx"/>')
    rc = _rc(path)
    px = _random_frame("ap1", 128, 128)
    C.finish(rc, Buf(px, 0, 0), None)
    lut = C._lut(rc, C._plan(rc))
    C.finish(rc, Buf(px, 0, 0), None)
    assert C._lut(rc, C._plan(rc)) is lut and not lut.cached
    assert sum(1 for k in rc.cache if isinstance(k, tuple) and k and k[0] == "color_lut") == 1
    assert os.listdir(tmp_path / "cache" / "colorlut")
    # a new process (here: a new renderer) loads the table instead of baking it
    rc2 = _rc(path)
    lut2 = C._lut(rc2, C._plan(rc2))
    assert lut2.cached
    assert np.array_equal(lut2.table, lut.table) and np.array_equal(lut2.rough, lut.rough)
    # a different plan does not hit that entry
    (tmp_path / "other").mkdir()
    rc3 = _rc(_doc(tmp_path / "other", '<colorManagement workingSpace="acescg" toneMapping="agx" exposure="1"/>'))
    assert not C._lut(rc3, C._plan(rc3)).cached
