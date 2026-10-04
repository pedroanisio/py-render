"""Particle birth and inherited transforms agree with independently authored scenes."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.nodes.particles import get_emitter, particles_at
from scenerender.render import Renderer


def scene(tmp_path, body, *, name='scene', symbols='', fields='', scale=1):
    path = tmp_path/(name+'.xml')
    path.write_text('<scene version="1.1"><project width="160" height="120" duration="4" fps="10" '
                    'linearLight="false"/>'+symbols+'<composition>'+body+'</composition>'
                    '<physics gravityY="0">'+fields+'</physics></scene>')
    return Renderer.open(str(path), strict=True, scale=scale)


def state(r, t, ref='p'):
    node, ctx = r.rc.ev.reference(ref, None, Ctx(t, t))
    loc = r.rc.node_location(node, ctx)
    em = get_emitter(loc.rc, node, ctx)
    p = particles_at(loc.rc, node, ctx)
    assert len(em.transforms) <= 8
    return np.stack([p[k] for k in ('x', 'y', 'vx', 'vy')], axis=-1)


def particle(attrs='', children='', bursts=(0,)):
    return '<particleEmitter id="p" rate="0" lifetime="4" size="3" '+attrs+'>'+children+''.join(
        f'<burst time="{t}" count="1"/>' for t in bursts)+'</particleEmitter>'


@pytest.mark.parametrize('link', ['attribute', 'expression', 'constraint_world', 'constraint_local'])
@pytest.mark.parametrize('motion', ['stationary', 'translation', 'rotation'])
def test_referenced_parent_matches_nested_parent_for_collisions_and_fields(tmp_path, link, motion):
    transform = 'x="40"'
    animation = {'stationary': '', 'translation': '<expression property="x">40+10*time</expression>',
                 'rotation': '<expression property="rotation">15*time</expression>'}[motion]
    anchor = f'<shape id="anchor" shape="rect" width="1" height="1" visible="false" {transform}>{animation}</shape>'
    attrs, children = '', ''
    if link == 'attribute':
        attrs = 'parent="anchor"'
    elif link == 'expression':
        children = '<expression property="parent">"anchor"</expression>'
    else:
        children = f'<transformConstraint type="parent" target="anchor" space="{link.split("_")[1]}"/>'
    common = 'x="10" y="50" speed="30" direction="0" collide="true" bounce="1"'
    wall = '<shape id="wall" shape="rect" x="60" y="10" width="3" height="100"><rigidBody type="static"/></shape>'
    field = '<forceField id="f" type="vortex" x="60" y="50" radius="40" strength="8" affects="particles"/>'
    r = scene(tmp_path, anchor+wall+particle(common+' '+attrs, children), fields=field)
    control = scene(tmp_path, wall+f'<group id="g" {transform}>{animation}'+particle(common)+'</group>',
                    fields=field, name='control')
    for t in (.6, .2, .8, .6):
        np.testing.assert_allclose(state(r, t), state(control, t), atol=1e-7)
        np.testing.assert_allclose(r.frame_linear(t), control.frame_linear(t), atol=1/255)
        np.testing.assert_allclose(state(r, t), state(Renderer.open(r.doc.path, strict=True), t), atol=1e-7)


@pytest.mark.parametrize('parented', [False, True])
def test_parent_motion_is_included_in_force_field_drag(tmp_path, parented):
    body = particle('x="40" y="50" speed="0"'+(' parent="anchor"' if parented else ''))
    motion = '<expression property="x">10+30*time</expression>'
    if parented:
        body = '<shape id="anchor" shape="rect" width="1" height="1" visible="false">'+motion+'</shape>'+body
    else:
        body = '<group id="g">'+motion+body+'</group>'
    field = '<forceField id="f" type="drag" strength="2" affects="particles"/>'
    r = scene(tmp_path, body, fields=field)
    control = scene(tmp_path, particle('x="50" y="50" speed="30" direction="0"'), name='control', fields=field)
    for t in (.5, .2, .8, .5):
        actual = state(r, t).copy()
        actual[:, 0] += 10+30*t
        actual[:, 2] += 30
        np.testing.assert_allclose(actual, state(control, t), atol=1e-8)
        np.testing.assert_allclose(r.frame_linear(t), control.frame_linear(t), atol=1/255)


@pytest.mark.parametrize('source', ['width', 'layout', 'align', 'anchor', 'motion_path'])
@pytest.mark.parametrize('clock_scale', [1, 2])
def test_birth_transform_samples_ancestor_box_layout_and_indirect_inputs(tmp_path, source, clock_scale):
    attrs, children = 'x="50%" y="30" speed="0"', ''
    group, before = 'width="80" height="80"', ''
    changes = '<expression property="width">80+40*time</expression>'
    expected = [[40, 30, 0, 0], [40+10/clock_scale, 30, 0, 0]]
    if source == 'layout':
        attrs, group = 'y="30" speed="0"', 'layout="row" width="140" height="80"'
        changes = '<expression property="gap">10+20*time</expression>'
        before = '<shape id="spacer" shape="rect" width="20" height="1" visible="false"/>'
        expected = [[30, 30, 0, 0], [30+10/clock_scale, 30, 0, 0]]
    elif source == 'align':
        attrs = 'alignX="right" y="30" speed="0"'
        expected = [[80, 30, 0, 0], [80+20/clock_scale, 30, 0, 0]]
    elif source == 'anchor':
        # CONVENTIONS 1.2: a percent anchor refers to the PARENT box, so the former "50% of the emitter
        # width" anchor is spelled out as the same length: 50% of (20+20*time) = 10+10*time
        attrs, changes = 'x="50" y="30" speed="0" emitterWidth="20" emitterShape="point"', ''
        children = ('<expression property="emitterWidth">20+20*time</expression>'
                    '<expression property="anchorX">10+10*time</expression>')
        expected = [[40, 30, 0, 0], [35, 30, 0, 0]]
    elif source == 'motion_path':
        attrs, changes = 'speed="0"', ''
        children = '<motionPath path="M10 30 L90 30" start="0" end="2"/>'
        expected = [[10, 30, 0, 0], [30, 30, 0, 0]]
    body = f'<group id="g" timeScale="{clock_scale}" {group}>{changes}{before}'+particle(attrs, children, (0, .5))+'</group>'
    r = scene(tmp_path, body)
    for t in (.8, .2, 1., .8):
        comp = t/clock_scale
        np.testing.assert_allclose(state(r, comp), expected[:2 if t >= .5 else 1], atol=1e-8)
        control_body = ''.join(particle(f'x="{p[0]}" y="{p[1]}" speed="0"', bursts=(0,)).replace(
            'id="p"', f'id="p{i}"') for i, p in enumerate(expected[:2 if t >= .5 else 1]))
        control = scene(tmp_path, control_body, name='control')
        np.testing.assert_allclose(r.frame_linear(comp), control.frame_linear(t), atol=1/255)
        np.testing.assert_array_equal(state(r, comp), state(Renderer.open(r.doc.path, strict=True), comp))


@pytest.mark.parametrize('kind', ['copy-position', 'follow-path', 'look-at'])
def test_nonparent_constraints_move_births_and_leave_existing_particles_behind(tmp_path, kind):
    target = '<shape id="target" shape="rect" width="1" height="1" y="30" visible="false">' \
             '<expression property="x">20+40*time</expression></shape>'
    attrs = 'speed="0"'
    if kind == 'copy-position':
        constraint = '<transformConstraint type="copy-position" target="target"/>'
        expected = [[20, 30, 0, 0], [40, 30, 0, 0]]
    elif kind == 'follow-path':
        constraint = '<transformConstraint type="follow-path" path="M20 30 L100 30">' \
                     '<expression property="progress">time/2</expression></transformConstraint>'
        expected = [[20, 30, 0, 0], [40, 30, 0, 0]]
    else:
        target = '<shape id="target" shape="rect" width="1" height="1" x="40" visible="false">' \
                 '<expression property="y">20+40*time</expression></shape>'
        attrs = 'x="20" y="20" speed="20" direction="0"'
        constraint = '<transformConstraint type="look-at" target="target"/>'
        expected = None
    r = scene(tmp_path, target+particle(attrs, constraint, (0, .5)))
    for t in (.8, .2, 1., .8):
        actual = state(r, t)
        if expected is None:
            v = 20/np.sqrt(2)
            want = [[20+20*t, 20, 20, 0]]
            if t >= .5:
                want += [[20+v*(t-.5), 20+v*(t-.5), v, v]]
        else:
            want = expected[:2 if t >= .5 else 1]
        np.testing.assert_allclose(actual, want, atol=1e-8)
        np.testing.assert_allclose(actual, state(Renderer.open(r.doc.path, strict=True), t), atol=1e-8)
        control_body = ''.join(particle(f'x="{p[0]}" y="{p[1]}" speed="0"').replace(
            'id="p"', f'id="p{i}"') for i, p in enumerate(want))
        control = scene(tmp_path, control_body, name='control')
        np.testing.assert_allclose(r.frame_linear(t), control.frame_linear(t), atol=1/255)


def test_births_do_not_require_invertible_outer_instance_canvas(tmp_path):
    content = particle('x="20" y="30" speed="10" direction="0"')
    symbols = '<symbols><symbol id="s" width="160" height="120" duration="3">'+content+'</symbol></symbols>'
    r = scene(tmp_path, '<instance id="i" symbol="s"><expression property="scaleX">time &lt; .1 ? 0 : 1</expression></instance>',
              symbols=symbols)
    np.testing.assert_allclose(state(r, .5, 'i/p'), [[25, 30, 10, 0]], atol=1e-8)
    control = scene(tmp_path, content, name='control')
    np.testing.assert_array_equal(r.frame_linear(.5), control.frame_linear(.5))


@pytest.mark.parametrize('scale', [.5, 1., 2.])
def test_cross_instance_parent_override_keeps_target_clock_and_transform(tmp_path, scale):
    anchor = '<shape id="anchor" shape="rect" width="1" height="1" visible="false">' \
             '<expression property="x">20*time</expression></shape>'
    content = anchor+particle('parent="anchor" x="10" y="50" speed="10" direction="0"')
    symbols = '<symbols><symbol id="s" width="160" height="120" duration="4">'+content+'</symbol></symbols>'
    body = '<instance id="a" symbol="s"><override target="p" property="parent" value="b/anchor"/></instance>' \
           '<instance id="b" symbol="s" x="60" speed="2"><override target="p" property="visible" value="false"/></instance>'
    r = scene(tmp_path, body, symbols=symbols, scale=scale)
    control = scene(tmp_path, '<group id="g"><expression property="x">60+40*time</expression>'+
                    particle('x="10" y="50" speed="10" direction="0"')+'</group>', name='control', scale=scale)
    for t in (.6, .2, .8, .6):
        np.testing.assert_allclose(state(r, t, 'a/p'), state(control, t), atol=1e-8)
        np.testing.assert_allclose(r.frame_linear(t), control.frame_linear(t), atol=1/255)


def test_parent_override_matches_nested_control_inside_retimed_symbol(tmp_path):
    anchor = '<shape id="anchor" shape="rect" width="1" height="1" x="40" visible="false"/>'
    wall = '<shape id="wall" shape="rect" x="60" y="10" width="3" height="100"><rigidBody type="static"/></shape>'
    attrs = 'x="10" y="50" speed="30" direction="0" collide="true" bounce="1"'
    symbols = '<symbols><symbol id="s" width="160" height="120" duration="4">'+anchor+wall+particle(attrs)+'</symbol></symbols>'
    r = scene(tmp_path, '<instance id="i" symbol="s" x="10" scaleX=".7" speed="2">' \
              '<override target="p" property="parent" value="anchor"/></instance>', symbols=symbols)
    control = scene(tmp_path, wall+'<group id="g" x="40">'+particle(attrs)+'</group>', name='control')
    for t in (.8, .2, .6, .8):
        np.testing.assert_allclose(state(r, t/2, 'i/p'), state(control, t), atol=1e-8)


@pytest.mark.parametrize('order', ['parent_first', 'parent_last'])
@pytest.mark.parametrize('influence', [1, .5])
def test_constraint_order_separates_birth_motion_from_parent_inheritance(tmp_path, order, influence):
    anchors = '<shape id="anchor" shape="rect" width="1" height="1" visible="false">' \
              '<expression property="x">40+10*time</expression></shape>' \
              '<shape id="target" shape="rect" width="1" height="1" y="30" visible="false">' \
              '<expression property="x">20+40*time</expression></shape>'
    parent = f'<transformConstraint type="parent" target="anchor" influence="{influence}"/>'
    own = '<transformConstraint type="copy-position" target="target"/>'
    constraints = parent+own if order == 'parent_first' else own+parent
    r = scene(tmp_path, anchors+particle('speed="0"', constraints, (0, .5)))
    parent_x = f'{40*influence}+{10*influence}*time'
    own_x = f'20+40*time-({parent_x})' if order == 'parent_first' else '20+40*time'
    body = f'<group id="g"><expression property="x">{parent_x}</expression>'+particle(
        'speed="0" y="30"', f'<expression property="x">{own_x}</expression>', (0, .5))+'</group>'
    control = scene(tmp_path, body, name='control')
    for t in (.8, .2, .6, .8):
        np.testing.assert_allclose(state(r, t), state(control, t), atol=1e-8)
        np.testing.assert_allclose(r.frame_linear(t), control.frame_linear(t), atol=1/255)
