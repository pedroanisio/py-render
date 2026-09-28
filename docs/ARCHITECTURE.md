# scenerender — architecture

`scenerender` is a generic Python renderer for documents valid under
`schema/scene-render-1.1.xsd`. It replaced the per-film scripts that used to live in `tools/`
(removed; see git history), which each hard-coded one document.

See [RUNTIME.md](RUNTIME.md) for graphics and import dependencies and
[SCHEMA-IMPLEMENTATION.md](SCHEMA-IMPLEMENTATION.md) for current conformance work.
[VPKG.md](VPKG.md) specifies scene video packages (`scenerender-vpkg`); [VPKG-HOWTO.md](VPKG-HOWTO.md) shows how to make and use them.

## Pipeline

```
scene.xml ─ document.load ─▶ prepared lxml tree ─ RenderContext.render_frame(t) ─▶ Buf ─ Renderer ─▶ RGB frames ─ output ─▶ files
             (validate, params,       (read-only)        compositor walks nodes,
              variants, layouts,                          handlers from registries
              binds, repeats,
              sequences, markers,
              styles, fonts)
```

| Module | Role |
|---|---|
| `schema.py` | Reads the XSD: element type by ancestor path, attribute simple types and defaults. Defaults and value parsing come from here, not from hand-copied tables. |
| `document.py` | Load, validate (lenient by default; `--strict` refuses invalid docs), includes, params → variant → CLI, binds, layout/variant overrides, `{{param}}` text, repeat expansion, sequence scheduling, markers + beat grids, tokens, text-style inheritance, font registration. |
| `evaluator.py` | `ev.get(el, prop, ctx)`: override → attribute → schema default, then `animate` / `link` / `motionPath` / `expression` in document order. Typed helpers `num`, `length`, `color`, `bool`, `str`. |
| `anim.py`, `curves.py` | Keyframes, all 41 curve types, extrapolation, TCB/Catmull-Rom, spring, steps, cubic-bezier handles. |
| `expr.py` | The expression language (safe Pratt parser; seeded random/noise/wiggle). |
| `compositor.py` | Frame walk, transforms, stacking, isolation/passthrough groups, masks, mattes, effect stacks, adjustment layers, transitions, instances. |
| `raster.py` | `Buf` tiles (premultiplied float32 RGBA in the working space at frame offsets), cairo `Canvas`, colour conversion. |
| `geometry.py` | SVG path parsing, shape kinds, flattening, trim paths, arc-length sampling. |
| `paint.py` | Colours and `url(#id)` paints → cairo sources (all gradient kinds, patterns). |
| `pango_bridge.py` | Pango text on pycairo surfaces without python3-gi-cairo (ctypes). |
| `registry.py` | Extension registries + support levels (`full`/`partial`/`none`). Unknown features warn once and are skipped. |
| `nodes/`, `assets/`, `effects/`, `transitions/` | Handlers, auto-imported by `registry.load_plugins()`: core nodes, particles, 2.5D camera/object3D; all asset kinds; 80 effects; 35 transitions. |
| `blend.py`, `masks.py`, `layout.py`, `constraints.py` | 35 blend modes; masks and track mattes; group flex layout; transform constraints. |
| `assets/text.py`, `text_animators.py`, `captions.py` | Pango text layout, range selectors + 24 presets, text on path; caption burn-in (plain, ASS, TTML/IMSC, SCC/CEA-608), transcription caches, SRT/VTT sidecars; per-letter 3D projects through the camera API. |
| `modifiers.py`, `deform.py`, `physics.py`, `camera.py` | Shape modifiers; tile deformers (hook `deform`); seekable rigid-body sim (hook `physics`); 2.5D projection (hook `camera`). |
| `audio/` | Mixer (tracks, buses, ducking, BS.1770 / dynamic normalisation, true-peak limiter), audio effects, `spectral.py` (phase vocoder, pitch shift, spectral gate), `spatial.py` (surround VBAP, ambisonics), amplitude envelopes for links/expressions. |
| `color.py` | Colour management "finish" hook: exposure, looks, tone mapping, output colour space/transfer. |
| `output.py`, `cli.py`, `coverage.py` | `<output>` rendering via ffmpeg (all codecs, posters, sidecars, parallel/resumable); CLI; support report. |

Hooks (`rc.hooks`) are installed by `render.hook_installer` functions: `captions`, `finish` (colour), `camera`, `physics`, `deform`, plus `ev.audio_amplitude` from `audio`.

## Contracts for handlers

All signatures are in the `registry.py` docstring. Essentials:

* **Buffers**: `Buf(px, x0, y0)`: `px` is `(h, w, 4)` float32, **premultiplied**, in the
  working space (linear light when `project/@linearLight` is true, the default). `x0, y0`
  place the tile in output-frame pixels. Return a new/larger tile rather than clipping spill.
