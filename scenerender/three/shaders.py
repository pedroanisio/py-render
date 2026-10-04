"""GLSL sources of the 3D renderer (GL 4.1 core). See renderer.py for the uniforms' meaning."""
from .texture_atlas import GLSL as SCALAR_SAMPLE
from .material_atlas import GLSL as EXTENSION_SAMPLE

LIGHT_DATA = """
vec4 lightDatum(int i, int field) {
    int width = textureSize(t_lights, 0).x, address = i*8+field;
    return texelFetch(t_lights, ivec2(address % width, address / width), 0);
}
int u_lt(int i) { return int(lightDatum(i, 0).x); }
int u_lsh(int i) { return int(lightDatum(i, 0).y); }
int u_lies(int i) { vec2 p = lightDatum(i, 0).zw; return int(p.x)+int(p.y)*65536; }
vec3 u_lc(int i) { return lightDatum(i, 1).xyz; }
vec3 u_lp(int i) { return lightDatum(i, 2).xyz; }
vec3 u_ld(int i) { return lightDatum(i, 3).xyz; }
vec3 u_lr(int i) { return lightDatum(i, 4).xyz; }
vec3 u_lu(int i) { return lightDatum(i, 5).xyz; }
vec2 u_ls(int i) { return lightDatum(i, 6).xy; }
vec2 u_laff(int i) { return lightDatum(i, 6).zw; }
vec4 u_lparam(int i) { return lightDatum(i, 7); }
vec4 shadowDatum(int s, int field) {
    int width = textureSize(t_shadowData, 0).x, address = s*14+field;
    return texelFetch(t_shadowData, ivec2(address % width, address / width), 0);
}
mat4 u_shMat(int s) { return mat4(shadowDatum(s, 0), shadowDatum(s, 1), shadowDatum(s, 2), shadowDatum(s, 3)); }
vec4 u_shP(int s) { return shadowDatum(s, 4); }
vec4 u_shQ(int s) { return shadowDatum(s, 5); }
vec3 u_shPos(int s) { return shadowDatum(s, 6).xyz; }
int u_shKind(int s) { return int(shadowDatum(s, 6).w); }
vec3 u_shDir(int s) { return shadowDatum(s, 7).xyz; }
"""

IES_SAMPLE = """
float iesAtan2(float y, float x) {
    // Some GL drivers approximate inverse trig to only ~1e-4 radians. Reduce
    // atan's Taylor series to [-tan(pi/8), tan(pi/8)] for float-precision angles,
    // so sub-degree candela knots are not shifted by the driver's approximation.
    float ax = abs(x), ay = abs(y);
    float z = min(ax, ay) / max(max(ax, ay), 1e-30);
    bool reduce = z > 0.414213562373095;
    if (reduce) z = (z-1.0)/(z+1.0);
    float t = z*z;
    float p = -1.0/19.0;
    p = 1.0/17.0 + t*p; p = -1.0/15.0 + t*p;
    p = 1.0/13.0 + t*p; p = -1.0/11.0 + t*p;
    p = 1.0/9.0 + t*p; p = -1.0/7.0 + t*p;
    p = 1.0/5.0 + t*p; p = -1.0/3.0 + t*p;
    float a = z*(1.0+t*p) + (reduce ? 0.7853981633974483 : 0.0);
    if (ay > ax) a = 1.5707963267948966-a;
    if (x < 0.0) a = 3.141592653589793-a;
    return y < 0.0 ? -a : a;
}
// Packed LM-63: nv, nh, type, vertical angles, horizontal angles, candela/max.
float iesDatum(int address) {
    int width = textureSize(t_ies, 0).x;
    return texelFetch(t_ies, ivec2(address % width, address / width), 0).r;
}
int iesInterval(int start, int count, float angle) {
    int low = 0, high = count;
    for (int step = 0; step < 32 && low < high; ++step) {
        int mid = low + (high - low) / 2;
        if (iesDatum(start + mid) <= angle) low = mid + 1;
        else high = mid;
    }
    return max(0, low - 1);
}
float iesSample(int base, vec3 direction) {
    if (base < 0) return 1.0;
    int nv = int(iesDatum(base)), nh = int(iesDatum(base+1)), type = int(iesDatum(base+2));
    int vs = base+3, hs = vs+nv, cs = hs+nh;
    float v0 = iesDatum(vs), v1 = iesDatum(vs+nv-1);
    float h0 = iesDatum(hs), h1 = iesDatum(hs+nh-1);
    // direction components are photometric horizontal, up, and nadir.
    // atan of both components is better conditioned near the poles than acos.
    // It also avoids the low-accuracy acos/asin approximations on some drivers.
    float v = degrees(iesAtan2(length(direction.xy), direction.z));
    float h = degrees(iesAtan2(direction.y, direction.x));
    if (type == 3) { v -= 90.0; h = -h; }
    else if (type == 2) {
        v = degrees(iesAtan2(direction.x, length(direction.yz)));
        h = degrees(iesAtan2(-direction.y, direction.z));
        if (v0 >= 0.0) v = abs(v);
    }
    if (type != 1 && h0 >= 0.0) h = abs(h);
    if (v < v0-0.0001 || v > v1+0.0001) return 0.0;
    if (nh == 1) h = h0;
    else if (type == 1) {
        h = mod(h, 360.0);
        if (h1 <= 90.000001) { h = min(h, 360.0-h); h = min(h, 180.0-h); }
        else if (h0 >= 89.999999 && h1 <= 270.000001) {
            if (h < 90.0) h = 180.0-h;
            else if (h > 270.0) h = 540.0-h;
        }
        else if (h1 <= 180.000001) h = min(h, 360.0-h);
        else h = mod(h-h0, 360.0)+h0;
    }
    else if (h < h0-0.0001 || h > h1+0.0001) return 0.0;
    int vi = iesInterval(vs, nv, v), vj = min(vi+1, nv-1);
    int hi = iesInterval(hs, nh, h), hj = min(hi+1, nh-1);
    float vf = clamp((v-iesDatum(vs+vi))/max(iesDatum(vs+vj)-iesDatum(vs+vi), 1e-12), 0.0, 1.0);
    float hf = clamp((h-iesDatum(hs+hi))/max(iesDatum(hs+hj)-iesDatum(hs+hi), 1e-12), 0.0, 1.0);
    float low = mix(iesDatum(cs+hi*nv+vi), iesDatum(cs+hi*nv+vj), vf);
    float high = mix(iesDatum(cs+hj*nv+vi), iesDatum(cs+hj*nv+vj), vf);
    return mix(low, high, hf);
}
"""

SHADOW_LOOKUP = """
const vec3 CF[6] = vec3[](vec3(1, 0, 0), vec3(-1, 0, 0), vec3(0, 1, 0), vec3(0, -1, 0), vec3(0, 0, 1), vec3(0, 0, -1));
const vec3 CU[6] = vec3[](vec3(0, 1, 0), vec3(0, 1, 0), vec3(0, 0, 1), vec3(0, 0, -1), vec3(0, 1, 0), vec3(0, 1, 0));
int cubeFace(vec3 v) {
    vec3 a = abs(v);
    if (a.x >= a.y && a.x >= a.z) return v.x > 0.0 ? 0 : 1;
    if (a.y >= a.z) return v.y > 0.0 ? 2 : 3;
    return v.z > 0.0 ? 4 : 5;
}
vec3 cubeShadowUV(vec3 v) {
    int f = cubeFace(v);
    vec3 F = CF[f]; vec3 U = CU[f]; vec3 R = cross(F, U);
    float d = dot(v, F);
    vec2 uv = vec2(dot(v, R), dot(v, U)) / d * 0.5 + 0.5;
    return vec3(uv, float(f));
}
float shTapFace(int s, vec2 uv, int face) {
    vec4 tile = shadowDatum(s, 8+face);
    ivec2 pixel = clamp(ivec2(floor(uv*tile.w)), ivec2(0), ivec2(int(tile.w)-1));
    return texelFetch(t_sh, ivec3(ivec2(tile.xy)+pixel, int(tile.z)), 0).r;
}
float shTap(int s, vec2 uv) { return shTapFace(s, uv, 0); }
float shTapCube(int s, vec3 direction) {
    vec3 uv = cubeShadowUV(direction);
    return shTapFace(s, uv.xy, int(uv.z));
}
"""

