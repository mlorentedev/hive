---
id: lesson-029-in-memory-mcp-tests-do-not-exercise-the-stdio
type: lesson
status: active
created: "2026-05-15"
owner: manu
tags: [hive, lesson, mcp, testing, stdio, cancellation, issue-75]
---

# In-memory MCP tests do not exercise the stdio transport race

**Context:** Debugging issue #75 (Hive transport dying after first rejected tool call). The in-memory FastMCP `call_tool` tests passed cleanly when I cancelled the task and made another call — looked like the server was fine.
**Problem:** That gave false confidence. The actual bug only reproduces when the cancellation goes through the JSON-RPC wire as a `notifications/cancelled` message AND the server runs as a real subprocess with `mcp.server.stdio`. The race is in `RequestResponder.__exit__`'s interaction with `anyio.CancelScope`, which the in-memory path never touches.
**Solution:** For any future MCP transport-level bug, write the regression test as a subprocess driving real JSON-RPC. `tests/test_transport_recovery.py` spawns `python -m hive.server` and sends `initialize` → `tools/call` → `notifications/cancelled` → `tools/call` to verify the second call still responds. Pairs in-memory tests for the API surface with subprocess tests for the transport.
**Why:** FastMCP's `call_tool` shortcuts the full message dispatch and never instantiates `RequestResponder` with the cancel-scope contract. The receive loop's task group is where the bug lives, not in the handler. Two distinct surfaces need two distinct test strategies.
**Tags:** `#mcp` `#testing` `#stdio` `#cancellation` `#issue-75`
