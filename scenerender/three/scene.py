"""Per-frame 3D scene assembly: object3D evaluation (geometry, instances, materials, animation),
lights, environment and shadow maps, plus the post-processing of rendered layers.

See renderer.py for the frame model and geometry.py / materials.py / lights.py for the pinned
semantics of each attribute.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .. import gl
from ..camera import Camera, camera_at, node_clock, world3d
from ..document import ln
from ..evaluator import Ctx
from ..raster import Buf, linear_to_srgb, warp_projective
from ..registry import warn_once
from . import geometry as G
from . import renderer as R
from .lights import evaluate as eval_lights
from .materials import Material, default_material, from_spec, material_for, sample_map, map_uvs

PRIMS = ("sphere", "box", "plane", "cylinder", "cone", "torus", "capsule", "text", "extrude")
_WEIGHTS = {100: "Thin", 200: "Ultra-Light", 300: "Light", 500: "Medium", 600: "Semi-Bold", 700: "Bold",
            800: "Ultra-Bold", 900: "Heavy"}


def scene_root(el):
    p = el.getparent()
    while p is not None and isinstance(p.tag, str) and ln(p) not in ("composition", "symbol"):
        p = p.getparent()
    return p


def objects_in(rc, root) -> list:
    key = ("obj3d-list", root)
    hit = rc.cache.get(key)
    if hit is None:
        hit = [e for e in root.iter() if isinstance(e.tag, str) and ln(e) == "object3D"] if root is not None else []
        rc.cache[key] = hit
    return hit


# ------------------------------------------------------------------ geometry
def _font_desc(rc, font: str | None) -> str:
    if not font:
        return "Sans"
    a = rc.doc.ids.get(font)
    if a is not None and ln(a) == "font":
        w = int(a.get("weight", 400) or 400)
        style = " Italic" if a.get("fontStyle") == "italic" else " Oblique" if a.get("fontStyle") == "oblique" else ""
        wn = _WEIGHTS.get(int(round(w / 100.0)) * 100, "")
        return f"{a.get('family')}{style}{(' ' + wn) if wn else ''}"
    return font


def primitive_geo(rc, el, ctx) -> tuple[tuple, G.Geo | None]:
    ev = rc.ev
    prim = ev.str(el, "primitive", ctx)
    r = ev.num(el, "radius", ctx, 50.0)
    has = lambda k: ev.explicit(el, k, ctx)  # noqa: E731
    w = ev.num(el, "width", ctx, 2 * r) if has("width") else 2 * r
    h = ev.num(el, "height", ctx, 2 * r) if has("height") else None
    seg = max(3, min(256, int(ev.num(el, "segments", ctx, 32))))
    bevel = ev.num(el, "bevel", ctx, 0.0)
    depth = ev.num(el, "depth", ctx, 10.0)
    if prim == "sphere":
        key = (prim, r, seg)
    elif prim == "box":
        key = (prim, w, h if h is not None else 2 * r, depth if has("depth") else w, bevel, seg)
    elif prim == "plane":
        key = (prim, w, h if h is not None else 2 * r, seg)
    elif prim in ("cylinder", "cone"):
        key = (prim, r, h if h is not None else 2 * r, seg, bevel)
    elif prim == "torus":
        key = (prim, r, h / 2 if h is not None else 0.35 * r, seg)
    elif prim == "capsule":
        key = (prim, r, h if h is not None else 4 * r, seg)
    elif prim == "text":
        key = (prim, ev.str(el, "text", ctx, "") or "", _font_desc(rc, ev.str(el, "font", ctx, None)),
               h if h is not None else 100.0, w if has("width") else None, depth, bevel)
    elif prim == "extrude":
        key = (prim, ev.str(el, "path", ctx, "") or "", depth, bevel)
    else:
        return (prim,), None
    ck = ("geo", key)
    if ck in rc.cache:
        return key, rc.cache[ck]
    g = None
    try:
        if prim == "sphere":
            g = G.sphere(r, seg)
        elif prim == "box":
            g = G.box(key[1], key[2], key[3], bevel, seg)
        elif prim == "plane":
            g = G.plane(key[1], key[2], seg)
        elif prim == "cylinder":
            g = G.cylinder(r, key[2], seg, bevel)
        elif prim == "cone":
            g = G.cylinder(r, key[2], seg, bevel, top_r=0.0)
        elif prim == "torus":
            g = G.torus(r, key[2], seg)
        elif prim == "capsule":
            g = G.capsule(r, key[2], seg)
        elif prim == "text":
            if key[1].strip():
                poly = G.text_polygons(key[1], key[2], key[3], key[4])
                g = G.extrude_polygons(poly, depth, bevel)
        elif prim == "extrude":
            if key[1].strip():
                g = G.extrude_polygons(G.path_polygons(key[1]), depth, bevel)
    except Exception as e:  # noqa: BLE001 — bad text/path input: nothing drawn
        warn_once("object3D", el.get("id", "?"), f"geometry failed: {e}")
        g = None
    rc.cache[ck] = g
    return key, g


def _displace(pos, nrm, uv, idx, mat: Material):
    arr = mat.maps.get("displacementMap")
    k = float(mat.p["displacementScale"])
    if arr is None or k == 0 or uv is None:
        return pos, nrm
    uvs = np.asarray(uv, np.float64) * [mat.p["uvScaleX"], mat.p["uvScaleY"]]
    h = sample_map(arr, uvs, mat.p.get('mapSamplers', {}).get('displacementMap'))[:, 0]
    p2 = np.asarray(pos, np.float64) + np.asarray(nrm, np.float64) * (h * k)[:, None]
    return p2.astype(np.float32), G.compute_normals(p2, np.asarray(idx).reshape(-1, 3))


def _normal_tangents(item, mat, pos, nrm, uvs):
    """Normal-map frames use the texture's coordinate convention, before image-origin conversion."""
    tangent = item.tangents
    if pos is not item.positions and getattr(item, 'mode', 4) == 4 and item.uvs is not None:
        tangent = G.compute_tangents(pos, nrm, item.uvs, np.asarray(item.indices).reshape(-1, 3))
    if mat.p.get('surfaceModel') == 'gltf' and tangent is not None:
        # Authored tangents define the shared glTF normal/coat/anisotropy frame.
        # A texture-coordinate transform changes image lookup, not that frame.
        return tangent
    uv = uvs.get('normalMap')
    if getattr(item, 'mode', 4) != 4 or 'normalMap' not in mat.maps or uv is None or mat.p.get('normalSpace') == 'world':
        return tangent
    spec = mat.p.get('mapUV', {}).get('normalMap', {})
    usd = mat.p.get('surfaceModel') == 'usdPreviewSurface'
    if 'normalMapScale' in mat.p or mat.p.get('normalTangentGeometry'):
        # MaterialX normalmap defaults use the geometry's frame, independently of
        # transforms on its image coordinates. Its geometric UV convention is +V up.
        if tangent is not None:
            tangent = np.array(tangent, copy=True)
            tangent[:, 3] *= -1  # Preserve authored T while reversing the model's +V-down convention.
            return tangent
        uv = np.asarray(item.uvs)*[1., -1.] if item.uvs is not None else None
        return G.compute_tangents(pos, nrm, uv, np.asarray(item.indices).reshape(-1, 3)) if uv is not None else tangent
    if usd:
        if 'constant' in spec or ('fallback' in spec and spec.get('texCoord') not in getattr(item, 'uv_sets', {})):
            uv = item.uvs
        if uv is not None:
            uv = np.asarray(uv) * [1., -1.]  # USD's tangent +V points up the image.
    if uv is not None and (usd or tangent is None or spec or pos is not item.positions):
        tangent = G.compute_tangents(pos, nrm, uv, np.asarray(item.indices).reshape(-1, 3))
    return tangent


