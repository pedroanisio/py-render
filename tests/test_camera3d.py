"""Camera maths: projection, switching, shake, lens fit, 3D transforms/constraints, 2.5D homographies."""
from __future__ import annotations

import math
import textwrap

import numpy as np
import pytest

from scenerender import camera as C
from scenerender.evaluator import Ctx
from scenerender.render import Renderer


def doc(tmp_path, body: str, *, w=320, h=180, extra="", project="", name="c.xml") -> Renderer:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="{w}" height="{h}" fps="10" duration="6" seed="5" background="#000000FF" {project}/>
  {extra}
  <composition>
{textwrap.indent(body, '    ')}
  </composition>
</scene>"""
    p = tmp_path / name
    p.write_text(xml)
    return Renderer.open(str(p))


def ctx(t=0.0):
    return Ctx(t=t, comp_t=t)


# ---------------------------------------------------------------- projection
def test_pinhole_projection_of_known_point(tmp_path):
    r = doc(tmp_path, '<camera id="c" x="160" y="90" z="-1000" fov="60"/>')     # engine (0, 0, 1000)
    cam = C.camera_at(r.rc, 0.0)
    f = 160 / math.tan(math.radians(30))
    s, ok = cam.project(np.array([100.0, 50.0, 0.0]))
    assert ok
    assert s == pytest.approx([160 + f * 0.1, 90 - f * 0.05], abs=1e-9)


def test_yaw_pitch_roll_orientation(tmp_path):
    r = doc(tmp_path, '<camera id="c" yaw="90"/><camera id="d" pitch="90" start="1"/>'
                      '<camera id="e" x="160" y="90" roll="90" start="2"/>')
    assert C.camera_at(r.rc, 0.5).fwd == pytest.approx([1, 0, 0], abs=1e-9)     # yaw > 0 turns right
    assert C.camera_at(r.rc, 1.5).fwd == pytest.approx([0, 1, 0], abs=1e-9)     # pitch > 0 looks up
    e = C.camera_at(r.rc, 2.5)
    s, _ = e.project(np.array([0.0, 100.0, -1000.0]))                          # a point above centre...
    assert s[0] < 160 - 1 and abs(s[1] - 90) < 1e-6                            # ...moves left: camera turns clockwise,
    #                                                                            the picture counter-clockwise


def test_target_look_at_and_focus_target(tmp_path):
    r = doc(tmp_path, '<camera id="c" x="460" y="-110" z="-500" target="o" focusTarget="o" depthOfField="true"/>'
                      '<object3D id="o" primitive="sphere" x="60" y="90" z="200"/>')      # engine (300, 200, 500), (-100, 0, -200)
    cam = C.camera_at(r.rc, 0.0)
    d = np.array([-400.0, -200.0, -700.0])
    assert cam.fwd == pytest.approx(d / np.linalg.norm(d))
    assert cam.focus == pytest.approx(np.linalg.norm(d))
    s, _ = cam.project(np.array([-100.0, 0, -200]))
    assert s == pytest.approx([160, 90], abs=1e-6)


def test_focal_length_is_horizontal_on_sensor_width(tmp_path):
    r = doc(tmp_path, '<camera id="c" focalLength="50" sensorWidth="36" sensorHeight="24"/>')
    assert C.camera_at(r.rc, 0).fpx == pytest.approx(50 * 320 / 36)             # fov = 2 atan(sw / 2f) across W
    r2 = doc(tmp_path, '<camera id="c" focalLength="50" sensorWidth="36" sensorHeight="24"/>', w=180, h=320, name="p.xml")
    assert C.camera_at(r2.rc, 0).fpx == pytest.approx(50 * 180 / 36)            # portrait too: no film-fit switch
    assert C.camera_at(r2.rc, 0).px_per_mm == pytest.approx(180 / 36)


def test_implicit_camera_is_60_degrees_horizontal(tmp_path):
    r = doc(tmp_path, '<shape id="s" shape="rect" width="1" height="1"/>')
    cam = C.camera_at(r.rc, 0)
    f = 160 / math.tan(math.radians(30))
    assert cam.fpx == pytest.approx(f)
    assert cam.eye == pytest.approx([0, 0, f])                                  # scene (W/2, H/2, -f)
    assert cam.focal_mm == pytest.approx(f * 36 / 320)


def test_orthographic_ortho_height(tmp_path):
    r = doc(tmp_path, '<camera id="c" projection="orthographic" orthoHeight="360" x="160" y="90" z="-500"/>')
    cam = C.camera_at(r.rc, 0)
    s, _ = cam.project(np.array([90.0, 45.0, -3000.0]))
    assert s == pytest.approx([160 + 45, 90 - 22.5])


def test_near_far_and_exposure_reach_the_renderer(tmp_path):
    r = doc(tmp_path, '<camera id="c" near="5" far="2000" exposure="1.5"/>')
    cam = C.camera_at(r.rc, 0)
    assert (cam.near, cam.far, cam.exposure) == (5, 2000, 1.5)
    P = cam.proj_matrix()
    for z, ndc in ((-5.0, -1.0), (-2000.0, 1.0)):
        v = P @ np.array([0, 0, z, 1.0])
        assert v[2] / v[3] == pytest.approx(ndc)


def test_camera_switching_with_windows_and_viewport_mode(tmp_path):
    body = ('<camera id="a" z="500"/><group id="g" start="2" end="3"><camera id="b" z="800"/></group>'
            '<camera id="c" z="900" start="4" active="false"/>')
    r = doc(tmp_path, body)
    assert [C.active_camera(r.rc, t).get("id") for t in (1, 2.5, 3.0, 4.5)] == ["a", "b", "a", "a"]
    r2 = doc(tmp_path, body, project='mode="viewport"', extra='<scene360 viewportCamera="c"/>', name="v.xml")
    assert C.active_camera(r2.rc, 1).get("id") == "c"


def test_shake_is_seeded_and_windowed(tmp_path):
    body = '<camera id="c" x="160" y="90" z="-800"><shake amplitude="20" frequency="3" rotation="2" zoom="0.1" seed="{s}" start="1" end="3"/></camera>'
    a, b = doc(tmp_path, body.format(s=7), name="a.xml"), doc(tmp_path, body.format(s=7), name="b.xml")
    c = doc(tmp_path, body.format(s=8), name="c2.xml")
    ea, eb, ec = (C.camera_at(x.rc, 2.2) for x in (a, b, c))
    assert np.allclose(ea.eye, eb.eye) and ea.fpx == eb.fpx
    assert not np.allclose(ea.eye, ec.eye)
    assert not np.allclose(ea.eye, [0, 0, 800])
    assert np.allclose(C.camera_at(a.rc, 0.5).eye, [0, 0, 800])                # before start
    assert np.allclose(C.camera_at(a.rc, 3.0).eye, [0, 0, 800])                # [start, end)


def test_shutter_angle_override(tmp_path):
    r = doc(tmp_path, '<camera id="c" shutterAngle="90"/><camera id="d" start="2"/>')
    assert C.shutter_angle(r.rc, 1.0, 180.0) == 90.0
    assert C.shutter_angle(r.rc, 2.5, 180.0) == 180.0


def test_coc_thin_lens():
    cam = C.Camera(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, -1.0]), 1000, False, 1,
                   1920, 1080, dof=True, focus=500, fstop=2, focal_mm=50, px_per_mm=1920 / 36)
    assert cam.coc_px(500.0) == pytest.approx(0.0)
    d, s, f = 10000.0, 5000.0, 50.0                                             # mm (1 unit = 10 mm)
    expect = 0.5 * (f * f / 2) * abs(d - s) / (d * (s - f)) * 1920 / 36
    assert cam.coc_px(1000.0) == pytest.approx(expect)


# ---------------------------------------------------------------- 3D transforms and constraints
def test_object_transform_order_and_parent(tmp_path):
    r = doc(tmp_path, '<object3D id="p" primitive="box" x="260" y="90" rotationY="-90"/>'     # engine x = 100
                      '<object3D id="o" primitive="box" x="50" parent="p" scaleX="2"/>')
    M = C.world3d(r.rc, r.doc.ids["o"], ctx())
    assert M[:3, 3] == pytest.approx([100, 0, -50], abs=1e-9)                 # +X of p turned to -Z
    assert np.linalg.norm(M[:3, 0]) == pytest.approx(2)
    plane = doc(tmp_path, '<object3D id="f" primitive="plane" rotationX="-90"/>', name="f.xml")
    Mf = C.world3d(plane.rc, plane.doc.ids["f"], ctx())
    assert Mf[:3, 2] == pytest.approx([0, 1, 0], abs=1e-9)                    # rotationX=-90 faces up (engine +Y)


def test_constraints_3d(tmp_path):
    r = doc(tmp_path, """
