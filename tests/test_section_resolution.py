"""Section shortcuts resolve to the bare filename first, everywhere (#468).

``vault_query`` reads ``context.md`` before ``00-context.md``. The health
report, the project listing and section writes must agree with it, or a
project on the current convention reads as missing its sections and a write
lands in a file nothing reads.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from fastmcp.tools import ToolResult

_SECTIONS = ("context", "tasks", "roadmap", "lessons")


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


def _bare_project(vault: Path, name: str = "bareproject") -> Path:
    project = vault / "10_projects" / name
    project.mkdir(parents=True)
    for section in _SECTIONS:
        (project / f"{section}.md").write_text(
            f"---\nid: {name}-{section}\ntype: note\nstatus: active\n---\n\n# {section}\n"
        )
    return project


def _project_block(report: str, name: str) -> str:
    start = report.index(f"/{name}")
    end = report.find("\n## ", start)
    return report[start:] if end == -1 else report[start:end]


async def test_health_does_not_call_bare_named_sections_missing(mock_vault: Path) -> None:
    _bare_project(mock_vault)
    mcp = create_server(vault_path=mock_vault)
    report = _text(await mcp.call_tool("vault_health", {}))
    assert "Missing sections" not in _project_block(report, "bareproject")


async def test_health_expects_only_context_of_a_project(mock_vault: Path) -> None:
    """tasks live on the board and lessons in each repo's docs/lessons/ (#468):
    a project with context.md and nothing else is complete."""
    project = mock_vault / "10_projects" / "contextonly"
    project.mkdir(parents=True)
    (project / "context.md").write_text("---\nid: c\ntype: project\nstatus: active\n---\n")
    mcp = create_server(vault_path=mock_vault)
    report = _text(await mcp.call_tool("vault_health", {}))
    assert "Missing sections" not in _project_block(report, "contextonly")


async def test_health_still_names_a_missing_context(mock_vault: Path) -> None:
    project = _bare_project(mock_vault)
    (project / "context.md").unlink()
    mcp = create_server(vault_path=mock_vault)
    report = _text(await mcp.call_tool("vault_health", {}))
    assert "Missing sections: context" in _project_block(report, "bareproject")


async def test_list_shows_bare_named_sections_as_shortcuts(mock_vault: Path) -> None:
    _bare_project(mock_vault)
    mcp = create_server(vault_path=mock_vault)
    listing = _text(await mcp.call_tool("vault_list", {}))
    line = next(ln for ln in listing.splitlines() if "/bareproject" in ln)
    assert "shortcuts: context, tasks, roadmap, lessons" in line


async def test_section_replace_writes_the_file_vault_query_reads(git_vault: Path) -> None:
    project = _bare_project(git_vault)
    subprocess.run(["git", "add", "."], cwd=git_vault, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-qm", "bare"], cwd=git_vault, capture_output=True, check=True)
    mcp = create_server(vault_path=git_vault)
    await mcp.call_tool(
        "vault_write",
        {
            "project": "bareproject",
            "section": "tasks",
            "operation": "replace",
            "content": (
                "---\nid: bareproject-tasks\ntype: note\nstatus: active\n---\n\n# Replaced\n"
            ),
        },
    )
    assert "# Replaced" in (project / "tasks.md").read_text()
    assert not (project / "11-tasks.md").exists()
    read_back = _text(
        await mcp.call_tool("vault_query", {"project": "bareproject", "section": "tasks"})
    )
    assert "# Replaced" in read_back


def test_a_directory_named_like_a_section_does_not_shadow_the_legacy_file(tmp_path: Path) -> None:
    from hive._helpers import section_file

    (tmp_path / "tasks.md").mkdir()
    (tmp_path / "11-tasks.md").write_text("# tasks\n")
    assert section_file(tmp_path, "tasks") == tmp_path / "11-tasks.md"


async def test_section_write_refuses_a_symlink_out_of_the_vault(
    git_vault: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "target.md"
    outside.write_text("untouched\n")
    project = _bare_project(git_vault)
    (project / "tasks.md").unlink()
    (project / "tasks.md").symlink_to(outside)
    mcp = create_server(vault_path=git_vault)
    result = _text(
        await mcp.call_tool(
            "vault_write",
            {"project": "bareproject", "section": "tasks", "content": "- appended\n"},
        )
    )
    assert "escapes vault boundary" in result
    assert outside.read_text() == "untouched\n"
