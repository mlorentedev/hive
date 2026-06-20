"""Shared helpers — path resolution, formatting, git ops, tracking."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import difflib
import functools
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import filelock
from mcp.types import ToolAnnotations

from hive.frontmatter import extract_body, parse_date, parse_frontmatter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from pathlib import Path

    from hive._context import ServerContext
    from hive._lock_eviction import LockEvictionTracker
    from hive.clients import ClientResponse
    from hive.frontmatter import Frontmatter

_log = logging.getLogger(__name__)

_REJECT_MSG = "The host should handle this task directly."

# HIVE-116 AC-3: named return code for Popens terminated by the supervisor.
# Replaces the bare ``-1`` used at multiple sites; gives operators + the
# partial-state hook (HIVE-116 AC-6) a one-grep predicate.
RC_EXTERNAL_TERMINATION: int = -1


def _subprocess_run_kwargs() -> dict[str, int]:
    """Platform-specific kwargs for read-path ``subprocess.run`` calls.

    On Windows, adds ``CREATE_NO_WINDOW`` to suppress the console window
    flash on git invocations (hive issue #249).  POSIX returns an empty
    dict — no creation flags needed.
    """
    import sys as _sys

    if _sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def synthesize_external_termination_stderr(original_stderr: bytes) -> str:
    """Format the synthetic stderr returned by :func:`_run_git` on external kill.

    HIVE-116 AC-3. Shape:

    ``"[external_termination] killed by supervisor at <ISO-8601>; original
    stderr: (empty|<N> bytes)"``

    The ISO-8601 timestamp is UTC with offset suffix (`+00:00`). Callers
    that need to grep across worker logs use the literal prefix
    ``[external_termination]`` — the rest of the line is informational.
    """
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    n = len(original_stderr)
    detail = "empty" if n == 0 else f"{n} bytes"
    return f"[external_termination] killed by supervisor at {ts} ; original stderr: {detail}"


# HIVE-116 AC-5/AC-6: contextvar threading partial-state signals from the
# write tool to the deadline supervisor. Set by ``run_sync_tool`` for the
# write tools (``vault_write`` / ``vault_patch``) so:
#   - the write handler can mark "disk write completed" after the file is
#     persisted but before ``_git_commit`` returns;
#   - ``_git_commit`` can mark "deadline killed" when it sees an externally-
#     terminated Popen;
#   - on supervisor TimeoutError, ``run_sync_tool`` reads the dict to format
#     the partial-state response instead of the generic "timed out" string.
# The CV value is the *same dict reference* in the awaiting coroutine and
# the to_thread worker (CPython propagates the context).
_PARTIAL_STATE_CV: contextvars.ContextVar[dict[str, bool] | None] = contextvars.ContextVar(
    "_PARTIAL_STATE", default=None
)

# HIVE-116 AC-1: contextvar propagating the vault path that ``tool_span``
# should target for cooperative filelock eviction on deadline. Set by
# ``wrap_sync_tool`` (which has ``ctx.vault``) before the tool body runs;
# read by ``tool_span``'s except branch to call ``evict_filelock``. Kept
# as a CV instead of a positional arg so the wide call surface of
# ``tool_span`` does not need to change.
_VAULT_FOR_EVICTION_CV: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "_VAULT_FOR_EVICTION", default=None
)

# Tools that opt in to partial-state response formatting. Read-path tools
# (``vault_query``, ``vault_search``, ``vault_health``) keep the generic
# timeout response since they have no disk-write side-effect.
_PARTIAL_STATE_TOOLS: frozenset[str] = frozenset(
    {"vault_write", "vault_patch"},
)

# HIVE-211 PR5: event loop captured by wrap_sync_tool's async wrapper and
# propagated into the thread via contextvars. Lets sync write tools schedule
# async hooks (e.g. index update) without converting the tools to async.
_RUNNING_LOOP_CV: contextvars.ContextVar[asyncio.AbstractEventLoop | None] = contextvars.ContextVar(
    "_RUNNING_LOOP", default=None
)


def schedule_async_hook(hook: object, filepath: Path) -> None:
    """Submit an async hook to the event loop from a sync tool running in a thread.

    Uses ``asyncio.run_coroutine_threadsafe`` with the loop captured in
    :data:`_RUNNING_LOOP_CV` by ``wrap_sync_tool`` before the thread was
    spawned. No-op when ``hook`` is ``None`` or no loop was captured.
    """
    if hook is None:
        return
    loop = _RUNNING_LOOP_CV.get()
    if loop is None or not loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(hook(filepath), loop)  # type: ignore[operator]


def mark_disk_write_done() -> None:
    """Signal that the partial-state CV write tool's FS write completed.

    No-op when the CV is not set (i.e. tool is not in
    :data:`_PARTIAL_STATE_TOOLS`, or the call is outside ``run_sync_tool``).
    """
    state = _PARTIAL_STATE_CV.get()
    if state is not None:
        state["disk_write_done"] = True


def mark_deadline_killed() -> None:
    """Signal that ``_run_git`` saw an externally-terminated Popen.

    No-op when the CV is not set.
    """
    state = _PARTIAL_STATE_CV.get()
    if state is not None:
        state["deadline_killed"] = True


def get_partial_state() -> dict[str, bool] | None:
    """Snapshot the partial-state CV; None when not active."""
    state = _PARTIAL_STATE_CV.get()
    if state is None:
        return None
    return dict(state)


def project_not_found(project: str) -> str:
    """Standard error string for unresolvable project slugs."""
    return f"Project '{project}' not found in vault."


_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)

SECTION_SHORTCUTS: dict[str, str] = {
    "context": "00-context.md",
    "tasks": "11-tasks.md",
    "roadmap": "10-roadmap.md",
    "lessons": "90-lessons.md",
}

# The scope KEY (not directory) that holds cross-project content. Several
# tools special-case it: project auto-scan and health enumeration skip it,
# and it is reached via the ``_meta`` slug shortcut. Centralised here so the
# literal is not duplicated across the resolver / read / health modules.
META_SCOPE = "meta"


def _default_scopes() -> dict[str, str]:
    """Fallback scopes when a caller does not pass an explicit mapping.

    Reads ``settings.vault_scopes`` (the single source of truth, defined in
    ``hive.config``) lazily so tests can monkeypatch the settings object
    after import — same idiom as :func:`_lock_timeout` / :func:`_post_kill_drain`.
    Avoids a duplicated literal that could silently drift from the real
    default (SSOT — Standing Order #2).
    """
    from hive.config import settings

    return settings.vault_scopes


_FENCED_CODE_RE = re.compile(
    r"(?ms)^(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^(?P=fence)[^\n]*$",
)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _strip_code(text: str) -> str:
    """Remove fenced and inline code from markdown text.

    Used by link-validator and lesson-heading parsers to skip wikilink-
    or heading-like syntax that appears inside code blocks (bash regex
    `[[:space:]]`, shell `[[ -z "$1" ]]`, code-block examples of lesson
    syntax, etc.).
    """
    text = _FENCED_CODE_RE.sub("", text)
    return _INLINE_CODE_RE.sub("", text)


_LESSON_HEADING_RE = re.compile(r"^### (\[\d{4}-\d{2}-\d{2}\] .+)$")
_FENCE_LINE_RE = re.compile(r"^(`{3,}|~{3,})")


def _mark_codeblock_lines(lines: list[str]) -> list[bool]:
    """Return a parallel boolean array: True iff that line sits inside
    a fenced code block (``` or ~~~). Fence lines themselves count as
    inside. Preserves line numbers — used by both find_lesson_heading
    and extract_lesson_headings.
    """
    in_block = [False] * len(lines)
    open_fence: str | None = None
    for idx, raw in enumerate(lines):
        match = _FENCE_LINE_RE.match(raw)
        if match:
            fence = match.group(1)
            if open_fence is None:
                open_fence = fence[0]
                in_block[idx] = True
                continue
            if raw.startswith(open_fence):
                in_block[idx] = True
                open_fence = None
                continue
        in_block[idx] = open_fence is not None
    return in_block


def extract_lesson_headings(content: str) -> list[str]:
    """Return every ``### [YYYY-MM-DD] title`` heading in ``content``,
    in document order, skipping those inside fenced code blocks.

    Result strings have the ``### `` prefix stripped to match the
    DB-key convention used by ``LessonReinforcementTracker``.
    """
    lines = content.splitlines()
    if not lines:
        return []
    in_block = _mark_codeblock_lines(lines)
    out: list[str] = []
    for idx, raw in enumerate(lines):
        if in_block[idx]:
            continue
        m = _LESSON_HEADING_RE.match(raw)
        if m:
            out.append(m.group(1))
    return out


def find_lesson_heading(content: str, line_no: int) -> str | None:
    """Walk back from ``line_no`` to the nearest lesson heading.

    A lesson heading is exactly ``### [YYYY-MM-DD] title`` (h3, ISO date
    in brackets). Headings inside fenced code blocks (``` or ~~~) are
    skipped — they are documentation about lessons, not lessons.

    Returns the heading text WITHOUT the ``### `` prefix (e.g.
    ``"[2026-05-18] foo"``) so it matches the DB-key convention used by
    ``LessonReinforcementTracker``. Returns ``None`` when no valid
    heading is found at or above ``line_no``.
    """
    lines = content.splitlines()
    if not lines:
        return None
    in_block = _mark_codeblock_lines(lines)
    start = min(line_no, len(lines))
    for idx in range(start - 1, -1, -1):
        if in_block[idx]:
            continue
        m = _LESSON_HEADING_RE.match(lines[idx])
        if m:
            return m.group(1)
    return None


_VAULT_NOT_FOUND_MSG = (
    "Vault not found at {path}.\n\n"
    "Set the vault path via the HIVE_VAULT_PATH env var (alias: VAULT_PATH) "
    "on the MCP server.\n\n"
    "  Config-file clients (Hermes, Cursor, Windsurf, …) — add to the "
    "server's env block:\n"
    "    env:\n"
    "      HIVE_VAULT_PATH: /path/to/your-vault\n\n"
    "  Claude Code:\n"
    "    claude mcp add -s user hive "
    "-e HIVE_VAULT_PATH=$HOME/your-vault "
    "-- uvx --upgrade hive-vault\n\n"
    "  Gemini CLI:\n"
    "    gemini mcp add -s user "
    "-e HIVE_VAULT_PATH=$HOME/your-vault "
    "hive-vault uvx -- --upgrade hive-vault\n\n"
    "See https://mlorentedev.github.io/hive/configuration/"
)


def _vault_guard(ctx: ServerContext) -> str:
    """Return an error message if vault is not available, or empty string if OK."""
    if ctx.vault.is_dir():
        return ""
    return _VAULT_NOT_FOUND_MSG.format(path=ctx.vault)


_SUMMARIZE_THRESHOLD = 50

_STATUS_WEIGHTS: dict[str, float] = {
    "active": 3.0,
    "draft": 2.0,
}

_RECENCY_DAYS_SCALE = 365


# ── Path resolution ────────────────────────────────────────────────────


def _parse_project_ref(project: str) -> tuple[str | None, str]:
    """Split 'scope:project' into (scope, project). Plain 'project' → (None, project)."""
    if ":" in project:
        scope, _, slug = project.partition(":")
        return scope, slug
    return None, project


def _resolve_project_dir(
    vault: Path,
    project: str,
    scopes: dict[str, str],
) -> tuple[Path, str] | None:
    """Resolve a project slug to (directory, scope_name).

    - ``_meta`` maps to the meta scope root (backward compat).
    - ``scope:project`` targets a specific scope.
    - Plain ``project`` auto-scans all scopes, first match wins.

    Supports hierarchical scopes: if the slug is not found at the first
    level of a scope directory, a breadth-first search finds it at any
    depth.  Slugs containing ``/`` are resolved as literal relative paths
    within the scope directory (no BFS).

    ``scopes`` is required: every production caller passes ``ctx.scopes``
    and the single default is resolved once at the ``create_server()``
    boundary, so there is no internal fallback to drift untested (#159).
    Tests that want the shipped default pass ``_default_scopes()``.

    Returns None if the project is not found or escapes the vault boundary.
    """
    # _meta special case → meta scope root
    if project == "_meta":
        meta_dir_name = scopes.get(META_SCOPE, "00_meta")
        d = vault / meta_dir_name
        if not d.is_dir():
            return None
        if _check_path_boundary(d, vault) is not None:
            return None
        return (d, META_SCOPE)

    explicit_scope, slug = _parse_project_ref(project)

    if explicit_scope is not None:
        dir_name = scopes.get(explicit_scope)
        if dir_name is None:
            return None
        scope_dir = vault / dir_name
        return _search_scope(scope_dir, slug, explicit_scope, vault)

    # Auto-scan: iterate scopes, first match wins, skip missing dirs
    for scope_name, dir_name in scopes.items():
        if scope_name == META_SCOPE:
            continue  # meta is not a project container
        scope_dir = vault / dir_name
        result = _search_scope(scope_dir, slug, scope_name, vault)
        if result is not None:
            return result

    return None


def _search_scope(
    scope_dir: Path,
    slug: str,
    scope_name: str,
    vault: Path,
) -> tuple[Path, str] | None:
    """Search for a slug within a scope directory.

    If *slug* contains ``/``, treat it as a literal relative path.
    Otherwise, try a direct child first, then breadth-first search.
    """
    if not scope_dir.is_dir():
        return None

    # Literal relative path (e.g. "20-products/hydra3d-plus")
    if "/" in slug:
        d = scope_dir / slug
        if d.is_dir() and _check_path_boundary(d, vault) is None:
            return (d, scope_name)
        return None

    # Fast path: direct child
    d = scope_dir / slug
    if d.is_dir() and _check_path_boundary(d, vault) is None:
        return (d, scope_name)

    # BFS: breadth-first search through subdirectories
    queue: deque[Path] = deque()
    try:
        queue.extend(sorted(c for c in scope_dir.iterdir() if c.is_dir()))
    except OSError:
        return None

    while queue:
        candidate = queue.popleft()
        try:
            children = sorted(c for c in candidate.iterdir() if c.is_dir())
        except OSError:
            continue
        for child in children:
            if child.name == slug and _check_path_boundary(child, vault) is None:
                return (child, scope_name)
            queue.append(child)

    return None


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
    scopes: dict[str, str],
) -> Path | str:
    """Resolve a vault file from project + section/path. Returns Path or error string."""
    result = _resolve_project_dir(vault, project, scopes)
    if result is None:
        return project_not_found(project)
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


# ── Match and replace ──────────────────────────────────────────────────


def _normalize_ws(text: str) -> str:
    """Strip trailing whitespace per line."""
    return "\n".join(line.rstrip() for line in text.splitlines())


def _find_line_numbers(content: str, needle: str) -> list[int]:
    """Return 1-indexed line numbers where ``needle`` starts in ``content``.

    Used to make ``vault_patch`` ambiguity errors point the caller at
    the conflicting matches instead of just saying "N occurrences".
    """
    out: list[int] = []
    start = 0
    while True:
        idx = content.find(needle, start)
        if idx == -1:
            return out
        out.append(content.count("\n", 0, idx) + 1)
        start = idx + 1


def _format_ambiguous(count: int, content: str, find: str, suffix: str = "") -> str:
    locations = _find_line_numbers(content, find)
    if not locations:
        return f"Ambiguous: find text appears {count} times{suffix}."
    shown = locations[:5]
    hint = ", ".join(f"line {ln}" for ln in shown)
    more = f" (+{len(locations) - len(shown)} more)" if len(locations) > len(shown) else ""
    return (
        f"Ambiguous: find text appears {count} times{suffix} at {hint}{more}. "
        "Add surrounding context to your `find` so it matches exactly one location."
    )


def _match_and_replace(
    content: str,
    find: str,
    replace: str,
) -> tuple[bool, str]:
    """Cascading match-and-replace: exact → body-only → whitespace-normalized.

    Returns ``(True, new_content)`` on success, ``(False, error_message)``
    on failure.
    """
    # ── Pass 1: Exact match on full content ──
    count = content.count(find)
    if count == 1:
        return True, content.replace(find, replace, 1)
    if count > 1:
        return False, _format_ambiguous(count, content, find)

    # ── Pass 2: Exact match on body (post-frontmatter) ──
    body = extract_body(content)
    frontmatter = content[: len(content) - len(body)] if body != content else ""

    count = body.count(find)
    if count == 1:
        return True, frontmatter + body.replace(find, replace, 1)
    if count > 1:
        return False, _format_ambiguous(count, content, find)

    # ── Pass 3: Whitespace-normalized match on body ──
    norm_body = _normalize_ws(body)
    norm_find = _normalize_ws(find)

    if norm_find:
        count = norm_body.count(norm_find)
        if count == 1:
            return True, frontmatter + norm_body.replace(norm_find, replace, 1)
        if count > 1:
            # After ws-normalize the offsets don't map back cleanly;
            # report count + hint without line numbers in this branch.
            return (
                False,
                f"Ambiguous: find text appears {count} times "
                "(after whitespace normalization). Add more context.",
            )

    # ── Diagnostic: similarity hint ──
    best_ratio = 0.0
    search_in = norm_body or body
    if norm_find:
        matcher = difflib.SequenceMatcher(None, norm_find, "")
        lines = search_in.splitlines()
        n_find = max(len(norm_find.splitlines()), 1)
        for i in range(max(1, len(lines) - n_find + 2)):
            chunk = "\n".join(lines[i : i + n_find])
            matcher.set_seq2(chunk)
            ratio = matcher.ratio()
            if ratio > best_ratio:
                best_ratio = ratio

    pct = int(best_ratio * 100)
    hint = f" Best match: {pct}% similar." if pct > 40 else ""
    return False, f"find text not found.{hint}"


# ── Text formatting ────────────────────────────────────────────────────


def _truncate(text: str, max_lines: int) -> str:
    """Truncate text to max_lines, appending a notice if truncated."""
    if max_lines <= 0:
        return text
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    remaining = len(lines) - max_lines
    return "\n".join(lines[:max_lines]) + f"\n\n[... truncated, {remaining} more lines]"


def _make_frontmatter(doc_id: str, doc_type: str) -> str:
    """Generate YAML frontmatter block with sanitized id and type."""
    safe_id = re.sub(r"[^\w\-.]", "_", doc_id)
    safe_type = re.sub(r"[^\w\-.]", "_", doc_type)
    return (
        f"---\n"
        f"id: {safe_id}\n"
        f"type: {safe_type}\n"
        f"status: active\n"
        f'created: "{date.today().isoformat()}"\n'
        f"---\n\n"
    )


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


# ── File I/O ───────────────────────────────────────────────────────────


def format_io_error(exc: BaseException, path: object, action: str) -> str:
    """Produce a user-facing message for an OSError / UnicodeDecodeError.

    Distinguishes the common cases (perms, missing, wrong type, encoding)
    and includes a remediation hint per case. Generic OSError keeps the
    errno + strerror for diagnosis but adds context the bare repr lacks.
    """
    if isinstance(exc, PermissionError):
        return (
            f"Cannot {action} '{path}': permission denied. "
            "Check the file is readable/writable by the MCP server process."
        )
    if isinstance(exc, FileNotFoundError):
        return (
            f"Cannot {action} '{path}': file not found. "
            "Check the path is correct and the parent directory exists."
        )
    if isinstance(exc, IsADirectoryError):
        return f"Cannot {action} '{path}': target is a directory, expected a file."
    if isinstance(exc, UnicodeDecodeError):
        return f"Cannot read '{path}': file is not valid UTF-8 (hive only supports UTF-8 markdown)."
    if isinstance(exc, OSError):
        errno = exc.errno or "?"
        strerror = exc.strerror or str(exc)
        return f"Cannot {action} '{path}': {strerror} (errno {errno})."
    return f"Cannot {action} '{path}': {exc!r}"


def _safe_read(f: Path) -> str | None:
    """Read file text, returning None on error."""
    try:
        return f.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        _log.debug("Skipping non-UTF-8 file: %s", f)
        return None
    except OSError as exc:
        _log.warning("Cannot read file %s: %s", f, exc)
        return None


# ── Tracking ───────────────────────────────────────────────────────────


def track(
    ctx: ServerContext,
    tool: str,
    result: str,
    project: str = "",
    section: str = "",
) -> str:
    """Log a tool call and return the result unchanged."""
    ctx.tracker.log_call(tool, project, len(result.splitlines()))
    if project and section:
        is_write = tool == "vault_write"
        ctx.relevance.record_access(project, section, is_write=is_write)
    return result


# ── Git operations ─────────────────────────────────────────────────────

_GIT_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()

# Per-call subprocess registry — populated by `_run_git` for every spawned
# Popen, drained by ``bounded_call`` / ``tool_span`` on deadline expiry to
# terminate stuck git invocations (HIVE-115 PR-3 / ADR-008). Set by
# tool wrappers right before the handler runs; thread-propagated via
# contextvars (asyncio.to_thread carries the context, so the worker
# thread sees the same list the async supervisor will iterate).
# Default ``None`` = legacy unsupervised call (e.g. ``_git_log`` /
# ``_git_recent`` read paths that retain ``subprocess.run`` per m1).
_GIT_REGISTRY_CV: contextvars.ContextVar[list[subprocess.Popen[bytes]] | None] = (
    contextvars.ContextVar("_GIT_REGISTRY", default=None)
)


_GIT_LOCK_WAIT_HISTORY: deque[int] = deque(maxlen=100)
_GIT_LOCK_WAIT_GUARD = threading.Lock()


def _record_lock_wait(waited_ms: int) -> None:
    """Append a lock-wait sample to the rolling N=100 window (thread-safe)."""
    with _GIT_LOCK_WAIT_GUARD:
        _GIT_LOCK_WAIT_HISTORY.append(waited_ms)


def _git_lock_stats_snapshot() -> dict[str, float | int]:
    """Snapshot of last_git_lock_wait_ms rolling window (mean, p99, count).

    HIVE-115 / ADR-010 telemetry — feeds Phase B gate decision. Surfaced
    in ``vault_health(include_runtime=True)``.
    """
    with _GIT_LOCK_WAIT_GUARD:
        samples = list(_GIT_LOCK_WAIT_HISTORY)
    n = len(samples)
    if n == 0:
        return {"mean_ms": 0.0, "p99_ms": 0.0, "sample_count": 0}
    mean = sum(samples) / n
    sorted_s = sorted(samples)
    p99_idx = min(n - 1, max(0, int(n * 0.99)))
    return {
        "mean_ms": float(mean),
        "p99_ms": float(sorted_s[p99_idx]),
        "sample_count": n,
    }


def _compute_wal_size_bytes(state_dir: Path) -> int:
    """Sum size of all ``*.db-wal`` files under ``state_dir`` (stat-only).

    HIVE-115 / ADR-009 telemetry — bounded WAL is the success signal
    for the periodic ``PRAGMA wal_checkpoint(PASSIVE)`` thread.
    """
    total = 0
    try:
        for wal in state_dir.glob("*.db-wal"):
            with contextlib.suppress(OSError):
                total += wal.stat().st_size
    except OSError:
        pass
    return total


def _clean_stale_wal_files(state_dir: Path, max_age_days: int = 30) -> None:
    """Remove stale (≥*max_age_days*) zero-byte WAL files under *state_dir*.

    A zero-byte ``.db-wal`` file means the WAL was fully checkpointed but
    SQLite left the file behind — cosmetic clutter, not a functional issue.
    These files accumulate over time and can confuse operators inspecting
    ``wal_size_bytes`` in ``vault_health``. This cleanup runs at server
    startup and is safe to call at any time (deleting a 0-byte WAL that a
    live sibling process just checkpointed is harmless — SQLite re-creates
    it on the next write).
    """
    now = time.time()
    cutoff = now - max_age_days * 86400
    try:
        for wal in state_dir.glob("*.db-wal"):
            if wal.stat().st_size == 0 and wal.stat().st_mtime < cutoff:
                wal.unlink()
                _log.info("mcp.wal_cleanup removed stale 0-byte WAL: %s", wal.name)
    except OSError:
        pass


def _count_competing_hive_processes() -> int:
    """Count distinct ``hive-vault`` PIDs (excluding self), same user only.

    Cached for ~30s via an epoch-bucket key on :func:`functools.lru_cache`
    so ``vault_health`` calls do not pay psutil's process-iter cost on every
    request (Windows ~100ms, POSIX ~10ms). Filters strictly by process
    name AND same user to avoid antivirus / backup-tool false positives
    (HIVE-115 audit MINOR finding).
    """
    epoch_bucket = int(time.time() // 30)
    return _competing_pid_count_cached(epoch_bucket)


@functools.lru_cache(maxsize=1)
def _competing_pid_count_cached(epoch_bucket: int) -> int:  # noqa: ARG001
    """Real count behind :func:`_count_competing_hive_processes`.

    The ``epoch_bucket`` argument is used solely as the lru_cache key so
    callers get a fresh result every 30 seconds while not paying for
    process_iter on every call.
    """
    try:
        import psutil
    except ImportError:
        return 0

    self_pid = os.getpid()
    try:
        self_user = psutil.Process(self_pid).username()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        self_user = None

    count = 0
    try:
        for proc in psutil.process_iter(attrs=["name", "pid", "username"]):
            try:
                info = proc.info
                if info.get("pid") == self_pid:
                    continue
                name = (info.get("name") or "").lower()
                if "hive-vault" not in name and "hive_vault" not in name:
                    continue
                if self_user is not None and info.get("username") != self_user:
                    continue
                count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:  # noqa: BLE001 — psutil can raise OS-specific errors
        return 0
    return count


def _lock_timeout() -> int:
    """Current lock-acquire timeout in seconds (HIVE-115 / ADR-010).

    Lazy read of ``settings.lock_timeout_s`` so tests can monkeypatch the
    settings object after import. Env: ``HIVE_LOCK_TIMEOUT_S`` (default 30,
    validated 1..600).
    """
    from hive.config import settings

    return settings.lock_timeout_s


def _post_kill_drain() -> float:
    """Current post-kill drain window in seconds (HIVE-116 / ADR-012).

    Lazy read of ``settings.post_kill_drain_s`` — tests monkeypatch the
    settings object after import. Env: ``HIVE_POST_KILL_DRAIN_S``
    (default 5.0, validated 0.5..30.0).
    """
    from hive.config import settings

    return settings.post_kill_drain_s


def _commit_args(message: str) -> list[str]:
    """Build the ``git commit`` argv, gated on ``git_commit_no_verify``.

    Lazy read of ``settings.git_commit_no_verify`` (default True) so tests
    can monkeypatch the settings object after import — same idiom as
    :func:`_lock_timeout`. Hive's auto-commits skip the human-oriented
    pre-commit hook chain (the slow vault hook hung writes ~60s until the
    deadline killed them); scanning lives push-side/CI now. Env:
    ``HIVE_GIT_COMMIT_NO_VERIFY`` (default True).
    """
    from hive.config import settings

    args = ["commit", "-m", message]
    if settings.git_commit_no_verify:
        args.insert(1, "--no-verify")
    return args


def _acquire_with_telemetry(
    lock: threading.Lock,
    lock_name: str,
    timeout: float | None = None,
) -> bool:
    """Acquire a threading.Lock and emit structured ``mcp.lock_contention`` log.

    Records ``waited_ms`` and ``abandoned`` (true on timeout). Feeds the
    Phase B gate decision per HIVE-115 (see vault backlog HIVE-115 entry +
    [[adr-010-external-committer-coexistence]]).
    """
    actual_timeout = float(timeout) if timeout is not None else float(_lock_timeout())
    start = time.monotonic()
    acquired = lock.acquire(timeout=actual_timeout)
    waited_ms = int((time.monotonic() - start) * 1000)
    _log.info(
        "mcp.lock_contention lock=%s waited_ms=%d abandoned=%s",
        lock_name,
        waited_ms,
        "false" if acquired else "true",
    )
    _record_lock_wait(waited_ms)
    return acquired


@contextmanager
def _filelock_with_telemetry(
    lock: filelock.BaseFileLock,
    lock_name: str,
    timeout: float | None = None,
) -> Iterator[None]:
    """Acquire a filelock as a context manager with structured telemetry.

    Mirror of :func:`_acquire_with_telemetry` for inter-process locks.
    Emits exactly one ``mcp.lock_contention`` log line per attempt; on
    timeout, emits ``abandoned=true`` and re-raises ``filelock.Timeout``
    so callers can fall through to their existing error path unchanged.
    """
    actual_timeout = float(timeout) if timeout is not None else float(_lock_timeout())
    start = time.monotonic()
    try:
        with lock.acquire(timeout=actual_timeout):
            waited_ms = int((time.monotonic() - start) * 1000)
            _log.info(
                "mcp.lock_contention lock=%s waited_ms=%d abandoned=false",
                lock_name,
                waited_ms,
            )
            _record_lock_wait(waited_ms)
            yield
    except filelock.Timeout:
        waited_ms = int((time.monotonic() - start) * 1000)
        _log.info(
            "mcp.lock_contention lock=%s waited_ms=%d abandoned=true",
            lock_name,
            waited_ms,
        )
        _record_lock_wait(waited_ms)
        raise


class WriteLockTimeout(Exception):  # noqa: N818  # mirrors stdlib TimeoutError naming
    """Either the intra-process or inter-process write lock timed out."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@contextmanager
def vault_write_lock(vault_path: Path) -> Iterator[None]:
    """Acquire both the thread lock and the inter-process filelock for a write.

    Combines what was a 3x-duplicated ``_WRITE_LOCK.acquire / try / filelock /
    try / except / finally`` block across ``vault_write`` create, append/replace,
    and ``vault_patch``. Raises :class:`WriteLockTimeout` (with a
    human-readable ``reason``) on either lock timeout; callers map that to a
    user-facing ``"Server busy"`` message.

    The filelock is the singleton from :func:`_git_filelock`, so a nested
    ``_git_commit`` inside this block re-enters the same lock cleanly.
    """
    if not _acquire_with_telemetry(_WRITE_LOCK, "_WRITE_LOCK"):
        raise WriteLockTimeout("write lock timeout")
    try:
        try:
            with _filelock_with_telemetry(_git_filelock(vault_path), "_git_filelock"):
                yield
        except filelock.Timeout as exc:
            raise WriteLockTimeout("inter-process lock timeout") from exc
    finally:
        _WRITE_LOCK.release()


_GIT_FILELOCKS: dict[str, filelock.BaseFileLock] = {}
_GIT_FILELOCKS_GUARD = threading.Lock()


def _git_filelock(vault_path: Path) -> filelock.BaseFileLock:
    """Inter-process lock file under the vault's .git dir.

    Serializes write critical sections across separate hive processes
    (the threading lock only covers a single process). The lock file
    lives alongside git's own ``index.lock`` so it travels with the
    repo and is naturally ignored by git itself.

    Returns a process-singleton ``FileLock`` instance per vault path so
    nested ``acquire()`` calls from the same thread re-enter cleanly:
    ``vault_write`` takes the lock around the read-modify-write, then
    calls ``_git_commit`` which would otherwise deadlock waiting on its
    own outer hold.
    """
    key = str(vault_path / ".git" / "hive.lock")
    with _GIT_FILELOCKS_GUARD:
        lock = _GIT_FILELOCKS.get(key)
        if lock is None:
            lock = filelock.FileLock(key)
            _GIT_FILELOCKS[key] = lock
    return lock


def evict_filelock(vault_path: Path) -> bool:
    """Pop the cached ``FileLock`` for ``vault_path`` from the singleton cache.

    HIVE-116 PR-2 / ADR-012 core primitive. Called by the deadline supervisor
    after the post-kill drain when at least one Popen was terminated, to
    unblock sibling workers that would otherwise wait on the still-held
    ``.git/hive.lock`` of a runaway worker thread.

    Returns True when an entry was evicted, False when nothing was cached
    (already-evicted or never-acquired). Idempotent under
    ``_GIT_FILELOCKS_GUARD``. Does **not** force-release the underlying
    lock — that is impossible from outside the holder; instead, the next
    ``_git_filelock`` call constructs a fresh ``FileLock`` against the
    same path. On POSIX the kernel tracks fcntl owners by fd so the new
    object can acquire as soon as the holder releases. On Windows the
    orphan file persists until the parent process exits (filelock library
    invariant), but no longer blocks new acquires.
    """
    key = str(vault_path / ".git" / "hive.lock")
    with _GIT_FILELOCKS_GUARD:
        lock = _GIT_FILELOCKS.pop(key, None)
    return lock is not None


@asynccontextmanager
async def tool_span(
    tool_name: str,
    timeout_seconds: float,
) -> AsyncIterator[None]:
    """Enforce a hard deadline AND register a Popen kill-set for the body.

    Raises ``TimeoutError`` if the tool body exceeds *timeout_seconds*.
    On expiry: terminates every Popen registered by ``_run_git`` during
    the body (POSIX SIGTERM → grace → SIGKILL; Windows TerminateProcess
    twice), then records the event in ``GHOST_RESPONSES`` with
    ``source="deadline"`` so operators can distinguish deadline-driven
    suppressions from cancellation-race suppressions in
    ``vault_health.ghost_responses.by_source`` (HIVE-115 PR-3 / ADR-008,
    pre-PR-3 audit B4).

    HIVE-116 PR-2: when ``_VAULT_FOR_EVICTION_CV`` is set AND at least
    one Popen was killed, sleep ``HIVE_POST_KILL_DRAIN_S`` (default 5.0s,
    capped 0.5..30) and evict the cached ``.git/hive.lock`` filelock so
    sibling workers can acquire freshly. Drain order matters: SIGTERM →
    grace → SIGKILL → stdio drain (in _terminate_registry) → telemetry
    → **post_kill_drain → evict_filelock** → raise. The post-kill drain
    is what lets the worker thread escape ``_filelock_with_telemetry``'s
    ``__exit__`` naturally on the happy path; eviction is the safety net
    for the worst case where the thread is stuck in proc.communicate().

    Registry propagation: a fresh ``list`` is set on ``_GIT_REGISTRY_CV``
    for the duration of the body. ``contextvars`` propagates the same
    list reference into ``asyncio.to_thread`` workers (CPython 3.9+),
    so ``_run_git`` callsites inside sync handlers populate the same
    object the supervisor iterates on timeout.
    """
    from hive._deadline import _DEFAULT_GRACE_S, _terminate_registry

    registry: list[subprocess.Popen[bytes]] = []
    token = _GIT_REGISTRY_CV.set(registry)
    start = time.monotonic()
    try:
        try:
            async with asyncio.timeout(timeout_seconds):
                yield
        except TimeoutError:
            elapsed = time.monotonic() - start
            try:
                killed = await _terminate_registry(registry, _DEFAULT_GRACE_S)
            except Exception as exc:  # noqa: BLE001
                _log.debug("tool_span termination cleanup failed: %s", exc)
                killed = []
            try:
                from hive._compat import GHOST_RESPONSES

                GHOST_RESPONSES.record(tool=tool_name, source="deadline")
            except Exception as exc:  # noqa: BLE001
                _log.debug("tool_span ghost_responses.record failed: %s", exc)
            _log.warning(
                "%s timed out after %.1fs (deadline %.0fs); killed %d "
                "subprocess(es) "
                "[mcp.tool_span.deadline_exceeded killed_pids=%s]",
                tool_name,
                elapsed,
                timeout_seconds,
                len(killed),
                killed,
            )
            # HIVE-116 AC-1: cooperative filelock eviction
            vault_for_eviction = _VAULT_FOR_EVICTION_CV.get()
            if vault_for_eviction is not None and killed:
                await _drain_and_evict(
                    vault_for_eviction,
                    killed,
                    tool_name,
                )
            raise
        else:
            elapsed = time.monotonic() - start
            _log.debug("%s completed in %.1fs", tool_name, elapsed)
    finally:
        _GIT_REGISTRY_CV.reset(token)


async def _drain_and_evict(
    vault_path: Path,
    killed_pids: list[int],
    tool_name: str,
) -> None:
    """Wait the post-kill drain, evict ``.git/hive.lock`` from cache, log.

    HIVE-116 AC-1 + AC-8. The drain window lets the worker thread holding
    the in-process side of the cooperative lock escape ``__exit__`` on
    the happy path; eviction unblocks new acquires when the thread is
    stuck. Recording into the ``LockEvictionTracker`` (T-2.5) happens
    lazily via the deferred import — keeping the supervisor agnostic of
    ``ServerContext`` lifecycle so this module stays import-cycle-free.
    """
    drain_s = _post_kill_drain()
    try:
        await asyncio.sleep(drain_s)
    except asyncio.CancelledError:
        # Even when the caller cancels, attempt the eviction. Re-raise
        # after the synchronous cleanup so the cancellation propagates.
        evicted = evict_filelock(vault_path)
        if evicted:
            _log.info(
                "mcp.lock_eviction lock=_git_filelock vault=%s "
                "killed_pids=%s drain_s=%.1f tool=%s cancelled=true",
                vault_path,
                killed_pids,
                drain_s,
                tool_name,
            )
        raise
    evicted = evict_filelock(vault_path)
    if evicted:
        _log.info(
            "mcp.lock_eviction lock=_git_filelock vault=%s killed_pids=%s drain_s=%.1f tool=%s",
            vault_path,
            killed_pids,
            drain_s,
            tool_name,
        )
        _record_lock_eviction(vault_path, killed_pids)


def _record_lock_eviction(
    vault_path: Path,
    killed_pids: list[int],
) -> None:
    """Persist the eviction event for vault_health telemetry (T-2.5).

    Looks up the global ``LockEvictionTracker`` via the module-level
    registry set by ``register_lock_eviction_tracker``. No-op when no
    tracker is registered (e.g. during unit tests of ``tool_span`` that
    do not build a full ``ServerContext``).
    """
    tracker = _LOCK_EVICTION_TRACKER
    if tracker is None:
        return
    try:
        tracker.record(vault_path, killed_pids)
    except Exception as exc:  # noqa: BLE001
        # Telemetry must never crash the supervisor.
        _log.debug("lock_eviction tracker.record failed: %s", exc)


# Module-level singleton registry for the LockEvictionTracker. Set by
# ``register_lock_eviction_tracker`` during ``create_server`` (T-2.5);
# read by ``_record_lock_eviction``. None outside production paths.
_LOCK_EVICTION_TRACKER: LockEvictionTracker | None = None


def register_lock_eviction_tracker(
    tracker: LockEvictionTracker | None,
) -> None:
    """Install the process-global LockEvictionTracker.

    Called once during ``create_server``. Stored as module-level
    singleton because the supervisor (``tool_span``, ``bounded_call``)
    does not receive ``ServerContext`` and cannot import it without
    creating a cycle. Accepts ``None`` for test teardown.
    """
    global _LOCK_EVICTION_TRACKER
    _LOCK_EVICTION_TRACKER = tracker


_GENERIC_TIMEOUT_FMT = (
    "{tool} timed out after {timeout:.0f}s. "
    "Server may be under load or a lock is contended; retry shortly."
)
_PARTIAL_STATE_TIMEOUT_FMT = (
    "{tool} timed out after {timeout:.0f}s — partial state: "
    "disk write succeeded, git commit killed by deadline; "
    "verify with vault_query before retrying."
)


async def run_sync_tool(
    tool_name: str,
    timeout_seconds: float,
    fn: Callable[..., str],
    *args: object,
    **kwargs: object,
) -> str:
    """Run a sync tool body in a worker thread with a wall-clock timeout.

    ``asyncio.timeout`` cancels the awaiting coroutine but cannot interrupt
    the underlying thread — see lesson 2026-03-13. This helper still uses
    it because the goal is to give the client a fast response (and unblock
    the MCP receive loop) even when the sync work is stuck on a lock or
    subprocess; the thread eventually returns and its late ``respond()``
    call is silenced by the ``_compat`` shim.

    HIVE-116 AC-6: write tools (``vault_write`` / ``vault_patch``) opt into
    a partial-state response via :data:`_PARTIAL_STATE_CV`. The CV holds a
    dict the to_thread worker mutates as it progresses; on TimeoutError
    the awaiter reads the dict and surfaces the partial-state message when
    the disk write landed before the kill (so the calling agent does not
    retry blindly and double-write).

    Returns a user-visible error string on timeout instead of propagating
    so the MCP client sees a normal tool response.
    """
    state_token: contextvars.Token[dict[str, bool] | None] | None = None
    if tool_name in _PARTIAL_STATE_TOOLS:
        state_token = _PARTIAL_STATE_CV.set(
            {"disk_write_done": False, "deadline_killed": False},
        )
    try:
        async with tool_span(tool_name, timeout_seconds):
            return await asyncio.to_thread(fn, *args, **kwargs)
    except TimeoutError:
        if (
            state_token is not None
            and (state := _PARTIAL_STATE_CV.get()) is not None
            and state.get("disk_write_done")
        ):
            return _PARTIAL_STATE_TIMEOUT_FMT.format(
                tool=tool_name,
                timeout=timeout_seconds,
            )
        return _GENERIC_TIMEOUT_FMT.format(
            tool=tool_name,
            timeout=timeout_seconds,
        )
    finally:
        if state_token is not None:
            _PARTIAL_STATE_CV.reset(state_token)


def wrap_sync_tool(
    ctx: ServerContext,
    tool_name: str,
) -> Callable[[Callable[..., str]], Callable[..., object]]:
    """Decorator: convert a sync MCP tool handler into async-with-timeout.

    Applied between ``@mcp.tool`` and the sync handler so the body keeps
    its original ``def`` form (no indentation churn) while gaining a
    wall-clock timeout and thread-pool execution. The wrapper preserves
    the original signature, annotations, name, and docstring so
    FastMCP's schema introspection still sees the correct shape.

    HIVE-116 PR-2: for tools in :data:`_PARTIAL_STATE_TOOLS`, the wrapper
    sets :data:`_VAULT_FOR_EVICTION_CV` to ``ctx.vault`` before the body
    runs so ``tool_span``'s deadline branch can call ``evict_filelock``
    against the correct vault on supervisor kill.

    Usage::

        @mcp.tool(annotations=_READ_ONLY)
        @wrap_sync_tool(ctx, "vault_list")
        def vault_list(project: str = "", ...) -> str:
            ...
    """
    import functools
    import inspect

    def decorator(fn: Callable[..., str]) -> Callable[..., object]:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args: object, **kwargs: object) -> str:
            vault_token: contextvars.Token[Path | None] | None = None
            if tool_name in _PARTIAL_STATE_TOOLS and ctx.vault.is_dir():
                vault_token = _VAULT_FOR_EVICTION_CV.set(ctx.vault)
            loop_token = _RUNNING_LOOP_CV.set(asyncio.get_running_loop())
            try:
                return await run_sync_tool(
                    tool_name,
                    ctx.tool_timeout,
                    fn,
                    *args,
                    **kwargs,
                )
            finally:
                if vault_token is not None:
                    _VAULT_FOR_EVICTION_CV.reset(vault_token)
                _RUNNING_LOOP_CV.reset(loop_token)

        wrapper.__signature__ = sig  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _run_git(
    args: list[str],
    vault_path: Path,
    *,
    registry: list[subprocess.Popen[bytes]] | None = None,
) -> tuple[int, str, str]:
    """Spawn one git invocation as a ``subprocess.Popen``; register on the
    deadline registry; return ``(returncode, stdout, stderr)`` text.

    The Green prerequisite of HIVE-115 PR-3 (per the 2026-05-22 audit B2).
    All write-path git callers (``_git_commit``, ``_git_commit_all``)
    funnel through this helper so the Popen migration touches one place
    and the supervisor's termination authority composes uniformly.

    Behaviour contract:

    - **No inner ``timeout=``.** ``bounded_call`` / ``tool_span`` are the
      single source of truth for deadlines (audit B1). An inner timeout
      would race the supervisor's external termination — different error
      semantics, harder to reason about.
    - **Cross-OS creation flags** via ``hive._deadline.popen_creation_kwargs``:
      Windows ``CREATE_NEW_PROCESS_GROUP``; POSIX ``start_new_session=True``.
      Both let the supervisor reach descendants on termination (audit M2).
    - **Registry**: caller-supplied list, or ``_GIT_REGISTRY_CV.get()``
      when ``None``. ``None`` from both = "no supervisor active"
      (legacy / test path); we still run, just without termination
      authority. Mutation of the registry is thread-safe because list
      append/remove are atomic in CPython.
    - **External termination defense**: catches ``BrokenPipeError`` and
      ``OSError`` because the supervisor may kill the Popen mid-flight;
      we drain whatever stdio is left and return ``rc=-1`` on hard
      failure.
    """
    from hive._deadline import popen_creation_kwargs

    if registry is None:
        registry = _GIT_REGISTRY_CV.get()

    proc = subprocess.Popen(  # noqa: S603
        ["git", *args],
        cwd=vault_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **popen_creation_kwargs(),
    )
    if registry is not None:
        registry.append(proc)
    stdout_b: bytes = b""
    stderr_b: bytes = b""
    externally_terminated = False
    try:
        try:
            stdout_b, stderr_b = proc.communicate()
        except (BrokenPipeError, OSError) as exc:
            # External termination by bounded_call / tool_span.
            _log.debug("git %s: external termination (%s)", args[0], exc)
            externally_terminated = True
            with contextlib.suppress(subprocess.SubprocessError, OSError):
                # Best-effort drain of whatever the kernel buffered before
                # the kill. Failures here are unrecoverable.
                stdout_b, stderr_b = proc.communicate()
        rc = proc.returncode if proc.returncode is not None else RC_EXTERNAL_TERMINATION
        # HIVE-116 AC-3: an external-termination rc must be remapped from
        # whatever the OS reported (Linux SIGTERM = -15, Windows = 1) to
        # our named sentinel so callers can predicate on the constant. The
        # synthetic stderr replaces the (often empty) original.
        if externally_terminated:
            stderr_b = synthesize_external_termination_stderr(
                stderr_b or b"",
            ).encode("utf-8")
            rc = RC_EXTERNAL_TERMINATION
            mark_deadline_killed()
    finally:
        if registry is not None and proc in registry:
            registry.remove(proc)
    return (
        rc,
        (stdout_b or b"").decode("utf-8", "replace"),
        (stderr_b or b"").decode("utf-8", "replace"),
    )


def _git_commit(
    vault_path: Path,
    rel_paths: list[Path],
    message: str,
) -> None:
    """Stage one or more files and commit them in a single git invocation.

    Accepts a list of paths so a multi-write tool (``vault_patch`` with N
    patches, ``capture_lesson`` batch with N lessons) issues exactly one
    ``git add`` + one ``git commit`` instead of N pairs — per-call cost
    drops from ~150ms*N to ~150ms total (HIVE-104 Fase A).

    Empty list is a logged no-op so callers may pass an empty result of
    a filter pass without guarding at every site.

    This is a best-effort side-effect: failures are logged but never
    propagated, so a git problem cannot crash the MCP server or prevent
    the tool response from reaching the client.

    Serialized at two levels: ``_GIT_LOCK`` (threading) serializes
    concurrent calls inside a single hive process; the filelock under
    ``vault/.git/hive.lock`` serializes concurrent calls across separate
    hive processes (one per MCP client session). Without the filelock,
    two hive subprocesses could race on git's own ``.git/index.lock``
    and time out (see hive.log history: 5+ ``git commit timed out``
    warnings from concurrent sessions).

    Both Popens (``git add`` then ``git commit``) register on the
    deadline registry via ``_run_git``; if ``bounded_call`` /
    ``tool_span`` fires its deadline mid-sequence, the surviving Popen
    is terminated externally and this function returns to the caller
    via the supervisor's ``TimeoutError``.
    """
    if not rel_paths:
        _log.debug("git commit no-op: empty path list")
        return
    safe_msg = message.replace("\n", " ").replace("\r", " ")
    path_strs = [str(p) for p in rel_paths]
    if not _acquire_with_telemetry(_GIT_LOCK, "_GIT_LOCK"):
        _log.warning(
            "git commit skipped for %s: thread lock timeout (%ds)",
            path_strs,
            _lock_timeout(),
        )
        return
    try:
        try:
            with _filelock_with_telemetry(_git_filelock(vault_path), "_git_filelock"):
                rc, _, err = _run_git(["add", *path_strs], vault_path)
                if rc != 0:
                    cause = "external_termination" if rc == RC_EXTERNAL_TERMINATION else "git_error"
                    _log.warning(
                        "git add failed for %s rc=%s cause=%s err=%s",
                        path_strs,
                        rc,
                        cause,
                        err.strip(),
                    )
                    return
                rc, _, err = _run_git(_commit_args(safe_msg), vault_path)
                if rc != 0:
                    cause = "external_termination" if rc == RC_EXTERNAL_TERMINATION else "git_error"
                    _log.warning(
                        "git commit failed for %s rc=%s cause=%s err=%s",
                        path_strs,
                        rc,
                        cause,
                        err.strip(),
                    )
        except filelock.Timeout:
            _log.warning(
                "git commit skipped for %s: inter-process lock timeout (%ds)",
                path_strs,
                _lock_timeout(),
            )
        except Exception as exc:
            _log.warning(
                "git commit unexpected error for %s: %s",
                path_strs,
                exc,
            )
    finally:
        _GIT_LOCK.release()


def _git_commit_all(
    vault_path: Path,
    message: str,
) -> tuple[str, str]:
    """Stage every change in the vault and create one commit.

    Returns ``(status, detail)`` where ``status`` is one of:
    - ``"committed"`` — a new commit was created; ``detail`` is the SHA.
    - ``"clean"`` — nothing to commit; ``detail`` is an empty string.
    - ``"error"`` — git failed; ``detail`` is the human-readable reason.

    Used by the ``vault_commit`` MCP tool to flush the working tree
    accumulated by ``commit=False`` writes (HIVE-104 Fase B1).
    """
    safe_msg = message.replace("\n", " ").replace("\r", " ") or "vault: batch update"
    if not _acquire_with_telemetry(_GIT_LOCK, "_GIT_LOCK"):
        return "error", "thread lock timeout"
    try:
        try:
            with _filelock_with_telemetry(_git_filelock(vault_path), "_git_filelock"):
                rc, porcelain_out, porcelain_err = _run_git(
                    ["status", "--porcelain"],
                    vault_path,
                )
                if rc != 0:
                    return "error", (f"git status rc={rc}: {porcelain_err.strip() or '<empty>'}")
                if not porcelain_out.strip():
                    return "clean", ""
                rc, _, err = _run_git(["add", "-A"], vault_path)
                if rc != 0:
                    return "error", f"git add -A rc={rc}: {err.strip() or '<empty>'}"
                rc, _, err = _run_git(_commit_args(safe_msg), vault_path)
                if rc != 0:
                    return "error", f"git commit rc={rc}: {err.strip() or '<empty>'}"
                rc, rev_out, err = _run_git(["rev-parse", "HEAD"], vault_path)
                if rc != 0:
                    return "error", f"git rev-parse rc={rc}: {err.strip() or '<empty>'}"
                return "committed", rev_out.strip()
        except filelock.Timeout:
            return "error", "inter-process lock timeout"
        except Exception as exc:
            return "error", f"unexpected: {type(exc).__name__}: {exc}"
    finally:
        _GIT_LOCK.release()


def _is_external_committer_healthy(
    vault_path: Path,
    auto_save_interval_minutes: int,
) -> bool:
    """Return True when an external committer (obsidian-git) is alive.

    Composite predicate per HIVE-115 pre-PR-3 audit M4:

    ``healthy ⇔ (last_commit_age < 2 * autoSaveInterval)
                ∨ (`git status --porcelain` is empty)``

    Rationale: a recent commit means the external committer is firing
    on schedule (deferable). An idle vault (clean working tree) means
    there's nothing for the external committer to commit, so deferral
    is safe by definition. The dangerous case — dirty vault + no
    recent commits — is the only one that falls back to hive's own
    commit path.

    Read-path helper (no termination needed); uses bare
    ``subprocess.run`` rather than the supervised ``_run_git``.
    """
    window_seconds = max(1, auto_save_interval_minutes) * 60 * 2
    try:
        log = subprocess.run(  # noqa: S603, S607
            ["git", "log", "-1", "--format=%ct"],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            **_subprocess_run_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug(
            "mcp.detect_and_defer.git_log_failed vault=%s exc=%s",
            vault_path,
            exc,
        )
        return False
    try:
        last_commit_epoch = int(log.stdout.strip()) if log.returncode == 0 else 0
    except ValueError:
        last_commit_epoch = 0
    if last_commit_epoch and (time.time() - last_commit_epoch) < window_seconds:
        return True
    # Stale commits → external committer may have stalled. Idle vault?
    try:
        porcelain = subprocess.run(  # noqa: S603, S607
            ["git", "status", "--porcelain"],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            **_subprocess_run_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug(
            "mcp.detect_and_defer.git_status_failed vault=%s exc=%s",
            vault_path,
            exc,
        )
        return False
    return porcelain.returncode == 0 and porcelain.stdout.strip() == ""


def _vault_write_deferral_suffix(
    vault_path: Path,
    *,
    requested_commit: bool,
) -> str:
    """Return the trailing suffix for a deferred vault_write response, or
    empty when no deferral applies.

    Truth table:

    +---------------+--------------------+---------------------------------+
    | requested     | defer predicate    | suffix                          |
    | commit=       | (env+obsidian+...) |                                 |
    +===============+====================+=================================+
    | False         | any                | ``""``                          |
    +---------------+--------------------+---------------------------------+
    | True          | False              | ``""``                          |
    +---------------+--------------------+---------------------------------+
    | True          | True               | ``" (deferred to obsidian-git;  |
    |               |                    | will be picked up on its next   |
    |               |                    | tick)"``                        |
    +---------------+--------------------+---------------------------------+

    The "uncommitted — call vault_commit to flush" suffix for
    ``commit=False`` lives at the original call site and is unchanged
    by this helper.
    """
    if not requested_commit:
        return ""
    # Avoid circular import — _vault_write imports _helpers.
    from hive._vault_write import _should_defer_to_external_committer

    if not _should_defer_to_external_committer(vault_path):
        return ""
    return " (deferred to obsidian-git; will be picked up on its next tick)"


def detect_obsidian_git(vault_path: Path) -> dict[str, int] | None:
    """Return obsidian-git config when the plugin is active in this vault.

    Reads ``<vault>/.obsidian/plugins/obsidian-git/data.json`` (the
    plugin's persisted settings file). Returns ``{"commit_interval": N}``
    when the file is present, parseable, and ``commitInterval > 0``.
    Returns ``None`` otherwise.

    All path joining goes through :class:`pathlib.Path` so the same
    code works on POSIX and Windows hosts (Risk #4: the maintainer
    rotates across Linux, macOS, and Windows daily; a hardcoded
    forward-slash would silently miss the file on Windows and disable
    the auto-detection feature without any error).
    """
    data_file = vault_path / ".obsidian" / "plugins" / "obsidian-git" / "data.json"
    try:
        raw = data_file.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(cfg, dict):
        return None
    interval = cfg.get("commitInterval")
    if not isinstance(interval, int) or interval <= 0:
        return None
    return {"commit_interval": interval}


_GIT_CACHE_TTL_S = 300.0  # 5 min — invalidated earlier if HEAD changes
_GIT_CACHE_LOCK = threading.Lock()
# Entry: (cached_at_monotonic, head_sha, value)
_git_log_cache: dict[tuple[str, int], tuple[float, str, str]] = {}
_git_recent_cache: dict[tuple[str, int], tuple[float, str, list[str]]] = {}


def _current_head_sha(vault_path: Path) -> str:
    """Fast read of .git/HEAD (and the referenced ref) for cache keys.

    Returns empty string on any failure — the caller treats that as
    "no cache" and skips caching, so a transient git problem just
    falls through to the live subprocess call.
    """
    try:
        head_file = vault_path / ".git" / "HEAD"
        head_text = head_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if head_text.startswith("ref:"):
        ref = head_text.split(":", 1)[1].strip()
        try:
            return (
                (vault_path / ".git" / ref)
                .read_text(
                    encoding="utf-8",
                )
                .strip()
            )
        except OSError:
            return ""
    return head_text  # already a detached SHA


def _git_log(vault_path: Path, n: int) -> str:
    """Return last n git log entries, or empty string on failure.

    Cached for ``_GIT_CACHE_TTL_S`` keyed by (vault_path, n, HEAD_SHA)
    so ``session_briefing`` does not spawn ``git log`` on every call
    when HEAD has not moved.
    """
    key = (str(vault_path), n)
    sha = _current_head_sha(vault_path)
    if sha:
        with _GIT_CACHE_LOCK:
            entry = _git_log_cache.get(key)
            if entry is not None:
                cached_at, cached_sha, value = entry
                if cached_sha == sha and time.monotonic() - cached_at < _GIT_CACHE_TTL_S:
                    return value
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{n}"],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=30,
            **_subprocess_run_kwargs(),
        )
    except Exception:
        return ""
    value = result.stdout.strip() if result.returncode == 0 else ""
    if sha:
        with _GIT_CACHE_LOCK:
            _git_log_cache[key] = (time.monotonic(), sha, value)
    return value


def _git_recent(vault_path: Path, since_days: int) -> list[str]:
    """Return vault-relative .md paths changed in the last N days via git.

    Cached for ``_GIT_CACHE_TTL_S`` keyed by (vault_path, since_days,
    HEAD_SHA) so ``vault_search(since_days=N)`` does not respawn git
    on every call when HEAD has not moved.
    """
    key = (str(vault_path), since_days)
    sha = _current_head_sha(vault_path)
    if sha:
        with _GIT_CACHE_LOCK:
            entry = _git_recent_cache.get(key)
            if entry is not None:
                cached_at, cached_sha, value = entry
                if cached_sha == sha and time.monotonic() - cached_at < _GIT_CACHE_TTL_S:
                    return list(value)
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since_days} days ago", "--name-only", "--pretty=format:"],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    value = sorted(
        {line.strip() for line in result.stdout.splitlines() if line.strip().endswith(".md")}
    )
    if sha:
        with _GIT_CACHE_LOCK:
            _git_recent_cache[key] = (time.monotonic(), sha, value)
    return list(value)


