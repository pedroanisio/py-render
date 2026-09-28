# Packaging a video with scenerender-vpkg: how-to

This guide covers two jobs:
- turning a scene-render project into a `.vpkg.zip`
- rebuilding a video from a package someone gives you

The format, meaning every key, rule and default, is specified in [VPKG.md](VPKG.md). This guide only shows how to use it.

**Contents**
1. [Setup](#1-setup)
2. [Package a project, step by step](#2-package-a-project-step-by-step)
3. [Write the pipeline](#3-write-the-pipeline)
4. [Large downloads, fonts and credits](#4-large-downloads-fonts-and-credits)
5. [Set up render engines on a machine](#5-set-up-render-engines-on-a-machine)
6. [Rebuild a video from a package](#6-rebuild-a-video-from-a-package)
7. [When pack refuses](#7-when-pack-refuses)
8. [When verify or run fails](#8-when-verify-or-run-fails)
9. [Release a new version](#9-release-a-new-version)
10. [Checklist](#10-checklist)

## 1. Setup

The tool ships with py-render. It uses only the Python standard library, so any Python 3.10 or newer can run it, even without the renderer's dependencies installed.

```bash
pip install -e PATH/TO/py-render    # a checkout that includes scenerender/vpkg (main since 9ecc77b)
scenerender-vpkg --version          # scenerender-vpkg 1.0.0 (format 1.0)
```

Without installing, run it from a checkout:

```bash
cd ~/src/py-render-engine && python3 -m scenerender.vpkg --help
```

The examples below write `scenerender-vpkg`; `python3 -m scenerender.vpkg` works the same way.

## 2. Package a project, step by step

The example is `~/videos/moon-formation`: a 10 s scene written by `make_scene.py`, with two DejaVu fonts in `assets/`.

### 2.1 Generate a starter spec

```bash
scenerender-vpkg init ~/videos/moon-formation
```

```
wrote .../moon-formation/vpkg.json
  scenes: scene.xml=primary
  review the scene roles and write the pipeline steps before packing
```

`init` detects the following. It doesn't overwrite an existing `vpkg.json` unless you pass `--force`, and `--stdout` prints the spec instead of writing it.

| It finds | How |
|---|---|
| Scene documents and their roles | A file named `scene.xml` is preferred as `primary`. Names like `*.rust.*` or `*.js-engine.*` become `variant`s with `targets`. Names like `pre-`, `old`, `test`, `backup` or `3dtest` become `archive`. A scene whose references are missing, while another scene's all resolve, becomes `draft`. |
| Piper voices | `*.onnx` files named like `pt_BR-faber-medium.onnx` become `fetch` entries with the Hugging Face URL and the file's SHA-256 |
| Python packages | Imports in the project's scripts (`numpy`, `lxml`, `PIL` → `Pillow`, …) |
| Tools | `ffmpeg` and `piper` usage in scripts |
| Render steps | One per primary or variant scene, rendering its first `<output>` |

`init` can't know how the project is built, so the result is always a starting point.

### 2.2 Review and complete `vpkg.json`

Open the file and fill in what only you know.

- **`package`**: set `title` (init falls back to the README's first heading or the folder name), `description`, `languages`, and `license` and `authors` if you have them. `id` must stay a lowercase slug.
- **`scenes`**: check every role. There must be exactly one `primary`.
  - Mark engine-specific copies as `variant` with `targets` and `derivedFrom`, so `run --engine` picks them automatically.
  - Mark unfinished scenes `draft` and old ones `archive`; neither is packed unless an `include` glob names it.
- **`pipeline.steps`**: add the generate steps in build order ([section 3](#3-write-the-pipeline)), and give the render step a `to` path.
- **`engines`**: record the engines you actually rendered with, and how far (`full`, `stills`, `partial`). This is evidence for whoever receives the package, not a requirement.
- **`credits`**: see [section 4](#4-large-downloads-fonts-and-credits).

This is the finished `moon-formation/vpkg.json`:

```json
{
  "format": "scene-video-package",
  "formatVersion": "1.0",
  "package": {"id": "moon-formation", "version": "1.0.0", "title": "Como a Lua se formou",
              "description": "10 s explainer of the giant-impact origin of the Moon.", "languages": ["pt-BR"]},
  "scenes": [{"path": "scene.xml", "role": "primary"}],
  "requirements": {"tools": [{"name": "python", "version": ">=3.10", "for": ["scene"]}]},
  "pipeline": {
    "steps": [
      {"id": "scene", "kind": "generate", "run": ["python3", "make_scene.py"],
       "inputs": ["make_scene.py"], "outputs": ["scene.xml"]},
      {"id": "render", "kind": "render", "needs": ["scene"],
       "render": {"scene": "scene.xml", "output": "main", "to": "renders/lua.mp4",
                  "args": {"js": ["--anchor-mode", "position"]}}}
    ],
    "deliverables": [{"path": "renders/lua.mp4", "role": "master", "step": "render"}]
  },
  "engines": [{"id": "py", "tested": "full"}, {"id": "rs", "tested": "full"}],
  "credits": [{"paths": ["assets/DejaVuSans*.ttf"], "title": "DejaVu Sans",
               "license": "Bitstream Vera Fonts license", "url": "https://dejavu-fonts.github.io/"}]
}
```

### 2.3 Dry-run the pack

```bash
scenerender-vpkg pack ~/videos/moon-formation -n        # check and plan, write nothing
scenerender-vpkg pack ~/videos/moon-formation -n -v     # also list every file left out, with the reason
```

```
left out: 55 excluded by default (-v lists them)
plan ok: 4 files
```

**Read the left-out list.** A file you expected to ship but that isn't listed there with a reason you agree with needs an `include` glob. A file that ships but shouldn't needs an `exclude` glob.

**Common surprises:**
- **Unreferenced files are left out.** Images or meshes a scene doesn't read, such as spare downloads or superseded models, don't ship. That's intended.
- **Generated review stills are left out.** `renders/`, `frames/`, `stills/` and `bench/` are excluded by default. To ship a contact sheet or before/after image, add an anchored glob such as `"/*.jpg"`.
- **A glob without `/` matches at any depth.** `"*.jpg"` also picks up every texture JPG in every subfolder, so anchor it with a leading `/` when you mean only the project root.

If `pack` refuses, see [section 7](#7-when-pack-refuses).

### 2.4 Pack and verify

```bash
scenerender-vpkg pack ~/videos/moon-formation -o ~/videos/packages/
scenerender-vpkg verify ~/videos/packages/moon-formation-1.0.0.vpkg.zip
```

`pack` prints the size per file role and the zip it wrote. `verify` re-reads the package from scratch:
- every hash
- no stray entries
- every scene reference resolving inside the package

### 2.5 Test it the way a recipient would

Unpack somewhere outside the project and run the pipeline there. That proves the package doesn't secretly depend on your project folder.

```bash
scenerender-vpkg unpack ~/videos/packages/moon-formation-1.0.0.vpkg.zip /tmp/moon --fetch
scenerender-vpkg run /tmp/moon --engine py --list    # the commands it would run
scenerender-vpkg run /tmp/moon --engine py           # generate steps are up to date, so it only renders
```

## 3. Write the pipeline

The pipeline is the project's build instructions: your README's command list, made executable. Every step runs from the project folder, or from `cwd` if given.

### 3.1 Script steps

```json
{"id": "vo", "kind": "audio", "description": "pt-BR narration and vo.json",
 "run": ["python3", "make_vo.py"],
 "needs": ["scene"],
 "inputs": ["make_vo.py", "script.json"],
 "outputs": ["vo.json", "assets/vo/*.wav"],
 "requires": ["ffmpeg"], "optional": true}
```

- **`run`** is an argv list, never a shell string, so it works the same on every OS. To run a shell script, write `["sh", "render_frames.sh"]`. A first word of `python3` or `python` is replaced by the interpreter given to `run --python`, which defaults to the one running the tool.
- **`kind`** is one of `generate`, `validate`, `audio`, `mux`, `command` or `render`. It's informational, except for `render`.
- **`needs`** lists the steps that must run first. Steps run in dependency order, keeping the written order where they're free.
- **`inputs` and `outputs`** (globs) make re-runs cheap: a step whose outputs all exist and are newer than its inputs is reported "up to date" and skipped. List outputs for every step that makes files. A step without outputs, such as a validator, always runs.
- **`requires`** names executables that must be on PATH. **`optional: true`** means "skip this step if a requirement is missing, or if it fails". Use it for steps whose outputs ship in the package anyway, such as narration synthesis that needs Piper and a network download.
- **`idempotent: false`** marks a step that must not run twice. For example, v007's `insert_dogfight.py` edits `scene.xml` in place. Such a step is skipped whenever its outputs exist, unless it's named with `--only`.
- **`env`** sets environment variables for one step, for example `{"DF_T0": "52", "DF_TITLE": "0"}`.

### 3.2 Render steps

A render step names a scene, never an engine. The engine is chosen by whoever runs the package.

```json
{"id": "render", "kind": "render", "needs": ["build"],
 "render": {"scene": "scene.xml", "output": "out-master", "to": "renders/master.mp4"}}
```

- **`to`** is the file to produce. Engines that take an ad-hoc output path (py, rs, c) write it directly.
- **`output`** is the scene's `<output id>`. An engine that can only render document outputs (js) renders that one, and the result is copied to `to`. Give both fields whenever the scene has outputs.
- **`args`** adds per-engine flags that belong to this package, for example `{"js": ["--anchor-mode", "position"]}` for scenes authored with anchor-positioned `x`/`y`.

**Engine variants.** If `scene.rust.xml` is listed as

```json
{"path": "scene.rust.xml", "role": "variant", "targets": ["rs"], "derivedFrom": "scene.xml"}
```

then `run --engine rs` on a step that renders `scene.xml` uses `scene.rust.xml` instead. Give only one variant per engine a given `derivedFrom`. Other cuts of the video, such as a published version or a thumbnail scene, get their own render step instead.

**Stills.** Review frames render one process per time, which keeps memory bounded:

```json
{"id": "stills", "kind": "render",
 "render": {"scene": "scene.xml", "stills": [5, 37, 48], "to": "frames/still_{time}.png"}}
```

`{time}` is the time as written (`5`, `37.5`), and `{frame}` is the frame number at the scene's fps.

### 3.3 Deliverables

`pipeline.deliverables` names what the pipeline is for, such as the master MP4 or the stills folder. The README inside the package lists it.

## 4. Large downloads, fonts and credits

### 4.1 Fetch entries instead of bundling

Large inputs that anyone can download, such as voice models, HDRIs or datasets, are pinned rather than copied:

```json
"fetch": [{"path": "sources/voice/pt_BR-faber-medium.onnx",
           "url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx",
           "sha256": "858555e3a064209c57088fe6bd70c4c3dc54d03eaa00c45d5ecaf43a33f95aa7", "size": 63201294,
           "license": "see MODEL_CARD at huggingface.co/rhasspy/piper-voices"}]
```

- Get the hash with `sha256sum FILE`. For Hugging Face files, the `x-linked-etag` response header is the same SHA-256.
- `pack` leaves the file out of the zip but checks that your local copy still matches the hash.
- `unpack --fetch`, `run` or `scenerender-vpkg fetch DIR` downloads the file and rejects it if the hash differs.
- Keep small companion files, such as the voice's `.onnx.json`, in the project: they are bundled normally.
- `pack --bundle-fetch` puts the fetch files inside the zip, for an offline recipient.

### 4.2 Fonts

Nothing needs doing if the scene declares its fonts as `<font>` assets. Otherwise `pack` resolves every family the scenes name:

1. It looks in the project's own font files first: `fonts/`, `assets/fonts/` or any bundled `.ttf`, `.otf` or `.ttc`.
2. Then it looks in the system font folders.
3. It copies what it finds, and declares it in the packaged scenes.

**A family that isn't installed anywhere is an error.** That's deliberate: without it the video would render with a fallback font and look different on every machine. Download the real font into `fonts/` and keep its licence file beside it.

Add a `SOURCES.md` there giving the release URL and the archive's SHA-256, as `v004-sun/fonts/` does, so the next person can check the files.

`pack --no-system-fonts` resolves from project fonts only, which shows exactly what the project carries itself.

### 4.3 Credits and licences

`pack` collects credits from three places:
- `license` and `credit` attributes on the scene's asset elements, attached to that file's entry in `files[]`
- a project `assets.manifest.json` with an `assets[]` list (`path`, `license`, `credit` or `attribution`)
- the `credits[]` section of `vpkg.json`, for everything else: fonts, HDRIs, meshes, datasets

```json
"credits": [
  {"paths": ["fonts/Inter-*.ttf"], "title": "Inter 4.1", "license": "SIL OFL 1.1",
   "url": "https://rsms.me/inter/", "from": "fonts/Inter-OFL.txt"},
  {"paths": ["models/hdri/*.hdr"], "title": "Kloppenheim 06", "author": "Poly Haven", "license": "CC0"}
]
```

Bundled system fonts record their copyright and licence strings in `fonts[]` automatically.

## 5. Set up render engines on a machine

`run` calls engines by these default command names, which must be on PATH: `scenerender` (py), `scene-render-rs`, `scene-render-c` and `scene-render-js`.

To use other names or paths, create `~/.config/scene-vpkg/engines.json`. It's read on every `run`; `$XDG_CONFIG_HOME` is honoured.

```json
{
  "py": {"command": ["/home/me/.venvs/py-render/bin/python", "-m", "scenerender.cli"]},
  "rs": {"command": ["/home/me/src/rs-scene-render/target/release/scene-render"], "env": {"SR_GPU_BACKEND": "vulkan"}},
  "c":  {"command": ["/home/me/.cache/c-scene-render-build/scene-render"]},
  "js": {"command": ["node", "/home/me/src/js-render-engine/bin/scene-render.js"], "args": ["--gpu", "auto"]}
}
```

- **`command`** replaces the executable prefix.
- **`args`** is appended to every render by that engine.
- **`env`** is added to the engine's environment.

A single run can override the command with an environment variable:

```bash
VPKG_ENGINE_PY="python3 -m scenerender.cli" scenerender-vpkg run /tmp/moon --engine py
```

An engine id that isn't in the table (`--engine myengine`) runs `myengine` with no known argument forms. Give it `to`, `output`, `all` and `still` argument templates in `engines.json` to make it usable, using the same `{scene} {to} {output} {time} {frame}` placeholders as the built-in entries.

## 6. Rebuild a video from a package

```bash
scenerender-vpkg info    sun-1.0.0.vpkg.zip                # what it is, its steps and downloads
scenerender-vpkg verify  sun-1.0.0.vpkg.zip                # integrity before trusting it
scenerender-vpkg unpack  sun-1.0.0.vpkg.zip ~/work/sun --fetch
scenerender-vpkg run     ~/work/sun --engine rs --list     # the plan
scenerender-vpkg run     ~/work/sun --engine rs            # everything that is not up to date
```

**Controlling what runs:**

| You want to | Run |
|---|---|
| Only render | `run DIR --engine rs --only render` |
| Rebuild from a step onwards | `run DIR --engine py --from vo` |
| Stop after a step | `run DIR --until build` |
| Re-run steps that are up to date | add `--force` |
| Use a specific Python for script steps | add `--python ~/.venvs/video/bin/python` |
| Run straight from the zip | `run sun-1.0.0.vpkg.zip --engine py` (unpacks beside it, or into `--dir`) |

**Things to know:**
- **Unpacked files are all up to date.** `unpack` gives every file the same modification time, so a fresh unpack treats every generated file as current, and only render steps (whose outputs aren't shipped) run.
- **Fonts are set up for you.** `run` points `FONTCONFIG_FILE` at `project/_vpkg/fonts.conf` when the package bundles system fonts. Scripts and fontconfig-based engines then see the same fonts the scenes declare.
- **Without the tool**, unzip the package, download each `fetch` entry and check its hash, export `FONTCONFIG_FILE=project/_vpkg/fonts.conf`, and run the steps listed in the package's `README.md` from `project/`.

## 7. When pack refuses

`pack` prints every problem at once and writes nothing. Each problem has a usual fix, and some also have an override flag that turns the error into a warning.

| Message | Cause | Usual fix | Override |
|---|---|---|---|
| `scene.xml: <image src="assets/x.png"> not found` | A scene reads a file that doesn't exist | Create the file, or add the step that makes it to the pipeline with the file in its `outputs`. For a downloadable file, add a `fetch` entry. | none |
| `font family 'Inter' is not declared and not installed here` | The scene names a font the machine doesn't have | Put the real font files in `fonts/`, with their licence | `--allow-missing-fonts` |
| `make_audio.py:22: error absolute-path: VOICE = "/home/me/..."` | A script reads a machine-specific path | Build the path from the script's folder: `os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources", ...)` | `--allow-nonportable` |
| `make_art.py:13: error outside-project: ... os.path.join(HERE, "..", "other-project")` | A script reads another project | Copy the files it needs into this project, for example `sources/house/`, with a README saying where they came from | `--allow-nonportable` |
| `warning temp-path: "/tmp/vo.wav"` (warning only) | A fixed scratch path | Use `tempfile.mkdtemp()` or `tempfile.gettempdir()` | none needed |
| `... is a remote reference (not portable ...)` | A scene reads an `http(s)` URL | Download it into the project, or make it a `fetch` entry and point the scene at the local path | `--allow-remote` |
| `references '../x' outside the project; only scene documents can be rewritten` | A glTF or other non-scene file points outside the project | Move the file into the project | none |
| `local file does not match the fetch sha256` | The local copy of a pinned download changed | Restore the original, or update `sha256` and `size` if the new file is intended | none |
| `scene document not listed in vpkg.json scenes; left out` (warning) | A scene isn't in `scenes[]` | List it with the right role | none needed |
| `invalid vpkg.json: ...` | Schema or pipeline error | Fix the key it names. Common causes: two `primary` scenes, a `needs` naming a missing step, a render step whose `scene` isn't listed, a path starting with `/` or containing `..` | none |

If a lint hit is a false positive, such as a deliberate per-OS list of system font folders, end that line with `# vpkg: allow`.

Scene references outside the project are fixed automatically: they're copied to `_external/`, and the packaged scene is rewritten to use them. `pack` prints `external: ../shared/logo.png -> _external/shared/logo.png` for each.

## 8. When verify or run fails

| Message | Meaning |
|---|---|
| `not a readable zip ...; the file may be truncated` | An incomplete copy or download. Transfer the file again. |
| `X: size or SHA-256 differs from vpkg.json` | The file was changed after packing, or corrupted in transfer |
| `X: in the zip but not in vpkg.json` | The zip was edited by hand. Repack from the project instead. |
| `font families resolved from the host: ...` (warning) | A packaged scene still names a family it doesn't declare, usually an archive scene added by `include` from an older tool version. Repack. |
| `[render] a render step: choose an engine with --engine` | Pass `--engine`, or run only non-render steps with `--until` |
| `[vo] needs ffmpeg on PATH` | Install the tool. An optional step is skipped instead, and its outputs from the package are used. |
| `engine 'js' has no still command` | That engine can't render single frames. Use another engine for still steps. |
| `[insert] skipped: not idempotent and its outputs exist` | Expected. Run it with `--only insert` if you really want to redo it. |

## 9. Release a new version

1. Change the project, then bump `package.version`: patch for fixes, minor for new content or steps, major for a different cut.
2. Pack with a fixed date so the zip is reproducible, and record it:

   ```bash
   SOURCE_DATE_EPOCH=$(date -d 2026-09-28 +%s) scenerender-vpkg pack PROJECT -o packages/
   ```

   The same project and date always give a byte-identical zip, so its SHA-256 can be published beside it. Without `SOURCE_DATE_EPOCH`, entries are dated 1980-01-01 and no pack date is recorded; the zip is still reproducible.
3. Run `verify`, then unpack and render once, as in [section 2.5](#25-test-it-the-way-a-recipient-would).

## 10. Checklist

- [ ] Exactly one `primary` scene. Engine copies are `variant` with `targets` and `derivedFrom`; unfinished and old scenes are `draft` or `archive`.
- [ ] Every generate step has `inputs` and `outputs`. Steps that need extra tools or network have `requires` and `optional`. In-place editors have `idempotent: false`.
- [ ] The render step has both `output` (the scene's output id) and `to`.
- [ ] Large public downloads are `fetch` entries with `sha256` and `size`.
- [ ] Fonts the scenes name are declared, or present in `fonts/` with their licence.
- [ ] Credits cover fonts, images, meshes, HDRIs and voices.
- [ ] `pack -n -v` leaves out only what you expect, and prints no errors.
- [ ] `verify` passes, and a fresh `unpack` plus `run --engine ...` produces the deliverable.
