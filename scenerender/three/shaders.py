"""GLSL sources of the 3D renderer (GL 4.1 core). See renderer.py for the uniforms' meaning."""

MAXL = 16
MAXS = 8

MAP_SAMPLE = """
vec4 mapSample(sampler2D image, vec2 uv, ivec2 mirror) {
    // Reflection stays in the fragment stage. Explicit gradients avoid a
    // spurious change in mip level where a derivative quad straddles a fold.
    vec2 tile = mod(uv, 2.0);
    vec2 reflected = 1.0 - abs(tile - 1.0);
    vec2 direction = mix(vec2(1.0), mix(vec2(-1.0), vec2(1.0), lessThan(tile, vec2(1.0))), bvec2(mirror));
    vec2 sampleUV = mix(uv, reflected, bvec2(mirror));
    return textureGrad(image, sampleUV, dFdx(uv) * direction, dFdy(uv) * direction);
}
"""

MAIN_VS = """
#version 410
in vec3 in_pos; in vec3 in_nrm; in vec2 in_uv; in vec4 in_tan; in vec4 in_col;
in vec2 in_nrmuv; in vec2 in_mruv; in vec2 in_occuv; in vec2 in_emisuv;
in vec4 in_m0; in vec4 in_m1; in vec4 in_m2; in vec4 in_m3;
uniform mat4 u_view; uniform mat4 u_proj; uniform vec2 u_uvScale;
out vec3 v_wpos; out vec3 v_nrm; out vec2 v_uv; out vec4 v_tan; out vec4 v_col; out float v_vdepth;
out vec2 v_nrmuv; out vec2 v_mruv; out vec2 v_occuv; out vec2 v_emisuv;
void main() {
    mat4 M = mat4(in_m0, in_m1, in_m2, in_m3);
    vec4 wp = M * vec4(in_pos, 1.0);
    mat3 M3 = mat3(M);
    v_wpos = wp.xyz;
    v_nrm = normalize(transpose(inverse(M3)) * in_nrm);
    vec3 t = M3 * in_tan.xyz;
    v_tan = vec4(length(t) > 1e-12 ? normalize(t) : vec3(1, 0, 0), in_tan.w * sign(determinant(M3)));
    v_uv = in_uv * u_uvScale;
    v_nrmuv = in_nrmuv * u_uvScale; v_mruv = in_mruv * u_uvScale;
    v_occuv = in_occuv * u_uvScale; v_emisuv = in_emisuv * u_uvScale;
    v_col = in_col;
    vec4 vp = u_view * wp;
    v_vdepth = -vp.z;
    gl_Position = u_proj * vp;
}
"""

