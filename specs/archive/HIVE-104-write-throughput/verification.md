---
tags: [spec, verification, templates]
created: "2026-05-20"
---

# Verification - HIVE-104-write-throughput

## Evidence

- [x] **AC-1 (Fase A — coalescer)** → commit `851b549` (`feat(hive): coalesce multi-path git commit`). Tests: `tests/test_helpers.py::TestGitCommitCoalesce::test_coalesces_multi_path` (1 `git add` + 1 `git commit` for N paths, asserted by mock counts), `::test_noop_on_empty_paths`, plus regression coverage from `tests/test_server.py::TestVaultPatch::test_multi_patch_single_git_commit` (end-to-end commit count delta).
- [x] **AC-2 (Fase C — visibility)** → commit `ae21041` (`feat(hive): observable ghost-response suppression`). Tests: `tests/test_server.py::TestVaultHealth::test_ghost_responses_block_when_counter_nonzero` and `::test_ghost_responses_block_omitted_when_zero` (block presence/absence in `vault_health` output). WARNING log + counter increment asserted by `tests/test_compat_shim.py::test_ghost_response_counter_records_and_logs`.
- [x] **AC-3 (Fase C — observable suppression)** → commit `ae21041`. Test `tests/test_compat_shim.py::test_ghost_response_counter_records_and_logs` confirms `_patched_respond` on `_completed=True` emits the literal prefix `mcp.ghost_response.suppressed_after_cancel_ack`, makes no wire write (no original_respond call), and bumps the counter. Wire-side regression guard: `tests/test_compat_shim.py::test_classify_cancellation_race` (scenario (a) — flaky on subprocess flooding, passes deterministically when isolated).
- [x] **AC-4 (Fase B1 — opt-in batching)** → commit `b7fd347` (`feat(hive): opt-in commit=False + vault_commit + obsidian-git detect`). Tests in `tests/test_commit_policy.py`: `TestCommitFalseLeavesFileDirty` (3 tests), `TestVaultCommitTool::test_vault_commit_returns_sha_and_clears_dirty`, `::test_vault_commit_noop_when_clean`, `TestObsidianGitDetection` (4 tests), `TestVaultHealthExternalCommitter` (2 tests). Default `commit=True` regression coverage: `test_vault_write_default_commits`.

## Test status

- Test suite: `make check` → **497 passed, 1 skipped, 57 deselected in 67.73s** (smoke + flaky classifier excluded by default). Coverage **90%** across src/hive.
- Manual smoke test: not run (vault tools exercised end-to-end through `git_vault` fixture in `test_commit_policy.py`; ghost-response surfacing verified in `test_server.py::TestVaultHealth`).
- No regressions in existing test suite: **yes** (478 → 497 — added 19 new tests across `test_helpers.py` (2), `test_compat_shim.py` (3), `test_server.py::TestVaultHealth` (2), `test_commit_policy.py` (11), plus the pre-existing `test_multi_patch_single_git_commit` still passes against the new signature). No tests deleted.

## Decisions made during implementation

- **2026-05-20 — Fase C design retracted and rewritten before any code shipped.** During spec drafting, a Sonnet subagent located the upstream framing utility (`BaseSession._send_response` at `mcp/shared/session.py:337-349`) and surfaced that `RequestResponder.cancel()` already calls `_send_response(ErrorData)` at session.py:148-150 before our patched `respond()` fires. The empirical classifier `tests/test_compat_shim.py::test_classify_cancellation_race` ran 20 iterations against a real hive subprocess on Linux; **20/20 produced scenario (a)** (ErrorData wins the race; wire response is always `{"id": N, "error": {"code": 0, "message": "Request cancelled"}}`). The original ADR-007 §1 plan ("best-effort raw send") would have generated a duplicate response in 100% of cases. Fase C scope reduced from ~80 LOC (raw stdio framing + safe write) to ~30 LOC (WARNING log + counter + docstring update). ADR-007 Amendment #2 captures the retraction.

## Promotion candidates

- [x] Lesson for `hive/90-lessons.md`? **yes** — *empirical-test-before-ADR pattern: classify wire behavior with a real subprocess before designing around a cancellation race* (already captured 2026-05-20).
- [x] ADR-worthy decision for `hive/30-architecture/adr-XXX.md`? **yes** — ADR-006 (commit policy) + ADR-007 (cancellation, Amendment #2) already Accepted in vault as part of the spec-drafting work.
- [ ] New pattern candidate for `00_meta/patterns/`? **no** — captured as a hive lesson; only promote if it recurs in another project.

## Archive checklist

- [x] `proposal.md` frontmatter set to `status: archived`
- [x] Folder moved: `specs/HIVE-104-write-throughput/` -> `specs/archive/HIVE-104-write-throughput/`
- [x] Backlog entry in vault `11-tasks.md` ticked with PR link
- [x] Promotions above executed (lesson + ADR-006/007 already in vault)

> Archived 2026-05-20 after PR #104 merged in commit `953b608`.
