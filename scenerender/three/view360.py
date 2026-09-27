"""360 output: project/@mode="equirectangular" with <scene360> (layout, size, stereo, viewport camera).

Pinned decisions (the schema names the options; the rest is open):
  * The viewer sits at scene360/@viewportCamera (else the active camera, else the default camera);
    that camera's orientation defines "front" (its forward axis is the centre of the panorama, its up
    axis the zenith direction of the layout). Its lens (fov, DoF, distortion) is ignored; exposure
    applies.
  * 3D content (object3D, threeD layers, nodes in collapsed threeD groups and a visible dome) renders
    into all six cube faces around the viewer, 3D participants depth-sorted farthest first per face.
    The 2D composition (every other node, including captions burn-in) is rendered as the normal flat
    frame and placed on the front face: its width spans the front face's 90 degrees, centred
    vertically, composited over the 3D content (2D overlays stay readable). Project background fills
    the remaining sphere (applied by the Renderer like in standard mode). The colour finish hook runs
    once on the final panorama.
  * Layouts (per eye image of ew x eh):
      equirectangular: longitude -180..180 degrees left to right (centre = front), latitude +90 at
      the top.
      cubemap: 3 x 2 cells of 90 degree faces with linear (gnomonic) sampling: top row left, front,
      right (image up = zenith); bottom row down, back, up forming one continuous band (image right
      = the band direction down -> back -> up, image down = viewer right).
      eac: the same 3 x 2 arrangement with equi-angular sampling inside each cell (YouTube EAC
      parametrisation: cube coordinate = tan(pi/4 * cell coordinate)).
      fisheye-180: equidistant fisheye of the front hemisphere (r proportional to the angle from
      front) inscribed in the eye image; outside the circle is transparent.
  * Stereo: mono, top-bottom (left eye on top, each eye ew x eh/2) or left-right (left eye left, each
    eye ew/2 x eh). interpupillary is in metres (camera.UNITS_PER_METRE). Omni-directional stereo:
    the four horizontal faces are rendered in 8 vertical strips per face, each from eyes displaced by
    -+ipd/2 perpendicular to the strip's viewing azimuth; the up/down faces use the centre eye
    (monoscopic poles, the usual ODS pole merge).
  * Face resolution: equirect ew/4, cubemap/eac ew/3 (and eh/2), fisheye max(ew, eh)/2, each with a
    one-pixel margin so bilinear sampling is seamless across face edges.
"""
from __future__ import annotations

import math

import numpy as np

from .. import blend as blending
from ..camera import UNITS_PER_METRE, Camera, active_camera, build_camera, collapse_routed, is_threed, node_depth
from ..document import ln
from ..evaluator import Ctx
from ..raster import Buf
from ..registry import FEATURES, FULL, warn_once
from .postfx import sample_bilinear
from ..values import parse_bool

FEATURES.declare("scene360", FULL, "equirectangular / cubemap / eac / fisheye-180, mono / top-bottom / left-right "
                                    "ODS stereo, viewportCamera; 2D frame on the front face")
FEATURES.declare("project:mode", FULL, "standard; equirectangular via render_360; viewport films through "
                                        "scene360/@viewportCamera")

STRIPS = 8


def is_360(doc) -> bool:
    return doc.project.get("mode") == "equirectangular"


def params(doc) -> dict:
    s = doc.section("scene360")
    g = (lambda k, d: s.get(k, d)) if s is not None else (lambda k, d: d)
    return dict(layout=g("layout", "equirectangular"), width=int(g("width", 3840)), height=int(g("height", 1920)),
                camera=g("viewportCamera", None), stereo=g("stereo", "mono"),
                ipd=float(g("interpupillary", 0.064)) * UNITS_PER_METRE)


def size_360(doc, scale: float) -> tuple[int, int]:
    p = params(doc)
    return max(1, round(p["width"] * scale)), max(1, round(p["height"] * scale))


def _viewer(rc, t: float, p: dict) -> Camera:
    el = rc.doc.ids.get(p["camera"]) if p["camera"] else None
    if el is None:
        el = active_camera(rc, t)
    return build_camera(rc, el, t)


def _faces(cam: Camera) -> dict:
    """name -> (forward, up) of the six faces in world space."""
    f, u, r = cam.fwd, cam.up, cam.right
    return {"front": (f, u), "back": (-f, u), "right": (r, u), "left": (-r, u), "up": (u, -f), "down": (-u, f)}


