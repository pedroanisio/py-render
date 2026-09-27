"""mesh assets used as 2D layer content: a 3D thumbnail of the model.

Pinned (the schema only says a mesh asset is a 3D model): a <layer> whose asset is a mesh draws the
model's first animation clip at the layer's media time (clipIn, speed, loop, timeRemap apply; the
clip loops), framed by a 30-degree perspective camera on +Z looking down -Z at the model's bounding
sphere so that it fits the layer box, lit by the default light rig (ambient 0.35 + key light). The
intrinsic size is 512 x 512 document pixels (boxWidth/boxHeight or fit change it). For placing a
model in the scene with the camera, lights and shadows use <object3D primitive="mesh">.
"""
from __future__ import annotations

import math

import numpy as np

from ..raster import Buf, linear_to_srgb, warp_projective
from ..registry import ASSET_SIZES, ASSETS, FULL

SIZE = 512.0


@ASSET_SIZES.register("mesh")
def mesh_size(rc, asset, ctx):
    return SIZE, SIZE


@ASSETS.register("mesh", level=FULL, note="3D thumbnail of the model (first clip at media time); object3D for scenes")
def render_mesh(rc, asset, M, ctx, *, layer=None, src_t=0.0, clip=None):
    from ..three import scene
    if not scene.gl_ok():
        return None
    k = math.sqrt(abs(M[0, 0] * M[1, 1] - M[0, 1] * M[1, 0])) or 1.0
    n = max(8, min(4096, int(math.ceil(SIZE * k))))
    col = scene.mesh_thumbnail(rc, asset, n, n, src_t)
    if col is None:
        return None
    if not rc.linear:
        a = col[..., 3:4]
        st = np.where(a > 1e-6, col[..., :3] / np.maximum(a, 1e-6), 0)
        col = np.concatenate([linear_to_srgb(np.clip(st, 0, 1)) * a, a], -1).astype(np.float32)
    if clip is not None:
        x0, y0, x1, y1 = (int(round(v * n / SIZE)) for v in clip)
        col = col.copy()
        col[:max(0, y0)] = 0
        col[max(0, y1):] = 0
        col[:, :max(0, x0)] = 0
        col[:, max(0, x1):] = 0
    tile = Buf(np.ascontiguousarray(col, np.float32), 0, 0)
    S = np.diag([n / SIZE, n / SIZE, 1.0])
    return warp_projective(tile, M @ np.linalg.inv(S), rc.frame_rect)
