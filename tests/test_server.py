"""Tests for Hive MCP Server tools."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest

from hive.clients import ClientResponse, ModelInfo, OpenRouterClient
from hive.server import create_server

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from fastmcp import FastMCP
    from fastmcp.resources.resource import ResourceResult
    from fastmcp.tools import ToolResult

    from hive.budget import BudgetTracker
    from hive.clients import OllamaClient


def _text(result: ToolResult) -> str:
    """Extract text from a ToolResult."""
    return result.content[0].text  # type: ignore[union-attr]


def _resource_text(result: ResourceResult) -> str:
    """Extract text from a ResourceResult."""
    return str(result.contents[0].content)


def _close_server(mcp: FastMCP) -> None:
    ctx = getattr(mcp, "_hive_ctx", None)
    if ctx is not None:
        ctx.close()


@pytest.fixture
def vault_mcp(mock_vault: Path) -> Generator[FastMCP, None, None]:
    """Create a vault server backed by mock_vault."""
    mcp = create_server(vault_path=mock_vault)
    yield mcp
    _close_server(mcp)


# ── vault not found (missing vault path) ──────────────────────


class TestVaultNotFound:
    """All vault tools return a helpful error when vault path does not exist."""

    @pytest.fixture
    def missing_vault_mcp(self, tmp_path: Path) -> Generator[FastMCP, None, None]:
        mcp = create_server(vault_path=tmp_path / "nonexistent")
        yield mcp
        _close_server(mcp)

    async def test_vault_list(self, missing_vault_mcp: FastMCP) -> None:
        result = await missing_vault_mcp.call_tool("vault_list", {})
        text = _text(result)
        assert "Vault not found" in text
        assert "VAULT_PATH" in text

    async def test_vault_query(self, missing_vault_mcp: FastMCP) -> None:
        result = await missing_vault_mcp.call_tool(
            "vault_query",
            {"project": "test"},
        )
        assert "Vault not found" in _text(result)

    async def test_vault_search(self, missing_vault_mcp: FastMCP) -> None:
        result = await missing_vault_mcp.call_tool(
            "vault_search",
            {"query": "hello"},
        )
        assert "Vault not found" in _text(result)

    async def test_session_briefing(self, missing_vault_mcp: FastMCP) -> None:
        result = await missing_vault_mcp.call_tool(
            "session_briefing",
            {"project": "test"},
        )
        assert "Vault not found" in _text(result)

    async def test_vault_write(self, missing_vault_mcp: FastMCP) -> None:
        result = await missing_vault_mcp.call_tool(
            "vault_write",
            {"project": "test", "content": "hello"},
        )
        assert "Vault not found" in _text(result)

    async def test_vault_patch(self, missing_vault_mcp: FastMCP) -> None:
        result = await missing_vault_mcp.call_tool(
            "vault_patch",
            {
                "project": "test",
                "path": "f.md",
                "find": "a",
                "replace": "b",
            },
        )
        assert "Vault not found" in _text(result)

    async def test_vault_health(self, missing_vault_mcp: FastMCP) -> None:
        result = await missing_vault_mcp.call_tool("vault_health", {})
        assert "Vault not found" in _text(result)

    def test_startup_logs_warning_for_missing_vault(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """create_server emits a loud WHY/FIX warning when the vault path is
        missing, so a dead path is visible at startup — not only once a tool is
        called (#246)."""
        missing = tmp_path / "nonexistent"
        with caplog.at_level(logging.WARNING, logger="hive"):
            mcp = create_server(vault_path=missing)
        _close_server(mcp)
        messages = [r.getMessage() for r in caplog.records]
        assert any("does not exist" in m and "FIX" in m and str(missing) in m for m in messages)

    async def test_capture_lesson(self, missing_vault_mcp: FastMCP) -> None:
        result = await missing_vault_mcp.call_tool(
            "capture_lesson",
            {
                "project": "test",
                "title": "t",
                "context": "c",
                "problem": "p",
                "solution": "s",
            },
        )
        assert "Vault not found" in _text(result)


# ── vault_list ──────────────────────────────────────────────


class TestVaultListProjects:
    async def test_returns_projects(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_list", {})
        assert "testproject" in _text(result)

    async def test_empty_vault(self, tmp_path: Path) -> None:
        (tmp_path / "10_projects").mkdir()
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_list", {})
        assert "No projects found" in _text(result)

    async def test_no_projects_dir(self, tmp_path: Path) -> None:
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_list", {})
        assert "No projects found" in _text(result)

    async def test_multiple_projects(self, mock_vault: Path) -> None:
        second = mock_vault / "10_projects" / "another"
        second.mkdir(parents=True)
        (second / "00-context.md").write_text(
            "---\nid: another\ntype: project\nstatus: active\n---\n\n# Another\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool("vault_list", {})
        assert "testproject" in _text(result)
        assert "another" in _text(result)

    async def test_glob_pattern_rejects_dotdot(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_list",
            {"project": "testproject", "pattern": "../../etc/*.md"},
        )
        assert "must not contain" in _text(result).lower()

    async def test_glob_pattern_valid(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_list",
            {"project": "testproject", "pattern": "*.md"},
        )
        text = _text(result)
        assert "00-context.md" in text


# ── unicode / encoding errors ────────────────────────────────────────


class TestUnicodeDecodeErrors:
    """Tools return error message instead of crashing on non-UTF-8 files."""

    async def test_vault_query_non_utf8(self, mock_vault: Path) -> None:
        bad = mock_vault / "10_projects" / "testproject" / "bad.md"
        bad.write_bytes(b"\xff\xfe invalid utf-8")
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool(
            "vault_query",
            {"project": "testproject", "path": "bad.md"},
        )
        text = _text(result)
        assert "utf-8" in text.lower() or "error" in text.lower()

    async def test_vault_patch_non_utf8(self, git_vault: Path) -> None:
        bad = git_vault / "10_projects" / "testproject" / "bad.md"
        bad.write_bytes(b"\xff\xfe invalid utf-8")
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_patch",
            {
                "project": "testproject",
                "path": "bad.md",
                "find": "a",
                "replace": "b",
            },
        )
        text = _text(result)
        assert "utf-8" in text.lower() or "error" in text.lower()

    async def test_vault_write_append_non_utf8(
        self,
        git_vault: Path,
    ) -> None:
        bad = git_vault / "10_projects" / "testproject" / "90-lessons.md"
        bad.write_bytes(b"\xff\xfe invalid utf-8")
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "content": "\nnew content\n",
                "section": "lessons",
                "operation": "append",
            },
        )
        text = _text(result)
        assert "utf-8" in text.lower() or "error" in text.lower()


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

    async def test_empty_query_rejected(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {})
        assert "query is required" in _text(result).lower()

    async def test_invalid_regex_rejected(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search",
            {"query": "[invalid", "use_regex": True},
        )
        assert "invalid regex" in _text(result).lower()

    async def test_negative_since_days_rejected(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search",
            {"query": "test", "since_days": -5},
        )
        assert "positive" in _text(result).lower()

    async def test_searches_across_files(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "Test"})
        text = _text(result)
        assert "00-context.md" in text
        assert "11-tasks.md" in text

    async def test_returns_matching_lines(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "Some lesson"})
        assert "Some lesson" in _text(result)

    async def test_max_lines_limits_output(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "Test", "max_lines": 5})
        text = _text(result)
        assert "truncated" in text.lower()
        content_lines = text.split("[...")[0].strip().splitlines()
        assert len(content_lines) == 5

    # -- metadata display --

    async def test_shows_metadata_per_file(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "Test Project"})
        text = _text(result)
        assert "type=project" in text
        assert "status=active" in text

    # -- type_filter --

    async def test_type_filter_includes_matching(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "decided", "type_filter": "adr"}
        )
        text = _text(result)
        assert "adr-001-test" in text

    async def test_type_filter_excludes_non_matching(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "Test", "type_filter": "adr"})
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

    async def test_regex_too_long_rejected(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "a" * 201, "use_regex": True})
        assert "too long" in _text(result).lower()

    async def test_regex_at_max_length_accepted(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "a" * 200, "use_regex": True})
        text = _text(result)
        # Should not be rejected — either finds matches or returns "no matches"
        assert "too long" not in text.lower()

    async def test_filter_skips_files_without_frontmatter(self, mock_vault: Path) -> None:
        bare = mock_vault / "10_projects" / "testproject" / "bare.md"
        bare.write_text("# No frontmatter\nSome active content.\n")
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool("vault_search", {"query": "active", "type_filter": "project"})
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
        # Assert against the section label, not the bare substring — the
        # identity block now embeds ``vault_path`` which may legitimately
        # contain "stale" inside a pytest tmp dir name.
        assert "stale files" not in _text(result).lower()

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
        assert "stale files" not in _text(result).lower()

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

    # -- ghost-response counter surface (HIVE-104 Fase C) --

    async def test_ghost_responses_block_when_counter_nonzero(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """When _compat.GHOST_RESPONSES has entries, vault_health surfaces them."""
        from hive import _compat as _hc

        _hc.GHOST_RESPONSES.reset()
        try:
            _hc.GHOST_RESPONSES.record("vault_patch")
            result = _text(await vault_mcp.call_tool("vault_health", {}))
            assert "ghost_responses" in result
            assert "total: 1" in result
            assert "vault_patch" in result
        finally:
            _hc.GHOST_RESPONSES.reset()

    async def test_ghost_responses_block_omitted_when_zero(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """When no ghost responses recorded, the block is omitted."""
        from hive import _compat as _hc

        _hc.GHOST_RESPONSES.reset()
        result = _text(await vault_mcp.call_tool("vault_health", {}))
        # Anchor on the section header so pytest tmp paths that contain
        # 'ghost_responses' in their dir name cannot collide.
        assert "## ghost_responses" not in result


# ── vault_health (server identity + runtime, issue #109) ─────────────


class TestVaultHealthIdentity:
    """Identity block is always present at the top of vault_health output."""

    async def test_identity_block_present_no_args(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """Default vault_health() includes the ## server identity block."""
        result = _text(await vault_mcp.call_tool("vault_health", {}))
        assert "## server" in result
        assert "- version:" in result
        assert "- python:" in result
        assert "- vault_path:" in result
        assert "- backends:" in result
        assert "- started_at:" in result

    async def test_identity_appears_before_project_stats(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """Identity block is prepended — appears before per-project blocks."""
        result = _text(await vault_mcp.call_tool("vault_health", {}))
        idx_server = result.find("## server")
        idx_project = result.find("testproject")
        assert idx_server >= 0
        assert idx_project >= 0
        assert idx_server < idx_project

    async def test_identity_with_validation_mode(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """Identity block also appears when checks=[...] is passed."""
        result = _text(
            await vault_mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter"]},
            ),
        )
        assert "## server" in result
        assert "- version:" in result

    async def test_identity_with_empty_vault(self, tmp_path: Path) -> None:
        """Identity block appears even when the vault has no projects."""
        (tmp_path / "10_projects").mkdir()
        mcp = create_server(vault_path=tmp_path)
        try:
            result = _text(await mcp.call_tool("vault_health", {}))
            assert "## server" in result
            assert "- version:" in result
        finally:
            _close_server(mcp)

    async def test_identity_backends_no_api_keys(
        self,
        mock_vault: Path,
    ) -> None:
        """The identity block never embeds API key material."""
        secret = "sk-DO-NOT-LEAK-CANARY-12345"
        mcp = create_server(
            vault_path=mock_vault,
            openrouter_client=OpenRouterClient(
                api_key=secret,
                default_model="qwen/qwen3-coder:free",
            ),
        )
        try:
            result = _text(await mcp.call_tool("vault_health", {}))
            assert secret not in result
            assert "## server" in result
            # presence boolean is exposed instead
            assert "openrouter" in result.lower()
        finally:
            _close_server(mcp)

    async def test_identity_total_length_bounded(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """Acceptance: identity block adds <10 lines to the report."""
        result = _text(await vault_mcp.call_tool("vault_health", {}))
        # Lines between '## server' (inclusive) and the next '## ' heading
        lines = result.splitlines()
        start = next(
            (i for i, line in enumerate(lines) if line.startswith("## server")),
            None,
        )
        assert start is not None
        end = next(
            (i for i, line in enumerate(lines[start + 1 :], start + 1) if line.startswith("## ")),
            len(lines),
        )
        assert (end - start) < 10


class TestVaultHealthRuntime:
    """include_runtime=True activates the optional ## runtime block."""

    async def test_runtime_block_omitted_by_default(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        result = _text(await vault_mcp.call_tool("vault_health", {}))
        assert "## runtime" not in result
        assert "uptime_s" not in result

    async def test_runtime_block_present_when_opted_in(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        result = _text(
            await vault_mcp.call_tool(
                "vault_health",
                {"include_runtime": True},
            ),
        )
        assert "## runtime" in result
        assert "uptime_s" in result
        assert "tools_registered" in result
        assert "openrouter_budget" in result

    async def test_runtime_lists_registered_tool_names(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """tools_registered must include the known hive tools."""
        result = _text(
            await vault_mcp.call_tool(
                "vault_health",
                {"include_runtime": True},
            ),
        )
        for expected in (
            "vault_query",
            "vault_search",
            "vault_health",
            "vault_list",
        ):
            assert expected in result

    async def test_runtime_orthogonal_to_include_usage(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """include_runtime is independent from include_usage — both can stack."""
        result = _text(
            await vault_mcp.call_tool(
                "vault_health",
                {"include_runtime": True, "include_usage": True},
            ),
        )
        assert "## runtime" in result
        # usage block surfaces 'No vault tool calls' or 'Total calls:'
        assert "no vault tool calls" in result.lower() or "Total calls:" in result

    async def test_runtime_block_includes_wal_size_bytes(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """HIVE-115 / ADR-009: wal_size_bytes field present as non-negative int."""
        import re

        result = _text(
            await vault_mcp.call_tool(
                "vault_health",
                {"include_runtime": True},
            ),
        )
        m = re.search(r"wal_size_bytes:\s*(\d+)", result)
        assert m is not None, f"wal_size_bytes field missing in:\n{result}"
        assert int(m.group(1)) >= 0

    async def test_runtime_block_includes_competing_pid_count(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """HIVE-115 / ADR-009: competing_pid_count field present (non-negative int)."""
        import re

        result = _text(
            await vault_mcp.call_tool(
                "vault_health",
                {"include_runtime": True},
            ),
        )
        m = re.search(r"competing_pid_count:\s*(\d+)", result)
        assert m is not None, f"competing_pid_count field missing in:\n{result}"
        assert int(m.group(1)) >= 0

    async def test_runtime_block_includes_last_git_lock_wait_ms(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """HIVE-115 / ADR-010: last_git_lock_wait_ms surfaces mean + p99 + samples."""
        result = _text(
            await vault_mcp.call_tool(
                "vault_health",
                {"include_runtime": True},
            ),
        )
        assert "last_git_lock_wait_ms:" in result
        assert "mean:" in result
        assert "p99:" in result
        assert "samples:" in result

    async def test_runtime_block_includes_obsidian_git_present(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """HIVE-115 / ADR-010: obsidian_git_present field is true/false."""
        import re

        result = _text(
            await vault_mcp.call_tool(
                "vault_health",
                {"include_runtime": True},
            ),
        )
        m = re.search(r"obsidian_git_present:\s*(true|false)", result)
        assert m is not None, f"obsidian_git_present field missing in:\n{result}"

    async def test_runtime_block_obsidian_git_present_when_plugin_active(
        self,
        mock_vault: Path,
    ) -> None:
        """When the obsidian-git plugin config exists, presence flips to true."""
        import json

        plugin_dir = mock_vault / ".obsidian" / "plugins" / "obsidian-git"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "data.json").write_text(
            json.dumps({"commitInterval": 10}),
            encoding="utf-8",
        )
        mcp = create_server(vault_path=mock_vault)
        try:
            result = _text(
                await mcp.call_tool(
                    "vault_health",
                    {"include_runtime": True},
                ),
            )
            assert "obsidian_git_present: true" in result
        finally:
            _close_server(mcp)

    async def test_runtime_block_includes_lock_eviction(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """HIVE-116 PR-2 / AC-8: lock_eviction.count_30d + last_iso fields."""
        import re

        result = _text(
            await vault_mcp.call_tool(
                "vault_health",
                {"include_runtime": True},
            ),
        )
        assert "lock_eviction:" in result
        m = re.search(r"count_30d:\s*(\d+)", result)
        assert m is not None, f"count_30d field missing in:\n{result}"
        # Fresh tracker → 0 evictions
        assert int(m.group(1)) == 0
        # last_iso is 'null' when no eviction has happened
        assert "last_iso: null" in result

    async def test_runtime_budget_does_not_leak_api_key(
        self,
        mock_vault: Path,
    ) -> None:
        secret = "sk-RUNTIME-NEVER-LEAK-ABCDEF"
        mcp = create_server(
            vault_path=mock_vault,
            openrouter_client=OpenRouterClient(
                api_key=secret,
                default_model="qwen/qwen3-coder:free",
            ),
        )
        try:
            result = _text(
                await mcp.call_tool(
                    "vault_health",
                    {"include_runtime": True},
                ),
            )
            assert secret not in result
            # budget block exposes cap+spent, not credentials
            assert "openrouter_budget" in result
        finally:
            _close_server(mcp)


# ── vault_write (update operations, with real YAML frontmatter validation) ────


class TestVaultWrite:
    async def test_append_missing_section_rejected(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {"project": "testproject", "operation": "append", "content": "x"},
        )
        assert "section" in _text(result).lower()

    async def test_append_invalid_section_rejected(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "section": "nonexistent",
                "operation": "append",
                "content": "x",
            },
        )
        text = _text(result)
        assert "not found" in text.lower() or "no file" in text.lower()

    async def test_append_to_existing_file(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
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
            "vault_write",
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
            "vault_write",
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
            "vault_write",
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
            "vault_write",
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
            "vault_write",
            {
                "project": "testproject",
                "section": "lessons",
                "operation": "append",
                "content": "\n## Git test\nCommitted.\n",
                # commit=True since ADR-018 made deferral the default; this
                # test is about the commit itself, not about which default
                # produces it.
                "commit": True,
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
            "vault_write",
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
            "vault_write",
            {
                "project": "testproject",
                "section": "tasks",
                "operation": "delete",
                "content": "stuff",
            },
        )
        assert "invalid" in _text(result).lower() or "operation" in _text(result).lower()


# ── capture_lesson ───────────────────────────────────────────────────


class TestCaptureLesson:
    """capture_lesson tool — inline lesson extraction to vault."""

    async def test_basic_capture(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Always validate frontmatter",
                "context": "Writing vault_write tests",
                "problem": "Replace operation accepted invalid YAML",
                "solution": "Added frontmatter validation before write",
                "tags": ["testing", "yaml"],
            },
        )
        text = _text(result)
        assert "captured" in text.lower()

        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        assert "Always validate frontmatter" in lessons
        assert "Writing vault_write tests" in lessons
        assert "Added frontmatter validation before write" in lessons

    async def test_capture_formats_with_date(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Test lesson",
                "context": "ctx",
                "problem": "prob",
                "solution": "sol",
                "tags": ["test"],
            },
        )
        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        # Should contain date header
        from datetime import date

        assert date.today().isoformat() in lessons
        # Should contain structured fields
        assert "**Context:**" in lessons
        assert "**Problem:**" in lessons
        assert "**Solution:**" in lessons
        assert "`#test`" in lessons

    async def test_capture_preserves_existing_content(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "New lesson",
                "context": "ctx",
                "problem": "prob",
                "solution": "sol",
                "tags": [],
            },
        )
        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        # Original content preserved
        assert "Entry 1" in lessons
        assert "New lesson" in lessons

    async def test_capture_deduplicates_by_title(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Unique lesson XYZ",
                "context": "first",
                "problem": "prob",
                "solution": "sol",
                "tags": [],
            },
        )
        result = await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Unique lesson XYZ",
                "context": "second",
                "problem": "prob2",
                "solution": "sol2",
                "tags": [],
            },
        )
        text = _text(result)
        assert "already exists" in text.lower()

        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        assert lessons.count("Unique lesson XYZ") == 1

    async def test_capture_creates_lessons_file_if_missing(self, git_vault: Path) -> None:
        # Remove lessons file
        lessons_file = git_vault / "10_projects" / "testproject" / "90-lessons.md"
        lessons_file.unlink()
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_vault, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "remove lessons"],
            cwd=git_vault,
            capture_output=True,
            check=True,
        )

        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "First lesson",
                "context": "ctx",
                "problem": "prob",
                "solution": "sol",
                "tags": ["new"],
            },
        )
        assert "captured" in _text(result).lower()
        assert lessons_file.exists()
        content = lessons_file.read_text()
        assert "First lesson" in content

    async def test_capture_rejects_unknown_project(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool(
            "capture_lesson",
            {
                "project": "nonexistent",
                "title": "Test",
                "context": "ctx",
                "problem": "prob",
                "solution": "sol",
                "tags": [],
            },
        )
        assert "not found" in _text(result).lower()

    async def test_capture_git_commits(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Git tracked lesson",
                "context": "ctx",
                "problem": "prob",
                "solution": "sol",
                "tags": [],
            },
        )
        import subprocess

        log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=git_vault,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "capture_lesson" in log.stdout or "lesson" in log.stdout.lower()


class TestCaptureLessonXmlDefense:
    """HIVE-115 / issue #114: defensive validation against XML-tag leakage.

    Malformed agent tool invocations can mix ``<parameter name="X">...</X>``
    with the proper ``<parameter name="X">...</parameter>`` syntax. When the
    closing tag never matches, the parser swallows subsequent XML into the
    field value, producing corrupted vault entries. Hive does not control
    the parser, but it CAN detect known-bad shapes at the input boundary
    and warn the caller without rejecting the write (warn-don't-reject so
    the agent's mid-turn context is preserved).
    """

    @pytest.mark.parametrize(
        ("field_name", "field_value"),
        [
            ("context", "Some context </context> leaked"),
            ("problem", "Issue with <parameter name='foo'> sneaking in"),
            ("solution", "Fixed by </parameter> handling"),
            ("title", "Bad title with </invoke> tag"),
        ],
    )
    async def test_xml_leak_emits_warning_and_corruption_marker(
        self,
        git_vault: Path,
        field_name: str,
        field_value: str,
    ) -> None:
        """SUSPECT pattern in any field → warning surfaced + marker in body.

        Lesson is still written (warn-don't-reject). The HTML comment
        marker makes the corruption visible during manual review of
        90-lessons.md.
        """
        mcp = create_server(vault_path=git_vault)
        payload = {
            "project": "testproject",
            "title": f"Lesson with leak via {field_name}",
            "context": "ctx",
            "problem": "prob",
            "solution": "sol",
            "tags": [],
        }
        payload[field_name] = field_value

        result = await mcp.call_tool("capture_lesson", payload)
        text = _text(result)

        # Response surfaces a warning that the agent / operator can see.
        assert "WARNING" in text or "POSSIBLE_CORRUPTION" in text, (
            f"expected XML-leak warning in response, got: {text}"
        )

        # The lesson WAS written (warn-don't-reject contract).
        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        # HTML comment marker visible during manual review.
        assert "POSSIBLE_CORRUPTION" in lessons, (
            "expected POSSIBLE_CORRUPTION marker in lessons file"
        )

    async def test_clean_lesson_has_no_warning_or_marker(
        self,
        git_vault: Path,
    ) -> None:
        """Normal lesson body without suspect patterns → no warning, no marker."""
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Clean lesson without XML",
                "context": "Normal context with no suspicious tags",
                "problem": "Normal problem statement",
                "solution": "Normal solution text",
                "tags": ["clean"],
            },
        )
        text = _text(result)
        assert "WARNING" not in text
        assert "POSSIBLE_CORRUPTION" not in text

        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        assert "POSSIBLE_CORRUPTION" not in lessons


# ── vault_write (create operations) ──────────────────────────────────


class TestVaultWriteCreate:
    async def test_create_new_adr(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "path": "30-architecture/adr-002-new.md",
                "content": "# ADR-002: New Decision\n\nWe decided something new.\n",
                "doc_type": "adr",
                "operation": "create",
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
            "vault_write",
            {
                "project": "testproject",
                "path": "92-new-lesson.md",
                "content": "# New Lesson\n\nLearned something.\n",
                "doc_type": "lesson",
                "operation": "create",
            },
        )
        assert "created" in _text(result).lower()

    async def test_create_missing_path_rejected(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {"project": "testproject", "content": "x", "doc_type": "note", "operation": "create"},
        )
        assert "path" in _text(result).lower()

    async def test_create_without_doc_type_defaults_to_note(self, git_vault: Path) -> None:
        # #202 Bug 2: doc_type is now optional and defaults to "note".
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {"project": "testproject", "path": "new.md", "content": "x", "operation": "create"},
        )
        assert "created" in _text(result).lower()
        content = (git_vault / "10_projects" / "testproject" / "new.md").read_text()
        assert "type: note" in content

    async def test_rejects_existing_file(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "path": "00-context.md",
                "content": "# Overwrite attempt\n",
                "doc_type": "project",
                "operation": "create",
            },
        )
        assert "exists" in _text(result).lower()

    async def test_auto_generates_frontmatter(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "path": "50-troubleshooting/error-timeout.md",
                "content": "# Timeout Error\n\nFix: increase timeout.\n",
                "doc_type": "troubleshooting",
                "operation": "create",
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
            "vault_write",
            {
                "project": "testproject",
                "path": "new-file.md",
                "content": "# New\n",
                "doc_type": "lesson",
                "operation": "create",
                # commit=True since ADR-018 made deferral the default; this
                # test is about the commit itself, not about which default
                # produces it.
                "commit": True,
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
            "vault_write",
            {
                "project": "_meta",
                "path": "patterns/pattern-new.md",
                "content": "# New Pattern\n\nDo this always.\n",
                "doc_type": "pattern",
                "operation": "create",
            },
        )
        assert "created" in _text(result).lower()
        filepath = git_vault / "00_meta" / "patterns" / "pattern-new.md"
        assert filepath.exists()

    async def test_missing_project(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {
                "project": "nonexistent",
                "path": "new.md",
                "content": "# New\n",
                "doc_type": "lesson",
                "operation": "create",
            },
        )
        assert "not found" in _text(result).lower()

    async def test_infers_create_from_path_without_operation(self, git_vault: Path) -> None:
        # #202 Bug 2: vault_write(project, path, content) — default op, no
        # section — is unambiguously a create and "just works" (type note).
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {"project": "testproject", "path": "notes/idea.md", "content": "# Idea\n"},
        )
        assert "created" in _text(result).lower()
        filepath = git_vault / "10_projects" / "testproject" / "notes" / "idea.md"
        assert filepath.exists()
        assert "type: note" in filepath.read_text()

    async def test_inferred_create_respects_existing_file(self, git_vault: Path) -> None:
        # Inference still routes through create's existing-file guard.
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {"project": "testproject", "path": "00-context.md", "content": "# Nope\n"},
        )
        assert "exists" in _text(result).lower()

    async def test_no_section_no_path_gives_actionable_error(self, git_vault: Path) -> None:
        # With neither section nor path, the error must point to BOTH ways out.
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {"project": "testproject", "content": "orphan content"},
        )
        text = _text(result).lower()
        assert "section" in text  # still names the append/replace requirement
        assert "path" in text or "create" in text  # now also points to create

    async def test_append_with_section_not_inferred_as_create(self, git_vault: Path) -> None:
        # Boundary: inference must NOT fire when a section is present.
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_write",
            {"project": "testproject", "section": "tasks", "content": "\n- [ ] New task\n"},
        )
        text = _text(result).lower()
        assert "updated" in text
        assert "created" not in text


