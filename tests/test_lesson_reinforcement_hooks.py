"""Integration tests for HIVE-97 lesson reinforcement hooks — Tier 2.

Spins up a real FastMCP server with a tmp_path git vault and an
in-memory ``LessonReinforcementTracker``. Verifies that the read hooks
in ``capture_lesson``, ``vault_query``, and ``vault_search`` update the
tracker correctly via side-effects observable on ``ctx.lessons``.
"""

from __future__ import annotations

import subprocess
from datetime import date
from typing import TYPE_CHECKING

import pytest

from hive._lesson_reinforcement import LessonReinforcementTracker
from hive.server import create_server

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from fastmcp import FastMCP
    from fastmcp.tools import ToolResult


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


_LESSONS_HEADER = (
    "---\nid: testproject-lessons\ntype: lesson\nstatus: active\n---\n\n# Test: Lessons\n\n"
)


def _three_lessons_body() -> str:
    return (
        _LESSONS_HEADER
        + "### [2026-01-01] alpha lesson\n"
        + "**Context:** ctx-a\n**Problem:** prob-a\n**Solution:** sol-a\n\n"
        + "### [2026-01-02] beta lesson\n"
        + "**Context:** ctx-b\n**Problem:** prob-b\n**Solution:** sol-b\n\n"
        + "### [2026-01-03] gamma lesson\n"
        + "**Context:** ctx-c\n**Problem:** prob-c\n**Solution:** sol-c\n\n"
    )


def _git_init_commit(vault: Path, msg: str = "init") -> None:
    subprocess.run(
        ["git", "init"],
        cwd=vault,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=vault,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=vault,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "."],
        cwd=vault,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=vault,
        check=True,
        capture_output=True,
    )


