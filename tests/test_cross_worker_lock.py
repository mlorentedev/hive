"""HIVE-116 PR-2 / AC-1 + AC-7 + AC-8: filelock eviction on deadline.

Promoted from Red-phase placeholder (PR-1) to green (PR-2) once
``evict_filelock`` + ``_drain_and_evict`` + ``LockEvictionTracker`` are
wired into the supervisor.

POSIX-first; Windows variant is the manual smoke recipe in
``docs/troubleshooting.md`` (T-3.2 will promote to CI matrix lane).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from hive import _helpers
from hive._helpers import (
    _GIT_FILELOCKS,
    _VAULT_FOR_EVICTION_CV,
    _git_filelock,
    evict_filelock,
    tool_span,
)
from hive._lock_eviction import LockEvictionTracker

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [
    pytest.mark.cross_worker,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only first pass; Windows variant lands in T-3.2",
    ),
]


# ── Fast-drain fixture ──────────────────────────────────────────────────


@pytest.fixture
def fast_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink HIVE_POST_KILL_DRAIN_S to 0.5s for test latency."""
    from hive.config import settings

    monkeypatch.setattr(settings, "post_kill_drain_s", 0.5)


# ── AC-1: eviction primitive in isolation ──────────────────────────────


def test_evict_filelock_pops_singleton(git_vault: Path) -> None:
    """``evict_filelock`` removes the cached FileLock from the singleton dict."""
    _git_filelock(git_vault)
    key = str(git_vault / ".git" / "hive.lock")
    assert key in _GIT_FILELOCKS

    assert evict_filelock(git_vault) is True
    assert key not in _GIT_FILELOCKS


def test_evict_filelock_idempotent(git_vault: Path) -> None:
    """Second eviction is a no-op (returns False)."""
    _git_filelock(git_vault)
    assert evict_filelock(git_vault) is True
    assert evict_filelock(git_vault) is False


def test_evict_filelock_then_reacquire_creates_fresh_instance(
    git_vault: Path,
) -> None:
    """After eviction, the next acquire returns a NEW FileLock object."""
    lock_a = _git_filelock(git_vault)
    assert evict_filelock(git_vault) is True
    lock_b = _git_filelock(git_vault)
    # Same lock file path, but different Python objects
    assert lock_a is not lock_b


# ── AC-1 + AC-8: tool_span deadline → eviction + tracker record ────────


@pytest.mark.asyncio
async def test_tool_span_deadline_evicts_filelock(
    git_vault: Path,
    fast_drain: None,  # noqa: ARG001
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Headline contract: tool_span deadline + killed Popen → filelock evicted.

    Spawns a real ``sleep`` Popen, registers it on the supervisor's CV
    registry (mimicking what ``_run_git`` would do), and waits for the
    deadline. Asserts:

    1. ``mcp.lock_eviction`` log line fires (AC-1)
    2. Cached FileLock is popped from ``_GIT_FILELOCKS`` (AC-1)
    3. ``LockEvictionTracker.record`` is called (AC-8 — verified via the
       registered global tracker)
    """
    caplog.set_level(logging.INFO, logger="hive._helpers")

    # 1. Prime the filelock cache so eviction has something to remove.
    _git_filelock(git_vault)
    key = str(git_vault / ".git" / "hive.lock")
    assert key in _GIT_FILELOCKS

    # 2. Install a tracker singleton + capture record() calls
    tracker = LockEvictionTracker(db_path=":memory:")
    _helpers.register_lock_eviction_tracker(tracker)
    try:
        # 3. Wire the eviction CV (mimics wrap_sync_tool for vault_write).
        vault_token = _VAULT_FOR_EVICTION_CV.set(git_vault)
        try:
            with pytest.raises(TimeoutError):  # noqa: PT012
                async with tool_span(
                    "test_tool_span_deadline_evicts_filelock",
                    timeout_seconds=0.5,
                ):
                    # Spawn a long-running Popen and register it for the
                    # supervisor to terminate. start_new_session enables
                    # killpg on POSIX.
                    proc = subprocess.Popen(  # noqa: S603, S607
                        ["sleep", "10"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                    )
                    registry = _helpers._GIT_REGISTRY_CV.get()
                    assert registry is not None
                    registry.append(proc)
                    # Sleep > deadline so the supervisor fires.
                    await asyncio.sleep(5.0)
        finally:
            _VAULT_FOR_EVICTION_CV.reset(vault_token)

        # 4. Eviction observed
        assert key not in _GIT_FILELOCKS, (
            "expected _GIT_FILELOCKS to no longer contain the vault key"
        )
        eviction_logs = [
            r.message for r in caplog.records
            if "mcp.lock_eviction" in r.message
        ]
        assert eviction_logs, (
            f"expected mcp.lock_eviction log line; got: "
            f"{[r.message for r in caplog.records]}"
        )

        # 5. Tracker recorded the event
        assert tracker.count_last_30d() == 1
        assert tracker.last_iso() is not None
    finally:
        _helpers.register_lock_eviction_tracker(None)
        tracker.close()


# ── AC-7 sketch: sibling worker unblocks after eviction ────────────────


@pytest.mark.asyncio
async def test_sibling_acquire_unblocks_after_eviction(
    git_vault: Path,
) -> None:
    """Same-process sibling: after eviction, a fresh acquire succeeds.

    Models the cross-process case where worker B is blocked on the OS-level
    filelock held by worker A's stuck thread. Within a single process we
    cannot deadlock on the same FileLock (it's a singleton + reentrant
    threading lock under the hood), so we exercise the *eviction* portion
    only: prime, evict, re-acquire. Cross-process behaviour requires
    spawning two ``hive-vault`` subprocesses — left as the Windows-only
    smoke recipe in docs/troubleshooting.md.
    """
    # Worker A acquires
    lock_a = _git_filelock(git_vault)
    # Supervisor evicts on deadline
    assert evict_filelock(git_vault) is True
    # Worker B acquires a fresh instance — must not be the same object
    lock_b = _git_filelock(git_vault)
    assert lock_a is not lock_b
    # Worker B can actually take the OS-level lock
    with lock_b.acquire(timeout=2.0):
        pass


# ── Unused import guard ────────────────────────────────────────────────

_ = (contextvars, os)
