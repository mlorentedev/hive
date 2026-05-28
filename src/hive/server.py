"""Hive MCP Server — on-demand Obsidian vault access + worker delegation."""

from __future__ import annotations

import logging
import logging.handlers
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from hive import _compat as _hive_compat

_hive_compat.apply()

from fastmcp import FastMCP  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path

from hive._context import ServerContext  # noqa: E402
from hive._diagnostics import LifecycleMiddleware  # noqa: E402
from hive._helpers import (  # noqa: E402
    _resolve_file,
    _safe_read,
    _truncate,
    register_lock_eviction_tracker,
)
from hive._lesson_reinforcement import LessonReinforcementTracker  # noqa: E402
from hive._lock_eviction import LockEvictionTracker  # noqa: E402
from hive._vault_health import health_report_text, register_vault_health  # noqa: E402
from hive._vault_read import list_projects_text, register_vault_read  # noqa: E402
from hive._vault_write import register_vault_write  # noqa: E402
from hive._workers import register_workers  # noqa: E402
from hive.budget import BudgetTracker  # noqa: E402
from hive.clients import OllamaClient, OpenRouterClient  # noqa: E402
from hive.config import settings  # noqa: E402
from hive.relevance import RelevanceTracker  # noqa: E402
from hive.usage import UsageTracker  # noqa: E402


def create_server(
    vault_path: Path | None = None,
    usage_tracker: UsageTracker | None = None,
    budget_tracker: BudgetTracker | None = None,
    ollama_client: OllamaClient | None = None,
    openrouter_client: OpenRouterClient | None = None,
    vault_scopes: dict[str, str] | None = None,
    relevance_tracker: RelevanceTracker | None = None,
    lesson_tracker: LessonReinforcementTracker | None = None,
    lock_eviction_tracker: LockEvictionTracker | None = None,
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
    lessons = lesson_tracker or LessonReinforcementTracker(
        db_path=settings.lesson_db_path,
    )
    from pathlib import Path as _Path

    lock_eviction = lock_eviction_tracker or LockEvictionTracker(
        db_path=str(
            _Path(settings.db_path).parent / "lock_evictions.db",
        ),
    )

    ctx = ServerContext(
        vault=resolved_path,
        scopes=scopes,
        tracker=tracker,
        budget=budget,
        ollama=ollama,
        openrouter=openrouter,
        relevance=relevance,
        lessons=lessons,
        lock_eviction=lock_eviction,
        stale_days=settings.stale_threshold_days,
        openrouter_budget=settings.openrouter_budget,
        openrouter_paid_model=settings.openrouter_paid_model,
        tool_timeout=settings.tool_timeout,
        started_at_iso=datetime.now(UTC).isoformat(timespec="seconds"),
        started_at_monotonic=time.monotonic(),
    )
    # HIVE-116 PR-2: register tracker as the process-global supervisor
    # singleton so ``tool_span`` / ``bounded_call`` can persist eviction
    # events without taking a ServerContext arg (would create a cycle).
    register_lock_eviction_tracker(lock_eviction)

    mcp = FastMCP(
        "Hive",
        middleware=[LifecycleMiddleware()],
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
            "Default is **inline mode** — pass `title`, `context`, `problem`, "
            "and `solution`. Pass `text=...` for **batch mode** when you "
            "want a worker model to extract multiple lessons from a free-form "
            "text block.\n"
            "- **Writing to vault:** Use `vault_write` to create, append, or "
            "replace files. Use `vault_patch` for surgical find-and-replace "
            "edits. Pass `commit=False` to defer the git commit; flush a "
            "batch of deferred writes with `vault_commit(message=...)`. "
            "When obsidian-git is configured on the vault, `commit=False` "
            "is safe — `vault_health` surfaces the detected interval.\n"
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
- Context/tasks: `vault_write(operation="replace", commit=False, ...)` for factual updates
- Lessons: `vault_write(operation="append", commit=False, ...)` for new entries only
- After all updates, flush with a single `vault_commit(message="vault-sync: <project> <date>")`
- Never delete vault content without explicit user request

### Step 6 — Verify
- `vault_query` the updated sections to confirm changes applied correctly
- Report what was updated and the commit SHA returned by `vault_commit`

## Rules

- Always confirm before writing — show the plan first
- Use `replace` for context and tasks (factual state)
- Use `append` for lessons (never modify existing)
- All content in English
- Batch with `commit=False` plus one `vault_commit` flush at the end —
  avoids N round-trips through `git add` / `git commit` and keeps the
  vault history tidy"""

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
    """Configure persistent file logging for post-mortem debugging.

    Captures the ``hive``, ``fastmcp`` and ``mcp`` loggers so that
    lifecycle events, cancellations, and transport-level issues are
    visible after the fact. Level is controlled by ``HIVE_LOG_LEVEL``
    (default ``INFO``).

    Each hive subprocess writes to its own ``hive-{pid}.log`` so the
    rotation race that ``RotatingFileHandler`` cannot survive under
    multiple concurrent writers cannot happen. The configured
    ``settings.log_path`` is reused as a *template*: its parent
    directory is taken; the stem and suffix are kept; the PID is
    appended before the suffix.
    """
    import os
    from pathlib import Path as _Path

    template = _Path(settings.log_path)
    template.parent.mkdir(parents=True, exist_ok=True)
    log_file = template.with_name(f"{template.stem}-{os.getpid()}{template.suffix}")
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=1, encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"),
    )
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    for name in ("hive", "fastmcp", "mcp"):
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        logger.setLevel(level)


def main() -> None:
    """Entry point for the hive CLI command.

    ``create_server()`` is called here rather than at module import
    so importing ``hive.server`` (e.g. from tests, from typing tools,
    or from a future ``hive serve --http`` entry point) is side-effect
    free. The previous import-time instantiation cost every ``uvx
    hive-vault`` spawn ~300-600 ms before main() even ran.
    """
    _setup_file_logging()
    _log = logging.getLogger("hive")
    server = create_server()
    try:
        server.run()
    except BaseException as exc:
        _log.critical("hive server exiting: %r", exc, exc_info=True)
        raise


if __name__ == "__main__":
    main()
