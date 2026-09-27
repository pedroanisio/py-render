"""Spherical Video V2 mesh projection (mshp) for eac / fisheye-180: an independent decoder of the
bit-packed mesh checks structure, CRC, winding and that every mesh (u, v) samples the renderer's
layout_dirs direction; end to end the mesh lands in mp4 sv3d and in Matroska Projection."""
from __future__ import annotations

import math
import os
import re
import struct
import subprocess
import zlib
from types import SimpleNamespace

import numpy as np
import pytest

from scenerender import output as O
from scenerender import spherical_mesh as SM
from scenerender.cli import main
from scenerender.three.view360 import layout_dirs

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures", "output_delivery.xml")
CAM = SimpleNamespace(right=np.array([1.0, 0, 0]), up=np.array([0, 1.0, 0]), fwd=np.array([0, 0, -1.0]))


# ---------------------------------------------------------------- reference decoder
class Bits:
    def __init__(self, data: bytes, pos: int = 0):
        self.data, self.pos = data, pos * 8

    def read(self, n: int) -> int:
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def align(self) -> None:
        self.pos = (self.pos + 7) & ~7


def unzigzag(n: int) -> int:
    return n >> 1 if n % 2 == 0 else -((n + 1) >> 1)


def parse_mesh(body: bytes) -> tuple[np.ndarray, list[tuple[int, int, list[int]]]]:
    b = Bits(body)
    assert b.read(1) == 0
    nc = b.read(31)
    coords = [struct.unpack(">f", bytes(b.read(8) for _ in range(4)))[0] for _ in range(nc)]
    assert b.read(1) == 0
    nv = b.read(31)
    ccsb, idx, verts = math.ceil(math.log2(nc * 2)), [0] * 5, []
    for _ in range(nv):
        row = []
        for k in range(5):
            idx[k] += unzigzag(b.read(ccsb))
            assert 0 <= idx[k] < nc
            row.append(coords[idx[k]])
        verts.append(row)
    b.align()
    assert b.read(1) == 0
    lists, vcsb = [], math.ceil(math.log2(nv * 2))
    for _ in range(b.read(31)):
        tex, itype = b.read(8), b.read(8)
        assert b.read(1) == 0
        cur, ids = 0, []
        for _ in range(b.read(31)):
            cur += unzigzag(b.read(vcsb))
            assert 0 <= cur < nv
            ids.append(cur)
        b.align()
        lists.append((tex, itype, ids))
    assert b.pos // 8 == len(body)
    return np.array(verts), lists


def parse_mshp(payload: bytes) -> list:
    """mshp payload (after size/type) -> [(vertices, lists)] per mesh box."""
    assert payload[:4] == bytes(4)                                   # version 0, flags 0
    crc, enc, rest = int.from_bytes(payload[4:8], "big"), payload[8:12], payload[12:]
    assert crc == zlib.crc32(payload[8:])
    assert enc in (b"dfl8", b"raw ")
    raw = zlib.decompress(rest, -15) if enc == b"dfl8" else rest
    meshes = []
    for typ, s, h, e in O._boxes(raw, 0, len(raw)):
        assert typ == b"mesh"
        meshes.append(parse_mesh(raw[s + h:e]))
    return meshes


def triangles(lists) -> list[tuple[int, int, int]]:
    out = []
    for _, itype, ids in lists:
        if itype == 0:
            out += [tuple(ids[i:i + 3]) for i in range(0, len(ids), 3)]
        elif itype == 1:
            out += [(ids[i], ids[i + 1], ids[i + 2]) if i % 2 == 0 else (ids[i + 1], ids[i], ids[i + 2])
                    for i in range(len(ids) - 2)]
        else:
            out += [(ids[0], ids[i], ids[i + 1]) for i in range(1, len(ids) - 1)]
    return out


def angle_errors(verts, tris, layout, w, h) -> np.ndarray:
    """Angle (deg) between each triangle centroid / near-vertex point's mesh direction and the direction
    the renderer gives the pixel its (u, v) addresses in a w x h eye image."""
    D, valid = layout_dirs(layout, w, h, CAM)
    tri = verts[np.array(tris)]                                      # (t, 3, 5)
    cen = tri.mean(1)
    near = tri[:, 0] * 0.9 + cen * 0.1                               # each vertex, nudged into its triangle
    pts = np.concatenate([cen, near])
    px = np.clip((pts[:, 3] * w).astype(int), 0, w - 1)
    py = np.clip(((1 - pts[:, 4]) * h).astype(int), 0, h - 1)
    if valid is not None:
        assert valid[py, px].all()
    d = D[py, px]
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    m = pts[:, :3] / np.linalg.norm(pts[:, :3], axis=1, keepdims=True)
    return np.degrees(np.arccos(np.clip((d * m).sum(1), -1, 1)))


# ---------------------------------------------------------------- mesh structure and geometry
@pytest.mark.parametrize("layout,eye,size", [("eac", (96, 64), (1536, 1024)), ("fisheye-180", (128, 64), (2048, 1024)),
                                             ("fisheye-180", (64, 96), (1024, 1536))])
