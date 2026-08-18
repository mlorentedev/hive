---
id: lesson-024-asyncio-timeout-cannot-interrupt-threads-use-
type: lesson
status: active
created: "2026-03-13"
owner: manu
tags: [hive, lesson, python, asyncio, concurrency, mcp]
---

# asyncio.timeout cannot interrupt threads — use lock timeouts for sync code

**Context:** Adding timeouts to MCP tool handlers to fix indefinite hangs (issue #63)
**Problem:** asyncio.timeout() only cancels at await points — it cannot interrupt a thread blocked on Lock.acquire() or subprocess.run(). Converting sync tools to async via to_thread gives false sense of control.
**Solution:** Use Lock.acquire(timeout=N) for sync blocking points, asyncio.timeout() for async handlers. Defense in depth: each layer has its own timeout mechanism matching its execution model.
**Tags:** `#python` `#asyncio` `#concurrency` `#mcp`
