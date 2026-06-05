---
tags: [spec, tasks]
created: "2026-06-05"
---

# Tasks - HIVE-202-mcp-contract-gaps (PR1: vault_search `limit`)

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.
> Scope of THIS tasks.md = **PR1 (Bug 3, `limit`)** only. Bugs 2/1/4 get their own tasks slices.

## Setup

- [x] Branch created from remote base: `feat/HIVE-202-vault-search-limit` off `origin/master`
- [x] `proposal.md` complete and acceptance criteria testable
- [x] **Both "MUST-RESOLVE" open questions answered** (flat/recent → alphabetical-first-N; precedence → tightest cap `min`) — 2026-06-05

## Implementation

> Inline normalization at the top of the `vault_search` body (`max_results = … limit …`, per the agreed precedence rule). Extend the existing `max_results` cap from ranked-only to flat + recent. `limit` is simple-typed (`int = 0`) so the JSON Schema stays `anyOf`-free. New tests join `tests/test_tool_param_aliases.py`.

- [x] Write failing test: `vault_search(query=Q, limit=5)` raises no validation error (AC1)
- [x] Write failing test: ranked mode — `limit=N` == `max_results=N`, byte-identical (AC3)
- [x] Write failing test: flat mode — `limit=N` caps result-file blocks to N when >N match (AC2)
- [x] Write failing test: recent mode — `since_days=D, limit=N` caps to N files (AC4)
- [x] Write failing test: precedence — both `limit` and `max_results` supplied → tightest cap (AC5)
- [x] Write failing test: `max_lines` still composes as an independent second guard
- [x] Implement `limit` alias + tightest-cap resolution at top of `vault_search` (`src/hive/_vault_read.py`)
- [x] Implement `max_results` cap in flat mode (file counter + early break, alphabetical-first-N)
- [x] Implement `max_results` cap in recent mode (`git_paths` slice after sort)
- [x] Reuse HIVE-119 schema guard — no registered tool `inputSchema` contains `anyOf` (AC6)
- [x] Extend `WRONG_NAMES["vault_search"]` with `limit` (AC7)
- [x] Update `vault_search` docstring (lead with `max_results`, call out `limit`) to make it pass
- [x] Fix `TestVaultSearchRecent::test_output_truncated` to opt out of the new file cap (`max_results=200`)

## Closing

- [ ] Every acceptance criterion from `proposal.md` covered by ≥1 test
- [ ] Every acceptance criterion has a matching `features.json` entry with a non-vacuous verification command
- [ ] Type checks pass (`mypy --strict src/`)
- [ ] Lint passes (`ruff check src/ tests/`)
- [ ] Full suite green (`make test`)
- [ ] CHANGELOG-worthy note: flat/recent now cap at `max_results` (behaviour change) — captured in the PR body
- [ ] No unrelated changes in the diff (no scope creep — Bugs 2/1/4 excluded)
- [ ] `verification.md` filled in
- [ ] PR opened referencing this spec folder, addressing the Bug 3 portion of #202

## Machine-readable features

Sibling `features.json` follows [[pattern-feature-list-as-primitive]]. Each acceptance criterion maps to ≥1 feature with `id`, `behavior`, `verification` (executable command), `state`, `evidence`.

**Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running `verification` and capturing exit code 0, may set that terminal state.
