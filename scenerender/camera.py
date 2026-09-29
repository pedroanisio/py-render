"""Cameras, 3D transforms and the 2.5D projection of threeD="true" nodes (hook `camera`).

World conventions (normative: scene-render 1.1 CONVENTIONS.md section 2):
  * Scene space (what the XML says): the composition's pixel space extended into depth, origin at
    the frame's top-left corner on z = 0, +x right, +y down, +z away from the viewer; one unit is one
    pixel. Physical quantities that the schema gives in metres (camera DoF, light falloff, shadow
    softness, material thickness/attenuation, interpupillary distance) use UNITS_PER_METRE = 100
    scene units per metre.
  * Engine space (internal; every world matrix, Camera and light in this package): origin at the
    frame centre, +X right, +Y up, +Z toward the viewer (GL). The XML is converted once, at this
    boundary: engine = A . scene with A = T(-W/2, H/2, 0) . E, E = diag(1, -1, -1), so a scene point
    (x, y, z) is (x - W/2, H/2 - y, -z) and an element's engine matrix is A . M_scene . E. A 2D
    node's frame point (xd, yd) at zDepth d is the scene point (xd, yd, d).
  * 2.5D planes (open question in CONVENTIONS section 3, kept as before): rotationY > 0 turns the
    node's right edge away from the viewer; rotationX > 0 tilts its top edge away. Both pivot on the
    node's anchor point (% anchors refer to the parent box), applied after its 2D transform (X then
    Y). The camera hook returns the exact 3x3 homography of that plane (local px -> frame px), so the
    compositor's projective path draws true perspective; it returns None when the whole plane is
    behind the camera (pixels behind the eye are dropped by the warp when it straddles it).
  * object3D: scene M = T(x, y, z) . Rz(rotation) . Ry(rotationY) . Rx(rotationX) . S(scaleX, scaleY,
    scaleZ) (right-handed rotations in scene space: +rotation is clockwise on screen like 2D; a plane
    faces the camera, normal -z). In engine space: T(E t [+ (-W/2, H/2, 0) at the root]) .
    Rz(-rotation) . Ry(-rotationY) . Rx(rotationX) . S. Cameras and lights: x/y/z are absolute scene
    positions; at yaw = pitch = roll = 0 they look along scene +z with +y down in the image; scene
    R = Ry(yaw) . Rx(pitch) . Rz(roll) (+yaw looks right, +pitch looks up, +roll turns the camera
    clockwise so the picture content turns counter-clockwise); engine: local -Z forward, +Y up,
    Ry(-yaw) . Rx(pitch) . Rz(-roll). A camera `target` replaces yaw/pitch with a look-at (up
    hint: scene -y = engine +Y), roll still applies.
  * @parent on a 3D element composes the target's world matrix (object3D / camera / light: its full
    3D matrix; a 2D node: its frame position at its zDepth, its rotation as a roll about Z and its
    mean 2D scale). XML ancestors only gate time, visibility and opacity of 3D elements.
  * transformConstraint on object3D / camera / light (3D semantics, blended by influence with TRS
    decomposition + quaternion slerp): parent (as @parent, space=local ignores the target's own
    parents), look-at (object3D: +Z axis toward the target; camera/light: -Z; offsetRotation adds
    roll), copy-position / copy-rotation / copy-scale / copy-transform (space=world: the target's
    world component, local: its own attributes; offsetX/offsetY add scene units, +y down), distance (clamp
    to [minDistance, maxDistance] from the target), follow-path (@path in document-frame pixels on
    the z = 0 plane at @progress; autoOrient rolls +X along the tangent). ik and track are
    delegated to scenerender.constraints on the node's projected 2D position (see note).
  * Lens: fov is horizontal (deg). focalLength overrides it with the horizontal fov
    2 atan(sensorWidth / (2 focalLength)) (sensorWidth default 36 mm; no film-fit switching to the
    sensor height). orthographic uses orthoHeight world units across the frame height (default: frame
    height, 1:1). near/far clip the 3D renderer; exposure (EV) scales rendered 3D radiance by
    2**exposure. lensDistortion is Brown-Conrady radial k1 on radius normalised by the half
    diagonal (x_d = x_u (1 + k1 r_u^2); k1 < 0 barrel, > 0 pincushion), applied to object3D renders
    and (via hook `camera_post`) to warped threeD layers.
  * Depth of field: thin lens; circle of confusion diameter on the sensor
    c = f^2 / N * |d - s| / (d (s - f)) (f focal length, N = fStop, s = focus distance
    (focusDistance, or the distance along the view axis to focusTarget), all in mm with
    UNITS_PER_METRE), converted to pixels through the sensor width. object3D renders use a
    per-pixel CoC from the depth buffer; threeD planes a per-pixel CoC from the plane depth. The
    bokeh is a disc (apertureBlades < 3) or a regular polygon with apertureBlades sides.
  * No active camera: the implicit camera at scene (W/2, H/2, -(W/2)/tan 30 deg) = engine (0, 0, f)
    with a horizontal fov of 60 deg looking along scene +z, so the plane z = 0 (zDepth = 0 without
    rotation) maps onto the frame pixel for pixel.
  * Active camera at t: the last camera with active="true" whose [start, end) (and its ancestors'
    windows) contains t. project/@mode="viewport" films through scene360/@viewportCamera instead.
  * shake: seeded fractal value noise (octaves, frequency Hz) in [start, end); amplitude = world
    units along the camera right/up axes, rotation = degrees of roll, zoom = fractional change of
    the focal length.
  * shutterAngle on the active camera overrides project/@shutterAngle for motion blur (it does not
    enable motion blur by itself); see `shutter_angle`.
  * Groups: a threeD node inside a threeD group without collapse is flattened into that group's
    plane (the hook leaves it 2D; the group is projected). With collapse="true" the children share
    the parent's camera space: their plane is transformed by the group's own 3D transform (its
    zDepth and rotationX/Y about its anchor) and projected by the active camera, while the group
    itself is not projected again. Inside a collapsed group, 3D participants (threeD nodes and
    object3D) with equal z are drawn farthest first (`depth_sort`), 2D nodes keep their slots.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from .document import ln
from .evaluator import Ctx
from .raster import Buf
from .registry import FEATURES, FULL
from .values import parse_bool

UNITS_PER_METRE = 100.0


FEATURES.register("threeD", level=FULL,
                  note="exact homography of the plane at zDepth rotated by rotationX/Y (projective draw path)")(None)
FEATURES.declare("group:collapse", FULL, "children share the group's camera space; farthest-first within the group")
FEATURES.declare("camera:depthOfField", FULL, "thin-lens per-pixel CoC, fStop, focusDistance/focusTarget, "
                                               "polygonal bokeh from apertureBlades")
FEATURES.declare("camera:shake", FULL, "seeded fBm on position/roll/zoom with frequency, octaves, [start, end)")
FEATURES.declare("camera:lensDistortion", FULL, "Brown-Conrady k1 on object3D renders and threeD layers")
FEATURES.declare("camera:transformConstraint", FULL, "3D parent/look-at/copy-*/distance/follow-path; ik/track via "
                                                      "the 2D solver on the projected position")
FEATURES.declare("camera:shutterAngle", FULL, "overrides project/@shutterAngle while the camera is active")

OFFSCREEN = np.array([[1e-6, 0, -1e7], [0, 1e-6, -1e7], [0, 0, 1]], np.float64)


def _norm(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


@dataclass
class Camera:
    eye: np.ndarray
    right: np.ndarray
    up: np.ndarray
    fwd: np.ndarray
    fpx: float                 # focal length in document pixels (perspective)
    ortho: bool
    k_ortho: float             # pixels per world unit (orthographic)
    W: float
    H: float
    near: float = 0.1
    el: object = None
    focus: float = 1000.0
    fstop: float = 2.8
    dof: bool = False
    far: float = 10000.0
    focal_mm: float = 50.0
    px_per_mm: float = 1.0     # document pixels per sensor millimetre (fitted dimension)
    blades: int = 0
    exposure: float = 0.0
    lens_k: float = 0.0
    shutter: float | None = None
    cx: float | None = None    # principal point (document px); default frame centre
    cy: float | None = None

    @property
    def ppx(self) -> float:
        return self.W / 2 if self.cx is None else self.cx

    @property
    def ppy(self) -> float:
        return self.H / 2 if self.cy is None else self.cy

    def to_cam(self, P: np.ndarray) -> np.ndarray:
        """World points (..., 3) -> camera coordinates (right, up, depth)."""
        d = np.asarray(P, np.float64) - self.eye
        return np.stack([d @ self.right, d @ self.up, d @ self.fwd], -1)

    def project_cam(self, C: np.ndarray) -> np.ndarray:
        """Camera coordinates -> document-pixel screen points (..., 2)."""
        C = np.asarray(C, np.float64)
        if self.ortho:
            k = self.k_ortho
            return np.stack([self.ppx + C[..., 0] * k, self.ppy - C[..., 1] * k], -1)
        z = np.maximum(C[..., 2], 1e-9)
        return np.stack([self.ppx + self.fpx * C[..., 0] / z, self.ppy - self.fpx * C[..., 1] / z], -1)

    def project(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        C = self.to_cam(P)
        return self.project_cam(C), (C[..., 2] > self.near) | self.ortho

    def scale_at(self, depth: float) -> float:
        return self.k_ortho if self.ortho else self.fpx / max(depth, 1e-9)

    def K(self) -> np.ndarray:
        """3x3 camera coordinates (right, up, depth) -> homogeneous document pixels."""
        if self.ortho:
            k = self.k_ortho
            return np.array([[k, 0, 0], [0, -k, 0], [0, 0, 0]], np.float64)
        f = self.fpx
        return np.array([[f, 0, self.ppx], [0, -f, self.ppy], [0, 0, 1]], np.float64)

    def rotation(self) -> np.ndarray:
        """3x3 world -> camera coordinates (rows right, up, fwd)."""
        return np.stack([self.right, self.up, self.fwd])

    def view_matrix(self) -> np.ndarray:
        """4x4 world -> GL eye space (x right, y up, looking down -Z)."""
        V = np.eye(4)
        V[0, :3], V[1, :3], V[2, :3] = self.right, self.up, -self.fwd
        V[:3, 3] = -V[:3, :3] @ self.eye
        return V

    def proj_matrix(self, W: float | None = None, H: float | None = None, near: float | None = None,
                    far: float | None = None, offset=(0.0, 0.0)) -> np.ndarray:
        """GL clip matrix for a viewport of W x H document pixels whose top-left sits at `offset` in
        the camera's document frame (for tiles and 360 faces)."""
        W = self.W if W is None else W
        H = self.H if H is None else H
        n = self.near if near is None else near
        f = self.far if far is None else far
        ox, oy = offset
        # pixel x = ppx + fpx * X / Z  ->  ndc x = 2 (x - ox) / W - 1
        P = np.zeros((4, 4))
        if self.ortho:
            k = self.k_ortho
            P[0, 0], P[0, 3] = 2 * k / W, 2 * (self.ppx - ox) / W - 1
            P[1, 1], P[1, 3] = 2 * k / H, 1 - 2 * (self.ppy - oy) / H
            P[2, 2], P[2, 3] = -2 / (f - n), -(f + n) / (f - n)
            P[3, 3] = 1
            return P
        P[0, 0], P[0, 2] = 2 * self.fpx / W, -(2 * (self.ppx - ox) / W - 1)
        P[1, 1], P[1, 2] = 2 * self.fpx / H, -(1 - 2 * (self.ppy - oy) / H)
        P[2, 2], P[2, 3] = -(f + n) / (f - n), -2 * f * n / (f - n)
        P[3, 2] = -1
        return P

    def coc_px(self, depth) -> np.ndarray:
        """Circle-of-confusion RADIUS in document pixels at camera depth(s) (scene units)."""
        if not self.dof or self.ortho:
            return np.zeros_like(np.asarray(depth, np.float64))
        mm = 1000.0 / UNITS_PER_METRE
        d = np.maximum(np.asarray(depth, np.float64) * mm, 1e-3)
        s = max(self.focus * mm, self.focal_mm * 1.001)
        f = self.focal_mm
        c = (f * f / max(self.fstop, 0.05)) * np.abs(d - s) / (d * (s - f))
        return 0.5 * c * self.px_per_mm


