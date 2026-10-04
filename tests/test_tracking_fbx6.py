"""Legacy tracking import, checked against authored curves and rendered controls."""
import gc
import hashlib
import math
from pathlib import Path
import subprocess
import struct
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from scenerender import tracking

sys.path.insert(0, str(Path(__file__).parent/'fixtures'/'tracking'))
import make_fbx6 as fbx


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
@pytest.mark.parametrize('version', [6000, 6100])
def test_linear_defaults_and_unordered_seeks(binary, version):
    data = fbx.scene(binary=binary, version=version)
    assert tracking.sniff_format(data, 'unknown.bin') == 'fbx'
    channels = tracking.parse_fbx(data)
    assert list(channels) == ['Tracker']
    channel = channels['Tracker']
    np.testing.assert_array_equal(channel.times, [0, 1])
    for time in [.25, 1, -.5, .333333333, 5, 0, .6]:
        got = channel.sample(time)
        assert got == pytest.approx(dict(x=10+20*min(max(time, 0), 1), y=20, z=0,
                                        rx=0, ry=0, rz=45, scaleX=2, scaleY=3))


def bezier(time, right, left):
    """Independent polynomial root solution for (0,0) -> (1,10), slopes 30/-5."""
    x = [0., right, 1-left, 1.]
    roots = np.roots([-x[0]+3*x[1]-3*x[2]+x[3], 3*x[0]-6*x[1]+3*x[2],
                      -3*x[0]+3*x[1], x[0]-time])
    s = next(float(root.real) for root in roots if abs(root.imag) < 1e-8 and 0 <= root.real <= 1)
    return 3*(1-s)**2*s*30*right+3*(1-s)*s*s*(10+5*left)+s**3*10


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
@pytest.mark.parametrize('mode,right,left', [('n', 1/3, 1/3), ('a', .5, .2), ('r', .5, 1/3), ('l', 1/3, .2)])
def test_cubic_interpolation_at_unbaked_times(binary, mode, right, left):
    weights = {'n': (), 'a': (right, left), 'r': (right,), 'l': (left,)}[mode]
    keys = [(0, 0, 'U', 's', 30., -5., mode, *weights), (1, 10, 'L')]
    data = fbx.scene(binary=binary, takes=[fbx.take(channels=[('Tracker', [
        fbx.transform(translation=[fbx.curve('X', keys)])])])])
    channel = tracking.parse_fbx(data)['Tracker']
    for time in [.127839, .5, .777, .001, .98]:
        assert channel.sample(time)['x'] == pytest.approx(bezier(time, right, left), abs=8e-6)


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
def test_constant_previous_next_and_key_values(binary):
    keys = [(0, 5, 'C', 's'), (1, 7, 'C', 'n'), (2, 9, 'L')]
    data = fbx.scene(binary=binary, takes=[fbx.take(channels=[('Tracker', [
        fbx.transform(translation=[fbx.curve('X', keys)])])])])
    channel = tracking.parse_fbx(data)['Tracker']
    assert [channel.sample(t)['x'] for t in [-1, 0, .5, 1, 1.1, 2, 3]] == [5, 5, 5, 7, 9, 9, 9]


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
def test_first_take_raw_units_local_transforms_and_sparse_keys(binary):
    model = fbx.model('Trackér', properties=[fbx.prop('Lcl Translation', 'Lcl Translation', 10., 20., 30.)])
    parent = fbx.model('Parent', properties=[fbx.prop('Lcl Translation', 'Lcl Translation', 100., 200., 300.)])
    first = fbx.take('First', start=2, end=4, channels=[('Trackér', [fbx.transform(translation=[
        fbx.curve('X', [(2, 10, 'L'), (4, 30, 'L')]),
        fbx.curve('Y', [(2.5, 40, 'L'), (3.5, 80, 'L')]), fbx.curve('Z', default=7.)],
        scaling=[fbx.curve('X', [(2, .5, 'L'), (4, 1.5, 'L')]),
                 fbx.curve('Y', default=3.), fbx.curve('Z', default=1.)])])])
    other = fbx.take('CurrentButSecond', channels=[('Trackér', [fbx.transform(translation=[
        fbx.curve('X', [(0, 500, 'L'), (1, 600, 'L')])])])])
    channel = tracking.parse_fbx(fbx.scene(models=[parent, model], takes=[first, other],
        parents={'Trackér': 'Parent'}, binary=binary))
    assert list(channel) == ['Trackér']
    channel = channel['Trackér']
    np.testing.assert_array_equal(channel.times, [2, 2.5, 3.5, 4])
    value = channel.sample(3)
    assert (value['x'], value['y'], value['z'], value['scaleX'], value['scaleY']) == (20, 60, 7, 1, 3)


