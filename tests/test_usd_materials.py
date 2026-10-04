"""USD material graphs retain their texture values, coordinates and animation."""
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
from pxr import Sdf, Usd, UsdGeom, UsdShade

from scenerender import gl
from scenerender.render import Renderer
from scenerender.three.loaders import load_model
from scenerender.three.materials import from_spec, map_uvs, sample_map


ST = np.array([[0., 0.], [1., 0.], [1., 1.], [0., 1.]])


class Scene:
    def __init__(self, root, name='test'):
        self.root, self.name = Path(root), name
        self.path = self.root / (name+'.usda')
        self.stage = Usd.Stage.CreateNew(str(self.path))
        UsdGeom.SetStageUpAxis(self.stage, 'Y')
        self.stage.SetTimeCodesPerSecond(24)
        self.stage.SetStartTimeCode(0)
        self.stage.SetEndTimeCode(24)
        self.mesh = UsdGeom.Mesh.Define(self.stage, '/Plane')
        self.mesh.CreatePointsAttr([(-32., -32., 0.), (32., -32., 0.), (32., 32., 0.), (-32., 32., 0.)])
        self.mesh.CreateFaceVertexCountsAttr([4])
        self.mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        self.mesh.CreateNormalsAttr([(0., 0., 1.)]*4)
        self.mesh.SetNormalsInterpolation('vertex')
        self.uv('st', ST)
        self.mat = UsdShade.Material.Define(self.stage, '/Mat')
        self.pbr = self.shader('Pbr', 'UsdPreviewSurface')
        self.mat.CreateSurfaceOutput().ConnectToSource(self.pbr.ConnectableAPI(), 'surface')
        UsdShade.MaterialBindingAPI.Apply(self.mesh.GetPrim()).Bind(self.mat)
        self.pbr.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set((.6, .6, .6))

    def shader(self, name, kind):
        shader = UsdShade.Shader.Define(self.stage, '/Mat/'+name)
        shader.CreateIdAttr(kind)
        return shader

    def uv(self, name, values, interp='vertex'):
        pv = UsdGeom.PrimvarsAPI(self.mesh).CreatePrimvar(name, Sdf.ValueTypeNames.TexCoord2fArray, interp)
        pv.Set(np.asarray(values).tolist())
        return pv

    def reader(self, name='Reader', var='st', fallback=(0., 0.)):
        shader = self.shader(name, 'UsdPrimvarReader_float2')
        shader.CreateInput('varname', Sdf.ValueTypeNames.String).Set(var)
        shader.CreateInput('fallback', Sdf.ValueTypeNames.Float2).Set(fallback)
        shader.CreateOutput('result', Sdf.ValueTypeNames.Float2)
        return shader

    def transform(self, name, source, scale=(1., 1.), angle=0., shift=(0., 0.)):
        shader = self.shader(name, 'UsdTransform2d')
        shader.CreateInput('in', Sdf.ValueTypeNames.Float2).ConnectToSource(source.ConnectableAPI(), 'result')
        shader.CreateInput('scale', Sdf.ValueTypeNames.Float2).Set(scale)
        shader.CreateInput('rotation', Sdf.ValueTypeNames.Float).Set(angle)
        shader.CreateInput('translation', Sdf.ValueTypeNames.Float2).Set(shift)
        shader.CreateOutput('result', Sdf.ValueTypeNames.Float2)
        return shader

    def texture(self, name, pixels, reader=None, space='raw', wrap='repeat'):
        filename = self.root / f'{self.name}-{name}.npy'
        np.save(filename, np.asarray(pixels, np.float32))
        texture = self.shader(name, 'UsdUVTexture')
        texture.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(str(filename))
        texture.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set(space)
        texture.CreateInput('wrapS', Sdf.ValueTypeNames.Token).Set(wrap)
        texture.CreateInput('wrapT', Sdf.ValueTypeNames.Token).Set(wrap)
        for output in ('r', 'g', 'b', 'a'):
            texture.CreateOutput(output, Sdf.ValueTypeNames.Float)
        texture.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
        if reader is not None:
            texture.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), 'result')
        return texture

    def connect(self, name, texture, output='rgb'):
        kind = Sdf.ValueTypeNames.Normal3f if name == 'normal' else (
            Sdf.ValueTypeNames.Color3f if name.endswith('Color') else Sdf.ValueTypeNames.Float)
        self.pbr.CreateInput(name, kind).ConnectToSource(texture.ConnectableAPI(), output)

    def model(self):
        self.stage.GetRootLayer().Save()
        return load_model(str(self.path))

    def renderer(self, *, animation=False, extra='', under='', lights=None):
        if not gl.available():
            pytest.skip('no GL')
        self.stage.GetRootLayer().Save()
        path = self.root / (self.name+'.xml')
        if lights is None:
            lights = '''<light id="a" type="ambient" intensity=".25"/>
                        <light id="l" type="directional" yaw="-25" pitch="35" intensity="2"/>'''
        path.write_text(f'''<scene version="1.1">
          <project width="80" height="80" fps="24" duration="2"/>
          <assets><mesh id="m" src="{self.path}" format="usd"/></assets>
          <materials><material id="red" baseColor="#FF0000" unlit="true"/></materials>
          <composition><camera id="c" z="200" projection="orthographic" orthoHeight="80"/>
            {under}
            <object3D id="o" primitive="mesh" mesh="m" {'animationClip="default"' if animation else ''}/>
            {extra}</composition>
          <lights>{lights}</lights>
        </scene>''')
        return Renderer.open(str(path), strict=True)


