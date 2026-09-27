"""Shape modifiers (shapeModifier type=...), applied in document order to a shape's paths.

Registered per-path handlers follow the registry contract fn(rc, mod_el, cmds, ctx) -> [cmds].
Modifiers that need every path at once (merge, trim) also expose `fn.apply_all(rc, mod_el,
paths, ctx) -> paths`, which nodes/core.shape_paths calls. `region(paths, rule, tol)` builds the exact
filled region of paths (nonzero/evenodd) as a shapely geometry (also used for rigidBody shape="path").

Attribute meanings (pinned here; the schema only lists names):
  repeater      copies (floor of the value), offset (index shift), offsetX/offsetY px, rotation deg and
                scale factor per copy, about the shape's anchor point; composite above (later copies
                on top) | below. start/endOpacity are attached to each returned path as `.opacity`
                (OpPath), which nodes/core draws as separate groups.
  offset-path   amount px: closed subpaths are filled (nonzero) and the region is grown (> 0) or shrunk
                by |amount| with shapely's buffer (join miter|round|bevel from @mode, miter limit 4 as in
                After Effects); open subpaths get a parallel curve (> 0 = right of travel on screen).
                Self-intersections and vanishing parts are resolved exactly.
  pucker-bloat  amount percent, Lottie semantics (> 0 bloat, < 0 pucker).
  zig-zag       size px, ridges per segment, mode corner|smooth.
  twist         amount deg at the farthest point from the path's centre, proportional to distance.
  round-corners amount = corner radius px (corners between straight segments).
  wiggle-path   size px amplitude, detail = points per original segment, frequency wiggles/s, seed.
  merge         mode add|subtract|intersect|exclude between paths (first path is the base; the rest are
                applied in order: union, difference, intersection, symmetric difference). Each operand
                is the nonzero-filled region of its path (curves flattened at 0.05 px); exact polygon
                booleans (shapely/GEOS); one path out, holes as opposite-winding subpaths. A single path
                uses its subpaths as the operands.
  trim          amount = percent trimmed from the end (amount < 0 trims from the start instead),
                offset deg (360 = one turn); sequential across all paths laid end to end (each path keeps
                its own piece and opacity), the window wraps around like After Effects' offset.
"""
from __future__ import annotations

import math

import numpy as np

from . import geometry
from .document import ln
from .physics import vnoise
from .registry import FULL, SHAPE_MODIFIERS, warn_once

KAPPA = geometry.KAPPA


class OpPath(list):
    """A path (list of commands) carrying a per-path opacity multiplier."""
    opacity: float = 1.0


def _with_opacity(cmds, op: float):
    p = OpPath(cmds)
    p.opacity = op
    return p


# ------------------------------------------------------------------ path helpers
def subpaths(cmds) -> list[tuple[list, bool]]:
    """Split into [(segments, closed)] where each segment is ((x0,y0),(c1),(c2),(x1,y1)); lines have
    control points equal to their endpoints (flagged by a trailing 'L')."""
    out = []
    segs: list = []
    cur = start = None
    closed = False
    for c in cmds:
        op = c[0]
        if op == "M":
            if segs:
                out.append((segs, closed))
            segs, closed = [], False
            cur = start = (c[1], c[2])
        elif op == "L":
            if cur is None:
                cur = start = (c[1], c[2])
                continue
            p = (c[1], c[2])
            segs.append((cur, cur, p, p, "L"))
            cur = p
        elif op == "C":
            if cur is None:
                cur = start = (c[5], c[6])
                continue
            p = (c[5], c[6])
            segs.append((cur, (c[1], c[2]), (c[3], c[4]), p, "C"))
            cur = p
        elif op == "Z":
            if cur is not None and start is not None:
                if math.dist(cur, start) > 1e-9:
                    segs.append((cur, cur, start, start, "L"))
                closed = True
                if segs:
                    out.append((segs, closed))
                segs, closed = [], False
                cur = start
    if segs:
        out.append((segs, closed))
    return out


def join(subs) -> list:
    cmds = []
    for segs, closed in subs:
        if not segs:
            continue
        cmds.append(("M", *segs[0][0]))
        for s in segs:
            if s[4] == "L":
                cmds.append(("L", *s[3]))
            else:
                cmds.append(("C", *s[1], *s[2], *s[3]))
        if closed:
            cmds.append(("Z",))
    return cmds