MAP_SAMPLE = """
int mapIndex(int pixel, int size, int wrap) {
    if (wrap == 10497) return (pixel + size) % size;
    if (wrap == 33648) { int n = size*2; int p = (pixel+n) % n; return min(p, n-1-p); }
    return clamp(pixel, 0, size-1);
}
vec4 mapTap(sampler2D image, int level, ivec2 pixel, ivec2 wrap, vec4 border) {
    ivec2 size = textureSize(image, level);
    if ((wrap.x == 33069 && (pixel.x < 0 || pixel.x >= size.x)) ||
        (wrap.y == 33069 && (pixel.y < 0 || pixel.y >= size.y))) return border;
    return texelFetch(image, ivec2(mapIndex(pixel.x, size.x, wrap.x), mapIndex(pixel.y, size.y, wrap.y)), level);
}
vec4 mapLevel(sampler2D image, int level, vec2 uv, ivec2 wrap, vec4 border, bool linear_) {
    // Fold before integer conversion, without introducing derivatives at folds.
    for (int axis = 0; axis < 2; ++axis) {
        if (wrap[axis] == 10497) uv[axis] = fract(uv[axis]);
        else if (wrap[axis] == 33648) uv[axis] = 1.0-abs(mod(uv[axis], 2.0)-1.0);
        else uv[axis] = clamp(uv[axis], -1.0, 2.0);
    }
    vec2 p = uv*vec2(textureSize(image, level));
    if (!linear_) return mapTap(image, level, ivec2(floor(p)), wrap, border);
    p -= .5;
    ivec2 lo = ivec2(floor(p)); vec2 f = fract(p);
    return mix(mix(mapTap(image, level, lo, wrap, border), mapTap(image, level, lo+ivec2(1, 0), wrap, border), f.x),
               mix(mapTap(image, level, lo+ivec2(0, 1), wrap, border), mapTap(image, level, lo+ivec2(1, 1), wrap, border), f.x), f.y);
}
vec4 mapSample(sampler2D image, vec2 uv, ivec4 settings, vec4 border) {
    // A zero min-filter selects the renderer's native anisotropic default.
    // Explicit imported samplers interpolate their float texels in the shader:
    // hardware filtering may quantize fractions even for an RGBA32F texture.
    if (settings.z == 0) return textureGrad(image, uv, dFdx(uv), dFdy(uv));
    float lod = textureQueryLod(image, uv).y;
    ivec2 wrap = settings.xy;
    int minimum = settings.z, maximum = settings.w;
    if (lod <= 0.0) return mapLevel(image, 0, uv, wrap, border, maximum == 9729);
    if (minimum == 9728 || minimum == 9729) return mapLevel(image, 0, uv, wrap, border, minimum == 9729);
    ivec2 size = textureSize(image, 0);
    int last = int(floor(log2(float(max(size.x, size.y)))));
    lod = clamp(lod, 0.0, float(last));
    bool linear_ = minimum == 9985 || minimum == 9987;
    if (minimum == 9984 || minimum == 9985)
        return mapLevel(image, max(0, int(ceil(lod+.5))-1), uv, wrap, border, linear_);
    int lo = int(floor(lod)), hi = min(lo+1, last);
    return mix(mapLevel(image, lo, uv, wrap, border, linear_), mapLevel(image, hi, uv, wrap, border, linear_), fract(lod));
}
vec4 mapSample(sampler2D image, vec2 uv, ivec4 settings) { return mapSample(image, uv, settings, vec4(0.0)); }
"""


MAIN_VS = """
#version 410
in vec3 in_pos; in vec3 in_nrm; in vec4 in_tan; in vec4 in_col;
in vec4 in_baseNrmUV; in vec4 in_mrOccUV; in vec4 in_emisRoughUV; in vec4 in_metalOpacityUV;
in vec4 in_iorCoatUV; in vec2 in_coatRoughUV;
in vec4 in_m0; in vec4 in_m1; in vec4 in_m2; in vec4 in_m3;
uniform mat4 u_view; uniform mat4 u_proj; uniform vec2 u_uvScale;
out vec3 v_wpos; out vec3 v_nrm; out vec2 v_uv; out vec4 v_tan; out vec4 v_col; out float v_vdepth;
out vec2 v_nrmuv; out vec2 v_mruv; out vec2 v_occuv; out vec2 v_emisuv;
out vec2 v_roughuv; out vec2 v_metaluv; out vec2 v_opacityuv;
out vec2 v_ioruv; out vec2 v_coatuv; out vec2 v_coatRoughuv;
uniform sampler2D t_extensionUV; uniform int u_hasExtensionUV;
out vec4 v_extensionUV[5];
vec2 extensionVertexUV(int map) {
    if (u_hasExtensionUV == 0) return vec2(0.0);
    int address = gl_VertexID*10+map, width = textureSize(t_extensionUV, 0).x;
    return texelFetch(t_extensionUV, ivec2(address%width, address/width), 0).rg*u_uvScale;
}
void main() {
    mat4 M = mat4(in_m0, in_m1, in_m2, in_m3);
    vec4 wp = M * vec4(in_pos, 1.0);
    mat3 M3 = mat3(M);
    v_wpos = wp.xyz;
    v_nrm = normalize(transpose(inverse(M3)) * in_nrm);
    vec3 t = M3 * in_tan.xyz;
    v_tan = vec4(length(t) > 1e-12 ? normalize(t) : vec3(1, 0, 0), in_tan.w * sign(determinant(M3)));
    v_uv = in_baseNrmUV.xy*u_uvScale; v_nrmuv = in_baseNrmUV.zw*u_uvScale;
    v_mruv = in_mrOccUV.xy*u_uvScale; v_occuv = in_mrOccUV.zw*u_uvScale;
    v_emisuv = in_emisRoughUV.xy*u_uvScale; v_roughuv = in_emisRoughUV.zw*u_uvScale;
    v_metaluv = in_metalOpacityUV.xy*u_uvScale; v_opacityuv = in_metalOpacityUV.zw*u_uvScale;
    v_ioruv = in_iorCoatUV.xy*u_uvScale; v_coatuv = in_iorCoatUV.zw*u_uvScale;
    v_coatRoughuv = in_coatRoughUV*u_uvScale;
    for (int i=0; i<5; ++i) v_extensionUV[i] = vec4(extensionVertexUV(i*2), extensionVertexUV(i*2+1));
    v_col = in_col;
    vec4 vp = u_view * wp;
    v_vdepth = -vp.z;
    gl_Position = u_proj * vp;
}
"""

