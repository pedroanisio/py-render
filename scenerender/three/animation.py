"""Posing a `Model`: keyframe sampling, node hierarchy, morph targets and linear blend skinning.

Pinned decisions (glTF 2.0 semantics for every source format):
  * Matrices are float64 4x4 for column vectors (p' = M @ [p, 1]); `Node.matrix` is stored in that
    math layout (a glTF column-major list reshaped to (4, 4) and transposed). A node with an explicit
    matrix is not animated (glTF forbids TRS channels on matrix nodes); channels on it are ignored.
  * world(node) = world(parent) @ local(node); nodes not reachable from `model.roots` still get a
    world matrix (joints may live outside the scene roots) but never emit draw items.
  * Sampling clamps to the first/last key. LINEAR rotation = shortest-path slerp, normalised;
    STEP holds the previous key; CUBICSPLINE reads (in-tangent, value, out-tangent) triplets, scales
    tangents by the key interval and normalises quaternion results.
  * Morph weights: `morph_override` (padded with zeros / truncated to the target count) > animated
    "weights" channel > `node.weights` (loaders copy glTF mesh.weights there) > zeros. Deltas are
    applied to positions and normals before skinning; missing normals are computed after morphing as
    area-weighted smooth vertex normals.
  * Skinning: joint matrix = world(joint) @ inverse_bind; vertices end in model space and the mesh
    node's own transform is ignored. Normals use the cofactor (inverse-transpose up to scale) of the
    blended / world 3x3, renormalised; tangents use the 3x3 itself and keep their w.
  * DrawItem.key = (id(model), node index, primitive index). DrawItem.static is False when any clip
    channel targets the node, one of its ancestors or (for skinned meshes) a joint or a joint's
    ancestor, or when morph weights are animated or overridden.
"""
from __future__ import annotations

import numpy as np

from .model import Channel, Clip, DrawItem, Model


def quat_to_mat(q: np.ndarray) -> np.ndarray:
    """3x3 float64 rotation matrix of a quaternion given as (x, y, z, w); normalised first."""
    x, y, z, w = np.asarray(q, dtype=np.float64) / (np.linalg.norm(q) or 1.0)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def trs_matrix(t: np.ndarray, q: np.ndarray, s: np.ndarray) -> np.ndarray:
    """4x4 float64 T @ R @ S."""
    m = np.eye(4)
    m[:3, :3] = quat_to_mat(q) * np.asarray(s, dtype=np.float64)[None, :]
    m[:3, 3] = t
    return m


def slerp(a: np.ndarray, b: np.ndarray, u: float) -> np.ndarray:
    d = float(np.dot(a, b))
    if d < 0.0:
        b, d = -b, -d
    if d > 0.9995:
        r = a + (b - a) * u
    else:
        th = np.arccos(d)
        r = (np.sin((1 - u) * th) * a + np.sin(u * th) * b) / np.sin(th)
    return r / (np.linalg.norm(r) or 1.0)


def sample_channel(ch: Channel, t: float) -> np.ndarray:
    """Channel value at time t (float64 vector)."""
    times = np.asarray(ch.times, dtype=np.float64)
    k = len(times)
    cubic = ch.interpolation == "CUBICSPLINE"
    v = np.asarray(ch.values, dtype=np.float64).reshape(k, 3, -1) if cubic else \
        np.asarray(ch.values, dtype=np.float64).reshape(k, -1)
    val = (lambda i: v[i, 1]) if cubic else (lambda i: v[i])
    rot = ch.path == "rotation"
    if k == 1 or t <= times[0]:
        r = val(0)
    elif t >= times[-1]:
        r = val(k - 1)
    else:
        i = int(np.clip(np.searchsorted(times, t, side="right") - 1, 0, k - 2))
        dt = times[i + 1] - times[i]
        u = (t - times[i]) / dt if dt > 0 else 0.0
        if ch.interpolation == "STEP":
            r = val(i)
        elif cubic:
            u2, u3 = u * u, u * u * u
            r = ((2 * u3 - 3 * u2 + 1) * v[i, 1] + (u3 - 2 * u2 + u) * dt * v[i, 2]
                 + (-2 * u3 + 3 * u2) * v[i + 1, 1] + (u3 - u2) * dt * v[i + 1, 0])
        elif rot:
            return slerp(v[i], v[i + 1], u)
        else:
            r = v[i] + (v[i + 1] - v[i]) * u
    return r / (np.linalg.norm(r) or 1.0) if rot else r


