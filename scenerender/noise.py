"""Seeded randomness (D24, CONVENTIONS 5.19): splitmix64 hashing and 1D gradient noise.

Every seeded function of the renderer draws from these, never from a library generator:
  * splitmix64(x): the standard finaliser (increment 0x9E3779B97F4A7C15, multipliers
    0xBF58476D1CE4E5B9 and 0x94D049BB133111EB), on unsigned 64-bit integers.
  * hash64(seed, channel, index) = splitmix64(seed ^ splitmix64(channel ^ splitmix64(index))), and
    uniform(seed, channel, index) its top 53 bits over 2^53, in [0, 1).
  * N(seed, channel, x): 1D Perlin gradient noise in [-1, 1], zero at integers. The gradient at
    lattice point i is 2 . uniform(seed, channel, i) - 1; with f = x - floor(x) and
    u = 6f^5 - 15f^4 + 10f^3, N = 2 (g_i f + (g_{i+1} (f - 1) - g_i f) u).
  * fractal(seed, channel, x, octaves): the sum over octaves k of 0.5^k N(seed, channel . 1024 + k,
    x . 2^k), over the sum of the weights.

Negative integers enter as their two's complement. Everything accepts NumPy arrays as well as Python
numbers (arrays: uint64 arithmetic, which wraps as the definition requires).

Beyond D24, for fields over the plane or space (turbulence, value-noise generators), lattice point
(i, j, k) is the index pack(i, j, k) = (i mod 2^21) | (j mod 2^21) << 21 | (k mod 2^21) << 42; and
sequences of draws (Rng) take index 0, 1, 2, ... of one channel.
"""
from __future__ import annotations

import math
import zlib

import numpy as np

_M64 = (1 << 64) - 1
_INC, _M1, _M2 = 0x9E3779B97F4A7C15, 0xBF58476D1CE4E5B9, 0x94D049BB133111EB
_U = np.uint64

# D25 wiggle channels: the property, numbered as the C renderer numbers the expression properties.
PROPERTY_CHANNELS = {p: i for i, p in enumerate(("x", "y", "rotation", "scaleX", "scaleY", "anchorX", "anchorY",
                                                 "opacity"))}


def channel_of(prop: str) -> int:
    """Noise channel of a property: its D25 number, else 8 + a CRC-32 of its name (properties D25
    does not list)."""
    c = PROPERTY_CHANNELS.get(prop)
    return c if c is not None else 8 + zlib.crc32(prop.encode())


def splitmix64(x):
    if isinstance(x, np.ndarray):
        with np.errstate(over="ignore"):
            z = x.astype(_U) + _U(_INC)
            z = (z ^ (z >> _U(30))) * _U(_M1)
            z = (z ^ (z >> _U(27))) * _U(_M2)
            return z ^ (z >> _U(31))
    z = (int(x) + _INC) & _M64
    z = ((z ^ (z >> 30)) * _M1) & _M64
    z = ((z ^ (z >> 27)) * _M2) & _M64
    return z ^ (z >> 31)


def _u64(v):
    if isinstance(v, np.ndarray):
        return v.astype(np.int64).astype(_U) if v.dtype.kind in "if" else v.astype(_U)
    return int(v) & _M64


def hash64(seed, channel, index):
    """splitmix64(seed ^ splitmix64(channel ^ splitmix64(index))); any argument may be an array."""
    arrays = any(isinstance(v, np.ndarray) for v in (seed, channel, index))
    if arrays:
        s, c, i = (np.asarray(_u64(v), _U) if isinstance(v, np.ndarray) else _U(int(v) & _M64)
                   for v in (seed, channel, index))
        return splitmix64(s ^ splitmix64(c ^ splitmix64(np.asarray(i, _U))))
    return splitmix64(_u64(seed) ^ splitmix64(_u64(channel) ^ splitmix64(_u64(index))))


def uniform(seed, channel, index):
    """The top 53 bits of hash64 over 2^53: uniform in [0, 1)."""
    h = hash64(seed, channel, index)
    if isinstance(h, np.ndarray):
        return (h >> _U(11)).astype(np.float64) * (1.0 / 9007199254740992.0)
    return (h >> 11) * (1.0 / 9007199254740992.0)


def gaussian(seed, channel, index):
    """A standard normal draw from one hash (Box-Muller): u1 = (high 32 bits + 1/2) / 2^32 and
    u2 = low 32 bits / 2^32 of hash64(seed, channel, index)."""
    h = hash64(seed, channel, index)
    if isinstance(h, np.ndarray):
        u1 = ((h >> _U(32)).astype(np.float64) + 0.5) * (1.0 / 4294967296.0)
        u2 = (h & _U(0xFFFFFFFF)).astype(np.float64) * (1.0 / 4294967296.0)
        return np.sqrt(-2.0 * np.log(u1)) * np.cos(2 * math.pi * u2)
    u1 = ((h >> 32) + 0.5) / 4294967296.0
    u2 = (h & 0xFFFFFFFF) / 4294967296.0
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2)


