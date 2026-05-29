---
id: adr-009-multi-process-wal-policy
type: adr
status: active
created: "2026-05-21"
---

# ADR-009: SQLite WAL Checkpoint Policy Under Multi-Process Orchestration

## Status

Proposed (2026-05-21) — Phase A v1 of HIVE-115. Will be **amended (v2)** by Phase B Outbox + Reconciler design once Phase A telemetry validates the contention model. v2 amendment is pre-scheduled in the same release bundle (v1.16.0); v1 is the standalone defensive policy.

## Context

[adr-004-thread-safety-model.md](adr-004-thread-safety-model.md) established intra-process SQLite safety via `threading.Lock` + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=10000`. [adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) established the inter-process orchestration model (stdio, one MCP server subprocess per Claude Code session) and dimensioned the scale envelope as "1-3 sessions = fine, 5 = occasional waits, 10+ = visible lag". The maintainer's multi-process-mcp-server pattern codified primitives 1-4 for this orchestration.

Two months of production use revealed that **the actual baseline daily usage is N=3-5 concurrent Claude Code sessions per user, not the edge of the scale table**. At that N, the WAL behavior under stress is not what ADR-005's table predicted.

### Empirical evidence (2026-05-21 local snapshot)

Local Linux session of hive maintainer, mid-day with 3 Claude Code windows open:

```text
$ ps -eo pid,etime,cmd | grep hive-vault
 475646       25:45 .../hive-vault    ← 25 min old
 529429        6:45 .../hive-vault    ←  7 min old
 540650        3:39 .../hive-vault    ←  4 min old

