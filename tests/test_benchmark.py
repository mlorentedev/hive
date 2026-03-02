"""Benchmark: static CLAUDE.md loading vs on-demand vault queries.

Measures the context efficiency ratio — how many tokens we save by
replacing static context with on-demand vault_query calls.

Run with: pytest tests/test_benchmark.py -v -s
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from hive.vault_server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.tools import ToolResult

# Rough token estimate: 1 token ≈ 4 characters
_CHARS_PER_TOKEN = 4


def _tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


def _count_file_tokens(path: Path) -> int:
    """Count tokens in a file, return 0 if missing."""
    if path.exists():
        return _tokens(path.read_text(encoding="utf-8"))
    return 0


@pytest.fixture
def bench_vault(mock_vault: Path) -> Path:
    """Extend mock_vault with realistic content sizes for benchmarking."""
    project = mock_vault / "10_projects" / "testproject"

    # Simulate a realistic 00-context.md (~60 lines)
    context_content = (
        "---\nid: testproject\ntype: project\nstatus: active\n---\n\n"
        "# Test Project\n\n> Goal: Build something great.\n\n"
        "## Technical Stack\n" + "- Item\n" * 20 + "\n"
        "## Architecture\n" + "- Component\n" * 15 + "\n"
        "## Hotspots\n" + "- Zone\n" * 10 + "\n"
    )
    (project / "00-context.md").write_text(context_content)

    # Simulate a realistic 11-tasks.md (~40 lines)
    tasks_content = (
        "---\nid: testproject-tasks\ntype: project-tasks\nstatus: active\n---\n\n"
        "# Backlog\n\n" + "- [ ] Task item\n" * 30
    )
    (project / "11-tasks.md").write_text(tasks_content)

    # Simulate a realistic 90-lessons.md (~50 lines)
    lessons_content = (
        "---\nid: testproject-lessons\ntype: lesson\nstatus: active\n---\n\n"
        "# Lessons\n\n"
    )
    for i in range(10):
        lessons_content += f"## Lesson {i}\n- Detail A\n- Detail B\n\n"
    (project / "90-lessons.md").write_text(lessons_content)

    # Simulate static CLAUDE.md that loads everything
    static_claude_md = "# CLAUDE.md (static)\n\n"
    for md_file in sorted(project.rglob("*.md")):
        static_claude_md += md_file.read_text(encoding="utf-8") + "\n\n"
    (mock_vault / "static_claude_md_simulation.md").write_text(static_claude_md)

    return mock_vault


class TestContextBenchmark:
    """Measures token savings from on-demand vault access vs static loading."""

    async def test_static_vs_ondemand_context(self, bench_vault: Path) -> None:
        """Compare: loading all project context statically vs querying one section."""
        mcp = create_server(vault_path=bench_vault)

        # BASELINE: static load = all project files concatenated
        static_file = bench_vault / "static_claude_md_simulation.md"
        static_tokens = _count_file_tokens(static_file)

        # ON-DEMAND: typical session queries only context
        result = await mcp.call_tool(
            "vault_query", {"project": "testproject", "section": "context"}
        )
        ondemand_tokens = _tokens(_text(result))

        savings_pct = (1 - ondemand_tokens / static_tokens) * 100

        print(f"\n{'='*60}")
        print("CONTEXT BENCHMARK")
        print(f"{'='*60}")
        print(f"Static load (all sections):  {static_tokens:>6} tokens")
        print(f"On-demand (context only):    {ondemand_tokens:>6} tokens")
        print(f"Savings:                     {savings_pct:>5.1f}%")
        print(f"{'='*60}")

        # We should save at least 40% by loading only context
        assert savings_pct > 40, f"Expected >40% savings, got {savings_pct:.1f}%"

    async def test_search_vs_full_load(self, bench_vault: Path) -> None:
        """Compare: searching for specific content vs loading everything."""
        mcp = create_server(vault_path=bench_vault)

        # BASELINE: static load
        static_file = bench_vault / "static_claude_md_simulation.md"
        static_tokens = _count_file_tokens(static_file)

        # ON-DEMAND: search for a specific topic
        result = await mcp.call_tool(
            "vault_search", {"query": "Task item", "max_lines": 20}
        )
        search_tokens = _tokens(_text(result))

        savings_pct = (1 - search_tokens / static_tokens) * 100

        print(f"\n{'='*60}")
        print("SEARCH BENCHMARK")
        print(f"{'='*60}")
        print(f"Static load (all sections):  {static_tokens:>6} tokens")
        print(f"Search (targeted, 20 lines): {search_tokens:>6} tokens")
        print(f"Savings:                     {savings_pct:>5.1f}%")
        print(f"{'='*60}")

        assert savings_pct > 60, f"Expected >60% savings, got {savings_pct:.1f}%"

    async def test_typical_session_budget(self, bench_vault: Path) -> None:
        """Simulate a typical session: context + tasks + one search.

        This is the realistic comparison — a session usually needs
        project context, current tasks, and maybe a search.
        """
        mcp = create_server(vault_path=bench_vault)

        # BASELINE: static load
        static_file = bench_vault / "static_claude_md_simulation.md"
        static_tokens = _count_file_tokens(static_file)

        # ON-DEMAND: context + tasks + search
        total_ondemand = 0

        r1 = await mcp.call_tool(
            "vault_query", {"project": "testproject", "section": "context"}
        )
        total_ondemand += _tokens(_text(r1))

        r2 = await mcp.call_tool(
            "vault_query", {"project": "testproject", "section": "tasks"}
        )
        total_ondemand += _tokens(_text(r2))

        r3 = await mcp.call_tool(
            "vault_search", {"query": "Lesson", "max_lines": 10}
        )
        total_ondemand += _tokens(_text(r3))

        # On-demand loads MORE than one section but still less than everything
        # The key insight: you only load what you need, when you need it
        print(f"\n{'='*60}")
        print("TYPICAL SESSION BENCHMARK")
        print(f"{'='*60}")
        print(f"Static load (everything):    {static_tokens:>6} tokens")
        print(f"On-demand (3 queries):       {total_ondemand:>6} tokens")
        print(f"Ratio:                       {total_ondemand/static_tokens:>5.2f}x")
        print(f"{'='*60}")

        # Even with 3 queries, we should use less than static
        assert total_ondemand < static_tokens
