"""compositor raster cache: static content drawn once per window, reused exactly while unmoved and
resampled while its placement animates; never stale after edits; dynamic content is not cached."""
from __future__ import annotations

import urllib.parse

import numpy as np
import pytest
from PIL import Image

from scenerender import compositor, document
from scenerender.compositor import RenderContext
from scenerender.evaluator import Evaluator
from scenerender.registry import load_plugins

load_plugins()


@pytest.fixture(autouse=True)
def cache_on(monkeypatch):
    monkeypatch.setattr(compositor, "RASTER_CACHE", True)


def load(tmp_path, body, effects="", name="s.xml"):
    p = tmp_path / name
    p.write_text(f'''<scene version="1.1">
      <project width="160" height="90" fps="24" duration="4" background="#101820FF"/>
      <assets><image id="img" src="pic.png" width="64" height="48"/></assets>
      <composition>{body}</composition>
      {f"<effects>{effects}</effects>" if effects else ""}</scene>''')
    doc = document.load(str(p))
    return RenderContext(doc, Evaluator(doc))


def picture(tmp_path, seed=0, size=(64, 48)):
    """A smooth photographic-like test image (per-pixel noise is the worst case for any resampling)."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size[1], 0:size[0]] / 16.0
    a = np.zeros((size[1], size[0], 4), np.uint8)
    for c in range(3):
        f, ph = rng.uniform(0.5, 1.5, 2), rng.uniform(0, 6.3)
        a[..., c] = (127.5 + 127 * np.sin(f[0] * x + f[1] * y + ph)).astype(np.uint8)
    a[..., 3] = 255
    Image.fromarray(a).save(tmp_path / "pic.png")


STATIC = '''<group id="g"><shape id="s" shape="ellipse" x="30" y="20" width="60" height="40" fill="#D0A040FF"/>
  <layer id="l" asset="img" x="90" y="30" boxWidth="50" boxHeight="40"/></group>'''


def frames(rc, times):
    return [rc.render_frame(t).px.copy() for t in times]


def exact(monkeypatch, rc, times):
    monkeypatch.setattr(compositor, "RASTER_CACHE", False)
    try:
        return frames(rc, times)
    finally:
        monkeypatch.setattr(compositor, "RASTER_CACHE", True)


def test_unmoved_static_content_is_reused_exactly(tmp_path, monkeypatch):
    picture(tmp_path)
    body = STATIC.replace('<group id="g">', '<group id="g"><animate property="opacity"><key time="0" value="0.3"/>'
                                            '<key time="1" value="1"/></animate>')
    rc = load(tmp_path, body)
    times = [0.1, 0.2, 0.3, 0.6]
    got = frames(rc, times)
    assert any(k[0] == "node" or k[0] == "run" for k in rc._rcache if isinstance(k, tuple)), "nothing cached"
    for a, b in zip(got, exact(monkeypatch, load(tmp_path, body, name="e.xml"), times)):
        np.testing.assert_array_equal(a, b)


def test_moving_static_content_is_resampled_closely(tmp_path, monkeypatch):
    picture(tmp_path)
    body = STATIC.replace('<group id="g">', '<group id="g"><animate property="x"><key time="0" value="0"/>'
                                            '<key time="1" value="17.3"/></animate>')
    rc = load(tmp_path, body)
    times = [0.1, 0.2, 0.3, 0.45, 0.6, 0.8]
    got = frames(rc, times)
    assert any(isinstance(v, dict) and not v["exact"] for v in rc._rcache.values()), "no resampling raster"
    ref = exact(monkeypatch, load(tmp_path, body, name="e.xml"), times)
    for a, b in zip(got, ref):
        d = np.abs(a - b)
        assert d.mean() < 0.01 and d.max() < 0.5


def test_document_edits_are_never_served_stale(tmp_path):
    picture(tmp_path)
    rc = load(tmp_path, STATIC)
    before = rc.render_frame(0.2).px.copy()
    rc.doc.ids["s"].set("fill", "#20C040FF")
    after = rc.render_frame(0.2).px
    assert np.abs(after - before).max() > 0.1


def test_changed_image_files_are_never_served_stale(tmp_path):
    picture(tmp_path, seed=0)
    rc = load(tmp_path, STATIC)
    before = rc.render_frame(0.2).px.copy()
    picture(tmp_path, seed=1, size=(66, 48))          # a different size: no timestamp-granularity race
    after = rc.render_frame(0.2).px
    assert np.abs(after - before).max() > 0.1


def test_animated_content_is_not_cached(tmp_path):
    picture(tmp_path)
    body = STATIC.replace('fill="#D0A040FF"/>', 'fill="#D0A040FF"><animate property="fill"><key time="0" value="#000000FF"/>'
                                                '<key time="1" value="#FFFFFFFF"/></animate></shape>')
    rc = load(tmp_path, body)
    rc.render_frame(0.2)
    assert not rc._rc_static(rc.doc.ids["g"], 0, True)[0]
    assert not rc._rc_static(rc.doc.ids["s"], 0, True)[0]


@pytest.mark.parametrize("code,pointwise", [
    ("void main(){ vec4 t = texture(inputTexture, uv); fragColor = vec4(t.rgb * .5, t.a); }", True),
    ("void main(){ fragColor = texture(inputTexture, uv) * uv.x; }", False),
    ("void main(){ fragColor = texture(inputTexture, uv + vec2(.01, 0.)); }", False),
    ("void main(){ fragColor = texture(inputTexture, uv) * sin(time); }", False),
])
def test_only_pointwise_shaders_join_a_raster(tmp_path, code, pointwise):
    picture(tmp_path)
    src = urllib.parse.quote(code)
    rc = load(tmp_path, STATIC.replace('<group id="g">', '<group id="g" effects="fx">'),
              effects=f'<effect id="fx" type="shader" src="data:,{src}"/>')
    assert rc._rc_static(rc.doc.ids["g"], 0, True)[0] is pointwise
