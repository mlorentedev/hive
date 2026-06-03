"""Vault health operations — health reports and validation."""

from __future__ import annotations

import contextlib
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

from hive._helpers import (
    _READ_ONLY,
    META_SCOPE,
    SECTION_SHORTCUTS,
    _resolve_project_dir,
    _safe_read,
    _strip_code,
    _vault_guard,
    count_stale_from,
    detect_obsidian_git,
    project_not_found,
    scan_project,
    track,
    wrap_sync_tool,
)
from hive.frontmatter import (
    _TERMINAL_STATUSES,
    extract_body,
    parse_date,
    parse_frontmatter,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from hive._context import ServerContext

_ALL_CHECKS = frozenset({"frontmatter", "stale", "links"})
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*?)?\]\]")
_POSIX_CLASS_RE = re.compile(r"^:[a-z]+:$")


def _hive_version() -> str:
    """Return the installed hive-vault version, or a marker for editable installs."""
    try:
        return metadata.version("hive-vault")
    except metadata.PackageNotFoundError:
        return "unknown"


def identity_block_text(ctx: ServerContext) -> str:
    """Always-on server identity block — issue #109 acceptance criterion 1.

    Renders ~5 lines: version, python, vault_path, backends (presence
    booleans only — never API keys), started_at. Stable across calls
    since started_at is captured at server construction.
    """
    # Ollama backend: report the cached availability probe. Unprobed →
    # False (we cannot claim presence we haven't verified). OpenRouter:
    # presence boolean derived from whether the client was constructed
    # (which requires an api_key) — the key itself is never embedded.
    ollama_present = bool(
        getattr(ctx.ollama, "_availability_cached", None),
    )
    openrouter_present = ctx.openrouter is not None
    backends = (
        '{"ollama": '
        + ("true" if ollama_present else "false")
        + ', "openrouter": '
        + ("true" if openrouter_present else "false")
        + "}"
    )
    return "\n".join(
        [
            "## server",
            f"- version: {_hive_version()}",
            f"- python: {sys.version.split()[0]}",
            f"- vault_path: {ctx.vault}",
            f"- backends: {backends}",
            f"- started_at: {ctx.started_at_iso}",
            "",
        ]
    )


def _registered_tool_names(mcp: FastMCP) -> list[str]:
    """Extract sorted tool names from the FastMCP local provider.

    Falls back to [] if the internal structure changes — runtime block
    must never crash vault_health.
    """
    try:
        components = getattr(mcp.providers[0], "_components", {})
    except (AttributeError, IndexError):
        return []
    names: set[str] = set()
    for key in components:
        if not isinstance(key, str) or not key.startswith("tool:"):
            continue
        # key shape is `tool:<name>@<scope>` — strip both prefix and suffix.
        rest = key[len("tool:") :]
        names.add(rest.split("@", 1)[0])
    return sorted(names)


