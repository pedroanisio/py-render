"""Image input declarations, layer selection and precision through rendered pixels."""
import hashlib

import numpy as np
import pytest
from PIL import Image

from scenerender.assets import buffer_for_asset, surface_for_asset
from scenerender.evaluator import Ctx
from scenerender.render import Renderer


def scene(tmp_path, assets, body='<layer id="L" asset="img"/>', *, w=16, h=8, extra="", before="", linear=True, **kwargs):
    path = tmp_path / "image.xml"
    path.write_text(f'<scene version="1.1"><project width="{w}" height="{h}" fps="4" duration="2" '
                    f'linearLight="{str(linear).lower()}"/>{before}<assets>{assets}</assets>{extra}'
                    f'<composition>{body}</composition></scene>')
    return Renderer.open(str(path), strict=True, **kwargs)


def image(src, attrs="", *, w=16, h=8, children=""):
    return f'<image id="img" src="{src}" width="{w}" height="{h}" {attrs}>{children}</image>'


@pytest.mark.parametrize("attrs,expected", [
    ("", (.2158605001 * 128/255, 0, 0, 128/255)),
    ('colorSpace="linear-srgb"', ((128/255)**2, 0, 0, 128/255)),
    ('transfer="linear"', ((128/255)**2, 0, 0, 128/255)),
    ('transfer="gamma22"', ((128/255)**3.2, 0, 0, 128/255)),
    ('alpha="none"', (.2158605001, 0, 0, 1)),
    ('alpha="premultiplied"', (128/255, 0, 0, 128/255)),
    ('alpha="straight"', (.2158605001 * 128/255, 0, 0, 128/255)),
])
def test_declared_input_color_and_alpha(tmp_path, attrs, expected):
    Image.new("RGBA", (16, 8), (128, 0, 0, 128)).save(tmp_path / "pixel.png")
    r = scene(tmp_path, image("pixel.png", attrs))
    np.testing.assert_allclose(r.frame_linear(0)[4, 8], expected, atol=2e-7)


def test_gamma_compositing_still_converts_linear_input(tmp_path):
    Image.new("RGBA", (16, 8), (128, 0, 0, 128)).save(tmp_path / "pixel.png")
    r = scene(tmp_path, image("pixel.png", 'colorSpace="linear-srgb"'), linear=False)
    expected = (1.055 * (128/255)**(1/2.4) - .055) * 128/255
    assert r.rc.render_frame(0).region((8, 4, 9, 5))[0, 0, 0] == pytest.approx(expected, abs=2e-7)


@pytest.mark.parametrize("fmt,channels", [("png", 1), ("png", 2), ("png", 3), ("png", 4),
                                         ("tiff", 1), ("tiff", 2), ("tiff", 3), ("tiff", 4)])
def test_sixteen_bit_samples_survive_decode_and_draw(tmp_path, fmt, channels):
    n = 1024
    ramp = np.arange(n, dtype=np.uint16) + 20000
    a = np.repeat(ramp[None, :, None], channels, -1)
    if channels in (2, 4):
        a[..., -1] = 32768
    path = tmp_path / f"ramp.{fmt}"
    if fmt == "png":
        import png
        with path.open("wb") as f:
            png.Writer(n, 1, greyscale=channels <= 2, alpha=channels in (2, 4), bitdepth=16).write(f, a.reshape(1, -1))
    else:
        import tifffile
        tifffile.imwrite(path, a[..., 0] if channels == 1 else a, photometric="minisblack" if channels <= 2 else "rgb",
                         extrasamples=["UNASSALPHA"] if channels in (2, 4) else None)
    r = scene(tmp_path, image(path.name, 'colorSpace="linear-srgb"', w=n, h=1), w=n, h=1)
    px = r.frame_linear(0)[0]
    opacity = 32768/65535 if channels in (2, 4) else 1
    np.testing.assert_allclose(px[:, 0], ramp/65535 * opacity, atol=1e-7)
    np.testing.assert_allclose(px[:, 3], opacity, atol=1e-7)
    assert len(np.unique(px[:, 0])) == n


