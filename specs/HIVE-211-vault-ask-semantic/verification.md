---
tags: [spec, verification]
created: "2026-06-05"
---

# Verification - HIVE-211-vault-ask-semantic (Stage 1)

> Skeleton — fill during implementation. Spec is `draft` (scaffolded 2026-06-05).

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, observed behavior).

- [ ] AC1 (answer with cited sources) -> test `<name>` / commit `<hash>`
- [ ] AC2 (disabled-default never breaks) -> test `<name>`
- [ ] AC3 (Ollama ↔ NaN by config) -> test `<name>`
- [ ] AC4 (lazy/incremental index; zero overhead when disabled) -> test `<name>`
- [ ] AC5 (no anyOf in schema) -> test `<name>`
- [ ] AC6 (cites real, existing files) -> test `<name>`

## Test status

- Test suite: `make test -> <output>`
- Base install (no `[semantic]` extra): imports + all existing tools work -> <yes/no>
- Manual smoke: `vault_ask("...")` against a real vault with NaN configured -> <observed>

## Decisions made during implementation

- NaN `/embeddings` available? -> <to fill>
- Vector store choice -> <to fill: sqlite-vec | numpy>
- Chunking strategy -> <to fill>

## Promotion candidates

- [ ] Lesson for `docs/lessons.md`? <yes/no>
- [ ] ADR-worthy (optional-dep + provider-agnostic semantic subsystem)? <likely yes — record the optional/graceful-degradation contract>
- [ ] New pattern for `00_meta/patterns/`? <maybe — "optional heavy capability behind a graceful-degradation gate" if it recurs>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved to `specs/archive/HIVE-211-vault-ask-semantic/`
- [ ] Backlog entry in vault `11-tasks.md` ticked with PR link
- [ ] Stage 2 decision recorded (escalate / defer / drop) based on instrumentation