MAIN_FS = """
#version 410
#define PI 3.14159265358979
in vec3 v_wpos; in vec3 v_nrm; in vec2 v_uv; in vec4 v_tan; in vec4 v_col; in float v_vdepth;
in vec2 v_nrmuv; in vec2 v_mruv; in vec2 v_occuv; in vec2 v_emisuv;
in vec2 v_roughuv; in vec2 v_metaluv; in vec2 v_opacityuv;
in vec2 v_ioruv; in vec2 v_coatuv; in vec2 v_coatRoughuv;
in vec4 v_extensionUV[5];
layout(location = 0) out vec4 o_color;
layout(location = 1) out vec4 o_depth;

uniform vec3 u_eye; uniform float u_exposure; uniform float u_upm; uniform float u_ortho; uniform vec3 u_camFwd;
// material
uniform vec4 u_base; uniform float u_metal; uniform float u_rough; uniform vec3 u_emis;
uniform int u_alphaMode; uniform float u_cutoff; uniform int u_unlit; uniform int u_doubleSided;
uniform float u_cc; uniform float u_ccRough; uniform float u_trans; uniform float u_ior; uniform float u_thick;
uniform vec3 u_attCol; uniform float u_attDist; uniform vec3 u_sheenCol; uniform float u_sheenRough;
uniform float u_spec; uniform vec3 u_specCol; uniform float u_irid; uniform float u_iridIor; uniform float u_iridThick;
uniform float u_iridThickMin; uniform float u_ccNormalScale;
uniform int u_usdSurface; uniform int u_specularWorkflow; uniform int u_usdTransparent;
uniform int u_normalWorld;
uniform int u_mxNormalMap; uniform vec2 u_mxNormalScale;
uniform float u_aniso; uniform float u_anisoRot; uniform float u_disp; uniform float u_nScale; uniform float u_modelScale;
uniform sampler2D t_base; uniform sampler2D t_nrm; uniform sampler2D t_mr; uniform sampler2D t_occ; uniform sampler2D t_emis;
uniform ivec4 u_baseSampler; uniform ivec4 u_nrmSampler; uniform ivec4 u_mrSampler; uniform ivec4 u_occSampler; uniform ivec4 u_emisSampler;
uniform int u_hasBase; uniform int u_hasNrm; uniform int u_hasMR; uniform int u_hasOcc; uniform int u_hasEmis;
uniform sampler2D t_rough; uniform sampler2D t_metal; uniform sampler2D t_opacity;
uniform int u_hasRough; uniform int u_hasMetal; uniform int u_hasOpacity;
uniform ivec4 u_roughSampler; uniform ivec4 u_metalSampler; uniform ivec4 u_opacitySampler;
uniform vec4 u_baseBorder; uniform vec4 u_nrmBorder; uniform vec4 u_mrBorder; uniform vec4 u_occBorder; uniform vec4 u_emisBorder;
uniform vec4 u_roughBorder; uniform vec4 u_metalBorder; uniform vec4 u_opacityBorder;
uniform float u_occStrength;
uniform float u_occlusion;
uniform int u_receiveShadow;
// lights
uniform int u_nl;
uniform sampler2D t_lights;
uniform sampler2D t_ies;
// shadows
uniform sampler2DArray t_sh;
uniform sampler2D t_shadowData;
// environment
uniform int u_ne; uniform sampler2DArray t_env; uniform sampler2D t_envData; uniform int u_envLevels;
uniform vec3 u_ambD; uniform vec3 u_ambS; uniform sampler2D t_lut;
// transmission
uniform int u_hasOpaque; uniform sampler2D t_opaque; uniform mat4 u_viewProj; uniform vec2 u_opaqueSize;

const mat3 XYZ_TO_REC709 = mat3(3.2404542, -0.9692660, 0.0556434, -1.5371385, 1.8760108, -0.2040259,
                                -0.4985314, 0.0415560, 1.0572252);
@MAP_SAMPLE@
@SCALAR_SAMPLE@
@EXTENSION_SAMPLE@
@LIGHT_DATA@
vec2 extensionUV(int map) {
    vec4 uv = v_extensionUV[map/2];
    return map%2 == 0 ? uv.xy : uv.zw;
}
float sq(float x) { return x * x; }
vec3 sq(vec3 x) { return x * x; }
float max3(vec3 v) { return max(v.x, max(v.y, v.z)); }

vec3 F_Schlick(vec3 f0, vec3 f90, float c) { return f0 + (f90 - f0) * pow(clamp(1.0 - c, 0.0, 1.0), 5.0); }
float F_Schlick(float f0, float c) { return f0 + (1.0 - f0) * pow(clamp(1.0 - c, 0.0, 1.0), 5.0); }
float D_GGX(float NdotH, float a) { float a2 = a * a; float f = NdotH * NdotH * (a2 - 1.0) + 1.0; return a2 / (PI * f * f); }
float V_GGX(float NdotL, float NdotV, float a) {
    float a2 = a * a;
    float gv = NdotL * sqrt(NdotV * NdotV * (1.0 - a2) + a2);
    float gl = NdotV * sqrt(NdotL * NdotL * (1.0 - a2) + a2);
    float s = gv + gl; return s > 0.0 ? 0.5 / s : 0.0;
}
float D_GGX_aniso(float NdotH, float TdotH, float BdotH, float at, float ab) {
    float a2 = at * ab; vec3 f = vec3(ab * TdotH, at * BdotH, a2 * NdotH);
    float w2 = a2 / dot(f, f); return a2 * w2 * w2 / PI;
}
float V_GGX_aniso(float NdotL, float NdotV, float BdotV, float TdotV, float TdotL, float BdotL, float at, float ab) {
    float gv = NdotL * length(vec3(at * TdotV, ab * BdotV, NdotV));
    float gl = NdotV * length(vec3(at * TdotL, ab * BdotL, NdotL));
    return clamp(0.5 / (gv + gl), 0.0, 1.0);
}
float D_Charlie(float r, float NdotH) {
    float a = max(r * r, 1e-4); float inv = 1.0 / a; float c2 = NdotH * NdotH;
    float s2 = max(1.0 - c2, 0.0078125); return (2.0 + inv) * pow(s2, inv * 0.5) / (2.0 * PI);
}
float V_Neubelt(float NdotL, float NdotV) { return clamp(1.0 / (4.0 * (NdotL + NdotV - NdotL * NdotV)), 0.0, 1.0); }
float V_Kelemen(float LdotH) { return 0.25 / max(LdotH * LdotH, 1e-4); }

// ---------------------------------------------------------------- iridescence (KHR_materials_iridescence)
vec3 fresnel0ToIor(vec3 f0) { vec3 s = sqrt(f0); return (vec3(1.0) + s) / (vec3(1.0) - s); }
vec3 iorToFresnel0(vec3 t, float i) { return sq((t - vec3(i)) / (t + vec3(i))); }
float iorToFresnel0(float t, float i) { return sq((t - i) / (t + i)); }
vec3 evalSensitivity(float OPD, vec3 shift) {
    float phase = 2.0 * PI * OPD * 1.0e-9;
    vec3 val = vec3(5.4856e-13, 4.4201e-13, 5.2481e-13);
    vec3 pos = vec3(1.6810e+06, 1.7953e+06, 2.2084e+06);
    vec3 var = vec3(4.3278e+09, 9.3046e+09, 6.6121e+09);
    vec3 xyz = val * sqrt(2.0 * PI * var) * cos(pos * phase + shift) * exp(-sq(phase) * var);
    xyz.x += 9.7470e-14 * sqrt(2.0 * PI * 4.5282e+09) * cos(2.2399e+06 * phase + shift[0]) * exp(-4.5282e+09 * sq(phase));
    xyz /= 1.0685e-7;
    return XYZ_TO_REC709 * xyz;
}
vec3 evalIridescence(float outside, float eta2, float cosT1, float thick, vec3 baseF0) {
    float iorI = mix(outside, eta2, smoothstep(0.0, 0.03, thick));
    float s2 = sq(outside / iorI) * (1.0 - sq(cosT1));
    float c2sq = 1.0 - s2;
    if (c2sq < 0.0) return vec3(1.0);
    float cosT2 = sqrt(c2sq);
    float R0 = iorToFresnel0(iorI, outside);
    float R12 = F_Schlick(R0, cosT1);
    float T121 = 1.0 - R12;
    float phi12 = iorI < outside ? PI : 0.0;
    float phi21 = PI - phi12;
    vec3 baseIor = fresnel0ToIor(clamp(baseF0, 0.0, 0.9999));
    vec3 R1 = iorToFresnel0(baseIor, iorI);
    vec3 R23 = R1 + (vec3(1.0) - R1) * pow(1.0 - cosT2, 5.0);
    vec3 phi23 = vec3(baseIor.x < iorI ? PI : 0.0, baseIor.y < iorI ? PI : 0.0, baseIor.z < iorI ? PI : 0.0);
    float OPD = 2.0 * iorI * thick * cosT2;
    vec3 phi = vec3(phi21) + phi23;
    vec3 R123 = clamp(R12 * R23, 1e-5, 0.9999);
    vec3 r123 = sqrt(R123);
    vec3 Rs = sq(T121) * R23 / (vec3(1.0) - R123);
    vec3 I = R12 + Rs;
    vec3 Cm = Rs - T121;
    for (int m = 1; m <= 2; ++m) { Cm *= r123; I += Cm * 2.0 * evalSensitivity(float(m) * OPD, float(m) * phi); }
    return max(I, vec3(0.0));
}

// ---------------------------------------------------------------- environment
vec2 dirToEquirect(vec3 d) {
    return vec2(atan(d.x, -d.z) / (2.0 * PI) + 0.5, 0.5 - asin(clamp(d.y, -1.0, 1.0)) / PI);
}
vec4 envDatum(int i, int field) {
    int width = textureSize(t_envData, 0).x, address = i*11+field;
    return texelFetch(t_envData, ivec2(address % width, address / width), 0);
}
vec3 envLevel(int layer, vec3 d, float lvl) {
    vec2 uv = dirToEquirect(d);
    ivec3 ts = textureSize(t_env, 0);
    float lh = float(ts.y) / float(u_envLevels);
    float v = clamp(uv.y, 0.5 / lh, 1.0 - 0.5 / lh);
    return texture(t_env, vec3(uv.x, (lvl + v) / float(u_envLevels), float(layer))).rgb;
}
// Dome environments only: ambient light (i < 0) is added apart (CONVENTIONS 5.20, see the lighting sum).
vec3 envSpec(int i, vec3 d, float rough) {
    if (i < 0) return vec3(0.0);
    vec4 meta = envDatum(i, 0);
    if (meta.w == 0.0) return vec3(0.0);
    int layer = int(meta.x);
    if (layer < 0) return envDatum(i, 1).rgb;
    float l = clamp(rough, 0.0, 1.0) * float(u_envLevels - 1);
    float l0 = floor(l); float l1 = min(l0 + 1.0, float(u_envLevels - 1));
    return mix(envLevel(layer, d, l0), envLevel(layer, d, l1), l - l0);
}
vec3 envIrradiance(int i, vec3 n) {
    if (i < 0) return vec3(0.0);
    vec4 meta = envDatum(i, 0);
    if (meta.z == 0.0) return vec3(0.0);
    if (meta.x < 0.0) return envDatum(i, 1).rgb * PI;
    vec3 e = envDatum(i, 2).rgb * 0.282095 + envDatum(i, 3).rgb * 0.488603 * n.y
           + envDatum(i, 4).rgb * 0.488603 * n.z + envDatum(i, 5).rgb * 0.488603 * n.x
           + envDatum(i, 6).rgb * 1.092548 * n.x * n.y + envDatum(i, 7).rgb * 1.092548 * n.y * n.z
           + envDatum(i, 8).rgb * 0.315392 * (3.0 * n.z * n.z - 1.0)
           + envDatum(i, 9).rgb * 1.092548 * n.x * n.z + envDatum(i, 10).rgb * 0.546274 * (n.x * n.x - n.y * n.y);
    return max(e, vec3(0.0));
}

// ---------------------------------------------------------------- shadows
const vec2 POISSON[16] = vec2[](vec2(-0.94201624, -0.39906216), vec2(0.94558609, -0.76890725), vec2(-0.09418410, -0.92938870),
    vec2(0.34495938, 0.29387760), vec2(-0.91588581, 0.45771432), vec2(-0.81544232, -0.87912464), vec2(-0.38277543, 0.27676845),
    vec2(0.97484398, 0.75648379), vec2(0.44323325, -0.97511554), vec2(0.53742981, -0.47373420), vec2(-0.26496911, -0.41893023),
    vec2(0.79197514, 0.19090188), vec2(-0.24188840, 0.99706507), vec2(-0.81409955, 0.91437590), vec2(0.19984126, 0.78641367),
    vec2(0.14383161, -0.14100790));
@SHADOW_LOOKUP@
float shadowAt(int s, vec3 P, vec3 N, vec3 L) {
    int kind = u_shKind(s);
    vec4 p = u_shP(s);      // bias (world), light radius (world / tan angle), near, far
    vec4 q = u_shQ(s);      // texel (uv), world per uv at unit distance (persp) or extent (ortho), map size, 0
    vec3 lp = u_shPos(s);
    float NdotL = clamp(dot(N, L), 0.0, 1.0);
    float d; vec2 uv; float wpu;
    vec3 T1 = normalize(abs(L.y) < 0.99 ? cross(L, vec3(0, 1, 0)) : cross(L, vec3(1, 0, 0)));
    vec3 T2 = cross(L, T1);
    // normal offset: one texel in world units at the receiver
    if (kind == 0) { wpu = q.y; }
    else { wpu = q.y * max(length(P - lp), 1e-3); }
    vec3 Pn = P + N * (q.x * wpu * 1.5 * (1.0 - NdotL * 0.5));
    if (kind == 0) { d = dot(Pn - lp, u_shDir(s)); vec4 c = u_shMat(s) * vec4(Pn, 1.0); uv = c.xy / c.w * 0.5 + 0.5; }
    else if (kind == 1) { d = length(Pn - lp); vec4 c = u_shMat(s) * vec4(Pn, 1.0); if (c.w <= 0.0) return 1.0; uv = c.xy / c.w * 0.5 + 0.5; }
    else { d = length(Pn - lp); uv = vec2(0.0); }
    if (kind != 2 && (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0)) return 1.0;
    float bias = p.x;
    float ang = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) * 2.0 * PI;
    mat2 rot = mat2(cos(ang), sin(ang), -sin(ang), cos(ang));
    // PCSS: blocker search on a regular 7x7 grid over the light's footprint, then rotated Poisson PCF
    float searchW = p.y > 0.0 ? (kind == 0 ? p.y * max(d, 1.0) : p.y) : 0.0;          // world units
    float filterW = q.x * wpu * 1.5;                                                  // world units (1.5 texels)
    if (p.y > 0.0) {
        float bsum = 0.0; float bn = 0.0;
        for (int j = 0; j < 7; ++j) {
            for (int i = 0; i < 7; ++i) {
                vec2 o = (vec2(float(i), float(j)) / 3.0 - 1.0) * searchW;
                if (dot(o, o) > searchW * searchW * 1.0001) continue;
                float z;
                if (kind == 2) z = shTapCube(s, Pn - lp + T1 * o.x + T2 * o.y);
                else z = shTap(s, uv + o / wpu);
                if (z < d - bias) { bsum += z; bn += 1.0; }
            }
        }
        if (bn < 0.5) return 1.0;
        float db = bsum / bn;
        float pen = kind == 0 ? (d - db) * p.y : p.y * (d - db) / max(db, 1e-3);
        filterW = max(filterW, pen);
    }
    float lit = 0.0;
    for (int k = 0; k < 2; ++k) {
        mat2 rk = k == 0 ? rot : mat2(rot[0][0] * 0.7071 - rot[0][1] * 0.7071, rot[0][0] * 0.7071 + rot[0][1] * 0.7071,
                                      -(rot[0][0] * 0.7071 + rot[0][1] * 0.7071), rot[0][0] * 0.7071 - rot[0][1] * 0.7071);
        float sc = k == 0 ? 1.0 : 0.6;
        for (int i = 0; i < 16; ++i) {
            vec2 o = rk * POISSON[i] * filterW * sc;
            float z;
            if (kind == 2) z = shTapCube(s, Pn - lp + T1 * o.x + T2 * o.y);
            else z = shTap(s, uv + o / wpu);
            lit += z < d - bias ? 0.0 : 1.0;
        }
    }
    return lit / 32.0;
}

// ---------------------------------------------------------------- surface
struct Surf {
    vec3 N; vec3 V; vec3 Ng; vec3 T; vec3 B;
    vec3 diff; vec3 F0; vec3 F90; float a; float at; float ab; float aniso;
    float cc; float ccA; float ccF0; vec3 sheen; float sheenR; float irid; vec3 iridF; float NdotV; float sheenScale;
    float specW;
};

vec3 lobe(Surf s, vec3 L, vec3 E, vec2 aff) {
    float NdotL = dot(s.N, L);
    if (NdotL <= 0.0) return vec3(0.0);
    vec3 H = normalize(L + s.V);
    float NdotH = max(dot(s.N, H), 0.0); float VdotH = max(dot(s.V, H), 0.0); float NdotV = s.NdotV;
    vec3 F = F_Schlick(s.F0, s.F90, VdotH);
    if (s.irid > 0.0) F = mix(F, s.iridF, s.irid);
    float DV;
    if (s.aniso > 0.0) {
        DV = D_GGX_aniso(NdotH, dot(s.T, H), dot(s.B, H), s.at, s.ab)
           * V_GGX_aniso(NdotL, NdotV, dot(s.B, s.V), dot(s.T, s.V), dot(s.T, L), dot(s.B, L), s.at, s.ab);
    } else DV = D_GGX(NdotH, s.a) * V_GGX(NdotL, NdotV, s.a);
    vec3 spec = F * DV * s.specW;
    vec3 diff = (vec3(1.0) - F * s.specW) * s.diff / PI;
    vec3 sh = s.sheen * D_Charlie(s.sheenR, NdotH) * V_Neubelt(NdotL, NdotV);
    vec3 base = diff * aff.x * s.sheenScale + spec * aff.y * s.sheenScale + sh * aff.x;
    if (s.cc > 0.0) {
        float cNL = max(dot(s.Ng, L), 0.0); float cNH = max(dot(s.Ng, H), 0.0);
        float Fc = F_Schlick(s.ccF0, VdotH) * s.cc;
        float c = D_GGX(cNH, s.ccA) * V_Kelemen(max(dot(L, H), 0.0)) * Fc * aff.y;
        return E * (base * NdotL * (1.0 - Fc) + vec3(c) * cNL);
    }
    return E * base * NdotL;
}

float spotFactor(int i, vec3 L) {
    float cd = dot(u_ld(i), -L);
    float ci = u_lparam(i).z; float co = u_lparam(i).w;
    float t = clamp((cd - co) / max(ci - co, 1e-4), 0.0, 1.0);
    return t * t;
}
@IES_SAMPLE@
float iesFactor(int i, vec3 toP) {
    return iesSample(u_lies(i), vec3(dot(toP, u_lr(i)), dot(toP, u_lu(i)), dot(toP, u_ld(i))));
}
float distAtt(int i, float dist) {
    float dm = max(dist / u_upm, 0.01);
    float att = 1.0 / pow(dm, u_lparam(i).y);
    float r = u_lparam(i).x;
    if (r > 0.0) att *= sq(clamp(1.0 - pow(dist / r, 4.0), 0.0, 1.0));
    return att;
}

vec3 areaLight(int i, Surf s, vec3 P) {
    int t = u_lt(i); vec3 C = u_lp(i); vec3 n = u_ld(i); vec3 Rt = u_lr(i); vec3 U = u_lu(i); vec2 sz = u_ls(i);
    vec3 total = vec3(0.0);
    vec2 aff = u_laff(i);
    if (t == 6) {   // sphere: diffuse = point at the centre (exact outside the sphere), spec = representative point
        vec3 dv = C - P; float dist = length(dv); vec3 L = dv / dist;
        vec3 E = u_lc(i) * distAtt(i, dist) * iesFactor(i, -L);
        total += lobe(s, L, E, vec2(aff.x, 0.0));
        vec3 Rr = reflect(-s.V, s.N);
        vec3 ctr = dot(dv, Rr) * Rr - dv;
        vec3 cp = dv + ctr * clamp(sz.x / max(length(ctr), 1e-6), 0.0, 1.0);
        float d2 = length(cp); vec3 Ls = cp / d2;
        float ap = clamp(s.a + sz.x / (2.0 * max(dist, 1e-3)), 0.0, 1.0);
        float norm = sq(s.a / max(ap, 1e-4));
        vec3 Es = u_lc(i) * distAtt(i, max(d2, 1e-3)) * norm * iesFactor(i, -Ls);
        return total + lobe(s, Ls, Es, vec2(0.0, aff.y));
    }
    // rect (4) / disk (5): 16 stratified points for diffuse, Lambertian emitter facing n
    if (dot(P - C, n) <= 0.0) return vec3(0.0);
    for (int k = 0; k < 16; ++k) {
        vec2 g = (vec2(float(k % 4), float(k / 4)) + 0.5) / 4.0;
        vec3 pt;
        if (t == 4) pt = C + Rt * (g.x * 2.0 - 1.0) * sz.x + U * (g.y * 2.0 - 1.0) * sz.y;
        else { float r = sqrt(g.x) * sz.x; float a = g.y * 2.0 * PI + g.x * 1.7; pt = C + (Rt * cos(a) + U * sin(a)) * r; }
        vec3 dv = pt - P; float dist = length(dv); vec3 L = dv / dist;
        float ce = max(dot(n, -L), 0.0);
        vec3 E = u_lc(i) / 16.0 * ce * distAtt(i, dist) * iesFactor(i, -L);
        total += lobe(s, L, E, vec2(aff.x, 0.0));
    }
    // representative point on the emitter for the reflection ray
    vec3 Rr = reflect(-s.V, s.N);
    float den = dot(Rr, n);
    vec3 hit;
    float tt = den < -1e-5 ? dot(C - P, n) / den : -1.0;
    if (tt > 0.0) hit = P + Rr * tt; else hit = P + Rr * length(C - P);
    vec3 lo = hit - C; lo -= n * dot(lo, n);
    vec2 lc = vec2(dot(lo, Rt), dot(lo, U));
    if (t == 4) lc = clamp(lc, -sz, sz); else if (length(lc) > sz.x) lc *= sz.x / length(lc);
    vec3 pt = C + Rt * lc.x + U * lc.y;
    vec3 dv = pt - P; float dist = length(dv); vec3 L = dv / dist;
    float ce = max(dot(n, -L), 0.0);
    float req = t == 4 ? sqrt(4.0 * sz.x * sz.y / PI) : sz.x;
    float ap = clamp(s.a + req / (2.0 * max(dist, 1e-3)), 0.0, 1.0);
    float norm = sq(s.a / max(ap, 1e-4));
    vec3 Es = u_lc(i) * ce * distAtt(i, dist) * norm * iesFactor(i, -L);
    return total + lobe(s, L, Es, vec2(0.0, aff.y));
}

vec3 srgbDecode(vec3 c) { return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c)); }

void main() {
    vec4 base = u_base * v_col;
    if (u_hasBase == 1) base *= mapSample(t_base, v_uv, u_baseSampler, u_baseBorder);
    if (u_hasOpacity == 1) base.a *= mapSample(t_opacity, v_opacityuv, u_opacitySampler, u_opacityBorder).r;
    float alpha = u_usdSurface == 1 ? clamp(base.a, 0.0, 1.0) : base.a;
    if (u_alphaMode == 1) { if (alpha < u_cutoff) discard; alpha = 1.0; }
    else if (u_alphaMode == 0) alpha = 1.0;
    vec3 Ng = normalize(v_nrm);
    if (u_doubleSided == 1 && !gl_FrontFacing) Ng = -Ng;
    vec3 V = u_ortho > 0.5 ? -u_camFwd : normalize(u_eye - v_wpos);
    vec3 T = v_tan.xyz;
    if (dot(T, T) < 1e-8) {
        vec3 dp1 = dFdx(v_wpos); vec3 dp2 = dFdy(v_wpos); vec2 du1 = dFdx(v_nrmuv); vec2 du2 = dFdy(v_nrmuv);
        T = dp1 * du2.y - dp2 * du1.y;
        if (dot(T, T) < 1e-12) T = abs(Ng.y) < 0.99 ? cross(vec3(0, 1, 0), Ng) : vec3(1, 0, 0);
    }
    T = normalize(T - Ng * dot(Ng, T));
    vec3 B = cross(Ng, T) * (v_tan.w < 0.0 ? -1.0 : 1.0);
    if (u_doubleSided == 1 && !gl_FrontFacing) B = -B;
    vec3 N = Ng;
    if (u_hasNrm == 1) {
        vec3 encoded = mapSample(t_nrm, v_nrmuv, u_nrmSampler, u_nrmBorder).xyz;
        vec3 tn = encoded * 2.0 - 1.0;
        if (u_mxNormalMap == 1) {
            if (dot(encoded, encoded) == 0.0) tn = vec3(0.0, 0.0, 1.0);
            tn.xy *= u_mxNormalScale;
            // A normalmap node produces a world-space vector. The MaterialX USD
            // preview shader interprets its input as tangent-space and performs
            // a second normalmap operation; preserve both graph operations.
            if (u_usdSurface == 1) tn = normalize(mat3(T, B, Ng) * tn);
        }
        tn.xy *= u_nScale;
        N = normalize(u_normalWorld == 1 ? tn : mat3(T, B, Ng) * tn);
    }
    float rough = u_rough; float metal = u_metal;
    if (u_hasMR == 1) {
        vec4 mr = u_hasExtensionMaps == 1 ? extensionSample(t_mr, 3, v_mruv) : mapSample(t_mr, v_mruv, u_mrSampler, u_mrBorder);
        rough *= mr.g; metal *= mr.b;
    }
    if (u_hasRough == 1) rough *= mapSample(t_rough, v_roughuv, u_roughSampler, u_roughBorder).r;
    vec3 specColor = u_specCol;
    specColor *= extensionSample(t_mr, 10, extensionUV(6)).rgb;
    float specAmount = u_spec*extensionSample(t_mr, 9, extensionUV(5)).a;
    if (u_hasMetal == 1) {
        vec4 sample_ = mapSample(t_metal, v_metaluv, u_metalSampler, u_metalBorder);
        if (u_specularWorkflow == 1) specColor *= sample_.rgb;
        else metal *= sample_.r;
    }
    if (u_specularWorkflow == 1) metal = 0.0;
    rough = clamp(rough, 0.03, 1.0);
    vec3 emis = u_emis;
    if (u_hasEmis == 1) emis *= mapSample(t_emis, v_emisuv, u_emisSampler, u_emisBorder).rgb;
    float ao = u_hasOcc == 1 ? mix(1.0, mapSample(t_occ, v_occuv, u_occSampler, u_occBorder).r, u_occStrength) : 1.0;
    ao *= u_occlusion;
    if (u_unlit == 1) {
        vec3 c = base.rgb * u_exposure;
        o_color = vec4(c * alpha, alpha);
        o_depth = vec4(v_vdepth * alpha, 0.0, 0.0, alpha);
        return;
    }
    Surf s;
    s.N = N; s.V = V; s.Ng = u_usdSurface == 1 ? N : Ng;
    s.NdotV = clamp(abs(dot(N, V)), 1e-4, 1.0);
    s.a = rough * rough;
    // anisotropy direction in the tangent plane
    float an = abs(u_aniso);
    float ar = u_anisoRot + (u_aniso < 0.0 ? PI * 0.5 : 0.0);
    if (extensionHas(t_mr, 13)) {
        vec3 sample_ = extensionSample(t_mr, 13, extensionUV(9)).rgb;
        vec2 direction = sample_.rg*2.0-1.0;
        if (dot(direction, direction) > 0.0) ar += atan(direction.y, direction.x);
        an *= sample_.b;
    }
    vec3 Ta = normalize(T * cos(ar) + B * sin(ar));
    vec3 Tn = normalize(Ta - N * dot(N, Ta));
    s.T = Tn; s.B = cross(N, Tn);
    s.aniso = an; s.at = mix(s.a, 1.0, an * an); s.ab = s.a;
    float ior = max(1e-6, u_ior*(u_hasExtensionMaps == 1 ? extensionSample(t_mr, 0, v_ioruv).r : scalarSample(t_mr, 0, v_ioruv)));
    float coat = u_cc*(u_hasExtensionMaps == 1 ? extensionSample(t_mr, 1, v_coatuv).r : scalarSample(t_mr, 1, v_coatuv));
    float coatRough = u_ccRough*(u_hasExtensionMaps == 1 ? extensionSample(t_mr, 2, v_coatRoughuv).r : scalarSample(t_mr, 2, v_coatRoughuv));
    if (extensionHas(t_mr, 4)) {
        vec3 tn = extensionSample(t_mr, 4, extensionUV(0)).rgb*2.0-1.0;
        tn.xy *= u_ccNormalScale;
        s.Ng = normalize(mat3(T, B, Ng)*tn);
    }
    float f0d = sq((ior - 1.0) / (ior + 1.0));
    vec3 dielF0 = min(vec3(f0d) * specColor, vec3(1.0)) * specAmount;
    if (u_usdSurface == 1) dielF0 = vec3(f0d);
    s.F0 = mix(dielF0, base.rgb, metal);
    s.F90 = vec3(mix(specAmount, 1.0, metal));
    if (u_usdSurface == 1) s.F90 = mix(vec3(1.0), base.rgb, metal);
    if (u_specularWorkflow == 1) { s.F0 = specColor; s.F90 = vec3(1.0); }
    s.specW = 1.0;
    float transAmount = u_usdTransparent == 1 ? 1.0-alpha : u_trans*extensionSample(t_mr, 5, extensionUV(1)).r;
    s.diff = base.rgb * (1.0 - metal) * (u_usdTransparent == 1 ? alpha : 1.0 - transAmount * (1.0 - metal));
    s.irid = u_irid*extensionSample(t_mr, 11, extensionUV(7)).r;
    float iridThickness = mix(u_iridThickMin, u_iridThick, extensionSample(t_mr, 12, extensionUV(8)).g);
    s.iridF = s.irid > 0.0 ? evalIridescence(1.0, u_iridIor, s.NdotV, iridThickness, s.F0) : vec3(0.0);
    s.cc = coat; s.ccA = max(coatRough * coatRough, 1e-3);
    s.ccF0 = u_usdSurface == 1 ? f0d : 0.04;
    s.sheen = u_sheenCol*extensionSample(t_mr, 7, extensionUV(3)).rgb;
    s.sheenR = max(u_sheenRough*extensionSample(t_mr, 8, extensionUV(4)).a, 0.03);
    vec3 lut = texture(t_lut, vec2(s.NdotV, rough)).rgb;
    float sheenE = texture(t_lut, vec2(s.NdotV, s.sheenR)).b;
    s.sheenScale = 1.0 - max3(s.sheen) * sheenE;

    vec3 P = v_wpos;
    vec3 col = vec3(0.0);
    for (int i = 0; i < u_nl; ++i) {
        int t = u_lt(i);
        float sh = 1.0;
        if (u_lsh(i) >= 0 && u_receiveShadow == 1) {
            vec3 Ls = t == 1 ? -u_ld(i) : normalize(u_lp(i) - P);
            sh = shadowAt(u_lsh(i), P, Ng, Ls);
            if (sh <= 0.0) continue;
        }
        if (t == 1) {
            col += lobe(s, -u_ld(i), u_lc(i) * iesFactor(i, u_ld(i)), u_laff(i)) * sh;
        } else if (t == 2 || t == 3) {
            vec3 dv = u_lp(i) - P; float dist = length(dv); vec3 L = dv / max(dist, 1e-6);
            float f = distAtt(i, dist) * iesFactor(i, -L);
            if (t == 3) f *= spotFactor(i, L);
            if (f > 0.0) col += lobe(s, L, u_lc(i) * f, u_laff(i)) * sh;
        } else if (t >= 4 && t <= 6) {
            col += areaLight(i, s, P) * sh;
        }
    }
    // image-based / ambient light
    vec3 Fr = s.F0 * lut.x + s.F90 * lut.y;
    if (s.irid > 0.0) Fr = mix(Fr, s.iridF, s.irid);
    float Ess = lut.x + lut.y;
    vec3 Fms = Fr * (1.0 + s.F0 * (1.0 / max(Ess, 1e-3) - 1.0));
    vec3 Rv = reflect(-V, N);
    if (an > 0.0) {
        vec3 at = cross(s.B, V); vec3 anN = cross(at, s.B);
        float bf = 1.0 - an * (1.0 - rough); bf = bf * bf * bf * bf;
        Rv = reflect(-V, normalize(mix(anN, N, bf)));
    }
    for (int i = -1; i < u_ne; ++i) { // ambient first, then each independent dome
        float envSh = 1.0;
        if (i >= 0 && u_receiveShadow == 1) {
            int index = int(envDatum(i, 0).y);
            if (index >= 0) envSh = mix(1.0, shadowAt(index, P, Ng, -u_shDir(index)), envDatum(i, 1).w);
        }
        // Ambient light (CONVENTIONS 5.20): dielectrics take it diffusely (albedo x radiance); metals reflect it
        // as a uniform environment, in proportion to metalness.
        vec3 iblSpec = (envSpec(i, Rv, rough) + (i < 0 ? u_ambS * metal : vec3(0.0))) * Fms;
        vec3 iblDiff = envIrradiance(i, N) * s.diff / PI * (vec3(1.0) - Fr) + (i < 0 ? u_ambD * s.diff / PI : vec3(0.0));
        vec3 iblSheen = s.sheen * sheenE * envSpec(i, Rv, s.sheenR);
        vec3 ibl = (iblDiff * s.sheenScale + iblSheen + iblSpec * s.sheenScale) * ao;
        if (s.cc > 0.0) {
            float cNV = clamp(dot(s.Ng, V), 1e-4, 1.0);
            vec3 cl = texture(t_lut, vec2(cNV, sqrt(s.ccA))).rgb;
            float Fc = F_Schlick(s.ccF0, cNV) * s.cc;
            ibl = ibl * (1.0 - Fc) + envSpec(i, reflect(-V, s.Ng), sqrt(s.ccA)) * (s.ccF0 * cl.x + cl.y) * s.cc * ao;
        }
        col += ibl * envSh;
    }
    // transmission (KHR_materials_transmission + volume + dispersion): screen-space refraction
    float outAlpha = u_usdTransparent == 1 ? 1.0 : alpha;
    if (transAmount > 0.0) {
        float thick = u_thick * extensionSample(t_mr, 6, extensionUV(2)).g * u_upm * u_modelScale;
        vec3 tint = u_usdTransparent == 1 ? vec3(1.0) : base.rgb;
        if (u_attDist > 0.0 && u_attDist < 1e20 && thick > 0.0) tint *= pow(max(u_attCol, vec3(1e-4)), vec3(thick / (u_attDist * u_upm * u_modelScale)));
        vec3 trans = vec3(0.0); float cover = 0.0;
        float half_ = (ior - 1.0) * 0.025 * u_disp;
        vec3 iors = vec3(ior - half_, ior, ior + half_);
        int nch = u_disp > 0.0 ? 3 : 1;
        for (int c = 0; c < 3; ++c) {
            if (c >= nch) break;
            float eta = nch == 1 ? ior : iors[c];
            vec3 rd = refract(-V, N, 1.0 / eta);
            vec3 ex = P + (thick > 0.0 ? rd * thick : vec3(0.0));
            vec4 clip = u_viewProj * vec4(ex, 1.0);
            vec2 uv = clip.xy / clip.w * 0.5 + 0.5;
            float lod = u_usdTransparent == 1 ? 0.0 : log2(max(u_opaqueSize.x, 1.0)) * rough * clamp(eta * 2.0 - 2.0, 0.0, 1.0);
            vec4 smp = u_hasOpaque == 1 ? textureLod(t_opaque, uv, lod) : vec4(0.0);
            if (nch == 1) { trans = smp.rgb; cover = smp.a; }
            else { trans[c] = smp[c]; cover += smp.a / 3.0; }
        }
        float coatThrough = u_usdTransparent == 1 ? 1.0 - F_Schlick(s.ccF0, s.NdotV)*s.cc : 1.0;
        vec3 w = transAmount * (1.0 - metal) * (vec3(1.0) - Fr) * coatThrough;
        col += trans * tint * w;
        float see = transAmount * (1.0 - metal) * (1.0 - max3(Fr)) * coatThrough * (1.0 - cover) * clamp(dot(tint, vec3(0.2126, 0.7152, 0.0722)), 0.0, 1.0);
        outAlpha *= 1.0 - see;
    }
    col += emis;
    col *= u_exposure;
    float a = u_alphaMode == 2 && u_usdTransparent == 0 ? alpha : 1.0;
    if (transAmount > 0.0) { o_color = vec4(col * a, outAlpha); o_depth = vec4(v_vdepth * outAlpha, 0.0, 0.0, outAlpha); return; }
    o_color = vec4(col * a, a);
    o_depth = vec4(v_vdepth * a, 0.0, 0.0, a);
}
""".replace("@MAP_SAMPLE@", MAP_SAMPLE).replace("@SCALAR_SAMPLE@", SCALAR_SAMPLE).replace("@EXTENSION_SAMPLE@", EXTENSION_SAMPLE).replace("@IES_SAMPLE@", IES_SAMPLE).replace("@LIGHT_DATA@", LIGHT_DATA).replace("@SHADOW_LOOKUP@", SHADOW_LOOKUP)

