---
title: Getting Started
description: Install and configure Hive in under a minute.
---

## Prerequisites

- An MCP-compatible client — [Claude Code](https://docs.anthropic.com/en/docs/build-with-claude/claude-code/overview), [Codex CLI](https://github.com/openai/codex), [Cursor](https://cursor.com/), [Windsurf](https://windsurf.com/), or any other MCP host
- An [Obsidian](https://obsidian.md/) vault (or any directory of Markdown files) — see [Vault Structure](/hive/guides/vault-structure/) for the expected layout
- **Optional for worker delegation:**
  - [Ollama](https://ollama.com/download) running locally — pull a model with `ollama pull qwen2.5-coder:7b`
  - [OpenRouter](https://openrouter.ai/) account — create an API key at [openrouter.ai/keys](https://openrouter.ai/keys) (free tier available)

## Install

Register Hive as an MCP server. Example with Claude Code:

```bash
claude mcp add hive -- uvx hive-vault
```

`uvx` handles the Python environment automatically. No cloning, no venv.

## First Query

Once registered, your AI assistant can use Hive tools. Try asking:

> "Use vault_query to load context for my-project"

The assistant will call:

```python
vault_query(project="my-project", section="context")
```

And get back the contents of `~/Projects/knowledge/10_projects/my-project/00-context.md`.

## Configure Vault Path

By default, Hive looks for your vault at `~/Projects/knowledge`. To change it:

```bash
claude mcp add hive -e VAULT_PATH=/path/to/your/vault -- uvx hive-vault
```

## Configure Worker (Optional)

To enable task delegation to cheaper models:

```bash
claude mcp add hive \
  -e VAULT_PATH=/path/to/your/vault \
  -e HIVE_OLLAMA_ENDPOINT=http://your-ollama:11434 \
  -e OPENROUTER_API_KEY=sk-or-... \
  -- uvx hive-vault
```

See [Configuration](/hive/configuration/) for all environment variables.

## Verify

Run a health check:

> "Use vault_health to check my vault"

You should see project counts, file counts, and staleness metrics for each project in your vault.

## Next Steps

- [Use Cases](/hive/guides/use-cases/) — real-world workflows with Hive
- [Configuration](/hive/configuration/) — full environment variable reference
- [Vault Tools](/hive/tools/vault/) — all 11 vault tools
- [Worker Tools](/hive/tools/worker/) — task delegation and routing
- [Vault Structure](/hive/guides/vault-structure/) — how to organize your vault
