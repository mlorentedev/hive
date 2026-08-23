---
tags: [spec, verification, templates]
created: "2026-08-23"
---

# Verification - HIVE-384-nan-worker-and-delegate-verb

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior).

- [ ] AC1 (hive delegate honours the wire contract) -> commit `<hash>` / test `<name>`
- [ ] AC2 (exit codes separate pool-unavailable from task-failed) -> commit `<hash>` / test `<name>`
- [ ] AC3 (timeout kills the worker and returns without waiting) -> commit `<hash>` / test `<name>`
- [ ] AC4 (routes through the daemon, degrades honestly without one) -> commit `<hash>` / test `<name>`
- [ ] AC5 (the worker reaches NaN) -> commit `<hash>` / test `<name>`
- [ ] AC6 (Ollama and OpenRouter gone from every surface) -> commit `<hash>` / test `<name>`
- [ ] AC7 (the credential never appears in output) -> commit `<hash>` / test `<name>`

## Test status

- Test suite: `<command> -> <output / coverage %>`
- Manual smoke test: what was exercised, what was observed
- No regressions in existing test suite: yes / no (if no, document)

## Decisions made during implementation

Brief log of non-obvious trade-offs or course corrections taken during the work. Routine choices belong in commit messages, not here.

-
-

## Promotion candidates

Before archiving, flag what (if anything) should be promoted to the vault. If all three are "no", archive in repo is the only persistence.

- [ ] Lesson for the repo's `docs/lessons/`? <yes / no - one line of what>
- [ ] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? <yes / no - one line of what>
- [ ] New pattern candidate for `00_meta/patterns/`? Only if this recurs in >1 project. <yes / no - one line>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-384-nan-worker-and-delegate-verb/` -> `specs/archive/HIVE-384-nan-worker-and-delegate-verb/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [ ] Promotions above executed (if any)
