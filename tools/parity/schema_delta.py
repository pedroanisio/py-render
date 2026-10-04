#!/usr/bin/env python3
"""Semantic diff of two scene-render schema versions (XSD + Schematron).

    schema_delta.py A.xsd A.sch B.xsd B.sch [--json out.json]

Not a text diff: both XSDs are resolved (named/anonymous complexTypes, groups, attributeGroups,
extensions/restrictions) into a tree of element paths ("scene/layer/effect"), each with a canonical
content model and attribute table, and the trees are compared.  Simple types are compared by their
resolved facets/enumerations.  Schematron asserts/reports are matched by id.
Stdlib + lxml only.
"""
import argparse
import json
import sys
from lxml import etree

XS = "http://www.w3.org/2001/XMLSchema"
SCH = "http://purl.oclc.org/dsdl/schematron"
INF = "unbounded"
FACETS = ["minInclusive", "maxInclusive", "minExclusive", "maxExclusive", "length", "minLength",
          "maxLength", "totalDigits", "fractionDigits", "whiteSpace"]


def q(tag):
    return "{%s}%s" % (XS, tag)


def local(name):
    return name.split(":", 1)[1] if name and ":" in name else name


def occ(node):
    lo = node.get("minOccurs", "1")
    hi = node.get("maxOccurs", "1")
    return int(lo), (INF if hi == INF else int(hi))


def mul(a, b):
    if a == 0 or b == 0:
        return 0
    if a == INF or b == INF:
        return INF
    return a * b


def occ_str(lo, hi):
    return "" if (lo, hi) == (1, 1) else "{%s,%s}" % (lo, hi)


def children(node):
    return [c for c in node if isinstance(c.tag, str) and c.tag.startswith("{%s}" % XS)
            and c.tag != q("annotation")]


