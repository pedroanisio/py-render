"""Spherical Video V2 mesh projection (google/spatial-media docs/spherical-video-v2-rfc.md, `mshp`).

Meshes for the panorama layouts that have no dedicated projection box, built from the same
parametrisation as scenerender.three.view360.layout_dirs so every mesh vertex's (u, v) samples the
pixel that the renderer filled from that vertex's direction:

  * eac: the 3 x 2 cell arrangement (top row left, front, right; bottom row down, back, up), each
    cell a GRID x GRID quad grid on the unit cube with equi-angular spacing (cube coordinate =
    tan(pi/4 * cell coordinate)); one triangle strip per quad row.
  * fisheye-180: equidistant fisheye (angle from front proportional to the radius) of the front
    hemisphere, the circle inscribed in the eye image (it depends on the eye's aspect ratio):
    RINGS rings x SECTORS sectors on the unit sphere, a fan around the centre and one strip per ring.

Pinned decisions:
  * Positions use the RFC's OpenGL frame: +X = viewer right, +Y = up, -Z = front (a view direction
    xR + yU + zF of the renderer maps to (x, y, -z)); (u, v) are per-eye texture coordinates with
    the origin at the lower left. Stereo frames get one mesh, the frame packing (top-bottom /
    left-right) is carried by st3d / StereoMode as the RFC prescribes for a single mesh box.
  * Triangles wind counter-clockwise seen from the viewer at the centre (normals face inward).
  * crc is the CRC-32 of every byte after the crc field (encoding four_cc + the encoded mesh
    boxes), as the RFC's wording says; encoding is 'dfl8' (raw deflate, RFC 1951).
  * Mesh sizes stay inside common player limits (ExoPlayer's ProjectionDecoder: <= 10000 coordinates,
    <= 32000 vertices, <= 128000 indices per list).
"""
from __future__ import annotations

import math
import struct
import zlib

import numpy as np

GRID = 16          # eac quads per cell side
RINGS = 16         # fisheye rings (5.6 degrees each)
SECTORS = 64       # fisheye sectors
INDEX_TRIANGLES, INDEX_STRIP, INDEX_FAN = 0, 1, 2

Mesh = tuple[np.ndarray, list[tuple[int, list[int]]]]     # (vertices (n, 5) x y z u v, [(index_type, indices)])

# renderer camera basis in mesh coordinates: right, up, forward
_R, _U, _F = np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, -1.0])


class _Verts:
    """Deduplicating vertex pool."""

    def __init__(self):
        self.rows: list[tuple[float, ...]] = []
        self.ids: dict[tuple[float, ...], int] = {}

    def add(self, pos, u: float, v: float) -> int:
        key = tuple(float(np.float32(c)) for c in (*pos, u, v))
        if key not in self.ids:
            self.ids[key] = len(self.rows)
            self.rows.append(key)
        return self.ids[key]


def _inward(V: _Verts, a: int, b: int, c: int) -> bool:
    p = np.array([V.rows[i][:3] for i in (a, b, c)])
    return float(np.cross(p[1] - p[0], p[2] - p[0]) @ (p[0] + p[1] + p[2])) < 0


def _strip(V: _Verts, top: list[int], bot: list[int]) -> list[int]:
    """Triangle strip zig-zagging between two vertex rows, ordered so the first triangle winds CCW from inside."""
    rows = (top, bot) if _inward(V, top[0], bot[0], top[1]) else (bot, top)
    return [i for pair in zip(*rows) for i in pair]


def eac_mesh(n: int = GRID) -> Mesh:
    V = _Verts()
    lists = []
    t = [math.tan(math.pi / 4 * (-1 + 2 * i / n)) for i in range(n + 1)]
    t[0], t[-1] = -1.0, 1.0
    for row in range(2):
        for col in range(3):
            al = (col - 1) * math.pi / 2
            ca, sa = round(math.cos(al)), round(math.sin(al))
            if row == 0:        # same frames as layout_dirs: centre, image-right, image-down
                c, h, dn = _F * ca + _R * sa, -_F * sa + _R * ca, -_U
            else:
                c, h, dn = -_F * ca + _U * sa, _F * sa + _U * ca, _R
            grid = [[V.add(c + h * t[i] + dn * t[j], (col + i / n) / 3, 1 - (row + j / n) / 2)
                     for i in range(n + 1)] for j in range(n + 1)]
            lists += [(INDEX_STRIP, _strip(V, grid[j], grid[j + 1])) for j in range(n)]
    return np.array(V.rows, np.float32), lists


