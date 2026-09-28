"""Speed paths (prefix reuse, noise tables, Numba/CUDA kernels, caches, GPU encoding) must render as before."""
import os

import numpy as np
import pytest

from scenerender import expr, gpu, kernels
from scenerender.expr import ExprError, builtin_functions, compile_expr

needs_numba = pytest.mark.skipif(kernels.nb is None, reason="numba not installed")


def run(src, time=1.5, **env):
    return compile_expr(src)({**builtin_functions(7, time), "time": time, **env})


# ---------------------------------------------------------------- expressions
PRE = ("let XS = [0, 10.5, -21, 40]; let ph = (index * 0.754877666 + 0.3) % 1; "
       "let s = (Math.max(0, time - 1) / 2 + ph) % 1; let u = s * 3; let i = Math.min(2, Math.floor(u)); "
       "let x = XS[i] + (XS[i + 1] - XS[i]) * (u - i); ")


@pytest.mark.parametrize("tail", ["x", "let xx = x - 6; xx * 2", "Math.max(0, Math.min(1, s / 0.03)) * 0.4",
                                  "[x, u, i, -1.5]"])
def test_shared_prefixes_reuse_equal_results(tail):
    expr._PREFIXES.clear()
    expr._PREFIX_STORED.clear()
    fresh = [run(PRE + tail, time=t, index=float(k)) for t in (1.25, 2.5) for k in range(3)]
    run(PRE + "x", time=1.25, index=0.0)         # another expression stores the shared prefix first
    run(PRE + "u", time=2.5, index=1.0)
    assert [run(PRE + tail, time=t, index=float(k)) for t in (1.25, 2.5) for k in range(3)] == fresh


def test_prefix_reuse_keeps_step_accounting():
    src = PRE + "x"
    expr._PREFIXES.clear()
    expr._PREFIX_STORED.clear()
    env = {**builtin_functions(7, 1.5), "time": 1.5, "index": 2.0}
    fn = compile_expr(src)
    limit = 1
    while True:                      # the fewest steps the expression needs
        try:
            fn(env, max_steps=limit)
            break
        except ExprError:
            limit += 1
    fn(env)                          # the prefix is now cached
    fn(env, max_steps=limit)         # still enough
    with pytest.raises(ExprError):
        fn(env, max_steps=limit - 1)


def test_impure_prefixes_are_not_shared():
    # random() draws depend on the evaluation, so its declarations are never reused.
    a = run("let r = random(); r", index=0.0)
    b = run("let r = random(); r + 0", index=0.0)
    assert a == b          # same draw sequence, evaluated each time
    assert compile_expr("let r = random(); r")._prefix == 0
    assert compile_expr("let q = Math.floor(time); q")._prefix == 1


@pytest.mark.parametrize("src", ["1.5 + 2.25", "7.5 % -2", "0 / 0", "1 / 0", "-1 / 0", "2 ** 0.5", "3 < 2.5",
                                 "0.1 * 3 === 0.30000000000000004", "[1, 2] + [3, 4]", "'a' + 1", "-[1, -2.5]"])
def test_number_fast_paths_match_general_semantics(src):
    import math
    a = run(src)
    b = compile_expr("(" + src + ") + 0 - 0")({**builtin_functions(7, 1.5), "time": 1.5}) \
        if isinstance(a, float) else a
    assert (a == b) or (isinstance(a, float) and math.isnan(a) and math.isnan(b))


# ---------------------------------------------------------------- noise
@needs_numba
@pytest.mark.parametrize("kind", ["basic", "turbulent", "rocky", "strings", "smooth", "turbulent-sharp"])
def test_fractal_grid_equals_fractal(kind):
    from scenerender.effects.fields import fractal, fractal_grid
    xs = (np.arange(64, dtype=np.float32) - 7) * np.float32(0.19)
    ys = (np.arange(40, dtype=np.float32) + 3) * np.float32(0.19) - np.float32(1.37)
    X, Y = np.meshgrid(xs, ys)
    memo = {}
    for evolution in (0.4, 1.7, 1.9):
        want = fractal(X, Y, 991, 5.5, evolution, kind)
        np.testing.assert_array_equal(fractal_grid(xs, ys, 991, 5.5, evolution, kind), want)
        np.testing.assert_array_equal(fractal_grid(xs, ys, 991, 5.5, evolution, kind, memo=memo), want)
        ys = ys - np.float32(0.3)              # the field drifts: the memo must follow
        X, Y = np.meshgrid(xs, ys)


