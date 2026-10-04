"""Observable cache behavior after MaterialX documents and dependencies change."""
from pathlib import Path
from types import SimpleNamespace
import json
import os

import MaterialX as mx
import numpy as np
from PIL import Image
import pytest

from scenerender.three import materialx_cache, materials
from scenerender.three.materialx_inputs import library
from test_materialx_inputs import inp, material, renderer


def rewrite(path, value, preserve_mtime=True):
    stat = path.stat()
    path.write_text(value)
    if preserve_mtime:
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def tree(tmp_path, *, image=False, nested=False, procedural=False):
    directory = tmp_path / 'included'
    directory.mkdir()
    leaf = directory / 'leaf.mtlx'
    if image:
        node = f'<image name="color" type="color3">{inp("file", "filename", "paint.png")}</image>'
        pixels = np.zeros((8, 8, 3), np.uint8)
        pixels[..., 0] = 64
        pixels[::2, :, 0] = 192
        Image.fromarray(pixels).save(directory / 'paint.png')
    else:
        node = f'<constant name="color" type="color3">{inp("value", "color3", (.2, .4, .6))}</constant>'
    leaf.write_text(f'<materialx version="1.39">{node}</materialx>')
    target = leaf
    if nested:
        target = directory / 'parent.mtlx'
        target.write_text('''<materialx version="1.39" xmlns:xi="http://www.w3.org/2001/XInclude">
            <xi:include href="leaf.mtlx"/></materialx>''')
    nodes = f'<xi:include href="included/{target.name}"/>'
    color = 'color'
    if procedural:
        nodes += '''<multiply name="scaled" type="color3"><input name="in1" type="color3" nodename="color"/>
            <input name="in2" type="color3" value="0.5, 0.5, 0.5"/></multiply>'''
        color = 'scaled'
    path = material(tmp_path, nodes=nodes, attrs='xmlns:xi="http://www.w3.org/2001/XInclude"',
                    inputs=f'<input name="base_color" type="color3" nodename="{color}"/>')
    return path, leaf


@pytest.mark.parametrize('source', ['root', 'include', 'nested_include', 'image'])
@pytest.mark.parametrize('preserve_mtime', [False, True])
def test_live_renderer_refreshes_changed_dependencies(tmp_path, source, preserve_mtime):
    path, leaf = tree(tmp_path, image=source == 'image', nested=source == 'nested_include')
    r = renderer(path, 'unlit="true"')
    first = r.rc.render_frame(0).px.copy()
    if source == 'image':
        image = leaf.parent / 'paint.png'
        stat = image.stat()
        pixels = np.zeros((8, 8, 3), np.uint8)
        pixels[..., 1] = 192
        Image.fromarray(pixels).save(image)
        if preserve_mtime:
            os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        expected = [0., 192/255, 0.]
    else:
        changed = path if source == 'root' else leaf
        if source == 'root':
            value = path.read_text().replace('nodename="color"', 'value="0.6, 0.4, 0.2"')
        else:
            value = leaf.read_text().replace('0.2, 0.4, 0.6', '0.6, 0.4, 0.2')
        rewrite(changed, value, preserve_mtime)
        expected = [.6, .4, .2]
    second = r.rc.render_frame(0).px
    assert np.max(np.abs(first-second)) > .1
    np.testing.assert_allclose(second[40, 40, :3], expected, atol=2e-6)
    # A reload replaces its entry; repeated reads return the same decoded data.
    spec = materials.load_materialx(r.rc, str(path))
    assert materials.load_materialx(r.rc, str(path)) is spec
    assert len([key for key in r.rc.cache if key[0] == 'mtlx']) == 1


@pytest.mark.parametrize('source', ['root', 'include', 'image'])
def test_missing_dependency_creation_and_deletion_are_observed(tmp_path, source):
    path, leaf = tree(tmp_path, image=source == 'image')
    missing = {'root': path, 'include': leaf, 'image': leaf.parent / 'paint.png'}[source]
    saved = missing.read_bytes()
    missing.unlink()
    rc = SimpleNamespace(cache={})
    first = materials.load_materialx(rc, str(path))
    if source == 'image':
        np.testing.assert_array_equal(first.textures['baseColorMap'][..., :3], 0.)
    else:
        assert first is None
    missing.write_bytes(saved)
    second = materials.load_materialx(rc, str(path))
    assert second is not None
    if source == 'image':
        assert second.textures['baseColorMap'][..., 0].max() > .7
    missing.unlink()
    third = materials.load_materialx(rc, str(path))
    if source == 'image':
        np.testing.assert_array_equal(third.textures['baseColorMap'][..., :3], 0.)
    else:
        assert third is None


