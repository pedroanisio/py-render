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
    these parameters using resolved node-definition defaults. Image inputs keep independent
    dimensions, UV graphs, channels, samplers and declared color spaces. Other procedural graphs
    are baked with MaterialX's TextureBaker (GLSL, 1024 x
    1024 float textures in UV space, cached by document and dependency contents) and the baked images are used
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
from .texture_atlas import SCALAR_MAP_KEYS
from .material_atlas import EXTENSION_MAP_KEYS

log = logging.getLogger("scenerender")

FEATURES.declare("materials", FULL, "glTF metallic-roughness + clearcoat, transmission (screen-space refraction), "
                                     "ior, volume, sheen, specular, iridescence, anisotropy, dispersion, emissive "
                                     "strength, unlit, alpha modes, all texture maps, displacement, uvScale")
FEATURES.declare("material:materialX", FULL,
                 "standard_surface / open_pbr_surface / gltf_pbr / UsdPreviewSurface mapped onto the glTF model; "
                 "procedural node graphs baked to textures with the MaterialX TextureBaker")

COLOR_KEYS = ("baseColor", "emissive", "attenuationColor", "sheenColor", "specularColor")
NATIVE_MAP_KEYS = ("baseColorMap", "normalMap", "metallicRoughnessMap", "occlusionMap", "emissiveMap", "displacementMap")
MAP_KEYS = NATIVE_MAP_KEYS + ("roughnessMap", "metallicMap", "opacityMap") + SCALAR_MAP_KEYS + EXTENSION_MAP_KEYS
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
        return self.p["transmission"] > 1e-4 or self.usd_transparent

    @property
    def usd_transparent(self) -> bool:
        return (self.p.get('surfaceModel') == 'usdPreviewSurface' and
                self.p.get('opacityMode') == 'transparent' and self.blend)

    @property
    def opaque_occluder(self) -> bool:
        return not self.blend and not self.transmissive

    @property
    def casts_shadow(self) -> bool:
        if self.p.get('surfaceModel') == 'usdPreviewSurface' and self.blend and self.p['opacity'] < .5:
            return False
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
        if key not in material.maps:
            continue
        spec = material.p.get("mapUV", {}).get(key, {})
        fallback = spec.get('constant', spec.get('fallback'))
        uv = getattr(item, 'uv_sets', {}).get(spec.get("texCoord", 0), item.uvs if fallback is None else None)
        if 'constant' in spec or uv is None and fallback is not None:
            uv = np.broadcast_to(fallback, (len(item.positions), 2))
        if uv is None:
            continue
        if 'matrix' in spec:
            matrix = np.asarray(spec['matrix']).reshape(3, 3)
            out[key] = (uv @ matrix[:2, :2].T + matrix[:2, 2]).astype(np.float32)
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
    if ev.explicit(mat_el, 'alphaMode', ctx) or not mx:
        p["alphaMode"] = ev.str(mat_el, "alphaMode", ctx, p["alphaMode"]) or "opaque"
    if not ev.explicit(mat_el, "attenuationDistance", ctx):
        p["attenuationDistance"] = p.get("attenuationDistance", float("inf")) if mx else float("inf")
    for k in NATIVE_MAP_KEYS:
        src = ev.str(mat_el, k, ctx)
        if src:
            arr = texture(rc, src, srgb=k in ("baseColorMap", "emissiveMap"))
            if arr is not None:
                maps[k] = arr
                reset = {k, 'roughnessMap', 'metallicMap'} if k == 'metallicRoughnessMap' else {k}
                # Native texture attributes have native coordinates and samplers.
                for group in ('mapUV', 'mapSamplers'):
                    if group in p:
                        p[group] = {name: value for name, value in p[group].items() if name not in reset}
                if k == 'normalMap':
                    p.pop('normalSpace', None)
                    p.pop('normalMapScale', None)
                    p.pop('normalTangentGeometry', None)
                if k == 'metallicRoughnessMap':
                    maps.pop('roughnessMap', None)
                    maps.pop('metallicMap', None)
                    if p.get('surfaceModel') == 'usdPreviewSurface':
                        # USD's combined-MR sampler may hold IOR/coat pyramids.
                        # Native overrides still replace the two input maps.
                        maps.pop(k)
                        maps['roughnessMap'] = np.repeat(arr[..., 1:2], 4, -1)
                        maps['metallicMap'] = np.repeat(arr[..., 2:3], 4, -1)
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
        elif (k in p or k in ("mapUV", "mapSamplers", "occlusionStrength", "occlusion",
                             "surfaceModel", "useSpecularWorkflow", "opacityMode", "normalSpace", "normalMapScale",
                             "normalTangentGeometry", "clearcoatNormalScale", "iridescenceThicknessMinimum",
                             "iridescenceThicknessMaximum")) and v is not None:
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


