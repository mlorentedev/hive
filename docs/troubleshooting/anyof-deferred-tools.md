---
id: anyof-deferred-tools
type: troubleshooting
status: active
created: "2026-03-06"
owner: manu
---

# anyOf in JSON Schema drops MCP tools from Claude Code

## Summary

Claude Code's deferred tool indexer silently drops MCP tools whose JSON Schema contains `anyOf` constructs. This means tools with `Optional` / `| None` parameter types become invisible — they cannot be loaded via `ToolSearch` and are unusable in a session.

## Root Cause

FastMCP (via Pydantic) generates `anyOf` for Python union types:

```python
# This generates anyOf — tool becomes invisible to Claude Code
def my_tool(tags: list[str] | None = None) -> str: ...

# This generates clean schema — tool is visible
def my_tool(tags: list[str] = []) -> str: ...
```

Schema comparison:

| Python type | JSON Schema | Claude Code |
|---|---|---|
| `str \| None = None` | `{"anyOf": [{"type": "string"}, {"type": "null"}]}` | Dropped |
| `str = ""` | `{"type": "string", "default": ""}` | Works |
| `list[str] \| None = None` | `{"anyOf": [{"type": "array"}, {"type": "null"}]}` | Dropped |
| `list[str] = []` | `{"type": "array", "default": []}` | Works |

## Impact

3 of 17 Hive tools were invisible in Claude Code sessions:
- `vault_patch` — the most critical write tool
- `capture_lesson` — inline lesson extraction
- `vault_list_files` — directory navigation (collateral — simple schema but contiguous with affected tools)

## Fix (2026-03-06)

Replaced all `| None = None` with empty defaults:

| Parameter | Before | After |
|---|---|---|
| `vault_patch.old_text` | `str \| None = None` | `str = ""` |
| `vault_patch.new_text` | `str \| None = None` | `str = ""` |
| `vault_patch.patches` | `list[dict[str, str]] \| None = None` | `list[dict[str, str]] = []` |
| `capture_lesson.tags` | `list[str] \| None = None` | `list[str] = []` |

Mutable default (`= []`) is safe here because FastMCP wraps parameters in Pydantic models (new instance per call). Ruff B006 suppressed with `# noqa: B006`.

Validation logic updated accordingly:
- `patches is not None` → `len(patches) > 0`
- `old_text is None` → `not old_text`
- `tags or []` → `tags`

## Verification

After fix, all 17 tools generate anyOf-free schemas:
```
$ uv run python -c "..." # schema inspection script
Total tools: 17
anyOf present: 0
```

Requires new Claude Code session to verify deferred tool registration (tool list is set at session start).

## Design Rule

**Never use `| None` in MCP tool parameters.** Use empty defaults instead:
- `str` → `str = ""`
- `list[T]` → `list[T] = []`  (with `# noqa: B006`)
- `int` → `int = 0` or `int = -1` (sentinel)
- `bool` → `bool = False`

This is a Claude Code client limitation, not an MCP protocol issue. Other clients may handle `anyOf` fine.
