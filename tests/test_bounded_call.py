"""TDD red phase — failing tests for ``hive._deadline.bounded_call``.

Covers HIVE-115 PR-3 acceptance criteria AC-7 + AC-8 + AC-9 + AC-9b +
AC-10 per the 2026-05-22 pre-PR-3 strategy audit. Implementations live
under ``src/hive/_deadline.py`` (new module) and refactored callsites in
``src/hive/_helpers.py``.

Pattern reference: [[pattern-multi-process-mcp-server]] §7 + ADR-008.

Tests deliberately ``@pytest.mark.skipif(sys.platform == "win32")`` where
they shell out to POSIX ``sleep``; the Windows path lives in
``TestWindowsTermination`` (T3.7).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX sleep/git path; Windows lives in TestWindowsTermination",
)


# ── T3.1 — async sleep terminated ───────────────────────────────────────


@pytest.mark.asyncio
async def test_async_sleep_terminated() -> None:
    """Pure-asyncio await > deadline raises within deadline + grace."""
    from hive._deadline import bounded_call

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await bounded_call(asyncio.sleep, 10.0, deadline_s=1.0)
    elapsed = time.monotonic() - start

    # deadline=1s + grace=2s + a generous test slack
    assert elapsed < 4.0, f"bounded_call took {elapsed:.2f}s; expected < 4s"


# ── T3.2 — blocking thread sleep terminated ─────────────────────────────


@pytest.mark.asyncio
async def test_blocking_thread_sleep_terminated() -> None:
    """``asyncio.to_thread(time.sleep, N)`` > deadline raises within budget.

    Validates the canonical lesson [[lesson-three-timeouts-chain-not-deadline]]:
    ``asyncio.timeout`` cannot interrupt the running thread on its own. The
    supervisor must enforce wall-clock — the thread is allowed to finish,
    but the caller sees the timeout error immediately.
    """
    from hive._deadline import bounded_call

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await bounded_call(
            asyncio.to_thread,
            time.sleep,
            10.0,
            deadline_s=1.0,
        )
    elapsed = time.monotonic() - start

    # The error must surface within deadline + grace, even though the
    # underlying sleep thread continues running until 10s wall-clock.
    assert elapsed < 4.0, f"bounded_call took {elapsed:.2f}s; expected < 4s"


# ── T3.3 — subprocess terminated (POSIX) ────────────────────────────────


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_subprocess_terminated() -> None:
    """A registered Popen ``sleep 60`` is killed within deadline + grace."""
    from hive._deadline import bounded_call

    registry: list[subprocess.Popen[bytes]] = []

    async def spawn_and_wait() -> None:
        proc = subprocess.Popen(  # noqa: S603
            ["sleep", "60"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        registry.append(proc)
        try:
            await asyncio.to_thread(proc.wait)
        finally:
            if proc in registry:
                registry.remove(proc)

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await bounded_call(
            spawn_and_wait,
            deadline_s=1.0,
            process_registry=registry,
        )
    elapsed = time.monotonic() - start

    assert elapsed < 4.0
    # After termination the registry should be either empty (inner ran
    # to completion via terminate-induced exit) or contain a reaped Popen
    # whose returncode is non-None.
    for proc in registry:
        assert proc.poll() is not None, "Popen still running after bounded_call"


# ── T3.4 — index lock PID ownership ─────────────────────────────────────


@_POSIX_ONLY
def test_index_lock_pid_ownership_skips_foreign(git_vault: Path) -> None:
    """``_cleanup_index_lock`` leaves a lock owned by another PID alone."""
    from hive._deadline import _cleanup_index_lock

    lock_path = git_vault / ".git" / "index.lock"
    lock_path.write_text("99999\n")  # impossibly-high foreign PID

    _cleanup_index_lock(git_vault, our_pids=[os.getpid()])

    assert lock_path.exists(), "foreign-PID lock was wrongly removed"
    assert lock_path.read_text().strip() == "99999"


@_POSIX_ONLY
def test_index_lock_pid_ownership_cleans_own(git_vault: Path) -> None:
    """``_cleanup_index_lock`` removes a lock we wrote."""
    from hive._deadline import _cleanup_index_lock

    lock_path = git_vault / ".git" / "index.lock"
    our_pid = os.getpid()
    lock_path.write_text(f"{our_pid}\n")

    _cleanup_index_lock(git_vault, our_pids=[our_pid])

    assert not lock_path.exists(), "own-PID lock not cleaned up"


@_POSIX_ONLY
def test_index_lock_missing_is_noop(git_vault: Path) -> None:
    """No-op when the lock file is absent — must not raise."""
    from hive._deadline import _cleanup_index_lock

    lock_path = git_vault / ".git" / "index.lock"
    assert not lock_path.exists()  # baseline

    # Should not raise even though there's nothing to clean.
    _cleanup_index_lock(git_vault, our_pids=[os.getpid()])

    assert not lock_path.exists()


# ── T3.5 — partial commit prevention (AC-9b) ────────────────────────────


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_partial_commit_prevention(git_vault: Path) -> None:
    """Kill between ``git add`` and ``git commit`` leaves HEAD untouched.

    AC-9b (per pre-PR-3 audit B3): the helper executes two sequential
    Popens. If termination lands between them, the index has staged
    changes but no new commit was created. HEAD must stay where it was.
    A subsequent successful call recovers by re-staging.
    """
    from hive._deadline import bounded_call

    new_file = git_vault / "audit-pr3-newfile.md"
    new_file.write_text("# new file for partial-commit test\n")

    head_before = subprocess.run(  # noqa: S603, S607
        ["git", "rev-parse", "HEAD"],
        cwd=git_vault, capture_output=True, text=True, check=True,
    ).stdout.strip()

    registry: list[subprocess.Popen[bytes]] = []

    async def two_step_commit() -> None:
        add = subprocess.Popen(  # noqa: S603, S607
            ["git", "add", new_file.name],
            cwd=git_vault, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        registry.append(add)
        await asyncio.to_thread(add.wait)
        registry.remove(add)
        # Stall between add and commit so the deadline lands HERE,
        # not during the subprocess itself.
        await asyncio.sleep(30.0)
        # Should never reach here under a 1s deadline.
        commit = subprocess.Popen(  # noqa: S603, S607
            ["git", "commit", "-m", "should-not-happen"],
            cwd=git_vault, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        registry.append(commit)
        await asyncio.to_thread(commit.wait)
        registry.remove(commit)

    with pytest.raises(TimeoutError):
        await bounded_call(
            two_step_commit,
            deadline_s=1.0,
            process_registry=registry,
        )

    head_after = subprocess.run(  # noqa: S603, S607
        ["git", "rev-parse", "HEAD"],
        cwd=git_vault, capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert head_before == head_after, "HEAD moved despite mid-commit kill"

    # Index reflects the staged add even though no commit landed.
    porcelain = subprocess.run(  # noqa: S603, S607
        ["git", "status", "--porcelain"],
        cwd=git_vault, capture_output=True, text=True, check=True,
    ).stdout
    assert "audit-pr3-newfile.md" in porcelain, (
        "staged file disappeared from porcelain — recovery path broken"
    )


# ── T3.6 — GHOST_RESPONSES source discriminator (B4) ────────────────────


def test_ghost_responses_record_accepts_source() -> None:
    """``GHOST_RESPONSES.record`` accepts ``source`` and the snapshot exposes it.

    Pre-PR-3 audit B4: deadline-driven late-respond suppressions must be
    distinguishable from cancellation-race suppressions so operators can
    tell the two failure modes apart in ``vault_health``.
    """
    from hive._compat import GHOST_RESPONSES

    GHOST_RESPONSES.reset()
    GHOST_RESPONSES.record(tool="vault_write", source="cancellation")
    GHOST_RESPONSES.record(tool="vault_write", source="deadline")
    GHOST_RESPONSES.record(tool="vault_patch", source="deadline")

    snap = GHOST_RESPONSES.snapshot()

    assert snap["total"] == 3
    assert snap["by_source"] == {"cancellation": 1, "deadline": 2}


def test_ghost_responses_record_backcompat_no_source() -> None:
    """Old callsites that omit ``source`` still work — default labels apply."""
    from hive._compat import GHOST_RESPONSES

    GHOST_RESPONSES.reset()
    GHOST_RESPONSES.record(tool="vault_write")  # no source kwarg

    snap = GHOST_RESPONSES.snapshot()
    assert snap["total"] == 1
    # Default source = "cancellation" (matches current behaviour of
    # ``_compat._patched_respond``; that callsite is the only existing
    # producer prior to PR-3).
    assert snap["by_source"] == {"cancellation": 1}


# ── T3.7 — Windows TerminateProcess (placeholder) ───────────────────────


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only TerminateProcess + CREATE_NEW_PROCESS_GROUP path",
)
@pytest.mark.asyncio
async def test_windows_subprocess_terminated_reaches_descendants() -> None:
    """Windows: ``CREATE_NEW_PROCESS_GROUP`` lets ``terminate()`` reach git's children."""
    from hive._deadline import bounded_call

    registry: list[subprocess.Popen[bytes]] = []

    async def spawn_cmd_sleep() -> None:
        proc = subprocess.Popen(  # noqa: S603
            ["cmd", "/c", "timeout", "/t", "60", "/nobreak"],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        registry.append(proc)
        try:
            await asyncio.to_thread(proc.wait)
        finally:
            if proc in registry:
                registry.remove(proc)

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await bounded_call(
            spawn_cmd_sleep,
            deadline_s=1.0,
            process_registry=registry,
        )
    elapsed = time.monotonic() - start

    assert elapsed < 5.0  # Windows has slower IPC; give an extra second
    for proc in registry:
        assert proc.poll() is not None


# ── T3.7b — concurrent bounded_call invocations (m2) ────────────────────


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_concurrent_bounded_writes_no_deadlock(git_vault: Path) -> None:
    """3 concurrent ``bounded_call`` writes to the same git_vault — no deadlock.

    Pre-PR-3 audit m2: validates the actual N=3-5 patology beyond
    single-process unit tests. This is a proxy for the full hive-subprocess
    integration test (which would require JSON-RPC handshake setup); the
    ``asyncio.gather`` here exercises the bounded_call concurrency surface
    + serialization-under-concurrency, the same shape that
    ``_helpers._git_commit`` will provide post-GREEN-3 via ``_GIT_LOCK``.

    Serialization via ``asyncio.Lock`` mirrors what the real
    ``_run_git`` callsites do via the inter-process file lock. The
    failure mode this test guards against is *deadlock* (timeout), not
    git's own lock-contention errors.
    """
    from hive._deadline import bounded_call

    serializer = asyncio.Lock()  # mirrors _GIT_LOCK behaviour in _helpers

    async def one_writer(idx: int) -> None:
        file = git_vault / f"concurrent-writer-{idx}.md"
        file.write_text(f"# writer {idx}\n")
        registry: list[subprocess.Popen[bytes]] = []

        async def commit_seq() -> None:
            async with serializer:
                for argv in (
                    ["git", "add", file.name],
                    ["git", "commit", "-m", f"writer-{idx}"],
                ):
                    proc = subprocess.Popen(  # noqa: S603, S607
                        argv, cwd=git_vault,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                    registry.append(proc)
                    try:
                        rc = await asyncio.to_thread(proc.wait)
                        if rc != 0:
                            _, err = proc.communicate(timeout=1)
                            raise RuntimeError(
                                f"git {argv[1]} rc={rc}: "
                                f"{err.decode('utf-8', 'replace').strip()}",
                            )
                    finally:
                        registry.remove(proc)

        await bounded_call(
            commit_seq, deadline_s=30.0, process_registry=registry,
        )

    start = time.monotonic()
    await asyncio.gather(*(one_writer(i) for i in range(3)))
    elapsed = time.monotonic() - start

    # Each commit is ~150ms; serialized worst case 3*150ms + lock overhead.
    # Generous ceiling: 3 * 5s = 15s. Failure mode is deadlock (timeout).
    assert elapsed < 15.0, f"concurrent writers took {elapsed:.1f}s — possible deadlock"

    log = subprocess.run(  # noqa: S603, S607
        ["git", "log", "--oneline"],
        cwd=git_vault, capture_output=True, text=True, check=True,
    ).stdout
    # All 3 commits landed
    for i in range(3):
        assert f"writer-{i}" in log, f"writer-{i} commit missing from log:\n{log}"


# ── AC-10 regression — tool_span replacement preserves contract ─────────


@pytest.mark.asyncio
async def test_bounded_call_success_returns_value() -> None:
    """Success path returns the awaitable's result; no exception, no kill."""
    from hive._deadline import bounded_call

    async def quick_work() -> int:
        await asyncio.sleep(0.01)
        return 42

    result = await bounded_call(quick_work, deadline_s=5.0)
    assert result == 42


@pytest.mark.asyncio
async def test_bounded_call_none_registry_does_not_raise() -> None:
    """``process_registry=None`` is valid; deadline still enforced."""
    from hive._deadline import bounded_call

    with pytest.raises(TimeoutError):
        await bounded_call(asyncio.sleep, 5.0, deadline_s=0.5)
    # No assertion on registry — there isn't one to inspect.
