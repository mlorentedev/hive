"""Tests for lock-acquire telemetry helpers in _helpers (HIVE-115 / ADR-010).

`_acquire_with_telemetry` and `_filelock_with_telemetry` wrap the existing
threading.Lock and filelock.FileLock acquires to emit a structured
``mcp.lock_contention`` log line on every attempt — success or timeout —
with ``waited_ms`` and ``abandoned`` fields. Telemetry feeds the Phase B
gate decision (see HIVE-115 backlog).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

import filelock
import pytest

from hive._helpers import (
    _acquire_with_telemetry,
    _filelock_with_telemetry,
    _lock_timeout,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_lock_timeout_reads_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_lock_timeout()` returns ``settings.lock_timeout_s`` (env-tunable)."""
    monkeypatch.setenv("HIVE_LOCK_TIMEOUT_S", "42")
    from hive import config as hive_config

    monkeypatch.setattr(hive_config, "settings", hive_config.HiveSettings())
    assert _lock_timeout() == 42


def test_acquire_with_telemetry_success_emits_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Successful acquire emits `mcp.lock_contention` with abandoned=false."""
    lock = threading.Lock()
    with caplog.at_level(logging.INFO, logger="hive._helpers"):
        acquired = _acquire_with_telemetry(lock, "_TEST_LOCK", timeout=1)
        try:
            assert acquired is True
        finally:
            lock.release()

    matching = [r for r in caplog.records if "mcp.lock_contention" in r.getMessage()]
    assert len(matching) == 1, f"expected 1 lock_contention log, got {len(matching)}"
    msg = matching[0].getMessage()
    assert "lock=_TEST_LOCK" in msg
    assert "abandoned=false" in msg
    assert "waited_ms=" in msg


def test_acquire_with_telemetry_timeout_emits_abandoned_true(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Timed-out acquire emits `mcp.lock_contention` with abandoned=true."""
    lock = threading.Lock()
    lock.acquire()  # block the lock
    try:
        with caplog.at_level(logging.INFO, logger="hive._helpers"):
            start = time.monotonic()
            acquired = _acquire_with_telemetry(lock, "_TEST_LOCK", timeout=0.1)
            elapsed = time.monotonic() - start
        assert acquired is False
        # Should have respected the 0.1s timeout.
        assert 0.05 < elapsed < 0.5, f"timeout misbehaving: {elapsed:.3f}s"
    finally:
        lock.release()

    matching = [r for r in caplog.records if "mcp.lock_contention" in r.getMessage()]
    assert len(matching) == 1
    msg = matching[0].getMessage()
    assert "lock=_TEST_LOCK" in msg
    assert "abandoned=true" in msg
    assert "waited_ms=" in msg


def test_acquire_with_telemetry_default_timeout_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `timeout` not passed, helper uses `settings.lock_timeout_s`."""
    monkeypatch.setenv("HIVE_LOCK_TIMEOUT_S", "1")
    from hive import config as hive_config

    monkeypatch.setattr(hive_config, "settings", hive_config.HiveSettings())

    lock = threading.Lock()
    lock.acquire()
    try:
        start = time.monotonic()
        acquired = _acquire_with_telemetry(lock, "_TEST_LOCK")  # no explicit timeout
        elapsed = time.monotonic() - start
        assert acquired is False
        assert 0.5 < elapsed < 2.0, f"should honor 1s settings default, got {elapsed:.3f}s"
    finally:
        lock.release()


def test_filelock_with_telemetry_success_emits_log(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Successful filelock acquire emits abandoned=false."""
    lock_path = tmp_path / "test.lock"
    lock = filelock.FileLock(str(lock_path))
    with (
        caplog.at_level(logging.INFO, logger="hive._helpers"),
        _filelock_with_telemetry(lock, "_TEST_FILELOCK", timeout=1),
    ):
        pass  # acquired, do nothing

    matching = [r for r in caplog.records if "mcp.lock_contention" in r.getMessage()]
    assert len(matching) == 1
    msg = matching[0].getMessage()
    assert "lock=_TEST_FILELOCK" in msg
    assert "abandoned=false" in msg