def runtime_block_text(ctx: ServerContext, mcp: FastMCP) -> str:
    """Opt-in runtime block — issue #109 + HIVE-115 multi-process telemetry.

    Surfaces (HIVE-115 / ADR-009 + ADR-010):
    - ``wal_size_bytes`` — sum of ``*.db-wal`` under hive state dir
    - ``competing_pid_count`` — other ``hive-vault`` processes (same user)
    - ``last_git_lock_wait_ms`` — rolling-100 mean + p99
    - ``obsidian_git_present`` — external committer detection
    """
    from hive import _helpers
    from hive.config import settings as _settings

    uptime_s = max(0.0, time.monotonic() - ctx.started_at_monotonic)
    tools = _registered_tool_names(mcp)
    period = datetime.now(UTC).strftime("%Y-%m")
    stats = ctx.budget.month_stats(ctx.openrouter_budget)

    # HIVE-115 telemetry. Each computation is defensive: a psutil error
    # or stat failure must NEVER prevent vault_health from returning.
    state_dir = Path(_settings.db_path).parent
    wal_size = _helpers._compute_wal_size_bytes(state_dir)
    competing = _helpers._count_competing_hive_processes()
    lock_stats = _helpers._git_lock_stats_snapshot()
    obsidian = _helpers.detect_obsidian_git(ctx.vault) is not None

    # HIVE-116 PR-2 / AC-8: cooperative-filelock eviction telemetry.
    # Defensive: a tracker DB hiccup must not break vault_health.
    eviction_count_30d = 0
    eviction_last_iso: str | None = None
    try:
        eviction_count_30d = ctx.lock_eviction.count_last_30d()
        eviction_last_iso = ctx.lock_eviction.last_iso()
    except Exception:  # noqa: BLE001
        pass

    lines = [
        "## runtime",
        f"- uptime_s: {uptime_s:.1f}",
        f"- tools_registered: {len(tools)} ({', '.join(tools) if tools else '<empty>'})",
        f"- wal_size_bytes: {wal_size}",
        f"- competing_pid_count: {competing}",
        "- last_git_lock_wait_ms:",
        f"  - mean: {lock_stats['mean_ms']:.1f}",
        f"  - p99: {lock_stats['p99_ms']:.1f}",
        f"  - samples: {lock_stats['sample_count']}",
        f"- obsidian_git_present: {str(obsidian).lower()}",
        "- lock_eviction:",
        f"  - count_30d: {eviction_count_30d}",
        f"  - last_iso: {eviction_last_iso or 'null'}",
        "- openrouter_budget:",
        f"  - spent_usd: {float(stats['spent']):.4f}",
        f"  - cap_usd: {ctx.openrouter_budget}",
        f"  - period: {period}",
        "",
    ]
    return "\n".join(lines)


def _find_duplicate_names(scope_dir: Path) -> list[tuple[str, list[str]]]:
    """Find directory names that appear at multiple depths within a scope.

    Returns a list of (name, [relative_paths]) for duplicated names.
    """
    from collections import defaultdict

    name_paths: dict[str, list[str]] = defaultdict(list)
    try:
        for d in scope_dir.rglob("*"):
            if d.is_dir():
                rel = d.relative_to(scope_dir).as_posix()
                name_paths[d.name].append(rel)
    except OSError:
        return []
    return [(name, paths) for name, paths in sorted(name_paths.items()) if len(paths) > 1]


