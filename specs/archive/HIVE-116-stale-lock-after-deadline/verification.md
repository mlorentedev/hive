---
id: "HIVE-116-verification"
type: spec-verification
status: draft
created: "2026-05-27"
tags: [spec, verification]
template_version: "1.0"
---

# HIVE-116 — Verification

> Per AC: exact pytest invocation, expected output marker, and (where relevant) the manual Windows-smoke recipe. Run order matches `tasks.md` phase order; do not skip ahead.

## Pre-flight (clean baseline)

```bash
make check
# Expect: ruff clean / mypy --strict clean / pytest 0 fails
git status
# Expect: working tree clean
```

If `make check` fails on master before any HIVE-116 work, STOP — fix master first or abandon this spec until baseline is green.

## AC-1: filelock evicted on deadline

```bash
uv run pytest tests/test_cross_worker_lock.py::test_evict_filelock_on_deadline -v
```

Expect: PASS within `deadline_s + grace_s + drain_s + 5s` (default 60+2+5+5 = 72s wall time).
Expect log line `mcp.lock_eviction lock=_git_filelock vault=<tmp_path> killed_pids=[N]` exactly once.

Manual Windows smoke (post-PR-2):
1. Launch 2 Claude Code sessions pointed at the same Windows vault.
2. From session A: trigger a slow `vault_patch` (use a fake `git.bat` in PATH that sleeps).
3. Wait for the 60s deadline + 5s drain.
4. From session B: call `vault_patch` on any other file.
5. Expect session B's call completes within ~5s, NOT timeout.
6. Inspect `%LOCALAPPDATA%\hive\lock_evictions.db`: one row, ISO ≈ now.

## AC-2: HIVE_POST_KILL_DRAIN_S env var

```bash
uv run pytest tests/test_config.py -k post_kill_drain -v
```

Expect: 4 parametrized cases — `0.5` ok, `5.0` ok (default), `30.0` ok, `0.1` raises ValueError, `60.0` raises ValueError. 5 passes, 0 fails.

## AC-3: synthetic stderr on external termination

```bash
uv run pytest tests/test_run_git.py::test_external_termination_synthetic_stderr -v
```

Expect: assertion that stderr matches `r"^\[external_termination\] killed by supervisor at \d{4}-\d{2}-\d{2}T.*; original stderr: (empty|\d+ bytes)$"`.

## AC-4: cause= tag in warning logs

```bash
uv run pytest tests/test_helpers.py::test_git_commit_warning_carries_cause -v
```

Expect: 2 cases (external_termination, git_error); `caplog.records` contains `"cause=external_termination"` exactly once for the kill case, `"cause=git_error"` exactly once for the rc=1 case.

## AC-5: partial-state suffix in write tools

```bash
uv run pytest tests/test_vault_write.py -k partial_state -v
```

Expect: 4 parametrized cases — committed (no suffix), uncommitted (existing `_UNCOMMITTED_SUFFIX`), deferred (existing `_DEFERRED_SUFFIX`), deadline-killed (new `_PARTIAL_STATE_SUFFIX`).

Manual contract probe (one-shot after T-1.4 lands):
```bash
echo "import json; from hive._vault_write import _PARTIAL_STATE_SUFFIX, _DEFERRED_SUFFIX, _UNCOMMITTED_SUFFIX; print(json.dumps({'partial': _PARTIAL_STATE_SUFFIX, 'deferred': _DEFERRED_SUFFIX, 'uncommitted': _UNCOMMITTED_SUFFIX}, indent=2))" | uv run python -
```

Expect: three distinct strings; partial-state string matches the wording the user signed off in T-0.2.

## AC-6: per-tool partial-state hook routing

```bash
uv run pytest tests/test_helpers.py::test_run_sync_tool_partial_state_hook -v
```

Expect: 2 cases — `vault_write` deadline → partial-state message; `vault_query` deadline → generic "timed out" message (unchanged).

## AC-7: cross-worker integration (THE money test)

```bash
uv run pytest tests/test_cross_worker_lock.py -m cross_worker -v --timeout=120
```

Expect: 1 pass, wall time < 75s. If wall time exceeds 90s, the eviction did not fire — investigate `_GIT_FILELOCKS` cache + `evict_filelock` log.

