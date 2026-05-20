---
id: HIVE-97-lesson-reinforcement-tasks
type: spec-tasks
status: active
created: 2026-05-18
---

# HIVE-97 — Implementation Tasks (TDD order)

> Strict red→green per task. Don't move to the next task until current tests pass.

## RED — Tests First (3 tiers, ~26 tests total)

### Tier 1: Unit — `tests/test_lesson_reinforcement.py` (10 tests)

Pure `LessonReinforcementTracker` behavior. In-memory DB. No MCP, no filesystem.

- [x] **T1.1** `test_schema_init` — fresh tracker has table with 7 columns + PK `(project, heading)`. ✅ 2026-05-19
- [x] **T1.2** `test_ensure_baseline` — `ensure()` then SELECT returns reinforcements=0, confidence=arg. ✅
- [x] **T1.3** `test_ensure_is_idempotent` — `ensure()` twice does NOT reset reinforcements (uses INSERT OR IGNORE, not REPLACE). ✅
- [x] **T1.4** `test_increment_arithmetic` — 5 increments from c0=0.7 → counter=5, `confidence ∈ [0.8218, 0.8238]`. Verifies `c_5 = 1 - 0.3 × 0.9^5`. ✅ (split into 2 tests: 1-inc + 5-inc)
- [x] **T1.5** `test_increment_ceiling` — 100 increments → `confidence ≤ 1.0` strict (never overflows). ✅
- [x] **T1.6** `test_increment_creates_row_if_missing` — `increment()` on unknown lesson lazy-creates at c=0.7, count=1. ✅
- [x] **T1.7** `test_concurrent_threads` — `threading.Thread × 20` on one row → final count == 20 (intra-process atomicity). ✅
- [x] **T1.8** `test_top_by_reinforcements` — 3 rows with counts [1, 5, 10] → top() returns [10, 5, 1]. ✅ (+ `test_top_respects_limit`)
- [x] **T1.9** `test_top_by_confidence_tie_recency` — equal confidence, different `last_referenced` → more-recent first. ✅
- [x] **T1.10** `test_top_by_hybrid_blend` — `alpha=0.7` blend. ✅ (split into 2 tests: high-BM25-wins + high-conf-tiebreaker)

**Tier 1 complete: 16/16 tests pass (commit `aeedf7d` RED + `9a3b87e` GREEN).**

### Tier 2: Integration — `tests/test_lesson_reinforcement_hooks.py` (10 tests)

Hooks wired into real `ServerContext` with a tmp_path vault. Calls handler functions directly (no MCP wire).

- [ ] **T2.1** `test_capture_lesson_inline_inserts_baseline` — `capture_lesson(inline)` writes lesson AND DB row exists.
- [ ] **T2.2** `test_capture_lesson_batch_inserts_baselines` — batch mode → N rows.
- [ ] **T2.3** `test_vault_query_increments_unique_headings_only` — `90-lessons.md` with 3 lessons → 3 increments, not 3×match_lines.
- [ ] **T2.4** `test_vault_query_non_lesson_file_no_op` — `vault_query` on `00-context.md` → DB untouched.
- [ ] **T2.5** `test_vault_search_default_byte_identical_to_pre_change` — golden assert: default rank_by="bm25" produces exact bytes pre-feature.
- [ ] **T2.6** `test_vault_search_rank_by_filters_to_lessons_only` — query matches `90-lessons.md` + `00-context.md`; `rank_by=reinforcements` → only lesson rows.
- [ ] **T2.7** `test_vault_search_rank_by_invalid_returns_error` — `rank_by="bogus"` → clear error message, no silent BM25 fallback.
- [ ] **T2.8** `test_lazy_ensure_pre_existing_lesson` — lesson on disk, no DB row → first `vault_query` lazy-inserts at c=0.7, count=1.
- [ ] **T2.9** `test_heading_in_codeblock_ignored` — `### [2026-01-01] foo` inside ``` ... ``` is NOT counted (reuse `_strip_code` from `_vault_health`).
- [ ] **T2.10** `test_malformed_heading_silently_skipped` — `### [not-a-date] x` doesn't crash, doesn't count.

### Tier 3: End-to-End — `tests/test_lesson_reinforcement_e2e.py` (6 tests)

Spin up FastMCP server in-memory per existing pattern (see `tests/test_integration.py`). Call tools via their MCP names. Verify both the tool response AND the on-disk SQLite state.

