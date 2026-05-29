---
id: adr-010-external-committer-coexistence
type: adr
status: active
created: "2026-05-21"
---

# ADR-010: External Committer Coexistence (obsidian-git, pre-commit hooks)

## Status

Proposed (2026-05-21) — Phase A of HIVE-115. Companion to [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) (the SQLite half of the same systemic issue). Will be amended by [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) v2 (Outbox + Reconciler) with the detect-and-defer pattern, in the same release bundle (v1.16.0).

## Context

[adr-006-commit-policy.md](adr-006-commit-policy.md) established Hive's commit policy as "best-effort, never fail the write" and introduced the opt-in `commit: bool = True` parameter + `vault_commit` MCP tool + obsidian-git auto-detection (HIVE-104, ADR-006 §6). The detection was treated as **informational only**: surface presence in `vault_health`, no behavioral consequence.

Two months of production use revealed that the informational stance is insufficient. The vault under Hive's care is the same vault under obsidian-git's care — they share `.git/index.lock` and there is no coordination between them.

### Empirical evidence (issue #110, 2026-05-21)

Local obsidian-git plugin config in `~/Projects/knowledge/.obsidian/plugins/obsidian-git/data.json`:

```json
{
  "autoSaveInterval": 10,        // minutes
  "autoPullInterval": 10,
  "autoPullOnBoot": true,
  "pullBeforePush": true,
  "syncMethod": "merge"
}
```

Operational implications:

1. obsidian-git fires a backup every 10 minutes. Each backup is `pull → commit → push` (because `pullBeforePush=true`), holding `.git/index.lock` for ~5-15 seconds. The "10-minute interval" is the inter-trigger gap, NOT the lock-hold duration.
2. `autoPullOnBoot=true` extends the lock window at session start.
3. Hive's `_GIT_LOCK.acquire(timeout=30)` (`_helpers.py:551`, hardcoded `_LOCK_TIMEOUT=30`) blocks during the obsidian-git window. When it abandons after 30s, hive logs a WARNING and proceeds without a commit — silent data trail loss in `git log`.

Reported as issue #110 with Windows repro: silent 30-second freezes per call coinciding with obsidian-git ticks, propagating into the 838s `capture_lesson` outlier of issue #111 (combined with `tool_span` non-preemption; see [adr-008-hard-deadline-enforcement.md](adr-008-hard-deadline-enforcement.md)).

### Why "best-effort" is now a bug

ADR-006's "best-effort, never fail the write" was correct in isolation: if git is busy, hive should not fail the user's `vault_write`. But:

- The user expects "I wrote it, it's in git". Silent commit abandonment violates that contract subtly.
- The 30-second wait BEFORE the abandonment is itself a UX failure.
- Without telemetry, there is no signal to operators that the coexistence is contentious.
- The pre-existing `detect_obsidian_git()` informational surface (HIVE-104) is unused — its data should DRIVE behavior, not just describe it.

### Constraint: cannot bypass `.git/index.lock`

Hive must not bypass git's filelock — that would risk index corruption visible to all parties (Obsidian UI, hive, user's manual git commands). The fix lives in cooperation, not circumvention.

## Decision

### 1. Promote `detect_obsidian_git()` from informational to first-class design concept

`obsidian_git_present: bool` is surfaced in `vault_health(include_runtime=True)` alongside other runtime telemetry from [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md). This is the foundation; subsequent ADRs (v2 amendments) use this signal to drive behavioral changes.

In Phase A (this ADR), the promotion is observability-only — `detect_obsidian_git()` is read at every `vault_health` call (cached 30s) and its presence is exposed. Behavioral coupling is deferred to Phase B ([adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) v2 amendment, detect-and-defer pattern).

### 2. `HIVE_LOCK_TIMEOUT_S` env-tunable

Hive's `_LOCK_TIMEOUT` (currently hardcoded 30 in `_helpers.py:551`) becomes `HiveSettings.lock_timeout_s` (env: `HIVE_LOCK_TIMEOUT_S`, default 30, capped at 600). Users with large vaults experiencing extended obsidian-git windows (e.g. large pull on a slow network) can raise the value. Users wanting fail-fast can lower it.

