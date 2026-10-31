"""``_git_commit`` commits only the paths it was given (#470).

Several agent sessions share one vault, and each runs its own hive process. A
commit without a pathspec records the whole index, so one session's write used
to land another session's staged, half-finished work under its own message.
These tests run real git, because the defect lives in what git puts in the
commit, not in the argv a fake would record.
"""

from __future__ import annotations

import subprocess
import sys
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
    result = await mcp.call_tool("vault_commit", {"message": "vault: flush mine"})
    text = result.content[0].text  # type: ignore[attr-defined,union-attr]

    assert _git(git_vault, "rev-parse", "HEAD") != head_before, "vault_commit committed nothing"
    assert _committed(git_vault) == {"10_projects/testproject/11-tasks.md"}
    assert _staged(git_vault) == {"theirs.md"}, "the other session's work must stay staged"
    assert "staged path(s) out: theirs.md." in text, text
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


def test_vault_commit_flush_names_both_halves_of_a_staged_rename(git_vault: Path) -> None:
    # `git commit -- <new>` alone would leave the source's deletion staged.
    from hive._helpers import _git_commit_all

    (git_vault / "old.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(git_vault, "add", "old.md")
    _git(git_vault, "commit", "-m", "add old.md")
    _git(git_vault, "mv", "old.md", "new.md")
    (git_vault / "dropped-in-by-hand.md").write_text("x\n", encoding="utf-8")

    status, _, left_out = _git_commit_all(git_vault, "vault: flush")

    assert status == "committed"
    assert sorted(left_out) == ["new.md", "old.md"]


def test_vault_commit_flush_works_when_the_vault_is_a_repo_subdirectory(tmp_path: Path) -> None:
    # Porcelain paths are relative to the repository root, not to the cwd.
    from hive._helpers import _git_commit_all

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "init")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("x\n", encoding="utf-8")

    status, detail, _ = _git_commit_all(vault, "vault: flush")

    assert status == "committed", detail
    # Only note.md is asserted: in such a vault hive's own lock lands in the
    # working tree as `vault/.hive.lock`, which a sweep also takes (#536).
    assert "vault/note.md" in _committed(tmp_path)


def test_vault_commit_flush_keeps_paths_off_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows caps a command line at 32,767 characters; a flush of a few
    # hundred paths must not depend on fitting under it.
    from hive._helpers import _git_commit_all

    porcelain = "".join(f"?? notes/{'x' * 80}-{i}.md\0" for i in range(2000))
    argvs: list[list[str]] = []

    def fake_run_git(
        args: list[str], _vault: Path, *, registry: object = None, **_kw: object
    ) -> tuple[int, str, str]:
        argvs.append(args)
        if args[:2] == ["status", "--porcelain"]:
            return 0, porcelain, ""
        if args[:1] == ["rev-parse"]:
            return 0, "0" * 40, ""
        return 0, "", ""

    monkeypatch.setattr("hive._helpers._run_git", fake_run_git)

    status, _, _ = _git_commit_all(tmp_path, "vault: flush")

    assert status == "committed"
    assert max(len(" ".join(a)) for a in argvs) < 1000


def test_vault_commit_flush_with_many_paths_commits_them_all(git_vault: Path) -> None:
    from hive._helpers import _git_commit_all

    (git_vault / "theirs.md").write_text("half-finished\n", encoding="utf-8")
    _git(git_vault, "add", "theirs.md")
    names = {f"bulk/{i:03d}[x].md" for i in range(300)}
    (git_vault / "bulk").mkdir()
    for name in names:
        (git_vault / name).write_text("x\n", encoding="utf-8")

    status, detail, left_out = _git_commit_all(git_vault, "vault: flush")

    assert status == "committed", detail
    assert _committed(git_vault) == names
    assert left_out == ["theirs.md"]


@pytest.mark.skipif(
    sys.platform in ("darwin", "win32"), reason="the filesystem refuses non-UTF-8 names"
)
def test_vault_commit_flush_commits_a_non_utf8_file_name(git_vault: Path) -> None:
    # Decoded with "replace", the name no longer matched the file on disk.
    import os

    from hive._helpers import _git_commit_all

    name = b"caf\xe9.md"
    (Path(os.fsencode(git_vault)) / name).write_bytes(b"x\n")  # type: ignore[operator]

    status, detail, _ = _git_commit_all(git_vault, "vault: flush")

    assert status == "committed", detail
    out = subprocess.run(
        ["git", "show", "--name-only", "-z", "--format=", "HEAD"],
        cwd=git_vault,
        capture_output=True,
        check=True,
    ).stdout
    assert out.rstrip(b"\0").split(b"\0")[-1] == name


@pytest.mark.asyncio
async def test_vault_commit_names_left_out_paths_beyond_the_first_ten(git_vault: Path) -> None:
    # A killed hive write can sit anywhere in the staged list.
    from hive.server import create_server

    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]
    for i in range(15):
        (git_vault / f"theirs-{i:02d}.md").write_text("x\n", encoding="utf-8")
    _git(git_vault, "add", ".")

    result = await mcp.call_tool("vault_commit", {"message": "vault: flush"})
    text = result.content[0].text  # type: ignore[attr-defined,union-attr]

    assert "theirs-14.md" in text, text
    ctx.reconciler.close()


@pytest.mark.asyncio
async def test_vault_commit_failure_still_names_left_out_paths(
    git_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hive import _vault_write
    from hive.server import create_server

    monkeypatch.setattr(
        _vault_write,
        "_git_commit_all",
        lambda _vault, _msg: ("error", "git commit rc=1: boom", ["theirs.md"]),
    )
    mcp = create_server(vault_path=git_vault)
    ctx = mcp._hive_ctx  # type: ignore[attr-defined]

    result = await mcp.call_tool("vault_commit", {"message": "vault: flush"})
    text = result.content[0].text  # type: ignore[attr-defined,union-attr]

    assert text.startswith("Commit failed"), text
    assert "staged path(s) out: theirs.md." in text, text
    ctx.reconciler.close()
