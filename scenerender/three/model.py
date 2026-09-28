"""In-memory 3D model: geometry primitives, node hierarchy, skins, animation clips and splats.

Every loader (glTF/GLB, OBJ/PLY/STL, USD/USDZ, FBX, .splat) produces a `Model`; the renderer only
reads `Model.pose(...)`. Units are the file's own units, read as metres; coordinates are
right-handed, +Y up (glTF convention); loaders of Z-up formats convert. An object3D places every
mesh format by CONVENTIONS 2.6: model point g -> scene (ppm gx, -ppm gy, -ppm gz) with
ppm = physics/@pixelsPerMeter (default 100), i.e. engine space (+Y up) ppm . g, before its own
transform (three/scene.build_object). Splat positions keep the file's COLMAP axes (y down, z forward),
which already match scene space, and are only scaled by ppm.

Animation (glTF semantics, used for every format):
  * A clip is a set of channels (node, path in translation|rotation|scale|weights) with keyframe
    times (s), values and interpolation LINEAR (quaternions: slerp), STEP or CUBICSPLINE
    (Hermite with in/out tangents scaled by the key interval).
  * Sampling clamps to the first/last key; the object3D decides the clip time (looping).
  * Skinning is linear blend skinning: joint matrix = world(joint) @ inverseBind, applied relative to
    the skinned mesh node's world (glTF: the mesh node transform is ignored for skinned meshes).
  * Morph targets add weighted position/normal deltas before skinning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np


@dataclass
class MaterialSpec:
    """Material parameters with the scene-render <material> attribute names; colours are LINEAR
    RGBA floats. Texture fields hold decoded arrays (h, w, C) float32 in [0, 1] (baseColor/emissive
    already converted to linear) or None. Missing keys fall back to the <material> schema defaults."""
    params: dict = field(default_factory=dict)
    textures: dict = field(default_factory=dict)   # "baseColorMap", "normalMap", ... -> ndarray
    name: str = ""


@dataclass
class Primitive:
    positions: np.ndarray                     # (n, 3) float32
    indices: np.ndarray                       # (m, 3) uint32 triangles
    normals: np.ndarray | None = None         # (n, 3) float32; computed when missing
    uvs: np.ndarray | None = None             # (n, 2) float32, glTF convention (v down from top of image)
    tangents: np.ndarray | None = None        # (n, 4) float32 (w = handedness)
    colors: np.ndarray | None = None          # (n, 4) float32 linear vertex colours
    joints: np.ndarray | None = None          # (n, k) int joint indices into the skin's joint list
    weights: np.ndarray | None = None         # (n, k) float32, all influences retained
    morph_positions: list = field(default_factory=list)   # [(n, 3)] deltas
    morph_normals: list = field(default_factory=list)     # [(n, 3)] deltas (may be empty)
    material: MaterialSpec | None = None
    variants: dict = field(default_factory=dict)           # KHR_materials_variants: name -> MaterialSpec
    uv_sets: dict = field(default_factory=dict)            # TEXCOORD_n -> (n, 2)
    mode: int = 4                            # glTF/OpenGL topology; strips/fans are triangulated
    morph_tangents: list = field(default_factory=list)    # [(n, 3)] tangent XYZ deltas


@dataclass
class Node:
    name: str = ""
    translation: np.ndarray = field(default_factory=lambda: np.zeros(3))
    rotation: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0, 1.0]))   # quaternion xyzw
    scale: np.ndarray = field(default_factory=lambda: np.ones(3))
    matrix: np.ndarray | None = None          # explicit 4x4 (not animatable), overrides TRS when set
    children: list = field(default_factory=list)
    mesh: int | None = None                   # index into Model.meshes
    skin: int | None = None                   # index into Model.skins
    weights: np.ndarray | None = None         # default morph weights for the mesh
    matrix_sampler: Callable[[float], np.ndarray] | None = None  # native transform-op evaluation (USD)
    weight_mapper: Callable[[np.ndarray], np.ndarray] | None = None  # USD inbetween weights


@dataclass
class Skin:
    joints: list                              # node indices
    inverse_bind: np.ndarray                  # (k, 4, 4)


@dataclass
class Channel:
    node: int
    path: str                                 # translation | rotation | scale | weights
    times: np.ndarray                         # (k,)
    values: np.ndarray                        # (k, c) or (3k, c) for CUBICSPLINE (in, value, out)
    interpolation: str = "LINEAR"


@dataclass
class Clip:
    name: str
    channels: list
    duration: float = 0.0


@dataclass
class Splats:
    """3D Gaussian splats: centres, per-axis scales (std dev), rotations (xyzw), linear RGBA."""
    positions: np.ndarray
    scales: np.ndarray
    rotations: np.ndarray
    colors: np.ndarray


@dataclass
class DrawItem:
    """One posed primitive ready to draw: vertices already morphed and skinned (model space of the
    object3D, i.e. after the node hierarchy)."""
    positions: np.ndarray
    normals: np.ndarray
    indices: np.ndarray
    uvs: np.ndarray | None
    tangents: np.ndarray | None
    colors: np.ndarray | None
    material: MaterialSpec | None
    key: tuple                                 # stable id for GPU buffer caching
    static: bool                               # geometry independent of time (cacheable)
    uv_sets: dict = field(default_factory=dict)
    mode: int = 4


@dataclass
class Model:
    nodes: list = field(default_factory=list)
    roots: list = field(default_factory=list)
    meshes: list = field(default_factory=list)     # list[list[Primitive]]
    skins: list = field(default_factory=list)
    clips: list = field(default_factory=list)
    splats: Splats | None = None
    variants: list = field(default_factory=list)   # KHR_materials_variants names in file order
    source: str = ""

    def clip(self, name: str | None) -> Clip | None:
        if not self.clips:
            return None
        if name is None:
            return None
        for c in self.clips:
            if c.name == name:
                return c
        if name.isdigit() and int(name) < len(self.clips):
            return self.clips[int(name)]
        return None

    def pose(self, clip: Clip | None, t: float, morph_override: list | None = None,
             variant: str | None = None) -> list[DrawItem]:
        """Posed draw items at clip time t (seconds; already looped by the caller). The rest pose (no
        clip, no morph override) does not depend on t and is computed once per material variant;
        callers must not modify the returned arrays in place."""
        from .animation import pose_model
        if clip is None and morph_override is None:
            memo = self.__dict__.setdefault("_rest_pose", {})
            hit = memo.get(variant)
            if hit is None:
                hit = memo[variant] = pose_model(self, None, 0.0, None, variant)
            return hit
        return pose_model(self, clip, t, morph_override, variant)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        items = self.pose(None, 0.0)
        pts = [i.positions for i in items if len(i.positions)]
        if self.splats is not None:
            pts.append(self.splats.positions)
        if not pts:
            return np.zeros(3), np.zeros(3)
        p = np.concatenate(pts)
        return p.min(0), p.max(0)
