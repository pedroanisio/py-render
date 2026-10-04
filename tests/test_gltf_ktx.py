"""BasisU input, original channel meanings, authored mips and material rendering."""
import base64
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
import pygltflib as gt
import pytest

from scenerender import gl
from scenerender.raster import srgb_to_linear
from scenerender.three import ktx
from scenerender.three.loaders import _Gltf
from scenerender.three.material_atlas import ATLAS_MAP_KEYS, pack_material_maps
from scenerender.three.materials import Material
from scenerender.three.texture_atlas import pack_scalar_maps
from scenerender.three.texture_image import TextureImage, mip_chain
from test_3d_loaders import Builder
from test_gltf_material_textures import CASES, atlas_probe, material, plane, render
from test_scalar_material_maps import reference_level, reference_pyramid, sampler_probe

FIX = Path(__file__).parent/'fixtures/media/ktx'
RECORDS = json.loads((FIX/'manifest.json').read_text())['fixtures']
PIXELS = np.load(FIX/'pixels.npz')
CODECS = ('etc1s', 'uastc', 'uastc_zstd')


@pytest.fixture(scope='module')
def decoder():
    try:
        ktx.library()
    except ktx.KTXUnavailable as exc:
        pytest.skip(str(exc))


def name(codec='uastc_zstd', color=False, channels=4):
    return f'{codec}_{channels}_{"color" if color else "data"}'


def decoded(source, level):
    a = PIXELS[source+'_decoded_'+str(level)].astype(np.float32)/255
    if '_color' in source:
        a[..., :3] = srgb_to_linear(a[..., :3])
    return a


def texture(builder, source, storage='buffer', fallback=False):
    data = (FIX/(source+'.ktx2')).read_bytes()
    fallback_source = None
    if fallback:
        fallback_texture = builder.image_png(np.array([[[255, 0, 255, 255]]], np.uint8))
        fallback_source = builder.g.textures.pop(fallback_texture).source
    if storage == 'buffer':
        image = gt.Image(bufferView=builder._view(data), mimeType='image/ktx2')
    elif storage == 'data':
        image = gt.Image(uri='data:image/ktx2;base64,'+base64.b64encode(data).decode())
    else:
        image = gt.Image(uri=(FIX/(source+'.ktx2')).resolve().as_uri().removeprefix('file://'))
    builder.g.images.append(image)
    builder.g.textures.append(gt.Texture(source=fallback_source,
        extensions={'KHR_texture_basisu': {'source': len(builder.g.images)-1}}))
    builder.g.extensionsUsed = ['KHR_texture_basisu']
    if not fallback:
        builder.g.extensionsRequired = ['KHR_texture_basisu']
    return len(builder.g.textures)-1


@pytest.mark.parametrize('record', RECORDS, ids=[r['name'] for r in RECORDS])
def test_decoding_matches_cli_and_original_channels(decoder, record):
    source = record['name']
    data = (FIX/(source+'.ktx2')).read_bytes()
    assert hashlib.sha256(data).hexdigest() == record['sha256']
    image = ktx.decode_basisu(data, record['srgb'])
    assert len(image.levels) == record['levels']
    for level, a in enumerate(image.levels):
        np.testing.assert_array_equal(a, decoded(source, level))
        original = PIXELS[source+'_input_'+str(level)].astype(np.float32)/255
        if record['srgb']:
            original[..., :3] = srgb_to_linear(original[..., :3])
        # Lossy ETC1S differs slightly; this independent check catches channel
        # misplacement that using the same transcoder's CLI oracle cannot catch.
        np.testing.assert_allclose(a[..., :record['channels']], original, atol=.035)
    np.testing.assert_array_equal(np.asarray(image), image.levels[0])
    assert image.nbytes == sum(a.nbytes for a in image.levels)


@pytest.mark.parametrize('codec', CODECS)
@pytest.mark.parametrize('storage', ['data', 'external', 'buffer', 'glb'])
@pytest.mark.parametrize('color', [False, True])
def test_gltf_image_sources_and_fallback_precedence(tmp_path, decoder, codec, storage, color):
    b = Builder()
    source = name(codec, color)
    index = texture(b, source, 'buffer' if storage == 'glb' else storage, fallback=True)
    loader = _Gltf(b.save(tmp_path/('model.glb' if storage == 'glb' else 'model.gltf'), glb=storage == 'glb'))
    image = loader.image(index, color)
    assert image is loader.image(index, color)
    for i, a in enumerate(image.levels):
        np.testing.assert_array_equal(a, decoded(source, i))