def _gpu(rc, key, build):
    ck = ("gpu-geo", key)
    hit = rc.cache.get(ck)
    if hit is None:
        hit = build()
        rc.cache[ck] = hit
    return hit


def _clip_time(rc, el, ctx, clip) -> float:
    ev = rc.ev
    t = (ctx.t - ctx.node_start) * ev.num(el, "animationSpeed", ctx, 1.0) + ev.num(el, "animationOffset", ctx, 0.0)
    d = clip.duration if clip is not None else 0.0
    if d > 1e-9:
        t = math.fmod(t, d)
        if t < 0:
            t += d
    return t


def _numlist(rc, el, prop, ctx) -> list | None:
    v = rc.ev.get(el, prop, ctx, None)
    if v is None:
        return None
    if isinstance(v, (tuple, list)):
        return [float(x) for x in v]
    if isinstance(v, (int, float)):
        return [float(v)]
    try:
        return [float(x) for x in str(v).replace(",", " ").split()]
    except ValueError:
        return None


def _mesh_items(rc, el, ctx, obj_mat: Material | None, ctx_gl):
    """[(GPUItem, Material, static)], splats, local bounds for a mesh object."""
    from .loaders import load_model
    mid = rc.ev.str(el, "mesh", ctx)
    asset = rc.doc.ids.get(mid) if mid else None
    if asset is None:
        warn_once("object3D", el.get("id", "?"), "primitive=mesh without a valid @mesh asset")
        return [], None, None
    path = rc.doc.resolve_path(rc.ev.str(asset, "src", ctx))
    try:
        model = load_model(path, rc.ev.str(asset, "format", ctx))
    except Exception as e:  # noqa: BLE001
        warn_once("mesh", path, f"not loaded: {e}")
        return [], None, None
    clip_name = rc.ev.str(el, "animationClip", ctx, None)
    clip = model.clip(clip_name) if clip_name else None
    if clip_name and clip is None:
        warn_once("object3D", f"{el.get('id')}:{clip_name}", "animationClip not found in the model")
    ct = _clip_time(rc, el, ctx, clip) if clip is not None else 0.0
    morph = _numlist(rc, el, "morphWeights", ctx)
    variant = rc.ev.str(el, "materialVariant", ctx, None)
    if variant and variant not in model.variants:
        warn_once("object3D", f"{el.get('id')}:{variant}", "materialVariant not in the model")
    out = []
    lo, hi = [], []
    for it in model.pose(clip, ct, morph, variant):
        m = obj_mat if obj_mat is not None else _spec_material(rc, it.material)
        # Map UVs and tangents are only needed to (re)upload geometry: a static item cached on the GPU
        # skips them (they used to be recomputed every frame just to be discarded).
        uvs = map_uvs(it, m) if "displacementMap" in m.maps else None
        if uvs is not None:
            pos, nrm = _displace(it.positions, it.normals, uvs.get("displacementMap"), it.indices, m)
        else:
            pos, nrm = it.positions, it.normals

        def build(it=it, m=m, pos=pos, nrm=nrm, uvs=uvs):
            u = uvs if uvs is not None else map_uvs(it, m)
            tan = _normal_tangents(it, m, pos, nrm, u)
            return R.upload(ctx_gl, pos, nrm, it.uvs, tan, it.colors, it.indices, u, it.mode)
        static = it.static and pos is it.positions
        item = _gpu(rc, ("mesh",) + tuple(it.key) + (path, m.key), build) if static else build()
        out.append((item, m, static))
        if len(pos):
            b = it.__dict__.get("_bounds") if static else None
            if b is None:
                b = (np.min(pos, 0), np.max(pos, 0))
                if static:
                    it.__dict__["_bounds"] = b       # rest-pose items are memoised (Model.pose)
            lo.append(b[0])
            hi.append(b[1])
    sp = model.splats
    if sp is not None and len(sp.positions):          # COLMAP axes (y down, z forward): E-flipped, see build_object
        b = sp.__dict__.get("_bounds")
        if b is None:
            b = sp.__dict__["_bounds"] = (sp.positions.min(0), sp.positions.max(0))   # loaded splats are immutable
        slo, shi = b
        lo.append(np.array([slo[0], -shi[1], -shi[2]]))
        hi.append(np.array([shi[0], -slo[1], -slo[2]]))
    bounds = (np.min(lo, 0), np.max(hi, 0)) if lo else None
    return out, sp, bounds


