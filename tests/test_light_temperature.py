"""The complete schema Kelvin range reaches both light consumers."""
import hashlib
from importlib.resources import files

import numpy as np
import pytest

from scenerender import gl
from scenerender.colorimetry import blackbody_rgb, blackbody_xyz
from scenerender.raster import linear_to_srgb
from scenerender.render import Renderer


# Reference chromaticities from Planck radiation and the CIE 1931 1 nm table.
# These include both ends outside the former polynomial's 1667–25000 K domain.
@pytest.mark.parametrize('kelvin,xy', [
    (1000, (.6527506433, .3444617081)),
    (1500, (.5857179312, .3931213205)),
    (1667, (.5650440443, .4027416994)),
    (3200, (.4233596195, .3989516843)),
    (6504, (.3134636867, .3235679260)),
    (25000, (.2525209637, .2522214048)),
    (30000, (.2500706765, .2487926861)),
    (40000, (.2472034110, .2447212542)),
])
def test_planckian_chromaticity_reference(kelvin, xy):
    xyz = blackbody_xyz(kelvin)
    np.testing.assert_allclose(xyz[:2] / xyz.sum(), xy, atol=1e-9, rtol=0)
    rgb = blackbody_rgb(kelvin)
    assert np.isfinite(rgb).all() and (rgb >= 0).all()
    assert rgb @ [.2126, .7152, .0722] == pytest.approx(1.)


def kang_rgb(kelvin):
    """Upstream's former tint: the Kang et al. (2002) fit of the Planckian locus, defined for 1667-25000 K."""
    T = float(kelvin)
    if T <= 4000:
        x = -0.2661239e9 / T ** 3 - 0.2343589e6 / T ** 2 + 0.8776956e3 / T + 0.179910
    else:
        x = -3.0258469e9 / T ** 3 + 2.1070379e6 / T ** 2 + 0.2226347e3 / T + 0.240390
    if T <= 2222:
        y = -1.1063814 * x ** 3 - 1.34811020 * x ** 2 + 2.18555832 * x - 0.20219683
    elif T <= 4000:
        y = -0.9549476 * x ** 3 - 1.37418593 * x ** 2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x ** 3 - 5.87338670 * x ** 2 + 3.75112997 * x - 0.37001483
    xyz = np.array([x / y, 1.0, (1 - x - y) / y])
    rgb = np.maximum(np.array([[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560],
                               [0.0556434, -0.2040259, 1.0572252]]) @ xyz, 0.0)
    return rgb / (np.array([.2126, .7152, .0722]) @ rgb)


def test_cie_integration_agrees_with_the_kang_fit_where_both_are_defined():
    """The XSD's kelvinType is [1000, 40000] K, so the tint integrates the CIE observer over all of it. Over
    the 1667-25000 K range of the Kang polynomial fit (the previous clamp) the two differ by at most 0.0055
    in linear RGB at unit luminance (the fit's own error, largest near 2600 K)."""
    kelvin = np.arange(1667, 25001, 7.)
    difference = max(np.abs(kang_rgb(t) - blackbody_rgb(t)).max() for t in kelvin)
    assert difference < .006


def test_observer_is_the_unmodified_cie_dataset():
    data = files('scenerender').joinpath('data/CIE_xyz_1931_2deg.csv').read_bytes()
    assert hashlib.sha256(data).hexdigest() == 'fa663e3535a7e0763a745993a1f0a192eb0275ac46ad2d1befd7626841e713c1'


KINDS = ['ambient', 'directional', 'point', 'spot', 'rect-area', 'disk-area', 'sphere-area', 'dome']
RGB = {1000: [4.5712677303, .0393574952, 0.], 40000: [.7011294374, .9831434314, 2.0470318483]}


def scene(tmp_path, kind, light_attrs='', children='', *, flat=False, name='scene.xml'):
    if flat:
        body = '<shape id="s" shape="rect" width="32" height="32" fill="#FFFFFF" effects="fx"/>'
    else:
        body = '<camera id="c" x="16" y="16" z="-300"/>' \
               '<object3D id="o" primitive="plane" x="16" y="16" width="50" height="50" material="m"/>'
    path = tmp_path / name
    path.write_text(f'''<scene version="1.1">
      <project width="32" height="32" duration="2" fps="10"/>
      <materials><material id="m" baseColor="#FFFFFF" roughness="1"/></materials>
      <composition>{body}</composition>
      <lights><light id="l" type="{kind}" x="16" y="16" z="{100 if flat else -100}" width="40" height="40"
                     radius="20" affectsSpecular="false" {light_attrs}>{children}</light></lights>
      <effects><effect id="fx" type="lighting" lights="l"/></effects>
    </scene>''')
    return Renderer.open(str(path), strict=True)


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('flat', [False, True], ids=['3d', '2d-effect'])
def test_kelvin_endpoints_and_animation_match_explicit_coloured_lights(tmp_path, kind, flat):
    if not flat and not gl.available():
        pytest.skip('no GL')
    animated = scene(tmp_path, kind, children='<animate property="colorTemperature">'
                     '<key time="0" value="1000"/><key time="1" value="40000"/></animate>', flat=flat)
    controls = {}
    for kelvin, value in RGB.items():
        rgb = np.array(value)
        peak = float(rgb.max())
        encoded = linear_to_srgb(rgb / peak)
        colour = ','.join(f'{v:.12f}' for v in encoded)
        # The 2D illumination model uses a unit-peak tint; 3D uses unit luminance.
        attrs = f'color="{colour}" intensity="{1 if flat else peak:.12f}"'
        control = scene(tmp_path, kind, attrs, flat=flat, name=f'control-{kelvin}.xml')
        controls[kelvin] = control.rc.render_frame(0).px.copy()
        assert controls[kelvin][..., :3].max() > .01
    assert np.max(np.abs(controls[1000] - controls[40000])) > .01
    for t in [0, 1, 0, 1]:
        np.testing.assert_allclose(animated.rc.render_frame(t).px,
                                   controls[1000 if t == 0 else 40000], atol=3e-6)
