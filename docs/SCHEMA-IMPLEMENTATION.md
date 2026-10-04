# Scene-render 1.1 implementation work

The active objective is complete implementation of the features in
`schema/scene-render-1.1.xsd`, with behavior verified against the schema, including
the gaps documented in `audits/scene-render-1.1/README.md`. The audit is a historical
snapshot. Registered feature names and an existing green test suite do not prove
that this objective has been achieved.

The implementation remains in progress. Do not treat this file as a declaration
of full support or close the objective based on the first repaired cases.

The [current inspection report](audits/scene-render-1.1/CURRENT-STATUS.md)
separates repaired native cases, source-confirmed import limits and outstanding
verification. Earlier audit reports retain their historical observations.

Implemented with new regression evidence:

| Area | Evidence |
|---|---|
| Include file origins, token isolation, dynamic property/parameter references, nested include preparation, strict validation, cycle handling and declared hash verification | `tests/test_document_scopes.py` |
| Repeat item variables (including nested data), reference copying, and subtractive opacity after animation | `tests/test_document_scopes.py` |
| Rendered visibility, z, shape and instance overrides; expression condition truthiness | `tests/test_evaluated_nodes.py` |
| Sequence source clocks and local keyframes; duration with trimming, speed, stretch and loops | `tests/test_evaluated_nodes.py` |
| Runtime sequence gaps and media durations, nested offsets/scales, per-instance schedules, effective references, remap endpoints, evaluated transition windows/defaults and video-layer audio clocks | `tests/test_sequence_scheduling.py`, `docs/TIMING.md` |
| Mutually consistent normalized duration/speed/stretch/trim/start/end/gap/group-scale values, descendant windows and bounded diagnostics for contradictory timing | `tests/test_sequence_scheduling.py` |
| Echo, posterize-time and node motion blur through group/sequence/instance clocks, reverse playback, loops and remaps; composition references and echo tails | `tests/test_temporal_clocks.py` |
| Particle and rigid-body history clocks, composition force fields and distinct replay caches for loop cycles; recorded-cache invalidation for changed clocks/references; state and rendered controls with backward seeks | `tests/test_simulation_clocks.py` |
| Particle birth/display evaluation, animated paths and seeds, sprite/mask clocks, preroll/window histories, unrestricted trail durations and render bounds under tall sprites/curved trails/nonuniform scales | `tests/test_particle_evaluation.py`, `docs/PARTICLES.md` |
| Spatial particle paints and style tokens, lifetime color/alpha fades, sprite multiplication and whole-trail coverage; conic gradients use analytic device-pixel angles with consistent coordinate units | `tests/test_particle_paints.py`, `docs/PARTICLES.md` |
| Animated particle collider alpha, node masks/effects/mattes/windows, swept thin-body contacts, body/parent velocities, symbol bounds, scoped overrides and output-scale invariance | `tests/test_particle_collisions.py`, `docs/PARTICLES.md` |
| Particle effective-parent coordinates, inherited motion in field velocities, per-birth layout/box/constraint transforms, scoped parent overrides and bounded transform caches | `tests/test_particle_transforms.py`, `docs/PARTICLES.md` |
| Layout properties use owner clocks while child dimensions and baselines use entered/scheduled clocks; identity-clock inversion avoids finite-difference drift | `tests/test_layout_clocks.py`, `tests/test_particle_transforms.py`, `docs/TIMING.md` |
| Particle-backed direct/group collider mattes, alpha/luma/inverted modes and independent colliding matte sources; bounded recent-state reuse, effective repeat bindings and cycle diagnostics with recovery | `tests/test_particle_mattes.py`, `docs/PARTICLES.md` |
| Collider ancestor opacity/clipping/masks/effects, adjustments, transition weights, complete reference dependencies, sibling layout isolation and projected alpha | `tests/test_particle_ancestors.py`, `docs/PARTICLES.md` |
| Projected collider contact matrices and velocities through flattened/collapsed/nested ancestors; perspective pinhole controls, camera translation/zoom/dolly and simulated dynamic bodies | `tests/test_particle_projection.py`, `docs/PARTICLES.md` |
| Projected emitter contact coordinates and Jacobian velocities, scene bounds, replay-independent frame contexts, binding traversal and gradient-based interior recovery | `tests/test_particle_projected_emitters.py`, `docs/PARTICLES.md` |
| Symbol-local particle replay through zero-scale outer instances, reappearance and nested instances; local dependencies and references across a shared instance boundary | `tests/test_particle_scene_space.py`, `docs/PARTICLES.md` |
| External raster references across collapsed canvases, frame-space mattes for projected consumers, off-frame source sampling and padded effect inputs | `tests/test_reference_pullback.py`, `docs/PARTICLES.md` |
| Referenced source geometry through projected groups and instances, inherited camera optics, fit/scoped clocks, effect/transition inputs and empty projected groups | `tests/test_reference_source_projection.py`, `docs/PARTICLES.md` |
| Scoped property/link references through nested instance clocks | `tests/test_evaluated_nodes.py` |
| `valueAtTime` across warped/borrowed clocks and changing windows; delayed/smoothed frame links; loop helpers with local/normalized time and vector components | `tests/test_expression_clocks.py` |
| Conic user coordinates and midpoint; radial focal animation without base attributes; deterministic gradient dithering | `tests/test_paint_layout_conformance.py` |
| Stretch allocation for row, column, stack and grid, with native group reflow | `tests/test_paint_layout_conformance.py` |
| Adaptive motion blur preserves return motion and short held-key changes | `tests/test_paint_layout_conformance.py` |
| Symbol body/camera discovery, scope-sensitive 3D caches, timing and geometry overrides, evaluated primitive/material/preset/constraint/deformer choices | `tests/test_remaining_conformance.py` |
| Symbol canvas dimensions, transformed 3D and camera-projected 2D children, and symbol-sized physics bounds | `tests/test_remaining_conformance.py`, `tests/test_camera3d.py` |
| Referenced node traversal preserves instance fit, canvas overrides, ancestor clocks and layout; symbol mattes, global shader sources, parent links and world/local constraints | `tests/test_remaining_conformance.py`, `tests/test_physics_constraints.py` |
| Matte visibility follows instantiated consumers and their clocks; effective matte/parent references distinguish instances of the same symbol | `tests/test_remaining_conformance.py` |
| Transition matte animation and source-media clocks, shared by luma and shader transitions | `tests/test_remaining_conformance.py`, `tests/test_transitions.py` |
| Animated force-field type, path, end and affected consumers; changing particle field lists and overridden emission starts | `tests/test_remaining_conformance.py`, `tests/test_physics.py`, `tests/test_particles.py` |
| Audio effect enable automation at individual samples; data-source SHA-256 verification | `tests/test_remaining_conformance.py` |
| Version gate using the original 1.0 element contract; four cross-field rules | `tests/test_semantic_validation.py`, `schema/scene-v1.PROVENANCE.md` |
| Radiance old/new RLE, multibyte runs, truncated-input rejection and XYZE conversion | `tests/test_3d_loaders.py` |
| Embedded/external IES tilt records, lamp geometries and `LAMPPOSITION`, legacy ballast factors, and animated burning-angle intensity correction | `tests/test_ies_tilt.py`, `docs/IES.md` |
| IES Type A/B/C coordinate frames, original-knot CPU/GPU interpolation, narrow beams, symmetries and seams, shared packed profiles beyond four lights, and diffuse/specular profile factors for all direct light types | `tests/test_ies_coordinates.py`, `docs/IES.md` |
| Dynamic direct-light/shadow counts, original-resolution shadow faces in array pages, cube-face boundaries, mixed lights/maps and transparency, animated uploads and backward seeks | `tests/test_light_storage.py`, `docs/LIGHTING.md` |
| Independent dome shadows and material lobes; specular-only shadows; all visible backgrounds; evaluated visibility/type/lobe flags; exact cache keys and live-map ownership; working framebuffer depth masks | `tests/test_dome_lights.py`, `docs/LIGHTING.md` |
| Full 1,000–40,000 K light-temperature range using the CIE observer; all eight light types in 3D and 2D effects, animated endpoints and explicit-color render controls; packaged observer data | `tests/test_light_temperature.py`, `docs/LIGHTING.md` |
| Image input color/transfer/alpha declarations; 16-bit PNG/TIFF and floating HDR samples; PSD layer/group and EXR part/channel selection; representations, proxy and hash checks; linear mip filtering, sequence missing-frame mixes and generated image precision | `tests/test_image_inputs.py`, `docs/IMAGE-INPUTS.md` |
| glTF padded matrices including sparse accessors, all skin weight sets, UV selection/transform and samplers per map, occlusion strength, point/line topology, default material, flat normals, partial normal/tangent morph targets | `tests/test_3d_loaders.py`, `tests/test_3d_render.py` |
| USD transform-op interpolation without shear loss; sparse blend shapes, normals, inbetweens and named animation weights | `tests/test_3d_loaders.py` |
| USD texture-coordinate graphs, interfaces, scale/bias, channel outputs, independent maps, metadata and 16-bit precision; animated material sampling, masked depth and displaced thumbnails; inherited skeleton/animation/skin bindings | `tests/test_usd_materials.py`, `docs/USD.md` |
| USD specular workflow and metal grazing reflectance; IOR/normal-aware clearcoat; transparent/presence opacity with direct, ambient and dome lighting; workflow/mode animation, tangent frames, shadows and shared-material mesh sidedness | `tests/test_usd_shading.py`, `docs/USD.md` |
| USD IOR/coat texture inputs with original-resolution float samples, independent UVs/wraps/mips, animated sampling, combined material stacks and cache eviction within existing GL sampler limits | `tests/test_scalar_material_maps.py`, `docs/USD.md` |
| MaterialX versioned defaults, typed opacity/alpha modes, independent image dimensions and UV/sampler graphs, color/precision handling, normal frames, native primitive sampling/overrides and animated material selection | `tests/test_materialx_inputs.py`, `docs/MATERIALX.md` |
| MaterialX dependency invalidation, nested image origins, authored GLSL dependencies, direct-node baking, version retention, concurrent publication and corrupt/failed bake recovery | `tests/test_materialx_cache.py`, `docs/MATERIALX.md` |
| Explicit material samplers use float interpolation and the declared border/min/mag/mip choices in color and shadow depth; independent CPU and native-GPU controls resolve the retained Intel numerical failures | `tests/test_scalar_material_maps.py`, `tests/test_materialx_inputs.py`, `docs/SAMPLING.md` |
| All 12 glTF material-extension texture inputs, independent sampling and UV sets, RGBA mip storage, iridescence thickness limits, clearcoat normal scale and authored tangent frames | `tests/test_gltf_material_textures.py`, `docs/GLTF.md` |
| glTF node/material animation pointers, independent texture transforms, typed interpolation, morph components, matrix translation, clip/variant isolation and per-instance playback with color/shadow controls | `tests/test_gltf_animation_pointer.py`, `docs/GLTF.md` |
| KTX2/BasisU sources, ETC1S/UASTC/Zstandard decoding, RGB/RGBA/R/RG channel semantics, authored mips across all material maps, partial-tail generation, animation and alpha shadows | `tests/test_gltf_ktx.py`, `docs/GLTF.md`, `docs/RUNTIME.md` |
| Shader asset inputs (including animated generators, image sequences and include namespaces); frame indices inferred by time-only rendering APIs | `tests/test_shaders.py` |
| ISF persistent feedback with bounded checkpoints, upstream effects and backward seeks; evaluated buffer sizes, pixel/normalized lookups, audio and FFT inputs | `tests/test_shaders.py`, `tests/test_audio.py`, `tests/test_audio_full.py` |
| Lottie embedded glyph shape trees and advances, including path text and text animators | `tests/test_lottie_text.py` |
| Tracking JSON SVG curves, multiple contours and holes, preserving path interpolation and landmark endpoints | `tests/test_tracking.py` |