@needs_numba
def test_fbm_kernel_equals_numpy(monkeypatch):
    from scenerender.assets import generator
    rng = np.random.default_rng(3)
    x, y = rng.random((30, 41)) * 20 - 4, rng.random((30, 41)) * 15
    perm = generator._perm(77)
    monkeypatch.setenv("SCENERENDER_KERNELS", "0")
    want = generator.fbm(x, y, 2.3, perm, 5)
    monkeypatch.setenv("SCENERENDER_KERNELS", "1")
    np.testing.assert_array_equal(generator.fbm(x, y, 2.3, perm, 5), want)


# ---------------------------------------------------------------- raster kernels
@needs_numba
def test_dither_and_bgra_kernels_equal_numpy(monkeypatch):
    import cairo
    from scenerender import paint, raster
    raw = np.random.default_rng(0).integers(0, 256, (21, 33, 4), dtype=np.uint8)
    raw[..., :3] = np.minimum(raw[..., :3], raw[..., 3:])
    a, b = np.empty((21, 33, 4), np.float32), np.empty((21, 33, 4), np.float32)
    monkeypatch.setenv("SCENERENDER_KERNELS", "0")
    raster._bgra_to_working(raw, True, a)
    g = cairo.LinearGradient(0, 0, 90, 30)
    g.add_color_stop_rgba(0, 1, .2, .1, .9)
    g.add_color_stop_rgba(1, .1, .3, 1, .4)
    m = cairo.Matrix(1.1, .2, -.1, .9, 3, 5)
    s0 = paint._dither_surface(g, m, 3, 5, 61, 43)
    monkeypatch.setenv("SCENERENDER_KERNELS", "1")
    raster._bgra_to_working(raw, True, b)
    s1 = paint._dither_surface(g, m, 3, 5, 61, 43)
    np.testing.assert_array_equal(a, b)

    def pixels(s):
        return np.frombuffer(s.get_data(), np.uint8).reshape(s.get_height(), s.get_stride())[:, :4 * s.get_width()]
    np.testing.assert_array_equal(pixels(s0), pixels(s1))


# ---------------------------------------------------------------- GPU
needs_gpu = pytest.mark.skipif(not gpu.enabled(), reason="no CUDA device")


@needs_gpu
@needs_numba
def test_gpu_kernels_equal_cpu_kernels(monkeypatch):
    from scenerender import raster
    rng = np.random.default_rng(4)
    px = rng.random((600, 500, 4), dtype=np.float32) * 1.1
    px[..., 3] = np.clip(px[..., 3], 0, 1)
    px[:100, :, 3] = 0
    px[..., :3] *= px[..., 3:]
    field = rng.standard_normal((600, 500, 3)).astype(np.float32)
    wmap = rng.random((600, 500, 1), dtype=np.float32)
    ops = [(kernels.OP_LGG, np.array([0, .01, .005, 1, 1 / 1.2, 1, 1, 1, 1.01], np.float32)),
           (kernels.OP_GRADE, np.array([.95, 1.04, .01], np.float32))]
    calls = {
        "display": lambda: kernels.display_chain(px, ops, True),
        "exposure": lambda: kernels.exposure(px, 1.3),
        "grain": lambda: kernels.grain(px, field, .004, .5, np.array([1, .9, 1.1], np.float32)),
        "vignette": lambda: kernels.vignette(px, wmap, np.array([0, 0, 0, 1], np.float32)),
        "mix": lambda: kernels.mix(px, px[::-1].copy(), .3),
        "rgb8": lambda: raster.working_to_rgb8(px, True, (.2, .3, .4)),
    }
    for name, call in calls.items():
        monkeypatch.setattr(gpu, "MIN_PIXELS", 1 << 30)
        want = call()
        monkeypatch.setattr(gpu, "MIN_PIXELS", 1)
        got = call()
        if name in ("display", "grain"):     # the same operations, one libm vs another for powf
            np.testing.assert_allclose(got, want, rtol=1e-6, atol=1e-7, err_msg=name)
        else:
            np.testing.assert_array_equal(got, want, err_msg=name)


