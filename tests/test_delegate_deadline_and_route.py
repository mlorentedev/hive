"""The deadline is the one that was asked for, and the route is reported (AC3, AC4).

Two properties that look like details and are not.

**AC3 — a per-dispatch deadline replaces the ambient one.** `ctx.tool_timeout`
defaults to 60s. A dispatcher that asks for 180 and silently gets 60 was never
given the deadline it asked for, and it learns this as a mysterious timeout on
long work. So the value passed in raises the ceiling rather than being bounded
by it — and the reverse also holds: a *shorter* deadline must actually cut the
call short rather than waiting out the ambient one.

The no-wait half matters because ADR-008's supervisor escalates: cancel,
``terminate()``, two-second grace, ``kill()``. If the verb waited for that whole
escalation its observable deadline would be ``--timeout + 2s``. It does not:
the grace applies to registered subprocesses, and an HTTP dispatch registers
none, so cancellation returns immediately. The test asserts the *observable*
property rather than the mechanism, so it keeps its meaning if the internals
change.

**AC4 — `degraded` is asserted in both directions.** A boolean only ever
observed in one state is not asserted; it would pass just as happily hardcoded.
So both routes are exercised: a reachable daemon must produce ``false`` and an
absent one ``true``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from hive.clients import ClientResponse
from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP

    from hive.budget import BudgetTracker
    from hive.clients import OpenAICompatibleClient


@pytest.fixture
def worker(
    mock_vault: Path,
    budget: BudgetTracker,
    worker_client: OpenAICompatibleClient,
) -> FastMCP:
    """A server wired to a controllable worker client."""
    return create_server(
        vault_path=mock_vault,
        budget_tracker=budget,
        worker_client=worker_client,
    )


def _record(result: Any) -> dict[str, Any]:
    text = result.content[0].text  # type: ignore[union-attr]
    return dict(json.loads(text))


class TestTheDeadlineIsTheOneAskedFor:
    @pytest.mark.asyncio
    async def test_a_longer_timeout_is_honoured_not_clamped(
        self, worker: FastMCP, worker_client: OpenAICompatibleClient
    ) -> None:
        """The regression this guards: a 60s ambient default capping a 180s ask."""

        async def slow_but_within_budget(*a: object, **kw: object) -> ClientResponse:
            await asyncio.sleep(0.3)
            return ClientResponse(text="done", model="m", tokens=1, cost_usd=0.0, latency_ms=300)

        worker_client.generate = AsyncMock(side_effect=slow_but_within_budget)  # type: ignore[method-assign]
        ctx = worker._hive_ctx  # type: ignore[attr-defined]
        ctx.tool_timeout = 0.1  # ambient budget is FIVE TIMES too small

        record = _record(
            await worker.call_tool(
                "delegate_task",
                {"prompt": "x", "structured": True, "timeout_s": 5.0},
            )
        )
        assert record["status"] == "ok", "the per-dispatch deadline must raise the ceiling"

    @pytest.mark.asyncio
    async def test_a_shorter_timeout_actually_cuts_the_call_short(
        self, worker: FastMCP, worker_client: OpenAICompatibleClient
    ) -> None:
        """Overriding must work downward too, or it is a floor and not a deadline."""

        async def hangs(*a: object, **kw: object) -> ClientResponse:
            await asyncio.sleep(999)
            raise AssertionError("unreachable")

        worker_client.generate = AsyncMock(side_effect=hangs)  # type: ignore[method-assign]
        ctx = worker._hive_ctx  # type: ignore[attr-defined]
        ctx.tool_timeout = 30.0  # ambient budget is far LONGER than the ask

        started = time.monotonic()
        record = _record(
            await worker.call_tool(
                "delegate_task",
                {"prompt": "x", "structured": True, "timeout_s": 0.2},
            )
        )
        elapsed = time.monotonic() - started

        assert record["status"] == "timeout"
        assert elapsed < 2.0, (
            "returned after the 2s grace window, so the observable deadline is "
            "timeout + grace rather than timeout"
        )

    @pytest.mark.asyncio
    async def test_the_timeout_detail_names_the_deadline_that_expired(
        self, worker: FastMCP, worker_client: OpenAICompatibleClient
    ) -> None:
        """Reporting the ambient 60s for a 0.2s dispatch sends a reader the wrong way."""

        async def hangs(*a: object, **kw: object) -> ClientResponse:
            await asyncio.sleep(999)
            raise AssertionError("unreachable")

        worker_client.generate = AsyncMock(side_effect=hangs)  # type: ignore[method-assign]
        ctx = worker._hive_ctx  # type: ignore[attr-defined]
        ctx.tool_timeout = 30.0

        record = _record(
            await worker.call_tool(
                "delegate_task",
                {"prompt": "x", "structured": True, "timeout_s": 0.2},
            )
        )
        # Positively, not just "the wrong number is absent": a mutation run
        # showed the negative form passing while the message was still wrong,
        # because 0.2 formatted as "0s" under `:.0f` and contained no "30"
        # either. A sub-second deadline reported as 0s reads as a broken timer.
        assert "0.2" in record["detail"], f"detail does not name the deadline: {record['detail']}"
        assert "30" not in record["detail"], "named the ambient budget, not the one asked for"

    @pytest.mark.asyncio
    async def test_zero_falls_back_to_the_ambient_budget(
        self, worker: FastMCP, worker_client: OpenAICompatibleClient
    ) -> None:
        """MCP callers that never heard of timeout_s keep today's behaviour."""

        async def hangs(*a: object, **kw: object) -> ClientResponse:
            await asyncio.sleep(999)
            raise AssertionError("unreachable")

        worker_client.generate = AsyncMock(side_effect=hangs)  # type: ignore[method-assign]
        ctx = worker._hive_ctx  # type: ignore[attr-defined]
        ctx.tool_timeout = 0.2

        record = _record(
            await worker.call_tool("delegate_task", {"prompt": "x", "structured": True})
        )
        assert record["status"] == "timeout"


