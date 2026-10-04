"""Tiny binary FBX writer used to build the tracking test fixtures.

Written from the published FBX binary layout (independently of scenerender.tracking's reader):
  header  "Kaydara FBX Binary  \\0" 0x1A 0x00, uint32 version
  record  EndOffset, NumProperties, PropertyListLen (uint32 < 7500, uint64 >= 7500),
          uint8 NameLen, Name, properties, nested records, null record (if nested)
  props   Y C I F D L scalars, S R length-prefixed, f d l i b arrays (length, encoding, byte len)

`python make_fbx.py` regenerates sample_7400.fbx (the committed fixture). Tests also call
`scene_fbx(7500, compress=True)` to cover 64-bit offsets and zlib-encoded arrays.
"""
from __future__ import annotations

import struct
import sys
import zlib

TICKS = 46186158000
MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"


def _prop(p, compress: bool) -> bytes:
    code, v = p
    if code in "YCIFDL":
        # Legacy Takes store bare interpolation letters in byte properties.
        fmt = {"Y": "<h", "C": "<B", "I": "<i", "F": "<f", "D": "<d", "L": "<q"}[code]
        return code.encode() + struct.pack(fmt, v)
    if code in "SR":
        raw = v.encode("utf-8") if isinstance(v, str) else v
        return code.encode() + struct.pack("<I", len(raw)) + raw
    fmt = {"f": "<f", "d": "<d", "l": "<q", "i": "<i", "b": "<B"}[code]
    raw = b"".join(struct.pack(fmt, x) for x in v)
    enc = 0
    if compress:
        raw, enc = zlib.compress(raw), 1
    return code.encode() + struct.pack("<III", len(v), enc, len(raw)) + raw


def _node(n, offset: int, version: int, compress: bool) -> bytes:
    name, props, children = n
    wide = version >= 7500
    head = 25 if wide else 13
    pdata = b"".join(_prop(p, compress) for p in props)
    body_start = offset + head + len(name) + len(pdata)
    kids = b""
    for c in children:
        kids += _node(c, body_start + len(kids), version, compress)
    if children:
        kids += b"\x00" * head
    end = body_start + len(kids)
    rec = struct.pack("<QQQ" if wide else "<III", end, len(props), len(pdata))
    return rec + bytes([len(name)]) + name.encode() + pdata + kids


def write_fbx(nodes, version: int = 7400, compress: bool = False) -> bytes:
    out = MAGIC + struct.pack("<I", version)
    for n in nodes:
        out += _node(n, len(out), version, compress)
    out += b"\x00" * (25 if version >= 7500 else 13)
    return out + b"\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e" + b"\x00" * 4 + struct.pack("<I", version) + b"\x00" * 120


def P(name, typ, *vals):
    codes = [("D", float(v)) if isinstance(v, float) else ("I", v) for v in vals]
    return ("P", [("S", name), ("S", typ), ("S", ""), ("S", "A")] + codes, [])


