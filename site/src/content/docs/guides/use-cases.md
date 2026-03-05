---
title: Use Cases
description: Real-world workflows and examples with Hive.
---

## Starting a New Session

Load all the context you need with a single call:

> "Run a session briefing for my-project"

Your AI assistant calls `session_briefing(project="my-project")` and gets back: active tasks, recent lessons, git history, and health metrics — everything needed to pick up where you left off.

## Querying Project Knowledge

Instead of pasting standards into CLAUDE.md, query them on demand:

> "Load the architecture patterns from the vault"

```python
vault_query(project="_meta", path="patterns/pattern-architecture.md")
```

Only the relevant document is loaded. Your 800-line CLAUDE.md stays at 100 lines.

## Finding Information Across Projects

Search across your entire knowledge base:

> "Search the vault for how we handle authentication"

```python
vault_search(query="authentication", doc_type="adr")
```

Returns matching lines from all files, filtered to only ADR documents, with metadata headers.

## Smart Search with Ranking

When you need the most relevant results, not just keyword matches:

> "Find the most relevant docs about deployment"

```python
vault_smart_search(query="deployment", max_results=5)
```

Results are ranked by: active > draft > archived, recent > old, and match density. The most useful documents surface first.

## Recording Lessons Learned

After solving a tricky bug or making an architecture decision:

> "Append this lesson to the vault: Always use WAL mode for SQLite in async contexts"

```python
vault_update(
    project="my-project",
    section="lessons",
    operation="append",
    content="\n## SQLite WAL Mode\nAlways use WAL mode..."
)
```

Auto-commits to git. The lesson is available in future sessions.

## Creating New Documents

Start a new ADR, runbook, or any document:

> "Create an ADR for choosing PostgreSQL over MySQL"

```python
vault_create(
    project="my-project",
    path="30-architecture/adr-005-postgresql.md",
    content="# ADR-005: PostgreSQL over MySQL\n\n## Context\n...",
    doc_type="adr"
)
```

Hive auto-generates YAML frontmatter and commits to git.

## Delegating Trivial Tasks

Save tokens by routing simple tasks to cheaper models:

> "Delegate: explain this regex ^(?:[a-z0-9]+\.)*[a-z0-9]+$"

```python
delegate_task(
    prompt="Explain this regex: ^(?:[a-z0-9]+\\.)*[a-z0-9]+$",
    context="Used for domain name validation"
)
```

Routes to Ollama (free, local) first. Falls back to OpenRouter if unavailable.

## End-of-Session Retrospective

Before ending a work session, capture what you learned:

> "Run a retrospective for my-project"

The `retrospective` prompt guides your assistant through:
1. Reviewing completed work
2. Identifying patterns and insights
3. Formatting structured lessons
4. Appending to the project's `90-lessons.md`

## Post-Sprint Vault Sync

After shipping a feature, reconcile docs with code:

> "Sync the vault for my-project"

The `vault_sync` prompt walks through:
1. Comparing vault docs with recent git history
2. Marking completed tasks as done
3. Updating stale documentation
4. Flagging gaps that need new docs

## Monitoring Token Savings

Curious how much Hive is saving you?

> "Run a benchmark"

The `benchmark` prompt checks `vault_usage` and estimates how many tokens would have been consumed by static context loading vs. on-demand queries.

## Checking Recent Vault Changes

See what's been updated in your vault recently:

> "Show vault changes from the last 7 days"

```python
vault_recent(since_days=7, project="my-project")
```

Combines git history with frontmatter dates to surface all recent activity.

## Checking Worker Budget

Monitor your OpenRouter spending:

> "Show worker status"

```python
worker_status()
```

Returns: monthly budget remaining, provider connectivity, request counts, and cost breakdown.
