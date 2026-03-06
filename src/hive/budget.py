"""BudgetTracker — SQLite-backed monthly budget tracking for worker requests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY,
    timestamp TEXT DEFAULT (datetime('now')),
    month TEXT DEFAULT (strftime('%Y-%m', 'now')),
    model TEXT NOT NULL,
    cost_usd REAL NOT NULL,
    tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    task_type TEXT DEFAULT 'general'
);
"""

_MONTH_CLAUSE = "WHERE month = strftime('%Y-%m', 'now')"


class BudgetTracker:
    """Track worker request costs against a monthly budget cap."""

    def __init__(self, db_path: str = ":memory:") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(db_path)
        if db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def record_request(
        self,
        model: str,
        cost_usd: float,
        tokens: int,
        latency_ms: int,
        task_type: str = "general",
    ) -> None:
        """Insert a completed request into the tracking table."""
        self._conn.execute(
            "INSERT INTO requests (model, cost_usd, tokens, latency_ms, task_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (model, cost_usd, tokens, latency_ms, task_type),
        )
        self._conn.commit()

    def month_spent(self) -> float:
        """Total USD spent in the current month."""
        row = self._conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0.0) FROM requests {_MONTH_CLAUSE}"
        ).fetchone()
        return float(row[0]) if row else 0.0

    def month_remaining(self, budget: float) -> float:
        """How much budget remains this month."""
        return budget - self.month_spent()

    def can_spend(self, budget: float, amount: float) -> bool:
        """Check if spending `amount` would stay within budget."""
        return self.month_remaining(budget) >= amount

    def month_stats(self, budget: float) -> dict[str, Any]:
        """Aggregate stats for the current month."""
        spent = self.month_spent()
        count_row = self._conn.execute(
            f"SELECT COUNT(*) FROM requests {_MONTH_CLAUSE}"
        ).fetchone()
        request_count = count_row[0] if count_row else 0

        by_model: dict[str, dict[str, Any]] = {}
        rows = self._conn.execute(
            "SELECT model, COUNT(*), SUM(cost_usd), AVG(latency_ms) "
            f"FROM requests {_MONTH_CLAUSE} GROUP BY model"
        ).fetchall()
        for model, cnt, total_cost, avg_latency in rows:
            by_model[model] = {
                "count": cnt,
                "total_cost": total_cost,
                "avg_latency_ms": int(avg_latency),
            }

        return {
            "spent": spent,
            "remaining": budget - spent,
            "request_count": request_count,
            "by_model": by_model,
        }
