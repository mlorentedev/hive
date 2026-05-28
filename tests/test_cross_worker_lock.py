"""TDD Red — HIVE-116 AC-7: cross-worker filelock eviction.

Spec: ``specs/HIVE-116-stale-lock-after-deadline/proposal.md`` AC-7.

When worker A hits a ``bounded_call`` deadline on a hung git subprocess,
worker B's next ``vault_patch`` against the same vault must complete
within ``deadline_s + grace_s + HIVE_POST_KILL_DRAIN_S + slack=5s``.

This is the integration test that proves the post-kill filelock eviction
actually unblocks sibling workers — the headline contract of HIVE-116 PR-2.

**Skipped during Red phase.** Promoted to runnable in T-2.7 once
``evict_filelock`` is wired into the supervisor and ``HIVE_POST_KILL_DRAIN_S``
is honored. The eventual budget on the wall-clock assertion is the only
sharp edge: must be tight enough that a regression (eviction not firing)
falls outside the budget.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.cross_worker


@pytest.mark.skip(
    reason="HIVE-116 T-2.x not yet green; promoted to runnable in T-2.7",
)
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only first pass; Windows variant lands in T-3.2",
)
@pytest.mark.asyncio
async def test_evict_filelock_on_deadline(git_vault: Path) -> None:
    """Worker B's vault_patch unblocks within budget after worker A's kill.

    Setup:
      1. Worker A spawns ``vault_patch`` against ``git_vault / file.md`` with
         a fake-git on PATH that ``sleep 120`` on the ``add`` subcommand.
      2. Worker A's ``bounded_call`` fires at deadline=2s, supervisor SIGTERMs.
      3. After ``HIVE_POST_KILL_DRAIN_S`` (default 5s), supervisor evicts the
         cached ``FileLock`` for ``git_vault`` from ``_GIT_FILELOCKS``.
      4. Worker B issues ``vault_patch`` against ``git_vault / other.md``.
      5. Worker B's call must complete within ``2 + 2 + 5 + 5 = 14s`` wall.

    Currently fails because ``evict_filelock`` does not exist.
    """
    from hive._helpers import _GIT_FILELOCKS, evict_filelock  # noqa: F401

    # Placeholder body — actual harness lands in T-2.7. The import above
    # is the Red-phase signal: collection fails today because
    # `evict_filelock` is not yet defined.
    raise NotImplementedError(
        "T-0.3 placeholder; full harness implemented in T-2.7",
    )
