"""``_git_commit`` commits only the paths it was given (#470).

Several agent sessions share one vault, and each runs its own hive process. A
commit without a pathspec records the whole index, so one session's write used
to land another session's staged, half-finished work under its own message.
These tests run real git, because the defect lives in what git puts in the
commit, not in the argv a fake would record.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hive._helpers import _git_commit


def _git(vault: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=vault, capture_output=True, text=True, check=True)
    return done.stdout


def _committed(vault: Path) -> set[str]:
    out = _git(vault, "show", "--name-only", "--format=", "HEAD")
    return set(out.split())


def _staged(vault: Path) -> set[str]:
    return set(_git(vault, "diff", "--cached", "--name-only").split())


def test_another_sessions_staged_work_stays_out_of_the_commit(git_vault: Path) -> None:
    (git_vault / "theirs.md").write_text("half-finished\n", encoding="utf-8")
    _git(git_vault, "add", "theirs.md")
    (git_vault / "mine.md").write_text("done\n", encoding="utf-8")

    assert _git_commit(git_vault, [Path("mine.md")], "vault: write mine.md")

    assert _committed(git_vault) == {"mine.md"}
    assert _staged(git_vault) == {"theirs.md"}, "the other session's work must stay staged"


def test_a_deletion_commits_without_sweeping_the_index(git_vault: Path) -> None:
    (git_vault / "gone.md").write_text("x\n", encoding="utf-8")
    _git(git_vault, "add", "gone.md")
    _git(git_vault, "commit", "-m", "add gone.md")
    (git_vault / "theirs.md").write_text("half-finished\n", encoding="utf-8")
    _git(git_vault, "add", "theirs.md")
    (git_vault / "gone.md").unlink()

    assert _git_commit(git_vault, [Path("gone.md")], "vault: delete gone.md")

    assert _committed(git_vault) == {"gone.md"}
    assert "gone.md" not in _git(git_vault, "ls-files")
    assert _staged(git_vault) == {"theirs.md"}


def test_a_path_with_glob_characters_is_matched_literally(git_vault: Path) -> None:
    # As a glob, `a[1].md` also matches `a1.md`.
    (git_vault / "a1.md").write_text("theirs\n", encoding="utf-8")
    _git(git_vault, "add", "a1.md")
    (git_vault / "a[1].md").write_text("mine\n", encoding="utf-8")

    assert _git_commit(git_vault, [Path("a[1].md")], "vault: write a[1].md")

    assert _committed(git_vault) == {"a[1].md"}
    assert _staged(git_vault) == {"a1.md"}


def test_staging_a_glob_named_path_leaves_its_glob_matches_alone(git_vault: Path) -> None:
    # A non-literal `git add a[1].md` also stages an unstaged `a1.md`.
    (git_vault / "a1.md").write_text("theirs, unstaged\n", encoding="utf-8")
    (git_vault / "a[1].md").write_text("mine\n", encoding="utf-8")

    assert _git_commit(git_vault, [Path("a[1].md")], "vault: write a[1].md")

    assert _committed(git_vault) == {"a[1].md"}
    assert _staged(git_vault) == set(), "a1.md must not have been staged"
    assert "a1.md" in _git(git_vault, "ls-files", "--others")


@pytest.mark.asyncio
async def test_vault_commit_leaves_another_sessions_staged_work_out(
    git_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #510: the same hazard as #470, one layer up. `vault_commit` flushed with
    # `git add -A` and a bare commit, so another session's staged file landed
    # under this session's message.
    from hive.server import create_server

    # Above the auto-tick ceiling: no reconciler thread races the flush.
    monkeypatch.setenv("HIVE_COMMIT_TICK_S", "1000")
    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]
    (git_vault / "theirs.md").write_text("half-finished\n", encoding="utf-8")
    _git(git_vault, "add", "theirs.md")
    head_before = _git(git_vault, "rev-parse", "HEAD")

    await mcp.call_tool(
        "vault_write",
        {
            "project": "testproject",
            "section": "tasks",
            "operation": "append",
            "content": "\n- [ ] mine\n",
            "commit": False,
        },
    )
    await mcp.call_tool("vault_commit", {"message": "vault: flush mine"})

    assert _git(git_vault, "rev-parse", "HEAD") != head_before, "vault_commit committed nothing"
    assert _committed(git_vault) == {"10_projects/testproject/11-tasks.md"}
    assert _staged(git_vault) == {"theirs.md"}, "the other session's work must stay staged"
    ctx.reconciler.close()


def test_vault_commit_flush_leaves_a_staged_then_edited_path_out(git_vault: Path) -> None:
    # `MM`: committing the working-tree copy would carry the staged half too.
    from hive._helpers import _git_commit_all

    (git_vault / "theirs.md").write_text("staged\n", encoding="utf-8")
    _git(git_vault, "add", "theirs.md")
    (git_vault / "theirs.md").write_text("staged, then edited\n", encoding="utf-8")
    (git_vault / "dropped-in-by-hand.md").write_text("x\n", encoding="utf-8")

    status, _, left_out = _git_commit_all(git_vault, "vault: flush")

    assert status == "committed"
    assert _committed(git_vault) == {"dropped-in-by-hand.md"}
    assert _staged(git_vault) == {"theirs.md"}
    assert left_out == ["theirs.md"]


def test_vault_commit_flush_commits_an_unstaged_deletion(git_vault: Path) -> None:
    from hive._helpers import _git_commit_all

    (git_vault / "gone.md").write_text("x\n", encoding="utf-8")
    _git(git_vault, "add", "gone.md")
    _git(git_vault, "commit", "-m", "add gone.md")
    (git_vault / "theirs.md").write_text("half-finished\n", encoding="utf-8")
    _git(git_vault, "add", "theirs.md")
    (git_vault / "gone.md").unlink()

    status, _, left_out = _git_commit_all(git_vault, "vault: flush")

    assert status == "committed"
    assert _committed(git_vault) == {"gone.md"}
    assert _staged(git_vault) == {"theirs.md"}


@pytest.mark.asyncio
async def test_vault_commit_says_so_when_only_staged_work_remains(git_vault: Path) -> None:
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]
    (git_vault / "theirs.md").write_text("half-finished\n", encoding="utf-8")
    _git(git_vault, "add", "theirs.md")
    head_before = _git(git_vault, "rev-parse", "HEAD")

    result = await mcp.call_tool("vault_commit", {"message": "vault: flush"})
    text = result.content[0].text  # type: ignore[attr-defined,union-attr]

    assert _git(git_vault, "rev-parse", "HEAD") == head_before
    # Named, not counted: one of them may be hive's own orphan, staged by a
    # write killed between `git add` and `git commit`.
    assert "staged path(s) out: theirs.md." in text, text
    assert "clean" not in text.lower(), "a dirty index must not read as a clean tree"
    ctx.reconciler.close()


def test_vault_commit_flush_reads_a_worktree_rename_as_two_paths(git_vault: Path) -> None:
    # Porcelain v1 `-z` gives a worktree rename (` R`, after `git add -N`) a
    # second field for its source, as it does a staged one. Read as an entry
    # of its own, `old.md` became a staged path called `.md`.
    from hive._helpers import _git_commit_all

    (git_vault / "old.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(git_vault, "add", "old.md")
    _git(git_vault, "commit", "-m", "add old.md")
    (git_vault / "old.md").rename(git_vault / "new.md")
    _git(git_vault, "add", "-N", "new.md")

    status, _, left_out = _git_commit_all(git_vault, "vault: flush")

    assert status == "committed"
    assert left_out == []
    shown = _git(git_vault, "show", "--name-only", "--no-renames", "--format=", "HEAD")
    assert set(shown.split()) == {"old.md", "new.md"}
    assert _git(git_vault, "status", "--porcelain") == ""