* **Matrices**: 3×3 numpy affine mapping the handler's local space to output-frame pixels
  (render scale included). Draw with `rc.canvas_for(M, w, h, pad)` → `Canvas` whose cairo
  context already has `M`; then `canvas.to_buf(rc.linear)`.
* **Values**: always read attributes through `rc.ev` (`rc.ev.num(el, "radius", ctx, 4.0)`),
  so animation, expressions, links and instance overrides work everywhere.
* **Units**: document pixels. Multiply pixel distances used on buffers by `rc.scale`.
* **Time**: `ctx.t` is the current clock (composition time, or symbol-local inside instances);
  `ctx.comp_t` is always composition time.
* **Determinism**: randomness only from `rc.ev.seed_for(el, extra)` (a pure function of
  project seed, element id/seed and `extra`); never wall clock, never unseeded RNGs.
* **Unsupported cases**: `registry.warn_once(category, name, msg)` and degrade gracefully.
* Register with a truthful level: `@EFFECTS.register("glow", level=FULL)`; use `PARTIAL`
  with a `note` when something is approximated.
* XML booleans accept `true`, `false`, `1`, `0`, and surrounding whitespace.
  Use `ev.bool` for evaluated values or `values.parse_bool` for raw attributes.
  Never compare an attribute to the literal string `"true"` or `"false"`.

## Support reporting and conformance

`scenerender coverage` inventories declared registry support, including animation,
expressions, links, motion paths, node motion blur and stabilization. It does not
verify every attribute combination or inspect external asset contents. Passing XSD
validation and a clean coverage report do not establish complete conformance.

Lottie text layers support animated mask paths (arc-length placement, margins,
reverse, perpendicular orientation and forced alignment) and expression selectors
using the scene's pure expression language, including scalar/vector percentages,
`textIndex`, `textTotal`, `selectorValue` and time. Arbitrary JavaScript and embedded
glyph outlines remain outside that implementation; external Lottie expressions
should be baked when they require a full JavaScript runtime.

MXF output writes `maxCLL` and `maxFALL` into picture descriptors using the ULs
recognized by FFmpeg's MXF reader. `mxf.py` updates primers, metadata copies,
partition offsets and random indexes without re-encoding essence. Regression tests
verify FFmpeg reads the values and decoded frames remain identical.

## Core APIs for handlers

* `rc.render_node_at(el, t, ctx, effects=False) -> Out | None`: el rendered at composition time `t`
  in its own place, its effect stack skipped (temporal effects: echo, pixel-motion-blur, posterize-time).
* `rc.render_node(el, ctx, PM, box, force=True)`: render any node (sources for mattes, displacement, luma).
* Projective matrices: a node matrix may be a full 3×3 homography (bottom row ≠ 0 0 1), e.g. from the
  `camera` hook. The compositor then draws the node flat at a matching resolution, runs its
  deform/masks/effects there, and warps the tile (`raster.warp_projective`). Hooks may return `None`
  to cull a node (behind the camera).
* `scenerender.gl`: one headless moderngl context per process (`gl.context()`, `gl.available()`,
  texture/framebuffer helpers, `reset_after_fork()` for workers). Raise/handle `gl.GLUnavailable`.
* Motion blur: `rc.mb_center` is the frame's centre time while the Renderer supersamples; node
  `motionBlur="off"` nodes are drawn at it, `motionBlur="on"` nodes supersample themselves when the
  project has motion blur off.
* `layout` baseline alignment calls `assets.text.first_baseline(rc, asset, ctx) -> float` when present.

## Pinned rules (schema leaves these open)

* Transform `M = T(x,y)·R(rotation)·Skew·S(scaleX,scaleY)·T(−anchorX,−anchorY)`; node geometry
  lives in its box `(0,0)–(w,h)`; x/y are relative to the parent box.
* Times are composition time; instances and sequence children run their subtree on a local
  clock. Windows are half-open `[start, end)`; start/end markers win over start/end.
* A layer's media time counts from the layer's own `start` (not its group's): source time =
  `clipIn + (t − layer.start) · speed / timeStretch`, then loop/reverse, unless `timeRemap` is given.
* Group children run at `t' = start + (t − start − timeOffset)·timeScale`.
* Stacking by `z`, then document order. Non-isolated groups pass through to the parent buffer.
* Masks are node-local. Matte nodes are hidden unless `matteVisible`.
* `line` shapes run horizontally through the middle of the box. Beat-grid marker ids are 1-based.
* `link source="marker:id"` yields seconds since the marker.

## Running

```bash
uv venv --system-site-packages --python /usr/bin/python3 .venv   # system pycairo + PyGObject
uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/scenerender still static/fixtures/scene.xml --assets-dir digital-front -t 10 -o still.png --scale 0.5
.venv/bin/scenerender coverage static/fixtures/scene-render-explainer-180s.xml
.venv/bin/python -m pytest -q
```
