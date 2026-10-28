"""sqlite3.connect options after the database path are keyword arguments."""

from __future__ import annotations

import sqlite3
import warnings

from vitaToolbox.sql.TimedConnection import TimedConnection


def test_timed_connection_does_not_forward_positional_connect_options():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        connection = sqlite3.connect(
            ":memory:",
            timeout=1,
            factory=TimedConnection,
            check_same_thread=False,
        )
        try:
            assert connection.execute("select 1").fetchone() == (1,)
        finally:
            connection.close()
    positional = [
        item for item in caught
        if issubclass(item.category, DeprecationWarning)
        and "positional" in str(item.message).lower()
    ]
    assert positional == []
