"""In-process outbox primitive — thread-safe append + atomic drain.

Implements the buffer half of primitive 6 in
[[pattern-multi-process-mcp-server]]: per-process buffering of
informational mutations (reinforcement counters, EMA samples), drained
in batches by a reconciler thread, optionally backed by SQLite ``BEGIN
IMMEDIATE`` + ``PRAGMA wal_checkpoint(PASSIVE)``.

Crash-loss contract (audit m3): unflushed entries are lost on a
process crash (kill -9, OOM, hard reboot). Suitable for *informational*
counters only — reinforcement counts, EMA scores, ranking signals. Do
NOT use for durable state (audit logs, transactional commits, monetary
ledgers). SQLite WAL durability is bypassed for buffered writes; the
trade-off is amortized commit cost across a tick.
"""

from __future__ import annotations

import threading


class Outbox[T]:
    """Thread-safe in-process buffer of pending mutations.

    Crash-loss contract: unflushed entries lost on process crash.
    Suitable for **informational counters only** (reinforcement, EMA,
    rankings) — not durable state. SQLite WAL durability is bypassed for
    buffered writes; the contract trades crash-safety for amortized
    commit cost.

    Two operations:

    - :py:meth:`append` — lock-protected push of one entry.
    - :py:meth:`drain` — atomic swap of the internal list for a fresh
      empty one; returns the previous contents. Concurrent ``append``
      calls during a drain land in the fresh buffer, never in the
      drained snapshot — no entries lost to races.

    Subclasses can specialize ``T`` to a concrete tuple shape (e.g.
    ``(project, heading, signal)``) for their reconciler's
    ``executemany`` payload.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer: list[T] = []

    def append(self, entry: T) -> None:
        """Push one entry. Thread-safe."""
        with self._lock:
            self._buffer.append(entry)

    def extend(self, entries: list[T]) -> None:
        """Push a batch of entries. Used by callers that already hold a
        local list (e.g. cross-tick replay)."""
        if not entries:
            return
        with self._lock:
            self._buffer.extend(entries)

    def drain(self) -> list[T]:
        """Atomically swap the internal buffer for a fresh empty list,
        returning the previous contents.

        Concurrent ``append`` calls during the swap land in the new
        buffer (not the drained snapshot), so no entry is lost to a race
        between drain and append.
        """
        with self._lock:
            drained = self._buffer
            self._buffer = []
            return drained

    def peek(self) -> list[T]:
        """Snapshot the current buffer without draining.

        Used by read paths that need to merge "buffered but not yet
        flushed" with the canonical DB state (the "own-writes-visible"
        invariant of primitive 6).
        """
        with self._lock:
            return list(self._buffer)

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)