# ------------------------------------------------------------------ rotations / matrices
def rx(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def ry(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rz(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def ypr(yaw: float, pitch: float, roll: float) -> np.ndarray:
    """Engine world-from-local rotation of a camera/light (local -Z forward, +Y up). Scene space has
    R = Ry(yaw) . Rx(pitch) . Rz(roll) (+yaw looks right, +pitch looks up, +roll turns the camera
    clockwise); conjugated by E = diag(1, -1, -1) that is Ry(-yaw) . Rx(pitch) . Rz(-roll)."""
    return ry(-yaw) @ rx(pitch) @ rz(-roll)


def look_rotation(fwd: np.ndarray, up_hint=np.array([0.0, 1.0, 0.0]), axis: str = "-z") -> np.ndarray:
    """Rotation whose `axis` (-z or +z) points along fwd, local +Y as close to up_hint as possible."""
    f = _norm(np.asarray(fwd, np.float64))
    r = np.cross(f, up_hint)
    if np.linalg.norm(r) < 1e-9:
        r = np.cross(f, np.array([0.0, 0.0, 1.0 if abs(f[2]) < 0.9 else 0.0]) + np.array([0, 0, 0]))
        if np.linalg.norm(r) < 1e-9:
            r = np.array([1.0, 0.0, 0.0])
    r = _norm(r)
    u = np.cross(r, f)
    if axis == "-z":
        return np.stack([r, u, -f], 1)
    return np.stack([-r, u, f], 1)


def trs(t, R, s) -> np.ndarray:
    M = np.eye(4)
    M[:3, :3] = np.asarray(R) @ np.diag(s)
    M[:3, 3] = t
    return M


def decompose(M: np.ndarray):
    """4x4 -> (t, R (3x3 orthonormal), s)."""
    t = M[:3, 3].copy()
    A = M[:3, :3]
    s = np.linalg.norm(A, axis=0)
    R = A / np.where(s > 1e-12, s, 1.0)
    if np.linalg.det(R) < 0:
        s[0] = -s[0]
        R[:, 0] = -R[:, 0]
    return t, R, s


def mat_to_quat(R: np.ndarray) -> np.ndarray:
    """3x3 rotation -> quaternion xyzw."""
    m = R
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def quat_to_mat(q) -> np.ndarray:
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def slerp(a, b, u: float) -> np.ndarray:
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    d = float(a @ b)
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        q = a + (b - a) * u
        return q / np.linalg.norm(q)
    th = math.acos(min(1.0, d))
    return (a * math.sin((1 - u) * th) + b * math.sin(u * th)) / math.sin(th)


def blend_matrix(A: np.ndarray, B: np.ndarray, u: float) -> np.ndarray:
    """TRS blend of two 4x4 transforms (u = 0 -> A, 1 -> B)."""
    if u >= 1:
        return B
    if u <= 0:
        return A
    ta, Ra, sa = decompose(A)
    tb, Rb, sb = decompose(B)
    return trs(ta + (tb - ta) * u, quat_to_mat(slerp(mat_to_quat(Ra), mat_to_quat(Rb), u)), sa + (sb - sa) * u)


# ------------------------------------------------------------------ world helpers
def frame_matrix(rc):
    return rc.root_matrix if rc.scene_matrix is None else rc.scene_matrix


def frame_size(rc):
    if rc.scene_context is None:
        return float(rc.doc.width), float(rc.doc.height)
    root, ctx = rc.scene_context
    return rc.ev.num(root, "width", ctx, rc.doc.width), rc.ev.num(root, "height", ctx, rc.doc.height)


def frame_to_world(rc, q_doc: np.ndarray, zdepth) -> np.ndarray:
    """Document-pixel frame points (..., 2) at zDepth -> world (..., 3)."""
    q = np.asarray(q_doc, np.float64)
    W, H = frame_size(rc)
    return np.stack([q[..., 0] - W / 2, H / 2 - q[..., 1], np.broadcast_to(-np.asarray(zdepth, np.float64), q.shape[:-1])], -1)


def _ctx(t: float) -> Ctx:
    return Ctx(t=t, comp_t=t)


def is3d_tag(el) -> bool:
    return ln(el) in ("object3D", "camera", "light")


def node_clock(rc, el, t: float, base: Ctx | None = None) -> Ctx | None:
    """The clock (Ctx) of `el` at composition time t, walking its XML ancestors like the compositor
    (sequence clock shifts, group timeOffset/timeScale, repeat variables); None when an ancestor or
    el itself is inactive."""
    from .nodes.core import _repeat_vars, child_ctx
    chain = []
    p = el.getparent()
    while p is not None and isinstance(p.tag, str) and ln(p) not in ("composition", "symbol", "scene", "lights"):
        chain.append(p)
        p = p.getparent()
    if base is None and rc.scene_context is not None:
        root, current = rc.scene_context
        if root in el.iterancestors():
            base = current
    ctx = base or Ctx(t=t, comp_t=t)
    for g in reversed(chain):
        if not rc.active(g, ctx):
            return None
        ctx = _repeat_vars(g, child_ctx(rc, g, rc.enter_node(g, ctx)))
    if ln(el) not in ("light",) and not rc.active(el, ctx):
        return None
    return rc.enter_node(el, ctx) if ln(el) != "light" else ctx


def _has_parent3d(rc, el, ctx: Ctx) -> bool:
    pid = rc.ev.str(el, "parent", ctx)
    par = rc.doc.ids.get(pid) if pid else None
    return par is not None and par is not el


def _local3d(rc, el, ctx: Ctx, rooted: bool | None = None) -> np.ndarray:
    """Engine-space local matrix of an object3D / camera / light. The document gives x/y/z in scene
    space (origin frame top-left on z = 0, +y down, +z away); the engine frame is A = T(-W/2, H/2, 0) . E
    with E = diag(1, -1, -1), so an element's engine matrix is A . M_doc . E: its translation is
    E . (x, y, z) (plus (-W/2, H/2, 0) when it has no resolvable @parent, whose own engine frame
    already carries that offset) and every rotation is conjugated by E (Rz and Ry change sign)."""
    ev = rc.ev
    inst = bool(ctx.vars) and all(name in ("index", "count") for name, _ in ctx.vars)
    if inst:
        # Instances (ctx.vars carries only index / count): only expressions can differ between copies,
        # so every other property is evaluated once per element and sample time and shared.
        def num(prop, default):
            if any(ln(a) == "expression" for a in ev._anims(el, prop)):
                return ev.num(el, prop, ctx, default)
            key = ("inst-prop", el, prop, ctx.t, ctx.comp_t, ctx.frame, ctx.scope)
            hit = rc.frame_cache.get(key)
            if hit is None:
                hit = rc.frame_cache[key] = ev.num(el, prop, ctx, default)
            return hit
    else:
        def num(prop, default):
            return ev.num(el, prop, ctx, default)
    t = np.array([num("x", 0.0), -num("y", 0.0), -num("z", 0.0)])
    if rooted is None:
        if inst:
            key = ("inst-rooted", el, ctx.t, ctx.comp_t, ctx.scope)
            rooted = rc.frame_cache.get(key)
            if rooted is None:
                rooted = rc.frame_cache[key] = not _has_parent3d(rc, el, ctx)
        else:
            rooted = not _has_parent3d(rc, el, ctx)
    if rooted:
        W, H = frame_size(rc)
        t = t + np.array([-W / 2, H / 2, 0.0])
    if ln(el) == "object3D":
        # scene space: Rz(rotation) . Ry(rotationY) . Rx(rotationX); E . R . E in engine space
        R = rz(-num("rotation", 0.0)) @ ry(-num("rotationY", 0.0)) @ rx(num("rotationX", 0.0))
        s = [num("scaleX", 1.0), num("scaleY", 1.0), num("scaleZ", 1.0)]
        return trs(t, R, s)
    return trs(t, ypr(num("yaw", 0.0), num("pitch", 0.0), num("roll", 0.0)), [1, 1, 1])


def world3d(rc, el, ctx: Ctx, local_only: bool = False, _depth: int = 0) -> np.ndarray:
    """4x4 world matrix of an object3D / camera / light, or the 3D frame of a 2D node."""
    key = ("world3d", el, ctx, local_only)
    hit = rc.frame_cache.get(key)
    if hit is not None:
        return hit
    if not is3d_tag(el):
        M = _frame_of_2d(rc, el, ctx)
        rc.frame_cache[key] = M
        return M
    M = _local3d(rc, el, ctx)
    if rc.ev.str(el, "target", ctx) and ln(el) == "camera":
        tgt = rc.doc.ids.get(rc.ev.str(el, "target", ctx))
        if tgt is not None and tgt is not el:
            tp = element_world_pos(rc, tgt, ctx.comp_t)
            if tp is not None and np.linalg.norm(tp - M[:3, 3]) > 1e-9:
                roll = rc.ev.num(el, "roll", ctx, 0.0)
                M[:3, :3] = look_rotation(tp - M[:3, 3]) @ rz(-roll)
    pid = rc.ev.str(el, "parent", ctx)
    base = None           # what M's local part was composed onto: the parent's world, or the frame offset
    if pid and not local_only and _depth < 32:
        par = rc.doc.ids.get(pid)
        if par is not None and par is not el:
            pc = node_clock(rc, par, ctx.comp_t) or ctx
            base = world3d(rc, par, pc, _depth=_depth + 1)
            M = base @ M
    if not local_only:
        for c in el:
            if isinstance(c.tag, str) and ln(c) == "transformConstraint":
                M = apply_constraint3d(rc, c, el, M, ctx, _depth, base)
    rc.frame_cache[key] = M
    return M


def _frame_of_2d(rc, el, ctx: Ctx) -> np.ndarray:
    """A 2D node as a 3D frame: position at its zDepth, roll from its rotation, mean scale."""
    try:
        Mw = np.linalg.inv(frame_matrix(rc)) @ rc.world_matrix(el, ctx)
    except Exception:  # noqa: BLE001 — unresolvable 2D target: identity
        return np.eye(4)
    p = frame_to_world(rc, Mw[:2, 2], rc.ev.num(el, "zDepth", ctx, 0.0))
    rot = math.degrees(math.atan2(Mw[1, 0], Mw[0, 0]))
    k = math.sqrt(abs(np.linalg.det(Mw[:2, :2]))) or 1.0
    return trs(p, rz(-rot), [k, k, k])


def element_world_pos(rc, el, t: float) -> np.ndarray | None:
    if el is None:
        return None
    c = node_clock(rc, el, t) if is3d_tag(el) else _ctx(t)
    c = c or _ctx(t)
    if is3d_tag(el):
        return world3d(rc, el, c)[:3, 3].copy()
    return _frame_of_2d(rc, el, c)[:3, 3].copy()


def apply_constraint3d(rc, c, el, M: np.ndarray, ctx: Ctx, depth: int = 0, base: np.ndarray | None = None) -> np.ndarray:
    """M after the constraint c. base is what the element's local matrix was composed onto (its
    @parent's world matrix; None: the frame offset of a rooted element), which a `parent` constraint
    replaces by the target's world matrix (D23, CONVENTIONS 5.4: x/y/z and the angles are then in the
    target's frame, and the frame offset is applied once, by the target's own chain)."""
    ev = rc.ev
    typ = ev.str(c, "type", ctx)
    infl = ev.num(c, "influence", ctx, 1.0)
    if infl <= 0:
        return M
    tgt = rc.doc.ids.get(ev.str(c, "target", ctx))
    local = ev.str(c, "space", ctx, "world") == "local"
    T = None
    if tgt is not None and tgt is not el and depth < 32:
        tc = (node_clock(rc, tgt, ctx.comp_t) if is3d_tag(tgt) else ctx) or ctx
        T = world3d(rc, tgt, tc, local_only=local, _depth=depth + 1) if is3d_tag(tgt) else _frame_of_2d(rc, tgt, tc)
    off = np.array([ev.num(c, "offsetX", ctx, 0.0), -ev.num(c, "offsetY", ctx, 0.0), 0.0])   # scene +y is down
    orot = ev.num(c, "offsetRotation", ctx, 0.0)
    t, R, s = decompose(M)
    out = None
    if typ == "parent" and T is not None:
        if base is None:
            W, H = frame_size(rc)
            local = M.copy()
            local[:3, 3] -= (-W / 2, H / 2, 0.0)
        else:
            local = np.linalg.inv(base) @ M
        out = T @ local
        out[:3, 3] += off
    elif typ == "look-at" and T is not None:
        axis = "+z" if ln(el) == "object3D" else "-z"
        d = T[:3, 3] - t
        if np.linalg.norm(d) > 1e-9:
            out = trs(t, look_rotation(d, axis=axis) @ rz(-orot if axis == "+z" else orot), s)
    elif typ in ("copy-position", "copy-rotation", "copy-scale", "copy-transform") and T is not None:
        gt, gR, gs = decompose(T)
        if typ in ("copy-position", "copy-transform"):
            t = gt + off
        if typ in ("copy-rotation", "copy-transform"):
            R = gR @ rz(-orot)
        if typ in ("copy-scale", "copy-transform"):
            s = gs
        out = trs(t, R, s)
    elif typ == "distance" and T is not None:
        g = T[:3, 3]
        d = float(np.linalg.norm(t - g))
        lo = ev.num(c, "minDistance", ctx, 0.0)
        hi = ev.num(c, "maxDistance", ctx, float("inf"))
        nd = min(max(d, lo), hi)
        if d > 1e-9 and nd != d:
            out = M.copy()
            out[:3, 3] = g + (t - g) * nd / d
    elif typ == "follow-path" and ev.str(c, "path", ctx):
        from .geometry import PathSampler
        path = ev.str(c, "path", ctx)
        key = ("fp", path)
        smp = rc.cache.get(key) or PathSampler(path)
        rc.cache[key] = smp
        x, y, ang = smp.at(ev.num(c, "progress", ctx, 0.0))
        p = frame_to_world(rc, np.array([x, y]), 0.0) + off
        if ev.bool(c, "autoOrient", ctx):
            R = rz(-(ang + orot))
        out = trs(p, R, s)
    elif typ in ("ik", "track"):
        out = _constraint_via_2d(rc, c, el, M, ctx)
    if out is None:
        return M
    return blend_matrix(M, out, infl)


def _constraint_via_2d(rc, c, el, M: np.ndarray, ctx: Ctx) -> np.ndarray | None:
    """ik / track on a 3D element: run the 2D solver on the element's projected frame position and
    move it within its camera-depth plane to the solved screen point."""
    try:
        from .constraints import apply_constraint
    except ImportError:
        return None
    cam = build_camera(rc, None, ctx.comp_t) if ln(el) == "camera" else camera_at(rc, ctx.comp_t)
    C = cam.to_cam(M[:3, 3])
    if C[2] <= cam.near and not cam.ortho:
        return None
    s = cam.project_cam(C)
    A = frame_matrix(rc) @ np.array([[1, 0, s[0]], [0, 1, s[1]], [0, 0, 1]])
    B = apply_constraint(rc, c, el, A, ctx)
    q = (np.linalg.inv(frame_matrix(rc)) @ B)[:2, 2]
    if np.allclose(q, s):
        return None
    if cam.ortho:
        cx, cy = (q[0] - cam.ppx) / cam.k_ortho, (cam.ppy - q[1]) / cam.k_ortho
    else:
        cx, cy = (q[0] - cam.ppx) * C[2] / cam.fpx, (cam.ppy - q[1]) * C[2] / cam.fpx
    out = M.copy()
    out[:3, 3] = cam.eye + cam.right * cx + cam.up * cy + cam.fwd * C[2]
    return out


def default_fpx(W: float) -> float:
    """Focal length (px) of the implicit camera: horizontal fov 60 deg."""
    return (W / 2) / math.tan(math.radians(30.0))


def _window_ok(rc, el, t: float) -> bool:
    return node_clock(rc, el, t) is not None


def cameras(rc) -> list:
    comp = rc.scene_context[0] if rc.scene_context is not None else rc.doc.section("composition")
    key = ("cameras", comp)
    hit = rc.cache.get(key)
    if hit is None:
        hit = [e for e in comp.iter() if isinstance(e.tag, str) and ln(e) == "camera"] if comp is not None else []
        rc.cache[key] = hit
    return hit


def active_camera(rc, t: float):
    """The last camera with active=true whose [start, end) (and its ancestors' windows) contains t;
    in project mode "viewport" the scene360/@viewportCamera when set."""
    if rc.doc.project.get("mode") == "viewport":
        s360 = rc.doc.section("scene360")
        vid = s360.get("viewportCamera") if s360 is not None else None
        if vid and vid in rc.doc.ids:
            return rc.doc.ids[vid]
    found = None
    for cam in cameras(rc):
        c = node_clock(rc, cam, t)
        if c is not None and rc.ev.bool(cam, "active", c, True):
            found = cam
    return found


def _shake(rc, cam_el, t: float):
    """(dx, dy, droll, dzoom) summed over the camera's shake children active at t."""
    dx = dy = dr = dz = 0.0
    ev = rc.ev
    c = _ctx(t)
    from .physics import fbm
    for i, sh in enumerate(s for s in cam_el if isinstance(s.tag, str) and ln(s) == "shake"):
        s0 = ev.num(sh, "start", c, 0.0)
        e0 = ev.num(sh, "end", c, 0.0) if sh.get("end") is not None else None
        if t < s0 or (e0 is not None and t >= e0):
            continue
        seed = int(sh.get("seed")) if sh.get("seed") else ev.seed_for(cam_el, f"shake{i}")
        seed &= 0x7FFFFFFF
        f = ev.num(sh, "frequency", c, 2.0)
        oc = max(1, int(ev.num(sh, "octaves", c, 2)))
        amp = ev.num(sh, "amplitude", c, 10.0)
        n = [float(fbm(seed + k * 101, t * f, k * 7.31, 0.0, oc)) * 1.6 for k in range(4)]
        dx += n[0] * amp
        dy += n[1] * amp
        dr += n[2] * ev.num(sh, "rotation", c, 0.0)
        dz += n[3] * ev.num(sh, "zoom", c, 0.0)
    return dx, dy, dr, dz


def build_camera(rc, el, t: float, W: float | None = None, H: float | None = None) -> Camera:
    """Evaluate a camera element (or the default camera for el=None) at composition time t."""
    fw, fh = frame_size(rc)
    W = fw if W is None else W
    H = fh if H is None else H
    if el is None:
        f = default_fpx(W)
        return Camera(np.array([0.0, 0.0, f]), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, -1.0]),
                      f, False, 1.0, W, H, focal_mm=f * 36.0 / W, px_per_mm=W / 36.0, far=max(10000.0, 20 * f))
    ev = rc.ev
    c = node_clock(rc, el, t) or rc.node_ctx(el, _ctx(t))
    M = world3d(rc, el, c)
    eye = M[:3, 3].copy()
    R = M[:3, :3] / np.maximum(np.linalg.norm(M[:3, :3], axis=0), 1e-12)
    right, up, fwd = R[:, 0].copy(), R[:, 1].copy(), -R[:, 2].copy()
    sx, sy, sr, sz = _shake(rc, el, t)
    if sr:
        right = _rot_axis(fwd, -sr, right)
        up = _rot_axis(fwd, -sr, up)
    eye = eye + right * sx + up * sy
    sw = ev.num(el, "sensorWidth", c, 36.0)
    if ev.explicit(el, "focalLength", c):          # horizontal fov = 2 atan(sensorWidth / (2 focalLength))
        fl = ev.num(el, "focalLength", c, 50.0)
        fpx = fl * W / sw
    else:
        fov = min(179.0, max(0.1, ev.num(el, "fov", c, 60.0)))
        fpx = (W / 2) / math.tan(math.radians(fov) / 2)
        fl = fpx * sw / W
    fpx *= 1 + sz
    ortho = ev.str(el, "projection", c, "perspective") == "orthographic"
    oh = ev.num(el, "orthoHeight", c, H)
    focus = ev.num(el, "focusDistance", c, 1000.0)
    ft = rc.doc.ids.get(ev.str(el, "focusTarget", c))
    fp = element_world_pos(rc, ft, t) if ft is not None else None
    if fp is not None:
        focus = max(1e-3, float((fp - eye) @ fwd))
    shutter = ev.num(el, "shutterAngle", c, 180.0) if ev.explicit(el, "shutterAngle", c) else None
    return Camera(eye, right, up, fwd, fpx, ortho, H / oh * (1 + sz), W, H, max(1e-4, ev.num(el, "near", c, 0.1)), el,
                  focus, ev.num(el, "fStop", c, 2.8), ev.bool(el, "depthOfField", c, False),
                  ev.num(el, "far", c, 10000.0), fl, W / sw * (1 + sz),
                  int(ev.num(el, "apertureBlades", c, 0)), ev.num(el, "exposure", c, 0.0),
                  ev.num(el, "lensDistortion", c, 0.0), shutter)


def camera_at(rc, t: float) -> Camera:
    """The camera filming the frame at t (an override installed by the 360 renderer wins)."""
    ov = rc.cache.get("camera_override")
    if ov is not None:
        return ov
    key = ("camera-at", t, rc.scene_context)
    hit = rc.frame_cache.get(key)
    if hit is None:
        hit = build_camera(rc, active_camera(rc, t), t)
        rc.frame_cache[key] = hit
    return hit


def _rot_axis(axis: np.ndarray, deg: float, v: np.ndarray) -> np.ndarray:
    """Rodrigues rotation of v about unit axis."""
    a = math.radians(deg)
    k = _norm(axis)
    return v * math.cos(a) + np.cross(k, v) * math.sin(a) + k * (k @ v) * (1 - math.cos(a))


def shutter_angle(rc, t: float, default: float) -> float:
    """Shutter angle for motion blur at t: the active camera's shutterAngle, else `default`."""
    el = active_camera(rc, t)
    if el is not None:
        c = node_clock(rc, el, t) or _ctx(t)
        if rc.ev.explicit(el, "shutterAngle", c):
            return rc.ev.num(el, "shutterAngle", c, default)
    return default


# ------------------------------------------------------------------ 2.5D nodes
def collapse_routed(rc) -> bool:
    """Whether the compositor routes every node of a collapsed threeD group through the camera hook
    (RenderContext.threed_routing, set by the core when it calls `is_threed`). Without it a collapsed
    threeD group falls back to flattening (drawn as one projected plane)."""
    return bool(getattr(rc, "threed_routing", False))


def _flattening_ancestor(el, rc=None) -> bool:
    """True when a threeD ancestor group without (routed) collapse flattens el into its plane."""
    routed = rc is not None and collapse_routed(rc)
    p = el.getparent()
    while p is not None and isinstance(p.tag, str) and ln(p) not in ("composition", "symbol"):
        if parse_bool(p.get("threeD")) and (not parse_bool(p.get("collapse")) or not routed):
            return True
        p = p.getparent()
    return False


flattened_into_ancestor = _flattening_ancestor      # public alias (used by text_animators for per-letter 3D)


def _collapsed_ancestors(el) -> list:
    """threeD groups with collapse=true above el (nearest first), up to a flattening one."""
    out = []
    p = el.getparent()
    while p is not None and isinstance(p.tag, str) and ln(p) not in ("composition", "symbol"):
        if parse_bool(p.get("threeD")):
            if not parse_bool(p.get("collapse")):
                break
            out.append(p)
        p = p.getparent()
    return out


def is_threed(rc, el) -> bool:
    """Whether the compositor should route el through the camera hook: threeD="true", or any node
    inside a collapsed threeD group (it shares that group's camera space at zDepth 0)."""
    if parse_bool(el.get("threeD")):
        return True
    return ln(el) not in ("object3D", "camera", "light") and bool(_collapsed_ancestors(el))


def _plane_rotation(rxd: float, ryd: float) -> np.ndarray:
    """2.5D rotation (rotationX > 0 tilts the top edge away, then rotationY > 0 turns the right edge away)."""
    a = math.radians(-rxd)
    ca, sa = math.cos(a), math.sin(a)
    Rx = np.array([[1, 0, 0], [0, ca, -sa], [0, sa, ca]])
    b = math.radians(ryd)
    cb, sb = math.cos(b), math.sin(b)
    Ry = np.array([[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]])
    return Ry @ Rx


def parent_box(rc, el, ctx: Ctx) -> tuple[float, float]:
    """(w, h) of el's parent box (the base of its % lengths): the frame at the top level, else the
    box its compositor traversal hands it."""
    p = el.getparent()
    if p is None or not isinstance(p.tag, str) or ln(p) in ("composition", "symbol", "scene"):
        return frame_size(rc)
    key = ("parent-box", el, ctx)
    hit = rc.frame_cache.get(key)
    if hit is None:
        try:
            hit = tuple(rc.node_location(el, ctx).box)
        except Exception:  # noqa: BLE001 — unresolvable traversal: the frame
            hit = frame_size(rc)
        rc.frame_cache[key] = hit
    return hit


def _node_3d(rc, el, M_frame_doc: np.ndarray, ctx: Ctx, size) -> tuple[np.ndarray, float, np.ndarray]:
    """(R, zDepth, pivot_world) of a 2.5D node whose doc-frame matrix is M_frame_doc."""
    ev = rc.ev
    bw, bh = parent_box(rc, el, ctx)                            # % anchors refer to the parent box
    ax = ev.length(el, "anchorX", ctx, bw)
    ay = ev.length(el, "anchorY", ctx, bh)
    zd = ev.num(el, "zDepth", ctx, 0.0)
    pv = M_frame_doc @ np.array([ax, ay, 1.0])
    pivot = frame_to_world(rc, pv[:2], zd)
    return _plane_rotation(ev.num(el, "rotationX", ctx, 0.0), ev.num(el, "rotationY", ctx, 0.0)), zd, pivot


def plane_affine(rc, el, M: np.ndarray, ctx: Ctx, size=None) -> np.ndarray:
    """3x3 G with world point P = G @ (u, v, 1) for el's local coordinates (u, v); M is el's frame
    matrix (frame px). Includes zDepth, rotationX/Y and collapsed ancestors' 3D transforms."""
    if size is None:
        size = rc.node_size(el, ctx, frame_size(rc))
    Md = np.linalg.inv(frame_matrix(rc)) @ M                    # local -> document frame px
    zd = rc.ev.num(el, "zDepth", ctx, 0.0)
    # world = S . Md . (u,v,1) + (-W/2, H/2, -zd) as an affine in (u, v, 1)
    W, H = frame_size(rc)
    G = np.zeros((3, 3))
    G[0] = Md[0]
    G[1] = -Md[1]
    G[0, 2] -= W / 2
    G[1, 2] += H / 2
    G[2, 2] = -zd
    R, _, pivot = _node_3d(rc, el, Md, ctx, size)
    G = _rotate_about(G, R, pivot)
    for g in _collapsed_ancestors(el):
        gc = ctx
        gM = rc.world_matrix(g, gc)
        gs = rc.node_size(g, gc, (W, H))
        Rg, zg, pg = _node_3d(rc, g, np.linalg.inv(frame_matrix(rc)) @ gM, gc, gs)
        G = G.copy()
        G[2, 2] -= zg                                         # child depth is relative to the group plane
        pg = pg.copy()
        G = _rotate_about(G, Rg, pg)
    return G


def _rotate_about(G: np.ndarray, R: np.ndarray, pivot: np.ndarray) -> np.ndarray:
    out = R @ G
    out[:, 2] += pivot - R @ pivot
    return out


def node_plane_world(rc, el, M: np.ndarray, ctx: Ctx, size=None):
    """World positions of el's local box corners (TL, TR, BR, BL), plus the local corners used."""
    if size is None:
        size = rc.node_size(el, ctx, frame_size(rc))
    w, h = size
    if w <= 0 or h <= 0:
        w = h = 100.0
    loc = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64)
    G = plane_affine(rc, el, M, ctx, size)
    return np.c_[loc, np.ones(4)] @ G.T, loc


