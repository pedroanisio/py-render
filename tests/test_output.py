"""Outputs: -o files (mp4, gif, png sequence, ...), <output> elements, stills, destinations,
parallel/resumed frame rendering and the ffmpeg argument builders."""
from __future__ import annotations

import os
import re
import subprocess

import numpy as np
import pytest

from scenerender import output as O
from scenerender.cli import main

HERE = os.path.dirname(__file__)
DEMO = os.path.join(HERE, "fixtures", "audio_demo.xml")


@pytest.fixture(scope="module", autouse=True)
def media():
    from test_audio import make_audio_media
    make_audio_media()


def ff() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe(path: str) -> str:
    return subprocess.run([ff(), "-hide_banner", "-i", path], capture_output=True, text=True).stderr


def decode_frames(path: str) -> tuple[int, int, int]:
    """(frames, width, height) by decoding every frame."""
    info = probe(path)
    m = re.search(r"Video: .*?, (\d+)x(\d+)", info)
    w, h = int(m.group(1)), int(m.group(2))
    raw = subprocess.run([ff(), "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return len(raw) // (w * h * 3), w, h


def loudness(path: str) -> float:
    err = subprocess.run([ff(), "-hide_banner", "-nostats", "-i", path, "-map", "0:a", "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])


def render(*argv) -> int:
    return main(["render", DEMO, *argv])


def test_mp4_with_audio(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert render("--scale", "0.25", "--to", "1", "--jobs", "1", "-o", "a.mp4") == 0
    n, w, h = decode_frames("a.mp4")
    assert (n, w, h) == (24, 80, 46)          # 45 rows padded to even for 4:2:0
    info = probe("a.mp4")
    assert "Audio: aac" in info and "bt709" in info
    assert "title" in info and "Audio demo" in info


def test_gif_and_png_sequence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert render("--scale", "0.25", "--to", "0.5", "--jobs", "1", "-o", "a.gif") == 0
    n, w, h = decode_frames("a.gif")
    assert (w, h) == (80, 45) and n == 12
    assert render("--scale", "0.25", "--from", "1", "--to", "1.5", "--jobs", "1", "-o", "seq/") == 0
    frames = sorted(f for f in os.listdir("seq") if f.endswith(".png"))
    assert len(frames) == 12 and frames[0] == "frame_000024.png"
    from PIL import Image
    assert Image.open(os.path.join("seq", frames[0])).size == (80, 45)
    assert os.path.exists(os.path.join("seq", "audio.wav"))


def test_outputs_from_document(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert render("--scale", "0.5", "--jobs", "1") == 0
    n, w, h = decode_frames("audio_demo.mp4")
    assert (n, w, h) == (96, 80, 46)          # output width 160 x --scale 0.5
    assert loudness("audio_demo.mp4") == pytest.approx(-16.0, abs=0.5)
    assert loudness("audio_demo.wav") == pytest.approx(-16.0, abs=0.3)
    from PIL import Image
    assert Image.open("audio_demo_poster.png").size == (80, 45)
    assert Image.open("audio_demo_thumb.jpg").size == (80, 45)
    # caption sidecar next to the video
    assert "One" in open("audio_demo.en.srt", encoding="utf-8").read()
    # file destination copied, s3 skipped
    assert os.path.exists(os.path.join("delivered", "audio_demo.mp4"))
    # --output filters
    os.remove("audio_demo.wav")
    assert render("--output", "out-wav") == 0
    assert os.path.exists("audio_demo.wav") and not os.path.exists("x.mp4")
    assert render("--output", "nope") == 2


def test_parallel_matches_serial_and_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert render("--scale", "0.25", "--to", "1", "--jobs", "1", "--no-audio", "-o", "s.mkv") == 0
    assert render("--scale", "0.25", "--to", "1", "--jobs", "3", "--no-audio", "--frames-dir", "fr", "-o", "p.mkv") == 0
    raw = lambda p: subprocess.run([ff(), "-v", "error", "-i", p, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],  # noqa: E731
                                   capture_output=True, check=True).stdout
    assert raw("s.mkv") == raw("p.mkv")
    assert "Audio:" not in probe("p.mkv")
    frames = sorted(os.listdir("fr"))
    assert len(frames) == 24
    # a rerun reuses the kept frames (they are not rewritten)
    marker = os.path.join("fr", frames[5])
    os.utime(marker, (1, 1))
    assert render("--scale", "0.25", "--to", "1", "--jobs", "1", "--no-audio", "--frames-dir", "fr", "-o", "q.mkv") == 0
    assert os.stat(marker).st_mtime == 1
    assert raw("q.mkv") == raw("s.mkv")


def test_two_pass_and_max_file_size(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = open(DEMO, encoding="utf-8").read()
    src = src.replace('src="media/', f'src="{os.path.join(HERE, "fixtures", "media")}/')
    src = src.replace('<output id="out-mp4" path="audio_demo.mp4" codec="h264" width="160" height="90" crf="28" preset="ultrafast">',
                      '<output id="out-mp4" path="tp.mp4" codec="h264" width="160" height="90" twoPass="true" '
                      'maxFileSize="60000" preset="ultrafast" colorSpace="rec2020" colorRange="full">')
    (tmp_path / "d.xml").write_text(src)
    assert main(["render", "d.xml", "--output", "out-mp4", "--jobs", "1"]) == 0
    size = os.path.getsize("tp.mp4")
    assert size < 60000 * 1.25
    info = probe("tp.mp4")
    assert "bt2020" in info and "pc" in info


def test_exr_and_alpha(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert render("--scale", "0.25", "--to", "0.25", "--jobs", "1", "-o", "e_%04d.exr") == 0
    assert sorted(f for f in os.listdir(".") if f.endswith(".exr"))[0] == "e_0000.exr"
    assert "exr" in probe("e_0000.exr")


# ---------------------------------------------------------------- argument builders
def _job(**attrs):
    j = O.Job(name="t", path="x.mp4", codec=attrs.pop("codec", "h264"), container=attrs.pop("container", "mp4"))
    j.attrs = attrs
    j.fps = O.Fraction(24)
    return j


def test_codec_args():
    a, vf, br, _ = O.video_codec_args(_job(crf=20, preset="slow", keyframeInterval="1"), 10, 192000)
    assert a[a.index("-crf") + 1] == "20" and a[a.index("-g") + 1] == "24" and not br
    assert "out_color_matrix=bt709" in vf
    a, _, br, _ = O.video_codec_args(_job(maxFileSize="1000000"), 10, 192000)
    assert br and int(a[a.index("-b:v") + 1]) == int(1000000 * 8 * 0.97 / 10 - 192000)
    a, _, _, _ = O.video_codec_args(_job(codec="prores", container="mov", proresProfile="4444"), 1, 0)
    assert a[a.index("-profile:v") + 1] == "4" and "yuva444p10le" in a
    a, _, _, _ = O.video_codec_args(_job(codec="h265", maxCLL="1000", maxFALL="400"), 1, 0)
    assert "max-cll=1000,400" in a[a.index("-x265-params") + 1]
    a, _, _, _ = O.video_codec_args(_job(codec="gif", loopCount="3"), 1, 0)
    assert a[a.index("-loop") + 1] == "3"
    j = _job(colorSpace="rec2020")
    j.color = ("rec2020", "pq")
    tags, _ = O.color_args(j, True)
    assert tags[tags.index("-color_trc") + 1] == "smpte2084" and tags[tags.index("-colorspace") + 1] == "bt2020nc"


def test_codec_for_path():
    assert O._codec_for_path("a.webm")[:2] == ("vp9", "webm")
    assert O._codec_for_path("out/")[0] == "png-sequence"
    assert O._codec_for_path("f_%04d.jpg")[0] == "jpeg-sequence"
    assert O._codec_for_path("x.png")[2] == "x_%06d.png"
    assert O._codec_for_path("m.wav")[0] == "audio-only"


def test_codecs_registered():
    from scenerender.registry import CODECS
    for c in ("h264", "h265", "ffv1", "av1", "vp9", "prores", "dnxhr", "gif", "apng", "webp", "png-sequence",
              "jpeg-sequence", "exr-sequence", "tiff-sequence", "audio-only"):
        assert c in CODECS.entries


def test_coverage_report():
    from scenerender.coverage import report
    txt = report(DEMO, all_features=True)
    assert "audioEffect" in txt and "reverb" in txt
    assert "codec" in txt and "h264" in txt
    assert "toneMapping:reinhard" in txt
    lines = txt.splitlines()
    # unsupported first, then partial, then supported
    idx = [i for i, l in enumerate(lines) if l and not l.startswith(" ")]
    titles = [lines[i] for i in idx][1:]
    order = [t.split()[0] for t in titles]
    assert order == sorted(order, key=["NOT", "PARTIAL", "SUPPORTED"].index)
    assert np is not None
