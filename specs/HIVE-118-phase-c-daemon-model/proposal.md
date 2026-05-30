---
id: "HIVE-118-phase-c-daemon-model"
type: spec
status: draft # draft | implementing | verifying | archived
created: "2026-05-29"
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-118: Phase C daemon model

> **Naming**: file lives at `<repo>/specs/HIVE-118-phase-c-daemon-model/proposal.md`.

<!-- from 10_projects/hive/11-tasks.md: "Phase C (ADR-011 hive-vault daemon model) deferred to v2.0 pending real-world telemetry. Automated +14d checkpoint on 2026-06-05 (#124). Single hive-vault daemon per machine; N clients via Unix socket (POSIX) / named pipe (Windows). Daemon sole owner of SQLite + git -> eliminates multi-process contention class entirely. ADR-011 supersedes parts of ADR-005's stay-on-stdio recommendation." -->
<!-- from 30-architecture/adr-005-transport-and-scale.md: stdio + process-per-client is the v1 decision; daemon (Option C) listed as the v2 milestone with explicit triggers. ADR-011 (daemon) is NOT YET WRITTEN — this spec drives that decision. -->
<!-- checkpoint: GitHub #124 (Phase C decision, due 2026-06-05) + routine trig_016wfbYEBsQkGGvSoVW1VbUa. -->
<!-- 2026-05-29 telemetry finding (this session): the multi-process CONTENTION class is already neutralised by HIVE-115/116 (lock_contention abandoned=0, max wait 264ms, largest WAL 62KB, 0 timeouts over ~1481 calls). The remaining concurrency pain is STARTUP cost from `uvx --upgrade` serialising on uv's exclusive tools lock — mitigated this session by dropping `--upgrade` + daily `uv tool upgrade` cron. THEREFORE: Phase C's justification is NOT latency-tail (that gate does not fire). The driver is the OPERATING MODEL: centralised cross-session observability, shared state, single-owner lifecycle, zero cold-starts. Frame the proposal on that axis, not on latency. -->

## Why

Hive today runs **one stdio process per Claude Code session** (ADR-001/ADR-005). At the maintainer's everyday baseline of 3–5 concurrent sessions this produces three structural costs that mitigation cannot fully remove: (1) **fragmented observability** — usage stats live in per-process buffers (`_LOG_BUFFER_MAX=50`, lost on exit), logs are ~250 orphaned per-PID files, and `vault_health(include_runtime)` only ever sees a single process, so there is no way to answer "what is hive doing across all my sessions right now?"; (2) **no shared state** — each process keeps its own relevance EMA, re-scans the vault, and cannot reuse a warm index; (3) **N cold-starts + version skew** — every session spawns its own interpreter and (until this session's fix) could even land on a different published version mid-release. Phase C replaces the N-process model with a **single long-lived daemon per machine** that owns SQLite + git and serves N thin clients, making centralised monitoring, shared state, and a single operational lifecycle first-class rather than bolted-on. This is the v2.0 milestone pre-registered in ADR-005 and tracked by checkpoint #124.

## What

Concrete behavior change after this PR:

1. A new long-lived process — `hive` daemon (`hived` / `hive serve`) — runs as a per-user service (systemd `--user` on Linux, launchd on macOS, service/Scheduled Task on Windows) and is the **sole owner** of the vault git working tree and all SQLite trackers (`worker`, `relevance`, `lesson_reinforcement`, `lock_evictions`, `usage`).
2. Claude Code sessions connect to the daemon as **clients** over a local transport (Unix domain socket / Windows named pipe, or loopback Streamable-HTTP) instead of each spawning its own `hive-vault`. A thin stdio shim keeps the existing `~/.claude.json` MCP contract working unchanged.
3. The daemon exposes a **cross-session observability surface** — a `/status` (and/or `hive status` CLI and/or extended `worker_status` MCP tool) reporting, live and aggregated across all connected sessions: per-tool call counts + latency percentiles, active sessions, OpenRouter budget, vault/git health, WAL state, and version — from one source of truth that does not lose data when individual sessions close.
4. A **fallback path**: if no daemon is reachable, the client transparently falls back to the current in-process stdio server, so a dead daemon degrades to today's behavior rather than breaking hive.
5. **Resilience + post-mortem debuggability are first-class** (see dedicated pillar below): the daemon is built crash-only — it auto-restarts under a supervisor, leaves a structured post-mortem artifact on every abnormal exit, exposes liveness/readiness, and every request carries a correlation id traceable across sessions. If it dies, you can reconstruct *what it was doing* and *why it died* from disk alone.

## Observability, disaster recovery & debuggability (design pillar)

> A single daemon serving N sessions is a single point of failure. This section is **load-bearing**, not an appendix: the daemon is only acceptable if a crash is observable, bounded, recoverable, and forensically reconstructable. Designed per crash-only-software principles (assume it will die; make dying cheap and traceable).

