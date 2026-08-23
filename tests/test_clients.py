"""Tests for the OpenAI-compatible HTTP client.

HIVE-384 removed ``OllamaClient`` and ``OpenRouterClient``, so their test
classes went with them — they exercised transports that no longer exist, and a
test of a deleted class is not coverage.

What was preserved rather than deleted with them: the **ReadTimeout →
ConnectionError** conversion, which was covered twice (once per retired client)
and is now covered once against ``OpenAICompatibleClient``. That behaviour
belongs to the surviving transport, and it is load-bearing — a read timeout
surfacing as ``ConnectionError`` is what lets the worker classify a stalled
provider as *pool unavailable* rather than as a failed task.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hive.clients import (
    ClientResponse,
    EmbedResponse,
    OpenAICompatibleClient,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _mock_response(
    status_code: int = 200, json_data: dict[str, Any] | None = None
) -> httpx.Response:
    """Build a fake httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data or {},
        request=httpx.Request("POST", "http://test"),
    )
    return resp


class TestReadTimeoutResilience:
    """A read timeout must surface as ConnectionError, not as a raw httpx error.

    Kept from the retired clients' tests: the worker classifies
    ``ConnectionError`` as *pool unavailable* (retry elsewhere is legitimate)
    and other failures as *task failed*. If a stalled provider leaked an
    httpx exception instead, it would be classified as a task failure and the
    dispatcher would stop advancing its chain on exactly the case the chain
    exists for.
    """

    @pytest.mark.asyncio
    async def test_read_timeout_becomes_connection_error(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://api.nan.example/v1",
            api_key="test-key",
            default_model="deepseek-v4-flash",
        )
        with (
            patch.object(
                client._http,
                "post",
                new_callable=AsyncMock,
                side_effect=httpx.ReadTimeout("inference took too long"),
            ),
            pytest.raises(ConnectionError, match="timed out"),
        ):
            await client.generate("test")


class TestOpenAICompatibleChat:
    """Generalized chat completions against an arbitrary OpenAI-compatible base_url."""

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://api.nan.builders/v1",
            api_key="sk-nan",
            default_model="deepseek-v4-flash",
            provider_name="NaN",
        )
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": "hola"}}],
                "usage": {"total_tokens": 12},
                "model": "deepseek-v4-flash",
            }
        )
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.generate("di hola")

        assert isinstance(result, ClientResponse)
        assert result.text == "hola"
        assert result.tokens == 12
        assert result.model == "deepseek-v4-flash"

    @pytest.mark.asyncio
    async def test_generate_posts_to_full_chat_completions_url(self) -> None:
        """The path is built from base_url (no httpx base_url join footgun)."""
        client = OpenAICompatibleClient(
            base_url="https://api.nan.builders/v1/",  # trailing slash must be tolerated
            default_model="m",
        )
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": "x"}}],
                "usage": {"total_tokens": 1},
                "model": "m",
            }
        )
        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await client.generate("hi")

        url = mock_post.call_args[0][0]
        assert url == "https://api.nan.builders/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_provider_name_surfaces_in_errors(self) -> None:
        client = OpenAICompatibleClient(
            base_url="http://x/v1", default_model="m", provider_name="NaN"
        )
        with (
            patch.object(
                client._http, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")
            ),
            pytest.raises(ConnectionError, match="NaN"),
        ):
            await client.generate("test")

    @pytest.mark.asyncio
    async def test_no_auth_header_when_no_api_key(self) -> None:
        """A local Ollama endpoint needs no key — no Authorization header is sent."""
        client = OpenAICompatibleClient(base_url="http://localhost:11434/v1", default_model="m")
        assert "Authorization" not in client._http.headers

    @pytest.mark.asyncio
    async def test_auth_header_present_when_api_key(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://api.nan.builders/v1", api_key="sk-nan", default_model="m"
        )
        assert client._http.headers["Authorization"] == "Bearer sk-nan"


class TestOpenAICompatibleEmbed:
    """embed() — the new capability that powers vault_ask retrieval."""

    @pytest.mark.asyncio
    async def test_embed_success(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://api.nan.builders/v1",
            api_key="sk-nan",
            default_model="qwen3-embedding",
            provider_name="NaN",
        )
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                ],
                "model": "qwen3-embedding",
                "usage": {"total_tokens": 8},
            }
        )
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.embed(["alpha", "beta"])

        assert isinstance(result, EmbedResponse)
        assert result.vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        assert result.model == "qwen3-embedding"
        assert result.tokens == 8

    @pytest.mark.asyncio
    async def test_embed_posts_to_embeddings_url_with_input_list(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://api.nan.builders/v1", default_model="qwen3-embedding"
        )
        mock_resp = _mock_response(
            json_data={"data": [{"index": 0, "embedding": [1.0]}], "usage": {"total_tokens": 1}}
        )
        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await client.embed(["only-text"])

        url = mock_post.call_args[0][0]
        body = mock_post.call_args[1]["json"]
        assert url == "https://api.nan.builders/v1/embeddings"
        assert body["input"] == ["only-text"]
        assert body["model"] == "qwen3-embedding"

    @pytest.mark.asyncio
    async def test_embed_preserves_input_order_when_provider_returns_out_of_order(self) -> None:
        """Vector↔text alignment is load-bearing for RAG: a provider may return
        ``data`` out of order, so we MUST sort by ``index`` before extracting vectors."""
        client = OpenAICompatibleClient(base_url="http://x/v1", default_model="qwen3-embedding")
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {"index": 2, "embedding": [2.0]},
                    {"index": 0, "embedding": [0.0]},
                    {"index": 1, "embedding": [1.0]},
                ],
                "usage": {"total_tokens": 3},
            }
        )
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.embed(["a", "b", "c"])

        assert result.vectors == [[0.0], [1.0], [2.0]]

    @pytest.mark.asyncio
    async def test_embed_model_override(self) -> None:
        client = OpenAICompatibleClient(base_url="http://x/v1", default_model="default-embed")
        mock_resp = _mock_response(
            json_data={"data": [{"index": 0, "embedding": [1.0]}], "usage": {"total_tokens": 1}}
        )
        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await client.embed(["t"], model="nomic-embed-text")

        assert mock_post.call_args[1]["json"]["model"] == "nomic-embed-text"

    @pytest.mark.asyncio
    async def test_embed_read_timeout(self) -> None:
        client = OpenAICompatibleClient(
            base_url="http://x/v1", default_model="m", provider_name="NaN"
        )
        with (
            patch.object(
                client._http,
                "post",
                new_callable=AsyncMock,
                side_effect=httpx.ReadTimeout("inference too long"),
            ),
            pytest.raises(ConnectionError, match="timed out"),
        ):
            await client.embed(["t"])

    @pytest.mark.asyncio
    async def test_embed_connection_error(self) -> None:
        client = OpenAICompatibleClient(base_url="http://x/v1", default_model="m")
        with (
            patch.object(
                client._http, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")
            ),
            pytest.raises(ConnectionError),
        ):
            await client.embed(["t"])

    @pytest.mark.asyncio
    async def test_embed_http_error(self) -> None:
        client = OpenAICompatibleClient(
            base_url="http://x/v1", default_model="m", provider_name="NaN"
        )
        mock_resp = _mock_response(status_code=500, json_data={"error": {"message": "boom"}})
        with (
            patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp),
            pytest.raises(RuntimeError, match="NaN"),
        ):
            await client.embed(["t"])
