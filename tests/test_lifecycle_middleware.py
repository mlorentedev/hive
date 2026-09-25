"""Session counting in ``LifecycleMiddleware`` across both handshakes (#434).

A client on the 2026-07-28 protocol revision (mcp 2.x) opens with
``server/discover`` and never sends ``initialize``. A middleware that counted
only ``initialize`` reported ``sessions_started: 0`` on ``/status`` for every
such client, while clients on older revisions were still counted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import MiddlewareContext

from hive._diagnostics import LifecycleMiddleware
from hive._metrics import METRICS

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _clean_metrics() -> Iterator[None]:
    METRICS.reset()
    yield
    METRICS.reset()


@pytest.mark.parametrize("method", ["initialize", "server/discover"])
async def test_each_handshake_counts_one_session(method: str) -> None:
    context: MiddlewareContext[Any] = MiddlewareContext(message=None, method=method)

    async def call_next(_: MiddlewareContext[Any]) -> str:
        return "ok"

    await LifecycleMiddleware().on_message(context, call_next)

    assert METRICS.snapshot()["sessions_started"] == 1


async def test_a_current_client_session_is_counted() -> None:
    """End to end: an in-process client of the installed mcp major."""
    server = FastMCP("probe", middleware=[LifecycleMiddleware()])

    @server.tool
    def ping() -> str:
        return "pong"

    async with Client(server) as client:
        await client.call_tool("ping", {})

    snap = METRICS.snapshot()
    assert snap["sessions_started"] == 1
    assert snap["tools"]["ping"]["calls"] == 1