def rotation(axis, degrees):
    angle = math.radians(degrees)
    c, s = math.cos(angle), math.sin(angle)
    if axis == 'X':
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == 'Y':
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
@pytest.mark.parametrize('order', ['XYZ', 'XZY', 'YZX', 'YXZ', 'ZXY', 'ZYX'])
def test_rotation_orders_pre_post_and_animation(binary, order):
    index = ['XYZ', 'XZY', 'YZX', 'YXZ', 'ZXY', 'ZYX'].index(order)
    model = fbx.model(properties=[fbx.prop('RotationActive', 'bool', 1), fbx.prop('RotationOrder', 'enum', index),
        fbx.prop('PreRotation', 'Vector3D', 0., 0., 30.), fbx.prop('PostRotation', 'Vector3D', 10., 0., 0.)])
    take = fbx.take(channels=[('Tracker', [fbx.transform(rotation=[
        fbx.curve('X', [(0, 20, 'L'), (1, 60, 'L')]),
        fbx.curve('Y', default=35.), fbx.curve('Z', default=-20.)])])])
    channel = tracking.parse_fbx(fbx.scene(models=[model], takes=[take], binary=binary))['Tracker']
    for time in (.17, .5, 1, 0):
        authored = dict(X=20+40*time, Y=35, Z=-20)
        local = np.eye(3)
        for axis in order:
            local = rotation(axis, authored[axis])@local
        expected = rotation('Z', 30)@local@rotation('X', -10)
        got = channel.sample(time)
        actual = rotation('Z', got['rz'])@rotation('Y', got['ry'])@rotation('X', got['rx'])
        np.testing.assert_allclose(actual, expected, atol=1e-8)


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
@pytest.mark.parametrize('mode', [1, 2, 3])
def test_camera_only_animation_and_fractional_optics(binary, mode):
    model = fbx.model('Camera', kind='Camera', properties=[
        fbx.prop('FocalLength', 'Number', 35.25), fbx.prop('FieldOfView', 'FieldOfView', 60.5),
        fbx.prop('ApertureMode', 'enum', mode), fbx.prop('FilmWidth', 'Number', 1.5),
        fbx.prop('FilmHeight', 'Number', .75), fbx.prop('Lcl Translation', 'Lcl Translation', 1., 2., 3.)])
    take = fbx.take(channels=[('Camera', [
        fbx.curve('FocalLength', [(0, 35.25, 'L'), (1, 70.75, 'L')]),
        fbx.curve('FieldOfView', [(0, 60.5, 'L'), (1, 90.25, 'L')])])])
    channel = tracking.parse_fbx(fbx.scene(models=[model], takes=[take], binary=binary))['Camera']
    for time in (0, .25, .5, 1):
        sample = channel.sample(time)
        fov = 60.5+(90.25-60.5)*time
        if mode == 1:
            fov = math.degrees(2*math.atan(math.tan(math.radians(fov)/2)*.75/1.5))
        assert sample['focal'] == pytest.approx(35.25+35.5*time)
        assert sample['fov'] == pytest.approx(fov)
        assert (sample['x'], sample['y'], sample['z']) == (1, 2, 3)


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
@pytest.mark.parametrize('version', [4002, 4003, 4004, 4005])
def test_legacy_key_versions(binary, version):
    attributes = ('U', 's', 30., -5.)+(() if version == 4003 else ('n',))
    keys = [(0, 0, *attributes), (1, 10, 'L')]
    take = fbx.take(channels=[('Tracker', [fbx.transform(translation=[fbx.curve('X', keys, version=version)])])])
    channel = tracking.parse_fbx(fbx.scene(takes=[take], binary=binary))['Tracker']
    assert channel.sample(.5)['x'] == pytest.approx(bezier(.5, 1/3, 1/3), abs=8e-6)


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
@pytest.mark.parametrize('version', [4002, 4003])
def test_old_constant_encoding_and_mixed_curve_versions(binary, version):
    old = [(0, 5, 'C'), (1, 10, 'L')]
    new = [(0, 10, 'U', 's', 20., 20., 'a', .5, .25), (1, 30, 'L')]
    take = fbx.take(channels=[('Tracker', [fbx.transform(translation=[
        fbx.curve('X', old, version=version), fbx.curve('Y', new), fbx.curve('Z', default=7.)])])])
    channel = tracking.parse_fbx(fbx.scene(takes=[take], binary=binary))['Tracker']
    for time in (.01, .2, .7, .99):
        got = channel.sample(time)
        assert (got['x'], got['y'], got['z']) == pytest.approx((5, 10+20*time, 7), abs=3e-6)
    assert channel.sample(1)['x'] == 10


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
def test_compatibility_preserves_camera_properties_and_names(binary):
    model = fbx.model('Caméra', kind='Camera', properties=[
        fbx.prop('FocalLength', 'Number', 35.25), fbx.prop('FieldOfView', 'Number', 60.5)])
    take = fbx.take(channels=[('Caméra', [fbx.curve('FocalLength', [
        (0, 35.25, 'U', 's', 30., -5.), (1, 45.25, 'L')], version=4003)])])
    channel = tracking.parse_fbx(fbx.scene(models=[model], takes=[take], binary=binary))['Caméra']
    got = channel.sample(.5)
    assert got['focal'] == pytest.approx(35.25+bezier(.5, 1/3, 1/3), abs=8e-6)
    assert got['fov'] == 60.5


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'latin-1'])
def test_text_encoding(encoding):
    text = fbx.scene().decode().replace('Tracker', 'Trackér')
    channel = tracking.parse_fbx(text.encode(encoding))['Trackér']
    assert channel.sample(.5)['x'] == 20


