---
tags: [spec, verification]
created: "2026-06-05"
---

# Verification - HIVE-202-mcp-contract-gaps (PR1: vault_search `limit`)

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior). All tests in `tests/test_tool_param_aliases.py::TestVaultSearchLimit` unless noted.

- [x] AC1 (no validation error) -> `test_limit_no_validation_error`
- [x] AC2 (flat cap, alphabetical-first-N) -> `test_limit_caps_flat`
- [x] AC3 (ranked alias-equivalence, byte-identical) -> `test_limit_aliases_max_results_ranked`
- [x] AC4 (recent cap) -> `test_limit_caps_recent`
- [x] AC5 (tightest-cap precedence) -> `test_limit_max_results_precedence_tightest_cap`
- [x] AC6 (no anyOf; limit is `int = 0`) -> `TestSchemaClean::test_no_tool_schema_contains_anyof`
- [x] AC7 (docstring call-out) -> `TestDocstringsCallOutWrongNames::test_descriptions_mention_common_wrong_names` (`WRONG_NAMES["vault_search"]` extended with `limit`)
- [x] Bonus: `max_lines` composes independently with the file cap -> `test_limit_and_max_lines_compose`

## Test status

- Targeted suite: `uv run pytest tests/test_tool_param_aliases.py tests/test_server.py::TestVaultSearchRecent tests/test_server.py::TestVaultSearch -q` -> **37 passed**.
- ruff: clean. mypy --strict on `src/hive/_vault_read.py`: clean (the 5 strict errors in `src/hive/_deadline.py` are pre-existing POSIX-only-on-Windows artifacts, unrelated; CI Linux+Windows is green).
- Full suite (`uv run pytest -q`): **680 passed, 19 skipped, 5 failed** (10m36s). 1 failure was `TestVaultSearchRecent::test_output_truncated` — a direct consequence of the intended recent-mode cap; fixed by having that test pass `max_results=200` to opt out of the file cap and still exercise `max_lines` truncation.
- No regressions introduced: the other **4 failures are pre-existing** (`test_bounded_call`, `test_daemon`, `test_lock_eviction`, `TestVaultHealthRuntime`) — **proven** by stashing this branch's changes and re-running them on the clean base, where they fail identically (none touch `vault_search`). Consistent with the documented pre-existing Windows-env failures.

## Decisions made during implementation

Brief log of non-obvious trade-offs or course corrections.

- Flat/recent capping semantics: **alphabetical-first-N** — keep flat/recent's stable path ordering, `limit` only slices; `ranked=True` is the relevance path. (decided 2026-06-05)
- `limit`/`max_results` precedence rule chosen: **tightest cap** — `min(limit, max_results) if limit else max_results`; `limit=0` means unset. (decided 2026-06-05)

## Promotion candidates

- [ ] Lesson for the repo's `docs/lessons.md`? <yes / no — one line>
- [ ] ADR-worthy decision for `docs/adr/`? <likely no — additive contract change, no architectural shift>
- [ ] New pattern candidate for `00_meta/patterns/`? <no — the alias pattern is already [[pattern-mcp-tool-design]]>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-202-mcp-contract-gaps/` -> `specs/archive/HIVE-202-mcp-contract-gaps/` (only after ALL four PRs land, or split per-PR if archived incrementally)
- [ ] Backlog entry in vault `11-tasks.md` ticked with PR link
- [ ] Promotions above executed (if any)
