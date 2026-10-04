"""Emitter positions and tangents use the same projected parent as drawing."""
import math

import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.nodes.particle_collisions import collision_parent
from scenerender.nodes.particles import get_emitter, particles_at
from scenerender.render import Renderer


WALL = '<shape id="wall" shape="rect" x="55" y="20" width="10" height="60"><rigidBody type="static"/></shape>'
PARTICLE = '<particleEmitter id="p" x="30" y="50" speed="100" direction="0" rate="0" lifetime="4" collide="true" bounce="1"><burst time="0" count="1"/></particleEmitter>'


def scene(tmp_path, content, *, name='scene', instanced=False, scale=1):
    symbols = ''
    if instanced:
        symbols = '<symbols><symbol id="s" width="160" height="120" duration="4">'+content+'</symbol></symbols>'
        content = '<instance id="i" symbol="s" speed="2" x="12" rotation="15" scaleX=".8"/>'
    path = tmp_path/(name+'.xml')
    path.write_text('<scene version="1.1"><project width="160" height="120" fps="10" duration="4" linearLight="false"/>'
                    +symbols+'<composition>'+content+'</composition><physics gravityY="0"/></scene>')
    return Renderer.open(str(path), strict=True, scale=scale)


def emitter(r, t, instanced=False):
    t = t/2 if instanced else t
    node, ctx = r.rc.ev.reference('i/p' if instanced else 'p', None, Ctx(t, t))
    loc = r.rc.node_location(node, ctx)
    return get_emitter(loc.rc, node, ctx), ctx


def state(r, t, instanced=False):
    em, ctx = emitter(r, t, instanced)
    p = particles_at(em.rc, em.el, ctx)
    return np.stack([p[k] for k in ('x', 'y', 'vx', 'vy')], axis=-1)


def camera(projection='orthographic', content=''):
    return f'<camera id="camera" projection="{projection}" orthoHeight="240" fov="90" x="80" y="60" z="-160">'+content+'</camera>'


def compare(r, control, *, instanced=False, scale=1):
    for t in (.6, .2, .4, .6):
        np.testing.assert_allclose(state(r, t, instanced), state(control, t, instanced), atol=1e-7)
        comp_t = t/2 if instanced else t
        np.testing.assert_allclose(r.frame_linear(comp_t), control.frame_linear(comp_t), atol=1/255)
        fresh = Renderer.open(r.doc.path, strict=True, scale=scale)
        # Rendering before querying state must not retain an ancestor's flat
        # intermediate-buffer context in subsequent collision snapshots.
        np.testing.assert_allclose(fresh.frame_linear(comp_t), r.frame_linear(comp_t), atol=1e-7)
        np.testing.assert_allclose(state(fresh, t, instanced), state(r, t, instanced), atol=1e-7)
        em, _ = emitter(r, t, instanced)
        assert len(em.collision_transforms) <= 8


@pytest.mark.parametrize('projection', ['orthographic', 'perspective'])
@pytest.mark.parametrize('placement', ['group', 'collapsed', 'nested', 'leaf'])
@pytest.mark.parametrize('instanced,scale', [(False, 1), (True, .5), (True, 2)])
def test_projected_emitter_and_wall_match_affine_scene(tmp_path, projection, placement, instanced, scale):
    content = WALL+PARTICLE
    if placement == 'nested':
        content = '<group id="inner" x="4" y="3" scaleX="1.2" scaleY=".8">'+content+'</group>'
    if placement == 'leaf':
        projected = content.replace('id="wall"', 'id="wall" threeD="true"').replace('id="p"', 'id="p" threeD="true"')
    else:
        collapse = 'collapse="true"' if placement == 'collapsed' else ''
        projected = f'<group id="g" threeD="true" {collapse}>'+content+'</group>'
    affine = '<group id="g" scaleX=".5" scaleY=".5" x="40" y="30">'+content+'</group>'
    r = scene(tmp_path, camera(projection)+projected, instanced=instanced, scale=scale)
    control = scene(tmp_path, affine, name='control', instanced=instanced, scale=scale)
    compare(r, control, instanced=instanced, scale=scale)


