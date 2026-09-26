"""Image-effect transitions: blur, glitch, pixelize, light-leak, morph, shader."""
from __future__ import annotations

import math

import numpy as np

from ..raster import color_to_working
from ..registry import FULL, NONE, PARTIAL, TRANSITIONS
from . import arrays, direction, gaussian, grid, mix, out, param, rng, sample, sstep


@TRANSITIONS.register("blur", level=FULL, note="blur rises to a peak at the cut (<param name='amount'>, sigma as a fraction of the frame, default 0.03)")
def blur(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    S = param(tr, "amount", 0.03) * max(rc.width, rc.height)
    sigma = S * (1 - abs(2 * p - 1))
    m = sstep(0.3, 0.7, p)
    A2 = gaussian(A, sigma, clamp=True) if m < 1 else A * 0
    B2 = gaussian(B, sigma, clamp=True) if m > 0 else B * 0
    return out(mix(A2, B2, m))


@TRANSITIONS.register("pixelize", level=FULL, note="mosaic grows to a peak at the cut (<param name='amount'>, block size as a fraction of the frame, default 0.05)")
def pixelize(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    m = sstep(0.4, 0.6, p)
    base = mix(A, B, m)
    bs = int(round(1 + (param(tr, "amount", 0.05) * max(rc.width, rc.height) - 1) * (1 - abs(2 * p - 1))))
    if bs <= 1:
        return out(base)
    return out(mosaic(base, bs))


def mosaic(px: np.ndarray, bs: int) -> np.ndarray:
    """Average over bs x bs blocks centred on the frame."""
    h, w = px.shape[:2]
    ox, oy = (-(w // 2)) % bs, (-(h // 2)) % bs          # align a block corner with the centre
    ph = (oy + h + bs - 1) // bs * bs
    pw = (ox + w + bs - 1) // bs * bs
    pad = np.zeros((ph, pw, 4), np.float32)
    cnt = np.zeros((ph, pw, 1), np.float32)
    pad[oy:oy + h, ox:ox + w] = px
    cnt[oy:oy + h, ox:ox + w] = 1
    s = pad.reshape(ph // bs, bs, pw // bs, bs, 4).sum(axis=(1, 3))
    n = cnt.reshape(ph // bs, bs, pw // bs, bs, 1).sum(axis=(1, 3))
    avg = s / np.maximum(n, 1)
    big = np.repeat(np.repeat(avg, bs, axis=0), bs, axis=1)
    return big[oy:oy + h, ox:ox + w]


@TRANSITIONS.register("glitch", level=FULL,
                      note="seeded per-frame slice displacement, RGB split and block swaps, peaking at the cut")
def glitch(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    k = math.sin(math.pi * p) ** 0.8
    base, other = (A, B) if p < 0.5 else (B, A)
    if k < 1e-3:
        return out(base.copy())
    g = rng(rc, tr, ctx, "glitch")
    h, w = base.shape[:2]
    res = base.copy()
    # Horizontal slices displaced sideways, some taken from the other picture.
    nsl = int(3 + 18 * k)
    for _ in range(nsl):
        y0 = int(g.integers(0, h))
        hh = int(max(1, g.uniform(0.005, 0.09) * h * (0.4 + k)))
        src = other if g.random() < 0.35 * k else base
        off = int(g.normal(0, 0.06 * w * k))
        res[y0:y0 + hh] = np.roll(src[y0:y0 + hh], off, axis=1)
    # Block swaps.
    for _ in range(int(8 * k)):
        bw, bh = int(g.uniform(0.03, 0.2) * w), int(g.uniform(0.01, 0.06) * h)
        x0, y0 = int(g.integers(0, max(1, w - bw))), int(g.integers(0, max(1, h - bh)))
        sx, sy = int(g.integers(0, max(1, w - bw))), int(g.integers(0, max(1, h - bh)))
        res[y0:y0 + bh, x0:x0 + bw] = (other if g.random() < 0.5 else base)[sy:sy + bh, sx:sx + bw]
    # RGB split.
    s = int(round(0.012 * w * k * (1 if g.random() < 0.5 else -1)))
    if s:
        res[..., 0] = np.roll(res[..., 0], s, axis=1)
        res[..., 2] = np.roll(res[..., 2], -s, axis=1)
        res[..., :3] = np.minimum(res[..., :3], np.maximum(res[..., 3:4], 0))
    return out(res)


_LEAK_SRGB = [(1.0, 0.56, 0.18), (1.0, 0.28, 0.12), (1.0, 0.86, 0.52)]


@TRANSITIONS.register("light-leak", level=FULL,
                      note="seeded warm light blobs sweep along @direction and screen over the picture, hiding the cut; an explicit @color tints them")
def light_leak(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    m = sstep(0.35, 0.65, p)
    base = mix(A, B, m)
    k = math.sin(math.pi * p) ** 1.2 * param(tr, "amount", 1.3)
    if k < 1e-3:
        return out(base)
    g = rng(rc, tr, ctx, "leak", temporal=False)
    d = direction(rc, tr, ctx)
    xs, ys = grid(rc)
    W, H = rc.width, rc.height
    D = max(W, H)
    if tr.get("color") is not None:
        c = rc.ev.color(tr, "color", ctx, (1, 0.6, 0.2, 1))
        cols = [tuple(c[:3])] * 3
    else:
        cols = _LEAK_SRGB
    L = np.zeros((H, W, 3), np.float32)
    for i, col in enumerate(cols):
        rgb = np.array(color_to_working(tuple(col) + (1.0,), rc.linear)[:3], np.float32)
        cx0, cy0 = g.uniform(0.15, 0.85) * W, g.uniform(0.1, 0.9) * H
        travel = g.uniform(0.5, 0.9) * D
        cx = cx0 + d[0] * travel * (p - 0.5)
        cy = cy0 + d[1] * travel * (p - 0.5) + g.uniform(-0.1, 0.1) * H * (p - 0.5)
        sig = g.uniform(0.18, 0.35) * D
        blob = np.exp(-((xs - cx) ** 2 + (ys - cy) ** 2) / (2 * sig * sig))
        L += blob[..., None] * rgb * g.uniform(0.6, 1.0)
    L = np.clip(L * k, 0, 1)
    res = base.copy()
    a_ = res[..., 3:4]
    res[..., :3] = res[..., :3] + L * (a_ - res[..., :3])     # screen, premultiplied
    return out(res)


@TRANSITIONS.register("morph", level=PARTIAL,
                      note="no feature correspondence: crossfade with a smooth displacement warp and a light blur peaking at the midpoint")
def morph(rc, tr, a, b, p, ctx):
    A, B = arrays(rc, a, b)
    xs, ys = grid(rc)
    W, H = rc.width, rc.height
    k = math.sin(math.pi * p)
    amp = 0.02 * max(W, H) * k
    g = rng(rc, tr, ctx, "morph", temporal=False)
    ph = g.uniform(0, 2 * math.pi, 4)
    dx = amp * (np.sin(2 * math.pi * ys / H * 1.3 + ph[0]) + 0.5 * np.sin(2 * math.pi * xs / W * 2.1 + ph[1]))
    dy = amp * (np.sin(2 * math.pi * xs / W * 1.1 + ph[2]) + 0.5 * np.sin(2 * math.pi * ys / H * 1.7 + ph[3]))
    sigma = 0.004 * max(W, H) * k
    A2 = gaussian(sample(A, xs + dx * p, ys + dy * p, clamp=True), sigma, clamp=True) if p < 1 else A * 0
    B2 = gaussian(sample(B, xs - dx * (1 - p), ys - dy * (1 - p), clamp=True), sigma, clamp=True) if p > 0 else B * 0
    return out(mix(A2, B2, sstep(0.0, 1.0, p)))


TRANSITIONS.declare("shader", NONE, "GLSL shaders are not executed; rendered as a crossfade")