# ── Stale detection ───────────────────────────────────────────────────


def scan_project(
    project_dir: Path,
) -> tuple[list[Path], dict[Path, str | None], dict[Path, Frontmatter | None]]:
    """One pass: walk + read + parse-frontmatter every .md in the project.

    Returns ``(files, contents_by_path, frontmatters_by_path)``. Files
    that cannot be read have ``None`` content and ``None`` frontmatter.
    This is the shared primitive for tools that need multiple views of
    the same project subtree (``vault_health``, ``session_briefing``,
    ``count_stale``) — eliminates the previous 2x walk + 2x parse.
    """
    try:
        files = list(project_dir.rglob("*.md"))
    except OSError:
        return [], {}, {}
    contents: dict[Path, str | None] = {}
    frontmatters: dict[Path, Frontmatter | None] = {}
    for f in files:
        content = _safe_read(f)
        contents[f] = content
        frontmatters[f] = parse_frontmatter(content) if content is not None else None
    return files, contents, frontmatters


def count_stale_from(
    project_dir: Path,
    files: list[Path],
    frontmatters: dict[Path, Frontmatter | None],
    threshold: date,
) -> list[str]:
    """Stale-file list from a precomputed :func:`scan_project` result."""
    from hive.frontmatter import _TERMINAL_STATUSES

    stale: list[str] = []
    for f in files:
        fm = frontmatters.get(f)
        if fm is not None and fm.status in _TERMINAL_STATUSES:
            continue
        created_date = parse_date(fm.created) if fm is not None else None
        if created_date is None:
            try:
                created_date = date.fromtimestamp(f.stat().st_mtime)
            except OSError:
                continue
        if created_date < threshold:
            stale.append(f.relative_to(project_dir).as_posix())
    return stale


def count_stale(
    project_dir: Path,
    threshold: date,
) -> list[str]:
    """Return list of stale file paths in a project directory.

    Backwards-compatible wrapper: when callers already have a scan
    result, prefer :func:`count_stale_from` to avoid the second walk
    and second frontmatter parse.
    """
    files, _, frontmatters = scan_project(project_dir)
    return count_stale_from(project_dir, files, frontmatters, threshold)
