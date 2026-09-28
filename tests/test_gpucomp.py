"""gpucomp: frames composited on the GPU match the CPU compositor; the GPU output conversions match
their definitions; tiles lent from a cache are never written through."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from scenerender import compositor, gl, gpucomp
from scenerender.registry import load_plugins
from scenerender.render import Renderer

load_plugins()
pytestmark = pytest.mark.skipif(not gl.available(), reason="no OpenGL context")

SCENE = '''<scene version="1.1">
  <project width="192" height="108" fps="24" duration="2" background="#101820FF" motionBlur="true"
           motionBlurSamples="3" shutterAngle="180"/>
  <assets><image id="img" src="pic.png" width="64" height="48"/></assets>
  <composition>
    <layer id="bg" asset="img" x="0" y="0" boxWidth="192" boxHeight="108">
      <animate property="scale"><key time="0" value="1"/><key time="2" value="1.3"/></animate></layer>
    <group id="labels">
      <shape id="a" shape="ellipse" x="20" y="10" width="40" height="30" fill="#D0A040FF"/>
      <shape id="b" shape="rect" x="120" y="60" width="50" height="30" fill="#4080E0C0"/>
    </group>
    <group id="fade" isolate="true">
      <animate property="opacity"><key time="0" value="0.2"/><key time="2" value="1"/></animate>
      <shape id="c" shape="star" x="70" y="30" width="50" height="50" fill="#E04060FF"/>
      <shape id="d" shape="rect" x="90" y="50" width="40" height="20" fill="#40E080A0"/>
    </group>
    <shape id="mover" shape="ellipse" y="70" width="24" height="24" fill="#FFFFFFD0" strokeWidth="2" stroke="#000000FF">
      <animate property="x"><key time="0" value="0"/><key time="2" value="170"/></animate></shape>
    <shape id="under" shape="rect" x="0" y="0" width="192" height="12" fill="#FF00FFFF" blend="behind"/>
  </composition></scene>'''


@pytest.fixture
def doc(tmp_path):
    y, x = np.mgrid[0:48, 0:64] / 9.0
    a = np.zeros((48, 64, 4), np.uint8)
    for c, (fx, fy) in enumerate(((0.7, 1.1), (1.3, 0.4), (0.5, 0.9))):
        a[..., c] = (127.5 + 127 * np.sin(fx * x + fy * y + c)).astype(np.uint8)
    a[..., 3] = 255
    Image.fromarray(a).save(tmp_path / "pic.png")
    p = tmp_path / "s.xml"
    p.write_text(SCENE)
    return str(p)


def _frames(path, gpu: bool, monkeypatch, times):
    monkeypatch.setattr(gpucomp, "_OK", gpu)
    r = Renderer.open(path)
    return [r.frame_rgb(t) for t in times]


@pytest.mark.parametrize("cache", [False, True])
def test_frames_match_cpu_compositing(doc, monkeypatch, cache):
    monkeypatch.setattr(compositor, "RASTER_CACHE", cache)
    times = [0.0, 0.3, 0.71, 1.2, 1.9]
    got = _frames(doc, True, monkeypatch, times)
    ref = _frames(doc, False, monkeypatch, times)
    for g, r in zip(got, ref):
        assert g.shape == r.shape
        assert int(np.abs(g.astype(int) - r.astype(int)).max()) <= 1


def test_yuv420p_follows_bt709(doc, monkeypatch):
    monkeypatch.setattr(gpucomp, "_OK", True)
    r = Renderer.open(doc)
    buf = r.frame_buf(0.7)
    bg = (0.02, 0.03, 0.05)
    rgb = gpucomp.to_rgb8(buf, r.rc.linear, bg).astype(np.float64)
    h, w = rgb.shape[:2]
    for full in (False, True):
        yuv = gpucomp.to_yuv420p_async(buf, r.rc.linear, bg, full=full)().astype(np.float64)
        luma = rgb @ [0.2126, 0.7152, 0.0722]
        cb, cr = (rgb[..., 2] - luma) / 1.8556, (rgb[..., 0] - luma) / 1.5748
        ky, kc = (1.0, 1.0) if full else (219 / 255, 224 / 255)
        want_y = np.clip(np.floor((0 if full else 16) + luma * ky + 0.5), 0, 255)
        box = lambda c: c.reshape(h // 2, 2, w // 2, 2).mean((1, 3))  # noqa: E731
        want_u = np.clip(np.floor(128 + box(cb) * kc + 0.5), 0, 255)
        want_v = np.clip(np.floor(128 + box(cr) * kc + 0.5), 0, 255)
        n = w * h
        assert np.abs(yuv[:n].reshape(h, w) - want_y).max() <= 1
        assert np.abs(yuv[n:n + n // 4].reshape(h // 2, w // 2) - want_u).max() <= 1
        assert np.abs(yuv[n + n // 4:].reshape(h // 2, w // 2) - want_v).max() <= 1


def test_lent_tiles_are_never_written_through(monkeypatch):
    monkeypatch.setattr(gpucomp, "_OK", True)
    from scenerender.blend import composite
    from scenerender.raster import Buf
    px = np.zeros((8, 8, 4), np.float32)
    px[2:6, 2:6] = (0.5, 0.25, 0.125, 0.5)
    tile = gpucomp.shared_upload(px)
    lent = gpucomp.lend(tile, 0, 0)
    lent.px[:] = 0                       # a writer gets a private copy
    np.testing.assert_array_equal(gpucomp.lend(tile, 0, 0).px, px)
    grown = composite(gpucomp.lend(tile, 0, 0), Buf(np.ones((4, 4, 4), np.float32), 6, 6))
    assert grown.rect == (0, 0, 10, 10)  # drawn into a copy, grown like a CPU buffer
    np.testing.assert_array_equal(gpucomp.lend(tile, 0, 0).px, px)
