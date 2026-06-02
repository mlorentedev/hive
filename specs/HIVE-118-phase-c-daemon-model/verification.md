---
tags: [spec, verification, templates]
created: "2026-05-29"
---

# Verification - HIVE-118-phase-c-daemon-model

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior). Fill during implementation.

- [ ] Single-owner daemon serves multiple clients -> test `<name>` + `lsof`/process assertion
- [ ] Cross-session observability surface (survives disconnect) -> test `<name>`
- [x] Transparent fallback to stdio when no daemon -> tests `test_client_falls_back_without_daemon` + `test_client_falls_back_on_stale_state` (slice 2)
- [x] Client auto-reconnect (closes M1): mid-session reconnect to a restarted daemon, write-safe under retry -> tests `test_client_reconnects_to_restarted_daemon_without_duplicate_write` + `test_client_degrades_in_process_when_daemon_dies_mid_session` (slice 3)
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
- 2026-06-02 (slice 1.3 impl, restart-on-upgrade — `feat/restart-on-upgrade`): the production daemon now OWNS its `uvicorn.Server`, built from the public `mcp.http_app(path=MCP_PATH)` (replacing `mcp.run(transport="http")`), so a background `_watch_for_upgrade` task can set `should_exit` — the spike-proven cooperative stop, while uvicorn's default signal handlers stay installed so `systemctl stop` still works. **Exit-code decision (user-confirmed):** a drift-triggered clean stop exits **75 (EX_TEMPFAIL)**, NOT 0. The slice text loosely said "exit(0)", but under the `Restart=on-failure` unit a 0 would NOT relaunch; 75 keeps the unit free of `RestartPreventExitStatus=` coupling and is consistent with the slice 1.2 convention — decline + signal-stop → 0 (no restart), drift + crash → non-zero (restart). `main()` re-raises `SystemExit` before the CRITICAL crash log so a clean restart code is not mislabelled a crash. `timeout_graceful_shutdown=2s` bounds the stop (streamable-http holds connections open; the drain is unreachable anyway). The drift predicate `_upgrade_detected` treats a **rollback** as drift (different code than the running process) and the `<not-found>` swap-window sentinel as **not** drift (never bounce a healthy daemon on a transient unreadable read). Drift detection is stdlib-only (`importlib.metadata.version` + `invalidate_caches`); the spike (`spike/upgrade_spike.py`, 2/2) is the e2e evidence, 8 unit tests cover the new predicate/watcher/exit-code seams. `make check` green (676 passed, 2 skipped; mypy --strict + ruff clean).
- 2026-06-01 (slice 1.2, startup self-heal): a singleton collision (second `hive serve` on a different auto-port) declines with **exit 0**, not a non-zero code. Rationale: consistency with the existing port-in-use guard ("exits cleanly"), and a no-op start that exits 0 will not loop under systemd `Restart=on-failure` (a non-zero would, unless the unit also set `RestartPreventExitStatus=`, coupling code to unit config). The decline reason is carried by a `hive.daemon.singleton.declined` WARNING + a stderr line, not the exit code. Index.lock self-heal is gated on the singleton lock (proves single ownership) and spares a lock naming a live PID via `_index_lock_is_live` — real git index.locks carry no parseable PID, so they read as stale and clear, while a rare tool-written live PID is preserved.
- 2026-06-02 (slice 3, client auto-reconnect — closes M1): the proxy's backend is resolved per forwarded call by `_ReconnectingBackend.__call__` (FastMCP invokes `client_factory` on every `_get_client`), so re-reading the published port+token there follows a restarted daemon to its new port+token — the seam spike-proven 3/3 cross-OS (`spike/reconnect_spike.py`, #186). Three design choices on top of the proven mechanism: **(1) dual-owner edge → prefer-daemon routing** (not teardown, not flock-gating). While the daemon is reachable every call is forwarded to it, so the in-process fallback (a full git+SQLite owner) performs no writes; it is built lazily, only on first unreachability, and cached — a healthy-daemon session never creates a second owner. Rejected *flock-gating the fallback*: the singleton `daemon.lock` is exclusive and the daemon *declines (exit 0)* if it can't take it (slice 1.2), so a fallback holding the flock would block the canonical daemon from starting — backwards. Rejected *async teardown of the standby*: it adds a real mid-call-close race to reclaim resources (idle SQLite connections + reconciler/checkpoint threads) that are already cross-process safe (HIVE-115/116 WAL hardening). The narrow up-transition race (a multi-shim fallback write in flight as the daemon returns) is covered by the idempotency key (slice 2) + `.git/index.lock` self-heal (slice 1.2). **(2) cold start stays direct** (`run_client` keeps its startup probe): no daemon at startup → `_serve_in_process()` directly, exactly today's behavior, unchanged. Only a daemon reachable at startup engages the reconnecting proxy. This is the conservative, backward-compatible choice; the residual one-shot (a cold shim won't auto-upgrade to a daemon that appears later) is benign because the supervised daemon is up before sessions spawn, and it self-corrects next session. M1 as defined (daemon dies *after* the startup probe) is fully closed. **(3) mode logged on transition only**: the factory logs `hive.client.mode=…` on the first call and whenever it changes (a new `endpoint=` port marks a reconnect), so recovery is observable without a line per forwarded call.
- 2026-06-02 (slice 2, write idempotency — ADR-013): chose **2a** (at-most-once via a `UNIQUE` idempotency key in a SQLite store) over 2b (durable journal + replay), with 2b documented as the telemetry-gated evolution. Implementation switched from claim-first to **check-then-record-on-success** (`is_applied` at the top of the write critical section → write → `claim`) after a patch test caught that a retried patch's `find` text is already gone, so the idempotency check MUST precede the per-tool validation. Recording the key only after the disk write — with no `await` between `write_text` and `claim` — means a restart cutting the git commit still leaves the key recorded, so the retry cleanly no-ops. `release` kept as the retention-pruning primitive (the store-growth follow-up).
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
