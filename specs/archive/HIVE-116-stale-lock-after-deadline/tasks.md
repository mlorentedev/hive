---
id: "HIVE-116-tasks"
type: spec-tasks
status: draft
created: "2026-05-27"
tags: [spec, tasks]
template_version: "1.0"
---

# HIVE-116 — Tasks

> TDD ordering: red (test first), green (implementation), refactor.
> Each task lists its target file(s), AC mapping, and a one-line Done-when.
> Phase boundaries are PR boundaries — each phase lands as one Conventional Commit PR + one release-please bump.

## Phase 0 — Spec & TDD Red (no implementation)

- **T-0.1** Draft proposal.md / tasks.md / verification.md, this spec. Already done if you are reading this. AC: structural only.
- **T-0.2** **[USER-DECIDED 2026-05-27]** Partial-state response contract locked. Add this exact constant to `src/hive/_vault_write.py` next to `_DEFERRED_SUFFIX` / `_UNCOMMITTED_SUFFIX`:
  ```python
  _PARTIAL_STATE_SUFFIX = (
      " (partial state — disk write succeeded, "
      "git commit killed by deadline; verify with vault_query "
      "before retrying)"
  )
  ```
  String-only, no JSON-shape extension. Downstream pattern-match key: literal substring `"partial state — disk write succeeded"`. Unblocks T-1.4. AC-5 wording.
- **T-0.3** Write failing test `tests/test_cross_worker_lock.py::test_evict_filelock_on_deadline` with the desired green behaviour: 2 workers, fake-hanging git, worker B unblocks within budget. Mark `@pytest.mark.cross_worker` + `@pytest.mark.skip(reason="HIVE-116 T-2.x not yet green")`. AC-7 prep.
- **T-0.4** Write failing test `tests/test_run_git.py::test_external_termination_synthetic_stderr` — spawn `subprocess.Popen(["sleep","60"])`, terminate from outside, assert `_run_git` (or its testable helper) returns the synthetic stderr shape. Currently fails because the synthetic-stderr branch does not exist. AC-3 prep.
- **T-0.5** Write failing test `tests/test_vault_write.py::test_partial_state_suffix_on_deadline` — mock `_git_commit` to raise `TimeoutError` after the FS write lands, assert `vault_write` response carries the `_PARTIAL_STATE_SUFFIX` (placeholder until T-0.2 sets the wording). AC-5 prep.
- **T-0.6** Run `make test` — every new test from T-0.3..T-0.5 must FAIL for the expected reason (not collection error, not import error). Capture failure output as evidence of Red phase. **Done when:** `pytest -k "evict_filelock or external_termination or partial_state_suffix" --no-header -q` shows 3 fails / 0 passes / 0 errors.

## Phase 1 — Quick Wins (PR-1, target v1.20.0, ~120 LOC)

Scope: log enrichment + partial-state suffix. No supervisor changes, no filelock eviction. Lands first because it's the user-facing observability fix and unblocks the second-session repro debugging.

- **T-1.1** Add `RC_EXTERNAL_TERMINATION = -1` named constant near top of `src/hive/_helpers.py`. Replace the magic `-1` in `_run_git` (line 981) with the constant. Mechanical. AC-3 prep.
- **T-1.2** In `_run_git`, when `proc.communicate()` raises `BrokenPipeError`/`OSError`, build the synthetic stderr `"[external_termination] killed by supervisor at <ts>; original stderr: <empty|<N> bytes>"` and return it as the stderr text. Preserve original stderr bytes when present. **File:** `src/hive/_helpers.py:960-989`. AC-3.
- **T-1.3** Update both warning logs in `_git_commit` (lines 1039-1049) to include `cause=external_termination` when `rc == RC_EXTERNAL_TERMINATION`, `cause=git_error` otherwise. Same change in `_git_commit_all` (lines 1091-1099) — three Popens, three warning sites. **File:** `src/hive/_helpers.py`. AC-4.
- **T-1.4** Add the user-decided `_PARTIAL_STATE_SUFFIX` constant to `src/hive/_vault_write.py` (from T-0.2). Wire it into `_commit_status_suffix` as a fourth branch: when the caller passes a new `deadline_killed: bool = False` flag, return the partial-state suffix regardless of `requested_commit` / `deferred`. **File:** `src/hive/_vault_write.py:60-76`. AC-5.
- **T-1.5** Thread the `deadline_killed` flag through `_git_commit` → returns a tuple `(committed: bool, deadline_killed: bool)` instead of returning `None`. Three callers in `_vault_write.py` need to capture the tuple. Backward compatibility: not a public function, no shim needed. AC-5.
- **T-1.6** Add a per-tool partial-state hook to `run_sync_tool`. Signature: `partial_state_hook: Callable[[str], str] | None = None`. For `vault_write` and `vault_patch`, pass a hook that, on `TimeoutError`, checks `_git_commit`'s last-known partial-state record via a thread-local and returns the partial-state message instead of the generic "timed out" string. For all other tools, hook is `None`, fallback behavior unchanged. **File:** `src/hive/_helpers.py:857-883`. AC-6.
- **T-1.7** Make T-0.4 and T-0.5 turn green. Verify T-0.3 still fails (gated on Phase 2). **Done when:** `pytest -k "external_termination or partial_state_suffix"` passes; cross-worker test still skipped.
- **T-1.8** Update EN+ES docs site: new section under `docs/troubleshooting.md` (and ES mirror) titled "Partial-state writes after deadline". Include the new suffix wording verbatim + git status recovery recipe. AC-11.
- **T-1.9** Conventional Commit PR. Title: `feat(hive): observable partial-state writes + structured subprocess termination logs (HIVE-116 PR-1)`. Body references issue #141. Triggers release-please for v1.20.0. AC-13.

