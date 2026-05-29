---
id: adr-006-commit-policy
type: adr
status: active
created: "2026-05-20"
---

## Status
Accepted (2026-05-20) — written after user reports of MCP slowness and ghost responses during write-heavy flows on a vault with 315 lessons across 17 projects.

## Context

[adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) established that filelock serialization (~5 writes/s) is the binding throughput constraint of the current architecture, and explicitly considered an **Option C — pivot to event sourcing / outbox** that would defer git commits to a background worker. ADR-005 rejected Option C: *"Not justified at current scale… Do not pursue Option C unless we see write-latency regressions that the daemon model alone cannot fix."*

This ADR revisits the commit-batching question 2 days later, after the user reported real UX pain (slowness, occasional MCP crashes, ghost responses during write-heavy flows). The analysis on 2026-05-20 concluded that ADR-005's rejection of Option C was correct, but the underlying problem — per-write commit cost — can be addressed with a **much smaller incremental change** that preserves ADR-005's spirit (avoid in-process background lifecycle complexity).

### Problem surface

Per `_helpers._git_commit` (`src/hive/_helpers.py:702-753`), every successful `vault_write` / `vault_patch` / `capture_lesson(inline)` invokes **two sequential `subprocess.run` calls** under both `_GIT_LOCK` (in-process) and the cross-process `filelock`:

1. `git add <rel_path>` — fork+exec, index read/write, ~30–100 ms on healthy SSD
2. `git commit -m <msg>` — fork+exec, pack lookup, ~30–100 ms

Total: 50–200 ms healthy / 500 ms – several seconds under contention. Already-batched call sites (`vault_patch` multi-section, `capture_lesson(text=...)` batch mode) only do this **once per tool call**, so they are fine. The remaining hot path is **multiple sequential `vault_write` calls in one logical user-level operation** — e.g. an agent writing 5 sections of a doc as 5 separate tool calls.

### Discovery: vault already has an external committer

The vault (`~/Projects/knowledge`) is an Obsidian vault with **obsidian-git plugin** configured to auto-commit every 10 minutes. This is a strong signal:

- The user already pays the cost of an external batcher.
- Adding a Hive-internal background flusher would race with obsidian-git for `.git/index`, produce interleaved commits, and break the user's existing setup.
- The lateral analysis on 2026-05-20 surfaced this as the single biggest risk of building Option C.

### Failure modes observed

- Multi-write user flows (e.g. spec scaffolding that creates `proposal.md` + `tasks.md` + `verification.md` in sequence): linear slowdown, occasional client-side timeout.
- Ghost responses (see [adr-007-mcp-cancellation-response.md](adr-007-mcp-cancellation-response.md)): the >200 ms write window is long enough that client-side tool-call timeouts fire before `respond()` is called, exposing the upstream SDK cancellation race.

### Constraint

ADR-005's invariant **"successful `vault_write` returns committed state"** is load-bearing for:

- Multi-process safety: callers in other processes can `git pull` or read HEAD and see the write.
- Crash recovery: there is nothing to recover — every successful return is durable in git.
- Debuggability: `git log` is a faithful timeline of vault operations.

We preserve this invariant by default and **only relax it opt-in**.

## Decision

### 1. Default semantics unchanged — `write ⇒ commit`

`vault_write` / `vault_patch` / `capture_lesson` continue to commit synchronously by default. The invariant from ADR-005 is preserved as a feature, not removed.

### 2. Add opt-in `commit: bool = True` parameter

Both `vault_write` and `vault_patch` accept an optional `commit` parameter. When `commit=False`:

- File is written to disk (under `_WRITE_LOCK` — atomicity preserved).
- `_git_commit` is **not** called.
- Response payload includes `{"committed": false}` so the client knows.
- No background task is started; the user (or an external committer like obsidian-git) is responsible for the eventual commit.

This is the **minimum viable change** to unblock write-heavy flows without owning the lifecycle complexity that ADR-005 rejected.

### 3. Add explicit `vault_commit(message: str = "")` tool

A new MCP tool that runs `git add -A && git commit -m <message>` against the vault, returning the commit SHA. Provides an escape hatch for clients that opted out of auto-commit and want to flush explicitly without involving an external committer.

### 4. Coalescer in `_git_commit`

When `_git_commit` receives multiple paths in a single invocation (already happens in `vault_patch` multi-section and `capture_lesson(text=...)` batch mode), it issues **one `git add path1 path2 …` + one `git commit`** instead of looping. Free win. Zero callers need to change. ~40% reduction in per-batch-call subprocess cost.

