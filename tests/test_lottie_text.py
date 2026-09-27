"""Render regressions for Lottie text paths and expression selectors."""
import copy
import json

import numpy as np
import pytest

from scenerender.assets import lottie as L
from test_assets import make_rc, asset_buf


def animation(text="AB"):
    st = L._static
    return {"v": "5.7.0", "fr": 10, "ip": 0, "op": 10, "w": 200, "h": 200, "assets": [],
            "fonts": {"list": [{"fName": "sans", "fFamily": "DejaVu Sans", "fStyle": "Regular"}]},
            "layers": [{"ty": 5, "ind": 1, "nm": "title", "ip": 0, "op": 10, "st": 0, "sr": 1,
                        "ks": {"p": st([0, 0, 0]), "a": st([0, 0, 0]), "s": st([100, 100, 100]),
                               "r": st(0), "o": st(100)},
                        "t": {"d": {"k": [{"t": 0, "s": {"t": text, "s": 24, "f": "sans", "j": 0,
                                                               "fc": [1, 1, 1]}}]},
                              "m": {"g": 1, "a": st([0, 0])}, "a": []}}]}


def shape(vertices, closed=False):
    return {"v": vertices, "i": [[0, 0] for _ in vertices], "o": [[0, 0] for _ in vertices], "c": closed}


def with_path(d, vertices=((20, 80), (180, 80)), **options):
    layer = d["layers"][0]
    layer["masksProperties"] = [{"nm": "baseline", "mode": "n", "inv": False,
                                  "pt": L._static(shape(vertices)), "o": L._static(100)}]
    layer["t"]["p"] = {"m": 1, **{k: L._static(v) for k, v in {"f": 0, "l": 0, "r": 0, "p": 1, "a": 0, **options}.items()}}
    return d


def renderer(tmp_path, d):
    p = tmp_path / "text.json"
    p.write_text(json.dumps(d))
    return make_rc(f'<lottie id="l" src="{p}" width="200" height="200"/>', "", tmp_path, w=200, h=200)


def pixels(rc, t=0):
    return asset_buf(rc, "l", src_t=t).region((0, 0, 200, 200))[..., 3]


def centre(a):
    y, x = np.indices(a.shape)
    assert a.sum() > 5
    return np.array([(a * (x + .5)).sum(), (a * (y + .5)).sum()]) / a.sum()


def test_text_path_renders_at_mask_baseline_and_rotates(tmp_path):
    horizontal = pixels(renderer(tmp_path, with_path(animation())))
    assert 20 < centre(horizontal)[0] < 65 and 55 < centre(horizontal)[1] < 80
    vertical = pixels(renderer(tmp_path, with_path(animation(), ((100, 20), (100, 180)))))
    # Clockwise 90-degree rotation around the first path vertex maps the glyphs
    # above a horizontal baseline to the right of the vertical baseline.
    hx, hy = centre(horizontal)
    np.testing.assert_allclose(centre(vertical), [100 - (hy - 80), 20 + hx - 20], atol=1)


def test_animated_mask_and_margin_move_rendered_text(tmp_path):
    d = with_path(animation(), f=5)
    layer = d["layers"][0]
    a, b = shape([[20, 60], [180, 60]]), shape([[20, 100], [180, 100]])
    layer["masksProperties"][0]["pt"] = {"a": 1, "k": [
        {"t": 0, "s": [a], "e": [b], "o": {"x": 0, "y": 0}, "i": {"x": 1, "y": 1}},
        {"t": 10, "s": [b]}]}
    layer["t"]["p"]["f"] = L._baked([0, 10], [5, 45])
    rc = renderer(tmp_path, d)
    np.testing.assert_allclose(centre(pixels(rc, .5)) - centre(pixels(rc)), [20, 20], atol=1)


