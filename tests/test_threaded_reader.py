"""Network reader thread keeps the socket drained and drops stale frames."""

import time

import pytest

from backEnd.native.open_capture import _maybe_threaded, _ThreadedFrames


def _gen(n, delay=0.0, fail=None):
    for i in range(n):
        if fail is not None and i == fail:
            raise ConnectionResetError(10054, "reset")
        if delay:
            time.sleep(delay)
        yield i


def test_slow_consumer_drops_oldest_but_keeps_reading():
    it = _ThreadedFrames(_gen(200), maxlen=5)
    time.sleep(0.3)  # consumer stalls; producer must not block
    got = list(it)
    assert it.dropped == 195 and got == list(range(195, 200))


def test_fast_consumer_gets_everything_in_order():
    assert list(_ThreadedFrames(_gen(50, delay=0.001), maxlen=5)) == list(range(50))


def test_error_propagates_after_queued_frames():
    it = _ThreadedFrames(_gen(10, fail=3), maxlen=50)
    assert [next(it), next(it), next(it)] == [0, 1, 2]
    with pytest.raises(ConnectionResetError):
        next(it)


def test_drop_is_logged(monkeypatch):
    lines = []

    class L:
        def info(self, m):
            lines.append(m)
    it = _ThreadedFrames(_gen(30), maxlen=2, log=L())
    time.sleep(0.2)
    it._last_log -= 61
    list(it)
    assert lines and "skipped 28 frames" in lines[0]


def test_only_network_sources_are_threaded():
    frames = iter([1])
    assert _maybe_threaded("C:/clips/a.mp4", frames, None) is frames
    t = _maybe_threaded("rtsp://cam/x", iter([1]), None)
    assert isinstance(t, _ThreadedFrames) and list(t) == [1]
