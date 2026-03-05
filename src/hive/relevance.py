"""RelevanceTracker — EMA-based section relevance scoring for adaptive context curation."""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

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

_DEFAULT_ALPHA = 0.3
_DECAY_FACTOR = 0.9
_PRUNE_THRESHOLD = 0.001
_WRITE_MULTIPLIER = 2.0
_DEFAULT_EPSILON = 0.15


class RelevanceTracker:
    """Track per-section relevance using Exponential Moving Average."""

    def __init__(self, db_path: str = ":memory:", alpha: float = _DEFAULT_ALPHA) -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        if db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._alpha = alpha

    def record_access(
        self, project: str, section: str, *, is_write: bool = False,
    ) -> None:
        """Record a section access, updating EMA score.

        Write operations (vault_update, vault_create) get a boosted signal.
        """
        signal = self._alpha * (_WRITE_MULTIPLIER if is_write else 1.0)
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
        self._conn.execute(
            "UPDATE section_scores SET score = score * ?", (_DECAY_FACTOR,),
        )
        self._conn.execute(
            "DELETE FROM section_scores WHERE score < ?", (_PRUNE_THRESHOLD,),
        )
        self._conn.commit()

    def top_sections(self, project: str, n: int = 5) -> list[str]:
        """Return top-N sections by score for a project."""
        rows = self._conn.execute(
            "SELECT section FROM section_scores "
            "WHERE project = ? ORDER BY score DESC LIMIT ?",
            (project, n),
        ).fetchall()
        return [row[0] for row in rows]

    def top_sections_with_exploration(
        self,
        project: str,
        n: int = 5,
        recent_sections: list[str] | None = None,
        epsilon: float = _DEFAULT_EPSILON,
    ) -> list[str]:
        """Top-N sections with epsilon-greedy exploration from recent vault changes."""
        top = self.top_sections(project, n)
        if not recent_sections or epsilon <= 0:
            return top

        explore_slots = max(1, int(n * epsilon))
        candidates = [s for s in recent_sections if s not in top]
        if not candidates:
            return top

        explored = random.sample(candidates, min(explore_slots, len(candidates)))
        result = top[: n - len(explored)] + explored
        return result[:n]

    def get_scores(self, project: str) -> dict[str, float]:
        """Get all section scores for a project, sorted by score descending."""
        rows = self._conn.execute(
            "SELECT section, score FROM section_scores "
            "WHERE project = ? ORDER BY score DESC",
            (project,),
        ).fetchall()
        return {section: score for section, score in rows}
