"""3D asset loaders producing `Model`, plus image / HDR / IES decoding for the 3D renderer.

Public API: `load_model`, `decode_image`, `load_hdr_image`, `load_ies` / `IESProfile`.

Pinned decisions
  General
    * `load_model` caches by (abspath, mtime) in process; the format comes from `fmt` (mesh asset
      @format: gltf|glb|obj|usd|usdz|fbx|ply|splat, plus stl) or the file extension. Units stay the
      file's (treated as metres); output is right-handed +Y up (Z-up USD stages get a synthetic root
      rotating -90 deg about X; FBX is converted by ufbx to right-handed Y-up). Splats keep their
      COLMAP axes. The renderer maps both into scene space (CONVENTIONS 2.6, see model.py).
    * Colours in `MaterialSpec.params` are linear float tuples (RGBA; emissive/attenuation/sheen/
      specular colours carry alpha 1). Textures are (h, w, 4) in [0, 1], float16 when decoded
      from 8-bit images and float32 otherwise (16-bit, HDR, EXR, npy); baseColorMap and
      emissiveMap are sRGB-decoded to linear, data maps (normal, metallicRoughness, occlusion) are not.
    * Node.matrix uses the math layout of animation.py (column vectors).
  glTF / GLB (pygltflib)
    * Accessors include byteStride, sparse, normalized integers and padded matrix columns;
      buffers may be GLB blobs, data URIs or external files. All TEXCOORD_n and JOINTS_n/WEIGHTS_n
      sets are retained; COLOR_0 drives vertex colour. Each material map selects its UV set and
      applies its own KHR_texture_transform before interpolation.
      Per-map samplers retain both axis wrap modes and the minification/magnification filters.
    * TRIANGLE_STRIP/FAN are converted to triangle lists; POINTS/LINES/LINE_LOOP/LINE_STRIP retain
      their topology through rendering. Missing indices = 0..n-1. Skin weights are renormalised.
      Missing normals use flat shading (also after morphing); normal and tangent morph deltas
      preserve absent-target attributes and tangent handedness.
    * mesh.weights is copied to node.weights when the node has none. Channels without a target node
      (KHR_animation_pointer) are ignored. Clip name = animation.name or its index as a string.
    * Material factors default per glTF (metallic = roughness = 1); extension params are only set when
      the extension is present. anisotropyRotation is converted to degrees. EXT_texture_webp sources
      are honoured; KHR_texture_basisu (KTX2) cannot be decoded with PIL and is skipped with a warning.
  OBJ / PLY / STL (trimesh, force="scene", process=False)
    * One Node (explicit matrix = scene-graph transform) per geometry instance. Vertex normals come
      from trimesh (file normals or trimesh-computed). UVs are flipped to glTF (v = 1 - v). OBJ/MTL
      colours are display-referred and converted sRGB -> linear; vertex colours likewise.
    * PLY with Gaussian-splat vertex properties is parsed here (ascii / binary little / big endian):
      colour = sRGB->linear(clamp(0.5 + 0.28209479 * f_dc)), alpha = sigmoid(opacity),
      scale = exp(scale_i), rotation (w, x, y, z) in the file -> normalised xyzw.
  .splat (antimatter15): 32-byte records; RGB sRGB -> linear, alpha linear /255, rotation
      (w, x, y, z) = (byte - 128) / 128 -> normalised xyzw.
  USD / USDA / USDC / USDZ (pxr)
    * One Node per Xformable prim (Scopes pass through); invisible prims and guide/proxy purpose
      subtrees are skipped. Local transforms at the default time; resetXformStack makes a root.
    * Meshes are fan-triangulated (leftHanded orientation flips winding). When any used primvar is
      faceVarying/uniform the mesh is un-indexed to one vertex per face corner. Normals: primvars:normals
      over the normals attribute. primvars:st (or the first texCoord2f primvar) -> uvs with v = 1 - v.
      GeomSubsets of the "materialBind" family split the mesh into primitives per bound material.
    * displayColor: constant -> baseColor when no material is bound; varying -> vertex colours.
      USD colours are already linear.
    * UsdPreviewSurface: diffuseColor, metallic, roughness, emissiveColor, opacity (opacity < 1 ->
      alphaMode blend; opacityThreshold > 0 -> mask), ior, clearcoat, clearcoatRoughness; UsdUVTexture
      files for diffuseColor / emissiveColor (sRGB unless sourceColorSpace = raw), normal (stored as
      the [0, 1] encoded image, i.e. assumes the standard scale 2 / bias -1), occlusion (R), and
      roughness / metallic merged into metallicRoughnessMap (G = roughness, B = metallic, missing
      one = 1); the matching factor param is then 1. Texture scale/bias and UsdTransform2d are ignored.
      Packaged USDZ textures ("pkg.usdz[inner]") are read from the zip.
    * Time-sampled xformOps -> clip "default": native USD transform evaluation at each requested
      time preserves shear, transform order and each op's interpolation; time = (code - start
      code) / timeCodesPerSecond.
    * UsdSkel: each bound Skeleton becomes joint Nodes (under the skeleton's Node) with rest
      transforms; inverse_bind = inverse(bindTransform) @ geomBindTransform; jointIndices/Weights are
      retained and renormalised. A bound SkelAnimation adds LINEAR TRS
      channels for its joints to clip "default" (attributes with a single value override the rest
      pose instead). Blend shapes include sparse point/normal offsets and inbetweens. Animation
      weights are mapped by name; object3D morphWeights overrides are resolved through inbetweens.
  FBX (ufbx)
    * Loaded with target_axes = right-handed Y-up, space conversion on the root, geometry and inherit
      modes resolved through helper nodes, file units kept, normals generated when missing.
    * One Node per ufbx node (TRS = local_transform); meshes are fan-triangulated per material part
      and de-duplicated per face corner. Skin clusters -> Skin (inverse_bind = geometry_to_bone,
      all weights retained). Blend shape channels -> morph targets (default weight = channel weight).
    * Materials from ufbx's PBR mapping (base colour x base factor, metalness, roughness, emission
      colour x factor, opacity); FBX colours are taken as linear. Textures: embedded content, else the
      absolute / relative file name if it exists (warning otherwise); base colour / emission / normal
      maps are bound.
    * Each anim stack -> clip (stack name) baked with ufbx.bake_anim (trim_start_time) into LINEAR
      TRS channels; blend-shape weights are sampled at the scene frame rate into "weights" channels.
  Images
    * decode_image: PIL formats (8-bit /255, 16-bit /65535), .hdr/.exr/.npy (float, linear; srgb flag
      ignored). HDR bytes are recognised by magic.
    * Radiance .hdr: new-style and old-style RLE and flat scanlines, any of the 8
      axis orientations, value = mantissa * 2^(exponent - 136) (no +0.5 dither), EXPOSURE ignored.
    * load_hdr_image returns (h, w, 3) linear float32, cached by (abspath, mtime).
  IES (LM-63-1986/1991/1995/2002)
    * TILT=INCLUDE data is skipped (lamp-tilt factors not applied); TILT=<file> is ignored with a
      warning. Candela are multiplied by the candela multiplier and the ballast factor. Photometric
      types A/B are treated as type C.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import urllib.parse
import zipfile
from dataclasses import dataclass

import numpy as np

from ..raster import srgb_to_linear
from .model import Channel, Clip, MaterialSpec, Model, Node, Primitive, Skin, Splats

log = logging.getLogger(__name__)

_MODEL_CACHE: dict[tuple[str, float], Model] = {}
_HDR_CACHE: dict[tuple[str, float], np.ndarray] = {}
_EXT_FMT = {".gltf": "gltf", ".glb": "glb", ".obj": "obj", ".ply": "ply", ".stl": "stl",
            ".usd": "usd", ".usda": "usd", ".usdc": "usd", ".usdz": "usdz", ".fbx": "fbx",
            ".splat": "splat"}


def load_model(path: str, fmt: str | None = None) -> Model:
    ap = os.path.abspath(path)
    key = (ap, os.path.getmtime(ap))
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    f = (fmt or _EXT_FMT.get(os.path.splitext(ap)[1].lower(), "")).lower()
    if f in ("gltf", "glb"):
        m = _load_gltf(ap)
    elif f == "ply" and _ply_is_splat(ap):
        m = Model(splats=_load_ply_splats(ap))
    elif f in ("obj", "ply", "stl"):
        m = _load_trimesh(ap)
    elif f in ("usd", "usda", "usdc", "usdz"):
        m = _load_usd(ap)
    elif f == "fbx":
        m = _load_fbx(ap)
    elif f == "splat":
        m = Model(splats=_load_splat(ap))
    else:
        raise ValueError(f"unsupported 3D format {fmt or os.path.splitext(ap)[1]!r} for {path}")
    m.source = ap
    _MODEL_CACHE[key] = m
    return m


# ------------------------------------------------------------------------------------ helpers
def _lin_rgb(rgb) -> tuple:
    return tuple(float(v) for v in srgb_to_linear(np.asarray(rgb, dtype=np.float64)[:3]))


def _rgba(rgb, a: float = 1.0) -> tuple:
    v = [float(x) for x in rgb]
    return tuple(v[:3]) + (float(v[3]) if len(v) > 3 else a,)


def _mat_to_quat(r: np.ndarray) -> np.ndarray:
    """Rotation matrix (3x3, orthonormal) -> quaternion xyzw."""
    tr = np.trace(r)
    if tr > 0:
        s = 2.0 * math.sqrt(tr + 1.0)
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = 2.0 * math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2])
        q = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = 2.0 * math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2])
        q = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = 2.0 * math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1])
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def _decompose(m: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """4x4 -> (translation, quaternion xyzw, scale); shear is dropped, negative det -> -sx."""
    lin = m[:3, :3]
    s = np.linalg.norm(lin, axis=0)
    if np.linalg.det(lin) < 0:
        s[0] = -s[0]
    r = lin / np.where(np.abs(s) > 1e-20, s, 1.0)[None, :]
    return m[:3, 3].copy(), _mat_to_quat(r), s


def _skin_weights(idx: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Preserve all influences and normalize each vertex's weights."""
    n, k = w.shape
    if k < 4:
        idx = np.concatenate([idx, np.zeros((n, 4 - k), idx.dtype)], 1)
        w = np.concatenate([w, np.zeros((n, 4 - k), w.dtype)], 1)
    s = w.sum(1, keepdims=True)
    return idx.astype(np.uint32), np.where(s > 0, w / np.where(s > 0, s, 1), w).astype(np.float32)


