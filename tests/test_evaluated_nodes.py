"""End-to-end checks for evaluated node properties and independent subtree clocks."""
import numpy as np
import pytest
from PIL import Image

from scenerender.document import load
from scenerender.render import Renderer


def scene(tmp_path, body, before="", name="scene.xml", after=""):
    path = tmp_path / name
    path.write_text(f'''<scene version="1.1"><project width="100" height="100" fps="10"
       duration="6" linearLight="false"/>{before}<composition>{body}</composition>{after}</scene>''')
    return str(path)


def test_visibility_z_and_shape_change_rendered_pixels(tmp_path):
    r = Renderer.open(scene(tmp_path, '''<shape id="red" shape="rect" width="40" height="40" fill="#ff0000">
       <animate property="z"><key time="0" value="0"/><key time="1" value="2"/></animate>
       <animate property="shape"><key time="0" value="ellipse"/></animate>
       <animate property="visible"><key time="0" value="true"/><key time="2" value="false"/></animate></shape>
       <shape id="blue" shape="rect" width="40" height="40" fill="#0000ff" z="1"/>'''), strict=True)
    np.testing.assert_array_equal(r.frame_rgb(0)[20, 20], [0, 0, 255])
    px = r.frame_rgb(1.5)
    np.testing.assert_array_equal(px[20, 20], [255, 0, 0])
    np.testing.assert_array_equal(px[0, 0], [0, 0, 255])
    np.testing.assert_array_equal(r.frame_rgb(2.5)[20, 20], [0, 0, 255])


def test_instance_overrides_apply_to_visibility_and_shape(tmp_path):
    symbols = '''<symbols><symbol id="sym" width="40" height="40" duration="5">
      <shape id="s" shape="rect" width="40" height="40"/></symbol></symbols>'''
    body = '''<instance id="hidden" symbol="sym"><override target="s" property="visible" value="false"/></instance>
      <instance id="round" symbol="sym" x="50"><override target="s" property="shape" value="ellipse"/></instance>'''
    px = Renderer.open(scene(tmp_path, body, symbols), strict=True).frame_rgba(.5)
    assert px[20, 20, 3] == 0
    assert px[20, 70, 3] == 255 and px[0, 50, 3] == 0


@pytest.mark.parametrize("condition,visible", [("[]", True), ("NaN", False), ("false", False)])
def test_conditions_follow_expression_truthiness(tmp_path, condition, visible):
    body = f'<shape id="s" shape="rect" width="20" height="20" condition="{condition}"/>'
    px = Renderer.open(scene(tmp_path, body), strict=True).frame_rgba(.5)
    assert bool(px[5, 5, 3]) == visible


def test_sequence_media_and_local_keyframes_use_one_start_offset(tmp_path):
    for i in range(10):
        Image.new("RGB", (10, 10), "red" if i < 5 else "blue").save(tmp_path / f"{i:02}.png")
    assets = '''<assets><imageSequence id="img" src="%02d.png" first="0" last="9" fps="10" width="10" height="10"/></assets>'''
    direct = '<layer id="l" asset="img" start="1"><animate property="x" timeBase="local"><key time="0" value="0"/><key time="1" value="50"/></animate></layer>'
    sequence = '''<sequence id="q"><shape id="intro" shape="rect" width="10" height="10" end="1"/>
      <layer id="l" asset="img"><animate property="x" timeBase="local"><key time="0" value="0"/>
      <key time="1" value="50"/></animate></layer></sequence>'''
    a = Renderer.open(scene(tmp_path, direct, assets, "direct.xml"), strict=True)
    b = Renderer.open(scene(tmp_path, sequence, assets, "sequence.xml"), strict=True)
    for time in (1.2, 1.7):
        np.testing.assert_array_equal(a.frame_rgb(time), b.frame_rgb(time))
    np.testing.assert_array_equal(b.frame_rgb(1.7)[5, 37], [0, 0, 255])


@pytest.mark.parametrize("attributes,expected", [('timeStretch="2"', 2), ('loop="2"', 3),
    ('speed="2" clipIn="0.2" clipOut="0.8" timeStretch="2" loop="1"', 1.2)])
def test_sequence_duration_includes_trim_stretch_speed_and_loops(tmp_path, attributes, expected):
    doc = load(scene(tmp_path, f'''<sequence id="q"><layer id="l" asset="v" {attributes}/>
       <shape id="next" shape="rect" width="10" height="10" end="1"/></sequence>''',
       '<assets><video id="v" src="unused.mp4" width="10" height="10" fps="10" duration="1"/></assets>'), strict=True)
    assert doc.window(doc.ids["l"]) == pytest.approx((0, expected))
    assert doc.window(doc.ids["next"])[0] == pytest.approx(expected)


def test_effective_instance_references_follow_clocks_overrides_and_nested_scopes(tmp_path):
    symbols = '''<symbols><symbol id="inner" width="100" height="100" duration="4">
      <shape id="s" shape="rect" width="5" height="5"><animate property="x"><key time="0" value="0"/>
      <key time="2" value="40"/></animate></shape></symbol>
      <symbol id="outer" width="100" height="100" duration="5"><instance id="nested" symbol="inner" speed="2"/></symbol></symbols>'''
    body = '''<instance id="i" symbol="outer" start="1"/>
      <shape id="follow" shape="rect" width="5" height="5" y="20"><expression property="x">prop('i/nested/s.x')</expression></shape>
      <shape id="linked" shape="rect" width="5" height="5" y="40"><link property="x" source="i/nested/s.x"/></shape>'''
    r = Renderer.open(scene(tmp_path, body, symbols), strict=True)
    px = r.frame_rgba(1.5)
    for y in (2, 22, 42):
        assert px[y, 22, 3] == 255 and px[y, 2, 3] == 0


