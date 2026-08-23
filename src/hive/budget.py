"""BudgetTracker — SQLite-backed usage telemetry for worker requests.

HIVE-384 trimmed this module rather than deleting it, and the split is worth
stating because the two halves look alike and only one was retired.

**Retired: the spend cap.** ``can_spend`` / ``month_spent`` / ``month_remaining``
existed to keep a pay-per-token provider inside a monthly dollar budget. The
worker now runs on a flat subscription, where marginal cost is zero and the
binding constraint is *concurrency*, not spend. A dollar cap there is not a
weakened guard — it is a guard measuring the wrong quantity, and one that would
have to be reintroduced by any future paid provider rather than inherited.

**Kept: usage telemetry.** ``record_request`` and the per-month aggregate remain,
because tokens and latency are what ``worker_status``, ``/status`` and
``usage.db`` actually read. Deleting them along with the cap would have removed
observability that nothing asked to remove.

``cost_usd`` survives in the schema but not in the API: dropping the column would
be a destructive migration of a database users already hold, for a field that now
records ``0.0``. It is left in place, unread.

**The class name is now a misnomer and is knowingly left alone.** It tracks usage,
not a budget. Renaming it touches 83 call sites across ten files, which would bury
this change's substance in mechanical churn on a PR a reviewer has to read closely.
Tracked separately rather than smuggled in here.
"""

from __future__ import annotations

from typing import Any

from hive._sqlite_tracker import _SqliteTracker

_MONTH_CLAUSE = "WHERE month = strftime('%Y-%m', 'now')"


class BudgetTracker(_SqliteTracker):
    """Track worker request volume, tokens and latency by month."""

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
        tokens: int,
        latency_ms: int,
        task_type: str = "general",
        cost_usd: float = 0.0,
    ) -> None:
        """Insert a completed request into the tracking table.

        ``cost_usd`` keeps its column so an existing ``worker.db`` opens
        unchanged, but it defaults to zero and no longer has a caller: on a flat
        subscription there is no per-request cost to record.
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO requests (model, cost_usd, tokens, latency_ms, task_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (model, cost_usd, tokens, latency_ms, task_type),
            )
            self._conn.commit()

    def month_usage(self) -> dict[str, Any]:
        """Aggregate usage for the current month.

        Reports volume rather than spend: request count, total tokens, and a
        per-model breakdown with average latency. This is the shape ``/status``
        and ``worker_status`` consume now that a dollar figure would always
        read zero.
        """
        with self._lock:
            totals = self._conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(tokens), 0) FROM requests {_MONTH_CLAUSE}"
            ).fetchone()
            request_count = totals[0] if totals else 0
            total_tokens = int(totals[1]) if totals else 0

            by_model: dict[str, dict[str, Any]] = {}
            rows = self._conn.execute(
                "SELECT model, COUNT(*), COALESCE(SUM(tokens), 0), AVG(latency_ms) "
                f"FROM requests {_MONTH_CLAUSE} GROUP BY model"
            ).fetchall()
            for model, cnt, tokens, avg_latency in rows:
                by_model[model] = {
                    "count": cnt,
                    "tokens": int(tokens),
                    "avg_latency_ms": int(avg_latency or 0),
                }

        return {
            "request_count": request_count,
            "total_tokens": total_tokens,
            "by_model": by_model,
        }
