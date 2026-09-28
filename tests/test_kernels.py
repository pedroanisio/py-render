"""Fused kernels, effect reuse and the exact fast paths must match the reference NumPy rendering."""
import numpy as np
import pytest
from PIL import Image

from scenerender import kernels
from scenerender.evaluator import Ctx
from scenerender.raster import Buf
from scenerender.render import Renderer

pytestmark = pytest.mark.skipif(kernels.nb is None, reason="numba not installed")

LIGHTS = ('<light id="amb" type="ambient" colorTemperature="5000" intensity="0.3"/>'
          '<light id="key" type="directional" colorTemperature="5000" yaw="-45" pitch="-45" castShadow="true" '
          'shadowSoftness="0.4"/>'
          '<light id="low" type="directional" yaw="80" pitch="-5" castShadow="true" shadowSoftness="0.2"/>'
          '<light id="pt" type="point" x="10" y="8" z="30"/>')
EFFECTS = ('<effect id="lgg" type="lift-gamma-gain" lift="0,0.01,0.005" gamma="1,1.2,0.9" gain="1,1,1.01"/>'
           '<effect id="grade" type="color-grade" saturation="0.95" contrast="1.04" brightness="0.01"/>'
           '<effect id="expo" type="exposure" exposure="0.3"/>'
           '<effect id="grain" type="film-grain" intensity="0.08" size="1.1" seed="5"/>'
           '<effect id="vig" type="vignette" intensity="0.2"/>'
           '<effect id="lit" type="lighting" lights="amb key" relief="0.8"/>'
           '<effect id="lit-low" type="lighting" lights="amb low" relief="6"/>'
           '<effect id="lit-pt" type="lighting" lights="amb pt" relief="0.8"/>'
           '<effect id="shadow" type="drop-shadow" color="#1E1E1C59" radius="3" offsetX="2" offsetY="-3"/>')


def scene(tmp_path, body, *, duration=2, name="k"):
    p = tmp_path / (name + ".xml")
    p.write_text(f'<scene version="1.1"><project width="48" height="32" fps="4" duration="{duration}"/>'
                 f'<composition>{body}</composition><lights>{LIGHTS}</lights><effects>{EFFECTS}</effects></scene>')
    return Renderer.open(str(p), strict=True)


