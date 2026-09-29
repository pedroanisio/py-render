"""The moderngl 3D renderer: primitives, PBR materials, lights, shadows, meshes, instancing, DoF, AA."""
from __future__ import annotations

import math
import os
import textwrap

import numpy as np
import pytest

from scenerender import gl
from scenerender.render import Renderer

pytestmark = pytest.mark.skipif(not gl.available(), reason="no GL")
MEDIA = os.path.join(os.path.dirname(__file__), "fixtures", "media", "3d")


def doc(tmp_path, body: str, *, lights: str = "", materials: str = "", w=160, h=90, project="", assets="",
        name="s.xml", extra="") -> Renderer:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="{w}" height="{h}" fps="10" duration="4" background="#000000FF" {project}/>
  {f'<assets>{assets}</assets>' if assets else ''}
  {f'<materials>{materials}</materials>' if materials else ''}
  {extra}
  <composition>
{textwrap.indent(body, '    ')}
  </composition>
  {f'<lights>{lights}</lights>' if lights else ''}
</scene>"""
    p = tmp_path / name
    p.write_text(xml)
    return Renderer.open(str(p))


def frame(r: Renderer, t: float = 0.0) -> np.ndarray:
    """Premultiplied linear frame (h, w, 4)."""
    return r.rc.render_frame(t).px


def centroid(a: np.ndarray) -> np.ndarray:
    """Coverage centroid in continuous frame coordinates (pixel centres at +0.5)."""
    ys, xs = np.mgrid[0:a.shape[0], 0:a.shape[1]] + 0.5
    s = a.sum()
    return np.array([(xs * a).sum() / s, (ys * a).sum() / s])


KEY = '<light id="k" type="directional" pitch="-40" yaw="-30" intensity="3"/>'


@pytest.mark.parametrize("wrap_s,wrap_t", [(10497, 10497), (33071, 10497), (10497, 33071),
                                          (33648, 33071), (33071, 33648), (33648, 33648)])
def test_gltf_texture_wrap_modes_render(tmp_path, wrap_s, wrap_t):
    from test_3d_loaders import Builder
    from scenerender.raster import srgb_to_linear
    import pygltflib as gt
    b = Builder()
    pos = b.add([[-40, 30, 0], [40, 30, 0], [40, -30, 0], [-40, -30, 0]], "VEC3")
    uv = b.add([[-.5, -.5], [2.5, -.5], [2.5, 2.5], [-.5, 2.5]], "VEC2")
    idx = b.add([0, 2, 1, 0, 3, 2], "SCALAR", gt.UNSIGNED_SHORT)
    img = np.array([[[255, 0, 0, 255], [0, 255, 0, 255]], [[0, 0, 255, 255], [255, 255, 255, 255]]], np.uint8)
    tex = b.image_png(img)
    b.g.samplers = [gt.Sampler(wrapS=wrap_s, wrapT=wrap_t, minFilter=9728, magFilter=9728)]
    b.g.textures[tex].sampler = 0
    b.g.materials = [gt.Material(pbrMetallicRoughness=gt.PbrMetallicRoughness(baseColorTexture=gt.TextureInfo(index=tex)),
                                extensions={"KHR_materials_unlit": {}})]
    b.g.meshes = [gt.Mesh(primitives=[gt.Primitive(indices=idx, material=0,
                                                  attributes=gt.Attributes(POSITION=pos, TEXCOORD_0=uv))])]
    b.g.nodes, b.g.scenes = [gt.Node(mesh=0)], [gt.Scene(nodes=[0])]
    path = b.save(tmp_path / "sampler.gltf")
    actual = frame(doc(tmp_path, '<object3D x="80" y="60" scaleX="0.01" scaleY="0.01" scaleZ="0.01" id="o" primitive="mesh" mesh="m"/>', h=120,
                       assets=f'<mesh id="m" src="{path}" format="gltf"/>'))
    # Interior points away from texel boundaries: expected texel indices follow
    # the authored repeat/clamp/reflection modes independently on each axis.
    def index(u, wrap):
        u = 1 - abs(u % 2 - 1) if wrap == 33648 else (u % 1 if wrap == 10497 else np.clip(u, 0, 1))
        return min(1, int(u * 2))
    for y in (34, 44, 54, 64, 74, 84):
        for x in (45, 59, 71, 85, 99, 111):
            u, v = (x + .5 - 40) / 80 * 3 - .5, (y + .5 - 30) / 60 * 3 - .5
            expected = srgb_to_linear(img[index(v, wrap_t), index(u, wrap_s), :3] / 255.)
            np.testing.assert_allclose(actual[y, x, :3], expected, atol=1e-5)


@pytest.mark.parametrize("minimum,expected", [(9728, [0, 1, 0]), (9729, [.3, .7, 0]),
    (9984, [.5, .5, 0]), (9985, [.5, .5, .1]), (9986, [.5, .5, .15]), (9987, [.5, .5, .22])])
def test_material_minification_filters_on_gpu(minimum, expected):
    import moderngl
    from scenerender.three.materials import Material
    from scenerender.three.renderer import _material_texture
    ctx = gl.context()
    pixels = np.tile(np.array([[[1, 0, 0, 1], [0, 1, 0, 1], [0, 0, 1, 1], [1, 1, 1, 1]]], np.float32), (4, 1, 1))
    material = Material({"mapSamplers": {"baseColorMap": {"minFilter": minimum, "magFilter": 9728}}},
                        {"baseColorMap": pixels})
    texture = _material_texture(ctx, material, "baseColorMap", {})
    prog = ctx.program(vertex_shader=gl.FULLSCREEN_VS, fragment_shader='''#version 330
        uniform sampler2D image; uniform float gradient; out vec4 color;
            void main() { color = textureLod(image, vec2(.3, .5), gradient); }''')
    quad, fbo = gl.fullscreen_quad(prog), gl.framebuffer(1, 1)
    try:
        fbo.use()
        ctx.disable(moderngl.BLEND | moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        texture.use(0)
        prog["image"].value = 0
        prog["gradient"].value = 1.3
        quad.render(moderngl.TRIANGLE_STRIP)
        # Explicit LOD isolates filter behavior from hardware derivative approximations.
        np.testing.assert_allclose(gl.read_rgba(fbo, 1, 1)[0, 0, :3], expected, atol=.01)
    finally:
        texture.release()
        quad.release()
        prog.release()
        for attachment in fbo.color_attachments:
            attachment.release()
        fbo.release()


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_gltf_point_and_line_modes_render(tmp_path, mode):
    from test_3d_loaders import Builder
    import pygltflib as gt
    b = Builder()
    pos = b.add([[-30, -20, 0], [30, -20, 0], [30, 20, 0], [-30, 20, 0]], "VEC3")
    col = b.add([[1, 0, 0, 1]] * 4, "VEC4")
    b.g.meshes = [gt.Mesh(primitives=[gt.Primitive(mode=mode, attributes=gt.Attributes(POSITION=pos, COLOR_0=col))])]
    b.g.nodes, b.g.scenes = [gt.Node(mesh=0)], [gt.Scene(nodes=[0])]
    path = b.save(tmp_path / "lines.gltf")
    r = doc(tmp_path, '<object3D x="80" y="45" scaleX="0.01" scaleY="0.01" scaleZ="0.01" id="o" primitive="mesh" mesh="mesh"/>',
            assets=f'<mesh id="mesh" src="{path}" format="gltf"/>')
    px = frame(r)
    red = (px[..., 0] > .1) & (px[..., 1] < .01)
    assert red.sum() >= (4 if mode == 0 else 80)
    # LINE_LOOP alone closes the left edge; LINE_STRIP includes the right edge.
    if mode > 0:
        assert bool(red[45, 50]) == (mode == 2)
        assert bool(red[45, 109]) == (mode in (2, 3))


@pytest.mark.parametrize("slot", ["baseColorMap", "emissiveMap", "metallicRoughnessMap", "occlusionMap", "normalMap"])
def test_gltf_maps_use_their_own_uv_set_and_transform(tmp_path, slot):
    from test_3d_loaders import Builder
    import pygltflib as gt
    b = Builder()
    pos = b.add([[-40, -30, 0], [40, -30, 0], [40, 30, 0], [-40, 30, 0]], "VEC3")
    uv0 = b.add([[.125, .5]] * 4, "VEC2")
    uv1 = b.add([[.375, .5]] * 4, "VEC2")
    idx = b.add([0, 1, 2, 0, 2, 3], "SCALAR", gt.UNSIGNED_SHORT)
    # Left half and right half differ strongly in every material channel.
    img = np.zeros((8, 8, 4), np.uint8)
    img[:, :4] = [255, 20, 20, 255]
    img[:, 4:] = [20, 255, 255, 255]
    tex = b.image_png(img)
    props = dict(index=tex, texCoord=1, extensions={"KHR_texture_transform": {"offset": [.25, 0]}})
    pbr = gt.PbrMetallicRoughness(baseColorFactor=[1, 1, 1, 1], metallicFactor=1, roughnessFactor=1)
    mat = gt.Material(pbrMetallicRoughness=pbr)
    if slot == "baseColorMap":
        pbr.baseColorTexture = gt.TextureInfo(**props)
    elif slot == "emissiveMap":
        mat.emissiveTexture, mat.emissiveFactor = gt.TextureInfo(**props), [1, 1, 1]
    elif slot == "normalMap":
        mat.normalTexture = gt.NormalMaterialTexture(**props)
    elif slot == "metallicRoughnessMap":
        pbr.metallicRoughnessTexture = gt.TextureInfo(**props)
    else:
        mat.occlusionTexture = gt.OcclusionTextureInfo(**props)
    b.g.materials = [mat]
    b.g.meshes = [gt.Mesh(primitives=[gt.Primitive(indices=idx, material=0,
        attributes=gt.Attributes(POSITION=pos, TEXCOORD_0=uv0, TEXCOORD_1=uv1))])]
    b.g.nodes = [gt.Node(mesh=0)]
    b.g.scenes = [gt.Scene(nodes=[0])]
    path = b.save(tmp_path / "multi.gltf")
    assets = f'<mesh id="mesh" src="{path}" format="gltf"/>'
    body = '<object3D x="80" y="45" scaleX="0.01" scaleY="0.01" scaleZ="0.01" id="o" primitive="mesh" mesh="mesh"/>'
    lights = '<light id="a" type="ambient" intensity="1"/>' + KEY
    actual = frame(doc(tmp_path, body, assets=assets, lights=lights))
    # Independent reference: bake the selected/transformed coordinate into set 0.
    import json
    data = json.loads((tmp_path / "multi.gltf").read_text())
    for node in (data["materials"][0], data["materials"][0]["pbrMetallicRoughness"]):
        for value in node.values():
            if isinstance(value, dict) and "index" in value:
                value["texCoord"] = 0
                value.pop("extensions", None)
    a = data["accessors"][uv0]
    import base64
    raw = bytearray(base64.b64decode(data["buffers"][0]["uri"].split(",")[1]))
    off = data["bufferViews"][a["bufferView"]].get("byteOffset", 0)
    raw[off:off + 32] = np.array([[.625, .5]] * 4, "<f4").tobytes()
    data["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(raw).decode()
    ref = tmp_path / "reference.gltf"
    ref.write_text(json.dumps(data))
    expected = frame(doc(tmp_path, body, assets=assets.replace(path, str(ref)), lights=lights, name="ref.xml"))
    assert np.any(actual[..., :3] > .05)
    np.testing.assert_allclose(actual, expected, atol=1e-5)


@pytest.mark.parametrize("strength", [0., .35, 1.])
def test_gltf_occlusion_strength_scales_indirect_light(tmp_path, strength):
    from test_3d_loaders import Builder
    import pygltflib as gt
    b = Builder()
    pos = b.add([[-40, -30, 0], [40, -30, 0], [40, 30, 0], [-40, 30, 0]], "VEC3")
    uv = b.add([[.5, .5]] * 4, "VEC2")
    idx = b.add([0, 1, 2, 0, 2, 3], "SCALAR", gt.UNSIGNED_SHORT)
    tex = b.image_png(np.array([[[64, 255, 255, 255]]], np.uint8))
    mat = gt.Material(pbrMetallicRoughness=gt.PbrMetallicRoughness(metallicFactor=0, roughnessFactor=1))
    b.g.materials = [mat]
    b.g.meshes = [gt.Mesh(primitives=[gt.Primitive(indices=idx, material=0,
        attributes=gt.Attributes(POSITION=pos, TEXCOORD_0=uv))])]
    b.g.nodes, b.g.scenes = [gt.Node(mesh=0)], [gt.Scene(nodes=[0])]
    body = '<object3D x="80" y="45" scaleX="0.01" scaleY="0.01" scaleZ="0.01" id="o" primitive="mesh" mesh="m"/>'
    lights = '<light id="a" type="ambient" intensity="1"/>'
    def render(name):
        path = b.save(tmp_path / f"{name}.gltf")
        return frame(doc(tmp_path, body, assets=f'<mesh id="m" src="{path}" format="gltf"/>',
                         lights=lights, name=f"{name}.xml"))
    reference = render("plain")
    mat.occlusionTexture = gt.OcclusionTextureInfo(index=tex, strength=strength)
    actual = render("occluded")
    assert reference[45, 80, :3].min() > .1
    np.testing.assert_allclose(actual[..., :3], reference[..., :3] * (1 - strength + strength * 64 / 255),
                               atol=1e-5)
    np.testing.assert_array_equal(actual[..., 3], reference[..., 3])


# ---------------------------------------------------------------- geometry / projection
def test_object_projects_to_the_expected_pixel(tmp_path):
    r = doc(tmp_path, '<camera x="160" y="90" id="c" z="-1000" fov="60"/>'
                      '<object3D id="o" primitive="sphere" radius="6" x="260" y="40" material="u" segments="48"/>',
            materials='<material id="u" baseColor="#FFFFFFFF" unlit="true"/>', w=320, h=180)
    a = frame(r)[..., 3]
    f = 160 / math.tan(math.radians(30))
    assert centroid(a) == pytest.approx([160 + f * 0.1, 90 - f * 0.05], abs=0.1)
    assert a.sum() == pytest.approx(math.pi * (6 * f / 1000) ** 2, rel=0.12)


@pytest.mark.parametrize("prim,extra", [("sphere", ""), ("box", 'bevel="6"'), ("plane", ""), ("cylinder", 'bevel="4"'),
                                        ("cone", ""), ("torus", 'height="20"'), ("capsule", ""),
                                        ("text", 'text="Hi" height="60" depth="20" bevel="3"'),
                                        ("extrude", 'path="M-30 -30 L30 -30 L0 30 Z" depth="15"')])
def test_every_primitive_draws(tmp_path, prim, extra):
    r = doc(tmp_path, f'<object3D x="80" y="45" id="o" primitive="{prim}" radius="30" rotationY="-20" rotationX="15" {extra}/>')
    px = frame(r)
    assert px[..., 3].sum() > 50
    assert px[..., :3].max() > 0.05


def test_occlusion_between_object_layers(tmp_path):
    # implicit camera f = 80 / tan 30 = 138.6 px: front ~62 px wide, back ~116 px wide
    r = doc(tmp_path, '<object3D x="80" y="45" id="front" primitive="box" width="40" height="40" depth="10" z="-50" material="g"/>'
                      '<object3D x="80" y="45" id="back" primitive="box" width="200" height="60" depth="10" z="100" material="r"/>',
            materials='<material id="r" baseColor="#FF0000FF" unlit="true"/><material id="g" baseColor="#00FF00FF" unlit="true"/>')
    px = frame(r)
    assert px[45, 80, 1] > 0.9 and px[45, 80, 0] < 0.05          # front box wins although drawn first
    assert px[45, 39, 0] > 0.9 and px[45, 120, 0] > 0.9           # the back box around it


def test_visibility_window_condition_and_opacity(tmp_path):
    r = doc(tmp_path, '<object3D x="80" y="45" id="a" primitive="sphere" radius="20" start="1" end="2"/>'
                      '<object3D y="45" id="b" primitive="sphere" radius="20" x="30" visible="false"/>'
                      '<object3D y="45" id="c" primitive="sphere" radius="20" x="130" opacity="0.5" material="u"/>',
            materials='<material id="u" baseColor="#FFFFFFFF" unlit="true"/>')
    assert frame(r, 0.5)[45, 80, 3] == 0 and frame(r, 1.5)[45, 80, 3] > 0.9
    assert frame(r, 1.5)[45, 30, 3] == 0
    assert frame(r, 1.5)[45, 130, 3] == pytest.approx(0.5, abs=0.02)


# ---------------------------------------------------------------- materials
def _sphere_scene(tmp_path, mat: str, name: str, lights: str = KEY) -> np.ndarray:
    r = doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="sphere" radius="35" material="m" segments="64"/>',
            materials=f'<material id="m" {mat}/>', lights=lights, name=name)
    return frame(r)


def test_metal_highlight_is_tinted_dielectric_highlight_is_white(tmp_path):
    metal = _sphere_scene(tmp_path, 'baseColor="#FF2020FF" metallic="1" roughness="0.25"', "m.xml")
    diel = _sphere_scene(tmp_path, 'baseColor="#FF2020FF" metallic="0" roughness="0.25"', "d.xml")
    def hot(px):
        lum = px[..., :3].sum(-1)
        y, x = np.unravel_index(np.argmax(lum), lum.shape)
        return px[y, x, :3]
    hm, hd = hot(metal), hot(diel)
    assert hm[1] / hm[0] < 0.3                                   # metal: highlight takes the base colour
    assert hd[1] / hd[0] > 0.5                                   # dielectric: white specular over red diffuse
    assert metal[..., :3].sum(-1)[metal[..., 3] > 0.5].mean() < diel[..., :3].sum(-1)[diel[..., 3] > 0.5].mean()


def test_roughness_spreads_the_highlight(tmp_path):
    a = _sphere_scene(tmp_path, 'baseColor="#202020FF" roughness="0.08"', "a.xml")[..., :3].sum(-1)
    b = _sphere_scene(tmp_path, 'baseColor="#202020FF" roughness="0.7"', "b.xml")[..., :3].sum(-1)
    assert a.max() > 3 * b.max()


def test_emissive_unlit_and_clearcoat_sheen_iridescence_anisotropy_change_shading(tmp_path):
    base = _sphere_scene(tmp_path, 'baseColor="#4060A0FF" roughness="0.6"', "b.xml")
    for i, attrs in enumerate(['emissive="#FF8000FF" emissiveStrength="3"', 'unlit="true"', 'clearcoat="1"',
                               'sheenColor="#FFFFFFFF" sheenRoughness="0.5"', 'iridescence="1"',
                               'anisotropy="0.9" metallic="1"', 'specular="0"',
                               'specularColor="#FF0000FF"', 'ior="2.4"']):
        px = _sphere_scene(tmp_path, f'baseColor="#4060A0FF" roughness="0.6" {attrs}', f"x{i}.xml")
        assert np.abs(px - base).max() > 0.01, attrs
    unlit = _sphere_scene(tmp_path, 'baseColor="#4060A0FF" unlit="true"', "u.xml")
    m = unlit[..., 3] > 0.99
    from scenerender.raster import srgb_to_linear
    assert unlit[m][:, :3].mean(0) == pytest.approx(srgb_to_linear(np.array([0x40, 0x60, 0xA0]) / 255), abs=1e-3)


def test_alpha_modes_and_double_sided(tmp_path):
    common = 'baseColor="#FFFFFF80" unlit="true"'
    for mode, expect in (("opaque", 1.0), ("blend", 0x80 / 255), ("mask", 1.0)):
        r = doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="plane" width="60" height="40" material="m"/>',
                materials=f'<material id="m" {common} alphaMode="{mode}" alphaCutoff="0.4"/>', name=f"{mode}.xml")
        assert frame(r)[45, 80, 3] == pytest.approx(expect, abs=0.01), mode
    r = doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="plane" width="60" height="40" material="m" alphaCutoff="0.9"/>'
            .replace(' alphaCutoff="0.9"', ''), materials=f'<material id="m" {common} alphaMode="mask" alphaCutoff="0.9"/>',
            name="cut.xml")
    assert frame(r)[45, 80, 3] == 0
    for ds, expect in (("false", 0.0), ("true", 1.0)):
        r = doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="plane" width="60" height="40" rotationY="-180" material="m"/>',
                materials=f'<material id="m" baseColor="#FFFFFFFF" unlit="true" doubleSided="{ds}"/>', name=f"ds{ds}.xml")
        assert frame(r)[45, 80, 3] == pytest.approx(expect), ds


def test_transmission_refracts_the_scene_behind(tmp_path):
    body = ('<object3D x="80" y="45" id="wall" primitive="box" width="400" height="300" depth="10" z="200" material="red"/>'
            '<object3D x="80" y="45" id="ball" primitive="sphere" radius="30" material="glass" segments="64"/>')
    mats = ('<material id="red" baseColor="#FF0000FF" unlit="true"/>'
            '<material id="glass" baseColor="#FFFFFFFF" transmission="1" roughness="0" ior="1.5" thickness="0.6" {x}/>')
    clear = frame(doc(tmp_path, body, materials=mats.format(x=""), name="a.xml"))
    tinted = frame(doc(tmp_path, body, materials=mats.format(x='attenuationColor="#00FF00FF" attenuationDistance="0.3"'),
                       name="b.xml"))
    assert clear[45, 80, 0] > 0.5 and clear[45, 80, 3] > 0.99   # the red wall is seen through the glass
    assert tinted[45, 80, 0] < clear[45, 80, 0] * 0.5           # Beer-Lambert attenuation
    disp = frame(doc(tmp_path, body.replace('z="-200"', 'z="-200" x="60"'),
                     materials=mats.format(x='dispersion="1"'), name="c.xml"))
    assert disp[..., 3].sum() > 0
    # without anything behind it the glass lets the 2D backdrop through
    alone = frame(doc(tmp_path, '<object3D x="80" y="45" id="ball" primitive="sphere" radius="30" material="glass"/>',
                      materials=mats.format(x=""), name="d.xml"))
    assert alone[45, 80, 3] < 0.5


def test_texture_maps_and_uv_scale(tmp_path):
    from PIL import Image
    img = np.zeros((8, 8, 3), np.uint8)
    img[:, :4] = 255
    Image.fromarray(img).save(tmp_path / "half.png")
    mats = ('<material id="m" baseColor="#FFFFFFFF" unlit="true" baseColorMap="half.png"/>'
            '<material id="t" baseColor="#FFFFFFFF" unlit="true" baseColorMap="half.png" uvScaleX="2"/>')
    r = doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="plane" width="160" height="90" material="m"/>', materials=mats)
    px = frame(r)
    assert px[45, 20, 0] > 0.9 and px[45, 140, 0] < 0.05
    r2 = doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="plane" width="160" height="90" material="t"/>', materials=mats, name="t.xml")
    row = frame(r2)[45, :, 0]
    assert (np.diff((row > 0.5).astype(int)) != 0).sum() >= 3     # two repeats: 3 transitions
    nm = np.zeros((8, 8, 3), np.uint8)
    nm[..., 0], nm[..., 1], nm[..., 2] = 200, 128, 180
    Image.fromarray(nm).save(tmp_path / "n.png")
    flat = frame(doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="plane" width="160" height="90" material="a"/>',
                     materials='<material id="a" baseColor="#808080FF"/>', lights=KEY, name="f.xml"))
    bump = frame(doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="plane" width="160" height="90" material="a"/>',
                     materials='<material id="a" baseColor="#808080FF" normalMap="n.png" normalScale="1"/>', lights=KEY,
                     name="b.xml"))
    assert np.abs(bump - flat)[..., :3].max() > 0.02
    disp = np.full((8, 8, 3), 255, np.uint8)
    Image.fromarray(disp).save(tmp_path / "d.png")
    up = frame(doc(tmp_path, '<camera x="80" y="45" id="c" z="-400"/><object3D x="80" y="45" id="o" primitive="plane" width="60" height="60" material="a" segments="4"/>',
                   materials='<material id="a" baseColor="#FFFFFFFF" unlit="true" displacementMap="d.png" displacementScale="100"/>',
                   name="dp.xml"))
    base = frame(doc(tmp_path, '<camera x="80" y="45" id="c" z="-400"/><object3D x="80" y="45" id="o" primitive="plane" width="60" height="60" material="a" segments="4"/>',
                     materials='<material id="a" baseColor="#FFFFFFFF" unlit="true"/>', name="dp0.xml"))
    assert up[..., 3].sum() > base[..., 3].sum() * 1.3             # displaced toward the camera -> larger


# ---------------------------------------------------------------- lights and shadows
FLOOR = ('<camera id="c" x="100" y="-240" z="-500" target="t"/><object3D x="100" y="60" id="t" primitive="sphere" radius="1" visible="false"/>'
         '<object3D x="100" y="60" id="floor" primitive="plane" width="1200" height="1200" rotationX="-90" material="w" castShadow="false"/>'
         '<object3D x="100" id="blk" primitive="box" width="80" height="80" depth="80" y="-60" material="w" {cs}/>')
WHITE = '<material id="w" baseColor="#C0C0C0FF" roughness="0.9"/>'


def _floor(tmp_path, light: str, name: str, cs: str = "") -> np.ndarray:
    r = doc(tmp_path, FLOOR.format(cs=cs), materials=WHITE, lights=light, name=name, w=200, h=120)
    return frame(r)


@pytest.mark.parametrize("light", [
    '<light id="l" type="directional" pitch="-90" intensity="3" castShadow="true" shadowMapSize="512"/>',
    '<light x="100" id="l" type="spot" y="-540" pitch="-90" spotAngle="90" intensity="400" castShadow="true" shadowMapSize="512"/>',
    '<light x="100" id="l" type="point" y="-540" intensity="400" castShadow="true" shadowMapSize="256"/>',
    '<light x="100" id="l" type="rect-area" y="-540" pitch="-90" width="100" height="100" intensity="400" castShadow="true" shadowMapSize="256"/>',
    '<light x="100" id="l" type="disk-area" y="-540" pitch="-90" radius="50" intensity="400" castShadow="true" shadowMapSize="256"/>',
    '<light x="100" id="l" type="sphere-area" y="-540" radius="40" intensity="400" castShadow="true" shadowMapSize="256"/>',
])
def test_shadows_present_for_every_light_type(tmp_path, light):
    lit = _floor(tmp_path, light, "a.xml", 'castShadow="false"')
    sh = _floor(tmp_path, light, "b.xml")
    diff = lit[..., :3].sum(-1) - sh[..., :3].sum(-1)
    assert diff.max() > 0.05 and (diff > 0.02).sum() > 100          # a shadow below the block
    assert diff.min() > -1e-3                                         # shadows only darken


def test_soft_shadow_penumbra_is_wider(tmp_path):
    L = '<light x="100" id="l" type="spot" y="-540" pitch="-90" spotAngle="90" intensity="400" castShadow="true" shadowMapSize="1024" shadowSoftness="{s}"/>'
    ref = _floor(tmp_path, L.format(s=0), "r.xml", 'castShadow="false"')[..., :3].sum(-1)
    m = ref > 0.05
    def partial(name, soft):
        f = _floor(tmp_path, L.format(s=soft), name)[..., :3].sum(-1)[m] / ref[m]
        return ((f > 0.1) & (f < 0.9)).sum()
    assert partial("s.xml", 0.4) > 2 * partial("h.xml", 0)


def test_receive_shadow_false_and_light_flags(tmp_path):
    L = '<light id="l" type="directional" pitch="-90" intensity="3" castShadow="true" shadowMapSize="512" {x}/>'
    r = doc(tmp_path, FLOOR.format(cs="").replace('castShadow="false"', 'castShadow="false" receiveShadow="false"'),
            materials=WHITE, lights=L.format(x=""), w=200, h=120)
    lit = _floor(tmp_path, L.format(x=""), "a.xml", 'castShadow="false"')
    assert np.abs(frame(r) - lit)[..., :3].max() < 1e-3
    nod = _floor(tmp_path, L.format(x='affectsDiffuse="false"'), "d.xml")
    nos = _floor(tmp_path, L.format(x='affectsSpecular="false"'), "s.xml")
    assert nod[..., :3].sum() < lit[..., :3].sum() * 0.3
    assert 0 < lit[..., :3].sum() - nos[..., :3].sum()


def test_point_falloff_range_and_spot_cone(tmp_path):
    def floor_with(light, name):
        return _floor(tmp_path, light, name, 'castShadow="false"')[..., :3].sum(-1)
    near = floor_with('<light x="100" id="l" type="point" y="-240" intensity="100"/>', "a.xml")
    far = floor_with('<light x="100" id="l" type="point" y="-540" intensity="100"/>', "b.xml")
    assert near.sum() / far.sum() == pytest.approx(2.7, rel=0.35)      # ~inverse square on the lit floor
    lin = floor_with('<light x="100" id="l" type="point" y="-540" intensity="100" falloff="1"/>', "c.xml")
    assert lin.sum() > far.sum() * 3
    ranged = floor_with('<light x="100" id="l" type="point" y="-540" intensity="100" range="650"/>', "d.xml")
    assert ranged.sum() < far.sum() * 0.8
    narrow = floor_with('<light x="100" id="l" type="spot" y="-540" pitch="-90" spotAngle="20" intensity="400"/>', "e.xml")
    wide = floor_with('<light x="100" id="l" type="spot" y="-540" pitch="-90" spotAngle="80" innerConeAngle="60" intensity="400"/>', "f.xml")
    assert (narrow > 0.02).sum() < (wide > 0.02).sum() * 0.5


def test_ies_profile_shapes_the_light(tmp_path):
    ies = os.path.join(MEDIA, "sample.ies")
    plain = _floor(tmp_path, '<light x="100" id="l" type="point" y="-440" pitch="-90" intensity="200"/>', "a.xml",
                   'castShadow="false"')
    shaped = _floor(tmp_path, f'<light x="100" id="l" type="point" y="-440" pitch="-90" intensity="200" ies="{ies}"/>', "b.xml",
                    'castShadow="false"')
    ratio = shaped[..., :3].sum(-1) / np.maximum(plain[..., :3].sum(-1), 1e-6)
    m = plain[..., :3].sum(-1) > 0.01
    assert ratio[m].max() - ratio[m].min() > 0.1


def test_color_temperature_and_exposure():
    from scenerender.three.lights import kelvin_rgb
    warm, d65, cool = kelvin_rgb(3200), kelvin_rgb(6504), kelvin_rgb(12000)
    assert warm[0] > warm[2] and cool[2] > cool[0]
    assert d65 == pytest.approx([1, 1, 1], abs=0.06)                     # the locus passes just below D65
    assert np.array([0.2126, 0.7152, 0.0722]) @ warm == pytest.approx(1.0)


def test_light_exposure_and_intensity_scale_linearly(tmp_path):
    a = _sphere_scene(tmp_path, 'baseColor="#808080FF"', "a.xml", '<light id="k" type="directional" pitch="-40" intensity="1"/>')
    b = _sphere_scene(tmp_path, 'baseColor="#808080FF"', "b.xml", '<light id="k" type="directional" pitch="-40" intensity="1" exposure="1"/>')
    assert b[..., :3].sum() == pytest.approx(2 * a[..., :3].sum(), rel=1e-3)


def test_dome_image_based_lighting_and_background(tmp_path):
    env = os.path.join(MEDIA, "env_sky.npy")
    px = _sphere_scene(tmp_path, 'baseColor="#FFFFFFFF" roughness="1"', "a.xml",
                       f'<light id="d" type="dome" environment="{env}"/>')
    m = px[..., 3] > 0.99
    ys = np.nonzero(m)[0]
    top = px[ys.min() + 2, 80, :3].sum()
    bottom = px[ys.max() - 2, 80, :3].sum()
    assert top > bottom * 1.5                                          # sky above, dark ground below
    chrome = _sphere_scene(tmp_path, 'baseColor="#FFFFFFFF" metallic="1" roughness="0.05"', "b.xml",
                           f'<light id="d" type="dome" environment="{env}"/>')
    assert chrome[..., :3].max() > 5                                    # the sun is reflected
    r = doc(tmp_path, '<camera x="80" y="45" id="c" z="-500"/>', lights=f'<light id="d" type="dome" environment="{env}" environmentVisible="true"/>',
            name="bg.xml")
    bg = frame(r)
    assert bg[..., 3].min() == pytest.approx(1.0) and bg[0, 80, 2] > bg[-1, 80, 2]   # sky gradient behind everything
    uni = _sphere_scene(tmp_path, 'baseColor="#FFFFFFFF" roughness="1"', "c.xml", '<light id="d" type="dome" intensity="0.5"/>')
    mm = uni[..., 3] > 0.99
    assert uni[mm][:, :3].std() < 0.05                                  # uniform dome: flat shading


def test_ambient_light(tmp_path):
    px = _sphere_scene(tmp_path, 'baseColor="#FFFFFFFF" roughness="1" specular="0"', "a.xml",
                       '<light id="a" type="ambient" intensity="0.5"/>')
    m = px[..., 3] > 0.99
    assert px[m][:, :3].mean() == pytest.approx(0.5, abs=0.02)         # irradiance pi*L -> albedo * L


def test_default_rig_without_lights(tmp_path):
    px = frame(doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="sphere" radius="30"/>'))
    assert px[..., :3].max() > 0.3


# ---------------------------------------------------------------- camera effects on 3D
def test_depth_of_field_from_depth_buffer(tmp_path):
    body = ('<camera x="80" y="45" id="c" z="-500" fov="40" depthOfField="true" fStop="0.7" focusDistance="{f}" apertureBlades="{b}"/>'
            '<object3D x="80" y="45" id="o" primitive="box" width="40" height="40" depth="4" z="300" material="u"/>')
    mats = '<material id="u" baseColor="#FFFFFFFF" unlit="true"/>'
    sharp = frame(doc(tmp_path, body.format(f=800, b=0), materials=mats, name="a.xml"))[..., 3]
    soft = frame(doc(tmp_path, body.format(f=200, b=0), materials=mats, name="b.xml"))[..., 3]
    hexa = frame(doc(tmp_path, body.format(f=200, b=6), materials=mats, name="c.xml"))[..., 3]
    assert soft.sum() == pytest.approx(sharp.sum(), rel=0.05)           # energy preserved
    assert ((soft > 0.02) & (soft < 0.98)).sum() > 3 * ((sharp > 0.02) & (sharp < 0.98)).sum()
    assert np.abs(hexa - soft).max() > 0.01                              # aperture shape matters


def test_camera_exposure_scales_3d(tmp_path):
    body = '<camera x="80" y="45" id="c" z="-600" exposure="{e}"/><object3D x="80" y="45" id="o" primitive="sphere" radius="30"/>'
    a = frame(doc(tmp_path, body.format(e=0), name="a.xml"))
    b = frame(doc(tmp_path, body.format(e=2), name="b.xml"))
    assert b[..., :3].sum() == pytest.approx(4 * a[..., :3].sum(), rel=1e-3)


def test_antialias3d_supersamples_edges(tmp_path):
    body = '<object3D x="80" y="45" id="o" primitive="box" width="80" height="50" rotation="17" material="u"/>'
    mats = '<material id="u" baseColor="#FFFFFFFF" unlit="true"/>'
    a1 = frame(doc(tmp_path, body, materials=mats, name="a.xml"))[..., 3]
    a3 = frame(doc(tmp_path, body, materials=mats, project='antialias3d="3"', name="b.xml"))[..., 3]
    assert len(np.unique(np.round(a3, 3))) > len(np.unique(np.round(a1, 3)))
    assert a3.sum() == pytest.approx(a1.sum(), rel=0.02)


def test_linear_light_false_encodes_srgb(tmp_path):
    body = '<object3D x="80" y="45" id="o" primitive="plane" width="160" height="90" material="u"/>'
    mats = '<material id="u" baseColor="#808080FF" unlit="true"/>'
    img = doc(tmp_path, body, materials=mats, project='linearLight="false"').frame_rgb(0.0)
    assert img[45, 80, 0] == pytest.approx(128, abs=1)


# ---------------------------------------------------------------- instancing, meshes
def test_instances_index_expressions_and_no_implicit_layout(tmp_path):
    """CONVENTIONS 5.2: copy i evaluates with index = i, count = N; equal transforms coincide."""
    mats = '<material id="u" baseColor="#FFFFFFFF" unlit="true"/>'
    one = frame(doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="sphere" radius="8" material="u"/>', materials=mats))
    four = frame(doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="sphere" radius="8" instances="4" material="u"/>',
                     materials=mats, name="4.xml"))
    assert np.abs(four[..., 3] - one[..., 3]).max() < 1e-3                         # no grid: one sphere's footprint
    row = frame(doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="sphere" radius="6" instances="3" material="u">'
                              '<expression property="x">index * 40 + 40</expression></object3D>', materials=mats, name="e.xml"))
    xs = np.nonzero((row[..., 3] > 0.5).any(0))[0]
    assert (np.diff(xs) > 1).sum() == 2                                             # three separate copies


def test_instances_differ_beyond_the_transform(tmp_path):
    """CONVENTIONS 5.2: every property may differ by index (here the radius and the material)."""
    mats = ('<material id="u" baseColor="#FFFFFFFF" unlit="true" alphaMode="blend">'
            '<expression property="opacity">index == 0 ? 1 : 0.5</expression></material>')
    px = frame(doc(tmp_path, '<object3D x="40" y="45" id="o" primitive="sphere" radius="6" instances="2" material="u">'
                             '<expression property="x">40 + index * 80</expression>'
                             '<expression property="radius">6 + index * 10</expression></object3D>', materials=mats))
    a = px[..., 3] > 0.2
    left, right = a[:, :80].sum(), a[:, 80:].sum()
    assert right > 4 * left > 0                                                     # copy 1 has radius 16, copy 0 6
    assert px[45, 40, 3] > 0.99 and 0.3 < px[45, 120, 3] < 0.7                       # its own material each


def test_obj_mesh_and_mesh_layer_thumbnail(tmp_path):
    obj = os.path.join(MEDIA, "cube.obj")
    r = doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="mesh" mesh="cube" scaleX="0.3" scaleY="0.3" scaleZ="0.3" rotationY="-30" rotationX="20"/>'
                      '<layer id="l" asset="cube" x="0" y="0" boxWidth="40" boxHeight="40" fit="contain"/>',
            assets=f'<mesh id="cube" src="{obj}" format="obj"/>')
    px = frame(r)
    assert px[45, 80, 3] > 0.9
    assert px[5:35, 5:35, 3].sum() > 50                                             # the thumbnail layer


def _gltf_moving_box(path, morph=False):
    import pygltflib as G
    from scenerender.three import geometry as geo
    g = geo.box(2, 2, 2)
    pos = g.positions.astype(np.float32)
    idx = g.indices.astype(np.uint32).ravel()
    times = np.array([0.0, 1.0], np.float32)
    trans = np.array([[0, 0, 0], [10, 0, 0]], np.float32)
    blobs = [pos.tobytes(), idx.tobytes(), times.tobytes(), trans.tobytes()]
    if morph:
        d = np.zeros_like(pos)
        d[:, 0] = np.sign(pos[:, 0]) * 2.0        # widen along x
        blobs.append(d.tobytes())
    data = b"".join(blobs)
    offs = np.cumsum([0] + [len(b) for b in blobs])
    views = [G.BufferView(buffer=0, byteOffset=int(offs[i]), byteLength=len(b)) for i, b in enumerate(blobs)]
    acc = [G.Accessor(bufferView=0, componentType=G.FLOAT, count=len(pos), type="VEC3", min=pos.min(0).tolist(), max=pos.max(0).tolist()),
           G.Accessor(bufferView=1, componentType=G.UNSIGNED_INT, count=len(idx), type="SCALAR"),
           G.Accessor(bufferView=2, componentType=G.FLOAT, count=2, type="SCALAR", min=[0.0], max=[1.0]),
           G.Accessor(bufferView=3, componentType=G.FLOAT, count=2, type="VEC3")]
    prim = G.Primitive(attributes=G.Attributes(POSITION=0), indices=1)
    if morph:
        acc.append(G.Accessor(bufferView=4, componentType=G.FLOAT, count=len(pos), type="VEC3"))
        prim.targets = [{"POSITION": 4}]
    gl_ = G.GLTF2(scene=0, scenes=[G.Scene(nodes=[0])], nodes=[G.Node(mesh=0)],
                  meshes=[G.Mesh(primitives=[prim], weights=[0.0] if morph else None)],
                  accessors=acc, bufferViews=views, buffers=[G.Buffer(byteLength=len(data))],
                  animations=[G.Animation(name="move", samplers=[G.AnimationSampler(input=2, output=3)],
                                          channels=[G.AnimationChannel(sampler=0, target=G.AnimationChannelTarget(node=0, path="translation"))])])
    gl_.set_binary_blob(data)
    gl_.save_binary(str(path))


def test_gltf_clip_playback_speed_offset_and_loop(tmp_path):
    glb = tmp_path / "box.glb"
    _gltf_moving_box(glb)
    body = ('<camera x="80" y="45" id="c" z="-1000" fov="60"/><object3D x="80" y="45" id="o" primitive="mesh" mesh="m" scaleX="0.05" scaleY="0.05" scaleZ="0.05" '
            'material="u" animationClip="move" animationSpeed="{s}" animationOffset="{o}"/>')
    mats = '<material id="u" baseColor="#FFFFFFFF" unlit="true"/>'
    f = 80 / math.tan(math.radians(30))
    def x_at(t, s=1, o=0, name="a.xml"):
        r = doc(tmp_path, body.format(s=s, o=o), materials=mats, assets=f'<mesh id="m" src="{glb}" format="glb"/>', name=name)
        return centroid(frame(r, t)[..., 3])[0]
    assert x_at(0.0) == pytest.approx(80, abs=0.3)
    assert x_at(0.5) == pytest.approx(80 + f * 25 / 1000, abs=0.3)                  # 5 units * 5 scale
    assert x_at(0.25, s=2, name="b.xml") == pytest.approx(80 + f * 25 / 1000, abs=0.3)
    assert x_at(0.0, o=0.5, name="c.xml") == pytest.approx(80 + f * 25 / 1000, abs=0.3)
    assert x_at(1.5, name="d.xml") == pytest.approx(80 + f * 25 / 1000, abs=0.3)    # clips loop


def test_gltf_morph_weights_override(tmp_path):
    glb = tmp_path / "m.glb"
    _gltf_moving_box(glb, morph=True)
    body = ('<object3D x="80" y="45" id="o" primitive="mesh" mesh="m" scaleX="0.1" scaleY="0.1" scaleZ="0.1" material="u" {w}/>')
    mats = '<material id="u" baseColor="#FFFFFFFF" unlit="true"/>'
    asset = f'<mesh id="m" src="{glb}" format="glb"/>'
    a = frame(doc(tmp_path, body.format(w=""), materials=mats, assets=asset, name="a.xml"))[..., 3]
    b = frame(doc(tmp_path, body.format(w='morphWeights="1"'), materials=mats, assets=asset, name="b.xml"))[..., 3]
    wa = (a > 0.5).any(0).sum()
    wb = (b > 0.5).any(0).sum()
    assert wb > wa * 1.5


def test_splat_mesh_renders(tmp_path):
    sp = os.path.join(MEDIA, "two.splat")
    r = doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="mesh" mesh="s" scaleX="0.4" scaleY="0.4" scaleZ="0.4"/>',
            assets=f'<mesh id="s" src="{sp}" format="splat"/>')
    assert frame(r)[..., 3].sum() > 1


def test_camera_layer_draws_visible_environment_as_backdrop(tmp_path):
    env = os.path.join(MEDIA, "env_sky.npy")
    body = ('<shape id="half" shape="rect" width="80" height="90" fill="#FF0000FF"/>'
            '<camera x="80" y="45" id="c" z="-300"/>'
            '<shape id="over" shape="rect" x="0" y="0" width="20" height="20" fill="#00FF00FF"/>'
            '<object3D y="45" id="o" primitive="sphere" radius="30" x="140"/>')
    r = doc(tmp_path, body, lights=f'<light id="d" type="dome" environment="{env}" environmentVisible="true"/>')
    px = frame(r)
    assert px[50, 40, 0] > 0.9 and px[50, 40, 1] < 0.05   # 2D content stays in front of the environment
    assert px[5, 5, 1] > 0.9
    assert px[..., 3].min() > 0.99                        # the environment fills the rest
    assert px[45, 120, :3].sum() != pytest.approx(px[80, 150, :3].sum(), abs=1e-3)


def test_materialx_constants_and_baked_procedural_graph(tmp_path):
    const = os.path.join(MEDIA, "constant.mtlx")
    r = doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="plane" width="160" height="90" material="m"/>',
            materials=f'<material id="m" materialX="{const}" unlit="true"/>')
    c = frame(r)[45, 80, :3]
    assert c[1] > 0.7 and c[0] < 0.15
    if not os.environ.get("DISPLAY"):
        pytest.skip("the MaterialX TextureBaker needs a GLX display")
    chk = os.path.join(MEDIA, "checker.mtlx")
    r = doc(tmp_path, '<object3D x="80" y="45" id="o" primitive="plane" width="160" height="90" material="m"/>',
            materials=f'<material id="m" materialX="{chk}" unlit="true"/>', name="b.xml")
    px = frame(r)
    red = (px[..., 0] > 0.5) & (px[..., 2] < 0.2)
    blue = (px[..., 2] > 0.5) & (px[..., 0] < 0.2)
    assert red.sum() > 1000 and blue.sum() > 1000


@pytest.mark.parametrize("name", ["scene3d_showcase.xml", "scene3d_layers.xml", "scene3d_360.xml"])
def test_fixture_documents_validate_and_render(name):
    from lxml import etree
    here = os.path.dirname(__file__)
    path = os.path.join(here, "fixtures", name)
    schema = etree.XMLSchema(etree.parse(os.path.join(here, "..", "schema", "scene-render-1.1.xsd")))
    assert schema.validate(etree.parse(path)), schema.error_log
    r = Renderer.open(path, scale=0.25)
    if name == "scene3d_360.xml":
        from scenerender.three.view360 import render_360
        px = render_360(r.rc, 0.0).px
        assert px.shape[:2] == (128, 256)
    else:
        px = r.rc.render_frame(0.5).px
    assert px[..., 3].mean() > 0.05 and np.isfinite(px).all()