MAIN_FS = """
#version 410
#define MAXL @MAXL@
#define MAXS @MAXS@
#define PI 3.14159265358979
in vec3 v_wpos; in vec3 v_nrm; in vec2 v_uv; in vec4 v_tan; in vec4 v_col; in float v_vdepth;
in vec2 v_nrmuv; in vec2 v_mruv; in vec2 v_occuv; in vec2 v_emisuv;
layout(location = 0) out vec4 o_color;
layout(location = 1) out vec4 o_depth;

uniform vec3 u_eye; uniform float u_exposure; uniform float u_upm; uniform float u_ortho; uniform vec3 u_camFwd;
// material
uniform vec4 u_base; uniform float u_metal; uniform float u_rough; uniform vec3 u_emis;
uniform int u_alphaMode; uniform float u_cutoff; uniform int u_unlit; uniform int u_doubleSided;
uniform float u_cc; uniform float u_ccRough; uniform float u_trans; uniform float u_ior; uniform float u_thick;
uniform vec3 u_attCol; uniform float u_attDist; uniform vec3 u_sheenCol; uniform float u_sheenRough;
uniform float u_spec; uniform vec3 u_specCol; uniform float u_irid; uniform float u_iridIor; uniform float u_iridThick;
uniform float u_aniso; uniform float u_anisoRot; uniform float u_disp; uniform float u_nScale; uniform float u_modelScale;
uniform sampler2D t_base; uniform sampler2D t_nrm; uniform sampler2D t_mr; uniform sampler2D t_occ; uniform sampler2D t_emis;
uniform ivec2 u_baseMirror; uniform ivec2 u_nrmMirror; uniform ivec2 u_mrMirror; uniform ivec2 u_occMirror; uniform ivec2 u_emisMirror;
uniform int u_hasBase; uniform int u_hasNrm; uniform int u_hasMR; uniform int u_hasOcc; uniform int u_hasEmis;
uniform float u_occStrength;
uniform int u_receiveShadow;
// lights
uniform int u_nl;
uniform int u_lt[MAXL]; uniform vec3 u_lc[MAXL]; uniform vec3 u_lp[MAXL]; uniform vec3 u_ld[MAXL];
uniform vec3 u_lr[MAXL]; uniform vec3 u_lu[MAXL]; uniform vec2 u_ls[MAXL]; uniform vec4 u_lparam[MAXL];
uniform vec2 u_laff[MAXL]; uniform int u_lies[MAXL]; uniform int u_lsh[MAXL];
uniform sampler2D t_ies[4];
// shadows
uniform sampler2D t_sh[MAXS];
uniform mat4 u_shMat[MAXS]; uniform int u_shKind[MAXS]; uniform vec4 u_shP[MAXS]; uniform vec4 u_shQ[MAXS];
uniform vec3 u_shPos[MAXS]; uniform vec3 u_shDir[MAXS];
// environment
uniform int u_hasEnv; uniform sampler2D t_env; uniform int u_envLevels; uniform vec3 u_sh9[9];
uniform vec3 u_ambD; uniform vec3 u_ambS; uniform float u_envSpecOn; uniform float u_envDiffOn;
uniform sampler2D t_lut; uniform float u_envShadow; uniform int u_envShIdx;
// transmission
uniform int u_hasOpaque; uniform sampler2D t_opaque; uniform mat4 u_viewProj; uniform vec2 u_opaqueSize;

const mat3 XYZ_TO_REC709 = mat3(3.2404542, -0.9692660, 0.0556434, -1.5371385, 1.8760108, -0.2040259,
                                -0.4985314, 0.0415560, 1.0572252);
@MAP_SAMPLE@
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
vec3 envLevel(vec3 d, float lvl) {
    vec2 uv = dirToEquirect(d);
    ivec2 ts = textureSize(t_env, 0);
    float lh = float(ts.y) / float(u_envLevels);
    float v = clamp(uv.y, 0.5 / lh, 1.0 - 0.5 / lh);
    return texture(t_env, vec2(uv.x, (lvl + v) / float(u_envLevels))).rgb;
}
vec3 envSpec(vec3 d, float rough) {
    if (u_hasEnv == 0) return u_ambS;
    float l = clamp(rough, 0.0, 1.0) * float(u_envLevels - 1);
    float l0 = floor(l); float l1 = min(l0 + 1.0, float(u_envLevels - 1));
    return mix(envLevel(d, l0), envLevel(d, l1), l - l0) * u_envSpecOn + u_ambS;
}
vec3 envIrradiance(vec3 n) {
    vec3 e = u_sh9[0] * 0.282095 + u_sh9[1] * 0.488603 * n.y + u_sh9[2] * 0.488603 * n.z + u_sh9[3] * 0.488603 * n.x
           + u_sh9[4] * 1.092548 * n.x * n.y + u_sh9[5] * 1.092548 * n.y * n.z + u_sh9[6] * 0.315392 * (3.0 * n.z * n.z - 1.0)
           + u_sh9[7] * 1.092548 * n.x * n.z + u_sh9[8] * 0.546274 * (n.x * n.x - n.y * n.y);
    return max(e, vec3(0.0)) * u_envDiffOn + u_ambD;
}

// ---------------------------------------------------------------- shadows
const vec2 POISSON[16] = vec2[](vec2(-0.94201624, -0.39906216), vec2(0.94558609, -0.76890725), vec2(-0.09418410, -0.92938870),
    vec2(0.34495938, 0.29387760), vec2(-0.91588581, 0.45771432), vec2(-0.81544232, -0.87912464), vec2(-0.38277543, 0.27676845),
    vec2(0.97484398, 0.75648379), vec2(0.44323325, -0.97511554), vec2(0.53742981, -0.47373420), vec2(-0.26496911, -0.41893023),
    vec2(0.79197514, 0.19090188), vec2(-0.24188840, 0.99706507), vec2(-0.81409955, 0.91437590), vec2(0.19984126, 0.78641367),
    vec2(0.14383161, -0.14100790));
const vec3 CF[6] = vec3[](vec3(1, 0, 0), vec3(-1, 0, 0), vec3(0, 1, 0), vec3(0, -1, 0), vec3(0, 0, 1), vec3(0, 0, -1));
const vec3 CU[6] = vec3[](vec3(0, 1, 0), vec3(0, 1, 0), vec3(0, 0, 1), vec3(0, 0, -1), vec3(0, 1, 0), vec3(0, 1, 0));
int cubeFace(vec3 v) {
    vec3 a = abs(v);
    if (a.x >= a.y && a.x >= a.z) return v.x > 0.0 ? 0 : 1;
    if (a.y >= a.z) return v.y > 0.0 ? 2 : 3;
    return v.z > 0.0 ? 4 : 5;
}
vec2 cubeAtlasUV(vec3 v) {
    int f = cubeFace(v);
    vec3 F = CF[f]; vec3 U = CU[f]; vec3 R = cross(F, U);
    float d = dot(v, F);
    vec2 uv = vec2(dot(v, R), dot(v, U)) / d * 0.5 + 0.5;
    return (vec2(float(f % 3), float(f / 3)) + uv) / vec2(3.0, 2.0);
}
float shTap(int s, vec2 uv) { return texture(t_sh[s], uv).r; }
float shadowAt(int s, vec3 P, vec3 N, vec3 L) {
    int kind = u_shKind[s];
    vec4 p = u_shP[s];      // bias (world), light radius (world / tan angle), near, far
    vec4 q = u_shQ[s];      // texel (uv), world per uv at unit distance (persp) or extent (ortho), map size, 0
    vec3 lp = u_shPos[s];
    float NdotL = clamp(dot(N, L), 0.0, 1.0);
    float d; vec2 uv; float wpu;
    vec3 T1 = normalize(abs(L.y) < 0.99 ? cross(L, vec3(0, 1, 0)) : cross(L, vec3(1, 0, 0)));
    vec3 T2 = cross(L, T1);
    // normal offset: one texel in world units at the receiver
    if (kind == 0) { wpu = q.y; }
    else { wpu = q.y * max(length(P - lp), 1e-3); }
    vec3 Pn = P + N * (q.x * wpu * 1.5 * (1.0 - NdotL * 0.5));
    if (kind == 0) { d = dot(Pn - lp, u_shDir[s]); vec4 c = u_shMat[s] * vec4(Pn, 1.0); uv = c.xy / c.w * 0.5 + 0.5; }
    else if (kind == 1) { d = length(Pn - lp); vec4 c = u_shMat[s] * vec4(Pn, 1.0); if (c.w <= 0.0) return 1.0; uv = c.xy / c.w * 0.5 + 0.5; }
    else { d = length(Pn - lp); uv = cubeAtlasUV(Pn - lp); }
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
                if (kind == 2) z = shTap(s, cubeAtlasUV(Pn - lp + T1 * o.x + T2 * o.y));
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
            if (kind == 2) z = shTap(s, cubeAtlasUV(Pn - lp + T1 * o.x + T2 * o.y));
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
    float cc; float ccA; vec3 sheen; float sheenR; float irid; vec3 iridF; float NdotV; float sheenScale;
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
        float Fc = F_Schlick(0.04, VdotH) * s.cc;
        float c = D_GGX(cNH, s.ccA) * V_Kelemen(max(dot(L, H), 0.0)) * Fc * aff.y;
        return E * (base * NdotL * (1.0 - Fc) + vec3(c) * cNL);
    }
    return E * base * NdotL;
}

float spotFactor(int i, vec3 L) {
    float cd = dot(u_ld[i], -L);
    float ci = u_lparam[i].z; float co = u_lparam[i].w;
    float t = clamp((cd - co) / max(ci - co, 1e-4), 0.0, 1.0);
    return t * t;
}
float iesFactor(int i, vec3 toP) {
    int k = u_lies[i];
    if (k < 0) return 1.0;
    float th = acos(clamp(dot(toP, u_ld[i]), -1.0, 1.0)) / PI;
    float ph = atan(dot(toP, u_lu[i]), dot(toP, u_lr[i])); if (ph < 0.0) ph += 2.0 * PI;
    return texture(t_ies[k], vec2(th, ph / (2.0 * PI))).r;
}
float distAtt(int i, float dist) {
    float dm = max(dist / u_upm, 0.01);
    float att = 1.0 / pow(dm, u_lparam[i].y);
    float r = u_lparam[i].x;
    if (r > 0.0) att *= sq(clamp(1.0 - pow(dist / r, 4.0), 0.0, 1.0));
    return att;
}

vec3 areaLight(int i, Surf s, vec3 P) {
    int t = u_lt[i]; vec3 C = u_lp[i]; vec3 n = u_ld[i]; vec3 Rt = u_lr[i]; vec3 U = u_lu[i]; vec2 sz = u_ls[i];
    vec3 total = vec3(0.0);
    vec2 aff = u_laff[i];
    if (t == 6) {   // sphere: diffuse = point at the centre (exact outside the sphere), spec = representative point
        vec3 dv = C - P; float dist = length(dv); vec3 L = dv / dist;
        vec3 E = u_lc[i] * distAtt(i, dist) * iesFactor(i, -L);
        total += lobe(s, L, E, vec2(aff.x, 0.0));
        vec3 Rr = reflect(-s.V, s.N);
        vec3 ctr = dot(dv, Rr) * Rr - dv;
        vec3 cp = dv + ctr * clamp(sz.x / max(length(ctr), 1e-6), 0.0, 1.0);
        float d2 = length(cp); vec3 Ls = cp / d2;
        float ap = clamp(s.a + sz.x / (2.0 * max(dist, 1e-3)), 0.0, 1.0);
        float norm = sq(s.a / max(ap, 1e-4));
        vec3 Es = u_lc[i] * distAtt(i, max(d2, 1e-3)) * norm;
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
        vec3 E = u_lc[i] / 16.0 * ce * distAtt(i, dist) * iesFactor(i, -L);
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
    vec3 Es = u_lc[i] * ce * distAtt(i, dist) * norm * iesFactor(i, -L);
    return total + lobe(s, L, Es, vec2(0.0, aff.y));
}

vec3 srgbDecode(vec3 c) { return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c)); }

void main() {
    vec4 base = u_base * v_col;
    if (u_hasBase == 1) base *= mapSample(t_base, v_uv, u_baseMirror);
    float alpha = base.a;
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
        vec3 tn = mapSample(t_nrm, v_nrmuv, u_nrmMirror).xyz * 2.0 - 1.0;
        tn.xy *= u_nScale;
        N = normalize(mat3(T, B, Ng) * tn);
    }
    float rough = u_rough; float metal = u_metal;
    if (u_hasMR == 1) { vec4 mr = mapSample(t_mr, v_mruv, u_mrMirror); rough *= mr.g; metal *= mr.b; }
    rough = clamp(rough, 0.03, 1.0);
    vec3 emis = u_emis;
    if (u_hasEmis == 1) emis *= mapSample(t_emis, v_emisuv, u_emisMirror).rgb;
    float ao = u_hasOcc == 1 ? mix(1.0, mapSample(t_occ, v_occuv, u_occMirror).r, u_occStrength) : 1.0;
    if (u_unlit == 1) {
        vec3 c = base.rgb * u_exposure;
        o_color = vec4(c * alpha, alpha);
        o_depth = vec4(v_vdepth * alpha, 0.0, 0.0, alpha);
        return;
    }
    Surf s;
    s.N = N; s.V = V; s.Ng = Ng;
    s.NdotV = clamp(abs(dot(N, V)), 1e-4, 1.0);
    s.a = rough * rough;
    // anisotropy direction in the tangent plane
    float an = abs(u_aniso);
    float ar = u_anisoRot + (u_aniso < 0.0 ? PI * 0.5 : 0.0);
    vec3 Ta = normalize(T * cos(ar) + B * sin(ar));
    vec3 Tn = normalize(Ta - N * dot(N, Ta));
    s.T = Tn; s.B = cross(N, Tn);
    s.aniso = an; s.at = mix(s.a, 1.0, an * an); s.ab = s.a;
    float f0d = sq((u_ior - 1.0) / (u_ior + 1.0));
    vec3 dielF0 = min(vec3(f0d) * u_specCol, vec3(1.0)) * u_spec;
    s.F0 = mix(dielF0, base.rgb, metal);
    s.F90 = vec3(mix(u_spec, 1.0, metal));
    s.specW = 1.0;
    s.diff = base.rgb * (1.0 - metal) * (1.0 - u_trans * (1.0 - metal));
    s.irid = u_irid;
    s.iridF = u_irid > 0.0 ? evalIridescence(1.0, u_iridIor, s.NdotV, u_iridThick, s.F0) : vec3(0.0);
    s.cc = u_cc; s.ccA = max(u_ccRough * u_ccRough, 1e-3);
    s.sheen = u_sheenCol; s.sheenR = max(u_sheenRough, 0.03);
    vec3 lut = texture(t_lut, vec2(s.NdotV, rough)).rgb;
    float sheenE = texture(t_lut, vec2(s.NdotV, s.sheenR)).b;
    s.sheenScale = 1.0 - max3(u_sheenCol) * sheenE;

    vec3 P = v_wpos;
    vec3 col = vec3(0.0);
    for (int i = 0; i < u_nl; ++i) {
        int t = u_lt[i];
        float sh = 1.0;
        if (u_lsh[i] >= 0 && u_receiveShadow == 1) {
            vec3 Ls = t == 1 ? -u_ld[i] : normalize(u_lp[i] - P);
            sh = shadowAt(u_lsh[i], P, Ng, Ls);
            if (sh <= 0.0) continue;
        }
        if (t == 1) {
            col += lobe(s, -u_ld[i], u_lc[i], u_laff[i]) * sh;
        } else if (t == 2 || t == 3) {
            vec3 dv = u_lp[i] - P; float dist = length(dv); vec3 L = dv / max(dist, 1e-6);
            float f = distAtt(i, dist) * iesFactor(i, -L);
            if (t == 3) f *= spotFactor(i, L);
            if (f > 0.0) col += lobe(s, L, u_lc[i] * f, u_laff[i]) * sh;
        } else if (t >= 4 && t <= 6) {
            col += areaLight(i, s, P) * sh;
        }
    }
    // image-based / ambient light
    float envSh = 1.0;
    if (u_envShIdx >= 0 && u_receiveShadow == 1) {
        envSh = mix(1.0, shadowAt(u_envShIdx, P, Ng, -u_shDir[u_envShIdx]), u_envShadow);
    }
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
    vec3 iblSpec = envSpec(Rv, rough) * Fms;
    vec3 iblDiff = envIrradiance(N) * s.diff / PI * (vec3(1.0) - Fr);
    vec3 iblSheen = u_sheenCol * sheenE * envSpec(Rv, s.sheenR);
    vec3 ibl = (iblDiff * s.sheenScale + iblSheen + iblSpec * s.sheenScale) * ao;
    if (s.cc > 0.0) {
        float cNV = clamp(dot(Ng, V), 1e-4, 1.0);
        vec3 cl = texture(t_lut, vec2(cNV, sqrt(s.ccA))).rgb;
        float Fc = F_Schlick(0.04, cNV) * s.cc;
        ibl = ibl * (1.0 - Fc) + envSpec(reflect(-V, Ng), sqrt(s.ccA)) * (0.04 * cl.x + cl.y) * s.cc * ao;
    }
    col += ibl * envSh;
    // transmission (KHR_materials_transmission + volume + dispersion): screen-space refraction
    float outAlpha = alpha;
    if (u_trans > 0.0) {
        float thick = u_thick * u_upm * u_modelScale;
        vec3 tint = base.rgb;
        if (u_attDist > 0.0 && u_attDist < 1e20 && thick > 0.0) tint *= pow(max(u_attCol, vec3(1e-4)), vec3(thick / (u_attDist * u_upm * u_modelScale)));
        vec3 trans = vec3(0.0); float cover = 0.0;
        float half_ = (u_ior - 1.0) * 0.025 * u_disp;
        vec3 iors = vec3(u_ior - half_, u_ior, u_ior + half_);
        int nch = u_disp > 0.0 ? 3 : 1;
        for (int c = 0; c < 3; ++c) {
            if (c >= nch) break;
            float eta = nch == 1 ? u_ior : iors[c];
            vec3 rd = refract(-V, N, 1.0 / eta);
            vec3 ex = P + (thick > 0.0 ? rd * thick : vec3(0.0));
            vec4 clip = u_viewProj * vec4(ex, 1.0);
            vec2 uv = clip.xy / clip.w * 0.5 + 0.5;
            float lod = log2(max(u_opaqueSize.x, 1.0)) * rough * clamp(eta * 2.0 - 2.0, 0.0, 1.0);
            vec4 smp = u_hasOpaque == 1 ? textureLod(t_opaque, uv, lod) : vec4(0.0);
            if (nch == 1) { trans = smp.rgb; cover = smp.a; }
            else { trans[c] = smp[c]; cover += smp.a / 3.0; }
        }
        vec3 w = u_trans * (1.0 - metal) * (vec3(1.0) - Fr);
        col += trans * tint * w;
        float see = u_trans * (1.0 - metal) * (1.0 - max3(Fr)) * (1.0 - cover) * clamp(dot(tint, vec3(0.2126, 0.7152, 0.0722)), 0.0, 1.0);
        outAlpha = alpha * (1.0 - see);
    }
    col += emis;
    col *= u_exposure;
    float a = u_alphaMode == 2 ? alpha : 1.0;
    if (u_trans > 0.0) { o_color = vec4(col * a, outAlpha); o_depth = vec4(v_vdepth * outAlpha, 0.0, 0.0, outAlpha); return; }
    o_color = vec4(col * a, a);
    o_depth = vec4(v_vdepth * a, 0.0, 0.0, a);
}
""".replace("@MAXL@", str(MAXL)).replace("@MAXS@", str(MAXS)).replace("@MAP_SAMPLE@", MAP_SAMPLE)