Additional shared-property changes (effect selection, mattes, media fitting/flips,
stroke choices, layout selection and group isolation) require broader regression
coverage; their code changes alone are not sufficient completion evidence.

Outstanding verification and implementation gates:

1. Map every requirement in the resolved declaration inventory to implementation
   and behavioral evidence. The inventory preserves named/anonymous types,
   inheritance, defaults, choices and cardinalities; its static source index does
   not establish conformance. Review preparation-time versus per-frame behavior.
2. Finish property evaluation across all native handlers and cached paths. Cover
   expressions/links/animation and instance overrides, not only static attributes.
   Audit per-instance cache identity, parent/matte/constraint transforms and nested
   clocks across 2D, 3D, physics, tracking, text and audio.
3. Review the remaining cross-field/reference semantics with authoritative
   evidence. The version gate and the four referenced semantic rules are now
   implemented; do not invent further restrictions absent from the contract.
4. Complete and verify external-format gaps identified by the audit: Lottie precomp
   glyphs and general expressions; glTF camera/light pointers; remaining USD
   shading/stage inputs documented in `USD.md`; FBX tracking inputs; ASS override
   behavior. Broaden combination coverage for the repaired import paths;
   glTF material-extension textures, iridescence thickness inputs and node/material
   animation pointers now have regression evidence. Remaining glTF shader-model
   and tangent-generation verification is described in `GLTF.md`.
   MaterialX's retained default, color-opacity and texture-dimension failures are
   repaired. Full shader-model semantics, unmapped inputs, general graph/sampling
   fidelity remain part of the native `materialX` gate;
   see [MATERIALX.md](MATERIALX.md). The six retained audit documents validate
   against MaterialX 1.39.5's library.
   Native image input declarations and PSD/flat-EXR selections now have rendered
   evidence. Remaining format and downstream limits include PSD adjustments,
   effects, text and non-RGB modes, deep EXR flattening, TIFF non-RGB modes,
   pattern paint precision and sequence-wide hash semantics; see `IMAGE-INPUTS.md`.
