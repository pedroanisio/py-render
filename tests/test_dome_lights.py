"""Independent dome contributions, visible backgrounds and cache lifetimes."""
from dataclasses import replace

import numpy as np
import pytest

from scenerender import gl
from scenerender.three import renderer as gpu
from scenerender.three.lights import LightState
from test_3d_render import doc, frame, FLOOR, WHITE, MEDIA

pytestmark = pytest.mark.skipif(not gl.available(), reason='no GL')
ENV = f'{MEDIA}/env_sky.npy'
CAMERA = '<camera id="c" z="-500"/>'


def image(tmp_path, lights, name, *, body=None, material=WHITE):
    return frame(doc(tmp_path, body or FLOOR.format(cs=''), materials=material, lights=lights,
                     w=96, h=64, name=name))


@pytest.mark.parametrize('material,flags', [
    (WHITE, 'affectsSpecular="false"'),
    ('<material id="w" baseColor="#C0C0C0FF" metallic="1" roughness=".3"/>', 'affectsDiffuse="false"'),
    ('<material id="w" baseColor="#8090C0FF" roughness=".4" metallic=".4" clearcoat=".7" '
     'clearcoatRoughness=".3" sheenColor="#8030C0FF" sheenRoughness=".5" anisotropy=".3"/>', ''),
])
@pytest.mark.parametrize('other_shadow,yaw', [(False, 0), (True, 0), (True, 70)])
def test_each_dome_matches_its_independent_contribution(tmp_path, material, flags, other_shadow, yaw):
    attrs = f'type="dome" environment="{ENV}" {flags}'
    red = f'<light id="red" {attrs} color="#FF3010FF" castShadow="true" shadowMapSize="48" shadowBias=".002"/>'
    blue = (f'<light id="blue" {attrs} color="#1030FFFF" yaw="{yaw}" castShadow="{str(other_shadow).lower()}" '
            'shadowMapSize="96" shadowBias=".005" shadowSoftness="3"/>')
    expected = image(tmp_path, red, 'red.xml', material=material)[..., :3] + image(tmp_path, blue, 'blue.xml', material=material)[..., :3]
    assert expected.max() > .01
    for name, lights in [('both.xml', red+blue), ('reversed.xml', blue+red)]:
        np.testing.assert_allclose(image(tmp_path, lights, name, material=material)[..., :3], expected, atol=3e-6)


@pytest.mark.parametrize('kind', ['ambient', 'dome'])
def test_uniform_lighting_is_not_shadowed_by_another_dome(tmp_path, kind):
    red = f'<light id="red" type="dome" environment="{ENV}" color="#FF0000FF" castShadow="true" shadowMapSize="96"/>'
    blue = f'<light id="blue" type="{kind}" color="#0000FFFF" intensity=".5"/>'
    expected = image(tmp_path, red, 'red.xml')[..., :3] + image(tmp_path, blue, 'blue.xml')[..., :3]
    np.testing.assert_allclose(image(tmp_path, red+blue, 'both.xml')[..., :3], expected, atol=3e-6)


def test_specular_only_dome_casts_its_own_shadow(tmp_path):
    material = '<material id="w" baseColor="#C0C0C0FF" metallic="1" roughness=".4"/>'
    attrs = f'type="dome" environment="{ENV}" affectsDiffuse="false" shadowMapSize="128"'
    lit = image(tmp_path, f'<light id="l" {attrs} castShadow="false"/>', 'lit.xml', material=material)
    shaded = image(tmp_path, f'<light id="l" {attrs} castShadow="true"/>', 'shaded.xml', material=material)
    assert np.max(lit[..., :3]-shaded[..., :3]) > 1e-4
    assert shaded[..., :3].sum() < lit[..., :3].sum()


def test_explicit_dome_shadow_softness_is_consumed(tmp_path):
    attrs = f'type="dome" environment="{ENV}" castShadow="true" shadowMapSize="128"'
    hard = image(tmp_path, f'<light id="l" {attrs} shadowSoftness=".1"/>', 'hard.xml')
    soft = image(tmp_path, f'<light id="l" {attrs} shadowSoftness="25"/>', 'soft.xml')
    assert np.max(np.abs(hard-soft)) > 1e-4


@pytest.mark.parametrize('textured', [False, True])
def test_all_visible_domes_contribute_to_background(tmp_path, textured):
    environment = f'environment="{ENV}"' if textured else ''
    lights = [f'<light id="l{i}" type="dome" {environment} yaw="{i*40}" intensity="{.1*(i+1)}" '
              'color="#C08040FF" environmentVisible="true"/>' for i in range(5)]
    expected = sum(image(tmp_path, light, f'one{i}.xml', body=CAMERA)[..., :3] for i, light in enumerate(lights))
    actual = image(tmp_path, ''.join(lights), 'all.xml', body=CAMERA)
    np.testing.assert_allclose(actual[..., :3], expected, atol=3e-6)
    np.testing.assert_array_equal(actual[..., 3], 1.)


def test_multiple_visible_domes_preserve_transmission_and_alpha(tmp_path):
    material = '<material id="w" baseColor="#C0E0FFFF" transmission=".85" thickness=".25" roughness=".1"/>'
    body = CAMERA+'<object3D id="s" primitive="sphere" radius="35" material="w"/>'
    lights = ''.join(f'<light id="l{i}" type="dome" intensity="{.1*(i+1)}" environmentVisible="true"/>' for i in range(4))
    expected = image(tmp_path, '<light id="l" type="dome" intensity="1" environmentVisible="true"/>',
                     'control.xml', body=body, material=material)
    actual = image(tmp_path, lights, 'many.xml', body=body, material=material)
    np.testing.assert_allclose(actual, expected, atol=3e-6)