DEPTH_VS = """
#version 410
in vec3 in_pos; in vec2 in_uv;
in vec4 in_m0; in vec4 in_m1; in vec4 in_m2; in vec4 in_m3;
uniform mat4 u_vp; uniform vec2 u_uvScale;
out vec3 v_wpos; out vec2 v_uv;
void main() {
    mat4 M = mat4(in_m0, in_m1, in_m2, in_m3);
    vec4 wp = M * vec4(in_pos, 1.0);
    v_wpos = wp.xyz; v_uv = in_uv * u_uvScale;
    gl_Position = u_vp * wp;
}
"""

# Depth-only (prepass) and shadow-map metric output. u_mode: 0 prepass, 1 ortho metric, 2 distance metric.
DEPTH_FS = """
#version 410
in vec3 v_wpos; in vec2 v_uv;
uniform int u_mode; uniform vec3 u_lp; uniform vec3 u_ldir;
uniform int u_alphaMode; uniform float u_cutoff; uniform float u_alpha; uniform int u_hasBase; uniform sampler2D t_base;
uniform ivec2 u_baseMirror;
out vec4 o;
@MAP_SAMPLE@
void main() {
    if (u_alphaMode == 1) {
        float a = u_alpha * (u_hasBase == 1 ? mapSample(t_base, v_uv, u_baseMirror).a : 1.0);
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
uniform int u_n; uniform sampler2D t_env0; uniform sampler2D t_env1; uniform mat3 u_rot0; uniform mat3 u_rot1;
uniform vec3 u_col0; uniform vec3 u_col1; uniform int u_img0; uniform int u_img1; uniform float u_exposure;
layout(location = 0) out vec4 o_color;
layout(location = 1) out vec4 o_depth;
vec2 dirToEquirect(vec3 d) { return vec2(atan(d.x, -d.z) / (2.0 * PI) + 0.5, 0.5 - asin(clamp(d.y, -1.0, 1.0)) / PI); }
void main() {
    vec4 a = u_invViewProj * vec4(v_ndc, -1.0, 1.0);
    vec4 b = u_invViewProj * vec4(v_ndc, 1.0, 1.0);
    vec3 d = u_ortho == 1 ? u_fwd : normalize(b.xyz / b.w - a.xyz / a.w);
    vec3 c = vec3(0.0);
    if (u_n > 0) c += u_col0 * (u_img0 == 1 ? textureLod(t_env0, dirToEquirect(transpose(u_rot0) * d), 0.0).rgb : vec3(1.0));
    if (u_n > 1) c += u_col1 * (u_img1 == 1 ? textureLod(t_env1, dirToEquirect(transpose(u_rot1) * d), 0.0).rgb : vec3(1.0));
    o_color = vec4(c * u_exposure, 1.0);
    o_depth = vec4(1e9, 0.0, 0.0, 1.0);
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
    mat3 J = u_ortho == 1 ? mat3(u_fx, 0.0, 0.0, 0.0, u_fy, 0.0, 0.0, 0.0, 0.0)
                          : mat3(u_fx / z, 0.0, 0.0, 0.0, u_fy / z, 0.0, u_fx * cam.x / (z * z), u_fy * cam.y / (z * z), 0.0);
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
