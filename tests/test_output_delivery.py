"""Delivery to FULL: 16-bit TIFF, GIF palettes/alpha, maxFileSize loop, HDR and spherical metadata (MP4 box
injection, Matroska through side data), and --publish destinations."""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import subprocess
import threading

import numpy as np
import pytest

from scenerender import output as O
from scenerender.cli import main

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures", "output_delivery.xml")
MD = "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50)"


def ff() -> str:
    return O.ffmpeg_exe()


def probe(path: str) -> str:
    return subprocess.run([ff(), "-hide_banner", "-i", path], capture_output=True, text=True).stderr


def frames_of(path: str) -> int:
    err = subprocess.run([ff(), "-v", "error", "-i", path, "-f", "null", "-"], capture_output=True, text=True)
    assert err.returncode == 0 and not err.stderr.strip(), err.stderr[:500]
    out = subprocess.run([ff(), "-v", "error", "-i", path, "-map", "0:v:0", "-f", "framemd5", "-"], capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if line and not line.startswith("#"))


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    d = tmp_path_factory.mktemp("delivery")
    cwd = os.getcwd()
    os.chdir(d)
    try:
        assert main(["render", FIX, "--jobs", "1"]) == 0
    finally:
        os.chdir(cwd)
    return d


# ---------------------------------------------------------------- codecs
def test_tiff_sequence_is_16_bit(rendered):
    files = sorted(os.listdir(rendered / "tiff"))
    assert len(files) == 10 and files[0] == "f_000000.tif"
    assert "rgba64le" in probe(str(rendered / "tiff" / files[0]))
    raw = subprocess.run([ff(), "-v", "error", "-i", str(rendered / "tiff" / files[0]), "-f", "rawvideo", "-pix_fmt", "rgba64le", "-"],
                         capture_output=True, check=True).stdout
    px = np.frombuffer(raw, "<u2").reshape(64, 128, 4)
    assert np.unique(px[..., 2]).size > 64 or (px[..., :3] % 257).any()   # not 8-bit values scaled up
    assert px[..., 3].min() < 65535                                        # alpha kept


