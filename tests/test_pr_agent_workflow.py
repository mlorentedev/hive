"""The caller of the shared reviewer (TOOL-031 dotfiles#2893, CI-007 ci-workflows#33).

A tag or a branch here would let a change in ci-workflows reach this repository
without a commit in it, so the pin is held to a full SHA with its tag beside it.

The review lane this file used to pin (#503, CI-001) moved into the shared job:
the repository-wide `concurrency` group with `queue: max`, the bounded PR-Agent
step below the job limit, and the publish guard on `!cancelled()` are declared in
mlorentedev/ci-workflows' `.github/workflows/pr-agent.yml` and tested there. A
`uses:` job cannot declare steps or job-level concurrency, so they cannot be
asserted here. What the caller still owns is the workflow-level group that lets a
push supersede the older review of its own pull request, and that stays pinned.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pr-agent.yml"
PINNED_LINE = re.compile(
    r"uses: mlorentedev/ci-workflows/\.github/workflows/pr-agent\.yml"
    r"@[0-9a-f]{40} # v\d+\.\d+\.\d+$",
    re.MULTILINE,
)
# The shared job's lane group; the caller's group must never collapse into it.
LANE_GROUP = "pr-agent-review-${{ github.repository }}"


def _load() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{WORKFLOW} did not parse into a mapping"
    return loaded


def _job() -> dict[str, Any]:
    job = _load()["jobs"]["review"]
    assert isinstance(job, dict)
    return job


def test_the_shared_workflow_is_pinned_by_full_sha_and_tag() -> None:
    assert PINNED_LINE.search(WORKFLOW.read_text(encoding="utf-8"))


def test_the_pool_comes_from_the_dotf_pin() -> None:
    assert _job()["with"]["pool"] == "embedded"


def test_release_pull_requests_are_not_reviewed_again() -> None:
    assert _job()["with"]["skip_branch_prefixes"].split() == ["release-please--"]


def test_the_caller_passes_neither_dotf_nor_a_model_chain() -> None:
    declared = {"dotf_version", "declared_model", "declared_fallback_models"}
    assert not declared & set(_job()["with"])


def test_the_pin_is_at_least_the_tag_that_defaults_them() -> None:
    # v0.1.0 requires dotf_version and the chain. The tag is compared, not the
    # SHA, so a Dependabot bump keeps this green and a revert turns it red.
    match = PINNED_LINE.search(WORKFLOW.read_text(encoding="utf-8"))
    assert match
    tag = match.group(0).rsplit("# v", 1)[1]
    assert tuple(int(part) for part in tag.split(".")) >= (0, 2, 0)


def test_secrets_are_passed_by_name() -> None:
    assert set(_job()["secrets"]) == {"NAN_API_KEY", "PR_AGENT_ANTHROPIC_API_KEY"}


def test_supersession_stays_at_the_workflow_level() -> None:
    """A push must still cancel the older review of its own pull request."""
    workflow = _load()
    group = workflow["concurrency"]["group"]
    assert "github.event.pull_request.number" in group, (
        "the workflow group must be per pull request"
    )
    assert "github.event.issue.number" in group, "slash-command runs key on the issue number"
    assert group != LANE_GROUP, "the workflow group and the lane must not collapse into one key"
    assert workflow["concurrency"]["cancel-in-progress"] is True, (
        "supersession at enqueue time is what stops a stale review from holding the lane"
    )


def test_the_queue_is_not_beside_a_cancelling_group() -> None:
    """GitHub refuses `queue: max` in a group that cancels in progress."""
    assert "queue" not in _load()["concurrency"], (
        "`queue: max` beside `cancel-in-progress: true` is a validation error, not a no-op"
    )
