"""XSD-valid cross-field violations must be visible to strict load and validate."""
import hashlib

from lxml import etree
from PIL import Image
import pytest

from scenerender.document import SceneError, load
from scenerender.render import Renderer
from scenerender.schema import default_schema


def scene(tmp_path, assets="", after="", body=""):
    path = tmp_path / "scene.xml"
    path.write_text(f'''<scene version="1.1"><project width="20" height="20" fps="10" duration="2"/>
      <assets>{assets}</assets><composition>{body}</composition>{after}</scene>''')
    return str(path)


@pytest.mark.parametrize("assets,after,rule", [
    ('<text id="t" width="20" height="20" size="12" text="one"><span>two</span></text>', '', 'SR-TEXT-SOURCE'),
    ('<text id="t" width="20" height="20" size="12"/>', '', 'SR-TEXT-SOURCE'),
    ('', '<physics><constraint id="p" type="pin" a="one" b="two"/></physics>', 'SR-PIN-BODY'),
    ('', '<captions><captionTrack id="c" language="en" transcribe="audio"/></captions>', 'SR-TRANSCRIPTION-CACHE'),
    ('<imageSequence id="s" src="%d.png" first="5" last="2" fps="10" width="20" height="20"/>', '', 'SR-IMAGE-SEQUENCE-RANGE'),
])
def test_explicit_cross_field_rules(tmp_path, assets, after, rule):
    path = scene(tmp_path, assets, after)
    default_schema().validator().assertValid(etree.parse(path))
    with pytest.raises(SceneError, match=rule):
        load(path, strict=True)
    assert any(rule in error for error in load(path).validation_errors)


@pytest.mark.parametrize("content", ['text=""', 'text="hello"', '><span>hello</span></text'])
def test_text_source_alternatives_and_empty_text_remain_valid(tmp_path, content):
    asset = ('<text id="t" width="20" height="20" size="12"' + content + '>') if content.startswith('>') else (
        '<text id="t" width="20" height="20" size="12" ' + content + '/>')
    assert not load(scene(tmp_path, asset), strict=True).validation_errors


def test_generated_cache_rejects_missing_and_changed_content(tmp_path):
    cache = tmp_path / "generated.png"
    Image.new("RGB", (20, 20), "red").save(cache)
    digest = hashlib.sha256(cache.read_bytes()).hexdigest()
    path = scene(tmp_path, f'<generated id="g" kind="image" provider="fixture" model="fixture" '
                 f'cache="generated.png" cacheSha256="{digest}"/>', body='<layer id="l" asset="g"/>')
    renderer = Renderer.open(path, strict=True)
    assert renderer.frame_rgba(0)[5, 5, 0] == 255
    Image.new("RGB", (20, 20), "blue").save(cache)
    with pytest.raises(SceneError, match="does not match cacheSha256"):
        renderer.frame_rgba(.1)
    cache.unlink()
    with pytest.raises(SceneError, match="not found"):
        renderer.frame_rgba(.2)


@pytest.mark.parametrize("content", [
    '<composition><repeat id="r" count="2"/></composition>',
    '<assets><imageSequence id="im" src="%d.png" first="0" last="2" fps="10" width="20" height="20"/></assets><composition/>',
    '<composition><shape id="s" shape="rect" width="20" height="20"><expression property="x">1</expression></shape></composition>',
    '<styles><token name="red" value="#ff0000"/></styles><composition/>',
])
def test_new_element_families_require_version_11(tmp_path, content):
    path = tmp_path / 'version.xml'
    xml = '<scene version="1.0"><project width="20" height="20" fps="10" duration="2"/>' + content + '</scene>'
    path.write_text(xml)
    default_schema().validator().assertValid(etree.parse(str(path)))
    with pytest.raises(SceneError, match='SR-VERSION-GATE'):
        load(str(path), strict=True)
    path.write_text(xml.replace('version="1.0"', 'version="1.1"'))
    assert not load(str(path), strict=True).validation_errors


def test_legacy_elements_accept_new_attributes(tmp_path):
    path = tmp_path / 'legacy.xml'
    path.write_text('<scene version="1.0"><project width="20" height="20" fps="10" duration="2"/>'
                    '<composition><shape id="s" shape="rect" width="10" height="10" '
                    'alignX="center" strokePosition="inside"><animate property="x">'
                    '<key time="0" value="1"/></animate></shape></composition></scene>')
    assert not load(str(path), strict=True).validation_errors
