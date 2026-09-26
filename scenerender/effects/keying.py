"""Soft colour/luminance keys and matte operations, preserving premultiplication."""
from __future__ import annotations

import math

import numpy as np

from . import (Params, display_rgb, from_display, luma, morphology, premul, result,
               smoothstep, source_buf, straight)
from ..registry import EFFECTS, FULL, PARTIAL


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


@EFFECTS.register("difference-key", level=PARTIAL, note="soft RGB distance from root-rendered source plate; transparent plate leaves pixels intact")
def difference_key(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    src = source_buf(rc, e, ctx, node)
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


@EFFECTS.register("matte-choke", level=PARTIAL, note="circular alpha erosion; negative amount dilates with diffused interior colours")
def matte_choke(rc, e, buf, ctx, node):
    p = Params(rc, e, ctx)
    amount = p.d("amount", 1)
    b = buf.pad(math.ceil(-amount+1)) if amount < 0 else buf
    rgb, a = straight(b.px)
    new_alpha = morphology(a[..., 0], abs(amount), amount < 0)[..., None]
    if amount < 0:
        # Carry colours into newly grown coverage instead of creating black fringes.
        from . import gaussian
        spread = gaussian(b.px, max(.3, abs(amount)))
        spread_rgb, _ = straight(spread)
        rgb = np.where(a > 1e-6, rgb, spread_rgb)
    return result(b, premul(rgb, new_alpha))
