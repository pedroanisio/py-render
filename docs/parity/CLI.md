# CLI parity: Rust `scene-render` vs Python `scenerender`

Sources compared (read-only):

- Rust: `crates/scene-render/src/main.rs` (clap derive, 1702 lines), `changes.rs` (frame fingerprints for `--changed-only`/`watch`), rs-scene-render HEAD `f296a4c`. `--help` text taken from the existing `target/release/scene-render` binary for every subcommand (it lists all 12 subcommands, so it is not stale relative to the source).
- Python: `scenerender/cli.py` (148 lines, entry point `scenerender = scenerender.cli:main` in `pyproject.toml`; there is no `__main__.py`), dispatching to `output.py` (`render_outputs`, `jobs_from_args`), `qa.py` (`check`, `EXIT_QA = 4`), `coverage.py`, `document.py` (`load`), `publish.py`. No `vpkg` module exists.

## 1. Subcommand matrix

| Rust subcommand | Python equivalent | Status |
|---|---|---|
| `validate FILE...` | `validate SCENE` | different (Python: one file, XSD + Schematron only, no asset checks, no JSON, no codes) |
| `inspect FILE [--json]` | none | missing-in-Python |
| `eval FILE [-t/-f] [--format json]` | none (closest: `still`, but that renders pixels) | missing-in-Python |
| `render FILE -o out.png` (GPU, PNG frames only) | `still -t T -o out.png` (single/multiple stills) and `render -o DIR/` (PNG sequence) | different (names collide; Rust `render` is PNG-only, Python `render` is the full encoder) |
| `watch FILE -o PAT` | none | missing-in-Python |
| `encode FILE` (document outputs or ad-hoc `-o`) | `render SCENE [-o OUT]` | different (same job, different name and flags) |
| `simulate FILE` (write physics cache) | none as a command; physics writes `physics@cache` as a side effect of rendering (`physics.py` ~l.1910) | missing-in-Python (as command) |
| `bake-volume FILE --object ID -o DIR` | none | missing-in-Python |
| `resolve FILE` (fill generated-media / transcription caches, pin SHA-256) | none (`assets/generated.py`: "The renderer never calls @provider") | missing-in-Python |
| `gpus` | none (GPU is auto-selected; `--no-gpu` / `SCENERENDER_GPU=0` only) | missing-in-Python |
| `explain [CODE]` | none | missing-in-Python |
| `completions SHELL` | none (argparse, no completer) | missing-in-Python |
| none | `check SCENE` (QA: flashes, contrast, safe areas, captions on a 0.25-scale pass) | Python-only (Rust runs the same accessibility checks inside `encode` and reports them under `--strict`; no standalone command) |
| none | `coverage SCENE [--all]` (registry feature coverage) | Python-only |
| none | `still SCENE -t T[,T...] -o OUT` | Python-only as a command (Rust `render -t/--frames` covers stills) |
| `help` | argparse `-h` | same |

Global (all Rust subcommands): `--color auto|always|never` (env `SCENE_RENDER_COLOR`), `--threads N` (env `SR_THREADS`, default every core), `-h`, `-V/--version`. Python: only `-h`; no `--version`, no colour switch, no global threads flag (`--jobs` is per `render`; env `SCENERENDER_THREADS` is internal).

## 2. Per-subcommand option tables

### 2.1 `validate`

| Rust flag | Meaning | Default | Python equivalent |
|---|---|---|---|
| `<FILE>...` | one or more documents | required | `scene` (exactly one) |
| `--format human|json` | output format | human | MISSING |
| `--no-assets` | skip file existence and SHA-256 checks | off (checks on) | n/a: Python `validate` never checks assets (`cmd_validate` calls `document.load(strict=False)`; sha256 is checked only in `document.py` for includes and `<data>`, and at render time in `assets/video.py`) |
| `--base-dir DIR` | base for relative URIs | each document's dir | MISSING (`--assets-dir` exists on render/still/check but not on `validate`) |
| `--deny-warnings` | warnings fail the exit status | off | MISSING (Python has no warning tier in validate) |
| `-q, --quiet` | only per-file summary | off | MISSING |
| (none) | cap the list | all | `--max-errors N` (Python-only; 0 = all) |

