"""Animation pointers follow typed property paths without mutating shared assets."""
import copy
import math

import numpy as np
import pygltflib as gt
import pytest

from scenerender.three.loaders import load_model
from test_3d_loaders import Builder
from test_gltf_material_textures import plane


FACTORS = [
    ('alphaCutoff', 'alphaCutoff', 1), ('emissiveFactor', 'emissive', 3),
    ('normalTexture/scale', 'normalScale', 1), ('occlusionTexture/strength', 'occlusionStrength', 1),
    ('pbrMetallicRoughness/baseColorFactor', 'baseColor', 4),
    ('pbrMetallicRoughness/metallicFactor', 'metallic', 1),
    ('pbrMetallicRoughness/roughnessFactor', 'roughness', 1),
]
for extension, fields in [
    ('clearcoat', [('clearcoatFactor', 'clearcoat', 1), ('clearcoatRoughnessFactor', 'clearcoatRoughness', 1),
                   ('clearcoatNormalTexture/scale', 'clearcoatNormalScale', 1)]),
    ('transmission', [('transmissionFactor', 'transmission', 1)]),
    ('ior', [('ior', 'ior', 1)]),
    ('volume', [('thicknessFactor', 'thickness', 1), ('attenuationDistance', 'attenuationDistance', 1),
                ('attenuationColor', 'attenuationColor', 3)]),
    ('sheen', [('sheenColorFactor', 'sheenColor', 3), ('sheenRoughnessFactor', 'sheenRoughness', 1)]),
    ('specular', [('specularFactor', 'specular', 1), ('specularColorFactor', 'specularColor', 3)]),
    ('iridescence', [('iridescenceFactor', 'iridescence', 1), ('iridescenceIor', 'iridescenceIor', 1),
                     ('iridescenceThicknessMinimum', 'iridescenceThicknessMinimum', 1),
                     ('iridescenceThicknessMaximum', 'iridescenceThicknessMaximum', 1)]),
    ('anisotropy', [('anisotropyStrength', 'anisotropy', 1), ('anisotropyRotation', 'anisotropyRotation', 1)]),
    ('dispersion', [('dispersion', 'dispersion', 1)]),
    ('emissive_strength', [('emissiveStrength', 'emissiveStrength', 1)]),
]:
    FACTORS += [('extensions/KHR_materials_'+extension+'/'+source, key, width) for source, key, width in fields]

TEXTURES = [
    ('pbrMetallicRoughness/baseColorTexture', 'baseColorMap'),
    ('pbrMetallicRoughness/metallicRoughnessTexture', 'metallicRoughnessMap'),
    ('normalTexture', 'normalMap'), ('occlusionTexture', 'occlusionMap'), ('emissiveTexture', 'emissiveMap'),
]
for extension, fields in [
    ('clearcoat', [('clearcoatTexture', 'clearcoatMap'), ('clearcoatRoughnessTexture', 'clearcoatRoughnessMap'),
                   ('clearcoatNormalTexture', 'clearcoatNormalMap')]),
    ('transmission', [('transmissionTexture', 'transmissionMap')]),
    ('volume', [('thicknessTexture', 'thicknessMap')]),
    ('sheen', [('sheenColorTexture', 'sheenColorMap'), ('sheenRoughnessTexture', 'sheenRoughnessMap')]),
    ('specular', [('specularTexture', 'specularMap'), ('specularColorTexture', 'specularColorMap')]),
    ('iridescence', [('iridescenceTexture', 'iridescenceMap'), ('iridescenceThicknessTexture', 'iridescenceThicknessMap')]),
    ('anisotropy', [('anisotropyTexture', 'anisotropyMap')]),
]:
    TEXTURES += [('extensions/KHR_materials_'+extension+'/'+source, key) for source, key in fields]


def enclosing_material(builder, path):
    raw = {'pbrMetallicRoughness': {}}
    parent = raw
    for part in path.split('/')[:-1]:
        parent = parent.setdefault(part, {})
        if part.endswith('Texture'):
            parent['index'] = builder.image_png(np.array([[[230, 190, 80, 255]]], np.uint8))
    builder.g.materials = [gt.Material.from_dict(raw)]
    plane(builder)
    return raw


