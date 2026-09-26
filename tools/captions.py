# Builds the English SDH sidecars (SRT and VTT) from the XML captionTrack.
# Run inline during the session; reproduced here as a single script with the same logic.
import xml.etree.ElementTree as ET, textwrap
r = ET.parse('/mnt/user-data/uploads/the-last-floor-episode-01_manga_scene.xml').getroot()
ct = r.find('.//captionTrack'); mc = int(ct.get('maxCharsPerLine')); ml = int(ct.get('maxLines'))
def ts(x):
    ms = int(round(x * 1000)); return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d}.{ms%1000:03d}"
srt = []; n = 0
for c in ct.findall('cue'):
    a, b = float(c.get('start')), float(c.get('end')); sp = c.get('speaker'); txt = c.get('text')
    body = f"{sp}: {txt}" if sp else txt
    lines = textwrap.wrap(body, mc)
    groups = [lines[i:i + ml] for i in range(0, len(lines), ml)]
    tot = sum(len(' '.join(g)) for g in groups); cur = a
    for g in groups:
        d = (b - a) * len(' '.join(g)) / tot; n += 1
        srt += [str(n), f"{ts(cur).replace('.', ',')} --> {ts(cur + d).replace('.', ',')}", *g, '']
        cur += d
open('captions_en.srt', 'w').write('\n'.join(srt))
vtt = ['WEBVTT', ''] + [l.replace(',', '.') if '-->' in l else l for l in srt]
open('captions_en.vtt', 'w').write('\n'.join(vtt))
print(n, 'cues')