<object3D id="t" primitive="sphere" x="460" y="-10" z="400"/>
<object3D id="look" primitive="box" x="160" y="90"><transformConstraint type="look-at" target="t"/></object3D>
<object3D id="copy" primitive="box" x="5"><transformConstraint type="copy-position" target="t" offsetX="10"/></object3D>
<object3D id="half" primitive="box" x="160" y="90"><transformConstraint type="copy-position" target="t" influence="0.5"/></object3D>
<object3D id="dist" primitive="box" x="460" y="-10" z="-600"><transformConstraint type="distance" target="t" maxDistance="200"/></object3D>
<camera id="cam" x="160" y="90" z="-900"><transformConstraint type="look-at" target="t"/></camera>
<object3D id="path" primitive="box"><transformConstraint type="follow-path" path="M0 0 L320 0" progress="0.5"/></object3D>""")
    ids = r.doc.ids
    t = np.array([300.0, 100, -400])                                            # engine space
    Ml = C.world3d(r.rc, ids["look"], ctx())
    assert Ml[:3, 2] == pytest.approx(t / np.linalg.norm(t))                  # object +Z toward the target
    assert C.world3d(r.rc, ids["copy"], ctx())[:3, 3] == pytest.approx(t + [10, 0, 0])
    assert C.world3d(r.rc, ids["half"], ctx())[:3, 3] == pytest.approx(t / 2)
    assert np.linalg.norm(C.world3d(r.rc, ids["dist"], ctx())[:3, 3] - t) == pytest.approx(200)
    cam = C.camera_at(r.rc, 0)
    assert cam.fwd == pytest.approx((t - [0, 0, 900]) / np.linalg.norm(t - [0, 0, 900]))
    assert C.world3d(r.rc, ids["path"], ctx())[:3, 3] == pytest.approx([0, 90, 0])   # frame (160, 0) -> world


@pytest.mark.parametrize("rig,cam", [('x="420" y="180" z="-554.256"', ''), ('x="320" y="180" z="-554.256"', 'x="100"')])
def test_parent_constraint_camera_is_in_the_targets_frame(tmp_path, rig, cam):
    """CONVENTIONS 5.4: a camera parented through transformConstraint type=parent has x/y/z in the
    rig's frame and the frame offset is applied once: the eye is at the rig (+ its own offset)."""
    r = doc(tmp_path, f'<object3D id="rig" primitive="sphere" radius="1" visible="false" {rig}/>'
                      f'<camera id="cam" fov="60" {cam}><transformConstraint type="parent" target="rig"/></camera>',
            w=640, h=360)
    c = C.camera_at(r.rc, 0.0)
    assert c.eye == pytest.approx([100.0, 0.0, 554.256], abs=1e-6)             # scene (420, 180, -554.256)
    s, ok = c.project(C.frame_to_world(r.rc, np.array([420.0, 180.0]), 0.0))
    assert ok and s == pytest.approx([320, 180], abs=1e-3)


