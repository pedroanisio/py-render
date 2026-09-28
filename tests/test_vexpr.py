"""vexpr evaluates the expressions instanced objects use for every index at once, with the values the
interpreter gives per index; anything outside its subset falls back to the interpreter."""
from __future__ import annotations

import numpy as np
import pytest

from scenerender import document, expr, vexpr
from scenerender.camera import world3d
from scenerender.compositor import RenderContext
from scenerender.evaluator import Ctx, Evaluator
from scenerender.registry import load_plugins
from scenerender.three import scene as three_scene

load_plugins()

ENV = {"time": 3.25, "frame": 97.0, "count": 8.0, "seed": 1234.0, "value": 1.5}

SAME = [
    "index / (count - 1)", "1 / (index - 2)", "(index - 2) / 0", "0 / (index * 0)", "index % 3", "-index % 2.5",
    "index % 0", "index ** 0.5", "(-index) ** 0.5", "0 ** -index", "index > 2 && index < 5 ? 10 : -1",
    "index > 2 && 7", "index || 9", "!index", "index == 3", "true + index", "value * index + seed",
    "Math.sin(index) + Math.max(index, 2, NaN)", "Math.round(index / 2) + Math.floor(-index / 3) + Math.sign(index - 3)",
    "var a = time * 2; a + index", "a = 3; b = a * index; b", "[1, 2, -3, 4, 5, 6, 7, 8][index] * time",
    "t0 = [0.5, 0.25, 1, 2, 3, 4, 5, 6][index]; a = time - t0; (a > 0 && a < 0.6 ? 1 - a * 1.4 : 0)",
    "Math.PI * index", "index > 1 ? index > 3 : 5", "Math.min()", "Math.atan2(index, 2) ** 2",
]


@pytest.mark.parametrize("src", SAME)
def test_matches_the_interpreter_per_index(src):
    n = int(ENV["count"])
    got = vexpr.compile(src)({**ENV, "index": np.arange(n, dtype=np.float64)}, n)
    assert got is not None
    for i in range(n):
        v = expr.compile_expr(src)({**expr.builtin_functions(1234, 3.25, value=1.5), **ENV, "index": float(i)})
        v = float(v)
        assert v == got[i] or (v != v and got[i] != got[i]) or abs(v - got[i]) <= 1e-12 * max(1.0, abs(v))


@pytest.mark.parametrize("src", ["wiggle(1, 2)", "'a' + index", "[index, 1][0]", "Math.random()", "index & 1",
                                 "value.x", "x", "[1, 2][index]"])
def test_falls_back_outside_its_subset(src):
    fn = vexpr.compile(src)
    assert fn is None or fn({**ENV, "index": np.arange(8, dtype=np.float64)}, 8) is None


def test_instance_matrices_equal_per_copy_world3d(tmp_path):
    p = tmp_path / "i.xml"
    p.write_text('''<scene version="1.1"><project width="320" height="180" fps="24" duration="2"/>
      <composition>
        <object3D id="rig" primitive="sphere" radius="4" x="100" y="80" z="30" rotationY="20"/>
        <object3D id="o" primitive="box" width="10" height="10" depth="10" instances="12" parent="rig" rotationX="15">
          <expression property="x">t0 = [0,1,2,3,4,5,6,7,8,9,10,11][index] * 0.1; (time - t0) * 40 + index * 3</expression>
          <expression property="y">Math.sin(index + time) * 20</expression>
          <expression property="rotation">index * 30</expression>
          <expression property="scaleX">index % 2 == 0 ? 1.5 : 0.5</expression>
        </object3D>
      </composition></scene>''')
    doc = document.load(str(p))
    rc = RenderContext(doc, Evaluator(doc))
    el = doc.ids["o"]
    for t in (0.0, 0.4, 1.3):
        ctx = rc.enter_node(el, Ctx(t=t, comp_t=t))
        fast = three_scene._instance_worlds(rc, el, ctx, 12)
        assert fast is not None
        slow = np.stack([world3d(rc, el, ctx.with_vars(index=float(i), count=12.0)) for i in range(12)])
        np.testing.assert_array_equal(fast, slow)
