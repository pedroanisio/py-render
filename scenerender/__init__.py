__version__ = "0.1.0"


def _tune_allocator() -> None:
    """Keep freed frame-sized buffers in the process heap (glibc only).

    A 1080p RGBA float32 tile is ~33 MB, above glibc's largest mmap threshold, so by
    default every temporary array is a fresh mmap whose pages the kernel faults in and
    zeroes, then unmaps on release. Serving them from the heap instead removes that cost,
    which dominates many whole-frame NumPy operations. SCENERENDER_MALLOC_TUNING=0 opts out.
    """
    import os
    import sys
    if not sys.platform.startswith("linux") or os.environ.get("SCENERENDER_MALLOC_TUNING", "1") == "0":
        return
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        M_TRIM_THRESHOLD, M_TOP_PAD, M_MMAP_THRESHOLD = -1, -2, -3
        libc.mallopt(M_MMAP_THRESHOLD, 1 << 30)
        libc.mallopt(M_TRIM_THRESHOLD, 1 << 30)
        libc.mallopt(M_TOP_PAD, 64 << 20)
    except (OSError, AttributeError):
        pass


_tune_allocator()


def threads() -> int:
    """Threads one frame may use for native (GIL-releasing) work; SCENERENDER_THREADS overrides.

    Frame worker processes set it to their share of the CPUs so parallel renders do not oversubscribe.
    """
    import os
    try:
        n = int(os.environ.get("SCENERENDER_THREADS", "0"))
    except ValueError:
        n = 0
    return max(1, n or os.cpu_count() or 1)
