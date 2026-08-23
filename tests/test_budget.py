"""Tests for BudgetTracker — SQLite-backed worker usage telemetry.

HIVE-384 retired the spend cap and kept the telemetry, so this file was trimmed
rather than deleted. What went: ``TestBudgetGuards`` in full, and the cost
assertions inside the record/aggregate tests — they covered ``can_spend`` /
``month_remaining`` / ``month_spent``, which exist to keep a pay-per-token
provider inside a dollar budget and have no subject on a flat subscription.

What deliberately stayed: recording, the month-scoped aggregate, WAL mode, file
DB creation and thread safety. None of those belong to the cap, and deleting the
module wholesale would have dropped concurrency coverage of a tracker that is
still written on every worker call.
"""

from __future__ import annotations

import threading

import pytest

from hive.budget import BudgetTracker


@pytest.fixture
def tracker() -> BudgetTracker:
    """In-memory budget tracker for tests."""
    return BudgetTracker(db_path=":memory:")


class TestRecordAndAggregate:
    """Recording requests and querying the monthly aggregate."""

    def test_record_single_request(self, tracker: BudgetTracker) -> None:
        tracker.record_request(
            model="deepseek-v4-flash",
            tokens=100,
            latency_ms=500,
            task_type="code_review",
        )
        usage = tracker.month_usage()
        assert usage["request_count"] == 1
        assert usage["total_tokens"] == 100

    def test_record_multiple_requests_sums_tokens(self, tracker: BudgetTracker) -> None:
        tracker.record_request("model-a", tokens=200, latency_ms=300)
        tracker.record_request("model-b", tokens=500, latency_ms=600)
        usage = tracker.month_usage()
        assert usage["request_count"] == 2
        assert usage["total_tokens"] == 700

    def test_empty_db_returns_zero(self, tracker: BudgetTracker) -> None:
        usage = tracker.month_usage()
        assert usage["request_count"] == 0
        assert usage["total_tokens"] == 0
        assert usage["by_model"] == {}


class TestMonthUsage:
    """Monthly usage aggregation."""

    def test_usage_with_requests(self, tracker: BudgetTracker) -> None:
        tracker.record_request("model-a", tokens=200, latency_ms=300, task_type="code")
        tracker.record_request("model-a", tokens=300, latency_ms=500, task_type="code")
        tracker.record_request("model-b", tokens=100, latency_ms=200)

        usage = tracker.month_usage()
        assert usage["request_count"] == 3
        assert usage["total_tokens"] == 600
        assert usage["by_model"]["model-a"]["count"] == 2
        assert usage["by_model"]["model-a"]["tokens"] == 500
        assert usage["by_model"]["model-a"]["avg_latency_ms"] == 400
        assert usage["by_model"]["model-b"]["count"] == 1

    def test_usage_only_current_month(self, tracker: BudgetTracker) -> None:
        """Requests from other months must not appear in the aggregate."""
        tracker.record_request("m", tokens=100, latency_ms=100)
        # Manually insert a row with a different month
        tracker._conn.execute(
            "INSERT INTO requests (month, model, cost_usd, tokens, latency_ms, task_type) "
            "VALUES ('2020-01', 'old-model', 99.0, 4242, 100, 'general')"
        )
        tracker._conn.commit()

        usage = tracker.month_usage()
        assert usage["request_count"] == 1
        assert usage["total_tokens"] == 100
        assert "old-model" not in usage["by_model"]


class TestWALMode:
    """SQLite WAL mode is enabled for file-backed databases."""

    def test_wal_mode_enabled_on_file_db(self, tmp_path: object) -> None:
        from pathlib import Path

        db_file = Path(str(tmp_path)) / "wal_test.db"
        file_tracker = BudgetTracker(db_path=str(db_file))
        result = file_tracker._conn.execute("PRAGMA journal_mode").fetchone()
        assert result is not None
        assert result[0] == "wal"

    def test_memory_db_skips_wal(self, tracker: BudgetTracker) -> None:
        result = tracker._conn.execute("PRAGMA journal_mode").fetchone()
        assert result is not None
        assert result[0] == "memory"


class TestFileDB:
    """Database persistence to file."""

    def test_creates_parent_dirs(self, tmp_path: object) -> None:
        from pathlib import Path

        db_file = Path(str(tmp_path)) / "sub" / "dir" / "test.db"
        tracker = BudgetTracker(db_path=str(db_file))
        tracker.record_request("m", tokens=10, latency_ms=50, task_type="test")
        assert db_file.exists()

        # Verify data persists across instances
        tracker2 = BudgetTracker(db_path=str(db_file))
        assert tracker2.month_usage()["total_tokens"] == 10


class TestThreadSafety:
    """Concurrent access must not raise SQLITE_MISUSE."""

    def test_concurrent_record_request(self, tmp_path: object) -> None:
        from pathlib import Path

        db = Path(str(tmp_path)) / "concurrent.db"
        tracker = BudgetTracker(db_path=str(db))
        errors: list[Exception] = []
        n_threads = 8
        n_ops = 50

        def worker() -> None:
            try:
                for i in range(n_ops):
                    tracker.record_request(
                        f"model-{i}",
                        tokens=10,
                        latency_ms=10,
                        task_type="test",
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert tracker.month_usage()["request_count"] == n_threads * n_ops

    def test_concurrent_record_and_read(self, tmp_path: object) -> None:
        from pathlib import Path

        db = Path(str(tmp_path)) / "mixed.db"
        tracker = BudgetTracker(db_path=str(db))
        errors: list[Exception] = []

        def writer() -> None:
            try:
                for _ in range(50):
                    tracker.record_request(
                        "m",
                        tokens=10,
                        latency_ms=10,
                        task_type="test",
                    )
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(50):
                    tracker.month_usage()
            except Exception as exc:
                errors.append(exc)

        threads = [
            *[threading.Thread(target=writer) for _ in range(4)],
            *[threading.Thread(target=reader) for _ in range(4)],
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
