# Scene video packages (`.vpkg.zip`), format 1.0

A scene video package holds one scene-render video and everything needed to rebuild it on another machine, except the render engine:
- the scenes and every file they read
- the fonts they name
- the scripts that generate them, and the pipeline that runs those scripts
- pinned downloads for the large inputs
- credits and a SHA-256 for every file

`scenerender-vpkg` (`python -m scenerender.vpkg`) writes and reads packages; for step-by-step instructions (packaging a project, writing the pipeline, engine setup, troubleshooting) see [VPKG-HOWTO.md](VPKG-HOWTO.md). It uses only the Python standard library and never imports the renderer. The manifest schema is [`scenerender/vpkg/schema/vpkg-1.0.schema.json`](../scenerender/vpkg/schema/vpkg-1.0.schema.json).

```bash
scenerender-vpkg init   PROJECT                 # write a starter PROJECT/vpkg.json
scenerender-vpkg pack   PROJECT -o packages/    # -> packages/<id>-<version>.vpkg.zip
scenerender-vpkg verify PKG.vpkg.zip            # zip, manifest, hashes, references
scenerender-vpkg info   PKG.vpkg.zip
scenerender-vpkg unpack PKG.vpkg.zip DIR --fetch
scenerender-vpkg run    DIR --engine py|rs|c|js [--list] [--only STEP] [--from STEP] [--until STEP] [--force]
```

## Zip layout

```
vpkg.json           manifest; always the first entry
vpkg.schema.json    the JSON Schema it follows
README.md           generated from the manifest
project/            the project tree, relative paths unchanged
  _vpkg/fonts/      font files copied from the packing machine
  _vpkg/fonts.conf  fontconfig file adding _vpkg/fonts (relative dir)
  _external/        files a scene read from outside the project
```

Packing is reproducible:
- `vpkg.json`, `vpkg.schema.json` and `README.md` come first, then entries in sorted order.
- Timestamps are `SOURCE_DATE_EPOCH`, or 1980-01-01 when it is unset.
- Modes are 0644, or 0755 for executable scripts.
- Media files are stored; everything else is deflated.

## `vpkg.json`

The author writes it in the project. `pack` validates it, then adds the sections marked *packed*.

| Key | Content |
|---|---|
| `format`, `formatVersion` | `"scene-video-package"`, `"1.0"` |
| `package` | `id` (slug), `version` (semver), `title`, `description`, `languages`, `license`, `authors`, `created`, `homepage` |
| `scenes[]` | `path`, `role`, `targets` (engine ids), `derivedFrom`, `generatedBy` (step id), `note` |
| `include`, `exclude` | Globs added to or removed from the defaults |
| `fetch[]` | Large inputs left out of the zip: `path`, `url` (https or file), `sha256`, `size`, `license`, `credit`, `note` |
| `requirements` | `tools[]` (`name`, `version`, `features`, `for` step ids, `note`), `python[]` (pip requirement strings), `notes[]` |
| `engines[]` | Tested engines, recorded as evidence rather than requirements: `id`, `impl`, `version`, `commit`, `tested` (`full`, `stills`, `partial` or `untested`), `note` |
| `pipeline` | `steps[]` and `deliverables[]` (`path`, `role`, `step`, `note`) |
| `credits[]` | `paths` (globs), `title`, `author`, `license`, `url`, `note`, `from` (a licence file in the package) |
| `editorial` | Free-form object |
| *packed* `video` | Read from the primary scene: `width`, `height`, `fps`, `duration`, `audio`, `captions`, `outputs[]` |
| *packed* `files[]` | Every file in `project/`: `path`, `size`, `sha256`, `role`, `mediaType`, `referencedBy`, `originalSha256` (on rewritten files), `license`, `credit` |
| *packed* `fonts[]` | `family`, `requested`, `path`, `weight`, `style`, `origin` (`project` or `system`), `copyright`, `license`, `licenseUrl`, `declaredIn` |
| *packed* `external[]`, `rewrites[]` | What `pack` changed, and why |
| *packed* `packed` | Tool, version, `projectDir`, `fileCount`, `totalSize`, and `at` when a date is given |

**Scene roles:**
- `primary`: exactly one.
- `variant`: another cut, or a copy adapted for one engine through `targets` and `derivedFrom`.
- `draft`: unfinished, and its references need not resolve.
- `archive`: kept history.

Drafts and archives are not packed unless `--with-archive` is given, or an `include` glob names them because a pipeline step reads them.

**File roles:** `scene`, `include`, `asset`, `font`, `script`, `schema`, `source`, `doc`, `report`, `preview`, `data`, `config`, `other`.

### Pipeline steps

```json
{"id": "vo", "kind": "audio", "run": ["python3", "make_vo.py"], "cwd": ".", "env": {"SEED": "1"},
 "needs": ["scene"], "inputs": ["make_vo.py"], "outputs": ["assets/vo/*.wav"],
 "requires": ["ffmpeg"], "optional": true, "idempotent": true}
{"id": "render", "kind": "render", "needs": ["vo"],
 "render": {"scene": "scene.xml", "output": "master", "to": "renders/master.mp4",
            "args": {"js": ["--anchor-mode", "position"]}}}
{"id": "stills", "kind": "render", "render": {"scene": "scene.xml", "stills": [5, 37], "to": "frames/{time}.png"}}
```

