"""Rigid-body shapes, filtering, bullets, joints, soft bodies and the physics cache (scenerender.physics)."""
from __future__ import annotations

import hashlib
import math
import textwrap

import numpy as np
import pytest

from scenerender import physics
from scenerender.render import Renderer


def scene(tmp_path, body: str, physics_attrs: str = 'bounds="none"', fields: str = "", w=400, h=400,
          name="ph.xml", duration=10):
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="{w}" height="{h}" fps="30" duration="{duration}" background="#000000FF" linearLight="false"/>
  <composition>
{textwrap.indent(body, '    ')}
  </composition>
  <physics {physics_attrs}>{fields}</physics>
</scene>"""
    p = tmp_path / name
    p.write_text(xml)
    return str(p)


def centre(r, eid, t):
    b = physics.body_state(r.rc, r.doc.ids[eid], t)
    sim = physics.get_sim(r.rc)
    c = b.box_centre()
    return c[0] * sim.ppm, -c[1] * sim.ppm, b


FLOOR = '<shape id="g" shape="rect" x="0" y="300" width="400" height="20"><rigidBody type="static"/></shape>'


# ---------------------------------------------------------------- shapes
def test_capsule_rests_on_its_radius(tmp_path):
    body = FLOOR + '<shape id="c" shape="rect" x="100" y="100" width="120" height="40"><rigidBody shape="capsule"/></shape>'
    r = Renderer.open(scene(tmp_path, body))
    x, y, b = centre(r, "c", 4.0)
    assert y == pytest.approx(300 - 20, abs=1.5)                 # radius = half the short side
    assert b.parts[0].r == pytest.approx(0.2) and len(b.parts[0].loc) == 2
    assert abs(math.degrees(b.angle)) < 1.0


def test_capsule_mass_properties_match_box2d():
    p = physics.Part(np.array([[-1.0, 0.0], [1.0, 0.0]]), 0.5)
    A, c, J = physics._part_mass(p)
    assert A == pytest.approx(2 * 0.5 * 2 + math.pi * 0.25)
    assert c.tolist() == pytest.approx([0, 0])


def test_polygon_triangle_rests_on_flat_side(tmp_path):
    # a triangle standing on its long edge: its lowest vertices end on the floor
    body = FLOOR + ('<shape id="t" shape="rect" x="150" y="100" width="100" height="60">'
                    '<rigidBody shape="polygon" path="0 60 100 60 50 0"/></shape>')
    r = Renderer.open(scene(tmp_path, body))
    sim = physics.get_sim(r.rc)
    b = physics.body_state(r.rc, r.doc.ids["t"], 4.0)
    ys = [-(v[:, 1]).max() * sim.ppm for v, _, _ in b.world_parts()]
    assert min(-v[:, 1].min() * sim.ppm for v, _, _ in b.world_parts()) == pytest.approx(300, abs=1.5)
    assert len(ys) == 1 and abs(math.degrees(b.angle)) < 1.0
    # centre of mass of a triangle: a third of the height above the base
    assert b.c_off[1] * sim.ppm == pytest.approx(40 - 30, abs=0.3)   # box centre 10 px above the COM


def test_concave_path_is_decomposed_and_collides(tmp_path):
    # an L-shaped static body: a ball dropped over the notch lands on the lower arm
    L = "M0 0 L40 0 L40 60 L100 60 L100 100 L0 100 Z"
    body = ('<shape id="L" shape="path" path="{p}" x="100" y="200" width="100" height="100">'
            '<rigidBody type="static" shape="path"/></shape>'
            '<shape id="ball" shape="ellipse" x="160" y="100" width="20" height="20"><rigidBody shape="circle"/></shape>').format(p=L)
    r = Renderer.open(scene(tmp_path, body))
    sim = physics.get_sim(r.rc)
    w = sim.world_at(0.0) or sim.sim.at(0)
    Lb = w.bodies[sim.index[r.doc.ids["L"]]]
    assert len(Lb.parts) >= 2 and all(physics._is_convex(p.loc) for p in Lb.parts)
    _, y, _ = centre(r, "ball", 3.0)
    assert y == pytest.approx(260 - 10, abs=1.5)                 # on the lower arm (y=260), not the top (200)


def test_convex_hull_from_alpha(tmp_path):
    body = FLOOR + ('<shape id="d" shape="ellipse" x="150" y="100" width="80" height="80" fill="#FFFFFFFF">'
                    '<rigidBody shape="convex-hull"/></shape>')
    r = Renderer.open(scene(tmp_path, body))
    sim = physics.get_sim(r.rc)
    b = sim.sim.at(0).bodies[sim.index[r.doc.ids["d"]]]
    assert len(b.parts) == 1 and len(b.parts[0].loc) > 16
    rad = np.hypot(*b.parts[0].loc.T) * sim.ppm
    assert rad.max() == pytest.approx(40, abs=1.0) and rad.min() > 38.0
    _, y, _ = centre(r, "d", 4.0)
    assert y == pytest.approx(300 - 40, abs=2.0)


# ---------------------------------------------------------------- filtering, sensors, bullets
def test_collision_groups_and_sensor(tmp_path):
    body = (FLOOR.replace('type="static"', 'type="static" collisionGroup="1"') +
            '<shape id="a" shape="rect" x="100" y="100" width="40" height="40"><rigidBody collidesWith="2"/></shape>'
            '<shape id="b" shape="rect" x="200" y="100" width="40" height="40"><rigidBody collidesWith="1 2"/></shape>'
            '<shape id="s" shape="rect" x="180" y="250" width="80" height="40"><rigidBody type="static" sensor="true" collisionGroup="2"/></shape>')
    r = Renderer.open(scene(tmp_path, body))
    _, ya, _ = centre(r, "a", 2.0)
    _, yb, _ = centre(r, "b", 2.0)
    assert ya > 400                                  # a does not accept group 1: falls through the floor
    assert yb == pytest.approx(280, abs=1.5)         # b accepts group 1; passes the sensor, rests on the floor
    hits = {h for t in np.arange(0.5, 2.0, 0.01) for h in physics.sensor_overlaps(r.rc, t)}
    assert ("b", "s") in hits or ("s", "b") in hits


def test_bullet_prevents_tunnelling(tmp_path):
    wall = '<shape id="w" shape="rect" x="300" y="0" width="4" height="400"><rigidBody type="static"/></shape>'
    ball = '<shape id="b" shape="ellipse" x="20" y="190" width="10" height="10"><rigidBody shape="circle" velocityX="400" {x}/></shape>'
    attrs = 'gravityY="0" bounds="none"'
    r = Renderer.open(scene(tmp_path, wall + ball.format(x=""), attrs, name="a.xml"))
    x, _, _ = centre(r, "b", 0.2)
    assert x > 304                                   # 3.3 m per step: tunnels through the 4 px wall
    r = Renderer.open(scene(tmp_path, wall + ball.format(x='bullet="true"'), attrs, name="b.xml"))
    xs = [centre(r, "b", t)[0] for t in np.arange(1 / 120, 0.3, 1 / 120)]
    assert max(xs) <= 300 - 5 + 1.0                  # stopped at the wall's face


# ---------------------------------------------------------------- joints
PEND = '<shape id="p" shape="rect" x="{x}" y="{y}" width="20" height="20"><rigidBody {rb}/></shape>'


def test_hinge_limits_hold(tmp_path):
    # a bar hinged to the world at its left end, falling: limited to 0..30 deg clockwise
    body = '<shape id="bar" shape="rect" x="100" y="100" width="100" height="10"><rigidBody linearDamping="0"/></shape>'
    fields = '<constraint id="h" type="hinge" a="bar" x="100" y="105" minAngle="0" maxAngle="30"/>'
    r = Renderer.open(scene(tmp_path, body, fields=fields), strict=False)     # a world hinge has no @b (C11)
    angs = []
    for t in np.arange(0.1, 3.0, 0.05):
        _, _, b = centre(r, "bar", t)
        angs.append(-math.degrees(b.angle))
    assert max(angs) == pytest.approx(30, abs=1.5) and min(angs) > -1.0
    x, y, b = centre(r, "bar", 3.0)
    assert math.hypot(x - 100, y - 105) == pytest.approx(50, abs=1.0)   # pivot held


def test_motor_reaches_speed_and_respects_max_force(tmp_path):
    body = '<shape id="wh" shape="ellipse" x="150" y="150" width="100" height="100"><rigidBody shape="circle" angularDamping="0"/></shape>'
    fields = '<constraint id="m" type="motor" a="wh" motorSpeed="180"/>'
    r = Renderer.open(scene(tmp_path, body, 'gravityY="0"', fields=fields, name="m1.xml"), strict=False)   # no @b (C11)
    _, _, b = centre(r, "wh", 1.0)
    assert -math.degrees(b.w) == pytest.approx(180, rel=0.01)
    fields = '<constraint id="m" type="motor" a="wh" motorSpeed="180" maxForce="0.05"/>'
    r = Renderer.open(scene(tmp_path, body, 'gravityY="0"', fields=fields, name="m2.xml"), strict=False)
    _, _, b = centre(r, "wh", 0.5)
    I = b.inertia
    # torque-limited spin-up: w = tau/I * t until the target is reached
    assert -b.w == pytest.approx(min(math.pi, 0.05 / I * 0.5), rel=0.05)


def test_slider_keeps_axis_and_limits(tmp_path):
    body = PEND.format(x=190, y=190, rb='linearDamping="0"')
    fields = '<constraint id="s" type="slider" a="p" x="200" y="200" axisAngle="45" restLength="0.5"/>'
    r = Renderer.open(scene(tmp_path, body, fields=fields), strict=False)     # a world slider has no @b (C11)
    x, y, b = centre(r, "p", 2.0)
    d = np.array([x - 200, y - 200])
    u = np.array([math.cos(math.radians(45)), math.sin(math.radians(45))])
    assert abs(d @ np.array([-u[1], u[0]])) < 1.0                # stays on the 45 deg axis
    assert d @ u == pytest.approx(50, abs=1.5)                    # stopped at +restLength
    assert abs(math.degrees(b.angle)) < 0.5                       # rotation locked


def test_weld_is_rigid_and_breaks(tmp_path):
    body = PEND.format(x=100, y=100, rb='') + '<shape id="q" shape="rect" x="120" y="100" width="20" height="20"><rigidBody/></shape>'
    fields = '<constraint id="w" type="weld" a="p" b="q" x="120" y="110"/><constraint id="pin" type="pin" a="p" x="110" y="110"/>'
    r = Renderer.open(scene(tmp_path, body, fields=fields, name="w1.xml"))
    for t in (1.0, 2.0):
        xp, yp, bp = centre(r, "p", t)
        xq, yq, bq = centre(r, "q", t)
        assert math.hypot(xq - xp, yq - yp) == pytest.approx(20, abs=0.5)
        assert (bq.angle - bp.angle) == pytest.approx(0, abs=0.02)
    fields2 = fields.replace('x="120" y="110"/>', 'x="120" y="110" breakForce="0.5"/>', 1)
    r = Renderer.open(scene(tmp_path, body, fields=fields2, name="w2.xml"))
    w = physics.get_sim(r.rc).world_at(2.0)
    assert w.joints[0].broken


# ---------------------------------------------------------------- soft bodies
def test_cloth_drapes_from_pinned_top(tmp_path):
    body = ('<shape id="c" shape="rect" x="100" y="50" width="200" height="100" fill="#FFFFFFFF">'
            '<softBody kind="cloth" pin="top" rows="6" cols="8" stiffness="200" damping="0.3" mass="0.5"/></shape>')
    r = Renderer.open(scene(tmp_path, body))
    s, pw = physics.soft_world(r.rc, r.doc.ids["c"], 4.0)
    top = pw[:8]
    assert np.allclose(top[:, 1], 50, atol=1e-6) and np.allclose(top[:, 0], np.linspace(100, 300, 8), atol=1e-6)
    bottom = pw[-8:]
    assert bottom[:, 1].min() > 150                    # hangs: stretched below its rest height
    # at rest the cloth hangs straight down (x within its columns) and is settled
    assert np.abs(s.vel).max() < 0.05
    assert np.all(np.diff(bottom[:, 0]) > 0)


def test_jelly_keeps_volume_on_floor(tmp_path):
    body = ('<shape id="j" shape="rect" x="150" y="100" width="100" height="100" fill="#FFFFFFFF">'
            '<softBody kind="jelly" rows="5" cols="5" stiffness="300" damping="0.4" pressure="200" mass="1"/></shape>')
    r = Renderer.open(scene(tmp_path, body, 'bounds="floor"', h=300))
    s, pw = physics.soft_world(r.rc, r.doc.ids["j"], 3.0)
    ring = pw[s.ring]
    area = abs(physics._signed_area(ring))
    assert area == pytest.approx(100 * 100, rel=0.1)
    assert pw[:, 1].max() == pytest.approx(300 - 1, abs=1.5)      # resting on the floor (1 cm skin)
    assert np.abs(s.vel).max() < 0.1


def test_soft_body_collides_with_rigid_body_and_warps_tile(tmp_path):
    body = ('<shape id="g" shape="rect" x="0" y="250" width="400" height="20"><rigidBody type="static"/></shape>'
            '<shape id="c" shape="rect" x="100" y="100" width="100" height="40" fill="#FFFFFFFF">'
            '<softBody kind="rope" pin="left" rows="2" cols="10" stiffness="400" damping="0.5" mass="0.2"/></shape>')
    r = Renderer.open(scene(tmp_path, body, name="rope.xml"))
    s, pw = physics.soft_world(r.rc, r.doc.ids["c"], 3.0)
    assert s.kind == "rope" and len(pw) == 10
    assert np.allclose(pw[0], [100, 120], atol=1e-6)            # pinned left end on the centre line
    assert pw[:, 1].max() <= 250 + 0.5                          # never below the ground's top face
    img = r.frame_rgba(3.0)
    alpha = img[..., 3] if img.shape[-1] == 4 else None
    assert alpha is not None and alpha[100:140, 100:110].max() > 0      # the rope's pinned end still drawn
    rows = np.nonzero(alpha[:, 150:200].max(1))[0]
    assert rows.max() > 145                                      # the free part hangs below its rest box


def test_soft_body_is_seekable(tmp_path):
    body = ('<shape id="c" shape="rect" x="100" y="50" width="100" height="100">'
            '<softBody kind="jelly" rows="4" cols="4" stiffness="100" pressure="50"/></shape>')
    p = scene(tmp_path, body, 'bounds="frame"')
    a = Renderer.open(p)
    ref = physics.soft_world(a.rc, a.doc.ids["c"], 2.5)[1].copy()
    b = Renderer.open(p)
    physics.soft_world(b.rc, b.doc.ids["c"], 4.0)
    assert np.array_equal(physics.soft_world(b.rc, b.doc.ids["c"], 2.5)[1], ref)


# ---------------------------------------------------------------- force fields
def test_attractor_path_exact_closest_point_and_tangent(tmp_path):
    r = Renderer.open(scene(tmp_path, '<shape id="x" shape="rect" width="1" height="1"/>',
                            fields='<forceField id="f" type="attractor-path" path="M0 100 C 100 0 200 200 300 100" '
                                   'strength="5" forceX="2"/>'))
    f = r.doc.ids["f"]
    pg = physics.path_geom(r.rc, f.get("path"))
    X, Y = np.array([150.0, 20.0]), np.array([300.0, 20.0])
    qx, qy, tx, ty, d = pg.closest(X, Y)
    # brute force on the exact bezier
    u = np.linspace(0, 1, 200001)
    bx = (1 - u) ** 3 * 0 + 3 * (1 - u) ** 2 * u * 100 + 3 * (1 - u) * u * u * 200 + u ** 3 * 300
    by = (1 - u) ** 3 * 100 + 3 * (1 - u) ** 2 * u * 0 + 3 * (1 - u) * u * u * 200 + u ** 3 * 100
    for k in range(2):
        dd = np.hypot(bx - X[k], by - Y[k])
        assert d[k] == pytest.approx(dd.min(), abs=0.05)
    ax, ay = physics.field_accel(r.rc, f, 0.0, X, Y, np.zeros(2), np.zeros(2), 100.0)
    n = np.stack([qx - X, qy - Y], 1) / d[:, None]
    assert np.allclose(np.stack([ax, ay], 1), n * 5 + np.stack([tx, ty], 1) * 2, atol=1e-9)


# ---------------------------------------------------------------- cache
def test_physics_cache_written_then_replayed(tmp_path):
    body = FLOOR + '<shape id="b" shape="rect" x="175" y="0" width="50" height="50"><rigidBody/></shape>'
    p = scene(tmp_path, body, 'bounds="none" cache="sim.npz"', duration=2)
    r = Renderer.open(p)
    ref = centre(r, "b", 1.5)[:2]
    cache = tmp_path / "sim.npz"
    assert cache.exists()
    sha = hashlib.sha256(cache.read_bytes()).hexdigest()
    p2 = scene(tmp_path, body, f'bounds="none" cache="sim.npz" cacheSha256="{sha}"', duration=2)
    r2 = Renderer.open(p2)
    sim = physics.get_sim(r2.rc)
    assert sim.cached is not None
    assert centre(r2, "b", 1.5)[:2] == pytest.approx(ref, abs=1e-9)
    # a wrong hash is ignored (re-simulated, file untouched)
    p3 = scene(tmp_path, body, f'bounds="none" cache="sim.npz" cacheSha256="{"0" * 64}"', duration=2, name="bad.xml")
    r3 = Renderer.open(p3)
    assert physics.get_sim(r3.rc).cached is None and hashlib.sha256(cache.read_bytes()).hexdigest() == sha
    assert centre(r3, "b", 1.5)[:2] == pytest.approx(ref, abs=1e-9)


def test_cloth_self_collision_separates_nodes(tmp_path):
    body = ('<shape id="c" shape="rect" x="100" y="50" width="100" height="100">'
            '<softBody kind="cloth" pin="top" rows="5" cols="5" selfCollision="true"/></shape>')
    r = Renderer.open(scene(tmp_path, body))
    sim = physics.get_sim(r.rc)
    w = sim.sim.at(0)
    s = w.softs[0]
    assert s.self_coll and s.neighbours is not None
    s.pos[24] = s.pos[0] + 1e-4                      # two far-apart nodes forced onto each other
    sim._soft_self(s)
    assert np.hypot(*(s.pos[24] - s.pos[0])) == pytest.approx(0.5 * s.spacing, rel=1e-3)
