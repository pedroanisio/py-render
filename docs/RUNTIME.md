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

The 2026-09-27 regression environment uses Python 3.12.3, Pycairo 1.25.1, Cairo
1.18.0 and Mesa llvmpipe. Local HTTP delivery tests require permission to bind
loopback sockets, and USDZ packaging creates temporary files in `/var/tmp`.

These dependencies enable the relevant runtime paths. They do not establish
complete schema conformance; outstanding implementation work is tracked in
[SCHEMA-IMPLEMENTATION.md](SCHEMA-IMPLEMENTATION.md).