@pytest.mark.parametrize("associated", [False, True])
def test_tiff_alpha_association_and_float_hdr(tmp_path, associated):
    import tifffile
    a = np.full((8, 16, 4), [50000., -.25, 2., .5], np.float32)
    tifffile.imwrite(tmp_path / "hdr.tif", a, photometric="rgb",
                     extrasamples=["ASSOCALPHA" if associated else "UNASSALPHA"])
    r = scene(tmp_path, image("hdr.tif", 'colorSpace="linear-srgb"'))
    expected = [50000, -.25, 2., .5] if associated else [25000, -.125, 1., .5]
    np.testing.assert_allclose(r.frame_linear(0)[4, 8], expected, atol=1e-6)


def test_downsampling_filters_linear_premultiplied_samples(tmp_path):
    a = np.zeros((128, 128, 4), np.uint8)
    a[:, ::2] = (255, 255, 255, 255)
    a[:, 1::2] = (255, 0, 0, 0)  # Transparent color must not bleed into the result.
    Image.fromarray(a).save(tmp_path / "stripes.png")
    r = scene(tmp_path, image("stripes.png", w=1, h=1), w=1, h=1)
    np.testing.assert_allclose(r.frame_linear(0)[0, 0], [.5, .5, .5, .5], atol=1e-6)
    a[:, 1::2] = (0, 0, 0, 255)
    Image.fromarray(a).save(tmp_path / "stripes.png")
    # Same renderer: a changed file must not reuse a stale decoded image.
    np.testing.assert_allclose(r.frame_linear(0)[0, 0], [.5, .5, .5, 1.], atol=1e-6)


def test_representation_color_proxy_size_and_hash(tmp_path, monkeypatch):
    from scenerender.assets import video
    Image.new("RGB", (16, 8), (255, 0, 0)).save(tmp_path / "base.png")
    Image.new("RGB", (4, 2), (128, 0, 0)).save(tmp_path / "alt.png")
    Image.new("RGB", (4, 2), (0, 255, 0)).save(tmp_path / "proxy.png")
    warnings = []
    monkeypatch.setattr(video, "warn_once", lambda *a: warnings.append(a))
    rep = '<representation name="preview" src="alt.png" width="4" height="2" colorSpace="linear-srgb" sha256="' + '0'*64 + '"/>'
    sha = hashlib.sha256((tmp_path / "base.png").read_bytes()).hexdigest()
    r = scene(tmp_path, image("base.png", f'proxy="proxy.png" sha256="{sha}"', children=rep), representation="preview")
    np.testing.assert_allclose(r.frame_linear(0)[4, 8], [128/255, 0, 0, 1], atol=1e-7)
    assert warnings and "sha256" in warnings[0][0]
    r.rc.cache["representation"] = "proxy"
    np.testing.assert_allclose(r.frame_linear(0)[4, 8], [0, 1, 0, 1], atol=1e-7)
    r.rc.cache["representation"] = "unknown"
    np.testing.assert_allclose(r.frame_linear(0)[4, 8], [1, 0, 0, 1], atol=1e-7)


def test_instance_input_override_has_separate_cache_identity(tmp_path):
    Image.new("RGBA", (8, 8), (128, 0, 0, 128)).save(tmp_path / "pixel.png")
    symbols = '<symbols><symbol id="sym" width="8" height="8" duration="2"><layer id="inner" asset="img"/></symbol></symbols>'
    body = '<instance id="a" symbol="sym"/><instance id="b" symbol="sym" x="8">' \
           '<override target="img" property="colorSpace" value="linear-srgb"/>' \
           '<override target="img" property="alpha" value="none"/></instance>'
    r = scene(tmp_path, image("pixel.png", w=8), body, extra=symbols)
    for t in (0, 1, .25):
        px = r.frame_linear(t)
        np.testing.assert_allclose(px[4, 4], [.2158605001 * 128/255, 0, 0, 128/255], atol=2e-7)
        np.testing.assert_allclose(px[4, 12], [128/255, 0, 0, 1], atol=2e-7)