### 2.2 `inspect`

| Rust flag | Meaning | Default | Python |
|---|---|---|---|
| `<FILE>` | document | required | MISSING (whole subcommand) |
| `--json` | full typed model as JSON | off (text summary) | MISSING |
| `--no-assets` | skip asset checks | off | MISSING |

### 2.3 `eval`

| Rust flag | Meaning | Default | Python |
|---|---|---|---|
| `<FILE>` | document | required | MISSING (whole subcommand) |
| `-t, --time S` | composition time (negatives allowed; conflicts with `-f`) | 0.0 | MISSING (`still -t` renders pixels instead) |
| `-f, --frame N` | frame index at project fps | none | MISSING |
| `--variant ID` | variant | none | `--variant` on still/render/check |
| `--layout ID` | layout | none | `--layout` on still/render/check |
| `--param ID=VALUE` (repeatable, parser-validated) | parameter | none | `--param` (repeatable; `k,_,v = partition("=")`, so `--param foo` silently sets `foo=""`; Rust rejects via `parse_param`) |
| `--row N` | data row for batch rendering | none | MISSING (no `--row`; no data-row batch in Python) |
| `--data ID` | data source of `--row` (requires `--row`) | first source | MISSING |
| `--format summary|json` | one line per node or full FrameGraph JSON | summary | MISSING |
| `--bench` | evaluate every frame, print median/p95/max ms | off | MISSING |
| `--no-assets` | skip asset checks | off | MISSING |

### 2.4 `render` (Rust: PNG frames on GPU) vs Python `still` / `render`

| Rust flag | Meaning | Default | Python equivalent |
|---|---|---|---|
| `<FILE>` | document | required | `scene` |
| `-t, --time S` | one frame at time | none | `still -t T[,T,...]` (also accepts marker ids, e.g. `r.doc.markers[spec]`; Rust has no marker lookup) |
| `-f, --frame N` | frame index | none | MISSING (Python `still` takes seconds only) |
| `--frames A..B` / `A..=B` | frame range | none | partially: `render --from S --to S` (seconds, not frames) with `-o DIR/` |
| `-o, --output PATH` | PNG; `%04d` or `{frame}` for several | required unless `--bench` | `still -o` (default `still.png`; multi-time names `{stem}_{t:07.3f}.png`) / `render -o DIR/` (`frame_%06d.png`; `%` patterns accepted) ; `{frame}` token MISSING |
| `--bit-depth 8|16` | PNG bits per channel | 8 | MISSING (still saves 8-bit RGB via PIL; 16-bit only through video codecs/EXR/TIFF sequences) |
| `--variant`, `--layout`, `--param` | as eval | none | same names |
| `--row`, `--data` | data row batch | none | MISSING |
| `--bench` | GPU-memory timing, no files | off | MISSING |
| `--stats` | per-frame renderer stats as JSON lines on stderr | off | MISSING (`-v` logs only) |
| `--pipelined` | with `--bench`, one frame in flight | off | MISSING |
| `--strict` | exit 1 if anything not rendered as authored (unsupported content, shader fallbacks, evaluator warnings) | off | different: Python `--strict` is the hidden default meaning "refuse invalid XML"; no "rendered as authored" gate (closest is `coverage` plus `check`) |
| `--quality draft|preview|final` | override `project@quality` (draft = half-size, <=2 motion-blur samples, no grain; preview = <=4 samples) | doc value | MISSING (nearest: `--scale 0.5`, `--no-motion-blur`, `check` scale 0.25; none read `project@quality`) |
| `--changed-only` | re-render only frames whose fingerprint changed (`.scene-render-frames.json` beside outputs) | off | partially: `--frames-dir DIR` keeps frames and resumes on rerun (existence-based, not content-fingerprint-based, so an edited scene reuses stale frames) |
| `--threads` / global | CPU workers | all cores | `--jobs N` (frame worker processes) |

