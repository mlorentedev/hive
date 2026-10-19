---
tags: [spec, tasks]
created: "2026-10-18"
---

# Tasks - CI-001-pr-agent-review-lane

> TDD order. One task = one focused commit. Tick as you go.
>
> **Inline markers**: `[P]` = safe to run in parallel with other unchecked tasks;
> `[AC<n>]` = maps to acceptance criterion `<n>` in `proposal.md`.

## Setup

- [x] Branch created from main: `feat/ci-pr-agent-review-lane`
- [x] `proposal.md` complete; acceptance criteria testable
- [x] No open questions left in "Risks / open questions"

## Implementation

- [ ] [P] [AC1] Write the failing invariant test: `tests/test_pr_agent_workflow.py`
      parses `.github/workflows/pr-agent.yml` and asserts the two concurrency
      scopes, the `queue: max` key and that `queue` is not beside a cancelling
      group (red: job-level group absent today)
- [ ] [AC1] Add the job-level lane (`group`, `cancel-in-progress: false`,
      `queue: max`) below the job's `timeout-minutes`, with the comment that says
      why the queue cannot live beside the cancelling group
- [ ] [P] [AC2] Write the failing bound test: step `timeout-minutes` present and
      strictly below the job bound (red: the step declares none)
- [ ] [AC2] Add `timeout-minutes: 12` to the `PR-Agent` step
- [ ] [P] [AC3] Write the failing guard test: the publish guard's `if:` contains
      `!cancelled()` (red: it uses `always()`)
- [ ] [AC3] Gate the guard on `!cancelled()` so a superseded run attests nothing
- [ ] Refactor: keep the new comments to the facts measured in #503, no prose
      duplication with the file's existing blocks

## Closing

- [ ] Every acceptance criterion is covered by at least one test
- [ ] Every acceptance criterion has a `features.json` entry with a non-vacuous
      verification command
- [ ] `make check` green (lint + mypy --strict + full suite)
- [ ] No unrelated changes in the diff
- [ ] `verification.md` filled with the command output that proves each AC
- [ ] PR opened referencing this spec folder

## Machine-readable features

See `features.json` beside this file. `state` stays `pending` until the harness
runs `verification` and captures exit 0.