def _spec_material(rc, spec) -> Material:
    if spec is None:
        return default_material()
    if spec.dynamic:
        return from_spec(spec)
    key = ("spec-mat", id(spec))
    hit = rc.cache.get(key)
    if hit is None or hit[0] is not spec:
        hit = (spec, from_spec(spec))
        rc.cache[key] = hit
    return hit[1]


def _instances(rc, el, ctx) -> np.ndarray:
    """World matrices of an object3D's copies: copy i is world3d evaluated with index = i and
    count = N (CONVENTIONS 5.2). There is no implicit layout: copies whose transforms are equal
    coincide."""
    n = max(1, int(rc.ev.num(el, "instances", ctx, 1)))
    if n == 1:
        return world3d(rc, el, ctx)[None]
    mats = _instance_worlds(rc, el, ctx, n)
    if mats is None:
        mats = np.stack([world3d(rc, el, ctx.with_vars(index=float(i), count=float(n))) for i in range(n)])
    return mats


_INSTANCE_PROPS = (("x", 0.0), ("y", 0.0), ("z", 0.0), ("rotation", 0.0), ("rotationY", 0.0), ("rotationX", 0.0),
                   ("scaleX", 1.0), ("scaleY", 1.0), ("scaleZ", 1.0))


def _instance_worlds(rc, el, ctx, n: int) -> np.ndarray | None:
    """world3d of every copy of an instanced object3D at once: its expressions evaluated for all
    indices in one pass (vexpr), the matrices built as camera._local3d and world3d build them. None
    when that does not apply (an expression outside vexpr's subset, a property driven by keys as well
    as an expression, constraints, a parent whose clock is unknown, ...): world3d per copy then."""
    from .. import vexpr
    from ..camera import frame_size
    from ..evaluator import _jsval
    ev = rc.ev
    if ln(el) != "object3D" or ctx.vars or ctx.scope.overrides or any(ln(c) == "transformConstraint" for c in el):
        return None
    c0 = ctx.with_vars(index=0.0, count=float(n))
    vals = []
    for prop, default in _INSTANCE_PROPS:
        anims = ev._anims(el, prop)
        exprs = [a for a in anims if ln(a) == "expression"]
        if not exprs:
            vals.append(ev.num(el, prop, c0, default))      # the same for every copy (see _local3d)
            continue
        a = exprs[0]
        if len(anims) != 1 or a.get("property") != prop or a.get("enabled", "true") not in ("true", "1"):
            return None
        fn = vexpr.compile(a.text or "")
        base = _jsval(ev.base(el, prop, c0, default))
        if fn is None or base is not None and type(base) is not float:
            return None
        v = fn({"time": c0.t, "frame": float(c0.frame), "index": np.arange(n, dtype=np.float64), "count": float(n),
                "seed": float(ev.expression_seed(a.get("seed"))), "value": base}, n)
        if v is None:
            return None
        vals.append(v)
    x, y, z, rot, rot_y, rot_x, sx, sy, sz = (np.broadcast_to(np.asarray(v, np.float64), (n,)) for v in vals)
    t = np.stack([x, -y, -z], 1)
    pid = ev.str(el, "parent", c0)
    par = rc.doc.ids.get(pid) if pid else None
    if par is el:
        par = None
    if par is None:
        W, H = frame_size(rc)
        t = t + np.array([-W / 2, H / 2, 0.0])
    M = np.zeros((n, 4, 4))
    M[:, :3, :3] = (_rot(rot, "z", -1) @ _rot(rot_y, "y", -1) @ _rot(rot_x, "x", 1)) * np.stack([sx, sy, sz], 1)[:, None, :]
    M[:, :3, 3] = t
    M[:, 3, 3] = 1.0
    if par is not None:
        pc = node_clock(rc, par, ctx.comp_t)
        if pc is None:
            return None
        M = world3d(rc, par, pc)[None] @ M
    return M


