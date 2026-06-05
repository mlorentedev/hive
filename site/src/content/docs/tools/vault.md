---
title: Vault Tools
description: Tools for querying, searching, and managing your Obsidian vault.
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

### Scope resolution

Projects are organized in **scopes** — top-level vault directories. By default, Hive ships with four scopes:

| Scope | Directory | Purpose |
|---|---|---|
| `projects` | `10_projects/` | Personal projects |
| `work` | `50_work/` | Work: products, clients, tickets, development |
| `agents` | `80_agents/` | AI-agent inboxes — each subdirectory is a first-class project |
| `meta` | `00_meta/` | Cross-project patterns and templates |

Use `scope:slug` syntax to target a specific scope: `vault_list(project="work:hydra3d-plus")`.

**Hierarchical scopes:** The `work` scope (and any scope) supports nested directories. If a slug isn't found as a direct child of the scope directory, Hive searches recursively using breadth-first search. For example, `work:hydra3d-plus` resolves to `50_work/20-products/hydra3d-plus/` automatically.

Use explicit paths with `/` to skip BFS: `work:20-products/hydra3d-plus`.

Without a scope prefix, Hive auto-scans all scopes in order — first match wins.

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

# Restrict search to a scope
vault_search(query="LVDS", scope="work")

# Lesson reinforcement ranking (HIVE-97)
vault_search(query="timeout", rank_by="reinforcements")  # most-read first
vault_search(query="timeout", rank_by="confidence")      # highest decayed confidence
vault_search(query="timeout", rank_by="hybrid")          # α=0.7 BM25 + 0.3 confidence
```

**Standard mode:** Returns matching lines grouped by file, with metadata headers. When `use_regex=True`, the query is compiled as a Python regular expression (case-insensitive).

**Ranked mode** (`ranked=True`): Scores results by status weight (active > draft > archived), recency, and match density. Returns top results with metadata and matching lines.

**Recent mode** (`since_days > 0`): Combines git history with frontmatter `created` dates to find files changed in the last N days. Optional `project` filter.

**Scope filter** (`scope`): Restricts the search to a single scope (e.g. `"work"`, `"projects"`). Works in all three modes. Without `scope`, searches the entire vault.

**Lesson-rank mode** (`rank_by != "bm25"`): Filters matches to `90-lessons.md` only and ranks by the chosen usage signal — every surfaced lesson is incremented once per call. `hybrid` blends BM25 (α=0.7) with confidence (1−α=0.3). Unknown `rank_by` values return a clear error rather than silently falling back to BM25.

## vault_health

Server identity, health metrics, drift detection, and usage statistics.

```python
# Health report for all projects (always includes the ## server identity block)
vault_health()

# Validate a specific project
vault_health(project="my-project", checks=["frontmatter", "stale", "links"], max_issues=50)

# Include usage statistics
vault_health(include_usage=True, usage_days=30)

