"""Decoded images with authored mip levels, shared by GPU and atlas uploads."""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, eq=False)
class TextureImage:
    """Level zero behaves as an array; explicit operations preserve every level."""
    levels: tuple

    def __post_init__(self):
        levels = tuple(np.ascontiguousarray(a, np.float32) for a in self.levels)
        if not levels or levels[0].ndim != 3 or min(levels[0].shape) < 1:
            raise ValueError('Texture must contain a nonempty image')
        h, w, c = levels[0].shape
        if len(levels) > max(h, w).bit_length():
            raise ValueError('Too many authored mip levels')
        for i, a in enumerate(levels):
            if a.shape != (max(1, h >> i), max(1, w >> i), c):
                raise ValueError('Authored mip dimensions must halve at each level')
        object.__setattr__(self, 'levels', levels)

    def __array__(self, dtype=None, copy=None):
        a = np.asarray(self.levels[0], dtype=dtype)
        return a.copy() if copy else a

    def __getitem__(self, key):
        return self.levels[0][key]

    @property
    def shape(self):
        return self.levels[0].shape

    @property
    def ndim(self):
        return self.levels[0].ndim

    @property
    def nbytes(self):
        return sum(a.nbytes for a in self.levels)


def map_image(image, operation):
    """Apply channel/color conversion to all authored levels, or to a plain array."""
    if isinstance(image, TextureImage):
        return TextureImage(tuple(operation(a) for a in image.levels))
    return operation(image)


def _reduce_axis(a, axis):
    old = a.shape[axis]
    if old == 1:
        return a
    count = old // 2
    a = np.moveaxis(a, axis, 0)
    if old % 2 == 0:
        return np.moveaxis(a.reshape(count, 2, *a.shape[1:]).mean(1), 0, axis)
    # Integrate pixel areas, including every sample of an odd-sized source.
    bounds = np.linspace(0., old, count+1)
    index = np.floor(bounds).astype(int)
    prefix = np.concatenate([np.zeros_like(a[:1], dtype=np.float64), np.cumsum(a, axis=0, dtype=np.float64)])
    partial = a[np.minimum(index, old-1)]*(bounds-index).reshape((-1,)+(1,)*(a.ndim-1))
    integral = prefix[index]+partial
    return np.moveaxis((np.diff(integral, axis=0)/(old/count)).astype(np.float32), 0, axis)


def mip_chain(image):
    """Keep authored levels verbatim and generate only the missing tail in linear space."""
    authored = image.levels if isinstance(image, TextureImage) else (np.asarray(image, np.float32),)
    yield from authored
    a = authored[-1]
    while a.shape[:2] != (1, 1):
        a = _reduce_axis(_reduce_axis(a, 1), 0)
        yield a
