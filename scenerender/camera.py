"""2.5D camera: projects threeD="true" nodes (and object3D fallbacks) through the active camera.

Conventions (pinned here; the schema leaves them open):
  * World space: document pixels, origin at the frame centre, +X right, +Y up, +Z toward the
    viewer. A 2D node's frame point (xd, yd) at zDepth d sits at (xd - W/2, H/2 - yd, -d), so a
    positive zDepth pushes the node away from the viewer.
  * rotationY > 0 turns the node's right edge away from the viewer; rotationX > 0 tilts its top
    edge away. Both pivot on the node's anchor point, applied after its 2D transform (X then Y).
  * Camera x, y, z are world coordinates. Orientation: `target` (look-at an object3D, camera or 2D
    node origin) else yaw (deg, > 0 turns right), pitch (deg, > 0 looks up); roll (deg, > 0
    clockwise on screen) applies in both cases. fov is horizontal (deg); focalLength with
    sensorWidth (mm) overrides it. orthographic uses orthoHeight world units across the frame
    height (default: frame height, i.e. 1:1).
  * No active camera: a default perspective camera (50 mm on 36 mm) at (0, 0, f) looking down -Z,
    where f is the focal length in pixels, so zDepth = 0 without rotation renders unchanged.
  * shake: seeded fractal noise; amplitude = world units along the camera's right/up axes,
    rotation = degrees of roll, zoom = fractional focal change; frequency Hz; [start, end).
The compositor works with affine matrices, so the hook returns the least-squares affine fit of
the node's projected box corners (exact for fronto-parallel planes, PARTIAL for tilted ones);
`project_quad` and `homography` give the exact quad for a future homography draw path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .document import ln
from .evaluator import Ctx
from .raster import Buf
from .registry import FEATURES, PARTIAL, warn_once

FEATURES.register("threeD", level=PARTIAL,
                  note="2.5D planes projected through the active camera as a best-fit affine (true perspective "
                       "needs the homography draw path); collapse is ignored")(None)
FEATURES.declare("camera:depthOfField", PARTIAL, "depth_of_field(): uniform blur per node by distance from focus")
FEATURES.declare("camera:shake", PARTIAL, "seeded noise on position/roll/zoom")
FEATURES.declare("camera:lensDistortion", "none", "ignored")
FEATURES.declare("camera:transformConstraint", "none", "camera constraints are ignored (use target)")

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

    def to_cam(self, P: np.ndarray) -> np.ndarray:
        """World points (..., 3) -> camera coordinates (right, up, depth)."""
        d = np.asarray(P, np.float64) - self.eye
        return np.stack([d @ self.right, d @ self.up, d @ self.fwd], -1)

    def project_cam(self, C: np.ndarray) -> np.ndarray:
        """Camera coordinates -> document-pixel screen points (..., 2)."""
        C = np.asarray(C, np.float64)
        if self.ortho:
            k = self.k_ortho
            return np.stack([self.W / 2 + C[..., 0] * k, self.H / 2 - C[..., 1] * k], -1)
        z = np.maximum(C[..., 2], 1e-9)
        return np.stack([self.W / 2 + self.fpx * C[..., 0] / z, self.H / 2 - self.fpx * C[..., 1] / z], -1)

    def project(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        C = self.to_cam(P)
        return self.project_cam(C), (C[..., 2] > self.near) | self.ortho

    def scale_at(self, depth: float) -> float:
        return self.k_ortho if self.ortho else self.fpx / max(depth, 1e-9)


# ------------------------------------------------------------------ world helpers
def frame_to_world(rc, q_doc: np.ndarray, zdepth: float) -> np.ndarray:
    """Document-pixel frame points (..., 2) at zDepth -> world (..., 3)."""
    q = np.asarray(q_doc, np.float64)
    W, H = rc.doc.width, rc.doc.height
    return np.stack([q[..., 0] - W / 2, H / 2 - q[..., 1], np.full(q.shape[:-1], -zdepth)], -1)


def _ctx(t: float) -> Ctx:
    return Ctx(t=t, comp_t=t)


def element_world_pos(rc, el, t: float) -> np.ndarray | None:
    if el is None:
        return None
    ev = rc.ev
    c = _ctx(t)
    tag = ln(el)
    if tag in ("object3D", "camera", "light"):
        return np.array([ev.num(el, "x", c, 0.0), ev.num(el, "y", c, 0.0), ev.num(el, "z", c, 0.0)])
    try:
        M = rc.world_matrix(el, c)
    except Exception:  # noqa: BLE001 — a bad target just leaves the camera orientation alone
        return None
    q = np.array([M[0, 2], M[1, 2]]) / rc.scale
    return frame_to_world(rc, q, ev.num(el, "zDepth", c, 0.0))


def _rot_axis(axis: np.ndarray, deg: float, v: np.ndarray) -> np.ndarray:
    """Rodrigues rotation of v about unit axis."""
    a = math.radians(deg)
    k = _norm(axis)
    return v * math.cos(a) + np.cross(k, v) * math.sin(a) + k * (k @ v) * (1 - math.cos(a))


def default_fpx(W: float) -> float:
    return (W / 2) / (36.0 / (2 * 50.0))


def _window_ok(rc, el, t: float) -> bool:
    p = el
    while p is not None and isinstance(p.tag, str):
        if ln(p) in ("composition", "symbol"):
            return True
        s, e = rc.doc.window(p)
        if t < s - 1e-9 or (e is not None and t >= e - 1e-9) or p.get("visible") == "false":
            return False
        p = p.getparent()
    return True


def cameras(rc) -> list:
    key = ("cameras",)
    hit = rc.cache.get(key)
    if hit is None:
        comp = rc.doc.section("composition")
        hit = [e for e in comp.iter() if isinstance(e.tag, str) and ln(e) == "camera"] if comp is not None else []
        rc.cache[key] = hit
    return hit


def active_camera(rc, t: float):
    """The last camera with active=true whose [start, end) (and its ancestors' windows) contains t."""
    found = None
    c = _ctx(t)
    for cam in cameras(rc):
        if rc.ev.bool(cam, "active", c, True) and _window_ok(rc, cam, t):
            found = cam
    return found


def _shake(rc, cam_el, t: float):
    """(dx, dy, droll, dzoom) from shake children."""
    dx = dy = dr = dz = 0.0
    ev = rc.ev
    c = _ctx(t)
    from .physics import fbm
    for i, sh in enumerate(s for s in cam_el if ln(s) == "shake"):
        s0 = ev.num(sh, "start", c, 0.0)
        e0 = ev.num(sh, "end", c, 0.0) if sh.get("end") is not None else None
        if t < s0 or (e0 is not None and t >= e0):
            continue
        seed = int(sh.get("seed")) if sh.get("seed") else ev.seed_for(cam_el, f"shake{i}")
        f = ev.num(sh, "frequency", c, 2.0)
        oc = int(ev.num(sh, "octaves", c, 2))
        amp = ev.num(sh, "amplitude", c, 10.0)
        n = [float(fbm(seed + k * 101, t * f, k * 7.31, 0.0, oc)) * 1.6 for k in range(4)]
        dx += n[0] * amp
        dy += n[1] * amp
        dr += n[2] * ev.num(sh, "rotation", c, 0.0)
        dz += n[3] * ev.num(sh, "zoom", c, 0.0)
    return dx, dy, dr, dz


def camera_at(rc, t: float) -> Camera:
    W, H = float(rc.doc.width), float(rc.doc.height)
    key = ("camera-at", t)
    hit = rc.frame_cache.get(key)
    if hit is not None:
        return hit
    el = active_camera(rc, t)
    if el is None:
        f = default_fpx(W)
        cam = Camera(np.array([0.0, 0.0, f]), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, -1.0]),
                     f, False, 1.0, W, H)
        rc.frame_cache[key] = cam
        return cam
    ev = rc.ev
    c = _ctx(t)
    eye = np.array([ev.num(el, "x", c, 0.0), ev.num(el, "y", c, 0.0), ev.num(el, "z", c, 0.0)])
    tgt = rc.doc.ids.get(el.get("target")) if el.get("target") else None
    tp = element_world_pos(rc, tgt, t) if tgt is not None and tgt is not el else None
    world_up = np.array([0.0, 1.0, 0.0])
    if tp is not None and np.linalg.norm(tp - eye) > 1e-9:
        fwd = _norm(tp - eye)
    else:
        yaw, pitch = math.radians(ev.num(el, "yaw", c, 0.0)), math.radians(ev.num(el, "pitch", c, 0.0))
        fwd = np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch), -math.cos(yaw) * math.cos(pitch)])
    right = np.cross(fwd, world_up)
    if np.linalg.norm(right) < 1e-9:
        right = np.array([1.0, 0.0, 0.0])
    right = _norm(right)
    up = np.cross(right, fwd)
    sx, sy, sr, sz = _shake(rc, el, t)
    roll = ev.num(el, "roll", c, 0.0) + sr
    if roll:
        right = _rot_axis(fwd, -roll, right)     # clockwise on screen
        up = _rot_axis(fwd, -roll, up)
    eye = eye + right * sx + up * sy
    if el.get("focalLength"):
        fl = ev.num(el, "focalLength", c, 50.0)
        sw = ev.num(el, "sensorWidth", c, 36.0)
        fov = 2 * math.degrees(math.atan(sw / (2 * fl)))
    else:
        fov = ev.num(el, "fov", c, 60.0)
    fov = min(179.0, max(0.1, fov))
    fpx = (W / 2) / math.tan(math.radians(fov) / 2) * (1 + sz)
    ortho = el.get("projection", "perspective") == "orthographic"
    oh = ev.num(el, "orthoHeight", c, H) if el.get("orthoHeight") else H
    focus = ev.num(el, "focusDistance", c, 1000.0)
    ft = rc.doc.ids.get(el.get("focusTarget")) if el.get("focusTarget") else None
    fp = element_world_pos(rc, ft, t) if ft is not None else None
    if fp is not None:
        focus = max(1e-3, float((fp - eye) @ fwd))
    cam = Camera(eye, right, up, fwd, fpx, ortho, H / oh * (1 + sz), W, H, max(0.1, ev.num(el, "near", c, 0.1)), el,
                 focus, ev.num(el, "fStop", c, 2.8), ev.bool(el, "depthOfField", c, False))
    rc.frame_cache[key] = cam
    return cam


