**Full scene-render 1.1 support is not established.** All 269 named alternatives
checked against the registries have handlers, and the earlier retained audit
counterexamples now pass. Imported-format limits and unfinished verification of
individual properties, evaluation contexts and combinations remain. The active
implementation objective is still open.

This report describes the working tree based on commit
`80bdb3dba51de74192a0a00294babbda72c2e515`, inspected on 2026-10-01. It supersedes
the current-status conclusions in [CURRENT-INSPECTION.md](CURRENT-INSPECTION.md)
and [README.md](README.md); those files preserve the historical failures.
The source and evidence hashes in [implementation-results.json](implementation-results.json)
identify the inspected content, including uncommitted changes.

The schema is unchanged: SHA-256
`3d0ecb499bc682ab69405a7f57dccfe1a659abbc3dbcb5a33897a17e28b3c603`.
The [declaration inventory](inventory.json) includes the complete XSD tree and
resolved inheritance, groups, defaults, choices, cardinalities and lexical facets:

| Inventory | Count |
|---|---:|
| Element declarations | 131 |
| Attribute declarations | 1,151 |
| Distinct attribute names | 623 |
| Complex types | 116 |
| Simple types | 167 |
| Runtime-reachable types | 114 |

These counts are not a conformance percentage. Static source-read candidates
include unrelated dictionary accesses and miss dynamic consumers; they are review
leads, not behavioral proof. The requirement-by-requirement evidence matrix remains
an outstanding gate in [SCHEMA-IMPLEMENTATION.md](../../SCHEMA-IMPLEMENTATION.md).

| Named family | Schema alternatives with handlers |
|---|---:|
| Nodes | 13/13 |
| Assets | 15/15 |
| Effects | 80/80 |
| Transitions | 35/35 |
| Blend modes | 35/35 |
| Shape modifiers | 9/9 |
| Deformers | 14/14 |
| Text animator presets | 24/24 |
| Audio effects | 16/16 |
| Caption presets | 13/13 |
| Output codecs | 15/15 |

GIF is marked partial because its format limits color count and transparency.
Particle emitters and particle collision also report partial support for
cyclic collision/matte feedback and wider ancestor/projected combinations; see [PARTICLES.md](../../PARTICLES.md).
There are also 182 cross-cutting registry declarations, without a corresponding
one-to-one schema denominator. Registry labels do not test imported file contents,
runtime dependencies, animated selectors, instance overrides or combinations.

**Current verification**

The complete suite passed **3,335 tests on Intel Arc** (739.52 seconds),
with no skips. The **182 focused KTX2/BasisU cases** also passed on Intel
(4.34 seconds), using libktx 4.4.2. All 297 source/schema/test inputs were
unchanged throughout final verification. Earlier full and targeted llvmpipe runs
retain their source and evidence hashes in the JSON's prior checkpoints; no new
full llvmpipe run is claimed here. Counts and source hashes are recorded in
[SCHEMA-IMPLEMENTATION.md](../../SCHEMA-IMPLEMENTATION.md) and the JSON checkpoint.
Passing the previous six cycle-diagnostic cases establishes rejection and recovery,
not cyclic feedback simulation.
The runtime is Python 3.12.3, Cairo 1.18.0, Pycairo 1.25.1 with Intel Arc and Mesa llvmpipe backends.
Local delivery tests need loopback sockets; USDZ packaging writes temporary files
under `/var/tmp`; the MaterialX baker needs a local GLX display socket.
Restricted failures must be reconciled with the rerun before
counting the suite as passed. No live remote publication was performed.

The prior Intel Arc run had 37 numerical failures among 155 material/texture
controls. Native filtering used eight-bit fractional interpolation weights;
ordinary imported textures now interpolate their float texels in the shader.
Inspection also repaired nearest-border filtering, which incorrectly blended
the border with edge texels. The expanded 345-case material/texture suite passes
as part of the complete current Intel suite, with no skips.
Historical failures, the precision probe and current results are retained in
[intel-numerical-results.json](intel-numerical-results.json).
The revised mip controls use independent float64 references; native GPU controls
use exactly representable fractions. Their numerical tolerances are unchanged.
See [SAMPLING.md](../../SAMPLING.md) for scope and the color/shadow-depth checks.