@pytest.mark.parametrize('instanced', [False, True])
@pytest.mark.parametrize('collapse', [False, True])
@pytest.mark.parametrize('angle', [-35, 25])
def test_tilted_plane_coordinates_and_reflection_match_pinhole_control(tmp_path, instanced, collapse, angle):
    particle = PARTICLE.replace('y="50"', 'y="60"')
    group = f'<group id="g" threeD="true" rotationY="{angle}" collapse="{str(collapse).lower()}">'+WALL+particle+'</group>'
    r = scene(tmp_path, camera('perspective')+group, instanced=instanced)
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    points = np.array([[30, 60, 1], [55, 60, 1], [80, 20, 1]], float)
    em, _ = emitter(r, .2, instanced)
    M = collision_parent(em, .2)
    screen = points @ M.T
    screen = screen[:, :2]/screen[:, 2:]
    x, y = points[:, 0], points[:, 1]
    depth = 160+s*x
    expected = np.stack([80+80*(-80+c*x)/depth, 60-80*(60-y)/depth], axis=-1)
    np.testing.assert_allclose(screen, expected, atol=1e-9)
    for step in range(1, 61):
        t = step/60
        p = state(r, t, instanced)[0]
        if p[2] < 0:
            # On the optical centre row the screen normal is horizontal, and
            # applying/inverting the contact Jacobian preserves local speed.
            assert p[2] == pytest.approx(-100, abs=1e-7)
            assert p[3] == pytest.approx(0, abs=5e-6)
            assert abs(p[0]-55) < 3
            break
    else:
        pytest.fail('No contact with the wall in the same projected plane.')
    for t in (.6, .2, .4, .6):
        fresh = Renderer.open(r.doc.path, strict=True)
        comp_t = t/2 if instanced else t
        np.testing.assert_allclose(fresh.frame_linear(comp_t), r.frame_linear(comp_t), atol=1e-7)
        np.testing.assert_allclose(state(fresh, t, instanced), state(r, t, instanced), atol=1e-7)


@pytest.mark.parametrize('instanced', [False, True])
@pytest.mark.parametrize('projection', ['orthographic', 'perspective'])
@pytest.mark.parametrize('edge', ['left', 'right', 'floor'])
def test_projected_bounds_match_affine_parent_control(tmp_path, instanced, projection, edge):
    particle = {
        'left': PARTICLE.replace('x="30"', 'x="-60"').replace('speed="100"', 'speed="-100"'),
        'right': PARTICLE.replace('x="30"', 'x="220"'),
        'floor': PARTICLE.replace('y="50"', 'y="150"').replace('direction="0"', 'direction="90"'),
    }[edge]
    projected = '<group id="g" threeD="true">'+particle+'</group>'
    affine = '<group id="g" scaleX=".5" scaleY=".5" x="40" y="30">'+particle+'</group>'
    r = scene(tmp_path, camera(projection)+projected, instanced=instanced)
    control = scene(tmp_path, affine, name='control', instanced=instanced)
    compare(r, control, instanced=instanced)


