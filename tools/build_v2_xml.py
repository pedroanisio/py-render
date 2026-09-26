# Builds revision 2.0 of the scene: photo plates with colour grade and slow zoom, gradient scrims, on-screen credits,
# narration track with music ducking, and the credits file. Values are the final ones used for the delivered render
# (the session first used brightness 0.62 / saturation 0.72 / tint 0.18 and a darker scrim, then raised them after review).
import xml.etree.ElementTree as ET, json, hashlib
SRC = '/mnt/user-data/uploads/starbucks-closures_scene.xml'
tree = ET.parse(SRC); r = tree.getroot()
by = {e.get('id'): e for e in r.iter() if e.get('id')}
md = r.find('metadata')
def setmeta(n, v):
    for m in md.findall('meta'):
        if m.get('name') == n: m.set('value', v); return
    ET.SubElement(md, 'meta', name=n, value=v)
md.set('revision', '2.0')
setmeta('editorial.graphics', 'Licensed file photographs from Wikimedia Commons behind each chapter, credited on screen. Photos are illustrative file images; no pictured location is identified as closing. Original schematic drawings retained only where they do not compete with photography.')
setmeta('editorial.photo_changes', 'All photographs cropped to 16:9, darkened, partially desaturated and overlaid with a gradient scrim.')
setmeta('production.status', 'Voiced master: neural TTS narration aligned to caption windows, music ducked under voice.')
setmeta('production.voice', 'Neutral synthetic English newsreader voice (Microsoft Edge neural TTS, en-US-AndrewNeural). No impersonation.')
assets = r.find('assets')
# chapter -> Commons photo key (metadata in img/<key>.json from dl.py / dlhi.py) and crop focus
PH = [('J', 1, (0.5, 0.42)), ('F', 2, (0.5, 0.55)), ('C', 3, (0.62, 0.5)), ('D', 4, (0.5, 0.52)), ('P', 5, (0.55, 0.42)),
      ('G', 6, (0.5, 0.6)), ('L', 7, (0.5, 0.5)), ('N', 8, (0.5, 0.5)), ('A', 9, (0.5, 0.42))]
DISCLAIM = {1: True, 8: True, 9: True}
BRIGHT = {4: '0.72'}
paints = r.find('paints')
g = ET.SubElement(paints, 'linearGradient', id='scrim_h', x1='0', y1='0', x2='1', y2='0')
for o, c in (('0', '#071714F2'), ('0.42', '#071714D0'), ('0.68', '#07171470'), ('1', '#07171438')): ET.SubElement(g, 'stop', offset=o, color=c)
g = ET.SubElement(paints, 'linearGradient', id='scrim_v', x1='0', y1='0', x2='0', y2='1')
for o, c in (('0', '#071714B3'), ('0.2', '#07171400'), ('0.62', '#07171400'), ('1', '#071714E6')): ET.SubElement(g, 'stop', offset=o, color=c)
aud = assets.find('audio')
aud.set('src', 'assets/news-bed.wav'); aud.set('credit', 'Original procedural instrumental composed for this report')
aud.set('sha256', hashlib.sha256(open('v2/assets/news-bed.wav', 'rb').read()).hexdigest())
n = ET.Element('audio', id='narration', src='assets/narration.wav', duration='90', sampleRate='48000', channels='2',
               sha256=hashlib.sha256(open('v2/assets/narration.wav', 'rb').read()).hexdigest(),
               credit='Synthetic narration, Microsoft Edge neural TTS (en-US-AndrewNeural)', license='Generated for this report')
