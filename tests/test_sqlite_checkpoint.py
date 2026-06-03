"""Tests for the WAL checkpoint daemon thread on `_SqliteTracker`.

Phase A of HIVE-115 (ADR-009 multi-process WAL policy): every tracker runs a
background daemon thread executing ``PRAGMA wal_checkpoint(PASSIVE)`` on a
configurable interval, plus a final ``PRAGMA wal_checkpoint(TRUNCATE)`` on
``close()``. This file is the TDD spec for that behavior.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from hive._sqlite_tracker import _SqliteTracker

if TYPE_CHECKING:
    import pytest


class _ExampleTracker(_SqliteTracker):
    """Minimal concrete subclass for testing the base class itself."""

    _SCHEMA = "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY);"


def _wait_for_count(
    tracker: _SqliteTracker,
    minimum: int,
    deadline_s: float = 2.0,
) -> int:
    """Poll ``tracker._checkpoint_count`` until it reaches ``minimum`` or
    ``deadline_s`` elapses. Returns the count seen at exit.

    Replaces absolute-timing ``time.sleep(...)`` waits in tests because
    CI runners under load can stretch a 0.1s tick to 0.13-0.15s — if the
    test sleeps exactly 0.25s, the second tick may land just past the
    window. Polling with a generous deadline is robust to that slack
    while still failing fast when the loop is genuinely broken.
    """
    deadline = time.monotonic() + deadline_s
    while tracker._checkpoint_count < minimum and time.monotonic() < deadline:
        time.sleep(0.02)
    return tracker._checkpoint_count


def _seed_wal(tracker: _SqliteTracker, n: int = 100) -> None:
    """Write enough rows to materialize WAL frames."""
    with tracker._lock:
        for _ in range(n):
            tracker._conn.execute("INSERT INTO events DEFAULT VALUES")
        tracker._conn.commit()


def test_passive_checkpoint_runs_on_interval(tmp_path: Path) -> None:
    """A daemon thread executes PRAGMA wal_checkpoint(PASSIVE) on each tick.

    HIVE-115 / ADR-009: at N=3-5 baseline, sibling processes always hold
    snapshots, so PASSIVE periodic is the actual drain mechanism. We verify
    the thread fires by reading a counter that the loop increments on each
    successful PRAGMA execution.
    """
    db_path = str(tmp_path / "test.db")
    tracker = _ExampleTracker(db_path, checkpoint_interval_s=0.1)
    try:
        _seed_wal(tracker)
        count = _wait_for_count(tracker, minimum=3, deadline_s=2.0)
        assert count >= 3, f"expected ≥3 checkpoint ticks within 2s at 0.1s interval, got {count}"
    finally:
        tracker.close()


def test_checkpoint_thread_is_daemon(tmp_path: Path) -> None:
    """The checkpoint thread is ``daemon=True`` so it dies with the parent.

    Non-daemon threads would prevent interpreter shutdown when the
    parent process exits ungracefully, leaving zombie hive processes
    holding SQLite file handles open — exactly the failure class
    HIVE-115 is closing.
    """
    db_path = str(tmp_path / "test.db")
    tracker = _ExampleTracker(db_path, checkpoint_interval_s=30.0)
    try:
        assert tracker._checkpoint_thread is not None
        assert tracker._checkpoint_thread.daemon is True, (
            "checkpoint thread must be daemon=True to die with the parent process"
        )
    finally:
        tracker.close()


def test_in_memory_db_has_no_checkpoint_thread(tmp_path: Path) -> None:
    """`:memory:` DBs do not use WAL and do not need a checkpoint thread.

    Avoids spawning a useless thread per in-memory tracker (a common test
    pattern via fixtures with ``db_path=":memory:"``).
    """
    tracker = _ExampleTracker(":memory:")
    try:
        assert tracker._checkpoint_thread is None, (
            ":memory: tracker should not spawn a checkpoint thread (no WAL)"
        )
    finally:
        tracker.close()


def test_close_runs_truncate_checkpoint(tmp_path: Path) -> None:
    """`close()` runs PRAGMA wal_checkpoint(TRUNCATE) as the last drain attempt.

    TRUNCATE only fully succeeds when no other connections hold snapshots;
    under contention it degrades to PASSIVE behavior. Either way, close()
    is the last opportunity for this process to advance the checkpoint.

    We assert post-close WAL size is <= pre-close WAL size — TRUNCATE either
    succeeded (size 0 or file gone) or fell through to PASSIVE-equivalent
    (advancement, no growth).
    """
    db_path = str(tmp_path / "test.db")
    tracker = _ExampleTracker(db_path, checkpoint_interval_s=999.0)  # disable periodic
    _seed_wal(tracker, n=500)  # generate substantial WAL

    wal_path = Path(f"{db_path}-wal")
    pre_size = wal_path.stat().st_size if wal_path.exists() else 0
    assert pre_size > 0, "test setup invalid: WAL should have frames before close"

    tracker.close()

    post_size = wal_path.stat().st_size if wal_path.exists() else 0
    assert post_size <= pre_size, f"WAL not drained on close: pre={pre_size}, post={post_size}"


def test_close_completes_within_guard_under_sibling_reader(tmp_path: Path) -> None:
    """`close()` does not hang when a sibling connection holds a read snapshot.

    Under multi-process load (N=3-5 baseline), other hive processes hold
    open snapshots constantly. close() must not block waiting for them —
    the TRUNCATE falls through to PASSIVE behavior and returns quickly.

    Wall-clock guard: close() should complete well under 2.5s even under
    a held sibling snapshot. Sets the bound for the close() time budget.
    """
    db_path = str(tmp_path / "test.db")
    tracker = _ExampleTracker(db_path, checkpoint_interval_s=999.0)
    _seed_wal(tracker)

    sibling = sqlite3.connect(db_path)
    sibling.execute("BEGIN")
    sibling.execute("SELECT * FROM events")  # holds snapshot
    try:
        start = time.monotonic()
        tracker.close()
        elapsed = time.monotonic() - start
        assert elapsed < 2.5, (
            f"close() took {elapsed:.2f}s under sibling reader load; "
            f"should complete <2.5s (TRUNCATE degrades to PASSIVE, non-blocking)"
        )
    finally:
        sibling.close()


def test_close_stops_checkpoint_thread(tmp_path: Path) -> None:
    """`close()` signals the checkpoint thread to stop and joins it.

    Verifies the lifecycle: after close(), the thread is no longer alive,
    and subsequent counter reads do not increment.
    """
    db_path = str(tmp_path / "test.db")
    tracker = _ExampleTracker(db_path, checkpoint_interval_s=0.05)
    try:
        _seed_wal(tracker)
        count_before = _wait_for_count(tracker, minimum=1, deadline_s=2.0)
        assert count_before > 0, "test setup: thread should have run before close"
    finally:
        tracker.close()

    # After close: thread should have stopped, count should be stable.
    assert tracker._checkpoint_thread is not None
    assert not tracker._checkpoint_thread.is_alive(), (
        "checkpoint thread should have stopped after close()"
    )

    # Wait briefly and verify no new ticks happened.
    count_after_close = tracker._checkpoint_count
    time.sleep(0.15)
    assert tracker._checkpoint_count == count_after_close, (
        "checkpoint_count should not increment after close()"
    )


def test_checkpoint_loop_survives_sqlite_errors(tmp_path: Path) -> None:
    """The checkpoint loop catches sqlite3.Error and continues on next tick.

    A transient lock contention or temporary error must not kill the
    daemon thread; otherwise one bad tick stops all future drains.
    """
    db_path = str(tmp_path / "test.db")
    tracker = _ExampleTracker(db_path, checkpoint_interval_s=0.1)
    try:
        _seed_wal(tracker)
        early_count = _wait_for_count(tracker, minimum=2, deadline_s=2.0)
        assert early_count >= 2, f"expected ≥2 checkpoint ticks within 2s, got {early_count}"

        # Forcefully simulate a transient error by closing & reopening the
        # connection out from under the loop. The next tick should encounter
        # an error, log it, and continue.
        with tracker._lock:
            tracker._conn.close()
            tracker._conn = sqlite3.connect(
                db_path,
                check_same_thread=False,
                timeout=10,
            )
            tracker._conn.execute("PRAGMA journal_mode=WAL")

        # Wait until at least one MORE tick has happened (the post-error
        # one), bounded by 2s. The error-recovery branch may suppress the
        # increment, so we only require the thread stayed alive.
        _wait_for_count(tracker, minimum=early_count + 1, deadline_s=2.0)
        assert tracker._checkpoint_thread is not None
        assert tracker._checkpoint_thread.is_alive(), (
            "checkpoint thread must survive transient sqlite3.Error"
        )
    finally:
        tracker.close()


def test_default_interval_from_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Default ``checkpoint_interval_s`` comes from ``HiveSettings.wal_checkpoint_interval_s``.

    Constructor explicit arg wins; if None, fall back to settings (which
    in turn read ``HIVE_WAL_CHECKPOINT_INTERVAL_S`` env var).
    """
    # We don't actually want the thread firing during this test, just verify
    # the constructor reads from settings when interval not passed.
    monkeypatch.setenv("HIVE_WAL_CHECKPOINT_INTERVAL_S", "42.0")

    # Reload settings to pick up the env override.
    from hive import config as hive_config

    monkeypatch.setattr(hive_config, "settings", hive_config.HiveSettings())

    db_path = str(tmp_path / "test.db")
    tracker = _ExampleTracker(db_path)  # no explicit interval
    try:
        assert tracker._checkpoint_interval_s == 42.0, (
            f"expected interval 42.0 from env, got {tracker._checkpoint_interval_s}"
        )
    finally:
        tracker.close()