class Schema:
    def __init__(self, path):
        self.path = path
        self.root = etree.parse(path).getroot()
        self.g = {k: {} for k in ("element", "complexType", "simpleType", "group", "attributeGroup")}
        for c in self.root:
            if isinstance(c.tag, str) and c.get("name") and etree.QName(c).localname in self.g:
                self.g[etree.QName(c).localname][c.get("name")] = c
        self._simple_cache = {}

    # ---------------------------------------------------------------- simple types
    def simple(self, node, seen=()):
        """Canonical dict of a simpleType node or a type name."""
        if isinstance(node, str):
            n = local(node)
            if node.startswith("xs:") or n not in self.g["simpleType"]:
                return {"builtin": node}
            if n in seen:
                return {"ref": n}
            return self.simple(self.g["simpleType"][n], seen + (n,))
        d = {}
        for c in children(node):
            if c.tag == q("restriction"):
                base = c.get("base")
                d["base"] = base
                inner = [x for x in children(c) if x.tag == q("simpleType")]
                if inner:
                    d["base"] = self.simple(inner[0], seen)
                enum, pats = [], []
                for f in children(c):
                    ln = etree.QName(f).localname
                    if ln == "enumeration":
                        enum.append(f.get("value"))
                    elif ln == "pattern":
                        pats.append(f.get("value"))
                    elif ln in FACETS:
                        d[ln] = f.get("value")
                if enum:
                    d["enumeration"] = enum
                if pats:
                    d["pattern"] = pats
            elif c.tag == q("union"):
                members = [m for m in (c.get("memberTypes") or "").split()]
                d["union"] = [self._member(m, seen) for m in members] + \
                             [self.simple(x, seen) for x in children(c) if x.tag == q("simpleType")]
            elif c.tag == q("list"):
                it = c.get("itemType")
                inner = [x for x in children(c) if x.tag == q("simpleType")]
                d["list"] = self._member(it, seen) if it else (self.simple(inner[0], seen) if inner else None)
        return d

    def _member(self, name, seen):
        n = local(name)
        if name.startswith("xs:") or n not in self.g["simpleType"]:
            return name
        return "type:" + n

    def attr_type(self, a):
        """(label, resolved) for an attribute / element-text type."""
        if a.get("type"):
            t = a.get("type")
            label = t
            return label, self.simple(t)
        inner = [x for x in children(a) if x.tag == q("simpleType")]
        if inner:
            return "(inline)", self.simple(inner[0])
        return "xs:anySimpleType", {"builtin": "xs:anySimpleType"}

    # ---------------------------------------------------------------- attributes
    def attrs_of(self, node, out=None, seen=()):
        """Collect attributes from a complexType/extension/restriction/attributeGroup node."""
        out = {} if out is None else out
        for c in children(node):
            ln = etree.QName(c).localname
            if ln == "attribute":
                name = c.get("name") or c.get("ref")
                label, resolved = self.attr_type(c)
                out[name] = {"type": label, "resolved": resolved, "use": c.get("use", "optional"),
                             "default": c.get("default"), "fixed": c.get("fixed")}
            elif ln == "attributeGroup" and c.get("ref"):
                n = local(c.get("ref"))
                if n in self.g["attributeGroup"] and n not in seen:
                    self.attrs_of(self.g["attributeGroup"][n], out, seen + (n,))
            elif ln in ("complexContent", "simpleContent"):
                self.attrs_of(c, out, seen)
            elif ln in ("extension", "restriction"):
                self.attrs_of(c, out, seen)
            elif ln == "anyAttribute":
                out["*"] = {"type": "anyAttribute", "resolved": {}, "use": "optional",
                            "default": None, "fixed": None}
        return out

    # ---------------------------------------------------------------- content model
    def particles(self, node, seen=()):
        """Model nodes for the content of a complexType/extension/restriction/group."""
        items = []
        for c in children(node):
            ln = etree.QName(c).localname
            if ln in ("sequence", "choice", "all"):
                items.append(self.model(c, seen))
            elif ln == "group" and c.get("ref"):
                items.append(self.model(c, seen))
            elif ln in ("complexContent",):
                items.extend(self.particles(c, seen))
            elif ln == "extension":
                base = local(c.get("base"))
                if base in self.g["complexType"] and base not in seen:
                    items.extend(self.particles(self.g["complexType"][base], seen + (base,)))
                items.extend(self.particles(c, seen))
            elif ln == "restriction":
                items.extend(self.particles(c, seen))
        return items

    def model(self, c, seen=()):
        ln = etree.QName(c).localname
        lo, hi = occ(c)
        if ln == "element":
            return {"k": "el", "name": c.get("name") or c.get("ref"), "min": lo, "max": hi, "node": c}
        if ln == "any":
            return {"k": "el", "name": "*any", "min": lo, "max": hi, "node": None}
        if ln == "group":
            n = local(c.get("ref"))
            if n in seen or n not in self.g["group"]:
                return {"k": "el", "name": "(group-ref %s)" % n, "min": lo, "max": hi, "node": None}
            inner = self.particles(self.g["group"][n], seen + (n,))
            m = inner[0] if len(inner) == 1 else {"k": "seq", "min": 1, "max": 1, "items": inner}
            m = dict(m)
            m["min"], m["max"] = mul(m["min"], lo), mul(m["max"], hi)
            return self.norm(m)
        items = [self.model(x, seen) for x in children(c)
                 if etree.QName(x).localname in ("element", "any", "sequence", "choice", "group", "all")]
        return self.norm({"k": {"sequence": "seq"}.get(ln, ln), "min": lo, "max": hi, "items": items})

    def norm(self, m):
        """Flatten 1..1 sequences nested in sequences and single-item 1..1 wrappers."""
        if "items" not in m:
            return m
        out = []
        for it in m["items"]:
            if it["k"] == "seq" and (it["min"], it["max"]) == (1, 1) and m["k"] == "seq":
                out.extend(it["items"])
            else:
                out.append(it)
        m = dict(m, items=out)
        if len(out) == 1 and m["k"] in ("seq", "all") and (m["min"], m["max"]) == (1, 1):
            return out[0]
        return m

    def type_of_element(self, el):
        """(typeNode|None, simple_label|None, key) for an element declaration."""
        t = el.get("type")
        if t:
            n = local(t)
            if not t.startswith("xs:") and n in self.g["complexType"]:
                return self.g["complexType"][n], None, "ct:" + n
            return None, t, "st:" + t
        for c in children(el):
            if c.tag == q("complexType"):
                return c, None, "anon:%d" % id(c)
            if c.tag == q("simpleType"):
                return None, "(inline)", "st:inline"
        return None, "xs:anyType", "st:anyType"

    def element_info(self, ct, label):
        """Resolved description of an element's type."""
        if ct is None:
            node_simple = None
            return {"text": label, "attrs": {}, "model": None, "mixed": False}
        attrs = self.attrs_of(ct)
        # extension chains: attributes of base types
        self._base_attrs(ct, attrs)
        items = self.particles(ct)
        model = None
        if items:
            model = items[0] if len(items) == 1 else {"k": "seq", "min": 1, "max": 1, "items": items}
            model = self.norm(dict(model))
        text = None
        sc = [c for c in children(ct) if c.tag == q("simpleContent")]
        if sc:
            ext = children(sc[0])[0]
            text = ext.get("base")
        mixed = ct.get("mixed") == "true"
        return {"text": text, "attrs": attrs, "model": model, "mixed": mixed}

    def _base_attrs(self, ct, attrs, seen=()):
        for c in children(ct):
            if etree.QName(c).localname in ("complexContent", "simpleContent"):
                for e in children(c):
                    base = e.get("base")
                    if base:
                        n = local(base)
                        if n in self.g["complexType"] and n not in seen:
                            b = self.g["complexType"][n]
                            base_attrs = self.attrs_of(b)
                            self._base_attrs(b, base_attrs, seen + (n,))
                            for k, v in base_attrs.items():
                                attrs.setdefault(k, v)

    # ---------------------------------------------------------------- tree walk
    def walk(self):
        """Dict path -> record, for every global element and all nested elements."""
        out = {}

        def visit(el, path, stack):
            ct, label, key = self.type_of_element(el)
            info = self.element_info(ct, label)
            rec = {"type": key if key.startswith(("ct:", "st:")) else "(anonymous)",
                   "text": None, "attrs": info["attrs"], "mixed": info["mixed"],
                   "model": ser(info["model"]), "model_sorted": ser(info["model"], True),
                   "child_occ": {}, "children": [], "identity": identity(el)}
            if ct is None:
                rec["text"] = label
                if label == "(inline)":
                    inner = [x for x in children(el) if x.tag == q("simpleType")][0]
                    rec["text"] = "(inline) " + json.dumps(self.simple(inner), sort_keys=True)
            elif info["text"]:
                rec["text"] = info["text"]
            rec["child_occ"] = eff(info["model"])
            out[path] = rec
            if key in stack and not key.startswith("st:"):
                rec["recursive"] = True
                return
            for m in leaves(info["model"]):
                if m["node"] is None:
                    continue
                rec["children"].append(m["name"])
                visit(m["node"], path + "/" + m["name"], stack + (key,))

        for name, el in self.g["element"].items():
            visit(el, name, ())
        return out

    def named_canon(self):
        """Canonical text of named complexTypes, groups, attributeGroups (for 'changed' flags)."""
        res = {}
        for kind in ("complexType", "group", "attributeGroup"):
            for n, node in self.g[kind].items():
                res[(kind, n)] = etree.tostring(strip(node), method="c14n")
        return res