class TestVaultDelete:
    # #202 Bug 4: destructive single-file delete, git-recoverable, no confirm.
    async def test_delete_removes_file_and_commits(self, git_vault: Path) -> None:
        import subprocess

        mcp = create_server(vault_path=git_vault)
        target = git_vault / "10_projects" / "testproject" / "00-context.md"
        assert target.exists()
        result = await mcp.call_tool(
            "vault_delete",
            {"project": "testproject", "path": "00-context.md"},
        )
        assert "deleted" in _text(result).lower()
        assert not target.exists()
        log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=git_vault,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "delete" in log.stdout.lower()

    async def test_delete_nonexistent_returns_error(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_delete",
            {"project": "testproject", "path": "does-not-exist.md"},
        )
        assert "not found" in _text(result).lower()

    async def test_delete_directory_rejected(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_delete",
            {"project": "testproject", "path": "30-architecture"},
        )
        text = _text(result).lower()
        assert "directory" in text
        assert (git_vault / "10_projects" / "testproject" / "30-architecture").is_dir()

    async def test_delete_path_escape_blocked(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_delete",
            {"project": "testproject", "path": "../../../../escape.md"},
        )
        assert "deleted" not in _text(result).lower()

    async def test_delete_missing_project(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_delete",
            {"project": "nonexistent", "path": "x.md"},
        )
        assert "not found" in _text(result).lower()

    async def test_delete_idempotent_with_key(self, git_vault: Path) -> None:
        import uuid

        # Unique key per run: the idempotency store is not test-isolated (#212),
        # so a fixed key would collide across runs and make the first call a no-op.
        key = uuid.uuid4().hex
        mcp = create_server(vault_path=git_vault)
        first = await mcp.call_tool(
            "vault_delete",
            {"project": "testproject", "path": "92-large-doc.md", "idempotency_key": key},
        )
        assert "deleted" in _text(first).lower()
        second = await mcp.call_tool(
            "vault_delete",
            {"project": "testproject", "path": "92-large-doc.md", "idempotency_key": key},
        )
        assert "idempotent" in _text(second).lower()

    async def test_delete_already_gone_with_key_succeeds(self, git_vault: Path) -> None:
        import uuid

        key = uuid.uuid4().hex
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool(
            "vault_delete",
            {"project": "testproject", "path": "never-existed.md", "idempotency_key": key},
        )
        text = _text(result).lower()
        assert "already deleted" in text
        assert "not found" not in text


