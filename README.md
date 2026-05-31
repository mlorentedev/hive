# hive-vault

[![CI](https://github.com/mlorentedev/hive/actions/workflows/ci.yml/badge.svg)](https://github.com/mlorentedev/hive/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/mlorentedev/hive/graph/badge.svg)](https://codecov.io/gh/mlorentedev/hive)
[![PyPI](https://img.shields.io/pypi/v/hive-vault)](https://pypi.org/project/hive-vault/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![Docs](https://img.shields.io/badge/docs-hive-blue)](https://mlorentedev.github.io/hive/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<a href="https://glama.ai/mcp/servers/mlorentedev/hive">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/mlorentedev/hive/badge" />
</a>

<!-- mcp-name: io.github.mlorentedev/hive-vault -->

**Your AI coding assistant forgets everything between sessions. Hive fixes that.**

Hive is an [MCP](https://modelcontextprotocol.io/) server that connects your AI assistant to an [Obsidian](https://obsidian.md/) vault. Instead of loading everything upfront, it queries only what's needed — on demand.

| Metric | Without Hive | With Hive |
|---|---|---|
| Context loaded per session | ~800 lines (static) | ~50 lines (on demand) |
| Token cost for context | 100% every session | 6% average per query |
| Knowledge retained between sessions | 0% | 100% (in vault) |

> Measured on a real vault with 19 projects, 200+ files. See [benchmarks](https://mlorentedev.github.io/hive/guides/benchmarks/).

## Quick Start

Hive runs without a vault — vault tools return a friendly error until `VAULT_PATH` is set, so you can install first and configure later.

```bash
# Minimal — uses default vault path ~/Projects/knowledge
claude mcp add -s user hive -- uvx --upgrade hive-vault

# With a custom vault path
claude mcp add -s user hive -e VAULT_PATH=$HOME/path/to/vault -- uvx --upgrade hive-vault

# Gemini CLI
gemini mcp add -s user -e VAULT_PATH=$HOME/path/to/vault hive-vault uvx -- --upgrade hive-vault
```

> Default vault path: `~/Projects/knowledge`. Override with `VAULT_PATH` (or `HIVE_VAULT_PATH`) as shown above.

For Codex CLI, GitHub Copilot, Cursor, Windsurf, and other clients, see [Getting Started](https://mlorentedev.github.io/hive/getting-started/).

Then ask your assistant: *"Use vault_list to see my vault"*

## Requirements

Hive degrades gracefully — every recommended or optional dependency reveals more capability without breaking the baseline.

- **Required**
  - Python **3.12+** (works on 3.13).
  - A directory of markdown files. The vault structure used by `00_meta` / `10_projects` / `50_work` / `80_agents` is optional — without it, vault tools still operate but the scope routing is flat.

- **Recommended**
  - `git` initialised inside the vault. Without it, `vault_write` / `vault_patch` still write to disk; they just skip the per-write commit (and `vault_commit` reports the working tree as untracked).
  - The **[Obsidian](https://obsidian.md)** desktop app to author the vault by hand.
  - The **[obsidian-git plugin](https://github.com/Vinzent03/obsidian-git)** with auto-commit set to **5–10 minutes**. Pair it with `vault_write(commit=False)` / `vault_patch(commit=False)` to push the git workload off the synchronous tool path; see *Recommended configuration* below.

- **Optional**
  - **[Ollama](https://ollama.com/)** running `qwen2.5-coder:7b` (or compatible) for local, free `delegate_task` / `capture_lesson` worker calls.
  - An **OpenRouter** API key (`OPENROUTER_API_KEY`) as a free-tier and paid fallback worker.
  - A **backup git remote** (e.g. private GitHub repo) so vault history survives a disk loss.

### Recommended configuration

Per [ADR-006 (commit policy)](docs/adr/adr-006-commit-policy.md), the recommended pairing for write-heavy flows is:

1. Install and enable the **obsidian-git** plugin in your vault.
2. Set its **auto-commit interval** to 5 or 10 minutes.
3. Call `vault_write(..., commit=False)` and `vault_patch(..., commit=False)` for all bulk operations.
4. Optionally call `vault_commit(message="...")` at the end of a session to force a checkpoint sooner than the obsidian-git tick.

`vault_health` reports a `## external_committer` block when it detects obsidian-git in the vault. The `commit=False` durability contract is explicit: files are persisted to disk regardless; only the *commit* is deferred. A crash before the next flush loses the commit, not the content.

When a tool call is cancelled mid-flight (slow worker, client timeout), the server may have already mutated the disk before the cancel ack reaches the wire. `vault_health` surfaces a `## ghost_responses` counter and emits a `mcp.ghost_response.suppressed_after_cancel_ack` WARNING for each event — verify state via `vault_query` rather than retrying, since the ErrorData ack does **not** imply rollback ([ADR-007](docs/adr/adr-007-mcp-cancellation-response.md)).

## Tools

| Tool | What it does |
|---|---|
| `vault_query` | Load project context, tasks, roadmap, lessons — or any file by path |
| `vault_search` | Full-text search with metadata filters, regex, ranked results, recent changes, lesson-usage ranking (`rank_by`) |
| `vault_list` | Browse projects and files with glob filtering |
| `vault_health` | Server identity (version, vault path, backends), health metrics, drift detection, usage stats, opt-in runtime block |
| `vault_write` | Create, append, or replace vault files. `commit=False` defers the git commit for batching |
| `vault_patch` | Surgical find-and-replace. `commit=False` defers the git commit for batching |
| `vault_commit` | Flush pending `commit=False` writes into one git commit |
| `capture_lesson` | Capture lessons inline / batch-extract from text / look up existing lessons by keyword (`find=`) |
| `session_briefing` | Tasks + lessons + git log + health in one call |
| `delegate_task` | Route tasks to cheaper models or summarize vault files |
| `worker_status` | Budget, connectivity, available models |

Plus 5 [resources](https://mlorentedev.github.io/hive/reference/resources/) and 4 [prompts](https://mlorentedev.github.io/hive/guides/prompts/) for guided workflows.

### Lesson reinforcement

Every read of a lesson via `vault_query`, `vault_search`, or `capture_lesson(find=…)` increments a counter and grows that lesson's confidence asymptotically toward 1.0. Validated lessons rank higher than one-shot captures over time.

```bash
# Surface the top-ranked lessons matching a keyword
capture_lesson(project="hive", find="multi-process")

# Search lessons ranked by usage signal (not BM25)
vault_search(query="timeout", rank_by="reinforcements")    # most-reinforced first
vault_search(query="timeout", rank_by="confidence")        # highest decayed confidence
vault_search(query="timeout", rank_by="hybrid")            # α=0.7 BM25 + 0.3 confidence
```

Storage: SQLite side-table at `HIVE_LESSON_DB_PATH` (default `~/.local/share/hive/lesson_reinforcement.db`). WAL mode + busy_timeout make it cross-process safe.

## Architecture

```
MCP Host (Claude Code, Gemini CLI, Codex CLI, Cursor, ...)
    └── hive-vault (MCP server, stdio)
            ├── Vault Tools (7) ── Obsidian vault (Markdown + YAML frontmatter)
            ├── Session Tools (1) ── Adaptive context assembly
            └── Worker Tools (2) ── Ollama (free) → OpenRouter free → paid ($1/mo cap) → reject
```

## Documentation

Full documentation at **[mlorentedev.github.io/hive](https://mlorentedev.github.io/hive/)**:

- [Getting Started](https://mlorentedev.github.io/hive/getting-started/) — install for all MCP clients
- [Configuration](https://mlorentedev.github.io/hive/configuration/) — all 19 environment variables
- [Vault Structure](https://mlorentedev.github.io/hive/guides/vault-structure/) — how to organize your vault
- [Use Cases](https://mlorentedev.github.io/hive/guides/use-cases/) — real-world workflows
- [Architecture](https://mlorentedev.github.io/hive/reference/architecture/) — module map and design decisions
- [Troubleshooting](https://mlorentedev.github.io/hive/guides/troubleshooting/) — common issues and fixes

Project-bound knowledge (docs-as-code) lives in [`docs/`](docs/):

- [`docs/adr/`](docs/adr/) — Architecture Decision Records
- [`docs/runbooks/`](docs/runbooks/) — operational procedures
- [`docs/troubleshooting/`](docs/troubleshooting/) — known issues and root-cause write-ups
- [`docs/lessons.md`](docs/lessons.md) — accumulated gotchas and post-mortems

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and PR workflow.

```bash
git clone https://github.com/mlorentedev/hive.git && cd hive
make install   # create venv + install deps
make check     # lint + typecheck + test (478 tests, 90% coverage)
```

## License

[MIT](LICENSE)
