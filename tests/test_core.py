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


# ---------------------------------------------------------------- perspective, motion blur, time-shifted renders
def test_projective_warp_matches_affine_when_affine():
    from scenerender.raster import Buf, warp_projective
    src = Buf(np.zeros((10, 10, 4), np.float32), 0, 0)
    src.px[2:8, 2:8] = 1.0
    H = np.array([[2.0, 0, 5], [0, 2.0, 5], [0, 0, 1]])
    out = warp_projective(src, H, (0, 0, 40, 40))
    full = out.region((0, 0, 40, 40))
    assert full[15, 15, 3] == pytest.approx(1.0) and full[3, 3, 3] == 0.0


def test_projective_node_path(tmp_path, monkeypatch):
    body = '<shape id="s" shape="rect" width="40" height="40" x="30" y="30" fill="#FFFFFFFF" threeD="true"/>'
    r = Renderer.open(scene(tmp_path, body))
    # A camera hook that returns a keystone (projective) matrix: the top edge shrinks.
    K = np.array([[1, 0, 0], [0, 1, 0], [0, 0.01, 1.0]])
    r.rc.hooks["camera"] = lambda rc, el, M, ctx: K @ M
    img = r.frame_rgb(0)
    assert img[33, 33, 0] == 255            # centre (50,50) projects to (33.3, 33.3)
    assert img[60, 60, 0] == 0              # the unwarped box would cover this; the projected one doesn't


def test_node_motion_blur_on_and_off(tmp_path):
    body = """<shape id="s" shape="rect" width="10" height="10" y="45" motionBlur="on" fill="#FFFFFFFF">
      <animate property="x"><key time="0" value="0"/><key time="1" value="100"/></animate>
    </shape>"""
    txt = open(scene(tmp_path, body)).read().replace('linearLight="false"', 'linearLight="false" motionBlurSamples="8"')
    p = tmp_path / "mb.xml"
    p.write_text(txt)
    r = Renderer.open(str(p))
    row = r.frame_rgb(0.5)[50, :, 0]
    assert 0 < row[48] < 255 and (row > 0).sum() > 12        # smeared along x
    p.write_text(txt.replace('motionBlur="on"', 'motionBlur="off"').replace('linearLight="false"', 'linearLight="false" motionBlur="true"'))
    r2 = Renderer.open(str(p))
    row2 = r2.frame_rgb(0.5)[50, :, 0]
    assert (row2 == 255).sum() == 10                          # excluded from the shutter: sharp


def test_render_node_at_other_time(tmp_path):
    body = """<shape id="s" shape="rect" width="10" height="10" fill="#FFFFFFFF">
      <animate property="x"><key time="0" value="0"/><key time="1" value="80"/></animate>
    </shape>"""
    r = Renderer.open(scene(tmp_path, body))
    out = r.rc.render_node_at(r.doc.ids["s"], 1.0, Ctx(t=0, comp_t=0))
    assert out.buf.x0 >= 75


def test_echo_tail_outlives_window_and_opacity_folds(tmp_path):
    body = """<shape id="s" shape="rect" width="10" height="10" y="45" end="1" opacity="0.5" effects="fx-echo" fill="#FFFFFFFF">
      <animate property="x"><key time="0" value="0"/><key time="1" value="90"/></animate>
    </shape>"""
    tail = '<effects><effect id="fx-echo" type="echo" samples="5" frequency="10" amount="0.1"/></effects>'
    p = scene(tmp_path, body)
    txt = open(p).read().replace("</composition>", "</composition>\n  " + tail)
    q = tmp_path / "echo.xml"
    q.write_text(txt)
    r = Renderer.open(str(q))
    during = r.frame_rgb(0.95)[50, :, 0]
    assert 128 in during and during.max() < 200             # 50 % opacity folded into every copy (overlaps stack)
    after = r.frame_rgb(1.15)[50, :, 0]
    assert after.max() > 0                                   # tail still visible after end="1"
    assert r.frame_rgb(1.6)[50, :, 0].max() == 0             # ...but only for (samples-1)/frequency