def _fan(counts: np.ndarray) -> np.ndarray:
    """Fan triangulation of polygons given their corner counts -> (m, 3) corner indices."""
    counts = np.asarray(counts, dtype=np.int64)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    ntri = np.maximum(counts - 2, 0)
    base = np.repeat(starts, ntri)
    k = np.arange(int(ntri.sum())) - np.repeat(np.cumsum(ntri) - ntri, ntri)
    return np.stack([base, base + k + 1, base + k + 2], 1)


def _fan_faces(counts: np.ndarray) -> np.ndarray:
    """Face index of every triangle produced by `_fan`."""
    counts = np.asarray(counts, dtype=np.int64)
    return np.repeat(np.arange(len(counts)), np.maximum(counts - 2, 0))


# ------------------------------------------------------------------------------------ images
def decode_image(src: bytes | str, srgb: bool, half: bool = True) -> np.ndarray:
    """Decode to (h, w, 4) in [0, 1] (HDR formats may exceed 1); srgb=True linearises RGB.
    8-bit sources come back as float16 when `half` (textures), everything else as float32."""
    if isinstance(src, str):
        ext = os.path.splitext(src)[1].lower()
        if ext == ".npy":
            a = np.load(src).astype(np.float32)
            return a if a.shape[-1] == 4 else _add_alpha(a)
        if ext in (".hdr", ".exr"):
            return _add_alpha(load_hdr_image(src))
        with open(src, "rb") as fh:
            src = fh.read()
    if src[:2] == b"#?":
        return _add_alpha(_read_rgbe(src))
    if src[:6] == b"\x93NUMPY":
        a = np.load(io.BytesIO(src)).astype(np.float32)
        return a if a.shape[-1] == 4 else _add_alpha(a)
    if src[:4] == b"\x76\x2f\x31\x01":
        return _add_alpha(_read_exr_bytes(src))
    from PIL import Image
    im = Image.open(io.BytesIO(src))
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        g = np.asarray(im, dtype=np.float32) / 65535.0
        a = np.stack([g, g, g, np.ones_like(g)], -1)
    else:
        # 8-bit sources: every channel value is one of 256 codes, so the conversion (scale, sRGB
        # decode of RGB, dtype) is a table built with the same operations, applied by one gather.
        # They are kept as half floats: 11 significant bits resolve every (sRGB-decoded) 8-bit level
        # to well under a quantisation step, at half the memory of float32 on the CPU and on the GPU
        # (a 4K RGBA map: 134 MB instead of 268 MB).
        raw = np.asarray(im if im.mode in ("RGB", "RGBA") else im.convert("RGBA"))
        lut = _code_table(srgb, np.float16 if half else np.float32)
        out = np.empty(raw.shape[:2] + (4,), lut.dtype)
        for c in range(3):
            np.take(lut[0], raw[..., c], out=out[..., c])
        if raw.shape[-1] == 4:
            np.take(lut[1], raw[..., 3], out=out[..., 3])
        else:
            out[..., 3] = lut[1][255]
        return out
    if srgb:
        a = np.concatenate([srgb_to_linear(a[..., :3]), a[..., 3:]], -1)
    return np.ascontiguousarray(a, dtype=np.float32)


_CODE_TABLES: dict = {}


def _code_table(srgb: bool, dtype) -> np.ndarray:
    """(2, 256): what decode_image computes for each 8-bit code, colour (row 0) and alpha (row 1)."""
    key = (srgb, np.dtype(dtype).str)
    hit = _CODE_TABLES.get(key)
    if hit is None:
        a = np.arange(256, dtype=np.float32)[:, None].repeat(4, 1) / 255.0
        if srgb:
            a = np.concatenate([srgb_to_linear(a[..., :3]), a[..., 3:]], -1)
        a = a.astype(dtype)
        hit = _CODE_TABLES[key] = np.ascontiguousarray(np.stack([a[:, 0], a[:, 3]]))
    return hit


def _add_alpha(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float32)
    if a.ndim == 2:
        a = np.stack([a] * 3, -1)
    return np.concatenate([a[..., :3], np.ones(a.shape[:2] + (1,), np.float32)], -1)


def load_hdr_image(path: str) -> np.ndarray:
    """Linear float32 (h, w, 3) from .hdr (RGBE), .exr, .npy or 8/16-bit PIL images (sRGB)."""
    ap = os.path.abspath(path)
    key = (ap, os.path.getmtime(ap))
    if key not in _HDR_CACHE:
        ext = os.path.splitext(ap)[1].lower()
        if ext == ".hdr":
            with open(ap, "rb") as fh:
                img = _read_rgbe(fh.read())
        elif ext == ".exr":
            img = _read_exr(ap)
        elif ext == ".npy":
            img = np.load(ap).astype(np.float32)
            img = np.stack([img] * 3, -1) if img.ndim == 2 else img[..., :3]
        else:
            img = decode_image(ap, srgb=True, half=False)[..., :3]
        _HDR_CACHE[key] = np.ascontiguousarray(img, dtype=np.float32)
    return _HDR_CACHE[key]


def _read_rgbe(data: bytes) -> np.ndarray:
    pos = 0
    fmt = b"32-bit_rle_rgbe"
    while True:
        end = data.index(b"\n", pos)
        line = data[pos:end].strip()
        pos = end + 1
        if not line:
            break
        if line.startswith(b"FORMAT="):
            fmt = line[7:]
    if fmt not in (b"32-bit_rle_rgbe", b"32-bit_rle_xyze"):
        raise ValueError(f"unsupported Radiance format {fmt!r}")
    end = data.index(b"\n", pos)
    a1, n1, a2, n2 = data[pos:end].split()
    pos = end + 1
    n1, n2 = int(n1), int(n2)
    if n1 <= 0 or n2 <= 0 or {a1[1:], a2[1:]} != {b"X", b"Y"} or any(
            a not in (b"+X", b"-X", b"+Y", b"-Y") for a in (a1, a2)):
        raise ValueError("invalid Radiance resolution")
    buf = np.frombuffer(data, dtype=np.uint8)
    out = np.empty((n1, n2, 4), np.uint8)

    def require(count):
        if pos + count > len(buf):
            raise ValueError("truncated RGBE scanline")

    y0 = 0
    from .. import kernels
    if kernels.enabled():
        # New-style RLE scanlines (virtually every file) in one compiled pass; the loop below takes
        # over at the first old-style scanline.
        y0, pos, err = kernels.rgbe_rle(buf, pos, n1, n2, out)
        if err:
            raise ValueError({1: "truncated RGBE scanline", 2: "RGBE scanline width mismatch",
                              3: "invalid RGBE run length"}[err])
    for y in range(y0, n1):
        require(4)
        if buf[pos] == 2 and buf[pos + 1] == 2 and not buf[pos + 2] & 0x80:
            if (int(buf[pos + 2]) << 8 | int(buf[pos + 3])) != n2:
                raise ValueError("RGBE scanline width mismatch")
            pos += 4
            for c in range(4):
                x = 0
                row = out[y, :, c]
                while x < n2:
                    require(1)
                    cnt = int(buf[pos])
                    length = cnt - 128 if cnt > 128 else cnt
                    if length == 0 or x + length > n2:
                        raise ValueError("invalid RGBE run length")
                    if cnt > 128:
                        require(2)
                        cnt -= 128
                        row[x:x + cnt] = buf[pos + 1]
                        pos += 2
                    else:
                        require(1 + cnt)
                        row[x:x + cnt] = buf[pos + 1:pos + 1 + cnt]
                        pos += 1 + cnt
                    x += cnt
        else:
            # Old Radiance RLE uses (1,1,1,count) pixels. Consecutive
            # markers provide little-endian bytes of the repeat count.
            # Reference: LBNL-ETA/Radiance src/common/color.c, oldreadcolrs.
            x, shift = 0, 0
            while x < n2:
                require(4)
                pixel = buf[pos:pos + 4]
                pos += 4
                if np.all(pixel[:3] == 1):
                    count = int(pixel[3]) << shift
                    if x == 0 or x + count > n2 or shift > 24:
                        raise ValueError("invalid old RGBE run length")
                    out[y, x:x + count] = out[y, x - 1]
                    x += count
                    shift += 8
                else:
                    out[y, x] = pixel
                    x += 1
                    shift = 0
    e = out[..., 3].astype(np.int32)
    f = np.where(e > 0, np.ldexp(1.0, e - 136), 0.0).astype(np.float32)
    img = out[..., :3].astype(np.float32) * f[..., None]
    if fmt == b"32-bit_rle_xyze":
        from ..color import matrix
        img = (img @ matrix("xyz", "srgb").T).astype(np.float32)
    # a1 is the slow axis: "-Y +X" (standard) = rows top to bottom, columns left to right.
    if a1[1:2] == b"X":
        img = img.transpose(1, 0, 2)
        ax_y, ax_x = a2, a1
    else:
        ax_y, ax_x = a1, a2
    if ax_y[:1] == b"+":
        img = img[::-1]
    if ax_x[:1] == b"-":
        img = img[:, ::-1]
    return np.ascontiguousarray(img)


