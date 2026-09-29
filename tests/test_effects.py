"""Effect contract and semantic checks through the public XML/rendering path."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from lxml import etree

from scenerender.effects import premul, straight
from scenerender.evaluator import Ctx
from scenerender.raster import Buf, linear_to_srgb, srgb_to_linear
from scenerender.registry import EFFECTS, load_plugins
from scenerender.render import Renderer


NS = {"xs": "http://www.w3.org/2001/XMLSchema"}
XSD = etree.parse(str(Path(__file__).parents[1]/"schema/scene-render-1.1.xsd"))
NAMES = XSD.xpath('//xs:complexType[@name="effectType"]/xs:attribute[@name="type"]//xs:enumeration/@value', namespaces=NS)
# Effects the Schematron requires an input for (C19): unconfigured, they must still pass through.
NEEDS_INPUT = {"lut", "gradient-map", "displacement-map", "shader"}
ADDITIVE = {"lens-blur", "film-grain", "white-balance", "lighting", "echo", "exposure", "bloom", "halation", "lens-flare", "light-leak", "light-sweep", "god-rays"}
CTX = Ctx(t=0, comp_t=0)


def effect(kind, **attrs):
    el = etree.Element("effect", id="fx", type=kind, **{k: str(v) for k, v in attrs.items()})
    return etree.tostring(el).decode()


@pytest.fixture
def make_renderer(tmp_path):
    counter = 0

    def make(effects, *, linear=True, scale=1, paints="", lights="", composition="", strict=True):
        nonlocal counter
        counter += 1
        path = tmp_path/f"scene-{counter}.xml"
        path.write_text(f'''<scene version="1.1">
          <project width="64" height="48" fps="24" duration="2" seed="43" linearLight="{str(linear).lower()}"/>
          {paints}<composition>{composition}</composition>{lights}
          <effects>{effects}</effects></scene>''')
        return Renderer.open(str(path), strict=strict, scale=scale)

    return make


@pytest.fixture
def tile():
    y, x = np.indices((48, 64), dtype=np.float32)
    a = np.zeros((48, 64, 1), np.float32)
    a[8:40, 10:54] = .75
    a[14:34, 18:46] = 1
    rgb = np.stack([x/63, y/47, (x+y)/110], -1)
    return Buf(premul(rgb, a), 7, -3)


def solid(rgb=(1,1,1), alpha=1, w=12, h=10, x0=0, y0=0):
    return Buf(premul(np.broadcast_to(np.asarray(rgb, np.float32), (h,w,3)), np.full((h,w,1), alpha, np.float32)), x0, y0)


def apply(r, buf, ctx=CTX, node=None):
    return r.rc.apply_effects(["fx"], buf, ctx, node)


def assert_contract(out, original, *, additive=False):
    assert isinstance(out, Buf)
    assert out.px.dtype == np.float32
    assert out.px.ndim == 3 and out.px.shape[-1] == 4
    assert out.w > 0 and out.h > 0
    assert isinstance(out.x0, (int, np.integer)) and isinstance(out.y0, (int, np.integer))
    assert np.isfinite(out.px).all()
    assert np.min(out.px) >= -1e-6
    assert np.max(out.px[..., 3]) <= 1+1e-6
    if not additive:
        assert np.all(out.px[..., :3] <= out.px[..., 3:4]+1e-4)
    else:
        assert np.all(out.px[out.px[..., 3] == 0, :3] == 0)


def test_registry_exactly_matches_schema():
    load_plugins()
    assert len(NAMES) == 80
    assert set(EFFECTS.entries) == set(NAMES)
    for name, entry in EFFECTS.entries.items():
        assert entry.note
        assert entry.level in ("full", "partial", "none")
        if name != "shader":
            assert entry.fn is not None
    for name in ("echo", "pixel-motion-blur", "posterize-time"):
        assert EFFECTS.entries[name].level == "full"


@pytest.mark.parametrize("kind", NAMES)
@pytest.mark.parametrize("linear", [True, False], ids=["linear", "srgb"])
@pytest.mark.parametrize("configured", [False, True], ids=["defaults", "configured"])
def test_every_effect_contract(make_renderer, tmp_path, tile, kind, linear, configured):
    attrs, paints, lights, composition = {}, "", "", ""
    if configured:
        attrs = dict(radius=2, amount=.6, size=6, angle=27, samples=6, seed=12,
                     intensity=.7, centerX=30, centerY=20, offsetX=3, offsetY=-2)
        if kind == "lut":
            (tmp_path/"identity.cube").write_text("LUT_1D_SIZE 2\n0 0 0\n1 1 1\n")
            attrs["src"] = "identity.cube"
        if kind in ("displacement-map", "difference-key", "gradient-map"):
            attrs["source"] = "plate"
            composition = '<shape id="plate" shape="rect" width="64" height="48" fill="#80A0C0FF"/>'
        if kind == "gradient-overlay":
            attrs["paint"] = "url(#ramp)"
            paints = '<paints><linearGradient id="ramp"><stop offset="0" color="#FF0000FF"/><stop offset="1" color="#0000FFFF"/></linearGradient></paints>'
        if kind == "lighting":
            attrs["lights"] = "amb pt spot sun"
            attrs["relief"] = 3
            lights = '''<lights><light id="amb" type="ambient" intensity="0.2"/>
              <light id="pt" type="point" x="20" y="12" range="60"/>
              <light id="spot" type="spot" x="30" y="25" range="80"/>
              <light id="sun" type="directional" yaw="30"/></lights>'''
    r = make_renderer(effect(kind, **attrs), linear=linear, paints=paints, lights=lights, composition=composition,
                      strict=not (kind in NEEDS_INPUT and not attrs.get("src") and not attrs.get("source")))
    before = tile.px.copy()
    out = apply(r, tile)
    other = apply(r, tile)
    assert_contract(out, tile, additive=kind in ADDITIVE)
    np.testing.assert_array_equal(tile.px, before)
    assert out.rect == other.rect
    np.testing.assert_array_equal(out.px, other.px)


@pytest.mark.parametrize("kind", NAMES)
def test_single_pixel_transparent_tiles(make_renderer, kind):
    r = make_renderer(effect(kind, radius=0, samples=1, offsetX=0, offsetY=0), strict=kind not in NEEDS_INPUT)
    b = solid(alpha=0, w=1, h=1)
    assert_contract(apply(r, b), b, additive=kind in ADDITIVE)


@pytest.mark.parametrize("kind,attrs,source,expected", [
    ("invert", {}, (1,1,1), (0,0,0)),
    ("grayscale", {}, (1,0,0), (.2126,.2126,.2126)),
    ("color-grade", {"saturation":0}, (.3,.3,.3), (.3,.3,.3)),
    ("lift-gamma-gain", {}, (.1,.2,.3), (.1,.2,.3)),
    ("cdl", {}, (.1,.2,.3), (.1,.2,.3)),
    ("hue-saturation", {"hue":120}, (1,0,0), (0,1,0)),
    ("exposure", {"exposure":1}, (.1,.2,.3), (.2,.4,.6)),
    ("fill", {"color":"#FF0000FF"}, (0,1,0), (1,0,0)),
    ("color-overlay", {"color":"#0000FFFF"}, (1,0,0), (0,0,1)),
    ("tonemap", {"tonemapper":"reinhard"}, (1,1,1), (.5,.5,.5)),
    ("tint", {"color":"#00FF00FF"}, (1,1,1), (0,1,0)),
    ("spill-suppress", {"spill":1}, (.2,.9,.3), (.2,.3,.3)),
])
def test_colour_semantics(make_renderer, kind, attrs, source, expected):
    b = solid(source, alpha=.6)
    out = apply(make_renderer(effect(kind, **attrs)), b)
    rgb, a = straight(out.px)
    np.testing.assert_allclose(rgb, np.broadcast_to(expected, rgb.shape), atol=2e-6)
    np.testing.assert_allclose(a, .6, atol=1e-7)


@pytest.mark.parametrize("kind,attrs,expected", [
    ("curves", {"curve":"0,0 .25,.5 1,1"}, .5),
    ("levels", {"inputWhite":.5}, .5),
    ("lift-gamma-gain", {"gamma":"2,2,2"}, .5),
    ("cdl", {"slope":"2,2,2"}, .5),
    ("posterize", {"levels":3}, 0),
])
def test_display_referred_operations(make_renderer, kind, attrs, expected):
    b = solid(tuple(srgb_to_linear(np.full(3, .25))), alpha=.5)
    rgb, a = straight(apply(make_renderer(effect(kind, **attrs)), b).px)
    np.testing.assert_allclose(linear_to_srgb(rgb), expected, atol=2e-6)


@pytest.mark.parametrize("attrs", [{}, {"exposure": .5}, {"contrast": 1.3}, {"saturation": .4}, {"brightness": -.1},
                                   {"exposure": -.3, "contrast": .8, "saturation": 1.6, "brightness": .05}])
def test_color_grade_is_d9(make_renderer, attrs):
    """D9: on unpremultiplied working values, 2^exposure, 0.18 (v/0.18)^contrast, Rec. 709 saturation,
    then brightness, negatives clamped to 0."""
    src = np.array([.02, .3, .9], np.float32)
    b = solid(tuple(src), alpha=.6)
    rgb, a = straight(apply(make_renderer(effect("color-grade", **attrs)), b).px)
    v = src * 2 ** attrs.get("exposure", 0)
    c = .18 * (v / .18) ** attrs.get("contrast", 1)
    y = .2126 * c[0] + .7152 * c[1] + .0722 * c[2]
    want = np.maximum(y + (c - y) * attrs.get("saturation", 1) + attrs.get("brightness", 0), 0)
    np.testing.assert_allclose(rgb, np.broadcast_to(want, rgb.shape), rtol=1e-5, atol=1e-6)
    np.testing.assert_allclose(a, .6, atol=1e-7)


def test_drop_shadow_radius_is_twice_sigma(make_renderer):
    """CONVENTIONS 5.7: a drop shadow's radius is twice the Gaussian standard deviation."""
    b = Buf.empty(0, 0, 80, 40)
    b.px[:, :40] = 1
    out = apply(make_renderer(effect("drop-shadow", radius=8, offsetX=0, offsetY=0, compositeOriginal="none")), b)
    row = out.region((0, 0, 80, 40))[20, :, 3]
    from math import erf, sqrt
    want = [.5 * (1 - erf((x + .5 - 40) / (4 * sqrt(2)))) for x in range(80)]
    np.testing.assert_allclose(row[30:50], want[30:50], atol=.02)