def test_included_image_prefix_and_interface_track_the_same_file(tmp_path):
    path, leaf = tree(tmp_path, image=True)
    (leaf.parent / 'maps').mkdir()
    original = leaf.parent / 'paint.png'
    original.rename(leaf.parent / 'maps' / 'paint.png')
    leaf.write_text('''<materialx version="1.39" fileprefix="maps/">
        <nodegraph name="G"><input name="filename" type="filename" value="paint.png"/>
        <image name="source" type="color3"><input name="file" type="filename" interfacename="filename"/></image>
        <output name="out" type="color3" nodename="source"/></nodegraph></materialx>''')
    path.write_text(path.read_text().replace('nodename="color"', 'nodegraph="G" output="out"'))
    document = mx.createDocument(); mx.readFromXmlFile(document, str(path)); document.setDataLibrary(library())
    assert document.validate()[0]
    rc = SimpleNamespace(cache={})
    first = materials.load_materialx(rc, str(path))
    assert first.textures['baseColorMap'][..., 0].max() > .7
    Image.fromarray(np.full((8, 8, 3), 64, np.uint8)).save(leaf.parent / 'maps' / 'paint.png')
    second = materials.load_materialx(rc, str(path))
    np.testing.assert_allclose(second.textures['baseColorMap'][..., :3], 64/255, atol=1e-7)


def test_replacing_dependency_symlink_changes_the_render(tmp_path):
    path, leaf = tree(tmp_path)
    target = leaf.with_name('alternate.mtlx')
    target.write_text(leaf.read_text().replace('0.2, 0.4, 0.6', '0.6, 0.4, 0.2'))
    rc = SimpleNamespace(cache={})
    first = materials.load_materialx(rc, str(path))
    leaf.unlink(); leaf.symlink_to(target.name)
    second = materials.load_materialx(rc, str(path))
    assert first.params['baseColor'] == pytest.approx((.2, .4, .6, 1.))
    assert second.params['baseColor'] == pytest.approx((.6, .4, .2, 1.))


def test_nested_include_preserves_image_origin_and_observes_new_shadowing_include(tmp_path, monkeypatch):
    path, leaf = tree(tmp_path, image=True, nested=True)
    nested = leaf.parent / 'nested'
    nested.mkdir()
    leaf.rename(nested / leaf.name)
    (leaf.parent / 'paint.png').rename(nested / 'paint.png')
    parent = leaf.parent / 'parent.mtlx'
    parent.write_text(parent.read_text().replace('leaf.mtlx', 'nested/leaf.mtlx'))
    rc = SimpleNamespace(cache={})
    first = materials.load_materialx(rc, str(path))
    assert first.textures['baseColorMap'][..., 0].max() > .7
    # Search-path includes keep their own asset directory. A new local include
    # must take precedence even though the old include itself did not change.
    monkeypatch.setenv('MATERIALX_SEARCH_PATH', str(nested))
    parent.write_text(parent.read_text().replace('nested/leaf.mtlx', 'leaf.mtlx'))
    second = materials.load_materialx(rc, str(path))
    np.testing.assert_array_equal(second.textures['baseColorMap'], first.textures['baseColorMap'])
    leaf.write_text('<materialx version="1.39"><constant name="color" type="color3">'
                    '<input name="value" type="color3" value="0.1, 0.7, 0.2"/></constant></materialx>')
    third = materials.load_materialx(rc, str(path))
    assert third.params['baseColor'] == pytest.approx((.1, .7, .2, 1.))


@pytest.fixture
def small_baker(tmp_path, monkeypatch):
    if not os.environ.get('DISPLAY'):
        pytest.skip('the MaterialX TextureBaker needs a GLX display')
    monkeypatch.setattr(materials, 'BAKE_SIZE', 32)
    monkeypatch.setattr(materialx_cache.tempfile, 'gettempdir', lambda: str(tmp_path))


