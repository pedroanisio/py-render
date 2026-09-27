"""Shape modifiers (scenerender.modifiers) and deformers (scenerender.deform)."""
from __future__ import annotations

import math
import textwrap

import numpy as np
import pytest
from lxml import etree

from scenerender import deform, geometry, modifiers
from scenerender.evaluator import Ctx
from scenerender.raster import Buf
from scenerender.registry import DEFORMERS, SHAPE_MODIFIERS
from scenerender.render import Renderer

C0 = Ctx(t=0.0, comp_t=0.0)


def scene(tmp_path, body: str, w=200, h=200, name="m.xml") -> str:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="{w}" height="{h}" fps="10" duration="4" background="#000000FF" linearLight="false"/>
  <composition>
{textwrap.indent(body, '    ')}
  </composition>
</scene>"""
    p = tmp_path / name
    p.write_text(xml)
    return str(p)


def setup(tmp_path, mod: str, shape='shape="rect" width="100" height="50"'):
    r = Renderer.open(scene(tmp_path, f'<shape id="s" {shape} fill="#FFFFFFFF">{mod}</shape>'))
    s = r.doc.ids["s"]
    m = next(c for c in s if etree.QName(c).localname == "shapeModifier")
    return r, s, m


def run(r, m, cmds, ctx=C0):
    return SHAPE_MODIFIERS.get(m.get("type"))(r.rc, m, cmds, ctx)


def pts_of(cmds):
    return np.array([c[1:3] if c[0] != "C" else c[5:7] for c in cmds if c[0] != "Z"], np.float64)


def area(cmds) -> float:
    tot = 0.0
    for pts, closed in geometry.flatten(cmds, 0.05):
        p = np.array(pts)
        tot += 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]))
    return abs(tot)


def raster_area(cmds, size=240) -> float:
    import cairo
    s = cairo.ImageSurface(cairo.FORMAT_A8, size, size)
    cr = cairo.Context(s)
    geometry.emit(cr, cmds)
    cr.set_fill_rule(cairo.FILL_RULE_WINDING)
    cr.fill()
    s.flush()
    return float(np.frombuffer(s.get_data(), np.uint8).sum()) / 255.0


RECT = geometry.rect(0, 0, 100, 50)


# ---------------------------------------------------------------- repeater
def test_repeater_copies_transform_and_opacity(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="repeater" copies="4" offsetX="10" rotation="90" '
                              'startOpacity="1" endOpacity="0.25"/>',
                    'shape="rect" width="20" height="10" anchorX="0" anchorY="0"')
    out = run(r, m, geometry.rect(0, 0, 20, 10))
    assert len(out) == 4
    assert [round(p.opacity, 3) for p in out] == [1.0, 0.75, 0.5, 0.25]
    p1 = pts_of(out[1])
    assert np.allclose(p1[1], (10, 20))           # (20,0) rotated 90 deg about the anchor, then +10 px
    assert np.allclose(pts_of(out[0]), pts_of(geometry.rect(0, 0, 20, 10)))


def test_repeater_zero_and_fractional_copies(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="repeater" copies="2.7"/>')
    assert len(run(r, m, RECT)) == 2
    m.set("copies", "0")
    assert run(r, m, RECT) == []


# ---------------------------------------------------------------- offset / pucker / zig-zag / twist / round
def test_offset_path_grows_and_shrinks(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="offset-path" amount="10"/>')
    out = run(r, m, RECT)[0]
    assert geometry.bounds(out) == pytest.approx((-10, -10, 110, 60), abs=1e-6)
    m.set("amount", "-5")
    assert geometry.bounds(run(r, m, RECT)[0]) == pytest.approx((5, 5, 95, 45), abs=1e-6)
    m.set("amount", "10")
    m.set("mode", "round")
    out = run(r, m, RECT)[0]
    assert geometry.bounds(out) == pytest.approx((-10, -10, 110, 60), abs=0.1)
    assert area(out) == pytest.approx(120 * 70 - (4 - math.pi) * 100, rel=0.01)


def test_pucker_bloat_moves_vertices(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="pucker-bloat" amount="0"/>')
    assert run(r, m, RECT) == [RECT]
    m.set("amount", "50")
    out = run(r, m, RECT)[0]
    v = pts_of(out)
    cen = np.array([50, 25])
    assert np.allclose(np.linalg.norm(v[1] - cen), 0.5 * np.linalg.norm(np.array([100, 0]) - cen))
    assert area(out) > area(RECT) * 0.6          # bloated edges


def test_zigzag_ridges_and_amplitude(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="zig-zag" size="5" ridges="3"/>')
    line = [("M", 0.0, 0.0), ("L", 60.0, 0.0)]
    out = run(r, m, line)[0]
    p = pts_of(out)
    assert len(p) == 7
    assert np.allclose(np.abs(p[:, 1]), 5)
    assert np.all(np.sign(p[:-1, 1]) != np.sign(p[1:, 1]))
    m.set("mode", "smooth")
    assert any(c[0] == "C" for c in run(r, m, line)[0])


def test_twist_preserves_radius(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="twist" amount="90"/>')
    out = run(r, m, RECT)[0]
    p = pts_of(out)
    cen = np.array([50, 25])
    d = np.linalg.norm(p - cen, axis=1)
    orig = np.array([pp for pts, _ in geometry.flatten(RECT, 0.25) for pp in pts[:-1]])
    assert d.max() == pytest.approx(np.linalg.norm(orig - cen, axis=1).max(), rel=1e-6)
    assert not np.allclose(p, orig[:len(p)])


def test_round_corners(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="round-corners" amount="10"/>')
    out = run(r, m, RECT)[0]
    assert geometry.bounds(out) == pytest.approx((0, 0, 100, 50), abs=1e-6)
    assert sum(1 for c in out if c[0] == "C") == 4
    assert area(out) == pytest.approx(5000 - (4 - math.pi) * 100, rel=0.005)


def test_wiggle_deterministic_animated_bounded(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="wiggle-path" size="4" detail="4" frequency="2" seed="3"/>',
                    'shape="ellipse" width="100" height="100"')
    base = geometry.ellipse(50, 50, 50, 50)
    a = run(r, m, base)[0]
    b = run(r, m, base)[0]
    c = run(r, m, base, Ctx(t=1.3, comp_t=1.3))[0]
    assert a == b and a != c
    p = pts_of(a)
    assert len(p) == 17                 # 16 wiggled points + the closing point
    rr = np.linalg.norm(p - 50, axis=1)
    assert rr.min() > 50 - 4 * 1.5 and rr.max() < 50 + 4 * 1.5


# ---------------------------------------------------------------- merge / trim
@pytest.mark.parametrize("mode,expect", [("add", 25200), ("intersect", 3600), ("subtract", 10800), ("exclude", 21600)])
def test_merge_modes_area(tmp_path, mode, expect):
    r, s, m = setup(tmp_path, f'<shapeModifier type="merge" mode="{mode}"/>')
    a, b = geometry.rect(0, 0, 120, 120), geometry.rect(60, 60, 120, 120)
    out = modifiers.merge_paths(r.rc, m, [a, b], C0)
    assert raster_area(out[0]) == pytest.approx(expect, rel=0.02)
    # per-path fallback: subpaths of one path are the operands
    out2 = run(r, m, a + b)
    assert raster_area(out2[0]) == pytest.approx(expect, rel=0.02)


def test_merge_keeps_holes(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="merge" mode="subtract"/>')
    out = modifiers.merge_paths(r.rc, m, [geometry.rect(0, 0, 100, 100), geometry.rect(25, 25, 50, 50)], C0)
    assert raster_area(out[0]) == pytest.approx(7500, rel=0.02)


def test_trim_modifier_sequential(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="trim" amount="50"/>')
    a = [("M", 0.0, 0.0), ("L", 100.0, 0.0)]
    b = [("M", 0.0, 10.0), ("L", 100.0, 10.0)]
    out = modifiers.trim_paths(r.rc, m, [a, b], C0)[0]
    lens = [sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1)) for p, _ in geometry.flatten(out)]
    assert sum(lens) == pytest.approx(100, abs=0.5) and len(lens) == 1
    m.set("amount", "0")
    assert run(r, m, a) == [a]


def test_modifiers_render_in_shape(tmp_path):
    p = scene(tmp_path, '<shape id="s" shape="ellipse" x="100" y="100" width="40" height="80" anchorX="20" anchorY="80" fill="#FFFFFFFF">'
                        '<shapeModifier type="repeater" copies="4" rotation="90"/></shape>')
    img = Renderer.open(p).frame_rgb(0.0)
    for y, x in ((50, 100), (100, 150), (150, 100), (100, 50)):
        assert img[y, x].max() > 200
    assert img[30, 30].max() == 0


# ---------------------------------------------------------------- deform
def deform_setup(tmp_path, mods: str, w=100, h=100):
    body = f'<shape id="s" shape="rect" x="50" y="50" width="{w}" height="{h}" fill="#FFFFFFFF"><deform>{mods}</deform></shape>'
    r = Renderer.open(scene(tmp_path, body))
    el = r.doc.ids["s"]
    M = r.rc.world_matrix(el, C0)
    return r, el, M


def square_tile(r, el, M):
    from scenerender.nodes.core import render_shape
    return render_shape(r.rc, el, C0, M, (100.0, 100.0))


@pytest.mark.parametrize("typ,extra", [("bend", 'amount="40"'), ("twist", 'amount="60"'), ("wave", 'amount="8" axis="x"'),
                                       ("squash", 'amount="20"'), ("stretch", 'amount="20" axis="x"'),
                                       ("corner-pin", 'corners="10 0 90 10 100 100 0 90"')])
def test_exact_inverses(tmp_path, typ, extra):
    r, el, M = deform_setup(tmp_path, f'<modifier type="{typ}" {extra}/>')
    m = el.find("deform").find("modifier")
    f, g = DEFORMERS.get(typ)(r.rc, m, C0, 100, 100, el)
    x, y = np.meshgrid(np.linspace(0, 100, 11), np.linspace(0, 100, 11))
    fx, fy = f(x, y)
    bx, by = g(fx, fy)
    assert np.allclose(bx, x, atol=1e-6) and np.allclose(by, y, atol=1e-6)
    assert not (np.allclose(fx, x) and np.allclose(fy, y))


def test_newton_inverse_for_forward_only_maps(tmp_path):
    r, el, M = deform_setup(tmp_path, '<modifier type="bulge" amount="40"/><modifier type="turbulence" amount="3" seed="2"/>')
    maps = deform.build_maps(r.rc, el, C0, (100, 100), M)
    assert len(maps) == 2 and all(g is None for _, g in maps)
    qx, qy = np.meshgrid(np.linspace(5, 95, 10), np.linspace(5, 95, 10))
    sx, sy = deform.invert(maps, qx, qy)
    fx, fy = deform._compose(maps)(sx, sy)
    assert np.allclose(fx, qx, atol=1e-2) and np.allclose(fy, qy, atol=1e-2)


def test_apply_deform_wave_moves_pixels(tmp_path):
    r, el, M = deform_setup(tmp_path, '<modifier type="wave" amount="10" frequency="0.5" phase="90" axis="x"/>')
    buf = square_tile(r, el, M)
    out = deform.apply_deform(r.rc, el, buf, C0, M, (100.0, 100.0))
    # frequency 0.5, phase 90 deg: left edge displaced +10 px (down), right edge -10 px (up)
    col_l = out.region((51, 30, 52, 170))[:, 0, 3]
    col_r = out.region((148, 30, 149, 170))[:, 0, 3]
    top_l = 30 + int(np.argmax(col_l > 0.5))
    top_r = 30 + int(np.argmax(col_r > 0.5))
    assert top_l == pytest.approx(60, abs=1.5) and top_r == pytest.approx(41, abs=2.5)
    assert out.px[..., 3].sum() == pytest.approx(buf.px[..., 3].sum(), rel=0.02)


def test_corner_pin_and_identity(tmp_path):
    r, el, M = deform_setup(tmp_path, '<modifier type="corner-pin" corners="0 0 50 0 50 50 0 50"/>')
    buf = square_tile(r, el, M)
    out = deform.apply_deform(r.rc, el, buf, C0, M, (100.0, 100.0))
    assert out.px[..., 3].sum() == pytest.approx(2500, rel=0.03)
    r2, el2, M2 = deform_setup(tmp_path, '<modifier type="wave" amount="0"/>')
    b2 = square_tile(r2, el2, M2)
    assert deform.apply_deform(r2.rc, el2, b2, C0, M2, (100.0, 100.0)) is b2


def test_mesh_warp_and_puppet(tmp_path):
    r, el, M = deform_setup(tmp_path, '<modifier type="mesh-warp" rows="2" cols="2">'
                                      '<point row="0" col="0" x="10" y="0"/><point row="0" col="1" x="10" y="0"/></modifier>')
    f, _ = deform.build_maps(r.rc, el, C0, (100, 100), M)[0]
    assert f(np.array([0.0, 50.0]), np.array([0.0, 100.0]))[0].tolist() == pytest.approx([10, 50])
    # one moved pin (plus starch, which stiffens but does not hold): ARAP translates rigidly
    r, el, M = deform_setup(tmp_path, '<modifier type="puppet"><pin restX="50" restY="0" x="0" y="-20"/>'
                                      '<pin kind="starch" restX="50" restY="100"/></modifier>')
    f, _ = deform.build_maps(r.rc, el, C0, (100, 100), M)[0]
    x, y = f(np.array([50.0, 50.0]), np.array([0.0, 100.0]))
    assert y[0] == pytest.approx(-20, abs=0.1) and y[1] == pytest.approx(80, abs=0.1)
    # a second (held) position pin anchors the bottom
    r, el, M = deform_setup(tmp_path, '<modifier type="puppet"><pin restX="50" restY="0" x="0" y="-20"/>'
                                      '<pin restX="50" restY="100"/></modifier>')
    f, _ = deform.build_maps(r.rc, el, C0, (100, 100), M)[0]
    x, y = f(np.array([50.0, 50.0]), np.array([0.0, 100.0]))
    assert y[0] == pytest.approx(-20, abs=0.1) and y[1] == pytest.approx(100, abs=0.1)


def test_skin_follows_bones(tmp_path):
    body = """<skeleton id="sk">
  <bone id="b0" x="50" y="100" length="50"/>
  <bone id="b1" parent="b0" x="50" y="0" length="50">
    <animate property="rotation"><key time="0" value="0"/><key time="1" value="90"/></animate>
  </bone>
</skeleton>
<shape id="s" shape="rect" x="50" y="95" width="100" height="10" fill="#FFFFFFFF">
  <deform><modifier type="skin" skeleton="sk"/></deform>
</shape>"""
    r = Renderer.open(scene(tmp_path, body))
    el = r.doc.ids["s"]
    M = r.rc.world_matrix(el, C0)
    assert deform.build_maps(r.rc, el, C0, (100, 10), M) == []        # rest pose: nothing to do
    c1 = Ctx(t=1.0, comp_t=1.0)
    f, _ = deform.build_maps(r.rc, el, c1, (100, 10), M)[0]
    x, y = f(np.array([0.0, 100.0]), np.array([5.0, 5.0]))
    # root end stays, tip (local 100,5 = world 150,100) swings 90 deg around the elbow (100,100)
    assert (x[0], y[0]) == pytest.approx((0, 5), abs=1.0)
    assert (x[1] + 50, y[1] + 95) == pytest.approx((100, 150), abs=3.0)


def test_deform_hook_installed(tmp_path):
    r, el, M = deform_setup(tmp_path, '<modifier type="wave" amount="3"/>')
    assert r.rc.hooks.get("deform") is deform.apply_deform