def _rot(deg: np.ndarray, axis: str, sign: int) -> np.ndarray:
    """camera.rx / ry / rz of sign * deg for every angle, (n, 3, 3)."""
    a = np.radians(sign * deg)
    c, s = np.cos(a), np.sin(a)
    o, z = np.ones_like(a), np.zeros_like(a)
    rows = {"x": ((o, z, z), (z, c, -s), (z, s, c)), "y": ((c, z, s), (z, o, z), (-s, z, c)),
            "z": ((c, -s, z), (s, c, z), (z, z, o))}[axis]
    return np.stack([np.stack(r, -1) for r in rows], -2)


_SPLAT_MEMO: dict = {}


def _splat_gpu(sp, M: np.ndarray) -> R.SplatGPU:
    """Splat centres/covariances in engine space; the last result per splat set is reused while its
    world matrix is unchanged (static splat objects: every frame and shutter sample)."""
    key = id(sp)
    hit = _SPLAT_MEMO.get(key)
    if hit is not None and hit[0] is sp and np.array_equal(hit[1], M):
        return hit[2]
    out = _splat_gpu_compute(sp, M)
    _SPLAT_MEMO.clear()               # one splat set at a time keeps memory bounded
    _SPLAT_MEMO[key] = (sp, np.array(M, copy=True), out)
    return out


def _splat_gpu_compute(sp, M: np.ndarray) -> R.SplatGPU:
    from ..camera import quat_to_mat
    A = M[:3, :3]
    centers = sp.positions @ A.T + M[:3, 3]
    q = sp.rotations / np.maximum(np.linalg.norm(sp.rotations, axis=1, keepdims=True), 1e-12)
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    Rm = np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
                   np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
                   np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], 1)
    Msr = A[None] @ Rm * sp.scales[:, None, :]
    C = Msr @ np.transpose(Msr, (0, 2, 1))
    cov = np.stack([C[:, 0, 0], C[:, 0, 1], C[:, 0, 2], C[:, 1, 1], C[:, 1, 2], C[:, 2, 2]], -1)
    del quat_to_mat
    return R.SplatGPU(centers.astype(np.float32), sp.colors.astype(np.float32), cov.astype(np.float32))


def pixels_per_metre(rc) -> float:
    """physics/@pixelsPerMeter (default 100): scene units per model metre."""
    ph = rc.doc.section("physics")
    try:
        k = float(ph.get("pixelsPerMeter", 100)) if ph is not None else 100.0
    except (TypeError, ValueError):
        k = 100.0
    return k if k > 0 else 100.0


_E4 = np.diag([1.0, -1.0, -1.0, 1.0])


def _object_items(rc, el, ctx: Ctx, ctx_gl):
    """(items, temp, splats, bounds) of one object3D evaluated at ctx; None when it draws nothing."""
    prim = rc.ev.str(el, "primitive", ctx)
    obj_mat = material_for(rc, el, ctx)
    items, temp, splats, bounds = [], [], None, None
    if prim in PRIMS:
        key, g = primitive_geo(rc, el, ctx)
        if g is None:
            return None
        m = obj_mat or default_material()
        uvs = map_uvs(g, m)
        pos, nrm = _displace(g.positions, g.normals, uvs.get('displacementMap'), g.indices, m)
        tan = _normal_tangents(g, m, pos, nrm, uvs)
        dkey = key + ('mapGeometry', str(m.p.get('mapUV', {})), m.p.get('normalSpace'),
                      m.p.get('surfaceModel'), m.p.get('normalTangentGeometry'),
                      id(m.maps.get('displacementMap')), m.p['displacementScale'],
                      m.p['uvScaleX'], m.p['uvScaleY'], str(m.p.get('mapSamplers', {}).get('displacementMap')))
        item = _gpu(rc, dkey, lambda: R.upload(ctx_gl, pos, nrm, g.uvs, tan, None, g.indices, uvs))
        items.append((item, m))
        bounds = (pos.min(0), pos.max(0))
    elif prim == "mesh":
        got, sp, bounds = _mesh_items(rc, el, ctx, obj_mat, ctx_gl)
        for item, m, static in got:
            items.append((item, m))
            if not static:
                temp.append(item)
        if sp is not None:
            splats = sp
    else:
        warn_once("object3D", str(prim), "unknown primitive")
        return None
    if not items and splats is None:
        return None
    return items, temp, splats, bounds