def fisheye_mesh(ew: int, eh: int, rings: int = RINGS, sectors: int = SECTORS) -> Mesh:
    V = _Verts()
    rad = min(ew, eh) / 2
    su, sv = rad / ew, rad / eh

    def vert(r: float, phi: float) -> int:
        x, y = r * math.cos(phi), r * math.sin(phi)            # image right / down, unit = circle radius
        th = r * math.pi / 2
        sn = math.sin(th) / r if r > 0 else 0.0
        d = math.cos(th) * _F + x * sn * _R - y * sn * _U
        return V.add(d, 0.5 + x * su, 0.5 - y * sv)

    ring = [[vert(k / rings, 2 * math.pi * (j % sectors) / sectors) for j in range(sectors + 1)]
            for k in range(rings + 1)]
    fan = ring[1] if _inward(V, ring[0][0], ring[1][0], ring[1][1]) else ring[1][::-1]
    lists = [(INDEX_FAN, [ring[0][0]] + fan)]
    lists += [(INDEX_STRIP, _strip(V, ring[k], ring[k + 1])) for k in range(1, rings)]
    return np.array(V.rows, np.float32), lists


def layout_mesh(layout: str, ew: int, eh: int) -> Mesh:
    if layout == "eac":
        return eac_mesh()
    if layout == "fisheye-180":
        return fisheye_mesh(ew, eh)
    raise ValueError(f"no mesh for layout {layout!r}")


class _Bits:
    """MSB-first bit writer."""

    def __init__(self):
        self.acc, self.n, self.out = 0, 0, bytearray()

    def put(self, value: int, bits: int) -> None:
        self.acc, self.n = (self.acc << bits) | value, self.n + bits
        while self.n >= 8:
            self.n -= 8
            self.out.append((self.acc >> self.n) & 0xFF)
        self.acc &= (1 << self.n) - 1

    def align(self) -> None:
        if self.n:
            self.put(0, 8 - self.n)


def _zigzag(d: int) -> int:
    return d * 2 if d >= 0 else -d * 2 - 1


def _bits_for(count: int) -> int:
    return max(1, math.ceil(math.log2(count * 2)))      # ccsb / vcsb (count 1 -> 1 bit; zig-zag 0 fits anyway)


def mesh_box(mesh: Mesh) -> bytes:
    """One `mesh` box: coordinate pool, zig-zag delta-coded vertex indices, vertex lists."""
    verts, lists = mesh
    coords = sorted(set(verts.ravel().tolist()))
    where = {c: i for i, c in enumerate(coords)}
    b = _Bits()
    b.put(len(coords), 32)
    for c in coords:
        b.out += struct.pack(">f", c)
    b.put(len(verts), 32)
    ccsb, prev = _bits_for(len(coords)), [0] * 5
    for row in verts.tolist():
        for k, c in enumerate(row):
            i = where[c]
            b.put(_zigzag(i - prev[k]), ccsb)
            prev[k] = i
    b.align()
    b.put(len(lists), 32)
    vcsb = _bits_for(len(verts))
    for itype, idx in lists:
        b.put(0, 8)                 # texture_id 0: this track's video frames
        b.put(itype, 8)
        b.put(len(idx), 32)
        last = 0
        for i in idx:
            b.put(_zigzag(i - last), vcsb)
            last = i
        b.align()
    return (8 + len(b.out)).to_bytes(4, "big") + b"mesh" + bytes(b.out)


def mshp_payload(meshes: list[Mesh], encoding: bytes = b"dfl8") -> bytes:
    """MeshProjection box body (FullBox version/flags, crc, encoding, meshes): the mshp payload after its
    size/type header, which is also Matroska's ProjectionPrivate for ProjectionType 3."""
    body = b"".join(mesh_box(m) for m in meshes)
    if encoding == b"dfl8":
        z = zlib.compressobj(9, zlib.DEFLATED, -15)
        body = z.compress(body) + z.flush()
    elif encoding != b"raw ":
        raise ValueError(f"mshp encoding {encoding!r}")
    tail = encoding + body
    return bytes(4) + zlib.crc32(tail).to_bytes(4, "big") + tail


def layout_mshp(layout: str, ew: int, eh: int) -> bytes:
    """mshp payload of one per-eye mesh for a view360 layout with ew x eh eye images."""
    return mshp_payload([layout_mesh(layout, ew, eh)])


__all__ = ["eac_mesh", "fisheye_mesh", "layout_mesh", "mesh_box", "mshp_payload", "layout_mshp"]