def channel(builder, pointer, values, type_='SCALAR', interpolation='LINEAR', times=(1., 3.),
            animation=None, ctype=gt.FLOAT, normalized=False):
    if animation is None:
        animation = gt.Animation(name='clip')
        builder.g.animations.append(animation)
    sampler = len(animation.samplers)
    animation.samplers.append(gt.AnimationSampler(input=builder.add(times, 'SCALAR'),
        output=builder.add(values, type_, ctype, normalized=normalized), interpolation=interpolation))
    animation.channels.append(gt.AnimationChannel(sampler=sampler,
        target=gt.AnimationChannelTarget(path='pointer', extensions={'KHR_animation_pointer': {'pointer': pointer}})))
    return animation


def model(builder, tmp_path, name='pointer'):
    return load_model(builder.save(tmp_path/(name+'.gltf')))


@pytest.mark.parametrize('source,key,width', FACTORS, ids=[row[0] for row in FACTORS])
def test_all_material_factor_pointer_paths(tmp_path, source, key, width):
    b = Builder()
    enclosing_material(b, source)
    values = np.array([[.3]*width, [.9]*width])
    if key in ('ior', 'iridescenceIor'):
        values += 1.
    channel(b, '/materials/0/'+source, values, 'SCALAR' if width == 1 else 'VEC'+str(width))
    m = model(b, tmp_path)
    base = m.pose(None, 0)[0].material
    original = copy.deepcopy(base.params)
    for time in (3., 1.5, 0., 8., 1.):
        sample = m.pose(m.clip('clip'), time)[0].material
        expected = values[0]+(values[1]-values[0])*np.clip((time-1)/2, 0, 1)
        if key == 'anisotropyRotation':
            expected *= 180/math.pi
        if width == 3:
            expected = np.r_[expected, 1.]
        np.testing.assert_allclose(sample.params[key], expected if width > 1 else expected[0], atol=2e-7)
        assert sample.dynamic
    assert base.params == original
    assert m.clip('clip').duration == 3.


@pytest.mark.parametrize('source,key', TEXTURES, ids=[row[0] for row in TEXTURES])
@pytest.mark.parametrize('field', ['offset', 'rotation', 'scale'])
def test_every_texture_transform_pointer_preserves_image_identity(tmp_path, source, key, field):
    path = source+'/extensions/KHR_texture_transform/'+field
    b = Builder()
    enclosing_material(b, path)
    values = [[.2], [.8]] if field == 'rotation' else [[.2, .3], [.8, .9]]
    channel(b, '/materials/0/'+path, values, 'SCALAR' if field == 'rotation' else 'VEC2')
    m = model(b, tmp_path)
    base = m.pose(None, 0)[0].material
    retained = {}
    for time in (2., 3., 1., 2.):
        item = m.pose(m.clip('clip'), time)[0]
        sample = item.material
        expected = np.asarray(values[0])+(np.asarray(values[1])-values[0])*(time-1)/2
        np.testing.assert_allclose(sample.params['mapUV'][key][field], expected[0] if field == 'rotation' else expected, atol=1e-7)
        assert sample.textures[key] is base.textures[key]
        assert not item.static
        if time in retained:
            assert sample is retained[time]
        retained[time] = sample
    for time in range(30):
        m.pose(m.clip('clip'), time/10)
    sampler = next(iter(m.clip('clip').material_samplers.values()))
    assert len(sampler.samples) == 8
    assert m.pose(m.clip('clip'), 2)[0].material is not retained[2.]


@pytest.mark.parametrize('interpolation', ['STEP', 'LINEAR', 'CUBICSPLINE'])
def test_pointer_interpolation_and_normalized_integer_conversion(tmp_path, interpolation):
    b = Builder(); enclosing_material(b, 'emissiveFactor')
    values = [[0, 32, 64], [255, 128, 192]]
    if interpolation == 'CUBICSPLINE':
        values = [[0, 0, 0], values[0], [64, 32, 16], [32, 64, 96], values[1], [0, 0, 0]]
    channel(b, '/materials/0/emissiveFactor', values, 'VEC3', interpolation, ctype=gt.UNSIGNED_BYTE, normalized=True)
    m = model(b, tmp_path)
    actual = m.pose(m.clip('clip'), 1.5)[0].material.params['emissive'][:3]
    v = np.asarray(values)/255
    if interpolation == 'STEP':
        expected = v[0]
    elif interpolation == 'LINEAR':
        expected = v[0]*.75+v[1]*.25
    else:
        u = .25
        expected = (2*u**3-3*u**2+1)*v[1]+(u**3-2*u**2+u)*2*v[2]+(-2*u**3+3*u**2)*v[4]+(u**3-u**2)*2*v[3]
    np.testing.assert_allclose(actual, expected, atol=1e-7)


