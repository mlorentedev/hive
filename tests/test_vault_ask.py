"""Tests for vault_ask — optional semantic Q&A (HIVE-211, disabled by default).

PR2 scope: the tool is registered and behaves gracefully when no semantic
backend is configured (or the optional ``[semantic]`` extra is absent). The
retrieval/synthesis pipeline lands in later changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from fastmcp.tools import ToolResult


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


def _has_anyof(node: object) -> bool:
    if isinstance(node, dict):
        if "anyOf" in node:
            return True
        return any(_has_anyof(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_anyof(v) for v in node)
    return False


class TestVaultAskDisabledByDefault:
    """AC2: with no embed backend, vault_ask is disabled but graceful."""

    async def test_disabled_default_is_graceful(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)  # no embed config
        answer = _text(await mcp.call_tool("vault_ask", {"question": "what did I decide about X?"}))
        # Clear, actionable, names the enable knobs — never an error/exception.
        assert "disabled" in answer.lower()
        assert "HIVE_EMBED_BASE_URL" in answer
        assert "[semantic]" in answer

    async def test_tool_registered_and_others_intact(self, mock_vault: Path) -> None:
        """Registering vault_ask must not break import or other tools (AC2)."""
        mcp = create_server(vault_path=mock_vault)
        names = {t.name for t in await mcp.list_tools()}
        assert "vault_ask" in names
        # A representative sample of existing tools must still be present:
        assert {"vault_search", "vault_query", "vault_write", "vault_health"} <= names

    async def test_disabled_when_backend_set_but_extra_missing(self, mock_vault: Path) -> None:
        """Backend configured but the [semantic] extra (numpy) absent -> disabled
        with an install hint, still graceful (AC2). numpy is not in the base/dev
        env, so this exercises the real extra-absent branch (no mocking)."""
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
        )
        answer = _text(await mcp.call_tool("vault_ask", {"question": "anything"}))
        assert "disabled" in answer.lower()
        assert "[semantic]" in answer


class TestVaultAskBackendReady:
    """Enabled path: backend configured + [semantic] extra present. PR2 returns
    a pending-index placeholder until the retrieval pipeline ships."""

    async def test_backend_ready_returns_pending_index(
        self, mock_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hive._vault_ask as va

        # numpy is genuinely absent in the base env, so simulate the extra
        # being installed to exercise the enabled branch.
        monkeypatch.setattr(va, "_semantic_extra_available", lambda: True)
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
        )
        answer = _text(await mcp.call_tool("vault_ask", {"question": "what did I decide?"}))
        assert "configured" in answer.lower()
        assert "vault_search" in answer


class TestVaultAskSchema:
    """AC5: vault_ask schema must contain no anyOf (the load-bearing | None ban)."""

    async def test_vault_ask_schema_has_no_anyof(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        tool = next(t for t in await mcp.list_tools() if t.name == "vault_ask")
        assert not _has_anyof(tool.parameters), f"anyOf in vault_ask schema: {tool.parameters}"
