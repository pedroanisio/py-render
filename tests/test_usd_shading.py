"""USD shading conventions, isolated lobe controls and actual rendered material animation."""
import math

import numpy as np
import pytest
from pxr import Sdf

from test_usd_materials import Scene, ST, pattern


FRONT = '<light id="l" type="directional" intensity="1"/>'


def set_input(scene, name, value):
    kind = (Sdf.ValueTypeNames.Token if isinstance(value, str) else
            Sdf.ValueTypeNames.Int if name == 'useSpecularWorkflow' else
            Sdf.ValueTypeNames.Normal3f if name == 'normal' else
            Sdf.ValueTypeNames.Color3f if name.endswith('Color') else Sdf.ValueTypeNames.Float)
    return scene.pbr.CreateInput(name, kind).Set(value)


@pytest.mark.parametrize('specular', [(0., 0., 0.), (.2, .04, .08), (1., .7, .3)])
@pytest.mark.parametrize('ior', [1., 1.5, 3.])
def test_specular_workflow_uses_direct_f0_and_ignores_ior_and_metallic(tmp_path, specular, ior):
    scene = Scene(tmp_path)
    for name, value in {'diffuseColor': (.3, .2, .1), 'specularColor': specular,
                        'useSpecularWorkflow': 1, 'ior': ior, 'metallic': 1., 'roughness': .5}.items():
        set_input(scene, name, value)
    rgb = scene.renderer(lights=FRONT).rc.render_frame(0).px[40, 40, :3]
    f0 = np.array(specular)
    # At normal incidence N=L=V: GGX D=1/(pi*r^4), visibility=1/4.
    expected = np.array([.3, .2, .1])*(1-f0)/math.pi + f0/(4*math.pi*.5**4)
    np.testing.assert_allclose(rgb, expected, rtol=1e-5, atol=1e-6)


@pytest.mark.parametrize('metallic', [0., .4, 1.])
def test_metallic_grazing_reflectance_uses_albedo(tmp_path, metallic):
    scene = Scene(tmp_path)
    # L and V are 120 degrees apart; their half vector is also the authored normal.
    scene.mesh.CreateNormalsAttr([(-math.sqrt(3)/2, 0., .5)]*4)
    set_input(scene, 'diffuseColor', (.8, .15, .05))
    set_input(scene, 'metallic', metallic)
    set_input(scene, 'roughness', .5)
    rgb = scene.renderer(lights='<light id="l" type="directional" yaw="120"/>').rc.render_frame(0).px[40, 40, :3]
    albedo = np.array([.8, .15, .05])
    f0 = .04*(1-metallic)+albedo*metallic
    f90 = 1-metallic+albedo*metallic
    fresnel = f0+(f90-f0)*.5**5
    # NdotL=NdotV=VdotH=.5; NdotH=1.
    distribution = 1/(math.pi*.5**4)
    visibility = .5/math.sqrt(.25*(1-.5**4)+.5**4)
    expected = ((1-fresnel)*albedo*(1-metallic)/math.pi+fresnel*distribution*visibility)*.5
    np.testing.assert_allclose(rgb, expected, rtol=2e-5, atol=1e-6)


@pytest.mark.parametrize('ior', [.8, 1., 1.5, 2.5])
def test_clearcoat_uses_material_ior(tmp_path, ior):
    scene = Scene(tmp_path)
    for name, value in {'diffuseColor': (0., 0., 0.), 'useSpecularWorkflow': 1,
                        'specularColor': (0., 0., 0.), 'ior': ior,
                        'clearcoat': .7, 'clearcoatRoughness': .5}.items():
        set_input(scene, name, value)
    rgb = scene.renderer(lights=FRONT).rc.render_frame(0).px[40, 40, :3]
    expected = .7*((ior-1)/(ior+1))**2/(4*math.pi*.5**4)
    np.testing.assert_allclose(rgb, expected, rtol=1e-5, atol=1e-6)


