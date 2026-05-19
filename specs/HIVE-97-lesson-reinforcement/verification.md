---
id: HIVE-97-lesson-reinforcement-verification
type: spec-verification
status: pending
created: 2026-05-18
---

# HIVE-97 — Verification

To be filled at the end of implementation per `specs/SKILL.md`.

## Commit log

_(to fill)_

## Test output

```
$ make check
(to fill)
```

## Smoke evidence

_(to fill — manual `capture_lesson(find="…")` against the knowledge vault, confirm increments visible via direct sqlite SELECT)_

## Acceptance criteria checklist

- [ ] AC1 — schema bootstrap
- [ ] AC2 — baseline insert
- [ ] AC3 — increment + decay arithmetic
- [ ] AC4 — per-tool-call dedup
- [ ] AC5 — concurrent reads
- [ ] AC6 — `rank_by=reinforcements`
- [ ] AC7 — `rank_by=hybrid`
- [ ] AC8 — `rank_by` filters non-lessons
- [ ] AC9 — `capture_lesson(find=...)`
- [ ] AC10 — back-compat
- [ ] AC11 — graceful pre-existing
- [ ] AC12 — `make check` clean
