---
id: "HIVE-119-tool-param-aliases"
type: spec
status: draft # draft | implementing | verifying | archived
created: "2026-05-31"
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-119-tool-param-aliases

> **Naming**: file lives at `<repo>/specs/HIVE-119-tool-param-aliases/proposal.md`.

## Why

<!-- from 11-tasks.md: — DX: clients repeatedly guess wrong MCP tool parameter names (75 `unexpected_keyword_argument` rejections; [#151](https://github.com/mlorentedev/hive/issues/151)). Hybrid fix (2026-05-30): tighten docstrings + accept real aliases only (`subpath→path`, `old_string/new_string→find/replace`, `regex→use_regex`, `identifier→path/section`); phantom params NOT added. Touches public contract → SDD-gated. Spec: `specs/HIVE-119-tool-param-aliases/`. -->

Across the maintainer's live debug logs (`~/.local/share/hive/hive-*.log`) there are **75** `Invalid arguments … unexpected_keyword_argument` rejections where a client (Claude itself included) calls a hive tool with the wrong parameter name. The call fails, the agent retries with a corrected name, and from the user's seat hive *appears* flaky/slow even though the per-call path is healthy. The wrong names are not random typos — they cluster into a few highly guessable patterns (Edit-tool muscle memory, plausible synonyms, sibling-tool bleed), which makes them fixable rather than inherent. This is issue [#151](https://github.com/mlorentedev/hive/issues/151), a DX finding surfaced during the 2026-05-29 concurrency-slowness investigation.

## What

After this PR, the highest-frequency wrong parameter names are **accepted as aliases** that normalize to the canonical parameter before the tool body runs, and every affected tool's docstring **leads with the canonical name and explicitly calls out the common wrong name**. Concretely, all of the following stop raising `unexpected_keyword_argument` and behave identically to their canonical form:

| Tool | Accepted alias | Normalizes to |
|---|---|---|
| `vault_list` | `subpath` | `path` |
| `vault_patch` | `old_string` / `new_string` | `find` / `replace` |
| `vault_search` | `regex` (bool) | `use_regex` |
| `vault_query` | `identifier` | `path` |

When both the canonical parameter and its alias are supplied, the **canonical value wins** and the alias is ignored (documented, deterministic). The JSON Schema for every tool stays `anyOf`-free — aliases are simple-typed with empty-value defaults (`str = ""`, `bool = False`), never `| None`, per [[pattern-mcp-tool-design]].

## Out of scope

- **Phantom params are NOT added.** Wrong guesses that map to no real parameter on the target tool — `vault_commit(project=)`, `session_briefing(days=)`, `capture_lesson(commit=)`, `vault_list(scope=)` — are addressed by docstring clarification only. Adding them as real params would lie in the schema and could *encourage* misuse.
- No change to tool *behavior* beyond accepting the alias (same return shape, same side effects).
- No process/transport/middleware changes — argument interception at the FastMCP boundary is explicitly rejected here (it couples to FastMCP internals); that operating-model layer is Phase C / HIVE-118.
- No alias for `section` on `vault_query` (the `identifier` alias maps to `path` only — see Risks).

## Risks / open questions

- **`identifier` → `path` vs `section` ambiguity (RESOLVED).** "identifier" reads as a file identifier, so it maps to `path`. Mapping to `section` would break when the value is a real path. Decision: alias to `path` only; docstring tells callers to use `section` for shortcuts.
- **Bool alias cannot distinguish unset from `False`.** `regex=False` is indistinguishable from "not passed". Mitigated by OR semantics: `use_regex = use_regex or regex` — the alias can only *force on*, never override an explicit canonical `True`. Acceptable because nobody passes `regex=False` meaningfully.
- **Schema surface grows by 5 params across 4 tools.** Accepted trade-off (the chosen hybrid). Docstrings mark each as an alias so the canonical name stays preferred. No `anyOf` introduced (verified by a schema-introspection test).
- **Regression surface.** Aliases are additive with empty defaults, so existing callers are unaffected; covered by asserting alias-call == canonical-call for each tool.

## Acceptance criteria

Observable outcomes. Each must be testable.

- [ ] `vault_list(project=P, subpath=X)` returns byte-identical output to `vault_list(project=P, path=X)` and raises no validation error.
- [ ] `vault_patch(project=P, path=F, old_string=A, new_string=B)` behaves identically to `vault_patch(project=P, path=F, find=A, replace=B)`.
- [ ] `vault_search(query=Q, regex=True)` behaves identically to `vault_search(query=Q, use_regex=True)`.
- [ ] `vault_query(project=P, identifier=X)` behaves identically to `vault_query(project=P, path=X)`.
- [ ] When both canonical and alias are supplied, the canonical value wins (alias ignored) — asserted for at least one tool.
- [ ] Every affected tool's JSON Schema contains no `anyOf` (schema-introspection test over the registered tools).
- [ ] Docstrings of `vault_list`, `vault_query`, `vault_search`, `vault_patch`, `vault_commit`, `session_briefing`, `capture_lesson` name the canonical param(s) and call out the common wrong name.

## References

- Vault: `10_projects/hive/11-tasks.md` (backlog entry **HIVE-119**)
- GitHub issue: [#151](https://github.com/mlorentedev/hive/issues/151)
- Related patterns: `00_meta/patterns/pattern-mcp-tool-design.md` (the `| None` ban / schema-clean rule)
- Related: `.claude/CLAUDE.md` "MCP tool schema rules (load-bearing)"
