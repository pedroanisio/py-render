# Rendering parity baseline

Python at origin/main 6431b8b vs rs-scene-render 0.1.2 (f296a4c, release build), Intel Arc (Vulkan), 2026-10-04.
Scenes: rs tests/corpus/valid (24) + tools/evidence/scenes (26), times 10/50/90 % of the duration. Python renders with --lenient (unsupported 1.2/1.3 elements are ignored).

Reproduce: `python tools/parity/render_parity.py --rs <rs>/target/release/scene-render --out OUT --sheets <rs>/tests/corpus/valid <rs>/tools/evidence/scenes`

RS-ERROR rows are reference failures on this machine (no ffprobe, empty transcripts, evidence scenes whose fixtures must be generated with rs tools/fixtures), not Python results.


| status | count |
|---|---:|
| CLOSE | 21 |
| DIFFERENT | 68 |
| MATCH | 19 |
| RS-ERROR | 42 |

| scene | t | status | PSNR | max | % >2 | rs s | py s | note |
|---|---:|---|---:|---:|---:|---:|---:|---|
| crater.scene.xml | 0.292 | CLOSE | 37.89 | 23 | 2.441 | 0.29 | 2.6 |  |
| crater.scene.xml | 1.500 | CLOSE | 37.74 | 40 | 2.441 | 0.41 | 2.31 |  |
| crater.scene.xml | 2.708 | CLOSE | 37.21 | 44 | 2.441 | 0.54 | 2.78 |  |
| fracture.scene.xml | 1.500 | CLOSE | 32.19 | 175 | 0.22 | 3.61 | 2.6 |  |
| fracture.scene.xml | 2.708 | CLOSE | 32.19 | 175 | 0.22 | 4.4 | 2.7 |  |
| particles3d.scene.xml | 0.208 | CLOSE | 31.06 | 105 | 17.993 | 0.29 | 3.55 |  |
| particles3d.scene.xml | 1.000 | CLOSE | 30.91 | 121 | 17.993 | 0.27 | 2.57 |  |
| particles3d.scene.xml | 1.792 | CLOSE | 31.29 | 23 | 17.969 | 0.33 | 2.52 |  |
| pyro-colliders.scene.xml | 0.100 | CLOSE | 40.25 | 68 | 0.391 | 59.51 | 2.38 |  |
| pyro-colliders.scene.xml | 0.500 | CLOSE | 33.55 | 90 | 0.879 | 74.45 | 2.86 |  |
| pyro-colliders.scene.xml | 0.900 | CLOSE | 33.55 | 90 | 0.879 | 61.28 | 2.34 |  |
| fluid.scene.xml | 0.292 | CLOSE | 32.52 | 185 | 1.148 | 0.78 | 1.3 |  |
| object3d-depth-order.scene.xml | 0.083 | CLOSE | 39.63 | 137 | 0.26 | 0.54 | 2.13 |  |
| object3d-depth-order.scene.xml | 0.500 | CLOSE | 39.63 | 137 | 0.26 | 0.36 | 2.31 |  |
| object3d-depth-order.scene.xml | 0.917 | CLOSE | 39.63 | 137 | 0.26 | 0.35 | 2.08 |  |
| review-product.scene.xml | 0.292 | CLOSE | 43.44 | 98 | 1.941 | 0.89 | 2.41 |  |
| review-product.scene.xml | 1.500 | CLOSE | 43.68 | 98 | 1.928 | 1.0 | 2.7 |  |
| review-product.scene.xml | 2.708 | CLOSE | 43.74 | 98 | 1.907 | 1.04 | 2.63 |  |
| review-title.scene.xml | 0.292 | CLOSE | 39.1 | 105 | 1.719 | 0.8 | 0.95 |  |
| review-title.scene.xml | 1.500 | CLOSE | 46.37 | 56 | 1.211 | 0.91 | 0.84 |  |
| review-title.scene.xml | 2.708 | CLOSE | 46.37 | 56 | 1.211 | 0.85 | 0.85 |  |
| advected-volume.scene.xml | 0.100 | DIFFERENT | 23.97 | 122 | 5.859 | 35.25 | 2.63 |  |
| advected-volume.scene.xml | 0.500 | DIFFERENT | 23.85 | 122 | 5.859 | 36.11 | 2.26 |  |
| advected-volume.scene.xml | 0.900 | DIFFERENT | 23.88 | 122 | 5.859 | 38.41 | 2.55 |  |
| baked-volume.scene.xml | 0.100 | DIFFERENT | 11.55 | 119 | 100.0 | 32.99 | 2.63 |  |
| baked-volume.scene.xml | 0.500 | DIFFERENT | 11.55 | 119 | 100.0 | 32.72 | 3.31 |  |
| baked-volume.scene.xml | 0.900 | DIFFERENT | 11.55 | 119 | 100.0 | 41.94 | 2.56 |  |
| fracture.scene.xml | 0.292 | DIFFERENT | 26.97 | 186 | 0.61 | 3.0 | 2.73 |  |
| globe-relief.scene.xml | 0.208 | DIFFERENT | 5.25 | 253 | 45.02 | 2.85 | 2.6 |  |
| globe-relief.scene.xml | 1.000 | DIFFERENT | 5.25 | 253 | 45.02 | 2.74 | 2.53 |  |
| globe-relief.scene.xml | 1.792 | DIFFERENT | 5.25 | 253 | 45.02 | 2.33 | 2.28 |  |
| mesh-sequence.scene.xml | 0.208 | DIFFERENT | 5.79 | 199 | 45.41 | 0.35 | 2.78 |  |
| mesh-sequence.scene.xml | 1.000 | DIFFERENT | 2.77 | 204 | 87.891 | 0.39 | 2.76 |  |
| mesh-sequence.scene.xml | 1.792 | DIFFERENT | 2.77 | 204 | 87.891 | 0.33 | 2.91 |  |
| openvdb-sequence.scene.xml | 0.100 | DIFFERENT | 10.99 | 124 | 100.0 | 0.39 | 3.01 |  |
| openvdb-sequence.scene.xml | 0.500 | DIFFERENT | 10.99 | 124 | 100.0 | 0.49 | 2.88 |  |
| openvdb-sequence.scene.xml | 0.900 | DIFFERENT | 10.99 | 124 | 100.0 | 0.29 | 2.77 |  |
| openvdb.scene.xml | 0.100 | DIFFERENT | 11.55 | 119 | 100.0 | 0.51 | 2.44 |  |
| openvdb.scene.xml | 0.500 | DIFFERENT | 11.55 | 119 | 100.0 | 0.38 | 3.63 |  |
| openvdb.scene.xml | 0.900 | DIFFERENT | 11.55 | 119 | 100.0 | 0.32 | 2.32 |  |
| pyro-fields.scene.xml | 0.500 | DIFFERENT | 28.53 | 155 | 0.879 | 54.89 | 2.85 |  |
| pyro-fields.scene.xml | 0.900 | DIFFERENT | 28.58 | 164 | 1.074 | 64.98 | 2.37 |  |
| pyro-mesh.scene.xml | 0.500 | DIFFERENT | 20.04 | 172 | 3.516 | 0.38 | 2.36 |  |
| pyro-mesh.scene.xml | 0.900 | DIFFERENT | 20.04 | 173 | 3.516 | 0.42 | 2.16 |  |
| pyro.scene.xml | 0.500 | DIFFERENT | 28.48 | 155 | 0.879 | 0.57 | 2.23 |  |
| pyro.scene.xml | 0.900 | DIFFERENT | 28.24 | 164 | 0.879 | 0.55 | 2.11 |  |
| solid-colliders.scene.xml | 0.100 | DIFFERENT | 7.84 | 194 | 96.777 | 1.4 | 2.25 |  |
| solid-colliders.scene.xml | 0.500 | DIFFERENT | 7.84 | 194 | 96.777 | 1.86 | 2.39 |  |
| solid-colliders.scene.xml | 0.900 | DIFFERENT | 7.84 | 194 | 96.777 | 2.89 | 2.37 |  |
| thermal-volume.scene.xml | 0.100 | DIFFERENT | 8.9 | 137 | 100.0 | 0.6 | 2.19 |  |
| thermal-volume.scene.xml | 0.500 | DIFFERENT | 8.9 | 137 | 100.0 | 0.36 | 2.21 |  |
| thermal-volume.scene.xml | 0.900 | DIFFERENT | 8.9 | 137 | 100.0 | 0.3 | 2.09 |  |
| volume-sequence.scene.xml | 0.100 | DIFFERENT | 11.55 | 119 | 100.0 | 0.45 | 2.08 |  |
| volume-sequence.scene.xml | 0.500 | DIFFERENT | 11.55 | 119 | 100.0 | 0.29 | 2.16 |  |
| volume-sequence.scene.xml | 0.900 | DIFFERENT | 11.55 | 119 | 100.0 | 0.45 | 2.32 |  |
| volume.scene.xml | 0.100 | DIFFERENT | 11.55 | 119 | 100.0 | 0.4 | 2.35 |  |
| volume.scene.xml | 0.500 | DIFFERENT | 11.55 | 119 | 100.0 | 0.36 | 2.06 |  |
| volume.scene.xml | 0.900 | DIFFERENT | 11.55 | 119 | 100.0 | 0.35 | 2.53 |  |
| cinematic-pbr.scene.xml | 0.208 | DIFFERENT | 13.58 | 255 | 64.784 | 0.92 | 2.82 |  |
| cinematic-pbr.scene.xml | 1.000 | DIFFERENT | 13.58 | 255 | 64.784 | 0.86 | 2.84 |  |
| cinematic-pbr.scene.xml | 1.792 | DIFFERENT | 13.58 | 255 | 64.784 | 0.88 | 2.78 |  |
| clay-3d.scene.xml | 0.292 | DIFFERENT | 20.33 | 183 | 7.128 | 1.0 | 2.34 |  |
| clay-3d.scene.xml | 1.500 | DIFFERENT | 20.14 | 183 | 7.71 | 0.99 | 2.15 |  |
| clay-3d.scene.xml | 2.708 | DIFFERENT | 20.15 | 183 | 7.693 | 0.82 | 2.04 |  |
| erosion.scene.xml | 0.292 | DIFFERENT | 4.68 | 227 | 100.0 | 0.75 | 0.93 |  |
| erosion.scene.xml | 1.500 | DIFFERENT | 4.55 | 215 | 100.0 | 0.99 | 0.81 |  |
| erosion.scene.xml | 2.708 | DIFFERENT | 4.58 | 209 | 100.0 | 1.34 | 0.85 |  |
| flocking.scene.xml | 0.417 | DIFFERENT | 10.4 | 216 | 21.161 | 0.72 | 0.81 |  |
| flocking.scene.xml | 2.000 | DIFFERENT | 10.93 | 216 | 17.698 | 0.77 | 1.16 |  |
| flocking.scene.xml | 3.583 | DIFFERENT | 11.23 | 216 | 16.084 | 0.88 | 0.83 |  |
| fluid.scene.xml | 1.500 | DIFFERENT | 25.76 | 184 | 4.507 | 1.57 | 1.24 |  |
| fluid.scene.xml | 2.708 | DIFFERENT | 23.73 | 186 | 8.334 | 2.3 | 0.87 |  |
| ocio-pipeline.scene.xml | 0.083 | DIFFERENT | 18.59 | 30 | 100.0 | 0.65 | 2.75 |  |
| ocio-pipeline.scene.xml | 0.500 | DIFFERENT | 18.59 | 30 | 100.0 | 0.79 | 2.64 |  |
| ocio-pipeline.scene.xml | 0.917 | DIFFERENT | 18.59 | 30 | 100.0 | 0.67 | 2.59 |  |
| path-tracing.scene.xml | 0.083 | DIFFERENT | 20.06 | 193 | 50.281 | 4.19 | 2.37 |  |
| path-tracing.scene.xml | 0.500 | DIFFERENT | 20.06 | 193 | 50.281 | 4.11 | 2.81 |  |
| path-tracing.scene.xml | 0.917 | DIFFERENT | 20.06 | 193 | 50.281 | 4.0 | 2.33 |  |
| rigid-3d.scene.xml | 0.292 | DIFFERENT | 17.24 | 239 | 10.339 | 0.69 | 2.26 |  |
| rigid-3d.scene.xml | 1.500 | DIFFERENT | 16.35 | 239 | 11.92 | 0.7 | 2.31 |  |
| rigid-3d.scene.xml | 2.708 | DIFFERENT | 16.67 | 239 | 11.859 | 0.66 | 2.61 |  |
| rigid-particles-fields.scene.xml | 0.292 | DIFFERENT | 14.76 | 239 | 13.538 | 0.64 | 1.8 |  |
| rigid-particles-fields.scene.xml | 1.500 | DIFFERENT | 13.99 | 239 | 26.207 | 0.67 | 8.14 |  |
| rigid-particles-fields.scene.xml | 2.708 | DIFFERENT | 14.09 | 239 | 26.579 | 0.7 | 12.29 |  |
| slime.scene.xml | 0.417 | DIFFERENT | 8.33 | 255 | 29.023 | 0.86 | 1.09 |  |
| slime.scene.xml | 2.000 | DIFFERENT | 10.56 | 255 | 25.037 | 1.64 | 0.97 |  |
| slime.scene.xml | 3.583 | DIFFERENT | 12.56 | 255 | 19.002 | 2.37 | 0.99 |  |
| softbody-jelly.scene.xml | 1.500 | DIFFERENT | 25.78 | 172 | 1.407 | 0.73 | 1.73 |  |
| softbody-jelly.scene.xml | 2.708 | DIFFERENT | 29.68 | 172 | 0.727 | 0.72 | 2.28 |  |
| minimal.scene.xml | 0.500 | MATCH | 99.0 | 0 | 0.0 | 4.43 | 1.14 |  |
| minimal.scene.xml | 2.500 | MATCH | 99.0 | 0 | 0.0 | 4.04 | 1.13 |  |
| minimal.scene.xml | 4.500 | MATCH | 99.0 | 0 | 0.0 | 4.04 | 1.06 |  |
| ocean.scene.xml | 0.208 | MATCH | 99.0 | 0 | 0.0 | 0.36 | 1.38 |  |
| ocean.scene.xml | 1.000 | MATCH | 99.0 | 0 | 0.0 | 0.43 | 0.98 |  |
| ocean.scene.xml | 1.792 | MATCH | 99.0 | 0 | 0.0 | 0.29 | 1.16 |  |
| pyro-fields.scene.xml | 0.100 | MATCH | 99.0 | 0 | 0.0 | 53.13 | 2.42 |  |
| pyro-mesh.scene.xml | 0.100 | MATCH | 99.0 | 0 | 0.0 | 0.42 | 2.54 |  |
| pyro.scene.xml | 0.100 | MATCH | 99.0 | 0 | 0.0 | 0.36 | 2.57 |  |
| version-1.0.scene.xml | 0.200 | MATCH | 99.0 | 0 | 0.0 | 0.66 | 1.08 |  |
| version-1.0.scene.xml | 1.000 | MATCH | 99.0 | 0 | 0.0 | 0.63 | 0.79 |  |
| version-1.0.scene.xml | 1.800 | MATCH | 99.0 | 0 | 0.0 | 0.64 | 0.97 |  |
| animated-z.scene.xml | 0.208 | MATCH | 99.0 | 0 | 0.0 | 0.65 | 1.04 |  |
| animated-z.scene.xml | 1.000 | MATCH | 99.0 | 0 | 0.0 | 0.74 | 0.97 |  |
| animated-z.scene.xml | 1.792 | MATCH | 99.0 | 0 | 0.0 | 0.63 | 1.12 |  |
| softbody-jelly.scene.xml | 0.292 | MATCH | 45.43 | 80 | 0.174 | 0.75 | 1.14 |  |
| stopmotion-holds.scene.xml | 0.083 | MATCH | 99.0 | 0 | 0.0 | 0.67 | 0.88 |  |
| stopmotion-holds.scene.xml | 0.500 | MATCH | 99.0 | 0 | 0.0 | 0.65 | 1.2 |  |
| stopmotion-holds.scene.xml | 0.917 | MATCH | 99.0 | 0 | 0.0 | 0.73 | 0.87 |  |
| a03-remote.scene.xml | 1.235 | RS-ERROR |  |  |  | 9.91 | 13.04 | error: frame at 1.235 s: auto: transcript line 1: expected value at line 1 column 1 |
| a03-remote.scene.xml | 6.240 | RS-ERROR |  |  |  | 9.69 | 12.77 | error: frame at 6.240 s: bg: remote video https is not fetched while rendering |
| a03-remote.scene.xml | 11.245 | RS-ERROR |  |  |  | 13.92 | 9.76 | error: frame at 11.245 s: auto: transcript line 1: expected value at line 1 column 1 |
| a04-sequence-hold.scene.xml | 1.235 | RS-ERROR |  |  |  | 9.04 | 12.77 | error: frame at 1.235 s: auto: transcript line 1: expected value at line 1 column 1 |
| a04-sequence-hold.scene.xml | 6.240 | RS-ERROR |  |  |  | 14.86 | 8.53 | error: frame at 6.240 s: bg: cannot run ffprobe: No such file or directory (os error 2); install FFmpeg or set SR_FFMPEG |
| a04-sequence-hold.scene.xml | 11.245 | RS-ERROR |  |  |  | 17.1 | 7.89 | error: frame at 11.245 s: auto: transcript line 1: expected value at line 1 column 1 |
| kitchen-sink.scene.xml | 1.235 | RS-ERROR |  |  |  | 3.39 | 5.5 | error: frame at 1.235 s: auto: transcript line 1: expected value at line 1 column 1 |
| kitchen-sink.scene.xml | 6.240 | RS-ERROR |  |  |  | 2.35 | 5.74 | error: frame at 6.240 s: bg: cannot run ffprobe: No such file or directory (os error 2); install FFmpeg or set SR_FFMPEG |
| kitchen-sink.scene.xml | 11.245 | RS-ERROR |  |  |  | 2.58 | 5.81 | error: frame at 11.245 s: auto: transcript line 1: expected value at line 1 column 1 |
| w01-non-finite.scene.xml | 1.235 | RS-ERROR |  |  |  | 1.84 | 4.85 | error: frame at 1.235 s: auto: transcript line 1: expected value at line 1 column 1 |
| w01-non-finite.scene.xml | 6.240 | RS-ERROR |  |  |  | 2.15 | 4.8 | error: frame at 6.240 s: bg: cannot run ffprobe: No such file or directory (os error 2); install FFmpeg or set SR_FFMPEG |
| w01-non-finite.scene.xml | 11.245 | RS-ERROR |  |  |  | 2.21 | 6.43 | error: frame at 11.245 s: auto: transcript line 1: expected value at line 1 column 1 |
| basemap.scene.xml | 0.100 | RS-ERROR |  |  |  | 0.01 | 0.99 | invalid basemap.scene.xml: 2 error(s), 0 warning(s) |
| basemap.scene.xml | 0.500 | RS-ERROR |  |  |  | 0.01 | 0.96 | invalid basemap.scene.xml: 2 error(s), 0 warning(s) |
| basemap.scene.xml | 0.900 | RS-ERROR |  |  |  | 0.02 | 0.97 | invalid basemap.scene.xml: 2 error(s), 0 warning(s) |
| broken-shader.scene.xml | 0.083 | RS-ERROR |  |  |  | 0.02 | 0.98 | invalid broken-shader.scene.xml: 1 error(s), 0 warning(s) |
| broken-shader.scene.xml | 0.500 | RS-ERROR |  |  |  | 0.02 | 1.12 | invalid broken-shader.scene.xml: 1 error(s), 0 warning(s) |
| broken-shader.scene.xml | 0.917 | RS-ERROR |  |  |  | 0.01 | 1.05 | invalid broken-shader.scene.xml: 1 error(s), 0 warning(s) |
| fbx-animation.scene.xml | 0.083 | RS-ERROR |  |  |  | 0.01 | 1.95 | invalid fbx-animation.scene.xml: 1 error(s), 0 warning(s) |
| fbx-animation.scene.xml | 0.500 | RS-ERROR |  |  |  | 0.01 | 2.05 | invalid fbx-animation.scene.xml: 1 error(s), 0 warning(s) |
| fbx-animation.scene.xml | 0.917 | RS-ERROR |  |  |  | 0.01 | 1.85 | invalid fbx-animation.scene.xml: 1 error(s), 0 warning(s) |
| gltf-character.scene.xml | 0.208 | RS-ERROR |  |  |  | 0.02 | 2.12 | invalid gltf-character.scene.xml: 1 error(s), 0 warning(s) |
| gltf-character.scene.xml | 1.000 | RS-ERROR |  |  |  | 0.01 | 2.24 | invalid gltf-character.scene.xml: 1 error(s), 0 warning(s) |
| gltf-character.scene.xml | 1.792 | RS-ERROR |  |  |  | 0.01 | 2.75 | invalid gltf-character.scene.xml: 1 error(s), 0 warning(s) |
| map-3d.scene.xml | 0.100 | RS-ERROR |  |  |  | 0.01 | 2.13 | invalid map-3d.scene.xml: 3 error(s), 0 warning(s) |
| map-3d.scene.xml | 0.500 | RS-ERROR |  |  |  | 0.01 | 2.25 | invalid map-3d.scene.xml: 3 error(s), 0 warning(s) |
| map-3d.scene.xml | 0.900 | RS-ERROR |  |  |  | 0.01 | 2.42 | invalid map-3d.scene.xml: 3 error(s), 0 warning(s) |
| shadertoy-look.scene.xml | 0.208 | RS-ERROR |  |  |  | 0.01 | 0.84 | invalid shadertoy-look.scene.xml: 1 error(s), 0 warning(s) |
| shadertoy-look.scene.xml | 1.000 | RS-ERROR |  |  |  | 0.01 | 1.09 | invalid shadertoy-look.scene.xml: 1 error(s), 0 warning(s) |
| shadertoy-look.scene.xml | 1.792 | RS-ERROR |  |  |  | 0.01 | 0.84 | invalid shadertoy-look.scene.xml: 1 error(s), 0 warning(s) |
| splat-object.scene.xml | 0.208 | RS-ERROR |  |  |  | 0.01 | 2.39 | invalid splat-object.scene.xml: 1 error(s), 0 warning(s) |
| splat-object.scene.xml | 1.000 | RS-ERROR |  |  |  | 0.01 | 2.45 | invalid splat-object.scene.xml: 1 error(s), 0 warning(s) |
| splat-object.scene.xml | 1.792 | RS-ERROR |  |  |  | 0.01 | 2.39 | invalid splat-object.scene.xml: 1 error(s), 0 warning(s) |
| still-formats.scene.xml | 0.100 | RS-ERROR |  |  |  | 0.01 | 0.94 | invalid still-formats.scene.xml: 5 error(s), 0 warning(s) |
| still-formats.scene.xml | 0.500 | RS-ERROR |  |  |  | 0.01 | 1.1 | invalid still-formats.scene.xml: 5 error(s), 0 warning(s) |
| still-formats.scene.xml | 0.900 | RS-ERROR |  |  |  | 0.02 | 1.03 | invalid still-formats.scene.xml: 5 error(s), 0 warning(s) |
| usd-binary.scene.xml | 0.083 | RS-ERROR |  |  |  | 0.01 | 2.57 | invalid usd-binary.scene.xml: 1 error(s), 0 warning(s) |
| usd-binary.scene.xml | 0.500 | RS-ERROR |  |  |  | 0.01 | 2.31 | invalid usd-binary.scene.xml: 1 error(s), 0 warning(s) |
| usd-binary.scene.xml | 0.917 | RS-ERROR |  |  |  | 0.01 | 2.4 | invalid usd-binary.scene.xml: 1 error(s), 0 warning(s) |
| world-map.scene.xml | 0.600 | RS-ERROR |  |  |  | 0.01 | 1.26 | invalid world-map.scene.xml: 1 error(s), 0 warning(s) |
| world-map.scene.xml | 3.000 | RS-ERROR |  |  |  | 0.01 | 0.97 | invalid world-map.scene.xml: 1 error(s), 0 warning(s) |
| world-map.scene.xml | 5.400 | RS-ERROR |  |  |  | 0.01 | 0.82 | invalid world-map.scene.xml: 1 error(s), 0 warning(s) |
