---
tags: [spec, verification, templates]
created: "2026-08-07"
---

# Verification - HIVE-322-commit-outbox

## Evidence

Every acceptance criterion maps to a named test. The machine-readable form, with the exact invocation per criterion, is the sibling `features.json`; this section adds the part a command cannot carry — whether the test would actually have caught the criterion failing.

Six criteria held **by construction** and their deliverable is the guard, not a fix. Those are called out explicitly, because a tick that hides "nothing had to change" is the kind of tick that lets a later refactor quietly break an invariant nobody re-checked. Each was neutered to prove the test discriminates.

| AC | Test | Non-vacuity |
|---|---|---|
| AC1 | `test_vault_write_deferred_does_not_commit_in_its_call_path` | Red before `_commit_or_queue` |
| AC2 | `test_one_tick_produces_one_commit_with_exactly_the_queued_paths` | Red before `CommitQueue` |
| AC3 | `test_commit_count_is_bounded_by_the_tick_not_the_write_count` (in-process), `test_commit_count_is_bounded_per_process_in_the_separate_process_regime` (`cross_worker`) | Reverting the writes to `commit=True` reproduces the pre-ADR-018 world and fails both: 200 commits against a bound of 13, and 180 against 45 |
| AC4 | `test_flush_exceeding_deadline_is_terminated` | Red before the watchdog |
| AC5 | `test_vault_health_reports_queue_depth_and_last_flush_age` | Red before the runtime block |
| AC6 | `test_server_lifespan_shutdown_drains_the_queue` | Red before the lifespan drain |
| AC7 | `test_reconciler_never_stages_a_file_it_did_not_queue` | Covers both an untracked foreign file and a modification to a tracked one; the latter is what a `git add -A` refactor would slip past a weaker test |
| AC8 | `test_queue_dedups_at_enqueue_not_at_drain` + two siblings | Dedup at `drain()` satisfies "appears once in the commit" while leaving AC5's depth dishonest — the unit test is what separates them |
| **AC9** | `test_startup_self_heal_reports_uncommitted_paths_and_never_commits` | **Held by construction.** Failed only on the *report* assertion. Injecting an `add -A` + commit into `_startup_self_heal` fails it on *"startup moved HEAD"* |
| **AC10** | `test_vault_commit_still_sweeps_foreign_working_tree_edits` | **Held by construction.** Two neuters, because the assertions fail differently: narrowing the sweep to hive-written paths fails on *"vault_commit did not commit"*; `add --update` commits happily and fails on the dropped untracked file |
| AC11 | `test_vault_health_reports_uncommitted_count_and_oldest_age`, `test_vault_health_reports_unknown_rather_than_clean_when_git_fails` | Returning `(0, None)` on enumeration failure — the tempting simplification — fails on *"a failed enumeration reported as clean"* |
| AC12 | `test_default_write_defers_and_commit_true_is_the_escape_hatch` + patch/delete siblings | Red before the default flip |
| **AC13** | `test_reconciler_commit_happens_under_the_git_filelock` | **Held by construction** (`_git_commit` already wraps both Popens). Asserted from a **foreign thread**: `filelock` builds the per-vault singleton `thread_local=True`, so same-thread re-entry is free and holding on the flushing thread would prove nothing. Neutering the filelock fails it on *"flush completed while the filelock was held"*, **not** on the commit count — the in-process `_GIT_LOCK` sails through, which is exactly why it cannot substitute for the cross-process bound |
| AC14 | `test_failed_flush_drops_its_paths_and_does_not_requeue` | Red before the drop-on-failure branch |
| (extra) | `test_tick_defers_to_a_healthy_external_committer_and_commits_otherwise`, `test_tick_commits_when_the_defer_predicate_cannot_be_evaluated` | Not an AC, but a contract the default flip would otherwise have defeated silently. Deferring on an unevaluable predicate fails the fallback test; dropping the `last_flush_at` stamp fails on `last_flush_at is None` |

