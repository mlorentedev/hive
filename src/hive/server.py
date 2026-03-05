"""Hive MCP Server — on-demand Obsidian vault access + worker delegation."""

from __future__ import annotations

import subprocess
from datetime import date, timedelta
from typing import TYPE_CHECKING

from fastmcp import FastMCP

if TYPE_CHECKING:
    from pathlib import Path

from hive.budget import BudgetTracker
from hive.clients import ClientResponse, OllamaClient, OpenRouterClient
from hive.config import settings
from hive.frontmatter import (
    _TERMINAL_STATUSES,
    Frontmatter,
    extract_body,
    parse_date,
    parse_frontmatter,
    validate_frontmatter,
)
from hive.relevance import RelevanceTracker
from hive.usage import UsageTracker

_VALID_OPERATIONS = {"append", "replace"}

SECTION_SHORTCUTS: dict[str, str] = {
    "context": "00-context.md",
    "tasks": "11-tasks.md",
    "roadmap": "10-roadmap.md",
    "lessons": "90-lessons.md",
}


_DEFAULT_SCOPES: dict[str, str] = {"projects": "10_projects", "meta": "00_meta"}


def _parse_project_ref(project: str) -> tuple[str | None, str]:
    """Split 'scope:project' into (scope, project). Plain 'project' → (None, project)."""
    if ":" in project:
        scope, _, slug = project.partition(":")
        return scope, slug
    return None, project


def _resolve_project_dir(
    vault: Path, project: str, scopes: dict[str, str] | None = None,
) -> tuple[Path, str] | None:
    """Resolve a project slug to (directory, scope_name).

    - ``_meta`` maps to the meta scope root (backward compat).
    - ``scope:project`` targets a specific scope.
    - Plain ``project`` auto-scans all scopes, first match wins.
    """
    scopes = scopes or _DEFAULT_SCOPES

    # _meta special case → meta scope root
    if project == "_meta":
        meta_dir_name = scopes.get("meta", "00_meta")
        d = vault / meta_dir_name
        return (d, "meta") if d.is_dir() else None

    explicit_scope, slug = _parse_project_ref(project)

    if explicit_scope is not None:
        dir_name = scopes.get(explicit_scope)
        if dir_name is None:
            return None
        d = vault / dir_name / slug
        return (d, explicit_scope) if d.is_dir() else None

    # Auto-scan: iterate scopes, first match wins, skip missing dirs
    for scope_name, dir_name in scopes.items():
        if scope_name == "meta":
            continue  # meta is not a project container
        scope_dir = vault / dir_name
        if not scope_dir.is_dir():
            continue
        d = scope_dir / slug
        if d.is_dir():
            return (d, scope_name)

    return None


def _truncate(text: str, max_lines: int) -> str:
    """Truncate text to max_lines, appending a notice if truncated."""
    if max_lines <= 0:
        return text
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    remaining = len(lines) - max_lines
    return "\n".join(lines[:max_lines]) + f"\n\n[... truncated, {remaining} more lines]"


def _resolve_file(
    vault: Path,
    project: str,
    section: str,
    path: str,
    scopes: dict[str, str] | None = None,
) -> Path | str:
    """Resolve a vault file from project + section/path. Returns Path or error string."""
    result = _resolve_project_dir(vault, project, scopes)
    if result is None:
        return f"Project '{project}' not found in vault."
    project_dir, _ = result

    if path:
        filepath = project_dir / path
    else:
        filename = SECTION_SHORTCUTS.get(section)
        if filename is None:
            available = ", ".join(SECTION_SHORTCUTS)
            return f"Section '{section}' not found. Available shortcuts: {available}"
        # Convention-first: try bare name (e.g. context.md) before legacy (00-context.md)
        bare = project_dir / f"{section}.md"
        filepath = bare if bare.exists() else project_dir / filename

    if not filepath.exists():
        target = path or section
        return f"'{target}' not found in project '{project}'."

    return filepath


_SUMMARIZE_THRESHOLD = 50

_STATUS_WEIGHTS: dict[str, float] = {
    "active": 3.0,
    "draft": 2.0,
}

_RECENCY_DAYS_SCALE = 365


def _format_metadata(fm: Frontmatter | None) -> str:
    """Format frontmatter as a one-line metadata summary."""
    if fm is None:
        return ""
    tags = ", ".join(fm.tags) if fm.tags else "none"
    return f"type={fm.type}, status={fm.status}, tags=[{tags}], created={fm.created}"


def _format_response(resp: ClientResponse) -> str:
    """Format a model response with metadata footer."""
    cost_str = f"${resp.cost_usd:.4f}" if resp.cost_usd > 0 else "$0.00"
    latency_str = f"{resp.latency_ms / 1000:.1f}s"
    header = (
        f"## Worker Response (model: {resp.model}, {resp.tokens} tokens, {cost_str}, {latency_str})"
    )
    return f"{header}\n\n{resp.text}"


def _build_delegation_prompt(
    meta: str, body: str, line_count: int, max_summary_lines: int
) -> str:
    """Build a structured prompt for delegating summarization to the worker."""
    parts = ["## Summarization Request", ""]
    if meta:
        parts.append(f"**Metadata:** {meta}")
    parts.append(f"**Source length:** {line_count} lines")
    parts.append(f"**Target length:** {max_summary_lines} lines")
    parts.extend([
        "",
        "### Suggested `delegate_task` parameters",
        f'- task: "Summarize the following document in at most {max_summary_lines} lines, '
        'preserving key decisions and action items."',
        "- max_tokens: 2000",
        "",
        "### Document body",
        "",
        body,
    ])
    return "\n".join(parts)


