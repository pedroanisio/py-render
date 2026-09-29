"""Forward PBR renderer (moderngl, GL 4.1) for object3D nodes.

Frame model (pinned):
  * The 3D scene of a frame is every active object3D under the same root (the composition, or a
    symbol) evaluated at its own clock, filmed by the active camera. It is built once per frame
    (shadow maps, environment, and the opaque colour buffer used for refraction are shared).
  * Each object3D node renders its own layer in one pass: the other opaque objects are drawn into the
    depth buffer first (so it is occluded exactly where they are in front of it), then the object is
    shaded. Layers are premultiplied linear radiance at frame resolution, cropped to their pixels,
    and composited by the compositor in stacking order with the node's opacity, blend mode and
    effects. Opaque layers are disjoint, so stacking order among them does not matter; objects with
    opacity < 1, alphaMode blend or transmission do not occlude other layers.
  * Transmissive materials refract the opaque 3D scene (and a visible dome background); where the
    refracted ray finds no 3D surface the 2D backdrop shows through unrefracted (layer alpha reduced
    by the transmitted fraction), since node handlers do not see the compositor's backdrop.
  * Anti-aliasing: 4x MSAA always; project/@antialias3d = N additionally supersamples N x N (box
    filtered), capped at 32 Mpx per pass. Shadow map sizes scale with the render scale.
  * Instancing: object3D/@instances = N draws N GPU instances. Copy i evaluates the transform with
    the expression variables index = i and count = N (as <repeat> does); there is no implicit layout,
    so copies whose transforms are equal coincide (CONVENTIONS 5.2).
  * Mesh animation: clip time = (t - start) * animationSpeed + animationOffset, looping over the clip
    duration (negative speeds play backwards); no animationClip = rest pose. morphWeights override
    the file's weights; materialVariant selects a KHR_materials_variants material. An object3D
    @material replaces every material of the mesh.
  * Gaussian splats (.splat / splat PLY) render as EWA-projected, depth-sorted premultiplied
    Gaussians (3 sigma), unlit; @material is ignored for them.
  * Exposure: camera exposure (EV) scales all 3D radiance; the output stays scene-linear (the colour
    finish hook tone-maps). With project/@linearLight="false" layers are sRGB-encoded (clipped).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from .. import gl
from ..camera import UNITS_PER_METRE, Camera
from . import shaders
from .lights import LightState, dominant_direction, sh9_irradiance, world_environment
from .materials import Material

log = logging.getLogger("scenerender")

VFMT = "3f 3f 2f 4f 4f 2f 2f 2f 2f"
VATTR = ("in_pos", "in_nrm", "in_uv", "in_tan", "in_col", "in_nrmuv", "in_mruv", "in_occuv", "in_emisuv")
IFMT = "4f 4f 4f 4f/i"
IATTR = ("in_m0", "in_m1", "in_m2", "in_m3")
MSAA = 4
LEVELS = 6


class Res:
    """Programs and shared textures of one GL context."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.main = ctx.program(vertex_shader=shaders.MAIN_VS, fragment_shader=shaders.MAIN_FS)
        self.depth = ctx.program(vertex_shader=shaders.DEPTH_VS, fragment_shader=shaders.DEPTH_FS)
        self.bg = ctx.program(vertex_shader=shaders.BG_VS, fragment_shader=shaders.BG_FS)
        self.prefilter = ctx.program(vertex_shader=gl.FULLSCREEN_VS, fragment_shader=shaders.PREFILTER_FS)
        self.splat = ctx.program(vertex_shader=shaders.SPLAT_VS, fragment_shader=shaders.SPLAT_FS)
        self.lut = _tex(ctx, brdf_lut(), mip=False)
        self.white = _tex(ctx, np.ones((1, 1, 4), np.float32), mip=False)
        self.far = _tex(ctx, np.full((1, 1, 4), 1e30, np.float32), mip=False, nearest=True)
        self.env_black = _tex(ctx, np.zeros((LEVELS, 1, 4), np.float32), mip=False)
        quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], np.float32).tobytes())
        self.bg_vao = ctx.vertex_array(self.bg, [(quad, "2f", "in_pos")])
        self.pf_vao = ctx.vertex_array(self.prefilter, [(quad, "2f", "in_pos")])
        self.down = ctx.program(vertex_shader=gl.FULLSCREEN_VS, fragment_shader=shaders.DOWNSAMPLE_FS)
        self.down_vao = ctx.vertex_array(self.down, [(quad, "2f", "in_pos")])
        self.down_targets: dict = {}
        corners = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], np.float32).tobytes())
        self.splat_corners = corners


_RES: dict = {}


def res() -> Res:
    ctx = gl.context()
    if ctx.version_code < 410:
        raise gl.GLUnavailable(f"GL {ctx.version_code} < 4.1")
    r = _RES.get(id(ctx))
    if r is None:
        r = _RES[id(ctx)] = Res(ctx)
    return r


def _tex(ctx, arr: np.ndarray, mip: bool = True, nearest: bool = False, repeat: bool = True):
    import moderngl
    half = np.asarray(arr).dtype == np.float16
    a = np.ascontiguousarray(arr, np.float16 if half else np.float32)
    if a.ndim == 2:
        a = a[..., None]
    h, w, c = a.shape
    t = ctx.texture((w, h), c, a, dtype="f2" if half else "f4")
    if nearest:
        t.filter = (moderngl.NEAREST, moderngl.NEAREST)
    elif mip:
        t.build_mipmaps()
        t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        t.anisotropy = 8.0
    else:
        t.filter = (moderngl.LINEAR, moderngl.LINEAR)
    t.repeat_x = t.repeat_y = repeat
    return t


def _material_texture(ctx, material: Material, key: str, cache: dict):
    """Cache image uploads with their sampler, including independently wrapped axes."""
    arr = material.maps[key]
    sampler = material.p.get("mapSamplers", {}).get(key)
    ck = (id(arr), tuple(sorted(sampler.items())) if sampler else ())
    hit = cache.get(ck)
    if hit is None or hit[0] is not arr:
        if sampler:
            minimum, maximum = sampler.get("minFilter", 9987), sampler.get("magFilter", 9729)
            tex = _tex(ctx, arr, mip=minimum >= 9984)
            tex.filter = (minimum, maximum)
            tex.anisotropy = 1.0
            # Mirrored coordinates are folded in mapSample; clamping at the
            # folded edge gives the same filter footprint as mirrored repeat.
            tex.repeat_x = sampler.get("wrapS", 10497) == 10497
            tex.repeat_y = sampler.get("wrapT", 10497) == 10497
        else:
            tex = _tex(ctx, arr)
        hit = cache[ck] = (arr, tex)
    return hit[1]


def _mirror_axes(material, key):
    sampler = material.p.get("mapSamplers", {}).get(key, {})
    return tuple(int(sampler.get(axis) == 33648) for axis in ("wrapS", "wrapT"))


