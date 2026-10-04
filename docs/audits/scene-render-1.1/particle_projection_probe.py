"""Check non-affine contact mappings against independent pinhole equations.

Run from the repository with .venv/bin/python. Six XSD-valid scenes and results
are retained under /tmp/scene-render-particle-projection-inspection. Assertions
verify first-contact velocities while a tilted plane and camera both move.
"""
from pathlib import Path
import json
import math
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scenerender.evaluator import Ctx
from scenerender.nodes.particle_collisions import snapshots
from scenerender.nodes.particles import get_emitter, particles_at
from scenerender.render import Renderer


ROOT = Path('/tmp/scene-render-particle-projection-inspection')


def run(placement, angle):
    wall = '<shape id="wall" shape="rect" x="55" y="20" width="10" height="60"><rigidBody type="static"/></shape>'
    if placement == 'leaf':
        body = wall.replace('shape="rect"', f'shape="rect" threeD="true" rotationY="{angle}"').replace(
            '<rigidBody', '<expression property="x">55+30*time</expression><rigidBody')
    else:
        collapse = 'collapse="true"' if placement == 'collapsed' else ''
        body = f'<group id="g" threeD="true" rotationY="{angle}" {collapse}>' \
               '<expression property="x">30*time</expression>'+wall+'</group>'
    path = ROOT/f'{placement}-{angle}.xml'
    path.write_text('<scene version="1.1"><project width="160" height="120" fps="10" duration="2" linearLight="false"/>'
                    '<composition><camera id="camera" fov="90" z="160"><expression property="z">160+30*time</expression></camera>'
                    +body+'<particleEmitter id="p" x="30" y="50" speed="100" direction="0" rate="0" lifetime="2" collide="true" bounce="1">'
                    '<burst time="0" count="1"/></particleEmitter></composition><physics gravityY="0"/></scene>')
    r = Renderer.open(str(path), strict=True)
    node = r.doc.ids['p']
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    dt = 1/60
    for step in range(1, 61):
        t = step*dt
        ctx = Ctx(t, t)
        p = particles_at(r.rc, node, ctx)
        if p['vx'][0] >= 0:
            continue
        x, y = float(p['x'][0]), float(p['y'][0])
        q = (x-80)/80
        offset = (55 if placement == 'leaf' else 0)+30*t-80
        camera_depth = 160+30*t
        u = (offset-camera_depth*q)/(s*q-c)
        depth = camera_depth+s*u
        v = 60+(y-60)*depth/80
        previous_depth = depth-30*dt
        previous_x = 80+80*(offset-30*dt+c*u)/previous_depth
        previous_y = 60-80*(60-v)/previous_depth
        body_vx, body_vy = (x-previous_x)/dt, (y-previous_y)/dt
        # Horizontal normal: elastic normal response and 0.8 tangential friction.
        expected = np.array([2*body_vx-100, .2*body_vy])
        observed = np.array([p['vx'][0], p['vy'][0]])
        np.testing.assert_allclose(observed[0], expected[0], atol=1e-7, rtol=0)
        np.testing.assert_allclose(observed[1], expected[1], atol=5e-6, rtol=0)
        em = get_emitter(r.rc, node, ctx)
        current = snapshots(em, t)[r.doc.ids['wall']]
        previous = snapshots(em, t-dt)[r.doc.ids['wall']]
        relative = previous.matrix @ np.linalg.inv(current.matrix)
        relative /= relative[2, 2]
        projective_term = float(np.hypot(relative[2, 0], relative[2, 1]))
        assert projective_term > 1e-6, 'This control must exercise a non-affine relative mapping.'
        return dict(scene=path.name, schema_valid=True, contact_time=t,
                    observed_velocity=observed.tolist(), expected_velocity=expected.tolist(),
                    max_abs_error=float(np.abs(observed-expected).max()),
                    relative_projective_term=projective_term)
    raise AssertionError('No contact with the projected wall.')


if __name__ == '__main__':
    ROOT.mkdir(exist_ok=True)
    rows = [run(placement, angle) for placement in ('leaf', 'group', 'collapsed') for angle in (-35, 25)]
    (ROOT/'results.json').write_text(json.dumps(rows, indent=2)+'\n')
    print(json.dumps(dict(passed=len(rows), max_abs_error=max(row['max_abs_error'] for row in rows))))
