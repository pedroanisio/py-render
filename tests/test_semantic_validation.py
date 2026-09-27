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