def brdf_lut(n: int = 32, samples: int = 256) -> np.ndarray:
    """Split-sum LUT (x = NdotV, y = roughness): R = A (F0 scale), G = B (F90 bias) for GGX with
    height-correlated Smith visibility, B channel = directional albedo of the Charlie sheen lobe."""
    nv = (np.arange(n) + 0.5) / n
    rough = (np.arange(n) + 0.5) / n
    NV, R = np.meshgrid(nv, rough)            # rows: roughness
    V = np.stack([np.sqrt(1 - NV ** 2), np.zeros_like(NV), NV], -1)
    i = np.arange(samples)
    xi1 = i / samples
    xi2 = np.array([int(f"{k:032b}"[::-1], 2) / 2 ** 32 for k in i])
    a = (R ** 2)[..., None]
    a2 = a * a
    phi = 2 * math.pi * xi1
    ct = np.sqrt((1 - xi2) / (1 + (a2 - 1) * xi2))
    st = np.sqrt(1 - ct ** 2)
    H = np.stack([st * np.cos(phi), st * np.sin(phi), ct], -1)
    VdotH = np.einsum("...c,...sc->...s", V, H)
    L = 2 * VdotH[..., None] * H - V[..., None, :]
    NdotL = np.clip(L[..., 2], 0, 1)
    NdotH = np.clip(H[..., 2], 0, 1)
    VdotH = np.clip(VdotH, 0, 1)
    nvv = NV[..., None]
    gv = NdotL * np.sqrt(nvv ** 2 * (1 - a2) + a2)
    gl_ = nvv * np.sqrt(NdotL ** 2 * (1 - a2) + a2)
    Vis = np.where(gv + gl_ > 0, 0.5 / np.maximum(gv + gl_, 1e-12), 0)
    G = Vis * 4 * NdotL * VdotH / np.maximum(NdotH, 1e-6)
    Fc = (1 - VdotH) ** 5
    A = np.mean(np.where(NdotL > 0, (1 - Fc) * G, 0), -1)
    B = np.mean(np.where(NdotL > 0, Fc * G, 0), -1)
    # sheen albedo: uniform hemisphere sampling of D_Charlie * V_Neubelt * NdotL
    u1 = xi1
    u2 = xi2
    ctu = u1
    stu = np.sqrt(1 - ctu ** 2)
    Lh = np.stack([stu * np.cos(2 * math.pi * u2), stu * np.sin(2 * math.pi * u2), ctu], -1)
    Hh = Lh[None, None] + V[..., None, :]
    Hh /= np.linalg.norm(Hh, axis=-1, keepdims=True)
    nh = Hh[..., 2]
    sr = np.maximum(R, 0.03)[..., None]
    al = np.maximum(sr ** 2, 1e-4)
    inv = 1 / al
    s2 = np.maximum(1 - nh ** 2, 0.0078125)
    D = (2 + inv) * s2 ** (inv * 0.5) / (2 * math.pi)
    nl = Lh[..., 2][None, None]
    Vn = np.clip(1 / (4 * (nl + nvv - nl * nvv)), 0, 1)
    E = np.mean(D * Vn * nl, -1) * 2 * math.pi
    out = np.stack([A, B, np.clip(E, 0, 1), np.ones_like(A)], -1).astype(np.float32)
    return out


def _set(prog, name: str, value) -> None:
    u = prog.get(name, None) if hasattr(prog, "get") else None
    if u is None:
        try:
            u = prog[name]
        except KeyError:
            return
    u.value = value


def _write(prog, name: str, arr, dtype=np.float32) -> None:
    try:
        u = prog[name]
    except KeyError:
        return
    u.write(np.ascontiguousarray(arr, dtype).tobytes())


# ------------------------------------------------------------------ GPU geometry
@dataclass
class GPUItem:
    vbo: object
    ibo: object
    count: int
    tangents: bool
    vaos: dict = field(default_factory=dict)
    mode: int = 4

    def release(self):
        for v in self.vaos.values():
            v.release()
        self.vbo.release()
        self.ibo.release()


def upload(ctx, positions, normals, uvs, tangents, colors, indices, map_uvs=None, mode=4) -> GPUItem:
    n = len(positions)
    uv = uvs if uvs is not None else np.zeros((n, 2), np.float32)
    tan = tangents if tangents is not None else np.zeros((n, 4), np.float32)
    col = colors if colors is not None else np.ones((n, 4), np.float32)
    if col.shape[1] == 3:
        col = np.c_[col, np.ones(n, np.float32)]
    maps = map_uvs or {}
    data = np.concatenate([np.asarray(positions, np.float32), np.asarray(normals, np.float32),
                           np.asarray(maps.get("baseColorMap", uv), np.float32),
                           np.asarray(tan, np.float32), np.asarray(col, np.float32)] +
                          [np.asarray(maps.get(k, uv), np.float32) for k in
                           ("normalMap", "metallicRoughnessMap", "occlusionMap", "emissiveMap")], 1)
    idx = np.ascontiguousarray(np.asarray(indices).reshape(-1), np.uint32)
    return GPUItem(ctx.buffer(np.ascontiguousarray(data, np.float32).tobytes()), ctx.buffer(idx.tobytes()), len(idx),
                   tangents is not None, mode=mode)


def item_vao(ctx, item: GPUItem, prog, inst) -> object:
    key = (id(prog), id(inst))
    v = item.vaos.get(key)
    if v is None:
        v = ctx.vertex_array(prog, [(item.vbo, VFMT, *VATTR), (inst, IFMT, *IATTR)], item.ibo, skip_errors=True)
        item.vaos[key] = v
    return v


@dataclass
class SplatGPU:
    centers: np.ndarray        # world (n, 3)
    colors: np.ndarray         # (n, 4) linear
    cov: np.ndarray            # (n, 6) world covariance (xx, xy, xz, yy, yz, zz)


@dataclass
class ObjDraw:
    el: object
    items: list                # [(GPUItem, Material)]
    instances: np.ndarray      # (k, 4, 4)
    cast: bool
    receive: bool
    occluder: bool
    center: np.ndarray
    radius: float
    model_scale: float
    splats: SplatGPU | None = None
    inst_buf: object = None
    temp: list = field(default_factory=list)   # per-frame GPU objects to release
    item_inst: list | None = None              # per item (instance buffer, count) when copies differ

    def inst_of(self, i: int) -> tuple:
        """(instance buffer, instance count) item i draws with."""
        if self.item_inst is not None:
            return self.item_inst[i]
        return self.inst_buf, len(self.instances)


@dataclass
class ShadowMap:
    tex: object
    kind: int                  # 0 ortho, 1 perspective, 2 cube atlas
    mat: np.ndarray
    params: tuple              # bias (world), light radius, near, far
    q: tuple                   # texel (uv), world per uv, size, 0
    pos: np.ndarray
    dir: np.ndarray


@dataclass
class EnvGPU:
    atlas: object | None
    sh9: np.ndarray
    amb_d: np.ndarray
    amb_s: np.ndarray
    spec_on: float
    diff_on: float
    visible: list              # [(texture or None, rot, colour)]
    shadow_dir: np.ndarray | None = None
    dominance: float = 0.0


