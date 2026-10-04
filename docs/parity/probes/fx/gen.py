import sys, json, subprocess, os
from pathlib import Path
import numpy as np
from PIL import Image
S=Path('/tmp/claude-1000/-home-admin-codebases-py-render/79c66742-8ab0-4941-b43a-ababfc401a00/scratchpad')
HERE=S/'fxp'
RS='/home/admin/src/rs-scene-render/target/release/scene-render'
PY='/home/admin/codebases/py-render/.venv/bin/python'
CELL=128; COLS=6
# (name, attrs-string)  explicit variant
EXPL = {
 'blur':'radius="3"', 'directional-blur':'radius="6" angle="30" samples="16"', 'radial-blur':'angle="12" samples="16"',
 'zoom-blur':'amount="1" samples="16"', 'lens-blur':'radius="6" levels="6" angle="10" samples="16" threshold="0.6" amount="1"',
 'tilt-shift':'radius="6" size="20" centerY="48"', 'pixel-motion-blur':'amount="1" samples="8"',
 'glow':'radius="4" threshold="0.5" intensity="1"','bloom':'radius="4" threshold="0.5" intensity="1"','halation':'radius="4" threshold="0.5" intensity="1"',
 'drop-shadow':'radius="6" offsetX="6" offsetY="8" color="#000000FF"','inner-shadow':'radius="4" offsetX="3" offsetY="3"','inner-glow':'radius="4" color="#FFFF00FF"',
 'long-shadow':'size="30" angle="45" color="#00000099"','stroke':'size="3" color="#FF0000FF" position="outside"','outline':'size="3" color="#FF0000FF" position="outside"',
 'matte-choke':'amount="3" softness="0"',
 'color-grade':'saturation="0.5" contrast="1.3" brightness="0.05"','lift-gamma-gain':'lift="0.1,0.0,0.0" gamma="1.2,1,1" gain="1.1,1,0.9"',
 'cdl':'slope="1.2,1,0.9" offset="0.02,0,0" power="1.1,1,1" saturation="0.8"','curves':'curve="0,0 0.5,0.6 1,1"','levels':'inputBlack="0.1" inputWhite="0.9" gamma="1.2"',
 'white-balance':'temperature="30" tint="10"','exposure':'exposure="1"','hue-saturation':'hue="40" saturation="1.3" brightness="0.05"','tonemap':'tonemapper="aces" exposure="0.5"',
 'tint':'color="#3080FFFF" amount="0.7"','tritone':'color="#FF8000FF" amount="0.8"','grayscale':'amount="0.7"','sepia':'amount="0.7"','invert':'amount="0.8"',
 'posterize':'levels="4"','threshold':'threshold="0.5"','color-overlay':'color="#FF0000FF" amount="0.5"','selective-color':'hue="0" tolerance="0.2" saturation="1.5" brightness="0.1"',
 'vignette':'amount="0.8" radius="30" softness="0.5"','letterbox':'size="2.39"','scanlines':'size="1" amount="0.8"',
 'film-grain':'amount="0.5" size="1" seed="3"','noise':'amount="0.2" seed="3"','glitch':'amount="6" size="4" seed="3"',
 'chromatic-aberration':'amount="4"','rgb-split':'offsetX="4" offsetY="2"','vhs':'amount="1" seed="3"','halftone':'size="6" angle="45" amount="1"',
 'pixelate':'size="8"','mosaic':'size="8"','sharpen':'amount="1"','unsharp-mask':'radius="2" amount="1" threshold="0.1"','emboss':'angle="45" relief="2" amount="1"','bevel':'size="4" angle="-45" intensity="1"',
 'echo':'samples="4" amount="2" intensity="0.6"','posterize-time':'frequency="2"','mirror':'angle="90"','kaleidoscope':'levels="6" angle="0"','tile':'amount="3" size="2"',
 'turbulent-displace':'amount="6" size="30" seed="3"','wave-warp':'amount="4" size="30"','ripple':'amount="4" size="20"','twirl':'angle="60" radius="40"',
 'spherize':'amount="0.8" radius="40"','bulge':'amount="1.5" radius="40"','lens-distortion':'amount="1"','heat-haze':'amount="2" size="20" seed="3"',
 'luma-key':'threshold="0.5" softness="0.1"','spill-suppress':'keyColor="#00FF00FF" spill="1"',
 'chroma-key':'keyColor="#00FF00FF" tolerance="0.3" softness="0.1"',
 'fill':'color="#FF8000FF"','fractal-noise':'size="20" intensity="1" amount="0.8" seed="3"','god-rays':'amount="1" threshold="0.5" samples="16" intensity="1"',
 'lens-flare':'centerX="50" centerY="40" intensity="1" size="1"','light-leak':'intensity="1" seed="3"','light-sweep':'size="20" intensity="1"',
 'gradient-map':'paint="url(#grad)" amount="1"','gradient-overlay':'paint="url(#grad)" angle="0" amount="0.6"',
 'lighting':'lights="pl" intensity="0.8" angle="-45"','lut':'src="inv.cube" space="srgb"',
 'displacement-map':'source="map" amount="6"','difference-key':'source="plate" tolerance="0.1" softness="0.05"',
}
SPECIAL={'lut'}
def scene(effs, expl):
    n=len(effs); rows=(n+COLS-1)//COLS
    W,H=CELL*COLS,CELL*rows
    layers=[];fx=[]
    for i,t in enumerate(effs):
        x,y=(i%COLS)*CELL+16,(i//COLS)*CELL+16
        attrs=EXPL.get(t,'') if expl else ''
        anim=''
        if t in('echo','posterize-time','pixel-motion-blur'):
            anim='<animate property="x"><key time="0" value="%d"/><key time="1" value="%d"/></animate>'%(x-10,x+10)
        layers.append(f'<layer id="n{i}" asset="tc" x="{x}" y="{y}" effects="e{i}">{anim}</layer>')
        fx.append(f'<effect id="e{i}" type="{t}" {attrs}/>')
    extra=''
    if 'displacement-map' in effs or 'difference-key' in effs:
        extra+='<layer id="map" asset="tc" x="0" y="0" visible="false"/><layer id="plate" asset="tc" x="0" y="0" visible="false"/>'
    return f'''<scene version="1.1"><project width="{W}" height="{H}" fps="10" duration="1" background="#303030"/>
<assets><image id="tc" src="testcard.png" width="96" height="96"/></assets>
<paints><linearGradient id="grad"><stop offset="0" color="#000000"/><stop offset="1" color="#FF8800"/></linearGradient></paints>
<composition>{extra}{''.join(layers)}</composition>
<lights><light id="pl" type="point" x="60" y="50"/></lights>
<effects>{''.join(fx)}</effects></scene>'''
def psnr(a,b):
    d=(a.astype(float)-b.astype(float))
    mse=(d**2).mean()
    return 99 if mse==0 else 10*np.log10(255**2/mse)
def run(effs, expl, t, tag):
    p=HERE/f'{tag}.xml'; p.write_text(scene(effs,expl))
    r=subprocess.run([RS,'render',p.name,'-t',str(t),'-o',f'{tag}_rs.png'],cwd=HERE,capture_output=True,text=True)
    if r.returncode: return {'_err':'rs '+r.stderr[-300:]}
    env=dict(os.environ,PYTHONPATH=str(S/'wt-matrix'))
    r=subprocess.run([PY,'-m','scenerender.cli','still',p.name,'-t',str(t),'-o',f'{tag}_py.png','--lenient'],cwd=HERE,capture_output=True,text=True,env=env)
    if r.returncode: return {'_err':'py '+r.stderr[-300:]}
    a=np.asarray(Image.open(HERE/f'{tag}_rs.png').convert('RGB'));b=np.asarray(Image.open(HERE/f'{tag}_py.png').convert('RGB'))
    out={}
    for i,e in enumerate(effs):
        x,y=(i%COLS)*CELL,(i//COLS)*CELL
        ca,cb=a[y:y+CELL,x:x+CELL],b[y:y+CELL,x:x+CELL]
        d=np.abs(ca.astype(int)-cb.astype(int)).max(-1)
        out[e]=(round(psnr(ca,cb),1),round(float((d>2).mean()*100),1))
    return out
if __name__=='__main__':
    effs=[e for e in EXPL if e!='letterbox']
    res={}
    for expl in (True,False):
        for k in range(0,len(effs),24):
            chunk=effs[k:k+24]
            r=run(chunk,expl,0.5,f'g{int(expl)}_{k}')
            res.setdefault('expl' if expl else 'def',{}).update(r)
    json.dump(res,open(HERE/'res.json','w'),indent=1)
    for e in effs: print(f'{e:22s} expl {res["expl"].get(e)}  def {res["def"].get(e)}')
