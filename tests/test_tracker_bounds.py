"""UsageTracker / BudgetTracker calls are bounded, and a timed-out read never reads as zero (#288).

Both trackers are telemetry since the spend cap was retired (#384), so a write
that times out is skipped, as #287 does for relevance. A read that times out
is rendered as unavailable: printing zeros would be a false reading.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from hive.budget import BudgetTracker
from hive.clients import ClientResponse, OpenAICompatibleClient
from hive.server import create_server
from hive.usage import UsageTracker

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.tools import ToolResult

_STALL_S = 0.5


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


class _StuckUsage(UsageTracker):
    def log_call(self, tool: str, project: str = "", response_lines: int = 0) -> None:
        with self._lock:
            time.sleep(_STALL_S)

    def stats(self, since_days: int = 30) -> dict[str, Any]:
        with self._lock:
            time.sleep(_STALL_S)
        return {"total_calls": 0}


class _StuckBudget(BudgetTracker):
    def record_request(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            time.sleep(_STALL_S)

    def month_usage(self) -> dict[str, Any]:
        with self._lock:
            time.sleep(_STALL_S)
        return {"request_count": 0, "total_tokens": 0, "by_model": {}}


@pytest.fixture(autouse=True)
def _short_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hive._helpers._TRACKER_TIMEOUT_S", 0.05)


async def _timed(mcp: Any, tool: str, args: dict[str, Any]) -> tuple[str, float]:
    start = time.monotonic()
    text = _text(await mcp.call_tool(tool, args))
    return text, time.monotonic() - start


# ── writes: bound and skip ────────────────────────────────────


async def test_a_stuck_usage_write_does_not_hold_a_tool(mock_vault: Path) -> None:
    mcp = create_server(vault_path=mock_vault, usage_tracker=_StuckUsage())
    text, elapsed = await _timed(mcp, "vault_query", {"project": "testproject", "section": "tasks"})
    assert "Task one" in text
    assert elapsed < 0.4, f"vault_query waited on the usage tracker for {elapsed:.2f}s"


async def test_a_stuck_budget_write_does_not_hold_delegate(mock_vault: Path) -> None:
    worker = OpenAICompatibleClient(
        base_url="http://localhost:1/v1", api_key="k", default_model="m"
    )
    worker.generate = AsyncMock(  # type: ignore[method-assign]
        return_value=ClientResponse(text="hello", model="m", tokens=10, cost_usd=0.0, latency_ms=5)
    )
    mcp = create_server(vault_path=mock_vault, budget_tracker=_StuckBudget(), worker_client=worker)
    text, elapsed = await _timed(mcp, "delegate_task", {"prompt": "say hello"})
    assert "hello" in text
    assert elapsed < 0.4, f"delegate_task waited on the budget tracker for {elapsed:.2f}s"


# ── reads: bound and say unavailable, never zero ──────────────


async def test_worker_status_says_usage_unavailable(mock_vault: Path) -> None:
    mcp = create_server(vault_path=mock_vault, budget_tracker=_StuckBudget())
    text, elapsed = await _timed(mcp, "worker_status", {"include_models": False})
    assert elapsed < 0.4
    assert "unavailable" in text
    assert "Requests: 0" not in text


async def test_health_runtime_says_worker_usage_unavailable(mock_vault: Path) -> None:
    mcp = create_server(vault_path=mock_vault, budget_tracker=_StuckBudget())
    text, elapsed = await _timed(mcp, "vault_health", {"include_runtime": True})
    assert elapsed < 0.4
    assert "worker_usage: unavailable" in text
    assert "request_count: 0" not in text


async def test_health_usage_says_unavailable_not_no_calls(mock_vault: Path) -> None:
    mcp = create_server(vault_path=mock_vault, usage_tracker=_StuckUsage())
    text, elapsed = await _timed(mcp, "vault_health", {"include_usage": True})
    assert elapsed < 0.4 + _STALL_S  # track() of vault_health itself is one bounded write
    assert "Vault usage unavailable" in text
    assert "No vault tool calls recorded" not in text
