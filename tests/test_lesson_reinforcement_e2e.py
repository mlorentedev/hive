"""End-to-end tests for HIVE-97 lesson reinforcement — Tier 3.

Spins the full FastMCP server in-memory (per existing
``tests/test_integration.py`` pattern) and exercises tools via their
MCP names across multiple calls. Validates both the tool response and
the SQLite-side state.

Multi-process tests (T3.4, T3.6) are marked ``@pytest.mark.smoke`` so
``make test`` stays fast; ``make smoke`` runs them.
"""

from __future__ import annotations

import multiprocessing
import subprocess
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


def _git_init_commit(vault: Path, msg: str = "init") -> None:
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "t@t.com"],
        ["git", "config", "user.name", "T"],
        ["git", "add", "."],
        ["git", "commit", "-m", msg],
    ):
        subprocess.run(cmd, cwd=vault, check=True, capture_output=True)


def _seed_three_lessons(project_dir: Path) -> None:
    project_dir.mkdir(parents=True)
    (project_dir / "00-context.md").write_text(
        "---\nid: testproject\ntype: project\nstatus: active\n---\n\n# Test\n\nOverview.\n",
    )
    (project_dir / "90-lessons.md").write_text(
        _LESSONS_HEADER
        + "### [2026-01-01] alpha bug fix\n"
        + "**Context:** ctx-a\n**Problem:** alpha was broken\n"
        + "**Solution:** sol-a\n\n"
        + "### [2026-01-02] beta refactor\n"
        + "**Context:** ctx-b\n**Problem:** beta needed refactor\n"
        + "**Solution:** sol-b\n\n"
        + "### [2026-01-03] gamma docs\n"
        + "**Context:** ctx-c\n**Problem:** gamma docs missing\n"
        + "**Solution:** sol-c\n\n",
    )


@pytest.fixture
def lesson_tracker() -> Generator[LessonReinforcementTracker, None, None]:
    t = LessonReinforcementTracker()
    yield t
    t.close()


@pytest.fixture
def e2e_vault(tmp_path: Path) -> Path:
    _seed_three_lessons(tmp_path / "10_projects" / "testproject")
    _git_init_commit(tmp_path)
    return tmp_path


@pytest.fixture
def e2e_mcp(
    e2e_vault: Path,
    lesson_tracker: LessonReinforcementTracker,
) -> Generator[FastMCP, None, None]:
    mcp = create_server(
        vault_path=e2e_vault,
        lesson_tracker=lesson_tracker,
    )
    yield mcp
    ctx = getattr(mcp, "_hive_ctx", None)
    if ctx is not None:
        ctx.tracker.close()
        ctx.budget.close()
        ctx.relevance.close()


# ── T3.1 — capture then search ranked ────────────────────────────────


