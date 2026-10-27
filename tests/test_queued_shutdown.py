"""Shutdown must survive a detector client that never finished initializing."""

from __future__ import annotations

from backEnd.QueuedDataManagerCloud import QueuedDataManagerCloud


def test_terminate_tolerates_missing_http_client():
    obj = QueuedDataManagerCloud.__new__(QueuedDataManagerCloud)
    obj.terminate()
    assert getattr(obj, "_httpClient", None) is None


def test_terminate_tolerates_http_client_left_none():
    obj = QueuedDataManagerCloud.__new__(QueuedDataManagerCloud)
    obj._httpClient = None
    obj.terminate()
    assert obj._httpClient is None
