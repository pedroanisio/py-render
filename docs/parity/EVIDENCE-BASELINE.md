# Evidence baseline: Python against Rust

2026-10-04 · Rust `scene-render 0.1.2` commit `f296a4c7b002` · Python tree `96cf06cbc912`

The Rust `tools/evidence.py` cases and pixel checks, run unchanged against both renderers (Python through `py_scene_render_shim.py`; `degraded` = the Python renderer logged a warning). Regenerate with `tools/parity/evidence_gate.py`.

| Rust \ Python | native | degraded | wrong | error | gap |
|---|---|---|---|---|---|
| **native** | 10 | 0 | 0 | 10 | 0 |
| **degraded** | 1 | 1 | 0 | 0 | 0 |
| **wrong** | 1 | 0 | 1 | 0 | 0 |
| **error** | 0 | 0 | 0 | 0 | 0 |
| **gap** | 0 | 0 | 0 | 0 | 0 |

| Case | Reference | Rust | Python | Verdict | Python evidence |
|---|---|---|---|---|---|
| `stopmotion-holds` | Object stop-motion (PES-style replacement animation) | native | **native** | SAME |  |
| `splat-object` | Captured real-world objects (photogrammetry / Gaussian splats) | native | **native** | SAME |  |
| `softbody-jelly` | Squash-and-stretch motion graphics | native | **native** | SAME |  |
| `rigid-particles-fields` | Physics-driven motion graphics (bodies, fire, sparks, fields) | native | **native** | SAME |  |
| `cinematic-pbr` | Cinematic product / CG shots | native | **native** | SAME |  |
| `object3d-depth-order` | Any clip mixing 2D layers with 3D objects that come towards camera | native | **native** | SAME |  |
| `gltf-character` | Character animation (Animist-style imported rigs) | native | **native** | SAME |  |
| `shadertoy-look` | Shader-driven looks (Shadertoy ports) | native | **native** | SAME |  |
| `animated-z` | Layered 2D animation that re-stacks elements over time | native | **native** | SAME |  |
| `broken-shader` | Any clip depending on a custom shader | degraded | **degraded** | SAME | warning: effect-shader '/home/admin/src/rs-scene-render/crates/sr-gpu/tests/shaders/broken.glsl#8f5970': GLSL compile/link failed; falling back. |
| `ocio-pipeline` | Studio-graded footage (OCIO / ACES pipelines) | wrong | **native** | PY-BETTER |  |
| `fluid` | Liquid / smoke simulation | native | **error** | PY-WORSE | error:   6: Element 'fluid': This element is not expected. Expected is one of ( group, sequence, layer, shape, object3D, camera, particleEmitter, instance, incl |
| `flocking` | Swarms and crowds | native | **error** | PY-WORSE | error:   6: Element 'flock': This element is not expected. Expected is one of ( group, sequence, layer, shape, object3D, camera, particleEmitter, instance, incl |
| `erosion-slime` | Generative growth (slime-mould trails) | native | **error** | PY-WORSE | error:   6: Element 'slime': This element is not expected. Expected is one of ( group, sequence, layer, shape, object3D, camera, particleEmitter, instance, incl |
| `erosion` | Landscape erosion | native | **error** | PY-WORSE | error:   6: Element 'erosion': This element is not expected. Expected is one of ( group, sequence, layer, shape, object3D, camera, particleEmitter, instance, in |
| `clay-3d` | 3D claymation look | native | **error** | PY-WORSE | error:   8: Element 'blob': This element is not expected. Expected is one of ( animate, expression, motionPath, link, transformConstraint ). |
| `path-tracing` | Offline-quality global illumination | native | **error** | PY-WORSE | error:   12: Element 'camera', attribute 'maxBounces': The attribute 'maxBounces' is not allowed. |
| `fbx-animation` | Rigs delivered as FBX | native | **native** | SAME |  |
| `usd-binary` | USD pipeline assets | degraded | **native** | PY-BETTER |  |
| `still-formats` | photo and design assets | wrong | **wrong** | SAME | HEIC tagged Display P3 (nclx) converts to sRGB as LittleCMS does (got #101418); JPEG XL (lossless) decodes exactly (got #101418) |
| `world-map` | GEOlayers / Flourish maps | native | **error** | PY-WORSE | error:   7: Element 'geo': This element is not expected. Expected is one of ( image, video, imageSequence, audio, text, vector, mesh, lottie, font, generator ). |
| `basemap` | MapLibre / Protomaps street maps | native | **error** | PY-WORSE | error:   7: Element 'tiles': This element is not expected. Expected is one of ( image, video, imageSequence, audio, text, vector, mesh, lottie, font, generator  |
| `map-3d` | Google Earth Studio / 3D city maps | native | **error** | PY-WORSE | error:   21: Element 'object3D', attribute 'textureSize': The attribute 'textureSize' is not allowed. |
| `rigid-3d` | 3D physics motion graphics (tumbling crates, pendulums) | native | **error** | PY-WORSE | error:   27: Element 'constraint', attribute 'type': [facet 'enumeration'] The value 'ball' is not an element of the set {'spring', 'distance', 'pin', 'rope', ' |

Python native on 12 of 24 cases (12 same class as Rust, 10 worse, 2 better).
