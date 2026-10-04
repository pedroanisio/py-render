"""Projected reference sources agree with independent full source renders."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.render import Renderer


def scene(tmp_path, content, *, name='scene', assets='', effects='', instanced=False, scale=1):
    symbols = ''
    if instanced:
        symbols = '<symbols><symbol id="s" width="160" height="120" duration="4">'+content+'</symbol></symbols>'
        content = '<instance id="i" symbol="s" speed="2" x="10" y="4" rotation="7" scaleX=".8"/>'
    path = tmp_path/(name+'.xml')
    path.write_text('<scene version="1.1"><project width="160" height="120" fps="10" duration="4" linearLight="false"/>'
                    +assets+symbols+'<composition>'+content+'</composition>'+effects+'</scene>')
    return Renderer.open(str(path), strict=True, scale=scale)


def source(kind):
    assets = ''
    color = '#80ff80'
    if kind == 'particle':
        node = f'<particleEmitter id="mask" x="60" y="45" speed="10" direction="0" size="24" shape="square" color="{color}" lifetime="4" rate="0"><burst time="0" count="1"/></particleEmitter>'
    elif kind in ('layer', 'text'):
        asset = (f'<generator id="image" kind="solid" width="24" height="30" paint="{color}"/>' if kind == 'layer' else
                 f'<text id="image" font="DejaVu Sans" size="18" width="40" height="30" color="{color}" text="Hi"/>')
        assets = '<assets>'+asset+'</assets>'
        node = '<layer id="mask" asset="image" x="45" y="25" opacity=".8"/>'
    else:
        attrs = 'shape="rect"' if kind != 'path' else 'shape="path" path="M0 0 L24 0 L20 30 L4 24 Z"'
        node = f'<shape id="mask" {attrs} x="45" y="25" width="24" height="30" fill="{color}" opacity=".8">' \
               '<expression property="x">45+10*time</expression></shape>'
        if kind == 'group':
            node = '<group id="mask" opacity=".8"><mask type="rect" width="68" height="120"/>'+node.replace('id="mask"', 'id="part"')+'</group>'
    return node, assets


@pytest.mark.parametrize('kind', ['shape', 'path', 'layer', 'text', 'particle', 'group'])
@pytest.mark.parametrize('placement', ['flat', 'nested', 'collapsed'])
@pytest.mark.parametrize('projection', ['orthographic', 'perspective'])
@pytest.mark.parametrize('instanced', [False, True])
def test_source_camera_canvas_matches_full_render(tmp_path, kind, placement, projection, instanced):
    node, assets = source(kind)
    if placement == 'nested':
        node = '<group id="inner" x="4" y="3" scaleX="1.1" scaleY=".9">'+node+'</group>'
    collapse = 'collapse="true"' if placement == 'collapsed' else ''
    camera = f'<camera id="camera" projection="{projection}" orthoHeight="240" fov="90" x="80" y="60" z="-160"/>'
    body = camera+f'<group id="g" threeD="true" rotationY="25" timeScale="1.3" {collapse}>'+node+'</group>'
    scale = 2 if instanced else .5
    control = scene(tmp_path, body, name='control', assets=assets, instanced=instanced, scale=scale)
    consumer = '<shape id="consumer" shape="rect" width="160" height="120" matte="mask"/>'
    r = scene(tmp_path, body+consumer, assets=assets, instanced=instanced, scale=scale)
    for t in (.3, .1, .3):
        expected = control.frame_linear(t)
        assert expected[..., 3].max() > .1
        for mode in ('alpha', 'luma'):
            r.doc.ids['consumer'].set('matteMode', mode)
            actual = r.frame_linear(t)
            coverage = expected[..., 3] if mode == 'alpha' else expected[..., :3] @ np.array([.2126, .7152, .0722])
            np.testing.assert_allclose(actual[..., 3], coverage, atol=1e-7)
            np.testing.assert_allclose(actual[..., :3], np.repeat(coverage[..., None], 3, -1), atol=1e-7)


@pytest.mark.parametrize('optics', ['lens', 'dof'])
@pytest.mark.parametrize('instanced', [False, True])
def test_source_inherits_group_camera_optics(tmp_path, optics, instanced):
    extra = 'lensDistortion=".3"' if optics == 'lens' else 'depthOfField="true" focusDistance="500" fStop=".5"'
    camera = f'<camera id="camera" projection="perspective" fov="90" x="80" y="60" z="-160" {extra}/>'
    node, _ = source('shape')
    body = camera+'<group id="g" threeD="true" rotationY="25">'+node+'</group>'
    control = scene(tmp_path, body, name='control', instanced=instanced)
    r = scene(tmp_path, body+'<shape id="consumer" shape="rect" width="160" height="120" matte="mask"/>', instanced=instanced)
    for t in (.3, .1, .3):
        np.testing.assert_allclose(r.frame_linear(t)[..., 3], control.frame_linear(t)[..., 3], atol=1e-7)


def test_source_appearance_is_evaluated_before_parent_compositing(tmp_path):
    # Referencing a child must not acquire its parent's later opacity/matte.
    # In particular a group's own matte can be supplied by one of its children.
    node, _ = source('shape')
    camera = '<camera id="camera" projection="perspective" fov="90" x="80" y="60" z="-160"/>'
    body = camera+'<group id="g" threeD="true" rotationY="25" opacity=".2" matte="mask">'+node+'</group>'
    consumer = '<shape id="consumer" shape="rect" width="160" height="120" matte="mask"/>'
    r = scene(tmp_path, body+consumer)
    control = scene(tmp_path, body.replace(' opacity=".2" matte="mask"', ''), name='control')
    np.testing.assert_allclose(r.frame_linear(.3)[..., 3], control.frame_linear(.3)[..., 3], atol=1e-7)


@pytest.mark.parametrize('kind', ['difference-key', 'transition'])
def test_projected_source_is_shared_by_effect_and_transition_inputs(tmp_path, kind):
    camera = '<camera id="camera" projection="orthographic" orthoHeight="240" x="80" y="60" z="-160"/>'
    source = camera+'<group id="g" threeD="true"><shape id="mask" shape="rect" x="40" y="20" width="40" height="60"/></group>'
    # Keep the source out of the primary picture while retaining its reference.
    hidden = '<shape id="hider" shape="rect" width="1" height="1" opacity="0" matte="mask"/>'
    if kind == 'difference-key':
        consumer = '<shape id="consumer" shape="rect" width="160" height="120" effects="key"/>'
        effects = '<effects><effect id="key" type="difference-key" source="mask" tolerance=".1" softness="0"/></effects>'
        r = scene(tmp_path, source+hidden+consumer, effects=effects)
        coverage = scene(tmp_path, source, name='control').frame_linear(.2)[..., 3]
        np.testing.assert_allclose(r.frame_linear(.2)[..., 3], 1-coverage, atol=1e-7)
    else:
        members = '<shape id="a" shape="rect" width="160" height="120"/><shape id="b" shape="rect" width="160" height="120"/>'
        transition = '<transition id="tr" type="luma" from="a" to="b" duration="1" matte="mask"/>'
        r = scene(tmp_path, source+members+transition)
        from scenerender.transitions import matte_rgba
        expected = scene(tmp_path, source, name='control').frame_linear(.2)
        np.testing.assert_allclose(matte_rgba(r.rc, r.doc.ids['tr'], Ctx(.2, .2)), expected, atol=1e-7)


@pytest.mark.parametrize('moving', [False, True])
@pytest.mark.parametrize('instanced', [False, True])
@pytest.mark.parametrize('scale', [.5, 2])
def test_projected_collider_matte_matches_analytic_affine_source(tmp_path, moving, instanced, scale):
    camera = '<camera id="camera" projection="orthographic" orthoHeight="240" x="80" y="60" z="-160"/>'
    animation = '<expression property="x">80+40*time</expression>' if moving else ''
    source = '<group id="g" threeD="true"><shape id="mask" shape="rect" x="80" y="0" width="20" height="100">'+animation+'</shape></group>'
    control_source = '<shape id="mask" shape="rect" x="80" y="30" width="10" height="50">' \
                     +('<expression property="x">80+20*time</expression>' if moving else '')+'</shape>'
    wall = '<shape id="wall" shape="rect" x="82" y="40" width="5" height="20" matte="mask"><rigidBody type="static"/></shape>'
    particle = '<particleEmitter id="p" x="60" y="50" speed="60" direction="0" rate="0" lifetime="4" collide="true" bounce="1"><burst time="0" count="1"/></particleEmitter>'
    r = scene(tmp_path, camera+source+wall+particle, instanced=instanced, scale=scale)
    control = scene(tmp_path, control_source+wall+particle, name='control', instanced=instanced, scale=scale)
    from scenerender.nodes.particles import get_emitter, particles_at
    def state(renderer, time):
        node, ctx = renderer.rc.ev.reference('i/p' if instanced else 'p', None, Ctx(time, time))
        loc = renderer.rc.node_location(node, ctx)
        em = get_emitter(loc.rc, node, ctx)
        out = particles_at(loc.rc, node, ctx)
        assert len(em.colliders) <= 2
        return np.stack([out[k] for k in ('x', 'y', 'vx', 'vy')], -1)
    for local in (.6, .2, .8, .6):
        t = local/2 if instanced else local
        np.testing.assert_allclose(r.frame_linear(t), control.frame_linear(t), atol=1e-7)
        np.testing.assert_allclose(state(r, t), state(control, t), atol=1e-7)
        fresh = Renderer.open(r.doc.path, strict=True, scale=scale)
        np.testing.assert_allclose(state(r, t), state(fresh, t), atol=1e-7)
    assert state(r, .3 if instanced else .6)[0, 2] == (-60 if not moving else 60)


@pytest.mark.parametrize('placement', ['instance', 'nested', 'group'])
@pytest.mark.parametrize('fit', ['none', 'fill', 'contain'])
@pytest.mark.parametrize('projection', ['orthographic', 'perspective'])
def test_source_projection_crosses_instance_boundaries(tmp_path, placement, fit, projection):
    camera = f'<camera id="camera" projection="{projection}" orthoHeight="240" fov="90" x="80" y="60" z="-160"/>'
    leaf, _ = source('shape')
    symbols = '<symbol id="s" width="160" height="120" duration="4">'+leaf+'</symbol>'
    target = 'i/mask'
    outer = 's'
    if placement == 'nested':
        symbols += '<symbol id="outer" width="160" height="120" duration="4"><instance id="n" symbol="s" speed="1.5" x="5" fit="fill" boxWidth="120" boxHeight="80"/></symbol>'
        target, outer = 'i/n/mask', 'outer'
    instance = f'<instance id="i" symbol="{outer}" speed="2" threeD="true" rotationY="25" fit="{fit}" boxWidth="120" boxHeight="100"/>'
    if placement == 'group':
        instance = '<group id="g" threeD="true" rotationY="-15" x="5">'+instance+'</group>'
    consumer = '<shape id="consumer" shape="rect" width="160" height="120"><expression property="matte">"'+target+'"</expression></shape>'
    renderers = []
    for name, content in [('scene', camera+instance+consumer), ('control', camera+instance)]:
        path = tmp_path/(name+'.xml')
        path.write_text('<scene version="1.1"><project width="160" height="120" duration="4" fps="10" linearLight="false"/>'
                        '<symbols>'+symbols+'</symbols><composition>'+content+'</composition></scene>')
        renderers.append(Renderer.open(str(path), strict=True, scale=1.5))
    r, control = renderers
    for t in (.3, .1, .3):
        expected = control.frame_linear(t)[..., 3]
        assert expected.max() > .1
        np.testing.assert_allclose(r.frame_linear(t)[..., 3], expected, atol=1e-7)


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('optics', ['lens', 'dof'])
def test_projected_instance_optics_use_its_enclosing_camera(tmp_path, nested, optics):
    extra = 'lensDistortion=".3"' if optics == 'lens' else 'depthOfField="true" focusDistance="500" fStop=".5"'
    camera = f'<camera id="camera" projection="perspective" fov="90" x="80" y="60" z="-160" {extra}/>'
    leaf, _ = source('shape')
    # A different inner camera must not replace the outer instance's optics.
    symbols = '<symbol id="s" width="160" height="120" duration="4"><camera id="inner-camera" projection="orthographic" orthoHeight="1000"/>'+leaf+'</symbol>'
    target, outer = 'i/mask', 's'
    if nested:
        symbols += '<symbol id="outer" width="160" height="120" duration="4"><instance id="n" symbol="s" speed="1.5"/></symbol>'
        target, outer = 'i/n/mask', 'outer'
    instance = f'<instance id="i" symbol="{outer}" speed="2" threeD="true" rotationY="25"/>'
    consumer = '<shape id="consumer" shape="rect" width="160" height="120"><expression property="matte">"'+target+'"</expression></shape>'
    renderers = []
    for name, content in [('scene', camera+instance+consumer), ('control', camera+instance)]:
        path = tmp_path/(name+'.xml')
        path.write_text('<scene version="1.1"><project width="160" height="120" duration="4" fps="10" linearLight="false"/>'
                        '<symbols>'+symbols+'</symbols><composition>'+content+'</composition></scene>')
        renderers.append(Renderer.open(str(path), strict=True))
    r, control = renderers
    for t in (.3, .1, .3):
        np.testing.assert_allclose(r.frame_linear(t)[..., 3], control.frame_linear(t)[..., 3], atol=1e-7)