# ── delegate_task: vault summarize mode ──────────────────────────────


class TestDelegateTaskSummarize:
    async def test_small_file_returns_content(self, vault_mcp: FastMCP) -> None:
        """Files ≤50 lines return content directly, no delegation prompt."""
        result = await vault_mcp.call_tool(
            "delegate_task", {"project": "testproject", "section": "context"}
        )
        text = _text(result)
        assert "# Test Project" in text
        assert "delegate_task" not in text

    async def test_small_file_includes_metadata(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "delegate_task", {"project": "testproject", "section": "context"}
        )
        text = _text(result)
        assert "**Metadata:**" in text
        assert "type=project" in text

    async def test_large_file_returns_summary_or_content(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """Files >50 lines: worker summary if available, raw content otherwise."""
        result = await vault_mcp.call_tool(
            "delegate_task",
            {"project": "testproject", "path": "92-large-doc.md"},
        )
        text = _text(result)
        # Worker available → summary; unavailable → raw content with notice
        assert "Large Document" in text or "Worker Response" in text
        assert "**Metadata:**" in text
        # If fallback, notice must be present
        if "Worker Response" not in text:
            assert "Summarization failed" in text or "Large Document" in text

    async def test_large_file_includes_metadata_in_prompt(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "delegate_task",
            {"project": "testproject", "path": "92-large-doc.md"},
        )
        text = _text(result)
        assert "type=lesson" in text
        assert "status=active" in text

    async def test_large_file_has_content(self, vault_mcp: FastMCP) -> None:
        """Large file returns either body content (fallback) or a summary."""
        result = await vault_mcp.call_tool(
            "delegate_task",
            {"project": "testproject", "path": "92-large-doc.md"},
        )
        text = _text(result)
        # Raw content has body lines; worker summary has "Worker Response"
        assert "Line 1:" in text or "Worker Response" in text

    async def test_max_summary_lines_accepted(self, vault_mcp: FastMCP) -> None:
        """max_summary_lines is a valid parameter (used when workers are online)."""
        result = await vault_mcp.call_tool(
            "delegate_task",
            {"project": "testproject", "path": "92-large-doc.md", "max_summary_lines": 10},
        )
        text = _text(result)
        assert "Large Document" in text

    async def test_missing_project(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("delegate_task", {"project": "nonexistent"})
        assert "not found" in _text(result).lower()

    async def test_missing_file(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "delegate_task", {"project": "testproject", "path": "nope.md"}
        )
        assert "not found" in _text(result).lower()

    async def test_path_overrides_section(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "delegate_task",
            {"project": "testproject", "section": "tasks", "path": "92-large-doc.md"},
        )
        text = _text(result)
        assert "Large Document" in text

    async def test_file_without_frontmatter(self, mock_vault: Path) -> None:
        bare = mock_vault / "10_projects" / "testproject" / "bare.md"
        bare.write_text("# Bare\nJust text.\n")
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool("delegate_task", {"project": "testproject", "path": "bare.md"})
        text = _text(result)
        assert "# Bare" in text
        assert "**Metadata:**" not in text


# ── vault_search (ranked) ────────────────────────────────────────────


class TestVaultSearchRanked:
    async def test_empty_query_rejected(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search",
            {"ranked": True},
        )
        assert "Query is required" in _text(result)

    async def test_finds_matching_files(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "Task one", "ranked": True})
        text = _text(result)
        assert "11-tasks.md" in text
        assert "score:" in text

    async def test_no_results(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "xyznonexistent", "ranked": True}
        )
        assert "no matches" in _text(result).lower()

    async def test_active_ranks_above_terminal(self, vault_mcp: FastMCP) -> None:
        """Active files should score higher than completed/accepted files."""
        result = await vault_mcp.call_tool("vault_search", {"query": "Lesson", "ranked": True})
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
        few.write_text("---\nid: few\ntype: lesson\nstatus: active\n---\n\nalpha once\n")
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool("vault_search", {"query": "alpha", "ranked": True})
        text = _text(result)
        score_lines = [ln for ln in text.splitlines() if "score:" in ln]
        many_idx = next(i for i, ln in enumerate(score_lines) if "many-matches" in ln)
        few_idx = next(i for i, ln in enumerate(score_lines) if "few-matches" in ln)
        assert many_idx < few_idx

    async def test_shows_metadata_per_result(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "Test Project", "ranked": True}
        )
        text = _text(result)
        assert "type=project" in text
        assert "status=active" in text

    async def test_max_results_limits_output(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "active", "ranked": True, "max_results": 2}
        )
        text = _text(result)
        score_lines = [ln for ln in text.splitlines() if "score:" in ln]
        assert len(score_lines) <= 2

    async def test_max_lines_truncates(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool(
            "vault_search", {"query": "Test", "ranked": True, "max_lines": 5}
        )
        text = _text(result)
        assert "truncated" in text.lower()

    async def test_case_insensitive(self, vault_mcp: FastMCP) -> None:
        result = await vault_mcp.call_tool("vault_search", {"query": "task ONE", "ranked": True})
        assert "11-tasks.md" in _text(result)

    async def test_files_without_frontmatter_searchable(self, mock_vault: Path) -> None:
        bare = mock_vault / "10_projects" / "testproject" / "bare.md"
        bare.write_text("# Bare\nSearchable bare content.\n")
        mcp = create_server(vault_path=mock_vault)
        result = await mcp.call_tool("vault_search", {"query": "Searchable bare", "ranked": True})
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
        result = await mcp.call_tool("vault_search", {"query": "keyword", "ranked": True})
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
        assert "vault_write" in text
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
        assert "vault_write" in text

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

    async def test_vault_write_records_write_boost(self, git_vault: Path) -> None:
        from hive.relevance import RelevanceTracker

        relevance = RelevanceTracker()
        mcp = create_server(vault_path=git_vault, relevance_tracker=relevance)
        await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "section": "lessons",
                "operation": "append",
                "content": "\n## New Lesson\nTest.",
            },
        )
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
        self,
        git_vault: Path,
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
        self,
        git_vault: Path,
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


class TestBriefingRelevanceTimeout:
    """Regression for #282: session_briefing hangs ~60s when a call into
    RelevanceTracker stalls, because asyncio.timeout cannot interrupt the
    worker thread once it is blocked inside the tracker's shared lock/SQLite
    call. Every relevance touch in session_briefing must be bounded well
    under the outer tool deadline and degrade gracefully instead.
    """

    async def test_briefing_degrades_gracefully_when_relevance_stalls(
        self,
        git_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import time

        from hive.relevance import RelevanceTracker

        monkeypatch.setattr("hive._vault_read._RELEVANCE_TIMEOUT_S", 0.05)

        class StuckRelevanceTracker(RelevanceTracker):
            def apply_decay(self) -> None:
                # Model the real #282 stall: held *while holding the lock*
                # (e.g. a slow disk under the SQLite write), not before it —
                # a stall taken before acquiring would leave the lock free
                # for every other relevance call and understate the bug.
                with self._lock:
                    time.sleep(0.5)

        relevance = StuckRelevanceTracker()
        mcp = create_server(vault_path=git_vault, relevance_tracker=relevance)

        start = time.monotonic()
        result = await mcp.call_tool("session_briefing", {"project": "testproject"})
        elapsed = time.monotonic() - start

        text = _text(result)
        # Only the first touch (apply_decay) pays the timeout; the
        # short-circuit skips the remaining relevance calls once degraded.
        # Bound generously above real git-subprocess/fs overhead (unrelated
        # to this fix) — the point is "nowhere near the 60s outer deadline",
        # not a tight micro-benchmark.
        assert elapsed < 2.0, f"session_briefing should degrade fast, took {elapsed:.2f}s"
        assert "relevance tracking degraded" in text
        assert "Task one" in text
        assert "## Recent Vault Activity" in text
        assert "Files:" in text


# ── vault_search (recent) ────────────────────────────────────────────


class TestVaultSearchRecent:
    async def test_recent_git_change_appears(self, git_vault: Path) -> None:
        import subprocess

        new_file = git_vault / "10_projects" / "testproject" / "new-note.md"
        new_file.write_text("---\nid: new-note\ntype: lesson\nstatus: active\n---\n\n# New\n")
        subprocess.run(["git", "add", "."], cwd=git_vault, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add note"],
            cwd=git_vault,
            capture_output=True,
            check=True,
        )
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool("vault_search", {"since_days": 1})
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
            cwd=git_vault,
            capture_output=True,
            check=True,
        )
        mcp = create_server(vault_path=git_vault)
        result = await mcp.call_tool("vault_search", {"since_days": 1, "project": "testproject"})
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
        result = await mcp.call_tool("vault_search", {"since_days": 1})
        assert "today-note.md" in _text(result)

    async def test_no_changes_returns_message(self, tmp_path: Path) -> None:
        """Empty vault with no git and no recent frontmatter dates."""
        project = tmp_path / "10_projects" / "emptyproj"
        project.mkdir(parents=True)
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool("vault_search", {"since_days": 1})
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
            cwd=git_vault,
            capture_output=True,
            check=True,
        )
        mcp = create_server(vault_path=git_vault)
        # max_results=200 opts out of the #202 default file cap (10) so this
        # still exercises the independent max_lines line-truncation guard.
        result = await mcp.call_tool(
            "vault_search",
            {"since_days": 1, "max_lines": 100, "max_results": 200},
        )
        assert "truncated" in _text(result).lower()


