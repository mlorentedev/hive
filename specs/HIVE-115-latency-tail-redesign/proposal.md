---
id: "HIVE-115-latency-tail-redesign"
type: spec
status: draft
created: "2026-05-21"
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-115: Latency Tail Redesign

> File lives at `specs/HIVE-115-latency-tail-redesign/proposal.md`. Three-phase redesign per [[pattern-phased-redesign-with-telemetry-gates]]. v1.16.0 bundles Phase A + Phase B (PR-1 through PR-4); Phase C (daemon) is a planned follow-up.

## Why

<!-- from 10_projects/hive/11-tasks.md (HIVE-115 backlog entry, 2026-05-21):
"Composition of locally-optimal decisions (ADR-001 process-per-client + ADR-006 best-effort commit + asyncio.timeout deadline) interacts badly under N=3-5 baseline + external committer (obsidian-git, 10-min interval, pullBeforePush=true). Empirical: 4.1MB WAL vs 53KB DB, 838s capture_lesson outlier, 2-month-stale worker.db-wal." -->

Three open issues (#110, #111, #114) trace to a single root cause: hive's composition of process-per-MCP-client orchestration (ADR-001) + best-effort auto-commit (ADR-006) + asyncio.timeout-as-deadline interacts pathologically under real daily usage — **3-5 concurrent Claude Code sessions per user against the same vault, with obsidian-git auto-commits every 10 minutes**. Empirical evidence collected 2026-05-21: 4.1 MB `relevance.db-wal` vs 53 KB DB (77× ratio), `worker.db-wal` unchecked for 2 months, 838s `capture_lesson` outlier vs configured 60s deadline. Users experience silent 30-second freezes per write, invisible hangs, and `capture_lesson` calls that the host treats as user-rejected.

The system is operating at the codo of ADR-005's own scale table during normal daily use. Without re-architecting the composition, the situation worsens as agent-driven workloads (anticipated N=10-15) come online.

## What

After this PR-bundle (v1.16.0):

1. **WAL bloat is bounded and observable.** Every hive process runs a periodic `PRAGMA wal_checkpoint(PASSIVE)` thread (default 30s tick); `vault_health(include_runtime=True)` surfaces `wal_size_bytes` so growth is visible before it hurts.
2. **External-committer contention is observable and tunable.** `last_git_lock_wait_ms` + `mcp.lock_contention` structured logs make obsidian-git coexistence visible. `HIVE_LOCK_TIMEOUT_S` env-tunable so large vaults / slow networks can absorb extended windows.
3. **Tool deadlines are enforced as hard contracts.** `bounded_call` supervisor replaces `tool_span`'s asyncio-only mechanism: `subprocess.run → Popen` migration in 5 git callsites; on deadline expiry, supervisor terminates the registered child processes (cross-OS) and surfaces `mcp.protocol.TimeoutError` to the client (no more silent hangs, no more "Server busy" canned strings).
4. **Vault git auto-defers to obsidian-git when healthy.** Outbox + Reconciler: when `detect_obsidian_git()` reports a healthy external committer (last commit < 2× autoSaveInterval ago), hive writes are implicit `commit=False`. Health probe + automatic fallback to hive's own reconciler when external committer is stale or absent.
5. **`capture_lesson` defends against malformed XML inputs at the boundary.** SUSPECT_PATTERNS regex set warns and annotates corruption without rejecting the write (preserves agent's mid-turn work, surfaces the issue for cleanup).

Closes issues #110 (mits #1+#2+#3+#5 + cooperation pattern), #111 (bounded_call), #114 (Tier-1 defense).

## Out of scope

- **Phase C (hive-vault daemon model)** — planned for v1.17.0 / v2.0 as ADR-011, after observing v1.16.0 telemetry under real load. The user's future state (agent-driven N=10-15) makes it inevitable, but its design needs Phase A+B telemetry as input.
- **`pygit2` native bindings** to eliminate fork+exec git overhead — ADR-006 §D deferred this; remains deferred unless Phase B telemetry shows residual subprocess cost dominates.
- **#114 Tier-2/Tier-3** (post-write self-check + vault_health lessons-integrity scan) — Tier-1 alone solves the immediate corruption surface; Tier-3 may come later if other malformed-input shapes surface.
- **Cross-vault federation, multi-tenant hive backend** — explicitly v3+ if ever.
- **Replacing the FastMCP stdio transport** — Phase C is when, not this.

## Risks / open questions

- **R1 — `psutil` cross-OS reliability.** New runtime dep. `process_iter(['open_files'])` is slow on Windows (~100ms); cache 30s. macOS requires admin for cross-user; we filter same-UID only. Antivirus / backup tools briefly opening DBs may inflate `competing_pid_count` — filter strictly by `name() == "hive-vault"`. Mitigation in `_vault_health` design; tests for each OS.
- **R2 — `bounded_call` subprocess termination semantics on Windows.** `TerminateProcess` is NOT graceful; descendants may orphan without `CREATE_NEW_PROCESS_GROUP`. `.git/index.lock` cleanup must verify PID ownership before unlinking. Tests must cover: SIGTERM-then-grace POSIX path, TerminateProcess Windows path, partial-commit prevention, `.git/index.lock` left intact when written by obsidian-git's PID.
- **R3 — Outbox eventual-consistency surface.** In Phase B, process A's `capture_lesson` writes to disk synchronously (markdown file) but reinforcement counter update goes through outbox + reconciler. Cross-process read sees stale rank for ~5s. Acceptable for ranking (informational) but document the contract clearly. **Resolved before tasks.md freeze:** verify no read paths assume immediate visibility of EMA/reinforcement updates.
- **R4 — Detect-and-defer health probe correctness.** `git log -1 --since="<N> minutes ago"` returning empty does NOT necessarily mean obsidian-git is broken — it may be that no commits happened (idle vault). Need additional signal: `(time.time() - last_obsidian_commit_age) > 2 * autoSaveInterval AND vault is dirty`. **Resolved 2026-05-22 (pre-PR-3 audit):** explicit predicate spelled in `tasks.md` T4.10 — `defer ⇔ env=true ∧ obsidian_git_present ∧ ((last_commit_age < 2*autoSaveInterval) ∨ (git status --porcelain is empty))`. Idle vault is treated as "external committer is alive but quiet" (safe to defer); dirty + stale = broken (fall back).
- **R5 — Behavior change from auto-defer.** In Phase B, when obsidian-git is present and healthy, `vault_write(commit=True)` becomes implicit `commit=False`. This is a hidden behavior change. **Resolved:** keep `commit=True` as documented default; auto-defer is OPTIONAL via `HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER=true` (default false) for v1.16.0, then re-evaluate based on Phase A telemetry whether to flip the default in v1.17.0.

## Acceptance criteria

Each criterion is testable. Mapped 1:1 to `tasks.md` TDD entries.

- [ ] **AC-1**: Every `_SqliteTracker` instance runs a daemon thread executing `PRAGMA wal_checkpoint(PASSIVE)` on a configurable interval (default 30s). Tests: thread starts on tracker init; thread exits on tracker close; checkpoint pragma is invoked at least once per N+1s test window.
- [ ] **AC-2**: `_SqliteTracker.close()` executes `PRAGMA wal_checkpoint(TRUNCATE)` with a 2-second wall-clock guard. Test: close completes within 2.5s even when a sibling connection holds a snapshot (TRUNCATE degrades to PASSIVE behavior, does not block).
- [ ] **AC-3**: `vault_health(include_runtime=True)` runtime block contains `wal_size_bytes` (sum of `~/.local/share/hive/*.db-wal`), `competing_pid_count` (psutil-based, same-UID, name-filtered, cached 30s), `last_git_lock_wait_ms` (rolling N=100 ring buffer with mean + p99), `obsidian_git_present` (boolean). Tests: presence under each scenario (0 / 1 / 3 hive procs, obsidian-git present/absent).
- [ ] **AC-4**: `HIVE_LOCK_TIMEOUT_S` env var honored end-to-end (default 30, capped at 600). Default behavior unchanged from current. Test: set to 5, verify `_GIT_LOCK.acquire` abandons at 5s; set to 0 or >600, verify rejected with `ValueError`.
- [ ] **AC-5**: Every `_GIT_LOCK.acquire` attempt emits exactly one structured log line `mcp.lock_contention` with fields `{tool, lock, waited_ms, abandoned, obsidian_git_present}`. Test: capture log with `caplog`, assert presence + correctness of fields for both success and timeout paths.
- [ ] **AC-6**: `capture_lesson` invocations with SUSPECT_PATTERNS (`</context>`, `</parameter>`, `<parameter name=`, `</invoke>`) in title/context/problem/solution prepend `<!-- POSSIBLE_CORRUPTION: ... -->` to the body and surface `warning` field in the JSON response. Lesson is still written (warn-don't-reject). Test: parametrized with each pattern + negative case.
- [ ] **AC-7**: `bounded_call(fn, deadline_s, process_registry=...)` raises `mcp.protocol.TimeoutError` within `deadline_s + 3` regardless of whether the work is in a thread, a subprocess, or chained. Test: 3 cases — async sleep > deadline, `subprocess.Popen(["sleep", str(deadline+10)])`, `asyncio.to_thread(blocking_sleep, deadline+10)`. All 3 return within deadline + grace. **(per B4)** `_compat.GHOST_RESPONSES.record(tool, source)` discriminates `source="deadline"` (bounded_call-driven late respond suppression) from `source="cancellation"` (race-condition suppression); `vault_health.ghost_responses.by_source` exposes the breakdown.
- [ ] **AC-8**: `bounded_call` deadline expiry terminates registered `Popen` children. Test: spawn `sleep 60`, expire 2s deadline, verify `Popen.poll()` is non-None within `2 + grace_s` of expiry; no zombie subprocess after `wait_for_zombies` helper. **(per M2)** Cross-OS symmetry: Windows uses `CREATE_NEW_PROCESS_GROUP` + `TerminateProcess`; POSIX uses `start_new_session=True` + `os.killpg(os.getpgid(pid), SIG)` so descendants are reached on both platforms.
- [ ] **AC-9**: `_cleanup_index_lock` removes `.git/index.lock` ONLY when PID matches our Popen.pid. Test: simulate lock file with our PID (cleaned), with another PID (untouched).
- [ ] **AC-9b** *(new — per B3)*: termination between sequential sub-operations of a multi-Popen helper (e.g. `_git_commit` runs `git add` then `git commit`) does not leave the repo in an unrecoverable state. If kill lands between `git add` and `git commit`, the index has staged changes but no commit — `git status --porcelain` shows them and the next successful `_git_commit` call rescues them by re-staging. Test: spawn `_git_commit`, kill after `git add` succeeds but before `git commit` starts, assert next call commits cleanly.
- [ ] **AC-10** *(revised — per m1)*: `subprocess.run → Popen` migration scoped to **write-path git callsites** (`_git_commit`, `_git_commit_all` — 6 Popens across 2 functions); read-path `_git_log`/`_git_recent` retain `subprocess.run` (cached 5min, advisory `tool_timeout`, no termination needed). No regression in existing test suite. Test: full `make check` passes; HIVE-104 coalescer tests still pass; ghost-response test (`test_classify_cancellation_race`) still passes; multi-process integration test (T3.7b) shows N=3 concurrent procs complete without deadlock.
- [ ] **AC-11** (Phase B / PR-4): in-process outbox for `RelevanceTracker` + `LessonReinforcementTracker`. Writes buffered; reconciler thread flushes every 5s with PASSIVE checkpoint. Read paths check outbox FIRST. Test: write-then-read same-process is immediate; cross-process read sees the write within `2 * tick_interval`.
- [ ] **AC-12** *(revised — per M4)*: detect-and-defer composite predicate (see R4 resolution): `defer ⇔ env=true ∧ obsidian_git_present ∧ ((last_commit_age < 2*autoSaveInterval) ∨ (git status --porcelain empty))`. Idle vault with empty porcelain is treated as deferable (external committer alive). Dirty + stale = broken, fall back. `vault_write(commit=True)` deferred → `{committed: false, deferred_to: "obsidian-git"}`; fallback → `{committed: true, fallback_from: "obsidian-git-stale"}`. Test: 4 scenarios — healthy + recent commits / healthy + idle / unhealthy (dirty + stale) / env=false (default, no defer).
- [ ] **AC-13** (cross-cutting): documentation site (EN + ES) has a new "Troubleshooting > Multi-session contention" page with zombie-detection recipes (`ps`/`lsof`/`Get-Process`/`tasklist`), `HIVE_LOCK_TIMEOUT_S` recommended ranges, and a sub-section on the obsidian-git cooperation pattern. Bilingual sync verified.
- [ ] **AC-14**: All 4 ADRs (008, 009 v1, 010) and 2 amendments (005, 006) committed to vault. Pattern `pattern-multi-process-mcp-server` extended with primitives 5-7. 5 lessons captured in `hive/90-lessons.md` + 1 meta-pattern in `_meta/patterns/`. Reference: 2026-05-21 vault commits `4d9c642` (lessons + meta-pattern) and `02d1f63` (ADRs).

## References

- Vault backlog: `10_projects/hive/11-tasks.md` (HIVE-115 entry, 2026-05-21)
- ADRs (vault): [[adr-008-hard-deadline-enforcement]], [[adr-009-multi-process-wal-policy]], [[adr-010-external-committer-coexistence]]
- ADR amendments (vault): [[adr-005-transport-and-scale]] (HIVE-115 telemetry gate triggered), [[adr-006-commit-policy]] (§C gate triggered)
- Patterns (vault): [[pattern-multi-process-mcp-server]] (extended with primitives 5-7), [[pattern-phased-redesign-with-telemetry-gates]] (meta-pattern, this redesign is its origin)
- Lessons (vault): [[lesson-three-timeouts-chain-not-deadline]], [[lesson-sqlite-wal-no-checkpoint-with-readers]], [[lesson-cooperative-external-committer]], [[lesson-telemetry-is-the-design]]
- TDD discipline: [[pattern-testing-standards]] — red-green-refactor, pytest+coverage, fixtures with tmp_path, parametrize for cross-OS
- Issues: [#110](https://github.com/mlorentedev/hive/issues/110), [#111](https://github.com/mlorentedev/hive/issues/111), [#114](https://github.com/mlorentedev/hive/issues/114)
- Prior HIVE-104 spec (archived): `specs/archive/HIVE-104-write-throughput/` — established `commit=False` + `vault_commit` + obsidian-git detection foundations
