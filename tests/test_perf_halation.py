"""The halation blur only computes the channels it keeps; the result equals the all-channel formulation."""
from __future__ import annotations

import math

import numpy as np
import pytest

from test_effects import make_renderer, tile, effect, apply, CTX
from scenerender.effects import Params, gaussian, linear_pixels, luma, result, straight, working_pixels
from scenerender.effects.light import _linear_color


def _reference_halation(rc, e, buf, ctx):
    """The halation branch of light._bloom before channel pruning: every channel blurred at every radius."""
    p = Params(rc, e, ctx)
    r = max(0, p.d("radius", 4))
    b = buf.pad(math.ceil(4*r))
    src = linear_pixels(rc, b.px)
    rgb, a = straight(src)
    threshold = max(0, p.n("threshold", .7))
    strength = np.maximum(luma(rgb)-threshold, 0)/np.maximum(luma(rgb), 1e-7)
    bright = src*strength[..., None]
    halo = gaussian(bright, r)*max(0, p.n("intensity", 1))
    tint = _linear_color(p, (1,.2,.04,1))
    halo[..., :3] *= tint[:3]
    energy = bright[..., :3]
    red = gaussian(energy, max(.01, r))
    green = gaussian(energy, max(.01, r*.45))
    halo[..., 0] = red[..., 0]*tint[0]*max(0, p.n("intensity", 1))
    halo[..., 1] = green[..., 1]*_linear_color(p, (1,.2,.04,1))[1]*max(0, p.n("intensity", 1))
    halo[..., 2] *= .25
    halo[..., :3] *= (1-strength)[..., None]
    halo *= tint[3]
    px = src+halo
    px[..., 3] = np.clip(src[..., 3]+halo[..., 3]*(1-src[..., 3]), 0, 1)
    return result(b, working_pixels(rc, px))


@pytest.mark.parametrize("radius", [0, 0.004, 3, 7.5])
@pytest.mark.parametrize("linear", [True, False])
def test_halation_matches_all_channel_formulation(make_renderer, tile, radius, linear):
    r = make_renderer(effect("halation", radius=radius, threshold=.2, intensity=1.3), linear=linear)
    buf = tile
    out = apply(r, buf)
    ref = _reference_halation(r.rc, r.rc.doc.ids["fx"], buf, CTX)
    assert (out.x0, out.y0, out.w, out.h) == (ref.x0, ref.y0, ref.w, ref.h)
    np.testing.assert_allclose(out.px, ref.px, atol=1e-6)
