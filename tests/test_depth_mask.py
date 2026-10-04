"""The depth mask lives on the framebuffer: moderngl's Context has no `depth_mask` (assigning one only
sets a Python attribute), so blended draws must switch depth writes off through `ctx.fbo.depth_mask`."""
import moderngl
import numpy as np
import pytest

from scenerender import gl
from test_3d_render import doc, frame

pytestmark = pytest.mark.skipif(not gl.available(), reason='no GL')

BLEND = '<material id="w" baseColor="#FF000080" alphaMode="blend" unlit="true"/>'
# Scene space (CONVENTIONS 2.7): the 96x64 frame centre is (48, 32), +z points away from the viewer.
# Instance 0 (nearer the camera) is submitted before instance 1, so a depth write by the first
# would hide the second.
BODY = ('<camera id="c" x="48" y="32" z="-500"/>'
        '<object3D id="o" x="48" y="32" primitive="plane" width="40" height="40" material="w" instances="2">'
        '<expression property="z">10*index</expression></object3D>')
AMBIENT = '<light id="l" type="ambient"/>'
BOTH = 1 - (1 - 128 / 255) ** 2           # two source-over layers of alpha 128/255
FRONT_ONLY = 128 / 255


def centre(tmp_path, name):
    return frame(doc(tmp_path, BODY, materials=BLEND, lights=AMBIENT, w=96, h=64, name=name))[32, 48]


def test_blended_draws_do_not_write_depth(tmp_path):
    np.testing.assert_allclose(centre(tmp_path, 'masked.xml'), [BOTH, 0, 0, BOTH], atol=2e-6)


def test_the_check_detects_an_ineffective_mask(tmp_path, monkeypatch):
    """Control: with the framebuffer's mask ignored (what assigning ctx.depth_mask amounted to), the
    rear layer fails the depth test and only one layer remains."""
    monkeypatch.setattr(moderngl.Framebuffer, 'depth_mask', property(lambda self: True, lambda self, value: None))
    np.testing.assert_allclose(centre(tmp_path, 'unmasked.xml'), [FRONT_ONLY, 0, 0, FRONT_ONLY], atol=2e-6)