**One verification command is load-bearing in its exact form.** AC3's separate-processes half is marked `cross_worker`, which `pyproject.toml`'s `addopts` excludes from the default run, and the required CI contexts invoke `tests/test_cross_worker_lock.py` only. Its `features.json` command therefore names `-m cross_worker` explicitly; drop that flag and the criterion silently verifies nothing. Keeping it out of the required contexts is deliberate — `cross_worker_lock` is a merge gate, and a benchmark that spawns interpreters should not be able to block every merge.

## Baseline (pre-implementation)

Measured 2026-08-06 on a 1300-file synthetic vault, 5 writes per worker, commit counts asserted after each run (`_git_commit` swallows failures by design, so a skipped commit would otherwise register as a fast sample).

Multi-process (per-session stdio servers, no daemon):

| procs | p50 | p95 | max | writes/s |
|---:|---:|---:|---:|---:|
| 1 | 25.1 ms | 35.0 ms | 35.0 ms | 35.8 |
| 5 | 24.6 ms | 485.8 ms | 637.0 ms | 33.2 |
| 10 | 23.5 ms | 1076.0 ms | 1389.6 ms | 33.1 |
| 12 | 25.5 ms | 1442.0 ms | 1745.7 ms | 31.5 |

Threads in one process (the daemon regime this spec targets):

| threads | p50 | p95 | max | writes/s |
|---:|---:|---:|---:|---:|
| 1 | 27.9 ms | 33.1 ms | 33.1 ms | 35.0 |
| 5 | 148.3 ms | 233.9 ms | 237.4 ms | 28.7 |
| 10 | 317.3 ms | 391.3 ms | 417.5 ms | 30.0 |
| 12 | 370.2 ms | 417.6 ms | 446.1 ms | 31.2 |

Throughput is flat at ~30-33 writes/s in both regimes: the daemon fixes tail *fairness* (max 1390 ms -> 418 ms at 10 writers) but not the ceiling, because a git commit against one repo is serial. This is the number the spec must move.

Reproduce with `spike/commit_contention.py` (see `spike/README.md`). The tables above are **one observed run**; absolute throughput varies by tens of percent between runs, so compare shapes and ratios rather than individual cells. The spike asserts the two things that hold regardless of noise: every expected commit actually landed, and throughput is sublinear in writer count (~1.5x observed for 12x the writers).

Coalescing raises the ceiling to ~85-100 writes/s and **holds it across the ladder** — it hits the serialized commit less often without removing the serialization. An earlier single-run reading suggested coalesced throughput degraded with N; repeated runs do not support that, and the claim is withdrawn.

## Result (post-implementation)

The baseline's question was never "how fast is a commit" — it was "how many commits does a write burst have to pay for". AC3 answers that directly, in both regimes:

| regime | writes | commits | elapsed | bound | writes per commit |
|---|---:|---:|---:|---:|---:|
| 10 writers, one process | 200 | **1** | 0.21 s | 3 | 200.0 |
| 3 separate processes | 180 | **3** | 2.13 s | 21 | 60.0 |
| *(neuter)* same, `commit=True` | 200 | 200 | 5.15 s | 13 | 1.0 |
| *(neuter)* same, per-write commit in each process | 180 | 180 | 6.17 s | 45 | 1.0 |

Both bounds are `ceil(elapsed/tick) + 2` per process, computed from **measured** elapsed rather than a fixed budget — a slow machine inflates `elapsed` and so only loosens the bound, which is what keeps these from being flaky timing gates. Both stable over five consecutive runs (1/3 and 3/21 every time).

The separate-processes row is the measurement that substantiates dropping the daemon-only scoping: three processes produced exactly one commit each, which is the duty-cycle arithmetic ADR-018 §Decision predicts, and the filelock serialized them without merging them.

## Test status

