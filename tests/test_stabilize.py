"""layer/@stabilize: whole-clip 2D stabilisation of video layers."""
from __future__ import annotations

import math
import subprocess

import numpy as np
import pytest

from scenerender import document
from scenerender.assets import generated as _gen  # noqa: F401
from scenerender.assets import video
from scenerender.compositor import RenderContext
from scenerender.evaluator import Evaluator
from scenerender.nodes import core as _core  # noqa: F401
from scenerender.raster import working_to_rgb8
from scenerender.registry import FEATURES, FULL

W, H, N, FPS = 160, 120, 40, 20


def _texture(seed=5, size=320):
    rng = np.random.default_rng(seed)
    k = np.fft.fftfreq(size)
    K = np.exp(-(k[:, None] ** 2 + k[None, :] ** 2) / (2 * 0.06 ** 2))
    t = np.fft.ifft2(np.fft.fft2(rng.random((size, size))) * K).real
    t = 0.6 * (t - t.min()) / (t.max() - t.min())
    return t


def _shaky_clip(path, seed=11):
    """A static scene viewed through a camera shaking by a seeded random walk (translation + roll)."""
    big = _texture()
    rng = np.random.default_rng(seed)
    txy = np.cumsum(rng.normal(0, 2.0, (N, 2)), 0)
    th = np.cumsum(rng.normal(0, 0.006, N))
    frames = []
    v, u = np.mgrid[0:H, 0:W].astype(np.float64)
    u -= (W - 1) / 2
    v -= (H - 1) / 2
    c = (big.shape[0] - 1) / 2
    for i in range(N):
        ct, st = math.cos(th[i]), math.sin(th[i])
        X = ct * u - st * v + txy[i, 0] + c
        Y = st * u + ct * v + txy[i, 1] + c
        g, _ = video._bilinear(big, X, Y)
        frames.append(np.repeat((g * 255 + 0.5).astype(np.uint8)[..., None], 3, 2))
    raw = np.stack(frames).tobytes()
    subprocess.run([video.ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "ffv1", "-pix_fmt", "bgr0", str(path)],
                   input=raw, check=True)


@pytest.fixture()
def clip(tmp_path, monkeypatch):
    monkeypatch.setenv("SCENERENDER_CACHE", str(tmp_path / "cache"))
    p = tmp_path / "shaky.mkv"
    _shaky_clip(p)
    return p


def make_rc(tmp_path, layer_attrs=""):
    src = f"""<?xml version="1.0"?>
<scene version="1.1">
  <project width="{W}" height="{H}" fps="{FPS}" duration="{N / FPS}" seed="7" background="#000000FF"/>
  <assets><video id="v" src="shaky.mkv" width="{W}" height="{H}" fps="{FPS}" duration="{N / FPS}"/></assets>
  <composition><layer id="L" asset="v" {layer_attrs}/></composition>
</scene>"""
    p = tmp_path / "doc.xml"
    p.write_text(src)
    doc = document.load(str(p))
    assert not doc.validation_errors, doc.validation_errors[:3]
    return RenderContext(doc, Evaluator(doc))


def _frames(rc, step=1):
    return [working_to_rgb8(rc.render_frame((i + 0.5) / FPS).px, rc.linear) for i in range(0, N - 1, step)]


def _motion(frames):
    """Mean frame-to-frame image motion (px) of the central crop, by FFT phase correlation."""
    g = [f[H // 5:-H // 5, W // 5:-W // 5, 1].astype(float) / 255 for f in frames]
    return float(np.mean([np.hypot(*video._phase_shift(a, b)) for a, b in zip(g, g[1:])]))


def test_feature_declared():
    assert FEATURES.level("layer:stabilize") == FULL


def test_estimator_recovers_similarity():
    big = _texture()
    v, u = np.mgrid[0:H, 0:W].astype(np.float64)
    u -= (W - 1) / 2
    v -= (H - 1) / 2

    def view(M):
        return video._bilinear(big, M[0, 0] * u + M[0, 1] * v + M[0, 2] + 159.5,
                               M[1, 0] * u + M[1, 1] * v + M[1, 2] + 159.5)[0]
    th, s = math.radians(1.5), 1.015
    Wt = np.array([[s * math.cos(th), -s * math.sin(th), 6.3], [s * math.sin(th), s * math.cos(th), -4.1], [0, 0, 1]])
    got = video.estimate_similarity(view(np.eye(3)), view(Wt))
    assert np.allclose(got, Wt, atol=2e-3)


def test_stabilize_reduces_motion(tmp_path, clip):
    raw = _motion(_frames(make_rc(tmp_path)))
    assert raw > 1.5
    rc = make_rc(tmp_path, 'stabilize="true" stabilizeSmoothness="0.8"')
    stab = _motion(_frames(rc))
    assert stab < 0.2 * raw, (stab, raw)


def test_stabilize_is_seek_independent_and_cached(tmp_path, clip):
    rc1 = make_rc(tmp_path, 'stabilize="true"')
    fwd = [working_to_rgb8(rc1.render_frame(i / FPS + 0.01).px, True) for i in (3, 17, 30)]
    rc2 = make_rc(tmp_path, 'stabilize="true"')
    bwd = [working_to_rgb8(rc2.render_frame(i / FPS + 0.01).px, True) for i in (30, 17, 3)][::-1]
    for a, b in zip(fwd, bwd):
        assert np.array_equal(a, b)
    assert list((tmp_path / "cache" / "stabilize").glob("*.npz"))
    # uncovered edges are transparent (no auto-crop): some border pixels show the black background
    assert any((f[0].max(1) < 5).any() or (f[:, 0].max(1) < 5).any() or (f[-1].max(1) < 5).any()
               or (f[:, -1].max(1) < 5).any() for f in fwd)


def test_smoothness_zero_leaves_frames_unchanged(tmp_path, clip):
    a = make_rc(tmp_path)
    b = make_rc(tmp_path, 'stabilize="true" stabilizeSmoothness="0"')
    for t in (0.01, 0.52, 1.33):
        assert np.array_equal(a.render_frame(t).px, b.render_frame(t).px)
    assert video.smooth_path(np.arange(12.0).reshape(3, 4), 20, 0).tolist() == np.arange(12.0).reshape(3, 4).tolist()
    p = np.random.default_rng(0).normal(size=(30, 4))
    assert np.allclose(video.smooth_path(p, 20, 1.0), p.mean(0))


def test_frame_mix_path_stabilises(tmp_path, clip):
    """Float path: each of the two mixed frames is warped by its own correction before the mix
    (frames 2i, 2i+1 per sample, so consecutive samples share no source frame)."""
    raw = _motion(_frames(make_rc(tmp_path, 'frameBlend="frame-mix"'), 2))
    stab = _motion(_frames(make_rc(tmp_path, 'frameBlend="frame-mix" stabilize="true" stabilizeSmoothness="0.8"'), 2))
    assert raw > 1.5
    assert stab < 0.2 * raw, (stab, raw)