DEPTH_VS = """
#version 410
in vec3 in_pos; in vec4 in_baseNrmUV; in vec4 in_metalOpacityUV;
in vec4 in_m0; in vec4 in_m1; in vec4 in_m2; in vec4 in_m3;
uniform mat4 u_vp; uniform vec2 u_uvScale;
out vec3 v_wpos; out vec2 v_uv; out vec2 v_opacityuv;
void main() {
    mat4 M = mat4(in_m0, in_m1, in_m2, in_m3);
    vec4 wp = M * vec4(in_pos, 1.0);
    v_wpos = wp.xyz; v_uv = in_baseNrmUV.xy * u_uvScale;
    v_opacityuv = in_metalOpacityUV.zw * u_uvScale;
    gl_Position = u_vp * wp;
}
"""

# Depth-only (prepass) and shadow-map metric output. u_mode: 0 prepass, 1 ortho metric, 2 distance metric.
DEPTH_FS = """
#version 410
in vec3 v_wpos; in vec2 v_uv; in vec2 v_opacityuv;
uniform int u_mode; uniform vec3 u_lp; uniform vec3 u_ldir;
uniform int u_alphaMode; uniform float u_cutoff; uniform float u_alpha; uniform int u_hasBase; uniform sampler2D t_base;
uniform ivec4 u_baseSampler;
uniform sampler2D t_opacity; uniform int u_hasOpacity; uniform ivec4 u_opacitySampler;
uniform vec4 u_baseBorder; uniform vec4 u_opacityBorder;
out vec4 o;
@MAP_SAMPLE@
void main() {
    if (u_alphaMode == 1) {
        float a = u_alpha * (u_hasBase == 1 ? mapSample(t_base, v_uv, u_baseSampler, u_baseBorder).a : 1.0);
        if (u_hasOpacity == 1) a *= mapSample(t_opacity, v_opacityuv, u_opacitySampler, u_opacityBorder).r;
        if (a < u_cutoff) discard;
    }
    float m = u_mode == 1 ? dot(v_wpos - u_lp, u_ldir) : length(v_wpos - u_lp);
    o = vec4(m, 0.0, 0.0, 1.0);
}
""".replace("@MAP_SAMPLE@", MAP_SAMPLE)