@pytest.mark.parametrize('instanced', [False, True])
@pytest.mark.parametrize('motion', ['group', 'camera', 'zoom', 'dolly'])
def test_moving_projected_parent_matches_affine_control(tmp_path, instanced, motion):
    cam = camera()
    group_motion = ''
    affine_attrs = 'scaleX=".5" scaleY=".5" y="30"'
    if motion == 'group':
        group_motion = '<expression property="x">30*time</expression>'
        affine_motion = '<expression property="x">40+15*time</expression>'
    elif motion == 'camera':
        cam = camera(content='<expression property="x">80+30*time</expression>')
        affine_motion = '<expression property="x">40-15*time</expression>'
    elif motion == 'zoom':
        cam = camera(content='<expression property="orthoHeight">240/(1+time)</expression>')
        affine_attrs = ''
        affine_motion = '<expression property="scaleX">.5*(1+time)</expression><expression property="scaleY">.5*(1+time)</expression>' \
                        '<expression property="x">40-40*time</expression><expression property="y">30-30*time</expression>'
    else:
        cam = camera('perspective', '<expression property="z">-(160+30*time)</expression>')
        affine_attrs = ''
        affine_motion = '<expression property="scaleX">80/(160+30*time)</expression><expression property="scaleY">80/(160+30*time)</expression>' \
                        '<expression property="x">80-6400/(160+30*time)</expression><expression property="y">60-4800/(160+30*time)</expression>'
    projected = '<group id="g" threeD="true">'+group_motion+WALL+PARTICLE+'</group>'
    affine = f'<group id="g" {affine_attrs}>'+affine_motion+WALL+PARTICLE+'</group>'
    r = scene(tmp_path, cam+projected, instanced=instanced)
    control = scene(tmp_path, affine, name='control', instanced=instanced)
    compare(r, control, instanced=instanced)


def test_camera_culled_emitter_keeps_finite_local_history(tmp_path):
    projected = '<group id="g" threeD="true" zDepth="-300">'+WALL+PARTICLE+'</group>'
    r = scene(tmp_path, camera('perspective')+projected)
    for t in (.6, .2, .6):
        np.testing.assert_allclose(state(r, t)[0], [30+100*t, 50, 100, 0], atol=1e-7)
        assert not r.frame_linear(t).any()


@pytest.mark.parametrize('binding', ['parent', 'constraint', 'layout', 'sequence', 'repeat'])
def test_reconstructed_matrix_matches_actual_traversal_with_bindings(tmp_path, binding):
    particle = PARTICLE.replace('collide="true"', 'collide="false" emitterWidth="10" emitterHeight="10"')
    attrs, ref = '', 'p'
    if binding == 'parent':
        particle = particle.replace('id="p"', 'id="p" parent="anchor"')
    elif binding == 'constraint':
        particle = particle.replace('<burst', '<transformConstraint type="parent" target="anchor" influence=".5"/><burst')
    elif binding == 'layout':
        attrs = 'layout="row" gap="5" padding="4"'
        particle = '<shape id="spacer" shape="rect" width="20" height="10"/>'+particle
    elif binding == 'sequence':
        particle = '<sequence id="sequence"><shape id="spacer" shape="rect" width="1" height="1" end=".2"/>' \
                   +particle.replace('id="p"', 'id="p" end="2"')+'</sequence>'
    elif binding == 'repeat':
        particle = '<repeat id="repeat" count="2" offsetX="15">'+particle+'</repeat>'
        ref = 'p#1'
    anchor = '<shape id="anchor" shape="rect" width="1" height="1" x="7" y="4" rotation="10" visible="false"/>'
    group = f'<group id="g" threeD="true" rotationY="25" width="160" height="120" timeScale="1.5" {attrs}>'+particle+'</group>'
    r = scene(tmp_path, camera('perspective')+anchor+group)
    points = np.array([[0, 0, 1], [1, 2, 1], [10, 10, 1]], float)
    for t in (.8, .3, .8):
        node, ctx = r.rc.ev.reference(ref, None, Ctx(t, t))
        loc = r.rc.node_location(node, ctx)
        em = get_emitter(loc.rc, node, ctx)
        _, drawn = r.rc.render_contribution(node, ctx, r.doc.section('composition'))
        reconstructed = collision_parent(em, ctx.t) @ em.L(ctx.t)
        actual, expected = points @ reconstructed.T, points @ drawn.T
        np.testing.assert_allclose(actual[:, :2]/actual[:, 2:], expected[:, :2]/expected[:, 2:], atol=1e-9)