### 2.5 `watch`

| Rust flag | Meaning | Default | Python |
|---|---|---|---|
| `<FILE>` | document | required | MISSING (whole subcommand) |
| `-o, --output PAT` | PNG path with `%04d`/`{frame}` (required) | none | MISSING |
| `--frames RANGE` | frame range | every frame | MISSING |
| `--quality TIER` | as render | doc | MISSING |
| `--interval MS` | poll interval | 250 | MISSING |
| `--max-runs N` | hidden; stop after N runs (tests) | none | n/a |

### 2.6 `encode` (Rust) vs Python `render`

| Rust flag | Meaning | Default | Python equivalent |
|---|---|---|---|
| `<FILE>` | document | required | `scene` |
| `--output ID` (repeatable) | only these `<output>` ids | all | `--output ID` (repeatable) |
| `-o, --path PATH` | ad-hoc output instead of document outputs | none | `-o, --out OUT` (note: Rust `-o` is `--path` here but `--output` is the id filter; Python is the reverse naming: `-o/--out` path, `--output` id) |
| `--codec NAME` | codec of ad-hoc output | from extension | MISSING (codec only from extension via `_EXT_CODEC` or from `<output codec>`) |
| `--out-dir DIR` | base for relative output paths | document dir | MISSING (paths relative to CWD) |
| `--representation R` | preferred asset representation | none | `--representation` |
| `--hw auto|software|nvenc|videotoolbox|vaapi|qsv|amf` | encoder family for H.264/H.265/AV1 | auto | MISSING; only `--no-gpu` (disables NVENC and GPU effects; sets `SCENERENDER_GPU=0`); NVENC is the only HW family probed (`output.py` `nvenc_ffmpeg`) |
| `--start S` / `--end S` | time range override | output's range | `--from S` / `--to S` |
| `--no-upload` | skip destinations | uploads on | inverse: uploads are OFF unless `--publish` (non-`file` destinations only; `file` destinations always copy) |
| `--parallel auto|N` | time segments encoded concurrently then joined without re-encode | auto | different: `--jobs N` is frame-worker processes (default 1; 0 = one per CPU, capped at 8) feeding a single encoder; no segment-parallel encode |
| `--param ID=VALUE` | parameter | none | `--param` |
| `--row N`, `--data ID` | data row | none | MISSING |
| `--json` | print each report as JSON (frames, size, fps, seconds, stage seconds, encoder, adapter, loudness, true peak, uploads, unsupported, accessibility, quality, segments) | off | MISSING (Python prints `name: file (+N files)`) |
| `--strict` | exit 1 on unsupported/evaluator warnings/accessibility findings (files still written) | off | MISSING as flag; QA errors (accessibility/safe-area at level `error`) always fail with exit 4 unless `--no-qa` |
| `--quality TIER` | quality tier | doc | MISSING |
| (no flag) | scale | doc | `--scale F` (output px per document px) |
| (no flag) | fps | output/doc fps | `--fps F` |
| (no flag) | CRF | per codec | `--crf N` |
| (no flag) | variant / layout override | output's | `--variant`, `--layout` |
| (no flag) | assets dir | doc dir | `--assets-dir DIR` |
| (no flag) | audio off | audio on | `--no-audio` |
| (no flag) | motion blur off | per project | `--no-motion-blur` |
| (no flag) | keep frames / resume | none | `--frames-dir DIR` |
| (no flag) | skip QA | QA on | `--no-qa` |
| (no flag) | lenient validation | strict | `--lenient` (and hidden no-op `--strict`) |
| (no flag) | verbose | off | `-v/--verbose` |
| (no flag) | publish | off | `--publish` |

### 2.7 `simulate`

| Rust flag | Meaning | Default | Python |
|---|---|---|---|
| `<FILE>` | document | required | MISSING (command) |
| `-o, --output FILE` | cache file | document's `physics@cache` | MISSING (Python writes `physics@cache` implicitly while rendering) |

### 2.8 `bake-volume`