BG_VS = """
#version 410
in vec2 in_pos;
out vec2 v_ndc;
void main() { v_ndc = in_pos; gl_Position = vec4(in_pos, 0.9999999, 1.0); }
"""

BG_FS = """
#version 410
#define PI 3.14159265358979
in vec2 v_ndc;
uniform mat4 u_invViewProj; uniform vec3 u_eye; uniform int u_ortho; uniform vec3 u_fwd;
uniform sampler2D t_env; uniform mat3 u_rot; uniform vec3 u_col;
uniform int u_img; uniform int u_first; uniform float u_exposure;
layout(location = 0) out vec4 o_color;
layout(location = 1) out vec4 o_depth;
vec2 dirToEquirect(vec3 d) { return vec2(atan(d.x, -d.z) / (2.0 * PI) + 0.5, 0.5 - asin(clamp(d.y, -1.0, 1.0)) / PI); }
void main() {
    vec4 a = u_invViewProj * vec4(v_ndc, -1.0, 1.0);
    vec4 b = u_invViewProj * vec4(v_ndc, 1.0, 1.0);
    vec3 d = u_ortho == 1 ? u_fwd : normalize(b.xyz / b.w - a.xyz / a.w);
    vec3 c = u_col * (u_img == 1 ? textureLod(t_env, dirToEquirect(transpose(u_rot) * d), 0.0).rgb : vec3(1.0));
    o_color = vec4(c * u_exposure, float(u_first));
    o_depth = u_first == 1 ? vec4(1e9, 0.0, 0.0, 1.0) : vec4(0.0);
}
"""

