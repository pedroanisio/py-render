"""<material> evaluation: glTF 2.0 metallic-roughness + KHR_materials_* parameters for the renderer.

Pinned decisions (the schema names the parameters; semantics follow glTF 2.0 / KHR extensions):
  * Colours are authored sRGB and converted to linear; baseColor alpha x @opacity is the surface
    alpha. alphaMode opaque ignores alpha, mask discards below alphaCutoff (then opaque), blend
    composites with premultiplied alpha (depth-sorted per object, back faces first when doubleSided).
  * specular/specularColor follow KHR_materials_specular (F0 = ((ior-1)/(ior+1))^2 * specularColor,
    scaled by specular; F90 = specular); ior follows KHR_materials_ior.
  * thickness and attenuationDistance are in metres (camera.UNITS_PER_METRE scene units per metre)
    in the object's local space (scaled by the object's scale), per KHR_materials_volume.
  * iridescence uses KHR_materials_iridescence with a fixed film thickness of 400 nm (the schema has
    no thickness attribute; 400 nm is the extension's default maximum).
  * anisotropyRotation is in degrees (counter-clockwise from the tangent); a negative anisotropy
    stretches highlights along the bitangent.
  * dispersion follows KHR_materials_dispersion: per-channel IORs ior -+ (ior-1) * 0.025 * dispersion.
  * Texture maps: baseColorMap/emissiveMap are sRGB, the others linear; metallicRoughnessMap uses G
    (roughness) and B (metal); occlusionMap R scales indirect light; normalMap is a tangent-space
    OpenGL (+Y up) map scaled by normalScale in XY; displacementMap R moves vertices along the normal
    by displacementScale * value (scene units) before shading; uvScaleX/Y multiply all UVs.
  * materialX: the referenced .mtlx document's first surfacematerial is read with the MaterialX
    library; standard_surface, open_pbr_surface, gltf_pbr and UsdPreviewSurface inputs are mapped onto
    these parameters (constants, image/tiledimage file textures). When an input is driven by a
    procedural node graph the document is first baked with MaterialX's TextureBaker (GLSL, 1024 x
    1024 float textures in UV space, cached on disk per file and mtime) and the baked images are used
    as the corresponding maps. Attributes set on the <material> element override the MaterialX values.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import numpy as np

from ..document import ln
from ..raster import srgb_to_linear
from ..registry import FEATURES, FULL, warn_once
from .model import MaterialSpec

log = logging.getLogger("scenerender")

FEATURES.declare("materials", FULL, "glTF metallic-roughness + clearcoat, transmission (screen-space refraction), "
                                     "ior, volume, sheen, specular, iridescence, anisotropy, dispersion, emissive "
                                     "strength, unlit, alpha modes, all texture maps, displacement, uvScale")
FEATURES.declare("material:materialX", FULL,
                 "standard_surface / open_pbr_surface / gltf_pbr / UsdPreviewSurface mapped onto the glTF model; "
                 "procedural node graphs baked to textures with the MaterialX TextureBaker")

COLOR_KEYS = ("baseColor", "emissive", "attenuationColor", "sheenColor", "specularColor")
MAP_KEYS = ("baseColorMap", "normalMap", "metallicRoughnessMap", "occlusionMap", "emissiveMap", "displacementMap")
DEFAULTS = dict(baseColor=(1.0, 1.0, 1.0, 1.0), metallic=0.0, roughness=0.5, emissive=(0.0, 0.0, 0.0, 1.0),
                emissiveStrength=1.0, opacity=1.0, alphaMode="opaque", alphaCutoff=0.5, doubleSided=False,
                unlit=False, clearcoat=0.0, clearcoatRoughness=0.0, transmission=0.0, ior=1.5, thickness=0.0,
                attenuationColor=(1.0, 1.0, 1.0, 1.0), attenuationDistance=float("inf"),
                sheenColor=(0.0, 0.0, 0.0, 1.0), sheenRoughness=0.0, specular=1.0,
                specularColor=(1.0, 1.0, 1.0, 1.0), iridescence=0.0, iridescenceIor=1.3, anisotropy=0.0,
                anisotropyRotation=0.0, dispersion=0.0, normalScale=1.0, displacementScale=0.0,
                uvScaleX=1.0, uvScaleY=1.0)
NUM_KEYS = [k for k, v in DEFAULTS.items() if isinstance(v, float) and k not in COLOR_KEYS]
BOOL_KEYS = ("doubleSided", "unlit")


@dataclass
class Material:
    """Evaluated material: linear parameters + decoded texture arrays (keys of MAP_KEYS)."""
    p: dict
    maps: dict = field(default_factory=dict)
    key: tuple = ()

    @property
    def blend(self) -> bool:
        return self.p["alphaMode"] == "blend"

    @property
    def transmissive(self) -> bool:
        return self.p["transmission"] > 1e-4

    @property
    def opaque_occluder(self) -> bool:
        return not self.blend and not self.transmissive

    @property
    def casts_shadow(self) -> bool:
        return self.p["transmission"] < 0.5 and not (self.blend and self.p["baseColor"][3] < 0.5)


def _lin(c) -> tuple:
    rgb = srgb_to_linear(np.array(c[:3], np.float32))
    return tuple(float(v) for v in rgb) + (float(c[3]) if len(c) > 3 else 1.0,)


def default_material() -> Material:
    return Material(dict(DEFAULTS, baseColor=_lin((0.8, 0.8, 0.82, 1.0))), {}, ("default",))


def map_uvs(item, material: Material) -> dict:
    """Resolve each map's UV set and KHR texture transform before interpolation."""
    out = {}
    for key in MAP_KEYS:
        spec = material.p.get("mapUV", {}).get(key, {})
        uv = item.uv_sets.get(spec.get("texCoord", 0), item.uvs)
        if uv is None:
            continue
        angle = spec.get("rotation", 0.)
        c, s = np.cos(angle), np.sin(angle)
        uv = np.asarray(uv) * spec.get("scale", (1., 1.))
        out[key] = (uv @ np.array([[c, s], [-s, c]]) + spec.get("offset", (0., 0.))).astype(np.float32)
    return out


