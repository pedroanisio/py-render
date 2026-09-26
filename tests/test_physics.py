"""Rigid-body physics (scenerender.physics): free fall, resting contact, constraints, determinism."""
from __future__ import annotations

import math
import textwrap

import numpy as np
import pytest

from scenerender import physics
from scenerender.render import Renderer


def scene(tmp_path, body: str, physics_attrs: str = 'bounds="none"', fields: str = "", w=400, h=400, name="ph.xml"):
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="{w}" height="{h}" fps="30" duration="10" background="#000000FF" linearLight="false"/>
  <composition>
{textwrap.indent(body, '    ')}
  </composition>
  <physics {physics_attrs}>{fields}</physics>
</scene>"""
    p = tmp_path / name
    p.write_text(xml)
    return str(p)


def centre_px(r, eid, t):
    b = physics.body_state(r.rc, r.doc.ids[eid], t)
    sim = physics.get_sim(r.rc)
    return b.pos[0] * sim.ppm, -b.pos[1] * sim.ppm, b


BOX = '<shape id="b" shape="rect" x="{x}" y="{y}" width="{s}" height="{s}"><rigidBody {rb}/></shape>'


def test_free_fall_follows_g_t2(tmp_path):
    p = scene(tmp_path, BOX.format(x=175, y=0, s=50, rb='linearDamping="0" angularDamping="0"'))
    r = Renderer.open(p)
    y0 = 25.0
    for t in (0.25, 0.5, 1.0):
        _, y, b = centre_px(r, "b", t)
        expect = y0 + 0.5 * 9.80665 * t * t * 100
        assert y == pytest.approx(expect, rel=0.02, abs=1.0)
        assert b.vel[1] == pytest.approx(-9.80665 * t, rel=0.02)


def test_rests_on_floor(tmp_path):
    p = scene(tmp_path, BOX.format(x=175, y=100, s=50, rb='restitution="0"'), 'bounds="floor"')
    r = Renderer.open(p)
    _, y, b = centre_px(r, "b", 4.0)
    assert y == pytest.approx(400 - 25, abs=1.5)
    assert abs(b.vel[1]) < 0.05 and abs(b.w) < 0.05
    assert math.degrees(b.angle) == pytest.approx(0, abs=0.5)


def test_rests_on_static_body_and_ball_bounces(tmp_path):
    body = (BOX.format(x=175, y=100, s=50, rb='') +
            '<shape id="g" shape="rect" x="0" y="300" width="400" height="20"><rigidBody type="static"/></shape>'
            '<shape id="c" shape="ellipse" x="50" y="0" width="40" height="40"><rigidBody shape="circle" restitution="0.8"/></shape>')
    r = Renderer.open(scene(tmp_path, body))
    _, y, _ = centre_px(r, "b", 3.0)
    assert y == pytest.approx(300 - 25, abs=1.5)
    ys = [centre_px(r, "c", t)[1] for t in np.arange(1 / 30, 3.0, 1 / 30)]
    first = next(i for i, v in enumerate(ys) if v >= 300 - 20 - 1.5)
    assert min(ys[first:]) < 300 - 20 - 40            # bounced back up
    assert max(ys) <= 300 - 20 + 3                    # never sank through the ground


def test_seek_independence_and_determinism(tmp_path):
    body = (BOX.format(x=100, y=0, s=40, rb='velocityX="2" angularVelocity="90"') +
            '<shape id="c" shape="ellipse" x="140" y="-60" width="40" height="40"><rigidBody shape="circle" restitution="0.5"/></shape>')
    p = scene(tmp_path, body, 'bounds="frame"')
    a = Renderer.open(p)
    ref = centre_px(a, "b", 2.0)[:2]
    b = Renderer.open(p)
    for t in (3.0, 0.7, 1.3):
        centre_px(b, "b", t)
    assert centre_px(b, "b", 2.0)[:2] == ref


def test_activate_at_holds_keyframes(tmp_path):
    body = '<shape id="b" shape="rect" x="100" y="50" width="20" height="20"><rigidBody activateAt="1"/></shape>'
    r = Renderer.open(scene(tmp_path, body))
    assert physics.body_transform(r.rc, r.doc.ids["b"], 0.5) is None
    x, y, rot = physics.body_transform(r.rc, r.doc.ids["b"], 1.5)
    assert x == pytest.approx(100, abs=1e-6)
    assert y == pytest.approx(50 + 0.5 * 9.80665 * 0.25 * 100, rel=0.06)   # first-order integrator


def test_body_transform_in_parent_space_with_anchor(tmp_path):
    body = ('<group id="g" x="50" y="20">'
            '<shape id="b" shape="rect" x="100" y="50" width="40" height="20" anchorX="20" anchorY="10" rotation="30">'
            '<rigidBody fixedRotation="true" linearDamping="0"/></shape></group>')
    p = scene(tmp_path, body, 'gravityY="0"')
    r = Renderer.open(p)
    x, y, rot = physics.body_transform(r.rc, r.doc.ids["b"], 1.0)
    assert (x, y, rot) == pytest.approx((100, 50, 30), abs=1e-4)      # no forces: pose unchanged


def test_static_bodies_keep_keyframes(tmp_path):
    body = '<shape id="g" shape="rect" x="0" y="300" width="400" height="20"><rigidBody type="static"/></shape>'
    r = Renderer.open(scene(tmp_path, body))
    assert physics.body_transform(r.rc, r.doc.ids["g"], 1.0) is None


def test_pin_constraint_keeps_length(tmp_path):
    body = BOX.format(x=280, y=180, s=40, rb='linearDamping="0"')
    fields = '<constraint id="p" type="pin" a="b" x="200" y="200"/>'
    r = Renderer.open(scene(tmp_path, body, fields=fields))
    la = np.hypot(*(np.array([300, 200]) - np.array([200, 200])))
    for t in (0.5, 1.0, 2.0):
        x, y, b = centre_px(r, "b", t)
        assert np.hypot(x - 200, y - 200) == pytest.approx(la, rel=0.03)
    assert centre_px(r, "b", 0.6)[1] > 230               # it swings down


def test_rope_and_spring(tmp_path):
    body = (BOX.format(x=180, y=100, s=20, rb='linearDamping="0"').replace('id="b"', 'id="a"') +
            BOX.format(x=180, y=150, s=20, rb='linearDamping="0"'))
    fields = ('<constraint id="pin" type="pin" a="a" x="190" y="110"/>'
              '<constraint id="r" type="rope" a="a" b="b" restLength="1"/>')
    r = Renderer.open(scene(tmp_path, body, fields=fields))
    xa, ya, _ = centre_px(r, "a", 2.0)
    xb, yb, _ = centre_px(r, "b", 2.0)
    assert np.hypot(xb - xa, yb - ya) <= 100 + 3

    fields = '<constraint id="s" type="spring" a="a" b="b" restLength="0.5" stiffness="80" damping="0.5"/>'
    body2 = (BOX.format(x=180, y=100, s=20, rb='type="kinematic"').replace('id="b"', 'id="a"') +
             BOX.format(x=180, y=150, s=20, rb=''))
    r = Renderer.open(scene(tmp_path, body2, fields=fields, name="sp.xml"))
    _, yb, b = centre_px(r, "b", 6.0)
    # equilibrium: k (L - L0) = m g  ->  L = 0.5 + 9.81/80 m
    assert (yb - 110) / 100 == pytest.approx(0.5 + 9.80665 / 80, abs=0.03)


def test_force_field_on_bodies(tmp_path):
    body = BOX.format(x=100, y=100, s=20, rb='linearDamping="0"')
    fields = '<forceField id="f" type="directional" forceX="3" forceY="0" affects="bodies"/>'
    r = Renderer.open(scene(tmp_path, body, 'gravityY="0"', fields=fields))
    x, _, _ = centre_px(r, "b", 1.0)
    assert x == pytest.approx(110 + 0.5 * 3 * 100, rel=0.03)


def test_hook_installed_and_softbody_reported(tmp_path, caplog):
    body = ('<shape id="b" shape="rect" x="100" y="0" width="20" height="20"><rigidBody/></shape>'
            '<shape id="f" shape="rect" x="0" y="0" width="20" height="20"><softBody/></shape>')
    r = Renderer.open(scene(tmp_path, body))
    hook = r.rc.hooks.get("physics")
    assert hook is not None
    from scenerender.evaluator import Ctx
    assert hook(r.rc, r.doc.ids["b"], Ctx(t=1.0, comp_t=1.0)) is not None
    assert hook(r.rc, r.doc.ids["f"], Ctx(t=1.0, comp_t=1.0)) is None
