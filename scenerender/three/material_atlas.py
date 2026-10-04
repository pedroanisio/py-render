"""Independent RGBA extension maps, sharing the core metallic/roughness sampler.

Metadata lives beside the texels to avoid per-map uniform and sampler limits.
Addresses are stored as separate X/Y coordinates, exact in float32 even when the
atlas contains more than 2**24 texels. Each source keeps its own full mip chain.
"""
from dataclasses import dataclass
import math

import numpy as np

from .texture_atlas import MIP_LEVELS, SCALAR_MAP_KEYS
from .texture_image import mip_chain

EXTENSION_MAP_KEYS = ('clearcoatNormalMap', 'transmissionMap', 'thicknessMap',
                      'sheenColorMap', 'sheenRoughnessMap', 'specularMap',
                      'specularColorMap', 'iridescenceMap', 'iridescenceThicknessMap',
                      'anisotropyMap')
ATLAS_MAP_KEYS = SCALAR_MAP_KEYS + ('metallicRoughnessMap',) + EXTENSION_MAP_KEYS
META_STRIDE = MIP_LEVELS + 3


@dataclass
class MaterialAtlas:
    sources: tuple
    pixels: np.ndarray
    levels: np.ndarray

    @property
    def nbytes(self):
        return self.pixels.nbytes


def pack_material_maps(images, samplers, max_size):
    images, samplers = tuple(images), tuple(samplers)
    if len(images) != len(samplers):
        raise ValueError('Every material map must have a sampler record')
    count = len(images)
    levels = np.zeros((count, MIP_LEVELS, 4), np.int32)
    headers = np.zeros((count, META_STRIDE, 4), np.float32)
    pieces, address = [], count*META_STRIDE
    for i, (image, sampler) in enumerate(zip(images, samplers)):
        headers[i, 1] = [sampler.get(name, default) for name, default in
                         [('wrapS', 10497), ('wrapT', 10497), ('minFilter', 9987), ('magFilter', 9729)]]
        headers[i, 2] = sampler.get('border', (0., 0., 0., 0.))
        if image is None:
            continue
        a = np.ascontiguousarray(image, np.float32)
        if a.ndim != 3 or a.shape[2] != 4 or min(a.shape[:2]) == 0:
            raise ValueError('Material texture must be a nonempty RGBA image')
        headers[i, 0, :2] = a.shape[1], a.shape[0]
        for level, a in enumerate(mip_chain(image)):
            if level >= MIP_LEVELS:
                raise ValueError(f'Material texture exceeds {MIP_LEVELS} mip levels')
            levels[i, level] = address, a.shape[1], a.shape[0], 0
            pieces.append(a.reshape(-1, 4))
            address += a.shape[0]*a.shape[1]
            if address > max_size*max_size or address >= 2**31:
                raise ValueError(f'Material atlas exceeds GPU capacity ({max_size} x {max_size} texels)')
            if a.shape[:2] == (1, 1):
                headers[i, 0, 2] = level
                break
    if address > max_size*max_size:
        raise ValueError(f'Material atlas exceeds GPU capacity ({max_size} x {max_size} texels)')
    width = min(max_size, max(1, math.ceil(math.sqrt(address))))
    pixels = np.zeros((max(1, math.ceil(address/width)), width, 4), np.float32)
    for i in range(count):
        for level in range(MIP_LEVELS):
            start, w, h, _ = levels[i, level]
            headers[i, level+3] = start % width, start // width, w, h
    flat = pixels.reshape(-1, 4)
    flat[:count*META_STRIDE] = headers.reshape(-1, 4)
    if pieces:
        np.concatenate(pieces, out=flat[count*META_STRIDE:address])
    return MaterialAtlas(images, pixels, levels)


GLSL = """
uniform int u_hasExtensionMaps;
vec4 extensionMeta(sampler2D atlas, int map, int row) {
    int address = map*META_STRIDE_VALUE+row, width = textureSize(atlas, 0).x;
    return texelFetch(atlas, ivec2(address%width, address/width), 0);
}
bool extensionHas(sampler2D atlas, int map) {
    return u_hasExtensionMaps == 1 && extensionMeta(atlas, map, 0).x > 0.0;
}
vec4 extensionTap(sampler2D atlas, int map, int level, ivec2 pixel, ivec2 wrap) {
    ivec4 mip = ivec4(extensionMeta(atlas, map, level+3));
    if ((wrap.x == 33069 && (pixel.x < 0 || pixel.x >= mip.z)) ||
        (wrap.y == 33069 && (pixel.y < 0 || pixel.y >= mip.w))) return extensionMeta(atlas, map, 2);
    int width = textureSize(atlas, 0).x;
    int address = mip.y*width+mip.x + mapIndex(pixel.y, mip.w, wrap.y)*mip.z + mapIndex(pixel.x, mip.z, wrap.x);
    return texelFetch(atlas, ivec2(address%width, address/width), 0);
}
vec4 extensionLevel(sampler2D atlas, int map, int level, vec2 uv, ivec2 wrap, bool linear_) {
    for (int axis=0; axis<2; ++axis) {
        if (wrap[axis] == 10497) uv[axis] = fract(uv[axis]);
        else if (wrap[axis] == 33648) uv[axis] = 1.0-abs(mod(uv[axis], 2.0)-1.0);
        else uv[axis] = clamp(uv[axis], -1.0, 2.0);
    }
    vec2 size = extensionMeta(atlas, map, level+3).zw;
    vec2 p = uv*size-(linear_ ? .5 : 0.0);
    ivec2 index = ivec2(floor(p));
    if (!linear_) return extensionTap(atlas, map, level, index, wrap);
    vec2 f = fract(p);
    return mix(mix(extensionTap(atlas,map,level,index,wrap), extensionTap(atlas,map,level,index+ivec2(1,0),wrap),f.x),
               mix(extensionTap(atlas,map,level,index+ivec2(0,1),wrap), extensionTap(atlas,map,level,index+ivec2(1,1),wrap),f.x),f.y);
}
vec4 extensionSample(sampler2D atlas, int map, vec2 uv) {
    if (!extensionHas(atlas, map)) return vec4(1.0);
    vec4 info = extensionMeta(atlas, map, 0);
    ivec4 settings = ivec4(extensionMeta(atlas, map, 1));
    float lod = textureQueryLod(atlas, uv*info.xy/vec2(textureSize(atlas, 0))).y;
    if (lod <= 0.0) return extensionLevel(atlas, map, 0, uv, settings.xy, settings.w == 9729);
    if (settings.z == 9728 || settings.z == 9729)
        return extensionLevel(atlas, map, 0, uv, settings.xy, settings.z == 9729);
    lod = clamp(lod, 0.0, info.z);
    bool linear_ = settings.z == 9985 || settings.z == 9987;
    if (settings.z == 9984 || settings.z == 9985)
        return extensionLevel(atlas, map, max(0, int(ceil(lod+.5))-1), uv, settings.xy, linear_);
    int lo = int(floor(lod)), hi = min(lo+1, int(info.z));
    return mix(extensionLevel(atlas,map,lo,uv,settings.xy,linear_),
               extensionLevel(atlas,map,hi,uv,settings.xy,linear_),fract(lod));
}
""".replace('META_STRIDE_VALUE', str(META_STRIDE))