def test_working_primaries_round_trip(tmp_path):
    body = '<shape id="s" shape="rect" width="100" height="100" fill="#C03020FF"/>'
    head = '<colorManagement workingSpace="acescg"/>'
    p = scene(tmp_path, body, head=head)
    txt = open(p).read().replace('linearLight="false"', 'linearLight="true"')
    q = tmp_path / "wp.xml"
    q.write_text(txt)
    r = Renderer.open(str(q))
    assert r.rc.working_primaries not in (None, "srgb")
    finish = r.rc.hooks.pop("finish", None)
    buf = r.rc.render_frame(0).px[50, 50]          # the composite, before the output transform
    assert abs(buf[0] - 0.5271) > 0.01            # composited in ACEScg, not in linear sRGB
    if finish is not None:
        r.rc.hooks["finish"] = finish
    assert px(r, 0, 50, 50) == pytest.approx((0xC0, 0x30, 0x20), abs=2)   # and back to the same sRGB colour


def test_mesh_gradient_is_bicubic_and_hits_grid_colours(tmp_path):
    paints = """<paints><meshGradient id="m" rows="3" cols="3" interpolationSpace="srgb">
      <point row="0" col="0" color="#000000FF"/><point row="0" col="1" color="#000000FF"/><point row="0" col="2" color="#000000FF"/>
      <point row="1" col="0" color="#000000FF"/><point row="1" col="1" color="#FFFFFFFF"/><point row="1" col="2" color="#000000FF"/>
      <point row="2" col="0" color="#000000FF"/><point row="2" col="1" color="#000000FF"/><point row="2" col="2" color="#000000FF"/>
    </meshGradient></paints>"""
    body = '<shape id="s" shape="rect" width="100" height="100" fill="url(#m)"/>'
    r = Renderer.open(scene(tmp_path, body, head=paints))
    img = r.frame_rgb(0)
    assert img[50, 50, 0] > 245                          # passes through the centre point's colour
    row = img[50, 50:100, 0].astype(int)
    assert (np.diff(row) <= 1).all()                     # smooth falloff to the edge
    # bicubic (Catmull-Rom) is not the bilinear tent: a quarter of the way out it stays brighter
    assert img[50, 62, 0] > 0.75 * 255 * 1.02


def test_param_validation(tmp_path):
    from scenerender.document import SceneError
    head = """<parameters>
      <param id="title" type="string" default="Hi" maxLength="5" pattern="[A-Za-z ]+"/>
      <param id="tone" type="enum" default="bold" options="calm,bold"/>
      <param id="speed" type="number" default="1" min="0.5" max="2"/>
    </parameters>"""
    p = scene(tmp_path, '<shape id="s" shape="rect" width="1" height="1"/>', head=head)
    load(p)
    for bad in ({"title": "Too long"}, {"title": "abc1"}, {"tone": "loud"}, {"speed": "3"}, {"speed": "x"}):
        with pytest.raises(SceneError):
            load(p, params=bad)
    assert load(p, params={"tone": "calm", "speed": "2"}).params["tone"] == "calm"


def test_spatial_tangents_and_roving():
    import types
    def key(t, v, **a):
        return anim.Key(t, tuple(map(float, v)), "", "linear", types.SimpleNamespace(get=lambda n, d=None: a.get(n, d)))
    # A curved path: out tangent up, in tangent up -> the midpoint bulges above the straight line.
    k = [key(0, (0, 0), spatialOut="0,-100"), key(1, (100, 0), spatialIn="0,-100")]
    x, y = anim.sample(k, 0.5)
    assert x == pytest.approx(50, abs=1) and y < -50
    # Roving: the middle key is retimed so speed is constant (distance 10 then 90 -> time 0.1).
    r = anim.apply_roving([key(0, (0, 0)), key(0.5, (10, 0), roving="true"), key(1, (100, 0))])
    assert r[1].time == pytest.approx(0.1)


def test_adaptive_motion_blur_skips_still_frames(tmp_path):
    body = """<shape id="s" shape="rect" width="10" height="10" y="45" fill="#FFFFFFFF">
      <animate property="x"><key time="1" value="0"/><key time="2" value="100"/></animate>
    </shape>"""
    txt = open(scene(tmp_path, body)).read().replace('linearLight="false"', 'linearLight="false" motionBlur="true" motionBlurSamples="8"')
    q = tmp_path / "amb.xml"
    q.write_text(txt)
    r = Renderer.open(str(q))
    calls = []
    real = r.rc.render_frame
    r.rc.render_frame = lambda t, f=0: calls.append(t) or real(t, f)
    r.frame_rgb(0.3)                      # still: only the shutter's end samples are rendered
    assert len(calls) == 2
    calls.clear()
    r.frame_rgb(1.5)                      # moving: every sample
    assert len(calls) == 8


