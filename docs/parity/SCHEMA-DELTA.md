# Schema delta: Python's scene-render 1.1 vs the Rust implementation's schema

What the Rust implementation (`rs-scene-render`, HEAD f296a4c) validates that Python's
`schema/scene-render-1.1.{xsd,sch}` does not, grouped by schema version. All three steps are purely
additive: nothing was removed, moved or retyped, apart from the few attribute changes listed in the
step sections. Documents keep `version="1.1"` until they use a 1.2/1.3 construct (rule V5/V8).

## Snapshots compared

| tag | source | xsd sha256 | sch sha256 | Schematron asserts |
|---|---|---|---|---|
| py-1.1 | this repo, origin/main 6431b8b (`scenerender/schema` is a symlink to `schema/`, so the packaged and root copies are the same) | 3d0ecb49... | 30859045... | 87 |
| rs-1.1.3 | rs-scene-render 73a0e54 "Schema: vendor sr-core schema 1.1.3" = sr-core tag schema-1.1.3 (exact sha match) | c426d6aa... | bd1d5b4a... | 123 |
| rs-1.2 | rs-scene-render bd305d4, the last schema commit before 1.3: SREPs 9, 10, 11, 13 (local, in review upstream). It also carries three small non-SREP xsd edits (719ee5b, ee08198, bd305d4: image size, warp edge modes, captions lineBreaks); the SREP-only xsd is 057a785 | fa04ce8d... | 4db8cb82... | 140 |
| rs-1.3 | rs-scene-render ce17128 = working tree: cinematic 1.3 (local proposal `srep-0000-cinematic-impact.md`, not accepted upstream) | f94906a9... | f59a9263... | 185 |

Regenerate the machine diffs (`delta/*.txt` here; JSON with every path via `--json`):

```
python tools/parity/schema_delta.py A.xsd A.sch B.xsd B.sch [--json out.json]
git -C ~/src/rs-scene-render show 73a0e54:schema/scene-render-1.1.xsd > rs-1.1.3.xsd   # etc.
```

Headline counts (element paths are expanded per reachable path, so a change to a shared group multiplies; the
subtree roots and new element names are the useful numbers):

| step | new element names | complexTypes / simpleTypes | attributes added | Schematron |
|---|---|---|---|---|
| py-1.1 -> 1.1.3 | 13 (geo, map, geoLayer, graticule, route, pin, flyTo, featureStyle, flock, fluid, fluidSource, slime, erosion, blob) | +14 / +7 | +391 (~32 changed) | +36, R10 widened |
| 1.1.3 -> 1.2 | 3 (tiles, basemap, segment) + rigidBody and output audioTrack/captionTrack as new children | +4 / +3 | +301 (~35) | +17 (V5, R27-R29, R38-R41, C46-C48, C54-C59); C20, C33, R14 widened |
| 1.2 -> 1.3 | 13 (crater, fracture, medium, meshSequence, ocean, particles3D, pyro, pyroImpulse, pyroSource, volume, waterImpulse, wave, whitewater) | +13 / +7 | +192 (~33) | +45 (V8, VOL1-9, PYRO1-8, P3D1-6, OCN1-5, GEO1-3, MSQ1-4, ...); R5 changed |


## Python schema (original 1.1) -> sr-core 1.1.3

Sources: `schemas/py-1.1.{xsd,sch}` (py-render 6431b8b, sha256 3d0ecb49..) vs `schemas/rs-1.1.3.{xsd,sch}` (rs-scene-render 73a0e54 "Schema: vendor sr-core schema 1.1.3", exact sha256 match to sr-core tag schema-1.1.3, SREP 8). Delta: `delta/py-to-113.{txt,json}`. Reasons come from the Rust commits cf6b082, 4ce84a6, 5e8ff6e, 21f60ed, 73a0e54 and README.md:568.

### Summary

- The delta is purely additive: nothing removed, moved, or retyped. 13 new element names, 14 new complexTypes, 7 new simpleTypes, 1 new enum member (`object3D/@primitive` += `clay`), 36 new Schematron asserts, 1 widened (R10). `scene/@version` enum is unchanged ({1.0, 1.1}); documents stay `version="1.1"`. xs:schema/@version goes "1.1" -> "1.1.3" (cosmetic).
- The XSD documentation of py-1.1 already lacked the "shared by every engine" wording; the 483 diff lines are almost all new definitions (3410 expanded element paths, but 162 subtree roots, all from 2 groups: `assets` and `nodeChoice`, plus `object3D`).
- Four feature families are new: (1) maps/geo (`geo`, `map` assets + 6 child types), (2) 2D simulations (`flock`, `fluid`, `slime`, `erosion`), (3) clay SDF objects (`object3D/blob`) and 3D lighting/render options (AO, SSR, path tracer, contact shadows), (4) provider/colour metadata (`colorProfile`, `captionTrack/@provider|model|prompt`).
- Validator impact: the py `.sch` is a strict subset (87 vs 123 asserts). Every new construct is rejected by py's XSD today (unknown element/attribute), so a 1.1.3 document fails validation in py before it ever reaches a renderer. Nothing in py `scenerender/` implements flock, fluid, slime, erosion, geo, map, clay, colorProfile, pathtrace, AO, contact shadows (grep of scenerender: no hits).
- Python already implements, but does not validate, the particle `sprite`/`forceFields` semantics that C50, R32, R34 now enforce, and `textStyle` (R35).

### 1. New elements (XSD content-model changes)

| Element / location | Before (py) | After (1.1.3) | Notes |
|---|---|---|---|
| `scene/assets` choice | 15 asset kinds (image ... generated) | + `geo` (geoAssetType), `map` (mapAssetType) | Rust 4ce84a6 "maps from geographic data" |
| `nodeChoice` group (composition, group, sequence, repeat, symbol; 96+ paths) | group, sequence, layer, shape, object3D, camera, particleEmitter, instance, ... | + `flock`, `fluid`, `slime`, `erosion` between particleEmitter and instance | Rust cf6b082; each uses `nodeBehaviour` + `nodeAttributes` like other nodes |
| `object3D` children | animationElements, transformConstraint | + `blob` (clayBlobType) | only meaningful for `primitive="clay"` (C51) |
| `fluid` children | n/a | `nodeBehaviour` choice + `fluidSource` (fluidSourceType) | |
| `map` children | n/a | `geoLayer`, `graticule`, `route`, `pin`, `flyTo` + animationElements | document order = paint order |
| `geoLayer` children | n/a | `featureStyle` + animationElements | |

### 2. New complexTypes and their key attributes (defaults/facets)