def test_parent_constraint_replaces_the_parent_chain(tmp_path):
    """D23: the constrained element composes onto the target's world transform instead of its
    @parent's; influence blends the two."""
    r = doc(tmp_path, '<object3D id="a" primitive="sphere" x="100" y="50"/><object3D id="b" primitive="sphere" x="200" y="50"/>'
                      '<object3D id="o" primitive="box" x="10" parent="a"><transformConstraint type="parent" target="b"/></object3D>'
                      '<object3D id="h" primitive="box" x="10" parent="a"><transformConstraint type="parent" target="b" influence="0.5"/></object3D>')
    ids = r.doc.ids
    wb = C.world3d(r.rc, ids["b"], ctx())[:3, 3]
    wa = C.world3d(r.rc, ids["a"], ctx())[:3, 3]
    assert C.world3d(r.rc, ids["o"], ctx())[:3, 3] == pytest.approx(wb + [10, 0, 0])
    assert C.world3d(r.rc, ids["h"], ctx())[:3, 3] == pytest.approx((wa + wb) / 2 + [10, 0, 0])


# ---------------------------------------------------------------- 2.5D
def _expected_corners(rc, el, M, cam, size, rxd, ryd, zd, ax, ay):
    """Independent re-derivation of the documented 2.5D convention."""
    Md = np.linalg.inv(rc.root_matrix) @ M
    w, h = size
    W, H = rc.doc.width, rc.doc.height
    pts = []
    pv = Md @ [ax, ay, 1]
    pivot = np.array([pv[0] - W / 2, H / 2 - pv[1], -zd])
    for u, v in ((0, 0), (w, 0), (w, h), (0, h)):
        q = Md @ [u, v, 1]
        P = np.array([q[0] - W / 2, H / 2 - q[1], -zd]) - pivot
        a = math.radians(-rxd)
        P = np.array([P[0], P[1] * math.cos(a) - P[2] * math.sin(a), P[1] * math.sin(a) + P[2] * math.cos(a)])
        b = math.radians(ryd)
        P = np.array([P[0] * math.cos(b) + P[2] * math.sin(b), P[1], -P[0] * math.sin(b) + P[2] * math.cos(b)])
        P = P + pivot
        d = P - cam.eye
        x, y, z = d @ cam.right, d @ cam.up, d @ cam.fwd
        pts.append([cam.ppx + cam.fpx * x / z, cam.ppy - cam.fpx * y / z])
    return (np.c_[np.array(pts), np.ones(4)] @ rc.root_matrix.T)[:, :2]


