"""Flat/paint fills and deterministic fractal noise generators."""
from __future__ import annotations

import numpy as np

from . import Params, grid, premul, result, value_noise
from ..raster import Canvas
from ..registry import EFFECTS, FULL


@EFFECTS.register("fill", level=FULL, note="replace colour with color or paint, retaining input coverage")
def fill(rc, e, buf, ctx, node):
    from ..paint import set_source
    p = Params(rc, e, ctx)
    paint = p.s("paint")
    if paint:
        canvas = Canvas(buf.rect)
        canvas.cr.translate(buf.x0, buf.y0)
        canvas.cr.scale(rc.scale, rc.scale)
        if set_source(rc, canvas.cr, paint, buf.w/rc.scale, buf.h/rc.scale, ctx, node):
            canvas.cr.rectangle(0, 0, buf.w/rc.scale, buf.h/rc.scale)
            canvas.cr.fill()
        return result(buf, canvas.to_buf(rc.linear).px*buf.px[..., 3:4])
    c = p.color()
    return result(buf, premul(np.broadcast_to(c[:3], buf.px[..., :3].shape), buf.px[..., 3:4]*c[3]))


@EFFECTS.register("fractal-noise", level=FULL, note="evolving basic/turbulent/smooth/sharp/rocky/strings fBm, fractional octaves, scale/contrast/brightness")
def fractal_noise(rc, e, buf, ctx, node):
    """Params noiseType, octaves (samples fallback), scale (size fallback),
    evolution degrees and roughness .5; speed in evolution turns per second.
    Output is opaque noise tinted by color; contrast is slope about middle gray.
    """
    from .fields import fractal
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    size = max(.01, p.param("scale", p.n("size",1))*rc.scale)
    frequency = p.n("frequency",1)/size
    x, y = (x+buf.x0+.37)*frequency, (y+buf.y0+.73)*frequency
    seed = rc.ev.seed_for(e, str(p.n("seed",0)))
    out = fractal(x, y, seed, p.param("octaves",p.n("samples",16)),
                  p.param("evolution",0)/360+ctx.t*p.n("speed",1),
                  p.param("noiseType","basic"), np.clip(p.param("roughness",.5),0,1))
    out = np.clip((out-.5)*p.n("contrast",1)+.5+p.n("brightness",0),0,1)
    c = p.color()
    return result(buf,premul(out[...,None]*c[:3],np.full(out.shape+(1,),c[3],np.float32)))


EFFECTS.declare("shader", "none", "GLSL is not executed by the Python renderer")