def _read_exr(path: str) -> np.ndarray:
    import OpenEXR
    ch = OpenEXR.File(path).channels()
    for k in ("RGB", "RGBA"):
        if k in ch:
            return np.asarray(ch[k].pixels, dtype=np.float32)[..., :3]
    if all(c in ch for c in "RGB"):
        return np.stack([np.asarray(ch[c].pixels, dtype=np.float32) for c in "RGB"], -1)
    first = np.asarray(next(iter(ch.values())).pixels, dtype=np.float32)
    return np.stack([first] * 3, -1) if first.ndim == 2 else first[..., :3]


def _read_exr_bytes(data: bytes) -> np.ndarray:
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".exr") as tf:
        tf.write(data)
        tf.flush()
        return _read_exr(tf.name)


# ------------------------------------------------------------------------------------ IES
@dataclass
class IESProfile:
    vertical_angles: np.ndarray       # (n_v,) degrees, type C: 0 = nadir
    horizontal_angles: np.ndarray     # (n_h,) degrees
    candela: np.ndarray               # (n_h, n_v)
    max_candela: float

    def table(self, n_theta: int = 64, n_phi: int = 64) -> np.ndarray:
        """float32 (n_phi, n_theta) of candela / max on theta = linspace(0, 180), phi = k*360/n_phi."""
        v, h, cd = self.vertical_angles, self.horizontal_angles, self.candela
        theta = np.linspace(0.0, 180.0, n_theta)
        phi = np.arange(n_phi) * (360.0 / n_phi)
        if len(h) == 1:
            planes = np.repeat(cd[:1], n_phi, axis=0)
        else:
            p = phi % 360.0
            if h[-1] <= 90.0 + 1e-6:                       # quadrant symmetry
                p = np.where(p > 180, 360 - p, p)
                p = np.where(p > 90, 180 - p, p)
            elif h[0] >= 90.0 - 1e-6 and h[-1] <= 270.0 + 1e-6:   # bilateral about the 90-270 plane
                p = np.where(p < 90, 180 - p, np.where(p > 270, 540 - p, p))
            elif h[-1] <= 180.0 + 1e-6:                    # bilateral about the 0-180 plane
                p = np.where(p > 180, 360 - p, p)
            elif h[-1] < 360.0:                            # full circle, close the gap
                h = np.append(h, h[0] + 360.0)
                cd = np.vstack([cd, cd[:1]])
            planes = np.stack([np.interp(p, h, cd[:, k]) for k in range(len(v))], 1)
        out = np.stack([np.interp(theta, v, row, left=0.0, right=0.0) for row in planes])
        return (out / (self.max_candela or 1.0)).astype(np.float32)


def load_ies(path: str) -> IESProfile:
    with open(path, "r", encoding="latin-1") as fh:
        lines = fh.read().splitlines()
    i = next((k for k, ln in enumerate(lines) if ln.strip().upper().startswith("TILT")), None)
    if i is None:
        raise ValueError(f"{path}: no TILT line")
    tilt = lines[i].split("=", 1)[1].strip()
    tok = " ".join(lines[i + 1:]).replace(",", " ").split()
    pos = 0
    if tilt.upper() == "INCLUDE":
        n = int(float(tok[1]))
        pos = 2 + 2 * n                                    # geometry, count, angles, factors
    elif tilt.upper() != "NONE":
        log.warning("IES %s: TILT file %r ignored", path, tilt)
    nums = [float(t) for t in tok[pos:]]
    _, _, mult, n_v, n_h, _, _, _, _, _ = nums[:10]
    ballast = nums[10]
    n_v, n_h = int(n_v), int(n_h)
    k = 13
    va = np.array(nums[k:k + n_v]); k += n_v
    ha = np.array(nums[k:k + n_h]); k += n_h
    cd = np.array(nums[k:k + n_v * n_h], dtype=np.float64)
    if cd.size != n_v * n_h:
        raise ValueError(f"{path}: expected {n_v * n_h} candela values, got {cd.size}")
    cd = cd.reshape(n_h, n_v) * mult * ballast
    return IESProfile(va, ha, cd, float(cd.max()) if cd.size else 0.0)


# ------------------------------------------------------------------------------------ glTF
_COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_NORM = {np.int8: 127.0, np.uint8: 255.0, np.int16: 32767.0, np.uint16: 65535.0}


def _attr(obj, name: str):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


class _Gltf:
    def __init__(self, path: str):
        import pygltflib
        self.dir = os.path.dirname(path)
        with open(path, "rb") as fh:
            data = fh.read()
        if data[:4] == b"glTF":
            self.g = pygltflib.GLTF2.load_from_bytes(data)
            n = int.from_bytes(data[12:16], "little")
            self.raw = json.loads(data[20:20 + n])
        else:
            self.g = pygltflib.GLTF2.from_json(data.decode("utf-8"))
            self.raw = json.loads(data)
        self._buffers: dict[int, bytes] = {}
        self._images: dict[tuple[int, bool], np.ndarray | None] = {}

    def uri_bytes(self, uri: str) -> bytes:
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        with open(os.path.join(self.dir, urllib.parse.unquote(uri)), "rb") as fh:
            return fh.read()

    def buffer(self, i: int) -> bytes:
        if i not in self._buffers:
            b = self.g.buffers[i]
            self._buffers[i] = self.g.binary_blob() if not b.uri else self.uri_bytes(b.uri)
        return self._buffers[i]

    def view(self, bv_index: int, offset: int, dtype, count: int, nc: int,
             matrix_size: int = 0) -> np.ndarray:
        bv = self.g.bufferViews[bv_index]
        dt = np.dtype(dtype).newbyteorder("<")
        if matrix_size:
            column_stride = (matrix_size * dt.itemsize + 3) // 4 * 4
            stride = bv.byteStride or matrix_size * column_stride
            return np.ndarray((count, matrix_size, matrix_size), dtype=dt,
                              buffer=self.buffer(bv.buffer), offset=(bv.byteOffset or 0) + offset,
                              strides=(stride, column_stride, dt.itemsize)).reshape(count, nc).copy()
        stride = bv.byteStride or dt.itemsize * nc
        return np.ndarray((count, nc), dtype=dt, buffer=self.buffer(bv.buffer),
                          offset=(bv.byteOffset or 0) + offset, strides=(stride, dt.itemsize)).copy()

    def accessor(self, i: int) -> np.ndarray:
        a = self.g.accessors[i]
        dt, nc = _COMP[a.componentType], _NCOMP[a.type]
        matrix_size = int(a.type[-1]) if a.type.startswith("MAT") else 0
        arr = np.zeros((a.count, nc), dt) if a.bufferView is None else \
            self.view(a.bufferView, a.byteOffset or 0, dt, a.count, nc, matrix_size)
        if a.sparse is not None and a.sparse.count:
            s = a.sparse
            idx = self.view(s.indices.bufferView, s.indices.byteOffset or 0, _COMP[s.indices.componentType],
                            s.count, 1)[:, 0]
            arr[idx] = self.view(s.values.bufferView, s.values.byteOffset or 0, dt, s.count, nc, matrix_size)
        if a.normalized and dt in _NORM:
            return np.maximum(arr.astype(np.float32) / _NORM[dt], -1.0)
        return arr

    def image(self, tex_index: int, srgb: bool) -> np.ndarray | None:
        tex = self.g.textures[tex_index]
        ext = tex.extensions or {}
        src = tex.source
        if "EXT_texture_webp" in ext:
            src = ext["EXT_texture_webp"].get("source", src)
        if src is None:
            log.warning("glTF texture %d has no decodable source (%s)", tex_index, ", ".join(ext) or "none")
            return None
        key = (src, srgb)
        if key not in self._images:
            img = self.g.images[src]
            if img.bufferView is not None:
                bv = self.g.bufferViews[img.bufferView]
                off = bv.byteOffset or 0
                data = self.buffer(bv.buffer)[off:off + bv.byteLength]
            else:
                data = self.uri_bytes(img.uri)
            self._images[key] = decode_image(data, srgb)
        return self._images[key]

    def material(self, m) -> MaterialSpec:
        p: dict = {}
        tx: dict = {}
        pbr = m.pbrMetallicRoughness
        bcf = (pbr.baseColorFactor if pbr else None) or [1.0, 1.0, 1.0, 1.0]
        p["baseColor"] = tuple(float(v) for v in bcf)
        p["metallic"] = float(pbr.metallicFactor if pbr and pbr.metallicFactor is not None else 1.0)
        p["roughness"] = float(pbr.roughnessFactor if pbr and pbr.roughnessFactor is not None else 1.0)
        p["emissive"] = _rgba(m.emissiveFactor or [0.0, 0.0, 0.0])
        p["opacity"] = 1.0
        p["alphaMode"] = (m.alphaMode or "OPAQUE").lower()
        p["alphaCutoff"] = float(m.alphaCutoff if m.alphaCutoff is not None else 0.5)
        p["doubleSided"] = bool(m.doubleSided)
        ext = m.extensions or {}
        e = ext.get("KHR_materials_emissive_strength")
        if e is not None:
            p["emissiveStrength"] = float(e.get("emissiveStrength", 1.0))
        p["unlit"] = "KHR_materials_unlit" in ext
        simple = {
            "KHR_materials_clearcoat": [("clearcoatFactor", "clearcoat", 0.0),
                                        ("clearcoatRoughnessFactor", "clearcoatRoughness", 0.0)],
            "KHR_materials_transmission": [("transmissionFactor", "transmission", 0.0)],
            "KHR_materials_ior": [("ior", "ior", 1.5)],
            "KHR_materials_volume": [("thicknessFactor", "thickness", 0.0)],
            "KHR_materials_sheen": [("sheenRoughnessFactor", "sheenRoughness", 0.0)],
            "KHR_materials_specular": [("specularFactor", "specular", 1.0)],
            "KHR_materials_iridescence": [("iridescenceFactor", "iridescence", 0.0),
                                          ("iridescenceIor", "iridescenceIor", 1.3)],
            "KHR_materials_anisotropy": [("anisotropyStrength", "anisotropy", 0.0)],
            "KHR_materials_dispersion": [("dispersion", "dispersion", 0.0)],
        }
        for name, fields in simple.items():
            if name in ext:
                for src, dst, dflt in fields:
                    p[dst] = float(ext[name].get(src, dflt))
        if "KHR_materials_volume" in ext:
            v = ext["KHR_materials_volume"]
            p["attenuationColor"] = _rgba(v.get("attenuationColor", [1.0, 1.0, 1.0]))
            if "attenuationDistance" in v:
                p["attenuationDistance"] = float(v["attenuationDistance"])
        if "KHR_materials_sheen" in ext:
            p["sheenColor"] = _rgba(ext["KHR_materials_sheen"].get("sheenColorFactor", [0.0, 0.0, 0.0]))
        if "KHR_materials_specular" in ext:
            p["specularColor"] = _rgba(ext["KHR_materials_specular"].get("specularColorFactor", [1.0, 1.0, 1.0]))
        if "KHR_materials_anisotropy" in ext:
            p["anisotropyRotation"] = math.degrees(float(ext["KHR_materials_anisotropy"].get("anisotropyRotation", 0.0)))
        if m.normalTexture is not None:
            p["normalScale"] = float(m.normalTexture.scale if m.normalTexture.scale is not None else 1.0)
        if m.occlusionTexture is not None:
            p["occlusionStrength"] = float(m.occlusionTexture.strength if m.occlusionTexture.strength is not None else 1.0)
        slots = [("baseColorMap", pbr.baseColorTexture if pbr else None, True),
                 ("metallicRoughnessMap", pbr.metallicRoughnessTexture if pbr else None, False),
                 ("normalMap", m.normalTexture, False), ("occlusionMap", m.occlusionTexture, False),
                 ("emissiveMap", m.emissiveTexture, True)]
        for key, info, srgb in slots:
            if info is not None and info.index is not None:
                img = self.image(info.index, srgb)
                if img is not None:
                    tx[key] = img
                tt = (info.extensions or {}).get("KHR_texture_transform", {})
                p.setdefault("mapUV", {})[key] = dict(texCoord=tt.get("texCoord", info.texCoord or 0),
                    offset=tuple(tt.get("offset", [0., 0.])), rotation=float(tt.get("rotation", 0.)),
                    scale=tuple(tt.get("scale", [1., 1.])))
                sampler_i = self.g.textures[info.index].sampler
                sampler = self.g.samplers[sampler_i] if sampler_i is not None else None
                p.setdefault("mapSamplers", {})[key] = dict(
                    wrapS=sampler.wrapS or 10497 if sampler is not None else 10497,
                    wrapT=sampler.wrapT or 10497 if sampler is not None else 10497,
                    minFilter=sampler.minFilter or 9987 if sampler is not None else 9987,
                    magFilter=sampler.magFilter or 9729 if sampler is not None else 9729)
        return MaterialSpec(params=p, textures=tx, name=m.name or "")


