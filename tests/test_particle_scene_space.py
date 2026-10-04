"""Symbol simulations survive singular outer display transforms and replay."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.nodes.particles import get_emitter, particles_at
from scenerender.render import Renderer


WALL = '<shape id="wall" shape="rect" x="55" y="20" width="10" height="60"><rigidBody type="static"/></shape>'
PARTICLE = '<particleEmitter id="p" x="30" y="50" speed="100" direction="0" rate="0" lifetime="4" collide="true" bounce="1"><burst time="0" count="1"/></particleEmitter>'


def scene(tmp_path, content, *, name='scene', placement='x', scale=1, fields='', nested=False, definitions=''):
    expression = '<expression property="scaleX">time &lt; .25 ? 0 : .8</expression>'
    attrs, animation = {
        'x': ('scaleX="0"', ''), 'y': ('scaleY="0"', ''),
        'both': ('scaleX="0" scaleY="0"', ''),
        'reappear': ('scaleX=".8"', expression),
        'regular': ('scaleX=".8"', ''),
    }[placement]
    symbols = '<symbol id="s" width="160" height="120" duration="4">'+content+'</symbol>'
    if nested:
        symbols += '<symbol id="outer" width="160" height="120" duration="4"><instance id="inner" symbol="s" speed="2" x="5" scaleX=".9"/></symbol>'
    body = f'<instance id="i" symbol="{"outer" if nested else "s"}" speed="{1 if nested else 2}" x="12" rotation="15" {attrs}>{animation}</instance>'
    path = tmp_path/(name+'.xml')
    path.write_text('<scene version="1.1"><project width="160" height="120" duration="4" fps="10" linearLight="false"/>'
                    '<symbols>'+definitions+symbols+'</symbols><composition>'+body+'</composition><physics gravityY="0">'+fields+'</physics></scene>')
    return Renderer.open(str(path), strict=True, scale=scale)


def state(r, t, ref='i/p'):
    node, ctx = r.rc.ev.reference(ref, None, Ctx(t, t))
    loc = r.rc.node_location(node, ctx)
    em = get_emitter(loc.rc, node, ctx)
    p = particles_at(loc.rc, node, ctx)
    assert len(em.transforms) <= 8 and len(em.collision_transforms) <= 8 and len(em.colliders) <= 3
    return np.stack([p[k] for k in ('x', 'y', 'vx', 'vy')], axis=-1)


@pytest.mark.parametrize('placement', ['x', 'y', 'both', 'reappear'])
@pytest.mark.parametrize('kind', ['plain', 'orthographic', 'tilted'])
@pytest.mark.parametrize('nested,scale', [(False, .5), (True, 2)])
def test_collision_replay_ignores_outer_display_collapse(tmp_path, placement, kind, nested, scale):
    content = WALL+PARTICLE
    if kind != 'plain':
        projection = 'orthographic' if kind == 'orthographic' else 'perspective'
        tilt = '' if kind == 'orthographic' else 'rotationY="25"'
        content = f'<camera id="camera" projection="{projection}" orthoHeight="240" fov="90" z="160"/>' \
                  f'<group id="g" threeD="true" {tilt}>'+content+'</group>'
    r = scene(tmp_path, content, placement=placement, nested=nested, scale=scale)
    control = scene(tmp_path, content, name='control', placement='regular', nested=nested, scale=scale)
    ref = 'i/inner/p' if nested else 'i/p'
    # The first visible frame must replay its preceding collapsed interval.
    frame = r.frame_linear(.3)
    if placement == 'reappear':
        np.testing.assert_allclose(frame, control.frame_linear(.3), atol=1/255)
    else:
        assert not np.any(frame)
    for t in (.3, .1, .4, .3):
        actual = state(r, t, ref)
        np.testing.assert_allclose(actual, state(control, t, ref), atol=1e-7)
        np.testing.assert_allclose(actual, state(Renderer.open(r.doc.path, strict=True, scale=scale), t, ref), atol=1e-7)
    if kind == 'plain':
        np.testing.assert_allclose(state(r, .3, ref), [[19.749999975164727, 50, -100, 0]], atol=1e-7)


@pytest.mark.parametrize('binding', ['parent', 'constraint', 'matte', 'field'])
@pytest.mark.parametrize('nested', [False, True])
def test_local_dependencies_keep_symbol_coordinates_during_collapse(tmp_path, binding, nested):
    content, fields = WALL+PARTICLE, ''
    anchor = '<shape id="anchor" shape="rect" width="1" height="1" visible="false"><expression property="x">5+10*time</expression></shape>'
    if binding == 'parent':
        content = anchor+WALL+PARTICLE.replace('id="p"', 'id="p" parent="anchor"')
    elif binding == 'constraint':
        content = anchor+WALL+PARTICLE.replace('<burst', '<transformConstraint type="parent" target="anchor"/><burst')
    elif binding == 'matte':
        mask = '<shape id="mask" shape="rect" x="50" y="10" width="30" height="90"/>'
        content = mask+WALL.replace('id="wall"', 'id="wall" matte="mask"')+PARTICLE
    else:
        fields = '<forceField id="f" type="drag" strength="2" affects="particles"/>'
    r = scene(tmp_path, content, placement='reappear', nested=nested, fields=fields)
    control = scene(tmp_path, content, name='control', placement='regular', nested=nested, fields=fields)
    ref = 'i/inner/p' if nested else 'i/p'
    np.testing.assert_allclose(r.frame_linear(.3), control.frame_linear(.3), atol=1/255)
    for t in (.3, .1, .4, .3):
        np.testing.assert_allclose(state(r, t, ref), state(control, t, ref), atol=1e-7)


@pytest.mark.parametrize('binding', ['parent', 'matte'])
def test_cross_instance_dependency_cancels_shared_collapsed_outer_instance(tmp_path, binding):
    if binding == 'parent':
        source = PARTICLE.replace('collide="true"', 'collide="false" parent="anchor"')
        target = '<shape id="anchor" shape="rect" width="1" height="1" visible="false"><expression property="x">10*time</expression></shape>'
        override = '<override target="p" property="parent" value="i/b/anchor"/>'
    else:
        source = WALL.replace('id="wall"', 'id="wall" matte="mask"')+PARTICLE
        target = '<shape id="mask" shape="rect" x="50" y="10" width="30" height="90"/>'
        override = '<override target="wall" property="matte" value="i/b/mask"/>'
    definitions = '<symbol id="source" width="160" height="120" duration="4">'+source+'</symbol>' \
                  '<symbol id="target" width="160" height="120" duration="4">'+target+'</symbol>'
    content = '<instance id="a" symbol="source" speed="2">'+override+'</instance><instance id="b" symbol="target" speed="3"/>'
    renderers = []
    for name, placement in [('scene', 'reappear'), ('control', 'regular')]:
        renderers.append(scene(tmp_path, content, name=name, placement=placement, definitions=definitions))
    r, control = renderers
    np.testing.assert_allclose(r.frame_linear(.3), control.frame_linear(.3), atol=1/255)
    for t in (.3, .1, .4, .3):
        np.testing.assert_allclose(state(r, t, 'i/a/p'), state(control, t, 'i/a/p'), atol=1e-7)
        if binding == 'parent':
            node, ctx = r.rc.ev.reference('i/a/p', None, Ctx(t, t))
            loc = r.rc.node_location(node, ctx)
            em = get_emitter(loc.rc, node, ctx)
            # Outer speed 2 and target speed 3 put its anchor at 60*t.
            np.testing.assert_allclose(em.parent_doc(ctx.t), [[1, 0, 60*t], [0, 1, 0], [0, 0, 1]], atol=1e-9)
