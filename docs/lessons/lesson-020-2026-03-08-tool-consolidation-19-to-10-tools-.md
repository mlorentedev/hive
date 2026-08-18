---
id: lesson-020-2026-03-08-tool-consolidation-19-to-10-tools-
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-08: Tool consolidation — 19 to 10 tools for Claude Code compatibility

- **Context:** Claude Code silently drops MCP tools beyond ~14 from its deferred tool list. Hive had 19 tools, 5 were invisible.
- **Problem:** Users could never discover or use `vault_patch`, `vault_list_files`, `extract_lessons`, `vault_validate`, or `vault_usage` because Claude Code's client-side limit hid them from the tool picker.
- **Solution:** Consolidated 19 tools into 10 by merging related functionality behind mode-switching parameters (e.g., `vault_search` gained `ranked=True` and `since_days=N` instead of separate `vault_smart_search` and `vault_recent` tools). No functionality lost — every feature accessible via the consolidated API.
- **Lesson:** MCP client implementations have undocumented limits. Design tool surfaces to stay under ~12 tools per server. Prefer fewer tools with mode parameters over many single-purpose tools.
