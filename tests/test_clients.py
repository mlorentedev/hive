"""Tests for Ollama and OpenRouter HTTP clients."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hive.clients import ClientResponse, ModelInfo, OllamaClient, OpenRouterClient

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


# ── OllamaClient ────────────────────────────────────────────────────


class TestOllamaGenerate:
    """Ollama /api/chat generation."""

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        client = OllamaClient(endpoint="http://localhost:11434", model="qwen2.5-coder:7b")
        mock_resp = _mock_response(
            json_data={
                "message": {"content": "def hello(): pass"},
                "eval_count": 42,
                "total_duration": 1_500_000_000,  # 1.5s in nanoseconds
            }
        )
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.generate("Write a hello function")

        assert isinstance(result, ClientResponse)
        assert result.text == "def hello(): pass"
        assert result.model == "qwen2.5-coder:7b"
        assert result.tokens == 42
        assert result.cost_usd == 0.0
        assert result.latency_ms == 1500

    @pytest.mark.asyncio
    async def test_generate_timeout(self) -> None:
        client = OllamaClient(endpoint="http://localhost:11434", model="test")
        with (
            patch.object(
                client._http,
                "post",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectTimeout("timeout"),
            ),
            pytest.raises(ConnectionError, match="Ollama"),
        ):
            await client.generate("test")

    @pytest.mark.asyncio
    async def test_generate_connection_error(self) -> None:
        client = OllamaClient(endpoint="http://localhost:11434", model="test")
        with (
            patch.object(
                client._http,
                "post",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("refused"),
            ),
            pytest.raises(ConnectionError, match="Ollama"),
        ):
            await client.generate("test")


class TestOllamaAvailability:
    """Ollama health check."""

    @pytest.mark.asyncio
    async def test_is_available_true(self) -> None:
        client = OllamaClient(endpoint="http://localhost:11434", model="test")
        mock_resp = _mock_response(200)
        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            assert await client.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_false_on_error(self) -> None:
        client = OllamaClient(endpoint="http://localhost:11434", model="test")
        with patch.object(
            client._http, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")
        ):
            assert await client.is_available() is False


# ── OpenRouterClient ────────────────────────────────────────────────


class TestOpenRouterGenerate:
    """OpenRouter /api/v1/chat/completions."""

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="qwen/qwen3-coder:free")
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": "result text"}}],
                "usage": {"total_tokens": 150},
                "model": "qwen/qwen3-coder:free",
            }
        )
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.generate("Explain this code")

        assert isinstance(result, ClientResponse)
        assert result.text == "result text"
        assert result.tokens == 150
        assert result.model == "qwen/qwen3-coder:free"

    @pytest.mark.asyncio
    async def test_generate_with_explicit_model(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="default-model")
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 10},
                "model": "deepseek/deepseek-v3.2",
            }
        )
        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await client.generate("test", model="deepseek/deepseek-v3.2")

        # Verify the explicit model was sent in the request body
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["json"]["model"] == "deepseek/deepseek-v3.2"

    @pytest.mark.asyncio
    async def test_generate_rate_limit(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="m")
        mock_resp = _mock_response(
            status_code=429, json_data={"error": {"message": "rate limited"}}
        )
        with (
            patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp),
            pytest.raises(RuntimeError, match="rate limit"),
        ):
            await client.generate("test")

    @pytest.mark.asyncio
    async def test_generate_api_error(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="m")
        mock_resp = _mock_response(
            status_code=500, json_data={"error": {"message": "internal error"}}
        )
        with (
            patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp),
            pytest.raises(RuntimeError, match="OpenRouter API error"),
        ):
            await client.generate("test")

    @pytest.mark.asyncio
    async def test_generate_connection_error(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="m")
        with (
            patch.object(
                client._http, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")
            ),
            pytest.raises(ConnectionError, match="OpenRouter"),
        ):
            await client.generate("test")

    @pytest.mark.asyncio
    async def test_generate_extracts_cost(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="m")
        mock_resp = _mock_response(
            json_data={
                "choices": [{"message": {"content": "x"}}],
                "usage": {"total_tokens": 50, "cost": 0.0012},
                "model": "m",
            }
        )
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.generate("test")

        assert result.cost_usd == pytest.approx(0.0012)


class TestOpenRouterListModels:
    """OpenRouter /api/v1/models."""

    @pytest.mark.asyncio
    async def test_list_models(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="m")
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {
                        "id": "qwen/qwen3-coder:free",
                        "name": "Qwen3 Coder (free)",
                        "context_length": 65536,
                        "pricing": {"prompt": "0.0", "completion": "0.0"},
                    },
                    {
                        "id": "deepseek/deepseek-v3",
                        "name": "DeepSeek V3",
                        "context_length": 128000,
                        "pricing": {"prompt": "0.00014", "completion": "0.00028"},
                    },
                ],
            }
        )
        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            models = await client.list_models()

        assert len(models) == 2
        assert isinstance(models[0], ModelInfo)
        assert models[0].id == "qwen/qwen3-coder:free"
        assert models[0].is_free is True
        assert models[1].is_free is False

    @pytest.mark.asyncio
    async def test_list_models_connection_error(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="m")
        with (
            patch.object(
                client._http, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")
            ),
            pytest.raises(ConnectionError, match="OpenRouter"),
        ):
            await client.list_models()

    @pytest.mark.asyncio
    async def test_list_models_http_error(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="m")
        mock_resp = _mock_response(status_code=500, json_data={"error": "internal"})
        with (
            patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp),
            pytest.raises(RuntimeError, match="models error"),
        ):
            await client.list_models()

    @pytest.mark.asyncio
    async def test_list_models_malformed_pricing(self) -> None:
        """Malformed pricing values default to 0.0 instead of crashing."""
        client = OpenRouterClient(api_key="sk-test", default_model="m")
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {
                        "id": "bad-model",
                        "name": "Bad Pricing",
                        "context_length": 4096,
                        "pricing": {"prompt": "not-a-number", "completion": None},
                    },
                ],
            }
        )
        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            models = await client.list_models()

        assert len(models) == 1
        assert models[0].cost_per_million_input == 0.0
        assert models[0].cost_per_million_output == 0.0
        assert models[0].is_free is True

    @pytest.mark.asyncio
    async def test_list_models_read_timeout(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="m")
        with (
            patch.object(
                client._http, "get", new_callable=AsyncMock,
                side_effect=httpx.ReadTimeout("read timed out"),
            ),
            pytest.raises(ConnectionError, match="timed out"),
        ):
            await client.list_models()


class TestReadTimeoutResilience:
    """Verify ReadTimeout is caught and converted to ConnectionError."""

    @pytest.mark.asyncio
    async def test_ollama_read_timeout(self) -> None:
        client = OllamaClient(endpoint="http://localhost:11434", model="test")
        with (
            patch.object(
                client._http, "post", new_callable=AsyncMock,
                side_effect=httpx.ReadTimeout("inference took too long"),
            ),
            pytest.raises(ConnectionError, match="timed out"),
        ):
            await client.generate("test")

    @pytest.mark.asyncio
    async def test_openrouter_read_timeout(self) -> None:
        client = OpenRouterClient(api_key="sk-test", default_model="m")
        with (
            patch.object(
                client._http, "post", new_callable=AsyncMock,
                side_effect=httpx.ReadTimeout("inference took too long"),
            ),
            pytest.raises(ConnectionError, match="timed out"),
        ):
            await client.generate("test")

    @pytest.mark.asyncio
    async def test_ollama_is_available_read_timeout(self) -> None:
        client = OllamaClient(endpoint="http://localhost:11434", model="test")
        with patch.object(
            client._http, "get", new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("slow"),
        ):
            assert await client.is_available() is False
