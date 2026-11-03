"""track() bounds its relevance write, so a stuck tracker cannot hang a tool (#289)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from hive.relevance import RelevanceTracker
from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _StuckRelevanceTracker(RelevanceTracker):
    def record_access(self, project: str, section: str, *, is_write: bool = False) -> None:
        # The #282 stall shape: held while holding the lock, as a slow disk
        # under the every-50th-call flush would be.
        with self._lock:
            time.sleep(0.5)


async def test_vault_query_returns_promptly_when_relevance_is_stuck(
    mock_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hive._helpers._TRACK_RELEVANCE_TIMEOUT_S", 0.05)
    mcp = create_server(vault_path=mock_vault, relevance_tracker=_StuckRelevanceTracker())

    start = time.monotonic()
    result = await mcp.call_tool("vault_query", {"project": "testproject", "section": "tasks"})
    elapsed = time.monotonic() - start

    assert "Task one" in result.content[0].text  # type: ignore[union-attr]
    assert elapsed < 0.4, f"vault_query waited on the stuck tracker for {elapsed:.2f}s"


async def test_track_still_records_access_when_relevance_is_healthy(mock_vault: Path) -> None:
    relevance = RelevanceTracker()
    mcp = create_server(vault_path=mock_vault, relevance_tracker=relevance)
    await mcp.call_tool("vault_query", {"project": "testproject", "section": "tasks"})
    relevance.flush()
    assert relevance.get_scores("testproject").get("tasks", 0) > 0
