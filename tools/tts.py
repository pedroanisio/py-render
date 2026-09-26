import xml.etree.ElementTree as ET, subprocess, json, re, os
r=ET.parse('/mnt/user-data/uploads/the-last-floor-episode-01_manga_scene.xml').getroot()
V={'REN':('en-US-BrianNeural','-3%','+8Hz'),'YUKI':('en-US-AvaNeural','+0%','+0Hz'),'AYA':('en-US-EmmaNeural','+2%','+12Hz'),
   'NAGAI':('en-US-ChristopherNeural','-12%','-14Hz'),'MARI':('en-US-MichelleNeural','-10%','-4Hz'),'WOMAN':('en-US-MichelleNeural','-10%','-4Hz'),
   'GIRL':('en-US-AnaNeural','-15%','-6Hz'),'OFFICE WORKER':('en-US-GuyNeural','+0%','+0Hz'),'DISPATCHER':('en-US-JennyNeural','+4%','+0Hz'),'CALLER':('en-US-EricNeural','-8%','+0Hz')}
out={}
for t in r.find('assets').findall('text'):
    i=t.get('id')
    if not i.startswith('dialogue'): continue
    who,line=t.get('text').split('\n',1); base=re.sub(r'\s*\(.*\)','',who)
    v,rate,pitch=V[base]; f=f'vo/{i}.mp3'
    if not os.path.exists(f) or os.path.getsize(f)==0:
        for k in range(3):
            p=subprocess.run(['edge-tts','--voice',v,f'--rate={rate}',f'--pitch={pitch}','--text',line.replace('\n',' '),'--write-media',f],capture_output=True,text=True)
            if p.returncode==0 and os.path.getsize(f)>0: break
    out[i]=dict(who=who,line=line,voice=v)
json.dump(out,open('vo/lines.json','w'),indent=1); print(len(out))