def project_quad(rc, el, M: np.ndarray, ctx: Ctx, size=None):
    """Frame-pixel screen positions of el's box corners (TL, TR, BR, BL) through the active camera,
    or None when any corner is behind the camera."""
    cam = camera_at(rc, ctx.comp_t)
    Pw, _ = node_plane_world(rc, el, M, ctx, size)
    s, ok = cam.project(Pw)
    if not np.all(ok):
        return None
    q = np.c_[s, np.ones(4)] @ frame_matrix(rc).T
    return q[:, :2]


def homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """3x3 H with dst ~ H @ src for 4 point pairs."""
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    _, _, Vt = np.linalg.svd(np.array(A, np.float64))
    Hm = Vt[-1].reshape(3, 3)
    return Hm / Hm[2, 2] if abs(Hm[2, 2]) > 1e-12 else Hm


def plane_homography(rc, el, M: np.ndarray, ctx: Ctx, cam: Camera | None = None, size=None):
    """(H, Gc): H maps el's local px to frame px (w > 0 in front of the camera); Gc maps local
    (u, v, 1) to camera coordinates (right, up, depth)."""
    cam = cam or camera_at(rc, ctx.comp_t)
    G = plane_affine(rc, el, M, ctx, size)
    Gc = cam.rotation() @ G
    Gc[:, 2] -= cam.rotation() @ cam.eye
    K = cam.K()
    if cam.ortho:
        Hd = K @ Gc
        Hd[0, 2] += cam.ppx
        Hd[1, 2] += cam.ppy
        Hd[2] = [0, 0, 1]
    else:
        Hd = K @ Gc
    Hm = frame_matrix(rc) @ Hd
    return Hm, Gc


