"""transformConstraint (scenerender.constraints): node IK chains, track, space local/world, look-at, copy."""
from __future__ import annotations

import json
import math
import textwrap

import numpy as np
import pytest

from scenerender import constraints
from scenerender.evaluator import Ctx
from scenerender.render import Renderer

C0 = Ctx(t=0.0, comp_t=0.0)


def scene(tmp_path, body: str, tracking: str = "", name="c.xml", w=400, h=400) -> str:
    trk = f"<tracking>{tracking}</tracking>" if tracking else ""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="{w}" height="{h}" fps="10" duration="4" background="#000000FF" linearLight="false"/>
  {trk}
  <composition>
{textwrap.indent(body, '    ')}
  </composition>
</scene>"""
    p = tmp_path / name
    p.write_text(xml)
    return str(p)


def pivot(r, eid, ctx=C0):
    el = r.doc.ids[eid]
    M = r.rc.world_matrix(el, ctx)
    ax, ay = constraints._anchor(r.rc, el, ctx)
    return (M @ np.array([ax, ay, 1.0]))[:2] / r.rc.scale


ARM = """<shape id="goal" shape="rect" width="4" height="4" x="{gx}" y="{gy}"/>
<shape id="upper" shape="rect" width="100" height="10" x="100" y="200" anchorY="5" fill="#FFFFFFFF"/>
<shape id="lower" parent="upper" shape="rect" width="100" height="10" x="100" y="5" anchorY="5" fill="#FFFFFFFF"/>
<shape id="hand" parent="lower" shape="rect" width="10" height="10" x="100" y="5" anchorY="5" fill="#FFFFFFFF">
  <transformConstraint type="ik" target="goal"{extra}/>
</shape>"""


def test_two_bone_ik_reaches_reachable_target(tmp_path):
    r = Renderer.open(scene(tmp_path, ARM.format(gx=220, gy=120, extra="")))
    assert r.rc.hooks.get("physics") is not None
    hand = pivot(r, "hand")
    assert hand == pytest.approx((220, 120), abs=0.3)
    # bone lengths preserved
    assert np.hypot(*(pivot(r, "lower") - pivot(r, "upper"))) == pytest.approx(100, abs=1e-6)
    assert np.hypot(*(hand - pivot(r, "lower"))) == pytest.approx(100, abs=1e-6)


def test_bend_positive_flips_elbow(tmp_path):
    a = Renderer.open(scene(tmp_path, ARM.format(gx=250, gy=200, extra=""), name="a.xml"))
    b = Renderer.open(scene(tmp_path, ARM.format(gx=250, gy=200, extra=' bendPositive="false"'), name="b.xml"))
    ea, eb = pivot(a, "lower"), pivot(b, "lower")
    assert pivot(a, "hand") == pytest.approx((250, 200), abs=0.3) and pivot(b, "hand") == pytest.approx((250, 200), abs=0.3)
    assert (ea[1] - 200) * (eb[1] - 200) < 0 and abs(ea[1] - 200) > 10       # mirrored elbows


def test_unreachable_target_straightens_toward_it(tmp_path):
    r = Renderer.open(scene(tmp_path, ARM.format(gx=100, gy=-300, extra="")))
    u, l, hnd = pivot(r, "upper"), pivot(r, "lower"), pivot(r, "hand")
    d = np.array([0.0, -1.0])
    assert (l - u) / 100 == pytest.approx(d, abs=1e-3) and (hnd - l) / 100 == pytest.approx(d, abs=1e-3)


def test_fabrik_chain_through_parent_links(tmp_path):
    body = ARM.format(gx=230, gy=90, extra="").replace(
        '<shape id="hand" parent="lower"',
        '<shape id="fore" parent="lower" shape="rect" width="60" height="10" x="100" y="5" anchorY="5"/>\n'
        '<shape id="hand" parent="fore"').replace('id="hand" parent="fore" shape="rect" width="10" height="10" x="100"',
                                                  'id="hand" parent="fore" shape="rect" width="10" height="10" x="60"')
    r = Renderer.open(scene(tmp_path, body))
    assert pivot(r, "hand") == pytest.approx((230, 90), abs=0.5)
    assert np.hypot(*(pivot(r, "fore") - pivot(r, "lower"))) == pytest.approx(100, abs=1e-6)
    assert np.hypot(*(pivot(r, "hand") - pivot(r, "fore"))) == pytest.approx(60, abs=1e-6)


def test_ik_influence_blends(tmp_path):
    r = Renderer.open(scene(tmp_path, ARM.format(gx=220, gy=120, extra=' influence="0"')))
    assert pivot(r, "hand") == pytest.approx((300, 200), abs=1e-6)          # straight, unconstrained


def test_look_at_and_copy_position_world_and_local(tmp_path):
    body = """<shape id="t" shape="rect" width="10" height="10" x="300" y="100" anchorX="5" anchorY="5"/>
