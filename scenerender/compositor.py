"""The frame compositor: walks the node tree at time t and returns a frame buffer.

Pinned rendering rules (where the schema leaves the choice open):
  * Local transform  M = T(x, y) · R(rotation) · Skew(skewX, skewY) · S(scaleX, scaleY) · T(-anchorX, -anchorY).
    Node geometry occupies its box (0,0)-(w,h) in local space; x/y and anchorX/anchorY are lengths relative to the parent box (% of the parent, never of the node).
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
from .values import parse_bool

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
    matte_users: tuple = ()
    transitions: dict = field(default_factory=dict)   # parent element -> [transition elements]
    trans_members: dict = field(default_factory=dict)  # node -> transition elements it takes part in
    hooks: dict = field(default_factory=dict)          # optional modules: "camera", "particles", "captions", "finish"
    mb_center: float | None = None                     # frame centre time while the Renderer supersamples a frame
    working_primaries: str = "srgb"                    # primaries the frame is composited in (colour management)
    threed_routing: bool = True                        # every node of a collapsed 3D group goes through is_threed
    exclude: frozenset = frozenset()                   # nodes left out of this frame (QA before/after renders)
    _node_mb: bool = False
    mb_interval: tuple | None = None         # (first, last) shutter sample times of the frame being supersampled
    _mb_nodes: dict | None = None            # node outputs reusable across this frame's shutter samples
    _mb_static: dict = field(default_factory=dict)
    _fx_memo: dict = field(default_factory=dict)   # (node, effects) -> last input and output, see _reuse_effect
    _root: object = None      # the context this one was copied from (None: it is a root), see base()
    _fx_const: dict = field(default_factory=dict)  # effect element -> parameters provably constant
    _fx_gen: int = 0
    _mb_mode: dict = field(default_factory=dict)   # node -> (undriven chain, its motionBlur mode)
    _flat_depth: int = 0
    _skip_effects: set = field(default_factory=set)
    scene_context: tuple | None = None       # (symbol root, context at its entrance)
    scene_matrix: np.ndarray | None = None   # symbol canvas -> output frame
    _rcache: dict = field(default_factory=dict)    # raster cache entries, see _render_cached
    _rc_ok: dict = field(default_factory=dict)     # (node, window) -> content provably static
    _rc_inside: int = 0                            # > 0 while rendering a raster-cache entry
    _gpu_frame: bool = False                       # this frame composites on the GPU (see gpucomp)

    def __post_init__(self):
        import cairo
        if cairo.cairo_version() < 11800 or not hasattr(cairo, "FORMAT_RGBA128F"):
            raise RuntimeError("Rendering requires Cairo >= 1.18 and Pycairo >= 1.25 "
                               "for deterministic gradient dithering; see docs/RUNTIME.md")
        d = self.doc
        self.linear = parse_bool(d.project.get("linearLight", "true"), True)
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
        matte_overrides = {o.get("target") for o in d.root.iter("override") if o.get("property") == "matte"}
        self.matte_users = tuple(el for el in d.root.iter() if isinstance(el.tag, str) and
                                 (el.get("matte") or el.get("id") in matte_overrides or
                                  any(c.get("property") == "matte" for c in el)))
        for el in d.root.iter():
            if isinstance(el.tag, str) and el.get("matte") and not parse_bool(el.get("matteVisible", "false")):
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

    def render_frame(self, t: float, frame: int | None = None) -> Buf:
        frame = int(round(t * float(self.doc.fps))) if frame is None else frame
        r360 = self.hooks.get("render360")
        if r360 is not None:
            return r360(self, t, frame)
        self.frame_cache = {}
        # Reusable effect results live one frame: entries this frame's predecessor did not use go.
        self._fx_gen += 1
        self._fx_memo = {k: v for k, v in self._fx_memo.items() if v[0] >= self._fx_gen - 1}
        self.install_working_primaries()
        blending.set_scene_seed(self.doc.seed)
        ctx = Ctx(t=t, comp_t=t, frame=frame)
        comp = self.doc.section("composition")
        out = self._frame_target()
        self._gpu_frame = out.gpu is not None
        out = self.render_children(comp, out, ctx, self.root_matrix, (self.doc.width, self.doc.height), 1.0)
        backdrop = self.hooks.get("backdrop")
        if backdrop is not None:
            b = backdrop(self, t)          # a visible dome without an active camera (nodes/scene3d.py)
            if b is not None:
                out = blending.composite(out, b, "behind")
        if self.doc.reframe == "fit-blur" and self.doc.layout:
            out = self._fit_blur_backdrop(out, ctx, comp)
        for name in ("captions", "finish"):
            hook = self.hooks.get(name)
            if hook:
                if name == "finish":
                    # Finishing is per pixel: spill outside the frame would be graded and then discarded.
                    out = out.crop_to(self.frame_rect)
                out = hook(self, out, ctx)
        return out.crop_to(self.frame_rect).expand_to(self.frame_rect)

    def _frame_target(self) -> Buf:
        """The empty frame the composition draws into: on the GPU when gpucomp is enabled (not for
        documents whose adjustment layers could read what is composited outside the frame)."""
        from . import gpucomp
        if self.frame_rect == (0, 0, self.width, self.height) and gpucomp.enabled():
            gpu_ok = self.cache.get("gpu-frame")
            if gpu_ok is None:
                gpu_ok = self.cache["gpu-frame"] = not any(ln(e) == "adjustment" for e in self.doc.root.iter("{*}adjustment", "adjustment"))
            if gpu_ok:
                return gpucomp.frame(self.frame_rect)
        return Buf.empty(0, 0, self.width, self.height)

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
    def child_order(self, parent, ctx: Ctx | None = None) -> list:
        ctx = ctx or Ctx(0, 0)
        kids = [c for c in self.doc.nodes(parent) if ln(c) != "transition"]
        # The order only changes with animated z: otherwise it is remembered per parent, scope and vars
        # (keyed on the children and their authored z, so edits are seen).
        sig = (tuple(kids), tuple(c.get("z") for c in kids))
        key = ("child-order", parent, ctx.scope, ctx.vars)
        hit = self.cache.get(key)
        if hit is not None and hit[0] == sig:
            return list(hit[1])

        # On object3D and camera, z is a 3D coordinate, not a stacking index.
        def z(c):
            return 0 if ln(c) in ("object3D", "camera") else self.ev.num(c, "z", self.enter_node(c, ctx), 0)
        order = sorted(kids, key=z)  # stable: equal z retains document order
        if all(ln(c) in ("object3D", "camera") or (not self.ev._anims(c, "z") and not str(c.get("z", "")).startswith(("var(", "{")))
               for c in kids):
            self.cache[key] = (sig, order)
        return order

    def render_children(self, parent, dst: Buf, ctx: Ctx, M: np.ndarray, box: tuple[float, float],
                        fold_opacity: float, layout: dict | None = None) -> Buf:
        layout = layout if layout is not None else self.layout_positions(parent, ctx, box)
        active_tr = self.active_transitions(parent, ctx)
        done_tr = set()
        hidden_mattes = self.hidden_mattes(ctx)
        order = self.child_order(parent, ctx)
        ds = self.hooks.get("depth_sort")
        if ds is not None and self.ev.bool(parent, "collapse", ctx):
            order = ds(self, parent, order, ctx)   # collapsed 3D group: farthest first
        runs = {}
        collapsed = ds is not None and self.ev.bool(parent, "collapse", ctx)
        if fold_opacity == 1.0 and len(order) > 1 and not collapsed and self._rc_usable(ctx):
            # Consecutive static, normal-blend siblings form one cached raster (see _cached_draw).
            k = self._rc_window(ctx)
            i = 0
            while i < len(order):
                j = i
                while j < len(order) and self._run_member(order[j], ctx, k, layout, active_tr, hidden_mattes):
                    j += 1
                if j - i >= 2:
                    runs[order[i]] = order[i:j]
                i = max(j, i + 1)
        skip = set()
        for child in order:
            if child in skip:
                continue
            run = runs.get(child)
            if run is not None:
                skip.update(run[1:])
                key = ("run", parent, tuple(run), self.scale, tuple(np.round(box, 6)))
                sig = self._run_signature(run, k)
                got = self._composite_cached(dst, key, sig, k, M, lambda rc, A, run=run: rc._render_run(run, ctx, A, box))
                if got is not _NOT_CACHED:
                    dst = got
                    continue
                skip.difference_update(run[1:])
            if child in hidden_mattes or child in self.exclude:
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

    def hidden_mattes(self, ctx: Ctx) -> set:
        if not self.matte_users:
            return set()
        key = ("hidden-mattes", ctx.comp_t, ctx.frame)
        if key not in self.frame_cache:
            from .references import node_contexts
            hidden = {}
            users = set(self.matte_users)
            for user, user_ctx in node_contexts(self, ctx):
                if user not in users:
                    continue
                ref = self.ev.str(user, "matte", user_ctx)
                if ref and not self.ev.bool(user, "matteVisible", user_ctx, False):
                    node, target_ctx = self.ev.reference(ref, user, user_ctx)
                    if node is not None:
                        hidden.setdefault(target_ctx.scope.path, set()).add(node)
            self.frame_cache[key] = hidden
        return self.frame_cache[key].get(ctx.scope.path, set())

    def _passthrough(self, el, dst, ctx, PM, box, layout, fold_opacity) -> Buf | None:
        """Render a non-isolated group straight into dst so its children blend with the real backdrop.
        Returns the updated dst, or None when the group must be isolated."""
        from .nodes.core import _repeat_vars, child_ctx, is_isolated
        if not self.ev.str(el, "effects", ctx) and not self.active(el, ctx):
            return dst       # inactive and without (possibly temporal) effects: nothing to draw either way
        if is_isolated(self, el, self.enter_node(el, ctx)) and not self._isolation_redundant(el, ctx):
            return None
        if not self.active(el, ctx):
            return dst
        ctx = nctx = self.enter_node(el, ctx)
        M = self.node_matrix(el, ctx, PM, box, layout.get(el))
        size = self.node_size(el, nctx, box, layout.get(el))
        cctx = _repeat_vars(el, child_ctx(self, el, nctx))
        return self.render_children(el, dst, cctx, M, size, fold_opacity)

    def _isolation_redundant(self, el, ctx) -> bool:
        """An isolated group renders exactly as if drawn in place when it has nothing that isolation
        affects: normal blend, full opacity, no effects, masks, matte, clip or 3D, and nothing inside
        (through non-isolated descendants) that blends with the backdrop other than by source-over, no
        adjustment layers and no transitions. Source-over is associative, so the extra buffer and its
        composite are skipped."""
        key = ("iso-redundant", el, ctx.scope, ctx.vars)
        hit = self.frame_cache.get(key)
        if hit is not None:
            return hit
        from .nodes.core import child_ctx, is_isolated
        ev = self.ev
        nctx = self.enter_node(el, ctx)

        def plain(g, gctx) -> bool:
            if (ev.str(g, "effects", gctx) or ev.str(g, "blend", gctx, "normal") != "normal"
                    or ev.str(g, "matte", gctx) is not None or any(ln(c) == "mask" for c in g)
                    or ev.bool(g, "clip", gctx) or ev.bool(g, "threeD", gctx) or ev.num(g, "opacity", gctx, 1.0) < 1.0
                    or self.transitions.get(g)):
                return False
            cctx = child_ctx(self, g, gctx)
            for c in self.doc.nodes(g):
                tag = ln(c)
                if tag in ("adjustment", "transition"):
                    return False
                if ev.str(c, "blend", self.enter_node(c, cctx), "normal") != "normal":
                    return False
                if tag in ("group", "sequence"):
                    c_ctx = self.enter_node(c, cctx)
                    if not is_isolated(self, c, c_ctx) and not plain(c, c_ctx):
                        return False
            return True

        hit = self.frame_cache[key] = ln(el) == "group" and not self.is_threed(el) and plain(el, nctx)
        return hit

    # ------------------------------------------------------------ node
    def active(self, el, ctx: Ctx, force: bool = False) -> bool:
        ctx = self.enter_node(el, ctx)
        if not self.ev.bool(el, "visible", ctx, True):
            return False
        if not force:
            s, e = ctx.node_start, ctx.node_end
            if ctx.t < s - 1e-9 or (e is not None and ctx.t >= e - 1e-9):
                return False
        return self.ev.condition(el, ctx)

    def node_ctx(self, el, ctx: Ctx) -> Ctx:
        return self.ev.node_ctx(el, ctx)

    def enter_node(self, el, ctx: Ctx) -> Ctx:
        """Enter a scheduled node once; its active window stays on the parent clock."""
        return self.ev.enter_node(el, ctx)

    def local_matrix(self, el, ctx: Ctx, box, size, layout_pos=None) -> np.ndarray:
        ev = self.ev
        bw, bh = box
        w, h = size
        x = ev.length(el, "x", ctx, bw)
        y = ev.length(el, "y", ctx, bh)
        if layout_pos is not None:
            x += layout_pos[0]
            y += layout_pos[1]
        ax = ev.length(el, "anchorX", ctx, bw)       # % anchors refer to the parent box (CONVENTIONS 1.2)
        ay = ev.length(el, "anchorY", ctx, bh)
        sx = ev.num(el, "scaleX", ctx, 1.0)
        sy = ev.num(el, "scaleY", ctx, 1.0)
        if layout_pos is not None and len(layout_pos) > 2 and ln(el) not in ("shape", "group", "sequence"):
            tw, th = layout_pos[2:]
            if tw is not None and w * abs(sx) > 0:
                sx *= tw / (w * abs(sx))
            if th is not None and h * abs(sy) > 0:
                sy *= th / (h * abs(sy))
        rot = ev.num(el, "rotation", ctx, 0.0)
        orient = ev.motion_path_angle(el, ctx)
        if orient is not None:
            rot += orient
        kx, ky = ev.num(el, "skewX", ctx, 0.0), ev.num(el, "skewY", ctx, 0.0)
        phys = self.hooks.get("physics")
        pose = phys(self, el, ctx) if phys is not None else None
        if pose is not None:  # simulated rigid body: parent-space x/y (px) and rotation (deg)
            x, y, rot = pose
        if pose is None and (ev.str(el, "alignX", ctx) or ev.str(el, "alignY", ctx)):
            x, y, sx, sy = self._align(el, ctx, box, (w, h), x, y, ax, ay, sx, sy, rot, kx, ky)
        L = translate(x, y) @ rotate(rot)
        if kx or ky:
            L = L @ skew(kx, ky)
        return L @ scale(sx, sy) @ translate(-ax, -ay)

    def _align(self, el, ctx, box, size, x, y, ax, ay, sx, sy, rot=0.0, kx=0.0, ky=0.0):
        """Place x/y (and scale for stretch) so the node's transformed box aligns within alignTo."""
        ev = self.ev
        target = self.ev.str(el, "alignTo", ctx, "parent")
        bx0, by0, bw, bh = 0.0, 0.0, box[0], box[1]
        if target in ("frame", "safe-area"):
            bw, bh = self.doc.width, self.doc.height
            if target == "safe-area":
                l, t_, r, b = self.safe_insets()
                bx0, by0, bw, bh = l * bw, t_ * bh, bw * (1 - l - r), bh * (1 - t_ - b)
        m = ev.length(el, "margin", ctx, min(bw, bh))
        w, h = size
        ax_mode, ay_mode = self.ev.str(el, "alignX", ctx), self.ev.str(el, "alignY", ctx)
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

    def node_size(self, el, ctx: Ctx, box, layout_pos=None) -> tuple[float, float]:
        w, h = self._natural_node_size(el, ctx, box)
        if layout_pos is not None and len(layout_pos) > 2 and ln(el) in ("shape", "group", "sequence"):
            tw, th = layout_pos[2:]
            if tw is not None:
                w = tw / max(1e-12, abs(self.ev.num(el, "scaleX", ctx, 1)))
            if th is not None:
                h = th / max(1e-12, abs(self.ev.num(el, "scaleY", ctx, 1)))
        return w, h

    def _natural_node_size(self, el, ctx: Ctx, box) -> tuple[float, float]:
        """The node's own box (used for anchors, % children, layout and alignment)."""
        tag = ln(el)
        ev = self.ev
        if tag in ("shape", "group", "sequence"):
            w = ev.length(el, "width", ctx, box[0], box[0] if tag != "shape" else 0.0)
            h = ev.length(el, "height", ctx, box[1], box[1] if tag != "shape" else 0.0)
            return w, h
        if tag == "layer":
            asset = self.layer_asset(el, ctx)
            natural = self.asset_size(asset, ctx) if asset is not None else (0.0, 0.0)
            return self._media_box(el, ctx, box, natural)
        if tag == "instance":
            sym = self.doc.ids.get(ev.str(el, "symbol", ctx))
            if sym is not None and sym.get("width"):
                natural = (ev.num(sym, "width", ctx, box[0]), ev.num(sym, "height", ctx, box[1]))
            else:
                natural = box
            return self._media_box(el, ctx, box, natural)
        if tag == "particleEmitter":
            return ev.num(el, "emitterWidth", ctx, 0.0), ev.num(el, "emitterHeight", ctx, 0.0)
        return box

    def _media_box(self, el, ctx, parent, natural):
        ev = self.ev
        bw, bh = ev.get(el, "boxWidth", ctx), ev.get(el, "boxHeight", ctx)
        if bw is None and bh is None:
            return parent if ev.str(el, "fit", ctx, "none") != "none" else natural
        aw, ah = natural
        w = ev.length(el, "boxWidth", ctx, parent[0]) if bw is not None else None
        h = ev.length(el, "boxHeight", ctx, parent[1]) if bh is not None else None
        if w is None:
            w = h * aw / ah if ah else parent[0]
        if h is None:
            h = w * ah / aw if aw else parent[1]
        return w, h

    def asset_size(self, asset, ctx) -> tuple[float, float]:
        ctx = ctx or Ctx(0, 0)
        fn = ASSET_SIZES.get(ln(asset))
        if fn:
            return fn(self, asset, ctx)
        return self.ev.num(asset, "width", ctx, 0), self.ev.num(asset, "height", ctx, 0)

    def layer_asset(self, el, ctx: Ctx):
        aid = self.ev.str(el, "asset", ctx)
        return self.doc.ids.get(aid) if aid else None

    def world_matrix(self, el, ctx: Ctx) -> np.ndarray:
        """Frame-space matrix of el's local space (used by @parent, constraints and mattes)."""
        key = ("world", el, ctx)
        hit = self.frame_cache.get(key)
        if hit is not None:
            return hit
        loc = self.node_location(el, ctx)
        M = loc.rc.node_matrix(el, loc.rc.enter_node(el, loc.ctx), loc.matrix, loc.box, loc.layout)
        self.frame_cache[key] = M
        return M

    def base(self) -> "RenderContext":
        """The long-lived root context. Per-render copies (references.locate, buffer_for_asset) carry
        per-frame state such as motion-blur sample buffers; anything cached across frames must hold
        the root instead, or it pins that state for the life of the cache."""
        return self._root if self._root is not None else self

    def node_location(self, el, ctx: Ctx):
        from .references import locate
        return locate(self, el, ctx)

    def node_matrix(self, el, ctx, PM, box, layout_pos) -> np.ndarray:
        nctx = self.node_ctx(el, ctx)
        size = self.node_size(el, nctx, box, layout_pos)
        L = self.local_matrix(el, nctx, box, size, layout_pos)
        parent_id = self.ev.str(el, "parent", nctx)
        if parent_id:
            target, target_ctx = self.ev.reference(parent_id, el, nctx)
            if target is not None and (target is not el or target_ctx.scope != nctx.scope):
                PM = self.world_matrix(target, target_ctx)
        M = PM @ L
        for c in el:
            if ln(c) == "transformConstraint":
                from .constraints import apply_constraint
                M = apply_constraint(self, c, el, M, nctx)
        return M

    def is_threed(self, el) -> bool:
        """2.5D participation: threeD="true", or a child of a collapsed 3D group (camera hook decides)."""
        fn = self.hooks.get("is_threed")
        return fn(self, el) if fn is not None else parse_bool(el.get("threeD"))

    def temporal_effects(self, el, ctx: Ctx) -> list:
        """(effect element, handler) pairs of el's effects that sample other times."""
        ctx = self.enter_node(el, ctx)
        result = []
        for eid in self.ev.str(el, "effects", ctx, "").split():
            e = self.doc.ids.get(eid)
            fn = EFFECTS.get(self.ev.str(e, "type", ctx)) if e is not None else None
            if fn is not None and getattr(fn, "temporal", False):
                result.append((e, fn))
        return result

    def _ghost_active(self, el, ctx: Ctx) -> bool:
        """Past its window, a node with a lookback effect (echo) stays alive while its tail lasts."""
        ctx = self.enter_node(el, ctx)
        if not self.ev.bool(el, "visible", ctx, True):
            return False
        return any(getattr(fn, "ghost_active", lambda *a: False)(self, fx, el, ctx)
                   for fx, fn in self.temporal_effects(el, ctx))

    def render_node(self, el, ctx: Ctx, PM, box, layout_pos=None, force: bool = False) -> Out | None:
        if not self.active(el, ctx, force):
            if self.temporal_effects(el, ctx) and self._ghost_active(el, ctx):
                return self._render_node_once(el, ctx, PM, box, layout_pos, ghost=True)
            return None
        mode = self.motion_blur_mode(el, ctx)
        if self.motion_blur and mode == "off" and self.mb_center is not None:
            # Excluded from the frame's shutter: always drawn at the frame's centre time.
            return self.render_node_at(el, self.mb_center, ctx, effects=True)
        elif not self.motion_blur and mode == "on" and not self._node_mb:
            return self._render_node_blurred(el, ctx, PM, box, layout_pos, force)
        if self._mb_nodes is not None and self.mb_reusable(el, ctx):
            return self._render_node_reused(el, ctx, PM, box, layout_pos)
        return self._render_node_once(el, ctx, PM, box, layout_pos)

    def _render_node_reused(self, el, ctx: Ctx, PM, box, layout_pos) -> Out | None:
        """_render_node_once, reusing the output of an earlier shutter sample of this frame when the node
        provably renders the same pixels at every sample time (see mb_reusable) and is placed identically."""
        key = (ctx.scope, ctx.vars, box, layout_pos)
        entries = self._mb_nodes.setdefault(el, [])
        for k, pm, out in entries:
            if k == key and np.array_equal(pm, PM):
                return None if out is None else Out(self._reuse_copy(out.buf), out.blend, out.opacity)
        out = self._render_node_once(el, ctx, PM, box, layout_pos)
        if out is not None and self._gpu_frame:
            # One GPU copy lent to every sample of the frame: no CPU copy or upload per sample.
            from . import gpucomp
            b = out.buf
            if b.gpu is None:
                tile = gpucomp.shared_upload(b.px)
            else:
                tile = b.gpu
                tile.shared = True
            out = Out(gpucomp.lend(tile, b.x0, b.y0), out.blend, out.opacity)
        kept = None if out is None else Out(self._reuse_copy(out.buf), out.blend, out.opacity)
        entries.append((key, np.array(PM, copy=True), kept))
        return out

    @staticmethod
    def _reuse_copy(b: Buf) -> Buf:
        """A Buf with b's pixels that its user may write into: a CPU copy, or another loan of a GPU tile
        (writing to a loan downloads a private copy)."""
        if b.gpu is not None and b.gpu.shared:
            from . import gpucomp
            return gpucomp.lend(b.gpu, b.x0, b.y0)
        return Buf(b.px.copy(), b.x0, b.y0)

    def mb_reusable(self, el, ctx: Ctx) -> bool:
        """Whether el's rendered output is the same at every time of the shutter interval.

        Conservative: el must sit on the composition clock, and neither its subtree nor anything it
        references may hold time-varying input (animated keys changing in the interval, expressions,
        links, simulations, media, effects, 3D, ...) or depend on another node's placement."""
        if ctx.scope.path or self.mb_interval is None:
            return False
        hit = self._mb_static.get(el)
        if hit is None:
            hit = self._mb_static[el] = self._subtree_static(el, *self.mb_interval)
        return hit

    def _subtree_static(self, el, first: float, last: float) -> bool:
        import re
        from .document import NODE_TAGS
        doc = self.doc
        comp = doc.section("composition")
        chain = [el, *el.iterancestors()]
        if comp not in chain:
            return False     # symbol content runs on its instance's clock
        # Ancestors reach the node only through its placement (matrix, box, layout: the reuse key) and
        # its clock: anything that re-times descendants disqualifies it.
        for a in chain[1:chain.index(comp)]:
            if (a in doc.clock_shift or ln(a) == "sequence" or a.find("timeRemap") is not None
                    or float(a.get("timeOffset", 0)) != 0 or float(a.get("timeScale", 1)) != 1):
                return False
        if el in doc.clock_shift:
            return False
        subtree = set(el.iter())
        todo, seen = [el], set()
        while todo:
            root = todo.pop()
            for d in root.iter():
                if d in seen or not isinstance(d.tag, str):
                    continue
                seen.add(d)
                # A re-timed descendant runs its subtree on a local clock the key times are not on.
                if d in doc.clock_shift or ln(d) == "sequence" or ln(d) == "timeRemap":
                    return False
                if not static_element(self, d, first, last):
                    return False
                if ln(d) in NODE_TAGS and (ln(d) in ("object3D", "camera") or self.is_threed(d)):
                    return False
                for value in d.attrib.values():
                    for token in re.findall(r"[^\s,;()#'\"]+", value):
                        target = doc.ids.get(token)
                        if target is None or target in seen:
                            continue
                        if ln(target) in NODE_TAGS:
                            if target not in subtree:
                                return False     # placed or timed by another node
                        else:
                            todo.append(target)
        return True

    def motion_blur_mode(self, el, ctx: Ctx) -> str:
        """Effective node motionBlur: the nearest non-"inherit" value on el or its ancestors."""
        if not ctx.scope.path and not ctx.scope.overrides:
            # Outside instances, an undriven chain has one answer per set of motionBlur attributes.
            chain = [n for n in [el, *el.iterancestors()] if isinstance(n.tag, str)]
            authored = tuple(n.get("motionBlur") for n in chain)
            hit = self._mb_mode.get(el)
            if hit is None or hit[0] != chain or hit[1] != authored:
                static = not any(self.ev._anims(n, "motionBlur") for n in chain)
                hit = self._mb_mode[el] = (chain, authored, static,
                                           self._motion_blur_mode(el, ctx) if static else None)
            if hit[2]:
                return hit[3]
        return self._motion_blur_mode(el, ctx)

    def _motion_blur_mode(self, el, ctx: Ctx) -> str:
        for n in [el, *el.iterancestors()]:
            if not isinstance(n.tag, str):
                continue
            _, nctx = self.ev.reference(n.get("id", ""), n, ctx)
            v = self.ev.str(n, "motionBlur", nctx, "inherit")
            if v in ("on", "off"):
                return v
        return "inherit"

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
                o = self.render_node_at(el, ctx.comp_t + dt, ctx, effects=True)
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

    def render_node_at(self, el, t: float, ctx: Ctx, effects: bool = False, *, local_time=None) -> Out | None:
        """el rendered at composition time t in its own place (for temporal effects such as echo);
        its effect stack is skipped unless effects=True, so an effect can't recurse into itself."""
        sample = replace(ctx, comp_t=t, frame=round(t * float(self.doc.fps)))
        loc = self.node_location(el, sample)
        c2 = loc.rc.enter_node(el, loc.ctx)
        if local_time is not None:
            c2 = replace(c2, t=local_time)
        skip = not effects and el not in self._skip_effects
        if skip:
            self._skip_effects.add(el)
        try:
            return loc.rc._render_node_once(el, c2, loc.matrix, loc.box, loc.layout)
        finally:
            if skip:
                self._skip_effects.discard(el)

    def _render_node_once(self, el, ctx: Ctx, PM, box, layout_pos=None, ghost: bool = False) -> Out | None:
        tag = ln(el)
        ctx = nctx = self.enter_node(el, ctx)
        opacity = self.ev.num(el, "opacity", nctx, 1.0)
        temporal = bool(self.temporal_effects(el, ctx))
        if opacity <= 1e-4 and not temporal:
            return None
        handler = NODES.get(tag)
        if handler is None:
            warn_once("node", tag)
            return None
        M = self.node_matrix(el, ctx, PM, box, layout_pos)
        if abs(M[0, 0] * M[1, 1] - M[0, 1] * M[1, 0]) < 1e-12:
            return None  # collapsed to zero area (e.g. scale 0): nothing visible, and cairo can't invert it
        size = self.node_size(el, nctx, box, layout_pos)
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
            return None if buf is None or buf.is_null else Out(buf, self.ev.str(el, "blend", nctx, "normal"), 1.0)
        if abs(M[2, 0]) + abs(M[2, 1]) > 1e-12:
            buf = self._render_projective(el, handler, nctx, M, size, PM, box)
        else:
            cached = self._render_cached(el, handler, nctx, M, size, PM, box)
            if cached is not _NOT_CACHED:
                buf = cached
            else:
                buf = handler(self, el, nctx, M, size)
                if isinstance(buf, Out):
                    return buf
                if buf is not None:
                    buf = self.finish_node(el, buf, nctx, M, size, PM, box)
        if buf is None:
            return None
        return Out(buf, self.ev.str(el, "blend", nctx, "normal"), opacity)

    # ------------------------------------------------------------ raster cache
    def _rc_usable(self, ctx) -> bool:
        return (RASTER_CACHE and not self._rc_inside and not self._flat_depth and self.scene_matrix is None
                and self.scene_context is None and not ctx.scope.path and not ctx.vars
                and not self.cache.get("pass360"))

    def _rc_window(self, ctx) -> int:
        tc = self.mb_center if self.mb_center is not None else ctx.comp_t
        return int(math.floor(tc / RASTER_WINDOW))

    def _rc_static(self, el, k: int, own_placement: bool):
        """(content provably static over window k, referenced elements), memoised."""
        base = self.base()
        hit = base._rc_ok.get((el, k, own_placement))
        if hit is None:
            refs = []
            hit = base._rc_ok[(el, k, own_placement)] = (
                self._content_static(el, max(0.0, k * RASTER_WINDOW - 0.1), (k + 1) * RASTER_WINDOW + 0.1, refs,
                                     own_placement), refs)
            if len(base._rc_ok) > 50000:
                base._rc_ok.clear()
        return hit

    def _render_cached(self, el, handler, ctx, M, size, PM, box):
        """A node whose content is provably unchanged over a window of RASTER_WINDOW seconds renders
        once per window and is reused (see _cached_draw). Its own placement and opacity may animate;
        masks and pointwise effects are part of the raster. _NOT_CACHED: render normally.
        SCENERENDER_RASTER_CACHE=0 disables the cache."""
        if ln(el) not in RASTER_TAGS or not self._rc_usable(ctx) or el.get("matte") or self.is_threed(el):
            return _NOT_CACHED
        k = self._rc_window(ctx)
        ok, refs = self._rc_static(el, k, True)
        if not ok:
            return _NOT_CACHED
        key = ("node", el, self.scale, tuple(np.round(size, 6)))
        # Only where transparent source pixels leave the backdrop unchanged may a raster be cropped.
        crop = el.get("blend", "normal") == "normal" and not self.ev._anims(el, "blend")
        return self._cached_draw(key, self._raster_signature(el, refs), k, M,
                                 lambda rc, A: rc._render_content(el, handler, ctx, A, size, PM, box), crop)

    def _render_content(self, el, handler, ctx, A, size, PM, box):
        buf = handler(self, el, ctx, A, size)
        if isinstance(buf, Out):
            return _NOT_CACHED
        return None if buf is None else self.finish_node(el, buf, ctx, A, size, PM, box)

    def _run_member(self, child, ctx, k: int, layout, active_tr, hidden) -> bool:
        """Can child join a cached run of siblings (static in the parent's space, normal blend)?"""
        if (ln(child) not in RASTER_TAGS or child in hidden or child in self.exclude or layout.get(child) is not None
                or child.get("matte") or child.get("blend", "normal") != "normal" or self.is_threed(child)
                or any(child in tr[1:3] for tr in active_tr)):
            return False
        return self._rc_static(child, k, False)[0]

    def _render_run(self, run, ctx, A, box):
        dst = Buf.null()
        for child in run:
            out = self.render_node(child, ctx, A, box, None)
            if out is not None:
                dst = blending.composite(dst, out.buf, out.blend, out.opacity)
        return None if dst.is_null else dst

    def _cached_draw(self, key, sig, k: int, M, draw, crop: bool = True, share: bool = False):
        """draw(rc, A) renders content whose placement in the frame is M. The first render in a window is
        exact (A = M) and is reused while M is unchanged; a changed M resamples a raster drawn at 15%
        above the current scale over a 25% margin around the view (redrawn when the view leaves it or
        zooms past it)."""
        cache = self.base()._rcache
        if cache.get("_k") != k:
            # A new window: rasters not proven static through the previous one are stale.
            for kk in [kk for kk, e in cache.items() if kk != "_k" and e["k"] < k - 1]:
                del cache[kk]
            cache["_k"] = k
        entry = _live_entry(cache, key, sig, k)
        if entry is None:
            buf = self._draw_with(draw, M, None)
            if buf is _NOT_CACHED:
                return _NOT_CACHED
            if crop and buf is not None and not buf.is_null:
                buf = _crop_alpha(buf)      # transparent pixels composite to nothing: copies stay small
            cache[key] = {"exact": True, "M": np.array(M, copy=True), "buf": buf, "sig": sig, "k": k}
            return None if buf is None else (buf if share else Buf(buf.px.copy(), buf.x0, buf.y0))
        if entry["exact"] and np.array_equal(entry["M"], M):
            b = entry["buf"]
            if b is not None and self._gpu_frame and not share:
                # Lent from the GPU copy: composited onto the GPU frame without a download (a caller
                # writing into it downloads a private copy).
                from . import gpucomp
                return gpucomp.lend(_entry_tile(entry), b.x0, b.y0)
            # share: the caller only reads it (composites it at once); otherwise callers may write into it
            return None if b is None else (b if share else Buf(b.px.copy(), b.x0, b.y0))
        s_now = math.sqrt(abs(M[0, 0] * M[1, 1] - M[0, 1] * M[1, 0]))
        view = self._local_view(M, 64)
        if view is None:
            return _NOT_CACHED
        if (not entry["exact"] and entry["s"] * 0.45 <= s_now <= entry["s"]
                and _contains(entry["region"], view)):
            return self._warp_raster(entry, M)
        s_r = s_now * 1.15
        region = self._local_view(M, 0.25 * max(self.width, self.height) + 64)
        rw, rh = math.ceil((region[2] - region[0]) * s_r), math.ceil((region[3] - region[1]) * s_r)
        if rw * rh > RASTER_MAX_PIXELS:
            return _NOT_CACHED
        A = np.array([[s_r, 0, -region[0] * s_r], [0, s_r, -region[1] * s_r], [0, 0, 1]], np.float64)
        buf = self._draw_with(draw, A, (rw, rh))
        if buf is _NOT_CACHED:
            return _NOT_CACHED
        if crop and buf is not None:
            buf = _crop_alpha(buf)
        entry = cache[key] = {"exact": False, "A": A, "s": s_r, "region": region, "buf": buf, "sig": sig, "k": k}
        return self._warp_raster(entry, M)

    def _draw_with(self, draw, A, dims):
        """draw(rc, A) into the frame (dims None) or into a dims-sized raster through a sized copy."""
        from copy import copy
        rc = self
        if dims is not None:
            rc = copy(self)
            rc._root = self.base()
            rc.width, rc.height = dims
            rc.frame_rect = (0, 0, dims[0], dims[1])
            rc.frame_cache, rc._mb_nodes, rc._fx_memo = {}, None, {}
            rc._gpu_frame = False
        rc._rc_inside += 1
        try:
            return draw(rc, A)
        finally:
            rc._rc_inside -= 1

    def _local_view(self, M, margin: float):
        """Node-local bounding box of the frame rect grown by margin, through the inverse of M."""
        try:
            Mi = np.linalg.inv(M)
        except np.linalg.LinAlgError:
            return None
        x0, y0, x1, y1 = self.frame_rect
        pts = np.array([[x0 - margin, y0 - margin, 1], [x1 + margin, y0 - margin, 1],
                        [x0 - margin, y1 + margin, 1], [x1 + margin, y1 + margin, 1]], np.float64) @ Mi.T
        return (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))

    def _warp_geometry(self, src: Buf, A, M):
        """(frame rect the warped raster covers, inverse map frame -> raster tile pixels) or None."""
        H = M @ np.linalg.inv(A)
        corners = np.array([[src.x0, src.y0, 1], [src.x0 + src.w, src.y0, 1], [src.x0, src.y0 + src.h, 1],
                            [src.x0 + src.w, src.y0 + src.h, 1]], np.float64) @ H.T
        fr = (self.frame_rect[0] - 64, self.frame_rect[1] - 64, self.frame_rect[2] + 64, self.frame_rect[3] + 64)
        rect = intersect((int(math.floor(corners[:, 0].min())) - 1, int(math.floor(corners[:, 1].min())) - 1,
                          int(math.ceil(corners[:, 0].max())) + 1, int(math.ceil(corners[:, 1].max())) + 1), fr)
        if rect is None:
            return None
        # sample coordinates relative to the source tile's origin
        hi = np.array([[1, 0, -src.x0], [0, 1, -src.y0], [0, 0, 1]], np.float64) @ np.linalg.inv(H)
        return rect, hi[:2]

    @staticmethod
    def _moved(entry, M) -> float:
        """Largest displacement (frame pixels) of the raster's corners between the placement of the
        entry's last warp and M; inf when there is none."""
        last = entry.get("last")
        if last is None or entry["buf"] is None:
            return math.inf
        src = entry["buf"]
        c = np.array([[src.x0, src.y0, 1], [src.x0 + src.w, src.y0, 1], [src.x0, src.y0 + src.h, 1],
                      [src.x0 + src.w, src.y0 + src.h, 1]], np.float64)
        Ai = np.linalg.inv(entry["A"])
        d = c @ (M @ Ai).T - c @ (last[0] @ Ai).T
        return float(np.hypot(d[:, 0], d[:, 1]).max())

    def _warp_raster(self, entry, M) -> Buf | None:
        from .raster import warp_projective
        src = entry["buf"]
        if src is None:
            return None
        if self._moved(entry, M) < WARP_REUSE_PX:
            b = entry["last"][1]
            return None if b is None else Buf(b.px.copy(), b.x0, b.y0)
        from . import kernels
        if self._gpu_frame:
            g = self._warp_geometry(src, entry["A"], M)
            if g is None:
                return None
            from . import gpucomp
            return gpucomp.warp(_entry_tile(entry), g[1], g[0])
        if not kernels.enabled():
            return warp_projective(src, M @ np.linalg.inv(entry["A"]), self.frame_rect)
        g = self._warp_geometry(src, entry["A"], M)
        if g is None:
            entry["last"] = (np.array(M, copy=True), None)
            return None
        rect, hi = g
        out = Buf(kernels.warp_affine(src.px, hi, rect), rect[0], rect[1])
        entry["last"] = (np.array(M, copy=True), out)
        return Buf(out.px.copy(), out.x0, out.y0)

    def _composite_cached(self, dst: Buf, key, sig, k: int, M, draw):
        """dst with a cached run composited over it (source-over), or _NOT_CACHED. Like _cached_draw,
        but the raster is never copied: an exact entry is composited as is, a resampled one is warped
        straight into dst in one pass."""
        from . import kernels
        entry = _live_entry(self.base()._rcache, key, sig, k)
        gpu = dst.gpu is not None
        moving = entry is not None and not entry["exact"] and (kernels.enabled() or gpu) and entry["buf"] is not None
        if moving:
            s_now = math.sqrt(abs(M[0, 0] * M[1, 1] - M[0, 1] * M[1, 0]))
            view = self._local_view(M, 64)
            if view is not None and entry["s"] * 0.45 <= s_now <= entry["s"] and _contains(entry["region"], view):
                if gpu:
                    # Resampled straight into the GPU frame: cheaper than reusing a nearby warp.
                    g = self._warp_geometry(entry["buf"], entry["A"], M)
                    if g is not None:
                        from . import gpucomp
                        gpucomp.warp_over(dst, _entry_tile(entry), g[1], g[0])
                    return dst
                if self._moved(entry, M) < WARP_REUSE_PX:
                    b = entry["last"][1]       # a shutter sample within WARP_REUSE_PX of the last warp
                    return dst if b is None else blending.composite(dst, b, "normal", 1.0)
                g = self._warp_geometry(entry["buf"], entry["A"], M)
                if g is None:
                    entry["last"] = (np.array(M, copy=True), None)
                    return dst
                rect, hi = g
                if WARP_REUSE_PX > 0:
                    # Keep this warp: the frame's other shutter samples usually land within a pixel
                    # fraction of it (slow pans, Ken Burns moves).
                    w = Buf(kernels.warp_affine(entry["buf"].px, hi, rect), rect[0], rect[1])
                    entry["last"] = (np.array(M, copy=True), w)
                    return blending.composite(dst, w, "normal", 1.0)
                dst = dst.expand_to(rect)
                d = dst.px[rect[1] - dst.y0:rect[3] - dst.y0, rect[0] - dst.x0:rect[2] - dst.x0]
                kernels.warp_over(d, entry["buf"].px, hi, (rect[0], rect[1]))
                return dst
        buf = self._cached_draw(key, sig, k, M, draw, share=True)
        if buf is _NOT_CACHED:
            return _NOT_CACHED
        if buf is None:
            return dst
        entry = self.base()._rcache.get(key)
        if gpu and entry is not None and entry["exact"] and entry["buf"] is buf:
            from . import gpucomp
            return blending.composite(dst, gpucomp.lend(_entry_tile(entry), buf.x0, buf.y0), "normal", 1.0)
        if (kernels.enabled() and not dst.is_null and entry is not None and entry["exact"]
                and entry["buf"] is buf and buf.px.dtype == np.float32):
            # A cached run is often sparse (lines, labels): composite only its occupied tiles.
            if "tiles" not in entry:
                entry["tiles"] = kernels.occupied_tiles(buf.px)
            if entry["tiles"] is not None:
                dst = dst.expand_to(buf.rect)
                d = dst.px[buf.y0 - dst.y0:buf.y0 - dst.y0 + buf.h, buf.x0 - dst.x0:buf.x0 - dst.x0 + buf.w]
                kernels.over_tiles(d, buf.px, entry["tiles"])
                return dst
        return blending.composite(dst, buf, "normal", 1.0)

    def _signature_memo(self) -> dict:
        """Signatures computed this frame: the shutter samples of a frame cannot see an edit in between."""
        if self.mb_center is None:
            return self.frame_cache
        base = self.base()
        frame_memo = base.__dict__.get("_sig_frame")
        if frame_memo is None or frame_memo[0] != self.mb_center:
            frame_memo = base.__dict__["_sig_frame"] = (self.mb_center, {})
        return frame_memo[1]

    def _run_signature(self, run, k: int) -> tuple:
        """The signatures of a cached run's members, formed once per frame."""
        memo = self._signature_memo()
        key = ("rc-run-sig", tuple(run), k)
        hit = memo.get(key)
        if hit is None:
            hit = memo[key] = tuple(self._raster_signature(c, self._rc_static(c, k, False)[1]) for c in run)
        return hit

    def _raster_signature(self, el, refs) -> tuple:
        """What a raster-cache entry was drawn from: the node's XML, the elements it references and the
        files they name (size, mtime), so edits between renders are never served stale."""
        key = ("rc-sig", el)
        memo = self._signature_memo()
        hit = memo.get(key)
        if hit is None:
            import os
            from lxml import etree
            files = []
            for r in refs:
                for attr in ("src", "proxy"):
                    v = r.get(attr)
                    if v and not v.startswith("data:"):
                        try:
                            path = self.doc.resolve_path(v)
                            st = os.stat(path)
                            files.append((path, st.st_size, st.st_mtime_ns))
                        except (OSError, TypeError, ValueError):
                            files.append((v, None, None))
            hit = memo[key] = (etree.tostring(el), tuple(etree.tostring(r) for r in refs), tuple(files))
        return hit

    def _content_static(self, el, first: float, last: float, refs: list | None = None,
                        own_placement: bool = True) -> bool:
        """_subtree_static for a raster-cache entry: el's own placement and opacity may animate (they
        are applied outside the raster); effects are allowed when static and pointwise. Referenced
        non-node elements (assets, effects, paints, ...) are appended to refs."""
        import re
        from .document import NODE_TAGS
        doc = self.doc
        comp = doc.section("composition")
        chain = [el, *el.iterancestors()]
        if comp not in chain:
            return False
        for a in chain[1:chain.index(comp)]:
            if (a in doc.clock_shift or ln(a) == "sequence" or a.find("timeRemap") is not None
                    or float(a.get("timeOffset", 0)) != 0 or float(a.get("timeScale", 1)) != 1):
                return False
        if el in doc.clock_shift:
            return False
        subtree = set(el.iter())
        todo, seen = [el], set()
        while todo:
            root = todo.pop()
            for d in root.iter():
                if d in seen or not isinstance(d.tag, str):
                    continue
                seen.add(d)
                tag = ln(d)
                if d in doc.clock_shift or tag in ("sequence", "timeRemap"):
                    return False
                if tag == "effect":
                    if not self._pointwise_effect(d, first, last):
                        return False
                    if refs is not None and d not in subtree:
                        refs.append(d)
                    continue
                if own_placement and tag == "animate" and d.getparent() is el and d.get("property") in SELF_PLACEMENT:
                    continue
                if not static_element(self, d, first, last):
                    return False
                if tag in NODE_TAGS and (tag in ("object3D", "camera", "instance") or self.is_threed(d)):
                    return False
                for value in d.attrib.values():
                    for token in re.findall(r"[^\s,;()#'\"]+", value):
                        target = doc.ids.get(token)
                        if target is None or target in seen:
                            continue
                        if ln(target) in NODE_TAGS:
                            if target not in subtree:
                                return False
                        else:
                            todo.append(target)
                            if refs is not None and target not in subtree and ln(target) != "effect":
                                refs.append(target)
        return True

    def _pointwise_effect(self, e, first: float, last: float) -> bool:
        """A static effect whose every output pixel depends only on the same input pixel (so it commutes
        with resampling): the display-referred colour operations, and GLSL that samples its input only
        at uv and reads no time, position or resolution."""
        if e.get("condition") or e.get("source"):
            return False
        for d in e.iter():
            if d is not e and isinstance(d.tag, str) and not static_element(self, d, first, last):
                return False
        typ = e.get("type")
        if typ in ("color-grade", "lift-gamma-gain"):
            return True
        if typ != "shader":
            return False
        hit = self.base().cache.get(("pointwise-shader", e.get("src")))
        if hit is None:
            hit = self.base().cache[("pointwise-shader", e.get("src"))] = _pointwise_glsl(self, e)
        return hit

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
        fx = self.ev.str(el, "effects", ctx)
        if fx and ln(el) != "adjustment" and el not in self._skip_effects:
            buf = self.apply_effects(fx.split(), buf, ctx, el)
        if self.ev.str(el, "matte", ctx):
            from .masks import apply_matte
            buf = apply_matte(self, el, buf, ctx)
        return buf

    def apply_effects(self, ids: list[str], buf: Buf, ctx, node=None) -> Buf:
        from . import kernels
        from .effects import Params
        from .effects.color import DISPLAY_OPS
        fuse = kernels.enabled()
        pending = []     # consecutive display-referred effects: one kernel pass, one sRGB round trip
        for eid in ids:
            e = self.doc.ids.get(eid)
            if e is None:
                warn_once("effect", eid, "effect id not found")
                continue
            if not self.ev.bool(e, "enabled", ctx, True):
                continue
            typ = self.ev.str(e, "type", ctx)
            fn = EFFECTS.get(typ)
            if fn is None:
                warn_once("effect", typ)
                continue
            mix = self.ev.num(e, "mix", ctx, 1.0)
            if mix <= 0:
                continue
            if fuse and mix >= 1 and typ in DISPLAY_OPS:
                pending.append((e, DISPLAY_OPS[typ](Params(self, e, ctx))))
                continue
            if pending:
                buf, pending = self._display_run(pending, buf, ctx, node), []
            res = self._reuse_effect(node, (e,), typ, buf, ctx, lambda: fn(self, e, buf, ctx, node))
            if res is None:
                continue
            if mix < 1:
                r = union(buf.rect, res.rect)
                a, b = buf.region(r), res.region(r)
                res = Buf(a + (b - a) * mix, r[0], r[1])
            buf = res
        if pending:
            buf = self._display_run(pending, buf, ctx, node)
        return buf

    def _display_run(self, run, buf: Buf, ctx, node) -> Buf:
        from .effects.color import display_chain
        return self._reuse_effect(node, tuple(e for e, _ in run), "lift-gamma-gain", buf, ctx,
                                  lambda: display_chain(self, buf, [op for _, op in run]))

    def _reuse_effect(self, node, effects: tuple, typ: str, buf: Buf, ctx, compute):
        """compute(), or the previous result when these effects already ran on identical input.

        Only for effect types that are functions of their input pixels and parameters alone, with
        every parameter (and anything it references, such as lights) constant over the timeline and
        no instance scope: animation "on twos" and held shots feed identical tiles frame after frame."""
        if node is None or ctx.scope.path or typ not in PURE_EFFECTS:
            return compute()
        # The effects and everything they reference, as authored now (documents may be edited
        # between frames): part of the key, and what the constant-parameter proof holds for.
        signature = tuple(self._effect_signature(e) for e in effects)
        if not all(self._effect_constant(e, sig) for e, sig in zip(effects, signature)):
            return compute()
        key = (node, effects, signature)
        hit = self._fx_memo.get(key)
        if (hit is not None and hit[1].x0 == buf.x0 and hit[1].y0 == buf.y0
                and hit[1].px.shape == buf.px.shape and np.array_equal(hit[1].px, buf.px)):
            self._fx_memo[key] = (self._fx_gen,) + hit[1:]
            out = hit[2]
            return None if out is None else Buf(out.px.copy(), out.x0, out.y0)
        res = compute()
        self._fx_memo[key] = (self._fx_gen, buf.copy(), None if res is None else res.copy())
        return res

    def _effect_constant(self, e, signature) -> bool:
        """The effect's parameters, and the elements they reference, hold one value at every time."""
        key = (e, signature)
        hit = self._fx_const.get(key)
        if hit is None:
            hit = self._fx_const[key] = self._references_constant(e)
        return hit

    def _effect_signature(self, e) -> bytes:
        """The serialized effect and the elements it references (see _references)."""
        from lxml import etree
        return b"".join(etree.tostring(d) for d in self._references(e))

    def _references(self, e) -> list:
        """e and the elements its attributes (and its children's) reference by ID, transitively."""
        import re
        out, todo, seen = [], [e], set()
        while todo:
            root = todo.pop()
            if root in seen:
                continue
            out.append(root)
            for d in root.iter():
                if not isinstance(d.tag, str):
                    continue
                seen.add(d)
                for value in d.attrib.values():
                    for token in re.findall(r"[^\s,;()#'\"]+", value):
                        target = self.doc.ids.get(token)
                        if target is not None and target not in seen:
                            todo.append(target)
        return out

    def _references_constant(self, e) -> bool:
        from .document import NODE_TAGS
        for root in self._references(e):
            for d in root.iter():
                if not isinstance(d.tag, str):
                    continue
                if ln(d) in NODE_TAGS or d.get("condition"):
                    return False     # another node's pixels or placement, or a switch
                if d is not e and not static_element(self, d, -math.inf, math.inf):
                    return False
        return True

    def apply_adjustment(self, el, dst: Buf, ctx, PM, box, fold_opacity) -> Buf:
        if not self.active(el, ctx):
            return dst
        ctx = nctx = self.enter_node(el, ctx)
        opacity = self.ev.num(el, "opacity", nctx, 1.0) * fold_opacity
        if opacity <= 0:
            return dst
        region = dst.expand_to(self.frame_rect) if dst.rect != self.frame_rect else dst
        from . import kernels
        masks = [m for m in el if ln(m) == "mask"]
        # The fused mix below only reads the backdrop; the NumPy blend updates it in place.
        fused = (kernels.enabled() and not masks and not self.ev.str(el, "matte", nctx)
                 and self.ev.str(el, "blend", nctx, "normal") == "normal")
        before = region if fused else region.copy()
        after = self.apply_effects((self.ev.str(el, "effects", nctx) or "").split(), region.copy(), nctx, el)
        after = after.crop_to(before.rect).expand_to(before.rect)
        if fused:
            return Buf(kernels.mix(before.px, after.px, opacity), before.x0, before.y0)
        M = self.node_matrix(el, ctx, PM, box, None)
        size = self.node_size(el, nctx, box)
        cov = np.ones(before.px.shape[:2], np.float32) * opacity
        if masks:
            from .masks import mask_coverage
            cov *= mask_coverage(self, masks, before.rect, nctx, M, size)
        if self.ev.str(el, "matte", nctx):
            from .masks import matte_coverage
            cov *= matte_coverage(self, el, before.rect, nctx)
        mode = self.ev.str(el, "blend", nctx, "normal")
        if mode == "normal":
            before.px += (after.px - before.px) * cov[..., None]
            return before
        mixed = blending.composite(before.copy(), Buf(after.px * cov[..., None], before.x0, before.y0), mode, 1.0)
        return mixed

    # ------------------------------------------------------------ layout
    def layout_positions(self, parent, ctx: Ctx, box) -> dict:
        mode = self.ev.str(parent, "layout", ctx, "none") if ln(parent) in ("group", "sequence") else "none"
        if mode == "none":
            return {}
        from .layout import flex_layout
        return flex_layout(self, parent, ctx, box, mode)

    # ------------------------------------------------------------ transitions
    def transition_value(self, tr, prop, ctx, default=None):
        from .scheduling import transition_value
        return transition_value(self.ev, tr, prop, ctx, default)

    def transitions_for(self, parent, ctx):
        from .scheduling import transitions_for
        return transitions_for(self.ev, parent, ctx)

    def transition_window(self, tr, ctx=None) -> tuple[float, float, float] | None:
        from .scheduling import transition_window
        return transition_window(self.ev, tr, ctx or Ctx(0, 0))

    def active_transitions(self, parent, ctx: Ctx) -> list:
        out = []
        for tr in self.transitions_for(parent, ctx):
            w = self.transition_window(tr, ctx)
            if w is None or self.transition_value(tr, "type", ctx) == "cut":
                continue
            s0, s1, _ = w
            if s0 <= ctx.t < s1:
                a = self.doc.ids.get(self.ev.str(tr, "from", ctx))
                b = self.doc.ids.get(self.ev.str(tr, "to", ctx))
                out.append((tr, a, b, s0, s1))
        return out

    def render_transition(self, tr_info, ctx, PM, box, layout) -> Out | None:
        from . import curves
        tr, a, b, s0, s1 = tr_info
        u = (ctx.t - s0) / max(1e-9, s1 - s0)
        p = curves.get(self.ev.str(tr, "curve", ctx, "ease-in-out"))(min(1.0, max(0.0, u)))
        full = lambda n: None if n is None else self._full(self.render_node(n, ctx, PM, box, layout.get(n), force=True))  # noqa: E731
        A, B = full(a), full(b)
        typ = self.transition_value(tr, "type", ctx)
        fn = TRANSITIONS.get(typ)
        if fn is None:
            warn_once("transition", typ, "not supported; falling back to crossfade")
            fn = TRANSITIONS.get("crossfade")
        # The mixed picture blends onto the backdrop with the members' mode (incoming wins past halfway).
        lead = b if (b is not None and (p >= 0.5 or a is None)) else a
        return Out(fn(self, tr, A, B, p, ctx), self.ev.str(lead, "blend", self.enter_node(lead, ctx), "normal")
                   if lead is not None else "normal")

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


