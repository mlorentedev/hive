---
id: adr-005-transport-and-scale
type: adr
status: proposed
created: "2026-05-18"
owner: manu
tags: [architecture, scalability, mcp, transport, concurrency]
---

# ADR-005: Transport Model and Multi-Process Scalability

## Status
Proposed (2026-05-18) — written after diagnosing inter-process hangs (see [lessons.md](../lessons.md), "Multi-process MCP server contention surfaces").

## Context

[adr-001-orchestration-model.md](adr-001-orchestration-model.md) established Hive as an MCP server with on-demand vault access. [adr-004-thread-safety-model.md](adr-004-thread-safety-model.md) addressed **intra**-process concurrency (multiple MCP tool calls within one server process). This ADR addresses **inter**-process concurrency — the gap that surfaced once Claude Code users started running 2–5 parallel sessions against the same vault.

### The current model (stdio, multi-process)

```
Claude Code session A  ─►  uvx hive-vault (PID 22768)  ─┐
Claude Code session B  ─►  uvx hive-vault (PID 29000)  ─┼─►  ~/Projects/knowledge/ (shared git)
Claude Code session C  ─►  uvx hive-vault (PID 31504)  ─┘    ~/.local/share/hive/*.db (shared SQLite)
```

The MCP spec assumes stdio transport = one server subprocess per client. When a user runs N parallel Claude Code sessions, they get N hive-vault subprocesses. All N share:

- The vault git repo (`git add` / `git commit`).
- Three SQLite databases (`worker.db`, `relevance.db`, usage tracker).
- Optionally Ollama / OpenRouter quotas (less interesting).

The patches that became PR-after-this-ADR (filelock, busy_timeout, tool_span on vault tools, respond-after-cancel shim) make this model **correct but not unlimited**. There is a ceiling.

### What we just fixed and what remains

| Bug | Fix shipped | Residual risk |
|---|---|---|
| Vault tool calls had no timeout | `wrap_sync_tool` decorator → `tool_span` on all 7 vault tools | None |
| Multi-process git races (5+ historical 30s timeouts in log) | `filelock` at `vault/.git/hive.lock` | Serializes writes — throughput ceiling, see below |
| SQLite `OperationalError: database is locked` | `PRAGMA busy_timeout=10s` + connect `timeout=10` | Same — serializes writers |
| `AssertionError: Request already responded to` killed the server after cancel | `_compat.py` patches `respond` to short-circuit when `_completed=True` | None; self-gated, removable when upstream fixes |
| Log line did not identify the hung tool | `LifecycleMiddleware` now logs `tool=<name>` | None |

The serialization fixes mean operations **wait** instead of failing. Waiting has a budget.

## Scale analysis

Numbers below are order-of-magnitude estimates; they would need a benchmark to validate. They are good enough to make a decision.

### Per-operation cost (warm vault, SSD, Windows 11)

| Operation | Time | Bottleneck |
|---|---|---|
| `vault_query` (one file, ≤1k lines) | 5–20 ms | filesystem read |
| `vault_search` (full text, ~200 files) | 200 ms – 2 s | rglob + per-file read |
| `vault_write` + git commit | 100–400 ms | `git add` + `git commit` subprocess |
| `vault_patch` + git commit | 150–500 ms | same |
| `session_briefing` | 50–300 ms | rglob (count_stale) + SQLite + git log |
| SQLite tracker write | 1–5 ms | WAL append |

### Sessions vs sustained write throughput

Writes are now serialized inter-process via the filelock. A `vault_write` releases the lock when the git commit returns. So the system's write throughput is roughly **1 / mean(write_duration)** = ~3–5 writes/second across all sessions combined, regardless of how many sessions are open.

| Sessions | Reads (parallel) | Writes (serialized) | User-perceived behavior |
|---|---|---|---|
| **1–3** | Fine | Fine — collisions are rare | Indistinguishable from single-user |
| **5** | Fine | Occasional 200–500 ms wait on `vault_write` | Imperceptible unless capturing many lessons in a burst |
| **10** | Mostly fine; `vault_search` may slow if disk is shared | Writes start queuing — bursts can add 1–3 s tail latency | Noticeable in capture_lesson-heavy sessions |
| **25** | OK if vault stable | Write tail latency hits 5–10 s during bursts | Users start to perceive lag; risk of filelock timeout (30s) |
| **50+** | Read pressure on rglob | Writes near the filelock timeout; some commits skipped | Architecture wall |

For Manu's actual usage (1–5 parallel Claude Code sessions on a single machine), the current architecture **after the fixes** is sufficient indefinitely. The interesting questions only arise if Hive is shared across a team or if a single user runs >10 sessions.

### Where the wall is

Three independent ceilings, hit at different scales:

1. **Filelock serialization (~5 writes/s)** — the binding constraint. Comes from `git commit` being inherently single-writer per repo.
2. **SQLite WAL write serialization (~50 writes/s)** — only a constraint if a tool does many small SQLite writes per call. Currently never the bottleneck.
3. **Vault git repo size (~10k files? gut estimate)** — `git status` and `count_stale` scale with file count; not a concurrency problem but does affect per-operation latency, which compounds the above.

## Alternatives

### Option A — stay on stdio multi-process (current, post-fix)

**Code change to ship:** what we just landed.

**Pros**
- Zero deploy complexity; works in every MCP client without configuration.
- Process isolation: a hive crash takes down one session, not all.
- Matches how every Claude Code user already runs Hive.

**Cons**
- Inter-process coordination via filelock + SQLite busy_timeout is correct but adds latency under contention.
- Each session pays the `uvx hive-vault` cold-start cost (~1 s on Windows).
- Hard ceiling at ~5 sustained writes/s across the machine.

**Best when:** ≤10 parallel sessions per machine, single user or trusted small team sharing a vault.

### Option B — HTTP transport + single hive-daemon

**Code change:** add `hive serve --http` mode that runs one persistent daemon on `localhost:NNNN`; each Claude Code client connects via FastMCP's HTTP transport instead of spawning a subprocess. The daemon holds the SQLite connections and the git repo, so locks become intra-process again ([adr-004-thread-safety-model.md](adr-004-thread-safety-model.md)'s model applies directly).

**Pros**
- Eliminates inter-process contention entirely — back to one process owning all state.
- No per-session cold start.
- Single observable process: logs, metrics, profiling, OOMs all consolidate.
- Throughput ceiling rises to ~50 writes/s (SQLite + threading, not git serial — git can stay serial via the existing `_GIT_LOCK`).
- Natural place to add features that need shared state across sessions: cross-session activity feed, shared inflight queue, cross-session deduplication of expensive worker calls.

**Cons**
- HTTP transport in MCP is **less universally supported**. Claude Code supports it; Gemini CLI, Codex CLI, Cursor, Windsurf vary. Some clients support only stdio. This would split the user base unless we offer **both** transports.
- Single point of failure: daemon crash kills all sessions simultaneously. Mitigated by supervisor (systemd unit / Windows service / Caddy reverse proxy with restart-on-fail).
- New deploy surface: users need to start the daemon (manually or via launchd/systemd/Task Scheduler).
- Auth becomes a real concern. localhost-only with a per-machine token is straightforward but new.
- Logs across sessions become harder to attribute without request_id correlation.

**Best when:** ≥10 parallel sessions per machine, team sharing a vault, or any deployment where a server admin owns the host.

### Option C — pivot to event sourcing / outbox

Defer git commits to a background worker. Writes touch only SQLite (the outbox); a single dedicated background goroutine drains the outbox into git at its own pace.

**Pros**
- Removes git from the hot path entirely. `vault_write` returns in <50ms regardless of git contention.
- Outbox naturally serializes commits without filelock.

**Cons**
- Writes are no longer immediately visible to other sessions reading from git (they would have to read from outbox + git).
- Materially more code: outbox schema, drain worker, failure handling, replay semantics.
- Loses the "every write is a commit you can `git log`" property that makes the current vault model legible.

**Best when:** sustained write-heavy workloads where git commit latency dominates. Not justified at current scale.

## Decision tree

```
                ┌─ ≤10 parallel sessions / machine? ──── yes ──► STAY (Option A)
                │
   How many ───┤
                │                                       ┌── all clients support HTTP? ── yes ──► MIGRATE (Option B)
                └─ 10 – 50 sessions ─────────────────┤
                                                        └── mixed clients? ──────────────────► OFFER BOTH (A + B)

   >50 sessions / machine ────────────────────────────────────────────────► Option C or rethink (multi-tenant
                                                                            hive backend, not single-machine MCP)
```

## Recommendation

**Stay on Option A.** Ship the six fixes (filelock, busy_timeout, tool_span on vault tools, respond-after-cancel patch, tool_name in log, catch-all in `_try_worker`). This is the right level of investment for the current usage envelope (1–5 parallel sessions per developer).

**Plan Option B as a v2 milestone** if either of these triggers fire:
- Sustained complaints about write tail latency from teams or power users.
- A real use case for shared-cross-session state (e.g. a "what is every Claude session in this org working on right now?" feature).

Add `hive serve --http` as a second transport mode rather than replacing stdio — keeps single-user friction at zero, opens the door for the team-shared model. The investment is bounded: FastMCP already supports HTTP transport natively, and the existing intra-process threading model from [adr-004-thread-safety-model.md](adr-004-thread-safety-model.md) applies unchanged to the daemon.

