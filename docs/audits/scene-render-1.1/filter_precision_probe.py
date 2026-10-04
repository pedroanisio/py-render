"""Measure native linear-filter fractional precision against independent CPU samples.

Run with .venv/bin/python filter_precision_probe.py OUTPUT.json on each backend.
This is a diagnostic, not a replacement for the rendering regression controls.
"""
from pathlib import Path
import json,sys
import moderngl
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from scenerender import gl
ctx=gl.context()
rng=np.random.default_rng(42)
values=rng.uniform(.05,3.,(3,5)).astype('f4')
texture=ctx.texture((5,3),1,values.tobytes(),dtype='f4')
texture.filter=(moderngl.LINEAR,moderngl.LINEAR)
texture.repeat_x=texture.repeat_y=True
program=ctx.program(vertex_shader='''#version 410
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2-1,0,1);}''',fragment_shader='''#version 410
uniform sampler2D source;out vec4 color;
void main(){vec2 uv=gl_FragCoord.xy/vec2(64.,64.)*vec2(1.3,.7)+vec2(-.15,.071);color=vec4(textureLod(source,uv,0).r,uv,1.);}''')
fbo=ctx.simple_framebuffer((64,64),components=4,dtype='f4');fbo.use();texture.use(0);program['source']=0
ctx.vertex_array(program,[]).render(vertices=3)
pixels=np.frombuffer(fbo.read(components=4,dtype='f4'),np.float32).reshape(64,64,4)
xy=pixels[...,1:3].astype(np.float64)*[5,3]-.5
ij=np.floor(xy).astype(int);fraction=xy-ij
rows=[]
for bits in [None,7,8,9,10,12,16]:
 f=fraction if bits is None else np.floor(fraction*(2**bits)+.5)/(2**bits)
 x,y=ij[...,0],ij[...,1];fx,fy=f[...,0],f[...,1]
 expected=(values[y%3,x%5]*(1-fx)+values[y%3,(x+1)%5]*fx)*(1-fy)+(values[(y+1)%3,x%5]*(1-fx)+values[(y+1)%3,(x+1)%5]*fx)*fy
 error=np.abs(expected-pixels[...,0])
 rows.append({'fraction_bits':bits,'maximum_absolute_difference':float(error.max()),'rms_difference':float(np.sqrt(np.mean(error**2)))})
result={'renderer':{k:ctx.info.get(k) for k in ('GL_VENDOR','GL_RENDERER','GL_VERSION')},'input':'3x5 R32F texture, 4096 level-zero linear samples with periodic wrapping; CPU reference uses the GLSL-returned UVs','observations':rows}
Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
