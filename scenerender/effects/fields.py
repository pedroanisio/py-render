"""Deterministic evolving fractals and CPU dense pyramidal block correspondence.

Fractals pin AE-like basic, turbulent, smooth, sharp, rocky and strings models.
Evolution is a continuous interpolation of independent lattice volumes, in turns;
no image translation is substituted for evolution. Fractional octaves are supported.
Flow minimizes local RGBA SSD, coarse to fine, with a smooth dense displacement
field. Its analysis resolution is bounded (384px); warps stay at output resolution.
"""
from __future__ import annotations

import numpy as np

from . import _box_axis, _lattice, gaussian, sample, value_noise


def fractal(x, y, seed, octaves=6, evolution=0, kind="basic", roughness=.5):
    out, total = np.zeros_like(x), 0.0
    octaves = float(np.clip(octaves, 1, 12))
    phase = np.floor(evolution)
    f = evolution-phase
    f = f*f*(3-2*f)
    for i in range(int(np.ceil(octaves))):
        weight = roughness**i*min(1, octaves-i)
        z = value_noise(x*2**i, y*2**i, seed+i*101+int(phase)*7919)
        z = z*(1-f)+value_noise(x*2**i, y*2**i, seed+i*101+(int(phase)+1)*7919)*f
        if kind in ("turbulent", "turbulent-smooth", "turbulent-sharp", "rocky", "strings"):
            z = abs(2*z-1)
        if kind in ("sharp", "turbulent-sharp"):
            z = z*z
        elif kind in ("smooth", "turbulent-smooth"):
            z = z*z*(3-2*z)
        elif kind == "rocky":
            z = np.clip(z*3-.5, 0, 1)
        elif kind == "strings":
            z = (1-z)**6
        out += z*weight
        total += weight
    return out/max(total, 1e-7)


_TURBULENT = ("turbulent", "turbulent-smooth", "turbulent-sharp", "rocky", "strings")
_SHAPING = {"sharp": 1, "turbulent-sharp": 1, "smooth": 2, "turbulent-smooth": 2, "rocky": 3, "strings": 4}


def fractal_grid(xs, ys, seed, octaves=6, evolution=0, kind="basic", roughness=.5, memo=None, device=False):
    """fractal() over the grid x = xs[column], y = ys[row] (float32).

    value_noise reads its lattice only at integer points: each octave and phase evaluates the
    lattice once for the columns and rows some pixel uses (with the same float32 steps), and a
    kernel interpolates and accumulates per pixel. With a memo dict, lattice rows are kept across
    calls: a field drifting along y or evolving by phase recomputes only the rows it newly needs."""
    from .. import kernels
    xs, ys = np.asarray(xs, np.float32), np.asarray(ys, np.float32)
    if device:     # accumulated on the GPU: the result is a device array (lattice tables stay CPU-exact)
        from ..gpu import FractalField
        field = FractalField(len(ys), len(xs))
    out, total = np.zeros((len(ys), len(xs)), np.float32) if not device else None, 0.0
    octaves = float(np.clip(octaves, 1, 12))
    phase = np.floor(evolution)
    f = evolution-phase
    f = f*f*(3-2*f)

    def axis(v):
        iv = np.floor(v)
        fv = v - iv
        points = np.union1d(iv, iv+1)
        return points, np.searchsorted(points, iv), np.searchsorted(points, iv+1), fv*fv*(3-2*fv)

    for i in range(int(np.ceil(octaves))):
        weight = roughness**i*min(1, octaves-i)
        px_, jx0, jx1, fxs = axis(xs*2**i)
        py_, jy0, jy1, fys = axis(ys*2**i)
        t0, t1 = (_lattice_rows(px_, py_, seed+i*101+(int(phase)+k)*7919, memo) for k in (0, 1))
        if device:
            field.octave(t0, t1, jx0, jx1, fxs, jy0, jy1, fys, f, weight, kind in _TURBULENT, _SHAPING.get(kind, 0))
        else:
            kernels.noise_octave(out, t0, t1, jx0, jx1, fxs, jy0, jy1, fys, f, weight,
                                 kind in _TURBULENT, _SHAPING.get(kind, 0))
        total += weight
    if memo is not None:
        used = memo.pop("_used", set())
        for key in [k for k in memo if k not in used]:
            del memo[key]
    if device:
        return field.normalized(total)
    return out/max(total, 1e-7)


def _lattice_rows(xs, ys, seed, memo):
    """_lattice at every (ys[j], xs[k]), reusing memoized rows (each row is the same elementwise
    computation whichever rows are evaluated with it)."""
    if memo is None:
        return _lattice(xs[None, :], ys[:, None], seed)
    key = (seed, xs.tobytes())
    known, table = memo.get(key, (np.empty(0, np.float32), np.empty((0, len(xs)), np.float32)))
    # ys is sorted and unique (lattice points): locate the rows already evaluated.
    at = np.minimum(np.searchsorted(known, ys), max(len(known) - 1, 0))
    have = (known[at] == ys) if len(known) else np.zeros(len(ys), bool)
    if not have.all():
        missing = ys[~have]
        known = np.concatenate([known, missing])
        table = np.concatenate([table, _lattice(xs[None, :], missing[:, None], seed)])
        order = np.argsort(known, kind="stable")
        known, table = known[order], table[order]
        at = np.searchsorted(known, ys)
    out = table[at]
    # Keep this call's rows only: the next frame needs about the same ones.
    memo[key] = (ys.copy(), out)
    memo.setdefault("_used", set()).add(key)
    return out


