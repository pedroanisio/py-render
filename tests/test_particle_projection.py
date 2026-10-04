"""Projected collider motion agrees with affine controls and pinhole geometry."""
import math

import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.nodes.particle_collisions import snapshots
from scenerender.nodes.particles import get_emitter, particles_at
from scenerender.render import Renderer


WALL = '<shape id="wall" shape="rect" x="55" y="20" width="10" height="60"><rigidBody type="static"/></shape>'
PARTICLE = '<particleEmitter id="p" x="30" y="50" speed="100" direction="0" rate="0" lifetime="4" collide="true" bounce="1"><burst time="0" count="1"/></particleEmitter>'


def scene(tmp_path, body, *, name='scene', instanced=False, scale=1):
    symbols = ''
    if instanced:
        symbols = '<symbols><symbol id="s" width="160" height="120" duration="4">'+body+'</symbol></symbols>'
        body = '<instance id="i" symbol="s" speed="2" x="12" rotation="15" scaleX=".8"/>'
    path = tmp_path/(name+'.xml')
    path.write_text('<scene version="1.1"><project width="160" height="120" fps="10" duration="4" linearLight="false"/>'
                    +symbols+'<composition>'+body+'</composition><physics gravityY="0"/></scene>')
    return Renderer.open(str(path), strict=True, scale=scale)


def emitter(r, t, instanced=False):
    ref = 'i/p' if instanced else 'p'
    node, ctx = r.rc.ev.reference(ref, None, Ctx(t/2 if instanced else t, t/2 if instanced else t))
    loc = r.rc.node_location(node, ctx)
    return get_emitter(loc.rc, node, ctx), ctx


def state(r, t, instanced=False):
    em, ctx = emitter(r, t, instanced)
    p = particles_at(em.rc, em.el, ctx)
    return np.stack([p[k] for k in ('x', 'y', 'vx', 'vy')], axis=-1)


def collider(r, t, instanced=False):
    em, ctx = emitter(r, t, instanced)
    return snapshots(em, ctx.t)[r.doc.ids['wall']]


def body(placement, angle=0):
    if placement == 'leaf':
        return WALL.replace('shape="rect"', f'shape="rect" threeD="true" rotationY="{angle}"').replace(
            '<rigidBody', '<expression property="x">55+30*time</expression><rigidBody')
    collapse = 'collapse="true"' if placement == 'collapsed' else ''
    wall = ('<group id="inner" x="7" y="3" scaleX="1.2" scaleY=".8">'+WALL+'</group>'
            if placement == 'nested' else WALL)
    return f'<group id="g" threeD="true" rotationY="{angle}" {collapse}>' \
           '<expression property="x">30*time</expression>'+wall+'</group>'


@pytest.mark.parametrize('placement', ['leaf', 'group', 'collapsed'])
@pytest.mark.parametrize('projection', ['orthographic', 'perspective'])
@pytest.mark.parametrize('instanced,scale', [(False, 1), (True, .5), (True, 1), (True, 2)])
def test_parallel_projected_motion_matches_affine_control(tmp_path, placement, projection, instanced, scale):
    camera = f'<camera id="camera" projection="{projection}" orthoHeight="240" fov="90" z="160"/>'
    control_body = '<group id="g" scaleX=".5" scaleY=".5" y="30"><expression property="x">40+15*time</expression>'+WALL+'</group>'
    r = scene(tmp_path, camera+body(placement)+PARTICLE, instanced=instanced, scale=scale)
    control = scene(tmp_path, control_body+PARTICLE, name='control')
    xx, yy = np.meshgrid(np.arange(160)+.5, np.arange(120)+.5)
    for t in (.6, .2, .5, .6):
        np.testing.assert_allclose(state(r, t, instanced), state(control, t), atol=1e-7)
        np.testing.assert_allclose(collider(r, t, instanced).sample(xx, yy), collider(control, t).sample(xx, yy), atol=1e-7)
        fresh = Renderer.open(r.doc.path, strict=True, scale=scale)
        np.testing.assert_allclose(state(r, t, instanced), state(fresh, t, instanced), atol=1e-7)
    assert state(r, .6, instanced)[0, 2] == pytest.approx(-70, abs=1e-7)


@pytest.mark.parametrize('placement', ['leaf', 'group', 'collapsed', 'nested'])
@pytest.mark.parametrize('angle', [-35, 25])
@pytest.mark.parametrize('instanced', [False, True])
def test_projective_local_to_scene_matrix_matches_pinhole_geometry(tmp_path, placement, angle, instanced):
    camera = '<camera id="camera" fov="90" z="160"/>'
    r = scene(tmp_path, camera+body(placement, angle)+PARTICLE, instanced=instanced)
    points = np.array([[0, 0, 1], [10, 0, 1], [10, 60, 1], [0, 60, 1], [3, 17, 1]], float)
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    for t in (.2, .6, .3, .2):
        col = collider(r, t, instanced)
        got = points @ col.matrix.T
        got = got[:, :2]/got[:, 2:]
        u, v = points[:, 0], points[:, 1]+20
        offset = 55 if placement == 'leaf' else 0
        u = u + (0 if placement == 'leaf' else 55)
        if placement == 'nested':
            u, v = u*1.2+7, v*.8+3
        depth = 160+s*u
        expected = np.stack([80+80*(offset+30*t-80+c*u)/depth, 60-80*(60-v)/depth], axis=-1)
        np.testing.assert_allclose(got, expected, atol=1e-9)


