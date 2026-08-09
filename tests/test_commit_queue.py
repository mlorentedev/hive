"""Tests for the deferred-commit reconciler primitives (HIVE-322 / ADR-018).

The reconciler runs in a plain daemon thread, so it cannot reuse
``bounded_call`` — that is ``async def`` and would need an event loop
stood up inside the thread purely to call it (ADR-018 §2). What it reuses
instead is the *termination* half of ``_deadline.py``, which is where the
cross-OS risk actually lives.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX sleep/process-group path; Windows termination is covered in test_bounded_call",
)


# ── AC4 — a flush that overruns its deadline ────────────────────────────


@_POSIX_ONLY
def test_flush_exceeding_deadline_is_terminated(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC4: an overrunning flush is killed, logged, and reports failure.

    The flush is driven past its deadline by replacing ``_run_git`` with a
    stand-in that spawns a genuinely long-lived process and registers it
    exactly as the real helper documents — caller-supplied list, else
    ``_GIT_REGISTRY_CV``. That keeps the subject of the test the watchdog,
    not git's own timing, while still handing it a real OS process to
    terminate.
    """
    from hive import _helpers
    from hive._commit_queue import flush_paths

    spawned: list[subprocess.Popen[bytes]] = []

    def _hanging_run_git(
        args: list[str],
        vault_path: Path,
        *,
        registry: list[subprocess.Popen[bytes]] | None = None,
    ) -> tuple[int, str, str]:
        if registry is None:
            registry = _helpers._GIT_REGISTRY_CV.get()
        proc = subprocess.Popen(  # noqa: S603, S607
            ["sleep", "60"],
            cwd=vault_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        spawned.append(proc)
        if registry is not None:
            registry.append(proc)
        proc.communicate()
        return (0, "", "")

    monkeypatch.setattr(_helpers, "_run_git", _hanging_run_git)

    target = git_vault / "10_projects" / "deadline-victim.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# queued before the flush\n", encoding="utf-8")

    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="hive._commit_queue"):
        ok = flush_paths(
            git_vault,
            [target.relative_to(git_vault)],
            "chore(vault): deferred commit",
            deadline_s=1.0,
        )
    elapsed = time.monotonic() - started

    # The flush reports failure rather than raising — the reconciler thread
    # must survive a git that hangs.
    assert ok is False

    # It returned on its own deadline, not on the 60s sleep.
    assert elapsed < 20.0, f"flush did not honour its deadline: {elapsed:.1f}s"

    # No orphaned process survives the kill.
    for proc in spawned:
        proc.wait(timeout=10)
        assert proc.poll() is not None

    # No stale index.lock is left behind for the next flush to trip over.
    assert not (git_vault / ".git" / "index.lock").exists()

    # The kill is observable; a silent one would be indistinguishable from
    # a flush that simply had nothing to do.
    assert any("deadline" in rec.message for rec in caplog.records), [
        r.message for r in caplog.records
    ]