@pytest.mark.parametrize('axis', [0, 1])
@pytest.mark.parametrize('normal_input', ['constant', 'texture', 'constant_st'])
@pytest.mark.parametrize('coat', [0., 1.])
def test_normal_frame_and_clearcoat_match_authored_geometry_normals(tmp_path, axis, normal_input, coat):
    actual, control = Scene(tmp_path), Scene(tmp_path, 'control')
    normal = [0., 0., .8]; normal[axis] = .6
    for scene in [actual, control]:
        set_input(scene, 'clearcoat', coat)
        set_input(scene, 'clearcoatRoughness', .35)
    if normal_input == 'constant':
        set_input(actual, 'normal', tuple(normal))
    else:
        reader = actual.reader() if normal_input == 'texture' else None
        texture = actual.texture('Normal', np.array([[normal+[1.]]]), reader, wrap='clamp')
        actual.connect('normal', texture)
    control.mesh.CreateNormalsAttr([tuple(normal)]*4)
    # Deliberately oblique light separates +X/+Y from a flipped tangent frame.
    lights = '<light id="l" type="directional" yaw="-25" pitch="35"/>'
    a, b = actual.renderer(lights=lights).rc.render_frame(0).px, control.renderer(lights=lights).rc.render_frame(0).px
    np.testing.assert_allclose(a, b, atol=3e-6)


def test_specular_texture_retains_independent_coordinates_and_animation(tmp_path):
    scene = Scene(tmp_path)
    set_input(scene, 'useSpecularWorkflow', 1)
    scene.uv('specUV', np.c_[1-ST[:, 0], ST[:, 1]])
    transform = scene.transform('Transform', scene.reader(var='specUV'))
    transform.GetInput('translation').Set((0., 0.), 0)
    transform.GetInput('translation').Set((.3, .1), 24)
    texture = scene.texture('Spec', pattern(), transform)
    scene.connect('specularColor', texture)
    set_input(scene, 'metallic', 1.)
    renderer = scene.renderer(animation=True)
    for i, t in enumerate([.75, .25, 0., .75]):
        control = Scene(tmp_path, 'control'+str(i))
        set_input(control, 'useSpecularWorkflow', 1)
        control.uv('st', np.c_[1-ST[:, 0], ST[:, 1]]+np.array([.3, .1])*t)
        control.connect('specularColor', control.texture('Spec', pattern(), control.reader()))
        np.testing.assert_allclose(renderer.rc.render_frame(t).px,
                                   control.renderer().rc.render_frame(0).px, atol=3e-6)


@pytest.mark.parametrize('mode', ['presence', 'transparent', 'default'])
@pytest.mark.parametrize('component', ['diffuse', 'specular', 'emissive', 'clearcoat'])
@pytest.mark.parametrize('light_type', ['directional', 'ambient', 'dome'])
def test_opacity_scales_only_selected_lobes(tmp_path, mode, component, light_type):
    scenes = [Scene(tmp_path, name) for name in ['full', 'half', 'zero']]
    for scene, opacity in zip(scenes, [1., .4, 0.]):
        set_input(scene, 'opacity', opacity)
        set_input(scene, 'diffuseColor', (.3, .5, .7) if component == 'diffuse' else (0., 0., 0.))
        set_input(scene, 'useSpecularWorkflow', 1)
        set_input(scene, 'specularColor', (.2, .3, .5) if component == 'specular' else (0., 0., 0.))
        set_input(scene, 'clearcoat', .6 if component == 'clearcoat' else 0.)
        set_input(scene, 'clearcoatRoughness', .4)
        set_input(scene, 'emissiveColor', (.3, .2, .1) if component == 'emissive' else (0., 0., 0.))
        if mode != 'default':
            set_input(scene, 'opacityMode', mode)
    lights = f'''<light id="l" type="{light_type}" intensity="{0 if component == 'emissive' else 1}"
       affectsDiffuse="{'true' if component == 'diffuse' else 'false'}"
       affectsSpecular="{'false' if component == 'diffuse' else 'true'}"/>'''
    frames = [s.renderer(lights=lights).rc.render_frame(0).px for s in scenes]
    for i, opacity in enumerate([.4, 0.], start=1):
        factor = opacity if component == 'diffuse' or mode == 'presence' else 1.
        np.testing.assert_allclose(frames[i][40, 40, :3], frames[0][40, 40, :3]*factor, atol=1e-6)


@pytest.mark.parametrize('mode', ['presence', 'transparent'])
def test_opacity_threshold_overrides_transparency_mode(tmp_path, mode):
    scene, control = Scene(tmp_path), Scene(tmp_path, 'control')
    for s in [scene, control]:
        set_input(s, 'emissiveColor', (.4, .2, .1))
    set_input(scene, 'opacityMode', mode)
    set_input(scene, 'opacity', .6)
    set_input(scene, 'opacityThreshold', .5)
    np.testing.assert_allclose(scene.renderer().rc.render_frame(0).px,
                               control.renderer().rc.render_frame(0).px, atol=1e-6)


