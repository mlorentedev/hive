"""Unit tests for LessonReinforcementTracker — Tier 1 of HIVE-97.

Pure tracker behavior. In-memory DB. No MCP, no filesystem.
"""

from __future__ import annotations

import threading

from hive._lesson_reinforcement import LessonReinforcementTracker


class TestSchemaInit:
    def test_schema_has_lesson_reinforcement_table(self) -> None:
        t = LessonReinforcementTracker()
        rows = t._conn.execute(  # noqa: SLF001
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='lesson_reinforcement'",
        ).fetchall()
        assert rows, "lesson_reinforcement table missing"

    def test_schema_columns(self) -> None:
        t = LessonReinforcementTracker()
        cols = [
            row[1]
            for row in t._conn.execute(  # noqa: SLF001
                "PRAGMA table_info(lesson_reinforcement)",
            ).fetchall()
        ]
        expected = {
            "project", "heading", "reinforcements", "confidence",
            "first_seen", "last_referenced",
        }
        assert expected.issubset(set(cols))

    def test_schema_primary_key_is_project_heading(self) -> None:
        t = LessonReinforcementTracker()
        pk_cols = [
            row[1]
            for row in t._conn.execute(  # noqa: SLF001
                "PRAGMA table_info(lesson_reinforcement)",
            ).fetchall()
            if row[5] > 0  # pk column index > 0 means part of PK
        ]
        assert set(pk_cols) == {"project", "heading"}


class TestEnsureBaseline:
    def test_ensure_creates_row_with_defaults(self) -> None:
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-05-18] foo")
        row = t.get("hive", "[2026-05-18] foo")
        assert row is not None
        assert row["reinforcements"] == 0
        assert abs(row["confidence"] - 0.7) < 1e-9
        assert row["first_seen"]  # non-empty ISO date

    def test_ensure_respects_custom_confidence(self) -> None:
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-05-18] bar", confidence=0.9)
        row = t.get("hive", "[2026-05-18] bar")
        assert row is not None
        assert abs(row["confidence"] - 0.9) < 1e-9


class TestEnsureIdempotent:
    def test_ensure_twice_does_not_reset_counter(self) -> None:
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-05-18] foo")
        t.increment("hive", "[2026-05-18] foo")
        t.increment("hive", "[2026-05-18] foo")
        t.ensure("hive", "[2026-05-18] foo", confidence=0.5)
        row = t.get("hive", "[2026-05-18] foo")
        assert row is not None
        assert row["reinforcements"] == 2  # NOT reset to 0
        # confidence NOT overwritten back to 0.5 — must remain the decayed value
        assert row["confidence"] > 0.7


class TestIncrementArithmetic:
    def test_five_increments_matches_decay_formula(self) -> None:
        """c_5 = 1 - 0.3 * 0.9^5 = 0.82285... ; tolerance 0.001."""
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-05-18] foo")  # c0 = 0.7
        for _ in range(5):
            t.increment("hive", "[2026-05-18] foo")
        row = t.get("hive", "[2026-05-18] foo")
        assert row is not None
        assert row["reinforcements"] == 5
        # 1 - 0.3 * 0.9**5 = 0.822855
        assert 0.8218 <= row["confidence"] <= 0.8238

    def test_first_increment_matches_formula(self) -> None:
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-05-18] foo")  # c0 = 0.7
        t.increment("hive", "[2026-05-18] foo")
        row = t.get("hive", "[2026-05-18] foo")
        assert row is not None
        # c_1 = 0.7 + 0.1 * (1 - 0.7) = 0.73
        assert abs(row["confidence"] - 0.73) < 1e-9


class TestIncrementCeiling:
    def test_100_increments_never_exceeds_one(self) -> None:
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-05-18] foo")
        for _ in range(100):
            t.increment("hive", "[2026-05-18] foo")
        row = t.get("hive", "[2026-05-18] foo")
        assert row is not None
        assert row["reinforcements"] == 100
        assert row["confidence"] <= 1.0
        # After 100 touches: 1 - 0.3 * 0.9**100 ≈ 0.9999...
        assert row["confidence"] > 0.99


class TestIncrementLazyCreate:
    def test_increment_unknown_lesson_lazy_creates(self) -> None:
        t = LessonReinforcementTracker()
        # No prior ensure() call.
        t.increment("hive", "[2026-05-18] foo")
        row = t.get("hive", "[2026-05-18] foo")
        assert row is not None
        assert row["reinforcements"] == 1
        # Lazy default: c0 = 0.7, then bumped once → 0.73
        assert abs(row["confidence"] - 0.73) < 1e-9


