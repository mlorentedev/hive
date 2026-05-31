---
tags: [spec, tasks, templates]
created: "2026-05-31"
---

# Tasks - HIVE-119-tool-param-aliases

> TDD order. One task = one focused commit. Tick as you go. Reorder freely while spec is in `draft` state; freeze once you start `implementing`.

## Setup

- [x] Branch created from main: `feat/HIVE-119-tool-param-aliases`
- [x] `proposal.md` is complete and acceptance criteria are testable
- [x] No open questions left in `proposal.md` "Risks / open questions" (`identifier→path`, bool-alias OR-semantics, conflict→canonical-wins all decided)

## Implementation

> Inline normalization at the top of each tool body (`canonical = canonical or alias`). No middleware, no FastMCP-boundary interception. Aliases are simple-typed (`str = ""`, `bool = False`) so the JSON Schema stays `anyOf`-free. New tests live in `tests/test_tool_param_aliases.py`.

- [ ] Write failing test: `vault_list(project=P, subpath="sub")` == `vault_list(project=P, path="sub")`, no validation error
- [ ] Implement `vault_list` `subpath` alias (`path = path or subpath`) in `src/hive/_vault_read.py`
- [ ] Write failing test: `vault_patch(... old_string=A, new_string=B)` == `vault_patch(... find=A, replace=B)`
- [ ] Implement `vault_patch` `old_string`/`new_string` aliases in `src/hive/_vault_write.py`
- [ ] Write failing test: `vault_search(query=Q, regex=True)` == `vault_search(query=Q, use_regex=True)`
- [ ] Implement `vault_search` `regex` alias (`use_regex = use_regex or regex`) in `src/hive/_vault_read.py`
- [ ] Write failing test: `vault_query(project=P, identifier=X)` == `vault_query(project=P, path=X)`
- [ ] Implement `vault_query` `identifier` alias (`path = path or identifier`) in `src/hive/_vault_read.py`
- [ ] Write failing test: conflict — `vault_list(path="canon", subpath="alias")` resolves to `canon` (canonical wins)
- [ ] Write failing test: schema guard — every registered tool's `inputSchema` contains no `anyOf` (introspect via FastMCP)
- [ ] Write failing test: docstrings of the 7 affected tools name the canonical param AND mention the common wrong name
- [ ] Tighten docstrings (`vault_list`, `vault_query`, `vault_search`, `vault_patch`, `vault_commit`, `session_briefing`, `capture_lesson`) to make it pass

## Closing

- [ ] Every acceptance criterion from `proposal.md` is covered by at least one test
- [ ] Every acceptance criterion has a matching entry in `features.json` with a non-vacuous verification command
- [ ] Type checks pass (`mypy --strict src/`)
- [ ] Lint passes (`ruff check src/ tests/`)
- [ ] Full suite green (`make test`)
- [ ] No unrelated changes in the diff (no scope creep)
- [ ] `verification.md` filled in
- [ ] PR opened referencing this spec folder, closes #151

## Machine-readable features

This spec emits a sibling `features.json` following [[pattern-feature-list-as-primitive]]. Each acceptance criterion maps to ≥1 feature with `id`, `behavior`, `verification` (executable command), `state`, `evidence`.

**Pass-state gating:** the agent CANNOT write `"state": "passing"` — only the harness, after running `verification` and capturing exit code 0, may set that terminal state.
