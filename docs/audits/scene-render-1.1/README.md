# scene-render 1.1 implementation audit

**Verdict: broad implementation coverage, but not complete conformance to
`schema/scene-render-1.1.xsd`.** All 269 named alternatives in the 11 registry
categories below are registered. Schema-valid documents nevertheless reproduce
incorrect rendering, ignored properties, broken references, and timing errors.
The previous fixes for XML boolean forms, Lottie text paths/selectors, MXF content
light metadata, and coverage reporting do not resolve these additional problems.

Inspected on 2026-09-27 using Python 3.12.3, against the current working tree based
on commit `9d2089f0969cc7558024ec51b9aad70bd2f1733f`, including its uncommitted changes.
Schema SHA-256:
`3d0ecb499bc682ab69405a7f57dccfe1a659abbc3dbcb5a33897a17e28b3c603`.
This audit adds evidence files; it does not change the renderer implementation.

The inspection combined schema inventory, handler and shared-runtime source
review, the complete existing test suite, and targeted counterexamples. The
schema contains 131 element declarations, 1,151 attribute declarations, 116
complex-type declarations and 167 simple-type declarations; these count repeated
and anonymous declarations, not distinct feature names. Not every possible
attribute combination or external file format was tested. A percentage of full
semantic conformance cannot be derived from these counts.

The complete test suite passed: **1,485 passed in 71.14 seconds**, with no skipped
tests reported. The audit probe completed without probe errors, generating **35
XSD-valid XML fixtures** (including controls). Its strict-include test also writes
one intentionally invalid child document, excluded from that count. The
version-gate fixture deliberately declares version 1.0 and passes this XSD.
Passing the existing suite does not negate the counterexamples below.

Run from the repository root:

```bash
MPLCONFIGDIR=/tmp/py-render-audit-mpl .venv/bin/python -m pytest -q
MPLCONFIGDIR=/tmp/py-render-audit-mpl .venv/bin/python docs/audits/scene-render-1.1/probe.py
```

[probe.py](probe.py) writes XML fixtures, rendered PNGs and fresh `results.json`
under `/tmp/scene-render-schema-audit`. [results.json](results.json) is the
saved snapshot from this audit. The probe prints observations; it is a diagnostic, not a regression
suite that treats known incorrect results as desired behavior. An exit status of
zero means the probes completed, not that the renderer conforms.

| Schema category | Schema names | Registered schema names | What the registry establishes |
|---|---:|---:|---|
| Nodes | 13 | 13 | Entry/preparation path exists |
| Assets | 15 | 15 | Handler exists in this environment |
| Effects | 80 | 80 | Named effect is registered |
| Transitions | 35 | 35 | Named transition is registered |
| Blend modes | 35 | 35 | Named blend is registered |
| Shape modifiers | 9 | 9 | Named modifier is registered |
| Deformers | 14 | 14 | Named modifier is registered |
| Text animator presets | 24 | 24 | All schema presets; 31 total registered names including extras |
| Audio effects | 16 | 16 | Named effect is registered |
| Caption presets | 13 | 13 | Named preset is registered |
| Output codecs | 15 | 15 | Output selection is implemented; GIF is declared partial |
| **Total** | **269** | **269** | **Name coverage only** |

