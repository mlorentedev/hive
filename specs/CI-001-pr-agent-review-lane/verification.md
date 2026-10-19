---
tags: [spec, verification]
created: "2026-10-18"
---

# Verification - CI-001-pr-agent-review-lane

> Evidence only: commands run and what they returned, in this session. AC4 is
> the one criterion that cannot be closed by a unit test; it is left open with
> the exact measurement that closes it, rather than marked done on intent.

## Baseline (before this change)

```
$ uv run pytest tests/test_pr_agent_workflow.py -q      # new file, against the old workflow
4 failed, 1 passed in 0.32s
```

The failures name the three absent pieces: no job-level lane
(`test_inference_runs_are_queued_not_raced_across_pull_requests`,
`test_the_queue_is_not_beside_a_cancelling_group`), no step bound
(`test_a_hung_attempt_releases_the_lane`), and `always()` on the guard
(`test_a_superseded_run_attests_nothing`). `test_supersession_stays_at_the_workflow_level`
already passed — the per-PR group existed, and this change must not remove it.

## AC1 — two concurrency scopes, lane not beside a cancelling group

```
$ uv run pytest tests/test_pr_agent_workflow.py -q
5 passed in 0.08s

$ uv run python -c "<parse the workflow and print the two scopes>"
workflow: True | per-PR key: True
job lane: {'group': 'pr-agent-review-${{ github.repository }}', 'cancel-in-progress': False, 'queue': 'max'}
```

Three tests hold it: the lane's group/`cancel-in-progress`/`queue` triple, the
workflow group still keyed on the pull-request number and the issue number, and
the assertion that `queue` appears in neither cancelling block.

**Behaviour evidence (probe, #503 comment):** four simultaneous runs with this
arrangement were `1 in_progress + 3 pending`, none discarded
(runs 39174829690, 39174829735, 39174829835, 39174829838), and a push to one ref
cancelled that ref's older run (39174829690 → `cancelled`), reproduced with and
without the job lane (control 39174957387).

## AC2 — the attempt is bounded below the job bound

```
$ python -c "<parse>"
bounds: job 15 step 12
```

`test_a_hung_attempt_releases_the_lane` asserts both keys exist and that the
step bound is strictly smaller, because a step bound at or above the job bound
never fires — the job limit cancels the step in flight, which is exactly how
each of the four recorded reds spent 15 minutes.

## AC3 — a superseded run attests nothing

```
guard if: !cancelled() && steps.credential.outcome != 'failure' && (github.event_name == ...
```

`test_a_superseded_run_attests_nothing` requires `!cancelled()` and rejects a
lingering `always()`. `!cancelled()` still runs the guard after a failed step,
so the credential and publish failures keep their diagnosis.

**Observed in production on this pull request**, which is the first one to run
the new config (a `pull_request` event runs the workflow from the PR's merge
ref, so the fix applied to its own review):

| run | planted | outcome |
|---|---|---|
| 39185684229 | first push | run `cancelled`; step 5 `PR-Agent` `cancelled`; step 6 guard **`skipped`** |
| 39186143313 | push 20 s after, superseding it | admitted to the lane 2 s later, `PR-Agent` `success`, guard `success`, Guide published |

Before this change the same shape ended `Fail if no review was published ->
failure` on every superseded run. It also shows the cost the proposal predicted:
the superseded attempt held the lane for 6 min 30 s before cancellation closed it.

## A correction about this record's own evidence

The first version of the `features.json` commands used `-k 'bound'` and `-k 'cancelled'`,
which select no test in this file. Re-run without a pipe, they exit **5** — pytest
refuses, and nothing could have been marked complete on them. An earlier note in this
PR said they exited `0`, which was the exit status of `tail`, not of pytest:
[[docs/lessons/lesson-094-a-verification-command-that-selects-zero-tests|lesson-094]] is
about exactly that observer error, and it caught this record making it. The claim was
corrected on the PR rather than left in place.

## AC4 — a burst of four real pull requests (OPEN)

Not claimed. The fix removes hive's self-inflicted collisions; it cannot remove
saturation caused by the pool's other consumers, so the only honest evidence is
a real burst:

1. Four open, non-draft pull requests created within a few minutes of each other.
2. `gh run list --workflow=pr-agent.yml --limit 8 --json databaseId,status,conclusion`
   shows one `in_progress` and the rest `pending`/`completed`, none `cancelled`
   for a shape other than supersession.
3. Each PR carries a `PR Reviewer Guide` comment authored by `github-actions[bot]`.
4. Classify any remaining `review` failure by **inspecting the failed step and the
   run's conclusion**, never by duration alone: the attempt bound is now 12
   minutes, the job bound 15. A step timeout at ~12 min is a hung attempt; a
   credential step failure, a missing marker or an API error is neither; and a
   `cancelled` conclusion means supersession. Only a step-level timeout is
   attributable to model-pool saturation.

The machine proxy in `features.json` (`group_by` over the hour bucket + no
`cancelled`/`failure` in the window) fails closed today — it returns `false`
against the current history, which still contains the four 15-minute hangs — and
it cannot see "a review was published per PR"; step 3 above covers that by hand.

## Suite gate

```
$ make check
1085 passed, 5 skipped, 56 deselected in 185.19s (0:03:05)
```

Baseline on `master` before this branch was 1080 passed; the five new tests are
this file. Lint and `mypy --strict src/` ran clean in the same invocation.

## Known limits, stated rather than hidden
- Cancellation is applied when the executing step closes, not instantly (probe:
  the superseded step ran its full 92 s). The lane therefore holds a superseded
  review for up to the 12-minute attempt bound.
- `queue:` at job level is accepted by GitHub but unknown to actionlint, and
  there is no actionlint in this repo's CI. The tests above are the whole gate.
- The retry path (a probe step, because PR-Agent exits 0 on a swallowed 429) is
  out of scope here and becomes the next change if AC4's burst still produces
  hangs.
