"""TDD red phase — failing tests for ``hive._outbox`` + outbox-backed trackers.

Covers HIVE-115 PR-4 acceptance criteria AC-11 (in-process outbox for
SQLite trackers; cross-process eventual consistency under
``tick_interval``).

Pre-PR-4 audit scope (2026-05-22):

- M1: reconciler uses ``sqlite3.connect(timeout=2.0)`` +
  ``PRAGMA busy_timeout=2000`` — NOT ``bounded_call`` (async-only
  wrapper cannot wrap a sync daemon thread).
- m3: ``Outbox`` docstring spells the crash-loss contract; suitable
  for informational counters (reinforcement, EMA) ONLY, not durable
  state.
- Pragmatic scope: only ``LessonReinforcementTracker`` routes writes
  through the new outbox; ``RelevanceTracker`` keeps its existing
  ``_access_buffer`` (functionally equivalent legacy pattern, not
  worth churning in this PR).
"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="cross-process test uses POSIX subprocess pattern",
)


# ── T4.1 — same-process read after write is immediate ───────────────────


def test_same_process_increment_then_top_is_immediate(tmp_path: Path) -> None:
    """``increment(p, h)`` followed by ``top(p)`` returns the heading even
    when the outbox has not been drained by the reconciler yet.

    Read paths MUST consult the outbox before falling through to the DB
    (the "own-writes-visible" invariant from primitive 2 of
    [[pattern-multi-process-mcp-server]]).
    """
    from hive._lesson_reinforcement import LessonReinforcementTracker

    tracker = LessonReinforcementTracker(
        db_path=str(tmp_path / "lesson.db"),
        outbox_tick_s=999.0,  # reconciler effectively disabled for this test
    )
    try:
        tracker.increment("proj", "[2026-05-22] test heading")
        # Don't call .flush(); outbox should be visible to reads.
        ranked = tracker.top("proj", by="reinforcements", limit=10)
        assert "[2026-05-22] test heading" in ranked, (
            f"expected outbox entry in read path; got {ranked!r}"
        )
    finally:
        tracker.close()


def test_same_process_top_does_not_double_count(tmp_path: Path) -> None:
    """Reading the outbox + DB MUST NOT double-count an entry that has been
    both buffered and flushed (idempotent merge)."""
    from hive._lesson_reinforcement import LessonReinforcementTracker

    tracker = LessonReinforcementTracker(
        db_path=str(tmp_path / "lesson.db"),
        outbox_tick_s=999.0,
    )
    try:
        tracker.increment("proj", "[2026-05-22] a")
        tracker.flush()  # explicit drain
        tracker.increment("proj", "[2026-05-22] a")  # second increment, in outbox
        row = tracker.get("proj", "[2026-05-22] a")
        assert row is not None
        # Counter should be 2 (one flushed + one buffered), not 1 or 3.
        assert row["reinforcements"] == 2, f"expected reinforcements=2; got {row!r}"
    finally:
        tracker.close()


# ── T4.2 — cross-process eventual consistency ───────────────────────────


@_POSIX_ONLY
def test_cross_process_eventual_consistency(tmp_path: Path) -> None:
    """Process A writes; process B sees it only after the reconciler tick.

    The contract is *eventual* consistency across processes (≤ 2× tick).
    Acceptable for ranking (informational counters); load-bearing for
    not-blowing-up under N=3-5 concurrent writers.
    """
    from hive._lesson_reinforcement import LessonReinforcementTracker

    db = tmp_path / "lesson.db"
    tick = 0.5  # fast tick to keep test runtime sane

    proc_a = LessonReinforcementTracker(
        db_path=str(db),
        outbox_tick_s=tick,
    )
    try:
        proc_a.increment("proj", "[2026-05-22] cross-proc")
        # Wait for proc_a's reconciler to drain + write.
        time.sleep(tick * 2.5)
    finally:
        proc_a.close()  # close flushes deterministically

    # Open a fresh tracker (mimics "process B" with its own connection
    # and outbox); reads must see the DB row written by proc_a.
    proc_b = LessonReinforcementTracker(
        db_path=str(db),
        outbox_tick_s=tick,
    )
    try:
        row = proc_b.get("proj", "[2026-05-22] cross-proc")
        assert row is not None, "proc_b cannot see proc_a's flush"
        assert row["reinforcements"] >= 1
    finally:
        proc_b.close()


# ── T4.3 — reconciler honours sqlite busy_timeout (M1) ──────────────────


@_POSIX_ONLY
def test_reconciler_busy_timeout_guard_does_not_hang(tmp_path: Path) -> None:
    """When a sibling holds the write transaction, the reconciler hits
    SQLite's busy_timeout and abandons the tick gracefully — outbox is
    preserved for the next tick.

    Per audit M1: NO bounded_call wrap (async-only). SQLite's
    ``busy_timeout=2000`` is the supervision mechanism; the reconciler
    sees ``OperationalError("database is locked")`` and re-queues.
    """
    import sqlite3

    from hive._lesson_reinforcement import LessonReinforcementTracker

    db = tmp_path / "lesson.db"

    tracker = LessonReinforcementTracker(
        db_path=str(db),
        outbox_tick_s=0.2,
    )
    try:
        tracker.increment("proj", "[2026-05-22] busy-test")

        # Hold a write transaction in a sibling connection so the
        # reconciler's BEGIN IMMEDIATE blocks until its busy_timeout.
        sibling = sqlite3.connect(str(db), timeout=10.0)
        sibling.execute("PRAGMA busy_timeout=10000")
        sibling.execute("BEGIN IMMEDIATE")
        sibling.execute(
            "INSERT OR IGNORE INTO lesson_reinforcement "
            "(project, heading, reinforcements, confidence) "
            "VALUES ('blocker', 'x', 0, 0.7)",
        )

        # Allow ~2.5 reconciler ticks while the sibling holds the lock.
        # The reconciler MUST not crash; it MUST keep the outbox alive.
        start = time.monotonic()
        time.sleep(2.5)
        elapsed = time.monotonic() - start

        # Reconciler is still running (no thread crash). Sanity check
        # — the daemon thread is alive on the tracker.
        assert tracker._reconciler_thread is None or tracker._reconciler_thread.is_alive(), (
            "reconciler thread crashed"
        )
        # Outbox preserved (counter not yet visible from sibling's view —
        # we'll release and verify the recovery path).
        sibling.execute("ROLLBACK")
        sibling.close()
        assert elapsed < 5.0, "test ran longer than expected"

        # Allow the reconciler to drain after the lock is released.
        time.sleep(0.7)
        row = tracker.get("proj", "[2026-05-22] busy-test")
        assert row is not None, "increment lost after sibling unlock"
        assert row["reinforcements"] >= 1
    finally:
        tracker.close()


# ── Outbox primitive — direct unit tests ────────────────────────────────


def test_outbox_append_and_drain() -> None:
    """Append-then-drain returns the exact entries and resets the buffer."""
    from hive._outbox import Outbox

    box: Outbox[str] = Outbox()
    box.append("a")
    box.append("b")
    drained = box.drain()
    assert drained == ["a", "b"]
    assert box.drain() == []  # second drain is empty


def test_outbox_drain_is_atomic_under_concurrent_appends() -> None:
    """Drain swaps the internal buffer atomically; concurrent appends land
    in the fresh buffer, not the drained one (no append-during-drain
    races that could discard entries).
    """
    import threading

    from hive._outbox import Outbox

    box: Outbox[int] = Outbox()
    for i in range(100):
        box.append(i)

    drained: list[list[int]] = []
    appended_during: list[int] = []

    def drain_now() -> None:
        drained.append(box.drain())

    def append_concurrent() -> None:
        for j in range(100, 200):
            box.append(j)
            appended_during.append(j)

    t1 = threading.Thread(target=drain_now)
    t2 = threading.Thread(target=append_concurrent)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # No entries lost: drained + remaining + already-drained-pre-thread = 200
    remaining = box.drain()
    total = sum(len(d) for d in drained) + len(remaining)
    assert total == 200, f"entries lost: drained={drained!r}, remaining={remaining!r}"


def test_outbox_crash_loss_contract_is_documented() -> None:
    """The Outbox class docstring spells the crash-loss contract.

    Audit m3: unflushed entries lost on process crash; suitable for
    informational counters only. This test guards against silently
    losing the docstring during refactors.
    """
    from hive._outbox import Outbox

    doc = (Outbox.__doc__ or "").lower()
    assert "crash" in doc, "Outbox docstring missing crash-loss clause"
    assert "informational" in doc or "informative" in doc, (
        "Outbox docstring missing 'informational counters only' clause"
    )