def camera_hook(rc, el, M: np.ndarray, ctx: Ctx) -> np.ndarray | None:
    """rc.hooks['camera']: the node's exact projective matrix (local px -> frame px), or None when
    the plane is entirely behind the camera. Returns M unchanged for nodes flattened into a
    non-collapsed threeD ancestor and for collapsed threeD groups themselves."""
    if _flattening_ancestor(el, rc) or rc._flat_depth > 0:
        return M
    if parse_bool(el.get("collapse")) and ln(el) in ("group", "sequence") and collapse_routed(rc):
        return M
    if rc.cache.get("pass360") == "flat":
        return None
    cam = camera_at(rc, ctx.comp_t)
    size = rc.node_size(el, ctx, frame_size(rc))
    Hm, Gc = plane_homography(rc, el, M, ctx, cam, size)
    w, h = size
    if w <= 0 or h <= 0:
        w = h = 100.0
    corners = np.array([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]], np.float64)
    depth = corners @ Gc[2]
    if not cam.ortho and np.all(depth <= cam.near):
        return None
    scale_w = abs(Hm[2, 2]) + 1e-300
    if abs(Hm[2, 0]) * w + abs(Hm[2, 1]) * h < 1e-12 * scale_w:
        Hm = Hm.copy()
        Hm[2, 0] = Hm[2, 1] = 0.0
    if Hm[2, 2] > 1e-12:
        Hm = Hm / Hm[2, 2]
    elif Hm[2, 2] < -1e-12:
        Hm = Hm / -Hm[2, 2]          # keep w > 0 for points in front of the eye
    rc.frame_cache[("plane3d", el, ctx)] = (Hm, Gc, size)
    return Hm


