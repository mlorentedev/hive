---
id: adr-011-phase-c-daemon-model
type: adr
status: proposed
created: "2026-05-30"
owner: manu
tags: [architecture, daemon, transport, observability, resilience, mcp]
---

# ADR-011: Phase C — Single-Owner Daemon Model

## Status

Proposed (2026-05-30). Drives the `specs/HIVE-118-phase-c-daemon-model/` work. **Supersedes the "Stay on Option A" recommendation of [adr-005-transport-and-scale.md](adr-005-transport-and-scale.md)** by adopting its Option B (the `hive serve` daemon). One residual `[MUST RESOLVE]` blocks acceptance: a transport spike must validate the chosen loopback-HTTP + token path on Windows (the file-handle terrain that produced HIVE-116). `tasks.md` stays unfrozen until that spike passes and this ADR is accepted.

## Context

[adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) analysed the stdio multi-process model (one `uvx hive-vault` per Claude Code session, all sharing one vault git repo + three SQLite DBs) and recommended **Option A** (stay on stdio, ship the six contention fixes) with **Option B** (a single persistent daemon) pre-registered as a v2 milestone gated on two triggers: sustained write-tail-latency complaints, or a real need for shared cross-session state.

Two things changed the calculus:

1. **The latency trigger does NOT fire.** The HIVE-115/116 redesign neutralised the multi-process contention class. Telemetry from ~1481 calls on the daily-use machine (2026-05-29): `lock_contention abandoned=0`, max git-lock wait 264 ms, largest WAL 62 KB, 0 tool timeouts. The acute "slow in simultaneous sessions" symptom traced to `uvx --upgrade` serialising on uv's exclusive tools lock at cold-start — mitigated separately (dropped `--upgrade`, daily `uv tool upgrade` cron). **Phase C is therefore NOT justified on latency grounds.**

2. **The operating-model cost remains, and is structural.** At the everyday baseline of 3–5 concurrent sessions, the N-process model imposes three costs that contention fixes cannot remove:
   - **Fragmented observability** — usage stats live in per-process buffers (lost on exit), ~250 orphaned per-PID log files, and `vault_health(include_runtime)` only ever sees one process. There is no way to answer "what is hive doing across all my sessions right now?"
   - **No shared state** — each process keeps its own relevance EMA, re-scans the vault, cannot reuse a warm index.
   - **N cold-starts + version skew** — every session spawns its own interpreter and (pre-fix) could land on a different published version mid-release.

   A live incident on 2026-05-30 sharpened a fourth: **no single owner of the vault git working tree.** Two concurrent sessions writing the same vault repo, with one switching branches, made a committed write from another session invisible (the working tree followed the branch checkout). N writers on one git working tree is fragile by construction — exactly what a single owner removes.

The decision this ADR records: **escalate Option B now on operating-model grounds**, not latency.

## Decision

Adopt a **single long-lived `hive` daemon per machine** (`hive serve`) that is the sole owner of the vault git working tree and all SQLite trackers (`worker`, `relevance`, `lesson_reinforcement`, `lock_evictions`, `usage`). Claude Code sessions become **thin clients**. Five load-bearing choices:

### 1. Process model

One daemon owns all backing state; intra-process thread-safety ([adr-004-thread-safety-model.md](adr-004-thread-safety-model.md)) applies directly again, and the inter-process filelock/WAL/deadline machinery (ADR-008/009/012) becomes mostly inert — kept as the fallback-mode safety net, not deleted in this PR.

### 2. Transport — loopback Streamable-HTTP + per-daemon token

The daemon listens on `127.0.0.1:PORT` using FastMCP's **native** Streamable-HTTP transport. Decided 2026-05-30 over Unix-socket/named-pipe and per-OS-hybrid alternatives because it is:

- **FastMCP-native** — zero custom transport code; the same code path runs on Linux, macOS, and Windows (no named-pipe handle terrain — the HIVE-116 failure class).
- **Observability for free** — `/status` and `/metrics` are HTTP endpoints on the same server (see §4).

A bare loopback port is reachable by **any** local process/user, so it is not owner-restricted the way a `0600` Unix socket is. We close that gap with a **per-daemon bearer token**: the daemon writes a random token to `~/.local/share/hive/daemon.token` with mode `0600` (owner-only) and publishes its port to a sibling state file; the thin client reads the token and sends it as an `Authorization` header. Requests without the matching token are rejected. This resolves the transport `[MUST RESOLVE]` and the local-transport-security item together.

