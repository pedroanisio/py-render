"""Locate referenced nodes using the same clocks, layouts and canvases as traversal."""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass

import numpy as np

from .document import ln
from .evaluator import Ctx


@dataclass
class Location:
    rc: object
    ctx: Ctx                 # clock before entering the referenced node
    matrix: object           # XML parent space -> frame
    box: tuple
    layout: tuple | None


class SingularCanvasReference(np.linalg.LinAlgError):
    """A raster reference can use this forward map even without an inverse."""
    def __init__(self, node, ctx, basis, canvas):
        super().__init__('Reference from a collapsed canvas has no inverse transform')
        self.node, self.ctx = node, ctx
        self.basis, self.canvas = basis, canvas


def scene_space(rc, node, ctx):
    """Evaluate in the node's scene units without inverting its display canvas.

    An instance may collapse or disappear while its local simulation continues.
    Keep the instance clock/overrides, but start geometric traversal at the
    symbol boundary. References outside that boundary resolve on demand.
    """
    local = copy(rc)
    root = next((a for a in (node, *node.iterancestors()) if ln(a) == 'symbol'), None)
    local.coordinate_root = (root, rc.ev.context_at(root, ctx, ctx.comp_t)) if root is not None else None
    local.scene_context = local.coordinate_root
    local.scene_matrix = np.eye(3) if root is not None else None
    local.root_matrix = np.eye(3)
    local.frame_cache = {}
    return local


def locate(rc, node, ctx: Ctx) -> Location:
    """Reconstruct a node's parent traversal without rendering its siblings.

    A composition reference starts from composition time. A symbol reference
    follows the caller's instance chain until it reaches that symbol, carrying
    instance overrides, fit transforms and local clocks along the way.
    """
    from .nodes.core import _repeat_vars, child_ctx, fit_matrix
    local = copy(rc)
    local._root = rc.base()
    local.scene_context, local.scene_matrix = None, None
    comp = rc.doc.section("composition")
    boundary = comp
    current = Ctx(ctx.comp_t, ctx.comp_t, frame=ctx.frame, vars=ctx.vars)
    matrix, box = rc.root_matrix, (rc.doc.width, rc.doc.height)
    symbol = next((a for a in (node, *node.iterancestors()) if ln(a) == "symbol"), None)
    skip = 0
    if rc.coordinate_root is not None:
        origin, base = rc.coordinate_root
        origin_ctx = rc.ev.context_at(origin, base, ctx.comp_t)
        target_ctx = rc.ev.context_at(symbol, ctx, ctx.comp_t) if symbol is not None else current
        a, b = origin_ctx.scope.path, target_ctx.scope.path
        common = 0
        while common < min(len(a), len(b)) and a[common] == b[common]:
            common += 1
        if common == len(a) and (a or symbol is origin):
            boundary, current, skip = origin, origin_ctx, common
            box = (rc.ev.num(origin, 'width', current, rc.doc.width),
                   rc.ev.num(origin, 'height', current, rc.doc.height))
            local.scene_context, local.scene_matrix = (origin, current), matrix
        else:
            # Cancel the shared instance prefix structurally. Only the path
            # from that common scene to the requested origin needs an inverse;
            # a collapsed outer instance never enters either matrix.
            basis = copy(rc)
            basis.coordinate_root = None
            basis.root_matrix = np.eye(3)
            basis.frame_cache = {}
            if common:
                instance = rc.doc.ids[a[common-1]]
                entered = rc.ev.context_at(instance, origin_ctx, ctx.comp_t)
                shared = rc.doc.ids[rc.ev.str(instance, 'symbol', entered)]
                basis.coordinate_root = (shared, rc.ev.context_at(shared, origin_ctx, ctx.comp_t))
            canvas = locate(basis, origin, origin_ctx).matrix
            try:
                inverse = np.linalg.inv(canvas)
            except np.linalg.LinAlgError:
                raise SingularCanvasReference(node, ctx, basis, canvas) from None
            basis.root_matrix = rc.root_matrix @ inverse
            basis.frame_cache = {}
            return locate(basis, node, ctx)

    def descend(target, root, clock, parent_matrix, parent_box):
        ancestors = []
        for ancestor in target.iterancestors():
            if ancestor is root:
                break
            ancestors.append(ancestor)
        parent = root
        for ancestor in reversed(ancestors):
            if ln(ancestor) not in ("group", "sequence"):
                continue
            position = local.layout_positions(parent, clock, parent_box).get(ancestor)
            entered = local.enter_node(ancestor, clock)
            parent_matrix = local.node_matrix(ancestor, entered, parent_matrix, parent_box, position)
            parent_box = local.node_size(ancestor, entered, parent_box, position)
            clock = _repeat_vars(ancestor, child_ctx(local, ancestor, entered))
            parent = ancestor
        position = local.layout_positions(parent, clock, parent_box).get(target)
        return clock, parent_matrix, parent_box, position

    if symbol is not None and boundary is not symbol:
        for instance_id in ctx.scope.path[skip:]:
            instance = rc.doc.ids.get(instance_id)
            if instance is None or boundary not in instance.iterancestors():
                break
            parent_ctx, pm, pb, position = descend(instance, boundary, current, matrix, box)
            entered = local.enter_node(instance, parent_ctx)
            matrix = local.node_matrix(instance, entered, pm, pb, position)
            size = local.node_size(instance, entered, pb, position)
            boundary = rc.doc.ids.get(rc.ev.str(instance, "symbol", entered))
            if boundary is None:
                break
            current = rc.ev.enter_instance(instance, entered, boundary, keep_finished=True)
            box = (rc.ev.num(boundary, "width", current, rc.doc.width),
                   rc.ev.num(boundary, "height", current, rc.doc.height))
            mode = rc.ev.str(instance, "fit", entered, "none")
            if mode != "none":
                matrix = matrix @ fit_matrix(local, instance, entered, *box, size, mode)
            local.scene_context, local.scene_matrix = (boundary, current), matrix
            if boundary is symbol:
                break
        if boundary is not symbol:
            # A standalone symbol has no selected instance transform. Keep its
            # own canvas and the explicitly supplied evaluation scope.
            boundary, current = symbol, ctx
            box = (rc.ev.num(symbol, "width", current, rc.doc.width),
                   rc.ev.num(symbol, "height", current, rc.doc.height))
            matrix = rc.root_matrix
            local.scene_context, local.scene_matrix = (symbol, current), matrix
    if node is boundary:
        return Location(local, current, matrix, box, None)
    clock, matrix, box, position = descend(node, boundary, current, matrix, box)
    return Location(local, clock, matrix, box, position)