def node_depth(rc, el, M: np.ndarray, ctx: Ctx) -> float:
    cam = camera_at(rc, ctx.comp_t)
    Pw, _ = node_plane_world(rc, el, M, ctx)
    return float(cam.to_cam(Pw.mean(0))[2])


def depth_sort(rc, parent, order: list, ctx: Ctx) -> list:
    """Hook `depth_sort` for collapsed groups: 3D participants with equal z drawn farthest first."""
    cam = camera_at(rc, ctx.comp_t)
    slots, items = [], []
    for i, ch in enumerate(order):
        tag = ln(ch)
        d = None
        try:
            if tag == "object3D":
                c = node_clock(rc, ch, ctx.comp_t)
                if c is not None:
                    d = float(cam.to_cam(world3d(rc, ch, c)[:3, 3])[2])
            elif tag not in ("camera", "light", "adjustment", "transition") and is_threed(rc, ch):
                d = node_depth(rc, ch, rc.world_matrix(ch, ctx), rc.node_ctx(ch, ctx))
        except Exception:  # noqa: BLE001 — unplaceable nodes keep their slot
            d = None
        if d is not None:
            slots.append(i)
            z = int(float(ch.get("z", 0))) if tag != "object3D" else 0
            items.append((z, -d, i, ch))
    if len(items) < 2:
        return order
    out = list(order)
    for slot, (_, _, _, ch) in zip(slots, sorted(items, key=lambda x: (x[0], x[1], x[2]))):
        out[slot] = ch
    return out