def test_filelock_with_telemetry_timeout_emits_and_reraises(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Timed-out filelock emits abandoned=true and re-raises Timeout."""
    lock_path = tmp_path / "test.lock"
    holder = filelock.FileLock(str(lock_path))
    holder.acquire(timeout=1)
    try:
        contender = filelock.FileLock(str(lock_path))
        with (
            caplog.at_level(logging.INFO, logger="hive._helpers"),
            pytest.raises(filelock.Timeout),
            _filelock_with_telemetry(contender, "_TEST_FILELOCK", timeout=0.1),
        ):
            pass  # never reached
    finally:
        holder.release()

    matching = [r for r in caplog.records if "mcp.lock_contention" in r.getMessage()]
    assert len(matching) == 1
    assert "lock=_TEST_FILELOCK" in matching[0].getMessage()
    assert "abandoned=true" in matching[0].getMessage()


# ── Rolling-window stats + compute helpers (HIVE-115 / ADR-009/010) ─────


def test_record_lock_wait_and_snapshot_empty() -> None:
    """Empty window returns zeros, not errors."""
    from hive._helpers import _GIT_LOCK_WAIT_HISTORY, _git_lock_stats_snapshot

    _GIT_LOCK_WAIT_HISTORY.clear()
    snap = _git_lock_stats_snapshot()
    assert snap == {"mean_ms": 0.0, "p99_ms": 0.0, "sample_count": 0}


def test_record_lock_wait_window_stats() -> None:
    """Window computes mean + p99 + sample_count correctly."""
    from hive._helpers import (
        _GIT_LOCK_WAIT_HISTORY,
        _git_lock_stats_snapshot,
        _record_lock_wait,
    )

    _GIT_LOCK_WAIT_HISTORY.clear()
    samples = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    for s in samples:
        _record_lock_wait(s)
    snap = _git_lock_stats_snapshot()
    assert snap["sample_count"] == 10
    assert snap["mean_ms"] == 55.0
    # p99 with n=10 → index min(9, int(10*0.99)) = min(9, 9) = 9 → 100ms
    assert snap["p99_ms"] == 100.0


def test_record_lock_wait_window_bounded_to_100() -> None:
    """Rolling window keeps only the last 100 samples."""
    from hive._helpers import (
        _GIT_LOCK_WAIT_HISTORY,
        _git_lock_stats_snapshot,
        _record_lock_wait,
    )

    _GIT_LOCK_WAIT_HISTORY.clear()
    for i in range(150):
        _record_lock_wait(i)
    snap = _git_lock_stats_snapshot()
    assert snap["sample_count"] == 100
    # Should contain only the latest 100: 50..149, mean = (50+149)/2 = 99.5
    assert snap["mean_ms"] == 99.5


def test_acquire_with_telemetry_records_into_window() -> None:
    """`_acquire_with_telemetry` populates the rolling window."""
    import threading as _threading

    from hive._helpers import (
        _GIT_LOCK_WAIT_HISTORY,
        _acquire_with_telemetry,
        _git_lock_stats_snapshot,
    )

    _GIT_LOCK_WAIT_HISTORY.clear()
    lock = _threading.Lock()
    acquired = _acquire_with_telemetry(lock, "_TEST_RECORD", timeout=1)
    try:
        snap = _git_lock_stats_snapshot()
        assert snap["sample_count"] == 1
    finally:
        if acquired:
            lock.release()


def test_compute_wal_size_bytes_empty_dir(tmp_path: Path) -> None:
    """Empty state dir returns 0."""
    from hive._helpers import _compute_wal_size_bytes

    assert _compute_wal_size_bytes(tmp_path) == 0


def test_compute_wal_size_bytes_sums_only_wal_files(tmp_path: Path) -> None:
    """Sums *.db-wal files only, ignoring other files."""
    from hive._helpers import _compute_wal_size_bytes

    (tmp_path / "a.db-wal").write_bytes(b"x" * 100)
    (tmp_path / "b.db-wal").write_bytes(b"y" * 250)
    (tmp_path / "c.db").write_bytes(b"z" * 9999)  # not WAL
    (tmp_path / "d.db-shm").write_bytes(b"q" * 9999)  # not WAL
    assert _compute_wal_size_bytes(tmp_path) == 350


def test_count_competing_hive_processes_returns_non_negative_int() -> None:
    """Helper is robust (never throws); cached snapshot is non-negative int."""
    from hive._helpers import _count_competing_hive_processes

    n = _count_competing_hive_processes()
    assert isinstance(n, int)
    assert n >= 0