### 5. Recommend obsidian-git as the canonical batcher

The README and the bilingual docs site (EN + ES) gain a **"Recommended configuration"** section that explicitly recommends the obsidian-git plugin (auto-commit interval 5–10 min) for users with write-heavy flows, paired with `commit=False` on Hive tool calls. This is the operational answer to ADR-005's Option C without building Option C.

### 6. Detection + soft warning

If Hive detects `<vault>/.obsidian/plugins/obsidian-git/data.json` on startup with `commitInterval > 0`, `vault_health` surfaces an INFO line: *"Detected obsidian-git auto-commit (every Nm). `commit=False` on vault_write/vault_patch is safe."*

This is a hint, not enforcement — the user still controls everything.

## Alternatives considered

### A) Per-call coalescer only (decision §4 alone)

**Pros:** zero new surface, ~40% improvement for already-batched call sites.
**Cons:** does nothing for the dominant hot path (sequential `vault_write` calls).
**Outcome:** included as part of this decision, but not sufficient alone.

### B) **Chosen: A + opt-in `commit=False` + external committer delegation + recommendation**

**Pros:**
- Preserves ADR-005's invariant by default.
- Delegates batching complexity to a well-tested external tool (obsidian-git) that the user already runs.
- Reversible: opt-in nature means no break for existing clients.
- Test surface grows modestly (~5–10 new tests).

**Cons:**
- Two committers in the ecosystem (Hive opt-in + obsidian-git) means the user has to think about which owns commits. Mitigated by §6 detection + docs.
- Does not help users without obsidian-git unless they invoke `vault_commit` explicitly.

### C) Background flusher inside Hive (Option C from ADR-005)

**Rejected, second time.** Reasons:

- Race with obsidian-git for `.git/index` is a real and severe failure mode (interleaved commits, broken index, user data integrity at risk).
- Crash recovery semantics are bug-prone: distinguishing "files we wrote and didn't commit yet" from "files the user edited manually in vim" requires a `pending_writes` SQLite table + reconciliation logic, all of which is new code we'd have to maintain forever.
- Multi-process flusher coordination (single-elected flusher via filelock) adds ~80 LOC of async lifecycle code that is notoriously hard to get right.
- ADR-005's gate condition — *"write-latency regressions that the daemon model alone cannot fix"* — is not met. Option B in this ADR is the smaller answer.

Re-evaluate only if measurements after Option B ship show sustained dolor that obsidian-git + `commit=False` cannot resolve.

### D) Migrate to `pygit2` native bindings

**Deferred.** Would reduce per-write cost from ~150 ms to ~10 ms by eliminating fork+exec. But:

- Adds a C-dependency (libgit2) to a previously pure-Python wheel.
- Multi-process safety with `pygit2`'s in-memory index needs careful verification — does not automatically inherit the filelock semantics.
- Strictly orthogonal to this ADR: a future PR could land this without touching the decision here.

Reconsider if Option B + obsidian-git proves insufficient at higher scale (>10 sessions/machine per ADR-005 §"Scale analysis").

## Consequences

- **Invariant preserved by default**: existing clients and tests see no behavior change. The "write success ⇒ git committed" property continues to hold unless the caller explicitly opts out.
- **Free perf win for already-batched callers**: `vault_patch` and `capture_lesson(text=...)` get the coalescer for free.
- **Opt-in batching unblocks write-heavy flows**: agents that issue many sequential writes can pass `commit=False` and either call `vault_commit` at the end or rely on obsidian-git.
- **New documentation responsibility**: README + site docs (EN + ES) must surface the obsidian-git recommendation prominently. This is part of the same PR as the code change — not a follow-up.
- **`vault_health` gains a "pending uncommitted writes" signal** when `commit=False` is in active use, so drift is observable.
- **Test surface grows modestly**: ~5–10 new tests covering `commit=False`, `vault_commit`, coalescer behavior, obsidian-git detection.
- **No new background tasks**, no new SQLite tables, no new lifecycle complexity. We stay on the simpler end of the architecture space that ADR-005 chose.
- **Shrinks the ghost-response race window** ([adr-007-mcp-cancellation-response.md](adr-007-mcp-cancellation-response.md)): with `commit=False`, write duration drops from ~150 ms to ~5 ms. Client-side cancellation timeouts become very unlikely to fire during writes.
- **Not addressed here**: reads under contention (e.g. `vault_search` on a large corpus with the HIVE-97 lesson tracker under WAL pressure) can still trigger the ghost-response race. That is ADR-007's territory.