5. Check all text, paint, shape, asset, effect, transition, camera, light, material,
   physics, deformation, tracking, caption, parameter, layout, color, audio,
   metadata and output requirements. Existing family tests are starting evidence,
   not a substitute for requirements they do not exercise.
6. Make support reporting accurate. Cairo >= 1.18 and Pycairo >= 1.25 are now
   documented in `RUNTIME.md` and checked before rendering. The optional import
   backends and their remaining support limits still need truthful reporting.
7. Update documentation and the audit runner to distinguish repaired cases from
   expected validation errors. Run the complete suite and rendered/media checks
   after integration. Review every remaining requirement before marking complete.

Latest integrated checkpoint (2026-10-01): **3,335 tests passed on Intel Arc**
(739.52 seconds), with no skips. The 182 focused KTX2/BasisU cases also
passed on Intel (4.34 seconds), using libktx 4.4.2. All 297
source/schema/test inputs were unchanged throughout final verification. Earlier
full, extension and pointer llvmpipe checkpoints retain their source/evidence
hashes; no new full llvmpipe run is claimed here.

The complete suite includes the 182 new BasisU cases and the preceding source
projection, reference sampling, particle, glTF, MaterialX, USD, lighting, IES,
timing, scope, simulation, image, import and output checks. The six previous
cycle-diagnostic cases establish rejection and recovery, not cyclic feedback
simulation. Prior Intel numerical failures stay resolved at their original
tolerances. Observer data was verified in the earlier built-wheel check.

