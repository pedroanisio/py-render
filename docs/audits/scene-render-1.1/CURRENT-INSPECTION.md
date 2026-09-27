**Inspection snapshot: no, the inspected codebase does not fully implement scene-render 1.1.**
It has broad feature coverage and a passing existing test suite, but new,
XSD-valid scenes reproduce 12 rendering/evaluation failures. There is also an
unfinished version gate, an unused data-source hash property, and documented
external-format limitations. Registration of every feature name is not evidence
that every property, default, scope and combination works.

Implementation followed this inspection. See
[SCHEMA-IMPLEMENTATION.md](../../SCHEMA-IMPLEMENTATION.md) and
[implementation-results.json](implementation-results.json) for repaired cases and
remaining work. The evidence and failures below describe the inspected commit.

Inspected on 2026-09-27 against commit
`3510103590da132390da9687b74760ae6bdc76ac`. The renderer sources were not changed
by this inspection. This report supersedes the **current-status conclusions** of
the [earlier audit](README.md); its historical results remain available.
Schema SHA-256: `3d0ecb499bc682ab69405a7f57dccfe1a659abbc3dbcb5a33897a17e28b3c603`.

**Inspection scope and execution evidence**

The inspection rebuilt the complete declaration inventory, followed its resolved
type graph, compared schema alternatives with registries, reviewed shared
evaluation/preparation paths and feature handlers, ran the complete existing
suite, reran the earlier audit probes, and added targeted rendered/audio
counterexamples. The inventory preserves anonymous declarations, inheritance,
defaults, facets, enumerations and child cardinalities. Its declaration and
resolved-type sections match the existing [inventory.json](inventory.json);
the static source-read index has been refreshed.

| Schema inventory | Count |
|---|---:|
| Element declarations, including repeated declarations | 131 |
| Attribute declarations, including repeated declarations | 1,151 |
| Distinct attribute names | 623 |
| Complex-type declarations | 116 |
| Simple-type declarations | 167 |
| Group declarations, including references | 47 |
| Attribute-group declarations, including references | 34 |
| Types reached through the runtime element graph | 114 |
| Named interpolation curves | 41 |

These counts are inventory sizes, not independently verified requirements or a
percentage of semantic conformance. A source-string hit is only a review lead:
some handlers dispatch dynamically, while some literal reads refer to unrelated
dictionaries. Every declaration combination and every possible imported file has
not been tested.

| Execution | Result |
|---|---|
| Complete existing pytest suite in the sandbox | 1,517 passed; 2 failed and 15 setup errors caused by access restrictions; 75.69 seconds |
| All 17 affected tests rerun outside the sandbox | 17 passed; 3.13 seconds |
| Existing suite, accounting for that rerun | **1,534 tests passed; no unresolved failure or skip reported** |
| Earlier audit runner, rerun on current source | 35 XSD-valid fixtures; no probe errors |
| New diagnostic runner | 24 XSD-valid fixtures; no probe errors; 12 reproduced rendering/evaluation failures plus the data-hash observation |

The restricted run could not bind local HTTP test servers, write USD temporary
files under `/var/tmp`, or connect MaterialX to the graphics display. Its failures
were environmental, as confirmed by rerunning exactly the affected tests. No
live publication to a remote service was performed. Existing output tests cover
encoding, metadata, delivery and mocked/local publication paths.

Runtime: Python 3.12.3, Pycairo 1.25.1, Cairo 1.18.0, OpenGL available through
`llvmpipe (LLVM 20.1.2, 256 bits)`. The new 3D probes used the GL renderer.

**Named-feature coverage**

| Registry family | Schema names | Present |
|---|---:|---:|
| Nodes | 13 | 13 |
| Assets | 15 | 15 |
| Effects | 80 | 80 |
| Transitions | 35 | 35 |
| Blend modes | 35 | 35 |
| Shape modifiers | 9 | 9 |
| Deformers | 14 | 14 |
| Text animator presets | 24 | 24 |
| Audio effects | 16 | 16 |
| Caption presets | 13 | 13 |
| Output codecs | 15 | 15 |
| **Total** | **269** | **269** |