def evaluate(rc, mat_el, ctx) -> Material:
    """A <material> element evaluated at ctx (all attributes animate)."""
    ev = rc.ev
    p = dict(DEFAULTS)
    mx = ev.str(mat_el, "materialX", ctx)
    if mx:
        spec = load_materialx(rc, rc.doc.resolve_path(mx))
        if spec is not None:
            base = from_spec(spec)
            p.update(base.p)
            maps = dict(base.maps)
        else:
            maps = {}
    else:
        maps = {}
    for k in NUM_KEYS:
        if ev.explicit(mat_el, k, ctx) or not mx:
            p[k] = ev.num(mat_el, k, ctx, p[k])
    for k in COLOR_KEYS:
        if ev.explicit(mat_el, k, ctx) or not mx:
            d = DEFAULTS[k]
            p[k] = _lin(ev.color(mat_el, k, ctx, d))
    for k in BOOL_KEYS:
        if ev.explicit(mat_el, k, ctx) or not mx:
            p[k] = ev.bool(mat_el, k, ctx, DEFAULTS[k])
    p["alphaMode"] = ev.str(mat_el, "alphaMode", ctx, p["alphaMode"]) or "opaque"
    if not ev.explicit(mat_el, "attenuationDistance", ctx):
        p["attenuationDistance"] = p.get("attenuationDistance", float("inf")) if mx else float("inf")
    for k in MAP_KEYS:
        src = ev.str(mat_el, k, ctx)
        if src:
            arr = texture(rc, src, srgb=k in ("baseColorMap", "emissiveMap"))
            if arr is not None:
                maps[k] = arr
    return Material(p, maps, (mat_el.get("id"),) + tuple(sorted((k, str(v)) for k, v in p.items())))


def _animated(el, prop: str) -> bool:
    return any(isinstance(a.tag, str) and a.get("property") == prop for a in el)


def from_spec(spec: MaterialSpec | None) -> Material:
    """A material embedded in a model file (glTF/USD/FBX/OBJ) -> Material with defaults filled."""
    if spec is None:
        return default_material()
    p = dict(DEFAULTS)
    for k, v in spec.params.items():
        if k in COLOR_KEYS and v is not None:
            v = tuple(float(x) for x in v)
            p[k] = v + (1.0,) if len(v) == 3 else v
        elif (k in p or k in ("mapUV", "mapSamplers", "occlusionStrength")) and v is not None:
            p[k] = v
    return Material(p, {k: v for k, v in spec.textures.items() if v is not None}, ("spec", id(spec)))