| Type (element) | Required | Notable attributes and defaults | Notes |
|---|---|---|---|
| geoAssetType (`geo`) | id, src | format enum {auto*, geojson, topojson, kml, gpx}; object; assetProvenance | GeoJSON/TopoJSON/KML/GPX; polygons are great-circle arcs |
| mapAssetType (`map`) | id, width, height (positiveInteger) | projection=equal-earth; parallels; centerLon/centerLat; zoom=0; rotation=0; fit (IDREFS); fitPadding=0; background; outline; outlineWidth=1; precision=0.7071 | zoom = doublings from fitted view; animatable; 3D tilt via layer rotation |
| geoLayerType | geo (IDREF) | filter; fill=#D9D9D9; stroke; strokeWidth=0.5; opacity=1; fillBy; palette (colorListType); domain (numberListType); scale enum {linear*, log, sqrt, quantize}; noData; keyBy=id; label; textStyle (IDREF); pointRadius=3; progress=1 | choropleth in sRGB; `prop=value` / `prop!=value` filter |
| featureStyleType | key | fill, stroke, strokeWidth, opacity=1 | animatable overrides by @keyBy value |
| graticuleType | none | step=10; stroke=#FFFFFF40; strokeWidth=0.5; opacity=1 | |
| pinType | lon, lat | radius=6; fill=#FF3B30; stroke=#FFFFFF; strokeWidth=2; opacity; label; textStyle; labelDx=10; labelDy=0 | hidden on far side of globe |
| routeType | none (C45: points or geo) | points (geoPointsType); geo (IDREF); filter; progress=1; stroke=#FFFFFF; strokeWidth=3; dash; opacity; headRadius=0; headFill | progress is ground distance |
| flyToType | begin, lon, lat | duration; zoom=0; rho=sqrt2 (1.4142135623730951); easing=ease-in-out | van Wijk-Nuij path; first move starts at map centre/zoom |
| flockType | width, height | count=200 (1..100000); seed; speed=120; maxSpeed=240; maxForce=400; perception=60; separationDistance=24; separation=1.5; alignment=1; cohesion=1; bounds=steer; edgeMargin=40; forceFields; size=6; color=#FFFFFFFF; shape enum {disc, streak*, sprite}; sprite; trail=0; blend; effects | Reynolds boids, drawn as particles in frame space |
| fluidType + fluidSourceType | width, height | resolution=128 (8..1024); viscosity, diffusion, dissipation, velocityDissipation, vorticity = 0; buoyancy=0; iterations=40 (<=500); bounds enum {closed*, open, wrap}; forceFields; blend; effects. Source: x,y=0; radius=20; color; density=1; velocityX/Y=0; start=0; end | Stam stable fluids; C52: end > start |
| slimeType | width, height | resolution=256 (8..2048); agents=20000 (<=2,000,000); seed; spawn enum {random, disc*, ring, center}; sensorAngle=30; sensorDistance=9; turnAngle=45; speed=60; deposit=1; decay=0.1; diffuse=0.5; saturation=4; color=#FFD27AFF; colorLow=#00000000 | Physarum (Jones 2010) |
| erosionType | width, height | resolution=256 (16..2048); seed; heightmap (IDREF); octaves=6 (<=12); frequency=3; droplets=20000; inertia=0.05; capacity=4; erodeRate=0.3; depositRate=0.3; evaporation=0.01; gravity=4; erodeRadius=3 (<=16); relief=1; sunAzimuth=315; sunElevation=45; colorLow=#3B4A2FFF; colorHigh=#E8E2D0FF | Beyer 2015 droplets |
| clayBlobType (`blob`) | none | shape enum {sphere*, box, capsule, torus}; x,y,z=0; radius=30; width/height/depth=60; length=60; rotationX/Y/rotation=0; blend=10; subtract=false | SDF smooth union; animatable |

### 3. New simpleTypes

| Type | Definition | Notes |
|---|---|---|
| colorListType | xs:list of colorType | geoLayer/@palette |
| colorProfileType | enum {embedded, declared} | see section 4 |
| latitudeType / longitudeType | xs:double in [-90,90] / [-180,180] | pin, flyTo, map centre |
| geoPointsType | pattern `lon,lat` pairs, whitespace separated, >= 2 points | route/@points |
| projectionType | 11 values: mercator, web-mercator, equirectangular, equal-earth, natural-earth, albers, lambert-conformal, orthographic, stereographic, azimuthal-equal-area, azimuthal-equidistant | d3-geo formulas; web-mercator = 512 px world at zoom 0 |
| simBoundsType | enum {steer, bounce, wrap} | flock/@bounds |

### 4. Attributes added to existing py elements

| Element | Attribute | Before | After | Notes |
|---|---|---|---|---|
| image, imageSequence | colorProfile | absent | colorProfileType default=embedded | embedded ICC / H.273 cicp / nclx beats colorSpace+transfer; `declared` restores them. Rust 4ce84a6. Decoders must read embedded profiles |
| camera | ambientOcclusion | absent | boolean=false | screen-space AO (raster) |
| camera | aoRadius / aoIntensity | absent | positiveDecimal=40 / nonNegativeDecimal=1 | |
| camera | screenSpaceReflections | absent | boolean=false | |
| camera | renderer | absent | enum {raster*, pathtrace} | progressive path tracer for final frames |
| camera | pathSamples | absent | positiveInteger=64, max 65536 | |
| camera | maxBounces | absent | positiveInteger=4, max 64 | |
| camera | denoise | absent | boolean=true | |
| light | contactShadows / contactShadowLength | absent | boolean=false / positiveDecimal=20 | screen-space contact shadows |
| object3D | primitive | 10 values | + `clay` | needs >= 1 blob (C51) |
| object3D | resolution | absent | positiveInteger 8..256, default 64 | clay marching resolution |
| object3D | fingerprints, boil | absent | nonNegativeDecimal=0 | clay surface noise (fingerprints, temporal boil) |
| object3D | seed | absent | xs:unsignedLong | |
| captionTrack | provider / model / prompt | absent | string default=whisper / base / (none) | resolve-step metadata only; render reads @cache. Rust 5e8ff6e |

### 5. Enumerations and facets summary

Only change to an existing enumeration: `object3D/@primitive` += `clay`. All other enums/facets listed above are on new items. No default or facet of any pre-existing py attribute changed. Existing `particleEmitter` already has shape={..sprite..}, sprite, spriteCols/Rows/Fps, forceFields in py (py-1.1.xsd:1665-1671).

### 6. Schematron rules

Counts: py 87 -> 1.1.3 123 (+36 asserts, 0 removed, 1 changed). Patterns p42, p50-p60 are new.

| Rule | Context | Before | After | Notes |
|---|---|---|---|---|
| R10 | `*[@parent][@id]` | context limited to layer, shape, group, sequence, instance, repeat, particleEmitter, object3D, adjustment, include | any element with @parent and @id | self-parent check now also covers camera, light, flock, fluid, slime, erosion, ... Validator-only |
| V6 | /scene[@version='1.0'] | absent | forbids audioEffect, blob, burst, bus, destination, erosion, flock, fluid, geo, map, master, param, pin, poster, representation, shake, slime, span, thumbnail | version gate; 1.0 docs cannot use these |
| V7 | same | absent | forbids object3D primitives capsule, clay, cone, cylinder, extrude, text, torus | version gate |
| R36 | geoLayer | absent | @geo must name /scene/assets/geo | |
| R37 | route | absent | @geo (if present) must name a geo asset | |
| C45 | route | absent | needs @points or @geo | |
| R26 | map | absent | every id in @fit must name a geo asset | uses `str:tokenize` (EXSLT) |
| C53 | geoLayer[@domain] | absent | @domain values strictly increase | |
| C50 | flock, particleEmitter with shape=sprite | absent | requires @sprite | applies to existing py particleEmitter |
| R32 | flock, particleEmitter with @sprite | absent | must name image, imageSequence, video or generator asset | applies to existing py particleEmitter |
| R33 | erosion[@heightmap] | absent | must name an image asset | |
| R34 | any element with @forceFields | absent | each id must name physics/forceField | applies to existing py particleEmitter |
| R35 | any element with @textStyle | absent | must name styles/textStyle | applies to existing py elements (text, chart, formula, geoLayer, pin) |
| C51 | object3D[@primitive='clay'] | absent | needs at least one `blob` | |
| C52 | fluidSource[@start and @end] | absent | end > start | |
| R30-colorEnd, -colorHigh, -colorLow, -headFill, -noData, -outline, -paint2 (7) | any element with one of those attrs | absent | `url(#id)` must name an element of /scene/paints | `colorEnd`, `paint2`, `outline` etc. already exist on py elements |
| R31-attenuationColor, -baseColor, -colorEnd, -colorHigh, -colorLow, -emissive, -foreground, -headFill, -keyColor, -noData, -outline, -paint2, -shadowColor, -sheenColor, -specularColor (15) | any element with one of those attrs | absent | `var(--name)` must name a styles/token | covers existing material/shadow/chroma-key colours: a py document with a dangling `var(--x)` there now fails |