> **Residual `[MUST RESOLVE]`:** a spike must confirm the loopback-HTTP + token round-trip on Windows (port binding, token-file perms equivalent, firewall prompts) before this ADR is accepted and `tasks.md` is frozen.

### 3. Fallback contract

If the client cannot reach a daemon — none running, token mismatch, or a protocol-version mismatch — it **transparently falls back to the current in-process stdio server**, and the response/health flags degraded (non-daemon) mode. Clients reconnect automatically when the daemon returns. A dead daemon degrades to today's behaviour; it never breaks hive. The existing `~/.claude.json` MCP contract is preserved unchanged by a thin stdio shim.

### 4. Resilience, observability & post-mortem (load-bearing, not appendix)

A single daemon serving N sessions is a single point of failure, so it is built **crash-only**:

- **Supervised auto-restart** — systemd `--user` `Restart=on-failure` (launchd `KeepAlive` / Windows service recovery); readiness < ~1 s.
- **Crash-safe durable state** — SQLite WAL + git working tree survive `SIGKILL` uncorrupted; informational counters/EMA may be lost but never block startup. Reuses the Outbox crash-loss contract + HIVE-116 partial-state contract.
- **Startup self-heal** — clears its own stale locks / zombie state from a prior unclean exit.
- **Liveness + readiness probes**, distinct.
- **Crash artifact** — abnormal exit flushes a black-box ring buffer of the last-N requests + lock events to a known path, with **no secrets/API keys**.
- **Three-plane telemetry** (decided, to avoid the "DB vs log" trap): (1) live metrics in-memory → `/metrics` + `hive status`, no synchronous per-call disk write; (2) forensic JSON-lines + crash artifact; (3) historical telemetry reusing `usage.db`, written async/reconciler-side, durable across restarts.
- **Correlated structured logging** — one daemon log (replaces per-PID files), JSON, with per-request `correlation_id` + `session_id`.

Primary observability surface: **`/status` HTTP endpoint** (free with the chosen transport), mirrored by a `hive status` CLI and the existing `worker_status` MCP tool, all reading one internal metrics core.

### 5. Scope boundary

Local, single-user daemon only (Ollama stays remote). NOT in scope: remote/multi-user "team edition", changing the MCP tool surface (that is HIVE-119 / #151), or reconciling other sessions' vault branches.

## Consequences

### Positive

- One source of truth for cross-session observability; ~250 per-PID logs collapse to one correlated log.
- Shared warm state (relevance EMA, vault index) across sessions; zero per-session cold start; no cross-session version skew.
- Single owner of the git working tree eliminates the concurrent-checkout class that made a committed write vanish (2026-05-30 incident).
- Intra-process locking (ADR-004) replaces inter-process coordination as the common path.

### Negative

- New single point of failure — bounded by the resilience pillar (supervised restart + transparent stdio fallback), but real.
- New deploy surface — users must have the daemon started (service unit / launchd / Task Scheduler).
- New skew class — thin-client shim vs daemon version mismatch during rolling upgrades (needs a protocol-version handshake; tracked as an open question in the spec).
- Write idempotency across reconnect/fallback must be defined so a retried write after a mid-call daemon death is not duplicated.

### Neutral

- The fallback path keeps the full stdio code path alive, so the inter-process safety machinery (filelock/WAL/deadline) is retained, not removed.
- `/metrics` stays Prometheus-*format* compatible without running a Prometheus server — a future team edition can scrape it without redesign.

## References

- Spec: `specs/HIVE-118-phase-c-daemon-model/` (proposal + tasks + verification) — the RFD layer.
- Supersedes (in part): [adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) §Recommendation (Option B chosen).
- Builds on: [adr-004-thread-safety-model.md](adr-004-thread-safety-model.md), [adr-008-hard-deadline-enforcement.md](adr-008-hard-deadline-enforcement.md), [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md), [adr-012-cooperative-filelock-eviction-on-deadline.md](adr-012-cooperative-filelock-eviction-on-deadline.md).
- Checkpoint: GitHub #124 (Phase C decision, due 2026-06-05).
- Related DX work (separate): #151 / HIVE-119 (tool param aliases).
