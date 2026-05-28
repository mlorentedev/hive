"""TDD Red — HIVE-116 AC-5: partial-state suffix on deadline.

Spec: ``specs/HIVE-116-stale-lock-after-deadline/proposal.md`` AC-5.

After T-1.4:

- ``hive._vault_write._PARTIAL_STATE_SUFFIX`` exists with the user-confirmed
  wording (2026-05-27) — see ``specs/.../tasks.md`` T-0.2.
- ``_commit_status_suffix`` accepts a ``deadline_killed: bool = False`` kwarg
  and returns ``_PARTIAL_STATE_SUFFIX`` when truthy (overrides
  requested_commit / deferred branches).

Both assertions fail today: constant missing, kwarg missing.
"""

from __future__ import annotations

import pytest

# ── AC-5 — constant exists with the locked wording ───────────────────────


def test_partial_state_suffix_constant_locked() -> None:
    """The literal wording is the public contract; pattern-match key must hold."""
    from hive._vault_write import _PARTIAL_STATE_SUFFIX

    # Pattern-match key per proposal.md R3 resolution: downstream agents
    # key off this substring. Wording rotations may happen later, but this
    # prefix MUST remain stable across minor versions.
    assert "partial state — disk write succeeded" in _PARTIAL_STATE_SUFFIX
    # Action-oriented imperative per user decision 2026-05-27.
    assert "verify with vault_query" in _PARTIAL_STATE_SUFFIX
    # Leading space matches sibling _DEFERRED_SUFFIX / _UNCOMMITTED_SUFFIX.
    assert _PARTIAL_STATE_SUFFIX.startswith(" (")
    assert _PARTIAL_STATE_SUFFIX.endswith(")")


# ── AC-5 — _commit_status_suffix routes deadline_killed correctly ────────


@pytest.mark.parametrize(
    ("requested_commit", "deferred", "deadline_killed", "expected_substring"),
    [
        (True, False, False, ""),  # committed inline — no suffix
        (True, True, False, "deferred to obsidian-git"),
        (False, False, False, "uncommitted"),
        # The new branch: deadline_killed dominates regardless of requested/deferred
        (True, False, True, "partial state — disk write succeeded"),
        (False, False, True, "partial state — disk write succeeded"),
        (True, True, True, "partial state — disk write succeeded"),
    ],
)
def test_commit_status_suffix_routes_deadline_killed(
    requested_commit: bool,
    deferred: bool,
    deadline_killed: bool,
    expected_substring: str,
) -> None:
    """Deadline-killed branch overrides the requested/deferred matrix.

    Today's _commit_status_suffix has signature (requested_commit, deferred).
    Red: calling with kwarg ``deadline_killed=...`` raises TypeError.
    """
    from hive._vault_write import _commit_status_suffix

    result = _commit_status_suffix(
        requested_commit=requested_commit,
        deferred=deferred,
        deadline_killed=deadline_killed,
    )
    if expected_substring == "":
        assert result == "", f"expected empty suffix, got {result!r}"
    else:
        assert expected_substring in result, (
            f"expected {expected_substring!r} in {result!r}"
        )
