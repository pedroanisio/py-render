exec(open('gen2.py').read().split("if __name__")[0])
import re
def one_off(t, attrs, dx, dy, adj=False):
    xml=scene1(t,attrs,adj).replace('x="16" y="16" effects="e"',f'x="{dx}" y="{dy}" effects="e"')
    return xml
def run(t,dx,dy):
    tag=f'o_{t}_{dx}_{dy}'
    p=HERE/f'{tag}.xml'; p.write_text(one_off(t,EXPL.get(t,''),dx,dy))
    r=subprocess.run([RS,'render',p.name,'-t','0.5','-o',f'{tag}_rs.png'],cwd=HERE,capture_output=True,text=True)
    env=dict(os.environ,PYTHONPATH=str(S/'wt-matrix'))
    r2=subprocess.run([PY,'-m','scenerender.cli','still',p.name,'-t','0.5','-o',f'{tag}_py.png','--lenient'],cwd=HERE,capture_output=True,text=True,env=env)
    a=np.asarray(Image.open(HERE/f'{tag}_rs.png').convert('RGB'));b=np.asarray(Image.open(HERE/f'{tag}_py.png').convert('RGB'))
    return round(psnr(a,b),1)
for t in ['pixelate','mosaic','scanlines','halftone','film-grain','noise','wave-warp','ripple','turbulent-displace','vhs','glitch','fractal-noise','blur','color-grade']:
    print(t, [run(t,dx,dy) for dx,dy in ((16,16),(19,21),(40,13))])