- `make check` (lint + `mypy --strict` + pytest with coverage): **861 passed, 2 skipped, 63 deselected**, coverage 85%. Log captured this session at `final-check.log`.
- `uv run pytest tests/test_cross_worker_lock.py -m cross_worker`: **5 passed** — re-verified because this change edits the `cross_worker` marker's description and that job is a required context.
- `uv run pytest tests/test_commit_queue_multiprocess.py -m cross_worker`: **1 passed** (excluded from the default run by design; see the note under Evidence).
- Every `features.json` verification command executed end to end: **15/15 exit 0**. Run as a batch rather than eyeballed, because a command naming a nonexistent test would exit 4 and a criterion could otherwise ship verifying nothing.
- `cd site && npm run build`: **31 pages, clean**; both new anchors confirmed present in the built HTML.
- No regressions: the baseline at `36e0818` was 849 passed / 2 skipped; the delta is entirely new tests.

## Decisions made during implementation

- **The uncommitted-path enumerator was built once and spent on AC9 and AC11 together.** Three of its choices are load-bearing rather than stylistic. `--no-optional-locks`, because a plain `git status` refreshes the index and takes `.git/index.lock` — at daemon boot the *reporter* could recreate the very lock `_startup_self_heal` exists to clear. `-z` with explicit consumption of a rename's source field, because porcelain quotes special characters and an unconsumed source path skews every later entry instead of failing loudly. And `None` on failure rather than an empty list, because with ADR-018 §3 refusing to self-heal this report is the *entire* recovery signal, so "clean" and "git could not answer" must not collapse into one number.
- **`_clear_stale_index_lock` was split out of `_startup_self_heal`.** The lock-clearing path is a chain of early `return`s, so appending the report to it would have skipped exactly the cases — a live or unclearable lock — where knowing the backlog matters most.
- **The uncommitted report does not subtract the commit queue.** Mid-tick the two overlap; that is truthful, since a queued path really is uncommitted on disk, and netting them off would reintroduce the provenance reasoning §3 exists to remove. It also keeps the enumerator free of any `ServerContext`, which is what lets daemon startup call it before one exists.
- **The external-committer predicate is evaluated at drain time, and an unevaluable predicate commits rather than defers.** Deferring would hand the paths to a committer nobody confirmed exists, and AC9 leaves no second chance to notice. A hand-off still stamps `last_flush_at`: deciding not to commit is work, and a reconciler cooperating with obsidian-git must not read as a stalled one.
- **AC3's multi-process benchmark stays out of the required CI contexts** (see Evidence). Its evidence runs through the `features.json` command instead.
- **Docs scope grew past the ACK-semantics section on purpose.** Four pages still asserted the old default in plain terms — `reference/architecture.md` said "By default, all vault writes auto-commit to git", and `configuration.mdx` step 3 still instructed callers to pass `commit=False` everywhere. A documented contradiction is worse than a doc that has not caught up.

## Defects found and their disposition

