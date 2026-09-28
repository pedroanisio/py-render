"""Locate referenced nodes using the same clocks, layouts and canvases as traversal."""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass

from .document import ln
from .evaluator import Ctx


@dataclass
class Location:
    rc: object
    ctx: Ctx                 # clock before entering the referenced node
    matrix: object           # XML parent space -> frame
    box: tuple
    layout: tuple | None


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
    symbol = next((a for a in node.iterancestors() if ln(a) == "symbol"), None)

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

    if symbol is not None:
        for instance_id in ctx.scope.path:
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