assets.insert(list(assets).index(aud) + 1, n)
credits = []
for k, shot, (fx, fy) in PH:
    d = json.load(open(f'img/{k}.json')); pid = f'photo_{shot}'
    assets.append(ET.Element('image', id=pid, src=f'assets/photos/{k}.jpg', sha256=d['sha256'],
                             credit=f"{d['artist']} / Wikimedia Commons", license=d['license'], licenseUrl=d['licurl'], sourceUrl=d['page']))
    txt = f"Photo: {d['artist']} / {d['license']} / Wikimedia Commons" + (" • File photo; location not identified as closing" if DISCLAIM.get(shot) else " • File photo")
    ET.SubElement(assets, 'text', id=f'credit_{shot}_asset', text=txt, width='2400', height='60', size='32', fontAsset='font_regular',
                  color='#BDCEC4CC', lineHeight='1.12', wrap='word', autoFit='shrink', minSize='26', maxLines='1', overflow='clip', align='right')
    credits.append((shot, pid, fx, fy, d))
comp = r.find('composition')
for shot, pid, fx, fy, d in credits:
    grp = by[f'shot_{shot}']; s0, s1 = float(grp.get('start')), float(grp.get('end'))
    lay = ET.Element('layer', id=f'photo_layer_{shot}', asset=pid, x='0', y='0', width='3840', height='2160', fit='cover', focusX=str(fx), focusY=str(fy), z='-20')
    ET.SubElement(lay, 'effect', type='color-grade', brightness=BRIGHT.get(shot, '0.9'), saturation='0.82', tint='#0E2A24', tintAmount='0.12')
    sc = ET.SubElement(lay, 'animate', property='scale', defaultInterpolation='linear', timeBase='composition')
    z0, z1 = (1.0, 1.07) if shot % 2 else (1.07, 1.0)
    ET.SubElement(sc, 'key', time=str(s0), value=str(z0)); ET.SubElement(sc, 'key', time=str(s1), value=str(z1))
    grp.insert(1, lay)
    grp.insert(2, ET.Element('shape', id=f'scrim_h_{shot}', shape='rect', x='0', y='0', width='3840', height='2160', fill='url(#scrim_h)', z='-19'))
    grp.insert(3, ET.Element('shape', id=f'scrim_v_{shot}', shape='rect', x='0', y='0', width='3840', height='2160', fill='url(#scrim_v)', z='-18'))
    grp.append(ET.Element('layer', id=f'credit_{shot}', asset=f'credit_{shot}_asset', x='1248', y='1622', z='5'))
# schematic storefront drawings compete with the photography in chapters 1-2
for sid in ('hero_shop_0', 'hero_shop_1', 'hero_shop_2', 'hero_label', 'scale_shop_0', 'scale_shop_1', 'scale_shop_2'):
    for grp in comp.iter('group'):
        for c in list(grp):
            if c.get('id') == sid: grp.remove(c)
by['end_stamp_asset'].set('text', 'Announcement: 24 September 2026\nIndependent explainer • photos via Wikimedia Commons, credited on screen')
mix = r.find('audioMix'); mt = mix.find('audioTrack')
mix.insert(1, ET.Element('audioTrack', id='narration_track', asset='narration', start='0', clipIn='0', clipOut='90', role='dialogue', volume='1.0'))
mt.set('duckBy', 'dialogue'); mt.set('duckAmount', '-10'); mt.set('duckAttack', '0.04'); mt.set('duckRelease', '0.45')
ET.indent(tree, '  ')
tree.write('v2/starbucks-closures_scene_v2.xml', encoding='UTF-8', xml_declaration=True)
lines = ['PHOTO CREDITS — Starbucks: 250 closures explained (v2)',
         'All photos from Wikimedia Commons. Changes: cropped to 16:9, darkened, partially desaturated, tinted, overlaid with a gradient; slow zoom applied.', '']
for shot, pid, fx, fy, d in credits:
    lines += [f"Chapter {shot}: {d['title'][5:]}", f"  Author: {d['artist']}", f"  License: {d['license']} ({d['licurl']})", f"  Source: {d['page']}", '']
lines += ['NARRATION: synthetic voice, Microsoft Edge neural TTS (en-US-AndrewNeural).', 'MUSIC: original procedural instrumental composed for this report.']
open('v2/CREDITS.txt', 'w').write('\n'.join(lines))