_OK_BODY = json.dumps(
    {"status": "ok", "model": "m", "output": "hi", "tokens": 1, "duration_ms": 5, "detail": ""}
)


class _ToolResult:
    """Minimal stand-in for a FastMCP tool result."""

    def __init__(self, text: str = _OK_BODY) -> None:
        class _Content:
            def __init__(self, t: str) -> None:
                self.text = t

        self.content = [_Content(text)]


class _RemoteClient:
    """A daemon client that answers."""

    async def __aenter__(self) -> _RemoteClient:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def call_tool(self, _name: str, _args: dict[str, Any]) -> _ToolResult:
        return _ToolResult()


class _BrokenRemoteClient:
    """A daemon that accepts TCP and then fails the MCP session."""

    async def __aenter__(self) -> _BrokenRemoteClient:
        raise ConnectionResetError("daemon restarting")

    async def __aexit__(self, *a: object) -> None:
        return None


class TestDegradedIsReportedInBothDirections:
    """A flag observed in one state only would pass while hardcoded."""

    def test_a_reachable_daemon_reports_not_degraded(self) -> None:
        from hive import _delegate

        with (
            patch("hive._client._read_state", return_value=(4242, "a-token")),
            patch("hive._client._daemon_reachable", return_value=True),
            patch("hive._client._remote_client", return_value=_RemoteClient()),
        ):
            record = _delegate._dispatch_once(
                prompt="x", model="m", timeout_s=5.0, context="", max_tokens=10
            )
        assert record["degraded"] is False
        assert record["status"] == "ok"

    def test_no_daemon_state_reports_degraded(self) -> None:
        from hive import _delegate

        local = AsyncMock(return_value=_ToolResult())
        with (
            patch("hive._client._read_state", return_value=None),
            patch("hive.server.create_server") as make,
        ):
            make.return_value.call_tool = local
            record = _delegate._dispatch_once(
                prompt="x", model="m", timeout_s=5.0, context="", max_tokens=10
            )
        assert record["degraded"] is True
        assert local.await_count == 1, "the in-process path must be the one that ran"

    def test_a_daemon_that_accepts_tcp_but_fails_the_call_degrades(self) -> None:
        """The TCP probe proves a listener, not a working MCP session.

        A daemon mid-restart accepts the connection and then fails the
        handshake. Failing the dispatch there would make a restart look like a
        worker error; degrading and saying so is the documented contract.
        """
        from hive import _delegate

        local = AsyncMock(return_value=_ToolResult())
        with (
            patch("hive._client._read_state", return_value=(4242, "a-token")),
            patch("hive._client._daemon_reachable", return_value=True),
            patch("hive._client._remote_client", return_value=_BrokenRemoteClient()),
            patch("hive.server.create_server") as make,
        ):
            make.return_value.call_tool = local
            record = _delegate._dispatch_once(
                prompt="x", model="m", timeout_s=5.0, context="", max_tokens=10
            )
        assert record["degraded"] is True
        assert local.await_count == 1

    def test_stale_state_with_nothing_listening_degrades(self) -> None:
        """A crashed daemon leaves its port file behind; the probe is what decides."""
        from hive import _delegate

        local = AsyncMock(return_value=_ToolResult())
        with (
            patch("hive._client._read_state", return_value=(4242, "a-token")),
            patch("hive._client._daemon_reachable", return_value=False),
            patch("hive.server.create_server") as make,
        ):
            make.return_value.call_tool = local
            record = _delegate._dispatch_once(
                prompt="x", model="m", timeout_s=5.0, context="", max_tokens=10
            )
        assert record["degraded"] is True
