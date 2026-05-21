---
tags: [spec, tasks, templates]
created: "2026-05-20"
---

# Tasks - HIVE-104-write-throughput

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.

## Setup

- [x] Branch created from main: `feat/HIVE-104-write-throughput`
- [x] `proposal.md` is complete and acceptance criteria are testable
- [x] No open questions left in `proposal.md` "Risks / open questions"

## Implementation

> TDD order. One bullet = one focused commit. Phases are sequenced by risk: A (coalescer, atomic signature change) → C (observability shim) → B1 (opt-in API + new tool + docs).

### Fase A — `_git_commit` coalescer (signature: `paths: list[Path]`)

- [x] Failing test `tests/test_helpers.py::test_git_commit_coalesces_multi_path` — `_git_commit(vault, [p1, p2, p3], "msg")` issues exactly **1** `git add` (with all 3 paths) and **1** `git commit` (mock `subprocess.run`)
- [x] Change signature `_git_commit(vault_path, rel_path: Path, message)` → `_git_commit(vault_path, rel_paths: list[Path], message)` in `src/hive/_helpers.py`; `git add` now invoked as `["git", "add", *map(str, rel_paths)]`
- [x] Failing test `tests/test_helpers.py::test_git_commit_noop_on_empty_paths` — empty list is a logged no-op (no subprocess call)
- [x] Update `src/hive/_vault_write.py` callers: `vault_write` create (line ~125), `vault_write` append/replace (line ~192), `vault_patch` (line ~331) — pass `[rel]` for single-file paths
- [x] Update `src/hive/_workers.py` callers: `capture_lesson` inline (line ~479), `capture_lesson` batch (line ~433) — pass `[rel]`
- [x] Failing test `tests/test_vault_patch.py::test_multi_patch_single_commit` — a `vault_patch` call with N patches in a single invocation issues exactly 1 add + 1 commit (mock `subprocess.run`, count calls)
- [x] Run full suite; verify no regressions in the 478 existing tests

### Fase C — observable ghost-response suppression

- [x] Failing test `tests/test_compat_shim.py::test_ghost_response_increments_counter` — driving `_patched_respond` on a `_completed=True` responder emits a WARNING log with literal prefix `mcp.ghost_response.suppressed_after_cancel_ack` AND increments a module-level counter
- [x] Add `_GhostResponseCounter` thread-safe class in `src/hive/_compat.py` with `record(tool: str)` + `snapshot() -> dict[str, object]` (fields: `total: int`, `last_seen: str|None`, `last_tool: str|None`); singleton instance `GHOST_RESPONSES`; upgrade log level from DEBUG → WARNING in `_patched_respond`
- [x] Failing test `tests/test_vault_health.py::test_vault_health_surfaces_ghost_responses` — when counter has entries, `vault_health()` output includes `ghost_responses: {total: N, last_seen: ..., last_tool: ...}` block
- [x] Wire counter snapshot into `health_report_text` (`src/hive/_vault_health.py`) — read from `hive._compat.GHOST_RESPONSES.snapshot()`, render as a section after Duplicate Names
- [x] Update docstring of `_patched_respond` in `_compat.py` to document the semantic mismatch per ADR-007 Amendment #2 ("ErrorData ack ≠ rollback; verify state via `vault_query`, do not retry")

### Fase B1 — opt-in `commit=False` + `vault_commit` tool + obsidian-git detection

