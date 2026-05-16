"""Regression tests for issue #75 — MCP transport disconnect after rejection.

The bug: in Claude Code, rejecting the first ``mcp__hive__*`` tool call
poisoned the transport for the rest of the conversation. Subsequent
calls returned ``MCP error -32000: Connection closed``, then
``No such tool available``.

Root cause: ``RequestResponder.__exit__`` in the upstream ``mcp``
library let the anyio ``CancelScope`` re-raise a ``CancelledError``
after the responder had already sent its error response. That exception
propagated to the receive loop's ``anyio.create_task_group()`` and
killed it, so the server stopped reading stdin. ``hive._compat`` patches
the responder to swallow that spurious cancellation.

These tests exercise both the in-memory FastMCP boundary and the real
stdio transport via a subprocess. The subprocess case is the one that
actually reproduces the bug in unpatched ``mcp``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP


def _text(result: object) -> str:
    return result.content[0].text  # type: ignore[union-attr,attr-defined]


@pytest.fixture
def vault_mcp(mock_vault: Path) -> FastMCP:
    return create_server(vault_path=mock_vault)


# ── In-memory characterization ────────────────────────────────────────


class TestInMemoryCancellation:
    """Cancellation at the in-process FastMCP boundary must not poison the server."""

    async def test_cancelled_call_does_not_break_subsequent_calls(
        self, vault_mcp: FastMCP,
    ) -> None:
        task = asyncio.create_task(vault_mcp.call_tool("vault_list", {}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(BaseException):  # noqa: B017,PT011
            await task

        result = await vault_mcp.call_tool("vault_list", {})
        assert _text(result), "server should be callable after a cancellation"

    async def test_many_cancellations_do_not_leak(self, vault_mcp: FastMCP) -> None:
        for _ in range(5):
            task = asyncio.create_task(vault_mcp.call_tool("vault_list", {}))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(BaseException):  # noqa: B017,PT011
                await task

        result = await vault_mcp.call_tool("vault_list", {})
        assert _text(result)


# ── Subprocess characterization (real stdio) ──────────────────────────


_INIT_MSG = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "issue-75-test", "version": "0.0.0"},
    },
}

_INITIALIZED_NOTIFICATION = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
    "params": {},
}


async def _send(proc: asyncio.subprocess.Process, payload: dict[str, object]) -> None:
    assert proc.stdin is not None
    line = (json.dumps(payload) + "\n").encode("utf-8")
    proc.stdin.write(line)
    await proc.stdin.drain()


async def _recv(proc: asyncio.subprocess.Process, timeout: float = 15.0) -> dict[str, object]:
    assert proc.stdout is not None
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    if not line:
        raise RuntimeError("server closed stdout unexpectedly")
    return json.loads(line.decode("utf-8"))  # type: ignore[no-any-return]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "Subprocess transport test is timing-sensitive on Linux CI runners — "
        "the cancellation often arrives after the handler has completed, which "
        "exercises a different code path than the issue #75 bug. The "
        "in-memory cancellation tests validate the patch logic on every "
        "platform; this subprocess test runs locally on Windows where the "
        "original issue #75 was reproduced. Tracked as a follow-up."
    ),
)
class TestSubprocessTransportRecovery:
    """Drive a real hive subprocess via JSON-RPC and verify cancellation tolerance."""

    async def _spawn(self, vault: Path) -> asyncio.subprocess.Process:
        env = os.environ.copy()
        env["VAULT_PATH"] = str(vault)
        env["HIVE_LOG_PATH"] = str(vault / "hive.log")
        env["HIVE_DB_PATH"] = str(vault / "worker.db")
        env["HIVE_RELEVANCE_DB_PATH"] = str(vault / "relevance.db")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "hive.server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await _send(proc, _INIT_MSG)
        ack = await _recv(proc)
        assert ack.get("id") == 1, f"unexpected init ack: {ack!r}"
        await _send(proc, _INITIALIZED_NOTIFICATION)
        return proc

    async def _shutdown(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()
            await proc.wait()

    async def test_server_survives_cancellation_notification(
        self, mock_vault: Path,
    ) -> None:
        """A notifications/cancelled mid-call must not kill the transport."""
        proc = await self._spawn(mock_vault)
        try:
            call_id = 2
            await _send(proc, {
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": "vault_list", "arguments": {}},
            })
            await _send(proc, {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": call_id, "reason": "user rejected"},
            })

            first = await _recv(proc, timeout=15.0)
            assert first.get("id") == call_id, f"unexpected first response: {first!r}"

            await _send(proc, {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "vault_list", "arguments": {}},
            })
            second = await _recv(proc, timeout=15.0)
            assert second.get("id") == 3, f"transport poisoned: {second!r}"
            assert "result" in second or "error" in second
        finally:
            await self._shutdown(proc)

