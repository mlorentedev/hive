---
id: HIVE-97-lesson-reinforcement
type: spec-proposal
status: active
created: 2026-05-18
owner: manu
links:
  - github_issue: https://github.com/mlorentedev/hive/issues/97
  - vault_origin: 10_projects/knowledge/11-tasks.md#SDD-036e
  - vault_hive: 10_projects/hive/11-tasks.md
---

# HIVE-97 — Lesson reinforcement counter with confidence decay

## Why

Lesson corpus across the knowledge vault is at 313 entries (18 projects). `vault_search` ranks ambiguous keyword hits by BM25-only — high-impact, recurrently-validated lessons rank identically to one-shot captures. Without a usage signal, the lesson corpus becomes a flat index regardless of which lessons have proven useful.

External validation: `rohitg00/agentmemory` (TS, 11.3k stars) implements the same shape over SQLite. The dual-memory split is our axiom; the reinforcement model is the part worth adopting.

**Re-activation context.** Originally deferred-with-criterion 2026-05-18. Criterion (1) Hive bug #94 closed — met via PR #95 / v1.12.10 (2026-05-19). Criterion (2) ranking pressure surfaces in practice — **explicitly overridden by user** in this session. Anti-pattern noted; user accepts.

## What

1. **`LessonReinforcementTracker`** — new SQLite-backed tracker (subclass of `_SqliteTracker`). One row per lesson; primary key `(project, heading)` raw.
   - Columns: `project TEXT`, `heading TEXT`, `reinforcements INTEGER DEFAULT 0`, `confidence REAL DEFAULT 0.7`, `first_seen TEXT NOT NULL`, `last_referenced TEXT`.
   - `ensure(project, heading, confidence)` — INSERT-OR-IGNORE baseline.
   - `increment(project, heading)` — atomic UPDATE: `reinforcements += 1`, `confidence = min(1.0, confidence + 0.1 * (1.0 - confidence))`, `last_referenced = today`.
   - `top(project, by, limit)` — ORDER BY `reinforcements | confidence | (alpha * bm25_norm + (1-alpha) * confidence)`.
   - `lookup(project, headings)` — fetch metadata for a list of headings (for hybrid score computation).

2. **Read hooks across 3 surfaces**, per-tool-call dedup via local set:
   - `_workers.py` `capture_lesson` **insert branch** — `ensure(project, heading, confidence)` after each write (inline + batch).
   - `_workers.py` `capture_lesson` **new `find` mode** — `find: str = ""` lookup param. Greps `90-lessons.md` headings for keyword; returns top matches by `rank_by`; increments each surfaced lesson once.
   - `_vault_read.py` `vault_query` — when file is `90-lessons.md`, parse headings inside truncated output; increment each.
   - `_vault_read.py` `vault_search` — for `90-lessons.md` matches, parse heading from match context (line-walk back to nearest `^### \[`); increment each unique lesson once.

3. **`rank_by` param on `vault_search`** — values: `bm25` (default, unchanged), `reinforcements`, `confidence`, `hybrid`.
   - Non-default values **filter implicitly to `90-lessons.md` matches only** (semantics: "search among lessons, ranked by usage signal"). Mixed results are out of scope by user decision; keeps semantics clean.
   - `hybrid = 0.7 * bm25_normalized + 0.3 * confidence` (alpha = 0.7, BM25-leaning).
   - Backwards compat: default `bm25` preserves current `ranked=True` behavior bit-for-bit.

4. **Config wiring** — `HiveSettings.lesson_db_path` (default `~/.local/share/hive/lesson_reinforcement.db`). `ServerContext.lessons: LessonReinforcementTracker`. `ServerContext.close()` adds `self.lessons.close()`.

## Out of scope

- Migration step for pre-existing lessons. Lazy `ensure()` on first read OR first increment. No bulk backfill.
- UI/MCP-surface for inspecting the table (would be a separate `lesson_stats` tool).
- Cross-project ranking. `top()` is per-project.
- Time-decay (forgetting curve) on `confidence`. Only positive reinforcement. Future work if proven needed.
- `vault_query` lookup on non-`90-lessons.md` files (no-op; would silently incorrect-classify section headings).

## Acceptance criteria

