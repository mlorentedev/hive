"""Smoke tests — real HTTP calls to Ollama and OpenRouter + vault tool checks.

Run with:  pytest -m smoke -v
Requires:  Ollama running + OPENROUTER_API_KEY set (for worker tests).
Vault smoke tests always run (no external deps needed).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import httpx
import pytest

from hive.budget import BudgetTracker
from hive.clients import OllamaClient, OpenRouterClient
from hive.server import create_server

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.tools import ToolResult

pytestmark = pytest.mark.smoke

OLLAMA_ENDPOINT = os.environ.get("HIVE_OLLAMA_ENDPOINT", "http://ollama.kubelab.live:11434")
OLLAMA_MODEL = os.environ.get("HIVE_OLLAMA_MODEL", "qwen2.5-coder:7b")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get(
    "HIVE_OPENROUTER_API_KEY"
)
OPENROUTER_MODEL = os.environ.get("HIVE_OPENROUTER_MODEL", "qwen/qwen3-coder:free")

# Trivial prompt to keep latency and token usage minimal.
PING_PROMPT = "Reply with exactly one word: pong"


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


def _ollama_reachable() -> bool:
    try:
        resp = httpx.get(f"{OLLAMA_ENDPOINT}/", timeout=5)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False


skip_no_ollama = pytest.mark.skipif(not _ollama_reachable(), reason="Ollama not reachable")
skip_no_openrouter = pytest.mark.skipif(
    not OPENROUTER_API_KEY, reason="OPENROUTER_API_KEY not set"
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def smoke_budget(tmp_path: Path) -> BudgetTracker:
    return BudgetTracker(db_path=str(tmp_path / "smoke.db"))


@pytest.fixture
def ollama_client() -> OllamaClient:
    return OllamaClient(endpoint=OLLAMA_ENDPOINT, model=OLLAMA_MODEL)


@pytest.fixture
def openrouter_client() -> OpenRouterClient | None:
    if not OPENROUTER_API_KEY:
        return None
    return OpenRouterClient(api_key=OPENROUTER_API_KEY, default_model=OPENROUTER_MODEL)


@pytest.fixture
def server(
    smoke_budget: BudgetTracker,
    ollama_client: OllamaClient,
    openrouter_client: OpenRouterClient | None,
    tmp_path: Path,
) -> FastMCP:
    # Create minimal vault structure for vault smoke tests
    project = tmp_path / "10_projects" / "smoketest"
    project.mkdir(parents=True)
    (project / "00-context.md").write_text(
        "---\nid: smoketest\ntype: project\nstatus: active\n---\n\n# Smoke Test Project\n"
    )
    return create_server(
        vault_path=tmp_path,
        budget_tracker=smoke_budget,
        ollama_client=ollama_client,
        openrouter_client=openrouter_client,
    )


# ── Vault smoke tests (always run, no external deps) ──────────────


class TestVaultSmoke:
    """Vault tools work in the unified server."""

    async def test_list_projects(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("vault_list_projects", {}))
        assert "smoketest" in result

    async def test_vault_query(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_query", {"project": "smoketest", "section": "context"}
            )
        )
        assert "Smoke Test Project" in result

    async def test_session_briefing(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("session_briefing", {"project": "smoketest"}))
        assert "Session Briefing" in result


# ── Ollama direct ───────────────────────────────────────────────────


@skip_no_ollama
class TestOllamaDirect:
    """Real calls to Ollama."""

    async def test_delegate_ollama_returns_response(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool("delegate_task", {"prompt": PING_PROMPT, "model": "ollama"})
        )
        assert "Worker Response" in result
        assert "qwen2.5-coder" in result

    async def test_delegate_ollama_has_tokens_and_latency(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool("delegate_task", {"prompt": PING_PROMPT, "model": "ollama"})
        )
        # Header format: ## Worker Response (model: X, N tokens, $0.00, Xs)
        assert "tokens" in result
        assert "$0.00" in result

    async def test_ollama_records_budget(
        self, server: FastMCP, smoke_budget: BudgetTracker
    ) -> None:
        await server.call_tool("delegate_task", {"prompt": PING_PROMPT, "model": "ollama"})
        stats = smoke_budget.month_stats(5.0)
        assert stats["request_count"] == 1
        assert stats["spent"] == 0.0


# ── OpenRouter free direct ──────────────────────────────────────────


@skip_no_openrouter
class TestOpenRouterFreeDirect:
    """Real calls to OpenRouter free tier."""

    async def test_delegate_openrouter_free_returns_response(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "delegate_task", {"prompt": PING_PROMPT, "model": "openrouter-free"}
            )
        )
        assert "Worker Response" in result

    async def test_openrouter_free_records_budget(
        self, server: FastMCP, smoke_budget: BudgetTracker
    ) -> None:
        await server.call_tool(
            "delegate_task", {"prompt": PING_PROMPT, "model": "openrouter-free"}
        )
        stats = smoke_budget.month_stats(5.0)
        assert stats["request_count"] == 1


# ── Auto routing (full cascade) ─────────────────────────────────────


@skip_no_ollama
class TestAutoRouting:
    """Auto routing with real backends."""

    async def test_auto_picks_ollama_first(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool("delegate_task", {"prompt": PING_PROMPT})
        )
        # Auto should prefer Ollama when available.
        assert "Worker Response" in result
        assert "qwen2.5-coder" in result

    async def test_auto_records_budget(
        self, server: FastMCP, smoke_budget: BudgetTracker
    ) -> None:
        await server.call_tool("delegate_task", {"prompt": PING_PROMPT})
        stats = smoke_budget.month_stats(5.0)
        assert stats["request_count"] == 1


# ── list_models ─────────────────────────────────────────────────────


class TestListModelsSmoke:
    """Real model listing."""

    @skip_no_ollama
    async def test_list_models_shows_ollama(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("list_models", {}))
        assert "online" in result.lower()
        assert OLLAMA_MODEL in result

    @skip_no_openrouter
    async def test_list_models_shows_openrouter(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("list_models", {}))
        assert "OpenRouter" in result


# ── worker_status ───────────────────────────────────────────────────


class TestWorkerStatusSmoke:
    """Real status check."""

    @skip_no_ollama
    async def test_status_shows_ollama_online(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("worker_status", {}))
        assert "online" in result.lower()

    async def test_status_shows_budget(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("worker_status", {}))
        assert "Budget" in result
        assert "$" in result
