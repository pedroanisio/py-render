import sys, numpy as np
sys.path.insert(0,'/tmp/claude-1000/-home-admin-codebases-py-render/79c66742-8ab0-4941-b43a-ababfc401a00/scratchpad/wt-matrix')
from scenerender.render import Renderer
from scenerender.document import ln
from scenerender.evaluator import Ctx
from scenerender.nodes.core import _repeat_vars
def world(path, t, **kw):
    r=Renderer.open(path,strict=False,**kw); rc=r.rc; ev=rc.ev; out={}
    def walk(parent, ctx, PM, box):
        layout=rc.layout_positions(parent, ctx, box)
        for c in rc.child_order(parent, ctx):
            if ln(c) in ('adjustment',): continue
            if not rc.active(c, ctx): continue
            ec=rc.enter_node(c, ctx)
            M=rc.node_matrix(c, ec, PM, box, layout.get(c))
            size=rc.node_size(c, rc.node_ctx(c, ctx), box, layout.get(c))
            out[c.get('id')]=dict(M=M, size=size, origin=(M@np.array([0,0,1.]))[:2].round(6).tolist(), x1=(M@np.array([1,0,1.]))[:2].round(6).tolist())
            if ln(c) in ('group','sequence'):
                walk(c, _repeat_vars(c, ev.child_ctx(c, ec)), M, size)
    walk(rc.doc.section('composition'), Ctx(t=t,comp_t=t,frame=round(t*float(rc.doc.fps))), rc.root_matrix, (rc.doc.width, rc.doc.height))
    return out
if __name__=='__main__':
    for k,v in world(sys.argv[1], float(sys.argv[2])).items(): print(k, v['origin'], v['size'])