def sample_map(arr: np.ndarray, uv: np.ndarray, sampler=None) -> np.ndarray:
    """Level-zero lookup with independently repeated, mirrored, clamped or border axes."""
    h, w = arr.shape[:2]
    sampler = sampler or {}
    wraps = [sampler.get(axis, 10497) for axis in ('wrapS', 'wrapT')]
    x, y = uv[:, 0]*w-.5, uv[:, 1]*h-.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    def at(ix, iy):
        valid = np.ones(len(ix), bool)
        coords = []
        for index, size, wrap in zip((ix, iy), (w, h), wraps):
            if wrap == 10497:
                index = np.mod(index, size)
            elif wrap == 33648:
                folded = np.mod(index, size*2)
                index = np.minimum(folded, 2*size-1-folded)
            else:
                if wrap == 33069:
                    valid &= (index >= 0) & (index < size)
                index = np.clip(index, 0, size-1)
            coords.append(index)
        border = np.asarray(sampler.get('border', (0.,)*arr.shape[-1]))[:arr.shape[-1]]
        return np.where(valid[:, None], arr[coords[1], coords[0]], border)
    if sampler.get('magFilter') == 9728:
        return at(np.floor(x+.5).astype(int), np.floor(y+.5).astype(int))
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
    "occlusion": ("occlusion", "float"), "alpha_mode": ("_alphaMode", "float"),
    "alpha_cutoff": ("alphaCutoff", "float"), "useSpecularWorkflow": ("useSpecularWorkflow", "float"),
    "specularColor": ("specularColor", "color"), "opacityMode": ("_opacityMode", "float"),
    "opacityThreshold": ("_opacityThreshold", "float"), "displacement": ("displacementScale", "float"),
}
_MX_TEX = {"baseColor": "baseColorMap", "emissive": "emissiveMap", "normal": "normalMap",
           "occlusion": "occlusionMap", "roughness": "roughnessMap", "metallic": "metallicMap",
           "opacity": "opacityMap", "displacementScale": "displacementMap"}


BAKE_SIZE = 1024


def _connected(inp):
    from .materialx_inputs import connected
    return connected(inp)


def bake_materialx(path: str) -> str | None:
    """Bake a MaterialX document's node graphs to textures; returns the baked .mtlx path (cached on
    disk). The TextureBaker opens its own (GLX) GL context, which may abort the process when no
    display is available, so it runs in a subprocess."""
    from .materialx_cache import bake
    return bake(path, BAKE_SIZE, _BAKE_SCRIPT)