| Rust flag | Meaning | Default | Python |
|---|---|---|---|
| `<FILE>` | document | required | MISSING |
| `--object ID` | instantiated pyro object id (namespaced) | required | MISSING |
| `-o, --output DIR` | new cache dir; never replaces | required | MISSING |
| `--first N` | first frame inclusive | 0 | MISSING |
| `--end N` | last frame exclusive | project duration | MISSING |
| `--max-mib N` | cache size cap | 65536 | MISSING |
| `--param ID=VALUE` | parameter | none | MISSING |
| `--json` | receipt as JSON | off | MISSING |

### 2.9 `resolve`

| Rust flag | Meaning | Default | Python |
|---|---|---|---|
| `<FILE>` | document (its `cacheSha256` attributes rewritten in place) | required | MISSING |
| `--check` | report staleness, change nothing, exit 1 if stale | off | MISSING |
| `--force` | remake every target | off | MISSING |
| `--only ID` (repeatable) | restrict ids | all | MISSING |
| `--allow-cloud` | allow openai/elevenlabs providers | off | MISSING |
| `--no-store` | skip shared result store | store on | MISSING |
| `--json` | results as JSON | off | MISSING |

Providers in Rust: whisper (whisper.cpp), piper, audioforge, openai, elevenlabs, or any `scene-render-provider-NAME` program speaking a JSON protocol. Python never calls `@provider` and only uses `@cache` when its SHA-256 equals `@cacheSha256` (`assets/generated.py` docstring).

### 2.10 `gpus`, `explain`, `completions`

| Subcommand | Rust flags/args | Python |
|---|---|---|
| `gpus` | `--json`; honours env `SR_GPU_ADAPTER`, `SR_GPU_BACKEND`; exit 0 if an adapter is chosen, else 2 | MISSING; Python env knobs `SCENERENDER_GPU`, `SCENERENDER_NO_GPU`, `SCENERENDER_GL_BACKEND` |
| `explain` | `[CODE]` (case-insensitive; e.g. `S06`, `C21`, `R24-fill`); no arg lists all | MISSING |
| `completions` | `<SHELL>` bash/elvish/fish/powershell/zsh | MISSING |

### 2.11 Python-only options

| Command | Flag | Meaning | Default |
|---|---|---|---|
| still/render/check | `--scale F` | output px per document px | 1.0 (check: 0.25) |
| still/render/check | `--lenient` / hidden `--strict` | render invalid documents with a warning | strict |
| still/render/check | `--assets-dir DIR` | asset base | scene dir |
| still/render/check | `-v` | INFO logging | WARNING |
| still | `-t, --time` (required), `-o` | seconds or marker id, comma list | `still.png` |
| render | `--fps`, `--crf`, `--from/--to`, `--frames-dir`, `--jobs`, `--no-audio`, `--no-motion-blur`, `--no-gpu`, `--publish`, `--no-qa` | see section 2.6 | see above |
| check | `--from/--to` | range | whole output |
| validate | `--max-errors N` | list cap | 0 (all) |
| coverage | `--all` | also list supported features | off |

## 3. Output formats, exit codes, diagnostics

### Rust