def health_report_text(ctx: ServerContext, filter_project: str = "") -> str:
    """Build health report text (shared by resource and tool).

    Always prepends the ``## server`` identity block (issue #109) so the
    ``hive://health`` resource and ``vault_health()`` tool agree on the
    static metadata exposed to MCP hosts.
    """
    stale_threshold = date.today() - timedelta(days=ctx.stale_days)
    lines: list[str] = [
        "# Vault Health Report",
        "",
        identity_block_text(ctx),
    ]
    found_any = False

    for scope_name, dir_name in ctx.scopes.items():
        if scope_name == META_SCOPE:
            continue
        scope_dir = ctx.vault / dir_name
        if not scope_dir.is_dir():
            continue
        try:
            projects = sorted(d for d in scope_dir.iterdir() if d.is_dir())
        except OSError:
            continue
        for project_dir in projects:
            if filter_project and project_dir.name != filter_project:
                continue
            found_any = True
            md_files, contents, frontmatters = scan_project(project_dir)
            total_lines = sum(len(c.splitlines()) for c in contents.values() if c is not None)
            stale_files = count_stale_from(
                project_dir,
                md_files,
                frontmatters,
                stale_threshold,
            )
            missing = [
                s for s, fname in SECTION_SHORTCUTS.items() if not (project_dir / fname).exists()
            ]

            lines.append(f"## {scope_name}/{project_dir.name}")
            lines.append(f"- Files: {len(md_files)}")
            lines.append(f"- Total lines: {total_lines}")
            if missing:
                lines.append(f"- Missing sections: {', '.join(missing)}")
            if stale_files:
                lines.append(
                    f"- Stale files (>{ctx.stale_days}d): {', '.join(sorted(stale_files))}"
                )
            lines.append("")

    # ── Duplicate name warnings ──
    dup_lines: list[str] = []
    for scope_name, dir_name in ctx.scopes.items():
        if scope_name == META_SCOPE:
            continue
        scope_dir = ctx.vault / dir_name
        if not scope_dir.is_dir():
            continue
        duplicates = _find_duplicate_names(scope_dir)
        for name, paths in duplicates:
            dup_lines.append(
                f"- **{scope_name}**: '{name}' exists at: "
                + ", ".join(paths)
                + f" (resolved to: {paths[0]})"
            )
    if dup_lines:
        found_any = True
        lines.append("## Duplicate Names (BFS resolution warning)")
        lines.extend(dup_lines)
        lines.append("")

    # ── External committer (HIVE-104 Fase B1) ──
    obsidian_git = detect_obsidian_git(ctx.vault)
    if obsidian_git is not None:
        found_any = True
        lines.append("## external_committer")
        lines.append('- name: "obsidian-git"')
        lines.append(f"- commit_interval: {obsidian_git['commit_interval']} min")
        lines.append(
            "- note: obsidian-git is active — `vault_write(commit=False)` "
            "and `vault_patch(commit=False)` are safe; the plugin will "
            "auto-commit on its interval."
        )
        lines.append(
            "- warning: obsidian-git's auto-commit timer races Hive's semantic "
            "commits to vault master (merge commits, rejected pushes — #174). "
            "Set autoSaveInterval=0 + syncMethod=rebase so Hive stays the single "
            "deliberate committer (see docs/adr/adr-014)."
        )
        lines.append("")

    # ── Ghost-response counter (HIVE-104 Fase C + HIVE-115 PR-3 source) ──
    from hive._compat import GHOST_RESPONSES

    snap = GHOST_RESPONSES.snapshot()
    if isinstance(snap.get("total"), int) and snap["total"] > 0:  # type: ignore[operator]
        found_any = True
        lines.append("## ghost_responses")
        lines.append(f"- total: {snap['total']}")
        lines.append(f"- last_seen: {snap['last_seen']}")
        lines.append(f"- last_tool: {snap['last_tool'] or '<unknown>'}")
        by_source = snap.get("by_source")
        if isinstance(by_source, dict) and by_source:
            breakdown = ", ".join(f"{src}={count}" for src, count in sorted(by_source.items()))
            lines.append(f"- by_source: {breakdown}")
        lines.append(
            "- note: ErrorData ack does NOT imply rollback — verify state "
            "via `vault_query`, do not retry."
        )
        lines.append("")

    if not found_any:
        lines.append("No projects found in vault.")
    return "\n".join(lines)


