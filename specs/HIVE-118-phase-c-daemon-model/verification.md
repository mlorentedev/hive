---
tags: [spec, verification, templates]
created: "2026-05-29"
---

# Verification - HIVE-118-phase-c-daemon-model

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior). Fill during implementation.

- [ ] Single-owner daemon serves multiple clients -> test `<name>` + `lsof`/process assertion
- [ ] Cross-session observability surface (survives disconnect) -> test `<name>`
- [ ] Transparent fallback to stdio when no daemon -> test `<name>`
- [ ] Cross-OS transport validated (Linux + Windows CI) -> CI lane `<name>`
- [ ] Supervised recovery: SIGKILL -> restart + state integrity + client continuity -> test `<name>`
- [ ] Post-mortem crash artifact (required fields, no secrets) -> test `<name>`
- [ ] Correlated structured logging (correlation_id + session_id) -> test `<name>`
- [ ] ADR-011 written + merged -> vault path `10_projects/hive/30-architecture/adr-011-*.md`

## Test status

- Test suite: `<command> -> <output / coverage %>`
- Manual smoke test: what was exercised, what was observed
- No regressions in existing test suite: yes / no (if no, document)

## Decisions made during implementation

Brief log of non-obvious trade-offs or course corrections taken during the work.

- 2026-05-29 (pre-implementation): telemetry showed the latency-tail gate for Phase C does NOT fire (lock contention ~0, WAL tiny, 0 timeouts). Phase C re-justified on operating-model grounds (observability + shared state + lifecycle), not latency. The acute concurrency symptom was traced to `uvx --upgrade` startup serialisation and mitigated separately (dropped `--upgrade` in `~/.claude.json` + daily `uv tool upgrade` cron). This decoupling means Phase C is a deliberate v2.0 architecture choice, not a forced firefight.
-

## Promotion candidates

Before archiving, flag what (if anything) should be promoted to the vault.

- [ ] Lesson for `10_projects/hive/90-lessons.md`? yes — likely: "the contention you designed against can be cured by mitigation, moving the bottleneck to startup; re-measure before escalating an architecture phase" (telemetry-gate discipline).
- [ ] ADR-worthy decision for `10_projects/hive/30-architecture/adr-011-*.md`? YES — ADR-011 is a deliverable of this spec (daemon model, transport, fallback).
- [ ] New pattern candidate for `00_meta/patterns/`? Maybe — "language-server-style daemon for local MCP" (single hot owner + thin clients) if it recurs beyond hive.

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-118-phase-c-daemon-model/` -> `specs/archive/HIVE-118-phase-c-daemon-model/`
- [ ] Backlog entry in vault `11-tasks.md` ticked with PR link
- [ ] Promotions above executed (ADR-011 at minimum)