def depth_of_field(rc, el, buf: Buf, ctx: Ctx, M: np.ndarray) -> Buf:
    """Per-pixel thin-lens depth of field for a threeD node's tile (called from finish_node).
    In the projective path the tile is the flat (pre-warp) drawing; the CoC is converted to flat
    pixels through the local Jacobian of the homography."""
    cam = camera_at(rc, ctx.comp_t)
    if not cam.dof or cam.ortho:
        return buf
    info = rc.frame_cache.get(("plane3d", el, ctx))
    if info is None:
        return buf
    Hm, Gc, _ = info
    h, w = buf.px.shape[:2]
    if h == 0 or w == 0:
        return buf
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float64)
    gx += buf.x0 + 0.5
    gy += buf.y0 + 0.5
    Ai = np.linalg.inv(M)                      # tile pixel -> local (u, v)
    u = Ai[0, 0] * gx + Ai[0, 1] * gy + Ai[0, 2]
    v = Ai[1, 0] * gx + Ai[1, 1] * gy + Ai[1, 2]
    depth = Gc[2, 0] * u + Gc[2, 1] * v + Gc[2, 2]
    coc = cam.coc_px(np.maximum(depth, cam.near)) * _root_scale(rc)          # frame px (radius)
    wz = Hm[2, 0] * u + Hm[2, 1] * v + Hm[2, 2]
    detH = abs(np.linalg.det(Hm))
    frame_per_local = np.sqrt(detH / np.maximum(np.abs(wz) ** 3, 1e-30))
    tile_per_local = math.sqrt(abs(np.linalg.det(M[:2, :2]))) or 1.0
    coc_tile = coc * tile_per_local / np.maximum(frame_per_local, 1e-9)
    if float(coc_tile.max()) < 0.5:
        return buf
    from .three.postfx import bokeh_blur
    pad = int(math.ceil(min(float(coc_tile.max()), 256.0))) + 2
    b = buf.pad(pad)
    cp = np.pad(coc_tile, pad, mode="edge")
    return Buf(bokeh_blur(b.px, cp, cam.blades), b.x0, b.y0)