def _score_file(match_count: int, fm: Frontmatter | None, today: date) -> float:
    """Score a file for smart search ranking."""
    status_weight = 1.0
    recency_bonus = 0.0

    if fm is not None:
        status_weight = _STATUS_WEIGHTS.get(fm.status, 1.0)
        created = parse_date(fm.created)
        if created is not None:
            days_ago = (today - created).days
            recency_bonus = max(0.0, 1.0 - days_ago / _RECENCY_DAYS_SCALE)

    return match_count * status_weight + recency_bonus


def create_server(
    vault_path: Path | None = None,
    usage_tracker: UsageTracker | None = None,
    budget_tracker: BudgetTracker | None = None,
    ollama_client: OllamaClient | None = None,
    openrouter_client: OpenRouterClient | None = None,
    vault_scopes: dict[str, str] | None = None,
    relevance_tracker: RelevanceTracker | None = None,
) -> FastMCP:
    """Create and configure the Hive MCP server."""
    resolved_path = vault_path or settings.vault_path
    scopes = vault_scopes or settings.vault_scopes
    tracker = usage_tracker or UsageTracker()
    budget = budget_tracker or BudgetTracker(db_path=settings.db_path)
    ollama = ollama_client or OllamaClient(
        endpoint=settings.ollama_endpoint, model=settings.ollama_model
    )
    openrouter: OpenRouterClient | None = None
    if openrouter_client is not None:
        openrouter = openrouter_client
    elif settings.openrouter_api_key:
        openrouter = OpenRouterClient(
            api_key=settings.openrouter_api_key, default_model=settings.openrouter_model
        )
    relevance = relevance_tracker or RelevanceTracker(
        db_path=settings.relevance_db_path,
    )
    mcp = FastMCP("Hive")

    def _track(
        tool: str, result: str, project: str = "", section: str = "",
    ) -> str:
        """Log a tool call and return the result unchanged."""
        tracker.log_call(tool, project, len(result.splitlines()))
        if project and section:
            is_write = tool in {"vault_update", "vault_create"}
            relevance.record_access(project, section, is_write=is_write)
        return result

    # ── Resources ────────────────────────────────────────────────────────

    @mcp.resource("hive://projects")
    def projects_resource() -> str:
        """List all vault projects with file counts and available shortcuts."""
        lines = ["# Vault Projects", ""]
        found_any = False
        for scope_name, dir_name in scopes.items():
            if scope_name == "meta":
                continue
            scope_dir = resolved_path / dir_name
            if not scope_dir.is_dir():
                continue
            projects = sorted(d for d in scope_dir.iterdir() if d.is_dir())
            for project_dir in projects:
                found_any = True
                sections = [
                    s for s, filename in SECTION_SHORTCUTS.items()
                    if (project_dir / filename).exists()
                ]
                md_count = len(list(project_dir.rglob("*.md")))
                lines.append(
                    f"- **{scope_name}/{project_dir.name}** — {md_count} files, "
                    f"shortcuts: {', '.join(sections) or 'none'}"
                )

        if not found_any:
            return "No projects found in vault."
        return "\n".join(lines)

    @mcp.resource("hive://health")
    def health_resource() -> str:
        """Vault health metrics for all projects."""
        stale_threshold = date.today() - timedelta(days=180)
        lines = ["# Vault Health Report", ""]
        found_any = False

        for scope_name, dir_name in scopes.items():
            if scope_name == "meta":
                continue
            scope_dir = resolved_path / dir_name
            if not scope_dir.is_dir():
                continue
            projects = sorted(d for d in scope_dir.iterdir() if d.is_dir())
            for project_dir in projects:
                found_any = True
                md_files = list(project_dir.rglob("*.md"))
                total_lines = 0
                stale_files: list[str] = []

                for f in md_files:
                    try:
                        content = f.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    total_lines += len(content.splitlines())

                    fm = parse_frontmatter(content)
                    if fm is not None and fm.status in _TERMINAL_STATUSES:
                        continue

                    created_date = parse_date(fm.created) if fm is not None else None
                    if created_date is None:
                        created_date = date.fromtimestamp(f.stat().st_mtime)

                    if created_date < stale_threshold:
                        stale_files.append(f.relative_to(project_dir).as_posix())

                missing = [
                    s for s, fname in SECTION_SHORTCUTS.items()
                    if not (project_dir / fname).exists()
                ]

                lines.append(f"## {scope_name}/{project_dir.name}")
                lines.append(f"- Files: {len(md_files)}")
                lines.append(f"- Total lines: {total_lines}")
                if missing:
                    lines.append(f"- Missing sections: {', '.join(missing)}")
                if stale_files:
                    lines.append(
                        f"- Stale files (>180d): {', '.join(sorted(stale_files))}"
                    )
                lines.append("")

        if not found_any:
            return "No projects found in vault."
        return "\n".join(lines)

    @mcp.resource("hive://projects/{project}/context")
    def context_resource(project: str) -> str:
        """Project context document (00-context.md)."""
        result = _resolve_file(resolved_path, project, "context", "", scopes)
        if isinstance(result, str):
            return result
        return _truncate(result.read_text(encoding="utf-8"), 200)

    @mcp.resource("hive://projects/{project}/tasks")
    def tasks_resource(project: str) -> str:
        """Project task backlog (11-tasks.md)."""
        result = _resolve_file(resolved_path, project, "tasks", "", scopes)
        if isinstance(result, str):
            return result
        return _truncate(result.read_text(encoding="utf-8"), 200)

    @mcp.resource("hive://projects/{project}/lessons")
    def lessons_resource(project: str) -> str:
        """Project lessons learned (90-lessons.md)."""
        result = _resolve_file(resolved_path, project, "lessons", "", scopes)
        if isinstance(result, str):
            return result
        return _truncate(result.read_text(encoding="utf-8"), 200)

    # ── Prompts ─────────────────────────────────────────────────────────

    @mcp.prompt
    def retrospective(project: str) -> str:
        """Quick end-of-session review that extracts lessons and appends them to the vault."""
        return f"""\
# Session Retrospective — {project}

## Protocol

### Step 1 — Summarize Session
- Review the conversation history for this session
- Identify: bugs fixed, decisions made, patterns discovered, surprises encountered
- If nothing notable happened, report "Nothing to capture" and stop

### Step 2 — Read Current Lessons
- `vault_query(project="{project}", section="lessons")` to load `90-lessons.md`
- Note existing lessons to avoid duplicates

### Step 3 — Draft Lessons
- Write 1-5 lessons using this exact template:

```markdown
### [YYYY-MM-DD] <Title>
**Context:** <what you were doing when you hit this>
**Problem:** <what went wrong or what decision was needed>
**Solution:** <what fixed it or what was decided>
**Why:** <root cause or rationale>
**Tags:** `#tag1` `#tag2`
```

- Show drafts to the user for approval before writing

### Step 4 — Append to Vault
- `vault_update(project="{project}", section="lessons", operation="append", content=<lessons>)`
- Never modify or rewrite existing lessons — append only

### Step 5 — Report
```
Retrospective complete:
  - X lessons appended to 90-lessons.md
  - Topics: <comma-separated titles>
```

## Rules

- Max 5 lessons per session — be selective
- Never modify existing vault content
- Skip entirely if nothing notable happened
- Source: conversation history only
- All content in English"""

    @mcp.prompt
    def delegate(task: str) -> str:
        """Structured protocol for delegating tasks to cheaper models via hive-worker."""
        return f"""\
# Worker Delegation — {task}

## Protocol

### Step 1 — Suitability Check
Evaluate the task against this matrix:

| Delegatable | NOT Delegatable |
|---|---|
| Summarization | Architecture decisions |
| Boilerplate generation | Multi-file refactoring |
| Format conversion | Security-sensitive logic |
| Documentation drafts | Complex debugging |
| Data transformation | Code that handles secrets |
| Regex/pattern writing | Ambiguous requirements |

If the task is NOT delegatable, say so and handle it directly. Stop here.

### Step 2 — Budget Check
- `worker_status()` to check remaining budget and model availability
- If budget exhausted or no models available, report and stop

### Step 3 — Context Compression
- `vault_summarize(paths=<relevant files>)` if the task needs vault context
- Strip the task to its essential instruction — remove conversational context
- Keep prompt under 2000 tokens

### Step 4 — Delegate
- `list_models()` to see available models and pick the appropriate tier
- `delegate_task(prompt=<compressed task>, task_type=<type>)`
- One task per call — never batch

### Step 5 — Evaluate Result
- Review the worker's output for correctness
- If acceptable: present to user with source attribution ("Generated by <model>")
- If poor quality: report failure, handle the task directly

## Rules

- Always check budget before delegating
- Never delegate tasks involving secrets, credentials, or auth logic
- One task per `delegate_task` call
- State which model handled the task in your response
- If the worker fails or returns poor quality, handle it yourself — don't retry"""

    @mcp.prompt
    def vault_sync(project: str) -> str:
        """Post-sprint vault synchronization — reconcile docs with shipped code."""
        return f"""\
# Vault Sync — {project}

## Protocol

### Step 1 — Gather Code State
- Run `git log --oneline -20` to see recent commits
- Run `git tag --sort=-creatordate | head -5` for recent releases
- Note: features added, bugs fixed, phases completed

### Step 2 — Gather Vault State
- `vault_health()` for overall vault status and stale documents
- `vault_query(project="{project}", section="context")` for project context doc
- `vault_query(project="{project}", section="tasks")` for task backlog

### Step 3 — Identify Drift
Compare code state vs vault state. Look for:
- Tasks marked TODO in vault that are already shipped in code
- Context doc describing old architecture that has changed
- Missing entries for new features/phases
- Stale status fields (e.g., "in progress" when already merged)

### Step 4 — Present Diff Plan
Show the user a summary:
```
Vault Sync Plan:
  context.md:
    - UPDATE: Phase X status "in progress" -> "shipped"
    - ADD: New tool vault_foo description
  tasks.md:
    - DONE: [x] Task A (commit abc123)
    - DONE: [x] Task B (commit def456)
  lessons.md:
    - APPEND: Sprint N retrospective (if any)
```

**Wait for explicit user approval before proceeding.**

### Step 5 — Apply Updates
- Context/tasks: `vault_update(operation="replace", ...)` for factual updates
- Lessons: `vault_update(operation="append", ...)` for new entries only
- Never delete vault content without explicit user request

### Step 6 — Verify
- `vault_query` the updated sections to confirm changes applied correctly
- Report what was updated

## Rules

- Always confirm before writing — show the plan first
- Use `replace` for context and tasks (factual state)
- Use `append` for lessons (never modify existing)
- All content in English
- One vault_update call per section to minimize git commits"""

    @mcp.prompt
    def benchmark() -> str:
        """Estimate token savings from hive MCP tools in the current session."""
        return """\
# Session Token Savings Benchmark

## Protocol

### Step 1 — Inventory Tool Usage
Scan this conversation for all hive MCP tool calls:
- `vault_query` / `vault_search` / `vault_smart_search` / `vault_summarize` calls
- `delegate_task` / `worker_status` / `list_models` calls
- Count each occurrence and note what was queried

### Step 2 — Estimate On-Demand Cost
For each vault tool call:
- Estimate response size in lines from the conversation
- Apply heuristic: **10 tokens per line**
- Sum total: this is the actual tokens consumed via on-demand loading

### Step 3 — Estimate Static Alternative
Without hive, the same information would require static CLAUDE.md sections:
- Each vault section queried = full section loaded every turn
- Estimate full section sizes (context ~200 lines, tasks ~150 lines, lessons ~100 lines)
- Multiply by number of conversation turns where that context was relevant
- This is the hypothetical static cost

### Step 4 — Worker Savings
- `worker_status()` to get delegation stats (tasks completed, tokens used)
- Each delegated task = tokens that the host didn't need to generate
- Estimate saved host tokens from delegation

### Step 5 — Report
```
=== Hive Session Benchmark ===

Vault queries: N calls
  On-demand tokens consumed: ~X
  Static alternative would cost: ~Y
  Vault savings: ~Z tokens (N% reduction)

Worker delegations: M tasks
  Worker tokens used: ~A
  Host tokens saved: ~B

Total estimated savings: ~C tokens
```

## Rules

- All numbers are estimates — state this clearly in the report
- Heuristic: 10 tokens per line of markdown
- Skip if no hive tools were used this session
- Source: conversation history only — no external instrumentation
- Do not count tool calls that returned errors"""

    # ── Tools ───────────────────────────────────────────────────────────

    @mcp.tool
    def vault_list_projects() -> str:
        """List all projects available in the Obsidian vault."""
        lines = ["# Vault Projects", ""]
        found_any = False
        for scope_name, dir_name in scopes.items():
            if scope_name == "meta":
                continue
            scope_dir = resolved_path / dir_name
            if not scope_dir.is_dir():
                continue
            projects = sorted(d for d in scope_dir.iterdir() if d.is_dir())
            for project_dir in projects:
                found_any = True
                sections = [
                    s for s, filename in SECTION_SHORTCUTS.items()
                    if (project_dir / filename).exists()
                ]
                md_count = len(list(project_dir.rglob("*.md")))
                lines.append(
                    f"- **{scope_name}/{project_dir.name}** — {md_count} files, "
                    f"shortcuts: {', '.join(sections) or 'none'}"
                )

        if not found_any:
            return _track("vault_list_projects", "No projects found in vault.")
        return _track("vault_list_projects", "\n".join(lines))

    @mcp.tool
    def vault_query(
        project: str,
        section: str = "context",
        path: str = "",
        max_lines: int = 0,
        include_metadata: bool = False,
    ) -> str:
        """Read content from a vault project.

        Args:
            project: Project slug (directory under 10_projects/), or '_meta' for 00_meta/.
            section: Shortcut name (context, tasks, roadmap, lessons). Ignored if path is set.
            path: Relative path to a specific .md file within the project. Overrides section.
            max_lines: Maximum lines to return. 0 = unlimited.
            include_metadata: Prepend a structured metadata line from YAML frontmatter.
        """
        resolved_section = path or section
        result = _resolve_file(resolved_path, project, section, path, scopes)
        if isinstance(result, str):
            return _track("vault_query", result, project, resolved_section)
        filepath = result

        content = filepath.read_text(encoding="utf-8")

        if include_metadata:
            fm = parse_frontmatter(content)
            if fm is not None:
                tags = ", ".join(fm.tags) if fm.tags else "none"
                meta_line = (
                    f"**Metadata:** type={fm.type}, status={fm.status}, "
                    f"tags=[{tags}], created={fm.created}\n\n"
                )
                content = meta_line + content

        return _track("vault_query", _truncate(content, max_lines),
                       project, resolved_section)

    @mcp.tool
    def vault_search(
        query: str,
        max_lines: int = 100,
        type_filter: str = "",
        status_filter: str = "",
        tag_filter: str = "",
    ) -> str:
        """Full-text search across all markdown files in the vault.

        Args:
            query: Text to search for (case-insensitive).
            max_lines: Maximum output lines. Default 100.
            type_filter: Only include files whose frontmatter type matches (e.g. 'adr').
            status_filter: Only include files whose frontmatter status matches (e.g. 'active').
            tag_filter: Only include files that have this tag in their frontmatter tags list.
        """
        results: list[str] = []
        query_lower = query.lower()
        has_filters = bool(type_filter or status_filter or tag_filter)

        for md_file in sorted(resolved_path.rglob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            fm = parse_frontmatter(content)

            if has_filters:
                if fm is None:
                    continue
                if type_filter and fm.type != type_filter:
                    continue
                if status_filter and fm.status != status_filter:
                    continue
                if tag_filter and tag_filter not in fm.tags:
                    continue

            matching_lines = [
                line.strip() for line in content.splitlines() if query_lower in line.lower()
            ]
            if matching_lines:
                rel = md_file.relative_to(resolved_path)
                meta = ""
                if fm is not None:
                    meta = f" [type: {fm.type}, status: {fm.status}]"
                results.append(f"### {rel}{meta}")
                for line in matching_lines[:5]:
                    results.append(f"  - {line}")

        if not results:
            return _track("vault_search", f"No matches found for '{query}'.")

        output = f"# Search: '{query}'\n\n" + "\n".join(results)
        return _track("vault_search", _truncate(output, max_lines))

    @mcp.tool
    def vault_health() -> str:
        """Return health metrics for all vault projects."""
        stale_threshold = date.today() - timedelta(days=180)
        lines = ["# Vault Health Report", ""]
        found_any = False

        for scope_name, dir_name in scopes.items():
            if scope_name == "meta":
                continue
            scope_dir = resolved_path / dir_name
            if not scope_dir.is_dir():
                continue
            projects = sorted(d for d in scope_dir.iterdir() if d.is_dir())
            for project_dir in projects:
                found_any = True
                md_files = list(project_dir.rglob("*.md"))
                total_lines = 0
                stale_files: list[str] = []

                for f in md_files:
                    try:
                        content = f.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    total_lines += len(content.splitlines())

                    fm = parse_frontmatter(content)
                    if fm is not None and fm.status in _TERMINAL_STATUSES:
                        continue

                    created_date = parse_date(fm.created) if fm is not None else None
                    if created_date is None:
                        created_date = date.fromtimestamp(f.stat().st_mtime)

                    if created_date < stale_threshold:
                        stale_files.append(f.relative_to(project_dir).as_posix())

                missing = [
                    s for s, fname in SECTION_SHORTCUTS.items()
                    if not (project_dir / fname).exists()
                ]

                lines.append(f"## {scope_name}/{project_dir.name}")
                lines.append(f"- Files: {len(md_files)}")
                lines.append(f"- Total lines: {total_lines}")
                if missing:
                    lines.append(f"- Missing sections: {', '.join(missing)}")
                if stale_files:
                    lines.append(
                        f"- Stale files (>180d): {', '.join(sorted(stale_files))}"
                    )
                lines.append("")

        if not found_any:
            return _track("vault_health", "No projects found in vault.")
        return _track("vault_health", "\n".join(lines))

    @mcp.tool
    def vault_update(
        project: str,
        section: str,
        operation: str,
        content: str,
    ) -> str:
        """Update a vault project section with auto git commit.

        Args:
            project: Project slug (directory under 10_projects/).
            section: Section shortcut (context, tasks, roadmap, lessons).
            operation: 'append' to add content at end, 'replace' to overwrite file.
            content: The markdown content to write.
        """
        if operation not in _VALID_OPERATIONS:
            return _track("vault_update", (
                f"Invalid operation '{operation}'. "
                f"Valid operations: {', '.join(sorted(_VALID_OPERATIONS))}"
            ), project)

        resolved = _resolve_project_dir(resolved_path, project, scopes)
        if resolved is None:
            return _track("vault_update",
                          f"Project '{project}' not found in vault.", project)
        project_dir, _ = resolved

        filename = SECTION_SHORTCUTS.get(section)
        if filename is None:
            available = ", ".join(SECTION_SHORTCUTS)
            return _track("vault_update",
                          f"Section '{section}' not found. Available: {available}", project)

        filepath = project_dir / filename

        if operation == "replace":
            error = validate_frontmatter(content)
            if error:
                return _track("vault_update",
                              f"Frontmatter validation failed: {error}", project)

        if operation == "append":
            existing = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
            filepath.write_text(existing + content, encoding="utf-8")
        else:
            filepath.write_text(content, encoding="utf-8")

        rel = filepath.relative_to(resolved_path)
        _git_commit(resolved_path, rel, f"vault: update {project}/{section}")

        return _track("vault_update",
                       f"Updated {project}/{section} ({operation}).",
                       project, section)

    @mcp.tool
    def vault_create(
        project: str,
        path: str,
        content: str,
        doc_type: str,
    ) -> str:
        """Create a new file in the vault with auto-generated frontmatter.

        Args:
            project: Project slug or '_meta' for cross-project content.
            path: Relative path for the new file (e.g. '30-architecture/adr-002.md').
            content: Markdown body (frontmatter will be auto-generated).
            doc_type: Document type for frontmatter (adr, lesson, pattern, troubleshooting, etc.).
        """
        resolved = _resolve_project_dir(resolved_path, project, scopes)
        if resolved is None:
            return _track("vault_create",
                          f"Project '{project}' not found in vault.", project)
        project_dir, _ = resolved

        filepath = project_dir / path
        if filepath.exists():
            return _track("vault_create",
                          f"File already exists: {path}. Use vault_update to modify it.",
                          project)

        # Auto-generate frontmatter
        stem = filepath.stem
        frontmatter = (
            f"---\n"
            f"id: {stem}\n"
            f"type: {doc_type}\n"
            f"status: active\n"
            f'created: "{date.today().isoformat()}"\n'
            f"---\n\n"
        )

        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(frontmatter + content, encoding="utf-8")

        rel = filepath.relative_to(resolved_path)
        display_project = "00_meta" if project == "_meta" else project
        _git_commit(resolved_path, rel, f"vault: create {display_project}/{path}")

        return _track("vault_create",
                       f"Created {project}/{path} (type: {doc_type}).",
                       project, path)

    @mcp.tool
    def vault_summarize(
        project: str,
        section: str = "context",
        path: str = "",
        max_summary_lines: int = 20,
    ) -> str:
        """Get a file summary or a delegation prompt for large files.

        Small files (≤50 lines) are returned directly with metadata.
        Large files (>50 lines) return a structured prompt to pass to delegate_task.

        Args:
            project: Project slug or '_meta'.
            section: Shortcut name. Ignored if path is set.
            path: Relative path to a .md file. Overrides section.
            max_summary_lines: Target summary length for delegation prompt.
        """
        result = _resolve_file(resolved_path, project, section, path, scopes)
        if isinstance(result, str):
            return _track("vault_summarize", result, project)
        filepath = result

        content = filepath.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        body = extract_body(content)
        line_count = len(content.splitlines())
        meta = _format_metadata(fm)

        if line_count <= _SUMMARIZE_THRESHOLD:
            header = f"**Metadata:** {meta}\n\n" if meta else ""
            return _track("vault_summarize", f"{header}{content}", project)

        return _track("vault_summarize",
                       _build_delegation_prompt(meta, body, line_count, max_summary_lines),
                       project)

    @mcp.tool
    def vault_smart_search(
        query: str,
        max_results: int = 10,
        max_lines: int = 100,
    ) -> str:
        """Ranked full-text search across the vault with frontmatter-aware scoring.

        Results are scored by match density, status weight, and recency.

        Args:
            query: Text to search for (case-insensitive).
            max_results: Maximum number of files to return. Default 10.
            max_lines: Maximum output lines. Default 100.
        """
        query_lower = query.lower()
        today = date.today()
        scored: list[tuple[float, str, str, list[str]]] = []

        for md_file in sorted(resolved_path.rglob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            lines = content.splitlines()
            matching = [ln.strip() for ln in lines if query_lower in ln.lower()]
            if not matching:
                continue

            fm = parse_frontmatter(content)
            score = _score_file(len(matching), fm, today)
            rel = md_file.relative_to(resolved_path).as_posix()
            meta = _format_metadata(fm)
            scored.append((score, rel, meta, matching))

        if not scored:
            return _track("vault_smart_search", f"No matches found for '{query}'.")

        scored.sort(key=lambda x: x[0], reverse=True)
        scored = scored[:max_results]

        results: list[str] = [f"# Smart Search: '{query}'", ""]
        for score, rel, meta, matching in scored:
            meta_part = f" [{meta}]" if meta else ""
            results.append(f"### {rel} (score: {score:.1f}){meta_part}")
            for line in matching[:5]:
                results.append(f"  - {line}")

        output = "\n".join(results)
        return _track("vault_smart_search", _truncate(output, max_lines))

    @mcp.tool
    def session_briefing(project: str) -> str:
        """One-call context briefing to start a new session.

        Assembles active tasks, recent lessons, git activity, and project
        health into a single response — replaces 3-4 manual tool calls.

        Args:
            project: Project slug (directory under 10_projects/).
        """
        resolved = _resolve_project_dir(resolved_path, project, scopes)
        if resolved is None:
            return _track("session_briefing",
                          f"Project '{project}' not found.", project)
        project_dir, _ = resolved

        # Decay stale relevance scores at session start
        relevance.apply_decay()

        # Build sections as keyed blocks
        sections: dict[str, str] = {}

        # Tasks
        task_result = _resolve_file(resolved_path, project, "tasks", "", scopes)
        if not isinstance(task_result, str):
            relevance.record_access(project, "tasks")
            body = _truncate(task_result.read_text(encoding="utf-8"), 50)
            sections["tasks"] = f"## Active Tasks\n{body}"

        # Lessons
        lessons_result = _resolve_file(
            resolved_path, project, "lessons", "", scopes,
        )
        if not isinstance(lessons_result, str):
            relevance.record_access(project, "lessons")
            lines = lessons_result.read_text(encoding="utf-8").splitlines()
            tail = lines[-30:] if len(lines) > 30 else lines
            sections["lessons"] = "## Recent Lessons\n" + "\n".join(tail)

        # Git activity (always shown, not ranked)
        git_block = "## Recent Vault Activity\n"
        git_block += _git_log(resolved_path, 5) or "(no git history available)"

        # Health (always shown, not ranked)
        md_files = list(project_dir.rglob("*.md"))
        stale_threshold = date.today() - timedelta(days=180)
        stale_count = 0
        for f in md_files:
            try:
                content = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm = parse_frontmatter(content)
            if fm is not None and fm.status in _TERMINAL_STATUSES:
                continue
            created_date = parse_date(fm.created) if fm is not None else None
            if created_date is None:
                created_date = date.fromtimestamp(f.stat().st_mtime)
            if created_date < stale_threshold:
                stale_count += 1
        health_lines = [f"- Files: {len(md_files)}"]
        if stale_count:
            health_lines.append(f"- Stale: {stale_count}")
        health_block = "## Project Health\n" + "\n".join(health_lines)

        # Order rankable sections by relevance (adaptive)
        default_order = ["tasks", "lessons"]
        scores = relevance.get_scores(project)
        if scores:
            ranked = sorted(
                sections.keys(), key=lambda s: scores.get(s, 0.0), reverse=True,
            )
        else:
            ranked = [s for s in default_order if s in sections]

        # Assemble output: header → ranked sections → fixed sections
        parts: list[str] = [f"# Session Briefing — {project}", ""]
        for key in ranked:
            parts.append(sections[key])
            parts.append("")
        parts.append(git_block)
        parts.append("")
        parts.append(health_block)

        return _track("session_briefing", "\n".join(parts), project)

    @mcp.tool
    def vault_recent(since_days: int = 7, project: str = "") -> str:
        """Show files changed in the vault in the last N days.

        Combines git history with frontmatter created dates for completeness.

        Args:
            since_days: Look back window in days. Default 7.
            project: Filter to this project only. Empty = all projects.
        """
        # Source 1: git-tracked changes
        git_paths = set(_git_recent(resolved_path, since_days))

        # Source 2: frontmatter created dates within window
        cutoff = date.today() - timedelta(days=since_days)
        for md_file in resolved_path.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm = parse_frontmatter(content)
            if fm is None:
                continue
            created = parse_date(fm.created)
            if created is not None and created >= cutoff:
                git_paths.add(md_file.relative_to(resolved_path).as_posix())

        # Filter to project if specified
        if project:
            resolved = _resolve_project_dir(resolved_path, project, scopes)
            if resolved is not None:
                prefix = resolved[0].relative_to(resolved_path).as_posix() + "/"
                git_paths = {p for p in git_paths if p.startswith(prefix)}
            else:
                git_paths = set()

        if not git_paths:
            return _track("vault_recent",
                          f"No changes found in the last {since_days} days.", project)

        lines: list[str] = [f"# Recent Changes (last {since_days} days)", ""]
        for rel_path in sorted(git_paths):
            full = resolved_path / rel_path
            if not full.exists():
                lines.append(f"- {rel_path} (deleted)")
                continue
            try:
                content = full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                lines.append(f"- {rel_path}")
                continue
            fm = parse_frontmatter(content)
            meta = _format_metadata(fm)
            if meta:
                lines.append(f"- {rel_path} [{meta}]")
            else:
                lines.append(f"- {rel_path}")

        output = "\n".join(lines)
        return _track("vault_recent", _truncate(output, 100), project)

    @mcp.tool
    def vault_usage(since_days: int = 30) -> str:
        """Show vault tool usage analytics.

        Reports call frequency, popular tools, popular projects, and total
        response lines served — useful for session profiling and benchmarking.

        Args:
            since_days: Look back window in days. Default 30.
        """
        stats = tracker.stats(since_days)
        if stats["total_calls"] == 0:
            return f"No vault tool calls recorded in the last {since_days} days."

        parts: list[str] = [f"# Vault Usage (last {since_days} days)", ""]
        parts.append(f"- Total calls: {stats['total_calls']}")
        parts.append(f"- Total response lines: {stats['total_response_lines']}")
        parts.append(
            f"- Estimated tokens served: ~{stats['total_response_lines'] * 10}"
        )
        parts.append("")

        if stats["by_tool"]:
            parts.append("## By Tool")
            for tool_name, count in stats["by_tool"].items():
                parts.append(f"- {tool_name}: {count} calls")
            parts.append("")

        if stats["by_project"]:
            parts.append("## By Project")
            for proj, count in stats["by_project"].items():
                parts.append(f"- {proj}: {count} calls")

        return "\n".join(parts)

    # ── Worker Tools ──────────────────────────────────────────────────

    def _record(resp: ClientResponse) -> None:
        """Record a successful response in the budget tracker."""
        budget.record_request(
            model=resp.model,
            cost_usd=resp.cost_usd,
            tokens=resp.tokens,
            latency_ms=resp.latency_ms,
            task_type="delegate",
        )

    async def _try_ollama(prompt: str, context: str, max_tokens: int) -> str:
        try:
            resp = await ollama.generate(prompt, context=context, max_tokens=max_tokens)
            _record(resp)
            return _format_response(resp)
        except (ConnectionError, RuntimeError) as exc:
            return f"Ollama error: {exc}. The host should handle this task directly."

    async def _try_openrouter_free(prompt: str, context: str, max_tokens: int) -> str:
        if openrouter is None:
            return "OpenRouter not configured. The host should handle this task directly."
        try:
            resp = await openrouter.generate(prompt, context=context, max_tokens=max_tokens)
            _record(resp)
            return _format_response(resp)
        except (ConnectionError, RuntimeError) as exc:
            return f"OpenRouter error: {exc}. The host should handle this task directly."

    async def _try_openrouter_paid(
        prompt: str, context: str, max_tokens: int, max_cost: float
    ) -> str:
        if openrouter is None:
            return "OpenRouter not configured. The host should handle this task directly."
        if not budget.can_spend(settings.openrouter_budget, max_cost):
            return "Monthly budget exhausted. The host should handle this task directly."
        try:
            resp = await openrouter.generate(
                prompt,
                context=context,
                model="deepseek/deepseek-chat-v3-0324:free",
                max_tokens=max_tokens,
            )
            _record(resp)
            return _format_response(resp)
        except (ConnectionError, RuntimeError) as exc:
            return f"OpenRouter paid error: {exc}. The host should handle this task directly."

    async def _try_openrouter_specific(
        prompt: str, context: str, max_tokens: int, model_id: str
    ) -> str:
        if openrouter is None:
            return "OpenRouter not configured. The host should handle this task directly."
        try:
            resp = await openrouter.generate(
                prompt, context=context, model=model_id, max_tokens=max_tokens
            )
            _record(resp)
            return _format_response(resp)
        except (ConnectionError, RuntimeError) as exc:
            msg = f"OpenRouter error ({model_id}): {exc}"
            return f"{msg}. The host should handle this task directly."

    @mcp.tool
    async def delegate_task(
        prompt: str,
        context: str = "",
        model: str = "auto",
        max_tokens: int = 2000,
        max_cost_per_request: float = 0.0,
    ) -> str:
        """Delegate a task to a cheaper model (Ollama or OpenRouter).

        Args:
            prompt: The task description or code to process.
            context: Optional system context for the model.
            model: Routing — 'auto', 'ollama', 'openrouter-free', or a model ID.
            max_tokens: Maximum tokens in the response.
            max_cost_per_request: Max USD to spend on this request. 0 = free models only.
        """
        if model == "ollama":
            return await _try_ollama(prompt, context, max_tokens)
        if model == "openrouter-free":
            return await _try_openrouter_free(prompt, context, max_tokens)
        if model == "openrouter":
            return await _try_openrouter_paid(prompt, context, max_tokens, max_cost_per_request)
        if model != "auto":
            return await _try_openrouter_specific(prompt, context, max_tokens, model)

        # Auto routing: Ollama → OpenRouter free → OpenRouter paid → reject
        errors: list[str] = []

        # Tier 1: Ollama
        if await ollama.is_available():
            try:
                resp = await ollama.generate(prompt, context=context, max_tokens=max_tokens)
                _record(resp)
                return _format_response(resp)
            except (ConnectionError, RuntimeError) as exc:
                errors.append(f"Ollama: {exc}")
        else:
            errors.append("Ollama: offline")

        # Tier 2: OpenRouter free
        if openrouter is not None:
            try:
                resp = await openrouter.generate(prompt, context=context, max_tokens=max_tokens)
                _record(resp)
                return _format_response(resp)
            except (ConnectionError, RuntimeError) as exc:
                errors.append(f"OpenRouter free: {exc}")
        else:
            errors.append("OpenRouter: no API key configured")

        # Tier 3: OpenRouter paid (only if max_cost > 0 and budget allows)
        if (
            max_cost_per_request > 0
            and openrouter is not None
            and budget.can_spend(settings.openrouter_budget, max_cost_per_request)
        ):
            try:
                resp = await openrouter.generate(
                    prompt,
                    context=context,
                    model="deepseek/deepseek-chat-v3-0324:free",
                    max_tokens=max_tokens,
                )
                _record(resp)
                return _format_response(resp)
            except (ConnectionError, RuntimeError) as exc:
                errors.append(f"OpenRouter paid: {exc}")

        # All tiers exhausted
        reasons = "; ".join(errors)
        return f"All workers unavailable. [{reasons}]. The host should handle this task directly."

    @mcp.tool
    async def list_models() -> str:
        """List available models across all providers."""
        lines = ["# Available Models", ""]

        # Ollama
        ollama_status = "online" if await ollama.is_available() else "offline / unavailable"
        lines.append(f"## Ollama ({ollama_status})")
        if "online" in ollama_status:
            lines.append(f"- **{ollama._model}** — local, free, no token limit")
        lines.append("")

        # OpenRouter
        lines.append("## OpenRouter")
        if openrouter is not None:
            try:
                models = await openrouter.list_models()
                for m in models:
                    cost_label = "free" if m.is_free else f"${m.cost_per_million_input:.2f}/M in"
                    lines.append(f"- **{m.id}** — {m.name}, ctx: {m.context_length}, {cost_label}")
            except (ConnectionError, RuntimeError) as exc:
                lines.append(f"- Error fetching models: {exc}")
        else:
            lines.append("- No API key configured")

        return "\n".join(lines)

    @mcp.tool
    async def worker_status() -> str:
        """Show worker health: budget, connectivity, usage stats."""
        stats = budget.month_stats(settings.openrouter_budget)
        ollama_up = await ollama.is_available()

        lines = [
            "# Worker Status",
            "",
            "## Budget",
            f"- Spent this month: ${stats['spent']:.2f}",
            f"- Remaining: ${stats['remaining']:.2f} / ${settings.openrouter_budget:.1f}",
            f"- Requests: {stats['request_count']}",
            "",
            "## Connectivity",
            f"- Ollama: {'online' if ollama_up else 'offline / unavailable'}",
            f"- OpenRouter: {'configured' if openrouter is not None else 'no API key'}",
            "",
        ]

        if stats["by_model"]:
            lines.append("## Top Models")
            for model_name, model_stats in stats["by_model"].items():
                lines.append(
                    f"- **{model_name}**: {model_stats['count']} requests, "
                    f"${model_stats['total_cost']:.4f}, avg {model_stats['avg_latency_ms']}ms"
                )

        return "\n".join(lines)

    mcp._usage_tracker = tracker  # type: ignore[attr-defined]

    return mcp


def _git_commit(vault_path: Path, rel_path: Path, message: str) -> None:
    """Stage a file and commit it in the vault git repo."""
    subprocess.run(
        ["git", "add", str(rel_path)],
        cwd=vault_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=vault_path,
        capture_output=True,
        check=True,
    )


def _git_log(vault_path: Path, n: int) -> str:
    """Return last n git log entries, or empty string on failure."""
    result = subprocess.run(
        ["git", "log", "--oneline", f"-{n}"],
        cwd=vault_path,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_recent(vault_path: Path, since_days: int) -> list[str]:
    """Return vault-relative .md paths changed in the last N days via git."""
    result = subprocess.run(
        ["git", "log", f"--since={since_days} days ago",
         "--name-only", "--pretty=format:"],
        cwd=vault_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return sorted({
        line.strip() for line in result.stdout.splitlines()
        if line.strip().endswith(".md")
    })


server = create_server()


def main() -> None:
    """Entry point for the hive CLI command."""
    server.run()


if __name__ == "__main__":
    main()
