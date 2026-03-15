---
title: Vault Tools
description: 7 tools for querying, searching, and managing your Obsidian vault.
---

## vault_list

List projects or browse files within a project.

```python
# List all projects
vault_list()

# Browse files in a project
vault_list(project="my-project")

# Browse a subdirectory
vault_list(project="my-project", path="30-architecture")

# Glob pattern filtering
vault_list(project="my-project", pattern="adr-*")
```

Without `project`, lists all vault projects with file counts and shortcuts. With `project`, lists files and directories (directories first, then files). With `pattern`, recursively finds all matching files.

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

Full-text search across the vault with optional ranking and recent-changes mode.

```python
# Standard keyword search
vault_search(
    query="authentication",
    max_lines=100,
    type_filter="",     # filter by frontmatter type
    status_filter="",   # filter by frontmatter status
    tag_filter="",      # filter by frontmatter tag
    use_regex=False     # treat query as a regular expression
)

# Ranked search (relevance scoring)
vault_search(query="deployment", ranked=True, max_results=5)

# Recent changes (last N days)
vault_search(since_days=7, project="my-project")
```

**Standard mode:** Returns matching lines grouped by file, with metadata headers. When `use_regex=True`, the query is compiled as a Python regular expression (case-insensitive).

**Ranked mode** (`ranked=True`): Scores results by status weight (active > draft > archived), recency, and match density. Returns top results with metadata and matching lines.

**Recent mode** (`since_days > 0`): Combines git history with frontmatter `created` dates to find files changed in the last N days. Optional `project` filter.

## vault_health

Health metrics, drift detection, and usage statistics.

```python
# Health report for all projects
vault_health()

# Validate a specific project
vault_health(project="my-project", checks=["frontmatter", "stale", "links"], max_issues=50)

# Include usage statistics
vault_health(include_usage=True, usage_days=30)
```

**Health report** (default): Per-project file count, total lines, stale files (>180 days, configurable via `HIVE_STALE_THRESHOLD_DAYS`), section coverage.

**Validation** (`checks` parameter): Drift detector for common issues:
- **frontmatter**: Missing or malformed YAML frontmatter, missing required fields (id, type, status), unparseable dates
- **stale**: Active files not modified in `HIVE_STALE_THRESHOLD_DAYS` (default 180)
- **links**: Broken `[[wikilinks]]` pointing to nonexistent files

Issues are categorized as `[error]` or `[warning]` with file path and description.

**Usage stats** (`include_usage=True`): Tool call counts by tool and project, with estimated token savings.

## vault_write

Create, append, or replace vault files with validation and auto git commit.

```python
# Append to an existing file
vault_write(
    project="my-project",
    section="lessons",
    content="New lesson learned...",
    operation="append"      # default
)

# Replace an entire file (requires valid frontmatter)
vault_write(
    project="my-project",
    section="context",
    content="---\nid: my-project\ntype: project\nstatus: active\n---\n\nNew content.",
    operation="replace"
)

# Create a new file with auto-generated frontmatter
vault_write(
    project="my-project",
    path="30-architecture/adr-005.md",
    content="# ADR-005: PostgreSQL\n\n## Context\n...",
    operation="create",
    doc_type="adr"          # used in generated frontmatter
)
```

- **append**: Adds content to the end of an existing file
- **replace**: Replaces the entire file. Requires valid YAML frontmatter with `id`, `type`, `status`
- **create**: Creates a new file. Auto-generates frontmatter with `id`, `type`, `status: draft`, `created: today`

All operations auto-commit to git.

## vault_patch

Surgical find-and-replace in a vault file.

```python
# Single replacement
vault_patch(
    project="my-project",
    path="30-architecture/adr-001.md",
    find="status: draft",
    replace="status: accepted"
)

# Multiple replacements (applied in sequence)
vault_patch(
    project="my-project",
    path="11-tasks.md",
    patches=[
        {"find": "- [ ] Task one", "replace": "- [x] Task one"},
        {"find": "- [ ] Task two", "replace": "- [x] Task two"},
    ]
)
```

Replaces exactly one occurrence of `find` with `replace`. Rejects ambiguous matches — if `find` appears more than once, the operation fails with an error asking for more context. Uses 3-pass cascading match: exact → body-only → whitespace-normalized. Auto-commits to git.

## capture_lesson

Capture lessons inline or batch-extract from text using a worker.

```python
# Inline capture (structured, single lesson)
capture_lesson(
    project="my-project",
    title="Root cause was stale cache",
    context="Debugging deploy failure",
    problem="Service returned 500 after deploy",
    solution="Clear Redis cache after config changes",
    tags=["deploy", "cache"]    # optional
)

# Batch extraction (worker-powered, multiple lessons)
capture_lesson(
    project="my-project",
    text="We found that the cache was stale after deploy...",
    min_confidence=0.7,
    max_lessons=5
)
```

**Inline mode** (no `text`): Appends a structured entry to `90-lessons.md` with date, context, problem, and solution. Creates the file with frontmatter if it doesn't exist. Deduplicates by title. Auto-commits to git.

**Batch mode** (`text` provided): Sends the text to a worker model (Ollama/OpenRouter) which extracts structured lessons (title, context, problem, solution, tags, confidence). Lessons above the confidence threshold are written to `90-lessons.md` with deduplication.

**When to use:** Immediately after discovering a bug root cause, architectural insight, or debugging trick — don't wait until session end.
