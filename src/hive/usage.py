"""UsageTracker — SQLite-backed vault tool call logging for session profiling."""

from __future__ import annotations

from typing import Any

from hive._sqlite_tracker import _SqliteTracker

_LOG_BUFFER_MAX = 50


class UsageTracker(_SqliteTracker):
    """Track vault tool calls for session profiling and analytics.

    ``log_call`` is on the hot path of every tool response. To avoid
    one SQLite INSERT+commit per call (which serializes across all
    threads of the process AND across all hive subprocesses sharing
    the DB), calls buffer in memory and flush in batches of
    ``_LOG_BUFFER_MAX``. Reads (``stats``) flush first so they see
    fresh data. ``close()`` flushes too. On a hard crash, up to
    ``_LOG_BUFFER_MAX`` informational rows can be lost — acceptable
    for usage analytics.
    """

    _SCHEMA = """\
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY,
    timestamp TEXT DEFAULT (datetime('now')),
    date TEXT DEFAULT (date('now')),
    tool TEXT NOT NULL,
    project TEXT DEFAULT '',
    response_lines INTEGER DEFAULT 0
);
"""

    def __init__(self, db_path: str = ":memory:") -> None:
        super().__init__(db_path)
        self._buffer: list[tuple[str, str, int]] = []

    def log_call(
        self,
        tool: str,
        project: str = "",
        response_lines: int = 0,
    ) -> None:
        """Buffer a vault tool call; flush when the buffer fills."""
        with self._lock:
            self._buffer.append((tool, project, response_lines))
            if len(self._buffer) >= _LOG_BUFFER_MAX:
                self._flush_locked()

    def _flush_locked(self) -> None:
        """Drain the in-memory buffer into SQLite. Caller MUST hold self._lock."""
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        self._conn.executemany(
            "INSERT INTO tool_calls (tool, project, response_lines) "
            "VALUES (?, ?, ?)",
            batch,
        )
        self._conn.commit()

    def flush(self) -> None:
        """Force a flush of the in-memory buffer (e.g. before shutdown)."""
        with self._lock:
            self._flush_locked()

    def close(self) -> None:
        """Flush the buffer, then close the underlying connection."""
        with self._lock:
            self._flush_locked()
        super().close()

    def stats(self, since_days: int = 30) -> dict[str, Any]:
        """Aggregate usage stats for the last N days."""
        since_clause = "WHERE date >= date('now', ? || ' days')"
        since_param = str(-since_days)

        with self._lock:
            self._flush_locked()
            total_row = self._conn.execute(
                f"SELECT COUNT(*) FROM tool_calls {since_clause}",
                (since_param,),
            ).fetchone()
            total_calls = total_row[0] if total_row else 0

            by_tool: dict[str, int] = {}
            rows = self._conn.execute(
                "SELECT tool, COUNT(*) FROM tool_calls "
                f"{since_clause} "
                "GROUP BY tool ORDER BY COUNT(*) DESC",
                (since_param,),
            ).fetchall()
            for tool, count in rows:
                by_tool[tool] = count

            by_project: dict[str, int] = {}
            rows = self._conn.execute(
                "SELECT project, COUNT(*) FROM tool_calls "
                f"{since_clause} "
                "AND project != '' "
                "GROUP BY project ORDER BY COUNT(*) DESC",
                (since_param,),
            ).fetchall()
            for project, count in rows:
                by_project[project] = count

            total_lines_row = self._conn.execute(
                "SELECT COALESCE(SUM(response_lines), 0) FROM tool_calls "
                f"{since_clause}",
                (since_param,),
            ).fetchone()
            total_lines = total_lines_row[0] if total_lines_row else 0

        return {
            "total_calls": total_calls,
            "total_response_lines": total_lines,
            "by_tool": by_tool,
            "by_project": by_project,
        }
