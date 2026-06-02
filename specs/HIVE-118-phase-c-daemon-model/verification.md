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
- 2026-06-02 (slice 1.3 spike — `spike/upgrade_spike.py`, 2/2 PASS): two findings. (1) `importlib.metadata.version()` reflects an in-place `*.dist-info` swap **immediately mid-process** (no `invalidate_caches()` needed) → a version-drift poll is a viable, stdlib-only upgrade detector. (2) A true in-flight **DRAIN is NOT achievable** over FastMCP's streamable-http: owning the `uvicorn.Server` (via the public `mcp.http_app()`) + `should_exit` yields a **clean stop** (`serve()` returns → exit 0), but the MCP session manager **cancels the active tool handler** on lifespan shutdown — neither the client response nor server-side completion is guaranteed. **Consequence:** restart-on-upgrade is NOT a self-contained "drain/swap"; its in-flight safety depends on idempotency (§6.2 at-most-once) + auto-reconnect. The reliable primitive we build on is *clean-stop*, not *drain*. Sequencing implication raised with the user.
- 2026-06-01 (slice 1.2, startup self-heal): a singleton collision (second `hive serve` on a different auto-port) declines with **exit 0**, not a non-zero code. Rationale: consistency with the existing port-in-use guard ("exits cleanly"), and a no-op start that exits 0 will not loop under systemd `Restart=on-failure` (a non-zero would, unless the unit also set `RestartPreventExitStatus=`, coupling code to unit config). The decline reason is carried by a `hive.daemon.singleton.declined` WARNING + a stderr line, not the exit code. Index.lock self-heal is gated on the singleton lock (proves single ownership) and spares a lock naming a live PID via `_index_lock_is_live` — real git index.locks carry no parseable PID, so they read as stale and clear, while a rare tool-written live PID is preserved.
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
