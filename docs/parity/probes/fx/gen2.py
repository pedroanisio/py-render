import sys, json, subprocess, os
from pathlib import Path
import numpy as np
from PIL import Image
from concurrent.futures import ThreadPoolExecutor
exec(open('gen.py').read().split("def scene")[0])  # EXPL, paths
EXPL['letterbox']='size="2.39"'
CELL=128
def scene1(t, attrs, adj=False):
    anim=''
    if t in('echo','posterize-time','pixel-motion-blur'):
        anim='<animate property="x"><key time="0" value="6"/><key time="1" value="26"/></animate>'
    extra=''
    if t in ('displacement-map','difference-key'):
        extra='<layer id="map" asset="tc" x="0" y="0" visible="false"/><layer id="plate" asset="tc" x="0" y="0" visible="false"/>'
    node = f'<adjustment id="n" effects="e"/>' if adj else f'<layer id="n" asset="tc" x="16" y="16" effects="e">{anim}</layer>'
    base = '<layer id="bg" asset="tc" x="16" y="16"/>' if adj else ''
    return f'''<scene version="1.1"><project width="128" height="128" fps="10" duration="1" background="#303030"/>
<assets><image id="tc" src="testcard.png" width="96" height="96"/></assets>
<paints><linearGradient id="grad"><stop offset="0" color="#000000"/><stop offset="1" color="#FF8800"/></linearGradient></paints>
<composition>{extra}{base}{node}</composition>
<lights><light id="pl" type="point" x="60" y="50"/></lights>
<effects><effect id="e" type="{t}" {attrs}/></effects></scene>'''
def psnr(a,b):
    d=(a.astype(float)-b.astype(float)); mse=(d**2).mean()
    return 99.0 if mse==0 else float(10*np.log10(255**2/mse))
def one(args):
    t,expl,adj=args
    tag=f'{t}_{int(expl)}_{int(adj)}'
    p=HERE/f's_{tag}.xml'; p.write_text(scene1(t,EXPL.get(t,'') if expl else '',adj))
    r=subprocess.run([RS,'render',p.name,'-t','0.5','-o',f's_{tag}_rs.png'],cwd=HERE,capture_output=True,text=True)
    if r.returncode: return args,{'err':'rs '+r.stderr[-200:]}
    env=dict(os.environ,PYTHONPATH=str(S/'wt-matrix'))
    r=subprocess.run([PY,'-m','scenerender.cli','still',p.name,'-t','0.5','-o',f's_{tag}_py.png','--lenient'],cwd=HERE,capture_output=True,text=True,env=env)
    if r.returncode: return args,{'err':'py '+r.stderr[-200:]}
    a=np.asarray(Image.open(HERE/f's_{tag}_rs.png').convert('RGB'));b=np.asarray(Image.open(HERE/f's_{tag}_py.png').convert('RGB'))
    d=np.abs(a.astype(int)-b.astype(int)).max(-1)
    return args,{'psnr':round(psnr(a,b),1),'off2':round(float((d>2).mean()*100),1)}
if __name__=='__main__':
    effs=list(EXPL)
    jobs=[(t,e,False) for t in effs for e in (True,False)]
    jobs+=[(t,True,True) for t in ('vignette','letterbox','chromatic-aberration','fractal-noise','light-leak','lens-flare','god-rays','light-sweep','radial-blur','zoom-blur','twirl','spherize','bulge','lens-distortion','mirror','kaleidoscope','tile','scanlines','displacement-map')]
    with ThreadPoolExecutor(8) as ex: res=list(ex.map(one,jobs))
    out={}
    for (t,e,a),r in res: out[f'{t}|{int(e)}|{int(a)}']=r
    json.dump(out,open('res2.json','w'),indent=1)
    for t in effs:
        def f(k): r=out.get(k); return '-' if r is None else (r.get('err','ERR')[:40] if 'err' in r else f"{r['psnr']}/{r['off2']}")
        print(f'{t:22s} expl {f(t+"|1|0"):12s} def {f(t+"|0|0"):12s} adj {f(t+"|1|1")}')
