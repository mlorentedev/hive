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
- [ ] Write failing test: daemon starts, owns the SQLite DBs, answers a `tools/list`
- [ ] Implement `hive serve` daemon entrypoint (long-lived; single owner of `ServerContext`)
- [ ] Write failing test: thin client connects over transport and round-trips one `vault_query`
- [ ] Implement client/stdio-shim that forwards MCP over the transport
- [ ] Write failing test: 2 concurrent clients, single daemon process owns DBs+git (multi-client integration)
- [ ] Write failing test: cross-session aggregated metrics survive a client disconnect
- [ ] Implement cross-session observability surface (`hive status` / `/status` / extended `worker_status`) over an internal metrics core
- [ ] Write failing test: no daemon -> client falls back to in-process stdio, response flags degraded mode
- [ ] Implement transparent fallback path + client auto-reconnect when daemon returns
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