_BAKE_SCRIPT = """
import os, sys
import MaterialX as mx
import MaterialX.PyMaterialXRender as mxr
import MaterialX.PyMaterialXRenderGlsl as mrg
from scenerender.three.materialx_cache import read_document
from scenerender.three.materialx_inputs import filename_path
src, out_dir, out, size = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
doc = read_document(src, set())
# Match the direct reader's source-relative image resolution in included files.
filenames = [(e, str(filename_path(e, os.path.dirname(src)))) for e in doc.traverseTree()
             if hasattr(e, 'getType') and e.getType() == 'filename' and e.getValueString()]
for e in doc.traverseTree():
    e.removeAttribute('fileprefix')
for e, filename in filenames:
    e.setValueString(filename)
# TextureBaker consumes connected outputs, while MaterialX also permits direct
# node connections. Expose those through outputs in their existing graph scope.
for shader in [e for e in doc.traverseTree() if isinstance(e, mx.Node) and e.getType() == 'surfaceshader']:
    for port in shader.getInputs():
        node = port.getConnectedNode()
        if node is not None and port.getConnectedOutput() is None:
            parent = node.getParent()
            output = parent.addOutput(parent.createValidChildName('bake_' + shader.getName() + '_' + port.getName()), port.getType())
            output.setConnectedNode(node)
            if port.hasOutputString():
                output.setOutputString(port.getOutputString())
            port.removeAttribute('nodename')
            port.setConnectedOutput(output)
valid, message = doc.validate()
if not valid:
    raise RuntimeError(message)
sp = mx.getDefaultDataSearchPath()
sp.append(mx.FilePath(os.path.dirname(src)))
baker = mrg.TextureBaker.create(size, size, mxr.BaseType.FLOAT)
baker.setOutputImagePath(mx.FilePath(out_dir))
baker.bakeAllMaterials(doc, sp, mx.FilePath(out))
# The baker reconstructs shader nodes without their definition/version selectors.
# Retain those selectors so unauthored inputs keep the same library defaults.
baked_doc = mx.createDocument()
mx.readFromXmlFile(baked_doc, out)
for node in baked_doc.getNodes():
    original = doc.getNode(node.getName())
    if original is not None and original.getCategory() == node.getCategory():
        for attribute in ('version', 'nodedef'):
            if original.hasAttribute(attribute):
                node.setAttribute(attribute, original.getAttribute(attribute))
mx.writeToXmlFile(baked_doc, out)
"""


def load_materialx(rc, path: str) -> MaterialSpec | None:
    from .materialx_cache import Entry, fingerprint, image_dependencies, read_document
    path = os.path.abspath(path)
    key = ("mtlx", path, os.environ.get('MATERIALX_SEARCH_PATH', ''))
    hit = rc.cache.get(key)
    if isinstance(hit, Entry) and hit.current():
        return hit.spec
    spec = None
    dependencies = set()
    try:
        import MaterialX as mx
        from .materialx_inputs import NeedsBake, library
        observed = {}
        doc = read_document(path, dependencies, observed)
        signature = tuple(sorted(observed.items()))
        shader = _first_shader(doc)
        if shader is None:
            rc.cache[key] = Entry(tuple(dependencies), signature, None)
            return None
        cacheable = True
        try:
            spec = _mx_shader_to_spec(shader, os.path.dirname(path))
        except NeedsBake:
            try:
                baked = bake_materialx(path)
            except Exception as e:  # noqa: BLE001 — no GL for the baker: constants and images only
                warn_once("materialX", path, f"procedural graph could not be baked ({e}); using input defaults")
                baked = None
                cacheable = False
            if baked:
                doc = mx.createDocument()
                mx.readFromXmlFile(doc, baked)
                doc.setDataLibrary(library())
                image_dependencies(doc, os.path.dirname(baked), dependencies)
                dependencies.add(baked)
                shader = _first_shader(doc)
                path = baked
            if shader is not None:
                spec = _mx_shader_to_spec(shader, os.path.dirname(path), fallback=True)
        if cacheable:
            final = fingerprint(dependencies)
            if all(dict(final).get(p) == digest for p, digest in signature):
                rc.cache[key] = Entry(tuple(dependencies), final, spec)
    except Exception as e:  # noqa: BLE001
        warn_once("materialX", path, f"could not read MaterialX document: {e}")
    return spec


def _first_shader(doc):
    mats = doc.getMaterialNodes()
    if mats:
        inp = mats[0].getInput("surfaceshader")
        if inp is not None and _connected(inp) is not None:
            return _connected(inp)
    nodes = [n for n in doc.getNodes() if n.getType() == "surfaceshader"]
    return nodes[0] if nodes else None


