---
id: lesson-014-subprocess-and-i-o-resilience-audit-4-crash-v
type: lesson
status: active
created: "2026-03-06"
owner: manu
tags: [hive, lesson, resilience, subprocess, httpx, mcp-stability]
---

# Subprocess and I/O resilience audit — 4 crash vectors found

**Context:** User reported `vault_patch` crashing MCP server on Windows. Expanded into full audit of all subprocess and I/O paths.
**Problem:** Four categories of unhandled exceptions could crash the server: (1) `_git_commit` missing catch-all, (2) `_git_log`/`_git_recent` only catching `TimeoutExpired`, (3) `httpx.ReadTimeout` not caught in HTTP clients, (4) file I/O in write tools with no `except OSError`.
**Solution:** (1) Catch-all `except Exception` in all git helpers + timeout 10→30s. (2) Same for `_git_log`/`_git_recent`. (3) Added `except httpx.TimeoutException` in `OllamaClient` and `OpenRouterClient`. (4) Wrapped `read_text`/`write_text` in write tools with `except OSError`. 10 new tests, 290 total.
**Tags:** `#resilience` `#subprocess` `#httpx` `#mcp-stability`
