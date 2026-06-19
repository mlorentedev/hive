"""Lifecycle diagnostics for the Hive MCP server.

Provides a FastMCP middleware that emits structured log entries for each
incoming MCP request: method, request id, source, duration, and outcome
(success / error / cancellation). Entries go to the hive log file via
the standard logging machinery.

The middleware is intentionally cheap: a single ``info`` line per
request when the log level is INFO or below. It does not buffer or
serialise the payload.

If the request handler raises ``CancelledError``, the middleware logs
it explicitly and re-raises. The in-flight cancellation is then absorbed
by ``mcp``'s own ``Server._handle_request`` guard; the residual
respond-after-cancel race is handled by the patch in :mod:`hive._compat`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from hive._metrics import METRICS

if TYPE_CHECKING:
    from fastmcp.server.middleware import CallNext

_log = logging.getLogger(__name__)


class LifecycleMiddleware(Middleware):
    """Log every MCP message with its method, request id, and duration."""

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        method = context.method or "<unknown>"
        request_id = _extract_request_id(context)
        tool = _extract_tool_name(context)
        start = time.monotonic()
        try:
            result = await call_next(context)
        except asyncio.CancelledError:
            elapsed_ms = (time.monotonic() - start) * 1000
            _record(tool, elapsed_ms, ok=False)
            _log.info(
                "mcp cancelled method=%s tool=%s id=%s elapsed_ms=%.0f",
                method,
                tool,
                request_id,
                elapsed_ms,
            )
            raise
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            _record(tool, elapsed_ms, ok=False)
            _log.warning(
                "mcp error method=%s tool=%s id=%s elapsed_ms=%.0f exc=%r",
                method,
                tool,
                request_id,
                elapsed_ms,
                exc,
            )
            raise
        else:
            elapsed_ms = (time.monotonic() - start) * 1000
            _record(tool, elapsed_ms, ok=True)
            if method == "initialize":
                METRICS.record_session_start()
            _log.info(
                "mcp ok method=%s tool=%s id=%s elapsed_ms=%.0f",
                method,
                tool,
                request_id,
                elapsed_ms,
            )
            return result


def _record(tool: str, elapsed_ms: float, *, ok: bool) -> None:
    """Feed the live-metrics core, but only for real tool calls.

    ``tool`` is ``"-"`` for non-``tools/call`` methods (initialize, list_*,
    notifications); those are not per-tool metrics, so they are skipped here —
    session starts are counted separately by the caller.
    """
    if tool != "-":
        METRICS.record_call(tool, elapsed_ms, ok=ok)


def _extract_request_id(context: MiddlewareContext[Any]) -> str:
    fastmcp_ctx = getattr(context, "fastmcp_context", None)
    if fastmcp_ctx is None:
        return "-"
    request_context = getattr(fastmcp_ctx, "request_context", None)
    if request_context is None:
        return "-"
    rid = getattr(request_context, "request_id", None)
    return str(rid) if rid is not None else "-"


def _extract_tool_name(context: MiddlewareContext[Any]) -> str:
    """Return the tool name for tools/call, else '-'."""
    if context.method != "tools/call":
        return "-"
    name = getattr(context.message, "name", None)
    return str(name) if name else "-"