# ── vault_health (usage) ─────────────────────────────────────────────


class TestVaultUsage:
    async def test_tracks_tool_calls(self, vault_mcp: FastMCP) -> None:
        """Tool calls should be recorded in the usage tracker."""
        await vault_mcp.call_tool("vault_list", {})
        await vault_mcp.call_tool("vault_query", {"project": "testproject"})
        result = await vault_mcp.call_tool(
            "vault_health",
            {"include_usage": True, "usage_days": 1},
        )
        text = _text(result)
        assert "vault_list" in text
        assert "vault_query" in text
        assert "Total calls:" in text

    async def test_tracks_project(self, vault_mcp: FastMCP) -> None:
        await vault_mcp.call_tool("vault_query", {"project": "testproject"})
        result = await vault_mcp.call_tool(
            "vault_health",
            {"include_usage": True, "usage_days": 1},
        )
        assert "testproject" in _text(result)

    async def test_empty_usage(self, tmp_path: Path) -> None:
        mcp = create_server(vault_path=tmp_path)
        result = await mcp.call_tool(
            "vault_health",
            {"include_usage": True, "usage_days": 1},
        )
        assert "no vault tool calls" in _text(result).lower()

    async def test_estimates_tokens(self, vault_mcp: FastMCP) -> None:
        await vault_mcp.call_tool("vault_query", {"project": "testproject"})
        result = await vault_mcp.call_tool(
            "vault_health",
            {"include_usage": True, "usage_days": 1},
        )
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
    async def test_ollama_first_when_available(self, worker: FastMCP, ollama: OllamaClient) -> None:
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

    @pytest.mark.asyncio
    async def test_explicit_openrouter_paid(
        self,
        worker: FastMCP,
        openrouter: OpenRouterClient,
    ) -> None:
        openrouter.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=ClientResponse(
                text="explicit paid",
                model="qwen/qwen3-coder",
                tokens=50,
                cost_usd=0.001,
                latency_ms=300,
            )
        )
        result = _text(
            await worker.call_tool("delegate_task", {"prompt": "test", "model": "openrouter"})
        )
        assert "explicit paid" in result

    @pytest.mark.asyncio
    async def test_explicit_custom_model(
        self,
        worker: FastMCP,
        openrouter: OpenRouterClient,
    ) -> None:
        openrouter.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=ClientResponse(
                text="custom model output",
                model="deepseek/deepseek-v3",
                tokens=40,
                cost_usd=0.0005,
                latency_ms=250,
            )
        )
        result = _text(
            await worker.call_tool(
                "delegate_task",
                {"prompt": "test", "model": "deepseek/deepseek-v3"},
            )
        )
        assert "custom model output" in result

    @pytest.mark.asyncio
    async def test_empty_prompt_no_project_rejected(self, worker: FastMCP) -> None:
        result = _text(await worker.call_tool("delegate_task", {}))
        assert "required" in result.lower()


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


# ── worker_status ───────────────────────────────────────────────────


class TestWorkerStatus:
    """worker_status tool shows budget, connectivity, and available models."""

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

    @pytest.mark.asyncio
    async def test_status_includes_models(
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
        result = _text(await worker.call_tool("worker_status", {}))
        assert "qwen2.5-coder:7b" in result
        assert "qwen/qwen3-coder:free" in result

    @pytest.mark.asyncio
    async def test_status_without_models(
        self, worker: FastMCP, ollama: OllamaClient, openrouter: OpenRouterClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        result = _text(await worker.call_tool("worker_status", {"include_models": False}))
        assert "Available Models" not in result
        assert "Budget" in result


# ── Multi-scope vault tests ─────────────────────────────────────────

MULTI_SCOPES = {"projects": "10_projects", "meta": "00_meta", "work": "50_work"}


class TestMultiScopeListProjects:
    async def test_lists_from_all_scopes(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_list", {}))
        assert "projects/testproject" in result
        assert "work/my-company" in result

    async def test_missing_scope_silently_skipped(self, mock_vault: Path) -> None:
        scopes = {**MULTI_SCOPES, "extra": "99_nonexistent"}
        mcp = create_server(vault_path=mock_vault, vault_scopes=scopes)
        result = _text(await mcp.call_tool("vault_list", {}))
        assert "projects/testproject" in result
        assert "99_nonexistent" not in result

    async def test_backward_compat(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        result = _text(await mcp.call_tool("vault_list", {}))
        assert "testproject" in result


class TestMultiScopeQuery:
    async def test_auto_scan_finds_work_project(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "my-company", "section": "context"},
            )
        )
        assert "My Company" in result

    async def test_explicit_scope(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "work:my-company", "section": "context"},
            )
        )
        assert "My Company" in result

    async def test_explicit_wrong_scope(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "projects:my-company", "section": "context"},
            )
        )
        assert "not found" in result.lower()

    async def test_first_match_wins(self, multi_scope_vault: Path) -> None:
        # Create a duplicate project name in the work scope
        dup = multi_scope_vault / "50_work" / "testproject"
        dup.mkdir(parents=True)
        (dup / "00-context.md").write_text(
            "---\nid: testproject-work\ntype: project\nstatus: active\n---\n\n# Work Copy\n"
        )
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "testproject", "section": "context"},
            )
        )
        # projects scope comes first → should find the original, not "Work Copy"
        assert "Test Project" in result

    async def test_meta_still_works(self, multi_scope_vault: Path) -> None:
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "_meta", "path": "patterns/pattern-tdd.md"},
            )
        )
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
            vault_path=git_multi_scope_vault,
            vault_scopes=MULTI_SCOPES,
        )
        result = _text(
            await mcp.call_tool(
                "vault_write",
                {
                    "project": "my-company",
                    "section": "lessons",
                    "operation": "append",
                    "content": "\n## New Lesson\nAlways test.\n",
                },
            )
        )
        assert "Updated" in result
        content = (git_multi_scope_vault / "50_work" / "my-company" / "90-lessons.md").read_text()
        assert "Always test" in content


class TestMultiScopeSearchRecent:
    async def test_project_filter_in_work_scope(
        self,
        git_multi_scope_vault: Path,
    ) -> None:
        mcp = create_server(
            vault_path=git_multi_scope_vault,
            vault_scopes=MULTI_SCOPES,
        )
        result = _text(
            await mcp.call_tool(
                "vault_search",
                {"project": "my-company", "since_days": 30},
            )
        )
        # Should find files in 50_work/my-company, not return "No changes"
        assert "my-company" in result or "50_work" in result


class TestHierarchicalScopeWriteGuard:
    """vault_write refuses to create entities at ambiguous locations in hierarchical scopes."""

    async def test_write_to_resolved_entity_works(self, git_multi_scope_vault: Path) -> None:
        """Writing to an existing resolved entity in hierarchical scope works."""
        products = git_multi_scope_vault / "50_work" / "20-products" / "hydra3d"
        products.mkdir(parents=True)
        (products / "00-context.md").write_text(
            "---\nid: hydra3d\ntype: project\nstatus: active\n---\n\n# Hydra3D\n"
        )
        import subprocess

        subprocess.run(
            ["git", "add", "."],
            cwd=git_multi_scope_vault,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add hydra3d"],
            cwd=git_multi_scope_vault,
            capture_output=True,
            check=True,
        )

        mcp = create_server(vault_path=git_multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_write",
                {
                    "project": "work:hydra3d",
                    "path": "notes.md",
                    "content": "# Notes\n",
                    "doc_type": "note",
                    "operation": "create",
                },
            )
        )
        assert "created" in result.lower()

    async def test_write_to_nonexistent_slug_without_path_fails(
        self,
        git_multi_scope_vault: Path,
    ) -> None:
        """Creating a file in a non-existent slug without category path returns error."""
        mcp = create_server(vault_path=git_multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_write",
                {
                    "project": "work:new-thing",
                    "path": "notes.md",
                    "content": "# Notes\n",
                    "doc_type": "note",
                    "operation": "create",
                },
            )
        )
        assert "not found" in result.lower()

    async def test_write_with_explicit_category_path_works(
        self,
        git_multi_scope_vault: Path,
    ) -> None:
        """Creating with explicit category/entity path works (vault_write resolves it)."""
        # my-company is a direct child of 50_work — use it as the target
        mcp = create_server(vault_path=git_multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_write",
                {
                    "project": "work:my-company",
                    "path": "40-runbooks/deploy.md",
                    "content": "# Deploy Guide\n",
                    "doc_type": "runbook",
                    "operation": "create",
                },
            )
        )
        assert "created" in result.lower()