@dataclass
class World3D:
    """Camera-independent part of a frame: objects, lights, environment and shadow maps."""
    objects: dict
    lights: list
    shadows: dict              # light index -> ShadowMap (plus "env")
    env: EnvGPU
    released: bool = False

    def release(self):
        if self.released:
            return
        self.released = True
        for o in self.objects.values():
            for g in o.temp:
                g.release()
            if o.inst_buf is not None:
                o.inst_buf.release()
        for s in self.shadows.values():
            release_shadow_texture(s.tex)


@dataclass
class Frame3D:
    """A World3D filmed by one camera at one render size."""
    cam: Camera
    pw: int
    ph: int
    ox: float
    oy: float
    ssaa: int
    world: World3D
    opaque: object = None      # texture with mipmaps (refraction source)
    frame_scale: float = 1.0   # frame px per document px
    view: tuple | None = None  # (x, y, w, h) of the render target in the camera's document frame

    @property
    def objects(self) -> dict:
        return self.world.objects

    @property
    def lights(self) -> list:
        return self.world.lights

    @property
    def shadows(self) -> dict:
        return self.world.shadows

    @property
    def env(self) -> EnvGPU:
        return self.world.env

    def release(self):
        if self.opaque is not None:
            self.opaque.release()
            self.opaque = None


# ------------------------------------------------------------------ passes
def _material_uniforms(r: Res, prog, m: Material, tex_cache: dict, unit0: int = 0) -> int:
    ctx = r.ctx
    p = m.p
    base = p["baseColor"]
    _set(prog, "u_base", (base[0], base[1], base[2], base[3] * p["opacity"]))
    _set(prog, "u_metal", float(p["metallic"]))
    _set(prog, "u_rough", float(p["roughness"]))
    em = p["emissive"]
    k = float(p["emissiveStrength"])
    _set(prog, "u_emis", (em[0] * k, em[1] * k, em[2] * k))
    _set(prog, "u_alphaMode", {"opaque": 0, "mask": 1, "blend": 2}.get(p["alphaMode"], 0))
    _set(prog, "u_cutoff", float(p["alphaCutoff"]))
    _set(prog, "u_unlit", int(bool(p["unlit"])))
    _set(prog, "u_doubleSided", int(bool(p["doubleSided"])))
    _set(prog, "u_cc", float(p["clearcoat"]))
    _set(prog, "u_ccRough", float(p["clearcoatRoughness"]))
    _set(prog, "u_trans", float(p["transmission"]))
    _set(prog, "u_ior", float(max(1.0, p["ior"])))
    _set(prog, "u_thick", float(p["thickness"]))
    _set(prog, "u_attCol", tuple(p["attenuationColor"][:3]))
    ad = p["attenuationDistance"]
    _set(prog, "u_attDist", float(ad) if ad is not None and math.isfinite(ad) else 1e30)
    _set(prog, "u_sheenCol", tuple(p["sheenColor"][:3]))
    _set(prog, "u_sheenRough", float(p["sheenRoughness"]))
    _set(prog, "u_spec", float(p["specular"]))
    _set(prog, "u_specCol", tuple(p["specularColor"][:3]))
    _set(prog, "u_irid", float(p["iridescence"]))
    _set(prog, "u_iridIor", float(p["iridescenceIor"]))
    _set(prog, "u_iridThick", 400.0)
    _set(prog, "u_aniso", float(p["anisotropy"]))
    _set(prog, "u_anisoRot", math.radians(float(p["anisotropyRotation"])))
    _set(prog, "u_disp", float(p["dispersion"]))
    _set(prog, "u_nScale", float(p["normalScale"]))
    _set(prog, "u_occStrength", float(p.get("occlusionStrength", 1.0)))
    _set(prog, "u_uvScale", (float(p["uvScaleX"]), float(p["uvScaleY"])))
    unit = unit0
    for name, key, flag in (("t_base", "baseColorMap", "u_hasBase"), ("t_nrm", "normalMap", "u_hasNrm"),
                            ("t_mr", "metallicRoughnessMap", "u_hasMR"), ("t_occ", "occlusionMap", "u_hasOcc"),
                            ("t_emis", "emissiveMap", "u_hasEmis")):
        arr = m.maps.get(key)
        t = r.white
        if arr is not None:
            t = _material_texture(ctx, m, key, tex_cache)
        t.use(unit)
        _set(prog, name, unit)
        _set(prog, flag, int(arr is not None))
        _set(prog, "u_" + name[2:] + "Mirror", _mirror_axes(m, key))
        unit += 1
    return unit


def _lights_uniforms(r: Res, prog, fr: Frame3D, unit: int) -> int:
    ML, MS = shaders.MAXL, shaders.MAXS
    lt = np.zeros(ML, np.int32)
    lc = np.zeros((ML, 3), np.float32)
    lp, ld, lr, lu = (np.zeros((ML, 3), np.float32) for _ in range(4))
    ls = np.zeros((ML, 2), np.float32)
    lparam = np.zeros((ML, 4), np.float32)
    laff = np.ones((ML, 2), np.float32)
    lies = np.full(ML, -1, np.int32)
    lsh = np.full(ML, -1, np.int32)
    ies_tex = []
    n = 0
    shadow_slots: list[ShadowMap] = []
    kinds = {"directional": 1, "point": 2, "spot": 3, "rect-area": 4, "disk-area": 5, "sphere-area": 6}
    for i, L in enumerate(fr.lights):
        if L.kind not in kinds or n >= ML:
            continue
        lt[n] = kinds[L.kind]
        lc[n] = L.color
        lp[n], ld[n], lr[n], lu[n] = L.pos, L.fwd, L.right, L.up
        ls[n] = L.size
        lparam[n] = (L.range, L.falloff, L.cos_inner, L.cos_outer)
        laff[n] = (float(L.diffuse), float(L.specular))
        if L.ies is not None and len(ies_tex) < 4:
            key = ("ies", id(L.ies))
            t = fr_cache_get(r, key, L.ies)
            lies[n] = len(ies_tex)
            ies_tex.append(t)
        sm = fr.shadows.get(i)
        if sm is not None and len(shadow_slots) < MS - 1:
            lsh[n] = len(shadow_slots)
            shadow_slots.append(sm)
        n += 1
    _set(prog, "u_nl", n)
    _write(prog, "u_lt", lt, np.int32)
    _write(prog, "u_lc", lc)
    _write(prog, "u_lp", lp)
    _write(prog, "u_ld", ld)
    _write(prog, "u_lr", lr)
    _write(prog, "u_lu", lu)
    _write(prog, "u_ls", ls)
    _write(prog, "u_lparam", lparam)
    _write(prog, "u_laff", laff)
    _write(prog, "u_lies", lies, np.int32)
    _write(prog, "u_lsh", lsh, np.int32)
    ies_units = []
    for k in range(4):
        t = ies_tex[k] if k < len(ies_tex) else r.white
        t.use(unit)
        ies_units.append(unit)
        unit += 1
    _set(prog, "t_ies", ies_units)
    env_sm = fr.shadows.get("env")
    env_idx = -1
    if env_sm is not None:
        env_idx = len(shadow_slots)
        shadow_slots.append(env_sm)
    _set(prog, "u_envShIdx", env_idx)
    _set(prog, "u_envShadow", float(fr.env.dominance) if env_sm is not None else 0.0)
    mats = np.zeros((MS, 4, 4), np.float32)
    kind = np.zeros(MS, np.int32)
    P = np.zeros((MS, 4), np.float32)
    Q = np.zeros((MS, 4), np.float32)
    pos = np.zeros((MS, 3), np.float32)
    dirs = np.zeros((MS, 3), np.float32)
    sh_units = []
    for k in range(MS):
        if k < len(shadow_slots):
            s = shadow_slots[k]
            mats[k] = s.mat.T
            kind[k] = s.kind
            P[k] = s.params
            Q[k] = s.q
            pos[k] = s.pos
            dirs[k] = s.dir
            s.tex.use(unit)
        else:
            r.far.use(unit)
        sh_units.append(unit)
        unit += 1
    _set(prog, "t_sh", sh_units)
    _write(prog, "u_shMat", mats)
    _write(prog, "u_shKind", kind, np.int32)
    _write(prog, "u_shP", P)
    _write(prog, "u_shQ", Q)
    _write(prog, "u_shPos", pos)
    _write(prog, "u_shDir", dirs)
    e = fr.env
    (e.atlas or r.env_black).use(unit)
    _set(prog, "t_env", unit)
    unit += 1
    _set(prog, "u_hasEnv", int(e.atlas is not None))
    _set(prog, "u_envLevels", LEVELS)
    _write(prog, "u_sh9", e.sh9)
    _set(prog, "u_ambD", tuple(float(v) for v in e.amb_d))
    _set(prog, "u_ambS", tuple(float(v) for v in e.amb_s))
    _set(prog, "u_envSpecOn", float(e.spec_on))
    _set(prog, "u_envDiffOn", float(e.diff_on))
    r.lut.use(unit)
    _set(prog, "t_lut", unit)
    unit += 1
    return unit


