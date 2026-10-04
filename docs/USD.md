# USD mesh import

USD, USDA, USDC and USDZ mesh assets use the `usd-core` backend. The importer
retains mesh hierarchies, material subsets, skeletons, blend shapes and supported
animation in the renderer's shared model representation. This is a mesh importer;
it does not render every USD prim type or every shading network.

`UsdPreviewSurface` materials now retain the following inputs:

| Input | Imported behavior |
|---|---|
| `diffuseColor`, `emissiveColor` | Constant color or an independent RGB texture |
| `roughness`, `metallic`, `occlusion` | Constant or an independently sampled texture channel |
| `useSpecularWorkflow`, `specularColor` | Select metalness or direct specular reflectance; the specular input accepts a constant or independent RGB texture |
| `normal` | Constant or signed tangent-space texture values, encoded for GPU normal mapping |
| `displacement` | Constant or texture channel moving mesh vertices along their normals |
| `opacity` | Constant or independent texture channel; `opacityThreshold` selects masking |
| `opacityMode` | `transparent` preserves specular and emissive response; `presence` scales the whole response |
| `ior`, `clearcoat`, `clearcoatRoughness` | Constant or an independently sampled texture channel |

The texture reader follows connected material inputs and nodegraph outputs.
Each map preserves its named UV primvar, nested `UsdTransform2d` operations,
channel selection, scale/bias and independently configured wrap axes. Indexed,
inherited, face-varying and uniform UV primvars are expanded with the geometry.
Missing primvars use the reader's fallback; missing images use the texture's
fallback value. Black borders include the texture bias. Masked opacity is also
sampled in depth and shadow passes.

IOR and the two clearcoat maps share a float atlas with separate coordinates,
wrap modes, borders and mip chains. Each map keeps its original resolution and
level-zero samples; reductions preserve pixel-area averages, including odd image
dimensions. The atlas uses the otherwise unused USD metallic/roughness slot.
Paired UV attributes keep the compiled program at 14 vertex attributes and 16 fragment
texture samplers. Atlas capacity failures are explicit instead of resizing maps.
Filtering uses an unclamped LOD query for each original image's footprint and a
zero minification/magnification crossover, permitted by the
[OpenGL 4.1 specification, section 3.8.12](https://registry.khronos.org/OpenGL/specs/gl/glspec41.core.pdf).

Color decoding follows `sourceColorSpace` independently of the destination
surface input. Automatic decoding recognizes sRGB metadata, including ICC
profile descriptions and PNG gamma, and otherwise uses the texture's component
type/count. Explicit `raw` avoids the sRGB transfer conversion. Sixteen-bit PNG
components retain their precision. These conventions follow the
[OpenUSD preview-surface specification](https://openusd.org/release/spec_usdpreviewsurface.html).

Time-sampled material inputs contribute to the `default` animation clip. This
includes texture paths and values, coordinate transforms, interface inputs and
constant surface parameters, workflow selection and opacity mode. Animated UVs
reuse decoded image storage. Material samples and decoded/processed texture
caches use bounded entry counts; GPU
uploads also have entry and byte budgets, retaining the maps needed by the
current draw. An evicted upload is reconstructed from its source pixels when
needed again.

USD shading retains its own reflectance conventions. The metalness workflow
interpolates both facing and grazing reflectance toward the albedo. The specular
workflow uses the supplied facing reflectance and a white grazing reflectance.
Clearcoat uses the material's IOR and the same normal as the base surface.
Normal-map frames account for USD's upward texture axis, including rotated and
mirrored coordinates and constant-coordinate texture reads.

Transparent materials use the renderer's thin-surface, screen-space transmission
path. Presence opacity uses premultiplied layer blending. A positive opacity
threshold makes the retained fragments opaque in either mode. Translucent USD
shadow maps use a binary 0.5 opacity test; they do not integrate fractional
transmission through a volume. As with other 3D materials, translucent layers
composite in document order, so background layers precede them.

Mesh `doubleSided` is applied per mesh, including back faces of transparent
materials. Meshes sharing an animated material keep their own sidedness setting.

Displacement applies in both `<object3D primitive="mesh">` and mesh-backed
`<layer>` thumbnails. The thumbnail camera continues to frame the model's
original bounds, so displacement beyond those bounds can extend outside its
original framing. Vertex displacement does not add tessellation.

Skeleton and animation-source bindings can be inherited. Constant joint
influences, mesh-local joint order and geometry bind transforms are resolved
through ancestors. The inheritance behavior follows
[UsdSkel binding rules](https://openusd.org/release/api/_usd_skel__schemas.html).

Evidence is in [test_usd_materials.py](../tests/test_usd_materials.py) and
[test_3d_loaders.py](../tests/test_3d_loaders.py). The 56 new regressions include
rendered comparisons against baked coordinates/geometry, independent roughness
and metallic maps, masked depth, animated backward seeks, CPU/GPU wrap edges,
six inherited-binding combinations, texture precision and cache eviction.
Another 88 regressions in [test_usd_shading.py](../tests/test_usd_shading.py)
cover analytic reflectance values, direct/ambient/dome opacity controls,
normal-frame orientation, workflow/mode animation, compositing, shadows and
shared-material sidedness.

The 131 regressions in [test_scalar_material_maps.py](../tests/test_scalar_material_maps.py)
cover IOR/coat textures: analytical shading, independent resolutions and UVs,
channel/scale/bias inputs, all wrap combinations and min/mag filters, mip averages,
animated coordinates and samplers, complete texture stacks, transparency and cache
eviction. Float64 CPU controls check sampling at the queried LOD; native GPU
comparisons use exactly representable weights. Explicit samplers interpolate
floating-point texels without hardware fraction quantization. See
[SAMPLING.md](SAMPLING.md) for the filter and nearest-border repairs.

Remaining implementation and verification gates include UDIM tiles and other
primvar-reader types. Non-sRGB ICC
profiles are not color-managed by this reader. Other USD prim types, animated
geometry/UV primvars and visibility, subdivision and non-linear
skinning require further work. Displacement combined with internal node scaling
or skinning needs additional verification. These limits remain part of the open
implementation objective in [SCHEMA-IMPLEMENTATION.md](SCHEMA-IMPLEMENTATION.md).
