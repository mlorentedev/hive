"""BudgetTracker — SQLite-backed monthly budget tracking for worker requests."""

from __future__ import annotations

from typing import Any

from hive._sqlite_tracker import _SqliteTracker

_MONTH_CLAUSE = "WHERE month = strftime('%Y-%m', 'now')"


class BudgetTracker(_SqliteTracker):
    """Track worker request costs against a monthly budget cap."""

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

    def record_request(
        self,
        model: str,
        cost_usd: float,
        tokens: int,
        latency_ms: int,
        task_type: str = "general",
    ) -> None:
        """Insert a completed request into the tracking table."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO requests (model, cost_usd, tokens, latency_ms, task_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (model, cost_usd, tokens, latency_ms, task_type),
            )
            self._conn.commit()

    def _month_spent(self) -> float:
        """Internal — caller MUST hold self._lock."""
        row = self._conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0.0) FROM requests {_MONTH_CLAUSE}"
        ).fetchone()
        return float(row[0]) if row else 0.0

    def month_spent(self) -> float:
        """Total USD spent in the current month."""
        with self._lock:
            return self._month_spent()

    def month_remaining(self, budget: float) -> float:
        """How much budget remains this month."""
        with self._lock:
            return budget - self._month_spent()

    def can_spend(self, budget: float, amount: float) -> bool:
        """Check if spending `amount` would stay within budget."""
        with self._lock:
            return (budget - self._month_spent()) >= amount

    def month_stats(self, budget: float) -> dict[str, Any]:
        """Aggregate stats for the current month."""
        with self._lock:
            spent = self._month_spent()
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