_COPY_SHARED = {name for name, _ in (("x", 0), ("y", 0), ("z", 0), ("rotation", 0), ("rotationY", 0), ("rotationX", 0),
                                     ("scaleX", 1), ("scaleY", 1), ("scaleZ", 1))} | {"instances", "opacity"}


def _copies_differ(rc, el, ctx: Ctx) -> bool:
    """Can an instanced object3D's copies differ in more than their transform? Only expressions see
    index / count, so: an expression on another of its properties (geometry, clip time, ...), or on
    a property of its material."""
    def exprs(e):
        return any(isinstance(c.tag, str) and ln(c) == "expression" and c.get("property") not in _COPY_SHARED
                   for c in e)
    if exprs(el):
        return True
    mid = rc.ev.str(el, "material", ctx)
    mat = rc.doc.ids.get(mid) if mid else None
    return mat is not None and any(isinstance(c.tag, str) and ln(c) == "expression" for c in mat)


def build_object(rc, el, ctx: Ctx, ctx_gl) -> R.ObjDraw | None:
    """The draw of an object3D and all its copies (CONVENTIONS 5.2). Copies that differ only in their
    transform are GPU instances of one set of items; otherwise each copy's items are evaluated with
    its own index and drawn with its own matrix (ObjDraw.item_inst)."""
    ev = rc.ev
    prim = ev.str(el, "primitive", ctx)
    n = max(1, int(ev.num(el, "instances", ctx, 1)))
    k = pixels_per_metre(rc) if prim == "mesh" else 1.0
    # Imported models are Y-up metres: scene point = ppm . E . g (CONVENTIONS 2.6), and the engine
    # frame is E . scene, so in engine space the model is only scaled by ppm (E . E = I). Applied
    # here, not in world3d, so @parent children do not inherit it.
    ppm = np.diag([k, k, k, 1.0])
    item_inst = None
    if n > 1 and _copies_differ(rc, el, ctx):
        items, temp, splats, boxes, item_inst, inst = [], [], None, [], [], []
        for i in range(n):
            ci = ctx.with_vars(index=float(i), count=float(n))
            got = _object_items(rc, el, ci, ctx_gl)
            if got is None:
                continue
            its, tmp, sp, b = got
            Mi = world3d(rc, el, ci) @ ppm
            items += its
            temp += tmp
            splats = splats if splats is not None else sp
            inst.append(Mi)
            if b is not None:
                boxes.append((b, Mi))
            buf = ctx_gl.buffer(np.ascontiguousarray(Mi.T.reshape(1, 16), np.float32).tobytes())
            temp.append(buf)
            item_inst += [(buf, 1)] * len(its)
        if not inst:
            return None
        inst = np.stack(inst)
    else:
        got = _object_items(rc, el, ctx, ctx_gl)
        if got is None:
            return None
        items, temp, splats, bounds = got
        inst = _instances(rc, el, ctx) @ ppm
        boxes = [(bounds, M) for M in inst] if bounds is not None else []
    corners = []
    for (lo, hi), M in boxes or [((np.zeros(3), np.zeros(3)), M) for M in inst]:
        c = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        corners.append(c @ M.T)
    wc = np.concatenate(corners)[:, :3]
    center = wc.mean(0)
    radius = float(np.max(np.linalg.norm(wc - center, axis=1))) if len(wc) else 0.0
    A = inst[0][:3, :3]
    mscale = abs(float(np.linalg.det(A))) ** (1 / 3)
    opacity = ev.num(el, "opacity", ctx, 1.0)
    mats = [m for _, m in items]
    occ = opacity >= 0.999 and all(m.opaque_occluder for m in mats) and splats is None
    cast = ev.bool(el, "castShadow", ctx, True) and (all(m.casts_shadow for m in mats) if mats else False)
    o = R.ObjDraw(el, items, inst, cast, ev.bool(el, "receiveShadow", ctx, True), occ, center, radius, mscale,
                  temp=temp, item_inst=item_inst)
    o.inst_buf = ctx_gl.buffer(np.ascontiguousarray(np.transpose(inst, (0, 2, 1)).reshape(len(inst), 16),
                                                    np.float32).tobytes())
    if splats is not None:
        o.splats = _splat_gpu(splats, inst[0] @ _E4)       # splat files already use scene axes: engine = E . scene
    return o