PREFILTER_FS = """
#version 410
#define PI 3.14159265358979
in vec2 uv;
uniform sampler2D t_src; uniform float u_rough; uniform float u_srcW;
out vec4 o;
vec2 dirToEquirect(vec3 d) { return vec2(atan(d.x, -d.z) / (2.0 * PI) + 0.5, 0.5 - asin(clamp(d.y, -1.0, 1.0)) / PI); }
float radicalInverse(uint b) {
    b = (b << 16u) | (b >> 16u); b = ((b & 0x55555555u) << 1u) | ((b & 0xAAAAAAAAu) >> 1u);
    b = ((b & 0x33333333u) << 2u) | ((b & 0xCCCCCCCCu) >> 2u); b = ((b & 0x0F0F0F0Fu) << 4u) | ((b & 0xF0F0F0F0u) >> 4u);
    b = ((b & 0x00FF00FFu) << 8u) | ((b & 0xFF00FF00u) >> 8u); return float(b) * 2.3283064365386963e-10;
}
void main() {
    // viewport y (uv.y) is the equirect v of this level band: v = 0 is straight up
    float lon = (uv.x - 0.5) * 2.0 * PI; float lat = (0.5 - uv.y) * PI;
    vec3 N = vec3(sin(lon) * cos(lat), sin(lat), -cos(lon) * cos(lat));
    if (u_rough < 0.01) { o = vec4(textureLod(t_src, dirToEquirect(N), 0.0).rgb, 1.0); return; }
    vec3 up = abs(N.y) < 0.999 ? vec3(0, 1, 0) : vec3(1, 0, 0);
    vec3 T = normalize(cross(up, N)); vec3 B = cross(N, T);
    float a = u_rough * u_rough; float a2 = a * a;
    vec3 acc = vec3(0.0); float wsum = 0.0;
    const uint NS = 256u;
    for (uint i = 0u; i < NS; ++i) {
        vec2 Xi = vec2(float(i) / float(NS), radicalInverse(i));
        float phi = 2.0 * PI * Xi.x;
        float ct = sqrt((1.0 - Xi.y) / (1.0 + (a2 - 1.0) * Xi.y)); float st = sqrt(1.0 - ct * ct);
        vec3 H = T * (st * cos(phi)) + B * (st * sin(phi)) + N * ct;
        vec3 L = 2.0 * dot(N, H) * H - N;
        float NdotL = dot(N, L);
        if (NdotL > 0.0) {
            float d = (ct * ct * (a2 - 1.0) + 1.0); float D = a2 / (PI * d * d);
            float pdf = D * 0.25;
            float saTexel = 4.0 * PI / (u_srcW * u_srcW * 0.5);
            float saSample = 1.0 / (float(NS) * pdf + 1e-4);
            float lod = max(0.5 * log2(saSample / saTexel) + 1.0, 0.0);
            acc += textureLod(t_src, dirToEquirect(L), lod).rgb * NdotL; wsum += NdotL;
        }
    }
    o = vec4(acc / max(wsum, 1e-6), 1.0);
}
"""