## Amendments

### 2026-05-21 — §C gate triggered (HIVE-115)

§C of this ADR rejected the background-flusher / Option C pattern with the gate condition: *"Re-evaluate only if measurements after Option B ship show sustained dolor that obsidian-git + commit=False cannot resolve."*

Measurements collected after Option B's 2026-05-20 ship (v1.14.0) confirm sustained dolor:

- **838s `capture_lesson` outlier** vs configured `tool_timeout=60` (issue #111, Windows user repro)
- **`relevance.db-wal` = 4.1 MB vs `.db` = 53 KB** (77× ratio) under N=3-5 concurrent baseline (issue #110)
- **Silent 30-second freezes per call** coinciding with obsidian-git auto-commit ticks (issue #110)
- **3 simultaneous hive-vault processes holding handles to all 3 SQLite DBs** locally — multi-reader pattern blocks WAL checkpoint indefinitely

The re-evaluation is documented in [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) v2 (Phase B Outbox + Reconciler amendment, shipping in v1.16.0 bundle alongside Phase A defensive work). The Outbox-in-Hive design carefully avoids the "race with obsidian-git for `.git/index`" failure mode that §C identified — by **detecting** obsidian-git presence and **deferring** to it when healthy (probe-based health check), with **automatic fallback** to a hive-internal reconciler when external committer is stale or absent. See [adr-010-external-committer-coexistence.md](adr-010-external-committer-coexistence.md) for the cooperation pattern.

### Decisions §1-§5 unchanged

- §1 (default semantics `write ⇒ commit`) — unchanged
- §2 (opt-in `commit=False`) — unchanged; auto-defer in Phase B is additive, not replacing
- §3 (`vault_commit` MCP tool) — unchanged
- §4 (coalescer in `_git_commit`) — unchanged
- §5 (recommend obsidian-git in docs) — unchanged; cooperation pattern made more explicit in [adr-010-external-committer-coexistence.md](adr-010-external-committer-coexistence.md)

### §6 detection promoted

§6's "INFO line in `vault_health`" was informational only. [adr-010-external-committer-coexistence.md](adr-010-external-committer-coexistence.md) promotes `detect_obsidian_git()` to first-class design concept: the boolean drives auto-defer behavior in Phase B, and `last_git_lock_wait_ms` + `mcp.lock_contention` structured logs surface contention with the external committer.

### §C retracted as rejection

§C is no longer a "rejected alternative" — it is a **deferred decision now unblocked by data**, formally implemented in [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) v2. The original concerns (race with obsidian-git, crash recovery, multi-process flusher coordination) are addressed by the cooperation-not-competition design of ADR-010.

## References

- [adr-001-orchestration-model.md](adr-001-orchestration-model.md) — original Hive orchestration model
- [adr-004-thread-safety-model.md](adr-004-thread-safety-model.md) — `_GIT_LOCK` and `_WRITE_LOCK` remain in force; this ADR does not change their semantics
- [adr-005-transport-and-scale.md](adr-005-transport-and-scale.md) — established the throughput ceiling and rejected Option C; **co-amended in this release** (HIVE-115)
- [adr-007-mcp-cancellation-response.md](adr-007-mcp-cancellation-response.md) — interrelated; the ghost-response race is shrunk by §2 here and handled by ADR-007
- [adr-008-hard-deadline-enforcement.md](adr-008-hard-deadline-enforcement.md) — Phase B deadline supervisor (companion ADR)
- [adr-009-multi-process-wal-policy.md](adr-009-multi-process-wal-policy.md) — Phase A WAL policy + v2 amendment (Outbox + Reconciler, the §C re-evaluation)
- [adr-010-external-committer-coexistence.md](adr-010-external-committer-coexistence.md) — Phase A obsidian-git cooperation pattern (operationalizes §6 detection)
- [lessons.md](../lessons.md) — "Cooperative external committer needs explicit coordination" (load-bearing rationale)
- obsidian-git plugin: https://github.com/Vinzent03/obsidian-git
- Per-write cost analysis: `src/hive/_helpers.py:702-753` (`_git_commit` — the two `subprocess.run` calls at lines 727 and 734)
- HIVE-104 spec (archived): `specs/archive/HIVE-104-write-throughput/`
- HIVE-115 spec: `specs/HIVE-115-latency-tail-redesign/` (forthcoming)
