---
title: Vault Tools
description: 12 tools for querying, searching, and managing your Obsidian vault.
---

## vault_list_projects

List all projects in the Obsidian vault.

```python
vault_list_projects()
```

Returns project names, file counts, and available section shortcuts.

## vault_query

Read sections or files on demand.

```python
vault_query(
    project="my-project",
    section="context",       # context | tasks | roadmap | lessons
    path="",                 # arbitrary path (overrides section)
    max_lines=200,           # 0 = unlimited
    include_metadata=False   # prepend frontmatter summary
)
```

Section shortcuts map to files:
- `context` → `00-context.md`
- `tasks` → `11-tasks.md`
- `roadmap` → `10-roadmap.md`
- `lessons` → `90-lessons.md`

Use `path` for arbitrary files: `vault_query(project="my-project", path="30-architecture/adr-001.md")`

Use `project="_meta"` to access `00_meta/` (cross-project patterns).

## vault_search

Full-text search across the vault.

```python
vault_search(
    query="authentication",
    max_lines=100,
    doc_type="",     # filter by frontmatter type
    status="",       # filter by frontmatter status
    tag=""           # filter by frontmatter tag
)
```

Returns matching lines grouped by file, with metadata headers.

## vault_health

Health metrics for all vault projects.

```python
vault_health()
```

Reports per-project: file count, total lines, stale files (>90 days without update), section coverage.

## vault_update

Write to an existing vault file with validation.

```python
vault_update(
    project="my-project",
    section="lessons",
    operation="append",   # append | replace
    content="New lesson learned..."
)
```

- **append**: adds content to the end
- **replace**: requires valid YAML frontmatter in the new content
- Auto-commits to git after successful write

## vault_create

Create a new file with auto-generated frontmatter.

```python
vault_create(
    project="my-project",
    path="30-architecture/adr-005.md",
    content="# ADR-005: ...",
    doc_type="adr"    # used in generated frontmatter
)
```

Generates YAML frontmatter with `id`, `type`, `status: draft`, `created: today`. Auto-commits to git.

## capture_lesson

Capture a lesson learned inline during a session.

```python
capture_lesson(
    project="my-project",
    title="Root cause was stale cache",
    context="Debugging deploy failure",
    problem="Service returned 500 after deploy",
    solution="Clear Redis cache after config changes",
    tags=["deploy", "cache"]    # optional
)
```

Appends a structured entry to `90-lessons.md` with date, context, problem, and solution. Creates the file with frontmatter if it doesn't exist. Deduplicates by title. Auto-commits to git.

**When to use:** Immediately after discovering a bug root cause, architectural insight, or debugging trick — don't wait until session end.

## vault_summarize

Smart summarization for vault files.

```python
vault_summarize(
    project="my-project",
    section="context",
    path="",
    max_summary_lines=50
)
```

- Files under the threshold are returned directly
- Large files return a delegation prompt for the worker to summarize

## vault_smart_search

Ranked search with relevance scoring.

```python
vault_smart_search(
    query="deployment",
    max_results=10,
    max_lines=200
)
```

Scores results by: status weight (active > draft > archived), recency, and match density. Returns top results with metadata and matching lines.

## session_briefing

One-call context load for starting a session.

```python
session_briefing(project="my-project")
```

Returns combined: active tasks, recent lessons, git log (last 10 commits), and health metrics.

## vault_recent

Files changed in the vault recently.

```python
vault_recent(
    since_days=7,
    project=""      # optional filter
)
```

Combines git history with frontmatter `created` dates to find recent activity.

## vault_usage

Tool usage analytics for the current session.

```python
vault_usage(since_days=30)
```

Returns call counts by tool and project, with estimated token savings.