SPLAT_VS = """
#version 410
in vec2 in_corner;
in vec3 in_center; in vec4 in_color; in vec3 in_covA; in vec3 in_covB;
uniform mat4 u_view; uniform mat4 u_proj; uniform vec2 u_vpSize; uniform float u_fx; uniform float u_fy;
uniform int u_ortho;
out vec4 v_color; out vec2 v_off; out float v_vdepth;
void main() {
    vec4 cam = u_view * vec4(in_center, 1.0);
    vec4 clip = u_proj * cam;
    v_vdepth = -cam.z;
    mat3 S = mat3(in_covA.x, in_covA.y, in_covA.z, in_covA.y, in_covB.x, in_covB.y, in_covA.z, in_covB.y, in_covB.z);
    float z = -cam.z;
    if (z <= 0.0) { gl_Position = vec4(0.0, 0.0, 2.0, 1.0); return; }
    // The EWA Jacobian is only valid near the view axis: for Gaussians far outside the frustum
    // x/z and y/z blow the projected footprint up to cover the screen. Like the reference 3DGS
    // rasterizer, clamp them to 1.3x the frustum half-extent, and cull centres beyond that.
    float limx = 1.3 * 0.5 * u_vpSize.x / u_fx;
    float limy = 1.3 * 0.5 * u_vpSize.y / u_fy;
    float tx = cam.x / z; float ty = cam.y / z;
    if (u_ortho == 0 && (abs(tx) > limx || abs(ty) > limy)) { gl_Position = vec4(0.0, 0.0, 2.0, 1.0); return; }
    tx = clamp(tx, -limx, limx); ty = clamp(ty, -limy, limy);
    mat3 J = u_ortho == 1 ? mat3(u_fx, 0.0, 0.0, 0.0, u_fy, 0.0, 0.0, 0.0, 0.0)
                          : mat3(u_fx / z, 0.0, 0.0, 0.0, u_fy / z, 0.0, u_fx * tx / z, u_fy * ty / z, 0.0);
    mat3 W = mat3(u_view);
    mat3 T = J * W;
    mat3 cov = T * S * transpose(T);
    float a = cov[0][0] + 0.3; float b = cov[0][1]; float c = cov[1][1] + 0.3;
    float mid = 0.5 * (a + c); float rad = length(vec2(0.5 * (a - c), b));
    float l1 = mid + rad; float l2 = max(mid - rad, 0.1);
    vec2 e1 = normalize(abs(b) > 1e-8 ? vec2(b, l1 - a) : vec2(1.0, 0.0)); vec2 e2 = vec2(-e1.y, e1.x);
    vec2 off = in_corner * 3.0;
    vec2 px = (e1 * off.x * sqrt(l1) + e2 * off.y * sqrt(l2));
    v_off = off;
    v_color = in_color;
    gl_Position = clip + vec4(px / u_vpSize * 2.0 * clip.w, 0.0, 0.0);
}
"""

