"""Hive MCP Server — on-demand Obsidian vault access + worker delegation."""

from __future__ import annotations

import logging
import logging.handlers
from typing import TYPE_CHECKING

from fastmcp import FastMCP

if TYPE_CHECKING:
    from pathlib import Path

from hive._context import ServerContext
from hive._helpers import (
    _resolve_file,
    _safe_read,
    _truncate,
)
from hive._vault_health import health_report_text, register_vault_health
from hive._vault_read import list_projects_text, register_vault_read
from hive._vault_write import register_vault_write
from hive._workers import register_workers
from hive.budget import BudgetTracker
from hive.clients import OllamaClient, OpenRouterClient
from hive.config import settings
from hive.relevance import RelevanceTracker
from hive.usage import UsageTracker


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

    ctx = ServerContext(
        vault=resolved_path,
        scopes=scopes,
        tracker=tracker,
        budget=budget,
        ollama=ollama,
        openrouter=openrouter,
        relevance=relevance,
        stale_days=settings.stale_threshold_days,
        openrouter_budget=settings.openrouter_budget,
        openrouter_paid_model=settings.openrouter_paid_model,
    )

    mcp = FastMCP(
        "Hive",
        instructions=(
            "Hive provides on-demand access to an Obsidian vault.\n\n"
            "## When to use each tool\n\n"
            "- **Start of session:** Call `session_briefing(project)` to load "
            "tasks, lessons, git activity, and health in one call.\n"
            "- **Browsing vault structure:** Use `vault_list` to discover "
            "projects and navigate directories. Call `vault_query` to read "
            "their content.\n"
            "- **Reading vault files:** Use `vault_query` instead of direct "
            "filesystem access. Supports section shortcuts (context, tasks, "
            "roadmap, lessons) and arbitrary paths. Use `scope:project` "
            "syntax when project names are ambiguous across scopes.\n"
            "- **Finding information:** Use `vault_search` for keyword/regex "
            "lookup with filters. Add `ranked=True` for relevance-ranked "
            "results, or `since_days=N` for recent changes.\n"
            "- **Recording lessons:** Call `capture_lesson` when a bug fix, "
            "architectural decision, or useful insight emerges during work. "
            "Use `capture_lesson(text=...)` for bulk extraction from large "
            "text blocks.\n"
            "- **Writing to vault:** Use `vault_write` to create, append, or "
            "replace files. Use `vault_patch` for surgical find-and-replace "
            "edits.\n"
            "- **Offloading work:** Use `delegate_task` for summarization, "
            "boilerplate generation, or analysis. Use "
            "`delegate_task(project=...)` to summarize vault files. Best for "
            "boilerplate and format conversion, not for architecture or "
            "security decisions.\n"
            "- **Checking workers:** Call `worker_status` before "
            "`delegate_task` to verify budget and model availability.\n"
            "- **Checking vault health:** Call `vault_health` after modifying "
            "vault files or periodically to detect drift. Add "
            "`include_usage=True` for usage statistics.\n\n"
            "Use `project='_meta'` to access cross-project content "
            "(patterns, templates).\n"
            "Read-only tools are safe to call freely. "
            "Write tools auto-commit to git."
        ),
    )

    # ── Resources ────────────────────────────────────────────────────────

    @mcp.resource("hive://projects")
    def projects_resource() -> str:
        """List all vault projects with file counts and available shortcuts."""
        return list_projects_text(ctx)

    @mcp.resource("hive://health")
    def health_resource() -> str:
        """Vault health metrics for all projects."""
        return health_report_text(ctx)

    @mcp.resource("hive://projects/{project}/context")
    def context_resource(project: str) -> str:
        """Project context document (00-context.md)."""
        result = _resolve_file(ctx.vault, project, "context", "", ctx.scopes)
        if isinstance(result, str):
            return result
        content = _safe_read(result)
        return _truncate(content, 200) if content else "Error reading file."

    @mcp.resource("hive://projects/{project}/tasks")
    def tasks_resource(project: str) -> str:
        """Project task backlog (11-tasks.md)."""
        result = _resolve_file(ctx.vault, project, "tasks", "", ctx.scopes)
        if isinstance(result, str):
            return result
        content = _safe_read(result)
        return _truncate(content, 200) if content else "Error reading file."

    @mcp.resource("hive://projects/{project}/lessons")
    def lessons_resource(project: str) -> str:
        """Project lessons learned (90-lessons.md)."""
        result = _resolve_file(ctx.vault, project, "lessons", "", ctx.scopes)
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
- `vault_write(project="{project}", section="lessons", operation="append", content=<lessons>)`
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
- `delegate_task(project=<slug>, path=<file>)` to summarize relevant vault files
- Strip the task to its essential instruction — remove conversational context
- Keep prompt under 2000 tokens

### Step 4 — Delegate
- `worker_status()` to see available models and pick the appropriate tier
- `delegate_task(prompt=<compressed task>)`
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
- Context/tasks: `vault_write(operation="replace", ...)` for factual updates
- Lessons: `vault_write(operation="append", ...)` for new entries only
- Never delete vault content without explicit user request

### Step 6 — Verify
- `vault_query` the updated sections to confirm changes applied correctly
- Report what was updated

## Rules

- Always confirm before writing — show the plan first
- Use `replace` for context and tasks (factual state)
- Use `append` for lessons (never modify existing)
- All content in English
- One vault_write call per section to minimize git commits"""

    @mcp.prompt
    def benchmark() -> str:
        """Estimate token savings from hive MCP tools in the current session."""
        return """\
# Session Token Savings Benchmark

## Protocol

### Step 1 — Inventory Tool Usage
Scan this conversation for all hive MCP tool calls:
- `vault_query` / `vault_search` calls
- `delegate_task` / `worker_status` calls
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

    # ── Register tools from domain modules ──────────────────────────────

    register_vault_read(mcp, ctx)
    register_vault_write(mcp, ctx)
    register_vault_health(mcp, ctx)
    register_workers(mcp, ctx)

    mcp._usage_tracker = tracker  # type: ignore[attr-defined]
    mcp._hive_ctx = ctx  # type: ignore[attr-defined]

    if not resolved_path.is_dir():
        _log = logging.getLogger("hive")
        _log.warning(
            "Vault path does not exist: %s — vault tools will return "
            "an error until VAULT_PATH is configured correctly.",
            resolved_path,
        )

    return mcp


def _setup_file_logging() -> None:
    """Configure persistent file logging for post-mortem debugging."""
    from pathlib import Path as _Path

    log_file = _Path(settings.log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=1, encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"),
    )
    logging.getLogger("hive").addHandler(handler)
    logging.getLogger("hive").setLevel(logging.WARNING)


def main() -> None:
    """Entry point for the hive CLI command."""
    _setup_file_logging()
    server.run()


server = create_server()

if __name__ == "__main__":
    main()
