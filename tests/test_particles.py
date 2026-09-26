"""Particle emitters (scenerender.nodes.particles) and the 2.5D camera / object3D fallback."""
from __future__ import annotations

import os
import textwrap

import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.render import Renderer

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "motion_demo.xml")


def scene(tmp_path, body: str, *, w=200, h=200, dur=6, extra="", name="p.xml") -> str:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="{w}" height="{h}" fps="10" duration="{dur}" seed="3" background="#000000FF" linearLight="false"/>
  <composition>
{textwrap.indent(body, '    ')}
  </composition>
  {extra}
</scene>"""
    p = tmp_path / name
    p.write_text(xml)
    return str(p)


def live(r: Renderer, eid: str, t: float) -> dict:
    from scenerender.nodes.particles import particles_at
    el = r.doc.ids[eid]
    return particles_at(r.rc, el, r.rc.node_ctx(el, Ctx(t=t, comp_t=t)))


# ---------------------------------------------------------------- determinism
def test_seek_independence_full_frames():
    a = Renderer.open(FIX, scale=0.25)
    direct = a.frame_rgb(2.0)
    b = Renderer.open(FIX, scale=0.25)
    b.frame_rgb(1.0)
    b.frame_rgb(3.0)
    after = b.frame_rgb(2.0)
    assert np.array_equal(direct, after)


def test_particle_state_is_seekable(tmp_path):
    p = scene(tmp_path, '<particleEmitter id="e" preset="smoke" x="100" y="150" seed="4" preroll="1"/>')
    r1, r2 = Renderer.open(p), Renderer.open(p)
    s1 = live(r1, "e", 2.37)
    for t in (0.1, 4.0, 1.0, 2.0):
        live(r2, "e", t)
    s2 = live(r2, "e", 2.37)
    assert len(s1["x"]) > 10
    for k in ("x", "y", "age", "pid"):
        assert np.array_equal(s1[k], s2[k])


def test_seed_changes_result(tmp_path):
    p = scene(tmp_path, '<particleEmitter id="e" preset="dust" x="100" y="100" emitterWidth="100" emitterHeight="100" seed="1"/>'
                        '<particleEmitter id="f" preset="dust" x="100" y="100" emitterWidth="100" emitterHeight="100" seed="2"/>')
    r = Renderer.open(p)
    a, b = live(r, "e", 2.0), live(r, "f", 2.0)
    assert len(a["x"]) == len(b["x"]) and not np.allclose(a["x"], b["x"])


# ---------------------------------------------------------------- emission
def test_burst_counts_and_repeat(tmp_path):
    p = scene(tmp_path, """<particleEmitter id="e" x="100" y="100" rate="0" lifetime="10" speed="0" seed="1">
  <burst time="0.5" count="50"/>
  <burst time="1" count="20" repeat="2" interval="0.5"/>
</particleEmitter>""")
    r = Renderer.open(p)
    assert len(live(r, "e", 0.45)["x"]) == 0
    assert len(live(r, "e", 0.75)["x"]) == 50
    assert len(live(r, "e", 1.25)["x"]) == 70
    assert len(live(r, "e", 2.2)["x"]) == 110


def test_rate_and_lifetime_expiry(tmp_path):
    p = scene(tmp_path, """<particleEmitter id="e" x="100" y="100" rate="0" lifetime="1" speed="10" seed="1">
  <burst time="0.5" count="30"/>
