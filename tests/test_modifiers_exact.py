"""Exact shape booleans/offsets (shapely), sequential trim, the mesh resampler, ARAP puppet and skinning."""
from __future__ import annotations

import json
import math
import textwrap

import numpy as np
import pytest
from lxml import etree

from scenerender import deform, geometry, modifiers
from scenerender.evaluator import Ctx
from scenerender.raster import Buf
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


def setup(tmp_path, mod: str):
    r = Renderer.open(scene(tmp_path, f'<shape id="s" shape="rect" width="100" height="50" fill="#FFFFFFFF">{mod}</shape>'))
    s = r.doc.ids["s"]
    return r, s, next(c for c in s if etree.QName(c).localname == "shapeModifier")


def area(cmds) -> float:
    g = modifiers.region([cmds])
    return 0.0 if g is None else g.area


# ---------------------------------------------------------------- offset-path
@pytest.mark.parametrize("mode,d,expect", [
    ("miter", 10, 120 * 70),
    ("round", 10, 100 * 50 + 2 * 10 * (100 + 50) + math.pi * 100),
    ("bevel", 10, 120 * 70 - 4 * 50),
    ("miter", -10, 80 * 30),
    ("round", -30, 0.0),                      # the rectangle vanishes: nothing left
])
def test_offset_path_area_vs_analytic(tmp_path, mode, d, expect):
    r, s, m = setup(tmp_path, f'<shapeModifier type="offset-path" amount="{d}" mode="{mode}"/>')
    out = modifiers._offset_path(r.rc, m, geometry.rect(0, 0, 100, 50), C0)[0]
    assert area(out) == pytest.approx(expect, rel=2e-3, abs=1.0)