def test_gif_alpha_and_palette_choice(rendered):
    info = probe(str(rendered / "a.gif"))
    assert "gif" in info
    raw = subprocess.run([ff(), "-v", "error", "-i", str(rendered / "a.gif"), "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, 64, 128, 4)[..., 3]
    assert set(np.unique(a)) <= {0, 255} and (a == 0).any() and (a == 255).any()


def test_gif_alpha_dither_keeps_mean_coverage():
    for alpha in (0, 32, 100, 128, 200, 254, 255):
        img = np.zeros((64, 64, 4), np.uint8)
        img[..., :3] = 200
        img[..., 3] = alpha
        out = O.gif_alpha(img)
        assert set(np.unique(out[..., 3])) <= {0, 255}
        assert out[..., 3].mean() / 255 == pytest.approx(alpha / 255, abs=1 / 64 + 1e-6)
        assert (out[out[..., 3] == 0][:, :3] == 0).all()


def test_gif_palette_mode():
    a, b = np.zeros(4096, bool), np.zeros(4096, bool)
    a[:200], b[200:400] = True, True                   # two frames with disjoint 200-colour sets
    assert O.gif_palette_mode([a, b]) == "frame"
    c = a.copy()
    c[:210] = True
    assert O.gif_palette_mode([a, c]) == "global"
    assert O.gif_palette_mode([a]) == "global"


# ---------------------------------------------------------------- maxFileSize
def test_fit_to_size_converges():
    calls = []

    def encode(v):                     # an encoder that overshoots its target bitrate by 35 % plus a fixed header
        calls.append(v)
        return int(v * 10 / 8 * 1.35 + 900 + 32000 * 10 / 8)
    cap = 200_000
    size, v = O.fit_to_size(encode, cap, 150_000, 32000 * 10)
    assert size <= cap and len(calls) <= 5 and calls == sorted(calls, reverse=True)
    with pytest.raises(RuntimeError):
        O.fit_to_size(lambda v: 10 ** 9, 1000, 100_000, 0)


def test_fit_quality_bisects():
    size, q = O.fit_quality(lambda q: 100 * q, 5000, 90)
    assert q == 50 and size == 5000


def test_max_file_size_output(rendered):
    size = os.path.getsize(rendered / "capped.mp4")
    assert size <= 12000
    assert frames_of(str(rendered / "capped.mp4")) == 10


def test_max_file_size_loop_end_to_end(tmp_path, monkeypatch):
    """A cap the first encode overshoots (tiny budget, noisy frames) is met by re-encoding."""
    src = open(FIX).read().replace('maxFileSize="12000"', 'maxFileSize="6000" bitrate="4000000"')
    p = tmp_path / "cap.xml"
    p.write_text(src)
    seen = []
    real = O.fit_to_size

    def spy(encode, cap, v0, fixed, **kw):
        def wrapped(v):
            s = encode(v)
            seen.append((v, s))
            return s
        return real(wrapped, cap, v0, fixed, **kw)
    monkeypatch.setattr(O, "fit_to_size", spy)
    monkeypatch.chdir(tmp_path)
    assert main(["render", str(p), "--jobs", "1", "--output", "capped"]) == 0
    assert os.path.getsize("capped.mp4") <= 6000 and seen and seen[-1][1] <= 6000


# ---------------------------------------------------------------- HDR and spherical metadata
def test_hdr_metadata_hevc_mp4_and_vp9_webm(rendered):
    for f in ("hevc.mp4", "vp9.webm"):
        info = probe(str(rendered / f))
        assert "Mastering Display Metadata" in info and "max_luminance=1000.000000" in info, f
        assert "MaxCLL=1000, MaxFALL=400" in info, f
        assert frames_of(str(rendered / f)) == 10
    assert "smpte2084" in probe(str(rendered / "hevc.mp4")) and "arib-std-b67" in probe(str(rendered / "vp9.webm"))


def test_spherical_metadata_mp4_and_mkv(rendered):
    boxes = O.read_sample_entry_boxes(str(rendered / "hevc.mp4"))
    assert boxes[b"st3d"] == bytes([0, 0, 0, 0, 1])                      # version/flags + top-bottom
    sv3d = boxes[b"sv3d"]
    kids = {t: sv3d[s + h:e] for t, s, h, e in O._boxes(sv3d, 0, len(sv3d))}
    assert kids[b"svhd"][4:] == b"scenerender\0"
    proj = kids[b"proj"]
    pk = {t: proj[s + h:e] for t, s, h, e in O._boxes(proj, 0, len(proj))}
    assert pk[b"prhd"] == bytes(16) and pk[b"equi"] == bytes(20)
    mkv = probe(str(rendered / "sph.mkv"))
    assert "spherical: equirectangular" in mkv and "stereo3d: top and bottom" in mkv
    assert "spherical" not in probe(str(rendered / "pub.mp4"))           # sphericalMetadata="false"


def test_injector_faststart_offsets_and_cubemap(tmp_path):
    p = str(tmp_path / "a.mp4")
    subprocess.run([ff(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=96x48:d=1:r=10", "-f", "lavfi", "-i", "sine=d=1",
                    "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", p], check=True)
    O.inject_sample_entry_boxes(p, O.spherical_boxes("cubemap", "left-right") + O.hdr_boxes(MD, 800, 200))
    assert frames_of(p) == 10
    info = probe(p)
    assert "spherical: cubemap" in info and "side by side" in info and "MaxCLL=800, MaxFALL=200" in info
    # re-injecting replaces boxes of the same type instead of duplicating them
    O.inject_sample_entry_boxes(p, O.spherical_boxes("equirectangular", "mono"))
    boxes = O.read_sample_entry_boxes(p)
    assert boxes[b"st3d"][-1] == 0 and b"mdcv" in boxes and frames_of(p) == 10


def test_chunk_offsets_promote_to_co64():
    stco = O.box(b"stco", (1).to_bytes(4, "big") + (2 ** 32 - 10).to_bytes(4, "big"), (0, 0))
    moov = O.box(b"moov", O.box(b"trak", O.box(b"mdia", O.box(b"minf", O.box(b"stbl", stco)))))
    out = O._shift_chunk_offsets(moov, 100)
    assert b"co64" in out and (2 ** 32 + 90).to_bytes(8, "big") in out


def test_parse_master_display():
    m = O.parse_master_display(MD)
    assert m["G"] == (13250, 34500) and m["L"] == (10000000, 50)
    with pytest.raises(ValueError):
        O.parse_master_display("G(1,2)")
    job = O.Job(name="x", path="x.mp4", codec="h265", attrs={"masteringDisplay": MD})
    assert O.hdr_target(job) == {"peak": 1000.0, "black": 0.005}


def test_prores_mov_and_dnxhr_mxf_hdr(tmp_path, monkeypatch):
    src = open(FIX).read()
    src = src.replace('<project width="128" height="64"', '<project width="256" height="128"')
    extra = (f'<output id="pr" path="pr.mov" codec="prores" maxCLL="600" maxFALL="100" masteringDisplay="{MD}"/>\n'
             f'  <output id="dn" path="dn.mxf" codec="dnxhr" container="mxf" fps="25" maxCLL="600" masteringDisplay="{MD}"/>\n  ')
    src = re.sub(r"<output .*?</output>\n  ", "", src, flags=re.S)
    src = re.sub(r"<output [^>]*/>\n  ", "", src)
    src = src.replace("<scene360", extra + "<scene360")
    p = tmp_path / "pro.xml"
    p.write_text(src)
    monkeypatch.chdir(tmp_path)
    assert main(["render", str(p), "--jobs", "1"]) == 0
    mov = probe("pr.mov")
    assert "Mastering Display Metadata" in mov and "MaxCLL=600" in mov and "spherical: equirectangular" in mov
    assert "Mastering Display Metadata" in probe("dn.mxf")
    assert frames_of("dn.mxf") == 25


# ---------------------------------------------------------------- destinations
class _Sink(http.server.BaseHTTPRequestHandler):
    got: list = []

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n)

    def do_PUT(self):  # noqa: N802
        _Sink.got.append(("PUT", self.path, dict(self.headers), len(self._read())))
        self.send_response(201)
        self.end_headers()

    def do_POST(self):  # noqa: N802
        _Sink.got.append(("POST", self.path, dict(self.headers), json.loads(self._read())))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def sink():
    _Sink.got = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Sink)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv.server_address[1]
    srv.shutdown()


def test_publish_only_with_flag(tmp_path, monkeypatch, sink):
    p = tmp_path / "pub.xml"
    p.write_text(open(FIX).read().replace("127.0.0.1:65000", f"127.0.0.1:{sink}"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCENERENDER_QA_TEST_HTTP_BEARER_TOKEN", "s3cret-token")
    assert main(["render", str(p), "--jobs", "1", "--output", "published"]) == 0
    assert _Sink.got == []                                                 # never uploads by default
    assert main(["render", str(p), "--jobs", "1", "--output", "published", "--publish"]) == 0
    puts = [g for g in _Sink.got if g[0] == "PUT"]
    posts = [g for g in _Sink.got if g[0] == "POST"]
    assert [g[1] for g in puts] == ["/up/pub.mp4"] and puts[0][3] == os.path.getsize("pub.mp4")
    assert puts[0][2].get("Authorization") == "Bearer s3cret-token"
    body = posts[0][3]
    assert body["event"] == "render.complete" and body["job"] == "published"
    assert body["files"][0]["name"] == "pub.mp4" and body["metadata"]["codec"] == "h264"


def test_publish_failure_fails_the_output(tmp_path, monkeypatch, capsys):
    p = tmp_path / "pub.xml"
    p.write_text(open(FIX).read().replace("127.0.0.1:65000", "127.0.0.1:9"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("scenerender.publish._sleep", lambda s: None)
    monkeypatch.setenv("SCENERENDER_QA_TEST_HTTP_BEARER_TOKEN", "s3cret-token")
    assert main(["render", str(p), "--jobs", "1", "--output", "published", "--publish"]) == 1
    err = capsys.readouterr().err
    assert "destination failed" in err and "s3cret" not in err
    assert shutil.which is not None


# ---------------------------------------------------------------- ambisonic audio
def test_ambisonic_mix_to_mka_and_webm(tmp_path, monkeypatch):
    from test_audio import make_audio_media
    make_audio_media()
    amb = os.path.join(HERE, "fixtures", "audio_ambisonic.xml")
    monkeypatch.chdir(tmp_path)
    for f in ("a.mka", "a.webm"):
        assert main(["render", amb, "--scale", "0.25", "--jobs", "1", "--to", "1", "-o", f]) == 0
        info = probe(f)
        assert re.search(r"Audio: opus, 48000 Hz, ambisonic 1", info), info