def test_missing_decoder_optional_fallback_and_required_error(tmp_path, monkeypatch):
    def missing():
        raise ktx.KTXUnavailable('decoder unavailable for test')
    monkeypatch.setattr(ktx, 'library', missing)
    b = Builder()
    index = texture(b, name(color=True), fallback=True)
    loader = _Gltf(b.save(tmp_path/'fallback.gltf'))
    np.testing.assert_array_equal(loader.image(index, True), [[[1., 0., 1., 1.]]])
    loader.g.extensionsRequired = ['KHR_texture_basisu']
    with pytest.raises(ktx.KTXUnavailable, match='decoder unavailable'):
        loader.image(index, True)
    loader.g.extensionsRequired = []
    loader.g.textures[index].source = None
    with pytest.raises(ktx.KTXUnavailable):
        loader.image(index, True)


def test_configured_library_failure_explains_installation(tmp_path, monkeypatch):
    monkeypatch.setenv('SCENERENDER_LIBKTX', str(tmp_path/'missing-ktx-library'))
    with pytest.raises(ktx.KTXUnavailable, match='Install it or set SCENERENDER_LIBKTX'):
        ktx.library()


def test_corrupt_compressed_payload_reports_error_and_decoder_recovers(decoder):
    data = bytearray((FIX/(name()+'.ktx2')).read_bytes())
    offset, length, _ = struct.unpack_from('<3Q', data, 80)
    data[offset:offset+length] = b'\0'*length
    with pytest.raises(ValueError, match='Cannot decode KTX2/BasisU'):
        ktx.decode_basisu(bytes(data), False)
    valid = ktx.decode_basisu((FIX/(name()+'.ktx2')).read_bytes(), False)
    np.testing.assert_array_equal(valid.levels[0], decoded(name(), 0))


@pytest.mark.parametrize('channels', [1, 2])
def test_red_and_red_green_payloads_cannot_claim_color_usage(channels):
    data = bytearray((FIX/(name(channels=channels)+'.ktx2')).read_bytes())
    dfd = struct.unpack_from('<I', data, 48)[0]
    data[dfd+13:dfd+15] = bytes([1, 2])
    with pytest.raises(ValueError, match='must contain linear data'):
        ktx.decode_basisu(bytes(data), True)


@pytest.mark.parametrize('mutation', ['magic', 'truncated', 'index', 'empty_level', 'width', 'depth', 'layers', 'faces',
    'format', 'compression', 'dfd', 'color', 'premultiplied', 'orientation', 'swizzle'])
def test_invalid_basis_images_fail_before_native_decode(monkeypatch, mutation):
    data = bytearray((FIX/(name()+'.ktx2')).read_bytes())
    dfd = struct.unpack_from('<I', data, 48)[0]
    if mutation == 'magic':
        data[0] = 0
    elif mutation == 'truncated':
        del data[-12:]
    elif mutation == 'index':
        struct.pack_into('<Q', data, 80, len(data)+10)
    elif mutation == 'empty_level':
        struct.pack_into('<Q', data, 88, 0)
    elif mutation in ('width', 'depth', 'layers', 'faces', 'format', 'compression'):
        offset, value = {'width': (20, 13), 'depth': (28, 4), 'layers': (32, 2), 'faces': (36, 6),
                         'format': (12, 37), 'compression': (44, 3)}[mutation]
        struct.pack_into('<I', data, offset, value)
    elif mutation == 'dfd':
        data[dfd] = 0
    elif mutation == 'color':
        data[dfd+14] = 2
    elif mutation == 'premultiplied':
        data[dfd+15] = 1
    else:
        key, value = (b'KTXorientation', b'ru') if mutation == 'orientation' else (b'KTXswizzle', b'bgra')
        entry = key+b'\0'+value+b'\0'
        kv = struct.pack('<I', len(entry))+entry+b'\0'*((-len(entry)) % 4)
        struct.pack_into('<II', data, 56, len(data), len(kv))
        data += kv
    def unexpected():
        pytest.fail('invalid KTX2 reached the native decoder')
    monkeypatch.setattr(ktx, 'library', unexpected)
    with pytest.raises(ValueError):
        ktx.decode_basisu(bytes(data), False)


@pytest.mark.parametrize('source', [None, -1, 5, True, '0'])
def test_invalid_gltf_basis_source(tmp_path, source):
    b = Builder()
    index = texture(b, name())
    b.g.textures[index].extensions['KHR_texture_basisu']['source'] = source
    loader = _Gltf(b.save(tmp_path/'bad.gltf'))
    with pytest.raises(ValueError, match='source index'):
        loader.image(index, False)


