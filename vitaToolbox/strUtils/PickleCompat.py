"""Pickle helpers for blobs that used to be Python 2 strings.

SQLite BLOBs, mmap-free file snapshots and XML-RPC binary values arrive as
``bytes`` (or a ``memoryview``). Wrapping them in ``str()`` produces
``\"b'...'\"`` and ``pickle.loads`` then fails. Protocol 0 pickles stored as
text are still accepted by encoding Latin-1, which round-trips every byte.
"""

from __future__ import annotations

import pickle
from typing import Any


def as_pickle_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, memoryview):
        return data.tobytes()
    if isinstance(data, str):
        return data.encode("latin-1")
    raw = getattr(data, "data", None)
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    return bytes(data)


def loads_pickle(data: Any) -> Any:
    return pickle.loads(as_pickle_bytes(data))
