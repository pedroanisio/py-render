"""Reproduce scene-render 1.1 conformance gaps without editing renderer code."""
from pathlib import Path
import hashlib
import json
import logging
import os
import sys
import subprocess

import numpy as np
from PIL import Image
from lxml import etree

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
os.chdir(REPO)
from scenerender.render import Renderer
from scenerender.evaluator import Ctx, Scope
from scenerender import document, registry
from scenerender.coverage import _load_all

logging.basicConfig(level=logging.ERROR)
ROOT = Path('/tmp/scene-render-schema-audit')
ROOT.mkdir(exist_ok=True)
RESULTS = {}
VALIDATED = []
XSD = etree.XMLSchema(etree.parse('schema/scene-render-1.1.xsd'))

def scene(body='', before='', after='', project=''):
    return f'''<scene version="1.1"><project width="100" height="100" fps="10"
        duration="2" linearLight="false" {project}/>{before}
        <composition>{body}</composition>{after}</scene>'''

def write(name, xml):
    path = ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml)
    XSD.assertValid(etree.parse(str(path)))
    VALIDATED.append(name)
    return path

def render(name, xml, t=0.5):
    r = Renderer.open(str(write(name, xml)), strict=True)
    px = r.frame_rgba(t)
    Image.fromarray(px).save(ROOT / (name.replace('/', '_') + '.png'))
    return r, px

def record(name, fn):
    try:
        RESULTS[name] = fn()
    except Exception as exc:
        RESULTS[name] = {'probe_error': type(exc).__name__, 'message': str(exc)}

def inventory():
    _load_all()
    tree = etree.parse('schema/scene-render-1.1.xsd')
    ns = {'x': 'http://www.w3.org/2001/XMLSchema'}
    specs = [('node', registry.NODES, "//x:group[@name='nodeChoice']//x:element/@name"),
             ('asset', registry.ASSETS, "//x:complexType[@name='assetsType']//x:element/@name"),
             ('blend', registry.BLENDS, "//x:simpleType[@name='blendType']//x:enumeration/@value")]
    for category, reg, typ, attr in [
        ('effect', registry.EFFECTS, 'effectType', 'type'),
        ('transition', registry.TRANSITIONS, 'transitionType', 'type'),
        ('shapeModifier', registry.SHAPE_MODIFIERS, 'shapeModifierType', 'type'),
        ('deform', registry.DEFORMERS, 'modifierType', 'type'),
        ('textAnimator', registry.TEXT_ANIMATORS, 'textAnimatorType', 'preset'),
        ('audioEffect', registry.AUDIO_EFFECTS, 'audioEffectType', 'type'),
        ('caption', registry.CAPTION_PRESETS, 'captionTrackType', 'preset'),
        ('codec', registry.CODECS, 'outputType', 'codec')]:
        specs.append((category, reg, f"//x:complexType[@name='{typ}']//x:attribute[@name='{attr}']//x:enumeration/@value"))
    out = {}
    for category, reg, xpath in specs:
        names = tree.xpath(xpath, namespaces=ns)
        out[category] = {'schema_count': len(names), 'registered_count': len(reg.entries),
                        'schema_names':names,
                        'missing': [n for n in names if n not in reg.entries],
                        'non_full': {n: {'level': reg.level(n), 'note': reg.entries[n].note}
                                     for n in names if n in reg.entries and reg.level(n) != 'full'}}
    out['declarations'] = {name: len(tree.xpath(f'//x:{name}', namespaces=ns))
                           for name in ['element', 'attribute', 'complexType', 'simpleType']}
    out['cross_cutting_registry'] = {'registered_count':len(registry.FEATURES.entries),
        'non_full':{n:e.note for n,e in registry.FEATURES.entries.items() if e.level != 'full'}}
    out['schema_sha256'] = hashlib.sha256(Path('schema/scene-render-1.1.xsd').read_bytes()).hexdigest()
    out['base_commit'] = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    out['python'] = sys.version.split()[0]
    return out

