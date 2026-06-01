---
tags: [spec, tasks, templates]
created: "2026-05-29"
---

# Tasks - HIVE-118-phase-c-daemon-model

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.
>
> **STATUS: FROZEN (2026-05-31).** ADR-011 is **accepted**; the design is locked. The transport spike passes on **Linux AND Windows** (5/5 both) and four companion spikes (load, idempotency, resilience, robustness) are green on both OSes. Implementation may begin in the TDD order below — change a step only with a one-line note here.

## Setup

- [ ] Branch created from main: `feat/HIVE-118-phase-c-daemon-model`
- [ ] `proposal.md` completed via `/spec fill` (Socratic pass — currently agent-scaffolded from vault context)
- [ ] **ADR-011 authored + merged** (daemon decision, transport choice, fallback contract, supersession of ADR-005) — gating
- [x] Open questions in `proposal.md` "Risks" resolved (cross-OS transport decided; SPOF/lifecycle strategy decided) — done in ADR-011 (2026-05-31); only the Windows transport spike remains

## Implementation (provisional skeleton — refine after fill + ADR-011)

> Keep small (one commit each), TDD order. Sequencing assumes a spike de-risks the transport first.

- [x] **Spike (transport):** loopback Streamable-HTTP + bearer-token round-trip. **Linux + Windows: DONE** (`spike/transport_spike.py`, 5/5 both — round-trip, missing/wrong-token 401, owner-only token file via `0600`/`icacls`). Closed ADR-011's transport residual.
- [x] **Spike (load/concurrency):** N parallel sessions → one daemon owning a shared SQLite. **Linux + Windows: DONE** (`spike/load_spike.py`, 4/4 both — zero failures, no lost writes, no head-of-line blocking). Pre-validated the single-owner concurrency model (ADR-011 §1).
- [x] **Spike (idempotency / resilience / robustness):** **Linux + Windows: DONE** — `idempotency_spike.py` 3/3 (§6.2 at-most-once incl. concurrent dupes → one row), `resilience_spike.py` 4/4 (§4 — state durable + `integrity_check=ok` after `SIGKILL`/`TerminateProcess`, reconnect, disconnect survival), `robustness_spike.py` 3/3 (1 MB payload, auth unbypassable, port-in-use exits cleanly). De-risked §6.2 + §4 + transport edges before building.
- [x] Write failing test: daemon starts, owns the SQLite DBs, answers a `tools/list` — **#164** (`tests/test_daemon.py::test_hive_serve_answers_tools_list`)
- [x] Implement `hive serve` daemon entrypoint (long-lived; single owner of `ServerContext`) — **#164** (`src/hive/_daemon.py`)
- [x] Write failing test: thin client connects over transport and round-trips one `vault_query` — slice 2 (`test_client_forwards_to_daemon`)
- [x] Implement client/stdio-shim that forwards MCP over the transport — slice 2 (`src/hive/_client.py`: `hive client` → `create_proxy` over token-gated HTTP)
- [ ] Write failing test: 2 concurrent clients, single daemon process owns DBs+git (multi-client integration)
- [ ] Write failing test: cross-session aggregated metrics survive a client disconnect
- [ ] Implement cross-session observability surface (`hive status` / `/status` / extended `worker_status`) over an internal metrics core
- [x] Write failing test: no daemon -> client falls back to in-process stdio, response flags degraded mode — **pulled forward into slice 2** (`test_client_falls_back_without_daemon` + `test_client_falls_back_on_stale_state`); degraded mode surfaced via the `hive.client.mode=fallback reason=...` startup log, not an MCP-visible flag (the rich surface is the observability slice above)
- [~] Implement transparent fallback path **[done — slice 2]** + client auto-reconnect when daemon returns **[deferred — resilience slice]**: the shim picks its mode once at startup (TCP liveness probe of the published port); mid-session reconnect when a dead daemon returns lands with the supervised-restart work below

> **Reorder note (2026-05-31, slice 2):** the fallback test + transparent-fallback path were pulled ahead of the multi-client / observability steps. Reason: a forwarding-only shim wired into `~/.claude.json` would break every session whenever the daemon is down, so fallback is a safety prerequisite for shipping the shim at all, not a later refinement. Auto-reconnect (mid-session daemon recovery) stays deferred to the resilience slice.