# Effects whose output depends on the input tile and their parameters alone (no time, frame or seed).
PURE_EFFECTS = frozenset({"lighting", "drop-shadow", "lift-gamma-gain", "color-grade", "vignette", "exposure"})

# Elements whose presence makes rendering time-dependent beyond their animate keys.
_NOT_CACHED = object()
RASTER_TAGS = frozenset({"group", "shape", "layer"})
SELF_PLACEMENT = frozenset({"x", "y", "scaleX", "scaleY", "rotation", "anchorX", "anchorY", "skewX", "skewY", "opacity"})
RASTER_WINDOW = 1.0                    # seconds a raster-cache entry may live
RASTER_MAX_PIXELS = 16_000_000         # larger resampling rasters are not cached
WARP_REUSE_PX = 0.1                    # a resampled raster is reused while it moves less than this
import os as _os
RASTER_CACHE = _os.environ.get("SCENERENDER_RASTER_CACHE", "1") != "0"


def _crop_alpha(buf: Buf) -> Buf | None:
    """buf cropped to its non-transparent pixels (a raster is warped every sample: keep it tight)."""
    cov = buf.px[..., 3] > 0
    rows = np.flatnonzero(cov.any(1))
    if len(rows) == 0:
        return None
    cols = np.flatnonzero(cov.any(0))
    y0, y1, x0, x1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    return Buf(np.ascontiguousarray(buf.px[y0:y1, x0:x1]), buf.x0 + int(x0), buf.y0 + int(y0))


