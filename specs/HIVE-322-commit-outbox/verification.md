---
tags: [spec, verification, templates]
created: "2026-08-07"
---

# Verification - HIVE-322-commit-outbox

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior).

- [ ] AC1 no commit in the write call path -> test `<name>`
- [ ] AC2 one commit per tick, exact path set -> test `<name>`
- [ ] AC3 latency + commit-count bound under 10 writers -> benchmark `<name>`
- [ ] AC4 reconciler deadline termination, no orphans -> test `<name>`
- [ ] AC5 `vault_health` queue depth + last-flush age -> test `<name>`
- [ ] AC6 clean-shutdown drain -> test `<name>`

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

## Test status

- Test suite: `<command> -> <output / coverage %>`
- Manual smoke test: what was exercised, what was observed
- No regressions in existing test suite: yes / no (if no, document)

## Decisions made during implementation

-

## Promotion candidates

- [ ] Lesson for the repo's `docs/lessons.md`? <yes / no - one line of what>
- [ ] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? yes — ADR-018 is gating, authored before implementation rather than promoted after
- [ ] New pattern candidate for `00_meta/patterns/`? Only if this recurs in >1 project. <yes / no - one line>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-322-commit-outbox/` -> `specs/archive/HIVE-322-commit-outbox/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [ ] Promotions above executed (if any)
