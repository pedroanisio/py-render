import subprocess, os, numpy as np
from PIL import Image
exec(open('gen.py').read().split("def scene")[0])
def psnr(a,b):
    d=(a.astype(float)-b.astype(float)); mse=(d**2).mean()
    return 99.0 if mse==0 else float(10*np.log10(255**2/mse))
def cmp(name, xml, t):
    p=HERE/f'{name}.xml'; p.write_text(xml)
    r=subprocess.run([RS,'render',p.name,'-t',str(t),'-o',f'{name}_rs.png'],cwd=HERE,capture_output=True,text=True)
    if r.returncode: print(name,'rs err',r.stderr[-200:]); return
    env=dict(os.environ,PYTHONPATH=str(S/'wt-matrix'))
    r=subprocess.run([PY,'-m','scenerender.cli','still',p.name,'-t',str(t),'-o',f'{name}_py.png','--lenient'],cwd=HERE,capture_output=True,text=True,env=env)
    if r.returncode: print(name,'py err',r.stderr[-300:]); return
    a=np.asarray(Image.open(HERE/f'{name}_rs.png').convert('RGB'));b=np.asarray(Image.open(HERE/f'{name}_py.png').convert('RGB'))
    d=np.abs(a.astype(int)-b.astype(int)).max(-1)
    print(f'{name:10s} t={t} psnr {psnr(a,b):.1f} off2 {(d>2).mean()*100:.1f}%')
HDR='<scene version="1.1"><project width="160" height="96" fps="10" duration="2" background="#303030" {pa}/><assets><image id="tc" src="testcard.png" width="96" height="96"/></assets>'
mv='<animate property="x"><key time="0" value="0"/><key time="1" value="80"/></animate>'
sq=lambda id,extra='',kids=mv:f'<shape id="{id}" shape="rect" x="8" y="20" width="24" height="24" fill="#FFCC00" {extra}>{kids}</shape>'
mbp='motionBlur="true" shutterAngle="180" shutterPhase="-90" motionBlurSamples="16"'
cmp('m1',HDR.format(pa=mbp)+f'<composition>{sq("a")}</composition></scene>',0.5)
cmp('m1b',HDR.format(pa=mbp)+f'<composition>{sq("a")}</composition></scene>',0.0)
cmp('m2',HDR.format(pa=mbp)+f'<composition><layer id="bg" asset="tc" x="30" y="0"/>{sq("a","blend=\"multiply\"")}</composition></scene>',0.5)
cmp('m3',HDR.format(pa=mbp)+f'<composition>{sq("a")}<adjustment id="adj" effects="e"/></composition><effects><effect id="e" type="invert"/></effects></scene>',0.5)
cmp('m4',HDR.format(pa='motionBlur="false" shutterPhase="-90" motionBlurSamples="16"')+f'<composition>{sq("a","motionBlur=\"on\"")}</composition></scene>',0.5)
cmp('m5',HDR.format(pa=mbp+' shutterAngle="360"')+f'<composition>{sq("a")}</composition></scene>',0.5)
cmp('m6',HDR.format(pa=mbp)+f'<composition><group id="g" x="0" y="0"><animate property="rotation"><key time="0" value="0"/><key time="1" value="90"/></animate><shape id="a" shape="rect" x="40" y="10" width="20" height="40" fill="#FFCC00"/></group></composition></scene>',0.5)
cmp('m7',HDR.format(pa=mbp)+f'<composition>{sq("a")}<layer id="s" asset="tc" x="60" y="0" effects="e"><animate property="x"><key time="0" value="0"/><key time="1" value="50"/></animate></layer></composition><effects><effect id="e" type="glow" radius="4" threshold="0.5"/></effects></scene>',0.5)