def test_glow_is_a_thresholded_bloom(make_renderer):
    """CONVENTIONS 5.6 / D9: glow adds the part above threshold (working-space luminance), so content
    below the threshold does not glow, and the glow is added to (not drawn over) the content."""
    dim = solid((.3, .3, .3), w=20, h=20)
    out = apply(make_renderer(effect("glow", radius=3, threshold=.5)), dim)
    np.testing.assert_allclose(out.region(dim.rect), dim.px, atol=1e-6)
    bright = solid((1, 1, 1), w=20, h=20)
    bright.px[:, :10] = 0
    out = apply(make_renderer(effect("glow", radius=3, threshold=.5)), bright)
    px = out.region(bright.rect)
    assert px[10, 8, 3] > .05                                          # spill beside the bright half
    assert px[10, 15, 0] > 1                                           # added onto the content


def test_vignette_is_d9(make_renderer):
    """CONVENTIONS 5.8: 1 - amount . smoothstep(r0, r0 + softness, r), r over the half diagonal from the
    frame centre, r0 = radius / half diagonal; absent attributes take the schema's effect defaults
    (amount 1, radius 4, softness 0.1)."""
    b = solid((1, 1, 1), w=64, h=48)
    half = .5 * np.hypot(64, 48)
    y, x = np.indices((48, 64)) + .5
    r = np.hypot(x - 32, y - 24) / half

    def ss(lo, hi, v):
        u = np.clip((v - lo) / (hi - lo), 0, 1)
        return u * u * (3 - 2 * u)
    got = apply(make_renderer(effect("vignette")), b).px[..., 0]
    np.testing.assert_allclose(got, 1 - 1.0 * ss(4 / half, 4 / half + .1, r), atol=1e-5)
    got = apply(make_renderer(effect("vignette", amount=.8, radius=10, softness=.2)), b).px[..., 0]
    np.testing.assert_allclose(got, 1 - .8 * ss(10 / half, 10 / half + .2, r), atol=1e-5)