def tile(seed=0, h=32, w=48):
    rng = np.random.default_rng(seed)
    px = rng.random((h, w, 4), dtype=np.float32) * 1.1
    px[..., 3] = np.clip(px[..., 3], 0, 1)
    px[: h // 4, ..., 3] = 0          # transparent rows
    px[:, : w // 6, 3] = 1            # opaque columns
    px[..., :3] *= px[..., 3:]
    return Buf(px, 3, -2)


@pytest.mark.parametrize("ids", [["lgg"], ["grade"], ["lgg", "grade"], ["expo"], ["grain"], ["vig"],
                                 ["lit"], ["lit-low"], ["lit-pt"], ["shadow"],
                                 ["lgg", "grade", "expo", "grain", "vig"]])
def test_kernels_match_numpy_effects(tmp_path, monkeypatch, ids):
    r = scene(tmp_path, "")
    ctx = Ctx(t=.5, comp_t=.5, frame=2)
    buf = tile()
    monkeypatch.setenv("SCENERENDER_KERNELS", "0")
    want = r.rc.apply_effects(ids, buf.copy(), ctx)
    monkeypatch.setenv("SCENERENDER_KERNELS", "1")
    got = r.rc.apply_effects(ids, buf.copy(), ctx)
    assert got.rect == want.rect
    np.testing.assert_allclose(got.px, want.px, rtol=2e-5, atol=2e-6)


def test_rgb8_matches_numpy(monkeypatch):
    from scenerender.raster import working_to_rgb8
    px = tile(3, 64, 64).px
    for linear in (True, False):
        for bg in (None, (.7, .68, .65)):
            monkeypatch.setenv("SCENERENDER_KERNELS", "0")
            want = working_to_rgb8(px, linear, bg)
            monkeypatch.setenv("SCENERENDER_KERNELS", "1")
            got = working_to_rgb8(px, linear, bg)
            assert np.abs(want.astype(int) - got).max() <= 1
            assert (want != got).mean() < 1e-3


def test_frames_match_numpy_rendering(tmp_path, monkeypatch):
    body = ('<group id="g" effects="lit shadow"><shape id="s" shape="rect" x="4" y="4" width="30" height="20" '
            'fill="#C06040"><animate property="x"><key time="0" value="4"/><key time="2" value="12"/></animate>'
            '</shape></group><adjustment id="fin" effects="lgg grade expo grain vig"/>')
    monkeypatch.setenv("SCENERENDER_KERNELS", "0")
    r = scene(tmp_path, body)
    want = [r.frame_rgb(t) for t in (0, .25, .5, 1.5)]
    monkeypatch.setenv("SCENERENDER_KERNELS", "1")
    r = scene(tmp_path, body)
    for t, w in zip((0, .25, .5, 1.5), want):
        got = r.frame_rgb(t)
        assert np.abs(w.astype(int) - got).max() <= 1, t


@pytest.mark.parametrize("attrs", ['saturation="0.5"',
                                   'saturation="1"><animate property="saturation"><key time="0" value="1"/>'
                                   '<key time="2" value="0"/></animate></effect><effect id="unused" type="vignette"'])
def test_reused_effects_equal_a_fresh_render(tmp_path, attrs):
    # The shape holds still from 0.5 s: the group's effect input repeats while (in the second case) its
    # saturation keeps animating, which must disable reuse.
    body = ('<group id="g" effects="sat lit"><shape id="s" shape="rect" x="4" y="4" width="30" height="20" '
            'fill="#C06040"><animate property="x"><key time="0" value="4"/><key time="0.5" value="12"/></animate>'
            '</shape></group>')
    global EFFECTS
    saved = EFFECTS
    EFFECTS = saved + f'<effect id="sat" type="color-grade" {attrs}/>'
    try:
        sequential = scene(tmp_path, body, name="seq")
        seq = [sequential.frame_linear(t) for t in (.25, .5, .75, 1, 1.25)]
        for t, px in zip((.25, .5, .75, 1, 1.25), seq):
            fresh = scene(tmp_path, body, name=f"fresh{int(t * 100)}")
            np.testing.assert_array_equal(px, fresh.frame_linear(t), err_msg=str(t))
    finally:
        EFFECTS = saved


def test_constant_effect_input_is_reused(tmp_path, monkeypatch):
    r = scene(tmp_path, '<group id="g" effects="lit"><shape id="s" shape="rect" x="4" y="4" width="30" '
                        'height="20" fill="#C06040"/></group>')
    import scenerender.effects.light as light
    calls = []
    original = light._lighting_fused
    monkeypatch.setattr(light, "_lighting_fused", lambda *a: calls.append(1) or original(*a))
    frames = [r.frame_linear(t) for t in (0, .25, .5)]
    assert len(calls) == 1
    np.testing.assert_array_equal(frames[0], frames[2])


def _shadow_reference(height, x, y, dx, dy, dz, steps, bias, softness):
    """The ray march before steps were skipped."""
    from scenerender.effects import sample
    visibility = np.ones(height.shape, np.float32)
    for u in (np.arange(steps) + 1) / (steps + 1):
        obstacle = sample(height[..., None], x + dx * u, y + dy * u)[..., 0]
        clearance = height + dz * u + max(bias, 1e-4) - obstacle
        visibility = np.minimum(visibility, np.clip(clearance / max(max(softness, 0) * u, 1e-4), 0, 1))
    return visibility


@pytest.mark.parametrize("dz,softness", [(2112.0, .4), (30.0, .4), (3.0, 0.0), (0.5, .2), (-4.0, .1)])
def test_shadow_step_skipping_is_exact(dz, softness):
    from scenerender.effects import grid
    from scenerender.effects.light import _shadow
    rng = np.random.default_rng(1)
    height = (rng.random((24, 40)).astype(np.float32) * 6).astype(np.float32)
    buf = Buf(np.zeros((24, 40, 4), np.float32))
    want = _shadow_reference(height, *grid(buf), -30.0, -12.0, dz, 16, .0005, softness)
    got = _shadow(height, lambda: grid(buf), -30.0, -12.0, dz, 16, .0005, softness)
    np.testing.assert_array_equal(np.ones_like(want) if got is None else got, want)


@pytest.mark.parametrize("dx,dy", [(3, -2), (0, 0), (-40, 5), (60, 60)])
def test_whole_pixel_shift_equals_bilinear_sampling(dx, dy):
    from scenerender.effects import sample, shifted
    px = tile(5).px
    y, x = np.indices(px.shape[:2], dtype=np.float32)
    np.testing.assert_array_equal(shifted(px, float(dx), float(dy)), sample(px, x - dx, y - dy))


@pytest.mark.parametrize("attrs", ['x="5" y="-3"', 'x="-2" y="4"', 'x="5.5" y="1"', 'x="3" y="2" scaleX="0.5"'])
def test_placed_images_equal_cairo(tmp_path, monkeypatch, attrs):
    import scenerender.assets.image_pixels as ip
    rng = np.random.default_rng(2)
    Image.fromarray(rng.integers(0, 256, (20, 30, 4), dtype=np.uint8)).save(tmp_path / "noise.png")
    p = tmp_path / "img.xml"
    p.write_text('<scene version="1.1"><project width="48" height="32" fps="4" duration="1"/><assets>'
                 '<image id="img" src="noise.png" width="30" height="20"/></assets>'
                 f'<composition><layer id="L" asset="img" {attrs}/></composition></scene>')
    fast = Renderer.open(str(p), strict=True).frame_linear(0)
    monkeypatch.setattr(ip, "_draw_translated", lambda *a: None)
    np.testing.assert_array_equal(fast, Renderer.open(str(p), strict=True).frame_linear(0))
