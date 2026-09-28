# scenerender (py-render)

A Python renderer for **scene-render 1.1** documents: XML files, validated by
[`schema/scene-render-1.1.xsd`](schema/scene-render-1.1.xsd), that describe a timed composition of
shapes, text, images, video, audio, effects, transitions, and 2.5D and 3D content. `scenerender`
turns such a document into stills, image sequences or encoded video.

```
scene.xml ──▶ load + validate ──▶ evaluate at time t ──▶ composite (linear light) ──▶ frames ──▶ ffmpeg ──▶ video
```

## Features

- **Timeline**: keyframes with 41 curve types, expressions (a safe ECMAScript subset), links, motion
  paths, sequences, markers, repeats, parameters and variants.
- **2D**: shapes and SVG paths, Pango text with text animators and text on paths, images (PNG, TIFF,
  PSD, EXR and more), video, Lottie, charts, formulas and barcodes.
- **Compositing**: premultiplied float in linear light, 35 blend modes, masks and track mattes,
  isolated groups, adjustment layers, motion blur.
- **Effects and transitions**: 80 effects, including user GLSL shaders (plain, Shadertoy, ISF), and 35
  transitions.
- **3D**: an OpenGL PBR renderer with shadows, image-based lighting, depth of field, glTF, OBJ, USD
  and FBX meshes, and Gaussian splats; 2.5D camera projection of 2D layers.
- **Audio**: mixing, buses, ducking, loudness normalisation, spatial audio.
- **Output**: H.264, HEVC, ProRes, DNxHD, FFV1, WebM, GIF, APNG, image sequences, captions (burned
  in or as SRT/VTT sidecars) and posters.
- **QA**: flash, contrast, safe-area and caption checks.
- **GPU**, when available: NVENC encoding, CUDA effect kernels and GL shader paths, all falling back
  to the CPU.

Run `scenerender coverage <scene.xml>` to see which features a given document uses and how fully each
is supported. [docs/SCHEMA-IMPLEMENTATION.md](docs/SCHEMA-IMPLEMENTATION.md) tracks the remaining
conformance work.

## Requirements

- Python 3.10 or later.
- Cairo 1.18 or later with Pycairo 1.25 or later, and Pango/PangoCairo with PyGObject (system
  packages).
- OpenGL 4.1 or later for 3D (Mesa llvmpipe works; NVIDIA via EGL is used when present).
- ffmpeg: supplied by `imageio-ffmpeg` unless you point `SCENERENDER_FFMPEG` at another one (for
  example one with NVENC).

[docs/RUNTIME.md](docs/RUNTIME.md) lists every dependency and runtime switch.

## Install

The virtual environment needs the system Cairo, Pango and GObject bindings:

```sh
uv venv --system-site-packages
uv pip install -e .            # core renderer
uv pip install -e '.[3d]'      # + USD/USDZ, FBX, OpenEXR, MaterialX
uv pip install -e '.[gpu]'     # + CUDA effect kernels (CuPy, CUDA 12)
uv pip install -e '.[test,codes]'
```

## Quick start

Save this as `hello.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="1280" height="720" fps="30" duration="3" background="#0B1320FF"/>
  <assets>
    <text id="title-text" text="Hello, scenerender" width="1000" height="90" size="64"
          color="#F4F1EAFF" align="center"/>
  </assets>
  <composition>
    <shape id="disc" shape="ellipse" x="540" y="200" width="200" height="200" fill="#E8B04BFF">
      <animate property="x">
        <key time="0" value="240"/>
        <key time="3" value="840"/>
      </animate>
    </shape>
    <layer id="title" asset="title-text" x="140" y="520"/>
  </composition>
</scene>
```

Then:

```sh
scenerender validate hello.xml                 # check it against the XSD
scenerender still hello.xml -t 1.5 -o hello.png
scenerender render hello.xml -o hello.mp4
```

## Command line

