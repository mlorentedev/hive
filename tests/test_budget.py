"""Tests for BudgetTracker — SQLite-backed budget tracking."""

from __future__ import annotations

import pytest

from hive.budget import BudgetTracker


@pytest.fixture
def tracker() -> BudgetTracker:
    """In-memory budget tracker for tests."""
    return BudgetTracker(db_path=":memory:")


class TestRecordAndSpent:
    """Recording requests and querying monthly spend."""

    def test_record_single_request(self, tracker: BudgetTracker) -> None:
        tracker.record_request(
            model="qwen/qwen3-coder:free",
            cost_usd=0.0,
            tokens=100,
            latency_ms=500,
            task_type="code_review",
        )
        assert tracker.month_spent() == 0.0

    def test_record_multiple_requests_sums_cost(self, tracker: BudgetTracker) -> None:
        tracker.record_request(
            "model-a", cost_usd=0.10, tokens=200, latency_ms=300, task_type="general"
        )
        tracker.record_request(
            "model-b", cost_usd=0.25, tokens=500, latency_ms=600, task_type="general"
        )
        assert tracker.month_spent() == pytest.approx(0.35)

    def test_empty_db_returns_zero_spent(self, tracker: BudgetTracker) -> None:
        assert tracker.month_spent() == 0.0


class TestBudgetGuards:
    """Budget cap enforcement."""

    def test_month_remaining_with_no_spend(self, tracker: BudgetTracker) -> None:
        assert tracker.month_remaining(budget=5.0) == 5.0

    def test_month_remaining_after_spend(self, tracker: BudgetTracker) -> None:
        tracker.record_request("m", cost_usd=1.50, tokens=100, latency_ms=100, task_type="general")
        assert tracker.month_remaining(budget=5.0) == pytest.approx(3.50)

    def test_can_spend_within_budget(self, tracker: BudgetTracker) -> None:
        assert tracker.can_spend(budget=5.0, amount=1.0) is True

    def test_can_spend_exceeds_budget(self, tracker: BudgetTracker) -> None:
        tracker.record_request("m", cost_usd=4.50, tokens=100, latency_ms=100, task_type="general")
        assert tracker.can_spend(budget=5.0, amount=1.0) is False

    def test_can_spend_exact_boundary(self, tracker: BudgetTracker) -> None:
        tracker.record_request("m", cost_usd=4.00, tokens=100, latency_ms=100, task_type="general")
        assert tracker.can_spend(budget=5.0, amount=1.0) is True


class TestMonthStats:
    """Monthly statistics aggregation."""

    def test_empty_stats(self, tracker: BudgetTracker) -> None:
        stats = tracker.month_stats(budget=5.0)
        assert stats["spent"] == 0.0
        assert stats["remaining"] == 5.0
        assert stats["request_count"] == 0
        assert stats["by_model"] == {}

    def test_stats_with_requests(self, tracker: BudgetTracker) -> None:
        tracker.record_request(
            "model-a", cost_usd=0.10, tokens=200, latency_ms=300, task_type="code"
        )
        tracker.record_request(
            "model-a", cost_usd=0.15, tokens=300, latency_ms=500, task_type="code"
        )
        tracker.record_request(
            "model-b", cost_usd=0.00, tokens=100, latency_ms=200, task_type="general"
        )

        stats = tracker.month_stats(budget=5.0)
        assert stats["spent"] == pytest.approx(0.25)
        assert stats["remaining"] == pytest.approx(4.75)
        assert stats["request_count"] == 3
        assert stats["by_model"]["model-a"]["count"] == 2
        assert stats["by_model"]["model-a"]["total_cost"] == pytest.approx(0.25)
        assert stats["by_model"]["model-a"]["avg_latency_ms"] == 400
        assert stats["by_model"]["model-b"]["count"] == 1

    def test_stats_only_current_month(self, tracker: BudgetTracker) -> None:
        """Requests from other months should not appear in stats."""
        tracker.record_request("m", cost_usd=1.00, tokens=100, latency_ms=100, task_type="general")
        # Manually insert a row with a different month
        tracker._conn.execute(
            "INSERT INTO requests (month, model, cost_usd, tokens, latency_ms, task_type) "
            "VALUES ('2020-01', 'old-model', 99.0, 100, 100, 'general')"
        )
        tracker._conn.commit()

        stats = tracker.month_stats(budget=5.0)
        assert stats["spent"] == pytest.approx(1.00)
        assert stats["request_count"] == 1


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
        tracker.record_request("m", cost_usd=0.5, tokens=10, latency_ms=50, task_type="test")
        assert db_file.exists()

        # Verify data persists across instances
        tracker2 = BudgetTracker(db_path=str(db_file))
        assert tracker2.month_spent() == pytest.approx(0.5)
