"""Shared SQLite tracker base — thread-safe init, WAL mode, schema setup.

Includes periodic ``PRAGMA wal_checkpoint(PASSIVE)`` daemon thread (HIVE-115,
ADR-009): at N=3-5 baseline concurrent hive sessions, sibling readers always
hold WAL snapshots that block automatic checkpoint advancement. The daemon
thread runs PASSIVE checkpoints on a configurable interval as the actual
drain mechanism; ``close()`` runs a final TRUNCATE attempt.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from pathlib import Path

_log = logging.getLogger(__name__)

_BUSY_TIMEOUT_SECONDS = 10  # connect-level + PRAGMA busy_timeout
_CLOSE_THREAD_JOIN_TIMEOUT_S = 2.0  # ceiling for checkpoint thread join on close


class _SqliteTracker:
    """Base class for thread-safe SQLite-backed trackers.

    Subclasses define `_SCHEMA` as a SQL string with one or more
    CREATE TABLE statements; the base handles connection setup, parent
    directory creation, WAL mode for on-disk databases, lock allocation,
    a periodic checkpoint daemon thread, and connection teardown.

    The connect-level ``timeout`` and the ``PRAGMA busy_timeout`` are
    both set to 10s so that two hive processes writing to the same
    on-disk DB (one per MCP client session) wait for each other
    instead of failing fast with ``OperationalError: database is locked``.
    WAL mode keeps readers non-blocking while writes serialize.
    """

    _SCHEMA: str = ""

    def __init__(
        self,
        db_path: str = ":memory:",
        *,
        checkpoint_interval_s: float | None = None,
    ) -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db_path = db_path
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
            # synchronous=NORMAL is safe with WAL: fsync only on
            # checkpoint, not on every commit. Tracker writes are
            # informational (usage/relevance/budget) — a power-loss
            # losing the last 1-2 events is acceptable.
            self._conn.execute("PRAGMA synchronous=NORMAL")
            # Checkpoint every 200 pages (~800 KB) instead of the
            # default 1000 — bounds WAL growth when N hive processes
            # share the DB and none of them is idle long enough to
            # trigger a passive checkpoint.
            self._conn.execute("PRAGMA wal_autocheckpoint=200")
        self._conn.executescript(self._SCHEMA)

        # ── Checkpoint daemon thread (HIVE-115 / ADR-009) ──────────────
        # At N=3-5 baseline sessions, the autocheckpoint above is blocked
        # by sibling readers holding snapshots. PASSIVE periodic is the
        # actual drain. :memory: DBs have no WAL and skip this entirely.
        self._checkpoint_count = 0
        self._stop_checkpoint = threading.Event()
        self._checkpoint_thread: threading.Thread | None = None
        if db_path != ":memory:":
            self._checkpoint_interval_s = (
                checkpoint_interval_s
                if checkpoint_interval_s is not None
                else _default_checkpoint_interval_s()
            )
            self._checkpoint_thread = threading.Thread(
                target=self._checkpoint_loop,
                name=f"hive-checkpoint-{Path(db_path).stem}",
                daemon=True,
            )
            self._checkpoint_thread.start()
        else:
            self._checkpoint_interval_s = (
                checkpoint_interval_s if checkpoint_interval_s is not None else 0.0
            )

    def _checkpoint_loop(self) -> None:
        """Daemon loop running PRAGMA wal_checkpoint(PASSIVE) on interval.

        PASSIVE does not block readers and advances checkpoint as far as
        current frame state allows. Survives transient ``sqlite3.Error``
        (logged at WARNING) so a temporary contention does not kill the
        thread.
        """
        while not self._stop_checkpoint.wait(timeout=self._checkpoint_interval_s):
            try:
                with self._lock:
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                self._checkpoint_count += 1
            except sqlite3.Error as e:
                _log.warning(
                    "WAL checkpoint failed for %s: %s",
                    self._db_path,
                    e,
                )
                # Continue — next tick retries; if close was called the
                # Event will be set and the loop exits cleanly.

    def close(self) -> None:
        """Stop checkpoint thread, run final TRUNCATE checkpoint, close conn.

        TRUNCATE only succeeds when no other connections hold snapshots;
        under contention it degrades to PASSIVE behavior (non-blocking
        per SQLite docs). The thread join has a 2-second ceiling so a
        hung worker cannot block shutdown.
        """
        self._stop_checkpoint.set()
        if self._checkpoint_thread is not None and self._checkpoint_thread.is_alive():
            self._checkpoint_thread.join(timeout=_CLOSE_THREAD_JOIN_TIMEOUT_S)
        with self._lock:
            with contextlib.suppress(sqlite3.Error):
                # Drop busy_timeout to 500ms for the final TRUNCATE so a
                # sibling reader cannot keep close() blocked for the full
                # 10-second busy_timeout. With siblings: TRUNCATE waits
                # 500ms then returns (no truncation, WAL stays — siblings
                # keep it alive anyway). Without siblings: TRUNCATE drains
                # the WAL fully in well under 500ms.
                self._conn.execute("PRAGMA busy_timeout=500")
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()


def _default_checkpoint_interval_s() -> float:
    """Read default checkpoint interval from settings (lazy import to avoid cycles).

    Tests can monkeypatch ``hive.config.settings`` to override.
    """
    from hive.config import settings

    return float(settings.wal_checkpoint_interval_s)