Both audit runners now complete without probe errors. All twelve current-audit
render/audio counterexamples match their controls; incorrect data hashes and the
1.0 version-gate example are rejected. Saved observations and source hashes are in
[`implementation-results.json`](audits/scene-render-1.1/implementation-results.json).
This resolves those reproducers, not the outstanding requirements above.

The additional [`light_probe.py`](audits/scene-render-1.1/light_probe.py) now matches
the equivalent intensity controls for 17 direct lights and eight shadow maps.
Neither case has differing channels above `1e-5`. The former count truncation has
been replaced with texture-backed data and an array atlas; see `LIGHTING.md`.
The same runner's red/blue dome comparison now also matches the independent
controls. Each dome retains its own shadow factor and settings. Animated
environment visibility/type/lobe flags, cache eviction, and multiple visible
backgrounds have new rendered regression coverage; see `LIGHTING.md`.

Known integration concerns requiring further work include arbitrary discontinuous
clock expressions, remaining rigid-body window combinations, general audio transition automation,
remaining direct XML reads, and projected/3D
combinations of referenced content. ISF feedback checkpoints now avoid replaying
the full history on every sequential export frame; the audio texture convention
is documented in `scenerender/effects/shader.py`.

Particle collisions now sample animated rendered alpha on each scene clock,
with swept contacts and parent/body velocity controls. Particle-backed collider mattes now work for acyclic dependencies. Cyclic
matte/physics feedback is rejected. Ancestor rendering, projected moving-body
velocities and projected emitter contact coordinates now have controls. Symbol-local
collision replay survives zero-scale outer instances and reappearance, including
external mattes/effect sources. Referenced sources retain group/instance projection
and enclosing camera optics. Wider singular, temporal, deformation and reference
combinations remain open. The
emitter/collision registry labels continue to report partial support;
see [PARTICLES.md](PARTICLES.md) for the repaired evaluation paths and remaining
native boundaries.