# ------------------------------------------------------------------ frame
def overscan(cam: Camera) -> float:
    """Document-pixel margin rendered around the frame so barrel distortion (k1 < 0) has content to
    pull in: (r_u(1) - 1) * half diagonal, r_u solving r_u (1 + k1 r_u^2) = 1."""
    k = cam.lens_k
    if k >= 0:
        return 0.0
    ru = 1.0
    for _ in range(20):
        f = ru * (1 + k * ru * ru) - 1
        fp = 1 + 3 * k * ru * ru
        if fp <= 1e-6:
            ru = 1 / math.sqrt(-3 * k)          # turning point of the barrel curve
            break
        ru -= f / fp
    return max(0.0, ru - 1) * math.hypot(cam.W, cam.H) / 2 + 2


def _render_size(rc, cam: Camera):
    """(pw, ph, ssaa, ox, oy, frame scale, view rect) of the 3D render target."""
    Rm = rc.root_matrix
    if rc.scene_matrix is not None:
        density = max(float(np.linalg.svd(rc.scene_matrix[:2, :2], compute_uv=False).max()), 1e-6)
        Rm = np.diag([density, density, 1.])
    sx, sy = abs(Rm[0, 0]), abs(Rm[1, 1])
    n = int(rc.doc.project.get("antialias3d", 1) or 1)
    n = max(1, min(4, n))
    m = overscan(cam)
    pw, ph = max(1, round((cam.W + 2 * m) * sx)), max(1, round((cam.H + 2 * m) * sy))
    while n > 1 and (pw * n * ph * n > 32_000_000 or max(pw, ph) * n > 16384):
        n -= 1
    view = (-m, -m, pw / sx, ph / sy)
    return pw * n, ph * n, n, Rm[0, 2] - m * sx, Rm[1, 2] - m * sy, sx, view


def world3d_at(rc, t: float, root, base: Ctx | None = None) -> R.World3D:
    """Camera-independent 3D state of objects under `root` at composition time t (cached per frame)."""
    key = ("world3d", root, t, base)
    hit = rc.frame_cache.get(key)
    if hit is not None:
        return hit
    r = R.res()
    ctx_gl = r.ctx
    live = rc.cache.setdefault("world3d-live", [])
    while live and (live[0][0] is not rc.frame_cache or len(live) > 2):
        old_fc, old_key, old = live.pop(0)          # oldest first; drop it from its cache too
        old_fc.pop(old_key, None)
        old.release()
    objs = {}
    for el in objects_in(rc, root):
        c = node_clock(rc, el, t, base if root is not None and ln(root) == "symbol" else None)
        if c is None or rc.ev.num(el, "opacity", c, 1.0) <= 1e-4:
            continue
        o = build_object(rc, el, c, ctx_gl)
        if o is not None:
            objs[el] = o
    lights = eval_lights(rc, t)
    if not lights:
        lights = default_lights()
    env = R.build_env(r, lights)
    w = R.World3D(objs, lights, {}, env)
    casters = [o for o in objs.values() if o.cast]
    if casters:
        cs = np.array([o.center for o in casters])
        c0 = cs.mean(0)
        rad = max(float(np.linalg.norm(o.center - c0)) + o.radius for o in casters)
        tex_cache = rc.cache.setdefault("gl-tex", {})
        for i, L in enumerate(lights):
            if L.shadow and L.kind != "dome":
                sm = R.render_shadow(r, list(objs.values()), L, (c0, rad), rc.scale, tex_cache)
                if sm is not None:
                    w.shadows[i] = sm
        for i, light, entry, _ in env.domes:
            if not light.shadow or entry.dominance <= 1e-3 or not (light.diffuse or light.specular):
                continue
            spread = .5 * math.tan(math.acos(min(1., max(0., entry.dominance))))
            softness = light.softness if light.softness > 0 else math.degrees(math.atan(spread))
            Ld = replace(light, kind='directional', pos=np.zeros(3), right=np.array([1., 0., 0.]),
                         up=np.array([0., 1., 0.]), fwd=-entry.shadow_dir, softness=softness)
            sm = R.render_shadow(r, list(objs.values()), Ld, (c0, rad), rc.scale, tex_cache)
            if sm is not None:
                w.shadows[i] = sm
    live.append((rc.frame_cache, key, w))
    rc.frame_cache[key] = w
    return w


