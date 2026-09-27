"""The frame compositor: walks the node tree at time t and returns a frame buffer.

Pinned rendering rules (where the schema leaves the choice open):
  * Local transform  M = T(x, y) · R(rotation) · Skew(skewX, skewY) · S(scaleX, scaleY) · T(-anchorX, -anchorY).
    Node geometry occupies its box (0,0)-(w,h) in local space; x/y are lengths relative to the parent box.
  * Times on nodes, keys and markers are composition time; a symbol instance, and a sequence child,
    runs its subtree on its own clock. Node windows are half-open [start, end).
  * A group's children run at  t' = start + (t - start - timeOffset) · timeScale.
  * Stacking is by z, then document order; later / higher is on top.
  * A group is isolated (rendered into its own buffer) when it has effects, masks, a matte,
    a non-normal blend, opacity < 1 or isolate="true"; otherwise its children blend straight
    into the parent's buffer with the group opacity folded in.
  * Masks are in node-local coordinates; matte nodes are drawn in their own place in the tree.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace

import numpy as np

from . import blend as blending
from .document import Document, ln
from .evaluator import Ctx, Evaluator
from .raster import Buf, Canvas, intersect, union
from .registry import ASSETS, ASSET_SIZES, EFFECTS, FEATURES, FULL, NODES, PARTIAL, TRANSITIONS, warn_once

log = logging.getLogger("scenerender")

FEATURES.declare("masks", FULL)
FEATURES.declare("trackMatte", FULL)
FEATURES.declare("parent", FULL)
FEATURES.declare("align", FULL, "aligns the transformed (rotated/skewed/scaled) box")
FEATURES.declare("groupLayout", FULL, "row/column/grid/stack, all justify and alignItems values (baseline: first text line)")


def translate(x, y):
    return np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], np.float64)


def scale(sx, sy):
    return np.array([[sx, 0, 0], [0, sy, 0], [0, 0, 1]], np.float64)


def rotate(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float64)


def skew(kx, ky):
    return np.array([[1, math.tan(math.radians(kx)), 0], [math.tan(math.radians(ky)), 1, 0], [0, 0, 1]], np.float64)


@dataclass
class Out:
    buf: Buf
    blend: str = "normal"
    opacity: float = 1.0


@dataclass
class RenderContext:
    doc: Document
    ev: Evaluator
    scale: float = 1.0                      # output pixels per document pixel
    motion_blur: bool = False
    cache: dict = field(default_factory=dict)
    frame_cache: dict = field(default_factory=dict)
    matte_nodes: set = field(default_factory=set)
    transitions: dict = field(default_factory=dict)   # parent element -> [transition elements]
    trans_members: dict = field(default_factory=dict)  # node -> transition elements it takes part in
    hooks: dict = field(default_factory=dict)          # optional modules: "camera", "particles", "captions", "finish"
    mb_center: float | None = None                     # frame centre time while the Renderer supersamples a frame
    working_primaries: str = "srgb"                    # primaries the frame is composited in (colour management)
    threed_routing: bool = True                        # every node of a collapsed 3D group goes through is_threed
    exclude: frozenset = frozenset()                   # nodes left out of this frame (QA before/after renders)
    _node_mb: bool = False
    _flat_depth: int = 0
    _skip_effects: set = field(default_factory=set)

    def __post_init__(self):
        d = self.doc
        self.linear = d.project.get("linearLight", "true") != "false"
        self.width = max(1, round(d.frame_width * self.scale))
        self.height = max(1, round(d.frame_height * self.scale))
        self.frame_rect = (0, 0, self.width, self.height)
        if d.project.get("mode") == "equirectangular" or d.section("scene360") is not None:
            from .three.view360 import is_360, size_360
            if is_360(d):
                self.width, self.height = size_360(d, self.scale)
                self.frame_rect = (0, 0, self.width, self.height)
        from . import color
        self.working_primaries = color.working_primaries_for(d) if self.linear else "srgb"
        self._wm = color.matrix("srgb", self.working_primaries) if self.working_primaries != "srgb" else None
        # The conversion matrix is process state (raster); the newest context owns it until a frame
        # of another context installs its own.
        self.install_working_primaries()
        self.root_matrix = scale(self.scale, self.scale) @ self.reframe_matrix(d.reframe)
        for el in d.root.iter():
            if isinstance(el.tag, str) and el.get("matte") and el.get("matteVisible", "false") != "true":
                m = d.ids.get(el.get("matte"))
                if m is not None:
                    self.matte_nodes.add(m)
            if ln(el) == "transition":
                self.transitions.setdefault(el.getparent(), []).append(el)
                m = d.ids.get(el.get("matte")) if el.get("matte") else None
                if m is not None:
                    self.matte_nodes.add(m)
                for k in ("from", "to"):
                    n = d.ids.get(el.get(k)) if el.get(k) else None
                    if n is not None:
                        self.trans_members.setdefault(n, []).append(el)

    def reframe_matrix(self, mode: str) -> np.ndarray:
        """Composition space -> layout frame for layout/@reframe fit, fit-blur and crop (reflow: identity)."""
        d = self.doc
        if mode == "reflow" or (d.width, d.height) == (d.frame_width, d.frame_height):
            return np.eye(3)
        lay = d.ids.get(d.layout)
        fx, fy = float(lay.get("focusX", 0.5)), float(lay.get("focusY", 0.5))
        kx, ky = d.frame_width / d.width, d.frame_height / d.height
        k = max(kx, ky) if mode == "crop" else min(kx, ky)
        return translate((d.frame_width - d.width * k) * fx, (d.frame_height - d.height * k) * fy) @ scale(k, k)

    # ------------------------------------------------------------ frame
    def install_working_primaries(self) -> None:
        from .raster import set_working_matrix
        set_working_matrix(self._wm)

    def render_frame(self, t: float, frame: int = 0) -> Buf:
        r360 = self.hooks.get("render360")
        if r360 is not None:
            return r360(self, t, frame)
        self.frame_cache = {}
        self.install_working_primaries()
        ctx = Ctx(t=t, comp_t=t, frame=frame)
        comp = self.doc.section("composition")
        out = Buf.empty(0, 0, self.width, self.height)
        out = self.render_children(comp, out, ctx, self.root_matrix, (self.doc.width, self.doc.height), 1.0)
        if self.doc.reframe == "fit-blur" and self.doc.layout:
            out = self._fit_blur_backdrop(out, ctx, comp)
        for name in ("captions", "finish"):
            hook = self.hooks.get(name)
            if hook:
                out = hook(self, out, ctx)
        return out.crop_to(self.frame_rect).expand_to(self.frame_rect)

    def _fit_blur_backdrop(self, fitted: Buf, ctx, comp) -> Buf:
        """fit-blur: a blurred, cover-scaled copy of the composition fills the bars around the fitted frame."""
        from .effects import gaussian
        cover = scale(self.scale, self.scale) @ self.reframe_matrix("crop")
        back = self.render_children(comp, Buf.empty(0, 0, self.width, self.height), ctx, cover,
                                    (self.doc.width, self.doc.height), 1.0)
        back = back.crop_to(self.frame_rect).expand_to(self.frame_rect)
        back = Buf(gaussian(back.px, max(4.0, 0.02 * self.width)) * 0.85, 0, 0)
        return blending.composite(back, fitted)

    # ------------------------------------------------------------ children
    def child_order(self, parent) -> list:
        key = ("order", parent)
        hit = self.cache.get(key)
        if hit is None:
            kids = [c for c in self.doc.nodes(parent) if ln(c) != "transition"]
            # On object3D and camera, z is a 3D coordinate, not a stacking index.
            sz = lambda c: 0 if ln(c) in ("object3D", "camera") else int(float(c.get("z", 0)))  # noqa: E731
            hit = [c for _, _, c in sorted((sz(c), i, c) for i, c in enumerate(kids))]
            self.cache[key] = hit
        return hit

    def render_children(self, parent, dst: Buf, ctx: Ctx, M: np.ndarray, box: tuple[float, float],
                        fold_opacity: float, layout: dict | None = None) -> Buf:
        layout = layout if layout is not None else self.layout_positions(parent, ctx, box)
        active_tr = self.active_transitions(parent, ctx)
        done_tr = set()
        order = self.child_order(parent)
        ds = self.hooks.get("depth_sort")
        if ds is not None and parent.get("collapse") == "true":
            order = ds(self, parent, order, ctx)   # collapsed 3D group: farthest first
        for child in order:
            if child in self.matte_nodes or child in self.exclude:
                continue
            tr = next((tr for tr in active_tr if child in tr[1:3]), None)
            if tr is not None:
                if tr[0] in done_tr:
                    continue
                done_tr.add(tr[0])
                out = self.render_transition(tr, ctx, M, box, layout)
            else:
                if ln(child) == "adjustment":
                    dst = self.apply_adjustment(child, dst, ctx, M, box, fold_opacity)
                    continue
                if ln(child) in ("group", "sequence"):
                    passed = self._passthrough(child, dst, ctx, M, box, layout, fold_opacity)
                    if passed is not None:
                        dst = passed
                        continue
                out = self.render_node(child, ctx, M, box, layout.get(child))
            if out is not None:
                dst = blending.composite(dst, out.buf, out.blend, out.opacity * fold_opacity)
        return dst

    def _passthrough(self, el, dst, ctx, PM, box, layout, fold_opacity) -> Buf | None:
        """Render a non-isolated group straight into dst so its children blend with the real backdrop.
        Returns the updated dst, or None when the group must be isolated."""
        from .nodes.core import _repeat_vars, child_ctx, is_isolated
        if is_isolated(self, el, self.node_ctx(el, ctx)):
            return None
        if not self.active(el, ctx):
            return dst
        shift = self.doc.clock_shift.get(el)
        if shift:
            ctx = replace(ctx, t=ctx.t - shift)
        nctx = self.node_ctx(el, ctx)
        M = self.node_matrix(el, ctx, PM, box, layout.get(el))
        size = self.node_size(el, nctx, box)
        cctx = _repeat_vars(el, child_ctx(self, el, nctx))
        return self.render_children(el, dst, cctx, M, size, fold_opacity)

    # ------------------------------------------------------------ node
    def active(self, el, ctx: Ctx, force: bool = False) -> bool:
        if el.get("visible") == "false":
            return False
        if not force:
            s, e = self.doc.window(el)
            if ctx.t < s - 1e-9 or (e is not None and ctx.t >= e - 1e-9):
                return False
        return self.ev.condition(el, ctx)

    def node_ctx(self, el, ctx: Ctx) -> Ctx:
        s, e = self.doc.window(el)
        return replace(ctx, node_start=s, node_end=e)

    def local_matrix(self, el, ctx: Ctx, box, size, layout_pos=None) -> np.ndarray:
        ev = self.ev
        bw, bh = box
        w, h = size
        x = ev.length(el, "x", ctx, bw)
        y = ev.length(el, "y", ctx, bh)
        if layout_pos is not None:
            x += layout_pos[0]
            y += layout_pos[1]
        ax = ev.length(el, "anchorX", ctx, w)
        ay = ev.length(el, "anchorY", ctx, h)
        sx = ev.num(el, "scaleX", ctx, 1.0)
        sy = ev.num(el, "scaleY", ctx, 1.0)
        rot = ev.num(el, "rotation", ctx, 0.0)
        orient = ev.motion_path_angle(el, ctx)
        if orient is not None:
            rot += orient
        kx, ky = ev.num(el, "skewX", ctx, 0.0), ev.num(el, "skewY", ctx, 0.0)
        phys = self.hooks.get("physics")
        pose = phys(self, el, ctx) if phys is not None else None
        if pose is not None:  # simulated rigid body: parent-space x/y (px) and rotation (deg)
            x, y, rot = pose
        if pose is None and (el.get("alignX") or el.get("alignY")):
            x, y, sx, sy = self._align(el, ctx, box, (w, h), x, y, ax, ay, sx, sy, rot, kx, ky)
        L = translate(x, y) @ rotate(rot)
        if kx or ky:
            L = L @ skew(kx, ky)
        return L @ scale(sx, sy) @ translate(-ax, -ay)

    def _align(self, el, ctx, box, size, x, y, ax, ay, sx, sy, rot=0.0, kx=0.0, ky=0.0):
        """Place x/y (and scale for stretch) so the node's transformed box aligns within alignTo."""
        ev = self.ev
        target = el.get("alignTo", "parent")
        bx0, by0, bw, bh = 0.0, 0.0, box[0], box[1]
        if target in ("frame", "safe-area"):
            bw, bh = self.doc.width, self.doc.height
            if target == "safe-area":
                l, t_, r, b = self.safe_insets()
                bx0, by0, bw, bh = l * bw, t_ * bh, bw * (1 - l - r), bh * (1 - t_ - b)
        m = ev.length(el, "margin", ctx, min(bw, bh))
        w, h = size
        ax_mode, ay_mode = el.get("alignX"), el.get("alignY")
        if ax_mode == "stretch" and w > 0:
            sx = (bw - 2 * m) / w
        if ay_mode == "stretch" and h > 0:
            sy = (bh - 2 * m) / h

        def extent():
            lin = rotate(rot) @ skew(kx, ky) @ scale(sx, sy)
            pts = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float64) - [ax, ay, 0]
            q = pts @ lin.T
            return q[:, 0].min(), q[:, 0].max(), q[:, 1].min(), q[:, 1].max()

        x0, x1, y0, y1 = extent()
        if ax_mode in ("left", "stretch"):
            x = bx0 + m - x0
        elif ax_mode == "center":
            x = bx0 + (bw - (x1 - x0)) / 2 - x0
        elif ax_mode == "right":
            x = bx0 + bw - m - x1
        if ay_mode in ("top", "stretch"):
            y = by0 + m - y0
        elif ay_mode == "middle":
            y = by0 + (bh - (y1 - y0)) / 2 - y0
        elif ay_mode == "bottom":
            y = by0 + bh - m - y1
        return x, y, sx, sy

    def safe_insets(self) -> tuple[float, float, float, float]:
        sa_id = self.doc.project.get("safeArea")
        lay = self.doc.ids.get(self.doc.layout) if self.doc.layout else None
        if lay is not None and lay.get("safeArea"):
            sa_id = lay.get("safeArea")
        sa = self.doc.ids.get(sa_id) if sa_id else None
        if sa is None:
            return (0.0, 0.0, 0.0, 0.0)
        from .safe_areas import insets
        return insets(sa, self.doc.width, self.doc.height)

    def node_size(self, el, ctx: Ctx, box) -> tuple[float, float]:
        """The node's own box (used for anchors, % children, layout and alignment)."""
        tag = ln(el)
        ev = self.ev
        if tag in ("shape", "group", "sequence"):
            w = ev.length(el, "width", ctx, box[0], box[0] if tag != "shape" else 0.0)
            h = ev.length(el, "height", ctx, box[1], box[1] if tag != "shape" else 0.0)
            return w, h
        if tag == "layer":
            if el.get("boxWidth") or el.get("width"):
                return (ev.length(el, "boxWidth" if el.get("boxWidth") else "width", ctx, box[0]),
                        ev.length(el, "boxHeight" if el.get("boxHeight") else "height", ctx, box[1]))
            asset = self.layer_asset(el, ctx)
            return self.asset_size(asset, ctx) if asset is not None else (0.0, 0.0)
        if tag == "instance":
            if el.get("boxWidth"):
                return ev.length(el, "boxWidth", ctx, box[0]), ev.length(el, "boxHeight", ctx, box[1])
            sym = self.doc.ids.get(el.get("symbol"))
            if sym is not None and sym.get("width"):
                return float(sym.get("width")), float(sym.get("height"))
            return box
        if tag == "particleEmitter":
            return ev.num(el, "emitterWidth", ctx, 0.0), ev.num(el, "emitterHeight", ctx, 0.0)
        return box

    def asset_size(self, asset, ctx) -> tuple[float, float]:
        fn = ASSET_SIZES.get(ln(asset))
        if fn:
            return fn(self, asset, ctx)
        return float(asset.get("width", 0) or 0), float(asset.get("height", 0) or 0)

    def layer_asset(self, el, ctx: Ctx):
        aid = self.ev.str(el, "asset", ctx)
        return self.doc.ids.get(aid) if aid else None

    def world_matrix(self, el, ctx: Ctx) -> np.ndarray:
        """Frame-space matrix of el's local space (used by @parent, constraints and mattes)."""
        key = ("world", el, ctx.t, ctx.scope)
        hit = self.frame_cache.get(key)
        if hit is not None:
            return hit
        parent = el.getparent()
        chain_box = (self.doc.width, self.doc.height)
        if parent is None or ln(parent) in ("composition", "symbol", "symbols", "scene"):
            PM = self.root_matrix
            if parent is not None and ln(parent) == "symbol" and parent.get("width"):
                chain_box = (float(parent.get("width")), float(parent.get("height")))
        else:
            PM = self.world_matrix(parent, ctx)
            chain_box = self.node_size(parent, ctx, (self.doc.width, self.doc.height))
        M = self.node_matrix(el, ctx, PM, chain_box, None)
        self.frame_cache[key] = M
        return M

    def node_matrix(self, el, ctx, PM, box, layout_pos) -> np.ndarray:
        nctx = self.node_ctx(el, ctx)
        size = self.node_size(el, nctx, box)
        L = self.local_matrix(el, nctx, box, size, layout_pos)
        parent_id = el.get("parent")
        if parent_id and parent_id in self.doc.ids:
            PM = self.world_matrix(self.doc.ids[parent_id], ctx)
        M = PM @ L
        for c in el:
            if ln(c) == "transformConstraint":
                from .constraints import apply_constraint
                M = apply_constraint(self, c, el, M, nctx)
        return M

    def is_threed(self, el) -> bool:
        """2.5D participation: threeD="true", or a child of a collapsed 3D group (camera hook decides)."""
        fn = self.hooks.get("is_threed")
        return fn(self, el) if fn is not None else el.get("threeD") == "true"

    def temporal_effects(self, el) -> list:
        """(effect element, handler) pairs of el's effects that sample other times."""
        key = ("temporal", el)
        hit = self.cache.get(key)
        if hit is None:
            hit = []
            for eid in (el.get("effects") or "").split():
                e = self.doc.ids.get(eid)
                fn = EFFECTS.get(e.get("type")) if e is not None else None
                if fn is not None and getattr(fn, "temporal", False):
                    hit.append((e, fn))
            self.cache[key] = hit
        return hit

    def _ghost_active(self, el, ctx: Ctx) -> bool:
        """Past its window, a node with a lookback effect (echo) stays alive while its tail lasts."""
        if el.get("visible") == "false":
            return False
        s, e = self.doc.window(el)
        if e is None or ctx.t < e - 1e-9:
            return False
        tail = max((getattr(fn, "lookback", lambda *a: 0.0)(self, fx, ctx) for fx, fn in self.temporal_effects(el)), default=0.0)
        return ctx.t < e + tail

    def render_node(self, el, ctx: Ctx, PM, box, layout_pos=None, force: bool = False) -> Out | None:
        if not self.active(el, ctx, force):
            if self.temporal_effects(el) and self._ghost_active(el, ctx):
                return self._render_node_once(el, ctx, PM, box, layout_pos, ghost=True)
            return None
        mode = self.motion_blur_mode(el)
        if self.motion_blur and mode == "off" and self.mb_center is not None:
            # Excluded from the frame's shutter: always drawn at the frame's centre time.
            ctx = ctx.at(ctx.t + (self.mb_center - ctx.comp_t))
        elif not self.motion_blur and mode == "on" and not self._node_mb:
            return self._render_node_blurred(el, ctx, PM, box, layout_pos, force)
        return self._render_node_once(el, ctx, PM, box, layout_pos)

    def motion_blur_mode(self, el) -> str:
        """Effective node motionBlur: the nearest non-"inherit" value on el or its ancestors."""
        key = ("mb", el)
        hit = self.cache.get(key)
        if hit is None:
            hit = "inherit"
            for n in [el, *el.iterancestors()]:
                v = n.get("motionBlur") if isinstance(n.tag, str) else None
                if v in ("on", "off"):
                    hit = v
                    break
            self.cache[key] = hit
        return hit

    def _render_node_blurred(self, el, ctx, PM, box, layout_pos, force) -> Out | None:
        p = self.doc.project
        n = max(1, int(p.get("motionBlurSamples", 16)))
        fps = float(self.doc.fps)
        base = float(p.get("shutterAngle", 180))
        sa = self.hooks.get("shutter_angle")
        shutter = (sa(self, ctx.comp_t, base) if sa else base) / 360.0 / fps   # active camera may override
        phase = float(p.get("shutterPhase", -90)) / 360.0 / fps
        outs = []
        self._node_mb = True
        try:
            for i in range(n):
                dt = phase + shutter * (i + 0.5) / n
                o = self._render_node_once(el, ctx.at(ctx.t + dt), PM, box, layout_pos)
                if o is not None:
                    outs.append(o)
        finally:
            self._node_mb = False
        if not outs:
            return None
        r = None
        for o in outs:
            r = union(r, o.buf.rect)
        acc = np.zeros((r[3] - r[1], r[2] - r[0], 4), np.float32)
        for o in outs:
            acc += o.buf.region(r) * o.opacity
        return Out(Buf(acc / n, r[0], r[1]), outs[0].blend, 1.0)

    def render_node_at(self, el, t: float, ctx: Ctx, effects: bool = False) -> Out | None:
        """el rendered at composition time t in its own place (for temporal effects such as echo);
        its effect stack is skipped unless effects=True, so an effect can't recurse into itself."""
        c2 = ctx.at(ctx.t + (t - ctx.comp_t))
        parent = el.getparent()
        root_like = parent is None or ln(parent) in ("composition", "symbol", "symbols", "scene")
        PM = self.root_matrix if root_like else self.world_matrix(parent, c2)
        box = (self.doc.width, self.doc.height) if root_like else self.node_size(parent, c2, (self.doc.width, self.doc.height))
        if not effects:
            self._skip_effects.add(el)
        try:
            return self._render_node_once(el, c2, PM, box, None)
        finally:
            self._skip_effects.discard(el)

    def _render_node_once(self, el, ctx: Ctx, PM, box, layout_pos=None, ghost: bool = False) -> Out | None:
        tag = ln(el)
        shift = self.doc.clock_shift.get(el)
        if shift:
            ctx = replace(ctx, t=ctx.t - shift)
        nctx = self.node_ctx(el, ctx)
        opacity = self.ev.num(el, "opacity", nctx, 1.0)
        temporal = bool(self.temporal_effects(el))
        if opacity <= 1e-4 and not temporal:
            return None
        handler = NODES.get(tag)
        if handler is None:
            warn_once("node", tag)
            return None
        M = self.node_matrix(el, ctx, PM, box, layout_pos)
        if abs(M[0, 0] * M[1, 1] - M[0, 1] * M[1, 0]) < 1e-12:
            return None  # collapsed to zero area (e.g. scale 0): nothing visible, and cairo can't invert it
        size = self.node_size(el, nctx, box)
        if self.is_threed(el) and self.hooks.get("camera"):
            M = self.hooks["camera"](self, el, M, nctx)
            if M is None:
                return None
        if temporal:
            # Temporal effects combine renders from several times, so each carries its own opacity
            # (and a node past its window contributes only its tail): fold opacity into the pixels.
            buf = Buf.null() if ghost else handler(self, el, nctx, M, size)
            if isinstance(buf, Out):
                buf = Buf(buf.buf.px * buf.opacity, buf.buf.x0, buf.buf.y0)
            if buf is None:
                buf = Buf.null()
            buf = Buf(buf.px * opacity, buf.x0, buf.y0)
            buf = self.finish_node(el, buf, nctx, M, size, PM, box)
            return None if buf is None or buf.is_null else Out(buf, el.get("blend", "normal"), 1.0)
        if abs(M[2, 0]) + abs(M[2, 1]) > 1e-12:
            buf = self._render_projective(el, handler, nctx, M, size, PM, box)
        else:
            buf = handler(self, el, nctx, M, size)
            if isinstance(buf, Out):
                return buf
            if buf is not None:
                buf = self.finish_node(el, buf, nctx, M, size, PM, box)
        if buf is None:
            return None
        return Out(buf, el.get("blend", "normal"), opacity)

    def _render_projective(self, el, handler, ctx, H, size, PM, box) -> Buf | None:
        """Perspective: draw the node flat at a resolution matching its projected size, run its
        deform/masks/effects there, then warp the tile through the homography into the frame."""
        from .raster import projected_scale, warp_projective
        w, h = max(1.0, size[0]), max(1.0, size[1])
        k = projected_scale(H, w, h)
        if k <= 0:
            return None
        A = scale(k, k)
        self._flat_depth += 1
        try:
            flat = handler(self, el, ctx, A, size)
            if isinstance(flat, Out):
                flat = flat.buf
            if flat is not None:
                flat = self.finish_node(el, flat, ctx, A, size, PM, box)
        finally:
            self._flat_depth -= 1
        if flat is None:
            return None
        out = warp_projective(flat, H @ np.linalg.inv(A), self.frame_rect)
        post = self.hooks.get("camera_post")
        if post is not None and out is not None and not self._flat_depth:
            out = post(self, el, out, ctx)   # lens distortion of the active camera
        return out

    def finish_node(self, el, buf: Buf, ctx, M, size, PM, box) -> Buf | None:
        """Deform, depth of field, masks, effect stack and track matte, in that order."""
        dh = self.hooks.get("deform")
        if dh is not None and any(ln(c) in ("deform", "softBody") for c in el):
            buf = dh(self, el, buf, ctx, M, size)
        if self.is_threed(el) and self.hooks.get("camera"):
            from .camera import depth_of_field
            buf = depth_of_field(self, el, buf, ctx, M)
        masks = [m for m in el if ln(m) == "mask"]
        if masks:
            from .masks import apply_masks
            buf = apply_masks(self, masks, buf, ctx, M, size)
        fx = el.get("effects")
        if fx and ln(el) != "adjustment" and el not in self._skip_effects:
            buf = self.apply_effects(fx.split(), buf, ctx, el)
        if el.get("matte"):
            from .masks import apply_matte
            buf = apply_matte(self, el, buf, ctx)
        return buf

    def apply_effects(self, ids: list[str], buf: Buf, ctx, node=None) -> Buf:
        for eid in ids:
            e = self.doc.ids.get(eid)
            if e is None:
                warn_once("effect", eid, "effect id not found")
                continue
            if not self.ev.bool(e, "enabled", ctx, True):
                continue
            typ = e.get("type")
            fn = EFFECTS.get(typ)
            if fn is None:
                warn_once("effect", typ)
                continue
            mix = self.ev.num(e, "mix", ctx, 1.0)
            if mix <= 0:
                continue
            res = fn(self, e, buf, ctx, node)
            if res is None:
                continue
            if mix < 1:
                r = union(buf.rect, res.rect)
                a, b = buf.region(r), res.region(r)
                res = Buf(a + (b - a) * mix, r[0], r[1])
            buf = res
        return buf

    def apply_adjustment(self, el, dst: Buf, ctx, PM, box, fold_opacity) -> Buf:
        if not self.active(el, ctx):
            return dst
        nctx = self.node_ctx(el, ctx)
        opacity = self.ev.num(el, "opacity", nctx, 1.0) * fold_opacity
        if opacity <= 0:
            return dst
        region = dst.expand_to(self.frame_rect) if dst.rect != self.frame_rect else dst
        before = region.copy()
        after = self.apply_effects((el.get("effects") or "").split(), region.copy(), nctx, el)
        after = after.crop_to(before.rect).expand_to(before.rect)
        M = self.node_matrix(el, ctx, PM, box, None)
        size = self.node_size(el, nctx, box)
        cov = np.ones(before.px.shape[:2], np.float32) * opacity
        masks = [m for m in el if ln(m) == "mask"]
        if masks:
            from .masks import mask_coverage
            cov *= mask_coverage(self, masks, before.rect, nctx, M, size)
        if el.get("matte"):
            from .masks import matte_coverage
            cov *= matte_coverage(self, el, before.rect, nctx)
        mode = el.get("blend", "normal")
        if mode == "normal":
            before.px += (after.px - before.px) * cov[..., None]
            return before
        mixed = blending.composite(before.copy(), Buf(after.px * cov[..., None], before.x0, before.y0), mode, 1.0)
        return mixed

    # ------------------------------------------------------------ layout
    def layout_positions(self, parent, ctx: Ctx, box) -> dict:
        mode = parent.get("layout", "none") if ln(parent) in ("group", "sequence") else "none"
        if mode == "none":
            return {}
        from .layout import flex_layout
        return flex_layout(self, parent, ctx, box, mode)

    # ------------------------------------------------------------ transitions
    def transition_window(self, tr) -> tuple[float, float, float] | None:
        d = self.doc
        a = d.ids.get(tr.get("from")) if tr.get("from") else None
        b = d.ids.get(tr.get("to")) if tr.get("to") else None
        if a is None and b is None:
            return None
        dur = float(tr.get("duration", 0.5))
        cut = d.window(b)[0] if b is not None else (d.window(a)[1] if d.window(a)[1] is not None else d.duration)
        al = tr.get("alignment", "center")
        s0 = cut - dur / 2 if al == "center" else cut if al == "start" else cut - dur
        return s0, s0 + dur, cut

    def active_transitions(self, parent, ctx: Ctx) -> list:
        out = []
        for tr in self.transitions.get(parent, ()):
            w = self.transition_window(tr)
            if w is None or tr.get("type") == "cut":
                continue
            s0, s1, _ = w
            if s0 <= ctx.t < s1:
                a = self.doc.ids.get(tr.get("from")) if tr.get("from") else None
                b = self.doc.ids.get(tr.get("to")) if tr.get("to") else None
                out.append((tr, a, b, s0, s1))
        return out

    def render_transition(self, tr_info, ctx, PM, box, layout) -> Out | None:
        from . import curves
        tr, a, b, s0, s1 = tr_info
        u = (ctx.t - s0) / max(1e-9, s1 - s0)
        p = curves.get(tr.get("curve", "ease-in-out"))(min(1.0, max(0.0, u)))
        full = lambda n: None if n is None else self._full(self.render_node(n, ctx, PM, box, layout.get(n), force=True))  # noqa: E731
        A, B = full(a), full(b)
        fn = TRANSITIONS.get(tr.get("type"))
        if fn is None:
            warn_once("transition", tr.get("type"), "not supported; falling back to crossfade")
            fn = TRANSITIONS.get("crossfade")
        # The mixed picture blends onto the backdrop with the members' mode (incoming wins past halfway).
        lead = b if (b is not None and (p >= 0.5 or a is None)) else a
        return Out(fn(self, tr, A, B, p, ctx), lead.get("blend", "normal") if lead is not None else "normal")

    def _full(self, out: Out | None) -> Buf:
        """A member flattened to a full frame with its opacity (its blend mode applies to the mix)."""
        base = Buf.empty(0, 0, self.width, self.height)
        if out is None:
            return base
        return blending.composite(base, out.buf, "normal", out.opacity, grow=False)

    # ------------------------------------------------------------ helpers for handlers
    def canvas_for(self, M, w, h, pad: float = 2.0, clip_to_frame: bool = True):
        from .raster import transformed_rect
        r = transformed_rect(M, 0, 0, w, h, pad)
        if self._flat_depth:
            # Flat (pre-perspective) tiles are not frame-aligned; cap their size instead of clipping.
            r = intersect(r, (-8192, -8192, 8192, 8192))
            if r is None:
                return None
        elif clip_to_frame:
            margin = 64
            r = intersect(r, (-margin, -margin, self.width + margin, self.height + margin))
            if r is None:
                return None
        c = Canvas(r)
        c.set_matrix(M)
        return c

    def eval_attrs(self, el, ctx, names) -> dict:
        ev = self.ev
        out = {}
        for n in names:
            v = ev.get(el, n, ctx, None)
            if v is not None:
                out[n] = v
        return out
