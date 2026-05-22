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
