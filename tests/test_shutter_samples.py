"""Shutter-sample reuse and worker processes must never change or stall a frame."""
import os
import sys

import numpy as np
import pytest

from scenerender.render import Renderer


def scene(tmp_path, body):
    p = tmp_path / "scene.xml"
    p.write_text('<scene version="1.1"><project width="64" height="32" fps="10" duration="6" '
                 f'linearLight="false"/><composition>{body}</composition></scene>')
    return Renderer.open(str(p), strict=True)


def test_nested_sequence_child_is_not_static_on_the_composition_clock(tmp_path):
    # b's keys are over by composition time .5, but b runs on the sequence's clock, which reads 0 at 2.
    # One child only: a junction between children is a transition, which is never static anyway.
    r = scene(tmp_path, '<group id="g"><sequence id="q">'
              '<shape id="b" shape="rect" width="10" height="10" fill="#0000ff" start="2" end="4">'
              '<animate property="x"><key time="0" value="0"/><key time="0.5" value="40"/></animate>'
              '</shape></sequence></group>')
    g = r.doc.ids["g"]
    assert not r.rc._subtree_static(g, 2.0, 2.1)


class _Result:
    def __init__(self, pool, fail):
        self.pool, self.fail = pool, fail

    def get(self):
        if self.pool.terminated:
            pytest.fail("waited on a result of a terminated pool")
        if self.fail:
            raise RuntimeError("worker died")


class _Pool:
    _processes = 3

    def __init__(self):
        self.terminated = False
        self.calls = 0

    def apply_async(self, fn, args):
        self.calls += 1
        return _Result(self, fail=self.calls == 1)

    def terminate(self):
        self.terminated = True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="checks /dev/shm")
def test_failed_worker_renders_all_pending_samples_serially(tmp_path, monkeypatch):
    r = scene(tmp_path, '<shape id="s" shape="rect" width="10" height="10" fill="#ff0000">'
              '<animate property="x"><key time="0" value="0"/><key time="1" value="50"/></animate></shape>')
    times = [0.1 * i for i in range(8)]
    monkeypatch.setattr(r, "_sample_pool", lambda times, frame: None)
    expected = r._accumulate(times, 0)
    pool = _Pool()
    r._pool = pool
    monkeypatch.setattr(r, "_sample_pool", lambda times, frame: pool)
    # The old path freed the buffer under the samples still being written here (a segfault) and then
    # waited forever on chunks the terminated pool never runs.
    got = r._accumulate(times, 0)
    assert pool.terminated and r._pool is None
    np.testing.assert_array_equal(got.px, expected.px)     # sums are Bufs (on the GPU where it composites)
    name = r._shm.name
    r.close_sample_pool()
    assert not os.path.exists(f"/dev/shm/{name}")