| Diagnostic runner | Current observations |
|---|---|
| [Original audit](probe.py), 35 XSD-valid fixtures | Previously retained visibility, repeats, includes, paint, layout, timing, motion blur and scoped-reference cases behave as expected. Invalid includes, hash mismatches, cycles and the 1.0 version-gate violation are rejected. |
| [Subsequent audit](current_probe.py), 24 XSD-valid fixtures | Ten rendered comparisons have zero changed channels; instance start visibility and audio-effect enable automation match expectations. The bad data digest is rejected. Symbol physics discovers its body and matches the direct scene. |
| [Light audit](light_probe.py) | 17 direct lights and eight shadow maps match equivalent summed-intensity controls within float rounding; no channel differs by more than `1e-5`. Independent red/blue dome contributions match exactly. |
| [MaterialX audit](materialx_probe.py), six library-validated documents | The seven checked defaults now match. Color opacity imports as 0.5 coverage with blending; differently sized roughness/metallic maps retain both original dimensions. |
| [Projection motion audit](particle_projection_probe.py), six XSD-valid fixtures | Tilted walls with camera depth motion exercise non-affine contact mappings. Independent pinhole velocities agree within `1e-7` horizontally and `5e-6` vertically; the maximum observed error is `1.74e-6` px/s. |

The audit runners distinguish an expected validation failure from a probe error.
Pixel equality applies to these controls, not to all valid scenes or to an
independent reference renderer for every effect.

Additional current regression evidence covers the dome repairs in
[test_dome_lights.py](../../../tests/test_dome_lights.py): independent map settings,
diffuse/specular flags, visible backgrounds, transparency, animation, backward
seeks and live cache eviction. The audit's physics body counter was updated to
inspect the simulator's branch-history cache instead of incorrectly reporting
zero bodies from an obsolete cache layout.

Inspection also found that `light/@colorTemperature` accepted the schema's
1,000–40,000 K range but clamped computation to 1,667–25,000 K. It now integrates
Planck radiation with the CIE observer table throughout the schema range. All
eight light types pass endpoint and animation comparisons in 2D and 3D; the
packaged wheel contains and reads the observer data. See
[LIGHTING.md](../../LIGHTING.md) and
[test_light_temperature.py](../../../tests/test_light_temperature.py).

The USD material reader now follows connected texture-coordinate graphs and
material interfaces, preserves scale/bias and channel outputs, and samples each
map independently. Material animation, masked depth, displaced mesh thumbnails,
16-bit PNG precision and inherited skin bindings have 56 new regressions. See
[USD.md](../../USD.md) and
[test_usd_materials.py](../../../tests/test_usd_materials.py). The previous USD
row's scale/bias and transform omissions are repaired; the remaining boundary is
more specific below.

The subsequent shading repair adds 88 regressions for USD specular workflow,
metal grazing reflectance, clearcoat IOR/normals, transparent-versus-presence
opacity, normal-map coordinate frames, and mesh sidedness. Tests cover direct,
ambient and dome light, material-mode animation, background compositing and
opacity-map shadows. The shader keeps native/glTF material conventions separate.

IOR and clearcoat texture inputs now use independent float mip chains in a shared
atlas. Their 131 regressions cover sampling, material combinations, animation and
eviction. UV packing keeps the program at 14 vertex attributes and 16 fragment
texture samplers. The glTF extension UV table adds one vertex sampler. See [test_scalar_material_maps.py](../../../tests/test_scalar_material_maps.py)
and the filtering/capacity details in [USD.md](../../USD.md).