> **Audit follow-ups (2026-05-31, post-slice-2 review → hardening PR):**
> - **H1 [fixed].** TCP-only liveness probe + fastmcp's `client_init_timeout=None` could let a *wedged* daemon (TCP up, MCP mute) hang the shim unboundedly — a "break", not a "degrade". Fixed: `_serve_proxy` wraps the backend in `Client(transport, init_timeout=5s)` so the per-request handshake fails fast. Per-request `timeout` left unset on purpose (long `delegate_task` calls; the daemon owns their deadline).
> - **M1 [deferred — resilience slice].** The mode decision is one-shot at startup; if the daemon dies *after* the probe, forwards error (now fast, bounded by H1) but the shim does not fall back mid-session. Full mid-session auto-reconnect is task 34's deferred half.
> - **M2 [measure in slice 3].** `create_proxy(transport)` uses a fresh backend session per forwarded request (`ProxyClient.new()`) — great for concurrent-session isolation (multi-client), but each call pays a new HTTP connect + MCP `initialize`. The `load_spike` measured *direct* SQLite access, not *proxied* latency. Slice 3 must measure proxied p50/p99, not assume the spike numbers.
> - **M3 [resolved by design — no code].** A cleanly stopped daemon leaves stale token/port files: uvicorn exits via the SIGTERM signal (rc -15), bypassing `finally`/`atexit`, and SIGTERM is how systemd/`kill` stop it. This is benign — the client's TCP probe falls back (`daemon_unreachable`) and a daemon restart overwrites the files. Forcing cleanup would mean hijacking uvicorn's SIGTERM handler (breaking its graceful shutdown). The only effect is the fallback *reason* string after a no-restart stop.
- [ ] **Resilience:** structured single-daemon logging with `correlation_id` + `session_id` (replace per-PID logs)
- [ ] **Resilience:** liveness + readiness probes
- [ ] **DR:** write failing test — `SIGKILL` under load -> supervisor restart, durable state (SQLite+git) intact, clients reconnect/fallback
- [ ] **DR:** startup self-heal (clear own stale locks/zombie state from prior unclean exit) + restart-on-upgrade drain/swap
- [ ] **Post-mortem:** in-memory black-box ring buffer of last-N requests + lock events
- [ ] **Post-mortem:** write failing test — abnormal exit flushes a crash artifact with required fields AND no secrets/API keys
- [ ] **Post-mortem:** `hive status --verbose` / debug endpoint dumps live state (active requests, lock holders, queue depths) from a running daemon
- [ ] **Telemetry planes:** in-memory metrics core (plane 1) feeding `/metrics` + `hive status`; assert hot path does no synchronous DB write
- [ ] **Telemetry planes:** promote existing `usage.db` to durable historical store (plane 3), written async/reconciler-side; test history survives a daemon restart
- [ ] Service install/lifecycle: systemd `--user` unit (`Restart=on-failure`) + launchd/Windows recovery stubs
- [ ] **Cross-OS CI lane** (extend the `cross_worker_lock` Linux+Windows matrix). Port the five spike scenarios into `tests/` as pytest integration tests **against the real `hive serve` daemon** — transport+token round-trip, single-owner concurrency, idempotency (§6.2), crash-only durability (§4), and transport/auth edges. The spikes (`spike/*.py`) are the executable spec for these tests; once the daemon exists they become real coverage of hive's own code (not just FastMCP) and gate every PR on both OSes. Until then they stay runnable as-is for manual cross-OS checks.

## Closing

- [ ] Every acceptance criterion from `proposal.md` is covered by at least one test
- [ ] Every acceptance criterion has a matching entry in `features.json` with a non-vacuous verification command
- [ ] Type checks pass (`mypy --strict`)
- [ ] Lint passes (ruff)
- [ ] No unrelated changes in the diff (no scope creep)
- [ ] `verification.md` filled in
- [ ] ADR-011 merged + ADR-005 amended to point at it
- [ ] PR opened referencing this spec folder

## Machine-readable features

This spec emits a sibling `features.json` (alongside this file) following [[pattern-feature-list-as-primitive]]. Author it once tasks freeze (after fill + ADR-011). Each acceptance criterion maps to ≥1 feature with `id`, `behavior`, `verification` (executable command), `state` (lifecycle), and `evidence` (harness-captured output).

**Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running `verification` and capturing exit code 0, may set that terminal state.