def test_matrix_node_translation_pointer_preserves_shear(tmp_path):
    b = Builder(); enclosing_material(b, 'alphaCutoff')
    matrix = np.eye(4); matrix[0, 1] = .7; matrix[:3, 3] = [2, 3, 4]
    b.g.nodes[0].matrix = matrix.T.ravel().tolist()
    channel(b, '/nodes/0/translation', [[10, 20, 30], [20, 40, 60]], 'VEC3')
    m = model(b, tmp_path)
    original = m.pose(None, 0)[0].positions
    for time in (2., 3., 1., 2.):
        item = m.pose(m.clip('clip'), time)[0]
        expected = original+np.array([10, 20, 30])*(1+(time-1)/2)-[2, 3, 4]
        np.testing.assert_allclose(item.positions, expected, atol=8e-6)
        assert not item.static
    np.testing.assert_array_equal(m.nodes[0].matrix, matrix)


def test_individual_morph_weight_pointers_preserve_other_defaults(tmp_path):
    b = Builder(); enclosing_material(b, 'alphaCutoff')
    dx = b.add([[10, 0, 0]]*4, 'VEC3'); dy = b.add([[0, 20, 0]]*4, 'VEC3')
    b.g.meshes[0].primitives[0].targets = [{'POSITION': dx}, {'POSITION': dy}]
    b.g.meshes[0].weights = [.1, .3]
    channel(b, '/nodes/0/weights/0', [.2, .8])
    m = model(b, tmp_path)
    original = m.pose(None, 0)[0].positions
    for time in (3., 1., 2.):
        item = m.pose(m.clip('clip'), time)[0]
        np.testing.assert_allclose(item.positions, original+[10*(.2+.6*(time-1)/2-.1), 0, 0], atol=6e-6)
    overridden = m.pose(m.clip('clip'), 3, [0, 0])[0]
    np.testing.assert_allclose(overridden.positions, original-[1, 6, 0], atol=6e-6)


def test_clips_variants_and_derived_point_materials_remain_isolated(tmp_path):
    b = Builder(); enclosing_material(b, 'emissiveFactor')
    b.g.materials.append(gt.Material(pbrMetallicRoughness=gt.PbrMetallicRoughness(baseColorFactor=[.2, .3, .4, 1])))
    b.g.extensions = {'KHR_materials_variants': {'variants': [{'name': 'other'}]}}
    p = b.g.meshes[0].primitives[0]
    p.extensions = {'KHR_materials_variants': {'mappings': [{'material': 1, 'variants': [0]}]}}
    channel(b, '/materials/0/pbrMetallicRoughness/baseColorFactor', [[1, 0, 0, 1], [0, 1, 0, 1]], 'VEC4')
    channel(b, '/materials/1/pbrMetallicRoughness/baseColorFactor', [[0, 0, 1, 1], [1, 1, 0, 1]], 'VEC4',
            animation=b.g.animations[0])
    second = channel(b, '/materials/0/pbrMetallicRoughness/baseColorFactor', [[0, 0, 0, 1], [1, 1, 1, 1]], 'VEC4')
    second.name = 'second'
    p.mode, p.indices, p.attributes.NORMAL, p.attributes.TANGENT = 0, None, None, None
    m = model(b, tmp_path)
    np.testing.assert_allclose(m.pose(m.clip('clip'), 2)[0].material.params['baseColor'], [.5, .5, 0, 1])
    assert m.pose(m.clip('clip'), 2)[0].material.params['unlit']
    np.testing.assert_allclose(m.pose(m.clip('second'), 2)[0].material.params['baseColor'], [.5, .5, .5, 1])
    np.testing.assert_allclose(m.pose(m.clip('clip'), 2, variant='other')[0].material.params['baseColor'], [.5, .5, .5, 1])
    assert m.pose(m.clip('clip'), 2, variant='other')[0].material.params['unlit']
    for time in range(30):
        m.pose(m.clip('clip'), time/10)
    assert all(len(sampler.samples) <= 8 for sampler in m.clip('clip').material_samplers.values())


@pytest.mark.parametrize('pointer', ['/materials/0/doubleSided', '/materials/0/pbrMetallicRoughness/baseColorFactor/0',
    '/materials/0/extensions/KHR_materials_ior/ior', '/materials/01/alphaCutoff', '/materials/0/unknown~2field',
    '/nodes/0/weights/0', '/nodes/0/matrix'])
