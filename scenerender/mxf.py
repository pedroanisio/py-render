"""Content light level metadata in MXF picture descriptors.

The MaxCLL/MaxFALL UInt16 properties use the registered Apple ULs understood by
FFmpeg's mxfdec.c (mxf_apple_coll_max_cll / mxf_apple_coll_max_fall). FFmpeg
currently reads these properties but does not write them. Add them to every
header metadata copy, preserving essence and index tables byte for byte.
"""
from __future__ import annotations

import io
import os
import shutil
import struct
import tempfile

PRIMER = bytes.fromhex("060e2b34020501010d01020101050100")
FILL = bytes.fromhex("060e2b34010101020301021001000000")
PARTITION = bytes.fromhex("060e2b34020501010d01020101")
RIP = bytes.fromhex("060e2b34020501010d01020101110100")
PICTURE = bytes.fromhex("060e2b34025301010d0101010101")
CLL = bytes.fromhex("060e2b340101010e0e20040105030101")
FALL = bytes.fromhex("060e2b340101010e0e20040105030102")


def _klvs(f, end):
    while f.tell() < end:
        start = f.tell()
        key, size = f.read(16), f.read(1)
        if len(key) != 16 or not size:
            raise ValueError("truncated MXF KLV header")
        n = size[0]
        if n & 128:
            count = n & 127
            if not 1 <= count <= 8:
                raise ValueError("invalid MXF BER length")
            raw = f.read(count)
            if len(raw) != count:
                raise ValueError("truncated MXF BER length")
            n = int.from_bytes(raw, "big")
        value = f.tell()
        if value + n > end:
            raise ValueError("MXF KLV exceeds its enclosing region")
        yield start, key, value, n
        f.seek(value + n)