def test_threshold_is_binary_and_preserves_matte(make_renderer, tile):
    out = apply(make_renderer(effect("threshold", threshold=.5)), tile)
    rgb, a = straight(out.px)
    assert np.all(np.isclose(rgb, 0) | np.isclose(rgb, 1))
    np.testing.assert_array_equal(a, tile.px[..., 3:4])


@pytest.mark.parametrize("radius", [0, 2, 15])
@pytest.mark.parametrize("kind", ["blur", "lens-blur", "directional-blur"])
def test_blur_conserves_alpha_and_grows(make_renderer, tile, radius, kind):
    out = apply(make_renderer(effect(kind, radius=radius, angle=35, samples=25)), tile)
    assert float(out.px[..., 3].sum()) == pytest.approx(float(tile.px[..., 3].sum()), rel=.02)
    assert out.x0 <= tile.x0 and out.y0 <= tile.y0
    if radius:
        assert out.w > tile.w and out.h > tile.h
    else:
        np.testing.assert_allclose(out.px, tile.px, atol=2e-7)


def test_blur_scaling(make_renderer, tile):
    fx = effect("blur", radius=2)
    a = apply(make_renderer(fx, scale=1), tile)
    b = apply(make_renderer(fx, scale=2), tile)
    assert b.w-tile.w == 2*(a.w-tile.w)
    assert b.x0-tile.x0 == 2*(a.x0-tile.x0)


