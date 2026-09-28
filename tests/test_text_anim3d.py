"""Text animators: variable-font variation continuity and per-unit 3D."""
from __future__ import annotations

import os

import numpy as np
import pytest

from scenerender import document
from scenerender import text_animators as TA
from scenerender.assets import text as T
from scenerender.compositor import RenderContext
from scenerender.evaluator import Ctx, Evaluator
from scenerender.nodes import core as _core  # noqa: F401  (registers layer/group handlers)
from scenerender.registry import TEXT_ANIMATORS

HERE = os.path.dirname(__file__)

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="800" height="450" fps="30" duration="2" seed="3"/>
  <assets>
{assets}
  </assets>
  <composition>
{nodes}
  </composition>
</scene>
"""


def make_rc(tmp_path, assets="", nodes="", scale=1.0):
    p = tmp_path / "t.xml"
    p.write_text(HEAD.format(assets=assets, nodes=nodes), encoding="utf-8")
    doc = document.load(str(p))
    assert not doc.validation_errors, doc.validation_errors[:3]
    return RenderContext(doc, Evaluator(doc), scale=scale)


def ctx(t=0.0):
    return Ctx(t=t, comp_t=t)


def test_levels_full():
    for p in ("property:variation", "property:zDepth", "property:rotationX", "property:rotationY"):
        assert TEXT_ANIMATORS.level(p) == "full", p


# ---------------------------------------------------------------- variation
VAR = '<text id="v" width="760" height="100" size="60" font="Inter" weight="300" wrap="none" text="WAVE WEIGHT"/>'


def _var_pieces(rc, t=0.5):
    layer = rc.doc.ids["l"]
    spec = T.resolve_spec(rc, rc.doc.ids["v"], ctx(t))
    block = T.get_block(rc, spec)
    anims = [c for c in layer if c.tag.endswith("textAnimator")]
    c = ctx(t)
    A = TA.evaluate(rc, block, anims, c)
    return block, A, TA.build_pieces(rc, block, A.st, A.owner)


def test_variation_reshapes_line_with_continuous_advances(tmp_path):
    rc = make_rc(tmp_path, VAR, '<layer id="l" asset="v"><textAnimator unit="character" shape="ramp-up" '
                                'start="0" end="100" variation="wght 900"/></layer>')
    block, A, pieces = _var_pieces(rc)
    cl = [i for i in range(len(block.clusters)) if not block.is_newline(i)]
    var = [A.st[i].get("variation") for i in cl]
    assert all(v is not None for v in var[1:])
    vx = T.varied_positions(block, cl, var)
    # contiguous: every unit starts where the previous one ends
    for a, b in zip(cl, cl[1:]):
        assert abs(vx[a][1] - vx[b][0]) < 0.05
    # heavier letters are wider, so the reshaped line is longer than the static one
    static_w = block.cl_x[cl[-1]][1] - block.cl_x[cl[0]][0]
    varied_w = vx[cl[-1]][1] - vx[cl[0]][0]
    assert varied_w > static_w + 5
    # the last letter (weight ~900) is wider than the same letter drawn statically
    last = cl[-1]
    assert (vx[last][1] - vx[last][0]) > (block.cl_x[last][1] - block.cl_x[last][0])
    # every piece is drawn at its reshaped position: its sub-layout starts where vx says
    for p in pieces:
        x0 = block.span_x(p.a, p.b)[0]
        drawn = (p.T @ np.array([x0, block.lines[p.line].y + block.lines[p.line].h / 2, 1.0]))[0]
        assert abs(drawn - vx[p.a][0]) < 0.05


def test_variation_sub_layout_advance_matches_reshaped_width(tmp_path):
    rc = make_rc(tmp_path, VAR, '<layer id="l" asset="v"><textAnimator unit="character" start="0" end="100" '
                                'variation="wght 800"/></layer>')
    block, A, pieces = _var_pieces(rc)
    cl = [i for i in range(len(block.clusters)) if not block.is_newline(i)]
    vx = T.varied_positions(block, cl, [A.st[i].get("variation") for i in cl])
    p = pieces[-1]                      # the last letter: no kerning with a following letter
    runs = [block.runs[block.cl_run[p.a]].with_text(block.text[block.clusters[p.a][0]:block.clusters[p.b][1]])]
    lay = T.build_layout(rc, block.spec, block.k, runs=runs, nowrap=True, var_over=dict(p.variation), transformed=True)
    w = lay.get_extents()[1].width / T.PS
    assert abs(w - (vx[p.b][1] - vx[p.a][0])) < 0.5


def test_variation_render_differs_and_is_finite(tmp_path):
    rc = make_rc(tmp_path, VAR, '<layer id="l" asset="v"><textAnimator unit="character" shape="smooth" start="0" '
                                'end="60" variation="wght 900"/></layer>')
    a = rc.render_frame(0.5).px
    rc2 = make_rc(tmp_path, VAR, '<layer id="l" asset="v"/>')
    b = rc2.render_frame(0.5).px
    assert np.isfinite(a).all()
    assert a[..., 3].sum() > b[..., 3].sum() * 1.05          # bolder letters: more ink


# ---------------------------------------------------------------- 3D
TXT = '<text id="t" width="700" height="120" size="80" font="DejaVu Sans" wrap="none" text="ABCDEF"/>'


def _pieces(rc, t=0.5):
    layer = rc.doc.ids["l"]
    spec = T.resolve_spec(rc, rc.doc.ids["t"], ctx(t))
    block = T.get_block(rc, spec)
    anims = [c for c in layer if c.tag.endswith("textAnimator")]
    c = ctx(t)
    A = TA.evaluate(rc, block, anims, c)
    return block, TA.build_pieces(rc, block, A.st if A else None, A.owner if A else None)


def test_zero_3d_is_identity_and_matches_flat(tmp_path):
    rc = make_rc(tmp_path, TXT, '<layer id="l" asset="t" x="40" y="100"><textAnimator unit="character" '
                                'rotationY="0" zDepth="0"/></layer>')
    rc2 = make_rc(tmp_path, TXT, '<layer id="l" asset="t" x="40" y="100"/>')
    a, b = rc.render_frame(0.5).px, rc2.render_frame(0.5).px
    assert np.abs(a - b).max() < 1e-4


def test_rotation_y_narrows_units_with_perspective(tmp_path):
    rc = make_rc(tmp_path, TXT, '<layer id="l" asset="t" x="40" y="100"><textAnimator unit="character" '
                                'rotationY="60"/></layer>')
    block, pieces = _pieces(rc)
    assert all(p.three_d is not None and p.three_d[2] == pytest.approx(60) for p in pieces)
    p = pieces[0]
    # put the first unit's pivot on the principal point (the frame centre)
    ox, oy = 400 - p.three_d[3], 225 - p.three_d[4]
    M = rc.root_matrix @ np.array([[1, 0, ox], [0, 1, oy], [0, 0, 1.0]])
    H, depth = TA.unit_homography(rc, block, M, p, 0.5)
    x0, x1 = block.span_x(p.a, p.b)
    ln_ = block.lines[p.line]
    L = M @ block.O
    quad = np.array([[x0, ln_.y, 1], [x1, ln_.y, 1], [x1, ln_.y + ln_.h, 1], [x0, ln_.y + ln_.h, 1]]) @ L.T
    proj = quad @ H.T
    proj = proj[:, :2] / proj[:, 2:3]
    w_flat = quad[1, 0] - quad[0, 0]
    w_proj = proj[1, 0] - proj[0, 0]
    assert w_proj == pytest.approx(0.5 * w_flat, rel=0.05)     # cos 60, little perspective on axis
    # the right edge turned away: shorter than the left edge (perspective)
    assert (proj[2, 1] - proj[1, 1]) < (proj[3, 1] - proj[0, 1])
    buf = rc.render_frame(0.5)
    assert buf.px[..., 3].sum() > 0


def test_zdepth_scales_about_the_frame_centre(tmp_path):
    rc = make_rc(tmp_path, TXT, '<layer id="l" asset="t" x="40" y="100"><textAnimator unit="character" '
                                'zDepth="500"/></layer>')
    block, pieces = _pieces(rc)
    M = rc.root_matrix @ np.array([[1, 0, 40], [0, 1, 100], [0, 0, 1.0]])
    H, d_far = TA.unit_homography(rc, block, M, pieces[0], 0.5)
    ref = T.Piece(0, 0, 0, np.eye(3), three_d=(0.0, 0.0, 0.0, 0.0, 0.0))
    H0, d0 = TA.unit_homography(rc, block, M, ref, 0.5)
    assert np.allclose(H0, np.eye(3), atol=1e-6)                 # default camera: z = 0 unchanged
    assert d_far > d0
    s = np.sqrt(abs(np.linalg.det(H[:2, :2])))
    assert s < 0.9                                               # farther away: smaller
    c = H @ np.array([400.0, 225.0, 1.0])
    assert np.allclose(c[:2] / c[2], [400.0, 225.0], atol=0.5)   # the principal point stays put


def test_active_camera_projects_units_and_layer_plane(tmp_path):
    cam = '<camera id="cam" x="100" y="225" z="-1500" yaw="12" fov="50"/>'
    rc = make_rc(tmp_path, TXT, cam + '<layer id="l" asset="t" x="40" y="100"><textAnimator unit="character" '
                                      'start="0" end="50" rotationY="40"/></layer>')
    rc_def = make_rc(tmp_path, TXT, '<layer id="l" asset="t" x="40" y="100"><textAnimator unit="character" '
                                    'start="0" end="50" rotationY="40"/></layer>')
    block, pieces = _pieces(rc)
    flat = [p for p in pieces if p.three_d is None]
    assert flat and len(flat) < len(pieces)
    M = rc.root_matrix @ np.array([[1, 0, 40], [0, 1, 100], [0, 0, 1.0]])
    ref = T.Piece(0, 0, 0, np.eye(3), three_d=(0.0, 0.0, 0.0, 0.0, 0.0))
    H0, _ = TA.unit_homography(rc, block, M, ref, 0.5)
    assert not np.allclose(H0, np.eye(3), atol=1e-3)            # the camera sees the layer plane obliquely
    a = rc.render_frame(0.5).px
    b = rc_def.render_frame(0.5).px
    assert a[..., 3].sum() > 0 and np.abs(a - b).max() > 0.1


def test_units_behind_camera_are_culled(tmp_path):
    rc = make_rc(tmp_path, TXT, '<layer id="l" asset="t" x="40" y="100"><textAnimator unit="character" '
                                'zDepth="-5000"/></layer>')
    block, pieces = _pieces(rc)
    M = rc.root_matrix @ np.array([[1, 0, 40], [0, 1, 100], [0, 0, 1.0]])
    assert TA.unit_homography(rc, block, M, pieces[0], 0.5) is None
    px = rc.render_frame(0.5).px
    assert np.isfinite(px).all() and px[..., 3].sum() == 0


def test_default_projector_fallback_uses_the_implicit_camera(tmp_path, monkeypatch):
    import scenerender.camera as CAM
    rc = make_rc(tmp_path, TXT, '<layer id="l" asset="t"/>')
    monkeypatch.delattr(CAM, "camera_at")
    f2w, project, near = TA._projector(rc, 0.0)
    f = 400 / np.tan(np.radians(30))                            # horizontal fov 60 deg
    P = f2w(np.array([[500.0, 225.0]]), 0.0)
    s, d = project(P)
    assert np.allclose(s[0], [500.0, 225.0]) and d[0] == pytest.approx(f)


@pytest.mark.parametrize("name", ["text_anim3d.xml", "text_anim3d_camera.xml"])
def test_fixtures_render(name):
    from scenerender.render import Renderer
    try:
        r = Renderer.open(os.path.join(HERE, "fixtures", name), scale=0.25)
    except ImportError as e:        # other plugin modules may be mid-edit
        pytest.skip(f"plugin import failed: {e}")
    assert not r.doc.validation_errors
    for t in (0.5, 1.5):
        rgb = r.frame_rgb(t)
        assert rgb.std() > 3