- Diagnostic type (`sr_model::Diagnostic`): `severity` (error|warning), `code` (stable), `message`, `loc {line, column}`, `path` (XPath-like, e.g. `/scene/composition/layer[2]`), optional `help`. Code families (`diag.rs`): `XML`, `S01`-`S12` structure, `V1`-`V4`, `C1`-`C44`, `R1`-`R25-*` Schematron assert ids, `A01`-`A07` assets, `W01`. `explain` (no arg) lists 230 codes by stage: 190 rules, 18 eval, 13 structure, 7 assets, 1 model, 1 xml. `explain CODE` prints stage, summary, and for Schematron rules the `context:` and `test:` XPath (e.g. `R24-fill`: `@fill: url(#id) must name an element of paints.`).
- Human format: rustc-style `error[CODE]: message`, `--> file:line:col`, source excerpt with caret, `= help:`, `= at /xpath`; then `valid|invalid FILE: N error(s), M warning(s)`. Colour via `--color`/`NO_COLOR`.
- `validate --format json`: `{"valid": bool, "files": [{file, valid, errors, warnings, io_error?, diagnostics:[...]}]}` pretty-printed on stdout. `eval --format json` prints the FrameGraph. `inspect --json`, `encode --json` (one report per line), `bake-volume --json`, `resolve --json`, `gpus --json`.
- Exit codes: 0 ok; 1 invalid / (warnings with `--deny-warnings`) / `--strict` problems / output failed / `resolve --check` stale / bake failure; 2 usage, I/O (unreadable or non-UTF-8 file, no matching `<output>`, no GPU, bad ad-hoc codec, unknown explain code, `gpus` found no adapter). BrokenPipe on stdout is exit 0. clap usage errors exit 2. No separate code for QA failure (QA findings are warnings unless `--strict`).
- Progress: stderr; live counter on a TTY, else a line about every 10 s with fps, ETA and per-stage times; prints the render adapter and warns on a software adapter.
- Stdout/stderr split: reports on stdout, errors/progress on stderr.

### Python

- Validation messages are plain strings `"{line}: {message}"` from lxml XSD error log plus `Schema.semantic_errors` (Schematron); no codes, no path, no excerpt, no severity, no JSON. `validate` prints `FILE: INVALID (N errors)` and `  line <msg>` lines, or `FILE: valid scene-render <version>`; `--max-errors` truncates.
- `render`: one line per output on stdout `name: file (+N files)`; QA report and failures on stderr (`name: QA FAILED (E errors, W warnings)` plus findings). `check` prints `qa.report` text for every output on stdout.
- Exit codes (module docstring): `render` 0 ok, 1 an output failed (RuntimeError/OSError/ValueError, or a destination failed), 2 nothing to render (no `-o` and no `<output>`, or `--output` id not found), 4 an output failed QA at level error. `check`: 0 passed (warnings allowed), 4 errors. `validate`: 0 valid, 3 invalid or `SceneError`. `still`/`coverage`: 0 (exceptions propagate as a traceback, exit 1). Strict load failure in `render`/`still`/`check` raises `SceneError` out of `_open` (traceback, exit 1) rather than a formatted report.
- Collision to note: Rust exit 1 = invalid and exit 2 = I/O/usage; Python exit 3 = invalid and exit 2 = nothing to render; Python 4 = QA. Scripts keyed on exit codes are not portable between the two.

## 4. Behavioural differences that matter

