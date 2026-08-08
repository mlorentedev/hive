"""Tests for the deferred-commit reconciler primitives (HIVE-322 / ADR-018).

The reconciler runs in a plain daemon thread, so it cannot reuse
``bounded_call`` — that is ``async def`` and would need an event loop
stood up inside the thread purely to call it (ADR-018 §2). What it reuses
instead is the *termination* half of ``_deadline.py``, which is where the
cross-OS risk actually lives.
"""

from __future__ import annotations

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
