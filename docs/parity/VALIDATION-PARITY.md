# Validation parity: Python vs the Rust implementation

Measured with `tools/parity/validation_corpus.py` against the Rust conformance corpus
(`rs-scene-render/tests/corpus`: 245 documents, 198 distinct expected codes, oracle = lxml XSD +
ISO Schematron over the Rust schema). Nothing in Python's validation was changed to produce these
numbers. Companion documents: [SCHEMA-DELTA.md](SCHEMA-DELTA.md), [CLI.md](CLI.md),
[validation-corpus.packaged.md](validation-corpus.packaged.md) and
[validation-corpus.rust-schema.md](validation-corpus.rust-schema.md) (full per-code tables and JSON).

## 1. Result

| | Python as shipped | Python with the Rust schema files dropped in |
|---|---|---|
| verdict agreement (valid / invalid) | **90.2 %** | **97.6 %** |
| invalid documents rejected | 219 / 221 | 215 / 221 |
| code set identical to the manifest | 3.7 % | **95.5 %** |
| expected codes found (document x code pairs) | 49.6 % | **95.2 %** |
| manifest codes ever reported | 95 / 199 | 193 / 199 |
| valid documents accepted | 2 / 24 | 24 / 24 |

"As shipped" fails mostly because it validates against the original 1.1 schema: 22 of the 24 valid or
warning-only corpus documents (every one that uses 1.2/1.3 constructs) are rejected, and every mutation document inherits the same
baseline errors (C20, C33, R14, S02, S04, S06). Its high "invalid rejected" figure is therefore not
evidence of correct checking: only 49.6 % of the expected codes are actually found.

Replacing `schema/scene-render-1.1.{xsd,sch}` with the Rust pair (`--schema`) closes almost all of
it with no other change: **235 of 245 documents end up with exactly the expected codes**. The
remaining 11 documents (6 distinct codes) are exactly the checks the Rust implementation performs
natively, outside XSD and Schematron:

| code | what | documents | why lxml misses it |
|---|---|---|---|
| A01 | referenced input file does not exist | 1 | Python checks no assets at load time (and logs nothing for a missing file) |
| A02 | SHA-256 differs from `sha256` / `cacheSha256` | 2 | same (digests are checked only for includes and `<data>`) |
| A03 | remote input cannot be verified offline (warning) | 1 | same |
| A04 | image-sequence frames missing (error when `missingFrame="error"`) | 3 | same |
| W01 | numeric attribute is INF / NaN / out of double range (warning, also inside lengths and number lists) | 3 | xs:double accepts them, so the XSD passes |
| S10 | IDREF / IDREFS names no id (and empty IDREFS, S06) | 2 | libxml2 does not resolve IDREFs |

