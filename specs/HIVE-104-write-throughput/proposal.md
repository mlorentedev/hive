---
id: "HIVE-104-write-throughput"
type: spec
status: verifying # draft | implementing | verifying | archived
created: "2026-05-20"
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-104: Write throughput

> **Naming**: file lives at `<repo>/specs/HIVE-104-write-throughput/proposal.md`. `HIVE-104-write-throughput` is `YYYY-MM-DD-<slug>` or `<TICKET-NN>`.

## Why

<!-- from 10_projects/hive/30-architecture/adr-006-commit-policy.md: per-write `git commit` = 2 `subprocess.run` calls (50–500 ms) makes multi-write user flows degrade linearly; ghost-response race amplified by long write window -->
<!-- from 10_projects/hive/30-architecture/adr-007-mcp-cancellation-response.md: `_compat._patched_respond` silently drops responses after `notifications/cancelled`, invisible at default INFO log level, producing user-visible "ghost responses" where successful writes never reach the client -->

Hive becomes severely slow or crashes under write-heavy flows because every `vault_write` and `vault_patch` invokes a synchronous `git commit` (50–500 ms each call). When the client cancels via tool-call timeout, the `_compat.py` shim silently drops the successful response, producing "ghost responses" that force the client to retry — generating extra load, user confusion, and making the bug invisible in logs because the suppression is DEBUG-level. If we don't ship this, the tool becomes impractical for growing corpora (>300 lessons) and for compound flows (spec scaffolding, mass refactors, bulk imports).

## What

After this change, Hive exposes:

1. **Opt-in batched writes** — `vault_write` and `vault_patch` accept a new `commit: bool = True` parameter; when `False`, the file is written to disk under `_WRITE_LOCK` but no `git commit` is invoked. The response payload includes `{"committed": false}` so the caller knows. Per-call cost drops from ~150 ms to ~5–15 ms.
2. **Explicit flush tool** — new MCP tool `vault_commit(message: str = "")` runs `git add -A && git commit -m <message>` against the vault and returns the commit SHA.
3. **Observable cancellation suppression** — `_compat._patched_respond` keeps silent suppression when `_completed=True` (empirically confirmed safest; see Risk #1), but emits a WARNING log line with the literal prefix `mcp.ghost_response.suppressed_after_cancel_ack` and increments a counter exposed in `vault_health` as `ghost_responses: {total, last_seen, last_tool}`. README + docs explain to callers that this signal means "the operation may have completed on disk despite the cancellation ack — verify via `vault_query` rather than retrying".
4. **Obsidian-git auto-detection** — on startup, Hive inspects `<vault>/.obsidian/plugins/obsidian-git/data.json`; if `commitInterval > 0`, logs an INFO line and `vault_health` surfaces "obsidian-git active — `commit=False` is safe".

## Out of scope

1. **In-process background flusher / auto-commit timer** — explicitly rejected for the second time in [[adr-006-commit-policy]] §C (collision risk with obsidian-git, complex crash recovery semantics, multi-process flusher coordination). Re-evaluate only if measurements after this PR show sustained pain that obsidian-git + `commit=False` cannot resolve.
2. **Flipping the default to `commit=False`** — this PR ships the opt-in flag only. Changing the default is a separate, breaking change deferred to a future minor version once usage data exists.
3. **`pygit2` / native libgit2 bindings** — deferred per [[adr-006-commit-policy]] §D. Would reduce per-write cost further but adds a C-dependency and requires fresh multi-process safety analysis. Orthogonal optimization; reconsider only if needed.

## Risks / open questions

1. **[RESOLVED 2026-05-20]** **Cancellation-race wire behavior in Fase C** — Empirically classified: `tests/test_compat_shim.py::test_classify_cancellation_race` ran 20 iterations against a real hive subprocess on Linux. **All 20 iterations produced scenario (a)** — `RequestResponder.cancel()`'s `_send_response(ErrorData)` always wins the race; the wire receives `{"id": N, "error": {"code": 0, "message": "Request cancelled"}}`. Scenarios (b) "our success wins" and (c) "both lost" were not observed. **Implication:** the original "best-effort raw send" design in [[adr-007-mcp-cancellation-response]] §1 would produce a DUPLICATE response in 100% of cases — protocol violation worse than the status quo. **Fase C plan revised** (see [[adr-007-mcp-cancellation-response]] Amendment #2): keep silent suppression as the delivery behavior, but make it observable via WARNING log `mcp.ghost_response.suppressed_after_cancel_ack` + counter in `vault_health`; document the semantic mismatch (clients receive ErrorData but disk state may be mutated; correct client behavior is to query state via `vault_query`, not retry).
2. **`_git_commit` signature change ripples** — the coalescer requires `_git_commit` to accept `paths: list[str]` instead of `path: str`. Call sites: `_vault_write.py` (3 sites: create/append/replace + vault_patch) and `_workers.py` (2 sites: `capture_lesson` inline + batch). All must update in lockstep. **Resolution during code, not blocking:** inventory all callers and update signature atomically.
3. **`commit=False` durability contract** — without explicit documentation, callers may assume `commit=False` is "fire and forget"; in reality, files are on disk but not in git, so a crash before `vault_commit` (or before the next obsidian-git tick) loses the *commit* (not the file content). **Resolution during docs, not blocking:** README + site docs must state this contract explicitly.
4. **Cross-platform path resolution for obsidian-git detection** — Hive runs on Linux, macOS, and Windows ([[adr-005-transport-and-scale]]); the maintainer rotates daily across all three OSes. Detection of `<vault>/.obsidian/plugins/obsidian-git/data.json` MUST use `pathlib.Path` joining and never hardcoded forward slashes; on Windows, the data file path uses backslashes and a naive string concat will silently miss the file, disabling the auto-detection feature without any error. **Resolution during code, not blocking:** add a unit test exercising path joining with mocked `pathlib.WindowsPath`, and gate the merge on CI green for `ubuntu-latest` **and** `windows-latest`.
5. **Documentation drift between README and site docs (EN vs ES)** — Hive's bilingual rule (`.claude/CLAUDE.md`: *"any doc change must update both languages — edit English first, then mirror to es/"*) is process-level, not enforced by tooling. Adding a Requirements section across three surfaces (README + EN site + ES site) raises the surface for drift. **Resolution during code, not blocking:** Closing checklist enforces all three updated in the same PR; reviewer pass verifies heading parity. Follow-up automation (CI step that diffs headings between EN and ES) deferred to a separate task if drift recurs in practice.

## Acceptance criteria

- [ ] **AC-1 (Fase A — coalescer):** A `vault_patch` call with N patches in a single invocation issues exactly **1** `git add` and **1** `git commit` subprocess call (asserted by unit test mocking `subprocess.run`). Multi-patch path wall-clock improves ≥ **40%** vs baseline measured on a vault with ≥100 files.
- [ ] **AC-2 (Fase A — visibility):** `vault_health` output includes a `ghost_responses: {total: N, last_seen: <iso8601|null>, last_tool: <str|null>}` block. When a response is suppressed, a WARNING log line containing the literal prefix `mcp.ghost_response` is emitted **and** the counter increments by 1. Verified by unit test driving a `_completed=True` responder.
- [ ] **AC-3 (Fase C — observable suppression):** When `_patched_respond` is invoked on a `_completed=True` responder, **no wire bytes are written** (silent suppression preserved per empirical Risk #1 resolution), AND a WARNING log line containing the literal prefix `mcp.ghost_response.suppressed_after_cancel_ack` with the tool name and request id is emitted, AND `vault_health` reflects the increment. Verified by unit test driving a `_completed=True` responder and asserting both the log line and the counter delta. The existing `tests/test_compat_shim.py::test_classify_cancellation_race` (already in tree from the spec-drafting investigation) remains as a regression guard that the wire behavior continues to match scenario (a).
- [ ] **AC-4 (Fase B1 — opt-in batching + obsidian-git delegation):** `vault_write(commit=False)` and `vault_patch(commit=False)` write the file but leave `git status --porcelain` showing it dirty; response payload contains `"committed": false`. `vault_commit(message="...")` returns a 40-char SHA and clears the dirty state. When `<vault>/.obsidian/plugins/obsidian-git/data.json` is present with `commitInterval > 0`, `vault_health` includes `external_committer: "obsidian-git"`. All verified by integration tests in `tests/test_commit_policy.py`.

## References

- Vault: `10_projects/hive/11-tasks.md` (backlog entry — pending; will be added on first PR open)
- Related ADRs: [[adr-006-commit-policy]], [[adr-007-mcp-cancellation-response]]
- Upstream-blocking: [[adr-005-transport-and-scale]] (this spec is the incremental answer to its rejected "Option C")
- Related upstream issue: modelcontextprotocol/python-sdk#2610 (target for `_compat.py` removal once fixed)
