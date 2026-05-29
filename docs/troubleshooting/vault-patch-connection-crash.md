---
id: "hive-issue-vault-patch-crash"
type: troubleshooting
status: resolved
severity: high
tags: [bug, vault_patch, mcp-stability, windows, git-commit]
created: "2026-03-06"
resolved: "2026-03-06"
owner: manu
---

# vault_patch: MCP server crashes during write operations

## Summary

`vault_patch` intermittently crashes the Hive MCP server process. Data is written to disk correctly, but the MCP response never reaches Claude Code. The server dies shortly after, causing all subsequent MCP calls to fail with `Connection closed`.

Read-only tools (`vault_query`, `vault_health`, `vault_usage`, `vault_search`) work consistently and respond in <2s.

## Environment

- **OS:** Windows 11 Pro 10.0.26100
- **Hardware:** AMD Ryzen 7 5825U, 16GB shared RAM (no dedicated GPU)
- **Hive version:** v1.4.2 (via `uvx hive-vault`)
- **Python:** 3.12
- **Claude Code client:** Opus 4.6

## Reproduction Steps

1. Start a Claude Code session with Hive MCP configured
2. Use `vault_query` (works fine, confirms server is alive)
3. Use `vault_patch` with one or more replacements
4. Observe: Claude Code reports "The user doesn't want to proceed with this tool use. The tool use was rejected"
5. Check the file on disk: patches ARE applied
6. Try any subsequent MCP call: `MCP error -32000: Connection closed`

## Observed Behavior

### Case 1: Multi-patch call (3 replacements)
- Claude Code reported: "rejected by user"
- All 3 patches were applied to disk (verified via `vault_query` after restarting server)
- Server continued responding after this call (did not crash immediately)

### Case 2: Single patch call
- Claude Code reported: "rejected by user"
- Patch was applied to disk
- Immediately after, `vault_query` returned: `MCP error -32000: Connection closed`
- Server process was dead — all subsequent calls failed

### Case 3: Second single patch call (after Case 2 server restarted)
- Same behavior: reported as rejected, data written, server crashed

## Analysis

### What vault_patch does internally
1. Read file from disk
2. Find `old_text` in content
3. Replace with `new_text`
4. Write file to disk
5. Call `_git_commit()` — runs `subprocess.run(["git", "commit", ...], timeout=10)`
6. Return MCP response

### Root Cause Hypothesis

The server crashes **after file write but before/during MCP response serialization**. Evidence:

| Observation | Implication |
|-------------|-------------|
| Data is written to disk | Steps 1-4 complete successfully |
| MCP response never arrives | Step 5 or 6 fails |
| Read-only tools never crash | No git commit, no file write in read path |
| Server process dies | Unhandled exception or process termination |

Most likely culprit: `_git_commit()` subprocess interaction on Windows.

Possible failure modes in `_git_commit()`:
- `subprocess.run(timeout=10)` may be too tight on Windows with antivirus/indexing overhead
- Git hook execution (if any pre-commit hooks exist in the vault repo)
- stdout/stderr pipe buffer overflow if git produces unexpected output
- FastMCP stdio transport may interpret subprocess stdout as MCP protocol data (stdout conflict)

### Why Claude Code shows "rejected by user"

Claude Code receives no MCP response (connection drops). It interprets this as user rejection. This is misleading — the user did not reject anything.

## Proposed Fixes (prioritized)

### P0: Response-first pattern
Send the MCP success response **before** running `_git_commit()`. The git commit is a side effect, not part of the user-facing result.

```python
# Current (risky):
_write_file(path, content)
_git_commit(message)       # <-- can crash here, response never sent
return {"result": "ok"}

# Proposed (safe):
_write_file(path, content)
result = {"result": "ok"}
try:
    _git_commit(message)
except Exception:
    log.warning("git commit failed, data saved but not committed")
return result
```

### P1: Async/background git commit
Move `_git_commit()` to a background thread or asyncio task so it cannot block the response.

### P2: Increase subprocess timeout on Windows
Change `timeout=10` to `timeout=30` for git operations. Windows disk I/O is slower, especially with:
- Windows Defender real-time scanning
- Windows Search indexer
- NTFS journaling overhead

### P3: Investigate stdout conflict
Check if `subprocess.run()` in `_git_commit()` captures stdout/stderr properly. If git writes to stdout and FastMCP uses stdio transport, the output could corrupt the MCP protocol stream.

```python
# Ensure git output doesn't leak to stdout
subprocess.run(
    ["git", "commit", "-m", message],
    capture_output=True,  # critical for stdio MCP servers
    timeout=30,
)
```

### P4: Add keep-alive / heartbeat
Check if FastMCP has connection timeout settings. A long-running `_git_commit()` may exceed idle timeout.

## Resolution (2026-03-06)

Fixed in branch `chore/cleanup`. Full audit of all subprocess and I/O paths.

### Fix 1: `_git_commit()` catch-all (original bug)
- Timeout: 10s → 30s (Windows margin)
- Added catch-all `except Exception` — git commit is best-effort, never propagates
- **Effect:** Write tools always return a response, even if git fails

### Fix 2: `_git_log()` / `_git_recent()` catch-all
- Same pattern: only caught `TimeoutExpired`, missed `OSError` (git not in PATH)
- Changed to catch-all `except Exception`, return empty on failure
- **Effect:** `session_briefing` and `vault_recent` survive missing git binary

### Fix 3: `httpx.ReadTimeout` in `clients.py`
- Clients only caught `ConnectError` and `ConnectTimeout`
- `ReadTimeout` (LLM inference too slow) was uncaught → would crash `delegate_task`
- Added `except httpx.TimeoutException` → converted to `ConnectionError`
- Applied to: `OllamaClient.generate`, `OpenRouterClient.generate`, `OpenRouterClient.list_models`, `OllamaClient.is_available`
- **Effect:** Slow inference returns error message instead of crashing server

### Fix 4: File I/O protection in write tools
- `vault_update`, `vault_create`, `vault_patch`, `capture_lesson`: `read_text()`/`write_text()` had no try/except
- Added `except OSError` → returns error message to client
- **Effect:** Permission errors or disk-full return clean MCP error, no crash

### Tests added
- `TestGitCommitResilience`: 3 tests (OSError, RuntimeError, post-failure recovery)
- `TestGitReadResilience`: 2 tests (session_briefing, vault_recent survive OSError)
- `TestFileIOResilience`: 2 tests (permission errors on read/write)
- `TestReadTimeoutResilience`: 3 tests (Ollama generate, OpenRouter generate, Ollama is_available)

### Verification
- 290 tests passed (was 265), 0 failures
- mypy --strict clean, ruff clean
- 90% coverage

## Workaround (no longer needed)

Use Claude Code's `Edit` tool to directly modify vault `.md` files. Then manually commit in the vault repo:

```bash
cd ~/Projects/knowledge && git add -A && git commit -m "vault update"
```

## Related

- `_git_commit()` implementation in `server.py`
- Security audit (2026-03-05) added `try/except CalledProcessError` to `_git_commit` — but did not cover all failure modes
- `subprocess.run(timeout=10)` added in same audit — insufficient for Windows
