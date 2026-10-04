"""Layout owners and scheduled children evaluate their own clocks."""
import numpy as np
import pytest

from scenerender.evaluator import Ctx
from scenerender.render import Renderer


def scene(tmp_path, content, *, name='scene', instanced=False):
    path = tmp_path/(name+'.xml')
    symbols = ''
    if instanced:
        symbols = '<symbols><symbol id="s" width="160" height="120" duration="6">'+content+'</symbol></symbols>'
        content = '<instance id="i" symbol="s" speed="2"/>'
    path.write_text('<scene version="1.1"><project width="160" height="120" duration="6" fps="10" '
                    'linearLight="false"/>'+symbols+'<composition>'+content+'</composition></scene>')
    return Renderer.open(str(path), strict=True)


@pytest.mark.parametrize('mode', ['row', 'column', 'stack', 'grid'])
@pytest.mark.parametrize('instanced', [False, True])
def test_layout_properties_use_owner_clock_and_child_sizes_use_warped_clock(tmp_path, mode, instanced):
    body = f'<group id="g" width="120" height="100" timeScale="2" layout="{mode}" gridColumns="2">' \
           '<expression property="padding">2+4*time</expression><expression property="gap">4+8*time</expression>' \
           '<shape id="a" shape="rect" width="10" height="10" fill="#ff0000">' \
           '<expression property="width">10+10*time</expression><expression property="height">10+10*time</expression></shape>' \
           '<shape id="b" shape="rect" width="10" height="10" fill="#00ff00"/></group>'
    r = scene(tmp_path, body, instanced=instanced)
    for t in (.3, .15, .4, .3):
        owner = t*(2 if instanced else 1)
        pad, gap, dim = 2+4*owner, 4+8*owner, 10+20*owner
        bx, by = pad, pad
        if mode == 'row':
            bx += dim+gap
        elif mode == 'column':
            by += dim+gap
        elif mode == 'grid':
            bx += (120-2*pad-gap)/2+gap
        control_body = f'<shape id="a" shape="rect" x="{pad}" y="{pad}" width="{dim}" height="{dim}" fill="#ff0000"/>' \
                       f'<shape id="b" shape="rect" x="{bx}" y="{by}" width="10" height="10" fill="#00ff00"/>'
        control = scene(tmp_path, control_body, name='control', instanced=instanced)
        np.testing.assert_allclose(r.frame_linear(t), control.frame_linear(t), atol=1/255)
        node, ctx = r.rc.ev.reference('i/b' if instanced else 'b', None, Ctx(t, t))
        np.testing.assert_allclose(r.rc.world_matrix(node, ctx)[:2, 2], [bx, by], atol=1e-9)


@pytest.mark.parametrize('mode', ['row', 'column'])
def test_sequence_layout_uses_each_childs_scheduled_clock(tmp_path, mode):
    body = f'<sequence id="g" width="120" height="120" timeScale="2" layout="{mode}" justify="end">' \
           '<expression property="gap">10+10*time</expression>' \
           '<shape id="a" shape="rect" width="10" height="10" end=".5">' \
           '<expression property="width">10+20*time</expression><expression property="height">10+20*time</expression></shape>' \
           '<shape id="b" shape="rect" width="10" height="10" end=".5" fill="#00ff00">' \
           '<expression property="width">10+20*time</expression><expression property="height">10+20*time</expression></shape></sequence>'
    r = scene(tmp_path, body)
    for t in (.3, .4, .3):
        dim = 10+20*(2*t-.5)
        x, y = (120-dim, 0) if mode == 'row' else (0, 120-dim)
        control = scene(tmp_path, f'<shape id="b" shape="rect" x="{x}" y="{y}" width="{dim}" height="{dim}" fill="#00ff00"/>', name='control')
        np.testing.assert_allclose(r.frame_linear(t), control.frame_linear(t), atol=1/255)
        node, ctx = r.rc.ev.reference('b', None, Ctx(t, t))
        np.testing.assert_allclose(r.rc.world_matrix(node, ctx)[:2, 2], [x, y], atol=1e-9)


def test_layout_mode_switch_uses_group_clock(tmp_path):
    body = '<group id="g" width="120" height="100" timeScale="2">' \
           '<expression property="layout">time &lt; .5 ? "row" : "column"</expression>' \
           '<shape id="a" shape="rect" width="10" height="10"/>' \
           '<shape id="b" shape="rect" width="10" height="10"/></group>'
    r = scene(tmp_path, body)
    for t in (.3, .8, .3):
        x, y = (10, 0) if t < .5 else (0, 10)
        control = scene(tmp_path, '<shape id="a" shape="rect" width="10" height="10"/>'+
                        f'<shape id="b" shape="rect" x="{x}" y="{y}" width="10" height="10"/>', name='control')
        np.testing.assert_array_equal(r.frame_linear(t), control.frame_linear(t))