def test_reverse_and_forced_alignment(tmp_path):
    normal = centre(pixels(renderer(tmp_path, with_path(animation("A")))))
    reverse = centre(pixels(renderer(tmp_path, with_path(animation("A"), r=1))))
    np.testing.assert_allclose(reverse, [200 - normal[0], 160 - normal[1]], atol=1)
    px = pixels(renderer(tmp_path, with_path(animation("AA"), f=10, l=20, a=1)))
    assert px[:, :60].sum() > 20 and px[:, 135:165].sum() > 20
    assert px[:, 65:130].sum() == 0


def test_closed_path_wrap_and_open_path_extrapolation():
    layer = with_path(animation(), ((20, 80), (180, 80)))["layers"][0]
    sampler, closed = L._mask_path(layer, 0)
    assert L._path_point(sampler, closed, -10, False) == pytest.approx((10, 80, 0))
    assert L._path_point(sampler, closed, 170, False) == pytest.approx((190, 80, 0))
    layer["masksProperties"][0]["pt"] = L._static(shape([[20, 20], [80, 20], [80, 80], [20, 80]], True))
    sampler, closed = L._mask_path(layer, 0)
    assert L._path_point(sampler, closed, sampler.total + 30, False) == pytest.approx((50, 20, 0))


def test_expression_selector_indexes_time_and_render(tmp_path):
    d = with_path(animation("AB"))
    d["layers"][0]["t"]["a"] = [{"s": {"t": 1, "b": 1,
        "x": "var $bm_rt; $bm_rt = textIndex == 1 && time < 0.5 ? selectorValue : 0;"},
        "a": {"o": L._static(0)}}]
    rc = renderer(tmp_path, d)
    first, later = pixels(rc), pixels(rc, .6)
    assert first[:, :35].sum() < 1
    assert later[:, :35].sum() > 20 and later.sum() > first.sum() * 1.5
    np.testing.assert_array_equal(pixels(rc), first)  # seeking is deterministic
    sel = {"t": 1, "x": "[textIndex / textTotal * selectorValue, 0, 100]"}
    assert L._selector_mult(sel, 1, 4, 0, None) == [0.5, 0, 1]


def test_vector_selector_moves_each_axis_independently(tmp_path):
    d = with_path(animation("A"))
    d["layers"][0]["t"]["a"] = [{"s": {"t": 1, "x": "[0, 100, 0]"}, "a": {"p": L._static([70, 30])}}]
    baseline = copy.deepcopy(d)
    baseline["layers"][0]["t"]["a"] = []
    np.testing.assert_allclose(centre(pixels(renderer(tmp_path, d))) -
                               centre(pixels(renderer(tmp_path, baseline))), [0, 30], atol=1)


def test_curved_path_uses_cubic_handles_and_upright_option(tmp_path):
    d = with_path(animation("AAAAAA"), ((20, 130), (180, 130)), p=0)
    path = d["layers"][0]["masksProperties"][0]["pt"]["k"]
    path["o"][0] = [0, -100]
    path["i"][1] = [0, -100]
    rc = renderer(tmp_path, d)
    px = pixels(rc)
    assert centre(px)[1] < 100
    # The tangents bend strongly, but perpendicular=0 leaves every glyph upright.
    prepared = L.prepare(rc, rc.doc.ids["l"], None)
    groups = prepared.data["layers"][0]["shapes"]
    assert all(L._num(g["it"][-1]["r"], 0) == 0 for g in groups)
    positions = [L._vec(g["it"][-1]["p"], 0, [0, 0]) for g in groups]
    assert np.ptp([p[1] for p in positions]) > 30


def test_invalid_selector_warns_without_dropping_text(tmp_path, caplog):
    d = with_path(animation("AB"))
    d["layers"][0]["t"]["a"] = [{"s": {"t": 1, "x": "1 / 0"}, "a": {"o": L._static(0)}}]
    assert pixels(renderer(tmp_path, d)).sum() > 100
    assert "finite scalar or vector" in caplog.text