def strip(node):
    import copy
    n = copy.deepcopy(node)
    for a in list(n.iter(q("annotation"))):
        a.getparent().remove(a)
    return n


def identity(el):
    res = {}
    for c in el:
        if isinstance(c.tag, str) and etree.QName(c).localname in ("key", "keyref", "unique"):
            sel = c.find(q("selector"))
            res[c.get("name")] = {
                "kind": etree.QName(c).localname, "refer": c.get("refer"),
                "selector": sel.get("xpath") if sel is not None else None,
                "fields": [f.get("xpath") for f in c.findall(q("field"))]}
    return res


def ser(m, sort=False):
    if m is None:
        return None
    if m["k"] == "el":
        return m["name"] + occ_str(m["min"], m["max"])
    parts = [ser(i, sort) for i in m["items"]]
    if sort and m["k"] in ("choice", "all"):
        parts = sorted(parts)
    return "%s%s(%s)" % (m["k"], occ_str(m["min"], m["max"]), ", ".join(parts))


def leaves(m):
    if m is None:
        return []
    if m["k"] == "el":
        return [m]
    return [x for i in m["items"] for x in leaves(i)]


def eff(m, lo=1, hi=1, out=None):
    """Effective occurrence of each child element name through the model."""
    out = {} if out is None else out
    if m is None:
        return out
    lo2, hi2 = mul(lo, m["min"]), mul(hi, m["max"])
    if m["k"] == "el":
        o = out.get(m["name"])
        if o:
            out[m["name"]] = [o[0] + lo2, INF if INF in (o[1], hi2) else o[1] + hi2]
        else:
            out[m["name"]] = [lo2, hi2]
        return out
    sub_lo = lo2 if m["k"] != "choice" or len(m["items"]) == 1 else 0
    for i in m["items"]:
        eff(i, sub_lo, hi2, out)
    return out