@pytest.mark.parametrize('placement', ['leaf', 'group', 'collapsed'])
@pytest.mark.parametrize('angle', [-35, 25])
def test_tilted_plane_contact_velocity_matches_independent_pinhole_derivative(tmp_path, placement, angle):
    r = scene(tmp_path, '<camera id="camera" fov="90" z="160"/>'+body(placement, angle)+PARTICLE)
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    for step in range(1, 61):
        t = step/60
        p = state(r, t)[0]
        if p[2] < 0:
            # Invert x_screen = 80 + 80*(offset + cos(a)*u)/(160 + sin(a)*u).
            # The wall's vertical left edge keeps its normal horizontal. At the
            # returned contact point, its velocity is dx_screen/dt=80*30/depth.
            screen_x = (p[0]-80)/80
            offset = (55 if placement == 'leaf' else 0)+30*t-80
            u = (offset-160*screen_x)/(s*screen_x-c)
            body_speed = 80*30/(160+s*u)
            assert p[2] == pytest.approx(2*body_speed-100, abs=1e-7)
            # Float32 raster resampling perturbs the alpha-gradient normal by
            # a few 1e-8, which produces microunits of tangential velocity.
            assert p[3] == pytest.approx(0, abs=5e-6)
            break
    else:
        pytest.fail('The particle did not contact the projected wall.')


@pytest.mark.parametrize('instanced', [False, True])
@pytest.mark.parametrize('motion', ['translate', 'zoom', 'dolly'])
def test_camera_motion_matches_animated_affine_control(tmp_path, motion, instanced):
    camera_motion = {'translate': '<expression property="x">30*time</expression>',
                     'zoom': '<expression property="orthoHeight">240/(1+time)</expression>',
                     'dolly': '<expression property="z">160+30*time</expression>'}[motion]
    projection = 'perspective' if motion == 'dolly' else 'orthographic'
    camera = f'<camera id="camera" projection="{projection}" orthoHeight="240" fov="90" z="160">'+camera_motion+'</camera>'
    if motion == 'translate':
        attrs = 'scaleX=".5" scaleY=".5" y="30"'
        expressions = '<expression property="x">40-15*time</expression>'
    elif motion == 'zoom':
        attrs = ''
        expressions = '<expression property="scaleX">.5*(1+time)</expression><expression property="scaleY">.5*(1+time)</expression>' \
                      '<expression property="x">40-40*time</expression><expression property="y">30-30*time</expression>'
    else:
        attrs = ''
        expressions = '<expression property="scaleX">80/(160+30*time)</expression><expression property="scaleY">80/(160+30*time)</expression>' \
                      '<expression property="x">80-6400/(160+30*time)</expression><expression property="y">60-4800/(160+30*time)</expression>'
    projected = '<group id="g" threeD="true">'+WALL+'</group>'
    affine = f'<group id="g" {attrs}>'+expressions+WALL+'</group>'
    r = scene(tmp_path, camera+projected+PARTICLE, instanced=instanced)
    control = scene(tmp_path, affine+PARTICLE, name='control')
    for t in (.6, .2, .4, .6):
        np.testing.assert_allclose(state(r, t, instanced), state(control, t), atol=1e-7)


@pytest.mark.parametrize('instanced', [False, True])
@pytest.mark.parametrize('projection', ['orthographic', 'perspective'])
def test_projected_dynamic_body_uses_simulated_render_pose(tmp_path, instanced, projection):
    camera = f'<camera id="camera" projection="{projection}" orthoHeight="240" fov="90" z="160"/>'
    wall = WALL.replace('type="static"', 'type="dynamic" velocityX=".3" linearDamping="0" angularDamping="0"')
    projected = '<group id="g" threeD="true">'+wall+'</group>'
    # Rigid-body velocity uses world m/s, so the affine control halves it to
    # match the camera's 0.5 projection of the physical world's 30 px/s.
    affine = '<group id="g" scaleX=".5" scaleY=".5" x="40" y="30">'+wall.replace('velocityX=".3"', 'velocityX=".15"')+'</group>'
    r = scene(tmp_path, camera+projected+PARTICLE, instanced=instanced)
    control = scene(tmp_path, affine+PARTICLE, name='control')
    for t in (.6, .2, .4, .6):
        np.testing.assert_allclose(state(r, t, instanced), state(control, t), atol=1e-7)
    assert state(r, .6, instanced)[0, 2] == pytest.approx(-70, abs=1e-7)
