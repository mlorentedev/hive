---
tags: [spec, verification, templates]
created: "2026-06-24"
---

# Verification - HIVE-267-upgrade-swap

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior).

- [ ] Criterion 1 (upgrade-while-running leaves a valid install) -> commit `<hash>` / test `<name>`
- [ ] Criterion 2 (#267 reproduction no longer fails on in-use files) -> commit `<hash>` / test `<name>`
- [ ] Criterion 3 (failed swap leaves previous install intact + actionable error) -> commit `<hash>` / test `<name>`
- [ ] Criterion 4 (validated on real non-admin Windows hardware) -> observed behavior

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

- [ ] Lesson for the repo's `docs/lessons.md`? <yes / no - one line of what>
- [ ] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? Likely yes — the A3/A4 choice updates/closes ADR-015 mechanism (A).
- [ ] New pattern candidate for `00_meta/patterns/`? Only if this recurs in >1 project. <yes / no - one line>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-267-upgrade-swap/` -> `specs/archive/HIVE-267-upgrade-swap/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [ ] Promotions above executed (if any)