def test_offset_circle_radius_exact(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="offset-path" amount="15" mode="round"/>')
    out = modifiers._offset_path(r.rc, m, geometry.ellipse(0, 0, 40, 40), C0)[0]
    assert area(out) == pytest.approx(math.pi * 55 ** 2, rel=2e-3)


def test_offset_open_path_parallel_curve(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="offset-path" amount="5"/>')
    out = modifiers._offset_path(r.rc, m, [("M", 0.0, 0.0), ("L", 100.0, 0.0)], C0)[0]
    ys = {round(c[2], 6) for c in out if c[0] in "ML"}
    assert ys == {5.0}                          # right of travel on screen (+y down)


# ---------------------------------------------------------------- merge
def test_merge_exact_areas_and_three_operands(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="merge" mode="add"/>')
    a, b = geometry.ellipse(0, 0, 50, 50), geometry.ellipse(50, 0, 50, 50)
    # lens area of two unit-radius-50 circles at distance 50
    R, dd = 50.0, 50.0
    lens = 2 * R * R * math.acos(dd / (2 * R)) - dd / 2 * math.sqrt(4 * R * R - dd * dd)
    A = math.pi * R * R
    for mode, expect in (("add", 2 * A - lens), ("intersect", lens), ("subtract", A - lens), ("exclude", 2 * A - 2 * lens)):
        m.set("mode", mode)
        out = modifiers.merge_paths(r.rc, m, [a, b], C0)[0]
        assert area(out) == pytest.approx(expect, rel=2e-3), mode
    m.set("mode", "subtract")
    c = geometry.rect(-10, -5, 120, 10)
    out = modifiers.merge_paths(r.rc, m, [geometry.rect(-60, -60, 220, 120), a, c], C0)[0]
    union_ac = modifiers.region([a]).union(modifiers.region([c])).area
    assert area(out) == pytest.approx(220 * 120 - union_ac, rel=1e-3)


def test_region_fill_rules(tmp_path):
    # two overlapping same-direction squares: nonzero keeps the overlap, evenodd drops it
    sq = geometry.rect(0, 0, 100, 100) + geometry.rect(50, 50, 100, 100)
    assert modifiers.region([sq], "nonzero").area == pytest.approx(17500)
    assert modifiers.region([sq], "evenodd").area == pytest.approx(15000)


# ---------------------------------------------------------------- trim
def test_trim_sequential_across_paths_with_wrap_and_opacity(tmp_path):
    r, s, m = setup(tmp_path, '<shapeModifier type="trim" amount="50" offset="270"/>')
    a = modifiers._with_opacity([("M", 0.0, 0.0), ("L", 100.0, 0.0)], 0.5)
    b = [("M", 0.0, 10.0), ("L", 100.0, 10.0)]
    out = modifiers.trim_paths(r.rc, m, [a, b], C0)
    # window [0.75, 1.25] of 200 px: the last half of b and the first half of a
    assert len(out) == 2 and getattr(out[0], "opacity", 1.0) == 0.5
    xa = sorted(c[1] for c in out[0] if c[0] in "ML")
    xb = sorted(c[1] for c in out[1] if c[0] in "ML")
    assert (xa[0], xa[-1]) == pytest.approx((0, 50)) and (xb[0], xb[-1]) == pytest.approx((50, 100))
    m.set("amount", "100")
    assert modifiers.trim_paths(r.rc, m, [a, b], C0) == []


# ---------------------------------------------------------------- mesh resampler
def test_mesh_map_inverse_is_exact():
    rest = deform.grid_rest(100, 60, 5, 3)
    rng = np.random.default_rng(3)
    D = rest + rng.normal(0, 3, rest.shape)
    f, g = deform.mesh_map(rest, D)
    x, y = rng.uniform(1, 99, 500), rng.uniform(1, 59, 500)
    fx, fy = f(x, y)
    bx, by = g(fx, fy)
    assert np.allclose(bx, x, atol=1e-7) and np.allclose(by, y, atol=1e-7)
    # outside the mesh the border triangles extrapolate (continuous spill)
    ox, oy = g(np.array([-20.0]), np.array([30.0]))
    assert ox[0] < 0


# ---------------------------------------------------------------- puppet ARAP
def deform_setup(tmp_path, modifier: str):
    r = Renderer.open(scene(tmp_path, f'<shape id="s" shape="rect" x="50" y="50" width="100" height="100" fill="#FFFFFFFF">'
                                      f'<deform>{modifier}</deform></shape>'))
    el = r.doc.ids["s"]
    return r, el, r.rc.world_matrix(el, C0)


def _edge_strain(f, w, h, n=11):
    gx, gy = np.meshgrid(np.linspace(0, w, n), np.linspace(0, h, n))
    x, y = f(gx, gy)
    dx0, dy0 = w / (n - 1), h / (n - 1)
    sh = np.hypot(np.diff(x, axis=1), np.diff(y, axis=1)) / dx0
    sv = np.hypot(np.diff(x, axis=0), np.diff(y, axis=0)) / dy0
    return sh, sv


def test_arap_rotates_rigidly_away_from_pins(tmp_path):
    # two pins moved as a rigid 30 deg rotation about the centre: ARAP reproduces the rotation
    c, s_ = math.cos(math.radians(30)), math.sin(math.radians(30))
    rot = lambda x, y: (50 + (x - 50) * c - (y - 50) * s_, 50 + (x - 50) * s_ + (y - 50) * c)  # noqa: E731
    (ax, ay), (bx, by) = rot(10, 50), rot(90, 50)
    mod = (f'<modifier type="puppet"><pin restX="10" restY="50" x="{ax - 10}" y="{ay - 50}"/>'
           f'<pin restX="90" restY="50" x="{bx - 90}" y="{by - 50}"/></modifier>')
    r, el, M = deform_setup(tmp_path, mod)
    f, g = deform.build_maps(r.rc, el, C0, (100, 100), M)[0]
    x, y = f(np.array([50.0, 50.0, 0.0]), np.array([0.0, 100.0, 0.0]))
    ex = [rot(50, 0), rot(50, 100), rot(0, 0)]
    for k in range(3):
        assert (x[k], y[k]) == pytest.approx(ex[k], abs=0.5)
    sh, sv = _edge_strain(f, 100, 100)
    assert np.abs(sh - 1).max() < 0.02 and np.abs(sv - 1).max() < 0.02


def test_arap_bend_keeps_far_region_rigid(tmp_path):
    # one end held, the other dragged down: lengths stay close to rest (as-rigid-as-possible),
    # unlike a blend of displacements which would shear/stretch the far region
    mod = ('<modifier type="puppet"><pin restX="5" restY="50"/><pin restX="95" restY="50" y="40"/>'
           '<pin kind="starch" restX="50" restY="50" amount="2"/></modifier>')
    r, el, M = deform_setup(tmp_path, mod)
    f, _ = deform.build_maps(r.rc, el, C0, (100, 100), M)[0]
    x, y = f(np.array([95.0, 5.0]), np.array([50.0, 50.0]))
    assert (x[1], y[1]) == pytest.approx((5, 50), abs=0.05) and y[0] == pytest.approx(90, abs=0.05)
    sh, sv = _edge_strain(f, 100, 100)
    assert np.median(np.abs(sh - 1)) < 0.08 and np.median(np.abs(sv - 1)) < 0.08


def test_arap_bend_pin_rotates_neighbourhood(tmp_path):
    mod = '<modifier type="puppet"><pin kind="bend" restX="50" restY="50" rotation="40"/></modifier>'
    r, el, M = deform_setup(tmp_path, mod)
    f, _ = deform.build_maps(r.rc, el, C0, (100, 100), M)[0]
    x, y = f(np.array([50.0, 90.0]), np.array([50.0, 50.0]))
    assert (x[0], y[0]) == pytest.approx((50, 50), abs=0.05)
    ang = math.degrees(math.atan2(y[1] - y[0], x[1] - x[0]))
    assert ang == pytest.approx(40, abs=2.0)                   # clockwise on screen (+y down)


# ---------------------------------------------------------------- skin
SKEL = """<skeleton id="sk"{w}>
  <bone id="b0" x="50" y="100" length="50"/>
  <bone id="b1" parent="b0" x="50" y="0" length="50">
    <animate property="rotation"><key time="0" value="0"/><key time="1" value="90"/></animate>
  </bone>
</skeleton>
<shape id="s" shape="rect" x="50" y="95" width="100" height="10" fill="#FFFFFFFF">
  <deform><modifier type="skin" skeleton="sk"/></deform>
</shape>"""


def test_skin_weights_file_grid(tmp_path):
    wts = {"format": "scenerender-skin-weights", "version": 1, "space": "node", "bones": ["b0", "b1"],
           "grid": {"cols": 3, "rows": 1}, "weights": [[1, 0], [1, 0], [0, 1]]}
    (tmp_path / "w.json").write_text(json.dumps(wts))
    r = Renderer.open(scene(tmp_path, SKEL.format(w=' weights="w.json"')))
    el = r.doc.ids["s"]
    M = r.rc.world_matrix(el, C0)
    c1 = Ctx(t=1.0, comp_t=1.0)
    f, _ = deform.build_maps(r.rc, el, c1, (100, 10), M)[0]
    x, y = f(np.array([50.0, 100.0]), np.array([5.0, 5.0]))
    assert (x[0] + 50, y[0] + 95) == pytest.approx((100, 100), abs=0.5)     # fully b0: fixed
    assert (x[1] + 50, y[1] + 95) == pytest.approx((100, 150), abs=0.5)     # fully b1: swung 90 deg
    # sparse per-vertex form, same result at the vertices
    wts2 = {"bones": ["b0", "b1"], "vertices": [[0, 5], [50, 5], [100, 5]],
            "weights": [{"b0": 1}, {"b0": 1}, {"b1": 1}]}
    (tmp_path / "w2.json").write_text(json.dumps(wts2))
    r = Renderer.open(scene(tmp_path, SKEL.format(w=' weights="w2.json"'), name="m2.xml"))
    el = r.doc.ids["s"]
    f, _ = deform.build_maps(r.rc, el, c1, (100, 10), r.rc.world_matrix(el, C0))[0]
    x, y = f(np.array([100.0]), np.array([5.0]))
    assert (x[0] + 50, y[0] + 95) == pytest.approx((100, 150), abs=0.5)


def test_skeleton_fabrik_chain_reaches_target(tmp_path):
    body = """<shape id="goal" shape="rect" width="4" height="4" x="160" y="60"/>
<skeleton id="sk">
  <bone id="a" x="40" y="100" length="50"/>
  <bone id="b" parent="a" x="50" y="0" length="50"/>
  <bone id="c" parent="b" x="50" y="0" length="50"/>
  <transformConstraint type="ik" target="goal" point="c"/>
</skeleton>"""
    r = Renderer.open(scene(tmp_path, body))
    pose = deform.skeleton_pose(r.rc, r.doc.ids["sk"], C0)
    tip = (pose[r.doc.ids["c"]] @ np.array([50, 0, 1.0]))[:2]
    assert tip == pytest.approx((160, 60), abs=0.5)
    for bid in "abc":                                           # bone lengths preserved
        Mb = pose[r.doc.ids[bid]]
        assert math.hypot(Mb[0, 0], Mb[1, 0]) == pytest.approx(1.0)


def test_apply_deform_puppet_moves_pixels(tmp_path):
    mod = '<modifier type="puppet"><pin restX="50" restY="50" x="30" y="0"/></modifier>'
    r, el, M = deform_setup(tmp_path, mod)
    px = np.zeros((100, 100, 4), np.float32)
    px[40:60, 40:60] = 1.0
    out = deform.apply_deform(r.rc, el, Buf(px, 50, 50), C0, M, (100.0, 100.0))
    a = out.region((0, 0, 200, 200))[..., 3]
    ys, xs = np.nonzero(a > 0.5)
    assert xs.mean() == pytest.approx(50 + 50 + 30, abs=1.0)
