"""Ghost-response counter surfaced by ``vault_health``.

A *ghost response* is a tool result that never reached the client although
the handler ran, so the disk state may have changed even though the client
saw a cancellation or a timeout (ADR-007 Amendment #2). The counter makes
those events visible as ``vault_health.ghost_responses``.

Until #434 this module also monkey-patched the private
``mcp.shared.session.RequestResponder.respond``: on mcp 1.x a response
produced after its request was cancelled tripped
``assert not self._completed`` and killed the server
(modelcontextprotocol/python-sdk#2416). hive now requires mcp 2.x, whose
dispatcher never answers a cancelled request, so the patch is gone;
``tests/test_cancel_race.py`` guards the behaviour it provided. Where the
counter should live, and what should feed its ``cancellation`` source on
2.x, is #442.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Literal

GhostSource = Literal["cancellation", "deadline"]


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

        - ``"cancellation"`` — recorded by the mcp 1.x respond-after-cancel
          patch (ADR-007), removed in #434. On mcp 2.x the dispatcher drops
          a cancelled request's late response itself, so nothing records
          this source until #442 decides its replacement.
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
