---
id: clean-code-solid-audit
type: troubleshooting
status: active
created: "2026-03-06"
owner: manu
---

# Clean Code & SOLID Audit (2026-03-06)

## Summary

Full audit of all `src/hive/` Python files for Clean Code principles, SOLID violations, and external attacker security vectors. Performed after fixing the vault_patch connection crash and anyOf schema issues.

## Security Findings (all fixed)

| Finding | Severity | Fix |
|---------|----------|-----|
| ReDoS via `vault_search` regex | MEDIUM | Pattern length capped at 200 chars |
| `list_models()` no status check before `resp.json()` | LOW | Added `if resp.status_code >= 400` guard |
| `float()` on API pricing without ValueError catch | LOW | Wrapped in try/except, defaults to 0.0 |
| Unbounded `rglob` results in `vault_list_files` | LOW | Capped at 500 entries |
| API error bodies relayed verbatim | LOW | Truncated to 200 chars |

## Security: Already Mitigated (no action needed)

- **Path traversal**: `.resolve()` + `relative_to()` on all path-based tools
- **Command injection**: subprocess list form, no `shell=True`
- **SQL injection**: parameterized queries everywhere
- **YAML injection**: `safe_load` + regex sanitization
- **SSRF**: endpoints only configurable via env var (not tool params)
- **API key exposure**: only in Authorization header, never logged
- **Insecure deserialization**: no pickle/eval/exec, only safe_load/JSON

## Clean Code Findings (tracked as refactoring tasks)

### HIGH (P2 refactoring)

| Finding | File | Lines |
|---------|------|-------|
| `create_server()` is 1366 lines (God Function) | server.py:227-1594 | All tools as closures |
| `vault_patch()` 120 lines, complexity ~13 | server.py:922-1042 | Needs helper extraction |
| `capture_lesson()` 87 lines, DRY frontmatter | server.py:1044-1130 | Shared with vault_create |
| `session_briefing()` 75 lines, 6 responsibilities | server.py:1225-1300 | Extract section builders |

### MEDIUM (P3 refactoring)

| Finding | File |
|---------|------|
| `vault_search` / `vault_smart_search` duplicate search logic | server.py:680,1173 |
| 4 `_try_*` worker functions near-identical | server.py:1410-1460 |
| SQLite init duplicated across budget/usage/relevance | 3 files |
| "Project not found" string repeated 6x | server.py |

### LOW (documented, not tracked)

- Magic numbers: `[:5]` lines per result, scoring weights, git timeout
- Undocumented constants: `_RECENCY_DAYS_SCALE`, `_WRITE_MULTIPLIER`
- Hardcoded paths in config.py defaults

## Decision

Security fixes applied immediately (5 fixes, 5 new tests). Clean code findings tracked as refactoring tasks in `11-tasks.md` — not bugs, not blocking features. The factory-closure pattern in `create_server()` is a deliberate architectural choice that trades file length for shared-state simplicity.

## Verification

298 tests passed, 90% coverage, mypy --strict clean, ruff clean.
