"""Shared helpers — path resolution, formatting, git ops, tracking."""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import subprocess
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import date
from typing import TYPE_CHECKING

from mcp.types import ToolAnnotations

from hive.frontmatter import extract_body, parse_date, parse_frontmatter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from hive._context import ServerContext
    from hive.clients import ClientResponse
    from hive.frontmatter import Frontmatter

_log = logging.getLogger(__name__)

_REJECT_MSG = "The host should handle this task directly."


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

_DEFAULT_SCOPES: dict[str, str] = {"projects": "10_projects", "meta": "00_meta", "work": "50_work"}

_VAULT_NOT_FOUND_MSG = (
    "Vault not found at {path}.\n\n"
    "Set the vault path when registering the MCP server:\n\n"
    "  Claude Code:\n"
    "    claude mcp add -s user hive "
    "-e VAULT_PATH=$HOME/your-vault "
    "-- uvx --upgrade hive-vault\n\n"
    "  Gemini CLI:\n"
    "    gemini mcp add -s user "
    "-e VAULT_PATH=$HOME/your-vault "
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
    vault: Path, project: str, scopes: dict[str, str] | None = None,
) -> tuple[Path, str] | None:
    """Resolve a project slug to (directory, scope_name).

    - ``_meta`` maps to the meta scope root (backward compat).
    - ``scope:project`` targets a specific scope.
    - Plain ``project`` auto-scans all scopes, first match wins.

    Supports hierarchical scopes: if the slug is not found at the first
    level of a scope directory, a breadth-first search finds it at any
    depth.  Slugs containing ``/`` are resolved as literal relative paths
    within the scope directory (no BFS).

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
        scope_dir = vault / dir_name
        return _search_scope(scope_dir, slug, explicit_scope, vault)

    # Auto-scan: iterate scopes, first match wins, skip missing dirs
    for scope_name, dir_name in scopes.items():
        if scope_name == "meta":
            continue  # meta is not a project container
        scope_dir = vault / dir_name
        result = _search_scope(scope_dir, slug, scope_name, vault)
        if result is not None:
            return result

    return None


def _search_scope(
    scope_dir: Path, slug: str, scope_name: str, vault: Path,
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
    scopes: dict[str, str] | None = None,
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
        return False, f"Ambiguous: find text appears {count} times."

    # ── Pass 2: Exact match on body (post-frontmatter) ──
    body = extract_body(content)
    frontmatter = content[: len(content) - len(body)] if body != content else ""

    count = body.count(find)
    if count == 1:
        return True, frontmatter + body.replace(find, replace, 1)
    if count > 1:
        return False, f"Ambiguous: find text appears {count} times."

    # ── Pass 3: Whitespace-normalized match on body ──
    norm_body = _normalize_ws(body)
    norm_find = _normalize_ws(find)

    if norm_find:
        count = norm_body.count(norm_find)
        if count == 1:
            return True, frontmatter + norm_body.replace(norm_find, replace, 1)
        if count > 1:
            return (
                False,
                f"Ambiguous: find text appears {count} times"
                " (after whitespace normalization).",
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
    ctx: ServerContext, tool: str, result: str, project: str = "", section: str = "",
) -> str:
    """Log a tool call and return the result unchanged."""
    ctx.tracker.log_call(tool, project, len(result.splitlines()))
    if project and section:
        is_write = tool == "vault_write"
        ctx.relevance.record_access(project, section, is_write=is_write)
    return result


# ── Git operations ─────────────────────────────────────────────────────

_GIT_LOCK = threading.Lock()

_LOCK_TIMEOUT = 30  # seconds — matches subprocess timeout


@asynccontextmanager
async def tool_span(
    tool_name: str, timeout_seconds: float,
) -> AsyncIterator[None]:
    """Enforce a timeout and log wall-clock duration for an async tool.

    Raises ``TimeoutError`` if the tool body exceeds *timeout_seconds*.
    """
    start = time.monotonic()
    try:
        async with asyncio.timeout(timeout_seconds):
            yield
    except TimeoutError:
        elapsed = time.monotonic() - start
        _log.warning(
            "%s timed out after %.1fs (limit: %.0fs)",
            tool_name, elapsed, timeout_seconds,
        )
        raise
    else:
        elapsed = time.monotonic() - start
        _log.debug("%s completed in %.1fs", tool_name, elapsed)


def _git_commit(vault_path: Path, rel_path: Path, message: str) -> None:
    """Stage a file and commit it in the vault git repo.

    This is a best-effort side-effect: failures are logged but never
    propagated, so a git problem cannot crash the MCP server or prevent
    the tool response from reaching the client.

    Serialized via ``_GIT_LOCK`` to prevent concurrent git-add/commit
    from interleaving and corrupting the index.
    """
    safe_msg = message.replace("\n", " ").replace("\r", " ")
    if not _GIT_LOCK.acquire(timeout=_LOCK_TIMEOUT):
        _log.warning("git commit skipped for %s: lock timeout (%ds)", rel_path, _LOCK_TIMEOUT)
        return
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
    finally:
        _GIT_LOCK.release()


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


# ── Stale detection ───────────────────────────────────────────────────


def count_stale(
    project_dir: Path, threshold: date,
) -> list[str]:
    """Return list of stale file paths in a project directory."""
    from hive.frontmatter import _TERMINAL_STATUSES

    stale: list[str] = []
    for f in project_dir.rglob("*.md"):
        content = _safe_read(f)
        if content is None:
            continue
        fm = parse_frontmatter(content)
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