CI verification (post-PR-3):
- GitHub Actions run for the new `cross_worker_lock` job shows green on `ubuntu-latest`.
- `windows-latest` lane: allowed-to-fail for first 14d; check the job log for `mcp.lock_eviction.race` warnings — should be 0 in 20 consecutive runs.

## AC-8: vault_health surfaces eviction counter

```bash
# After triggering at least one eviction via AC-7:
uv run python -c "from hive._context import build_context; ctx = build_context(); print(ctx.lock_eviction.count_last_30d(), ctx.lock_eviction.last_iso())"
```

Expect: count ≥ 1, ISO timestamp within the last hour.

```bash
# Health block:
uv run pytest tests/test_vault_health.py::test_runtime_block_includes_lock_eviction -v
```

Expect: `lock_eviction_count_30d` and `last_lock_eviction_iso` present in the runtime block dict.

Persistence check:
```bash
# Restart hive, re-read the counter:
sqlite3 ~/.local/share/hive/lock_evictions.db "SELECT COUNT(*) FROM lock_evictions WHERE iso_ts > datetime('now', '-30 days')"
```

Expect: matches the value from the prior step.

## AC-9: GHOST_RESPONSES.by_source regression

```bash
uv run pytest tests/test_compat_shim.py -k by_source -v
```

Expect: no new source tag introduced; `deadline` + `cancellation` are still the only two.

## AC-10: CI matrix lane

After PR-3 merges and Dependabot runs once:
- Inspect the GitHub Actions run page for the most recent push to master.
- `cross_worker_lock (ubuntu-latest)` — expected GREEN.
- `cross_worker_lock (windows-latest)` — allowed-to-fail; investigate logs but DO NOT block release on it for first 14d.

## AC-11: docs site EN+ES parity

```bash
make site
# Then manually visit /troubleshooting/ and /es/troubleshooting/
```

Expect: both languages have "Partial-state writes after deadline" section. Line count parity within ±5 lines per the existing bilingual link-validator CI.

## AC-12: ADRs + lesson committed to vault

```bash
ls ~/Projects/knowledge/10_projects/hive/30-architecture/adr-012-cooperative-filelock-eviction-on-deadline.md
ls ~/Projects/knowledge/10_projects/hive/90-lessons.md  # check for [2026-XX-XX] lesson-cancel-a-thread-you-cannot heading
grep -l "Cooperative-lock eviction" ~/Projects/knowledge/10_projects/hive/30-architecture/adr-008-hard-deadline-enforcement.md
```

Expect: all three commands return paths/matches without error.

## AC-13: release-please bumps versions correctly

Post-PR-1 merge:
- release-please opens a PR for `v1.20.0` with HIVE-116 PR-1 in the changelog.
- After that release PR merges, PyPI tag `v1.20.0` lands within 30 minutes.

Same shape for PR-2 → `v1.21.0` and PR-3 → `v1.22.0`. Verify via:
```bash
gh release list --repo mlorentedev/hive --limit 5
```

## Final regression sweep

After all three PRs merge:
```bash
make check
uv run pytest -m "not smoke and not cross_worker" --cov=hive --cov-report=term-missing
uv run pytest -m cross_worker --timeout=120
```

Expect:
- `make check` green (ruff + mypy + default pytest).
- Coverage ≥ 90% (per HIVE-104 baseline; this spec must not regress coverage).
- cross_worker tests pass within their budget.

## Field validation (post v1.22.0 on real vault)

Run the live Windows session that originally reproduced issue #141:
1. Two Claude Code sessions, same vault, obsidian-git active.
2. Issue a deliberately slow `vault_patch` via a 174 MB vault (the original repro shape).
3. After 60s deadline kill in session A, immediately `vault_patch` from session B on a different file.
4. Expect: session B succeeds within ~10s.
5. Expect: `.git/hive.lock` still exists as a 0-byte file (Windows file-handle invariant — unchanged), but `_GIT_FILELOCKS[<vault>]` is no longer cached and the next acquire creates a fresh FileLock.
6. Expect: `vault_health(include_runtime=True)` reports `lock_eviction_count_30d >= 1`.

If field validation passes, comment on issue #141 with the result + close. If it fails, reopen this spec with a new ADR-013 covering the residual failure mode.
