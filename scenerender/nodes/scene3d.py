"""3D-ish node kinds: camera (no drawing; drives the 2.5D hook), skeleton (no drawing; used by skin
deformers) and object3D (PARTIAL flat fallback).

object3D fallback: primitives are tessellated (box, plane, cylinder, cone, capsule, torus) or drawn
as a shaded disc (sphere), projected through the active camera (scenerender.camera conventions:
world units = document pixels, +Y up, origin at the frame centre) and painter-sorted per object.
Shading is flat: material baseColor x (ambient 0.35 + 0.65 * key light from upper-left-front)
+ emissive x emissiveStrength; unlit materials use baseColor; transmission lowers opacity.
Objects are stacked by z/document order, not by depth. text, mesh and extrude are not drawn.
object3D @parent and group transforms are ignored (object coordinates are world coordinates).
"""
from __future__ import annotations

import math

import cairo
import numpy as np

from .. import camera as cam3d
from .. import deform, modifiers, physics  # noqa: F401  (register their handlers / hooks)
from ..compositor import RenderContext
from ..document import ln
from ..evaluator import Ctx
from ..raster import Canvas
from ..registry import FEATURES, FULL, NONE, NODES, PARTIAL, warn_once

FEATURES.declare("lights", PARTIAL, "3D lights are not evaluated; object3D fallback uses a fixed key light")
FEATURES.declare("materials", PARTIAL, "baseColor, emissive, opacity, transmission, unlit only (flat shading)")
for _p, _lvl in (("sphere", PARTIAL), ("box", PARTIAL), ("plane", PARTIAL), ("cylinder", PARTIAL),
                 ("cone", PARTIAL), ("torus", PARTIAL), ("capsule", PARTIAL), ("mesh", NONE), ("text", NONE),
                 ("extrude", NONE)):
    FEATURES.declare(f"object3D:{_p}", _lvl, "flat-shaded silhouette through the camera" if _lvl == PARTIAL else
                     "not drawn")

LIGHT = np.array([-0.45, 0.7, 0.55]) / np.linalg.norm([-0.45, 0.7, 0.55])


@NODES.register("camera", level=PARTIAL, note="drives the 2.5D projection of threeD nodes; draws nothing")
def render_camera(rc: RenderContext, el, ctx: Ctx, M, size):
    return None


@NODES.register("skeleton", level=FULL, note="not drawn; bones are read by deform modifier type=skin")
def render_skeleton(rc: RenderContext, el, ctx: Ctx, M, size):
    return None


# ------------------------------------------------------------------ geometry
def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _lathe(profile, seg):
    """Faces (list of (k,3) arrays) of a surface of revolution around +Y; profile [(r, y)]."""
    faces = []
    a = np.linspace(0, 2 * math.pi, seg + 1)
    for (r0, y0), (r1, y1) in zip(profile, profile[1:]):
        for i in range(seg):
            q = [(r0 * math.cos(a[i]), y0, r0 * math.sin(a[i])), (r0 * math.cos(a[i + 1]), y0, r0 * math.sin(a[i + 1])),
                 (r1 * math.cos(a[i + 1]), y1, r1 * math.sin(a[i + 1])), (r1 * math.cos(a[i]), y1, r1 * math.sin(a[i]))]
            q = np.array(q)
            if np.linalg.norm(q[0] - q[1]) < 1e-9 and np.linalg.norm(q[2] - q[3]) < 1e-9:
                continue
            faces.append(q)
    return faces


def _box(w, h, d):
    x, y, z = w / 2, h / 2, d / 2
    v = np.array([[-x, -y, -z], [x, -y, -z], [x, y, -z], [-x, y, -z], [-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]])
    idx = [(4, 5, 6, 7), (1, 0, 3, 2), (5, 1, 2, 6), (0, 4, 7, 3), (7, 6, 2, 3), (0, 1, 5, 4)]
    return [v[list(f)] for f in idx]


