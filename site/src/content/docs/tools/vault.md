---
title: Vault Tools
description: 16 tools for querying, searching, and managing your Obsidian vault.
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
    type_filter="",     # filter by frontmatter type
    status_filter="",   # filter by frontmatter status
    tag_filter="",      # filter by frontmatter tag
    use_regex=False     # treat query as a regular expression
)
```

Returns matching lines grouped by file, with metadata headers.

When `use_regex=True`, the query is compiled as a Python regular expression (case-insensitive). Invalid regex patterns return an error message.

## vault_health

Health metrics for all vault projects.

```python
vault_health()
```

Reports per-project: file count, total lines, stale files (>180 days by default, configurable via `HIVE_STALE_THRESHOLD_DAYS`), section coverage.

## vault_validate

Drift detector — validate vault files for common issues.

```python
vault_validate(project="my-project", checks=["frontmatter", "stale", "links"], max_issues=50)
```

| Parameter | Default | Description |
|---|---|---|
| `project` | `""` (all) | Project to validate. Empty scans all projects |
| `checks` | `[]` (all) | Which checks to run: `frontmatter`, `stale`, `links` |
| `max_issues` | `50` | Maximum issues to report |

**Checks:**
- **frontmatter**: Missing or malformed YAML frontmatter, missing required fields (id, type, status), unparseable dates
- **stale**: Active files not modified in `HIVE_STALE_THRESHOLD_DAYS` (default 180)
- **links**: Broken `[[wikilinks]]` pointing to nonexistent files

Issues are categorized as `[error]` or `[warning]` with file path and description.

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

## vault_list_files

List files and directories in a vault project.

```python
vault_list_files(
    project="my-project",
    path="",           # subdirectory (relative to project root)
    pattern=""         # glob pattern for recursive filtering (e.g. 'adr-*', '*.md')
)
```

Without `pattern`, lists the immediate contents of the directory (directories first, then files). With `pattern`, recursively finds all matching files.

## vault_patch

Surgical text replacement in a vault file.

```python
vault_patch(
    project="my-project",
    path="30-architecture/adr-001.md",
    old_text="status: draft",
    new_text="status: accepted"
)
```

Replaces exactly one occurrence of `old_text` with `new_text`. Rejects ambiguous matches — if `old_text` appears more than once, the operation fails with an error asking for more context. Auto-commits to git.

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

## extract_lessons

Batch-extract lessons from raw text using a worker model.

```python
extract_lessons(
    project="my-project",
    text="We found that the cache was stale after deploy...",
    min_confidence=0.7,   # 0.0-1.0, filter low-confidence extractions
    max_lessons=5          # cap on lessons extracted
)
```

Sends the text to a worker model (Ollama/OpenRouter) which extracts structured lessons (title, context, problem, solution, tags, confidence). Lessons above the confidence threshold are written to `90-lessons.md` with deduplication.

**Why use this instead of `capture_lesson`?** `capture_lesson` requires you to structure each lesson manually. `extract_lessons` sends raw text to a cheaper model that does the structuring — saving your primary model's tokens on bulk extraction.

Returns a summary: which lessons were written, which were skipped (duplicates or low confidence).

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