@_POSIX_ONLY
def test_flush_within_deadline_commits_and_reports_success(git_vault: Path) -> None:
    """A flush that completes in time commits its paths and returns True."""
    from hive._commit_queue import flush_paths

    target = git_vault / "10_projects" / "happy-path.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# committed by the reconciler\n", encoding="utf-8")

    head_before = subprocess.run(  # noqa: S603, S607
        ["git", "rev-parse", "HEAD"],
        cwd=git_vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    ok = flush_paths(
        git_vault,
        [target.relative_to(git_vault)],
        "chore(vault): deferred commit",
        deadline_s=30.0,
    )

    assert ok is True

    head_after = subprocess.run(  # noqa: S603, S607
        ["git", "rev-parse", "HEAD"],
        cwd=git_vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_after != head_before, "flush reported success but HEAD did not move"

    committed = subprocess.run(  # noqa: S603, S607
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=git_vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "10_projects/happy-path.md" in committed


# ── AC2 / AC8 — the queue and its reconciler ────────────────────────────


def _text_of(result: object) -> str:
    """Extract the text payload from a ToolResult."""
    return result.content[0].text  # type: ignore[attr-defined,union-attr,no-any-return]


def _head(vault: Path) -> str:
    return subprocess.run(  # noqa: S603, S607
        ["git", "rev-parse", "HEAD"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _rev_count(vault: Path) -> int:
    return int(
        subprocess.run(  # noqa: S603, S607
            ["git", "rev-list", "--count", "HEAD"],
            cwd=vault,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )


def _files_in_head(vault: Path) -> list[str]:
    return subprocess.run(  # noqa: S603, S607
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()


def _write(vault: Path, rel: str) -> Path:
    target = vault / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# {rel}\n", encoding="utf-8")
    return target


def test_queue_dedups_at_enqueue_not_at_drain() -> None:
    """AC8: the same path twice produces ONE queue entry, not two that collapse.

    Deduplicating on the way out would satisfy "appears once in the commit"
    while leaving depth reporting dishonest — AC5 surfaces queue depth, and
    a depth of 2 for one pending file would misdescribe the backlog.
    """
    from pathlib import Path as _Path

    from hive._commit_queue import CommitQueue

    queue = CommitQueue()
    queue.add(_Path("10_projects/a.md"))
    queue.add(_Path("10_projects/a.md"))
    queue.add(_Path("10_projects/b.md"))

    assert len(queue) == 2, "dedup must happen at enqueue, not at drain"

    drained = queue.drain()
    assert drained == [_Path("10_projects/a.md"), _Path("10_projects/b.md")]
    assert len(queue) == 0


def test_queue_drain_is_atomic_against_concurrent_add() -> None:
    """A path added during a drain lands in the fresh buffer, never lost."""
    from pathlib import Path as _Path

    from hive._commit_queue import CommitQueue

    queue = CommitQueue()
    queue.add(_Path("a.md"))
    drained = queue.drain()
    queue.add(_Path("b.md"))

    assert drained == [_Path("a.md")]
    assert queue.drain() == [_Path("b.md")]


@_POSIX_ONLY
def test_one_tick_produces_one_commit_with_exactly_the_queued_paths(
    git_vault: Path,
) -> None:
    """AC2: N writes across one tick produce exactly one commit of those N paths.

    Driven by an explicit ``flush_now()`` rather than by waiting on wall-clock
    ticks — a timing-dependent assertion here would be the same class of
    non-deterministic CI failure as hive#344.
    """
    from hive._commit_queue import CommitReconciler

    rels = ["10_projects/one.md", "10_projects/two.md", "00_meta/three.md"]
    for rel in rels:
        _write(git_vault, rel)

    # A dirty file that is NOT queued. The reconciler must not sweep it —
    # this is the ADR-014 invariant AC7 will guard explicitly.
    _write(git_vault, "10_projects/unqueued.md")

    reconciler = CommitReconciler(git_vault, tick_s=3600.0)
    try:
        for rel in rels:
            reconciler.enqueue(git_vault / rel)
        assert len(reconciler.queue) == 3

        count_before = _rev_count(git_vault)
        assert reconciler.flush_now() is True
        count_after = _rev_count(git_vault)
    finally:
        reconciler.close()

    assert count_after == count_before + 1, "one tick must produce exactly one commit"
    assert sorted(_files_in_head(git_vault)) == sorted(rels)
    assert "10_projects/unqueued.md" not in _files_in_head(git_vault)
    assert len(reconciler.queue) == 0


@_POSIX_ONLY
def test_path_written_twice_in_one_tick_appears_once_in_the_commit(
    git_vault: Path,
) -> None:
    """AC8, end to end: a repeated write yields one entry and one commit path."""
    from hive._commit_queue import CommitReconciler

    target = _write(git_vault, "10_projects/rewritten.md")

    reconciler = CommitReconciler(git_vault, tick_s=3600.0)
    try:
        reconciler.enqueue(target)
        target.write_text("# rewritten before the tick\n", encoding="utf-8")
        reconciler.enqueue(target)

        assert len(reconciler.queue) == 1

        assert reconciler.flush_now() is True
    finally:
        reconciler.close()

    assert _files_in_head(git_vault) == ["10_projects/rewritten.md"]


@_POSIX_ONLY
def test_enqueue_normalises_absolute_and_relative_to_one_entry(
    git_vault: Path,
) -> None:
    """Absolute and vault-relative spellings of one file must not both queue.

    Without normalisation the dedup is defeated by the caller's choice of
    spelling, and the same file would be staged twice in one commit.
    """
    from pathlib import Path as _Path

    from hive._commit_queue import CommitReconciler

    target = _write(git_vault, "10_projects/spelled-twice.md")

    reconciler = CommitReconciler(git_vault, tick_s=3600.0)
    try:
        reconciler.enqueue(target)
        reconciler.enqueue(_Path("10_projects/spelled-twice.md"))
        assert len(reconciler.queue) == 1
    finally:
        reconciler.close()


@_POSIX_ONLY
def test_flush_with_nothing_queued_creates_no_commit(git_vault: Path) -> None:
    """An idle tick is a no-op, not an empty commit."""
    from hive._commit_queue import CommitReconciler

    reconciler = CommitReconciler(git_vault, tick_s=3600.0)
    try:
        count_before = _rev_count(git_vault)
        assert reconciler.flush_now() is False
        assert _rev_count(git_vault) == count_before
    finally:
        reconciler.close()


@_POSIX_ONLY
def test_reconciler_thread_drains_on_its_own_tick(git_vault: Path) -> None:
    """The daemon loop flushes without an explicit call.

    Deliberately the only wall-clock test here: it asserts the loop is wired
    at all, with a generous bound, rather than asserting anything about when.
    """
    from hive._commit_queue import CommitReconciler

    target = _write(git_vault, "10_projects/by-the-tick.md")

    reconciler = CommitReconciler(git_vault, tick_s=0.2)
    try:
        count_before = _rev_count(git_vault)
        reconciler.enqueue(target)
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if _rev_count(git_vault) > count_before:
                break
            time.sleep(0.1)
    finally:
        reconciler.close()

    assert _rev_count(git_vault) == count_before + 1
    assert _files_in_head(git_vault) == ["10_projects/by-the-tick.md"]


# ── AC14 — a failed flush drops rather than re-queues ───────────────────


@_POSIX_ONLY
def test_failed_flush_drops_its_paths_and_does_not_requeue(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC14: a failed commit forgets its paths; it never puts them back.

    Re-queueing is the retry loop "report, never self-heal" rejects: under a
    permanent failure — a rejecting hook, a full disk, a foreign index.lock —
    every tick would spawn another doomed git against a queue that only
    grows. The files stay on disk, so what is dropped is the knowledge that a
    commit is owed, and AC11's report is what carries that instead.
    """
    from hive import _commit_queue
    from hive._commit_queue import CommitReconciler

    monkeypatch.setattr(
        _commit_queue,
        "_git_commit",
        lambda *_args, **_kwargs: False,
    )

    target = _write(git_vault, "10_projects/doomed.md")

    reconciler = CommitReconciler(git_vault, tick_s=3600.0)
    try:
        reconciler.enqueue(target)
        assert len(reconciler.queue) == 1

        with caplog.at_level(logging.WARNING, logger="hive._commit_queue"):
            assert reconciler.flush_now() is False

        assert len(reconciler.queue) == 0, "a failed flush must not re-queue its paths"
        assert reconciler.queue.peek() == []
    finally:
        reconciler.close(drain=False)

    assert any("flush_failed" in rec.message for rec in caplog.records), [
        r.message for r in caplog.records
    ]


# ── AC7 — the reconciler never stages what it did not queue ─────────────


@_POSIX_ONLY
def test_reconciler_never_stages_a_file_it_did_not_queue(git_vault: Path) -> None:
    """AC7: the load-bearing ADR-014 invariant, guarded explicitly.

    ADR-014 turned obsidian-git's auto-commit off because a timer sweeping
    ``git add -A`` stages an agent's half-written change. ADR-018 is only
    exempt from that objection because a path enters the queue *after* its
    write completes and the reconciler stages nothing else. That exemption
    is the whole basis for amending ADR-014, so it gets a test rather than
    a convention.

    Both kinds of foreign dirt are covered: an untracked new file, and a
    modification to an already-tracked one. A refactor to ``git add -A``
    would pass a test that only checked the former, because ``git show
    --name-only`` on a fresh commit would still list it.
    """
    from hive._commit_queue import CommitReconciler

    tracked = git_vault / "10_projects" / "already-tracked.md"
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("# committed at fixture time\n", encoding="utf-8")
    subprocess.run(  # noqa: S603, S607
        ["git", "add", "10_projects/already-tracked.md"],
        cwd=git_vault,
        capture_output=True,
        check=True,
    )
    subprocess.run(  # noqa: S603, S607
        ["git", "commit", "-m", "track the file"],
        cwd=git_vault,
        capture_output=True,
        check=True,
    )

    # Foreign dirt of both kinds, neither queued.
    tracked.write_text("# edited by a human in Obsidian, mid-sentence\n", encoding="utf-8")
    _write(git_vault, "10_projects/untracked-foreign.md")

    ours = _write(git_vault, "10_projects/ours.md")

    reconciler = CommitReconciler(git_vault, tick_s=3600.0)
    try:
        reconciler.enqueue(ours)
        assert reconciler.flush_now() is True
    finally:
        reconciler.close()

    assert _files_in_head(git_vault) == ["10_projects/ours.md"]

    # The foreign edits survive untouched in the working tree — the
    # reconciler neither committed nor reverted them.
    still_dirty = subprocess.run(  # noqa: S603, S607
        ["git", "status", "--porcelain"],
        cwd=git_vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "10_projects/already-tracked.md" in still_dirty
    assert "10_projects/untracked-foreign.md" in still_dirty
    assert tracked.read_text(encoding="utf-8").startswith("# edited by a human")


# ── AC13 — the deferred commit runs under the inter-process lock ────────


@_POSIX_ONLY
def test_reconciler_commit_happens_under_the_git_filelock(git_vault: Path) -> None:
    """AC13: a concurrent holder of ``_git_filelock`` excludes the flush.

    The synchronous write path took this lock and released it when the tool
    returned. Deferral moves the commit *after* that release, so without the
    reconciler taking the lock itself the deferred commit would run outside
    the very lock the daemon-only rescope was dropped on the strength of
    (ADR-018 §Decision): with P processes the bound is P commits per tick
    only if those P commits cannot interleave.

    Exclusion is asserted from another thread on purpose.
    ``_git_filelock`` returns a per-vault singleton, but ``filelock``
    builds it ``thread_local=True``, so a same-thread re-entry is free —
    that is what lets ``vault_write_lock`` nest ``_git_commit`` without
    deadlocking — while a *foreign* thread opens its own descriptor and
    genuinely contends at the OS level. Holding the lock on this thread
    and flushing on this thread would therefore prove nothing.
    """
    import threading

    from hive._commit_queue import CommitReconciler
    from hive._helpers import _git_filelock

    target = _write(git_vault, "10_projects/under-the-lock.md")
    count_before = _rev_count(git_vault)

    reconciler = CommitReconciler(git_vault, tick_s=3600.0)
    flushed: list[bool] = []

    def _flush() -> None:
        flushed.append(reconciler.flush_now())

    try:
        reconciler.enqueue(target)

        with _git_filelock(git_vault).acquire(timeout=30.0):
            worker = threading.Thread(target=_flush, name="ac13-flush", daemon=True)
            worker.start()

            # Blocked, not merely slow: the flush waits out the whole hold.
            # The lock timeout is 30s by default, so this window is well
            # inside it and the flush resumes rather than abandoning.
            worker.join(timeout=1.5)
            assert worker.is_alive(), "flush completed while the filelock was held"
            assert _rev_count(git_vault) == count_before, (
                "a commit landed while another holder had the filelock"
            )

        worker.join(timeout=30.0)
        assert not worker.is_alive(), "flush never resumed after the filelock released"
    finally:
        reconciler.close(drain=False)

    assert flushed == [True], f"flush did not commit after acquiring the lock: {flushed}"
    assert _rev_count(git_vault) == count_before + 1
    assert _files_in_head(git_vault) == ["10_projects/under-the-lock.md"]


# ── AC1 — the write path defers instead of committing inline ────────────


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_vault_write_deferred_does_not_commit_in_its_call_path(
    git_vault: Path,
) -> None:
    """AC1: a deferred write returns with no ``git commit`` in its call path.

    Asserted by intercepting ``_git_commit`` rather than by timing, so the
    test states the contract ("no commit happened here") instead of
    measuring a proxy for it.
    """
    from hive import _vault_write
    from hive.server import create_server

    calls: list[list[str]] = []
    real_commit = _vault_write._git_commit

    def _spy(vault: Path, rel_paths: list[Path], message: str) -> bool:
        calls.append([p.as_posix() for p in rel_paths])
        return real_commit(vault, rel_paths, message)

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]
    assert ctx.reconciler is not None, "serving contexts must carry a reconciler"

    import unittest.mock

    with unittest.mock.patch.object(_vault_write, "_git_commit", _spy):
        await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "path": "deferred-write.md",
                "content": "# written without a commit\n",
                "commit": False,
            },
        )

    assert calls == [], f"a deferred write must not commit inline, got {calls}"
    assert [p.as_posix() for p in ctx.reconciler.queue.peek()] == [
        "10_projects/testproject/deferred-write.md"
    ]

    # And the deferred path really does commit when the tick comes.
    count_before = _rev_count(git_vault)
    assert ctx.reconciler.flush_now() is True
    assert _rev_count(git_vault) == count_before + 1
    assert _files_in_head(git_vault) == ["10_projects/testproject/deferred-write.md"]

    ctx.reconciler.close()


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_vault_write_commit_true_still_commits_synchronously(
    git_vault: Path,
) -> None:
    """``commit=True`` stays the synchronous escape hatch (ADR-018 §4)."""
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]

    count_before = _rev_count(git_vault)
    await mcp.call_tool(
        "vault_write",
        {
            "project": "testproject",
            "path": "sync-write.md",
            "content": "# committed before the call returned\n",
            "commit": True,
        },
    )

    assert _rev_count(git_vault) == count_before + 1
    assert len(ctx.reconciler.queue) == 0
    ctx.reconciler.close()


# ── AC6 — clean shutdown drains the queue ───────────────────────────────


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_server_lifespan_shutdown_drains_the_queue(git_vault: Path) -> None:
    """AC6: nothing queued is discarded when the server shuts down cleanly.

    Driven through the FastMCP lifespan rather than through a signal, which
    is the seam both transports share: the stdio run and the daemon's
    ``http_app(lifespan="on")`` both fire it on teardown. A drain hung off
    ``finally`` would not survive the daemon's stop path — ``_daemon`` records
    that uvicorn's SIGTERM handling exits via the signal and bypasses
    ``finally``/``atexit``, and SIGTERM is exactly how systemd stops it.
    """
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]

    target = _write(git_vault, "10_projects/queued-at-shutdown.md")
    ctx.reconciler.enqueue(target)
    assert len(ctx.reconciler.queue) == 1

    count_before = _rev_count(git_vault)

    # Drive the ASGI lifespan exactly as uvicorn does on the daemon path.
    app = mcp.http_app(path="/mcp", transport="http")
    async with app.router.lifespan_context(app):
        pass

    assert _rev_count(git_vault) == count_before + 1, (
        "a clean shutdown must commit what was queued, not discard it"
    )
    assert _files_in_head(git_vault) == ["10_projects/queued-at-shutdown.md"]
    assert len(ctx.reconciler.queue) == 0


# ── AC5 — the reconciler is observable in vault_health ──────────────────


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_vault_health_reports_queue_depth_and_last_flush_age(
    git_vault: Path,
) -> None:
    """AC5: queue depth and last-flush age are visible in the runtime block.

    Both numbers are reported alongside the tick, because neither is
    interpretable alone: a depth of 12 is normal one tick after a burst and
    alarming ten ticks later, and that judgement needs the tick length to
    compare against. Together they are what makes a stalled reconciler
    visible rather than silent.
    """
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]

    # Nothing queued, nothing flushed yet.
    report = _text_of(
        await mcp.call_tool("vault_health", {"include_runtime": True}),
    )
    assert "commit_queue:" in report
    assert "depth: 0" in report
    assert "last_flush_age_s: null" in report

    # A queued path shows up as depth.
    for name in ("a.md", "b.md"):
        ctx.reconciler.enqueue(_write(git_vault, f"10_projects/{name}"))
    report = _text_of(
        await mcp.call_tool("vault_health", {"include_runtime": True}),
    )
    assert "depth: 2" in report

    # After a drain the depth clears and the age becomes a real number.
    assert ctx.reconciler.flush_now() is True
    report = _text_of(
        await mcp.call_tool("vault_health", {"include_runtime": True}),
    )
    assert "depth: 0" in report
    assert "last_flush_age_s: null" not in report

    ctx.reconciler.close()


# ── AC12 — deferral becomes the default ─────────────────────────────────


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_default_write_defers_and_commit_true_is_the_escape_hatch(
    git_vault: Path,
) -> None:
    """AC12: the default no longer commits; ``commit=True`` still does.

    This is the breaking half of ADR-018. A successful write stops implying
    that a commit exists — which is the whole point, since the measured
    problem was caused by the default rather than by the absence of an
    opt-in. HIVE-104 already shipped the opt-in and agents kept avoiding
    the MCP for writes.
    """
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]

    # Plain call: no commit in the call path.
    before = _rev_count(git_vault)
    await mcp.call_tool(
        "vault_write",
        {
            "project": "testproject",
            "path": "default-defers.md",
            "content": "# no commit here\n",
        },
    )
    assert _rev_count(git_vault) == before
    assert len(ctx.reconciler.queue) == 1

    # commit=False is indistinguishable from the default: queued, not held.
    await mcp.call_tool(
        "vault_write",
        {
            "project": "testproject",
            "path": "explicit-false.md",
            "content": "# also queued\n",
            "commit": False,
        },
    )
    assert _rev_count(git_vault) == before
    assert len(ctx.reconciler.queue) == 2

    # commit=True remains the synchronous escape hatch.
    await mcp.call_tool(
        "vault_write",
        {
            "project": "testproject",
            "path": "sync-escape-hatch.md",
            "content": "# committed before returning\n",
            "commit": True,
        },
    )
    assert _rev_count(git_vault) == before + 1
    assert "10_projects/testproject/sync-escape-hatch.md" in _files_in_head(git_vault)

    ctx.reconciler.close()


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_vault_patch_default_defers(git_vault: Path) -> None:
    """AC12, the patch half: the default defers there too."""
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]

    before = _rev_count(git_vault)
    await mcp.call_tool(
        "vault_patch",
        {
            "project": "testproject",
            "path": "11-tasks.md",
            "find": "- [ ] Task one",
            "replace": "- [x] Task one done",
        },
    )
    assert _rev_count(git_vault) == before
    assert len(ctx.reconciler.queue) == 1

    ctx.reconciler.close()


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_vault_delete_commits_synchronously_regardless_of_tick(
    git_vault: Path,
) -> None:
    """AC12: ``vault_delete`` opts out of the queue entirely.

    A delete and a recreate inside one tick collapse to a single state, and
    git-recoverability is precisely the guarantee this tool sells — so it
    keeps committing inline even though every other write now defers.
    """
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]

    before = _rev_count(git_vault)
    await mcp.call_tool(
        "vault_delete",
        {"project": "testproject", "path": "90-lessons.md"},
    )
    assert _rev_count(git_vault) == before + 1, "vault_delete must still commit inline"
    assert len(ctx.reconciler.queue) == 0

    ctx.reconciler.close()


# ── AC9 — startup reports uncommitted paths and never commits ───────────
#
# The enumerator these two ACs share lives in `_helpers.uncommitted_paths`.
# It is built once here and spent twice: AC9 logs it at daemon startup, AC11
# renders it in the `vault_health` runtime block. Both need count + oldest
# age and nothing else, but the primitive returns the paths so the summary
# stays derived and the parse stays testable.


def _dirty(vault: Path) -> str:
    """Porcelain status, for asserting a path is still uncommitted."""
    return subprocess.run(  # noqa: S603, S607
        ["git", "status", "--porcelain"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_uncommitted_paths_enumerates_untracked_staged_and_modified(
    git_vault: Path,
) -> None:
    """The enumerator must see all three shapes of "on disk, not committed".

    The staged-but-uncommitted case is the one worth stating: a flush killed
    between ``git add`` and ``git commit`` — the HIVE-115 AC-9b window, and
    exactly AC14's deadline-kill path — leaves residue with only the X column
    set. An enumerator that filtered to ``??`` and `` M`` would hide the
    report's most important customer.
    """
    from hive._helpers import uncommitted_paths

    _write(git_vault, "10_projects/testproject/untracked.md")

    staged = _write(git_vault, "10_projects/testproject/staged.md")
    subprocess.run(  # noqa: S603, S607
        ["git", "add", str(staged.relative_to(git_vault))],
        cwd=git_vault,
        capture_output=True,
        check=True,
    )

    tracked = git_vault / "10_projects" / "testproject" / "00-context.md"
    tracked.write_text(tracked.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")

    found = uncommitted_paths(git_vault)
    assert found is not None
    by_path = {p.rel_path: p for p in found}

    assert "10_projects/testproject/untracked.md" in by_path
    assert "10_projects/testproject/staged.md" in by_path, (
        "a flush killed between `git add` and `git commit` leaves staged residue; "
        "the report exists for exactly that file"
    )
    assert "10_projects/testproject/00-context.md" in by_path

    assert by_path["10_projects/testproject/untracked.md"].status == "??"
    assert by_path["10_projects/testproject/staged.md"].status.startswith("A")
    # mtime is the only provenance-free age signal available.
    assert by_path["10_projects/testproject/untracked.md"].mtime is not None


def test_uncommitted_paths_returns_none_when_enumeration_fails(tmp_path: Path) -> None:
    """A failed enumeration is NOT an empty one.

    AC11 calls this the entire recovery signal, so conflating "git could not
    tell me" with "nothing is pending" is precisely the silent data rot the
    row warns about. `None` is the only honest answer for a non-repo.
    """
    from hive._helpers import uncommitted_paths

    assert uncommitted_paths(tmp_path) is None, "a non-repo must report unknown, not clean"


def test_uncommitted_paths_parses_a_renamed_entry_without_misaligning(
    git_vault: Path,
) -> None:
    """`-z` emits a second NUL field for renames — consume it or the parse skews.

    Without the extra read, the source path is mistaken for the next entry's
    ``XY PATH`` record and every subsequent path is garbage. One rename is
    enough to catch it, and the trailing untracked file is what proves the
    stream re-synchronised rather than merely surviving.
    """
    from hive._helpers import uncommitted_paths

    src = git_vault / "10_projects" / "testproject" / "90-lessons.md"
    subprocess.run(  # noqa: S603, S607
        ["git", "mv", str(src.relative_to(git_vault)), "10_projects/testproject/renamed.md"],
        cwd=git_vault,
        capture_output=True,
        check=True,
    )
    _write(git_vault, "10_projects/testproject/zz-after-the-rename.md")

    found = uncommitted_paths(git_vault)
    assert found is not None
    rel_paths = {p.rel_path for p in found}

    assert "10_projects/testproject/renamed.md" in rel_paths
    assert "10_projects/testproject/zz-after-the-rename.md" in rel_paths, (
        "the entry after a rename was lost — the `-z` source field was not consumed"
    )
    assert "" not in rel_paths


def test_uncommitted_summary_reports_unknown_rather_than_clean(tmp_path: Path) -> None:
    """The summary preserves the primitive's unknown/clean distinction."""
    from hive._helpers import uncommitted_summary

    count, oldest_age_s = uncommitted_summary(tmp_path)
    assert count is None
    assert oldest_age_s is None


def test_uncommitted_summary_counts_and_ages_a_dirty_tree(git_vault: Path) -> None:
    """Non-vacuity the other way: a dirty tree yields a real count and age."""
    from hive._helpers import uncommitted_summary

    _write(git_vault, "10_projects/testproject/pending.md")

    count, oldest_age_s = uncommitted_summary(git_vault)
    assert count is not None
    assert count >= 1
    assert oldest_age_s is not None
    assert oldest_age_s >= 0.0


def test_startup_self_heal_reports_uncommitted_paths_and_never_commits(
    git_vault: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC9: startup reports; it never commits — identically in both regimes.

    Asserted **with and without** the daemon lock held because the point of
    the ADR-018 §3 revision is that the two regimes stopped differing. The
    first draft let daemon-side recovery commit under ``daemon.lock``; review
    killed it, since that lock excludes sibling hives but not a human with a
    half-edited note open in Obsidian. Report-only is the only resolution
    needing no provenance, so a future change that makes the behaviour
    lock-dependent again is the regression this test exists to catch.
    """
    import filelock

    from hive import _daemon

    dirty = _write(git_vault, "10_projects/testproject/orphaned-by-a-crash.md")
    before_bytes = dirty.read_bytes()
    before_head = _head(git_vault)
    before_count = _rev_count(git_vault)

    lock = filelock.FileLock(str(_daemon.lock_file_path()))

    for regime, held in (("daemon lock held", True), ("no daemon lock", False)):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="hive._daemon"):
            if held:
                with lock:
                    _daemon._startup_self_heal(git_vault)
            else:
                _daemon._startup_self_heal(git_vault)

        assert _head(git_vault) == before_head, f"{regime}: startup moved HEAD"
        assert _rev_count(git_vault) == before_count, f"{regime}: startup created a commit"
        assert dirty.read_bytes() == before_bytes, f"{regime}: startup rewrote the dirty file"
        assert "10_projects/testproject/orphaned-by-a-crash.md" in _dirty(git_vault), (
            f"{regime}: the dirty path stopped being uncommitted"
        )

        report = [r for r in caplog.records if "uncommitted" in r.getMessage()]
        assert report, f"{regime}: startup produced no uncommitted-path report"
        message = report[0].getMessage()
        assert "uncommitted_count=" in message, f"{regime}: report omits the count"
        assert "oldest_age_s=" in message, f"{regime}: report omits the oldest age"
        assert "orphaned-by-a-crash.md" not in message, (
            f"{regime}: the report enumerated paths; a dirty vault would flood the log"
        )


# ── AC11 — the uncommitted-path report reaches vault_health ─────────────


@pytest.mark.asyncio
async def test_vault_health_reports_uncommitted_count_and_oldest_age(
    git_vault: Path,
) -> None:
    """AC11: count and oldest age of uncommitted paths, beside the queue depth.

    With AC9 refusing to self-heal, this is the *entire* recovery signal for
    a path dropped by a failed flush or orphaned by a hard kill. It sits next
    to ``commit_queue`` on purpose: mid-tick the two overlap, and that is
    truthful — a queued path really is uncommitted on disk. Subtracting the
    queue would reintroduce the provenance reasoning ADR-018 §3 removed.
    """
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]

    report = _text_of(await mcp.call_tool("vault_health", {"include_runtime": True}))
    assert "uncommitted:" in report
    assert "count: 0" in report, "a clean tree must report zero, not unknown"

    # A file on disk with no commit behind it — the shape AC14 drops and a
    # hard kill orphans.
    _write(git_vault, "10_projects/testproject/awaiting-a-commit.md")

    report = _text_of(await mcp.call_tool("vault_health", {"include_runtime": True}))
    assert "count: 1" in report
    oldest = [ln for ln in report.splitlines() if "oldest_age_s:" in ln]
    assert oldest, "the report omits the oldest age"
    assert "null" not in oldest[0], f"a pending path must carry a real age: {oldest[0]!r}"

    ctx.reconciler.close()


@pytest.mark.asyncio
async def test_vault_health_reports_unknown_rather_than_clean_when_git_fails(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC11's data-rot guard: a failed enumeration must never read as clean.

    ``count: 0`` when git could not answer is the silent rot the row warns
    about — an operator would see a healthy vault while orphaned paths
    accumulated. The unknown case has to be *loud in the same field*, which
    is why the primitive returns ``None`` instead of an empty list.
    """
    from hive import _helpers
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]

    monkeypatch.setattr(_helpers, "uncommitted_paths", lambda _vault: None)

    report = _text_of(await mcp.call_tool("vault_health", {"include_runtime": True}))
    uncommitted = report.split("uncommitted:", 1)[1].splitlines()[1:3]
    assert any("count: null" in ln for ln in uncommitted), (
        f"a failed enumeration reported as clean: {uncommitted!r}"
    )
    assert any("oldest_age_s: null" in ln for ln in uncommitted)

    ctx.reconciler.close()


# ── AC10 — vault_commit stays the one sanctioned sweep ──────────────────


@pytest.mark.asyncio
async def test_vault_commit_still_sweeps_foreign_working_tree_edits(
    git_vault: Path,
) -> None:
    """AC10: the explicit user flush may stage edits hive never wrote.

    This is the exact inverse of AC7, and the asymmetry is the decision.
    ADR-014's objection was never to *sweeping* — it was to a **timer**
    sweeping unasked, which is why obsidian-git's auto-commit had to go while
    ``vault_commit`` did not. A human asking for a flush has consented to
    flushing their own work in progress; a tick has no such consent.

    So a future "safety" narrowing here — path-scoping the sweep to match the
    reconciler, or filtering to hive-written paths — would look like hardening
    and would instead remove the **only** remediation ADR-018 §3 leaves. With
    startup refusing to self-heal, this sweep and the next write to the vault
    are all an orphaned path has.

    Both shapes of foreign edit are asserted, mirroring AC7's reasoning: a
    narrowing to "paths hive knows about" could still sweep an untracked file
    while silently dropping a modification to a tracked one.
    """
    from hive._helpers import uncommitted_summary
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]

    # Neither of these went through hive: one is a note dropped into the vault
    # by hand, the other an edit made in Obsidian to an already-tracked file.
    foreign_new = _write(git_vault, "10_projects/testproject/written-in-obsidian.md")
    foreign_edit = git_vault / "10_projects" / "testproject" / "11-tasks.md"
    foreign_edit.write_text(
        foreign_edit.read_text(encoding="utf-8") + "\n- [ ] added by hand\n",
        encoding="utf-8",
    )
    assert len(ctx.reconciler.queue) == 0, "fixture error: hive must not have queued these"

    before = _rev_count(git_vault)
    result = _text_of(await mcp.call_tool("vault_commit", {"message": "manual flush"}))

    assert _rev_count(git_vault) == before + 1, f"vault_commit did not commit: {result!r}"
    committed = _files_in_head(git_vault)
    assert str(foreign_new.relative_to(git_vault)) in committed, (
        "the sweep dropped an untracked foreign file — remediation is gone"
    )
    assert str(foreign_edit.relative_to(git_vault)) in committed, (
        "the sweep dropped a modification to a tracked file; a path-scoped "
        "narrowing passes the untracked case and fails exactly here"
    )

    # The loop the three rows form: AC9 reports, AC11 surfaces, this clears.
    count, _oldest = uncommitted_summary(git_vault)
    assert count == 0, f"the vault is still dirty after an explicit flush: {count}"

    ctx.reconciler.close()


# ── The ADR-010 hand-off survives the queue ─────────────────────────────


def _install_obsidian_git_config(vault: Path, interval_minutes: int = 10) -> None:
    """Make ``detect_obsidian_git`` see the plugin as installed.

    Mirrors ``test_detect_and_defer``'s fixture rather than importing it —
    the predicate itself is exercised for real here, not stubbed, so the
    test fails if the queue path and the write path ever disagree about
    what "an external committer is handling this" means.
    """
    cfg_dir = vault / ".obsidian" / "plugins" / "obsidian-git"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "data.json").write_text(
        json.dumps({"commitInterval": interval_minutes}),
        encoding="utf-8",
    )


@_POSIX_ONLY
def test_tick_defers_to_a_healthy_external_committer_and_commits_otherwise(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drain hands off to obsidian-git when it is healthy; else it commits.

    Deferral is now the default, so every write is queued whether or not an
    external committer exists. A reconciler that committed on every tick
    would therefore defeat the ADR-010 hand-off *silently* — the write path
    still consults the predicate, but only on the `commit=True` branch that
    the default flip made rare. Evaluating it at drain time keeps the
    decision in one place instead of two competing deferral mechanisms.

    The paths are **drained, not left queued**: they stay on disk for
    obsidian-git to pick up, and until it does they remain visible in the
    uncommitted-path report. That composition is the intended behaviour, not
    a leak — the report describes the working tree, whoever is committing it.
    """
    from hive._commit_queue import CommitReconciler
    from hive._helpers import uncommitted_summary

    _install_obsidian_git_config(git_vault)
    # A large tick disables the loop, so the drains below are the only ones.
    reconciler = CommitReconciler(git_vault, tick_s=3600.0)
    try:
        # ── Healthy external committer: drain, hand off, do not commit ──
        monkeypatch.setenv("HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER", "true")
        deferred = _write(git_vault, "10_projects/testproject/left-for-obsidian.md")
        reconciler.enqueue(deferred)

        before = _rev_count(git_vault)
        assert reconciler.flush_now() is False, "the drain committed despite the hand-off"
        assert _rev_count(git_vault) == before, "hive committed over obsidian-git"
        assert len(reconciler.queue) == 0, "a deferred drain must still drain"
        assert deferred.exists(), "the file must stay on disk for obsidian-git"
        # Handing off is work, so the stall detector must see a fresh drain.
        assert reconciler.last_flush_at is not None
        # Still owed a commit, and still visible as such.
        count, _oldest = uncommitted_summary(git_vault)
        assert count is not None
        assert count >= 1, "a handed-off path must remain in the uncommitted report"

        # ── No external committer: the same tick commits ───────────────
        monkeypatch.delenv("HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER", raising=False)
        committed = _write(git_vault, "10_projects/testproject/hive-commits-this.md")
        reconciler.enqueue(committed)

        before = _rev_count(git_vault)
        assert reconciler.flush_now() is True, "the drain skipped a commit it owned"
        assert _rev_count(git_vault) == before + 1
        assert "10_projects/testproject/hive-commits-this.md" in _files_in_head(git_vault)
    finally:
        reconciler.close(drain=False)


@_POSIX_ONLY
def test_tick_commits_when_the_defer_predicate_cannot_be_evaluated(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unverifiable predicate falls back to committing, never to deferring.

    Deferring on a predicate that raised would hand the paths to a committer
    nobody confirmed exists, and with startup refusing to self-heal there is
    no second chance to notice. Committing is the recoverable error of the
    two, and it matches what the predicate itself already does when it finds
    obsidian-git installed but broken.
    """
    from hive import _vault_write
    from hive._commit_queue import CommitReconciler

    def _boom(_vault: Path) -> bool:
        raise RuntimeError("plugin config unreadable")

    monkeypatch.setattr(_vault_write, "_should_defer_to_external_committer", _boom)

    reconciler = CommitReconciler(git_vault, tick_s=3600.0)
    try:
        reconciler.enqueue(_write(git_vault, "10_projects/testproject/predicate-blew-up.md"))
        before = _rev_count(git_vault)
        assert reconciler.flush_now() is True
        assert _rev_count(git_vault) == before + 1
    finally:
        reconciler.close(drain=False)


# ── The auto-flush ceiling, which is also a public knob's cliff ──────────


def test_tick_above_the_ceiling_disables_auto_flush_and_says_so(
    git_vault: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A tick past ``_MAX_AUTO_TICK_S`` starts no loop, and announces it.

    The ceiling earns its keep as a test affordance — every reconciler in
    this file passes ``tick_s=3600.0`` precisely so no background thread
    races an explicit ``flush_now()``. But ``HIVE_COMMIT_TICK_S`` is public
    and documented, so a plausible "commit every 10 minutes" reaches the
    same branch and gets no reconciler at all. Nine tests here depended on
    that branch and none asserted it, which is how a test affordance stays
    invisible as a production cliff.

    The second half is what makes this discriminating rather than a
    restatement of the constructor: *at* the ceiling the loop still starts,
    so the assertion is about the threshold, not about construction.
    """
    from hive._commit_queue import _MAX_AUTO_TICK_S, CommitReconciler

    with caplog.at_level(logging.WARNING, logger="hive._commit_queue"):
        # Just past the ceiling rather than comfortably past it: with `%.1f`
        # this value rendered as "tick_s=300.0 max_tick_s=300.0", a warning
        # that reads as though the ceiling had not been crossed. A `+1.0`
        # probe cannot see that, which is why the margin is 0.04.
        disabled = CommitReconciler(git_vault, tick_s=_MAX_AUTO_TICK_S + 0.04)
    try:
        assert disabled._thread is None
        warnings = [
            rec.getMessage() for rec in caplog.records if "auto_flush_disabled" in rec.message
        ]
        assert warnings, [r.message for r in caplog.records]
        # The rendered values, not just the event name: a lossy format would
        # report the configured tick as equal to the ceiling it exceeded.
        assert "tick_s=300.04" in warnings[0], warnings[0]
        assert "max_tick_s=300.0" in warnings[0], warnings[0]
    finally:
        disabled.close(drain=False)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="hive._commit_queue"):
        enabled = CommitReconciler(git_vault, tick_s=_MAX_AUTO_TICK_S)
    try:
        assert enabled._thread is not None
        assert not any("auto_flush_disabled" in rec.message for rec in caplog.records)
    finally:
        enabled.close(drain=False)