**Disaster recovery / resilience**
- **Supervised auto-restart** — systemd `--user` `Restart=on-failure` (launchd `KeepAlive` / Windows service recovery). Restart is the primary recovery path; restart must be fast (<1s readiness) and idempotent.
- **Transparent client fallback** — a client that cannot reach the daemon falls back to in-process stdio (item 4) so a daemon outage degrades, never breaks. Clients reconnect automatically when the daemon returns.
- **Crash-safe state** — durable state (SQLite WAL, git working tree) must survive `SIGKILL` with no corruption; informational state (counters, EMA buffers) may be lost but never block startup. Reuses the existing Outbox crash-loss contract + HIVE-116 partial-state write contract; the daemon must not weaken either.
- **Restart-on-upgrade** — a defined, atomic story for replacing a running daemon with a new published version (drain in-flight → stop → swap → start), so upgrades don't corrupt state or strand clients.
- **Startup self-heal** — on boot the daemon detects and clears its own stale locks / zombie state from a prior unclean exit (extends `_clean_stale_wal_files` + cooperative-filelock eviction).

**Observability (live)**
- **Structured logging** — single daemon log (replaces ~250 per-PID files), JSON-structured, leveled, with per-request `correlation_id` + `session_id` so one request is traceable end to end and across sessions.
- **Metrics surface** — `/metrics` (Prometheus-style) and/or `hive status`: per-tool call counts + latency p50/p95/p99, active session count, error/timeout counts, budget snapshot, WAL/git health, uptime, version. Aggregated across all sessions, persistent across disconnects.
- **Liveness + readiness** — distinct health probes (process alive vs ready-to-serve) the supervisor and clients can poll.

**Post-mortem / debuggability (when it dies)**
- **Black-box recorder** — an in-memory ring buffer of the last N requests/responses + lock events, flushed to a timestamped crash artifact on any abnormal exit (uncaught exception, signal, deadline storm). This is the autopsy record.
- **Crash artifact** — on abnormal exit write a self-contained dump (last-N from the recorder, open handles, lock state, DB/WAL sizes, in-flight requests, stack/traceback, version, uptime) to a known path for offline analysis.
- **Live introspection** — `hive status --verbose` / a debug endpoint to dump current state (active requests, lock holders, queue depths) from a *running* daemon without attaching a debugger.
- **Correlation across the stack** — the same `correlation_id` appears in the client, the daemon log, the metrics, and the crash artifact, so an incident can be reconstructed from any entry point.

**Telemetry storage model — three planes (decided, to avoid the "DB vs log" trap)**

> Each plane answers a different operational question and uses a different store. They are deliberately separate: forcing all three into one store either slows the hot path (DB on every call) or makes time-range queries impossible (everything in logs).

| Plane | Question it answers | Store | Cost / placement |
|---|---|---|---|
| **1. Live metrics** | "What is happening *right now*?" — p50/p95/p99 per tool, active sessions, queue depth, current lock holders, budget, WAL size, uptime | **In-memory** counters/histograms, exposed via `/metrics` + `hive status` | Zero disk, zero latency; updated inline on the hot path |
| **2. Forensic trail** | "What happened / why did it crash?" | **JSON-lines log + crash artifact** (append-only) | Cheap append; analysed offline with `jq` (no DB needed for forensics at single-user scale) |
| **3. Historical telemetry** | "How does this trend over time / across restarts?" — latency drift as the vault grows, crash count this week, usage per project | **Local SQLite — reuse the existing `usage.db` (`UsageTracker`)**, made durable by the long-lived daemon | Off the hot path — async/reconciler writes only; **never a synchronous INSERT per call** (the exact anti-pattern HIVE-115 removed) |

**Explicitly out:** Prometheus/Grafana server, OpenTelemetry collector, or a dedicated time-series DB — over-engineering for a single-user local daemon. The in-memory `/metrics` surface stays Prometheus-*format* compatible so a future team/multi-machine edition can scrape it without redesign.

## Out of scope

- **Remote / multi-machine / multi-user daemon.** This spec is a *local, single-user* daemon owning local resources (vault, git, SQLite); Ollama stays remote as today. A networked "team edition" (vault over the network, auth, TLS) is a separate future product.
- **Changing the MCP tool surface / semantics.** Same tools, same parameters; only the transport and process model change. (Tool-parameter ergonomics — the `Invalid arguments` noise — is a separate ticket.)
- **Replacing the cooperative-filelock / WAL / deadline machinery wholesale.** Those stay as the fallback-mode safety net; the daemon makes them mostly inert but does not delete them in this PR.

## Risks / open questions

