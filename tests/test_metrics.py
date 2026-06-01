"""Unit tests for the in-memory metrics core (HIVE-118 / ADR-011 §4).

In-process (no daemon subprocess), so they exercise — and attribute coverage
to — the aggregation logic the `/status` integration test drives end to end:
per-tool counts, error tallies, latency percentiles, session counting, reset.
"""

from __future__ import annotations

from hive._metrics import MetricsCore, _percentile


def test_record_call_counts_and_latency() -> None:
    core = MetricsCore()
    core.record_call("vault_query", 5.0, ok=True)
    core.record_call("vault_query", 7.0, ok=True)
    snap = core.snapshot()
    assert snap["tools"]["vault_query"]["calls"] == 2
    assert snap["tools"]["vault_query"]["errors"] == 0
    assert snap["total_calls"] == 2


def test_record_call_tracks_errors_separately() -> None:
    core = MetricsCore()
    core.record_call("vault_write", 3.0, ok=True)
    core.record_call("vault_write", 9.0, ok=False)
    tool = core.snapshot()["tools"]["vault_write"]
    assert tool["calls"] == 2
    assert tool["errors"] == 1


def test_sessions_started_counter() -> None:
    core = MetricsCore()
    assert core.snapshot()["sessions_started"] == 0
    core.record_session_start()
    core.record_session_start()
    assert core.snapshot()["sessions_started"] == 2


def test_reset_clears_everything() -> None:
    core = MetricsCore()
    core.record_call("ping", 1.0, ok=True)
    core.record_session_start()
    core.reset()
    snap = core.snapshot()
    assert snap["tools"] == {}
    assert snap["sessions_started"] == 0
    assert snap["total_calls"] == 0


def test_percentile_empty_is_zero() -> None:
    from collections import deque

    assert _percentile(deque(), 50) == 0.0


def test_percentile_nearest_rank() -> None:
    from collections import deque

    # 0..10 (11 values) — avoids any .5 rounding boundary: p50 -> index
    # round(0.5*10)=5 -> 5.0; p99 -> index round(0.99*10)=10 -> 10.0.
    values: deque[float] = deque(float(n) for n in range(11))
    assert _percentile(values, 50) == 5.0
    assert _percentile(values, 99) == 10.0


def test_snapshot_is_independent_copy() -> None:
    """Mutating the core after a snapshot must not change the snapshot."""
    core = MetricsCore()
    core.record_call("ping", 1.0, ok=True)
    snap = core.snapshot()
    core.record_call("ping", 1.0, ok=True)
    assert snap["tools"]["ping"]["calls"] == 1