@pytest.mark.parametrize("mode,expected", [("behind", (1,0,0)), ("on-top", (0,0,1)), ("none", (0,0,1))])
def test_shadow_composite_original_placement(make_renderer, mode, expected):
    """CONVENTIONS 5.6: compositeOriginal places the effect, by default beneath the original."""
    r = make_renderer(effect("drop-shadow", radius=0, offsetX=0, offsetY=0,
                             color="#0000FFFF", compositeOriginal=mode))
    out = apply(r, solid((1,0,0)))
    np.testing.assert_allclose(out.px[..., :3], np.broadcast_to(expected, out.px[..., :3].shape))


def test_drop_shadow_offset_and_spill(make_renderer, tile):
    out = apply(make_renderer(effect("drop-shadow", radius=2, offsetX=9, offsetY=-5, compositeOriginal="none")), tile)
    assert out.w > tile.w and out.h > tile.h
    def centroid(b):
        y, x = np.indices(b.px.shape[:2])
        a = b.px[..., 3]
        return np.array([((x+b.x0)*a).sum()/a.sum(), ((y+b.y0)*a).sum()/a.sum()])
    np.testing.assert_allclose(centroid(out)-centroid(tile), [9,-5], atol=.01)


@pytest.mark.parametrize("kind", ["stroke", "outline", "bevel"])
@pytest.mark.parametrize("position", ["outside", "inside", "center"])
def test_style_position_and_original_none(make_renderer, kind, position):
    b = Buf.empty(5, 4, 20, 20)
    b.px[5:15, 5:15] = 1
    out = apply(make_renderer(effect(kind, radius=2, position=position, compositeOriginal="none")), b)
    coverage = out.region(b.rect)[..., 3]
    assert coverage.sum() > 0
    assert coverage[10, 10] == 0
    if position == "inside":
        assert out.rect == b.rect
        assert np.all(coverage[b.px[..., 3] == 0] == 0)
    elif position == "outside":
        assert np.all(coverage[b.px[..., 3] == 1] == 0)


