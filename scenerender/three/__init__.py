"""Real 3D for scene-render: the moderngl PBR renderer behind object3D, model loaders and 360 output.

Modules: model (data model), loaders (glTF/GLB, OBJ/PLY/STL, USD/USDZ, FBX, splats, HDR/EXR, IES),
animation (clip sampling, morphs, skinning), geometry (primitives, 3D text, extrusions), materials
(<material> + MaterialX), lights (<lights>, IBL maths), shaders / renderer (GL passes), scene
(per-frame assembly and layer post-processing), postfx (bokeh, resampling) and view360 (panoramas).

Core entry points: `scenerender.nodes.scene3d` registers the object3D / camera handlers;
`render_360(rc, t, frame) -> Buf` renders a panorama (installed as rc.hooks["render360"] when
project/@mode="equirectangular"); `size_360(doc, scale)` gives its size.
"""
from __future__ import annotations


def render_360(rc, t: float, frame: int = 0):
    from .view360 import render_360 as _r
    return _r(rc, t, frame)


def size_360(doc, scale: float):
    from .view360 import size_360 as _s
    return _s(doc, scale)
