import json, numpy as np, copy
from rlottie_python import LottieAnimation
def R(d,f=0):
    a=LottieAnimation.from_data(json.dumps(d)); return np.asarray(a.render_pillow_frame(f).convert("RGBA")).astype(float)
def ks(p=(50,50),**k):
    d={"o":{"a":0,"k":100},"r":{"a":0,"k":0},"p":{"a":0,"k":[p[0],p[1],0]},"a":{"a":0,"k":[0,0,0]},"s":{"a":0,"k":[100,100,100]}}; d.update(k); return d
tr0={"ty":"tr","p":{"a":0,"k":[0,0]},"a":{"a":0,"k":[0,0]},"s":{"a":0,"k":[100,100]},"r":{"a":0,"k":0},"o":{"a":0,"k":100}}
def rect(w,h,x=0,y=0): return {"ty":"rc","p":{"a":0,"k":[x,y]},"s":{"a":0,"k":[w,h]},"r":{"a":0,"k":0}}
fill=lambda c:{"ty":"fl","c":{"a":0,"k":c},"o":{"a":0,"k":100}}
def shapeLayer(items,ind=1,**kw):
    L={"ty":4,"ind":ind,"nm":"l%d"%ind,"ip":0,"op":60,"st":0,"ks":kw.pop("ks",ks()),"shapes":[{"ty":"gr","it":items+[tr0]}]}; L.update(kw); return L
def comp(layers,**extra):
    d={"v":"5.7","fr":30,"ip":0,"op":60,"w":100,"h":100,"layers":layers}; d.update(extra); return d
sq=lambda col=[1,0,0,1]: [rect(40,40),fill(col)]
# mask modes: base square 60 at center, mask small square 20
def mpath(x0,y0,x1,y1): return {"a":0,"k":{"c":True,"v":[[x0,y0],[x1,y0],[x1,y1],[x0,y1]],"i":[[0,0]]*4,"o":[[0,0]]*4}}
for m in "aniisdlf":
    pass
for m in ["a","s","i","l","d","f"]:
    L=shapeLayer([rect(60,60),fill([1,0,0,1])],ks=ks((50,50)))
    L["hasMask"]=True; L["masksProperties"]=[{"inv":False,"mode":m,"pt":mpath(-10,-10,10,10),"o":{"a":0,"k":100},"x":{"a":0,"k":0}}]
    r=R(comp([L])); print("mask",m,round(r[...,3].sum()/255))
# track matte: layer 1 = matte (small rect), layer2 = content with tt
for tt in [1,2,3,4]:
    A=shapeLayer([rect(20,20),fill([1,1,1,1])],ind=1,td=1)
    B=shapeLayer([rect(60,60),fill([1,0,0,1])],ind=2,tt=tt)
    r=R(comp([A,B])); print("matte",tt,round(r[...,3].sum()/255))
# time stretch & remap precomp
inner={"id":"c1","layers":[shapeLayer([rect(10,10),fill([1,0,0,1])],ks=dict(ks((10,50)),p={"a":1,"k":[{"t":0,"s":[10,50,0],"e":[90,50,0],"i":{"x":[1],"y":[1]},"o":{"x":[0],"y":[0]}},{"t":30,"s":[90,50,0]}]}))]}
pl={"ty":0,"ind":1,"nm":"pc","refId":"c1","ip":0,"op":60,"st":0,"w":100,"h":100,"ks":ks((0,0))}
d=comp([pl],assets=[inner])
def cx(r):
    ys,xs=np.nonzero(r[...,3]>128); return xs.mean() if len(xs) else None
print("precomp f15",cx(R(d,15)))
pl2=dict(pl,sr=2.0); d2=comp([pl2],assets=[inner]); print("stretch2 f30",cx(R(d2,30)))
pl3=dict(pl,tm={"a":0,"k":0.5}); d3=comp([pl3],assets=[inner]); print("remap0.5s f5",cx(R(d3,5)))
# clip precomp to size
inner2={"id":"c2","layers":[shapeLayer([rect(200,20),fill([1,0,0,1])],ks=ks((0,50)))]}
pl4=dict(pl,refId="c2",w=50,h=100); print("precomp clip width",R(comp([pl4],assets=[inner2]))[...,3].nonzero()[1].max())
# parenting
P=shapeLayer([rect(2,2),fill([0,0,0,0])],ind=1,ks=ks((60,60))); C=shapeLayer([rect(10,10),fill([1,0,0,1])],ind=2,parent=1,ks=ks((0,0)))
print("parent cx",cx(R(comp([P,C]))))
# gradient fill w/ opacity stops
g={"ty":"gf","t":1,"s":{"a":0,"k":[0,0]},"e":{"a":0,"k":[100,0]},"g":{"p":2,"k":{"a":0,"k":[0,1,0,0,1,1,0,0,0,1,0.5,1, 0,1,0.5,1]}},"o":{"a":0,"k":100}}
r=R(comp([shapeLayer([rect(100,100,50,50),g])])); print("grad alpha", r[50,5,3], r[50,95,3])
# stroke dash+trim + polystar etc done. text layer