def register_vault_health(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register vault health tools on the MCP server."""

    @mcp.tool(annotations=_READ_ONLY)
    @wrap_sync_tool(ctx, "vault_health")
    def vault_health(
        project: str = "",
        checks: list[str] = [],  # noqa: B006
        max_issues: int = 50,
        include_usage: bool = False,
        usage_days: int = 30,
        include_runtime: bool = False,
    ) -> str:
        """Return vault health metrics, validation, and optional usage analytics.

        Always emits the ``## server`` identity block (version, python,
        vault path, backend presence, started_at) at the top.

        Without parameters, returns a health summary for all projects.
        When checks are specified, runs drift detection (frontmatter, stale, links).
        When include_usage is True, appends tool usage analytics.
        When include_runtime is True, appends runtime metadata (uptime, tools, budget).

        Args:
            project: Project slug to validate. Empty = all projects.
            checks: Validation checks to run. Empty = health summary only. Options: frontmatter, stale, links.
            max_issues: Maximum validation issues to report. Default 50.
            include_usage: Append vault tool usage analytics. Default False.
            usage_days: Usage look-back window in days. Default 30.
            include_runtime: Append runtime metadata block. Default False.
        """  # noqa: E501
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_health", guard, project)

        parts: list[str] = []

        if not checks:
            parts.append(health_report_text(ctx, filter_project=project))
        else:
            # Identity block is part of every successful vault_health
            # response — prepend before the validation summary so MCP
            # hosts can read the running server version even when only
            # checks=[...] is requested.
            parts.append("# Vault Health Report\n\n" + identity_block_text(ctx))
            active_checks = frozenset(checks) & _ALL_CHECKS
            unknown = frozenset(checks) - _ALL_CHECKS
            if unknown:
                valid_names = ", ".join(sorted(_ALL_CHECKS))
                return track(
                    ctx,
                    "vault_health",
                    f"Unknown check(s): {', '.join(sorted(unknown))}. Valid: {valid_names}",
                    project,
                )

            project_dirs: list[tuple[Path, str]] = []
            if project:
                resolved = _resolve_project_dir(
                    ctx.vault,
                    project,
                    ctx.scopes,
                )
                if resolved is None:
                    return track(
                        ctx,
                        "vault_health",
                        project_not_found(project),
                        project,
                    )
                project_dirs.append(resolved)
            else:
                for scope_name, dir_name in ctx.scopes.items():
                    if scope_name == META_SCOPE:
                        continue
                    scope_dir = ctx.vault / dir_name
                    if not scope_dir.is_dir():
                        continue
                    # Only directories are projects. Files directly under a
                    # scope root (e.g. an ``80_agents/_index.md`` landing page)
                    # are intentionally skipped — the ``_index.md`` convention
                    # is optional, so health does not enumerate or validate it
                    # (decision: #159 item 3; enforcing it would false-flag
                    # vaults that don't use the convention).
                    for d in sorted(scope_dir.iterdir()):
                        if d.is_dir():
                            project_dirs.append((d, scope_name))

            if not project_dirs:
                return track(
                    ctx,
                    "vault_health",
                    "No projects found in vault.",
                )

            all_stems: set[str] = set()
            all_paths: set[str] = set()
            if "links" in active_checks:
                # Index every .md in the vault under all 4 forms Obsidian
                # accepts: bare stem, vault-rooted full path, vault-rooted
                # without scope dir, and project-relative. Meta-scope files
                # also get an `_meta/`-prefixed form (the slug Obsidian/Hive
                # users type in wikilinks).
                scope_dir_names = set(ctx.scopes.values())
                meta_dir_name = ctx.scopes.get(META_SCOPE)
                try:
                    md_files = list(ctx.vault.rglob("*.md"))
                except OSError:
                    md_files = []
                for f in md_files:
                    if not f.is_file():
                        continue
                    all_stems.add(f.stem)
                    with contextlib.suppress(ValueError):
                        vault_rel = f.relative_to(ctx.vault).with_suffix("").as_posix()
                        all_paths.add(vault_rel)
                        if "/" in vault_rel:
                            leading, rest = vault_rel.split("/", 1)
                            if leading in scope_dir_names:
                                all_paths.add(rest)
                                if leading == meta_dir_name:
                                    all_paths.add(f"_meta/{rest}")
                    for pd, _ in project_dirs:
                        with contextlib.suppress(ValueError):
                            all_paths.add(
                                f.relative_to(pd).with_suffix("").as_posix(),
                            )

            issues: list[str] = []
            stale_threshold = date.today() - timedelta(days=ctx.stale_days)

            for project_dir, _scope_name in project_dirs:
                proj_name = project_dir.name
                for f in sorted(project_dir.rglob("*.md")):
                    if len(issues) >= max_issues:
                        break
                    rel = f.relative_to(project_dir).as_posix()
                    content = _safe_read(f)
                    if content is None:
                        issues.append(
                            f"[error] {proj_name}/{rel}: File unreadable (I/O or encoding error)",
                        )
                        continue

                    fm = parse_frontmatter(content)
                    in_memory_dir = "memory" in (f.relative_to(project_dir).parts)

                    if "frontmatter" in active_checks and not in_memory_dir:
                        if fm is None:
                            issues.append(
                                f"[error] {proj_name}/{rel}: Missing or invalid frontmatter"
                            )
                            continue
                        missing_fields = {"id", "type", "status"} - fm.raw.keys()
                        if missing_fields:
                            issues.append(
                                f"[error] {proj_name}/{rel}: "
                                f"Frontmatter missing fields: "
                                f"{', '.join(sorted(missing_fields))}"
                            )
                        if fm.created and parse_date(fm.created) is None:
                            issues.append(
                                f"[warning] {proj_name}/{rel}: Unparseable date: '{fm.created}'"
                            )

                    if (
                        "stale" in active_checks
                        and fm is not None
                        and fm.status not in _TERMINAL_STATUSES
                    ):
                        created_date = parse_date(fm.created) if fm.created else None
                        if created_date is None:
                            try:
                                created_date = date.fromtimestamp(
                                    f.stat().st_mtime,
                                )
                            except OSError:
                                continue
                        if created_date < stale_threshold:
                            issues.append(
                                f"[warning] {proj_name}/{rel}: "
                                f"Stale (active since "
                                f"{created_date.isoformat()}, "
                                f">{ctx.stale_days}d)"
                            )

                    if "links" in active_checks:
                        body = _strip_code(extract_body(content))
                        for m in _WIKILINK_RE.finditer(body):
                            target = m.group(1).strip().rstrip("\\").strip()
                            if _POSIX_CLASS_RE.match(target):
                                continue
                            if target not in all_stems and target not in all_paths:
                                issues.append(
                                    f"[warning] {proj_name}/{rel}: Broken link [[{target}]]"
                                )
                                if len(issues) >= max_issues:
                                    break

                if len(issues) >= max_issues:
                    break

            if not issues:
                scope_label = f" for '{project}'" if project else ""
                parts.append(
                    f"Vault clean{scope_label}. "
                    f"0 issues found "
                    f"({', '.join(sorted(active_checks))})."
                )
            else:
                errors = sum(1 for i in issues if i.startswith("[error]"))
                warnings = sum(1 for i in issues if i.startswith("[warning]"))
                header = f"Found {len(issues)} issues ({errors} errors, {warnings} warnings)"
                if len(issues) >= max_issues:
                    header += f" — truncated at {max_issues}, more may exist"
                validate_lines = [header, ""]
                validate_lines.extend(issues)
                parts.append("\n".join(validate_lines))

        if include_usage:
            stats = ctx.tracker.stats(usage_days)
            if stats["total_calls"] == 0:
                parts.append(f"\nNo vault tool calls recorded in the last {usage_days} days.")
            else:
                usage_parts: list[str] = [
                    f"\n# Vault Usage (last {usage_days} days)",
                    "",
                    f"- Total calls: {stats['total_calls']}",
                    f"- Total response lines: {stats['total_response_lines']}",
                    f"- Estimated tokens served: ~{stats['total_response_lines'] * 10}",
                    "",
                ]
                if stats["by_tool"]:
                    usage_parts.append("## By Tool")
                    for tool_name, count in stats["by_tool"].items():
                        usage_parts.append(f"- {tool_name}: {count} calls")
                    usage_parts.append("")
                if stats["by_project"]:
                    usage_parts.append("## By Project")
                    for proj, count in stats["by_project"].items():
                        usage_parts.append(f"- {proj}: {count} calls")
                parts.append("\n".join(usage_parts))

        if include_runtime:
            parts.append(runtime_block_text(ctx, mcp))

        return track(ctx, "vault_health", "\n".join(parts), project)