## Phase 2 — Root cause: filelock eviction on deadline (PR-2, target v1.21.0, ~150 LOC)

Scope: extend `bounded_call` and `tool_span` to evict the cached filelock after the post-kill drain window. Adds the new env var. Adds the new persisted counter. Lands the cross-worker test as the green proof.

- **T-2.1** Add `HIVE_POST_KILL_DRAIN_S` to `HiveSettings` (`src/hive/config.py`). **Default 5.0 (user-confirmed 2026-05-27)**, pydantic validator constrains to [0.5, 30.0]. Test in `tests/test_config.py`: valid + invalid values. AC-2.
- **T-2.2** Add `evict_filelock(vault_path: Path) -> bool` helper to `src/hive/_helpers.py`. Returns True if eviction happened, False if nothing was cached. Pops from `_GIT_FILELOCKS` under `_GIT_FILELOCKS_GUARD`. Idempotent. AC-1 helper.
- **T-2.3** In `_deadline.bounded_call` post-kill branch (after `_terminate_registry`, before `_cleanup_index_lock`), add `await asyncio.sleep(settings.post_kill_drain_s)` then call `evict_filelock(vault_for_index_cleanup)` if any PID was killed. Same insertion in `tool_span` so both supervisors share the eviction step. **Files:** `src/hive/_deadline.py:235-253`, `src/hive/_helpers.py:826-849`. AC-1.
- **T-2.4** New persistent counter `LockEvictionTracker` in `src/hive/_lock_eviction.py` extending `_SqliteTracker`. DB at `~/.local/share/hive/lock_evictions.db`. Schema: `(iso_ts TEXT PRIMARY KEY, vault_path TEXT, killed_pids_json TEXT)`. Methods: `record(vault, killed_pids)`, `count_last_30d()`, `last_iso()`. AC-8.
- **T-2.5** Wire `LockEvictionTracker` into `ServerContext` and instantiate in `create_server()` (mirrors `RelevanceTracker`, `LessonReinforcementTracker`). Call `record()` from `bounded_call` immediately after `evict_filelock` returns True. AC-8.
- **T-2.6** Extend `_vault_health.runtime_block_text` to surface `lock_eviction_count_30d` + `last_lock_eviction_iso`. Test: trigger eviction via cross-worker harness, assert health block reports the count++. AC-8.
- **T-2.7** Promote T-0.3 from skip → run. Implement the fake-hanging-git fixture in `tests/conftest.py`: a script at `tmp_path / "fake-git"` that sleeps 120s when invoked with `add`, returns 0 otherwise. Prepend tmp_path to PATH for the test. Assert worker B's `vault_patch` completes within `deadline_s + grace_s + drain_s + slack=5s`. AC-7.
- **T-2.8** Calibration sub-task: run T-0.3 with `HIVE_POST_KILL_DRAIN_S ∈ {1, 5, 10}` × `runs=20`. Record `unblocked_within_ms.p99` per drain value. Pick the smallest drain whose p99 < 35s. Document the chosen default in `_deadline.py` docstring + the new ADR. AC-2 / R2.
- **T-2.9** Add `mcp.lock_eviction.race` WARNING log: 1s after eviction, if the evicted FileLock object reports `is_locked == True`, emit the warning + bump a new `vault_health.runtime.lock_eviction_race_count` field. Test: race the eviction by gripping a held lock and calling `evict_filelock`; assert log + counter. AC-1 / R1.
- **T-2.10** Draft ADR-012 in vault: `30-architecture/adr-012-cooperative-filelock-eviction-on-deadline.md`. Cover: why eviction is needed (R1 worker-thread cannot be preempted), why post-kill-drain not pre-kill (avoids racing the supervisor's own SIGTERM grace), why a per-process singleton cache stays (perf — only the affected vault's entry is evicted). Cross-link to lesson + amendment. AC-12.
- **T-2.11** Amend ADR-008 with a new §5 "Cooperative-lock eviction": link to ADR-012, note the contract change (deadline now evicts cooperative locks; supervisor is the only safe eviction point). AC-12.
- **T-2.12** Capture lesson `lesson-cancel-a-thread-you-cannot.md` in vault `10_projects/hive/90-lessons.md`. Body: 60-90 lines on why Python thread cancellation is impossible and the cooperative-eviction pattern it forces. AC-12.
- **T-2.13** Conventional Commit PR. Title: `fix(hive): evict cooperative filelock on deadline to unblock sibling workers (HIVE-116 PR-2)`. Body cross-references PR-1 + issue #141. Triggers release-please for v1.21.0. AC-13.