def _strip_fan(idx: np.ndarray, mode: int) -> np.ndarray:
    n = len(idx) - 2
    if n <= 0:
        return np.zeros((0, 3), np.uint32)
    k = np.arange(n)
    if mode == 6:
        return np.stack([np.full(n, idx[0]), idx[k + 1], idx[k + 2]], 1)
    odd = k % 2 == 1
    a, b = np.where(odd, idx[k + 1], idx[k]), np.where(odd, idx[k], idx[k + 1])
    return np.stack([a, b, idx[k + 2]], 1)


def _load_gltf(path: str) -> Model:
    L = _Gltf(path)
    g = L.g
    materials = [L.material(m) for m in g.materials or []]
    root_ext = g.extensions or {}
    variant_names = [v.get("name", str(i)) for i, v in
                     enumerate(root_ext.get("KHR_materials_variants", {}).get("variants", []))]
    meshes = []
    for mi, mesh in enumerate(g.meshes or []):
        prims = []
        for pi, pr in enumerate(mesh.primitives):
            mode = 4 if pr.mode is None else pr.mode
            at = L.raw["meshes"][mi]["primitives"][pi]["attributes"]
            pos_i = _attr(at, "POSITION")
            if pos_i is None:
                continue
            pos = L.accessor(pos_i).astype(np.float32)
            idx = L.accessor(pr.indices)[:, 0].astype(np.uint32) if pr.indices is not None else \
                np.arange(len(pos), dtype=np.uint32)
            tris = (idx.reshape(-1, 3) if mode == 4 else _strip_fan(idx, mode).astype(np.uint32)) if mode >= 4 else idx

            def get(name: str, dtype=np.float32):
                i = _attr(at, name)
                return None if i is None else L.accessor(i).astype(dtype)
            cols = get("COLOR_0")
            if cols is not None and cols.shape[1] == 3:
                cols = np.concatenate([cols, np.ones((len(cols), 1), np.float32)], 1)
            sets = sorted(int(name[7:]) for name in at if name.startswith("JOINTS_"))
            joints = weights = None
            if sets:
                joints, weights = _skin_weights(
                    np.concatenate([get(f"JOINTS_{i}", np.uint32) for i in sets], axis=1),
                    np.concatenate([get(f"WEIGHTS_{i}") for i in sets], axis=1))
            prim = Primitive(positions=pos, indices=tris, normals=get("NORMAL"), uvs=get("TEXCOORD_0"),
                             tangents=get("TANGENT"), colors=cols, joints=joints,
                             weights=weights,
                             material=materials[pr.material] if pr.material is not None else
                             MaterialSpec(params={"baseColor": (1., 1., 1., 1.), "metallic": 1., "roughness": 1.}))
            prim.uv_sets = {int(name[9:]): get(name) for name in at if name.startswith("TEXCOORD_")}
            prim.mode = 4 if mode >= 4 else mode
            if mode < 4 and prim.normals is None:
                # glTF point/line primitives without normals are unlit.
                from dataclasses import replace
                base = prim.material or MaterialSpec(params={"metallic": 1., "roughness": 1.})
                prim.material = replace(base, params=dict(base.params, unlit=True))
            for tgt in pr.targets or []:
                tp, tn, tt = _attr(tgt, "POSITION"), _attr(tgt, "NORMAL"), _attr(tgt, "TANGENT")
                prim.morph_positions.append(L.accessor(tp).astype(np.float64) if tp is not None
                                            else np.zeros((len(pos), 3)))
                prim.morph_normals.append(L.accessor(tn).astype(np.float64) if tn is not None
                                          else np.zeros((len(pos), 3)))
                prim.morph_tangents.append(L.accessor(tt).astype(np.float64) if tt is not None
                                           else np.zeros((len(pos), 3)))
            if prim.normals is None:
                prim.tangents = None             # glTF requires ignoring tangents without normals
                prim.morph_tangents = []
                if prim.mode == 4:
                    # Give every face its own vertices so normals are flat both
                    # at rest and after morphing, without changing vertex data.
                    flat = prim.indices.reshape(-1)
                    for attr in ("positions", "uvs", "colors", "joints", "weights"):
                        values = getattr(prim, attr)
                        if values is not None:
                            setattr(prim, attr, values[flat])
                    prim.uv_sets = {k: v[flat] for k, v in prim.uv_sets.items()}
                    prim.morph_positions = [v[flat] for v in prim.morph_positions]
                    prim.morph_normals = []
                    prim.indices = np.arange(len(flat), dtype=np.uint32).reshape(-1, 3)
            for mp in ((pr.extensions or {}).get("KHR_materials_variants") or {}).get("mappings", []):
                for vi in mp.get("variants", []):
                    if vi < len(variant_names):
                        prim.variants[variant_names[vi]] = materials[mp["material"]]
            prims.append(prim)
        meshes.append(prims)

    nodes = []
    for n in g.nodes or []:
        nd = Node(name=n.name or "", children=list(n.children or []), mesh=n.mesh, skin=n.skin)
        if n.matrix is not None and not np.allclose(n.matrix, np.eye(4).ravel()):
            nd.matrix = np.asarray(n.matrix, dtype=np.float64).reshape(4, 4).T
        if n.translation is not None:
            nd.translation = np.asarray(n.translation, dtype=np.float64)
        if n.rotation is not None:
            nd.rotation = np.asarray(n.rotation, dtype=np.float64)
        if n.scale is not None:
            nd.scale = np.asarray(n.scale, dtype=np.float64)
        w = L.raw["nodes"][len(nodes)].get("weights")        # pygltflib drops node.weights
        if w is None and n.mesh is not None:
            w = g.meshes[n.mesh].weights
        nd.weights = None if w is None else np.asarray(w, dtype=np.float64)
        nodes.append(nd)
    if g.scenes:
        roots = list(g.scenes[g.scene or 0].nodes or [])
    else:
        child = {c for n in nodes for c in n.children}
        roots = [i for i in range(len(nodes)) if i not in child]

    skins = []
    for s in g.skins or []:
        ibm = L.accessor(s.inverseBindMatrices).astype(np.float64).reshape(-1, 4, 4).transpose(0, 2, 1) \
            if s.inverseBindMatrices is not None else np.tile(np.eye(4), (len(s.joints), 1, 1))
        skins.append(Skin(joints=list(s.joints), inverse_bind=ibm))

    clips = []
    for ai, an in enumerate(g.animations or []):
        chans = []
        for ch in an.channels:
            if ch.target is None or ch.target.node is None:
                continue
            smp = an.samplers[ch.sampler]
            times = L.accessor(smp.input)[:, 0].astype(np.float64)
            interp = (smp.interpolation or "LINEAR").upper()
            vals = L.accessor(smp.output).astype(np.float64)
            rows = len(times) * (3 if interp == "CUBICSPLINE" else 1)
            chans.append(Channel(node=ch.target.node, path=ch.target.path, times=times,
                                 values=vals.reshape(rows, -1), interpolation=interp))
        dur = max((float(c.times[-1]) for c in chans if len(c.times)), default=0.0)
        clips.append(Clip(name=an.name or str(ai), channels=chans, duration=dur))
    return Model(nodes=nodes, roots=roots, meshes=meshes, skins=skins, clips=clips, variants=variant_names)