def _root_scale(rc) -> float:
    return math.sqrt(abs(np.linalg.det(frame_matrix(rc)[:2, :2])))


def lens_distort(rc, buf: Buf, cam: Camera) -> Buf:
    """Brown-Conrady radial distortion (k1 = lensDistortion) of a frame-space tile; the result covers
    the frame, sampling the tile (which may extend beyond the frame, e.g. rendered with overscan)."""
    k = cam.lens_k
    if abs(k) < 1e-9:
        return buf
    R = frame_matrix(rc)
    fr = rc.frame_rect
    cx, cy = R[0, 0] * cam.ppx + R[0, 2], R[1, 1] * cam.ppy + R[1, 2]
    half_diag = math.hypot(R[0, 0] * cam.W, R[1, 1] * cam.H) / 2
    gy, gx = np.mgrid[fr[1]:fr[3], fr[0]:fr[2]].astype(np.float64)
    dx, dy = (gx + 0.5 - cx) / half_diag, (gy + 0.5 - cy) / half_diag
    rd = np.hypot(dx, dy)
    ru = rd.copy()
    for _ in range(8):                         # Newton on ru (1 + k ru^2) = rd
        f = ru * (1 + k * ru * ru) - rd
        fp = 1 + 3 * k * ru * ru
        ru = ru - f / np.where(np.abs(fp) > 1e-6, fp, 1e-6)
    ru = np.maximum(ru, 0)
    sc = np.where(rd > 1e-12, ru / np.maximum(rd, 1e-12), 1.0)
    sx = cx + dx * sc * half_diag - 0.5 - buf.x0
    sy = cy + dy * sc * half_diag - 0.5 - buf.y0
    from .three.postfx import sample_bilinear
    out = sample_bilinear(buf.px, sx, sy)
    return Buf(out, fr[0], fr[1])


def camera_post(rc, el, buf: Buf, ctx: Ctx) -> Buf:
    """Hook `camera_post`: frame-space lens distortion of a warped threeD layer."""
    cam = camera_at(rc, ctx.comp_t)
    return lens_distort(rc, buf, cam) if abs(cam.lens_k) > 1e-9 else buf


def install(rc) -> None:
    rc.hooks["camera"] = camera_hook
    rc.hooks["camera_post"] = camera_post
    rc.hooks["depth_sort"] = depth_sort
    rc.hooks["is_threed"] = is_threed
    rc.hooks["shutter_angle"] = shutter_angle
    from .nodes.scene3d import dome_backdrop
    rc.hooks["backdrop"] = dome_backdrop


try:
    from .render import hook_installer
    hook_installer("camera")(install)
except ImportError:      # pragma: no cover
    pass
