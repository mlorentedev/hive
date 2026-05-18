"""RelevanceTracker — EMA-based section relevance scoring for adaptive context curation."""

from __future__ import annotations

import random

from hive._sqlite_tracker import _SqliteTracker

_DEFAULT_ALPHA = 0.3
_DECAY_FACTOR = 0.9
_PRUNE_THRESHOLD = 0.001
_WRITE_MULTIPLIER = 2.0
_DEFAULT_EPSILON = 0.15
_DEFAULT_DECAY_MIN_INTERVAL_S = 60
_ACCESS_BUFFER_MAX = 50


class RelevanceTracker(_SqliteTracker):
    """Track per-section relevance using Exponential Moving Average."""

    _SCHEMA = """\
CREATE TABLE IF NOT EXISTS section_scores (
    project TEXT NOT NULL,
    section TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0.0,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (project, section)
);
CREATE TABLE IF NOT EXISTS relevance_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

    def __init__(
        self,
        db_path: str = ":memory:",
        alpha: float = _DEFAULT_ALPHA,
        decay_factor: float = _DECAY_FACTOR,
        epsilon: float = _DEFAULT_EPSILON,
        decay_min_interval_seconds: int = _DEFAULT_DECAY_MIN_INTERVAL_S,
    ) -> None:
        super().__init__(db_path)
        self._alpha = alpha
        self._decay_factor = decay_factor
        self._epsilon = epsilon
        self._decay_min_interval = decay_min_interval_seconds
        # Buffer of pending (project, section, signal) tuples; flushed
        # in batches via executemany to keep the hot path off SQLite.
        self._access_buffer: list[tuple[str, str, float]] = []

    def record_access(
        self, project: str, section: str, *, is_write: bool = False,
    ) -> None:
        """Record a section access, updating EMA score.

        Write operations (vault_write) get a boosted signal.

        Buffered: appends to an in-memory list and flushes in batches
        via executemany. Reads (``get_scores``, ``top_sections``,
        ``apply_decay``) flush first. On a hard crash up to
        ``_ACCESS_BUFFER_MAX`` access events may be lost — acceptable
        for relevance scoring since next-session access re-records.
        """
        signal = self._alpha * (_WRITE_MULTIPLIER if is_write else 1.0)
        with self._lock:
            self._access_buffer.append((project, section, signal))
            if len(self._access_buffer) >= _ACCESS_BUFFER_MAX:
                self._flush_access_locked()

    def _flush_access_locked(self) -> None:
        """Drain the access buffer with one executemany. Caller holds self._lock."""
        if not self._access_buffer:
            return
        batch = [
            (project, section, signal, self._alpha)
            for project, section, signal in self._access_buffer
        ]
        self._access_buffer = []
        self._conn.executemany(
            "INSERT INTO section_scores "
            "(project, section, score, access_count) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(project, section) DO UPDATE SET "
            "score = excluded.score + (1 - ?) * section_scores.score, "
            "access_count = section_scores.access_count + 1, "
            "last_accessed = datetime('now')",
            batch,
        )
        self._conn.commit()

    def flush(self) -> None:
        """Force a flush of the in-memory access buffer."""
        with self._lock:
            self._flush_access_locked()

    def close(self) -> None:
        """Flush the buffer, then close the underlying connection."""
        with self._lock:
            self._flush_access_locked()
        super().close()

    def apply_decay(self) -> None:
        """Apply decay factor to all scores. No-op if last decay was recent.

        Gated by ``last_decay_at`` in ``relevance_meta`` so concurrent
        ``session_briefing`` calls from multiple hive processes do not
        compound the decay: without this, N briefings within a minute
        would multiply scores by ``decay_factor ^ N`` and corrupt the
        EMA model. The first caller within each interval wins; the
        others see the gate fail and skip.

        Implemented as an atomic INSERT ... ON CONFLICT ... WHERE ... so
        the gate read and the "claim" write happen in one statement
        (SQLite handles the race via its row-level locking + WAL).
        """
        with self._lock:
            self._flush_access_locked()
            cursor = self._conn.execute(
                "INSERT INTO relevance_meta (key, value) "
                "VALUES ('last_decay_at', datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value = datetime('now') "
                "WHERE (julianday('now') - julianday(value)) * 86400 >= ?",
                (self._decay_min_interval,),
            )
            if cursor.rowcount == 0:
                self._conn.commit()
                return
            self._conn.execute(
                "UPDATE section_scores SET score = score * ?",
                (self._decay_factor,),
            )
            self._conn.execute(
                "DELETE FROM section_scores WHERE score < ?",
                (_PRUNE_THRESHOLD,),
            )
            self._conn.commit()

    def _top_sections(self, project: str, n: int = 5) -> list[str]:
        """Internal — caller MUST hold self._lock and have flushed buffer."""
        rows = self._conn.execute(
            "SELECT section FROM section_scores "
            "WHERE project = ? ORDER BY score DESC LIMIT ?",
            (project, n),
        ).fetchall()
        return [row[0] for row in rows]

    def top_sections(self, project: str, n: int = 5) -> list[str]:
        """Return top-N sections by score for a project."""
        with self._lock:
            self._flush_access_locked()
            return self._top_sections(project, n)

    def top_sections_with_exploration(
        self,
        project: str,
        n: int = 5,
        recent_sections: list[str] | None = None,
        epsilon: float | None = None,
    ) -> list[str]:
        """Top-N sections with epsilon-greedy exploration from recent vault changes."""
        eps = epsilon if epsilon is not None else self._epsilon
        with self._lock:
            self._flush_access_locked()
            top = self._top_sections(project, n)
        if not recent_sections or eps <= 0:
            return top

        explore_slots = max(1, int(n * eps))
        candidates = [s for s in recent_sections if s not in top]
        if not candidates:
            return top

        explored = random.sample(candidates, min(explore_slots, len(candidates)))
        result = top[: n - len(explored)] + explored
        return result[:n]

    def get_scores(self, project: str) -> dict[str, float]:
        """Get all section scores for a project, sorted by score descending."""
        with self._lock:
            self._flush_access_locked()
            rows = self._conn.execute(
                "SELECT section, score FROM section_scores "
                "WHERE project = ? ORDER BY score DESC",
                (project,),
            ).fetchall()
        return {section: score for section, score in rows}