# ------------------------------------------------------------------ 2.5D nodes
def node_plane_world(rc, el, M: np.ndarray, ctx: Ctx, size=None):
    """World positions of el's local box corners (TL, TR, BR, BL), plus the local corners used."""
    ev = rc.ev
    if size is None:
        size = rc.node_size(el, ctx, (rc.doc.width, rc.doc.height))
    w, h = size
    if w <= 0 or h <= 0:
        w = h = 100.0
    loc = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64)
    ax = ev.length(el, "anchorX", ctx, size[0] or w)
    ay = ev.length(el, "anchorY", ctx, size[1] or h)
    Minv_root = 1.0 / rc.scale
    def frame(p):
        q = np.c_[p, np.ones(len(p))] @ M.T
        return q[:, :2] * Minv_root
    zd = ev.num(el, "zDepth", ctx, 0.0)
    Pw = frame_to_world(rc, frame(loc), zd)
    pivot = frame_to_world(rc, frame(np.array([[ax, ay]])), zd)[0]
    rx, ry = ev.num(el, "rotationX", ctx, 0.0), ev.num(el, "rotationY", ctx, 0.0)
    if rx or ry:
        d = Pw - pivot
        a = math.radians(-rx)             # > 0: top edge away (-Z)
        ca, sa = math.cos(a), math.sin(a)
        d = np.stack([d[:, 0], d[:, 1] * ca - d[:, 2] * sa, d[:, 1] * sa + d[:, 2] * ca], -1)
        b = math.radians(ry)              # > 0: right edge away
        cb, sb = math.cos(b), math.sin(b)
        d = np.stack([d[:, 0] * cb + d[:, 2] * sb, d[:, 1], -d[:, 0] * sb + d[:, 2] * cb], -1)
        Pw = pivot + d
    return Pw, loc