def noise1(seed, channel, x):
    """D24's N(seed, channel, x); x a number or an array."""
    if isinstance(x, np.ndarray) or isinstance(channel, np.ndarray):
        x = np.asarray(x, np.float64)
        fl = np.floor(x)
        f = x - fl
        i = fl.astype(np.int64)
        g0 = 2.0 * uniform(seed, channel, i) - 1.0
        g1 = 2.0 * uniform(seed, channel, i + 1) - 1.0
        a, b = g0 * f, g1 * (f - 1.0)
        u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0)
        return 2.0 * (a + (b - a) * u)
    x = float(x)
    if not math.isfinite(x):
        return math.nan
    fl = math.floor(x)
    f = x - fl
    i = int(fl)
    a = (2.0 * uniform(seed, channel, i) - 1.0) * f
    b = (2.0 * uniform(seed, channel, i + 1) - 1.0) * (f - 1.0)
    u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0)
    return 2.0 * (a + (b - a) * u)


def fractal(seed, channel, x, octaves: int = 1, gain: float = 0.5):
    """D24's fractal sum: octave k weighs gain^k, samples channel . 1024 + k at x . 2^k, and the sum
    is divided by the sum of the weights (gain 0.5 is D24's; D25's wiggle passes ampMult)."""
    total, weight, amp, freq = 0.0, 0.0, 1.0, 1.0
    for k in range(max(1, int(octaves))):
        total = total + amp * noise1(seed, channel * 1024 + k, x * freq)
        weight += amp
        amp *= gain
        freq *= 2.0
    return total / weight if weight != 0 else total * 0.0


def pack(i, j=0, k=0):
    """Lattice index of (i, j, k): 21 bits each (two's complement), as one unsigned 64-bit index."""
    m = (1 << 21) - 1
    if any(isinstance(v, np.ndarray) for v in (i, j, k)):
        i, j, k = (np.asarray(v).astype(np.int64).astype(_U) & _U(m) for v in (i, j, k))
        return i | (j << _U(21)) | (k << _U(42))
    return (int(i) & m) | (int(j) & m) << 21 | (int(k) & m) << 42


class Rng:
    """A sequence of draws: draw n is uniform(seed, channel, n). Offers the few numpy.random
    Generator methods the renderer uses (random, uniform, integers, normal, permutation)."""

    def __init__(self, seed: int, channel: int = 0):
        self.seed, self.channel, self.n = int(seed) & _M64, int(channel) & _M64, 0

    def _draw(self, size=None):
        if size is None:
            u = uniform(self.seed, self.channel, self.n)
            self.n += 1
            return u
        count = int(np.prod(size))
        u = uniform(self.seed, self.channel, np.arange(self.n, self.n + count, dtype=np.uint64))
        self.n += count
        return u.reshape(size)

    def random(self, size=None):
        return self._draw(size)

    def uniform(self, low=0.0, high=1.0, size=None):
        return low + (high - low) * self._draw(size)

    def integers(self, low, high=None, size=None):
        if high is None:
            low, high = 0, low
        u = self._draw(size)
        v = np.floor(low + (high - low) * np.asarray(u)).astype(np.int64)
        return int(v) if size is None else v

    def normal(self, loc=0.0, scale=1.0, size=None):
        """Draw n is gaussian(seed, channel, n)."""
        if size is None:
            z = gaussian(self.seed, self.channel, self.n)
            self.n += 1
            return loc + scale * z
        count = int(np.prod(size))
        z = gaussian(self.seed, self.channel, np.arange(self.n, self.n + count, dtype=np.uint64))
        self.n += count
        return loc + scale * z.reshape(size)

    def standard_normal(self, size=None):
        return self.normal(size=size)

    def permutation(self, n: int) -> np.ndarray:
        """Fisher-Yates over range(n), drawing j = floor(u . (i + 1)) for i = n - 1 ... 1."""
        p = np.arange(n)
        for i in range(n - 1, 0, -1):
            j = int(self._draw() * (i + 1))
            p[i], p[j] = p[j], p[i]
        return p

    def shuffle(self, seq: list) -> None:
        for i in range(len(seq) - 1, 0, -1):
            j = int(self._draw() * (i + 1))
            seq[i], seq[j] = seq[j], seq[i]