def test_threed_homography_matches_projected_corners(tmp_path):
    r = doc(tmp_path, '<camera id="c" x="200" y="120" z="-700" yaw="8" pitch="-5" roll="-3" fov="50"/>'
                      '<shape id="s" shape="rect" x="60" y="40" width="120" height="70" anchorX="30" anchorY="20" '
                      'threeD="true" rotationX="25" rotationY="-40" zDepth="150" rotation="10" fill="#FFFFFFFF"/>')
    r.rc.scale = 1.0
    el = r.doc.ids["s"]
    c = r.rc.node_ctx(el, ctx())
    M = r.rc.world_matrix(el, c)
    H = C.camera_hook(r.rc, el, M, c)
    assert abs(H[2, 0]) + abs(H[2, 1]) > 1e-6                                   # truly projective
    loc = np.array([[0, 0, 1], [120, 0, 1], [120, 70, 1], [0, 70, 1]], np.float64)
    q = loc @ H.T
    got = q[:, :2] / q[:, 2:]
    cam = C.camera_at(r.rc, 0)
    exp = _expected_corners(r.rc, el, M, cam, (120, 70), 25, -40, 150, 30, 20)
    assert got == pytest.approx(exp, abs=1e-6)
    assert C.project_quad(r.rc, el, M, c) == pytest.approx(exp, abs=1e-6)


def test_threed_fronto_parallel_is_affine_and_behind_is_culled(tmp_path):
    r = doc(tmp_path, '<camera id="c" x="160" y="90" z="-1000" fov="60"/>'
                      '<shape id="s" shape="rect" width="50" height="50" threeD="true" zDepth="1000"/>'
                      '<shape id="b" shape="rect" width="50" height="50" threeD="true" zDepth="-2000"/>')
    s, b = r.doc.ids["s"], r.doc.ids["b"]
    Ms = C.camera_hook(r.rc, s, r.rc.world_matrix(s, ctx()), ctx())
    assert Ms[2].tolist() == [0.0, 0.0, 1.0]
    assert C.camera_hook(r.rc, b, r.rc.world_matrix(b, ctx()), ctx()) is None