**Do not pursue Option C** unless we see write-latency regressions that the daemon model alone cannot fix.

## Consequences

- Inter-process coordination becomes part of Hive's contract. The lock files (`vault/.git/hive.lock` and SQLite WAL/SHM) must be respected by any tool that touches the same backing store outside Hive (a user manually running `git commit` in the vault while a `vault_write` is in flight will block, not corrupt).
- The 6-fix bundle is the **last** patch this architecture should need at small scale. The next architectural decision (Option B vs. growing complexity in Option A) should be driven by measured contention, not by intuition.
- Telemetry needs to improve before we can confidently make that decision: the new `tool=` log line is a start, but we want a `--show-stats` mode that surfaces filelock acquire-wait time and SQLite busy-time so contention is visible without log diving.

## Amendments

### 2026-05-21 — HIVE-115 telemetry gates triggered + baseline re-dimensioned

Empirical evidence collected during HIVE-115 investigation invalidates the optimistic scale projections in §"Scale analysis" of this ADR:

- **Actual baseline is N=3-5 concurrent sessions per user, not the "1-3 = fine" projection.** Local lsof snapshot 2026-05-21 confirms 3 simultaneous hive-vault processes holding open handles to all 3 SQLite DBs. The system is operating at the codo of this ADR's own scale table during normal daily use.
- **`relevance.db-wal` reaches 4.1 MB vs 53 KB steady-state DB (77× ratio)** under N=3-5. `worker.db-wal` was last checkpointed 2 months ago.
- **838s `capture_lesson` outlier observed (issue #111)** — `tool_span` non-preemption confirmed; the "defense in depth" of §"What we just fixed" is correct at each layer but does not compose into a global deadline.
- **Anticipated future state**: agent-driven hive workloads at N=10-15 concurrent clients per machine. The current stdio multi-process model has a hard architectural ceiling well below that even with all 4 primitives of the multi-process-mcp-server pattern applied.

### Gates invoked

**Option C (event sourcing / outbox) gate condition** was: *"Re-evaluate only if write-latency regressions that the daemon model alone cannot fix."* Telemetry above satisfies the gate. Option C is no longer rejected — see [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) v1 (Phase A defensive, shipping v1.16.0) and ADR-009 v2 amendment (Phase B Outbox + Reconciler, same bundle).

**Option B (HTTP daemon) gate conditions** were: *"Sustained complaints about write tail latency from teams or power users"* + *"a real use case for shared-cross-session state."* The agent-driven future state satisfies both prospectively. Option B is re-affirmed as the inevitable v1.17.0 / v2.0 target, tracked as ADR-011 (daemon model, forthcoming). The decision tree's "≥10 sessions" trigger is recognized as a near-term inevitability, not a far-future contingency.

### Companion changes in v1.16.0 bundle

- [adr-008-hard-deadline-enforcement.md](adr-008-hard-deadline-enforcement.md) — `bounded_call` supervisor replacing `tool_span`'s asyncio-only enforcement
- [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) — periodic WAL checkpoint + telemetry + tunable lock
- [adr-010-external-committer-coexistence.md](adr-010-external-committer-coexistence.md) — obsidian-git detection promoted to first-class
- [adr-006-commit-policy.md](adr-006-commit-policy.md) §C gate also triggered (see ADR-006 amendments)
- the multi-process-mcp-server pattern extended with primitives 5-7

This ADR's §"Recommendation" remains: stay on stdio for v1.16.0, ship A+B together, observe under real load, then make the Option B (daemon) call with data — for what is now an inevitable destination, not a contingent one.

## References

- [adr-001-orchestration-model.md](adr-001-orchestration-model.md) — original Hive orchestration model
- [adr-004-thread-safety-model.md](adr-004-thread-safety-model.md) — intra-process locking (still in force; this ADR extends it)
- [adr-006-commit-policy.md](adr-006-commit-policy.md) — companion commit-policy ADR; co-amended in this release
- [adr-008-hard-deadline-enforcement.md](adr-008-hard-deadline-enforcement.md) — Phase B deadline supervisor
- [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) — Phase A WAL drain + telemetry
- [adr-010-external-committer-coexistence.md](adr-010-external-committer-coexistence.md) — Phase A obsidian-git coordination
- [transport-closed-after-reject.md](../troubleshooting/transport-closed-after-reject.md) — issue #75, the prior cancellation race
- Log incident `2026-05-18 10:43` — first observed `AssertionError('Request already responded to')` killing a hive process after a 39-min hang

<!-- Provenance (maintainer's cross-project knowledge store; not linked to preserve repo->store independence): pattern-phased-redesign-with-telemetry-gates (the meta-pattern guiding HIVE-115). HIVE-115 backlog tracked in the forge (GitHub issues / milestones). -->
