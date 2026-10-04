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


def test_observer_is_the_unmodified_cie_dataset():
    data = files('scenerender').joinpath('data/CIE_xyz_1931_2deg.csv').read_bytes()
    assert hashlib.sha256(data).hexdigest() == 'fa663e3535a7e0763a745993a1f0a192eb0275ac46ad2d1befd7626841e713c1'


KINDS = ['ambient', 'directional', 'point', 'spot', 'rect-area', 'disk-area', 'sphere-area', 'dome']
RGB = {1000: [4.5712677303, .0393574952, 0.], 40000: [.7011294374, .9831434314, 2.0470318483]}


def scene(tmp_path, kind, light_attrs='', children='', *, flat=False, name='scene.xml'):
    if flat:
        body = '<shape id="s" shape="rect" width="32" height="32" fill="#FFFFFF" effects="fx"/>'
    else:
        body = '<camera id="c" z="300"/>' \
               '<object3D id="o" primitive="plane" width="50" height="50" material="m"/>'
    path = tmp_path / name
    path.write_text(f'''<scene version="1.1">
      <project width="32" height="32" duration="2" fps="10"/>
      <materials><material id="m" baseColor="#FFFFFF" roughness="1"/></materials>
      <composition>{body}</composition>
      <lights><light id="l" type="{kind}" x="16" y="16" z="100" width="40" height="40"
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
