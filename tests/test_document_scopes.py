"""Reusable scenes must preserve file origins, references, data and copy semantics."""
import hashlib

import numpy as np
import pytest
from PIL import Image

from scenerender.document import SceneError, load
from scenerender.render import Renderer


def scene(body, before="", after=""):
    return f'''<scene version="1.1"><project width="100" height="100" fps="10"
      duration="4" linearLight="false"/>{before}<composition>{body}</composition>{after}</scene>'''


def write(tmp_path, name, xml):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(xml)
    return str(p)


def test_include_preserves_relative_paths_through_multiple_levels(tmp_path):
    sub = tmp_path / "sub/deep"
    sub.mkdir(parents=True)
    Image.new("RGB", (20, 20), "red").save(sub / "red.png")
    write(tmp_path, "sub/deep/image.xml", scene('<layer id="l" asset="img"/>',
          '<assets><image id="img" src="red.png" width="20" height="20"/></assets>'))
    write(tmp_path, "sub/middle.xml", scene('<include id="inner" src="deep/image.xml"/>'))
    r = Renderer.open(write(tmp_path, "parent.xml", scene('<include id="outer" src="sub/middle.xml"/>')), strict=True)
    np.testing.assert_array_equal(r.frame_rgb(.5)[5, 5], [255, 0, 0])
    assert r.doc.resolve_path(r.doc.ids["outer/inner/img"].get("src")) == str(sub / "red.png")


def test_include_namespace_preserves_computed_references_and_tokens(tmp_path):
    write(tmp_path, "child.xml", scene('''<shape id="a" shape="rect" x="30" width="10" height="10" fill="var(--accent)"/>
       <shape id="b" shape="rect" y="20" width="10" height="10"><link property="x" source="a.x"/></shape>
       <shape id="c" shape="rect" y="40" width="10" height="10"><expression property="x">prop(param('name') + '.x')</expression></shape>''',
       '''<parameters><param id="name" type="string" default="a"/></parameters>
       <styles><token name="accent" value="#ff0000"/></styles>'''))
    r = Renderer.open(write(tmp_path, "parent.xml", scene('''<include id="a" src="child.xml"/>
       <shape id="p" shape="rect" x="60" width="10" height="10" fill="var(--accent)"/>''',
       '<styles><token name="accent" value="#0000ff"/></styles>')), strict=True)
    px = r.frame_rgb(.5)
    np.testing.assert_array_equal(px[5, 35], [255, 0, 0])
    np.testing.assert_array_equal(px[5, 65], [0, 0, 255])
    assert px[25, 35].min() == 255 and px[45, 35].min() == 255


def test_included_repeat_and_sequence_are_prepared_in_the_parent(tmp_path):
    write(tmp_path, "child.xml", scene('''<repeat id="r" over="items" var="row" offsetY="20">
       <shape id="s" shape="rect" width="10" height="10"><expression property="x">param('row')</expression></shape></repeat>
       <sequence id="q" y="50"><shape id="one" shape="rect" width="10" height="10" end="1"/>
       <shape id="two" shape="rect" x="20" width="10" height="10" end="1"/></sequence>''',
       '<parameters><param id="items" type="list" default="[10,40]"/></parameters>'))
    r = Renderer.open(write(tmp_path, "parent.xml", scene('<include id="inc" src="child.xml"/>')), strict=True)
    px = r.frame_rgba(.5)
    assert px[5, 15, 3] == 255 and px[25, 45, 3] == 255
    assert r.doc.window(r.doc.ids["inc/two"]) == (1, 2)


def test_include_strict_cycle_and_hash_contracts(tmp_path):
    bad = write(tmp_path, "bad.xml", scene('<shape id="s" shape="rect" bogus="1"/>'))
    parent = write(tmp_path, "parent.xml", scene('<include id="inc" src="bad.xml"/>'))
    with pytest.raises(SceneError, match="bogus"):
        load(parent, strict=True)
    cycle = write(tmp_path, "cycle.xml", scene('<include id="inc" src="cycle.xml"/>'))
    with pytest.raises(SceneError, match="include cycle"):
        load(cycle, strict=True)
    good = write(tmp_path, "good.xml", scene('<shape id="s" shape="rect" width="10" height="10"/>'))
    parent = write(tmp_path, "parent.xml", scene('<include id="inc" src="good.xml" sha256="' + '0'*64 + '"/>'))
    with pytest.raises(SceneError, match="sha256 mismatch"):
        load(parent, strict=True)
    digest = hashlib.sha256(open(good, "rb").read()).hexdigest()
    parent = write(tmp_path, "parent.xml", scene(f'<include id="inc" src="good.xml" sha256="{digest}"/>'))
    assert Renderer.open(parent, strict=True).frame_rgba(.5)[5, 5, 3] == 255


