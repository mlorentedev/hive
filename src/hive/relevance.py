"""RelevanceTracker — EMA-based section relevance scoring for adaptive context curation."""

from __future__ import annotations

import random

from hive._sqlite_tracker import _SqliteTracker

_DEFAULT_ALPHA = 0.3
_DECAY_FACTOR = 0.9
_PRUNE_THRESHOLD = 0.001
_WRITE_MULTIPLIER = 2.0
_DEFAULT_EPSILON = 0.15


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
"""

    def __init__(
        self,
        db_path: str = ":memory:",
        alpha: float = _DEFAULT_ALPHA,
        decay_factor: float = _DECAY_FACTOR,
        epsilon: float = _DEFAULT_EPSILON,
    ) -> None:
        super().__init__(db_path)
        self._alpha = alpha
        self._decay_factor = decay_factor
        self._epsilon = epsilon

    def record_access(
        self, project: str, section: str, *, is_write: bool = False,
    ) -> None:
        """Record a section access, updating EMA score.

        Write operations (vault_write) get a boosted signal.
        """
        signal = self._alpha * (_WRITE_MULTIPLIER if is_write else 1.0)
        with self._lock:
            row = self._conn.execute(
                "SELECT score FROM section_scores WHERE project = ? AND section = ?",
                (project, section),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO section_scores (project, section, score, access_count) "
                    "VALUES (?, ?, ?, 1)",
                    (project, section, signal),
                )
            else:
                old_score: float = row[0]
                new_score = signal + (1 - self._alpha) * old_score
                self._conn.execute(
                    "UPDATE section_scores SET score = ?, access_count = access_count + 1, "
                    "last_accessed = datetime('now') WHERE project = ? AND section = ?",
                    (new_score, project, section),
                )
            self._conn.commit()

    def apply_decay(self) -> None:
        """Apply decay factor to all scores. Prune near-zero entries."""
        with self._lock:
            self._conn.execute(
                "UPDATE section_scores SET score = score * ?", (self._decay_factor,),
            )
            self._conn.execute(
                "DELETE FROM section_scores WHERE score < ?", (_PRUNE_THRESHOLD,),
            )
            self._conn.commit()

    def _top_sections(self, project: str, n: int = 5) -> list[str]:
        """Internal — caller MUST hold self._lock."""
        rows = self._conn.execute(
            "SELECT section FROM section_scores "
            "WHERE project = ? ORDER BY score DESC LIMIT ?",
            (project, n),
        ).fetchall()
        return [row[0] for row in rows]

    def top_sections(self, project: str, n: int = 5) -> list[str]:
        """Return top-N sections by score for a project."""
        with self._lock:
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
            rows = self._conn.execute(
                "SELECT section, score FROM section_scores "
                "WHERE project = ? ORDER BY score DESC",
                (project,),
            ).fetchall()
        return {section: score for section, score in rows}
