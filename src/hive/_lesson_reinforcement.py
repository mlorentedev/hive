"""LessonReinforcementTracker — reinforcement counter + confidence decay per lesson.

One row per ``(project, heading)`` in SQLite. Each "read" of a lesson via
``vault_query`` / ``vault_search`` / ``capture_lesson(find=...)`` increments
the counter and grows confidence asymptotically toward 1.0.

Decay formula (positive reinforcement, no time-decay):
    c_{n+1} = c_n + 0.1 * (1.0 - c_n)     with ceiling at 1.0
Closed form from baseline c_0 = 0.7:
    c_n = 1 - 0.3 * 0.9^n

HIVE-115 PR-4: writes route through an in-process :class:`Outbox`
drained by a daemon reconciler thread. Reads flush the outbox first so
the own-writes-visible invariant holds in the same process; a periodic
tick drains across processes (eventual consistency, informational
ranking — crash-loss is documented and acceptable).
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading

from hive._outbox import Outbox
from hive._sqlite_tracker import _SqliteTracker

_log = logging.getLogger(__name__)

_DEFAULT_CONFIDENCE = 0.7
_GROWTH_RATE = 0.1
_HYBRID_ALPHA = 0.7  # BM25 weight; (1 - alpha) goes to confidence

_DEFAULT_OUTBOX_TICK_S = 5.0
_RECONCILER_BUSY_TIMEOUT_MS = 2000  # audit M1: bounded wait on BEGIN IMMEDIATE
_RECONCILER_BACKOFFS_S: tuple[float, ...] = (1.0, 2.0, 4.0)  # audit M3 (total 7s)


def _default_outbox_tick_s() -> float:
    """Read ``HIVE_OUTBOX_TICK_S`` from env, fall back to 5s default."""
    raw = os.environ.get("HIVE_OUTBOX_TICK_S")
    if raw is None:
        return _DEFAULT_OUTBOX_TICK_S
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_OUTBOX_TICK_S
    return value if value > 0 else _DEFAULT_OUTBOX_TICK_S


class LessonReinforcementTracker(_SqliteTracker):
    """Track lesson usage with reinforcement counter + decay-grown confidence.

    Per HIVE-115 PR-4: every :py:meth:`increment` appends to an in-process
    :class:`Outbox`; a daemon reconciler thread drains the outbox into
    SQLite via ``BEGIN IMMEDIATE`` on its own connection (per audit M1 —
    avoids blocking read paths under sibling write contention).

    Reads flush the outbox synchronously so a same-process
    ``increment → top`` chain returns the recently-buffered heading.
    Cross-process consistency is eventual within ``~2 * outbox_tick_s``.

    Crash-loss contract: unflushed outbox entries lost on hard process
    crash. Acceptable because reinforcement counts are informational
    (ranking signal, not durable state). Documented on :class:`Outbox`.
    """

    _SCHEMA = """\