def test_partial_mips_generate_only_the_missing_tail(decoder):
    image = ktx.decode_basisu((FIX/(name()+'.ktx2')).read_bytes(), False)
    partial = TextureImage(image.levels[:2])
    expected = [reference_pyramid(image.levels[1][..., c]) for c in range(4)]
    complete = tuple(mip_chain(partial))
    assert len(complete) == 4
    np.testing.assert_array_equal(complete[0], image.levels[0])
    for i, level in enumerate(complete[1:]):
        np.testing.assert_allclose(level, np.stack([a[i] for a in expected], -1), atol=1e-7)
    assert np.max(abs(complete[-1]-image.levels[-1])) > .05


def test_atlases_preserve_every_authored_level(decoder):
    image = ktx.decode_basisu((FIX/(name()+'.ktx2')).read_bytes(), False)
    rgba = pack_material_maps([image], [{}], 64)
    scalar = pack_scalar_maps([image], 64)
    for i, a in enumerate(image.levels):
        start, w, h, _ = rgba.levels[0, i]
        np.testing.assert_array_equal(rgba.pixels.reshape(-1, 4)[start:start+w*h].reshape(h, w, 4), a)
        start, w, h, _ = scalar.levels[0, i]
        np.testing.assert_array_equal(scalar.pixels.ravel()[start:start+w*h].reshape(h, w), a[..., 0])
    assert rgba.pixels.reshape(-1, 4)[0, 2] == scalar.info[0, 2] == 3


CORE = [('core', 'baseColorTexture', 'baseColorMap', True),
        ('core', 'metallicRoughnessTexture', 'metallicRoughnessMap', False),
        ('core', 'normalTexture', 'normalMap', False),
        ('core', 'occlusionTexture', 'occlusionMap', False),
        ('core', 'emissiveTexture', 'emissiveMap', True)]
SLOTS = CORE+[(ext, field, key, srgb) for ext, field, key, _, _, srgb in CASES]


def bind_material(builder, slot, index, scale):
    extension, field, _, _ = slot
    info = dict(index=index, extensions={'KHR_texture_transform': dict(offset=[.13, .21], scale=[scale, scale])})
    ext = {
        'clearcoat': dict(clearcoatFactor=.8, clearcoatRoughnessFactor=.6),
        'transmission': dict(transmissionFactor=.65 if extension in ('volume', 'transmission') else 0.),
        'volume': dict(thicknessFactor=.05, attenuationDistance=.1, attenuationColor=[.3, .6, .8]),
        'sheen': dict(sheenColorFactor=[.3, .6, .8], sheenRoughnessFactor=.7),
        'specular': dict(specularFactor=.7, specularColorFactor=[.9, .6, .4]),
        'iridescence': dict(iridescenceFactor=.6),
        'anisotropy': dict(anisotropyStrength=.65, anisotropyRotation=.3),
    }
    m = material(ext)
    if extension != 'core':
        m.extensions['KHR_materials_'+extension][field] = info
    elif field in ('baseColorTexture', 'metallicRoughnessTexture'):
        setattr(m.pbrMetallicRoughness, field, gt.TextureInfo(**info))
    else:
        cls = {'normalTexture': gt.NormalMaterialTexture, 'occlusionTexture': gt.OcclusionTextureInfo,
               'emissiveTexture': gt.TextureInfo}[field]
        setattr(m, field, cls(**info))
    if field == 'baseColorTexture':
        m.alphaMode = 'BLEND'
    if field == 'emissiveTexture':
        m.emissiveFactor = [.8, .6, .4]
    builder.g.materials = [m]
    builder.g.samplers = [gt.Sampler(wrapS=33648, wrapT=10497, minFilter=9987, magFilter=9729)]
    builder.g.textures[index].sampler = 0


@pytest.mark.parametrize('slot', SLOTS, ids=[s[1] for s in SLOTS])
def test_every_material_slot_retains_authored_levels(tmp_path, decoder, slot):
    b = Builder()
    source = name(color=slot[3])
    index = texture(b, source)
    bind_material(b, slot, index, 2.5)
    loader = _Gltf(b.save(tmp_path/'slot.gltf'))
    spec = loader.material(loader.g.materials[0])
    result = spec.textures[slot[2]]
    assert isinstance(result, TextureImage)
    for i, a in enumerate(result.levels):
        expected = decoded(source, i)
        if slot[2] in ('clearcoatMap', 'clearcoatRoughnessMap'):
            channel = 0 if slot[2] == 'clearcoatMap' else 1
            expected = np.repeat(expected[..., channel:channel+1], 4, -1)
        np.testing.assert_array_equal(a, expected)
    assert spec.params['mapUV'][slot[2]]['scale'] == (2.5, 2.5)
    assert spec.params['mapSamplers'][slot[2]]['wrapS'] == 33648