def node_contexts(rc, ctx):
    """Visit instantiated nodes on their own clocks, including inactive matte users."""
    from .nodes.core import _repeat_vars, child_ctx

    def walk(parent, current, symbols=()):
        for node in rc.doc.nodes(parent):
            entered = rc.enter_node(node, current)
            yield node, entered
            if ln(node) in ("group", "sequence"):
                yield from walk(node, _repeat_vars(node, child_ctx(rc, node, entered)), symbols)
            elif ln(node) == "instance":
                symbol = rc.doc.ids.get(rc.ev.str(node, "symbol", entered))
                if symbol is not None and symbol not in symbols:
                    scope = rc.ev.enter_instance(node, entered, symbol, keep_finished=True)
                    yield from walk(symbol, scope, (*symbols, symbol))

    yield from walk(rc.doc.section("composition"), Ctx(ctx.comp_t, ctx.comp_t, frame=ctx.frame))


def drawing_location(rc, node, ctx):
    """Locate a source in its actual drawing canvas, retaining ancestor warps.

    Ancestors supply clocks, layout, geometry and camera optics. The referenced
    node supplies its own pixels; parent opacity/mattes/effects are subsequent
    composition operations, not part of this dependency (as for 2D references).
    """
    from .nodes.core import _repeat_vars, child_ctx, fit_matrix
    root = next((a for a in node.iterancestors() if ln(a) == 'symbol'), rc.doc.section('composition'))
    branch = [node]
    for ancestor in node.iterancestors():
        if ancestor is root:
            break
        if ln(ancestor) in ('group', 'sequence'):
            branch.append(ancestor)
    branch.reverse()
    loc = locate(rc, branch[0], ctx)
    local, stages = loc.rc, []
    clock, PM, box = loc.ctx, loc.matrix, loc.box
    local._flat_depth, local._raster_to_frame = 0, None
    if ln(root) == 'symbol':
        entrance = local.ev.context_at(root, ctx, ctx.comp_t)
        origin_depth = len(local.coordinate_root[1].scope.path) if local.coordinate_root is not None else 0
        if len(entrance.scope.path) > origin_depth:
            instance = local.doc.ids[entrance.scope.path[-1]]
            instance_ctx = local.ev.context_at(instance, ctx, ctx.comp_t)
            upstream = drawing_location(local, instance, instance_ctx)
            if upstream is None:
                return None
            pos, stages = upstream
            local = pos.rc
            entered = local.enter_node(instance, pos.ctx)
            M = local.node_matrix(instance, entered, pos.matrix, pos.box, pos.layout)
            size = local.node_size(instance, entered, pos.box, pos.layout)
            M = _drawing_parent(local, instance, entered, M, size, stages)
            if M is None:
                return None
            clock = local.ev.enter_instance(instance, entered, root, keep_finished=True)
            box = (local.ev.num(root, 'width', clock, local.doc.width),
                   local.ev.num(root, 'height', clock, local.doc.height))
            mode = local.ev.str(instance, 'fit', entered, 'none')
            PM = M if mode == 'none' else M @ fit_matrix(local, instance, entered, *box, size, mode)
            local.scene_context, local.scene_matrix = (root, clock), PM
    parent = root
    for el in branch[:-1]:
        position = local.layout_positions(parent, clock, box).get(el)
        entered = local.enter_node(el, clock)
        M = local.node_matrix(el, entered, PM, box, position)
        size = local.node_size(el, entered, box, position)
        M = _drawing_parent(local, el, entered, M, size, stages)
        if M is None:
            return None
        PM, box = M, size
        clock = _repeat_vars(el, child_ctx(local, el, entered))
        parent = el
    position = local.layout_positions(parent, clock, box).get(node)
    return Location(local, clock, PM, box, position), stages


