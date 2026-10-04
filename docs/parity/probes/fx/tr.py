import json
from concurrent.futures import ThreadPoolExecutor
exec(open('mb.py').read().split("HDR=")[0])
import io, contextlib
KINDS="cut crossfade additive-dissolve dip-to-color wipe slide push cover reveal zoom-in zoom-out spin whip-pan circle-open circle-close iris clock-wipe radial-wipe barn-door blinds luma blur glitch pixelize flip cube page-curl film-roll stripe squash shuffle carousel light-leak morph".split()
def scene(k):
    extra='matte="lm"' if k=='luma' else ''
    return f'''<scene version="1.1"><project width="128" height="96" fps="10" duration="3" background="#303030"/><assets><image id="tc" src="testcard.png" width="96" height="96"/><image id="tc2" src="testcard.png" width="96" height="96"/></assets>
<composition><layer id="a" asset="tc" x="16" y="0" end="2"/><layer id="b" asset="tc2" x="0" y="0" scaleX="1.3333" scaleY="1" start="2"/><shape id="lm" shape="rect" x="0" y="0" width="64" height="96" fill="#FFFFFF" visible="false"/>
<transition type="{k}" from="a" to="b" duration="1" {extra}/></composition></scene>'''
def one(k):
    name='tr_'+k
    p=HERE/f'{name}.xml'; p.write_text(scene(k))
    out={}
    for t in (1.7,2.0,2.3):
        tag=f'{name}_{t}'
        r=subprocess.run([RS,'render',p.name,'-t',str(t),'-o',f'{tag}_rs.png'],cwd=HERE,capture_output=True,text=True)
        if r.returncode: out[t]='rs-err'; continue
        env=dict(os.environ,PYTHONPATH=str(S/'wt-matrix'))
        r=subprocess.run([PY,'-m','scenerender.cli','still',p.name,'-t',str(t),'-o',f'{tag}_py.png','--lenient'],cwd=HERE,capture_output=True,text=True,env=env)
        if r.returncode: out[t]='py-err'; continue
        a=np.asarray(Image.open(HERE/f'{tag}_rs.png').convert('RGB'));b=np.asarray(Image.open(HERE/f'{tag}_py.png').convert('RGB'))
        out[t]=round(psnr(a,b),1)
    return k,out
with ThreadPoolExecutor(8) as ex:
    for k,o in ex.map(one,KINDS): print(f'{k:18s}',o)