def test_keying_and_choke(make_renderer):
    green = solid((0,1,0))
    red = solid((1,0,0))
    key = make_renderer(effect("chroma-key", keyColor="#00FF00FF"))
    assert not apply(key, green).px.any()
    np.testing.assert_allclose(apply(key, red).px, red.px)
    luma = make_renderer(effect("luma-key", threshold=.5, softness=0))
    assert not apply(luma, solid((0,0,0))).px.any()
    np.testing.assert_array_equal(apply(luma, solid()).px, solid().px)
    b = Buf.empty(0, 0, 20, 20)
    b.px[5:15, 5:15] = 1
    choke = apply(make_renderer(effect("matte-choke", amount=2)), b)
    grow = apply(make_renderer(effect("matte-choke", amount=-2)), b)
    assert choke.px[..., 3].sum() < b.px[..., 3].sum() < grow.px[..., 3].sum()
    assert_contract(grow, b)


def test_lut_1d_domain_and_alpha(make_renderer, tmp_path):
    (tmp_path/"invert.cube").write_text('TITLE "invert"\nLUT_1D_SIZE 2\nDOMAIN_MIN .2 .2 .2\nDOMAIN_MAX .8 .8 .8\n1 1 1\n0 0 0\n')
    encoded = np.array([.2,.5,.8], np.float32)
    b = solid(tuple(srgb_to_linear(encoded)), alpha=.4)
    out = apply(make_renderer(effect("lut", src="invert.cube")), b)
    rgb, a = straight(out.px)
    np.testing.assert_allclose(linear_to_srgb(rgb), np.broadcast_to([1,.5,0], rgb.shape), atol=1e-6)
    np.testing.assert_array_equal(a, b.px[..., 3:4])


def test_lut_3d_trilinear_axis_order(make_renderer, tmp_path):
    rows = [f"{r*g} {g*b} {b*r}" for b in (0,1) for g in (0,1) for r in (0,1)]
    (tmp_path/"rgb.cube").write_text("LUT_3D_SIZE 2\n"+"\n".join(rows))
    rgb = np.array([.3,.6,.8], np.float32)
    b = solid(tuple(srgb_to_linear(rgb)), alpha=.3)
    out = apply(make_renderer(effect("lut", src="rgb.cube")), b)
    value, a = straight(out.px)
    np.testing.assert_allclose(linear_to_srgb(value), np.broadcast_to([.18,.48,.24], value.shape), atol=1e-6)


@pytest.mark.parametrize("src,contents", [("bad.clf", ""), ("bad.cube", "LUT_3D_SIZE 2\n0 0 0"), ("missing.cube", None)])
def test_unsupported_or_bad_lut_warns_and_skips(make_renderer, tmp_path, tile, caplog, src, contents):
    from scenerender.registry import _warned
    _warned.discard(("effect-lut", src))
    if contents is not None:
        (tmp_path/src).write_text(contents)
    r = make_renderer(effect("lut", src=src))
    np.testing.assert_array_equal(apply(r, tile).px, tile.px)
    apply(r, tile)
    assert len([record for record in caplog.records if "effect-lut" in record.message]) == 1


def test_gradient_map_paint_stop_space_and_midpoint(make_renderer):
    paints = '''<paints><linearGradient id="ramp" interpolationSpace="srgb">
      <stop offset="0" color="#FF0000FF" midpoint="0.25"/>
      <stop offset="1" color="#0000FF80"/></linearGradient></paints>'''
    out = apply(make_renderer(effect("gradient-map", source="ramp"), paints=paints), solid((.25,.25,.25)))
    rgb, a = straight(out.px)
    np.testing.assert_allclose(linear_to_srgb(rgb), np.broadcast_to([.5,0,.5], rgb.shape), atol=3e-5)
    np.testing.assert_allclose(a, (1+128/255)/2, atol=3e-5)


def test_source_node_inputs_and_offsets(make_renderer):
    composition = '<shape id="plate" shape="rect" x="9" y="6" width="12" height="10" fill="#FF0000FF"/>'
    b = solid((1,0,0), x0=9, y0=6)
    r = make_renderer(effect("difference-key", source="plate", softness=0), composition=composition)
    assert apply(r, b).px[..., 3].max() == 0
    r = make_renderer(effect("displacement-map", source="plate", amount=2, channel="red"), composition=composition)
    out = apply(r, b)
    assert out.w > b.w
    assert not np.array_equal(out.region(b.rect), b.px)
    r = make_renderer(effect("gradient-map", source="plate"), composition=composition)
    rgb, a = straight(apply(r, solid((.5,.5,.5))).px)
    np.testing.assert_allclose(rgb, np.broadcast_to([1,0,0], rgb.shape))