- **`vault_delete`'s `commit=False` is the removed indefinite-deferral mode.** The site docs claimed it carried "the same durability contract as `vault_write`"; it does not, because `vault_delete` never routes through `_commit_or_queue`. The false claim was corrected in the docs and the code was left alone: ADR-018 §4 says both "`vault_delete` opts out of the queue entirely" and "the indefinite-deferral mode is removed", and delete's `commit=False` sits exactly where those two readings disagree. Resolving that is a design change, which the freeze puts behind an ADR amendment. **Ticketed:** [#353](https://github.com/mlorentedev/hive/issues/353).
- **The `cross_worker` marker description was inaccurate** once a second file joined it ("requires fake-git PATH fixture" is true of only the HIVE-116 lock tests). Fixed in scope, one line in `pyproject.toml`. It is the only diff line outside this feature's obvious footprint and is called out here so the "no unrelated changes" review has something to check against.

### Found during the pre-archive adversarial review (2026-08-09)

The review ran after the merge, against the published 2.0.0 artifact rather than the checkout. Three findings; the first is fixed, the other two are open.

- **`HIVE_COMMIT_TICK_S` above the 300s ceiling silently started no reconciler at all.** The knob is public and documented with its 5s default, but `_MAX_AUTO_TICK_S` was named nowhere and `CommitReconciler.__init__` had no `else` branch, so a plausible "commit every 10 minutes" left queued paths committing only on clean shutdown or an explicit `vault_commit`. The ceiling is deliberate — nine tests here pass `tick_s=3600.0` so no background thread races an explicit `flush_now()` — and that test affordance is exactly what kept the production cliff invisible: all nine depend on the branch and none asserted it. **Fixed in [#355](https://github.com/mlorentedev/hive/pull/355)** (warning log, a test asserting the threshold in both directions, EN + ES docs).
- **A vault that is not a git repository is accepted in silence.** Verified against 2.0.0: startup emits no warning, and `vault_write` answers `(queued — commits on the next reconciler tick)` — a promise nothing can keep. Each tick logs `git add failed … fatal: not a git repository` then `flush_failed dropped=1`, but none of it reaches the caller unless they pass `include_runtime=True`. The contrast is the point: a *nonexistent* vault path gets two startup warnings and an actionable message naming `HIVE_VAULT_PATH`, its alias and the client `env:` block. **Open — needs a ticket.**
- **`_git_filelock` fabricates a `.git/` directory in a non-git vault.** The lock lives at `vault/.git/hive.lock`, so the directory is created as a side effect: verified absent before startup and present afterwards containing only `['hive.lock']`. What remains is a `.git` that is not a repository, which misleads both a human and any tool that tests for `.git` presence. **Open — needs a ticket.**

What the same probes confirmed working, recorded so it is not re-litigated: spaces and non-ASCII in the vault path, a symlinked vault, a read-only project directory (`Cannot create …: permission denied`, no crash), and — the one that matters most for ADR-018 §3 — `uncommitted.count` reporting `null` rather than `0` when git cannot answer. That design decision holds in the shipped artifact.

An A/B load test on the published package, same harness both modes, 120 writes per cell at the default 5s tick: deferred runs 693–1070 writes/s against 35–37 for `commit=True`, p50 at 12 writers 13.2 ms against 319.2 ms, and 480 deferred writes produced 2 commits against 480. The synchronous ladder is flat at ~37 writes/s regardless of writer count, reproducing the baseline shape this spec set out to move.

## Promotion candidates

- [x] Lesson for the repo's `docs/lessons.md`? **Two.** (1) `--no-optional-locks` is what makes a `git status` genuinely read-only — without it a read-only telemetry path takes `index.lock` and contends with writers, which is a trap for anyone adding git-backed metrics. (2) The flake-proof benchmark shape: bound a *count*, derive the bound from *measured* elapsed rather than a fixed budget, and add a `bound < load` precondition so a later shrink of the workload fails loudly instead of passing vacuously.
- [x] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? yes — ADR-018 is gating, authored before implementation rather than promoted after
- [ ] New pattern candidate for `00_meta/patterns/`? Not yet — the benchmark shape above is a plausible cross-project pattern, but it has been used in one project, and the bar is >1

## Archive checklist

- [x] `proposal.md` frontmatter set to `status: archived` — AC1-AC14 ticked at the same time; every one is evidenced in the table above, and leaving them blank would have archived a spec that understates itself
- [x] Folder moved: `specs/HIVE-322-commit-outbox/` -> `specs/archive/HIVE-322-commit-outbox/`
- [x] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018) — [#322](https://github.com/mlorentedev/hive/issues/322) closed 2026-08-09T03:03:32Z by the [#354](https://github.com/mlorentedev/hive/pull/354) merge, which the board workflow moves to Done automatically
- [x] Promotions above executed (if any) — verified already landed rather than re-created: both lessons are in `docs/lessons.md` (the `--no-optional-locks` one and the flake-proof benchmark shape), and ADR-018 exists at `docs/adr/adr-018-asynchronous-commit-queue.md`. The pattern candidate stays declined at one project

## Adversarial review verdict

**PASS WITH GAPS** (2026-08-09), run from a different session than the one that implemented the change. No blockers. All 18 tests named in the evidence table were confirmed to exist by name rather than by suite. The two load-bearing invariants were re-derived from the code rather than trusted: AC7 holds because the only `git add -A` in `src/` is reachable from `vault_commit` alone, and AC13 because `_git_commit` wraps both Popens in `_git_filelock`. Rubric: Verification A, Maintainability A, Handoff-readiness A, Correctness B, Scope B, Reliability B. The gaps are the three findings recorded above.
