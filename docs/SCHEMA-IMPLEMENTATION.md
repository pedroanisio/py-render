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
| Scoped property/link references through nested instance clocks | `tests/test_evaluated_nodes.py` |
| Conic user coordinates and midpoint; radial focal animation without base attributes; deterministic gradient dithering | `tests/test_paint_layout_conformance.py` |
| Stretch allocation for row, column, stack and grid, with native group reflow | `tests/test_paint_layout_conformance.py` |
| Adaptive motion blur preserves return motion and short held-key changes | `tests/test_paint_layout_conformance.py` |

Additional shared-property changes (effect selection, mattes, media fitting/flips,
stroke choices, layout selection and group isolation) require broader regression
coverage; their code changes alone are not sufficient completion evidence.

Outstanding verification and implementation gates:

1. Resolve every named/anonymous schema type, inherited attribute, default,
   enumerated choice and child relationship into a conformance inventory. Review
   actual consumers, including preparation-time versus per-frame behavior.
2. Finish property evaluation across all native handlers and cached paths. Cover
   expressions/links/animation and instance overrides, not only static attributes.
   Audit per-instance cache identity, parent/matte/constraint transforms and nested
   clocks across 2D, 3D, physics, tracking, text and audio.
3. Add the referenced semantic validation rules and version gate with tests;
   preserve valid 1.0 behavior and avoid inventing restrictions absent from the
   contract. Validate cross-field/reference semantics with authoritative evidence.
4. Complete and verify external-format gaps identified by the audit: Lottie glyphs
   and general expressions; glTF UV/skin sets, primitive modes, padded accessors,
   animated pointers and KTX2; USD texture transforms/scale/bias, blend shapes and
   transforms; IES tilt/types; old Radiance RLE; tracking curves/FBX inputs; ASS
   override behavior; shader image inputs and persistent buffers.
5. Check all text, paint, shape, asset, effect, transition, camera, light, material,
   physics, deformation, tracking, caption, parameter, layout, color, audio,
   metadata and output requirements. Existing family tests are starting evidence,
   not a substitute for requirements they do not exercise.
6. Make runtime dependency requirements and support reporting accurate. Explicit
   gradient dithering currently uses Cairo 1.18 floating image surfaces; verify
   and document the supported Cairo/Pycairo baseline or provide a correct fallback.
7. Update documentation and the audit runner to distinguish repaired cases from
   expected validation errors. Run the complete suite and rendered/media checks
   after integration. Review every remaining requirement before marking complete.

The baseline before these implementation changes was 1,485 passing tests. The
first combined new/core regression run passed 59 tests. A complete integration run
is still pending as of this checkpoint.
