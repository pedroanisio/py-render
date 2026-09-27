"""font/@collectionIndex selects one face of a .ttc/.otc collection."""
from __future__ import annotations

import glob

import numpy as np
import pytest

from scenerender import document, pango_bridge
from scenerender.assets import text as T
from scenerender.compositor import RenderContext
from scenerender.evaluator import Ctx, Evaluator
from scenerender.nodes import core as _core  # noqa: F401

TTCS = sorted(glob.glob("/usr/share/fonts/**/NotoSansCJK-Regular.ttc", recursive=True)
              + glob.glob("/usr/share/fonts/**/NotoSerifCJK-Regular.ttc", recursive=True))
pytestmark = pytest.mark.skipif(not TTCS, reason="no CJK .ttc collection installed")

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<scene version="1.1">
  <project width="400" height="120" fps="30" duration="1" seed="1"/>
  <assets>
    <font id="f" src="{src}" family="{family}" {extra}/>
    <text id="t" width="400" height="120" size="64" fontAsset="f" text="骨直次"/>
  </assets>
  <composition/>
</scene>
"""


def _render(tmp_path, index, family, extra=""):
    p = tmp_path / f"t{index}.xml"
    attrs = extra + (f' collectionIndex="{index}"' if index is not None else "")
    p.write_text(HEAD.format(src=TTCS[0], family=family, extra=attrs), encoding="utf-8")
    doc = document.load(str(p))
    assert not doc.validation_errors, doc.validation_errors[:3]
    rc = RenderContext(doc, Evaluator(doc))
    ctx = Ctx(t=0.0, comp_t=0.0)
    b = T.get_block(rc, T.resolve_spec(rc, doc.ids["t"], ctx))
    fam = T.families(rc, {"fontAsset": "f"})[0]
    return T.render_block(rc, b, rc.root_matrix, ctx).px[..., 3], fam


def test_face_info_lists_collection_faces():
    f0, f1 = pango_bridge.face_info(TTCS[0], 0), pango_bridge.face_info(TTCS[0], 1)
    assert f0["count"] > 1 and f0["family"] != f1["family"]
    assert f0["weight"] == 400 and f0["slant"] == "normal"
    assert pango_bridge.face_info(TTCS[0], f0["count"]) is None


def test_collection_index_selects_face(tmp_path, caplog):
    f0, f1 = (pango_bridge.face_info(TTCS[0], i) for i in (0, 1))
    a0, fam0 = _render(tmp_path, None, f0["family"])
    a1, fam1 = _render(tmp_path, 1, f0["family"])   # @family names face 0: the face's own family wins
    assert "is not a family of that face" in caplog.text
    assert (fam0, fam1) == (f0["family"], f1["family"])
    assert a0.shape == a1.shape
    assert (np.abs(a0 - a1) > 0.5).sum() > 100        # different glyph designs (JP vs KR Han forms)
    a1b, _ = _render(tmp_path, 1, f1["family"])       # a matching @family renders the same face
    assert np.array_equal(a1, a1b)


def test_resolve_font_face_static_face_wins():
    info = pango_bridge.face_info(TTCS[0], 1)
    assert pango_bridge.resolve_font_face(TTCS[0], 1, "Nope", 700, "italic") == (info["family"], 400, "normal")
    assert pango_bridge.resolve_font_face(TTCS[0], 99, "Nope", 700, "italic") == ("Nope", 700, "italic")