Codes in the Rust catalogue that this corpus does **not** exercise: `XML`, S08, S11, S12, A05, A06,
A07, P01-P05, M01, and the 18 runtime ("eval") codes. Their parity is unmeasured; test documents
have to be written for them (the Rust `codes.rs` gives each one's rule).

Full Python test suite with the Rust schema swapped in (uncommitted experiment): **3554 passed,
40 skipped, 1 failed**. The one failure, `tests/test_particle_evaluation.py::
test_animated_display_properties_match_static_controls[sprite-first-second]`, builds an emitter with
an animated `sprite` but no static `@sprite`, which the new rule C50 correctly rejects: a fixture
fix, not a behaviour change. Validation cost rises with the rule count (87 -> 185 asserts): mean
0.02 s -> 0.07 s per corpus document, worst case 0.17 s; the existing result cache absorbs repeats.

## 2. What the corpus does not measure

* Mapping of libxml2 XSD errors to S01-S12 is a heuristic in the harness (`xsd_code`): S09 is inferred
  from "not a valid value of xs:ID" on a well-formed name, S11/S12 are untested. Rust derives the
  S-codes from its own structural validator, so exact-agreement on S-codes after adoption needs the
  mapper promoted to a library function with its own tests (step 2 below).
* Line numbers, columns, messages and ordering of diagnostics are not compared, only codes and the
  verdict.
* Rendering: a schema-valid 1.2/1.3 document that Python accepts is not thereby rendered correctly.
  The 24 valid corpus documents load, but `scenerender` ignores constructs it does not implement
  (maps, 3D rigid bodies, segments, volumes, pyro, ocean...). See SCHEMA-DELTA.md for the list by
  module.

## 3. Proposed approach: adopt the Rust schema and code space

Order chosen so every step is independently shippable and measurable with the harness.

**Step 1: vendor the schema (small, high yield).**
Replace `schema/scene-render-1.1.{xsd,sch}` (symlinked into `scenerender/schema/`) with the Rust
pair (rs-1.3: sr-core 1.1.3 plus local SREPs 9/10/11/13 and the 1.3 cinematic proposal), with a
`PROVENANCE`/`UPSTREAM` record carrying the sha256s and the sr-core tag, as the Rust repo does.
Expected effect: verdict 90.2 -> 97.6 %, code-set 3.7 -> 95.5 %. What breaks:
1. One test fixture (C50 above). Nothing else in the suite.
2. Behavioural: previously Python refused documents using 1.1.3+ constructs (strict) and now
   accepts them; the renderer silently drops what it does not implement. To keep "valid" from
   meaning "supported", have the render path warn once per unimplemented element/attribute (the
   registry behind `scenerender coverage` already knows) and make `--strict`-style tooling able to
   fail on it. Decide whether 1.2/1.3 are vendored at once or the 1.1.3 pair first; they are
   additive, so the Python code does not care, but the 1.3 file is a local proposal not yet accepted
   upstream (`srep-0000-cinematic-impact.md`), so pinning rs-1.1.3 first and rs-1.3 behind a
   switch is the conservative route.
3. `schema.py` builds type/default tables from the XSD for the evaluator: newly declared attributes
   gain defaults with no consumer; harmless, but the table build time and the typed-attribute
   lookups should be re-timed.
4. Schematron runtime x3.5 (see above); the cache key already includes the rule digest.

**Step 2: a diagnostics layer with the Rust code space.**
New module (e.g. `scenerender/diagnostics.py`): `Diagnostic(code, severity, message, line, column, path)`,
produced by (a) the XSD error mapper from the harness (promoted, tested per S-code), (b) the
Schematron `failed-assert/@id` read from the SVRL report instead of parsed out of the message text,
with `location` -> line via the document, and (c) the native checks of step 3. `SceneError` keeps
its current message text for existing callers and gains `.diagnostics`. Add `explain CODE` (the
catalogue is the sch assert ids plus the fixed table in Rust `codes.rs`; the sch carries each
assert's message, context and test), and `validate --format json`.

**Step 3: native checks lxml cannot do** (in the order the corpus justifies):
1. S10 and empty IDREFS (S06): resolve `IDREF`/`IDREFS` against the id set (2 docs).
2. W01 non-finite / overflowing numerics, including lengths and number lists (3 docs), as a warning.
3. A01, A02, A03, A04 asset checks (6 docs): file existence relative to the base dir, SHA-256 of
   `sha256`/`cacheSha256`, offline classification of remote URIs, sequence-frame completeness honouring
   `missingFrame`, plus A05-A07 (unreadable file, absent physics cache, declared vs actual image
   size). `--no-assets` and `--base-dir` come with it (CLI.md section 2.1).
4. S11 foreign namespaces, S12 nesting > 256, P01-P05 (soft-body stiffness, absurd counts, symbol
   cycles, expression bracket depth 62, matte cycles): no corpus coverage today, so write the
   documents first (from the Rust descriptions) and add them to the harness.

**Step 4: CLI and gate.**
`validate FILE... --format human|json --no-assets --base-dir --deny-warnings -q`, `explain`, and
exit codes as Rust. Add a test that runs the harness against a vendored copy (or a path from
`SR_CORPUS`, skipped when absent) and fails below the thresholds reached at each step. The corpus is
2.5 MB (245 documents, 31 media files) and generated by `tools/build_corpus.py`; vendoring a copy
with its manifest is the simplest way to get a CI gate (decision for the maintainers: copy, or
submodule).

**Not part of this plan:** implementing the 1.1.3/1.2/1.3 features themselves (maps, rigid bodies,
output segments, volumes, pyro, ocean, fracture...). Validation parity makes those documents
*admissible*; rendering parity is the other workstream.

## 4. Reproduce

```
python tools/parity/validation_corpus.py --md out.md --json out.json          # as shipped
python tools/parity/validation_corpus.py --schema ~/src/rs-scene-render/schema/scene-render-1.1.xsd ...   # what-if
python tools/parity/validation_corpus.py --show c38.scene.xml                 # raw Python diagnostics of one document
SR_CORPUS=/path/to/tests/corpus python tools/parity/validation_corpus.py     # another corpus location
```
