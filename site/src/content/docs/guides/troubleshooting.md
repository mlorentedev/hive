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

## Getting Help

If your issue isn't listed here:

1. Run `vault_health` to check vault connectivity and file counts
2. Run `worker_status` to check provider connectivity and budget
3. Check `~/.local/share/hive/hive.log` for error details
4. Check the [Configuration](/hive/configuration/) page for all environment variables
5. Open an issue at [github.com/mlorentedev/hive](https://github.com/mlorentedev/hive/issues)
