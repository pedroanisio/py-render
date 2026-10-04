"""Planckian light colours using the CIE 1931 standard observer.

The bundled, unmodified 1 nm colour-matching data covers 360–830 nm.
Its source, checksum and licence are in data/CIE-1931.md.
"""
from functools import lru_cache
from importlib.resources import files

import numpy as np


@lru_cache(maxsize=1)
def _observer():
    source = files("scenerender").joinpath("data/CIE_xyz_1931_2deg.csv")
    with source.open("r") as stream:
        table = np.loadtxt(stream, delimiter=",")
    wavelengths = table[:, 0]
    weights = table[:, 1:].copy()
    weights[[0, -1]] *= .5  # Trapezoidal integration at the table boundaries.
    return wavelengths, weights


@lru_cache(maxsize=512)
def _blackbody_xyz(kelvin):
    wavelengths, weights = _observer()
    # Planck's second radiation constant h*c/k, expressed in nm*K. The first
    # constant and any common radiance scale cancel when normalising Y.
    spectrum = (560 / wavelengths)**5 / np.expm1(1.4387768775039337e7 / wavelengths / kelvin)
    xyz = spectrum @ weights
    return tuple(xyz / xyz[1])


def blackbody_xyz(kelvin):
    """XYZ with Y=1 over the schema's complete 1000–40000 K light range."""
    return np.array(_blackbody_xyz(float(np.clip(kelvin, 1000, 40000))))


def blackbody_rgb(kelvin):
    """Nonnegative linear sRGB light tint, normalised to unit Rec.709 luminance."""
    matrix = np.array([[3.2404542, -1.5371385, -.4985314],
                       [-.9692660, 1.8760108, .0415560],
                       [.0556434, -.2040259, 1.0572252]])
    rgb = np.maximum(matrix @ blackbody_xyz(kelvin), 0.)
    return rgb / (np.array([.2126, .7152, .0722]) @ rgb)