def test_invalid_or_undefined_pointer_is_reported(tmp_path, pointer):
    b = Builder(); enclosing_material(b, 'alphaCutoff')
    channel(b, pointer, [.2, .8])
    with pytest.raises(ValueError, match='glTF'):
        model(b, tmp_path)


def test_overlapping_core_and_pointer_channels_are_rejected(tmp_path):
    b = Builder(); enclosing_material(b, 'alphaCutoff')
    animation = channel(b, '/nodes/0/translation', [[0, 0, 0], [1, 1, 1]], 'VEC3')
    animation.channels.append(gt.AnimationChannel(sampler=0, target=gt.AnimationChannelTarget(node=0, path='translation')))
    with pytest.raises(ValueError, match='Overlapping'):
        model(b, tmp_path)


@pytest.mark.parametrize('field', ['rotation', 'scale'])
def test_identity_matrix_does_not_define_rotation_or_scale_pointer(tmp_path, field):
    b = Builder(); enclosing_material(b, 'alphaCutoff')
    b.g.nodes[0].matrix = np.eye(4).ravel().tolist()
    values = [[0, 0, 0, 1]]*2 if field == 'rotation' else [[1, 1, 1]]*2
    channel(b, '/nodes/0/'+field, values, 'VEC4' if field == 'rotation' else 'VEC3')
    with pytest.raises(ValueError, match='matrix nodes'):
        model(b, tmp_path)


def test_material_pointer_playback_uses_each_instance_clock_and_backward_seeks(tmp_path):
    from test_3d_render import doc, frame
    b = Builder(); enclosing_material(b, 'pbrMetallicRoughness/baseColorFactor')
    b.g.materials[0].alphaMode = 'BLEND'
    lo, hi = np.array([.125, .25, .75, .25]), np.array([.875, .75, .25, .875])
    channel(b, '/materials/0/pbrMetallicRoughness/baseColorFactor', [lo, hi], 'VEC4')
    path = b.save(tmp_path/'shared.gltf')
    props = 'primitive="mesh" scaleX=".5" scaleY=".8"'
    body = f'<object3D id="a" {props} mesh="m" x="-40" animationClip="clip" animationOffset=".4"/>'
    body += f'<object3D id="b" {props} mesh="m" x="40" animationClip="clip" animationSpeed="-.5" animationOffset="2.4"/>'
    lights = '<light id="key" type="directional" intensity="2" yaw="25"/>'
    actual = doc(tmp_path, body, h=120, assets=f'<mesh id="m" src="{path}" format="gltf"/>', lights=lights)
    for i, time in enumerate([0., .7, 1.6, .2, 3.4]):
        assets = ''
        for name, speed, offset in [('a', 1., .4), ('b', -.5, 2.4)]:
            control = copy.deepcopy(b)
            control.g.animations = []
            ct = (time*speed+offset)%3
            control.g.materials[0].pbrMetallicRoughness.baseColorFactor = (lo+(hi-lo)*np.clip((ct-1)/2, 0, 1)).tolist()
            src = control.save(tmp_path/f'{name}-{i}.gltf')
            assets += f'<mesh id="m{name}" src="{src}" format="gltf"/>'
        expected_body = f'<object3D id="a" {props} mesh="ma" x="-40"/><object3D id="b" {props} mesh="mb" x="40"/>'
        control = doc(tmp_path, expected_body, h=120, assets=assets, lights=lights, name=f'control-{i}.xml')
        np.testing.assert_allclose(frame(actual, time), frame(control), atol=7e-6)


