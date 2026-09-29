"""3D node kinds: camera, skeleton and object3D.

  * object3D: rendered by the moderngl PBR renderer (scenerender.three); the handler returns the
    object's own layer (premultiplied working-space radiance at frame resolution, occluded by the
    other opaque objects of its scene) which the compositor places in stacking order. Every
    attribute's semantics are pinned in scenerender.three.* module docstrings. Without GL 4.1 a flat
    painter's-algorithm fallback draws primitives (warned once).
  * camera: no geometry. The active camera's layer is the visible dome environment
    (light type="dome" environmentVisible="true") when there is one, composited with blend "behind":
    the environment is infinitely far, so it is the backdrop of everything in the camera's parent
    buffer (nodes stacked after the camera still draw over it normally). Without an active camera the
    dome is seen through the implicit camera beneath the whole composition (hook `backdrop`). The
    camera drives the 2.5D projection and the 3D renderer.
  * skeleton: not drawn; bones are read by deform modifier type=skin.
"""
from __future__ import annotations

import cairo
import numpy as np

from .. import camera as cam3d
from .. import deform, modifiers, physics  # noqa: F401  (register their handlers / hooks)
from ..compositor import Out, RenderContext
from ..evaluator import Ctx
from ..raster import Canvas
from ..registry import FEATURES, FULL, NODES, warn_once
from ..three import lights, materials, view360
from ..values import parse_bool  # noqa: F401  (feature declarations; render360 hook)

for _p in ("sphere", "box", "plane", "cylinder", "cone", "torus", "capsule", "text", "extrude"):
    FEATURES.declare(f"object3D:{_p}", FULL, "tessellated, PBR-shaded, shadowed")
FEATURES.declare("object3D:mesh", FULL, "glTF/GLB (hierarchy, skins, clips, morphs, variants), OBJ/PLY/STL, "
                                         "USD/USDZ (incl. UsdSkel), FBX (ufbx), Gaussian splats (.splat / PLY)")
FEATURES.declare("object3D:instances", FULL, "GPU instancing; per-copy index/count variables")
FEATURES.declare("project:antialias3d", FULL, "4x MSAA plus N x N supersampling of 3D passes")


def _dome_visible(rc) -> bool:
    from ..three import lights as L3
    return any(L.get("type") == "dome" and parse_bool(L.get("environmentVisible")) for L in L3.light_elements(rc))


@NODES.register("camera", level=FULL, note="films the 3D scene and 2.5D layers; its layer is the visible dome background")
def render_camera(rc: RenderContext, el, ctx: Ctx, M, size):
    if rc.cache.get("pass360") or cam3d.active_camera(rc, ctx.comp_t) is not el or not _dome_visible(rc):
        return None
    from ..three import scene
    if not scene.gl_ok():
        return None
    buf = scene.render_background_layer(rc, ctx.comp_t, scene.scene_root(el))
    return None if buf is None else Out(buf, "behind", 1.0)


def dome_backdrop(rc: RenderContext, t: float):
    """Hook `backdrop`: without an active camera the visible dome environment is still seen, through
    the implicit camera (CONVENTIONS 5.5), beneath the whole composition (what the active camera's
    layer does in its parent buffer). None when a camera is active or no dome is visible."""
    if rc.cache.get("pass360") or rc.scene_context is not None or not _dome_visible(rc):
        return None
    if cam3d.active_camera(rc, t) is not None:
        return None
    from ..three import scene
    if not scene.gl_ok():
        return None
    return scene.render_background_layer(rc, t, None)


@NODES.register("skeleton", level=FULL, note="not drawn; bones are read by deform modifier type=skin")
def render_skeleton(rc: RenderContext, el, ctx: Ctx, M, size):
    return None


@NODES.register("object3D", level=FULL, note="moderngl forward PBR layer per node (see scenerender.three)")
def render_object3d(rc: RenderContext, el, ctx: Ctx, M, size):
    if rc.cache.get("pass360") == "flat":
        return None
    from ..three import scene
    if scene.gl_ok():
        return scene.render_node_layer(rc, el, ctx)
    return _cpu_fallback(rc, el, ctx)


# ------------------------------------------------------------------ CPU fallback (no GL)
LIGHT = np.array([-0.45, 0.7, 0.55]) / np.linalg.norm([-0.45, 0.7, 0.55])


def _cpu_fallback(rc: RenderContext, el, ctx: Ctx):
    """Flat-shaded painter's-algorithm drawing of primitive meshes (used only without GL 4.1)."""
    from ..raster import srgb_to_linear
    from ..three.scene import primitive_geo
    from ..three.materials import material_for
    cam = cam3d.camera_at(rc, ctx.comp_t)
    _, g = primitive_geo(rc, el, ctx)
    if g is None:
        warn_once("object3D", el.get("primitive", "?"), "not drawn by the CPU fallback")
        return None
    m = material_for(rc, el, ctx)
    base = np.array(m.p["baseColor"][:3]) if m is not None else srgb_to_linear(np.array([0.8, 0.8, 0.82]))
    emis = np.array(m.p["emissive"][:3]) * m.p["emissiveStrength"] if m is not None else np.zeros(3)
    unlit = bool(m.p["unlit"]) if m is not None else False
    W = cam3d.world3d(rc, el, ctx)
    P = np.c_[g.positions, np.ones(len(g.positions))] @ W.T
    tri = P[g.indices][:, :, :3]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    cen = tri.mean(1)
    view = (cam.eye - cen) if not cam.ortho else -cam.fwd[None]
    keep = np.einsum("ij,ij->i", n, view) > 0
    C = cam.to_cam(tri[keep].reshape(-1, 3)).reshape(-1, 3, 3)
    front = (C[:, :, 2] > cam.near).all(1) | cam.ortho
    C, nn = C[front], n[keep][front]
    if not len(C):
        return None
    order = np.argsort(-C[:, :, 2].mean(1))
    cv = Canvas((0, 0, rc.width, rc.height))
    cr = cv.cr
    cr.set_antialias(cairo.ANTIALIAS_GOOD)
    Rm = rc.root_matrix
    # Project and shade every triangle at once; only the cairo drawing stays per triangle.
    C = C[order]
    S = cam.project_cam(C)
    S = np.concatenate([S, np.ones(S.shape[:2] + (1,))], -1) @ Rm.T
    lam = np.maximum(0.0, nn[order] @ LIGHT)
    cols = np.broadcast_to(base, (len(C), 3)) if unlit else base * (0.35 + 0.65 * lam[:, None]) + emis
    cols = np.clip(cols, 0, 1)
    if not rc.linear:
        from ..raster import linear_to_srgb
        cols = linear_to_srgb(cols.astype(np.float32))
    cr.set_line_width(0.5)
    for s, col in zip(S[:, :, :2].tolist(), cols.tolist()):
        cr.move_to(*s[0])
        for p in s[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.set_source_rgba(col[0], col[1], col[2], 1.0)
        cr.fill_preserve()
        cr.stroke()
    buf = cv.to_buf(False)
    if rc.linear:
        from ..raster import srgb_to_linear as s2l
        # Only the drawn pixels need converting: transparent ones stay zero.
        drawn = buf.px[..., 3] > 0
        rows, cols = np.flatnonzero(drawn.any(1)), np.flatnonzero(drawn.any(0))
        if rows.size:
            px = buf.px[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
            a = px[..., 3:4]
            st = np.where(a > 0, px[..., :3] / np.maximum(a, 1e-6), 0)
            px[..., :3] = s2l(st) * a
    return buf
