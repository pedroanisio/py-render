"""Colour management: matrices, transfer functions, tone mappers, CDL/LUT looks and the finishing hook."""
from __future__ import annotations

import os
import textwrap

import numpy as np
import pytest

from scenerender import color as C
from scenerender.raster import Buf


def test_primaries_matrices():
    # BT.2087 linear Rec.709 -> Rec.2020
    M = C.matrix("srgb", "rec2020")
    assert np.allclose(M, [[0.6274, 0.3293, 0.0433], [0.0691, 0.9195, 0.0114], [0.0164, 0.0880, 0.8956]], atol=2e-4)
    # Bradford-adapted linear sRGB -> ACEScg (as in OCIO / ACES configs)
    A = C.matrix("srgb", "ap1")
    assert np.allclose(A, [[0.6131, 0.3395, 0.0474], [0.0702, 0.9164, 0.0135], [0.0206, 0.1096, 0.8698]], atol=2e-3)
    assert np.allclose(C.matrix("srgb", "p3d65") @ C.matrix("p3d65", "srgb"), np.eye(3), atol=1e-9)
    # white stays white in every D65 space
    for p in ("p3d65", "rec2020"):
        assert np.allclose(C.matrix("srgb", p) @ np.ones(3), 1.0, atol=1e-6)


@pytest.mark.parametrize("tf", ["srgb", "bt1886", "gamma22", "gamma26", "acescct", "acescc", "slog3", "logc3", "vlog",
                                "logc4", "pq", "hlg"])
def test_transfer_roundtrip(tf):
    x = np.array([0.0, 0.001, 0.02, 0.18, 0.5, 0.9, 1.0])
    y = C.decode(C.encode(x, tf), tf)
    assert np.allclose(y, x, atol=2e-3 if tf in ("pq", "hlg", "slog3", "logc3", "vlog", "logc4") else 1e-6)


def test_known_codes():
    assert float(C.encode(np.array(0.18), "acescct")) == pytest.approx(0.4135, abs=1e-3)
    assert float(C.encode(np.array(1.0), "srgb")) == pytest.approx(1.0)
    assert float(C.encode(np.array(1.0), "pq")) == pytest.approx(0.5807, abs=2e-3)       # 203 nits
    assert float(C.encode(np.array(1.0), "hlg")) == pytest.approx(0.75, abs=5e-3)


@pytest.mark.parametrize("mode", ["reinhard", "filmic", "aces", "aces2", "agx"])
def test_tone_mappers(mode):
    x = np.linspace(0, 20, 400)[:, None].repeat(3, 1)
    y = C.tone_map(x, mode)
    assert y.min() >= 0 and y.max() <= 1
    assert np.all(np.diff(y[:, 1]) >= -1e-6)
    assert y[0, 1] <= 0.01
    if mode == "reinhard":
        assert C.tone_map(np.array([[1.0, 1, 1]]), mode)[0, 0] == pytest.approx(0.5)


def test_cdl():
    x = np.array([[0.5, 0.5, 0.5]])
    y = C.cdl(x, np.array([2.0, 1, 1]), np.array([0.0, 0.1, 0]), np.array([1.0, 1, 2]), 1.0)
    assert np.allclose(y, [[1.0, 0.6, 0.25]])
    g = C.cdl(np.array([[1.0, 0.0, 0.0]]), np.ones(3), np.zeros(3), np.ones(3), 0.0)
    assert np.allclose(g, 0.2126)


def test_cube_lut(tmp_path):
    n = 5
    lines = ["LUT_3D_SIZE 5"]
    for b in range(n):
        for g in range(n):
            for r in range(n):
                lines.append(f"{1 - r / 4} {1 - g / 4} {1 - b / 4}")     # invert
    p = tmp_path / "inv.cube"
    p.write_text("\n".join(lines))
    data = C.read_cube(str(p))
    x = np.array([[0.1, 0.5, 0.8], [0.33, 0.66, 0.99]], np.float32)
    assert np.allclose(C.apply_cube(x, data), 1 - x, atol=1e-5)


SCENE = """\
<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="8" height="4" fps="1" duration="1" background="#000000FF"/>
  {cm}
  <composition>
    <shape id="a" shape="rect" width="4" height="4" fill="#FF0000FF"/>
    <shape id="b" shape="rect" x="4" width="4" height="4" fill="#808080FF"/>
  </composition>
</scene>
"""


def _render(tmp_path, cm="", out_color=None):
    from scenerender.render import Renderer
    p = tmp_path / "c.xml"
    p.write_text(SCENE.replace("{cm}", cm))
    r = Renderer.open(str(p))
    if out_color:
        r.rc.cache["output_color"] = out_color
    return r, r.frame_rgb(0.0).astype(int)


def test_hook_noop_without_color_management(tmp_path):
    r, img = _render(tmp_path)
    buf = Buf(np.random.default_rng(0).random((4, 8, 4)).astype(np.float32))
    assert C.finish(r.rc, buf, None) is buf
    assert tuple(img[0, 0]) == (255, 0, 0) and tuple(img[0, 6]) == (128, 128, 128)


def test_exposure_and_looks(tmp_path):
    _, base = _render(tmp_path)
    _, img = _render(tmp_path, '<colorManagement exposure="1"/>')
    # +1 stop doubles linear light: sRGB 128 (0.216 linear) -> 0.432 linear -> 175
    assert abs(img[0, 6, 0] - 175) <= 1
    _, img = _render(tmp_path, '<colorManagement looks="lk"><look id="lk" space="linear-srgb" saturation="0"/></colorManagement>')
    assert img[0, 0, 0] == img[0, 0, 1] == img[0, 0, 2]              # desaturated red
    _, img = _render(tmp_path, '<colorManagement><look id="lk" slope="1,1,1" mix="0"/></colorManagement>')
    assert np.abs(img - base).max() <= 1                               # mix 0 = unchanged
    _, img = _render(tmp_path, '<colorManagement toneMapping="reinhard"/>')
    assert img[0, 6, 0] < base[0, 6, 0]


def test_output_colour_conversion(tmp_path):
    _, base = _render(tmp_path)
    _, rec2020 = _render(tmp_path, out_color=("rec2020", None))
    # pure sRGB red is a less saturated colour inside the wider Rec.2020 gamut
    assert rec2020[0, 0, 1] > base[0, 0, 1]
    _, lin = _render(tmp_path, out_color=("linear-srgb", None))
    assert abs(lin[0, 6, 0] - round(0.2158 * 255)) <= 1


def test_background_is_graded(tmp_path):
    from scenerender.output import _graded_background
    r, _ = _render(tmp_path, '<colorManagement exposure="2"/>')
    r.doc.project.set("background", "#404040FF")
    bg = _graded_background(r)
    assert bg[0] == pytest.approx(4 * float(C.decode(np.array(64 / 255), "srgb")), rel=1e-3)
    assert os.path.exists(tmp_path / "c.xml")
    assert textwrap is not None