- [ ] **AC1 — schema bootstrap.** Fresh DB → table created with all 7 columns, primary key `(project, heading)`, default values respected.
- [ ] **AC2 — baseline insert.** `capture_lesson` writes a new lesson → row exists with `reinforcements=0`, `confidence=` capture confidence (default 0.7).
- [ ] **AC3 — increment + decay arithmetic.** 5 increments from `c0=0.7` → `reinforcements=5`, `confidence ∈ [0.819, 0.821]` (0.001 tolerance). Verifies `c_5 = 1 - 0.3 * 0.9^5 = 0.8228...`. Wait — recompute: `c_1 = 0.7 + 0.1*0.3 = 0.73`; `c_n = 1 - 0.3 * 0.9^n` → `c_5 = 1 - 0.3 * 0.59049 = 0.82285`. Tolerance 0.001 ⇒ `[0.8218, 0.8238]`.
- [ ] **AC4 — per-tool-call dedup.** `vault_query` on a `90-lessons.md` with 3 lessons → each row's `reinforcements` incremented exactly once, not once per matching line.
- [ ] **AC5 — concurrent reads.** 10 parallel `vault_query` calls of the same lesson → final `reinforcements=10` (SQLite WAL + atomic UPDATE in tracker base handle the race; no Python-level lock needed beyond `_SqliteTracker._lock`).
- [ ] **AC6 — `rank_by=reinforcements` sort.** `vault_search` query matching 3 lessons with reinforcements `[5, 1, 10]` → output order is lesson-10, lesson-5, lesson-1.
- [ ] **AC7 — `rank_by=hybrid` blend.** Lesson with high BM25 but low confidence beats lesson with low BM25 and high confidence iff `0.7*Δbm25 > 0.3*Δconfidence`.
- [ ] **AC8 — `rank_by` filters non-lessons.** Query matching both a `90-lessons.md` and a `00-context.md` with `rank_by=reinforcements` → only lesson result appears.
- [ ] **AC9 — `capture_lesson(find="…")` lookup mode.** Returns top-N matching lessons by `rank_by` (default `reinforcements`); each surfaced lesson is incremented once.
- [ ] **AC10 — back-compat.** All 428 existing tests pass. `vault_search` without `rank_by` returns byte-identical output.
- [ ] **AC11 — graceful pre-existing.** Lesson present on disk but NOT in DB → first read lazy-inserts at `confidence=0.7`, `reinforcements=1` (the read counts).
- [ ] **AC12 — `make check` clean.** ruff + mypy --strict + pytest all green.
- [ ] **AC13 — `rank_by` invalid value rejected loudly.** `rank_by="bogus"` returns explicit error (no silent BM25 fallback that masks typos).
- [ ] **AC14 — codeblock-aware heading parser.** `### [date] foo` inside a fenced code block is NOT counted as a lesson (reuse `_strip_code` from `_vault_health` to avoid duplicate parser logic and stay consistent with link-validator behaviour).
- [ ] **AC15 — malformed heading no-op.** Heading like `### [not-a-date] x` or `### no-bracket title` does not crash, does not count.
- [ ] **AC16 — `ensure` is INSERT OR IGNORE.** Calling `ensure()` on a lesson that already has `reinforcements=5` does NOT reset to 0. The baseline insert path must be non-destructive.
- [ ] **AC17 — confidence ceiling.** 100 increments → `confidence ≤ 1.0` strictly (never > 1.0).
- [ ] **AC18 — true cross-process atomicity.** 2 OS-level subprocesses (not threads) each calling `vault_query` on the same lesson 5× → final counter == 10. Validates the SQLite-WAL + busy_timeout combination under real inter-process contention. This is the failure mode that bit PR #90/92.
- [ ] **AC19 — concurrent lazy-ensure race.** 2 subprocesses simultaneously first-touching the same pre-existing lesson → exactly one row, counter == 2. INSERT OR IGNORE wins the race; no duplicate key error.

## Test plan (3 tiers)

See `tasks.md § RED` for full enumeration. Summary:

| Tier | File | Tests | Scope |
|---|---|---|---|
| Unit | `tests/test_lesson_reinforcement.py` | 10 | Tracker arithmetic, atomicity, ranking. No filesystem, no MCP. |
| Integration | `tests/test_lesson_reinforcement_hooks.py` | 10 | Hooks against real `ServerContext` + tmp_path vault. Handler-level. |
| E2E | `tests/test_lesson_reinforcement_e2e.py` | 6 | Full MCP wire (FastMCP in-memory per existing `test_integration.py` pattern). Cross-process via `multiprocessing.Process`. |
| **Total** | | **26** | |

Smoke against real vault is documented but not in CI (requires the local 313-lesson corpus).

## Design discussions

### Why `(project, heading)` raw as primary key

Heading-raw includes the date prefix `[2026-05-18]`. If user re-formats whitespace or em-dash, the cell key changes — we lose the count for that lesson. **Accepted risk:** in practice lessons are append-only; manual heading edits are rare and detectable (the old row simply stays orphan). The alternatives (slug-only, sha1) move the problem rather than solving it. Keeping the spec minimal beats premature normalization.

### Why `rank_by` filters to lessons only

Mixed-rank semantics ("re-rank lessons within a mixed result set, leave others alone") is impossible to explain in one sentence. The user query for `rank_by=reinforcements` is "give me well-validated lessons matching X" — filter-on-input matches that intent. Non-lesson results are still reachable via `rank_by=bm25` (default).

### Why no migration step

The vault has 313 lessons. Bulk backfill costs ~313 INSERTs at startup. Lazy `ensure()` on first read defers cost to actual usage — pre-existing lessons that are never referenced never enter the table. Cold cost = zero. Hot cost = one INSERT-OR-IGNORE per first-touch.

### Why `capture_lesson` gains `find=` mode (vs separate tool)

User decision in session: keeps the lesson surface single-tool. Symmetry: capture writes lessons, capture queries lessons. Avoids tool proliferation. ~50 LOC additional but aligned with existing MCP design language.

## Estimated effort

| Phase | LOC | Time |
|---|---|---|
| `LessonReinforcementTracker` | ~120 | 45 min |
| `ServerContext` + `HiveSettings` wiring | ~15 | 10 min |
| `vault_search rank_by` | ~50 | 30 min |
| `vault_query` + `capture_lesson` hooks (insert + `find`) | ~80 | 45 min |
| Tests (12 ACs) | ~250 | 60 min |
| README + site/ docs (EN + ES) | ~40 | 30 min |
| **Total production LOC** | **~265** | **~3.5h** |

Atomic-PR limit (300 LOC excluding tests) — within budget, no decomposition needed.
