"""Integration tests — multi-tool workflows and end-to-end scenarios."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hive.vault_server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.tools import ToolResult


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


class TestWorkflowListThenQuery:
    """List projects, then query a specific section."""

    async def test_discover_and_read(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)

        projects = _text(await mcp.call_tool("vault_list_projects", {}))
        assert "testproject" in projects

        context = _text(
            await mcp.call_tool("vault_query", {"project": "testproject"})
        )
        assert "# Test Project" in context


class TestWorkflowCreateThenQuery:
    """Create a file, then query it back."""

    async def test_create_and_read_back(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)

        create_result = _text(
            await mcp.call_tool(
                "vault_create",
                {
                    "project": "testproject",
                    "path": "40-runbooks/deploy-guide.md",
                    "content": "# Deploy Guide\n\nStep 1: push to main.\n",
                    "doc_type": "runbook",
                },
            )
        )
        assert "created" in create_result.lower()

        query_result = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "testproject", "path": "40-runbooks/deploy-guide.md"},
            )
        )
        assert "Deploy Guide" in query_result
        assert "type: runbook" in query_result


class TestWorkflowUpdateThenSearch:
    """Append content, then search for it."""

    async def test_append_and_find(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)

        await mcp.call_tool(
            "vault_update",
            {
                "project": "testproject",
                "section": "lessons",
                "operation": "append",
                "content": "\n## Unique Marker 7x9z\nThis is searchable.\n",
            },
        )

        search_result = _text(
            await mcp.call_tool("vault_search", {"query": "Unique Marker 7x9z"})
        )
        assert "90-lessons.md" in search_result
        assert "Unique Marker 7x9z" in search_result


class TestWorkflowHealthAfterChanges:
    """Health metrics should reflect newly created files."""

    async def test_health_counts_new_files(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)

        health_before = _text(await mcp.call_tool("vault_health", {}))

        await mcp.call_tool(
            "vault_create",
            {
                "project": "testproject",
                "path": "new-doc.md",
                "content": "# New Document\n",
                "doc_type": "lesson",
            },
        )

        health_after = _text(await mcp.call_tool("vault_health", {}))
        assert health_before != health_after


class TestWorkflowCrossProjectAccess:
    """Query project content and meta patterns in the same session."""

    async def test_project_and_meta_in_one_session(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)

        project_ctx = _text(
            await mcp.call_tool("vault_query", {"project": "testproject"})
        )
        assert "Test Project" in project_ctx

        meta_pattern = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "_meta", "path": "patterns/pattern-tdd.md"},
            )
        )
        assert "Test-Driven Development" in meta_pattern