| Command | What it does |
|---|---|
| `scenerender validate SCENE` | Validate against the XSD. |
| `scenerender still SCENE -t TIMES -o OUT.png` | Render stills at comma-separated times or marker ids. |
| `scenerender render SCENE [-o OUT]` | Render the document's `<output>`s, or a single file with `-o`. |
| `scenerender check SCENE` | Run the QA checks on a quick low-resolution pass. |
| `scenerender coverage SCENE` | Report which of the document's features the renderer supports. |
| `scenerender-vpkg ...` | Build and inspect portable scene video packages (`.vpkg.zip`); see [docs/VPKG-HOWTO.md](docs/VPKG-HOWTO.md). |

Useful `render` options:

| Option | Effect |
|---|---|
| `--from` / `--to` | Render part of the timeline. |
| `--jobs N` | Number of frame worker processes (0 means one per CPU). |
| `--frames-dir DIR` | Keep rendered frames in DIR; a rerun resumes from them. |
| `--no-motion-blur` | Faster previews. |
| `--no-gpu` | Keep all work on the CPU. |
| `--param ID=VALUE`, `--variant`, `--layout` | Select parameters, variants and layouts. |

Run `scenerender <command> --help` for the full list.

## Using it from Python

```python
from scenerender.render import Renderer

r = Renderer.open("hello.xml")
rgb = r.frame_rgb(1.5)          # (h, w, 3) uint8 at t = 1.5 s
linear = r.frame_linear(1.5)    # premultiplied float32 RGBA in the working space
```

## Performance

- **Workers**: rendering is parallel across frame worker processes, and each worker's count is
  sized to the memory available (host or container limit).
- **Raster cache**: content that is provably unchanged over a one-second window is drawn once and
  reused. It is reused exactly while it stays in place, and resampled while a camera or Ken Burns
  move animates it.
- **Kernels**: per-pixel work runs in Numba kernels, and on the GPU where that is faster.
- **Exact rendering**: set `SCENERENDER_RASTER_CACHE=0` to draw every frame from scratch.

The other switches are in [docs/RUNTIME.md](docs/RUNTIME.md):

| Switch | Effect |
|---|---|
| `SCENERENDER_GPU=0` | Keep all work on the CPU. |
| `SCENERENDER_KERNELS=0` | Use the NumPy reference implementations instead of the kernels. |
| `SCENERENDER_THREADS` | Threads per frame. |
| `SCENERENDER_BLAS_THREADS` | Threads for NumPy's BLAS. |

## Tests

```sh
python -m pytest -q tests
SCENERENDER_GPU=0 python -m pytest -q tests      # the CPU paths
```

- **Exact renders**: the conformance tests compare exact renders, so `tests/conftest.py` turns the
  raster cache off. `tests/test_raster_cache.py` covers the cache itself.
- **Optional tests**: tests that need optional pieces (LaTeX, CJK fonts, the MaterialX baker, a GL
  display) skip themselves when those are missing.

## Repository layout

| Path | Contents |
|---|---|
| `scenerender/` | The package: document loading, evaluator, compositor, and handlers in `nodes/`, `assets/`, `effects/`, `transitions/`, `three/` (3D) and `audio/`; also output encoding and the CLI. |
| `schema/` | The scene-render 1.1 XSD and Schematron rules. |
| `tests/` | The pytest suite, with fixtures under `static/fixtures/`. |
| `docs/` | Documentation (see below). |

Documentation in `docs/`:

| File | Topic |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Pipeline, module map and contracts for writing node, asset and effect handlers. |
| [RUNTIME.md](docs/RUNTIME.md) | Dependencies, GPU use, caches and environment switches. |
| [TIMING.md](docs/TIMING.md) | How time, windows and clocks are evaluated. |
| [IMAGE-INPUTS.md](docs/IMAGE-INPUTS.md) | Supported image formats and their limits. |
| [SCHEMA-IMPLEMENTATION.md](docs/SCHEMA-IMPLEMENTATION.md) | Conformance status. |
| [VPKG.md](docs/VPKG.md) and [VPKG-HOWTO.md](docs/VPKG-HOWTO.md) | Scene video packages. |

## Extending

New nodes, assets, effects, transitions, blend modes and codecs register themselves in
`scenerender/registry.py` and are imported by `registry.load_plugins()`. A feature the renderer does
not implement warns once and is skipped rather than failing the render.
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes the buffer, matrix, value and determinism
contracts that handlers follow.
