# MaterialX material inputs

`<material materialX="path.mtlx">` reads the first surface material in the document.
The reader resolves the selected node definition, including its version and
inherited defaults. Explicit scene attributes override imported values.
This remains a partial mapping of MaterialX shader models to the raster renderer.

Defaults are model-specific. For example, roughness defaults to 0.2 for Standard
Surface, 0.3 for OpenPBR, 1 for glTF PBR and 0.5 for USD Preview Surface. Standard Surface's
color-valued opacity is converted to luminance in the renderer's linear Rec.709
space and applied as coverage. The glTF shader's opaque/mask/blend choice and
cutoff survive import. USD preview materials use the same reflectance and opacity
conventions as [USD mesh materials](USD.md).

The input reader follows material interfaces and nodegraph outputs. Constants,
images, tiled images and channel extraction/swizzling can be read directly.
Each texture keeps its own dimensions, selected UV set, coordinate transform,
wrap axes and border value. `place2d` preserves pivot, scale, rotation, translation
and operation order; tiled images preserve their tiling and real-world size ratio.
These coordinates now reach native primitives as well as imported meshes.
Closest and linear filtering are retained; CPU vertex displacement also respects
the closest filter. Roughness and metallic images are no longer combined into a
single image. Explicit sampler interpolation uses float texels in both the color
and depth passes; nearest border sampling does not blend with edge texels. See
[SAMPLING.md](SAMPLING.md) for the cross-backend controls.

Missing images use their node's default value. Color constants and color images
follow their declared color space; vector and scalar images remain data. An absent
color-space declaration does not imply sRGB decoding. Direct conversions include
sRGB and linear Rec.709, Rec.709 gamma 1.8/2.2/2.4, linear AP0/AP1, P3-D65,
Rec.2020 and XYZ-D65, and sRGB-encoded P3-D65. PNG decoding retains 16-bit samples.
PNG and NumPy components retain their source order, with absent components filled
with zero and absent alpha filled with one.

MaterialX normal inputs are world-space vectors, except the USD preview model's
tangent-space input. A standard `normalmap` node with the default geometry frame
retains scalar or two-component scale, its zero-vector fallback and the mesh's
tangent frame, including authored tangent directions, independently of image-coordinate transforms. Native normal-map
overrides restore the scene renderer's normal-map convention.
The MaterialX USD preview definition also uses that geometry frame. Connecting a
`normalmap` output to its tangent-space input retains both transformations in the
graph, including on rotated meshes.

Native texture overrides replace imported sampling metadata. A native combined
metallic/roughness override replaces both independent maps; for USD preview
materials it is split into channels so the IOR/coat atlas can retain its sampler.
Animated selection between MaterialX files updates primitive UVs during forward
and backward playback.

The in-memory cache observes the root document, nested includes, resolved image
files, authored shader implementations and recursive GLSL includes. Changed,
created, deleted or replaced dependencies invalidate the cached material, including
edits that restore the original modification time. Included files retain their own
image directories and filename prefixes. A new include earlier on the search path
also invalidates the old selection.

Disk bakes use SHA-256 content fingerprints, the bake size, the baker script,
MaterialX version and installed library/shader contents. Installed libraries are
treated as immutable during a process; restart after changing an installed library.
Concurrent writers share a lock. Only validated documents with all generated images
are published, and an artifact manifest detects missing or corrupt cached files.
Failed or interrupted bakes and dependencies edited during a bake are not cached.
Direct shader-node connections are exposed as outputs for the MaterialX baker,
and shader version/definition selectors survive baking. These paths have separate
regressions in [test_materialx_cache.py](../tests/test_materialx_cache.py).

These input conventions follow the
[MaterialX standard-node specification](https://github.com/AcademySoftwareFoundation/MaterialX/blob/main/documents/Specification/MaterialX.StandardNodes.md)
and the installed MaterialX 1.39.5 node definitions. Evidence is in
[test_materialx_inputs.py](../tests/test_materialx_inputs.py), including numerical
sampling controls, rendered opacity and normal comparisons, and comparisons with
equivalent USD surfaces. The retained
[import audit](audits/scene-render-1.1/materialx_probe.py) checks the original seven
default mismatches, color-opacity failure and differently sized image failure.

Remaining gates include unmapped shader inputs and complete Standard Surface /
OpenPBR lobe semantics, material-level displacement/back-surface assignments,
additional material-extension textures, custom normal
frames, geometry-dependent graphs, frame/UDIM filename substitutions and all
image-layer/component combinations. Other graphs still use the existing 1024²
MaterialX baker; baking and filtering fidelity still need work. The dependency
tracking above does not implement unresolved frame/UDIM filename substitutions.
Unsupported color spaces and cubic sampling do not have a direct reader path.
The successful cases above do not establish full MaterialX or schema conformance.
