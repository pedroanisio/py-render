"""Delivery QA: WCAG 2.3.1 flash detection, contrast of burned text, safe-area enforcement, required captions,
audio description, the `scenerender check` command and QA failures in `scenerender render`."""
from __future__ import annotations

import os

import numpy as np
import pytest

from scenerender import qa
from scenerender.cli import main

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures", "output_qa.xml")


@pytest.fixture(scope="module", autouse=True)
def media():
    from test_audio import make_audio_media
    make_audio_media()


def _flash_frames(hz: float, fps: int = 30, seconds: float = 2.0, size=(96, 128), area=1.0, colour=(255, 255, 255)):
    n = int(fps * seconds)
    h, w = size
    for i in range(n):
        on = int(i / fps * hz * 2) % 2 == 1
        img = np.zeros((h, w, 3), np.uint8)
        if on:
            ah, aw = int(h * area ** 0.5), int(w * area ** 0.5)
            img[:ah, :aw] = colour
        yield img


def _detect(frames, fps=30):
    det = qa.FlashDetector(fps)
    for f in frames:
        det.feed(f)
    return det


def test_flash_5hz_fails_2hz_passes():
    fail = _detect(_flash_frames(5))
    v = fail.violations()
    assert v and v[0][0] == "general" and v[0][3] >= 4
    assert fail.findings("error")[0].level == "error"
    assert _detect(_flash_frames(2)).violations() == []
    assert _detect(_flash_frames(3)).violations() == []                # exactly 3 flashes a second is allowed


def test_flash_area_and_luminance_thresholds():
    # a 5 Hz flash over a small patch (under 25 % of the 10-degree window) passes
    assert _detect(_flash_frames(5, area=0.02)).violations() == []
    # both states brighter than 0.8 relative luminance: not a flash
    bright = ((np.full((64, 64, 3), 255 if (i // 3) % 2 else 235, np.uint8)) for i in range(60))
    assert _detect(bright).violations() == []
    # dim flicker below a 10 % luminance change: not a flash
    dim = ((np.full((64, 64, 3), 30 if (i // 3) % 2 else 20, np.uint8)) for i in range(60))
    assert _detect(dim).violations() == []


def test_red_flash():
    red = _detect(_flash_frames(5, colour=(255, 0, 0)))
    kinds = {k for k, *_ in red.violations()}
    assert "red" in kinds
    # a saturated red flash at 2 Hz passes; a desaturated pink flash is no red flash
    assert _detect(_flash_frames(2, colour=(255, 0, 0))).violations() == []
    pink = _detect(_flash_frames(5, colour=(255, 150, 150)))
    assert "red" not in {k for k, *_ in pink.violations()}


def test_slow_ramps_count_as_transitions():
    # a luminance ramp over several frames still makes opposing transitions (per-pixel extremes)
    fps = 30
    frames = []
    for i in range(60):
        ph = (i % 6) / 6
        v = int(255 * (1 - abs(2 * ph - 1)))
        frames.append(np.full((32, 32, 3), v, np.uint8))
    assert _detect(frames, fps).violations()


def test_contrast_ratio_formula():
    assert float(qa.contrast_ratio(1.0, 0.0)) == pytest.approx(21.0)
    assert float(qa.contrast_ratio(0.5, 0.5)) == pytest.approx(1.0)


def _findings(**kw):
    from scenerender.render import Renderer
    r = Renderer.open(FIX, scale=0.5)
    f, written = qa.static_checks(r, t0=0.0, t1=2.0, **kw)
    return f, written


def test_static_checks_catch_grey_on_grey_safe_area_and_captions(tmp_path):
    f, written = _findings(out_path=str(tmp_path / "x.mp4"))
    by = {}
    for x in f:
        by.setdefault(x.check, []).append(x)
    assert [x.message for x in by["contrastCheck"]] and all("l-grey" in x.message for x in by["contrastCheck"])
    assert not any("l-white" in x.message for x in by["contrastCheck"])
    assert any("cta" in x.message and "right" in x.message for x in by["safeArea"])
    assert not any("l-white" in x.message or "l-grey" in x.message for x in by["safeArea"])
    assert by["requireCaptions"][0].level == "error"
    # the muted audio-description track is written as a sidecar WAV
    assert written == [str(tmp_path / "x.audio-description.wav")] and os.path.getsize(written[0]) > 1000


def test_captions_satisfy_require_captions(tmp_path):
    src = open(FIX).read().replace("</scene>", """  <captions>
    <captionTrack id="cc" language="en" mode="sidecar"><cue start="0" end="1" text="hello"/></captionTrack>
  </captions>
</scene>""")
    p = tmp_path / "c.xml"
    p.write_text(src)
    from scenerender.render import Renderer
    f, _ = qa.static_checks(Renderer.open(str(p), scale=0.5), t0=0, t1=2, write=False)
    assert not any(x.check == "requireCaptions" for x in f)


def test_missing_audio_description():
    src = open(FIX).read().replace('audioDescription="ad"', 'audioDescription="l-white"')
    from scenerender import document
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".xml", dir=os.path.dirname(FIX), delete=False) as t:
        t.write(src)
    try:
        from scenerender.render import Renderer
        f, _ = qa.static_checks(Renderer.open(t.name, scale=0.5), t0=0, t1=2, write=False)
    finally:
        os.remove(t.name)
    assert any(x.check == "audioDescription" and "not an audio track" in x.message for x in f)
    assert document is not None


def test_check_command_and_render_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["check", FIX, "--scale", "0.25"]) == qa.EXIT_QA
    out = capsys.readouterr().out
    assert "flashCheck" in out and "contrastCheck" in out and "safeArea" in out
    # render fails QA before encoding (static errors) -> exit status 4 and no file
    assert main(["render", FIX, "--scale", "0.25", "--jobs", "1", "-o", "q.mp4"]) == qa.EXIT_QA
    assert not os.path.exists("q.mp4")
    # --no-qa renders it anyway
    assert main(["render", FIX, "--scale", "0.25", "--jobs", "1", "--no-qa", "-o", "q.mp4"]) == 0
    assert os.path.exists("q.mp4")


def test_flash_only_failure_after_streaming(tmp_path, monkeypatch, capsys):
    """Only the flash check at level error: the frames stream, the analysis fails the output afterwards."""
    src = open(FIX).read()
    src = src.replace('contrastCheck="error"', 'contrastCheck="off"').replace('requireCaptions="true"', 'requireCaptions="false"')
    src = src.replace('audioDescription="ad"', "").replace('enforce="error"', 'enforce="off"')
    p = tmp_path / "flash.xml"
    p.write_text(src.replace('src="media/', f'src="{os.path.join(HERE, "fixtures", "media")}/'))
    monkeypatch.chdir(tmp_path)
    assert main(["render", str(p), "--scale", "0.25", "--jobs", "1", "-o", "f.mp4"]) == qa.EXIT_QA
    err = capsys.readouterr().err
    assert "general flashes" in err
    slow = tmp_path / "slow.xml"
    slow.write_text(p.read_text().replace("time * 10", "time * 4"))
    assert main(["render", str(slow), "--scale", "0.25", "--jobs", "1", "-o", "s.mp4"]) == 0