def polyline_cmds(pts, closed) -> list:
    if len(pts) < 2:
        return []
    cmds = [("M", float(pts[0][0]), float(pts[0][1]))] + [("L", float(p[0]), float(p[1])) for p in pts[1:]]
    if closed:
        cmds.append(("Z",))
    return cmds


def catmull_rom(pts, closed) -> list:
    """Smooth path through points (uniform Catmull-Rom as cubic beziers)."""
    n = len(pts)
    if n < 3:
        return polyline_cmds(pts, closed)
    P = [np.asarray(p, np.float64) for p in pts]
    cmds = [("M", *map(float, P[0]))]
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p0 = P[(i - 1) % n] if (closed or i > 0) else P[i]
        p1, p2 = P[i], P[(i + 1) % n]
        p3 = P[(i + 2) % n] if (closed or i + 2 < n) else P[(i + 1) % n]
        c1 = p1 + (p2 - p0) / 6
        c2 = p2 - (p3 - p1) / 6
        cmds.append(("C", *map(float, c1), *map(float, c2), *map(float, p2)))
    if closed:
        cmds.append(("Z",))
    return cmds


def _bez(seg, u):
    p0, c1, c2, p1 = (np.asarray(v, np.float64) for v in seg[:4])
    m = 1 - u
    return m ** 3 * p0 + 3 * m * m * u * c1 + 3 * m * u * u * c2 + u ** 3 * p1


def _bez_d(seg, u):
    p0, c1, c2, p1 = (np.asarray(v, np.float64) for v in seg[:4])
    m = 1 - u
    d = 3 * m * m * (c1 - p0) + 6 * m * u * (c2 - c1) + 3 * u * u * (p1 - c2)
    if np.hypot(*d) < 1e-9:
        d = p1 - p0
    return d


def _flat(cmds, tol=0.2):
    return [(np.array(p, np.float64), closed) for p, closed in geometry.flatten(cmds, tol)]


def _area(pts) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _shape_of(m):
    p = m.getparent()
    return p if p is not None and ln(p) == "shape" else None


def _seed(rc, m, tag):
    shp = _shape_of(m)
    idx = list(shp).index(m) if shp is not None else 0
    if m.get("seed"):
        return int(m.get("seed")) ^ rc.doc.seed
    return rc.ev.seed_for(shp if shp is not None else m, f"{tag}:{idx}")


# ================================================================== repeater
def _repeater(rc, m, cmds, ctx):
    ev = rc.ev
    n = int(math.floor(ev.num(m, "copies", ctx, 3.0) + 1e-9))
    if n <= 0:
        return []
    off = ev.num(m, "offset", ctx, 0.0)
    ox, oy = ev.num(m, "offsetX", ctx, 0.0), ev.num(m, "offsetY", ctx, 0.0)
    rot, sc = ev.num(m, "rotation", ctx, 0.0), ev.num(m, "scale", ctx, 1.0)
    so, eo = ev.num(m, "startOpacity", ctx, 1.0), ev.num(m, "endOpacity", ctx, 1.0)
    shp = _shape_of(m)
    ax = ay = 0.0
    if shp is not None:
        w = ev.length(shp, "width", ctx, rc.doc.width)
        h = ev.length(shp, "height", ctx, rc.doc.height)
        ax, ay = ev.length(shp, "anchorX", ctx, w), ev.length(shp, "anchorY", ctx, h)
    out = []
    for i in range(n):
        k = i + off
        a = math.radians(rot * k)
        s = sc ** k if sc > 0 else 0.0
        ca, sa = math.cos(a) * s, math.sin(a) * s
        tx, ty = ax + ox * k, ay + oy * k

        def f(x, y):
            dx, dy = x - ax, y - ay
            return tx + ca * dx - sa * dy, ty + sa * dx + ca * dy
        path = []
        for c in cmds:
            if c[0] == "Z":
                path.append(c)
            elif c[0] == "C":
                path.append(("C", *f(c[1], c[2]), *f(c[3], c[4]), *f(c[5], c[6])))
            else:
                path.append((c[0], *f(c[1], c[2])))
        op = so + (eo - so) * (i / (n - 1) if n > 1 else 0.0)
        out.append(_with_opacity(path, op * getattr(cmds, "opacity", 1.0)))
    if m.get("composite", "above") == "below":
        out.reverse()
    return out


SHAPE_MODIFIERS.register("repeater", level=FULL,
                          note="per-copy start/endOpacity drawn as separate groups")(_repeater)


