"""MXF metadata updates retain the essence and correct every absolute offset."""
import hashlib
import struct
import subprocess

import pytest

from scenerender import mxf
from scenerender.output import ffmpeg_exe


def test_content_light_roundtrip_preserves_frames_and_other_level(tmp_path):
    path = tmp_path / "video.mxf"
    ff = ffmpeg_exe()
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=256x128:rate=25",
                    "-frames:v", "3", "-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p",
                    str(path)], check=True)

    def digest():
        raw = subprocess.run([ff, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv422p", "-"],
                             check=True, capture_output=True).stdout
        return hashlib.sha256(raw).hexdigest()

    before = digest()
    mxf.inject_content_light(path, 600, 100)
    assert "MaxCLL=600, MaxFALL=100" in subprocess.run(
        [ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    size = path.stat().st_size
    mxf.inject_content_light(path, 800)
    assert "MaxCLL=800, MaxFALL=100" in subprocess.run(
        [ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    assert path.stat().st_size == size and digest() == before
    mxf.inject_content_light(path, 0, 0)
    assert "MaxCLL=0, MaxFALL=0" in subprocess.run(
        [ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr


def test_header_growth_relocates_partitions_and_random_index(tmp_path):
    # A tiny legal KAG with no filler forces growth. The opaque essence payload
    # includes UL-like bytes: the writer must skip it using its BER length.
    def klv(key, data):
        return key + b"\x83" + len(data).to_bytes(3, "big") + data

    def partition(kind, this, previous, footer, header):
        data = struct.pack(">HHIQQQQQI QI", 1, 3, 1, this, previous, footer, header, 0, 0, 0, 1)
        return klv(mxf.PARTITION + bytes([kind, 4, 0]), data + bytes(24))

    primer = klv(mxf.PRIMER, struct.pack(">II", 0, 18))
    descriptor = klv(mxf.PICTURE + b"\x28\x00", struct.pack(">HHI", 0x3203, 4, 256))
    metadata = primer + descriptor
    plen = len(partition(2, 0, 0, 0, 0))
    essence = klv(bytes.fromhex("060e2b34010201010d01030115010800"), b"unchanged essence" + mxf.PRIMER)
    body_pos = plen + len(metadata)
    footer_pos = body_pos + plen + len(essence)
    rip_data = b"".join(struct.pack(">IQ", 1, p) for p in (0, body_pos, footer_pos))
    rip = klv(mxf.RIP, rip_data + struct.pack(">I", 20 + len(rip_data) + 4))
    path = tmp_path / "tight.mxf"
    path.write_bytes(partition(2, 0, 0, footer_pos, len(metadata)) + metadata +
                     partition(3, body_pos, 0, footer_pos, 0) + essence +
                     partition(4, footer_pos, body_pos, footer_pos, 0) + rip)
    old_size = path.stat().st_size
    mxf.inject_content_light(path, 600, 100)
    data = path.read_bytes()
    growth = len(data) - old_size
    assert growth > 0 and essence in data
    assert int.from_bytes(data[20 + 32:20 + 40], "big") == len(metadata) + growth
    for pos, previous in ((body_pos + growth, 0), (footer_pos + growth, body_pos + growth)):
        assert data[pos:pos + 13] == mxf.PARTITION
        assert struct.unpack_from(">QQQ", data, pos + 28) == (pos, previous, footer_pos + growth)
    ri = data.index(mxf.RIP) + 20
    assert [struct.unpack_from(">Q", data, ri + i * 12 + 4)[0] for i in range(3)] == [0, body_pos + growth, footer_pos + growth]


def test_bad_metadata_leaves_original_file_intact(tmp_path):
    path = tmp_path / "bad.mxf"
    raw = mxf.PRIMER + b"\x83\xff\xff\xff"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="exceeds"):
        mxf.inject_content_light(path, 600, 100)
    assert path.read_bytes() == raw
    with pytest.raises(ValueError, match="16-bit"):
        mxf.inject_content_light(path, 65536)
    assert path.read_bytes() == raw