# SPLAT_VS with the attributes read from storage buffers (GL 4.3): `order` lists the splats far to
# near (sorted on the GPU, see SPLAT_SORT_CS), `attr` holds 13 floats per splat in load order
# (centre, colour, covariance A, covariance B).
SPLAT_VS_SSBO = SPLAT_VS.replace("#version 410", "#version 430").replace(
    "in vec3 in_center; in vec4 in_color; in vec3 in_covA; in vec3 in_covB;",
    """layout(std430, binding = 0) readonly buffer Attr { float attr[]; };
layout(std430, binding = 1) readonly buffer Order { uint order[]; };""").replace(
    "void main() {\n    vec4 cam = u_view * vec4(in_center, 1.0);",
    """void main() {
    uint at = order[gl_InstanceID] * 13u;
    vec3 in_center = vec3(attr[at], attr[at + 1u], attr[at + 2u]);
    vec4 in_color = vec4(attr[at + 3u], attr[at + 4u], attr[at + 5u], attr[at + 6u]);
    vec3 in_covA = vec3(attr[at + 7u], attr[at + 8u], attr[at + 9u]);
    vec3 in_covB = vec3(attr[at + 10u], attr[at + 11u], attr[at + 12u]);
    vec4 cam = u_view * vec4(in_center, 1.0);""")

# Depth sort of the splats far to near: keys -dot(centre - eye, fwd) in double precision with the splat
# index as tie-break (the order of a stable sort of the keys), padded to a power of two with +inf.
SPLAT_KEYS_CS = """
#version 430
layout(local_size_x = 256) in;
layout(std430, binding = 0) readonly buffer Centres { double c[]; };
layout(std430, binding = 1) buffer Keys { double key[]; };
layout(std430, binding = 2) buffer Vals { uint val[]; };
uniform uint u_n; uniform uint u_pad;
uniform dvec3 u_eye; uniform dvec3 u_fwd;
void main() {
    uint i = gl_GlobalInvocationID.x;
    if (i >= u_pad) return;
    val[i] = i;
    if (i < u_n) {
        dvec3 p = dvec3(c[3u * i], c[3u * i + 1u], c[3u * i + 2u]) - u_eye;
        key[i] = -(p.x * u_fwd.x + p.y * u_fwd.y + p.z * u_fwd.z);
    } else {
        key[i] = packDouble2x32(uvec2(0u, 0x7FF00000u));     // +inf: padding sorts last
    }
}
"""

SPLAT_SORT_CS = """
#version 430
layout(local_size_x = 256) in;
layout(std430, binding = 1) buffer Keys { double key[]; };
layout(std430, binding = 2) buffer Vals { uint val[]; };
uniform uint u_k; uniform uint u_j; uniform uint u_pad;
void main() {
    uint i = gl_GlobalInvocationID.x;
    uint l = i ^ u_j;
    if (i >= u_pad || l <= i) return;
    double ki = key[i], kl = key[l];
    uint vi = val[i], vl = val[l];
    bool after = ki > kl || (ki == kl && vi > vl);          // (key, index) of i sorts after l's
    if (((i & u_k) == 0u) == after) {
        key[i] = kl; key[l] = ki; val[i] = vl; val[l] = vi;
    }
}
"""

SPLAT_FS = """
#version 410
in vec4 v_color; in vec2 v_off; in float v_vdepth;
uniform float u_exposure;
layout(location = 0) out vec4 o_color;
layout(location = 1) out vec4 o_depth;
void main() {
    float r2 = dot(v_off, v_off);
    if (r2 > 9.0) discard;
    float a = v_color.a * exp(-0.5 * r2);
    if (a < 1.0 / 255.0) discard;
    o_color = vec4(v_color.rgb * u_exposure * a, a);
    o_depth = vec4(v_vdepth * a, 0.0, 0.0, a);
}
"""


DOWNSAMPLE_FS = """
#version 410
// Exact n x n box average of the resolved colour (and depth) targets: the supersampling (SSAA)
// reduction, done here so only final-size pixels are read back. u_origin is the block origin in
// bottom-up target pixels.
uniform sampler2D u_col;
uniform sampler2D u_dep;
uniform int u_n;
uniform int u_depth;
uniform ivec2 u_origin;
layout(location = 0) out vec4 o_col;
layout(location = 1) out vec4 o_dep;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy) * u_n + u_origin;
    vec4 c = vec4(0.0);
    vec4 d = vec4(0.0);
    for (int j = 0; j < u_n; ++j)
        for (int i = 0; i < u_n; ++i) {
            c += texelFetch(u_col, p + ivec2(i, j), 0);
            if (u_depth == 1) d += texelFetch(u_dep, p + ivec2(i, j), 0);
        }
    float k = 1.0 / float(u_n * u_n);
    o_col = c * k;
    o_dep = d * k;
}
"""