# ------------------------------------------------------------------------- Schematron
def sch_rules(path):
    root = etree.parse(path).getroot()
    ns = {"s": SCH}
    res = {}
    for pat in root.iterfind(".//s:pattern", ns):
        for rule in pat.iterfind("s:rule", ns):
            lets = {l.get("name"): l.get("value") for l in rule.iterfind("s:let", ns)}
            ctx = rule.get("context")
            for a in rule:
                if not isinstance(a.tag, str) or etree.QName(a).localname not in ("assert", "report"):
                    continue
                kind = etree.QName(a).localname
                msg = []
                if a.text:
                    msg.append(a.text)
                for ch in a:
                    if isinstance(ch.tag, str) and etree.QName(ch).localname == "value-of":
                        msg.append("{%s}" % ch.get("select"))
                    else:
                        msg.append("".join(ch.itertext()))
                    if ch.tail:
                        msg.append(ch.tail)
                rid = a.get("id") or "noid:%s|%s" % (ctx, a.get("test"))
                res[(kind, rid)] = {"id": rid, "kind": kind, "pattern": pat.get("id"), "context": ctx,
                                    "test": " ".join((a.get("test") or "").split()),
                                    "message": " ".join("".join(msg).split()), "lets": lets}
    return res


# ------------------------------------------------------------------------- diffing
def diff_simple(a, b):
    """List of change strings between two canonical simple-type dicts."""
    ch = []
    if a == b:
        return ch
    ea, eb = a.get("enumeration"), b.get("enumeration")
    if ea != eb:
        ea, eb = ea or [], eb or []
        add = [x for x in eb if x not in ea]
        rem = [x for x in ea if x not in eb]
        if add:
            ch.append("enumeration added: %s" % add)
        if rem:
            ch.append("enumeration removed: %s" % rem)
        if not add and not rem:
            ch.append("enumeration reordered: %s -> %s" % (ea, eb))
        elif [x for x in ea if x in eb] != [x for x in eb if x in ea]:
            ch.append("enumeration reordered (common values)")
    for k in FACETS + ["pattern", "base", "union", "list", "builtin", "ref"]:
        if a.get(k) != b.get(k) and k in (set(a) | set(b)):
            ch.append("%s: %s -> %s" % (k, json.dumps(a.get(k)), json.dumps(b.get(k))))
    return ch


