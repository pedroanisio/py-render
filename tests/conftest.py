"""Conformance tests compare exact renders: the raster cache (which resamples moving static content,
see compositor._cached_draw) is off unless a test enables it (tests/test_raster_cache.py)."""
import os

os.environ.setdefault("SCENERENDER_RASTER_CACHE", "0")
