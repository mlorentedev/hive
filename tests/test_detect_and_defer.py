"""TDD red phase — detect-and-defer cooperation with obsidian-git.

Covers HIVE-115 PR-4 acceptance criterion AC-12 (audit M4 explicit
predicate). Closes the remaining surface of issue #110: cooperate with
obsidian-git when present and healthy, fall back to hive's own commit
when it isn't.

Predicate (post-2026-05-22 audit M4):

    defer ⇔
        env "HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER" == "true"
        AND detect_obsidian_git(vault) returns a non-None config
        AND (
            (now - last_commit_epoch < 2 * autoSaveInterval_seconds)
            OR (`git status --porcelain` is empty)  # idle vault
        )

Idle vault is treated as deferable (external committer alive, quiet).
Dirty + stale = broken; fall back. env=false (default) preserves
current behaviour (always commit).
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="git subprocess fixture uses POSIX path conventions",
)


def _install_obsidian_git_config(
    vault: Path,
    auto_save_interval_minutes: int = 10,
    *,
    commit: bool = False,
) -> None:
    """Drop a minimal obsidian-git ``data.json`` so ``detect_obsidian_git``
    sees the plugin as installed.

    Pass ``commit=True`` to also stage + commit the resulting
    ``.obsidian/`` directory, leaving the working tree clean (needed
    for the "idle vault" test case).
    """
    cfg_dir = vault / ".obsidian" / "plugins" / "obsidian-git"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "data.json").write_text(
        json.dumps({"commitInterval": auto_save_interval_minutes}),
        encoding="utf-8",
    )
    if commit:
        subprocess.run(  # noqa: S603, S607
            ["git", "add", ".obsidian"],
            cwd=vault,
            check=True,
            capture_output=True,
        )
        subprocess.run(  # noqa: S603, S607
            ["git", "commit", "-m", "test: stage obsidian-git config"],
            cwd=vault,
            check=True,
            capture_output=True,
        )


# ── T4.4 — defer when env=true + obsidian healthy ───────────────────────


@_POSIX_ONLY
def test_defer_when_env_true_and_recent_commit(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env=true + obsidian-git present + last commit is recent → defer."""
    from hive._vault_write import _should_defer_to_external_committer

    _install_obsidian_git_config(git_vault, auto_save_interval_minutes=10)
    monkeypatch.setenv("HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER", "true")

    # git_vault fixture creates an "init" commit just now — within
    # 2 * 10 min = 20min window → recent.
    assert _should_defer_to_external_committer(git_vault) is True


# ── T4.4b — env=false → never defer (audit M4 negative) ─────────────────


@_POSIX_ONLY
def test_no_defer_when_env_false(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env=false (default) + obsidian-git healthy → still commits via hive.

    Audit M4: guard against accidental flip of the default during
    refactors. The env var must be explicitly opted into.
    """
    from hive._vault_write import _should_defer_to_external_committer

    _install_obsidian_git_config(git_vault, auto_save_interval_minutes=10)
    monkeypatch.delenv("HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER", raising=False)

    assert _should_defer_to_external_committer(git_vault) is False


@_POSIX_ONLY
def test_no_defer_when_env_explicitly_false(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicitly setting env to 'false' must also skip deferral."""
    from hive._vault_write import _should_defer_to_external_committer

    _install_obsidian_git_config(git_vault, auto_save_interval_minutes=10)
    monkeypatch.setenv("HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER", "false")

    assert _should_defer_to_external_committer(git_vault) is False


# ── T4.5 — fallback when obsidian stale AND dirty (audit M4) ────────────


@_POSIX_ONLY
def test_fallback_when_obsidian_stale_and_dirty(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env=true + obsidian present + last commit > 2*interval AND vault is
    dirty → external committer is genuinely broken → fall back.

    Simulated by rewriting the init commit's date so its age exceeds the
    interval window, and by leaving an uncommitted file in the vault.
    """
    from hive._vault_write import _should_defer_to_external_committer

    _install_obsidian_git_config(git_vault, auto_save_interval_minutes=1)
    monkeypatch.setenv("HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER", "true")

    # Make the existing init commit appear ancient (> 2 minutes ago).
    ancient_iso = "2024-01-01T00:00:00Z"
    subprocess.run(  # noqa: S603, S607
        ["git", "commit", "--amend", "--no-edit", "--date", ancient_iso],
        cwd=git_vault,
        env={
            **subprocess.os.environ,
            "GIT_COMMITTER_DATE": ancient_iso,
        },
        check=True,
        capture_output=True,
    )

    # Leave a dirty file.
    (git_vault / "dirty.md").write_text("# uncommitted\n", encoding="utf-8")

    assert _should_defer_to_external_committer(git_vault) is False


# ── T4.5b — idle vault (clean porcelain) → defer even with stale commit ─


@_POSIX_ONLY
def test_defer_when_obsidian_present_and_vault_idle(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env=true + obsidian present + last commit > 2*interval BUT vault
    is idle (clean porcelain) → defer is safe; nothing to commit anyway.
    """
    from hive._vault_write import _should_defer_to_external_committer

    _install_obsidian_git_config(
        git_vault,
        auto_save_interval_minutes=1,
        commit=True,
    )
    monkeypatch.setenv("HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER", "true")

    # Make the commit ancient AND leave the vault clean.
    ancient_iso = "2024-01-01T00:00:00Z"
    subprocess.run(  # noqa: S603, S607
        ["git", "commit", "--amend", "--no-edit", "--date", ancient_iso],
        cwd=git_vault,
        env={
            **subprocess.os.environ,
            "GIT_COMMITTER_DATE": ancient_iso,
        },
        check=True,
        capture_output=True,
    )
    # Clean porcelain — assert it actually is clean
    porcelain = subprocess.run(  # noqa: S603, S607
        ["git", "status", "--porcelain"],
        cwd=git_vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert porcelain == "", f"vault not idle: {porcelain!r}"

    assert _should_defer_to_external_committer(git_vault) is True


# ── T4.6 — no obsidian-git → hive's commit path runs ───────────────────


@_POSIX_ONLY
def test_no_defer_when_obsidian_absent(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env=true but obsidian-git absent → fall back to hive's commit."""
    from hive._vault_write import _should_defer_to_external_committer

    # NO obsidian-git config file installed
    monkeypatch.setenv("HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER", "true")

    assert _should_defer_to_external_committer(git_vault) is False


# ── Response surface for vault_write ────────────────────────────────────


@_POSIX_ONLY
def test_vault_write_response_announces_deferral(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a write is deferred, the tool response says so explicitly.

    Implementation contract: when ``_should_defer_to_external_committer``
    returns True, ``_git_commit`` is skipped AND the response string ends
    with ``"(deferred to obsidian-git ...)"`` (analogous to the existing
    ``"(uncommitted — call vault_commit to flush)"`` suffix).
    """
    from hive._helpers import _vault_write_deferral_suffix

    _install_obsidian_git_config(git_vault, auto_save_interval_minutes=10)
    monkeypatch.setenv("HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER", "true")

    suffix = _vault_write_deferral_suffix(git_vault, requested_commit=True)
    assert "deferred" in suffix.lower()
    assert "obsidian-git" in suffix.lower()


@_POSIX_ONLY
def test_vault_write_suffix_empty_when_no_defer(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No defer + commit=True → empty deferral suffix (existing behaviour)."""
    from hive._helpers import _vault_write_deferral_suffix

    monkeypatch.delenv("HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER", raising=False)
    suffix = _vault_write_deferral_suffix(git_vault, requested_commit=True)
    assert suffix == ""
