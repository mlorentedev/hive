"""Tests for Hive MCP Server tools."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from hive.clients import ClientResponse, ModelInfo
from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP
    from fastmcp.resources.resource import ResourceResult
    from fastmcp.tools import ToolResult

    from hive.budget import BudgetTracker
    from hive.clients import OllamaClient, OpenRouterClient


def _text(result: ToolResult) -> str:
    """Extract text from a ToolResult."""
    return result.content[0].text  # type: ignore[union-attr]


def _resource_text(result: ResourceResult) -> str:
    """Extract text from a ResourceResult."""
    return str(result.contents[0].content)


@pytest.fixture
def vault_mcp(mock_vault: Path) -> FastMCP:
    """Create a vault server backed by mock_vault."""
    return create_server(vault_path=mock_vault)


# ── vault_list_projects ──────────────────────────────────────────────


class TestVaultListProjects:
    async def test_returns_projects(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_list_projects", {})
        assert "testproject" in _text(result)

    async def test_empty_vault(self, tmp_path: Path) -> None:
        (tmp_path / "10_projects").mkdir()
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_list_projects", {})
        assert "No projects found" in _text(result)

    async def test_no_projects_dir(self, tmp_path: Path) -> None:
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_list_projects", {})
        assert "No projects found" in _text(result)

    async def test_multiple_projects(self, mock_vault: Path) -> None:
        second = mock_vault / "10_projects" / "another"
        second.mkdir(parents=True)
        (second / "00-context.md").write_text(
            "---\nid: another\ntype: project\nstatus: active\n---\n\n# Another\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool("vault_list_projects", {})
        assert "testproject" in _text(result)
        assert "another" in _text(result)


# ── vault_query ──────────────────────────────────────────────────────


class TestVaultQuery:
    """Tests for vault_query with shortcuts, paths, _meta, and max_lines."""

    # -- Shortcuts (backward compat) --

    async def test_shortcut_context(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_query", {"project": "testproject"})
        assert "# Test Project" in _text(result)

    async def test_shortcut_tasks(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query", {"project": "testproject", "section": "tasks"}
        )
        assert "Task one" in _text(result)

    async def test_shortcut_lessons(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query", {"project": "testproject", "section": "lessons"}
        )
        assert "Some lesson" in _text(result)

    # -- Path-based access (new) --

    async def test_path_to_adr(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query",
            {"project": "testproject", "path": "30-architecture/adr-001-test.md"},
        )
        text = _text(result)
        assert "ADR-001: Test Decision" in text

    async def test_path_overrides_section(self, vault_mcp: FastMCP) -> None:
        """When both path and section are given, path wins."""
        result = await vault_mcp.call_tool(
            "vault_query",
            {
                "project": "testproject",
                "section": "tasks",
                "path": "30-architecture/adr-001-test.md",
            },
        )
        assert "ADR-001" in _text(result)

    async def test_path_not_found(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query",
            {"project": "testproject", "path": "nonexistent.md"},
        )
        assert "not found" in _text(result).lower()

    # -- _meta for cross-project content (new) --

    async def test_meta_patterns(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query",
            {"project": "_meta", "path": "patterns/pattern-tdd.md"},
        )
        text = _text(result)
        assert "Test-Driven Development" in text

    async def test_meta_not_found(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query",
            {"project": "_meta", "path": "nonexistent.md"},
        )
        assert "not found" in _text(result).lower()

    # -- max_lines (new) --

    async def test_max_lines_truncates(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query",
            {"project": "testproject", "section": "tasks", "max_lines": 3},
        )
        text = _text(result)
        assert "truncated" in text.lower()
        # Content before truncation notice should be limited
        content_lines = text.split("[...")[0].strip().splitlines()
        assert len(content_lines) == 3

    async def test_max_lines_zero_means_unlimited(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query",
            {"project": "testproject", "section": "tasks", "max_lines": 0},
        )
        text = _text(result)
        assert "Task one" in text
        assert "Task two" in text

    # -- Error cases --

    async def test_missing_project(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_query", {"project": "nonexistent"})
        assert "not found" in _text(result).lower()

    async def test_missing_section(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query", {"project": "testproject", "section": "roadmap"}
        )
        assert "not found" in _text(result).lower()

    # -- include_metadata --

    async def test_include_metadata_prepends_line(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query",
            {"project": "testproject", "include_metadata": True},
        )
        text = _text(result)
        assert "**Metadata:**" in text
        assert "type=project" in text
        assert "status=active" in text

    async def test_include_metadata_false_no_line(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query",
            {"project": "testproject", "include_metadata": False},
        )
        assert "**Metadata:**" not in _text(result)

    async def test_include_metadata_no_frontmatter(self, mock_vault: Path) -> None:
        """File without frontmatter should return content without metadata line."""
        bare = mock_vault / "10_projects" / "testproject" / "bare.md"
        bare.write_text("# No frontmatter\nJust text.\n")
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool(
            "vault_query",
            {"project": "testproject", "path": "bare.md", "include_metadata": True},
        )
        text = _text(result)
        assert "**Metadata:**" not in text
        assert "No frontmatter" in text


# ── vault_search ─────────────────────────────────────────────────────


class TestVaultSearch:
    async def test_finds_matching_content(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "Task one"})
        text = _text(result)
        assert "testproject" in text
        assert "11-tasks.md" in text

    async def test_case_insensitive(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "task one"})
        assert "11-tasks.md" in _text(result)

    async def test_no_results(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "xyznonexistent"})
        assert "no matches" in _text(result).lower()

    async def test_searches_across_files(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "active"})
        text = _text(result)
        assert "00-context.md" in text
        assert "11-tasks.md" in text

    async def test_returns_matching_lines(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "Some lesson"})
        assert "Some lesson" in _text(result)

    async def test_max_lines_limits_output(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "active", "max_lines": 5})
        text = _text(result)
        assert "truncated" in text.lower()
        content_lines = text.split("[...")[0].strip().splitlines()
        assert len(content_lines) == 5

    # -- metadata display --

    async def test_shows_metadata_per_file(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "Test Project"})
        text = _text(result)
        assert "[type: project, status: active]" in text

    # -- type_filter --

    async def test_type_filter_includes_matching(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "decided", "type_filter": "adr"}
        )
        text = _text(result)
        assert "adr-001-test" in text

    async def test_type_filter_excludes_non_matching(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "Test", "type_filter": "adr"}
        )
        text = _text(result)
        assert "00-context.md" not in text

    # -- status_filter --

    async def test_status_filter(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "Lesson", "status_filter": "completed"}
        )
        text = _text(result)
        assert "extra-lesson" in text
        assert "90-lessons.md" not in text

    # -- tag_filter --

    async def test_tag_filter_includes_matching(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "timeout", "tag_filter": "networking"}
        )
        text = _text(result)
        assert "timeout-fix.md" in text

    async def test_tag_filter_excludes_non_matching(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "active", "tag_filter": "nonexistent-tag"}
        )
        assert "no matches" in _text(result).lower()

    # -- combined filters --

    async def test_combined_type_and_tag(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search",
            {"query": "Python", "type_filter": "lesson", "tag_filter": "python"},
        )
        text = _text(result)
        assert "extra-lesson" in text

    async def test_filter_skips_files_without_frontmatter(self, mock_vault: Path) -> None:
        bare = mock_vault / "10_projects" / "testproject" / "bare.md"
        bare.write_text("# No frontmatter\nSome active content.\n")
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool(
            "vault_search", {"query": "active", "type_filter": "project"}
        )
        text = _text(result)
        assert "bare.md" not in text


# ── vault_health ─────────────────────────────────────────────────────


class TestVaultHealth:
    async def test_returns_project_stats(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_health", {})
        assert "testproject" in _text(result)

    async def test_reports_file_count(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_health", {})
        # testproject has 7 files (context, tasks, lessons, adr-001,
        # timeout-fix, extra-lesson, large-doc)
        assert "7" in _text(result)

    async def test_reports_total_lines(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_health", {})
        assert "line" in _text(result).lower()

    async def test_empty_vault(self, tmp_path: Path) -> None:
        (tmp_path / "10_projects").mkdir()
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_health", {})
        assert "no projects" in _text(result).lower()

    # -- stale detection --

    async def test_stale_file_detected(self, tmp_path: Path) -> None:
        project = tmp_path / "10_projects" / "staleproj"
        project.mkdir(parents=True)
        (project / "old.md").write_text(
            '---\nid: old\ntype: lesson\nstatus: active\ncreated: "2024-01-01"\n---\n\n# Old\n'
        )
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_health", {})
        assert "stale files" in _text(result).lower()
        assert "old.md" in _text(result)

    async def test_terminal_status_not_stale(self, tmp_path: Path) -> None:
        project = tmp_path / "10_projects" / "termproj"
        project.mkdir(parents=True)
        (project / "done.md").write_text(
            '---\nid: done\ntype: adr\nstatus: completed\ncreated: "2020-01-01"\n---\n\n# Done\n'
        )
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_health", {})
        assert "stale" not in _text(result).lower()

    async def test_recent_file_not_stale(self, tmp_path: Path) -> None:
        from datetime import date

        project = tmp_path / "10_projects" / "freshproj"
        project.mkdir(parents=True)
        today = date.today().isoformat()
        (project / "fresh.md").write_text(
            f'---\nid: fresh\ntype: project\nstatus: active\ncreated: "{today}"\n---\n\n# Fresh\n'
        )
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_health", {})
        assert "stale" not in _text(result).lower()

    async def test_stale_fallback_to_mtime(self, tmp_path: Path) -> None:
        import os

        project = tmp_path / "10_projects" / "mtimeproj"
        project.mkdir(parents=True)
        f = project / "no-created.md"
        f.write_text("---\nid: nc\ntype: lesson\nstatus: active\n---\n\n# No date\n")
        # Set mtime to 1 year ago
        old_time = os.path.getmtime(str(f)) - 365 * 86400
        os.utime(str(f), (old_time, old_time))
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_health", {})
        assert "stale files" in _text(result).lower()
        assert "no-created.md" in _text(result)


# ── vault_update (with real YAML frontmatter validation) ─────────────


class TestVaultUpdate:
    async def test_append_to_existing_file(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_update",
            {
                "project": "testproject",
                "section": "lessons",
                "operation": "append",
                "content": "\n## Entry 2\nNew lesson learned.\n",
            },
        )
        assert "updated" in _text(result).lower()
        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        assert "Entry 2" in lessons

    async def test_replace_section_content(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_update",
            {
                "project": "testproject",
                "section": "tasks",
                "operation": "replace",
                "content": (
                    "---\nid: testproject-tasks\ntype: project-tasks\n"
                    "status: active\n---\n\n# Replaced\n"
                ),
            },
        )
        assert "updated" in _text(result).lower()
        tasks = (git_vault / "10_projects" / "testproject" / "11-tasks.md").read_text()
        assert "# Replaced" in tasks
        assert "Task one" not in tasks

    async def test_replace_rejects_missing_frontmatter(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_update",
            {
                "project": "testproject",
                "section": "tasks",
                "operation": "replace",
                "content": "# No frontmatter here\n",
            },
        )
        assert "frontmatter" in _text(result).lower()
        # File unchanged
        tasks = (git_vault / "10_projects" / "testproject" / "11-tasks.md").read_text()
        assert "Task one" in tasks

    async def test_replace_rejects_invalid_yaml(self, git_vault: Path) -> None:
        """YAML that parses but lacks required fields should be rejected."""
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_update",
            {
                "project": "testproject",
                "section": "tasks",
                "operation": "replace",
                "content": "---\ntitle: no id or type\n---\n\n# Bad\n",
            },
        )
        text = _text(result)
        assert "id" in text.lower() or "required" in text.lower()

    async def test_replace_rejects_malformed_yaml(self, git_vault: Path) -> None:
        """Content starting with --- but containing invalid YAML."""
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_update",
            {
                "project": "testproject",
                "section": "tasks",
                "operation": "replace",
                "content": "---\n: broken: yaml: [[\n---\n\n# Bad\n",
            },
        )
        assert "frontmatter" in _text(result).lower() or "yaml" in _text(result).lower()

    async def test_auto_commits_to_git(self, git_vault: Path) -> None:
        import subprocess

        mcp = create_server(vault_path=git_vault)
        await mcp.call_tool(
            "vault_update",
            {
                "project": "testproject",
                "section": "lessons",
                "operation": "append",
                "content": "\n## Git test\nCommitted.\n",
            },
        )
        log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=git_vault,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "testproject" in log.stdout.lower()

    async def test_missing_project(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_update",
            {
                "project": "nonexistent",
                "section": "tasks",
                "operation": "append",
                "content": "stuff",
            },
        )
        assert "not found" in _text(result).lower()

    async def test_invalid_operation(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_update",
            {
                "project": "testproject",
                "section": "tasks",
                "operation": "delete",
                "content": "stuff",
            },
        )
        assert "invalid" in _text(result).lower() or "operation" in _text(result).lower()


# ── vault_create ─────────────────────────────────────────────────────


class TestVaultCreate:
    async def test_create_new_adr(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_create",
            {
                "project": "testproject",
                "path": "30-architecture/adr-002-new.md",
                "content": "# ADR-002: New Decision\n\nWe decided something new.\n",
                "doc_type": "adr",
            },
        )
        text = _text(result)
        assert "created" in text.lower()

        filepath = git_vault / "10_projects" / "testproject" / "30-architecture" / "adr-002-new.md"
        assert filepath.exists()
        content = filepath.read_text()
        assert "---" in content
        assert "type: adr" in content
        assert "ADR-002: New Decision" in content

    async def test_create_lesson(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_create",
            {
                "project": "testproject",
                "path": "92-new-lesson.md",
                "content": "# New Lesson\n\nLearned something.\n",
                "doc_type": "lesson",
            },
        )
        assert "created" in _text(result).lower()

    async def test_rejects_existing_file(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_create",
            {
                "project": "testproject",
                "path": "00-context.md",
                "content": "# Overwrite attempt\n",
                "doc_type": "project",
            },
        )
        assert "exists" in _text(result).lower()

    async def test_auto_generates_frontmatter(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        await mcp.call_tool(
            "vault_create",
            {
                "project": "testproject",
                "path": "50-troubleshooting/error-timeout.md",
                "content": "# Timeout Error\n\nFix: increase timeout.\n",
                "doc_type": "troubleshooting",
            },
        )
        filepath = (
            git_vault / "10_projects" / "testproject" / "50-troubleshooting" / "error-timeout.md"
        )
        content = filepath.read_text()
        assert content.startswith("---\n")
        assert "id:" in content
        assert "type: troubleshooting" in content
        assert "status:" in content

    async def test_auto_commits(self, git_vault: Path) -> None:
        import subprocess

        mcp = create_server(vault_path=git_vault)
        await mcp.call_tool(
            "vault_create",
            {
                "project": "testproject",
                "path": "new-file.md",
                "content": "# New\n",
                "doc_type": "lesson",
            },
        )
        log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=git_vault,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "testproject" in log.stdout.lower()

    async def test_create_in_meta(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_create",
            {
                "project": "_meta",
                "path": "patterns/pattern-new.md",
                "content": "# New Pattern\n\nDo this always.\n",
                "doc_type": "pattern",
            },
        )
        assert "created" in _text(result).lower()
        filepath = git_vault / "00_meta" / "patterns" / "pattern-new.md"
        assert filepath.exists()

    async def test_missing_project(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_create",
            {
                "project": "nonexistent",
                "path": "new.md",
                "content": "# New\n",
                "doc_type": "lesson",
            },
        )
        assert "not found" in _text(result).lower()


# ── vault_summarize ──────────────────────────────────────────────────


class TestVaultSummarize:
    async def test_small_file_returns_content(self, vault_mcp: FastMCP) -> None:
        """Files ≤50 lines return content directly, no delegation prompt."""
        result = await vault_mcp.call_tool(
            "vault_summarize", {"project": "testproject", "section": "context"}
        )
        text = _text(result)
        assert "# Test Project" in text
        assert "delegate_task" not in text

    async def test_small_file_includes_metadata(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_summarize", {"project": "testproject", "section": "context"}
        )
        text = _text(result)
        assert "**Metadata:**" in text
        assert "type=project" in text

    async def test_large_file_returns_delegation_prompt(self, vault_mcp: FastMCP) -> None:
        """Files >50 lines return a structured delegation prompt."""
        result = await vault_mcp.call_tool(
            "vault_summarize",
            {"project": "testproject", "path": "92-large-doc.md"},
        )
        text = _text(result)
        assert "delegate_task" in text
        assert "Summarization Request" in text
        assert "Document body" in text

    async def test_large_file_includes_metadata_in_prompt(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_summarize",
            {"project": "testproject", "path": "92-large-doc.md"},
        )
        text = _text(result)
        assert "type=lesson" in text
        assert "status=active" in text

    async def test_large_file_includes_body(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_summarize",
            {"project": "testproject", "path": "92-large-doc.md"},
        )
        text = _text(result)
        assert "Line 1:" in text
        assert "Line 80:" in text

    async def test_custom_max_summary_lines(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_summarize",
            {"project": "testproject", "path": "92-large-doc.md", "max_summary_lines": 10},
        )
        text = _text(result)
        assert "10 lines" in text

    async def test_default_max_summary_lines(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_summarize",
            {"project": "testproject", "path": "92-large-doc.md"},
        )
        assert "20 lines" in _text(result)

    async def test_missing_project(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_summarize", {"project": "nonexistent"}
        )
        assert "not found" in _text(result).lower()

    async def test_missing_file(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_summarize", {"project": "testproject", "path": "nope.md"}
        )
        assert "not found" in _text(result).lower()

    async def test_path_overrides_section(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_summarize",
            {"project": "testproject", "section": "tasks", "path": "92-large-doc.md"},
        )
        text = _text(result)
        assert "Large Document" in text or "delegate_task" in text

    async def test_file_without_frontmatter(self, mock_vault: Path) -> None:
        bare = mock_vault / "10_projects" / "testproject" / "bare.md"
        bare.write_text("# Bare\nJust text.\n")
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool(
            "vault_summarize", {"project": "testproject", "path": "bare.md"}
        )
        text = _text(result)
        assert "# Bare" in text
        assert "**Metadata:**" not in text


# ── vault_smart_search ───────────────────────────────────────────────


class TestVaultSmartSearch:
    async def test_finds_matching_files(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_smart_search", {"query": "Task one"}
        )
        text = _text(result)
        assert "11-tasks.md" in text
        assert "score:" in text

    async def test_no_results(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_smart_search", {"query": "xyznonexistent"}
        )
        assert "no matches" in _text(result).lower()

    async def test_active_ranks_above_terminal(self, vault_mcp: FastMCP) -> None:
        """Active files should score higher than completed/accepted files."""
        result = await vault_mcp.call_tool(
            "vault_smart_search", {"query": "Lesson"}
        )
        text = _text(result)
        lines = text.splitlines()
        score_lines = [ln for ln in lines if "score:" in ln]
        # 90-lessons.md (active) should appear before 91-extra-lesson.md (completed)
        assert len(score_lines) >= 2
        lessons_idx = next(i for i, ln in enumerate(score_lines) if "90-lessons" in ln)
        extra_idx = next(i for i, ln in enumerate(score_lines) if "extra-lesson" in ln)
        assert lessons_idx < extra_idx

    async def test_higher_match_density_ranks_first(self, mock_vault: Path) -> None:
        """File with more matches should rank higher."""
        many = mock_vault / "10_projects" / "testproject" / "many-matches.md"
        many.write_text(
            "---\nid: many\ntype: lesson\nstatus: active\n---\n\n"
            "alpha alpha alpha\nalpha again\nalpha more\n"
        )
        few = mock_vault / "10_projects" / "testproject" / "few-matches.md"
        few.write_text(
            "---\nid: few\ntype: lesson\nstatus: active\n---\n\n"
            "alpha once\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool("vault_smart_search", {"query": "alpha"})
        text = _text(result)
        score_lines = [ln for ln in text.splitlines() if "score:" in ln]
        many_idx = next(i for i, ln in enumerate(score_lines) if "many-matches" in ln)
        few_idx = next(i for i, ln in enumerate(score_lines) if "few-matches" in ln)
        assert many_idx < few_idx

    async def test_shows_metadata_per_result(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_smart_search", {"query": "Test Project"}
        )
        text = _text(result)
        assert "type=project" in text
        assert "status=active" in text

    async def test_max_results_limits_output(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_smart_search", {"query": "active", "max_results": 2}
        )
        text = _text(result)
        score_lines = [ln for ln in text.splitlines() if "score:" in ln]
        assert len(score_lines) <= 2

    async def test_max_lines_truncates(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_smart_search", {"query": "active", "max_lines": 5}
        )
        text = _text(result)
        assert "truncated" in text.lower()

    async def test_case_insensitive(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_smart_search", {"query": "task ONE"}
        )
        assert "11-tasks.md" in _text(result)

    async def test_files_without_frontmatter_searchable(self, mock_vault: Path) -> None:
        bare = mock_vault / "10_projects" / "testproject" / "bare.md"
        bare.write_text("# Bare\nSearchable bare content.\n")
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool(
            "vault_smart_search", {"query": "Searchable bare"}
        )
        text = _text(result)
        assert "bare.md" in text
        assert "score:" in text

    async def test_matching_lines_limited_to_five(self, mock_vault: Path) -> None:
        lines = ["---\nid: verbose\ntype: lesson\nstatus: active\n---\n"]
        for i in range(10):
            lines.append(f"keyword line {i}")
        (mock_vault / "10_projects" / "testproject" / "verbose.md").write_text(
            "\n".join(lines) + "\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool("vault_smart_search", {"query": "keyword"})
        text = _text(result)
        # Should show at most 5 matching lines per file
        match_lines = [ln for ln in text.splitlines() if ln.strip().startswith("- keyword")]
        assert len(match_lines) <= 5


# ── Prompts ─────────────────────────────────────────────────────────


class TestPrompts:
    """Tests for MCP prompts registered via @mcp.prompt."""

    @staticmethod
    def _prompt_text(result: object) -> str:
        """Extract text from a PromptResult, handling TextContent wrapper."""
        content = result.messages[0].content  # type: ignore[attr-defined]
        return content.text if hasattr(content, "text") else str(content)

    async def test_all_prompts_registered(self, vault_mcp: FastMCP) -> None:
        prompts = await vault_mcp.list_prompts()
        names = {p.name for p in prompts}
        assert names == {"retrospective", "delegate", "vault_sync", "benchmark"}

    # -- retrospective --

    async def test_retrospective_contains_protocol(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.render_prompt("retrospective", {"project": "hive"})
        text = self._prompt_text(result)
        assert "vault_query" in text
        assert "vault_update" in text
        assert "**Context:**" in text

    async def test_retrospective_interpolates_project(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.render_prompt("retrospective", {"project": "myproject"})
        text = self._prompt_text(result)
        assert "myproject" in text
        assert "<repo>" not in text

    # -- delegate --

    async def test_delegate_contains_suitability_matrix(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.render_prompt("delegate", {"task": "summarize docs"})
        text = self._prompt_text(result)
        assert "Delegatable" in text
        assert "NOT Delegatable" in text

    async def test_delegate_contains_tools(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.render_prompt("delegate", {"task": "summarize docs"})
        text = self._prompt_text(result)
        assert "delegate_task" in text
        assert "worker_status" in text

    async def test_delegate_interpolates_task(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.render_prompt("delegate", {"task": "generate boilerplate"})
        text = self._prompt_text(result)
        assert "generate boilerplate" in text

    # -- vault_sync --

    async def test_vault_sync_contains_protocol(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.render_prompt("vault_sync", {"project": "hive"})
        text = self._prompt_text(result)
        assert "vault_health" in text
        assert "vault_update" in text

    async def test_vault_sync_interpolates_project(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.render_prompt("vault_sync", {"project": "testproj"})
        text = self._prompt_text(result)
        assert "testproj" in text

    # -- benchmark --

    async def test_benchmark_contains_protocol(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.render_prompt("benchmark", {})
        text = self._prompt_text(result)
        assert "worker_status" in text
        assert "10 tokens per line" in text

    async def test_benchmark_has_no_required_args(self, vault_mcp: FastMCP) -> None:
        prompts = await vault_mcp.list_prompts()
        bench = next(p for p in prompts if p.name == "benchmark")
        required = [a for a in (bench.arguments or []) if a.required]
        assert len(required) == 0


# ── Resources ────────────────────────────────────────────────────────


class TestResources:
    async def test_static_resources_registered(self, vault_mcp: FastMCP) -> None:
        resources = await vault_mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        assert "hive://projects" in uris or "hive://projects/" in uris
        assert "hive://health" in uris or "hive://health/" in uris

    async def test_templates_registered(self, vault_mcp: FastMCP) -> None:
        templates = await vault_mcp.list_resource_templates()
        patterns = {t.uri_template for t in templates}
        assert any("context" in p for p in patterns)
        assert any("tasks" in p for p in patterns)
        assert any("lessons" in p for p in patterns)

    async def test_projects_resource(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.read_resource("hive://projects")
        assert "testproject" in _resource_text(result)

    async def test_health_resource(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.read_resource("hive://health")
        assert "testproject" in _resource_text(result)

    async def test_context_template(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.read_resource("hive://projects/testproject/context")
        assert "# Test Project" in _resource_text(result)

    async def test_tasks_template(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.read_resource("hive://projects/testproject/tasks")
        assert "Task one" in _resource_text(result)

    async def test_lessons_template(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.read_resource("hive://projects/testproject/lessons")
        assert "Some lesson" in _resource_text(result)

    async def test_nonexistent_project(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.read_resource("hive://projects/nonexistent/context")
        assert "not found" in _resource_text(result).lower()


# ── session_briefing ─────────────────────────────────────────────────


class TestSessionBriefing:
    async def test_returns_tasks(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool("session_briefing", {"project": "testproject"})
        assert "Task one" in _text(result)

    async def test_returns_lessons(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool("session_briefing", {"project": "testproject"})
        assert "Some lesson" in _text(result)

    async def test_returns_git_log_section(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool("session_briefing", {"project": "testproject"})
        text = _text(result)
        assert "## Recent Vault Activity" in text

    async def test_returns_health(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool("session_briefing", {"project": "testproject"})
        assert "Files:" in _text(result)

    async def test_missing_project(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool("session_briefing", {"project": "nonexistent"})
        assert "not found" in _text(result).lower()


# ── relevance tracking ───────────────────────────────────────────────


class TestRelevanceTracking:
    """Tests for relevance recording in vault tools."""

    async def test_vault_query_records_relevance(self, git_vault: Path) -> None:
        from hive.relevance import RelevanceTracker

        relevance = RelevanceTracker()
        mcp = create_server(vault_path=git_vault, relevance_tracker=relevance)
        await mcp.call_tool("vault_query", {"project": "testproject", "section": "tasks"})
        scores = relevance.get_scores("testproject")
        assert "tasks" in scores

    async def test_vault_update_records_write_boost(self, git_vault: Path) -> None:
        from hive.relevance import RelevanceTracker

        relevance = RelevanceTracker()
        mcp = create_server(vault_path=git_vault, relevance_tracker=relevance)
        await mcp.call_tool("vault_update", {
            "project": "testproject",
            "section": "lessons",
            "operation": "append",
            "content": "\n## New Lesson\nTest.",
        })
        scores = relevance.get_scores("testproject")
        assert "lessons" in scores

    async def test_briefing_tracks_sections(self, git_vault: Path) -> None:
        from hive.relevance import RelevanceTracker

        relevance = RelevanceTracker()
        mcp = create_server(vault_path=git_vault, relevance_tracker=relevance)
        await mcp.call_tool("session_briefing", {"project": "testproject"})
        scores = relevance.get_scores("testproject")
        assert len(scores) > 0


class TestAdaptiveBriefing:
    """Tests for relevance-based section ordering in session_briefing."""

    async def test_cold_start_includes_defaults(self, git_vault: Path) -> None:
        """With no history, briefing should include default sections."""
        from hive.relevance import RelevanceTracker

        relevance = RelevanceTracker()
        mcp = create_server(vault_path=git_vault, relevance_tracker=relevance)
        result = await mcp.call_tool("session_briefing", {"project": "testproject"})
        text = _text(result)
        assert "Tasks" in text
        assert "Lessons" in text

    async def test_briefing_prioritizes_high_score_sections(
        self, git_vault: Path,
    ) -> None:
        """After repeated task queries, briefing should show tasks first."""
        from hive.relevance import RelevanceTracker

        relevance = RelevanceTracker()
        mcp = create_server(vault_path=git_vault, relevance_tracker=relevance)
        # Simulate heavy tasks usage
        for _ in range(5):
            relevance.record_access("testproject", "tasks")
        result = await mcp.call_tool("session_briefing", {"project": "testproject"})
        text = _text(result)
        tasks_pos = text.find("Tasks")
        lessons_pos = text.find("Lessons")
        assert tasks_pos < lessons_pos

    async def test_briefing_reorders_when_lessons_dominate(
        self, git_vault: Path,
    ) -> None:
        """When lessons are accessed more, they should appear before tasks."""
        from hive.relevance import RelevanceTracker

        relevance = RelevanceTracker()
        mcp = create_server(vault_path=git_vault, relevance_tracker=relevance)
        for _ in range(10):
            relevance.record_access("testproject", "lessons")
        result = await mcp.call_tool("session_briefing", {"project": "testproject"})
        text = _text(result)
        tasks_pos = text.find("Tasks")
        lessons_pos = text.find("Lessons")
        assert lessons_pos < tasks_pos


class TestDecayOnBriefing:
    """Verify session_briefing applies decay to prevent stale scores."""

    async def test_briefing_applies_decay(self, git_vault: Path) -> None:
        from hive.relevance import RelevanceTracker

        relevance = RelevanceTracker()
        # Record access for a section NOT in briefing so decay isn't offset
        relevance.record_access("testproject", "roadmap")
        score_before = relevance.get_scores("testproject")["roadmap"]
        mcp = create_server(vault_path=git_vault, relevance_tracker=relevance)
        await mcp.call_tool("session_briefing", {"project": "testproject"})
        # Decay should reduce roadmap score (briefing doesn't re-access it)
        score_after = relevance.get_scores("testproject")["roadmap"]
        assert score_after < score_before


# ── vault_recent ─────────────────────────────────────────────────────


class TestVaultRecent:
    async def test_recent_git_change_appears(self, git_vault: Path) -> None:
        import subprocess

        new_file = git_vault / "10_projects" / "testproject" / "new-note.md"
        new_file.write_text(
            "---\nid: new-note\ntype: lesson\nstatus: active\n---\n\n# New\n"
        )
        subprocess.run(["git", "add", "."], cwd=git_vault, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add note"],
            cwd=git_vault, capture_output=True, check=True,
        )
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool("vault_recent", {"since_days": 1})
        assert "new-note.md" in _text(result)

    async def test_project_filter(self, git_vault: Path) -> None:
        import subprocess

        # Add files in two projects
        second = git_vault / "10_projects" / "other"
        second.mkdir(parents=True)
        (second / "note.md").write_text(
            "---\nid: other-note\ntype: lesson\nstatus: active\n---\n\n# Other\n"
        )
        subprocess.run(["git", "add", "."], cwd=git_vault, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add other"],
            cwd=git_vault, capture_output=True, check=True,
        )
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_recent", {"since_days": 1, "project": "testproject"}
        )
        text = _text(result)
        assert "other" not in text.lower() or "testproject" in text

    async def test_frontmatter_created_today(self, mock_vault: Path) -> None:
        from datetime import date

        today = date.today().isoformat()
        (mock_vault / "10_projects" / "testproject" / "today-note.md").write_text(
            f'---\nid: today-note\ntype: lesson\nstatus: active\ncreated: "{today}"\n'
            f"---\n\n# Today\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool("vault_recent", {"since_days": 1})
        assert "today-note.md" in _text(result)

    async def test_no_changes_returns_message(self, tmp_path: Path) -> None:
        """Empty vault with no git and no recent frontmatter dates."""
        project = tmp_path / "10_projects" / "emptyproj"
        project.mkdir(parents=True)
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_recent", {"since_days": 1})
        assert "no changes" in _text(result).lower()

    async def test_output_truncated(self, git_vault: Path) -> None:
        import subprocess

        # Create many files to exceed 100 lines
        project = git_vault / "10_projects" / "testproject"
        for i in range(120):
            (project / f"bulk-{i:03d}.md").write_text(
                f"---\nid: bulk-{i}\ntype: lesson\nstatus: active\n---\n\n# Bulk {i}\n"
            )
        subprocess.run(["git", "add", "."], cwd=git_vault, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "bulk add"],
            cwd=git_vault, capture_output=True, check=True,
        )
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool("vault_recent", {"since_days": 1})
        assert "truncated" in _text(result).lower()


# ── vault_usage ──────────────────────────────────────────────────────


class TestVaultUsage:
    async def test_tracks_tool_calls(self, vault_mcp: FastMCP) -> None:
        """Tool calls should be recorded in the usage tracker."""
        await vault_mcp.call_tool("vault_list_projects", {})
        await vault_mcp.call_tool("vault_query", {"project": "testproject"})
        result = await vault_mcp.call_tool("vault_usage", {"since_days": 1})
        text = _text(result)
        assert "vault_list_projects" in text
        assert "vault_query" in text
        # vault_usage itself is also tracked
        assert "Total calls:" in text

    async def test_tracks_project(self, vault_mcp: FastMCP) -> None:
        await vault_mcp.call_tool("vault_query", {"project": "testproject"})
        result = await vault_mcp.call_tool("vault_usage", {"since_days": 1})
        assert "testproject" in _text(result)

    async def test_empty_usage(self, tmp_path: Path) -> None:
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_usage", {"since_days": 1})
        assert "no vault tool calls" in _text(result).lower()

    async def test_estimates_tokens(self, vault_mcp: FastMCP) -> None:
        await vault_mcp.call_tool("vault_query", {"project": "testproject"})
        result = await vault_mcp.call_tool("vault_usage", {"since_days": 1})
        assert "tokens served" in _text(result).lower()


# ── Worker fixture ──────────────────────────────────────────────────


@pytest.fixture
def worker(
    mock_vault: Path,
    budget: BudgetTracker,
    ollama: OllamaClient,
    openrouter: OpenRouterClient,
) -> FastMCP:
    """Create a unified server with worker deps for worker-specific tests."""
    return create_server(
        vault_path=mock_vault,
        budget_tracker=budget,
        ollama_client=ollama,
        openrouter_client=openrouter,
    )


# ── delegate_task: auto routing ─────────────────────────────────────


class TestDelegateTaskAutoRouting:
    """Auto routing: Ollama first, then OpenRouter free, then paid."""

    @pytest.mark.asyncio
    async def test_ollama_first_when_available(
        self, worker: FastMCP, ollama: OllamaClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=ClientResponse(
                text="hello world",
                model="qwen2.5-coder:7b",
                tokens=10,
                cost_usd=0.0,
                latency_ms=200,
            )
        )
        result = _text(await worker.call_tool("delegate_task", {"prompt": "say hello"}))
        assert "hello world" in result
        assert "qwen2.5-coder:7b" in result

    @pytest.mark.asyncio
    async def test_fallback_to_openrouter_free_when_ollama_down(
        self, worker: FastMCP, ollama: OllamaClient, openrouter: OpenRouterClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]
        openrouter.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=ClientResponse(
                text="from openrouter",
                model="qwen/qwen3-coder:free",
                tokens=50,
                cost_usd=0.0,
                latency_ms=800,
            )
        )
        result = _text(await worker.call_tool("delegate_task", {"prompt": "test"}))
        assert "from openrouter" in result

    @pytest.mark.asyncio
    async def test_ollama_error_falls_to_openrouter(
        self, worker: FastMCP, ollama: OllamaClient, openrouter: OpenRouterClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(side_effect=ConnectionError("ollama failed"))  # type: ignore[method-assign]
        openrouter.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=ClientResponse(
                text="fallback ok",
                model="qwen/qwen3-coder:free",
                tokens=30,
                cost_usd=0.0,
                latency_ms=500,
            )
        )
        result = _text(await worker.call_tool("delegate_task", {"prompt": "test"}))
        assert "fallback ok" in result

    @pytest.mark.asyncio
    async def test_all_unavailable_returns_reject(
        self, worker: FastMCP, ollama: OllamaClient, openrouter: OpenRouterClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]
        openrouter.generate = AsyncMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]
        result = _text(await worker.call_tool("delegate_task", {"prompt": "test"}))
        assert "The host should handle this task directly" in result


# ── delegate_task: budget enforcement ───────────────────────────────


class TestDelegateTaskBudget:
    """Budget cap enforcement for paid models."""

    @pytest.mark.asyncio
    async def test_max_cost_zero_skips_paid(
        self, worker: FastMCP, ollama: OllamaClient, openrouter: OpenRouterClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]
        # Free model fails
        openrouter.generate = AsyncMock(side_effect=RuntimeError("rate limit"))  # type: ignore[method-assign]
        result = _text(
            await worker.call_tool("delegate_task", {"prompt": "test", "max_cost_per_request": 0.0})
        )
        assert "The host should handle this task directly" in result

    @pytest.mark.asyncio
    async def test_max_cost_allows_paid_fallback(
        self,
        worker: FastMCP,
        ollama: OllamaClient,
        openrouter: OpenRouterClient,
    ) -> None:
        ollama.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]

        call_count = 0

        async def _side_effect(*args: object, **kwargs: object) -> ClientResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: free model fails
                raise RuntimeError("rate limit")
            # Second call: paid model succeeds
            return ClientResponse(
                text="paid result",
                model="deepseek/deepseek-v3",
                tokens=100,
                cost_usd=0.03,
                latency_ms=1000,
            )

        openrouter.generate = AsyncMock(side_effect=_side_effect)  # type: ignore[method-assign]

        result = _text(
            await worker.call_tool(
                "delegate_task", {"prompt": "test", "max_cost_per_request": 0.05}
            )
        )
        assert "paid result" in result

    @pytest.mark.asyncio
    async def test_budget_exhausted_rejects_paid(
        self,
        worker: FastMCP,
        budget: BudgetTracker,
        ollama: OllamaClient,
        openrouter: OpenRouterClient,
    ) -> None:
        # Exhaust budget
        budget.record_request("m", cost_usd=5.0, tokens=100, latency_ms=100, task_type="general")
        ollama.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]
        openrouter.generate = AsyncMock(side_effect=RuntimeError("rate limit"))  # type: ignore[method-assign]

        result = _text(
            await worker.call_tool(
                "delegate_task", {"prompt": "test", "max_cost_per_request": 0.10}
            )
        )
        assert "The host should handle this task directly" in result


# ── delegate_task: explicit model ───────────────────────────────────


class TestDelegateTaskExplicitModel:
    """Explicit model selection bypasses auto-routing."""

    @pytest.mark.asyncio
    async def test_explicit_ollama(self, worker: FastMCP, ollama: OllamaClient) -> None:
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=ClientResponse(
                text="explicit ollama",
                model="qwen2.5-coder:7b",
                tokens=20,
                cost_usd=0.0,
                latency_ms=150,
            )
        )
        result = _text(
            await worker.call_tool("delegate_task", {"prompt": "test", "model": "ollama"})
        )
        assert "explicit ollama" in result

    @pytest.mark.asyncio
    async def test_explicit_openrouter_free(
        self, worker: FastMCP, openrouter: OpenRouterClient
    ) -> None:
        openrouter.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=ClientResponse(
                text="explicit free",
                model="qwen/qwen3-coder:free",
                tokens=30,
                cost_usd=0.0,
                latency_ms=400,
            )
        )
        result = _text(
            await worker.call_tool("delegate_task", {"prompt": "test", "model": "openrouter-free"})
        )
        assert "explicit free" in result


# ── delegate_task: records to budget tracker ────────────────────────


class TestDelegateTaskRecording:
    """Successful requests are recorded in the budget tracker."""

    @pytest.mark.asyncio
    async def test_records_on_success(
        self, worker: FastMCP, budget: BudgetTracker, ollama: OllamaClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=ClientResponse(
                text="ok", model="qwen2.5-coder:7b", tokens=10, cost_usd=0.0, latency_ms=100
            )
        )
        await worker.call_tool("delegate_task", {"prompt": "test"})
        assert budget.month_stats(5.0)["request_count"] == 1


# ── list_models ─────────────────────────────────────────────────────


class TestListModels:
    """list_models tool combines Ollama + OpenRouter info."""

    @pytest.mark.asyncio
    async def test_list_models_output(
        self, worker: FastMCP, ollama: OllamaClient, openrouter: OpenRouterClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        openrouter.list_models = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                ModelInfo(
                    id="qwen/qwen3-coder:free",
                    name="Qwen3 Coder",
                    context_length=65536,
                    cost_per_million_input=0.0,
                    cost_per_million_output=0.0,
                    is_free=True,
                ),
            ]
        )
        result = _text(await worker.call_tool("list_models", {}))
        assert "qwen2.5-coder:7b" in result
        assert "qwen/qwen3-coder:free" in result

    @pytest.mark.asyncio
    async def test_list_models_ollama_down(
        self, worker: FastMCP, ollama: OllamaClient, openrouter: OpenRouterClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]
        openrouter.list_models = AsyncMock(return_value=[])  # type: ignore[method-assign]
        result = _text(await worker.call_tool("list_models", {}))
        assert "offline" in result.lower() or "unavailable" in result.lower()


# ── worker_status ───────────────────────────────────────────────────


class TestWorkerStatus:
    """worker_status tool shows budget + connectivity."""

    @pytest.mark.asyncio
    async def test_status_shows_budget(
        self,
        worker: FastMCP,
        budget: BudgetTracker,
        ollama: OllamaClient,
        openrouter: OpenRouterClient,
    ) -> None:
        budget.record_request("m", cost_usd=1.23, tokens=100, latency_ms=100, task_type="general")
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        result = _text(await worker.call_tool("worker_status", {}))
        assert "1.23" in result
        assert "$1.0" in result

    @pytest.mark.asyncio
    async def test_status_shows_ollama_connectivity(
        self, worker: FastMCP, ollama: OllamaClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]
        result = _text(await worker.call_tool("worker_status", {}))
        assert "offline" in result.lower() or "unavailable" in result.lower()


# ── Multi-scope vault tests ─────────────────────────────────────────

MULTI_SCOPES = {"projects": "10_projects", "meta": "00_meta", "work": "50_work"}


class TestMultiScopeListProjects:
    async def test_lists_from_all_scopes(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_list_projects", {}))
        assert "projects/testproject" in result
        assert "work/my-company" in result

    async def test_missing_scope_silently_skipped(self, mock_vault: Path) -> None:
        scopes = {**MULTI_SCOPES, "extra": "99_nonexistent"}
        mcp = create_server(vault_path=mock_vault, vault_scopes=scopes)
        result = _text(await mcp.call_tool("vault_list_projects", {}))
        assert "projects/testproject" in result
        assert "99_nonexistent" not in result

    async def test_backward_compat(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        result = _text(await mcp.call_tool("vault_list_projects", {}))
        assert "testproject" in result


class TestMultiScopeQuery:
    async def test_auto_scan_finds_work_project(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool(
            "vault_query", {"project": "my-company", "section": "context"},
        ))
        assert "My Company" in result

    async def test_explicit_scope(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool(
            "vault_query", {"project": "work:my-company", "section": "context"},
        ))
        assert "My Company" in result

    async def test_explicit_wrong_scope(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool(
            "vault_query", {"project": "projects:my-company", "section": "context"},
        ))
        assert "not found" in result.lower()

    async def test_first_match_wins(self, multi_scope_vault: Path) -> None:
        # Create a duplicate project name in the work scope
        dup = multi_scope_vault / "50_work" / "testproject"
        dup.mkdir(parents=True)
        (dup / "00-context.md").write_text(
            "---\nid: testproject-work\ntype: project\nstatus: active\n---\n\n"
            "# Work Copy\n"
        )
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool(
            "vault_query", {"project": "testproject", "section": "context"},
        ))
        # projects scope comes first → should find the original, not "Work Copy"
        assert "Test Project" in result

    async def test_meta_still_works(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool(
            "vault_query", {"project": "_meta", "path": "patterns/pattern-tdd.md"},
        ))
        assert "Test-Driven Development" in result


class TestMultiScopeHealth:
    async def test_reports_across_scopes(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_health", {}))
        assert "testproject" in result
        assert "my-company" in result


class TestMultiScopeUpdate:
    async def test_update_work_project(self, git_multi_scope_vault: Path) -> None:
        mcp = create_server(
            vault_path=git_multi_scope_vault, vault_scopes=MULTI_SCOPES,
        )
        result = _text(await mcp.call_tool("vault_update", {
            "project": "my-company",
            "section": "lessons",
            "operation": "append",
            "content": "\n## New Lesson\nAlways test.\n",
        }))
        assert "Updated" in result
        content = (
            git_multi_scope_vault / "50_work" / "my-company" / "90-lessons.md"
        ).read_text()
        assert "Always test" in content


class TestMultiScopeRecent:
    async def test_project_filter_in_work_scope(
        self, git_multi_scope_vault: Path,
    ) -> None:
        mcp = create_server(
            vault_path=git_multi_scope_vault, vault_scopes=MULTI_SCOPES,
        )
        result = _text(await mcp.call_tool(
            "vault_recent", {"project": "my-company", "since_days": 30},
        ))
        # Should find files in 50_work/my-company, not return "No changes"
        assert "my-company" in result or "50_work" in result


class TestSectionFallback:
    async def test_bare_name_takes_priority(self, mock_vault: Path) -> None:
        # Create a bare context.md alongside the legacy 00-context.md
        project = mock_vault / "10_projects" / "testproject"
        (project / "context.md").write_text(
            "---\nid: bare-context\ntype: project\nstatus: active\n---\n\n"
            "# Bare Context\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(await mcp.call_tool(
            "vault_query", {"project": "testproject", "section": "context"},
        ))
        assert "Bare Context" in result

    async def test_legacy_fallback(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        result = _text(await mcp.call_tool(
            "vault_query", {"project": "testproject", "section": "context"},
        ))
        assert "Test Project" in result