class TestDuplicateNameDetection:
    """vault_health warns about duplicate directory names within a scope."""

    async def test_detects_duplicate_names(self, multi_scope_vault: Path) -> None:
        """Health report warns when same name exists at different depths."""
        (multi_scope_vault / "50_work" / "agents").mkdir(parents=True, exist_ok=True)
        (multi_scope_vault / "50_work" / "30-clients" / "acme" / "agents").mkdir(
            parents=True,
        )
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_health", {}))
        assert "duplicate" in result.lower()
        assert "agents" in result

    async def test_no_false_positive_without_duplicates(
        self,
        multi_scope_vault: Path,
    ) -> None:
        """No duplicate warning when all names are unique."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(await mcp.call_tool("vault_health", {}))
        assert "duplicate" not in result.lower()


class TestVaultSearchScopeFilter:
    """vault_search scope parameter restricts search to a specific scope."""

    async def test_scope_filter_limits_to_work(self, multi_scope_vault: Path) -> None:
        """Search with scope='work' only finds files in 50_work."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_search",
                {
                    "query": "Project",
                    "scope": "work",
                },
            )
        )
        assert "50_work" in result or "my-company" in result
        assert "10_projects" not in result

    async def test_scope_filter_limits_to_projects(self, multi_scope_vault: Path) -> None:
        """Search with scope='projects' only finds files in 10_projects."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_search",
                {
                    "query": "Project",
                    "scope": "projects",
                },
            )
        )
        assert "10_projects" in result or "testproject" in result
        assert "50_work" not in result

    async def test_scope_filter_invalid_scope(self, multi_scope_vault: Path) -> None:
        """Search with an invalid scope name returns error."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_search",
                {
                    "query": "anything",
                    "scope": "nonexistent",
                },
            )
        )
        assert "unknown scope" in result.lower()

    async def test_no_scope_searches_everything(self, multi_scope_vault: Path) -> None:
        """Without scope, searches the entire vault (backward compat)."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_search",
                {
                    "query": "Project",
                },
            )
        )
        assert "testproject" in result.lower() or "10_projects" in result
        assert "my-company" in result.lower() or "50_work" in result

    async def test_scope_filter_ranked_mode(self, multi_scope_vault: Path) -> None:
        """Scope filter works in ranked mode too."""
        mcp = create_server(vault_path=multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_search",
                {
                    "query": "Project",
                    "scope": "work",
                    "ranked": True,
                },
            )
        )
        assert "10_projects" not in result

    async def test_scope_filter_recent_mode(self, git_multi_scope_vault: Path) -> None:
        """Scope filter works in recent mode too."""
        mcp = create_server(vault_path=git_multi_scope_vault, vault_scopes=MULTI_SCOPES)
        result = _text(
            await mcp.call_tool(
                "vault_search",
                {
                    "scope": "work",
                    "since_days": 30,
                },
            )
        )
        assert "10_projects" not in result


# ── Path Traversal Protection ────────────────────────────────────────


class TestPathTraversal:
    async def test_query_path_escape_blocked(self, vault_mcp: FastMCP) -> None:
        # Needs enough ../.. to escape tmp_path (vault root)
        result = _text(
            await vault_mcp.call_tool(
                "vault_query",
                {"project": "testproject", "path": "../../../../etc/passwd"},
            )
        )
        assert "escapes vault boundary" in result.lower()

    async def test_create_path_escape_blocked(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_write",
                {
                    "project": "testproject",
                    "path": "../../../../tmp/evil.md",
                    "content": "pwned",
                    "doc_type": "test",
                    "operation": "create",
                },
            )
        )
        assert "escapes vault boundary" in result.lower()

    async def test_delegate_summarize_path_escape_blocked(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        result = _text(
            await vault_mcp.call_tool(
                "delegate_task",
                {"project": "testproject", "path": "../../../../etc/shadow"},
            )
        )
        assert "escapes vault boundary" in result.lower()

    async def test_project_param_traversal_blocked(
        self,
        vault_mcp: FastMCP,
    ) -> None:
        """H1: crafted project name must not escape vault boundary."""
        result = _text(
            await vault_mcp.call_tool(
                "vault_write",
                {
                    "project": "projects:../../etc",
                    "section": "context",
                    "operation": "append",
                    "content": "pwned",
                },
            )
        )
        assert "not found" in result.lower()

    async def test_yaml_injection_sanitized(self, git_vault: Path) -> None:
        """H2: newlines in doc_type must not inject YAML fields."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_write",
                {
                    "project": "testproject",
                    "path": "injected.md",
                    "content": "body",
                    "doc_type": "adr\nevil_field: true",
                    "operation": "create",
                },
            )
        )
        assert "created" in result.lower()
        content = (git_vault / "10_projects" / "testproject" / "injected.md").read_text()
        # Newline was stripped — no separate YAML key injected
        assert "evil_field: true\n" not in content
        # Type value is sanitized into a single safe string
        assert "\ntype: adr" in content

    async def test_patch_path_escape_blocked(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "../../../../etc/passwd",
                    "find": "root",
                    "replace": "pwned",
                },
            )
        )
        assert "escapes vault boundary" in result.lower()

    async def test_list_files_path_escape_blocked(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_list",
                {
                    "project": "testproject",
                    "path": "../../../../etc",
                },
            )
        )
        assert "escapes vault boundary" in result.lower()

    async def test_list_files_glob_capped(self, mock_vault: Path) -> None:
        """vault_list caps results at 500 entries."""
        project = mock_vault / "10_projects" / "testproject" / "bulk"
        project.mkdir(parents=True, exist_ok=True)
        for i in range(510):
            (project / f"file_{i:04d}.md").write_text(f"# File {i}\n")
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_list",
                {"project": "testproject", "path": "bulk", "pattern": "*.md"},
            )
        )
        # Should contain at most 500 file entries
        file_lines = [ln for ln in result.splitlines() if ln.startswith("- ")]
        assert len(file_lines) <= 500

    async def test_patch_invalid_keys_returns_error(self, git_vault: Path) -> None:
        """Patches with wrong dict keys return error, not KeyError crash."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "patches": [{"old": "wrong", "new": "keys"}],
                },
            )
        )
        assert "find" in result.lower() and "replace" in result.lower()


# ── vault_patch ─────────────────────────────────────────────────────


class TestVaultPatch:
    """Tests for vault_patch single and multi-replacement."""

    async def test_single_patch(self, git_vault: Path) -> None:
        """Single find/replace works."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "find": "- [ ] Task one",
                    "replace": "- [x] Task one",
                },
            )
        )
        assert "1 patch" in result.lower()
        content = (git_vault / "10_projects" / "testproject" / "11-tasks.md").read_text()
        assert "- [x] Task one" in content

    async def test_multi_patch_applies_all(self, git_vault: Path) -> None:
        """Multiple patches applied in sequence to the same file."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "patches": [
                        {"find": "- [ ] Task one", "replace": "- [x] Task one"},
                        {"find": "- [x] Task two", "replace": "- [ ] Task two reopened"},
                    ],
                },
            )
        )
        assert "2 patches" in result.lower()
        content = (git_vault / "10_projects" / "testproject" / "11-tasks.md").read_text()
        assert "- [x] Task one" in content
        assert "- [ ] Task two reopened" in content

    async def test_multi_patch_single_item(self, git_vault: Path) -> None:
        """A patches list with one item works fine."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "patches": [
                        {"find": "- [ ] Task one", "replace": "- [x] Task one"},
                    ],
                },
            )
        )
        assert "1 patch" in result.lower()

    async def test_multi_patch_rejects_mixed_params(self, git_vault: Path) -> None:
        """Providing both patches AND find/replace is an error."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "find": "- [ ] Task one",
                    "replace": "- [x] Task one",
                    "patches": [
                        {"find": "- [x] Task two", "replace": "- [ ] Task two"},
                    ],
                },
            )
        )
        assert "error" in result.lower() or "cannot" in result.lower() or "mix" in result.lower()
        # File must remain unchanged
        content = (git_vault / "10_projects" / "testproject" / "11-tasks.md").read_text()
        assert "- [ ] Task one" in content

    async def test_multi_patch_empty_list(self, git_vault: Path) -> None:
        """An empty patches list is an error."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "patches": [],
                },
            )
        )
        assert "provide" in result.lower() or "error" in result.lower()

    async def test_multi_patch_ambiguous_aborts_all(self, git_vault: Path) -> None:
        """If any patch in the list is ambiguous, no patches are applied."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "patches": [
                        {"find": "- [ ] Task one", "replace": "- [x] Task one"},
                        {"find": "Task", "replace": "Item"},  # ambiguous
                    ],
                },
            )
        )
        assert "ambiguous" in result.lower()
        # File must remain unchanged — first patch NOT applied
        content = (git_vault / "10_projects" / "testproject" / "11-tasks.md").read_text()
        assert "- [ ] Task one" in content

    async def test_multi_patch_not_found_aborts_all(self, git_vault: Path) -> None:
        """If any patch find text is not found, no patches are applied."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "patches": [
                        {"find": "- [ ] Task one", "replace": "- [x] Task one"},
                        {"find": "nonexistent text", "replace": "something"},
                    ],
                },
            )
        )
        assert "not found" in result.lower()
        # File must remain unchanged
        content = (git_vault / "10_projects" / "testproject" / "11-tasks.md").read_text()
        assert "- [ ] Task one" in content

    async def test_multi_patch_sequential_dependency(self, git_vault: Path) -> None:
        """Later patches see the result of earlier patches."""
        mcp = create_server(vault_path=git_vault)
        # First patch changes text, second patch modifies the result of the first
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "patches": [
                        {"find": "- [ ] Task one", "replace": "- [x] Task alpha"},
                        {"find": "- [x] Task alpha", "replace": "- [x] Task alpha (done)"},
                    ],
                },
            )
        )
        assert "2 patches" in result.lower()
        content = (git_vault / "10_projects" / "testproject" / "11-tasks.md").read_text()
        assert "- [x] Task alpha (done)" in content

    async def test_single_patch_project_not_found(self, git_vault: Path) -> None:
        """Single patch with unknown project returns error."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "nonexistent",
                    "path": "11-tasks.md",
                    "find": "foo",
                    "replace": "bar",
                },
            )
        )
        assert "not found" in result.lower()

    async def test_single_patch_file_not_found(self, git_vault: Path) -> None:
        """Single patch with unknown file returns error."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "nonexistent.md",
                    "find": "foo",
                    "replace": "bar",
                },
            )
        )
        assert "not found" in result.lower()

    async def test_single_patch_ambiguous(self, git_vault: Path) -> None:
        """Single patch with ambiguous match returns error."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "find": "Task",
                    "replace": "Item",
                },
            )
        )
        assert "ambiguous" in result.lower()

    async def test_single_patch_not_found_in_file(self, git_vault: Path) -> None:
        """Single patch with text not in file returns error."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "find": "nonexistent text here",
                    "replace": "replacement",
                },
            )
        )
        assert "not found" in result.lower()

    async def test_no_params_error(self, git_vault: Path) -> None:
        """Neither find/replace nor patches provided is an error."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                },
            )
        )
        assert "error" in result.lower() or "provide" in result.lower()

    async def test_multi_patch_single_git_commit(self, git_vault: Path) -> None:
        """Multi-patch produces exactly one git commit."""
        import subprocess

        mcp = create_server(vault_path=git_vault)
        # Count commits before
        before = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=git_vault,
            capture_output=True,
            text=True,
            check=True,
        )
        count_before = int(before.stdout.strip())

        await mcp.call_tool(
            "vault_patch",
            {
                "project": "testproject",
                "path": "11-tasks.md",
                "patches": [
                    {"find": "- [ ] Task one", "replace": "- [x] Task one"},
                    {"find": "- [x] Task two", "replace": "- [ ] Task two reopened"},
                ],
                # commit=True since ADR-018 made deferral the default; this
                # test is about the commit itself, not about which default
                # produces it.
                "commit": True,
            },
        )

        after = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=git_vault,
            capture_output=True,
            text=True,
            check=True,
        )
        count_after = int(after.stdout.strip())
        assert count_after == count_before + 1


class TestVaultPatchTolerantMatching:
    """Tests for tolerant matching in vault_patch (Issue #52)."""

    async def test_patch_tolerates_trailing_whitespace(
        self,
        git_vault: Path,
    ) -> None:
        """Multi-line find text with trailing whitespace differences."""
        tasks = git_vault / "10_projects" / "testproject" / "11-tasks.md"
        # Write a table with trailing spaces on each line
        raw = tasks.read_text()
        raw = raw.replace(
            "- [ ] Task one\n- [x] Task two",
            "| A | B |   \n|---|---|  \n| 1 | 2 |   ",
        )
        tasks.write_text(raw)
        import subprocess

        subprocess.run(
            ["git", "add", "."],
            cwd=git_vault,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "ws"],
            cwd=git_vault,
            capture_output=True,
        )

        mcp = create_server(vault_path=git_vault)
        # LLM stripped trailing spaces from multi-line text
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "find": "| A | B |\n|---|---|\n| 1 | 2 |",
                    "replace": "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |",
                },
            )
        )
        assert "1 patch" in result.lower()

    async def test_patch_not_found_shows_similarity(
        self,
        git_vault: Path,
    ) -> None:
        """Close miss includes similarity % in error."""
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "find": "- [ ] Task ones",
                    "replace": "- [x] Task one",
                },
            )
        )
        assert "not found" in result.lower()
        assert "%" in result

    async def test_patch_roundtrip_query_then_patch(
        self,
        git_vault: Path,
    ) -> None:
        """Real workflow: vault_query output used as vault_patch find text."""
        mcp = create_server(vault_path=git_vault)
        query_result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "testproject", "section": "tasks"},
            )
        )
        assert "- [ ] Task one" in query_result
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "find": "- [ ] Task one",
                    "replace": "- [x] Task one (completed)",
                },
            )
        )
        assert "1 patch" in result.lower()

    async def test_patch_body_only_match_when_frontmatter_overlaps(
        self,
        git_vault: Path,
    ) -> None:
        """find text matches body uniquely even if ambiguous in full file."""
        tasks = git_vault / "10_projects" / "testproject" / "11-tasks.md"
        # "active" is in the frontmatter status AND we add it to body
        raw = tasks.read_text()
        raw = raw.replace("- [ ] Task one", "- [ ] active task")
        tasks.write_text(raw)
        import subprocess

        subprocess.run(
            ["git", "add", "."],
            cwd=git_vault,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "ov"],
            cwd=git_vault,
            capture_output=True,
        )

        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "find": "- [ ] active task",
                    "replace": "- [x] active task (done)",
                },
            )
        )
        assert "1 patch" in result.lower()
        content = tasks.read_text()
        assert content.startswith("---")
        assert "- [x] active task (done)" in content


class TestGitCommitResilience:
    """Verify that git failures never crash the server or lose data."""

    async def test_git_oserror_does_not_crash(self, git_vault: Path) -> None:
        """OSError in _git_commit (e.g. git not in PATH) must not propagate."""
        from unittest.mock import patch

        mcp = create_server(vault_path=git_vault)

        with patch("hive._helpers.subprocess.run", side_effect=OSError("git not found")):
            result = _text(
                await mcp.call_tool(
                    "vault_patch",
                    {
                        "project": "testproject",
                        "path": "11-tasks.md",
                        "find": "- [ ] Task one",
                        "replace": "- [x] Task one done",
                    },
                )
            )

        assert "applied" in result.lower()
        # Data was written to disk despite git failure
        content = (git_vault / "10_projects" / "testproject" / "11-tasks.md").read_text()
        assert "- [x] Task one done" in content

    async def test_git_unexpected_exception_does_not_crash(self, git_vault: Path) -> None:
        """Any unexpected exception in _git_commit must be swallowed."""
        from unittest.mock import patch

        mcp = create_server(vault_path=git_vault)

        with patch("hive._helpers.subprocess.run", side_effect=RuntimeError("unexpected")):
            result = _text(
                await mcp.call_tool(
                    "vault_write",
                    {
                        "project": "testproject",
                        "section": "tasks",
                        "operation": "append",
                        "content": "\n- [ ] New task from test\n",
                    },
                )
            )

        assert "updated" in result.lower()

    async def test_server_responds_after_git_failure(self, git_vault: Path) -> None:
        """After a git failure, subsequent MCP calls still work."""
        from unittest.mock import patch

        mcp = create_server(vault_path=git_vault)

        # First call: git fails
        with patch("hive._helpers.subprocess.run", side_effect=OSError("git not found")):
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "find": "- [ ] Task one",
                    "replace": "- [x] Task one done",
                },
            )

        # Second call: read-only, must succeed (no mock — real subprocess)
        result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "testproject", "section": "tasks"},
            )
        )
        assert "task one done" in result.lower()