def test_rotation_inactive_ignores_pre_post_and_order():
    model = fbx.model(properties=[fbx.prop('RotationActive', 'bool', 0), fbx.prop('RotationOrder', 'enum', 4),
        fbx.prop('PreRotation', 'Vector3D', 0., 0., 30.), fbx.prop('PostRotation', 'Vector3D', 10., 0., 0.)])
    take = fbx.take(channels=[('Tracker', [fbx.transform(rotation=[
        fbx.curve('X', [(0, 20, 'L'), (1, 60, 'L')]), fbx.curve('Y', default=35.), fbx.curve('Z', default=-20.)])])])
    sample = tracking.parse_fbx(fbx.scene(models=[model], takes=[take]))['Tracker'].sample(.5)
    assert (sample['rx'], sample['ry'], sample['rz']) == (40, 35, -20)


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
def test_empty_and_corrupt_takes_fail_and_next_load_recovers(binary):
    with pytest.raises(tracking.TrackError, match='no animation Takes'):
        tracking.parse_fbx(fbx.scene(binary=binary, takes=[]))
    with pytest.raises(tracking.TrackError, match='no animated models'):
        tracking.parse_fbx(fbx.scene(binary=binary, takes=[fbx.take()]))
    with pytest.raises(tracking.TrackError, match='fbx:'):
        tracking.parse_fbx(fbx.scene(binary=binary)[:150])
    assert tracking.parse_fbx(fbx.scene(binary=binary))['Tracker'].sample(.5)['x'] == 20


def test_missing_native_dependency_does_not_break_fbx7(monkeypatch):
    monkeypatch.setitem(sys.modules, 'ufbx', None)
    with pytest.raises(tracking.TrackError, match=r'install scenerender\[3d\]'):
        tracking.parse_fbx(fbx.scene())
    path = Path(__file__).parent/'fixtures'/'tracking'/'sample_ascii.fbx'
    assert 'Tracker_Null' in tracking.parse_fbx(path.read_bytes())


@pytest.mark.parametrize('field,value', [(0, 27), (0, 999999), (8, 999999), (4, 999999)])
def test_corrupt_binary_offsets_are_rejected(field, value):
    from scenerender.tracking_fbx import _keyver_compat
    data = bytearray(fbx.scene(binary=True))
    struct.pack_into('<I', data, 27+field, value)
    # A backward offset must not hang the parser or a compatibility retry.
    # The native reader itself tolerates some malformed header metadata.
    with pytest.raises(tracking.TrackError, match='fbx:'):
        tracking._fbx_binary(bytes(data))
    with pytest.raises(tracking.TrackError, match='fbx:'):
        _keyver_compat(bytes(data))