def test_mesh_decodes_and_matches_layout(layout, eye, size):
    payload = SM.layout_mshp(layout, *eye)
    (verts, lists), = parse_mshp(payload)
    ref, ref_lists = SM.layout_mesh(layout, *eye)
    assert len(verts) == len(ref) and np.array_equal(verts.astype(np.float32), ref)
    assert [ids for _, _, ids in lists] == [ids for _, ids in ref_lists]
    assert all(tex == 0 for tex, _, _ in lists)
    if layout == "eac":
        assert 6 * 17 * 17 - 80 < len(verts) <= 6 * 17 * 17 and len(lists) == 6 * 16 and {t for _, t, _ in lists} == {1}
    else:
        assert len(verts) == 1 + 16 * 64 and lists[0][1] == 2 and {t for _, t, _ in lists[1:]} == {1}
    assert len(verts) <= 32000 and max(len(i) for *_, i in lists) <= 128000   # common player limits
    assert 0.0 <= verts[:, 3:].min() and verts[:, 3:].max() <= 1.0
    tris = triangles(lists)
    used = {i for t in tris for i in t}
    assert used == set(range(len(verts)))
    p = verts[:, :3][np.array(tris)]
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    assert ((n * p.mean(1)).sum(1) < 0).all()                        # CCW seen from the centre, none degenerate
    # quads/sectors cover the whole sphere (eac) or the front hemisphere (fisheye): solid angle
    a, b, c = (p[:, k] / np.linalg.norm(p[:, k], axis=1, keepdims=True) for k in range(3))
    omega = 2 * np.arctan2(np.abs((a * np.cross(b, c)).sum(1)), 1 + (a * b).sum(1) + (b * c).sum(1) + (c * a).sum(1))
    assert omega.sum() == pytest.approx(4 * math.pi if layout == "eac" else 2 * math.pi, rel=0.002)
    err = angle_errors(verts, tris, layout, *size)
    assert err.max() < 1.0, err.max()


def test_front_and_up_orientation():
    """Front (-Z) is the EAC centre cell's centre and the fisheye centre; +Y is the up cell (bottom right)."""
    v, _ = SM.eac_mesh()
    ctr = v[np.argmin(np.hypot(v[:, 3] - 0.5, v[:, 4] - 0.75))]
    assert np.allclose(ctr[:3], [0, 0, -1], atol=1e-6)
    up = v[np.argmin(np.hypot(v[:, 3] - 5 / 6, v[:, 4] - 0.25))]
    assert np.allclose(up[:3], [0, 1, 0], atol=1e-6)
    f, _ = SM.fisheye_mesh(100, 100)
    assert np.allclose(f[0], [0, 0, -1, 0.5, 0.5], atol=1e-6)
    right = f[np.argmin(np.hypot(f[:, 3] - 1.0, f[:, 4] - 0.5))]
    assert np.allclose(right[:3], [1, 0, 0], atol=1e-6)


def test_raw_encoding_two_meshes_and_bit_widths():
    m = SM.fisheye_mesh(64, 64, rings=2, sectors=4)
    payload = SM.mshp_payload([m, SM.eac_mesh(2)], b"raw ")
    assert payload[8:12] == b"raw "
    (v1, l1), (v2, l2) = parse_mshp(payload)
    assert len(v1) == 9 and len(v2) == len(SM.eac_mesh(2)[0]) and triangles(l2)
    with pytest.raises(ValueError):
        SM.mshp_payload([m], b"lzma")
    with pytest.raises(ValueError):
        SM.layout_mesh("cubemap", 8, 8)


def test_spherical_boxes_carry_mshp():
    st3d, sv3d = O.spherical_boxes("fisheye-180", "left-right", eye=(128, 64))
    assert st3d[-1] == 2
    proj = next(sv3d[s + h:e] for t, s, h, e in O._boxes(sv3d, 8, len(sv3d)) if t == b"proj")
    kids = {t: proj[s + h:e] for t, s, h, e in O._boxes(proj, 0, len(proj))}
    assert kids[b"prhd"] == bytes(16) and kids[b"mshp"] == SM.layout_mshp("fisheye-180", 128, 64)
    assert b"equi" not in kids and b"cbmp" not in kids


# ---------------------------------------------------------------- end to end
def _ebml_check(path: str) -> None:
    """Every CRC-32 element verifies; SeekHead and Cues positions land on the elements they name / Clusters."""
    data = open(path, "rb").read()
    seg = next(x for x in O._ebml_elems(data, 0, len(data)) if x[0] == 0x18538067)
    base = seg[1] + seg[2]
    starts = {s - base: cid for cid, s, h, e in O._ebml_elems(data, base, seg[3])}
    assert base + sum(e - s for _, s, _, e in O._ebml_elems(data, base, seg[3])) == seg[3] == len(data)

    def walk(s, e):
        kids = list(O._ebml_elems(data, s, e))
        if kids and kids[0][0] == 0xBF:
            assert data[kids[0][1] + kids[0][2]:kids[0][3]] == zlib.crc32(data[kids[0][3]:e]).to_bytes(4, "little")
        for cid, cs, ch, ce in kids:
            if cid == 0x4DBB:
                f = {k: data[ks + kh:ke] for k, ks, kh, ke in O._ebml_elems(data, cs + ch, ce)}
                assert starts[int.from_bytes(f[0x53AC], "big")] == int.from_bytes(f[0x53AB], "big")
            elif cid == 0xF1:
                assert starts[int.from_bytes(data[cs + ch:ce], "big")] == 0x1F43B675
            elif cid in O._EBML_MASTERS:
                walk(cs + ch, ce)
    walk(base, seg[3])