- [x] Failing test `tests/test_commit_policy.py::test_vault_write_commit_false_leaves_file_dirty` — `vault_write(commit=False)` writes the file but `git status --porcelain` shows it dirty AND response contains `"committed": false`
- [x] Add `commit: bool = True` parameter to `vault_write` in `src/hive/_vault_write.py`; skip `_git_commit` call when `commit=False`; append `(uncommitted)` to success response
- [x] Failing test `tests/test_commit_policy.py::test_vault_patch_commit_false_leaves_file_dirty` — same contract for `vault_patch`
- [x] Add `commit: bool = True` parameter to `vault_patch`; skip `_git_commit` when False
- [x] Failing test `tests/test_commit_policy.py::test_vault_commit_tool_returns_sha_and_clears_dirty` — `vault_commit(message="batch")` returns a 40-char SHA AND `git status --porcelain` is empty after
- [x] Add new tool `vault_commit(message: str = "")` in new module `src/hive/_vault_commit.py` (or in `_vault_write.py`); runs `git add -A && git commit -m <message>` under `vault_write_lock`; returns SHA string
- [x] Register `vault_commit` tool in `src/hive/server.py` (`register_vault_write` or new `register_vault_commit`)
- [x] Failing test `tests/test_commit_policy.py::test_obsidian_git_detection_windows_path` — with mocked `<vault>/.obsidian/plugins/obsidian-git/data.json` containing `{"commitInterval": 10}` (use `pathlib.Path` joining; verify both POSIX and Windows path separators work)
- [x] Add `detect_obsidian_git(vault: Path) -> dict | None` in `src/hive/_helpers.py` — returns `{"commit_interval": N}` if data.json exists with `commitInterval > 0`, else None; use `pathlib.Path` joining (Risk #4)
- [x] Failing test `tests/test_vault_health.py::test_vault_health_surfaces_external_committer` — when obsidian-git is detected, `vault_health()` includes `external_committer: "obsidian-git"`
- [x] Wire `detect_obsidian_git` into `health_report_text` — render as `external_committer: "obsidian-git"` if detected; otherwise omit the field
- [x] Failing test `tests/test_commit_policy.py::test_commit_false_response_contract` — confirm response JSON includes `"committed": false` flag for both `vault_write` and `vault_patch`
- [x] Update docstrings of `vault_write`, `vault_patch`, and `vault_commit` to document the durability contract (Risk #3: files on disk but not in git; crash before flush loses the *commit*, not the content)
- [x] Update `src/hive/server.py` `instructions` block to mention `vault_commit` + `commit=False` pairing

## Closing

- [x] Every acceptance criterion from `proposal.md` is covered by at least one test
- [ ] Every acceptance criterion has a matching entry in `features.json` (see below) with a non-vacuous verification command — *deferred; verification.md evidence section serves as the harness contract for this PR*
- [x] `README.md` gains a `## Requirements` section with three tiers — **Required** (Python 3.12+, any markdown folder), **Recommended** (git in the vault, Obsidian app, obsidian-git plugin with auto-commit 5–10 min), **Optional** (Ollama, OpenRouter API key, backup git remote). Plus a `### Recommended configuration` subsection that walks through pairing `commit=False` with obsidian-git, citing [[adr-006-commit-policy]]
- [x] `site/src/content/docs/configuration.md` (EN) mirrors the README Requirements structure + adds the semantic-mismatch note for cancellation (per [[adr-007-mcp-cancellation-response]]: "ErrorData ack does NOT imply rollback — verify state via `vault_query`")
- [x] `site/src/content/docs/es/configuration.md` (ES) mirror — per repo rule "edit English first, then mirror to es/"
- [x] `site/astro.config.mjs` sidebar updated with bilingual labels if a new page was added — *no new page added; `vault_commit` lives in existing `tools/vault.md` (EN+ES). Sidebar unchanged.*
- [x] All three docs surfaces (README + EN + ES) reference the same three tiers in the same order; reviewer pass verifies parity
- [ ] CI green on `ubuntu-latest` **and** `windows-latest` (verifies cross-platform path resolution per Risk #4) — *verified in CI on PR open*
- [x] Type checks pass (`make typecheck`)
- [x] Lint passes (`make lint`)
- [x] Full suite passes (`make test`) — no regressions in the existing 478 tests (now 497)
- [x] No unrelated changes in the diff (no scope creep)
- [x] `verification.md` filled in
- [ ] PR opened referencing this spec folder — *this commit*

## Machine-readable features

This spec emits a sibling `features.json` (alongside this file) following [[pattern-feature-list-as-primitive]]. The JSON is the harness-facing contract: each acceptance criterion maps to ≥1 feature with `id`, `behavior`, `verification` (executable command), `state` (lifecycle), and `evidence` (harness-captured output).

**Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running `verification` and capturing exit code 0, may set that terminal state. Reviewers must reject PRs where features.json contains `passing` entries with empty `evidence`.

Minimal `features.json` skeleton (drop into `<repo>/specs/HIVE-104-write-throughput/features.json`):

```json
[
  {
    "id": "HIVE-104-write-throughput-f1",
    "behavior": "<one-line copy of an acceptance criterion>",
    "verification": "<single shell command; exit 0 means pass>",
    "state": "pending",
    "evidence": ""
  }
]
```