class TestE2ECaptureThenSearchRanked:
    async def test_lesson_hit_more_tops_the_ranked_result(
        self,
        e2e_mcp: FastMCP,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        """Read activity drives the ranking: a lesson read 5× outranks
        peers read only 1×."""
        # Trigger 5 increments on 'alpha' lesson via vault_search
        # (BM25 default doesn't increment, so use rank_by=reinforcements
        # which IS our read hook on the lesson surface).
        for _ in range(5):
            await e2e_mcp.call_tool(
                "vault_search",
                {"query": "alpha", "rank_by": "reinforcements"},
            )
        # One pass that surfaces all 3 lessons (each counted once).
        # 'Context' matches a body line under EACH lesson heading.
        await e2e_mcp.call_tool(
            "vault_search",
            {"query": "Context", "rank_by": "reinforcements"},
        )
        # Direct verification on tracker state.
        alpha = lesson_tracker.get(
            "testproject",
            "[2026-01-01] alpha bug fix",
        )
        beta = lesson_tracker.get(
            "testproject",
            "[2026-01-02] beta refactor",
        )
        gamma = lesson_tracker.get(
            "testproject",
            "[2026-01-03] gamma docs",
        )
        assert alpha is not None
        assert beta is not None
        assert gamma is not None
        # alpha was hit 5× alone + 1× in the broad query = 6.
        # beta, gamma only hit by the broad query = 1.
        assert alpha["reinforcements"] == 6
        assert beta["reinforcements"] == 1
        assert gamma["reinforcements"] == 1

        # Now verify the ranked search response surfaces alpha first.
        result = _text(
            await e2e_mcp.call_tool(
                "vault_search",
                {"query": "Context", "rank_by": "reinforcements"},
            )
        )
        alpha_pos = result.find("alpha bug fix")
        beta_pos = result.find("beta refactor")
        gamma_pos = result.find("gamma docs")
        assert alpha_pos != -1
        assert beta_pos != -1
        assert gamma_pos != -1
        assert alpha_pos < beta_pos
        assert alpha_pos < gamma_pos


# ── T3.2 — capture_lesson(find=...) lookup mode ──────────────────────


class TestE2ECaptureLessonFindMode:
    async def test_find_mode_returns_top_matches_and_increments_each(
        self,
        e2e_mcp: FastMCP,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        result = _text(
            await e2e_mcp.call_tool(
                "capture_lesson",
                {"project": "testproject", "find": "alpha"},
            )
        )
        # Response surfaces the matching lesson.
        assert "alpha bug fix" in result
        # The matching lesson got incremented.
        alpha = lesson_tracker.get(
            "testproject",
            "[2026-01-01] alpha bug fix",
        )
        assert alpha is not None
        assert alpha["reinforcements"] == 1
        # Non-matching lessons untouched.
        beta = lesson_tracker.get(
            "testproject",
            "[2026-01-02] beta refactor",
        )
        assert beta is None

    async def test_find_mode_no_matches_returns_clear_message(
        self,
        e2e_mcp: FastMCP,
    ) -> None:
        result = _text(
            await e2e_mcp.call_tool(
                "capture_lesson",
                {"project": "testproject", "find": "nonexistent-xyz"},
            )
        )
        lower = result.lower()
        assert "no" in lower and "lesson" in lower


# ── T3.3 — read-then-rank flow via vault_query ───────────────────────


class TestE2EReadThenRank:
    async def test_vault_query_increments_visible_in_next_rank_by_search(
        self,
        e2e_mcp: FastMCP,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        """vault_query on lessons reads everything once each. A subsequent
        rank_by search includes those touched lessons in the ranking."""
        # First, read the lessons file → each lesson incremented once.
        await e2e_mcp.call_tool(
            "vault_query",
            {"project": "testproject", "section": "lessons"},
        )
        # Verify each lesson got the increment.
        for heading in (
            "[2026-01-01] alpha bug fix",
            "[2026-01-02] beta refactor",
            "[2026-01-03] gamma docs",
        ):
            row = lesson_tracker.get("testproject", heading)
            assert row is not None
            assert row["reinforcements"] == 1
        # Now rank_by search finds them (body content lookup).
        result = _text(
            await e2e_mcp.call_tool(
                "vault_search",
                {"query": "Context", "rank_by": "reinforcements"},
            )
        )
        assert "alpha" in result or "beta" in result or "gamma" in result


# ── T3.5 — back-compat against representative searches ──────────────


class TestE2EBackCompat:
    async def test_default_search_invariant_across_multi_query_replay(
        self,
        e2e_vault: Path,
        lesson_tracker: LessonReinforcementTracker,
    ) -> None:
        """Replay 3 representative default searches; confirm output is
        identical regardless of tracker state. Stronger than T2.5 — checks
        every search variant (filter, regex, plain)."""
        mcp1 = create_server(
            vault_path=e2e_vault,
            lesson_tracker=lesson_tracker,
        )
        try:
            queries = [
                {"query": "lesson"},
                {"query": "alpha"},
                {"query": "context", "type_filter": "project"},
            ]
            golden = [_text(await mcp1.call_tool("vault_search", q)) for q in queries]
        finally:
            ctx = getattr(mcp1, "_hive_ctx", None)
            if ctx is not None:
                ctx.tracker.close()
                ctx.budget.close()
                ctx.relevance.close()

        # Pollute tracker with data.
        lesson_tracker.ensure("testproject", "[2026-01-01] alpha bug fix")
        for _ in range(10):
            lesson_tracker.increment(
                "testproject",
                "[2026-01-01] alpha bug fix",
            )

        # Re-run with the SAME tracker and a fresh server. Default outputs
        # must match byte-for-byte.
        mcp2 = create_server(
            vault_path=e2e_vault,
            lesson_tracker=lesson_tracker,
        )
        try:
            for i, q in enumerate(queries):
                replay = _text(await mcp2.call_tool("vault_search", q))
                assert replay == golden[i], f"default vault_search query {q} drifted between runs"
        finally:
            ctx = getattr(mcp2, "_hive_ctx", None)
            if ctx is not None:
                ctx.tracker.close()
                ctx.budget.close()
                ctx.relevance.close()


# ── T3.4 + T3.6 — multi-process (smoke only) ────────────────────────


def _subprocess_increment_target(
    db_path: str,
    project: str,
    heading: str,
    n: int,
) -> None:
    """Target for multiprocessing.Process — opens its OWN tracker over
    the same on-disk SQLite file and increments N times. Validates the
    WAL + busy_timeout combination from `_SqliteTracker`."""
    # Import inside the target so the child process gets its own module
    # state and connection.
    from hive._lesson_reinforcement import LessonReinforcementTracker

    t = LessonReinforcementTracker(db_path=db_path)
    try:
        for _ in range(n):
            t.increment(project, heading)
    finally:
        t.close()


@pytest.mark.smoke
class TestE2ECrossProcessIncrement:
    """T3.4 — 2 OS processes × 5 increments → final count == 10."""

    def test_two_subprocesses_no_lost_updates(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "lesson.db")
        project = "p"
        heading = "[2026-01-01] same lesson"

        # Pre-seed so neither subprocess races on the INSERT branch
        # (that race is T3.6's responsibility).
        seeder = LessonReinforcementTracker(db_path=db_path)
        seeder.ensure(project, heading)
        seeder.close()

        ctx = multiprocessing.get_context("spawn")
        procs = [
            ctx.Process(
                target=_subprocess_increment_target,
                args=(db_path, project, heading, 5),
            )
            for _ in range(2)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
            assert p.exitcode == 0, f"subprocess failed: {p.exitcode}"

        verifier = LessonReinforcementTracker(db_path=db_path)
        try:
            row = verifier.get(project, heading)
            assert row is not None
            assert row["reinforcements"] == 10, (
                f"expected 10 cumulative increments across 2 processes, got {row['reinforcements']}"
            )
        finally:
            verifier.close()


@pytest.mark.smoke
class TestE2EConcurrentLazyEnsureRace:
    """T3.6 — 2 subprocesses first-touch same lesson → exactly one row,
    count == 2 (INSERT OR IGNORE wins the race)."""

    def test_concurrent_first_touch_no_duplicate_key(
        self,
        tmp_path: Path,
    ) -> None:
        db_path = str(tmp_path / "lesson.db")
        project = "p"
        heading = "[2026-01-01] never-seen lesson"

        ctx = multiprocessing.get_context("spawn")
        procs = [
            ctx.Process(
                target=_subprocess_increment_target,
                args=(db_path, project, heading, 1),
            )
            for _ in range(2)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
            assert p.exitcode == 0

        verifier = LessonReinforcementTracker(db_path=db_path)
        try:
            row = verifier.get(project, heading)
            assert row is not None, "lazy-create lost the race entirely"
            assert row["reinforcements"] == 2, (
                f"expected exactly 2 increments under lazy-create race, got {row['reinforcements']}"
            )
        finally:
            verifier.close()
