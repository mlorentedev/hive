"""Tests for opt-in batched writes + vault_commit tool + obsidian-git detection.

Covers HIVE-104 Fase B1 acceptance criterion AC-4:
- ``vault_write(commit=False)`` and ``vault_patch(commit=False)`` write the
  file but leave ``git status --porcelain`` showing it dirty; response payload
  contains ``"committed": false``.
- ``vault_commit(message="...")`` returns a 40-char SHA and clears the dirty
  state.
- When ``<vault>/.obsidian/plugins/obsidian-git/data.json`` is present with
  ``commitInterval > 0``, ``vault_health`` includes
  ``external_committer: "obsidian-git"``.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from hive._helpers import detect_obsidian_git
from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from mcp.types import ContentBlock


def _text(result: object) -> str:
    """Extract the first text block from an MCP call_tool result."""
    content: list[ContentBlock] = result.content  # type: ignore[attr-defined]
    [first] = content
    return first.text  # type: ignore[attr-defined]


def _porcelain(vault: Path) -> str:
    """Run git status --porcelain in the vault and return stdout."""
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _commit_count(vault: Path) -> int:
    return int(
        subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=vault,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )


@pytest.mark.asyncio
class TestCommitFalseLeavesFileDirty:
    """vault_write(commit=False) / vault_patch(commit=False) skip git commit."""

    async def test_vault_write_commit_false_leaves_file_dirty(
        self,
        git_vault: Path,
    ) -> None:
        before = _commit_count(git_vault)
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_write",
                {
                    "project": "testproject",
                    "section": "tasks",
                    "operation": "append",
                    "content": "\n- [ ] uncommitted task\n",
                    "commit": False,
                },
            )
        )
        # No new commit made
        assert _commit_count(git_vault) == before
        # File is dirty in working tree
        assert "11-tasks.md" in _porcelain(git_vault)
        # Response signals uncommitted state
        assert "uncommitted" in result.lower()

    async def test_vault_patch_commit_false_leaves_file_dirty(
        self,
        git_vault: Path,
    ) -> None:
        before = _commit_count(git_vault)
        mcp = create_server(vault_path=git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_patch",
                {
                    "project": "testproject",
                    "path": "11-tasks.md",
                    "find": "- [ ] Task one",
                    "replace": "- [x] Task one done",
                    "commit": False,
                },
            )
        )
        assert _commit_count(git_vault) == before
        assert "11-tasks.md" in _porcelain(git_vault)
        assert "uncommitted" in result.lower()

    async def test_vault_write_default_commits(self, git_vault: Path) -> None:
        """Default commit=True must keep current behavior (additive change)."""
        before = _commit_count(git_vault)
        mcp = create_server(vault_path=git_vault)
        await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "section": "tasks",
                "operation": "append",
                "content": "\n- [ ] task\n",
            },
        )
        assert _commit_count(git_vault) == before + 1
        assert _porcelain(git_vault) == ""


@pytest.mark.asyncio
class TestVaultCommitTool:
    """vault_commit(message) flushes the working tree into a single commit."""

    async def test_vault_commit_returns_sha_and_clears_dirty(
        self,
        git_vault: Path,
    ) -> None:
        mcp = create_server(vault_path=git_vault)
        # Dirty the tree without committing
        await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "section": "tasks",
                "operation": "append",
                "content": "\n- [ ] one\n",
                "commit": False,
            },
        )
        await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "section": "lessons",
                "operation": "append",
                "content": "\n### [2026-05-20] note\nbody.\n",
                "commit": False,
            },
        )
        assert _porcelain(git_vault) != ""
        before = _commit_count(git_vault)

        result = _text(
            await mcp.call_tool(
                "vault_commit",
                {"message": "batch update"},
            )
        )

        assert _commit_count(git_vault) == before + 1
        assert _porcelain(git_vault) == ""
        # Result mentions a 40-char SHA (trailing punctuation is stripped)
        assert any(
            len(tok) == 40 and all(c in "0123456789abcdef" for c in tok)
            for tok in result.replace(".", " ").split()
        ), f"expected 40-char SHA in result, got: {result!r}"

    async def test_vault_commit_noop_when_clean(
        self,
        git_vault: Path,
    ) -> None:
        mcp = create_server(vault_path=git_vault)
        before = _commit_count(git_vault)
        result = _text(
            await mcp.call_tool(
                "vault_commit",
                {"message": "noop"},
            )
        )
        assert _commit_count(git_vault) == before
        assert "clean" in result.lower() or "nothing" in result.lower()


class TestObsidianGitDetection:
    """detect_obsidian_git uses pathlib joining (Risk #4 cross-platform)."""

    def test_detect_returns_interval_when_data_json_present(
        self,
        tmp_path: Path,
    ) -> None:
        plugin_dir = tmp_path / ".obsidian" / "plugins" / "obsidian-git"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "data.json").write_text(
            json.dumps({"commitInterval": 10}),
            encoding="utf-8",
        )
        result = detect_obsidian_git(tmp_path)
        assert result is not None
        assert result["commit_interval"] == 10

    def test_detect_returns_none_when_no_obsidian_dir(
        self,
        tmp_path: Path,
    ) -> None:
        assert detect_obsidian_git(tmp_path) is None

    def test_detect_returns_none_when_interval_zero(
        self,
        tmp_path: Path,
    ) -> None:
        plugin_dir = tmp_path / ".obsidian" / "plugins" / "obsidian-git"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "data.json").write_text(
            json.dumps({"commitInterval": 0}),
            encoding="utf-8",
        )
        assert detect_obsidian_git(tmp_path) is None

    def test_detect_returns_none_when_malformed_json(
        self,
        tmp_path: Path,
    ) -> None:
        plugin_dir = tmp_path / ".obsidian" / "plugins" / "obsidian-git"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "data.json").write_text("not json", encoding="utf-8")
        assert detect_obsidian_git(tmp_path) is None


@pytest.mark.asyncio
class TestVaultHealthExternalCommitter:
    """vault_health surfaces external_committer when obsidian-git is present."""

    async def test_health_surfaces_external_committer(
        self,
        git_vault: Path,
    ) -> None:
        plugin_dir = git_vault / ".obsidian" / "plugins" / "obsidian-git"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "data.json").write_text(
            json.dumps({"commitInterval": 5}),
            encoding="utf-8",
        )
        mcp = create_server(vault_path=git_vault)
        result = _text(await mcp.call_tool("vault_health", {}))
        assert "external_committer" in result
        assert "obsidian-git" in result

    async def test_health_omits_external_committer_when_absent(
        self,
        git_vault: Path,
    ) -> None:
        mcp = create_server(vault_path=git_vault)
        result = _text(await mcp.call_tool("vault_health", {}))
        assert "external_committer" not in result