def visibility():
    r, px = render('visibility.xml', scene('''<shape id="s" shape="rect" width="40" height="40" fill="#ff0000">
       <animate property="visible"><key time="0" value="false"/></animate></shape>'''))
    return {'evaluated_visible': r.rc.ev.bool(r.doc.ids['s'], 'visible', Ctx(.5,.5)),
            'painted_pixels': int(np.count_nonzero(px[...,3]))}

def instance_visibility():
    r, px = render('instance_visibility.xml', scene('''<instance id="i" symbol="sym">
       <override target="s" property="visible" value="false"/></instance>''',
       '''<symbols><symbol id="sym" width="100" height="100" duration="2">
       <shape id="s" shape="rect" width="40" height="40" fill="#ff0000"/>
       </symbol></symbols>'''))
    return {'painted_pixels': int(np.count_nonzero(px[...,3]))}

def repeat_param():
    r, px = render('repeat_param.xml', scene('''<repeat id="r" over="items" var="item" offsetY="25">
      <shape id="s" shape="rect" width="10" height="10" fill="#ff0000">
      <expression property="x">param('item')</expression></shape></repeat>''',
      '''<parameters><param id="items" type="list" default="[10,40]"/></parameters>'''))
    return {'first_row_left': int(np.where(px[5,:,3] > 0)[0].min()),
            'second_row_left': int(np.where(px[30,:,3] > 0)[0].min()), 'expected': [10,40]}

def repeat_opacity():
    _, px = render('repeat_opacity.xml', scene('''<repeat id="r" count="2" offsetX="30" opacity="0.5" opacityStep="0.25">
      <shape id="s" shape="rect" width="20" height="20" fill="#ff0000"/></repeat>'''))
    return {'alpha': [int(px[5,5,3]), int(px[5,35,3])], 'expected_approx': [128,64]}

def repeat_references():
    r, px = render('repeat_parent.xml', scene('''<repeat id="r" count="2" offsetY="30">
      <group id="p" x="20"/>
      <shape id="s" parent="p" shape="rect" width="10" height="10" fill="#ff0000"/>
      </repeat>'''))
    return {'copied_parent_ids': [r.doc.ids[f's#{i}'].get('parent') for i in range(2)],
            'original_parent_exists': 'p' in r.doc.ids,
            'rendered_x': int(np.where(px[5,:,3]>0)[0].min()), 'expected_x':20}

def include_relative():
    Image.new('RGB', (20,20), 'red').save(ROOT / 'child_red.png')
    child = ROOT / 'sub'
    child.mkdir(exist_ok=True)
    Image.new('RGB',(20,20),'red').save(child / 'red.png')
    write('sub/relative.xml', scene('<layer id="l" asset="img"/>',
          '<assets><image id="img" src="red.png" width="20" height="20"/></assets>'))
    r = Renderer.open(str(write('include_relative.xml', scene('<include id="inc" src="sub/relative.xml"/>'))), strict=True)
    resolved = r.doc.resolve_path(r.doc.ids['inc/img'].get('src'))
    try:
        px = r.frame_rgba(.5)
        outcome = {'painted_pixels': int(np.count_nonzero(px[...,3]))}
    except Exception as exc:
        outcome = {'render_error': type(exc).__name__, 'message': str(exc)}
    return {'resolved_asset':resolved, 'resolved_exists':Path(resolved).exists(),
            'correct_asset_exists':(child/'red.png').exists(), **outcome}

