"""Tests for the deferred-commit reconciler primitives (HIVE-322 / ADR-018).

The reconciler runs in a plain daemon thread, so it cannot reuse
``bounded_call`` — that is ``async def`` and would need an event loop
stood up inside the thread purely to call it (ADR-018 §2). What it reuses
instead is the *termination* half of ``_deadline.py``, which is where the
cross-OS risk actually lives.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX sleep/process-group path; Windows termination is covered in test_bounded_call",
)


# ── AC4 — a flush that overruns its deadline ────────────────────────────


@_POSIX_ONLY
def test_flush_exceeding_deadline_is_terminated(
    git_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC4: an overrunning flush is killed, logged, and reports failure.

    The flush is driven past its deadline by replacing ``_run_git`` with a
    stand-in that spawns a genuinely long-lived process and registers it
    exactly as the real helper documents — caller-supplied list, else
    ``_GIT_REGISTRY_CV``. That keeps the subject of the test the watchdog,
    not git's own timing, while still handing it a real OS process to
    terminate.
    """
    from hive import _helpers
    from hive._commit_queue import flush_paths

    spawned: list[subprocess.Popen[bytes]] = []

    def _hanging_run_git(
        args: list[str],
        vault_path: Path,
        *,
        registry: list[subprocess.Popen[bytes]] | None = None,
    ) -> tuple[int, str, str]:
        if registry is None:
            registry = _helpers._GIT_REGISTRY_CV.get()
        proc = subprocess.Popen(  # noqa: S603, S607
            ["sleep", "60"],
            cwd=vault_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        spawned.append(proc)
        if registry is not None:
            registry.append(proc)
        proc.communicate()
        return (0, "", "")

    monkeypatch.setattr(_helpers, "_run_git", _hanging_run_git)

    target = git_vault / "10_projects" / "deadline-victim.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# queued before the flush\n", encoding="utf-8")

    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="hive._commit_queue"):
        ok = flush_paths(
            git_vault,
            [target.relative_to(git_vault)],
            "chore(vault): deferred commit",
            deadline_s=1.0,
        )
    elapsed = time.monotonic() - started

    # The flush reports failure rather than raising — the reconciler thread
    # must survive a git that hangs.
    assert ok is False

    # It returned on its own deadline, not on the 60s sleep.
    assert elapsed < 20.0, f"flush did not honour its deadline: {elapsed:.1f}s"

    # No orphaned process survives the kill.
    for proc in spawned:
        proc.wait(timeout=10)
        assert proc.poll() is not None

    # No stale index.lock is left behind for the next flush to trip over.
    assert not (git_vault / ".git" / "index.lock").exists()

    # The kill is observable; a silent one would be indistinguishable from
    # a flush that simply had nothing to do.
    assert any("deadline" in rec.message for rec in caplog.records), [
        r.message for r in caplog.records
    ]


@_POSIX_ONLY
def test_flush_within_deadline_commits_and_reports_success(git_vault: Path) -> None:
    """A flush that completes in time commits its paths and returns True."""
    from hive._commit_queue import flush_paths

    target = git_vault / "10_projects" / "happy-path.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# committed by the reconciler\n", encoding="utf-8")

    head_before = subprocess.run(  # noqa: S603, S607
        ["git", "rev-parse", "HEAD"],
        cwd=git_vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    ok = flush_paths(
        git_vault,
        [target.relative_to(git_vault)],
        "chore(vault): deferred commit",
        deadline_s=30.0,
    )

    assert ok is True

    head_after = subprocess.run(  # noqa: S603, S607
        ["git", "rev-parse", "HEAD"],
        cwd=git_vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_after != head_before, "flush reported success but HEAD did not move"

    committed = subprocess.run(  # noqa: S603, S607
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=git_vault,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "10_projects/happy-path.md" in committed