## Phase 3 — Cross-OS validation (PR-3, target v1.22.0, ~50 LOC + CI config)

Scope: lift the cross-worker test from `cross_worker` marker into the actual CI matrix on both Linux and Windows. Audit policy: opt-in for 14 days, then promote to required via HIVE-117 (out of scope).

- **T-3.1** Edit `.github/workflows/ci.yml` (or equivalent) to add a job `cross_worker_lock` with matrix `os: [ubuntu-latest, windows-latest]`. Continue-on-error: true (allowed-to-fail) for first 14 days. AC-10.
- **T-3.2** Re-run T-2.7 on Windows manually before merging — Windows filelock semantics may diverge per R1. If `mcp.lock_eviction.race` fires consistently on Windows, escalate: add a `time.sleep(0.1)` between eviction and the next acquire (Windows file-handle release is not synchronous). Document the choice. R1.
- **T-3.3** Update `tests/test_compat_shim.py::test_classify_cancellation_race` skip-marker to ALSO run on the Windows lane (currently Linux-only per file docstring lines 4-5). Verify the existing flake tolerance (subprocess flooding) doesn't tank the new lane. AC-10.
- **T-3.4** Document the new CI lane in `CONTRIBUTING.md` + the EN+ES docs site "Multi-session coexistence" page. AC-11.
- **T-3.5** Conventional Commit PR. Title: `ci(hive): cross-OS test lane for filelock eviction + cancellation race (HIVE-116 PR-3)`. Triggers release-please for v1.22.0. AC-13.

## Post-ship

- **T-9.1** Open follow-up issue: "Promote `cross_worker_lock` job to required after 14d observation (HIVE-117)" — owner gets reminded via the existing belt-and-braces pattern (issue + routine + dated TODO; see [[pattern-triple-reminder-belt-and-braces]]).
- **T-9.2** Archive `specs/HIVE-116-stale-lock-after-deadline/` to `specs/archive/` via a `chore(spec)` PR. Mirror HIVE-104, HIVE-115 archival pattern.
- **T-9.3** Update vault `10_projects/hive/11-tasks.md`: tick HIVE-116 as DONE, paste the three PR links + version tags. Note the 2026-06-05 Phase C checkpoint (#124) now has additional data: post-eviction `_compat.GHOST_RESPONSES.by_source["deadline"]` count.
- **T-9.4** Close issue #141 with a comment summarizing the fix and linking to the three release notes.

## Risks called out in proposal.md, mapped to tasks

| Risk | Mitigation task |
|------|----------------|
| R1 worker-thread escape race | T-2.9 race-warning log + counter; T-3.2 Windows-specific calibration |
| R2 drain-default calibration | T-2.8 (parametric sweep 1/5/10s × 20 runs) |
| R3 partial-state wording = public contract | T-0.2 USER decides; blocks T-1.4 |
| R4 Windows CI flake | T-3.1 continue-on-error 14d; T-3.3 retry policy = none |
| R5 Outbox interaction | T-2.7 cross-worker harness exercises `capture_lesson` path (uses Outbox internally) |

## Dependencies between phases

- Phase 1 may ship without Phase 2 — the user-visible improvements (logs + partial-state suffix) are independently valuable.
- Phase 2 depends on T-0.2 (user contract decision) being merged into the Phase 1 PR before Phase 2 starts implementing AC-5's full shape.
- Phase 3 depends on Phase 2 being green on Linux first (else the new CI lane has nothing to verify).