# ------------------------------------------------------------------------------------ trimesh
def _load_trimesh(path: str) -> Model:
    import trimesh
    scene = trimesh.load(path, force="scene", process=False)
    model = Model()
    for node_name in sorted(scene.graph.nodes_geometry):
        transform, geom_name = scene.graph[node_name]
        geom = scene.geometry[geom_name]
        if not isinstance(geom, trimesh.Trimesh) or not len(geom.faces):
            log.warning("%s: skipping non-triangle geometry %r", path, geom_name)
            continue
        prim = Primitive(positions=np.asarray(geom.vertices, np.float32),
                         indices=np.asarray(geom.faces, np.uint32),
                         normals=np.asarray(geom.vertex_normals, np.float32))
        vis = geom.visual
        if vis.kind == "texture":
            if vis.uv is not None and len(vis.uv) == len(prim.positions):
                uv = np.asarray(vis.uv, np.float32)
                prim.uvs = np.stack([uv[:, 0], 1.0 - uv[:, 1]], 1)
            prim.material = _trimesh_material(vis.material)
        elif vis.kind == "vertex":
            vc = np.asarray(vis.vertex_colors, np.float32) / 255.0
            prim.colors = np.concatenate([srgb_to_linear(vc[:, :3]), vc[:, 3:]], 1)
        elif vis.kind == "face" and hasattr(vis, "main_color"):
            c = np.asarray(vis.main_color, np.float64) / 255.0
            prim.material = MaterialSpec(params={"baseColor": _lin_rgb(c) + (float(c[3]),)})
        model.nodes.append(Node(name=str(node_name), matrix=np.asarray(transform, np.float64), mesh=len(model.meshes)))
        model.roots.append(len(model.nodes) - 1)
        model.meshes.append([prim])
    return model


def _trimesh_material(mat) -> MaterialSpec:
    pbr = mat.to_pbr() if hasattr(mat, "to_pbr") else mat
    p: dict = {}
    tx: dict = {}
    bcf = getattr(pbr, "baseColorFactor", None)
    if bcf is not None:
        c = np.asarray(bcf, np.float64)
        c = c / 255.0 if np.issubdtype(np.asarray(bcf).dtype, np.integer) else c
        p["baseColor"] = _lin_rgb(c) + (float(c[3]) if len(c) > 3 else 1.0,)
    for src, dst in (("metallicFactor", "metallic"), ("roughnessFactor", "roughness")):
        v = getattr(pbr, src, None)
        if v is not None:
            p[dst] = float(v)
    img = getattr(pbr, "baseColorTexture", None)
    if img is not None:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        tx["baseColorMap"] = decode_image(buf.getvalue(), srgb=True)
    return MaterialSpec(params=p, textures=tx, name=getattr(mat, "name", "") or "")


# ------------------------------------------------------------------------------------ splats
_PLY_T = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "i2", "int16": "i2",
          "ushort": "u2", "uint16": "u2", "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
          "float": "f4", "float32": "f4", "double": "f8", "float64": "f8"}
_SPLAT_PROPS = {"x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
                "rot_0", "rot_1", "rot_2", "rot_3"}


def _ply_header(path: str) -> tuple[str, list, int]:
    """(format, [(element, count, [(prop, type)] or None when it has list props)], data offset)."""
    with open(path, "rb") as fh:
        head = fh.read(65536)
    end = head.find(b"end_header")
    if not head.startswith(b"ply") or end < 0:
        raise ValueError(f"{path}: not a PLY file")
    off = head.index(b"\n", end) + 1
    fmt, elems = "", []
    for ln in head[:end].decode("latin-1").splitlines():
        t = ln.split()
        if not t:
            continue
        if t[0] == "format":
            fmt = t[1]
        elif t[0] == "element":
            elems.append([t[1], int(t[2]), []])
        elif t[0] == "property" and elems:
            if t[1] == "list" or elems[-1][2] is None:
                elems[-1][2] = None
            else:
                elems[-1][2].append((t[2], _PLY_T[t[1]]))
    return fmt, elems, off


def _ply_is_splat(path: str) -> bool:
    _, elems, _ = _ply_header(path)
    v = next((e for e in elems if e[0] == "vertex"), None)
    return v is not None and v[2] is not None and _SPLAT_PROPS <= {n for n, _ in v[2]}


def _load_ply_splats(path: str) -> Splats:
    fmt, elems, off = _ply_header(path)
    with open(path, "rb") as fh:
        data = fh.read()
    vi = next(i for i, e in enumerate(elems) if e[0] == "vertex")
    _, count, props = elems[vi]
    if fmt == "ascii":
        lines = data[off:].decode("latin-1").split("\n")
        skip = sum(e[1] for e in elems[:vi])
        arr = np.loadtxt(lines[skip:skip + count], dtype=np.float64, ndmin=2)
        v = {n: arr[:, k] for k, (n, _) in enumerate(props)}
    else:
        bo = "<" if fmt == "binary_little_endian" else ">"
        for name, n, pr in elems[:vi]:
            if pr is None:
                raise ValueError(f"{path}: variable-size element {name!r} before vertex data")
            off += n * np.dtype([(p, bo + t) for p, t in pr]).itemsize
        rec = np.frombuffer(data, dtype=np.dtype([(p, bo + t) for p, t in props]), count=count, offset=off)
        v = {n: rec[n].astype(np.float64) for n, _ in props}
    col = np.clip(0.5 + 0.28209479 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 1), 0.0, 1.0)
    alpha = 1.0 / (1.0 + np.exp(-v["opacity"]))
    q = np.stack([v["rot_1"], v["rot_2"], v["rot_3"], v["rot_0"]], 1)
    q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-20)
    return Splats(positions=np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float32),
                  scales=np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], 1)).astype(np.float32),
                  rotations=q.astype(np.float32),
                  colors=np.concatenate([srgb_to_linear(col), alpha[:, None]], 1).astype(np.float32))


def _load_splat(path: str) -> Splats:
    rec = np.fromfile(path, dtype=np.dtype([("p", "<f4", 3), ("s", "<f4", 3), ("c", "u1", 4), ("r", "u1", 4)]))
    c = rec["c"].astype(np.float32) / 255.0
    r = (rec["r"].astype(np.float64) - 128.0) / 128.0
    q = r[:, [1, 2, 3, 0]]
    q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-20)
    return Splats(positions=rec["p"].astype(np.float32), scales=rec["s"].astype(np.float32),
                  rotations=q.astype(np.float32),
                  colors=np.concatenate([srgb_to_linear(c[:, :3]), c[:, 3:]], 1).astype(np.float32))


# ------------------------------------------------------------------------------------ USD
def _gf(m) -> np.ndarray:
    """Gf.Matrix4d (row-vector convention) -> column-vector 4x4."""
    return np.array(m, dtype=np.float64).reshape(4, 4).T


def _usd_asset_bytes(p: str) -> bytes | None:
    if "[" in p and p.endswith("]"):
        outer, inner = p[:-1].split("[", 1)
        with zipfile.ZipFile(outer) as z:
            return z.read(inner)
    if p and os.path.exists(p):
        with open(p, "rb") as fh:
            return fh.read()
    return None


