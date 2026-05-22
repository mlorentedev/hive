---
tags: [spec, tasks, HIVE-115]
created: "2026-05-21"
---

# Tasks — HIVE-115-latency-tail-redesign

> TDD order per [[pattern-testing-standards]] — red, green, refactor. One task = one focused commit. Reorder freely while spec is `draft`; freeze on transition to `implementing`.
>
> Bundle: v1.16.0 = PR-1 (Phase A defensive) + PR-2 (#114 XML defense) + PR-3 (bounded_call) + PR-4 (Outbox+detect-and-defer). PR-4 may slip to v1.17.0 if review reveals issues.

## Setup

- [ ] Branch `feat/HIVE-115-latency-tail` cut from `master`
- [ ] `proposal.md` complete; R3 + R4 + R5 (open questions) resolved before any code
- [ ] `make check` passes on master baseline (508 passed, 3 skipped per v1.15.0)
- [ ] `psutil` added to `pyproject.toml` runtime deps (planned for PR-1)

---

## PR-1 — Phase A defensive (target ~170 LOC)

> Closes #110 mits #1+#2+#3+#5 (operational, observability, tunable, periodic WAL drain).

### Red — failing tests first

- [ ] T1.1: write failing test `tests/test_sqlite_checkpoint.py::test_passive_checkpoint_runs_on_interval` — create `RelevanceTracker`, write 1000 events, sleep 35s, assert `cursor.execute("PRAGMA wal_checkpoint")` reports advancement
- [ ] T1.2: write failing test `tests/test_sqlite_checkpoint.py::test_checkpoint_thread_dies_with_parent` — assert thread is `daemon=True`
- [ ] T1.3: write failing test `tests/test_sqlite_checkpoint.py::test_close_truncates_with_2s_guard` — open tracker with sibling connection holding read snapshot; close should complete within 2.5s
- [ ] T1.4: write failing tests `tests/test_vault_health.py::TestRuntimeWalMetrics` — parametrized: `wal_size_bytes` reflects actual file size; `competing_pid_count` returns 0 baseline, increments under sibling subprocess
- [ ] T1.5: write failing test `tests/test_vault_health.py::test_last_git_lock_wait_ms_rolling_window` — emit 150 acquire events, assert ring buffer keeps last 100 and exposes mean + p99
- [ ] T1.6: write failing test `tests/test_vault_health.py::test_obsidian_git_present_surfaces` — fixture creates `.obsidian/plugins/obsidian-git/data.json`; assert `vault_health.runtime` reflects it
- [ ] T1.7: write failing test `tests/test_settings.py::test_hive_lock_timeout_s_env` — `HIVE_LOCK_TIMEOUT_S=5` honored; `=0` and `=601` rejected with `ValueError`
- [ ] T1.8: write failing test `tests/test_helpers.py::test_git_lock_emits_structured_log` — caplog captures `mcp.lock_contention` event with required fields on both timeout-pass and timeout-fail paths

### Green — implement

- [ ] T1.9: `src/hive/_sqlite_tracker.py` — add `_checkpoint_loop` daemon thread + `_stop_checkpoint` Event; wire from `__init__` and `close`. Use `with self._lock` around `cursor.execute("PRAGMA wal_checkpoint(PASSIVE)")`. Honor `HIVE_WAL_CHECKPOINT_INTERVAL_S` env (default 30)
- [ ] T1.10: `src/hive/_vault_health.py::_runtime_block` — extend with `wal_size_bytes` (`Path(state_dir).glob('*.db-wal')` size sum), `competing_pid_count` (new helper `_count_competing_hive_processes` using `psutil.process_iter`, cached 30s via functools.lru_cache + time-based eviction), `last_git_lock_wait_ms` (rolling window in `_helpers._GIT_LOCK_STATS` global), `obsidian_git_present` (reuse `detect_obsidian_git()` from HIVE-104)
- [ ] T1.11: `pyproject.toml` — add `psutil>=5.9.0` to `dependencies` (runtime). `uv lock` regenerated
- [ ] T1.12: `src/hive/config.py` — add `HiveSettings.lock_timeout_s` (env `HIVE_LOCK_TIMEOUT_S`, default 30, validator 1≤x≤600); `HiveSettings.wal_checkpoint_interval_s` (default 30)
- [ ] T1.13: `src/hive/_helpers.py:551` — replace hardcoded `_LOCK_TIMEOUT = 30` with `ctx.lock_timeout_s`; refactor `_GIT_LOCK.acquire(timeout=...)` callsites; instrument with `time.monotonic()` brackets emitting `mcp.lock_contention` structured log

### Refactor

- [ ] T1.14: extract `_compute_wal_size_bytes(state_dir: Path) -> int` and `_count_competing_hive_processes(self_pid: int) -> int` as pure helpers in `_helpers.py` for testability
- [ ] T1.15: deduplicate ring-buffer logic between `_GIT_LOCK_STATS` and any future tracker stats (if applicable)

### Docs

- [ ] T1.16: `site/src/content/docs/guides/troubleshooting.md` — add "Multi-session contention" section: zombie kill recipes for POSIX (`ps`, `lsof`) and Windows (`Get-Process`, `tasklist`); `HIVE_LOCK_TIMEOUT_S` recommended ranges; obsidian-git cooperation pattern
- [ ] T1.17: `site/src/content/docs/es/guides/troubleshooting.md` — mirror EN (bilingual sync mandatory per CLAUDE.md)
- [ ] T1.18: `site/src/content/docs/configuration.md` + `es/configuration.md` — document new env vars `HIVE_LOCK_TIMEOUT_S` + `HIVE_WAL_CHECKPOINT_INTERVAL_S`

### Closing PR-1

- [ ] AC-1..AC-5 + AC-13 verified
- [ ] `make check` green
- [ ] Conventional commit `feat(hive): HIVE-115 Phase A defensive — WAL periodic + telemetry + tunable lock`
- [ ] PR opened against master referencing #110

---

## PR-2 — #114 Tier-1 capture_lesson XML defense (target ~30 LOC)

> Closes #114. Independent of PR-1/3/4; can ship in any order. Sonnet or haiku.

### Red

- [ ] T2.1: failing test `tests/test_workers.py::TestCaptureLessonXmlDefense` — parametrized over `SUSPECT_PATTERNS`; assert response includes `warning` field; assert body contains `<!-- POSSIBLE_CORRUPTION ... -->` comment
- [ ] T2.2: negative test — normal lesson body without patterns has no warning, no HTML comment

### Green

- [ ] T2.3: `src/hive/_workers.py::capture_lesson` — add `SUSPECT_PATTERNS = [r"</context>", r"</parameter>", r"<parameter name=", r"</invoke>"]` (compiled regex)
- [ ] T2.4: scan `title`, `context`, `problem`, `solution` strings; if any match, prepend `<!-- POSSIBLE_CORRUPTION: detected XML-tag leak in input; review and clean manually -->` to the final body; return `warning` field

### Closing PR-2

- [ ] AC-6 verified
- [ ] Conventional commit `feat(hive): HIVE-115 capture_lesson XML-leak defense (#114 Tier-1)`
- [ ] PR opened against master referencing #114

---

## PR-3 — `bounded_call` hard deadline (target ~250 LOC)

> Closes #111. Opus-tier. Bundled with PR-1/2 for v1.16.0.

### Red

- [ ] T3.1: failing test `tests/test_bounded_call.py::test_async_sleep_terminated` — `bounded_call(asyncio.sleep, 10, deadline_s=2)` returns `TimeoutError` within `2 + grace_s`
- [ ] T3.2: failing test `tests/test_bounded_call.py::test_blocking_thread_sleep_terminated` — `bounded_call(asyncio.to_thread(time.sleep, 10), deadline_s=2)` returns within `2 + grace_s`
- [ ] T3.3: failing test `tests/test_bounded_call.py::test_subprocess_terminated` — spawn `subprocess.Popen(["sleep", "60"])`, register, expire 2s deadline, assert `proc.poll()` is non-None within 4s; assert `proc.returncode` non-zero
- [ ] T3.4: failing test `tests/test_bounded_call.py::test_index_lock_pid_ownership` — write `.git/index.lock` with PID 99999 (not ours), expire deadline, assert lock untouched. Then write with our PID, assert cleaned up
- [ ] T3.5: failing test `tests/test_bounded_call.py::test_partial_commit_prevention` — start `git commit` Popen, kill mid-flight, assert HEAD unchanged (no half commit)
- [ ] T3.6: regression test `tests/test_compat_shim.py::test_classify_cancellation_race` still passes (ghost-response test untouched)
- [ ] T3.7 (Windows): conditional test marked `@pytest.mark.skipif(not IS_WINDOWS)` — spawn cmd /c sleep 60, verify `TerminateProcess` + `CREATE_NEW_PROCESS_GROUP` correctly reaches descendants

### Green

- [ ] T3.8: new `src/hive/_deadline.py` module — `bounded_call(fn, *args, deadline_s, process_registry=None, **kwargs)`. Async-only signature; inner work either awaits or uses `asyncio.to_thread`. On deadline expiry: iterate `process_registry`, `terminate()`, sleep 2s, `kill()` any survivors, drain stdio best-effort, raise `mcp.protocol.TimeoutError` with structured payload
- [ ] T3.9: `src/hive/_helpers.py` — refactor `_git_commit(paths, message)` from `subprocess.run` to `subprocess.Popen` + `proc.communicate(timeout=30)`. Add `process_registry` parameter; register the Popen. Replace remaining `subprocess.run` calls (`_git_commit_all`, `_git_log_recent`, `_current_head_sha`, status callers) with same pattern
- [ ] T3.10: cross-OS termination — `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` when `sys.platform == "win32"`; otherwise no flag (POSIX uses signal-based termination)
- [ ] T3.11: `_cleanup_index_lock(vault: Path, our_pid: int)` helper — read PID from lock file, compare, unlink ONLY if match. Wrap in try/except OSError
- [ ] T3.12: replace `tool_span` (`src/hive/_helpers.py`) with wrapper that delegates to `bounded_call`. Existing `HIVE_TOOL_TIMEOUT` env honored unchanged

### Refactor

- [ ] T3.13: extract subprocess context manager from each git callsite into shared helper `_run_git(args, vault, registry, timeout) -> tuple[returncode, stdout, stderr]`
- [ ] T3.14: assert all 5 git callsites use the new helper (audit via grep `subprocess.run` should return 0 hits in hive source after refactor; non-git subprocess uses stay)

### Closing PR-3

- [ ] AC-7 + AC-8 + AC-9 + AC-10 verified
- [ ] `make check` green; `pytest tests/test_compat_shim.py` regression untouched
- [ ] Conventional commit `feat(hive): HIVE-115 bounded_call hard deadline (#111, ADR-008)`
- [ ] PR opened against master referencing #111

---

## PR-4 — Outbox + Reconciler + detect-and-defer (target ~300 LOC)

> Closes #110 fully; Phase B semantic change. Opus-tier. Pre-flight: PR-3 must merge first (reconciler thread relies on bounded_call for preemption authority). May slip to v1.17.0 if review reveals issues.

### Red

- [ ] T4.1: failing test `tests/test_outbox.py::test_same_process_read_after_write` — `RelevanceTracker.record('x')` then `RelevanceTracker.score('x')` returns the recorded value immediately (outbox-first read path)
- [ ] T4.2: failing test `tests/test_outbox.py::test_cross_process_eventual_consistency` — process A writes; process B reads within `tick_interval` does NOT see it; process B reads after `2 * tick_interval` does see it
- [ ] T4.3: failing test `tests/test_outbox.py::test_reconciler_thread_bounded_by_supervisor` — reconciler's `BEGIN IMMEDIATE` blocks > deadline; `bounded_call` terminates it; outbox preserved for next tick
- [ ] T4.4: failing test `tests/test_detect_and_defer.py::test_auto_defer_when_obsidian_healthy` — `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER=true` + `detect_obsidian_git` healthy → `vault_write(commit=True)` returns `{committed: false, deferred_to: "obsidian-git"}`
- [ ] T4.5: failing test `tests/test_detect_and_defer.py::test_fallback_when_obsidian_stale` — obsidian-git config present BUT `git log -1 --since="<2*interval>"` returns empty → fallback to hive commit
- [ ] T4.6: failing test `tests/test_detect_and_defer.py::test_no_external_committer_uses_internal_reconciler` — no obsidian-git → hive's reconciler does its own commit with backoff retry on lock contention

### Green

- [ ] T4.7: new `src/hive/_outbox.py` — `Outbox` class with thread-safe append + drain. Subclassable per resource (SQLite outbox, vault outbox).
- [ ] T4.8: `RelevanceTracker` + `LessonReinforcementTracker` — replace direct INSERT/UPSERT with outbox append; reconciler thread drains every 5s (configurable via `HIVE_OUTBOX_TICK_S`)
- [ ] T4.9: reconciler thread does `BEGIN IMMEDIATE` + UPSERT bulk + `PRAGMA wal_checkpoint(PASSIVE)` per tick; wrapped in `bounded_call` (from PR-3) so a hung reconciler cannot stall shutdown
- [ ] T4.10: `src/hive/_vault_write.py` — `vault_write`/`vault_patch` consult `_should_defer_to_external_committer(ctx)` before invoking `_git_commit`. Helper checks `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER` env + `detect_obsidian_git()` + recency probe (`git log -1 --since=...`)
- [ ] T4.11: when deferred, response includes `{committed: false, deferred_to: "obsidian-git"}`; when fallback fires, response includes `{committed: true, fallback_from: "obsidian-git-stale"}` so tests can assert behavior
- [ ] T4.12: backoff retry helper `_acquire_git_lock_with_backoff(ctx, backoffs=(3, 6, 12))` for the case where no external committer is present and hive's reconciler hits contention. After exhausting backoffs, abandon with structured log

### Refactor

- [ ] T4.13: deduplicate outbox-read pattern across trackers (`get` always checks outbox first, then DB)
- [ ] T4.14: extract `_is_external_committer_healthy(vault, autoSaveInterval) -> bool` for testability

### Docs

- [ ] T4.15: site docs (EN + ES) update — "obsidian-git integration" page describing auto-defer behavior, env var `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER`, fallback semantics
- [ ] T4.16: README — link to new troubleshooting + obsidian-git pages

### Closing PR-4

- [ ] AC-11 + AC-12 verified
- [ ] `make check` green; full suite including outbox + detect-and-defer tests
- [ ] Conventional commit `feat(hive): HIVE-115 Outbox + Reconciler + detect-and-defer (ADR-009 v2, ADR-010)`
- [ ] PR opened against master, referencing #110 (closes when merged)

---

## Release & verification

- [ ] After PR-1..PR-4 merged: release-please auto-bumps to v1.16.0
- [ ] After v1.16.0 ships: schedule cron firing 14 days later for Phase C decision (per task #13 in session task list)
- [ ] Fill `verification.md` with concrete evidence (commit hashes, test names, observed behavior); flag promotion candidates (mostly "no" since ADRs/lessons/pattern already promoted)
- [ ] `/spec archive HIVE-115-latency-tail-redesign --pr <pr-urls>` ticks backlog entry

---

## Machine-readable features

Will emit `specs/HIVE-115-latency-tail-redesign/features.json` mapping ACs 1-14 to verification commands per [[pattern-feature-list-as-primitive]]. Drafted alongside PR-1 implementation (T1.16/T1.17 area).
