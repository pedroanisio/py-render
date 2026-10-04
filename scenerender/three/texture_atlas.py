"""Scalar material maps in a float atlas, with independent mip chains and samplers.

The atlas stores original level-zero samples verbatim. Lower levels integrate
pixel areas, including odd dimensions; no map is resized to another map's size.
The shader fetches individual texels, so atlas adjacency cannot leak into a map.
"""
from dataclasses import dataclass
import math

import numpy as np

from .texture_image import _reduce_axis, mip_chain

SCALAR_MAP_KEYS = ('iorMap', 'clearcoatMap', 'clearcoatRoughnessMap')
MIP_LEVELS = 32


@dataclass
class ScalarAtlas:
    sources: tuple
    pixels: np.ndarray
    info: np.ndarray
    levels: np.ndarray

    @property
    def nbytes(self):
        return self.pixels.nbytes


def pack_scalar_maps(images, max_size):
    """Pack independent R-channel pyramids; fail explicitly if the GPU cannot store them."""
    images = tuple(images)
    info = np.zeros((len(images), 4), np.int32)
    levels = np.zeros((len(images), MIP_LEVELS, 4), np.int32)
    pieces, address = [], 0
    for i, image in enumerate(images):
        if image is None:
            continue
        a = np.ascontiguousarray(image[..., 0] if image.ndim == 3 else image, np.float32)
        if a.ndim != 2 or min(a.shape) == 0:
            raise ValueError('Scalar texture must be a nonempty image')
        info[i, :2] = a.shape[::-1]
        for level, source in enumerate(mip_chain(image)):
            if level >= MIP_LEVELS:
                raise ValueError(f'Scalar texture exceeds {MIP_LEVELS} mip levels')
            a = np.ascontiguousarray(source[..., 0] if source.ndim == 3 else source, np.float32)
            levels[i, level] = address, a.shape[1], a.shape[0], 0
            pieces.append(a.reshape(-1))
            address += a.size
            if address > max_size*max_size or address >= 2**31:
                raise ValueError(f'Scalar material atlas exceeds GPU capacity ({max_size} x {max_size} texels)')
            if a.shape == (1, 1):
                info[i, 2] = level
                break
    width = min(max_size, max(1, math.ceil(math.sqrt(address))))
    height = max(1, math.ceil(address/width))
    pixels = np.zeros((height, width), np.float32)
    if pieces:
        np.concatenate(pieces, out=pixels.ravel()[:address])
    return ScalarAtlas(images, pixels, info, levels)


GLSL = """
const int SCALAR_MIP_LEVELS = MIP_LEVELS_VALUE;
uniform int u_hasScalarMaps;
uniform ivec4 u_scalarInfo[3];  // original width, height, last mip, unused
uniform ivec4 u_scalarLevels[3*SCALAR_MIP_LEVELS]; // address, width, height, unused
uniform ivec4 u_scalarSampler[3]; // S/T wrap, min/mag filter
uniform vec4 u_scalarBorder;  // R-channel borders; final component unused
int scalarIndex(int index, int size, int wrap) {
    // scalarLevel has already folded UVs; index >= -1. GLSL leaves a negative
    // dividend's remainder undefined, so make it nonnegative before modulo.
    if (wrap == 10497) return (index + size) % size;
    if (wrap == 33648) { int n = size*2; int p = (index + n) % n; return min(p, n-1-p); }
    return clamp(index, 0, size-1);
}
float scalarTap(sampler2D atlas, int map, int level, ivec2 pixel) {
    ivec4 mip = u_scalarLevels[map*SCALAR_MIP_LEVELS+level];
    ivec2 wrap = u_scalarSampler[map].xy;
    if ((wrap.x == 33069 && (pixel.x < 0 || pixel.x >= mip.y)) ||
        (wrap.y == 33069 && (pixel.y < 0 || pixel.y >= mip.z))) return u_scalarBorder[map];
    int address = mip.x + scalarIndex(pixel.y, mip.z, wrap.y)*mip.y + scalarIndex(pixel.x, mip.y, wrap.x);
    int width = textureSize(atlas, 0).x;
    return texelFetch(atlas, ivec2(address % width, address / width), 0).r;
}
float scalarLevel(sampler2D atlas, int map, int level, vec2 uv, bool linear_) {
    ivec2 wrap = u_scalarSampler[map].xy;
    // Reduce coordinates before converting to integer texel indices, preserving
    // large repeated coordinates and avoiding overflow for clamped/black axes.
    for (int axis = 0; axis < 2; ++axis) {
        if (wrap[axis] == 10497) uv[axis] = fract(uv[axis]);
        else if (wrap[axis] == 33648) uv[axis] = 1.0-abs(mod(uv[axis], 2.0)-1.0);
        else uv[axis] = clamp(uv[axis], -1.0, 2.0);
    }
    vec2 p = uv*vec2(u_scalarLevels[map*SCALAR_MIP_LEVELS+level].yz);
    if (!linear_) return scalarTap(atlas, map, level, ivec2(floor(p)));
    p -= .5;
    ivec2 lo = ivec2(floor(p)); vec2 f = fract(p);
    return mix(mix(scalarTap(atlas, map, level, lo), scalarTap(atlas, map, level, lo+ivec2(1, 0)), f.x),
               mix(scalarTap(atlas, map, level, lo+ivec2(0, 1)), scalarTap(atlas, map, level, lo+ivec2(1, 1)), f.x), f.y);
}
float scalarSample(sampler2D atlas, int map, vec2 uv) {
    if (u_hasScalarMaps == 0 || u_scalarInfo[map].x == 0) return 1.0;
    vec2 size = vec2(u_scalarInfo[map].xy);
    // Restore this image's footprint before querying the unclamped LOD. The
    // atlas dimensions must not change its minification or mip transitions.
    float lod = textureQueryLod(atlas, uv*size/vec2(textureSize(atlas, 0))).y;
    int minimum = u_scalarSampler[map].z, maximum = u_scalarSampler[map].w;
    // Use the unconditional zero crossover permitted by GL 4.1 section 3.8.12.
    float boundary = 0.0;
    if (lod <= boundary) return scalarLevel(atlas, map, 0, uv, maximum == 9729);
    if (minimum == 9728 || minimum == 9729) return scalarLevel(atlas, map, 0, uv, minimum == 9729);
    lod = clamp(lod, 0.0, float(u_scalarInfo[map].z));
    bool linear_ = minimum == 9985 || minimum == 9987;
    if (minimum == 9984 || minimum == 9985) return scalarLevel(atlas, map, max(0, int(ceil(lod+.5))-1), uv, linear_);
    int lo = int(floor(lod)), hi = min(lo+1, u_scalarInfo[map].z);
    return mix(scalarLevel(atlas, map, lo, uv, linear_), scalarLevel(atlas, map, hi, uv, linear_), fract(lod));
}
""".replace('MIP_LEVELS_VALUE', str(MIP_LEVELS))