# ================================================================== offset-path
def _winding(pt, rings) -> int:
    x, y = pt
    w = 0
    for R in rings:
        a, b = R, np.roll(R, -1, 0)
        up = (a[:, 1] <= y) & (b[:, 1] > y)
        dn = (a[:, 1] > y) & (b[:, 1] <= y)
        cr = (b[:, 0] - a[:, 0]) * (y - a[:, 1]) - (x - a[:, 0]) * (b[:, 1] - a[:, 1])
        w += int(np.sum(up & (cr > 0))) - int(np.sum(dn & (cr < 0)))
    return w


def region(paths, rule: str = "nonzero", tol: float = 0.05):
    """Exact filled region of paths (open subpaths are closed, as filling does) as a shapely geometry."""
    import shapely
    from shapely.geometry import LineString
    rings = []
    for cmds in paths:
        for pts, _closed in geometry.flatten(cmds, tol):
            P = np.asarray(pts, np.float64)
            if len(P) > 1 and np.allclose(P[0], P[-1]):
                P = P[:-1]
            if len(P) >= 3 and abs(_area(P)) > 1e-12:
                rings.append(P)
    if not rings:
        return None
    noded = shapely.unary_union([LineString(np.vstack([R, R[:1]])) for R in rings])
    keep = []
    for f in shapely.polygonize([noded]).geoms:
        if f.area <= 1e-12:
            continue
        w = _winding(tuple(f.representative_point().coords[0]), rings)
        if (w != 0) if rule != "evenodd" else (w % 2 == 1):
            keep.append(f)
    return shapely.unary_union(keep) if keep else None


def region_cmds(geom) -> list:
    """Path commands of a shapely (Multi)Polygon: exteriors and holes with opposite windings."""
    from shapely.geometry.polygon import orient
    out: list = []
    if geom is None or geom.is_empty:
        return out
    for g in getattr(geom, "geoms", [geom]):
        if g.geom_type != "Polygon" or g.is_empty:
            continue
        g = orient(g, 1.0)
        for ring in [g.exterior, *g.interiors]:
            out += polyline_cmds(np.asarray(ring.coords)[:-1], True)
    return out


_JOINS = {"miter": "mitre", "round": "round", "bevel": "bevel"}


def _offset_path(rc, m, cmds, ctx):
    from shapely.geometry import LineString
    d = rc.ev.num(m, "amount", ctx, 0.0)
    if abs(d) < 1e-9:
        return [cmds]
    js = _JOINS.get((m.get("mode") or "miter").strip(), "mitre")
    closed = [join([sp]) for sp in subpaths(cmds) if sp[1]]
    out: list = []
    if closed:
        reg = region(closed)
        if reg is not None:
            out += region_cmds(reg.buffer(d, quad_segs=16, join_style=js, mitre_limit=4.0))
    for pts, cl in _flat(cmds, 0.05):
        if cl or len(pts) < 2:
            continue
        # shapely offsets to the left of travel in its (y-up) frame = the right of travel on a y-down screen
        oc = LineString(pts).offset_curve(d, quad_segs=16, join_style=js, mitre_limit=4.0)
        for g in getattr(oc, "geoms", [oc]):
            if not g.is_empty:
                out += polyline_cmds(np.asarray(g.coords), False)
    return [_with_opacity(out, cmds.opacity) if isinstance(cmds, OpPath) else out]


SHAPE_MODIFIERS.register("offset-path", level=FULL,
                         note="exact region offset (shapely buffer, miter/round/bevel joins); open paths get a "
                              "parallel curve")(_offset_path)


# ================================================================== pucker-bloat
def _pucker_bloat(rc, m, cmds, ctx):
    amt = rc.ev.num(m, "amount", ctx, 0.0) / 100.0
    if amt == 0:
        return [cmds]
    subs = subpaths(cmds)
    verts = [np.array(s[0], np.float64) for segs, _ in subs for s in segs]
    if not verts:
        return [cmds]
    cen = np.mean(verts, 0)
    mv = lambda p, k: tuple(np.asarray(p) + (cen - np.asarray(p)) * k)  # noqa: E731
    out = []
    for segs, closed in subs:
        ns = []
        for p0, c1, c2, p1, _ in segs:
            ns.append((mv(p0, amt), mv(c1, -amt), mv(c2, -amt), mv(p1, amt), "C"))
        out.append((ns, closed))
    return [join(out)]


SHAPE_MODIFIERS.register("pucker-bloat", level=FULL)(_pucker_bloat)


