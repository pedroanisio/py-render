# Runtime dependencies

Rendering requires Python 3.10 or later, Cairo 1.18 or later, Pycairo 1.25 or
later, and the system Pango/PangoCairo libraries with their PyGObject bindings.
Cairo floating-point image surfaces are required to quantize gradient dithering
deterministically. The render context checks that capability before rendering.

Create an environment with access to system graphics bindings, then install the
Python package and its import-format dependencies:

```sh
uv venv --system-site-packages
uv pip install -e '.[3d,test,codes]'
```

The `3d` extra provides USD/USDZ, FBX, OpenEXR and MaterialX libraries. The smaller
`exr` extra installs OpenEXR 3.3 or later for still-image parts and channel sets.
PNG precision, TIFF decoding and PSD compositing dependencies are included in the
base package. See [IMAGE-INPUTS.md](IMAGE-INPUTS.md) for input behavior and limits.
OpenGL 4.1
or later is required by the 3D renderer; a software Mesa implementation can be
used. MaterialX procedural baking also needs an accessible graphics display.
FFmpeg is supplied by `imageio-ffmpeg` unless configured otherwise.

Whole-frame effect chains (lighting, colour grading, exposure, film grain, vignette,
compositing and the final 8-bit conversion) run as fused Numba kernels. Numba compiles
them on first use and caches the machine code beside the package (or in
`NUMBA_CACHE_DIR`), so the first render after an install pays a few seconds of
compilation. Kernel results agree with the NumPy implementations to float32 rounding,
which moves an 8-bit output value by at most one level on rare pixels.
`SCENERENDER_KERNELS=0` selects the NumPy implementations; they are also used when Numba
is not installed.

With an NVIDIA GPU, rendering and encoding use it automatically:

- Whole-frame effect kernels (heat haze and turbulent displacement, colour grading,
  exposure, film grain, vignette, adjustment mixing and the 8-bit conversion) run as CUDA
  kernels through CuPy, for tiles of 2^18 pixels or more. Install the `gpu` extra
  (`uv pip install -e '.[gpu]'`). The kernels are compiled without fused multiply-add and
  follow the CPU kernels operation for operation; noise lattices are still evaluated on
  the CPU, so the GPU path renders the same pixels.
- Single-pass H.264 outputs of at least 256x128 pixels encode with NVENC (`h264_nvenc`,
  constant quality at the output's `crf`). The engine probes `SCENERENDER_FFMPEG`, the
  bundled ffmpeg and the `ffmpeg` on `PATH`, in that order, for one that can open the
  encoder; without one, or for two-pass and `maxFileSize` outputs, x264 encodes as before.

- Single-pass shader effects in the `srgb` and `linear-srgb` spaces convert to and from the
  shader's space on the GPU (the same arithmetic as the CPU conversion), and the `bloom` effect
  runs as GL passes that reproduce the NumPy passes; pixels are uploaded once and read back once.

- Frames composite on the GPU (`scenerender/gpucomp.py`, GL): the frame is a float texture, and
  source-over (and `behind`) composites, isolated groups, raster-cache entries (uploaded once, then
  drawn or warped with exact bilinear fetches), 3D layers, single-pass shader effects, shape canvases
  (converted from 8-bit through the CPU's own tables) and the motion-blur sum stay there. The finished
  frame is converted on the GPU to the encoder's input, packed 8-bit RGB or, for 8-bit BT.709 H.264 /
  H.265 at the rendered size, yuv420p (the same values as ffmpeg's accurate swscale conversion within
  one code), and read back asynchronously one frame behind. Everything else reads a buffer's pixels as
  before and downloads it on first access, so CPU-only paths are unchanged; frames match the CPU
  compositor within one 8-bit code. The GPU frame covers exactly the frame rectangle, so documents with
  adjustment layers (which can read what is composited outside it) keep the CPU frame.
  `SCENERENDER_GPU_COMPOSITE=0` keeps compositing on the CPU.
- With GL 4.3, Gaussian splats are depth-sorted on the GPU (a bitonic sort of (depth, index) keys, the
  order of the CPU's stable sort) and drawn through the sorted order from attributes uploaded once.

`SCENERENDER_GPU=0` (or `render --no-gpu`) keeps all work on the CPU.

Rendering reuses work across frames with a raster cache. A node (or a run of consecutive sibling
nodes with normal blending) whose content is provably unchanged over a one-second window is drawn
once per window. While its frame placement is unchanged it is reused exactly; while its placement
animates (a camera pan, a Ken Burns move) it is resampled bilinearly from a raster drawn 15% above
its current scale over a margin around the view, and redrawn when the view leaves that raster or
zooms past it. Masks and pointwise effects (colour grades, and GLSL that samples its input only at
the pixel itself) are part of the raster; anything time-varying inside a node (animated content,
expressions, media, other effects) keeps it out of the cache. Entries carry the node's XML, the
elements it references and the files they name, so documents or files edited between renders are
never served stale. Resampling moving content softens edges by a fraction of a pixel compared with
drawing it directly; `SCENERENDER_RASTER_CACHE=0` renders every frame exactly (the conformance
tests run that way).

A raster-cache entry stays valid from window to window for as long as its content stays static.
Instanced `object3D` copies evaluate their property expressions for all indices in one NumPy pass
(`scenerender/vexpr.py`, a checked subset of the expression language; anything else is interpreted
per copy).

`render` uses one process by default, the least CPU time: every extra worker reopens the document and
redraws what the first has cached. `--jobs N` (or `0`, one per CPU) trades CPU time for wall time.
Motion-blur samples of one frame can also run in worker processes (`SCENERENDER_SAMPLE_WORKERS=1`).
Each worker holds a full renderer, so their number is limited to what fits in half of the memory
still available (host or cgroup), and the pool is shrunk when a frame outgrows it. Frame workers are
sized the same way after the first frame, and a worker that dies stops the render with an error
instead of hanging it. NumPy's BLAS runs single-threaded (`SCENERENDER_BLAS_THREADS` overrides),
Numba's OpenMP threads wait passively (`OMP_WAIT_POLICY=PASSIVE` unless set) and the NVIDIA GL driver
sleeps instead of spinning while it waits for the GPU (`__GL_YIELD=USLEEP` unless set): all three
otherwise spin-wait and multiply CPU time without making frames faster. The NVENC probe result is
kept in the user cache directory (`$XDG_CACHE_HOME/scenerender`), keyed by the ffmpeg builds and the
NVIDIA driver.

The 2026-09-27 regression environment uses Python 3.12.3, Pycairo 1.25.1, Cairo
1.18.0 and Mesa llvmpipe. Local HTTP delivery tests require permission to bind
loopback sockets, and USDZ packaging creates temporary files in `/var/tmp`.

The full test suite also expects the `3d`, `test` and `codes` extras, `cryptography`
(signed publishing), librsvg's GObject bindings (`gir1.2-rsvg-2.0`), the Noto Color Emoji
font, and Inter 3.19's variable font (`Inter Variable/Inter.ttf` from the release archive,
family "Inter"; Inter 4 renamed it "Inter Variable"). The 2026-09-28 run on an NVIDIA
RTX 6000 Ada (EGL, OpenGL 4.6, CUDA through the `gpu` extra) passes with and without
`SCENERENDER_GPU=0`; LaTeX formulas, CJK font collections and the MaterialX texture baker
(which needs a GLX display) are skipped when absent.

These dependencies enable the relevant runtime paths. They do not establish
complete schema conformance; outstanding implementation work is tracked in
[SCHEMA-IMPLEMENTATION.md](SCHEMA-IMPLEMENTATION.md).
