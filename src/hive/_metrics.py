"""In-memory cross-session metrics core for the Phase C daemon (ADR-011 §4).

A single long-lived daemon serves N sessions; this is the **live-metrics plane**
(plane 1 of the three-plane telemetry model): per-tool call counts + latency
percentiles + error counts, plus a session-start counter, all held in memory and
aggregated across every session. Because the daemon outlives any one session,
these survive a client disconnect — the property a per-process model could never
offer. Updated inline on the hot path with **no synchronous DB write** (the
anti-pattern HIVE-115 removed); exposed via :meth:`MetricsCore.snapshot` for the
``/status`` endpoint.

The module-level :data:`METRICS` singleton is the process-global the daemon's
``LifecycleMiddleware`` feeds — mirroring the existing ``GHOST_RESPONSES`` /
lock-eviction singleton pattern, so handlers need no ``ServerContext`` plumbing.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Any

# Bounded per-tool latency ring: enough for stable p50/p99 without unbounded
# growth on a long-lived daemon. Matches the rolling-deque sizing used for the
# git-lock wait history (HIVE-115).
_LATENCY_SAMPLES = 256


class _ToolMetrics:
    """Mutable per-tool counters + a bounded latency ring."""

    __slots__ = ("calls", "errors", "latencies")

    def __init__(self) -> None:
        self.calls = 0
        self.errors = 0
        self.latencies: deque[float] = deque(maxlen=_LATENCY_SAMPLES)


def _percentile(values: deque[float], pct: float) -> float:
    """Nearest-rank percentile of *values* in ms, rounded to 0.1 ms."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return round(ordered[idx], 1)


class MetricsCore:
    """Thread-safe live-metrics aggregator (one instance = the daemon's view)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tools: dict[str, _ToolMetrics] = defaultdict(_ToolMetrics)
        self._sessions_started = 0

    def record_call(self, tool: str, elapsed_ms: float, *, ok: bool) -> None:
        """Record one completed tool call (hot path — in-memory only)."""
        with self._lock:
            metrics = self._tools[tool]
            metrics.calls += 1
            if not ok:
                metrics.errors += 1
            metrics.latencies.append(elapsed_ms)

    def record_session_start(self) -> None:
        """Record a new MCP session (one ``initialize``)."""
        with self._lock:
            self._sessions_started += 1

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the live metrics."""
        with self._lock:
            return {
                "sessions_started": self._sessions_started,
                "total_calls": sum(m.calls for m in self._tools.values()),
                "tools": {
                    name: {
                        "calls": m.calls,
                        "errors": m.errors,
                        "p50_ms": _percentile(m.latencies, 50),
                        "p99_ms": _percentile(m.latencies, 99),
                    }
                    for name, m in self._tools.items()
                },
            }

    def reset(self) -> None:
        """Clear all metrics — for test isolation only."""
        with self._lock:
            self._tools.clear()
            self._sessions_started = 0


# Process-global singleton the daemon's middleware feeds and /status reads.
METRICS = MetricsCore()