def _pack(key, value):
    n = len(value)
    count = max(3, (n.bit_length() + 7) // 8)
    return key + bytes([128 | count]) + n.to_bytes(count, "big") + value


def _local_items(value):
    out, pos = [], 0
    while pos < len(value):
        if pos + 4 > len(value):
            raise ValueError("truncated MXF local tag")
        tag, n = struct.unpack_from(">HH", value, pos)
        pos += 4
        if pos + n > len(value):
            raise ValueError("truncated MXF local value")
        out.append((tag, value[pos:pos + n]))
        pos += n
    return out


def _metadata(raw, levels, kag):
    records = []
    for start, key, value, n in _klvs(io.BytesIO(raw), len(raw)):
        if key[:7] == FILL[:7] and key[8:] == FILL[8:]:
            continue
        records.append((key, raw[value:value + n]))
    primers = [i for i, (key, _) in enumerate(records) if key == PRIMER]
    if len(primers) != 1:
        raise ValueError("MXF header must contain one primer pack")
    pi = primers[0]
    primer = records[pi][1]
    if len(primer) < 8:
        raise ValueError("truncated MXF primer")
    count, size = struct.unpack_from(">II", primer)
    if size != 18 or len(primer) != 8 + count * size:
        raise ValueError("invalid MXF primer entries")
    entries = [(int.from_bytes(primer[i:i + 2], "big"), primer[i + 2:i + 18])
               for i in range(8, len(primer), 18)]
    used = {tag for tag, _ in entries}
    by_ul = {ul: tag for tag, ul in entries}
    for ul in levels:
        if ul not in by_ul:
            tag = next((t for t in range(0x8000, 0x10000) if t not in used), None)
            if tag is None:
                raise ValueError("MXF primer has no free dynamic tags")
            used.add(tag)
            entries.append((tag, ul))
            by_ul[ul] = tag
    records[pi] = (PRIMER, struct.pack(">II", len(entries), 18) +
                   b"".join(struct.pack(">H", tag) + ul for tag, ul in entries))
    found = 0
    for i, (key, value) in enumerate(records):
        if key[:14] != PICTURE or key[14] not in (0x28, 0x29, 0x51):
            continue
        items = _local_items(value)
        updates = {by_ul[ul]: struct.pack(">H", n) for ul, n in levels.items()}
        items = [(tag, data) for tag, data in items if tag not in updates]
        items.extend(updates.items())
        records[i] = (key, b"".join(struct.pack(">HH", tag, len(data)) + data for tag, data in items))
        found += 1
    if not found:
        raise ValueError("MXF header has no supported picture descriptor")
    result = b"".join(_pack(key, value) for key, value in records)
    target = len(raw)
    # Keep the existing span if it fits; otherwise grow by whole KAGs. A fill
    # item needs 20 bytes with our BER4 encoding.
    if len(result) > target or 0 < target - len(result) < 20:
        target += ((len(result) + 20 - target + kag - 1) // kag) * kag
    pad = target - len(result)
    return result + (_pack(FILL, bytes(pad - 20)) if pad else b"")


def inject_content_light(path, max_cll=None, max_fall=None):
    """Atomically insert/update MaxCLL/MaxFALL without re-encoding the video.

    Reclaim header fill first. If a header grows, relocate partition/RIP absolute
    offsets and update its HeaderByteCount. Essence stream offsets stay unchanged.
    Omitted values preserve any existing property; zero is an explicit value.
    """
    levels = {}
    for ul, value in ((CLL, max_cll), (FALL, max_fall)):
        if value is not None:
            n = int(value)
            if not 0 <= n <= 65535:
                raise ValueError("MXF MaxCLL/MaxFALL must fit an unsigned 16-bit value")
            levels[ul] = n
    if not levels:
        return
    path = os.fspath(path)
    size = os.path.getsize(path)
    regions, partitions, rips = [], [], []
    with open(path, "rb") as f:
        active = None
        for start, key, value, n in _klvs(f, size):
            if key[:13] == PARTITION and key[13] in (2, 3, 4):
                f.seek(value)
                data = bytearray(f.read(n))
                if len(data) < 64:
                    raise ValueError("truncated MXF partition pack")
                partitions.append((value, data))
                active = (data, int.from_bytes(data[32:40], "big"), int.from_bytes(data[4:8], "big"))
            elif key == PRIMER and active is not None:
                data, header_size, kag = active
                if not header_size or not 1 <= kag <= 1024 * 1024 or start + header_size > size:
                    raise ValueError("invalid MXF header size or KAG")
                f.seek(start)
                raw = f.read(header_size)
                rewritten = _metadata(raw, levels, kag)
                regions.append((start, start + header_size, rewritten))
                data[32:40] = len(rewritten).to_bytes(8, "big")
                active = None
            elif key == RIP:
                f.seek(value)
                rips.append((value, bytearray(f.read(n))))
        if not regions:
            raise ValueError("no MXF header metadata found")

        def relocate(offset):
            return offset + sum(len(raw) - (end - start) for start, end, raw in regions if end <= offset)

        patches = list(regions)
        for offset, data in partitions:
            for pos in (8, 16, 24):  # ThisPartition, PreviousPartition, FooterPartition
                old = int.from_bytes(data[pos:pos + 8], "big")
                data[pos:pos + 8] = relocate(old).to_bytes(8, "big")
            patches.append((offset, offset + len(data), data))
        for offset, data in rips:
            if len(data) < 4 or (len(data) - 4) % 12:
                raise ValueError("invalid MXF random index pack")
            for pos in range(4, len(data) - 4, 12):
                old = int.from_bytes(data[pos:pos + 8], "big")
                data[pos:pos + 8] = relocate(old).to_bytes(8, "big")
            patches.append((offset, offset + len(data), data))
        patches.sort()
        if any(a[1] > b[0] for a, b in zip(patches, patches[1:])):
            raise ValueError("overlapping MXF metadata regions")
        fd, tmp = tempfile.mkstemp(prefix=".mxf-metadata-", dir=os.path.dirname(os.path.abspath(path)))
        try:
            with os.fdopen(fd, "wb") as out:
                f.seek(0)
                for start, end, raw in patches:
                    remaining = start - f.tell()
                    while remaining:
                        chunk = f.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("truncated MXF during copy")
                        out.write(chunk)
                        remaining -= len(chunk)
                    out.write(raw)
                    f.seek(end)
                shutil.copyfileobj(f, out)
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