class TestGitReadResilience:
    """Verify _git_log/_git_recent don't crash on unexpected errors."""

    async def test_session_briefing_survives_git_oserror(self, git_vault: Path) -> None:
        """session_briefing must not crash if git binary is missing."""
        from unittest.mock import patch

        mcp = create_server(vault_path=git_vault)

        with patch("hive._helpers.subprocess.run", side_effect=OSError("git not found")):
            result = _text(
                await mcp.call_tool(
                    "session_briefing",
                    {"project": "testproject"},
                )
            )

        assert "session briefing" in result.lower()

    async def test_vault_search_recent_survives_git_oserror(self, git_vault: Path) -> None:
        """vault_search (recent mode) must not crash if git binary is missing."""
        from unittest.mock import patch

        mcp = create_server(vault_path=git_vault)

        with patch("hive._helpers.subprocess.run", side_effect=OSError("git not found")):
            result = _text(
                await mcp.call_tool(
                    "vault_search",
                    {"since_days": 7},
                )
            )

        # Should return gracefully (no changes or empty result)
        assert result  # non-empty response


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod 0o000/0o444 does not restrict access on Windows — POSIX-only",
)
class TestFileIOResilience:
    """Verify write tools return errors instead of crashing on I/O failures."""

    async def test_vault_patch_read_permission_error(self, git_vault: Path) -> None:
        """vault_patch returns error on read failure, not crash."""
        mcp = create_server(vault_path=git_vault)
        tasks = git_vault / "10_projects" / "testproject" / "11-tasks.md"
        tasks.chmod(0o000)
        try:
            result = _text(
                await mcp.call_tool(
                    "vault_patch",
                    {
                        "project": "testproject",
                        "path": "11-tasks.md",
                        "find": "foo",
                        "replace": "bar",
                    },
                )
            )
            # format_io_error wording: "Cannot read 'X': permission denied. ..."
            lower = result.lower()
            assert "permission" in lower or "error" in lower
        finally:
            tasks.chmod(0o644)

    async def test_vault_write_permission_error(self, git_vault: Path) -> None:
        """vault_write returns error on write failure, not crash."""
        mcp = create_server(vault_path=git_vault)
        tasks = git_vault / "10_projects" / "testproject" / "11-tasks.md"
        tasks.chmod(0o444)
        try:
            result = _text(
                await mcp.call_tool(
                    "vault_write",
                    {
                        "project": "testproject",
                        "section": "tasks",
                        "operation": "append",
                        "content": "\n- [ ] New task\n",
                    },
                )
            )
            lower = result.lower()
            assert "permission" in lower or "error" in lower
        finally:
            tasks.chmod(0o644)


class TestSectionFallback:
    async def test_bare_name_takes_priority(self, mock_vault: Path) -> None:
        # Create a bare context.md alongside the legacy 00-context.md
        project = mock_vault / "10_projects" / "testproject"
        (project / "context.md").write_text(
            "---\nid: bare-context\ntype: project\nstatus: active\n---\n\n# Bare Context\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "testproject", "section": "context"},
            )
        )
        assert "Bare Context" in result

    async def test_legacy_fallback(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "testproject", "section": "context"},
            )
        )
        assert "Test Project" in result


# ── capture_lesson (batch) ─────────────────────────────────────────────────


@pytest.fixture
def worker_vault(
    git_vault: Path,
    budget: BudgetTracker,
    ollama: OllamaClient,
    openrouter: OpenRouterClient,
) -> FastMCP:
    """Server with git vault + worker deps for capture_lesson (batch) tests."""
    return create_server(
        vault_path=git_vault,
        budget_tracker=budget,
        ollama_client=ollama,
        openrouter_client=openrouter,
    )


def _worker_response(text: str) -> ClientResponse:
    return ClientResponse(
        text=text,
        model="qwen2.5-coder:7b",
        tokens=100,
        cost_usd=0.0,
        latency_ms=500,
    )


_VALID_LESSONS_JSON = json.dumps(
    [
        {
            "title": "Always check return values",
            "context": "Debugging a crash in the parser",
            "problem": "Function returned None, caller didn't check",
            "solution": "Added explicit None guard before use",
            "tags": ["python", "debugging"],
            "confidence": 0.9,
        },
        {
            "title": "Use parameterized queries",
            "context": "Writing database layer",
            "problem": "String interpolation in SQL is injection risk",
            "solution": "Switched to ? placeholders",
            "tags": ["sql", "security"],
            "confidence": 0.8,
        },
    ]
)


class TestExtractLessons:
    """capture_lesson (batch) tool — worker-powered lesson extraction."""

    @pytest.mark.asyncio
    async def test_extracts_and_writes_lessons(
        self,
        git_vault: Path,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=_worker_response(_VALID_LESSONS_JSON),
        )
        result = _text(
            await worker_vault.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": "We found a crash..."},
            )
        )
        assert "2" in result  # 2 lessons extracted
        assert "Always check return values" in result

        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        assert "Always check return values" in lessons
        assert "Use parameterized queries" in lessons

    @pytest.mark.asyncio
    async def test_deduplicates_existing(
        self,
        git_vault: Path,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        # Pre-write a lesson with same title
        lessons_file = git_vault / "10_projects" / "testproject" / "90-lessons.md"
        existing = lessons_file.read_text()
        lessons_file.write_text(
            existing + "\n### [2026-01-01] Always check return values\nExisting.\n"
        )
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=_worker_response(_VALID_LESSONS_JSON),
        )
        result = _text(
            await worker_vault.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": "session text"},
            )
        )
        # First lesson skipped (duplicate), second written
        assert "skipped" in result.lower() or "duplicate" in result.lower()
        assert "Use parameterized queries" in result

    @pytest.mark.asyncio
    async def test_filters_low_confidence(
        self,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        low_conf = json.dumps(
            [
                {
                    "title": "Maybe this matters",
                    "context": "ctx",
                    "problem": "prob",
                    "solution": "sol",
                    "tags": [],
                    "confidence": 0.3,
                },
            ]
        )
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=_worker_response(low_conf),
        )
        result = _text(
            await worker_vault.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": "some text", "min_confidence": 0.7},
            )
        )
        assert "0" in result or "no lessons" in result.lower()

    @pytest.mark.asyncio
    async def test_worker_unavailable(
        self,
        worker_vault: FastMCP,
        ollama: OllamaClient,
        openrouter: OpenRouterClient,
    ) -> None:
        ollama.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]
        openrouter.generate = AsyncMock(  # type: ignore[method-assign]
            side_effect=ConnectionError("no workers"),
        )
        result = _text(
            await worker_vault.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": "some text"},
            )
        )
        assert "unavailable" in result.lower() or "worker" in result.lower()

    @pytest.mark.asyncio
    async def test_worker_invalid_json(
        self,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=_worker_response("This is not JSON at all, just text."),
        )
        result = _text(
            await worker_vault.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": "some text"},
            )
        )
        assert "parse" in result.lower() or "invalid" in result.lower()

    @pytest.mark.asyncio
    async def test_worker_empty_array(
        self,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=_worker_response("[]"),
        )
        result = _text(
            await worker_vault.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": "nothing interesting"},
            )
        )
        assert "no lessons" in result.lower()

    @pytest.mark.asyncio
    async def test_markdown_wrapped_json(
        self,
        git_vault: Path,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        """Worker wraps JSON in markdown fences — still parsed correctly."""
        wrapped = (
            "```json\n"
            + json.dumps(
                [
                    {
                        "title": "Fence test",
                        "context": "ctx",
                        "problem": "prob",
                        "solution": "sol",
                        "tags": [],
                        "confidence": 0.9,
                    }
                ]
            )
            + "\n```"
        )
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=_worker_response(wrapped),
        )
        result = _text(
            await worker_vault.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": "session notes"},
            )
        )
        assert "Fence test" in result

    @pytest.mark.asyncio
    async def test_respects_max_lessons(
        self,
        git_vault: Path,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        many = json.dumps(
            [
                {
                    "title": f"Lesson {i}",
                    "context": "c",
                    "problem": "p",
                    "solution": "s",
                    "tags": [],
                    "confidence": 0.9,
                }
                for i in range(10)
            ]
        )
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=_worker_response(many),
        )
        await worker_vault.call_tool(
            "capture_lesson",
            {"project": "testproject", "text": "big session", "max_lessons": 3},
        )
        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        # At most 3 lessons written
        count = lessons.count("### [")
        assert count <= 3

    @pytest.mark.asyncio
    async def test_sanitizes_newlines_in_worker_output(
        self,
        git_vault: Path,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        """Worker output with newlines in fields is sanitized."""
        injected = json.dumps(
            [
                {
                    "title": "Good title\nevil_field: true",
                    "context": "ctx\ninjection",
                    "problem": "prob",
                    "solution": "sol",
                    "tags": [],
                    "confidence": 0.9,
                }
            ]
        )
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=_worker_response(injected),
        )
        result = _text(
            await worker_vault.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": "session"},
            )
        )
        assert "Good title" in result
        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        # Newlines stripped — no injection
        assert "evil_field: true" not in lessons.split("\n")
        assert "\nevil_field" not in lessons

    @pytest.mark.asyncio
    async def test_write_error_surfaces_in_summary(
        self,
        git_vault: Path,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        """Filesystem errors during lesson write are reported, not silently dropped."""
        lessons_file = git_vault / "10_projects" / "testproject" / "90-lessons.md"
        # Make file read-only so appends fail
        lessons_file.chmod(0o444)
        try:
            ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
            ollama.generate = AsyncMock(  # type: ignore[method-assign]
                return_value=_worker_response(_VALID_LESSONS_JSON),
            )
            result = _text(
                await worker_vault.call_tool(
                    "capture_lesson",
                    {"project": "testproject", "text": "session notes"},
                )
            )
            lower = result.lower()
            assert "permission" in lower or "write error" in lower or "error" in lower
        finally:
            lessons_file.chmod(0o644)

    @pytest.mark.asyncio
    async def test_curly_braces_in_user_text(
        self,
        git_vault: Path,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        """User text with curly braces (code, JSON) doesn't crash str.format()."""
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=_worker_response(_VALID_LESSONS_JSON),
        )
        # Text containing curly braces — would crash without escaping
        result = _text(
            await worker_vault.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": 'def foo(): return {"key": value}'},
            )
        )
        assert "Always check return values" in result

    @pytest.mark.asyncio
    async def test_sanitizes_newlines_in_tags(
        self,
        git_vault: Path,
        worker_vault: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        """Tags with newlines are sanitized to prevent markdown injection."""
        injected_tags = json.dumps(
            [
                {
                    "title": "Tag injection test",
                    "context": "ctx",
                    "problem": "prob",
                    "solution": "sol",
                    "tags": ["python\n### Fake Lesson"],
                    "confidence": 0.9,
                }
            ]
        )
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=_worker_response(injected_tags),
        )
        result = _text(
            await worker_vault.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": "session"},
            )
        )
        assert "Tag injection test" in result
        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text()
        # Newline sanitized: "### Fake Lesson" must NOT appear as a standalone heading
        for line in lessons.splitlines():
            assert not line.startswith("### Fake Lesson")


# ── vault_health (validation) ──────────────────────────────────────