@pytest.mark.parametrize('name,output,key,channel', [
    ('diffuseColor', 'rgb', 'baseColorMap', None), ('emissiveColor', 'rgb', 'emissiveMap', None),
    ('normal', 'rgb', 'normalMap', None), ('roughness', 'g', 'roughnessMap', 1),
    ('metallic', 'b', 'metallicMap', 2), ('occlusion', 'r', 'occlusionMap', 0),
    ('opacity', 'a', 'opacityMap', 3), ('displacement', 'g', 'displacementMap', 1),
])
def test_scale_bias_and_channel_outputs(tmp_path, name, output, key, channel):
    scene = Scene(tmp_path)
    source = np.array([[[.1, .2, .4, .8], [.7, .6, .3, .2]]], np.float32)
    texture = scene.texture('Tex', source, scene.reader())
    scale, bias = np.array([2., -1., .5, .25]), np.array([-.3, 1., .1, .2])
    texture.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set(tuple(scale))
    texture.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set(tuple(bias))
    scene.connect(name, texture, output)
    material = scene.model().pose(None, 0)[0].material
    expected = source*scale+bias
    if name == 'normal':
        expected[..., :3] = expected[..., :3]*.5+.5
    if channel is None:
        np.testing.assert_allclose(material.textures[key][..., :3], expected[..., :3], atol=1e-7)
        np.testing.assert_array_equal(material.textures[key][..., 3], 1)
    else:
        np.testing.assert_allclose(material.textures[key][..., 0], expected[..., channel], atol=1e-7)


@pytest.mark.parametrize('mode,space,expected', [
    ('RGB', 'auto', .2158605), ('RGB', 'raw', 128/255), ('RGB', 'sRGB', .2158605),
    ('L', 'auto', 128/255), ('L', 'sRGB', .2158605), ('LA', 'auto', 128/255),
])
def test_texture_color_space_is_independent_of_surface_input(tmp_path, mode, space, expected):
    scene = Scene(tmp_path)
    path = tmp_path/'source.png'
    Image.new(mode, (2, 2), (128,)*len(mode) if len(mode) > 1 else 128).save(path)
    tex = scene.texture('Tex', np.ones((2, 2, 4)), space=space)
    tex.GetInput('file').Set(str(path))
    scene.connect('roughness', tex, 'g')
    np.testing.assert_allclose(scene.model().pose(None, 0)[0].material.textures['roughnessMap'][..., 0], expected, atol=1e-7)


