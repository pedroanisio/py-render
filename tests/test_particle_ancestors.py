"""Collider contributions use the rendering hierarchy and complete dependencies."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.nodes.particle_collisions import snapshots
from scenerender.nodes.particles import get_emitter, particles_at
from scenerender.render import Renderer


WALL = '<shape id="wall" shape="rect" x="55" y="20" width="10" height="60"><rigidBody type="static"/></shape>'
PARTICLE = '<particleEmitter id="p" x="30" y="50" speed="60" direction="0" rate="0" lifetime="4" collide="true" bounce="1"><burst time="0" count="1"/></particleEmitter>'
PROBE = PARTICLE.replace('<burst time="0"', '<burst time="3"')


def scene(tmp_path, content, *, name='scene', instanced=False, extra='', assets='', scale=1):
    symbols = ''
    if instanced:
        symbols = '<symbols><symbol id="s" width="160" height="120" duration="4">'+content+'</symbol></symbols>'
        content = '<instance id="i" symbol="s" speed="2" x="12" rotation="15" scaleX=".8"/>'
    path = tmp_path/(name+'.xml')
    path.write_text('<scene version="1.1"><project width="160" height="120" fps="10" duration="4" linearLight="false"/>'
                    +assets+symbols+'<composition>'+content+'</composition>'+extra+'<physics gravityY="0"/></scene>')
    return Renderer.open(str(path), strict=True, scale=scale)


def emitter(r, t, instanced=False):
    node, ctx = r.rc.ev.reference('i/p' if instanced else 'p', None, Ctx(t, t))
    loc = r.rc.node_location(node, ctx)
    return get_emitter(loc.rc, node, ctx), ctx


def state(r, t, instanced=False):
    em, ctx = emitter(r, t, instanced)
    p = particles_at(em.rc, em.el, ctx)
    return np.stack([p[k] for k in ('x', 'y', 'vx', 'vy')], axis=-1)


def collider_alpha(r, t, instanced=False, ref='wall'):
    em, ctx = emitter(r, t, instanced)
    col = snapshots(em, ctx.t).get(r.doc.ids[ref])
    xx, yy = np.meshgrid(np.arange(160)+.5, np.arange(120)+.5)
    return col.sample(xx, yy) if col is not None else np.zeros_like(xx)


@pytest.mark.parametrize('instanced', [False, True])
@pytest.mark.parametrize('kind', ['opacity', 'clip', 'mask', 'effect', 'matte', 'nested', 'clock', 'sequence', 'echo', 'adjustment', 'projection'])
def test_ancestor_alpha_matches_independent_full_render(tmp_path, kind, instanced):
    attrs, children, other, extra = '', '', '', ''
    if kind == 'opacity':
        children = '<expression property="opacity">time &lt; .5 ? .7 : .2</expression>'
    elif kind == 'clip':
        attrs = 'clip="true" width="60" height="70"'
    elif kind == 'mask':
        children = '<mask type="rect" width="56" height="120"><expression property="width">56+8*time</expression></mask>'
    elif kind == 'effect':
        attrs = 'effects="fx"'
        extra = '<effects><effect id="fx" type="blur"><expression property="radius">1+3*time</expression></effect></effects>'
    elif kind == 'matte':
        attrs = 'matte="m"'
        other = '<group id="m"><shape id="m1" shape="rect" x="55" y="20" width="6" height="60"/></group>'
    elif kind == 'nested':
        attrs = 'opacity=".9" clip="true" width="64" height="72"'
    elif kind == 'clock':
        attrs = 'timeScale="2" timeOffset=".1"'
        children = '<expression property="opacity">time &lt; .5 ? .7 : 1</expression>'
    elif kind == 'sequence':
        children = '<shape id="spacer" shape="rect" width="1" height="1" end=".3" opacity="0"/>'
    elif kind == 'echo':
        attrs = 'effects="fx" end=".6"'
        children = '<expression property="x">10*time</expression>'
        extra = '<effects><effect id="fx" type="echo" samples="3"><param name="interval" value=".2"/><param name="decay" value="1"/></effect></effects>'
    elif kind == 'adjustment':
        extra = '<effects><effect id="fx" type="blur"><param name="radius" value="3"/></effect></effects>'
    elif kind == 'projection':
        attrs = 'threeD="true" rotationY="25" width="120" height="100" anchorX="60" anchorY="50" x="80" y="60" clip="true"'
    wall = WALL
    if kind in ('nested', 'clock'):
        wall = '<group id="inner" opacity=".8"><expression property="x">3*time</expression>'+WALL+'</group>'
    if kind == 'sequence':
        wall = WALL.replace('width="10"', 'end="1" width="10"')
    if kind == 'adjustment':
        wall += '<adjustment id="adj" effects="fx"/>'
    tag = 'sequence' if kind == 'sequence' else 'group'
    body = f'<{tag} id="g" {attrs}>'+children+wall+f'</{tag}>'+other
    r = scene(tmp_path, body+PROBE, extra=extra, instanced=instanced)
    # Echo intervals are composition seconds, so speed=2 doubles the interval
    # expressed on the standalone control's symbol-local timeline.
    control_extra = extra.replace('name="interval" value=".2"', 'name="interval" value=".4"') if instanced and kind == 'echo' else extra
    control = scene(tmp_path, body, extra=control_extra, name='control')
    for local in (.2, .8, 1.2, .2):
        t = local/2 if instanced else local
        expected = np.clip(control.frame_linear(local)[..., 3], 0, 1)
        if expected.max() < .5:
            expected[:] = 0  # No collision surface survives the contact threshold.
        np.testing.assert_allclose(collider_alpha(r, t, instanced), expected, atol=1e-7)
        em, _ = emitter(r, t, instanced)
        assert len(em.colliders) <= 2


@pytest.mark.parametrize('instanced', [False, True])
@pytest.mark.parametrize('kind', ['opacity', 'nested_opacity', 'clip', 'mask'])
def test_invisible_ancestor_surface_allows_particle_passage(tmp_path, kind, instanced):
    attrs = {'opacity': 'opacity=".2"', 'nested_opacity': 'opacity=".8"',
             'clip': 'clip="true" width="50" height="120"', 'mask': ''}[kind]
    child = WALL
    if kind == 'nested_opacity':
        child = '<group id="inner" opacity=".6">'+child+'</group>'
    elif kind == 'mask':
        child = '<mask type="rect" width="50" height="120"/>'+child
    r = scene(tmp_path, f'<group id="g" {attrs}>'+child+'</group>'+PARTICLE, instanced=instanced)
    control = scene(tmp_path, WALL.replace('shape="rect"', 'shape="rect" opacity=".2"')+PARTICLE, name='control')
    for local in (.8, .2, .6, .8):
        t = local/2 if instanced else local
        np.testing.assert_allclose(state(r, t, instanced), state(control, local), atol=1e-8)
        fresh = Renderer.open(r.doc.path, strict=True)
        np.testing.assert_array_equal(state(r, t, instanced), state(fresh, t, instanced))
    np.testing.assert_allclose(state(r, .4 if instanced else .8, instanced)[0], [78, 50, 60, 0], atol=1e-8)


@pytest.mark.parametrize('dependency', ['shape', 'particle', 'effect'])
def test_siblings_supply_dependencies_without_becoming_colliders(tmp_path, dependency):
    if dependency == 'particle':
        mask = '<particleEmitter id="m1" x="60" y="50" speed="0" size="20" shape="square" rate="0" lifetime="4"><burst time="0" count="1"/></particleEmitter>'
    else:
        mask = '<shape id="m1" shape="rect" x="50" y="40" width="20" height="20"/>'
    mask = '<group id="m">'+mask+'</group>'
    attrs, extra = 'matte="m"', ''
    if dependency == 'effect':
        attrs = 'effects="fx"'
        extra = '<effects><effect id="fx" type="difference-key"><param name="source" value="m"/></effect></effects>'
    body = f'<group id="g" {attrs}>'+WALL+'</group>'+mask
    noise = '<shape id="nonbody" shape="rect" x="5" y="5" width="20" height="20"/>'
    r = scene(tmp_path, '<group id="outer">'+body+noise+PROBE+'</group>', extra=extra)
    control = scene(tmp_path, body, extra=extra, name='control')
    if dependency == 'effect':
        control.rc.exclude = frozenset([control.doc.ids['m']])
    for t in (.8, .2, .8):
        expected = control.frame_linear(t)[..., 3]
        np.testing.assert_allclose(collider_alpha(r, t), expected, atol=1e-7)
        assert collider_alpha(r, t)[10, 10] == 0


@pytest.mark.parametrize('member', ['outgoing', 'incoming'])
def test_transition_retains_body_weight_without_partner_pixels(tmp_path, member):
    a = WALL.replace('width="10"', 'start="0" end="1" width="10"')
    b = '<shape id="b" shape="rect" x="5" y="5" width="20" height="20" start="1" end="2"/>'
    if member == 'incoming':
        a, b = b.replace('start="1" end="2"', 'start="0" end="1"'), a.replace('start="0" end="1"', 'start="1" end="2"')
    first, second = ('wall', 'b') if member == 'outgoing' else ('b', 'wall')
    transition = f'<transition id="tr" type="crossfade" from="{first}" to="{second}" duration="1" curve="linear"/>'
    r = scene(tmp_path, '<group id="g">'+a+b+transition+'</group>'+PROBE)
    # The partner stays in the scheduling tree, with transparent primary paint.
    control = scene(tmp_path, '<group id="g">'+(a+b).replace('id="b"', 'id="b" opacity="0"')+transition+'</group>', name='control')
    for t in (.6, .9, 1.1, 1.4, .6):
        expected = control.frame_linear(t)[..., 3]
        if expected.max() < .5:
            expected[:] = 0
        np.testing.assert_allclose(collider_alpha(r, t), expected, atol=1e-7)


@pytest.mark.parametrize('hidden', ['matte', 'excluded'])
def test_body_hidden_in_composition_has_no_primary_collision_surface(tmp_path, hidden):
    extra = '<shape id="consumer" shape="rect" width="1" height="1" matte="wall"/>' if hidden == 'matte' else ''
    r = scene(tmp_path, '<group id="g">'+WALL+'</group>'+extra+PARTICLE)
    if hidden == 'excluded':
        r.rc.exclude = frozenset([r.doc.ids['g']])
    np.testing.assert_allclose(state(r, .8)[0], [78, 50, 60, 0], atol=1e-8)


@pytest.mark.parametrize('layout', ['row', 'column', 'stack', 'grid'])
def test_nonbody_siblings_keep_their_layout_slots(tmp_path, layout):
    spacer = '<shape id="spacer" shape="rect" width="20" height="20"/>'
    body = f'<group id="g" layout="{layout}" width="140" height="110" gap="5" padding="4" opacity=".8">'+spacer+WALL+'</group>'
    r = scene(tmp_path, body+PROBE)
    control = scene(tmp_path, body.replace('id="spacer"', 'id="spacer" opacity="0"'), name='control')
    for t in (.8, .2, .8):
        np.testing.assert_allclose(collider_alpha(r, t), control.frame_linear(t)[..., 3], atol=1e-7)


@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_layer_body_preserves_nested_masks_at_scene_resolution(tmp_path, scale):
    layer = '<layer id="wall" asset="source" x="55" y="20"><rigidBody type="static"/></layer>'
    assets = '<assets><generator id="source" kind="solid" width="10" height="60" paint="#ffffffff"/></assets>'
    body = '<group id="g" width="60" height="75" clip="true" opacity=".8">' \
           '<group id="inner"><mask type="rect" width="160" height="65"/>'+layer+'</group></group>'
    r = scene(tmp_path, body+PROBE, assets=assets, instanced=True, scale=scale)
    control = scene(tmp_path, body, assets=assets, name='control')
    for local in (.8, .2, .8):
        np.testing.assert_allclose(collider_alpha(r, local/2, True), control.frame_linear(local)[..., 3], atol=1e-7)


def test_multiple_bodies_do_not_share_primary_pixels(tmp_path):
    wall2 = WALL.replace('id="wall"', 'id="wall2"').replace('x="55"', 'x="90"')
    body = '<group id="g" opacity=".8" effects="fx">'+WALL+wall2+'</group>'
    extra = '<effects><effect id="fx" type="blur" radius="2"/></effects>'
    r = scene(tmp_path, body+PROBE, extra=extra)
    for name, other in [('wall', 'wall2'), ('wall2', 'wall')]:
        control = scene(tmp_path, body.replace(f'id="{other}"', f'id="{other}" opacity="0"'), extra=extra, name=name)
        for t in (.8, .2, .8):
            np.testing.assert_allclose(collider_alpha(r, t, ref=name), control.frame_linear(t)[..., 3], atol=1e-7)
