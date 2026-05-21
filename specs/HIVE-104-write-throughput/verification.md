---
tags: [spec, verification, templates]
created: "2026-05-20"
---

# Verification - HIVE-104-write-throughput

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior).

- [ ] Criterion 1 -> commit `<hash>` / test `<name>`
- [ ] Criterion 2 -> commit `<hash>` / test `<name>`
- [ ] Criterion 3 -> commit `<hash>` / test `<name>`

## Test status

- Test suite: `<command> -> <output / coverage %>`
- Manual smoke test: what was exercised, what was observed
- No regressions in existing test suite: yes / no (if no, document)

## Decisions made during implementation

- **2026-05-20 — Fase C design retracted and rewritten before any code shipped.** During spec drafting, a Sonnet subagent located the upstream framing utility (`BaseSession._send_response` at `mcp/shared/session.py:337-349`) and surfaced that `RequestResponder.cancel()` already calls `_send_response(ErrorData)` at session.py:148-150 before our patched `respond()` fires. The empirical classifier `tests/test_compat_shim.py::test_classify_cancellation_race` ran 20 iterations against a real hive subprocess on Linux; **20/20 produced scenario (a)** (ErrorData wins the race; wire response is always `{"id": N, "error": {"code": 0, "message": "Request cancelled"}}`). The original ADR-007 §1 plan ("best-effort raw send") would have generated a duplicate response in 100% of cases. Fase C scope reduced from ~80 LOC (raw stdio framing + safe write) to ~30 LOC (WARNING log + counter + docstring update). ADR-007 Amendment #2 captures the retraction.

## Promotion candidates

Before archiving, flag what (if anything) should be promoted to the vault. If all three are "no", archive in repo is the only persistence.

- [ ] Lesson for `hive/90-lessons.md`? <yes / no - one line of what>
- [ ] ADR-worthy decision for `hive/30-architecture/adr-XXX.md`? <yes / no - one line of what>
- [ ] New pattern candidate for `00_meta/patterns/`? Only if this recurs in >1 project. <yes / no - one line>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-104-write-throughput/` -> `specs/archive/HIVE-104-write-throughput/`
- [ ] Backlog entry in vault `11-tasks.md` ticked with PR link
- [ ] Promotions above executed (if any)
