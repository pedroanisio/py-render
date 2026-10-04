"""Check nearest borders and float interpolation on the selected GL backend.

Run from the repository with .venv/bin/python sampler_probe.py OUTPUT.json.
The program uses analytical controls; it does not infer conformance from exit 0.
"""
from pathlib import Path
import json
import sys

import moderngl
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scenerender.three import renderer as gpu, shaders
from scenerender.three.materials import Material


def run():
    resources = gpu.res()
    ctx = resources.ctx
    program = ctx.program(vertex_shader='''#version 410
      void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2-1,0,1);}''',
        fragment_shader='''#version 410
      uniform sampler2D image; uniform vec2 uv; uniform ivec4 settings; uniform vec4 border; out vec4 color;
      '''+shaders.MAP_SAMPLE+'''
      void main(){color=mapSample(image,uv,settings,border);}''')
    target = ctx.simple_framebuffer((1, 1), components=4, dtype='f4')
    vao = ctx.vertex_array(program, [])
    target.use()
    ctx.disable(ctx.DEPTH_TEST | ctx.BLEND | ctx.CULL_FACE)
    cases = []
    for name, source, coordinate, sampler, expected in [
        ('nearest_constant_border', np.ones((4, 4)), (-.1, .5),
         {'wrapS': 33069, 'wrapT': 33071, 'minFilter': 9728, 'magFilter': 9728, 'border': (.2,)*4}, .2),
        ('float_linear_interpolation', np.array([[.1, .9], [.3, .7]]), (.35, .7),
         {'wrapS': 10497, 'wrapT': 10497, 'minFilter': 9729, 'magFilter': 9729}, .368),
    ]:
        material = Material({'mapSamplers': {'baseColorMap': sampler}},
                            {'baseColorMap': np.repeat(source.astype(np.float32)[..., None], 4, -1)})
        texture = gpu._material_texture(ctx, material, 'baseColorMap', {})
        texture.use(0)
        program['image'], program['uv'] = 0, coordinate
        program['settings'] = gpu._map_sampler(material, 'baseColorMap')
        program['border'] = gpu._map_border(material, 'baseColorMap')
        vao.render(vertices=3)
        actual = float(np.frombuffer(target.read(components=4, dtype='f4'), np.float32)[0])
        cases.append({'name': name, 'actual': actual, 'expected': expected,
                      'absolute_difference': abs(actual-expected), 'within_1e-6': abs(actual-expected) <= 1e-6})
        texture.release()
    samplers = sum(getattr(resources.main[name], 'gl_type', None) in (35678, 35680, 36289)
                   for name in resources.main)
    vertex_samplers = int('t_extensionUV' in resources.main)
    attributes = {name: resources.main[name].location for name in resources.main
                  if isinstance(resources.main[name], moderngl.Attribute)}
    result = {'renderer': {key: ctx.info.get(key) for key in ('GL_VENDOR', 'GL_RENDERER', 'GL_VERSION')},
              'vertex_attributes': sum(location >= 0 for location in attributes.values()),
              'vertex_attribute_locations': attributes,
              'texture_samplers': samplers, 'vertex_texture_samplers': vertex_samplers,
              'fragment_texture_samplers': samplers-vertex_samplers, 'observations': cases}
    vao.release(); target.release(); program.release()
    return result


if __name__ == '__main__':
    result = run()
    Path(sys.argv[1]).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