def test_real_bake_refreshes_nested_image_and_graph_changes(tmp_path, small_baker):
    path, leaf = tree(tmp_path, image=True, nested=True, procedural=True)
    rc = SimpleNamespace(cache={})
    first = materials.load_materialx(rc, str(path))
    assert first.textures['baseColorMap'][..., 0].max() > .3
    assert first.textures['baseColorMap'][..., 1].max() == 0.
    image = leaf.parent / 'paint.png'
    stat = image.stat()
    pixels = np.asarray(Image.open(image)).copy()[..., [1, 0, 2]]
    Image.fromarray(pixels).save(image)
    os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    second = materials.load_materialx(rc, str(path))
    np.testing.assert_allclose(second.textures['baseColorMap'][..., 1], first.textures['baseColorMap'][..., 0], atol=1e-6)
    assert second.textures['baseColorMap'][..., 0].max() == 0.
    rewrite(leaf, leaf.read_text().replace('</image>', inp('default', 'color3', (.5, .5, .5))+'</image>'))
    third = materials.load_materialx(rc, str(path))
    assert third is not second
    assert len(list((tmp_path / 'scenerender-mtlx' / 'v2').glob('*/manifest.json'))) == 3


@pytest.mark.parametrize('damage', ['missing_image', 'corrupt_image', 'corrupt_document', 'missing_manifest'])
def test_real_bake_repairs_incomplete_or_corrupt_artifacts(tmp_path, small_baker, damage):
    path, leaf = tree(tmp_path, image=True, procedural=True)
    baked = Path(materials.bake_materialx(str(path)))
    rc = SimpleNamespace(cache={})
    before = materials.load_materialx(rc, str(path))
    image = next(baked.parent.glob('*.hdr'))
    if damage == 'missing_image':
        image.unlink()
    elif damage == 'corrupt_image':
        stat = image.stat()
        image.write_bytes(b'x'*stat.st_size)
        os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif damage == 'corrupt_document':
        baked.write_text('<broken>')
    else:
        (baked.parent / 'manifest.json').unlink()
    assert Path(materials.bake_materialx(str(path))) == baked
    after = materials.load_materialx(SimpleNamespace(cache={}), str(path))
    np.testing.assert_array_equal(before.textures['baseColorMap'], after.textures['baseColorMap'])
    assert materialx_cache._valid_artifacts(baked.parent)
    assert not any('scenerender-mtlx' in e.getValueString() for e in _filenames(baked))


def _filenames(path):
    document = mx.createDocument(); mx.readFromXmlFile(document, str(path))
    return [e for e in document.traverseTree() if hasattr(e, 'getType') and e.getType() == 'filename']


def test_failed_bake_is_not_reused_and_does_not_poison_recovery(tmp_path, monkeypatch):
    path, leaf = tree(tmp_path, image=True, procedural=True)
    monkeypatch.setattr(materialx_cache.tempfile, 'gettempdir', lambda: str(tmp_path))
    # A real subprocess writes an incomplete document and fails, as a killed or
    # unavailable GL baker can do. It must never publish a reusable entry.
    script = "import pathlib,sys; pathlib.Path(sys.argv[3]).write_text('<materialx/>'); sys.exit(1)"
    for _ in range(2):
        with pytest.raises(RuntimeError):
            materialx_cache.bake(str(path), 8, script)
    assert not list((tmp_path / 'scenerender-mtlx').rglob('manifest.json'))
    assert not list((tmp_path / 'scenerender-mtlx').rglob('baked.mtlx'))


def test_bake_size_and_script_are_part_of_the_cache_identity(tmp_path, monkeypatch):
    path, leaf = tree(tmp_path)
    monkeypatch.setattr(materialx_cache.tempfile, 'gettempdir', lambda: str(tmp_path))
    script = "import shutil,sys; shutil.copyfile(sys.argv[1], sys.argv[3])"
    # Use a self-contained constant document for this publication-only control.
    path = material(tmp_path, name='constant')
    a = materialx_cache.bake(str(path), 8, script)
    assert materialx_cache.bake(str(path), 8, script) == a
    assert materialx_cache.bake(str(path), 16, script) != a
    assert materialx_cache.bake(str(path), 8, script+'\n# revision') != a
    assert json.loads((Path(a).parent / 'manifest.json').read_text()).keys() == {'baked.mtlx'}


def test_dependencies_changed_during_bake_are_not_published(tmp_path, monkeypatch):
    path = material(tmp_path)
    monkeypatch.setattr(materialx_cache.tempfile, 'gettempdir', lambda: str(tmp_path))
    script = r'''import pathlib,shutil,sys
shutil.copyfile(sys.argv[1], sys.argv[3])
p=pathlib.Path(sys.argv[1]);p.write_text(p.read_text()+'\n<!-- edited while baking -->')
'''
    with pytest.raises(RuntimeError, match='dependencies changed'):
        materialx_cache.bake(str(path), 8, script)
    assert not list((tmp_path / 'scenerender-mtlx').rglob('manifest.json'))