Capped at 600 to prevent foot-guns (a 1-hour timeout would freeze the user's entire session).

### 3. Structured logging per `_GIT_LOCK` acquire attempt

Every call to `_GIT_LOCK.acquire(timeout=ctx.lock_timeout_s)` emits exactly one structured log:

```json
{"event": "mcp.lock_contention", "tool": "<tool_name>", "lock": "_GIT_LOCK",
 "waited_ms": 12345, "abandoned": false, "obsidian_git_present": true}
```

- `waited_ms` is captured with `time.monotonic()` brackets around the acquire call.
- `abandoned=true` indicates the timeout fired and the write was not committed (best-effort behavior preserves).
- `obsidian_git_present` correlates the contention with the suspected source.

These events are the input data for the Phase B gate decision (when to ship Outbox + Reconciler) and for ops users grepping their logs.

### 4. README + docs update: cooperation pattern is the recommended setup

The bilingual docs site (EN + ES) gets an updated section under Configuration:

> **Recommended pairing: obsidian-git + hive**
>
> If your vault uses the obsidian-git plugin for automatic backups, the two cooperate cleanly when configured properly:
>
> - Set obsidian-git's `autoSaveInterval` to 5-10 minutes.
> - Use `commit=False` on `vault_write` / `vault_patch` for write-heavy flows. Hive writes to disk; obsidian-git commits on its next tick.
> - Watch `vault_health(include_runtime=True)` → `last_git_lock_wait_ms`. If p99 stays above 5 seconds, raise `HIVE_LOCK_TIMEOUT_S` to 60 to absorb longer windows.
> - Phase B of HIVE-115 (forthcoming) will make this cooperation automatic.

This is the operational answer to ADR-006's recommended setup, with concrete numbers.

## Alternatives considered

### A) Status quo (informational detection only)

**Rejected.** The detection exists but drives no behavior. At N=3-5 baseline with obsidian-git active, the silent contention is reported as user pain (issue #110). Telemetry + tunable is the minimum responsible response.

### B) Auto-defer to obsidian-git when detected (skip ahead to Phase B behavior)

**Deferred to [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) v2 amendment.** Conditionally rewriting hive's commit path based on external committer presence is a semantic change with risk surface (what if obsidian-git is paused, broken, or removed mid-session?). Phase A keeps behavior unchanged; Phase B introduces the conditional defer with health-probe fallback.

### C) Bypass `.git/index.lock` (force-acquire)

**Rejected categorically.** Bypassing the lock would risk index corruption visible to Obsidian UI and user's manual git commands. Never compete-blindly — always cooperate-or-fallback.

### D) Disable hive's commits entirely when obsidian-git detected

**Rejected.** Loses crash-recovery semantics (each write was a commit you could `git log`). Loses the `vault_commit` escape hatch for clients that need an explicit flush. ADR-006's "preserve invariant by default" stance still applies in Phase A.

### E) Backoff + retry on lock contention (3s → 6s → 12s)

**Deferred to Phase B ([adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) v2).** Useful when the external committer is NOT present and hive's reconciler must keep trying. In Phase A, the simpler `HIVE_LOCK_TIMEOUT_S` tunable covers the common case without introducing retry-loop complexity.

## Consequences

- **Positive**: contention with obsidian-git becomes observable via structured logs + `vault_health` metrics. Operators can grep `mcp.lock_contention` to investigate slowness. The data informs Phase B gate decisions.
- **Positive**: `HIVE_LOCK_TIMEOUT_S` gives users a quick escape hatch for large-vault or slow-network situations without code changes.
- **Positive**: docs site cooperation pattern reduces user confusion about "why is hive slow when Obsidian is also running".
- **Neutral**: zero behavior change for users without obsidian-git (or with `commitInterval=0`). The promotion is gated on detection.
- **Negative**: `HIVE_LOCK_TIMEOUT_S` foot-gun (too-low = constant abandons, too-high = long freezes). Mitigated by documented recommended ranges and the 600s cap.
- **Marginal**: every `_GIT_LOCK.acquire` now produces a log line. Volume is bounded by write frequency (~1-5/min at N=3-5). Negligible disk impact; not paginated to a log file because there's already a per-PID file.
- **Forward path**: ADR-009 v2 takes the `obsidian_git_present` + recency probe + lock-wait telemetry from this ADR and turns them into automatic detect-and-defer behavior. Phase A is the foundation; Phase B is the closure.

## References

- [adr-006-commit-policy.md](adr-006-commit-policy.md) — original commit policy + first introduction of `detect_obsidian_git()`; **amended in this release** to acknowledge §C "Re-evaluate" gate triggered
- [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) — companion ADR (SQLite half); shares the `HIVE_LOCK_TIMEOUT_S` + `mcp.lock_contention` infrastructure
- [adr-008-hard-deadline-enforcement.md](adr-008-hard-deadline-enforcement.md) — `bounded_call` supervisor; the deadline-enforcement piece that lets reconciler operations have a hard ceiling
- [lessons.md](../lessons.md) — "Cooperative external committer needs explicit coordination" (load-bearing rationale) + "Telemetry IS the design" (instrumentation discipline)
- Spec: `specs/HIVE-115-latency-tail-redesign/` (forthcoming)
- obsidian-git plugin: https://github.com/Vinzent03/obsidian-git
- Issue #110: https://github.com/mlorentedev/hive/issues/110