def _mx_shader_to_spec(shader, base_dir: str, fallback=False) -> MaterialSpec:
    from .materialx_inputs import Inputs, ImageValue, NeedsBake, input_port
    reader = Inputs(base_dir)
    definition = shader.getNodeDef()
    names = dict.fromkeys([i.getName() for i in definition.getActiveInputs()] if definition else [])
    names.update(dict.fromkeys(i.getName() for i in shader.getInputs()))
    usd = shader.getCategory() == 'UsdPreviewSurface'
    params: dict = {'mapUV': {}, 'mapSamplers': {}}
    textures: dict = {}
    for name in names:
        inp = input_port(shader, name)
        target = _MX_MAP.get(name)
        if name in ("normal", "geometry_normal"):
            target = ("normal", "vector")
        if target is None:
            continue
        pname, kind = target
        try:
            val = reader.signal(inp)
        except NeedsBake as exc:
            if not fallback:
                raise
            warn_once('materialX', shader.getNamePath()+'/'+name, f'{exc}; using the node definition default')
            val = reader.signal(definition.getActiveInput(name)) if definition else None
        if val is None:
            continue
        if pname == 'opacity':
            # Standard Surface's implementation uses luminance for color opacity.
            # All color inputs have already been converted to linear Rec.709.
            luma = lambda a: np.sum(a[..., :3]*[.2126, .7152, .0722], -1, keepdims=True)
            if isinstance(val, ImageValue) and val.pixels.shape[-1] >= 3:
                val = val.channels(luma)
            elif np.asarray(val).ndim and not isinstance(val, ImageValue):
                val = float(luma(np.asarray(val))[0])
        if isinstance(val, ImageValue):
            key = _MX_TEX.get(pname)
            if usd and pname in ('ior', 'clearcoat', 'clearcoatRoughness', 'specularColor'):
                key = pname+'Map'
            if key is None:
                warn_once('materialX', shader.getNamePath()+'/'+name, 'texture input has no renderer mapping')
                continue
            if pname == 'normal':
                params['normalSpace'] = 'tangent' if usd or val.normal_scale is not None else 'world'
                if val.normal_scale is not None:
                    params['normalMapScale'] = val.normal_scale
                else:
                    val = val.channels(lambda a: a*.5+.5)
            def rgba(a):
                if kind == 'float':
                    return np.repeat(a[..., :1], 4, -1)
                return np.concatenate((a[..., :3], np.ones((*a.shape[:-1], 1))), -1)
            val = val.channels(rgba)
            textures[key] = val.pixels
            params['mapUV'][key], params['mapSamplers'][key] = val.uv, val.sampler
            if pname != 'normal':
                params[pname] = (1., 1., 1., 1.) if kind == 'color' else 1.
            continue
        if kind == "color":
            v = tuple(float(x) for x in (val.asTuple() if hasattr(val, "asTuple") else val))
            params[pname] = v[:3] + (1.0,)  # Declared color spaces were converted to linear Rec.709.
        elif kind == "float":
            params[pname] = float(val)
        elif pname == 'normal':
            params['normalSpace'] = 'tangent' if usd else 'world'
            textures['normalMap'] = np.r_[np.asarray(val)*.5+.5, 1.].astype(np.float32)[None, None]
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
    if shader.getCategory() == 'gltf_pbr':
        params['alphaMode'] = {0: 'opaque', 1: 'mask', 2: 'blend'}.get(int(params.pop('_alphaMode', 0)), 'opaque')
    else:
        params['alphaMode'] = 'blend' if params.get('opacity', 1.) < 1. or 'opacityMap' in textures else 'opaque'
    if usd:
        params['surfaceModel'] = 'usdPreviewSurface'
        params['normalTangentGeometry'] = True  # The MaterialX definition uses the default geometry frame.
        params['opacityMode'] = 'presence' if params.pop('_opacityMode', 0) == 1 else 'transparent'
        threshold = params.pop('_opacityThreshold', 0.)
        if threshold > 0:
            params['alphaMode'], params['alphaCutoff'] = 'mask', threshold
        inactive = 'metallicMap' if params.get('useSpecularWorkflow') else 'specularColorMap'
        textures.pop(inactive, None)
        params['mapUV'].pop(inactive, None)
        params['mapSamplers'].pop(inactive, None)
        if 'displacementMap' not in textures and params.get('displacementScale', 0.):
            textures['displacementMap'] = np.ones((1, 1, 4), np.float32)
    return MaterialSpec(params, textures, shader.getName())


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
