"""Shared SQLite tracker base — thread-safe init, WAL mode, schema setup."""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from pathlib import Path

_BUSY_TIMEOUT_SECONDS = 10  # connect-level + PRAGMA busy_timeout


class _SqliteTracker:
    """Base class for thread-safe SQLite-backed trackers.

    Subclasses define `_SCHEMA` as a SQL string with one or more
    CREATE TABLE statements; the base handles connection setup, parent
    directory creation, WAL mode for on-disk databases, lock allocation,
    and connection teardown.

    The connect-level ``timeout`` and the ``PRAGMA busy_timeout`` are
    both set to 10s so that two hive processes writing to the same
    on-disk DB (one per MCP client session) wait for each other
    instead of failing fast with ``OperationalError: database is locked``.
    WAL mode keeps readers non-blocking while writes serialize.
    """

    _SCHEMA: str = ""

    def __init__(self, db_path: str = ":memory:") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            timeout=_BUSY_TIMEOUT_SECONDS,
        )
        if db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                f"PRAGMA busy_timeout={_BUSY_TIMEOUT_SECONDS * 1000}",
            )
        self._conn.executescript(self._SCHEMA)

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.close()