def _face_camera(base: Camera, eye, fwd, up, S: int, s: float, x0: int = 0, x1: int | None = None) -> Camera:
    """90-degree face camera rendering columns [x0, x1) of a (S + 2)-pixel face (1-px margin)."""
    x1 = S + 2 if x1 is None else x1
    right = np.cross(fwd, up)
    k = 1.0 / s
    return Camera(np.asarray(eye, np.float64), right, np.asarray(up, np.float64), np.asarray(fwd, np.float64),
                  (S / 2) * k, False, 1.0, (x1 - x0) * k, (S + 2) * k, base.near, base.el, base.focus, base.fstop,
                  False, base.far, base.focal_mm, base.px_per_mm, 0, base.exposure, 0.0, None,
                  cx=(S / 2 + 1 - x0) * k, cy=(S / 2 + 1) * k)


class _Override:
    """Temporarily render rc at another frame size / camera."""

    def __init__(self, rc, w: int, h: int, cam: Camera | None, flag: str):
        self.rc, self.w, self.h, self.cam, self.flag = rc, w, h, cam, flag

    def __enter__(self):
        rc = self.rc
        self.saved = (rc.width, rc.height, rc.frame_rect, rc.root_matrix, rc.cache.get("camera_override"),
                      rc.cache.get("pass360"), rc.frame_cache)
        rc.width, rc.height = self.w, self.h
        rc.frame_rect = (0, 0, self.w, self.h)
        if self.cam is not None:
            rc.root_matrix = np.diag([rc.scale, rc.scale, 1.0])
            rc.cache["camera_override"] = self.cam
        rc.cache["pass360"] = self.flag
        rc.frame_cache = {k: v for k, v in rc.frame_cache.items()
                          if isinstance(k, tuple) and k and k[0] in ("world3d",)}
        return self

    def __exit__(self, *exc):
        rc = self.rc
        (rc.width, rc.height, rc.frame_rect, rc.root_matrix, ov, flag, fc) = self.saved
        for k, v in rc.frame_cache.items():
            if isinstance(k, tuple) and k and k[0] == "world3d":
                fc.setdefault(k, v)
        rc.frame_cache = fc
        if ov is None:
            rc.cache.pop("camera_override", None)
        else:
            rc.cache["camera_override"] = ov
        if flag is None:
            rc.cache.pop("pass360", None)
        else:
            rc.cache["pass360"] = flag


def participants(rc, t: float) -> list:
    """3D participants of the composition at t: (element, parent ctx, parent matrix, box, opacity)."""
    from dataclasses import replace
    from ..nodes.core import _repeat_vars, child_ctx
    out = []
    comp = rc.doc.section("composition")

    def walk(parent, ctx, PM, box, op):
        for ch in rc.child_order(parent, ctx):
            tag = ln(ch)
            if tag in ("camera", "adjustment", "transition", "skeleton") or ch in rc.hidden_mattes(ctx):
                continue
            if not rc.active(ch, ctx):
                continue
            if tag == "object3D":
                out.append((ch, ctx, PM, box, op))
                continue
            grp = tag in ("group", "sequence")
            routed = grp and parse_bool(ch.get("collapse")) and collapse_routed(rc)
            if is_threed(rc, ch) and not _in_flattening(ch, rc) and not routed:
                out.append((ch, ctx, PM, box, op))
                continue
            if grp:
                c2 = nctx = rc.enter_node(ch, ctx)
                M = rc.node_matrix(ch, c2, PM, box, None)
                size = rc.node_size(ch, nctx, box)
                walk(ch, _repeat_vars(ch, child_ctx(rc, ch, nctx)), M, size, op * rc.ev.num(ch, "opacity", nctx, 1.0))

    if comp is not None:
        walk(comp, Ctx(t=t, comp_t=t), rc.root_matrix, (rc.doc.width, rc.doc.height), 1.0)
    return out


def _in_flattening(el, rc) -> bool:
    from ..camera import _flattening_ancestor
    return _flattening_ancestor(el, rc)


def render_view(rc, t: float, cam: Camera, w: int, h: int) -> Buf:
    """The 3D content of the frame at t filmed by cam into a w x h buffer."""
    from . import scene
    with _Override(rc, w, h, cam, "3d"):
        dst = Buf.empty(0, 0, w, h)
        if scene.gl_ok():
            comp = rc.doc.section("composition")
            bg = scene.render_background_layer(rc, t, comp)
            if bg is not None:
                dst = blending.composite(dst, bg)
        parts = participants(rc, t)
        keyed = []
        for i, (el, ctx, PM, box, op) in enumerate(parts):
            try:
                if ln(el) == "object3D":
                    from ..camera import node_clock, world3d
                    c = node_clock(rc, el, t)
                    d = float(cam.to_cam(world3d(rc, el, c)[:3, 3])[2]) if c is not None else 0.0
                else:
                    d = node_depth(rc, el, rc.node_matrix(el, ctx, PM, box, None), rc.node_ctx(el, ctx))
            except Exception:  # noqa: BLE001
                d = 0.0
            keyed.append((-d, i, el, ctx, PM, box, op))
        for _, _, el, ctx, PM, box, op in sorted(keyed, key=lambda x: (x[0], x[1])):
            o = rc.render_node(el, ctx, PM, box)
            if o is not None:
                dst = blending.composite(dst, o.buf, o.blend, o.opacity * op)
        return dst.crop_to((0, 0, w, h)).expand_to((0, 0, w, h))


