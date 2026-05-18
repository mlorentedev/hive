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
from typing import TYPE_CHECKING, Any

import anyio

if TYPE_CHECKING:
    from types import TracebackType

_log = logging.getLogger(__name__)

_PATCH_APPLIED_ATTR = "_hive_cancellation_patch_applied"
_REQUIRED_ATTRS = ("_completed", "_on_complete", "_entered", "_cancel_scope")


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
        if not self._entered:
            raise RuntimeError(
                "RequestResponder must be used as a context manager",
            )
        if self._completed:
            _log.debug(
                "Suppressed respond() on already-completed responder %s "
                "(handler finished after client cancellation)",
                self.request_id,
            )
            return
        await original_respond(self, response)

    return _patched_respond


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
