"""Lifecycle diagnostics for the Hive MCP server.

Provides a FastMCP middleware that emits structured log entries for each
incoming MCP request: method, request id, source, duration, and outcome
(success / error / cancellation). Entries go to the hive log file via
the standard logging machinery.

The middleware is intentionally cheap: a single ``info`` line per
request when the log level is INFO or below. It does not buffer or
serialise the payload.

If the request handler raises ``CancelledError``, the middleware logs
it explicitly and re-raises — the patch in :mod:`hive._compat` is what
keeps the cancellation from killing the receive loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

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
        start = time.monotonic()
        try:
            result = await call_next(context)
        except asyncio.CancelledError:
            elapsed_ms = (time.monotonic() - start) * 1000
            _log.info(
                "mcp cancelled method=%s id=%s elapsed_ms=%.0f",
                method, request_id, elapsed_ms,
            )
            raise
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            _log.warning(
                "mcp error method=%s id=%s elapsed_ms=%.0f exc=%r",
                method, request_id, elapsed_ms, exc,
            )
            raise
        else:
            elapsed_ms = (time.monotonic() - start) * 1000
            _log.info(
                "mcp ok method=%s id=%s elapsed_ms=%.0f",
                method, request_id, elapsed_ms,
            )
            return result


def _extract_request_id(context: MiddlewareContext[Any]) -> str:
    fastmcp_ctx = getattr(context, "fastmcp_context", None)
    if fastmcp_ctx is None:
        return "-"
    request_context = getattr(fastmcp_ctx, "request_context", None)
    if request_context is None:
        return "-"
    rid = getattr(request_context, "request_id", None)
    return str(rid) if rid is not None else "-"