def frame3d(rc, t: float, root, base: Ctx | None = None) -> R.Frame3D:
    """The shared 3D frame (world + active camera + render size) at composition time t."""
    cam = camera_at(rc, t)
    ck = (cam.eye.tobytes(), cam.fwd.tobytes(), cam.up.tobytes(), cam.fpx, cam.ortho, cam.k_ortho, cam.W, cam.H,
          cam.cx, cam.cy, cam.exposure, cam.dof, cam.focus)
    key = ("frame3d", root, t, ck, base, rc.root_matrix.tobytes(),
           None if rc.scene_matrix is None else rc.scene_matrix.tobytes())
    hit = rc.frame_cache.get(key)
    if hit is not None:
        return hit
    world = world3d_at(rc, t, root, base)
    pw, ph, n, ox, oy, fs, view = _render_size(rc, cam)
    live = rc.cache.setdefault("frame3d-live", [])
    while live and (live[0][0] is not rc.frame_cache or len(live) > 8):
        old_fc, old_key, old = live.pop(0)
        old_fc.pop(old_key, None)
        old.release()
    fr = R.Frame3D(cam, pw, ph, ox, oy, n, world, frame_scale=fs, view=view)
    live.append((rc.frame_cache, key, fr))
    rc.frame_cache[key] = fr
    return fr


def default_lights():
    """No <lights>: a neutral rig — ambient 0.35 plus a directional key from upper-left-front whose
    irradiance pi * 0.65 makes a white Lambertian surface facing it reach 1.0."""
    from .lights import LightState
    d = -np.array([-0.45, 0.7, 0.55]) / np.linalg.norm([-0.45, 0.7, 0.55])
    amb = LightState(None, "ambient", np.full(3, 0.35), np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]),
                     np.array([0, 0, -1.0]))
    key = LightState(None, "directional", np.full(3, math.pi * 0.65), np.zeros(3), np.array([1.0, 0, 0]),
                     np.array([0, 1.0, 0]), d)
    return [amb, key]