def test_recursive_source_is_guarded(make_renderer, caplog):
    composition = '<shape id="recursive-plate" shape="rect" width="10" height="10" effects="fx"/>'
    r = make_renderer(effect("displacement-map", source="recursive-plate"), composition=composition)
    out = apply(r, solid())
    assert np.isfinite(out.px).all()
    assert "recursive source reference" in caplog.text


def test_lighting_ambient_and_point_frame_coordinates(make_renderer):
    lights = '<lights><light id="amb" type="ambient" intensity="0.5"/></lights>'
    b = solid((.4,.6,.8), alpha=.7)
    out = apply(make_renderer(effect("lighting", lights="amb"), lights=lights), b)
    np.testing.assert_allclose(out.px[..., :3], b.px[..., :3]*.5, atol=1e-6)
    np.testing.assert_array_equal(out.px[..., 3], b.px[..., 3])
    lights = '<lights><light id="pt" type="point" x="20" y="10" range="20"/></lights>'
    r = make_renderer(effect("lighting", lights="pt"), lights=lights)
    near = apply(r, solid((.5,.5,.5), w=1, h=1, x0=20, y0=10))
    far = apply(r, solid((.5,.5,.5), w=1, h=1, x0=40, y0=10))
    assert near.px[0,0,0] > far.px[0,0,0]


@pytest.mark.parametrize("kind", ["pixelate", "mosaic"])
def test_block_effects_have_constant_cells(make_renderer, tile, kind):
    tile = Buf(tile.px, 0, 0)
    out = apply(make_renderer(effect(kind, size=4)), tile)
    for y in range(0, tile.h, 4):
        for x in range(0, tile.w, 4):
            block = out.px[y:y+4,x:x+4]
            np.testing.assert_allclose(block, np.broadcast_to(block[0,0], block.shape))
    if kind == "mosaic":
        np.testing.assert_allclose(out.px.sum((0,1)), tile.px.sum((0,1)), rtol=1e-5)


def test_animation_and_framework_mix_enabled(make_renderer, tile):
    anim = '''<effect id="fx" type="exposure"><animate property="exposure">
      <key time="0" value="0"/><key time="1" value="1"/></animate></effect>'''
    r = make_renderer(anim)
    np.testing.assert_allclose(apply(r, tile).px, tile.px, atol=1e-7)
    out = apply(r, tile, Ctx(t=1, comp_t=1))
    np.testing.assert_allclose(out.px[..., :3], tile.px[..., :3]*2, atol=1e-7)
    r = make_renderer(effect("invert", enabled="false"))
    np.testing.assert_array_equal(apply(r, tile).px, tile.px)
    full = apply(make_renderer(effect("blur", radius=2)), tile)
    half = apply(make_renderer(effect("blur", radius=2, mix=.5)), tile)
    assert half.rect == full.rect
    np.testing.assert_allclose(half.px, (tile.region(full.rect)+full.px)/2, atol=1e-7)


@pytest.mark.parametrize("kind", ["noise", "film-grain", "glitch", "vhs", "fractal-noise", "turbulent-displace", "heat-haze"])
def test_random_effects_are_independent_of_render_order(make_renderer, tile, kind):
    r = make_renderer(effect(kind, size=5, amount=3, seed=21))
    c = Ctx(t=.35, comp_t=.35, frame=8)
    before = apply(r, tile, c)
    apply(r, tile, Ctx(t=1, comp_t=1, frame=24))
    after = apply(r, tile, c)
    np.testing.assert_array_equal(before.px, after.px)


