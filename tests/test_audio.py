"""Audio mix: BS.1770 loudness, limiter, pan law, fades, ducking, routing, envelopes, determinism."""
from __future__ import annotations

import math
import os
import wave

import numpy as np
import pytest

from scenerender import document
from scenerender.audio import dsp
from scenerender.audio.effects import FxContext
from scenerender.audio.io import decode, write_wav
from scenerender.audio.mix import Mixer
from scenerender.evaluator import Evaluator
from scenerender.registry import AUDIO_EFFECTS

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures")
MEDIA = os.path.join(FIX, "media")
DEMO = os.path.join(FIX, "audio_demo.xml")
SR = 48000


def _write(path, x, sr):
    q = np.clip(np.round(np.asarray(x) * 32767), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(q.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(q.tobytes())


def make_audio_media() -> None:
    """Deterministic tone WAVs used by audio_demo.xml."""
    os.makedirs(MEDIA, exist_ok=True)
    t = np.arange(4 * SR) / SR
    amp = 10 ** (-12 / 20)
    music = amp * 0.5 * (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 330 * t))
    _write(os.path.join(MEDIA, "audio_music.wav"), np.stack([music, np.roll(music, 37)], 1), SR)
    gate = ((t >= 1.0) & (t < 2.0)) | ((t >= 2.5) & (t < 3.0))
    env = np.clip(np.minimum((t % 0.5) / 0.01, 1), 0, 1)
    voice = 10 ** (-6 / 20) * np.sin(2 * np.pi * 1000 * t) * gate * env
    _write(os.path.join(MEDIA, "audio_voice.wav"), voice[:, None], SR)
    sr2 = 44100
    tc = np.arange(int(0.1 * sr2)) / sr2
    rng = np.random.default_rng(3)
    click = 0.5 * rng.standard_normal(len(tc)) * np.exp(-tc / 0.015)
    _write(os.path.join(MEDIA, "audio_click.wav"), click[:, None], sr2)


@pytest.fixture(scope="module", autouse=True)
def media():
    make_audio_media()


def sine(freq, db, secs=5.0, sr=SR, ch=1):
    t = np.arange(int(secs * sr)) / sr
    x = 10 ** (db / 20) * np.sin(2 * np.pi * freq * t)
    return np.repeat(x[:, None], ch, 1).astype(np.float32)


def demo_mixer(**kw):
    doc = document.load(DEMO, **kw)
    return Mixer(doc, Evaluator(doc))


# ---------------------------------------------------------------- loudness
def test_bs1770_reference_sine():
    # A 1 kHz sine in one channel at -20 dBFS reads -23.01 LUFS (0 dBFS in one channel = -3.01).
    assert dsp.integrated_loudness(sine(1000, -20), SR) == pytest.approx(-23.01, abs=0.2)
    assert dsp.integrated_loudness(sine(1000, 0), SR) == pytest.approx(-3.01, abs=0.1)
    # the same tone in both stereo channels is 3 dB louder
    assert dsp.integrated_loudness(sine(1000, -20, ch=2), SR) == pytest.approx(-20.0, abs=0.2)
    # coefficients are re-derived for other rates
    assert dsp.integrated_loudness(sine(1000, -20, sr=44100), 44100) == pytest.approx(-23.01, abs=0.2)


def test_bs1770_gating():
    x = np.concatenate([sine(1000, -20, 4), np.zeros((4 * SR, 1), np.float32)])
    # silence is gated out (absolute gate), so loudness equals the tone alone
    assert dsp.integrated_loudness(x, SR) == pytest.approx(-23.01, abs=0.2)
    assert dsp.integrated_loudness(np.zeros((SR, 1)), SR) == -math.inf


def test_kweighting_48k_matches_standard():
    (b1, a1), (b2, a2) = dsp.k_weighting_sections(48000)
    assert np.allclose(b1, [1.53512485958697, -2.69169618940638, 1.19839281085285], atol=1e-9)
    assert np.allclose(a1, [1.0, -1.69065929318241, 0.73248077421585], atol=1e-9)
    assert np.allclose(a2, [1.0, -1.99004745483398, 0.99007225036621], atol=1e-9)


# ---------------------------------------------------------------- limiter / true peak
def test_true_peak_detects_intersample_overs():
    n = np.arange(4800)
    x = np.sin(2 * np.pi * n / 4 + np.pi / 4)[:, None]      # samples at +-0.707, true peak 1.0
    assert np.abs(x).max() == pytest.approx(0.7071, abs=1e-3)
    assert dsp.true_peak_db(x) == pytest.approx(0.0, abs=0.15)


def test_limiter_ceiling():
    x = sine(997, 6.0, 2, ch=2)
    rng = np.random.default_rng(0)
    x = x + 0.3 * rng.standard_normal(x.shape).astype(np.float32)
    y = dsp.limit(x, SR, -1.0)
    assert dsp.true_peak_db(y) <= -1.0 + 0.1
    # quiet material passes untouched
    q = sine(440, -20, 1)
    assert np.array_equal(dsp.limit(q, SR, -1.0), q)


# ---------------------------------------------------------------- pan / fades / filters
def test_pan_law_constant_power():
    for p in np.linspace(-1, 1, 21):
        gl, gr = dsp.pan_gains(p)
        assert gl ** 2 + gr ** 2 == pytest.approx(2.0)
    assert dsp.pan_gains(0.0) == pytest.approx((1.0, 1.0))
    gl, gr = dsp.pan_gains(-1.0)
    assert gl == pytest.approx(math.sqrt(2)) and gr == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("curve", ["linear", "equal-power", "logarithmic", "exponential", "s-curve"])
def test_fade_curves(curve):
    p = np.linspace(0, 1, 101)
    g = dsp.fade_curve(p, curve)
    assert g[0] == pytest.approx(0.0, abs=1e-9) and g[-1] == pytest.approx(1.0)
    assert np.all(np.diff(g) >= -1e-12)
    mid = float(dsp.fade_curve(0.5, curve))
    expect = {"linear": 0.5, "equal-power": math.sqrt(0.5), "s-curve": 0.5}
    if curve in expect:
        assert mid == pytest.approx(expect[curve])
    if curve == "logarithmic":
        assert mid > 0.5
    if curve == "exponential":
        assert mid < 0.5


def test_biquad_response():
    lo = dsp.filter_sections(sine(100, -6, 1), dsp.butter_sections("highpass", 1000, SR, 2))
    assert np.sqrt(np.mean(lo[SR // 2:] ** 2)) < 0.01 * 0.5          # -40 dB two decades down
    pk = dsp.filter_sections(sine(1000, -20, 1), [dsp.biquad("peak", 1000, SR, 1.0, 6.0)])
    gain = 20 * np.log10(np.sqrt(np.mean(pk[SR // 2:] ** 2)) / np.sqrt(np.mean(sine(1000, -20, 1)[SR // 2:] ** 2)))
    assert gain == pytest.approx(6.0, abs=0.05)


def test_resample_keeps_tone():
    x = sine(1000, -6, 1)
    y = dsp.resample(x, SR, 44100)
    assert y.shape[0] == 44100
    assert dsp.integrated_loudness(y, 44100) == pytest.approx(dsp.integrated_loudness(x, SR), abs=0.1)


def test_every_schema_effect_is_registered():
    names = ["eq", "highpass", "lowpass", "compressor", "limiter", "gate", "de-esser", "reverb", "delay", "chorus",
             "pitch-shift", "noise-reduction", "stereo-width", "gain", "distortion", "telephone"]
    for n in names:
        assert n in AUDIO_EFFECTS.entries, n


@pytest.mark.parametrize("name", ["eq", "highpass", "lowpass", "compressor", "limiter", "gate", "de-esser", "reverb",
                                  "delay", "chorus", "pitch-shift", "stereo-width", "gain", "distortion", "telephone"])
def test_effects_run(name):
    from lxml import etree
    el = etree.Element("audioEffect", type=name)
    x = sine(440, -12, 0.5, ch=2)
    fx = FxContext(el, SR, 0.0, lambda k, d: el.get(k, d), lambda k, d, n: np.full(n, float(el.get(k, d))),
                   lambda i, n: None)
    y = AUDIO_EFFECTS.get(name)(fx, x)
    assert y.shape == x.shape and np.all(np.isfinite(y))


def test_compressor_reduces_loud_signal():
    from lxml import etree
    el = etree.Element("audioEffect", type="compressor", threshold="-30", ratio="4")
    get = lambda k, d: float(el.get(k)) if el.get(k) else d  # noqa: E731
    fx = FxContext(el, SR, 0.0, get, lambda k, d, n: np.full(n, d), lambda i, n: None)
    x = sine(440, -6, 1)
    y = AUDIO_EFFECTS.get("compressor")(fx, x)
    # -9 dBFS RMS is 21 dB over the threshold -> ~15.75 dB of gain reduction
    red = 20 * np.log10(np.sqrt(np.mean(y[SR // 2:] ** 2)) / np.sqrt(np.mean(x[SR // 2:] ** 2)))
    assert red == pytest.approx(-15.75, abs=1.0)


# ---------------------------------------------------------------- the demo mix
def test_demo_mix_routing_ducking_normalize():
    m = demo_mixer()
    x = m.render()
    assert x.shape == (4 * SR, 2)
    # normalised to -16 LUFS integrated, true peak under -1 dBTP
    assert dsp.integrated_loudness(x, SR) == pytest.approx(-16.0, abs=0.3)
    assert dsp.true_peak_db(x) <= -0.9
    # ducking: the music bed is reduced while the voice plays
    music = m.track("trk-music")
    assert m.stats["duck"]["trk-music"] == pytest.approx(-10.0, abs=0.01)
    seg = music.data
    rms = lambda a, b: float(np.sqrt(np.mean(seg[int(a * SR):int(b * SR)] ** 2)))  # noqa: E731
    assert 20 * np.log10(rms(1.5, 1.9) / rms(0.6, 0.9)) == pytest.approx(-10.0, abs=1.0)
    # fades: silent at the very start and the very end of the music track
    assert np.abs(seg[:10]).max() < 1e-3
    assert np.abs(seg[-10:]).max() < 1e-3
    # pan automation: the voice starts left of centre and ends right of it
    v = m.track("trk-voice").data
    l1, r1 = np.abs(v[int(1.1 * SR):int(1.9 * SR)]).mean(0)
    l2, r2 = np.abs(v[int(2.6 * SR):int(2.9 * SR)]).mean(0)
    assert l1 > r1 and (l2 / r2) < (l1 / r1)
    # startMarker + loop: three clicks from 0.5 s, 0.1 s apart
    c = m.track("trk-click")
    assert c.i0 == int(0.5 * SR)


def test_amplitude_envelopes():
    m = demo_mixer()
    assert m.amplitude("trk-voice", None, 0.5) < 1e-4
    assert m.amplitude("trk-voice", None, 1.5) > 0.1
    assert m.amplitude("aud-voice", None, 1.5) > 0.1          # by asset id
    assert m.amplitude("bus-music", "low", 0.8) > m.amplitude("bus-music", "high", 0.8)
    assert m.amplitude("nope", None, 1.0) == 0.0


def test_link_reads_audio(tmp_path):
    from scenerender.render import Renderer
    r = Renderer.open(DEMO, scale=0.25)
    meter = r.doc.ids["meter"]
    from scenerender.evaluator import Ctx
    assert r.rc.ev.num(meter, "scaleX", Ctx(t=0.3, comp_t=0.3), 0) == pytest.approx(0.01, abs=1e-3)
    assert r.rc.ev.num(meter, "scaleX", Ctx(t=1.5, comp_t=1.5), 0) > 0.3


def test_mix_is_deterministic(tmp_path):
    a = demo_mixer().render()
    b = demo_mixer().render()
    assert np.array_equal(a, b)
    pa, pb = tmp_path / "a.wav", tmp_path / "b.wav"
    write_wav(str(pa), a, SR, 24, True, seed=11)
    write_wav(str(pb), b, SR, 24, True, seed=11)
    assert pa.read_bytes() == pb.read_bytes()
    back = decode(str(pa), SR, 2)
    assert np.max(np.abs(back - a)) < 2e-6 * 8


def test_mute_and_speed(tmp_path):
    src = open(DEMO, encoding="utf-8").read()
    src = src.replace('id="trk-voice" asset="aud-voice"', 'id="trk-voice" asset="aud-voice" mute="true"')
    src = src.replace('id="trk-music" asset="aud-music"', 'id="trk-music" asset="aud-music" speed="2" clipOut="2"')
    p = tmp_path / "d.xml"
    p.write_text(src.replace('src="media/', f'src="{MEDIA}/'))
    m = Mixer(*(lambda d: (d, Evaluator(d)))(document.load(str(p))))
    assert m.track("trk-voice") is None
    music = m.track("trk-music")
    assert music.data.shape[0] == pytest.approx(1 * SR, abs=2)     # 2 s of source at 2x speed
    assert "duck" not in m.stats or m.stats["duck"].get("trk-music", 0) > -0.5


LAYER_SCENE = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="64" height="36" fps="10" duration="3"/>
  <assets>
    <video id="v" src="{media}/testsrc.mp4" width="64" height="36" fps="10" duration="1" hasAudio="true"/>
  </assets>
  <composition>
    <layer id="a" asset="v" start="0" end="1"/>
    <layer id="b" asset="v" start="1" end="2" volume="0.5"/>
    <layer id="c" asset="v" start="2" end="3" mute="true"/>
    <transition type="crossfade" from="a" to="b" duration="0.4" audio="equal-power"/>
  </composition>
</scene>
"""


def test_video_layer_audio(tmp_path):
    from scenerender.audio import mixer_for
    from scenerender.render import Renderer
    p = tmp_path / "v.xml"
    p.write_text(LAYER_SCENE.format(media=MEDIA))
    r = Renderer.open(str(p))
    m = mixer_for(r)
    assert m.has_audio()
    x = m.render()
    rms = lambda a, b: float(np.sqrt(np.mean(x[int(a * SR):int(b * SR)] ** 2)))  # noqa: E731
    assert rms(0.1, 0.7) > 0.01
    assert rms(1.3, 1.9) > 0.01
    assert rms(1.3, 1.9) == pytest.approx(0.5 * rms(0.3, 0.7), rel=0.35)   # volume 0.5 (same source span shifted)
    assert rms(2.1, 2.9) < 1e-4                                             # muted layer
