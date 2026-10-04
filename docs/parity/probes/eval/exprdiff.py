import sys, json, subprocess, os, tempfile
sys.path.insert(0,'.')
from rs import B
sys.path.insert(0,'/tmp/claude-1000/-home-admin-codebases-py-render/79c66742-8ab0-4941-b43a-ababfc401a00/scratchpad/wt-matrix')
from scenerender.render import Renderer
from scenerender.evaluator import Ctx
EXPRS = [l.rstrip('\n') for l in open(sys.argv[1]) if l.strip()]
TIMES=[0.0,0.37,1.3,2.9]
def scene(e, prop='x'):
    e=e.replace('&','&amp;').replace('<','&lt;')
    return f'''<scene version="1.1"><project width="1000" height="500" fps="10" duration="10" seed="1"/>
<parameters><param id="n" type="number" default="3"/></parameters>
<assets><image id="img" src="img.png" width="100" height="50"/></assets>
<composition><layer id="a" asset="img" x="5"><expression property="{prop}">{e}</expression></layer>
<layer id="b" asset="img" x="7" y="9"/></composition></scene>'''
def rs_val(path,t):
    p=subprocess.run([B,'eval',path,'--time',str(t),'--format','json','--no-assets'],capture_output=True,text=True)
    out=p.stdout
    if p.returncode: 
        msg=(out+p.stderr).strip().splitlines()
        return ('ERR',next((l for l in msg if 'error' in l),msg[0] if msg else '')[:90])
    i=out.find('\n{\n'); i=0 if out.startswith('{\n') else i+1
    d=json.loads(out[i:]); n=[n for n in d['nodes'] if n['id']=='a'][0]
    return n['props'].get('x'), n['world'][4]
def py_val(path,t):
    r=Renderer.open(path,strict=False); rc=r.rc; ev=rc.ev
    el=rc.doc.ids['a']; ctx=Ctx(t=t,comp_t=t,frame=round(t*10))
    try:
        v=ev.get(el,'x',ctx)
    except Exception as e: return ('ERR',str(e)[:90])
    return v
os.makedirs('ed',exist_ok=True)
for k,e in enumerate(EXPRS):
    path=f'ed/e{k}.xml'; open(path,'w').write(scene(e))
    res=[]
    for t in TIMES:
        r=rs_val(path,t); 
        try: p=py_val(path,t)
        except Exception as ex: p=('PYEXC',str(ex)[:80])
        res.append((t,r,p))
    # compare
    bad=False; line=[]
    for t,r,p in res:
        if r[0]=='ERR' or (isinstance(p,tuple) and p and p[0] in('ERR','PYEXC')):
            ok = (r[0]=='ERR' and isinstance(p,tuple) and p and p[0] in('ERR','PYEXC'))
        else:
            try:
                rv=float(r[1]); pv=float(p) if not isinstance(p,(tuple,list)) else float(p[0])
                ok = abs(rv-pv)<1e-6*max(1,abs(rv)) or (rv!=rv and pv!=pv)
            except Exception: ok=False
        if not ok: bad=True
        line.append((t,r,p,ok))
    print(('DIFF ' if bad else 'ok   ')+e)
    if bad:
        for t,r,p,ok in line:
            if not ok: print('      t=%s RS=%s PY=%s'%(t,r,p)); break
