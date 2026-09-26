"""Flat/paint fills and deterministic fractal noise generators."""
from __future__ import annotations

import numpy as np

from . import Params, grid, premul, result, value_noise
from ..raster import Canvas
from ..registry import EFFECTS, FULL, PARTIAL


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


@EFFECTS.register("fractal-noise", level=PARTIAL, note="seeded fractal sum of smooth lattice noise; samples is octave count, capped at 12")
def fractal_noise(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    x, y = grid(buf)
    size = max(.01, p.d("size", 1))
    frequency, time = p.n("frequency", 1)/size, ctx.t*p.n("speed", 1)
    # A fixed subpixel phase avoids flat noise at integer lattice coordinates.
    x = (x+buf.x0+.37)*frequency
    y = (y+buf.y0+.73)*frequency
    seed = rc.ev.seed_for(e, str(p.n("seed", 0)))
    octaves = max(1, min(12, round(p.n("samples", 16))))
    out, total = np.zeros_like(x), 0.0
    for i in range(octaves):
        weight = .5**i
        out += value_noise(x*2**i+time, y*2**i+time*.63, seed+i)*weight
        total += weight
    out = np.clip((out/total-.5)*p.n("contrast", 1)+.5+p.n("brightness", 0), 0, 1)
    c = p.color()
    return result(buf, premul(out[..., None]*c[:3], np.full(out.shape+(1,), c[3], np.float32)))


EFFECTS.declare("shader", "none", "GLSL is not executed by the Python renderer")
