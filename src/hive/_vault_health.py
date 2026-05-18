"""Vault health operations — health reports and validation."""

from __future__ import annotations

import contextlib
import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

from hive._helpers import (
    _READ_ONLY,
    SECTION_SHORTCUTS,
    _resolve_project_dir,
    _safe_read,
    _vault_guard,
    count_stale,
    project_not_found,
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
    from pathlib import Path

    from fastmcp import FastMCP

    from hive._context import ServerContext

_ALL_CHECKS = frozenset({"frontmatter", "stale", "links"})
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*?)?\]\]")
_FENCED_CODE_RE = re.compile(
    r"(?ms)^(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^(?P=fence)[^\n]*$",
)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _strip_code(text: str) -> str:
    """Remove fenced and inline code so wikilink-like syntax inside code
    (e.g. bash regex `[[:space:]]`, shell `[[ -z "$1" ]]`) is not parsed
    as a wikilink.
    """
    text = _FENCED_CODE_RE.sub("", text)
    return _INLINE_CODE_RE.sub("", text)


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
    return [
        (name, paths) for name, paths in sorted(name_paths.items())
        if len(paths) > 1
    ]


def health_report_text(ctx: ServerContext, filter_project: str = "") -> str:
    """Build health report text (shared by resource and tool)."""
    stale_threshold = date.today() - timedelta(days=ctx.stale_days)
    lines = ["# Vault Health Report", ""]
    found_any = False

    for scope_name, dir_name in ctx.scopes.items():
        if scope_name == "meta":
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
            try:
                md_files = list(project_dir.rglob("*.md"))
            except OSError:
                md_files = []
            total_lines = 0
            for f in md_files:
                content = _safe_read(f)
                if content is not None:
                    total_lines += len(content.splitlines())
            stale_files = count_stale(project_dir, stale_threshold)
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
                    f"- Stale files (>{ctx.stale_days}d): "
                    f"{', '.join(sorted(stale_files))}"
                )
            lines.append("")

    # ── Duplicate name warnings ──
    dup_lines: list[str] = []
    for scope_name, dir_name in ctx.scopes.items():
        if scope_name == "meta":
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

    if not found_any:
        return "No projects found in vault."
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
    ) -> str:
        """Return vault health metrics, validation, and optional usage analytics.

        Without parameters, returns a health summary for all projects.
        When checks are specified, runs drift detection (frontmatter, stale, links).
        When include_usage is True, appends tool usage analytics.

        Args:
            project: Project slug to validate. Empty = all projects.
            checks: Validation checks to run. Empty = health summary only. Options: frontmatter, stale, links.
            max_issues: Maximum validation issues to report. Default 50.
            include_usage: Append vault tool usage analytics. Default False.
            usage_days: Usage look-back window in days. Default 30.
        """  # noqa: E501
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_health", guard, project)

        parts: list[str] = []

        if not checks:
            parts.append(health_report_text(ctx, filter_project=project))
        else:
            active_checks = frozenset(checks) & _ALL_CHECKS
            unknown = frozenset(checks) - _ALL_CHECKS
            if unknown:
                valid_names = ", ".join(sorted(_ALL_CHECKS))
                return track(
                    ctx, "vault_health",
                    f"Unknown check(s): {', '.join(sorted(unknown))}. "
                    f"Valid: {valid_names}",
                    project,
                )

            project_dirs: list[tuple[Path, str]] = []
            if project:
                resolved = _resolve_project_dir(
                    ctx.vault, project, ctx.scopes,
                )
                if resolved is None:
                    return track(
                        ctx, "vault_health",
                        project_not_found(project),
                        project,
                    )
                project_dirs.append(resolved)
            else:
                for scope_name, dir_name in ctx.scopes.items():
                    if scope_name == "meta":
                        continue
                    scope_dir = ctx.vault / dir_name
                    if not scope_dir.is_dir():
                        continue
                    for d in sorted(scope_dir.iterdir()):
                        if d.is_dir():
                            project_dirs.append((d, scope_name))

            if not project_dirs:
                return track(
                    ctx, "vault_health", "No projects found in vault.",
                )

            all_stems: set[str] = set()
            all_paths: set[str] = set()
            if "links" in active_checks:
                for pd, _ in project_dirs:
                    for f in pd.rglob("*.md"):
                        all_stems.add(f.stem)
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
                            f"[error] {proj_name}/{rel}: "
                            "File unreadable (I/O or encoding error)",
                        )
                        continue

                    fm = parse_frontmatter(content)
                    in_memory_dir = "memory" in (
                        f.relative_to(project_dir).parts
                    )

                    if "frontmatter" in active_checks and not in_memory_dir:
                        if fm is None:
                            issues.append(
                                f"[error] {proj_name}/{rel}: "
                                "Missing or invalid frontmatter"
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
                                f"[warning] {proj_name}/{rel}: "
                                f"Unparseable date: '{fm.created}'"
                            )

                    if (
                        "stale" in active_checks
                        and fm is not None
                        and fm.status not in _TERMINAL_STATUSES
                    ):
                            created_date = (
                                parse_date(fm.created)
                                if fm.created
                                else None
                            )
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
                            if (
                                target not in all_stems
                                and target not in all_paths
                            ):
                                issues.append(
                                    f"[warning] {proj_name}/{rel}: "
                                    f"Broken link [[{target}]]"
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
                errors = sum(
                    1 for i in issues if i.startswith("[error]")
                )
                warnings = sum(
                    1 for i in issues if i.startswith("[warning]")
                )
                header = (
                    f"Found {len(issues)} issues "
                    f"({errors} errors, {warnings} warnings)"
                )
                if len(issues) >= max_issues:
                    header += (
                        f" — truncated at {max_issues}, more may exist"
                    )
                validate_lines = [header, ""]
                validate_lines.extend(issues)
                parts.append("\n".join(validate_lines))

        if include_usage:
            stats = ctx.tracker.stats(usage_days)
            if stats["total_calls"] == 0:
                parts.append(
                    f"\nNo vault tool calls recorded "
                    f"in the last {usage_days} days."
                )
            else:
                usage_parts: list[str] = [
                    f"\n# Vault Usage (last {usage_days} days)",
                    "",
                    f"- Total calls: {stats['total_calls']}",
                    f"- Total response lines: "
                    f"{stats['total_response_lines']}",
                    f"- Estimated tokens served: "
                    f"~{stats['total_response_lines'] * 10}",
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

        return track(ctx, "vault_health", "\n".join(parts), project)