def test_temporal_approximations_use_evaluated_node_translation(make_renderer):
    composition = '''<shape id="moving" shape="rect" width="12" height="10">
      <animate property="x"><key time="0" value="0"/><key time="1" value="24"/></animate></shape>'''
    r = make_renderer(effect("posterize-time", frequency=2), composition=composition)
    b = solid(x0=18)
    c = Ctx(t=.75, comp_t=.75, frame=18)
    out = apply(r, b, c, r.doc.ids["moving"])
    assert out.px[..., 3].sum() == pytest.approx(b.px[..., 3].sum())
    y, x = np.where(out.px[..., 3] > .99)
    assert x.min()+out.x0 == 12
    r = make_renderer(effect("pixel-motion-blur"), composition=composition)
    out = apply(r, b, c, r.doc.ids["moving"])
    assert out.w > b.w


def test_shader_warns_and_skips(make_renderer, tile, caplog):
    from scenerender.registry import _warned
    _warned.discard(("effect", "shader"))
    r = make_renderer(effect("shader", src="fragment.glsl"))
    np.testing.assert_array_equal(apply(r, tile).px, tile.px)
    assert "shader" in caplog.text


def test_fill_paint_scale_and_alpha(make_renderer):
    paints = '''<paints><linearGradient id="paint-fill" interpolationSpace="srgb">
      <stop offset="0" color="#FF0000FF"/><stop offset="1" color="#0000FFFF"/>
      </linearGradient></paints>'''
    fx = effect("fill", paint="url(#paint-fill)")
    b = solid(alpha=.4, w=40, h=20, x0=13, y0=9)
    normal = apply(make_renderer(fx, paints=paints), b)
    scaled = apply(make_renderer(fx, paints=paints, scale=2), b)
    np.testing.assert_allclose(normal.px, scaled.px, atol=2e-3)
    np.testing.assert_allclose(normal.px[..., 3], .4, atol=1e-7)
    assert normal.px[10, 0, 0] > normal.px[10, -1, 0]
    assert normal.px[10, 0, 2] < normal.px[10, -1, 2]


def test_colour_and_radius_animation(make_renderer, tile):
    xml = '''<effect id="fx" type="fill"><animate property="color">
      <key time="0" value="#FF0000FF"/><key time="1" value="#0000FFFF"/>
      </animate></effect>'''
    r = make_renderer(xml)
    assert apply(r, tile).px[..., 0].sum() > 0
    out = apply(r, tile, Ctx(t=1, comp_t=1))
    assert out.px[..., 0].sum() == 0 and out.px[..., 2].sum() > 0
    xml = '''<effect id="fx" type="blur"><animate property="radius">
      <key time="0" value="0"/><key time="1" value="3"/>
      </animate></effect>'''
    r = make_renderer(xml, scale=2)
    assert apply(r, tile).rect == tile.rect
    assert apply(r, tile, Ctx(t=1, comp_t=1)).w == tile.w+48


@pytest.mark.parametrize("kind", ["curves", "levels", "invert"])
def test_alpha_channel_operations_remultiply(make_renderer, kind):
    attrs = {"channel":"alpha"}
    if kind == "curves":
        attrs["curve"] = "0,1 1,0"
    elif kind == "levels":
        attrs.update(outputBlack=1, outputWhite=0)
    b = solid((.1,.2,.3), alpha=.25)
    out = apply(make_renderer(effect(kind, **attrs)), b)
    rgb, a = straight(out.px)
    np.testing.assert_allclose(rgb, np.broadcast_to([.1,.2,.3], rgb.shape), atol=1e-6)
    np.testing.assert_allclose(a, .75, atol=1e-6)
    assert_contract(out, b)


def test_physical_blur_and_exposure_match_between_working_spaces(make_renderer):
    b = solid((.2,.3,.4), alpha=.7)
    b.px[:, 6:, :3] *= .2
    rgb, a = straight(b.px)
    encoded = Buf(premul(linear_to_srgb(rgb), a))
    for kind, attrs in (("blur", {"radius":2}), ("exposure", {"exposure":1})):
        fx = effect(kind, **attrs)
        linear = apply(make_renderer(fx), b)
        srgb = apply(make_renderer(fx, linear=False), encoded)
        rgb, alpha = straight(srgb.px)
        assert linear.rect == srgb.rect
        np.testing.assert_allclose(linear.px, premul(srgb_to_linear(rgb), alpha), atol=1e-6)


