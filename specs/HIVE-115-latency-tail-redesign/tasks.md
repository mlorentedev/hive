---
tags: [spec, tasks, HIVE-115]
created: "2026-05-21"
---

# Tasks — HIVE-115-latency-tail-redesign

> TDD order per [[pattern-testing-standards]] — red, green, refactor. One task = one focused commit. Reorder freely while spec is `draft`; freeze on transition to `implementing`.
>
> Bundle: v1.16.0 = PR-1 (Phase A defensive) + PR-2 (#114 XML defense) + PR-3 (bounded_call) + PR-4 (Outbox+detect-and-defer). PR-4 may slip to v1.17.0 if review reveals issues.

> **Audit note (2026-05-22, pre-PR-3 strategy review)** — surfaced 4 blockers (B1–B4) and 4 majors (M1–M4) in the original task sequence. Resolutions reflected below:
>
> - **B1**: T3.9 drops the inner `proc.communicate(timeout=30)` — `bounded_call` is the single source of truth for deadlines. Inner timeout would race with external termination.
> - **B2**: `_run_git` helper promoted from Refactor (was T3.13) to Green prerequisite (new T3.8a) — 8 actual `subprocess.run` callsites (not 5) make duplicating Popen+registry logic untenable without the helper landing first.
> - **B3**: `process_registry` is `list[Popen]`, not single — `_git_commit` executes 2 Popens (add+commit) sequentially; `_git_commit_all` executes 4. AC-9b added: termination between sub-operations must not leave staged-but-uncommitted state inconsistent (best-effort via next write rescuing index).
> - **B4**: T3.12 extends `GHOST_RESPONSES.record(tool, source)` with `source: Literal['cancellation', 'deadline']` so operators can distinguish race-condition suppressions (existing) from bounded_call-driven termination suppressions (new) in `vault_health` reports.
> - **M1**: T4.9 reconciler drops `bounded_call` wrap — instead uses `sqlite3.connect(..., timeout=2.0)` + `PRAGMA busy_timeout=2000`. Drops PR-4→PR-3 dependency; PR-4 can ship in parallel.
> - **M2**: T3.10 adds POSIX `start_new_session=True` + `os.killpg(os.getpgid(pid), SIG)` for symmetry with Windows `CREATE_NEW_PROCESS_GROUP`. Current `git` commands don't spawn descendants, but future-proofs against `git rebase`/`git clone`/hooks.
> - **M3**: T4.12 backoff sequence retuned `(3, 6, 12)` → `(1, 2, 4)` (total 7s) — fits inside reconciler tick budget without exceeding any deadline.
> - **M4**: T4.10 spells the detect-and-defer predicate explicitly: `defer ⇔ env=true ∧ obsidian_git_present ∧ ((last_commit_age < 2*autoSaveInterval) ∨ (git status --porcelain is empty))`. Idle vault (empty porcelain) ⇒ safe to defer; dirty + stale commits ⇒ external committer broken, fall back.
> - **m1**: Read-path `subprocess.run` (`_git_log`, `_git_recent`) stays as-is — cached 5min, advisory `tool_timeout`, no write side-effect to terminate. `bounded_call`/Popen migration scoped to write paths only.
> - **m2**: New T3.7b integration test — 3 concurrent hive subprocesses writing to a shared `git_vault` fixture, validates the actual N=3-5 patology.
> - **m3**: T4.7 Outbox docstring contract: unflushed entries lost on process crash; suitable for informational counters (reinforcement/EMA) only, not durable state. SQLite WAL durability is bypassed for buffered writes.
> - **m4**: T3.3/T3.5 marked `@pytest.mark.skipif(sys.platform == 'win32')` — Windows path lives in T3.7. Avoids `sleep` binary non-portability.

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

## PR-3 — `bounded_call` hard deadline (target ~400 LOC after audit)

> Closes #111. Opus-tier. Bundled with PR-1/2 for v1.16.0. LOC revised upward from 250 → ~400 per 2026-05-22 audit (8 actual `subprocess.run` callsites + `_run_git` helper + GHOST_RESPONSES discriminator + multi-process integration test).

### Red

- [ ] T3.1: failing test `tests/test_bounded_call.py::test_async_sleep_terminated` — `bounded_call(asyncio.sleep, 10, deadline_s=2)` returns `TimeoutError` within `2 + grace_s`
- [ ] T3.2: failing test `tests/test_bounded_call.py::test_blocking_thread_sleep_terminated` — `bounded_call(asyncio.to_thread(time.sleep, 10), deadline_s=2)` returns within `2 + grace_s`
- [ ] T3.3: failing test `tests/test_bounded_call.py::test_subprocess_terminated` — `@pytest.mark.skipif(sys.platform == 'win32')` (per m4); spawn `subprocess.Popen(["sleep", "60"])`, register, expire 2s deadline, assert `proc.poll()` is non-None within 4s; assert `proc.returncode` non-zero
- [ ] T3.4: failing test `tests/test_bounded_call.py::test_index_lock_pid_ownership` — write `.git/index.lock` with PID 99999 (not ours), expire deadline, assert lock untouched. Then write with our PID, assert cleaned up
- [ ] T3.5: failing test `tests/test_bounded_call.py::test_partial_commit_prevention` — `@pytest.mark.skipif(sys.platform == 'win32')` (per m4); start `git commit` Popen, kill mid-flight, assert HEAD unchanged (no half commit). AC-9b: also assert that if the kill lands between `git add` and `git commit`, `.git/index` may have staged changes but `git status --porcelain` shows them — next `_git_commit` call rescues by re-staging.
- [ ] T3.6: regression test `tests/test_compat_shim.py::test_classify_cancellation_race` still passes (ghost-response test untouched). Add `test_classify_deadline_vs_cancellation_source` asserting `GHOST_RESPONSES.snapshot()` discriminates source per B4.
- [ ] T3.7 (Windows): conditional test marked `@pytest.mark.skipif(not IS_WINDOWS)` — spawn cmd /c sleep 60, verify `TerminateProcess` + `CREATE_NEW_PROCESS_GROUP` correctly reaches descendants
- [ ] T3.7b: integration test `tests/test_bounded_call.py::TestMultiProcess::test_concurrent_writes_no_deadlock` (per m2) — spawn 3 hive subprocesses concurrently against shared `git_vault` fixture; each does 10 `vault_write` calls; assert all 30 writes complete within `30 * grace_s`, no deadlock, WAL bounded (`<1MB` post-checkpoint). Validates the actual N=3-5 patology, not just single-process unit tests.

### Green

- [ ] T3.8: new `src/hive/_deadline.py` module — `bounded_call(fn, *args, deadline_s, process_registry: list[subprocess.Popen[bytes]] | None = None, **kwargs)` (per B3: registry is a **list**, mutated by `_run_git` as it spawns/reaps Popens). Async-only signature; inner work either awaits or uses `asyncio.to_thread`. On deadline expiry: iterate `process_registry`, `terminate()` each, sleep 2s, `kill()` any survivors, drain stdio best-effort, raise `mcp.protocol.TimeoutError` with structured payload `{tool, deadline_s, elapsed_s, killed_pids}`.
- [ ] T3.8a (Green prerequisite, per B2): extract `_run_git(args, vault, registry, env=None) -> tuple[int, str, str]` in `src/hive/_helpers.py` — single helper that spawns `subprocess.Popen` (no inner `timeout=` per B1; `bounded_call` is SSOT), appends to registry, calls `communicate()` inside `asyncio.to_thread` so the event loop stays responsive, removes from registry on exit. Catches `BrokenPipeError`/`OSError` defensively because the Popen may be killed externally. **Must land BEFORE T3.9** — 8 actual callsites (2 in `_git_commit`, 4 in `_git_commit_all`, plus future write paths) make this a prerequisite, not a refactor.
- [ ] T3.9 (per B1 + m1): refactor `_git_commit(paths, message)` to call `_run_git(["add", *paths], ...)` then `_run_git(["commit", "-m", msg], ...)`. Refactor `_git_commit_all` to call `_run_git` for status/add/commit/rev-parse. **Keep read-path `subprocess.run` in `_git_log` / `_git_recent` untouched** (cached 5min, advisory `tool_timeout`, no termination needed — Popen+registry adds complexity without safety gain). Documented in `_run_git` docstring.
- [ ] T3.10 (per M2): cross-OS termination — Windows: `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`; POSIX: `start_new_session=True` + termination via `os.killpg(os.getpgid(pid), signal.SIGTERM)` then `SIGKILL` after grace. Symmetric reach to descendants on both platforms (future-proofs against `git rebase`/`git clone`/hooks; current git commands are leaf processes).
- [ ] T3.11: `_cleanup_index_lock(vault: Path, our_pids: list[int])` helper (per B3: accept list since `_git_commit` may have spawned multiple) — read PID from `.git/index.lock`, compare against any of our_pids, unlink ONLY if match. Wrap in try/except OSError. O_EXCL semantics protect against external lock takeover during the read→unlink window.
- [ ] T3.12 (per B4): replace `tool_span` (`src/hive/_helpers.py`) with wrapper that delegates to `bounded_call`. Existing `HIVE_TOOL_TIMEOUT` env honored unchanged. **Also**: extend `_compat.GHOST_RESPONSES.record(tool, source: Literal["cancellation", "deadline"])` so deadline-driven late-respond suppressions are distinguishable from cancellation-race suppressions; `_compat._patched_respond` records `source="cancellation"`; `bounded_call` (on TimeoutError raise) emits `source="deadline"` via the same counter. `vault_health.ghost_responses` block adds a `by_source: {cancellation: N, deadline: M}` field.

### Refactor

- [ ] T3.13 (per B2 — promoted to Green as T3.8a): _Closed in T3.8a._ Original Refactor task moved up because the 8-callsite migration is impractical without the helper landing first.
- [ ] T3.14 (per m1): assert all write-path git callsites use `_run_git` (audit via `grep "subprocess.run" src/hive/_helpers.py` shows ONLY `_git_log` / `_git_recent` retain the bare call, with inline comment justifying the read-path exception). Non-git subprocess uses stay.

### Closing PR-3

- [ ] AC-7 + AC-8 + AC-9 + AC-9b (per B3) + AC-10 verified
- [ ] `make check` green; `pytest tests/test_compat_shim.py` regression untouched; `GHOST_RESPONSES.snapshot()` shows non-zero `by_source.deadline` after a forced-timeout test
- [ ] Multi-process integration test (T3.7b) passes — 3 concurrent hive procs, no deadlock, WAL <1MB
- [ ] Conventional commit `feat(hive): HIVE-115 bounded_call hard deadline (#111, ADR-008)`
- [ ] PR opened against master referencing #111

---

## PR-4 — Outbox + Reconciler + detect-and-defer (target ~500 LOC after audit)

> Closes #110 fully; Phase B semantic change. Opus-tier. Pre-flight: ~~PR-3 must merge first~~ — **(per M1) PR-4 is now independent of PR-3.** Reconciler uses `sqlite3.connect(..., timeout=2.0)` + `PRAGMA busy_timeout=2000` so `BEGIN IMMEDIATE` cannot block more than 2s — no `bounded_call` wrap needed (which would have required composing async into a sync daemon thread). PR-3 and PR-4 can ship in parallel. May slip to v1.17.0 if review reveals issues. LOC revised upward 300 → ~500 per audit (predicate spelling + crash-loss docstring + multi-process scenarios).

### Red

- [ ] T4.1: failing test `tests/test_outbox.py::test_same_process_read_after_write` — `RelevanceTracker.record('x')` then `RelevanceTracker.score('x')` returns the recorded value immediately (outbox-first read path)
- [ ] T4.2: failing test `tests/test_outbox.py::test_cross_process_eventual_consistency` — process A writes; process B reads within `tick_interval` does NOT see it; process B reads after `2 * tick_interval` does see it
- [ ] T4.3 (per M1): failing test `tests/test_outbox.py::test_reconciler_busy_timeout_guard` — reconciler's `BEGIN IMMEDIATE` blocked by sibling holding write transaction; assert `sqlite3.OperationalError("database is locked")` raised within 2.5s (busy_timeout=2000ms + epsilon); assert outbox preserved for next tick (no data loss on retry).
- [ ] T4.4: failing test `tests/test_detect_and_defer.py::test_auto_defer_when_obsidian_healthy` — `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER=true` + `detect_obsidian_git` present + (last commit within `2 * autoSaveInterval` OR working tree clean) → `vault_write(commit=True)` returns `{committed: false, deferred_to: "obsidian-git"}`.
- [ ] T4.4b (per M4 — explicit negative): failing test `test_no_defer_when_env_false` — `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER=false` (default) + obsidian-git present + healthy → `vault_write(commit=True)` still commits via hive (`{committed: true}`). Guards against accidental flip of the default during refactoring.
- [ ] T4.5 (per M4): failing test `tests/test_detect_and_defer.py::test_fallback_when_obsidian_stale_AND_dirty` — obsidian-git config present BUT last commit > `2 * autoSaveInterval` ago AND `git status --porcelain` non-empty → external committer is genuinely broken → fall back to hive commit (`{committed: true, fallback_from: "obsidian-git-stale"}`).
- [ ] T4.5b (per M4 — explicit positive for idle vault): failing test `test_defer_when_obsidian_present_and_vault_idle` — obsidian-git config present, last commit > 2*interval ago, but `git status --porcelain` is **empty** (vault idle) → defer is safe (external committer alive, just nothing to commit) → return `{committed: false, deferred_to: "obsidian-git"}`.
- [ ] T4.6: failing test `tests/test_detect_and_defer.py::test_no_external_committer_uses_internal_reconciler` — no obsidian-git → hive's reconciler does its own commit with backoff retry on lock contention

### Green

- [ ] T4.7 (per m3): new `src/hive/_outbox.py` — `Outbox` class with thread-safe append + drain. Subclassable per resource (SQLite outbox, vault outbox). **Docstring contract**: "Unflushed entries are lost on process crash (kill -9, OOM, hard reboot). Suitable for *informational* counters only — reinforcement counts, EMA scores, ranking signals. Do **not** use for durable state (audit logs, transactional commits). SQLite WAL durability is bypassed for buffered writes; the trade-off is amortized commit cost across a tick."
- [ ] T4.8: `RelevanceTracker` + `LessonReinforcementTracker` — replace direct INSERT/UPSERT with outbox append; reconciler thread drains every 5s (configurable via `HIVE_OUTBOX_TICK_S`)
- [ ] T4.9 (per M1): reconciler thread does `BEGIN IMMEDIATE` + UPSERT bulk + `PRAGMA wal_checkpoint(PASSIVE)` per tick. Uses `sqlite3.connect(..., timeout=2.0)` + `PRAGMA busy_timeout=2000` so `BEGIN IMMEDIATE` cannot block more than 2s. On `OperationalError("database is locked")`: log + leave outbox intact for next tick (idempotent retry). **No `bounded_call` wrap** — async-sync impedance mismatch + SQLite's own guard is sufficient.
- [ ] T4.10 (per M4): `src/hive/_vault_write.py` — `vault_write`/`vault_patch` consult `_should_defer_to_external_committer(ctx)` before invoking `_git_commit`. The composite predicate (extracted as `_is_external_committer_healthy` per T4.14):
  ```
  defer ⇔
      env "HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER" == "true"
      AND detect_obsidian_git(vault) returns a config (i.e. plugin installed and enabled)
      AND (
          (now - last_commit_epoch < 2 * autoSaveInterval_seconds)
          OR
          (git status --porcelain produces empty output  # vault idle, defer is safe)
      )
  ```
  Idle vault (empty porcelain) ⇒ external committer alive but nothing to commit ⇒ defer is safe. Recent commits ⇒ external committer healthy ⇒ defer. Dirty + stale commits ⇒ external committer broken ⇒ fall back.
- [ ] T4.11: when deferred, response includes `{committed: false, deferred_to: "obsidian-git"}`; when fallback fires, response includes `{committed: true, fallback_from: "obsidian-git-stale"}` so tests can assert behavior
- [ ] T4.12 (per M3): backoff retry helper `_acquire_git_lock_with_backoff(ctx, backoffs=(1, 2, 4))` for the case where no external committer is present and hive's reconciler hits contention. **Total wait = 7s**, fits inside any deadline ≥ 10s and within reconciler tick budget. After exhausting backoffs, abandon with structured log `mcp.git_lock.abandoned vault=<path> total_waited_s=7`.

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