</particleEmitter>
<particleEmitter id="r" x="100" y="100" rate="20" lifetime="100" speed="0" seed="1"/>""")
    r = Renderer.open(p)
    assert len(live(r, "e", 1.4)["x"]) == 30
    assert len(live(r, "e", 1.6)["x"]) == 0
    n = len(live(r, "r", 2.0)["x"])
    assert 38 <= n <= 41


def test_max_particles_cap(tmp_path):
    p = scene(tmp_path, '<particleEmitter id="e" x="100" y="100" rate="500" lifetime="100" maxParticles="64" seed="1"/>')
    assert len(live(Renderer.open(p), "e", 2.0)["x"]) == 64


def test_preset_defaults_and_explicit_override(tmp_path):
    from scenerender.nodes.particles import get_emitter
    p = scene(tmp_path, '<particleEmitter id="a" preset="sparks" x="0" y="0"/>'
                        '<particleEmitter id="b" preset="sparks" x="0" y="0" size="9" shape="disc"/>')
    r = Renderer.open(p)
    c = Ctx(t=0.0, comp_t=0.0)
    A, B = get_emitter(r.rc, r.doc.ids["a"], c).P, get_emitter(r.rc, r.doc.ids["b"], c).P
    assert A.get("size") == 3 and A.get("shape") == "streak" and A.get("rate") == 80
    assert B.get("size") == 9 and B.get("shape") == "disc" and B.get("rate") == 80


def test_gravity_and_direction(tmp_path):
    p = scene(tmp_path, """<particleEmitter id="up" x="100" y="100" emitterShape="point" rate="0" lifetime="10" speed="50" direction="-90" seed="1">
  <burst time="0" count="5"/>
</particleEmitter>
<particleEmitter id="g" x="100" y="100" emitterShape="point" rate="0" lifetime="10" speed="0" gravityY="100" seed="1">
  <burst time="0" count="5"/>
</particleEmitter>""")
    r = Renderer.open(p)
    up = live(r, "up", 1.0)
    assert np.allclose(up["x"], 100, atol=1e-6) and np.allclose(up["y"], 50, atol=1.0)
    g = live(r, "g", 1.0)
    assert np.allclose(g["y"], 150, atol=2.5)        # 100 + g t^2 / 2 (semi-implicit Euler)


def test_moving_emitter_leaves_particles_behind(tmp_path):
    p = scene(tmp_path, """<particleEmitter id="e" x="20" y="100" emitterShape="point" rate="0" lifetime="10" speed="0" seed="1">
  <animate property="x"><key time="0" value="20"/><key time="2" value="180"/></animate>
  <burst time="0.25" count="3"/>
</particleEmitter>""")
    r = Renderer.open(p)
    s = live(r, "e", 2.0)
    assert np.allclose(s["x"], 40, atol=1.5)          # born where the emitter was at t=0.25


def test_emitter_renders_pixels_and_blend(tmp_path):
    p = scene(tmp_path, """<particleEmitter id="e" x="100" y="100" emitterShape="rect" emitterWidth="100" emitterHeight="100"
    rate="0" lifetime="5" speed="0" size="12" color="#FF0000FF" seed="1" blend="add">
  <burst time="0" count="40"/>
</particleEmitter>""")
    img = Renderer.open(p).frame_rgb(1.0)
    red = (img[..., 0] > 200) & (img[..., 1] < 40)
    assert red.sum() > 300
    assert not red[:40].any() and not red[160:].any()


@pytest.mark.parametrize("preset", ["smoke", "sparks", "dust", "rain", "snow", "confetti", "fire", "bubbles",
                                    "bokeh", "glitter"])
def test_every_preset_draws(tmp_path, preset):
    p = scene(tmp_path, f'<particleEmitter id="e" preset="{preset}" x="100" y="100" emitterWidth="80" emitterHeight="80" seed="2" preroll="2"/>')
    img = Renderer.open(p).frame_rgb(1.0)
    assert img.max() > 30


def test_force_field_vortex_turns_particles(tmp_path):
    body = """<particleEmitter id="e" x="100" y="100" emitterShape="point" rate="0" lifetime="10" speed="60" direction="0" seed="1" forceFields="ff">
  <burst time="0" count="1"/>
