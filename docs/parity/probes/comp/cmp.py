import subprocess, sys, numpy as np
from PIL import Image
R="/home/admin/src/rs-scene-render/target/release/scene-render"
PYR="/tmp/claude-1000/-home-admin-codebases-py-render/79c66742-8ab0-4941-b43a-ababfc401a00/scratchpad/wt-matrix"
def run(scene, t, tag):
    subprocess.run([R,"render",scene,"--time",str(t),"-o",f"{tag}_rs.png"],check=True,capture_output=True)
    import os
    env=dict(os.environ,PYTHONPATH=PYR)
    r=subprocess.run(['/home/admin/codebases/py-render/.venv/bin/python',"-m","scenerender.cli","still",scene,"--time",str(t),"-o",f"{tag}_py.png","--lenient"],env=env,capture_output=True,text=True)
    if r.returncode: print(r.stderr[-500:])
    a=np.asarray(Image.open(f"{tag}_rs.png").convert("RGB")).astype(int)
    b=np.asarray(Image.open(f"{tag}_py.png").convert("RGB")).astype(int)
    mse=((a-b)**2).mean()
    psnr=99 if mse==0 else 10*np.log10(255**2/mse)
    print(tag,t,"PSNR %.1f"%psnr,"off>2: %.2f%%"%(100*(np.abs(a-b).max(-1)>2).mean()))
    return a,b
if __name__=="__main__":
    run(sys.argv[1], float(sys.argv[2]), sys.argv[3])
