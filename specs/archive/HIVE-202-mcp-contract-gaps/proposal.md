---
id: "HIVE-202-mcp-contract-gaps"
type: spec
status: archived # draft | implementing | verifying | archived
created: "2026-06-05"
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-202: MCP contract gaps

> **Naming**: file lives at `<repo>/specs/archive/HIVE-202-mcp-contract-gaps/proposal.md`.

## Outcome (archived 2026-06-05)

All four findings shipped — one atomic PR each, all merged; issue [#202](https://github.com/mlorentedev/hive/issues/202) CLOSED:

| Bug | Change | PR | Release |
|---|---|---|---|
| 3 | `vault_search` `limit` alias of `max_results` (+ cap flat/recent) | [#209](https://github.com/mlorentedev/hive/pull/209) | v1.34.0 |
| 2 | `vault_write` infers create + optional `doc_type` | [#213](https://github.com/mlorentedev/hive/pull/213) | v1.35.0 |
| 1 | `HIVE_VAULT_PATH` discoverability (error UX + docs EN/ES) | [#215](https://github.com/mlorentedev/hive/pull/215) | v1.35.1 |
| 4 | `vault_delete` (dedicated tool, git-recoverable, no-confirm) | [#217](https://github.com/mlorentedev/hive/pull/217) | 1.36.0 (pending #218) |

Follow-ups spun out: semantic `vault_ask` ([#211](https://github.com/mlorentedev/hive/issues/211)), test-isolation/Windows-env debt ([#212](https://github.com/mlorentedev/hive/issues/212)), dotfiles windowless upgrade task ([dotfiles#230](https://github.com/mlorentedev/dotfiles/issues/230)).

## Why

<!-- from 11-tasks.md: HIVE-202 — MCP contract gaps from the Hermes beta test (#202, tested v1.32.3). Four findings, decomposed into atomic PRs; PR1 = Bug 3 (limit). Continues HIVE-119 alias doctrine but adds a real behaviour change. -->

A Hermes-agent beta test against Hive v1.32.3 (issue [#202](https://github.com/mlorentedev/hive/issues/202)) surfaced four MCP-contract gaps where the documented or intuitively-expected tool surface disagrees with the implementation. Each makes a client (Claude or Hermes) call a tool, get an `unexpected_keyword_argument` / "Section is required" rejection, retry, and from the operator's seat Hive *looks* flaky even though the per-call path is healthy — the same DX failure mode that motivated [[HIVE-119]]. This spec frames all four; **PR1 fixes only Bug 3 (`vault_search` `limit`)**, the cleanest and most reproducible gap. Bugs 2/1/4 follow as separate atomic PRs (one logical change each, per the repo's ~300-LOC PR limit).

## What

After **PR1**, `vault_search(query=Q, limit=N)` stops raising `unexpected_keyword_argument` and instead bounds the number of result **files** to `N` — in **all three search modes** (flat, ranked, recent), not just ranked. Concretely:

- `limit` is accepted as an **int alias of `max_results`** (canonical), normalized before the tool body runs — exactly the [[HIVE-119]] alias pattern (`limit = …` resolution at the top of the handler).
- `max_results` (today only honored in **ranked** mode) is **extended** to cap the result-file count in **flat** and **recent (`since_days`)** modes too. This is a deliberate behaviour change, not a pure alias — see Risks.
- The JSON Schema stays `anyOf`-free: `limit` is `int = 0` (empty-value default), never `int | None`, per the load-bearing MCP schema rule and [[pattern-mcp-tool-design]].

The remaining three findings are scoped here for context but **out of PR1**:

| Bug | Finding (v1.32.3) | Reality on current `master` | Later PR |
|---|---|---|---|
| 2 | `vault_write(project, path, content)` for a new file → "Section is required" | `create` mode **already exists** (needs `operation="create"` + `path` + `doc_type`); the intuitive call falls through to `append`. Ergonomics/discoverability, not a missing feature. | PR2 |
| 1 | `HIVE_VAULT_PATH` not configurable | Honored via pydantic-settings (+ `VAULT_PATH` alias) but **env-only / undiscoverable**; "Vault not found" gives no hint. | PR3 (docs + error UX) |
| 4 | No way to delete files | No `vault_delete` / `operation="delete"` anywhere. Destructive → needs guardrails. | PR4 (own spec slice) |

## Out of scope

- **Bugs 2, 1, 4** — separate atomic PRs; not in PR1's diff.
- **No new `anyOf`.** `limit` is simple-typed with an empty-value default (`int = 0`), never `| None`.
- **No change to flat/ranked output *shape*** beyond the file-count cap (same headers, same per-file line budget, same `max_lines` truncation as a second, independent guard).
- **No re-ranking of flat/recent modes.** `limit` caps the existing (alphabetical) ordering; it does NOT turn flat search into relevance-ranked search (that is what `ranked=True` is for).
- **No removal or renaming of `max_results` or `max_lines`.** Both stay; `limit` is additive.

## Risks / open questions

> Both items below were resolved with the maintainer on 2026-06-05; `tasks.md` is cleared to freeze.

- **[RESOLVED 2026-06-05] Flat/recent capping semantics → alphabetical-first-N.** Flat and recent modes keep their current **alphabetical path order** (`sorted(rglob("*.md"))` / `sorted(git_paths)`); `limit` just slices the first N matching files — no re-ordering. The docstring states the caveat and points callers to `ranked=True` for relevance ordering. (Rejected: sorting flat by match-count, which would change flat's stable path ordering — a larger behaviour change.)
- **[RESOLVED 2026-06-05] Precedence when BOTH `limit` and `max_results` are supplied → tightest cap.** Both are `max` caps, so the smaller wins: `max_results = min(limit, max_results) if limit else max_results` (a `limit` of `0` means "unset"). Predictable, and never returns *more* than the tighter cap requested. (Rejected: alias-first OR `limit or max_results`, which could return more than an explicit smaller `max_results`; and canonical-wins-when-non-default, which hardcodes the `10` default and cannot detect an explicit `10`.)
- **Regression surface (LOW).** `limit` is additive with an empty default, so existing callers are unaffected. The *behaviour change* is that flat/recent now cap at `max_results` (default 10) — callers who relied on flat search returning *every* matching file would see at most 10 unless they raise `max_results`/`limit`. Mitigation: default stays 10 (already the ranked default); documented in the docstring + CHANGELOG; covered by a test asserting the pre-change unbounded behaviour is now bounded.
- **`max_lines` vs file cap interaction.** `max_lines` (output-line truncation) remains an independent second guard applied after the file cap. A test pins that both guards compose (file cap first, line truncation second).

## Acceptance criteria

Observable outcomes. Each must be testable. (All MUST-RESOLVE questions resolved 2026-06-05.)

- [x] AC1 — `vault_search(query=Q, limit=5)` raises **no** validation error (no `unexpected_keyword_argument`).
- [x] AC2 — flat mode: `vault_search(query=Q, limit=N)` returns at most `N` result-file blocks (alphabetical-first-N) when more than `N` files match.
- [x] AC3 — ranked mode: `vault_search(query=Q, ranked=True, limit=N)` is byte-identical to `vault_search(query=Q, ranked=True, max_results=N)`.
- [x] AC4 — recent mode: `vault_search(query=Q, since_days=D, limit=N)` returns at most `N` files when more than `N` changed.
- [x] AC5 — tightest cap wins when both supplied: `vault_search(ranked=True, limit=5, max_results=3)` → ≤3 files; `limit=5, max_results=20` → ≤5.
- [x] AC6 — no registered tool's JSON Schema contains `anyOf` (schema-introspection test; the load-bearing `| None` ban holds).
- [x] AC7 — `vault_search`'s docstring leads with the canonical `max_results`/`max_lines` and explicitly calls out `limit` as the accepted alias.

## References

- Vault: `10_projects/hive/11-tasks.md` (backlog entry **HIVE-202**)
- GitHub issue: [#202](https://github.com/mlorentedev/hive/issues/202) (beta-test findings + maintainer triage comment)
- Predecessor spec: `specs/HIVE-119-tool-param-aliases/` (the alias doctrine this PR continues)
- Related patterns: `00_meta/patterns/pattern-mcp-tool-design.md` (the `| None` ban / schema-clean rule)
- Related: `.claude/CLAUDE.md` "MCP tool schema rules (load-bearing)"