def diff_xsd(A, B):
    r = {}
    for kind in ("element", "complexType", "simpleType", "group", "attributeGroup"):
        a, b = set(A.g[kind]), set(B.g[kind])
        r["global_%s_added" % kind] = sorted(b - a)
        r["global_%s_removed" % kind] = sorted(a - b)
    # named simple types
    st = {}
    for n in sorted(set(A.g["simpleType"]) & set(B.g["simpleType"])):
        c = diff_simple(A.simple(A.g["simpleType"][n]), B.simple(B.g["simpleType"][n]))
        if c:
            st[n] = c
    r["simpleType_changes"] = st
    ca, cb = A.named_canon(), B.named_canon()
    r["named_structure_changed"] = sorted("%s %s" % k for k in set(ca) & set(cb) if ca[k] != cb[k]
                                          and k[0] != "simpleType")
    # element paths
    pa, pb = A.walk(), B.walk()
    added = sorted(set(pb) - set(pa))
    removed = sorted(set(pa) - set(pb))
    moved = []
    by_name_a, by_name_b = {}, {}
    for p in removed:
        by_name_a.setdefault(p.rsplit("/", 1)[-1], []).append(p)
    for p in added:
        by_name_b.setdefault(p.rsplit("/", 1)[-1], []).append(p)
    moved_from, moved_to = set(), set()
    for n in by_name_a:
        if n in by_name_b:
            for x, y in zip(by_name_a[n], by_name_b[n]):
                moved.append({"from": x, "to": y})
                moved_from.add(x)
                moved_to.add(y)
    r["elements_added"] = [p for p in added if p not in moved_to]
    r["elements_removed"] = [p for p in removed if p not in moved_from]
    r["elements_moved"] = moved
    nm = lambda ps: {p.rsplit("/", 1)[-1] for p in ps}
    r["element_names_added"] = sorted(nm(pb) - nm(pa))
    r["element_names_removed"] = sorted(nm(pa) - nm(pb))
    ad_set = set(r["elements_added"])
    r["elements_added_subtree_roots"] = [p for p in r["elements_added"] if p.rsplit("/", 1)[0] not in ad_set]
    content, attrs, text, ident = {}, {}, {}, {}
    for p in sorted(set(pa) & set(pb)):
        x, y = pa[p], pb[p]
        if x["model"] != y["model"]:
            c = {"old": x["model"], "new": y["model"]}
            if x["model_sorted"] == y["model_sorted"]:
                c["note"] = "only the order of choice/all members changed"
            oa, ob = x["child_occ"], y["child_occ"]
            c["children_added"] = sorted(set(ob) - set(oa))
            c["children_removed"] = sorted(set(oa) - set(ob))
            c["occurrence_changes"] = {k: {"old": oa[k], "new": ob[k]} for k in sorted(set(oa) & set(ob))
                                       if oa[k] != ob[k]}
            ka = [k for k in x["children"] if k in y["children"]]
            kb = [k for k in y["children"] if k in x["children"]]
            if ka != kb:
                c["child_order_changed"] = {"old": ka, "new": kb}
            content[p] = c
        if x["mixed"] != y["mixed"]:
            content.setdefault(p, {})["mixed"] = {"old": x["mixed"], "new": y["mixed"]}
        if x["text"] != y["text"]:
            text[p] = {"old": x["text"], "new": y["text"]}
        aa, ab = x["attrs"], y["attrs"]
        d = {"added": {}, "removed": {}, "changed": {}}
        for n in sorted(set(ab) - set(aa)):
            d["added"][n] = {k: ab[n][k] for k in ("type", "use", "default", "fixed")}
        for n in sorted(set(aa) - set(ab)):
            d["removed"][n] = {k: aa[n][k] for k in ("type", "use", "default", "fixed")}
        for n in sorted(set(aa) & set(ab)):
            one, two = aa[n], ab[n]
            c = {}
            for k in ("type", "use", "default", "fixed"):
                if one[k] != two[k]:
                    c[k] = {"old": one[k], "new": two[k]}
            if one["type"] == two["type"] == "(inline)" or one["type"] != two["type"]:
                s = diff_simple(one["resolved"], two["resolved"])
                if s:
                    c["simple_type"] = s
            if c:
                d["changed"][n] = c
        if d["added"] or d["removed"] or d["changed"]:
            attrs[p] = d
        if x["identity"] != y["identity"]:
            ia, ib = x["identity"], y["identity"]
            ident[p] = {"added": sorted(set(ib) - set(ia)), "removed": sorted(set(ia) - set(ib)),
                        "changed": sorted(k for k in set(ia) & set(ib) if ia[k] != ib[k])}
    for p in r["elements_added"]:
        if pb[p]["identity"]:
            ident[p] = {"added": sorted(pb[p]["identity"]), "removed": [], "changed": []}
    r["content_model_changes"] = content
    r["attribute_changes"] = attrs
    r["element_text_type_changes"] = text
    r["identity_constraint_changes"] = ident
    r["counts_paths"] = {"A": len(pa), "B": len(pb)}
    return r


def diff_sch(A, B):
    a, b = sch_rules(A), sch_rules(B)
    r = {"added": [], "removed": [], "changed": {}, "counts": {"A": len(a), "B": len(b)}}
    for k in sorted(set(b) - set(a), key=natkey):
        r["added"].append({"id": k[1], "kind": k[0], "context": b[k]["context"], "test": b[k]["test"],
                           "message": b[k]["message"]})
    for k in sorted(set(a) - set(b), key=natkey):
        r["removed"].append({"id": k[1], "kind": k[0], "context": a[k]["context"]})
    for k in sorted(set(a) & set(b), key=natkey):
        c = {}
        for f in ("context", "test", "message", "lets", "pattern"):
            if a[k][f] != b[k][f]:
                c[f] = {"old": a[k][f], "new": b[k][f]}
        if c:
            r["changed"][k[1]] = c
    return r


