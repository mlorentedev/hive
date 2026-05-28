"""Cooperative-filelock eviction telemetry — HIVE-116 PR-2 / ADR-012.

Persists every ``evict_filelock`` event to ``~/.local/share/hive/lock_evictions.db``
so ``vault_health(include_runtime=True)`` can surface the 30-day count + the
most-recent ISO timestamp. This is informational telemetry, not state — a
crash that loses the last 1-2 events is acceptable per the
:class:`hive._sqlite_tracker._SqliteTracker` synchronous=NORMAL contract.

The tracker is the source of the Phase C decision input documented in
[[adr-005-transport-and-scale]] §C amendment: when eviction frequency rises
sustainably, the cooperation pattern is reaching its limits and the daemon
model becomes mandatory.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import TYPE_CHECKING

from hive._sqlite_tracker import _SqliteTracker

if TYPE_CHECKING:
    from pathlib import Path


class LockEvictionTracker(_SqliteTracker):
    """Append-only log of cooperative-filelock evictions.

    Schema is single-table to keep the WAL footprint minimal. Primary key is
    ``iso_ts`` because eviction events are per-microsecond-unique in practice
    (the supervisor cannot fire twice in the same instant — see
    ``_terminate_registry`` which blocks on ``asyncio.sleep(grace_s)``).
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS lock_evictions (
        iso_ts TEXT PRIMARY KEY,
        vault_path TEXT NOT NULL,
        killed_pids_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_lock_evictions_ts
        ON lock_evictions(iso_ts);
    """

    def record(self, vault_path: Path, killed_pids: list[int]) -> None:
        """Persist one eviction event. ISO timestamp is UTC with offset."""
        iso = _dt.datetime.now(_dt.UTC).isoformat(timespec="microseconds")
        pids = json.dumps(killed_pids)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO lock_evictions "
                "(iso_ts, vault_path, killed_pids_json) VALUES (?, ?, ?)",
                (iso, str(vault_path), pids),
            )
            self._conn.commit()

    def count_last_30d(self) -> int:
        """Number of eviction events in the trailing 30 days."""
        cutoff = (
            _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=30)
        ).isoformat(timespec="microseconds")
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM lock_evictions WHERE iso_ts >= ?",
                (cutoff,),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def last_iso(self) -> str | None:
        """ISO-8601 timestamp of the most recent eviction, or None."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT iso_ts FROM lock_evictions "
                "ORDER BY iso_ts DESC LIMIT 1",
            )
            row = cur.fetchone()
        return str(row[0]) if row else None
