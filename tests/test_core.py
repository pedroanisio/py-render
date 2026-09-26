"""Core engine tests: schema typing, document preparation, animation, evaluation, geometry, compositing."""
from __future__ import annotations

import math
import textwrap

import numpy as np
import pytest

from scenerender import anim, curves, geometry
from scenerender.document import load
from scenerender.evaluator import Ctx, Evaluator
from scenerender.render import Renderer
from scenerender.schema import default_schema


def scene(tmp_path, body: str, *, head: str = "", w=100, h=100, dur=4, extra_root="", name="s.xml") -> str:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="{w}" height="{h}" fps="10" duration="{dur}" background="#000000FF" linearLight="false"/>
  {head}
  <composition>
{textwrap.indent(body, '    ')}
  </composition>
  {extra_root}
</scene>"""
    p = tmp_path / name
    p.write_text(xml)
    return str(p)


def px(r: Renderer, t: float, x: int, y: int):
    return tuple(int(v) for v in r.frame_rgb(t)[y, x])


# ---------------------------------------------------------------- schema
def test_schema_defaults_and_kinds():
    s = default_schema()
    shape = s.type_of_path(("scene", "composition", "group", "shape"))
    assert shape.attrs["fill"].default == "#FFFFFFFF"
    assert s.base_kind(shape.attrs["x"].type) == "length"
    assert s.base_kind(shape.attrs["fill"].type) == "paint"
    assert s.base_kind(shape.attrs["opacity"].type) == "number"
    key = s.type_of_path(("scene", "composition", "shape", "animate", "key"))
    assert "interpolation" in key.attrs


# ---------------------------------------------------------------- document
def test_params_variant_bind_and_templates(tmp_path):
    head = """<parameters>
      <param id="title" type="string" default="Hello"/>
      <param id="col" type="color" default="#FF0000FF"/>
      <bind param="col" target="box" property="fill"/>
      <variant id="v2"><set param="title" value="World"/><override target="box" property="width" value="30"/></variant>
    </parameters>
    <assets><text id="t" text="Say {{title}}" width="50" height="20" size="10"/></assets>"""
    p = scene(tmp_path, '<shape id="box" shape="rect" width="10" height="10"/>', head=head)
    d = load(p)
    assert d.ids["box"].get("fill") == "#FF0000FF"
    assert d.ids["t"].get("text") == "Say Hello"
    d2 = load(p, variant="v2", params={"col": "#00FF00FF"})
    assert d2.ids["t"].get("text") == "Say World"
    assert d2.ids["box"].get("width") == "30"
    assert d2.ids["box"].get("fill") == "#00FF00FF"


def test_repeat_expands_with_steps(tmp_path):
    body = """<repeat id="r" count="3" offsetX="20" rotationStep="10" opacityStep="0.25" timeStep="0.5">
      <shape id="dot" shape="ellipse" width="5" height="5"/>
    </repeat>"""
    d = load(scene(tmp_path, body))
    copies = [d.ids[f"r#{i}"] for i in range(3)]
    assert [float(c.get("x")) for c in copies] == [0, 20, 40]
    assert float(copies[2].get("opacity")) == pytest.approx(0.5)
    assert float(copies[1].get("timeOffset")) == pytest.approx(0.5)
    assert "dot#2" in d.ids


def test_sequence_schedules_children(tmp_path):
    body = """<sequence id="seq" start="1" timeGap="0.5">
      <group id="a" start="0" end="2"/>
      <group id="b" start="0" end="1"/>
      <group id="c" start="0.25" end="1"/>
    </sequence>"""
    d = load(scene(tmp_path, body, dur=10))
    assert d.window(d.ids["a"]) == (1.0, 3.0)
    assert d.window(d.ids["b"]) == (3.5, 4.5)
    assert d.window(d.ids["c"]) == (5.25, 6.0)  # duration = authored end - start
    assert d.clock_shift[d.ids["b"]] == pytest.approx(3.5)


def test_beat_grid_markers(tmp_path):
    body = '<shape id="s" shape="rect" width="1" height="1"/>'
    d = load(scene(tmp_path, body, dur=4, head='<markers><beatGrid bpm="120" offset="0.5" beatsPerBar="4"/></markers>'))
    assert d.markers["beat.1"] == pytest.approx(0.5)
    assert d.markers["beat.3"] == pytest.approx(1.5)
    assert d.markers["bar.2"] == pytest.approx(2.5)


def test_lenient_load_of_invalid_doc(tmp_path):
    p = scene(tmp_path, '<shape id="s" shape="rect" width="1" height="1" bogus="1"/>')
    d = load(p)
    assert d.validation_errors
    with pytest.raises(Exception):
        load(p, strict=True)


# ---------------------------------------------------------------- animation
class _K:
    def __init__(self, **a):
        self.a = a

    def get(self, k, d=None):
        return self.a.get(k, d)


def keys(*pairs, interp=None, **kw):
    return [anim.Key(t, float(v), str(v), interp, _K(**kw)) for t, v in pairs]


def test_linear_hold_steps_and_extrapolation():
    k = keys((0, 0), (1, 10))
    assert anim.sample(k, 0.5) == pytest.approx(5)
    assert anim.sample(k, -1) == 0 and anim.sample(k, 2) == 10
    assert anim.sample(keys((0, 0), (1, 10), interp="hold"), 0.99) == 0
    assert anim.sample(keys((0, 0), (1, 10), interp="steps", steps=4), 0.6) == pytest.approx(5)
    assert anim.sample(k, 1.25, after="loop") == pytest.approx(2.5)
    assert anim.sample(k, 1.25, after="ping-pong") == pytest.approx(7.5)
    assert anim.sample(k, 1.25, after="offset") == pytest.approx(12.5)
    assert anim.sample(k, 2, after="linear") == pytest.approx(20)


def test_curves_endpoints_and_shape():
    for name in curves.EASES:
        f = curves.get(name)
        assert f(0) == pytest.approx(0, abs=1e-6), name
        assert f(1) == pytest.approx(1, abs=1e-6), name
    assert curves.get("ease-in")(0.5) < 0.5 < curves.get("ease-out")(0.5)
    assert curves.get("back-out")(0.8) > 1
    bz = curves.cubic_bezier(0.25, 0.1, 0.25, 1.0)
    assert bz(0.5) == pytest.approx(0.8024, abs=2e-3)


def test_catmull_rom_passes_through_keys():
    k = keys((0, 0), (1, 10), (2, 0), (3, 10), interp="catmull-rom")
    for t, v in ((0, 0), (1, 10), (2, 0), (3, 10)):
        assert anim.sample(k, t) == pytest.approx(v)
    k2 = keys((0, 0), (1, 10), (2, 10), interp="catmull-rom")
    assert anim.sample(k2, 0.5) == pytest.approx(5.625)


def test_timebase_marker_additive_and_colors(tmp_path):
    body = """<shape id="s" shape="rect" width="10" height="10" x="5" start="1" end="3" fill="#000000FF">
      <animate property="x" timeBase="local"><key time="0" value="0"/><key time="1" value="100"/></animate>
      <animate property="x" additive="true"><key time="0" value="1"/></animate>
      <animate property="y" timeBase="normalized"><key time="0" value="0"/><key time="1" value="10"/></animate>
      <animate property="rotation"><key time="0" value="0" marker="m"/><key time="1" value="90" marker="m"/></animate>
      <animate property="fill"><key time="1" value="#000000FF"/><key time="3" value="#FFFFFFFF"/></animate>
    </shape>"""
    d = load(scene(tmp_path, body, head='<markers><marker id="m" time="2"/></markers>'))
    ev = Evaluator(d)
    el = d.ids["s"]
    ctx = Ctx(t=1.5, comp_t=1.5, node_start=1, node_end=3)
    assert ev.num(el, "x", ctx) == pytest.approx(51)
    assert ev.num(el, "y", ctx) == pytest.approx(2.5)
    assert ev.num(el, "rotation", ctx.at(2.5)) == pytest.approx(45)
    c = ev.color(el, "fill", ctx.at(2))
    assert c[0] == pytest.approx(0.5, abs=1e-6)


def test_link_expression_and_scale_alias(tmp_path):
    body = """<shape id="a" shape="rect" width="1" height="1" x="3">
      <animate property="x"><key time="0" value="0"/><key time="4" value="40"/></animate>
    </shape>
    <shape id="b" shape="rect" width="1" height="1">
      <link property="x" source="a.x" scale="2" offset="1" delay="1"/>
      <expression property="y">time * 10 + index</expression>
      <animate property="scale"><key time="0" value="2,3"/></animate>
    </shape>"""
    d = load(scene(tmp_path, body))
    ev = Evaluator(d)
    b = d.ids["b"]
    ctx = Ctx(t=2, comp_t=2)
    assert ev.num(b, "x", ctx) == pytest.approx(21)  # a.x at t=1 is 10 → 10*2+1
    assert ev.num(b, "y", ctx) == pytest.approx(20)
    assert ev.num(b, "scaleX", ctx) == 2 and ev.num(b, "scaleY", ctx) == 3


# ---------------------------------------------------------------- geometry
def test_svg_path_and_trim_and_sampler():
    cmds = geometry.parse_svg_path("M0 0 L100 0 A50 50 0 0 1 100 100 Z")
    x0, y0, x1, y1 = geometry.bounds(cmds)
    assert x1 == pytest.approx(150, abs=0.5)
    line = geometry.parse_svg_path("M0 0 H100")
    half = geometry.trim(line, 0, 0.5)
    assert geometry.bounds(half)[2] == pytest.approx(50, abs=0.5)
    s = geometry.PathSampler("M0 0 L100 0 L100 100")
    x, y, ang = s.at(0.75)
    assert (x, y) == pytest.approx((100, 50), abs=0.5) and ang == pytest.approx(90)


def test_shape_kinds_fill_their_box():
    for kind in ("rect", "rounded-rect", "ellipse", "polygon", "star"):
        b = geometry.bounds(geometry.shape_commands(kind, 40, 20, {"radius": 4}))
        assert b[0] >= -0.5 and b[2] <= 40.5 and b[3] <= 20.5, kind


# ---------------------------------------------------------------- compositing
def test_shape_position_anchor_rotation(tmp_path):
    body = '<shape id="s" shape="rect" x="50" y="50" width="20" height="10" anchorX="10" anchorY="5" rotation="90" fill="#FF0000FF"/>'
    r = Renderer.open(scene(tmp_path, body))
    assert px(r, 0, 50, 58) == (255, 0, 0)   # rotated: now 10 wide, 20 tall around (50,50)
    assert px(r, 0, 58, 50) == (0, 0, 0)


def test_stacking_z_and_group_opacity(tmp_path):
    body = """<shape id="top" shape="rect" width="50" height="50" z="1" fill="#0000FFFF"/>
    <shape id="bottom" shape="rect" width="100" height="100" fill="#FF0000FF"/>
    <group id="g" opacity="0.5" x="60" y="60">
      <shape id="g1" shape="rect" width="20" height="20" fill="#FFFFFFFF"/>
      <shape id="g2" shape="rect" width="20" height="20" fill="#FFFFFFFF"/>
    </group>"""
    r = Renderer.open(scene(tmp_path, body))
    assert px(r, 0, 10, 10) == (0, 0, 255)
    assert px(r, 0, 80, 20) == (255, 0, 0)
    # isolated group: two overlapping white rects at 50% read as one layer at 50%
    assert px(r, 0, 70, 70) == pytest.approx((255, 128, 128), abs=2)


def test_window_mask_and_matte(tmp_path):
    body = """<shape id="late" shape="rect" width="100" height="100" start="1" end="2" fill="#00FF00FF"/>
    <shape id="masked" shape="rect" width="100" height="100" fill="#FFFFFFFF" z="-1">
      <mask type="rect" x="0" y="0" width="50" height="100"/>
    </shape>
    <shape id="mt" shape="rect" width="10" height="10" x="80" y="80"/>
    <shape id="gated" shape="rect" width="100" height="100" fill="#FF0000FF" z="2" start="3" matte="mt"/>"""
    r = Renderer.open(scene(tmp_path, body))
    assert px(r, 0, 25, 50) == (255, 255, 255) and px(r, 0, 75, 50) == (0, 0, 0)
    assert px(r, 1.5, 75, 50) == (0, 255, 0)
    assert px(r, 2.0, 75, 50) == (0, 0, 0)                   # end is exclusive
    assert px(r, 3.0, 85, 85) == (255, 0, 0) and px(r, 3.0, 75, 50) == (0, 0, 0)


def test_instance_clock_and_overrides(tmp_path):
    head = """<symbols><symbol id="sym" width="100" height="100" duration="2">
      <shape id="inner" shape="rect" width="10" height="10" fill="#FFFFFFFF">
        <animate property="x"><key time="0" value="0"/><key time="1" value="50"/></animate>
      </shape>
    </symbol></symbols>"""
    body = """<instance id="i1" symbol="sym" start="1" speed="2" y="0"/>
    <instance id="i2" symbol="sym" start="0" y="50"><override target="inner" property="fill" value="#FF0000FF"/></instance>"""
    r = Renderer.open(scene(tmp_path, body, head=head))
    # i1 at t=1.25: local = 0.5 → x = 25; i2 at t=0.5 → x=25, red
    assert px(r, 1.25, 27, 5) == (255, 255, 255)
    assert px(r, 0.5, 27, 55) == (255, 0, 0)
    assert px(r, 3.5, 5, 55) == (0, 0, 0)  # symbol duration elapsed


def test_group_time_scale(tmp_path):
    body = """<group id="g" start="0" timeScale="2">
      <shape id="s" shape="rect" width="10" height="10">
        <animate property="x"><key time="0" value="0"/><key time="2" value="80"/></animate>
      </shape>
    </group>"""
    r = Renderer.open(scene(tmp_path, body))
    assert px(r, 1.0, 85, 5) == (255, 255, 255)


def test_gradient_paint(tmp_path):
    body = '<shape id="s" shape="rect" width="100" height="100" fill="url(#g)"/>'
    paints = """<paints><linearGradient id="g" interpolationSpace="srgb">
      <stop offset="0" color="#000000FF"/><stop offset="1" color="#FFFFFFFF"/></linearGradient></paints>"""
    r = Renderer.open(scene(tmp_path, body, head=paints))
    left, mid, right = px(r, 0, 2, 50)[0], px(r, 0, 50, 50)[0], px(r, 0, 97, 50)[0]
    assert left < 12 and 115 < mid < 140 and right > 243