def _entry_tile(entry: dict):
    """The GPU copy of a raster-cache entry's pixels, uploaded on first use and freed with the entry."""
    t = entry.get("tile")
    if t is None:
        from . import gpucomp
        t = entry["tile"] = gpucomp.shared_upload(entry["buf"].px)
    return t


def _live_entry(cache: dict, key, sig, k: int):
    """The raster-cache entry for key if it is still valid in window k, else None.

    The caller has proven the content static over window k (with 0.1 s margins); an entry proven static
    over window k - 1 therefore stays valid, since two overlapping static intervals are one static
    interval. Rasters so carry over from window to window for as long as their content stays unchanged,
    instead of being redrawn every RASTER_WINDOW seconds."""
    entry = cache.get(key)
    if entry is None or entry["sig"] != sig:      # absent, or the document or a file changed since
        return None
    if entry["k"] == k - 1:
        entry["k"] = k
    return entry if entry["k"] == k else None


def _contains(outer, inner) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


_GLSL_POSITIONAL = ("time", "iTime", "TIME", "localTime", "iFrame", "frame", "FRAMEINDEX", "iTimeDelta", "timeDelta",
                    "gl_FragCoord", "fragCoord", "iResolution", "resolution", "RENDERSIZE", "iOffset", "tileOffset",
                    "frameResolution", "iFrameResolution", "iMouse", "iDate", "DATE", "seed", "texelFetch",
                    "textureSize", "textureLod", "dFdx", "dFdy", "fwidth", "sourceTexture", "iChannel1", "iChannel2",
                    "iChannel3", "IMG_PIXEL", "IMG_NORM_PIXEL", "IMG_THIS_PIXEL", "isf_FragNormCoord", "padding",
                    "mainImage", "centerX", "centerY", "center", "radius", "offsetX", "offsetY")