There are also 182 cross-cutting feature declarations, all labeled `full` in this
environment. They have no one-to-one denominator in the XSD. GIF's declared
palette/transparency limits are inherent to the format, not evidence that a
schema codec name is missing. Runtime dependencies and external asset contents
still matter. [coverage.py](../../../scenerender/coverage.py#L247) reports registry
declarations and explicitly says it does not verify attribute combinations or
external asset contents.

The following are **reproduced rendering and evaluation failures**. “High” means
ordinary valid input can silently produce materially wrong content; “medium”
means a narrower case, appearance defect, or crash on an exceptional input. These
are audit priorities, not security severity ratings. Fixture names correspond to
the generated files; observation keys are in the results snapshot.

| ID / priority | Schema contract and input | Observed behavior | Implementation and reproduction |
|---|---|---|---|
| R01 / High | Animation computes the targeted property; instance overrides set the target attribute. `visible=false`, animated `z=2`, animated `shape=ellipse`. [animation](../../../schema/scene-render-1.1.xsd#L311), [overrides](../../../schema/scene-render-1.1.xsd#L301) | Evaluator returns false but the shape paints 1,600 pixels; a visibility override also paints 1,600 pixels. Evaluated `z=2` stays behind `z=1`. Evaluated ellipse stays rectangular. | Raw attribute reads bypass the evaluator in [visibility](../../../scenerender/compositor.py#L223), [cached order](../../../scenerender/compositor.py#L162), [shape kind](../../../scenerender/nodes/core.py#L79). `visibility.xml`, `instance_visibility.xml`, `animated_z.xml`, `animated_shape.xml`. |
| R02 / High | Inside repeat, `param(@var)` is the current item. List `[10,40]`, `var=item`, `x=param('item')`. [repeat](../../../schema/scene-render-1.1.xsd#L1894) | Both copies render at x=0, instead of x=10 and x=40. | [run_expression](../../../scenerender/evaluator.py#L336) resolves `param()` only from document parameters. It exposes the repeat item as a bare variable instead. `repeat_param.xml`; `repeat_current_item`. |
| R03 / Medium | Repeat copy opacity is `opacity - i*opacityStep`. `opacity=.5`, `opacityStep=.25`. [repeat](../../../schema/scene-render-1.1.xsd#L1894) | First/second alpha are 128/96, approximately .5/.375, rather than 128/64, approximately .5/.25. | [repeat expansion](../../../scenerender/document.py#L424) leaves opacity on the parent and sets each child's opacity to `1-i*step`, producing multiplication. `repeat_opacity.xml`. |
| R04 / High | Repeat copies contain a shape parented to a sibling group. [node parent](../../../schema/scene-render-1.1.xsd#L1174), [repeat](../../../schema/scene-render-1.1.xsd#L1894) | IDs become `p#0`, `p#1`; shapes still reference nonexistent `p`. Parent x=20 is lost; the shape renders at x=0. | [repeat expansion](../../../scenerender/document.py#L454) rewrites IDs without rewriting references. Parent failure is rendered; other internal references require the same remapping but were not individually pixel-tested. `repeat_parent.xml`. |
| R05 / High | An included scene has a local image beside its own XML. [include](../../../schema/scene-render-1.1.xsd#L1879) | The image resolves from the including scene's directory, where it does not exist; the included layer paints zero pixels. The correct file exists beside the included scene. | [include expansion](../../../scenerender/document.py#L367) merges relative paths without retaining/rebasing their origin. Compare [document base handling](../../../scenerender/document.py#L115). `include_relative.xml`, `sub/relative.xml`. |
| R06 / High | Includes namespace the imported scene. Internal `link source='a.x'` and `prop('a.x')` should continue to identify its own node. [include](../../../schema/scene-render-1.1.xsd#L1879) | Imported `a` becomes `inc/a`; expression/link strings remain `a.x`. Source shape renders at x=30; both dependent shapes render at x=0 instead of x=30. | [namespace rewriting](../../../scenerender/document.py#L407) covers IDREFs and paint URLs, but not link source strings or expression references. `include_links.xml`, `sub/links.xml`. |
| R07 / High | Included styles are merged under the include namespace. Parent has blue `accent`; included scene has red `accent`. [include](../../../schema/scene-render-1.1.xsd#L1879) | Parent shape changes from blue to red. Only one unqualified `accent` token remains. | [include merge](../../../scenerender/document.py#L386), [token collection](../../../scenerender/document.py#L308): token names and `var(--name)` references are not namespaced. `include_tokens.xml`, `sub/tokens.xml`. |
| R08 / Medium | A scene includes itself. Recursive include handling must terminate meaningfully. | Loading raises `RecursionError: maximum recursion depth exceeded`. This is a robustness failure, not a claim that an infinite scene should render. | [include expansion](../../../scenerender/document.py#L367) checks a `depth` argument but recursively calls `load()` without incrementing or carrying it. `cycle.xml`; `include_cycle`. |
| R09 / High | Sequential children play in order; a media layer following a one-second intro should be .7 seconds into its source at t=1.7. [sequence](../../../schema/scene-render-1.1.xsd#L1838), [layer](../../../schema/scene-render-1.1.xsd#L1514) | Sequence image layer shows the red first half of its source; an equivalent direct layer starting at t=1 shows the blue second half. | [sequence scheduling](../../../scenerender/document.py#L506) sets a clock shift; [node rendering](../../../scenerender/compositor.py#L482) subtracts it; [media_time](../../../scenerender/nodes/core.py#L196) subtracts the scheduled start again. `sequence_media.xml` and `sequence_media_control.xml`. |
| R10 / High | A one-second video with `timeStretch=2` occupies two seconds when sequenced. [timeStretch](../../../schema/scene-render-1.1.xsd#L1540), [sequence](../../../schema/scene-render-1.1.xsd#L1838) | Prepared video window is [0,1]; the next child begins at t=1 instead of t=2. No media decoder is needed for this scheduling probe. | [natural_duration](../../../scenerender/document.py#L481) uses speed but omits timeStretch for video duration. `sequence_stretch.xml`. Other duration combinations are not established by this one test. |
| R11 / High | Conic gradient `units=user` uses node pixels. Center (50,50) in a 100×100 rectangle is equivalent to object center (.5,.5). [gradient units](../../../schema/scene-render-1.1.xsd#L529) | User-unit gradient paints only 62 pixels; object-unit control paints all 10,000. | [_conic](../../../scenerender/paint.py#L173) always uses radius 4, appropriate for the unit box but only four pixels in user space. `conic_user.xml`, `conic_object.xml`. |
| R12 / Medium | Conic gradient stops expose `midpoint`. [stop](../../../schema/scene-render-1.1.xsd#L514) | Changing midpoint from .1 to .9 changes zero rendered channels. | [_conic](../../../scenerender/paint.py#L184) discards the midpoint returned by `_stops`; linear/radial stop processing has midpoint logic. `conicGradient_midpoint_a.xml`, `conicGradient_midpoint_b.xml`. |
| R13 / Medium | Gradient `dither` is a boolean, default true. [gradient common](../../../schema/scene-render-1.1.xsd#L540) | `dither=true` and `false` produce identical low-contrast linear-gradient images. | [paint.py](../../../scenerender/paint.py) does not consume this attribute. The source inspection establishes the no-op; identical images alone would not prove it. `linearGradient_dither_a.xml`, `linearGradient_dither_b.xml`. |
| R14 / Medium | Radial `fx` can be supplied by animation without a static attribute. [radial](../../../schema/scene-render-1.1.xsd#L559), [animation](../../../schema/scene-render-1.1.xsd#L341) | Evaluated fx=.2 is ignored when the base attribute is absent. Adding redundant base `fx=.5` changes 15,218 output channels even though the same keyframe still evaluates to .2. | [_radial](../../../scenerender/paint.py#L158) checks raw attribute presence before evaluating fx/fy. The fx case is reproduced. `radial_no_base.xml`, `radial_base.xml`. |
| R15 / Medium | Node condition uses the expression language's truthiness. [condition](../../../schema/scene-render-1.1.xsd#L1171), [expression language](../../../schema/scene-render-1.1.xsd#L358) | Empty array `[]` is truthy in the implemented expression language but the conditional shape paints zero pixels. | [condition](../../../scenerender/evaluator.py#L170) uses Python `bool()` rather than expression truthiness. `condition.xml`; the probe also calls the language's `truthy([])` and gets true. |
| R16 / High | Adaptive motion blur must retain actual motion during the shutter. [project blur](../../../schema/scene-render-1.1.xsd#L2848) | A sinusoid with equal first/last samples paints 220 pixels with adaptive blur and 840 with full sampling; 2,700 output channels differ. | [frame_linear](../../../scenerender/render.py#L67) assumes equal endpoints imply all intermediate frames match. That assumption fails for return motion. `blur_adaptive.xml`, `blur_full.xml`. |
| R17 / Medium | Symbol child effective ID is `instanceId/innerId`. [instance](../../../schema/scene-render-1.1.xsd#L1853) | `prop('i/s.x')` cannot resolve; consumer renders at x=0 instead of x=30. `i/s` is absent from the index. | [prop_fn](../../../scenerender/evaluator.py#L343) only looks in `doc.ids`; [render_instance](../../../scenerender/nodes/core.py#L328) does not provide a scoped property resolver. `instance_scoped_reference.xml`. |

R01 is a shared implementation problem, not three isolated missing feature names.
The same raw-read pattern occurs for additional properties such as blend,
strokeCap, clip and reverse. The audit directly establishes the cases in R01 and
R14; it does not claim to have individually reproduced every other raw read.
The repository's own [handler contract](../../ARCHITECTURE.md#contracts-for-handlers)
requires evaluated reads so animations, links, expressions and overrides work.

There are also **validation and support-contract gaps**, distinguished from the
rendering failures above:

| ID | Finding | Evidence and limits |
|---|---|---|
| V01 | Strict mode does not propagate into includes. | A child with an unknown attribute is rejected by direct `document.load(..., strict=True)`, but is rendered by a strict parent include. [Nested load](../../../scenerender/document.py#L374) omits `strict`; the top-level API defaults it to false. `include_strict.xml`, deliberately invalid `sub/invalid.xml`; `include_strict` result. |
| V02 | The annotated version gate is not enforced. | The [XSD introduction](../../../schema/scene-render-1.1.xsd#L5) delegates version and cross-field rules to `scene-render-1.1.sch`. That file is absent and [Schema.validator](../../../scenerender/schema.py#L51) only constructs an XMLSchema validator. A version-1.0 scene containing repeat is accepted in strict mode and rendered. `version_gate.xml`. Other absent cross-field checks were not individually enumerated. |
| C01 | `alignItems=stretch` has no dedicated implementation. | [flex_layout](../../../scenerender/layout.py#L13) returns positions only; [_cross](../../../scenerender/layout.py#L88) handles center/end, with stretch falling through to start. Start/stretch controls are identical. The XSD calls this a flexbox subset but does not define fixed-size versus intrinsic-size stretch rules; the observation is firm, but an exact replacement sizing algorithm requires a documented interpretation. `group_stretch.xml`, `group_start.xml`. |
| C02 | Include `sha256` is not checked. | An all-zero declared digest still loads and renders a nonempty included scene. [Include expansion](../../../scenerender/document.py#L367) never reads it for verification and discards it when replacing the include. `include_hash.xml`. The XSD declares the digest but does not specify whether mismatch should warn or reject, so that policy is not asserted here. |

External-format support is also **bounded**, even with all native handlers present.
The following limits are established by the implementation/documentation, not
new end-to-end external-format reproducers in this audit:

| Input/backend | Current boundary | Source |
|---|---|---|
| Lottie | Text paths and expression selectors have implementations. Embedded `chars` glyph outlines are ignored in favor of Pango/font shaping. General Lottie expressions are not evaluated; selector expressions use the scene's pure expression subset. | [assets/lottie.py](../../../scenerender/assets/lottie.py#L38) |
| glTF / GLB | Reads UV/color/skin set 0 only; textures specifying higher UV sets use UV0. Skips point/line primitives, KHR_animation_pointer channels and undecodable KTX2/BasisU textures. Padded byte/short matrix accessors unsupported. | [three/loaders.py](../../../scenerender/three/loaders.py#L14) |
| USD / USDZ | Texture scale/bias and UsdTransform2d ignored; blend shapes not read; skin weights reduced to four influences; sampled transforms lose shear when decomposed. | [three/loaders.py](../../../scenerender/three/loaders.py#L34) |
| IES / HDR | IES tilt factors are not applied; photometric types A/B are treated as C. Old-style Radiance RLE is unsupported. | [three/loaders.py](../../../scenerender/three/loaders.py#L70) |
| Tracking | JSON mask paths accept one straight-segment subpath (M/L/H/V/Z). FBX 6 Takes layout and FBX curve velocity flags are unsupported. | [tracking.py](../../../scenerender/tracking.py#L145), [mask parser](../../../scenerender/tracking.py#L503) |
| ASS captions | Several tags are warned and ignored, including animated transforms, clipping, drawings and some 3D/skew/border controls. | [captions.py](../../../scenerender/captions.py#L80) |
| ISF shader conventions | Image inputs beyond the second are transparent; persistent buffers start empty each frame. These are external shader-format limits, not missing XSD effect names. | [effects/shader.py](../../../scenerender/effects/shader.py#L644) |
| Missing OpenGL/EGL | Shader effects pass through; shader transitions crossfade; 3D can use a limited flat CPU fallback. A `full` registry label does not prove a usable GL runtime. | [effects/shader.py](../../../scenerender/effects/shader.py#L598), [transitions/shader.py](../../../scenerender/transitions/shader.py#L110), [nodes/scene3d.py](../../../scenerender/nodes/scene3d.py#L66) |
| Missing optional/system dependencies | SVG falls back to a subset without librsvg. Formula typesetting falls back from LaTeX to mathtext/Pango. Data Matrix/PDF417 need zxing-cpp; USD/FBX/EXR/MaterialX need their associated backends. | [vector.py](../../../scenerender/assets/vector.py#L240), [formula.py](../../../scenerender/assets/formula.py#L1), [pyproject.toml](../../../pyproject.toml#L11) |

The remaining major areas have concrete implementations and existing tests:

| Area | Source and existing test evidence | Audit conclusion |
|---|---|---|
| Typed values, keyframes, curves, expressions, links, paths | `schema.py`, `values.py`, `anim.py`, `curves.py`, `expr.py`, `evaluator.py`; `test_core.py`, `test_expr.py`, `test_schema_conformance.py` | Substantial implementation; shared evaluation and scope failures above prevent a blanket claim. |
| Shapes, masks/mattes, blend modes, shape modifiers, deformation | `geometry.py`, `masks.py`, `blend.py`, `modifiers.py`, `deform.py`; core/blend/modifier/effect tests | Named handlers are present and existing tests pass; arbitrary combinations are not certified. |
| Native text, spans, typography, text animators and paths | `assets/text.py`, `text_animators.py`; text/international/font/text-3D tests | Extensive implementation; external Lottie/ASS limits are separate. |
| Images, video, image sequences, generated assets, charts, codes, formulas | `assets/`; asset/video/color/code tests | Existing asset tests pass; include path and sequence timing failures affect otherwise supported media. |
| Cameras, 2.5D/3D, lights/materials, 360/stereo | `camera.py`, `nodes/scene3d.py`, `three/`; camera/3D/loaders/360/spherical tests | Implemented with backend and imported-format limits; no blanket external-file compatibility. |
| Particles, rigid/soft bodies, physics constraints, rigging, tracking | `nodes/particles.py`, `physics.py`, `constraints.py`, `deform.py`, `tracking.py`; particle/physics/constraint/tracking tests | Existing tests pass; repeat reference copying can break cross-node behavior. |
| Audio effects, routing, ducking, normalization, spatial/ambisonic audio | `audio/`; `test_audio.py`, `test_audio_full.py`, delivery tests | 16/16 named effects registered and existing tests pass. This audit did not add an independent perceptual/reference comparison for every DSP parameter. |
| Color management, HDR and output delivery | `color.py`, `output.py`, `mxf.py`; color/management/output/delivery/MXF/QA tests | Existing tests pass, including earlier MXF fixes. No new live cloud publication was performed; publication tests use local servers/mocks where applicable. |
| Metadata, parameters, variants, layouts, safe areas, symbols and reuse | `document.py`, `layout.py`, `safe_areas.py`, `evaluator.py`, `nodes/core.py` | Multiple demonstrated gaps in reusable documents, repeat data and scoped references. |

Recommended repair order is the shared evaluator and scoping paths first, then
include/repeat preparation and sequence clocks, then gradient/motion-blur
correctness and validation propagation. Each reproduced case should become a
regression assertion for the intended behavior. Registry labels and the existing
suite can remain useful inventories, but a full-support claim needs a conformance
matrix covering attributes, defaults, nested scopes, time mapping and supported
external-format subsets.