@pytest.mark.parametrize('source,key', TEXTURES, ids=[row[0] for row in TEXTURES])
def test_animated_core_and_extension_uvs_match_static_rendered_controls(tmp_path, source, key):
    from test_3d_render import doc, frame
    from test_gltf_material_textures import material
    b = Builder()
    pixel = np.array([[[80, 100, 200, 60], [180, 160, 230, 120]],
                      [[120, 200, 180, 180], [200, 50, 210, 230]]], np.uint8)
    texture = b.image_png(pixel)
    base = material({
        'clearcoat': dict(clearcoatFactor=.9, clearcoatRoughnessFactor=.7),
        'transmission': dict(transmissionFactor=.7),
        'volume': dict(thicknessFactor=.05, attenuationDistance=.1, attenuationColor=[.3, .6, .8]),
        'sheen': dict(sheenColorFactor=[.3, .6, .8], sheenRoughnessFactor=.7),
        'specular': dict(specularFactor=.7, specularColorFactor=[.9, .6, .4]),
        'iridescence': dict(iridescenceFactor=.6, iridescenceThicknessMinimum=650., iridescenceThicknessMaximum=150.),
        'anisotropy': dict(anisotropyStrength=.65, anisotropyRotation=.3),
    }).to_dict()
    base['emissiveFactor'] = [.15, .2, .1]
    parent = base
    for part in source.split('/')[:-1]:
        parent = parent.setdefault(part, {})
    parent[source.split('/')[-1]] = {'index': texture, 'extensions': {'KHR_texture_transform': {}}}
    b.g.materials = [gt.Material.from_dict(base)]
    plane(b)
    endpoints = {'offset': (np.array([.125, .25]), np.array([.75, .875])),
                 'scale': (np.array([.5, .75]), np.array([1.25, 1.5])),
                 'rotation': (np.array([.125]), np.array([.875]))}
    animation = None
    for field, (lo, hi) in endpoints.items():
        animation = channel(b, '/materials/0/'+source+'/extensions/KHR_texture_transform/'+field,
                            [lo, hi], 'SCALAR' if field == 'rotation' else 'VEC2', times=(0., 2.), animation=animation)
    path = b.save(tmp_path/'animated.gltf')
    body = '<object3D id="o" primitive="mesh" mesh="m" animationClip="clip" animationSpeed="-.75" animationOffset=".3"/>'
    lights = '<light id="key" type="directional" intensity="2" yaw="25" pitch="-15"/><light id="d" type="dome" intensity=".3"/>'
    actual = doc(tmp_path, body, h=120, assets=f'<mesh id="m" src="{path}" format="gltf"/>', lights=lights)
    observed = []
    for i, time in enumerate([.2, 1.4, .7, .2]):
        raw = copy.deepcopy(base)
        parent = raw
        for part in source.split('/'):
            parent = parent[part]
        progress = ((time*-.75+.3)%2)/2
        for field, (lo, hi) in endpoints.items():
            value = lo+(hi-lo)*progress
            parent['extensions']['KHR_texture_transform'][field] = float(value[0]) if field == 'rotation' else value.tolist()
        control = copy.deepcopy(b)
        control.g.materials, control.g.animations = [gt.Material.from_dict(raw)], []
        src = control.save(tmp_path/f'control-{i}.gltf')
        expected = doc(tmp_path, '<object3D id="o" primitive="mesh" mesh="m"/>', h=120,
                       assets=f'<mesh id="m" src="{src}" format="gltf"/>', lights=lights, name=f'control-{i}.xml')
        result = frame(actual, time)
        np.testing.assert_allclose(result, frame(expected), atol=1e-5)
        observed.append(result)
    np.testing.assert_array_equal(observed[0], observed[-1])
    assert np.max(abs(observed[0]-observed[1])) > 1e-4


@pytest.mark.parametrize('field', ['translation', 'rotation', 'scale'])
@pytest.mark.parametrize('interpolation', ['STEP', 'LINEAR', 'CUBICSPLINE'])
def test_node_pointer_matches_standard_channel(tmp_path, field, interpolation):
    b = Builder(); enclosing_material(b, 'alphaCutoff')
    if field == 'rotation':
        values = [[0, 0, 0, 1], [0, 0, math.sqrt(.5), math.sqrt(.5)]]
    elif field == 'translation':
        values = [[2, 3, 4], [8, -2, 10]]
    else:
        values = [[1, 2, 3], [2, 1, .5]]
    if interpolation == 'CUBICSPLINE':
        zero = [0]*len(values[0])
        values = [zero, values[0], zero, zero, values[1], zero]
    channel(b, '/nodes/0/'+field, values, 'VEC4' if field == 'rotation' else 'VEC3', interpolation)
    standard = copy.deepcopy(b)
    standard.g.animations[0].channels[0].target = gt.AnimationChannelTarget(node=0, path=field)
    actual, control = model(b, tmp_path), model(standard, tmp_path, 'standard')
    for time in (0., 1., 1.5, 2., 4., 1.5):
        a, c = actual.pose(actual.clips[0], time)[0], control.pose(control.clips[0], time)[0]
        np.testing.assert_allclose(a.positions, c.positions, atol=1e-7)
        np.testing.assert_allclose(a.normals, c.normals, atol=1e-7)
        assert not a.static


