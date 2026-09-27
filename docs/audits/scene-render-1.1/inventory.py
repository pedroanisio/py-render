"""Rebuild the declaration-level inventory; source hits are review leads, not proof.

Run from any directory. Output is beside this script. The complete XSD declaration
tree preserves anonymous types, facets, defaults and particle cardinalities. The
resolved type graph separately shows inheritance/group expansion used at runtime.
"""
from collections import deque
from dataclasses import asdict
import ast
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from scenerender.schema import Schema, XS


def declaration(el):
    return {
        "kind": el.tag.removeprefix(XS), "line": el.sourceline,
        "attributes": dict(el.attrib),
        **({"text": " ".join("".join(el.itertext()).split())}
           if el.tag == XS + "documentation" else {}),
        "children": [declaration(c) for c in el if isinstance(c.tag, str)],
    }


def source_reads(names):
    hits = {name: [] for name in sorted(names)}
    for path in sorted((REPO / "scenerender").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            method = call.func.attr
            index = 0 if method == "get" else 1
            if method not in ("get", "str", "num", "bool", "length", "color", "base"):
                continue
            # Evaluator.get has the property in its second argument; XML/dict.get
            # has it in the first. Both are explicitly only static review leads.
            if method == "get" and len(call.args) > 1 and not isinstance(call.args[0], ast.Constant):
                index = 1
            if len(call.args) <= index or not isinstance(call.args[index], ast.Constant):
                continue
            name = call.args[index].value
            if isinstance(name, str) and name in hits:
                hits[name].append({"file": str(path.relative_to(REPO)), "line": call.lineno,
                                   "method": method, "expression": ast.unparse(call)})
    return hits


def build():
    schema = Schema(str(REPO / "schema/scene-render-1.1.xsd"))
    root = schema.tree.getroot()
    pending = deque([(schema.root_type, ("scene",))])
    resolved = {}
    while pending:
        name, path = pending.popleft()
        if name in resolved:
            continue
        info = schema.type(name)
        node = schema._complex.get(name)
        resolved[name] = {
            "schema_line": node.sourceline if node is not None else None,
            "representative_path": "/".join(path),
            "simple_content": info.simple_content,
            "attributes": {k: {**asdict(v), "parsing_kind": schema.base_kind(v.type),
                                "whitespace": schema.whitespace(v.type)}
                           for k, v in sorted(info.attrs.items())},
            "children": info.children,
        }
        for child, typ in info.children.items():
            pending.append((typ, (*path, child)))
    attrs = {a.get("name") for a in root.iter(XS + "attribute") if a.get("name")}
    counts = {kind: len(list(root.iter(XS + kind))) for kind in
              ("element", "attribute", "complexType", "simpleType", "group", "attributeGroup")}
    return {
        "schema": "schema/scene-render-1.1.xsd",
        "sha256": hashlib.sha256(Path(schema.path).read_bytes()).hexdigest(),
        "counts": {**counts, "distinct_attribute_names": len(attrs),
                   "reachable_runtime_types": len(resolved)},
        "evidence_limit": "This is a complete declaration inventory and static source index, not a conformance score. "
                          "Source reads can refer to dictionaries or unrelated element types; dynamic consumers may "
                          "not have a literal source hit. Every behavior still needs review and regression evidence.",
        "declarations": declaration(root),
        "resolved_types": resolved,
        "source_read_candidates_by_attribute": source_reads(attrs),
    }


if __name__ == "__main__":
    result = build()
    dest = Path(__file__).with_name("inventory.json")
    dest.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(dest.relative_to(REPO)), **result["counts"]}, indent=2))
