"""Tests for vault_ask — optional semantic Q&A (HIVE-211).

PR2 scope: graceful disabled-by-default behavior.
PR3 scope: retrieval pipeline (chunker + embed + numpy vector store).
PR4 scope: LLM synthesis — top-k chunks → cited answer via OpenAICompatibleClient.generate().
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


# ── PR4: synthesis (AC1, AC6) ────────────────────────────────────────────────


class TestVaultAskSynthesis:
    """AC1: synthesized answer; AC6: cites real vault files."""

    def _make_synth_mock(self, answer: str) -> MagicMock:
        """Mock OpenAICompatibleClient whose generate() returns a fixed answer."""
        from hive.clients import ClientResponse

        async def _generate(prompt: str, model: str = "") -> ClientResponse:
            return ClientResponse(text=answer, model=model or "mock", tokens=10, latency_ms=1)

        client = MagicMock()
        client.generate = _generate
        return client

    async def test_synthesis_returns_synthesized_answer(
        self,
        mock_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """With synth_model set, vault_ask returns the synthesized LLM answer (AC1)."""
        monkeypatch.setattr(
            "hive._vault_ask._build_embed_client",
            lambda base_url, api_key, model: _make_embed_mock(),
        )
        monkeypatch.setattr(
            "hive._vault_ask._index_dir_for",
            lambda: tmp_path / "idx",
        )
        monkeypatch.setattr(
            "hive._vault_ask._build_synth_client",
            lambda base_url, api_key, model: self._make_synth_mock(
                "The project uses TDD. [source: 00_meta/patterns/pattern-tdd.md]"
            ),
        )
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
            synth_model="deepseek-v4-flash",
        )
        answer = _text(await mcp.call_tool("vault_ask", {"question": "what testing approach?"}))
        assert "disabled" not in answer.lower()
        assert "TDD" in answer or ".md" in answer

    async def test_synthesis_cites_vault_source(
        self,
        mock_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Synthesized answer must contain a .md citation (AC6)."""
        monkeypatch.setattr(
            "hive._vault_ask._build_embed_client",
            lambda base_url, api_key, model: _make_embed_mock(),
        )
        monkeypatch.setattr(
            "hive._vault_ask._index_dir_for",
            lambda: tmp_path / "idx",
        )
        monkeypatch.setattr(
            "hive._vault_ask._build_synth_client",
            lambda base_url, api_key, model: self._make_synth_mock(
                "Test-driven development is the pattern. [source: 00_meta/patterns/pattern-tdd.md]"
            ),
        )
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
            synth_model="deepseek-v4-flash",
        )
        answer = _text(await mcp.call_tool("vault_ask", {"question": "TDD?"}))
        assert ".md" in answer

    async def test_synthesis_prompt_includes_chunk_context(
        self,
        mock_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """The synthesis prompt must include retrieved vault content (anti-hallucination)."""
        from hive.clients import ClientResponse

        captured: list[str] = []

        async def _spy_generate(prompt: str, model: str = "") -> ClientResponse:
            captured.append(prompt)
            return ClientResponse(
                text="Answer. [source: some.md]", model="mock", tokens=5, latency_ms=1
            )

        synth_mock = MagicMock()
        synth_mock.generate = _spy_generate

        monkeypatch.setattr(
            "hive._vault_ask._build_embed_client",
            lambda base_url, api_key, model: _make_embed_mock(),
        )
        monkeypatch.setattr(
            "hive._vault_ask._index_dir_for",
            lambda: tmp_path / "idx",
        )
        monkeypatch.setattr(
            "hive._vault_ask._build_synth_client",
            lambda base_url, api_key, model: synth_mock,
        )
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
            synth_model="deepseek-v4-flash",
        )
        await mcp.call_tool("vault_ask", {"question": "what is TDD?"})
        assert captured, "generate() was never called"
        prompt = captured[0]
        # Prompt must contain the vault section content AND source paths
        assert ".md" in prompt
        assert "what is TDD?" in prompt

    async def test_no_synth_model_returns_retrieval_only(
        self,
        mock_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When synth_model is empty, vault_ask returns formatted retrieval (no LLM call)."""
        monkeypatch.setattr(
            "hive._vault_ask._build_embed_client",
            lambda base_url, api_key, model: _make_embed_mock(),
        )
        monkeypatch.setattr(
            "hive._vault_ask._index_dir_for",
            lambda: tmp_path / "idx",
        )
        # No synth_model — create_server without it
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
            # synth_model intentionally omitted
        )
        answer = _text(await mcp.call_tool("vault_ask", {"question": "TDD?"}))
        # Should still return retrieval output (not disabled, not synthesized)
        assert "disabled" not in answer.lower()
        assert ".md" in answer

    async def test_synthesis_graceful_on_llm_error(
        self,
        mock_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """If generate() raises, vault_ask falls back gracefully (no crash)."""
        from hive.clients import ClientResponse  # noqa: F401

        async def _failing_generate(prompt: str, model: str = "") -> ClientResponse:
            raise RuntimeError("LLM unreachable")

        synth_mock = MagicMock()
        synth_mock.generate = _failing_generate

        monkeypatch.setattr(
            "hive._vault_ask._build_embed_client",
            lambda base_url, api_key, model: _make_embed_mock(),
        )
        monkeypatch.setattr(
            "hive._vault_ask._index_dir_for",
            lambda: tmp_path / "idx",
        )
        monkeypatch.setattr(
            "hive._vault_ask._build_synth_client",
            lambda base_url, api_key, model: synth_mock,
        )
        mcp = create_server(
            vault_path=mock_vault,
            embed_base_url="https://api.nan.builders/v1",
            embed_model="qwen3-embedding",
            synth_model="deepseek-v4-flash",
        )
        answer = _text(await mcp.call_tool("vault_ask", {"question": "TDD?"}))
        # Must not crash — returns retrieval or error message
        assert isinstance(answer, str)
        assert "disabled" not in answer.lower()


# ── PR2: schema (AC5) ────────────────────────────────────────────────────────


class TestVaultAskSchema:
    """AC5: vault_ask schema must contain no anyOf (the load-bearing | None ban)."""

    async def test_vault_ask_schema_has_no_anyof(self, mock_vault: Path) -> None:
        mcp = create_server(vault_path=mock_vault)
        tool = next(t for t in await mcp.list_tools() if t.name == "vault_ask")
        assert not _has_anyof(tool.parameters), f"anyOf in vault_ask schema: {tool.parameters}"