# ------------------------------------------------------------------ layer post-processing
def _downsample(a: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return a
    h, w = a.shape[0] // n, a.shape[1] // n
    return a[:h * n, :w * n].reshape(h, n, w, n, -1).mean((1, 3))


def finish_layer(rc, fr: R.Frame3D, col: np.ndarray, dep: np.ndarray | None, at=(0, 0)) -> Buf | None:
    """Downsample, crop, depth of field, lens distortion and working-space conversion of a layer read
    back at render-target pixel offset `at`."""
    from .postfx import bokeh_blur
    # col / dep arrive already reduced by the SSAA factor (renderer._read does it on the GPU); `at`
    # is still in render-target pixels.
    col = np.asarray(col, np.float32)
    ax, ay = at[0] // fr.ssaa, at[1] // fr.ssaa
    a = col[..., 3]
    cov = a > 1e-5
    rows = np.flatnonzero(cov.any(1))
    if len(rows) == 0:
        return None
    cols_ = np.flatnonzero(cov.any(0))
    ys, xs = rows[[0, -1]], cols_[[0, -1]]
    cam = fr.cam
    coc = None
    if dep is not None and cam.dof and not cam.ortho:
        depth = dep[..., 0] / np.maximum(dep[..., 3], 1e-6)
        coc = np.where(a > 1e-5, cam.coc_px(np.maximum(depth, cam.near)) * fr.frame_scale, 0.0)
    pad = 2 + (int(math.ceil(min(float(coc.max()), 256.0))) if coc is not None else 0)
    y0, y1 = max(0, ys.min() - pad), min(col.shape[0], ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(col.shape[1], xs.max() + 1 + pad)
    px = col[y0:y1, x0:x1]
    if coc is not None and float(coc.max()) >= 0.5:
        px = np.pad(px, ((pad, pad), (pad, pad), (0, 0)))
        cc = np.pad(coc[y0:y1, x0:x1], pad)
        px = bokeh_blur(px, cc, cam.blades)
        y0 -= pad
        x0 -= pad
    buf = Buf(np.ascontiguousarray(px, np.float32), int(round(fr.ox)) + ax + x0, int(round(fr.oy)) + ay + y0)
    if rc.scene_matrix is not None:
        buf = warp_projective(buf, rc.scene_matrix @ np.diag([1 / fr.frame_scale, 1 / fr.frame_scale, 1.]), rc.frame_rect)
        if buf is None:
            return None
    if abs(cam.lens_k) > 1e-9:
        from ..camera import lens_distort
        buf = lens_distort(rc, buf, cam)
    if not rc.linear:
        p = buf.px
        al = p[..., 3:4]
        straight = np.where(al > 1e-6, p[..., :3] / np.maximum(al, 1e-6), 0.0)
        p = np.concatenate([linear_to_srgb(np.clip(straight, 0, 1)) * al, np.clip(al, 0, 1)], -1)
        buf = Buf(np.ascontiguousarray(p, np.float32), buf.x0, buf.y0)
    return buf


def render_node_layer(rc, el, ctx: Ctx) -> Buf | None:
    root = scene_root(el)
    base = ((rc.scene_context[1] if rc.scene_context is not None and rc.scene_context[0] is root else ctx)
            if root is not None and ln(root) == "symbol" else None)
    fr = frame3d(rc, ctx.comp_t, root, base)
    obj = fr.objects.get(el)
    if obj is None:
        return None
    r = R.res()
    gpu = _gpu_layer(rc, fr)
    if fr.backdrop is None:
        fr.backdrop = rc.frame_cache.get("3d-backdrop")     # what transmissive surfaces refract (5.24)
        fr.linear = rc.linear
    got = R.render_layer(r, fr, obj, rc.cache.setdefault("gl-tex", {}), depth=fr.cam.dof and not fr.cam.ortho, gpu=gpu)
    if got is None:
        return None
    col, dep, at = got
    if gpu:
        return _gpu_buf(fr, col, at)
    return finish_layer(rc, fr, col, dep, at)


def render_background_layer(rc, t: float, root) -> Buf | None:
    fr = frame3d(rc, t, root)
    gpu = _gpu_layer(rc, fr)
    col = R.render_background(R.res(), fr, gpu=gpu)
    if col is None:
        return None
    if gpu:
        return _gpu_buf(fr, col, (0, 0))
    return finish_layer(rc, fr, col, None)


def _gpu_layer(rc, fr) -> bool:
    """Can the layer stay on the GPU? When the frame composites there (gpucomp) and finish_layer would
    only crop it: no depth of field, 2.5D scene matrix or lens distortion, and a linear working space."""
    cam = fr.cam
    return (rc._gpu_frame and rc.linear and rc.scene_matrix is None and abs(cam.lens_k) <= 1e-9
            and not (cam.dof and not cam.ortho))


def _gpu_buf(fr, tile, at) -> Buf:
    """finish_layer for a GPU layer: placed like a read-back one, uncropped (its transparent pixels
    composite to nothing)."""
    return Buf(None, int(round(fr.ox)) + at[0] // fr.ssaa, int(round(fr.oy)) + at[1] // fr.ssaa, gpu=tile)


def gl_ok() -> bool:
    try:
        R.res()
        return True
    except (gl.GLUnavailable, Exception) as e:  # noqa: BLE001 — shader/driver problems fall back to CPU
        warn_once("object3D", "gl", f"3D renderer unavailable ({e}); using the flat CPU fallback")
        return False


# ------------------------------------------------------------------ mesh assets as 2D layer content
THUMB_FOV = 30.0


def mesh_thumbnail(rc, asset, w: int, h: int, t: float) -> np.ndarray | None:
    """Premultiplied linear (h, w, 4) render of a mesh asset (see assets/mesh.py for the pinned view)."""
    from .loaders import load_model
    try:
        model = load_model(rc.doc.resolve_path(asset.get("src")), asset.get("format"))
    except Exception as e:  # noqa: BLE001
        warn_once("mesh", asset.get("src", "?"), f"not loaded: {e}")
        return None
    clip = model.clips[0] if model.clips else None
    ct = math.fmod(t, clip.duration) if clip is not None and clip.duration > 1e-9 else 0.0
    if ct < 0 and clip is not None:
        ct += clip.duration
    r = R.res()
    ctx_gl = r.ctx
    lo, hi = model.bounds()
    c = (lo + hi) / 2
    rad = max(float(np.linalg.norm(hi - lo)) / 2, 1e-6)
    items, temp = [], []
    for it in model.pose(clip, ct):
        mat = _spec_material(rc, it.material)
        uvs = map_uvs(it, mat)
        pos, nrm = _displace(it.positions, it.normals, uvs.get("displacementMap"), it.indices, mat)
        tan = _normal_tangents(it, mat, pos, nrm, uvs)
        g = R.upload(ctx_gl, pos, nrm, it.uvs, tan, it.colors, it.indices, uvs, it.mode)
        items.append((g, mat))
        temp.append(g)
    inst = np.eye(4)[None]
    o = R.ObjDraw(asset, items, inst, False, False, True, c, rad, 1.0, temp=temp)
    o.inst_buf = ctx_gl.buffer(np.eye(4, dtype=np.float32).tobytes())
    if model.splats is not None:
        o.splats = _splat_gpu(model.splats, np.eye(4))
    world = R.World3D({asset: o}, default_lights(), {}, R.build_env(r, default_lights()))
    half_v = math.radians(THUMB_FOV) / 2
    fpx = (h / 2) / math.tan(half_v)
    half = half_v if w >= h else math.atan(math.tan(half_v) * w / h)
    dist = rad / math.sin(half)
    near = max(dist - rad * 2, dist * 1e-3)
    cam = Camera(c + np.array([0, 0, dist]), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, -1.0]),
                 fpx, False, 1.0, float(w), float(h), near, None, far=dist + rad * 2)
    fr = R.Frame3D(cam, w, h, 0.0, 0.0, 1, world, frame_scale=1.0)
    try:
        got = R.render_layer(r, fr, o, rc.cache.setdefault("gl-tex", {}), depth=False)
        col = None
        if got is not None:
            col = np.zeros((h, w, 4), np.float32)
            c, _, (x0, y0) = got
            col[y0:y0 + c.shape[0], x0:x0 + c.shape[1]] = c
    finally:
        fr.release()
        world.release()
    return col
