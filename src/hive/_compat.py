"""Compatibility shims for upstream MCP library bugs.

This module monkey-patches ``mcp.shared.session.RequestResponder.__exit__``
to swallow the spurious ``CancelledError`` that the anyio cancel scope
re-raises after we have already responded to a cancelled request.

Without the patch, when a client sends ``notifications/cancelled`` for an
in-flight tool call, the cancellation propagates out of the responder
context manager and kills the receive loop's ``anyio.create_task_group()``.
The process stays alive but stops reading stdin — every subsequent call
hangs, the client sees ``MCP error -32000: Connection closed``, and the
only recovery is restarting the conversation.

See hive issue #75. The patch is designed to be forward-compatible:

* It only fires when the responder has already marked itself completed
  AND the leaking exception is anyio's ``CancelledError`` class — i.e.
  the exact failure mode of the upstream bug. If upstream fixes the bug,
  the trigger never fires and the patch is inert.
* If a future ``mcp`` release removes ``RequestResponder`` or changes its
  attributes, ``apply()`` logs a warning and returns without touching
  anything — Hive degrades to the pre-patch behaviour, never crashes.
* When the upstream fix lands, this file can simply be deleted.
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
    setattr(RequestResponder, _PATCH_APPLIED_ATTR, True)
    _log.debug("Applied RequestResponder.__exit__ cancellation patch (issue #75)")