def _decode_ok(path: str) -> None:
    err = subprocess.run([O.ffmpeg_exe(), "-v", "error", "-i", path, "-f", "null", "-"], capture_output=True, text=True)
    lines = [ln for ln in err.stderr.splitlines() if "Unknown projection type: mshp" not in ln]  # ffmpeg has no mesh support
    assert err.returncode == 0 and not lines, err.stderr[:500]


@pytest.fixture(scope="module")
def mesh_renders(tmp_path_factory):
    d = tmp_path_factory.mktemp("mesh")
    src = open(FIX).read()
    outs = ('<output id="eac-mp4" path="eac.mp4" codec="h264" preset="ultrafast"/>\n'
            '  <output id="eac-mkv" path="eac.mkv" codec="h264" preset="ultrafast"/>\n'
            '  <output id="eac-webm" path="eac.webm" codec="vp9" preset="ultrafast"/>\n  ')
    src = re.sub(r"<output .*?</output>\n  ", "", src, flags=re.S)
    src = re.sub(r"<output [^>]*/>\n  ", "", src)
    eac = src.replace("<scene360", outs + "<scene360").replace('layout="equirectangular" stereo="top-bottom" width="128" height="64"',
                                                             'layout="eac" stereo="top-bottom" width="96" height="128"')
    fish = src.replace("<scene360", '<output id="fish" path="fish.mp4" codec="h264" preset="ultrafast"/>\n  '
                       '<output id="fish-mkv" path="fish.mkv" codec="h264" preset="ultrafast"/>\n  <scene360')
    fish = fish.replace('layout="equirectangular" stereo="top-bottom" width="128" height="64"',
                        'layout="fisheye-180" stereo="left-right" width="128" height="48"')
    cwd = os.getcwd()
    os.chdir(d)
    try:
        for name, text in (("eac.xml", eac), ("fish.xml", fish)):
            (d / name).write_text(text)
            assert main(["render", str(d / name), "--jobs", "1"]) == 0
    finally:
        os.chdir(cwd)
    return d


def test_mp4_mesh_projection(mesh_renders):
    for f, layout, stereo, eye in (("eac.mp4", "eac", 1, (96, 64)), ("fish.mp4", "fisheye-180", 2, (64, 48))):
        p = str(mesh_renders / f)
        boxes = O.read_sample_entry_boxes(p)
        assert boxes[b"st3d"] == bytes([0, 0, 0, 0, stereo])
        sv3d = boxes[b"sv3d"]
        proj = next(sv3d[s + h:e] for t, s, h, e in O._boxes(sv3d, 0, len(sv3d)) if t == b"proj")
        kids = {t: proj[s + h:e] for t, s, h, e in O._boxes(proj, 0, len(proj))}
        assert kids[b"mshp"] == SM.layout_mshp(layout, *eye)
        (verts, lists), = parse_mshp(kids[b"mshp"])
        assert len(verts) == len(SM.layout_mesh(layout, *eye)[0])
        _decode_ok(p)


def test_matroska_mesh_projection(mesh_renders):
    for f, layout, eye, stereo in (("eac.mkv", "eac", (96, 64), "top and bottom"), ("eac.webm", "eac", (96, 64), "top and bottom"),
                                   ("fish.mkv", "fisheye-180", (64, 48), "side by side")):
        p = str(mesh_renders / f)
        pr = O.read_matroska_projection(p)
        assert int.from_bytes(pr[0x7671], "big") == 3
        assert pr[0x7672] == SM.layout_mshp(layout, *eye)
        parse_mshp(pr[0x7672])
        info = subprocess.run([O.ffmpeg_exe(), "-hide_banner", "-i", p], capture_output=True, text=True).stderr
        assert stereo in info
        _ebml_check(p)
        _decode_ok(p)


def test_matroska_injection_is_idempotent(tmp_path):
    p = str(tmp_path / "a.mkv")
    subprocess.run([O.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=96x48:d=2:r=10", "-f", "lavfi",
                    "-i", "sine=d=2", "-c:v", "libx264", "-c:a", "libopus", p], check=True)
    O.inject_matroska_projection(p, 3, SM.layout_mshp("eac", 96, 48))
    once = open(p, "rb").read()
    O.inject_matroska_projection(p, 3, SM.layout_mshp("eac", 96, 48))
    assert open(p, "rb").read() == once
    _ebml_check(p)
    _decode_ok(p)
    O.inject_matroska_projection(p, 2, bytes(12))                    # replaced, not duplicated
    assert O.read_matroska_projection(p) == {0x7671: b"\x02", 0x7672: bytes(12)}
    _ebml_check(p)