@pytest.mark.skipif(not gl.available(), reason='no GL')
@pytest.mark.parametrize('slot', SLOTS, ids=[s[1] for s in SLOTS])
@pytest.mark.parametrize('minified', [False, True])
def test_every_material_slot_renders_authored_mips(tmp_path, decoder, slot, minified):
    source = name(CODECS[SLOTS.index(slot) % 3], slot[3])
    a, b = Builder(), Builder()
    ia = texture(a, source)
    level = 3 if minified else 0
    ib = b.image_png(PIXELS[source+'_decoded_'+str(level)])
    for builder, index in ((a, ia), (b, ib)):
        bind_material(builder, slot, index, 256. if minified else 1.)
    actual, expected = render(a, tmp_path, 'ktx'), render(b, tmp_path, 'png')
    assert expected[..., :3].max() > .01
    np.testing.assert_allclose(actual, expected, atol=8e-6)


def filtered_levels(levels, scale, lod, sampler, channel=0):
    y, x = np.mgrid[:32, :32]
    uv = np.stack(((x+.5)/32, (y+.5)/32), -1)*scale+(.113, -.217)
    minimum, maximum = sampler['minFilter'], sampler['magFilter']
    wraps = (sampler['wrapS'], sampler['wrapT'])
    result = np.zeros((32, 32), np.float64)
    for selected, mode in ((lod <= 0, maximum), (lod > 0, minimum)):
        if mode in (9728, 9729):
            a = reference_level(levels[0][..., channel], uv, mode == 9729, wraps, 0.)
        else:
            l = np.clip(lod, 0, len(levels)-1)
            a = np.zeros_like(result)
            for i, level in enumerate(levels):
                weight = (np.maximum(0, np.ceil(l+.5)-1) == i) if mode in (9984, 9985) else np.maximum(0, 1-abs(l-i))
                a += weight*reference_level(level[..., channel], uv, mode in (9985, 9987), wraps, 0.)
        result[selected] = a[selected]
    return result


@pytest.mark.skipif(not gl.available(), reason='no GL')
@pytest.mark.parametrize('minimum', [9728, 9729, 9984, 9985, 9986, 9987])
@pytest.mark.parametrize('maximum', [9728, 9729])
@pytest.mark.parametrize('scale', [.5, 5.2, 64.])
def test_authored_mip_sampling_matches_independent_reference(decoder, sampler_probe, atlas_probe, minimum, maximum, scale):
    image = ktx.decode_basisu((FIX/(name()+'.ktx2')).read_bytes(), False)
    sampler = dict(wrapS=33648, wrapT=10497, minFilter=minimum, magFilter=maximum)
    material = Material({'mapSamplers': {'iorMap': sampler}}, {'iorMap': image})
    scalar = sampler_probe(material, 0, (scale, scale), (.113, -.217))
    for c in (0, 1):
        expected = filtered_levels(image.levels, scale, scalar[..., c+2], sampler)
        np.testing.assert_allclose(scalar[..., c], expected, atol=3e-6)
    rgba, lod = atlas_probe(material, ATLAS_MAP_KEYS.index('iorMap'), scale)
    for c in range(4):
        expected = filtered_levels(image.levels, scale, lod[..., 0], sampler, c)
        np.testing.assert_allclose(rgba[..., c], expected, atol=3e-6)


@pytest.mark.skipif(not gl.available(), reason='no GL')
@pytest.mark.parametrize('source', ['uastc_zstd_4_color_single', 'etc1s_4_data_partial'])
def test_incomplete_ktx_pyramids_reach_gpu_with_generated_tail(decoder, sampler_probe, atlas_probe, source):
    image = ktx.decode_basisu((FIX/(source+'.ktx2')).read_bytes(), '_color' in source)
    material = Material({}, {'iorMap': image})
    result = sampler_probe(material, 0, (64., 64.))
    expected = np.array([reference_pyramid(image.levels[-1][..., c])[-1][0, 0] for c in range(4)])
    np.testing.assert_allclose(result[..., :2], expected[0], atol=3e-7)
    rgba, _ = atlas_probe(material, 0, 64.)
    np.testing.assert_allclose(rgba, np.broadcast_to(expected, rgba.shape), atol=3e-7)