@pytest.mark.parametrize('components', [False, True])
def test_whole_and_independent_weight_arrays(tmp_path, components):
    b = Builder(); enclosing_material(b, 'alphaCutoff')
    dx = b.add([[10, 0, 0]]*4, 'VEC3'); dy = b.add([[0, 20, 0]]*4, 'VEC3')
    b.g.meshes[0].primitives[0].targets = [{'POSITION': dx}, {'POSITION': dy}]
    if components:
        animation = channel(b, '/nodes/0/weights/0', [.25, .75])
        channel(b, '/nodes/0/weights/1', [.75, .25], animation=animation)
    else:
        channel(b, '/nodes/0/weights', [.25, .75, .75, .25])
    m = model(b, tmp_path)
    original = m.pose(None, 0)[0].positions
    for time in (1., 3., 2., 1.):
        u = (time-1)/2
        expected = original+[10*(.25+.5*u), 20*(.75-.5*u), 0]
        np.testing.assert_allclose(m.pose(m.clips[0], time)[0].positions, expected, atol=1e-7)


@pytest.mark.parametrize('error', ['vector_type', 'wrong_count', 'duplicate_times', 'empty_times', 'nonfinite', 'node_and_pointer'])
def test_invalid_pointer_channel_data_is_rejected(tmp_path, error):
    b = Builder(); enclosing_material(b, 'alphaCutoff')
    values, type_, times = [.2, .8], 'SCALAR', [1., 3.]
    if error == 'vector_type':
        values, type_ = [[.2, .3], [.8, .9]], 'VEC2'
    elif error == 'wrong_count':
        values = [.2, .3, .8]
    elif error == 'duplicate_times':
        times = [1., 1.]
    elif error == 'empty_times':
        times = []
    elif error == 'nonfinite':
        values = [.2, float('nan')]
    animation = channel(b, '/materials/0/alphaCutoff', values, type_, times=times)
    if error == 'node_and_pointer':
        animation.channels[0].target.node = 0
    with pytest.raises(ValueError, match='glTF'):
        model(b, tmp_path)


def test_camera_pointer_limit_is_explicit_and_preserves_clip_duration(tmp_path, caplog):
    b = Builder(); enclosing_material(b, 'alphaCutoff')
    b.g.cameras = [gt.Camera(type='perspective', perspective=gt.Perspective(yfov=.7, znear=.1))]
    channel(b, '/cameras/0/perspective/yfov', [.7, .8], times=(0., 5.))
    m = model(b, tmp_path)
    assert m.clips[0].duration == 5.
    assert 'not consumed by the mesh renderer' in caplog.text


def test_animated_mask_cutoff_matches_color_and_shadow_controls(tmp_path):
    from test_3d_render import doc, frame
    b = Builder(); enclosing_material(b, 'alphaCutoff')
    b.g.materials[0].alphaMode = 'MASK'
    b.g.materials[0].pbrMetallicRoughness.baseColorFactor = [.4, .6, .8, .5]
    channel(b, '/materials/0/alphaCutoff', [.25, .75], times=(0., 2.))
    path = b.save(tmp_path/'mask.gltf')
    under = '<object3D id="under" primitive="plane" width="150" height="110" z="-15" material="white"/>'
    materials = '<material id="white" baseColor="#FFFFFFFF" roughness=".8"/>'
    lights = '<light id="key" type="directional" intensity="2" yaw="25" pitch="-15" shadow="true" shadowMapSize="128"/>'
    actual = doc(tmp_path, under+'<object3D id="o" primitive="mesh" mesh="m" animationClip="clip"/>', h=120,
                 assets=f'<mesh id="m" src="{path}" format="gltf"/>', materials=materials, lights=lights)
    observed = []
    for i, time in enumerate([.2, 1.7, .7, .2]):
        control = copy.deepcopy(b)
        control.g.animations = []
        control.g.materials[0].alphaCutoff = .25+.5*time/2
        src = control.save(tmp_path/f'mask-control-{i}.gltf')
        expected = doc(tmp_path, under+'<object3D id="o" primitive="mesh" mesh="m"/>', h=120,
            assets=f'<mesh id="m" src="{src}" format="gltf"/>', materials=materials, lights=lights, name=f'control-{i}.xml')
        observed.append(frame(actual, time))
        np.testing.assert_allclose(observed[-1], frame(expected), atol=6e-6)
    assert np.max(abs(observed[0]-observed[1])) > .01
    np.testing.assert_array_equal(observed[0], observed[-1])