Note on numbering: the Rust commit message says the geo reference checks "are R36 and R37 (they shared R24/R25)"; py has the R24-* and R25-* families (paint url(#id) / token var(--name) checks on fill, stroke, color, background, paint, activeColor, highlight, strokeColor; py-1.1.sch) but no plain R26; the geo assertions take the new ids R36/R37 and R26 (map/@fit) is first defined in 1.1.3. Neither V5 nor R27-R29 appear until 1.2.

### 7. Ranking (what a Python implementation must do)

1. Validator-blocking (highest): the XSD rejects every new element, attribute and `clay`. Until the schema is replaced, no 1.1.3 document loads. Vendoring the 1.1.3 pair is mandatory for interchange.
2. Validator-only rules affecting existing py features: R10 widening, R30/R31 (url(#)/var(--) references on existing colour attributes), R32, R34, R35, C50 on particleEmitter, V6/V7. These change accept/reject behaviour for documents that py already renders.
3. Rendering, large: `geo`/`map` + geoLayer/graticule/route/pin/flyTo/featureStyle, 11 projections with clipping, TopoJSON/KML/GPX parsing, `geo()`/`geoVisible()` expression functions (XSD text lines 398-399; py expr.py has neither).
4. Rendering, large: simulations flock, fluid, slime, erosion (fixed 1/60 s step, checkpoint per simulated second, seeds, force fields). Boids reuse the particle drawing path.
5. Rendering, medium: clay SDF objects (`blob`), camera AO/SSR/path tracer/denoise, light contactShadows. A Python path tracer is a large item; raster-only fallback with a FEATURES.declare of PARTIAL is acceptable for `renderer="pathtrace"`.
6. Rendering, small: `colorProfile` (embedded ICC / cicp precedence for stills).
7. Accept-only: `captionTrack/@provider|model|prompt` (render reads @cache; py captions.py already documents this), `object3D@seed|resolution|fingerprints|boil` unless clay is implemented.

### Consequences for the Python implementation

Validator-only:
- Replace `schema/scene-render-1.1.xsd` and `.sch` (single copy; `scenerender/schema` is a symlink to `../schema`, loaded by `scenerender/schema.py` SCHEMA_PATH line 18, Schematron derived at `splitext(path)+".sch"`, line 62). Record the SHA-256 pair (c426d6aa.., bd1d5b4a..) like Rust's `schema/UPSTREAM`.
- `schema.py` type table (`TypeInfo`) must cope with xs:list (colorListType), unions already in use, and anonymous restricted attributes; run the existing corpus plus per-assert cases (Rust added 160 documents, one per new assert).
- `references.py` / `document.py` ID-ref resolution (textStyle at `document.py:394`, forceFields in `nodes/particles.py`, sprite) should stay consistent with R32-R35 so semantic validation and renderer agree.
- lxml's `isoschematron` needs the EXSLT `str:tokenize` namespace for R26/R34; confirm it works (py 1.1 rules already use `str:`).
- `coverage.py` / `registry.py` / `FEATURES.declare` need entries for each new element so unsupported ones report instead of being silently dropped.

Rendering work (likely modules under `/home/admin/codebases/py-render/scenerender`):
- Maps/geo: new `assets/geo.py` and `assets/map.py` (parsers, projections, clipping, fly-to), hooked in `assets/__init__.py`, `assets/vector.py` for path drawing, `expr.py`/`vexpr.py` for `geo()` and `geoVisible()`, `text_animators.py`/`assets/text.py` for labels (`textStyle`).
- Simulations: new node types in `nodes/` (e.g. `nodes/sims.py`) registered from `nodes/core.py`/`registry.py`; flock reuses `nodes/particles.py` and `nodes/particle_paints.py` (sprite shape) and force fields in `physics.py`; fluid/slime/erosion produce a raster in the node box; cache keying in `compositor.py` (commit ee38da0 style cache keys must include simulated content); `scheduling.py` for the fixed-step clock.
- Clay: `nodes/scene3d.py` + `three/geometry.py` (SDF to mesh, marching at @resolution), `three/materials.py` for fingerprints/boil.
- Render options: `camera.py`, `three/renderer.py`, `three/postfx.py` (AO, SSR, denoise), `three/lights.py` and `three/shaders.py` (contact shadows), `three/scene.py` for the path tracer option.
- Colour: `assets/image_io.py`, `assets/image_pixels.py`, `colorimetry.py` for embedded ICC / cicp precedence and `colorProfile="declared"`.
- Accept-only: `captions.py` (provider/model/prompt are ignored; resolve step is out of scope).

---

## sr-core 1.1.3 -> local 1.2 (SREPs 9 tiles/basemaps, 10 draped ground/globes, 11 rigid bodies on object3D, 13 output segments)

Sources: `schemas/rs-1.1.3.{xsd,sch}` vs `schemas/rs-1.2.{xsd,sch}` (Rust bd305d4); line numbers below are in rs-1.2.xsd / rs-1.2.sch. Delta: `delta/113-to-12.txt`. The change is purely additive (0 removed, 0 moved, no simpleType changed). XSD: 3 new element names (`tiles`, `basemap`, `segment`), 4 new complexTypes, 3 new simpleTypes, 301 attributes added (mostly one group fanned over ~96 `flock/fluid/particleEmitter` paths and 32 `object3D` paths). Schematron: 123 -> 140 rules (+17: V5, R27-R29, R38-R41, C46-C48, C54-C59), 3 widened (C20, C33, R14). Version enum becomes {1.0,1.1,1.2}. Note the xsd root still carries version="1.1.3" (not bumped). Also in the delta but not tied to a listed SREP: `captionTrack@lineBreaks`, `audioRoleType` refactor, documentation-only edits (keyframe interpolation sentence at 349; anchorX/anchorY note at ~1417).

### Summary
- SREP 9 adds a `tiles` asset (PMTiles archive or resolved online tile service) and a `basemap` child of `map` (vector MVT via MapLibre style, or raster warped into the map projection). Rendering never fetches tiles.
- SREP 10 adds `object3D@primitive` values `map` and `globe` that drape a map asset on a ground plane (optional terrain/buildings) or a sphere.
- SREP 11 adds an `object3D/rigidBody` child plus 3D extensions of the 2D physics model (`gravityZ`, `forceZ`, `z`, joint axes, `ball` joint, `useForceFields`).
- SREP 13 adds `output/segment` (cuts, speed, time remap, per-segment transitions), per-output `audioTrack`/`captionTrack`, and audio selection/overlay/joinFade attributes on `output`.
- Gate: V5 rejects every 1.2 construct in documents declaring version 1.0/1.1.

### SREP 9: tiles and basemaps

| Item | Before | After | Notes |
|---|---|---|---|
| element `assets/tiles` (tilesAssetType, xsd 1148-1168) | absent | new; added to `assetsType` choice before `map` (1354) | Required `@id` (xs:ID). |
| `tiles@src` anyURI | - | new | PMTiles archive: vector (MVT) or raster (PNG/JPEG/WebP). |
| `tiles@url` string | - | new | Template with `{z}`,`{x}`,`{y}`, optional `{s}` subdomain. A resolve step run before rendering downloads needed tiles into `@cache`; rendering never fetches. |
| `tiles@cache` anyURI, `@cacheSha256` sha256Type | - | new | Cache is a PMTiles archive pinned by hash. |
| `tiles@tileSize` positiveInteger | - | new, no default in xsd | Doc: default 512 vector, 256 raster (applied by consumer). |
| `tiles@minZoom` / `@maxZoom` tileZoomType | - | new, defaults 0 / 19 | Bound zooms fetched from a service. |
| `tiles@attribution` string, `@sha256` sha256Type | - | new | Credit line; archive hash. |
| simpleType `tileZoomType` (1145) | absent | nonNegativeInteger, maxInclusive 24 | Facet 0..24. |
| element `map/basemap` (basemapType, 1169-1188) | absent | new; first in `mapAsset` choice (1312) | Also accepts animationElements (animate/expression/motionPath/link), so its attrs are animatable. |
| `basemap@tiles` IDREF | - | new, required | Must name `assets/tiles` (R27). |
| `basemap@mapStyle` string | - | new | MapLibre style JSON file, or `protomaps-light` / `protomaps-dark` (default). Vector tiles only. |
| `basemap@opacity` unitDecimal | - | default 1 | |
| `basemap@detail` double | - | default 0 | Shifts tile zoom; positive = finer. |
| `basemap@labels` boolean | - | default true | false drops style text. |
| `basemap@attribution` boolean | - | default true | Draw credit in map corner. |
| `basemap@id` ID | - | new, optional | |

| Rule | Context | Test / intent |
|---|---|---|
| C46 (sch 286) | `tiles` | `@src or (@url and @cache and @cacheSha256)`: an online source must be pinned/cached. |
| R27 (sch 283) | `basemap` | `/scene/assets/tiles[@id=current()/@tiles]`: basemap references a tiles asset. |
| V5 | /scene | gate (see version section). |

Rendering implied: tile-pyramid reader for PMTiles (header, directory, gzip/zstd, MVT decode), MapLibre-style vector drawing (Protomaps default styles must be bundled), raster tile warp into the map projection and painting beneath vector content, tile zoom selection with `detail`, label suppression, attribution text in the corner. A separate resolve/prefetch step (network) with cache hash check; render stays offline.

### SREP 10: draped ground and globes (object3D primitive map/globe)

| Item | Before | After | Notes |
|---|---|---|---|
| `object3D@primitive` enum (xsd ~2024) | ... clay | + `map`, `globe` | |
| `object3D@map` IDREF | - | new | Map asset to drape. Required when primitive is map/globe (C47); must name `assets/map` (R28). |
| `object3D@terrain` IDREF | - | new | `assets/tiles` of elevation (R29). Needs web-mercator map (doc). |
| `object3D@terrainEncoding` | - | enum `terrarium`(default) / `mapbox` | Terrarium vs Mapbox Terrain-RGB decode. |
| `object3D@exaggeration` nonNegativeDecimal | - | default 1 | Height = true height x exaggeration, raised toward camera (-z). |
| `object3D@buildings` boolean | - | default false | Extrudes footprints of the map's vector basemap to their heights, in `@material`. Web-mercator only. |
| `object3D@textureSize` | - | positiveInteger 64..8192, default 2048 | Drape size px along a map's longer side; for globe the drape height (width = 2x). |

Semantics (xsd doc): `map` = ground in the object's xy plane, one map pixel per scene unit, draped with the map as drawn, terrain grid of `@resolution` cells (existing attr). `globe` = sphere of `@radius`, poles on y, 0 deg longitude facing camera, drape shows whole world.

| Rule | Context | Intent |
|---|---|---|
| C47 | object3D | `not(@primitive='map' or 'globe') or @map` |
| R28 | object3D | `@map` names `/scene/assets/map` |
| R29 | object3D | `@terrain` names `/scene/assets/tiles` |

Rendering implied: render the map asset to a texture (needs SREP 9 basemaps for imagery), build a displaced grid from decoded elevation tiles, extrude building footprints from MVT heights, UV-map an equirectangular world drape on a sphere. Depends on the map asset (geo/map rendering) existing in the Python renderer.

### SREP 11: rigid bodies on object3D (3D physics)

| Item | Before | After | Notes |
|---|---|---|---|
| element `object3D/rigidBody` (rigidBody3DType, xsd 1591-1636) | absent | new; added to object3D choice (2013) | Appears on ~32 object3D paths (inside group/repeat/sequence/symbol). |
| `rigidBody@type` | - | enum static/kinematic/dynamic (default dynamic) | Kinematic follows its animation. |
| `rigidBody@shape` | - | enum auto, box, sphere, capsule, cylinder, cone, convex-hull, trimesh, decomposition (default auto) | auto = primitive's own form, convex hull for other meshes; plane = thin box; trimesh exact triangles; decomposition = convex parts of closed mesh. |
| `mass` positiveDecimal=1; `friction` unitDecimal=0.5; `restitution` unitDecimal=0; `linearDamping`/`angularDamping` unitDecimal=0.01 | - | new | |
| `velocityX/Y/Z`, `angularVelocityX/Y/Z` double=0 | - | new | px/s in scene axes (y down, z away from camera); deg/s about scene axes. |
| `collisionGroup` nonNegativeInteger=0; `collidesWith` string=all | - | new | |
| `sensor`, `fixedRotation`, `bullet` boolean=false | - | new | |
| `activateAt` double=0 | - | new | Seconds. |
| `physics@gravityZ` double=0 | - | new | m/s^2 toward camera. |
| `forceField@forceZ` double=0, `@z` double=0 | - | new | Radial centre depth; z force. |
| `constraint@z`, `@axisX/Y/Z` double | - | new, optional | Anchor z; hinge/motor default axis z, slider x. |
| `constraint@type` enum | ... | + `ball` | Ball-and-socket. |
| `flock/fluid/particleEmitter @forceFields` IDREFS | no doc | doc added: absent = all scene force fields, present = only those | Semantics clarified (no schema change). |
| `flock/fluid/particleEmitter @useForceFields` boolean=true | - | new | false = no field acts, whatever forceFields lists. ~96 paths. |

| Rule | Context | Intent |
|---|---|---|
| C48 (sch 374) | object3D/rigidBody | `not(@shape='trimesh') or @type='static' or 'kinematic'`: trimesh cannot be dynamic. |

Rendering implied: a 3D rigid-body engine (the Rust side presumably uses a physics crate) stepped on the same `physics@start`/`fixedStep` checkpoints; body poses drive object3D transform (position + rotations in parent frame) overriding its own attrs; mesh-derived colliders (hull, trimesh, convex decomposition); 3D joints; 3D force fields; z gravity. Deterministic and seekable like the 2D physics.

### SREP 13: output segments

| Item | Before | After | Notes |
|---|---|---|---|
| `output` content choice (xsd 3594-3596) | poster, thumbnail, destination | + `segment`, `audioTrack`, `captionTrack` | audioTrack/captionTrack reuse audioTrackType/captionTrackType. |
| element `output/segment` (segmentType, xsd 3038-3066) | absent | new | Children (unordered choice): `timeRemap`, `transition`, `animate`. Played in document order. |
| `segment@id` ID; `@from`,`@to` double; `@fromMarker`,`@toMarker` IDREF | - | new | Composition seconds or markers. |
| `segment@speed` positiveDecimal | - | default 1 | |
| `segment@audio` | - | enum stretch(default)/resample/mute | stretch keeps pitch; resample changes pitch; mute silences. |
| `segment@focusX/@focusY` unitDecimal | - | new, animatable in segment time | Replace layout reframing focus for crop and fit-blur. |
| `segment/timeRemap` (timeRemapType) | - | reused | key/@time = segment time from 0, key/@value = composition time (ramps, freezes, reversals). |
| `segment/transition` (transitionType) | - | reused | Joins this segment to the next. |
| `output@audioTracks` IDREFS, `@audioRoles` audioRoleListType, `@audioBuses` IDREFS | - | new | Selection is a union (source matching any plays); none given = all sources play. "With segments". |
| `output@overlay` IDREF | - | new | A `symbol` drawn over output picture in output time. |
| `output@joinFade` nonNegativeDecimal | - | default 0.01 | Equal-power audio crossfade s centred on each cut. |
| simpleType `audioRoleType` (3027) / `audioRoleListType` | inline enum on `audioTrack@role` | named type, same 7 values (dialogue, voiceover, music, effects, ambience, audio-description, other); list type new | Refactor for reuse in `output@audioRoles`. No value change. |
| `captionTrack@lineBreaks` | - | enum greedy(default)/source | Not SREP-bound; `source` ends a line at each cue newline, blank lines add no rows. |

| Rule | Context | Intent |
|---|---|---|
| C54 | output | `not(segment) or (start absent/0 and no @end)`: segments replace start/end. |
| C55 | segment | needs from-or-fromMarker AND to-or-toMarker, or a timeRemap. |
| C56 | segment | if both @from,@to: 0 <= from < to <= project/@duration. |
| C57 | segment | each end time or marker, not both. |
| C58 | segment | at most one timeRemap and one transition. |
| R38 | segment | markers must exist in /scene/markers. |
| C59 | segment/transition | no from/to, not morph and not luma (joins two rendered pictures). |
| R39 | output | audioTracks name audioMix tracks; audioBuses name audioMix buses (uses `str:tokenize`, EXSLT). |
| R40 | output | overlay names a `/scene/symbols/symbol`. |
| R41 | output/captionTrack[@transcribe] | transcribe names an audioTrack that is a child of the same output. |

### Amended rules and version

| Rule | before | after | Notes |
|---|---|---|---|
| C20 (transition) | `@from or @to` | `@from or @to or parent::segment` | Segment transitions carry no from/to (pairs with C59). |
| C33 (captionTrack@transcribe) | must name `/scene/audioMix/audioTrack` | `... or parent::output` | Output-level captionTrack validated by R41 instead. |
| R14 (output@burnCaptions) | names `/scene/captions/captionTrack` | `... or captionTrack[@id=current()/@burnCaptions]` | Can burn an output-local caption track. |
| scene@version enum (xsd 3717) | 1.0, 1.1 | 1.0, 1.1, 1.2 | |
| V5 (sch 22-24, pattern p1b) | - | context `/scene[@version='1.0' or '1.1']`: `not(assets/tiles | .//basemap | .//object3D/rigidBody | .//object3D[@primitive='map' or 'globe'] | output/segment | output/audioTrack | output/captionTrack)` | Version gate. Does not list new attributes (`@useForceFields`, `@gravityZ`, `ball`, `lineBreaks`) so those are not gated. |

### Consequences for the Python implementation

Validator-only (schema/schema.py, schema/ copy of xsd+sch):
- Vendor 1.2 xsd/sch (py-1.1 is a distinct file from rs-1.1.3, so the 1.1.3 delta must land first); update `SCHEMA_PATH` consumers. Python's .sch is found beside the xsd, silently skipped if absent. Check lxml ISO-Schematron handles `str:tokenize` (R39) and `current()`; `sch:let` in `output`.
- Accept `version="1.2"` and V5 gating; C20/C33/R14 relaxations.
- `lineBreaks`, `audioRoleType`, `useForceFields`, `gravityZ`, `forceZ`, `ball` are parseable immediately; they need rendering work to take effect or must be warned as unsupported.

Rendering work (scenerender modules; none currently reference tiles/basemap/rigidBody or a map asset by grep):
- SREP 9: new `assets/` loader (e.g. a tiles/basemap module beside `assets/vector.py`, `assets/image.py`), PMTiles + MVT + MapLibre-style drawing, raster warp; registry/`references.py` for IDREF resolution; `publish.py`/`cli.py` for the offline resolve/cache step and sha256 pinning; `document.py` for asset parsing. The `map` asset (mapAssetType) is absent from py-1.1.xsd entirely and no map rendering code was found, so the 1.1.3 delta (map/geo assets) is a prerequisite for SREPs 9 and 10.
- SREP 10: `three/scene.py` and `three/geometry.py` (`primitive_geo`), `nodes/scene3d.py` for map/globe primitives, terrain grid, building extrusion, drape texture from the map asset (`three/materials.py`/`texture_image.py`); CPU fallback in `nodes/scene3d.py` needs a warning for unsupported primitives.
- SREP 11: `physics.py` is 2D only (Body/Part, Seekable checkpoints); a 3D engine (new module, e.g. `physics3d.py`) plus `nodes/scene3d.py` and `three/scene.py` to apply poses; `constraints.py` for 3D joints/ball; force-field z in `physics.py:field_accel`; `useForceFields` filtering in `physics.py` and `nodes/particles.py`/`particle_collisions.py`.
- SREP 13: `output.py` (Job, jobs_from_args, begin_frame/render_frame time mapping, encode pipeline), `render.py` (composition-time mapping from segment time, timeRemap, focus override via `layout.py`/`camera.py` crop and fit-blur), `transitions/` (segment joins between rendered pictures, no morph/luma), `audio/` (stretch/resample/mute, equal-power joinFade, track/role/bus selection), `captions.py` (output-local captionTrack, transcribe from output audioTrack, lineBreaks=source), symbol overlay drawn in output time via `compositor.py`. Note ffmpeg is absent in this environment (per memory), which constrains audio verification.
- Test fixtures: C54-C59, R38-R41, C46-C48, R27-R29 each need one valid and one invalid document; V5 needs a 1.1 document using each trigger.

---

## local 1.2 -> local 1.3 cinematic (volumes, pyro, particles3D, ocean, mesh sequences, fracture, craters, globe elevation)

Sources: `schemas/rs-1.2.{xsd,sch}` (bd305d4) vs `schemas/rs-1.3.{xsd,sch}` (ce17128; identical to Rust HEAD f296a4c); `delta/12-to-13.txt`; intent/status from `srep-0000-cinematic-impact.md` (header: "Status: Draft - implementation in progress", "Schema-Version: 1.3 (proposed)") and `tools/evidence/cinematic-impact.json` (`requirements[]`). XSD line numbers are rs-1.3.xsd, SCH ids are rs-1.3.sch.

### Summary

- Purely additive (delta: 0 removed, 0 moved). 13 new element names (crater, fracture, medium, meshSequence, ocean, particles3D, pyro, pyroImpulse, pyroSource, volume, waterImpulse, wave, whitewater), 13 new complexTypes, 7 simpleTypes, 1 attributeGroup (`pyroShape`), 1 version enum value, 6 new object3D attributes (5 globe-terrain: planetRadius, terrainTileSize, terrainZoom, terrainMissing, terrainMemoryMiB; plus `volume`; terrainEncoding already exists in 1.2), 1 new `@primitive` value, and 45 new Schematron asserts (1 existing assert, R5, widened). Element-path count 13737 -> 15531 (+1794, one group change multiplied across reachable paths).
- New content-model slots: `assets` += meshSequence, volume; `nodeChoice` += particles3D, ocean (all containers); `object3D` children += medium, pyro, crater, fracture.
- Every new element/feature is gated behind `scene/@version="1.3"` by a Schematron rule (V8 for the volume family; FRX1, CRT1, MSQ1, GEO1, OCN1, P3D1 individually), not by the XSD. The XSD root `version="1.1.3"` attribute is not bumped (unchanged from 1.2).
- SREP status: every family is `in_progress` in the ledger except `tiles` (UHD tile buffers, `verified`, renderer-only, no schema) and `delivery` (`pending`). `goal_complete` is not claimed; SREP says the schema/validator reconciliation and the "final 31-rule scorecard" are pending. The XSD/SCH here are nonetheless the executable form the Rust validator uses.
- Python 1.1 schema has none of these; the py-1.1 xsd already defines `rigidBodyType` (line 1293), which the new fracture/crater/MSQ rules depend on.

### 1. Version rule (V8 and the enum)

| Item | Before (1.2) | After (1.3) | Notes |
|---|---|---|---|
| `scene/@version` enum (xsd 4101) | 1.0, 1.1, 1.2 | + `1.3` | Only enumeration change to existing simple types. |
| **V8** (sch 110, rule ctx `/scene[@version!='1.3']`) | absent | `not(assets/volume\|.//medium\|.//pyro\|.//object3D[@primitive='volume' or @volume])` | Gates the VOL/pyro family only. Note: gating is by context `@version!='1.3'`, so it fires on 1.0-1.2 docs. Other families have their own `X1` rule: FRX1, CRT1, MSQ1, GEO1, OCN1, P3D1. |
| **R5** (sch 293) | `not(@mesh) or /scene/assets/mesh[@id=current()/@mesh]` | `.../assets/*[(self::mesh or self::meshSequence) and @id=current()/@mesh]`; message "mesh or meshSequence asset" | The only changed (not added) rule. Applies to object3D/@mesh regardless of version, but meshSequence is itself gated by MSQ1. |

### 2. VOL: volume assets, medium, volume primitive

SREP: "Volume assets and sequences", "Volumetric objects and shading", "Frozen native pyro bakes (implemented)"; ledger `volumes`: `in_progress`; remaining: "production-quality native impact volume validation; finish advection GPU transport/regression checks and identical-content bake timestamp test. Schema and scene loading are connected; completion is not yet claimed". Ledger lists as implemented: SRVOL ingestion, medium/volume assets in XSD/Schematron/Rust, thermal emission, sequence selection/interpolation, native OpenVDB 5/4/3 ingestion.

| Element / attribute / rule | Before | After | Notes |
|---|---|---|---|
| `assets/volume` (`volumeAssetType`, xsd 1360) | - | new | attrs: `id` ID req, `src` (`volumeSourceType`) req, `sha256`, `format` {srvol(default), srvseq, openvdb}, `densityGrid` (default `density`), `temperatureGrid`, `velocityGridX/Y/Z`, `first`, `last` (`volumeFrameIndexType`), `fps` (`fpsType`), `interpolation` {hold(default), linear, advect}, `missingFrame` {error(default), hold, transparent}, `boundsMin/Max X/Y/Z` (double). |
| `volumeChannelType` | - | string, pattern `[A-Za-z0-9_.\-]{1,64}` | grid/channel names |
| `volumeSequencePatternType` | - | string, pattern `.*(%[0-9]*d\|#+).*` | printf/hash placeholder |
| `volumeSourceType` | - | union(`xs:anyURI`, `volumeSequencePatternType`) | `src` may be a pattern, not only URI |
| `volumeFrameIndexType` | - | integer, -2147483648..2147483647 | also used by meshSequence |
| `volumeInterpolationType`, `volumeMissingFrameType` | - | enums above | `advect` documented in the xsd annotation |
| `object3D/medium` (`mediumType`, xsd 1389) | - | new, 0..unbounded `animationElements` | `densityScale` (>=0, 1), `extinction` (>=0, 1), `albedo` color (#FFFFFFFF), `anisotropy` double -0.99..0.99 (0), `emissionColor` (#000000FF), `emissionScale` (>=0, 0), `blackbody` bool (false), `temperatureScale` >0 (1), `stepSize` >0 (1), `maxSteps` 1..65536 (2048). |
| `object3D/@primitive` | enum without volume | + `volume` (xsd 2398) | |
| `object3D/@volume` | - | xs:IDREF, optional (xsd 2440) | volume asset reference |
| VOL1 (obj3D) | - | primitive=volume needs exactly one of `@volume` or a `pyro` child | |
| VOL2 | - | `@volume` must name `assets/volume` | |
| VOL3 | - | <=1 `medium`; medium/@volume/pyro only on `primitive='volume'` | |
| VOL5 | - | blackbody=true needs `pyro` or the asset's `@temperatureGrid` | |
| VOL4 (`assets/volume`) | - | bounds: all six or none; each min < max | |
| VOL6 | - | non-srvseq sequence needs first+last, `last>=first`, span <1000000, numbered-pattern src; sequence options (`fps`, `interpolation!=hold`, `missingFrame!=error`) need a sequence | pattern test uses `%[0-9]*d` (width <=64) or `#` run (<64) |
| VOL7 | - | `sha256` not allowed with `first`/`last` | |
| VOL8 | - | `format='srvseq'` requires `sha256`, forbids first/last/fps, missingFrame must be error | |
| VOL9 | - | advect iff all three `velocityGrid*` present | |

### 3. PYRO: pyro simulation (child of object3D primitive=volume)

SREP: "Pyro simulation"; ledger `pyro`: `in_progress` (remaining: bounded importer dependencies, production visual/performance validation; "exact solver-state resumption is not claimed").

| Element / attribute / rule | Before | After | Notes |
|---|---|---|---|
| `object3D/pyro` (`pyroType`, xsd 2289) | - | new; children `pyroSource`, `pyroImpulse` (0..unbounded) | req: `width`, `height`, `depth`, `voxelSize` (all >0). Optional: `dt` (1/60), `ambientTemperature` (>0, <=50000, 300), `dissipation`, `cooling`, `buoyancy`, `vorticity`, `turbulence` (>=0, 0), `seed` (unsignedLong, 0), `pressureTolerance` (1e-6), `boundary` {open, closed(default)}, `pressureIterations` 1..10000 (200), `maxMemoryMiB` 1..4096 (256), `checkpointMemoryMiB` (256), `meshMemoryMiB` (128), `colliders` IDREFS, `colliderThickness`, `forceFields` IDREFS, `useForceFields` (true). |
| `pyroShape` attributeGroup (xsd 2250) | - | new | `shape` {sphere(default), box, mesh}, `radius` (1), `width/height/depth`, `x,y,z`, `rotation`, `rotationX/Y`, `scaleX/Y/Z`, `mesh` IDREF |
| `pyroSource` (xsd type `pyroSourceType`) | - | new; `animationElements` children | `pyroShape` + `start`, `end`, `densityRate`, `temperatureRate`, `velocityRateX/Y/Z`, `expansion` |
| `pyroImpulse` (`pyroImpulseType`) | - | new | `pyroShape` + `time` req, `density`, `temperature`, `velocityX/Y/Z`, `expansion` |
| PYRO1 | - | grid cells per axis: integer ratio (tol 1e-8) in 2..1024 | |
| PYRO2 | - | source `end > start` (`@start` absent treated as 0) | ctx pyroSource\|pyroImpulse; no `@end` on pyroImpulse so it is vacuous there |
| PYRO3 | - | shape=box requires width, height, depth | |
| PYRO4 | - | no zero scaleX/Y/Z | |
| PYRO5 | - | shape=mesh iff `@mesh`, naming `assets/mesh` | note: meshSequence does not qualify here |
| PYRO6 | - | `shape`/`mesh` not animatable | |
| PYRO7 | - | `colliders`: <=4096, each distinct, resolving to object3D with supported primitive (box, sphere, globe, plane, cylinder, cone, capsule, torus, mesh, text, extrude, clay) | |
| PYRO8 | - | collider geometry static: no animated primitive/mesh/radius/width/height/depth/segments etc.; clay with boil>0 and fingerprints>0 rejected | |

### 4. P3D: particles3D

SREP: "Three-dimensional particles"; ledger `particles`: `in_progress` (remaining: large-count batching, UHD performance/visual validation, mesh-importer dependency bounds, collider geometry retention audit; "still emits one Draw3 per particle/prototype").

| Element / attribute / rule | Before | After | Notes |
|---|---|---|---|
| `particles3D` (`particles3DType`, xsd 2158) | - | new node in `nodeChoice` (all containers) | children: `animationElements`, `transformConstraint`, `burst` (reuses existing `burstType`). Node attrs as other 3D nodes: `id` req, `name`, `x,y,z`, rotation(X/Y), `start/end/condition/parent`, `scaleX/Y/Z`, `visible`, `opacity`, `motionBlur`, `castShadow`, `receiveShadow`. |
| emission | - | `rate` (10), `emissionStart` (0), `emissionEnd`, `lifetime` >0 (2), `lifetimeVariance`, `seed` (0), `maxParticles` 1..1e6 (10000), `maxEvents` 1..1e6 (16384), `dt` (1/60), `emitterShape` {point(default), box, sphere, mesh}, `emitterWidth/Height/Depth/Radius`, `emitterMesh` IDREF | |
| motion | - | `speed`, `speedVariance`, `velocityX/Y/Z`, `gravityX/Y/Z`, `drag`, `directionX/Y/Z` (0,-1,0), `spread` 0..360, `inheritedVelocityX/Y/Z`, `rotation0X/Y/Z`, `rotationVarianceX/Y/Z`, `angularVelocity(+Variance)X/Y/Z`, `useForceFields` (true), `forceFields` IDREFS | |
| collision | - | `colliders` IDREFS, `collisionRadius` (0.5), `collisionTolerance` (0.001), `bounce` unit (0.5), `friction` (0) | |
| appearance | - | `shape` {sphere(default), billboard, streak, mesh}, `mesh`, `sprite`, `material` (IDREF), `size`, `sizeEnd`, `scaleVariance` [0,1), `color` (#FFFFFFFF), `colorEnd`, `opacityEnd`, `sizeCurve`, `colorCurve` (curveType, linear), `trail` (1), `segments` 1..256 (12) | |
| budgets | - | `maxMemoryMiB` (256), `checkpointMemoryMiB` (64), `meshMemoryMiB` (128), `maxWork` <=1e9 (1e8) | |
| P3D1 | - | version 1.3 | |
| P3D2 | - | `emitterMesh`/`mesh` iff shape=mesh and name `assets/mesh`; `sprite` only with shape=billboard naming `assets/image`; `material` must exist | |
| P3D3 | - | lifetimeVariance < lifetime; speedVariance <= speed; nonzero direction; emissionEnd >= emissionStart; every burst/@time >= emissionStart | |
| P3D4 | - | animatable props limited to pose, rate, opacity, size, sizeEnd, color, colorEnd, opacityEnd, trail | solver config is static |
| P3D5 | - | colliders <=4096, distinct, supported primitives (same list as PYRO7) | |
| P3D6 | - | collider geometry must be static (same pattern as PYRO8) | |

### 5. OCN: ocean

SREP: "Ocean surfaces and impulses", "Foam and spray", "Implemented numerical contract"; ledger `ocean`: `in_progress` (remaining: production visual/temporal validation in the full UHD film; importer allocation hardening). SREP: "Water is depth-averaged and whitewater is a cinematic read-only tracer effect, not overturning-sheet or compressible impact simulation."

| Element / attribute / rule | Before | After | Notes |
|---|---|---|---|
| `ocean` (`oceanType`, xsd 2076) | - | new node in `nodeChoice`; children `animationElements`, `transformConstraint`, `waterImpulse`, `wave`, `whitewater` | pose attrs as other 3D nodes; grid `width`,`depth` (64), `cellSize` (1), `waterLevel`, `bottomDepth` (10), `gravity` (9.81), `damping`, `dt` (1/60), `dryTolerance` (1e-10), `initialVelocityX/Z`, `seed`, `boundary` {closed(default), open, periodic}, bathymetry: `bathymetry` IDREF, `bathymetryEncoding` {red(default), terrarium, mapbox}, `bathymetryScale`, `bathymetryOffset`; `material` IDREF; budgets `maxMemoryMiB` (256), `checkpointMemoryMiB` (64, 0 allowed), `meshMemoryMiB`, `surfaceMemoryMiB` (128), `maxWork` (1e8) |
| `ocean/wave` (`oceanWaveType`) | - | new | `wavelength` (16), `amplitude`, `direction`, `phase`, `speed` |
| `ocean/waterImpulse` | - | new | `time`, `x`, `z`, `radius`, `amplitude`, `velocityX/Z`, `type` {displace(default), add-water} |
| `ocean/whitewater` (`whitewaterType`) | - | new, max one | `emissionRate` (10), `threshold`, `start`, `end`, `lifetime` (3), `sprayFraction`, `launchSpeed`, `drag`, `radius`, `seed`, `maxParticles` (10000), `maxMemoryMiB` (64), `maxWork`, `foamMaterial`, `sprayMaterial` |
| OCN1 | - | version 1.3 | |
| OCN2 | - | bathymetry is an `assets/image` (no `@layer`) or `assets/mesh`; material exists; scale/offset/encoding require bathymetry; non-red encoding requires an image | |
| OCN3 | - | grid width/depth integral multiples of cellSize, each >=1 cell, <=4,000,000 cells; dt>=1e-6; finite values; <=64 waves, <=16384 impulses; wave wavelength >= 2*cellSize; add-water amplitude >=0 | message truncated in the delta listing; test is in sch 89 |
| OCN4 | - | animatable props limited to pose and opacity | |
| OCN5 | - | <=1 whitewater; `end >= start` of the ocean (note: test compares to the ocean's `@start`); foam/spray materials exist | |

### 6. GEO: globe elevation (object3D attributes)

SREP "Globe elevation (implemented)", ledger `geometry`: `in_progress` (remaining: broader animated/imported/parent-transform geometry parity, rigid deformation boundary velocities, importer allocation accounting, UHD validation). `terrain`, `terrainEncoding`, and `exaggeration` already exist in 1.2; 1.3 adds the sampling/physical-scale controls.

| Attribute / rule | Before | After | Notes |
|---|---|---|---|
| `object3D/@planetRadius` (xsd 2416) | - | positiveDecimal, default 6378137 | metres; scene displacement = elevation * radius / planetRadius * exaggeration |
| `@terrainTileSize` | - | positiveInteger <=4096, default 256 | GEO2 requires a power of two |
| `@terrainZoom` | - | nonNegativeInteger <=22, no default | |
| `@terrainMissing` | - | {error(default), zero} | |
| `@terrainMemoryMiB` | - | positiveInteger <=4096, default 128 | |
| GEO1 | - | version 1.3 required for globe+terrain or any new attribute | existing flat-map terrain and plain globes unchanged |
| GEO2 | - | `planetRadius` only on globes; tile/zoom/missing/memory only on globe with `@terrain`; tile size power of two (1..4096) | |
| GEO3 | - | globe terrain/planetRadius/tile options not animatable; globe+terrain+rigidBody may not animate radius/segments/exaggeration | |

### 7. MSQ: mesh sequences

SREP "Mesh sequences (implemented; importer hardening pending)"; ledger `geometry`: `in_progress`.

| Element / attribute / rule | Before | After | Notes |
|---|---|---|---|
| `assets/meshSequence` (`meshSequenceAssetType`, xsd 955) | - | new | `id` req, `src` (`volumeSourceType`) req, `format` {gltf, glb, obj, ply, usd, usda, usdc, usdz, fbx}, `first`/`last` req (`volumeFrameIndexType`), `fps` req (`fpsType`), `interpolation` {hold(default), linear} (`meshSequenceInterpolationType`), `missingFrame` {error(default), hold, transparent}, `maxMemoryMiB` <=4096 (256), `assetProvenance` group |
| MSQ1 | - | version 1.3 | |
| MSQ2 | - | ordered first<=last, span <1000000, numbered src pattern | same pattern logic as VOL6 |
| MSQ3 | - | no `sha256` (attribute is not declared in the type either; rule is belt-and-braces) | |
| MSQ4 | - | rigidBody mesh object that references a meshSequence needs `fracture` + static/kinematic rigidBody; meshSequence objects cannot be named in `colliders` of object3D/pyro/particles3D | "rigid collider consumers require an explicit static proxy" |
| R5 | see section 1 | | |

### 8. Fracture and crater (object3D children)

SREP: "Fracture (native scene, rendering and cache integration implemented)", "Crater evolution (rendering, rigid, particle and pyro integration)"; ledger `geometry` `in_progress` as above.

| Element / attribute / rule | Before | After | Notes |
|---|---|---|---|
| `object3D/fracture` (`fractureType`, xsd 2315) | - | new, no children | `at` (>=0, 0), `pieces` 1..4096 (8), `seed` (0), `interiorMaterial` IDREF **required**, `interiorUvScale` (1), `impulseX/Y/Z`, `radialImpulse` (>=0), `maxMemoryMiB` (256). Annotation: cinematic partitioning, not a stress/failure calculation. |
| FRX1 | - | version 1.3 | |
| FRX2 | - | parent object3D, not primitive volume/plane/map; exactly one `fracture` and one `rigidBody` | |
| FRX3 | - | `interiorMaterial` exists in `/scene/materials/material` | |
| FRX4 | - | all numeric attributes finite | |
| `object3D/crater` (`craterType`, xsd 2340) | - | new, no children | `centerX/Y/Z`, `normalX/Y/Z` (0,0,-1), `radius` (50), `depth` (10), `rimHeight` (2), `rimWidth` (10), `influenceDepth` (optional, default 2*max(radius,depth,rimHeight) per annotation), `start` (0), `end` (1), `curve` {linear, ease-in, ease-out, ease-in-out(default), step}, `maxMemoryMiB` (128). Deformation formula given in the xsd annotation. |
| CRT1 | - | version 1.3 | |
| CRT2 | - | one crater, on a non-volume object3D | |
| CRT3 | - | `end > start` | |
| CRT4 | - | finite values; nonzero normal; `rimWidth <= radius`; if `influenceDepth` set, >= 2*max(depth, rimHeight) | |
| CRT5 | - | any sibling rigidBody must be static/kinematic with shape auto/trimesh (or absent) | dynamic rigid bodies rejected |

### Consequences for the Python implementation

Validator-only (schema + Schematron + loader acceptance; no pixels needed):
- Switching `scenerender/schema.py` `SCHEMA_PATH` (line 18) to a 1.3 XSD (and `.sch` alongside, which `semantic_errors` derives by name) makes every new element and all 45 rules validate. The 1.3 xsd/sch are Rust-owned and self-contained, so the local 1.1 baseline would first need to reach 1.1.3 and 1.2 (see the other delta sections).
- `scenerender/schema.py` (xsd introspection used for element/attribute lookups), `document.py` / `coverage.py` (feature coverage lists), `registry.py` (node-type tables) must learn the 13 new element names, otherwise unknown elements either fail or are skipped. `scene/@version` enum must accept `1.3`.
- Schematron needs XPath features used in the new rules: EXSLT `str:tokenize` (P3D5, PYRO7; the sch declares a `str` namespace), `sch:let` with `number(...)` guards, and `round()`. lxml's ISO Schematron handles these only if the namespace is bound; test before relying on it.
- `fracture` and `crater` depend on `rigidBodyType` (py-1.1 xsd line 1293, grep shows rigidBody handled in `physics.py`, `modifiers.py`, `render.py`, `compositor.py`, `coverage.py`).
- R5 widening is a trivial one-line change but only meaningful once `meshSequence` exists.

Rendering/simulation work (each is a new runtime feature; none exists in `scenerender` today, grep for meshSequence/particles3D/ocean/pyro/globe/pmtiles found nothing):
- VOL/PYRO: new volume ray-march (SRVOL/srvseq/OpenVDB readers, emission/absorption/scattering, medium shading) plus a seeded MAC fluid solver with seekable caches. Likely modules: new `assets/volume.py`, `nodes/scene3d.py`, `three/scene.py` (`primitive_geo` dispatches object3D primitives), `three/renderer.py` and `three/shaders.py` for GL integration, `physics.py` (`Seekable`, seeded noise helpers) for deterministic seek.
- P3D: 3D particles extend the existing 2D system in `nodes/particles.py`, `nodes/particle_collisions.py`, `nodes/particle_paints.py` (see `docs/PARTICLES.md`); also `three/scene.py`/`renderer.py` for drawing, and `physics.py` for rigid colliders.
- OCN: new depth-averaged shallow-water solver and whitewater tracers (new nodes/ module), plus `three/scene.py`/`materials.py` for the surface mesh.
- GEO: PMTiles v3 reader and Terrarium/Mapbox decoding for globe relief: new asset module under `assets/`, hooks in `three/geometry.py`/`three/scene.py`; also needs a `globe` primitive (grep finds no `globe` in the package today).
- MSQ: per-frame mesh loading with caching in `three/loaders.py` (`load_model`) / `assets/mesh.py`, topology checks, hold/linear interpolation.
- Fracture/crater: mesh partitioning, deformation maps in `three/geometry.py`, with rigid-body bookkeeping in `physics.py`/`modifiers.py`; particle/pyro collider replacement ties to `nodes/particle_collisions.py`.

Caveats: do not treat Rust's `in_progress` ledger entries as a stable contract. SREP says completion is not claimed and the numeric/field inventory review is pending, so XSD defaults and limits above may still change; the schema delta is only a literal diff of two commits. Counts of rules: 45 added = VOL 9 (VOL1-9), PYRO 8, P3D 6, OCN 5, MSQ 4, FRX 4, CRT 5, GEO 3, V8 1.
