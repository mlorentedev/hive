"""HIVE-119 — accept high-frequency wrong param names as aliases (#151).

Spec: ``specs/HIVE-119-tool-param-aliases/proposal.md``.

These assert that the four "real" aliases normalize to their canonical
parameter (identical behaviour, no ``unexpected_keyword_argument``), that the
canonical value wins on conflict, that no alias introduces an ``anyOf`` in the
JSON Schema, and that affected docstrings call out the common wrong name.

All alias-equivalence tests fail today: the alias params do not exist, so
``call_tool`` rejects them at the FastMCP validation boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.tools import ToolResult


def _text(result: ToolResult) -> str:
    """Extract text from a ToolResult."""
    return result.content[0].text  # type: ignore[union-attr]


# ── real aliases normalize to the canonical param ────────────────────────


class TestRealAliases:
    async def test_vault_list_subpath_aliases_path(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        canonical = _text(
            await mcp.call_tool(
                "vault_list",
                {"project": "testproject", "path": "30-architecture"},
            )
        )
        alias = _text(
            await mcp.call_tool(
                "vault_list",
                {"project": "testproject", "subpath": "30-architecture"},
            )
        )
        assert "adr-001-test.md" in alias
        assert alias == canonical

    async def test_vault_query_identifier_aliases_path(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        target = "30-architecture/adr-001-test.md"
        canonical = _text(
            await mcp.call_tool("vault_query", {"project": "testproject", "path": target})
        )
        alias = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "testproject", "identifier": target},
            )
        )
        assert "Test Decision" in alias
        assert alias == canonical

    async def test_vault_search_regex_aliases_use_regex(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        # A pattern that only matches as a regex, never as a literal substring.
        query = r"Task\s+one"
        canonical = _text(await mcp.call_tool("vault_search", {"query": query, "use_regex": True}))
        alias = _text(await mcp.call_tool("vault_search", {"query": query, "regex": True}))
        assert alias == canonical

    async def test_vault_patch_old_new_string_alias(self, git_vault: Path) -> None:
        mcp = create_server(vault_path=git_vault)
        resp = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "old_string": "- [ ] Task one",
                    "new_string": "- [x] Task one DONE",
                },
            )
        )
        assert "error" not in resp.lower()
        readback = _text(
            await mcp.call_tool(
                "vault_query",
                {"project": "testproject", "path": "11-tasks.md"},
            )
        )
        assert "- [x] Task one DONE" in readback


# ── conflict resolution: canonical value wins ────────────────────────────


class TestConflictCanonicalWins:
    async def test_vault_list_path_beats_subpath(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        result = _text(
            await mcp.call_tool(
                "vault_list",
                {
                    "project": "testproject",
                    "path": "30-architecture",
                    "subpath": "50-troubleshooting",
                },
            )
        )
        # canonical `path` (30-architecture) won; the alias dir is not listed.
        assert "adr-001-test.md" in result
        assert "timeout-fix.md" not in result


# ── schema stays anyOf-free (the load-bearing MCP rule) ──────────────────


class TestSchemaClean:
    @staticmethod
    def _has_anyof(node: object) -> bool:
        if isinstance(node, dict):
            if "anyOf" in node:
                return True
            return any(TestSchemaClean._has_anyof(v) for v in node.values())
        if isinstance(node, list):
            return any(TestSchemaClean._has_anyof(v) for v in node)
        return False

    async def test_no_tool_schema_contains_anyof(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        tools = await mcp.list_tools()
        offenders = [t.name for t in tools if self._has_anyof(t.parameters)]
        assert offenders == [], f"anyOf leaked into tool schema(s): {offenders}"


# ── docstrings name the canonical param and call out the wrong name ──────


class TestDocstringsCallOutWrongNames:
    # tool -> wrong names that MUST be mentioned in its description
    WRONG_NAMES = {
        "vault_list": ["subpath"],
        "vault_query": ["identifier"],
        "vault_search": ["regex"],
        "vault_patch": ["old_string", "new_string"],
        "vault_commit": ["project"],
        "session_briefing": ["days"],
        "capture_lesson": ["commit"],
    }

    async def test_descriptions_mention_common_wrong_names(self, mock_vault: Path) -> None:
        # FastMCP splits the docstring into a summary (`description`) plus
        # per-parameter schema descriptions; ``fn.__doc__`` is the full SSOT
        # both are derived from, so we assert against it.
        mcp = create_server(vault_path=mock_vault)
        by_name = {t.name: t for t in await mcp.list_tools()}
        missing: list[str] = []
        for tool, wrong in self.WRONG_NAMES.items():
            doc = by_name[tool].fn.__doc__ or ""
            for token in wrong:
                if token not in doc:
                    missing.append(f"{tool}:{token}")
        assert missing == [], f"docstrings missing wrong-name call-outs: {missing}"