@pytest.mark.parametrize("kind", ["blur", "drop-shadow", "glow", "inner-shadow", "inner-glow", "stroke", "outline", "bevel", "long-shadow"])
def test_translucent_style_colours_stay_premultiplied(make_renderer, tile, kind):
    r = make_renderer(effect(kind, color="#A040C080", radius=2, intensity=.8, compositeOriginal="none"))
    out = apply(r, tile)
    assert_contract(out, tile)
    if kind != "blur":
        assert out.px[..., 3].max() <= 128/255+1e-6


def test_zero_displacement_and_rotational_warps(make_renderer, tile):
    for kind in ("turbulent-displace", "heat-haze", "wave-warp", "ripple", "spherize", "bulge", "lens-distortion"):
        out = apply(make_renderer(effect(kind, amount=0)), tile)
        assert out.rect == tile.rect
        np.testing.assert_allclose(out.px, tile.px, atol=6e-6)


def test_sharpen_increases_edge_contrast(make_renderer):
    b = solid((.2,.2,.2), w=20)
    b.px[:, 10:, :3] = .7
    for kind in ("sharpen", "unsharp-mask"):
        out = apply(make_renderer(effect(kind, radius=1, threshold=0)), b)
        assert out.px[5, 9, 0] < b.px[5, 9, 0]
        assert out.px[5, 10, 0] > b.px[5, 10, 0]
        np.testing.assert_array_equal(out.px[..., 3], b.px[..., 3])


@pytest.mark.parametrize("samples", [1, 8, 15])
def test_lens_blur_does_not_translate_image(make_renderer, tile, samples):
    out = apply(make_renderer(effect("lens-blur", radius=4, samples=samples)), tile)
    def centroid(b):
        y, x = np.indices(b.px.shape[:2])
        a = b.px[..., 3]
        return np.array([((x+b.x0)*a).sum(), ((y+b.y0)*a).sum()])/a.sum()
    np.testing.assert_allclose(centroid(out), centroid(tile), atol=1e-5)


@pytest.mark.parametrize("mode", ["ambient", "point", "spot", "directional"])
def test_lighting_types_respond_to_colour_intensity_and_scale(make_renderer, mode):
    lights = f'<lights><light id="lamp" type="{mode}" x="10" y="5" range="30" color="#FF0000FF" intensity="0.5"/></lights>'
    fx = effect("lighting", lights="lamp")
    near = apply(make_renderer(fx, lights=lights), solid(w=1, h=1, x0=10, y0=5))
    large = apply(make_renderer(fx, lights=lights, scale=2), solid(w=1, h=1, x0=20, y0=10))
    assert 0 < near.px[0,0,0] <= .5
    assert near.px[0,0,1] == near.px[0,0,2] == 0
    np.testing.assert_allclose(near.px, large.px, atol=1e-6)


def test_source_opacity_and_transparent_plate(make_renderer):
    composition = '<shape id="plate-alpha" shape="rect" width="12" height="10" fill="#FF0000FF" opacity="0.5"/>'
    r = make_renderer(effect("difference-key", source="plate-alpha"), composition=composition)
    b = solid((1,0,0))
    np.testing.assert_allclose(apply(r, b).px[..., 3], .5, atol=1e-7)
    outside = solid((1,0,0), x0=30)
    np.testing.assert_array_equal(apply(r, outside).px, outside.px)


def test_seed_animation_uses_evaluator(make_renderer, tile):
    xml = '''<effect id="fx" type="noise"><animate property="seed">
      <key time="0" value="1"/><key time="1" value="2"/></animate></effect>'''
    r = make_renderer(xml)
    first = apply(r, tile, CTX)
    later = apply(r, tile, Ctx(t=1, comp_t=1, frame=0))
    assert not np.array_equal(first.px, later.px)
    np.testing.assert_array_equal(first.px, apply(r, tile, CTX).px)