def test_bounded_evaluation_cache_and_retained_channel_lifetime():
    from scenerender.tracking_fbx import LegacySampler
    sampler = LegacySampler(fbx.scene())
    retained = len(sampler.pool)
    for time in np.linspace(0, 1, 50):
        values = sampler.sample(time)
        assert next(iter(values.values()))['x'] == pytest.approx(10+20*time)
    assert len(sampler.pool) == retained
    assert len(sampler.samples) <= 8
    leaf = tracking.parse_fbx(fbx.scene())['Tracker']
    del sampler
    gc.collect()
    assert leaf.sample(.21387)['x'] == pytest.approx(14.2774)


def test_native_lifetime_under_repeated_loads_and_gc():
    # A subprocess makes the known native-binding lifetime failure an explicit
    # test failure instead of terminating the entire regression suite.
    code = '''
import gc, sys
sys.path.insert(0, 'tests/fixtures/tracking')
import make_fbx6 as fbx
from scenerender.tracking import parse_fbx
for iteration in range(30):
    channels = [parse_fbx(fbx.scene(binary=bool(i%2)))['Tracker'] for i in range(4)]
    for frame in range(12):
        gc.collect()
        for channel in reversed(channels):
            time = ((frame*7)%13)/13
            assert abs(channel.sample(time)['x']-(10+20*time)) < 1e-10
    del channels
    gc.collect()
print('completed')
'''
    result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', code],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    assert result.stdout.strip() == 'completed'


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
def test_load_track_clock_hash_sniff_and_source_invalidation(tmp_path, binary, caplog):
    from scenerender.document import load
    path = tmp_path/'data.bin'
    path.write_bytes(fbx.scene(binary=binary))
    scene = tmp_path/'scene.xml'
    scene.write_text('''<scene version="1.1"><project width="64" height="48" fps="24" duration="3"/>
      <tracking><trackData id="track" src="data.bin" kind="point" timeOffset="1"/></tracking>
      <composition/></scene>''')
    doc = load(str(scene), strict=True)
    rc = SimpleNamespace(doc=doc, cache={})
    td = doc.ids['track']
    td.set('sha256', hashlib.sha256(path.read_bytes()).hexdigest())
    track = tracking.load_track(rc, td)
    assert track is tracking.load_track(rc, td)
    assert track.format == 'fbx'
    assert track.sample(None, 1.25)['x'] == 15
    td.attrib.pop('sha256')
    replacement = fbx.scene(binary=binary, takes=[fbx.take(channels=[('Tracker', [
        fbx.transform(translation=[fbx.curve('X', [(0, 100, 'L'), (1, 200, 'L')])])])])])
    path.write_bytes(replacement)
    replaced = tracking.load_track(rc, td)
    assert replaced is not track
    assert replaced.sample(None, 1.25)['x'] == 125
    assert track.sample(None, 1.25)['x'] == 15
    td.set('sha256', '0'*64)
    assert tracking.load_track(rc, td) is None
    assert 'does not match sha256' in caplog.text


@pytest.mark.parametrize('binary', [False, True], ids=['ascii', 'binary'])
def test_tracking_constraint_render_matches_authored_motion(tmp_path, binary):
    from scenerender.render import Renderer
    (tmp_path/'motion.fbx').write_bytes(fbx.scene(binary=binary))

    def renderer(name, body, tracking=''):
        path = tmp_path/name
        path.write_text(f'''<scene version="1.1"><project width="100" height="100" fps="24" duration="3"
          linearLight="false"/>{tracking}<composition>{body}</composition></scene>''')
        return Renderer.open(str(path), strict=True)

    source = renderer('track.xml', '''<shape id="s" shape="rect" width="8" height="5" fill="#CC3300">
      <transformConstraint type="track" target="td" point="Tracker"/></shape>''',
      '<tracking><trackData id="td" src="motion.fbx" format="fbx" kind="point" timeOffset="1"/></tracking>')
    for time in (1.25, 2., 1.127839, 0., 1.75):
        x = 10+20*min(max(time-1, 0), 1)
        control = renderer('control.xml', f'''<shape id="s" shape="rect" width="8" height="5" fill="#CC3300"
          x="{x}" y="20" rotation="45" scaleX="2" scaleY="3"/>''')
        expected = control.frame_rgba(time)
        assert np.count_nonzero(expected[..., 3]) > 50
        np.testing.assert_array_equal(source.frame_rgba(time), expected)
