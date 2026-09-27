"""<lights> evaluation for the 3D renderer.

Pinned decisions (units follow glTF KHR_lights_punctual with camera.UNITS_PER_METRE = 100 scene
units per metre; radiance values feed a linear HDR pipeline, the colour finish hook tone-maps):
  * Every light: radiometric colour = linear(color) x blackbody(colorTemperature) x intensity x
    2**exposure. colorTemperature tints by the Planckian-locus chromaticity (Kang et al. 2002, valid
    1667-25000 K; values outside are clamped to that range) normalised to unit luminance.
  * Orientation: position x/y/z, forward = local -Z of Ry(-yaw) . Rx(pitch) . Rz(roll) (like cameras);
    @parent and transformConstraint compose as for object3D.
  * ambient: constant radiance intensity from every direction (diffuse irradiance pi*L and, when
    affectsSpecular, the split-sum specular of a uniform environment). Uniform light casts no shadow.
  * directional: irradiance (lux) along forward.
  * point / spot: luminous intensity (cd); irradiance = I / d^falloff with d in metres (falloff 2 is
    the inverse-square law), times the KHR_lights_punctual range window
    clamp(1 - (d/range)^4, 0, 1)^2 when range is set (scene units). spot: spotAngle is the full
    outer cone angle, innerConeAngle the full inner cone angle (default 0), glTF smooth falloff.
  * rect-area (width x height, default 1 m square), disk-area (radius, default 0.5 m) and sphere-area
    (radius, default 0.5 m): intensity is the luminous intensity (cd) of the whole emitter along its
    normal (sphere: in every direction); rect/disk emit from their -Z face with a Lambertian
    cos(theta) distribution. Diffuse is integrated over 16 stratified emitter points (sphere: exact
    point equivalence), specular uses the representative point (closest point to the reflection ray)
    with Karis' energy normalisation.
  * ies: LM-63 profile normalised to its peak, so intensity stays the peak candela; theta = 0 is the
    light's forward axis (photometric nadir), phi = 0 its local +X, increasing toward local +Y.
  * dome: equirectangular HDRI (environment; the image centre looks down world -Z, u grows toward
    +X, v = 0 is +Y), rotated by yaw/pitch/roll, radiance x colour x intensity; without environment
    a uniform radiance. Several domes are summed. Diffuse = 9-coefficient SH irradiance, specular =
    GGX prefiltered mips + split-sum LUT. environmentVisible draws it as the background (through the
    active camera node's layer). castShadow on a dome shadows its dominant direction (the
    luminance-weighted mean direction) with a shadow map, weighted by the dominance (|mean|).
  * castShadow: shadow maps (directional: orthographic fit to the shadow casters; spot: perspective
    cone; point/area/dome-dominant: cube maps) of shadowMapSize texels, PCSS soft shadows where the
    light radius is shadowSoftness (metres; directional: degrees of angular radius) or, when 0, the
    area light's own size; shadowBias is a fraction of the shadow depth range, plus a one-texel
    normal offset.
  * affectsDiffuse / affectsSpecular gate the diffuse (incl. sheen) and specular (incl. clearcoat)
    lobes of that light.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..camera import UNITS_PER_METRE, node_clock, world3d
from ..document import ln
from ..evaluator import Ctx
from ..raster import srgb_to_linear
from ..registry import FEATURES, FULL, warn_once

FEATURES.declare("lights", FULL, "ambient, directional, point, spot, rect/disk/sphere area (stratified diffuse + "
                                  "representative-point specular), dome IBL, IES, colour temperature, PCSS shadows")

TYPES = {"ambient": 0, "directional": 1, "point": 2, "spot": 3, "rect-area": 4, "disk-area": 5, "sphere-area": 6,
         "dome": 7}


@dataclass
class LightState:
    el: object
    kind: str
    color: np.ndarray                 # linear radiometric colour x intensity
    pos: np.ndarray
    right: np.ndarray
    up: np.ndarray
    fwd: np.ndarray
    size: tuple = (0.0, 0.0)          # rect half extents / disk & sphere radius (scene units)
    range: float = 0.0                # 0 = infinite
    falloff: float = 2.0
    cos_inner: float = 1.0
    cos_outer: float = 0.0
    shadow: bool = False
    softness: float = 0.0
    bias: float = 0.0005
    map_size: int = 2048
    diffuse: bool = True
    specular: bool = True
    ies: np.ndarray | None = None     # (n_phi, n_theta) normalised table
    env: np.ndarray | None = None     # dome: (h, w, 3) linear radiance (already x colour x intensity)
    env_rot: np.ndarray = field(default_factory=lambda: np.eye(3))    # world-from-env rotation
    visible: bool = False
    key: tuple = ()


def kelvin_rgb(T: float) -> np.ndarray:
    """Linear sRGB of the Planckian locus at T kelvin, unit luminance (Kang et al. 2002)."""
    T = min(25000.0, max(1667.0, float(T)))
    if T <= 4000:
        x = -0.2661239e9 / T ** 3 - 0.2343589e6 / T ** 2 + 0.8776956e3 / T + 0.179910
    else:
        x = -3.0258469e9 / T ** 3 + 2.1070379e6 / T ** 2 + 0.2226347e3 / T + 0.240390
    if T <= 2222:
        y = -1.1063814 * x ** 3 - 1.34811020 * x ** 2 + 2.18555832 * x - 0.20219683
    elif T <= 4000:
        y = -0.9549476 * x ** 3 - 1.37418593 * x ** 2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x ** 3 - 5.87338670 * x ** 2 + 3.75112997 * x - 0.37001483
    XYZ = np.array([x / y, 1.0, (1 - x - y) / y])
    M = np.array([[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560],
                  [0.0556434, -0.2040259, 1.0572252]])
    rgb = np.maximum(M @ XYZ, 0.0)
    lum = float(np.array([0.2126, 0.7152, 0.0722]) @ rgb)
    return rgb / max(lum, 1e-9)


def light_elements(rc) -> list:
    key = ("light-els",)
    hit = rc.cache.get(key)
    if hit is None:
        sec = rc.doc.section("lights")
        hit = [e for e in sec if isinstance(e.tag, str) and ln(e) == "light"] if sec is not None else []
        rc.cache[key] = hit
    return hit


def evaluate(rc, t: float) -> list[LightState]:
    out = []
    for el in light_elements(rc):
        c = node_clock(rc, el, t) or Ctx(t=t, comp_t=t)
        out.append(_eval(rc, el, c))
    return out


def _eval(rc, el, c: Ctx) -> LightState:
    ev = rc.ev
    kind = el.get("type", "point")
    col = np.array(srgb_to_linear(np.array(ev.color(el, "color", c, (1, 1, 1, 1))[:3], np.float32)), np.float64)
    if el.get("colorTemperature") is not None or any(isinstance(a.tag, str) and a.get("property") == "colorTemperature"
                                                      for a in el):
        col = col * kelvin_rgb(ev.num(el, "colorTemperature", c, 6500.0))
    col = col * ev.num(el, "intensity", c, 1.0) * 2.0 ** ev.num(el, "exposure", c, 0.0)
    M = world3d(rc, el, c)
    R = M[:3, :3] / np.maximum(np.linalg.norm(M[:3, :3], axis=0), 1e-12)
    L = LightState(el, kind, col, M[:3, 3].copy(), R[:, 0].copy(), R[:, 1].copy(), -R[:, 2].copy())
    m = UNITS_PER_METRE
    if kind == "rect-area":
        L.size = (ev.num(el, "width", c, m) / 2, ev.num(el, "height", c, m) / 2)
    elif kind in ("disk-area", "sphere-area"):
        r = ev.num(el, "radius", c, m / 2)
        L.size = (r, r)
    L.range = ev.num(el, "range", c, 0.0) if el.get("range") is not None else 0.0
    L.falloff = ev.num(el, "falloff", c, 2.0)
    outer = min(179.0, max(0.5, ev.num(el, "spotAngle", c, 45.0)))
    inner = min(outer, max(0.0, ev.num(el, "innerConeAngle", c, 0.0)))
    L.cos_outer, L.cos_inner = math.cos(math.radians(outer / 2)), math.cos(math.radians(inner / 2))
    L.shadow = ev.bool(el, "castShadow", c, False) and kind != "ambient"
    L.softness = ev.num(el, "shadowSoftness", c, 0.0)
    L.bias = ev.num(el, "shadowBias", c, 0.0005)
    L.map_size = int(min(8192, max(16, ev.num(el, "shadowMapSize", c, 2048))))
    L.diffuse = ev.bool(el, "affectsDiffuse", c, True)
    L.specular = ev.bool(el, "affectsSpecular", c, True)
    if el.get("ies") and kind != "ambient" and kind != "dome":
        L.ies = ies_table(rc, rc.doc.resolve_path(el.get("ies")))
    if kind == "dome":
        L.visible = ev.bool(el, "environmentVisible", c, False)
        L.env_rot = R
        if el.get("environment"):
            img = environment_image(rc, rc.doc.resolve_path(el.get("environment")))
            if img is not None:
                L.env = img
    L.key = (el.get("id"), kind, tuple(np.round(L.color, 6)), tuple(np.round(L.pos, 4)), tuple(np.round(L.fwd, 6)),
             L.size, L.shadow, L.softness, L.map_size)
    return L


def ies_table(rc, path: str) -> np.ndarray | None:
    key = ("ies", path)
    if key not in rc.cache:
        try:
            from .loaders import load_ies
            rc.cache[key] = load_ies(path).table(64, 64)
        except Exception as e:  # noqa: BLE001
            warn_once("light", path, f"IES profile not loaded: {e}")
            rc.cache[key] = None
    return rc.cache[key]


def environment_image(rc, path: str) -> np.ndarray | None:
    key = ("envimg", path)
    if key not in rc.cache:
        try:
            from .loaders import load_hdr_image
            rc.cache[key] = np.ascontiguousarray(load_hdr_image(path)[..., :3], np.float32)
        except Exception as e:  # noqa: BLE001
            warn_once("light", path, f"environment not loaded: {e}")
            rc.cache[key] = None
    return rc.cache[key]


# ------------------------------------------------------------------ environment maths
def equirect_dirs(w: int, h: int) -> np.ndarray:
    """Unit directions (h, w, 3) at equirect pixel centres (centre = -Z, u -> +X, v = 0 is +Y)."""
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    lon = (u - 0.5) * 2 * math.pi
    lat = (0.5 - v) * math.pi
    LON, LAT = np.meshgrid(lon, lat)
    return np.stack([np.sin(LON) * np.cos(LAT), np.sin(LAT), -np.cos(LON) * np.cos(LAT)], -1)


def dir_to_equirect(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    d = d / np.maximum(np.linalg.norm(d, axis=-1, keepdims=True), 1e-12)
    lon = np.arctan2(d[..., 0], -d[..., 2])
    lat = np.arcsin(np.clip(d[..., 1], -1, 1))
    return lon / (2 * math.pi) + 0.5, 0.5 - lat / math.pi


def world_environment(domes: list[LightState], w: int = 256, h: int = 128, specular: bool = True) -> np.ndarray:
    """Sum of the domes (affectsSpecular or affectsDiffuse selection) resampled into a world-aligned
    equirect of w x h, radiance x colour x intensity."""
    out = np.zeros((h, w, 3), np.float64)
    D = equirect_dirs(w, h)
    for L in domes:
        if (specular and not L.specular) or (not specular and not L.diffuse):
            continue
        if L.env is None:
            out += L.color
            continue
        local = D @ L.env_rot          # env-space directions: R^T d
        u, v = dir_to_equirect(local)
        out += _sample_equirect(L.env, u, v) * L.color
    return out.astype(np.float32)


def _sample_equirect(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Area-averaged (box-prefiltered) bilinear lookup, u wrapping."""
    H, W = img.shape[:2]
    oh, ow = u.shape
    # prefilter by block averaging down to ~2x the output size to avoid aliasing of small suns
    src = img.astype(np.float64)
    while src.shape[1] > 2 * ow and src.shape[0] > 2 * oh and src.shape[0] % 2 == 0 and src.shape[1] % 2 == 0:
        src = src.reshape(src.shape[0] // 2, 2, src.shape[1] // 2, 2, 3).mean((1, 3))
    H, W = src.shape[:2]
    x = u * W - 0.5
    y = np.clip(v * H - 0.5, 0, H - 1)
    x0 = np.floor(x).astype(int)
    y0 = np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    y1 = np.minimum(y0 + 1, H - 1)
    a = src[y0, np.mod(x0, W)]
    b = src[y0, np.mod(x0 + 1, W)]
    c = src[y1, np.mod(x0, W)]
    d = src[y1, np.mod(x0 + 1, W)]
    return a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy


def sh9_irradiance(env: np.ndarray) -> np.ndarray:
    """(9, 3) SH coefficients of the cosine-convolved irradiance E(n) of a world equirect radiance."""
    h, w = env.shape[:2]
    D = equirect_dirs(w, h)
    lat = (0.5 - (np.arange(h) + 0.5) / h) * math.pi
    dA = (2 * math.pi / w) * (math.pi / h) * np.cos(lat)[:, None]
    x, y, z = D[..., 0], D[..., 1], D[..., 2]
    Y = np.stack([np.full_like(x, 0.282095), 0.488603 * y, 0.488603 * z, 0.488603 * x, 1.092548 * x * y,
                  1.092548 * y * z, 0.315392 * (3 * z * z - 1), 1.092548 * x * z, 0.546274 * (x * x - y * y)], -1)
    L = np.einsum("hwk,hwc,hw->kc", Y, env.astype(np.float64), dA)
    A = np.array([math.pi, 2.094395, 2.094395, 2.094395, 0.785398, 0.785398, 0.785398, 0.785398, 0.785398])
    return (L * A[:, None]).astype(np.float32)


def dominant_direction(env: np.ndarray) -> tuple[np.ndarray, float]:
    """Luminance-weighted mean direction of a world equirect and its length (0 uniform .. 1 point)."""
    h, w = env.shape[:2]
    D = equirect_dirs(w, h)
    lat = (0.5 - (np.arange(h) + 0.5) / h) * math.pi
    wgt = (env @ np.array([0.2126, 0.7152, 0.0722])) * np.cos(lat)[:, None]
    s = float(wgt.sum())
    if s <= 1e-12:
        return np.array([0.0, 1.0, 0.0]), 0.0
    m = np.einsum("hwc,hw->c", D, wgt) / s
    n = float(np.linalg.norm(m))
    return (m / n if n > 1e-9 else np.array([0.0, 1.0, 0.0])), n