1. Validation default. Rust `validate`/`inspect`/`eval`/`render`/`encode` all load through `sr_model::load_file` with `LoadOptions::default()` (asset existence and SHA-256 verified; invalid documents refused with exit 1; no lenient mode at all, so a Rust flag equivalent to `--lenient` does not exist). Python is strict by default but offers `--lenient`; its `validate` never checks assets (`A01`-`A07` family absent) and its Schematron coverage is `Schema.semantic_errors` (a port, not every R-id verified here).
2. "Strict" means different things. Rust `--strict` (render, encode) = fail with exit 1 when anything was not rendered as authored (unsupported content, pass-through effects, crossfaded transitions, evaluator warnings, accessibility findings); files are still written. Python `--strict` is a hidden no-op for the default validation behaviour. Python has no "unsupported" ledger at render time (only `coverage`, a static registry listing).
3. Parameters, variants, layouts. Both take `--param ID=VALUE` (repeatable), `--variant`, `--layout`. Rust validates `ID=VALUE` syntax with `parse_param`; Python accepts anything. In Python an `<output>`'s own `layout`/`variant` attributes are overridden by the CLI flags (`jobs_from_args`); Rust `encode` has no `--variant`/`--layout` flags at all (only on `eval` and `render`), so per-output variants come only from the document. Rust has no `--scale`/`--assets-dir`/`--lenient`.
4. Data rows. Rust `--row N [--data ID]` batch-renders a data row on eval/render/encode; Python has no equivalent (grep of `render.py`/`document.py` finds no row selection).
5. Frames dir / resume. Python `--frames-dir` keeps frames and resumes by presence; with multiple outputs it appends `/<output name>` (`run_job`). Rust `render --changed-only` and `watch` use content fingerprints (evaluated frame, element source text, document-outside-composition, referenced files, render settings) stored in `.scene-render-frames.json`; when motion blur or time effects are in use any edit re-renders every frame (`changes.rs` header). Python resume can serve stale frames after an edit.
6. Jobs / parallelism. Python `--jobs` (default 1, "least CPU time"; 0 = one per CPU capped at 8) = frame worker processes, each reopening the document. Rust `--threads` (global, default all cores) = CPU stage threads; `encode --parallel auto|N` encodes time segments concurrently and joins them without re-encoding (auto: 3 with a hardware encoder, else half the cores up to 4).
7. Motion blur. Python `--no-motion-blur` disables project motion blur. Rust has no such flag; `--quality draft` caps blur samples at 2, `preview` at 4, `final` as authored, and draft also drops grain and renders at half size.
8. Representation. Both `--representation` (Rust: `encode` only; Python: still/render/check, and falls back to `<output representation>`).
9. Formats/codecs. Rust `encode -o` default codec by extension: `.mov` prores, `.mxf` dnxhr, `.mkv` ffv1, `.webm` vp9, `.gif`, `.webp`, `.png` apng (or png-sequence if the file name has `%`), `.jpg/.jpeg` jpeg-sequence, `.exr` exr-sequence, `.tif/.tiff` tiff-sequence, `.wav/.m4a/.mp3` audio-only, anything else h264; `--codec` overrides. Python `_EXT_CODEC`: `.mp4/.m4v/.mov/.mkv` h264 (container from extension, so `.mov` is H.264 not ProRes, `.mkv` is H.264 not FFV1), `.webm` vp9, `.mxf` dnxhr, `.gif`, `.apng`, `.webp`, `.png` png-sequence (even without `%`; written as `stem_%06d.png`), `.jpg`, `.tif`, `.exr`, audio `.wav .m4a .mp3 .flac .aac .ogg .opus .mka`; unknown extension is a hard `SystemExit`. Rust `.png` without `%` is a single APNG; Python `.png` is a numbered sequence. Rust has `.flac/.ogg/.opus/.aac/.mka` only via `--codec`/document `<output>`. Hardware: Rust `--hw` picks software/nvenc/videotoolbox/vaapi/qsv/amf; Python only NVENC via ffmpeg probing, switched off by `--no-gpu`.
10. Audio. Rust `encode` always mixes audio per `<output audio>` and reports LUFS integrated and dBTP true peak (`--json`: `loudness`, `true_peak`, `audio_seconds`); no `--no-audio`. Python `--no-audio` skips it; sequence outputs with audio also write a sidecar WAV; audio-only outputs fail with `RuntimeError` if the document has no audio.
11. Sidecars and deliverables. Python `render` also writes poster/thumbnail stills (`write_still`), caption sidecars (`write_captions`), spherical/HDR metadata post-processing, and copies to `file://` destinations, uploads others with `--publish` (s3, gcs, azure-blob, http-put, sftp, webhook; `publish.py`). Rust `encode` uploads destinations by default and skips with `--no-upload`; its report has `uploads`.
12. QA. Python `check` + built-in QA gate at render: accessibility/safe-area at level `error` blocks delivery of that output (exit 4, nothing written for that output) unless `--no-qa`. Rust reports accessibility findings in the encode report and only fails with `--strict` (files still written).
13. Output naming/flag collision. Rust: `render -o/--output` is a PNG path; `encode --output` is an id filter and `-o/--path` is the path. Python: `render -o/--out` path and `--output` id filter; `still -o/--out`.
14. Scale/fps/time. Python has `--scale`, `--fps`, `--crf`, `--from/--to` (seconds); Rust has `--start/--end` (seconds, encode) and `--frame/-f` and `--frames A..B` (frames, render), no scale/fps/crf overrides (use `<output>` attributes or `--quality`).
15. GPU. Rust requires a GPU adapter for render/encode (software/GL adapters accepted with a warning; exit 2 if none; `audio-only` outputs skip GPU init); `gpus` lists them. Python uses the GPU opportunistically, falls back to CPU, `--no-gpu`.
16. Bit depth. Rust `render --bit-depth 16` for PNG; Python still is 8-bit PNG.
17. Version/help. Rust has `-V` and an Examples/Exit-status epilogue; Python none.

