---
id: lesson-003-2026-03-01-python-over-go-for-mcp-servers
type: lesson
status: active
created: "2026-05-01"
owner: manu
tags: [hive, lesson]
---

# 2026-03-01: Python over Go for MCP servers

- **Context:** User prefers Go for performance. Evaluated both options.
- **Decision:** Python (FastMCP SDK).
- **Rationale:** MCP servers are I/O bound (file reads, API calls). Go's performance advantage is ~1ms per request on workloads that take seconds. FastMCP SDK is mature, reduces boilerplate to ~400 lines. Can rewrite in Go later (MCP is protocol-based).
