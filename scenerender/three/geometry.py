"""Procedural geometry for object3D primitives, 3D text and extruded SVG paths.

All meshes are built in the object's engine-local space (camera.py: scene units = document pixels,
+Y up = scene -y, +Z toward the implicit camera = scene -z, so a plane's normal is scene -z) with smooth normals, UVs (glTF convention: v = 0 at the top of an image) and
MikkTSpace-style per-vertex tangents. Pinned dimensions (the schema leaves them open):

  * sphere: radius; `segments` longitudes and segments/2 latitude bands; equirectangular UVs.
  * box: width x height x depth (width/height default 2*radius, depth defaults to width when the
    attribute is absent — the schema default 10 is meant for text/extrude); `bevel` rounds every
    edge with that radius (clamped to half the smallest side); faces are subdivided segments/8 times.
  * plane: width x height in the local XY plane facing +Z, subdivided segments x segments (so
    displacement maps have vertices to move).
  * cylinder / cone: radius, height (default 2*radius) along Y, capped; `bevel` rounds the rims.
  * torus: ring radius = radius, tube radius = height/2 when height is set, else 0.35*radius; tube
    segments = segments/2.
  * capsule: radius, total height (default 4*radius) including the hemispherical caps.
  * text: Pango layout of @text in @font (a Pango font description or a font asset id) at an em size
    of `height` (default 100); `width` wraps lines; the ink box is centred on the origin in X/Y.
  * extrude: SVG path data in document pixels (+y down, flipped to +Y up); the path origin is the
    object origin.
  * text/extrude: caps are the fill (non-zero winding) polygons triangulated by constrained
    Delaunay; the solid spans z = -depth/2 .. +depth/2; `bevel` adds a rounded (quarter-circle,
    4-step) chamfer that insets the caps by `bevel` and shortens the straight sides, like the
    "round" bevel of After Effects / Cinema 4D extrusions. Side walls use flat normals at corners
    sharper than 35 degrees and smooth normals elsewhere.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Geo:
    positions: np.ndarray
    normals: np.ndarray
    uvs: np.ndarray
    indices: np.ndarray
    tangents: np.ndarray | None = None

    def finish(self) -> "Geo":
        self.positions = np.ascontiguousarray(self.positions, np.float32)
        self.normals = np.ascontiguousarray(self.normals, np.float32)
        self.uvs = np.ascontiguousarray(self.uvs, np.float32)
        self.indices = np.ascontiguousarray(self.indices, np.uint32).reshape(-1, 3)
        if self.tangents is None:
            self.tangents = compute_tangents(self.positions, self.normals, self.uvs, self.indices)
        return self


def merge(parts: list[Geo]) -> Geo:
    pos, nrm, uv, idx = [], [], [], []
    off = 0
    for g in parts:
        pos.append(g.positions)
        nrm.append(g.normals)
        uv.append(g.uvs)
        idx.append(np.asarray(g.indices).reshape(-1, 3) + off)
        off += len(g.positions)
    return Geo(np.concatenate(pos), np.concatenate(nrm), np.concatenate(uv), np.concatenate(idx)).finish()


def compute_normals(pos: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Area-weighted smooth vertex normals."""
    p = np.asarray(pos, np.float64)
    tri = p[idx]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n = np.zeros_like(p)
    for k in range(3):
        np.add.at(n, idx[:, k], fn)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return (n / np.where(ln > 1e-20, ln, 1.0)).astype(np.float32)


