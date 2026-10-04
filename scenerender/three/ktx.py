"""KHR_texture_basisu decoding through the public libktx 4 C API.

ETC1S/BasisLZ and UASTC (including Zstandard) transcode to RGBA8 on the CPU.
No private native structures or GPU compression capabilities are required.
"""
import ctypes as C
import ctypes.util
from functools import lru_cache
import os
from pathlib import Path
import struct
import sys

import numpy as np

from ..raster import srgb_to_linear
from .texture_image import TextureImage

MAGIC = b'\xabKTX 20\xbb\r\n\x1a\n'


class KTXUnavailable(RuntimeError):
    """The optional native decoder is missing or incompatible."""


@lru_cache(maxsize=4)
def _library(configured):
    names = ('libktx.so.4', 'libktx.so', 'libktx.4.dylib', 'libktx.dylib', 'ktx.dll')
    candidates = [configured] if configured else [
        str(Path(sys.prefix)/'lib'/name) for name in names]
    if not configured:
        candidates += [str(Path(sys.prefix)/'Library'/'bin'/'ktx.dll'),
                       ctypes.util.find_library('ktx'), *names]
    signatures = {
        'ktxTexture2_CreateFromMemory': ([C.c_void_p, C.c_size_t, C.c_uint, C.POINTER(C.c_void_p)], C.c_int),
        'ktxTexture2_Destroy': ([C.c_void_p], None),
        'ktxTexture2_NeedsTranscoding': ([C.c_void_p], C.c_bool),
        'ktxTexture2_TranscodeBasis': ([C.c_void_p, C.c_int, C.c_uint], C.c_int),
        'ktxTexture2_GetImageOffset': ([C.c_void_p, C.c_uint, C.c_uint, C.c_uint, C.POINTER(C.c_size_t)], C.c_int),
        'ktxTexture_GetData': ([C.c_void_p], C.c_void_p),
        'ktxTexture_GetDataSize': ([C.c_void_p], C.c_size_t),
        'ktxTexture_GetRowPitch': ([C.c_void_p, C.c_uint], C.c_uint),
        'ktxErrorString': ([C.c_int], C.c_char_p),
    }
    failures = []
    for name in filter(None, candidates):
        try:
            lib = (C.WinDLL if os.name == 'nt' else C.CDLL)(name)
            for symbol, (args, result) in signatures.items():
                function = getattr(lib, symbol)
                function.argtypes, function.restype = args, result
            return lib
        except (OSError, AttributeError) as exc:
            failures.append(str(exc))
    detail = failures[0] if configured and failures else 'library not found'
    raise KTXUnavailable('KTX2/BasisU textures require libktx 4 (Khronos KTX-Software). '
                         'Install it or set SCENERENDER_LIBKTX to its shared-library path: '+detail)


def library():
    return _library(os.environ.get('SCENERENDER_LIBKTX', ''))


