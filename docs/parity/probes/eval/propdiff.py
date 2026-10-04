import sys, json
sys.path.insert(0,'.')
from rs import rs
sys.path.insert(0,'/tmp/claude-1000/-home-admin-codebases-py-render/79c66742-8ab0-4941-b43a-ababfc401a00/scratchpad/wt-matrix')
from scenerender.render import Renderer
from scenerender.evaluator import Ctx
from scenerender.compositor import Ctx as C2
def run(path, times, nodes=None, **kw):
    r=Renderer.open(path,strict=False); rc=r.rc; ev=rc.ev
    for t in times:
        d,diag=rs(path,t)
        if d is None: print('RS ERR',diag[:300]); return
        for n in d['nodes']:
            if nodes and n['id'] not in nodes: continue
            el=rc.doc.ids.get(n['id'])
            if el is None: continue
            # python ctx: need enter_node for node-local things
            ctx=Ctx(t=t,comp_t=t,frame=round(t*float(rc.doc.fps)))
            ctx=rc.enter_node(el,ctx)
            for k,v in n['props'].items():
                try: pv=ev.get(el,k,ctx)
                except Exception as e: pv='EXC '+str(e)[:60]
                print(f"t={t} {n['id']}.{k}: RS={v!r}  PY={pv!r}")
if __name__=='__main__':
    run(sys.argv[1],[float(x) for x in sys.argv[2:]])