_IES_TEX: dict = {}


def fr_cache_get(r: Res, key, arr):
    hit = _IES_TEX.get(key)
    if hit is None or hit[0] is not arr:
        hit = (arr, _tex(r.ctx, arr[..., None], mip=False, repeat=False))
        _IES_TEX[key] = hit
    return hit[1]


def _camera_uniforms(prog, fr: Frame3D, V: np.ndarray, P: np.ndarray, exposure: float = 1.0) -> None:
    cam = fr.cam
    _write(prog, "u_view", V.T)
    _write(prog, "u_proj", P.T)
    _write(prog, "u_viewProj", (P @ V).T)
    _set(prog, "u_eye", tuple(float(v) for v in cam.eye))
    _set(prog, "u_camFwd", tuple(float(v) for v in cam.fwd))
    _set(prog, "u_ortho", 1.0 if cam.ortho else 0.0)
    _set(prog, "u_exposure", float(2.0 ** cam.exposure) * exposure)
    _set(prog, "u_upm", UNITS_PER_METRE)


def draw_object(r: Res, fr: Frame3D, obj: ObjDraw, V, P, tex_cache: dict, opaque_tex=None) -> None:
    """Shade one object (all items, all instances) with the current framebuffer state."""
    import moderngl
    ctx = r.ctx
    prog = r.main
    _camera_uniforms(prog, fr, V, P)
    _set(prog, "u_modelScale", float(obj.model_scale))
    _set(prog, "u_receiveShadow", int(obj.receive))
    for i, (item, m) in enumerate(obj.items):
        unit = _material_uniforms(r, prog, m, tex_cache, 0)
        unit = _lights_uniforms(r, prog, fr, unit)
        if opaque_tex is not None and m.transmissive:
            opaque_tex.use(unit)
            _set(prog, "t_opaque", unit)
            _set(prog, "u_hasOpaque", 1)
            _set(prog, "u_opaqueSize", (float(opaque_tex.width), float(opaque_tex.height)))
        else:
            r.white.use(unit)
            _set(prog, "t_opaque", unit)
            _set(prog, "u_hasOpaque", 0)
        buf, k = obj.inst_of(i)
        vao = item_vao(ctx, item, prog, buf)
        ds = bool(m.p["doubleSided"])
        if m.blend or m.transmissive:
            ctx.depth_mask = m.transmissive and not m.blend
            if ds and not m.transmissive and item.mode == 4:
                ctx.enable(moderngl.CULL_FACE)
                ctx.cull_face = "front"
                vao.render(item.mode, instances=k)
                ctx.cull_face = "back"
                vao.render(item.mode, instances=k)
            else:
                ctx.enable(moderngl.CULL_FACE)
                ctx.cull_face = "back"
                vao.render(item.mode, instances=k)
            ctx.depth_mask = True
        else:
            if ds:
                ctx.disable(moderngl.CULL_FACE)
            else:
                ctx.enable(moderngl.CULL_FACE)
                ctx.cull_face = "back"
            vao.render(item.mode, instances=k)
    if obj.splats is not None:
        draw_splats(r, fr, obj, V, P)


def draw_depth(r: Res, obj: ObjDraw, VP: np.ndarray, mode: int, lp=(0, 0, 0), ldir=(0, 0, 1), tex_cache=None) -> None:
    import moderngl
    ctx = r.ctx
    prog = r.depth
    _write(prog, "u_vp", VP.T)
    _set(prog, "u_mode", mode)
    _set(prog, "u_lp", tuple(float(v) for v in lp))
    _set(prog, "u_ldir", tuple(float(v) for v in ldir))
    for i, (item, m) in enumerate(obj.items):
        _set(prog, "u_alphaMode", 1 if m.p["alphaMode"] == "mask" else 0)
        _set(prog, "u_cutoff", float(m.p["alphaCutoff"]))
        _set(prog, "u_alpha", float(m.p["baseColor"][3] * m.p["opacity"]))
        _set(prog, "u_uvScale", (float(m.p["uvScaleX"]), float(m.p["uvScaleY"])))
        arr = m.maps.get("baseColorMap")
        if arr is not None and tex_cache is not None and m.p["alphaMode"] == "mask":
            _material_texture(ctx, m, "baseColorMap", tex_cache).use(0)
            _set(prog, "u_hasBase", 1)
        else:
            _set(prog, "u_hasBase", 0)
        _set(prog, "t_base", 0)
        _set(prog, "u_baseMirror", _mirror_axes(m, "baseColorMap"))
        if m.p["doubleSided"] or mode != 0:
            ctx.disable(moderngl.CULL_FACE)
        else:
            ctx.enable(moderngl.CULL_FACE)
            ctx.cull_face = "back"
        buf, k = obj.inst_of(i)
        item_vao(ctx, item, prog, buf).render(item.mode, instances=k)


_SPLAT_ORDER: dict = {}      # id(splat set) -> (splats, eye, fwd, depth span, instance buffer, order)