def test_real_bake_invalidates_authored_glsl_and_recursive_includes(tmp_path, small_baker):
    path, leaf = tree(tmp_path, image=True)
    source = leaf.parent / 'custom.glsl'
    factor = leaf.parent / 'factor.glsl'
    factor.write_text('vec3 custom_factor() { return vec3(0.25); }\n')
    source.write_text('#include "factor.glsl"\nvoid custom_color(vec3 color, out vec3 result) { result=color*custom_factor(); }\n')
    leaf.write_text(leaf.read_text().replace('</materialx>', '''
        <nodedef name="ND_custom_color" node="custom_color"><input name="color" type="color3" value="1, 1, 1"/>
          <output name="out" type="color3"/></nodedef>
        <implementation name="IM_custom_color" nodedef="ND_custom_color" target="genglsl"
          file="custom.glsl" function="custom_color"/>
        </materialx>'''))
    path.write_text(path.read_text().replace('<standard_surface', '''
        <custom_color name="custom" type="color3"><input name="color" type="color3" nodename="color"/></custom_color>
        <standard_surface''').replace('name="base_color" type="color3" nodename="color"',
                                      'name="base_color" type="color3" nodename="custom"'))
    rc = SimpleNamespace(cache={})
    first = materials.load_materialx(rc, str(path))
    assert first.textures['baseColorMap'][..., 0].max() > .1
    rewrite(factor, factor.read_text().replace('0.25', '0.75'))
    second = materials.load_materialx(rc, str(path))
    np.testing.assert_allclose(second.textures['baseColorMap'][..., 0],
                               first.textures['baseColorMap'][..., 0]*3, atol=.008)
    rewrite(source, source.read_text().replace('color*custom_factor()', 'color*custom_factor()*0.5'))
    third = materials.load_materialx(rc, str(path))
    np.testing.assert_allclose(third.textures['baseColorMap'][..., 0],
                               second.textures['baseColorMap'][..., 0]*.5, atol=.004)


def test_concurrent_bakers_publish_one_complete_entry(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    path = material(tmp_path)
    monkeypatch.setattr(materialx_cache.tempfile, 'gettempdir', lambda: str(tmp_path))
    script = r'''import pathlib,shutil,sys,time
with pathlib.Path(sys.argv[1]).with_suffix('.calls').open('a') as stream: stream.write('bake\n')
time.sleep(0.15)
shutil.copyfile(sys.argv[1], sys.argv[3])
'''
    barrier = Barrier(2)
    def run():
        barrier.wait()
        return materialx_cache.bake(str(path), 8, script)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: run(), range(2)))
    assert a == b
    assert path.with_suffix('.calls').read_text() == 'bake\n'
    assert materialx_cache._valid_artifacts(Path(a).parent)


def test_real_bake_preserves_versioned_defaults(tmp_path, small_baker):
    path = material(tmp_path, version='version="1.0.0"',
                    inputs='<input name="specular_roughness" type="float" nodename="rough"/>',
                    nodes='''<multiply name="rough" type="float"><input name="in1" type="float" value="0.2"/>
                      <input name="in2" type="float" value="2"/></multiply>''')
    spec = materials.load_materialx(SimpleNamespace(cache={}), str(path))
    assert spec.params['baseColor'] == (1., 1., 1., 1.)  # Version 1.0.1 defaults to 0.8.
    assert spec.params['roughness'] == pytest.approx(.4)


def test_exit_zero_with_missing_generated_image_is_not_cached(tmp_path, monkeypatch):
    path, leaf = tree(tmp_path, image=True)
    # Copy a valid standalone document with a direct image, but omit its pixels.
    standalone = material(tmp_path, name='standalone',
        nodes='<image name="image" type="color3"><input name="file" type="filename" value="missing.hdr"/></image>',
        inputs='<input name="base_color" type="color3" nodename="image"/>')
    monkeypatch.setattr(materialx_cache.tempfile, 'gettempdir', lambda: str(tmp_path))
    script = 'import shutil,sys; shutil.copyfile(sys.argv[1], sys.argv[3])'
    with pytest.raises(RuntimeError, match='did not produce missing.hdr'):
        materialx_cache.bake(str(standalone), 8, script)
    assert not list((tmp_path / 'scenerender-mtlx').rglob('manifest.json'))