# ================================================================== zig-zag
def _zigzag(rc, m, cmds, ctx):
    ev = rc.ev
    size = ev.num(m, "size", ctx, 10.0)
    ridges = max(0, int(ev.num(m, "ridges", ctx, 5.0)))
    if ridges == 0 or size == 0:
        return [cmds]
    smooth = (m.get("mode") or "corner") == "smooth"
    out = []
    for segs, closed in subpaths(cmds):
        pts = []
        sign = 1.0
        for si, seg in enumerate(segs):
            ks = range(0 if si == 0 else 1, 2 * ridges + 1)
            for k in ks:
                u = k / (2 * ridges)
                p = _bez(seg, u)
                d = _bez_d(seg, u)
                nn = np.array([-d[1], d[0]]) / (np.hypot(*d) or 1.0)
                pts.append(p + nn * size * sign)
                sign = -sign
        if closed and len(pts) > 1 and np.allclose(_bez(segs[-1], 1.0), _bez(segs[0], 0.0)):
            pts = pts[:-1]
        out += catmull_rom(pts, closed) if smooth else polyline_cmds(pts, closed)
    return [out]


SHAPE_MODIFIERS.register("zig-zag", level=FULL)(_zigzag)


# ================================================================== twist
def _twist(rc, m, cmds, ctx):
    ang = rc.ev.num(m, "amount", ctx, 0.0)
    if ang == 0:
        return [cmds]
    polys = _flat(cmds, 0.25)
    if not polys:
        return [cmds]
    allp = np.concatenate([p for p, _ in polys])
    cen = (allp.min(0) + allp.max(0)) / 2
    R = float(np.max(np.hypot(*(allp - cen).T))) or 1.0
    out = []
    for pts, closed in polys:
        d = pts - cen
        a = np.radians(ang) * np.hypot(d[:, 0], d[:, 1]) / R
        c, s = np.cos(a), np.sin(a)
        q = cen + np.stack([d[:, 0] * c - d[:, 1] * s, d[:, 0] * s + d[:, 1] * c], 1)
        if closed:
            q = q[:-1]
        out += polyline_cmds(q, closed)
    return [out]


SHAPE_MODIFIERS.register("twist", level=FULL)(_twist)


# ================================================================== round-corners
def _round_corners(rc, m, cmds, ctx):
    r = rc.ev.num(m, "amount", ctx, 0.0)
    if r <= 0:
        return [cmds]
    out = []
    for segs, closed in subpaths(cmds):
        n = len(segs)
        new = []
        cut_start = [None] * n        # replaced start point of segment i
        for i in range(n):
            if not closed and i == n - 1:
                break
            a, b = segs[i], segs[(i + 1) % n]
            if a[4] != "L" or b[4] != "L":
                continue
            v = np.array(a[3])
            u1 = v - np.array(a[0])
            u2 = np.array(b[3]) - v
            l1, l2 = math.hypot(*u1), math.hypot(*u2)
            if l1 < 1e-9 or l2 < 1e-9:
                continue
            u1, u2 = u1 / l1, u2 / l2
            if abs(u1[0] * u2[1] - u1[1] * u2[0]) < 1e-6:
                continue
            cut_start[(i + 1) % n] = (v, u1, u2, min(r, l1 / 2, l2 / 2))
        for i in range(n):
            p0, c1, c2, p1, kind = segs[i]
            cs = cut_start[i]
            ce = cut_start[(i + 1) % n] if (closed or i + 1 < n) else None
            s0 = tuple(cs[0] + cs[2] * cs[3]) if cs else p0
            e0 = tuple(ce[0] - ce[1] * ce[3]) if ce else p1
            if kind == "L":
                new.append((s0, s0, e0, e0, "L"))
            else:
                new.append((s0, c1, c2, e0, "C"))
            if ce:
                v, ua, ub, d = ce
                pa, pb = v - ua * d, v + ub * d
                new.append((tuple(pa), tuple(pa + ua * d * KAPPA), tuple(pb - ub * d * KAPPA), tuple(pb), "C"))
        out.append((new, closed))
    return [join(out)]


SHAPE_MODIFIERS.register("round-corners", level=FULL, note="corners between straight segments")(_round_corners)