def natkey(k):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", k[1])]


# ------------------------------------------------------------------------- text report
def render(r, args):
    L = []
    w = L.append
    x, s = r["xsd"], r["sch"]
    w("SCHEMA DELTA")
    w("  A: %s + %s" % (args[0], args[1]))
    w("  B: %s + %s" % (args[2], args[3]))
    w("")
    w("== XSD: global declarations ==")
    for kind in ("element", "complexType", "simpleType", "group", "attributeGroup"):
        ad, rm = x["global_%s_added" % kind], x["global_%s_removed" % kind]
        if ad or rm:
            w("  %s: +%d -%d" % (kind, len(ad), len(rm)))
            for n in ad:
                w("    + %s" % n)
            for n in rm:
                w("    - %s" % n)
    if x["named_structure_changed"]:
        w("  named complexType/group/attributeGroup with changed structure (%d):"
          % len(x["named_structure_changed"]))
        for n in x["named_structure_changed"]:
            w("    ~ %s" % n)
    w("")
    w("== XSD: element paths (A=%d, B=%d) ==" % (x["counts_paths"]["A"], x["counts_paths"]["B"]))
    w("  new element names (%d): %s" % (len(x["element_names_added"]), ", ".join(x["element_names_added"])))
    w("  gone element names (%d): %s" % (len(x["element_names_removed"]), ", ".join(x["element_names_removed"])))
    w("  added paths: %d total (the same group/type is reachable by many paths); %d subtree roots:"
      % (len(x["elements_added"]), len(x["elements_added_subtree_roots"])))
    for p in x["elements_added_subtree_roots"]:
        w("    + %s" % p)
    w("  removed (%d):" % len(x["elements_removed"]))
    for p in x["elements_removed"]:
        w("    - %s" % p)
    w("  moved (%d):" % len(x["elements_moved"]))
    for m in x["elements_moved"]:
        w("    %s -> %s" % (m["from"], m["to"]))
    w("")
    w("== XSD: content model changes (%d) ==" % len(x["content_model_changes"]))
    groups = {}
    for p, c in x["content_model_changes"].items():
        groups.setdefault(json.dumps(c, sort_keys=True), []).append(p)
    w("  (%d distinct changes; identical changes on several paths are shown once)" % len(groups))
    for p, c in ((v[0], x["content_model_changes"][v[0]]) for v in groups.values()):
        more = len(groups[json.dumps(c, sort_keys=True)]) - 1
        w("  %s%s" % (p, "  (+%d more paths, same change)" % more if more else ""))
        if "old" in c:
            if "note" in c:
                w("    (%s)" % c["note"])
            for k in ("children_added", "children_removed"):
                if c[k]:
                    w("    %s: %s" % (k, ", ".join(c[k])))
            for k, v in c["occurrence_changes"].items():
                w("    occurs %s: %s -> %s" % (k, v["old"], v["new"]))
            if "child_order_changed" in c:
                w("    child order changed")
            w("    old: %s" % trunc(c["old"]))
            w("    new: %s" % trunc(c["new"]))
        if "mixed" in c:
            w("    mixed: %s" % c["mixed"])
    w("")
    w("== XSD: attribute changes (%d elements) ==" % len(x["attribute_changes"]))
    na = nr = nc = 0  # counted per distinct change below; summary has per-path totals
    agroups = {}
    for p, d in x["attribute_changes"].items():
        agroups.setdefault(json.dumps(d, sort_keys=True), []).append(p)
    for k, ps in agroups.items():
        p, d = ps[0], x["attribute_changes"][ps[0]]
        w("  %s%s" % (p, "  (+%d more paths, same change)" % (len(ps) - 1) if len(ps) > 1 else ""))
        for n, v in d["added"].items():
            na += 1
            w("    + @%s : %s use=%s%s%s" % (n, v["type"], v["use"],
                                           " default=%s" % v["default"] if v["default"] is not None else "",
                                           " fixed=%s" % v["fixed"] if v["fixed"] is not None else ""))
        for n, v in d["removed"].items():
            nr += 1
            w("    - @%s : %s use=%s" % (n, v["type"], v["use"]))
        for n, v in d["changed"].items():
            nc += 1
            bits = ["%s: %s -> %s" % (k, v[k]["old"], v[k]["new"]) for k in ("type", "use", "default", "fixed")
                    if k in v] + v.get("simple_type", [])
            w("    ~ @%s : %s" % (n, "; ".join(bits)))
    w("")
    w("== XSD: named simpleType changes (%d) ==" % len(x["simpleType_changes"]))
    for n, c in x["simpleType_changes"].items():
        w("  %s" % n)
        for i in c:
            w("    %s" % i)
    w("")
    w("== XSD: element text type changes (%d) ==" % len(x["element_text_type_changes"]))
    for p, c in x["element_text_type_changes"].items():
        w("  %s: %s -> %s" % (p, c["old"], c["new"]))
    w("")
    w("== XSD: identity constraints (%d elements) ==" % len(x["identity_constraint_changes"]))
    for p, c in x["identity_constraint_changes"].items():
        w("  %s: +%s -%s ~%s" % (p, c["added"], c["removed"], c["changed"]))
    w("")
    w("== Schematron (A=%d rules, B=%d) ==" % (s["counts"]["A"], s["counts"]["B"]))
    w("  added (%d):" % len(s["added"]))
    for a in s["added"]:
        w("    + %s [%s] ctx=%s" % (a["id"], a["kind"], a["context"]))
    w("  removed (%d):" % len(s["removed"]))
    for a in s["removed"]:
        w("    - %s [%s] ctx=%s" % (a["id"], a["kind"], a["context"]))
    w("  changed (%d):" % len(s["changed"]))
    for i, c in s["changed"].items():
        w("    ~ %s" % i)
        for f, v in c.items():
            w("        %s:" % f)
            w("          old: %s" % trunc(v["old"], 300))
            w("          new: %s" % trunc(v["new"], 300))
    w("")
    w("== SUMMARY ==")
    for k, v in r["summary"].items():
        w("  %-34s %s" % (k, v))
    return "\n".join(L) + "\n"


