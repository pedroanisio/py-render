"""Pixel-level regressions for gradient coordinates, layout allocation and shutter sampling."""
import numpy as np
import pytest

from scenerender.render import Renderer


def render(tmp_path, name, body, paints="", project="", time=.5):
    path = tmp_path / (name + ".xml")
    path.write_text(f'''<scene version="1.1"><project width="100" height="100" fps="10" duration="3"
       linearLight="false" {project}/>{paints}<composition>{body}</composition></scene>''')
    return Renderer.open(str(path), strict=True).frame_rgba(time)


def gradient(tmp_path, name, kind, attributes="", midpoint=".5", animation=""):
    return render(tmp_path, name,
        '<shape id="s" shape="rect" width="100" height="100" fill="url(#g)"/>',
        f'''<paints><{kind} id="g" {attributes}><stop offset="0" color="#333333" midpoint="{midpoint}"/>
        <stop offset="1" color="#444444"/>{animation}</{kind}></paints>''')


def test_conic_user_units_cover_the_painted_box(tmp_path):
    obj = gradient(tmp_path, "object", "conicGradient", 'cx=".5" cy=".5"')
    user = gradient(tmp_path, "user", "conicGradient", 'units="user" cx="50" cy="50"')
    assert np.all(obj[..., 3] == 255) and np.all(user[..., 3] == 255)
    # Tessellation and dithering may differ at the rounding threshold, but both
    # coordinate systems describe the same angular colours across the whole box.
    assert np.abs(obj.astype(int) - user.astype(int)).max() <= 2


def test_conic_midpoint_changes_colour_distribution(tmp_path):
    early = gradient(tmp_path, "early", "conicGradient", midpoint=".1")
    late = gradient(tmp_path, "late", "conicGradient", midpoint=".9")
    assert early[..., :3].mean() > late[..., :3].mean() + 5


def test_gradient_dither_is_effective_and_deterministic(tmp_path):
    on = gradient(tmp_path, "on", "linearGradient", 'dither="true"')
    off = gradient(tmp_path, "off", "linearGradient", 'dither="false"')
    again = gradient(tmp_path, "again", "linearGradient", 'dither="true"')
    assert np.count_nonzero(on != off) > 100
    np.testing.assert_array_equal(on, again)
    assert abs(on[..., :3].mean() - off[..., :3].mean()) < .1


@pytest.mark.parametrize("attribute", ["fx", "fy"])
def test_radial_focal_animation_does_not_require_base_attribute(tmp_path, attribute):
    animation = f'<animate property="{attribute}"><key time="0" value=".2"/></animate>'
    a = gradient(tmp_path, "implicit", "radialGradient", animation=animation)
    b = gradient(tmp_path, "explicit", "radialGradient", f'{attribute}=".5"', animation=animation)
    np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize("layout,box", [("row", (20, 80)), ("column", (80, 20)),
                                      ("stack", (80, 80)), ("grid", (40, 80))])
def test_group_stretch_fills_allocated_cross_axis(tmp_path, layout, box):
    px = render(tmp_path, layout, f'''<group id="g" width="100" height="100" layout="{layout}" padding="10" alignItems="stretch">
      <shape id="s" shape="rect" width="20" height="20"/></group>''')
    w, h = box
    assert np.count_nonzero(px[..., 3]) == w * h
    assert px[10+h-1, 10+w-1, 3] == 255


def test_stretch_reflows_group_children(tmp_path):
    px = render(tmp_path, "group", '''<group id="g" width="100" height="100" layout="row" padding="10" alignItems="stretch">
      <group id="child" width="20" height="20"><shape id="s" shape="rect" width="100%" height="100%"/></group>
      <shape id="other" shape="rect" width="20" height="20"/></group>''')
    assert np.count_nonzero(px[..., 3]) == 40 * 80


@pytest.mark.parametrize("motion", [
    '<expression property="x">10+60*Math.sin((time-.95)/.1*Math.PI)</expression>',
    '<animate property="x"><key time=".95" value="10" interpolation="hold"/>'
    '<key time=".98" value="60" interpolation="hold"/><key time="1.02" value="10"/></animate>',
])
def test_adaptive_blur_preserves_motion_between_matching_endpoints(tmp_path, motion):
    body = f'<shape id="s" shape="rect" width="10" height="20" y="20">{motion}</shape>'
    project = 'motionBlur="true" motionBlurSamples="8" shutterAngle="360" shutterPhase="-180"'
    a = render(tmp_path, "adaptive", body, project=project + ' adaptiveMotionBlur="true"', time=1)
    b = render(tmp_path, "all", body, project=project + ' adaptiveMotionBlur="false"', time=1)
    np.testing.assert_array_equal(a, b)
    assert np.count_nonzero(a[..., 3]) > 200