# ================================================================== wiggle-path
def _wiggle(rc, m, cmds, ctx):
    ev = rc.ev
    size = ev.num(m, "size", ctx, 10.0)
    if size == 0:
        return [cmds]
    detail = max(1.0, ev.num(m, "detail", ctx, 10.0))
    freq = ev.num(m, "frequency", ctx, 2.0)
    seed = _seed(rc, m, "wiggle")
    out = []
    for si, (segs, closed) in enumerate(subpaths(cmds)):
        count = max(3, int(round(detail * len(segs))))
        sub = join([(segs, closed)])
        flat = _flat(sub, 0.1)
        if not flat:
            continue
        pts, cl = flat[0]
        acc = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        total = acc[-1]
        if total <= 0:
            continue
        ds = np.linspace(0, total, count + (0 if closed else 1), endpoint=not closed)
        xs, ys = np.interp(ds, acc, pts[:, 0]), np.interp(ds, acc, pts[:, 1])
        idx = np.arange(len(ds)) + si * 1000
        t = ctx.t * freq
        dx = vnoise(seed, idx * 0.61803, t, 0.5) * size
        dy = vnoise(seed + 17, idx * 0.61803, t, 3.5) * size
        q = np.stack([xs + dx, ys + dy], 1)
        out += catmull_rom(list(q), closed)
    return [out]


SHAPE_MODIFIERS.register("wiggle-path", level=FULL, note="seeded value noise, animated by time x frequency")(_wiggle)


# ================================================================== merge
def merge_paths(rc, m, paths, ctx) -> list:
    """Boolean combination of paths (first = base). Returns one path."""
    paths = [p for p in paths if p]
    if len(paths) == 1:                   # one path: its subpaths are the operands
        paths = [join([sp]) for sp in subpaths(paths[0])]
    if len(paths) < 2:
        return paths
    mode = m.get("mode") or "add"
    if mode not in ("add", "subtract", "intersect", "exclude"):
        warn_once("shapeModifier", f"merge:{mode}", "unknown merge mode; using add")
        mode = "add"
    from shapely.geometry import Polygon
    empty = Polygon()
    acc = region([paths[0]]) or empty
    for p in paths[1:]:
        b = region([p]) or empty
        acc = {"add": acc.union, "intersect": acc.intersection, "subtract": acc.difference,
               "exclude": acc.symmetric_difference}[mode](b)
    return [region_cmds(acc)]


def _merge(rc, m, cmds, ctx):
    return merge_paths(rc, m, [cmds], ctx)


_merge.apply_all = merge_paths
SHAPE_MODIFIERS.register("merge", level=FULL,
                         note="exact polygon booleans (shapely) across the shape's paths (apply_all)")(_merge)


# ================================================================== trim
def _trim_range(rc, m, ctx):
    a = rc.ev.num(m, "amount", ctx, 0.0) / 100.0
    off = rc.ev.num(m, "offset", ctx, 0.0) / 360.0
    a = max(-1.0, min(1.0, a))
    return (0.0, 1.0 - a, off) if a >= 0 else (-a, 1.0, off)


def trim_paths(rc, m, paths, ctx) -> list:
    """Sequential trim across all paths laid end to end; each path keeps its own piece (and opacity)."""
    s, e, off = _trim_range(rc, m, ctx)
    if s <= 0 and e >= 1 and off % 1 == 0:
        return paths
    if e - s <= 1e-9:
        return []
    s, e = s + off, e + off
    k = math.floor(s)
    s, e = s - k, e - k
    spans = [(s, e)] if e <= 1 else [(s, 1.0), (0.0, e - 1)]
    flat = [[(p, geometry._cum(p), cl) for p, cl in geometry.flatten(cmds)] for cmds in paths]
    total = sum(acc[-1] for f in flat for _, acc, _ in f)
    if total <= 0:
        return []
    out, base = [], 0.0
    for cmds, f in zip(paths, flat):
        piece: list = []
        for pts, acc, cl in f:
            L = acc[-1]
            for a, b in spans:
                a0, b0 = max(a * total - base, 0.0), min(b * total - base, L)
                if b0 - a0 <= 1e-9:
                    continue
                if cl and a0 <= 1e-9 and b0 >= L - 1e-9:
                    piece += polyline_cmds(pts[:-1], True)
                    continue
                seg = geometry._cut(pts, acc, a0, b0)
                if len(seg) > 1:
                    piece += polyline_cmds(seg, False)
            base += L
        if piece:
            out.append(_with_opacity(piece, cmds.opacity) if isinstance(cmds, OpPath) else piece)
    return out


def _trim(rc, m, cmds, ctx):
    return trim_paths(rc, m, [cmds], ctx)


_trim.apply_all = trim_paths
SHAPE_MODIFIERS.register("trim", level=FULL,
                         note="amount = percent trimmed, offset in degrees; sequential across paths (apply_all)")(_trim)
