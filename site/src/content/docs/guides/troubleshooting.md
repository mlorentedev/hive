---
title: Troubleshooting
description: Common issues and fixes for Hive MCP server.
---

## Vault Not Found

**Symptom:** Vault tools (vault_list, vault_query, etc.) return "Vault not found at /home/user/Projects/knowledge" with setup instructions.

**Cause:** The vault path was not configured when registering the MCP server. Hive defaults to `~/Projects/knowledge`, which may not exist on your machine.

**Fix:** Re-register the MCP server with `VAULT_PATH` pointing to your Obsidian vault:

```bash
# Claude Code
claude mcp add -s user hive -e VAULT_PATH=$HOME/my-vault -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user -e VAULT_PATH=$HOME/my-vault hive-vault uvx -- --upgrade hive-vault
```

Both `VAULT_PATH` and `HIVE_VAULT_PATH` are accepted. If both are set, `HIVE_VAULT_PATH` takes precedence.

**Note:** The server starts even without a valid vault path — worker tools (`worker_status`, `delegate_task`) still work. Only vault-specific tools require a valid path.

## Hive Not Available in Other Projects

**Symptom:** You installed Hive in one project but it doesn't appear when starting a session in a different project.

**Cause:** You registered Hive at **project scope** (default) instead of **user scope**. Project-scoped MCP servers only work in the project directory where they were registered.

**Fix:** Re-register at user scope — this is the recommended setup since Hive's vault is shared across all projects:

```bash
# Claude Code — note the -s user flag
claude mcp add -s user hive -- uvx --upgrade hive-vault

# Gemini CLI — already user scope by default with -s user
gemini mcp add -s user hive-vault uvx -- --upgrade hive-vault
```

After re-registering, restart your AI assistant session. Hive will now appear in every project.

**Why user scope matters:** Hive connects to a single knowledge vault that stores context for *all* your projects. Project-scope registration defeats this — you'd need to register Hive separately in every project, and they'd all point to the same vault anyway.

## Ollama Shows "offline" in worker_status

**Symptom:** `worker_status` reports Ollama as offline, but `curl http://your-ollama:11434/api/tags` works.

**Cause:** The `HIVE_OLLAMA_ENDPOINT` environment variable is not set in your MCP server registration.

**Fix:** Re-register the MCP server with the endpoint explicitly set:

```bash
# Claude Code
claude mcp add -s user hive \
  -e HIVE_OLLAMA_ENDPOINT=http://your-ollama:11434 \
  -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user \
  -e HIVE_OLLAMA_ENDPOINT=http://your-ollama:11434 \
  hive-vault uvx -- --upgrade hive-vault
```

MCP servers do **not** inherit your shell's environment variables. Every env var must be passed explicitly at registration time.

## OpenRouter Returns 429 (Rate Limit)

**Symptom:** `delegate_task` fails with a rate limit error on the free tier.

**Cause:** OpenRouter free tier models have per-minute rate limits. This is normal under heavy usage.

**Fix:** Wait 60 seconds and retry. For sustained workloads, set `max_cost_per_request=0.01` to use the paid tier (capped by `HIVE_OPENROUTER_BUDGET`).

## Changes to MCP Config Don't Take Effect

**Symptom:** You updated an environment variable (e.g., `VAULT_PATH`) but Hive still uses the old value.

**Cause:** MCP servers are loaded at session start. Config changes require a new session.

**Fix:** Exit and restart your AI assistant session (e.g., restart Claude Code, start a new Gemini CLI session).

## vault_list Returns Empty

**Symptom:** `vault_list` shows no projects.

**Cause:** Either `VAULT_PATH` doesn't point to the right directory, or your vault layout doesn't match the configured scopes.

**Fix:**
1. Verify your vault path: `ls $VAULT_PATH`
2. Check that project directories exist under the expected scope directory (default: `10_projects/`)
3. If your vault uses a different layout, configure `HIVE_VAULT_SCOPES`:

```bash
HIVE_VAULT_SCOPES='{"projects": "Projects", "meta": "Templates"}'
```

See [Vault Structure](/hive/guides/vault-structure/) for layout details.

## "Project not found" Errors

**Symptom:** `vault_query(project="my-app")` returns "Project not found" but the directory exists.

**Possible causes:**
- The project directory is not inside a configured scope directory
- Typo in the project name (it must match the directory name exactly)
- The scope directory itself doesn't exist

**Fix:** Run `vault_list` to see what Hive can find. If your project isn't listed, check your `HIVE_VAULT_SCOPES` configuration.

