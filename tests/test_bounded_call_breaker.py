"""A stuck tracker costs one bound and one thread, not one per call (#559).

``_bounded_sync_call`` abandons a timed-out thread, which stays blocked on the
tracker lock. Without a breaker every later call waits the full bound again
and leaves another thread behind. With a ``breaker`` key, calls on that key
are skipped outright while the abandoned thread is still alive.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from hive._helpers import _bounded_sync_call
from hive.relevance import RelevanceTracker
from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path


class _Calls:
    def __init__(self) -> None:
        self.count = 0
        self.release = threading.Event()

    def stuck(self) -> None:
        self.count += 1
        self.release.wait(5.0)


def _bounded_threads() -> int:
    return sum(1 for t in threading.enumerate() if t.name.startswith("hive-bounded-"))


def test_a_tripped_breaker_skips_without_waiting_or_spawning() -> None:
    calls, key = _Calls(), object()
    try:
        assert _bounded_sync_call(calls.stuck, 0.05, label="a", breaker=key) == (False, None)
        start = time.monotonic()
        assert _bounded_sync_call(calls.stuck, 0.05, label="a", breaker=key) == (False, None)
        assert time.monotonic() - start < 0.03
        assert calls.count == 1
    finally:
        calls.release.set()


def test_the_breaker_closes_once_the_abandoned_thread_exits() -> None:
    calls, key = _Calls(), object()
    _bounded_sync_call(calls.stuck, 0.05, label="a", breaker=key)
    calls.release.set()
    time.sleep(0.05)
    assert _bounded_sync_call(lambda: 7, 1.0, label="a", breaker=key) == (True, 7)


def test_breakers_are_independent_per_key() -> None:
    calls = _Calls()
    try:
        _bounded_sync_call(calls.stuck, 0.05, label="a", breaker=object())
        assert _bounded_sync_call(lambda: 1, 1.0, label="b", breaker=object()) == (True, 1)
    finally:
        calls.release.set()


def test_without_a_breaker_every_call_runs() -> None:
    calls = _Calls()
    try:
        _bounded_sync_call(calls.stuck, 0.05, label="a")
        _bounded_sync_call(calls.stuck, 0.05, label="a")
        assert calls.count == 2
    finally:
        calls.release.set()


class _StuckRelevance(RelevanceTracker):
    def __init__(self, release: threading.Event) -> None:
        super().__init__()
        self._release = release

    def record_access(self, *args: Any, **kwargs: Any) -> None:
        self._release.wait(5.0)


async def test_track_pays_a_stuck_relevance_lock_once(mock_vault: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("hive._helpers._TRACK_RELEVANCE_TIMEOUT_S", 0.1)
    release = threading.Event()
    mcp = create_server(vault_path=mock_vault, relevance_tracker=_StuckRelevance(release))
    args = {"project": "testproject", "section": "tasks"}
    try:
        before = _bounded_threads()
        await mcp.call_tool("vault_query", args)
        start = time.monotonic()
        for _ in range(3):
            await mcp.call_tool("vault_query", args)
        assert time.monotonic() - start < 0.25, "later calls waited on the stuck lock again"
        assert _bounded_threads() - before <= 1
    finally:
        release.set()