class TestVaultValidate:
    """vault_health validation mode — drift detection and vault linting."""

    async def test_unknown_check_names_rejected(self, mock_vault: Path) -> None:
        """Typo in check name returns error instead of false positive."""
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmater"]},
            )
        )
        assert "Unknown check" in result
        assert "frontmatter" in result

    async def test_validation_empty_vault(self, tmp_path: Path) -> None:
        """Validation with checks on vault with no projects returns clean."""
        scope = tmp_path / "10_projects"
        scope.mkdir()
        mcp = create_server(vault_path=tmp_path)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter"]},
            )
        )
        assert "no projects" in result.lower() or "0 issues" in result.lower()

    async def test_healthy_vault_no_errors(self, mock_vault: Path) -> None:
        """Well-formed vault produces no error-level issues."""
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter", "stale", "links"]},
            )
        )
        # Anchor on the `[error]` issue marker (and the "Vault clean"
        # all-green message) rather than the bare substring — the
        # identity block now exposes ``vault_path`` which may contain
        # "error" inside a pytest tmp dir name (e.g. test_healthy_…0).
        assert "[error]" not in result or "Vault clean" in result

    async def test_missing_frontmatter_detected(self, mock_vault: Path) -> None:
        """File with no frontmatter is flagged."""
        bad = mock_vault / "10_projects" / "testproject" / "no-frontmatter.md"
        bad.write_text("# Just a heading\n\nNo frontmatter here.\n")
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter", "stale", "links"]},
            )
        )
        assert "no-frontmatter.md" in result
        assert "frontmatter" in result.lower()

    async def test_incomplete_frontmatter_detected(self, mock_vault: Path) -> None:
        """Frontmatter missing required fields is flagged."""
        bad = mock_vault / "10_projects" / "testproject" / "incomplete.md"
        bad.write_text("---\nid: incomplete\n---\n\n# Missing type and status\n")
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter", "stale", "links"]},
            )
        )
        assert "incomplete.md" in result
        assert "type" in result.lower() or "status" in result.lower()

    async def test_unparseable_date_detected(self, mock_vault: Path) -> None:
        """Frontmatter with bad created date is flagged."""
        bad = mock_vault / "10_projects" / "testproject" / "bad-date.md"
        bad.write_text(
            '---\nid: bad-date\ntype: note\nstatus: active\ncreated: "not-a-date"\n'
            "---\n\n# Bad date\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter", "stale", "links"]},
            )
        )
        assert "bad-date.md" in result
        assert "date" in result.lower()

    async def test_stale_file_detected(self, mock_vault: Path) -> None:
        """Active file with old created date is flagged as stale."""
        stale = mock_vault / "10_projects" / "testproject" / "ancient.md"
        stale.write_text(
            '---\nid: ancient\ntype: note\nstatus: active\ncreated: "2020-01-01"\n'
            "---\n\n# Very old file\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter", "stale", "links"]},
            )
        )
        assert "ancient.md" in result
        assert "stale" in result.lower()

    async def test_terminal_status_not_flagged_stale(self, mock_vault: Path) -> None:
        """Completed/archived files are NOT flagged as stale."""
        old = mock_vault / "10_projects" / "testproject" / "old-done.md"
        old.write_text(
            '---\nid: old-done\ntype: note\nstatus: completed\ncreated: "2020-01-01"\n'
            "---\n\n# Old but done\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter", "stale", "links"]},
            )
        )
        assert "old-done.md" not in result

    async def test_broken_wikilink_detected(self, mock_vault: Path) -> None:
        """Wikilink pointing to nonexistent file is flagged."""
        doc = mock_vault / "10_projects" / "testproject" / "with-links.md"
        doc.write_text(
            "---\nid: with-links\ntype: note\nstatus: active\n---\n\n"
            "See [[nonexistent-doc]] for details.\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter", "stale", "links"]},
            )
        )
        assert "nonexistent-doc" in result
        assert "link" in result.lower() or "broken" in result.lower()

    async def test_valid_wikilink_not_flagged(self, mock_vault: Path) -> None:
        """Wikilink to existing file is NOT flagged."""
        doc = mock_vault / "10_projects" / "testproject" / "good-link.md"
        doc.write_text(
            "---\nid: good-link\ntype: note\nstatus: active\n---\n\n"
            "See [[adr-001-test]] for details.\n"
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter", "stale", "links"]},
            )
        )
        assert "adr-001-test" not in result

    async def test_project_filter(self, mock_vault: Path) -> None:
        """Only validates the specified project."""
        # Add a bad file to a different project
        other = mock_vault / "10_projects" / "otherproject"
        other.mkdir(parents=True)
        (other / "broken.md").write_text("No frontmatter here.\n")
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"project": "testproject"},
            )
        )
        assert "broken.md" not in result

    async def test_checks_filter(self, mock_vault: Path) -> None:
        """Only runs specified checks."""
        stale = mock_vault / "10_projects" / "testproject" / "ancient2.md"
        stale.write_text(
            '---\nid: ancient2\ntype: note\nstatus: active\ncreated: "2020-01-01"\n'
            "---\n\n# Very old\n"
        )
        bad = mock_vault / "10_projects" / "testproject" / "no-fm.md"
        bad.write_text("No frontmatter.\n")
        mcp = create_server(vault_path=mock_vault)
        # Only run frontmatter check — stale should not appear
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter"]},
            )
        )
        assert "no-fm.md" in result
        assert "ancient2.md" not in result

    async def test_max_issues_cap(self, mock_vault: Path) -> None:
        """Output is capped at max_issues."""
        project = mock_vault / "10_projects" / "testproject"
        for i in range(20):
            (project / f"bad-{i}.md").write_text("No frontmatter.\n")
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["frontmatter"], "max_issues": 5},
            )
        )
        assert "truncated" in result.lower() or "more" in result.lower()

    async def test_project_not_found(self, mock_vault: Path) -> None:
        """Unknown project returns error."""
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"project": "nonexistent", "checks": ["frontmatter"]},
            )
        )
        assert "not found" in result.lower()

    async def test_wikilink_inside_fenced_code_block_not_flagged(
        self,
        mock_vault: Path,
    ) -> None:
        """Bash regex classes (e.g. [[:space:]], [[ -z "$1" ]]) inside fenced
        code blocks must NOT be parsed as wikilinks. Currently false-positives.
        """
        doc = mock_vault / "10_projects" / "testproject" / "shell-snippets.md"
        doc.write_text(
            "---\nid: shell-snippets\ntype: note\nstatus: active\n---\n\n"
            "# Shell snippets\n\n"
            "```bash\n"
            'if [[ -z "$1" ]]; then echo "no arg"; fi\n'
            'echo "$x" | grep "[[:space:]]"\n'
            "```\n\n"
            "And a tilde fence:\n\n"
            "~~~bash\n"
            '[[ "$x" =~ ^[[:digit:]]+$ ]]\n'
            "~~~\n",
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"project": "testproject", "checks": ["links"]},
            )
        )
        assert "shell-snippets.md" not in result, (
            f"Bash regex inside fenced blocks should not produce broken-link "
            f"warnings; got: {result}"
        )

    async def test_escaped_pipe_in_wikilink_resolves_target(
        self,
        mock_vault: Path,
    ) -> None:
        """`[[target\\|alias]]` is Obsidian's escape for pipes inside table cells.
        The parser must treat `\\|` as an alias separator, not part of the target.
        """
        doc = mock_vault / "10_projects" / "testproject" / "with-escaped-pipe.md"
        doc.write_text(
            "---\nid: with-escaped-pipe\ntype: note\nstatus: active\n---\n\n"
            "| Col | Link |\n"
            "|-----|------|\n"
            "| ADR | [[adr-001-test\\|the ADR]] |\n",
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"project": "testproject", "checks": ["links"]},
            )
        )
        # The target adr-001-test exists; the escaped pipe must be parsed as
        # an alias separator, so no broken-link warning should appear.
        assert "with-escaped-pipe.md" not in result, (
            f"Escaped-pipe wikilink should resolve to existing target; got: {result}"
        )

    async def test_subdir_path_wikilink_to_existing_file_not_flagged(
        self,
        mock_vault: Path,
    ) -> None:
        """`[[30-architecture/adr-001-test|alias]]` should resolve to the
        existing file `30-architecture/adr-001-test.md`, not be flagged broken.
        """
        doc = mock_vault / "10_projects" / "testproject" / "with-subdir-link.md"
        doc.write_text(
            "---\nid: with-subdir-link\ntype: note\nstatus: active\n---\n\n"
            "See [[30-architecture/adr-001-test|the ADR]] for the rationale.\n",
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"project": "testproject", "checks": ["links"]},
            )
        )
        assert "with-subdir-link.md" not in result, (
            f"Subdir-path wikilink to existing file should not be flagged; got: {result}"
        )

    async def test_memory_file_skipped_by_frontmatter_check(
        self,
        mock_vault: Path,
    ) -> None:
        """Files under */memory/ use Claude auto-memory schema
        (`name`/`description`/`metadata.type`), not the standard vault schema.
        Frontmatter check must skip them rather than flagging missing
        id/type/status fields.
        """
        mem_dir = mock_vault / "10_projects" / "testproject" / "memory"
        mem_dir.mkdir()
        (mem_dir / "feedback_example.md").write_text(
            "---\nname: feedback-example\n"
            "description: example feedback memory\n"
            "metadata:\n  type: feedback\n---\n\n"
            "Lead with the rule.\n",
        )
        (mem_dir / "MEMORY.md").write_text(
            "# Project Memory\n\nIndex content with no frontmatter.\n",
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"project": "testproject", "checks": ["frontmatter"]},
            )
        )
        assert "memory/feedback_example.md" not in result, (
            f"Auto-memory schema must be accepted; got: {result}"
        )
        assert "memory/MEMORY.md" not in result, f"MEMORY.md must be skipped; got: {result}"

    async def test_meta_scope_wikilink_resolves(
        self,
        mock_vault: Path,
    ) -> None:
        """`[[pattern-X]]` targeting `00_meta/patterns/pattern-X.md` must
        resolve. The meta scope is the canonical home for cross-project
        patterns/templates and must be a valid link target from every project.

        Regression for #94 category 1.
        """
        doc = mock_vault / "10_projects" / "testproject" / "with-meta-link.md"
        doc.write_text(
            "---\nid: with-meta-link\ntype: note\nstatus: active\n---\n\n"
            "See [[pattern-tdd]] for context.\n"
            "Also [[_meta/patterns/pattern-tdd]] (scope-rooted form).\n",
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"project": "testproject", "checks": ["links"]},
            )
        )
        assert "with-meta-link.md" not in result, (
            f"Wikilink to existing meta-scope target must resolve; got: {result}"
        )

    async def test_cross_project_vault_rooted_wikilink_resolves(
        self,
        mock_vault: Path,
    ) -> None:
        """`[[other-project/30-architecture/adr-X]]` must resolve to the
        existing file under another project in the same scope. This is the
        standard Obsidian disambiguation form for cross-project references.

        Regression for #94 category 2.
        """
        kubelab = mock_vault / "10_projects" / "kubelab"
        kubelab.mkdir()
        (kubelab / "00-context.md").write_text(
            "---\nid: kubelab\ntype: project\nstatus: active\n---\n\n# Kubelab\n",
        )
        adrs = kubelab / "30-architecture" / "adrs"
        adrs.mkdir(parents=True)
        (adrs / "adr-038-orchestrator-architecture.md").write_text(
            "---\nid: adr-038\ntype: adr\nstatus: accepted\n---\n\n# ADR-038\n",
        )

        doc = mock_vault / "10_projects" / "testproject" / "with-xproj-link.md"
        doc.write_text(
            "---\nid: with-xproj-link\ntype: note\nstatus: active\n---\n\n"
            "Driven by "
            "[[kubelab/30-architecture/adrs/adr-038-orchestrator-architecture"
            "|ADR-038]].\n",
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"checks": ["links"]},
            )
        )
        assert "with-xproj-link.md" not in result, (
            f"Cross-project vault-rooted wikilink must resolve; got: {result}"
        )

    async def test_posix_class_in_heading_not_flagged(
        self,
        mock_vault: Path,
    ) -> None:
        """POSIX character classes (`[[:space:]]`, `[[:digit:]]`, etc.) that
        appear in markdown headings or prose (outside fenced/inline code) must
        not be reported as broken wikilinks. No valid Obsidian filename matches
        the shape `:<lowercase>:`.

        Regression for #94 category 3.
        """
        doc = mock_vault / "10_projects" / "testproject" / "posix-heading.md"
        # Explicit UTF-8 — Path.write_text defaults to platform encoding
        # (cp1252 on Windows), which encodes em dash as 0x97; _safe_read
        # then fails to decode as UTF-8 and the file is reported as
        # unreadable, masking the actual POSIX-class regression check.
        doc.write_text(
            "---\nid: posix-heading\ntype: note\nstatus: active\n---\n\n"
            "### \\s is not POSIX — use [[:space:]] in bash regex\n\n"
            "And the digit class [[:digit:]] is similar.\n",
            encoding="utf-8",
        )
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_health",
                {"project": "testproject", "checks": ["links"]},
            )
        )
        assert "posix-heading.md" not in result, (
            f"POSIX character classes outside code must not be flagged; got: {result}"
        )


# ── _setup_file_logging ─────────────────────────────────────────────


class TestSetupFileLogging:
    def test_creates_log_file_and_handler(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import logging
        import os

        from hive.server import _setup_file_logging

        log_file = tmp_path / "subdir" / "test.log"
        monkeypatch.setattr("hive.server.settings.log_path", str(log_file))

        _setup_file_logging()

        # Per-PID suffix: test.log -> test-{pid}.log
        expected = log_file.with_name(f"test-{os.getpid()}.log")

        logger = logging.getLogger("hive")
        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
            and str(expected) in str(getattr(h, "baseFilename", ""))
        ]
        assert len(file_handlers) == 1

        # Log directory was created
        assert log_file.parent.is_dir()

        # Cleanup: remove the handler to avoid leaking into other tests
        for h in file_handlers:
            logger.removeHandler(h)
            h.close()


# ── _gc_stale_logs (#329) ──────────────────────────────────────────


