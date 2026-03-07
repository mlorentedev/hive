"""Hive MCP Server — on-demand Obsidian vault access + worker delegation."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import date, timedelta
from typing import TYPE_CHECKING

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from pathlib import Path

    from hive.frontmatter import Frontmatter

from hive.budget import BudgetTracker
from hive.clients import ClientResponse, OllamaClient, OpenRouterClient
from hive.config import settings
from hive.frontmatter import (
    _TERMINAL_STATUSES,
    extract_body,
    parse_date,
    parse_frontmatter,
    validate_frontmatter,
)
from hive.relevance import RelevanceTracker
from hive.usage import UsageTracker

_log = logging.getLogger(__name__)

_VALID_OPERATIONS = {"append", "replace"}

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)

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

    Returns None if the project is not found or escapes the vault boundary.
    """
    scopes = scopes or _DEFAULT_SCOPES

    # _meta special case → meta scope root
    if project == "_meta":
        meta_dir_name = scopes.get("meta", "00_meta")
        d = vault / meta_dir_name
        if not d.is_dir():
            return None
        if _check_path_boundary(d, vault) is not None:
            return None
        return (d, "meta")

    explicit_scope, slug = _parse_project_ref(project)

    if explicit_scope is not None:
        dir_name = scopes.get(explicit_scope)
        if dir_name is None:
            return None
        d = vault / dir_name / slug
        if not d.is_dir():
            return None
        if _check_path_boundary(d, vault) is not None:
            return None
        return (d, explicit_scope)

    # Auto-scan: iterate scopes, first match wins, skip missing dirs
    for scope_name, dir_name in scopes.items():
        if scope_name == "meta":
            continue  # meta is not a project container
        scope_dir = vault / dir_name
        if not scope_dir.is_dir():
            continue
        d = scope_dir / slug
        if d.is_dir() and _check_path_boundary(d, vault) is None:
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