All these names are registered in this environment. Text animators also register
seven extra names. There are 182 additional cross-cutting declarations, all
labeled `full`, without a one-to-one denominator in the schema. GIF is labeled
`partial` because of its palette/transparency limits; those format limits do not
mean a schema codec is absent. The
[coverage command](../../../scenerender/coverage.py#L247) reports registry labels
and explicitly says it does not verify attribute combinations or external
contents. The failures below occur despite those positive labels.

**Current rendering and evaluation failures**

The IDs below are new findings, separate from the earlier audit's R01–R17.
“High” prioritizes silent wrong output in reusable content or ordinary controls;
“medium” identifies narrower property combinations. These are repair priorities,
not security severity ratings. Each case was loaded with `strict=True` after
independent XSD validation. The probe compares an evaluated or instanced form
with equivalent static/direct content where applicable.

| ID / priority | Schema requirement and reproducer | Actual result | Cause and source |
|---|---|---|---|
| N01 / High | A [symbol](../../../schema/scene-render-1.1.xsd#L2020) can contain a shape with a rigid body, and [instances](../../../schema/scene-render-1.1.xsd#L1853) instantiate that content. `physics_in_symbol.xml` versus `physics_direct.xml`. | The symbol body remains at y=10 at t=.5; the direct body falls to y=22. The simulator discovers **zero** bodies in the symbol. | [PhysicsSim](../../../scenerender/physics.py#L904) scans only descendants of the composition; it does not expand symbol instances. |
| N02 / High | Symbols can contain [cameras](../../../schema/scene-render-1.1.xsd#L1745). A symbol camera with orthographic height 50 should affect its 20×20 object. `camera_in_symbol.xml` versus `camera_direct.xml`. | No symbol camera is discovered. The object covers 400 pixels; the direct-camera control covers 1,600. | [cameras()](../../../scenerender/camera.py#L507) only walks the composition tree. |
| N03 / High | Instance overrides must affect [only that instance](../../../schema/scene-render-1.1.xsd#L1853). Two copies override object x to −20 and +20. `instance_3d_cache.xml`. | Evaluated values are −20/+20, but cached world positions are **−20/−20**. Only one square is visible; the direct control has two. | [world3d](../../../scenerender/camera.py#L360) omits scope from its cache key. The [shared 3D world](../../../scenerender/three/scene.py#L348) and [frame](../../../scenerender/three/scene.py#L399) keys also omit instance scope. |
| N04 / High | An instance override changes a symbol child's `start` attribute to 1. `instance_start.xml`, rendered at t=.5. | The evaluator returns start=1, but the shape already paints **400 pixels**, instead of zero. | [active()](../../../scenerender/compositor.py#L238) reads [Document.window](../../../scenerender/document.py#L135), which uses the prepared/raw timing, bypassing the override. |
| N05 / High | An instance supplies `object3D.width=20` where no base width is authored. `instance_width.xml` versus `instance_width_static.xml`. | The plane remains width 100, using radius-derived geometry: **2,000 pixels instead of 400**. | [primitive_geo](../../../scenerender/three/scene.py#L58) checks raw attribute/animation presence before consulting the evaluator; scope overrides do not satisfy the check. |
| N06 / Medium | `object3D` accepts [animation elements](../../../schema/scene-render-1.1.xsd#L1681). A constant primitive key selects sphere from base box. `primitive_animated.xml`. | Evaluator: sphere. Geometry handler: box. Output differs from the static sphere in **7,923 channels**. | [primitive_geo](../../../scenerender/three/scene.py#L60) and [build_object](../../../scenerender/three/scene.py#L262) read raw `primitive`. |
| N07 / High | A constant `object3D.material` key selects blue instead of red. `material_animated.xml`. | Evaluator: blue. Rendered center: **[255,0,0]**; static control: **[0,0,255]**. | [material_for](../../../scenerender/three/materials.py#L406) reads raw `material` despite its docstring saying it is evaluated. |
| N08 / Medium | Camera `orthoHeight=50` is supplied by a key without a base attribute. `camera_ortho_animated.xml`. | Evaluator: 50. Projection scale: **1 instead of 2**; 400 painted pixels versus 1,600 in the static control. | [build_camera](../../../scenerender/camera.py#L588) only evaluates the property if the raw attribute exists. |
| N09 / High | An instance overrides a particle emitter preset from rain to snow. `particle_preset_override.xml`. | Evaluator: snow. Emitter speed defaults remain **1,200 instead of snow's 60**. Output differs in 2,507 channels. | [Params](../../../scenerender/nodes/particles.py#L127) constructs preset defaults from raw `preset`, ignoring the scope override. |
| N10 / High | [audioEffect](../../../schema/scene-render-1.1.xsd#L2361) accepts animation children. A gain effect of −20 dB has a constant `enabled=false` key. `audio_effect_enabled.xml`. | Evaluator: false. The effect still runs, yielding **0.1×** the RMS amplitude of the disabled static control. | [process_chain](../../../scenerender/audio/effects.py#L518) reads raw `enabled` before constructing the evaluated effect context. |
| N11 / High | [transformConstraint](../../../schema/scene-render-1.1.xsd#L474) accepts animation children. A copy-position constraint animates its target from a at x=10 to b at x=60. `constraint_target_animated.xml`. | Evaluator: b. Shape remains at **x=10 instead of x=60**. | [apply_constraint](../../../scenerender/constraints.py#L130) reads raw `target`; it also selects type/space from raw attributes. Only target was reproduced here. |
| N12 / Medium | A [deform modifier](../../../schema/scene-render-1.1.xsd#L1236) has a constant type key selecting twist from wave. `deform_type_animated.xml`. | Evaluator: twist. Dispatch remains wave; output differs from static twist in **2,984 channels**. | [build_maps](../../../scenerender/deform.py#L785) selects its handler using raw `type`. |

These are consumer failures: in several probes the shared evaluator already
returns the intended value. Fixing only the evaluator will not repair handlers
that bypass it, discovery passes that omit symbol contents, or caches that omit
instance identity. N01/N02 also show that having a handler for both individual
features does not establish support for their schema-valid composition.

Further source-review leads include raw material texture-map selection in
[materials.evaluate](../../../scenerender/three/materials.py#L126), camera
target/focus/shutter presence checks, and other constraint selector reads. They
are not counted as additional reproduced failures in this report.

**Validation and declared-property gaps**

| ID | Current state | Evidence and qualification |
|---|---|---|
| V02, still open | The version gate described in the [schema introduction](../../../schema/scene-render-1.1.xsd#L5) is not implemented. | A Schematron file now exists and runs, but its [comment](../../../schema/scene-render-1.1.sch#L4) explicitly defers the version gate and none of its four rules checks `@version`. The previous version-1.0/repeat probe still loads. An authoritative 1.0 inventory is needed to define exactly which constructs require 1.1; this report does not invent that inventory. |
| N13 | `parameters/data/@sha256` has no effect. | `data_hash.xml` declares an all-zero digest for a JSON file containing `[10,40]`; strict loading accepts and uses both rows. [_load_data](../../../scenerender/document.py#L332) never reads the digest. The XSD declares the property without specifying warning-versus-rejection policy, so this is an unused-property finding rather than an asserted mandatory rejection policy. |

The four new Schematron rules cover text source exclusivity, pin constraints
without body b, transcription cache/hash pairing, and image-sequence range order.
Their tests passed. Strict validation now propagates into includes, include
cycles fail with a meaningful `SceneError`, and include hash mismatches are
rejected. Those earlier gaps should not be described as still broken.

**Earlier findings that now pass their probes**

The refreshed observations are saved in [rechecked-results.json](rechecked-results.json).
For the tested cases, earlier **R01–R17, V01, C01 and C02** are repaired. This
does not certify all related combinations.

| Area | Current observed result |
|---|---|
| Visibility, z, shape, condition | Hidden/overridden-hidden shapes paint zero pixels; animated z and ellipse shape affect pixels; the truthy-array condition paints as expected. |
| Repeat variables, opacity and references | List copies start at x=10/40; alpha is 128/64; copied parent references point to their own copies and apply x=20. |
| Include origins and namespaces | The child-local image resolves and paints 400 pixels; links/expressions all resolve x=30; parent and imported color tokens remain separate. |
| Include failures | Cycles produce `SceneError`; strict invalid children are rejected; incorrect include hashes are rejected. |
| Sequence clocks and duration | The sequenced image matches the blue direct-layer control; stretched video occupies [0,2] and the next child starts at 2. |
| Gradients | Conic user coordinates cover all 10,000 pixels; midpoint and dither changes affect output; animated radial focus works without a redundant base attribute. |
| Adaptive motion blur | The return-motion case matches full sampling exactly: 840 painted pixels and zero differing channels. |
| Scoped property references and layout stretch | `prop('i/s.x')` resolves to 30; stretch changes the layout and covers 1,600 pixels in the probe. |

Existing regression evidence includes `test_document_scopes.py`,
`test_evaluated_nodes.py`, `test_paint_layout_conformance.py`, and
`test_semantic_validation.py`. Earlier Lottie text and MXF metadata regressions
also passed in the full suite.

**External-format and runtime boundaries**

These are limits documented in the current source and reviewed here; this
inspection did not build a new external-file reproducer for each one. They are
separate from the native XML failures above. Merely accepting an external file
format name does not promise every feature of that format.

| Input/backend | Current boundary | Source |
|---|---|---|
| Lottie | Embedded `chars` outlines are ignored in favor of font shaping. General Lottie expressions are not evaluated; expression selectors use the scene expression subset. | [lottie.py](../../../scenerender/assets/lottie.py#L38) |
| glTF/GLB | UV/color/skin set 0 only; higher texture-coordinate sets use UV0; point/line primitives skipped; padded byte/short matrix accessors unsupported; animation-pointer channels ignored; KTX2/BasisU textures skipped when undecodable. | [loaders.py](../../../scenerender/three/loaders.py#L14) |
| USD/USDZ | Texture scale/bias and `UsdTransform2d` ignored; blend shapes not read; skin weights reduced to four; sampled transform decomposition drops shear. | [loaders.py](../../../scenerender/three/loaders.py#L34) |
| IES and Radiance HDR | Tilt factors not applied; IES types A/B treated as C; old-style Radiance RLE unsupported. | [loaders.py](../../../scenerender/three/loaders.py#L71) |
| Tracking | JSON mask paths support a single M/L/H/V/Z subpath; FBX 6 Takes and FBX curve velocity handling are unsupported. | [tracking.py](../../../scenerender/tracking.py#L145), [path parser](../../../scenerender/tracking.py#L506) |
| ASS captions | Animated transform tags, clipping, drawings and several 3D/skew/border tags are warned and ignored. | [captions.py](../../../scenerender/captions.py#L80) |
| ISF shaders | Image inputs beyond two are transparent; persistent buffers start empty on every frame. | [shader.py](../../../scenerender/effects/shader.py#L642), [buffers](../../../scenerender/effects/shader.py#L666) |
| Missing OpenGL | Shader effects pass through; shader transitions crossfade; 3D uses a limited CPU fallback. | [shader effect](../../../scenerender/effects/shader.py#L596), [transition](../../../scenerender/transitions/shader.py#L110), [3D node](../../../scenerender/nodes/scene3d.py#L56) |
| Optional/system dependencies | SVG degrades without librsvg; formula support depends on the LaTeX/mathtext backend; Data Matrix/PDF417 and some 3D formats require extras. Gradient dithering directly uses Cairo's floating-point surface API; no older-runtime fallback was verified. | [vector.py](../../../scenerender/assets/vector.py#L240), [formula.py](../../../scenerender/assets/formula.py#L370), [pyproject.toml](../../../pyproject.toml#L11), [paint.py](../../../scenerender/paint.py#L117) |

**Assessment by subsystem**

| Subsystem | Assessment and evidence |
|---|---|
| Schema parsing, typing, defaults and validation | Implemented and tested; version/cross-field support remains incomplete as above. |
| Animation, expressions, links and scoped values | Shared implementation is extensive; consumer and cache failures N03–N12 prevent complete support. Core/expression/scoped-node tests pass. |
| Shapes, paints, masks, blends, shape modifiers | Named implementations and tests pass; earlier paint/evaluation defects are repaired. No exhaustive combinations or reference-render comparison was performed. |
| Reuse, includes, repeats, sequencing and layouts | Many recent repairs verified; instance timing, 3D, physics and camera gaps remain. |
| Text, typography, spans and text animators | Extensive native text/international/font/text-3D tests pass; Lottie and subtitle import have the separate limits listed above. |
| Images, video, sequences, vectors, generators, charts, codes, formulas, generated assets | All asset names present and asset tests pass. Dependency/import limits apply; generated-cache tests verify missing/changed cache content is rejected. |
| 3D, lights, materials, camera, 360 and stereo | Substantial rendering and import tests pass; N02/N03/N05–N08 prevent full native conformance. External-file coverage is bounded. |
| Particles, physics, constraints, deformation and rigging | Existing family tests pass; N01/N09/N11/N12 demonstrate missing combinations and evaluated selectors. |
| Tracking and stabilization | Existing tests pass; imported tracking formats have documented subsets. This audit adds no exhaustive tracking-reference comparison. |
| Audio routing, effects, automation, spatial audio and normalization | All 16 effect names and existing audio tests pass; N10 proves an automation gap. Perceptual/reference DSP certification was not performed. |
| Captions, metadata, safe areas and accessibility | Existing format/QA/delivery tests pass; subtitle-format boundaries remain. |
| Color management, HDR, codecs and publication | Existing color/output/MXF tests pass in the usable environment. No claim of live cloud interoperability or every codec/container/attribute combination. |
| Parameters, variants and data | Existing parameter/scope tests pass; the data-source digest is unused. |

**Reproduction and next implementation work**

From the repository root:

```bash
MPLCONFIGDIR=/tmp/py-render-audit-mpl .venv/bin/python -m pytest -q
MPLCONFIGDIR=/tmp/py-render-audit-mpl .venv/bin/python -m pytest -q --lf
MPLCONFIGDIR=/tmp/py-render-audit-mpl .venv/bin/python docs/audits/scene-render-1.1/probe.py
MPLCONFIGDIR=/tmp/py-render-audit-mpl .venv/bin/python docs/audits/scene-render-1.1/current_probe.py
.venv/bin/python docs/audits/scene-render-1.1/inventory.py
```

The second command was run outside the sandbox after the first run's access
failures; `--lf` uses pytest's recorded failures. The audit runners are
diagnostics, not assertions that the observed bugs are intended behavior. Their
successful exit means the runner completed. Fresh XML/PNG/JSON artifacts are
written under `/tmp/scene-render-schema-audit` and
`/tmp/scene-render-current-inspection`. The new result snapshot is
[current-results.json](current-results.json); its producer is
[current_probe.py](current_probe.py). The inventory command refreshes the checked-in
inventory file.

The first repair priority is symbol-aware camera/physics discovery and complete
instance identity in 3D caches, followed by evaluated timing and property reads
in every consumer. Convert these counterexamples into regression assertions for
the intended outputs. Then complete the version gate from the authoritative
1.0 contract, make hash/dependency support explicit, and either implement the
external-format gaps or declare their supported subsets accurately. Full-support
claims require that work and requirement-level evidence beyond registry counts
and the existing passing suite.