- **Step kinds** are `generate`, `validate`, `render`, `audio`, `mux` and `command`. Every step except a render step has `run`, an argv list rather than a shell string. When `python` or `python3` is the first word, `run --python` supplies the interpreter.
- **Render steps name a scene, never an engine.** `run --engine ID` picks the engine and uses the scene's variant made for that engine, if there is one.
  - Engines that can write to an arbitrary path get `to`.
  - Others render the scene's `<output>` named by `output`, and the result is copied to `to`.
  - `stills` renders one process per time, and `{time}` and `{frame}` are substituted.
- **Skipping:**
  - A step is skipped when all its `outputs` exist and are newer than its `inputs`; `--force` re-runs it.
  - An `idempotent: false` step runs only when named with `--only`.
  - An `optional` step whose `requires` are missing is skipped, because its outputs are already in the package.

**Default engine commands** can be overridden per engine id in `~/.config/scene-vpkg/engines.json` (`{"py": {"command": [...], "args": [...], "env": {...}}}`) or by `VPKG_ENGINE_<ID>`:

| id | ad-hoc path | by output id | still |
|---|---|---|---|
| `py` | `scenerender render S -o P` | `--output ID` | `scenerender still S -t T -o P` |
| `rs` | `scene-render-rs encode S -o P` | `--output ID` | `render S --time T -o P` |
| `c` | `scene-render-c --scene S --output P` | (document outputs) | `--frame N --output P` |
| `js` | (none) | `scene-render-js render S --output ID` | (none) |

## What `pack` bundles

1. **Scenes and their inputs.** This covers every `primary` and `variant` scene and every file it reads, classified by the scene-render 1.1 XSD:
   - **Inputs:** `src`, `proxy`, `fontFile`, `environment`, `ies`, material maps, `ocioConfig`, transition `shader`, skeleton `weights`, and `cache` attributes when the file exists.
   - **Outputs** (never bundled): `<output>`, `<poster>`, `<thumbnail>` and `<still>` paths, and `<destination>` URIs.
   - **Nested dependencies:**
     - `<include>`d documents
     - glTF and GLB buffers and images
     - OBJ `mtllib` files and MTL maps
     - MaterialX `filename` inputs
     - image-sequence patterns (`%04d`, `####`)
   - An input is bundled even when it sits in an excluded directory.
2. **Other project files**, decided in this order:
   1. The author's `exclude`.
   2. The author's `include`.
   3. The default excludes: `renders/`, `bench/`, `stills/`, `frames/`, `map-frames/`, `__pycache__/`, VCS directories, virtualenvs, `*.log`, `*.err`, `*.zip`, editor files.
   4. The default includes: scripts, `*.xsd`/`*.sch`, docs, `*.json`/`*.csv`/`*.toml`/`*.yaml`, shaders and LUTs, `sources/**`, `fonts/**`.

   Anything else is left out, and `pack -v` lists it. Globs follow gitignore rules: a pattern without `/` matches at any depth, and a leading `/` anchors it at the project root.
3. **Never** the `fetch` files, unless `--bundle-fetch` is given. `pack` checks that the local copy matches the pinned SHA-256.

## Portability rules

Only the engine is left out of a package. `pack` refuses in these cases unless the matching `--allow-*` flag is given:

| Case | What `pack` does |
|---|---|
| A scene input that doesn't exist | Refuses, unless a pipeline step lists it in `outputs` or `fetch` provides it |
| A scene input outside the project | Copies it to `_external/` and rewrites the packaged scene; the source scene is not touched |
| A non-scene file that reaches outside the project | Refuses, because only XML can be rewritten safely |
| A font family named but not declared by `<font>` | Resolves it: project fonts first, then system font directories, reading the sfnt `name` and `OS/2` tables (`DejaVu Sans Bold` means family DejaVu Sans, weight 700) |
| A family that can't be found | Refuses. On another machine it would silently fall back to a different font |
| A script with a machine-specific path (`/home/...`, `/Users/...`, `/src`, `C:\...`, `/usr/share/fonts/...`) | Refuses (lint). A line with `vpkg: allow` is exempt |
| A script path that climbs out of the project (`"../x"`, `join(HERE, "..", ...)`) | Refuses (lint) |
| A literal `/tmp` path | Warning only |
| A remote `http(s)` reference in a scene | Refuses (`--allow-remote` overrides) |

For each resolved font family, `pack` does three things:
- copies the faces into `_vpkg/fonts/`
- declares every face as a `<font>` asset in each packaged scene that uses the family, and also declares the requested name when it carried style words
- writes `_vpkg/fonts.conf`, so engines and scripts that look families up through fontconfig find the same files; `run` sets `FONTCONFIG_FILE` to it

## Reading a package

`verify` checks:
- that the file is a readable zip (a truncated file has no central directory)
- that entry names are safe (no absolute paths, `..`, backslashes or symlinks)
- the manifest against the schema and the semantic rules: one primary scene, unique step ids, known `needs`, no cycles
- every listed file's size and SHA-256, and that nothing unlisted is in the zip

Unless `--shallow` is given, it also extracts the package and re-scans every packaged scene, so that each reference resolves inside the package, to a fetch entry or to a pipeline output.

`unpack` runs the same checks while extracting and gives every file one mtime. `--fetch` then downloads the fetch entries and checks each one's SHA-256.