@dataclass
class ProjectionStage:
    node: object
    ctx: Ctx
    matrix: np.ndarray
    warp: np.ndarray | None
    outer: np.ndarray | None
    projected: bool
    scene_context: tuple | None
    scene_matrix: np.ndarray | None


def _drawing_parent(local, el, entered, M, size, stages):
    projected = local.is_threed(el) and local.hooks.get('camera') is not None
    if projected:
        M = local.hooks['camera'](local, el, M, entered)
        if M is None:
            return None
    outer, warp = local._raster_to_frame, None
    if abs(M[2, 0])+abs(M[2, 1]) > 1e-12:
        A = local.projective_flat_matrix(M, size)
        if A is None:
            return None
        warp = M @ np.linalg.inv(A)
        local._raster_to_frame = warp if outer is None else outer @ warp
        M = A
        local._flat_depth += 1
    if projected or warp is not None:
        stages.append(ProjectionStage(el, entered, M, warp, outer, projected,
                                      local.scene_context, local.scene_matrix))
    return M


def rendered_matrix(rc, node, ctx):
    """Drawing coordinates without rendering geometry or recursive dependencies."""
    located = drawing_location(rc, node, ctx)
    if located is None:
        return None
    loc, _ = located
    local = loc.rc
    entered = local.enter_node(node, loc.ctx)
    M = local.node_matrix(node, entered, loc.matrix, loc.box, loc.layout)
    if local.is_threed(node) and local.hooks.get('camera'):
        M = local.hooks['camera'](local, node, M, entered)
        if M is None:
            return None
    return M if local._raster_to_frame is None else local._raster_to_frame @ M


def render_source(rc, node, ctx, *, force=False):
    """Draw a referenced subtree through its inherited camera canvases."""
    from .compositor import Out
    from .raster import Buf, warp_projective
    located = drawing_location(rc, node, ctx)
    if located is None:
        return None
    loc, stages = located
    local = loc.rc
    local._render_selection = {}
    local._render_matrices = None
    out = local.render_node(node, loc.ctx, loc.matrix, loc.box, loc.layout, force=force)
    if out is None or not stages:
        return out
    buf = Buf(out.buf.px*out.opacity, out.buf.x0, out.buf.y0)
    for stage in reversed(stages):
        local.scene_context, local.scene_matrix = stage.scene_context, stage.scene_matrix
        if stage.projected:
            from .camera import depth_of_field
            buf = depth_of_field(local, stage.node, buf, stage.ctx, stage.matrix)
        if stage.warp is not None:
            local._flat_depth -= 1
            local._raster_to_frame = stage.outer
            buf = warp_projective(buf, stage.warp, local.raster_bounds(margin=0))
            if buf is None:
                return None
            post = local.hooks.get('camera_post')
            if post is not None and not local._flat_depth:
                buf = post(local, stage.node, buf, stage.ctx)
    return Out(buf, out.blend, 1.)
