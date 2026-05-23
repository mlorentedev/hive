"""Compatibility shims for upstream MCP library bugs.

This module monkey-patches two methods on
``mcp.shared.session.RequestResponder`` to keep the stdio receive loop
alive across two related cancellation races:

1. **``__exit__``** — swallow the spurious ``CancelledError`` that the
   anyio cancel scope re-raises after we have already responded to a
   cancelled request (hive issue #75).
2. **``respond``** — when a handler finishes *after* the client has
   already sent ``notifications/cancelled`` (so ``_completed`` is True),
   the original assertion ``assert not self._completed`` fires inside
   ``_handle_request``, propagates to the receive loop's
   ``anyio.create_task_group()``, and kills the server with
   ``AssertionError('Request already responded to')``. Subsequent calls
   from any session sharing the process get ``Connection closed``. The
   patched version short-circuits silently with a debug log.

Without either patch, a slow tool call that the client cancels mid-flight
poisons the transport — the process either stops reading stdin or exits
with the assertion. Recovery requires restarting Claude Code.

Both patches are self-gated to the exact failure mode (responder already
``_completed``) so if upstream fixes the bug the patch becomes inert.
If ``RequestResponder`` is renamed/removed in a future ``mcp`` release,
``apply()`` logs a warning and returns without touching anything.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import anyio

if TYPE_CHECKING:
    from types import TracebackType

GhostSource = Literal["cancellation", "deadline"]

_log = logging.getLogger(__name__)

_PATCH_APPLIED_ATTR = "_hive_cancellation_patch_applied"
_REQUIRED_ATTRS = ("_completed", "_on_complete", "_entered", "_cancel_scope")


class _GhostResponseCounter:
    """Thread-safe counter for responses suppressed after client cancellation.

    Surfaces the otherwise-invisible Fase C event ("handler finished after
    notifications/cancelled; ErrorData ack already on the wire; our late
    success would create a duplicate response") so callers and operators
    can detect when the disk state may have mutated despite the
    cancellation ack the client received.

    Semantic mismatch (ADR-007 Amendment #2): an ErrorData ack does NOT
    imply rollback. The correct client behavior is to verify state via
    ``vault_query`` rather than retry the operation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._last_seen: str | None = None
        self._last_tool: str | None = None
        self._by_source: dict[str, int] = {}

    def record(
        self,
        tool: str | None = None,
        source: GhostSource = "cancellation",
    ) -> None:
        """Record a suppressed late response.

        ``source`` discriminates the trigger so operators can break down
        the metric in ``vault_health.ghost_responses.by_source``:

        - ``"cancellation"`` — client sent ``notifications/cancelled``
          and ``RequestResponder.cancel()`` already wrote an ``ErrorData``
          to the wire; our late success would be a duplicate response
          (the original race documented in ADR-007). Default for
          ``_compat._patched_respond`` to preserve the prior contract.
        - ``"deadline"`` — ``bounded_call`` enforced a hard deadline
          (HIVE-115 PR-3 / ADR-008); the worker thread completed past
          the deadline and we are silencing its late respond(). The
          disk state may have mutated.
        """
        with self._lock:
            self._total += 1
            self._last_seen = datetime.now(UTC).isoformat()
            if tool:
                self._last_tool = tool
            self._by_source[source] = self._by_source.get(source, 0) + 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "total": self._total,
                "last_seen": self._last_seen,
                "last_tool": self._last_tool,
                "by_source": dict(self._by_source),
            }

    def reset(self) -> None:
        with self._lock:
            self._total = 0
            self._last_seen = None
            self._last_tool = None
            self._by_source = {}


GHOST_RESPONSES = _GhostResponseCounter()


def _make_patched_exit(original_exit: Any) -> Any:  # noqa: ARG001 (kept for symmetry)
    def _patched_exit(
        self: Any,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            if self._completed:
                self._on_complete(self)
        finally:
            self._entered = False
            if not self._cancel_scope:
                raise RuntimeError("No active cancel scope")
            try:
                self._cancel_scope.__exit__(exc_type, exc_val, exc_tb)
            except BaseException as exc:
                cancelled_cls = anyio.get_cancelled_exc_class()
                if self._completed and isinstance(exc, cancelled_cls):
                    _log.debug(
                        "Swallowed spurious cancellation on completed "
                        "responder %s (issue #75)",
                        self.request_id,
                    )
                    return
                raise

    return _patched_exit


def _make_patched_respond(original_respond: Any) -> Any:
    async def _patched_respond(self: Any, response: Any) -> None:
        """Suppress respond() when the responder was cancelled mid-flight.

        When the client has already sent ``notifications/cancelled``,
        the upstream ``RequestResponder.cancel()`` synchronously writes
        an ``ErrorData`` frame to the wire (empirically observed in
        20/20 race iterations on Linux — see ADR-007 §1 Amendment #2
        and ``tests/test_compat_shim.py::test_classify_cancellation_race``).
        Our late success here would create a duplicate response under the
        same ``request_id``, which some MCP clients treat as a protocol
        error. So we silently drop the late response.

        Semantic mismatch: the client receives an ErrorData ack but the
        disk state may already be mutated by the handler that ran to
        completion. The ack does NOT imply rollback. Correct client
        behavior is to verify state via ``vault_query`` rather than
        retry the operation.

        Observability: every suppression bumps the module-level
        :data:`GHOST_RESPONSES` counter and emits a WARNING log line
        with the literal prefix
        ``mcp.ghost_response.suppressed_after_cancel_ack`` so operators
        can correlate user-visible "ghost response" reports with server
        events.
        """
        if not self._entered:
            raise RuntimeError(
                "RequestResponder must be used as a context manager",
            )
        if self._completed:
            tool = _tool_name_from_responder(self)
            GHOST_RESPONSES.record(tool)
            _log.warning(
                "mcp.ghost_response.suppressed_after_cancel_ack "
                "request_id=%s tool=%s — disk state may be mutated; "
                "verify via vault_query, do not retry.",
                self.request_id, tool or "<unknown>",
            )
            return
        await original_respond(self, response)

    return _patched_respond


def _tool_name_from_responder(responder: Any) -> str | None:
    """Best-effort extraction of the tool name from a RequestResponder.

    The MCP ``RequestResponder`` carries the original request as
    ``self.request`` (a ``ClientRequest`` union). For tool calls, the
    inner model's ``params`` has a ``name`` attribute. The chain may
    differ across mcp versions, so every dereference is guarded — on
    any miss we return ``None`` and the counter records the event
    without a tool label.
    """
    try:
        request = getattr(responder, "request", None)
        if request is None:
            return None
        root = getattr(request, "root", request)
        params = getattr(root, "params", None)
        name = getattr(params, "name", None)
        return name if isinstance(name, str) else None
    except Exception:
        return None


def apply() -> None:
    """Apply the cancellation-safety patch to ``RequestResponder.__exit__``.

    Idempotent and best-effort: if the upstream API has shifted such
    that the patch cannot be applied safely, log a warning and return.
    """
    try:
        from mcp.shared.session import RequestResponder
    except ImportError as exc:
        _log.warning(
            "Could not import mcp.shared.session.RequestResponder — "
            "cancellation patch skipped (issue #75): %s", exc,
        )
        return

    if getattr(RequestResponder, _PATCH_APPLIED_ATTR, False):
        return

    missing = [a for a in _REQUIRED_ATTRS if a not in RequestResponder.__dict__
               and not hasattr(RequestResponder, a)]
    # Instance attrs aren't in the class dict; we test via __init__ source.
    # A simpler heuristic: just check __exit__ exists, then trust it at call
    # time — our patch only short-circuits when the attrs are set on the
    # instance, so a shape change degrades to the original behaviour.
    if not hasattr(RequestResponder, "__exit__"):
        _log.warning(
            "RequestResponder has no __exit__ — cancellation patch skipped "
            "(issue #75). Missing attrs hint: %s", missing,
        )
        return

    original_exit = RequestResponder.__exit__
    RequestResponder.__exit__ = _make_patched_exit(original_exit)  # type: ignore[method-assign]

    if hasattr(RequestResponder, "respond"):
        original_respond = RequestResponder.respond
        RequestResponder.respond = _make_patched_respond(  # type: ignore[method-assign]
            original_respond,
        )
        _log.debug(
            "Applied RequestResponder.respond patch (respond-after-cancel)",
        )
    else:
        _log.warning(
            "RequestResponder has no respond — respond-after-cancel patch "
            "skipped. Server may crash with AssertionError if a handler "
            "completes after a client cancellation.",
        )

    setattr(RequestResponder, _PATCH_APPLIED_ATTR, True)
    _log.debug("Applied RequestResponder.__exit__ cancellation patch (issue #75)")