def test_repeat_parent_links_mattes_and_expressions_stay_in_each_copy(tmp_path):
    body = '''<repeat id="r" count="2" offsetY="30"><group id="p" x="20"/>
      <shape id="m" shape="rect" width="5" height="10" parent="p"/>
      <shape id="s" shape="rect" width="10" height="10" parent="p" matte="m" fill="#ff0000"/>
      <shape id="f" shape="rect" width="5" height="10" y="15"><expression property="x">prop('p.x')</expression></shape>
      </repeat>'''
    r = Renderer.open(write(tmp_path, "repeat.xml", scene(body)), strict=True)
    px = r.frame_rgba(.5)
    for y in (5, 35):
        assert px[y, 22, 3] == 255 and px[y, 27, 3] == 0
        assert px[y+15, 22, 3] == 255


def test_repeat_opacity_decrements_after_animation(tmp_path):
    body = '''<repeat id="r" count="2" offsetX="30" opacity="0.8" opacityStep="0.25">
      <animate property="opacity"><key time="0" value="0.5"/></animate>
      <shape id="s" shape="rect" width="20" height="20"/></repeat>'''
    r = Renderer.open(write(tmp_path, "repeat.xml", scene(body)), strict=True)
    px = r.frame_rgba(.5)
    assert int(px[5, 5, 3]) == 128 and int(px[5, 35, 3]) == 64


def test_nested_repeat_parameter_bindings_keep_outer_items(tmp_path):
    body = '''<repeat id="outer" over="xs" var="row" offsetY="30"><repeat id="inner" over="ys" var="cell">
      <shape id="s" shape="rect" width="5" height="5">
      <expression property="x">param('row') + param('cell')</expression></shape></repeat></repeat>'''
    params = '''<parameters><param id="xs" type="list" default="[10,40]"/>
      <param id="ys" type="list" default="[2,12]"/></parameters>'''
    r = Renderer.open(write(tmp_path, "nested.xml", scene(body, params)), strict=True)
    px = r.frame_rgba(.5)
    for x, y in [(12, 0), (22, 0), (42, 30), (52, 30)]:
        assert px[y+2, x+2, 3] == 255


def test_xsd_token_whitespace_is_normalized_before_reference_resolution(tmp_path):
    body = '''<group id="  parent  " x="20"/>
      <shape id=" s " parent=" parent " shape="rect" width="10" height="10"/>'''
    r = Renderer.open(write(tmp_path, "whitespace.xml", scene(body)), strict=True)
    assert "parent" in r.doc.ids and "s" in r.doc.ids
    assert r.frame_rgba(.5)[5, 25, 3] == 255


def test_included_asset_bind_and_computed_paint_references_keep_scope(tmp_path):
    child = tmp_path / "sub"
    child.mkdir()
    Image.new("RGB", (10, 10), "red").save(child / "red.png")
    write(tmp_path, "sub/child.xml", scene('''<layer id="l" asset="img"/>
      <shape id="s" shape="rect" x="20" width="10" height="10"><expression property="fill">'var(--' + param('token') + ')'</expression></shape>''',
      '''<parameters><param id="choice" type="asset" default="img"/>
      <param id="token" type="string" default="accent"/>
      <bind param="choice" target="l" property="asset"/></parameters>
      <styles><token name="accent" value="#00ff00"/></styles>
      <assets><image id="img" src="red.png" width="10" height="10"/></assets>'''))
    r = Renderer.open(write(tmp_path, "parent.xml", scene('<include id="inc" src="sub/child.xml"/>')), strict=True)
    px = r.frame_rgb(.5)
    np.testing.assert_array_equal(px[5, 5], [255, 0, 0])
    np.testing.assert_array_equal(px[5, 25], [0, 255, 0])