def include_links():
    write('sub/links.xml', scene('''<shape id="a" shape="rect" width="10" height="10" x="30"/>
      <shape id="b" shape="rect" width="10" height="10" y="20"><link property="x" source="a.x"/></shape>
      <shape id="c" shape="rect" width="10" height="10" y="40"><expression property="x">prop('a.x')</expression></shape>'''))
    r, px = render('include_links.xml', scene('<include id="inc" src="sub/links.xml"/>'))
    return {'link_source':r.doc.ids['inc/b'].find('link').get('source'),
            'expression':r.doc.ids['inc/c'].find('expression').text,
            'rendered_x':[int(np.where(px[y,:,3]>0)[0].min()) for y in (5,25,45)], 'expected':[30,30,30]}

def include_tokens():
    write('sub/tokens.xml', scene('<shape id="s" shape="rect" width="20" height="20" fill="var(--accent)"/>',
          '<styles><token name="accent" value="#ff0000"/></styles>'))
    r, px = render('include_tokens.xml', scene('''<shape id="parent" shape="rect" x="30" width="20" height="20" fill="var(--accent)"/>
      <include id="inc" src="sub/tokens.xml"/>''', '<styles><token name="accent" value="#0000ff"/></styles>'))
    return {'tokens':r.doc.tokens, 'parent_rgb':px[5,35,:3].tolist(), 'expected_parent_rgb':[0,0,255]}

def include_cycle():
    path = write('cycle.xml', scene('<include id="inc" src="cycle.xml"/>'))
    try:
        document.load(str(path), strict=True)
    except Exception as exc:
        return {'error':type(exc).__name__, 'message':str(exc)[:150]}
    return {'error':None}

def conic_user():
    stop='<stop offset="0" color="#ff0000"/><stop offset="1" color="#0000ff"/>'
    _, user = render('conic_user.xml', scene('<shape id="s" shape="rect" width="100" height="100" fill="url(#g)"/>',
               f'<paints><conicGradient id="g" units="user" cx="50" cy="50">{stop}</conicGradient></paints>'))
    _, obj = render('conic_object.xml', scene('<shape id="s" shape="rect" width="100" height="100" fill="url(#g)"/>',
               f'<paints><conicGradient id="g" cx="0.5" cy="0.5">{stop}</conicGradient></paints>'))
    return {'user_painted_pixels':int(np.count_nonzero(user[...,3])),
            'equivalent_object_painted_pixels':int(np.count_nonzero(obj[...,3]))}

def gradient_toggle(kind, attr, a, b):
    def xml(value):
        props = f'{attr}="{value}"' if attr != 'midpoint' else ''
        mid = f'midpoint="{value}"' if attr == 'midpoint' else ''
        return scene('<shape id="s" shape="rect" width="100" height="100" fill="url(#g)"/>',
            f'<paints><{kind} id="g" {props}><stop offset="0" color="#333333" {mid}/>'
            f'<stop offset="1" color="#444444"/></{kind}></paints>')
    _, pxa = render(f'{kind}_{attr}_a.xml', xml(a))
    _, pxb = render(f'{kind}_{attr}_b.xml', xml(b))
    return {'changed_channels':int(np.count_nonzero(pxa != pxb)), 'values':[a,b]}

def radial_animation():
    def xml(base):
        return scene('<shape id="s" shape="rect" width="100" height="100" fill="url(#g)"/>',
        f'''<paints><radialGradient id="g" {base}><stop offset="0" color="#ff0000"/>
        <stop offset="1" color="#0000ff"/><animate property="fx"><key time="0" value="0.2"/></animate>
        </radialGradient></paints>''')
    r, without = render('radial_no_base.xml',xml(''))
    _, withbase = render('radial_base.xml',xml('fx="0.5"'))
    return {'evaluated_fx':r.rc.ev.num(r.doc.ids['g'],'fx',Ctx(.5,.5)),
            'changed_channels_when_redundant_base_added':int(np.count_nonzero(without != withbase))}

def condition():
    _, px = render('condition.xml', scene('<shape id="s" shape="rect" width="20" height="20" condition="[]"/>'))
    from scenerender.expr import truthy
    return {'expression_language_truthiness':truthy([]),'painted_pixels':int(np.count_nonzero(px[...,3]))}