def test_pattern_and_shader_asset_helpers_use_input_declarations(tmp_path):
    from scenerender.raster import bgra_to_working
    Image.new("RGBA", (16, 8), (128, 0, 0, 128)).save(tmp_path / "pixel.png")
    r = scene(tmp_path, image("pixel.png", 'colorSpace="linear-srgb" alpha="none"'))
    asset = r.doc.ids["img"]
    np.testing.assert_allclose(buffer_for_asset(r.rc, asset, Ctx(0, 0)).px[4, 8], [128/255, 0, 0, 1], atol=1e-7)
    surface, w, h = surface_for_asset(r.rc, asset, Ctx(0, 0))
    assert (w, h) == (16, 8)
    # Pattern paint remains an 8-bit Cairo API, but its input transform must apply.
    surface.flush()
    samples = np.frombuffer(surface.get_data(), np.uint8).reshape(8, surface.get_stride() // 4, 4)
    assert bgra_to_working(samples, True)[4, 8, 0] == pytest.approx(128/255, abs=.006)


def test_sequence_transfer_alpha_and_frame_mix(tmp_path):
    Image.new("RGBA", (16, 8), (128, 0, 0, 128)).save(tmp_path / "f01.png")
    Image.new("RGBA", (16, 8), (0, 0, 255, 255)).save(tmp_path / "f02.png")
    asset = '<imageSequence id="img" src="f%02d.png" first="1" last="2" fps="2" width="16" height="8" colorSpace="linear-srgb" alpha="none"/>'
    r = scene(tmp_path, asset, '<layer id="L" asset="img" frameBlend="frame-mix"/>')
    np.testing.assert_allclose(r.frame_linear(.25)[4, 8], [64/255, 0, .5, 1], atol=1e-7)


@pytest.mark.parametrize("selector", ["Red", "Group/Red", "Group"])
def test_psd_layer_selection_preserves_canvas_offset_and_alpha(tmp_path, selector):
    from psd_tools import PSDImage
    p = PSDImage.new("RGB", (16, 8))
    p.create_pixel_layer(Image.new("RGB", (16, 8), (0, 0, 255)), name="Blue")
    red = p.create_pixel_layer(Image.new("RGBA", (4, 2), (128, 0, 0, 128)), name="Red", left=6, top=3)
    group = p.create_group(name="Group")
    red.move_to_group(group)
    group.visible = False
    p.save(tmp_path / "layers.psd")
    r = scene(tmp_path, image("layers.psd", f'layer="{selector}" colorSpace="linear-srgb"'))
    px = r.frame_linear(0)
    np.testing.assert_allclose(px[3:5, 6:10], np.broadcast_to([(128/255)**2, 0, 0, 128/255], (2, 4, 4)), atol=2e-7)
    assert np.count_nonzero(px[..., 3]) == 8


def exr_fixture(tmp_path):
    import OpenEXR
    shape = (2, 4)
    channel = lambda value: np.full(shape, value, np.float32)
    part = lambda name, channels: OpenEXR.Part({"name": name, "dataWindow": (np.array([6, 3], np.int32), np.array([9, 4], np.int32)),
        "displayWindow": (np.array([0, 0], np.int32), np.array([15, 7], np.int32))}, {k: channel(v) for k, v in channels.items()})
    a = part("beauty", {"R": 50000, "G": -.25, "B": 2, "A": .5})
    b = part("effects", {"glow.R": 3, "glow.G": 4, "glow.B": 5, "glow.A": 0, "depth.Z": 17})
    OpenEXR.File([a, b]).write(str(tmp_path / "layers.exr"))


@pytest.mark.parametrize("selector,expected", [("beauty", [50000, -.25, 2, .5]), ("0", [50000, -.25, 2, .5]),
    ("glow", [3, 4, 5, 0]), ("effects", [3, 4, 5, 0]), ("effects:glow", [3, 4, 5, 0]), ("1:glow", [3, 4, 5, 0]),
    ("effects:depth.Z", [17, 17, 17, 1])])
def test_exr_parts_channels_hdr_and_display_window(tmp_path, selector, expected):
    exr_fixture(tmp_path)
    r = scene(tmp_path, image("layers.exr", f'layer="{selector}" colorSpace="linear-srgb"'))
    px = r.frame_linear(0)
    np.testing.assert_allclose(px[3:5, 6:10], np.broadcast_to(expected, (2, 4, 4)), atol=1e-6)
    px[3:5, 6:10] = 0
    assert np.count_nonzero(px) == 0


def test_missing_exr_selection_is_reported_instead_of_substitution(tmp_path, caplog):
    exr_fixture(tmp_path)
    r = scene(tmp_path, image("layers.exr", 'layer="unknown" colorSpace="linear-srgb"'))
    assert not np.any(r.frame_linear(0))
    assert "unknown" in caplog.text and "not found" in caplog.text


@pytest.mark.parametrize("mode,alpha", [("black", 1), ("transparent", .5), ("hold", 1)])
@pytest.mark.parametrize("t", [.25, .75])
def test_sequence_missing_frames_participate_in_frame_mix(tmp_path, mode, alpha, t):
    Image.new("RGB", (16, 8), (255, 0, 0)).save(tmp_path / "f01.png")
    Image.new("RGB", (16, 8), (255, 0, 0)).save(tmp_path / "f03.png")
    asset = f'<imageSequence id="img" src="f%02d.png" first="1" last="3" fps="2" width="16" height="8" missingFrame="{mode}"/>'
    r = scene(tmp_path, asset, '<layer id="L" asset="img" frameBlend="frame-mix"/>')
    np.testing.assert_allclose(r.frame_linear(t)[4, 8], [1 if mode == "hold" else .5, 0, 0, alpha], atol=1e-7)


def test_sequence_missing_black_respects_crop(tmp_path):
    from scenerender.assets.image import render_sequence
    asset = '<imageSequence id="img" src="f##.png" first="1" last="2" fps="2" width="16" height="8" missingFrame="black"/>'
    r = scene(tmp_path, asset)
    b = render_sequence(r.rc, r.doc.ids["img"], np.eye(3), Ctx(0, 0), clip=(4, 2, 12, 6))
    assert np.count_nonzero(b.px[..., 3]) == 32


def test_generated_image_keeps_sixteen_bit_precision(tmp_path):
    import png
    ramp = np.arange(1024, dtype=np.uint16) + 30000
    path = tmp_path / "cache.png"
    with path.open("wb") as f:
        png.Writer(1024, 1, greyscale=True, bitdepth=16).write(f, ramp[None])
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    asset = f'<generated id="img" kind="image" provider="local" model="fixture" prompt="ramp" cache="cache.png" cacheSha256="{sha}"/>'
    r = scene(tmp_path, asset, w=1024, h=1)
    expected = ((ramp/65535 + .055) / 1.055)**2.4
    np.testing.assert_allclose(r.frame_linear(0)[0, :, 0], expected, atol=2e-7)


def test_input_primaries_are_converted_to_working_space(tmp_path):
    from scenerender.assets.image import render_image
    Image.new("RGB", (16, 8), (255, 0, 0)).save(tmp_path / "red.png")
    r = scene(tmp_path, image("red.png", 'colorSpace="display-p3" transfer="linear"'),
              before='<colorManagement workingSpace="acescg"/>')
    # Independent P3-D65 -> XYZ -> Bradford D60 -> AP1 reference, rounded to 6 places.
    expected = [.735798, .04718, .003564, 1.]
    b = render_image(r.rc, r.doc.ids["img"], np.eye(3), Ctx(0, 0))
    np.testing.assert_allclose(b.region((8, 4, 9, 5))[0, 0], expected, atol=3e-5)
