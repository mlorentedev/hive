"""Tests for RelevanceTracker — EMA-based section relevance scoring."""

from __future__ import annotations

from hive.relevance import RelevanceTracker


class TestRecordAccess:
    def test_single_access_creates_score(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        scores = t.get_scores("hive")
        assert "tasks" in scores
        assert scores["tasks"] > 0

    def test_repeated_access_increases_score(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        score1 = t.get_scores("hive")["tasks"]
        t.record_access("hive", "tasks")
        score2 = t.get_scores("hive")["tasks"]
        assert score2 > score1

    def test_different_sections_independent(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        t.record_access("hive", "tasks")
        t.record_access("hive", "lessons")
        scores = t.get_scores("hive")
        assert scores["tasks"] > scores["lessons"]

    def test_different_projects_independent(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        t.record_access("dotfiles", "tasks")
        hive_scores = t.get_scores("hive")
        dot_scores = t.get_scores("dotfiles")
        assert "tasks" in hive_scores
        assert "tasks" in dot_scores

    def test_empty_project_returns_empty(self) -> None:
        t = RelevanceTracker()
        assert t.get_scores("nonexistent") == {}


class TestDecay:
    def test_decay_reduces_scores(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        before = t.get_scores("hive")["tasks"]
        t.apply_decay()
        after = t.get_scores("hive")["tasks"]
        assert after < before

    def test_decay_preserves_ordering(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        t.record_access("hive", "tasks")
        t.record_access("hive", "lessons")
        t.apply_decay()
        scores = t.get_scores("hive")
        assert scores["tasks"] > scores["lessons"]

    def test_decay_removes_near_zero(self) -> None:
        t = RelevanceTracker(alpha=0.01)
        t.record_access("hive", "tasks")
        for _ in range(50):
            t.apply_decay()
        scores = t.get_scores("hive")
        assert "tasks" not in scores or scores["tasks"] < 0.001


class TestTopSections:
    def test_returns_top_n(self) -> None:
        t = RelevanceTracker()
        for section in ["a", "b", "c", "d", "e"]:
            t.record_access("hive", section)
        # Access "a" more to make it top
        t.record_access("hive", "a")
        t.record_access("hive", "a")
        top = t.top_sections("hive", n=3)
        assert len(top) == 3
        assert top[0] == "a"

    def test_returns_all_if_fewer_than_n(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        top = t.top_sections("hive", n=10)
        assert top == ["tasks"]

    def test_empty_project_returns_empty(self) -> None:
        t = RelevanceTracker()
        assert t.top_sections("nope", n=5) == []


class TestExploration:
    def test_explore_adds_recent_sections(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        recent_sections = ["lessons", "roadmap"]
        result = t.top_sections_with_exploration(
            "hive", n=3, recent_sections=recent_sections, epsilon=1.0,
        )
        assert any(s in result for s in recent_sections)

    def test_no_exploration_at_zero_epsilon(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        recent_sections = ["lessons", "roadmap"]
        result = t.top_sections_with_exploration(
            "hive", n=1, recent_sections=recent_sections, epsilon=0.0,
        )
        assert result == ["tasks"]

    def test_exploration_does_not_duplicate(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        # "tasks" is already in top — should not be added again as exploration
        result = t.top_sections_with_exploration(
            "hive", n=3, recent_sections=["tasks"], epsilon=1.0,
        )
        assert result.count("tasks") == 1


class TestWriteBoost:
    def test_record_write_boosts_more_than_read(self) -> None:
        t = RelevanceTracker()
        t.record_access("hive", "tasks")
        read_score = t.get_scores("hive")["tasks"]
        t2 = RelevanceTracker()
        t2.record_access("dotfiles", "tasks", is_write=True)
        write_score = t2.get_scores("dotfiles")["tasks"]
        assert write_score > read_score


class TestFileDB:
    def test_creates_parent_dirs(self, tmp_path: object) -> None:
        from pathlib import Path

        db = Path(str(tmp_path)) / "sub" / "relevance.db"
        t = RelevanceTracker(str(db))
        t.record_access("hive", "tasks")
        assert db.exists()
        assert t.get_scores("hive")["tasks"] > 0

    def test_persists_across_instances(self, tmp_path: object) -> None:
        from pathlib import Path

        db = Path(str(tmp_path)) / "relevance.db"
        t1 = RelevanceTracker(str(db))
        t1.record_access("hive", "tasks")
        score1 = t1.get_scores("hive")["tasks"]
        # New instance, same DB
        t2 = RelevanceTracker(str(db))
        score2 = t2.get_scores("hive")["tasks"]
        assert score2 == score1
