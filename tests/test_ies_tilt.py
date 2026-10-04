"""LM-63 burning-angle correction stays separate from peak-normalized distribution."""
import numpy as np
import pytest

from scenerender.three.loaders import load_ies


def profile(tmp_path, *, geometry=1, external=False, lamp_position='', tilt=None, version='IESNA:LM-63-2002'):
    records = tilt or f'{geometry}, 3\n0, 90, 180\n1, .5, .25\n'
    if external:
        (tmp_path / 'lamp tilt.tlt').write_text(records)
    p = tmp_path / 'light.ies'
    p.write_text(version + '\n' + (f'[LAMPPOSITION] {lamp_position}\n' if lamp_position else '')
                 + ('TILT=lamp tilt.tlt\n' if external else 'TILT=INCLUDE\n' + records)
                 + '1 -1 2 3 1 1 1 0 0 0\n.5 .8 10\n0 90 180\n0\n100 100 100\n')
    return p


NADIR = ((0, -1, 0), (1, 0, 0), (0, 0, -1))  # forward, right, up
SIDE = ((1, 0, 0), (0, 1, 0), (0, 0, -1))


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('geometry,nadir,side', [(1, 1., .5), (2, .5, .5), (3, .5, .25)])
def test_lamp_geometries_and_external_tilt(tmp_path, external, geometry, nadir, side):
    p = load_ies(str(profile(tmp_path, external=external, geometry=geometry)))
    assert p.max_candela == pytest.approx(100.)
    assert p.tilt_factor(*NADIR) == pytest.approx(nadir)
    assert p.tilt_factor(*SIDE) == pytest.approx(side)
    np.testing.assert_allclose(p.table(5, 8), 1.)


def test_lamp_position_overrides_geometry(tmp_path):
    p = load_ies(str(profile(tmp_path, geometry=2, lamp_position='0 90')))
    assert p.tilt_factor(*SIDE) == pytest.approx(.25)  # Lamp follows right, not up.
    p = load_ies(str(profile(tmp_path, geometry=3, lamp_position='90 90')))
    assert p.tilt_factor(*SIDE) == pytest.approx(.5)


def test_tilt_interpolation_and_90_degree_symmetry(tmp_path):
    p = load_ies(str(profile(tmp_path)))
    a = np.sqrt(.5)
    assert p.tilt_factor((a, -a, 0), (a, a, 0), (0, 0, -1)) == pytest.approx(.75)
    p = load_ies(str(profile(tmp_path, tilt='1 2\n0 90\n1 .5\n')))
    assert p.tilt_factor((0, 1, 0), (1, 0, 0), (0, 0, 1)) == pytest.approx(1.)


@pytest.mark.parametrize('version,expected', [('IESNA91', 80), ('legacy fixture', 80),
    ('IESNA:LM-63-1995', 100), ('IESNA:LM-63-2002', 100)])
def test_version_specific_ballast_lamp_factor(tmp_path, version, expected):
    p = load_ies(str(profile(tmp_path, version=version)))
    assert p.max_candela == pytest.approx(expected)


@pytest.mark.parametrize('records', ['1 3 0 90', '4 2 0 90 1 1', '1 2 90 0 1 1',
    '1 2 0 90 1 nan', '1 2.5 0 90 1 1'])
def test_malformed_tilt_is_rejected(tmp_path, records):
    with pytest.raises(ValueError):
        load_ies(str(profile(tmp_path, external=True, tilt=records)))


def test_animated_tilt_matches_explicit_intensity_control(tmp_path):
    from scenerender import gl
    from test_3d_render import doc, frame, FLOOR, WHITE
    if not gl.available():
        pytest.skip('no GL')
    p = profile(tmp_path, external=True)
    light = f'<light id="l" type="point" y="500" intensity="200" ies="{p}">' \
            '<animate property="pitch"><key time="0" value="-90"/><key time="1" value="0"/></animate></light>'
    r = doc(tmp_path, FLOOR.format(cs='castShadow="false"'), materials=WHITE, lights=light, w=120, h=72)
    for t, factor in ((0., 1.), (.5, .75), (1., .5), (.5, .75)):
        control = doc(tmp_path, FLOOR.format(cs='castShadow="false"'), materials=WHITE,
                      lights=f'<light id="l" type="point" y="500" intensity="{200*factor}"/>',
                      w=120, h=72, name='control.xml')
        expected = frame(control, t)
        assert expected[..., :3].max() > .01
        np.testing.assert_allclose(frame(r, t), expected, atol=2e-6)
