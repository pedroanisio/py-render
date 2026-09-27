"""Schema-driven attribute typing.

Reads scene-render-1.1.xsd and answers, for any element of a document: which
complexType it has (resolved through its ancestor path, since names such as
"effect" or "key" are reused with different types), and for each attribute its
simple type and default. The evaluator uses this so defaults and value parsing
come from the schema, not from hand-copied tables.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from lxml import etree

XS = "{http://www.w3.org/2001/XMLSchema}"
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema", "scene-render-1.1.xsd")


@dataclass
class AttrInfo:
    name: str
    type: str            # simpleType name ("colorType", "xs:double", ...) or "enum" for inline enumerations
    default: str | None
    required: bool
    enum: tuple[str, ...] = ()


@dataclass
class TypeInfo:
    name: str
    attrs: dict[str, AttrInfo] = field(default_factory=dict)
    children: dict[str, str] = field(default_factory=dict)   # child element name -> type name
    simple_content: str | None = None



class Schema:
    def __init__(self, path: str = SCHEMA_PATH):
        self.path = path
        self.tree = etree.parse(path)
        root = self.tree.getroot()
        self._complex = {e.get("name"): e for e in root.iter(XS + "complexType") if e.get("name")}
        self._simple = {e.get("name"): e for e in root.iter(XS + "simpleType") if e.get("name")}
        self._groups = {e.get("name"): e for e in root.findall(XS + "group")}
        self._agroups = {e.get("name"): e for e in root.findall(XS + "attributeGroup")}
        self.types: dict[str, TypeInfo] = {}
        self.root_type = self._anon_root(root)
        self._validator: etree.XMLSchema | None = None
        self._semantic_validator = None

    # ------------------------------------------------------------ validation
    def validator(self) -> etree.XMLSchema:
        if self._validator is None:
            self._validator = etree.XMLSchema(self.tree)
        return self._validator

    def semantic_errors(self, tree) -> list[str]:
        """Cross-field rules kept beside the XSD and shipped in the package."""
        path = os.path.splitext(self.path)[0] + ".sch"
        if not os.path.isfile(path):
            return []  # Custom schemas need not supply the scene-render rules.
        if self._semantic_validator is None:
            from lxml.isoschematron import Schematron
            self._semantic_validator = Schematron(etree.parse(path), store_report=True)
        validator = self._semantic_validator
        if validator.validate(tree):
            return []
        ns = {"svrl": "http://purl.oclc.org/dsdl/svrl"}
        return [f"{failure.get('location')}: {failure.get('id')}: "
                + " ".join(failure.xpath("svrl:text/text()", namespaces=ns))
                for failure in validator.validation_report.xpath("//svrl:failed-assert", namespaces=ns)]

    # ------------------------------------------------------------ type table
    def _anon_root(self, root) -> str:
        scene = root.find(XS + "element[@name='scene']")
        ct = scene.find(XS + "complexType")
        self._complex["#scene"] = ct
        return "#scene"

    def type(self, name: str) -> TypeInfo:
        if name not in self.types:
            info = TypeInfo(name)
            self.types[name] = info
            node = self._complex.get(name)
            if node is not None:
                self._collect(node, info)
        return self.types[name]

    def _collect(self, node, info: TypeInfo) -> None:
        for c in node:
            tag = c.tag
            if tag == XS + "attribute":
                self._attr(c, info)
            elif tag == XS + "attributeGroup" and c.get("ref"):
                self._collect(self._agroups[c.get("ref")], info)
            elif tag == XS + "group" and c.get("ref"):
                self._collect(self._groups[c.get("ref")], info)
            elif tag == XS + "element":
                name = c.get("name") or c.get("ref")
                t = c.get("type")
                if t is None:
                    anon = c.find(XS + "complexType")
                    if anon is not None:
                        t = f"#{info.name}/{name}"
                        self._complex[t] = anon
                    else:
                        t = "xs:string"
                info.children.setdefault(name, t)
            elif tag in (XS + "complexContent", XS + "simpleContent"):
                for d in c:
                    if d.tag in (XS + "extension", XS + "restriction"):
                        base = d.get("base")
                        if base in self._complex:
                            base_info = self.type(base)
                            info.attrs.update({k: v for k, v in base_info.attrs.items() if k not in info.attrs})
                            info.children.update({k: v for k, v in base_info.children.items() if k not in info.children})
                            info.simple_content = info.simple_content or base_info.simple_content
                        elif tag == XS + "simpleContent":
                            info.simple_content = base
                        self._collect(d, info)
            elif tag in (XS + "sequence", XS + "choice", XS + "all"):
                self._collect(c, info)

    def _attr(self, a, info: TypeInfo) -> None:
        name = a.get("name")
        if name is None:
            return
        t = a.get("type")
        enum: tuple[str, ...] = ()
        if t is None:
            st = a.find(XS + "simpleType")
            enum = tuple(e.get("value") for e in st.iter(XS + "enumeration")) if st is not None else ()
            base = st.find(XS + "restriction") if st is not None else None
            t = "enum" if enum else (base.get("base") if base is not None else "xs:string")
        elif t in self._simple:
            enum = tuple(e.get("value") for e in self._simple[t].iter(XS + "enumeration"))
        info.attrs[name] = AttrInfo(name, t, a.get("default"), a.get("use") == "required", enum)

    # ------------------------------------------------------------ element lookup
    def type_of_path(self, path: tuple[str, ...]) -> TypeInfo | None:
        return self._type_of_path(path)

    @lru_cache(maxsize=4096)
    def _type_of_path(self, path: tuple[str, ...]) -> TypeInfo | None:
        if not path or path[0] != "scene":
            return None
        cur = self.type(self.root_type)
        for name in path[1:]:
            t = cur.children.get(name)
            if t is None:
                return None
            cur = self.type(t)
        return cur

    def base_kind(self, type_name: str) -> str:
        """Collapse a simple type to a parsing kind: number, integer, bool, color, paint, length, string, ..."""
        seen = set()
        t = type_name
        while t not in seen:
            seen.add(t)
            if t in _BUILTIN_KIND:
                return _BUILTIN_KIND[t]
            if t in _NAMED_KIND:
                return _NAMED_KIND[t]
            st = self._simple.get(t)
            if st is None:
                return "string"
            r = st.find(XS + "restriction")
            if r is not None and r.get("base"):
                t = r.get("base")
                continue
            if st.find(XS + "list") is not None:
                return "list"
            return "string"
        return "string"

    def whitespace(self, type_name: str) -> str:
        """Effective XSD whitespace facet (preserve for string-derived types)."""
        if type_name in ("xs:string", "enum", "xs:anySimpleType"):
            return "preserve"
        if type_name == "xs:normalizedString":
            return "replace"
        if type_name.startswith("xs:"):
            return "collapse"
        node = self._simple.get(type_name)
        if node is None:
            return "preserve"
        if node.find(XS + "union") is not None or node.find(XS + "list") is not None:
            return "collapse"
        restriction = node.find(XS + "restriction")
        if restriction is not None:
            facet = restriction.find(XS + "whiteSpace")
            return facet.get("value") if facet is not None else self.whitespace(restriction.get("base", "xs:string"))
        return "preserve"


_BUILTIN_KIND = {
    "xs:double": "number", "xs:decimal": "number", "xs:float": "number",
    "xs:integer": "integer", "xs:int": "integer", "xs:positiveInteger": "integer",
    "xs:nonNegativeInteger": "integer", "xs:unsignedLong": "integer", "xs:unsignedInt": "integer",
    "xs:boolean": "bool", "enum": "string", "xs:string": "string", "xs:ID": "string",
    "xs:IDREF": "string", "xs:IDREFS": "string", "xs:NCName": "string", "xs:NMTOKEN": "string",
    "xs:NMTOKENS": "string", "xs:anyURI": "string", "xs:dateTime": "string",
}
_NAMED_KIND = {
    "colorType": "color", "paintType": "paint", "lengthType": "length",
    "positiveLengthType": "length", "pointType": "point", "numberListType": "list",
    "fpsType": "fps",
}


@lru_cache(maxsize=1)
def default_schema() -> Schema:
    return Schema()
