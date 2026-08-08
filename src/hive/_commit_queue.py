"""Deferred-commit primitives for the ADR-018 reconciler.

Sibling of :py:mod:`hive._outbox`, deliberately not a subclass of it.
``Outbox[T]``'s contract says "do NOT use for durable state
(... transactional commits ...)" and that sentence stays absolute —
carving an exception into a contract that says *never* would make it
advisory, and a later reader could reasonably conclude the outbox is
safe for durable state generally (ADR-018 §1).

The contract here is narrower than the one it declines to inherit: an
unflushed path is a **delayed commit, not lost data**, because the file
write lands on disk *before* its path is queued. That ordering is what
makes every weaker guarantee below honest.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING

from hive._helpers import _GIT_REGISTRY_CV, _git_commit

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path

_log = logging.getLogger(__name__)

_DEFAULT_COMMIT_TICK_S = 5.0
_DEFAULT_FLUSH_DEADLINE_S = 30.0
# Above this the loop is assumed to be deliberately disabled (tests pass a
# large tick to drive flushes explicitly), mirroring the guard the lesson
# reconciler uses for the same purpose.
_MAX_AUTO_TICK_S = 300.0


def _env_float(name: str, default: float) -> float:
    """Read a positive float from the environment, falling back on anything odd.

    Mirrors ``_lesson_reinforcement._default_outbox_tick_s``: a malformed or
    non-positive value is a misconfiguration, and defaulting beats refusing
    to start the server over it.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def flush_paths(
    vault_path: Path,
    rel_paths: list[Path],
    message: str,
    *,
    deadline_s: float,
) -> bool:
    """Commit ``rel_paths`` under a synchronous deadline. Never raises.

    Returns ``True`` when a commit was created. A ``False`` return means
    the caller must **drop** those paths rather than re-queue them: the
    files are already on disk, so a dropped path is a file awaiting a
    commit rather than a missing edit, and it surfaces through the
    uncommitted-path report instead (ADR-018 §1, AC14).

    The deadline is enforced by a :py:class:`threading.Timer` over a
    registry-scoped kill, not by ``bounded_call``. ``bounded_call`` is
    ``async def``, so reusing it would mean standing up an event loop
    inside a daemon thread purely to call it; the *termination* half is
    shared instead, which is where the cross-OS risk lives (ADR-018 §2).

    The timer wraps the whole of :py:func:`_git_commit`, so a deadline
    landing between its two Popens (``git add`` then ``git commit``)
    is covered — that window is the one HIVE-115 AC-9b documented.
    """
    if not rel_paths:
        return False

    from hive._deadline import _cleanup_index_lock, terminate_registry_sync

    registry: list[subprocess.Popen[bytes]] = []
    # A fresh Thread starts with its own context, so setting the ContextVar
    # here cannot leak into other threads. This is the seam _run_git
    # documents: caller-supplied list, else _GIT_REGISTRY_CV.
    token = _GIT_REGISTRY_CV.set(registry)
    killed: list[int] = []
    fired = threading.Event()

    def _on_deadline() -> None:
        fired.set()
        try:
            killed.extend(terminate_registry_sync(registry))
        except Exception as exc:  # noqa: BLE001
            # A watchdog that raises would kill the reconciler thread and
            # stop every future flush — strictly worse than a missed kill.
            _log.debug("flush watchdog termination failed: %s", exc)

    timer = threading.Timer(deadline_s, _on_deadline)
    timer.daemon = True
    timer.start()
    try:
        committed = _git_commit(vault_path, rel_paths, message)
    except Exception as exc:  # noqa: BLE001
        # _git_commit is best-effort and swallows its own failures; this
        # guards the reconciler thread against anything it does not.
        _log.warning("mcp.reconciler.flush_error paths=%d err=%s", len(rel_paths), exc)
        committed = False
    finally:
        timer.cancel()
        _GIT_REGISTRY_CV.reset(token)

    if fired.is_set():
        # Clearing the index.lock is PID-guarded: only a lock whose owner
        # is one of the processes we just killed is removed, so a foreign
        # committer's lock is never touched (ADR-008 §4).
        if killed:
            _cleanup_index_lock(vault_path, killed)
        _log.warning(
            "mcp.reconciler.flush_deadline_exceeded deadline_s=%.1f dropped=%d killed_pids=%s",
            deadline_s,
            len(rel_paths),
            killed,
        )
        return False

    if not committed:
        _log.warning("mcp.reconciler.flush_failed dropped=%d", len(rel_paths))
    return committed