def test_collapse_composes_group_3d_transform(tmp_path):
    r = doc(tmp_path, '<camera id="c" x="160" y="90" z="-900"/>'
                      '<group id="g" x="100" y="50" width="100" height="80" threeD="true" rotationY="30" zDepth="100" collapse="true">'
                      '<shape id="k" shape="rect" width="40" height="40" threeD="true" zDepth="20" fill="#FFFFFFFF"/></group>')
    rc = r.rc
    g, k = r.doc.ids["g"], r.doc.ids["k"]
    G = C.plane_affine(rc, k, rc.world_matrix(k, ctx()), rc.node_ctx(k, ctx()))
    # child origin: frame (100, 50) at depth 100 + 20, turned 30 degrees about the group's anchor (its origin)
    pv = C.frame_to_world(rc, np.array([100.0, 50.0]), 100.0)
    p0 = C.frame_to_world(rc, np.array([100.0, 50.0]), 120.0) - pv
    b = math.radians(30)
    exp = pv + np.array([p0[0] * math.cos(b) + p0[2] * math.sin(b), p0[1], -p0[0] * math.sin(b) + p0[2] * math.cos(b)])
    assert G @ [0, 0, 1] == pytest.approx(exp)
    assert C.is_threed(rc, k) and C.is_threed(rc, g)
    # routed (the core default): the group is not projected, its children are
    M = rc.world_matrix(k, ctx())
    Mg = rc.world_matrix(g, ctx())
    assert np.allclose(C.camera_hook(rc, g, Mg, ctx()), Mg)
    assert not np.allclose(C.camera_hook(rc, k, M, ctx()), M)
    # fallback without routing: the group is flattened, the child left 2D
    rc.threed_routing = False
    try:
        assert np.allclose(C.camera_hook(rc, k, M, ctx()), M)
    finally:
        rc.threed_routing = True


def test_depth_sort_hook_orders_farthest_first(tmp_path):
    r = doc(tmp_path, '<group id="g" collapse="true">'
                      '<shape id="a" shape="rect" width="10" height="10" threeD="true" zDepth="-100"/>'
                      '<shape id="flat" shape="rect" width="10" height="10"/>'
                      '<shape id="b" shape="rect" width="10" height="10" threeD="true" zDepth="300"/>'
                      '<object3D id="o" primitive="sphere" x="160" y="90" z="600"/></group>')
    g = r.doc.ids["g"]
    order = r.rc.child_order(g)
    slots = [i for i, e in enumerate(order) if e.get("id") != "flat"]
    out = C.depth_sort(r.rc, g, order, ctx())
    assert out[[i for i, e in enumerate(order) if e.get("id") == "flat"][0]].get("id") == "flat"   # 2D keeps its slot
    assert [out[i].get("id") for i in slots] == ["o", "b", "a"]                                   # far -> near


def test_lens_distortion_barrel_pulls_points_inward(tmp_path):
    from scenerender.raster import Buf
    r = doc(tmp_path, '<camera id="c" lensDistortion="-0.2"/>')
    cam = C.camera_at(r.rc, 0)
    px = np.zeros((180, 320, 4), np.float32)
    px[20:24, 280:284] = 1.0                                                   # near the top-right corner
    out = C.lens_distort(r.rc, Buf(px, 0, 0), cam).px
    ys, xs = np.nonzero(out[..., 3] > 0.25)
    assert xs.mean() < 281.5 and ys.mean() > 21.5                              # moved toward the centre
    assert 0.2 * px[..., 3].sum() < out[..., 3].sum() < px[..., 3].sum()          # barrel minifies the periphery


def test_threed_depth_of_field_blurs_out_of_focus_plane(tmp_path):
    body = ('<camera id="c" x="160" y="90" z="-600" fov="40" depthOfField="true" fStop="0.8" focusDistance="{f}"/>'
            '<shape id="s" shape="rect" x="140" y="70" width="40" height="40" threeD="true" zDepth="-300" fill="#FFFFFFFF"/>')
    sharp = doc(tmp_path, body.format(f=300), name="a.xml").frame_rgb(0.0)[..., 0].astype(float)
    soft = doc(tmp_path, body.format(f=3000), name="b.xml").frame_rgb(0.0)[..., 0].astype(float)
    def edge(img):
        return np.abs(np.diff(img, axis=1)).max()
    assert edge(soft) < edge(sharp) * 0.6