def _sorted_splats(ctx, sp, cam):
    """Instance buffer of the splats sorted far to near. The previous one is reused while the camera
    has moved less than 0.5% of the splats' depth range and turned less than 0.5 degree (e.g. the
    shutter samples of one frame): the depth order cannot change enough to be visible."""
    hit = _SPLAT_ORDER.get(id(sp))
    if hit is not None and hit[0] is sp:
        _, eye, fwd, span, buf, _ = hit
        if np.linalg.norm(cam.eye - eye) < 0.005 * span and float(np.dot(cam.fwd, fwd)) > math.cos(math.radians(0.5)):
            return buf, False
    cam_z = (sp.centers - cam.eye) @ cam.fwd
    packed = sp.__dict__.get("_packed")
    if packed is None:      # the instance attributes, interleaved once per splat set
        packed = sp.__dict__["_packed"] = np.ascontiguousarray(
            np.concatenate([sp.centers, sp.colors, sp.cov], 1).astype(np.float32))
    prev = _SPLAT_ORDER.pop(id(sp), None)
    if prev is not None and prev[0] is sp:
        # The same order as sorting from scratch (a stable sort of the negated depths), started from the
        # previous order: the camera moved little, so the input is nearly sorted and the merge sort
        # behind kind="stable" runs in close to linear time. Equal depths keep their index order.
        # Equal depths would keep the previous order instead: then sort from scratch (rare).
        before = prev[5]
        key = -cam_z[before]
        step = np.argsort(key, kind="stable")
        ks = key[step]
        order = before[step] if not np.any(ks[1:] == ks[:-1]) else np.argsort(-cam_z, kind="stable")
    else:
        order = np.argsort(-cam_z, kind="stable")
    data = packed[order]
    span = float(cam_z.max() - cam_z.min()) if len(cam_z) else 0.0
    buf = prev[4] if prev is not None and prev[4].size == data.nbytes else None
    if buf is None:
        if prev is not None:
            prev[4].release()
        buf = ctx.buffer(data)
    else:
        buf.write(data)
    while len(_SPLAT_ORDER) >= 4:                       # a few splat objects at a time
        _SPLAT_ORDER.pop(next(iter(_SPLAT_ORDER)))[4].release()
    _SPLAT_ORDER[id(sp)] = (sp, np.array(cam.eye, copy=True), np.array(cam.fwd, copy=True), span, buf, order)
    return buf, True


_SPLAT_GPU: dict = {}        # id(splat set) -> _GpuSplats (GL 4.3 path, see _gpu_sorted_splats)


class _GpuSplats:
    """A splat set on the GPU: attributes and centres uploaded once, keys and order sorted there."""

    def __init__(self, ctx, sp):
        self.sp, self.n = sp, len(sp.centers)
        self.pad = 1 << max(0, (self.n - 1).bit_length())
        packed = np.concatenate([sp.centers, sp.colors, sp.cov], 1).astype(np.float32)
        self.attr = ctx.buffer(np.ascontiguousarray(packed))
        self.centres = ctx.buffer(np.ascontiguousarray(sp.centers, np.float64))
        self.keys = ctx.buffer(reserve=self.pad * 8)
        self.vals = ctx.buffer(reserve=self.pad * 4)
        self.eye = self.fwd = None
        self.span = 0.0

    def release(self):
        for b in (self.attr, self.centres, self.keys, self.vals):
            b.release()


def _gpu_sort_programs(r: Res):
    progs = getattr(r, "splat_sort", None)
    if progs is None:
        from . import shaders
        progs = r.splat_sort = (r.ctx.compute_shader(shaders.SPLAT_KEYS_CS), r.ctx.compute_shader(shaders.SPLAT_SORT_CS),
                                r.ctx.program(vertex_shader=shaders.SPLAT_VS_SSBO, fragment_shader=shaders.SPLAT_FS))
    return progs