def _torus(R, r, su, sv):
    faces = []
    U = np.linspace(0, 2 * math.pi, su + 1)
    V = np.linspace(0, 2 * math.pi, sv + 1)

    def p(u, v):
        return np.array([(R + r * math.cos(v)) * math.cos(u), r * math.sin(v), (R + r * math.cos(v)) * math.sin(u)])
    for i in range(su):
        for j in range(sv):
            faces.append(np.array([p(U[i], V[j]), p(U[i], V[j + 1]), p(U[i + 1], V[j + 1]), p(U[i + 1], V[j])]))
    return faces


def primitive_faces(rc, el, ctx):
    ev = rc.ev
    prim = el.get("primitive")
    r = ev.num(el, "radius", ctx, 50.0)
    w = ev.num(el, "width", ctx, 0.0) or 2 * r
    h = ev.num(el, "height", ctx, 0.0) or 2 * r
    seg = max(8, min(48, int(ev.num(el, "segments", ctx, 32))))
    if prim == "plane":
        return [np.array([[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]])], False
    if prim == "box":
        d = ev.num(el, "depth", ctx, 0.0) if el.get("depth") else w
        return _box(w, h, d), True
    if prim == "cylinder":
        return _lathe([(0, -h / 2), (r, -h / 2), (r, h / 2), (0, h / 2)], seg), True
    if prim == "cone":
        return _lathe([(0, -h / 2), (r, -h / 2), (0, h / 2)], seg), True
    if prim == "capsule":
        body = max(0.0, h - 2 * r) / 2
        prof = [(r * math.sin(a), -body - r * math.cos(a)) for a in np.linspace(0, math.pi / 2, 6)]
        prof += [(r * math.cos(a), body + r * math.sin(a)) for a in np.linspace(0, math.pi / 2, 6)]
        return _lathe(prof, seg), True
    if prim == "torus":
        return _torus(r, r * 0.35, seg, max(8, seg // 3)), True
    return None, False


def _material(rc, el, ctx):
    mat = rc.doc.ids.get(el.get("material")) if el.get("material") else None
    ev = rc.ev
    if mat is None:
        return dict(base=(0.8, 0.8, 0.82, 1.0), emissive=(0, 0, 0), unlit=False, alpha=1.0, metal=0.0, rough=0.5)
    base = ev.color(mat, "baseColor", ctx, (1, 1, 1, 1))
    em = ev.color(mat, "emissive", ctx, (0, 0, 0, 1))
    k = ev.num(mat, "emissiveStrength", ctx, 1.0)
    trans = ev.num(mat, "transmission", ctx, 0.0)
    return dict(base=base, emissive=tuple(min(1.0, c * min(k, 3.0) * em[3]) for c in em[:3]),
                unlit=ev.bool(mat, "unlit", ctx, False),
                alpha=base[3] * ev.num(mat, "opacity", ctx, 1.0) * (1 - 0.65 * trans),
                metal=ev.num(mat, "metallic", ctx, 0.0), rough=ev.num(mat, "roughness", ctx, 0.5))


def _shade(mat, n):
    if mat["unlit"]:
        return mat["base"][:3]
    lam = max(0.0, float(n @ LIGHT))
    k = 0.35 + 0.65 * lam
    if mat["metal"] > 0.5:
        k = 0.2 + 0.9 * lam ** 2
    return tuple(min(1.0, b * k + e) for b, e in zip(mat["base"][:3], mat["emissive"]))


def _clip_near(C: np.ndarray, near: float) -> np.ndarray:
    out = []
    n = len(C)
    for i in range(n):
        a, b = C[i], C[(i + 1) % n]
        ina, inb = a[2] > near, b[2] > near
        if ina:
            out.append(a)
        if ina != inb:
            u = (near - a[2]) / (b[2] - a[2])
            out.append(a + (b - a) * u)
    return np.array(out) if out else np.zeros((0, 3))


@NODES.register("object3D", level=PARTIAL,
                note="flat-shaded primitive silhouettes through the camera; mesh/text/extrude not drawn")
def render_object3d(rc: RenderContext, el, ctx: Ctx, M, size):
    ev = rc.ev
    prim = el.get("primitive")
    cam = cam3d.camera_at(rc, ctx.comp_t)
    pos = np.array([ev.num(el, "x", ctx, 0.0), ev.num(el, "y", ctx, 0.0), ev.num(el, "z", ctx, 0.0)])
    R = (_ry(math.radians(ev.num(el, "rotationY", ctx, 0.0))) @ _rx(math.radians(ev.num(el, "rotationX", ctx, 0.0)))
         @ _rz(math.radians(-ev.num(el, "rotation", ctx, 0.0))))
    S = np.diag([ev.num(el, "scaleX", ctx, 1.0), ev.num(el, "scaleY", ctx, 1.0), ev.num(el, "scaleZ", ctx, 1.0)])
    mat = _material(rc, el, ctx)
    cv = Canvas((0, 0, rc.width, rc.height))
    cr = cv.cr
    cr.set_antialias(cairo.ANTIALIAS_GOOD)
    k = rc.scale
    if prim == "sphere":
        r = ev.num(el, "radius", ctx, 50.0) * float(np.max(np.abs(np.diag(S))))
        C = cam.to_cam(pos)
        if not cam.ortho and C[2] <= cam.near + r * 0.2:
            return None
        s = cam.project_cam(C) * k
        rs = r * cam.scale_at(float(C[2])) * k
        lx, ly = -0.38, -0.42
        g = cairo.RadialGradient(s[0] + lx * rs, s[1] + ly * rs, rs * 0.05, s[0], s[1], rs)
        hi = _shade(mat, LIGHT)
        lo = _shade(mat, -LIGHT * 0.2 + np.array([0, 0, 0.3]))
        a = mat["alpha"]
        if mat["metal"] > 0.5 and not mat["unlit"]:      # mirror-ish: hot spot, dark band, bright rim
            base = mat["base"][:3]
            g.add_color_stop_rgba(0, 1, 1, 1, a)
            g.add_color_stop_rgba(0.25, *(min(1.0, c * 0.95) for c in base), a)
            g.add_color_stop_rgba(0.75, *(c * 0.22 for c in base), a)
            g.add_color_stop_rgba(1, *(c * 0.55 for c in base), a)
        else:
            g.add_color_stop_rgba(0, *(min(1.0, c + 0.15) for c in hi), a)
            g.add_color_stop_rgba(0.45, *hi, a)
            g.add_color_stop_rgba(1, *lo, a)
        cr.set_source(g)
        cr.arc(float(s[0]), float(s[1]), float(rs), 0, 2 * math.pi)
        cr.fill()
        return cv.to_buf(rc.linear)
    faces, closed = primitive_faces(rc, el, ctx)
    if faces is None:
        warn_once("object3D", prim, "primitive not drawn by the Python renderer")
        return None
    drawn = []
    for f in faces:
        Pw = (f @ S.T) @ R.T + pos
        n = np.cross(Pw[1] - Pw[0], Pw[2] - Pw[0])
        if np.linalg.norm(n) < 1e-12 and len(Pw) > 3:
            n = np.cross(Pw[2] - Pw[0], Pw[3] - Pw[0])
        n = n / (np.linalg.norm(n) or 1.0)
        cen = Pw.mean(0)
        view = cam.eye - cen if not cam.ortho else -cam.fwd
        facing = float(n @ view)
        if closed and facing <= 0:
            continue
        if not closed and facing < 0:
            n = -n
        Cc = cam.to_cam(Pw)
        if not cam.ortho:
            Cc = _clip_near(Cc, cam.near)
            if len(Cc) < 3:
                continue
        drawn.append((float(cam.to_cam(cen)[2]), cam.project_cam(Cc) * k, n))
    if not drawn:
        return None
    drawn.sort(key=lambda d: -d[0])
    a = mat["alpha"]
    cr.set_line_width(0.6)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for _, pts, n in drawn:
        col = _shade(mat, n)
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.set_source_rgba(*col, a)
        if a >= 0.999:
            cr.fill_preserve()
            cr.stroke()
        else:
            cr.fill()
    return cv.to_buf(rc.linear)

