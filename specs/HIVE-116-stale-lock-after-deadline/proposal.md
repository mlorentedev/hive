---
id: "HIVE-116-stale-lock-after-deadline"
type: spec
status: draft
created: "2026-05-27"
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-116: Stale `.git/hive.lock` + partial-state silence after `bounded_call` deadline

> File lives at `specs/HIVE-116-stale-lock-after-deadline/proposal.md`. Post-HIVE-115 regression surfaced by issue [#141](https://github.com/mlorentedev/hive/issues/141) — fixes the lock-handle and partial-state observability gaps that the v1.18.0 + v1.19.0 deadline supervisor left open. Three-phase landing per [[pattern-phased-redesign-with-telemetry-gates]] (QW → root-cause → cross-OS validation), each phase ships independently.

## Why

<!-- from issue #141 (2026-05-27) + sibling-session evidence from the same day:
  Two independent reproductions on Windows + NTFS within one hour, both
  on the same machine but different Claude Code sessions, both ending
  in: subprocess killed by HIVE-115 PR-3 bounded_call, on-disk write
  persisted, git commit never ran, .git/hive.lock orphaned with open
  parent file handle, client received a generic "timed out" string
  that contradicted the actual disk state. -->

HIVE-115 PR-3 (`bounded_call` hard-deadline supervisor) closed the original "infinite hang on stuck subprocess" failure mode by terminating registered Popens on deadline expiry. Two-week empirical use surfaces three residual gaps that **the supervisor was not specified to close** but in practice users hit as regressions:

1. **Hive's own cooperative filelock (`.git/hive.lock`) is not cleaned up on deadline.** The supervisor's `_cleanup_index_lock` is PID-matched and scoped to git's *native* `.git/index.lock`. The filelock under `.git/hive.lock` — Hive's inter-process serialization mechanism — is left in whatever state the worker thread leaves it in. When the worker thread is still inside `_filelock_with_telemetry`'s `with` block at deadline expiry, the lock remains held; the parent process retains an open file handle that on Windows blocks external cleanup (`rm` fails with `Device or resource busy`).
2. **`stderr=""` from externally-terminated subprocess leaves operators blind.** `_run_git` returns `rc=-1` on `BrokenPipeError`/`OSError` after the supervisor kills the Popen; the `WARNING git add failed for … rc=-1 err=` line carries no signal about *why*. Operators cannot distinguish "we killed it on deadline" from "git failed with empty stderr". Empirical: 2026-05-27 logs `hive-3468.log`, `hive-9092.log`, `hive-31272.log` all show `err=` empty after deadline kills.
3. **Client gets a generic "timed out — retry shortly" string while the disk write has already landed.** `run_sync_tool`'s timeout branch (`_helpers.py:880-883`) returns a canned message that contradicts reality: the markdown file IS on disk; only the git commit failed. Agents (Claude Code, OpenCode, etc.) interpret the message as failure and **retry with native FS writes**, risking double-writes. Today's repro shows obsidian-git auto-commit picking up the orphan modified files ~4 minutes later — partial state self-heals on this machine, but the client never learns that.

Empirical evidence (2026-05-27, Windows 11, ~174 MB vault, 2 concurrent `hive-vault` workers, obsidian-git active):

- `~/.local/share/hive/hive-31272.log` — `vault_patch` on `10_projects/dotfiles/11-tasks.md`, 60s deadline, `killed_pids=[10424]`, file persisted, `.git/hive.lock` 0-byte orphan with mtime exactly matching the timeout.
- `~/.local/share/hive/hive-9092.log` — independent session 2 hours later, `vault_patch` on `10_projects/mapi-msg-dumper/11-tasks.md`; both `git add` AND `git commit` returned `rc=1 err=""`; `capture_lesson` runs **246 seconds** wall-time after the supposed 60s deadline (HIVE-115 PR-3 closes the *child kill* loop but `tool_span` cannot preempt the parent Python thread — separate, known constraint).

**Root cause synthesis** (from reading `src/hive/_helpers.py:992-1060` + `src/hive/_deadline.py:201-258` against the log timeline):

- `bounded_call` deadline → `_terminate_registry` SIGTERMs Popen → grace → SIGKILL. ✅
- Worker thread sitting in `_run_git → proc.communicate()` is **not** preempted by Python (asyncio cannot cancel threads; this is a CPython invariant, see lesson [[lesson-three-timeouts-chain-not-deadline]]).
- Worker thread holds `_GIT_LOCK` (threading) + the `_git_filelock` FileLock context throughout. Until the thread escapes `_filelock_with_telemetry.__exit__`, no other call can acquire — and the cached singleton in `_GIT_FILELOCKS` keeps the file-handle pinned at the OS layer.
- The supervisor *does* return `TimeoutError` to the awaiter, but the worker thread is now "an abandoned thread holding a global lock for an unbounded time." Subsequent `vault_patch` calls in the same hive process block on `_GIT_LOCK.acquire(timeout=30s)` → "Server busy"; calls from sibling hive processes block on the OS filelock → same result.

This is not a bug in `bounded_call` per se — its contract was "kill the subprocess, return to the client." It is an **unspecified cooperation contract between the supervisor and the worker-thread side of the same critical section**, and the spec needs to close it.

## What

After this spec ships (target: v1.20.0 → v1.21.0 → v1.22.0 across three PRs):

1. **`.git/hive.lock` is released or evicted within the deadline grace window.** When `bounded_call` fires its deadline and at least one subprocess was killed, the supervisor explicitly evicts the cached `FileLock` instance from `_GIT_FILELOCKS` after a configurable post-kill drain period. Subsequent calls create a fresh `FileLock` against the same path; on Windows the orphan file persists until process exit (filelock library invariant), but is no longer cached and no longer blocks new acquires. New env: `HIVE_POST_KILL_DRAIN_S` (default 5.0, validated 0.5..30).
2. **Externally-terminated subprocesses log a structured cause, not blank stderr.** `_run_git` distinguishes `rc=-1` (we killed it) from `rc>0` (git itself failed). On external termination, the warning line carries `cause=external_termination signalled_pid=N` plus the supervisor's recorded kill timestamp. On natural failure, the original stderr is logged unchanged.
3. **Write tools return a structured "partial state" response on deadline.** `vault_write` and `vault_patch` detect when the disk-write landed but the commit did not, and surface a deterministic suffix the agent can pattern-match (final wording to be decided by the user — see Risk R3). `run_sync_tool`'s canned "timed out — retry shortly" is replaced by the disk-status-aware variant for these two tools only; read-path tools continue to return the original message.
4. **Cross-worker coordination is testable.** A new integration test under `tests/test_cross_worker_lock.py` spawns two `hive-vault` subprocesses against the same `tmp_path` vault and asserts that a deadline-killed write in worker A does not block worker B's next write beyond `deadline + grace + drain` total.
5. **Cross-OS validation matrix.** New CI lane: matrix `os: [ubuntu-latest, windows-latest]` for `tests/test_cross_worker_lock.py` + `tests/test_compat_shim.py`. Existing tests continue to run on Python 3.12 + 3.13 only — Windows lane is opt-in to keep the default CI fast.

Closes issue [#141](https://github.com/mlorentedev/hive/issues/141). Feeds the **2026-06-05 Phase C decision checkpoint** ([#124](https://github.com/mlorentedev/hive/issues/124)): if `_compat.GHOST_RESPONSES.by_source["deadline"]` ticks at all after this ships, the cooperation pattern is working as designed; persistent ticks reinforce the daemon-model gate.

## Out of scope

- **Preempting the worker thread itself.** Python cannot cancel a running thread; this spec accepts that constraint and works around it via lock-eviction. A daemon model that owns the SQLite + git would dissolve the problem class entirely — that is Phase C (#124), explicitly NOT this spec.
- **Transactional disk-write + git-commit.** Wrapping the FS write and the commit in a single rollback unit would require either copy-on-write semantics or a write-ahead log per vault. Both are larger than this spec; the partial-state response contract is an explicit acknowledgement that partial states are *acceptable by design* as long as the client learns about them.
- **`obsidian-git` coexistence policy changes.** HIVE-115 PR-4 already shipped detect-and-defer; this spec does not touch that. The interaction between auto-defer and the new partial-state suffix is exercised in tests but the predicate itself is unchanged.
- **Replacing `filelock` with a custom inter-process primitive.** `filelock` is the right tool; the bug is in our composition, not the library.
- **Phase C (#124) trigger inputs.** This spec contributes telemetry but does not pre-decide the 2026-06-05 checkpoint.

## Risks / open questions

- **R1 — Evicting the cached `FileLock` while the worker thread still holds it.** If we evict at `T = deadline + drain` and the worker thread escapes the `with` block at `T + ε`, the `__exit__` releases a different `FileLock` object than the one the next caller is trying to acquire. On POSIX `fcntl.flock` this is benign (the kernel tracks the file descriptor); on Windows `msvcrt.locking` the semantics are subtler. **Mitigation:** test on both OSes (T-X.Y in tasks.md). Fallback: emit `mcp.lock_eviction.race` warning if the supposedly-evicted lock is still `is_locked` 1s after eviction, and surface the count in `vault_health.runtime`.
- **R2 — `HIVE_POST_KILL_DRAIN_S` value.** Too short → race R1; too long → user-perceived "Server busy" window grows. **Default locked at 5.0s** (user-confirmed 2026-05-27) to match `HIVE_OUTBOX_TICK_S` for symmetry. T-2.8 calibration sweep ({1, 5, 10}) is retained as a verification step — if p99 at 5.0s exceeds 35s, the default is revisited in a follow-up patch before promoting the CI lane to required (per AC-10).
- **R3 — Partial-state response wording is a public contract.** ~~Open question~~ **Resolved 2026-05-27:** suffix is action-oriented; tells the agent exactly what to do. String-only (no JSON-shape evolution) to preserve symmetry with `_DEFERRED_SUFFIX` / `_UNCOMMITTED_SUFFIX` and avoid a breaking change for callers that treat `vault_write`'s return as plain text. Final wording: `" (partial state — disk write succeeded, git commit killed by deadline; verify with vault_query before retrying)"`. Downstream agents are expected to pattern-match on the literal substring `"partial state — disk write succeeded"` (the part that is stable across future minor wording tweaks).
- **R4 — Cross-worker test flake on Windows CI.** Spawning real hive subprocesses is slow on `windows-latest` (~5s cold start). The existing flaky `test_classify_cancellation_race` already shows this shape. **Mitigation:** keep the cross-worker test marked `@pytest.mark.windows_smoke`, run only on the windows lane, retry policy = none (we want flake signal, not flake suppression).
- **R5 — Interaction with HIVE-115 PR-4 Outbox.** Reinforcement counters route through Outbox, so a deadline mid-`capture_lesson` does NOT corrupt the counter (Outbox owns its own DB connection in the reconciler thread). But the *markdown write* of the lesson is still routed through `_vault_write._write_lesson` → `_git_commit`, which is exactly the path this spec fixes. Confirm in tests that the Outbox reconciler is unaffected.

## Acceptance criteria

Each criterion is testable. Mapped 1:1 to `tasks.md` TDD entries. Numbering reset (this is a fresh spec, not a continuation of HIVE-115's AC counters).

- [ ] **AC-1**: On `bounded_call` deadline expiry with `killed_pids != []`, after `HIVE_POST_KILL_DRAIN_S` seconds the supervisor evicts the cached `FileLock` for the affected vault from `_GIT_FILELOCKS`. Test: simulate hung subprocess via fake-git PATH injection, set drain=1s, assert `_GIT_FILELOCKS` does not contain the vault key 1.5s after deadline.
- [ ] **AC-2**: `HIVE_POST_KILL_DRAIN_S` env var honored end-to-end (default 5.0, validated 0.5..30 inclusive). Test: parametrized with valid + invalid values; invalid raises `ValueError` at config-load time.
- [ ] **AC-3**: `_run_git` returns `rc=-1` AND a synthetic `stderr` of the form `"[external_termination] killed by supervisor at <ISO-8601>; original stderr: <empty|N bytes>"` when the Popen was killed externally. Test: spawn a 60s `sleep`, kill from outside, assert the synthetic stderr is well-formed.
- [ ] **AC-4**: `WARNING git add failed for … rc=… err=…` log line carries `cause=external_termination` when `rc=-1`, `cause=git_error` otherwise. Test: `caplog` capture, parametrized for both branches.
- [ ] **AC-5**: `vault_write` and `vault_patch` return a partial-state suffix when the disk write succeeded but git commit failed *because the supervisor killed it*. Suffix shape and JSON schema TBD by user (see R3). Tests: parametrized for the four states `{write OK, commit OK}`, `{write OK, commit failed/killed}`, `{write OK, commit failed/git-error}`, `{write failed}`.
- [ ] **AC-6**: `run_sync_tool` routes deadline exceptions through a per-tool partial-state hook when the tool is `vault_write` or `vault_patch`; falls back to the original generic message for all other tools. Test: deadline-fire on `vault_write` → partial-state response; deadline-fire on `vault_query` (read path) → generic timeout response.
- [ ] **AC-7**: New integration test `tests/test_cross_worker_lock.py` spawns two `hive-vault` subprocess workers against the same `tmp_path` vault, fires a deadline-kill in worker A on a fake-hanging git, then issues a `vault_patch` in worker B; worker B's call completes within `deadline + grace + drain + 5s` total (currently 60+2+5+5 = 72s budget). Marked `@pytest.mark.cross_worker`, excluded from default `make test`.
- [ ] **AC-8**: `vault_health(include_runtime=True)` runtime block gains `lock_eviction_count_30d` (rolling daily counter persisted to `~/.local/share/hive/lock_evictions.db`) and `last_lock_eviction_iso` (ISO-8601 of most recent eviction or null). Tests: trigger an eviction, assert both fields populate; restart hive, assert counter persists.
- [ ] **AC-9**: `_compat.GHOST_RESPONSES.snapshot().by_source` continues to discriminate `deadline` vs `cancellation` (unchanged from HIVE-115); no new source tags introduced by this spec. Regression-only check.
- [ ] **AC-10**: CI matrix lane `os: [ubuntu-latest, windows-latest]` runs `tests/test_cross_worker_lock.py` and `tests/test_compat_shim.py::test_classify_cancellation_race`. Lane non-blocking (allowed-to-fail) for the first 14 days post-merge to surface flake patterns without blocking releases; AFTER that window, promoted to required (HIVE-117 follow-up to flip the bit).
- [ ] **AC-11**: Documentation site (EN + ES) gains a new "Troubleshooting > Partial-state writes after deadline" page. Recipes: how to detect via `git status`, how to interpret the new response suffix, when to retry vs. when to verify with `vault_query`. Bilingual sync verified by existing CI parity check.
- [ ] **AC-12**: One new ADR drafted in vault: `[[adr-012-cooperative-filelock-eviction-on-deadline]]`. One new lesson: `[[lesson-cancel-a-thread-you-cannot]]` (re-uses [[lesson-three-timeouts-chain-not-deadline]] as foundation; this is the corollary that motivates eviction). One amendment to `[[adr-008-hard-deadline-enforcement]]` documenting the post-kill drain + eviction step.
- [ ] **AC-13**: All three PRs (QW, root-cause, cross-OS) ship as Conventional Commits so release-please drives the version bumps automatically. Target versions: v1.20.0 (QW), v1.21.0 (root-cause), v1.22.0 (cross-OS test lane).

## References

- Issue: [#141](https://github.com/mlorentedev/hive/issues/141) — original report + 2nd-session reproduction comment
- Vault backlog: `10_projects/hive/11-tasks.md` (HIVE-116 entry, 2026-05-27)
- Prior spec (archived): `specs/archive/HIVE-115-latency-tail-redesign/` — context for `bounded_call`, `_run_git`, `tool_span`, `_compat.GHOST_RESPONSES.by_source`
- ADRs (vault): [[adr-008-hard-deadline-enforcement]] (amendment target), [[adr-009-multi-process-wal-policy]] (referenced — unchanged), [[adr-010-external-committer-coexistence]] (referenced — unchanged), [[adr-012-cooperative-filelock-eviction-on-deadline]] (new, drafted in T-12)
- Lessons (vault): [[lesson-three-timeouts-chain-not-deadline]] (foundation), [[lesson-cancel-a-thread-you-cannot]] (new, drafted in T-12)
- Patterns (vault): [[pattern-multi-process-mcp-server]] (extended with eviction primitive), [[pattern-phased-redesign-with-telemetry-gates]]
- Code surface (read by spec author 2026-05-27):
  - `src/hive/_helpers.py:992-1060` — `_git_commit` filelock + threading-lock composition
  - `src/hive/_helpers.py:777-797` — `_git_filelock` singleton cache (eviction target)
  - `src/hive/_helpers.py:705-737` — `_filelock_with_telemetry` (CM exit path)
  - `src/hive/_helpers.py:857-883` — `run_sync_tool` (partial-state hook injection point)
  - `src/hive/_deadline.py:201-258` — `bounded_call` supervisor (drain + eviction extension target)
  - `src/hive/_vault_write.py:36-77` — `_commit_status_suffix` (partial-state suffix sibling)
- TDD discipline: [[pattern-testing-standards]] — red-green-refactor, pytest, `caplog`, `tmp_path`, parametrize