def _pointwise_glsl(rc, e) -> bool:
    import re
    from .effects.shader import CAT, load_source
    uri = e.get("src")
    if not uri or any(ln(c) == "param" and c.get("name") == "padding" for c in e):
        return False
    loaded = load_source(rc, uri, CAT)
    if loaded is None:
        return False
    if re.search(r"/\*\s*\{", loaded[0]):
        return False          # ISF header: passes, persistent buffers, declared inputs
    code = re.sub(r"//[^\n]*|/\*.*?\*/", " ", loaded[0], flags=re.S)
    idents = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code))
    if (idents & set(_GLSL_POSITIONAL) or any(i.startswith("IMG_") for i in idents) or "#include" in code
            or "import" in idents):
        return False
    # uv may appear only as the coordinate of an input lookup at the pixel itself.
    rest = re.sub(r"texture(?:2D)?\s*\(\s*(?:inputTexture|iChannel0)\s*,\s*uv\s*\)", " ", code)
    rest = re.sub(r"\bin\s+vec2\s+uv\s*;", " ", rest)
    return re.search(r"\buv\b", rest) is None


DYNAMIC_TAGS = frozenset({
    "expression", "link", "motionPath", "particleEmitter", "physics", "rigidBody", "softBody", "deform",
    "shapeModifier", "shake", "textAnimator", "effect", "transition", "instance", "video", "imageSequence",
    "lottie", "audiogram", "generator", "generated", "captions", "transformConstraint", "timeRemap"})


