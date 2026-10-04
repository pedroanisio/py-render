"""Particle inputs retain their own evaluation and media clocks through caching."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.nodes.particles import get_emitter, particles_at
from scenerender.render import Renderer


def scene(tmp_path, attributes='', children='', *, assets='', name='particles', symbols='', body=None):
    path = tmp_path/(name+'.xml')
    if body is None:
        body = f'<particleEmitter id="p" {attributes}>{children}</particleEmitter>'
    path.write_text('<scene version="1.1"><project width="128" height="96" fps="10" duration="6" '
                    'seed="3" linearLight="false"/>'+assets+symbols+'<composition>'+body+'</composition></scene>')
    return Renderer.open(str(path), strict=True)


def live(renderer, t, ref='p'):
    node, ctx = renderer.rc.ev.reference(ref, None, Ctx(t, t))
    loc = renderer.rc.node_location(node, ctx)
    return particles_at(loc.rc, node, ctx)


def switch(prop, first, second):
    return f'<expression property="{prop}">time &lt; .5 ? \'{first}\' : \'{second}\'</expression>'


def test_animated_emission_path_changes_at_birth_and_survives_backward_seeks(tmp_path):
    r = scene(tmp_path, 'emitterShape="path" rate="0" lifetime="4" speed="0"',
              '<burst time="0" count="3"/><burst time="1" count="3"/>'+
              switch('emitterPath', 'M10 20 L30 20', 'M70 60 L90 60'))
    for t in (1.2, .2, 1.7, .2, 1.2):
        p = live(r, t)
        np.testing.assert_allclose(p['y'][:3], 20)
        if t > 1:
            np.testing.assert_allclose(p['y'][3:], 60)
            assert np.all((p['x'][3:] >= 70) & (p['x'][3:] <= 90))
        fresh = Renderer.open(r.doc.path, strict=True)
        np.testing.assert_array_equal(p['x'], live(fresh, t)['x'])
        np.testing.assert_array_equal(r.frame_linear(t), fresh.frame_linear(t))


DRAW_CASES = [
    ('color', '#FF0000FF', '#0000FFFF'),
    ('colorEnd', '#FFFF00FF', '#00FFFFFF'),
    ('opacityEnd', '.2', '1'),
    ('shape', 'disc', 'square'),
    ('orientToVelocity', 'false', 'true'),
    ('colorCurve', 'linear', 'ease-in'),
    ('sizeCurve', 'linear', 'ease-in'),
    ('spriteCols', '1', '2'),
    ('spriteRows', '1', '2'),
    ('spriteFps', '0', '2'),
    ('sprite', 'first', 'second'),
]


@pytest.mark.parametrize('prop,first,second', DRAW_CASES)
def test_animated_display_properties_match_static_controls(tmp_path, prop, first, second):
    values = dict(x='40', y='40', rate='0', lifetime='4', speed='20', direction='0',
                  size='20', sizeEnd='35', rotation0='35', color='#FF0000FF', colorEnd='#00FFFFFF',
                  shape='square', seed='7')
    assets = ''
    if prop.startswith('sprite'):
        values.update(shape='sprite', sprite='first', color='#FFFFFFFF', colorEnd='#FFFFFFFF')
        if prop == 'spriteFps':
            values['spriteCols'] = '2'
        assets = '<assets><generator id="first" kind="checkerboard" width="16" height="12" scale="7" ' \
                 'paint="#FFFFFFFF" paint2="#00FF00FF"/>' \
                 '<generator id="second" kind="solid" width="16" height="12" paint="#0000FFFF"/></assets>'
    values.pop(prop, None)
    attributes = ' '.join(f'{key}="{value}"' for key, value in values.items())
    burst = '<burst time="0" count="1"/>'
    r = scene(tmp_path, attributes, burst+switch(prop, first, second), assets=assets)
    controls = {value: scene(tmp_path, attributes+f' {prop}="{value}"', burst, assets=assets,
                            name='control-'+str(i)) for i, value in enumerate((first, second))}
    for t in (.2, .8, 1.2, .2, .8):
        value = first if t < .5 else second
        np.testing.assert_array_equal(r.frame_linear(t), controls[value].frame_linear(t))
    assert np.any(controls[first].frame_linear(.8) != controls[second].frame_linear(.8))


def test_animated_sprite_asset_uses_current_time_and_keeps_instances_separate(tmp_path):
    assets = '<assets><generator id="sprite" kind="solid" width="12" height="12">' \
             '<expression property="paint">time &lt; .5 ? \'#FF0000FF\' : \'#0000FFFF\'</expression>' \
             '</generator></assets>'
    particle = '<particleEmitter id="p" x="16" y="16" rate="0" lifetime="5" speed="0" size="12" ' \
               'shape="sprite" sprite="sprite"><burst time="0" count="1"/></particleEmitter>'
    symbols = '<symbols><symbol id="s" width="32" height="32" duration="5">'+particle+'</symbol></symbols>'
    r = scene(tmp_path, assets=assets, symbols=symbols,
              body='<instance id="a" symbol="s" speed="1"/><instance id="b" symbol="s" x="64" speed="2"/>')
    for t in (.3, .7, .3):
        frame = r.frame_linear(t)
        expected_a = [1, 0, 0, 1] if t < .5 else [0, 0, 1, 1]
        np.testing.assert_array_equal(frame[16, 16], expected_a)
        np.testing.assert_array_equal(frame[16, 80], [0, 0, 1, 1])


def test_asset_alpha_samples_subframe_changes_at_birth(tmp_path):
    assets = '<assets><generator id="mask" kind="stripes" width="40" height="10" scale="40" ' \
             'paint="#FFFFFFFF" paint2="#00000000">' \
             '<expression property="evolution">time &lt; .02 ? 0 : .5</expression></generator></assets>'
    r = scene(tmp_path, 'emitterShape="asset-alpha" emitterAsset="mask" rate="0" lifetime="4" speed="0"',
              '<burst time="0" count="10"/><burst time=".03" count="10"/>', assets=assets)
    points = live(r, .2)
    assert np.all(points['x'][:10] > 0)
    assert np.all(points['x'][10:] < 0)


def test_particle_seed_instance_override_matches_authored_control(tmp_path):
    attrs = 'rate="0" lifetime="4" speed="20" speedVariance="10" spread="360" emitterWidth="20" emitterHeight="20"'
    particle = f'<particleEmitter id="p" {attrs}><burst time="0" count="12"/></particleEmitter>'
    symbols = '<symbols><symbol id="s" width="128" height="96" duration="4">'+particle+'</symbol></symbols>'
    r = scene(tmp_path, symbols=symbols, body='<instance id="i" symbol="s"><override target="p" property="seed" value="12"/></instance>')
    control = scene(tmp_path, attrs+' seed="12"', '<burst time="0" count="12"/>', name='control')
    for t in (.7, .2, .7):
        p, q = live(r, t, 'i/p'), live(control, t)
        for key in ('x', 'y', 'vx', 'vy'):
            np.testing.assert_array_equal(p[key], q[key])


def test_births_within_one_step_sample_their_own_properties(tmp_path):
    r = scene(tmp_path, 'emitterShape="path" rate="0" lifetime="4" speed="0"',
              '<burst time="0" count="1"/><burst time=".01" count="1"/>'
              '<expression property="emitterPath">time &lt; .005 ? "M10 20 L30 20" : "M70 60 L90 60"</expression>')
    np.testing.assert_allclose(live(r, .02)['y'], [20, 60])


def test_negative_particle_speed_reverses_direction(tmp_path):
    r = scene(tmp_path, 'rate="0" lifetime="4" x="60" y="40" speed="-20" direction="0"',
              '<burst time="0" count="1"/>')
    p = live(r, .5)
    np.testing.assert_allclose(p['x'], 50)
    np.testing.assert_allclose(p['vx'], -20)


def test_animated_seed_is_sampled_at_birth_and_is_seek_independent(tmp_path):
    from scenerender.physics import hash01
    r = scene(tmp_path, 'rate="0" lifetime="4" speed="0" emitterWidth="20" emitterHeight="20"',
              '<burst time="0" count="3"/><burst time="1" count="3"/>'+switch('seed', '1', '2'))
    el = r.doc.ids['p']
    first = r.rc.ev.seed_for(el, 'particles', Ctx(0, 0))
    second = r.rc.ev.seed_for(el, 'particles', Ctx(1, 1))
    expected = np.r_[(hash01(first, np.arange(3), 1)-.5)*20,
                     (hash01(second, np.arange(3, 6), 1)-.5)*20]
    for t in (1.3, .2, 1.3):
        p = live(r, t)
        np.testing.assert_array_equal(p['x'], expected[:len(p['x'])])
        np.testing.assert_array_equal(p['seed'], [first]*3+([second]*3 if t > 1 else []))


def test_animated_preroll_matches_requested_history_and_backward_seeks(tmp_path):
    attrs = 'rate="10" lifetime="5" x="40" y="40" speed="0"'
    r = scene(tmp_path, attrs, switch('preroll', '0', '1'))
    controls = {p: scene(tmp_path, attrs+f' preroll="{p}"', name='preroll-'+str(p)) for p in (0, 1)}
    for t in (.2, .8, .2, .8):
        expected = live(controls[0 if t < .5 else 1], t)
        actual = live(r, t)
        for key in ('x', 'age', 'pid'):
            np.testing.assert_array_equal(actual[key], expected[key])


@pytest.mark.parametrize('length', [3., 12.])
def test_trails_have_no_hidden_duration_cap_and_animate_without_seek_dependence(tmp_path, length):
    from scenerender.nodes.particles import _trail_points
    attrs = f'rate="0" lifetime="20" x="16" y="32" speed="4" direction="0" shape="streak" size="3"'
    burst = '<burst time="0" count="1"/>'
    r = scene(tmp_path, attrs, burst+switch('trail', '0', str(length)))
    control = scene(tmp_path, attrs+f' trail="{length}"', burst, name='trail-control')
    for t in (.2, length+.3, .2, length+.3):
        actual = live(r, t)
        if t > .5:
            expected = live(control, t)
            np.testing.assert_array_equal(actual['hx'], expected['hx'])
            pts, times = _trail_points(actual['x'][0], actual['y'][0], actual['hx'][0], actual['hy'][0], actual['frac'][0], length)
            assert times[-1] == pytest.approx(length)
            assert pts[-1][0] == pytest.approx(actual['x'][0]-4*length, abs=1e-6)
            np.testing.assert_array_equal(r.frame_linear(t), control.frame_linear(t))


def test_animated_preset_uses_spawn_defaults_at_birth(tmp_path):
    r = scene(tmp_path, 'rate="0" lifetime="4" speed="0" sizeVariance="0"',
              '<burst time="0" count="1"/><burst time="1" count="1"/>'+switch('preset', 'rain', 'smoke'))
    p = live(r, 1.2)
    # Rain has no sizeEnd; smoke grows from 40 to 160. The choice follows each birth.
    np.testing.assert_allclose(p['s0'], [2, 40])
    np.testing.assert_allclose(p['s1'], [2, 160])


def test_sprite_source_sequence_follows_emitter_start(tmp_path):
    from PIL import Image
    for i, color in enumerate(('red', 'blue')):
        Image.new('RGBA', (12, 12), color).save(tmp_path/f'sprite-{i}.png')
    assets = '<assets><imageSequence id="sprite" src="sprite-%d.png" first="0" last="1" ' \
             'fps="2" width="12" height="12"/></assets>'
    r = scene(tmp_path, 'start="1" x="40" y="40" rate="0" lifetime="5" speed="0" '
              'shape="sprite" sprite="sprite" size="12"', '<burst time="1" count="1"/>', assets=assets)
    for t, expected in ((1.2, [1, 0, 0, 1]), (1.7, [0, 0, 1, 1]), (1.2, [1, 0, 0, 1])):
        np.testing.assert_array_equal(r.frame_linear(t)[40, 40], expected)


@pytest.mark.parametrize('first,second', [('rain', 'fire'), ('rain', 'smoke'), ('smoke', 'rain')])
def test_animated_preset_display_defaults_match_static_controls(tmp_path, first, second):
    attrs = ('x="40" y="40" rate="0" lifetime="4" lifetimeVariance="0" speed="0" speedVariance="0" '
             'size="24" sizeEnd="24" sizeVariance="0" rotation0="0" rotationVariance="0" '
             'angularVelocity="0" angularVelocityVariance="0" gravityX="0" gravityY="0" turbulence="0" '
             'drag="0" trail="0" shape="square"')
    burst = '<burst time="0" count="1"/>'
    r = scene(tmp_path, attrs, burst+switch('preset', first, second))
    controls = {p: scene(tmp_path, attrs+f' preset="{p}"', burst, name='control-'+p) for p in (first, second)}
    for t in (.2, .8, .2, .8):
        control = controls[first if t < .5 else second]
        np.testing.assert_array_equal(r.frame_linear(t), control.frame_linear(t))
    assert np.any(controls[first].frame_linear(.8) != controls[second].frame_linear(.8))


def test_tall_sprites_are_not_cropped_to_their_width(tmp_path):
    assets = '<assets><generator id="sprite" kind="solid" width="4" height="48" paint="#FFFFFFFF"/></assets>'
    r = scene(tmp_path, 'x="64" y="40" rate="0" lifetime="4" speed="0" size="4" shape="sprite" sprite="sprite"',
              '<burst time="0" count="1"/>', assets=assets)
    frame = r.frame_linear(.2)
    np.testing.assert_array_equal(frame[[18, 40, 61], 64, 3], 1)


def test_anisotropic_parent_scale_preserves_particle_bounds(tmp_path):
    r = scene(tmp_path, body='<group id="g" scaleX="4" scaleY=".25"><particleEmitter id="p" '
              'x="16" y="160" rate="0" lifetime="4" speed="0" size="20">'
              '<burst time="0" count="1"/></particleEmitter></group>')
    frame = r.frame_linear(.2)
    assert frame[40, 30, 3] > .9
    assert frame[40, 96, 3] > .9


def test_curved_trail_remains_visible_when_head_velocity_reaches_zero(tmp_path):
    r = scene(tmp_path, 'x="10" y="40" rate="0" lifetime="4" speed="60" direction="0" '
              'gravityX="-60" shape="streak" size="3" trail="1"', '<burst time="0" count="1"/>')
    t = 61/60
    p = live(r, t)
    np.testing.assert_allclose(p['vx'], 0, atol=1e-9)
    assert r.frame_linear(t)[40, 20, 3] > .05


def test_path_and_sprite_caches_stay_bounded_across_animation(tmp_path):
    assets = '<assets><generator id="sprite" kind="solid" width="4" height="4" paint="#FFFFFFFF"/></assets>'
    r = scene(tmp_path, 'rate="0" lifetime="4" speed="0" emitterShape="path" shape="sprite" sprite="sprite"',
              '<burst time="0" count="1" repeat="20" interval=".1"/>'
              '<expression property="emitterPath">"M" + (10+time) + " 20 L" + (30+time) + " 20"</expression>', assets=assets)
    for t in np.arange(.1, 2.1, .1):
        r.frame_linear(float(t))
    emitter = get_emitter(r.rc, r.doc.ids['p'], r.rc.node_ctx(r.doc.ids['p'], Ctx(2, 2)))
    assert len(emitter.path_samplers) == 8
    assert len(r.rc.cache['particle-sprites']) == 8


@pytest.mark.parametrize('prop,first,second', [('start', '0', '.2'), ('end', '.4', '1')])
def test_animated_windows_keep_replay_histories_separate(tmp_path, prop, first, second):
    attrs = 'rate="12" lifetime="4" speed="20" direction="0" x="30" y="40"'
    r = scene(tmp_path, attrs, switch(prop, first, second))
    controls = {v: scene(tmp_path, attrs+f' {prop}="{v}"', name='window-'+str(i))
                for i, v in enumerate((first, second))}
    for t in (.3, .8, .3, .8):
        control = controls[first if t < .5 else second]
        p, q = live(r, t), live(control, t)
        for key in ('x', 'age', 'pid'):
            np.testing.assert_array_equal(p[key], q[key])
        np.testing.assert_array_equal(r.frame_linear(t), control.frame_linear(t))


def test_changing_windows_do_not_accumulate_unbounded_history_caches(tmp_path):
    r = scene(tmp_path, 'rate="12" lifetime="4" speed="0"',
              '<expression property="start">time/10</expression>')
    for t in np.arange(.1, 2.1, .1):
        live(r, float(t))
    histories = [value for key, value in r.rc.cache.items() if isinstance(key, tuple) and key[0] == 'particles']
    assert len(histories) == 1
    assert len(histories[0]) == 8


@pytest.mark.parametrize('driver', ['animate', 'expression', 'link'])
def test_display_drivers_follow_each_instance_clock(tmp_path, driver):
    expression = switch('color', '#FF0000FF', '#0000FFFF')
    source = ''
    if driver == 'animate':
        expression = '<animate property="color"><key time="0" value="#FF0000FF" interpolation="hold"/>' \
                     '<key time=".5" value="#0000FFFF"/></animate>'
    elif driver == 'link':
        expression = '<link property="opacityEnd" source="source.opacity"/>'
        source = '<shape id="source" shape="rect" width="1" height="1" visible="false">'+switch('opacity', '.2', '1')+'</shape>'
    particle = '<particleEmitter id="p" x="16" y="16" rate="0" lifetime="5" speed="0" size="12">' \
               '<burst time="0" count="1"/>'+expression+'</particleEmitter>'
    symbols = '<symbols><symbol id="s" width="32" height="32" duration="5">'+source+particle+'</symbol></symbols>'
    r = scene(tmp_path, symbols=symbols,
              body='<instance id="a" symbol="s" speed="1"/><instance id="b" symbol="s" x="64" speed="2"/>')
    for t in (.3, .7, .3):
        frame = r.frame_linear(t)
        if driver == 'link':
            np.testing.assert_allclose(frame[16, 16], 1-.8*t/5 if t < .5 else 1, atol=1/255)
            np.testing.assert_array_equal(frame[16, 80], 1)
        else:
            np.testing.assert_array_equal(frame[16, 16], [1, 0, 0, 1] if t < .5 else [0, 0, 1, 1])
            np.testing.assert_array_equal(frame[16, 80], [0, 0, 1, 1])
