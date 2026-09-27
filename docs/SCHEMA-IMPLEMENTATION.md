# Scene-render 1.1 implementation work

The active objective is complete implementation of the features in
`schema/scene-render-1.1.xsd`, with behavior verified against the schema, including
the gaps documented in `audits/scene-render-1.1/README.md`. The audit is a historical
snapshot. Registered feature names and an existing green test suite do not prove
that this objective has been achieved.

The implementation remains in progress. Do not treat this file as a declaration
of full support or close the objective based on the first repaired cases.

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
| Particle and rigid-body history clocks, composition force fields and distinct replay caches for loop cycles; state and rendered controls with backward seeks | `tests/test_simulation_clocks.py` |
| Scoped property/link references through nested instance clocks | `tests/test_evaluated_nodes.py` |
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
| Image input color/transfer/alpha declarations; 16-bit PNG/TIFF and floating HDR samples; PSD layer/group and EXR part/channel selection; representations, proxy and hash checks; linear mip filtering, sequence missing-frame mixes and generated image precision | `tests/test_image_inputs.py`, `docs/IMAGE-INPUTS.md` |
| glTF padded matrices including sparse accessors, all skin weight sets, UV selection/transform and samplers per map, occlusion strength, point/line topology, default material, flat normals, partial normal/tangent morph targets | `tests/test_3d_loaders.py`, `tests/test_3d_render.py` |
| USD transform-op interpolation without shear loss; sparse blend shapes, normals, inbetweens and named animation weights | `tests/test_3d_loaders.py` |
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
   glyphs and general expressions; glTF animated pointers and KTX2; USD texture
   transforms/scale/bias; IES tilt/types; FBX tracking inputs; ASS override
   behavior. Broaden combination coverage for the repaired import paths; remaining
   glTF material-extension textures also need review.
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

Latest integrated checkpoint (2026-09-27): **1,739 tests passed**, consisting of
1,721 in the restricted full run (98.00 seconds), all 16 checks that needed local
socket or USD temporary-directory access passing on rerun (2.32 seconds), and two
additional reference/remap regressions passing afterward. All 32 sequence
regressions passed together (4.32 seconds). This includes the 43 image-input
regressions and earlier shader, glTF, reference and transition-matte changes.
No tests were skipped in this checkpoint.

Both audit runners now complete without probe errors. All twelve current-audit
render/audio counterexamples match their controls; incorrect data hashes and the
1.0 version-gate example are rejected. Saved observations and source hashes are in
[`implementation-results.json`](audits/scene-render-1.1/implementation-results.json).
This resolves those reproducers, not the outstanding requirements above.

Known integration concerns requiring further work include arbitrary discontinuous
clock expressions, simulation preroll/window changes, delayed links and
`valueAtTime` across clock warps, general audio transition automation,
remaining direct XML reads, and projected/3D
combinations of referenced content. ISF feedback checkpoints now avoid replaying
the full history on every sequential export frame; the audio texture convention
is documented in `scenerender/effects/shader.py`.
