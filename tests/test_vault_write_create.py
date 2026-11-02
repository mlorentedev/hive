"""vault_write create mode: no doubled project path, no stacked frontmatter (#383)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from hive.frontmatter import parse_frontmatter
from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.tools import ToolResult


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


async def _create(vault: Path, path: str, content: str, **extra: str) -> str:
    mcp = create_server(vault_path=vault)
    args = {"project": "testproject", "path": path, "content": content, **extra}
    return _text(await mcp.call_tool("vault_write", args))


_AUTHORED = "---\nid: s-1\ntype: session\nstatus: active\n---\n\n# Session\n"


# ── Defect 1: a vault-rooted path is doubled onto the project dir ──


async def test_create_refuses_a_path_that_repeats_the_project_path(git_vault: Path) -> None:
    result = await _create(git_vault, "10_projects/testproject/sessions/x.md", "# x\n")
    project = git_vault / "10_projects" / "testproject"
    assert not (project / "10_projects").exists()
    assert "relative to the project" in result
    assert "sessions/x.md" in result


@pytest.mark.parametrize(
    "path",
    ["./10_projects/testproject/sessions/x.md", "10_projects/./testproject/sessions/x.md"],
)
async def test_create_refuses_dot_segment_spellings_of_the_doubled_path(
    git_vault: Path, path: str
) -> None:
    result = await _create(git_vault, path, "# x\n")
    assert not (git_vault / "10_projects" / "testproject" / "10_projects").exists()
    assert "Pass path='sessions/x.md'" in result


async def test_create_accepts_a_project_relative_path(git_vault: Path) -> None:
    await _create(git_vault, "sessions/x.md", "# x\n")
    assert (git_vault / "10_projects" / "testproject" / "sessions" / "x.md").exists()


# ── Defect 2: authored frontmatter gets a second block on top ──


async def test_create_keeps_authored_frontmatter_as_the_only_block(git_vault: Path) -> None:
    await _create(git_vault, "sessions/s-1.md", _AUTHORED)
    written = (git_vault / "10_projects" / "testproject" / "sessions" / "s-1.md").read_text()
    assert written == _AUTHORED
    fm = parse_frontmatter(written)
    assert fm is not None
    assert fm.type == "session"


async def test_create_refuses_a_doc_type_that_contradicts_the_authored_type(
    git_vault: Path,
) -> None:
    result = await _create(git_vault, "sessions/s-1.md", _AUTHORED, doc_type="note")
    assert not (git_vault / "10_projects" / "testproject" / "sessions" / "s-1.md").exists()
    assert "type: session" in result
    assert "doc_type='note'" in result


async def test_create_accepts_a_doc_type_that_matches_the_authored_type(
    git_vault: Path,
) -> None:
    await _create(git_vault, "sessions/s-1.md", _AUTHORED, doc_type="session")
    written = (git_vault / "10_projects" / "testproject" / "sessions" / "s-1.md").read_text()
    assert written == _AUTHORED


async def test_create_detects_frontmatter_after_leading_blank_lines(git_vault: Path) -> None:
    await _create(git_vault, "sessions/s-2.md", "\n\n" + _AUTHORED)
    written = (git_vault / "10_projects" / "testproject" / "sessions" / "s-2.md").read_text()
    assert written.count("type:") == 1


async def test_create_still_adds_frontmatter_to_a_body_only_content(git_vault: Path) -> None:
    await _create(git_vault, "notes/n.md", "# Body only\n")
    written = (git_vault / "10_projects" / "testproject" / "notes" / "n.md").read_text()
    fm = parse_frontmatter(written)
    assert fm is not None
    assert fm.type == "note"
    assert written.count("\n---\n") == 1
