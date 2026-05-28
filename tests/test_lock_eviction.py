"""HIVE-116 PR-2 / AC-8: LockEvictionTracker unit tests."""

from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING

import pytest

from hive._lock_eviction import LockEvictionTracker

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tracker(tmp_path: Path) -> LockEvictionTracker:
    """Fresh tracker on a tmp DB; closed by Python GC at test end."""
    return LockEvictionTracker(
        db_path=str(tmp_path / "lock_evictions.db"),
    )


class TestLockEvictionTracker:
    def test_empty_tracker_returns_zero(
        self, tracker: LockEvictionTracker,
    ) -> None:
        assert tracker.count_last_30d() == 0
        assert tracker.last_iso() is None

    def test_record_then_count(
        self, tracker: LockEvictionTracker, tmp_path: Path,
    ) -> None:
        tracker.record(tmp_path, killed_pids=[111, 222])
        assert tracker.count_last_30d() == 1
        last = tracker.last_iso()
        assert last is not None
        # ISO-8601 with UTC offset
        assert last.endswith("+00:00") or last.endswith("Z")

    def test_multiple_records_ordered(
        self, tracker: LockEvictionTracker, tmp_path: Path,
    ) -> None:
        for i in range(5):
            tracker.record(tmp_path, killed_pids=[1000 + i])
        assert tracker.count_last_30d() == 5

    def test_old_events_excluded_from_30d_count(
        self, tracker: LockEvictionTracker, tmp_path: Path,
    ) -> None:
        """Manually insert a row dated 40 days ago; not in 30d window."""
        old_ts = (
            _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=40)
        ).isoformat(timespec="microseconds")
        tracker._conn.execute(
            "INSERT INTO lock_evictions "
            "(iso_ts, vault_path, killed_pids_json) VALUES (?, ?, ?)",
            (old_ts, str(tmp_path), "[]"),
        )
        tracker._conn.commit()
        # Then a fresh one
        tracker.record(tmp_path, killed_pids=[99])
        # Only the fresh one counts toward 30d
        assert tracker.count_last_30d() == 1

    def test_in_memory_tracker_works(self) -> None:
        """:memory: DB works for unit tests; no checkpoint thread."""
        t = LockEvictionTracker(db_path=":memory:")
        assert t.count_last_30d() == 0
        from pathlib import Path

        t.record(Path("/tmp/vault"), [42])
        assert t.count_last_30d() == 1