- [ ] **T3.1** `test_e2e_capture_then_search_ranked` — flow: `capture_lesson(inline)` × 3 lessons → `vault_search(query, rank_by="reinforcements")` × 5 — Lesson hit 5× tops the result list.
- [ ] **T3.2** `test_e2e_capture_lesson_find_mode_increments` — `capture_lesson(project, find="bug")` returns top-N AND increments each surfaced lesson exactly once (verified via direct SELECT after call).
- [ ] **T3.3** `test_e2e_vault_query_lesson_increments_visible_in_next_search` — `vault_query(project, section="lessons")` then `vault_search(rank_by="reinforcements")` — read-then-rank flow surfaces the just-read lesson.
- [ ] **T3.4** `test_e2e_concurrent_subprocess_reads_no_lost_updates` — spawn 2 `multiprocessing.Process` subprocesses, each calls `vault_query` on same lesson 5× → final count == 10. Validates SQLite WAL + busy_timeout under real cross-process contention (the failure mode that bit PR #90).
- [ ] **T3.5** `test_e2e_back_compat_existing_callers_unchanged` — replay representative golden vault_search calls from `tests/test_integration.py` — outputs must match byte-for-byte.
- [ ] **T3.6** `test_e2e_lazy_ensure_under_concurrent_first_touch` — 2 subprocesses simultaneously first-touch the same pre-existing lesson → exactly one row, count == 2 (INSERT OR IGNORE wins the race).

### Smoke (manual, documented in verification.md, NOT in CI)

- Run `capture_lesson(project="hive", find="multi-process")` against the real `~/Projects/knowledge` vault → confirm hits are real lessons + DB row count matches.
- Confirm `vault_search` performance against 313-lesson corpus stays under 2s (single-pass + sqlite lookup).

## GREEN — Implementation

- [x] **T2.** `src/hive/_lesson_reinforcement.py` — `LessonReinforcementTracker(_SqliteTracker)` with `_SCHEMA`, `ensure`, `increment`, `top`, `lookup`, `get` methods. ✅ commit `9a3b87e`.
- [x] **T3.** `src/hive/config.py` — added `lesson_db_path` field. ✅ commit `c15a36f`. README env-var count 18→19 still pending in T9.
- [x] **T4.** `src/hive/_context.py` — `ServerContext.lessons` field + `close()` teardown. `src/hive/server.py` — `create_server()` instantiates tracker + accepts `lesson_tracker` kwarg. ✅ commit `c15a36f`.
- [ ] **T5.** `src/hive/_workers.py` `capture_lesson`:
  - After successful `_write_lesson` in inline + batch branches: `ctx.lessons.ensure(project, heading, confidence)`.
  - New `find: str = ""` param. When set: parse `90-lessons.md` headings, filter by keyword, rank by `rank_by` (default `reinforcements`), return top N, `ctx.lessons.increment` each once (per-call set dedup).
- [ ] **T6.** `src/hive/_vault_read.py` `vault_query` — after returning content, if file is `90-lessons.md`, walk parsed headings → `ctx.lessons.ensure` + `ctx.lessons.increment` each unique heading once (set-based dedup).
- [ ] **T7.** `src/hive/_vault_read.py` `vault_search` — new `rank_by: str = "bm25"` param. When ≠ bm25: filter results to `90-lessons.md` matches only, group hits by heading (walk back to nearest `^### \[`), rank by `ctx.lessons.top(by=rank_by)`, increment each surfaced heading once. Default `bm25` path unchanged.

## VERIFY

- [ ] **T8.** `make check` — ruff, mypy --strict, pytest. Iterate to zero failures.
- [ ] **T9.** README — new "Lesson reinforcement" subsection. Update env-var count 18→19. Update test count after T8.
- [ ] **T10.** `site/src/content/docs/{,es/}` — mirror docs in EN + ES (bilingual rule).
- [ ] **T11.** `specs/HIVE-97-lesson-reinforcement/verification.md` — commit hashes, `pytest -k lesson_reinforcement` output, smoke evidence.
- [ ] **T12.** Vault patch — tick `10_projects/hive/11-tasks.md § Feature requests § SDD-036e` → DONE with PR link. Tick `10_projects/knowledge/11-tasks.md § SDD-036e` DEFERRED → DONE.

## OUT — On merge

- [ ] **T13.** Move `specs/HIVE-97-lesson-reinforcement/` → `specs/archive/HIVE-97-lesson-reinforcement/`.
- [ ] **T14.** Flip `00_meta/patterns/pattern-memory-consolidation.md` confidence-decay hook from "future work" → "implemented (Hive v1.12.X)".