class TestGcStaleLogs:
    """One ``hive-<pid>.log`` per process start, never collected (#329: 295
    observed). Each file is size-capped by ``RotatingFileHandler``, so the
    unbounded axis is the file *count*, not the file size.

    Deletion needs two guards, not one. Age alone is unsafe: an idle daemon
    that has not logged in a week has a stale mtime while still holding the
    file open, and unlinking it on POSIX silently detaches the writer's inode.
    """

    STALE = 30 * 86_400

    @staticmethod
    def _template(tmp_path: Path) -> Path:
        return tmp_path / "hive.log"

    def _make_log(self, tmp_path: Path, pid: int, *, age_s: float, suffix: str = "") -> Path:
        path = tmp_path / f"hive-{pid}.log{suffix}"
        path.write_text("x", encoding="utf-8")
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
        return path

    def test_stale_log_from_dead_pid_is_deleted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hive.server import _gc_stale_logs

        monkeypatch.setattr("psutil.pid_exists", lambda _pid: False)
        victim = self._make_log(tmp_path, 424242, age_s=self.STALE)

        _gc_stale_logs(self._template(tmp_path), 7)

        assert not victim.exists()

    def test_rotated_backup_is_collected_too(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``RotatingFileHandler`` leaves ``hive-<pid>.log.1`` beside the live
        file; collecting only the base name would halve the reclaimed space."""
        from hive.server import _gc_stale_logs

        monkeypatch.setattr("psutil.pid_exists", lambda _pid: False)
        backup = self._make_log(tmp_path, 424242, age_s=self.STALE, suffix=".1")

        _gc_stale_logs(self._template(tmp_path), 7)

        assert not backup.exists()

    def test_fresh_log_is_kept(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hive.server import _gc_stale_logs

        monkeypatch.setattr("psutil.pid_exists", lambda _pid: False)
        recent = self._make_log(tmp_path, 424242, age_s=60)

        _gc_stale_logs(self._template(tmp_path), 7)

        assert recent.exists()

    def test_live_pid_log_is_kept_even_when_stale(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The guard that age alone cannot provide: an idle-but-running daemon."""
        from hive.server import _gc_stale_logs

        monkeypatch.setattr("psutil.pid_exists", lambda _pid: True)
        idle_daemon = self._make_log(tmp_path, 424242, age_s=self.STALE)

        _gc_stale_logs(self._template(tmp_path), 7)

        assert idle_daemon.exists()

    def test_own_log_is_never_collected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hive.server import _gc_stale_logs

        # Even with the liveness probe lying about us, our own PID is excluded.
        monkeypatch.setattr("psutil.pid_exists", lambda _pid: False)
        mine = self._make_log(tmp_path, os.getpid(), age_s=self.STALE)

        _gc_stale_logs(self._template(tmp_path), 7)

        assert mine.exists()

    def test_unrelated_files_are_untouched(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hive.server import _gc_stale_logs

        monkeypatch.setattr("psutil.pid_exists", lambda _pid: False)
        stamp = time.time() - self.STALE
        keepers = []
        for name in ("worker.db", "hive.log", "hive-notapid.log", "relevance.db"):
            path = tmp_path / name
            path.write_text("x", encoding="utf-8")
            os.utime(path, (stamp, stamp))
            keepers.append(path)

        _gc_stale_logs(self._template(tmp_path), 7)

        assert all(p.exists() for p in keepers)

    def test_unlink_failure_never_propagates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A GC failure must not take down server startup — AGENTS.md's
        broad-``Exception`` rule, and on Windows an in-use file raises
        ``PermissionError`` here rather than anything git-shaped."""
        from pathlib import Path as _Path

        from hive.server import _gc_stale_logs

        monkeypatch.setattr("psutil.pid_exists", lambda _pid: False)
        self._make_log(tmp_path, 424242, age_s=self.STALE)

        def _boom(self: Path, **_kw: object) -> None:
            raise PermissionError("file in use")

        monkeypatch.setattr(_Path, "unlink", _boom)

        _gc_stale_logs(self._template(tmp_path), 7)  # must not raise

    def test_missing_log_dir_is_a_no_op(self, tmp_path: Path) -> None:
        from hive.server import _gc_stale_logs

        _gc_stale_logs(tmp_path / "absent" / "hive.log", 7)  # must not raise


# ── CLI dispatch (hive --version / --help / unknown token) ─────────


class TestCliDispatch:
    """`main()` runs the stdio MCP server ONLY on a bare invocation (the v1
    per-session contract). Any explicit token routes to a subcommand or a usage
    error — `hive --version` must print a version, never boot the daemon.
    """

    def test_version_flag_prints_and_exits_zero(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hive.server import _dispatch

        assert _dispatch(["--version"]) == 0
        assert "hive-vault" in capsys.readouterr().out

    def test_version_short_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        from hive.server import _dispatch

        assert _dispatch(["-V"]) == 0
        assert "hive-vault" in capsys.readouterr().out

    def test_help_flag_prints_usage_and_exits_zero(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hive.server import _dispatch

        assert _dispatch(["--help"]) == 0
        assert "usage" in capsys.readouterr().out.lower()

    def test_help_short_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        from hive.server import _dispatch

        assert _dispatch(["-h"]) == 0
        assert "usage" in capsys.readouterr().out.lower()

    def test_unknown_command_exits_two_on_stderr(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hive.server import _dispatch

        assert _dispatch(["frobnicate"]) == 2
        assert "unknown command" in capsys.readouterr().err.lower()

    def test_stray_flag_exits_two_not_server_launch(self) -> None:
        from hive.server import _dispatch

        # The original footgun: a stray flag used to fall through to the
        # stdio-server `else` branch instead of erroring.
        assert _dispatch(["--bogus"]) == 2

    def test_serve_routes_to_run_serve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hive import server

        seen: dict[str, list[str]] = {}
        monkeypatch.setattr(
            server,
            "_run_serve",
            lambda argv: (seen.update(argv=argv), 0)[1],
        )
        assert server._dispatch(["serve", "--port", "9"]) == 0
        assert seen["argv"] == ["--port", "9"]

    def test_bare_invocation_runs_stdio_server(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hive import server

        monkeypatch.setattr(server, "_setup_file_logging", lambda: None)
        run = Mock()
        monkeypatch.setattr(server, "create_server", lambda: Mock(run=run))
        monkeypatch.setattr(sys, "argv", ["hive"])

        server.main()  # bare path returns normally, no SystemExit

        run.assert_called_once_with()

    def test_version_via_main_exits_zero_without_launching_server(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hive import server

        monkeypatch.setattr(server, "_setup_file_logging", lambda: None)
        never = Mock()
        monkeypatch.setattr(server, "create_server", never)
        monkeypatch.setattr(sys, "argv", ["hive", "--version"])

        with pytest.raises(SystemExit) as exc:
            server.main()

        assert exc.value.code == 0
        never.assert_not_called()

    def test_unknown_token_via_main_exits_two_without_launching_server(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hive import server

        monkeypatch.setattr(server, "_setup_file_logging", lambda: None)
        never = Mock()
        monkeypatch.setattr(server, "create_server", never)
        monkeypatch.setattr(sys, "argv", ["hive", "--bogus"])

        with pytest.raises(SystemExit) as exc:
            server.main()

        assert exc.value.code == 2
        never.assert_not_called()


# ── Timeout tests (issue #63) ──────────────────────────────────────


class TestToolTimeouts:
    """Verify async tools return error on timeout instead of hanging."""

    @pytest.mark.asyncio
    async def test_delegate_task_timeout(
        self,
        worker: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        """delegate_task returns timeout message when worker hangs."""

        async def slow_generate(*a: object, **kw: object) -> ClientResponse:
            await asyncio.sleep(999)
            raise AssertionError("unreachable")

        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(side_effect=slow_generate)  # type: ignore[method-assign]

        ctx = worker._hive_ctx  # type: ignore[attr-defined]
        ctx.tool_timeout = 0.1

        result = _text(await worker.call_tool("delegate_task", {"prompt": "test"}))
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_worker_status_timeout(
        self,
        worker: FastMCP,
        ollama: OllamaClient,
    ) -> None:
        """worker_status returns timeout message when connectivity check hangs."""

        async def slow_check() -> bool:
            await asyncio.sleep(999)
            return True

        ollama.is_available = AsyncMock(side_effect=slow_check)  # type: ignore[method-assign]

        ctx = worker._hive_ctx  # type: ignore[attr-defined]
        ctx.tool_timeout = 0.1

        result = _text(await worker.call_tool("worker_status", {}))
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_capture_lesson_batch_timeout(
        self,
        git_vault: Path,
        budget: BudgetTracker,
        ollama: OllamaClient,
        openrouter: OpenRouterClient,
    ) -> None:
        """capture_lesson batch mode returns timeout when worker hangs."""
        mcp = create_server(
            vault_path=git_vault,
            budget_tracker=budget,
            ollama_client=ollama,
            openrouter_client=openrouter,
        )

        async def slow_generate(*a: object, **kw: object) -> ClientResponse:
            await asyncio.sleep(999)
            raise AssertionError("unreachable")

        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(side_effect=slow_generate)  # type: ignore[method-assign]

        ctx = mcp._hive_ctx  # type: ignore[attr-defined]
        ctx.tool_timeout = 0.1

        result = _text(
            await mcp.call_tool(
                "capture_lesson",
                {"project": "testproject", "text": "some text to extract"},
            )
        )
        assert "timed out" in result.lower()
        _close_server(mcp)


class TestWriteLockTimeout:
    """Verify vault_write/vault_patch return error on lock timeout."""

    @pytest.mark.asyncio
    async def test_write_lock_timeout_returns_error(
        self,
        git_vault: Path,
    ) -> None:
        """vault_write returns friendly error when write lock cannot be acquired."""
        from unittest.mock import patch

        mcp = create_server(vault_path=git_vault)

        with patch("hive._helpers._WRITE_LOCK") as mock_lock:
            mock_lock.acquire.return_value = False
            result = _text(
                await mcp.call_tool(
                    "vault_write",
                    {
                        "project": "testproject",
                        "section": "lessons",
                        "operation": "append",
                        "content": "\nNew content\n",
                    },
                )
            )
        assert "busy" in result.lower() or "timeout" in result.lower()
        _close_server(mcp)

    @pytest.mark.asyncio
    async def test_patch_lock_timeout_returns_error(
        self,
        git_vault: Path,
    ) -> None:
        """vault_patch returns friendly error when write lock cannot be acquired."""
        from unittest.mock import patch

        mcp = create_server(vault_path=git_vault)

        with patch("hive._helpers._WRITE_LOCK") as mock_lock:
            mock_lock.acquire.return_value = False
            result = _text(
                await mcp.call_tool(
                    "vault_patch",
                    {
                        "project": "testproject",
                        "path": "11-tasks.md",
                        "find": "- [ ] Task one",
                        "replace": "- [x] Task one done",
                    },
                )
            )
        assert "busy" in result.lower() or "timeout" in result.lower()
        _close_server(mcp)

    @pytest.mark.asyncio
    async def test_create_lock_timeout_returns_error(
        self,
        git_vault: Path,
    ) -> None:
        """vault_write create mode returns error when write lock cannot be acquired."""
        from unittest.mock import patch

        mcp = create_server(vault_path=git_vault)

        with patch("hive._helpers._WRITE_LOCK") as mock_lock:
            mock_lock.acquire.return_value = False
            result = _text(
                await mcp.call_tool(
                    "vault_write",
                    {
                        "project": "testproject",
                        "operation": "create",
                        "path": "new-doc.md",
                        "doc_type": "note",
                        "content": "# New Doc\n",
                    },
                )
            )
        assert "busy" in result.lower() or "timeout" in result.lower()
        _close_server(mcp)


# ── agents scope (HIVE-120) ─────────────────────────────────────────
#
# These exercise the *default* scopes (no vault_scopes= arg) so they prove
# the shipped default now includes "agents": "80_agents" and that every
# tool treats an 80_agents/ subdir as a first-class project, identical to
# 10_projects/ and 50_work/.


def _seed_agent(vault: Path) -> Path:
    """Create an 80_agents/Hermes-NaN project inside *vault*."""
    agent = vault / "80_agents" / "Hermes-NaN"
    agent.mkdir(parents=True)
    (agent / "00-context.md").write_text(
        "---\nid: hermes-nan\ntype: project\nstatus: active\n---\n\n"
        "# Hermes-NaN\n\nRemote ops agent on NaN infrastructure.\n",
    )
    return agent


class TestAgentsScope:
    async def test_vault_list_shows_agents(self, mock_vault: Path) -> None:
        _seed_agent(mock_vault)
        mcp = create_server(vault_path=mock_vault)
        result = _text(await mcp.call_tool("vault_list", {}))
        assert "agents/Hermes-NaN" in result
        _close_server(mcp)

    async def test_query_explicit_agents_scope(self, mock_vault: Path) -> None:
        _seed_agent(mock_vault)
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "agents:Hermes-NaN", "section": "context"},
            )
        )
        assert "Remote ops agent" in result
        _close_server(mcp)

    async def test_query_auto_scan_agents(self, mock_vault: Path) -> None:
        _seed_agent(mock_vault)
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "Hermes-NaN", "section": "context"},
            )
        )
        assert "Remote ops agent" in result
        _close_server(mcp)

    async def test_health_enumerates_agents(self, mock_vault: Path) -> None:
        _seed_agent(mock_vault)
        mcp = create_server(vault_path=mock_vault)
        result = _text(await mcp.call_tool("vault_health", {}))
        assert "agents/Hermes-NaN" in result
        _close_server(mcp)

    async def test_write_to_agents_scope(self, git_multi_scope_vault: Path) -> None:
        import subprocess

        agent = _seed_agent(git_multi_scope_vault)
        subprocess.run(
            ["git", "add", "."],
            cwd=git_multi_scope_vault,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add agent"],
            cwd=git_multi_scope_vault,
            capture_output=True,
            check=True,
        )
        mcp = create_server(vault_path=git_multi_scope_vault)
        result = _text(
            await mcp.call_tool(
                "vault_write",
                {
                    "project": "agents:Hermes-NaN",
                    "section": "tasks",
                    "operation": "append",
                    "content": "\n## Inbox\nFirst task for the agent.\n",
                },
            )
        )
        assert "Updated" in result
        assert "First task for the agent" in (agent / "11-tasks.md").read_text()
        _close_server(mcp)