def compute_tangents(pos, nrm, uv, idx) -> np.ndarray:
    """Per-vertex tangents (xyz, w = bitangent sign) accumulated from UV gradients (Lengyel)."""
    p = np.asarray(pos, np.float64)
    t = np.asarray(uv, np.float64)
    n = np.asarray(nrm, np.float64)
    idx = np.asarray(idx).reshape(-1, 3)
    if len(idx) == 0:
        return np.zeros((len(p), 4), np.float32)
    e1, e2 = p[idx[:, 1]] - p[idx[:, 0]], p[idx[:, 2]] - p[idx[:, 0]]
    d1, d2 = t[idx[:, 1]] - t[idx[:, 0]], t[idx[:, 2]] - t[idx[:, 0]]
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(r) > 1e-20, 1.0 / np.where(np.abs(r) > 1e-20, r, 1.0), 0.0)[:, None]
    # glTF UVs have v pointing down the image; tangent follows +u, bitangent follows -v.
    sdir = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r
    tdir = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r
    T = np.zeros_like(p)
    B = np.zeros_like(p)
    for k in range(3):
        np.add.at(T, idx[:, k], sdir)
        np.add.at(B, idx[:, k], tdir)
    T = T - n * np.sum(n * T, 1, keepdims=True)
    ln = np.linalg.norm(T, axis=1, keepdims=True)
    fallback = np.cross(n, np.where(np.abs(n[:, 1:2]) < 0.99, [[0, 1, 0]], [[1, 0, 0]]))
    T = np.where(ln > 1e-12, T / np.where(ln > 1e-12, ln, 1), fallback / np.maximum(np.linalg.norm(fallback, axis=1, keepdims=True), 1e-12))
    w = np.where(np.sum(np.cross(n, T) * B, 1) < 0, -1.0, 1.0)
    return np.c_[T, w].astype(np.float32)


def _grid(nu: int, nv: int, f):
    """Parametric surface on a (nu+1) x (nv+1) grid: f(u, v) -> (pos, normal) arrays."""
    u, v = np.meshgrid(np.linspace(0, 1, nu + 1), np.linspace(0, 1, nv + 1))
    P, N = f(u, v)
    uv = np.stack([u, v], -1).reshape(-1, 2)
    i = np.arange((nu + 1) * (nv + 1)).reshape(nv + 1, nu + 1)
    a, b, c, d = i[:-1, :-1], i[:-1, 1:], i[1:, 1:], i[1:, :-1]
    idx = np.concatenate([np.stack([a, d, b], -1).reshape(-1, 3), np.stack([b, d, c], -1).reshape(-1, 3)])
    return Geo(P.reshape(-1, 3), N.reshape(-1, 3), uv, idx)