## Gemini CLI: MCP Registration Syntax

**Symptom:** `gemini mcp add` fails with argument parsing errors.

**Cause:** Gemini CLI has specific argument ordering requirements. The `--` separator is needed to prevent Gemini from consuming the server's arguments.

**Correct syntax:**

```bash
# Basic registration
gemini mcp add -s user hive-vault uvx -- --upgrade hive-vault

# With environment variables
gemini mcp add -s user \
  -e VAULT_PATH=$HOME/my-vault \
  hive-vault uvx -- --upgrade hive-vault
```

**Key details:**
- Server name comes before the command (`hive-vault uvx`)
- `--` separates Gemini flags from server arguments
- `-s user` installs at user scope (persists across projects)
- Environment variable values are expanded immediately (not stored as references)

## vault_write Rejects My Content

**Symptom:** `vault_write` with `operation="replace"` returns a validation error.

**Cause:** When replacing an entire file, Hive validates that YAML frontmatter includes required fields: `id`, `type`, and `status`.

**Fix:** Include valid frontmatter in your content:

```markdown
---
id: my-doc
type: context
status: active
---

Your content here.
```

Or use `operation="append"` to add content without replacing frontmatter.

## Database Files Growing Large

**Symptom:** SQLite files at `~/.local/share/hive/` are growing.

**Expected sizes:**
- `worker.db` — Budget/usage tracking. Grows ~1KB per delegate_task call. Typical: 10-50KB.
- `relevance.db` — Adaptive context scoring. Grows ~0.5KB per session_briefing call. Typical: 5-20KB.

Both use WAL mode for performance. If sizes seem excessive, you can safely delete them — Hive recreates them automatically. Budget tracking resets on deletion.

## Checking the Debug Log

Hive writes warnings and errors to a persistent log file for post-mortem debugging:

```
~/.local/share/hive/hive.log
```

Check this file when tools return unexpected results or the server fails silently. The log rotates at 1MB with one backup file (`hive.log.1`).

To change the log location:

```bash
claude mcp add -s user hive \
  -e HIVE_LOG_PATH=/path/to/custom.log \
  -- uvx --upgrade hive-vault
```

## Tool Calls Hanging or Timing Out

**Symptom:** MCP tool calls freeze with no response, or return "Tool timed out after 60s".

**Cause:** Worker tools (`capture_lesson`, `delegate_task`) call external APIs (Ollama/OpenRouter) that may be slow or unresponsive. Write tools (`vault_write`, `vault_patch`) may stall if another operation holds the write lock.

**Fix:**
1. Check worker connectivity: `worker_status` — if Ollama is offline, fix the connection first
2. If timeouts are too aggressive, increase them:
   - `HIVE_TOOL_TIMEOUT=120` — per-tool timeout for async worker tools (default: 60s)
   - `HIVE_HTTP_TIMEOUT=120` — HTTP timeout for Ollama/OpenRouter calls (default: 60s)
3. Check `~/.local/share/hive/hive.log` for timeout warnings with tool names and elapsed times
4. If write tools return "Server busy", retry shortly — a previous write operation is finishing

**Note:** Timeouts are a safety net. A timed-out tool returns a clear error message instead of hanging your session indefinitely. The underlying operation (HTTP call, git commit) is cleaned up automatically.

## MCP Transport Disconnect After Rejecting the First Tool Call

**Symptom:** In Claude Code (and likely other MCP hosts), rejecting the very first `mcp__hive__*` permission prompt poisons the transport for the rest of the conversation. Subsequent calls to any Hive tool return `MCP error -32000: Connection closed`, then `No such tool available`. Restarting the conversation recovers, and `claude mcp list` still reports the server as connected at the process level.

**Cause:** A race condition in the upstream `mcp` Python SDK around `mcp.shared.session.RequestResponder`. When the client sends `notifications/cancelled` for an in-flight request, two failure modes can fire:

1. The responder's anyio `CancelScope` re-raises a `CancelledError` after the cancellation response has already been sent. That spurious exception propagates to the server's receive loop `task_group` and kills it — the process stays alive but stops reading stdin.
2. The handler finishes *after* the client has already sent the cancellation, and the late call into `RequestResponder.respond()` fails with `AssertionError("Request already responded to")`. The exception escapes the receive loop and kills the same `task_group`.

