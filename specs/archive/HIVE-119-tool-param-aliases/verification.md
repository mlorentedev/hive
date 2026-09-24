---
tags: [spec, verification, templates]
created: "2026-05-31"
---

# Verification - HIVE-119-tool-param-aliases

## Evidence

All tests live in `tests/test_tool_param_aliases.py` (7 tests, all green).

- [x] `vault_list(subpath=)` == `vault_list(path=)` -> test `TestRealAliases::test_vault_list_subpath_aliases_path`
- [x] `vault_patch(old_string=/new_string=)` == `find`/`replace` -> test `TestRealAliases::test_vault_patch_old_new_string_alias`
- [x] `vault_search(regex=True)` == `use_regex=True` -> test `TestRealAliases::test_vault_search_regex_aliases_use_regex`
- [x] `vault_query(identifier=)` == `vault_query(path=)` -> test `TestRealAliases::test_vault_query_identifier_aliases_path`
- [x] Conflict — canonical wins over alias -> test `TestConflictCanonicalWins::test_vault_list_path_beats_subpath`
- [x] No `anyOf` in any registered tool schema -> test `TestSchemaClean::test_no_tool_schema_contains_anyof`
- [x] Docstrings call out the common wrong name (7 tools) -> test `TestDocstringsCallOutWrongNames::test_descriptions_mention_common_wrong_names`

## Test status

- New suite: `uv run pytest tests/test_tool_param_aliases.py -q` -> **7 passed**
- Full suite: `make check` (ruff + mypy --strict + pytest --cov) -> **exit 0; 606 passed, 2 skipped, 62 deselected; 87% coverage; 163.94s**
- No regressions: yes — full suite green with only the 3 source files + 1 test file changed.
- Diff scope: 35 production LOC across `_vault_read.py`, `_vault_write.py`, `_workers.py` (no unrelated changes).

## Decisions made during implementation

- **Inline normalization, not a FastMCP middleware.** Validation happens at the FastMCP boundary before the handler runs, so the only schema-clean way to *accept* an alias is to add it to the signature and normalize in-body (`canonical = canonical or alias`). Middleware interception was rejected (couples to FastMCP internals; that layer is Phase C).
- **Phantom params documented, not added.** `vault_commit(project)`, `session_briefing(days)`, `capture_lesson(commit)`, `vault_list(scope)` map to no real parameter — adding them would lie in the schema and could encourage misuse. Docstrings clarify instead.
- **`identifier` -> `path` only** (not `section`): a value passed as `identifier` reads as a file path; mapping to `section` would break on real paths.
- **Bool alias OR-semantics:** `use_regex = use_regex or regex` — `regex` can only force-on, never override an explicit canonical `True` (a bool default cannot distinguish unset from `False`).
- **Docstring test asserts against `fn.__doc__`** (the full docstring SSOT) rather than `tool.description`, because FastMCP splits the docstring into a summary (`description`) plus per-parameter schema descriptions — the alias call-outs live in `Args:` and surface to the model as per-property descriptions.

## Promotion candidates

- [ ] Lesson for `hive/90-lessons.md`? **yes** — "FastMCP rejects unknown kwargs at the validation boundary; aliases must be real signature params normalized in-body, and the `| None` ban means alias params stay simple-typed." Worth capturing post-merge.
- [ ] ADR-worthy decision? **no** — additive, reversible, no architectural shift.
- [ ] New pattern for `00_meta/patterns/`? **no** — folds into the existing `pattern-mcp-tool-design`.

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-119-tool-param-aliases/` -> `specs/archive/HIVE-119-tool-param-aliases/`
- [ ] Backlog entry in vault `11-tasks.md` ticked with PR link
- [ ] Promotions above executed (if any)