</particleEmitter>"""
    extra = '<physics><forceField id="ff" type="vortex" x="100" y="100" strength="200" affects="particles"/></physics>'
    r = Renderer.open(scene(tmp_path, body, extra=extra))
    s = live(r, "e", 0.5)
    assert s["y"][0] > 100 + 5          # clockwise on screen: +x velocity bends toward +y


def test_collide_with_floor(tmp_path):
    p = scene(tmp_path, """<particleEmitter id="e" x="100" y="150" emitterShape="point" rate="0" lifetime="10" speed="0" gravityY="400" collide="true" bounce="0.5" seed="1">
  <burst time="0" count="1"/>
</particleEmitter>""")
    r = Renderer.open(p)
    ys = [float(live(r, "e", t)["y"][0]) for t in np.arange(0.1, 4, 0.1)]
    assert max(ys) <= 200.5


# ---------------------------------------------------------------- camera / 3D
def test_threed_without_camera_is_identity(tmp_path):
    from scenerender.camera import camera_hook
    p = scene(tmp_path, '<shape id="s" shape="rect" x="30" y="40" width="50" height="20" threeD="true" fill="#FFFFFFFF"/>')
    r = Renderer.open(p, scale=0.5)
    el = r.doc.ids["s"]
    ctx = Ctx(t=0.0, comp_t=0.0)
    M = r.rc.world_matrix(el, ctx)
    assert np.allclose(camera_hook(r.rc, el, M, r.rc.node_ctx(el, ctx)), M, atol=1e-6)


def test_zdepth_shrinks_and_rotation_foreshortens(tmp_path):
    from scenerender.camera import camera_hook, project_quad
    p = scene(tmp_path, '<camera id="c" x="0" y="0" z="1000" fov="60"/>'
                        '<shape id="s" shape="rect" x="75" y="75" width="50" height="50" threeD="true" zDepth="1000"/>'
                        '<shape id="t" shape="rect" x="75" y="75" width="50" height="50" anchorX="25" threeD="true" rotationY="60"/>')
    r = Renderer.open(p)
    ctx = Ctx(t=0.0, comp_t=0.0)
    s, t = r.doc.ids["s"], r.doc.ids["t"]
    Ms = camera_hook(r.rc, s, r.rc.world_matrix(s, ctx), ctx)
    M0 = r.rc.world_matrix(s, ctx)
    ratio = np.hypot(Ms[0, 0], Ms[1, 0]) / np.hypot(M0[0, 0], M0[1, 0])
    f = 100 / np.tan(np.radians(30))
    assert ratio == pytest.approx(f / 2000, rel=1e-6)
    q = project_quad(r.rc, t, r.rc.world_matrix(t, ctx), ctx)
    width_top = np.hypot(*(q[1] - q[0]))
    assert width_top < 50 * f / 1000 * 0.6          # cos(60) foreshortening (plus perspective)


def test_camera_switching_last_active_wins(tmp_path):
    from scenerender.camera import active_camera
    p = scene(tmp_path, '<camera id="a" z="500"/><camera id="b" z="800" start="1" end="2"/>'
                        '<camera id="c" z="900" start="1.5" active="false"/>')
    r = Renderer.open(p)
    assert active_camera(r.rc, 0.5).get("id") == "a"
    assert active_camera(r.rc, 1.5).get("id") == "b"
    assert active_camera(r.rc, 2.0).get("id") == "a"


def test_object3d_fallback_draws(tmp_path):
    head = ('<materials><material id="m" baseColor="#FF0000FF"/></materials>')
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="200" height="200" fps="10" duration="2" background="#000000FF" linearLight="false"/>
  {head}
  <composition>
    <object3D id="b" primitive="box" material="m" width="60" height="60" rotationY="30" rotationX="20"/>
    <object3D id="s" primitive="sphere" material="m" radius="20" x="70" y="70"/>
  </composition>
</scene>"""
    path = tmp_path / "o.xml"
    path.write_text(xml)
    img = Renderer.open(str(path)).frame_rgb(0.0)
    assert img[100, 100, 0] > 80 and img[100, 100, 1] < 40      # box at the centre
    assert img[30, 170, 0] > 80                                 # sphere up-right (+y is up)
