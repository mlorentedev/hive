---
id: HIVE-97-lesson-reinforcement-verification
type: spec-verification
status: pending
created: 2026-05-18
---

# HIVE-97 — Verification

> **All 19 acceptance criteria verified 2026-05-19.** Branch ready for PR.

## Cross-machine resumption pointer

To pick this up on a new machine:

```bash
git fetch && git checkout feat/lesson-reinforcement
git log --oneline master..HEAD   # expect 4 commits ending at c15a36f
make check                        # must be green before continuing
```

Then resume at Tier 2: write `tests/test_lesson_reinforcement_hooks.py` per `tasks.md § T2.1–T2.10`, then implement hooks in `_workers.py` + `_vault_read.py` per § T5–T7.

## Commit log (HIVE-97 branch — `feat/lesson-reinforcement`)

| Commit | Subject |
|---|---|
| `34a996b` | `spec(HIVE-97): scaffold lesson reinforcement counter (SDD-036e)` |
| `aeedf7d` | `test(HIVE-97): Tier 1 RED tests for LessonReinforcementTracker` |
| `9a3b87e` | `feat(HIVE-97): LessonReinforcementTracker (Tier 1 GREEN)` |
| `c15a36f` | `feat(HIVE-97): wire LessonReinforcementTracker into ServerContext` |
| `c5a928c` | `docs(HIVE-97): tick Tier 1 done in tasks.md + verification.md handoff` |
| `cb28066` | `refactor(HIVE-97): move _strip_code from _vault_health to _helpers` |
| `75ce241` | `feat(HIVE-97): find_lesson_heading helper + TestStripCode coverage` |
| `37a9363` | `test(HIVE-97): Tier 2 RED tests for lesson reinforcement hooks` |
| `9b30ac4` | `feat(HIVE-97): wire lesson reinforcement hooks across surfaces (Tier 2 GREEN)` |
| `6162da6` | `docs(HIVE-97): tick Tier 2 GREEN in tasks.md + verification.md` |
| `bdbb4f5` | `feat(HIVE-97): Tier 3 e2e + capture_lesson(find=...) lookup mode` |

## Test output — final (2026-05-19)

```
$ make check
478 passed, 1 skipped, 57 deselected in 27.31s
ruff: All checks passed.
mypy --strict: Success: no issues found in 18 source files.

$ uv run pytest tests/test_lesson_reinforcement_e2e.py -m smoke --no-cov
2 passed, 5 deselected in 2.46s
```

Counts by tier:
- Tier 1 (unit, `test_lesson_reinforcement.py`):     16 / 16 ✅
- Helpers (`test_helpers.py` new):                   15 / 15 ✅
- Tier 2 (integration, `*_hooks.py`):                10 / 10 ✅
- Tier 3 (e2e, `*_e2e.py`):                           5 / 5 ✅ (+ 2 smoke ✅)
- Other regression tests:                          430 / 430 ✅
- **Total in `make test`:**                        **478 / 479 (1 unrelated network skip)**

## Smoke evidence

Cross-process multi-procs (`@pytest.mark.smoke`, `make smoke`):
- T3.4 — 2 OS processes × 5 increments → final count == 10 ✅
- T3.6 — 2 OS processes simultaneous first-touch → exactly one row, count == 2 ✅

Both passed in 2.46s on a single-machine sanity check; they validate
the WAL + `busy_timeout` inheritance from `_SqliteTracker` under real
inter-process SQLite contention.

Vault smoke against the live `~/Projects/knowledge` (313 lessons across
18 projects) — _deferred_; will be done post-merge on the released
version.

## Acceptance criteria checklist (final, all 19 verified)

- [x] AC1 — schema bootstrap (Tier 1 — 3 schema tests)
- [x] AC2 — baseline insert (Tier 2 T2.1, T2.2 + capture_lesson hook)
- [x] AC3 — increment + decay arithmetic (Tier 1 — first+five increment tests)
- [x] AC4 — per-tool-call dedup (Tier 2 T2.3 — `dict.fromkeys` headings)
- [x] AC5 — concurrent reads (Tier 3 T3.4 covers the superset — cross-process)
- [x] AC6 — `rank_by=reinforcements` (Tier 2 T2.6 + Tier 3 T3.1 ranked order)
- [x] AC7 — `rank_by=hybrid` (Tier 1 unit tests on blend math + Tier 3 e2e)
- [x] AC8 — `rank_by` filters non-lessons (Tier 2 T2.6 — 00-context.md excluded)
- [x] AC9 — `capture_lesson(find=...)` (Tier 3 T3.2 — surfaces + increments)
- [x] AC10 — back-compat (Tier 2 T2.5 + Tier 3 T3.5 — byte-identical golden)
- [x] AC11 — graceful pre-existing (Tier 2 T2.8 — lazy ensure on first read)
- [x] AC12 — `make check` clean (478 pass + ruff + mypy --strict OK)
- [x] AC13 — `rank_by="bogus"` rejected (Tier 2 T2.7 — explicit error)
- [x] AC14 — codeblock-aware heading parser (Tier 2 T2.9 + helper unit tests)
- [x] AC15 — malformed heading no-op (Tier 2 T2.10)
- [x] AC16 — `ensure` is INSERT OR IGNORE (Tier 1 idempotency test)
- [x] AC17 — confidence ceiling (Tier 1 100-increments test)
- [x] AC18 — true cross-process atomicity (Tier 3 T3.4 smoke — passes)
- [x] AC19 — concurrent lazy-ensure race (Tier 3 T3.6 smoke — passes)

**19 / 19 ✅ — branch ready for PR.**
