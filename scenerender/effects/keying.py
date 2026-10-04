"""Soft colour/luminance keys and matte operations, preserving premultiplication."""
from __future__ import annotations

import math

import numpy as np

from . import (Params, display_rgb, from_display, luma, morphology, premul, result,
               smoothstep, source_buf, straight)
from ..registry import EFFECTS, FULL


def _despill(rgb, key, amount):
    channel = int(np.argmax(key[:3]))
    others = [i for i in range(3) if i != channel]
    out = rgb.copy()
    neutral = np.maximum(rgb[..., others[0]], rgb[..., others[1]])
    out[..., channel] -= np.maximum(rgb[..., channel]-neutral, 0)*np.clip(amount, 0, 1)
    return out


@EFFECTS.register("chroma-key", level=FULL, note="soft RGB-distance key in encoded sRGB with dominant-channel spill suppression")
def chroma_key(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    key = rc.ev.color(e, "keyColor", ctx, (0,1,0,1))
    distance = np.linalg.norm(rgb-np.asarray(key[:3]), axis=-1)/math.sqrt(3)
    tol = p.n("tolerance", .2)
    matte = smoothstep(tol, tol+p.n("softness", .1), distance)[..., None]
    rgb = _despill(rgb, key, p.n("spill", .5)*(1-matte[..., 0]))
    return from_display(rc, buf, rgb, a*matte)


@EFFECTS.register("luma-key", level=FULL, note="soft luminance cutoff at threshold, preserving bright pixels")
def luma_key(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = straight(buf.px)
    t, s = p.n("threshold", .7), p.n("softness", .1)
    matte = smoothstep(t-s/2, t+s/2, luma(rgb))[..., None]
    return result(buf, premul(rgb, a*matte))


@EFFECTS.register("difference-key", level=FULL, note="frame-aligned difference plate in real parent transform; tolerance/softness and plate opacity")
def difference_key(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    src = source_buf(rc, e, ctx, node, rect=buf.rect)
    if src is None:
        return buf.copy()
    rgb, a = display_rgb(rc, buf.px)
    plate, pa = display_rgb(rc, src.region(buf.rect))
    distance = np.linalg.norm(rgb-plate, axis=-1)/math.sqrt(3)
    tol = p.n("tolerance", .2)
    matte = smoothstep(tol, tol+p.n("softness", .1), distance)[..., None]
    return from_display(rc, buf, rgb, a*(1-pa+pa*matte))


@EFFECTS.register("spill-suppress", level=FULL, note="reduce dominant keyColor channel towards the larger remaining channel")
def spill_suppress(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    rgb, a = display_rgb(rc, buf.px)
    key = rc.ev.color(e, "keyColor", ctx, (0,1,0,1))
    return from_display(rc, buf, _despill(rgb, key, p.n("spill", .5)*p.n("amount", 1)), a)


@EFFECTS.register("matte-choke", level=FULL, note="positive choke erodes, negative spreads; gray-level softness and iterative choke/spread")
def matte_choke(rc, e, buf, ctx, node):
    """Simple Choker at softness=0; positive amount erodes, negative spreads.

    softness controls preblur sigma (softness*abs(amount)) and the surviving
    gray-level transition width about 50% alpha; iterations repeats the stage.
    Zero choke is identity. Spread carries neighbouring straight colour.
    """
    p = Params(rc, e, ctx)
    amount = p.d("amount", 1)
    if amount == 0:
        return buf.copy()
    iterations = max(1,min(32,round(p.param("iterations",1))))
    b = buf.pad(math.ceil(-amount*iterations+1)) if amount < 0 else buf
    rgb, a = straight(b.px)
    from . import gaussian
    new_alpha = a[...,0].copy()
    softness = np.clip(p.n("softness",.1),0,1)
    for _ in range(iterations):
        new_alpha = morphology(new_alpha,abs(amount),amount < 0)
        # Gray-level softness rounds the choke's hard threshold without moving
        # the 50% contour. Zero is Simple Choker; one retains all gray levels.
        if softness:
            new_alpha = gaussian(new_alpha[...,None],softness*abs(amount))[...,0]
            new_alpha = np.clip((new_alpha-.5)/max(softness,1e-6)+.5,0,1)
    new_alpha = new_alpha[...,None]
    if amount < 0:
        # Carry colours into newly grown coverage instead of creating black fringes.
        from . import gaussian
        spread = gaussian(b.px, max(.3, abs(amount)*iterations))
        spread_rgb, _ = straight(spread)
        rgb = np.where(a > 1e-6, rgb, spread_rgb)
    return result(b, premul(rgb, new_alpha))