@pytest.mark.parametrize('mode', ['presence', 'transparent'])
@pytest.mark.parametrize('opacity', [0., .4, 1.])
def test_translucent_surface_composites_against_an_opaque_mesh(tmp_path, mode, opacity):
    scene, control = Scene(tmp_path), Scene(tmp_path, 'control')
    for s in [scene, control]:
        set_input(s, 'useSpecularWorkflow', 1)
        set_input(s, 'specularColor', (.2, .2, .2))
        set_input(s, 'emissiveColor', (.05, .1, .2))
        set_input(s, 'opacityMode', mode)
        set_input(s, 'opacity', opacity)
    empty = scene.renderer(lights=FRONT).rc.render_frame(0).px[40, 40]
    back = '<object3D id="back" primitive="plane" width="80" height="80" z="-10" material="red"/>'
    actual = control.renderer(lights=FRONT, under=back).rc.render_frame(0).px[40, 40]
    expected = empty+np.array([1., 0., 0., 1.])*(1-empty[3])
    np.testing.assert_allclose(actual, expected, atol=2e-6)


def test_workflow_and_opacity_mode_switches_with_backward_seeks(tmp_path):
    scene = Scene(tmp_path)
    scene.uv('specUV', np.c_[1-ST[:, 0], ST[:, 1]])
    specular = scene.texture('Spec', pattern(), scene.reader('SpecReader', 'specUV'))
    metallic = scene.texture('Metal', pattern(), scene.reader('MetalReader'))
    scene.connect('specularColor', specular)
    scene.connect('metallic', metallic, 'r')
    set_input(scene, 'opacity', .4)
    workflow = scene.mat.CreateInput('workflow', Sdf.ValueTypeNames.Int)
    workflow.Set(0, 0); workflow.Set(1, 12); workflow.Set(0, 24)
    scene.pbr.CreateInput('useSpecularWorkflow', Sdf.ValueTypeNames.Int).ConnectToSource(workflow)
    mode = scene.pbr.CreateInput('opacityMode', Sdf.ValueTypeNames.Token)
    mode.Set('presence', 0); mode.Set('transparent', 12); mode.Set('presence', 24)
    renderer = scene.renderer(animation=True)
    for i, t in enumerate([.75, .25, .5, 0., .75]):
        control = Scene(tmp_path, f'control{i}')
        active = int(t >= .5)
        set_input(control, 'opacity', .4)
        set_input(control, 'opacityMode', 'transparent' if active else 'presence')
        set_input(control, 'useSpecularWorkflow', active)
        if active:
            control.uv('st', np.c_[1-ST[:, 0], ST[:, 1]])
            control.connect('specularColor', control.texture('Spec', pattern(), control.reader()))
        else:
            control.connect('metallic', control.texture('Metal', pattern(), control.reader()), 'r')
        np.testing.assert_allclose(renderer.rc.render_frame(t).px,
                                   control.renderer().rc.render_frame(0).px, atol=3e-6)


def test_translucent_opacity_map_also_masks_shadow_depth(tmp_path):
    actual, control = Scene(tmp_path), Scene(tmp_path, 'control')
    pixels = np.ones((16, 16, 4)); pixels[:, :8] = 0.
    for s in [actual, control]:
        s.connect('opacity', s.texture('Opacity', pixels, s.reader()), 'r')
        set_input(s, 'opacityMode', 'presence')
    set_input(control, 'opacityThreshold', .5)
    back = '<object3D id="back" primitive="plane" width="80" height="80" z="-10"/>'
    light = '<light id="l" type="directional" yaw="20" castShadow="true" shadowMapSize="128"/>'
    a, b = [s.renderer(lights=light, under=back).rc.render_frame(0).px for s in [actual, control]]
    # Compare fully transparent/opaque regions, excluding the filtered alpha edge.
    np.testing.assert_allclose(a[20:60, 12:28], b[20:60, 12:28], atol=3e-6)
    np.testing.assert_allclose(a[20:60, 52:68], b[20:60, 52:68], atol=3e-6)


@pytest.mark.parametrize('angle,scale', [(90., (1., 1.)), (0., (-1., 1.)),
                                       (0., (1., -1.)), (45., (.5, 2.))])