class TestConcurrentThreads:
    def test_20_threads_increment_same_row_no_lost_updates(self) -> None:
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-05-18] foo")
        n_threads = 20

        def worker() -> None:
            t.increment("hive", "[2026-05-18] foo")

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        row = t.get("hive", "[2026-05-18] foo")
        assert row is not None
        assert row["reinforcements"] == n_threads


class TestTopByReinforcements:
    def test_orders_by_counter_desc(self) -> None:
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-01-01] a")
        t.ensure("hive", "[2026-01-02] b")
        t.ensure("hive", "[2026-01-03] c")
        for _ in range(1):
            t.increment("hive", "[2026-01-01] a")
        for _ in range(10):
            t.increment("hive", "[2026-01-02] b")
        for _ in range(5):
            t.increment("hive", "[2026-01-03] c")
        top = t.top("hive", by="reinforcements", limit=3)
        # Result is list of headings ordered by counter desc.
        assert top == [
            "[2026-01-02] b",  # 10
            "[2026-01-03] c",  # 5
            "[2026-01-01] a",  # 1
        ]

    def test_top_respects_limit(self) -> None:
        t = LessonReinforcementTracker()
        for i in range(5):
            heading = f"[2026-01-0{i+1}] l{i}"
            t.ensure("hive", heading)
            for _ in range(i):
                t.increment("hive", heading)
        top = t.top("hive", by="reinforcements", limit=2)
        assert len(top) == 2


class TestTopByConfidenceTieBreaker:
    def test_equal_confidence_breaks_by_recency(self) -> None:
        """Equal confidence → more recent last_referenced first."""
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-01-01] old")
        t.ensure("hive", "[2026-01-02] new")
        # Both get one increment → equal confidence 0.73. Post-PR-4 the
        # increment routes through the outbox; flush() between the
        # increment and the manual last_referenced override so the
        # later flush-on-read doesn't overwrite our explicit values
        # with date('now').
        t.increment("hive", "[2026-01-01] old")
        t.flush()
        t._conn.execute(  # noqa: SLF001
            "UPDATE lesson_reinforcement SET last_referenced='2026-01-01' "
            "WHERE heading=?",
            ("[2026-01-01] old",),
        )
        t.increment("hive", "[2026-01-02] new")
        t.flush()
        t._conn.execute(  # noqa: SLF001
            "UPDATE lesson_reinforcement SET last_referenced='2026-05-18' "
            "WHERE heading=?",
            ("[2026-01-02] new",),
        )
        t._conn.commit()  # noqa: SLF001
        top = t.top("hive", by="confidence", limit=2)
        assert top[0] == "[2026-01-02] new"  # more recent first


class TestTopByHybridBlend:
    def test_hybrid_blend_favors_high_bm25_when_low_conf_gap(self) -> None:
        """High BM25 wins over moderate confidence gap (alpha=0.7 BM25-leaning)."""
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-01-01] hi_bm25", confidence=0.7)   # low conf
        t.ensure("hive", "[2026-01-02] hi_conf", confidence=1.0)   # high conf

        bm25 = {
            "[2026-01-01] hi_bm25": 1.0,  # max BM25
            "[2026-01-02] hi_conf": 0.0,  # zero BM25
        }
        # hybrid_score(hi_bm25) = 0.7*1.0 + 0.3*0.7 = 0.91
        # hybrid_score(hi_conf) = 0.7*0.0 + 0.3*1.0 = 0.30
        top = t.top(
            "hive", by="hybrid", limit=2, bm25_scores=bm25,
        )
        assert top[0] == "[2026-01-01] hi_bm25"

    def test_hybrid_blend_favors_high_conf_when_bm25_close(self) -> None:
        """When BM25 scores are close, confidence acts as tiebreaker."""
        t = LessonReinforcementTracker()
        t.ensure("hive", "[2026-01-01] low_conf", confidence=0.5)
        t.ensure("hive", "[2026-01-02] hi_conf", confidence=1.0)

        bm25 = {
            "[2026-01-01] low_conf": 0.5,
            "[2026-01-02] hi_conf": 0.5,
        }
        # hybrid(low) = 0.7*0.5 + 0.3*0.5 = 0.50
        # hybrid(hi)  = 0.7*0.5 + 0.3*1.0 = 0.65
        top = t.top("hive", by="hybrid", limit=2, bm25_scores=bm25)
        assert top[0] == "[2026-01-02] hi_conf"
