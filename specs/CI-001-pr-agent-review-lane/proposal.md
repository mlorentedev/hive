---
id: "CI-001-pr-agent-review-lane"
type: spec
status: draft # draft | implementing | verifying | archived
created: "2026-10-18"
issue: "mlorentedev/hive#503"   # repo#NNN — GitHub issue / Project item that tracks this spec
tags: [spec, proposal, ci, reviewer]
template_version: "1.0"
---

# CI-001-pr-agent-review-lane

> **Naming**: file lives at `<repo>/specs/CI-001-pr-agent-review-lane/proposal.md`. `CI-001-pr-agent-review-lane` is `AREA-NNN-slug` (e.g. `TOOL-001-secret-dedup`).

## Why

<!-- from issue #503: PR-Agent review check goes red from reviewer infrastructure and blocks the merge condition -->

`pr-agent.yml` groups runs per pull request, so four simultaneous PRs run four
reviews against a NaN model pool whose limit is five concurrent requests per
model and which is shared with this account's other consumers — pi, qq, the
sibling repositories' adversarial-review pools and hive's own delegate worker.
The measured outcome is not a rate-limit error but a hang: four runs in the
history reach the job's 15-minute bound with the `PR-Agent` step cancelled, the
publish guard then fails, and a red `review` check blocks the owner's merge
condition. Every red run also produces no review at all, so the second reviewer
this repository added to stop merging unread (the reason the workflow exists)
delivers nothing precisely when PRs are busiest. Hive cannot widen the pool; the
only lever it owns is to stop colliding with itself.

The probe recorded on #503 adds a second, independent population of red checks:
a run superseded by an ordinary push also ends with the guard failing, because
the guard reports "no review was published" for a run that was told to stop. Four
of the nine cancelled runs in the last 60 are that shape, so the red check today
does not distinguish infrastructure from supersession and "Done when" cannot be
measured until it does.

## What

After this change a burst of simultaneous pull requests is reviewed one at a
time across the repository instead of racing itself: a run that cannot get the
lane waits as pending rather than being discarded, and a push still supersedes
the older review of its own PR at enqueue time. The attempt itself is bounded,
so a hung review releases the lane instead of holding it for the full job bound.
A run cancelled by supersession no longer reports a missing review — the guard
stays silent for it, because the successor run owns that verdict — which makes
the remaining reds attributable to a single cause. A test pins both concurrency
scopes and the `queue: max` key, so the arrangement cannot be flattened back to
per-PR parallelism, or lose the queue key, without a red suite.

## Out of scope

- **Retry with backoff.** PR-Agent exits `0` on a swallowed 429, so a retry
  cannot be gated on its exit status; it needs the publish check extracted into a
  probe step first. Deferred until the lane is measured on a real burst.
- **Neutral "reviewer unavailable" status.** A job cannot conclude `neutral`:
  it requires a check-run published through the API with `checks: write`, and it
  removes the red `review` check rather than adding a state to it. Contradicts
  #1107 unless the check is read, and `review` is not a required check.
- **The draft gate reading a payload snapshot** (`dotfiles` #2591): a preceding
  bug, unaffected by the lane, tracked separately.
- **`reviewable` skip for a pull request merged or closed while queued**: real
  with a serialized lane, but it needs its own state query step.

## Risks / open questions

- **The lane does not remove external saturation.** A single serialized review
  still needs one free slot in a pool shared with other consumers, so reds can
  persist through no fault of this workflow. The verification therefore measures
  *attribution* (no self-inflicted collisions, no supersession reds), not the
  absence of red. If hangs remain, the probe-step retry becomes the next change.
- **Cancelling is not instantaneous.** Measured on the probe: the superseded
  run's step continued to its natural end (~92 s) before the run concluded
  `cancelled`. The lane therefore holds a superseded review for up to the
  attempt bound; `timeout-minutes: 12` on the step caps that cost.
- **Queue starvation under repeated pushes.** `queue: max` holds up to 100
  pending runs, and a superseded run is cancelled at enqueue, so a push-heavy PR
  cannot crowd out others; a pathological burst beyond 100 drops runs, which the
  guard would surface as a missing review rather than silence.
- **`queue:` at job level is accepted but undocumented by actionlint.** The
  arrangement is the one `dotfiles` has run since 2026-10-01 and `kubelab` pins
  with tests; the probe reproduced it in this repository. There is no actionlint
  in CI, so the test below is the only gate.

## Acceptance criteria

- [ ] AC1: Concurrency is declared in two scopes inside `pr-agent.yml` — the
      workflow-level group keyed per pull request/issue with
      `cancel-in-progress: true`, and a job-level group keyed by repository with
      `cancel-in-progress: false` and `queue: max` — and a test fails if either
      scope is removed, merged into the other, or loses the `queue` key.
- [ ] AC2: The `PR-Agent` step declares a timeout shorter than the job bound, so
      a hung attempt releases the lane before the job's own limit (test asserts
      step bound < job bound, both present).
- [ ] AC3: The publish guard does not fail a cancelled run: a test asserts the
      guard's condition contains a `!cancelled()` term, and a cancelled run
      leaves no red `review` behind.
- [ ] AC4: A burst of four real pull requests each receive a review, with the
      runs observed as one running plus three pending and none discarded, and
      no reds attributable to supersession.

## References

- Bitácora board: https://github.com/mlorentedev/hive/issues/503 (probe evidence in the issue thread)
- Same arrangement, run in production: `dotfiles/.github/workflows/pr-agent.yml` (job-level lane, step bound, probe step)
- Invariant pinned by tests: `kubelab/tests/test_pr_agent_workflow.py`
- Doctrine this preserves: #1107 ("a review that did not happen must not report success")
- Related ADR: `docs/adr/` has no CI-review decision; the merge-condition rule lives in the dotfiles AGENTS.md §1