def adaptive_blur():
    body='''<shape id="s" shape="rect" width="10" height="20" y="20">
      <expression property="x">10+60*Math.sin((time-0.95)/0.1*Math.PI)</expression></shape>'''
    # First and last shutter samples are symmetric around the peak.
    def xml(adaptive):
        return scene(body, project=f'motionBlur="true" motionBlurSamples="8" shutterAngle="360" shutterPhase="-180" adaptiveMotionBlur="{adaptive}"')
    _, a = render('blur_adaptive.xml',xml('true'),1)
    _, b = render('blur_full.xml',xml('false'),1)
    return {'changed_channels':int(np.count_nonzero(a!=b)),
            'adaptive_painted_pixels':int(np.count_nonzero(a[...,3])),
            'full_painted_pixels':int(np.count_nonzero(b[...,3]))}

def animated_z():
    r, px = render('animated_z.xml', scene('''<shape id="red" shape="rect" width="20" height="20" fill="#ff0000">
      <animate property="z"><key time="0" value="2"/></animate></shape>
      <shape id="blue" z="1" shape="rect" width="20" height="20" fill="#0000ff"/>'''))
    return {'evaluated_z':r.rc.ev.num(r.doc.ids['red'],'z',Ctx(.5,.5)),
            'rgb':px[5,5,:3].tolist(),'expected_rgb':[255,0,0]}

def animated_shape():
    r, px = render('animated_shape.xml', scene('''<shape id="s" shape="rect" width="40" height="40" fill="#ff0000">
      <animate property="shape"><key time="0" value="ellipse"/></animate></shape>'''))
    return {'evaluated_shape':r.rc.ev.str(r.doc.ids['s'],'shape',Ctx(.5,.5)),
            'corner_alpha':int(px[0,0,3]),'expected_corner_alpha':0}

def sequence_media():
    seqdir=ROOT/'frames'
    seqdir.mkdir(exist_ok=True)
    for i in range(10):
        Image.new('RGB',(20,20),'red' if i<5 else 'blue').save(seqdir/f'{i:02}.png')
    assets='''<assets><imageSequence id="img" src="frames/%02d.png" first="0" last="9" fps="10" width="20" height="20"/></assets>'''
    _, seq = render('sequence_media.xml', scene('''<sequence id="q"><shape id="intro" shape="rect" width="20" height="20" end="1"/>
      <layer id="l" asset="img"/></sequence>''',assets),1.7)
    _, direct=render('sequence_media_control.xml',scene('<layer id="l" asset="img" start="1"/>',assets),1.7)
    return {'sequence_rgb':seq[5,5,:3].tolist(),'equivalent_direct_rgb':direct[5,5,:3].tolist()}

def sequence_stretch():
    path=write('sequence_stretch.xml',scene('''<sequence id="q"><layer id="l" asset="v" timeStretch="2"/>
      <shape id="s" shape="rect" width="20" height="20" end="1"/></sequence>''',
      '<assets><video id="v" src="unused.mp4" width="20" height="20" fps="10" duration="1"/></assets>'))
    doc=document.load(str(path),strict=True)
    return {'video_window':doc.window(doc.ids['l']), 'next_child_window':doc.window(doc.ids['s']),
            'expected_video_window':[0,2]}

def instance_scoped_reference():
    r, px=render('instance_scoped_reference.xml',scene('''<instance id="i" symbol="sym"/>
      <shape id="consumer" shape="rect" width="10" height="10" y="30"><expression property="x">prop('i/s.x')</expression></shape>''',
      '''<symbols><symbol id="sym" width="100" height="100" duration="2"><shape id="s" shape="rect" width="10" height="10" x="30"/>
      </symbol></symbols>'''))
    return {'scoped_id_indexed':'i/s' in r.doc.ids,
            'consumer_x':int(np.where(px[35,:,3]>0)[0].min()),'expected_x':30}