class CommitQueue:
    """Thread-safe set of vault paths awaiting a deferred commit.

    Crash-loss contract, stated in full rather than inherited: unflushed
    paths are lost when the process dies (kill -9, OOM, hard reboot). That
    is acceptable *here and only here* because the file content already
    reached disk before its path was queued, so what is lost is the
    knowledge that a commit is owed — never an edit. Recovery is a report,
    not a replay (ADR-018 §3): an orphaned path waits for the next write to
    that vault, an explicit ``vault_commit``, or an external committer, and
    stays visible in ``vault_health`` throughout.

    Deduplication happens at :py:meth:`add`, not at :py:meth:`drain`. A
    path already pending is a no-op, so the queue holds one entry per file
    and its length is an honest backlog depth — draining a collapsed pair
    would report two pending commits for one file.

    Backed by a ``dict`` keyed on path: insertion order is preserved, so a
    commit stages files in the order they were written.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[Path, None] = {}

    def add(self, rel_path: Path) -> None:
        """Queue one vault-relative path. Repeats are no-ops."""
        with self._lock:
            self._pending[rel_path] = None

    def drain(self) -> list[Path]:
        """Atomically swap the pending set for a fresh one and return it.

        Concurrent :py:meth:`add` calls during the swap land in the new
        mapping, never in the drained snapshot.
        """
        with self._lock:
            drained = list(self._pending)
            self._pending = {}
            return drained

    def peek(self) -> list[Path]:
        """Snapshot the pending paths without draining them."""
        with self._lock:
            return list(self._pending)

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)


class CommitReconciler:
    """Drains a :class:`CommitQueue` into one git commit per tick.

    Runs in **every** hive process, daemon or not. The cross-process
    coordination this relies on already exists and is load-bearing:
    ``_git_filelock(vault)`` is acquired by every write path, and the queue
    changes commit *frequency*, not the concurrency model (ADR-018
    §Decision). With P processes the bound is P commits per tick, which is
    the point a single daemon-owned queue improves on.

    A failed flush **drops** its paths; it never re-queues them. The retry
    loop that re-queueing implies is what "report, never self-heal" rejects,
    and it degrades worst in exactly the conditions that cause a failed
    flush — a rejecting hook, a full disk, a foreign ``index.lock`` — where
    each tick would spawn another doomed git against a queue that only
    grows (ADR-018 §1, AC14).
    """

    def __init__(
        self,
        vault_path: Path,
        *,
        tick_s: float | None = None,
        deadline_s: float | None = None,
    ) -> None:
        self.vault_path = vault_path
        self.queue = CommitQueue()
        self._tick_s = (
            tick_s
            if tick_s is not None
            else _env_float("HIVE_COMMIT_TICK_S", _DEFAULT_COMMIT_TICK_S)
        )
        self._deadline_s = (
            deadline_s
            if deadline_s is not None
            else _env_float("HIVE_COMMIT_FLUSH_DEADLINE_S", _DEFAULT_FLUSH_DEADLINE_S)
        )
        self._stop = threading.Event()
        self._flush_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_flush_at: float | None = None
        if self._tick_s <= _MAX_AUTO_TICK_S:
            self._thread = threading.Thread(
                target=self._loop,
                name=f"hive-commit-reconciler-{os.getpid()}",
                daemon=True,
            )
            self._thread.start()

    # ── Write path ──────────────────────────────────────────────────

    def enqueue(self, path: Path) -> None:
        """Queue a written file for the next drain.

        Accepts either an absolute path or one already relative to the
        vault and normalises to the latter. Without this the dedup is
        defeated by the caller's spelling, and one file would be staged
        twice in a single commit.
        """
        rel = path
        if path.is_absolute():
            try:
                rel = path.relative_to(self.vault_path)
            except ValueError:
                # Outside the vault: nothing here can commit it, and
                # staging it would breach the ADR-014 path-scope invariant.
                _log.warning(
                    "mcp.reconciler.enqueue_outside_vault path=%s vault=%s",
                    path,
                    self.vault_path,
                )
                return
        self.queue.add(rel)

    # ── Drain path ──────────────────────────────────────────────────

    def flush_now(self) -> bool:
        """Drain the queue and commit whatever it held. Never raises.

        Returns ``True`` only when a commit was created — ``False`` covers
        both "nothing was queued" and "the commit failed", which are the
        same thing to a caller: no new commit exists.
        """
        with self._flush_lock:
            paths = self.queue.drain()
            if not paths:
                return False
            message = _batch_message(paths)
            committed = flush_paths(
                self.vault_path,
                paths,
                message,
                deadline_s=self._deadline_s,
            )
            self.last_flush_at = time.time()
            return committed

    def _loop(self) -> None:
        """Daemon loop: drain on every tick until stopped.

        Never raises out of the thread. An unhandled failure here would
        disable deferred committing for the life of the process, which is
        strictly worse than a skipped tick.
        """
        while not self._stop.wait(timeout=self._tick_s):
            if len(self.queue) == 0:
                continue
            try:
                self.flush_now()
            except Exception as exc:  # noqa: BLE001
                _log.warning("mcp.reconciler.tick_failed err=%s", exc)

    def close(self, *, drain: bool = True) -> None:
        """Stop the loop and, by default, drain what is still queued.

        The final drain is what keeps a clean shutdown from discarding
        queued work. A hard kill gets no drain — that path is covered by
        the uncommitted-path report, not by this method.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=self._tick_s + self._deadline_s + 5.0)
        if drain:
            try:
                self.flush_now()
            except Exception as exc:  # noqa: BLE001
                _log.warning("mcp.reconciler.shutdown_drain_failed err=%s", exc)


def _batch_message(paths: list[Path]) -> str:
    """Commit subject for one drained batch.

    Follows the ``vault: <verb> <path>`` convention of the synchronous
    write path, naming the single path when there is one so a one-file
    tick reads no worse than the commit it replaces.
    """
    if len(paths) == 1:
        return f"vault: deferred commit {paths[0].as_posix()}"
    return f"vault: deferred commit ({len(paths)} paths)"
