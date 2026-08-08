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
import threading
from typing import TYPE_CHECKING

from hive._helpers import _GIT_REGISTRY_CV, _git_commit

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path

_log = logging.getLogger(__name__)


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