def resize(px, h, w):
    y, x = np.meshgrid(np.linspace(0, px.shape[0]-1, h, dtype=np.float32),
                       np.linspace(0, px.shape[1]-1, w, dtype=np.float32), indexing="ij")
    return sample(px, x, y, "clamp")


def optical_flow(a, b, max_size=384, iterations=3, window=9):
    """Dense A->B displacement (pixels), including alpha in matching costs."""
    h, w = a.shape[:2]
    ratio = min(1., max_size/max(h, w))
    hh, ww = max(1, round(h*ratio)), max(1, round(w*ratio))
    A, B = resize(a, hh, ww), resize(b, hh, ww)
    # Normalize colour jointly so HDR does not swamp coverage correspondences.
    norm = max(float(np.max(abs(A))), float(np.max(abs(B))), 1.)
    A, B = A/norm, B/norm
    # Exposure/background colour differences must not overpower motion evidence.
    # Remove each frame's DC colour; alpha retains its original coverage signal.
    A[...,:3] -= np.median(A[...,:3], axis=(0,1))
    B[...,:3] -= np.median(B[...,:3], axis=(0,1))
    pyramid = [(A, B)]
    while min(A.shape[:2]) > 20:
        A, B = gaussian(A, .8)[::2, ::2], gaussian(B, .8)[::2, ::2]
        pyramid.append((A, B))
    flow = None
    for A, B in reversed(pyramid):
        ph, pw = A.shape[:2]
        coarsest = flow is None
        if flow is None:
            flow = np.zeros((ph, pw, 2), np.float32)
        else:
            sy, sx = ph/flow.shape[0], pw/flow.shape[1]
            flow = resize(flow, ph, pw)*[sx, sy]
        y, x = np.indices((ph, pw), dtype=np.float32)
        for iteration in range(max(1, iterations)):
            best = np.full((ph, pw), np.inf, np.float32)
            update = np.zeros_like(flow)
            radius = 8 if coarsest and iteration == 0 else 1
            candidates = sorted(((dx,dy) for dx in range(-radius,radius+1)
                                  for dy in range(-radius,radius+1)),key=lambda d:d[0]**2+d[1]**2)
            for dx, dy in candidates:
                warped = sample(B, x+flow[..., 0]+dx, y+flow[..., 1]+dy)
                cost = np.mean((A-warped)**2, -1)
                support = min(window,5) if coarsest else window
                cost = _box_axis(_box_axis(cost, support, 0), support, 1)
                # Stable ties preserve the coarse-level displacement.
                mask = cost < best-1e-10
                best = np.where(mask, cost, best)
                update[..., 0] = np.where(mask, dx, update[..., 0])
                update[..., 1] = np.where(mask, dy, update[..., 1])
            flow += update
            # Edge-clamped regularization: constant translations remain constant.
            pad = np.pad(flow, ((2,2),(2,2),(0,0)), mode="edge")
            flow = gaussian(pad, .85)[2:-2, 2:-2]
    return resize(flow, h, w)*[w/ww, h/hh]


def flow_warp(px, flow, fraction):
    """Forward bilinear splat of the dense trajectories, normalizing collisions.

    Unlike inverse fixed-point lookup, this stays stable when independent
    motions make the flow noninvertible. Normalized-convolution hole filling
    reconstructs small disocclusions from neighbouring warped samples; genuine
    transparent source pixels remain transparent because they have coverage.
    """
    h, w = px.shape[:2]
    y, x = np.indices(px.shape[:2], dtype=np.float32)
    tx, ty = x+fraction*flow[...,0], y+fraction*flow[...,1]
    ix, iy = np.floor(tx).astype(np.int32), np.floor(ty).astype(np.int32)
    fx, fy = tx-ix, ty-iy
    acc = np.zeros((h*w,px.shape[-1]),np.float32)
    coverage = np.zeros(h*w,np.float32)
    for dx,dy,weight in ((0,0,(1-fx)*(1-fy)),(1,0,fx*(1-fy)),
                          (0,1,(1-fx)*fy),(1,1,fx*fy)):
        xx, yy = ix+dx, iy+dy
        mask = (xx>=0)&(xx<w)&(yy>=0)&(yy<h)&(weight>0)
        indices = (yy[mask]*w+xx[mask]).ravel()
        weights = weight[mask]
        coverage += np.bincount(indices,weights,minlength=h*w).astype(np.float32)
        for c in range(px.shape[-1]):
            acc[:,c] += np.bincount(indices,weights*px[...,c][mask],minlength=h*w).astype(np.float32)
    acc /= np.maximum(coverage[:,None],1e-7)
    acc = acc.reshape(px.shape)
    valid = (coverage.reshape(h,w) > 1e-7)[...,None]
    for sigma in (1.,2.,4.):
        if valid.all():
            break
        support = gaussian(valid.astype(np.float32),sigma)
        fill = gaussian(acc*valid,sigma)/np.maximum(support,1e-7)
        holes = ~valid & (support>1e-7)
        acc = np.where(holes,fill,acc)
        valid |= holes
    return acc