The MaterialX reader now resolves node-definition defaults and typed opacity,
keeps independent texture graphs and samplers, and passes their UVs to native
primitives. Rendered evidence covers normal frames, image borders, color decoding,
native overrides and animated material selection. See
[MATERIALX.md](../../MATERIALX.md) for the repaired paths and remaining boundaries.

MaterialX cache regressions also cover live dependency changes, nested image
origins, GLSL include changes, direct-node baking, version retention and corrupt
or incomplete bake recovery. Bakes use content fingerprints and publish their
artifact manifest after all outputs are ready. Installed MaterialX library
changes require a process restart. See
[test_materialx_cache.py](../../../tests/test_materialx_cache.py).

**Remaining source-confirmed boundaries**

These are current native behavior and input/backend boundaries, distinct from a
missing native XML element. The XSD's acceptance of a format name does not define every feature of
that external format. No new end-to-end reproducer was added for each row during
this inspection; the cited implementation documents or implements the limit.

| Area | Remaining boundary | Source |
|---|---|---|
| Particle emitters | Spatial paints, evaluated transforms, acyclic particle mattes, ancestor rendering, projected contacts and replay through zero-scale instances, including external raster references, have controls. Sources retain projected group/instance geometry and camera optics. Cyclic feedback is rejected; nonlinear lens/sibling/temporal interactions and wider singular/deformation/reference combinations remain open. | [PARTICLES.md](../../PARTICLES.md), [particle_collisions.py](../../../scenerender/nodes/particle_collisions.py) |
| Lottie | Precomp glyphs and general Lottie expressions remain outside the implemented subset. Embedded shape glyphs and scene-language expression selectors are implemented. | [lottie.py](../../../scenerender/assets/lottie.py#L38) |
| glTF/GLB | Node/material animation pointers are supported; camera, light and application-specific targets issue an unsupported warning. KTX2/BasisU decoding and authored mip chains are supported with libktx 4. Tangent generation, BRDF reference equivalence and spatially varying transmission shadows remain verification/implementation gates. | [GLTF.md](../../GLTF.md), [pointer reader](../../../scenerender/three/gltf_pointers.py), [material reader](../../../scenerender/three/loaders.py#L638) |
| USD/USDZ | UDIM and other primvar readers remain. Non-mesh stage types, animated geometry/UVs/visibility, subdivision and other skinning/displacement combinations require work. Transparent surfaces use the raster backend's screen-space transmission and binary shadows. | [USD.md](../../USD.md), [usd_materials.py](../../../scenerender/three/usd_materials.py) |
| MaterialX | The retained defaults, opacity, texture-dimension and dependency-cache failures are repaired. Unmapped shader inputs, full Standard Surface/OpenPBR lobe semantics, custom normal frames, frame/UDIM inputs and baking/filtering fidelity remain. | [MATERIALX.md](../../MATERIALX.md), [materialx_probe.py](materialx_probe.py) |
| Tracking FBX | FBX 6 Takes is rejected; curve-velocity flags are outside the implemented evaluator. This is the tracking importer, distinct from the ufbx mesh loader. | [tracking.py](../../../scenerender/tracking.py#L145), [parse_fbx](../../../scenerender/tracking.py#L1380) |
| ASS captions | Animated transforms, clipping, drawings and several 3D/skew/border overrides are warned and ignored. | [captions.py](../../../scenerender/captions.py#L60) |
| Images and paint | PSD compositing inherits backend limits; some non-RGB PSD/TIFF modes need work. Deep EXR requires pre-flattening. Pattern paint still uses an eight-bit Cairo surface. | [IMAGE-INPUTS.md](../../IMAGE-INPUTS.md), [deep EXR rejection](../../../scenerender/assets/image_io.py#L205) |
| Runtime availability | Full SVG, formula, code, shader and 3D paths depend on the installed backends. Missing GL causes reduced rendering paths; a registry label does not establish runtime availability. | [RUNTIME.md](../../RUNTIME.md), [pyproject.toml](../../../pyproject.toml) |

The previously identified glTF texture omissions are repaired. All 12 extension
texture inputs now retain their channels, sampling settings and independent UVs;
iridescence thickness limits and clearcoat normal scale reach the shader.
The 100 new regressions include rendered factor and spatial-sampling controls,
the complete core/extension texture stack, float64 filter references, atlas
eviction and authored tangent frames. See [GLTF.md](../../GLTF.md) for the
normative sources, GPU storage design and remaining boundaries.

Animation pointers now reach node transforms, whole/individual morph weights,
material factors and all 17 texture-coordinate transforms. The 131 regressions
cover typed interpolation, defaults and invalid targets, matrix translation,
clip/variant isolation, independent instance clocks, backward seeks and rendered
color/shadow controls. Camera/light targets remain outside the mesh renderer;
the camera warning and retained duration are tested. See [GLTF.md](../../GLTF.md).

The 182 BasisU regressions cover ETC1S, UASTC and UASTC/Zstandard, typed channel
unpacking, color/alpha transfer, external/data/GLB sources and fallback behavior.
All 17 material slots retain authored mip levels through rendering, animation
and cache eviction. Independent mip-filter controls and alpha-shadow comparisons
pass. Of 21 fixture files, 19 pass Khronos 4.4.2's glTF validator; legacy RRRG and
a partial pyramid are documented compatibility controls. See
[GLTF.md](../../GLTF.md), [RUNTIME.md](../../RUNTIME.md) and
[test_gltf_ktx.py](../../../tests/test_gltf_ktx.py).

**Remaining native verification gates**

The 36 particle evaluation regressions cover animated paths/display properties,
subframe births and masks, scoped and animated seeds, sprite source clocks,
preroll/window replay, long trails and transformed drawing bounds. Tests compare
rendered static controls and analytic states, including backward seeks and bounded
caches. The subsequent 60 paint regressions cover all five paint families,
color/alpha fades, sprite multiplication, whole-trail coverage, shared paint clocks
and style tokens. Conic gradients now use analytic device-pixel angles, with
independent scalar color-interpolation checks. The collision follow-up evaluates
current rendered alpha, scene-space bounds and relative body/parent motion, with
independent clock and moving-wall controls. Cyclic particle-matte feedback
and wider temporal/singular reference combinations remain open.

Another 36 particle-transform cases cover effective parents, inherited field
velocity, per-birth layout/percentage/anchor/motion-path inputs, constraints and
cross-instance overrides. Eleven independent layout cases verify owner clocks,
child clocks and sequence offsets across row, column, stack and grid. Identity
clock inversion no longer acquires seek-dependent finite-difference drift.

Another 29 particle-matte cases cover direct/group alpha and luma mattes, inverted
modes, animated opacity and separate symbol clocks. Colliding matte particles in
an independent world match analytically animated shape controls. Six cases check
cycle diagnostics and recovery, including repeat and cross-world dependencies;
these establish safe rejection, not support for cyclic feedback simulation.

Another 45 collider-ancestor cases cover opacity, clipping, masks, effects, mattes,
adjustments, sequence windows and echo tails, including retimed symbols and
backward seeks. Independent complete renders verify projected alpha, transition
weights, sibling layout slots and layer bodies at multiple output scales. The
retained parent-opacity reproducer now agrees with its equivalent node-opacity
control.

Another 56 projected-collider cases verify actual render matrices, homogeneous
relative sweeps and contact velocities. The orthographic moving-wall reproducer
now matches its affine control at -70 px/s. Independent pinhole equations cover
tilted planes; further controls cover camera translation/zoom/dolly, dynamic
bodies, nested/flattened/collapsed placement, retimed symbols and output scales.
Six additional standalone depth-motion controls verify non-affine relative
mappings against independent pinhole equations.
Another 58 projected-emitter cases verify contact coordinates, Jacobian velocity
mapping, bounds, binding traversal, fresh-frame-first replay and moving-camera
equivalence. The retained emitter reproducer now agrees with its affine control
(local x=19.4922 at 0.6 seconds), including pixels. Engulfed contacts now follow
the alpha gradient to an exit; numerical ties between exterior pixels no longer
eject equivalent scenes in opposite vertical directions.

Another 34 scene-coordinate cases verify local collision replay while outer
instances have zero scale on either axis, including reappearance, nested and
retimed instances, output scales, camera projection, local parents/constraints,
mattes and force fields. Cross-instance dependencies cancel a shared outer
instance before resolving relative coordinates. The original collapsed-instance
reproducer now matches its standalone control: local x=19.75, vx=-100 at 0.6
seconds.

Another 59 reference-sampling cases cover external mattes across either/both
collapsed axes, alpha/luma and inverted modes, source clocks and opacity,
shared instance boundaries, particle sources, difference/displacement effects,
transition mattes, off-frame shape/particle/layer geometry and tilted consumers.
Numerical controls check premultiplied interpolation and transparent handling of
projective poles and out-of-range coordinates. The retained external-matte
variant now matches an independently authored collision-window control exactly
in state and pixels (x=90, vx=100 at symbol time 0.6).

Another 109 projected-source cases cover six source kinds, both camera projection
types, nested/collapsed groups, symbol clocks and output scales, alpha/luma mattes,
camera optics, effect and transition inputs, and independent collision controls.
Projected and nested instances retain fit transforms and their enclosing cameras,
including when an inner symbol declares a different camera. Empty projected
groups render transparently. References continue to use a node's own appearance
before its parent's later opacity/matte/effect composition.

The retained source-group and source-instance reproducers now match their
independently rendered controls exactly: alpha sum 150.118 and zero differing
pixels, replacing the former sum 600 and 780 differing alpha pixels. General
singular transform dependencies, temporal interactions and wider projection,
deformation and reference combinations remain unverified.

| Subsystem | Current evidence and work still needed |
|---|---|
| Schema/defaults/validation | XSD validation, original-1.0 version gate and four semantic rules have tests. The remaining cross-field/reference requirements need explicit mapping. |
| Animation, expressions and links | Extensive evaluation, scope, clock and backward-seek regressions. All handler reads and cache identities still need per-property review. |
| Includes, repeats, symbols, sequencing and layouts | Repaired origin/hash/scope/override/scheduling cases pass. Arbitrary discontinuous clocks, nested windows and reference combinations remain review gates. |
| Shapes, paints, masks, blends, effects and transitions | All named handlers exist and family tests pass. Every attribute/default and interaction has not been independently verified. |
| Native text and typography | Shaping, international text, spans, text paths and animator tests exist. Complete combinations of selectors, scopes, projection and references need evidence. |
| 3D, materials, cameras and lighting | Broad render tests and repaired imports, light counts, domes, temperature paths and explicit texture sampling on Intel/llvmpipe. Remaining material-texture and projected-reference/matte combinations need review. |
| Particles, physics, deformation and tracking | State, rendering, history, spatial-paint and cache regressions pass, including particle preroll/window changes. Animated collider silhouettes and swept relative motion now have controls. Particle-backed collider mattes now have controls. Cyclic feedback, remaining rigid-body windows and broader projected/ancestor/reference combinations require work. |
| Audio | Effect enable automation, routing, mixing and spatial tests exist. General transition automation, resampling of control signals and layer discovery need broader proof. |
| Assets, hashes and deterministic caches | Native image declarations and declared generated caches have checks. Sequence-wide provenance hashing lacks a defined aggregate contract; remaining format boundaries apply. |
| Color, metadata, captions, QA and output | Existing family/delivery tests pass in the usable environment. No exhaustive independent codec/DSP/color-reference certification or live-cloud verification is claimed. |

Successful tests establish the tested behaviors. Completing the open objective
requires resolving the remaining implementation boundaries and assigning evidence
to the remaining native requirements; it cannot be inferred from registry totals.