def sphere(r: float, seg: int) -> Geo:
    def f(u, v):
        th, ph = u * 2 * math.pi, v * math.pi
        n = np.stack([-np.cos(th) * np.sin(ph), np.cos(ph), np.sin(th) * np.sin(ph)], -1)
        return n * r, n
    return _grid(seg, max(2, seg // 2), f).finish()


def plane(w: float, h: float, seg: int) -> Geo:
    def f(u, v):
        P = np.stack([(u - 0.5) * w, (0.5 - v) * h, np.zeros_like(u)], -1)
        return P, np.broadcast_to([0.0, 0.0, 1.0], P.shape)
    return _grid(seg, seg, f).finish()


def torus(R: float, r: float, seg: int) -> Geo:
    def f(u, v):
        th, ph = u * 2 * math.pi, v * 2 * math.pi
        c = np.stack([np.cos(th), np.zeros_like(th), -np.sin(th)], -1)
        n = c * np.cos(ph)[..., None] + np.array([0, 1.0, 0]) * np.sin(ph)[..., None]
        return c * R + n * r, n
    g = _grid(seg, max(3, seg // 2), f)
    g.indices = np.asarray(g.indices)[:, ::-1]
    return g.finish()


def lathe(profile: list[tuple[float, float]], seg: int, v_of=None) -> Geo:
    """Surface of revolution about +Y of a (radius, y) profile, bottom to top. Consecutive equal
    points split the normal (hard edge). v runs 1 (bottom) .. 0 (top) by arc length."""
    pts = np.array(profile, np.float64)
    parts = []
    runs, cur = [], [pts[0]]
    for p in pts[1:]:
        if np.allclose(p, cur[-1]):
            runs.append(cur)
            cur = [p]
        else:
            cur.append(p)
    runs.append(cur)
    total = sum(float(np.sum(np.linalg.norm(np.diff(np.array(r), axis=0), axis=1))) for r in runs if len(r) > 1) or 1.0
    acc = 0.0
    for run in runs:
        run = np.array(run)
        if len(run) < 2:
            continue
        d = np.linalg.norm(np.diff(run, axis=0), axis=1)
        s = np.r_[0, np.cumsum(d)]
        # 2D normals of the profile polyline (outward = +radius side), averaged at inner vertices.
        seg_n = np.stack([np.diff(run[:, 1]), -np.diff(run[:, 0])], -1)
        seg_n /= np.maximum(np.linalg.norm(seg_n, axis=1, keepdims=True), 1e-12)
        vn = np.zeros_like(run)
        vn[:-1] += seg_n
        vn[1:] += seg_n
        vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
        k = len(run)
        th = np.linspace(0, 2 * math.pi, seg + 1)
        c, sn = np.cos(th), -np.sin(th)
        P = np.stack([run[:, 0:1] * c, np.broadcast_to(run[:, 1:2], (k, seg + 1)), run[:, 0:1] * sn], -1)
        N = np.stack([vn[:, 0:1] * c, np.broadcast_to(vn[:, 1:2], (k, seg + 1)), vn[:, 0:1] * sn], -1)
        U, V = np.meshgrid(th / (2 * math.pi), 1 - (acc + s) / total)
        i = np.arange(k * (seg + 1)).reshape(k, seg + 1)
        a, b, cc, dd = i[:-1, :-1], i[:-1, 1:], i[1:, 1:], i[1:, :-1]
        idx = np.concatenate([np.stack([a, b, dd], -1).reshape(-1, 3), np.stack([b, cc, dd], -1).reshape(-1, 3)])
        parts.append(Geo(P.reshape(-1, 3), N.reshape(-1, 3), np.stack([U, V], -1).reshape(-1, 2), idx))
        acc += s[-1]
    return merge(parts)


def _arc(cx, cy, r, a0, a1, n=5):
    return [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in np.linspace(a0, a1, n)]


def cylinder(r: float, h: float, seg: int, bevel: float = 0.0, top_r: float | None = None) -> Geo:
    """Capped cylinder (top_r = r) or cone (top_r = 0) along Y; bevel rounds the rims."""
    top = r if top_r is None else top_r
    b = min(bevel, r * 0.5, h * 0.25) if bevel > 0 else 0.0
    y0, y1 = -h / 2, h / 2
    if b <= 0:
        prof = [(0, y0), (r, y0), (r, y0), (top, y1)] + ([(top, y1), (0, y1)] if top > 1e-9 else [])
        return lathe(prof, seg)
    if top <= 1e-9:     # cone: round only the base rim, blending into the slanted side
        slope = math.atan2(r, h)                      # side normal tilt from horizontal
        prof = [(0, y0), (r - b, y0)] + _arc(r - b, y0 + b, b, -math.pi / 2, slope)[1:] + [(0, y1)]
        return lathe(prof, seg)
    prof = [(0, y0), (r - b, y0)] + _arc(r - b, y0 + b, b, -math.pi / 2, 0)[1:] + \
        _arc(top - b, y1 - b, b, 0, math.pi / 2)[:-1] + [(top - b, y1), (0, y1)]
    return lathe(prof, seg)


def capsule(r: float, h: float, seg: int) -> Geo:
    body = max(0.0, h - 2 * r) / 2
    n = max(3, seg // 4)
    prof = [(r * math.sin(a), -body - r * math.cos(a)) for a in np.linspace(0, math.pi / 2, n + 1)]
    prof += [(r * math.cos(a), body + r * math.sin(a)) for a in np.linspace(0, math.pi / 2, n + 1)]
    return lathe(prof, seg)


def box(w: float, h: float, d: float, bevel: float = 0.0, seg: int = 32) -> Geo:
    """Box centred at the origin; with bevel, a rounded box (each vertex of a subdivided cube is
    clamped to the inner box and pushed out by the bevel radius along the offset direction)."""
    b = min(max(0.0, bevel), w / 2, h / 2, d / 2)
    n = max(1, seg // 8) if b <= 0 else max(4, seg // 4)
    half = np.array([w, h, d]) / 2
    faces = [((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
             ((1, 0, 0), (0, 0, -1), (0, 1, 0)), ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
             ((0, 1, 0), (1, 0, 0), (0, 0, -1)), ((0, -1, 0), (1, 0, 0), (0, 0, 1))]
    parts = []
    for nz, ux, vy in faces:
        nz, ux, vy = map(np.array, (nz, ux, vy))

        def f(u, v, nz=nz, ux=ux, vy=vy):
            P = (nz[None, None] + ux * (u[..., None] * 2 - 1) + vy * (1 - v[..., None] * 2)) * half
            if b > 0:
                inner = half - b
                c = np.clip(P, -inner, inner)
                dlt = P - c
                ln = np.linalg.norm(dlt, axis=-1, keepdims=True)
                N = dlt / np.maximum(ln, 1e-12)
                return c + N * b, N
            return P, np.broadcast_to(nz.astype(float), P.shape)
        parts.append(_grid(n, n, f))
    return merge(parts)


# ------------------------------------------------------------------ 2D outlines -> solids
def _rings_to_polygons(rings: list[np.ndarray]):
    """Non-zero-winding fill of closed rings -> shapely (Multi)Polygon."""
    import shapely
    from shapely.geometry import Polygon
    polys = []
    for r in rings:
        if len(r) < 3:
            continue
        p = Polygon(r)
        if not p.is_valid:
            p = shapely.make_valid(p)
        if p.area > 1e-9:
            polys.append((p, _signed_area(r)))
    if not polys:
        return None
    # Winding number by containment: sum signs of rings containing a sample point of each piece.
    union = shapely.union_all([p for p, _ in polys])
    pieces = shapely.get_parts(shapely.polygonize([shapely.union_all([p.boundary for p, _ in polys])]))
    keep = []
    for pc in pieces:
        pt = pc.representative_point()
        wn = sum((1 if s > 0 else -1) for p, s in polys if p.contains(pt))
        if wn != 0:
            keep.append(pc)
    if not keep:
        return union
    return shapely.union_all(keep)


def _signed_area(r: np.ndarray) -> float:
    x, y = r[:, 0], r[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _triangulate(poly) -> tuple[np.ndarray, np.ndarray]:
    """Constrained Delaunay triangles of a polygon -> (vertices (k,2), indices (t,3)) CCW."""
    import shapely
    tris = shapely.get_parts(shapely.constrained_delaunay_triangles(poly))
    pts, idx, lut = [], [], {}
    for t in tris:
        c = np.asarray(t.exterior.coords)[:3]
        if _signed_area(c) < 0:
            c = c[::-1]
        tri = []
        for p in c:
            k = (round(p[0], 6), round(p[1], 6))
            if k not in lut:
                lut[k] = len(pts)
                pts.append(p)
            tri.append(lut[k])
        idx.append(tri)
    return np.array(pts, np.float64).reshape(-1, 2), np.array(idx, np.int64).reshape(-1, 3)


def _offset_ring(ring: np.ndarray, dist: float) -> np.ndarray:
    """Move every vertex of a CCW-outer / CW-hole ring inward by dist along its miter (1:1 vertices)."""
    prev, nxt = np.roll(ring, 1, 0), np.roll(ring, -1, 0)
    def nrm(d):
        d = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
        return np.stack([-d[:, 1], d[:, 0]], -1)          # left normal = inward for CCW
    n1, n2 = nrm(ring - prev), nrm(nxt - ring)
    m = n1 + n2
    ml = np.linalg.norm(m, axis=1, keepdims=True)
    m = np.where(ml > 1e-9, m / np.maximum(ml, 1e-12), n1)
    cosh = np.sum(m * n1, 1, keepdims=True)
    k = np.minimum(1.0 / np.maximum(cosh, 0.35), 2.0)
    return ring + m * dist * k


def extrude_polygons(poly, depth: float, bevel: float = 0.0, steps: int = 4) -> Geo | None:
    """Solid from a shapely (Multi)Polygon: caps at z = +-depth/2, rounded bevel of width `bevel`."""
    import shapely
    from shapely.geometry import Polygon
    if poly is None or poly.is_empty:
        return None
    parts = []
    d2 = depth / 2
    b = max(0.0, min(bevel, d2))
    minx, miny, maxx, maxy = poly.bounds
    sx, sy = max(maxx - minx, 1e-9), max(maxy - miny, 1e-9)
    for pg in shapely.get_parts(poly):
        if not isinstance(pg, Polygon) or pg.area <= 1e-9:
            continue
        pg = shapely.geometry.polygon.orient(pg, 1.0)          # exterior CCW, holes CW
        rings = [np.asarray(pg.exterior.coords)[:-1]] + [np.asarray(h.coords)[:-1] for h in pg.interiors]
        # profile: (inset, z) from the front cap edge down the side to the back cap edge
        if b > 0:
            angs = np.linspace(0, math.pi / 2, steps + 1)
            front = [(b * (1 - math.sin(a)), d2 - b + b * math.cos(a)) for a in angs]   # inset b..0, z d2..d2-b
            prof = front + [(i, -z) for i, z in reversed(front)]
        else:
            prof = [(0.0, d2), (0.0, -d2)]
        cap_rings = [_offset_ring(r, prof[0][0]) for r in rings]
        cap = Polygon(cap_rings[0], cap_rings[1:])
        if not cap.is_valid or cap.area <= 1e-9:
            cap = pg.buffer(-prof[0][0], join_style="mitre") if prof[0][0] > 0 else pg
        if cap.is_empty:
            cap = pg
        v2, tri = _triangulate(cap)
        uv = np.c_[(v2[:, 0] - minx) / sx, (maxy - v2[:, 1]) / sy]
        for z, sgn in ((prof[0][1], 1.0), (prof[-1][1], -1.0)):
            P = np.c_[v2, np.full(len(v2), z)]
            N = np.tile([0, 0, sgn], (len(v2), 1))
            parts.append(Geo(P, N, uv, tri if sgn > 0 else tri[:, ::-1]))
        for ring in rings:
            parts.append(_side(ring, prof, sx))
    return merge(parts) if parts else None


def _side(ring: np.ndarray, prof, perim_scale: float) -> Geo:
    """Side walls of one ring along the (inset, z) bevel/side profile; vertices split at corners
    sharper than 35 degrees, smooth normals elsewhere."""
    n = len(ring)
    closed = np.r_[ring, ring[:1]]
    seg = np.diff(closed, axis=0)
    segl = np.maximum(np.linalg.norm(seg, axis=1), 1e-12)
    tdir = seg / segl[:, None]
    out_n = np.stack([tdir[:, 1], -tdir[:, 0]], -1)            # outward for CCW exterior / CW hole
    sharp = np.sum(out_n * np.roll(out_n, 1, 0), 1) < math.cos(math.radians(35))
    inward = _offset_ring(ring, 1.0) - ring                      # per-vertex miter (inset direction)
    s_acc = np.r_[0, np.cumsum(segl)]
    pr = np.array(prof, np.float64)
    pnorm = [_prof_normal(pr, q) for q in range(len(pr))]
    k = len(pr)
    P, N, UV, idx = [], [], [], []
    for i in range(n):
        j = (i + 1) % n
        ends = []
        for v in (i, j):
            if sharp[v]:
                nn = out_n[i]
            else:
                m = out_n[v] + out_n[(v - 1) % n]
                nn = m / max(float(np.linalg.norm(m)), 1e-12)
            ends.append((ring[v], inward[v], nn, s_acc[i] if v == i else s_acc[i + 1]))
        base = len(P)
        for q in range(k):
            ins, z = pr[q]
            radial, nz = pnorm[q]
            for pt, inw, nn, s in ends:
                P.append([pt[0] + inw[0] * ins, pt[1] + inw[1] * ins, z])
                v3 = np.array([nn[0] * radial, nn[1] * radial, nz])
                N.append(v3 / max(float(np.linalg.norm(v3)), 1e-12))
                UV.append([s / perim_scale, q / max(1, k - 1)])
        for q in range(k - 1):
            a0, b0, c0, d0 = base + 2 * q, base + 2 * q + 1, base + 2 * q + 2, base + 2 * q + 3
            idx += [[a0, c0, b0], [b0, c0, d0]]
    return Geo(np.array(P), np.array(N), np.array(UV), np.array(idx))


def _prof_normal(pr: np.ndarray, q: int) -> tuple[float, float]:
    """Outward (radial, z) normal of the profile at sample q; the profile runs front (z>0) to back."""
    a = pr[max(q - 1, 0)]
    b = pr[min(q + 1, len(pr) - 1)]
    d_out, dz = -(b[0] - a[0]), b[1] - a[1]      # direction along the profile in (outward, z)
    nr, nz = -dz, d_out                            # rotated to the outward side
    ln = math.hypot(nr, nz) or 1.0
    return nr / ln, nz / ln


def text_polygons(text: str, font: str, size: float, width: float | None = None, align: str = "center"):
    """Glyph outlines of a Pango layout -> shapely geometry in scene units (+Y up), ink box centred."""
    import cairo
    from shapely import affinity
    from .. import pango_bridge as pb
    Pango = pb.Pango
    lay = pb.new_layout()
    fd = Pango.FontDescription.from_string(font or "Sans")
    fd.set_absolute_size(size * Pango.SCALE)
    lay.set_font_description(fd)
    if width:
        lay.set_width(int(width * Pango.SCALE))
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
    lay.set_alignment({"left": Pango.Alignment.LEFT, "right": Pango.Alignment.RIGHT}.get(align, Pango.Alignment.CENTER))
    lay.set_text(text, -1)
    surf = cairo.RecordingSurface(cairo.CONTENT_ALPHA, None)
    cr = cairo.Context(surf)
    cr.set_tolerance(max(0.02, size / 400))
    pb.layout_path(cr, lay)
    poly = _cairo_path_polygons(cr.copy_path_flat())
    if poly is None:
        return None
    poly = affinity.scale(poly, 1.0, -1.0, origin=(0, 0))
    minx, miny, maxx, maxy = poly.bounds
    return affinity.translate(poly, -(minx + maxx) / 2, -(miny + maxy) / 2)


def _cairo_path_polygons(path):
    import cairo
    rings, cur = [], []
    for kind, pts in path:
        if kind == cairo.PATH_MOVE_TO:
            if len(cur) > 2:
                rings.append(np.array(cur))
            cur = [pts]
        elif kind == cairo.PATH_LINE_TO:
            cur.append(pts)
        elif kind == cairo.PATH_CLOSE_PATH:
            if len(cur) > 2:
                rings.append(np.array(cur))
            cur = []
    if len(cur) > 2:
        rings.append(np.array(cur))
    rings = [_dedupe(r) for r in rings]
    return _rings_to_polygons([r for r in rings if len(r) >= 3])


def _dedupe(r: np.ndarray) -> np.ndarray:
    keep = np.r_[True, np.linalg.norm(np.diff(r, axis=0), axis=1) > 1e-7]
    r = r[keep]
    if len(r) > 1 and np.linalg.norm(r[0] - r[-1]) < 1e-7:
        r = r[:-1]
    return r


def path_polygons(d: str, tolerance: float = 0.25):
    """SVG path data (document px, +y down) -> shapely geometry in scene units (+Y up)."""
    import cairo
    from shapely import affinity
    from ..geometry import emit, parse_svg_path
    surf = cairo.RecordingSurface(cairo.CONTENT_ALPHA, None)
    cr = cairo.Context(surf)
    cr.set_tolerance(tolerance)
    emit(cr, parse_svg_path(d))
    poly = _cairo_path_polygons(cr.copy_path_flat())
    return None if poly is None else affinity.scale(poly, 1.0, -1.0, origin=(0, 0))
