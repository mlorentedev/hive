"""TDD Red — HIVE-116 AC-3 + AC-4: external termination signalling.

Spec: ``specs/HIVE-116-stale-lock-after-deadline/proposal.md`` AC-3, AC-4.

When ``bounded_call`` / ``tool_span`` terminates a registered Popen
mid-flight, the current behaviour is:

- ``_run_git`` returns ``rc=-1, stdout="", stderr=""`` (magic ``-1``).
- ``_git_commit`` logs ``WARNING git add failed for [...] rc=-1 err=`` —
  empty err leaves the operator with no signal about *why*.

After this spec:

- AC-3: ``_run_git`` returns a synthetic stderr of the form
  ``"[external_termination] killed by supervisor at <ISO-8601>; original
  stderr: (empty|<N> bytes)"`` so log readers + the partial-state hook
  can distinguish supervisor-kill from genuine git failure.
- AC-4: ``_git_commit``'s warning log carries ``cause=external_termination``
  when ``rc == RC_EXTERNAL_TERMINATION``, ``cause=git_error`` otherwise.

This file unit-tests the synthesis primitives only. The full integration
(supervisor kills Popen inside ``_run_git`` and the synthetic stderr
propagates) lives in ``test_cross_worker_lock.py`` (T-2.7).
"""

from __future__ import annotations

import logging
import re
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only first pass; Windows variant lands in T-3.2",
)


# ── AC-3 (a) — named constant exists ─────────────────────────────────────


def test_rc_external_termination_constant_exists() -> None:
    """``RC_EXTERNAL_TERMINATION = -1`` replaces the magic number in _run_git.

    Red: constant not yet defined; ImportError on collection.
    """
    from hive._helpers import RC_EXTERNAL_TERMINATION

    assert RC_EXTERNAL_TERMINATION == -1


# ── AC-3 (b) — synthetic stderr shape ────────────────────────────────────


_SYNTHETIC_STDERR_RE = re.compile(
    r"^\[external_termination\] killed by supervisor at "
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}) ; "
    r"original stderr: (empty|\d+ bytes)$",
)


def test_synthesize_external_termination_stderr_empty() -> None:
    """Synthesizer with no original stderr produces ``original stderr: empty``.

    Red: ``synthesize_external_termination_stderr`` not yet defined.
    """
    from hive._helpers import synthesize_external_termination_stderr

    out = synthesize_external_termination_stderr(original_stderr=b"")
    assert "[external_termination] killed by supervisor at " in out
    assert "original stderr: empty" in out


def test_synthesize_external_termination_stderr_with_bytes() -> None:
    """Synthesizer with N bytes of stderr produces ``original stderr: <N> bytes``."""
    from hive._helpers import synthesize_external_termination_stderr

    out = synthesize_external_termination_stderr(original_stderr=b"hello world")
    assert "original stderr: 11 bytes" in out


# ── AC-4 — warning log carries cause= tag ────────────────────────────────


@_POSIX_ONLY
def test_git_commit_warning_carries_cause_git_error(
    git_vault: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """rc>0 (genuine git failure) → cause=git_error in WARNING log.

    Red: current log line lacks the cause= tag. After T-1.3, every
    warning carries either cause=external_termination or cause=git_error
    to make operator triage one-grep-able.
    """
    from hive._helpers import _git_commit

    # Force a git failure: ask to commit a file that does not exist.
    caplog.set_level(logging.WARNING, logger="hive._helpers")
    bogus = git_vault / "does-not-exist.md"
    _git_commit(git_vault, [bogus.relative_to(git_vault)], "test")

    git_warnings = [
        rec.message
        for rec in caplog.records
        if rec.levelno >= logging.WARNING
        and ("git add" in rec.message or "git commit" in rec.message)
    ]
    assert git_warnings, "expected at least one git WARNING log"
    assert any("cause=git_error" in m for m in git_warnings), (
        f"expected 'cause=git_error' in one of: {git_warnings}"
    )
