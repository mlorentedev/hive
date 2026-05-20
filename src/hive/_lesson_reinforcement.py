"""LessonReinforcementTracker — reinforcement counter + confidence decay per lesson.

One row per ``(project, heading)`` in SQLite. Each "read" of a lesson via
``vault_query`` / ``vault_search`` / ``capture_lesson(find=...)`` increments
the counter and grows confidence asymptotically toward 1.0.

Decay formula (positive reinforcement, no time-decay):
    c_{n+1} = c_n + 0.1 * (1.0 - c_n)     with ceiling at 1.0
Closed form from baseline c_0 = 0.7:
    c_n = 1 - 0.3 * 0.9^n
"""

from __future__ import annotations

from hive._sqlite_tracker import _SqliteTracker

_DEFAULT_CONFIDENCE = 0.7
_GROWTH_RATE = 0.1
_HYBRID_ALPHA = 0.7  # BM25 weight; (1 - alpha) goes to confidence


class LessonReinforcementTracker(_SqliteTracker):
    """Track lesson usage with reinforcement counter + decay-grown confidence."""

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

    def ensure(
        self,
        project: str,
        heading: str,
        confidence: float = _DEFAULT_CONFIDENCE,
    ) -> None:
        """Seed a baseline row. Non-destructive: existing rows untouched."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO lesson_reinforcement "
                "(project, heading, reinforcements, confidence, first_seen) "
                "VALUES (?, ?, 0, ?, date('now'))",
                (project, heading, confidence),
            )
            self._conn.commit()

    def increment(self, project: str, heading: str) -> None:
        """Atomic counter + confidence bump. Lazy-creates the row if missing.

        The arithmetic lives in SQL so the read-modify-write of confidence
        is a single statement — safe under thread AND cross-process
        contention (SQLite serializes writers via WAL + busy_timeout
        inherited from ``_SqliteTracker``).
        """
        bumped = _DEFAULT_CONFIDENCE + _GROWTH_RATE * (1.0 - _DEFAULT_CONFIDENCE)
        with self._lock:
            self._conn.execute(
                "INSERT INTO lesson_reinforcement "
                "(project, heading, reinforcements, confidence, "
                " first_seen, last_referenced) "
                "VALUES (?, ?, 1, ?, date('now'), date('now')) "
                "ON CONFLICT(project, heading) DO UPDATE SET "
                "  reinforcements = reinforcements + 1, "
                "  confidence = MIN(1.0, confidence + ? * (1.0 - confidence)), "
                "  last_referenced = date('now')",
                (project, heading, bumped, _GROWTH_RATE),
            )
            self._conn.commit()

    def get(self, project: str, heading: str) -> dict[str, object] | None:
        """Fetch a single row as a dict, or None if absent."""
        with self._lock:
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
        self, project: str, headings: list[str],
    ) -> dict[str, dict[str, object]]:
        """Batch metadata fetch for a list of headings (for hybrid ranking)."""
        if not headings:
            return {}
        placeholders = ",".join("?" * len(headings))
        with self._lock:
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
        msg = (
            f"Unknown rank_by={by!r}. Expected one of: "
            f"reinforcements, confidence, hybrid."
        )
        raise ValueError(msg)

    def _top_by_counter(self, project: str, limit: int) -> list[str]:
        with self._lock:
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
            rows = self._conn.execute(
                "SELECT heading FROM lesson_reinforcement "
                "WHERE project = ? "
                "ORDER BY confidence DESC, last_referenced DESC "
                "LIMIT ?",
                (project, limit),
            ).fetchall()
        return [row[0] for row in rows]

    def _top_by_hybrid(
        self, project: str, limit: int, bm25_scores: dict[str, float],
    ) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT heading, confidence FROM lesson_reinforcement "
                "WHERE project = ?",
                (project,),
            ).fetchall()
        scored = [
            (
                heading,
                _HYBRID_ALPHA * bm25_scores.get(heading, 0.0)
                + (1.0 - _HYBRID_ALPHA) * confidence,
            )
            for heading, confidence in rows
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [heading for heading, _ in scored[:limit]]