def test_reference_inside_symbol_uses_its_own_instance_clock(tmp_path):
    symbols = '''<symbols><symbol id="sym" width="100" height="100" duration="4">
      <shape id="s" shape="rect" width="5" height="5"><expression property="x">20 * time</expression></shape>
      <shape id="f" shape="rect" width="5" height="5" y="10"><expression property="x">prop('s.x')</expression></shape>
      </symbol></symbols>'''
    r = Renderer.open(scene(tmp_path, '<instance id="i" symbol="sym" start="1" speed="2"/>', symbols), strict=True)
    px = r.frame_rgba(1.5)
    assert px[2, 22, 3] == 255 and px[12, 22, 3] == 255


@pytest.mark.parametrize("property,base,value", [
    ("type", "rect", "ellipse"), ("mode", "intersect", "subtract"),
    ("invert", "false", "true"), ("fillRule", "nonzero", "evenodd"),
])
def test_mask_discrete_properties_use_evaluated_values(tmp_path, property, base, value):
    attrs = dict(type="path", mode="intersect", invert="false", fillRule="nonzero")
    attrs[property] = base
    def body(animate):
        values = dict(attrs)
        if not animate:
            values[property] = value
        mask = ' '.join(f'{k}="{v}"' for k, v in values.items())
        expr = f'<animate property="{property}"><key time="0" value="{value}"/></animate>' if animate else ''
        return f'''<shape id="s" shape="rect" width="60" height="60"><mask {mask}
          width="40" height="40" path="M0,0 H40 V40 H0 Z M10,10 H30 V30 H10 Z">{expr}</mask></shape>'''
    dynamic = Renderer.open(scene(tmp_path, body(True), name="dynamic.xml"), strict=True)
    direct = Renderer.open(scene(tmp_path, body(False), name="direct.xml"), strict=True)
    np.testing.assert_array_equal(dynamic.frame_rgba(.5), direct.frame_rgba(.5))


def test_image_sequence_loops_reverses_and_animates_pattern_source(tmp_path):
    Image.new("RGB", (10, 10), "red").save(tmp_path / "00.png")
    Image.new("RGB", (10, 10), "blue").save(tmp_path / "01.png")
    assets = '''<assets><imageSequence id="img" src="%02d.png" first="0" last="1" fps="2" width="10" height="10"/></assets>'''
    r = Renderer.open(scene(tmp_path, '''<layer id="l" asset="img" loop="5">
      <animate property="reverse"><key time="0" value="false"/><key time="2" value="true"/></animate>
      </layer>''', assets), strict=True)
    for t, color in ((.1, [255, 0, 0]), (.7, [0, 0, 255]), (1.1, [255, 0, 0]), (2.1, [0, 0, 255])):
        np.testing.assert_array_equal(r.frame_rgb(t)[5, 5], color)
    r = Renderer.open(scene(tmp_path, '<shape id="s" shape="rect" width="50" height="50" fill="url(#p)"/>',
        assets + '<paints><pattern id="p" asset="img"/></paints>', "pattern.xml"), strict=True)
    np.testing.assert_array_equal(r.frame_rgb(.1)[5, 5], [255, 0, 0])
    np.testing.assert_array_equal(r.frame_rgb(.7)[5, 5], [0, 0, 255])


def test_animated_motion_blur_selection_is_not_cached(tmp_path):
    def body(animation):
        return f'''<shape id="s" shape="rect" width="5" height="5" motionBlur="off">
          <expression property="x">40 * time</expression>{animation}</shape>'''
    animated = '<animate property="motionBlur"><key time="0" value="off"/><key time="1" value="on"/></animate>'
    r = Renderer.open(scene(tmp_path, body(animated)), strict=True)
    assert np.count_nonzero(r.frame_rgba(.5)[..., 3]) == 25
    px = r.frame_rgba(1.5)
    control = Renderer.open(scene(tmp_path, body('').replace('motionBlur="off"', 'motionBlur="on"'), name="on.xml"), strict=True)
    np.testing.assert_array_equal(px, control.frame_rgba(1.5))
    assert np.count_nonzero(px[..., 3]) > 25


def test_evaluated_effect_stack_keeps_echo_tail_after_node_end(tmp_path):
    effects = '''<effects><effect id="echo" type="echo" samples="2"><param name="interval" value=".5"/>
      <param name="decay" value=".5"/></effect></effects>'''
    r = Renderer.open(scene(tmp_path, '''<shape id="s" shape="rect" width="10" height="10" end="1">
      <animate property="effects"><key time="0" value="echo"/></animate></shape>''', after=effects), strict=True)
    assert r.frame_rgba(.5)[5, 5, 3] == 255
    assert r.frame_rgba(1.2)[5, 5, 3] in (127, 128)
    assert r.frame_rgba(1.6)[5, 5, 3] == 0