**Fix:** Hive applies two targeted monkey-patches at startup in [`src/hive/_compat.py`](https://github.com/mlorentedev/hive/blob/master/src/hive/_compat.py):

- `RequestResponder.__exit__` — swallows the spurious `CancelledError` once the responder is marked completed.
- `RequestResponder.respond` — short-circuits the late call with a WARNING log line (`mcp.ghost_response.suppressed_after_cancel_ack`) and bumps a counter exposed in `vault_health`.

Both patches are self-gated to the exact failure mode (`_completed=True`), so they remain inert once upstream fixes the bug.

Tracked in [issue #75](https://github.com/mlorentedev/hive/issues/75) and [upstream python-sdk#2610](https://github.com/modelcontextprotocol/python-sdk/issues/2610). Regression tests: [`tests/test_transport_recovery.py`](https://github.com/mlorentedev/hive/blob/master/tests/test_transport_recovery.py) + [`tests/test_compat_shim.py`](https://github.com/mlorentedev/hive/blob/master/tests/test_compat_shim.py).

**If you still see the disconnect:**

1. Confirm you are on `hive-vault >= 1.14.0` — earlier versions did not ship the second (respond-after-cancel) patch.
2. Check `~/.local/share/hive/hive.log` for `mcp.ghost_response.suppressed_after_cancel_ack` WARNING lines (always logged) or `Swallowed spurious cancellation on completed responder` debug lines (set `HIVE_LOG_LEVEL=DEBUG` to enable).
3. As a workaround, always accept the first Hive tool call in a fresh conversation. Later rejections do not break the transport.

## Cancelled a Tool Call but the Vault Changed Anyway

**Symptom:** You (or your client) cancelled a `vault_write` / `vault_patch` / `capture_lesson` call mid-flight — got an `ErrorData` response back saying *"Request cancelled"* — but on the next `vault_query` the file shows the new content as if the operation had succeeded.

**Cause:** This is not a bug, it is a documented semantic mismatch ([ADR-007](https://github.com/mlorentedev/hive/blob/master/docs/architecture/adr-007-mcp-cancellation-response.md), amended twice). When the cancel arrives, the upstream `RequestResponder.cancel()` writes the `ErrorData` frame to the wire immediately — empirically 20/20 times on Linux, see [`tests/test_compat_shim.py::test_classify_cancellation_race`](https://github.com/mlorentedev/hive/blob/master/tests/test_compat_shim.py). But the underlying handler thread keeps running to completion; Hive cannot safely interrupt a partial write (Python `asyncio.timeout` cancels the awaiting coroutine but cannot interrupt the blocking thread). So the disk is mutated **after** the cancel ack reaches the client.

**The ErrorData ack does NOT imply rollback.** The client correctness rule is: *verify state via `vault_query` instead of retrying.* Retrying a `vault_write(operation="append", ...)` after a ghost-response duplicates content; retrying `vault_patch` may produce ambiguous-match errors against the already-applied result.

**How to detect this in your sessions:** call `vault_health` — when ghost responses have occurred, the output includes a block like:

```
## ghost_responses
- total: 3
- last_seen: 2026-05-20T22:14:07+00:00
- last_tool: vault_patch
- note: ErrorData ack does NOT imply rollback — verify state via `vault_query`, do not retry.
```

The counter resets when the server restarts. Each event is also logged at WARNING level with the prefix `mcp.ghost_response.suppressed_after_cancel_ack` plus the tool name and request id.

**Mitigations:**

- Increase `HIVE_TOOL_TIMEOUT` (default 60s) so slow worker calls finish before the client cancels.
- For batch writes, prefer `vault_write(commit=False)` + `vault_commit` — the per-write cost drops from ~150ms to ~5–15ms, shrinking the cancellation window.
- After any cancellation: `vault_query(project=..., path=...)` the affected file to inspect the actual disk state before issuing another write.

Available from `hive-vault >= 1.14.0`. The empirical wire-behavior classifier ran 20 iterations on Linux and confirmed scenario (a) — ErrorData wins the race — in 20/20 cases; see ADR-007 Amendment #2 for the full retraction of the earlier "raw send" plan.

## Multi-Session Contention (3-5 concurrent Claude Code sessions)

**Baseline note:** Hive is commonly used with 3-5 Claude Code sessions open in parallel against the same vault. This is the daily-usage baseline, not edge case. The MCP stdio model spawns one `hive-vault` subprocess per session, so 4 windows = 4 sibling processes sharing the same SQLite DBs (`~/.local/share/hive/*.db`) and the same vault git repo.

If those processes accumulate, or if you also run [obsidian-git](https://github.com/Vinzent03/obsidian-git) for auto-backups, three symptoms can appear:

- **WAL file bloat.** `~/.local/share/hive/relevance.db-wal` grows 10-100× the size of the steady-state DB because concurrent readers prevent SQLite from checkpointing the WAL.
- **Silent freezes during writes.** A `vault_write` or `capture_lesson` takes 10-30 seconds when obsidian-git is auto-committing in the background (its 10-minute interval holds `.git/index.lock` during pull + commit + push).
- **Zombie hive processes.** A Claude Code window crashed or was force-quit, but its `uvx hive-vault` child stayed alive holding file handles open.

### Inspect the current state

Run `vault_health(include_runtime=True)` from any session. The `## runtime` block reports:

```yaml
- wal_size_bytes: 4137984          # >5 MB sustained = contention
- competing_pid_count: 3            # other hive-vault PIDs (same user)
- last_git_lock_wait_ms:
  - mean: 12.5
  - p99: 8234.0                     # p99 > 5000ms = contention
  - samples: 47
- obsidian_git_present: true        # external committer detected
```

Healthy: `wal_size_bytes` under a few MB, `last_git_lock_wait_ms.p99` under 100ms.

### Spot a zombie hive process

**POSIX (Linux, macOS)** — list all hive processes and their age:

```bash
ps -eo pid,etime,cmd | grep hive-vault | grep -v grep
```

Inspect which file handles a specific PID holds:

```bash
lsof -p <PID> | grep hive
```

Kill a zombie:

```bash
kill <PID>          # graceful
kill -9 <PID>       # force
```

**Windows (PowerShell)** — list hive processes:

```powershell
Get-Process | Where-Object { $_.ProcessName -match "hive-vault|python" } |
  Select-Object Id, StartTime, ProcessName, Path
```

Kill by PID:

```powershell
Stop-Process -Id <PID>           # graceful
Stop-Process -Id <PID> -Force    # force
```

### Tune `HIVE_LOCK_TIMEOUT_S`

Default `30` seconds. The lock-acquire timeout used when hive contends for the git filelock. Capped at 600 to prevent foot-guns.

| Scenario | Recommended | Why |
|---|---|---|
| Default | `30` | Matches subprocess timeout; absorbs typical obsidian-git ticks |
| Large vault + slow disk | `60-90` | obsidian-git's pull + commit + push can hold the lock 15-30s on a 50 MB vault |
| Slow network + autoPull=true | `90-120` | Network pull dominates; raise to avoid abandons |
| Fast vault, fail-fast preference | `10` | If you'd rather see errors than wait |

Set via `HIVE_LOCK_TIMEOUT_S=60` env var.

### Tune `HIVE_WAL_CHECKPOINT_INTERVAL_S`

Default `30.0` seconds. How often each hive process runs `PRAGMA wal_checkpoint(PASSIVE)` to drain its SQLite WAL. Lower = more aggressive draining; higher = less CPU spent on idle ticks.

Default is appropriate for most users. Raise to `120` if you observe excessive CPU on idle hive processes (e.g., on resource-constrained machines); the trade-off is slower WAL drain.

### obsidian-git cooperation pattern

If your vault uses obsidian-git for auto-backups, the two tools cooperate cleanly when configured properly:

- Set obsidian-git's `autoSaveInterval` to 5-10 minutes (default is fine).
- For write-heavy flows, prefer `vault_write(commit=False)` / `vault_patch(commit=False)`. Hive writes the file; obsidian-git commits on its next tick. Per-write cost drops from ~150ms to ~5-15ms.
- Use `vault_commit` only when you need an explicit flush before obsidian-git's next tick (e.g., before closing Obsidian).
- Watch `vault_health.runtime.last_git_lock_wait_ms.p99`. If it stays above 5000ms, raise `HIVE_LOCK_TIMEOUT_S` to absorb the longer windows.

A future release will make the cooperation automatic (auto-defer when external committer healthy); for now it is opt-in via `commit=False`.

## Getting Help

If your issue isn't listed here:

1. Run `vault_health` to check vault connectivity and file counts — the `## server` identity block at the top reports the running version, python, vault path, and backend presence so you can include them verbatim in a bug report (no API keys are exposed). Add `include_runtime=True` for uptime, registered tool names, multi-session contention metrics, and the OpenRouter budget snapshot.
2. Run `worker_status` to check provider connectivity and budget
3. Check `~/.local/share/hive/hive.log` for error details
4. Check the [Configuration](/hive/configuration/) page for all environment variables
5. Open an issue at [github.com/mlorentedev/hive](https://github.com/mlorentedev/hive/issues)
