"""Tests for Vault MCP Server tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from hive.vault_server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP
    from fastmcp.tools import ToolResult


def _text(result: ToolResult) -> str:
    """Extract text from a ToolResult."""
    return result.content[0].text  # type: ignore[union-attr]


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
        result = await vault_mcp.call_tool(
            "vault_query", {"project": "testproject"}
        )
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
        result = await vault_mcp.call_tool(
            "vault_query", {"project": "nonexistent"}
        )
        assert "not found" in _text(result).lower()

    async def test_missing_section(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_query", {"project": "testproject", "section": "roadmap"}
        )
        assert "not found" in _text(result).lower()


# ── vault_search ─────────────────────────────────────────────────────


class TestVaultSearch:
    async def test_finds_matching_content(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "Task one"}
        )
        text = _text(result)
        assert "testproject" in text
        assert "11-tasks.md" in text

    async def test_case_insensitive(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "task one"}
        )
        assert "11-tasks.md" in _text(result)

    async def test_no_results(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "xyznonexistent"}
        )
        assert "no matches" in _text(result).lower()

    async def test_searches_across_files(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "active"}
        )
        text = _text(result)
        assert "00-context.md" in text
        assert "11-tasks.md" in text

    async def test_returns_matching_lines(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "Some lesson"}
        )
        assert "Some lesson" in _text(result)

    async def test_max_lines_limits_output(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "active", "max_lines": 5}
        )
        text = _text(result)
        assert "truncated" in text.lower()
        content_lines = text.split("[...")[0].strip().splitlines()
        assert len(content_lines) == 5


# ── vault_health ─────────────────────────────────────────────────────


class TestVaultHealth:
    async def test_returns_project_stats(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_health", {})
        assert "testproject" in _text(result)

    async def test_reports_file_count(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_health", {})
        # testproject has 4 files now (context, tasks, lessons, adr-001)
        assert "4" in _text(result)

    async def test_reports_total_lines(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_health", {})
        assert "line" in _text(result).lower()

    async def test_empty_vault(self, tmp_path: Path) -> None:
        (tmp_path / "10_projects").mkdir()
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_health", {})
        assert "no projects" in _text(result).lower()


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
            cwd=git_vault, capture_output=True, text=True, check=True,
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
                "path": "91-extra-lesson.md",
                "content": "# Extra Lesson\n\nLearned something.\n",
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
            git_vault / "10_projects" / "testproject"
            / "50-troubleshooting" / "error-timeout.md"
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
            cwd=git_vault, capture_output=True, text=True, check=True,
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
