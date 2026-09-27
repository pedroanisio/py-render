"""Schema-valid lexical forms must produce the same behavior and delivery settings."""
from argparse import Namespace

import numpy as np
import pytest

from scenerender.coverage import report
from scenerender.evaluator import Ctx
from scenerender.render import Renderer
from scenerender.values import parse_bool


def write_scene(tmp_path, text, name="scene.xml"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


@pytest.mark.parametrize("on,off", [("true", "false"), ("1", "0"), (" 1 ", " 0 ")])
def test_boolean_forms_render_and_evaluate_equally(tmp_path, on, off):
    def render(yes, no, name):
        path = write_scene(tmp_path, f'''<scene version="1.1">
          <project width="64" height="32" fps="10" duration="1" linearLight="{no}"
                   motionBlur="{yes}" adaptiveMotionBlur="{no}" motionBlurSamples="4"/>
          <parameters><param id="flag" type="boolean" default="1"/></parameters>
          <composition>
            <shape id="hidden" shape="rect" width="64" height="32" visible="{no}" fill="#FF0000"/>
            <shape id="s" shape="rect" width="10" height="20" x="3">
              <animate property="x"><key time="0" value="10"/><key time="1" value="40"/></animate>
              <animate property="x" additive="{yes}"><key time="0" value="2"/></animate>
              <expression property="x" enabled="{no}">999</expression>
              <expression property="y">param("flag") ? 5 : 500</expression>
              <mask type="rect" width="5" height="20" invert="{yes}"/>
            </shape>
          </composition></scene>''', name)
        return Renderer.open(path, strict=True)
    r, ref = render(on, off, "test.xml"), render("true", "false", "ref.xml")
    assert r.rc.motion_blur and not r.rc.linear
    ctx = Ctx(t=0.5, comp_t=0.5)
    assert r.rc.ev.num(r.doc.ids["s"], "x", ctx) == 27
    assert r.rc.ev.num(r.doc.ids["s"], "y", ctx) == 5
    actual = r.frame_rgb(0.5)
    assert actual.max() > 0
    np.testing.assert_array_equal(actual, ref.frame_rgb(0.5))


def test_required_numeric_boolean_parameter(tmp_path):
    from scenerender.document import SceneError, load
    path = write_scene(tmp_path, '''<scene version="1.1">
      <project width="16" height="16" fps="1" duration="1"/>
      <parameters><param id="name" type="string" required="1" default="hello"/></parameters>
      <composition/></scene>''')
    with pytest.raises(SceneError, match="required"):
        load(path, strict=True, params={"name": ""})


def test_numeric_output_flags(tmp_path):
    from scenerender.output import jobs_from_args
    path = write_scene(tmp_path, '''<scene version="1.1">
      <project width="16" height="16" fps="1" duration="1"/>
      <output path="test.mov" codec="prores" alpha="1" audio="0"/>
      <composition/></scene>''')
    r = Renderer.open(path, strict=True)
    args = Namespace(scene=path, param=[], output=None, out=None, layout=None, variant=None,
                     t0=None, t1=None, fps=None, crf=None, scale=1)
    jobs = jobs_from_args(r, args)
    assert len(jobs) == 1 and jobs[0].alpha and not jobs[0].audio


def test_coverage_reports_animation_and_scope(tmp_path):
    path = write_scene(tmp_path, '''<scene version="1.1">
      <project width="16" height="16" fps="1" duration="1" motionBlur="1"/>
      <composition><shape id="s" shape="rect" width="5" height="5">
        <expression property="x" enabled="0">20</expression>
        <animate property="y"><key time="0" value="1"/></animate>
      </shape></composition></scene>''')
    text = report(path, all_features=True)
    assert "motionBlur" in text and "expression" in text and "animate" in text
    assert "Registry coverage" in text and "not verified" in text


def test_boolean_parser_accepts_native_values_and_defaults():
    assert parse_bool(True) and parse_bool(1) and parse_bool(None, True)
    assert not parse_bool(False) and not parse_bool(0) and not parse_bool(None)


def test_coverage_sees_node_motion_blur_and_stabilization(tmp_path):
    path = write_scene(tmp_path, '''<scene version="1.1">
      <project width="16" height="16" fps="1" duration="1"/>
      <assets><image id="im" src="image.png" width="16" height="16"/></assets>
      <composition><layer id="l" asset="im" motionBlur="on" stabilize="1"/></composition>
      </scene>''')
    text = report(path, all_features=True)
    assert "(valid)" in text and "motionBlur" in text and "layer:stabilize" in text