def _check_path_boundary(filepath: Path, boundary: Path) -> str | None:
    """Return an error string if filepath escapes boundary, else None."""
    try:
        filepath.resolve().relative_to(boundary.resolve())
    except ValueError:
        return "Path escapes vault boundary. Use a relative path within the project."
    return None


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
        boundary_error = _check_path_boundary(filepath, vault)
        if boundary_error:
            return boundary_error
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
        endpoint=settings.ollama_endpoint, model=settings.ollama_model,
        timeout=settings.http_timeout,
    )
    openrouter: OpenRouterClient | None = None
    if openrouter_client is not None:
        openrouter = openrouter_client
    elif settings.openrouter_api_key:
        openrouter = OpenRouterClient(
            api_key=settings.openrouter_api_key, default_model=settings.openrouter_model,
            timeout=settings.http_timeout,
        )
    relevance = relevance_tracker or RelevanceTracker(
        db_path=settings.relevance_db_path,
        alpha=settings.relevance_alpha,
        decay_factor=settings.relevance_decay,
        epsilon=settings.relevance_epsilon,
    )
    stale_days = settings.stale_threshold_days
    mcp = FastMCP(
        "Hive",
        instructions=(
            "Hive provides on-demand access to an Obsidian vault. "
            "Use vault tools (vault_query, vault_search, vault_patch, etc.) "
            "instead of direct filesystem access for files under the vault path. "
            "Read-only tools are safe to call freely. "
            "Write tools (vault_update, vault_create, vault_patch, capture_lesson) "
            "auto-commit to git. "
            "Use extract_lessons to batch-extract lessons from session text "
            "via a worker model — saves host tokens on bulk extraction."
        ),
    )

    def _track(
        tool: str, result: str, project: str = "", section: str = "",
    ) -> str:
        """Log a tool call and return the result unchanged."""
        tracker.log_call(tool, project, len(result.splitlines()))
        if project and section:
            is_write = tool in {"vault_update", "vault_create"}
            relevance.record_access(project, section, is_write=is_write)
        return result

    # ── Shared helpers ────────────────────────────────────────────────────

    def _list_projects_text() -> str:
        """Build project listing text (shared by resource and tool)."""
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

    def _count_stale(
        project_dir: Path, threshold: date,
    ) -> list[str]:
        """Return list of stale file paths in a project directory."""
        stale: list[str] = []
        for f in project_dir.rglob("*.md"):
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
            if created_date < threshold:
                stale.append(f.relative_to(project_dir).as_posix())
        return stale

    def _health_report_text() -> str:
        """Build health report text (shared by resource and tool)."""
        stale_threshold = date.today() - timedelta(days=stale_days)
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
                total_lines = sum(
                    len(f.read_text(encoding="utf-8").splitlines())
                    for f in md_files
                    if _safe_read(f) is not None
                )
                stale_files = _count_stale(project_dir, stale_threshold)
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
                        f"- Stale files (>{stale_days}d): "
                        f"{', '.join(sorted(stale_files))}"
                    )
                lines.append("")

        if not found_any:
            return "No projects found in vault."
        return "\n".join(lines)

    def _safe_read(f: Path) -> str | None:
        """Read file text, returning None on error."""
        try:
            return f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    # ── Resources ────────────────────────────────────────────────────────

    @mcp.resource("hive://projects")
    def projects_resource() -> str:
        """List all vault projects with file counts and available shortcuts."""
        return _list_projects_text()

    @mcp.resource("hive://health")
    def health_resource() -> str:
        """Vault health metrics for all projects."""
        return _health_report_text()

    @mcp.resource("hive://projects/{project}/context")
    def context_resource(project: str) -> str:
        """Project context document (00-context.md)."""
        result = _resolve_file(resolved_path, project, "context", "", scopes)
        if isinstance(result, str):
            return result
        content = _safe_read(result)
        return _truncate(content, 200) if content else "Error reading file."

    @mcp.resource("hive://projects/{project}/tasks")
    def tasks_resource(project: str) -> str:
        """Project task backlog (11-tasks.md)."""
        result = _resolve_file(resolved_path, project, "tasks", "", scopes)
        if isinstance(result, str):
            return result
        content = _safe_read(result)
        return _truncate(content, 200) if content else "Error reading file."

    @mcp.resource("hive://projects/{project}/lessons")
    def lessons_resource(project: str) -> str:
        """Project lessons learned (90-lessons.md)."""
        result = _resolve_file(resolved_path, project, "lessons", "", scopes)
        if isinstance(result, str):
            return result
        content = _safe_read(result)
        return _truncate(content, 200) if content else "Error reading file."

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

    @mcp.tool(annotations=_READ_ONLY)
    def vault_list_projects() -> str:
        """List all projects available in the Obsidian vault."""
        return _track("vault_list_projects", _list_projects_text())

    @mcp.tool(annotations=_READ_ONLY)
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

        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            return _track("vault_query",
                          f"File I/O error: {exc}", project, resolved_section)

        if include_metadata:
            fm = parse_frontmatter(content)
            meta = _format_metadata(fm)
            if meta:
                content = f"**Metadata:** {meta}\n\n{content}"

        return _track("vault_query", _truncate(content, max_lines),
                       project, resolved_section)

    @mcp.tool(annotations=_READ_ONLY)
    def vault_search(
        query: str,
        max_lines: int = 500,
        type_filter: str = "",
        status_filter: str = "",
        tag_filter: str = "",
        use_regex: bool = False,
    ) -> str:
        """Full-text search across all markdown files in the vault.

        Args:
            query: Text to search for (case-insensitive). Supports regex when use_regex=True.
            max_lines: Maximum output lines. Default 500.
            type_filter: Only include files whose frontmatter type matches (e.g. 'adr').
            status_filter: Only include files whose frontmatter status matches (e.g. 'active').
            tag_filter: Only include files that have this tag in their frontmatter tags list.
            use_regex: Treat query as a regular expression. Default False (literal match).
        """
        if use_regex:
            if len(query) > 200:
                return _track("vault_search",
                              "Regex pattern too long (max 200 chars).")
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error as exc:
                return _track("vault_search",
                              f"Invalid regex '{query}': {exc}")

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

            if use_regex:
                matching_lines = [
                    line.strip() for line in content.splitlines() if pattern.search(line)
                ]
            else:
                matching_lines = [
                    line.strip() for line in content.splitlines()
                    if query_lower in line.lower()
                ]
            if matching_lines:
                rel = md_file.relative_to(resolved_path)
                meta_str = _format_metadata(fm)
                meta = f" [{meta_str}]" if meta_str else ""
                results.append(f"### {rel}{meta}")
                for line in matching_lines[:5]:
                    results.append(f"  - {line}")

        if not results:
            return _track("vault_search", f"No matches found for '{query}'.")

        output = f"# Search: '{query}'\n\n" + "\n".join(results)
        return _track("vault_search", _truncate(output, max_lines))

    @mcp.tool(annotations=_READ_ONLY)
    def vault_health() -> str:
        """Return health metrics for all vault projects."""
        return _track("vault_health", _health_report_text())

    @mcp.tool(annotations=_WRITE)
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

        try:
            if operation == "append":
                existing = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
                filepath.write_text(existing + content, encoding="utf-8")
            else:
                filepath.write_text(content, encoding="utf-8")
        except OSError as exc:
            return _track("vault_update",
                          f"File I/O error: {exc}", project)

        rel = filepath.relative_to(resolved_path)
        _git_commit(resolved_path, rel, f"vault: update {project}/{section}")

        return _track("vault_update",
                       f"Updated {project}/{section} ({operation}).",
                       project, section)

    @mcp.tool(annotations=_WRITE)
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
        boundary_error = _check_path_boundary(filepath, resolved_path)
        if boundary_error:
            return _track("vault_create", boundary_error, project)
        if filepath.exists():
            return _track("vault_create",
                          f"File already exists: {path}. Use vault_update to modify it.",
                          project)

        # Auto-generate frontmatter (sanitize to prevent YAML injection)
        safe_stem = re.sub(r"[^\w\-.]", "_", filepath.stem)
        safe_type = re.sub(r"[^\w\-.]", "_", doc_type)
        frontmatter = (
            f"---\n"
            f"id: {safe_stem}\n"
            f"type: {safe_type}\n"
            f"status: active\n"
            f'created: "{date.today().isoformat()}"\n'
            f"---\n\n"
        )

        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(frontmatter + content, encoding="utf-8")
        except OSError as exc:
            return _track("vault_create",
                          f"File I/O error: {exc}", project)

        rel = filepath.relative_to(resolved_path)
        display_project = "00_meta" if project == "_meta" else project
        _git_commit(resolved_path, rel, f"vault: create {display_project}/{path}")

        return _track("vault_create",
                       f"Created {project}/{path} (type: {doc_type}).",
                       project, path)

    @mcp.tool(annotations=_READ_ONLY)
    def vault_list_files(
        project: str,
        path: str = "",
        pattern: str = "",
    ) -> str:
        """List files and directories in a vault project.

        Args:
            project: Project slug or '_meta' for cross-project content.
            path: Subdirectory to list (relative to project root). Empty = project root.
            pattern: Glob pattern to filter files (e.g. 'adr-*', '*.md'). Empty = all.
        """
        resolved = _resolve_project_dir(resolved_path, project, scopes)
        if resolved is None:
            return _track("vault_list_files",
                          f"Project '{project}' not found in vault.", project)
        project_dir, _ = resolved

        target = project_dir / path if path else project_dir
        boundary_error = _check_path_boundary(target, resolved_path)
        if boundary_error:
            return _track("vault_list_files", boundary_error, project)
        if not target.is_dir():
            return _track("vault_list_files",
                          f"Path '{path}' not found in project '{project}'.", project)

        lines: list[str] = [f"# Files: {project}/{path}" if path else f"# Files: {project}", ""]

        max_list_results = 500
        if pattern:
            # Recursive glob for pattern matching
            files = sorted(f for f in target.rglob(pattern) if f.is_file())
            for f in files[:max_list_results]:
                rel_f = f.relative_to(target)
                lines.append(f"- {rel_f}")
            if len(lines) == 2:
                return _track("vault_list_files",
                              f"No files matching '{pattern}' in {project}/{path}.",
                              project)
        else:
            # List directories first, then files
            dirs = sorted(d for d in target.iterdir() if d.is_dir())
            for d in dirs:
                lines.append(f"- {d.name}/")
            files = sorted(f for f in target.iterdir() if f.is_file())
            for f in files:
                lines.append(f"- {f.name}")

        return _track("vault_list_files", "\n".join(lines), project, path)

    @mcp.tool(annotations=_WRITE)
    def vault_patch(
        project: str,
        path: str,
        old_text: str = "",
        new_text: str = "",
        patches: list[dict[str, str]] = [],  # noqa: B006
    ) -> str:
        """Surgical text replacement in a vault file with auto git commit.

        Supports single or multi-replacement. For single replacement, provide
        old_text and new_text. For multiple replacements, provide patches — a list
        of {old_text, new_text} dicts applied in sequence. Do not mix both modes.

        Each old_text must appear exactly once in the file (after prior patches in
        the list have been applied). If any patch fails validation, no changes are
        written.

        Args:
            project: Project slug or '_meta' for cross-project content.
            path: Relative path to the file within the project.
            old_text: Exact text to find and replace (single mode). Empty = not set.
            new_text: Replacement text (single mode). Empty = not set.
            patches: List of {old_text, new_text} dicts (multi mode).
        """
        has_single = bool(old_text) or bool(new_text)
        has_multi = len(patches) > 0

        if has_single and has_multi:
            return _track(
                "vault_patch",
                "Cannot mix old_text/new_text with patches. "
                "Use one mode or the other.",
                project,
            )

        if has_single:
            if not old_text or not new_text:
                return _track(
                    "vault_patch",
                    "Provide both old_text and new_text for single replacement.",
                    project,
                )
            patch_list: list[dict[str, str]] = [
                {"old_text": old_text, "new_text": new_text},
            ]
        elif has_multi:
            patch_list = patches
        else:
            return _track(
                "vault_patch",
                "Provide old_text/new_text or a patches list.",
                project,
            )

        resolved = _resolve_project_dir(resolved_path, project, scopes)
        if resolved is None:
            return _track("vault_patch",
                          f"Project '{project}' not found in vault.", project)
        project_dir, _ = resolved

        filepath = project_dir / path
        boundary_error = _check_path_boundary(filepath, resolved_path)
        if boundary_error:
            return _track("vault_patch", boundary_error, project)
        if not filepath.exists():
            return _track("vault_patch",
                          f"File '{path}' not found in project '{project}'.",
                          project)

        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            return _track("vault_patch",
                          f"File I/O error reading '{path}': {exc}", project)

        # Validate and apply all patches on a working copy first
        working = content
        for i, patch in enumerate(patch_list, 1):
            if "old_text" not in patch or "new_text" not in patch:
                label = f"patch {i}: " if len(patch_list) > 1 else ""
                return _track(
                    "vault_patch",
                    f"{label}Each patch must have 'old_text' and 'new_text' keys.",
                    project,
                )
            old = patch["old_text"]
            new = patch["new_text"]
            count = working.count(old)

            if count == 0:
                label = f"patch {i}: " if len(patch_list) > 1 else ""
                return _track(
                    "vault_patch",
                    f"{label}old_text not found in file '{path}'.",
                    project,
                )
            if count > 1:
                label = f"patch {i}: " if len(patch_list) > 1 else ""
                return _track(
                    "vault_patch",
                    f"{label}Ambiguous: old_text appears {count} times "
                    f"in '{path}'. "
                    "Provide more context to make the match unique.",
                    project,
                )
            working = working.replace(old, new, 1)

        try:
            filepath.write_text(working, encoding="utf-8")
        except OSError as exc:
            return _track("vault_patch",
                          f"File I/O error writing '{path}': {exc}", project)

        rel = filepath.relative_to(resolved_path)
        n = len(patch_list)
        _git_commit(resolved_path, rel, f"vault: patch {project}/{path}")

        noun = "patch" if n == 1 else "patches"
        return _track("vault_patch",
                       f"Applied {n} {noun} to {project}/{path}.",
                       project, path)

    def _write_lesson(
        project_dir: Path,
        project: str,
        title: str,
        context: str,
        problem: str,
        solution: str,
        tags: list[str],
    ) -> tuple[str, str]:
        """Write a single lesson to 90-lessons.md. Returns (status, message).

        Status is one of: 'written', 'skipped' (duplicate), 'error'.
        """
        lessons_file = project_dir / "90-lessons.md"

        existing = ""
        if lessons_file.exists():
            try:
                existing = lessons_file.read_text(encoding="utf-8")
            except OSError as exc:
                return "error", f"File I/O error: {exc}"

        if f"] {title}\n" in existing:
            return "skipped", f"Lesson already exists: '{title}'. Skipping."

        tag_str = " ".join(f"`#{t}`" for t in tags)
        entry = (
            f"\n### [{date.today().isoformat()}] {title}\n"
            f"**Context:** {context}\n"
            f"**Problem:** {problem}\n"
            f"**Solution:** {solution}\n"
        )
        if tag_str:
            entry += f"**Tags:** {tag_str}\n"

        try:
            if not lessons_file.exists():
                safe_project = re.sub(r"[^\w\-.]", "_", project)
                frontmatter = (
                    f"---\n"
                    f"id: {safe_project}-lessons\n"
                    f"type: lesson\n"
                    f"status: active\n"
                    f'created: "{date.today().isoformat()}"\n'
                    f"---\n\n"
                    f"# Lessons Learned\n"
                )
                lessons_file.write_text(frontmatter + entry, encoding="utf-8")
            else:
                with lessons_file.open("a", encoding="utf-8") as f:
                    f.write(entry)
        except OSError as exc:
            return "error", f"File I/O error: {exc}"

        return "written", f"Lesson captured: '{title}' → {project}/90-lessons.md"

    @mcp.tool(annotations=_WRITE)
    def capture_lesson(
        project: str,
        title: str,
        context: str,
        problem: str,
        solution: str,
        tags: list[str] = [],  # noqa: B006
    ) -> str:
        """Capture a lesson learned inline during a session.

        Appends a structured lesson to the project's 90-lessons.md file.
        Deduplicates by title to avoid recording the same lesson twice.

        Args:
            project: Project slug (directory under 10_projects/).
            title: Short descriptive title for the lesson.
            context: What you were doing when this came up.
            problem: What went wrong or what decision was needed.
            solution: What fixed it or what was decided.
            tags: Optional list of tags (e.g. ["python", "testing"]).
        """
        resolved = _resolve_project_dir(resolved_path, project, scopes)
        if resolved is None:
            return _track("capture_lesson",
                          f"Project '{project}' not found in vault.", project)
        project_dir, _ = resolved

        status, msg = _write_lesson(project_dir, project, title, context, problem, solution, tags)
        if status == "error":
            return _track("capture_lesson", msg, project)
        if status == "skipped":
            return _track("capture_lesson", msg, project, "lessons")

        rel = (project_dir / "90-lessons.md").relative_to(resolved_path)
        _git_commit(resolved_path, rel, f"vault: capture_lesson {project} — {title}")

        return _track("capture_lesson", msg, project, "lessons")

    def _parse_lessons_json(raw: str) -> list[dict[str, object]]:
        """Parse JSON from worker response, stripping markdown fences."""
        text = raw.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first line (```json) and last line (```)
            inner = "\n".join(
                ln for ln in lines[1:] if not ln.strip().startswith("```")
            )
            text = inner.strip()
        # Fallback: extract first [...] block
        if not text.startswith("["):
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                text = match.group(0)
        return json.loads(text)  # type: ignore[no-any-return]

    extract_prompt = (
        "Extract key lessons from the following text. A lesson is a decision, "
        "bug root cause, or pattern choice worth remembering.\n\n"
        "For each lesson, provide a JSON object with:\n"
        '- "title": Short descriptive title (max 10 words)\n'
        '- "context": What was being done (1 sentence)\n'
        '- "problem": What went wrong or what decision was needed (1 sentence)\n'
        '- "solution": What fixed it or what was decided (1 sentence)\n'
        '- "tags": List of 1-3 relevant tags (lowercase, no #)\n'
        '- "confidence": 0.0-1.0 how confident this is a real, reusable lesson\n\n'
        "Return ONLY a JSON array. No markdown, no explanation. "
        "Max {max_lessons} lessons. "
        "Only include lessons with confidence > {min_confidence}.\n"
        "If no lessons found, return: []\n\n"
        "Text:\n---\n{text}\n---"
    )

    max_extract_input = 8000  # chars, safe for any worker model

    @mcp.tool(annotations=_WRITE)
    async def extract_lessons(
        project: str,
        text: str,
        min_confidence: float = 0.7,
        max_lessons: int = 5,
    ) -> str:
        """Extract lessons from text using a worker model and write to vault.

        Sends text to a cheaper model (Ollama/OpenRouter) which extracts
        structured lessons, then writes them to the project's 90-lessons.md.

        Args:
            project: Project slug (directory under 10_projects/).
            text: Raw text to extract lessons from (session notes, debug logs, etc.).
            min_confidence: Minimum confidence threshold (0.0-1.0). Default 0.7.
            max_lessons: Maximum lessons to extract. Default 5.
        """
        resolved = _resolve_project_dir(resolved_path, project, scopes)
        if resolved is None:
            return _track("extract_lessons",
                          f"Project '{project}' not found in vault.", project)
        project_dir, _ = resolved

        # Truncate input to safe length
        truncated = text[:max_extract_input]
        prompt = extract_prompt.format(
            max_lessons=max_lessons,
            min_confidence=min_confidence,
            text=truncated,
        )

        # Send to worker via auto-routing
        errors: list[str] = []

        if await ollama.is_available():
            try:
                resp = await ollama.generate(prompt, max_tokens=2000)
            except (ConnectionError, RuntimeError) as exc:
                errors.append(f"Ollama: {exc}")
                resp = None
            else:
                pass  # resp is set
        else:
            errors.append("Ollama: offline")
            resp = None

        if resp is None and openrouter is not None:
            try:
                resp = await openrouter.generate(prompt, max_tokens=2000)
            except (ConnectionError, RuntimeError) as exc:
                errors.append(f"OpenRouter: {exc}")

        if resp is None:
            reasons = "; ".join(errors) if errors else "no workers configured"
            return _track("extract_lessons",
                          f"All workers unavailable [{reasons}]. "
                          "Cannot extract lessons without a worker model.",
                          project)

        # Parse worker response
        try:
            lessons_raw = _parse_lessons_json(resp.text)
        except (json.JSONDecodeError, ValueError):
            snippet = resp.text[:200]
            return _track("extract_lessons",
                          f"Could not parse worker response as JSON: {snippet}",
                          project)

        if not isinstance(lessons_raw, list):
            return _track("extract_lessons",
                          "Worker returned non-array JSON.", project)

        if not lessons_raw:
            return _track("extract_lessons", "No lessons found in text.", project)

        # Filter, validate, and write
        written: list[str] = []
        skipped: list[str] = []
        for lesson in lessons_raw[:max_lessons]:
            if not isinstance(lesson, dict):
                continue
            title = str(lesson.get("title", "")).strip()
            if not title:
                continue
            try:
                confidence = float(str(lesson.get("confidence", 0.5)))
            except (ValueError, TypeError):
                confidence = 0.5
            if confidence < min_confidence:
                skipped.append(f"{title} (confidence {confidence:.1f})")
                continue

            l_context = str(lesson.get("context", ""))
            l_problem = str(lesson.get("problem", ""))
            l_solution = str(lesson.get("solution", ""))
            raw_tags = lesson.get("tags", [])
            l_tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []

            status, msg = _write_lesson(
                project_dir, project, title, l_context, l_problem, l_solution, l_tags,
            )
            if status == "written":
                written.append(title)
            elif status == "skipped":
                skipped.append(f"{title} (duplicate)")
            # errors are silently skipped for individual lessons

        # Single git commit for all lessons
        if written:
            rel = (project_dir / "90-lessons.md").relative_to(resolved_path)
            _git_commit(resolved_path, rel,
                        f"vault: extract_lessons {project} — {len(written)} lessons")

        # Build summary
        parts: list[str] = []
        if written:
            titles = ", ".join(written)
            parts.append(f"Extracted {len(written)} lessons: {titles}")
        if skipped:
            skip_details = ", ".join(skipped)
            parts.append(f"Skipped {len(skipped)}: {skip_details}")
        if not written and not skipped:
            parts.append("No lessons found in text.")

        summary = ". ".join(parts) + "."
        return _track("extract_lessons", summary, project, "lessons")

    @mcp.tool(annotations=_READ_ONLY)
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

        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            return _track("vault_summarize",
                          f"File I/O error: {exc}", project)
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

    @mcp.tool(annotations=_READ_ONLY)
    def vault_smart_search(
        query: str,
        max_results: int = 10,
        max_lines: int = 500,
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

    @mcp.tool(annotations=_READ_ONLY)
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
            task_content = _safe_read(task_result)
            if task_content is not None:
                relevance.record_access(project, "tasks")
                body = _truncate(task_content, 50)
                sections["tasks"] = f"## Active Tasks\n{body}"

        # Lessons
        lessons_result = _resolve_file(
            resolved_path, project, "lessons", "", scopes,
        )
        if not isinstance(lessons_result, str):
            lessons_content = _safe_read(lessons_result)
            if lessons_content is not None:
                relevance.record_access(project, "lessons")
                lines = lessons_content.splitlines()
                tail = lines[-30:] if len(lines) > 30 else lines
                sections["lessons"] = "## Recent Lessons\n" + "\n".join(tail)

        # Git activity (always shown, not ranked)
        git_block = "## Recent Vault Activity\n"
        git_block += _git_log(resolved_path, 5) or "(no git history available)"

        # Health (always shown, not ranked)
        md_files = list(project_dir.rglob("*.md"))
        stale_threshold = date.today() - timedelta(days=stale_days)
        stale_count = len(_count_stale(project_dir, stale_threshold))
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

    @mcp.tool(annotations=_READ_ONLY)
    def vault_recent(since_days: int = 7, project: str = "", max_lines: int = 100) -> str:
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
        return _track("vault_recent", _truncate(output, max_lines), project)

    @mcp.tool(annotations=_READ_ONLY)
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
                model=settings.openrouter_paid_model,
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

    @mcp.tool(annotations=_WRITE)
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
                    model=settings.openrouter_paid_model,
                    max_tokens=max_tokens,
                )
                _record(resp)
                return _format_response(resp)
            except (ConnectionError, RuntimeError) as exc:
                errors.append(f"OpenRouter paid: {exc}")

        # All tiers exhausted
        reasons = "; ".join(errors)
        return f"All workers unavailable. [{reasons}]. The host should handle this task directly."

    @mcp.tool(annotations=_READ_ONLY)
    async def list_models() -> str:
        """List available models across all providers."""
        lines = ["# Available Models", ""]

        # Ollama
        ollama_status = "online" if await ollama.is_available() else "offline / unavailable"
        lines.append(f"## Ollama ({ollama_status})")
        if "online" in ollama_status:
            lines.append(f"- **{ollama.model}** — local, free, no token limit")
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

    @mcp.tool(annotations=_READ_ONLY)
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
    """Stage a file and commit it in the vault git repo.

    This is a best-effort side-effect: failures are logged but never
    propagated, so a git problem cannot crash the MCP server or prevent
    the tool response from reaching the client.
    """
    safe_msg = message.replace("\n", " ").replace("\r", " ")
    try:
        subprocess.run(
            ["git", "add", str(rel_path)],
            cwd=vault_path,
            capture_output=True,
            check=True,
            timeout=30,
        )
        subprocess.run(
            ["git", "commit", "-m", safe_msg],
            cwd=vault_path,
            capture_output=True,
            check=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        _log.warning("git commit failed for %s: %s", rel_path, exc)
    except subprocess.TimeoutExpired as exc:
        _log.warning("git commit timed out for %s: %s", rel_path, exc)
    except Exception as exc:
        _log.warning("git commit unexpected error for %s: %s", rel_path, exc)


def _git_log(vault_path: Path, n: int) -> str:
    """Return last n git log entries, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{n}"],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_recent(vault_path: Path, since_days: int) -> list[str]:
    """Return vault-relative .md paths changed in the last N days via git."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since_days} days ago",
             "--name-only", "--pretty=format:"],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return []
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