def _git_recommit(vault: Path, msg: str = "update") -> None:
    subprocess.run(
        ["git", "add", "."],
        cwd=vault,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=vault,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def lesson_tracker() -> Generator[LessonReinforcementTracker, None, None]:
    """Fresh in-memory tracker per test (no filesystem leakage)."""
    t = LessonReinforcementTracker()
    yield t
    t.close()


@pytest.fixture
def lesson_vault(tmp_path: Path) -> Path:
    """Git-backed vault with one project: 3 dated lessons + a context file."""
    project = tmp_path / "10_projects" / "testproject"
    project.mkdir(parents=True)
    (project / "00-context.md").write_text(
        "---\nid: testproject\ntype: project\nstatus: active\n---\n\n"
        "# Test Project\n\nOverview mentioning alpha as a keyword.\n",
    )
    (project / "90-lessons.md").write_text(_three_lessons_body())
    _git_init_commit(tmp_path)
    return tmp_path


@pytest.fixture
def lesson_mcp(
    lesson_vault: Path,
    lesson_tracker: LessonReinforcementTracker,
) -> Generator[FastMCP, None, None]:
    """MCP server with the in-memory lesson tracker injected."""
    mcp = create_server(vault_path=lesson_vault, lesson_tracker=lesson_tracker)
    yield mcp
    ctx = getattr(mcp, "_hive_ctx", None)
    if ctx is not None:
        # Don't close the lesson_tracker here — its own fixture handles that.
        ctx.tracker.close()
        ctx.budget.close()
        ctx.relevance.close()


# ── T2.1 + T2.2 — capture_lesson seeds baseline DB rows ──────────────


class TestCaptureLessonInsertsBaseline:
    """T2.1, T2.2 — capture_lesson must call ctx.lessons.ensure() per write."""

    async def test_inline_capture_inserts_baseline(
        self,
        lesson_mcp: FastMCP,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        today = date.today().isoformat()
        await lesson_mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Delta lesson",
                "context": "ctx-d",
                "problem": "prob-d",
                "solution": "sol-d",
            },
        )
        row = lesson_tracker.get("testproject", f"[{today}] Delta lesson")
        assert row is not None, "capture_lesson should ensure baseline DB row"
        assert row["reinforcements"] == 0
        assert abs(row["confidence"] - 0.7) < 1e-9

    async def test_multiple_inline_captures_each_get_baseline_row(
        self,
        lesson_mcp: FastMCP,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        today = date.today().isoformat()
        for title in ("L1", "L2", "L3"):
            await lesson_mcp.call_tool(
                "capture_lesson",
                {
                    "project": "testproject",
                    "title": title,
                    "context": "c",
                    "problem": "p",
                    "solution": "s",
                },
            )
        for title in ("L1", "L2", "L3"):
            row = lesson_tracker.get("testproject", f"[{today}] {title}")
            assert row is not None, f"missing baseline row for {title}"
            assert row["reinforcements"] == 0


# ── T2.3 + T2.4 — vault_query increments existing lesson rows ────────


class TestVaultQueryIncrementsLessons:
    async def test_vault_query_lessons_increments_each_heading_once(
        self,
        lesson_mcp: FastMCP,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        await lesson_mcp.call_tool(
            "vault_query",
            {"project": "testproject", "section": "lessons"},
        )
        for heading in (
            "[2026-01-01] alpha lesson",
            "[2026-01-02] beta lesson",
            "[2026-01-03] gamma lesson",
        ):
            row = lesson_tracker.get("testproject", heading)
            assert row is not None, f"no increment for {heading}"
            assert row["reinforcements"] == 1, (
                f"{heading}: expected single dedup increment, got {row['reinforcements']}"
            )

    async def test_vault_query_non_lesson_file_is_noop_on_db(
        self,
        lesson_mcp: FastMCP,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        await lesson_mcp.call_tool(
            "vault_query",
            {"project": "testproject", "section": "context"},
        )
        # No lesson rows should have been created.
        for heading in (
            "[2026-01-01] alpha lesson",
            "[2026-01-02] beta lesson",
            "[2026-01-03] gamma lesson",
        ):
            assert lesson_tracker.get("testproject", heading) is None


# ── T2.5 + T2.6 + T2.7 — vault_search rank_by semantics ──────────────


class TestVaultSearchRankBy:
    async def test_default_rank_output_invariant_to_tracker_state(
        self,
        lesson_mcp: FastMCP,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        """Back-compat: default vault_search output is byte-identical
        regardless of tracker state. The hook must NOT mutate the
        rank_by='bm25' code path."""
        before = _text(
            await lesson_mcp.call_tool(
                "vault_search",
                {"query": "lesson"},
            )
        )
        # Mutate the tracker — this must NOT change subsequent default output.
        lesson_tracker.ensure("testproject", "[2026-01-01] alpha lesson")
        lesson_tracker.increment("testproject", "[2026-01-01] alpha lesson")
        lesson_tracker.increment("testproject", "[2026-01-01] alpha lesson")
        after = _text(
            await lesson_mcp.call_tool(
                "vault_search",
                {"query": "lesson"},
            )
        )
        assert before == after, "default vault_search output drifted with tracker state"

    async def test_rank_by_reinforcements_filters_to_lessons_file_only(
        self,
        lesson_mcp: FastMCP,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        """`alpha` matches both 00-context.md AND 90-lessons.md. With
        rank_by=reinforcements only the lesson hit must appear."""
        lesson_tracker.ensure("testproject", "[2026-01-01] alpha lesson")
        lesson_tracker.increment("testproject", "[2026-01-01] alpha lesson")
        result = _text(
            await lesson_mcp.call_tool(
                "vault_search",
                {"query": "alpha", "rank_by": "reinforcements"},
            )
        )
        assert "90-lessons.md" in result
        assert "00-context.md" not in result

    async def test_rank_by_invalid_returns_clear_error(
        self,
        lesson_mcp: FastMCP,
    ) -> None:
        """`rank_by='bogus'` MUST NOT silently fall back to bm25 — it
        must surface a clear error mentioning both the field and the bad
        value so typos are caught immediately."""
        result = _text(
            await lesson_mcp.call_tool(
                "vault_search",
                {"query": "alpha", "rank_by": "bogus"},
            )
        )
        lower = result.lower()
        assert "rank_by" in lower
        assert "bogus" in result, (
            "Error message must include the bad value 'bogus' so the user sees what to fix"
        )


# ── T2.8 — lazy ensure on first read of pre-existing lesson ──────────


class TestLazyEnsureForPreExistingLessons:
    async def test_first_vault_query_lazy_inserts_existing_disk_lessons(
        self,
        lesson_mcp: FastMCP,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        # Tracker is empty before the call.
        assert (
            lesson_tracker.get(
                "testproject",
                "[2026-01-01] alpha lesson",
            )
            is None
        )
        await lesson_mcp.call_tool(
            "vault_query",
            {"project": "testproject", "section": "lessons"},
        )
        row = lesson_tracker.get(
            "testproject",
            "[2026-01-01] alpha lesson",
        )
        assert row is not None, "first read should lazy-create the DB row"
        # First read counts as the lazy-create + the increment.
        assert row["reinforcements"] == 1
        # Confidence after one increment from default c0=0.7 → 0.73.
        assert abs(row["confidence"] - 0.73) < 1e-9


# ── T2.9 + T2.10 — codeblock + malformed-heading parsing ─────────────


class TestHeadingParserEdgeCases:
    async def test_heading_inside_fenced_codeblock_not_counted(
        self,
        lesson_vault: Path,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        """A `### [date] foo` inside ``` … ``` must NOT be counted as a
        lesson — that block is documentation about lesson syntax, not a
        lesson itself."""
        project = lesson_vault / "10_projects" / "testproject"
        (project / "90-lessons.md").write_text(
            _LESSONS_HEADER
            + "### [2026-01-01] real heading\n"
            + "body line\n\n"
            + "```\n"
            + "### [2026-12-31] fake heading inside code\n"
            + "```\n\n",
        )
        _git_recommit(lesson_vault)

        mcp = create_server(
            vault_path=lesson_vault,
            lesson_tracker=lesson_tracker,
        )
        try:
            await mcp.call_tool(
                "vault_query",
                {"project": "testproject", "section": "lessons"},
            )
        finally:
            ctx = getattr(mcp, "_hive_ctx", None)
            if ctx is not None:
                ctx.tracker.close()
                ctx.budget.close()
                ctx.relevance.close()

        real = lesson_tracker.get(
            "testproject",
            "[2026-01-01] real heading",
        )
        assert real is not None
        assert real["reinforcements"] == 1
        fake = lesson_tracker.get(
            "testproject",
            "[2026-12-31] fake heading inside code",
        )
        assert fake is None, "heading inside code block must NOT be counted"

    async def test_malformed_heading_does_not_crash_or_count(
        self,
        lesson_vault: Path,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        """### no-date and ### [not-a-date] x must not crash and must
        not create rows in the tracker."""
        project = lesson_vault / "10_projects" / "testproject"
        (project / "90-lessons.md").write_text(
            _LESSONS_HEADER
            + "### no date here\n"
            + "body\n\n"
            + "### [not-a-date] also broken\n"
            + "body\n\n",
        )
        _git_recommit(lesson_vault)

        mcp = create_server(
            vault_path=lesson_vault,
            lesson_tracker=lesson_tracker,
        )
        try:
            # The call must not raise.
            await mcp.call_tool(
                "vault_query",
                {"project": "testproject", "section": "lessons"},
            )
        finally:
            ctx = getattr(mcp, "_hive_ctx", None)
            if ctx is not None:
                ctx.tracker.close()
                ctx.budget.close()
                ctx.relevance.close()

        assert lesson_tracker.get("testproject", "no date here") is None
        assert (
            lesson_tracker.get(
                "testproject",
                "[not-a-date] also broken",
            )
            is None
        )