$ lsof ~/.local/share/hive/*.db
hive-vault 475646  ... worker.db  relevance.db  lesson_reinforcement.db
hive-vault 529429  ... worker.db  relevance.db  lesson_reinforcement.db
hive-vault 540650  ... worker.db  relevance.db  lesson_reinforcement.db

$ ls -lah ~/.local/share/hive/*.db*
12K   lesson_reinforcement.db
157K  lesson_reinforcement.db-wal   ← 13× ratio
53K   relevance.db
4.1M  relevance.db-wal              ← 77× ratio
8.2K  worker.db
91K   worker.db-wal                 ← 11× ratio, last modified 2026-03-13 (2 months ago)
```

**Every concurrent hive process holds open file handles to ALL three SQLite databases**, regardless of which trackers a given tool call needs. The reader snapshots never release because the processes never exit. `relevance.db-wal` reaches 77× the size of the steady-state DB; `worker.db-wal` was last checkpointed 2 months ago.

Reported on Windows by an independent user (issue #110): identical pattern, plus contention with obsidian-git (10-min `autoSaveInterval` + `pullBeforePush=true`) producing 838s `capture_lesson` outliers (issue #111).

### Why the prior plan is insufficient

PRAGMA `journal_mode=WAL` + `wal_autocheckpoint=200` (multi-process-mcp-server pattern §2) does **not** mean "WAL stays small". Any concurrent reader holding a snapshot blocks checkpoint advancement past the frame it's reading. With process-per-client orchestration at N=3-5 baseline, there is virtually always a snapshot holder; the WAL grows unboundedly.

A naive mitigation — "call `PRAGMA wal_checkpoint(TRUNCATE)` on shutdown" — is **virtually inert at N=3-5 baseline** because there is rarely a "last process to shut down". A user always has at least one Claude Code window open. The shutdown drain never fires; the WAL grows indefinitely.

Future state (per HIVE-115 backlog): agent-driven hive workloads may reach N=10-15 concurrent clients per machine. The current architecture must scale at least to N=3-5 baseline without growth pathology; the eventual daemon pivot (ADR-011) addresses the higher tail.

### Constraint: cannot break ADR-005's stay-on-stdio invariant

Phase A is **defensive**, not architectural. Whatever this ADR decides must work within the current stdio multi-process orchestration model. Changes that require a new transport, a new lifecycle, or breaking the per-session subprocess contract belong in ADR-011 (Phase C daemon model), not here.

## Decision

### 1. PRIMARY: Periodic `PRAGMA wal_checkpoint(PASSIVE)` thread per hive process

Each `_SqliteTracker` subclass starts a `threading.Thread(daemon=True)` on init. The thread runs an infinite loop with a 30-second sleep, executing `PRAGMA wal_checkpoint(PASSIVE)` against the tracker's connection on each tick. PASSIVE does NOT block readers and advances checkpoint as far as the current frame state allows.

This is the **primary drain mechanism** under N>1 baseline. PASSIVE is cooperative: it advances what it can without disturbing concurrent reads, so it never causes contention. At N=3-5 over multiple hours, even partial advancement drains the steady-state WAL growth.

Thread is `daemon=True` so it dies with the parent process — does not leak as zombie. Sleep interval is tunable via `HIVE_WAL_CHECKPOINT_INTERVAL_S` (default 30).

### 2. SECONDARY: `PRAGMA wal_checkpoint(TRUNCATE)` on graceful shutdown

Each `_SqliteTracker.close()` invokes `PRAGMA wal_checkpoint(TRUNCATE)` before closing the connection. TRUNCATE only fully succeeds when no other connection holds a snapshot — under N=3-5 it usually degrades to PASSIVE-equivalent behavior. Wrapped in a 2-second wall-clock guard via `threading.Timer` so a hung checkpoint cannot block shutdown.

When N IS 0 (the last hive process shuts down), TRUNCATE truncates the WAL fully — the only opportunity for full drain. Not the primary mechanism, but valuable when it fires.

### 3. Observability via `vault_health(include_runtime=True)`

The runtime block surfaces:

- `wal_size_bytes`: sum of `*.db-wal` file sizes in `~/.local/share/hive/` (cheap stat call).
- `competing_pid_count`: distinct PIDs (excluding self) holding open file handles on our DBs, via `psutil.process_iter(['name','pid','open_files'])`. Filtered strictly by `name() == "hive-vault"` (cross-OS stem match) + same UID + LRU-cached 30s to amortize `process_iter` cost (~100ms on Windows).
- `last_git_lock_wait_ms`: rolling N=100 ring buffer of `_GIT_LOCK.acquire()` wait times. Mean + p99.
- `obsidian_git_present`: boolean from `detect_obsidian_git()` (already implemented in HIVE-104).

These metrics directly inform the Phase B gate (when to ship Outbox + Reconciler) and the Phase C trigger (when to ship daemon).

### 4. Tunable lock timeout + structured logging

`HiveSettings.lock_timeout_s` (env: `HIVE_LOCK_TIMEOUT_S`, default 30) replaces the hardcoded `30` in `_helpers.py`. Capped at 600 to prevent foot-guns.

Every `_GIT_LOCK.acquire(timeout=...)` attempt emits a structured log:

```json
{"event": "mcp.lock_contention", "tool": "...", "lock": "_GIT_LOCK",
 "waited_ms": 12345, "abandoned": false}
```

Phase B gating consumes these events; ops users can grep them.

### 5. New dependency: `psutil`

Added to `pyproject.toml` runtime deps. ~750KB pure-Python wheel + small C extension. Cross-platform process introspection. Already a standard MCP-server dep elsewhere. Filtered strictly to avoid antivirus / backup-tool false positives in `competing_pid_count`.

## Alternatives considered

### A) `?mode=ro` for non-writer connections

**Rejected.** Initial Phase B design assumed `?mode=ro` connections would not block checkpoints. **This is wrong:** read-only SQLite connections still hold WAL snapshots that block `wal_checkpoint` from advancing past their frame. `?mode=ro` only prevents accidental writes — it does not reduce contention. The real fix is **short transactions** (open → query → close per call), which is the Phase B (ADR-009 v2) refinement.

### B) `PRAGMA wal_checkpoint(TRUNCATE)` on shutdown alone

**Rejected as PRIMARY mechanism, kept as fallback (§2).** At N=3-5 baseline there is rarely a "last process to shut down", so the drain virtually never fires. PASSIVE periodic (§1) is the actual drain.

### C) Outbox + Reconciler pattern (Phase B preview)

**Deferred to ADR-009 v2 amendment.** Outbox + Reconciler is the structural fix that lets us OPEN-CLOSE-PER-CALL connections (short transactions), eliminates the long-lived snapshot problem at source, and unifies SQLite + git coordination. But it is a larger semantic change with risk surface; it belongs in its own design pass after Phase A telemetry validates the contention model.

Importantly: ADR-006 §C explicitly considered an outbox/event-sourcing pattern and rejected it twice (2026-05-18, 2026-05-20) with the gate condition: "Re-evaluate only if measurements after Option B ship show sustained dolor that obsidian-git + commit=False cannot resolve." The empirical evidence collected for HIVE-115 (4.1 MB WAL, 838s outliers, 3-5 baseline N) formally invokes that gate. ADR-009 v2 will document the re-evaluation and supersede ADR-006 §C's rejection.

### D) HTTP daemon transport (Phase C, ADR-011)

**Deferred.** [adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) explicitly listed this as Option B and proposed it as a v2 milestone. The user's future state (agent-driven N=10-15) makes it inevitable, but it is a transport pivot that requires its own ADR and its own validation cycle. Phase A+B telemetry under real load is the input to Phase C's design. Tracked as ADR-011 (forthcoming).

### E) Per-tracker dedicated writer process (CQRS variant)

**Rejected** in the user-conversation phase of HIVE-115 (2026-05-21). CQRS-style write/read DB split would solve the WAL bloat for write-DBs (single writer, no contention) but only moves the problem to the read-DB. Eventual-consistency cost is high (breaks "write-then-read" contract for `capture_lesson` → `vault_search`), and the data is KB-scale (worker.db=8KB, relevance.db=53KB) so the complexity is not justified at current size. Worth revisiting only at GB-scale data or N>20 baseline. See ADR-009 v2 (forthcoming) "Alternatives considered" for the long-form rejection.

## Consequences

- **Positive**: WAL bloat is observable (via `vault_health`) and drained periodically (via PASSIVE thread). At N=3-5 baseline, steady-state WAL size stays bounded instead of growing 77×. `worker.db-wal`'s 2-month-stale state self-resolves on next checkpoint tick. `last_git_lock_wait_ms` distribution becomes input to Phase B parameter sizing.
- **Positive**: ADR-006 §C gate is formally invoked with data, not intuition. Phase B (ADR-009 v2 amendment) has clean rationale for re-evaluating prior rejection of outbox.
- **Positive**: `psutil` dep enables future telemetry (per-tracker contention, slow query detection) without re-arch.
- **Neutral**: Added background thread per tracker (3 threads per hive process). All `daemon=True`, ~negligible CPU (sleep 30s, ~ms work per tick). No new zombie classes.
- **Negative**: New runtime dep (psutil). ~750KB. Acceptable for the observability value.
- **Negative**: `HIVE_LOCK_TIMEOUT_S` foot-gun risk if user sets it too low (5s) → hive abandons every commit during obsidian-git ticks. Mitigated by documented recommended range in troubleshooting.md.
- **Marginal failure mode**: PASSIVE checkpoint thread could theoretically race with a concurrent SQLite operation if both target the same WAL frame. SQLite documentation explicitly states PASSIVE is safe to run concurrently with readers/writers — verified by `PRAGMA wal_checkpoint` documentation. No new failure mode in practice.

## References

- [adr-001-orchestration-model.md](adr-001-orchestration-model.md) — original Hive orchestration model (unchanged by this ADR)
- [adr-004-thread-safety-model.md](adr-004-thread-safety-model.md) — intra-process SQLite safety; this ADR adds inter-process drain mechanism
- [adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) — established stdio multi-process model; **amended in this release** to re-baseline scale table for N=3-5 daily usage + acknowledge agent-driven future
- [adr-006-commit-policy.md](adr-006-commit-policy.md) — **amended in this release**; ADR-006 §C "Re-evaluate" gate is invoked formally with HIVE-115 telemetry
- [adr-007-mcp-cancellation-response.md](adr-007-mcp-cancellation-response.md) — ghost-response handling; unchanged
- [adr-010-external-committer-coexistence.md](adr-010-external-committer-coexistence.md) — companion ADR for git lock contention (the other half of the same systemic issue)
- [adr-008-hard-deadline-enforcement.md](adr-008-hard-deadline-enforcement.md) — `bounded_call` supervisor; orthogonal but ships in same bundle (v1.16.0)
- [lessons.md](../lessons.md) — "SQLite WAL doesn't auto-checkpoint when N processes hold readers" (load-bearing rationale)
- Spec: `specs/HIVE-115-latency-tail-redesign/` (forthcoming)

<!-- Provenance (maintainer's cross-project knowledge store; not linked to preserve repo->store independence): pattern-multi-process-mcp-server (extended with primitives 5-7 in this release); pattern-phased-redesign-with-telemetry-gates (meta-pattern guiding HIVE-115). HIVE-115 backlog tracked in the forge (GitHub issues / milestones). -->
- `psutil` docs: https://psutil.readthedocs.io/
- SQLite WAL checkpoint modes: https://www.sqlite.org/pragma.html#pragma_wal_checkpoint