@pytest.mark.parametrize('slot', SLOTS, ids=[s[1] for s in SLOTS])
def test_animated_materials_retain_mips_through_seeks_and_eviction(tmp_path, decoder, slot):
    from test_gltf_animation_pointer import TEXTURES, channel, model
    b = Builder()
    source = name(color=slot[3])
    bind_material(b, slot, texture(b, source), 1.)
    plane(b)
    pointer = dict((key, path) for path, key in TEXTURES)[slot[2]]
    channel(b, '/materials/0/'+pointer+'/extensions/KHR_texture_transform/scale',
            [[1., 1.], [256., 256.]], 'VEC2', times=(0., 2.))
    m = model(b, tmp_path)
    image = m.pose(None, 0)[0].material.textures[slot[2]]
    for time in (2., .5, 0., 1., 2., *np.linspace(0, 2, 30), .5):
        sample = m.pose(m.clip('clip'), time)[0].material
        assert sample.textures[slot[2]] is image
        assert len(image.levels) == 4
        np.testing.assert_allclose(sample.params['mapUV'][slot[2]]['scale'], (1+255*time/2,)*2)
    sampler = next(iter(m.clip('clip').material_samplers.values()))
    assert len(sampler.samples) == 8


@pytest.mark.skipif(not gl.available(), reason='no GL')
@pytest.mark.parametrize('codec', CODECS)
def test_basis_alpha_mips_match_animated_color_and_shadow_controls(tmp_path, decoder, codec):
    from test_3d_render import doc, frame
    from test_gltf_animation_pointer import channel
    b = Builder()
    source = name(codec, True)
    index = texture(b, source)
    bind_material(b, SLOTS[0], index, 256.)
    b.g.materials[0].alphaMode = 'MASK'
    plane(b)
    channel(b, '/materials/0/alphaCutoff', [.25, .85], times=(0., 2.))
    path = b.save(tmp_path/'alpha.gltf')
    under = '<object3D id="under" primitive="plane" width="150" height="110" z="-15" material="white"/>'
    materials = '<material id="white" baseColor="#FFFFFFFF" roughness=".8"/>'
    lights = '<light id="key" type="directional" intensity="2" yaw="25" pitch="-15" shadow="true" shadowMapSize="128"/>'
    actual = doc(tmp_path, under+'<object3D id="o" primitive="mesh" mesh="m" animationClip="clip"/>', h=120,
        assets=f'<mesh id="m" src="{path}" format="gltf"/>', materials=materials, lights=lights)
    observations = []
    for i, time in enumerate((.2, 1.8, .7, .2)):
        control = Builder()
        tex = control.image_png(PIXELS[source+'_decoded_3'])
        bind_material(control, SLOTS[0], tex, 256.)
        control.g.materials[0].alphaMode = 'MASK'
        control.g.materials[0].alphaCutoff = .25+.6*time/2
        plane(control)
        src = control.save(tmp_path/f'alpha-control-{i}.gltf')
        expected = doc(tmp_path, under+'<object3D id="o" primitive="mesh" mesh="m"/>', h=120,
            assets=f'<mesh id="m" src="{src}" format="gltf"/>', materials=materials, lights=lights, name=f'control-{i}.xml')
        observations.append(frame(actual, time))
        np.testing.assert_allclose(observations[-1], frame(expected), atol=8e-6)
    assert np.max(abs(observations[0]-observations[1])) > .01
    np.testing.assert_array_equal(observations[0], observations[-1])


@pytest.mark.skipif(not gl.available(), reason='no GL')
def test_authored_levels_survive_gpu_cache_eviction(decoder):
    from scenerender.three import renderer as gpu
    context = gpu.res().ctx
    original = ktx.decode_basisu((FIX/(name()+'.ktx2')).read_bytes(), False)
    material = Material({}, {'baseColorMap': original})
    cache = {}
    first = gpu._material_texture(context, material, 'baseColorMap', cache)
    for value in range(140):
        other = Material({}, {'baseColorMap': np.full((1, 1, 4), value/140, np.float32)})
        gpu._material_texture(context, other, 'baseColorMap', cache)
    reloaded = gpu._material_texture(context, material, 'baseColorMap', cache)
    assert reloaded is not first and len(cache) <= 128
    for i, level in enumerate(original.levels):
        actual = np.frombuffer(reloaded.read(level=i), np.float32).reshape(level.shape)
        np.testing.assert_array_equal(actual, level)
    for _, tex in cache.values():
        tex.release()
