"""Invariants for the reviewer workflow's concurrency and guards (#503, CI-001).

Why these are tests and not prose: the arrangement below exists to stop this
repository from colliding with itself in a model pool it does not own (five
concurrent requests per model, shared with pi, qq, the sibling repositories'
review pools and hive's own delegate worker). Every symptom of getting it wrong
is silent. `review` is not a required check, there is no actionlint in CI, and a
run that never gets a runner publishes nothing — so a flattened group, or a
`queue:` key dropped from a job block, produces cancelled reviews that look
exactly like the saturation bug these lines fix.

Measured, not assumed: the two-scope arrangement with `queue: max` held four
simultaneous runs as one running plus three pending, none discarded, and a push
still cancelled the older run of its own ref (#503, probe runs 39174829690 and
its successors).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pr-agent.yml"

LANE_GROUP = "pr-agent-review-${{ github.repository }}"


def _load() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{WORKFLOW} did not parse into a mapping"
    return loaded


def _review_job() -> dict[str, Any]:
    job = _load()["jobs"]["review"]
    assert isinstance(job, dict)
    return job


def _step(name: str) -> dict[str, Any]:
    for step in _review_job()["steps"]:
        if step.get("name") == name:
            return step  # type: ignore[no-any-return]
    raise AssertionError(f"no step named {name!r} in the review job")


def test_inference_runs_are_queued_not_raced_across_pull_requests() -> None:
    """One review at a time per repository, and nothing discarded while waiting."""
    lane = _review_job()["concurrency"]
    assert lane["group"] == LANE_GROUP, (
        "the lane group must be keyed by repository: keying it per pull request "
        "is what lets four PRs spend the model pool's five slots at once"
    )
    assert lane["cancel-in-progress"] is False, "never cancel the review that is running"
    assert lane.get("queue") == "max", (
        "without `queue: max` a group keeps one running and one pending run, and a "
        "third request cancels the second before it ever gets a runner"
    )


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
    workflow = _load()
    assert "queue" not in workflow["concurrency"], (
        "`queue: max` beside `cancel-in-progress: true` is a validation error, not a no-op"
    )
    assert _review_job()["concurrency"]["cancel-in-progress"] is False


def test_a_hung_attempt_releases_the_lane() -> None:
    """The measured failure mode is a hang, so the attempt needs a bound of its own."""
    job_bound = _review_job()["timeout-minutes"]
    step_bound = _step("PR-Agent").get("timeout-minutes")
    assert step_bound is not None, "the PR-Agent step has no bound of its own"
    assert step_bound < job_bound, (
        "a step bound at or above the job bound never fires: the job limit cancels the "
        "step in flight, which is how four reds each spent fifteen minutes"
    )


def test_a_superseded_run_attests_nothing() -> None:
    """A cancelled run must not report a missing review; its successor owns the verdict."""
    guard = _step("Fail if no review was published")
    condition = " ".join(str(guard["if"]).split())
    assert "!cancelled()" in condition, (
        "the guard runs on `always()`, so a push-superseded run fails it and leaves a red "
        "`review` that is indistinguishable from infrastructure failure (#503)"
    )
    assert "always()" not in condition, "`!cancelled()` already covers failed steps; keep one gate"
