#!/usr/bin/env python3
"""Runs the Rust implementation's conformance corpus through the Python loader and compares the
outcome with the oracle's manifest.

    python tools/parity/validation_corpus.py [--corpus DIR] [--json OUT.json] [--md OUT.md]
                                             [--jobs N] [--only SUBSTR] [--show DOC ...]

For every document it records
  * the verdict of the real entry point (document.load, strict, the default), and
  * the codes the Python validator yields, in the Rust code space (`scene-render explain CODE`):
      - XSD errors mapped to S01-S12 (libxml2 error class -> structural code),
      - Schematron failures, which already carry the rule id (C38, R2, V5, ...),
    and keeps whatever it cannot map in `unmapped` so nothing is silently dropped. The loader's own warnings are
    recorded (`warnings`) but not turned into A01-A07: Python does not check assets at load time, and a
    warning about an unrelated file (a font it cannot open) is not evidence about the one Rust checks.
It compares verdict and code set with manifest.json and writes a table and a JSON report.
Python validation behaviour is only measured here, never changed.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import json
import logging
import multiprocessing
import os
import re
import signal
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = Path(os.environ.get("SR_CORPUS", "/home/admin/src/rs-scene-render/tests/corpus"))
TIMEOUT = 120                                   # seconds per document

# Rust codes that are warnings by themselves: a document that only raises these is still valid.
WARNING_CODES = {"W01", "A03", "A04", "A06", "A07"}


# ---------------------------------------------------------------------------- mapping
def family(code: str) -> str:
    """R24-fill -> R24: the Rust corpus names one assert per attribute, Schematron rules may be shared."""
    return code.split("-", 1)[0]


def xsd_code(type_name: str, message: str) -> str | None:
    """libxml2 schema error -> the Rust structural code (S01-S12); None when it fits none."""
    t, m = type_name, message
    if t == "SCHEMAV_CVC_ELT_1" or "No matching global declaration" in m:
        if "{" in m or "namespace" in m.lower():
            return "S11"
        return "S01"
    if "Missing child element" in m:
        return "S03"
    if t in ("SCHEMAV_ELEMENT_CONTENT", "SCHEMAV_CVC_ELT_4_1", "SCHEMAV_CVC_COMPLEX_TYPE_2_4_A",
             "SCHEMAV_CVC_COMPLEX_TYPE_2_4_B", "SCHEMAV_CVC_COMPLEX_TYPE_2_4_C", "SCHEMAV_CVC_COMPLEX_TYPE_2_4_D"):
        if "{" in m and "namespace" in m.lower():
            return "S11"
        return "S02"
    if t in ("SCHEMAV_CVC_COMPLEX_TYPE_3_2_1", "SCHEMAV_CVC_COMPLEX_TYPE_3_2_2"):
        return "S11" if "{" in m else "S04"
    if t == "SCHEMAV_CVC_COMPLEX_TYPE_4":
        return "S05"
    if t == "SCHEMAV_CVC_COMPLEX_TYPE_2_3":
        return "S07"
    if "Duplicate" in m and ("ID" in m or "key-sequence" in m):
        return "S09"
    if "IDREF" in m and re.search(r"no|not|unknown", m, re.I) and "valid value" not in m:
        return "S10"
    dup = re.search(r"'([^']*)' is not a valid value of the atomic type 'xs:ID'", m)
    if dup and re.fullmatch(r"[A-Za-z_][\w.\-]*", dup.group(1)):
        return "S09"                              # well-formed NCName, so the problem is that it is taken
    if t.startswith("SCHEMAV_CVC_DATATYPE") or t.startswith("SCHEMAV_CVC_FACET") or t in (
            "SCHEMAV_CVC_ENUMERATION_VALID", "SCHEMAV_CVC_PATTERN_VALID", "SCHEMAV_CVC_MININCLUSIVE_VALID",
            "SCHEMAV_CVC_MAXINCLUSIVE_VALID", "SCHEMAV_CVC_MINEXCLUSIVE_VALID", "SCHEMAV_CVC_MAXEXCLUSIVE_VALID",
            "SCHEMAV_CVC_LENGTH_VALID", "SCHEMAV_CVC_MINLENGTH_VALID", "SCHEMAV_CVC_MAXLENGTH_VALID",
            "SCHEMAV_CVC_TYPE_3_1_3"):
        return "S06" if re.search(r"attribute '", m) else "S08"
    return None


def message_code(message: str) -> str | None:
    """Schematron failures are formatted '<location>: <id>: <text>'."""
    m = re.match(r"^\S+: ([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)?): ", message)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------- worker
class _Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise _Timeout()


def check(path: str) -> dict:
    """Runs one document through Python validation; always returns a plain dict."""
    os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="parity-cache-"))
    if str(REPO) not in sys.path:                      # the checkout being measured, not an installed copy
        sys.path.insert(0, str(REPO))
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(TIMEOUT)
    out: dict = {"path": path, "xml_error": None, "xsd": [], "sch": [], "load": None,
                 "warnings": [], "codes": [], "unmapped": [], "seconds": 0.0}
    t0 = time.perf_counter()
    try:
        from lxml import etree
        from scenerender import document
        from scenerender.schema import Schema, default_schema

        records: list[logging.LogRecord] = []

        class _H(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = _H(level=logging.WARNING)
        logging.getLogger().addHandler(h)
        try:
            parser = etree.XMLParser(remove_comments=True, remove_pis=True, huge_tree=True)
            try:
                tree = etree.parse(path, parser)
            except etree.XMLSyntaxError as e:
                out["xml_error"] = str(e)
                out["codes"] = ["XML"]
                out["verdict"] = "invalid"
                return out
            alt = os.environ.get("SR_PARITY_SCHEMA")           # what-if: another scene-render-1.1.xsd (+ .sch)
            schema = Schema(alt) if alt else default_schema()
            v = schema.validator()
            if not v.validate(tree):
                for e in v.error_log:
                    out["xsd"].append({"line": e.line, "type": e.type_name, "message": e.message,
                                       "code": xsd_code(e.type_name, e.message)})
            document._normalize_attributes(document.Document(      # same preparation load() applies
                path=path, tree=tree, schema=schema, base=os.path.dirname(os.path.abspath(path)),
                strict=True, include_stack=()))
            for msg in schema.semantic_errors(tree):
                out["sch"].append({"message": msg, "code": message_code(msg)})
            # The real entry point: what a user of the library or the CLI gets.
            try:
                document.load(path, strict=True, schema=schema)
                out["load"] = {"ok": True}
            except document.SceneError as e:
                out["load"] = {"ok": False, "kind": "SceneError", "message": str(e)[:600]}
            except _Timeout:
                raise
            except Exception as e:  # noqa: BLE001 - a crash is a result, not a harness failure
                out["load"] = {"ok": False, "kind": type(e).__name__, "message": str(e)[:600]}
        finally:
            logging.getLogger().removeHandler(h)
        out["warnings"] = [r.getMessage()[:300] for r in records]
    except _Timeout:
        out["load"] = {"ok": False, "kind": "Timeout", "message": f"> {TIMEOUT}s"}
    except Exception as e:  # noqa: BLE001
        out["load"] = {"ok": False, "kind": "HarnessError:" + type(e).__name__, "message": str(e)[:600]}
    finally:
        signal.alarm(0)
        out["seconds"] = round(time.perf_counter() - t0, 2)

    codes: list[str] = []
    for e in out["xsd"]:
        if e["code"]:
            codes.append(e["code"])
        else:
            out["unmapped"].append(f"xsd {e['type']}: {e['message']}"[:200])
    for e in out["sch"]:
        if e["code"]:
            codes.append(e["code"])
        else:
            out["unmapped"].append("sch " + e["message"][:200])
    out["codes"] = sorted(set(codes))
    load = out["load"] or {}
    out["verdict"] = "valid" if load.get("ok") else "invalid"
    if not load.get("ok") and load.get("kind") not in ("SceneError",) and not out["xsd"] and not out["sch"]:
        out["unmapped"].append(f"load {load.get('kind')}: {load.get('message')}"[:200])
    return out


# ---------------------------------------------------------------------------- comparison
def expectations(corpus: Path) -> list[dict]:
    m = json.loads((corpus / "manifest.json").read_text())
    rows = []
    for section, verdict in (("valid", "valid"), ("warnings", "valid"), ("invalid", "invalid")):
        for name, codes in sorted(m.get(section, {}).items()):
            sub = "invalid" if section == "invalid" else "valid"
            p = corpus / sub / name
            if not p.exists():                                  # warnings live in valid/ or invalid/
                alt = corpus / ("valid" if sub == "invalid" else "invalid") / name
                p = alt if alt.exists() else p
            rows.append({"name": name, "section": section, "path": str(p), "verdict": verdict,
                         "codes": sorted(codes)})
    return rows


def compare(exp: dict, got: dict, baseline: set[str] = frozenset()) -> dict:
    """`baseline`: codes Python already reports for the unmutated kitchen sink (it lacks schema 1.2/1.3), which
    every mutation inherits; they are not evidence about the mutated rule."""
    ec, gc = set(exp["codes"]), set(got["codes"])
    err_expected = {c for c in ec if c not in WARNING_CODES or exp["verdict"] == "invalid"}
    efam, gfam = {family(c) for c in ec}, {family(c) for c in gc}
    if exp["verdict"] == "valid":
        cls = "ok" if got["verdict"] == "valid" else "false-reject"
    else:
        cls = "ok" if got["verdict"] == "invalid" else "false-accept"
    if ec == gc:
        match = "exact"
    elif efam == gfam:
        match = "family"
    elif ec and ec <= gc:
        match = "superset"
    elif ec & gc or efam & gfam:
        match = "partial"
    elif not ec and gc:
        match = "extra"
    else:
        match = "none"
    novel = gc - ec - set(baseline)
    complete = ec <= gc or all(family(c) in gfam for c in ec)
    return {"verdict_class": cls, "code_match": match, "missing": sorted(ec - gc),
            "extra": sorted(gc - ec), "novel_extra": sorted(novel), "err_expected": sorted(err_expected),
            "all_expected_found": complete, "clean": complete and not novel}


def run(corpus: Path, jobs: int, only: str | None) -> list[dict]:
    exps = [e for e in expectations(corpus) if not only or only in e["name"]]
    ctx = multiprocessing.get_context("spawn")
    results: dict[str, dict] = {}
    with cf.ProcessPoolExecutor(max_workers=jobs, mp_context=ctx) as pool:
        futs = {pool.submit(check, e["path"]): e for e in exps}
        for f in cf.as_completed(futs):
            e = futs[f]
            try:
                results[e["path"]] = f.result()
            except Exception as ex:  # noqa: BLE001 - the worker died (segfault, OOM)
                results[e["path"]] = {"path": e["path"], "codes": [], "verdict": "invalid", "xsd": [], "sch": [],
                                      "warnings": [], "unmapped": [f"worker died: {ex}"],
                                      "load": {"ok": False, "kind": "WorkerDied", "message": str(ex)}, "seconds": 0}
    base = next((set(r["codes"]) for p, r in results.items() if p.endswith("valid/kitchen-sink.scene.xml")), set())
    rows = []
    for e in exps:
        g = results[e["path"]]
        rows.append({**e, "python": g, **compare(e, g, base)})
    for r in rows:
        r["baseline_codes"] = sorted(base)
    return rows


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    verdict = collections.Counter((r["verdict"], r["python"]["verdict"]) for r in rows)
    agree = sum(1 for r in rows if r["verdict_class"] == "ok")
    inv = [r for r in rows if r["verdict"] == "invalid"]
    match = collections.Counter(r["code_match"] for r in rows)
    per_code: dict[str, dict] = {}
    for r in rows:
        for c in r["codes"]:
            d = per_code.setdefault(c, {"expected": 0, "detected": 0, "family_detected": 0, "instead": collections.Counter()})
            d["expected"] += 1
            if c in r["python"]["codes"]:
                d["detected"] += 1
            if family(c) in {family(x) for x in r["python"]["codes"]}:
                d["family_detected"] += 1
            if c not in r["python"]["codes"]:
                for x in r["python"]["codes"] or ["(none)"]:
                    d["instead"][x] += 1
    false_pos = collections.Counter(c for r in rows for c in r["novel_extra"])
    with_codes = [r for r in rows if r["codes"]]
    exp_total = sum(len(r["codes"]) for r in with_codes)
    exp_found = sum(len(r["codes"]) - len(r["missing"]) for r in with_codes)
    return {
        "baseline_codes": rows[0].get("baseline_codes", []) if rows else [],
        "expected_code_recall_pct": round(100.0 * exp_found / exp_total, 1) if exp_total else 0,
        "docs_all_expected_found": sum(1 for r in with_codes if r["all_expected_found"]),
        "docs_with_expected_codes": len(with_codes),
        "docs_clean": sum(1 for r in with_codes if r["clean"]),
        "documents": n,
        "verdict_agreement": round(100.0 * agree / n, 1) if n else 0,
        "verdict_matrix": {f"expected {a} / python {b}": k for (a, b), k in sorted(verdict.items())},
        "invalid_documents": len(inv),
        "invalid_rejected": sum(1 for r in inv if r["python"]["verdict"] == "invalid"),
        "code_match": dict(match),
        "code_exact_pct": round(100.0 * match["exact"] / n, 1) if n else 0,
        "code_exact_or_family_pct": round(100.0 * (match["exact"] + match["family"]) / n, 1) if n else 0,
        "codes_expected": len(per_code),
        "codes_ever_detected": sum(1 for d in per_code.values() if d["detected"]),
        "per_code": {c: {"expected": d["expected"], "detected": d["detected"],
                         "family_detected": d["family_detected"],
                         "reported_instead": dict(d["instead"].most_common(4))}
                     for c, d in sorted(per_code.items(), key=lambda kv: (re.sub(r"\d+.*", "", kv[0]), int(re.search(r"\d+", kv[0]).group()) if re.search(r"\d+", kv[0]) else 0, kv[0]))},
        "false_positive_codes": dict(false_pos.most_common()),
        "crashes": [r["name"] for r in rows if (r["python"].get("load") or {}).get("kind") not in (None, "SceneError")],
    }


def markdown(summary: dict, rows: list[dict]) -> str:
    s = summary
    out = ["# Validation corpus: Python vs the Rust oracle" + (f" (schema: {os.environ['SR_PARITY_SCHEMA']})" if os.environ.get("SR_PARITY_SCHEMA") else ""), "",
           f"Documents: **{s['documents']}** ({s['invalid_documents']} invalid, the rest valid or warning-only).", "",
           "| measure | value |", "|---|---|",
           f"| verdict agreement (valid/invalid) | **{s['verdict_agreement']} %** |",
           f"| invalid documents rejected by Python | {s['invalid_rejected']} / {s['invalid_documents']} |",
           f"| code set identical to the manifest | **{s['code_exact_pct']} %** |",
           f"| identical, counting rule families as one (R24-fill = R24) | {s['code_exact_or_family_pct']} % |",
           f"| manifest codes ever reported by Python | {s['codes_ever_detected']} / {s['codes_expected']} |",
           f"| expected codes found (recall over all document x code pairs) | **{s['expected_code_recall_pct']} %** |",
           f"| documents where every expected code was found | {s['docs_all_expected_found']} / {s['docs_with_expected_codes']} |",
           f"| ... and nothing new reported beyond the kitchen-sink baseline | {s['docs_clean']} / {s['docs_with_expected_codes']} |", "",
           "Baseline = codes Python reports for the unmutated `valid/kitchen-sink.scene.xml` (it lacks schema 1.2/1.3): "
           + (", ".join(s["baseline_codes"]) or "none") + ".", "",
           "Verdict matrix: " + "; ".join(f"{k}: {v}" for k, v in s["verdict_matrix"].items()), "",
           "Code-set outcome per document: " + ", ".join(f"{k} {v}" for k, v in sorted(s["code_match"].items())), "",
           "## Per code", "", "| code | expected in | detected | same family | reported instead |", "|---|---|---|---|---|"]
    for c, d in s["per_code"].items():
        inst = ", ".join(f"{k}×{v}" for k, v in d["reported_instead"].items()) if d["detected"] < d["expected"] else ""
        out.append(f"| {c} | {d['expected']} | {d['detected']} | {d['family_detected']} | {inst} |")
    if s["false_positive_codes"]:
        out += ["", "## Codes Python reports that the manifest does not expect (beyond the baseline)", "",
                ", ".join(f"{k}×{v}" for k, v in s["false_positive_codes"].items())]
    miss = [r for r in rows if r["verdict_class"] != "ok"]
    if miss:
        out += ["", "## Documents with the wrong verdict", "", "| document | expected | python | python reported |", "|---|---|---|---|"]
        for r in miss:
            rep = ", ".join(r["python"]["codes"]) or (r["python"].get("load") or {}).get("message", "")[:80]
            out.append(f"| {r['name']} | {r['verdict']} {' '.join(r['codes'])} | {r['python']['verdict']} | {rep} |")
    if s["crashes"]:
        out += ["", "## Loader crashes (not a SceneError)", "", ", ".join(s["crashes"])]
    return "\n".join(out) + "\n"


def _compact(row: dict) -> dict:
    """The row without the bulky raw diagnostics: counts plus the first few messages."""
    g = dict(row["python"])
    for k in ("xsd", "sch", "warnings"):
        items = g.get(k) or []
        g[k + "_count"] = len(items)
        g[k] = items[:3]
    g["unmapped"] = (g.get("unmapped") or [])[:3]
    return {**row, "python": g}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--json", type=Path, help="write the full report here")
    ap.add_argument("--md", type=Path, help="write the table here (default: print)")
    ap.add_argument("--full", action="store_true", help="keep every raw Python diagnostic in the JSON (large)")
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 2))
    ap.add_argument("--only", help="only documents whose name contains this")
    ap.add_argument("--schema", type=Path, help="what-if: validate with this scene-render-1.1.xsd (and the .sch beside it)"
                    " instead of the packaged one, e.g. the Rust repo's schema/")
    ap.add_argument("--show", nargs="*", help="print the raw Python diagnostics of these documents and exit")
    a = ap.parse_args()
    if a.schema:
        os.environ["SR_PARITY_SCHEMA"] = str(a.schema.resolve())    # inherited by the spawned workers
    if a.show:
        for name in a.show:
            p = next((q for sub in ("invalid", "valid") for q in [a.corpus / sub / name] if q.exists()), None)
            if p is None:
                print(name, "not found")
                continue
            r = check(str(p))
            print(json.dumps(r, indent=1, default=str))
        return 0
    rows = run(a.corpus, a.jobs, a.only)
    summary = summarise(rows)
    md = markdown(summary, rows)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        docs = rows if a.full else [_compact(r) for r in rows]
        a.json.write_text(json.dumps({"summary": summary, "documents": docs}, indent=1, default=str))
    if a.md:
        a.md.parent.mkdir(parents=True, exist_ok=True)
        a.md.write_text(md)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    sys.exit(main())