<group id="g" x="50" y="50" width="400" height="400">
  <shape id="t2" shape="rect" width="10" height="10" x="20" y="30"/>
  <shape id="a" shape="rect" width="40" height="10" x="100" y="100" anchorY="5">
    <transformConstraint type="look-at" target="t"/>
  </shape>
  <shape id="cw" shape="rect" width="10" height="10" x="0" y="0"><transformConstraint type="copy-position" target="t"/></shape>
  <shape id="cl" shape="rect" width="10" height="10" x="0" y="0"><transformConstraint type="copy-position" target="t2" space="local" offsetX="5"/></shape>
</group>"""
    r = Renderer.open(scene(tmp_path, body))
    M = r.rc.world_matrix(r.doc.ids["a"], C0)
    ang = math.degrees(math.atan2(M[1, 0], M[0, 0]))
    assert ang == pytest.approx(math.degrees(math.atan2(100 - 150, 300 - 150)), abs=1e-6)
    assert pivot(r, "cw") == pytest.approx((300, 100), abs=1e-6)            # onto t's anchor point (world)
    # local: t2's parent-space position (20, 30) + offset in this node's parent space (the group at 50, 50)
    assert pivot(r, "cl") == pytest.approx((50 + 25, 50 + 30), abs=1e-6)


def test_follow_path_local_space(tmp_path):
    body = """<group id="g" x="100" y="100" width="200" height="200">
  <shape id="f" shape="rect" width="10" height="10">
    <transformConstraint type="follow-path" path="M0 0 L100 0 M0 50 L100 50" progress="0.75" space="local" autoOrient="true"/>
  </shape>
</group>"""
    r = Renderer.open(scene(tmp_path, body))
    assert pivot(r, "f") == pytest.approx((150, 150), abs=1e-6)             # second subpath: no bridge


def test_track_point_json_with_time_offset(tmp_path):
    data = {"version": 1, "tracks": {"tower": {"keys": [{"t": 0, "x": 100, "y": 100, "rotation": 0},
                                                        {"t": 2, "x": 300, "y": 200, "rotation": 20}]}}}
    (tmp_path / "t.json").write_text(json.dumps(data))
    body = """<shape id="n" shape="rect" width="20" height="20" anchorX="10" anchorY="10">
  <transformConstraint type="track" target="td" point="tower" offsetX="5"/>
</shape>"""
    r = Renderer.open(scene(tmp_path, body, '<trackData id="td" src="t.json" kind="point" timeOffset="1"/>'))
    c = Ctx(t=2.0, comp_t=2.0)                          # track time 1: halfway
    assert pivot(r, "n", c) == pytest.approx((205, 150), abs=1e-6)
    M = r.rc.world_matrix(r.doc.ids["n"], c)
    assert math.degrees(math.atan2(M[1, 0], M[0, 0])) == pytest.approx(10, abs=1e-6)
    assert pivot(r, "n", Ctx(t=0.0, comp_t=0.0)) == pytest.approx((105, 100), abs=1e-6)   # held before


def test_track_planar_corner_pins_the_node(tmp_path):
    corners = [[100, 100], [300, 120], [280, 300], [90, 260]]
    data = {"tracks": {"screen": {"keys": [{"t": 0, "corners": corners}]}}}
    (tmp_path / "p.json").write_text(json.dumps(data))
    body = """<shape id="n" shape="rect" width="160" height="90" fill="#FFFFFFFF">
  <transformConstraint type="track" target="td" point="screen"/>
</shape>"""
    r = Renderer.open(scene(tmp_path, body, '<trackData id="td" src="p.json" kind="planar"/>'))
    H = r.rc.world_matrix(r.doc.ids["n"], C0)
    for (u, v), (x, y) in zip([(0, 0), (160, 0), (160, 90), (0, 90)], corners):
        q = H @ np.array([u, v, 1.0])
        assert (q[:2] / q[2] / r.rc.scale) == pytest.approx((x, y), abs=1e-6)
    img = r.frame_rgba(0.0)
    assert img[200, 200, 3] > 200 and img[50, 50, 3] == 0              # drawn inside the quad only