def _render_faces(rc, t: float, base: Camera, S: int, eye_sign: float, ipd: float) -> dict:
    faces = {}
    s = rc.scale
    up0 = base.up
    for name, (fwd, up) in _faces(base).items():
        horizontal = name in ("front", "back", "left", "right")
        if eye_sign == 0 or not horizontal:
            cam = _face_camera(base, base.eye, fwd, up, S, s)
            faces[name] = render_view(rc, t, cam, S + 2, S + 2).px
            continue
        cols = []
        bounds = [0] + [1 + round(j * S / STRIPS) for j in range(1, STRIPS)] + [S + 2]
        right = np.cross(fwd, up)
        for j in range(STRIPS):
            x0, x1 = bounds[j], bounds[j + 1]
            xc = ((x0 + x1) / 2 - 1) / S * 2 - 1          # strip centre in face coordinates [-1, 1]
            d = fwd + right * xc
            d /= np.linalg.norm(d)
            off = np.cross(d, up0)
            off /= max(np.linalg.norm(off), 1e-12)
            eye = base.eye + off * (eye_sign * ipd / 2)
            cam = _face_camera(base, eye, fwd, up, S, s, x0, x1)
            cols.append(render_view(rc, t, cam, x1 - x0, S + 2).px)
        faces[name] = np.concatenate(cols, 1)
    return faces


def _overlay_flat(face: np.ndarray, flat: np.ndarray) -> np.ndarray:
    """Composite the flat 2D frame over the front face (width = the face's 90 degrees, centred)."""
    S = face.shape[1] - 2
    fh, fw = flat.shape[:2]
    oh = S * fh / fw
    gy, gx = np.mgrid[0:S + 2, 0:S + 2].astype(np.float64)
    sx = (gx + 0.5 - 1) * fw / S - 0.5
    sy = (gy + 0.5 - 1 - (S - oh) / 2) * fw / S - 0.5
    src = sample_bilinear(flat, sx, sy)
    return src + face * (1 - src[..., 3:4])


def _sample_faces(faces: dict, base: Camera, D: np.ndarray) -> np.ndarray:
    """Bilinear lookup of world directions D (h, w, 3) in the rendered faces."""
    S = next(iter(faces.values())).shape[0] - 2
    out = np.zeros(D.shape[:2] + (4,), np.float32)
    fdict = _faces(base)
    names = list(fdict)
    dots = np.stack([D @ fdict[n][0] for n in names], -1)
    best = np.argmax(dots, -1)
    for i, n in enumerate(names):
        m = best == i
        if not m.any():
            continue
        fwd, up = fdict[n]
        right = np.cross(fwd, up)
        d = D[m]
        z = d @ fwd
        u = (d @ right) / z
        v = (d @ up) / z
        px = 1 + (u * 0.5 + 0.5) * S - 0.5
        py = 1 + (0.5 - v * 0.5) * S - 0.5
        out[m] = sample_bilinear(faces[n], px[None], py[None])[0]
    return out


