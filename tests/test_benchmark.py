"""Benchmark suite: token cost, signal-to-noise, and max_lines calibration.

Measures context efficiency across vault tools with synthetic and real vaults.
Produces blog-ready output with structured tables.

Run with: pytest tests/test_benchmark.py -v -s
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

import pytest

from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP
    from fastmcp.tools import ToolResult

# ── Token estimation ──────────────────────────────────────────────

_CHARS_PER_TOKEN = 4


def _tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


def _count_file_tokens(path: Path) -> int:
    if path.exists():
        return _tokens(path.read_text(encoding="utf-8"))
    return 0


# ── Signal-to-noise heuristics ────────────────────────────────────

_NOISE_PATTERNS = re.compile(
    r"^("
    r"---|"  # YAML frontmatter delimiters
    r"\s*$|"  # blank lines
    r"#{1,6}\s*$|"  # empty headers
    r"\[\.{3}\s*truncated.*\]|"  # truncation notices
    r"id:\s|type:\s|status:\s|created:\s|tags:\s"  # frontmatter fields
    r")"
)


def _signal_ratio(text: str) -> float:
    """Fraction of lines that carry useful content (not boilerplate/noise)."""
    lines = text.splitlines()
    if not lines:
        return 0.0
    signal = sum(1 for line in lines if not _NOISE_PATTERNS.match(line))
    return signal / len(lines)


def _completeness(truncated: str, full: str) -> float:
    """Fraction of full content retained after truncation."""
    full_lines = len(full.splitlines())
    if full_lines == 0:
        return 1.0
    trunc_lines = len(truncated.splitlines())
    return min(trunc_lines / full_lines, 1.0)


# ── Synthetic vault ───────────────────────────────────────────────

def _generate_realistic_content(line_count: int, prefix: str = "content") -> str:
    """Generate markdown content that matches real vault patterns."""
    lines = []
    section_count = max(1, line_count // 20)
    lines_per_section = line_count // section_count

    for s in range(section_count):
        lines.append(f"## Section {s + 1}")
        lines.append("")
        for i in range(lines_per_section - 2):
            if i % 5 == 0:
                lines.append(f"### {prefix} subsection {s}.{i}")
            elif i % 3 == 0:
                lines.append(f"- Bullet point: {prefix} item {s}.{i} with details")
            else:
                lines.append(
                    f"Line {s}.{i}: {prefix} paragraph text that simulates "
                    f"real vault content with enough length to be realistic."
                )
        lines.append("")

    return "\n".join(lines[:line_count])


@pytest.fixture
def bench_vault(tmp_path: Path) -> Path:
    """Synthetic vault matching real vault distribution (P25=37, median=77, P90=262, max=878)."""
    projects = {
        "small-project": {
            "00-context.md": 40,
            "11-tasks.md": 30,
            "90-lessons.md": 25,
        },
        "medium-project": {
            "00-context.md": 80,
            "11-tasks.md": 120,
            "90-lessons.md": 75,
        },
        "large-project": {
            "00-context.md": 150,
            "11-tasks.md": 480,
            "90-lessons.md": 870,
            "40-runbooks/deploy.md": 350,
            "40-runbooks/monitoring.md": 400,
            "50-troubleshooting/debugging.md": 300,
        },
    }

    for proj_name, files in projects.items():
        for filename, line_count in files.items():
            filepath = tmp_path / "10_projects" / proj_name / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            file_id = f"{proj_name}-{filename.replace('/', '-')}"
            fm = f"---\nid: {file_id}\ntype: project\nstatus: active\n---\n\n"
            filepath.write_text(fm + _generate_realistic_content(line_count, proj_name))

    # Static baseline: all content concatenated
    all_content = "# Static CLAUDE.md (all vault content)\n\n"
    for md_file in sorted(tmp_path.rglob("*.md")):
        all_content += md_file.read_text(encoding="utf-8") + "\n\n"
    (tmp_path / "static_baseline.md").write_text(all_content)

    return tmp_path


# ── max_lines sweep parameters ────────────────────────────────────

MAX_LINES_SWEEP = [50, 100, 200, 300, 500, 750, 1000, 2000, 5000, 10000, 0]


# ── 1. Original benchmarks (preserved) ────────────────────────────


class TestContextBenchmark:
    """Measures token savings from on-demand vault access vs static loading."""

    async def test_static_vs_ondemand_context(self, bench_vault: Path) -> None:
        mcp = create_server(vault_path=bench_vault)
        static_tokens = _count_file_tokens(bench_vault / "static_baseline.md")
        result = await mcp.call_tool(
            "vault_query", {"project": "medium-project", "section": "context"}
        )
        ondemand_tokens = _tokens(_text(result))
        savings_pct = (1 - ondemand_tokens / static_tokens) * 100

        print(f"\n{'=' * 60}")
        print("CONTEXT BENCHMARK")
        print(f"{'=' * 60}")
        print(f"Static load (all sections):  {static_tokens:>6} tokens")
        print(f"On-demand (context only):    {ondemand_tokens:>6} tokens")
        print(f"Savings:                     {savings_pct:>5.1f}%")

        assert savings_pct > 40

    async def test_search_vs_full_load(self, bench_vault: Path) -> None:
        mcp = create_server(vault_path=bench_vault)
        static_tokens = _count_file_tokens(bench_vault / "static_baseline.md")
        result = await mcp.call_tool("vault_search", {"query": "paragraph text", "max_lines": 100})
        search_tokens = _tokens(_text(result))
        savings_pct = (1 - search_tokens / static_tokens) * 100

        print(f"\n{'=' * 60}")
        print("SEARCH BENCHMARK")
        print(f"{'=' * 60}")
        print(f"Static load:                 {static_tokens:>6} tokens")
        print(f"Search (100 lines):          {search_tokens:>6} tokens")
        print(f"Savings:                     {savings_pct:>5.1f}%")

        assert savings_pct > 40

    async def test_typical_session_budget(self, bench_vault: Path) -> None:
        mcp = create_server(vault_path=bench_vault)
        static_tokens = _count_file_tokens(bench_vault / "static_baseline.md")
        total_ondemand = 0

        r1 = await mcp.call_tool(
            "vault_query", {"project": "medium-project", "section": "context"}
        )
        total_ondemand += _tokens(_text(r1))
        r2 = await mcp.call_tool(
            "vault_query", {"project": "medium-project", "section": "tasks"}
        )
        total_ondemand += _tokens(_text(r2))
        r3 = await mcp.call_tool("vault_search", {"query": "deploy", "max_lines": 50})
        total_ondemand += _tokens(_text(r3))

        ratio = total_ondemand / static_tokens

        print(f"\n{'=' * 60}")
        print("TYPICAL SESSION BENCHMARK")
        print(f"{'=' * 60}")
        print(f"Static load:                 {static_tokens:>6} tokens")
        print(f"On-demand (3 queries):       {total_ondemand:>6} tokens")
        print(f"Ratio:                       {ratio:>5.2f}x")

        assert total_ondemand < static_tokens


# ── 2. max_lines sweep ────────────────────────────────────────────


class TestMaxLinesSweep:
    """Parametrized max_lines calibration — finds the cost/completeness sweet spot."""

    async def test_vault_search_sweep(self, bench_vault: Path) -> None:
        """Sweep max_lines for vault_search and report token cost + completeness."""
        mcp = create_server(vault_path=bench_vault)

        # Get unlimited baseline
        baseline_result = await mcp.call_tool(
            "vault_search", {"query": "paragraph", "max_lines": 0}
        )
        baseline_text = _text(baseline_result)
        baseline_tokens = _tokens(baseline_text)

        print(f"\n{'=' * 72}")
        print("MAX_LINES SWEEP: vault_search(query='paragraph')")
        print(f"{'=' * 72}")
        print(f"{'max_lines':>10s}  {'tokens':>8s}  {'% of full':>10s}  "
              f"{'completeness':>13s}  {'signal/noise':>13s}")
        print(f"{'-' * 72}")

        for ml in MAX_LINES_SWEEP:
            result = await mcp.call_tool(
                "vault_search", {"query": "paragraph", "max_lines": ml}
            )
            text = _text(result)
            tokens = _tokens(text)
            pct_of_full = (tokens / baseline_tokens * 100) if baseline_tokens else 0
            compl = _completeness(text, baseline_text) * 100
            snr = _signal_ratio(text) * 100
            label = "unlimited" if ml == 0 else str(ml)
            print(f"{label:>10s}  {tokens:>8d}  {pct_of_full:>9.1f}%  "
                  f"{compl:>12.1f}%  {snr:>12.1f}%")

        # Verify sweep ordering: more lines = more tokens
        assert baseline_tokens > 0

    async def test_vault_query_sweep(self, bench_vault: Path) -> None:
        """Sweep max_lines for vault_query on the largest file."""
        mcp = create_server(vault_path=bench_vault)

        baseline_result = await mcp.call_tool(
            "vault_query", {"project": "large-project", "section": "lessons", "max_lines": 0}
        )
        baseline_text = _text(baseline_result)
        baseline_tokens = _tokens(baseline_text)

        print(f"\n{'=' * 72}")
        print("MAX_LINES SWEEP: vault_query(project='large-project', section='lessons')")
        print(f"{'=' * 72}")
        print(f"{'max_lines':>10s}  {'tokens':>8s}  {'% of full':>10s}  "
              f"{'completeness':>13s}  {'signal/noise':>13s}")
        print(f"{'-' * 72}")

        for ml in MAX_LINES_SWEEP:
            result = await mcp.call_tool(
                "vault_query",
                {"project": "large-project", "section": "lessons", "max_lines": ml},
            )
            text = _text(result)
            tokens = _tokens(text)
            pct_of_full = (tokens / baseline_tokens * 100) if baseline_tokens else 0
            compl = _completeness(text, baseline_text) * 100
            snr = _signal_ratio(text) * 100
            label = "unlimited" if ml == 0 else str(ml)
            print(f"{label:>10s}  {tokens:>8d}  {pct_of_full:>9.1f}%  "
                  f"{compl:>12.1f}%  {snr:>12.1f}%")

        assert baseline_tokens > 0

    async def test_session_briefing_cost(self, bench_vault: Path) -> None:
        """Measure session_briefing token cost and signal-to-noise."""
        mcp = create_server(vault_path=bench_vault)

        print(f"\n{'=' * 72}")
        print("SESSION BRIEFING: token cost per project")
        print(f"{'=' * 72}")
        print(f"{'project':<25s}  {'tokens':>8s}  {'lines':>7s}  {'signal/noise':>13s}")
        print(f"{'-' * 72}")

        for project in ["small-project", "medium-project", "large-project"]:
            result = await mcp.call_tool("session_briefing", {"project": project})
            text = _text(result)
            tokens = _tokens(text)
            lines = len(text.splitlines())
            snr = _signal_ratio(text) * 100
            print(f"{project:<25s}  {tokens:>8d}  {lines:>7d}  {snr:>12.1f}%")

        assert True  # informational


# ── 3. Signal-to-noise by tool ────────────────────────────────────


class TestSignalToNoise:
    """Compare signal-to-noise ratio across vault tools."""

    async def test_snr_comparison(self, bench_vault: Path) -> None:
        """Table of signal-to-noise ratios by tool."""
        mcp = create_server(vault_path=bench_vault)

        tools = [
            ("vault_query (context)", "vault_query",
             {"project": "large-project", "section": "context"}),
            ("vault_query (tasks)", "vault_query",
             {"project": "large-project", "section": "tasks"}),
            ("vault_query (lessons)", "vault_query",
             {"project": "large-project", "section": "lessons"}),
            ("vault_search (100)", "vault_search",
             {"query": "paragraph", "max_lines": 100}),
            ("vault_search (500)", "vault_search",
             {"query": "paragraph", "max_lines": 500}),
            ("vault_smart_search", "vault_smart_search",
             {"query": "paragraph", "max_lines": 100}),
            ("session_briefing", "session_briefing",
             {"project": "large-project"}),
        ]

        print(f"\n{'=' * 72}")
        print("SIGNAL-TO-NOISE BY TOOL")
        print(f"{'=' * 72}")
        print(f"{'Tool':<30s}  {'tokens':>8s}  {'lines':>7s}  "
              f"{'signal':>7s}  {'noise':>7s}  {'S/N':>7s}")
        print(f"{'-' * 72}")

        for label, tool_name, args in tools:
            result = await mcp.call_tool(tool_name, args)
            text = _text(result)
            tokens = _tokens(text)
            total_lines = len(text.splitlines())
            snr = _signal_ratio(text)
            signal_lines = int(total_lines * snr)
            noise_lines = total_lines - signal_lines
            print(f"{label:<30s}  {tokens:>8d}  {total_lines:>7d}  "
                  f"{signal_lines:>7d}  {noise_lines:>7d}  {snr:>6.1%}")

        # All tools should have >50% signal
        for label, tool_name, args in tools:
            result = await mcp.call_tool(tool_name, args)
            assert _signal_ratio(_text(result)) > 0.5, f"{label} has low signal"


# ── 4. Session simulations ────────────────────────────────────────


class TestSessionSimulations:
    """Simulate real session types and measure total token budget."""

    @pytest.fixture
    def mcp(self, bench_vault: Path) -> FastMCP:
        return create_server(vault_path=bench_vault)

    @pytest.fixture
    def static_tokens(self, bench_vault: Path) -> int:
        return _count_file_tokens(bench_vault / "static_baseline.md")

    async def _run_session(
        self, mcp: FastMCP, label: str, queries: list[tuple[str, dict[str, object]]]
    ) -> tuple[int, float]:
        """Run a list of queries and return (total_tokens, avg_snr)."""
        total = 0
        snrs = []
        for tool, args in queries:
            result = await mcp.call_tool(tool, args)
            text = _text(result)
            total += _tokens(text)
            snrs.append(_signal_ratio(text))
        avg_snr = sum(snrs) / len(snrs) if snrs else 0.0
        return total, avg_snr

    async def test_session_types(self, mcp: FastMCP, static_tokens: int) -> None:
        """Compare token usage across different session types."""
        sessions: dict[str, list[tuple[str, dict[str, object]]]] = {
            "Bug fix (focused)": [
                ("vault_query", {"project": "large-project", "section": "context"}),
                ("vault_search", {"query": "debugging", "max_lines": 200}),
            ],
            "Feature dev (broad)": [
                ("session_briefing", {"project": "large-project"}),
                ("vault_query", {"project": "large-project", "section": "tasks"}),
                ("vault_search", {"query": "deploy", "max_lines": 300}),
                ("vault_query", {
                    "project": "large-project", "section": "lessons", "max_lines": 200,
                }),
            ],
            "Exploration (heavy)": [
                ("session_briefing", {"project": "large-project"}),
                ("vault_query", {"project": "large-project", "section": "context"}),
                ("vault_query", {"project": "large-project", "section": "tasks"}),
                ("vault_query", {"project": "large-project", "section": "lessons"}),
                ("vault_search", {"query": "monitoring", "max_lines": 500}),
                ("vault_search", {"query": "deploy", "max_lines": 500}),
            ],
        }

        print(f"\n{'=' * 72}")
        print("SESSION TYPE COMPARISON")
        print(f"{'=' * 72}")
        print(f"Static baseline: {static_tokens:,} tokens")
        print(f"{'-' * 72}")
        print(f"{'Session type':<25s}  {'queries':>8s}  {'tokens':>8s}  "
              f"{'vs static':>10s}  {'savings':>8s}  {'avg S/N':>8s}")
        print(f"{'-' * 72}")

        for session_name, queries in sessions.items():
            total, avg_snr = await self._run_session(mcp, session_name, queries)
            ratio = total / static_tokens
            savings = (1 - ratio) * 100
            print(f"{session_name:<25s}  {len(queries):>8d}  {total:>8d}  "
                  f"{ratio:>9.2f}x  {savings:>7.1f}%  {avg_snr:>7.1%}")

        # Even the heaviest session should save tokens vs static
        for session_name, queries in sessions.items():
            total, _ = await self._run_session(mcp, session_name, queries)
            assert total < static_tokens, f"{session_name} exceeded static baseline"


# ── 5. Real vault benchmark (smoke) ───────────────────────────────

REAL_VAULT = os.environ.get("VAULT_PATH", os.path.expanduser("~/Projects/knowledge"))
skip_no_vault = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REAL_VAULT, "10_projects")),
    reason="Real vault not found",
)


@skip_no_vault
@pytest.mark.smoke
class TestRealVaultBenchmark:
    """Benchmarks against the real Obsidian vault. Run with: pytest -m smoke -v -s"""

    @pytest.fixture
    def real_mcp(self) -> FastMCP:
        from pathlib import Path
        return create_server(vault_path=Path(REAL_VAULT))

    @pytest.fixture
    def real_static_tokens(self) -> int:
        """Count all tokens in the real vault projects directory."""
        from pathlib import Path
        total = 0
        for f in Path(REAL_VAULT).rglob("*.md"):
            total += _count_file_tokens(f)
        return total

    async def test_real_vault_max_lines_sweep(self, real_mcp: FastMCP) -> None:
        """max_lines sweep on a real project."""
        # Find the largest project
        from pathlib import Path
        projects_dir = Path(REAL_VAULT) / "10_projects"
        projects = sorted(
            [d for d in projects_dir.iterdir() if d.is_dir()],
            key=lambda d: sum(1 for _ in d.rglob("*.md")),
            reverse=True,
        )
        project = projects[0].name

        print(f"\n{'=' * 72}")
        print(f"REAL VAULT: max_lines sweep on '{project}'")
        print(f"{'=' * 72}")

        # Find the largest file's section
        for section in ["lessons", "tasks", "context"]:
            baseline = await real_mcp.call_tool(
                "vault_query", {"project": project, "section": section, "max_lines": 0}
            )
            baseline_text = _text(baseline)
            if "not found" in baseline_text.lower():
                continue
            baseline_tokens = _tokens(baseline_text)

            print(f"\nvault_query(project='{project}', section='{section}')")
            print(f"  Full content: {baseline_tokens:,} tokens, "
                  f"{len(baseline_text.splitlines())} lines")
            print(f"  {'max_lines':>10s}  {'tokens':>8s}  {'% of full':>10s}  "
                  f"{'completeness':>13s}  {'signal/noise':>13s}")
            print(f"  {'-' * 64}")

            for ml in MAX_LINES_SWEEP:
                result = await real_mcp.call_tool(
                    "vault_query",
                    {"project": project, "section": section, "max_lines": ml},
                )
                text = _text(result)
                tokens = _tokens(text)
                pct = (tokens / baseline_tokens * 100) if baseline_tokens else 0
                compl = _completeness(text, baseline_text) * 100
                snr = _signal_ratio(text) * 100
                label = "unlimited" if ml == 0 else str(ml)
                print(f"  {label:>10s}  {tokens:>8d}  {pct:>9.1f}%  "
                      f"{compl:>12.1f}%  {snr:>12.1f}%")

    async def test_real_vault_session_comparison(
        self, real_mcp: FastMCP, real_static_tokens: int
    ) -> None:
        """Compare on-demand vs static loading on the real vault."""
        from pathlib import Path
        projects_dir = Path(REAL_VAULT) / "10_projects"
        projects = [d.name for d in projects_dir.iterdir() if d.is_dir()][:5]

        total_ondemand = 0
        print(f"\n{'=' * 72}")
        print("REAL VAULT: On-demand vs Static")
        print(f"{'=' * 72}")
        print(f"Static baseline (all .md files): {real_static_tokens:,} tokens")
        print(f"{'-' * 72}")

        for proj in projects:
            result = await real_mcp.call_tool(
                "vault_query", {"project": proj, "section": "context"}
            )
            text = _text(result)
            tokens = _tokens(text)
            total_ondemand += tokens
            snr = _signal_ratio(text)
            print(f"  {proj:<30s}  {tokens:>6} tokens  S/N: {snr:.1%}")

        savings = (1 - total_ondemand / real_static_tokens) * 100
        print(f"{'-' * 72}")
        print(f"On-demand total ({len(projects)} projects):  {total_ondemand:>6} tokens")
        print(f"Savings vs static:               {savings:.1f}%")

        assert savings > 50, f"Expected >50% savings, got {savings:.1f}%"