def pattern():
    y, x = np.mgrid[:16, :16]
    return np.stack([x/15, y/15, ((x//4+y//4)%2)*.8+.1, np.ones_like(x)], -1)


def test_nested_transform_named_indexed_primvar_and_interface_connections(tmp_path):
    scene = Scene(tmp_path)
    values = ST*.7+.1
    pv = scene.uv('detail', values[[2, 0, 3, 1]], 'faceVarying')
    pv.SetIndices([1, 3, 0, 2])
    reader = scene.reader(var='wrong')
    interface = scene.mat.CreateInput('uvName', Sdf.ValueTypeNames.String)
    interface.Set('detail')
    reader.GetInput('varname').ConnectToSource(interface)
    a = scene.transform('First', reader, scale=(.8, .6), angle=90, shift=(.8, .1))
    b = scene.transform('Second', a, scale=(1.2, .7), angle=-90, shift=(.1, .8))
    tex = scene.texture('Tex', pattern(), b)
    # Pass the texture output through a nodegraph output.
    graph = UsdShade.NodeGraph.Define(scene.stage, '/Mat/Graph')
    output = graph.CreateOutput('color', Sdf.ValueTypeNames.Color3f)
    output.ConnectToSource(tex.ConnectableAPI(), 'rgb')
    scene.pbr.GetInput('diffuseColor').ConnectToSource(output)
    item = scene.model().pose(None, 0)[0]
    first = np.c_[-values[:, 1]*.6+.8, values[:, 0]*.8+.1]
    final = np.c_[first[:, 1]*.7+.1, -first[:, 0]*1.2+.8]
    expected_uv = np.c_[final[:, 0], 1-final[:, 1]]
    np.testing.assert_allclose(map_uvs(item, from_spec(item.material))['baseColorMap'], expected_uv, atol=1e-7)
    control = Scene(tmp_path, 'control')
    control.uv('st', final)
    control.connect('diffuseColor', control.texture('Tex', pattern(), control.reader()))
    np.testing.assert_allclose(scene.renderer().rc.render_frame(0).px,
                               control.renderer().rc.render_frame(0).px, atol=2e-6)


@pytest.mark.parametrize('with_reader', [False, True])
def test_constant_and_missing_primvar_fallback_coordinates(tmp_path, with_reader):
    scene = Scene(tmp_path)
    reader = scene.reader(var='missing', fallback=(.25, .75)) if with_reader else None
    tex = scene.texture('Tex', pattern(), reader)
    if not with_reader:
        tex.CreateInput('st', Sdf.ValueTypeNames.Float2).Set((.25, .75))
    scene.connect('diffuseColor', tex)
    item = scene.model().pose(None, 0)[0]
    np.testing.assert_allclose(map_uvs(item, from_spec(item.material))['baseColorMap'], [[.25, .25]]*4)


def test_missing_texture_uses_scaled_fallback_without_border_clipping(tmp_path):
    scene = Scene(tmp_path)
    tex = scene.texture('Tex', pattern(), scene.reader(), wrap='black')
    tex.GetInput('file').Set('absent.png')
    tex.CreateInput('fallback', Sdf.ValueTypeNames.Float4).Set((.2, .4, .6, .8))
    tex.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set((2., 1., .5, .25))
    tex.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set((.1, .2, .3, .4))
    scene.connect('diffuseColor', tex)
    spec = scene.model().pose(None, 0)[0].material
    np.testing.assert_allclose(spec.textures['baseColorMap'][0, 0], [.5, .6, .6, 1.], atol=1e-7)
    assert spec.params['mapSamplers']['baseColorMap']['wrapS'] == 33071


def test_roughness_and_metallic_keep_independent_coordinates(tmp_path):
    actual, control = Scene(tmp_path), Scene(tmp_path, 'control')
    actual.uv('metalUV', np.c_[1-ST[:, 0], ST[:, 1]])
    images = pattern()
    for scene in [actual, control]:
        rough = scene.texture('Rough', images, scene.reader('RoughReader'))
        metal_image = images if scene is actual else images[:, ::-1]
        metal = scene.texture('Metal', metal_image, scene.reader('MetalReader', 'metalUV' if scene is actual else 'st'))
        scene.connect('roughness', rough, 'r')
        scene.connect('metallic', metal, 'g')
    # Vary the metal source in X so selecting the wrong UV set is observable.
    for scene in [actual, control]:
        path = tmp_path/f'{scene.name}-Metal.npy'
        pixels = images.copy(); pixels[..., 1] = images[..., 0]
        np.save(path, pixels if scene is actual else pixels[:, ::-1])
    a = actual.renderer().rc.render_frame(0).px
    b = control.renderer().rc.render_frame(0).px
    assert np.ptp(a[30, 15:65, 0]) > .02
    np.testing.assert_allclose(a, b, atol=3e-6)


def test_animated_uv_transform_bias_and_material_interface_with_backward_seeks(tmp_path):
    scene = Scene(tmp_path)
    transform = scene.transform('Transform', scene.reader())
    transform.GetInput('translation').Set((0., 0.), 0)
    transform.GetInput('translation').Set((.4, .2), 24)
    tex = scene.texture('Tex', pattern(), transform)
    tex.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set((0., 0., 0., 0.), 0)
    tex.GetInput('bias').Set((.2, .1, 0., 0.), 24)
    scene.connect('diffuseColor', tex)
    rough = scene.mat.CreateInput('roughness', Sdf.ValueTypeNames.Float)
    rough.Set(.1, 0); rough.Set(.9, 24)
    scene.pbr.CreateInput('roughness', Sdf.ValueTypeNames.Float).ConnectToSource(rough)
    renderer = scene.renderer(animation=True)
    for i, t in enumerate([.75, .25, 0., .75]):
        control = Scene(tmp_path, f'control{i}')
        control.uv('st', ST+np.array([.4, .2])*t)
        pixels = pattern(); pixels[..., :3] += np.array([.2, .1, 0.])*t
        control.connect('diffuseColor', control.texture('Tex', pixels, control.reader()))
        control.pbr.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(.1+.8*t)
        np.testing.assert_allclose(renderer.rc.render_frame(t).px,
                                   control.renderer().rc.render_frame(0).px, atol=3e-6)


def test_opacity_map_reaches_depth_pass_without_using_diffuse_texture_alpha(tmp_path):
    scene = Scene(tmp_path)
    pixels = np.ones((16, 16, 4)); pixels[:, :8, 3] = 0.
    reader = scene.reader()
    diffuse = scene.texture('Color', np.full((1, 1, 4), [.1, .7, .2, 0.]), reader)
    opacity = scene.texture('Alpha', pixels, reader)
    scene.connect('diffuseColor', diffuse)
    scene.connect('opacity', opacity, 'a')
    scene.pbr.CreateInput('opacityThreshold', Sdf.ValueTypeNames.Float).Set(.5)
    back = '<object3D id="back" primitive="plane" width="64" height="64" z="-10" material="red"/>'
    rendered = scene.renderer(extra=back).rc.render_frame(0).px
    np.testing.assert_allclose(rendered[40, 20], [1., 0., 0., 1.], atol=1e-6)
    assert rendered[40, 60, 1] > rendered[40, 60, 0]


@pytest.mark.parametrize('wrap,expected', [
    (10497, [.5, .5, .5, .5]), (33648, [.5, 0., 1., .5]),
    (33071, [0., 0., 1., 1.]), (33069, [.2, .1, .6, .2]),
])
def test_cpu_displacement_wraps_and_border_footprints(wrap, expected):
    pixels = np.repeat(np.array([[[0.], [1.]]]), 4, -1)
    uv = np.array([[-.5, .5], [0., .5], [1., .5], [1.5, .5]])
    actual = sample_map(pixels, uv, {'wrapS': wrap, 'wrapT': 33071, 'border': (.2,)*4})[:, 0]
    np.testing.assert_allclose(actual, expected, atol=1e-7)


@pytest.mark.parametrize('wrap,expected', [
    (10497, [.5, .5, .5, .5]), (33648, [.5, 0., 1., .5]),
    (33071, [0., 0., 1., 1.]), (33069, [.2, .1, .6, .2]),
])
@pytest.mark.parametrize('axis', [0, 1])
def test_gpu_wraps_and_black_border_filter_footprints(wrap, expected, axis):
    from scenerender.three.materials import Material
    from scenerender.three import renderer as gpu, shaders
    if not gl.available():
        pytest.skip('no GL')
    context = gpu.res().ctx
    pixels = np.repeat(np.array([[[0.], [1.]]], np.float32), 4, -1)
    if axis == 1:
        pixels = pixels.transpose(1, 0, 2)
    sampler = {'wrapS': wrap if axis == 0 else 33071, 'wrapT': wrap if axis == 1 else 33071,
               'minFilter': 9987, 'magFilter': 9729, 'border': (.2,)*4}
    material = Material({'mapSamplers': {'baseColorMap': sampler}}, {'baseColorMap': pixels})
    cache = {}
    texture = gpu._material_texture(context, material, 'baseColorMap', cache)
    program = context.program(vertex_shader='''#version 410
      void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2-1,0,1);}''',
      fragment_shader='''#version 410
      uniform sampler2D image; uniform vec2 uv; uniform ivec4 settings; out vec4 color;
      '''+shaders.MAP_SAMPLE+'''
      void main(){color=mapSample(image,uv,settings,vec4(.2));}''')
    color = context.texture((1, 1), 4, dtype='f4')
    target = context.framebuffer([color])
    vao = context.vertex_array(program, [])
    try:
        target.use(); context.viewport = (0, 0, 1, 1)
        context.disable(context.DEPTH_TEST | context.BLEND | context.CULL_FACE)
        texture.use(0); program['image'] = 0
        program['settings'] = gpu._map_sampler(material, 'baseColorMap')
        actual = []
        for v in [-.5, 0., 1., 1.5]:
            program['uv'] = (v, .5) if axis == 0 else (.5, v)
            vao.render(vertices=3)
            actual.append(np.frombuffer(color.read(), np.float32)[0])
        np.testing.assert_allclose(actual, expected, atol=1e-7)
    finally:
        vao.release(); program.release(); target.release(); color.release(); texture.release()
        gpu.restore_state(gpu.res())


def test_constant_normal_displacement_and_occlusion(tmp_path):
    from scenerender.three.scene import _displace
    scene = Scene(tmp_path)
    scene.pbr.CreateInput('normal', Sdf.ValueTypeNames.Normal3f).Set((.6, 0., .8))
    scene.pbr.CreateInput('displacement', Sdf.ValueTypeNames.Float).Set(7.)
    scene.pbr.CreateInput('occlusion', Sdf.ValueTypeNames.Float).Set(.3)
    item = scene.model().pose(None, 0)[0]
    material = from_spec(item.material)
    np.testing.assert_allclose(material.maps['normalMap'][0, 0], [.8, .5, .9, 1.], atol=1e-7)
    uv = map_uvs(item, material)
    positions, _ = _displace(item.positions, item.normals, uv['displacementMap'], item.indices, material)
    np.testing.assert_allclose(positions[:, 2], 7.)
    assert material.p['occlusion'] == pytest.approx(.3)


@pytest.mark.parametrize('inherited', ['skeleton', 'animation', 'indices', 'joints', 'bind_transform', 'all'])
def test_inherited_skin_bindings_match_explicit_mesh_bindings(tmp_path, inherited):
    from pxr import Gf, UsdSkel
    from test_3d_loaders import USD_SKEL

    def model(name, inheritance):
        path = tmp_path/(name+'.usda')
        path.write_text(USD_SKEL)
        stage = Usd.Stage.Open(str(path))
        parent = UsdGeom.Xform.Define(stage, '/Rig/Bound').GetPrim()
        Sdf.CopySpec(stage.GetRootLayer(), '/Rig/Tri', stage.GetRootLayer(), '/Rig/Bound/Tri')
        stage.RemovePrim('/Rig/Tri')
        mesh = stage.GetPrimAtPath('/Rig/Bound/Tri')
        b = UsdSkel.BindingAPI.Apply(mesh)
        b.CreateJointsAttr(['root/tip', 'root'])
        b.CreateJointIndicesPrimvar(True, 1).Set([0])
        b.CreateJointWeightsPrimvar(True, 1).Set([1.])
        b.CreateGeomBindTransformAttr(Gf.Matrix4d().SetTranslate((.5, .25, 0.)))
        names = {
            'skeleton': ['skel:skeleton'], 'indices': ['primvars:skel:jointIndices', 'primvars:skel:jointWeights'],
            'joints': ['skel:joints'], 'bind_transform': ['primvars:skel:geomBindTransform'],
        }
        props = sum(names.values(), []) if inheritance == 'all' else names.get(inheritance, [])
        UsdSkel.BindingAPI.Apply(parent)
        for prop in props:
            Sdf.CopySpec(stage.GetRootLayer(), mesh.GetPath().AppendProperty(prop),
                         stage.GetRootLayer(), parent.GetPath().AppendProperty(prop))
            mesh.RemoveProperty(prop)
        if inheritance in ('animation', 'all'):
            UsdSkel.BindingAPI.Apply(stage.GetPrimAtPath('/Rig')).CreateAnimationSourceRel().SetTargets(['/Rig/Skel/Anim'])
            stage.GetPrimAtPath('/Rig/Skel').RemoveProperty('skel:animationSource')
        stage.GetRootLayer().Save()
        return load_model(str(path))

    control = model('explicit', None)
    actual = model('inherited', inherited)
    assert actual.skins and actual.clip('default') is not None
    for t in [0., .25, .75, .25]:
        expected = control.pose(control.clip('default'), t)[0]
        result = actual.pose(actual.clip('default'), t)[0]
        np.testing.assert_allclose(result.positions, expected.positions, atol=1e-6)
        np.testing.assert_allclose(result.normals, expected.normals, atol=1e-6)


@pytest.mark.parametrize('channels', [1, 2, 3, 4])
@pytest.mark.parametrize('space', ['auto', 'raw'])
def test_sixteen_bit_texture_precision_and_channel_expansion(tmp_path, channels, space):
    import png
    scene = Scene(tmp_path)
    path = tmp_path/'precision.png'
    values = np.arange(8*channels, dtype=np.uint16).reshape(2, 4, channels)+32768
    with path.open('wb') as stream:
        png.Writer(4, 2, greyscale=channels < 3, alpha=channels in (2, 4), bitdepth=16).write(
            stream, values.reshape(2, -1))
    tex = scene.texture('Tex', pattern(), space=space)
    tex.GetInput('file').Set(str(path))
    scene.connect('emissiveColor', tex)
    scene.connect('opacity', tex, 'a')
    spec = scene.model().pose(None, 0)[0].material
    expected = values.astype(np.float32)/65535
    rgb = np.repeat(expected[..., :1], 3, -1) if channels < 3 else expected[..., :3]
    alpha = expected[..., -1] if channels in (2, 4) else np.ones((2, 4))
    np.testing.assert_allclose(spec.textures['emissiveMap'][..., :3], rgb, atol=1e-8)
    np.testing.assert_allclose(spec.textures['opacityMap'][..., 0], alpha, atol=1e-8)


@pytest.mark.parametrize('gamma,expected', [(1., 128/255), (1/2.2, .2158605), (.6, 128/255)])
def test_auto_color_space_and_wrap_modes_from_png_metadata(tmp_path, gamma, expected):
    import png
    from PIL.PngImagePlugin import PngInfo
    import struct
    scene = Scene(tmp_path)
    path = tmp_path/'metadata.png'
    metadata = PngInfo()
    metadata.add_text('wrapS', 'mirror')
    metadata.add_text('wrapT', 'clamp')
    metadata.add(b'gAMA', struct.pack('>I', round(gamma*100000)))
    Image.new('RGB', (2, 2), (128,)*3).save(path, pnginfo=metadata)
    tex = scene.texture('Tex', pattern(), space='auto', wrap='useMetadata')
    tex.GetInput('file').Set(str(path))
    scene.connect('diffuseColor', tex)
    spec = scene.model().pose(None, 0)[0].material
    np.testing.assert_allclose(spec.textures['baseColorMap'][..., :3], expected, atol=1e-7)
    assert spec.params['mapSamplers']['baseColorMap']['wrapS'] == 33648
    assert spec.params['mapSamplers']['baseColorMap']['wrapT'] == 33071


def test_auto_recognizes_embedded_srgb_profile_on_grayscale_texture(tmp_path):
    from PIL import ImageCms
    scene = Scene(tmp_path)
    path = tmp_path/'profile.png'
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    Image.new('L', (2, 2), 128).save(path, icc_profile=profile)
    tex = scene.texture('Tex', pattern(), space='auto')
    tex.GetInput('file').Set(str(path))
    scene.connect('roughness', tex, 'r')
    spec = scene.model().pose(None, 0)[0].material
    np.testing.assert_allclose(spec.textures['roughnessMap'][..., 0], .2158605, atol=1e-7)


def test_animated_constant_coordinates_use_start_value_and_reuse_image_storage(tmp_path):
    scene = Scene(tmp_path)
    tex = scene.texture('Tex', pattern())
    tex.CreateInput('st', Sdf.ValueTypeNames.Float2).Set((.25, .5), 0)
    tex.GetInput('st').Set((.75, .5), 24)
    scene.connect('diffuseColor', tex)
    model = scene.model()
    initial = model.pose(None, 0)[0]
    pixels = initial.material.textures['baseColorMap']
    np.testing.assert_allclose(map_uvs(initial, from_spec(initial.material))['baseColorMap'], [[.25, .5]]*4)
    for t in [*np.linspace(0, 1, 40), .25, .75, 0.]:
        item = model.pose(model.clip('default'), t)[0]
        assert item.material.textures['baseColorMap'] is pixels
        np.testing.assert_allclose(map_uvs(item, from_spec(item.material))['baseColorMap'],
                                   [[.25+.5*t, .5]]*4, atol=1e-7)
    assert len(initial.material.sampler.__self__.samples) <= 8


@pytest.mark.parametrize('thumbnail', [False, True])
def test_transformed_displacement_matches_baked_geometry_in_both_mesh_paths(tmp_path, thumbnail):
    scene, control = Scene(tmp_path), Scene(tmp_path, 'control')
    for s in [scene, control]:
        points = np.array([[-32, -32, 0], [32, -32, 0], [32, 32, 0], [-32, 32, 0],
                           [-36, -36, -12], [36, 36, 12]], np.float32)
        normals = np.array([[0., 0., 1.]]*4+[[0., 0., 0.]]*2)
        if s is control:
            points[:4, 2] = [9, 7, 3, 5]
            normal = np.array([2/64, 4/64, 1.])
            normals[:4] = normal/np.linalg.norm(normal)
        s.mesh.CreatePointsAttr(points.tolist())
        s.mesh.CreateNormalsAttr(normals.tolist())
        s.uv('st', np.r_[ST*.5+.25, [[0., 0.], [0., 0.]]])
    transform = scene.transform('Transform', scene.reader(), shift=(1., 0.))
    tex = scene.texture('Tex', np.repeat(np.array([[[1.], [2.]], [[3.], [4.]]]), 4, -1),
                        transform, wrap='mirror')
    tex.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set((2.,)*4)
    tex.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set((1.,)*4)
    scene.connect('displacement', tex, 'r')
    def render(s):
        renderer = s.renderer()
        if thumbnail:
            from scenerender.three.scene import mesh_thumbnail
            return mesh_thumbnail(renderer.rc, renderer.rc.doc.ids['m'], 80, 80, 0)
        return renderer.rc.render_frame(0).px
    actual, expected = render(scene), render(control)
    assert np.count_nonzero(actual[..., 3]) > 500
    np.testing.assert_allclose(actual, expected, atol=3e-6)


def test_texture_upload_eviction_preserves_current_maps_and_reuploads_old_pixels():
    from scenerender.three import renderer as gpu
    from scenerender.three.materials import Material
    if not gl.available():
        pytest.skip('no GL')
    context = gpu.res().ctx
    materials = [Material({}, {'baseColorMap': np.full((2, 2, 4), i/140, np.float32)})
                 for i in range(140)]
    cache = {}
    try:
        first = gpu._material_texture(context, materials[0], 'baseColorMap', cache)
        for material in materials[1:-8]:
            gpu._material_texture(context, material, 'baseColorMap', cache)
        current = [gpu._material_texture(context, m, 'baseColorMap', cache) for m in materials[-8:]]
        for texture, material in zip(current, materials[-8:]):
            np.testing.assert_array_equal(np.frombuffer(texture.read(), np.float32).reshape(2, 2, 4),
                                          material.maps['baseColorMap'])
        restored = gpu._material_texture(context, materials[0], 'baseColorMap', cache)
        assert restored is not first and len(cache) <= 128
        np.testing.assert_array_equal(np.frombuffer(restored.read(), np.float32), np.zeros(16))
    finally:
        for _, texture in cache.values():
            texture.release()
