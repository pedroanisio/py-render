# Work packages for phase 4: MAPS, RESOLVE, SIM2, CINE

Planning only. Built on the parity matrix, the phase-2/3 plan ([WORKPACKAGES-P2-P3.md](WORKPACKAGES-P2-P3.md)) and the evidence baseline ([EVIDENCE-BASELINE.md](EVIDENCE-BASELINE.md)). These four areas are the ones the matrix marks almost entirely MISSING (MAPS 33/34, RESOLVE 25/32, SIM2 32/42, CINE 31/38) and nearly all of them are schema-gated: **Bob's phase-1 contract (vendored 1.1.3+1.2+1.3 XSD/Schematron, diagnostics) lands first**. Machine-readable twin: [WORKPACKAGES-P4.json](WORKPACKAGES-P4.json). Sizes: S=0.5, M=2, L=5 days. Rust is the spec; where Rust looks wrong the package mirrors its observable behaviour and the observation is under *Rust possibly wrong* (for docs/parity/RUST-ISSUES.md).

## Summary

| Area | Packages | Est. days | Tracks | Evidence cases it should turn native |
|---|---:|---:|---|---|
| MAPS | 27 | 57 | P4-M1, P4-M2, P4-M3, P4-M4 | world-map, basemap, map-3d |
| RESOLVE | 18 | 25.5 | P4-R1, P4-R2, P4-R3, P4-R4, P4-R5 | none (resolve gate rig, see area notes) |
| SIM2 | 34 | 69.5 | P4-S1, P4-S2, P4-S3, P4-S4 | fluid, flocking, erosion-slime, erosion, clay-3d, path-tracing |
| CINE | 43 | 126.5 | P4-C0, P4-C1, P4-C2, P4-C3, P4-C4, P4-C5, P4-C6, P4-C7 | none in cases.json: packages bring their own gates |
| **All** | **122** | **278.5** | 21 tracks | |

Every non-PRESENT row of the four areas is in a package or in *Deferred* (uncovered rows: 0). 72 of 122 packages need BOB-PHASE1 first; 11 can start on today's schema with no dependency: P4-MAPS-00, P4-RESOLVE-00, P4-RESOLVE-01, P4-RESOLVE-02, P4-SIM2-00, P4-SIM2-01, P4-CINE-00, P4-CINE-01, P4-CINE-13, P4-CINE-16, P4-CINE-32.

## Parallel tracks

Each track is one agent's work; inside a track follow `depends_on`. Tracks within an area own disjoint Python files except for the hub files below.

| Track | Area | Packages | Est. days | Python files owned |
|---|---|---|---:|---|
| P4-M1 | MAPS | P4-MAPS-02 .. P4-MAPS-09 (8) | 16 | scenerender/geo/data.py, scenerender/geo/sphere.py, scenerender/geo/project.py, scenerender/geo/clip.py, scenerender/geo/view.py, scenerender/geo/camera.py, scenerender/geo/__init__.py, tests/test_geo_{data,sphere,project,clip,view,d3_parity}.py |
| P4-M2 | MAPS | P4-MAPS-00 .. P4-MAPS-13 (6) | 7.5 | scenerender/geo/model.py, scenerender/assets/map.py (hub shared with M3), scenerender/assets/map_draw.py, scenerender/assets/__init__.py (one line), tools/parity/maps_parity.py, tools/parity/scenes/maps/*, tests/fixtures/geo/*, scenerender/data/styles/*, tests/test_{geo_model,map_render,map_choropleth,map_route,geo_expr,geo_fixtures}.py |
| P4-M3 | MAPS | P4-MAPS-14 .. P4-MAPS-21 (8) | 23.5 | scenerender/geo/tiles.py, scenerender/geo/pmtiles.py, scenerender/geo/mvt.py, scenerender/geo/style.py, scenerender/geo/basemap_vector.py, scenerender/geo/labels.py, scenerender/geo/basemap_raster.py, scenerender/geo/tileplan.py, tests/test_{geo_pmtiles,geo_mvt,geo_tiles_select,geo_style,basemap_vector,basemap_labels,basemap_raster,map_attribution,geo_tileplan}.py |
| P4-M4 | MAPS | P4-MAPS-22 .. P4-MAPS-26 (5) | 10 | scenerender/geo/drape.py, scenerender/geo/dem.py, scenerender/three/maps3d.py, tests/test_map3d_*.py, tests/test_map_drape.py |
| P4-R1 | RESOLVE | P4-RESOLVE-00 .. P4-RESOLVE-06 (5) | 4 | tools/parity/example_provider.py, tools/parity/resolve_parity.py, tools/parity/scenes/resolve/*, scenerender/resolve/__init__.py, scenerender/resolve/protocol.py, scenerender/resolve/docedit.py, scenerender/resolve/content.py, scenerender/resolve/targets.py, tests/test_resolve_harness.py, tests/test_resolve_protocol.py, tests/test_resolve_docedit.py, tests/test_resolve_content.py, tests/test_resolve_targets.py |
| P4-R2 | RESOLVE | P4-RESOLVE-03 .. P4-RESOLVE-09 (4) | 6.5 | scenerender/resolve/safety.py, scenerender/resolve/store.py, scenerender/resolve/engine.py, scenerender/resolve/cli.py, tests/test_resolve_safety.py, tests/test_resolve_engine.py, tests/test_resolve_cli.py, docs/parity/CLI.md |
| P4-R3 | RESOLVE | P4-RESOLVE-05 .. P4-RESOLVE-15 (4) | 6.5 | scenerender/resolve/providers/__init__.py, scenerender/resolve/providers/process.py, scenerender/resolve/providers/external.py, scenerender/resolve/providers/whisper.py, scenerender/resolve/providers/piper.py, scenerender/resolve/providers/audioforge.py, scenerender/resolve/providers/cloud.py, tests/test_resolve_providers.py, tests/test_resolve_whisper.py, tests/test_resolve_local_providers.py, tests/test_resolve_cloud.py |
| P4-R4 | RESOLVE | P4-RESOLVE-10, P4-RESOLVE-11, P4-RESOLVE-12 | 4.5 | scenerender/resolve/transcribe.py, tests/test_resolve_transcribe.py |
| P4-R5 | RESOLVE | P4-RESOLVE-16, P4-RESOLVE-17 | 4 | scenerender/resolve/providers/tiles.py, scenerender/resolve/pmtiles_write.py, scenerender/resolve/tileset.py, tests/test_resolve_tiles.py, tests/test_resolve_tileset.py |
| P4-S1 | SIM2 | P4-SIM2-00 .. P4-SIM2-10 (11) | 19 | scenerender/sim/*, scenerender/nodes/sims.py, tools/parity/sim_gate.py, tools/parity/scenes/sim2/* (2D-sim scenes), docs/parity/SIM2-STATUS.md, tests/test_sim_*.py |
| P4-S2 | SIM2 | P4-SIM2-11, P4-SIM2-12, P4-SIM2-13 | 6 | scenerender/three/clay.py, tests/test_clay_*.py |
| P4-S3 | SIM2 | P4-SIM2-14 .. P4-SIM2-20 (7) | 12.5 | scenerender/three/prepass.py, scenerender/three/ssao.py, scenerender/three/ssr.py, tests/test_3d_prepass.py, tests/test_3d_ssao.py, tests/test_3d_contact_shadows.py, tests/test_3d_ssr.py, tests/test_3d_cascades.py, tests/test_3d_shadow_budget.py, tests/test_3d_shadow_filter.py |
| P4-S4 | SIM2 | P4-SIM2-21 .. P4-SIM2-33 (13) | 32 | scenerender/three/pathtrace.py, scenerender/three/pathtrace.comp, scenerender/three/pathtrace_scene.py, scenerender/three/bvh.py, tests/test_pathtrace_*.py |
| P4-C0 | CINE | P4-CINE-00 | 2 | tools/parity/cine_stats.py, tools/parity/scenes/cine/*.scene.xml, tools/parity/scenes/cine/gen_fixtures.py, tools/parity/scenes/cine/scenes.json |
| P4-C1 | CINE | P4-CINE-01 .. P4-CINE-07 (7) | 17 | scenerender/assets/volume.py, scenerender/cine/__init__.py, scenerender/cine/volume/__init__.py, scenerender/cine/volume/advection.py, scenerender/cine/volume/blend.py, scenerender/cine/volume/grid.py, scenerender/cine/volume/openvdb.py, scenerender/cine/volume/sequence.py, scenerender/cine/volume/srvol.py, scenerender/cine/volume/srvseq.py, scenerender/cine/volume/vdb_codec.py, tests/fixtures/openvdb/ |
| P4-C2 | CINE | P4-CINE-08 .. P4-CINE-12 (5) | 16 | scenerender/cine/render/__init__.py, scenerender/cine/render/blackbody_table.py, scenerender/cine/render/medium.py, scenerender/cine/render/thermal.py, scenerender/cine/render/uhd.py, scenerender/cine/render/volume_gl.py, scenerender/cine/render/volume_glsl.py, scenerender/cine/render/volume_pt.py, tools/parity/cine_uhd_probe.py |
| P4-C3 | CINE | P4-CINE-13 .. P4-CINE-20 (8) | 20.5 | scenerender/cine/geo/__init__.py, scenerender/cine/geo/bvh.py, scenerender/cine/geo/closed_mesh.py, scenerender/cine/sim/__init__.py, scenerender/cine/sim/bake.py, scenerender/cine/sim/clocks.py, scenerender/cine/sim/errors.py, scenerender/cine/sim/fields3d.py, scenerender/cine/sim/pyro.py, scenerender/cine/sim/pyro_inputs.py, scenerender/cine/sim/pyro_node.py, scenerender/cine/sim/pyro_sources.py, scenerender/cine/sim/rng.py, scenerender/cine/sim/timeline.py |
| P4-C4 | CINE | P4-CINE-21 .. P4-CINE-24 (4) | 15.5 | scenerender/cine/render/particles3d_node.py, scenerender/cine/render/particles3d_pt.py, scenerender/cine/sim/particles3d.py, scenerender/cine/sim/particles3d_collide.py, scenerender/cine/sim/particles3d_mesh.py |
| P4-C5 | CINE | P4-CINE-25 .. P4-CINE-32 (8) | 20.5 | scenerender/assets/mesh_sequence.py, scenerender/cine/geo/crater.py, scenerender/cine/geo/mesh_blend.py, scenerender/cine/geo/mesh_sequence.py, scenerender/cine/geo/terrain.py, scenerender/cine/render/ocean_node.py, scenerender/cine/render/whitewater_mesh.py, scenerender/cine/sim/bathymetry.py, scenerender/cine/sim/ocean.py, scenerender/cine/sim/ocean_flux.py, scenerender/cine/sim/ocean_impulse.py, scenerender/cine/sim/ocean_waves.py, scenerender/cine/sim/whitewater.py |
| P4-C6 | CINE | P4-CINE-33 .. P4-CINE-37 (5) | 19 | scenerender/cine/geo/fracture.py, scenerender/cine/geo/fracture_cut.py, scenerender/cine/geo/fracture_surface.py, scenerender/cine/geo/fracture_validate.py, scenerender/cine/render/fracture_node.py, scenerender/cine/sim/fracture_world.py, scenerender/cine/sim/solid_colliders.py |
| P4-C7 | CINE | P4-CINE-38 .. P4-CINE-42 (5) | 16 | scenerender/cine/budgets.py, scenerender/cine/diagnostics.py, scenerender/cine/sim/crater_integration.py, scenerender/cine/sim/crater_pyro.py, tests/fixtures/corpus_cine/, tests/test_cine_corpus.py, tools/parity/cine_film.py, tools/parity/scenes/cine/impact-film.manifest.json, tools/parity/scenes/cine/impact-film.scene.xml, tools/parity/validation_corpus.py |

**Shared files (merge-conflict risk).** Edit different functions, keep commits small; whoever lands second rebases.

| File | Packages by track |
|---|---|
| pyproject.toml | P4-S1: SIM2-01 (numba decision); BOB/packaging: any; CINE: CINE-07 |
| scenerender/assets/__init__.py | RESOLVE: RESOLVE-04 (optional probe helper), RESOLVE-12; CINE: CINE-02 |
| scenerender/assets/map.py | MAPS: MAPS-01, MAPS-10, MAPS-17, MAPS-19, MAPS-20 |
| scenerender/assets/map_draw.py | MAPS: MAPS-10, MAPS-11, MAPS-12 |
| scenerender/captions.py | RESOLVE: RESOLVE-10, RESOLVE-12 (shared with the TEXT area) |
| scenerender/cine/render/volume_object.py | CINE: CINE-09, CINE-18 |
| scenerender/cine/sim/colliders.py | CINE: CINE-22, CINE-37, CINE-38 |
| scenerender/cine/sim/pyro_colliders.py | CINE: CINE-19, CINE-37, CINE-39 |
| scenerender/cli.py | RESOLVE: RESOLVE-09 (6-line registration; shared with the CLI area); CINE: CINE-20 |
| scenerender/compositor.py | P4-S1: SIM2-02 (only if a texture-quad helper is needed); CINE: CINE-18 |
| scenerender/coverage.py | RESOLVE: RESOLVE-10 |
| scenerender/document.py | MAPS: MAPS-01 (shared with schema/BOB and RESOLVE) |
| scenerender/document.py + scenerender/schema/* | RESOLVE: BOB-PHASE1 owns; RESOLVE-10/12 consume |
| scenerender/document.py / scenerender/references.py | CINE: CINE-02 |
| scenerender/evaluator.py | CINE: CINE-18 |
| scenerender/expr.py + scenerender/evaluator.py | MAPS: MAPS-13 (also touched by EVAL-area tracks) |
| scenerender/geo/basemap_vector.py | MAPS: MAPS-17, MAPS-18, MAPS-20 |
| scenerender/geo/clip.py + project.py + data.py + camera.py | MAPS: MAPS-03,05,06,07 / 04 / 02,03,09 / 08,09,13 (sequenced inside M1, single owner) |
| scenerender/geo/model.py | MAPS: MAPS-01, MAPS-10, MAPS-21 |
| scenerender/geo/tiles.py | MAPS: MAPS-14, MAPS-15 |
| scenerender/gl.py | MAPS: MAPS-19 (only if a textured-quad helper is added) |
| scenerender/nodes/particles.py | P4-S1: SIM2-04; particles-area: any |
| scenerender/nodes/scene3d.py | P4-S2: SIM2-13; P4-S4: SIM2-26 |
| scenerender/nodes/sims.py | P4-S1: SIM2-02, SIM2-03, SIM2-04, SIM2-06, SIM2-07, SIM2-08, SIM2-09 |
| scenerender/physics.py | CINE: CINE-17 |
| scenerender/physics.py or the SIM 3D world module | CINE: CINE-35 |
| scenerender/registry.py | CINE: CINE-02, CINE-09, CINE-23, CINE-27, CINE-31 |
| scenerender/resolve/__init__.py | RESOLVE: RESOLVE-01 creates; all others only import |
| scenerender/resolve/engine.py | RESOLVE: RESOLVE-07, RESOLVE-08, RESOLVE-17 (phase-2 hook): same track R2 except 17 - sequence 07 -> 08 -> 17 |
| scenerender/resolve/providers/process.py | RESOLVE: RESOLVE-05, RESOLVE-15 (curl helpers; same track R3, sequential) |
| scenerender/resolve/transcribe.py | RESOLVE: RESOLVE-10, RESOLVE-11 (same track, sequential) |
| scenerender/three/geometry.py | P4-S2: SIM2-13; CINE: CINE-29 |
| scenerender/three/lights.py | P4-S3: SIM2-16, SIM2-18 |
| scenerender/three/pathtrace.py | CINE: CINE-11, CINE-24 |
| scenerender/three/pathtrace_tiles.py | CINE: CINE-12 |
| scenerender/three/renderer.py | P4-S3: SIM2-14, SIM2-15, SIM2-16, SIM2-17, SIM2-18, SIM2-19, SIM2-20; P4-S4: SIM2-21; CINE: CINE-10, CINE-23 |
| scenerender/three/scene.py | P4-S2: SIM2-13; P4-S3: SIM2-14, SIM2-18, SIM2-19; P4-S4: SIM2-21, SIM2-31; CINE: CINE-09, CINE-10, CINE-11, CINE-23, CINE-27, CINE-30, CINE-31, CINE-36 |
| scenerender/three/scene.py + scenerender/nodes/scene3d.py + scenerender/three/materials.py + scenerender/three/geometry.py | MAPS: MAPS-23, MAPS-24, MAPS-26 (hub shared with 3D area) |
| scenerender/three/shaders.py | P4-S3: SIM2-14, SIM2-15, SIM2-16, SIM2-17, SIM2-18, SIM2-20; P4-S4: SIM2-24 (read only, shares GGX string) |
| tests/conftest.py | P4-S4: SIM2-33 (sim2 marker) |
| tools/parity/render_parity.py | MAPS: MAPS-00 (additive); CINE: CINE-00 |

## Cross-area issues to settle before starting

* Rows with two owners: SIM-36 (3D particles) and 3D-44 (meshSequence, crater, fracture) in the matrix duplicate CINE-18..20 and CINE-26/28..33. The plan assigns them to the P4-CINE packages; the phase-5 SIM/3D plan must not schedule them again.
* P4-CINE-12 (UHD tiling) may overlap SIM2-37 (tiled path tracing in P4-SIM2); P4-CINE-12 must be re-scoped to what SIM2 leaves before it starts.
* MAPS-21 (PMTiles reader) is needed by RESOLVE (tile enumeration, P4-RESOLVE-16/17) and CINE terrain (P4-CINE-29); MAPS-29..33 overlap RESOLVE-17 (tile sets are exact integer sets, so zoom rounding must be one implementation). P4-MAPS owns the code; P4-RESOLVE imports it.
* Environment: numba and scipy are declared/needed but not installed in the dev venv (erosion, BVH build, pyro CG solves need a JIT or scipy; blosc optional for OpenVDB). The path tracer needs GL 4.3 compute (Mesa here gives 4.6; the existing renderer targets GL 4.1). Decide the dependency policy before P4-SIM2-14+ and P4-CINE-04+.
* Shadow budget policy conflict: Python raises on over-capacity by design, Rust degrades with a note (P4-SIM2 shadow-budget package); mirroring Rust changes a pinned Python behaviour and golden images.

## Statistical and non-pixel gates

Pixel PSNR (the render-parity classes MATCH >= 45 dB, CLOSE >= 30 dB) is the gate only where Python reproduces the algorithm deterministically. Rust measured here is byte-reproducible on one adapter and agrees across adapters (Intel Arc vs llvmpipe) at 67-87 dB, so it is usable as a pixel oracle at 40-45 dB for deterministic code (fluid, erosion). Everything else gets one of these, each calibrated from Rust's own run-to-run spread rather than picked by hand (tolerances stored by `tools/parity/sim_gate.py`, P4-SIM2-00, and the CINE gate library G1-G6, P4-CINE-00):

* **Chaotic/seeded 2D sims (flock, slime):** 6-seed sweep; mean luminance, region moments and luma-histogram total variation within the Rust seed-to-seed spread (flock mean luma 0.1976 +- 0.0019, TV up to 0.0117).
* **Path tracer:** sample-exact port of Rust's PCG stream and `rnd()` call order, expecting >= 45 dB at equal spp; fallback gate is convergence (128 spp vs a 4096 spp reference: 36.9 dB undenoised, 47.4 dB denoised) and a white-furnace energy test within 1%. Slope of error against spp must lie in [-0.65, -0.35] over 8 seeds.
* **Fields (pyro, ocean, fluid state):** relative L2 against Rust `bake-volume` output 1e-6..2e-4 (pyro), 1e-6..5e-5 (ocean); conservation checks (water volume 1e-12, fracture momentum 1e-9).
* **Rasteriser-shaded features (clay, SSAO, contact shadows, SSR, cascades):** silhouette IoU, delta-image correlation or shadow-mask IoU, because the rasterisers shade differently.
* **Particles3D:** blob matching within 0.5 px with exact counts. **Luma-histogram distance** (Wasserstein) <= 0.02 for volumes. **Tiled render equals whole frame bit-exactly.**
* **Maps:** geometry numerically against the `d3.json` fixtures the Rust crate ships (coordinates to 1e-6 px, fits to 1e-9); pixels by PSNR class plus per-region colour and coverage statistics (cairo/Pango cannot match Rust bit for bit); 3D by silhouette and region means.
* **Resolve:** byte-for-byte on request keys, sidecars, rewritten document, row JSON and exit codes using a mock provider (`tools/parity/example_provider.py`, `tools/parity/resolve_parity.py`); transcription keys gated numerically (summation order of the float mix differs).

## Area notes

**MAPS.** ORDER. BOB-PHASE1 first (schema, diagnostics, version gates V5/V6, validation codes R26/R27/R28/R29/R36/R37/C45/C46/C47/C53 are its rules; P4-MAPS-01 only wires the model). Critical path: 00 -> 02 -> 03 -> 04 -> 05 -> 06 -> 07 -> 08 -> 10 -> (11,12,13) for 2D vector maps; 14 -> 15 -> (16 -> 17 -> 18) and 19 -> 20 for basemaps; 22 -> 23 -> (24,25,26) for 3D. Evidence cases native after: world-map = 10+12 (needs 08 camera, flyTo at t=5.9, plus the globe clip 06); basemap = 17+19 (+20 optional, labels gate coverage check via 18); map-3d = 23,24,25,26 (needs 17/19 for the draped tiles and buildings). Expect world-map to turn native at the end of P4-M2 (~day 25 elapsed), basemap at ~day 35, map-3d at ~day 40 with M3 in parallel.

SHARED INFRASTRUCTURE. scenerender/geo/ is a new pure-Python package (no GL). The geo core (data, sphere, project, clip, view) must be PLAIN PYTHON FLOATS in d3's operation order, never numpy reductions, to keep d3 parity at 1e-6 px / 1e-6 rel areas / 1e-9 fits; numba is allowed only after the d3 gate passes and only if bit-identical. All 2D map drawing is cairo (pycairo already a dependency): a single Painter that accumulates paths so same-paint fills merge into one fill call (this is the Rust seam fix). Text labels use the existing Pango bridge. 3D reuses three/*: the map drape is a cairo ImageSurface turned into an in-memory baseColorMap; terrain/buildings are generated geometry fed to the existing PBR renderer. No GPU is required for anything in this area except that the 3D draw itself is the existing moderngl renderer.

DEPENDENCIES. Everything needed is present in pyproject (numpy, pillow, lxml, pycairo, shapely, scipy, moderngl, trimesh); gzip/zlib/struct/hashlib are stdlib. NO new dependency: protobuf is hand-rolled (Rust does the same, mvt.rs 266 lines), PMTiles reader/writer is hand-rolled (stdlib gzip, sha256), triangulation reuses shapely + three/geometry.extrude_polygons (earcut and triangle are NOT installed, do not add them), pyproj/mapbox-vector-tile/maplibre are not needed and must not be added. WebP tile decoding relies on Pillow's webp plugin (check PIL.features.check('webp') in the venv; if absent, report PY-ERROR for webp tiles only).

WHAT IS INFEASIBLE FOR BIT PARITY. Pixels: Rust rasterises on its own vector tile rasteriser (vello-like), Python on cairo: AA, stroke joins and gradient dithering differ, so pixel gates are PSNR classes (target CLOSE >= 30 dB, MATCH only for flat-colour geometry) plus per-region mean colour and coverage statistics, as already used by the evidence gate. Text: Pango versus sr-text give different glyph rasters and advances, so label and credit gates use positions, set membership and coverage, not pixels. 3D: the lit draw is a different rasteriser and shader stack, gate on silhouette rows, coverage and 3x3 region means. Numbers: ALL geometry (decode, projection, clip, area, fit, flyTo) IS gated numerically against the Rust-derived d3.json fixtures at 1e-6/1e-9, and is expected to be bit-close because Rust ported d3 formulae in f64 and Python floats are f64. Documented-convention target: sRGB-space cairo compositing instead of Rust's linear-light drape compositing (differences only on AA edges); Python may draw document paints in drapes where Rust draws grey (superset, see deferred).

PERFORMANCE. Pure-Python clipping of 177 countries x 13 setups for the d3 suite is a few seconds; per-frame rendering of countries at 10 fps is acceptable with a per-(setup,view) Planar cache (view changes only when centre/zoom/rotation animate). Basemap z15 views decode MVT in pure Python: cache decoded tiles keyed by (archive,z,x,y) and projected paths per frame state. Label placement is O(n^2) in boxes; use a uniform grid broad phase that keeps Rust's insertion order.

CROSS-AREA. P4-MAPS-13 needs the EVAL track's expression hook (P4-EVAL-00 harness for the gate); P4-MAPS-21 needs RESOLVE-28; P4-MAPS-24/25 leave the hook for CINE-25 globe relief (shared dem decode in scenerender/geo/dem.py, coordinate ownership); P4-MAPS-23 touches the 3D hub files (materials/scene) owned by the 3D area, keep changes additive (new primitive branches only). DOC-08 (version gates V5/V6) and the 1.3 attributes on object3D (GEO1-GEO3) are BOB-PHASE1.

GATE HARNESS. tools/parity/maps_parity.py plus the maps scene set; evidence_gate.py case ids world-map, basemap, map-3d are the end gates. Rust fixtures are vendored verbatim, so a Python test can assert exactly what the Rust tests assert.

**RESOLVE.** SCOPE AND OVERLAP: all 32 RESOLVE rows; 3 are PRESENT (01, 04, 32) and covered only by regression gates, 2 are DIVERGENT read-side rows (02, 03) in P4-RESOLVE-12, 1 PARTIAL (23, in 04, and 30 in 06), the rest MISSING. The same capability is also listed in other areas: CLI-25 (resolve subcommand, XL) = P4-RESOLVE-05..09 together; TEXT-29 (captionTrack provider/model/prompt + resolve) = P4-RESOLVE-10 (+13); TEXT-28 (transcription cache) and DOC-26/27 (A01/A02) = P4-RESOLVE-12; MAPS-29 (online tiles) = P4-RESOLVE-16/17; TEXT-36/MEDIA-18/19 supply the output-time audio used by P4-RESOLVE-11. The plan owner should mark those rows as 'covered by P4-RESOLVE-xx' to avoid double work. ORDERING: P4-RESOLVE-00 (harness, fixtures) first and in parallel with 01/02/03/04 (pure libraries, no deps beyond 01). Then 05 (provider framework) and 06 (targets); then 07 settle, 08 orchestration, 09 CLI (the first end-to-end Python resolve; before it the harness reports PY-MISSING and gates on Rust golden values). 13/14/15 (providers) run in parallel after 05. 10 needs BOB-PHASE1 (captionTrack attributes in the 1.1.3+1.2+1.3 XSD, 1.2/1.3 documents loading) plus a working Mixer; 11 additionally needs the MEDIA output-segment audio; 12 needs BOB-PHASE1 diagnostics; 16/17 need MAPS (PMTiles reader, tile math, projections, view evaluation) and are the tail. Everything schema-independent (00-09, 13-15) can proceed BEFORE BOB-PHASE1 because resolve reads raw attributes with lxml; this is deliberate so the biggest chunk of the area is unblocked. CODE LAYOUT: new package scenerender/resolve/ (protocol, docedit, safety, content, targets, store, engine, transcribe, cli, pmtiles_write, tileset, providers/{__init__,process,external,whisper,piper,audioforge,cloud,tiles}). Plugin pattern: scenerender/registry.py is for render-time ASSETS/EFFECTS and is NOT used; providers use their own find() table with lazy imports so provider files stay disjoint. Render-side files touched are only assets/__init__.py and captions.py (P4-RESOLVE-12/10) and cli.py (09). STRATEGY: moderngl/cairo are irrelevant to this area: it is process orchestration, hashing, file safety and JSON. numpy is already used by the Mixer. Dependencies: standard library only (subprocess, threading, hashlib, fcntl, tempfile, http.server for fixtures) plus PIL (image validation, already present) and the system `curl`. No new Python dependency. External programs (ffmpeg/ffprobe, whisper-cli, piper, audioforge) are NOT installed on the dev box: ffprobe lives in the brief's static_ffmpeg dir for the Rust side; Python uses a pure-Python WAV validator and ffmpeg only for conversions, so every gate is mock-driven (fake executables, local HTTP server, the Python example provider). The Rust example provider binary is not built, so tools/parity/example_provider.py (P4-RESOLVE-00) is run by BOTH engines via SR_PROVIDER_EXAMPLE to make provider output identical by construction. PARITY TARGETS: bit/byte parity is achievable and REQUIRED for: request key and sidecar (generated assets), rewritten document bytes, cache digests, row JSON, exit codes, error message prefixes. Not achievable: (a) keys of transcription requests, because inputSha256 hashes a mono 24-bit WAV produced by a float mix whose summation order differs from sr-audio - the convention is numeric closeness of the input (RMS < -90 dBFS, equal length) and the same document-level result, with stores not shared across engines for captions; (b) real whisper.cpp / piper outputs (nondeterministic across builds; piper samples noise by design); (c) PMTiles writer byte layout only if Rust's directory split differs - the gate is decoded equality. SCHEMA: version="1.2" test fixtures and captionTrack provider/model/prompt are not in the Python 1.1 XSD (SCHEMA-DELTA.md line 103); P4-RESOLVE-10/11/12/16/17 are marked needs_schema_adoption; 00-09 and 13-15 are not. Rust refuses to resolve a document that fails validation (sr_model::load_str); Python reproduces that only after BOB-PHASE1 (until then raw parse). TOTAL: 18 packages, about 25 working days (S=0.5, M=2); tracks P4-R1..R5 are independent; R5 is blocked on the MAPS area and may slip beyond the rest. EVIDENCE: tools/evidence/cases.json has no resolve/generated/caption case, so no evidence_cases apply; the parity gate is tools/parity/resolve_parity.py (new) with render_parity.py used only for the read-side pixel check.

**SIM2.** ORDERING AND TRACKS. Four independent tracks with disjoint owned files. P4-S1 (2D sims, 19 d) is pure NumPy/numba and can start the moment BOB-PHASE1 lands the 1.1.3+1.2+1.3 schema (only P4-SIM2-00 and 01 can start before it: gate harness and RNG/Timeline are schema-free). Internal order: 00,01 -> 02 -> {03 -> 04, 05 -> 06 -> 07, 08, 09 -> 10}. P4-S2 clay (6 d) is independent of S1 and needs only BOB-PHASE1 for the blob child element and clay primitive attributes. P4-S3 screen-space lighting and shadows (12.5 d) is a sequential chain on the shared GLSL renderer (renderer.py/shaders.py are the hubs; one agent). P4-S4 path tracer (32 d) is the largest and the riskiest; it depends on P4-SIM2-13 (clay triangles) only for the clay flatten case, otherwise independent of S1-S3. Package total: 34 packages (SIM2-00..33), 69.5 agent-days by package size (S1 19, S2 6, S3 12.5, S4 32).

GPU/CPU STRATEGY. (1) The four grid/agent sims are CPU float64/float32 code in Rust (no threads: grep rayon/par_iter in sr-sim finds none), so they are reproducible on NumPy/numba and need NO new GPU path: moderngl+cairo is enough for drawing (SimImage to a texture quad; flock through the existing cairo particle drawer). Flock, fluid projection and fluid vorticity vectorise in NumPy exactly; the SOR pressure solver is red-black so checkerboard slicing is bit-equivalent. Slime deposit and erosion droplets are order-dependent sequential updates (erosion 60000 droplets x 30 steps x ~29 brush cells per sim-second): pure python is minutes per scene, so a JIT is mandatory. numba is already DECLARED in pyproject.toml but is NOT installed in the dev .venv (memory: 'numba not installed in .venv, though pyproject requires it'; verified: import numba fails in /home/admin/codebases/py-render/.venv, as does scipy there). New dependency decision: make numba a hard requirement for scenerender.sim (with fastmath off) and keep a literal pure-python reference path for tests only; alternative would be a small C extension (gcc is present, cffi is not), which is not recommended. scipy is also declared and absent: avoid it in the sim code. (2) The 3D features (SSAO, contact shadows, SSR, cascades) fit the existing moderngl GLSL 4.1 forward renderer: add a prepass FBO and fullscreen passes; no new dependency. (3) The path tracer needs GL 4.3 compute shaders. gl.context() requests 4.6 first and this machine's Mesa Intel Arc and Mesa llvmpipe provide >= 4.5, so moderngl compute is viable (no cupy/embree needed); the BVH build (binned SAH) needs numba. CPU path tracing in NumPy is infeasible (480x270x128 spp x 5 bounces = ~83M ray segments per frame). Fallback to the raster pass with a note when compute is unavailable (P4-SIM2-31).

RUST DETERMINISM (measured here, Intel Arc Vulkan vs llvmpipe Vulkan CPU, 640x360 PNG at t=2.0 for fluid, flocking, slime, erosion, clay-3d, path-tracing evidence scenes; path-tracing at 480x270 t=0): two runs on the same adapter are byte-identical for all six scenes; across adapters the six scenes differ only slightly: PSNR 80.6 (fluid), 68.5 (flocking), 87.3 (slime), 67.1 (erosion), 77.2 (clay-3d), 72.2 (path-tracing) with max channel difference 1-5 of 255 and identical means. Reasons: the sims are CPU, single-threaded, counter-RNG, and the draw is the only GPU part; the tracer is sample-deterministic (PCG hash of pixel and sample, no wall-clock, no atomics in accumulation) so only float rounding differs. Consequences for gates: (a) Rust is a valid pixel oracle at roughly 45-60 dB for anything Python reproduces algorithmically; (b) cross-adapter noise floor 67 dB means PSNR thresholds of 40-45 dB are safe; (c) there is no state dump (eval JSON exposes only node transforms, not particles or grids), so all sim comparison is image-based.

TARGET CONVENTION. Exact vs convention: (1) bit-exact is realistic and targeted for RNG, init states, Timeline, erosion (f64 sequential, sqrt/floor only), fluid (f32 storage, f64 solve, red-black), clay SDF (f32) and BVH/flatten (integers/f32). (2) Chaotic systems (flock beyond ~0.25 s, slime beyond a few frames because f32 cosf/sinf may differ by an ulp between glibc and numpy) cannot be gated by pixels at long times: target is statistical equivalence (mean luminance, histogram TV, coverage, order parameters) with tolerances calibrated from Rust's own seed-to-seed variance (see P4-SIM2-00). (3) Rasteriser-dependent output (clay shading, AO/contact/SSR, cascades) is compared by structure, not by absolute pixels: silhouette IoU, delta-image (on minus off) correlation, shadow-mask IoU, since Python's PBR forward renderer and Rust's wgpu renderer differ in shading by design (render parity classes DIFFERENT). (4) Path tracer: GLSL port with the same PCG stream and the same rnd() consumption order, so 'sample-exact' comparison at equal spp should reach >= 45 dB; if the port deviates in rnd() order, fall back to convergence gates (Python at N spp vs Rust 4096 spp within the Rust-vs-Rust noise floor, measured here: 128 spp undenoised 36.9 dB, denoised 47.4 dB, 512 spp 50.7 dB and 4096 spp as reference with mean luminance equal to 4 digits).

EVIDENCE CASES. fluid -> P4-SIM2-07; flocking -> P4-SIM2-04; erosion-slime -> P4-SIM2-08; erosion -> P4-SIM2-09/10; clay-3d -> P4-SIM2-13; path-tracing -> P4-SIM2-26 and P4-SIM2-33. All six currently error in Python at validation (the XSD lacks fluid/flock/slime/erosion elements, blob children and camera renderer/maxBounces), so BOB-PHASE1 is the first dependency; once it lands, the nodes exist in the schema and render as empty until each package lands.

SCHEMA SURFACE (SIM2-41). Elements: flock, fluid (+ fluidSource), slime, erosion as composition children; object3D primitive='clay' with blob children (clayBlobType: shape, x,y,z, rotation, rotationX/Y, radius, width/height/depth, length, blend, subtract; animatable); camera attributes renderer (raster|pathtrace), pathSamples, maxBounces, denoise, ambientOcclusion, aoRadius, aoIntensity, screenSpaceReflections; light attributes contactShadows, contactShadowLength; useForceFields/forceFields on the sim nodes. Validation rules R30/R31 (colorLow/colorHigh must be literal) and codes C51/C52/R33/R10 in the Rust invalid corpus touch these elements: they are BOB-PHASE1 concerns. The flock margin attribute is named edgeMargin (matrix SIM2-41 review note).

OTHER OBSERVATIONS. (a) Seeds: Rust takes element seed from @seed as `v as u64` else hash_str(id) = mix64(FNV-1a(id)); the project seed is NOT mixed in for sims (agents.rs:80-85): mirror exactly. (b) sim steps run on the node's local clock with step = floor((t - 0)/(1/60) + 1e-9): note the sim starts at local time 0, not at the node's begin offset. (c) A scene with volumes automatically uses the tracer at 4 spp/2 bounces/denoise (render_three.rs:2444-2446): volume area hook. (d) Python's existing Seekable is integer-step; the float-time Timeline wrapper must not break physics.py/particles.py users. (e) Deferred sub-clauses are listed in `deferred`.

**CINE.** ORDERING. Start (no BOB dependency): P4-CINE-00 (harness), -01 (SRVOL/grid), -13 (timeline), -16 (closed mesh), -32 (mesh blend), then BOB-PHASE1-gated packages -02, -17, -30, -41. Volume chain: 01 -> 02/03 -> 04/05/06 -> 07, 08 -> 09 -> 10 -> 11 (needs SIM2 path tracer) -> 12. Sim chain: 13 -> 14 -> 15/17 -> 18 -> 19 -> 20; particles 21 -> 22/23 -> 24; ocean 25 -> 26 -> 27 -> 28; fracture 33 -> 34 -> 35 (needs SIM 3D rigid) -> 36/37; crater 30 -> 38/39; closing: 40, 41, 42. Total planned: see tracks (126.5 agent-days if run serially; seven independent tracks, critical path P4-C3 plus the SIM2/SIM dependencies).

SHARED INFRASTRUCTURE. New package scenerender/cine/{volume,sim,geo,render} keeps CINE files disjoint from other areas. Shared utilities: counter RNG (cine/sim/rng.py, SplitMix64 finaliser, in P4-CINE-14; also used by particles, ocean, whitewater, fracture; do not use physics.hash01), Timeline (P4-CINE-13), SparseGrid/Volume (P4-CINE-01), closed mesh + BVH (P4-CINE-16), collider adapters (cine/sim/colliders.py, P4-CINE-22). Hub files touched additively by several tracks are listed in tracks.shared_files-style map below (shared_files key).

GPU/CPU STRATEGY. moderngl (GL 4.3 compute where available, GL 4.1 fragment fallback) plus numpy is sufficient for everything EXCEPT the path tracer, which is SIM2's (SIM2-29..39) and a hard dependency of CINE-10 (volume in path tracer), -36 (UHD tiles) and particles/ocean path-traced rendering. Key Rust fact: ANY scene with a volume is rendered through the path tracer (three.rs:1419, default 4 spp / 2 bounces / denoise on). volume.wgsl is included in pathtrace.wgsl; the volume march (midpoint emission-absorption, analytic lights, dome single-scatter with ONE random sample per step) is deterministic apart from the dome term. So P4-CINE-10 builds a GLSL library plus a standalone raymarch pass, which gives CPU-checkable parity for volume-only and unlit-surface scenes immediately; P4-CINE-11 plugs it into SIM2's path tracer. Simulations are CPU numpy float64: pyro (dense MAC grid, matrix-free CG), ocean (finite volume), particles (vectorised), fracture (geometry). numba is declared in pyproject.toml but is NOT installed in the dev venv (/home/admin/codebases/py-render/.venv: numpy, scipy, trimesh, shapely 2.1.2 present; numba, blosc, lz4, zstandard absent): plan on numpy/scipy only and treat numba as optional. NEW DEPENDENCY: optional `blosc` (python-blosc) for OpenVDB Blosc (P4-CINE-07, extra `volumes`); no other new dependency (shapely 2.1.2 supplies constrained Delaunay for fracture caps; Rust uses spade). OpenVDB itself is never a runtime dependency; the official-library fixtures (tools/openvdb_fixture.cpp output, 17 files in crates/sr-volume/tests/data/openvdb) are the golden inputs.

BIT PARITY IS INFEASIBLE FOR: GPU path-traced pixels (different RNG consumption order in GLSL vs WGSL, f32 transcendental differences), CG pressure iteration counts, Parry vs own sphere-cast contact solves, Rapier vs the SIM 3D solver after chaotic contacts, spade vs GEOS cap triangulations, blosc/zlib compressor outputs. BIT-EXACT or near-exact (1e-9..1e-12) targets: SRVOL/SRVSEQ bytes, OpenVDB decode, counter RNG streams, particle births and ballistic motion (before contacts), ocean finite-volume fields (1e-6 vs Rust), medium/thermal CPU integrals, fracture volumes/centroids, crater displacement, tiled==whole in Python.

STATISTICAL GATES (proposed and justified; implemented once in P4-CINE-00 tools/parity/cine_stats.py):
G1 deterministic fields: relative L2 per field, tolerance chosen per scheme: SRVOL/openvdb/crater 1e-9..1e-12; pyro 1e-6 at frame 1 and 2e-4 at frame 24 (CG stop differences), ocean 1e-6 / 5e-5, medium GPU vs CPU reference 2e-3 rel (f32). Justification: algorithms are identical in exact arithmetic, differences are only summation order and f32.
G2 region moments (deterministic renders): 16 regions, mean RGB linear within 2% relative (>=1e-3 absolute); PSNR classes keep MATCH>=45, CLOSE>=30 from RENDER-BASELINE. Used where pixel PSNR is dominated by tonemapping/shading divergences owned by the 3D area.
G3 luma histogram distance (1-D Wasserstein, 64 bins) <= 0.02 for stochastic images (insensitive to pixel registration of noise).
G4 blob match for particle/spray fields: connected components, Hungarian matching of centroids, >=95% within 0.5 px early (before the 3rd collision) and counts exact; later only resting/occupancy statistics (chaos).
G5 Monte-Carlo convergence: RMS error vs a 1024-spp Python mean has log-log slope in [-0.65,-0.35]; plus per-region |mean_py - mean_rs| <= max(2%, 3*sigma_combined) with K=8 Python seeds and the (bit-reproducible) Rust render, and sample variance ratio in [0.5,2].
G6 energy conservation: white furnace (albedo 1, dome radiance 1 -> radiance 1 within 1% at 256 spp), slab transmittance exp(-sigma d) within 0.5% at 64 spp, water volume 1e-12, momentum at fracture release 1e-9.
SEED POLICY: Rust's path tracer is deterministic per (global pixel, sample) via pcg(); Python replicates that hash so a single Python render is run-to-run deterministic and tiled == whole bit-exactly; the harness adds a salt (0..7) only for statistical sweeps. Simulation seeds are the document's u64 seeds with the same counter RNG, so seeded sims are directly comparable.

ORACLES. (1) Rust `bake-volume` gives field-level pyro oracles without needing the Python renderer; (2) Rust renders of analytic slabs give exact transport checks; (3) Rust corpus files tests/corpus/{valid,invalid}/*.scene.xml for validation (they reference media absent from the repo: tools/parity/scenes/cine/gen_fixtures.py generates them and Rust validates the generators); (4) no Rust command dumps particle/ocean/fracture state, so those are compared through rendered probes (blob centroids, depth-encoded top-down views) or invariants.

CROSS-AREA. SIM2: SIM2-29/30/31/33 (path tracer entry, BVH/flattening, NEE, RR/dome), SIM2-36/37 (denoise, tiling/determinism), SIM2-19..22 (clay for solid colliders). SIM: SIM-11..14 (3D rigid, shapes, joints, bounds/fracture hooks) block CINE-30/27/31 rigid halves; SIM-12 shape definitions feed particle colliders; SIM-17/15/16 force fields overlap CINE-17; SIM-36 (3D particles) IS CINE-18..20 and 3D-44 (meshSequence, crater, fracture) IS CINE-26/28..33: one owner (CINE) must be recorded in the matrix to avoid double work; SIM-37/38 (simulate command, SRPHYS03 cache) needed by CINE-30. 3D: 3D-17/18/20/27 (shading, transmission, lights, shadows) bound how close lit particle/ocean/volume pictures can get; 3D-43 and MAPS-20/21/31 feed globe terrain (CINE-25); 3D-28 SSAO etc. not needed. Evidence case rigid-3d belongs to SIM (cases.json 'rigid-3d', currently PY-WORSE: Python rejects constraint type 'ball'); CINE fracture/crater/pyro-collider packages depend on that SIM work and do not claim the case. No evidence case in cases.json covers volumes, pyro, ocean, particles3D, fracture, crater or mesh sequences (the evidence set pre-dates them), so P4-CINE-00/-42 supply their own gates: the 'evidence_cases' lists are intentionally empty except regression guards.

SCHEMA. All new elements are 1.3 and gated: volume/medium/pyro/pyroSource/pyroImpulse (V8), particles3D (P3D1), ocean/wave/waterImpulse/whitewater (OCN1), meshSequence (MSQ1), crater (CRT1), fracture (FRX1), globe terrain attributes (GEO1). BOB-PHASE1 must land the vendored XSD/Schematron and diagnostics before any parse-dependent package; packages with needs_schema_adoption=false are pure engines testable without the schema: P4-CINE-00, P4-CINE-01, P4-CINE-03, P4-CINE-08, P4-CINE-10, P4-CINE-11, P4-CINE-12, P4-CINE-13, P4-CINE-16, P4-CINE-20, P4-CINE-24, P4-CINE-32, P4-CINE-33.

## Work packages

### MAPS

#### P4-MAPS-00: Maps parity harness, vendored fixtures and scene set

Rows:  · size S · track P4-M2 · group A · schema adoption: no · depends on: none

Create the shared test substrate for the area. (1) Vendor the Rust fixtures byte-for-byte into tests/fixtures/geo/: crates/sr-geo/tests/fixtures/{countries-110m.json,d3.json,tiles.json,style.json,baixa.pmtiles,raster.pmtiles,hill.pmtiles,NOTICE} and crates/sr-gpu/tests/fixtures/geo/{squares.geojson,place.kml,track.gpx}, plus crates/sr-geo/styles/protomaps-{light,dark}.json into scenerender/data/styles/ (BSD-3 Protomaps; ship NOTICE). (2) Author tools/parity/scenes/maps/*.scene.xml (list in acceptance_gate) using the same {GEO} placeholder the evidence gate already substitutes, and a scenes.json manifest (scene, times, kind: pixel|statistical, expected status per package). (3) tools/parity/maps_parity.py: runs `scene-render render` and the Python renderer on each manifest scene, reports MATCH/CLOSE/DIFFERENT through render_parity.py classes and, for statistical scenes, the coverage / mean-colour-per-region statistic named in each package. (4) tests/test_geo_fixtures.py: a conftest fixture `geo_fixture(name)` and a loader for d3.json (13 setups) used by every later package. No rendering code.

* Python files: tests/fixtures/geo/* (new, copied); scenerender/data/styles/protomaps-light.json (new); scenerender/data/styles/protomaps-dark.json (new); scenerender/data/styles/NOTICE (new); tools/parity/scenes/maps/*.scene.xml + scenes.json (new); tools/parity/maps_parity.py (new); tools/parity/render_parity.py (touch: accept sidecar manifest; shared with other areas, additive only); pyproject.toml (package-data: data/styles/*.json)
* Tests: tests/test_geo_fixtures.py (new); tests/conftest.py (touch: geo_fixture)
* Rust reference: crates/sr-geo/tests/fixtures/*; crates/sr-gpu/tests/fixtures/geo/*; crates/sr-geo/styles/*; tools/evidence/scenes/{world-map,basemap,map-3d}.scene.xml; tools/evidence/cases.json ids world-map, basemap, map-3d
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/world-map.scene.xml` (new): copy of evidence world-map (Equal Earth, graticule, highlighted FRA, route, pin, flying globe) Expect: manifest runs; Python status ERROR until later packages
  * scene `tools/parity/scenes/maps/projections-grid.scene.xml` (new): one 11-projection setup grid (all projection enum values, rotate via centerLon/centerLat, parallels for albers/lambert-conformal) Expect: as above
  * scene `tools/parity/scenes/maps/choropleth.scene.xml` (new): fillBy/palette/scale linear|log|sqrt|quantize + noData over countries-110m properties Expect: as above
  * scene `tools/parity/scenes/maps/kml-gpx.scene.xml` (new): place.kml + track.gpx layers and a route from geo Expect: as above
  * scene `tools/parity/scenes/maps/globe-flyto.scene.xml` (new): orthographic globe with two chained flyTo, antimeridian crossing, animated rotation Expect: as above
  * scene `tools/parity/scenes/maps/basemap-vector.scene.xml` (new): web-mercator Baixa at zoom 15.5 with protomaps-light and protomaps-dark, labels on/off, detail=-1/+1 Expect: as above
  * scene `tools/parity/scenes/maps/basemap-raster.scene.xml` (new): raster.pmtiles in web-mercator, orthographic, equal-earth, albers; opacity 0.5; geoLayer over it Expect: as above
  * scene `tools/parity/scenes/maps/map3d-globe.scene.xml` (new): globe primitive, rotationY sweep, textureSize 512/2048 Expect: as above
  * scene `tools/parity/scenes/maps/map3d-terrain.scene.xml` (new): hill.pmtiles terrain, exaggeration 1/3, terrainEncoding terrarium, edge-on Expect: as above
  * scene `tools/parity/scenes/maps/map3d-buildings.scene.xml` (new): baixa buildings=true in stone material edge-on and 45 degree tilt Expect: as above
  * scene `tools/parity/scenes/maps/neg-*.scene.xml` (new): negative probes: R26/R27/R28/R29/R36/R37/C45/C46/C47/C53, unresolved url tiles, over-budget detail=20 Expect: Python diagnostic codes equal Rust codes
  * non-pixel: harness self-test: --rs-vs-rs on world-map prints MATCH; manifest lists every scene named in packages 01-26.
* Risks: Fixture licence: countries-110m.json is Natural Earth (public domain), Protomaps styles BSD-3: keep NOTICE files.; Fixture binary size (~MBs) in repo; acceptable, same as Rust.

#### P4-MAPS-01: Maps element model, asset registration and schema-gated validation hooks

Rows: MAPS-01 · size M · track P4-M2 · group A · schema adoption: yes · depends on: BOB-PHASE1

Typed Python model for the maps schema surface, parsed from the lxml tree after BOB-PHASE1 vendors the 1.1.3+1.2+1.3 XSD/Schematron: geo (src,format auto|geojson|topojson|kml|gpx,object, assetProvenance), tiles (src,url,cache,cacheSha256,tileSize,minZoom 0..24 default 0,maxZoom default 19,attribution,sha256), map (width,height,projection enum of 11 incl. default equal-earth,parallels,centerLon,centerLat,zoom,rotation,fit IDREFS,fitPadding,background,outline,outlineWidth default 1,precision default 0.7071) with children basemap(tiles,mapStyle,opacity,detail,labels,attribution), geoLayer(geo,filter,fill default #D9D9D9,stroke,strokeWidth 0.5,opacity,fillBy,palette,domain,scale,noData,keyBy default id,label,textStyle,pointRadius 3,progress 1) + featureStyle(key,fill,stroke,strokeWidth,opacity), graticule(step 10,stroke #FFFFFF40,strokeWidth 0.5), route(points,geo,filter,progress,stroke,strokeWidth 3,dash,opacity,headRadius 0,headFill), pin(lon,lat,radius 6,fill #FF3B30,stroke #FFFFFF,strokeWidth 2,opacity,label,textStyle,labelDx 10,labelDy 0), flyTo(begin,duration,lon,lat,zoom 0,rho sqrt2,easing ease-in-out), and animationElements children on every one. Parse helpers: longitudeType/latitudeType, geoPointsType 'lon,lat lon,lat', numberList/colorList. Register ASSETS 'geo','tiles','map' (declared levels: initially level none, flipped by the renderer packages) and ASSET_SIZES 'map' = (width,height). Resolve geo/tiles src relative to the document dir and verify sha256 like document.py's existing data-sha256 check. Make the diagnostic codes owned by the schema parity work (R26 fit ids, R27 basemap/@tiles, R28 object3D/@map, R29 object3D/@terrain, R36 geoLayer/@geo, R37 route/@geo, C45 route needs points or geo, C46 tiles need src or url+cache+cacheSha256, C47 map/globe need @map, C53 domain increasing, V5/V6 version gates) reachable from the model so later packages can assert them; this package only wires and tests them, the rule implementations live in BOB-PHASE1. Keyed child naming for animation: children get the evaluator key 'id' else 'parent/name[k]' (animate/expression/motionPath/link excluded from k), exactly as sr-gpu map.rs keyed().

* Python files: scenerender/geo/__init__.py (new); scenerender/geo/model.py (new); scenerender/assets/map.py (new: registration stubs only); scenerender/document.py (touch: asset path resolution for geo/tiles src, sha256); scenerender/registry.py (touch: none beyond declares)
* Tests: tests/test_geo_model.py (new)
* Rust reference: README.md Maps / Schema paragraph; schema/scene-render-1.1.xsd lines 1125-1360 (maps), 2397-2435 (object3D map attrs); crates/sr-model/src/rules.rs:971,975,976,993,1403 (R36,R37,C45,R26,C53); crates/sr-model/src/model.rs GeoAsset/MapAsset/TilesAsset; crates/sr-gpu/src/text_map.rs keyed() lines 29-48; tests/corpus/valid + invalid geo scenes
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/neg-*.scene.xml` (new): one negative scene per code R26 R27 R28 R29 R36 R37 C45 C46 C47 C53 Expect: Python diag codes == Rust codes (render_parity negative mode)
  * port `crates/sr-gpu/tests/maps.rs::bad_geo_references_fail_validation`: geoLayer geo=unknown and route geo=unknown fail validation with R36/R37
  * non-pixel: python -m scenerender validate on the three evidence scenes (world-map, basemap, map-3d) produces zero errors; baseline today is 'Element geo not expected'.
* Risks: Depends on how BOB-PHASE1 exposes the element tree; keep the model a thin lxml view, no copy.

#### P4-MAPS-02: GeoJSON, TopoJSON, KML and GPX readers

Rows: MAPS-02, MAPS-03 · size M · track P4-M1 · group B · schema adoption: no · depends on: P4-MAPS-00

scenerender/geo/data.py: Format enum + detect(path,text) (extension first, then content sniffing), parse(text,format,object) and load(path,format,object) returning Feature(id,properties,geometry). Geometry: Point, MultiPoint, LineString, MultiLineString, Polygon, MultiPolygon, GeometryCollection plus the Sphere sentinel. GeoJSON: FeatureCollection/Feature/bare Geometry. TopoJSON: delta-encoded arcs, transform scale/translate, negative (reversed) arc references, select one object by @object else merge all objects (feature id, properties string/number as Feature::text/number). KML: Placemark Point/LineString/Polygon (outer+inner rings), ExtendedData/Data and SimpleData into properties, name. GPX: wpt, rte, trk/trkseg into Point/LineString features. Pure Python lists of floats, json + lxml; no new dependency. Whole file read once, per-process cache keyed by (path,size,mtime,format,object) (Rust geo.rs:35).

* Python files: scenerender/geo/data.py (new)
* Tests: tests/test_geo_data.py (new)
* Rust reference: crates/sr-geo/src/data.rs (Format 87, detect 96, parse 122-143, tests 499-541); crates/sr-eval/src/geo.rs load() 32-40; crates/sr-gpu/tests/maps.rs kml_and_gpx_sources_draw 148
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/kml-gpx.scene.xml` (new): KML placemarks and GPX track drawn over a world map Expect: feature counts equal Rust; PSNR CLOSE once drawing exists (P4-MAPS-10)
  * port `crates/sr-geo/tests/d3.rs::topojson_decodes_like_topojson_client`: all 177 countries decode: ring counts and coordinates equal topojson-client output stored in d3.json
  * port `crates/sr-geo/src/data.rs::geojson_features_and_winding / topojson_arcs_decode / kml_and_gpx`: small inline documents parse to the expected features
  * port `crates/sr-geo/tests/d3.rs::(feature id/property access)`: id 250 is France; numeric id vs string id handling
* Risks: TopoJSON id may be string or number; Rust Feature::text/number coercion must be copied or filters such as id=250 break (world-map case).

#### P4-MAPS-03: Spherical geometry: rotation, distance, great-circle interpolation, area, containment, ring rewinding

Rows: MAPS-04, MAPS-05 · size M · track P4-M1 · group B · schema adoption: no · depends on: P4-MAPS-02

scenerender/geo/sphere.py: cartesian/spherical, point_equal, Rotation(dl,dphi,dgamma).apply/invert (d3 rotate), distance, interpolate(a,b,t) along the great circle, polygon_area (spherical excess, d3 geoArea), polygon_contains (d3 geoContains, winding based), Ll helpers. Polygon rewinding in data.py post-step: RFC 7946 wound rings are turned so the interior is the smaller side (area > 2pi flips), edges treated as great-circle arcs. Plain Python float math in d3's operation order (do NOT use numpy reductions) to hold 1e-6 relative on areas and 1e-6 px on coordinates against d3.json.

* Python files: scenerender/geo/sphere.py (new); scenerender/geo/data.py (touch: rewind step, owned by P4-MAPS-02 author; sequence after it)
* Tests: tests/test_geo_sphere.py (new)
* Rust reference: crates/sr-geo/src/sphere.rs (Rotation 85-122, distance 124, interpolate 136, polygon_area 188, polygon_contains 198, tests 255-287); crates/sr-geo/src/data.rs rewinding; d3-geo area.js/contains.js semantics
* **Acceptance:** 
  * port `crates/sr-geo/src/sphere.rs::rotation_round_trips / distances_and_areas / containment_follows_winding`: rotation invertible, known distances/areas, containment follows winding
  * port `crates/sr-geo/tests/d3.rs::projected_countries_match_d3 (area part)`: planar/spherical area of every country within 1e-6 relative of d3 geoArea
  * statistical: Areas: relative error <= 1e-6 over 177 countries; no seed (deterministic).
* Risks: Hemisphere-plus polygons (Antarctica, Fiji, Russia) are the failure cases; summation order must match d3.

#### P4-MAPS-04: Raw projections and the Projection transform pipeline (cylindrical, pseudo-cylindrical, conic, azimuthal)

Rows: MAPS-06, MAPS-07 · size M · track P4-M1 · group B · schema adoption: no · depends on: P4-MAPS-03

scenerender/geo/project.py: Raw forward() for mercator, web-mercator (512 px world at zoom 0, MapLibre zoom = log2(2 pi scale/512)), equirectangular, equal-earth, natural-earth, conic equal-area (albers, parallels default 29.5/45.5), conic conformal (lambert-conformal), orthographic, stereographic, azimuthal equal-area, azimuthal equidistant, with inverse where Rust has one. Projection class: set_scale/translate/center/rotate/angle (plane rotation)/clip/extent/precision, point(lon,lat)->Optional, point_unclipped, visible(lon,lat), default_clip per family, scale/translate/rotate getters (compose rotation -> raw -> scale/translate -> angle exactly as d3 projection/index.js). scenerender/geo/view.py Kind.parse (enum names), Kind.azimuthal, Kind.raw(parallels).

* Python files: scenerender/geo/project.py (new); scenerender/geo/view.py (new: Kind only; rest in P4-MAPS-08)
* Tests: tests/test_geo_project.py (new)
* Rust reference: crates/sr-geo/src/project.rs (Raw 15-130, forward 68, Projection 191-340, tests 541); crates/sr-geo/src/view.rs Kind 23-85; crates/sr-geo/tests/d3.rs setups build() 27-87
* **Acceptance:** 
  * port `crates/sr-geo/src/project.rs::mercator_matches_the_textbook_formula`: mercator y = ln tan(pi/4+phi/2)
  * port `crates/sr-geo/tests/d3.rs::projected_countries_match_d3 (point forward part)`: for each of the 13 setups the projected point of probe lon/lat equals d3 output to 1e-6 px
  * statistical: Projected probe points: abs error <= 1e-6 px for all 13 setups.
* Risks: Each family needs its own clip default (cut vs cap): get the enum->clip table from project.rs:131 default_clip.

#### P4-MAPS-05: Antimeridian cutting and polygon rejoin (including polygons around a pole)

Rows: MAPS-08 · size L · track P4-M1 · group B · schema adoption: no · depends on: P4-MAPS-03, P4-MAPS-04

scenerender/geo/clip.py part 1: Clip enum (Antimeridian, Circle(angle), None) with clip_line(points)->list of runs and clip_polygon(rings)->rings, sphere() outline; port of d3-geo clip/antimeridian.js and clip/rejoin.js (intersection linked lists, entry/exit flags, polygon-contains-pole winding via the same polygon_contains) as ported in clip.rs 1-266 and 267-450. Geometry is rotated first (Rotation from P4-MAPS-03) then clipped on the sphere along the antimeridian opposite the centre; lines split; polygons rejoined along the cut; polygons around a pole (Antarctica) wrapped to the frame edge. Float behaviour must follow d3 including epsilon constants (1e-6, 1e-12).

* Python files: scenerender/geo/clip.py (new)
* Tests: tests/test_geo_clip.py (new)
* Rust reference: crates/sr-geo/src/clip.rs (Clip 37-100, antimeridian + rejoin up to 266, tests 772-798); crates/sr-geo/tests/d3.rs projected_countries_match_d3 115-174; d3-geo src/clip/antimeridian.js, rejoin.js (for reading only)
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/projections-grid.scene.xml` (new): equirectangular/mercator/equal-earth/natural-earth with centerLon 150 so Russia, Fiji and Antarctica cross the cut Expect: PSNR CLOSE or MATCH vs Rust once drawing exists; geometry gate below is primary
  * port `crates/sr-geo/src/clip.rs::lines_split_at_the_antimeridian / a_polygon_round_the_south_pole_fills_to_the_edge`: line crossing 180 splits in two; Antarctica polygon closes along the frame edge
  * port `crates/sr-geo/tests/d3.rs::projected_countries_match_d3`: for Russia, Fiji, Antarctica the full projected ring coordinates equal d3 to 1e-6 px and every one of 177 countries matches area, bounds and ring count
  * statistical: Coordinates abs <= 1e-6 px; area rel <= 1e-6; bounds abs <= 1e-6 px; ring counts exactly equal across all 177 countries x 13 setups.
* Risks: Hardest numeric item of the area: port line-by-line from clip.rs, do not reinvent. Pure-Python speed: 177 countries x 13 setups must stay under ~60 s in CI; use plain lists, numba only after correctness.

#### P4-MAPS-06: Visible-cap circle clipping and frame-rectangle clipping

Rows: MAPS-09 · size M · track P4-M1 · group B · schema adoption: no · depends on: P4-MAPS-05

clip.py part 2: Clip.Circle(angle) for orthographic (90 deg), stereographic, azimuthal equal-area/equidistant (d3 clipCircle with small-circle intersections, visible(p) test, polygon variant), and Rect clip (clip_line/clip_polygon in projected pixels, clipExtent) used for every map frame; Projection.set_extent / set_clip wiring and Projection.visible(lon,lat) for pins and geo(). The map keeps a 64 px margin round the frame so strokes/pins/labels reaching in survive (geo.rs:112).

* Python files: scenerender/geo/clip.py (touch, same owner as P4-MAPS-05); scenerender/geo/project.py (touch: set_extent)
* Tests: tests/test_geo_clip.py (touch)
* Rust reference: crates/sr-geo/src/clip.rs (Clip::visible 46, circle variant, Rect 267, rect clip_line/clip_polygon 451-459, test circle_clip_hides_the_far_side 790); crates/sr-geo/src/project.rs set_extent/set_clip 270-280, visible 523
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/globe-flyto.scene.xml` (new): orthographic + stereographic + azimuthal globes at centre 20E 20N, far side hidden Expect: PSNR >= 30 dB (CLOSE) vs Rust; globe limb coverage equal
  * evidence cases to turn native: world-map
  * port `crates/sr-geo/src/clip.rs::circle_clip_hides_the_far_side`: points beyond 90 degrees are not visible, lines are cut at the limb
  * port `crates/sr-geo/tests/d3.rs::projected_countries_match_d3 (orthographic setups)`: orthographic setups match d3 to 1e-6 px
  * statistical: As P4-MAPS-05 for orthographic/stereographic/azimuthal setups of d3.json.
* Risks: Frame clip happens after projection with extent plus margin; mis-ordering shows only as pixel edge artefacts.

#### P4-MAPS-07: Adaptive resampling and projected geometry (Planar, area, bounds)

Rows: MAPS-10 · size S · track P4-M1 · group B · schema adoption: no · depends on: P4-MAPS-05, P4-MAPS-06

project.py: resample (d3 resample.js: adaptive midpoint subdivision with precision 0.7071 px and cos-delta tolerance, depth limit 16) integrated into Projection.project(geometry)->Planar(polygons, lines, points), Planar.area() and bounds(). Precision comes from map/@precision.

* Python files: scenerender/geo/project.py (touch)
* Tests: tests/test_geo_project.py (touch)
* Rust reference: crates/sr-geo/src/project.rs set_precision 280, project() 417, Planar 144-190; d3-geo src/projection/resample.js
* **Acceptance:** 
  * port `crates/sr-geo/tests/d3.rs::projected_countries_match_d3`: areas and bounds of Planar equal d3 path.area/bounds
  * statistical: Planar area rel <= 1e-6, bounds abs <= 1e-6 px, ring point counts exactly equal (resample is deterministic).
* Risks: Point counts differ if resample's epsilon or depth cap deviates; compare ring lengths first.

#### P4-MAPS-08: Map camera: fit, fitPadding, centre/zoom/rotation view, ZoomPath and flyTo sequencing

Rows: MAPS-11, MAPS-12, MAPS-13 · size M · track P4-M1 · group B · schema adoption: no · depends on: P4-MAPS-07

scenerender/geo/view.py: Map.new(kind,parallels,size,fit_geometries,fit_padding,center) -> (Map,center) via Projection.fit_extent (d3 fitExtent) with the whole sphere default, Map.projection(view) (zoom = doublings from fit; web-mercator uses MapLibre levels), View(lon,lat,zoom,rotation), view_width/zoom_for_width, ZoomPath(p0,p1,rho) with natural duration and at(t) (van Wijk and Nuij; same-point branch d2<1e-12; rho clamp 1e-3), Fly list sequencing: first move starts from the map's static centerLon/centerLat/zoom, later ones from the previous target, shortest way across the antimeridian, longitude normalised to (-180,180], easing = curve of the latest begun move, rotation always the animated value. scenerender/geo/camera.py: camera(doc,map_el) with per-setup fit cache keyed by (projection,parallels,size,centre,fitPadding,precision, geo file identity); view(cam,map_el,animated,t) mirroring sr-eval geo.rs:120-153 including that animated centerLon/centerLat/zoom/rotation are read through the evaluator for the map element.

* Python files: scenerender/geo/view.py (touch); scenerender/geo/camera.py (new)
* Tests: tests/test_geo_view.py (new)
* Rust reference: crates/sr-geo/src/view.rs (Map::new 117, projection 168, ZoomPath 197-258, Fly/view_at 260-310, tests 327-358); crates/sr-geo/src/project.rs fit_extent 482; crates/sr-eval/src/geo.rs camera 71-118, view 120-153; crates/sr-geo/tests/d3.rs fits_match_d3 175, fly_to_paths_match_d3_interpolate_zoom 194
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/globe-flyto.scene.xml` (new): two chained flyTo on orthographic globe (Atlantic to Beijing zoom 1.5, then back across the antimeridian) with rotation animated Expect: view dump (lon,lat,zoom) at 10 times equals Rust within 1e-6; pixels CLOSE
  * evidence cases to turn native: world-map
  * port `crates/sr-geo/tests/d3.rs::fits_match_d3`: fitted scale and translate for each setup equal d3 fitExtent to 1e-9
  * port `crates/sr-geo/tests/d3.rs::fly_to_paths_match_d3_interpolate_zoom`: ZoomPath duration and sampled points equal d3.interpolateZoom to 1e-9
  * port `crates/sr-geo/src/view.rs::zoom_zero_fits_the_sphere / views_follow_the_moves`: Equal Earth 960x500 padding 10: bounds [10..950]; flyTo start/end and antimeridian shortest path
  * statistical: Fitted scale rel <= 1e-9, translate abs <= 1e-9, path durations abs <= 1e-9 s; camera per frame deterministic (no seed).
* Risks: Quirk to mirror: before the first flyTo begins the view is the animated base; once it begins the first move starts from the STATIC attributes (view.rs:289-307) - see rust_may_be_wrong.

#### P4-MAPS-09: d3-parity suite across the whole geo core and per-setup caches

Rows: MAPS-34 · size S · track P4-M1 · group C · schema adoption: no · depends on: P4-MAPS-02, P4-MAPS-05, P4-MAPS-08

Aggregate gate and caches: tests/test_geo_d3_parity.py runs every d3.json check (topojson decode, 177-country area/bounds/ring-count for 13 setups, full coordinates for Russia/Fiji/Antarctica, fitted scales, zoom-path durations) as one suite with the tolerances 1e-6 px / 1e-6 rel / 1e-9 / 1e-12; add file-identity-keyed caches for loaded geo data and fitted cameras (path,size,mtime), asserting that two renders of the same document reuse and that a touched file invalidates. Document the plain-float policy in scenerender/geo/__init__.py. Marks the d3 gate as a required CI test.

* Python files: scenerender/geo/__init__.py (touch); scenerender/geo/camera.py (touch); scenerender/geo/data.py (touch: cache)
* Tests: tests/test_geo_d3_parity.py (new)
* Rust reference: crates/sr-geo/tests/d3.rs (whole file, 210 lines); crates/sr-geo/tests/fixtures/d3.json, countries-110m.json; tools/fixtures/make_geo_expected.mjs (per README)
* **Acceptance:** 
  * port `crates/sr-geo/tests/d3.rs::topojson_decodes_like_topojson_client, projected_countries_match_d3, fits_match_d3, fly_to_paths_match_d3_interpolate_zoom`: the four d3 gates in one suite
  * statistical: All four d3 gates pass at the tolerances above; wall time budget 60 s.
* Risks: None beyond P4-MAPS-05 performance.

#### P4-MAPS-10: Map asset renderer: frame, background/outline, geoLayer fills/strokes/dots, filters, featureStyle, seam-free same-paint merge

Rows: MAPS-15, MAPS-17 · size M · track P4-M2 · group B · schema adoption: yes · depends on: P4-MAPS-01, P4-MAPS-06, P4-MAPS-08

scenerender/assets/map.py (ASSETS 'map' level FULL, ASSET_SIZES 'map'): draw the map in its width x height box via pycairo under M. Order exactly as text_map.rs map_drawing_as: raster basemap tiles beneath everything (hook for P4-MAPS-19), background fill of the globe/world outline (Sphere projected), children in document order (basemap, geoLayer, graticule, route, pin), outline stroke over the sphere (outlineWidth default 1), clip to the frame (hook P4-MAPS-20). geoLayer: polygons fill (nonzero rule) + stroke (round cap/join, miter limit 4), lines stroke drawn on by progress (trim by ground distance like route), points as dots of pointRadius, filter 'prop=value;prop!=value' (number compare when both parse, else string; all conditions must hold), opacity, animated fill/stroke/strokeWidth/opacity/progress through the evaluator. featureStyle: per-feature override by keyBy property (default feature id), animatable paint, strokes/width/opacity override. Same-paint merge: consecutive features with identical fill are filled as ONE path so shared borders leave no AA seam; strokes follow after the run of fills. Paints go through the existing paint.py resolver (solid, gradients, var()). Frame-time evaluation reads animated attributes by element key.

* Python files: scenerender/assets/map.py (touch, new content); scenerender/assets/map_draw.py (new: Painter, merge, filters); scenerender/assets/__init__.py (touch: import map); scenerender/geo/model.py (touch)
* Tests: tests/test_map_render.py (new)
* Rust reference: crates/sr-gpu/src/text_map.rs (Painter 113-170, map_drawing_as 120-, geoLayer branch 196-370, keep() 80-98, polys_of_rings); README Maps Content; crates/sr-gpu/tests/maps.rs equirectangular_world_places_land_and_sea 51, maps_stop_at_their_frame 327
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/world-map.scene.xml` (new): Equal Earth world, FRA highlighted by featureStyle key 250, graticule 15 Expect: PSNR >= 30 dB; checked pixels of case world-map t=1.0 pass
  * scene `tools/parity/scenes/maps/projections-grid.scene.xml` (new): 11 projections of countries-110m, stroke 0.5 Expect: CLOSE (>=30 dB); no seam pixels between same-fill neighbours
  * evidence cases to turn native: world-map
  * port `crates/sr-gpu/tests/maps.rs::equirectangular_world_places_land_and_sea`: Congo/Algeria/Brazil/Australia white, Atlantic/Pacific/Indian ocean blue pixel-exact within 1e-3 at 1px/degree
  * port `crates/sr-gpu/tests/maps.rs::maps_stop_at_their_frame`: content beyond the frame is not drawn; 8px strokes at the edge are cut
  * port `crates/sr-gpu/tests/maps.rs::(featureStyle) animated feature style changes colour; border pixel filled without seam`: animated featureStyle fill and shared-border pixel
  * statistical: Per-region mean colour (land/sea samples listed in cases.json world-map) within tol 0.03 of Rust; whole-frame PSNR >= 30 dB (cairo vs Rust rasteriser AA differs, bit parity impossible). No seed.
* Risks: AA differs between cairo and Rust's tile rasteriser; gate on regional means and PSNR class not bit parity.; Cairo needs path simplification for 177 polygons x several zoom keyframes; cache the projected Planar per (setup,view).

#### P4-MAPS-11: Choropleth, feature labels, graticule, globe/world background and outline

Rows: MAPS-16, MAPS-18 · size M · track P4-M2 · group B · schema adoption: yes · depends on: P4-MAPS-10

assets/map_draw.py: choropleth: fillBy numeric property through domain (data range when absent; must increase, C53) and scale linear|log|sqrt|quantize onto palette (colours interpolated in sRGB as d3 interpolateRgb, piecewise one stop per colour, quantize picks one colour per equal step), noData paint for missing values. graticule(step): d3 geoGraticule layout (minor meridians stop at +-80, those on multiples of 90 reach the poles, parallels to +-80), stroke #FFFFFF40 default. label: property text drawn at each feature's centre = area-weighted centroid of the largest projected ring, textStyle IDREF -> existing text style machinery (Pango via assets/text.py helpers, Align centre). Background/outline of globe or world already in P4-MAPS-10 are completed here for Sphere. Not in Rust (do not add): label collision for pins and geo layers.

* Python files: scenerender/assets/map_draw.py (touch)
* Tests: tests/test_map_choropleth.py (new)
* Rust reference: crates/sr-gpu/src/text_map.rs (Scale 5-47, graticule 49-70, label_anchor 94-112); crates/sr-gpu/tests/maps.rs choropleths_map_values_to_the_palette 64; schema geoLayerType 1217-1260
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/choropleth.scene.xml` (new): fillBy numeric id/property over 177 countries with four scale types, quantize palette of 5, noData #888 Expect: region-mean colours equal Rust within 0.02; PSNR >= 30 dB
  * port `crates/sr-gpu/tests/maps.rs::choropleths_map_values_to_the_palette`: linear, log, sqrt, quantize colours and no-data colour at probe features
  * statistical: Choropleth colour per feature (sRGB 8-bit) exactly equal after quantisation; whole frame PSNR >= 30 dB.
* Risks: Label text rendering uses Pango vs Rust sr-text: glyph raster differs, gate label on centroid position +-1 px and bbox overlap, not PSNR.

#### P4-MAPS-12: Great-circle routes (ground-distance progress, headRadius, dash) and pins

Rows: MAPS-19 · size S · track P4-M2 · group B · schema adoption: yes · depends on: P4-MAPS-03, P4-MAPS-10

map_draw.py: route from @points ('lon,lat ...') or from the line features of @geo (+filter): each segment is a great circle sampled via sphere.interpolate, progress trimmed along GROUND distance so speed is constant in any projection (ground_trim), stroke/strokeWidth/dash/opacity, round caps; headRadius>0 puts a dot at the trimmed tip filled with headFill (default stroke). pin: lon/lat marker radius 6, fill, stroke, strokeWidth, opacity, optional label offset by labelDx/labelDy (textStyle), hidden when on the far side of a globe or outside the frame (Projection.visible + extent test, as locate()). Animated lon/lat/progress.

* Python files: scenerender/assets/map_draw.py (touch)
* Tests: tests/test_map_route.py (new)
* Rust reference: crates/sr-gpu/src/text_map.rs ground_trim 72-92 and route/pin branches; crates/sr-geo/src/sphere.rs interpolate/distance; crates/sr-gpu/tests/maps.rs globes_hide_the_far_side_and_fly_to_their_targets 111, route stops at half its ground length
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/world-map.scene.xml` (new): Paris-Tokyo route progress 0.5 and Tokyo pin Expect: evidence checks quarter-way yellow, three quarters not drawn, pin colour
  * evidence cases to turn native: world-map
  * port `crates/sr-gpu/tests/maps.rs::globes_hide_the_far_side_and_fly_to_their_targets`: pins on the far side not drawn, fly-to target ends centred
  * statistical: Trim point lon/lat of route at progress p within 1e-6 deg of Rust ground_trim; pixels PSNR>=30.
* Risks: Great-circle sampling density decides curve smoothness; use the same interpolation step as Rust (check text_map.rs route branch).

#### P4-MAPS-13: Expression functions geo() and geoVisible()

Rows: MAPS-14 · size S · track P4-M2 · group C · schema adoption: yes · depends on: P2-EVAL-00, P4-MAPS-08, P4-MAPS-10

geo('mapId',lon,lat) -> [x,y] map pixels (following animation and flyTo) and geoVisible('mapId',lon,lat) -> 1 on the near side of a globe and inside the frame else 0, available in expr.py/vexpr names with compile-time arity check and 'unknown map' runtime error consistent with Rust (sr-eval geo.rs locate 156). Evaluator exposes the map camera at COMPOSITION time t: evaluator builds the View for the named map through scenerender/geo/camera.view(...) with the animated attributes of that map evaluated at t (careful: evaluating a map's animated attributes must not recurse into an expression that calls geo() of the same map). So a photo layer rides a spinning globe and fades out when hidden. Document that geo/geoVisible require schema 1.1+ (V6 gate handled by BOB-PHASE1).

* Python files: scenerender/expr.py (touch: names, hub); scenerender/evaluator.py (touch: env entries geo/geoVisible, hub); scenerender/geo/camera.py (touch)
* Tests: tests/test_geo_expr.py (new)
* Rust reference: crates/sr-eval/src/geo.rs locate 156-163; crates/sr-eval/src/eval.rs geo/geoVisible builtins; README Maps 'Placing any layer'; crates/sr-gpu/tests/maps.rs expressions_place_layers_on_the_map 200
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/expr-geo.scene.xml` (new): pin-like text layer x=geo('globe',2.35,48.86)[0] on a spinning globe (centerLon animated), opacity=geoVisible(...) Expect: eval-level layer x/y/opacity equal Rust to 1e-6 at 10 times (eval_parity.py), pixels CLOSE
  * port `crates/sr-gpu/tests/maps.rs::expressions_place_layers_on_the_map`: geo() returns the pixel where Paris lands and geoVisible flips to 0 when the globe turns away
  * non-pixel: tools/parity/eval_parity.py (P2-EVAL-00): layer transform fields of expr-geo.scene.xml equal `scene-render eval --format json` within 1e-6 at t=0..5.
* Risks: Cross-area: the Python evaluator's cache design must key frames by time only; geo() reads another asset's animation at t.

#### P4-MAPS-14: Tile math and PMTiles v3 reader/writer

Rows: MAPS-20, MAPS-21 · size M · track P4-M3 · group D · schema adoption: yes · depends on: P4-MAPS-01, P4-MAPS-04

scenerender/geo/tiles.py: lonlat(z,x,y), tile_xy, map_zoom(projection)=log2(2pi scale/512), tile_zoom(proj,tile_size,min,max), screen_lonlat(proj,x,y) (web-mercator inversion else None), Tile ordering. scenerender/geo/pmtiles.py: Header.parse (magic, version 3, offsets, compressions, tile type, min/max zoom, bounds), Hilbert tile_id/tile_zxy, varint directory parse/serialize (columnar), root + leaf directories, internal/tile compression none/gzip (stdlib) with size-BOUNDED decompression (decompress_within; mislabeled gzip, header claiming more than file holds, u32 truncation of directory columns are errors), Archive.open/tile(z,x,y) with run-length entries and per-process cache, TileType raster (png,jpeg,webp)/mvt, metadata JSON (attribution), sha256/cacheSha256 pin check, and write(tiles,type,compression,metadata) used by tests and the cache. Web Mercator tile geometry must equal the view's affine map exactly.

* Python files: scenerender/geo/tiles.py (new); scenerender/geo/pmtiles.py (new)
* Tests: tests/test_geo_pmtiles.py (new)
* Rust reference: crates/sr-geo/src/tiles.rs (lonlat 29, tile_xy 37, map_zoom 44, tile_zoom 49, screen_lonlat 56, tests 273-358); crates/sr-geo/src/pmtiles.rs (Header 53-150, tile_id 155, tile_zxy 178, parse_directory 250, decompress_within 314, Archive 351-433, write 435, tests 519-622); crates/sr-geo/tests/tiles.rs, bounded_tiles.rs, fixtures tiles.json
* **Acceptance:** 
  * port `crates/sr-geo/tests/tiles.rs::tile_ids_match_the_reference`: Hilbert ids equal the pmtiles reference for tiles.json cases
  * port `crates/sr-geo/tests/tiles.rs::a_protomaps_extract_reads_like_the_reference`: baixa.pmtiles header, metadata and tile bytes equal reference
  * port `crates/sr-geo/tests/tiles.rs::raster_archives_from_the_reference_writer`: raster.pmtiles tiles decode to the expected colours (image_rgb)
  * port `crates/sr-geo/tests/bounded_tiles.rs::uncompressed_and_mislabeled_gzip_payloads_obey_the_requested_bound / directory_columns_cannot_silently_truncate_to_u32`: bounded decompression and u32 guard
  * port `crates/sr-geo/src/pmtiles.rs::archives_round_trip_with_leaf_directories / crafted_directories_are_errors / decompression_is_capped / a_header_cannot_claim_more_than_the_file_holds`: writer/reader round trip and hostile-input errors
  * statistical: Exact equality (integers/bytes); no tolerance.
* Risks: Python recursion/loops over large directories: use memoryview and int varint loops; fine for fixtures.

#### P4-MAPS-15: Mapbox Vector Tile decoder and visible-tile selection (quadtree walk, overzoom, detail)

Rows: MAPS-22, MAPS-23 · size M · track P4-M3 · group D · schema adoption: yes · depends on: P4-MAPS-07, P4-MAPS-14

scenerender/geo/mvt.py: hand-rolled protobuf (varint, length-delimited), Layer(name,extent,version,keys,values,features), Feature(id,type,tags->properties string/int/float/bool, geometry command stream MoveTo/LineTo/ClosePath with zigzag deltas, overflow -> error, field longer than address space truncated), polygons() grouping rings by signed_area (exterior positive, holes negative). tiles.py: visible_within(proj,z,max) one-zoom-at-a-time quadtree walk keeping tiles whose projected outline reaches the frame (through Projection.project of the tile outline; error 'the view shows more than N tiles at zoom Z'), visible, source(tile,max_zoom)->(archive tile, window x,y,size) for overzoom (parent quarter beyond max zoom), Placer(proj,tile,extent,window): lines/polygons/points placed through the exact affine path for web-mercator and through the spherical pipeline otherwise, basemap_zoom(proj,tile_size,detail,raster) = floor (vector) or round (raster) of log2(2pi scale/tileSize)+detail clamped 0..24 (sr-eval geo.rs:259).

* Python files: scenerender/geo/mvt.py (new); scenerender/geo/tiles.py (touch)
* Tests: tests/test_geo_mvt.py (new); tests/test_geo_tiles_select.py (new)
* Rust reference: crates/sr-geo/src/mvt.rs (decode 176, polygons 28, signed_area 43, tests 246-266); crates/sr-geo/src/tiles.rs (visible_within 110, source 138, Placer 149-262, tests 282-358); crates/sr-eval/src/geo.rs basemap_zoom 259
* **Acceptance:** 
  * port `crates/sr-geo/src/tiles.rs::views_pick_the_tiles_they_show / enumeration_stops_at_the_tile_budget / the_fast_path_agrees_with_the_sphere / overzoom_takes_the_parents_quarter / screen_points_invert`: tile selection, budget error, affine-vs-sphere agreement, overzoom window
  * port `crates/sr-geo/src/mvt.rs::a_field_longer_than_the_address_space_is_truncated / coordinates_that_overflow_are_errors`: hostile MVT inputs
  * port `crates/sr-gpu/tests/maps.rs::a_basemap_asked_for_more_tiles_than_a_frame_can_use_is_an_error`: detail=20 gives a render error naming the tile limit, not a hang
  * statistical: Decode of every tile of baixa.pmtiles: layer names, feature counts per layer and geometry vertex counts exactly equal Rust-derived JSON (generate once with a small Rust-free script from the reference MVT, or compare to tiles.json when it contains them); visible tile set exactly equal at 5 views.
* Risks: Placer's affine path must match the spherical path to ~1e-6 (test the_fast_path_agrees_with_the_sphere).

#### P4-MAPS-16: MapLibre style engine: filters (legacy and expression), functions, expression operators

Rows: MAPS-24 · size L · track P4-M3 · group D · schema adoption: yes · depends on: P4-MAPS-15

scenerender/geo/style.py: parse style JSON (version, sources ignored, layers with type/source-layer/minzoom/maxzoom/filter/paint/layout), value type V (null,bool,num,str,color,array,object), colour parsing (hex, rgb(a), hsl(a), names, as in style.rs parse_color), expression evaluator eval(expr,ctx,scope) with the full operator set of style.rs 175-470 (get/has/in/!in/==/!=/</<=/>/>=, all/any/none/!, case/match/coalesce/step/interpolate linear|exponential|cubic-bezier, let/var, zoom, geometry-type, id, concat, upcase/downcase, to-string/number, literal, math ops, coalesce, format/number-format as far as Rust, etc. - read the file; unsupported operators are errors with the same message), legacy filters (all/any/none/has/!has/in/!in/==/!=/</<=/>/>= with $type/$id), legacy zoom functions {stops,base}, property(name,value,ctx), Layer.visible_at(zoom)/paint/layout/accepts(ctx), builtin('protomaps-light'|'protomaps-dark') loading the vendored JSON (P4-MAPS-00). Same numeric semantics (f64, string coercions) as Rust.

* Python files: scenerender/geo/style.py (new)
* Tests: tests/test_geo_style.py (new)
* Rust reference: crates/sr-geo/src/style.rs (V 23, Ctx 97, eval 162-470, is_expression_filter 473, filter 530, default_of 577, property 605, Layer 642, Style::parse 683, builtin 711, parse_color 721, tests 940-959); crates/sr-geo/tests/style.rs protomaps_light_evaluates_like_maplibre 34; fixtures style.json (expected values from MapLibre)
* **Acceptance:** 
  * port `crates/sr-geo/tests/style.rs::protomaps_light_evaluates_like_maplibre`: every paint/layout/filter evaluation of protomaps-light over style.json cases equals the MapLibre reference values
  * port `crates/sr-geo/src/style.rs::colours_parse / legacy_and_expression_filters`: colour parsing and both filter dialects
  * statistical: All style.json reference cases equal (numeric rel <= 1e-9, colours exact 8-bit).
* Risks: Operator list in style.rs is large; port by reading, do not rely on spec memory. numeric string coercion in 'to-string' and 'match' type rules is a classic divergence.

#### P4-MAPS-17: Vector basemap drawing: background, fill, line (width/dash/cap/join/casing), circle, same-paint merge, built-in protomaps styles

Rows: MAPS-25 · size L · track P4-M3 · group E · schema adoption: yes · depends on: P4-MAPS-10, P4-MAPS-14, P4-MAPS-15, P4-MAPS-16

scenerender/geo/basemap_vector.py + assets/map.py hook: for each basemap with vector tiles, choose zoom via basemap_zoom, enumerate tiles (visible_within, MAX_TILES 4096 per frame -> error), decode (cached per archive/z/x/y with a bound), then draw layer by layer through the style: background; fill (fill-color/opacity/antialias, neighbours of one paint merged into one path so tile and feature borders leave no seam); line (width via zoom/exponential interpolation, dasharray, line-cap/join, opacity, casing drawn as full width); circle (radius, colour, stroke). Web-mercator through the exact affine Placer, other projections via the spherical Placer. Layer types outside the supported set are skipped and reported once (as Rust: Run.skipped). Honour basemap/@opacity, @mapStyle (file path or protomaps-light/dark), @detail, @labels=false (drops symbol layers). Cairo drawing with painter path accumulation so merge is one fill call.

* Python files: scenerender/geo/basemap_vector.py (new); scenerender/assets/map.py (touch: basemap hook; hub with P4-MAPS-10/19/20)
* Tests: tests/test_basemap_vector.py (new)
* Rust reference: crates/sr-gpu/src/text_basemap.rs (draw 102-178, features 179-339, push 340, MAX_TILES 24); crates/sr-geo/styles/protomaps-light.json, protomaps-dark.json; crates/sr-gpu/tests/maps.rs vector_basemaps_draw_their_style 242
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/basemap-vector.scene.xml` (new): Baixa z15.5 protomaps-light and protomaps-dark, labels=false (symbol layers excluded to isolate fills/lines) Expect: PSNR >= 30 dB vs Rust; evidence basemap coverage and Tagus colour checks pass
  * evidence cases to turn native: basemap
  * port `crates/sr-gpu/tests/maps.rs::vector_basemaps_draw_their_style`: water, park, road colours at probe pixels; labels cover the land
  * statistical: Fill-colour area fractions per style layer (water, park, building, road, land) of the frame within 2 percentage points of Rust; evidence case basemap checks (colour #80DEEA at (320,306) tol 0.02, coverage). Whole-frame PSNR >= 30 dB for labels=false; bit parity infeasible (cairo AA).
* Risks: Road casing and line width interpolation at fractional zoom 15.5 are where PSNR is lost; verify interpolation base/stops arithmetic against style.rs.; Performance: Python decoding+cairo for a z15 city view: cache decoded tiles and projected paths across frames when only unrelated animation changes.

#### P4-MAPS-18: Symbol layers: label placement without overlap (anchors, radial offsets, wrapping, line labels, halos, sort keys)

Rows: MAPS-26 · size L · track P4-M3 · group E · schema adoption: yes · depends on: P4-MAPS-17

scenerender/geo/labels.py: collect Label{where: point with anchor candidates | along a line, offset in ems, text, size, colour, halo, sort key, layer order} from symbol layers (text-field with tokens/expressions, text-size, text-max-width wrapping, text-anchor and text-variable-anchor candidates, text-radial-offset/text-offset, text-transform, text-letter-spacing as far as Rust, text-halo colour/width, symbol-sort-key, symbol-placement point|line), place_labels(): greedy placement with axis-aligned box overlap test (overlaps()), ORDER = higher style layers first then lower symbol-sort-key (and the exact tie-break of text_basemap.rs 360-488), first candidate anchor that fits wins; line labels follow a straight segment (not the curve) and are kept upright; draw halo then fill with Pango (existing text helpers) at 1x. Icons/sprites/style fonts unsupported (skip, report once like Rust). Placement is determinism-critical: iterate in Rust's order, no sets/dicts with unstable order.

* Python files: scenerender/geo/labels.py (new); scenerender/geo/basemap_vector.py (touch)
* Tests: tests/test_basemap_labels.py (new)
* Rust reference: crates/sr-gpu/src/text_basemap.rs (Label 70-100, place_labels 360-488, overlaps 355); README Basemaps: Vector tiles bullets; crates/sr-gpu/tests/maps.rs vector_basemaps_draw_their_style
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/basemap-vector.scene.xml` (new): same scene with labels=true (street names along lines, point labels) Expect: placed-label set (text,anchor,box) equal to Rust's; PSNR >= 25 dB (Pango vs sr-text glyph differences)
  * evidence cases to turn native: basemap
  * port `crates/sr-gpu/tests/maps.rs::vector_basemaps_draw_their_style (labels part)`: labels drawn without overlapping; streets named
  * statistical: Label placement set: Jaccard similarity of placed (text,layer) pairs vs Rust >= 0.95 over baixa z15.5 (exact order cannot be verified because Rust exposes no placement dump; obtain Rust's set by OCR-free method: run Rust with SR_LOG if a debug hook exists, else count placed labels via glyph-coverage regions per label box). Coverage statistic: fraction of frame area within 1px of text halo/fill colours within 20% relative of Rust.
* Risks: Glyph metrics differ (Pango vs sr-text) so box sizes differ by a few px and greedy placement can legitimately diverge; the gate is statistical, not exact.; Rust gives no placement dump: P4-MAPS-18 should add (in Python only) a debug dump and compare visually; do not edit Rust.

#### P4-MAPS-19: Raster tiles warped into any projection, drawn beneath vector content

Rows: MAPS-27 · size M · track P4-M3 · group E · schema adoption: yes · depends on: P4-MAPS-10, P4-MAPS-14, P4-MAPS-15

scenerender/geo/basemap_raster.py: for raster basemaps (png/jpeg/webp via Pillow), choose zoom = round(log2(2pi scale/tileSize)+detail) (default tileSize 256 raster), enumerate visible tiles, cut each tile into cells (recursive split while a cell crosses the frame edge, text_basemap.rs emit 497-554) whose four corners are projected through the map projection, drawn as textured quads/triangles (bilinear sampling) so tiles bend into any projection and stop at the globe's edge; exact crop when the cell is square to the frame (web-mercator). Painted BENEATH all vector content including the background (assets/map.py order from P4-MAPS-10), opacity honoured; if an opaque background would hide the raster tiles emit the same warning once (warn_once). Overzoom via tiles.source. Implementation: numpy inverse-mapping into the frame buffer (per pixel inverse via screen_lonlat for web-mercator; per-cell affine/bilinear for others) or moderngl textured quads; choose moderngl when GL context is available, numpy otherwise, same output.

* Python files: scenerender/geo/basemap_raster.py (new); scenerender/assets/map.py (touch); scenerender/gl.py (touch only if a helper for textured quads is needed; shared)
* Tests: tests/test_basemap_raster.py (new)
* Rust reference: crates/sr-gpu/src/text_basemap.rs (raster 556-591, emit 497-554, Bitmap cells 489); crates/sr-gpu/src/drape.rs (warp stage 29-63); crates/sr-gpu/tests/maps.rs raster_basemaps_warp_into_the_projection 287; crates/sr-geo/tests/fixtures/raster.pmtiles
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/basemap-raster.scene.xml` (new): raster.pmtiles in orthographic (20E,20N), web-mercator, equal-earth, albers; opacity 0.5 Expect: centre pixel colours = tile z1 (1,0) colour within 0.02; nothing beyond the globe limb; PSNR >= 30 dB for web-mercator, >= 28 for the others
  * evidence cases to turn native: basemap
  * port `crates/sr-gpu/tests/maps.rs::raster_basemaps_warp_into_the_projection`: orthographic globe centre shows the expected tile colour and the rim beyond the limb stays background
  * statistical: Sampled colours at 9 grid points inside the globe: |delta| <= 0.02 per channel vs Rust (bilinear resampling differs sub-pixel); outside-limb pixels exactly background.
* Risks: Cell splitting thresholds change resampling error near the limb; mirror the emit() recursion depth/limits.

#### P4-MAPS-20: Basemap attribution, frame clipping and tile budget errors

Rows: MAPS-28 · size S · track P4-M3 · group E · schema adoption: yes · depends on: P4-MAPS-17, P4-MAPS-19

Attribution: tiles/@attribution (HTML tags stripped, &copy; and &amp; decoded as strip_tags) or the archive metadata 'attribution', drawn small in the bottom-right corner of the map unless basemap/@attribution="false"; map content clipped to the frame (rectangle clip with the 64 px projection margin kept for pins and labels reaching over); tiles carry attribution and sha256 only (no licence attributes). Budget: more than 4096 tiles in a frame is a render error. Python error surfaced through the existing render diagnostics path.

* Python files: scenerender/assets/map.py (touch); scenerender/geo/basemap_vector.py (touch)
* Tests: tests/test_map_attribution.py (new)
* Rust reference: crates/sr-gpu/src/text_map.rs credit 387-430, strip_tags 51-65; README Basemaps Attribution / Clipping; crates/sr-gpu/tests/maps.rs maps_stop_at_their_frame 327
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/basemap-raster.scene.xml` (new): raster archive with attribution='Test tiles' (credit visible) and the same with attribution=false Expect: credit text bbox region has text pixels in the first, background-only in the second
  * port `crates/sr-gpu/tests/maps.rs::maps_stop_at_their_frame`: content beyond the frame is not drawn
  * statistical: Credit region (bottom-right 120x16 px) mean luminance difference between attribution on and off > 3 percent of range, as in Rust.
* Risks: Attribution font differs (Pango): region test, not glyph test.

#### P4-MAPS-21: Tile plan for online tiles: per-frame view enumeration, cache archives, cacheSha256 pinning

Rows: MAPS-29 · size M · track P4-M3 · group F · schema adoption: yes · depends on: P4-MAPS-08, P4-MAPS-14, P4-MAPS-15, P4-MAPS-25, P4-RESOLVE-16, P4-RESOLVE-17

Python side of the resolve cooperation, no network code: scenerender/geo/tileplan.py computes, for every tiles asset with @url, the (z,x,y) set to fetch exactly as sr-resolve tile_set() does: raster detection from the URL extension, tileSize default 512 vector / 256 raster, zmin/zmax from minZoom/maxZoom (error when min>max), for every basemap that references the asset the views of EVERY frame of the document (animated centre/zoom/rotation and flyTo evaluated per frame) at basemap_zoom with detail, plus elevation tiles for 3D terrain at the DEM zoom, plus the whole world at the drape zoom for globes; refuses over SR_TILES_MAX (2000) before any fetch. At render time load @cache (a PMTiles archive) after verifying cacheSha256; a tiles asset with @url but no resolved cache is a render error 'run resolve' (Rust: render side rejects Resolved::Remote; geo.rs tiles_asset 165-183). The fetcher (HTTP, serial, identifying User-Agent, pmtiles write) belongs to the RESOLVE area (RESOLVE-28) and consumes this plan.

* Python files: scenerender/geo/tileplan.py (new); scenerender/geo/model.py (touch)
* Tests: tests/test_geo_tileplan.py (new)
* Rust reference: crates/sr-resolve/src/lib.rs tile_set 769-; crates/sr-eval/src/geo.rs tiles_asset 165-183, archive 185-200; README Basemaps Archives; crates/sr-geo/src/pmtiles.rs write 435
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/neg-remote-tiles.scene.xml` (new): tiles url=https://example.invalid/{z}/{x}/{y}.png with cache absent Expect: Python and Rust both refuse to render with the same actionable error; tile plan JSON equals `scene-render resolve --plan` output when that flag exists, else the set size and zoom histogram equal the Rust log line
  * port `crates/sr-geo/tests/bounded_tiles.rs::(cache archive bounds)`: a pinned cache obeys decompression bounds
  * statistical: Tile set: exact equality of (z,x,y) sets for the flyTo globe scene and the 3D terrain scene; if Rust has no dump flag, equality of count per zoom level via its resolve --dry-run output.
* Risks: Needs Rust's resolve dump to compare (check `scene-render resolve --help` at implementation time); otherwise compare through per-zoom counts.; Plan enumerates every frame: cost scales with duration x fps; subsample only if Rust does (read lib.rs 769-).

#### P4-MAPS-22: 3D map drape: rasterise a map at textureSize, cache by animated state

Rows: MAPS-30 · size M · track P4-M4 · group G · schema adoption: yes · depends on: P4-MAPS-10, P4-MAPS-19

scenerender/geo/drape.py: drape(map_asset, state, size) -> RGBA8 sRGB straight-alpha array for use as a baseColorMap. The map is drawn by the same assets/map.py code on a cairo ImageSurface laid out in map pixels (a flat map: width x height; a globe: the whole world in equirectangular 2H x H, H the map's height, so labels and lines keep their 2D size) and scaled to textureSize (the longer side for primitive=map, the HEIGHT for a globe, width 2x), raster tile bitmaps warped first. Compositing in linear light per Rust drape.rs then sRGB-encode (document paints that have no CPU form draw mid-grey is NOT needed: cairo path draws them natively - record as Python superset). Cache keyed by hash of (map element key, animated props of the map and its children, plus the time when flyTo exists) = render_map3d.rs map_state(); invalidated when the key changes; bounded LRU.

* Python files: scenerender/geo/drape.py (new)
* Tests: tests/test_map_drape.py (new)
* Rust reference: crates/sr-gpu/src/drape.rs rasterize 65-127; crates/sr-gpu/src/render_map3d.rs map_state 25-37, drape 182-250; README 3D maps Drape/Material
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/map3d-globe.scene.xml` (new): drape of equirect world at 512 Expect: drape == 2D map render at the same size within PSNR 40 dB (same cairo path)
  * port `crates/sr-gpu/tests/caches.rs::raster_map_tiles_are_kept_only_while_shown`: raster tile cache entries are evicted when the map stops being shown
  * statistical: Cache: drape recomputed iff state hash changes (counter test); texture size exact.
* Risks: Colour space: Rust encodes sRGB via linear compositing; cairo composites in sRGB. Differences near AA edges only.

#### P4-MAPS-23: object3D primitive=map: flat map ground with material

Rows: MAPS-30 · size M · track P4-M4 · group G · schema adoption: yes · depends on: BOB-PHASE1, P4-MAPS-01, P4-MAPS-22

In the three renderer: primitive='map' builds a plane in the object's xy plane of width x height scene units (ONE map pixel per scene unit, map centre at object origin) with UVs, gets its baseColor texture from the drape (P4-MAPS-22) at textureSize (default 2048, 64..8192), the object's material multiplies the drape (default white, roughness 0.9), resolution grid N x M cells (default 64, 8..256; aspect-preserving as render_map3d.rs). primitive_geo gains 'map' and 'globe' branches that return None for geo but register a map-geo builder (hub: three/scene.py, nodes/scene3d.py). Texture update when the drape key changes; the Python material pipeline takes the array as an in-memory baseColorMap (extend materials.texture() with an 'array:' source or Material.maps injection). Attributes: map IDREF, material, textureSize, resolution, terrain, exaggeration, buildings (the last three in packages 25/26). Errors: map not found / not a map asset (C47/R28 diag).

* Python files: scenerender/three/maps3d.py (new); scenerender/three/scene.py (touch: primitive_geo; hub); scenerender/nodes/scene3d.py (touch; hub); scenerender/three/materials.py (touch: in-memory texture; hub)
* Tests: tests/test_map3d_ground.py (new)
* Rust reference: crates/sr-gpu/src/render_map3d.rs map3d_draws 39-181 (flat branch), ground 251-; crates/sr-gpu/src/drape.rs; README 3D maps
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/map3d-ground.scene.xml` (new): map primitive of a coloured geoLayer, rotationX 90 / 60 / 0, material tint, textureSize 256 Expect: PSNR >= 30 dB vs Rust; non-background coverage within 3 percent
  * evidence cases to turn native: map-3d
  * statistical: 3D draw is GPU-lit: compare per-region mean colour (3x3 grid over the object bbox) within 0.03 and non-background coverage within 3 percent; no PSNR requirement stricter than 28 dB (different rasteriser, shading).
* Risks: Texture orientation: Rust draws the map upright when the object is unrotated; verify Python flip convention against the 2D render.

#### P4-MAPS-24: object3D primitive=globe: sphere wrapped in the world map

Rows: MAPS-33 · size M · track P4-M4 · group G · schema adoption: yes · depends on: P4-MAPS-04, P4-MAPS-23

primitive='globe': sphere of @radius (default 200, segments default 96 clamp 24..512), poles on y, longitude 0 facing the camera (-z), built by rotating the standard sphere by -90 deg about y exactly as render_map3d.rs (vertices, normals, tangents); texture = drape of the map's content over the WHOLE world in equirectangular, laid out 2H x H map pixels, textureSize = drape height. rotationY turns it (negative turns west forward). The material multiplies the drape. Whole-world basemap tiles at the drape zoom come from the resolved cache (P4-MAPS-21). Hook for CINE-25 (globe radial elevation): leave a vertex displacement slot, do not implement terrain on globes here. Mesh cache per (radius,segments).

* Python files: scenerender/three/maps3d.py (touch); scenerender/three/scene.py (touch; hub); scenerender/three/geometry.py (touch: sphere variant with rotation)
* Tests: tests/test_map3d_globe.py (new)
* Rust reference: crates/sr-gpu/src/render_map3d.rs globe branch 122-148, drape 182-250; crates/sr-gpu/tests/maps3d.rs globes_turn_their_map_to_the_camera 28; tests/corpus/valid/globe-relief.scene.xml (for CINE-25 hook)
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/map3d-globe.scene.xml` (new): globe radius 90 with a white square 50W-10W,10S-30N on blue; rotationY -30 and 90 Expect: pixel (100,110) white-ish at -30, blue at 90, background outside
  * evidence cases to turn native: map-3d
  * port `crates/sr-gpu/tests/maps3d.rs::globes_turn_their_map_to_the_camera`: 30W faces the camera at rotationY=-30; 90E is ocean at rotationY=90; pixel outside the sphere is background
  * statistical: Evidence case map-3d check 1 (150,150 = #785050 tol 0.03 raster tile z2 (2,2)); 3x3 mean colours of the globe disc within 0.03 of Rust.
* Risks: UV convention (west forward for negative rotationY) is the known risk (matrix note); pin with the unit test first.

#### P4-MAPS-25: Terrain from elevation tiles (Terrarium, Mapbox Terrain-RGB) on map grounds

Rows: MAPS-31 · size M · track P4-M4 · group G · schema adoption: yes · depends on: P4-MAPS-14, P4-MAPS-23

primitive='map' with @terrain (tiles asset of PNG/WebP elevation tiles), @terrainEncoding terrarium|mapbox (default terrarium), @exaggeration (default 1), @resolution grid: DEM decode Terrarium metres=R*256+G+B/256-32768, Mapbox -10000+(R*65536+G*256+B)*0.1 (f32, tile cache keyed by (archive,z,x,y,enc), capped), elevation(lon,lat) bilinear within a tile (clamped edges - mirror, see rust_may_be_wrong), DEM zoom chosen so one elevation pixel is about one grid cell, grid of nx x ny vertices (nx=resolution, ny=round(resolution*h/w) for landscape and transposed otherwise) raised toward the camera (-z) by (height-h0)*ppm*exaggeration with h0 the height at the map centre and ppm = scene units per metre at map scale (web-mercator metres per pixel at the centre latitude), normals from slope (central differences of the grid), screen_lonlat for the inverse. Only web-mercator maps; other projections drape flat (error/warning as Rust). Missing tile -> height 0. Globe elevation (planetRadius, terrainTileSize, terrainZoom, terrainMissing, terrainMemoryMiB) is CINE-25, not here.

* Python files: scenerender/three/maps3d.py (touch); scenerender/geo/dem.py (new)
* Tests: tests/test_map3d_terrain.py (new)
* Rust reference: crates/sr-eval/src/geo.rs DemEncoding 267, dem 278-312, elevation 314-328; crates/sr-gpu/src/render_map3d.rs ground 251-, z_at 303-308, grid 309-; crates/sr-gpu/tests/maps3d.rs terrain_raises_the_ground_toward_the_camera 80 (hill_archive generator 55-78); fixtures hill.pmtiles
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/map3d-terrain.scene.xml` (new): hill.pmtiles terrain on web-mercator zoom 12 at 0,0 exaggeration 3 resolution 96 textureSize 256, rotationX 90 vs same without terrain Expect: topmost lit row with terrain at least 40 px above flat ground; equals Rust within 4 px
  * scene `tools/parity/scenes/maps/map3d-terrain-mapbox.scene.xml` (new): same hill re-encoded as Terrain-RGB by a test helper Expect: same silhouette within 4 px
  * evidence cases to turn native: map-3d
  * port `crates/sr-gpu/tests/maps3d.rs::terrain_raises_the_ground_toward_the_camera`: topmost lit row of the hill is >=40 px above the flat ground line (2000 m x3 at z12 ~ 314 units)
  * port `crates/sr-eval/tests/terrain.rs::terrain_tile_formats_are_checked_before_numeric_interpretation`: (globe path; CINE-25) skip here
  * statistical: Silhouette top row within 4 px; height at 9 grid vertices equals elevation() reference within 1e-3 m; coverage within 3 percent.
* Risks: Depends on a lit rasteriser reaching the same silhouette; verify normal convention (-z toward camera).; CINE-25 shares dem decode: coordinate with the CINE owner so geo/dem.py is reused rather than duplicated.

#### P4-MAPS-26: Extruded buildings from the vector basemap

Rows: MAPS-32 · size M · track P4-M4 · group G · schema adoption: yes · depends on: P4-MAPS-15, P4-MAPS-23, P4-MAPS-25

primitive='map' with buildings='true' on a web-mercator map whose FIRST vector basemap has a layer named 'buildings' or 'building' (source-layer): decode the footprints of the view's tiles (MVT polygons with holes), height from properties 'height' else 'render_height' else 8 m, base from 'min_height' else 'render_min_height' else 0, standing on the terrain when terrain is set, in the object's material (not the drape), walls plus flat roof triangulated with holes (use shapely for validity and the existing geometry.extrude_polygons triangulator, or its cap triangulation for roofs), metres converted to scene units via the same ppm as terrain, scaled by exaggeration as Rust does. Dedupe features across tile borders as Rust does (verify). Returned as a second draw with its own cache key.

* Python files: scenerender/three/maps3d.py (touch); scenerender/three/geometry.py (touch: extrude footprint helper; hub)
* Tests: tests/test_map3d_buildings.py (new)
* Rust reference: crates/sr-gpu/src/render_map3d.rs buildings branch 360-422 (height 375, min_height 376); crates/sr-gpu/tests/maps3d.rs buildings_rise_from_the_basemap 108; fixtures baixa.pmtiles
* **Acceptance:** 
  * scene `tools/parity/scenes/maps/map3d-buildings.scene.xml` (new): Baixa buildings=true in a red stone material seen edge-on Expect: red pixel count with buildings exceeds flat by the same factor (+- 20 percent) as Rust; evidence map-3d coverage check passes
  * evidence cases to turn native: map-3d
  * port `crates/sr-gpu/tests/maps3d.rs::buildings_rise_from_the_basemap`: edge-on red pixel count is much larger with buildings=true than flat
  * statistical: Red-coverage count ratio (buildings/flat) within 20 percent of Rust; evidence case map-3d buildings coverage (see cases.json) within its bounds.
* Risks: Triangulation with holes: the existing extrude helper may cap with a different triangulator but the silhouette is what is tested.; Footprint dedupe across tiles can double-draw walls; check against Rust.

### RESOLVE

#### P4-RESOLVE-00: Resolve parity harness and the reference example provider (offline, mock)

Rows: RESOLVE-18 · size M · track P4-R1 · group R1 · schema adoption: no · depends on: none

Build the offline test rig every other RESOLVE package is gated by. (1) tools/parity/example_provider.py: a line-for-line port of crates/sr-resolve/src/bin/example_provider.rs speaking protocol v1 on stdin/stdout: speech/music/sound-effect = 16-bit mono WAV sine (freq 220 + (seed%8)*55 Hz, amplitude 0.5, rate = max(sampleRate,8000), duration = request.duration else 0.3 s/prompt word (speech, min 1 word) else 1.0 s; only .wav accepted), image = solid PNG of width x height (default 64x64) coloured by FNV-1a-32 of the prompt (h>>16,h>>8,h bytes), transcribe task = 10 ms hop loudness runs (peak > 0.01, joined across gaps <= 5 hops) each labelled with the next prompt word or 'wordN', one segment, answer {ok,version:'example 1'}; appends '<id> <kind>' to <baseDir>/.example-calls when SR_EXAMPLE_LOG is set; extra, harness-only, SR_EXAMPLE_KEEP_INPUT copies request.input to <baseDir>/.example-input-<id>.wav so mono-WAV inputs of both engines can be compared. Because the Rust example binary is NOT built in /home/admin/src/rs-scene-render/target/release (only scene-render) and that tree is read-only, BOTH engines are pointed at this one script with SR_PROVIDER_EXAMPLE='python3 tools/parity/example_provider.py' so provider output is byte-identical by construction and any difference is the resolver's. (2) tools/parity/resolve_parity.py: for each scene in tools/parity/scenes/resolve/ copy it twice into scratch project folders, run `scene-render resolve FILE --json` (SR_RS_BIN / --rs) in one and `python -m scenerender resolve FILE --json` in the other with identical env (SR_RESOLVE_STORE=<scratch>/store, SR_EXAMPLE_LOG=1, PATH extended with the ffprobe dir), then compares: exit code, JSON rows (id, element, provider, cache, status, sha256, notes, message prefix), the rewritten document bytes, every cache file sha256, sidecar JSON (key, sha256, provider, version, request fields except output/workdir/baseDir paths), example-call counts, and store file names. Statuses PASS / DIFF / RS-ERROR / PY-ERROR / PY-MISSING (before the CLI package lands, the harness degrades to running only the Rust side and prints the golden values in tools/parity/scenes/resolve/golden.json: keys, digests, statuses). Also adds a read-side pixel check: after resolving, hands the pinned scene to render_parity.py (generated image drawn from the verified cache) - the RESOLVE-01 regression gate. (3) Scenes: narrated (mirrors tests/resolve.rs::narrated), images-errors, collision, comments-layout, resolved-image.

* Python files: tools/parity/example_provider.py (new); tools/parity/resolve_parity.py (new); tools/parity/scenes/resolve/narrated.scene.xml (new); tools/parity/scenes/resolve/images-errors.scene.xml (new); tools/parity/scenes/resolve/collision.scene.xml (new); tools/parity/scenes/resolve/comments-layout.scene.xml (new); tools/parity/scenes/resolve/resolved-image.scene.xml (new); tools/parity/scenes/resolve/golden.json (new); tools/parity/render_parity.py (touch only if --extra-path/env passthrough is missing)
* Tests: tests/test_resolve_harness.py (new; skips without SR_RS_BIN; unit tests of example_provider.py run always: WAV length/frequency, PNG colour, run grouping, call log)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/bin/example_provider.rs (whole file, 166 lines); /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:12-56 (setup/project/opts/calls/narrated helpers); /home/admin/src/rs-scene-render/crates/scene-render/src/main.rs:659-705 (rows printed, exit code)
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/narrated.scene.xml` (new): 6 s scene, version 1.2: generated speech 'vo' (provider=example, prompt 'hello there world', cache gen/vo.wav, ZERO digest), audioTrack voice start=2, captionTrack subs transcribe=voice provider=example prompt=Hello cache gen/subs.json. Verbatim shape of resolve.rs::narrated(). Expect: Harness runs Rust resolve twice (cold then warm): cold rows made,made; warm up-to-date,up-to-date; example-calls=2 both times. Golden values recorded in golden.json. Python column PY-MISSING until P4-RESOLVE-09.
  * scene `tools/parity/scenes/resolve/resolved-image.scene.xml` (new): 64x64 scene with one generated image (provider=example prompt 'a red door' width=32 height=16 cache pic.png) drawn full-frame on a layer. Harness resolves it with Rust, then renders both engines from the pinned result. Expect: render_parity class MATCH (>=45 dB) at t=0: the read side of RESOLVE-01 stays at parity (regression gate for PRESENT rows RESOLVE-01/02/32).
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::setup() + project()/opts()/calls() helpers`: the example provider is selected through SR_PROVIDER_EXAMPLE and logs one line per call
  * non-pixel: Self-test: resolve_parity.py --rs-vs-rs on the five scenes reports PASS for every comparison (proves comparison/normalisation is correct); then the Rust golden.json equals a fresh Rust run; example_provider.py unit tests pass offline with no ffmpeg/whisper/piper/network.
* Risks: The Rust example provider is not built here; the Python port must be exactly equal in observable output only for the two engines to be comparable, and exactness is guaranteed because both call the same script. Do not 'fix' the script to differ from the Rust binary semantics (documented in golden notes). resolve needs ffprobe on PATH for Rust audio/video validation (use the brief's static_ffmpeg dir).

#### P4-RESOLVE-01: Protocol v1 request model and content-addressed request key

Rows: RESOLVE-07 · size S · track P4-R1 · group R1 · schema adoption: no · depends on: none

scenerender/resolve/protocol.py: Request dataclass mirroring protocol.rs (protocol=1, task generate|transcribe, kind image|video|speech|music|sound-effect|captions|tiles, id, provider, model, prompt, voice, language, seed, width, height, fps, duration, sampleRate, bitDepth, timeline{projectDuration,start?}, input?, inputSha256?, output, workdir, baseDir), Response {ok,version,error,notes}, Timeline, to_json() emitting compact serde_json-compatible bytes: FIXED field order as the Rust struct (protocol, task, kind, id, provider, model, prompt, voice, language, seed, width, height, fps, duration, sampleRate, bitDepth, timeline, input, inputSha256, output, workdir, baseDir), camelCase, None fields skipped (task is always emitted), separators (',',':'), ensure_ascii=False, floats as shortest round-trip (Python repr matches ryu for the ranges in documents: 30.0, 2.5; guard exponent forms like 1e-05 -> 1e-5 and 1e21 -> 1e21 via a small formatter), integers as ints. Request.key(): clear id/workdir/baseDir, input=None, output reduced to '.'+lower-case extension ('' when none), sha256 of to_json bytes, then for each model file (providers.model_files hook; piper .onnx + .onnx.json, whisper ggml-*.bin) append b'\0model\0' + the file's sha256 hex (or its path string when unreadable). file_sha256 (1 MiB chunks), hex. `model_files` is a registry hook the provider packages fill, so this package has no dependency on them. Interop target: a key computed by Python for a request equals the key Rust wrote in <cache>.resolve.json for the same document, so sidecars and the SR_RESOLVE_STORE are shareable between engines (except transcriptions - see risks).

* Python files: scenerender/resolve/__init__.py (new, hub); scenerender/resolve/protocol.py (new)
* Tests: tests/test_resolve_protocol.py (new)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/protocol.rs:14-143 (types, Request::key 87-109, file_sha256); /home/admin/src/rs-scene-render/crates/sr-resolve/src/protocol.rs:149-166 test keys_ignore_paths_and_ids_but_not_content; /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs:37-57 model_files; /home/admin/src/rs-scene-render/crates/sr-resolve/tests/local_models.rs (model content participates in the key)
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/narrated.scene.xml`: Key conformance: harness reads the Rust-written gen/vo.wav.resolve.json (key + request) from the P4-RESOLVE-00 run, rebuilds Request.from_sidecar(request) in Python and recomputes key(). Expect: Python key == Rust key byte for byte for vo (generated speech, no inputSha256). Also for images-errors pic. captionTrack keys are NOT compared here (inputSha256 depends on the mix).
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/protocol.rs::keys_ignore_paths_and_ids_but_not_content`: equal keys when only id, workdir and output directory/case of extension differ; different prompt or extension changes the key
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/local_models.rs::local model keys hash file contents (piper .onnx/.onnx.json, whisper model)`: same bytes at different relative paths -> same key; changed bytes -> new key
  * non-pixel: Golden: 10 hand-built Requests (every optional present/absent, fps 30000/1001 as f64, unicode prompt with control chars, ints > 2^31, duration 2.5) -> key compared with values produced by Rust sidecars from the Rust resolve of generated scenes with those attributes (golden.json).
* Risks: Float formatting corner cases (exponent form, -0.0, large ints in f64 fields) differ between ryu and repr: restrict to documented attribute ranges and add the formatter test. A key mismatch silently disables store sharing, so the golden-vector gate is mandatory.

#### P4-RESOLVE-02: Text-preserving in-place attribute edit (set_attr)

Rows: RESOLVE-11 · size S · track P4-R1 · group R1 · schema adoption: no · depends on: none

scenerender/resolve/docedit.py: set_attr(text, element, id, attr, value) -> new text by a byte-range scan, never an lxml serialise: find_tag skips comments, CDATA sections and processing instructions (an unterminated comment hides the rest => error), requires the element name to end at whitespace, '/' or '>', skips quoted values while finding the tag end, matches the id by reading attributes in order (attr_span) so text such as id='a' inside another attribute value never matches; replaces the value in place keeping the original quote style and layout, or appends ` attr="value"` before '/>' or '>' when absent. Error text: `<name id="id"> not found in the document text`. Used for generated, captionTrack and tiles cacheSha256. Works on str; the engine reads/writes UTF-8 without newline translation (open(..., newline='')).

* Python files: scenerender/resolve/docedit.py (new)
* Tests: tests/test_resolve_docedit.py (new)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/doc.rs:1-110 (find_tag, attr_span, set_attr); /home/admin/src/rs-scene-render/crates/sr-resolve/src/doc.rs:112-160 tests; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:578,640,742 (call sites)
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/comments-layout.scene.xml` (new): narrated-style document with a leading comment, single-quoted attributes, CRLF line endings, a decoy <generated> inside a comment and inside CDATA, two generated assets, attributes spread over several lines, tab indentation. Expect: After `resolve` in both engines the document bytes are IDENTICAL between Rust and Python and differ from the input only inside the cacheSha256 values (and an appended cacheSha256 on a tag that lacked it).
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/doc.rs::edits_only_the_attribute`: only the attribute value changes; single quotes, multi-line tags, absent attribute appended before '/>' and '>', longer element name sharing a prefix, id text inside another attribute value, missing id is an error
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/doc.rs::tags_in_comments_cdata_and_instructions_are_not_elements`: decoys in <!-- -->, CDATA and <? ?> are ignored; unterminated comment hides the rest
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::speech_and_its_captions_resolve_and_pin (strip() assertion)`: after pinning, the document equals the original with only cacheSha256 values replaced
  * non-pixel: Property test: for random documents from tests/fixtures/*.xml, set_attr on every generated/captionTrack id then restoring the old value yields the original bytes (round trip), and the lxml tree of the edited text equals the tree of the original except that attribute.
* Risks: Rust slices are byte offsets; Python str indices are code points. Keep everything in str and avoid mixing bytes (the scan characters are all ASCII so results are equal). Preserve BOM and CRLF.

#### P4-RESOLVE-03: Cache path containment, destination preflight, atomic writes, private scratch, per-document lock

Rows: RESOLVE-14, RESOLVE-15 · size M · track P4-R2 · group R2 · schema adoption: no · depends on: P4-RESOLVE-01

scenerender/resolve/safety.py. (a) local_in(src, base, root): resolve a cache attribute exactly as assets::resolve (relative to base, absolute, file: URI, percent-decoded; http(s) -> error '<url>: a remote cache cannot be written'), then lexical normalisation (apply . and .. without touching the FS) must lie strictly inside root (error text contains 'outside the project folder ... SR_RESOLVE_ROOT'), then physical(): realpath of the longest existing ancestor + the not-yet-existing tail, dangling symlink = error, must still be inside the physical root ('resolves outside the project folder'). SR_RESOLVE_ROOT (non-empty) replaces the document folder as root. (b) preflight_destinations(text, document, base): over all <generated>, <tiles url=...>, <captionTrack transcribe=...> cache attributes build destination list [(sidecar, '<id> sidecar'), (cache, id)] and a protected list [document, document+'.resolve.lock', every local src/cache/model attribute (model only when it is a file)]; error if any destination overlaps (prefix either way) a protected path ('destination for ID (PATH) conflicts with protected input PATH') or an earlier destination ('... conflicts with OWNER (PATH); use a distinct cache for each target'). Unselected (--only) targets still reserve their paths. (c) replace_atomic(path, writer): exclusive create (O_CREAT|O_EXCL|O_NOFOLLOW) of '<path>.part-<pid>-<n>', write, os.replace, remove temp on failure; copy_atomic, write_atomic. (d) Scratch: tempfile.mkdtemp(prefix='scene-render-resolve-') mode 0700, removed on exit, never reuses a planted symlink name. (e) DocumentLock: open '<realpath(doc)>.resolve.lock' (create, no truncate), fcntl.flock(LOCK_EX|LOCK_NB), message '<doc>: another resolver holds the document lock: ...', lock file is left in place; not taken in --check mode. Windows: msvcrt.locking fallback.

* Python files: scenerender/resolve/safety.py (new)
* Tests: tests/test_resolve_safety.py (new)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:115-141 replace_atomic/copy_atomic; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:163-181 Scratch; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:183-242 lexical/physical/local_in/local; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:246-297 preflight_destinations; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:486-514 DocumentLock; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:902-946 unit tests
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/collision.scene.xml` (new): two generated images with different prompts and the SAME cache shared.png over a pre-existing shared.png ('previous cache'). Expect: Both engines: exit 2 (error: ... conflicts with ...), shared.png unchanged, document unchanged, example calls 0, with and without --only a.
  * scene `tools/parity/scenes/resolve/images-errors.scene.xml` (new): includes caches '../outside.wav', '/etc/passwd-like absolute', file:///outside, https://example.com/x.wav, and a symlink 'link -> /tmp' directory used in a cache path. Expect: Both engines emit an error row per offending target (messages contain 'outside the project folder' / 'remote cache cannot be written' / 'resolves outside'); the compliant targets are still made.
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs::caches_stay_inside_the_root`: relative/%20/file: URI forms accepted; ../x, absolute, ., a/../.., sibling folder, remote refused; wider SR_RESOLVE_ROOT admits ../media
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs::scratch_is_private_and_does_not_reuse_existing_paths`: 0700 scratch, planted symlink untouched, removed on drop
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::cache_symlinks_cannot_escape_the_project / caches_support_project_and_internal_directory_symlinks / caches_outside_the_project_are_refused / temporary_symlinks_cannot_redirect_resolver_writes`: symlink cache dirs pointing out are refused, inside are fine, temp-file symlink is never followed
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::destination_plan_preserves_document_and_inputs_before_generation / destination_plan_detects_document_symlink_aliases / different_requests_cannot_share_a_cache_even_with_only_filter`: document, lock, inputs and duplicate caches are never overwritten; nothing is generated
  * non-pixel: Concurrency check: two Python processes resolving the same document - the second exits non-zero with the 'another resolver holds the document lock' message and leaves caches/document untouched.
* Risks: Windows is out of scope for the dev box; keep the fcntl path correct and the msvcrt branch compile-checked only. symlink behaviour of os.replace over a symlink destination must be checked (Rust rename replaces the link itself).

#### P4-RESOLVE-04: Output content validation and FFmpeg conversion to the cache extension

Rows: RESOLVE-16, RESOLVE-23 · size S · track P4-R1 · group R1 · schema adoption: no · depends on: P4-RESOLVE-01

scenerender/resolve/content.py: validate_content(path, kind) -> None | error string: empty file -> 'empty provider output'; image -> PIL Image.open + verify (plus load of first frame); captions -> parse with the Rust from_transcript contract (strict JSON; segments with start/end/text/words, or words[]; Rust rejects segments lacking 'end' - see tests malformed_transcription_is_not_published: {"segments":[{"start":0,"text":"lost"}]} must FAIL) implemented as captions.cache_json_cues PLUS a strict transcript check module-private to resolve (do not tighten the render-side reader, which stays a superset); tiles -> PMTiles header check (delegates to MAPS-21 reader when present, else magic+version) ; video -> a video stream with width>0 and height>0 via assets.probe_media; speech/music/sound-effect -> an audio stream with rate>0 and channels>0 - implemented FIRST with a pure-Python RIFF/WAVE header parser (wave module + float WAV), falling back to ffprobe/ffmpeg probe for other containers, so validation works on this dev box which has no ffmpeg; unknown kind -> 'unknown generated content kind K'. convert(src, dst): same lower-case extension -> shutil.copyfile; otherwise `ffmpeg -v error -nostdin -y -i SRC -frames:v 1 DST` using audio.io.ffmpeg_exe (error text 'FFmpeg failed (...): tail'), under the shared deadline of P4-RESOLVE-05 (takes an optional run() callable so it can be unit tested without ffmpeg).

* Python files: scenerender/resolve/content.py (new); scenerender/assets/__init__.py (touch only to expose a header-only WAV probe if useful; shared)
* Tests: tests/test_resolve_content.py (new)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:317-344 validate_content; /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs:304-319 convert; /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:717-785 (invalid_provider_content_never_replaces_a_valid_cache, matching_digest_does_not_make_invalid_cache_content_current, malformed_transcription_is_not_published); /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:273-294 the_store_keeps_output_formats_distinct; /home/admin/src/rs-scene-render/crates/sr-text/src/captions.rs:470-520 from_transcript
* **Acceptance:** 
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::malformed_transcription_is_not_published`: a transcript with a segment lacking 'end' is an error row and nothing is published
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::invalid_provider_content_never_replaces_a_valid_cache`: 'not media' fails for image/speech/video
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::matching_digest_does_not_make_invalid_cache_content_current`: digest-consistent but invalid content is stale in check mode
  * non-pixel: Table-driven: {empty, truncated WAV, 0 Hz WAV, 0-channel header, PNG, truncated PNG, JSON transcript variants (Rust-accepted vs rejected), tiny MP4 when ffmpeg exists} run through the Rust validator indirectly (via resolve of a provider writing that file; status error vs made) and through validate_content: accept/reject must agree on every row.
* Risks: Rust accepts whatever sr_media::probe opens (any container FFmpeg understands); the pure-Python WAV path is a subset, so non-WAV audio goes through ffprobe and is untestable here without ffmpeg (skip with marker). Transcript strictness: confirm exact from_transcript required fields against sr-text before coding.

#### P4-RESOLVE-05: Provider framework: lookup order, external-program protocol, shared deadline, bounded process I/O

Rows: RESOLVE-17, RESOLVE-19, RESOLVE-29 · size M · track P4-R3 · group R3 · schema adoption: no · depends on: P4-RESOLVE-01

scenerender/resolve/providers/__init__.py + process.py. Provider base class {name, cloud=False, run(req)->Response}; find(name): SR_PROVIDER_<NAME> (upper-case, '-'->'_', command split on whitespace like split_whitespace, non-empty) -> External; else shutil.which('scene-render-provider-<name>') (execute bit required on unix) -> External; else built-ins whisper|piper|audioforge|openai|elevenlabs|tiles looked up in a lazy table (modules imported by name so provider packages stay file-disjoint); else error 'unknown provider "X": install a `scene-render-provider-X` program, set SR_PROVIDER_X, or use whisper, piper, audioforge, openai or elevenlabs'. External.run: write the request JSON to stdin, take the LAST non-empty stdout line as the answer; success only if answer.ok and exit 0; {ok:false} -> its error (or 'failed'); otherwise 'provider N exited with STATUS without a JSON answer: <last 3 non-empty stderr lines joined ' | '>'. execute(cmd, what, body, timeout): the one place subprocesses run: new process group (start_new_session), stdin written by a thread while stdout/stderr are drained by threads keeping only the last 1 MiB, one monotonic deadline = min(per-call timeout, thread-local DEADLINE set by invoke()), SR_PROVIDER_TIMEOUT positive integer seconds default 1800 (else 'SR_PROVIDER_TIMEOUT must be positive seconds'), on expiry killpg(SIGKILL)+wait and error '<what> timed out' (also for blocked stdin or inherited pipes: the 'stdout/stderr/stdin timed out' variants). invoke(provider, req) installs the shared per-target deadline covering retries and conversions. run(cmd, what) -> stdout text or '<what> failed (<status>): <tail>'. which(), tool(env, default), shell_split(s) (quotes group, empty quoted string is an argument), tail(). Also model_files hook registry consumed by P4-RESOLVE-01.

* Python files: scenerender/resolve/providers/__init__.py (new); scenerender/resolve/providers/process.py (new); scenerender/resolve/providers/external.py (new)
* Tests: tests/test_resolve_providers.py (new)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs:24-123 (Provider, External, provider_timeout, invoke); /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs:125-213 execute; /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs:245-302 which/tool/run; /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs:321-378 find/shell_split; /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs:426-543 tests; /home/admin/src/rs-scene-render/crates/sr-resolve/src/protocol.rs:1-10 (answer protocol); /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:205-226 images_and_errors
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/images-errors.scene.xml`: provider=example (made), provider=openai without --allow-cloud (error mentioning --allow-cloud; needs P4-RESOLVE-07), provider=no-such-provider. Expect: Unknown-provider row message contains 'scene-render-provider-no-such-provider' in both engines; example row made with the same sha256.
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs::deadline_covers_blocked_stdin_and_inherited_pipes`: a child that never reads stdin or leaves a grandchild holding the pipes still times out in < 3 s with 'timed out'
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs::builtin_execution_honours_deadline_and_bounds_output`: run() times out after SR_PROVIDER_TIMEOUT=1 and caps captured stdout at 1 MiB
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs::test_external_provider_timeout_reaps_the_child`: hung provider is killed and reaped quickly
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs::test_external_provider_drains_output_while_sending_request`: 256 KiB stderr+stdout noise while a 256 KiB request is written does not deadlock
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs::executable_lookup_requires_unix_execute_permission / executable_lookup_honours_windows_pathext`: which() needs the x bit on unix; PATHEXT handling
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs::prompts_split_like_a_shell`: shell_split('duration=auto bpm=96 key=\'A minor\'') and empty quoted arg
  * non-pixel: Protocol conformance: Python External run against the Rust-compatible example provider for answer shapes {ok:true}, {ok:false,error}, exit 0 with no JSON, exit 1 with ok:true, trailing blank lines, banner lines before the answer - each classified identically to Rust (results recorded from Rust through resolve rows).
* Risks: SIGKILL of the process group needs start_new_session; subprocess.run(timeout=) alone does not reap grandchildren - keep the explicit threads+killpg design. Thread-local deadline must be a contextvar/threading.local because the engine may later run targets in threads.

#### P4-RESOLVE-06: Targets and requests for generated assets

Rows: RESOLVE-06, RESOLVE-30 · size S · track P4-R1 · group R1 · schema adoption: no · depends on: P4-RESOLVE-01, P4-RESOLVE-03

scenerender/resolve/targets.py: scan the document for <generated> in document order and build Target{element, id, cache (Path from safety.local), cache_attr, pinned (cacheSha256), request} with Request fields exactly as lib.rs:545-572: protocol=1, task=generate, kind, id, provider, model, prompt, voice, language, seed (unsigned long), width, height (positiveInteger), fps (rational -> float via values.parse_fps), duration (float), sampleRate/bitDepth from <audioMix> (defaults 48000/24; bitDepth enum 16|24|32 parsed as int), timeline {projectDuration = document duration, start = track_start(asset)} where track_start = audioTrack.start - audioTrack.clipIn of the FIRST <audioTrack asset=ID> in <audioMix> in document order (None when no track plays it), base_dir = document folder. Attribute reading is raw (lxml attributes, with Rust's defaults), NOT through the strict XSD loader, so documents carrying 1.2/1.3 attributes or placeholder digests can be resolved before BOB-PHASE1 lands; an include/repeat-expanded document is NOT resolved (Rust resolves the loaded model of the file - check expansion behaviour of sr_model::load_str: generated inside <include> targets are not in scope for 1.x; record whichever Rust does with a probe scene). Cache path errors become per-target error rows (not aborts). Generated metadata attributes (provider, model, prompt, voice, language, seed, license, fps, duration) are carried verbatim; license is not part of the request/key (Rust ignores it) - assert that. Render side stays as is (RESOLVE-30 PARTIAL gap = nothing consumed the attributes).

* Python files: scenerender/resolve/targets.py (new)
* Tests: tests/test_resolve_targets.py (new)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:299-315 track_start/Target; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:477-484 audio_format; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:526-581 generated phase; /home/admin/src/rs-scene-render/schema/scene-render-1.1.xsd:1100-1123 generatedAssetType
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/narrated.scene.xml`: track_start: vo plays at start=2 with no clipIn; add variant 'clip-in' with audioTrack start=5 clipIn=1.5 -> timeline.start 3.5; two tracks use the asset -> first wins. Expect: Request JSON in the Python sidecar equals the Rust sidecar request (all fields except output/workdir/baseDir) for vo, pic, and the audioforge-style 'music' asset with duration=auto (timeline start/projectDuration).
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::images_and_errors / speech_and_its_captions_resolve_and_pin (sidecar request fields)`: request contents recorded in the sidecar for generated assets
  * non-pixel: Request-diff table over every <generated> in tests/fixtures/assets_demo.xml, assets_full.xml and Rust tests/corpus/valid/*.scene.xml containing <generated>: Python Request JSON (with key) vs Rust sidecar request from a Rust resolve run with a stub provider; zero diffs.
* Risks: Fps as f64: Rust Fps::as_f64 for 30000/1001 and Python Fraction -> float must give the identical double. Confirm what Rust does with <generated> nested in <symbol>/<include> before assuming top-level only.

#### P4-RESOLVE-07: Target settlement: sidecar, up-to-date decision, result store, --force/--only/--no-store/--check

Rows: RESOLVE-08, RESOLVE-09, RESOLVE-10 · size M · track P4-R2 · group R2 · schema adoption: no · depends on: P4-RESOLVE-01, P4-RESOLVE-03, P4-RESOLVE-04, P4-RESOLVE-05, P4-RESOLVE-06

scenerender/resolve/store.py + engine.py::settle(target, options, workdir) -> (Resolution, pin|None), a port of lib.rs:346-475. Sidecar '<cache>.resolve.json' = {key, sha256, provider, version?, request} (indent=2, key order key, sha256, provider, version, request; version omitted when None). Decision order: (1) unless force: sidecar exists AND cache is a file AND sidecar.key == key AND sha256(cache) == sidecar.sha256 AND validate_content ok -> if cacheSha256 equals it (case-insensitive) status up-to-date, else check -> stale('cacheSha256 differs from the cache') else pinned (pin = sidecar sha). (2) check mode: stale with message 'the cache was made from a different request' (cache file exists) or 'the cache is missing', nothing written. (3) create the cache directory; store lookup (unless force): entry '<store>/<key>.<ext>' + sidecar, key equal, sha equal, content valid -> copy_atomic to the cache, status restored. (4) else provider: find(name) (error row on failure); cloud providers without --allow-cloud -> error 'provider N sends the prompt to a cloud service; run with --allow-cloud to allow it' (checked AFTER the store, as Rust does - see rust_may_be_wrong); scratch dir; request with output=<scratch>/result.<ext>, workdir=scratch; invoke; 'N reported success but wrote nothing'; validate_content -> 'N produced invalid KIND: why'; copy_atomic to cache; status made, notes from the answer. (5) write the sidecar atomically (request includes the real output path as Rust does), write the store entry + sidecar only for made results (errors ignored), set sha256, pin when the document digest differs. Store dir: Options.store ('' => none) else SR_RESOLVE_STORE (empty => disabled) else $XDG_CACHE_HOME or ~/.cache + '/scene-render/resolve'. Options{check, force, only, allow_cloud, store}; --no-store maps to store=''. Status enum up-to-date|pinned|restored|made|stale|error; Resolution{id, element, provider, cache, status, sha256?, message?, notes?}. Result: a failed forced regeneration never touches the existing cache, sidecar or pin (atomic publish only after validation).

* Python files: scenerender/resolve/store.py (new); scenerender/resolve/engine.py (new; settle + Options + Resolution)
* Tests: tests/test_resolve_engine.py (new)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:44-113 (Options, Status, Resolution, Sidecar); /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:142-161 store_dir/store_entry; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:346-475 settle; /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:154-293 (changes_remake..., the_store_restores..., test_store_invalid_entries_regenerate_without_pinning_corruption, the_store_keeps_output_formats_distinct); /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:717-785
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/narrated.scene.xml`: Sequence on one project: resolve (made, made) -> resolve (up-to-date) -> edit prompt -> --check (vo stale, subs up-to-date, file unchanged) -> resolve (made, made) -> hand-edit digest to ZERO -> resolve (pinned) -> rm -r gen -> resolve (restored, restored) -> --force (made) -> --force --only subs. Expect: Statuses, example-call counts (2,2,2,4,4,4,4,6,7), restored bytes and sha256 identical between engines at every step (harness case 'lifecycle').
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::changes_remake_what_they_affect_and_check_changes_nothing`: check reports stale/up-to-date without writing; remake cascades to captions; digest repin makes no provider call
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::the_store_restores_results_without_the_provider`: store restore after rm -r gen; --force remakes; --only limits rows; unknown --only is an error
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::test_store_invalid_entries_regenerate_without_pinning_corruption`: a corrupt or mismatched store entry is a miss, provider is called, corruption never pinned
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::the_store_keeps_output_formats_distinct`: same prompt, .wav vs .mp3 caches use different keys/store paths
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::invalid_provider_content_never_replaces_a_valid_cache / matching_digest_does_not_make_invalid_cache_content_current`: failed forced regeneration preserves doc/cache/sidecar bytes; digest-valid but invalid cache is stale
  * non-pixel: State-machine test: random sequence of {edit prompt, edit digest, delete cache, delete sidecar, delete store, corrupt cache, --force, --check} over narrated; after each step the Python and Rust (run on a parallel copy) status vectors and call counts agree.
* Risks: Sidecar JSON formatting (pretty, key order) need not match byte for byte for gating but comparing parsed JSON is required; keep key order anyway so a repo can mix engines. Python float repr in the sidecar request (e.g. 3.5) must round-trip into the Rust reader (serde accepts any JSON number).

#### P4-RESOLVE-08: resolve() orchestration: phases, in-place pinning, change detection, --only validation, outcome rows

Rows: RESOLVE-12, RESOLVE-31 · size M · track P4-R2 · group R2 · schema adoption: no · depends on: P4-RESOLVE-02, P4-RESOLVE-07

scenerender/resolve/engine.py::resolve(path, options) -> list[Resolution], a port of lib.rs:497-765: take the DocumentLock (not for check); read the UTF-8 text and remember the original; phase 1 generated assets (targets from P4-RESOLVE-06, preflight_destinations first - its failure aborts the run with exit 2 and writes nothing), each settled in document order with a fresh Scratch, pin via set_attr('generated') as it settles; phase 2 tiles (hook: calls P4-RESOLVE-17 when present; otherwise rows only for tiles targets that need provider, see deferred note) after RELOADING the text-derived model; phase 3 captionTracks with transcribe= (P4-RESOLVE-10) after reloading again so a transcriber hears speech made in this run and sees the freshly pinned digests. 'unselected' targets are skipped by --only but their paths stay reserved. End: when not check, re-read the file; if it differs from the original raise '<path> changed during resolution; generated caches were kept, but the document was not overwritten'; if text changed, write_atomic. Finally every --only id that produced no row -> error '<id>: no generated asset or transcribed caption track has this id'. Per-target failures are rows (status error, message) and never abort other targets; `error_row` helper for cache/transcribe-without-cache problems ('transcribe needs @cache', 'tiles with @url need @cache'). Resolution rows are JSON-serialisable exactly as Rust: keys id, element (generated|captionTrack|tiles), provider, cache, status (kebab-case), sha256 (omitted when none), message (omitted when empty), notes (omitted when empty); pretty-printed with 2-space indent by the CLI.

* Python files: scenerender/resolve/engine.py (touch; shared with P4-RESOLVE-07 - this package owns resolve() and error_row, 07 owns settle())
* Tests: tests/test_resolve_engine.py (extend)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:497-765; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:871-882 error_row; /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:58-82 test_resolve_preserves_edits_made_during_generation; /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:84-121; /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:205-226
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/narrated.scene.xml`: Full pin flow with a provider script ('editprobe') that appends '<!-- concurrent edit -->' to the document while generating; and the normal flow with the example provider. Expect: Editprobe: both engines return an error containing 'changed', document bytes equal the concurrently edited text, caches kept. Normal flow: document differs from the original only in cacheSha256 values; transcript word 0 start in [1.989,2.011], end in [2.889,2.911].
  * scene `tools/parity/scenes/resolve/images-errors.scene.xml`: image made (32 px wide asset), cloud speech error row, unknown provider error row, plus --only nope. Expect: Row set identical to Rust; --only nope -> exit 2 with the 'no generated asset or transcribed caption track has this id' message.
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::test_resolve_preserves_edits_made_during_generation`: an edit made to the document while a provider runs makes resolve fail and leaves the user's text intact
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::speech_and_its_captions_resolve_and_pin`: two targets made, validated document afterwards, only digests changed, transcriber hears vo at 2.0-2.9 s, sidecar provider/version, second run all up-to-date with no new calls
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::images_and_errors`: per-target rows: made image of requested size, cloud refusal names --allow-cloud, unknown provider names the scene-render-provider-<name> program
  * non-pixel: Row-schema check: JSON rows validate against a small JSON schema derived from the Rust Resolution serde output (golden rows from the Rust run) and equal them field for field.
* Risks: engine.py is touched by 07 and 08 - sequence them or let one agent do both; 08 needs the transcription hook to exist as a no-op before 10 lands. Rust loads via sr_model::load_str (which validates) so a document that fails validation aborts resolve with the diagnostics: Python raw-parse cannot reproduce that until BOB-PHASE1 - see risk in P4-RESOLVE-09.

#### P4-RESOLVE-09: `scenerender resolve` CLI and exit contract

Rows: RESOLVE-05 · size S · track P4-R2 · group R2 · schema adoption: no · depends on: P4-RESOLVE-08

Add the subcommand `resolve FILE [--check] [--force] [--only ID]... [--allow-cloud] [--no-store] [--json]` (scenerender/resolve/cli.py with a 6-line registration in scenerender/cli.py). Output as main.rs:659-705: per row `{label:>10}  ID (ELEMENT, PROVIDER)  CACHE  <first 12 hex>...` then indented `message` and `note: N` lines, labels 'up to date','pinned','restored','made','stale','error' (colour only on a tty, NO_COLOR honoured); empty result prints 'nothing to resolve: no <generated> assets or transcribed caption tracks'; --json prints the rows pretty-printed (indent 2) on stdout. Exit codes: 0 ok; 1 when any row is error or (with --check) stale; 2 when resolve() itself fails ('error: MSG' on stderr: unreadable file, lock held, preflight conflict, document changed, unknown --only id, invalid document). Before BOB-PHASE1: a document that does not load is reported as exit 2 using the existing document.load diagnostics text; after it: the same diagnostics as `validate`. Also `python -m scenerender resolve`; update docs/parity/CLI.md row 2.9/line 20 from missing-in-Python to PRESENT once gates pass. Environment variables honoured: SR_RESOLVE_STORE, SR_RESOLVE_ROOT, SR_PROVIDER_*, SR_PROVIDER_TIMEOUT.

* Python files: scenerender/resolve/cli.py (new); scenerender/cli.py (touch: register subparser; SHARED hub with the CLI area); docs/parity/CLI.md (touch)
* Tests: tests/test_resolve_cli.py (new); tests/test_output_delivery.py (touch only if it pins the subcommand list)
* Rust reference: /home/admin/src/rs-scene-render/crates/scene-render/src/main.rs:354-378 (clap args), 659-705 (resolve fn), 1668-1671 (dispatch); /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:61-95 (Status/Resolution)
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/narrated.scene.xml`: All harness cases through the real CLIs: `resolve`, `resolve --json`, `--check` on stale (exit 1) and fresh (exit 0), `--only vo`, `--only nope` (exit 2), missing file (exit 2), `--no-store`, `--force`. Expect: Exit codes equal; --json parses and rows equal Rust's (sha256 equal); human output equal modulo ANSI codes and path text.
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/crates/scene-render/src/main.rs::resolve() output/exit mapping (no dedicated unit test; exercised by cli integration tests in crates/scene-render/tests)`: exit 1 for error or stale-under-check, exit 2 for resolve failure
  * non-pixel: `scenerender resolve --help` lists the same options as `scene-render resolve --help` (names compared).
* Risks: Strict loading: Rust refuses documents that fail validation; Python stays on raw parse until BOB-PHASE1 vendors the 1.1.3+1.2+1.3 XSD, so version="1.2" docs (all the Rust test fixtures) must be accepted by resolve before that - do not call document.load(strict=True) in this package.

#### P4-RESOLVE-10: captionTrack transcription: provider/model/prompt attributes, mono mix input, transcribe requests

Rows: RESOLVE-20, RESOLVE-13 · size M · track P4-R4 · group R4 · schema adoption: yes · depends on: BOB-PHASE1, P4-RESOLVE-05, P4-RESOLVE-08

(1) Schema surface (gated by BOB-PHASE1): captionTrack attributes provider (default whisper), model (default base), prompt are read by scenerender (captions.py accepts them, no render effect), validated by the vendored XSD, listed in coverage. (2) scenerender/resolve/transcribe.py: for every <captionTrack transcribe=ID cache=...> in <captions> (composition tracks) in document order (honouring --only): render the scene's audio with Mixer.signal(ID) semantic = the audioTrack's post-fader, per-track-effects signal in COMPOSITION time from t=0 at the mix sample rate (placement, trims, speed, loops, volume, effects, node signal), downmix to mono by channel average (Rust mono(): sum of channels / channel count, longer channel padded with 0), write a 24-bit mono WAV (audio.io writer, rate = mix rate) into the scratch dir, request.input = path, request.inputSha256 = sha256 of that file; Request{task=transcribe, kind=captions, provider (default whisper), model (default base), prompt, language = captionTrack @language string (always sent, default per XSD), sampleRate/bitDepth from audioMix, timeline{projectDuration = project duration, start=None}, base_dir}. Errors as rows: 'transcribe needs @cache', 'audio track "ID" is not in the mix', 'the scene has no audio to transcribe', 'mixing the scene's audio: ...'. The phase runs after generated assets are pinned and reloads the document text so same-run speech is heard at its track start (words at 2.0 s for start=2). Deterministic-mix note: the transcription key includes inputSha256, so keys match Rust only if the mix is bit-identical; the harness therefore compares the INPUT numerically (SR_EXAMPLE_KEEP_INPUT) rather than the key.

* Python files: scenerender/resolve/transcribe.py (new); scenerender/captions.py (touch: read provider/model/prompt without effect; SHARED with the TEXT area); scenerender/schema/* (BOB-PHASE1 owned; this package only consumes); scenerender/coverage.py (touch: list the attributes)
* Tests: tests/test_resolve_transcribe.py (new); tests/test_captions_formats.py (touch: captionTrack with provider/model/prompt still renders)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:645-746 (phase 3); /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:884-900 mono/mix; /home/admin/src/rs-scene-render/crates/sr-model captionTrack provider/model/prompt (schema/scene-render-1.1.xsd:3585-3600); /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:84-121; /home/admin/src/rs-scene-render/crates/sr-deliver/src/captions.rs (cache_sha256 handling)
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/narrated.scene.xml`: captionTrack subs transcribes track voice (vo plays at 2 s, 3 words x 0.3 s); also variant 'speed' with audioTrack speed=2 + volume 0.5 + a gain effect; variant 'stereo' with a 2-channel asset. Expect: SR_EXAMPLE_KEEP_INPUT dumps: Python mono WAV vs Rust mono WAV same duration (+-1 sample), same rate, RMS error < -90 dBFS (floating point mix tolerance), peak error < 1e-4; transcript words in each engine at the same times within 0.011 s (the example provider's 10 ms hop).
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::speech_and_its_captions_resolve_and_pin (transcript timing)`: transcriber hears the voice from composition time 2.0; word 0 spans 2.0-2.9
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::malformed_transcription_is_not_published`: a transcriber answering a transcript without 'end' yields an error row, nothing published
  * statistical: Input audio: not bit parity (float32 mix order differs). Gate = duration equal within 1 sample, RMS(py-rs) < -90 dBFS re full scale over the whole file, and for the example transcriber the word boundaries within 11 ms. Fixed seed policy: the Python Mixer is deterministic (document seed); noise/random effects excluded from the gate scenes.
  * non-pixel: Transcription-input unit test: constant-amplitude stereo track with unequal channels (0.4/0.2) -> mono sample 0.3; a track starting at 2 s has 96000 leading zero samples at 48 kHz.
* Risks: The mix must come from the SAME Mixer the renderer uses (scenerender.audio.mix) so what is transcribed is what is heard; Mixer needs a loaded Document (strict load may fail on version 1.2/1.3 attributes before BOB-PHASE1): use strict=False/lenient load for resolve until then. Mixer.signal(track id) returns post-fader per-track signal - verify it includes the track's effects and is composition-aligned (mix.py:966-989) before relying on it.

#### P4-RESOLVE-11: Output-owned captionTrack transcription in output time

Rows: RESOLVE-13 · size M · track P4-R4 · group R4 · schema adoption: yes · depends on: BOB-PHASE1, P4-RESOLVE-10 · external: MEDIA-13, MEDIA-17, MEDIA-18, TEXT-36

For captionTracks that are children of <output> (schema 1.3), transcribe the output's OWN audio tracks alone, post-fader, in OUTPUT time (no ducking beneath or keying from other tracks, sidechain effects dropped), with project duration for the request timeline = length of the first own-track buffer / rate (0 when none); error row 'output PATH: ...' when the output's own mix cannot be built. This reuses the output-time time map and own-track mixing of MEDIA-13/17/18; this package only adds the transcription driver: transcribe.py::own_tracks_input(doc, output) calling the MEDIA segment-audio API, plus the resolve phase wiring (tracks list = composition tracks then each output's children). Output-time cue mapping at render time is TEXT-36/MEDIA-19, not this package.

* Python files: scenerender/resolve/transcribe.py (touch; same file as P4-RESOLVE-10, sequence after it)
* Tests: tests/test_resolve_transcribe.py (extend)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:656-677 (output-owned tracks, own_mix); /home/admin/src/rs-scene-render/crates/sr-deliver/src/segment_audio.rs:301-329 own_tracks; /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:122-153 an_outputs_own_voice_is_transcribed_in_output_time
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/output-voice.scene.xml` (new): Mirror of tests/resolve.rs:122-153: output plays composition 3..6 s via a segment, owns an audioTrack voice from output time 1 s, and an output-level captionTrack transcribing it. Expect: Both engines: words start at output time 1.0 (not composition time 4.0); status made; sidecars' request.timeline.projectDuration equal within 1 sample/rate.
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::an_outputs_own_voice_is_transcribed_in_output_time`: transcribed word times are output seconds
  * statistical: Same audio statistic as P4-RESOLVE-10 (RMS error < -90 dBFS, +-1 sample length).
* Risks: Blocked on the output-segments work in the MEDIA area (Rust-only elements); if it slips this package is the first to defer. Hard-schema-gated (1.3).

#### P4-RESOLVE-12: Render-time strictness: refuse a bad generated cache or transcription cache, with Rust's diagnostics

Rows: RESOLVE-02, RESOLVE-03 · size S · track P4-R4 · group R4 · schema adoption: yes · depends on: BOB-PHASE1, P4-RESOLVE-10

Align the READ side with Rust A02/A05. (a) generated: keep generated_cache_path raising SceneError but with the Rust text: '<file> does not match @cacheSha256 of <generated>: expected <hex>, found <hex>' + help 'run `scene-render resolve` (scenerender resolve) to make the cache and pin its digest' and, for a missing/unreadable file, A05 'cannot read <file>: <reason>' (missing file also A01 per DOC-26); the check moves to document load time (diagnostics A02/A05 on the generated, captionTrack and tiles elements, error severity, attribute location) so `validate`, `render` and `check` all refuse before frame 0 - delivered through BOB-PHASE1's diagnostic API. (b) captionTrack with cache+cacheSha256: a mismatch is an error (A02), not log.error + skipped track (captions.py:1627-1645 currently continues rendering without captions); missing cache file with transcribe set = error; --lenient/--no-assets downgrade as Rust does for assets (A02/A05 are skipped with --no-assets). Python's superset of cache shapes (SRT/VTT/Deepgram/...) stays; digest check applies to all of them. Add the same refusal text on the render path so message wording matches (Rust tests compare 'expected'/'found' substrings).

* Python files: scenerender/assets/__init__.py (touch: generated_cache_path message; SHARED); scenerender/captions.py (touch: _cache_cues refusal; SHARED with TEXT); scenerender/document.py / validation module owned by BOB-PHASE1 (integration point only); tests/test_semantic_validation.py (touch), tests/test_assets.py (touch), tests/test_captions_formats.py (touch)
* Tests: tests/test_semantic_validation.py; tests/test_assets.py::test_generated_cache_verification; tests/test_captions_formats.py (348-356 cache digest cases)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-model/src/assets.rs:335-366 (A02 with help text, A05); /home/admin/src/rs-scene-render/crates/sr-deliver/src/captions.rs:86,396 (inline cache sha handling); /home/admin/src/rs-scene-render/crates/sr-text/src/captions.rs:470-520
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/bad-digest.scene.xml` (new): generated speech/image with a wrong cacheSha256, and a captionTrack with a tampered cache JSON. Expect: validate/render/still: Rust exit 1 with A02 (expected/found hexes, help text); Python exit 1/3 with same code A02 and message; no frame rendered. The tampered caption cache is an error, not an empty caption track.
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::speech_and_its_captions_resolve_and_pin (document validates only after pinning)`: placeholder digests fail validation; pinned ones pass
  * non-pixel: Diagnostic-code table: {missing generated cache, wrong digest, unreadable, tampered caption cache} each yields A01/A02/A05 with the same primary code as `scene-render validate --format json`.
* Risks: Behaviour change: today a bad caption cache is silently skipped; fixtures in tests/ that rely on that must be updated. Overlaps DOC-26/DOC-27/TEXT-28 owned by other areas: coordinate (this package contributes only the generated/captionTrack/tiles cases and wording).

#### P4-RESOLVE-13: whisper provider (whisper.cpp) with DTW word times

Rows: RESOLVE-21 · size M · track P4-R3 · group R3 · schema adoption: no · depends on: P4-RESOLVE-01, P4-RESOLVE-04, P4-RESOLVE-05

scenerender/resolve/providers/whisper.py. Locate whisper-cli via SR_WHISPER (which() then literal) else PATH; model_file(): the request.model as a path (absolute, or relative to baseDir) if it is a file, else 'ggml-<model>.bin' (strip 'ggml-' prefix and '.bin' suffix) searched in SR_WHISPER_MODELS (os.pathsep list) then <whisper.cpp tree>/models (exe canonical path ancestor #3); error 'whisper model ggml-X.bin not found (download it with whisper.cpp's models/download-ggml-model.sh and set SR_WHISPER_MODELS)'. run(): ffmpeg `-v error -nostdin -y -i INPUT -ac 1 -ar 16000 -c:a pcm_s16le whisper-16k.wav`; trim_leading_silence (first |sample| > 104 (-50 dBFS) index minus 3200 samples = keep 0.2 s; cut only when >= 1600 samples; rewrite canonical 16 kHz mono 16-bit WAV; returns seconds cut); language = primary subtag lower-cased of request.language, forced 'en' for models ending '.en', else 'auto'; command `whisper-cli -m MODEL -f WAV -ojf -of <work>/whisper -np -l LANG [--prompt P] [--dtw PRESET -nfa]` with dtw_preset(model) from {tiny,tiny.en,base,base.en,small,small.en,medium,medium.en,large.v1,large.v2,large.v3,large.v3.turbo} after stripping ggml-/.bin and '-'->'.'; read whisper.json; transcript(): join tokens into words (skip '[_...' / '<|...' / empty tokens; DTW time t_dtw/100 if >=0 else token offset; word continues when the token does not start with a space; word ends at the next word's start, last at segment end), segment start = first word start (>= segment start), segment dropped when text and words are empty; shift all times by the cut; write {segments:[{start,end,text,words[{start,end,text}]}]} pretty JSON; version 'whisper.cpp ggml-<model>.bin'; note '<model> has no DTW preset: word times are whisper's token timestamps'. model file participates in the request key (hook from P4-RESOLVE-01). Optional extra (not parity): faster-whisper backend behind provider name 'whisper' only when SR_WHISPER is unset and whisper-cli absent - NOT in scope (would change keys); keep it deferred.

* Python files: scenerender/resolve/providers/whisper.py (new)
* Tests: tests/test_resolve_whisper.py (new; fake whisper-cli script + fake ffmpeg shim via SR_FFMPEG-like env or monkeypatched convert)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/whisper.rs (dtw_preset 26-47, model_file 49-70, transcript 78-108, trim_leading_silence 113-127, shift, run 148-200); /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/whisper.rs:tokens_join_into_timed_words test (201-226); /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:516-556 piper_speech_captioned_by_whisper (only when installed)
* **Acceptance:** 
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/whisper.rs::tokens_join_into_timed_words`: token JSON -> words Hello,(1.1-1.8) world.(1.8-3.0), segment text, dtw_preset('large-v3')='large.v3', custom model None
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::piper_speech_captioned_by_whisper`: end-to-end piper->whisper (skipped unless both installed)
  * statistical: Real whisper.cpp output is not reproducible across builds: gate on fake-whisper determinism only (transcript conversion exact) and, when whisper-cli exists, word texts equal Rust's and word times within 50 ms.
  * non-pixel: Offline: a fake `whisper-cli` shell script (SR_WHISPER) that records argv and writes a canned JSON proves the exact argv ('-ojf','-np','-l en','--dtw base.en','-nfa','--prompt'), the 16 kHz trim and the time shift (a 3 s leading-silence WAV is cut to keep 0.2 s and times are shifted back by 2.8 s).
* Risks: whisper-cli, piper and ffmpeg are absent on the dev box: all gates use fakes; real-binary tests are skipif. DTW word times are whisper.cpp-version specific. FFmpeg dependency for the 16 kHz resample: locate with audio.io.ffmpeg_exe (imageio_ffmpeg) - check it actually resolves in this venv, else resample with numpy/soxr fallback marked as non-parity.

#### P4-RESOLVE-14: piper and audioforge providers

Rows: RESOLVE-22, RESOLVE-24 · size S · track P4-R3 · group R3 · schema adoption: no · depends on: P4-RESOLVE-01, P4-RESOLVE-04, P4-RESOLVE-05

providers/piper.py: kind must be speech ('piper makes speech, not K'); text = non-blank @prompt ('piper needs the text as @prompt'); exe via SR_PIPER else PATH ('piper not found (install piper-tts or set SR_PIPER)'); voice(): model as a path (abs or baseDir-relative) if file, else '<model>.onnx' in SR_PIPER_VOICES dirs, ~/.local/share/piper, ~/.local/share/piper-voices ('piper voice "M" not found (set SR_PIPER_VOICES to the folder of its .onnx file)'); write text to <work>/piper.txt; `piper -m VOICE -i piper.txt -f piper.wav [-s N when @voice parses as unsigned int]`; convert(piper.wav -> output); version 'piper <voice file name>'; the .onnx and .onnx.json (when present) are hashed into the request key (model_files hook). providers/audioforge.py: kinds music|sound-effect only; exe via SR_AUDIOFORGE else PATH; params(): shell_split(prompt) then append seed=N unless a seed= key exists and duration=D unless a duration= key exists (D from request.duration), then any param equal to 'duration=auto' (spaces removed) becomes duration=round(max(projectDuration - max(start,0), 0.05), 4); command `audioforge cue render MODEL PARAMS... -o <work>/audioforge.wav --sample-rate R --bit-depth (16 if bitDepth==16 else 24)`; convert; version = stdout of `audioforge --version` trimmed else 'audioforge'.

* Python files: scenerender/resolve/providers/piper.py (new); scenerender/resolve/providers/audioforge.py (new)
* Tests: tests/test_resolve_local_providers.py (new; fake piper/audioforge executables in tmp PATH)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/piper.rs (whole, 63 lines); /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/audioforge.rs (whole; test auto_duration_fills_the_project_after_the_track_starts 70-88); /home/admin/src/rs-scene-render/crates/sr-resolve/tests/local_models.rs; /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:557-581 audioforge_renders_a_cue
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/audioforge-cue.scene.xml` (new): generated music provider=audioforge model='bed/test' prompt='duration=auto bpm=96 key=\'A minor\'' seed=7, audioTrack start=4.5, project 30 s; SR_AUDIOFORGE points at tools/parity/fake_audioforge.py (writes a WAV, logs argv). Expect: Both engines invoke the fake with the same argv: cue render bed/test duration=25.5 bpm=96 'key=A minor' seed=7 -o <wav> --sample-rate 48000 --bit-depth 24; same rows/sha256.
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/audioforge.rs::auto_duration_fills_the_project_after_the_track_starts`: params -> ['duration=25.5','bpm=96','key=A minor','seed=7']
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::audioforge_renders_a_cue`: fake audioforge: argv, version note, validated WAV
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/local_models.rs::piper voice files change the request key`: two voices with equal relative names but different bytes -> different keys
  * non-pixel: argv-equality check against Rust using the same fake executables (they record argv to a file; harness diffs).
* Risks: Both tools are external and absent: only fakes are testable. Needs convert() with ffmpeg for non-wav cache extensions (fake the converter).

#### P4-RESOLVE-15: Cloud providers: curl transport, credentials, openai and elevenlabs (mock server only)

Rows: RESOLVE-25, RESOLVE-26, RESOLVE-27 · size M · track P4-R3 · group R3 · schema adoption: no · depends on: P4-RESOLVE-04, P4-RESOLVE-05, P4-RESOLVE-07

providers/cloud.py + curl in process.py. Cloud gate lives in settle (P4-RESOLVE-07) via Provider.cloud(); this package supplies: curl(config): exe from SR_CURL else 'curl', args ['--disable','--max-filesize','268435456','--config','-'] ('--disable' MUST be first), config text on STDIN (never argv), run under the shared deadline, non-zero exit -> 'curl failed (STATUS)' WITHOUT stderr, stdout parsed as the HTTP status; curl_quote(s) (backslash, quote, \n, \r, \t escaped, wrapped in double quotes). post(url, headers, json_body, work, out): config = url, request POST, silent, show-error, Content-Type + extra headers, data-binary @request.json, output, write-out %{http_code}. failure(service, status) -> '<service> answered HTTP N' (response body never read or echoed). openai: key OPENAI_API_KEY ('OPENAI_API_KEY is not set'), base OPENAI_BASE_URL (default https://api.openai.com/v1, trailing / stripped), Authorization Bearer; non-blank @prompt; speech -> POST /audio/speech {model,input,voice (default alloy),response_format 'wav'} expecting 200, rename body to wav, convert; image -> POST /images/generations {model,prompt,n:1,size 'WxH' from width+height else '1024x1024', response_format 'b64_json' only for models starting 'dall-e'}, parse data[0].b64_json ('OpenAI answer has no data[0].b64_json'), base64-decode (standard and urlsafe alphabets, whitespace ignored), PNG -> convert; other kinds error 'openai makes speech and images, not K'; version 'openai <model>'. elevenlabs: speech only; key ELEVENLABS_API_KEY, base ELEVENLABS_BASE_URL (default https://api.elevenlabs.io/v1); needs @voice ('elevenlabs needs @voice (a voice id)') and prompt; POST {base}/text-to-speech/<voice>?output_format=pcm_44100 header xi-api-key, body {text, model_id, seed (seed mod 2^32 when set), language_code (primary subtag of @language when set)}; raw PCM16 mono -> 44100 Hz WAV (wav_from_pcm16) -> convert; version 'elevenlabs <model>'. cloud() is true for both; tiles also cloud. NO real network calls in any gate: only a local http.server fixture and a fake curl script.

* Python files: scenerender/resolve/providers/cloud.py (new); scenerender/resolve/providers/process.py (touch: curl, curl_quote; owned by P4-RESOLVE-05 - add via small follow-up commit after 05)
* Tests: tests/test_resolve_cloud.py (new; local http.server on 127.0.0.1:0 as the fake service; fake SR_CURL script)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/cloud.rs (whole; tests 138-199); /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs:215-237 curl/curl_quote; /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:405-415 (gate); /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:402-515 (serve() fixture, openai_speech_and_images_through_a_local_server)
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/cloud-openai.scene.xml` (new): generated speech (openai tts-1 voice alloy) and image (gpt/dall-e-2 256x256) pointed at a 127.0.0.1 mock via OPENAI_BASE_URL; OPENAI_API_KEY=dummy-key; --allow-cloud. The same mock serves both engines, which record the received method, path, headers and JSON body. Expect: Both engines produce identical requests at the server (path /audio/speech and /images/generations, Authorization Bearer dummy-key, body fields exactly as Rust, response_format absent for gpt-image-1 and present for dall-e) and identical rows/sha256; without --allow-cloud both give an error naming --allow-cloud and the server sees 0 requests.
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/cloud.rs::remote_errors_do_not_echo_credentials_or_unbounded_bodies`: error text for HTTP 400 never contains the secret in the body and is < 1 KiB
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/cloud.rs::curl_disables_user_config_and_keeps_error_stream_private`: fake SR_CURL: args start with --disable, stderr (which echoes config) never leaks into the error
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::openai_speech_and_images_through_a_local_server`: mock server sees bearer key, paths, bodies; speech and PNG results land in the cache
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs::base64_decodes`: RFC 4648 decoding, whitespace ignored, invalid input rejected
  * non-pixel: Secret hygiene scan: run a resolve where the mock returns an error body containing the API key; assert key never appears on stdout/stderr/JSON rows/sidecar of the Python run (grep over scratch).
* Risks: Python could use urllib, but SR_CURL (fake curl in tests), '--disable', key-in-config-on-stdin are observable security properties of the Rust design: keep curl. curl must exist on PATH (it does here: /usr/bin/curl). Never contact api.openai.com/api.elevenlabs.io in tests or harness.

#### P4-RESOLVE-16: tiles provider: fetch an explicit tile list and write a PMTiles cache

Rows: RESOLVE-28 · size M · track P4-R5 · group R5 · schema adoption: yes · depends on: BOB-PHASE1, P4-MAPS-14, P4-RESOLVE-07, P4-RESOLVE-15

providers/tiles.py (provider 'tiles', cloud=True) + scenerender/resolve/pmtiles_write.py. Request: model = URL template ({z},{x},{y},{s}; {s} = a|b|c by (x+y)%3), prompt = space-separated 'z/x/y' list (unparsable tokens dropped). run(): SR_TILES_MAX (default 2000; more -> 'the maps need more tiles than SR_TILES_MAX (N); lower the zoom or the length of the moves, or use a downloaded PMTiles archive'); GET each tile with curl config (silent, show-error, location, max-time 60, retry 2, User-Agent 'scene-render/<ver> (+https://github.com/pedroanisio/rs-scene-render)' - use scenerender's own name/version but keep the shape, header, output, write-out http_code) through P4-RESOLVE-15's curl; 200 = keep bytes, 404/204 = missing, other = error 'tile Z/X/Y: HTTP N'; tile type sniffed from the first tile (PNG, JPEG, WEBP, gzip -> MVT compressed, else MVT) ; write a PMTiles v3 archive (header, root directory (Hilbert tile ids, delta/varint-encoded, gzip internal compression as Rust's sr_geo::pmtiles::write does), metadata JSON {source: template}) to request.output; notes ['N tiles fetched', 'M tiles missing on the service']; version 'tiles 1'. The writer must produce an archive our own MAPS-21 reader (and sr-geo's reader) opens; byte parity with sr_geo::pmtiles::write is a goal (read /home/admin/src/rs-scene-render/crates/sr-geo/src/pmtiles.rs first) but equal-decoded-content is the gate. Cache request kind 'tiles', provider 'tiles'.

* Python files: scenerender/resolve/providers/tiles.py (new); scenerender/resolve/pmtiles_write.py (new)
* Tests: tests/test_resolve_tiles.py (new; local http.server serving 6 tiles with a 404)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/tiles.rs (whole, 127 lines); /home/admin/src/rs-scene-render/crates/sr-geo/src/pmtiles.rs (write); /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:582-657 online_tiles_are_fetched_for_the_views_and_pinned
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/tiles-local.scene.xml` (new): tiles asset with url pointing to the 127.0.0.1 mock ({z}/{x}/{y}.png), a fixed prompt-tile-list injected through a harness-only 'tilesList' mode (resolve of a scene whose map views are static, depends on P4-RESOLVE-17 for the real enumeration); smoke variant lists tiles directly via SR_PROVIDER_TILES wrapper. Expect: Both archives open in the Rust sr-geo reader and the Python MAPS-21 reader; tile set and bytes per tile identical; 404 tile absent; row notes equal; mock server sees the User-Agent header and the same GET paths.
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/tiles.rs::(no unit test) tile_url / max_tiles / over_budget`: {s} rotation, budget refusal text
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::online_tiles_are_fetched_for_the_views_and_pinned`: tiles fetched for the views, archive valid, digest pinned, SR_TILES_MAX refusal
  * non-pixel: Archive round-trip: write N random tiles (png-like blobs) -> read back with the MAPS-21 reader -> every (z,x,y) returns identical bytes; header fields (tile type, compression, min/max zoom, tile count) equal the Rust-written archive of the same input.
* Risks: PMTiles v3 writer is new code (directory encoding, Hilbert curve ids, leaf split thresholds) and the Rust reader is the oracle - read crates/sr-geo/src/pmtiles.rs for the exact directory layout before coding. Blocked on MAPS-21 for the reader used in the gate; the schema attribute set for <tiles> arrives with BOB-PHASE1/MAPS-01.

#### P4-RESOLVE-17: tiles: enumerate every tile the maps, terrain and globe views need, frame by frame

Rows: RESOLVE-28 · size M · track P4-R5 · group R5 · schema adoption: yes · depends on: BOB-PHASE1, P4-MAPS-14, P4-MAPS-15, P4-MAPS-21, P4-MAPS-22, P4-MAPS-23, P4-MAPS-24, P4-MAPS-25, P4-RESOLVE-16

scenerender/resolve/tileset.py::tile_set(doc, tiles_asset) -> sorted set of (z,x,y) and the resolve phase-2 wiring (settle with kind 'tiles', pinned = tiles@cacheSha256, 'tiles with @url need @cache' error row, missing/invalid minZoom>maxZoom error 'tiles ID: minZoom Z is above maxZoom Z', pin via set_attr('tiles')). Algorithm (lib.rs:769-869): tileSize default 256 for raster URLs (.png/.jpg/.jpeg/.webp before '?') else 512; zoom range clamped to 0..24; for each <map> whose basemap child references this tiles id: evaluate the map camera/view at every frame 0..ceil(duration*fps) at the project fps (animated view properties via the Python evaluator), projection -> basemap_zoom(projection, tileSize, detail, raster) clamped to [minZoom,maxZoom], visible tiles via the quadtree walk (MAPS-22 visible_within(proj, z, cap)); 3D maps: object3D primitive=globe -> world equirectangular 2H x H drape at its basemap zoom; object3D terrain=<tiles id> -> DEM zoom round(map_zoom + 1 - log2(max(w,h)/resolution)) clamped, over each frame's view; budget SR_TILES_MAX enforced WHILE listing (refuse before walking further, same message as P4-RESOLVE-16). request.prompt = ' '.join('z/x/y'). A document with no map using the tiles asset yields an empty set (provider still runs, archive empty).

* Python files: scenerender/resolve/tileset.py (new); scenerender/resolve/engine.py (touch: phase 2 call; owned by P4-RESOLVE-08)
* Tests: tests/test_resolve_tileset.py (new)
* Rust reference: /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:583-643 (phase 2), 767-869 tile_set; /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/tiles.rs:25-40 (budget); /home/admin/src/rs-scene-render/crates/sr-eval/src/geo.rs (basemap_zoom, camera, view); /home/admin/src/rs-scene-render/crates/sr-geo/src/tiles.rs (visible_within, map_zoom); /home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs:402-425 a_view_over_the_tile_budget_is_refused_before_its_tiles_are_listed, 582-657
* **Acceptance:** 
  * scene `tools/parity/scenes/resolve/tiles-views.scene.xml` (new): A 1280x720 map with an animated zoom/pan (flyTo) over a basemap tiles asset, a second static map, and a 3D map ground with terrain tiles; url mock + SR_TILES_MAX=50 variant. Expect: The (z,x,y) set listed in the request.prompt of the Python sidecar EQUALS the Rust sidecar's prompt (sets compared as sets; exact equality), including after enabling terrain and globe; with SR_TILES_MAX below the need both refuse with the same message and the mock sees 0 requests.
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::a_view_over_the_tile_budget_is_refused_before_its_tiles_are_listed`: budget refusal happens during enumeration
  * port `/home/admin/src/rs-scene-render/crates/sr-resolve/tests/resolve.rs::online_tiles_are_fetched_for_the_views_and_pinned`: fetched tile set equals the views' needs; pinned digest validates
  * non-pixel: Set equality of tile lists vs Rust on 5 scenes (static map, fly-to, rotation, globe, terrain); differences reported as symmetric difference sizes (target 0).
* Risks: Entirely dependent on the MAPS area (projection, camera/view evaluation, quadtree visibility). Tile sets are exact integer sets, so any rounding difference in basemap_zoom/visible_within produces a different request key and different cache bytes: expect iteration. Largest schedule risk of the area.

### SIM2

#### P4-SIM2-00: Statistical gate harness for stochastic/simulated output (sim_gate.py) and noise-floor calibration

Rows:  · size S · track P4-S1 · group A · schema adoption: no · depends on: none

tools/parity/sim_gate.py: a gate runner that (1) renders a scene with the Rust binary and with Python at given times (reusing render_parity.py's runner), (2) computes the metric set below, (3) compares against tolerances stored in tools/parity/scenes/sim2/gates.json, (4) writes results.json + SUMMARY.md with PASS / FAIL / INFO per metric. Metrics: (a) PSNR classes as render_parity (exact-mode gate); (b) global mean luminance and per-colour-channel mean; (c) 16-bin luminance histogram total-variation distance; (d) coverage fraction of non-background pixels; (e) delta-image statistics for on/off feature pairs (Pearson r of (on-off) images, total darkened mass ratio); (f) silhouette IoU of an alpha/colour-threshold mask (for 3D objects whose shading differs between rasterisers); (g) region means on a coarse grid (8x4) with relative tolerance; (h) convergence study: Python at N spp against a Rust reference at 4096 spp. Tolerances are NOT hand-picked: a calibration mode renders the Rust binary K=6 times with seeds 1..6 (sims) or spp 128/256/384/512 against 4096 (tracer) and stores mean and 3-sigma noise floors in gates.json together with the Rust version/adapter. Python passes a statistical metric when it is inside max(3 sigma floor, 1.5 x worst Rust seed-to-seed value). Calibration measured on Intel Arc/Vulkan at 640x360, t=2.0 (this plan): flocking mean luminance 0.1976 +/- 0.0019, histogram TV mean/max 0.0048/0.0117; slime 0.1252 +/- 0.0044, hist TV 0.0102/0.0199; erosion 0.5555 +/- 0.0367 (hist TV is large, 0.24/0.43, so use same-seed gates only); path-tracing at t=0: 128 spp denoised PSNR 47.4 dB vs 4096 spp, 128 spp NOT denoised 36.9 dB (rmse 0.0143), mean luminance 0.5404 vs 0.5405. Also records the Rust determinism fact set (below, area_notes) so a regression in Rust itself is caught: the harness renders every Rust scene twice and asserts identical bytes before using it as an oracle.

* Python files: tools/parity/sim_gate.py (new); tools/parity/scenes/sim2/gates.json (new, calibrated tolerances + Rust adapter/version); tools/parity/render_parity.py (read-only reuse; add --seed-sweep and --repeat flags only if needed); tools/parity/scenes/sim2/*.scene.xml (new, see per-package lists)
* Tests: tests/test_sim_gate_harness.py (new; unit-tests the metrics on synthetic images: identical -> inf dB, shifted histogram -> TV, mask IoU; skips Rust-dependent parts unless SR_RS_BIN is set)
* Rust reference: tools/evidence/cases.json (case ids fluid, flocking, erosion-slime, erosion, clay-3d, path-tracing and their checks types coverage/changes/not_color); tools/parity/evidence_gate.py (existing evidence runner whose result classes the harness extends)
* **Acceptance:** 
  * scene `/home/admin/src/rs-scene-render/tools/evidence/scenes/flocking.scene.xml`: calibration subject: seeds 1..6 with the scene's own parameters Expect: noise floors recorded
  * scene `/home/admin/src/rs-scene-render/tools/evidence/scenes/path-tracing.scene.xml`: calibration subject: spp 128..512 vs 4096 Expect: noise floors recorded
  * statistical: This package IS the statistical policy: tolerances come from measured Rust-vs-Rust noise (see goal), seed policy = identical seed attribute on both sides for exact-mode gates (the sims are counter-based deterministic, so same seed means same stream), different seeds only to calibrate; sample policy for the tracer = identical pathSamples on both sides for sample-exact gates (same PCG stream), 4096 spp both sides for converged gates.
  * non-pixel: Self-test: Rust vs Rust (two runs of the same scene, bytes identical -> PASS) and Rust(seed1) vs Rust(seed2) -> metrics land inside their own calibrated tolerance.
* Risks: Calibration depends on the adapter. Store the adapter name; gates.json carries a tolerance multiplier per adapter class (GPU vs llvmpipe) because Rust itself differs 67-87 dB between them. Do not lower tolerances below the Rust cross-adapter floor.

#### P4-SIM2-01: Sim foundation: counter RNG, FNV/mix64 seeding, value/curl noise, float-time seekable Timeline with checkpoint thinning, JIT policy

Rows: SIM2-01, SIM2-02, SIM2-04 · size M · track P4-S1 · group A · schema adoption: no · depends on: none

New package scenerender/sim/ with: (1) rng.py: mix64, hash(seed,a,b) = splitmix finaliser of seed ^ a*0x9E3779B97F4A7C15 ^ b*0xC2B2AE3D27D4EB4F (u64 wrapping, vectorised numpy uint64 and scalar forms), unit() = (hash>>11)/2^53, signed(), noise2 (smoothstep 3-2t value noise on the (x,y) lattice, lattice = signed(seed, x as u64, y as u64) with i64->u64 wrap), curl2, hash_str = FNV-1a 64 over UTF-8 bytes then mix64 (used for seed default = hash_str(node id); Rust seed attr: numeric `v as u64`, saturating). These are DISTINCT from physics.hash01/vnoise (D24) and must not replace them. (2) timeline.py Timeline(start, dt, state0, bytes_fn): at(t, step) with target = 0 if t <= start else floor((t-start)/dt + 1e-9); checkpoints every `every` = round(1/dt) steps; restore the nearest earlier checkpoint when seeking back or when target - step > every; step callback receives (state, k, t0 = start + k*dt); BUDGET = 256 MiB thinning: after each insert, while len(cp) > 2 and len(cp)*bytes > BUDGET: every *= 2 and keep only keys divisible by every. Built on, not replacing, physics.Seekable (Seekable stays integer-step for physics/particles); a thin adapter may share the deepcopy-checkpoint machinery. (3) jit.py: optional numba (declared in pyproject but absent from the dev venv: see area_notes) behind `njit_or_python(fn)`; the pure-python path must stay bit-identical to the jitted path (no fastmath, no fma contraction) and is only used for tests/small sizes. (4) hash_str and rng golden vectors committed as constants derived from the Rust formulas.

* Python files: scenerender/sim/__init__.py (new); scenerender/sim/rng.py (new); scenerender/sim/timeline.py (new); scenerender/sim/jit.py (new); pyproject.toml (documentation of numba as a hard runtime dependency for scenerender.sim; owner = BOB/packaging, shared)
* Tests: tests/test_sim_rng.py (new); tests/test_sim_timeline.py (new)
* Rust reference: crates/sr-sim/src/rng.rs:1-44 hash/unit/signed/noise2/curl2; crates/sr-eval/src/rng.rs:7-40 mix64, hash_str (FNV-1a + mix64); crates/sr-sim/src/timeline.rs:1-106 Timeline::new/at/thin, BUDGET (L12), test seeking_replays_the_same_steps_and_thinning_keeps_the_budget (L80-105); crates/sr-eval/src/agents.rs:25 STEP = 1/60, :287-335 Timeline::new(0.0, STEP, init)
* **Acceptance:** 
  * port `crates/sr-sim/src/timeline.rs::seeking_replays_the_same_steps_and_thinning_keeps_the_budget`: start 0.5, dt 0.25, huge state: forward order [0,1,3,9] and backward order [9,3,1,0] give identical states, state at t=1.0 is [50, 1075], checkpoints kept within budget
  * statistical: None needed: everything here is integer/IEEE-deterministic. RNG vectors are exact (bit-for-bit u64) against vectors computed from the Rust formula; noise2/curl2 within 1e-15 absolute (libm-free arithmetic).
  * non-pixel: Golden vectors: hash(7,0,0), hash(7,1,2), unit(7,5,1), hash_str('birds') etc. computed independently (a ten-line integer reference in the test) and asserted; Timeline order-independence property test over random seek orders.
* Risks: SIM2-04 row says streams differ from Python's D24 noise: keep both. numba absent in the dev venv; decide in this package whether a pure-NumPy path or a hard numba requirement is accepted (recommend hard requirement + pure-python reference path for tests).

#### P4-SIM2-02: Sim node plumbing: flock/fluid/slime/erosion nodes, attribute parsing with Rust defaults and clamps, node-local clock, SimImage compositing into the node box

Rows: SIM2-03, SIM2-41 · size M · track P4-S1 · group B · schema adoption: yes · depends on: BOB-PHASE1

scenerender/nodes/sims.py registers NODES handlers 'flock','fluid','slime','erosion' (auto-imported by registry.load_plugins). Parse every attribute with the Rust defaults/clamps (agents.rs:97-228): flock count 200 clamp 1..100000, speed 120, maxSpeed 240, maxForce 400, perception 60, separationDistance 24, separation 1.5, alignment 1, cohesion 1, bounds steer|bounce|wrap (default steer), edgeMargin 40, size 6, shape streak|disc|sprite, trail 0, sprite, color; fluid resolution 128 clamp 8..1024, iterations 40 clamp 1..500, viscosity/diffusion/dissipation/velocityDissipation/vorticity/buoyancy default 0, bounds closed|open|wrap, fluidSource children (x,y,radius 20,color white,density 1,velocityX/Y 0,start 0,end); slime resolution 256 clamp 8..2048, agents 20000 clamp 1..2e6, spawn disc|random|ring|center, sensorAngle 30, sensorDistance 9, turnAngle 45, speed 60, deposit 1, decay 0.1, diffuse 0.5, saturation 4, colorLow transparent, color #FFA333-equivalent (1.0/0.64/0.2); erosion resolution 256 clamp 16..2048, octaves 6 clamp 1..12, frequency 3, droplets 20000, inertia 0.05, capacity 4, erodeRate 0.3, depositRate 0.3, evaporation 0.01, gravity 4, erodeRadius 3 clamp 1..16, relief 1, sunAzimuth 315, sunElevation 45, colorLow/colorHigh defaults (0.043,0.066,0.028 / 0.8,0.76,0.63 linear), heightmap asset id. Box size = node width/height (default 1x1). seed = numeric @seed (u64 cast) else hash_str(id). Nodes run on the node-LOCAL clock (ctx.t after local_time/offset; negative clamped to 0), STEP = 1/60 s, via sim.Timeline from P4-SIM2-01. Colour attributes must be literal; non-literal -> problem '{id}: @{attr}: simulations take literal colours; the default applies' (R30/R31 corpus codes colorHigh/colorLow belong to BOB validation). Common output path (SimImage): premultiplied linear-sRGB float RGBA grid (nx x ny) -> converted to the working colour space exactly as render.rs:1034-1075 (un-premultiply, from_linear_srgb, re-premultiply), uploaded as a texture / sampled bilinearly (mip chain in Rust, resources::mips) and drawn into the node's box through the node's world transform with node opacity and blend; cache key = hash(id) ^ step*golden so an unchanged step reuses the image. A node with no registered sim body returns None. Add FEATURES entries as PARTIAL until each sim lands.

* Python files: scenerender/nodes/sims.py (new); scenerender/sim/image.py (new: SimImage + draw/resample helper); scenerender/sim/params.py (new: attribute tables with defaults/clamps); scenerender/registry.py (read-only; FEATURES.declare calls); scenerender/compositor.py (shared: only if the Out/blend entry point needs a new 'texture quad' helper)
* Tests: tests/test_sim_nodes.py (new: parsing, defaults, clamps, seed rule, non-literal colour problem, local-clock mapping, SimImage working-space conversion)
* Rust reference: crates/sr-eval/src/agents.rs:97-228 build(), :36-47 SimImage, :56-70 Sim/Sims, :80-95 literal colours, :340-420 Sims::apply and cached(); crates/sr-gpu/src/render.rs:1034-1100 emit_sim_image, :2355 dispatch for fluid/slime/erosion; schema/scene-render-1.1.xsd:2762-2994 flockType, fluidType, fluidSourceType, slimeType, erosionType, composition child list
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/sim-plumbing.scene.xml` (new): four sim nodes with default attributes in one scene, each 64x64 in its own corner; plus a node with an @begin offset Expect: validates under the adopted schema and renders (empty/black until sims land); local clock offset respected
  * port `crates/sr-gpu/tests/sim.rs::simulated_content_inside_isolated_groups_follows_the_frame`: a sim inside an isolated group moves with the group's frame transform
  * statistical: Not applicable (plumbing). Exactness: attribute-table test compares every default/clamp against a table generated from agents.rs by hand-checked values; SimImage colour conversion within 1e-6 of the Rust formula.
  * non-pixel: Diagnostics gate: the 'literal colours' problem string matches Rust's text; schema validation of every attribute accepted by the XSD for these elements round-trips.
* Risks: Draw path depends on how the compositor accepts a float RGBA image into a node's box under an arbitrary affine; confirm against nodes/core.py image layers. If P2-EVAL-01 lands first, reuse its node-local clock; otherwise use ctx.t.

#### P4-SIM2-03: Flock core: Reynolds boids on a stable uniform neighbour grid, bounds steer/bounce/wrap

Rows: SIM2-05, SIM2-06 · size M · track P4-S1 · group B · schema adoption: yes · depends on: P4-SIM2-01, P4-SIM2-02

scenerender/sim/flock.py: FlockSpec/Agents (SoA f64), init(), step(spec, agents, dt, extra). init: x = unit(seed,k,1)*W, y = unit(seed,k,2)*H, dir = unit(seed,k,3)*2pi, v = max(speed, 0.5*maxSpeed) with math.cos/sin (glibc libm, NOT numpy SIMD, to stay bit-equal). step: cell = max(perception,1); cols/rows = ceil(size/cell); counting-sort agents into cells (stable) and visit the 3x3 neighbourhood in order dr=-1..1, dc=-1..1 (wrapping with rem_euclid in wrap mode, skipping outside otherwise), neighbours in sorted order, each agent reading ONLY previous-step state; per-neighbour: wrap-minimum-image dx,dy, d2 > perception^2 skip, accumulate alignment (velocity sum), cohesion (offset sum), separation (-d/d2 when d2 < sepDist^2 and d2 > 1e-12). Steering: steer(dir) = limit(dir/|dir|*maxSpeed - v, maxForce), weights separation/alignment/cohesion, zero-weight and zero-separation-direction skipped, only when count > 0. Steer bounds add a margin push max_force*(1 - d/margin) from each edge when edgeMargin > 0. Integrate: v = limit(v + a*dt, maxSpeed); if speed > 0 and |v| < speed, renormalise to speed (or +x if ~0); position update; wrap mode uses rem_euclid, bounce/steer reflect the velocity component and clamp to the box. Accumulation order per agent must equal Rust's sequential order to stay bit-exact: implement as NumPy over agents with an explicit loop over the 9 cells x slot index (padded) preserving order, or numba scalar loops; both validated against a literal pure-python transliteration in the tests. State.bytes = n*32.

* Python files: scenerender/sim/flock.py (new); scenerender/nodes/sims.py (hook the 'flock' kind)
* Tests: tests/test_sim_flock.py (new)
* Rust reference: crates/sr-sim/src/flock.rs:1-291 (init L63-77, limit L79, steer L91, step L99-250), tests L200-291; crates/sr-eval/src/agents.rs:98-125 FlockSpec defaults, :296-300 flock stepping, :335-362 Rust flock_frame
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/flock-t0.scene.xml` (new): 200 boids, seed 3, drawn as discs at t=0 (no step): isolates init + draw Expect: PSNR >= 45 dB vs Rust (init is exact; only raster differs)
  * scene `tools/parity/scenes/sim2/flock-bounds.scene.xml` (new): three 100-agent flocks, bounds steer/bounce/wrap, seed 5, 64x64 boxes in a row Expect: inside-box invariants at every sampled time; PSNR >= 35 dB at t<=0.25
  * scene `/home/admin/src/rs-scene-render/tools/evidence/scenes/flocking.scene.xml`: evidence scene: 600 starlings seed 7 Expect: statistical gate (below) at t=1.0, 1.5, 2.0
  * port `crates/sr-sim/src/flock.rs::alignment_orders_the_flock_and_separation_keeps_distance`: with alignment polarisation rises from <0.3 to >0.8; with alignment 0 and cohesion off it stays <0.4; separation keeps >=3x fewer close pairs; speeds stay within [speed, maxSpeed]; positions stay inside the box
  * port `crates/sr-sim/src/flock.rs::steering_bounds_keep_agents_inside`: steer mode keeps every agent inside the box
  * statistical: Boids are chaotic: bit differences amplify, so PSNR vs Rust is meaningful only for t <= ~0.25 s (gate >= 35 dB; if the Python path is exactly order-preserving expect much higher, report the actual number). For t >= 1 s the gate is statistical against the Rust IMAGE (no state dump exists in Rust): same seed; mean luminance within 3 sigma of Rust seed-to-seed (|d| <= 0.006), 16-bin luminance histogram TV <= 0.02 (Rust seed-to-seed max 0.0117), coverage fraction (non-#101418) within 3% relative. State-level (Python-only, no oracle): polarisation order parameter, nearest-neighbour distance distribution and speed bounds exactly as the Rust unit tests, over 5 seeds each. Seed policy: identical seed attribute on both sides; calibrate with seeds 1..6.
  * non-pixel: Transliteration test: the vectorised/jitted step equals a 40-line literal pure-python port of flock.rs::step bit-for-bit on 3 random states of 200 agents for 120 steps (determinism of our own port).
* Risks: Chaos: exact agreement is not guaranteed past a fraction of a second if any single add is reordered or if a libm cos/sin differs by 1 ulp; keep init scalar-math and keep accumulation order. If Rust is also compiled with FMA contraction on some targets (it is not by default), expect ulp drift.

#### P4-SIM2-04: Flock drawing (streaks, discs, sprites, trails) and force-field coupling for flocks

Rows: SIM2-07, SIM2-08 · size M · track P4-S1 · group B · schema adoption: yes · depends on: P4-SIM2-03

Feed the simulated agents through the existing cairo particle drawer: frame_particles(agents, world affine, look) producing pos/vel/size/rot (deg = atan2(vy,vx) mapped through the node world affine, velocity transformed by its linear part), orient=true, color_t=0, alpha=1, frame 0; look: size (default 6), shape streak (default; trail = max(trail, 3*size/max(speed, 0.5*maxSpeed, 1))), disc, sprite (only when the sprite asset resolves, else streak), colour attribute (literal or animated expression), sprite lookup by last path component like Rust. Add a particle-drawer entry point in nodes/particles.py (render_state(rc, state_arrays, M, look)) that does NOT require a particleEmitter element (small refactor of render_particles into a state-array path; touches the particles area, so keep the edit minimal and additive). Force fields: forceFields attribute = list of field ids (all fields when absent, none with useForceFields=false); evaluated at EACH STEP's start time t0 + (composition time - local time offset); acceleration from physics.field_accel(world affine, field list, pos, vel, t) applied per agent (in box axes: world linear part inverse), added to the steering force before integration; skip evaluating the node transform per step when no field can act (the Rust `forced` shortcut). Fluid use of the same helper is wired in P4-SIM2-06.

* Python files: scenerender/sim/draw.py (new: flock_frame); scenerender/nodes/sims.py; scenerender/nodes/particles.py (shared with the particles area: additive entry point only); scenerender/physics.py (read: field_accel; extend only if the box-axis transform needs a helper)
* Tests: tests/test_sim_flock_draw.py (new); tests/test_sim_force_fields.py (new)
* Rust reference: crates/sr-eval/src/agents.rs:366-436 flock_frame, :210-262 Forces/accel, :290-300 force coupling per step; crates/sr-sim/src/fields.rs total(); crates/sr-gpu/tests/sim.rs:303-315 force_fields_act_on_the_simulations_that_take_them
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/flock-draw-shapes.scene.xml` (new): three 40-agent flocks (streak, disc, sprite with a 8x8 PNG) seed 2, trail variants Expect: PSNR >= 35 dB at t=0 (positions exact; raster/cairo AA differences only), shape and orientation visible
  * scene `tools/parity/scenes/sim2/flock-fields.scene.xml` (new): flock inside a physics block with a directional forceField, one flock forceFields list, one useForceFields=false Expect: mean x velocity (measured as centroid drift over 1 s) differs by field and equals the Rust centroid drift within 5%
  * evidence cases to turn native: flocking
  * port `crates/sr-gpu/tests/sim.rs::force_fields_act_on_the_simulations_that_take_them`: a directional field of 10 px/s^2 moves a particle 20 px in a 2 s preroll for emitters that list it or list none; not for useForceFields=false (measured by row width)
  * statistical: Evidence gate flocking (coverage >= 0.01 of non-#101418 at t=2.0, change >= 0.002 between t=1.0 and 1.5) is deterministic-threshold; additionally the sim_gate statistical metrics of P4-SIM2-03 at t=2.0 (mean luminance, histogram TV, coverage within tolerance).
  * non-pixel: Drawer parity: 40 agents drawn from fixed arrays equal (bit-exact in float, 1/255 after quantisation) the same arrays drawn through the particleEmitter path.
* Risks: Streak vs cairo: Rust draws streaks on the GPU with different AA; do not demand >35 dB on thin streaks. nodes/particles.py is owned by the particles area: coordinate via the shared-file list.

#### P4-SIM2-05: Fluid grid, MAC staggering, edge handling and SOR pressure projection

Rows: SIM2-09 · size M · track P4-S1 · group B · schema adoption: yes · depends on: P4-SIM2-01

scenerender/sim/fluid.py part 1: Fluid state (nx, ny, h = cell size from box/resolution per FluidSpec::grid, u (nx+1)*ny and v nx*(ny+1) as f32 faces, dye RGBA f32 per cell), init(), face indexing ui/vi, bilinear sampler with edge modes (closed clamp, open clamp, wrap rem_euclid; staggered_x flag), edges() boundary handling, divergence_at, divergence(), velocity(x,y,edge), diffuse() Jacobi (iters count, f32), and project(): divergence in f64 from f32 faces, SOR omega 1.9 with RED-BLACK order (parity 0 then 1 within each iteration, closed = no neighbour for wall, open = p=0 outside, wrap = periodic), subtract the pressure gradient from the faces (skip wall faces for closed), then edges(). Red-black sweeps vectorise exactly with NumPy checkerboard masks because a colour's cells depend only on the other colour; keep p in f64 and faces in f32 with the same cast points (f.u[k] -= (p_a - p_b) as f32). Tests: projection divergence < 1/1000 and after a step < 5% of before.

* Python files: scenerender/sim/fluid.py (new)
* Tests: tests/test_sim_fluid_projection.py (new)
* Rust reference: crates/sr-sim/src/fluid.rs:1-115 spec/state/grid/bilinear, :133-163 edges, :164-232 project, :233-257 divergence/diffuse, tests L485-513 projection_removes_divergence
* **Acceptance:** 
  * port `crates/sr-sim/src/fluid.rs::projection_removes_divergence`: a random/noisy velocity field has mean |divergence| reduced by >1000x by project(), and after one full step stays <5% of the pre-projection value
  * statistical: Numerical-analysis gate rather than pixels: divergence reduction ratio (>= 1000x), pressure-solve determinism (two runs bit-equal), vectorised red-black result equals a scalar literal transliteration of project() bit-for-bit (f32 faces) on a 24x24 grid for 40 iterations.
  * non-pixel: Transliteration equality as above; no Rust oracle for intermediate state exists.
* Risks: Float width discipline is the whole risk: f64 for divergence and p, f32 for stored faces/dye; any accidental f64 face storage changes results at 1e-7 relative, which is fine for PSNR but breaks bit equality tests.

#### P4-SIM2-06: Fluid sources, force fields, buoyancy, vorticity confinement, viscosity

Rows: SIM2-10, SIM2-11, SIM2-08 · size M · track P4-S1 · group B · schema adoption: yes · depends on: P4-SIM2-04, P4-SIM2-05

fluid.py part 2, the first half of step(): (1) sources: for each fluidSource active in [start, end), radius r = radius/h, weight(x,y) = 1 - d/r inside the disc; dye += colour*(density*dt)*w on cell centres (all 4 channels), velocity relaxes toward (velocityX,Y)/h on faces with weight w (u: i, j+0.5; v: i+0.5, j). (2) forces: gravity-like force-field acceleration (accel callback of P4-SIM2-04, box axes) sampled at cell centres and mapped to faces; buoyancy lifts dye (-y, proportional to dye density/alpha, coefficient 'buoyancy'); (3) vorticity confinement computed at cell centres from the central-difference curl and its gradient, 'vorticity' coefficient; (4) viscosity by Jacobi diffusion on u and v (iterations count 20), dye diffusion at the end of step. The exact term formulas, ordering and cast points are those of fluid.rs:258-430; port statement-by-statement. Wires 'fluid' kind and fluidSource children in nodes/sims.py (attributes in P4-SIM2-02) and the Forces callback evaluating field set at t0 + offset.

* Python files: scenerender/sim/fluid.py; scenerender/nodes/sims.py; scenerender/sim/forces.py (new: Forces, accel(), box-axis inverse transform shared with flock)
* Tests: tests/test_sim_fluid_sources.py (new); tests/test_sim_fluid_forces.py (new)
* Rust reference: crates/sr-sim/src/fluid.rs:258-450 step() (sources L261-305, forces/buoyancy ~L306-319, vorticity ~L320-340, face accelerations ~L341-356, viscosity ~L357-385); crates/sr-eval/src/agents.rs:170-195 fluidSource parsing, :210-262 accel(); crates/sr-gpu/tests/sim.rs:303-315 (fluid takes fields)
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/fluid-source-window.scene.xml` (new): closed box 64x64 res 48, one source with start=0.2 end=0.6 and velocityY, no buoyancy/vorticity: isolates the source term Expect: PSNR >= 40 dB vs Rust at t=0.1,0.3,0.8 (no vorticity/advection chaos yet at this size)
  * scene `tools/parity/scenes/sim2/fluid-forces.scene.xml` (new): buoyancy 120 + vorticity 3 + viscosity 0.5, one source Expect: PSNR >= 35 dB at t=1,2 and dye centroid within 2 px of Rust
  * port `crates/sr-sim/src/fluid.rs::a_rising_source_carries_dye_up_and_conserves_it_in_a_closed_box`: dye centroid y < 150 after rising (buoyant source), and total dye mass is conserved to 1e-4 in the closed box
  * statistical: Fluid has no random numbers, so it is deterministic: the primary gate is pixel PSNR vs Rust (MATCH >= 45 desired; CLOSE >= 35 minimum with explicit report). Scalar metrics as second line: total dye mass per channel within 1e-3 relative, dye centroid (x,y) within 1 cell, kinetic energy sum(u^2+v^2) within 2%. If the first gate fails but scalar metrics pass, record DIFFERENT-but-statistically-equivalent and file a numeric-detail issue (the Rust code is the spec).
  * non-pixel: Step-by-step transliteration test against a scalar python port of each term (sources, buoyancy, vorticity, viscosity) on a 16x16 grid.
* Risks: Vorticity term is a neighbourhood stencil with single-precision intermediates; transcribe literally. Source weight uses cell-centre/face coordinates in cell units, easy to shift by half a cell.

#### P4-SIM2-07: Fluid advection, dye transport, edge modes (closed/open/wrap), conservation rescale, dissipation and image output

Rows: SIM2-12, SIM2-13 · size M · track P4-S1 · group B · schema adoption: yes · depends on: P4-SIM2-06

fluid.py part 3: semi-Lagrangian advection (backtrace with the staggered velocity sampler, bilinear sample with edge mode) of u, v and each dye channel; the step sequence per Rust (viscosity, project, advect, project); conservation: where no dye can leave (closed or wrap edges) each dye channel is rescaled to its pre-advection total (fluid.rs:~416-440), skipped for open edges; dye diffusion with 20 Jacobi iterations when 'diffusion' > 0; dissipation kd = exp(-dissipation*dt) on dye and kv = exp(-velocityDissipation*dt) on velocities; image(): premultiplied RGBA of dye with alpha capped at 1 by scaling rgb by 1/alpha when alpha > 1, negatives clamped to 0. The output goes through the SimImage path of P4-SIM2-02 and fills the box with bilinear sampling. Node-level closure: seeking determinism (reaching t directly equals playing up to it).

* Python files: scenerender/sim/fluid.py; scenerender/nodes/sims.py
* Tests: tests/test_sim_fluid_step.py (new); tests/test_sim_seek.py (new: shared by all four sims, mirrors sim.rs determinism test)
* Rust reference: crates/sr-sim/src/fluid.rs:386-452 (advection, conservation rescale ~L400-440, dissipation L446-449), image L454-462, tests L555-566 dissipation_decays_dye_exponentially, :517-552 dye conserved; crates/sr-gpu/tests/sim.rs:142-166 flocks_fluids_slime_and_erosion_draw_and_seek_deterministically
* **Acceptance:** 
  * scene `/home/admin/src/rs-scene-render/tools/evidence/scenes/fluid.scene.xml`: evidence smoke plume (vorticity 3, buoyancy 120, dissipation 0.2) Expect: PSNR >= 40 dB at t=1,2,3; coverage/changes checks of case 'fluid' pass
  * scene `tools/parity/scenes/sim2/fluid-edges.scene.xml` (new): three boxes closed/open/wrap with a source near an edge, resolution 48 Expect: PSNR >= 40 dB at t=1.5; wrap box leaks dye to the opposite edge, closed does not, open drains
  * evidence cases to turn native: fluid
  * port `crates/sr-sim/src/fluid.rs::dissipation_decays_dye_exponentially`: with dissipation 1.0 for 1 s a unit alpha dye falls to 0.5*exp(-1) within 1e-3
  * port `crates/sr-gpu/tests/sim.rs::flocks_fluids_slime_and_erosion_draw_and_seek_deterministically (fluid case)`: fluid draws, changes over time, direct seek == played == seek back (1e-5)
  * statistical: Deterministic: pixel PSNR >= 40 dB is the gate at t=1,2,3 for the evidence scene (Rust is bit-stable run to run and 80.6 dB between its own Vulkan GPU and llvmpipe renders of this scene, so >= 40 dB leaves wide margin for f32 differences). Fallback metrics if PSNR < 40: plume coverage fraction within 3% relative, dye mass within 1e-3, centroid within 1.5 px, luminance histogram TV <= 0.02.
  * non-pixel: Seek-consistency property test (same as Rust sim.rs) and mass/centroid unit checks.
* Risks: Semi-Lagrangian advection of dye with open edges has implementation-specific boundary sampling; compare per-edge-mode images separately.

#### P4-SIM2-08: Slime (Physarum): Jones agents, sensing, deposit, 3x3 diffusion, decay, two-colour ramp

Rows: SIM2-14, SIM2-15 · size M · track P4-S1 · group B · schema adoption: yes · depends on: P4-SIM2-01, P4-SIM2-02, P4-SIM2-07

scenerender/sim/slime.py: init (agents with x,y f32 and heading f32 from unit(seed,k,1/2/3): spawn random (u*nx, w*ny, ang), disc (r = 0.35*min(nx,ny), rr=sqrt(u)*r, t=w*2pi, heading t), ring (heading t+pi), center), grid per SlimeSpec::grid (cell h), step(spec, slime, step_index, dt): sa/ta in radians as f32, sd = sensorDistance/h, dist = speed*dt/h; sense three taps (heading, heading-sa, heading+sa) from a SNAPSHOT of the trail with rem_euclid wrap and clamped integer cell; turning rule (f>=l&&f>=r keep; f<l&&f<r random coin = unit(seed^0x51A1, i, step_index)<0.5 -> +ta else -ta; l>r -> -ta; else +ta); move; wall bounce: clamp to [0, w-1e-3] and new random heading unit(seed^0xB0A1, i, step)*2pi; deposit into the LIVE trail in agent order (np.add.at in index order, or numba loop, so same-cell accumulation order is preserved in f32); then 3x3 mean diffusion with clamped edges (t0 + (mean - t0)*diffuse)*(1-decay). image(): t = clamp(v/saturation, 0, 1), colour = low + (high-low)*t (premultiplied linear). f32 cos/sin: use np.float32 versions; document 1-ulp differences vs glibc cosf (heading/position drift is cell-quantised so statistically negligible).

* Python files: scenerender/sim/slime.py (new); scenerender/nodes/sims.py
* Tests: tests/test_sim_slime.py (new)
* Rust reference: crates/sr-sim/src/slime.rs:70-155 init/step/image, tests L171-214 agents_self_organise_into_a_network; crates/sr-eval/src/agents.rs:214-236 slime defaults (agents 20000, saturation 4, color 1.0/0.64/0.2)
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/slime-step1.scene.xml` (new): 2000 agents spawn disc seed 3 64x64 res 64 at t = 1/60 and 0.25 Expect: PSNR >= 45 dB at t=1/60 (one step; positions exact up to cosf), >= 35 dB at t=0.25
  * scene `tools/parity/scenes/sim2/slime-spawns.scene.xml` (new): four 64x64 nodes spawn random/disc/ring/center Expect: structure visible per spawn; statistical gate t=1.0
  * scene `/home/admin/src/rs-scene-render/tools/evidence/scenes/slime.scene.xml`: evidence: 40000 agents res 320 seed 3 Expect: statistical gate at t=1.0, 2.0, 3.0
  * evidence cases to turn native: erosion-slime
  * port `crates/sr-sim/src/slime.rs::agents_self_organise_into_a_network`: network contrast of sensing agents > 2x that of blind agents (sensorDistance 0 / turnAngle 0), and total trail <= agents*deposit/decay*1.001
  * statistical: Rust slime is deterministic per seed but not reproducible beyond ~0.25 s by pixels (an f32 cosf/sinf ulp flips a cell quantisation and trajectories diverge). Gates: t<=0.25: PSNR >= 35 dB. t>=1: same seed; mean luminance within 3 sigma of Rust seed-to-seed (|d| <= 0.013; Rust 0.1252 +/- 0.0044), luminance histogram TV <= 0.04 (Rust seed-to-seed max 0.0199), coverage of case 'erosion-slime' >= 0.05 non-black, mean trail mass within 3%, network contrast ratio (sensing vs blind, from Python state) > 2. 6-seed Rust sweep sets the floors.
  * non-pixel: Mass bound and contrast test; transliteration equality of vectorised vs scalar step with a float32-exact cos table.
* Risks: float32 transcendental differences make exact slime reproduction impractical past short horizons; documented as the convention target (statistical). Memory: 2e6 agents max in the clamp; keep float32 arrays and chunk deposits.

#### P4-SIM2-09: Erosion: Beyer droplet hydraulic erosion on fractal-noise or heightmap terrain

Rows: SIM2-16, SIM2-17 · size M · track P4-S1 · group B · schema adoption: yes · depends on: P4-SIM2-01, P4-SIM2-02

scenerender/sim/erosion.py: Terrain state (height f32 nx*ny, dropped u64, carry f64), init(): fractal value noise (octaves with seed + o*7919, frequency/long, amp halving, f doubling) using the erosion-local value_noise (smoothstep 3-2t; lattice = unit(seed, (i as i64 as u64)<<32 ^ (j & 0xFFFFFFFF), 9)); normalised to [0,1] with span >= 1e-9; heightmap option resampled nearest by (i*w/nx, j*h/ny) from a luma f32 image via the assets layer (failure -> noise + problem '{id}: heightmap: {err}; noise applies'); brush(radius) weights as erosion.rs:177-190; step(): exact = carry + droplets*dt; n = floor(exact); carry = exact - n; run droplets k = dropped.. sequentially (strict order: each droplet reads terrain modified by the previous ones); droplet(): start x,y = unit(seed^0xD20F, k, 1/2)*(n-1), inertia direction update, bilinear height/gradient, move 1 cell, leave-map break, capacity = max(-dh*speed*water*capacity, 0.01), deposit (dh>0 or sediment>cap) with bilinear four-cell split else erode with the brush clipped to the map, speed = sqrt(max(speed^2 + dh*gravity, 0)), water*= 1-evaporation, lifetime 30; leftover sediment deposited when the droplet ends inside the map. Heavy sequential loop: JIT only (numba), f64 scalar math with f32 stores at height[...] (+= (x as f32)); no fastmath. 60000 droplets x 30 steps x ~29 brush cells is ~5e7 operations in the evidence scene by t=2.5: infeasible in pure python (minutes), seconds in numba.

* Python files: scenerender/sim/erosion.py (new); scenerender/nodes/sims.py; scenerender/assets/image_pixels.py (read: luma f32 access for the heightmap)
* Tests: tests/test_sim_erosion.py (new)
* Rust reference: crates/sr-sim/src/erosion.rs:1-208 (value_noise L57, init L68-97, sample L99-110, droplet L111-176, brush L177-190, step L194-205); crates/sr-eval/src/agents.rs:238-282 erosion defaults, heightmap asset lookup via sr_media::still; tests L237-298 droplets_carve_valleys_and_conserve_material
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/erosion-t0.scene.xml` (new): erosion node 128x128 res 128 seed 5 at t=0 (init terrain shaded, no droplets) Expect: PSNR >= 45 dB vs Rust (init arithmetic is exact; shading f32)
  * scene `tools/parity/scenes/sim2/erosion-small.scene.xml` (new): 64x64 res 64 droplets 30000 seed 6 Expect: PSNR >= 40 dB at t=0.5,1.0,2.0 (droplets sequential and f64: bit-exact is attainable with numba)
  * scene `/home/admin/src/rs-scene-render/tools/evidence/scenes/erosion.scene.xml`: evidence: 640x360 res 256, 60000 droplets/s, seed 5, relief 1.5 Expect: PSNR >= 40 dB at t=0.2 and 2.5; coverage/changes checks of 'erosion' pass
  * evidence cases to turn native: erosion
  * port `crates/sr-sim/src/erosion.rs::droplets_carve_valleys_and_conserve_material`: after 60000 droplets total height stays within [0.9, 1.0+1e-3] x before, max height not above the old peak + 1e-3, lowest tenth gained material and highest tenth lost it, roughness finite, image finite and >= 0
  * statistical: Deterministic and f64 + sequential: expect near bit-exact with a no-fastmath numba kernel, so the gate is pixel PSNR >= 40 dB at t=0.2, 2.0, 2.5 (Rust self cross-adapter 67 dB). Fallback statistics if it falls short: height histogram (16 bins) TV <= 0.02 against Rust's rendered shading luminance histogram, mean luminance within 1%, ridge density (roughness = mean |laplacian| of the luma) within 5%. Calibrated seed-to-seed floors for erosion are LARGE (mean luminance 0.5555 +/- 0.0367, histogram TV 0.24-0.43) because the seed changes the whole terrain, so only same-seed gates are used for erosion.
  * non-pixel: Mass conservation (sum height within 1e-3 relative of the Rust test bound), droplet counter exactness (dropped == floor(total)), transliteration equality of the jit kernel vs a scalar python port on a 32x32 map for 500 droplets.
* Risks: Needs numba (see area_notes). FMA contraction or fastmath in LLVM would silently perturb sequential droplets; pin jit options (fastmath=False) and add a test that two droplet batches run in different chunkings give identical terrains. Python must reproduce Rust's `x as i64 as u64` cast wrap for negative lattice coords in value_noise.

#### P4-SIM2-10: Erosion hill-shaded rendering and heightmap asset path; 2D-sim evidence gate closure

Rows: SIM2-18 · size S · track P4-S1 · group C · schema adoption: yes · depends on: P4-SIM2-00, P4-SIM2-04, P4-SIM2-07, P4-SIM2-08, P4-SIM2-09

erosion.image(t, relief, azimuth, elevation, low, high): sun = (sin(az)cos(el), -cos(az)cos(el), sin(el)) (azimuth 0 = up the image, clockwise), scale = relief*max(nx,ny)*0.25, central differences with clamped edges, normal (-gx,-gy,1) normalised, lambert = max(n.sun, 0), shade = 0.25 + 0.75*lambert, colour = low + (high-low)*height times shade, alpha from the colours (opaque by default); f32 per-pixel; output through SimImage. Add the heightmap fixture path (assets id lookup by trailing path component; problem text on failure). Closure of the 2D group: run tools/parity/evidence_gate.py on fluid, flocking, erosion-slime, erosion and record the result in docs; wire the sim_gate statistical metrics for all four.

* Python files: scenerender/sim/erosion.py; tools/parity/scenes/sim2/height.png (new fixture) + erosion-heightmap.scene.xml (new); docs/parity/SIM2-STATUS.md (new, generated by sim_gate)
* Tests: tests/test_sim_erosion_shading.py (new)
* Rust reference: crates/sr-sim/src/erosion.rs:206-232 image(); crates/sr-eval/src/agents.rs:240-270 heightmap loader; tools/evidence/cases.json ids fluid, flocking, erosion-slime, erosion
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/erosion-heightmap.scene.xml` (new): heightmap PNG (gradient + bump) 128x128 res 128, sunAzimuth 45 sunElevation 30 relief 2, droplets 0 Expect: PSNR >= 45 dB vs Rust at t=0 (pure shading of known heights)
  * scene `/home/admin/src/rs-scene-render/tools/evidence/scenes/erosion.scene.xml`: evidence erosion Expect: native
  * evidence cases to turn native: fluid, flocking, erosion-slime, erosion
  * statistical: Evidence cases are threshold checks (coverage and change fractions at fixed times) and must turn from error to native; in addition the sim_gate statistical metrics per scene as listed in P4-SIM2-03/07/08/09. Shading is deterministic: PSNR >= 45 dB on a droplet-free heightmap scene.
  * non-pixel: docs/parity/SIM2-STATUS.md lists, for each of the 4 sims, PSNR per time and statistical metrics vs tolerances.
* Risks: Hill shading uses f32 math; if the PSNR gate fails by quantisation (1/255 steps) widen to 42 dB, not below.

#### P4-SIM2-11: Clay SDF: blob solids, polynomial smooth union and smooth subtraction, fingerprints displacement, boil re-seeding

Rows: SIM2-19, SIM2-20 · size M · track P4-S2 · group D · schema adoption: yes · depends on: BOB-PHASE1

scenerender/three/clay.py: Blob(shape sphere|box|capsule|torus, center, rotation quaternion from Euler YXZ(rotationY, rotationX, rotation degrees), radius 30, size 60,60,60 (width,height,depth), length 60, blend 10, subtract) with sdf(p): inverse-rotate (p-center); sphere |q|-r; box |d|+min(max(d),0) with d = |q|-size/2; capsule along local y with caps length/2; torus ring radius r, tube width/2 in the local xz plane. smin(a,b,k) = b + (a-b)h - k h(1-h), h = clamp(0.5+0.5(b-a)/k,0,1) (k<=0: min); ssub(base,cut,k) = base + (-cut-base)h + k h(1-h), h = clamp(0.5-0.5(base+cut)/k, 0, 1) (k<=0: max(base,-cut)); field(): blobs in order, first blob subtract -> +inf. Finish(amount, seed, boil): hash3 (u64 mixing with 0x9E3779B97F4A7C15, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9, >>24 to f32/2^24), trilinear value noise with smoothstep, roughness(p) = amount*(2.5*(noise(p/14)-0.5) + 0.6*(1-|2*noise(seed^0x51, p/3)-1|)); boil seed = seed ^ (floor(t*boil) * 0x2545F4914F6CDD1D) when boil > 0; sample = field - roughness. All math in float32 NumPy (vectorised over grid points) mirroring glam f32 operations; quaternion rotation of a vector as glam (q*v via the two-cross-product form). Animated blob attributes (x,y,z,radius,blend,... via <animate>) are resolved per frame on the object's local clock before building Blobs (parts lookup key 'id/blob[k]' in Rust).

* Python files: scenerender/three/clay.py (new)
* Tests: tests/test_clay_sdf.py (new)
* Rust reference: crates/sr-3d/src/clay.rs:14-135 BlobShape/Blob/Finish/smin/ssub/sdf/bounds/field, :124-165 hash3/noise/roughness, tests L405-486; crates/sr-eval/src/solid.rs:13-76 clay_inputs defaults (radius 30, blend 10, size 60, length 60, fingerprints 0, boil 0, resolution 64 clamp 8..256, seed hash_str(id))
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/clay-shapes.scene.xml` (new): one object3D clay per shape (sphere, box, capsule, torus, subtract carve) flat ambient light, resolution 64 Expect: silhouette IoU >= 0.97 vs Rust per object (alpha/colour mask), shading not compared
  * port `crates/sr-3d/src/clay.rs::blobs_merge_when_they_meet_and_split_when_they_part`: two spheres 2 components apart, 1 when near within the blend; subtracting a blob lowers volume by > 5%
  * statistical: Geometry is deterministic float32. Gates are exact-ish: field() at 10,000 random points equals a scalar pure-python transliteration within 1e-4 relative; smin/ssub boundary identities (k<=0 -> min/max); silhouette IoU >= 0.97 vs Rust render (shading differs by rasteriser so not PSNR). No sampling statistics needed.
  * non-pixel: Analytic checks: sphere sdf = |p|-r; torus distance on ring; ssub removes volume monotonically with blend.
* Risks: Rust computes with glam f32; Python NumPy f32 should match to ~1 ulp per op but order of operations inside Quat*Vec3 matters; accept 1e-4 relative field differences, they only move the mesh by < 0.01 cell.

#### P4-SIM2-12: Clay meshing: naive surface nets with admission budgets

Rows: SIM2-21 · size M · track P4-S2 · group D · schema adoption: yes · depends on: P4-SIM2-11

clay.mesh(blobs, finish, resolution, t) and mesh_with_budget(..., max_bytes): validation (resolution <= 256, finite t/amount/boil, amount in [0,1], boil >= 0, per-blob finite/positive size/radius/length>=0/blend>=0 -> error 'invalid clay field, time or resolution'); if all blobs subtract -> empty; bounds = union of non-subtract blob bounds (+blend) padded by 4*amount+2; cell = max extent/max(resolution,4); dims = ceil(extent/cell)+1; charge = points*(8 + 2*sizeof(Vertex) + 144) > max_bytes -> 'clay grid and surface exceed memory budget'; points*blobs*8 > 1e8 (only with a finite budget) -> 'clay field sampling exceeds work budget'; grid sampled in f32 as lo + (i,j,k)*cell, index (k*ny + j)*nx + i; one vertex per crossed cell at lo + (cell index + mean of edge crossings)*cell (edge table and corner order as clay.rs:230-231, s = v[a]/(v[a]-v[b])), normal = normalised central-difference gradient with e = cell/2; quads for each crossed grid edge between the 4 surrounding cells with the winding of clay.rs:300-340 (skip first-plane rule for the two TRANSVERSE axes only), two triangles per quad, per-axis orientation by inside/outside of the edge start. Vectorise with NumPy (cell classification, edge crossings via masks); vertex ordering must follow Rust's k,j,i loop order so triangle indices are comparable. Outputs the same Primitive layout as three/geometry.py meshes (positions, normals, uv 0, tangent (1,0,0,1), indices).

* Python files: scenerender/three/clay.py; scenerender/three/geometry.py (shared: primitive 'clay' hook added in P4-SIM2-13, not here)
* Tests: tests/test_clay_mesh.py (new)
* Rust reference: crates/sr-3d/src/clay.rs:166-405 mesh_with_budget, tests L434-468 a_sphere_meshes_closed_with_the_right_volume_and_outward_normals / blobs_merge_...
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/clay-sphere.scene.xml` (new): one sphere radius 40 resolution 48, flat ambient light, orthographic-looking centred camera Expect: silhouette IoU >= 0.985 vs Rust
  * port `crates/sr-3d/src/clay.rs::a_sphere_meshes_closed_with_the_right_volume_and_outward_normals`: volume within 2% of 4/3 pi r^3, vertices within 1 unit of radius 40, normals dot outward > 0.9, every edge shared by exactly two triangles (closed)
  * port `crates/sr-3d/src/clay.rs::blobs_merge_when_they_meet_and_split_when_they_part`: component count 2 apart / 1 near; carved volume < 0.95 whole
  * statistical: Deterministic mesh. Gates: closed manifold, volume within 2% (target 0.5%), vertex radial error < 1 unit, normal alignment > 0.9, component counts, and a Rust-render silhouette IoU >= 0.985 at resolution 48. Budget errors compared as exact strings.
  * non-pixel: Mesh-level invariants plus error strings for invalid inputs.
* Risks: Memory: resolution 256 is 16.8M samples x blob count; chunk the field evaluation by z-slabs.

#### P4-SIM2-13: Clay object3D integration: primitive='clay', blob children, per-frame re-meshing cache, evidence gate

Rows: SIM2-22 · size M · track P4-S2 · group D · schema adoption: yes · depends on: BOB-PHASE1, P4-SIM2-12

Wire primitive='clay' into the object3D path: three/geometry.py primitive dispatch calls clay.mesh with the evaluated blobs (children element 'blob' of the object3D; per-blob animated attributes on the object's local clock; resolution/fingerprints/boil attributes animatable), material as for other primitives; mesh cache key = (id, blobs tuple, finish, resolution, floor(t*boil)) so unchanged frames reuse the GPU mesh; mesh errors (budget) -> object skipped with a note like Rust 'clay: <error>'. Shadows/AO/path tracer see the same triangles (flatten step of P4-SIM2-23 consumes the cached mesh). Physics collisions treat clay as a sphere of its radius unless given a shape: that clause belongs to the physics area (see deferred).

* Python files: scenerender/three/geometry.py (shared, additive branch); scenerender/three/scene.py (shared: blob child parsing + mesh cache); scenerender/three/clay.py; scenerender/nodes/scene3d.py (FEATURES.declare object3D:clay)
* Tests: tests/test_clay_object.py (new)
* Rust reference: crates/sr-gpu/src/render_three.rs:630-700 clay_mesh cache and kind=='clay' branch; crates/sr-eval/src/sim3d.rs:273-275, :398 clay mesh for simulation; crates/sr-gpu/tests/scene3d.rs:479-508 clay_blobs_merge_split_and_animate; tests/corpus/valid/solid-colliders.scene.xml
* **Acceptance:** 
  * scene `/home/admin/src/rs-scene-render/tools/evidence/scenes/clay-3d.scene.xml`: evidence claymation: 3 blobs, fingerprints 0.5, boil 12, resolution 96, a blob slides out over 3 s Expect: silhouette IoU >= 0.93 vs Rust at t=0.5, 1.5, 2.5 (boil jitter included: same boil frame = same seed); evidence checks coverage #C8643C tol 0.35 min 0.2 and changes >= 0.002 pass
  * scene `tools/parity/scenes/sim2/clay-merge-split.scene.xml` (new): the sim.rs scene: 128x128, blobs radius 22 and 12 blend 10, second slides 14 -> 50 over 2 s Expect: 1 run along the middle row at t=0, 2 runs at t=2 (as the Rust test)
  * evidence cases to turn native: clay-3d
  * port `crates/sr-gpu/tests/scene3d.rs::clay_blobs_merge_split_and_animate`: number of covered runs along the middle pixel row is 1 at t=0 (merged) and 2 at t=2 (split)
  * port `crates/sr-3d/src/clay.rs::fingerprints_roughen_and_boil_changes_the_surface_between_frames`: same vertex count with/without boil, positions equal within one boil frame, differ across boil frames; roughness amplitude > 1 unit
  * statistical: Clay shading differs between rasterisers (Python PBR vs Rust PBR) so PSNR is the wrong gate. Gates: silhouette IoU (colour-or-alpha mask) >= 0.93 on fingerprinted, >= 0.97 on smooth; coverage fraction of the clay colour region within 5% relative of Rust; centroid of the mask within 1.5 px. Seed policy: seed attribute default hash_str(id) identical on both sides, boil frame floor(t*boil) identical by construction; sweep of 6 object ids for the fingerprint noise floor.
  * non-pixel: Mesh cache test: re-requesting the same frame returns the identical GPU mesh object; boil frame change rebuilds.
* Risks: Fingerprint roughness + resolution 96 yields a hash-seeded surface: any difference in hash3 or noise smoothing changes the silhouette at the 0.5-unit level, which is why IoU (not equality) is the gate.

#### P4-SIM2-14: Screen-space prepass: view normal, roughness, view depth, split-sum reflectance

Rows: SIM2-23 · size M · track P4-S3 · group E · schema adoption: yes · depends on: BOB-PHASE1

Add a second geometry pass in the moderngl renderer that writes, per pixel of the camera's 3D pass, view-space normal (xyz) + roughness (w) and view depth (r) + split-sum specular reflectance (g) for opaque objects (skips transmissive/unlit as Rust does; msaa-resolved depth with the depth-resolve rule of three_depth.wgsl fs_depth_resolve). Only runs when ambientOcclusion, contactShadows or screenSpaceReflections is enabled on the camera or light (cache per frame, shared by the three effects). Textures RGBA16F/32F sized like the 3D pass. Attributes consumed (schema-gated, camera): ambientOcclusion (bool), aoRadius (default 40), aoIntensity (default 1), screenSpaceReflections (bool); light: contactShadows (bool), contactShadowLength (default 20).

* Python files: scenerender/three/renderer.py (shared hub); scenerender/three/shaders.py (shared hub: prepass fragment); scenerender/three/prepass.py (new: pass setup + textures); scenerender/three/scene.py (shared: plumb attributes from camera/light into Frame3D)
* Tests: tests/test_3d_prepass.py (new)
* Rust reference: crates/sr-gpu/src/three.rs:~728 three-prepass pipelines, :~1860 prepass draw; crates/sr-gpu/src/three_depth.wgsl fs_depth_resolve; crates/sr-gpu/src/render_three.rs:373-376 (ex.ao = [aoRadius, aoIntensity], ex.ssr), :1939 contact
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/ssl-stage.scene.xml` (new): the 128x128 'stage' of scene3d.rs (floor + ball + ambient) with a debug env var exposing the prepass buffers Expect: prepass normals/depth equal the analytic sphere/plane values within 1% (depth) and 0.02 (normal) at 8 probe pixels
  * statistical: Deterministic analytic checks (no Rust image oracle for the prepass since it is not a user-visible output): depth of the floor plane and sphere centre pixel vs closed-form ray-plane/ray-sphere intersection <1%, normals within 0.02, roughness copy exact.
  * non-pixel: Probe-pixel unit tests.
* Risks: Python renderer is GLSL 4.1 forward with MSAA/SSAA factors (renderer.py header): the prepass must respect the supersampling reduction ('_read does it on the GPU') so depth is consistent with the colour buffer.

#### P4-SIM2-15: Screen-space ambient occlusion: 16-sample hemisphere kernel, 4x4 rotation pattern, range check, depth-aware 4x4 blur

Rows: SIM2-24 · size M · track P4-S3 · group E · schema adoption: yes · depends on: P4-SIM2-14

Fullscreen pass over the prepass: 16 samples on a fixed golden-angle-spiral hemisphere kernel (sample k at f=(k+0.5)/16 'denser near the centre'), rotated by a 4x4 interleaved per-pixel pattern, projected to view depth, occlusion accumulated with a range check against aoRadius, ao = clamp(1 - aoIntensity*occ/16, 0, 1); then a 4x4 depth-aware box blur (fs_ao_blur) removes the pattern; the result multiplies ONLY ambient and environment (IBL diffuse) light in the lighting shader (needs an ao texture binding + uniform in the forward shader), never direct lights. Mirror the exact kernel, rotation pattern and thresholds of three_post.wgsl:161-230.

* Python files: scenerender/three/ssao.py (new: passes); scenerender/three/shaders.py (shared: ao multiply in the ambient/env terms; fs_ssao, fs_ao_blur); scenerender/three/renderer.py (shared: pass ordering)
* Tests: tests/test_3d_ssao.py (new)
* Rust reference: crates/sr-gpu/src/three_post.wgsl:161-230 fs_ssao/fs_ao_blur; crates/sr-gpu/src/three.rs scene.ao; crates/sr-gpu/tests/scene3d.rs:546-563 ambient_occlusion_darkens_contacts_only
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/ssao-contact.scene.xml` (new): scene3d.rs 'stage' + ball r=18 at z=40, ambient intensity 1, camera ambientOcclusion aoRadius 12, rendered with and without AO Expect: Python on/off delta image: >40 pixels darkened >5% in the ball-foot region, open floor change <2%; Pearson r >= 0.8 with the Rust delta image over the ball-foot region and darkened-mass ratio within [0.7, 1.4]
  * port `crates/sr-gpu/tests/scene3d.rs::ambient_occlusion_darkens_contacts_only`: with ambientOcclusion aoRadius 12, more than 40 pixels around the ball foot (rows 84-100, cols 40-90) are darkened below 0.95x of the AO-off value, and the open floor at (10,120) changes by < 2%
  * statistical: Compare the DELTA image (on minus off) not absolute pixels, since base shading differs between rasterisers: Pearson r >= 0.8 inside the contact region, total darkened luminance mass within 30% of Rust's, far-field change < 2% (Rust's own bound). The kernel and pattern are deterministic, so with the same prepass the AO factor image should correlate > 0.9; report the number.
  * non-pixel: AO factor image sanity: flat floor -> 1.0, concave corner -> < 0.8.
* Risks: AO depends on exact prepass depth; any depth-convention mismatch (reversed/linear) shows as global darkening. Add a floor-only invariance test (AO == 1 on a flat plane within 0.02).

#### P4-SIM2-16: Contact shadows: 16-step screen-space march toward the light

Rows: SIM2-25 · size S · track P4-S3 · group E · schema adoption: yes · depends on: P4-SIM2-14

In the lighting shader, when light.contactShadows: march 16 steps in screen/view space from the shaded pixel toward the light over contactShadowLength (default 20 scene units), comparing against the prepass depth with the Rust thickness/bias rules (three.wgsl contact_shadow()), multiplying the light's direct contribution by the occlusion; works with no shadow map at all and composes with shadow-map attenuation (min or product exactly as the Rust shader).

* Python files: scenerender/three/shaders.py (shared); scenerender/three/renderer.py (shared: uniform plumbing); scenerender/three/lights.py (shared: contactShadows/contactShadowLength attributes)
* Tests: tests/test_3d_contact_shadows.py (new)
* Rust reference: crates/sr-gpu/src/three.wgsl contact_shadow(); crates/sr-gpu/src/render_three.rs:1939 contact length; crates/sr-gpu/tests/scene3d.rs:566-586 contact_shadows_catch_what_the_shadow_map_misses
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/contact-shadow.scene.xml` (new): cube 16^3 on the stage floor, ambient 0.1 + directional pitch -25 yaw 70, no castShadow, contactShadows length 30; on/off Expect: Python: >20 pixels darker than 0.7x and open floor within 0.02; delta image Pearson r >= 0.7 vs Rust
  * port `crates/sr-gpu/tests/scene3d.rs::contact_shadows_catch_what_the_shadow_map_misses`: with only contact shadows, more than 20 pixels darken below 0.7x of the off render, open floor unchanged within 0.02
  * statistical: Delta-image correlation and shadowed-pixel count as described (no randomness: the march has a fixed step pattern; confirm in the Rust shader whether per-pixel jitter is used and, if so, replicate the hash).
  * non-pixel: Analytic: a unit cube at known pose casts a contact shadow of expected extent along the light direction within 25%.
* Risks: Interaction with directional cascade shadows (P4-SIM2-19) must keep the same combination rule.

#### P4-SIM2-17: Screen-space reflections: view-space march with per-pixel jitter and bisection, replacing the environment specular

Rows: SIM2-26 · size M · track P4-S3 · group E · schema adoption: yes · depends on: P4-SIM2-14

A fullscreen pass (three_ssr.wgsl port, 113 lines) after the opaque lit colour: for each reflective pixel (reflectance g of the prepass > 1e-3, roughness <= 0.6), start at view_pos + n*max(z*0.003, 0.5), reflect the view ray, march in view space against prepass depth with per-pixel jitter, bisect between the last miss and the hit, reject back-face hits, fade at screen edges, with roughness and for rays toward the camera; result colour replaces the ENVIRONMENT share of the specular reflection (weight = split-sum reflectance) and falls back to the environment lookup s_env (equirect with yaw) otherwise. Per-pixel jitter hash must be copied exactly from the shader. Attribute: camera screenSpaceReflections.

* Python files: scenerender/three/ssr.py (new); scenerender/three/shaders.py (shared); scenerender/three/renderer.py (shared)
* Tests: tests/test_3d_ssr.py (new)
* Rust reference: crates/sr-gpu/src/three_ssr.wgsl:1-113 fs_ssr; crates/sr-gpu/tests/scene3d.rs:588-601 screen_space_reflections_mirror_objects_in_glossy_floors; README line 445
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/ssr-gloss-floor.scene.xml` (new): glossy floor (roughness 0.05) with a red ball r=14 at z=60; SSR on/off Expect: Python: red gain > 0.05 below the ball (rows 100-112 at x=64); empty floor within 0.03; delta-image Pearson r >= 0.7 vs Rust in the reflection region
  * port `crates/sr-gpu/tests/scene3d.rs::screen_space_reflections_mirror_objects_in_glossy_floors`: red channel (r-g) rises by > 0.05 below the ball in the mirror image, empty floor changes < 0.03
  * statistical: Delta correlation + red-gain threshold as above; if the jitter is a deterministic per-pixel hash both sides, also compare reflection-region mean colour within 10%.
  * non-pixel: Mirror analytic test: reflected sphere centre row within 2 px of the geometric mirror position.
* Risks: Python's existing environment specular path (split-sum LUT in renderer.brdf_lut) must expose the env/non-env split exactly like Rust's so replacement does not double-count.

#### P4-SIM2-18: Cascaded directional shadow maps: 4 cascades, practical split, camera-fitted, with Python PCSS kept

Rows: SIM2-27 · size L · track P4-S3 · group E · schema adoption: no · depends on: BOB-PHASE1

Replace the single all-casters orthographic directional/dome map (renderer.render_shadow kind 0) with 4 camera-fitted cascades when the camera is perspective: far = max view-z of the scene's bounding corners (>= 2*near, <= camera far); split(i) = 0.8*near*(far/near)^(i/4) + 0.2*(near + (far-near)*i/4); slice corners from the view frustum (half-extents size/2/focal_px*z); each cascade an orthographic fit to its slice AND to the casters inside the scene bounds (shadow_casters(): casters outside every slice dropped), packed as 4 layers in the existing shadow atlas (shadowData stride 14 records may need a cascade-splits field: Rust stores splits in the light's size slot: [split0, split1, split2, ies_row]); fragment selects the cascade by view z against the three splits and blends none (hard select as Rust three.wgsl); orthographic cameras and spot/point lights unchanged (no cascades when orthographic). Filtering: PCSS stays the Python default (see P4-SIM2-20 for the Rust-PCF mode) with the blocker search done per selected cascade. Golden-image policy: pixel-different from the old single map by design; update golden shadow tests in tests/test_3d_render.py after review.

* Python files: scenerender/three/renderer.py (shared hub: render_shadow, _build_lighting, shadow atlas); scenerender/three/scene.py (shared: shadow construction at L583-596); scenerender/three/shaders.py (shared: cascade selection in SHADOW_LOOKUP); scenerender/three/lights.py (shared); tests/test_3d_render.py (shadow golden update)
* Tests: tests/test_3d_cascades.py (new); tests/test_3d_render.py (touch shadow tests)
* Rust reference: crates/sr-gpu/src/three.rs:1487-1620 cascade split/fit, :176 shadow_casters, :1609-1611 splits in light.size; crates/sr-gpu/src/three.wgsl:196-236 shadow(); crates/sr-gpu/tests/scene3d.rs:527-543 directional_shadows_fit_what_the_camera_sees
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/cascade-pole.scene.xml` (new): the scene3d.rs scene: 20000-unit floor, 6x60x6 pole, sun pitch -35 yaw 70, shadowMapSize 512, ambient 0.2 Expect: pole shadow visible: darkest floor pixel in rows 86-91, cols 70-110 < 0.6x lit floor at col 30 (Python and Rust); shadow-mask IoU >= 0.8 vs Rust
  * scene `tools/parity/scenes/sim2/cascade-depth.scene.xml` (new): long checker floor with 5 posts at increasing depth, sun low Expect: each post's shadow length within 15% of Rust's measured length; shadow-mask IoU >= 0.8
  * port `crates/sr-gpu/tests/scene3d.rs::directional_shadows_fit_what_the_camera_sees`: with a huge floor and a small pole a whole-scene shadow map would be 40 units/texel and lose the pole; the camera-fitted cascade shows a shadow darker than 0.6x of the lit floor
  * statistical: Shadow geometry is deterministic: compare binary shadow masks (luminance < 0.7 x the local lit reference) between Python and Rust: IoU >= 0.8 (hard edges differ by filter: Python PCSS vs Rust 3x3 PCF; set the PCF mode of P4-SIM2-20 on for the comparison, then IoU >= 0.9). Cascade splits exact: unit test equals the formula for several (near, far).
  * non-pixel: Numeric unit tests of the split and slice-fit functions (exact), plus shadow-texel-size-vs-depth monotonicity.
* Risks: Biggest structural change in the 3D renderer; the shadow data layout (14-vec4 records, atlas placement) is shared with dome and point shadows. Orthographic cameras and view-dependent fitting break 'cache per light' assumptions (cascades depend on the camera: the World3D shadow cache is camera-independent in Python).

#### P4-SIM2-19: Shadow memory budget (512 MiB): map-size reduction and caster-layer capping with a note

Rows: SIM2-28 · size S · track P4-S3 · group E · schema adoption: no · depends on: P4-SIM2-18

Mirror three.rs:27/176/1624: SHADOW_BUDGET = 512 MiB; while map_size > 64 and map_size^2 * 4 * layers > budget halve map_size; shadow_casters(lights, cascades, max_layers) caps the number of casting layers to the device array-layer limit, dropping lowest-priority casters; both emit a render note ('shadow maps reduced ...') instead of raising. Policy decision: today Python raises explicit errors by design (renderer.py:17-19, :641-654 'GPU capacity errors are explicit, never truncation'); the parity rule mirrors Rust (degrade with note). Keep the raise for impossible requests (size > GL_MAX_TEXTURE_SIZE only after reduction fails).

* Python files: scenerender/three/renderer.py (shared hub: _shadow_atlas, render_shadow); scenerender/three/scene.py (shared)
* Tests: tests/test_3d_shadow_budget.py (new)
* Rust reference: crates/sr-gpu/src/three.rs:27 SHADOW_BUDGET, :176-230 shadow_casters, :1620-1640 map-size reduction; crates/sr-gpu/tests/hardening.rs (shadow budget cases if present)
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/shadow-budget.scene.xml` (new): 12 point lights castShadow shadowMapSize 8192 (cube maps): exceeds 512 MiB Expect: renders (no raise); a note reports the reduced size; result still shows shadows
  * statistical: Not applicable: capacity arithmetic is exact; test asserts the reduced size equals the Rust reduction loop for a table of (layers, requested size).
  * non-pixel: Table test of the halving loop.
* Risks: Behaviour change from raise to degrade is a policy call; record it in docs.

#### P4-SIM2-20: Shadow-map filter mode: Rust-compatible 3x3 PCF with light-controlled kernel scale (alongside Python PCSS)

Rows: SIM2-R01 · size S · track P4-S3 · group E · schema adoption: no · depends on: P4-SIM2-18

Add a filter mode switch (default per decision: keep PCSS where shadowSoftness > 0, use the Rust 3x3 PCF with kernel scale max(li.spot.w, 1) texels otherwise, and an env/option `shadowFilter=pcf` for parity runs): 9 taps at offsets (x,y)*soft/dim around the lookup, textureSampleCompare with bias li.flags.z, average of 9, receiver world offset along the normal 0.5, uv/ndc range rejects returning 1.0 (lit). Records the divergence in docs.

* Python files: scenerender/three/shaders.py (shared); scenerender/three/renderer.py (shared)
* Tests: tests/test_3d_shadow_filter.py (new)
* Rust reference: crates/sr-gpu/src/three.wgsl:196-236 shadow()
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/cascade-pole.scene.xml`: same pole scene with shadowFilter=pcf Expect: shadow edge profile (10-90% width in px) within 1 px of Rust
  * statistical: Edge-width and mask IoU >= 0.9 as in P4-SIM2-18.
  * non-pixel: Edge-profile unit test on a synthetic shadow.
* Risks: A product decision: PCSS is nicer than Rust's PCF; parity runs only.

#### P4-SIM2-21: Path tracer foundation: camera renderer=pathtrace/pathSamples/maxBounces/denoise, GL compute harness, accumulation and output

Rows: SIM2-29 · size L · track P4-S4 · group F · schema adoption: yes · depends on: BOB-PHASE1

Attributes (camera, schema-gated): renderer raster|pathtrace, pathSamples default 64 clamp 1..65536, maxBounces default 4 clamp 1..64, denoise default true. When pathtrace is selected for a 3D pass, the raster object pass is replaced by scenerender/three/pathtrace.py: compile pathtrace.comp (GLSL 4.3 compute, workgroup 8x8, port of cs_trace), upload buffers (SSBOs: triangle verts 24 vec4 per triangle record, nodes, tri_mat, mats, lights), dispatch in chunks of PER_DISPATCH=4 samples, accumulate radiance (float32 RGBA SSBO), average, convert through the fs_out equivalent (accumulated radiance * alpha handling, sRGB-encode/working-space as Rust: transparent where the primary ray misses, alpha=1 where covered) and hand the layer to the same compositing path as raster 3D layers (Out with premultiplied working-space radiance). Requires GL >= 4.3; gl.context() already tries 4.6 first (verified: Mesa Intel Arc 4.6 here); when unavailable the package of P4-SIM2-31 supplies the raster fallback. Includes the shared uniform block mirroring Params, camera unprojection incl. orthographic and lens-distortion ndc *= 1 + k1*dot(ndc,ndc), and DoF hooks (finished in P4-SIM2-32).

* Python files: scenerender/three/pathtrace.py (new); scenerender/three/pathtrace.comp (new GLSL port of pathtrace.wgsl cs_trace/fs_out); scenerender/three/renderer.py (shared: dispatch point); scenerender/three/scene.py (shared: option parsing); scenerender/gl.py (read; maybe a require_compute() helper)
* Tests: tests/test_pathtrace_core.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.rs:15-30 PathOpts, :821-1053 render(); crates/sr-gpu/src/pathtrace.wgsl:59 Params, :439-480 radiance setup, :637-736 cs_trace/fs_out; crates/sr-gpu/src/render_three.rs:377-381 option parsing, :2444-2461 dispatch + notes; crates/sr-gpu/tests/scene3d.rs:603-636 traced() helper and PT const
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-furnace.scene.xml` (new): grey (#BCBCBC, roughness 1) sphere r=30 under ambient intensity 1, camera fov 60 at z=-110.85, PT 64 spp 4 bounces no denoise Expect: the four probe pixels (64,64),(54,54),(74,60),(64,80) match raster within 0.03 + 0.04*a per channel (Rust test bound) and alpha 1; (2,2) alpha 0
  * port `crates/sr-gpu/tests/scene3d.rs::path_tracing_matches_the_rasteriser_where_both_are_exact`: white-furnace sphere under ambient and a directional-only case: traced equals raster at four pixels within 0.03 + 0.04*value; primary miss alpha 0
  * statistical: Furnace/energy-conservation gates (analytic, no oracle): ambient-only grey sphere returns albedo*ambient within 0.03+0.04a at 64 spp; mean over the sphere disc within 1% of albedo*ambient at 1024 spp; alpha exact.
  * non-pixel: Compile-and-dispatch smoke test, buffer-size accounting, accumulation equals running the same samples in two dispatch chunkings.
* Risks: Compute shaders are a new GL requirement (4.3); the Python renderer states GL 4.1 in places (renderer.py header). GLSL port of WGSL (var<private>, pointer params, select(), textureLoad) needs care; array bounds on SSBOs; watchdog timeouts on huge dispatches (tiling is P4-SIM2-29). This package owns the GLSL scaffold the other tracer packages fill in; keep functions in separate include files so later packages touch disjoint chunks.

#### P4-SIM2-22: Path-trace scene flattening and binned-SAH BVH

Rows: SIM2-30 · size L · track P4-S4 · group F · schema adoption: no · depends on: P4-SIM2-21

pathtrace.build(scene): flatten the same Scene3 the rasteriser draws into world-space triangles (instances expanded, deformed/skinned/morphed vertices as drawn, clay meshes from P4-SIM2-13) packed as 24 vec4 per triangle (pos/nrm xyz per corner, 6 uv sets, per-triangle material index, optional vertex colours), materials in PtMat rows (base, params metallic/roughness/transmission/ior, emissive, extra specular weight/castShadow/receiveShadow, 6 map descriptors, texture params, borders), lights, and the BVH: binned SAH with BINS = 12 over triangle centroids, leaf = (first tri, count), interior node stores second child index (first child = index - 1), 32-byte nodes {lo, a, hi, b}, triangle reorder per the build, `tri_mat` permuted. Needs a fast builder: numba (or NumPy vectorised binning per level); pure python is too slow past ~5k triangles. A CPU traverse() reference (numba) used by the brute-force equivalence test. Notes list ('what the tracer leaves out') returned to the render notes.

* Python files: scenerender/three/pathtrace_scene.py (new: flatten + packing); scenerender/three/bvh.py (new: SAH build + reference traverse); scenerender/three/pathtrace.py
* Tests: tests/test_pathtrace_bvh.py (new); tests/test_pathtrace_flatten.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.rs:165-385 build(), :385-540 bvh()/traverse, test L541-580 bvh_traversal_finds_the_same_nearest_hits_as_brute_force; crates/sr-gpu/src/pathtrace.wgsl trace()/tri_hit()/box_hit()
* **Acceptance:** 
  * scene `/home/admin/src/rs-scene-render/tools/evidence/scenes/path-tracing.scene.xml`: evidence scene geometry (64-segment spheres + box floor): triangle count = Rust's Expect: triangle count and BVH node count equal Rust's (log via --stats or recomputed from scene), nearest-hit equivalence vs brute force
  * port `crates/sr-gpu/src/pathtrace.rs::bvh_traversal_finds_the_same_nearest_hits_as_brute_force`: for many random rays BVH traversal returns the same nearest hit distance as brute force over all triangles
  * statistical: Deterministic geometry: BVH equivalence exact (hit distance within 1e-6, hit triangle identical except exact ties); flatten counts exact (triangles == sum of mesh triangles incl. instances).
  * non-pixel: Brute-force equivalence; SAH cost not worse than 1.1x of Rust's on the evidence scene.
* Risks: BVH build time in Python for large meshes (glTF characters ~100k tris) is the practical bottleneck; numba required. Tie-breaking in SAH bin choice should follow Rust exactly or traversal order differs (results do not, only speed).

#### P4-SIM2-23: Next-event estimation to analytic lights (directional, point, spot, rect/disk/sphere area)

Rows: SIM2-31 · size M · track P4-S4 · group F · schema adoption: no · depends on: P4-SIM2-22, P4-SIM2-24

Port light_sample/light_radiance/visibility/ies_value as they ARE in pathtrace.wgsl (NOT the matrix's MIS description: there is no MIS or pdf in the shader, see rust_may_be_wrong): per bounce, for every non-ambient light: sample a point (sphere area: uniform on the sphere surface via z=2u-1, a=2 pi u, radius = size.x; rect: right/up offsets (u-0.5)*width/height; disk: sqrt(u)*width/2, angle 2 pi u), direction/distance, distance attenuation att = 1/pow(max(dist/100, 0.01), falloffExponent) with the range window clamp(1-(d/range)^4,0,1) applied TWICE (as shipped, see rust_may_be_wrong), spot smoothstep(cosOuter, cosInner, dot(-l, dir)), rect/disk cosine max(dot(-l,dir),0), IES via ies_value; shadow ray only when the light casts shadows and the surface receives them; contribution = thr*bsdf*cos*radiance*visible, indirect (bounce>0) contributions clamped to 20 per channel. The sequence of rnd() calls per light must match Rust (z,a for sphere; two for rect; two for disk), which is what makes sample-exact comparison possible. Volume transmittance on shadow rays only when volumes exist (volume area).

* Python files: scenerender/three/pathtrace.comp (light include chunk); scenerender/three/pathtrace_scene.py (PtLight packing: pos/kind, dir/range, colour/falloff, spot, size, right/influence bits)
* Tests: tests/test_pathtrace_lights.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.wgsl:382-436 light_sample/ies_value/light_radiance, :575-607 NEE loop, visibility(); crates/sr-gpu/src/pathtrace.rs:55-70 PtLight
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-lights.scene.xml` (new): diffuse floor lit by one light of each kind (directional, point, spot, rect, disk, sphere-area) in separate panels Expect: sample-exact mode: PSNR >= 45 dB vs Rust at 128 spp no denoise; converged mode (1024 spp both): regional means within 1%
  * port `crates/sr-gpu/tests/scene3d.rs::path_tracing_matches_the_rasteriser_where_both_are_exact (directional case)`: directional light only: traced equals raster
  * port `crates/sr-gpu/tests/scene3d.rs::path_tracing_is_deterministic_and_shadows_and_refracts`: same frame twice bit-identical, floor under the ball in shadow (< 0.5x open floor), glass shows the red layer behind
  * statistical: Primary exact-mode gate: same pathSamples and the Rust PCG stream => per-pixel agreement (PSNR >= 45 dB, Rust itself is 72 dB between its own adapters on this scene). Secondary converged gate: 4096 spp both, 8x4 grid region means within 1% and global mean within 0.5%; noise level: standard deviation in a flat region at 128 spp within 1.5x of Rust's. Rust measured on this machine: 128 spp vs 4096 spp reference = 36.9 dB undenoised, 47.4 dB denoised.
  * non-pixel: Energy test: a point light over an infinite diffuse plane equals the analytic irradiance albedo/pi * I * cos / d^2 (with the shipped attenuation law) within 2%.
* Risks: Because shipped area-light sampling is not unbiased (no pdf, no area Jacobian), analytic physical expectations do not apply to area lights: the oracle is the Rust image.

#### P4-SIM2-24: Path tracer BSDF: Lambert + GGX (rasteriser-matching), lobe sampling, smooth dielectric transmission

Rows: SIM2-32 · size M · track P4-S4 · group F · schema adoption: no · depends on: P4-SIM2-22

Port d_ggx, v_smith, bsdf(s,n,v,l,lobes), pdf_of, sample_dir, surf_of (specular weight mix(clamp(extra.x,0,1), 1, metallic), lobe selection probability clamp(mix(0.25,1,metallic),0.05,1) per line 316), transmission/ior path (fresnel Schlick reflectance choose reflect vs refract with rnd() < fr, tint thr *= albedo on transmission, 1e-3 offset along the normal, transmission events do not count as bounces), opacity events. Identical algebra to the existing Python raster GGX where possible: reuse shader functions from three/shaders.py by including them in the compute shader so raster and tracer agree.

* Python files: scenerender/three/pathtrace.comp (BSDF chunk); scenerender/three/shaders.py (read; share GGX/Smith functions as an include string)
* Tests: tests/test_pathtrace_bsdf.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.wgsl:240-380 d_ggx/v_smith/surf_of/bsdf/pdf_of/sample_dir, :560-585 transmission branch; crates/sr-gpu/tests/scene3d.rs:611-636
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-materials.scene.xml` (new): spheres: rough dielectric, smooth metal, rough metal, glass (transmission 1, ior 1.5) over a floor with a grey ambient and one directional light Expect: sample-exact PSNR >= 45 dB at 128 spp; converged region means within 1.5%
  * port `crates/sr-gpu/tests/scene3d.rs::path_tracing_matches_the_rasteriser_where_both_are_exact`: BRDF agreement between traced and rasterised diffuse sphere
  * statistical: Importance-sampling consistency (pdf integrates to 1 over the hemisphere within 1%; E[bsdf*cos/pdf] over samples equals the directional albedo within 2% at 1e5 samples, computed in numpy against the GLSL port via a debug dispatch) plus the image gates of P4-SIM2-23.
  * non-pixel: Furnace tests: rough conductor with white ambient has albedo <= 1 and above 0.8 for roughness 0.5.
* Risks: The matrix correctly says a dielectric ray bend is new vs Python's screen-space refraction.

#### P4-SIM2-25: Russian roulette, firefly clamp, ambient and dome light for escaping rays

Rows: SIM2-33 · size M · track P4-S4 · group F · schema adoption: no · depends on: P4-SIM2-24

After bounce 2, survive with probability q = min(max(thr), 0.95) and rescale thr /= q (using one rnd()); indirect light contributions clamped to 20 (done in the NEE chunk); rays that leave the scene gather env_radiance(direction): the dome environment (equirect sampling with yaw/pitch of the Python lights.py dome, mip/blur level by roughness as the Rust lookup) and the ambient light split by the previous scattering event's lobe fractions (ambient_diffuse/ambient_specular from diffuse/total, specular/total) so ambient does not change dome illumination; alpha accounting: miss on the primary ray => alpha 0 (transparent), only_glass paths still see the 2D plane (see P4-SIM2-26).

* Python files: scenerender/three/pathtrace.comp (RR + env chunk); scenerender/three/lights.py (read: dome yaw/pitch, ambient); scenerender/three/pathtrace.py (environment texture binding)
* Tests: tests/test_pathtrace_env.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.wgsl:610-625 roulette, env_radiance, :236-262 s_env, README line 466
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-dome.scene.xml` (new): diffuse + chrome sphere in a dome light (equirect gradient PNG), yaw 40, no other lights Expect: sample-exact PSNR >= 45 dB 128 spp; chrome sphere reflection mean colour within 2% at 1024 spp
  * port `crates/sr-gpu/tests/scene3d.rs::the_dome_turns_by_its_yaw_and_pitch_and_decodes_srgb`: raster dome orientation/sRGB decode (the tracer shares the lookup)
  * statistical: Unbiasedness of roulette: mean pixel value at maxBounces 8 with RR equals the value computed with RR disabled (debug define) within 1% at 2048 spp (statistical test, 3 sigma using the sample variance); otherwise as P4-SIM2-23.
  * non-pixel: Roulette variance/bias unit check on a closed furnace box.
* Risks: Rust applies roulette after bounce>2 regardless of maxBounces; keep identical (it changes the noise pattern, not the expectation).

#### P4-SIM2-26: Glass sees the 2D layers: exit rays hit the composition plane

Rows: SIM2-35 · size M · track P4-S4 · group F · schema adoption: no · depends on: P4-SIM2-24

Rays that leave the 3D scene after passing only through glass (only_glass) intersect the composition plane z = 0 and sample the 2D layer pixels behind the pass (the already-rendered backdrop texture, uploaded per frame) at the hit's composition position, multiplied by the accumulated throughput tint; replaces Python's screen-space refraction for traced passes. Supports the layer texture in working space, premultiplied, with the same pass-to-composition coordinate mapping as 2.5D layers.

* Python files: scenerender/three/pathtrace.comp (plane chunk); scenerender/three/pathtrace.py (backdrop texture upload from the compositor); scenerender/nodes/scene3d.py (backdrop access)
* Tests: tests/test_pathtrace_glass_layers.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.wgsl radiance() (layer plane sample); crates/sr-gpu/tests/scene3d.rs:82-101 glass_refracts_the_layers_behind_it, :434-449; README line 457
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-glass-layer.scene.xml` (new): the scene3d.rs scene: red 32x32 layer behind a glass sphere r=16 at z=-10 and a white floor + ball, sun pitch -80 Expect: red visible through the glass (r > 0.3, r > 2*g) in both; PSNR >= 45 dB sample-exact on the refracted region
  * evidence cases to turn native: path-tracing
  * port `crates/sr-gpu/tests/scene3d.rs::path_tracing_is_deterministic_and_shadows_and_refracts`: through the glass the red layer behind shows refracted: r > 0.3 and r > 2*g at (96,50)
  * statistical: Region mean colour inside the glass disc within 3% of Rust at 1024 spp; refracted-image displacement (centroid of the red layer inside the glass) within 1 px.
  * non-pixel: Analytic refraction: ray through a glass slab (box) shifts by the Snell lateral displacement within 0.5 px.
* Risks: Backdrop = everything below the 3D block in z order; interplay with 'objects in front of the plane' ordering (scene3d.rs:422) needs checking in the compositor.

#### P4-SIM2-27: A-trous denoiser: 5 passes, edge-aware, guided by first-hit albedo and normal

Rows: SIM2-36 · size M · track P4-S4 · group F · schema adoption: yes · depends on: P4-SIM2-21

Compute passes cs_atrous with step 1,2,4,8,16 (DENOISE_PASSES=5), edge-stopping weights from first-hit albedo, normal and luminance as in the shader (Dammertz 2010), guide buffers written by the trace pass on the first sample, DENOISE_HALO = 2*(2^5-1) = 62 px when tiled; `denoise` toggle; alpha preserved. Run on the accumulated radiance after all samples.

* Python files: scenerender/three/pathtrace.comp (a-trous chunk, separate program); scenerender/three/pathtrace.py
* Tests: tests/test_pathtrace_denoise.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.wgsl cs_atrous; crates/sr-gpu/src/pathtrace.rs:583-625 DENOISE_*, tiles(); crates/sr-gpu/tests/scene3d.rs:659-681 path_tracing_denoiser_smooths_noise_and_keeps_the_mean
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-denoise.scene.xml` (new): floor + sphere under a sphere-area light r=25 intensity 60, 4 spp, denoise on/off Expect: variation (mean |neighbour difference|) falls below 0.5x and mean stays within 5% (Rust test bounds); PSNR(denoised Python, denoised Rust) >= 40 dB
  * port `crates/sr-gpu/tests/scene3d.rs::path_tracing_denoiser_smooths_noise_and_keeps_the_mean`: at 4 spp the a-trous filter lowers pixel-to-pixel total variation below half and keeps the region mean within 5%
  * statistical: Region statistics (mean preserved within 5%, total-variation ratio < 0.5) as in the Rust test, plus denoised-vs-denoised PSNR >= 40 dB when both sides use the same input noise (sample-exact). With different noise the denoised outputs agree within the 128 spp convergence floor (47 dB vs 4096 spp for Rust).
  * non-pixel: Edge preservation: a synthetic step edge keeps >= 90% of its contrast after filtering.
* Risks: Edge-stopping constants are in the shader; copy literally.

#### P4-SIM2-28: Path tracer determinism and tiled execution: PCG (pixel,sample) RNG, 4-sample dispatches, device-limit tiling

Rows: SIM2-37 · size M · track P4-S4 · group F · schema adoption: no · depends on: P4-SIM2-21, P4-SIM2-27

pcg(v) = ((s >> ((s >> 28) + 4)) ^ s) * 277803737 with s = v*747796405 + 2891336453 (u32 wrap), rnd() = (rng >> 8)/2^24, per-sample seed rng = pcg(global_pix*9781 + pcg(si*6271 + 1)), global_pix computed from the TILE-global pixel position, si = sample index within the whole sequence (start + i), so tiled output equals untiled; tiles sized to DEFAULT_TILE_BYTES = 32 MiB (limit_buffers(bytes) rejects budgets that cannot hold the denoise halo, MIN_TILE_BYTES = (2*HALO+64)^2*32), halo 62 px when denoising, bit-identical repeat renders (no accumulation leaks across frames: clear buffers). UHD (3840x2160) renders path-traced without fallback.

* Python files: scenerender/three/pathtrace.py (tiling + dispatch loop); scenerender/three/pathtrace.comp (rng chunk)
* Tests: tests/test_pathtrace_tiles.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.wgsl:147-160 pcg/rnd, :637-660 per-pixel seeding; crates/sr-gpu/src/pathtrace.rs:582-640 PER_DISPATCH/tiles/limit_buffers; crates/sr-gpu/tests/pathtrace_tiles.rs:12-118 (uhd, tiled == whole frame, repeated render equals)
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-tiles.scene.xml` (new): small lit scene rendered with tile budget forced small (env SCENERENDER_PT_TILE_BYTES) Expect: tiled output == whole-frame output exactly (bytes), repeated render identical
  * port `crates/sr-gpu/tests/pathtrace_tiles.rs::tiled_paths_match_whole_frame_at_edges_and_denoise_seams`: for denoise on/off and orthographic on/off, a tiny tile budget gives the same pixels as the whole frame, repeated renders identical, limit_buffers(1024) rejected
  * port `crates/sr-gpu/tests/pathtrace_tiles.rs::uhd_document_renders_pathtraced_without_fallback`: 3840x2160 renders with no errors and no unsupported notes, centre pixel lit, corner alpha 0
  * statistical: Determinism is exact on a given GL driver: byte-identical repeats and tiled==whole (tested). Cross-driver tolerance (Rust measured: Vulkan GPU vs llvmpipe 72.2 dB, max channel diff 5/255 on path-tracing.scene.xml): Python must reproduce its own results across Mesa GL GPU and llvmpipe at >= 60 dB. Sample-exact vs Rust: PSNR >= 45 dB (see P4-SIM2-23).
  * non-pixel: PCG golden vectors (seeded constants) in a unit test, compared with an independent numpy u32 implementation.
* Risks: Rust has no random seed attribute for the tracer, so determinism = purely pixel/sample hash: reproducing it exactly is what makes the cheaper sample-exact gate possible; any extra rnd() call in a branch shifts the stream for the rest of the path.

#### P4-SIM2-29: Path tracer feature coverage part 1: texture maps, displacement, vertex colours, alpha masks/blending in shadow rays, orthographic and offscreen projection

Rows: SIM2-38 · size L · track P4-S4 · group F · schema adoption: no · depends on: P4-SIM2-22, P4-SIM2-24

map_sample over the 6 material maps (shared coordinate sets with the rasteriser: base colour, metallic-roughness, normal with normalScale, occlusion strength, emissive, ...) with packed sRGB/sampler flags and borders in PtMat.maps/borders; pixel atlas uploaded as an SSBO (pixels u32) per Rust packing; displacement already applied in flatten (deformed vertices), vertex colours, alphaMode MASK/BLEND handling in tri_hit and shadow rays (alpha cutoff, opacity events); orthographic camera (env.z) and resized offscreen clip transforms. Texture uploads reuse the renderer's texture atlas (three/texture_atlas.py, material_atlas.py) to share decoding.

* Python files: scenerender/three/pathtrace_scene.py; scenerender/three/pathtrace.comp (maps chunk); scenerender/three/material_atlas.py (read); scenerender/three/texture_atlas.py (read)
* Tests: tests/test_pathtrace_maps.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.wgsl map_sample/tuv; crates/sr-gpu/src/pathtrace.rs:165-385 build() texture packing; crates/sr-gpu/tests/maps3d.rs:169-190 (ortho pathtrace maps); crates/sr-gpu/tests/ocean.rs, fracture.rs (pathtrace mode)
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-maps.scene.xml` (new): textured plane (checker base colour + normal map + MR map) and an alpha-masked leaf quad, perspective and orthographic cameras Expect: sample-exact PSNR >= 40 dB at 128 spp; converged region means within 2%
  * port `crates/sr-gpu/tests/maps3d.rs::ortho pathtrace maps (L169)`: orthographic path-traced maps sample the same texels as raster
  * statistical: Texel-exact where deterministic: sampled colour of a flat-lit textured quad equals the texture within 1/255 at 16 probe points (nearest/linear as the sampler says). Rendering statistics otherwise as P4-SIM2-23.
  * non-pixel: Texture-probe unit tests per map type.
* Risks: Large surface area; Python raster already supports the maps so most data is prepared. Mipmap/LOD choice in the tracer differs from raster (no derivatives): Rust picks nearest/LOD0? confirm in map_sample before porting.

#### P4-SIM2-30: Path tracer feature coverage part 2: Gaussian splats with SH colours in the BVH, IES profiles, lens distortion

Rows: SIM2-38 · size M · track P4-S4 · group F · schema adoption: no · depends on: P4-SIM2-23, P4-SIM2-29

Splat records (24 vec4 per splat, tri_mat = 0xFFFFFFFF marker) intersect as 3D ellipsoid footprints: t = -d.(M p)/max(d.(M d), 1e-20), radius = q.(M q) <= 9, opacity = min(0.99, alpha*exp(-0.5 radius)) and composite along rays; SH colours evaluated per view direction (pt_sh_color); IES profile texture (ies_value polar lookup, shared pixel buffer) per light; lens distortion k1 in the camera ray generation (already in P4-SIM2-21 uniforms) verified against the raster path. Cross-area: Gaussian splat loading exists in Python (three/loaders.py).

* Python files: scenerender/three/pathtrace_scene.py (splat + IES packing); scenerender/three/pathtrace.comp (splat/ies chunks); scenerender/three/lights.py (IES profiles: read)
* Tests: tests/test_pathtrace_splats_ies.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.wgsl:167-200 splat tri_hit, pt_sh_color, ies_value (L405-418); crates/sr-gpu/src/pathtrace.rs:165-385 splat packing, PtLight IES offset/width
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-splat-ies.scene.xml` (new): a 2k-splat object (generated) and an IES-profiled spot on a wall Expect: sample-exact PSNR >= 40 dB; IES profile ring brightness within 3%
  * statistical: Splat compositing: converged region means within 2%; IES: mean radiance on 8 azimuth-sampled points of the lit wall equals the profile candela ratio within 3%.
  * non-pixel: IES polar lookup unit test against Python's raster IES texture.
* Risks: Rust notes() currently returns an empty list (nothing left out), so every feature above is expected to be traced.

#### P4-SIM2-31: Path tracer fallback to rasterisation under device limits, with a diagnostic note

Rows: SIM2-39 · size S · track P4-S4 · group F · schema adoption: no · depends on: P4-SIM2-21

fits(size, triangles, limits): u32 pixel index check (w*h <= u32::MAX), min tile bytes (storage binding size >= MIN_TILE_BYTES), triangles*384 bytes <= min(max_storage_buffer_binding_size, max_buffer_size); limit_note(scene, limits): volumes validation error, geometry + texture + IES + volume + 128 bytes against the cap; messages exact ('path tracing {n} triangles needs a {m} MiB buffer and this device binds at most {k} MiB; rasterised instead' etc.). When a limit fails, or GL < 4.3 / no compute shader, render the 3D pass with the raster path and append the note to the node/camera render notes. Map GL limits GL_MAX_SHADER_STORAGE_BLOCK_SIZE and GL_MAX_TEXTURE_BUFFER_SIZE to wgpu's two caps.

* Python files: scenerender/three/pathtrace.py; scenerender/three/scene.py (shared: note plumbing)
* Tests: tests/test_pathtrace_fallback.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.rs:107-163 fits/limit_note; crates/sr-gpu/tests/hardening.rs:131 (limit fallback test); crates/sr-gpu/src/render_three.rs:2444-2461
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-fallback.scene.xml` (new): pathtrace camera with an env override that sets a tiny storage-buffer limit Expect: renders via raster, note text equals Rust's format
  * port `crates/sr-gpu/tests/hardening.rs::path-trace fallback under limits (L131)`: an over-limit scene is rasterised and a note is reported
  * statistical: Not applicable (control flow).
  * non-pixel: Message strings and decision table tests.
* Risks: GL has no direct equivalent of wgpu's two limits; document the mapping.

#### P4-SIM2-32: Path tracer thin-lens depth of field

Rows: SIM2-34 · size S · track P4-S4 · group F · schema adoption: no · depends on: P4-SIM2-21

In ray generation: when the camera has an aperture (cam.y radius > 0 and not orthographic) focus = o + d*(cam.z/dot(d, forward)); lens sample radius = sqrt(rnd())*apertureRadius, angle = 2 pi rnd() (circular aperture; apertureBlades is NOT used by the tracer), origin offset in camera space, d = normalize(focus - o). Aperture radius and focus distance from the same camera parameters as the raster DoF (fStop, sensor, focusDistance/focusTarget resolved in camera.py). rnd() ordering: lens samples BEFORE any path rnd().

* Python files: scenerender/three/pathtrace.comp (camera chunk); scenerender/three/pathtrace.py; scenerender/camera.py (read: DoF parameter derivation)
* Tests: tests/test_pathtrace_dof.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.wgsl:439-460 thin lens; crates/sr-gpu/src/pathtrace.rs:913 cam params; crates/sr-gpu/src/render_three.rs:367-436 DoF/focus parameters; crates/sr-gpu/tests/scene3d.rs:205-216 camera_depth_of_field_blurs_out_of_focus_objects
* **Acceptance:** 
  * scene `tools/parity/scenes/sim2/pt-dof.scene.xml` (new): three spheres at 3 depths, focus on the middle one, fStop 2.8, 128 spp Expect: sample-exact PSNR >= 40 dB; blur radius (edge 10-90% width) of the near and far spheres within 15% of Rust
  * port `crates/sr-gpu/tests/scene3d.rs::camera_depth_of_field_blurs_out_of_focus_objects`: out-of-focus objects blur, in-focus stays sharp
  * statistical: Circle-of-confusion radius from edge-spread width vs the thin-lens formula within 15%; same-RNG comparison vs Rust PSNR >= 40 dB.
  * non-pixel: Analytic CoC: c = A * |s - f| / s.
* Risks: Raster DoF (post-process, polygonal blades) and tracer DoF (circular) differ by design in both engines; do not try to unify.

#### P4-SIM2-33: Path tracer acceptance: evidence case path-tracing, convergence study vs Rust 4096 spp, behavioural suite closure for SIM2-40

Rows: SIM2-40 · size M · track P4-S4 · group G · schema adoption: yes · depends on: P4-SIM2-10, P4-SIM2-13, P4-SIM2-15, P4-SIM2-16, P4-SIM2-17, P4-SIM2-18, P4-SIM2-21, P4-SIM2-22, P4-SIM2-23, P4-SIM2-24, P4-SIM2-25, P4-SIM2-26, P4-SIM2-27, P4-SIM2-28, P4-SIM2-29, P4-SIM2-30, P4-SIM2-31, P4-SIM2-32

Closure package for the area test suite parity: run evidence case 'path-tracing' (coverage >= 0.3 of non-#101418 in region [100,100,300,120] at t=0, centre-ball pixel (250,150) not black), produce the convergence study with sim_gate (Python at 64/128/256/512/1024 spp vs Rust 4096 spp: PSNR, regional means, noise sigma) and commit its table to docs/parity/SIM2-STATUS.md; assemble and run the full ported test list of the area (flock/fluid/slime/erosion unit tests, clay tests, AO/contact/SSR/cascade, BVH, furnace, determinism, denoiser, tiles) as a marker group `pytest -m sim2` ; SIM2-40 row: behaviour parity of the Rust suites = every ported test listed in each package exists and passes.

* Python files: tools/parity/sim_gate.py; docs/parity/SIM2-STATUS.md; tests/conftest.py (marker sim2, shared)
* Tests: tests/test_pathtrace_acceptance.py (new)
* Rust reference: tools/evidence/cases.json case path-tracing (bench: true); crates/sr-gpu/tests/scene3d.rs:603-681; crates/sr-gpu/tests/sim.rs; crates/sr-sim/src/*.rs tests; crates/sr-3d/src/clay.rs tests
* **Acceptance:** 
  * scene `/home/admin/src/rs-scene-render/tools/evidence/scenes/path-tracing.scene.xml`: evidence still life: chrome, glass, soft sphere-area light, 128 spp, 5 bounces, 480x270 Expect: evidence checks pass; sample-exact PSNR >= 45 dB (denoise on or off per scene default: the scene has denoise default true); converged (4096 spp both): global mean within 0.5%, 8x4 region means within 1%, PSNR >= 38 dB undenoised
  * scene `tools/parity/scenes/sim2/pt-convergence.scene.xml` (new): same scene, spp as parameter Expect: Python RMSE vs the Rust 4096 spp reference falls like 1/sqrt(spp) (log-log slope -0.5 +/- 0.1)
  * evidence cases to turn native: path-tracing
  * statistical: Convergence at N spp: Python image at N in {128, 512, 4096} vs Rust 4096 spp: PSNR (undenoised) must be within 3 dB of the Rust-vs-Rust figure at the same N (measured: Rust 128 vs 4096 = 36.9 dB, 47.4 dB denoised, mean luminance 0.5404 vs 0.5405 -> gate: >= 34 dB undenoised at 128, >= 44 dB denoised); mean and regional means within 0.5%/1%; RMSE slope -0.5 +/- 0.1; per-channel mean over the chrome-ball disc within 2%. Seed/sample policy: RNG is a hash of (pixel, sample) so there is no seed: the sample index set {0..N-1} is the only 'seed'; identical on both sides.
  * non-pixel: docs/parity/SIM2-STATUS.md table of all gates for SIM2.
* Risks: Needs GL compute on the CI machine; mark tests with skip-if-no-compute and a software (llvmpipe) lane. Wall-clock: path-tracing 4096 spp took 19.6 s in Rust on an iGPU; Python on llvmpipe will be much slower: keep a 128 spp gate in CI and 4096 spp nightly.

### CINE

#### P4-CINE-00: CINE parity harness: statistical comparators, volume-field oracle, fixture generators

Rows: CINE-37, CINE-38 · size M · track P4-C0 · group G0 (first) · schema adoption: no · depends on: none

Single tool set every CINE package uses as its gate runner. (1) tools/parity/cine_stats.py: comparators with fixed tolerances: region_moments (mean/variance over an NxM grid of regions, linear-light), luma_histogram_emd (1-D Wasserstein over 64 bins), blob_match (connected-component centroids + Hungarian match for particle/spray fields), field_rel_l2 (grid fields), convergence_slope (log-log error vs spp), furnace_energy (mean radiance vs analytic), seed_sweep (renders K seeds -> mean and sigma per region). (2) SRVOL/SRVSEQ field oracle: runs the Rust release binary `scene-render bake-volume SCENE --object ID -o DIR` (works on any Rust-valid pyro scene, even if the Python renderer cannot render it) and loads the frames with the Python SRVOL reader (P4-CINE-01) for field-level comparison against Python's own pyro. (3) tools/parity/scenes/cine/gen_fixtures.py: deterministic generators (numpy only) for every binary input the Rust corpus references but does not ship (tests/corpus/valid/*.scene.xml point at ../media/uniform.srvol, advected-%d.srvol, impact-0.vdb, terrain.pmtiles, mesh-frame-%d.obj, ... which are absent): SRVOL cloud/slab/advected sequences, SRVSEQ bake dirs, OBJ mesh sequences, Terrarium/Mapbox PNG DEM, a tiny PMTiles v3 archive, a closed sphere/box/torus OBJ set. Each fixture is validated by running the Rust binary (`scene-render validate` and a strict 1-frame render) so the generators are checked by Rust, not by our own reader. (4) render_parity.py sidecar flags --spp/--seed/--region-grid so the PNG-level statistics (2) run on both renderers' output. (5) A results.json/SUMMARY.md per run listing each gate (name, statistic, value, tolerance, verdict).

* Python files: tools/parity/cine_stats.py (new); tools/parity/scenes/cine/gen_fixtures.py (new); tools/parity/scenes/cine/*.scene.xml (new; authored by the packages that own them, listed in each gate); tools/parity/scenes/cine/scenes.json (new manifest: scene, args, times, expected verdict per package); tools/parity/render_parity.py (touch: sidecar args; shared with other areas, additive only)
* Tests: tests/test_cine_stats.py (new): unit tests of each comparator on synthetic inputs with known answers (EMD of shifted histograms, Hungarian match with dropped blobs, slope of a synthetic 1/sqrt(N) series = -0.5, furnace on a constant image); tests/test_cine_fixtures.py (new): generators are deterministic (sha256 stable across two runs); skipped Rust validation when SR_RS_BIN unset
* Rust reference: tools/evidence/cinematic-impact.json (visual_probe, thermal_preview, sequence_preview: how the Rust author produced small probe scenes); tools/openvdb_fixture.cpp (OpenVDB fixture generator: reused for P4-CINE-07 fixture list); crates/scene-render/src/main.rs:46,597 bake-volume (oracle command); crates/sr-volume/tests/data/openvdb/*.vdb (17 official-library files reusable verbatim as Python fixtures)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/harness-selftest.scene.xml` (new): Smoke: Rust-vs-Rust and Rust-vs-perturbed run of every comparator on a pyro scene baked by Rust Expect: Rust vs itself -> all gates PASS; one perturbed region -> the named gate FAIL with region id
  * statistical: policy: Defines the tolerances reused everywhere (see area_notes G1-G6): G1 deterministic-field rel-L2; G2 region moments; G3 histogram EMD; G4 blob match; G5 convergence slope; G6 furnace energy. Seeds: Rust path tracer seed policy is a pcg() hash of global pixel and sample index (crates/sr-gpu/src/pathtrace.wgsl:149-160), so repeated Rust renders are bit-identical; K=8 'seed sweep' for Python varies a seed salt only inside the harness (not exposed in schema).; tolerances: harness self-test must show Rust-vs-Rust PASS at tolerance zero
  * non-pixel: Harness self-test only; no renderer feature.
* Risks: Rust bake-volume needs the GPU only for nothing (CPU bake) but a pyro scene must be valid 1.3; fixtures that fail Rust strict render are bugs in the generator, not in Rust; render_parity.py is shared with the other areas: edits must stay additive

#### P4-CINE-01: SRVOL v1 reader/writer, SparseGrid and affine world-space trilinear sampling

Rows: CINE-02, CINE-03 · size M · track P4-C1 · group G0 (no deps; starts immediately) · schema adoption: no · depends on: none

Python mirror of sr-volume lib.rs. SRVOL: magic 'SRVOL\0\r\n', u32 version 1, u32 grid count (0-64), per grid name (u16 length, 1-64 ASCII [A-Za-z0-9_.-], unique, sorted), f32 finite background, 16 f64 column-major affine index-to-world (finite, invertible, finite inverse; reflection/shear allowed, projective rejected), u32 brick count, per brick 3 x i32 key in [-2^28, 2^28-1] then 512 f32 (8^3, x fastest); keys unique and sorted, background-only bricks pruned, EOF required (trailing bytes error), budgets (256 MiB, 100k bricks, 64 grids default CacheLimits) enforced from header counts BEFORE allocation, non-finite sample rejected, failed insert leaves the grid unchanged. Writer is canonical: same Volume -> identical bytes. SparseGrid: dict brick-key -> float32 (8,8,8) array (packed into a (n,8,8,8) pool plus a sorted key array for vectorised lookup via np.searchsorted on a packed int64 key); Euclidean (floor) division for negative indices; absent brick samples as background; trilinear sample_index includes background from absent bricks; sample_world = world_to_index through the grid's own Transform (each channel has its own transform). Vectorised sample_world(points[N,3]) is the hot path used by the CPU medium reference and by pyro export. Sampling arithmetic in float64 index space, values float32 widened to f64 (match Rust).

* Python files: scenerender/cine/__init__.py (new package); scenerender/cine/volume/__init__.py (new); scenerender/cine/volume/grid.py (new: Transform, SparseGrid, BRICK constants, errors); scenerender/cine/volume/srvol.py (new: Volume, CacheLimits, read/write)
* Tests: tests/test_cine_srvol.py (new); tests/test_cine_grid.py (new)
* Rust reference: crates/sr-volume/src/lib.rs:52-90 (Transform), 118-230 (SparseGrid::new/set/value/sample_index/sample_world), 234-330 (Volume insert/grid/bytes/write/read); crates/sr-volume/tests/cache.rs (6 tests), crates/sr-volume/tests/grid.rs (4 tests)
* **Acceptance:** 
  * port `crates/sr-volume/tests/cache.rs::cache_bytes_are_deterministic_and_roundtrip_fields`: write(read(x)) is byte-identical; fields survive
  * port `crates/sr-volume/tests/cache.rs::independent_wire_fixture_checks_layout_and_hostile_payloads`: hand-built wire bytes parse; hostile counts rejected before allocation
  * port `crates/sr-volume/tests/cache.rs::cache_rejects_truncation_trailing_bytes_version_and_resource_exhaustion`: every truncation prefix, a trailing byte, version 2, budget exhaustion all error
  * port `crates/sr-volume/tests/grid.rs::sampling_interpolates_across_bricks_and_affine_world_transform`: trilinear across brick boundary and under a rotated/reflected affine
  * port `crates/sr-volume/tests/grid.rs::sparse_negative_coordinates_and_pruning_preserve_background`: negative voxel indices use floor division; background-only bricks pruned
  * port `crates/sr-volume/tests/grid.rs::transforms_reject_nonfinite_singular_and_projective_matrices`: Transform::new validation
  * non-pixel: Byte-level: Python reads every SRVOL emitted by Rust bake-volume (harness 3) and re-writes it; sha256 of the re-serialised bytes must equal the Rust file. Rust reads a Python-written SRVOL via `scene-render validate` of a scene that references it (fixture check in P4-CINE-00).
* Risks: f64 index-space sampling must be done in float64 or sparse-boundary samples drift 1e-7 from Rust; pure-numpy brick lookup must be vectorised; a Python loop per sample is 100x too slow for the medium reference

#### P4-CINE-02: <volume> asset element: model, channels, integrity, dependency discovery, ext/format contract

Rows: CINE-01 · size M · track P4-C1 · group G1 (after BOB-PHASE1) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-01

Register the 1.3 <volume> asset under <assets>: src (volumeSourceType = anyURI or %d/%0Nd/# pattern), sha256, format srvol (default) | srvseq | openvdb (no extension guessing; unknown format error), densityGrid (default 'density'), temperatureGrid, velocityGridX/Y/Z, first/last (i32), fps, interpolation hold(default)|linear|advect, missingFrame error(default)|hold|transparent, boundsMinX/Y/Z + boundsMaxX/Y/Z. Channel names 1-64 chars of [A-Za-z0-9_.-]. Typed model dataclass VolumeAsset built from the lxml element (via BOB's model/diagnostics API), resolved relative to the document with sha256 integrity (mismatch = same error code/message class as images), discovered by asset verification (`validate`) and by incremental dependency discovery (watch/changed-only: a changed .srvol/.srvseq/.vdb or any frame of a pattern invalidates dependent frames). Python-side contract checks beyond Schematron: format/extension not guessed; per-format attribute prohibitions (srvseq prohibits first/last/fps/missingFrame and requires sha256 (VOL8); advect requires all three velocityGrid*; density background nonzero requires explicit bounds; temperatureGrid required when medium blackbody is set (checked at P4-CINE-09)). Register in registry.ASSETS ('volume', FULL once renderable) and declare ASSET_SIZES as (0,0) (volumes have no pixel size).

* Python files: scenerender/assets/volume.py (new: VolumeAsset model, resolve, verify, dependency discovery); scenerender/assets/__init__.py (touch: import volume); scenerender/registry.py (touch: ASSETS declare); scenerender/document.py / scenerender/references.py (touch: IDREF to volume for object3D/@volume; BOB owns the schema side)
* Tests: tests/test_cine_volume_asset.py (new)
* Rust reference: crates/sr-model (volume asset type; VOL1-VOL9 rules; tests/volume_assets.rs, tests/volume_rules.rs); crates/sr-eval/tests/volume.rs (asset resolution, dependency discovery); crates/sr-gpu/src/render_three.rs:823-860 (volume_cache: format dispatch srvol/openvdb, 256 MiB / 64-asset cache clear); schema/scene-render-1.1.xsd:1360 (volumeAssetType) and Schematron VOL1-9, V8
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/volume-asset-contract.scene.xml` (new): Valid volume asset with sha256, bounds and each format; then one scene per contract violation (bad channel name, unknown format, srvseq with first/last, missing sha256 on srvseq) Expect: valid scene: both renderers render the cloud (same coverage); violation scenes: Rust and Python give the same diagnostic code and message class
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/volume.scene.xml`: Rust corpus scene; needs ../media/uniform.srvol from gen_fixtures Expect: validates in both; renders after P4-CINE-10
  * port `crates/sr-model/tests/volume_assets.rs::(all tests)`: attribute contract, channel-name grammar, format enumeration, defaults
  * port `crates/sr-eval/tests/volume.rs::(asset/dependency tests)`: changed cache file or frame invalidates the frame; sha256 mismatch is an error
  * non-pixel: `scenerender validate` and `scene-render validate` emit the same diagnostics code set over tests/corpus/{valid,invalid}/vol*.scene.xml (the corpus gate is run formally in P4-CINE-41).
* Risks: Schema adoption lands separately: code here must only use the BOB model API, not edit the XSD; sha256/integrity path must reuse the existing image asset pipeline instead of forking it

#### P4-CINE-03: Numbered volume sequences: placeholder expansion, local-clock frame selection, hold/linear pairing, missingFrame

Rows: CINE-05 · size M · track P4-C1 · group G2 (after 01) · schema adoption: no · depends on: P4-CINE-01

Mirror sr_volume::sequence. src %d / %0Nd / # runs (<=64 digits) expanded BEFORE URI decoding; first/last inclusive i32 labels, <=1,000,000 frames; time = object local time after retiming (animationSpeed/Offset etc. supplied by caller), position clamped at both ends; hold -> floor frame; linear -> enclosing pair, and at an exact sample loads only one frame; missingFrame error | hold (backward search inside [first,last], independent of seek history) | transparent (zero fields ONLY for an absent file; a corrupt file or missing selected channel always errors); equal endpoint references are shared (one load); frame-pair cache bounded (256 MiB frame cache, pair budget); load_timed returns, with each frame, its actual label and the motion interval so advection can use held-frame labels without precision loss at large labels. Reuse the placeholder logic of assets/image.py frame_path only as a starting point; Rust semantics differ (i32 labels, 64-digit limit, expansion before URI decode).

* Python files: scenerender/cine/volume/sequence.py (new)
* Tests: tests/test_cine_volume_sequence.py (new)
* Rust reference: crates/sr-volume/src/sequence.rs:29-203 (Position, FramePair, TimedFramePair, Sequence::position/load/load_timed); crates/sr-volume/tests/sequence.rs (10 tests)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/volume-sequence.scene.xml` (new): Analytic cloud sequence uniform-0%d.srvol with hold, linear, a missing middle frame under each missingFrame mode and an object with animationSpeed 0.5 and offset 1 Expect: pixel region means MATCH (CPU transport is deterministic) after P4-CINE-10; before that the check is the frame-label table below
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/volume-sequence.scene.xml`: Rust corpus (needs generated media) Expect: validates; renders after P4-CINE-10
  * port `crates/sr-volume/tests/sequence.rs::missing_hold_is_a_predecessor_search_independent_of_seek_history`: hold fallback = nearest earlier present frame, same for any request order
  * port `crates/sr-volume/tests/sequence.rs::transparent_missing_frames_and_corrupt_frames_are_distinct`: transparent only for absent file
  * port `crates/sr-volume/tests/sequence.rs::sequence_blends_fields_in_world_space_with_different_transforms`: linear blend evaluated per endpoint in its own world space
  * port `crates/sr-volume/tests/sequence.rs::timed_frames_preserve_actual_held_labels_without_large_label_precision_loss`: labels near 2^31 stay exact
  * non-pixel: Table gate: for a probe scene `scene-render eval` does not expose frame labels, so the gate compares which SRVOL file each renderer opens: Rust via `strace -e openat -f` (or SR_TRACE if present) filtered to *.srvol, Python via a file-open recorder; sequences must match for every probe time.
* Risks: The only way to observe Rust frame selection is by file-open tracing or pixel output; keep a Python-side frame-label log (debug logger) so the two can be diffed

#### P4-CINE-04: Temporal field interpolation: world-space linear and motion-aware advect

Rows: CINE-06 · size M · track P4-C1 · group G3 (after 03) · schema adoption: yes · depends on: P4-CINE-01, P4-CINE-03

interpolation=linear: each endpoint frame evaluated in WORLD space (own grid transform), then blended. interpolation=advect: velocityGridX/Y/Z (asset-world vectors, scene units per second; the transform locates samples but never rotates/scales the vector values; the three components must share a transform, which may differ from density/temperature transform and resolution); frozen-velocity midpoint RK2 backtrace p_mid = p - h*v(p)/2, p_src = p - h*v(p_mid) applied to the enclosing endpoints (the later endpoint with negative h) before blending density and temperature (kelvin); inferred density bounds expand by the max endpoint travel per axis (peak |v| incl. background x |elapsed|), authored bounds clip; elapsed time and displacement finite checks; same-label or clamped endpoints freeze motion (no advection); velocity grids count toward cache budgets. Medium-side blend factor frame_blend. Provide a vectorised `Medium.sample_density/sample_temperature(points)` entry that P4-CINE-08 consumes.

* Python files: scenerender/cine/volume/advection.py (new); scenerender/cine/volume/blend.py (new: world-space pair blend + FramePair adapters)
* Tests: tests/test_cine_advection.py (new)
* Rust reference: crates/sr-volume/src/advection.rs:13-85 (Advection::new/backtrace); crates/sr-volume/src/medium.rs:220-300 (with_next_frame, with_advection, sample_field, frame_blend); crates/sr-volume/tests/advection.rs (4 tests); crates/sr-volume/tests/sequence.rs medium_interpolates_density_and_temperature_before_nonlinear_transport; tools/evidence/cinematic-impact.json volume_advection_validation
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/advected-volume.scene.xml` (new): Translating blob in advected-%d.srvol with density+temperature+velocityGrid{X,Y,Z}; frames 0,1; render at t=0.5 with interpolation hold, linear, advect; second object rotated and scaled Expect: advect shows ONE blob at the midpoint, linear shows two ghosts; region means MATCH vs Rust within G2
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/advected-volume.scene.xml`: Rust corpus Expect: validates (VOL9)
  * port `crates/sr-volume/tests/advection.rs::translating_density_and_temperature_meet_between_frames_without_ghosts`: advect aligns both endpoints at the midpoint (single peak) while linear ghosts
  * port `crates/sr-volume/tests/advection.rs::midpoint_trace_uses_spatial_velocity_and_asset_world_axes`: vector values not transformed by the grid affine
  * port `crates/sr-volume/tests/advection.rs::advected_support_expands_inferred_bounds_but_authored_bounds_clip`: bounds inference
  * port `crates/sr-volume/tests/advection.rs::advection_rejects_invalid_time_mismatched_components_and_overflow`: validation errors
  * non-pixel: Field-level: sample the Python advected density on a 32^3 point lattice and compare to the rendered-by-Rust field via a 1-sample strict render of a thin slab object (rel-L2 <= 1e-3 in the slab region) — the render of a 1-voxel-thick slab with extinction=1, albedo 0, emission=density gives the line integral which equals the density to the slab thickness.
* Risks: advect needs velocity in asset-world units; Python must not push velocity through the grid affine (the Rust test pins this)

#### P4-CINE-05: SRVSEQ frozen manifest: reader, deduplicating writer, composition-time playback

Rows: CINE-07 · size M · track P4-C1 · group G3 (after 03) · schema adoption: yes · depends on: P4-CINE-01, P4-CINE-03

Binary manifest 'SRVSEQ\r\n', u32 version 1, f64 start, f64 fps, u32 count (1-100,000), per sample SHA-256 (32 B) + u64 length (HEADER 32, ENTRY 40 bytes); absent sample = zero length + zero digest; frames named <hex digest>.srvol in the manifest directory; repeated digests deduplicated (must agree on length, conflicting lengths error); every decoded byte verified against its digest and length; interval [start, start+count/fps); a render time outside the interval errors; linear clamps to the last sample; integral times within <=1e-6 frame snapped; VOL8: with format=srvseq the scene must give the manifest sha256 and must not give first/last/fps/missingFrame; five native fields (density, temperature, velocityX/Y/Z) read as the channels of the same names; frame pair loading bounded (retained-manifest cap 8, 256 MiB frames). BakeWriter: transactional write (temp dir, atomic publish, remove owned partial output on abort or error, never replace an existing output), BakeLimits (frame count/bytes), receipt (frames, unique, bytes, manifest sha256).

* Python files: scenerender/cine/volume/srvseq.py (new: BakedSequence, BakeWriter, BakeReceipt)
* Tests: tests/test_cine_srvseq.py (new)
* Rust reference: crates/sr-volume/src/bake.rs:19-419 (MAGIC, BakedSequence::open/load_timed, BakeWriter::new/push/finish); crates/sr-volume/tests/bake.rs (6 tests); crates/sr-eval/tests/pyro_bake.rs; crates/sr-gpu/tests/volume.rs baked_pyro_matches_native_pixels_with_parent_and_source_retiming
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/baked-volume.scene.xml` (new): Rust-baked manifest (produced by the harness running scene-render bake-volume on the Rust pyro corpus scene, 12 frames) played back by both renderers with volume=srvseq asset, parent and object retiming Expect: region means MATCH; interval edges: t just outside range -> both report an error with the same diagnostic class
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/baked-volume.scene.xml`: Rust corpus Expect: validates
  * port `crates/sr-volume/tests/bake.rs::archive_round_trips_deduplicates_and_seeks_on_composition_time`: writer dedups identical frames; seek by composition time
  * port `crates/sr-volume/tests/bake.rs::failed_bakes_leave_no_published_archive_and_never_replace_existing_output`: transactional publish
  * port `crates/sr-volume/tests/bake.rs::malformed_manifests_and_frame_limits_fail_before_loading_payloads`: bounded manifest parse
  * port `crates/sr-volume/tests/bake.rs::partial_bakes_select_exact_samples_at_nonzero_start_and_fractional_fps`: snap tolerance 1e-6 frame
  * non-pixel: Byte gate: Python BakedSequence.open on a Rust-produced bake verifies all digests; Python BakeWriter fed the same frames produces a manifest with identical sha256 (manifest bytes are deterministic).
* Risks: f64 time snapping and 1e-6 frame tolerance are easy to get off by one at fractional fps

#### P4-CINE-06: OpenVDB reader part 1: archive header, metadata, transforms, tree topology, uncompressed/ZIP/half/active-mask, f32/f64/half scalar

Rows: CINE-04 · size L · track P4-C1 · group G1 (after 01; independent of BOB for the decoder itself) · schema adoption: yes · depends on: P4-CINE-01

Native parser (no OpenVDB runtime) for little-endian seekable .vdb archives, file versions 222-225, floating-point 5/4/3 trees. This part: archive header and UUID, per-file and per-grid metadata, grid descriptors with offsets and instance parents, transform maps (AffineMap, UnitaryMap, TranslationMap, ScaleMap, ScaleTranslateMap, UniformScale... converted to the SRVOL affine; reflection and shear preserved; unsupported maps e.g. frustum error), tree topology (root table, internal 5/4 nodes with child/tile masks, leaf 3), value types half, float, double (rounded to f32 with range rejection), active/inactive voxel and tile values, background, half-quantised storage, active-mask compression, ZIP (zlib) payloads, root and internal tile expansion into 8^3 bricks with admission BEFORE allocation (BRICK_CHARGE = 512*4+128 bytes/brick, WORKSPACE 4 MiB, root bookkeeping charged even when all tiles equal background), non-seekable archives, bad offsets, trailing bytes. Output: sr Volume (P4-CINE-01) with grids named by the vdb grid names; Blosc and vec3 splitting come in P4-CINE-07. Mirror the Rust error strings' classes.

* Python files: scenerender/cine/volume/openvdb.py (new: read(), Archive, Tree, Transform maps); scenerender/cine/volume/vdb_codec.py (new: ZIP/half/mask decoders; Blosc stub raising an explicit error until -07)
* Tests: tests/test_cine_openvdb.py (new); tests/fixtures/openvdb/ (new: copy of the 17 official-library .vdb files from crates/sr-volume/tests/data/openvdb, license permitting; otherwise referenced via env SR_RS_REPO)
* Rust reference: crates/sr-volume/src/openvdb.rs:18-405 (read, transform, decode, root, node, fill, insert); crates/sr-volume/src/openvdb/read.rs (94 lines, little-endian cursor); crates/sr-volume/tests/openvdb.rs (8 tests); tools/openvdb_fixture.cpp (fixture generator, development-only, official OpenVDB 10.0.1)
* **Acceptance:** 
  * port `crates/sr-volume/tests/openvdb.rs::official_scalar_files_preserve_active_and_inactive_values_across_codecs`: density-none/zip/half files decode to known active+inactive values
  * port `crates/sr-volume/tests/openvdb.rs::official_affine_background_and_grid_instances_preserve_field_coordinates`: affine-background.vdb, instanced.vdb
  * port `crates/sr-volume/tests/openvdb.rs::official_tiles_are_preserved_and_expansion_is_admitted_before_allocation`: tiles.vdb, large-tile.vdb: tile expansion budget
  * port `crates/sr-volume/tests/openvdb.rs::corrupt_or_unsupported_files_return_errors_without_panics`: unsupported-bool.vdb, truncations, bit flips
  * port `crates/sr-volume/tests/openvdb.rs::root_bookkeeping_is_charged_even_when_all_tiles_equal_background`: admission accounting
  * non-pixel: Golden gate against the official library: tools/openvdb_fixture.cpp (not built here; OpenVDB headers absent) produced the 17 checked-in files with known voxel values; Python must decode each to the same (index -> value) map as the Rust decoder. For files where Rust's test records expected values, assert exactly; for the rest, compare to the Rust decode via a Rust-rendered 1-voxel-slab probe (P4-CINE-00 oracle) at 8 sample points, rel error <= 1e-6. If an OpenVDB Python wheel (pyopenvdb/openvdb) is available in the dev environment it is used as an extra independent oracle but is never a runtime dependency.
* Risks: Largest from-scratch parser of the area; the tree format (RootNode/InternalNode child masks, value masks, compression flags per version 222-225) must be read from the VDB spec because the Rust reader is only 405+94+164 lines but encodes many subtle layout rules; float16 conversion: use numpy float16 -> float32 exactly (Rust half crate); verify round-to-nearest-even equality on a sweep

#### P4-CINE-07: OpenVDB reader part 2: Blosc codec, vec3 grids, double rounding, instanced grids, hostile-file budgets, format=openvdb asset path

Rows: CINE-04 · size M · track P4-C1 · group G3 (after 06) · schema adoption: yes · depends on: P4-CINE-02, P4-CINE-06

Finish CINE-04: Blosc-compressed payloads (c-blosc 1.x frame header: version, versionlz, flags, typesize, nbytes, blocksize, cbytes, block start table; compressors blosclz, lz4, lz4hc, snappy, zlib, zstd; shuffle/bitshuffle; memcpyed) -- DEPENDENCY DECISION: add optional dependency `blosc` (python-blosc, wheels exist for cp312 manylinux) under extras `volumes`; if absent, fall back to a pure-Python decoder for zlib/lz4/blosclz-only streams and raise the same 'OpenVDB Blosc' error class otherwise. Keep Rust's checks: header/decoded-size validate before output allocation (blosc_cbuffer_validate semantics: exact envelope, bounded output), 'exact envelope' fixture test (codec.rs:141). vec3s/vec3d grids exposed as <name>.x/.y/.z scalar channels (vector transform handling: values are not transformed), double grids rounded to f32 with range rejection, half grids, instanced grids (shared tree via instance parent; duplicate or cyclic instance error; instance payload cannot hide trailing bytes), grid class (fog/level set/staggered) read but unused. Wire format=openvdb into the volume asset loader (P4-CINE-02): selects densityGrid/temperatureGrid/velocityGridX/Y/Z by name incl. '.x' style names, errors on missing channel (no silent zero).

* Python files: scenerender/cine/volume/vdb_codec.py (touch: Blosc); scenerender/cine/volume/openvdb.py (touch: vec3, instances); pyproject.toml (touch: optional extra `volumes` = blosc>=1.11); tests/fixtures/openvdb/ (the remaining .vdb files)
* Tests: tests/test_cine_openvdb_codecs.py (new)
* Rust reference: crates/sr-volume/src/openvdb/codec.rs:100-164 (Blosc validate + decompress, ZLIB; test blosc_blocks_require_exact_envelope_and_bounded_output at :141); crates/sr-volume/src/openvdb.rs:27-100 (components, from_name for vec grids), 253-405; crates/sr-volume/tests/openvdb.rs (vector, double, instance tests); tools/evidence/cinematic-impact.json openvdb_validation; crates/sr-gpu/tests/volume.rs openvdb_sequence_renders_thermal_fields_and_replays_local_time, openvdb_sparse_pixels_match_independent_grid_and_cache_respects_format
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/openvdb-sequence.scene.xml` (new): render-0.vdb / render-1.vdb (the Rust-test files) as a 2-frame openvdb sequence with density + temperature, blackbody medium; plus vector.vdb velocity channels for advect Expect: region means MATCH Rust (deterministic transport) after P4-CINE-10
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/openvdb.scene.xml`: Rust corpus; src impact-0.vdb -> substitute render-0.vdb Expect: validates in both
  * port `crates/sr-volume/tests/openvdb.rs::official_vector_grids_split_into_named_scalar_components`: vector.vdb -> channels name.x/.y/.z
  * port `crates/sr-volume/tests/openvdb.rs::official_dense_compression_and_double_fields_match_reference_values`: dense-blosc, dense-zip, double.vdb
  * port `crates/sr-volume/tests/openvdb.rs::instance_payload_cannot_hide_trailing_bytes`: instance trailing-byte error
  * port `crates/sr-volume/src/openvdb/codec.rs::blosc_blocks_require_exact_envelope_and_bounded_output`: Blosc envelope
  * non-pixel: All 17 fixture files decode identically (same index->value map, same channels) to Rust's decode, checked through the Rust-rendered thin-slab oracle at 8 points per grid with rel error <= 1e-6; the file list is the acceptance table.
* Risks: python-blosc availability on the target platform (wheel for cp312 manylinux exists; musl does not) -> pure fallback only handles zlib/lz4/blosclz; A new optional dependency needs a pyproject extra and a docs/RUNTIME.md note

#### P4-CINE-08: Medium CPU reference: Optical/Medium/Bounds, midpoint ray integration, Henyey-Greenstein, blackbody table

Rows: CINE-10, CINE-11 · size M · track P4-C2 · group G1 (after 01) · schema adoption: no · depends on: P4-CINE-01

Pure-numpy float64 mirror of sr_volume::medium and ::thermal; it is the oracle for every GPU/GL gate and the engine for CPU-only fallbacks. Optical (densityScale, extinction, albedo RGB 0-1, anisotropy [-0.99,0.99], emissionColor, emissionScale, blackbody, temperatureScale>0) validated before transport; Bounds (authored or inferred from density support, expanded for interpolation travel); Ray(origin,direction,distance) and `integrate()`: split at every domain entry/exit, midpoint sampling with step <= stepSize and at most maxSteps (insufficient budget is an error, never a dropped tail), analytic Beer-Lambert attenuation per step, constant-coefficient emission per step with the series weight ds*(1-od/2+od^2/6) for od<=1e-3 and (1-exp(-od))/sigma otherwise, overlapping media add extinction and sources (order independent), opaque-surface limit clips the integral, heterogeneous density by trilinear SparseGrid sampling, extinction in WORLD distance including non-uniform object scale, density outside domain = 0, phase_hg(cos,g)=(1-g^2)/(4*pi*(1+g^2-2g*cos)^1.5), single-scatter albedo normalisation. Thermal: Planck B(lambda,T) integrated 360-830 nm at 5 nm, trapezoid; CIE 1931 via the Wyman-Sloan-Shirley 2013 multi-lobe fit (the exact lobe constants at thermal.rs:47-56; DO NOT reuse scenerender/colorimetry.py:_blackbody_xyz which integrates the tabulated CSV and differs in colour); XYZ normalised by Y(6500 K); 0 K emits 0; XYZ->linear sRGB, negatives clipped, then working primaries; source = emissionScale*(emissionColor+thermalRGB); kelvin sampled in the temperature grid's OWN transform and interpolated BEFORE scaling by temperatureScale; scaled values must be in 0..50,000 K (checked even when emissionScale=0); table T_i=50000*(i/1024)^2, 1025 entries, interpolation coordinate sqrt(T/50000)*1024 (what the GL shader will use); bounded table must track direct integration.

* Python files: scenerender/cine/render/__init__.py (new); scenerender/cine/render/medium.py (new: Optical, Medium, Bounds, Ray, integrate, phase_hg); scenerender/cine/render/thermal.py (new: planck, cie_wssh, blackbody_rgb, blackbody_table)
* Tests: tests/test_cine_medium.py (new); tests/test_cine_thermal.py (new)
* Rust reference: crates/sr-volume/src/medium.rs:17-477 (Bounds, Optical, Medium, Ray, March, phase_hg, integrate); crates/sr-volume/src/thermal.rs:1-96 (spectral_radiance, cie, xyz, blackbody_rgb, blackbody_table, TABLE_INTERVALS); crates/sr-volume/tests/medium.rs (8 tests), crates/sr-volume/tests/thermal.rs (5 tests)
* **Acceptance:** 
  * port `crates/sr-volume/tests/medium.rs::homogeneous_slab_matches_beer_lambert_and_emission_integral`: T=exp(-sigma d); L = S/sigma (1-T) for emission
  * port `crates/sr-volume/tests/medium.rs::optically_thick_slabs_preserve_small_nonzero_transmittance`: no underflow-to-zero shortcut
  * port `crates/sr-volume/tests/medium.rs::overlapping_media_combine_coefficients_independently_of_insertion_order`: order independence
  * port `crates/sr-volume/tests/medium.rs::transformed_density_uses_world_distance_and_sparse_support`: non-uniform scale
  * port `crates/sr-volume/tests/medium.rs::bounded_marching_rejects_insufficient_budget_instead_of_dropping_tail`: maxSteps error
  * port `crates/sr-volume/tests/medium.rs::single_scattering_has_correct_albedo_and_phase_normalization`: HG integrates to 1 over the sphere
  * port `crates/sr-volume/tests/thermal.rs::bounded_thermal_table_tracks_direct_spectral_integration`: 1025-entry table vs direct
  * port `crates/sr-volume/tests/thermal.rs::visible_blackbody_is_dark_when_cold_and_preserves_brightness_growth`: monotone brightness
  * non-pixel: Cross-implementation constant check: blackbody_rgb(T) for T in {1000,1500,2000,3000,4000,6500,10000,50000} equals Rust's value; Rust exposes it only through rendering, so the gate renders a vacuum emission slab (density 1, extinction 0, emissionColor black, blackbody, temperature uniform T) with Rust and compares the RGB pixel of the unlit centre to Python blackbody_rgb(T)*emissionScale*ds; rel err <= 1e-3 (f32 GPU + tonemap-free linear read of an EXR/16-bit PNG).
* Risks: Reusing Python's existing colorimetry blackbody would make every fire render the wrong colour (CINE-11 is PARTIAL for exactly this reason); Vectorised march must keep per-ray step counts independent of the lane (use masked loop over max steps)

#### P4-CINE-09: Volumetric object: object3D primitive="volume", @volume IDREF, <medium> child, bounds, animation and seek equivalence

Rows: CINE-09 · size M · track P4-C2 · group G4 (after 02,04,08) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-02, P4-CINE-04, P4-CINE-08

object3D primitive="volume" with @volume (IDREF to assets/volume) OR exactly one owned <pyro> child (VOL1; ambiguity rejected), reusing object transform, parent, visibility, start/end, opacity, motion-blur shutter; owned <medium> child (VOL3, at most one): densityScale, extinction, albedo (RGB 0-1), anisotropy [-0.99,0.99], emissionColor, emissionScale, blackbody, temperatureScale (>0), stepSize, maxSteps (1-65536, default 2048; see rust_may_be_wrong for the GPU 1,048,576 check). Extinction integrates over WORLD distance including non-uniform scale; opacity multiplies density; all medium parameters animatable (animate/expression/link) with forward/backward seek equivalence (pure function of t); bounds (authored boundsMin/Max or inferred) must include interpolation support (advect travel); castShadow/receiveShadow flags honoured by the transport (P4-CINE-10/11). Frame-local errors: a volume that cannot be represented fails the frame with a diagnostic and never falls back to a surface-only draw. Build a VolumeDraw dataclass (Medium + March + object matrix + flags + thermal colour matrix) from scene evaluation in scenerender/three/scene.py `_object_items`/`build_object` hook (volume branch), unit conversion scene units; evaluate medium animated properties through the existing Ctx machinery.

* Python files: scenerender/cine/render/volume_object.py (new: build_volume_draw(rc, el, ctx)); scenerender/three/scene.py (touch: volume branch in _object_items/build_object; shared hub); scenerender/registry.py (touch: declare primitive volume, node child medium FULL)
* Tests: tests/test_cine_volume_object.py (new)
* Rust reference: crates/sr-model (object3D volume/medium, VOL1-9); crates/sr-eval/tests/volume.rs; crates/sr-gpu/src/volume.rs:25-130 (VolumeDraw::new validation: f32 invertibility of object/grid matrices, voxel coordinate exactness limit 2^20-1, advection precision); crates/sr-gpu/tests/volume.rs volume_document_loads_a_cache_and_respects_start_time, thermal_document_reads_temperature_and_animates_its_scale, volume_rejects_missing_selected_temperature_even_without_blackbody; crates/sr-gpu/tests/volume.rs gpu_volume_rejects_unrepresentable_data_and_excessive_work_before_upload
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/volume-medium-contract.scene.xml` (new): One cloud with animated densityScale/emissionScale/temperatureScale, parent scaling non-uniformly, opacity 0.5, start/end window, hidden window; and the negative set: both @volume and <pyro>, medium on a non-volume primitive, missing temperature with blackbody Expect: positive: coverage + region means MATCH after P4-CINE-10; negatives: identical diagnostics
  * port `crates/sr-gpu/tests/volume.rs::thermal_document_reads_temperature_and_animates_its_scale`: temperatureScale animation changes emission; backward seek identical
  * port `crates/sr-gpu/tests/volume.rs::volume_rejects_missing_selected_temperature_even_without_blackbody`: selected temperatureGrid absent from file is an error
  * port `crates/sr-gpu/tests/volume.rs::volume_document_loads_a_cache_and_respects_start_time`: start/end windows
  * non-pixel: Eval-level: the Python volume node record (object matrix, medium parameters, grid hashes) at t in {0,0.3,0.6,1.0} compared with the parameter values the Rust eval prints (`scene-render eval --format json` exposes the object3D node; medium fields compared where present).
* Risks: The Rust GPU check limit (max_steps 1,048,576) disagrees with the XSD limit 65536; mirror the XSD in validation and the Rust GPU behaviour is unobservable from valid documents

#### P4-CINE-10: GL volume transport: GLSL march library, brick-atlas upload, single scattering, shadows, standalone volume pass

Rows: CINE-10, CINE-11 · size L · track P4-C2 · group G5 (after 09,08) · schema adoption: no · depends on: P4-CINE-08, P4-CINE-09

GPU implementation of volume.wgsl in GLSL (moderngl, GL>=4.3 for SSBO, falls back to textures on 4.1): packed record per medium (matrix, inverse, optical, march, flags), brick data as one R32F 3D atlas texture of 8^3 bricks (+ an RG32I/R32I indirection 3D texture keyed by brick coordinate window, or a sorted key SSBO with binary search like volume_key_less) so trilinear sampling that crosses brick boundaries uses 8 texelFetch exactly like volume_voxel/volume_grid_sample; absent bricks return the grid background; density = grid trilinear * densityScale * opacity; advection: velocity sampled at the midpoint (volume_advected_point) for both endpoints then blended; emission = emissionScale*(emissionColor + thermal) with the 1025-entry blackbody table uploaded as a 1-D texture and interpolated in sqrt(T/50000)*1024; volume_transport = split at every entry/exit boundary, midpoint steps ds<=stepSize, series weight at small optical depth; overlapping media sum extinction and sources; single scattering MEDIUM_LIGHTING path (only when albedo>0): analytic lights via phase_hg*light_radiance*visibility (shadow rays through volume_transmittance and, when receiveShadow, surface visibility), authored ambient adds colour directly, dome light sampled with ONE uniform-sphere sample per step (4*pi*phase weighting) -> stochastic; media limit 64 domains, 128 MiB per volume file storage binding, 256 MiB frame cache, f32 precision checks raise frame errors (never a surface-only fallback). Deliver (a) the GLSL library as a string module `volume_glsl.py` that SIM2's path tracer includes, and (b) a standalone compute/fragment pass `render_volume_pass(fr, rays)` that renders a camera-ray march against the raster depth buffer so volume-only and volume+raster-surface scenes work before the path tracer lands; (b) is NOT what Rust does with surfaces present (Rust forces the path tracer, three.rs:1419-1423) so scenes mixing volumes with lit surfaces are only gated for parity after P4-CINE-11.

* Python files: scenerender/cine/render/volume_glsl.py (new: GLSL source assembled from fragments); scenerender/cine/render/volume_gl.py (new: packing, atlas, pass); scenerender/cine/render/blackbody_table.py (new: GL table upload, uses thermal.py); scenerender/three/renderer.py (touch: call the volume pass after opaque 3D; shared hub); scenerender/three/scene.py (touch: attach VolumeDraw list to World3D; shared hub)
* Tests: tests/test_cine_volume_gl.py (new; skipped without GL)
* Rust reference: crates/sr-gpu/src/volume.rs:130-288 (record packing, brick rows RECORD_ROWS 64 / BRICK_ROWS 129, MAX_MEDIA 64); crates/sr-gpu/src/volume.wgsl:1-187 (volume_voxel/grid_sample/advected_point/density/emission/phase/transmittance/incident/transport); crates/sr-gpu/src/three.rs:1409-1423 (volumes force the path tracer; error on limit_note); crates/sr-gpu/tests/volume.rs (12 tests)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/volume-slabs.scene.xml` (new): Analytic homogeneous slabs: absorption-only, emission-only, albedo+analytic point/directional light, two overlapping media, non-uniform scale object; orthographic camera so path length is exact Expect: pixel value equals closed form (G1 tolerance 2e-3 relative in linear light) and region means match Rust (G2)
  * scene `tools/parity/scenes/cine/volume-cloud.scene.xml` (new): Heterogeneous cloud (SRVOL from gen_fixtures) in front of a lit 3D sphere, perspective camera, point light shadows on and off Expect: Rust-vs-Python PSNR CLOSE (>=30) if the surface is unlit/emissive; with lit surfaces gated only after P4-CINE-11
  * scene `tools/parity/scenes/cine/volume-thermal.scene.xml` (new): Temperature field + blackbody, animated temperatureScale Expect: region means within G2; colour (hue) error dE00<=3 at 8 sample points
  * port `crates/sr-gpu/tests/volume.rs::gpu_volume_transport_matches_analytic_slabs_and_overlapping_media`: GPU slab radiance equals Beer-Lambert/emission closed form incl. overlapping media
  * port `crates/sr-gpu/tests/volume.rs::gpu_volume_rejects_unrepresentable_data_and_excessive_work_before_upload`: f32 limits and step budgets are errors before upload
  * port `crates/sr-gpu/tests/volume.rs::openvdb_sparse_pixels_match_independent_grid_and_cache_respects_format`: sparse grid pixels equal an independent dense evaluation
  * port `crates/sr-gpu/tests/volume.rs::advected_cache_documents_move_fields_replay_and_validate_channels`: advect/replay/channel validation
  * statistical: policy: Deterministic transport (emission/absorption, analytic lights, shadows) is NOT stochastic: gate G1/G2 with no seeds. Only the dome-light single-scatter term is stochastic (one uniform-sphere sample per march step, volume.wgsl:136-143): render K=8 seeds in Python, require per-region mean within 3*sigma/sqrt(K) of the Rust single render's 4-seed-smoothed mean, and the Python sample variance within a factor 2 of the Rust one; with denoise enabled compare after denoise using G2.; tolerances: G1: |GL - CPU float64 reference| <= 2e-3 relative (>=1e-4 absolute) per pixel on slabs, mean error <= 2e-4; G2 vs Rust: region-mean RGB <= 2% relative (>=1e-3 absolute linear); non-stochastic scenes PSNR >= 45 MATCH goal, >= 35 required; seeds: deterministic scenes: none; dome scattering: seed salt 0..7 (harness)
  * non-pixel: CPU-reference parity: the GL image must equal medium.integrate rendered per pixel on a 64x64 test (all geometry analytic).
* Risks: Brick indirection on GL 4.1 without SSBO needs a texture-based key search; llvmpipe GL limits (GL_MAX_3D_TEXTURE_SIZE, GL_MAX_SHADER_STORAGE_BLOCK_SIZE) must be probed at runtime and reported as frame errors, not clamped; Fragment loops with 2048 steps are slow on llvmpipe: budget the gate scenes at <=160x120; f32 GLSL vs f64 Rust WGSL-f32: Rust itself is f32 on GPU so f32 vs f32; keep step arithmetic ordering identical to volume.wgsl

#### P4-CINE-11: Volumes inside the path tracer: forced path-trace mode, medium transport per bounce, shadows through media, cameras inside media

Rows: CINE-10 · size L · track P4-C2 · group G6 (after SIM2 path tracer entry/BVH/NEE land) · schema adoption: no · depends on: P4-CINE-10, P4-SIM2-21, P4-SIM2-22, P4-SIM2-23, P4-SIM2-25, P4-SIM2-28

Mirror Rust's rule that ANY scene containing a volume renders its 3D pass through the path tracer (three.rs:1419: default PathOpts samples 4, bounces 2, denoise true when camera renderer is not pathtrace; an explicit camera renderer=pathtrace with pathSamples/maxBounces/denoise wins; limit_note failure is a frame error for volume scenes, a raster fallback otherwise). Include volume_glsl.py (P4-CINE-10) into SIM2's compute path tracer: at each segment (origin, direction, hit distance) `volume_transport` adds throughput*emission/scatter to colour and multiplies throughput by transmittance; first-segment alpha accumulates (1 - transmittance) when no surface was met; HAS_MEDIA / MEDIUM_LIGHTING specialisation constants; shadow rays for surface lighting multiply by volume_transmittance when light passes through a medium (pathtrace.wgsl:601) so transparent surfaces and medium self-shadowing respect transmittance; the camera may be inside a medium (ray origin inside domain: interval starts at 0); overlapping domains order-independent; opaque geometry bounds the march (hit.t). Volume AABBs/domains enter the same scene build as SIM2-30 (flattening) but not the BVH. Particles and ocean do not need this package.

* Python files: scenerender/cine/render/volume_pt.py (new: glue + specialisation constants + volume uniform packing for the path tracer); scenerender/three/pathtrace.py (SIM2 owns; touch: call hooks for HAS_MEDIA, media records) - shared hub; scenerender/three/scene.py (touch: force path mode when volumes present)
* Tests: tests/test_cine_volume_pt.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.wgsl:5,29,474-480,601 (HAS_MEDIA, MEDIUM_LIGHTING, volume_transport hook, shadow visibility); crates/sr-gpu/src/three.rs:1409-1460 (forced path tracing; limit_note error); crates/sr-gpu/src/pathtrace.rs:101-165 (notes, fits, limit_note); crates/sr-gpu/tests/volume.rs (12 tests; those with surfaces and shadows)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/volume-lit-surface.scene.xml` (new): Cloud in front of a lit floor and sphere, point + dome light, castShadow/receiveShadow combinations, camera outside; then the same with the camera inside the medium Expect: statistical gates below
  * scene `tools/parity/scenes/cine/volume-glass.scene.xml` (new): Cloud behind a glass pane (transmission) and fog slab between camera and a surface Expect: statistical gates below
  * scene `tools/parity/scenes/cine/volume-furnace.scene.xml` (new): Closed uniform sphere medium, albedo=1, extinction>0, uniform dome radiance 1 (white furnace) Expect: radiance == 1 everywhere inside the sphere silhouette
  * port `crates/sr-gpu/tests/volume.rs::native_pyro_renders_and_invalidates_isolated_volume_content`: volume content redraws inside cached isolated groups when the field changes
  * port `crates/sr-gpu/tests/volume.rs::native_pyro_collider_carves_the_rendered_volume`: collider carves smoke in the final image
  * statistical: policy: Path-traced output is stochastic only through (a) dome sampling in volume_incident, (b) BSDF/NEE sampling, (c) lens DoF. Tests render at fixed spp N in {16,64,256,1024} with K=8 seed salts. Gate S1 (distribution): for each of 16 regions, |mean_py - mean_rs| <= max(2%, 3*sqrt(var_py/K+var_rs/Krs)) where Rust is rendered once (bit-identical re-render) at 1024 spp as the reference mean and at the default 4 spp for the variance comparison; Python per-region sample variance within [0.5,2]x Rust's at equal spp. Gate S2 (convergence): log-log slope of RMS error to the 1024-spp Python mean vs N is in [-0.65,-0.35] (Monte-Carlo 1/sqrt(N)). Gate S3 (energy conservation, white furnace): mean radiance in the sphere interior within 1% of 1.0 at 256 spp for albedo=1; with albedo 0.5 within 1% of the analytic first-order series for the single-scatter slab. Gate S4 (denoised default 4 spp,2 bounces): luma histogram EMD (64 bins) <= 0.02 and PSNR vs Rust >= 28 dB on the smooth cloud (CLOSE class).; tolerances: S1 2%/3sigma; S2 slope [-0.65,-0.35]; S3 1%; S4 EMD<=0.02; seeds: seed salt = global pixel hash XOR salt, salts 0..7; Python path tracer reuses the pcg()/pixel+sample hash of pathtrace.wgsl:149-160 so a salt-0 render is deterministic run to run
  * non-pixel: Energy/limit test: transmittance of a homogeneous slab estimated by the path tracer's alpha channel equals exp(-sigma d) within 0.5% at 64 spp.
* Risks: Hard cross-area dependency: nothing here can be accepted until SIM2-29/30/31/33/37 exist; schedule so SIM2 delivers a path tracer with a shader-include seam (HAS_MEDIA specialisation) early; Stochastic gates need many renders of a software-GL path tracer: budget scenes at 160x120, K=8, N<=1024 (llvmpipe: minutes)

#### P4-CINE-12: UHD tiled path tracing on bounded bindings and the 3840x2160 strict probe

Rows: CINE-36 · size M · track P4-C2 · group G7 (after SIM2-36/37 and CINE-11) · schema adoption: no · depends on: P4-CINE-11, P4-SIM2-27, P4-SIM2-28

UHD behaviour on top of SIM2-37 (determinism/tiled execution) and SIM2-36 (a-trous denoiser): internal output tiles with GLOBAL pixel/sample seeds and the original camera projection; denoise halo = 2*(2^5-1) = 62 px around every core (DENOISE_HALO = 2*(2^DENOISE_PASSES-1)) so tile edges are never image edges; tile buffer budget default 32 MiB at 32 B/px (DEFAULT_TILE_BYTES), minimum 1,131,008 bytes (64x64 core + halo), clamped by the device maximum storage-buffer block size (GL_MAX_SHADER_STORAGE_BLOCK_SIZE, query at runtime); working buffers REUSED across tiles so peak image memory is independent of frame area (UHD guides at 32 B/px would otherwise need 265,420,800 B in one binding); u32 pixel-index and dispatch-count limits checked (error, no wraparound); whole-frame vs tiled equivalence is exact (bit-identical in Python with the same seeds); tile planner `tiles(size, bytes, denoise)` ported line for line (pathtrace.rs:601-625). Includes the strict 3840x2160 single-feature probes Rust has recorded: static cloud 1 spp, ocean 1 spp (7.5 s warm frame and 1.2 GB RSS on llvmpipe are Rust's numbers, recorded as reference not as pass criteria). If SIM2-37 already implements tiling with budgets this package reduces to the UHD gates and the 128 MiB clamp; coordinate before starting.

* Python files: scenerender/three/pathtrace_tiles.py (new or SIM2-owned; confirm owner before starting); scenerender/cine/render/uhd.py (new: memory/limit probes, planner tests, probe runners); tools/parity/cine_uhd_probe.py (new)
* Tests: tests/test_cine_uhd_tiles.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.rs:582-640 (PER_DISPATCH 4, DENOISE_PASSES 5, DENOISE_HALO, MIN_TILE_BYTES, DEFAULT_TILE_BYTES, tiles()); crates/sr-gpu/src/pathtrace.rs:799-835 (limit_buffers, work-buffer reuse), 821-896 (render, passes); crates/sr-gpu/tests/pathtrace_tiles.rs (2 tests); tools/evidence/cinematic-impact.json requirements.tiles (status verified), visual_probe.uhd, ocean_uhd_probe
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/uhd-cloud.scene.xml` (new): Static cloud, 3840x2160, 1 spp, strict mode (no fallback) Expect: renders; peak RSS and wall time recorded; whole==tiled test on a 192x128 crop with tile budget forced to MIN_TILE_BYTES
  * scene `tools/parity/scenes/cine/uhd-ocean.scene.xml` (new): Ocean 1 spp UHD functional frame (after P4-CINE-27) Expect: renders in strict mode
  * port `crates/sr-gpu/tests/pathtrace_tiles.rs::tiled_paths_match_whole_frame_at_edges_and_denoise_seams`: tiled render with 1,131,008-byte tiles is pixel-identical to the whole-frame render, including tile seams with denoise
  * port `crates/sr-gpu/tests/pathtrace_tiles.rs::uhd_document_renders_pathtraced_without_fallback`: a UHD document renders path-traced in strict mode with no raster fallback
  * statistical: policy: Exactness, not statistics: tiled == whole bit-exact (same seeds). Memory gate: peak GPU+host allocation of a 3840x2160 frame at 1 spp must be <= (tile budget + fixed overhead) and must NOT scale with area: measure at 640x360, 1920x1080 and 3840x2160, require max/min of the peak buffer bytes <= 1.05.; tolerances: bit-exact tiled==whole; peak-bytes ratio <=1.05; seeds: fixed
  * non-pixel: Planner unit test: tiles(size,bytes,denoise) equals the Rust planner output for 20 (size,bytes) pairs generated by reading pathtrace.rs:601-625 (pure integer function, ported test table).
* Risks: Ownership conflict with SIM2-37; this package must be re-scoped if SIM2 already builds tiling; llvmpipe 3840x2160 path tracing needs >1 GB RAM and minutes

#### P4-CINE-13: Fixed-step seekable simulation framework: timeline, checkpoint thinning, budgets, atomic failure

Rows: CINE-34 · size M · track P4-C3 · group G0 (no deps) · schema adoption: no · depends on: none

Common machinery for pyro, particles3D, ocean, whitewater and 3D rigid. Extend (do not replace) physics.Seekable with scenerender/cine/sim/timeline.py: Timeline(start, dt, state) with at(t, step_fn): target = 0 for t<=start else floor((t-start)/dt + 1e-9) steps, restore from the latest checkpoint <= target when seeking backward or farther than one checkpoint interval; a checkpoint every simulated second (every = round(1/dt)), thinned when copies*bytes exceed the checkpoint budget (BUDGET 256 MiB; thin: every*=2, keep k % every == 0, keep >2 checkpoints) BEFORE cloning; independent hard budgets: workspace/resident (max_bytes 256 MiB) vs checkpoint bytes (64 MiB for particles/ocean), per-seek work units (100,000,000) and step cap (10,000,000 for pyro; 1M steps note), forward/backward/fractional seeks give identical states, a failed request leaves the published frame, caches and clock unchanged (build the new state on a copy, publish after success); exhaustion raises a typed error (Limit/Invalid) instead of partial output. Provide the THREE rounding conventions Rust uses as named helpers so each simulator can mirror its own Rust one (see rust_may_be_wrong): timeline_step (+1e-9), pyro_fixed_step_index (|r-round(r)| <= 4*eps*max(r,1) -> round else floor), ocean_canonical_target (floor then correct by comparing target*dt with time), particles3d offset rounding (8*eps*max(|offset|,1), min with 1e-6). Local clocks: ancestor-clock inversion through the existing scenerender temporal clock machinery (tests/test_expression_clocks.py, test_simulation_clocks.py show current API); nonlinear parent clocks rebuild branch-dependent history; hidden/conditional nodes do not reset a simulation. Python's Seekable stays for 2D physics.

* Python files: scenerender/cine/sim/__init__.py (new); scenerender/cine/sim/timeline.py (new); scenerender/cine/sim/errors.py (new: Limit, Invalid, atomic()); scenerender/cine/sim/clocks.py (new: local->source time mapping for sim nodes)
* Tests: tests/test_cine_timeline.py (new)
* Rust reference: crates/sr-sim/src/timeline.rs:1-106 (Timeline, State, BUDGET, thin(), test seeking_replays_the_same_steps_and_thinning_keeps_the_budget); crates/sr-sim/tests/sim.rs any_order_of_times_gives_the_same_state; crates/sr-sim/src/pyro.rs:1035-1038 (fixed_step_index); crates/sr-sim/src/ocean.rs:208-262 (at); crates/sr-sim/src/particles3d.rs:256-306, 399-487 (at/advance); crates/sr-eval/src/sim3d.rs (clock mapping)
* **Acceptance:** 
  * port `crates/sr-sim/src/timeline.rs::seeking_replays_the_same_steps_and_thinning_keeps_the_budget`: forward and backward visiting orders give identical states; thinning keeps copies within BUDGET (start 0.5, dt 0.25, state 64 MiB)
  * port `crates/sr-sim/tests/sim.rs::any_order_of_times_gives_the_same_state`: random access order independence
  * non-pixel: Order-independence property test: for each simulator package, states at 12 times visited in 20 random orders are bit-identical (np.array_equal) to a forward run; wired in each simulator's test file via a shared fixture from this package.
* Risks: The three step-boundary conventions are inconsistent in Rust; an impulse or burst landing exactly on a boundary can fire one step differently per simulator. Mirror each, test each at the boundary

#### P4-CINE-14: Pyro solver core: density, temperature, MAC velocity, advection, buoyancy, vorticity, turbulence, CG pressure projection

Rows: CINE-12 · size L · track P4-C3 · group G3 (after 01 and 13) · schema adoption: yes · depends on: P4-CINE-01, P4-CINE-13

Mirror sr_sim::pyro::{Spec, State, Simulation::step} on a dense bounded 3D grid: cells[3] each = width/voxelSize in 2..1024 (integer-ness checked), origin, voxelSize, dt (default 1/60), boundary closed|open, ambient 300 K, dissipation (exponential density decay /s), cooling (exponential decay of excess kelvin /s), buoyancy (accel toward -y per excess kelvin), vorticity confinement (Fedkiw), turbulence (seeded, amplitude scene units/s^2, u64 seed ALL bits via the counter hash sr-sim rng.rs: SplitMix64 finaliser of seed^a*0x9E3779B97F4A7C15^b*0xC2B2AE3D27D4EB4F with unit()=(h>>11)/2^53), pressureIterations (default 200), pressureTolerance (RMS divergence 1e-6 /s), maxMemory (256 MiB, before allocation). Centred density/temperature, staggered MAC velocities (face arrays, sizes (nx+1,ny,nz) etc.). Step order (pyro.rs:610-690): voxelise obstacles (solid mask + moving-boundary velocities at low/high faces) -> zero density, ambient temperature in solids -> boundaries -> midpoint semi-Lagrangian advect with exponential dissipation/cooling -> sources (P4-CINE-15) -> impulses -> forces (buoyancy, vorticity, turbulence, spatial accelerations) -> boundaries -> matrix-free Jacobi-preconditioned CG pressure projection to the target divergence field (expansion) with the unconverged-projection ERROR (a sealed closed domain cannot sustain net expansion) -> validate_state (finite). Open vs closed boundaries; separate checkpoint and workspace budgets; state.volume() export of density, temperature, velocity components (cell centres) as an SRVOL Volume with a transform mapping index->scene (this is what rendering and bake-volume consume: velocity channels velocityX/Y/Z). Numerics: numpy float64, vectorised stencils (np.roll-free slicing), CG via scipy.sparse.linalg-free explicit loops over vectors with numpy dot (keep operation order stable); numba is a declared dependency but NOT installed in the dev venv at /home/admin/codebases/py-render/.venv (verified missing) so do not rely on it; grids up to 64^3 must step in < 1 s (numpy), 128^3 documented as slow.

* Python files: scenerender/cine/sim/pyro.py (new: Spec, State, Simulation, project, advect, forces); scenerender/cine/sim/rng.py (new: counter hash, unit, signed; shared with particles/ocean/fracture)
* Tests: tests/test_cine_pyro.py (new)
* Rust reference: crates/sr-sim/src/pyro.rs:36-123 (Spec/defaults), 349-533 (State, trace, volume export), 540-692 (Simulation::new/step), 693-1066 (validate_state, boundaries, advect, forces, project); crates/sr-sim/tests/pyro.rs (16 tests); crates/sr-sim/src/rng.rs:1-30; tools/evidence/cinematic-impact.json pyro_validation (223 CPU tests)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/pyro-plume.scene.xml` (new): Rust corpus pyro.scene.xml scaled to 24x24x24 cells: sphere source start 0.15 end 0.35 plus a box impulse at t=0.5 with temperature 3000, buoyancy, vorticity, seeded turbulence variant Expect: bake oracle: Python volume frames vs Rust bake-volume frames at every frame (G1); rendering gated after P4-CINE-10
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/pyro.scene.xml`: Rust corpus scene (8^3, dt 0.1) Expect: bake-volume of this scene gives the smallest field oracle
  * port `crates/sr-sim/tests/pyro.rs::projection_reduces_three_dimensional_divergence_and_preserves_walls`: post-projection RMS divergence <= pressure_tolerance; wall-normal velocity zero
  * port `crates/sr-sim/tests/pyro.rs::buoyancy_moves_hot_smoke_up_and_seeded_turbulence_is_repeatable`: hot blob rises; same seed -> identical; different seed -> different
  * port `crates/sr-sim/tests/pyro.rs::open_domain_expansion_is_projected_to_the_authored_divergence`: target divergence reached in open domain
  * port `crates/sr-sim/tests/pyro.rs::obstacle_wall_prevents_smoke_crossing_and_resets_covered_cells`: solid mask zeroes density
  * port `crates/sr-sim/tests/pyro.rs::volume_export_preserves_cell_centres_temperature_and_all_velocity_components`: export transform and channels
  * port `crates/sr-sim/tests/pyro.rs::invalid_inputs_and_exhausted_budgets_fail_before_changing_state`: atomic failure
  * port `crates/sr-sim/tests/pyro.rs::checkpointed_seeking_matches_fresh_replay_with_a_tight_memory_budget`: checkpoint budget
  * statistical: policy: Not stochastic (seeded, deterministic) but not bit-comparable to Rust: CG with a residual stop (tolerance 1e-6) can stop one iteration earlier/later under different float summation order, so the Python field differs from Rust at roughly tolerance level. Tier A (no turbulence, 8^3 and 24^3, 24 frames): relative L2 error of density, temperature and each velocity component against Rust bake-volume frames <= 1e-6 at frame 1 and <= 2e-4 at frame 24. Tier B (seeded turbulence): same bound <= 5e-4 (the seeded force is a pure function; only projection differs). Integral statistics at every frame: total mass sum(rho)h^3 within 1e-6 relative (conservation modulo dissipation), centroid within 0.05 cell, kinetic energy within 0.5%, max|div| <= pressure_tolerance. If Tier A/B fail while integral statistics pass, the documented-convention fallback target is the integral gate only and the divergence is recorded in the package report.; tolerances: rel-L2 1e-6 (frame 1), 2e-4 (frame 24 tier A), 5e-4 (tier B); mass 1e-6; centroid 0.05 cell; KE 0.5%; seeds: turbulence seed = scene seed, all 64 bits; counter RNG replicated exactly so the force field is identical
  * non-pixel: Invariants: backward seek bit-identical to forward replay (Python-vs-Python); pressure projection leaves max|div| <= tolerance (checked each step in debug).
* Risks: Largest numeric kernel of the area; float64 numpy stencils at 64^3x240 steps are slow; CG iteration-count divergence from Rust (see statistical); do not chase bit parity; semi-Lagrangian trace uses trilinear interpolation on staggered grids: face-array index conventions must match pyro.rs:341 face_dims exactly or buoyancy sign errors appear

#### P4-CINE-15: Pyro sources and impulses: <pyroSource>, <pyroImpulse>, shapes, windows, animated rates

Rows: CINE-13 · size M · track P4-C3 · group G4 (after 14) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-14

Schema 1.3 pyroShape attribute group on pyroSource/pyroImpulse: shape sphere|box|mesh (mesh requires closed mesh, see CINE-16), local transform (position, rotation, rotationX/Y, scaleX/Y/Z nonzero), radius/width/height/depth. pyroSource: start/end windows (end>start) integrated over the overlap with each fixed substep (overlap = max(0, min(end_step, source.end) - max(start_step, source.start)); amounts = rate*overlap), densityRate/temperatureRate (>=0), signed velocityRateX/Y/Z, expansion (target divergence 1/s, scaled overlap/dt); all animatable, evaluated at EACH fixed step's source time (the evaluator supplies an Inputs struct per step); shape/mesh static. pyroImpulse: one-shot density/temperature/velocity totals and integrated expansion at the exact time; static (no animation children); events whose ratio time/dt lies on a step boundary belong to the FOLLOWING interval (fixed_step_index with 4*eps tolerance) and decimal rounding is corrected (0.85/0.05 trap). Totals are conserved: injected quantity = rate*overlap spread over covered cells (inject() semantics: pyro.rs:~1040-1066 -- read it for normalisation by covered-cell count/volume). Conditions omitting the owning object do not reset the simulation (P4-CINE-18 supplies windows).

* Python files: scenerender/cine/sim/pyro_sources.py (new: Source, Impulse, Shape, inject, coverage); scenerender/cine/sim/pyro.py (touch: call sources in step; owned by CINE-14, sequence after it); scenerender/cine/sim/pyro_inputs.py (new: evaluator -> Inputs adapter, animated-property sampling per substep)
* Tests: tests/test_cine_pyro_sources.py (new)
* Rust reference: crates/sr-sim/src/pyro.rs:200-330 (Source, Impulse, Inputs::validate), 640-690 (source/impulse loops), 1035-1066 (fixed_step_index, inject); crates/sr-eval/src/pyro.rs:1-319 (evaluator inputs, animated sampling); crates/sr-sim/tests/pyro.rs source_windows_integrate_partial_steps_and_cooling_is_exponential, impulses_fire_once_at_the_half_open_step_boundary_and_replay_identically, transformed_source_uses_local_shape_and_spatial_forces_act_in_three_dimensions; crates/sr-eval/tests/pyro.rs; crates/sr-model/tests/pyro_rules.rs (PYRO1-8)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/pyro-sources.scene.xml` (new): Two sources with overlapping windows partially inside a step, an impulse exactly on a step boundary (t = 0.5 with dt 1/60 is not on one; use dt 0.1 and t 0.5), a rotated/scaled box source, animated densityRate Expect: bake oracle frame-by-frame (G1); impulse fires in the same frame as Rust
  * port `crates/sr-sim/tests/pyro.rs::source_windows_integrate_partial_steps_and_cooling_is_exponential`: partial overlap integration
  * port `crates/sr-sim/tests/pyro.rs::impulses_fire_once_at_the_half_open_step_boundary_and_replay_identically`: half-open boundary and replay
  * port `crates/sr-sim/tests/pyro.rs::transformed_source_uses_local_shape_and_spatial_forces_act_in_three_dimensions`: local transforms
  * non-pixel: Same bake oracle and tolerances as P4-CINE-14 (G1) on the source scenes; impulse step index equality asserted by comparing the first frame where Rust's and Python's total mass jumps.
* Risks: The 0.85/0.05 float trap: use the pyro-specific convention, not the timeline one

#### P4-CINE-16: Closed-mesh regions: rest-pose import, welding, orientation checks, bounded BVH inside test, mesh budgets

Rows: CINE-16 · size M · track P4-C3 · group G0 (no deps; used by 15,19,22,33) · schema adoption: no · depends on: none

Shared geometry service used by pyro sources/colliders, particle colliders and fracture. Imported meshes (via scenerender.three.loaders.load_model, glTF/OBJ/PLY/USD/FBX) use the REST pose with scene-axis conversion incl. node transforms and reflected winding (determinant sign flips winding); exact coincident positions welded across normal/UV seams (weld by exact float equality, then canonical re-index); open, non-manifold, degenerate (zero-area) and inconsistently oriented surfaces rejected with distinct errors; a globally reversed winding is normalised (negative signed volume -> flip), oppositely oriented cavities (nested shells) retained; unused vertices ignored for bounds/area/numerical scale; inside test = nearest oriented surface using a bounded BVH (numpy-built AABB tree with iterative vectorised closest-point-on-triangle; pseudo-normal sign) -- must give the same result as brute force; meshMemoryMiB budget (default 128) charged BEFORE BVH creation (Mesh::required_bytes); Mesh::moving (two poses, midpoint region) and boundary_velocity (barycentric surface velocity) stubs defined here, completed in CINE-19/38; included documents resolve their own mesh assets. Geometric self-intersection detection is NOT implemented in Rust either (see matrix note): do not add it (mirror) except where fracture (CINE-28) requires its own check. Pure-numpy; scipy.spatial.cKDTree on triangle centroids may prune candidates but the final decision must be exact.

* Python files: scenerender/cine/geo/__init__.py (new); scenerender/cine/geo/closed_mesh.py (new: Mesh, weld, orient, bvh, contains, nearest, bytes); scenerender/cine/geo/bvh.py (new)
* Tests: tests/test_cine_closed_mesh.py (new)
* Rust reference: crates/sr-sim/src/pyro/mesh.rs:17-260 (Mesh::new, moving, build, bytes, boundary_velocity, contains); crates/sr-sim/tests/pyro_mesh.rs (7 tests); tools/evidence/cinematic-impact.json pyro_mesh_fields_validation; crates/sr-sim/src/particles3d/mesh.rs (shared geometry validation for particle emitters/colliders)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/pyro-mesh.scene.xml` (new): Rust corpus pyro-mesh.scene.xml shape: closed OBJ torus source and hollow shell obstacle (generated by gen_fixtures) Expect: region means after P4-CINE-10; the volume oracle bake matches at G1
  * port `crates/sr-sim/tests/pyro_mesh.rs::closed_mesh_contains_interior_surface_and_preserves_hollow_regions`: inside test incl. cavity
  * port `crates/sr-sim/tests/pyro_mesh.rs::mesh_validation_rejects_open_nonfinite_degenerate_and_oversized_inputs`: validation errors
  * port `crates/sr-sim/tests/pyro_mesh.rs::coincident_vertices_split_for_uvs_or_normals_do_not_open_the_volume`: welding across seams
  * port `crates/sr-sim/tests/pyro_mesh.rs::mesh_emission_matches_the_same_analytic_box_in_all_three_axes`: emission equals analytic box
  * port `crates/sr-sim/tests/pyro_mesh.rs::unused_vertices_do_not_change_mesh_bounds_or_numerical_scale`: unused vertices
  * non-pixel: Property test: inside() equals a brute-force winding-number test on 10,000 random points for 6 meshes (sphere, torus, cube with cavity, reflected-scale cube, concave L-shape, welded OBJ).
* Risks: Pure-numpy BVH closest-point queries can be slow: vectorise by processing point batches against node batches; target 100k queries/s on a 10k-triangle mesh; No numba in dev venv

#### P4-CINE-17: 3D force fields for volumetric sims: forceZ, z, gravityZ, 3D pose, windows, animation, drag, unit conversion

Rows: CINE-17 · size M · track P4-C3 · group G1 (after BOB-PHASE1) · schema adoption: yes · depends on: BOB-PHASE1

Scene force fields (affects all|particles; schema attributes forceField/@forceZ, @z, physics/@gravityZ) evaluated in 3D SCENE space for pyro, particles3D (and 3D rigid via SIM-17): domain-axis positions/velocities transformed by the domain's 3D pose into scene space and the acceleration transformed back, activation windows and animated field properties sampled at each fixed step's mapped scene time, physics-driven domain parents (rigid body moving a smoke domain) sampled per substep, drag uses the PRE-step velocity (also after a checkpoint restore -- state_dependent_inputs_replay_from_checkpoint_velocity), unit conversion through physics@pixelsPerMeter; forceFields IDREFS and useForceFields toggles on pyro and particles3D. Types as in the 2D engine: directional, wind, radial, vortex, turbulence, drag, attractor-path with falloff/radius, extended to 3D vectors. This is the 3D generalisation of physics.field_accel (scenerender/physics.py:285-358, 2D only); implement as scenerender/cine/sim/fields3d.py and add a thin entry point in physics.py. Overlaps SIM-17 / SIM-15/16 (SIM owns 2D field type fidelity): this package consumes whatever SIM delivers for the 2D semantics and adds the Z/pose/3D handling; if SIM has not landed, port field semantics from sr-sim/src/fields.rs directly.

* Python files: scenerender/cine/sim/fields3d.py (new); scenerender/physics.py (touch: export hook only; SIM-owned hub)
* Tests: tests/test_cine_fields3d.py (new)
* Rust reference: crates/sr-sim/src/fields.rs:1-180 (field evaluation); crates/sr-sim/src/physics3d.rs (3D field coupling); crates/sr-eval/src/pyro.rs (forces sampling per step); crates/sr-sim/tests/pyro.rs transformed_source_uses_local_shape_and_spatial_forces_act_in_three_dimensions, state_dependent_inputs_replay_from_checkpoint_velocity; crates/sr-eval/tests/pyro.rs (field tests); tests/corpus/valid/pyro-fields.scene.xml
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/pyro-fields.scene.xml` (new): Rust corpus pyro-fields.scene.xml: wind, vortex, drag with windows; rotated domain parent Expect: bake oracle (G1) once pyro core exists; before that, field-evaluation unit gate
  * evidence cases to turn native: rigid-particles-fields
  * port `crates/sr-sim/tests/pyro.rs::state_dependent_inputs_replay_from_checkpoint_velocity`: drag after a checkpoint restore equals the uninterrupted run
  * port `crates/sr-eval/tests/pyro.rs::(force-field tests)`: fields in rotated/reflected domains
  * non-pixel: Field-evaluation table: acceleration at 200 seeded points/times for each field type equals values produced by Rust pyro bakes of one-step scenes (zero-density, velocityRate impulses) -- compare velocity grid after 1 step, G1 tolerance 1e-6.
* Risks: SIM-15/16/17 overlap; agree on a single owner for field-type fidelity; Rust drag uses pre-step velocity; 2D Python uses exp(-k dt) -- keep both paths separate

#### P4-CINE-18: Pyro evaluator integration: owned <pyro> child, local/parent clocks, checkpoints, seek, cache keys, frame-local errors

Rows: CINE-14 · size M · track P4-C3 · group G5 (after 09,14,15,17) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-09, P4-CINE-14, P4-CINE-15, P4-CINE-17

Exactly one of @volume or one <pyro> child on a volume object (VOL1). Object supplies pose, timeline (start/end), visibility, medium; <pyro> supplies domain: width/height/depth, voxelSize, dt, boundary, ambientTemperature, dissipation, cooling, buoyancy, vorticity, turbulence, seed, pressureIterations/Tolerance, memory budgets (maxMemoryMiB, checkpointMemoryMiB), forceFields/useForceFields, colliders. animationSpeed/animationOffset map object local time to simulation source time; ancestor clocks inverted through the same machinery used for particles (nodes/particles.py ancestor clock tests); clock overrides; nonlinear parent clocks rebuild branch-dependent history; the Simulation runs on source time with Timeline checkpoints (P4-CINE-13); hard-budget checkpoints thinned before cloning; backward requests replay identical fixed steps and inputs; the exported fields (density/temperature/velocity grids + transforms) participate in compositor cache keys including isolated groups (grid content hash or (sim id, step) key) so a changed field redraws a cached group; errors are FRAME-LOCAL diagnostics (the frame fails, the rest of the timeline renders). Wires the exported SRVOL Volume into the VolumeDraw of P4-CINE-09 (medium on the object, advect not applicable).

* Python files: scenerender/cine/sim/pyro_node.py (new: build/evaluate/seek, Inputs assembly); scenerender/cine/render/volume_object.py (touch: pyro branch); scenerender/compositor.py (touch: cache-key hook for volume content; shared hub); scenerender/evaluator.py (touch only if clock inversion API lacks a hook; shared hub)
* Tests: tests/test_cine_pyro_node.py (new)
* Rust reference: crates/sr-eval/src/pyro.rs:1-319; crates/sr-eval/tests/pyro.rs; crates/sr-sim/src/timeline.rs; crates/sr-gpu/tests/volume.rs native_pyro_renders_and_invalidates_isolated_volume_content, baked_pyro_matches_native_pixels_with_parent_and_source_retiming
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/pyro-clocks.scene.xml` (new): Pyro object under a parent with timeScale 2 and offset, animationSpeed 0.5, a nonlinear (eased) parent clock, hidden window in the middle, inside an isolated group with opacity Expect: render order-independence: frames t=0.9,0.3,0.9 identical; Rust-vs-Python region means G2 after P4-CINE-10
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/pyro.scene.xml`: Rust corpus Expect: renders in both
  * port `crates/sr-eval/tests/pyro.rs::(retiming and checkpoint tests)`: local/parent clock mapping, seek equivalence
  * port `crates/sr-sim/tests/pyro.rs::checkpointed_seeking_matches_fresh_replay_with_a_tight_memory_budget`: tight budget replay equals fresh run
  * port `crates/sr-gpu/tests/volume.rs::native_pyro_renders_and_invalidates_isolated_volume_content`: cache invalidation in isolated group
  * non-pixel: Order-independence: bit-identical frames for permuted request order (Python-vs-Python); parity of exported fields vs Rust bake-volume with parent/animationSpeed retiming (G1).
* Risks: Compositor cache keys are a hub file used by all areas: additive hook only

#### P4-CINE-19: Pyro obstacles and colliders: voxelised rigid proxies, moving-boundary velocity, procedural regions

Rows: CINE-15 · size L · track P4-C3 · group G6 (after 14,16,18) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-14, P4-CINE-16, P4-CINE-18

colliders IDREFS (<=4096 distinct) referencing box/sphere/globe/plane/cylinder/cone/capsule/torus/mesh objects (text/extrude/clay solids delivered by P4-CINE-37): analytic regions (cylinder axis y, cone apex -half_height, capsule segment, torus ring in xz with minor<=major rule) or closed rendered surfaces; plain globes as spheres, planes as colliderThickness slabs (default 2*voxelSize), relieved globes and craters as shared closed meshes (CINE-25/26/38 hooks); sampled at cell centres; poses sampled at step start and end including rigid-body simulation (SIM-11..14 world; if absent, kinematic animation only); affine boundary velocity field G = (A1 - A0) * inverse((A1 + A0)/2) / dt evaluated at MAC faces (skew-symmetric for pure rotation, includes scale), midpoint-pose region test; singular motion rejected atomically (invalid_affine_collider_motion_is_atomic); reflected/rotated domains; per-consumer immutable acceleration caches charged to meshMemoryMiB; cells inside a collider: density 0, temperature ambient, face velocity = body velocity; Obstacle::stationary. Rust: crates/sr-sim/src/pyro.rs Obstacle + Shape::Transformed; evaluator in sr-eval/src/pyro.rs and src/solid.rs.

* Python files: scenerender/cine/sim/pyro_colliders.py (new: Obstacle, region shapes, affine boundary velocity, collider pose sampling); scenerender/cine/sim/pyro.py (touch: obstacle voxelisation hook; sequence after CINE-14)
* Tests: tests/test_cine_pyro_colliders.py (new)
* Rust reference: crates/sr-sim/src/pyro.rs:126-260 (boundary_velocity, Shape::contains, Obstacle::velocity_at), 640-662 (voxelisation loop); crates/sr-sim/tests/pyro.rs moving_solid_faces_carry_the_prescribed_velocity, affine_solid_motion_prescribes_rotation_and_scaling_at_faces, invalid_affine_collider_motion_is_atomic, curved_collider_primitives_preserve_holes_caps_and_taper; crates/sr-sim/tests/deforming_colliders.rs; crates/sr-sim/tests/pyro_mesh.rs deforming_obstacle_evaluates_velocity_at_mac_faces; crates/sr-eval/tests/pyro_colliders.rs; crates/sr-gpu/tests/volume.rs native_pyro_collider_carves_the_rendered_volume; tests/corpus/valid/pyro-colliders.scene.xml
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/pyro-colliders.scene.xml` (new): Smoke around a static sphere, a translating box and a rotating (kinematic) cylinder; plane slab; torus with hole; mesh collider Expect: bake oracle G1 plus a coverage gate: cells inside the collider have zero density in both renderers
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/pyro-colliders.scene.xml`: Rust corpus Expect: validates and bakes
  * evidence cases to turn native: rigid-3d
  * port `crates/sr-sim/tests/pyro.rs::affine_solid_motion_prescribes_rotation_and_scaling_at_faces`: boundary velocity formula
  * port `crates/sr-sim/tests/pyro.rs::curved_collider_primitives_preserve_holes_caps_and_taper`: torus hole / cone taper regions
  * port `crates/sr-sim/tests/pyro.rs::invalid_affine_collider_motion_is_atomic`: singular motion -> atomic error
  * port `crates/sr-eval/tests/pyro_colliders.rs::(all)`: scene-level collider resolution, limits 4096
  * non-pixel: G1 bake oracle on the collider scenes; plus invariant: solid cells carry the prescribed face velocity (max deviation 1e-12).
* Risks: Rigid proxy poses need SIM-11..14 (3D rigid); a staged delivery with kinematic-only motion is acceptable and recorded; Mesh colliders with large triangle counts stress the numpy BVH

#### P4-CINE-20: bake-volume CLI: freeze a native pyro object to a deduplicated SRVSEQ with receipt

Rows: CINE-08 · size S · track P4-C3 · group G7 (after 05,14,18) · schema adoption: no · depends on: P4-CINE-05, P4-CINE-14, P4-CINE-18

`scenerender bake-volume SCENE --object ID -o DIR [--first N --end N --param id=value --max-mib N --json]` mirroring `scene-render bake-volume` (crates/scene-render/src/main.rs:46,597): evaluates the instantiated pyro object over COMPOSITION time (including nested remaps, animationSpeed/Offset, forces, rigid-body proxies), writes deduplicated SRVOL frames and the SRVSEQ manifest through BakeWriter (P4-CINE-05), progress on stderr, receipt + manifest digest on stdout (JSON with --json), rejects existing output, aborts transactionally and removes owned partial output; the object then plays the bake with volume="..." (format srvseq) instead of a <pyro> child (bit-identical to native on the same frames: pyro_bake_validation '24 cached PNGs byte-identical to native originals'). Subcommand added to scenerender/cli.py following docs/parity/CLI.md conventions.

* Python files: scenerender/cli.py (touch: bake-volume subcommand; shared hub); scenerender/cine/sim/bake.py (new: bake driver)
* Tests: tests/test_cine_bake_cli.py (new)
* Rust reference: crates/scene-render/src/main.rs:46,597 (bake_volume); crates/sr-eval/tests/pyro_bake.rs; crates/sr-volume/src/bake.rs:234-352 (BakeWriter); crates/sr-gpu/tests/volume.rs baked_pyro_matches_native_pixels_with_parent_and_source_retiming; tools/evidence/cinematic-impact.json pyro_bake_validation
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/bake-roundtrip.scene.xml` (new): Pyro object, 12 frames at 12 fps; bake with Python CLI then play the bake; also play a Rust-made bake Expect: Python-bake playback equals Python-native render bit-exactly; manifests of Python and Rust bakes agree within the P4-CINE-14 field tolerances (compare frame fields, not digests, since float summation differs)
  * port `crates/sr-eval/tests/pyro_bake.rs::(bake tests)`: receipt fields, rejects existing output, transactional abort, retiming
  * non-pixel: Playback of a Python bake == native Python render bit-exactly (pixel arrays equal) for 12 frames; the Rust bake manifests parse and play.
* Risks: Frame time convention (composition time, start/fps snapping) must match Rust exactly or baked playback is off by a frame

#### P4-CINE-21: particles3D CPU core: exact births, shapes, seeded sampling, forces, drag, checkpoints, budgets

Rows: CINE-18 · size L · track P4-C4 · group G4 (after 13,14,17) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-13, P4-CINE-14, P4-CINE-17

Mirror sr_sim::particles3d (Spec at particles3d.rs:32-106; defaults: step 1/60, lifetime 2, direction (0,-1,0), radius 0.5, collision_tolerance 0.001, restitution 0.5, max_particles 10,000, max_events 16,384, max_bytes 256 MiB, checkpoint_bytes 64 MiB, max_work 1e8). Exact EVENT-TIMESTAMP births: constant rate integrated as a quota over fixed intervals (births at exact times, not step ends; rate carry is deterministic across seeks; huge burst counts bounded and particle ids never wrap), bursts fire once (burst time exactly on the interval boundary belongs to the following interval; error-bounded rounding 8*eps*(1+rate*(|end|+|lo|)) capped 1e-8); emitter shapes point, box, solid sphere, mesh (by LOCAL triangle area, bounded geometry; unused vertices do not alter area sampling) with uniform-in-solid-angle cone sampling (spread = full cone angle degrees); 64-bit seeds split into independent channels (position, direction, speed, lifetime, size, rotation, angular velocity) via the counter hash (P4-CINE-14 rng.py); drop-new live cap with checked emitted/dropped counters (overflow is an error where Rust errors); full birth affine (scale/shear) retained: local velocity through the linear part plus world inherited velocity (inheritedVelocity); birth rotation Rz*Ry*Rx in degrees, world-degree spin (angularVelocity), birth scale uniform in [1-v, 1+v) (v in [0,1)) scaling both render basis and world collision radius; gravity vector (x,y,z), drag (analytic exponential solution for constant force: drag_matches_the_analytic_constant_force_solution), force fields (P4-CINE-17) and useForceFields; per-seek work/event limits atomic and a shorter recovery seek still succeeds; checkpointed replay identical after scrubbing with evicted checkpoints; invalid inputs keep the last frame. Collision hooks (emission/acceleration/sweep callbacks) are declared here and implemented in CINE-22. Frame state = arrays (pos[N,3], vel[N,3], birth_time, id, basis[N,9], spin, seed channels) kept sorted by (birth_time, id) like Rust's Ord for Frame. Vectorise over live particles; per-particle ballistic segment integration with analytic drag solution.

* Python files: scenerender/cine/sim/particles3d.py (new); scenerender/cine/sim/particles3d_mesh.py (new: mesh emitter sampling; may reuse closed_mesh helpers but accepts open meshes with area sampling)
* Tests: tests/test_cine_particles3d.py (new)
* Rust reference: crates/sr-sim/src/particles3d.rs:32-307 (Spec/Emission/Particle/Frame/Emitter::at), 308-557 (validate, advance, emission, spawn), 558-720 (motion, flight, helpers); crates/sr-sim/src/particles3d/mesh.rs; crates/sr-sim/tests/particles3d.rs (22 tests); crates/sr-eval/src/particles3d.rs:34-275 (build from element, emission/acceleration callbacks); crates/sr-eval/tests/particles3d.rs; tests/corpus/valid/particles3d.scene.xml
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/p3d-fountain.scene.xml` (new): Rate emission 40/s + two bursts, cone spread 30 deg, gravity, drag, seeded size/lifetime/rotation variance, shape=sphere particles of fixed colour on a black background, orthographic camera (positions read directly from pixels) Expect: blob_match gate G4: per-frame particle count equal (exact) and >= 95% of blobs within 0.5 px; drag-free case within 0.05 px
  * scene `tools/parity/scenes/cine/p3d-shapes.scene.xml` (new): point/box/sphere/mesh emitters, burst at t=0.5 exactly, preroll via start before 0 Expect: birth counts exact; occupancy histogram by region equal
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/particles3d.scene.xml`: Rust corpus (burst of 12 dust particles bouncing on a floor) Expect: renders after CINE-22/23
  * port `crates/sr-sim/tests/particles3d.rs::births_use_their_exact_time_and_ballistic_motion_in_all_three_axes`: exact birth timestamp + ballistic equation in x,y,z
  * port `crates/sr-sim/tests/particles3d.rs::rate_carry_caps_and_expiry_are_deterministic_across_seeks`: rate carry, cap, expiry across arbitrary seek orders
  * port `crates/sr-sim/tests/particles3d.rs::constant_emission_counts_do_not_lose_particles_at_fractional_steps`: quota integration at fractional steps
  * port `crates/sr-sim/tests/particles3d.rs::huge_burst_counts_are_bounded_and_particle_ids_never_wrap`: burst bounds
  * port `crates/sr-sim/tests/particles3d.rs::drag_matches_the_analytic_constant_force_solution`: drag
  * port `crates/sr-sim/tests/particles3d.rs::spherical_emission_and_cone_spread_are_three_dimensional_and_seeded`: solid-angle uniformity
  * port `crates/sr-sim/tests/particles3d.rs::particle_instances_keep_the_birth_affine_and_spin_in_world_space`: birth basis retained
  * port `crates/sr-sim/tests/particles3d.rs::size_and_spin_variance_are_seeded_and_collision_radius_tracks_birth_size`: variance
  * port `crates/sr-sim/tests/particles3d.rs::work_and_event_limits_are_atomic_and_do_not_prevent_shorter_recovery_seeks`: atomic limits
  * port `crates/sr-sim/tests/particles3d.rs::dense_ejecta_replays_identically_after_scrubbing_with_evicted_checkpoints`: scrubbing
  * port `crates/sr-sim/tests/particles3d.rs::mesh_emission_samples_triangle_area_and_rejects_bad_or_oversized_geometry`: mesh emission
  * statistical: policy: Particle systems are deterministic with a counter-based RNG, so Python replicates Rust exactly up to libm (sin/cos/exp 1 ulp). Gate on STATE where observable and on STATISTICS where only pixels are: (1) per-frame live count equal; (2) blob positions: Hungarian-matched centroid error <= 0.5 px (orthographic, 640x360) for >=95% of particles, 100% at t<=0.5 s; (3) distributional tests where particle identity is not recoverable: for the cone-spread scene the empirical distribution of speed and polar angle at birth compared with a two-sample Kolmogorov-Smirnov test against Rust's extracted blob velocities (p>0.01) and region occupancy histograms EMD<=0.01; (4) RNG: the first 64 values of each channel for seed s equal the Rust values (compared through a 1-particle probe scene: size_variance probes read from rendered disc radius).; tolerances: count exact; position <=0.5 px; KS p>0.01; EMD<=0.01; seeds: scene seed (64-bit); channel split by counter hash; no extra Python seeds
  * non-pixel: Order independence: states at 12 times in 20 random orders bit-identical; birth times checked against analytic list of event timestamps.
* Risks: Birth-time exactness near step boundaries (three rounding rules in Rust) -- port the exact expressions; Particle counts up to 10^5 need vectorised integration; a per-particle Python loop is too slow

#### P4-CINE-22: particles3D rigid collisions: continuous sphere sweeps vs moving/rotating/deforming surfaces

Rows: CINE-19 · size L · track P4-C4 · group G5 (after 21,16 and SIM-12 3D shape definitions) · schema adoption: yes · depends on: P4-CINE-16, P4-CINE-21 · external: SIM-12

Replace Rust's Parry shape queries with an exact-enough own implementation of continuous sphere casting against the REST-pose surface of colliders: primitives (box, sphere, plane as FINITE planes, cylinder, cone, capsule, torus incl. the hole, globe), meshes (triangle BVH, P4-CINE-16), text/path extrusions and carved clay and relieved globes/craters (those surfaces are produced by CINE-37/29/30; this package consumes triangle soups); translating and rotating colliders swept between canonical step endpoints (>90 degree rotation per sweep is an ERROR), moving-surface velocity at the contact point, initial-penetration recovery, restitution and tangential friction (friction impulse capped by the tangential component, uses contact surface velocity), one-way semantics, collisionRadius*birth scale as sphere radius; curved-flight chord bound |a - k v0| dt^2 / 8 against collision_tolerance (default 0.001) refines segments (>16 contacts per step is an error, also at the end of a step); a swept-triangle BVH with polynomial vertex/edge/face contact-time solves for moving/deforming meshes (barycentric surface velocities, linearly moving vertices between canonical endpoints), supporting-contact stabilisation so resting particles do not jitter (fast_particles_sweep... rest under gravity), outgoing curved flight does not repeat spurious chord contacts. Collider references IDREFS expand released fracture objects (CINE-37) and craters (CINE-38). Shared triangle collider geometry validated and budgeted (shared_triangle_collider_geometry_is_validated_and_budgeted).

* Python files: scenerender/cine/sim/particles3d_collide.py (new: sweep queries, contact solve, geometry cache); scenerender/cine/sim/colliders.py (new: collider adapters shared by particles/pyro/fracture: pose sampling, rest-pose surface, velocity); scenerender/cine/sim/particles3d.py (touch: hook; sequence after CINE-21)
* Tests: tests/test_cine_particle_collisions3d.py (new)
* Rust reference: crates/sr-sim/src/particles3d/collider.rs; crates/sr-sim/src/particles3d.rs:558-690 (motion, flight, charge); crates/sr-sim/tests/particles3d.rs (collision tests: fast_particles_sweep_against_3d_geometry_and_rest_under_gravity, curved_flight_cannot_skip_a_wall_when_both_step_endpoints_are_on_the_same_side, outgoing_curved_flight_does_not_repeat_spurious_chord_contacts, friction_uses_contact_surface_velocity_and_caps_the_tangent_impulse, rigid_colliders_sweep_thin_geometry_with_translation_and_rotation, particles_land_on_real_3d_colliders_and_follow_a_moving_surface, collision_overflow_is_an_error_even_at_the_end_of_a_step, shared_triangle_collider_geometry_is_validated_and_budgeted); crates/sr-sim/tests/deforming_particles.rs; crates/sr-eval/src/particles3d.rs:169-275 (sweep callback), crates/sr-eval/src/sim3d.rs; tools/evidence/cinematic-impact.json particles3d_validation
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/p3d-collide.scene.xml` (new): Particles falling onto a box floor, a tilted plane, a sphere, a torus (through the hole), a moving kinematic box (follow-surface), friction 0 and 0.8; one-way Expect: blob_match G4 per frame; resting heights equal within 0.02 scene units; contact-count histogram per region
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/particles3d.scene.xml`: Rust corpus bounce scene Expect: blob_match
  * evidence cases to turn native: rigid-particles-fields
  * port `crates/sr-sim/tests/particles3d.rs::fast_particles_sweep_against_3d_geometry_and_rest_under_gravity`: fast particle never tunnels; comes to rest
  * port `crates/sr-sim/tests/particles3d.rs::curved_flight_cannot_skip_a_wall_when_both_step_endpoints_are_on_the_same_side`: chord bound refinement
  * port `crates/sr-sim/tests/particles3d.rs::friction_uses_contact_surface_velocity_and_caps_the_tangent_impulse`: friction model
  * port `crates/sr-sim/tests/particles3d.rs::rigid_colliders_sweep_thin_geometry_with_translation_and_rotation`: moving thin collider
  * port `crates/sr-sim/tests/deforming_particles.rs::(all)`: particles vs deforming triangle surfaces
  * statistical: policy: Collision response is deterministic given exact contact times, but contact solvers differ in last digits and chaotic bounce sequences diverge. Gate early (before the 3rd bounce) on exact particle positions via blob_match <=0.5 px, and later on resting statistics: final rest height of each particle within 0.02 scene units for >=90% particles (not chaotic: gravity+restitution decay), per-region occupancy EMD<=0.02 at rest, total kinetic energy never increases across a contact (assert E_after <= E_before*(1+1e-9) for restitution<=1).; tolerances: 0.5 px early, 0.02 units resting, EMD 0.02; seeds: scene seed
  * non-pixel: Analytic: ball dropped on a plane bounces to restitution^2*h; tangential friction with Coulomb cap (closed form).
* Risks: Replacing Parry exactly is impossible (different shape-cast algorithms); the target is the documented behaviour: no tunnelling, deterministic, restitution/friction per Rust formulas; Torus and text-extrusion colliders need triangle soups from other packages

#### P4-CINE-23: particles3D scene node and render shapes in the 3D surface pass (raster)

Rows: CINE-20 · size L · track P4-C4 · group G6 (after 21) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-21 · external: 3D-17, 3D-27

Additive nodeChoice <particles3D> (id, pose x/y/z/rotation/scale, start/end, rate, burst children, emitterShape point|box|sphere|mesh, direction, speed, spread, inheritedVelocity, gravityX/Y/Z, drag, rotation/spin variance, scaleVariance, forceFields/useForceFields, colliders/collisionRadius/bounce/friction, lifetime, shape sphere|billboard|streak|mesh (sprite image asset, segments<=256), material, castShadow/receiveShadow, size/color/opacity-over-life curves, trail) built from the element through the P4-CINE-21 emitter and drawn as instances in Python's native 3D surface pass: depth testing against other 3D objects, lighting, shadows (receive/cast), shutter sampling for motion blur (sub-frame world at shutter times; scenerender shutter machinery in tests/test_shutter_samples.py), prototypes share vertex buffers (reuse scene._instances GPU instancing: one draw per prototype with an instance matrix buffer; Rust draws one Draw3 per instance today, the output is identical), billboards face the camera, streaks stretch along velocity, mesh particles use the referenced mesh asset, sprite sheets/transfer functions honoured (particle_sprites_honor_the_image_transfer_declaration), age curves for size/colour/opacity. Parent transform affects the emitter pose only: births use the emitter pose at the birth time and existing particles are not attached to it. Particles redraw inside cached isolated groups when their state changes (cache key hook, CINE-18). Registers NODES 'particles3D' FULL; the 2D particleEmitter node is untouched.

* Python files: scenerender/cine/render/particles3d_node.py (new); scenerender/three/scene.py (touch: collect particles3D nodes into World3D items; shared hub); scenerender/three/renderer.py (touch: instanced prototype draw; shared hub); scenerender/registry.py (touch)
* Tests: tests/test_cine_particles3d_render.py (new)
* Rust reference: crates/sr-eval/src/particles3d.rs:34-153 (build, frame); crates/sr-gpu/tests/particles3d.rs (4 tests: particles_share_native_depth_and_redraw_inside_cached_groups, billboard_streak_and_mesh_particles_render_native_geometry_and_sprites, particles_use_age_curves_and_native_shutter_samples, particle_sprites_honor_the_image_transfer_declaration); crates/sr-eval/tests/particles3d.rs; crates/sr-gpu/src/particles.rs, crates/sr-gpu/src/three.rs (particle draw prototypes); crates/sr-model/tests/particles3d_rules.rs (P3D1-6); tools/evidence/cinematic-impact.json particles3d_preview
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/p3d-render.scene.xml` (new): Sparks: billboard + streak particles with age curves over a lit floor with a shadowing spot; sphere and mesh particles in front of and behind a 3D object (depth), motion blur on Expect: PSNR >= 30 (CLOSE) with matching blob statistics (G4) -- shading differences from the shared raster-3D divergences (3D-17/27) are not charged to this package
  * scene `tools/parity/scenes/cine/p3d-group-cache.scene.xml` (new): Particles inside an isolated group with opacity Expect: group content changes between frames (cache invalidation)
  * port `crates/sr-gpu/tests/particles3d.rs::particles_share_native_depth_and_redraw_inside_cached_groups`: depth interplay and cache redraw
  * port `crates/sr-gpu/tests/particles3d.rs::billboard_streak_and_mesh_particles_render_native_geometry_and_sprites`: shape kinds
  * port `crates/sr-gpu/tests/particles3d.rs::particles_use_age_curves_and_native_shutter_samples`: curves and shutter
  * port `crates/sr-gpu/tests/particles3d.rs::particle_sprites_honor_the_image_transfer_declaration`: sprite transfer
  * statistical: policy: Raster rendering is deterministic; gate with pixel PSNR plus blob statistics as in P4-CINE-21 (positions, counts, per-blob mean colour within 3% after lighting). No seeds.; tolerances: PSNR>=30 required (45 aspirational on emissive-only scenes); blob colour 3%; seeds: n/a
  * non-pixel: Geometry: instance matrices equal Rust's for 5 particles (read from a Rust --format json eval when exposed; otherwise via silhouettes).
* Risks: Shading of lit particles inherits whatever divergences 3D-17/20/27 leave (marked DIVERGENT in the matrix): charge those to the 3D area; Mesh particles with segments<=256 sphere tessellation must use the same tessellation to match silhouettes

#### P4-CINE-24: particles3D in the path tracer (triangle expansion) and parity polish

Rows: CINE-20 · size S · track P4-C4 · group G7 (after 23 and SIM2-30) · schema adoption: no · depends on: P4-CINE-23, P4-SIM2-22

When camera renderer=pathtrace (or volumes force it), particle prototypes are expanded to triangles in the path-trace scene build (Rust: the path tracer expands triangles; raster uses instancing): one instance's world matrix applied to the prototype mesh, material per particle colour/opacity, billboard orientation from the camera, streak length from velocity, shadow flags; budgets: expanded triangle count counted in the SIM2 scene-fit check (limit_note) and raising the frame error/fallback note when exceeded. Also the collision/emission plumbing check that the same Python particle frame feeds raster and path tracer (frames equal).

* Python files: scenerender/cine/render/particles3d_pt.py (new); scenerender/three/pathtrace.py (SIM2-owned; touch hook) shared hub
* Tests: tests/test_cine_particles3d_pt.py (new)
* Rust reference: crates/sr-gpu/src/pathtrace.rs:165-385 (build: scene flattening incl. particle expansion); crates/sr-gpu/tests/particles3d.rs (path-trace regressions); crates/sr-gpu/tests/ocean.rs whitewater_batches_render_typed_materials_in_raster_and_pathtrace (same expansion mechanism)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/p3d-pathtrace.scene.xml` (new): p3d-render.scene.xml with camera renderer=pathtrace, 64 spp Expect: stochastic gates of P4-CINE-11 (S1 region means 3 sigma, S4 EMD)
  * statistical: policy: As P4-CINE-11 S1/S4 at 64 spp, K=8 seeds; plus triangle-count equality with the raster instance count.; tolerances: S1 2%/3sigma, EMD<=0.02; seeds: salts 0..7
  * non-pixel: Raster vs path-traced particle silhouettes equal within 1 px at 1 spp with an unlit emissive material.
* Risks: Depends on SIM2 scene flattening seam

#### P4-CINE-25: Ocean shallow-water solver core: depth + two momenta, hydrostatic reconstruction, wet/dry, boundaries, budgets

Rows: CINE-21 · size L · track P4-C5 · group G4 (after 13) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-13

Mirror sr_sim::ocean (ocean.rs:36-325, flux.rs): first-order finite-volume depth-averaged shallow-water on square x/z cells (cellSize), unknowns q = (h, hu, hv) per cell, local Lax-Friedrichs numerical fluxes with Audusse hydrostatic reconstruction over a bed y (bathymetry), unsplit CFL bound 0.45 with substepping inside one canonical dt, dryTolerance (default 1e-10) momentum removal (water never discarded), boundaries closed (reflecting) | open (zero-gradient, NON-absorbing, ocean.rs:29) | periodic (both horizontal axes incl. bathymetry), damping (exponential horizontal velocity decay), gravity 9.81, initial velocity; canonical dt (default 1/60, >=1e-6 s); bounded memory/checkpoints (<=4M cells, 4096 checkpoints, checkpoint_bytes 64 MiB, max_bytes 256 MiB including retained input capacity and the event-sort workspace, 100M work units per seek: eight per cell per impulse and per substep); fractional (non-grid-time) samples are DISPOSABLE (never canonical): the sampled frame is computed by advancing a COPY by the remainder; replay after checkpoint eviction bit-identical (FIFO eviction, ocean.rs:243-246); failed seeks atomic (published frame, caches intact); numerical errors for nonfinite/overlarge state; time quantisation uses the canonical-time comparison loop (ocean.rs:218-227). numpy float64, vectorised flux with array slicing; unsplit 2-D update per substep.

* Python files: scenerender/cine/sim/ocean.py (new: Spec, Ocean, advance, flux); scenerender/cine/sim/ocean_flux.py (new: velocity, interface/hydrostatic reconstruction)
* Tests: tests/test_cine_ocean.py (new)
* Rust reference: crates/sr-sim/src/ocean.rs:36-325; crates/sr-sim/src/ocean/flux.rs; crates/sr-sim/tests/ocean.rs (17 tests); tools/evidence/cinematic-impact.json ocean_validation (82 sim regressions incl. the independently derived dam-break convergence test)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/ocean-solver.scene.xml` (new): Closed 16x16 basin with a displaced hump (waterImpulse), periodic variant, open variant, dry beach slope; whitewater off; orthographic top-down camera, water material emissive depth-encoded so pixels read depth Expect: region field gate G1 on depth (rel-L2 <= 1e-6 at frame 1, 5e-5 at frame 36) and volume conservation to 1e-12
  * port `crates/sr-sim/tests/ocean.rs::uneven_lake_and_dry_shore_stay_at_rest`: well-balancedness (hydrostatic reconstruction): lake at rest stays at rest to 1e-12
  * port `crates/sr-sim/tests/ocean.rs::dam_break_wets_dry_cells_without_losing_water`: mass conserved to 1e-12, positivity
  * port `crates/sr-sim/tests/ocean.rs::dry_dam_break_matches_the_analytic_rarefaction`: dry dam-break against the analytic rarefaction (Ritter) solution
  * port `crates/sr-sim/tests/ocean.rs::small_right_travelling_wave_matches_the_shallow_water_speed`: wave speed sqrt(g h)
  * port `crates/sr-sim/tests/ocean.rs::periodic_uniform_flow_has_analytic_damping`: damping closed form
  * port `crates/sr-sim/tests/ocean.rs::checkpoint_eviction_preserves_exact_replay`: replay after eviction bit-identical
  * port `crates/sr-sim/tests/ocean.rs::insufficient_displacement_water_and_work_failure_are_atomic`: atomic failure
  * port `crates/sr-sim/tests/ocean.rs::budgets_and_invalid_data_fail_before_solver_allocation`: admission
  * port `crates/sr-sim/tests/ocean.rs::two_dimensional_high_energy_flow_stays_positive_and_conservative`: positivity + conservation
  * port `crates/sr-sim/tests/ocean.rs::floating_point_tick_rounding_cannot_fire_a_future_impulse`: tick rounding
  * statistical: policy: Deterministic finite-volume scheme in f64: field-level gate G1 against Rust with tolerance accounting for vectorised-vs-scalar summation order (1e-9 relative per step growth): rel-L2 of h, hu, hv <= 1e-6 at frame 1 and <= 5e-5 at frame 36 (gravity waves, non-chaotic). Invariants: total water volume conserved to 1e-12 relative (closed/periodic), well-balanced lake at rest (|dh|<=1e-12), positivity (h>=0), CFL<=0.45 substep count equal to Rust's (observable through whitewater/work accounting only), dam-break convergence order about 1 under mesh refinement (estimated slope in [0.8,1.2] from 3 refinement levels). Rust has no ocean-state dump; field comparison uses the depth-encoded top-down render and the 16-bit linear PNG path.; tolerances: rel-L2 1e-6 / 5e-5; conservation 1e-12; convergence slope [0.8,1.2]; seeds: none (no RNG in solver)
  * non-pixel: Solver self-gates listed above
* Risks: Reading depth back from a Rust render needs a depth-encoded material path with 16-bit output; if that is infeasible gate with regional means G2 only; Vectorised flux must keep the same operation order as flux.rs to stay near 1e-9

#### P4-CINE-26: Ocean forcing: analytic <wave> swell and timed <waterImpulse> (displace / add-water)

Rows: CINE-22 · size M · track P4-C5 · group G5 (after 25) · schema adoption: yes · depends on: P4-CINE-25

<wave> (<=64 per ocean): wavelength (>= 2*cell), amplitude, direction, phase, speed (default sqrt(g*bottomDepth)); amplitude tapered to half the local depth, wet-cell mean subtracted so volume is conserved, never fed back into the solver state (analytic overlay on the published frame), horizontal velocity speed*h/depth. <waterImpulse> time, x, z, radius, amplitude, velocity, type displace|add: exact-time substep splitting (events at time 0 belong to the initial state; equal times keep document order; floating-point rounding cannot fire a future impulse); add-water adds amplitude*max(0,1-r^2)^2 volume; displace conserves water between the central disc max(0,1-4 r^2)^2 and an annulus sin^2(2 pi (r-0.5)) with donor/receiver weights (never borrows outside its radius; insufficient water is an atomic error; overflowing displacement capacity is an error not created water); event sort workspace charged to the resident budget; an impulse does not modify flow outside its support; max events from the budget.

* Python files: scenerender/cine/sim/ocean_waves.py (new); scenerender/cine/sim/ocean_impulse.py (new); scenerender/cine/sim/ocean.py (touch: apply hooks; sequence after CINE-25)
* Tests: tests/test_cine_ocean_forcing.py (new)
* Rust reference: crates/sr-sim/src/ocean/waves.rs:7-60; crates/sr-sim/src/ocean/impulse.rs; crates/sr-sim/src/ocean.rs:286-325 (advance applies events); crates/sr-sim/tests/ocean.rs timed_displacement_is_conservative_and_seek_order_independent, injection_adds_water_and_displacement_never_borrows_from_outside_its_radius, an_impulse_does_not_modify_flow_outside_its_support, procedural_swell_has_authored_speed_and_preserves_water_and_the_solver_state, overflowing_displacement_capacity_is_an_error_not_created_water, event_sort_workspace_is_included_in_the_resident_budget; crates/sr-model/tests/ocean_rules.rs (OCN1-5)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/ocean-forcing.scene.xml` (new): Swell of 3 waves plus two timed impulses (displace and add) incl. one at t=0 and two equal times Expect: depth-field gate G1 as P4-CINE-25
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/ocean.scene.xml`: Rust corpus ocean (hump impulse, wave, whitewater) Expect: renders
  * port `crates/sr-sim/tests/ocean.rs::timed_displacement_is_conservative_and_seek_order_independent`: conservation and seek independence
  * port `crates/sr-sim/tests/ocean.rs::procedural_swell_has_authored_speed_and_preserves_water_and_the_solver_state`: swell speed, volume, solver untouched
  * port `crates/sr-sim/tests/ocean.rs::injection_adds_water_and_displacement_never_borrows_from_outside_its_radius`: kernel support
  * non-pixel: G1 depth field vs Rust render; conservation 1e-12.
* Risks: Kernel formulas must be copied from impulse.rs, not from the description

#### P4-CINE-27: Bathymetry sources (red/terrarium/mapbox image, mesh) and the <ocean> node with rendered water surface

Rows: CINE-23 · size M · track P4-C5 · group G6 (after 25,26) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-25, P4-CINE-26

Bathymetry IDREF to an image or mesh asset; encodings red (raw normalised red channel, NO colour transform), terrarium -(R*256 + G + B/256 - 32768), mapbox -(-10000 + (R*65536 + G*256 + B)*0.1), then bathymetryScale/Offset; the full raster maps to the domain and is sampled bilinearly at cell centres; mesh bathymetry sampled by the topmost transformed intersection per cell (a cell not covered by the mesh is an error); 128 MiB encoded cap and header dimension check BEFORE decode (layered-image validation); <ocean> node (width, depth, cellSize, waterLevel, bottomDepth, boundary, seed, material, memory/work budgets maxMemoryMiB/checkpointMemoryMiB/maxWork, pose, opacity, visibility) evaluated through the P4-CINE-25/26 solver; rendered as a native 3D object in the raster surface pass AND the path tracer (default clear-water material equals the explicit clear-water optics, ocean_default_material_matches_explicit_clear_water_optics): triangle grid with positions displaced to surface height (water level + h - bed...), vertex normals from height gradients, the whitewater meshes (CINE-28) appended, redraw inside isolated groups on state change, replay deterministic. Python's existing transmission/IOR/volume materials (three/materials.py) are the water look.

* Python files: scenerender/cine/sim/bathymetry.py (new); scenerender/cine/render/ocean_node.py (new: node eval + surface mesh build + draw item); scenerender/three/scene.py (touch: collect ocean nodes; shared hub); scenerender/registry.py (touch)
* Tests: tests/test_cine_bathymetry.py (new); tests/test_cine_ocean_render.py (new)
* Rust reference: crates/sr-sim/src/ocean.rs (bathymetry intake); crates/sr-eval/src/ocean.rs:36-196; crates/sr-eval/tests/ocean.rs; crates/sr-gpu/tests/ocean.rs native_ocean_renders_changes_inside_an_isolated_group_and_replays, ocean_default_material_matches_explicit_clear_water_optics; crates/sr-model/tests/ocean_rules.rs; tools/evidence/cinematic-impact.json ocean_preview, ocean_uhd_probe
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/ocean-bathymetry.scene.xml` (new): Terrarium PNG DEM (generated), Mapbox PNG, red-channel PNG and a mesh seabed; a shoreline where dry cells appear; clear-water material over a lit seabed Expect: depth-field G1 and PSNR>=30 on the lit view
  * scene `tools/parity/scenes/cine/ocean-isolated-group.scene.xml` (new): Ocean in an isolated group with opacity; waves change frame to frame Expect: group redraws
  * port `crates/sr-gpu/tests/ocean.rs::native_ocean_renders_changes_inside_an_isolated_group_and_replays`: ocean redraw in cached group, replay
  * port `crates/sr-gpu/tests/ocean.rs::ocean_default_material_matches_explicit_clear_water_optics`: default material == explicit clear-water
  * port `crates/sr-eval/tests/ocean.rs::(bathymetry decode tests)`: decode formulas and budget
  * statistical: policy: Surface render deterministic: PSNR (CLOSE>=30) plus depth-field G1. Path-traced water stochastic: P4-CINE-11 S1/S4.; tolerances: as cited; seeds: path tracer salts if used
  * non-pixel: Decode formulas: terrarium/mapbox decode of 20 pixel values equals the formula; header dimension check rejects a 100000x100000 PNG header before decode.
* Risks: Mesh bathymetry needs ray casting against a triangle soup: reuse CINE-16 BVH; Water shading parity depends on 3D transmission divergences (3D-04/18)

#### P4-CINE-28: Ocean whitewater: foam and spray tracers, native foam/spray meshes

Rows: CINE-24 · size M · track P4-C5 · group G7 (after 27) · schema adoption: yes · depends on: P4-CINE-14, P4-CINE-27

Static <whitewater> child of <ocean>: activity = max(|grad h|, Froude), expected births per canonical interval = max(0, activity - threshold)*emissionRate*dt (integer part + seeded Bernoulli remainder; seeded positions inside the cell), births only at canonical endpoints (fractional samples emit nothing, ocean replay unchanged); foam advects with the sampled flow and rides the wet surface, spray ballistic with gravity + drag and a launchSpeed, landing becomes foam, dry cells remove particles, open/periodic/closed edge handling (wrap, remove, reflect per the Rust code); lifetime, sprayFraction, launchSpeed, drag, radius, seed (u64 all bits), maxParticles <= 1,000,000 (overflow is an error, activity_overflow_is_reported...), maxMemoryMiB, maxWork charges; bed capacity retained and budgeted; backward seek reconstructs from zero; the source water state is never modified (foam_advects_wraps_and_dies_without_modifying_source_water). Rendered as native meshes: foam = 8-triangle discs lying on the surface, spray = 8-triangle ... (see whitewater.rs/render for exact tessellation) with typed materials in raster and path tracer (whitewater_batches_render_typed_materials_in_raster_and_pathtrace). Uses the CINE-14 counter RNG.

* Python files: scenerender/cine/sim/whitewater.py (new); scenerender/cine/render/whitewater_mesh.py (new); scenerender/cine/render/ocean_node.py (touch, sequence after CINE-27)
* Tests: tests/test_cine_whitewater.py (new)
* Rust reference: crates/sr-sim/src/ocean/whitewater.rs:6-378; crates/sr-sim/tests/whitewater.rs (7 tests); crates/sr-gpu/tests/ocean.rs whitewater_batches_render_typed_materials_in_raster_and_pathtrace; tools/evidence/cinematic-impact.json ocean_validation (whitewater_core 7)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/whitewater.scene.xml` (new): Breaking dam over a slope: foam and spray, emissionRate 20, seeds, orthographic top-down + side view Expect: blob_match G4 per frame: foam/spray counts equal, centroids within 0.5 px early
  * port `crates/sr-sim/tests/whitewater.rs::calm_water_is_empty_and_moving_water_emits_only_at_canonical_ticks`: no emission when calm or between ticks
  * port `crates/sr-sim/tests/whitewater.rs::spray_flies_ballistically_then_becomes_surface_foam`: spray lifecycle
  * port `crates/sr-sim/tests/whitewater.rs::replay_fractional_queries_and_all_seed_bits_are_deterministic`: replay + all 64 seed bits
  * port `crates/sr-sim/tests/whitewater.rs::foam_advects_wraps_and_dies_without_modifying_source_water`: source untouched
  * port `crates/sr-sim/tests/whitewater.rs::slope_emission_dry_land_open_boundaries_and_emission_windows`: emission cases
  * port `crates/sr-sim/tests/whitewater.rs::limits_and_bad_source_samples_fail_atomically`: atomic
  * statistical: policy: Seeded Bernoulli remainders make particle numbers a deterministic function of the (bit-exact) counter RNG and of the water solution; since the water solution agrees to ~1e-6 the births agree except near thresholds. Gate: births per frame within +-2 particles of Rust over 36 frames and cumulative birth count within 1%; blob_match >=90% within 1 px; if a threshold crossing flips individual births, the per-frame region occupancy histogram EMD<=0.02. Foam/spray ratio equals sprayFraction within a binomial 3-sigma band.; tolerances: count +-2/frame, cumulative 1%, EMD 0.02; seeds: scene seed
  * non-pixel: Invariants: foam never in dry cells; spray count*launch kinematics analytic.
* Risks: Threshold-crossing sensitivity amplifies tiny solver differences into different births: statistical gate chosen deliberately

#### P4-CINE-29: Globe radial elevation from PMTiles terrain (terrain, terrainEncoding, planetRadius, exaggeration, tiles sampling)

Rows: CINE-25 · size L · track P4-C5 · group G5 (after MAPS-21 and 3D-43) · schema adoption: yes · depends on: BOB-PHASE1, P4-MAPS-14 · external: 3D-43

object3D primitive=globe with a tiles asset (local PMTiles v3 archive, none/gzip tile compression only, buffered tiles rejected) and map drape: attributes terrain (tiles asset IDREF), terrainEncoding terrarium|mapbox, planetRadius Rp, exaggeration e, terrainTileSize, terrainZoom, terrainMissing error|zero, terrainMemoryMiB. Radius r = Rs*(1 + h/Rp*e) with h from RGB8/RGBA8 PNG/WebP numeric tiles; bilinear sampling across tile edges with date-line wrap; automatic zoom ceil(log2(segments/tileSize)) clamped 0-22 or terrainZoom; Web-Mercator coverage with smoothstep polar closure beyond 85.051 degrees; welded date-line/pole vertices; f64 area-weighted vertex normals; east-projected tangents; segments clamped 24-512 (verify exact bound in terrain.rs); memory_cost admission before sampling; closed-surface mesh feeds the colliders (particles/pyro) as 'relieved globe'. DEPENDS on the MAPS area for the PMTiles v3 reader (MAPS-21) and Mercator tile math (MAPS-20) and on 3D-43 (globe primitive and map drape); this package supplies only terrain sampling and displaced geometry. Overlaps MAPS-31 (terrain from elevation tiles): agree one owner; this plan assigns the globe radial-elevation geometry to CINE.

* Python files: scenerender/cine/geo/terrain.py (new: Globe spec, sample tiles, radial geometry, memory_cost); scenerender/three/geometry.py (touch: globe displacement hook; 3D-owned hub)
* Tests: tests/test_cine_terrain.py (new)
* Rust reference: crates/sr-3d/src/terrain.rs:6-132 (Globe, memory_cost, globe); crates/sr-3d/tests/terrain.rs (3 tests); crates/sr-eval/src/terrain.rs:25-164; crates/sr-eval/tests/terrain.rs, terrain_memory.rs; crates/sr-gpu/src/drape.rs, render_map3d.rs; tests/corpus/valid/globe-relief.scene.xml; tools/evidence/cinematic-impact.json globe_validation, globe_preview; tools/fixtures/make_tile_expected.py
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/globe-relief.scene.xml` (new): Gaussian-hill Terrarium PMTiles (gen_fixtures) on a draped globe, exaggeration 20, date-line crossing, polar cap, terrainMissing zero and error variants Expect: PSNR>=35 (deterministic raster); vertex-radius gate: sampled silhouette radius at 36 meridians within 0.2%
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/globe-relief.scene.xml`: Rust corpus Expect: validates
  * evidence cases to turn native: map-3d
  * port `crates/sr-3d/tests/terrain.rs::elevations_are_radial_and_convert_metres_to_scene_units`: radial offset from metres
  * port `crates/sr-3d/tests/terrain.rs::rough_globes_have_exact_seams_watertight_poles_and_outward_nonzero_faces`: welded seams/poles, outward faces
  * port `crates/sr-3d/tests/terrain.rs::relief_changes_normals_and_limits_fail_before_sampling`: normals + admission
  * port `crates/sr-eval/tests/terrain_memory.rs::(memory admission)`: terrainMemoryMiB
  * non-pixel: Geometry: Python vertex radii vs analytic Gaussian hill heights (exact within f32) and exact seam welding (edge-manifold check).
* Risks: Hard cross-area dependency on MAPS PMTiles and 3D-43 globe geometry; fallback: ship with an in-package minimal PMTiles reader behind a TODO to switch; Tile sampling across zoom and date-line wrap has many off-by-one cases

#### P4-CINE-30: Crater deformation kernel (<crater>) applied to rendered surface geometry

Rows: CINE-26 · size M · track P4-C5 · group G1 (after BOB-PHASE1) · schema adoption: yes · depends on: BOB-PHASE1

Owned <crater> on a surface object3D (primitive geometry, imported mesh, globe): centre/normal, radius R, depth, rimHeight/rimWidth, influenceDepth, start/end on the object LOCAL clock, curve linear|ease-in|ease-out|ease-in-out|step giving p(t), maxMemoryMiB. Displacement along n by p*(-depth*B(r/(R p)) + rim*B((r - R p)/(w p)))*B(h/influenceDepth) with the compact bump B(x)=(1-x^2)^2 on |x|<1 (crater.rs:189); identity at p=0; analytic Jacobian J used for normals via inverse-transpose, tangents Gram-Schmidt re-orthogonalised, UV/colour/handedness preserved; topology unchanged; applied AFTER node/skin/morph transforms and respecting the imported mesh basis and node transforms (crater_deformation_respects_imported_mesh_basis_and_node_transforms); deformed bounds (current vertex bounds) drive shadow fitting and depth sorting; per-object draw discard + diagnostic when budget/limits fail; memory admitted before copying (a 1 MiB crater budget must error, not allocate 16 MiB). Python hook: scenerender/three/scene.py mesh pose pipeline (animation.py CPU deform) -- add the deformer as the last vertex stage; works in raster and path tracer (same CPU-deformed mesh).

* Python files: scenerender/cine/geo/crater.py (new: Spec, Mapping, deform, jacobian); scenerender/three/scene.py (touch: apply crater stage after pose; shared hub)
* Tests: tests/test_cine_crater.py (new)
* Rust reference: crates/sr-3d/src/crater.rs:1-201 (Spec, Crater, Mapping::map, deform, bump); crates/sr-3d/tests/crater.rs (6 tests); crates/sr-eval/src/crater.rs:14-47; crates/sr-gpu/tests/crater.rs native_crater_deforms_both_renderers_and_replays_inside_isolated_groups; crates/sr-model/tests/crater.rs (CRT1-5); tests/corpus/valid/crater.scene.xml
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/crater.scene.xml` (new): Subdivided globe/box with an animated crater (curves ease-in-out, step), start/end window, imported mesh with node transforms; shadows on Expect: PSNR>=40 (pure deterministic geometry) and a per-vertex displacement gate
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/crater.scene.xml`: Rust corpus Expect: renders
  * port `crates/sr-3d/tests/crater.rs::crater_excavates_center_and_raises_rim_with_compact_support`: profile
  * port `crates/sr-3d/tests/crater.rs::crater_jacobian_matches_finite_differences_and_keeps_orientation`: Jacobian vs FD
  * port `crates/sr-3d/tests/crater.rs::crater_normals_follow_the_deformed_surface_and_preserve_vertex_data`: normals/UV/colour
  * port `crates/sr-3d/tests/crater.rs::crater_rejects_invalid_domains_and_admits_memory_before_copying`: admission
  * port `crates/sr-3d/tests/crater.rs::crater_mapping_and_differential_rotate_with_its_authored_frame`: frame
  * port `crates/sr-3d/tests/crater.rs::crater_deformation_respects_imported_mesh_basis_and_node_transforms`: basis
  * non-pixel: Vertex gate: displaced vertex positions (f64) equal the Rust formula implemented independently in a spreadsheet-style test at 100 vertices (1e-12); rendered silhouette gate against Rust.
* Risks: Rust applies after skin/morph; Python animation.py computes poses on CPU, hook order must be right

#### P4-CINE-31: Mesh sequences (<meshSequence> asset): numbered topology-changing mesh playback, cache, missingFrame

Rows: CINE-32 · size M · track P4-C5 · group G2 (after BOB and 32) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-32

Asset <meshSequence> under <assets>: src (%d/%0Nd/#, <=64 digits), optional format (gltf, glb, obj, ply, usd, usda, usdc, usdz, fbx; else by extension -- note: unlike volumes, extension inference is allowed here), first/last (<=1,000,000 frames), fps, interpolation hold|linear, missingFrame error|hold|transparent, maxMemoryMiB; object3D primitive=mesh references it by @mesh; time t = local_time*animationSpeed + animationOffset, q = clamp(t*fps, 0, last-first); hold picks floor(q); linear blends i,i+1 (CINE-32); per-Program FIFO cache of 64 frames / 256 MiB; missing-frame hold = backward search; transparent gives no geometry at all (an object that draws nothing); linear with ONE absent endpoint under transparent fades by temporal weight (missing_mesh_frames_distinguish_holes_from_decode_errors_and_fade_explicit_transparency); decode errors are never treated as holes; sequences preserve topology changes in hold mode (vertex/index counts may differ per frame); hierarchy depth limit independent of node order and cycle/invalid reference rejection (mesh_cache_hold_rejects_cycles...). Reuse scenerender/three/loaders.load_model for each frame (it has an mtime-keyed model cache; add a sequence-level FIFO with the Rust sizes) and assets/image.py placeholder expansion pattern as a starting point (Rust differences as in P4-CINE-03). Bytes accounting (sequence.rs:114 bytes()). GPU upload cache keyed by frame label.

* Python files: scenerender/assets/mesh_sequence.py (new: asset model, resolve, frame selection); scenerender/cine/geo/mesh_sequence.py (new: Sample, Sequence, FIFO cache, bytes); scenerender/three/scene.py (touch: mesh branch asks the sequence for the frame model; shared hub); scenerender/registry.py (touch)
* Tests: tests/test_cine_mesh_sequence.py (new)
* Rust reference: crates/sr-3d/src/sequence.rs:20-359 (Sample, Sequence::new/sample/resolve, bytes, validate); crates/sr-3d/tests/sequence.rs (sequence_sampling_clamps_replays_and_preserves_topology_changes_in_hold_mode, missing_mesh_frames_distinguish_holes_from_decode_errors_and_fade_explicit_transparency, mesh_cache_hold_rejects_cycles_and_invalid_texture_or_skin_references, mesh_hierarchy_depth_limit_is_independent_of_node_order); crates/sr-eval/src/mesh_sequence.rs:38-141; crates/sr-eval/tests/mesh_sequence.rs; crates/sr-gpu/tests/mesh_sequence.rs topology_changing_mesh_cache_renders_and_replays_in_both_renderers; crates/sr-model/tests/mesh_sequence.rs (MSQ1-4); tests/corpus/valid/mesh-sequence.scene.xml; tools/fixtures/make_corpus_mesh.py; tools/evidence/cinematic-impact.json mesh_sequence_validation, mesh_sequence_preview
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/mesh-sequence.scene.xml` (new): OBJ sequence of a deforming sphere whose vertex count changes at frame 3, hold and linear (linear only over compatible frames), a missing frame under hold/transparent/error, retimed by animationSpeed and offset Expect: PSNR>=40 (deterministic raster) per frame; error scenes give the same diagnostic
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/mesh-sequence.scene.xml`: Rust corpus (mesh-frame-%d.obj generated) Expect: validates and renders
  * port `crates/sr-3d/tests/sequence.rs::sequence_sampling_clamps_replays_and_preserves_topology_changes_in_hold_mode`: clamp + topology change
  * port `crates/sr-3d/tests/sequence.rs::missing_mesh_frames_distinguish_holes_from_decode_errors_and_fade_explicit_transparency`: holes vs decode errors, fade
  * port `crates/sr-3d/tests/sequence.rs::mesh_hierarchy_depth_limit_is_independent_of_node_order`: depth limit
  * port `crates/sr-gpu/tests/mesh_sequence.rs::topology_changing_mesh_cache_renders_and_replays_in_both_renderers`: render + replay
  * non-pixel: Frame-selection table via file-open trace (as P4-CINE-03).
* Risks: 3D-44 in the 3D area also lists meshSequence: this plan puts it in CINE; the 3D plan must not duplicate it; Python loaders cache by mtime; the sequence cache must not double-count bytes

#### P4-CINE-32: Mesh-sequence linear interpolation with topology-compatibility checks

Rows: CINE-33 · size S · track P4-C5 · group G0 (no deps; model.py dataclasses only) · schema adoption: no · depends on: none

Sample::blend (sequence.rs:262 interpolate/mix): linear requires IDENTICAL node parent/primitive/skin assignments, triangle indices, vertex counts, tangent handedness, UV/morph layouts, material assignments/variants, basis, and material/texture/skin/clip definitions (display names ignored: display_names_do_not_define_mesh_interpolation_topology); positions/UVs/colours/skin weights/morph offsets lerp, node rotations slerp, normals and orthogonalised tangents renormalised (direction attributes); incompatible layouts or unresolved directions are ERRORS with no silent hold fallback; distinct endpoints budgeted as 2x first model + second before blending (bytes); models reject invalid references, non-finite attributes, cycles. Operates on scenerender.three.model structures (read model.py to map node/mesh/material dataclasses).

* Python files: scenerender/cine/geo/mesh_blend.py (new)
* Tests: tests/test_cine_mesh_blend.py (new)
* Rust reference: crates/sr-3d/src/sequence.rs:114-359 (bytes, validate, mix, direction, interpolate); crates/sr-3d/tests/sequence.rs linear_mesh_samples_interpolate_geometry_and_normalize_direction_attributes, display_names_do_not_define_mesh_interpolation_topology; tools/evidence/cinematic-impact.json mesh_sequence_validation.oracle_cases
* **Acceptance:** 
  * port `crates/sr-3d/tests/sequence.rs::linear_mesh_samples_interpolate_geometry_and_normalize_direction_attributes`: lerp/slerp/renormalise
  * port `crates/sr-3d/tests/sequence.rs::display_names_do_not_define_mesh_interpolation_topology`: names ignored
  * non-pixel: mesh_sequence_validation.oracle_cases from cinematic-impact.json (independent oracle cases): each case's expected positions reproduced to 1e-9.
* Risks: Dataclass equality must ignore display names but not material variants

#### P4-CINE-33: Fracture kernel: seeded plane-cut partition of closed triangle solids with mass/volume conservation

Rows: CINE-28 · size L · track P4-C6 · group G2 (after 16) · schema adoption: no · depends on: P4-CINE-14, P4-CINE-16

Mirror sr_3d::fracture::fracture(vertices, triangles, Spec{pieces 1..4096 (default 8), seed u64, mass, max_bytes 256 MiB, max_work 1e8}) -> Vec<Piece{vertices centroid-relative, faces[Face{indices, source Option<tri>}], center, volume, mass}>. Steps (fracture.rs:135-270): admission before allocation; 1M element cap; weld exactly coincident positions (key = f64 bit pattern, +0/-0 equal); normalise into a unit frame (origin = bbox centre, scale = max extent; finite cube required); closure check (every edge shared by two faces with opposite orientation), orientation (negative signed volume => reverse winding), geometric INTERSECTION validation of the surface (validate.rs: overlapping source surfaces rejected rather than counting material twice; nested outward shells need cavity orientation and keep empty interiors), split into disconnected components (components > pieces is an error 'piece count is below the number of disconnected solids'); then repeatedly cut the largest-volume piece with a seeded plane (the seed advances a SplitMix-style stream `random()` at fracture.rs:271: replicate bit-exactly so plane normals/biases equal Rust's; consult the exact plane sampling code) until `pieces` reached; cut::split clips each triangle against the plane (snap |d|<1e-12), emits new cut edges, and triangulates the planar cap polygons (with holes, concave) -- Rust uses `spade` constrained Delaunay; Python: assemble cap loops by chaining cut edges, build shapely polygons (holes by containment) and triangulate with shapely.constrained_delaunay_triangles (shapely 2.1.2 installed: verified), falling back to an ear-clipping triangulator if a polygon is invalid; cap faces carry source=None; repair_collinear (cut.rs:161) ported; results are scaled back to input units, centred on each piece centroid, volume positive (fragments_never_publish_underflowed_zero_volume), mass = total mass * volume/total volume; concavities and cavities preserved; fewer-than-requested pieces never silently; deterministic across runs. Triangle lists of caps will legitimately differ from Rust (different CDT library) -- the contract is volumes, centroids, masses, closedness, source-face provenance, not triangle identity.

* Python files: scenerender/cine/geo/fracture.py (new: Spec, Piece, fracture); scenerender/cine/geo/fracture_cut.py (new: split, caps, repair_collinear); scenerender/cine/geo/fracture_validate.py (new: check, intersections, separate, properties)
* Tests: tests/test_cine_fracture.py (new)
* Rust reference: crates/sr-3d/src/fracture.rs:15-277; crates/sr-3d/src/fracture/cut.rs:1-197; crates/sr-3d/src/fracture/validate.rs:1-273; crates/sr-3d/tests/fracture.rs (9 tests); tools/evidence/cinematic-impact.json fracture_kernel_validation
* **Acceptance:** 
  * port `crates/sr-3d/tests/fracture.rs::partitions_a_solid_without_inventing_volume_and_preserves_mass_and_centroid`: sum of piece volumes == source volume; mass-weighted centroid == source centroid
  * port `crates/sr-3d/tests/fracture.rs::concave_fragments_keep_the_hole_and_partition_sampled_occupancy`: torus/concave holes preserved: Monte-Carlo occupancy partition
  * port `crates/sr-3d/tests/fracture.rs::rejects_open_nonmanifold_nonfinite_and_over_budget_inputs`: error classes
  * port `crates/sr-3d/tests/fracture.rs::source_overlaps_are_rejected_instead_of_counting_the_same_material_twice`: intersection check
  * port `crates/sr-3d/tests/fracture.rs::nested_shells_require_cavity_orientation_and_keep_empty_interiors`: cavities
  * port `crates/sr-3d/tests/fracture.rs::disconnected_material_is_not_joined_into_one_rigid_fragment`: components
  * port `crates/sr-3d/tests/fracture.rs::many_fragments_remain_closed_across_seeds`: closedness for 200 seeds
  * port `crates/sr-3d/tests/fracture.rs::fragments_never_publish_underflowed_zero_volume`: no zero-volume pieces
  * statistical: policy: Geometric, deterministic. Cut planes are seed-determined (bit-exact RNG), so per-piece volume and centroid are directly comparable with Rust once exposed through the scene path (P4-CINE-35 release masses/COM). Where only the kernel runs, statistical invariants: sum volume == source (1e-12), 1e5-point occupancy partition has zero mismatches, and for 200 seeds the piece-volume distribution (sorted) of the Python kernel equals the Rust distribution observed through the scene gate within 1e-9 relative.; tolerances: volume 1e-12; centroid 1e-12; occupancy mismatches 0; seeds: u64 seed, all bits
  * non-pixel: Kernel invariants: closedness, positive volumes, sum rules, provenance coverage (every exterior face has a source triangle).
* Risks: Cap triangulation library differs from Rust (spade); contract is geometric not triangle-identical; The surface-intersection validator (validate.rs) is an O(n^2)-ish geometric test: needs a spatial grid in numpy to stay usable at 20k triangles; Planar cut with exact snap tolerance 1e-12 is numerically delicate: port the exact tolerances

#### P4-CINE-34: Fracture surface reconstruction: exterior batches per source material, interior cut-face batch

Rows: CINE-29 · size M · track P4-C6 · group G3 (after 33) · schema adoption: yes · depends on: P4-CINE-33

sr_3d::fracture::surface: from pieces plus the source render mesh rebuild per-source-material exterior render batches and a separate INTERIOR batch: barycentric interpolation of primary/alternate UVs, map UVs and vertex colours at cut vertices from the source triangle (Face.source); normals normalised, tangents orthogonalised, NO averaging across triangle or material seams (provenance splits batches); source material IDs/variants kept; cap faces get outward FLAT normals, white colour, a canonical planar UV basis scaled by interiorUvScale with OPPOSITE handedness on opposite caps (opposite_cut_faces_share_uvs_and_keep_outward_shading_frames); frozen morph/skin applied by the caller before fracture; invalid provenance/attributes are errors; output admission before allocation (maxMemoryMiB). Outputs scenerender.three.model-compatible mesh batches so the existing renderer draws them (exterior with the source material, interior with interiorMaterial).

* Python files: scenerender/cine/geo/fracture_surface.py (new)
* Tests: tests/test_cine_fracture_surface.py (new)
* Rust reference: crates/sr-3d/src/fracture/surface.rs:1-203; crates/sr-3d/tests/fracture_surface.rs (4 tests); crates/sr-gpu/tests/fracture.rs imported_fracture_keeps_texture_maps_and_separate_interior_material; tools/evidence/cinematic-impact.json fracture_surface_schema_validation
* **Acceptance:** 
  * port `crates/sr-3d/tests/fracture_surface.rs::cut_surfaces_keep_exterior_uvs_colors_and_distinct_interior_materials`: attribute interpolation
  * port `crates/sr-3d/tests/fracture_surface.rs::provenance_splits_exterior_batches_without_merging_material_seams`: batching
  * port `crates/sr-3d/tests/fracture_surface.rs::reconstruction_rejects_broken_provenance_attributes_and_budget`: errors
  * port `crates/sr-3d/tests/fracture_surface.rs::opposite_cut_faces_share_uvs_and_keep_outward_shading_frames`: cap UV and handedness
  * non-pixel: Attribute interpolation exact (1e-12) vs independent barycentric computation.
* Risks: Cap UV basis must follow surface.rs exactly or interior textures mirror

#### P4-CINE-35: Fracture activation and 3D rigid-world registration: pieces replace the source collider, impulses, joints, mass checks

Rows: CINE-30 · size L · track P4-C6 · group G4 (after 33 and SIM 3D rigid) · schema adoption: yes · depends on: P4-CINE-33 · external: SIM-11, SIM-12, SIM-13, SIM-14, SIM-38

Physics-side integration in the (SIM-owned) Python 3D rigid world (SIM-11..14): an object3D with one rigidBody (not plane/map/volume) and an owned <fracture> (at, pieces, seed u64, interiorMaterial, interiorUvScale, impulseX/Y/Z, radialImpulse, maxMemoryMiB) registers source + pieces: World3::with_fractures semantics -- activation at the FIRST fixed-step boundary >= at (no epsilon; hidden sources wait), pieces replace the source collider inheriting pose, linear and angular velocity AT EACH PIECE COM (v + w x r), mass-fraction impulse distribution (impulse*mass_i/mass, in scene units, applied once and replayed across checkpoints), radialImpulse from the source centroid (release orientation), joint detachment (joints on the source detach at release and are restored on backward seek), hidden-piece windows (hidden pieces preserve inherited state), registration validity (mass sum check: reject ambiguous or nonconservative inputs; mesh-integrated inertia; aggregate source inertia), kinematic sources transfer ACTUAL motion not authored initial velocity, retired source no longer blocks a later body, release overflow does not partially apply, rounding never fires early, momentum conserved (partition_preserves_linear_and_angular_momentum_at_release), checkpoint replay identical both ways. Python side: a `FractureWorld` adapter in cine/sim that wraps the SIM world API; if SIM-11..14 have not landed this package is blocked (it cannot be done against the 2D world). Also SRPHYS03 physics cache (SIM-37/38: v3 cache format with fracture records) read/write: baked fractures play back identically to live.

* Python files: scenerender/cine/sim/fracture_world.py (new: registration, activation, impulses, joints, cache records); scenerender/physics.py or the SIM 3D world module (touch hooks; SIM-owned hub)
* Tests: tests/test_cine_fracture_world.py (new)
* Rust reference: crates/sr-sim/src/physics3d.rs (World3::with_fractures, activation, joint detach, SRPHYS03 records); crates/sr-sim/tests/fracture.rs (11 tests); crates/sr-eval/src/fracture.rs:1-108; crates/sr-eval/tests/fracture.rs; tools/evidence/cinematic-impact.json fracture_rigid_validation, fracture_scene_validation
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/fracture-rigid.scene.xml` (new): Box falls on a floor and fractures at t=0.5 into 8 pieces with radial impulse; kinematic spinning source; hidden-source variant; jointed source (joint detaches) Expect: piece centre-of-mass trajectories (blob/centroid tracking of coloured pieces) within 1 px early; momentum invariants exact
  * evidence cases to turn native: rigid-3d
  * port `crates/sr-sim/tests/fracture.rs::fracture_activates_on_boundary_with_source_pose_velocity_and_spin`: boundary activation + inherited kinematics
  * port `crates/sr-sim/tests/fracture.rs::partition_preserves_linear_and_angular_momentum_at_release`: momentum conservation
  * port `crates/sr-sim/tests/fracture.rs::radial_impulse_uses_release_orientation_and_mass_fraction`: radial impulse
  * port `crates/sr-sim/tests/fracture.rs::fracture_impulse_uses_scene_units_once_and_replays_across_checkpoints`: once + replay
  * port `crates/sr-sim/tests/fracture.rs::fracture_registration_rejects_ambiguous_or_nonconservative_inputs`: registration errors
  * port `crates/sr-sim/tests/fracture.rs::hidden_sources_defer_fracture_and_hidden_pieces_preserve_inherited_state`: visibility
  * port `crates/sr-sim/tests/fracture.rs::fracture_detaches_source_joints_and_restores_them_on_backward_seek`: joints
  * port `crates/sr-sim/tests/fracture.rs::kinematic_source_transfers_actual_motion_instead_of_authored_initial_velocity`: kinematic
  * port `crates/sr-sim/tests/fracture.rs::release_overflow_does_not_partially_apply_an_event`: atomic
  * port `crates/sr-sim/tests/fracture.rs::rounding_never_fires_an_event_before_its_authored_time`: tick rounding
  * port `crates/sr-sim/tests/fracture.rs::retired_source_no_longer_blocks_a_later_body`: retire
  * statistical: policy: Rigid dynamics after release are chaotic (collisions between pieces) and the rigid engine differs (Rapier 3D vs Python's 3D solver by SIM). Gate in two tiers: (1) EXACT release-instant state: piece masses, COM positions, velocities and angular velocities at the release step equal Rust's within 1e-9 (deterministic functions of kinematics and the seeded cut); (2) post-release: centroid of the whole debris cloud and total linear/angular momentum within 1% for 0.5 s, then statistical per-region occupancy EMD<=0.03.; tolerances: release state 1e-9; momentum 1e-9 invariant; cloud centroid 1%; EMD 0.03; seeds: fracture seed u64
  * non-pixel: Conservation: sum of piece momenta == source momentum at release (1e-9).
* Risks: HARD BLOCKER: SIM-11..14 (3D rigid bodies) and SIM-37/38 cache format; schedule this package only after SIM ships a 3D world with a registration seam; Rapier vs Python solver divergence after release is expected: documented-convention target

#### P4-CINE-36: Fracture scene node, rendering of exterior/interior batches, frozen animated/imported/globe sources

Rows: CINE-30, CINE-29 · size M · track P4-C6 · group G5 (after 34,35) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-34, P4-CINE-35

<fracture> evaluation on object3D: the source geometry is frozen at release time (animated, imported with skin/morph, crater-deformed, globe-with-terrain, text/extrude/clay sources via CINE-37 solids) -> kernel (CINE-33) -> surface (CINE-34) -> pieces drawn with their rigid-body poses each frame, exterior with the source material and interior with interiorMaterial/interiorUvScale; both raster and path tracer; map drape preserved on fractured globes (fractured_globe_preserves_map_drape), imported textures/maps preserved; source object hidden after release; text and clay keep their rendered solid at release (text_and_clay_keep_their_rendered_solid_at_fracture_release); memory budget maxMemoryMiB; frame-local errors; replay. Plugs into scene.py as a draw-item generator.

* Python files: scenerender/cine/render/fracture_node.py (new); scenerender/three/scene.py (touch: fracture items; shared hub)
* Tests: tests/test_cine_fracture_render.py (new)
* Rust reference: crates/sr-eval/src/fracture.rs; crates/sr-gpu/tests/fracture.rs (4 tests); crates/sr-eval/tests/fracture.rs; tests/corpus/valid/fracture.scene.xml
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/fracture-render.scene.xml` (new): Textured imported box (glTF with map) fractures; interior material red; raster and pathtrace Expect: PSNR>=30 raster; pathtrace stochastic gates S1
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/fracture.scene.xml`: Rust corpus Expect: renders
  * port `crates/sr-gpu/tests/fracture.rs::xml_fracture_renders_exterior_and_interior_in_both_modes_and_replays`: end-to-end
  * port `crates/sr-gpu/tests/fracture.rs::imported_fracture_keeps_texture_maps_and_separate_interior_material`: textures
  * port `crates/sr-gpu/tests/fracture.rs::fractured_globe_preserves_map_drape`: globe drape
  * statistical: policy: Raster deterministic: PSNR. Path traced: P4-CINE-11 S1/S4.; tolerances: PSNR>=30; S1/S4; seeds: salts 0..7
  * non-pixel: n/a
* Risks: Blocked by 35

#### P4-CINE-37: Fracture fragments and procedural solids (text, extrude, clay) as particle and pyro colliders

Rows: CINE-31 · size L · track P4-C6 · group G6 (after 35,19,22 and SIM2 clay) · schema adoption: yes · depends on: P4-CINE-19, P4-CINE-22, P4-CINE-35, P4-SIM2-11, P4-SIM2-12

Particle and pyro collider references expand a released fracture object into its ENABLED pieces (retired source surface dropped), sweeping pieces with displacement/quaternion deltas between sampled poses (particles) or smoke obstacles with translation/rotation boundary velocity incl. reflected/rotated domains (pyro), live and from SRPHYS03 caches (identical), per-consumer immutable acceleration caches charged to meshMemoryMiB (48*V + 512*T + 4096 bytes for particles; 256*V + 768*T + 4096 for smoke). Closed-solid colliders from text (shared outlines with counters from the text engine -- scenerender/three/geometry.py has 3D text/extrusion generation), path extrusions (holes preserved) and clay (frozen blob/smooth-union/fingerprint/boil, surface-nets meshing -- SIM2-19..22 provide the clay SDF and meshing; fall back to the same naive surface nets) frozen at the sampled time with FULL u64 seeds; geometry construction independent of conditions/visibility/windows while participation is sampled at runtime; static/kinematic automatic rigid surfaces preserve holes before fracture; dynamic bodies use decomposition (SIM-12). Release-time frozen clay geometry and renderer silhouette/counter preservation in raster and path trace; backward renderer replay; byte-reproducible authored font fixture (tools/fixtures/make_solid_font.py).

* Python files: scenerender/cine/sim/solid_colliders.py (new: text/path/clay -> closed mesh, budgets); scenerender/cine/sim/colliders.py (touch: expand fracture pieces; sequence after CINE-22); scenerender/cine/sim/pyro_colliders.py (touch: piece obstacles)
* Tests: tests/test_cine_solid_colliders.py (new); tests/test_cine_fracture_colliders.py (new)
* Rust reference: crates/sr-eval/src/solid.rs:1-219; crates/sr-eval/tests/fracture_colliders.rs; crates/sr-eval/tests/solid_colliders.rs; crates/sr-3d/src/clay.rs (clay solid); crates/sr-eval/src/terrain.rs:60 (collider_triangles); tools/evidence/cinematic-impact.json fracture_collider_validation, fracture_solid_sources_validation, procedural_collider_validation; tools/fixtures/make_solid_font.py; tests/corpus/valid/solid-colliders.scene.xml
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/fracture-colliders.scene.xml` (new): Particles bounce off released fragments; smoke is deflected by a fragment cloud; text "O" with counter and a clay blob as particle/smoke colliders Expect: blob_match G4 for particles; bake oracle G1 for smoke
  * scene `/home/admin/src/rs-scene-render/tests/corpus/valid/solid-colliders.scene.xml`: Rust corpus Expect: validates; bakes
  * port `crates/sr-eval/tests/fracture_colliders.rs::(5 tests)`: retired-source removal, displaced-piece contact, smoke piece occupancy + moving boundary, backward replay, live == SRPHYS03
  * port `crates/sr-eval/tests/solid_colliders.rs::(all)`: text counters and carved holes preserved; conditional discovery
  * statistical: policy: Particle part: blob_match G4 early, rest-statistics later (as P4-CINE-22). Smoke part: bake-oracle G1 (tolerance 5e-4 given moving boundaries) and solid-cell invariants.; tolerances: as cited; seeds: scene seeds
  * non-pixel: Budget formulas equal Rust for V,T pairs.
* Risks: Largest dependency fan-in of the area (FRACTURE x PYRO x PARTICLES x TEXT x CLAY x RIGID); stage by consumer

#### P4-CINE-38: Crater integration: rigid bodies and particle contacts

Rows: CINE-27 · size L · track P4-C7 · group G6 (after 30,22 and SIM 3D rigid) · schema adoption: yes · depends on: P4-CINE-13, P4-CINE-22, P4-CINE-30 · external: SIM-11, SIM-12

(Rust remaining per matrix note: rigid crate integration at fixed steps.) Owned rigid bodies (explicit static/kinematic, auto/trimesh) have triangle surfaces REBUILT at fixed-step endpoints from the object's local clock (admission 96*V + 524*T + 4096 bytes, checkpoints with revision identities, sleeping bodies woken when the surface moves under them -- a ball rests in the excavated bowl and replays); particle CCD uses linearly moving crater triangles between canonical step endpoints with barycentric surface velocities and support-contact stabilisation, curved-flight refinement for outgoing velocity, small fast spheres do not tunnel through one-interval retained geometry. Wiring through scenerender/cine/geo/crater.py (P4-CINE-30) into the SIM 3D world and into cine/sim/colliders.py (P4-CINE-22 deforming-surface API).

* Python files: scenerender/cine/sim/crater_integration.py (new); scenerender/cine/sim/colliders.py (touch, after 22/37); SIM 3D world module (touch hook; SIM-owned)
* Tests: tests/test_cine_crater_integration.py (new)
* Rust reference: crates/sr-3d/src/crater.rs; crates/sr-sim/tests/deforming_particles.rs; crates/sr-eval/tests/crater.rs; crates/sr-eval/tests/crater_memory.rs; tools/evidence/cinematic-impact.json crater_validation, crater_particle_validation, rigid_checkpoint_validation
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/crater-rigid-particles.scene.xml` (new): A ball rolls into an excavating crater and rests in the bowl; debris particles land on the moving crater floor Expect: rest height within 0.02 units; blob_match G4 early
  * port `crates/sr-sim/tests/deforming_particles.rs::(all)`: particles vs moving crater triangles, curved flight, tunnelling regressions
  * port `crates/sr-eval/tests/crater_memory.rs::(all)`: 1 MiB crater budget errors before allocation
  * statistical: policy: As P4-CINE-22 (early exact, late resting statistics).; tolerances: 0.02 units; seeds: scene seed
  * non-pixel: Replay: backward seek bit-identical.
* Risks: Blocked on SIM-11/12 for the rigid-body half; particle half can ship alone

#### P4-CINE-39: Crater integration: pyro obstacles (closed midpoint regions, MAC-face material velocity, budgets)

Rows: CINE-27 · size M · track P4-C7 · group G7 (after 30,19,18) · schema adoption: yes · depends on: P4-CINE-18, P4-CINE-19, P4-CINE-30

Pyro samples the crater kernel at step ends; the obstacle is the closed deformed surface voxelised at the MIDPOINT of the step, with MAC-face material velocities from barycentric surface motion (512*V + 800*T + 8192 charge), shared domain-relative transforms and local clocks, deterministic replay, bounded procedural geometry; horn-torus (self-overlapping) crater region REJECTED; excavation opens a native smoke emission region (crater_excavation_opens_a_native_smoke_emission_region: pyro source from a crater mesh); native raster and path-traced smoke response.

* Python files: scenerender/cine/sim/crater_pyro.py (new); scenerender/cine/sim/pyro_colliders.py (touch; sequence after CINE-19/37)
* Tests: tests/test_cine_crater_pyro.py (new)
* Rust reference: crates/sr-sim/tests/pyro_mesh.rs deforming_mesh_uses_midpoint_region_and_material_boundary_velocity; crates/sr-eval/tests/crater.rs; crates/sr-gpu/tests/crater.rs crater_excavation_opens_a_native_smoke_emission_region; tools/evidence/cinematic-impact.json crater_pyro_validation
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/crater-pyro.scene.xml` (new): Smoke released by an excavating crater, deflected by the moving floor Expect: bake oracle G1 (tolerance 5e-4)
  * port `crates/sr-sim/tests/pyro_mesh.rs::deforming_mesh_uses_midpoint_region_and_material_boundary_velocity`: midpoint voxelisation and velocity under both windings
  * port `crates/sr-gpu/tests/crater.rs::crater_excavation_opens_a_native_smoke_emission_region`: excavation emits smoke
  * non-pixel: G1 bake oracle.
* Risks: Horn-torus rejection rule must match the Rust test set

#### P4-CINE-40: Explicit resource budgets and actionable errors across cinematic features (admission accounting, frame-local diagnostics)

Rows: CINE-35 · size M · track P4-C7 · group G8 (after features) · schema adoption: yes · depends on: BOB-PHASE1, P4-CINE-02, P4-CINE-14, P4-CINE-21, P4-CINE-25, P4-CINE-30

One module cine/budgets.py consolidating the per-feature admission rules so every cinematic feature fails loudly and early: attributes maxMemoryMiB, checkpointMemoryMiB, meshMemoryMiB, surfaceMemoryMiB, terrainMemoryMiB, maxWork, maxParticles, maxEvents, maxSteps checked BEFORE allocation (cell counts x bytes, conservative workspace estimates mirroring each Rust estimator: pyro state+workspace, ocean q arrays + event sort + retained input, particles frame bytes, fracture Budget, crater 96V+524T+4096, mesh 48V+512T+4096 / 256V+768T+4096, terrain memory_cost); renderer limits: 128 MiB per volume file storage binding, 256 MiB frame cache and frame-pair budgets, 8 retained manifests, 64 media domains, f32/GPU precision errors, vertex/index/texture device limits; failure to represent a volume or meet a budget is an ERROR diagnostic (never a surface-only fallback) and is FRAME-LOCAL (other frames render). Mapping to Python diagnostics: add codes under BOB's diagnostics registry mirroring Rust messages (sr-eval/src/codes.rs lists only E01-E18; the cinematic errors are free-text in Rust -- record message classes, not codes, and flag the lack of codes in rust_may_be_wrong). Budgets are component admission estimates, NOT process RSS limits (document). This package also owns the cross-feature test matrix: each feature has a 'budget exhausted' scene whose Rust and Python diagnostics have the same severity and class.

* Python files: scenerender/cine/budgets.py (new); scenerender/cine/diagnostics.py (new: message classes)
* Tests: tests/test_cine_budgets.py (new)
* Rust reference: crates/sr-volume/src/lib.rs (CacheLimits); crates/sr-sim/src/pyro.rs (max_bytes, validate); crates/sr-sim/src/ocean.rs:118-200 (Ocean::new admission); crates/sr-sim/src/particles3d.rs:308-400 (validate), crates/sr-3d/src/fracture.rs:61-100 (Budget); crates/sr-eval/tests/crater_memory.rs, terrain_memory.rs; crates/sr-gpu/src/volume.rs (limits); crates/sr-eval/src/codes.rs
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/budget-errors.scene.xml` (new): One scene per budget attribute with a value too small (maxMemoryMiB=1 on a 128^3 pyro, ocean 3000x3000 cells, maxParticles overflow, crater 1 MiB, meshMemoryMiB tiny, terrainMemoryMiB tiny, 70 media domains) Expect: same severity and message class in Rust and Python; no frame renders partially; the next frame in the timeline still renders
  * port `crates/sr-eval/tests/crater_memory.rs::(all)`: crater memory admission
  * port `crates/sr-eval/tests/terrain_memory.rs::(all)`: terrain memory admission
  * port `crates/sr-sim/tests/pyro.rs::invalid_inputs_and_exhausted_budgets_fail_before_changing_state`: pyro
  * non-pixel: Budget table: for 12 (feature, size) pairs the Python admission estimate equals the Rust estimate within 5% (formulas are explicit constants) and failure/success decision is identical.
* Risks: Estimators are conservative approximations in Rust; mirror the formulas, not RSS; No Rust diagnostic codes for cinematic errors; classes only

#### P4-CINE-41: Schema 1.3 conformance corpus for cinematic elements (validation fixtures, rule IDs, version gate)

Rows: CINE-38 · size M · track P4-C7 · group G1 (after BOB-PHASE1; may start immediately after) · schema adoption: yes · depends on: BOB-PHASE1

Once BOB-PHASE1 vendors the 1.1.3+1.2+1.3 XSD/Schematron: run Rust's tests/corpus/valid and invalid fixtures for every cinematic family (valid: particles3d, crater, globe-relief, mesh-sequence, solid-colliders, pyro, pyro-colliders, pyro-fields, pyro-mesh, volume, volume-sequence, advected-volume, baked-volume, openvdb, openvdb-sequence, thermal-volume, ocean, fracture, version-1.0; invalid: vol1-9, v8-volumes, pyro1-8, p3d1-6, ocn1-5, geo1-3, crt1-5, frx1-4, msq1-4) through the Python validator and assert verdict AND rule id (VOL1-9, PYRO1-8, P3D1-6, OCN1-5, GEO1-3, CRT1-5, FRX1-4, MSQ1-4, V8) equal Rust's; version gate: every cinematic element in a version<1.3 document is rejected by the family gate; the SREP 31-rule scorecard as a table; plus Python-side semantic rules not expressible in Schematron (those the Rust model enforces in sr-model tests: channel grammar, bounds ordering, wave limits <=64, waterImpulse ordering, pyro cell-count limits 2..1024). Extends tools/parity/validation_corpus.py (exists) with a --family cine filter and a rule-id comparison. Rust ledger says 'final mechanical-validator coverage and complete field/default inventory are still pending reconciliation': record per-rule coverage honestly.

* Python files: tools/parity/validation_corpus.py (touch: --family cine, rule-id comparison); tests/test_cine_corpus.py (new); tests/fixtures/corpus_cine/ (new: copy of the cinematic fixtures if licence permits, else read from SR_RS_REPO)
* Tests: tests/test_cine_corpus.py (new)
* Rust reference: tests/corpus/valid/*.scene.xml, tests/corpus/invalid/{vol,pyro,p3d,ocn,geo,crt,frx,msq}*.scene.xml, v8-volumes.scene.xml; crates/sr-model/tests/{volume_rules,volume_assets,pyro_rules,particles3d_rules,ocean_rules,terrain_rules,crater,fracture,mesh_sequence}.rs; crates/sr-model/tests/corpus.rs; tools/build_corpus.py; tools/evidence/cinematic-impact.json validation_counts, volume_advection_validation.verified (245 independent schema corpus cases); docs/parity/SCHEMA-DELTA.md section 1.2->1.3
* **Acceptance:** 
  * port `crates/sr-model/tests/corpus.rs::(corpus runner)`: every valid fixture valid, every invalid fixture invalid with its rule id
  * non-pixel: Corpus gate: 100% verdict agreement on the cinematic families (24 valid + ~45 invalid fixtures) and rule-id agreement; any disagreement is a bug in the Python validator unless recorded under rust_may_be_wrong.
* Risks: The 245-case independent oracle mentioned in the Rust ledger lives outside the repo; only the checked-in corpus is portable

#### P4-CINE-42: Native 3840x2160 strict impact-film acceptance harness (reduced-scale in CI, UHD gate manual)

Rows: CINE-37 · size L · track P4-C7 · group G9 (last) · schema adoption: yes · depends on: P4-CINE-11, P4-CINE-12, P4-CINE-18, P4-CINE-23, P4-CINE-28, P4-CINE-29, P4-CINE-30, P4-CINE-36, P4-CINE-38, P4-CINE-40

Rust's own status: requirements.delivery pending, goal_complete=false; only strict single-feature UHD probes and 24-36 frame 160x120 previews exist. This package builds the ACCEPTANCE HARNESS the Python port is judged by and the reduced-scale proof: tools/parity/cine_film.py renders a multi-feature impact scene (volume smoke + pyro, ocean + whitewater, particles3D ejecta, crater, fracture, terrain) at (a) 160x120 x 24 frames in CI, (b) 960x540 x 36 frames nightly, (c) 3840x2160 strict single frames (manual) and the full-sequence run is recorded with adapter/settings, peak memory (resource.getrusage + GL allocations) and time; encodes through the existing output pipeline (scenerender/output.py) in strict mode (no fallbacks: any warning fails). Gate is 'no strict failures, frame-to-frame temporal sanity (SSIM of consecutive frames in [0.5,0.999], not all frames identical: unique frames == frame count), recorded manifest, reviewed contact sheet'. Rust parity of the film is statistical (see statistical). The full UHD film acceptance for either engine remains DEFERRED (deferred list).

* Python files: tools/parity/cine_film.py (new); tools/parity/scenes/cine/impact-film.scene.xml (new); tools/parity/scenes/cine/impact-film.manifest.json (new: expected features, sizes, budgets)
* Tests: tests/test_cine_film_smoke.py (new; 160x120x6 frames)
* Rust reference: tools/evidence/cinematic-impact.json requirements.delivery, visual_probe, thermal_preview, sequence_preview, pyro_preview, particles3d_preview, ocean_preview, ocean_uhd_probe, pending_verification; docs/ in rs-scene-render: srep-0000-cinematic-impact.md (Conformance and acceptance)
* **Acceptance:** 
  * scene `tools/parity/scenes/cine/impact-film.scene.xml` (new): Composite film exercising every cinematic feature at 160x120/24 frames (CI) and 960x540/36 frames (nightly) Expect: Python and Rust both render all frames strictly; per-frame region moments within the G2/S1 bands where deterministic; temporal sanity gates
  * statistical: policy: Film frames mix deterministic raster/field content and stochastic path-traced content. Per-frame gates: for deterministic frames PSNR>=30; for path-traced frames the P4-CINE-11 S1/S4 bands at 64 spp, K=4 seeds; sequence-level: temporal noise estimate (variance of frame difference in static regions) within 2x of Rust's at the same spp; mean luminance trajectory over time correlation >= 0.98 with Rust's; peak RSS and wall time recorded, not gated against Rust (Rust's UHD numbers are llvmpipe-specific).; tolerances: PSNR 30; S1/S4; trajectory corr 0.98; noise 2x; seeds: salts 0..3
  * non-pixel: Strict-mode run with zero warnings; unique frame count == total; manifest and contact sheet reviewed.
* Risks: The film cannot be accepted until nearly every other package lands; plan reduced-scale gates first; UHD path tracing in Python on software GL is slow: a UHD frame may take tens of minutes

## Deferred

| Row | Reason | Area |
|---|---|---|
| MAPS-01 (colorProfile attribute part) | colorProfile is declared on image/video assets (xsd 749, 793), not on map assets; behaviour belongs to COMP-20 / INTEROP-25. P4-MAPS-01 covers only the maps schema surface and geo()/geoVisible() names. | MAPS |
| MAPS-29 (HTTP fetch, User-Agent, pmtiles cache writing by the resolver) | Python has no resolve subcommand; the fetcher is RESOLVE-28. P4-MAPS-21 covers the tile plan and cache load/verify consumed by it. | MAPS |
| MAPS-31 (globe radial relief: planetRadius, terrainTileSize, terrainZoom, terrainMissing, terrainMemoryMiB) | matrix row CINE-25 owns globe relief; P4-MAPS-25 covers flat map terrain only and P4-MAPS-24 leaves the displacement hook. | MAPS |
| MAPS-30/33 Paint.External drape grey fallback | Rust draws document paints as mid-grey in the CPU drape because it has no CPU form; Python's cairo path can draw them, so no deliberate regression is planned (recorded under notes). | MAPS |
| RESOLVE-01 | Already PRESENT (render-side cache read of generated image/video/speech/music/sound-effect); only regression-gated through P4-RESOLVE-00 resolved-image scene. Open nit: generated media carries no colour tags in Python (sRGB assumed) - confirm Rust does the same before closing. | RESOLVE |
| RESOLVE-04 | PRESENT (Python reader is a superset of the Rust transcript shapes). Per-word 'emphasis' round-trip unverified: add one fixture to P4-RESOLVE-04's tests; no package needed. | RESOLVE |
| RESOLVE-32 | PRESENT (generated-video audio and speech/music/sfx as mix sources); the transcriber-hears-the-mix half is P4-RESOLVE-10. | RESOLVE |
| RESOLVE-21 (alternative backends) | faster-whisper/openai-whisper backends deliberately not planned: they cannot reproduce whisper.cpp DTW times and would change request keys; only the whisper.cpp adapter is ported (P4-RESOLVE-13). | RESOLVE |
| RESOLVE-28 (real online fetch) | No gate ever contacts a tile service; P4-RESOLVE-16/17 are tested against a local mock server only. Real-service smoke test left to manual use with --allow-cloud. | RESOLVE |
| RESOLVE-25/27 (real services) | No gate contacts OpenAI/ElevenLabs; mock server only (task rule: offline/mock providers). | RESOLVE |
| SIM2-22 (clause: clay collides in 3D physics as a sphere of its radius unless given a shape) | Physics-area behaviour (crates/sr-sim/src/physics3d.rs, README limitations); the rendering/caching part of SIM2-22 is in P4-SIM2-13. Hand the clause to the physics area; solid-colliders.scene.xml (valid corpus) and solid-colliders-boil/-blob (invalid corpus, P3D6 PYRO8) are the gates there. | SIM2/PHYS |
| SIM2-38 (clause: volume transmittance on tracer shadow rays and automatic 4-sample 2-bounce transport when a scene has volumes, render_three.rs:2444-2446) | Volumes/pyro are another area; the tracer exposes a hook (HAS_MEDIA in pathtrace.wgsl) that the volume package fills. | SIM2/VOLUME |
| CINE-37 | Full native 3840x2160 impact film (volumes, pyro, ocean, particles, crater, fracture, terrain over a reviewed multi-frame sequence with recorded adapter/memory/time). Rust itself has goal_complete=false and requirements.delivery pending (tools/evidence/cinematic-impact.json). P4-CINE-42 delivers only the acceptance harness, CI-scale and 960x540 runs and single strict UHD frames; the full UHD film is deferred until both engines can render it. | CINE |
| CINE-36 | Production-quality sample counts, sustained UHD throughput and device-limit behaviour on real GPUs are unmeasured even in Rust ('production sample counts and full impact scene remain unmeasured', cinematic-impact.json requirements.tiles). P4-CINE-12 covers exact tiling, halo, memory-independence and the 1-spp strict UHD probe only. | CINE |

## Rust possibly wrong

Mirror the observable behaviour anyway; each entry is for docs/parity/RUST-ISSUES.md.

| Row | Observation | Rust evidence | Why questionable | Area |
|---|---|---|---|---|
| MAPS-13 | Before the first flyTo begins the view is the ANIMATED base (centerLon etc.), but once it begins the first move starts from the STATIC map attributes, so an animated centre followed by a flyTo jumps at the begin time. | crates/sr-geo/src/view.rs:289-307 (view_at: returns base before flies[0].begin, then `cur = rest`), crates/sr-eval/src/geo.rs:128-133 (rest built from static attributes) | schema text says only 'the first move starts from the map's own centerLon/centerLat/zoom', which is compatible, but the discontinuity is surely unintended when centerLon is animated; world-map evidence does not animate the centre so it is unaffected. | MAPS |
| MAPS-31 | elevation() bilinear-interpolates only inside one tile, clamping at the tile edge, so heights are not continuous across elevation-tile borders (visible seams in terrain). | crates/sr-eval/src/geo.rs:314-327 (fx/fy clamp to the single tile's 0..w-1) | Neighbouring tile samples are ignored; a one-pixel step appears at every tile boundary of a DEM grid. | MAPS |
| MAPS-31 | The DEM tile cache is keyed by the archive Arc pointer address (as usize), not by the archive identity. | crates/sr-eval/src/geo.rs:281 (and 209 for decoded tiles), cache clear at >512 entries line 305 | A dropped archive and a new one can reuse the same address and return stale tiles; a global cache cleared wholesale at 512 entries also makes timing nondeterministic. Python should key by (path,size,mtime) and still match observable pixels. | MAPS |
| MAPS-20 | Two different zoom rules exist: tiles::tile_zoom rounds for all tiles while eval basemap_zoom floors vector tiles (MapLibre) and rounds raster. | crates/sr-geo/src/tiles.rs:49-53 versus crates/sr-eval/src/geo.rs:259-263 | tile_zoom looks like a stale duplicate; the resolver (sr-resolve tile_set) must choose the same rule as the renderer or it fetches the wrong zoom. Python should implement basemap_zoom as the single rule and keep tile_zoom only for the unit test. | MAPS |
| MAPS-22 | Tile budgets disagree: the renderer errors above 4096 tiles per basemap per frame, the resolver refuses above 2000 (SR_TILES_MAX). | crates/sr-gpu/src/text_basemap.rs:24 (MAX_TILES 4096) versus README Basemaps Archives / crates/sr-resolve/src/lib.rs tile_set | Two limits for one concept; a view needing 3000 tiles can never be resolved yet would be accepted by the renderer from a hand-built archive. | MAPS |
| MAPS-11 | The fitted camera cache signature uses file mtime and size but not the file content or the evaluated frame; geo data is also keyed by mtime. | crates/sr-eval/src/geo.rs:35, 89, 188, 241 | Two documents with same-size files touched within mtime granularity can hit a stale cache; harmless in a one-shot render, wrong in a long-lived server process. Python uses (path,size,mtime_ns) and should add a content hash for data under 64 MiB. | MAPS |
| MAPS-18 | Label collision is not implemented for pins and geoLayer labels, only for basemap symbols; line labels follow a straight segment not the curve. | README Maps/Basemaps notes quoted in the matrix (MAPS-18, MAPS-26) | Documented limitations, not bugs; Python must reproduce the overlap rather than 'fix' it. | MAPS |
| MAPS-09 | The frame margin is a hard-coded 64 px not exposed in the schema. | crates/sr-eval/src/geo.rs:112 (map.margin = 64.0) | Content beyond 64 px outside the frame is dropped by the projection even when a stroke is wide or a pin label long; magic number with observable effect on very large pins/labels. | MAPS |
| RESOLVE-13 | The transcriber input is a plain channel AVERAGE of the post-fader signal (every channel counted equally, LFE and surround channels included, longer channel zero padded). | /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:884-888 | For 5.1/7.1 scenes the centre (dialogue) channel is diluted by 1/6..1/8 and the LFE adds rumble, which lowers recognition accuracy; a centre-weighted or ITU downmix would be normal. Python mirrors the average (P4-RESOLVE-10) and records the discrepancy. | RESOLVE |
| RESOLVE-26 | The --allow-cloud gate is evaluated only AFTER the result store lookup, so a store hit restores a cloud-made result without --allow-cloud, and --check never reports the cloud requirement. | /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:407-435 (store at 407-421, gate at 427-435) | README presents --allow-cloud as the consent to send prompts off the machine; a hit sends nothing, so it is arguably fine, but the same document run on a cold store errors, making resolve results machine-state dependent. Mirrored as is. | RESOLVE |
| RESOLVE-08 | The sidecar embeds the full Request including the absolute cache path (output) and the absolute document directory (baseDir). | /home/admin/src/rs-scene-render/crates/sr-resolve/src/lib.rs:360,461 (req.output = t.cache.display(); Sidecar{request: req.clone()}) and protocol.rs:76-81 | Sidecars are meant to be committed next to caches, so machine-specific absolute paths and usernames leak into repositories and make diffs noisy; the key deliberately ignores them, which shows they are not needed. Python writes the same fields (interoperability) but the harness normalises them. | RESOLVE |
| RESOLVE-17 | A provider that answers {ok:true} but exits non-zero is reported as 'exited with <status> without a JSON answer', hiding that a valid answer was present; ok:false with exit 0 returns the provider's own message. | /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/mod.rs:83-92 | Misleading diagnostic for the exit/answer disagreement the README says is checked ('exit status and answer must agree'); harmless for parity but Python mirrors the message text. | RESOLVE |
| RESOLVE-24 | audioForge is asked for 16-bit output only when bitDepth == 16; a scene with audioMix bitDepth=32 (valid in the XSD enum 16/24/32) gets a 24-bit cue silently, and lib.rs audio_format falls back to 24 on any parse failure. | /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/audioforge.rs:54 and src/lib.rs:478-484 | 32 is a legal bitDepth value that is quietly downgraded and also folded into the request key as 32, so the key and the produced format disagree. | RESOLVE |
| RESOLVE-28 | The archive tile type/compression is taken from the FIRST fetched tile only; a service mixing formats (or a first tile that is gzip while later ones are not) produces an archive whose header mislabels the rest. | /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/tiles.rs:61-76 kind_of, 109-112 kind.get_or_insert | Silent mislabelling is possible for odd services; also tile_set walks EVERY frame (0..=ceil(duration*fps)) even for non-animated maps (lib.rs:845-867), which is wasteful but not wrong. Python mirrors the first-tile rule. | RESOLVE |
| RESOLVE-21 | trim_leading_silence locates the PCM payload by searching the whole file for the first 'data' bytes and assumes 16-bit mono samples, instead of walking RIFF chunks. | /home/admin/src/rs-scene-render/crates/sr-resolve/src/providers/whisper.rs:113-127 | A LIST/INFO chunk or any header text containing 'data' before the real data chunk would be mis-parsed (silent corruption of the whisper input). Works for ffmpeg's canonical output today; Python should walk chunks (equal results on valid input). | RESOLVE |
| SIM2-31 | The distance-range window clamp(1-(d/range)^4,0,1) is multiplied into the attenuation twice in a row (the line is duplicated), so the effective falloff window is the square of the documented quartic window, in both the tracer and the rasteriser. | crates/sr-gpu/src/pathtrace.wgsl:428-429 and crates/sr-gpu/src/three.wgsl:305-306 | Two identical consecutive statements look like a copy-paste error; no comment or README sentence explains a squared window. Mirror it (observable image) and record. | SIM2/LIGHTS |
| SIM2-31 | The matrix/README description 'area lights sampled on their shape, combined with MIS against BSDF sampling, light_sample returns direction, distance, pdf' does not match the code: light_sample returns only (direction, distance), there is no pdf, no area-to-solid-angle Jacobian, no MIS, and emission is never hit by BSDF rays. Sphere-area lights are sampled uniformly on the whole sphere surface (including the far side). | crates/sr-gpu/src/pathtrace.wgsl:382-403 (light_sample), :420-435 (light_radiance), :575-607 (NEE loop) | Area light brightness is artistic (intensity / (dist/100)^exponent) rather than radiometric, so converged images are not physically unbiased for area lights; the doc comment overstates the algorithm. Mirror the shader behaviour, not the prose. | SIM2 |
| SIM2-16 | Erosion droplet speed update speed = sqrt(max(speed^2 + dh*gravity, 0)) uses dh = new - old height, so droplets LOSE speed when going downhill (dh < 0) and gain speed uphill, the reverse of a physical accelerate-downhill model (inherited from the common Lague implementation of Beyer's method). | crates/sr-sim/src/erosion.rs:160 (with dh defined near :146) | Sign looks inverted relative to Beyer 2015 (v^2 += -dh*g); it changes sediment capacity (proportional to speed). Mirror exactly. | SIM2 |
| SIM2-32 | The tracer's camera lens sample is a circular aperture and ignores apertureBlades, while the raster DoF honours blade count. | crates/sr-gpu/src/pathtrace.wgsl:451-455 (sqrt(rnd())*radius, uniform angle) vs crates/sr-gpu/src/render_three.rs:391 (blades parsed for the raster DoF only) | Same attribute behaves differently by renderer; likely an omission. Mirror (circular). | SIM2 |
| SIM2-33 | The firefly clamp (20 per channel) only applies to light contributions at bounce > 0; first-bounce NEE is unclamped and the BSDF-continuation weights are never clamped, so close small bright area lights can still produce fireflies at the first hit. The clamp also biases energy (darkening highlights of bright indirect light). | crates/sr-gpu/src/pathtrace.wgsl:603-604 | A per-light, per-bounce constant clamp in linear radiance is scene-scale dependent. Mirror. | SIM2 |
| SIM2-05 | Steer-mode bounds apply a soft margin push AND then reflect/clamp velocity at the hard box edge in the same integration code as bounce mode (Bounds::Bounce / Bounds::Steer share the branch), so steer mode can still bounce. | crates/sr-sim/src/flock.rs:189-199 (match arm Bounce / Steer) | Probably intentional safety net, but README presents 'steer' as turning agents back only. Mirror. | SIM2 |
| SIM2-14 | Slime trail diffusion uses clamped-edge replication of a 3x3 mean, which does not conserve mass at the box boundary, but the unit test bounds total trail by agents*deposit/decay*1.001 as if conservation held; the bound only holds because decay removes mass first. | crates/sr-sim/src/slime.rs:136-153 (diffusion), :209-212 (mass test) | Low severity: the invariant is an upper bound, not an equality, so edge replication could inflate trail above the bound in extreme settings (diffuse 1, decay 0). Mirror; port the test with its tolerance unchanged. | SIM2 |
| CINE-09 | GPU VolumeDraw::new accepts maxSteps up to 1,048,576 while the schema limits medium/@maxSteps to 1..65536 (default 2048). Documents in the valid range are unaffected; the GPU limit is unreachable by valid documents and the check is dead or mismatched. | crates/sr-gpu/src/volume.rs:32 vs schema/scene-render-1.1.xsd:1400 | Two sources of truth for one limit; if the XSD is later widened the GPU still caps differently, and the error text names 1048576 which the user cannot author. Python mirrors the XSD limit (65536) and validates there. | CINE |
| CINE-34 | Four different fixed-step boundary conventions: Timeline floors (t-start)/dt + 1e-9; pyro uses /r - round(r)/ <= 4*eps*max(r,1) -> round else floor; ocean floors then corrects by comparing target*dt with the time; particles3D rounds with min(8*eps*/offset/, 1e-6). An event or sample exactly on a boundary can land in different steps per simulator, e.g. t = 0.85 with dt = 0.05. | crates/sr-sim/src/timeline.rs:41; crates/sr-sim/src/pyro.rs:1035-1038; crates/sr-sim/src/ocean.rs:218-227; crates/sr-sim/src/particles3d.rs:269 | Same document semantics (half-open [k*dt,(k+1)*dt) intervals) implemented four ways; the absolute 1e-9 in the generic timeline is scale-dependent and disagrees with the relative tolerance used by pyro. Python mirrors each per simulator (P4-CINE-13) and tests the boundary cases. | CINE |
| CINE-21 | boundary="open" is zero-gradient extrapolation, explicitly NOT an absorbing/radiation boundary, so waves reflect partially off 'open' edges. | crates/sr-sim/src/ocean.rs:29 | The name suggests outflow; film-scale scenes with an 'open' sea will show reflections. Documented in a comment but not in user-facing schema text. Python mirrors the zero-gradient behaviour. | CINE |
| CINE-10 | Any scene containing a volume silently switches the whole 3D pass to the path tracer with samples=4, bounces=2, denoise=true unless camera renderer=pathtrace is given; the raster lighting/shading of every other 3D object in that scene changes accordingly, and a path-tracer limit_note becomes a hard error only for volume scenes. | crates/sr-gpu/src/three.rs:1419-1425 | Implicit mode switch driven by an unrelated feature, with low defaults (4 spp) that yield visibly denoised/biased output; there is no authored attribute to keep raster surfaces with a volume. Python mirrors the forced path tracer (P4-CINE-11) and documents it. | CINE |
| CINE-12 | Pyro pressure solve runs at most pressureIterations (default 200) Jacobi-preconditioned CG iterations to a 1e-6 RMS-divergence tolerance (1/s, absolute) and an unconverged projection is a hard ERROR, with grids up to 1024 per axis admitted. | crates/sr-sim/src/pyro.rs:77-79, 610-690 (project call returns Err on non-convergence) | CG iteration counts grow with grid resolution, so 200 iterations can be insufficient on large admitted grids, turning a quality knob into a render failure; the absolute tolerance is also not scale-free. Python mirrors the error behaviour and records the default. | CINE |
| CINE-16 | Closed-mesh validation for pyro regions does not detect geometric self-intersection, whereas the fracture kernel does (validate::intersections) for the same kind of input. | crates/sr-sim/src/pyro/mesh.rs:84-198 (build) vs crates/sr-3d/src/fracture/validate.rs (intersections); matrix row CINE-16 note | Inconsistent acceptance of the same closed-solid input between consumers; a self-intersecting mesh is accepted as a pyro region and rejected as a fracture source. Python mirrors both behaviours separately. | CINE |
| CINE-35 | Evaluation diagnostics define only E01-E18 (plus E16 warning); every cinematic budget/limit/numerical failure is a free-text error without a stable code. | crates/sr-eval/src/codes.rs:1-23 | Consumers and tests cannot match cinematic failures by code; cross-engine parity can only compare message classes. Python records classes (P4-CINE-40) and will adopt codes if Rust assigns them. | CINE |
| CINE-34 | Ocean checkpoints use a fixed-capacity list that evicts the OLDEST checkpoint (remove(0)) when full instead of thinning like the generic Timeline. | crates/sr-sim/src/ocean.rs:243-247 vs crates/sr-sim/src/timeline.rs:64-71 | After a long forward run the early checkpoints are the ones lost, so backward scrubs to early times replay from t=0 (up to the 100M work-unit cap and error) while thinning would bound the worst replay. Results stay identical; only seek cost and the possibility of a work-budget error differ. Python mirrors the eviction so the same seeks fail. | CINE |