def _header(data, srgb):
    """Validate the glTF image restrictions and all sections before native decoding."""
    if len(data) < 80 or data[:12] != MAGIC:
        raise ValueError('KHR_texture_basisu requires a KTX2 image')
    (fmt, size, w, h, depth, layers, faces, levels, compression,
     dfd_offset, dfd_size, kv_offset, kv_size, sgd_offset, sgd_size) = struct.unpack_from('<13I2Q', data, 12)
    if not w or not h or w % 4 or h % 4 or depth or layers or faces != 1:
        raise ValueError('glTF KTX2 textures must be 2D, with dimensions divisible by four')
    levels = max(1, levels)
    if levels > max(w, h).bit_length() or len(data) < 80+24*levels:
        raise ValueError('Invalid KTX2 mip level index')
    if fmt != 0 or size != 1:
        raise ValueError('KHR_texture_basisu requires an ETC1S or UASTC payload')

    def section(offset, length):
        if offset < 80+24*levels or offset+length > len(data):
            raise ValueError('Truncated or overlapping KTX2 section')
        return data[offset:offset+length]

    for i in range(levels):
        off, length, _ = struct.unpack_from('<3Q', data, 80+24*i)
        if not length:
            raise ValueError('Empty KTX2 mip level')
        section(off, length)
    if sgd_size:
        section(sgd_offset, sgd_size)
    dfd = section(dfd_offset, dfd_size)
    if len(dfd) < 44 or struct.unpack_from('<I', dfd)[0] != len(dfd):
        raise ValueError('Invalid KTX2 data format descriptor')
    block_size = struct.unpack_from('<H', dfd, 10)[0]
    if block_size < 40 or block_size+4 > len(dfd) or (block_size-24) % 16:
        raise ValueError('Invalid KTX2 data format descriptor samples')
    model, primaries, transfer, flags = dfd[12:16]
    channels = tuple(dfd[i+3] & 15 for i in range(28, block_size+4, 16))
    valid = ((model == 163 and compression == 1 and channels in ((0,), (0, 15), (3,), (3, 4))) or
             (model == 166 and compression in (0, 2) and channels in ((0,), (3,), (4,), (5,), (6,))))
    if not valid:
        raise ValueError('KHR_texture_basisu requires ETC1S/BasisLZ or UASTC with optional Zstandard')
    if (primaries, transfer) != ((1, 2) if srgb else (0, 1)):
        raise ValueError('KTX2 color space does not match its glTF color/data texture usage')
    if srgb and not ((model == 163 and channels in ((0,), (0, 15))) or
                     (model == 166 and channels in ((0,), (3,)))):
        raise ValueError('Red and red/green KTX2 textures must contain linear data')
    if flags & 1:
        raise ValueError('glTF material textures require straight alpha, not premultiplied KTX2 alpha')
    if kv_size:
        kv = section(kv_offset, kv_size)
        pos = 0
        while pos < len(kv):
            if pos+4 > len(kv):
                raise ValueError('Truncated KTX2 metadata')
            length = struct.unpack_from('<I', kv, pos)[0]
            pos += 4
            if pos+length > len(kv) or not length:
                raise ValueError('Truncated KTX2 metadata value')
            entry = kv[pos:pos+length]
            if b'\0' not in entry:
                raise ValueError('Invalid KTX2 metadata key')
            key, value = entry.split(b'\0', 1)
            if key in (b'KTXorientation', b'KTXswizzle'):
                expected = b'rd' if key == b'KTXorientation' else b'rgba'
                if value.rstrip(b'\0') != expected:
                    raise ValueError('glTF KTX2 requires rd orientation and rgba swizzle')
            pos += (length+3)//4*4
        if pos != len(kv):
            raise ValueError('Truncated KTX2 metadata padding')
    # ETC1S's second slice, and legacy UASTC RRRG, carry green in alpha.
    # This is payload packing, not the optional KTXswizzle metadata.
    green_in_alpha = (model == 163 and channels == (3, 4)) or (model == 166 and channels == (5,))
    red_only = (model == 163 and channels == (3,)) or (model == 166 and channels == (4,))
    return w, h, levels, green_in_alpha, red_only


def decode_basisu(data: bytes, srgb: bool) -> TextureImage:
    w, h, count, green_in_alpha, red_only = _header(data, srgb)
    lib = library()
    texture = C.c_void_p()
    source = C.create_string_buffer(data)

    def check(code):
        if code:
            raise ValueError('Cannot decode KTX2/BasisU: '+lib.ktxErrorString(code).decode('utf-8', 'replace'))

    try:
        check(lib.ktxTexture2_CreateFromMemory(source, len(data), 1, C.byref(texture)))
        if not lib.ktxTexture2_NeedsTranscoding(texture):
            raise ValueError('KTX2 image does not contain a Basis Universal payload')
        check(lib.ktxTexture2_TranscodeBasis(texture, 13, 0))  # KTX_TTF_RGBA32
        start, length = lib.ktxTexture_GetData(texture), lib.ktxTexture_GetDataSize(texture)
        levels = []
        for level in range(count):
            width, height = max(1, w >> level), max(1, h >> level)
            offset = C.c_size_t()
            check(lib.ktxTexture2_GetImageOffset(texture, level, 0, 0, C.byref(offset)))
            pitch = lib.ktxTexture_GetRowPitch(texture, level)
            size = pitch*height
            if not start or pitch < width*4 or offset.value+size > length:
                raise ValueError('Invalid transcoded KTX2 image layout')
            raw = C.string_at(start+offset.value, size)
            a = np.frombuffer(raw, np.uint8).reshape(height, pitch)[:, :width*4].reshape(height, width, 4)
            a = a.astype(np.float32)/255.
            if green_in_alpha:
                a[..., 1] = a[..., 3]
                a[..., 2], a[..., 3] = 0., 1.
            if red_only:
                a[..., 1:3] = 0.
            if srgb:
                a[..., :3] = srgb_to_linear(a[..., :3])
            levels.append(a)
        return TextureImage(tuple(levels))
    finally:
        if texture:
            lib.ktxTexture2_Destroy(texture)