# Include runtime metadata (uptime, registered tools, OpenRouter budget)
vault_health(include_runtime=True)
```

**Identity block** (always-on, ~5 lines): Prepended to every successful response so MCP hosts and operators can answer *"which hive-vault version is serving this session?"* without leaving the conversation.

```text
## server
- version: 1.15.0
- python: 3.12.10
- vault_path: /home/me/Projects/knowledge
- backends: {"ollama": true, "openrouter": false}
- started_at: 2026-05-21T20:33:20+00:00
```

Backends are reported as presence booleans only — **API keys are never embedded**. `ollama` reflects the cached availability probe; `openrouter` is true when an API key was configured at startup.

**Health report** (default): Per-project file count, total lines, stale files (>180 days, configurable via `HIVE_STALE_THRESHOLD_DAYS`), section coverage. Also detects **duplicate directory names** within hierarchical scopes and warns which path BFS will resolve to.

**Validation** (`checks` parameter): Drift detector for common issues:
- **frontmatter**: Missing or malformed YAML frontmatter, missing required fields (id, type, status), unparseable dates
- **stale**: Active files not modified in `HIVE_STALE_THRESHOLD_DAYS` (default 180)
- **links**: Broken `[[wikilinks]]` pointing to nonexistent files

Issues are categorized as `[error]` or `[warning]` with file path and description.

**Usage stats** (`include_usage=True`): Tool call counts by tool and project, with estimated token savings.

**Runtime block** (`include_runtime=True`, opt-in): Dynamic diagnostics layered after the report — uptime in seconds, count + names of MCP tools currently registered (sanity check that no module failed to register), and the OpenRouter budget snapshot (`spent_usd`, `cap_usd`, `period`). Independent of `include_usage`; both can stack in one call.

## vault_write

Create, append, or replace vault files with frontmatter validation. Auto-commits to git by default; pass `commit=False` to defer the commit and flush a batch later via [`vault_commit`](#vault_commit).

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

All operations auto-commit to git by default. Pass `commit=False` to defer the commit — the file is written to disk but `git status` will still show it dirty. Flush a batch later with [`vault_commit`](#vault_commit), or let an external committer (e.g. obsidian-git auto-commit) pick it up. See [Configuration → Recommended configuration](/configuration/#recommended-configuration) for the durability contract.

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

Replaces exactly one occurrence of `find` with `replace`. Rejects ambiguous matches — if `find` appears more than once, the operation fails with an error asking for more context. Uses 3-pass cascading match: exact → body-only → whitespace-normalized. Auto-commits to git by default; pass `commit=False` to defer the commit (same contract as `vault_write`).

## vault_commit

Flush every pending write into one git commit. Companion to `vault_write(commit=False)` and `vault_patch(commit=False)`.

```python
vault_commit(message="vault: end-of-session checkpoint")
```

Stages everything dirty in the vault (`git add -A`) and creates a single commit. Returns the new SHA, a clean-tree notice if nothing is dirty, or a human-readable error. Best paired with the obsidian-git plugin (see [Configuration → Recommended configuration](/configuration/#recommended-configuration)).

## vault_delete

Delete a single file from the vault. **Destructive — only on explicit user request.** Recoverable from git history.

```python
vault_delete(
    project="my-project",
    path="30-architecture/adr-005.md",
    commit=True,            # default: stage + commit the deletion
    idempotency_key=""      # optional at-most-once token
)
```

Removes one file and commits the deletion, so it stays recoverable via `git revert` / `git show`. **Files only** — directories are rejected. A non-existent path is an error, unless `idempotency_key` is set, in which case a retry against an already-removed file is a no-op success. Pass `commit=False` to defer the commit (same durability contract as `vault_write`).

## capture_lesson

Capture lessons inline, batch-extract from text, or look up existing lessons by keyword.

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

# Lookup mode — surface existing lessons by keyword (HIVE-97)
capture_lesson(
    project="my-project",
    find="cache",
    rank_by="reinforcements",   # or "confidence", "hybrid"
    max_lessons=5
)
```

**Inline mode** (no `text`, no `find`): Appends a structured entry to `90-lessons.md` with date, context, problem, and solution. Creates the file with frontmatter if it doesn't exist. Deduplicates by title. Auto-commits to git. Each inline write also seeds a baseline row in the reinforcement table at `confidence=0.7`.

**Batch mode** (`text` provided): Sends the text to a worker model (Ollama/OpenRouter) which extracts structured lessons (title, context, problem, solution, tags, confidence). Lessons above the confidence threshold are written to `90-lessons.md` with deduplication and seeded in the reinforcement table.

**Lookup mode** (`find` provided): Greps lesson headings (codeblock-aware) for the keyword, ranks the matches by `rank_by` (default `reinforcements`), increments each surfaced lesson once, and returns the top `max_lessons`. Single-tool symmetry: `capture_lesson` writes lessons AND queries them.

**When to use:** Immediately after discovering a bug root cause, architectural insight, or debugging trick — don't wait until session end. Use `find=` to recover past lessons by topic.
