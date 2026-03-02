"""Tests for Worker MCP Server tools."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from hive.budget import BudgetTracker
from hive.clients import ClientResponse, ModelInfo, OllamaClient, OpenRouterClient
from hive.worker_server import create_server

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.tools import ToolResult


def _text(result: ToolResult) -> str:
    """Extract text from a ToolResult."""
    return result.content[0].text  # type: ignore[union-attr]


@pytest.fixture
def budget() -> BudgetTracker:
    return BudgetTracker(db_path=":memory:")


@pytest.fixture
def ollama() -> OllamaClient:
    return OllamaClient(endpoint="http://localhost:11434", model="qwen2.5-coder:3b")


@pytest.fixture
def openrouter() -> OpenRouterClient:
    return OpenRouterClient(api_key="sk-test", default_model="qwen/qwen3-coder:free")


@pytest.fixture
def worker(budget: BudgetTracker, ollama: OllamaClient, openrouter: OpenRouterClient) -> FastMCP:
    return create_server(budget_tracker=budget, ollama_client=ollama, openrouter_client=openrouter)


# ── delegate_task: auto routing ─────────────────────────────────────


class TestDelegateTaskAutoRouting:
    """Auto routing: Ollama first, then OpenRouter free, then paid."""

    @pytest.mark.asyncio
    async def test_ollama_first_when_available(self, worker: FastMCP, ollama: OllamaClient) -> None:
        ollama.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=ClientResponse(
                text="hello world",
                model="qwen2.5-coder:3b",
                tokens=10,
                cost_usd=0.0,
                latency_ms=200,
            )
        )
        result = _text(await worker.call_tool("delegate_task", {"prompt": "say hello"}))
        assert "hello world" in result
        assert "qwen2.5-coder:3b" in result

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
        assert "Claude should handle this task directly" in result


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
        assert "Claude should handle this task directly" in result

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
        assert "Claude should handle this task directly" in result


# ── delegate_task: explicit model ───────────────────────────────────


class TestDelegateTaskExplicitModel:
    """Explicit model selection bypasses auto-routing."""

    @pytest.mark.asyncio
    async def test_explicit_ollama(self, worker: FastMCP, ollama: OllamaClient) -> None:
        ollama.generate = AsyncMock(  # type: ignore[method-assign]
            return_value=ClientResponse(
                text="explicit ollama",
                model="qwen2.5-coder:3b",
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
                text="ok", model="qwen2.5-coder:3b", tokens=10, cost_usd=0.0, latency_ms=100
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
        assert "qwen2.5-coder:3b" in result
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
        assert "5.0" in result or "3.77" in result

    @pytest.mark.asyncio
    async def test_status_shows_ollama_connectivity(
        self, worker: FastMCP, ollama: OllamaClient
    ) -> None:
        ollama.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]
        result = _text(await worker.call_tool("worker_status", {}))
        assert "offline" in result.lower() or "unavailable" in result.lower()