- **[MUST RESOLVE before code] Cross-OS transport.** Unix socket (POSIX) vs named pipe (Windows) — the same Windows file-handle terrain that caused HIVE-116. Decide transport (raw socket vs loopback Streamable-HTTP, which FastMCP supports natively) and validate the Windows path before committing to implementation. This is the #1 design risk.
- **[MUST RESOLVE before code] Single point of failure + lifecycle.** Daemon crash takes down hive for all sessions until restart. Requires the full resilience pillar above: supervisor auto-restart, client-side fallback-to-stdio (item 4), crash-safe durable state, restart-on-upgrade, and startup self-heal. A daemon without the post-mortem/recovery design is NOT acceptable to ship.
- **Forensic completeness vs overhead.** The black-box recorder + correlation ids must capture enough to reconstruct an incident without (a) measurably slowing the hot path or (b) writing secrets/API keys into crash artifacts. Open question: ring-buffer size N, what fields are safe to record, retention/rotation of crash artifacts.
- **Observability surface choice.** `/metrics` (Prometheus/HTTP) vs `hive status` CLI vs extended `worker_status` MCP tool — likely all three over one internal metrics core, but decide the primary in ADR-011.
- **Concurrency model inside the daemon.** One process now multiplexes all sessions' calls — confirm the async event loop + existing thread-safety (ADR-004) hold when serving N clients, and that one slow git op cannot head-of-line-block other sessions.
- **Decision gate.** ADR-011 is not yet written. Open question: does the #124 telemetry justify Phase C *now* on latency grounds (finding: **no**), and is the operating-model case (observability + shared state) sufficient on its own to escalate to a v2.0 build (maintainer: leaning **yes**)? Resolve via ADR-011 before freezing tasks.md.

## Acceptance criteria

Observable outcomes. Each must be testable.

- [ ] **Single-owner daemon serves multiple clients.** With the daemon running, ≥2 simultaneous client sessions complete vault reads/writes through it and **exactly one** `hive` daemon process owns the SQLite DBs + git (verifiable: process/`lsof` assertion + a passing multi-client integration test).
- [ ] **Cross-session observability surface.** A single call (`hive status` / `/status` / extended `worker_status`) returns aggregated metrics covering ≥2 active sessions — per-tool counts + latency, active-session count, budget, version — from the daemon, persisting across a session disconnect (test: open 2 clients, close 1, metrics still reflect its calls).
- [ ] **Transparent fallback.** With no daemon running, a client call still succeeds via the in-process stdio path, and the response/health indicates degraded (non-daemon) mode (test: kill daemon, issue a tool call, assert success + mode flag).
- [ ] **Cross-OS transport validated.** The chosen transport passes its integration test on Linux AND (CI matrix) Windows — extending the existing `cross_worker_lock` cross-OS lane pattern.
- [ ] **Supervised recovery.** Killing the daemon (`SIGKILL`) triggers auto-restart by the supervisor, the daemon comes back ready in <Ns, durable state (SQLite + git) is intact with no corruption, and in-flight clients reconnect or fall back without manual intervention (test: kill under load, assert restart + state integrity + client continuity).
- [ ] **Post-mortem artifact on crash.** An abnormal daemon exit produces a crash artifact at a known path containing the last-N request ring buffer, lock/DB state, in-flight requests, version, and traceback — and contains **no secrets/API keys** (test: induce a fault, assert artifact exists with required fields and no key material).
- [ ] **Correlated, structured logging.** Every request is logged once with a `correlation_id` + `session_id` traceable across the daemon log and metrics; a single daemon log replaces per-PID files (test: issue a call, assert one correlated structured log line + same id in `hive status`).
- [ ] **Three-plane telemetry separation.** Live metrics are served from memory (no per-call disk write on the hot path — verifiable: hot path issues no synchronous DB INSERT); the forensic trail is JSON-lines + crash artifact; historical telemetry persists in `usage.db` across a daemon restart (test: record calls, restart daemon, assert history survives while the live counters reset).
- [ ] **ADR-011 written and merged** capturing the daemon decision, transport choice, fallback contract, resilience/observability design, and what it supersedes in ADR-005.

## References

- Vault: `10_projects/hive/11-tasks.md` (Phase C entry, "Active" stream) + `10_projects/hive/10-roadmap.md` (Phase 6 production hardening)
- Related ADR: `10_projects/hive/30-architecture/adr-005-transport-and-scale.md` (Option C / v2 trigger); ADR-011 (daemon) — **to be authored by this spec**
- Related patterns: `00_meta/patterns/pattern-multi-process-mcp-server.md`, `00_meta/patterns/pattern-phased-redesign-with-telemetry-gates.md`
- GitHub issue (collaboration layer, per `pattern-three-layer-proposal-lifecycle`): #124 (Phase C decision checkpoint, due 2026-06-05) — this spec's `proposal.md` is the RFD/RFC layer per `pattern-change-lifecycle`
- Related DX issue surfaced same investigation: #151 (clients guess wrong tool param names) — out of scope here
- Prior phases: `specs/archive/HIVE-115-latency-tail-redesign/` (Phases A+B), `specs/archive/HIVE-116-stale-lock-after-deadline/`