@pytest.mark.parametrize('axis', [0, 1])
def test_transformed_normal_texture_uses_usd_tangent_orientation(tmp_path, angle, scale, axis):
    scene, control = Scene(tmp_path), Scene(tmp_path, 'control')
    normal = np.array([0., 0., .8]); normal[axis] = .6
    transform = scene.transform('NormalUV', scene.reader(), angle=angle, scale=scale)
    scene.connect('normal', scene.texture('Normal', np.array([[list(normal)+[1.]]]), transform))
    a = math.radians(angle)
    # A unit step in transformed U follows the first column of inverse(R*S).
    tangent = np.array([math.cos(a)/scale[0], -math.sin(a)/scale[1], 0.])
    tangent /= np.linalg.norm(tangent)
    bitangent = np.array([-tangent[1], tangent[0], 0.])*np.sign(scale[0]*scale[1])
    mapped = normal[0]*tangent+normal[1]*bitangent+np.array([0., 0., normal[2]])
    control.mesh.CreateNormalsAttr([tuple(mapped)]*4)
    for s in [scene, control]:
        set_input(s, 'clearcoat', .5)
        set_input(s, 'clearcoatRoughness', .35)
    lights = '<light id="l" type="directional" yaw="-25" pitch="35"/>'
    np.testing.assert_allclose(scene.renderer(lights=lights).rc.render_frame(0).px,
                               control.renderer(lights=lights).rc.render_frame(0).px, atol=4e-6)


@pytest.mark.parametrize('animated', [False, True])
def test_mesh_sidedness_is_preserved_with_shared_animated_materials(tmp_path, animated):
    from pxr import UsdGeom
    scene = Scene(tmp_path)
    scene.mesh.CreateFaceVertexIndicesAttr([3, 2, 1, 0])
    scene.mesh.CreateNormalsAttr([(0., 0., -1.)]*4)
    Sdf.CopySpec(scene.stage.GetRootLayer(), '/Plane', scene.stage.GetRootLayer(), '/Right')
    right = UsdGeom.Mesh(scene.stage.GetPrimAtPath('/Right'))
    for mesh, x in [(scene.mesh, -17), (right, 17)]:
        points = np.asarray(mesh.GetPointsAttr().Get()).copy()
        points[:, :2] *= .35
        points[:, 0] += x
        mesh.CreatePointsAttr(points.tolist())
    right.CreateDoubleSidedAttr(True)
    set_input(scene, 'emissiveColor', (.3, .1, 0.))
    if animated:
        emission = scene.pbr.GetInput('emissiveColor')
        emission.Set((.3, .1, 0.), 0); emission.Set((0., .1, .4), 24)
    renderer = scene.renderer(animation=animated, lights='<light id="dark" type="ambient" intensity="0"/>')
    for t in ([.75, .25, 0., .75] if animated else [0.]):
        px = renderer.rc.render_frame(t).px
        np.testing.assert_allclose(px[40, 23], [0., 0., 0., 0.], atol=1e-7)
        np.testing.assert_allclose(px[40, 57], [.3*(1-t), .1, .4*t, 1.], atol=1e-6)


def test_unbound_double_sided_mesh_keeps_default_material(tmp_path):
    from pxr import UsdShade
    scene, control = Scene(tmp_path), Scene(tmp_path, 'control')
    for s in [scene, control]:
        UsdShade.MaterialBindingAPI(s.mesh).UnbindAllBindings()
    scene.mesh.CreateDoubleSidedAttr(True)
    scene.mesh.CreateFaceVertexIndicesAttr([3, 2, 1, 0])
    scene.mesh.CreateNormalsAttr([(0., 0., -1.)]*4)
    np.testing.assert_allclose(scene.renderer().rc.render_frame(0).px,
                               control.renderer().rc.render_frame(0).px, atol=1e-6)


@pytest.mark.parametrize('mode', ['presence', 'transparent'])
def test_translucent_double_sided_back_faces_match_front_faces(tmp_path, mode):
    scene, control = Scene(tmp_path), Scene(tmp_path, 'control')
    scene.mesh.CreateDoubleSidedAttr(True)
    scene.mesh.CreateFaceVertexIndicesAttr([3, 2, 1, 0])
    scene.mesh.CreateNormalsAttr([(0., 0., -1.)]*4)
    for s in [scene, control]:
        set_input(s, 'opacity', .4)
        set_input(s, 'opacityMode', mode)
        set_input(s, 'emissiveColor', (.1, .2, .3))
    back = '<object3D id="back" primitive="plane" width="80" height="80" z="-10" material="red"/>'
    np.testing.assert_allclose(scene.renderer(under=back).rc.render_frame(0).px,
                               control.renderer(under=back).rc.render_frame(0).px, atol=2e-6)