def test_transparent_instances_do_not_write_occluding_depth(tmp_path):
    material = '<material id="w" baseColor="#FF000080" alphaMode="blend" unlit="true"/>'
    # Front instance is submitted before the rear one. Both must contribute.
    # Scene space: origin top-left, +z away from the viewer; the 96x64 frame centre is (48, 32).
    body = '<camera id="c" x="48" y="32" z="-500"/>'+(
        '<object3D id="o" x="48" y="32" primitive="plane" width="40" height="40" material="w" instances="2">'
        '<expression property="z">10*index</expression></object3D>')
    actual = image(tmp_path, '<light id="l" type="ambient"/>', 'overlap.xml', body=body, material=material)
    alpha = 1-(1-128/255)**2
    np.testing.assert_allclose(actual[32, 48], [alpha, 0., 0., alpha], atol=2e-6)


def test_uniform_dome_records_cross_texture_rows(tmp_path):
    lights = ''.join(f'<light id="l{i}" type="dome" intensity=".01"/>' for i in range(97))
    expected = image(tmp_path, '<light id="l" type="dome" intensity=".97"/>', 'control.xml')
    np.testing.assert_allclose(image(tmp_path, lights, 'many.xml'), expected, atol=5e-6)


@pytest.mark.parametrize('textured', [False, True])
def test_animated_environment_visibility_and_backward_seeks(tmp_path, textured):
    environment = f'environment="{ENV}"' if textured else ''
    attrs = f'type="dome" {environment} color="#C08040FF"'
    light = f'<light id="l" {attrs}><animate property="environmentVisible">' \
            '<key time="0" value="false"/><key time="1" value="true"/><key time="2" value="false"/></animate></light>'
    scene = doc(tmp_path, CAMERA, lights=light, w=96, h=64)
    for t in [0, 1, 0, 2, 1]:
        expected = image(tmp_path, f'<light id="l" {attrs} environmentVisible="{str(t == 1).lower()}"/>', f'control{t}.xml', body=CAMERA)
        np.testing.assert_allclose(frame(scene, t), expected, atol=2e-6)


def test_animated_light_type_can_enable_environment_background(tmp_path):
    attrs = f'environment="{ENV}" environmentVisible="true"'
    light = f'<light id="l" type="point" {attrs}><animate property="type">' \
            '<key time="0" value="point"/><key time="1" value="dome"/><key time="2" value="ambient"/></animate></light>'
    scene = doc(tmp_path, CAMERA, lights=light, w=96, h=64)
    for t in [0, 1, 2, 1, 0]:
        kind = ['point', 'dome', 'ambient'][t]
        expected = image(tmp_path, f'<light id="l" type="{kind}" {attrs}/>', f'control{t}.xml', body=CAMERA)
        np.testing.assert_allclose(frame(scene, t), expected, atol=2e-6)


def test_animated_diffuse_specular_flags_reuse_radiance_without_stale_settings(tmp_path):
    attrs = f'type="dome" environment="{ENV}" castShadow="true" shadowMapSize="64"'
    values = {'affectsDiffuse': ['true', 'false', 'true'], 'affectsSpecular': ['true', 'true', 'false']}
    keys = ''.join(f'<animate property="{name}">'+''.join(f'<key time="{i}" value="{value}"/>' for i, value in enumerate(series))+'</animate>'
                   for name, series in values.items())
    scene = doc(tmp_path, FLOOR.format(cs=''), materials=WHITE, lights=f'<light id="l" {attrs}>{keys}</light>', w=96, h=64)
    for t in [0, 1, 2, 1, 0]:
        flags = ' '.join(f'{name}="{series[t]}"' for name, series in values.items())
        expected = image(tmp_path, f'<light id="l" {attrs} {flags}/>', f'control{t}.xml')
        np.testing.assert_allclose(frame(scene, t), expected, atol=2e-6)


def test_small_animated_intensity_change_is_not_rounded_out_of_cache(tmp_path):
    light = f'<light id="l" type="dome" environment="{ENV}"><animate property="intensity">' \
            '<key time="0" value="1"/><key time="1" value="1.0000002"/></animate></light>'
    scene = doc(tmp_path, FLOOR.format(cs=''), materials=WHITE, lights=light, w=96, h=64)
    initial, changed = frame(scene, 0), frame(scene, 1)
    expected = image(tmp_path, f'<light id="l" type="dome" environment="{ENV}" intensity="1.0000002"/>', 'control.xml')
    assert np.any(initial[..., :3] != changed[..., :3])
    np.testing.assert_array_equal(changed, expected)


def test_live_environment_survives_cache_eviction():
    r = gpu.res()
    source = np.ones((8, 16, 3), np.float32)
    light = LightState(None, 'dome', np.array([.2, .4, .8]), np.zeros(3), np.array([1., 0., 0.]),
                       np.array([0., 1., 0.]), np.array([0., 0., -1.]), env=source, visible=True)
    old = gpu.build_env(r, [light])
    try:
        before = old.atlas.read()
        background = old.visible[0][0].read()
        for i in range(10):
            other = gpu.build_env(r, [replace(light, color=np.array([1.+i, .3, .2]), visible=False)])
            other.release()
        assert len(r.environments) <= 8
        assert all(entry is not old.maps[0] for entry in r.environments.values())
        assert old.atlas.read() == before
        assert old.visible[0][0].read() == background
    finally:
        old.release()