def project_quad(rc, el, M: np.ndarray, ctx: Ctx, size=None):
    """Frame-pixel screen positions of el's box corners (TL, TR, BR, BL) through the active camera,
    or None when any corner is behind the camera."""
    cam = camera_at(rc, ctx.comp_t)
    Pw, _ = node_plane_world(rc, el, M, ctx, size)
    s, ok = cam.project(Pw)
    if not np.all(ok):
        return None
    return s * rc.scale


def homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """3x3 H with dst ~ H @ src for 4 point pairs."""
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    _, _, Vt = np.linalg.svd(np.array(A, np.float64))
    Hm = Vt[-1].reshape(3, 3)
    return Hm / Hm[2, 2] if abs(Hm[2, 2]) > 1e-12 else Hm


def _fit_affine(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    A = np.c_[src, np.ones(len(src))]
    X, *_ = np.linalg.lstsq(A, dst, rcond=None)
    out = np.eye(3)
    out[:2, :] = X.T
    return out


def camera_hook(rc, el, M: np.ndarray, ctx: Ctx) -> np.ndarray:
    """rc.hooks['camera']: best affine approximation of the projected plane (local -> frame px)."""
    cam = camera_at(rc, ctx.comp_t)
    Pw, loc = node_plane_world(rc, el, M, ctx)
    C = cam.to_cam(Pw)
    ok = (C[:, 2] > cam.near) | cam.ortho
    if ok.sum() < 3:
        return OFFSCREEN.copy()
    if not ok.all():
        warn_once("threeD", el.get("id", "?"), "plane crosses the camera near plane; drawn approximately")
    s = cam.project_cam(C[ok]) * rc.scale
    return _fit_affine(loc[ok], s)


def node_depth(rc, el, M: np.ndarray, ctx: Ctx) -> float:
    cam = camera_at(rc, ctx.comp_t)
    Pw, _ = node_plane_world(rc, el, M, ctx)
    return float(cam.to_cam(Pw.mean(0))[2])


def depth_of_field(rc, el, buf: Buf, ctx: Ctx, M: np.ndarray) -> Buf:
    """PARTIAL depth of field: blur the node's tile uniformly by its circle of confusion
    CoC = (f / N) * |d - focus| / d  (pixels, f = focal length in px, N = fStop) / 20."""
    cam = camera_at(rc, ctx.comp_t)
    if not cam.dof or cam.ortho:
        return buf
    d = node_depth(rc, el, M, ctx)
    if d <= cam.near:
        return buf
    coc = (cam.fpx / max(cam.fstop, 0.1)) * abs(d - cam.focus) / d / 20.0 * rc.scale
    sigma = min(64.0, coc / 2)
    if sigma < 0.3:
        return buf
    from .effects import gaussian
    pad = int(math.ceil(sigma * 3))
    b = buf.pad(pad)
    return Buf(gaussian(b.px, sigma), b.x0, b.y0)


def install(rc) -> None:
    rc.hooks["camera"] = camera_hook


try:
    from .render import hook_installer
    hook_installer("camera")(install)
except ImportError:      # pragma: no cover
    pass
