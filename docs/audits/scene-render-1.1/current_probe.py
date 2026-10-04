"""Current-tree conformance counterexamples; does not modify renderer code.

Run from the repository root with .venv/bin/python. All generated scenes are
XSD-validated and loaded strictly. Results and PNG controls go to /tmp.
An exit code of zero means probes ran, not that conformance was established.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import wave

import numpy as np
from lxml import etree
from PIL import Image

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from scenerender.render import Renderer
from scenerender.evaluator import Ctx, Scope

ROOT = Path('/tmp/scene-render-current-inspection')
ROOT.mkdir(exist_ok=True)
XSD = etree.XMLSchema(etree.parse(str(REPO / 'schema/scene-render-1.1.xsd')))
VALIDATED = []


def scene(body='', before='', after=''):
    return (f'<scene version="1.1"><project width="100" height="100" fps="10" '
            f'duration="2" linearLight="false"/>{before}<composition>{body}</composition>'
            f'{after}</scene>')


def open_scene(name, xml):
    path = ROOT / (name + '.xml')
    path.write_text(xml)
    XSD.assertValid(etree.parse(str(path)))
    VALIDATED.append(path.name)
    return Renderer.open(str(path), strict=True)


def render(name, xml, t=.5):
    r = open_scene(name, xml)
    px = r.frame_rgba(t)
    Image.fromarray(px).save(ROOT / (name + '.png'))
    return r, px


def compare(a, b):
    return {'changed_channels': int(np.count_nonzero(a != b)),
            'actual_painted_pixels': int(np.count_nonzero(a[..., 3])),
            'control_painted_pixels': int(np.count_nonzero(b[..., 3]))}


def key(prop, value):
    return f'<animate property="{prop}"><key time="0" value="{value}"/></animate>'


def symbol(body):
    return f'<symbols><symbol id="sym" width="100" height="100" duration="2">{body}</symbol></symbols>'


def instance_window():
    before = symbol('<shape id="s" shape="rect" width="20" height="20"/>')
    r, px = render('instance_start', scene('<instance id="i" symbol="sym">'
                  '<override target="s" property="start" value="1"/></instance>', before))
    ctx = Ctx(.5, .5, scope=Scope().push('i', {('s', 'start'): '1'}))
    return {'evaluated_start': r.rc.ev.num(r.doc.ids['s'], 'start', ctx),
            'painted_pixels_at_half_second': int(np.count_nonzero(px[..., 3])),
            'expected_painted_pixels': 0}


def primitive_animation():
    r, actual = render('primitive_animated', scene('<object3D id="o" primitive="box" radius="20">'
                       + key('primitive', 'sphere') + '</object3D>'))
    _, control = render('primitive_static', scene('<object3D id="o" primitive="sphere" radius="20"/>'))
    from scenerender.three.scene import primitive_geo
    geo_key, _ = primitive_geo(r.rc, r.doc.ids['o'], Ctx(.5, .5))
    return {'evaluated_primitive': r.rc.ev.str(r.doc.ids['o'], 'primitive', Ctx(.5, .5)),
            'geometry_primitive': geo_key[0], **compare(actual, control)}


def material_animation():
    before = '<materials><material id="red" baseColor="#ff0000" unlit="true"/>' \
             '<material id="blue" baseColor="#0000ff" unlit="true"/></materials>'
    r, actual = render('material_animated', scene('<object3D id="o" primitive="plane" '
                       'width="40" height="40" material="red">' + key('material', 'blue') + '</object3D>', before))
    _, control = render('material_static', scene('<object3D id="o" primitive="plane" '
                        'width="40" height="40" material="blue"/>', before))
    return {'evaluated_material': r.rc.ev.str(r.doc.ids['o'], 'material', Ctx(.5, .5)),
            'actual_center_rgb': actual[50, 50, :3].tolist(),
            'control_center_rgb': control[50, 50, :3].tolist(), **compare(actual, control)}


def camera_ortho_animation():
    body = '<object3D id="o" primitive="plane" width="20" height="20"/>'
    r, actual = render('camera_ortho_animated', scene('<camera id="cam" z="200" projection="orthographic">'
                       + key('orthoHeight', '50') + '</camera>' + body))
    _, control = render('camera_ortho_static', scene('<camera id="cam" z="200" projection="orthographic" '
                        'orthoHeight="50"/>' + body))
    from scenerender.camera import build_camera
    cam = build_camera(r.rc, r.doc.ids['cam'], .5)
    return {'evaluated_ortho_height': r.rc.ev.num(r.doc.ids['cam'], 'orthoHeight', Ctx(.5, .5)),
            'actual_projection_scale': cam.k_ortho, 'expected_projection_scale': 2,
            **compare(actual, control)}


def instance_geometry_width():
    body = '<instance id="i" symbol="sym"><override target="o" property="width" value="20"/></instance>'
    _, actual = render('instance_width', scene(body, symbol('<object3D id="o" primitive="plane" height="20"/>')))
    _, control = render('instance_width_static', scene('<instance id="i" symbol="sym"/>',
                        symbol('<object3D id="o" primitive="plane" width="20" height="20"/>')))
    return compare(actual, control)


def instance_3d_cache():
    before = symbol('<object3D id="o" primitive="plane" width="10" height="10"/>')
    body = ''.join(f'<instance id="i{i}" symbol="sym"><override target="o" property="x" value="{x}"/>'
                   '</instance>' for i, x in enumerate((-20, 20)))
    r, actual = render('instance_3d_cache', scene(body, before))
    _, control = render('instance_3d_cache_control', scene(''.join(
        f'<object3D id="o{i}" primitive="plane" x="{x}" width="10" height="10"/>'
        for i, x in enumerate((-20, 20)))))
    from scenerender.camera import world3d
    r.rc.frame_cache.clear()
    o = r.doc.ids['o']
    contexts = [Ctx(.5, .5, scope=Scope().push(f'i{i}', {('o', 'x'): str(x)}))
                for i, x in enumerate((-20, 20))]
    return {'evaluated_positions': [r.rc.ev.num(o, 'x', c) for c in contexts],
            'cached_world_positions': [float(world3d(r.rc, o, c)[0, 3]) for c in contexts],
            'actual_right_alpha': int(actual[50, 70, 3]),
            'control_right_alpha': int(control[50, 70, 3]), **compare(actual, control)}


def particle_preset_override():
    body = '<instance id="i" symbol="sym"><override target="p" property="preset" value="snow"/></instance>'
    r, actual = render('particle_preset_override', scene(body, symbol(
        '<particleEmitter id="p" preset="rain" x="50" y="10"/>')))
    _, control = render('particle_preset_static', scene('<instance id="i" symbol="sym"/>', symbol(
        '<particleEmitter id="p" preset="snow" x="50" y="10"/>')))
    from scenerender.nodes.particles import get_emitter
    ctx = Ctx(.5, .5, scope=Scope().push('i', {('p', 'preset'): 'snow'}))
    p = r.doc.ids['p']
    return {'evaluated_preset': r.rc.ev.str(p, 'preset', ctx),
            'emitter_default_speed': get_emitter(r.rc, p, ctx).P.get('speed'),
            'expected_snow_default_speed': 60, **compare(actual, control)}


def audio_effect_enabled():
    sr = 48000
    audio_path = ROOT / 'tone.wav'
    t = np.arange(sr) / sr
    raw = (np.sin(2 * np.pi * 440 * t) * 6000).astype('<i2')
    with wave.open(str(audio_path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(raw.tobytes())
    before = '<assets><audio id="a" src="tone.wav"/></assets>'
    def mix_xml(fx):
        return scene(before=before, after='<audioMix><audioTrack id="track" asset="a">' + fx
                     + '</audioTrack></audioMix>')
    r = open_scene('audio_effect_enabled', mix_xml('<audioEffect type="gain" gain="-20">'
                   + key('enabled', 'false') + '</audioEffect>'))
    ref = open_scene('audio_effect_disabled', mix_xml('<audioEffect type="gain" gain="-20" enabled="false"/>'))
    from scenerender.audio.mix import Mixer
    a = Mixer(r.doc, r.rc.ev).render(0, 1)
    b = Mixer(ref.doc, ref.rc.ev).render(0, 1)
    fx = r.doc.root.find('.//audioEffect')
    return {'evaluated_enabled': r.rc.ev.bool(fx, 'enabled', Ctx(.5, .5)),
            'rms_ratio_to_disabled_control': float(np.sqrt(np.mean(a*a) / np.mean(b*b))),
            'expected_ratio': 1}


def data_hash():
    (ROOT / 'data.json').write_text('[10,40]')
    from scenerender.document import SceneError
    try:
        r = open_scene('data_hash', scene('<repeat id="r" over="rows"><shape id="s" shape="rect" '
                       'width="10" height="10"/></repeat>',
                       '<parameters><data id="rows" src="data.json" sha256="' + '0' * 64 + '"/></parameters>'))
    except SceneError as exc:
        return {'incorrect_hash_accepted': False, 'rejection': str(exc)}
    return {'incorrect_hash_accepted': True, 'loaded_rows': r.doc.data['rows']}


def constraint_target_animation():
    prefix = '<group id="a" x="10"/><group id="b" x="60"/>'
    def body(target, animation=''):
        return prefix + '<shape id="s" shape="rect" width="10" height="10">' \
            f'<transformConstraint type="copy-position" target="{target}">{animation}' \
            '</transformConstraint></shape>'
    r, actual = render('constraint_target_animated', scene(body('a', key('target', 'b'))))
    _, control = render('constraint_target_static', scene(body('b')))
    c = r.doc.ids['s'].find('transformConstraint')
    return {'evaluated_target': r.rc.ev.str(c, 'target', Ctx(.5, .5)),
            'actual_left': int(np.where(actual[5, :, 3] > 0)[0].min()),
            'control_left': int(np.where(control[5, :, 3] > 0)[0].min()), **compare(actual, control)}


def deform_type_animation():
    def body(typ, animation=''):
        return '<shape id="s" shape="rect" width="30" height="30" x="35" y="35">' \
            f'<deform><modifier type="{typ}" amount="20">{animation}</modifier></deform></shape>'
    r, actual = render('deform_type_animated', scene(body('wave', key('type', 'twist'))))
    _, control = render('deform_type_static', scene(body('twist')))
    m = r.doc.ids['s'].find('deform/modifier')
    return {'evaluated_type': r.rc.ev.str(m, 'type', Ctx(.5, .5)), **compare(actual, control)}


def physics_in_symbol():
    shape = '<shape id="s" shape="rect" x="10" y="10" width="10" height="10">' \
            '<rigidBody linearDamping="0"/></shape>'
    ph = '<physics gravityY="-1" pixelsPerMeter="100"/>'
    r, actual = render('physics_in_symbol', scene('<instance id="i" symbol="sym"/>', symbol(shape), ph))
    _, control = render('physics_direct', scene(shape, after=ph))
    simulations = [sim for k, histories in r.rc.cache.items()
                   if isinstance(k, tuple) and k[0] == 'physics-sim'
                   for sim in histories.values()
                   if sim is not None and getattr(sim, 'root', None) is r.doc.ids['sym']]
    return {'discovered_symbol_bodies': sum(len(sim.body_els) for sim in simulations),
            'actual_top': int(np.where(actual[:, 15, 3] > 0)[0].min()),
            'control_top': int(np.where(control[:, 15, 3] > 0)[0].min()), **compare(actual, control)}


def camera_in_symbol():
    body = '<camera id="cam" z="200" projection="orthographic" orthoHeight="50"/>' \
           '<object3D id="o" primitive="plane" width="20" height="20"/>'
    r, actual = render('camera_in_symbol', scene('<instance id="i" symbol="sym"/>', symbol(body)))
    _, control = render('camera_direct', scene(body))
    return {'discovered_symbol_cameras': len(r.rc.cache.get(('cameras', r.doc.ids['sym']), [])),
            **compare(actual, control)}


def main():
    import cairo
    from scenerender import gl
    result = {'base_commit': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
              'schema_sha256': hashlib.sha256((REPO / 'schema/scene-render-1.1.xsd').read_bytes()).hexdigest(),
              'environment': {'python': sys.version.split()[0], 'pycairo': cairo.version,
                              'cairo': cairo.cairo_version_string(), 'gl_available': gl.available(),
                              'gl_renderer': gl.context().info.get('GL_RENDERER') if gl.available() else None}}
    for fn in (instance_window, primitive_animation, material_animation, camera_ortho_animation,
               instance_geometry_width, instance_3d_cache, particle_preset_override, audio_effect_enabled,
               constraint_target_animation, deform_type_animation, physics_in_symbol, camera_in_symbol, data_hash):
        try:
            result[fn.__name__] = fn()
        except Exception as exc:
            result[fn.__name__] = {'probe_error': type(exc).__name__, 'message': str(exc)}
    result['validated_fixtures'] = VALIDATED
    path = ROOT / 'results.json'
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(path.read_text())


if __name__ == '__main__':
    main()