def group_stretch():
    def xml(align):
        return scene(f'''<group id="g" layout="row" width="100" height="100" padding="10" alignItems="{align}">
        <shape id="s" shape="rect" width="20" height="20"/></group>''')
    _,a=render('group_stretch.xml',xml('stretch'))
    _,b=render('group_start.xml',xml('start'))
    return {'changed_channels':int(np.count_nonzero(a!=b)), 'painted_pixels':int(np.count_nonzero(a[...,3]))}

def include_hash():
    write('sub/hash.xml',scene('<shape id="s" shape="rect" width="20" height="20"/>'))
    try:
        _,px=render('include_hash.xml',scene('<include id="inc" src="sub/hash.xml" sha256="'+'0'*64+'"/>'))
    except document.SceneError as exc:
        return {'incorrect_hash_accepted':False, 'rejection':str(exc)}
    return {'incorrect_hash_accepted':bool(px[...,3].any())}

def include_strict():
    # This is deliberately invalid; it is NOT part of the valid-fixture counter.
    bad=ROOT/'sub/invalid.xml'
    bad.write_text(scene('<shape id="s" shape="rect" width="20" height="20" unexpected="x"/>'))
    try:
        document.load(str(bad),strict=True)
        direct_rejected=False
    except document.SceneError:
        direct_rejected=True
    try:
        _,px=render('include_strict.xml',scene('<include id="inc" src="sub/invalid.xml"/>'))
    except document.SceneError as exc:
        return {'invalid_child_rejected_directly':direct_rejected,
                'strict_parent_still_draws_invalid_child':False, 'rejection':str(exc)}
    return {'invalid_child_rejected_directly':direct_rejected,'strict_parent_still_draws_invalid_child':bool(px[...,3].any())}

def version_gate():
    try:
        r,px=render('version_gate.xml',scene('<repeat id="r" count="1"><shape id="s" shape="rect" width="20" height="20"/></repeat>').replace('version="1.1"','version="1.0"'))
    except document.SceneError as exc:
        return {'declared_version':'1.0', 'new_1_1_node_rendered':False, 'rejection':str(exc),
                'referenced_schematron_exists':Path('schema/scene-render-1.1.sch').exists()}
    return {'declared_version':r.doc.root.get('version'),'new_1_1_node_rendered':bool(px[...,3].any()),
        'referenced_schematron_exists':Path('schema/scene-render-1.1.sch').exists()}

for name, fn in [('inventory',inventory),('visibility_animation',visibility),('instance_visibility_override',instance_visibility),
    ('repeat_current_item',repeat_param),('repeat_opacity',repeat_opacity),('repeat_references',repeat_references),
    ('include_relative_paths',include_relative),('include_property_references',include_links),('include_tokens',include_tokens),
    ('include_cycle',include_cycle),('conic_user_units',conic_user),
    ('conic_midpoint',lambda:gradient_toggle('conicGradient','midpoint','.1','.9')),
    ('gradient_dither',lambda:gradient_toggle('linearGradient','dither','true','false')),
    ('radial_animation_without_base',radial_animation),('condition_truthiness',condition),('adaptive_motion_blur',adaptive_blur),
    ('animated_z',animated_z),('animated_shape',animated_shape),('sequence_media_clock',sequence_media),
    ('sequence_time_stretch',sequence_stretch),('instance_scoped_reference',instance_scoped_reference),
    ('group_stretch',group_stretch),('include_sha256',include_hash),('include_strict',include_strict),
    ('version_gate',version_gate)]:
    record(name,fn)
RESULTS['validated_fixtures'] = VALIDATED
(ROOT/'results.json').write_text(json.dumps(RESULTS,indent=2))
print(json.dumps(RESULTS,indent=2))
if any(isinstance(v,dict) and 'probe_error' in v for v in RESULTS.values()):
    raise SystemExit('One or more audit probes could not complete; inspect results.json.')
