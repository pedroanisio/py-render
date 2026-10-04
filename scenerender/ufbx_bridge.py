"""Native FBX binding lifetime management shared by mesh and tracking readers."""

class Keep:
    """Keep-alive proxy for ufbx 0.0.5 objects. The binding caches one wrapper per element without
    owning it: once a wrapper is freed, fetching the same element again returns a dangling object
    (segfault at the next GC). Everything fetched through this proxy stays referenced in `pool`
    for the duration of the load; arguments passed to methods are unwrapped."""
    __slots__ = ("_o", "_pool")

    def __init__(self, o, pool: list):
        self._o, self._pool = o, pool
        pool.append(o)

    def _wrap(self, v):
        return v if v is None or isinstance(v, (int, float, str, bytes, tuple)) else Keep(v, self._pool)

    def __getattr__(self, k: str):
        v = getattr(self._o, k)
        if callable(v):
            return lambda *a, **kw: self._wrap(v(*[x._o if isinstance(x, Keep) else x for x in a], **kw))
        return self._wrap(v)

    def __len__(self) -> int:
        return len(self._o)

    def __getitem__(self, i):
        return self._wrap(self._o[i])

    def __iter__(self):
        return (self._wrap(x) for x in self._o)

