"""Audio to FULL: time-stretch, pitch-shift, noise reduction, dynamic loudness, fitToDuration,
surround/ambisonics, instance layer audio, transition audio on tracks, effect attributes."""
from __future__ import annotations

import os
import subprocess
import wave

import numpy as np
import pytest
from lxml import etree

from scenerender import document
from scenerender.audio import dsp, spectral
from scenerender.audio.effects import FxContext, note_seconds
from scenerender.audio.io import decode, write_wav
from scenerender.audio.mix import Mixer, probe_channels
from scenerender.audio.spatial import Layout, pan_to_azimuth
from scenerender.evaluator import Evaluator
from scenerender.registry import AUDIO_EFFECTS, FEATURES, FULL

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures")
MEDIA = os.path.join(FIX, "media")
SR = 48000


def _write(path, x, sr=SR):
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    q = np.clip(np.round(x * 32767), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(q.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(q.tobytes())


def make_media() -> None:
    """Deterministic media for the audio_*.xml fixtures (tests/fixtures/media/audio_*)."""
    os.makedirs(MEDIA, exist_ok=True)
    t = np.arange(4 * SR) / SR
    _write(os.path.join(MEDIA, "audio_tone440.wav"), 0.25 * np.sin(2 * np.pi * 440 * t))
    rng = np.random.default_rng(7)
    noisy = 0.05 * rng.standard_normal(len(t)) + 0.3 * np.sin(2 * np.pi * 1000 * t) * (t >= 1.0)
    _write(os.path.join(MEDIA, "audio_noisy.wav"), noisy)
    bars = []
    for f in (300, 400, 500, 700):
        tb = np.arange(2 * SR) / SR
        bars.append(0.3 * np.sin(2 * np.pi * f * tb))
    tr = np.arange(SR // 2) / SR
    bars.append(0.3 * np.sin(2 * np.pi * 700 * (tr + 2.0)) * np.exp(-tr / 0.15))
    _write(os.path.join(MEDIA, "audio_bars.wav"), np.concatenate(bars))
    t16 = np.arange(16 * SR) / SR
    lv = np.where(t16 < 8, 10 ** (-30 / 20), 10 ** (-10 / 20)) * np.sin(2 * np.pi * 1000 * t16)
    _write(os.path.join(MEDIA, "audio_levels.wav"), np.stack([lv, lv], 1))
    t2 = np.arange(2 * SR) / SR
    _write(os.path.join(MEDIA, "audio_stereo.wav"),
           np.stack([0.25 * np.sin(2 * np.pi * 500 * t2), 0.25 * np.sin(2 * np.pi * 700 * t2)], 1))
    av = os.path.join(MEDIA, "audio_av.mkv")
    if not os.path.exists(av):
        ta = np.arange(SR) / SR
        wav = os.path.join(MEDIA, "audio_av_src.wav")
        _write(wav, 0.3 * np.sin(2 * np.pi * np.where(ta < 0.5, 440, 880) * ta))
        import imageio_ffmpeg
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "testsrc=size=64x36:rate=10:duration=1", "-i", wav, "-c:v", "ffv1",
                        "-c:a", "pcm_s16le", "-shortest", av], check=True)
        os.remove(wav)


@pytest.fixture(scope="module", autouse=True)
def media():
    make_media()
    from test_audio import make_audio_media
    make_audio_media()


def mixer(name: str, replace: dict | None = None, tmp_path=None, renderer: bool = False):
    path = os.path.join(FIX, name)
    if replace:
        src = open(path, encoding="utf-8").read()
        for a, b in replace.items():
            assert a in src, a
            src = src.replace(a, b)
        path = str(tmp_path / name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(src.replace('src="media/', f'src="{MEDIA}/'))
    if renderer:
        from scenerender.audio import mixer_for
        from scenerender.render import Renderer
        return mixer_for(Renderer.open(path))
    doc = document.load(path)
    return Mixer(doc, Evaluator(doc))


def peak_hz(x, sr=SR):
    x = np.asarray(x, np.float64)
    x = x[:, 0] if x.ndim > 1 else x
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)), 8 * len(x)))
    return float(np.argmax(X)) * sr / (8 * len(x))


