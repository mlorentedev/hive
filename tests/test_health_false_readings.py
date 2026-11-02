"""vault_health false readings from #466: ``last_updated`` and suffix wikilinks."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.tools import ToolResult


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


async def _health(vault: Path, check: str) -> str:
    mcp = create_server(vault_path=vault)
    return _text(await mcp.call_tool("vault_health", {"checks": [check]}))


def _note(path: Path, body: str = "", extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nid: {path.stem}\ntype: note\nstatus: active\n{extra}---\n\n{body}\n")


# ── 1. last_updated is a freshness date ────────────────────────


async def test_stale_reads_last_updated(mock_vault: Path) -> None:
    today = date.today().isoformat()
    _note(
        mock_vault / "10_projects" / "testproject" / "refreshed.md",
        extra=f'created: "2020-01-01"\nlast_updated: "{today}"\n',
    )
    assert "refreshed.md" not in await _health(mock_vault, "stale")


async def test_stale_still_flags_an_old_last_updated(mock_vault: Path) -> None:
    old = (date.today() - timedelta(days=2000)).isoformat()
    _note(
        mock_vault / "10_projects" / "testproject" / "abandoned.md",
        extra=f'created: "2020-01-01"\nlast_updated: "{old}"\n',
    )
    report = await _health(mock_vault, "stale")
    assert f"abandoned.md: Stale (last touched {old}" in report


async def test_the_later_of_updated_and_last_updated_wins(mock_vault: Path) -> None:
    """A note that used ``updated`` and was later refreshed by /context-refresh
    carries both keys; its last touch is the later date, whichever key holds it."""
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=2000)).isoformat()
    project = mock_vault / "10_projects" / "testproject"
    _note(project / "old-updated.md", extra=f'updated: "{old}"\nlast_updated: "{today}"\n')
    _note(project / "old-last.md", extra=f'updated: "{today}"\nlast_updated: "{old}"\n')
    report = await _health(mock_vault, "stale")
    assert "old-updated.md" not in report
    assert "old-last.md" not in report


# ── 2. a unique path-suffix wikilink resolves ───────────────────


async def test_unique_suffix_wikilink_is_not_broken(mock_vault: Path) -> None:
    project = mock_vault / "10_projects" / "testproject"
    _note(project / "research" / "harness-sota" / "_index.md")
    _note(project / "research" / "_index.md", body="See [[harness-sota/_index]].")
    assert "harness-sota/_index" not in await _health(mock_vault, "links")


async def test_ambiguous_suffix_wikilink_is_still_reported(mock_vault: Path) -> None:
    project = mock_vault / "10_projects" / "testproject"
    _note(project / "a" / "shared" / "note.md")
    _note(project / "b" / "shared" / "note.md")
    _note(project / "linker.md", body="See [[shared/note]].")
    assert "Broken link [[shared/note]]" in await _health(mock_vault, "links")


async def test_suffix_must_start_at_a_path_boundary(mock_vault: Path) -> None:
    project = mock_vault / "10_projects" / "testproject"
    _note(project / "research" / "harness-sota" / "_index.md")
    _note(project / "linker.md", body="See [[ss-sota/_index]].")
    assert "Broken link [[ss-sota/_index]]" in await _health(mock_vault, "links")