def layout_dirs(layout: str, w: int, h: int, cam: Camera) -> tuple[np.ndarray, np.ndarray | None]:
    """World directions (h, w, 3) of every output pixel of one eye image, plus a validity mask."""
    R, U, F = cam.right, cam.up, cam.fwd
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float64)
    if layout == "equirectangular":
        lon = ((gx + 0.5) / w - 0.5) * 2 * math.pi
        lat = (0.5 - (gy + 0.5) / h) * math.pi
        d = (np.sin(lon) * np.cos(lat))[..., None] * R + np.sin(lat)[..., None] * U + (np.cos(lon) * np.cos(lat))[..., None] * F
        return d, None
    if layout == "fisheye-180":
        rad = min(w, h) / 2
        x = (gx + 0.5 - w / 2) / rad
        y = (gy + 0.5 - h / 2) / rad
        r = np.hypot(x, y)
        th = r * math.pi / 2
        sn = np.where(r > 1e-12, np.sin(th) / np.maximum(r, 1e-12), 0.0)
        d = np.cos(th)[..., None] * F + (x * sn)[..., None] * R - (y * sn)[..., None] * U
        return d, r <= 1.0
    # cubemap / eac: 3 x 2 cells
    cw, ch = w / 3, h / 2
    col = np.minimum((gx // cw).astype(int), 2)
    row = np.minimum((gy // ch).astype(int), 1)
    a = ((gx - col * cw + 0.5) / cw) * 2 - 1           # cell coordinates [-1, 1], image right / down
    b = ((gy - row * ch + 0.5) / ch) * 2 - 1
    if layout == "eac":
        a, b = np.tan(a * math.pi / 4), np.tan(b * math.pi / 4)
    alpha = (col - 1) * (math.pi / 2)
    ca, sa = np.cos(alpha)[..., None], np.sin(alpha)[..., None]
    top = row == 0
    # top band: centre F cos + R sin, image-right = d/dalpha, image-down = -U
    c_top = F * ca + R * sa
    h_top = -F * sa + R * ca
    v_top = np.broadcast_to(-U, c_top.shape)
    # bottom band: down -> back -> up; centre -F cos + U sin, image-right = d/dalpha, image-down = +R
    c_bot = -F * ca + U * sa
    h_bot = F * sa + U * ca
    v_bot = np.broadcast_to(R, c_bot.shape)
    c = np.where(top[..., None], c_top, c_bot)
    hh = np.where(top[..., None], h_top, h_bot)
    vv = np.where(top[..., None], v_top, v_bot)
    return c + hh * a[..., None] + vv * b[..., None], None


def render_360(rc, t: float, frame: int = 0) -> Buf:
    """The panorama of the frame at t (size scene360 width x height x render scale)."""
    p = params(rc.doc)
    W, H = size_360(rc.doc, rc.scale)
    hooks = rc.hooks
    saved = {k: hooks.pop(k) for k in ("render360", "finish") if k in hooks}
    rc.frame_cache = {}
    try:
        base = _viewer(rc, t, p)
        # flat 2D pass at the standard frame size
        fw = max(1, round(rc.doc.frame_width * rc.scale))
        fh = max(1, round(rc.doc.frame_height * rc.scale))
        with _Override(rc, fw, fh, None, "flat"):
            from ..compositor import scale as mscale
            rc.root_matrix = mscale(rc.scale, rc.scale) @ rc.reframe_matrix(rc.doc.reframe)
            flat = rc.render_frame(t, frame).px
        stereo = p["stereo"] if p["stereo"] in ("top-bottom", "left-right") else "mono"
        if stereo == "top-bottom":
            ew, eh = W, H // 2
        elif stereo == "left-right":
            ew, eh = W // 2, H
        else:
            ew, eh = W, H
        layout = p["layout"]
        if layout not in ("equirectangular", "cubemap", "eac", "fisheye-180"):
            warn_once("scene360", layout, "unknown layout; using equirectangular")
            layout = "equirectangular"
        S = {"equirectangular": ew / 4, "cubemap": max(ew / 3, eh / 2), "eac": max(ew / 3, eh / 2)}.get(
            layout, max(ew, eh) / 2)
        S = max(8, int(math.ceil(S)))
        D, valid = layout_dirs(layout, ew, eh, base)
        eyes = [0.0] if stereo == "mono" else [-1.0, 1.0]
        imgs = []
        for sign in eyes:
            faces = _render_faces(rc, t, base, S, sign, p["ipd"])
            faces["front"] = _overlay_flat(faces["front"], flat)
            img = _sample_faces(faces, base, D)
            if valid is not None:
                img *= valid[..., None]
            imgs.append(img)
        if stereo == "top-bottom":
            out = np.concatenate(imgs, 0)
        elif stereo == "left-right":
            out = np.concatenate(imgs, 1)
        else:
            out = imgs[0]
        buf = Buf(np.ascontiguousarray(out, np.float32), 0, 0).expand_to((0, 0, W, H))
    finally:
        hooks.update(saved)
    fin = hooks.get("finish")
    if fin is not None:
        buf = fin(rc, buf, Ctx(t=t, comp_t=t, frame=frame))
    return buf


def install(rc) -> None:
    if is_360(rc.doc):
        rc.hooks["render360"] = render_360


try:
    from ..render import hook_installer
    hook_installer("render360")(install)
except ImportError:      # pragma: no cover
    pass

__all__ = ["render_360", "size_360", "is_360", "layout_dirs"]