@needs_gpu
@needs_numba
def test_gpu_heat_haze_equals_cpu(tmp_path, monkeypatch):
    from scenerender.render import Renderer
    p = tmp_path / "haze.xml"
    p.write_text('<scene version="1.1"><project width="640" height="480" fps="24" duration="2"/><composition>'
                 '<shape id="s" shape="rect" x="20" y="20" width="560" height="400" fill="#C06040"/>'
                 '<adjustment id="h" effects="haze"/></composition><effects>'
                 '<effect id="haze" type="heat-haze" amount="4" size="26" speed="3" frequency="2"/></effects></scene>')
    monkeypatch.setattr(gpu, "MIN_PIXELS", 1 << 30)
    want = [Renderer.open(str(p)).frame_linear(t) for t in (0.5, 1.25)]
    monkeypatch.setattr(gpu, "MIN_PIXELS", 1)
    r = Renderer.open(str(p))
    for t, w in zip((0.5, 1.25), want):
        np.testing.assert_array_equal(r.frame_linear(t), w)


def test_gpu_encoder_respects_switches(monkeypatch):
    from scenerender import output
    job = type("Job", (), {"codec": "h264", "a": lambda self, k, d=None: None})()
    monkeypatch.setenv("SCENERENDER_GPU", "0")
    assert output.gpu_encoder(job) is None
    monkeypatch.setenv("SCENERENDER_GPU", "1")
    assert output.gpu_encoder(job, two_pass=True) is None
    job.codec = "prores"
    assert output.gpu_encoder(job) is None


# ---------------------------------------------------------------- evaluator caches
def test_static_attribute_cache_and_reference_shortcut(tmp_path):
    from scenerender.render import Renderer
    p = tmp_path / "s.xml"
    p.write_text('<scene version="1.1"><project width="32" height="32" fps="4" duration="2"/><composition>'
                 '<group id="g" motionBlur="off"><shape id="s" shape="rect" width="8" height="8" x="3" '
                 'fill="#ff0000"><animate property="y"><key time="0" value="0"/><key time="1" value="10"/>'
                 '</animate></shape></group></composition></scene>')
    r = Renderer.open(str(p))
    rc, s = r.rc, r.doc.ids["s"]
    from scenerender.evaluator import Ctx
    ctx = Ctx(t=.5, comp_t=.5)
    assert rc.ev.num(s, "x", ctx) == 3 and rc.ev.num(s, "x", ctx) == 3       # cached
    assert rc.ev.num(s, "y", ctx) == 5 and rc.ev.num(s, "y", Ctx(t=1, comp_t=1)) == 10   # animated: not cached
    assert rc.ev.num(s, "missing", ctx, 4.5) == 4.5 and rc.ev.num(s, "missing", ctx, 1.5) == 1.5
    assert rc.ev.reference("nope", s, ctx) == (None, ctx)
    assert rc.motion_blur_mode(s, ctx) == "off" and rc.motion_blur_mode(s, ctx) == "off"


def test_small_frames_encode_on_the_cpu(monkeypatch):
    from scenerender import output
    monkeypatch.setenv("SCENERENDER_GPU", "1")
    monkeypatch.setattr(output, "nvenc_ffmpeg", lambda codec="h264_nvenc": "/usr/bin/ffmpeg")
    job = type("Job", (), {"codec": "h264", "a": lambda self, k, d=None: None})()
    assert output.gpu_encoder(job, dims=(160, 90)) is None
    assert output.gpu_encoder(job, dims=(1920, 1080)) == "/usr/bin/ffmpeg"