def rms(x):
    return float(np.sqrt(np.mean(np.square(np.asarray(x, np.float64)))))


# ---------------------------------------------------------------- levels
def test_features_full():
    for k in ("audio:normalize-dynamic", "audio:fitToDuration", "audio:preservePitch", "audio:surround",
              "audio:layerAudio", "transitionAudio"):
        assert FEATURES.entries[k].level == FULL, k
    for k in ("reverb", "pitch-shift", "noise-reduction"):
        assert AUDIO_EFFECTS.entries[k].level == FULL


# ---------------------------------------------------------------- time stretch / pitch
def test_preserve_pitch_speed():
    m = mixer("audio_stretch.xml")
    fast = m.track("fast").data
    assert fast.shape[0] == round(4 * SR / 1.5)                   # duration scales
    assert peak_hz(fast[SR // 4:-SR // 4]) == pytest.approx(440, abs=1.0)
    assert rms(fast[SR // 4:-SR // 4]) == pytest.approx(0.25 / np.sqrt(2), rel=0.03)
    vs = m.track("varispeed").data                                   # preservePitch=false
    assert vs.shape[0] == fast.shape[0]
    assert peak_hz(vs[SR // 4:-SR // 4]) == pytest.approx(660, abs=1.0)
    slow = m.track("slow").data
    assert slow.shape[0] == round(2 * SR / 0.75)
    assert peak_hz(slow[SR // 4:-SR // 4]) == pytest.approx(440, abs=1.0)


def test_time_stretch_stereo_and_transients():
    t = np.arange(2 * SR) / SR
    x = np.stack([np.sin(2 * np.pi * 300 * t), np.sin(2 * np.pi * 300 * t + 1.0)], 1).astype(np.float32) * 0.3
    y = spectral.time_stretch(x, 0.8, SR)
    assert y.shape == (round(2 * SR / 0.8), 2)
    mid = y[SR // 2:-SR // 2]
    # inter-channel phase (the stereo image) survives: same lag between L and R as the input
    c = np.correlate(mid[:4000, 0], mid[:4000, 1], "full")
    lag = np.argmax(c) - 3999
    assert abs(abs(lag) - round(1.0 / (2 * np.pi * 300) * SR)) <= 2 or abs(lag - (SR / 300 - 1.0 / (2 * np.pi * 300) * SR)) <= 2
    # a click stays a click (phase reset at onsets): energy concentrated near the stretched onset
    z = np.zeros((SR, 1), np.float32)
    z[SR // 2:SR // 2 + 48, 0] = np.hanning(48)
    s = spectral.time_stretch(z, 0.5, SR)[:, 0]
    env = np.abs(s)
    assert abs(int(np.argmax(env)) - SR) < 1500
    assert np.sum(env[SR - 2400:SR + 2400] ** 2) > 0.8 * np.sum(env ** 2)


def test_pitch_shift_octave_and_formants():
    m = mixer("audio_stretch.xml")
    y = m.track("octave").data
    assert y.shape[0] == 4 * SR
    assert peak_hz(y[SR // 4:-SR // 4]) == pytest.approx(880, abs=1.5)
    # formant preservation: harmonics of 150 Hz through a resonance at 1 kHz
    t = np.arange(2 * SR) / SR
    x = sum(np.sin(2 * np.pi * 150 * k * t) * np.exp(-((150 * k - 1000) / 250.0) ** 2) for k in range(1, 30))
    x = (0.3 * x / np.abs(x).max())[:, None].astype(np.float32)

    def centroid(sig):
        S = np.abs(np.fft.rfft(sig[SR // 4:-SR // 4, 0])) ** 2
        f = np.fft.rfftfreq(len(sig) - SR // 2, 1 / SR)
        return float(np.sum(f * S) / np.sum(S))
    plain = spectral.pitch_shift(x, 7, SR)
    kept = spectral.pitch_shift(x, 7, SR, formants=True)
    c0, c1, c2 = centroid(x), centroid(plain), centroid(kept)
    assert c1 > c0 * 1.35                        # the envelope moved up with the pitch
    assert abs(c2 - c0) < abs(c1 - c0) * 0.5     # ...and is restored with formants=true


# ---------------------------------------------------------------- noise reduction
def _snr(y, sig_region):
    t = np.arange(len(y)) / SR
    ref = np.sin(2 * np.pi * 1000 * t)[sig_region]
    s = np.asarray(y, np.float64)[sig_region, 0]
    a = np.dot(s, ref) / np.dot(ref, ref)
    res = s - a * ref
    return 10 * np.log10(np.sum((a * ref) ** 2) / np.sum(res ** 2))


def test_noise_reduction_improves_snr():
    m = mixer("audio_stretch.xml")
    reg = slice(int(1.3 * SR), int(3.7 * SR))
    raw = m.decode_asset(m.doc.ids["noisy"])
    before = _snr(raw, reg)
    for tid in ("clean", "clean-profile"):
        y = m.track(tid).data
        assert _snr(y, reg) > before + 10, tid
        # the tone keeps its level, the noise-only part is strongly reduced
        assert rms(y[reg]) == pytest.approx(rms(raw[reg]) * 0.986, rel=0.03)
        assert rms(y[int(0.1 * SR):int(0.9 * SR)]) < rms(raw[int(0.1 * SR):int(0.9 * SR)]) * 0.2


# ---------------------------------------------------------------- dynamic loudness
def test_dynamic_normalize_tracks_short_term_loudness():
    m = mixer("audio_dynamic.xml")
    y = m.render()
    t, S = dsp.short_term_loudness(y, SR)
    for a, b in ((4.5, 6.4), (13.0, 16.0)):          # (the centred window anticipates the step at 8 s)
        sel = (t >= a) & (t <= b)
        assert np.all(np.abs(S[sel] + 16.0) < 1.0), (a, b, S[sel].min(), S[sel].max())
    assert dsp.true_peak_db(y) <= -0.9
    # the programme itself changes by 20 dB; the AGC moved the gain by ~20 dB between sections
    tg, g = m.stats["dynamic_gain_db"]
    assert g[np.searchsorted(tg, 6.0)] - g[np.searchsorted(tg, 15.0)] == pytest.approx(20, abs=1.5)


def test_dynamic_holds_gain_through_silence(tmp_path):
    x = np.zeros((10 * SR, 2), np.float32)
    t = np.arange(3 * SR) / SR
    x[:3 * SR] = (0.05 * np.sin(2 * np.pi * 1000 * t))[:, None]
    x[7 * SR:] = (0.05 * np.sin(2 * np.pi * 1000 * t))[:, None]
    tg, g = dsp.dynamic_gain_db(x, SR, -16.0)
    mid = g[(tg > 4.5) & (tg < 5.5)]
    assert np.ptp(mid) < 1.0 and mid.max() < 20.0 - 1e-6       # no pumping up of the pause


# ---------------------------------------------------------------- fitToDuration
def test_fit_to_duration_ends_exactly_on_bar_grid():
    m = mixer("audio_fit.xml")
    seg = m.track("music")
    assert seg.i0 == 0 and seg.i1 == m.n == int(np.ceil(13.3 * SR))
    x = seg.data[:, 0]
    # M = 6 whole bars: bars 0 1 2 0 1 (body loops back to bar 0) then the ending bar (700 Hz)
    expect = [300, 400, 500, 300, 400, 700]
    for j, f in enumerate(expect):
        part = x[j * 2 * SR + SR // 10:(j + 1) * 2 * SR - SR // 10]
        assert peak_hz(part) == pytest.approx(f, abs=2), j
    # ring-out follows the ending bar, then silence to the last sample
    assert rms(x[12 * SR + 100:12 * SR + 2400]) > 0.1
    assert rms(x[int(12.6 * SR):]) < 1e-3
    # seams are crossfaded: no sample-to-sample jump beyond the sine's own slope
    assert np.max(np.abs(np.diff(x[3 * 2 * SR - 2000:3 * 2 * SR + 2000]))) < 0.3 * 2 * np.pi * 500 / SR * 1.5


def test_fit_to_duration_trims_and_uses_beatgrid(tmp_path):
    # shorter project: 5 s -> 2 whole bars: bar 0 then the ending; ring-out truncated at the end
    m = mixer("audio_fit.xml", {'duration="13.3"': 'duration="4.2"'}, tmp_path)
    x = m.track("music").data[:, 0]
    assert len(x) == m.n
    assert peak_hz(x[SR // 10:2 * SR - SR // 10]) == pytest.approx(300, abs=2)
    assert peak_hz(x[2 * SR + SR // 10:4 * SR - SR // 10]) == pytest.approx(700, abs=2)
    assert abs(x[-1]) < 1e-3                     # truncated ring-out faded to the last sample
    # no asset bpm: tempo and bar length from a beatGrid whose source is the asset (3/4 at 90 bpm = 2 s)
    m = mixer("audio_fit.xml", {' bpm="120"': "", "<composition/>":
                                '<markers><beatGrid bpm="90" beatsPerBar="3" source="bars"/></markers><composition/>'}, tmp_path)
    assert m.tempo(m.doc.ids["bars"]) == (90.0, 3)
    x = m.track("music").data[:, 0]
    assert len(x) == m.n and peak_hz(x[10 * SR + SR // 10:12 * SR - SR // 10]) == pytest.approx(700, abs=2)


# ---------------------------------------------------------------- surround
def test_51_pan_vbap_channel_order():
    m = mixer("audio_surround.xml")
    assert m.nch == 6 and m.layout.channels == ("FL", "FR", "FC", "LFE", "BL", "BR")
    assert m.ffmpeg_layout == "5.1" and m.layout.wav_mask == 0x3F
    left = m.track("left").data
    per = np.sqrt(np.mean(left.astype(np.float64) ** 2, axis=0))
    assert per[0] > 0.1 and np.all(per[1:] < 1e-6)                   # hard-left: FL only
    ctr = np.sqrt(np.mean(m.track("centre").data.astype(np.float64) ** 2, axis=0))
    assert ctr[2] == pytest.approx(0.25 / np.sqrt(2), rel=1e-3) and np.all(np.delete(ctr, 2) < 1e-6)
    rear = np.sqrt(np.mean(m.track("rear").data.astype(np.float64) ** 2, axis=0))
    assert rear[4] == pytest.approx(rear[5], rel=1e-3) and rear[:4].max() < 1e-6   # pan 2 = behind
    wide = m.track("wide").data                                       # stereo source -> FL/FR
    assert peak_hz(wide[:, 0]) == pytest.approx(500, abs=1) and peak_hz(wide[:, 1]) == pytest.approx(700, abs=1)
    assert np.abs(wide[:, 2:]).max() < 1e-6
    mix = m.render()
    assert mix.shape == (2 * SR, 6) and np.abs(mix[:, 3]).max() < 1e-6          # LFE silent
    # the bus reverb puts its wet signal on the left/right speakers, not in the centre
    fx = m.bus("fx")
    assert np.abs(fx[:, 2]).max() < 1e-6 and rms(fx[:, 0]) > 1e-4


def test_vbap_power_and_layouts():
    lay = Layout("7.1.4")
    assert lay.n == 12 and lay.ffmpeg == "7.1.4"
    for p in np.linspace(-2, 2, 41):
        g = lay.pan_gains(pan_to_azimuth(p))[0]
        assert np.sum(g ** 2) == pytest.approx(1.0, abs=1e-9)
        assert np.all(g[[3, 8, 9, 10, 11]] == 0)                        # LFE and heights
    assert Layout.resolve("auto", 6)[0].name == "5.1"
    assert Layout.resolve("5.1", 2)[1] is not None                     # disagreement warned
    w = Layout("5.1").loudness_weights()
    assert list(w) == [1, 1, 1, 0, 1.41, 1.41]


def test_wav_extensible_roundtrip(tmp_path):
    x = (0.1 * np.random.default_rng(1).standard_normal((4800, 6))).astype(np.float32)
    p = str(tmp_path / "s.wav")
    write_wav(p, x, SR, 24, False)
    assert probe_channels(p) == 6
    y = decode(p, SR, 6)
    assert np.max(np.abs(y - x)) < 1e-6
    import imageio_ffmpeg
    info = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", p], capture_output=True, text=True).stderr
    assert "5.1" in info


def test_ambisonic_first_order_gains(tmp_path):
    m = mixer("audio_ambisonic.xml")
    assert m.nch == 4 and m.ffmpeg_layout == "ambisonic 1" and m.layout.wav_mask == 0
    t = np.arange(m.n) / SR
    ref = 0.25 * np.sin(2 * np.pi * 440 * t)

    def gains(tid):
        d = m.track(tid).data.astype(np.float64)
        return d.T @ ref / (ref @ ref)
    W, Y, Z, X = gains("a30")                                   # azimuth +30 deg (pan -1)
    assert (W, Y, Z, X) == pytest.approx((1.0, 0.5, 0.0, np.sqrt(3) / 2), abs=1e-3)
    W, Y, Z, X = gains("back")                                  # azimuth 180 deg (pan -2)
    assert (W, Y, Z, X) == pytest.approx((1.0, 0.0, 0.0, -1.0), abs=1e-3)
    # bus pan rotates the sound field: front source through pan=1 (-30 deg)
    b = m.bus("spin").astype(np.float64).T @ ref / (ref @ ref)
    assert tuple(b) == pytest.approx((1.0, -0.5, 0.0, np.sqrt(3) / 2), abs=1e-3)
    m3 = mixer("audio_ambisonic.xml", {'channels="4" channelLayout="ambisonic-1"': 'channels="16" channelLayout="ambisonic-3"'}, tmp_path)
    d = m3.track("a30").data.astype(np.float64)
    g = d.T @ ref / (ref @ ref)
    assert m3.nch == 16 and g[0] == pytest.approx(1.0, abs=1e-3)
    # ACN 4 (V, sqrt(3)/2 sin 2az) and ACN 8 (U, sqrt(3)/2 cos 2az) at 30 deg; ACN 15 = sqrt(5/8) cos 3az = 0
    assert g[4] == pytest.approx(np.sqrt(3) / 2 * np.sin(np.pi / 3), abs=1e-3)
    assert g[8] == pytest.approx(np.sqrt(3) / 2 * np.cos(np.pi / 3), abs=1e-3)
    assert g[15] == pytest.approx(0.0, abs=1e-3) and g[9] == pytest.approx(np.sqrt(5 / 8), abs=1e-3)


# ---------------------------------------------------------------- layer audio in instances
def test_layer_audio_in_instance_uses_instance_clock():
    m = mixer("audio_instance.xml", renderer=True)
    assert m.has_audio()
    x = m.render()[:, 0]
    assert rms(x[:int(0.95 * SR)]) < 1e-4                          # before the first instance
    # "late": clipIn 0.5 + loop -> the 880 Hz half plays twice from t = 1 s
    assert peak_hz(x[int(1.05 * SR):int(1.45 * SR)]) == pytest.approx(880, abs=3)
    assert peak_hz(x[int(1.55 * SR):int(1.95 * SR)]) == pytest.approx(880, abs=3)
    assert rms(x[int(2.05 * SR):int(2.45 * SR)]) < 1e-4
    # "half": speed 0.5 -> source 0..0.5 s over 2.5..3.5 s at half speed (varispeed: 220 Hz)
    assert peak_hz(x[int(2.6 * SR):int(3.4 * SR)]) == pytest.approx(220, abs=3)
    assert peak_hz(x[int(3.55 * SR):int(3.95 * SR)]) == pytest.approx(440, abs=3)


# ---------------------------------------------------------------- transition audio on tracks
def test_transition_audio_cut_on_owned_tracks():
    m = mixer("audio_transition.xml")
    dlg = m.track("dlg")
    x = dlg.data[:, 0]
    tt = (dlg.i0 + np.arange(len(x))) / SR
    assert rms(x[(tt > 1.5) & (tt < 1.99)]) > 0.1
    assert np.abs(x[tt >= 2.0]).max() == 0.0                       # cut at the transition's cut point
    bed = m.track("bed").data[:, 0]                                # music beds are not owned
    assert rms(bed[-SR // 2:]) > 0.1
    b = m.track("dlgB")                                            # sceneB's own track: unaffected
    assert rms(b.data[:, 0]) > 0.1


def test_transition_audio_crossfade_on_owned_tracks(tmp_path):
    m = mixer("audio_transition.xml", {'audio="cut"': 'audio="equal-power"'}, tmp_path)
    dlg = m.track("dlg")
    x = dlg.data[:, 0].astype(np.float64)
    tt = (dlg.i0 + np.arange(len(x))) / SR
    env = lambda a, b: rms(x[(tt >= a) & (tt < b)]) / (0.25 / np.sqrt(2))  # noqa: E731
    assert env(1.5, 1.79) == pytest.approx(1.0, rel=0.02)
    assert env(1.99, 2.01) == pytest.approx(np.cos(np.pi / 4), rel=0.05)    # halfway through the window
    assert np.abs(x[tt >= 2.2]).max() == 0.0


# ---------------------------------------------------------------- effect attributes
def _fx(el, sr=SR, bpm=None, key=None):
    def get(k, d):
        for p in el:
            if p.tag == "param" and p.get("name") == k:
                try:
                    return float(p.get("value"))
                except ValueError:
                    return p.get("value")
        v = el.get(k)
        if v is None:
            return d
        try:
            return float(v)
        except ValueError:
            return v
    return FxContext(el, sr, 0.0, get, lambda k, d, n: np.full(n, float(get(k, d))), key or (lambda i, n: None),
                     0, bpm, None)


def test_freeverb_damping_room_and_predelay():
    imp = np.zeros((3 * SR, 1), np.float32)
    imp[0] = 1.0

    def wet(**attrs):
        el = etree.Element("audioEffect", type="reverb", **{k: str(v) for k, v in attrs.items() if k != "damping"})
        if "damping" in attrs:
            etree.SubElement(el, "param", name="damping", value=str(attrs["damping"]))
        y = AUDIO_EFFECTS.get("reverb")(_fx(el), imp)[:, 0].astype(np.float64)
        y[0] -= 1.0                                        # remove the dry impulse
        return y
    # pre-delay: nothing before `time`, then the first comb echoes
    y = wet(roomSize=0.5, time=0.1)
    assert np.abs(y[:int(0.1 * SR)]).max() < 1e-9 and np.abs(y[int(0.1 * SR):int(0.2 * SR)]).max() > 1e-4
    # bigger rooms ring longer
    late = lambda v: np.sum(v[int(1.0 * SR):] ** 2)  # noqa: E731
    assert late(wet(roomSize=0.9)) > 10 * late(wet(roomSize=0.3))
    # damping inside the comb loops: high frequencies of the tail decay faster
    def hf_ratio(v):
        seg = v[int(0.6 * SR):int(1.2 * SR)]
        S = np.abs(np.fft.rfft(seg)) ** 2
        f = np.fft.rfftfreq(len(seg), 1 / SR)
        return np.sum(S[f > 5000]) / np.sum(S[(f > 100) & (f < 1000)])
    assert hf_ratio(wet(roomSize=0.7, damping=0.9)) < 0.1 * hf_ratio(wet(roomSize=0.7, damping=0.0))


def test_delay_sync_to_bpm_and_feedback():
    m = mixer("audio_stretch.xml")
    seg = m.track("echo")
    x = np.abs(seg.data[:, 0])
    assert note_seconds("1/4", 120) == 0.5 and note_seconds("1/8d", 120) == pytest.approx(0.375)
    assert note_seconds("1/4t", 120) == pytest.approx(1 / 3)
    first = int(np.argmax(x[:SR // 4]))
    echo = int(np.argmax(x[SR // 4:3 * SR // 4])) + SR // 4
    assert (echo - first) / SR == pytest.approx(0.5, abs=0.002)
    assert x[echo + SR // 2 - 200:echo + SR // 2 + 200].max() == pytest.approx(0.5 * x[echo], rel=0.1)   # feedback 0.5


def test_gate_hysteresis_and_expander():
    t = np.arange(SR) / SR
    lvl = np.where((t > 0.3) & (t < 0.7), 10 ** (-24 / 20), 10 ** (-12 / 20))   # dips 2 dB under threshold
    x = (lvl * np.sin(2 * np.pi * 500 * t) * np.sqrt(2))[:, None].astype(np.float32)
    el = etree.Element("audioEffect", type="gate", threshold="-22", amount="0.5", attack="0.001", release="0.01")
    closed = AUDIO_EFFECTS.get("gate")(_fx(el), x)
    etree.SubElement(el, "param", name="hysteresis", value="6")
    held = AUDIO_EFFECTS.get("gate")(_fx(el), x)
    mid = slice(int(0.4 * SR), int(0.6 * SR))
    assert rms(closed[mid]) < rms(x[mid]) * 0.05                  # closes below threshold
    assert rms(held[mid]) == pytest.approx(rms(x[mid]), rel=0.02)  # stays open within the hysteresis
    ex = etree.Element("audioEffect", type="gate", threshold="-20", ratio="2", knee="0", amount="1",
                       attack="0.001", release="0.001")
    y = AUDIO_EFFECTS.get("gate")(_fx(ex), x)
    # expander 1:2: -24 dB (4 dB under) -> 4 dB more reduction
    assert 20 * np.log10(rms(y[mid]) / rms(x[mid])) == pytest.approx(-4.0, abs=0.5)


def test_generic_output_gain_and_limiter_threshold():
    x = (0.5 * np.sin(2 * np.pi * 300 * np.arange(SR) / SR))[:, None].astype(np.float32)
    from scenerender.audio.effects import process_chain
    el = etree.Element("audioEffect", type="highpass", frequency="50", gain="-6")
    y = process_chain([el], x, lambda e: _fx(e))
    assert rms(y[SR // 2:]) / rms(x[SR // 2:]) == pytest.approx(10 ** (-6 / 20), rel=0.02)
    lim = etree.Element("audioEffect", type="limiter", threshold="-12", attack="0.005")
    y = AUDIO_EFFECTS.get("limiter")(_fx(lim), x)
    assert np.abs(y).max() <= 10 ** (-12 / 20) + 1e-4


def test_eq_band_q_and_shelves():
    b, a = dsp.biquad("peak", 1000, SR, 4.0, 6.0)
    w = np.exp(-2j * np.pi * np.array([1000, 1250]) / SR)
    H = np.abs((b[0] + b[1] * w + b[2] * w * w) / (a[0] + a[1] * w + a[2] * w * w))
    assert 20 * np.log10(H[0]) == pytest.approx(6.0, abs=0.01)
    assert 20 * np.log10(H[1]) < 2.0                             # q=4 is narrow
    b, a = dsp.biquad("low-shelf", 200, SR, 0.707, -9.0)
    w = np.exp(-2j * np.pi * np.array([20.0, 10000.0]) / SR)
    H = np.abs((b[0] + b[1] * w + b[2] * w * w) / (a[0] + a[1] * w + a[2] * w * w))
    assert 20 * np.log10(H[0]) == pytest.approx(-9.0, abs=0.1) and 20 * np.log10(H[1]) == pytest.approx(0, abs=0.1)
