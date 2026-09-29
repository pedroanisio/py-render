"""360 output: layouts, sizes, seams, stereo, the 2D front face and the render360 hook."""
from __future__ import annotations

import math
import os
import textwrap

import numpy as np
import pytest

from scenerender import gl
from scenerender.render import Renderer
from scenerender.three import view360 as V

pytestmark = pytest.mark.skipif(not gl.available(), reason="no GL")
ENV = os.path.join(os.path.dirname(__file__), "fixtures", "media", "3d", "env_sky.npy")


def doc(tmp_path, body="", *, s360='layout="equirectangular" width="256" height="128"', lights=None, mode="equirectangular",
        name="p.xml") -> Renderer:
    lights = lights if lights is not None else f'<light id="d" type="dome" environment="{ENV}" environmentVisible="true"/>'
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="320" height="180" fps="10" duration="4" background="#000000FF" mode="{mode}"/>
  <scene360 {s360}/>
  <composition>
{textwrap.indent(body, '    ')}
  </composition>
  <lights>{lights}</lights>
</scene>"""
    p = tmp_path / name
    p.write_text(xml)
    return Renderer.open(str(p))


@pytest.mark.parametrize("layout,stereo,size", [
    ("equirectangular", "mono", (256, 128)), ("equirectangular", "top-bottom", (256, 256)),
    ("equirectangular", "left-right", (512, 128)), ("cubemap", "mono", (192, 128)), ("eac", "mono", (192, 128)),
    ("fisheye-180", "mono", (128, 128))])
def test_layout_sizes(tmp_path, layout, stereo, size):
    r = doc(tmp_path, s360=f'layout="{layout}" stereo="{stereo}" width="{size[0]}" height="{size[1]}"')
    assert V.size_360(r.doc, 1.0) == size
    buf = V.render_360(r.rc, 0.0)
    assert (buf.w, buf.h) == size
    assert buf.px[..., 3].max() > 0.9


def smooth_env(tmp_path) -> str:
    """A radiance field linear in the direction (smooth everywhere, so any step is a seam)."""
    from scenerender.three.lights import equirect_dirs
    d = equirect_dirs(128, 64)
    env = (0.5 + 0.3 * d[..., 0:1] * [1, 0.5, 0.2] + 0.2 * d[..., 1:2] + 0.1 * d[..., 2:3] * [0.2, 1, 0.5]).astype(np.float32)
    p = tmp_path / "smooth.npy"
    np.save(p, env)
    return str(p)


def test_equirect_matches_environment_and_has_no_seams(tmp_path):
    envp = smooth_env(tmp_path)
    r = doc(tmp_path, lights=f'<light id="d" type="dome" environment="{envp}" environmentVisible="true"/>')
    px = V.render_360(r.rc, 0.0).px
    env = np.load(envp)
    from scenerender.three.lights import _sample_equirect
    h, w = px.shape[:2]
    u = (np.arange(w) + 0.5) / w
    vv = (np.arange(h) + 0.5) / h
    U, VV = np.meshgrid(u, vv)
    ref = _sample_equirect(env, U, VV)
    err = np.abs(px[..., :3] - ref)
    assert err.max() < 0.02
    lum = px[..., :3].sum(-1)
    assert np.abs(lum[:, 0] - lum[:, -1]).max() < 0.03            # wrap-around column continuity
    step = np.abs(np.diff(lum, axis=1)).max()
    assert step < 0.03                                            # no step at the cube-face boundaries


def test_cubemap_and_eac_faces_are_continuous(tmp_path):
    envp = smooth_env(tmp_path)
    for layout in ("cubemap", "eac"):
        r = doc(tmp_path, s360=f'layout="{layout}" width="192" height="128"', name=f"{layout}.xml",
                lights=f'<light id="d" type="dome" environment="{envp}" environmentVisible="true"/>')
        px = V.render_360(r.rc, 0.0).px[..., :3].sum(-1)
        for band in (px[:64], px[64:]):                             # left|front|right and down|back|up
            steps = np.abs(np.diff(band, axis=1))
            inner = np.delete(steps, [62, 63, 64, 126, 127, 128], axis=1).max()
            assert steps[:, [63, 127]].max() < 1.5 * inner + 1e-3, layout   # cell borders are no rougher


def test_fisheye_circle_is_masked(tmp_path):
    r = doc(tmp_path, s360='layout="fisheye-180" width="128" height="128"')
    px = V.render_360(r.rc, 0.0).px
    assert px[2, 2, 3] == 0 and px[64, 64, 3] > 0.99


def test_layout_directions():
    from scenerender.camera import Camera
    cam = Camera(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, -1.0]), 100, False, 1, 100, 100)
    d, _ = V.layout_dirs("equirectangular", 8, 4, cam)
    centre = (d[1, 3] + d[1, 4] + d[2, 3] + d[2, 4]) / 4
    assert centre / np.linalg.norm(centre) == pytest.approx([0, 0, -1], abs=1e-9)   # centre = front
    assert d[0, 6][0] > 0.3                                                         # right half looks +X
    c, _ = V.layout_dirs("cubemap", 300, 200, cam)
    front = c[50, 150] / np.linalg.norm(c[50, 150])
    assert front == pytest.approx([0, 0, -1], abs=0.02)                               # pixel centre of the cell
    e, _ = V.layout_dirs("eac", 300, 200, cam)
    back = e[150, 150] / np.linalg.norm(e[150, 150])
    assert back == pytest.approx([0, 0, 1], abs=0.02)


def test_two_d_frame_on_the_front_face(tmp_path):
    r = doc(tmp_path, '<shape id="s" shape="rect" width="320" height="180" fill="#FF0000FF"/>', lights="")
    px = V.render_360(r.rc, 0.0).px
    h, w = px.shape[:2]
    assert px[h // 2, w // 2, 0] > 0.9                                     # front centre
    assert px[h // 2, 0, 3] == 0                                           # back: nothing
    cols = np.nonzero(px[h // 2, :, 0] > 0.5)[0]
    assert (cols.max() - cols.min() + 1) == pytest.approx(w / 4, abs=2)     # frame width spans 90 degrees


def test_objects_render_all_around(tmp_path):
    body = ('<camera x="160" y="90" id="c"/><object3D x="160" y="90" id="f" primitive="sphere" radius="80" z="600" material="u"/>'
            '<object3D x="160" y="90" id="b" primitive="sphere" radius="80" z="-600" material="u"/>'
            '<object3D x="160" id="up" primitive="sphere" radius="80" y="-510" material="u"/>')
    lights = ''
    xml_mat = '<material id="u" baseColor="#FFFFFFFF" unlit="true"/>'
    r = doc(tmp_path, body, lights=lights)
    p = tmp_path / "p.xml"
    p.write_text(p.read_text().replace("<scene360", f"<materials>{xml_mat}</materials>\n  <scene360"))
    r = Renderer.open(str(p))
    px = V.render_360(r.rc, 0.0).px
    h, w = px.shape[:2]
    assert px[h // 2, w // 2, 3] > 0.9          # front (scene +z, engine -Z)
    assert px[h // 2, 1, 3] > 0.9               # back (scene -z) at the left/right edge
    assert px[1, w // 3, 3] > 0.9               # zenith


def test_stereo_disparity(tmp_path):
    body = '<camera x="160" y="90" id="c"/><object3D x="160" y="90" id="n" primitive="box" width="20" height="20" depth="20" z="80" material="u"/>'
    p = tmp_path / "s.xml"
    doc(tmp_path, body, s360='layout="equirectangular" stereo="left-right" width="512" height="128" interpupillary="0.2"',
        lights="", name="s.xml")
    p.write_text(p.read_text().replace("<scene360", '<materials><material id="u" baseColor="#FFFFFFFF" unlit="true"/></materials>\n  <scene360'))
    r = Renderer.open(str(p))
    px = V.render_360(r.rc, 0.0).px[..., 3]
    left, right = px[:, :256], px[:, 256:]
    cl = (left.sum(0) * np.arange(256)).sum() / left.sum()
    cr = (right.sum(0) * np.arange(256)).sum() / right.sum()
    # the left eye sits to the left, so a near object appears further right for it
    assert cl - cr == pytest.approx(math.degrees(2 * math.atan(10 / 80)) / 360 * 256, rel=0.25)
    mono = doc(tmp_path, s360='layout="equirectangular" stereo="top-bottom" width="128" height="128"', name="m.xml")
    tb = V.render_360(mono.rc, 0.0).px
    assert np.abs(tb[:64] - tb[64:]).max() <= 1e-3 + 0.01 * tb[:64].max()   # environment at infinity: no parallax


def test_render360_hook_installed_only_in_equirect_mode(tmp_path):
    assert "render360" in doc(tmp_path).rc.hooks
    assert "render360" not in doc(tmp_path, mode="standard", name="s.xml").rc.hooks


def test_viewport_mode_films_through_the_viewport_camera(tmp_path):
    body = ('<camera x="160" y="90" id="main" z="-800"/><camera y="90" id="vp" active="false" x="460" z="-800"/>'
            '<object3D x="160" y="90" id="o" primitive="sphere" radius="40" material="u"/>')
    p = tmp_path / "v.xml"
    doc(tmp_path, body, s360='viewportCamera="vp"', mode="viewport", lights="", name="v.xml")
    p.write_text(p.read_text().replace("<scene360", '<materials><material id="u" baseColor="#FFFFFFFF" unlit="true"/></materials>\n  <scene360'))
    r = Renderer.open(str(p))
    a = r.rc.render_frame(0.0).px[..., 3]
    xs = np.nonzero(a.any(0))[0]
    assert xs.mean() < 160 - 30                                        # seen from 300 px right of the sphere: left of centre


def test_dome_is_visible_without_a_camera(tmp_path):
    """CONVENTIONS 5.5: a visible dome is seen through the implicit camera when no camera exists,
    exactly as through an explicit camera at the implicit camera's pose."""
    envp = smooth_env(tmp_path)
    lights = f'<light id="d" type="dome" environment="{envp}" environmentVisible="true"/>'
    f = 160 / math.tan(math.radians(30))
    bare = doc(tmp_path, '<object3D id="o" primitive="sphere" radius="1" x="-999" y="-999"/>', mode="standard",
               lights=lights, name="a.xml").rc.render_frame(0.0).px
    cam = doc(tmp_path, f'<camera id="c" fov="60" x="160" y="90" z="{-f}"/>', mode="standard", lights=lights,
              name="b.xml").rc.render_frame(0.0).px
    assert bare[..., 3].min() > 0.99
    assert np.abs(bare - cam).max() < 1e-3
