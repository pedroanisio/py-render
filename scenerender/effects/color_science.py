"""Colour science used by effects, with linear Rec.709 as the core connection space.

ACES: OCIO 2.5 ACES 2.0 SDR 100 nit Rec.709 builtin display transform.
AgX: Blender 4.0.0 base sRGB, exact OCIO matrix/log2/57^3 formation LUT
(inset, sigmoid, outset, hue compensation), decoded back to linear light.
PQ: BT.2390-4 section 5.4.1 EETF, mastering black/white and display black/white
in nits; input is ST2084 code values, output relative linear SDR light.
White balance: CIE D locus >=4000K; Planckian locus below, Bradford CAT to D65.
Tint is delta CIE 1960 v in units of 0.001. Temperature 0 means D65 identity.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np


@lru_cache(maxsize=1)
def ocio_config():
    import PyOpenColorIO as ocio
    return ocio.Config.CreateFromBuiltinConfig('studio-config-v4.0.0_aces-v2.0_ocio-v2.5')


def apply_cpu(cpu, rgb):
    out = np.array(rgb, dtype=np.float32, order='C', copy=True)
    cpu.applyRGB(out)
    return out


@lru_cache(maxsize=1)
def aces_processor():
    import PyOpenColorIO as ocio
    t = ocio.DisplayViewTransform(src='Linear Rec.709 (sRGB)', display='sRGB - Display',
                                  view='ACES 2.0 - SDR 100 nits (Rec.709)')
    return ocio_config().getProcessor(t).getDefaultCPUProcessor()


@lru_cache(maxsize=1)
def agx_processor():
    import PyOpenColorIO as ocio
    from .agx_data import cube_text
    table = np.loadtxt(cube_text().splitlines()[9:], dtype=np.float32).reshape(57,57,57,3)
    lut = ocio.Lut3DTransform(gridSize=57, interpolation=ocio.INTERP_TETRAHEDRAL)
    # OCIO in-memory tables have blue fastest; cube files have red fastest.
    lut.setData(np.ascontiguousarray(table.transpose(2,1,0,3)))
    rec709_xyz = np.linalg.inv(np.array([[3.2410032329763587,-1.5373989694887855,-.4986158819963629],
                                       [-.9692242522025164,1.8759299836951759,.0415542263400847],
                                       [.0556394198519755,-.2040112061239099,1.0571489771875333]]))
    egamut_xyz = np.array([[.7053968501,.1640413283,.08101774865],
                           [.2801307241,.8202066415,-.1003373656],
                           [-.1037815116,-.07290725703,1.265746519]])
    matrix = np.eye(4)
    matrix[:3,:3] = np.linalg.solve(egamut_xyz, rec709_xyz)
    t = ocio.GroupTransform([ocio.MatrixTransform(matrix=matrix.ravel()),
                            ocio.AllocationTransform(allocation=ocio.ALLOCATION_LG2,
                                                     vars=[-12.47393,12.5260688117]), lut])
    return ocio.Config.CreateRaw().getProcessor(t).getDefaultCPUProcessor()


def white_xyz(kelvin, tint=0):
    t = np.clip(kelvin or 6504, 1667, 25000)
    if t >= 4000:
        x = (-4.607e9/t**3+2.9678e6/t**2+99.11/t+.244063) if t <= 7000 else (-2.0064e9/t**3+1.9018e6/t**2+247.48/t+.237040)
        y = -3*x*x+2.87*x-.275
        # Anchor the standard daylight reference exactly to the core's D65.
        if abs(t-6504) < 1:
            x, y = .3127, .3290
    else:
        x = -.2661239e9/t**3-.2343589e6/t**2+.8776956e3/t+.179910
        if t <= 2222:
            y = -1.1063814*x**3-1.3481102*x*x+2.18555832*x-.20219683
        else:
            y = -.9549476*x**3-1.37418593*x*x+2.09137015*x-.16748867
    den = -2*x+12*y+3
    u, v = 4*x/den, 6*y/den+tint*.001
    den = 2*u-8*v+4
    x, y = 3*u/den, 2*v/den
    return np.array([x/y, 1., (1-x-y)/y])


def bradford(kelvin, tint=0):
    from ..color import _BRADFORD, _rgb_to_xyz
    m = _rgb_to_xyz('srgb')
    cat = np.linalg.inv(_BRADFORD) @ np.diag((_BRADFORD @ white_xyz(6504))/(_BRADFORD @ white_xyz(kelvin, tint))) @ _BRADFORD
    return np.linalg.solve(m, cat @ m)


def temperature_rgb(kelvin):
    from ..color import _rgb_to_xyz
    rgb = np.linalg.solve(_rgb_to_xyz('srgb'), white_xyz(kelvin))
    return np.maximum(rgb, 0)/max(float(np.max(rgb)), 1e-7)


def pq_encode(nits):
    v = (np.maximum(nits, 0)/10000)**(2610/16384)
    return ((3424/4096+2413/128*v)/(1+2392/128*v))**(2523/32)


def pq_decode(code):
    v = np.clip(code,0,1)**(32/2523)
    return 10000*(np.maximum(v-3424/4096,0)/np.maximum(2413/128-2392/128*v,1e-9))**(16384/2610)


def bt2390(code, source_peak=1000, target_peak=100, source_black=0, target_black=0):
    lo, hi = pq_encode(source_black), pq_encode(source_peak)
    span = max(hi-lo, 1e-9)
    minimum = (pq_encode(target_black)-lo)/span
    maximum = (pq_encode(target_peak)-lo)/span
    knee = 1.5*maximum-.5
    e1 = np.clip((code-lo)/span, 0, 1)
    t = np.clip((e1-knee)/max(1-knee, 1e-9), 0, 1)
    spline = (2*t**3-3*t*t+1)*knee+(t**3-2*t*t+t)*(1-knee)+(-2*t**3+3*t*t)*maximum
    e2 = np.where(e1 < knee, e1, spline)
    e3 = e2+minimum*(1-e2)**4
    return pq_decode(e3*span+lo)/max(target_peak,1e-7)
