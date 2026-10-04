import json, numpy as np
from rlottie_python import LottieAnimation
def anim(shapes, tr=None, extra=None):
    ks = {"o":{"a":0,"k":100},"r":{"a":0,"k":0},"p":{"a":0,"k":[50,50,0]},"a":{"a":0,"k":[0,0,0]},"s":{"a":0,"k":[100,100,100]}}
    if tr: ks.update(tr)
    d={"v":"5.7","fr":30,"ip":0,"op":30,"w":100,"h":100,"layers":[{"ty":4,"ind":1,"nm":"l","ip":0,"op":30,"st":0,"ks":ks,"shapes":shapes}]}
    if extra: d.update(extra)
    return d
def render(d):
    a=LottieAnimation.from_data(json.dumps(d))
    im=a.render_pillow_frame(0)
    return np.asarray(im.convert("RGBA")).astype(float)
def rect(w=40,h=40): return {"ty":"rc","p":{"a":0,"k":[0,0]},"s":{"a":0,"k":[w,h]},"r":{"a":0,"k":0}}
fill={"ty":"fl","c":{"a":0,"k":[1,0,0,1]},"o":{"a":0,"k":100}}
tr0={"ty":"tr","p":{"a":0,"k":[0,0]},"a":{"a":0,"k":[0,0]},"s":{"a":0,"k":[100,100]},"r":{"a":0,"k":0},"o":{"a":0,"k":100}}
def grp(items): return {"ty":"gr","it":items+[tr0]}
base=render(anim([grp([rect(),fill])]))
def diff(name,d):
    r=render(d); print(name, "alpha sum", r[...,3].sum()/255, "vs base", base[...,3].sum()/255, "changed" if np.abs(r-base).sum()>0 else "SAME")
diff("zigzag", anim([grp([rect(),{"ty":"zz","s":{"a":0,"k":10},"r":{"a":0,"k":4},"pt":{"a":0,"k":1}},fill])]))
diff("pucker", anim([grp([rect(),{"ty":"pb","a":{"a":0,"k":50}},fill])]))
diff("twist", anim([grp([rect(),{"ty":"tw","a":{"a":0,"k":90},"c":{"a":0,"k":[0,0]}},fill])]))
diff("offset", anim([grp([rect(),{"ty":"op","a":{"a":0,"k":10},"lj":1,"ml":4},fill])]))
diff("roundcorners", anim([grp([rect(),{"ty":"rd","r":{"a":0,"k":10}},fill])]))
diff("trim", anim([grp([rect(),{"ty":"tm","s":{"a":0,"k":0},"e":{"a":0,"k":50},"o":{"a":0,"k":0},"m":1},{"ty":"st","c":{"a":0,"k":[1,0,0,1]},"o":{"a":0,"k":100},"w":{"a":0,"k":4},"lc":1,"lj":1}])]))
diff("skew", anim([grp([rect(),fill])], tr={"sk":{"a":0,"k":30},"sa":{"a":0,"k":0}}))
diff("splitpos", anim([grp([rect(),fill])], tr={"p":{"s":True,"x":{"a":0,"k":80},"y":{"a":0,"k":20}}}))
diff("merge-sub", anim([grp([rect(40,40),{"ty":"rc","p":{"a":0,"k":[0,0]},"s":{"a":0,"k":[20,20]},"r":{"a":0,"k":0}},{"ty":"mm","mm":3},fill])]))
diff("repeater", anim([grp([rect(10,10),{"ty":"rp","c":{"a":0,"k":3},"o":{"a":0,"k":0},"m":1,"tr":{"ty":"tr","p":{"a":0,"k":[20,0]},"a":{"a":0,"k":[0,0]},"s":{"a":0,"k":[100,100]},"r":{"a":0,"k":0},"so":{"a":0,"k":100},"eo":{"a":0,"k":100}}},fill])]))
diff("star-round", anim([grp([{"ty":"sr","sy":1,"p":{"a":0,"k":[0,0]},"r":{"a":0,"k":0},"pt":{"a":0,"k":5},"or":{"a":0,"k":30},"ir":{"a":0,"k":15},"os":{"a":0,"k":100},"is":{"a":0,"k":100}},fill])]))
diff("dash", anim([grp([rect(),{"ty":"st","c":{"a":0,"k":[1,0,0,1]},"o":{"a":0,"k":100},"w":{"a":0,"k":4},"lc":1,"lj":1,"d":[{"n":"d","nm":"d","v":{"a":0,"k":5}},{"n":"g","nm":"g","v":{"a":0,"k":5}}]}])]))
mask=lambda m: {"hasMask":True,"masksProperties":[{"inv":False,"mode":m,"pt":{"a":0,"k":{"c":True,"v":[[40,40],[60,40],[60,60],[40,60]],"i":[[0,0]]*4,"o":[[0,0]]*4}},"o":{"a":0,"k":100},"x":{"a":0,"k":0}}]}
for m in ["a","s","i","l","d","f"]:
    d=anim([grp([rect(60,60),fill])]); d["layers"][0].update(mask(m))
    r=render(d); print("mask",m,r[...,3].sum()/255)
d=anim([grp([rect(),fill])],tr={"s":{"a":0,"k":[100,100,100]}}); d["layers"][0]["ks"]["p"]={"a":0,"k":[50,50,0]}