class _Usd:
    def __init__(self, path: str):
        from pxr import Usd, UsdGeom
        self.stage = Usd.Stage.Open(path)
        self.model = Model()
        self.channels: list[Channel] = []
        self.start = self.stage.GetStartTimeCode() if self.stage.HasAuthoredTimeCodeRange() else 0.0
        self.tps = self.stage.GetTimeCodesPerSecond() or 24.0
        self.materials: dict[str, MaterialSpec | None] = {}
        self.skels: dict[str, tuple[list[str], list[int], np.ndarray]] = {}   # path -> (joints, nodes, ibm)
        self.skinned: list = []                                 # (prim, node index) bound to skeletons
        self.blends: dict = {}
        self.reset_roots: list[int] = []                        # prims with resetXformStack
        roots: list[int] = []
        for child in self.stage.GetPseudoRoot().GetChildren():
            roots += self.visit(child)
        for prim, ni in self.skinned:
            self.bind_skin(prim, ni)
        roots += self.reset_roots
        if UsdGeom.GetStageUpAxis(self.stage) == UsdGeom.Tokens.z:
            s = math.sqrt(0.5)
            self.model.nodes.append(Node(name="__zup_to_yup", rotation=np.array([-s, 0.0, 0.0, s]), children=roots))
            roots = [len(self.model.nodes) - 1]
        self.model.roots = roots
        if self.channels:
            self.model.clips.append(Clip("default", self.channels, max(float(c.times[-1]) for c in self.channels)))

    def add_node(self, nd: Node) -> int:
        self.model.nodes.append(nd)
        return len(self.model.nodes) - 1

    def visit(self, prim) -> list[int]:
        """Returns the node indices created for prim (itself, or its children when not Xformable)."""
        from pxr import Usd, UsdGeom, UsdSkel
        if not prim.IsA(UsdGeom.Imageable):
            return []
        img = UsdGeom.Imageable(prim)
        if img.GetVisibilityAttr().Get() == UsdGeom.Tokens.invisible or \
                img.GetPurposeAttr().Get() in (UsdGeom.Tokens.guide, UsdGeom.Tokens.proxy):
            return []
        if not prim.IsA(UsdGeom.Xformable):
            return [i for c in prim.GetChildren() for i in self.visit(c)]
        xf = UsdGeom.Xformable(prim)
        ni = self.add_node(Node(name=prim.GetName()))
        nd = self.model.nodes[ni]
        local = _gf(xf.GetLocalTransformation(Usd.TimeCode.Default()))
        times = sorted({t for op in xf.GetOrderedXformOps() for t in op.GetTimeSamples()})
        if times:
            ts = np.array([(t - self.start) / self.tps for t in times])
            matrices = np.array([_gf(xf.GetLocalTransformation(Usd.TimeCode(t))).ravel() for t in times])
            self.channels.append(Channel(ni, "matrix", ts, matrices))
            nd.matrix = local
            nd.matrix_sampler = lambda t, xf=xf: _gf(xf.GetLocalTransformation(Usd.TimeCode(self.start + t * self.tps)))
        elif not np.allclose(local, np.eye(4)):
            nd.matrix = local
        if prim.IsA(UsdGeom.Mesh):
            nd.mesh = len(self.model.meshes)
            self.model.meshes.append(self.mesh(prim))
            self.bind_blends(prim, ni)
            if UsdSkel.BindingAPI(prim).GetSkeleton():
                self.skinned.append((prim, ni))
        elif prim.IsA(UsdSkel.Skeleton):
            self.skeleton(prim, ni)
        kids = [i for c in prim.GetChildren() for i in self.visit(c)]
        nd.children.extend(kids)
        if xf.GetResetXformStack():
            self.reset_roots.append(ni)
            return []
        return [ni]

    # --- geometry
    def mesh(self, prim) -> list[Primitive]:
        from pxr import UsdGeom
        m = UsdGeom.Mesh(prim)
        pts = np.asarray(m.GetPointsAttr().Get() or [], np.float64).reshape(-1, 3)
        counts = np.asarray(m.GetFaceVertexCountsAttr().Get() or [], np.int64)
        fvi = np.asarray(m.GetFaceVertexIndicesAttr().Get() or [], np.int64)
        tri_c = _fan(counts)                                   # triangle corners (face-vertex indices)
        tri_f = _fan_faces(counts)
        if m.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded:
            tri_c = tri_c[:, ::-1]
        face_of_corner = np.repeat(np.arange(len(counts)), counts)
        pv = UsdGeom.PrimvarsAPI(prim)

        def primvar(name: str):
            p = pv.GetPrimvar(name)
            if not p or not p.HasValue():
                return None
            v = p.ComputeFlattened()
            return None if v is None else (np.asarray(v, np.float64), p.GetInterpolation())
        normals = primvar("normals")
        if normals is None and m.GetNormalsAttr().HasValue():
            normals = (np.asarray(m.GetNormalsAttr().Get(), np.float64), m.GetNormalsInterpolation())
        st = primvar("st")
        if st is None:
            st = next((primvar(p.GetPrimvarName()) for p in pv.GetPrimvarsWithValues()
                       if str(p.GetTypeName()) in ("texCoord2f[]", "float2[]")), None)
        dc = primvar("displayColor")
        do = primvar("displayOpacity")
        joints = self.skin_influences(prim, len(pts))
        morph_pos, morph_nrm = self.blend_shapes(prim, len(pts))
        attrs = {"n": normals, "uv": st, "c": dc if dc is not None and dc[1] != "constant" else None}
        unindex = any(a is not None and a[1] in ("faceVarying", "uniform") for a in attrs.values())

        def expand(a):
            val, interp = a
            if interp == "constant":
                return np.repeat(val.reshape(1, -1), len(fvi) if unindex else len(pts), 0)
            if interp in ("vertex", "varying"):
                return val[fvi] if unindex else val
            return val if interp == "faceVarying" else val[face_of_corner]
        out = {k: expand(a) for k, a in attrs.items() if a is not None}
        pos = pts[fvi] if unindex else pts
        corner = tri_c if unindex else fvi[tri_c]
        if joints is not None and unindex:
            joints = (joints[0][fvi], joints[1][fvi])
        if unindex:
            morph_pos = [a[fvi] for a in morph_pos]
            morph_nrm = [a[fvi] for a in morph_nrm]
        uvs = None if "uv" not in out else \
            np.stack([out["uv"][:, 0], 1.0 - out["uv"][:, 1]], 1).astype(np.float32)
        colors = None
        if "c" in out:
            alpha = np.ones((len(out["c"]), 1))
            colors = np.concatenate([out["c"][:, :3], alpha], 1).astype(np.float32)
        # material assignment per face: GeomSubsets of family materialBind, else the prim binding
        face_mat = np.zeros(len(counts), np.int64)
        mats = [self.bound_material(prim)]
        for sub in UsdGeom.Subset.GetGeomSubsets(m, UsdGeom.Tokens.face, "materialBind"):
            idx = np.asarray(sub.GetIndicesAttr().Get() or [], np.int64)
            face_mat[idx[idx < len(counts)]] = len(mats)
            mats.append(self.bound_material(sub.GetPrim()) or mats[0])
        if mats[0] is None and dc is not None and dc[1] == "constant":
            a = float(do[0][0]) if do is not None else 1.0
            mats[0] = MaterialSpec(params={"baseColor": tuple(float(x) for x in dc[0][0][:3]) + (a,)})
        prims = []
        for mi, mat in enumerate(mats):
            sel = face_mat[tri_f] == mi
            if not sel.any():
                continue
            prims.append(Primitive(
                positions=pos.astype(np.float32), indices=corner[sel].astype(np.uint32),
                normals=_unit_f32(out["n"]) if "n" in out else None, uvs=uvs, colors=colors,
                joints=None if joints is None else joints[0], weights=None if joints is None else joints[1],
                morph_positions=morph_pos, morph_normals=morph_nrm,
                material=mat if mat is not None else (mats[0] if mi else None)))
        return prims

    def blend_shapes(self, prim, n_points):
        from pxr import UsdSkel
        binding = UsdSkel.BindingAPI(prim)
        query = UsdSkel.BlendShapeQuery(binding)
        if not query or not query.GetNumBlendShapes():
            return [], []
        self.blends[str(prim.GetPath())] = query
        indices = query.ComputeBlendShapePointIndices()
        positions, normals = [], []
        for i, offsets in enumerate(query.ComputeSubShapePointOffsets()):
            bi = query.GetBlendShapeIndex(i)
            selection = np.asarray(indices[bi], np.int64)
            if not len(selection):
                selection = np.arange(n_points)
            pos, nrm = np.zeros((n_points, 3)), np.zeros((n_points, 3))
            if len(offsets):
                pos[selection] = np.asarray(offsets)
                inbetween = query.GetInbetween(i)
                normal_offsets = inbetween.GetNormalOffsets() if inbetween else query.GetBlendShape(bi).GetNormalOffsetsAttr().Get()
                if normal_offsets is not None and len(normal_offsets):
                    nrm[selection] = np.asarray(normal_offsets)
            positions.append(pos)
            normals.append(nrm)
        return positions, normals

    def bind_blends(self, prim, ni):
        from pxr import Usd, UsdSkel
        query = self.blends.get(str(prim.GetPath()))
        if query is None:
            return
        binding = UsdSkel.BindingAPI(prim)
        names = list(binding.GetBlendShapesAttr().Get() or [])
        nd = self.model.nodes[ni]
        nd.weights = np.zeros(query.GetNumBlendShapes())

        def map_weights(weights):
            values = np.zeros(query.GetNumBlendShapes(), np.float32)
            values[:min(len(weights), len(values))] = weights[:len(values)]
            w, _, subindices = query.ComputeSubShapeWeights(values.tolist())
            out = np.zeros(query.GetNumSubShapes())
            out[np.asarray(subindices, np.int64)] = w
            return out
        nd.weight_mapper = map_weights
        anim_prim = binding.GetInheritedAnimationSource()
        if not anim_prim:
            skel = binding.GetInheritedSkeleton()
            anim_prim = UsdSkel.BindingAPI(skel).GetInheritedAnimationSource() if skel else None
        if not anim_prim:
            return
        anim = UsdSkel.Animation(anim_prim)
        attr = anim.GetBlendShapeWeightsAttr()
        if not attr.HasValue():
            return
        order = list(anim.GetBlendShapesAttr().Get() or [])
        times = attr.GetTimeSamples()
        vals = []
        for t in times or [None]:
            value = attr.Get(Usd.TimeCode.Default() if t is None else Usd.TimeCode(t))
            vals.append([value[order.index(n)] if n in order else 0. for n in names])
        if len(times) > 1:
            self.channels.append(Channel(ni, "weights", (np.asarray(times) - self.start) / self.tps,
                                         np.asarray(vals, np.float64)))
        else:
            nd.weights = np.asarray(vals[0], np.float64)

    # --- materials
    def bound_material(self, prim) -> MaterialSpec | None:
        from pxr import UsdShade
        mat = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
        if not mat:
            return None
        key = str(mat.GetPath())
        if key not in self.materials:
            self.materials[key] = self.preview_surface(mat)
        return self.materials[key]

    def preview_surface(self, mat) -> MaterialSpec | None:
        from pxr import UsdShade
        shader = mat.ComputeSurfaceSource()[0]
        if not shader or shader.GetIdAttr().Get() != "UsdPreviewSurface":
            return None
        p: dict = {"metallic": 0.0, "roughness": 0.5}
        tx: dict = {}
        mr: dict = {}

        def inp(name: str):
            i = shader.GetInput(name)
            if not i:
                return None, None
            if i.HasConnectedSource():
                src, out_name, _ = i.GetConnectedSource()
                s = UsdShade.Shader(src.GetPrim())
                if s.GetIdAttr().Get() == "UsdUVTexture":
                    f = s.GetInput("file")
                    ap = f.Get() if f else None
                    cs = s.GetInput("sourceColorSpace")
                    return None, (ap.resolvedPath or ap.path if ap else "", str(out_name),
                                  (cs.Get() if cs else "auto") != "raw")
                return None, None
            return i.Get(), None

        def texture(spec, srgb_ok: bool) -> np.ndarray | None:
            path, out_name, srgb = spec
            data = _usd_asset_bytes(path)
            if data is None:
                log.warning("USD texture %r not found", path)
                return None
            img = decode_image(data, srgb and srgb_ok)
            ch = {"r": 0, "g": 1, "b": 2, "a": 3}.get(out_name)
            return img if ch is None else img[..., ch]
        v, t = inp("diffuseColor")
        if v is not None:
            p["baseColor"] = _rgba(v)
        if t is not None and (img := texture(t, True)) is not None:
            tx["baseColorMap"] = img if img.ndim == 3 else _add_alpha(img)
            p["baseColor"] = (1.0, 1.0, 1.0, 1.0)
        v, t = inp("emissiveColor")
        if v is not None:
            p["emissive"] = _rgba(v)
        if t is not None and (img := texture(t, True)) is not None:
            tx["emissiveMap"] = img if img.ndim == 3 else _add_alpha(img)
            p["emissive"] = (1.0, 1.0, 1.0, 1.0)
        for name in ("metallic", "roughness"):
            v, t = inp(name)
            if v is not None:
                p[name] = float(v)
            if t is not None and (img := texture(t, False)) is not None:
                mr[name] = img if img.ndim == 2 else img[..., 0]
                p[name] = 1.0
        if mr:
            h = max(a.shape[0] for a in mr.values())
            w = max(a.shape[1] for a in mr.values())

            def fit(a):
                if a is None:
                    return np.ones((h, w), np.float32)
                return a[(np.arange(h) * a.shape[0]) // h][:, (np.arange(w) * a.shape[1]) // w]
            one = np.ones((h, w), np.float32)
            tx["metallicRoughnessMap"] = np.stack([one, fit(mr.get("roughness")), fit(mr.get("metallic")), one], -1)
        _, t = inp("normal")
        if t is not None and (img := texture(t, False)) is not None and img.ndim == 3:
            tx["normalMap"] = img
        _, t = inp("occlusion")
        if t is not None and (img := texture(t, False)) is not None:
            a = img if img.ndim == 2 else img[..., 0]
            tx["occlusionMap"] = np.stack([a, a, a, np.ones_like(a)], -1)
        for name in ("ior", "clearcoat", "clearcoatRoughness"):
            v, _ = inp(name)
            if v is not None:
                p[name] = float(v)
        op, _ = inp("opacity")
        thr, _ = inp("opacityThreshold")
        if op is not None:
            p["opacity"] = float(op)
            if thr is not None and float(thr) > 0:
                p["alphaMode"], p["alphaCutoff"] = "mask", float(thr)
            elif float(op) < 1.0:
                p["alphaMode"] = "blend"
        return MaterialSpec(params=p, textures=tx, name=mat.GetPrim().GetName())

    # --- skeletons
    def skeleton(self, prim, skel_node: int) -> None:
        from pxr import Usd, UsdSkel
        skel = UsdSkel.Skeleton(prim)
        joints = [str(j) for j in skel.GetJointsAttr().Get() or []]
        rest = [_gf(m) for m in skel.GetRestTransformsAttr().Get() or []]
        bind = [_gf(m) for m in skel.GetBindTransformsAttr().Get() or []]
        if not rest:                                          # rest defaults to bind (local-ised)
            rest = [np.eye(4)] * len(joints)
        nodes: list[int] = []
        for k, j in enumerate(joints):
            t, q, s = _decompose(rest[k])
            ni = self.add_node(Node(name=j.rsplit("/", 1)[-1], translation=t, rotation=q, scale=s))
            parent = j.rsplit("/", 1)[0] if "/" in j else None
            (self.model.nodes[nodes[joints.index(parent)]] if parent in joints else
             self.model.nodes[skel_node]).children.append(ni)
            nodes.append(ni)
        ibm = np.stack([np.linalg.inv(b) for b in bind]) if len(bind) == len(joints) and joints else \
            np.tile(np.eye(4), (len(joints), 1, 1))
        self.skels[str(prim.GetPath())] = (joints, nodes, ibm)
        anim_prim = UsdSkel.BindingAPI(prim).GetAnimationSource()
        if not anim_prim:
            return
        anim = UsdSkel.Animation(anim_prim)
        ajoints = [str(j) for j in anim.GetJointsAttr().Get() or []]
        amap = [nodes[joints.index(j)] if j in joints else -1 for j in ajoints]
        for attr, path in ((anim.GetTranslationsAttr(), "translation"), (anim.GetRotationsAttr(), "rotation"),
                           (anim.GetScalesAttr(), "scale")):
            if not attr.HasValue():
                continue
            times = attr.GetTimeSamples()
            vals = np.stack([np.array([[*q.GetImaginary(), q.GetReal()] for q in v]) if path == "rotation"
                             else np.asarray(v, np.float64)
                             for v in (attr.Get(Usd.TimeCode(t) if times else Usd.TimeCode.Default())
                                       for t in times or [0.0])])          # (k, n_joints, c)
            ts = np.array([(t - self.start) / self.tps for t in times])
            for ji, ni in enumerate(amap):
                if ni < 0:
                    continue
                if len(times) > 1:
                    self.channels.append(Channel(ni, path, ts, vals[:, ji]))
                else:                                          # constant value overrides the rest pose
                    setattr(self.model.nodes[ni], path, vals[0, ji].astype(np.float64))

    def skin_influences(self, prim, n_points: int):
        from pxr import UsdSkel
        b = UsdSkel.BindingAPI(prim)
        ji, jw = b.GetJointIndicesPrimvar(), b.GetJointWeightsPrimvar()
        if not ji or not jw or not ji.HasValue():
            return None
        es = ji.GetElementSize()
        idx = np.asarray(ji.ComputeFlattened(), np.int64).reshape(-1, es)
        w = np.asarray(jw.ComputeFlattened(), np.float64).reshape(-1, es)
        if ji.GetInterpolation() == "constant":
            idx, w = np.repeat(idx[:1], n_points, 0), np.repeat(w[:1], n_points, 0)
        return _skin_weights(idx, w)

    def bind_skin(self, prim, ni: int) -> None:
        from pxr import UsdSkel
        b = UsdSkel.BindingAPI(prim)
        skel = b.GetSkeleton()
        entry = self.skels.get(str(skel.GetPath())) if skel else None
        if entry is None:
            return
        names, nodes, ibm = entry
        gb = b.GetGeomBindTransformAttr()
        geom_bind = _gf(gb.Get()) if gb and gb.HasValue() else np.eye(4)
        joints_attr = b.GetJointsAttr()
        order = [names.index(str(j)) for j in joints_attr.Get()] if joints_attr and joints_attr.HasValue() \
            else list(range(len(names)))                    # mesh-local joint order when authored
        self.model.skins.append(Skin([nodes[k] for k in order], ibm[order] @ geom_bind))
        self.model.nodes[ni].skin = len(self.model.skins) - 1


def _unit_f32(v: np.ndarray) -> np.ndarray:
    ln = np.linalg.norm(v, axis=1, keepdims=True)
    return (v / np.where(ln > 1e-20, ln, 1.0)).astype(np.float32)


def _load_usd(path: str) -> Model:
    return _Usd(path).model


# ------------------------------------------------------------------------------------ FBX
class _Keep:
    """Keep-alive proxy for ufbx 0.0.5 objects. The binding caches one wrapper per element without
    owning it: once a wrapper is freed, fetching the same element again returns a dangling object
    (segfault at the next GC). Everything fetched through this proxy stays referenced in `pool`
    for the duration of the load; arguments passed to methods are unwrapped."""
    __slots__ = ("_o", "_pool")

    def __init__(self, o, pool: list):
        self._o, self._pool = o, pool
        pool.append(o)

    def _wrap(self, v):
        return v if v is None or isinstance(v, (int, float, str, bytes, tuple)) else _Keep(v, self._pool)

    def __getattr__(self, k: str):
        v = getattr(self._o, k)
        if callable(v):
            return lambda *a, **kw: self._wrap(v(*[x._o if isinstance(x, _Keep) else x for x in a], **kw))
        return self._wrap(v)

    def __len__(self) -> int:
        return len(self._o)

    def __getitem__(self, i):
        return self._wrap(self._o[i])

    def __iter__(self):
        return (self._wrap(x) for x in self._o)


def _ints(seq) -> np.ndarray:
    return np.fromiter(iter(seq), np.int64, count=len(seq))


def _ufbx_mat(m) -> np.ndarray:
    return np.array([[m.c0.x, m.c1.x, m.c2.x, m.c3.x], [m.c0.y, m.c1.y, m.c2.y, m.c3.y],
                     [m.c0.z, m.c1.z, m.c2.z, m.c3.z], [0.0, 0.0, 0.0, 1.0]])


def _fbx_texture(tex, base_dir: str, srgb: bool) -> np.ndarray | None:
    if tex is None:
        return None
    data = bytes(tex.content or b"")
    if not data:
        for p in (tex.absolute_filename, os.path.join(base_dir, tex.relative_filename or ""),
                  os.path.join(base_dir, os.path.basename(tex.filename or ""))):
            if p and os.path.isfile(p):
                with open(p, "rb") as fh:
                    data = fh.read()
                break
    if not data:
        log.warning("FBX texture %r not found", tex.filename)
        return None
    return decode_image(data, srgb)


def _fbx_material(mat, base_dir: str) -> MaterialSpec:
    pbr = mat.pbr
    bc, bf = pbr.base_color.value_vec4, pbr.base_factor.value_vec4.x if pbr.base_factor.has_value else 1.0
    ec, ef = pbr.emission_color.value_vec4, pbr.emission_factor.value_vec4.x if pbr.emission_factor.has_value else 1.0
    op = pbr.opacity.value_vec4.x if pbr.opacity.has_value else 1.0
    p = {"baseColor": (bc.x * bf, bc.y * bf, bc.z * bf, 1.0),
         "metallic": float(pbr.metalness.value_vec4.x) if pbr.metalness.has_value else 0.0,
         "roughness": float(pbr.roughness.value_vec4.x) if pbr.roughness.has_value else 0.5,
         "emissive": (ec.x * ef, ec.y * ef, ec.z * ef, 1.0), "opacity": float(op)}
    if op < 1.0:
        p["alphaMode"] = "blend"
    tx = {}
    for key, mm, srgb in (("baseColorMap", pbr.base_color, True), ("emissiveMap", pbr.emission_color, True),
                          ("normalMap", pbr.normal_map, False)):
        img = _fbx_texture(mm.texture, base_dir, srgb) if mm.texture is not None else None
        if img is not None:
            tx[key] = img
    return MaterialSpec(params=p, textures=tx, name=mat.name)


def _vattr(va, corners: np.ndarray, width: int) -> np.ndarray | None:
    """Per-corner values of a ufbx vertex attribute (None when absent)."""
    if not va.exists:
        return None
    vals = np.array([tuple(v) for v in va.values], np.float64).reshape(-1, width)
    return vals[_ints(va.indices)[corners]]


def _fbx_mesh(mesh, mats: dict, base_dir: str) -> list[Primitive]:
    faces = list(mesh.faces)
    counts = np.array([f.num_indices for f in faces], np.int64)
    begins = np.array([f.index_begin for f in faces], np.int64)
    corners_of_face = [np.arange(b, b + c) for b, c in zip(begins, counts)]
    all_corners = np.concatenate(corners_of_face) if faces else np.zeros(0, np.int64)
    tri = _fan(counts)                        # into all_corners
    tri_f = _fan_faces(counts)
    tri_corner = all_corners[tri]
    pts_of_corner = _ints(mesh.vertex_indices)
    pos = _vattr(mesh.vertex_position, np.arange(mesh.num_indices), 3)
    nrm = _vattr(mesh.vertex_normal, np.arange(mesh.num_indices), 3)
    uv = _vattr(mesh.vertex_uv, np.arange(mesh.num_indices), 2)
    col = _vattr(mesh.vertex_color, np.arange(mesh.num_indices), 4)
    # skinning per point
    joints = weights = None
    if len(mesh.skin_deformers):
        sd = mesh.skin_deformers[0]
        sw = list(sd.weights)
        k = max(1, sd.max_weights_per_vertex)
        ji = np.zeros((mesh.num_vertices, k), np.int64)
        jw = np.zeros((mesh.num_vertices, k), np.float64)
        for vi, sv in enumerate(sd.vertices):
            for n in range(min(sv.num_weights, k)):
                w = sw[sv.weight_begin + n]
                ji[vi, n], jw[vi, n] = w.cluster_index, w.weight
        joints, weights = _skin_weights(ji, jw)
    # blend shapes per point
    morphs = []
    for bd in mesh.blend_deformers:
        for ch in bd.channels:
            shape = ch.target_shape
            d = np.zeros((mesh.num_vertices, 3))
            if shape is not None and shape.num_offsets:
                d[_ints(shape.offset_vertices)] = \
                    np.array([tuple(v) for v in shape.position_offsets], np.float64)
            morphs.append(d)
    face_mat = _ints(mesh.face_material) if len(mesh.face_material) else np.zeros(len(faces), np.int64)
    prims = []
    for mi in np.unique(face_mat[tri_f]) if len(tri_f) else []:
        tc = tri_corner[face_mat[tri_f] == mi]
        used = np.unique(tc.ravel())
        key_cols = [pts_of_corner[used][:, None].astype(np.float64)] + \
            [a[used] for a in (nrm, uv, col) if a is not None]
        _, first, inv = np.unique(np.concatenate(key_cols, 1), axis=0, return_index=True, return_inverse=True)
        rep = used[first]                     # representative corner per output vertex
        remap = np.zeros(mesh.num_indices, np.int64)
        remap[used] = inv.ravel()
        pts = pts_of_corner[rep]
        mat = mesh.materials[int(mi)] if int(mi) < len(mesh.materials) else None
        if mat is not None and mat.element_id not in mats:
            mats[mat.element_id] = _fbx_material(mat, base_dir)
        prim = Primitive(
            positions=pos[rep].astype(np.float32), indices=remap[tc].astype(np.uint32),
            normals=None if nrm is None else _unit_f32(nrm[rep]),
            uvs=None if uv is None else np.stack([uv[rep, 0], 1.0 - uv[rep, 1]], 1).astype(np.float32),
            colors=None if col is None else col[rep].astype(np.float32),
            joints=None if joints is None else joints[pts], weights=None if weights is None else weights[pts],
            morph_positions=[d[pts] for d in morphs],
            material=None if mat is None else mats[mat.element_id])
        prims.append(prim)
    return prims


def _load_fbx(path: str) -> Model:
    import ufbx
    pool: list = []
    scene = _Keep(ufbx.load_file(
        path, target_axes=ufbx.axes_right_handed_y_up, space_conversion=ufbx.SpaceConversion.TRANSFORM_ROOT,
        geometry_transform_handling=ufbx.GeometryTransformHandling.HELPER_NODES,
        inherit_mode_handling=ufbx.InheritModeHandling.HELPER_NODES, generate_missing_normals=True,
        load_external_files=True, ignore_missing_external_files=True), pool)
    base_dir = os.path.dirname(path)
    model = Model()
    mats: dict = {}
    mesh_index: dict[int, int] = {}
    for n in scene.nodes:
        lt = n.local_transform
        nd = Node(name=n.name, translation=np.array(tuple(lt.translation), np.float64),
                  rotation=np.array(tuple(lt.rotation), np.float64), scale=np.array(tuple(lt.scale), np.float64),
                  children=[c.typed_id for c in n.children])
        if n.mesh is not None:
            me = n.mesh
            if me.element_id not in mesh_index:
                mesh_index[me.element_id] = len(model.meshes)
                model.meshes.append(_fbx_mesh(me, mats, base_dir))
            nd.mesh = mesh_index[me.element_id]
            if len(me.skin_deformers):
                cl = me.skin_deformers[0].clusters
                model.skins.append(Skin(joints=[c.bone_node.typed_id if c.bone_node is not None else n.typed_id
                                                for c in cl],
                                        inverse_bind=np.stack([_ufbx_mat(c.geometry_to_bone) for c in cl])
                                        if len(cl) else np.eye(4)[None]))
                nd.skin = len(model.skins) - 1
            chans = [c for bd in me.blend_deformers for c in bd.channels]
            if chans:
                nd.weights = np.array([c.weight for c in chans], np.float64)
        model.nodes.append(nd)
    model.roots = [scene.root_node.typed_id]
    fps = scene.settings.frames_per_second or 30.0
    for stack in scene.anim_stacks:
        chans: list[Channel] = []
        baked = _Keep(ufbx.bake_anim(scene._o, stack.anim._o, trim_start_time=True), pool)
        for bn in baked.nodes:
            for keys, path in ((bn.translation_keys, "translation"), (bn.rotation_keys, "rotation"),
                               (bn.scale_keys, "scale")):
                if len(keys) > 1:
                    chans.append(Channel(bn.typed_id, path, np.array([k.time for k in keys]),
                                         np.array([tuple(k.value) for k in keys], np.float64)))
        t0, t1 = stack.anim.time_begin, stack.anim.time_end
        times = np.arange(t0, t1 + 0.5 / fps, 1.0 / fps) if t1 > t0 else np.array([t0])
        for ni, n in enumerate(scene.nodes):
            bchans = [c for bd in (n.mesh.blend_deformers if n.mesh is not None else []) for c in bd.channels]
            if bchans and len(times) > 1:
                w = np.array([[c.evaluate_blend_weight(stack.anim, float(t)) for c in bchans] for t in times])
                if np.ptp(w, axis=0).max() > 0:
                    chans.append(Channel(ni, "weights", times - t0, w))
        dur = max((float(c.times[-1]) for c in chans), default=0.0)
        model.clips.append(Clip(name=stack.name, channels=chans, duration=dur))
    return model
