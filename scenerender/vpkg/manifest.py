"""vpkg.json: loading and validation.

The JSON Schema in schema/vpkg-1.0.schema.json is the single definition of the format; `schema_errors`
interprets the subset of JSON Schema it uses (type, const, enum, required, properties, additionalProperties,
items, minItems, minLength, minimum, pattern, $ref) so validation needs only the standard library.
`semantic_errors` adds the rules a schema cannot express: exactly one primary scene, unique step ids,
known `needs`, a command or a render block per step, an acyclic pipeline.
"""
from __future__ import annotations

import json
import os
import re

FORMAT = "scene-video-package"
FORMAT_VERSION = "1.0"
MANIFEST = "vpkg.json"
SCHEMA_NAME = "vpkg.schema.json"
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema", "vpkg-1.0.schema.json")


class ManifestError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("invalid vpkg.json:\n  " + "\n  ".join(self.errors))


def load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "number": (int, float), "integer": int}


def schema_errors(value, schema: dict | None = None, root: dict | None = None, where: str = "$") -> list:
    root = root or schema or load_schema()
    schema = root if schema is None else schema
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return schema_errors(value, node, root, where)
    errors = []
    t = schema.get("type")
    if t:
        ok = isinstance(value, _TYPES[t]) and not (t in ("number", "integer") and isinstance(value, bool))
        if not ok:
            return [f"{where}: expected {t}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: too short")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{where}: below {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{where}: needs at least {schema['minItems']} item(s)")
        if "items" in schema:
            for i, item in enumerate(value):
                errors += schema_errors(item, schema["items"], root, f"{where}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: missing {key!r}")
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                errors += schema_errors(item, props[key], root, f"{where}.{key}")
            elif extra is False:
                errors.append(f"{where}: unknown key {key!r}")
            elif isinstance(extra, dict):
                errors += schema_errors(item, extra, root, f"{where}.{key}")
    return errors


def semantic_errors(m: dict) -> list:
    errors = []
    scenes = m.get("scenes", [])
    primaries = [s for s in scenes if s.get("role") == "primary"]
    if len(primaries) != 1:
        errors.append(f"$.scenes: exactly one scene must have role 'primary' ({len(primaries)} found)")
    paths = [s.get("path") for s in scenes]
    if len(set(paths)) != len(paths):
        errors.append("$.scenes: duplicate path")
    steps = m.get("pipeline", {}).get("steps", [])
    ids = [s.get("id") for s in steps]
    if len(set(ids)) != len(ids):
        errors.append("$.pipeline.steps: duplicate step id")
    for i, s in enumerate(steps):
        where = f"$.pipeline.steps[{i}] ({s.get('id')})"
        for need in s.get("needs", []):
            if need not in ids:
                errors.append(f"{where}: needs unknown step {need!r}")
        if s.get("kind") == "render":
            if "render" not in s:
                errors.append(f"{where}: a render step needs a 'render' block")
            elif s["render"]["scene"] not in paths:
                errors.append(f"{where}: render scene {s['render']['scene']!r} is not listed in scenes")
        elif "run" not in s:
            errors.append(f"{where}: needs 'run'")
    try:
        order(steps)
    except ValueError as e:
        errors.append(f"$.pipeline.steps: {e}")
    fetch = [f.get("path") for f in m.get("fetch", [])]
    if len(set(fetch)) != len(fetch):
        errors.append("$.fetch: duplicate path")
    return errors


def order(steps: list) -> list:
    """Steps in dependency order, keeping the written order where free; ValueError on a cycle."""
    by_id, done, out = {s["id"]: s for s in steps}, set(), []

    def visit(s, stack=()):
        if s["id"] in done:
            return
        if s["id"] in stack:
            raise ValueError("cycle through " + " -> ".join(stack + (s["id"],)))
        for need in s.get("needs", []):
            if need in by_id:
                visit(by_id[need], stack + (s["id"],))
        done.add(s["id"])
        out.append(s)
    for s in steps:
        visit(s)
    return out


def validate(m: dict) -> list:
    return schema_errors(m) + semantic_errors(m)


def load(path: str, check: bool = True) -> dict:
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    if check:
        errors = validate(m)
        if errors:
            raise ManifestError(errors)
    return m


def primary(m: dict) -> dict:
    return next(s for s in m["scenes"] if s["role"] == "primary")


def scene_for(m: dict, scene: str, engine: str | None) -> str:
    """The variant of `scene` made for an engine (targets + derivedFrom), else `scene` itself."""
    if engine:
        for s in m["scenes"]:
            if s.get("role") == "variant" and engine in s.get("targets", []) and s.get("derivedFrom") == scene:
                return s["path"]
    return scene


def dump(m: dict) -> str:
    return json.dumps(m, indent=2, ensure_ascii=False) + "\n"