def static_element(rc, el, first: float, last: float) -> bool:
    """Whether el alone contributes nothing time-varying over [first, last] (conservative)."""
    from .document import NODE_TAGS
    tag = ln(el)
    if tag == "expression" and not parse_bool(el.get("enabled"), True):
        return True
    if tag in DYNAMIC_TAGS or el.get("condition"):
        return False
    if tag in NODE_TAGS:
        start, end = rc.doc.window(el)
        if first < start <= last or (end is not None and first < end <= last):
            return False
        if float(el.get("timeOffset", 0)) != 0 or float(el.get("timeScale", 1)) != 1:
            return False
    if tag != "animate":
        return True
    owner = el.getparent()
    keys = rc.ev.keys(owner, el, el.get("property"))
    if not keys:
        return True
    lo, hi = first, last
    base = el.get("timeBase", "composition")
    start, end = rc.doc.window(owner)
    if base in ("local", "normalized"):
        lo, hi = lo - start, hi - start
        if base == "normalized":
            span = end - start if end is not None else 0
            lo, hi = (lo / span, hi / span) if span > 0 else (0, 0)
    if hi <= keys[0].time and el.get("extrapolateBefore", "hold") == "hold":
        return True
    if lo >= keys[-1].time and el.get("extrapolateAfter", "hold") == "hold":
        return True
    # Identical scalar/colour keys are constant unless spatial handles describe a loop between identical positions.
    return (all(k.value == keys[0].value for k in keys)
            and not any(k.el.get("spatialIn") or k.el.get("spatialOut") for k in keys))