def test_repeat_over_data_fills_text_templates(tmp_path):
    head = """<parameters><param id="p" type="string" default="x"/>
      <data id="rows" format="json">[{"name":"Lamp","price":"$49"},{"name":"Chair","price":"$129"}]</data></parameters>
    <assets><text id="t" text="{{item.name}} {{item.price}} #{{index}}" width="200" height="30" size="12"/></assets>"""
    body = """<repeat id="r" over="rows" var="item" offsetY="40">
      <layer id="L" asset="t"/>
    </repeat>"""
    d = load(scene(tmp_path, body, head=head))
    texts = [d.ids[d.ids[f"L#{i}"].get("asset")].get("text") for i in range(2)]
    assert texts == ["Lamp $49 #0", "Chair $129 #1"]


# ---------------------------------------------------------------- CONVENTIONS 5.15, 5.16, 5.21, entry 17
def test_mask_feather_sigma_and_add(tmp_path):
    """5.15 / D16: feather is a Gaussian of standard deviation `feather`; add is a + m - a m."""
    body = """<shape id="f" shape="rect" width="100" height="50" fill="#FFFFFFFF">
      <mask type="rect" x="-100" y="-100" width="150" height="300" feather="3"/>
    </shape>
    <shape id="a" shape="rect" y="50" width="100" height="50" fill="#FFFFFFFF">
      <mask type="rect" x="0" y="0" width="60" height="50" mode="add" opacity="0.5"/>
      <mask type="rect" x="40" y="0" width="60" height="50" mode="add" opacity="0.5"/>
    </shape>"""
    r = Renderer.open(scene(tmp_path, body))
    row = r.frame_linear(0)[25, :, 0]
    want = np.array([0.5 * (1 - math.erf((x + 0.5 - 50) / (3 * math.sqrt(2)))) for x in range(100)])
    np.testing.assert_allclose(row, want, atol=0.012)
    assert px(r, 0, 20, 75)[0] == pytest.approx(128, abs=1)
    assert px(r, 0, 50, 75)[0] == pytest.approx(191, abs=1)                 # 0.5 + 0.5 - 0.25


def test_rect_corner_radii_and_stroke_start():
    """Entry 17: cornerRadii (TL TR BR BL) apply to shape="rect". 5.21: a rect's outline starts at 3
    o'clock and runs clockwise on screen, as an ellipse's does."""
    cmds = geometry.shape_commands("rect", 40, 20, {"cornerRadii": [8, 0, 0, 0]})
    assert cmds[0] == ("M", 40, 10)
    assert cmds[1][0] == "L" and cmds[1][2] > 10                                # first down the right edge
    assert any(c[0] == "C" for c in cmds)
    first = geometry.trim(cmds, 0, 0.05)
    assert geometry.bounds(first)[0] == pytest.approx(40, abs=1e-6)            # trimStart at 3 o'clock
    e = geometry.shape_commands("ellipse", 40, 20, {})
    assert e[0][1:] == (40, 10) and e[1][-2:] == (20, 20)         # ellipse: 3 o'clock, then down


def _sequence(tmp_path, n=10):
    from PIL import Image
    d = tmp_path / "seq"
    d.mkdir(exist_ok=True)
    for i in range(1, n + 1):
        Image.new("RGBA", (10, 10), (25 * i, 255, 0, 255)).save(d / f"f_{i:04d}.png")
    return f'<assets><imageSequence id="seq" src="{d}/f_%04d.png" first="1" last="{n}" fps="10" width="10" height="10"/></assets>'


def test_clip_ends_when_its_media_runs_out(tmp_path):
    """5.16 / D9: a clip that does not loop ends when its media runs out; freezeAt holds its source
    time for the whole window; a finite loop ends after its last play."""
    body = """<layer id="plain" asset="seq"/>
    <layer id="frozen" asset="seq" y="10" freezeAt="0.3"/>
    <layer id="looped" asset="seq" y="20" loop="1"/>
    <layer id="forever" asset="seq" y="30" loop="-1"/>"""
    r = Renderer.open(scene(tmp_path, body, head=_sequence(tmp_path)))
    assert px(r, 0.95, 5, 5)[1] == 255                                       # the last frame still plays
    assert px(r, 1.0, 5, 5) == (0, 0, 0)                                      # then the clip has ended
    assert px(r, 3.0, 5, 15) == pytest.approx((100, 255, 0), abs=1)            # frame 4 (0.3 s) held
    assert px(r, 1.5, 5, 25)[1] == 255 and px(r, 2.0, 5, 25) == (0, 0, 0)     # two plays, then nothing
    assert px(r, 3.5, 5, 35)[1] == 255