def packw(right: float, next_left: float) -> float:
    """KeyAttrDataFloat[2]: two int16 weights (x 9999) packed into the bits of one float32."""
    bits = (round(next_left * 9999) << 16) | round(right * 9999)
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _curve(cid, frames, values, fps=24, attrs=None):
    """attrs: [(flags, [rightSlope, nextLeftSlope, packed weights, velocity], refcount), ...]."""
    kids = [("Default", [("D", 0.0)], []), ("KeyVer", [("I", 4009)], []),
            ("KeyTime", [("l", [f * TICKS // fps for f in frames])], []),
            ("KeyValueFloat", [("f", values)], [])]
    if attrs:
        kids += [("KeyAttrFlags", [("i", [a[0] for a in attrs])], []),
                 ("KeyAttrDataFloat", [("f", [x for a in attrs for x in a[1]])], []),
                 ("KeyAttrRefCount", [("i", [a[2] for a in attrs])], [])]
    return ("AnimationCurve", [("L", cid), ("S", "\x00\x01AnimCurve"), ("S", "")], kids)


def scene_nodes():
    """Scene: TrackNull (linear T), Camera01 (linear T + FocalLength, additive Z layer), Spinner
    (cubic/constant key attributes, RotationOrder ZXY + PreRotation). Stack "Take 001" (layers Base and
    Add, additive 50 %) is first; stack "Take 002" animates TrackNull differently and must be ignored."""
    null, cam, attr, spin = 1001, 1002, 1003, 1004
    cn_t, cn_ct, cn_f, cn_t2, cn_add, cn_s = 2001, 2002, 2003, 2004, 2005, 2006
    stack1, stack2, base, add, other = 5001, 5002, 5101, 5102, 5201
    lin = [(0x4, [0.0, 0.0, packw(1 / 3, 1 / 3), 0.0], 3)]
    objects = [
        ("AnimationStack", [("L", stack1), ("S", "Take 001\x00\x01AnimStack"), ("S", "")], []),
        ("AnimationStack", [("L", stack2), ("S", "Take 002\x00\x01AnimStack"), ("S", "")], []),
        ("AnimationLayer", [("L", base), ("S", "Base\x00\x01AnimLayer"), ("S", "")], []),
        ("AnimationLayer", [("L", add), ("S", "Add\x00\x01AnimLayer"), ("S", "")], [
            ("Properties70", [], [P("Weight", "Number", 50.0), P("BlendMode", "enum", 0)])]),
        ("AnimationLayer", [("L", other), ("S", "Other\x00\x01AnimLayer"), ("S", "")], []),
        ("NodeAttribute", [("L", attr), ("S", "Camera01\x00\x01NodeAttribute"), ("S", "Camera")], [
            ("Properties70", [], [P("FieldOfView", "FieldOfView", 40.0), P("FocalLength", "Number", 35.0),
                                  P("ApertureMode", "enum", 2)])]),
        ("Model", [("L", null), ("S", "TrackNull\x00\x01Model"), ("S", "Null")], [
            ("Version", [("I", 232)], []),
            ("Properties70", [], [P("Lcl Rotation", "Lcl Rotation", 0.0, 0.0, 45.0),
                                  P("Lcl Scaling", "Lcl Scaling", 2.0, 3.0, 1.0)])]),
        ("Model", [("L", cam), ("S", "Camera01\x00\x01Model"), ("S", "Camera")], [("Version", [("I", 232)], [])]),
        ("Model", [("L", spin), ("S", "Spinner\x00\x01Model"), ("S", "Null")], [
            ("Properties70", [], [P("RotationActive", "bool", 1), P("RotationOrder", "enum", 4),
                                  P("PreRotation", "Vector3D", 0.0, 0.0, 90.0),
                                  P("Lcl Rotation", "Lcl Rotation", 30.0, 45.0, 20.0)])]),
        ("AnimationCurveNode", [("L", cn_t), ("S", "T\x00\x01AnimCurveNode"), ("S", "")], [
            ("Properties70", [], [P("d|X", "Number", 0.0), P("d|Y", "Number", 0.0), P("d|Z", "Number", 2.5)])]),
        ("AnimationCurveNode", [("L", cn_ct), ("S", "T\x00\x01AnimCurveNode"), ("S", "")], [
            ("Properties70", [], [P("d|X", "Number", 0.0), P("d|Y", "Number", 1.0), P("d|Z", "Number", 10.0)])]),
        ("AnimationCurveNode", [("L", cn_f), ("S", "FocalLength\x00\x01AnimCurveNode"), ("S", "")], [
            ("Properties70", [], [P("d|FocalLength", "Number", 35.0)])]),
        ("AnimationCurveNode", [("L", cn_t2), ("S", "T\x00\x01AnimCurveNode"), ("S", "")], [
            ("Properties70", [], [P("d|X", "Number", 0.0), P("d|Y", "Number", 0.0), P("d|Z", "Number", 0.0)])]),
        ("AnimationCurveNode", [("L", cn_add), ("S", "T\x00\x01AnimCurveNode"), ("S", "")], [
            ("Properties70", [], [P("d|X", "Number", 0.0), P("d|Y", "Number", 0.0), P("d|Z", "Number", 4.0)])]),
        ("AnimationCurveNode", [("L", cn_s), ("S", "T\x00\x01AnimCurveNode"), ("S", "")], [
            ("Properties70", [], [P("d|X", "Number", 0.0), P("d|Y", "Number", 0.0), P("d|Z", "Number", 0.0)])]),
        _curve(3001, [0, 1, 2], [0.0, 10.0, 20.0], attrs=lin),
        _curve(3002, [0, 1, 2], [5.0, 5.0, 15.0]),
        _curve(3003, [0, 2], [0.0, 1.0]),
        _curve(3004, [0, 2], [35.0, 50.0]),
        _curve(3005, [0, 1, 2], [100.0, 200.0, 300.0]),
        # Spinner X: cubic user tangents; segment 0 unweighted (rs 30, next-left 0), segment 1 weighted
        # (right weight 0.5, next-left weight 0.2, rs 0, next-left slope -20)
        _curve(3006, [0, 24, 48], [0.0, 10.0, 0.0], attrs=[
            (0x408, [30.0, 0.0, packw(1 / 3, 1 / 3), 0.0], 1),
            (0x408 | 0x03000000, [0.0, -20.0, packw(0.5, 0.2), 0.0], 2)]),
        # Spinner Y: constant (standard), then constant-next
        _curve(3007, [0, 24, 48], [5.0, 7.0, 9.0], attrs=[
            (0x2, [0.0, 0.0, 0.0, 0.0], 1), (0x102, [0.0, 0.0, 0.0, 0.0], 2)]),
    ]
    C = lambda *a: ("C", [("S", a[0]), ("L", a[1]), ("L", a[2])] + ([("S", a[3])] if len(a) > 3 else []), [])
    conns = [
        C("OO", null, 0), C("OO", cam, 0), C("OO", spin, 0), C("OO", attr, cam),
        C("OO", base, stack1), C("OO", add, stack1), C("OO", other, stack2),
        C("OO", cn_t, base), C("OO", cn_ct, base), C("OO", cn_f, base), C("OO", cn_s, base),
        C("OO", cn_add, add), C("OO", cn_t2, other),
        C("OP", cn_t, null, "Lcl Translation"), C("OP", 3001, cn_t, "d|X"), C("OP", 3002, cn_t, "d|Y"),
        C("OP", cn_t2, null, "Lcl Translation"), C("OP", 3005, cn_t2, "d|X"),
        C("OP", cn_ct, cam, "Lcl Translation"), C("OP", 3003, cn_ct, "d|X"),
        C("OP", cn_add, cam, "Lcl Translation"),
        C("OP", cn_f, attr, "FocalLength"), C("OP", 3004, cn_f, "d|FocalLength"),
        C("OP", cn_s, spin, "Lcl Translation"), C("OP", 3006, cn_s, "d|X"), C("OP", 3007, cn_s, "d|Y"),
    ]
    return [
        ("FBXHeaderExtension", [], [("FBXHeaderVersion", [("I", 1003)], []), ("FBXVersion", [("I", 7400)], [])]),
        ("Objects", [], objects),
        ("Connections", [], conns),
    ]


def scene_fbx(version: int = 7400, compress: bool = False) -> bytes:
    return write_fbx(scene_nodes(), version, compress)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else __file__.rsplit("/", 1)[0] + "/sample_7400.fbx"
    open(out, "wb").write(scene_fbx(7400))
