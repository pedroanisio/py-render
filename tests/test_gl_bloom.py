"""effects.gl_bloom reproduces the NumPy bloom (effects.light._bloom) pass for pass."""
from __future__ import annotations

import numpy as np
import pytest

from scenerender import document, gl
from scenerender.compositor import RenderContext
from scenerender.effects import light
from scenerender.evaluator import Ctx, Evaluator
from scenerender.raster import Buf

pytestmark = pytest.mark.skipif(not gl.available(), reason="no OpenGL context")


@pytest.mark.parametrize("radius", [2, 3.5, 12, 30])
def test_gl_bloom_matches_numpy(tmp_path, monkeypatch, radius):
    p = tmp_path / "b.xml"
    p.write_text(f'''<scene version="1.1"><project width="96" height="64" fps="24" duration="1"/>
      <composition/><effects><effect id="b" type="bloom" radius="{radius}" intensity="0.8" threshold="0.6"
      color="#FFE0C0C0"/></effects></scene>''')
    doc = document.load(str(p))
    rc = RenderContext(doc, Evaluator(doc))
    rng = np.random.default_rng(3)
    px = (rng.random((40, 56, 4)) * 1.6).astype(np.float32)
    px[..., 3] = np.clip(rng.random((40, 56)) * 1.3 - 0.15, 0, 1)
    px[::4, ::7, 3] = 0
    px[..., :3] *= px[..., 3:]
    e, ctx = doc.ids["b"], Ctx(t=0.0, comp_t=0.0)
    monkeypatch.setenv("SCENERENDER_GPU", "1")
    got = light.bloom(rc, e, Buf(px.copy(), 5, 7), ctx, None)
    monkeypatch.setenv("SCENERENDER_GPU", "0")
    ref = light.bloom(rc, e, Buf(px.copy(), 5, 7), ctx, None)
    assert (got.x0, got.y0, got.px.shape) == (ref.x0, ref.y0, ref.px.shape)
    np.testing.assert_allclose(got.px, ref.px, rtol=1e-5, atol=2e-6)
