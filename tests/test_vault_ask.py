"""Tests for vault_ask — optional semantic Q&A (HIVE-211).

PR2 scope: graceful disabled-by-default behavior.
PR3 scope: retrieval pipeline (chunker + embed + numpy vector store).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from hive.clients import EmbedResponse
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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_embed_mock(dim: int = 4) -> MagicMock:
    """Mock OpenAICompatibleClient that returns unit vectors for any input."""

    async def _embed(texts: list[str], model: str = "") -> EmbedResponse:
        vecs = [[1.0] + [0.0] * (dim - 1)] * len(texts)
        return EmbedResponse(vectors=vecs, model=model or "mock", tokens=5, latency_ms=1)

    client = MagicMock()
    client.embed = _embed
    return client


# ── PR2: disabled-by-default (AC2, AC5) ──────────────────────────────────────


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

    async def test_disabled_when_backend_set_but_extra_missing(
        self, mock_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Backend configured but the [semantic] extra (numpy) absent -> disabled
        with an install hint, still graceful (AC2). The 'absent' branch is exercised
        by monkeypatching _semantic_extra_available to False (numpy is in dev extras
        so it is genuinely installed; mocking ensures the branch is always tested)."""
        import hive._vault_ask as va

        monkeypatch.setattr(va, "_semantic_extra_available", lambda: False)
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
        )
        answer = _text(await mcp.call_tool("vault_ask", {"question": "anything"}))
        assert "disabled" in answer.lower()
        assert "[semantic]" in answer


# ── PR3: retrieval pipeline (AC1 partial — raw chunks, no synthesis yet) ─────


class TestVaultAskRetrieval:
    """Backend + [semantic] extra present — vault_ask returns retrieved chunks."""

    async def test_retrieval_returns_chunks_from_vault(
        self,
        mock_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """vault_ask with a configured backend returns relevant vault sections."""
        monkeypatch.setattr(
            "hive._vault_ask._build_embed_client",
            lambda base_url, api_key, model: _make_embed_mock(),
        )
        monkeypatch.setattr(
            "hive._vault_ask._index_dir_for",
            lambda: tmp_path / "idx",
        )
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
        )
        answer = _text(await mcp.call_tool("vault_ask", {"question": "what is the test project?"}))
        # Returns vault content — not the disabled/pending message
        assert "disabled" not in answer.lower()
        assert "vault_search" not in answer or "vault_ask" in answer  # not just a redirect

    async def test_empty_question_rejected_gracefully(
        self, mock_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
        )
        answer = _text(await mcp.call_tool("vault_ask", {"question": ""}))
        assert "question" in answer.lower() or "empty" in answer.lower()

    async def test_retrieval_output_cites_sources(
        self,
        mock_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Output format must cite vault source paths."""
        monkeypatch.setattr(
            "hive._vault_ask._build_embed_client",
            lambda base_url, api_key, model: _make_embed_mock(),
        )
        monkeypatch.setattr(
            "hive._vault_ask._index_dir_for",
            lambda: tmp_path / "idx",
        )
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
        )
        answer = _text(await mcp.call_tool("vault_ask", {"question": "TDD pattern"}))
        # At minimum the answer should cite a .md file from the vault
        assert ".md" in answer


# ── PR2: schema (AC5) ────────────────────────────────────────────────────────


class TestVaultAskSchema:
    """AC5: vault_ask schema must contain no anyOf (the load-bearing | None ban)."""

    async def test_vault_ask_schema_has_no_anyof(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        tool = next(t for t in await mcp.list_tools() if t.name == "vault_ask")
        assert not _has_anyof(tool.parameters), f"anyOf in vault_ask schema: {tool.parameters}"
