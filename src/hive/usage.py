"""UsageTracker — SQLite-backed vault tool call logging for session profiling."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

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


class UsageTracker:
    """Track vault tool calls for session profiling and analytics."""

    def __init__(self, db_path: str = ":memory:") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        if db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def log_call(
        self,
        tool: str,
        project: str = "",
        response_lines: int = 0,
    ) -> None:
        """Record a vault tool call."""
        self._conn.execute(
            "INSERT INTO tool_calls (tool, project, response_lines) VALUES (?, ?, ?)",
            (tool, project, response_lines),
        )
        self._conn.commit()

    def stats(self, since_days: int = 30) -> dict[str, Any]:
        """Aggregate usage stats for the last N days."""
        since_clause = "WHERE date >= date('now', ? || ' days')"
        since_param = str(-since_days)

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
