import sys, json
sys.path.insert(0,'/tmp/claude-1000/-home-admin-codebases-py-render/79c66742-8ab0-4941-b43a-ababfc401a00/scratchpad/wt-matrix')
from scenerender.render import Renderer
from scenerender.document import ln
from scenerender.evaluator import Ctx
def dump(path, t, **kw):
    r = Renderer.open(path, strict=False, **kw)
    rc, ev = r.rc, r.rc.ev
    out=[]
    def walk(parent, ctx, depth):
        for c in rc.child_order(parent, ctx):
            ec = rc.enter_node(c, ctx)
            act = rc.active(c, ctx)
            rec = dict(id=c.get('id'), kind=ln(c), active=act, tl=round(ec.t,6), depth=depth)
            if act:
                rec.update(x=ev.num(c,'x',ec,0), y=ev.num(c,'y',ec,0), op=ev.num(c,'opacity',ec,1), rot=ev.num(c,'rotation',ec,0))
                out.append(rec)
                if ln(c) in ('group','sequence'):
                    from scenerender.nodes.core import _repeat_vars
                    walk(c, _repeat_vars(c, ev.child_ctx(c, ec)), depth+1)
                elif ln(c)=='layer':
                    from scenerender.nodes.core import media_time
                    a = rc.layer_asset(c, ec)
                    if a is not None:
                        rec['text']=a.get('text')
                        d = a.get('duration'); rec['src']=media_time(rc,c,ec,float(d) if d else None)
                elif ln(c)=='instance':
                    sym = doc_sym = rc.doc.ids.get(ev.str(c,'symbol',ec))
                    sc = ev.enter_instance(c, ec, sym)
                    if sc is None: rec['active']=False
                    else: walk(sym, sc, depth+1)
            else: out.append(rec)
    walk(rc.doc.section('composition'), Ctx(t=t,comp_t=t,frame=round(t*float(rc.doc.fps))), 0)
    return out
if __name__=='__main__':
    for rec in dump(sys.argv[1], float(sys.argv[2])): print(rec)
