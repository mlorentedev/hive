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
