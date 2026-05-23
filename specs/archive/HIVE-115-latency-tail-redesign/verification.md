---
tags: [spec, verification, HIVE-115]
created: "2026-05-21"
---

# Verification — HIVE-115-latency-tail-redesign

> Filled progressively as PR-1..PR-4 merge. Routine implementation choices belong in commit messages, not here.

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior). To be filled per-PR.

- [ ] AC-1 (PASSIVE checkpoint thread) → commit `<hash>` / test `tests/test_sqlite_checkpoint.py::test_passive_checkpoint_runs_on_interval`
- [ ] AC-2 (TRUNCATE on close with 2s guard) → commit `<hash>` / test `tests/test_sqlite_checkpoint.py::test_close_truncates_with_2s_guard`
- [ ] AC-3 (vault_health runtime metrics) → commit `<hash>` / test `tests/test_vault_health.py::TestRuntimeWalMetrics` + `test_obsidian_git_present_surfaces`
- [ ] AC-4 (HIVE_LOCK_TIMEOUT_S env) → commit `<hash>` / test `tests/test_settings.py::test_hive_lock_timeout_s_env`
- [ ] AC-5 (mcp.lock_contention structured log) → commit `<hash>` / test `tests/test_helpers.py::test_git_lock_emits_structured_log`
- [ ] AC-6 (capture_lesson XML defense) → commit `<hash>` / test `tests/test_workers.py::TestCaptureLessonXmlDefense`
- [ ] AC-7 (bounded_call enforces deadline across async/thread/subprocess) → commit `<hash>` / tests `tests/test_bounded_call.py::test_async_sleep_terminated` + `test_blocking_thread_sleep_terminated` + `test_subprocess_terminated`
- [ ] AC-8 (bounded_call terminates registered Popen) → commit `<hash>` / test `tests/test_bounded_call.py::test_subprocess_terminated`
- [ ] AC-9 (.git/index.lock PID-ownership cleanup) → commit `<hash>` / test `tests/test_bounded_call.py::test_index_lock_pid_ownership`
- [ ] AC-9b (partial commit between sub-Popens does not block recovery — added 2026-05-22 per B3) → commit `<hash>` / test `tests/test_bounded_call.py::test_partial_commit_prevention`
- [ ] AC-10 (no regression; write paths migrated, read paths advisory) → `make check` green; ghost-response test passes; HIVE-104 coalescer tests pass; multi-process integration test (T3.7b) passes
- [ ] AC-11 (outbox + reconciler for SQLite trackers) → commit `<hash>` / tests `tests/test_outbox.py::*`
- [ ] AC-12 (detect-and-defer to obsidian-git) → commit `<hash>` / tests `tests/test_detect_and_defer.py::*`
- [ ] AC-13 (bilingual troubleshooting docs) → commits `<hashes>` / files `site/.../guides/troubleshooting.md` (EN + ES)
- [ ] AC-14 (ADRs + lessons + pattern persisted in vault) → vault commits `4d9c642` (lessons + meta-pattern, 2026-05-21) and `02d1f63` (ADRs + amendments + pattern extension, 2026-05-21); plus repo commit `<hash>` (specs/HIVE-115-latency-tail-redesign/* — this spec scaffold)

## Test status

Per-PR fill-in:

- PR-1: `make check` → `<output>` / coverage `<pct>%`
- PR-2: `make check` → `<output>`
- PR-3: `make check` → `<output>` / regression `tests/test_compat_shim.py` → `<output>`
- PR-4: `make check` → `<output>` / cross-process eventual-consistency test `<output>`

Manual smoke test:

- (PR-1) Open 3 Claude Code sessions concurrently; observe `vault_health(include_runtime=True)` reports `competing_pid_count: 3`, `wal_size_bytes` stays bounded after periodic checkpoint runs; verify `mcp.lock_contention` logs accumulate in per-PID log files
- (PR-3) Trigger an artificial git lock contention (hold `.git/index.lock` manually with `sleep 120 > .git/index.lock` style); fire `vault_write`; verify bounded_call surfaces `mcp.protocol.TimeoutError` within `HIVE_TOOL_TIMEOUT + 3s`; verify no zombie subprocess
- (PR-4) With `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER=true` and obsidian-git active, fire 5 `vault_write` calls; verify responses say `{deferred_to: "obsidian-git"}`; verify obsidian-git's next tick produces a single commit with all 5 files

## Decisions made during implementation

Non-obvious trade-offs and course corrections taken during the work. Filled per-PR.

- (PR-1): _filled at PR-1 completion_
- (PR-2): _filled at PR-2 completion_
- (PR-3): _filled at PR-3 completion_
- (PR-4): _filled at PR-4 completion_

## Promotion candidates

ADRs and lessons were already promoted to the vault BEFORE implementation (the rationale is the spec's foundation). Cross-reference, don't re-promote.

- [x] Lessons for `hive/90-lessons.md`? **YES — pre-promoted 2026-05-21.** Four lessons captured: [[lesson-three-timeouts-chain-not-deadline]], [[lesson-sqlite-wal-no-checkpoint-with-readers]], [[lesson-cooperative-external-committer]], [[lesson-telemetry-is-the-design]] (vault commit `4d9c642`).
- [x] ADR-worthy decisions for `hive/30-architecture/adr-XXX.md`? **YES — pre-promoted 2026-05-21.** Three new: [[adr-008-hard-deadline-enforcement]], [[adr-009-multi-process-wal-policy]], [[adr-010-external-committer-coexistence]]. Two amended: [[adr-005-transport-and-scale]], [[adr-006-commit-policy]] (vault commit `02d1f63`).
- [x] New pattern candidate for `00_meta/patterns/`? **YES — pre-promoted 2026-05-21.** [[pattern-phased-redesign-with-telemetry-gates]] created; [[pattern-multi-process-mcp-server]] extended with primitives 5-7 (vault commits `4d9c642` + `02d1f63`).
- [ ] Implementation-time lesson (only fill if something unexpected emerges during coding): `<yes / no — one line>`

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-115-latency-tail-redesign/` → `specs/archive/HIVE-115-latency-tail-redesign/`
- [ ] Backlog entry in vault `hive/11-tasks.md` ticked with PR links (PR-1 + PR-2 + PR-3 + PR-4)
- [ ] Promotions above already executed (pre-implementation); no further vault writes needed unless an implementation-time lesson emerges
- [ ] Cron scheduled for v1.16.0 + 14 days (Phase C daemon decision per ADR-011)
