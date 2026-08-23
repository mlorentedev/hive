---
id: lesson-063-mcp-tool-contract-gaps-read-as-server-flakine
type: lesson
status: active
created: "2026-06-04"
owner: manu
tags: [hive, lesson, mcp, dx, tool-contract, observability, llm-ergonomics, HIVE-202]
---

# MCP tool-contract gaps read as server "flakiness" — mine the rejection logs to find them

**Context:** A Hermes-agent beta test against Hive v1.32.3 (issue #202) surfaced four gaps where the documented/intuitive tool surface disagreed with the implementation: `vault_search` rejected `limit`, `vault_write` demanded an explicit create flag, the vault-not-found error never named `HIVE_VAULT_PATH`, and there was no `vault_delete`. Each was fixed as an atomic PR (#209/#213/#215/#217 → v1.34.0–v1.36.0).
**Problem:** When a client (Claude or another agent) calls a tool with the parameter name it *expects* and gets `unexpected_keyword_argument` / "Section is required", the LLM retries, sometimes silently degrades, and from the operator's seat the server *looks* flaky — even though every individual call path is healthy. The failure is in the contract, not the code, so it never shows up as an exception or a failing test. The earlier observation "75 'Invalid arguments' tool rejections traced to three wrong-param-name patterns" (#20830) was the same class, found only by reading `~/.local/share/hive/hive-*.log`.
**Solution:** Treat the server's own rejection logs as a *product signal*, not noise. Periodically grep the per-PID logs for `Invalid arguments` / `unexpected_keyword_argument` / validation rejections and cluster by tool+param; each recurring cluster is a contract gap. Close it the [[#2026-03-15 MCP tool parameter names must match LLM mental models]] way — accept the LLM-guessed name as an `int = 0` / `str = ""` alias resolved at the top of the handler (never `T | None`, keeps the schema `anyOf`-free), or relax the precondition (infer create mode) rather than forcing the client to know an internal flag. Decompose multi-gap findings into one-logical-change PRs under the repo's ~300-LOC limit.
**Why:** Tool-call rejections are invisible to standard observability (no stack trace, no 5xx) but directly erode the operator's trust in the server. The cheapest detector already exists — the logs the server writes on every rejection. Mining them turns "feels flaky" into a concrete, fixable backlog. This is the doctrine behind both HIVE-119 (alias) and HIVE-202 (alias + behaviour relaxation).
**Tags:** `#mcp` `#dx` `#tool-contract` `#observability` `#llm-ergonomics` `#HIVE-202`