def smooth_normals(pos: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Area-weighted smooth vertex normals (float64); isolated vertices get +Z."""
    n = np.zeros_like(pos, dtype=np.float64)
    if len(tris):
        p = pos[tris]
        fn = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        for c in range(3):
            np.add.at(n, tris[:, c], fn)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return np.where(ln > 1e-20, n / np.maximum(ln, 1e-20), np.array([0.0, 0.0, 1.0]))


def _cofactor(m: np.ndarray) -> np.ndarray:
    """Cofactor of (..., 3, 3), sign-corrected so it maps normals like the inverse-transpose."""
    r0, r1, r2 = m[..., 0, :], m[..., 1, :], m[..., 2, :]
    c = np.stack([np.cross(r1, r2), np.cross(r2, r0), np.cross(r0, r1)], axis=-2)
    det = np.einsum("...i,...i->...", r0, c[..., 0, :])
    return c * np.where(det < 0, -1.0, 1.0)[..., None, None]


def _unit(v: np.ndarray) -> np.ndarray:
    ln = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(ln > 1e-20, ln, 1.0)


def pose_model(model: Model, clip: Clip | None, t: float, morph_override: list | None = None,
               variant: str | None = None) -> list[DrawItem]:
    nodes = model.nodes
    anim: dict[tuple[int, str], np.ndarray] = {}
    touched: set[int] = set()
    for ch in clip.channels if clip is not None else ():
        if 0 <= ch.node < len(nodes) and len(ch.times):
            anim[(ch.node, ch.path)] = sample_channel(ch, t)
            if ch.path != "weights":
                touched.add(ch.node)
    parent = [-1] * len(nodes)
    for i, nd in enumerate(nodes):
        for c in nd.children:
            parent[c] = i
    world: list[np.ndarray | None] = [None] * len(nodes)
    dynamic = [False] * len(nodes)

    def world_of(i: int) -> np.ndarray:
        if world[i] is None:
            nd = nodes[i]
            if nd.matrix is not None:
                local = np.asarray(nd.matrix, dtype=np.float64)
            else:
                local = trs_matrix(anim.get((i, "translation"), nd.translation),
                                   anim.get((i, "rotation"), nd.rotation), anim.get((i, "scale"), nd.scale))
            p = parent[i]
            world[i] = local if p < 0 else world_of(p) @ local
            dynamic[i] = (i in touched and nd.matrix is None) or (p >= 0 and dynamic[p])
        return world[i]

    reachable: list[int] = []
    stack = list(reversed(model.roots))
    seen: set[int] = set()
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        reachable.append(i)
        stack.extend(reversed(nodes[i].children))

    items: list[DrawItem] = []
    for i in sorted(reachable):
        nd = nodes[i]
        if nd.mesh is None:
            continue
        W = world_of(i)
        prims = model.meshes[nd.mesh]
        n_targets = max((len(p.morph_positions) for p in prims), default=0)
        morph_dyn = n_targets > 0 and (morph_override is not None or (i, "weights") in anim)
        src = morph_override if morph_override is not None else anim.get((i, "weights"), nd.weights)
        w = np.zeros(n_targets)
        if src is not None:
            src = np.asarray(src, dtype=np.float64).ravel()[:n_targets]
            w[:len(src)] = src
        skin = model.skins[nd.skin] if nd.skin is not None and nd.skin < len(model.skins) else None
        joint_mats = None
        dyn = dynamic[i] or morph_dyn
        if skin is not None:
            joint_mats = np.stack([world_of(j) @ skin.inverse_bind[k] for k, j in enumerate(skin.joints)]) \
                if skin.joints else np.eye(4)[None]
            dyn = dyn or any(dynamic[j] for j in skin.joints)
        for pi, prim in enumerate(prims):
            pos = np.asarray(prim.positions, dtype=np.float64)
            nrm = None if prim.normals is None else np.asarray(prim.normals, dtype=np.float64)
            for k, wk in enumerate(w):
                if wk == 0.0:
                    continue
                if k < len(prim.morph_positions):
                    pos = pos + wk * prim.morph_positions[k]
                if nrm is not None and k < len(prim.morph_normals):
                    nrm = nrm + wk * prim.morph_normals[k]
            if nrm is None:
                nrm = smooth_normals(pos, prim.indices)
            tan = None if prim.tangents is None else np.asarray(prim.tangents, dtype=np.float64)
            if joint_mats is not None and prim.joints is not None and prim.weights is not None:
                jw = np.asarray(prim.weights, dtype=np.float64)
                ji = np.clip(np.asarray(prim.joints, dtype=np.int64), 0, len(joint_mats) - 1)
                M = np.einsum("vk,vkij->vij", jw, joint_mats[ji])
                lin = M[:, :3, :3]
                pos = np.einsum("vij,vj->vi", lin, pos) + M[:, :3, 3]
                nrm = np.einsum("vij,vj->vi", _cofactor(lin), nrm)
                if tan is not None:
                    tan = np.concatenate([np.einsum("vij,vj->vi", lin, tan[:, :3]), tan[:, 3:]], axis=1)
            else:
                lin = W[:3, :3]
                pos = pos @ lin.T + W[:3, 3]
                nrm = nrm @ _cofactor(lin).T
                if tan is not None:
                    tan = np.concatenate([tan[:, :3] @ lin.T, tan[:, 3:]], axis=1)
            if tan is not None:
                tan[:, :3] = _unit(tan[:, :3])
            mat = prim.variants.get(variant, prim.material) if variant is not None else prim.material
            items.append(DrawItem(
                positions=pos.astype(np.float32), normals=_unit(nrm).astype(np.float32),
                indices=np.asarray(prim.indices, dtype=np.uint32), uvs=prim.uvs,
                tangents=None if tan is None else tan.astype(np.float32), colors=prim.colors,
                material=mat, key=(id(model), i, pi), static=not dyn))
    return items