def _gpu_sorted_splats(r: Res, sp, cam) -> "_GpuSplats":
    """The splat set on the GPU with its order buffer sorted far to near for cam: the order of
    _sorted_splats (a stable sort of the negated depths; depths equal to within float64 rounding may
    differ), computed by a bitonic sort in compute shaders. Reused under the same conditions."""
    g = _SPLAT_GPU.get(id(sp))
    if g is None or g.sp is not sp:
        while len(_SPLAT_GPU) >= 4:
            _SPLAT_GPU.pop(next(iter(_SPLAT_GPU))).release()
        g = _SPLAT_GPU[id(sp)] = _GpuSplats(r.ctx, sp)
    if g.eye is not None and np.linalg.norm(cam.eye - g.eye) < 0.005 * g.span \
            and float(np.dot(cam.fwd, g.fwd)) > math.cos(math.radians(0.5)):
        return g
    keys_cs, sort_cs, _ = _gpu_sort_programs(r)
    g.centres.bind_to_storage_buffer(0)
    g.keys.bind_to_storage_buffer(1)
    g.vals.bind_to_storage_buffer(2)
    groups = -(-g.pad // 256)
    keys_cs["u_n"].value, keys_cs["u_pad"].value = g.n, g.pad
    keys_cs["u_eye"].value = tuple(float(v) for v in cam.eye)
    keys_cs["u_fwd"].value = tuple(float(v) for v in cam.fwd)
    keys_cs.run(groups)
    sort_cs["u_pad"].value = g.pad
    uk, uj = sort_cs["u_k"], sort_cs["u_j"]
    k = 2
    while k <= g.pad:
        uk.value = k
        j = k >> 1
        while j > 0:
            uj.value = j
            r.ctx.memory_barrier()
            sort_cs.run(groups)
            j >>= 1
        k <<= 1
    r.ctx.memory_barrier()
    # The depth span sets the reuse threshold, as on the CPU path (the eye cancels out).
    cz = sp.centers @ cam.fwd
    g.span = float(cz.max() - cz.min())
    g.eye, g.fwd = np.array(cam.eye, copy=True), np.array(cam.fwd, copy=True)
    return g


def draw_splats(r: Res, fr: Frame3D, obj: ObjDraw, V, P) -> None:
    import moderngl
    ctx = r.ctx
    sp = obj.splats
    n_splats = len(sp.centers)
    if ctx.version_code >= 430 and n_splats > 0:
        g = _gpu_sorted_splats(r, sp, fr.cam)
        prog = _gpu_sort_programs(r)[2]
        g.attr.bind_to_storage_buffer(0)
        g.vals.bind_to_storage_buffer(1)
        vao = ctx.vertex_array(prog, [(r.splat_corners, "2f", "in_corner")], skip_errors=True)
    else:
        ibuf, _ = _sorted_splats(ctx, sp, fr.cam)
        prog = r.splat
        vao = ctx.vertex_array(prog, [(r.splat_corners, "2f", "in_corner"),
                                      (ibuf, "3f 4f 3f 3f/i", "in_center", "in_color", "in_covA", "in_covB")],
                               skip_errors=True)
    _write(prog, "u_view", V.T)
    _write(prog, "u_proj", P.T)
    cam = fr.cam
    s = fr.pw / view_rect(fr)[2]
    k = cam.k_ortho if cam.ortho else cam.fpx
    _set(prog, "u_fx", float(k * s))
    _set(prog, "u_fy", float(k * s))
    _set(prog, "u_ortho", int(cam.ortho))
    _set(prog, "u_vpSize", (float(fr.pw), float(fr.ph)))
    _set(prog, "u_exposure", float(2.0 ** cam.exposure))
    ctx.disable(moderngl.CULL_FACE)
    ctx.depth_mask = False
    vao.render(moderngl.TRIANGLE_STRIP, vertices=4, instances=n_splats)
    ctx.depth_mask = True
    vao.release()


def draw_background(r: Res, fr: Frame3D, V, P) -> bool:
    vis = fr.env.visible
    if not vis:
        return False
    prog = r.bg
    _write(prog, "u_invViewProj", np.linalg.inv(P @ V).T)
    _set(prog, "u_ortho", int(fr.cam.ortho))
    _set(prog, "u_fwd", tuple(float(v) for v in fr.cam.fwd))
    _set(prog, "u_n", min(2, len(vis)))
    for k in range(2):
        t, rot, col = vis[k] if k < len(vis) else (None, np.eye(3), np.zeros(3))
        (t or r.white).use(k)
        _set(prog, f"t_env{k}", k)
        _write(prog, f"u_rot{k}", np.asarray(rot, np.float32).T)
        _set(prog, f"u_col{k}", tuple(float(v) for v in col))
        _set(prog, f"u_img{k}", int(t is not None))
    _set(prog, "u_exposure", float(2.0 ** fr.cam.exposure))
    r.ctx.depth_mask = False
    r.bg_vao.render(5)       # TRIANGLE_STRIP
    r.ctx.depth_mask = True
    return True


# ------------------------------------------------------------------ frame setup
def look_view(eye, fwd, up_hint) -> np.ndarray:
    f = fwd / np.linalg.norm(fwd)
    r = np.cross(f, up_hint)
    if np.linalg.norm(r) < 1e-6:
        r = np.cross(f, np.array([1.0, 0.0, 0.0]) if abs(f[0]) < 0.9 else np.array([0.0, 0.0, 1.0]))
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    V = np.eye(4)
    V[0, :3], V[1, :3], V[2, :3] = r, u, -f
    V[:3, 3] = -V[:3, :3] @ eye
    return V


def persp(fov_deg: float, aspect: float, n: float, f: float) -> np.ndarray:
    t = 1 / math.tan(math.radians(fov_deg) / 2)
    P = np.zeros((4, 4))
    P[0, 0], P[1, 1] = t / aspect, t
    P[2, 2], P[2, 3] = -(f + n) / (f - n), -2 * f * n / (f - n)
    P[3, 2] = -1
    return P


def ortho(half_w: float, half_h: float, n: float, f: float) -> np.ndarray:
    P = np.eye(4)
    P[0, 0], P[1, 1] = 1 / half_w, 1 / half_h
    P[2, 2], P[2, 3] = -2 / (f - n), -(f + n) / (f - n)
    return P


CUBE_F = [np.array(v, float) for v in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
CUBE_U = [np.array(v, float) for v in ((0, 1, 0), (0, 1, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, 1, 0))]


def _cube_view(eye, f) -> np.ndarray:
    F, U = CUBE_F[f], CUBE_U[f]
    R = np.cross(F, U)
    V = np.eye(4)
    V[0, :3], V[1, :3], V[2, :3] = R, U, -F
    V[:3, 3] = -V[:3, :3] @ eye
    return V


_SHADOW_FREE: dict = {}       # (w, h) -> [(texture, framebuffer)] ready for reuse
_SHADOW_PARTS: dict = {}      # id(texture) -> (texture, depth renderbuffer, framebuffer), in use or free


def _shadow_target(ctx, W: int, H: int):
    """A W x H shadow-map texture with its depth-tested framebuffer, reused once released (the map size
    is fixed per light, and each frame and shutter sample renders the maps again)."""
    import moderngl
    free = _SHADOW_FREE.get((W, H))
    if free:
        return free.pop()
    tex = ctx.texture((W, H), 1, dtype="f4")
    tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
    tex.repeat_x = tex.repeat_y = False
    depth = ctx.depth_renderbuffer((W, H))
    fbo = ctx.framebuffer([tex], depth)
    _SHADOW_PARTS[id(tex)] = (tex, depth, fbo)
    return tex, fbo


def release_shadow_texture(tex) -> None:
    """Return a shadow map's texture (and its framebuffer) for reuse; a few per size are kept."""
    parts = _SHADOW_PARTS.get(id(tex))
    if parts is None or parts[0] is not tex:
        tex.release()
        return
    free = _SHADOW_FREE.setdefault(tex.size, [])
    if len(free) < 4:
        free.append((tex, parts[2]))
        return
    del _SHADOW_PARTS[id(tex)]
    for o in (parts[2], parts[1], tex):
        o.release()


def render_shadow(r: Res, fr_objs: list, L: LightState, bounds, scale: float, tex_cache) -> ShadowMap | None:
    """Shadow map of light L over the casters (bounds = (centre, radius) of all casters)."""
    import moderngl
    ctx = r.ctx
    casters = [o for o in fr_objs if o.cast]
    if not casters:
        return None
    c, rad = bounds
    rad = max(rad, 1.0)
    size = int(max(16, min(8192, round(L.map_size * max(0.25, min(1.0, scale))))))
    kind = 0
    if L.kind in ("directional", "dome"):
        kind = 0
    elif L.kind == "spot":
        kind = 1
    else:
        kind = 2
    if kind == 2:
        size = min(size, 2730)
        W, H = size * 3, size * 2
    else:
        W = H = size
    tex, fbo = _shadow_target(ctx, W, H)
    fbo.use()
    fbo.clear(1e30, 0, 0, 0, depth=1.0)
    ctx.enable(moderngl.DEPTH_TEST)
    ctx.disable(moderngl.BLEND)
    up = L.up if np.linalg.norm(L.up) > 0.5 else np.array([0.0, 1.0, 0.0])
    if kind == 0:
        d = L.fwd / np.linalg.norm(L.fwd)
        eye = c - d * rad * 2
        V = look_view(eye, d, up)
        Pm = ortho(rad, rad, 0.01, rad * 4)
        VP = Pm @ V
        ctx.viewport = (0, 0, W, H)
        for o in casters:
            draw_depth(r, o, VP, 1, eye, d, tex_cache)
        if L.kind == "dome":
            lr = 0.5 * math.tan(math.acos(min(1.0, max(0.0, L.softness))))
        else:
            lr = math.tan(math.radians(L.softness))
        sm = ShadowMap(tex, 0, VP, (L.bias * rad * 4, lr, 0.01, rad * 4), (1.0 / size, rad * 2, size, 0), eye, d)
    elif kind == 1:
        dist = float(np.linalg.norm(c - L.pos)) + rad
        far = L.range if L.range > 0 else max(dist, 1.0)
        near = max(0.5, far * 1e-4)
        fov = min(170.0, 2 * math.degrees(math.acos(max(-1.0, min(1.0, L.cos_outer)))) + 2.0)
        V = look_view(L.pos, L.fwd, up)
        Pm = persp(fov, 1.0, near, far)
        VP = Pm @ V
        ctx.viewport = (0, 0, W, H)
        for o in casters:
            draw_depth(r, o, VP, 2, L.pos, L.fwd, tex_cache)
        sm = ShadowMap(tex, 1, VP, (L.bias * (far - near), _light_radius(L), near, far),
                       (1.0 / size, 2 * math.tan(math.radians(fov) / 2), size, 0), L.pos.copy(), L.fwd.copy())
    else:
        dist = float(np.linalg.norm(c - L.pos)) + rad
        far = L.range if L.range > 0 else max(dist, 1.0)
        near = max(0.5, far * 1e-4)
        Pm = persp(90.0, 1.0, near, far)
        for f in range(6):
            ctx.viewport = ((f % 3) * size, (f // 3) * size, size, size)
            VP = Pm @ _cube_view(L.pos, f)
            for o in casters:
                draw_depth(r, o, VP, 2, L.pos, L.fwd, tex_cache)
        sm = ShadowMap(tex, 2, np.eye(4), (L.bias * (far - near), _light_radius(L), near, far),
                       (1.0 / size, 2.0, size, 0), L.pos.copy(), L.fwd.copy())
    restore_state(r)          # the framebuffer stays with the texture (see release_shadow_texture)
    return sm


def _light_radius(L: LightState) -> float:
    if L.softness > 0:
        return L.softness * UNITS_PER_METRE
    if L.kind == "rect-area":
        return math.sqrt(4 * L.size[0] * L.size[1] / math.pi)
    if L.kind in ("disk-area", "sphere-area"):
        return L.size[0]
    return 0.0


_ENV_CACHE: dict = {}


def build_env(r: Res, lights: list[LightState]) -> EnvGPU:
    import moderngl
    ctx = r.ctx
    domes = [L for L in lights if L.kind == "dome"]
    amb = [L for L in lights if L.kind == "ambient"]
    amb_d = sum((L.color * np.pi for L in amb if L.diffuse), np.zeros(3))
    amb_s = sum((L.color for L in amb if L.specular), np.zeros(3))
    if not domes:
        return EnvGPU(None, np.zeros((9, 3), np.float32), amb_d, amb_s, 0.0, 0.0, [])
    key = tuple((id(L.env) if L.env is not None else None, tuple(np.round(L.env_rot, 6).ravel()),
                 tuple(np.round(L.color, 6)), L.diffuse, L.specular) for L in domes)
    hit = _ENV_CACHE.get(key)
    if hit is None or any(h is not L.env for h, L in zip(hit[0], domes)):
        spec_env = world_environment(domes, 512, 256, specular=True)
        diff_env = world_environment(domes, 128, 64, specular=False)
        # texture row 0 = image top (+Y): shader lookups use t = v_equirect directly
        src = _tex(ctx, np.c_[spec_env.reshape(-1, 3), np.ones(512 * 256)].reshape(256, 512, 4), mip=True)
        src.repeat_y = False                          # equirect: wrap in longitude, clamp at the poles
        lw, lh = 256, 128
        atlas = ctx.texture((lw, lh * LEVELS), 4, dtype="f4")
        atlas.filter = (moderngl.LINEAR, moderngl.LINEAR)
        atlas.repeat_x = True
        atlas.repeat_y = False
        fbo = ctx.framebuffer([atlas])
        fbo.use()
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.BLEND)
        src.use(0)
        _set(r.prefilter, "t_src", 0)
        _set(r.prefilter, "u_srcW", 512.0)
        for lvl in range(LEVELS):
            # level lvl fills texture t in [lvl, lvl + 1) / LEVELS; inside it t grows with v_equirect
            ctx.viewport = (0, lvl * lh, lw, lh)
            _set(r.prefilter, "u_rough", lvl / (LEVELS - 1))
            r.pf_vao.render(5)
        fbo.release()
        src.release()
        sh = sh9_irradiance(diff_env)
        dom, dn = dominant_direction(diff_env)
        vis = []
        for L in domes:
            if L.visible:
                t = None
                if L.env is not None:
                    img = L.env
                    t = _tex(ctx, np.c_[img.reshape(-1, 3), np.ones(img.shape[0] * img.shape[1])]
                             .reshape(img.shape[0], img.shape[1], 4), mip=False)
                    t.repeat_y = False
                vis.append((t, L.env_rot, L.color))
        hit = ([L.env for L in domes], atlas, sh, vis, dom, dn)
        for old in list(_ENV_CACHE):
            _ENV_CACHE.pop(old)[1].release()
        _ENV_CACHE[key] = hit
    _, atlas, sh, vis, dom, dn = hit
    spec_on = 1.0 if any(L.specular for L in domes) else 0.0
    diff_on = 1.0 if any(L.diffuse for L in domes) else 0.0
    return EnvGPU(atlas, sh, amb_d, amb_s, spec_on, diff_on, vis, dom, dn)


# ------------------------------------------------------------------ targets
@dataclass
class Target:
    fbo: object
    color: object
    depth_att: object
    zbuf: object
    resolve: object
    rc_tex: object
    rd_tex: object
    w: int
    h: int

    def release(self):
        for o in (self.fbo, self.color, self.depth_att, self.zbuf, self.resolve, self.rc_tex, self.rd_tex):
            o.release()


_TARGETS: dict = {}


def target(r: Res, w: int, h: int) -> Target:
    t = _TARGETS.get((w, h))
    if t is None:
        ctx = r.ctx
        for k in list(_TARGETS):
            _TARGETS.pop(k).release()
        color = ctx.renderbuffer((w, h), 4, samples=MSAA, dtype="f4")
        datt = ctx.renderbuffer((w, h), 4, samples=MSAA, dtype="f4")
        z = ctx.depth_renderbuffer((w, h), samples=MSAA)
        fbo = ctx.framebuffer([color, datt], z)
        rc_tex = ctx.texture((w, h), 4, dtype="f4")
        rd_tex = ctx.texture((w, h), 4, dtype="f4")
        resolve = ctx.framebuffer([rc_tex, rd_tex])
        t = _TARGETS[(w, h)] = Target(fbo, color, datt, z, resolve, rc_tex, rd_tex, w, h)
    return t


def _begin(r: Res, t: Target):
    import moderngl
    ctx = r.ctx
    t.fbo.use()
    ctx.viewport = (0, 0, t.w, t.h)
    t.fbo.color_mask = ((True, True, True, True), (True, True, True, True))
    t.fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
    ctx.enable(moderngl.DEPTH_TEST)
    ctx.depth_func = "<"
    ctx.enable(moderngl.BLEND)
    ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
    ctx.blend_equation = moderngl.FUNC_ADD
    ctx.front_face = "ccw"


def restore_state(r: Res) -> None:
    """Leave the shared context in its default state (other GL users assume it)."""
    import moderngl
    ctx = r.ctx
    ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE | moderngl.BLEND)
    ctx.depth_mask = True
    ctx.blend_func = moderngl.DEFAULT_BLENDING


def _down_target(r: Res, w: int, h: int):
    """Colour + depth framebuffer of the SSAA downsample pass, at least w x h (the pass draws into its
    (0, 0, w, h) corner). Sizes are rounded up to 128-pixel steps and the least recently used of more
    than 16 is dropped: a layer's screen rect changes size from frame to frame, and each new size
    otherwise allocates textures and a framebuffer."""
    key = (-(-w // 128) * 128, -(-h // 128) * 128)
    hit = r.down_targets.pop(key, None)
    if hit is None:
        while len(r.down_targets) >= 16:
            fb, a, b = r.down_targets.pop(next(iter(r.down_targets)))
            fb.release(); a.release(); b.release()
        a = r.ctx.texture(key, 4, dtype="f4")
        b = r.ctx.texture(key, 4, dtype="f4")
        hit = (r.ctx.framebuffer([a, b]), a, b)
    r.down_targets[key] = hit          # most recently used last
    return hit


def _read(r: Res, t: Target, rect=None, depth: bool = True, n: int = 1, gpu: bool = False):
    """Resolve and read (colour, depth or None) of rect = (x0, y0, x1, y1) in top-down pixels, reduced
    by an exact n x n box average (SSAA) on the GPU when n > 1; rect must then be n-aligned.
    gpu: the colour stays on the GPU as a top-down gpucomp.GpuTile (no depth)."""
    ctx = r.ctx
    ctx.copy_framebuffer(t.resolve, t.fbo)
    x0, y0, x1, y1 = rect if rect is not None else (0, 0, t.w, t.h)
    if gpu:
        from .. import gpucomp
        if n <= 1:
            return gpucomp.from_gl(t.rc_tex, x0, t.h - y1, x1 - x0, y1 - y0), None
        depth = False
    if n > 1:
        import moderngl
        w, h = (x1 - x0) // n, (y1 - y0) // n
        fb, col_tex, _ = _down_target(r, w, h)
        fb.use()
        ctx.viewport = (0, 0, w, h)
        ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND | moderngl.CULL_FACE)
        t.rc_tex.use(0)
        t.rd_tex.use(1)
        _set(r.down, "u_col", 0)
        _set(r.down, "u_dep", 1)
        _set(r.down, "u_n", int(n))
        _set(r.down, "u_depth", int(bool(depth)))
        _set(r.down, "u_origin", (int(x0), int(t.h - y1)))
        r.down_vao.render(moderngl.TRIANGLE_STRIP)
        if gpu:
            return gpucomp.from_gl(col_tex, 0, 0, w, h), None
        col = np.frombuffer(fb.read(viewport=(0, 0, w, h), components=4, dtype="f4", attachment=0), np.float32)
        col = np.ascontiguousarray(col.reshape(h, w, 4)[::-1])
        dep = None
        if depth:
            d = np.frombuffer(fb.read(viewport=(0, 0, w, h), components=4, dtype="f4", attachment=1), np.float32)
            dep = np.ascontiguousarray(d.reshape(h, w, 4)[::-1])
        return col, dep
    vp = (x0, t.h - y1, x1 - x0, y1 - y0)
    h, w = y1 - y0, x1 - x0
    col = np.frombuffer(t.resolve.read(viewport=vp, components=4, dtype="f4", attachment=0), np.float32)
    col = np.ascontiguousarray(col.reshape(h, w, 4)[::-1])
    dep = None
    if depth:
        d = np.frombuffer(t.resolve.read(viewport=vp, components=4, dtype="f4", attachment=1), np.float32)
        dep = np.ascontiguousarray(d.reshape(h, w, 4)[::-1])
    return col, dep


def screen_rect(fr: Frame3D, obj: ObjDraw, align: int = 1):
    """Conservative top-down pixel rect of an object's bounding sphere in the render target."""
    cam = fr.cam
    c, rad = obj.center, obj.radius
    pts = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], np.float64) * rad + c
    C = cam.to_cam(pts)
    if obj.splats is not None or (not cam.ortho and np.any(C[:, 2] <= cam.near)):
        return (0, 0, fr.pw, fr.ph)
    vx, vy, vw, vh = view_rect(fr)
    s = cam.project_cam(C) - [vx, vy]
    kx, ky = fr.pw / vw, fr.ph / vh
    x0 = int(math.floor(s[:, 0].min() * kx)) - 2
    x1 = int(math.ceil(s[:, 0].max() * kx)) + 2
    y0 = int(math.floor(s[:, 1].min() * ky)) - 2
    y1 = int(math.ceil(s[:, 1].max() * ky)) + 2
    x0, y0 = max(0, x0 - x0 % align), max(0, y0 - y0 % align)
    x1, y1 = min(fr.pw, x1 + (-x1) % align), min(fr.ph, y1 + (-y1) % align)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def view_rect(fr: Frame3D) -> tuple:
    return fr.view if fr.view is not None else (0.0, 0.0, fr.cam.W, fr.cam.H)


def matrices(fr: Frame3D) -> tuple[np.ndarray, np.ndarray]:
    cam = fr.cam
    vx, vy, vw, vh = view_rect(fr)
    return cam.view_matrix(), cam.proj_matrix(vw, vh, offset=(vx, vy))


def render_opaque(r: Res, fr: Frame3D, tex_cache: dict):
    """Colour of all opaque occluders + visible dome, with mipmaps (refraction source)."""
    import moderngl
    t = target(r, fr.pw, fr.ph)
    _begin(r, t)
    V, P = matrices(fr)
    draw_background(r, fr, V, P)
    for o in fr.objects.values():
        if o.occluder:
            draw_object(r, fr, o, V, P, tex_cache)
    col, _ = _read(r, t, None, False)
    restore_state(r)
    tex = r.ctx.texture((t.w, t.h), 4, np.ascontiguousarray(col[::-1]).tobytes(), dtype="f4")
    tex.build_mipmaps()
    tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
    tex.repeat_x = tex.repeat_y = False
    return tex


def render_layer(r: Res, fr: Frame3D, obj: ObjDraw, tex_cache: dict, depth: bool = True, gpu: bool = False):
    """(premultiplied colour, premultiplied view depth or None, (x0, y0)) of one object, read back over
    its screen rect at the frame's render size; None when it is off screen. gpu: the colour stays on
    the GPU (a gpucomp.GpuTile, see _read)."""
    import moderngl
    rect = screen_rect(fr, obj, fr.ssaa)
    if rect is None:
        return None
    needs_opaque = any(m.transmissive for _, m in obj.items)
    if needs_opaque and fr.opaque is None:
        fr.opaque = render_opaque(r, fr, tex_cache)
    t = target(r, fr.pw, fr.ph)
    _begin(r, t)
    V, P = matrices(fr)
    VP = P @ V
    t.fbo.color_mask = ((False, False, False, False), (False, False, False, False))
    for o in fr.objects.values():
        if o is not obj and o.occluder:
            draw_depth(r, o, VP, 0, tex_cache=tex_cache)
    t.fbo.color_mask = ((True, True, True, True), (True, True, True, True))
    r.ctx.enable(moderngl.BLEND)
    draw_object(r, fr, obj, V, P, tex_cache, fr.opaque)
    col, dep = _read(r, t, rect, depth, n=fr.ssaa, gpu=gpu)
    restore_state(r)
    return col, dep, rect[:2]


def render_background(r: Res, fr: Frame3D, gpu: bool = False):
    if not fr.env.visible:
        return None
    t = target(r, fr.pw, fr.ph)
    _begin(r, t)
    V, P = matrices(fr)
    draw_background(r, fr, V, P)
    col = _read(r, t, None, False, n=fr.ssaa, gpu=gpu)[0]
    restore_state(r)
    return col