def trunc(v, n=400):
    v = v if isinstance(v, str) else json.dumps(v)
    return v if len(v) <= n else v[:n] + "...(%d more chars)" % (len(v) - n)


def summary(x, s):
    return {
        "elements_added_paths": len(x["elements_added"]),
        "elements_added_subtree_roots": len(x["elements_added_subtree_roots"]),
        "element_names_added": len(x["element_names_added"]),
        "element_names_removed": len(x["element_names_removed"]), "elements_removed_paths": len(x["elements_removed"]),
        "elements_moved": len(x["elements_moved"]),
        "content_model_changes": len(x["content_model_changes"]),
        "elements_with_attribute_changes": len(x["attribute_changes"]),
        "attributes_added": sum(len(d["added"]) for d in x["attribute_changes"].values()),
        "attributes_removed": sum(len(d["removed"]) for d in x["attribute_changes"].values()),
        "attributes_changed": sum(len(d["changed"]) for d in x["attribute_changes"].values()),
        "simpleTypes_added": len(x["global_simpleType_added"]),
        "simpleTypes_removed": len(x["global_simpleType_removed"]),
        "simpleTypes_changed": len(x["simpleType_changes"]),
        "element_text_type_changes": len(x["element_text_type_changes"]),
        "identity_constraint_changes": len(x["identity_constraint_changes"]),
        "global_other_added": sum(len(x["global_%s_added" % k]) for k in
                                  ("element", "complexType", "group", "attributeGroup")),
        "global_other_removed": sum(len(x["global_%s_removed" % k]) for k in
                                    ("element", "complexType", "group", "attributeGroup")),
        "sch_added": len(s["added"]), "sch_removed": len(s["removed"]), "sch_changed": len(s["changed"]),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("axsd")
    ap.add_argument("asch")
    ap.add_argument("bxsd")
    ap.add_argument("bsch")
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args(argv)
    x = diff_xsd(Schema(a.axsd), Schema(a.bxsd))
    s = diff_sch(a.asch, a.bsch)
    r = {"a": [a.axsd, a.asch], "b": [a.bxsd, a.bsch], "xsd": x, "sch": s, "summary": summary(x, s)}
    sys.stdout.write(render(r, [a.axsd, a.asch, a.bxsd, a.bsch]))
    if a.json_out:
        with open(a.json_out, "w") as fh:
            json.dump(r, fh, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