def texture(rc, src: str, srgb: bool) -> np.ndarray | None:
    path = rc.doc.resolve_path(src)
    key = ("tex3d", path, srgb)
    hit = rc.cache.get(key)
    if hit is not None:
        return hit if hit is not False else None
    try:
        from .loaders import decode_image
        arr = decode_image(path, srgb)
    except Exception as e:  # noqa: BLE001 — a missing map degrades to the constant parameter
        warn_once("material", src, f"texture not loaded: {e}")
        rc.cache[key] = False
        return None
    rc.cache[key] = arr
    return arr


def sample_map(arr: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Bilinear, repeat-wrapped lookup of a texture at glTF UVs (n, 2) -> (n, C)."""
    h, w = arr.shape[:2]
    x = np.mod(uv[:, 0], 1.0) * w - 0.5
    y = np.mod(uv[:, 1], 1.0) * h - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    def at(ix, iy):
        return arr[np.mod(iy, h), np.mod(ix, w)]
    return (at(x0, y0) * (1 - fx) * (1 - fy) + at(x0 + 1, y0) * fx * (1 - fy) + at(x0, y0 + 1) * (1 - fx) * fy
            + at(x0 + 1, y0 + 1) * fx * fy)


# ------------------------------------------------------------------ MaterialX
_MX_MAP = {
    # standard_surface / open_pbr_surface / gltf_pbr / UsdPreviewSurface input -> (param, kind)
    "base_color": ("baseColor", "color"), "base_color_": ("baseColor", "color"), "diffuseColor": ("baseColor", "color"),
    "metalness": ("metallic", "float"), "base_metalness": ("metallic", "float"), "metallic": ("metallic", "float"),
    "specular_roughness": ("roughness", "float"), "roughness": ("roughness", "float"),
    "specular": ("specular", "float"), "specular_weight": ("specular", "float"),
    "specular_color": ("specularColor", "color"), "specular_IOR": ("ior", "float"), "specular_ior": ("ior", "float"),
    "ior": ("ior", "float"), "coat": ("clearcoat", "float"), "coat_weight": ("clearcoat", "float"),
    "clearcoat": ("clearcoat", "float"), "coat_roughness": ("clearcoatRoughness", "float"),
    "clearcoatRoughness": ("clearcoatRoughness", "float"), "clearcoat_roughness": ("clearcoatRoughness", "float"),
    "transmission": ("transmission", "float"), "transmission_weight": ("transmission", "float"),
    "transmission_color": ("attenuationColor", "color"), "attenuation_color": ("attenuationColor", "color"),
    "attenuation_distance": ("attenuationDistance", "float"), "thickness": ("thickness", "float"),
    "sheen": ("_sheenWeight", "float"), "sheen_weight": ("_sheenWeight", "float"),
    "fuzz_weight": ("_sheenWeight", "float"), "sheen_color": ("sheenColor", "color"), "fuzz_color": ("sheenColor", "color"),
    "sheen_roughness": ("sheenRoughness", "float"), "fuzz_roughness": ("sheenRoughness", "float"),
    "thin_film_thickness": ("_filmThickness", "float"), "thin_film_weight": ("iridescence", "float"),
    "thin_film_IOR": ("iridescenceIor", "float"), "thin_film_ior": ("iridescenceIor", "float"),
    "iridescence": ("iridescence", "float"), "iridescence_ior": ("iridescenceIor", "float"),
    "specular_anisotropy": ("anisotropy", "float"), "anisotropy_strength": ("anisotropy", "float"),
    "specular_rotation": ("_anisoRot01", "float"), "anisotropy_rotation": ("anisotropyRotation", "float"),
    "emission": ("_emissionWeight", "float"), "emission_luminance": ("_emissionWeight", "float"),
    "emission_color": ("emissive", "color"), "emissive": ("emissive", "color"), "emissiveColor": ("emissive", "color"),
    "emissive_strength": ("emissiveStrength", "float"), "opacity": ("opacity", "float"),
    "alpha": ("opacity", "float"), "geometry_opacity": ("opacity", "float"), "ior_": ("ior", "float"),
    "dispersion": ("dispersion", "float"), "transmission_dispersion_scale": ("dispersion", "float"),
}
_MX_TEX = {"baseColor": "baseColorMap", "emissive": "emissiveMap", "normal": "normalMap",
           "occlusion": "occlusionMap", "roughness": "_roughMap", "metallic": "_metalMap"}


BAKE_SIZE = 1024


def _connected(inp):
    """The node driving a shader input, following nodegraph outputs."""
    node = inp.getConnectedNode()
    if node is None and inp.getNodeGraphString():
        doc = inp.getDocument()
        ng = doc.getNodeGraph(inp.getNodeGraphString())
        if ng is not None:
            out = ng.getOutput(inp.getOutputString()) if inp.getOutputString() else (ng.getOutputs() or [None])[0]
            node = out.getConnectedNode() if out is not None else None
    return node


def _procedural(shader) -> bool:
    for inp in shader.getInputs():
        n = _connected(inp)
        if n is not None and _mx_file_of(n) is None:
            return True
    return False


def bake_materialx(path: str) -> str | None:
    """Bake a MaterialX document's node graphs to textures; returns the baked .mtlx path (cached on
    disk). The TextureBaker opens its own (GLX) GL context, which may abort the process when no
    display is available, so it runs in a subprocess."""
    import hashlib
    import subprocess
    import sys
    import tempfile
    st = os.stat(path)
    h = hashlib.sha1(f"{os.path.abspath(path)}:{st.st_mtime_ns}:{BAKE_SIZE}".encode()).hexdigest()[:16]
    out_dir = os.path.join(tempfile.gettempdir(), "scenerender-mtlx", h)
    out = os.path.join(out_dir, "baked.mtlx")
    if os.path.exists(out):
        return out
    os.makedirs(out_dir, exist_ok=True)
    res = subprocess.run([sys.executable, "-c", _BAKE_SCRIPT, os.path.abspath(path), out_dir, out, str(BAKE_SIZE)],
                         capture_output=True, text=True, timeout=600)
    if res.returncode != 0 or not os.path.exists(out):
        raise RuntimeError((res.stderr or res.stdout).strip().splitlines()[-1:] or "baker failed")
    return out


_BAKE_SCRIPT = """
import os, sys
import MaterialX as mx
import MaterialX.PyMaterialXRender as mxr
import MaterialX.PyMaterialXRenderGlsl as mrg
src, out_dir, out, size = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
doc = mx.createDocument()
mx.readFromXmlFile(doc, src)
lib = mx.createDocument()
sp = mx.getDefaultDataSearchPath()
sp.append(mx.FilePath(os.path.dirname(src)))
mx.loadLibraries(mx.getDefaultDataLibraryFolders(), sp, lib)
doc.setDataLibrary(lib)
baker = mrg.TextureBaker.create(size, size, mxr.BaseType.FLOAT)
baker.setOutputImagePath(mx.FilePath(out_dir))
baker.bakeAllMaterials(doc, sp, mx.FilePath(out))
"""


def load_materialx(rc, path: str) -> MaterialSpec | None:
    key = ("mtlx", path)
    if key in rc.cache:
        return rc.cache[key]
    spec = None
    try:
        import MaterialX as mx
        doc = mx.createDocument()
        mx.readFromXmlFile(doc, path)
        shader0 = _first_shader(doc)
        if shader0 is not None and _procedural(shader0):
            try:
                baked = bake_materialx(path)
            except Exception as e:  # noqa: BLE001 — no GL for the baker: constants and images only
                warn_once("materialX", path, f"procedural graph could not be baked ({e}); using input defaults")
                baked = None
            if baked:
                doc = mx.createDocument()
                mx.readFromXmlFile(doc, baked)
        mats = doc.getMaterialNodes()
        shader = None
        if mats:
            inp = mats[0].getInput("surfaceshader")
            shader = inp.getConnectedNode() if inp is not None else None
        if shader is None:
            nodes = [n for n in doc.getNodes() if n.getType() == "surfaceshader"]
            shader = nodes[0] if nodes else None
        if shader is not None:
            spec = _mx_shader_to_spec(shader, os.path.dirname(path))
    except Exception as e:  # noqa: BLE001
        warn_once("materialX", path, f"could not read MaterialX document: {e}")
    rc.cache[key] = spec
    return spec


def _first_shader(doc):
    mats = doc.getMaterialNodes()
    if mats:
        inp = mats[0].getInput("surfaceshader")
        if inp is not None and inp.getConnectedNode() is not None:
            return inp.getConnectedNode()
    nodes = [n for n in doc.getNodes() if n.getType() == "surfaceshader"]
    return nodes[0] if nodes else None


def _mx_shader_to_spec(shader, base_dir: str) -> MaterialSpec:
    params: dict = {}
    textures: dict = {}
    for inp in shader.getInputs():
        name = inp.getName()
        target = _MX_MAP.get(name)
        if name == "normal":
            target = ("normal", "vector")
        if target is None:
            continue
        pname, kind = target
        node = _connected(inp)
        if node is not None:
            f = _mx_file_of(node)
            if f:
                tk = _MX_TEX.get(pname)
                if tk:
                    path = f if os.path.isabs(f) else os.path.join(base_dir, f)
                    textures[tk] = path
                continue
            warn_once("materialX", node.getName(), "procedural node graph input not evaluated; using its default")
            continue
        val = inp.getValue()
        if val is None:
            continue
        if kind == "color":
            v = tuple(float(x) for x in (val.asTuple() if hasattr(val, "asTuple") else val))
            params[pname] = v[:3] + (1.0,)       # MaterialX colours are linear (lin_rec709) by default
        elif kind == "float":
            params[pname] = float(val)
    w = params.pop("_emissionWeight", None)
    if w is not None:
        params["emissiveStrength"] = w
    sw = params.pop("_sheenWeight", None)
    if sw is not None and "sheenColor" in params:
        params["sheenColor"] = tuple(c * sw for c in params["sheenColor"][:3]) + (1.0,)
    r01 = params.pop("_anisoRot01", None)
    if r01 is not None:
        params["anisotropyRotation"] = r01 * 360.0
    params.pop("_filmThickness", None)
    from .loaders import decode_image
    out_tex = {}
    rough = textures.pop("_roughMap", None)
    metal = textures.pop("_metalMap", None)
    for k, pth in textures.items():
        try:
            out_tex[k] = decode_image(pth, k in ("baseColorMap", "emissiveMap"))
        except Exception as e:  # noqa: BLE001
            warn_once("materialX", pth, f"texture not loaded: {e}")
    if rough or metal:
        try:
            r = decode_image(rough, False) if rough else None
            m = decode_image(metal, False) if metal else None
            ref = r if r is not None else m
            mr = np.ones_like(ref)
            if r is not None:
                mr[..., 1] = r[..., 0]
            if m is not None:
                mr[..., 2] = m[..., 0]
            if r is not None:
                params["roughness"] = 1.0
            if m is not None:
                params["metallic"] = 1.0
            out_tex["metallicRoughnessMap"] = mr
        except Exception as e:  # noqa: BLE001
            warn_once("materialX", str(rough or metal), f"texture not loaded: {e}")
    return MaterialSpec(params, out_tex, shader.getName())


def _mx_file_of(node) -> str | None:
    """The file of an image/tiledimage node (following one level of normalmap/convert wrappers)."""
    cat = node.getCategory()
    if cat in ("image", "tiledimage", "gltf_image", "UsdUVTexture"):
        f = node.getInput("file")
        if f is not None:
            return str(f.getValueString())
    if cat in ("normalmap", "convert", "extract", "swizzle", "gltf_normalmap"):
        for i in node.getInputs():
            n = i.getConnectedNode()
            if n is not None:
                return _mx_file_of(n)
    return None


def material_for(rc, el, ctx) -> Material | None:
    """object3D @material evaluated, or None when the object has none (mesh materials apply)."""
    mid = rc.ev.str(el, "material", ctx)
    if not mid:
        return None
    m = rc.doc.ids.get(mid)
    if m is None or ln(m) != "material":
        warn_once("object3D", mid, "material id not found")
        return None
    return evaluate(rc, m, ctx)