CREATE TABLE IF NOT EXISTS lesson_reinforcement (
    project TEXT NOT NULL,
    heading TEXT NOT NULL,
    reinforcements INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.7,
    first_seen TEXT NOT NULL DEFAULT (date('now')),
    last_referenced TEXT,
    PRIMARY KEY (project, heading)
);
"""

    def __init__(
        self,
        db_path: str = ":memory:",
        *,
        outbox_tick_s: float | None = None,
        checkpoint_interval_s: float | None = None,
    ) -> None:
        super().__init__(
            db_path=db_path,
            checkpoint_interval_s=checkpoint_interval_s,
        )
        self._outbox: Outbox[tuple[str, str]] = Outbox()
        self._outbox_tick_s = (
            outbox_tick_s if outbox_tick_s is not None else _default_outbox_tick_s()
        )
        self._stop_reconciler = threading.Event()
        self._reconciler_thread: threading.Thread | None = None
        # Only start the reconciler for on-disk DBs and when ticks are
        # short enough to be meaningful (tests pass a large tick to
        # effectively disable auto-flushing).
        if db_path != ":memory:" and self._outbox_tick_s <= 300:
            self._reconciler_thread = threading.Thread(
                target=self._reconciler_loop,
                name=f"hive-lesson-reconciler-{os.getpid()}",
                daemon=True,
            )
            self._reconciler_thread.start()

    # ── Write path ──────────────────────────────────────────────────

    def ensure(
        self,
        project: str,
        heading: str,
        confidence: float = _DEFAULT_CONFIDENCE,
    ) -> None:
        """Seed a baseline row. Non-destructive: existing rows untouched.

        Goes directly to the DB (not through the outbox) because
        ``ensure`` is idempotent (``INSERT OR IGNORE``) and only fires
        once per new lesson — outbox amortization is wasted here.
        """
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO lesson_reinforcement "
                "(project, heading, reinforcements, confidence, first_seen) "
                "VALUES (?, ?, 0, ?, date('now'))",
                (project, heading, confidence),
            )
            self._conn.commit()

    def increment(self, project: str, heading: str) -> None:
        """Append an increment to the outbox; reconciler will flush.

        Reads (``get``, ``top``, ``lookup``) flush the outbox first so
        the own-writes-visible invariant holds inside the same process.
        Cross-process visibility is eventual within ``~2 * outbox_tick_s``.
        """
        self._outbox.append((project, heading))

    # ── Drain primitive ─────────────────────────────────────────────

    def _flush_locked(self, conn: sqlite3.Connection | None = None) -> None:
        """Drain the outbox and apply increments via ``executemany``.

        Caller MUST hold ``self._lock``. ``conn`` defaults to the shared
        ``self._conn``; the reconciler thread passes its own connection
        so its long-running ``BEGIN IMMEDIATE`` does not block readers
        that hold ``self._lock`` for sub-millisecond windows.
        """
        pending = self._outbox.drain()
        if not pending:
            return
        target = conn if conn is not None else self._conn
        bumped = _DEFAULT_CONFIDENCE + _GROWTH_RATE * (1.0 - _DEFAULT_CONFIDENCE)
        try:
            target.executemany(
                "INSERT INTO lesson_reinforcement "
                "(project, heading, reinforcements, confidence, "
                " first_seen, last_referenced) "
                "VALUES (?, ?, 1, ?, date('now'), date('now')) "
                "ON CONFLICT(project, heading) DO UPDATE SET "
                "  reinforcements = reinforcements + 1, "
                "  confidence = MIN(1.0, confidence + ? * (1.0 - confidence)), "
                "  last_referenced = date('now')",
                [(project, heading, bumped, _GROWTH_RATE) for project, heading in pending],
            )
            target.commit()
        except sqlite3.OperationalError as exc:
            # Lock contention or other operational issue — re-queue.
            _log.warning(
                "mcp.lesson_reconciler.flush_blocked entries=%d exc=%s",
                len(pending),
                exc,
            )
            self._outbox.extend(pending)
            raise

    def flush(self) -> None:
        """Force a synchronous flush of the outbox via the shared connection.

        ``OperationalError`` is swallowed because ``_flush_locked``
        re-queues the entries before re-raising.
        """
        with self._lock, contextlib.suppress(sqlite3.OperationalError):
            self._flush_locked()

    # ── Reconciler thread ───────────────────────────────────────────

    def _reconciler_loop(self) -> None:
        """Daemon loop that periodically drains the outbox.

        Uses its **own** SQLite connection (per audit M1) with
        ``busy_timeout=2000`` so its ``BEGIN IMMEDIATE`` is bounded and
        does NOT compete for ``self._lock`` while waiting. On
        ``OperationalError`` (typically lock contention from another
        process), backs off and re-tries the next tick — entries are
        already re-queued by :py:meth:`_flush_locked`.

        Never raises out of the thread: any unhandled exception is
        logged and the loop continues, so a transient failure cannot
        permanently disable the drain.
        """
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=_RECONCILER_BUSY_TIMEOUT_MS / 1000.0,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={_RECONCILER_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error as exc:
            _log.warning(
                "mcp.lesson_reconciler.connect_failed db=%s exc=%s",
                self._db_path,
                exc,
            )
            return

        try:
            while not self._stop_reconciler.wait(timeout=self._outbox_tick_s):
                if len(self._outbox) == 0:
                    continue
                self._drain_with_backoff(conn)
        finally:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    def _drain_with_backoff(self, conn: sqlite3.Connection) -> None:
        """One reconciler tick — drain, write, optional checkpoint, backoff."""
        backoff_sequence = (0.0, *_RECONCILER_BACKOFFS_S)
        for delay in backoff_sequence:
            if delay > 0 and self._stop_reconciler.wait(timeout=delay):
                return
            try:
                with self._lock:
                    self._flush_locked(conn=conn)
                # Best-effort WAL checkpoint after a successful drain.
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except sqlite3.Error as exc:
                    _log.debug(
                        "mcp.lesson_reconciler.checkpoint_skipped exc=%s",
                        exc,
                    )
                return
            except sqlite3.OperationalError:
                continue
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "mcp.lesson_reconciler.unhandled exc=%s",
                    exc,
                )
                return
        _log.warning(
            "mcp.lesson_reconciler.abandoned total_waited_s=%.1f",
            sum(_RECONCILER_BACKOFFS_S),
        )

    def close(self) -> None:
        """Signal the reconciler, drain the outbox one last time, close conn."""
        self._stop_reconciler.set()
        thread = self._reconciler_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=self._outbox_tick_s + 2.0)
        with self._lock:
            try:
                self._flush_locked()
            except sqlite3.OperationalError:
                _log.warning(
                    "mcp.lesson_reconciler.final_flush_blocked entries=%d",
                    len(self._outbox),
                )
        super().close()

    # ── Read paths (flush outbox first) ─────────────────────────────

    def get(self, project: str, heading: str) -> dict[str, object] | None:
        """Fetch a single row as a dict, or None if absent.

        Flushes the outbox first so a same-process
        ``increment(...) → get(...)`` chain returns the bumped row.
        """
        with self._lock:
            with contextlib.suppress(sqlite3.OperationalError):
                self._flush_locked()
            row = self._conn.execute(
                "SELECT project, heading, reinforcements, confidence, "
                "       first_seen, last_referenced "
                "FROM lesson_reinforcement WHERE project = ? AND heading = ?",
                (project, heading),
            ).fetchone()
        if row is None:
            return None
        return {
            "project": row[0],
            "heading": row[1],
            "reinforcements": row[2],
            "confidence": row[3],
            "first_seen": row[4],
            "last_referenced": row[5],
        }

    def lookup(
        self,
        project: str,
        headings: list[str],
    ) -> dict[str, dict[str, object]]:
        """Batch metadata fetch for a list of headings (for hybrid ranking)."""
        if not headings:
            return {}
        placeholders = ",".join("?" * len(headings))
        with self._lock:
            with contextlib.suppress(sqlite3.OperationalError):
                self._flush_locked()
            rows = self._conn.execute(
                f"SELECT heading, reinforcements, confidence, last_referenced "
                f"FROM lesson_reinforcement "
                f"WHERE project = ? AND heading IN ({placeholders})",
                (project, *headings),
            ).fetchall()
        return {
            row[0]: {
                "reinforcements": row[1],
                "confidence": row[2],
                "last_referenced": row[3],
            }
            for row in rows
        }

    def top(
        self,
        project: str,
        by: str = "reinforcements",
        limit: int = 10,
        bm25_scores: dict[str, float] | None = None,
    ) -> list[str]:
        """Return the top-N headings ordered by the chosen signal.

        Modes:
            - ``reinforcements``: counter DESC.
            - ``confidence``: confidence DESC, last_referenced DESC tiebreaker.
            - ``hybrid``: ``alpha*bm25 + (1-alpha)*confidence`` DESC. Lessons
              absent from ``bm25_scores`` get bm25 = 0.0.
        """
        if by == "reinforcements":
            return self._top_by_counter(project, limit)
        if by == "confidence":
            return self._top_by_confidence(project, limit)
        if by == "hybrid":
            return self._top_by_hybrid(project, limit, bm25_scores or {})
        msg = f"Unknown rank_by={by!r}. Expected one of: reinforcements, confidence, hybrid."
        raise ValueError(msg)

    def _top_by_counter(self, project: str, limit: int) -> list[str]:
        with self._lock:
            with contextlib.suppress(sqlite3.OperationalError):
                self._flush_locked()
            rows = self._conn.execute(
                "SELECT heading FROM lesson_reinforcement "
                "WHERE project = ? "
                "ORDER BY reinforcements DESC, last_referenced DESC "
                "LIMIT ?",
                (project, limit),
            ).fetchall()
        return [row[0] for row in rows]

    def _top_by_confidence(self, project: str, limit: int) -> list[str]:
        with self._lock:
            with contextlib.suppress(sqlite3.OperationalError):
                self._flush_locked()
            rows = self._conn.execute(
                "SELECT heading FROM lesson_reinforcement "
                "WHERE project = ? "
                "ORDER BY confidence DESC, last_referenced DESC "
                "LIMIT ?",
                (project, limit),
            ).fetchall()
        return [row[0] for row in rows]

    def _top_by_hybrid(
        self,
        project: str,
        limit: int,
        bm25_scores: dict[str, float],
    ) -> list[str]:
        with self._lock:
            with contextlib.suppress(sqlite3.OperationalError):
                self._flush_locked()
            rows = self._conn.execute(
                "SELECT heading, confidence FROM lesson_reinforcement WHERE project = ?",
                (project,),
            ).fetchall()
        scored = [
            (
                heading,
                _HYBRID_ALPHA * bm25_scores.get(heading, 0.0) + (1.0 - _HYBRID_ALPHA) * confidence,
            )
            for heading, confidence in rows
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [heading for heading, _ in scored[:limit]]