## 5. Prioritised gap list for Python

P0 (blocks tooling and conformance parity)
1. Structured diagnostics: carry `code`, `severity`, `line:col`, XPath `path`, `help` through `validate`; add `validate --format json` with the Rust schema (`valid`, `files[]` of `file, valid, errors, warnings, io_error, diagnostics[]`). Needs the XSD/Schematron error to code mapping (S/C/R-id/A/W) and warnings tier.
2. `validate` multi-file, `--deny-warnings`, `--quiet`, `--no-assets` (and asset existence + SHA-256 checks as A01-A07, on by default), `--base-dir`; align exit codes (0 valid, 1 invalid, 2 usage/I-O) and, for `render`, decide the mapping of 3 and 4.
3. `explain [CODE]` backed by a codes table (all 230 codes: XML, S01-S12, V1-V4, C1-C44, R-ids with context/test, A01-A07, eval codes, W01).
4. Parameter parsing: reject `--param` without `=` (Rust `parse_param`).

P1 (feature parity of the commands users run)
5. `eval` (FrameGraph summary and JSON, `-t/-f`, `--bench`, `--no-assets`) and `inspect [--json]`.
6. Data-row batch: `--row N [--data ID]` on eval/render/encode.
7. `--quality draft|preview|final` honouring `project@quality`; and a Rust-style `--strict` ("rendered as authored") with an unsupported/fallback ledger (Python currently surfaces this only in `coverage`). Resolve the name clash with Python's no-op `--strict`.
8. `--frames A..B`, `-f/--frame`, `{frame}` token, `--bit-depth 8|16` for PNG output.
9. Content-fingerprint incremental rendering (`--changed-only`, `watch`): replace/augment presence-only `--frames-dir` resume, which can go stale.
10. `render --json` (per-output report with frames, size, fps, seconds, encoder, uploads, warnings, accessibility, loudness/true peak) and a stderr progress meter matching Rust (TTY counter, 10 s lines).
11. Extension to codec defaults: `.mov` prores, `.mkv` ffv1, `.png` apng vs sequence (or document the intentional difference); `--codec` for ad-hoc output; `--out-dir`.

P2 (workflow and integrations)
12. `resolve` (`--check`, `--force`, `--only`, `--allow-cloud`, `--no-store`, `--json`) with providers whisper/piper/audioforge/openai/elevenlabs/external `scene-render-provider-NAME`; pin `cacheSha256` in place.
13. `simulate [-o FILE]` as an explicit command for `physics@cache`; `bake-volume` (`--object --first --end --max-mib --param --json`).
14. `--hw auto|software|nvenc|videotoolbox|vaapi|qsv|amf` and segment-parallel `--parallel auto|N` encode with lossless join (Python `--jobs` is frame-level only).
15. `gpus [--json]`, global `--color`/`NO_COLOR`, global `--threads`, `-V/--version`, `completions SHELL`.
16. `--no-upload` as the Rust default behaviour question: Rust uploads unless told not to; Python requires `--publish`. Pick one policy and document it.
17. `--variant`/`--layout`/`--scale`/`--assets-dir`/`--lenient` stay Python-only unless Rust gains them (conversely, Rust `encode` lacks `--variant`/`--layout`); list in the parity doc as intentional differences.

Python-only features worth keeping (no Rust equivalent): `check` (quick QA pass), `coverage`, `still` multi-time/marker ids, `--lenient`, `--scale`, `--fps`, `--crf`, `--no-audio`, `--no-motion-blur`, `--no-gpu`, `--no-qa`, `--publish`, `--max-errors`.
