"""Vault read operations — list, query, search, session briefing."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

from hive._helpers import (
    _READ_ONLY,
    SECTION_SHORTCUTS,
    _format_metadata,
    _git_log,
    _git_recent,
    _resolve_file,
    _resolve_project_dir,
    _safe_read,
    _score_file,
    _truncate,
    count_stale,
    track,
)
from hive.frontmatter import parse_date, parse_frontmatter

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from hive._context import ServerContext


def list_projects_text(ctx: ServerContext) -> str:
    """Build project listing text (shared by resource and tool)."""
    lines = ["# Vault Projects", ""]
    found_any = False
    for scope_name, dir_name in ctx.scopes.items():
        if scope_name == "meta":
            continue
        scope_dir = ctx.vault / dir_name
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


def register_vault_read(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register vault read tools on the MCP server."""

    @mcp.tool(annotations=_READ_ONLY)
    def vault_list(
        project: str = "",
        path: str = "",
        pattern: str = "",
    ) -> str:
        """List vault projects, or files within a project.

        When called without arguments, lists all available projects.
        When called with a project, lists files in that project directory.

        Args:
            project: Project slug. Empty = list all projects.
            path: Subdirectory within the project. Empty = project root.
            pattern: Glob pattern to filter files (e.g. 'adr-*', '*.md').
        """
        if not project:
            return track(ctx, "vault_list", list_projects_text(ctx))

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(ctx, "vault_list",
                         f"Project '{project}' not found in vault.", project)
        project_dir, _ = resolved

        from hive._helpers import _check_path_boundary

        target = project_dir / path if path else project_dir
        boundary_error = _check_path_boundary(target, ctx.vault)
        if boundary_error:
            return track(ctx, "vault_list", boundary_error, project)
        if not target.is_dir():
            return track(ctx, "vault_list",
                         f"Path '{path}' not found in project '{project}'.",
                         project)

        lines: list[str] = [
            f"# Files: {project}/{path}" if path else f"# Files: {project}",
            "",
        ]

        max_list_results = 500
        if pattern:
            files = sorted(f for f in target.rglob(pattern) if f.is_file())
            if not files:
                return track(
                    ctx, "vault_list",
                    f"No files matching '{pattern}' in {project}/{path}.",
                    project,
                )
            for f in files[:max_list_results]:
                rel_f = f.relative_to(target)
                lines.append(f"- {rel_f}")
        else:
            dirs = sorted(d for d in target.iterdir() if d.is_dir())
            for d in dirs:
                lines.append(f"- {d.name}/")
            files = sorted(f for f in target.iterdir() if f.is_file())
            for f in files:
                lines.append(f"- {f.name}")

        return track(ctx, "vault_list", "\n".join(lines), project, path)

    @mcp.tool(annotations=_READ_ONLY)
    def vault_query(
        project: str,
        section: str = "context",
        path: str = "",
        max_lines: int = 0,
        include_metadata: bool = False,
    ) -> str:
        """Read content from a vault project — use instead of direct filesystem access.

        Args:
            project: Project slug (directory under 10_projects/), or '_meta' for 00_meta/.
            section: Shortcut name (context, tasks, roadmap, lessons). Ignored if path is set.
            path: Relative path to a specific .md file within the project. Overrides section.
            max_lines: Maximum lines to return. 0 = unlimited.
            include_metadata: Prepend a structured metadata line from YAML frontmatter.
        """
        resolved_section = path or section
        result = _resolve_file(ctx.vault, project, section, path, ctx.scopes)
        if isinstance(result, str):
            return track(ctx, "vault_query", result, project, resolved_section)
        filepath = result

        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            return track(ctx, "vault_query",
                         f"File I/O error: {exc}", project, resolved_section)

        if include_metadata:
            fm = parse_frontmatter(content)
            meta = _format_metadata(fm)
            if meta:
                content = f"**Metadata:** {meta}\n\n{content}"

        return track(ctx, "vault_query", _truncate(content, max_lines),
                     project, resolved_section)

    @mcp.tool(annotations=_READ_ONLY)
    def vault_search(
        query: str = "",
        max_lines: int = 500,
        type_filter: str = "",
        status_filter: str = "",
        tag_filter: str = "",
        use_regex: bool = False,
        ranked: bool = False,
        max_results: int = 10,
        since_days: int = 0,
        project: str = "",
    ) -> str:
        """Search the vault: full-text, ranked, or recent changes.

        Default mode: flat full-text search across all vault files.
        Ranked mode (ranked=True): results scored by relevance.
        Recent mode (since_days>0): files changed in the last N days.

        Args:
            query: Text to search for (case-insensitive).
            max_lines: Maximum output lines. Default 500.
            type_filter: Only files whose frontmatter type matches.
            status_filter: Only files whose frontmatter status matches.
            tag_filter: Only files that have this frontmatter tag.
            use_regex: Treat query as regex. Default False.
            ranked: Score results by relevance. Default False.
            max_results: Max files when ranked. Default 10.
            since_days: Show recent changes (0 = disabled). Default 0.
            project: Filter to this project (recent mode only).
        """
        if since_days < 0:
            return track(
                ctx, "vault_search", "since_days must be a positive number.",
            )

        # ── Recent mode ──
        if since_days > 0:
            git_paths = set(_git_recent(ctx.vault, since_days))
            cutoff = date.today() - timedelta(days=since_days)
            for md_file in ctx.vault.rglob("*.md"):
                content = _safe_read(md_file)
                if content is None:
                    continue
                fm = parse_frontmatter(content)
                if fm is None:
                    continue
                created = parse_date(fm.created)
                if created is not None and created >= cutoff:
                    git_paths.add(
                        md_file.relative_to(ctx.vault).as_posix(),
                    )

            if project:
                resolved = _resolve_project_dir(
                    ctx.vault, project, ctx.scopes,
                )
                if resolved is not None:
                    prefix = (
                        resolved[0].relative_to(ctx.vault).as_posix()
                        + "/"
                    )
                    git_paths = {
                        p for p in git_paths if p.startswith(prefix)
                    }
                else:
                    git_paths = set()

            if not git_paths:
                return track(
                    ctx, "vault_search",
                    f"No changes found in the last {since_days} days.",
                    project,
                )

            rlines: list[str] = [
                f"# Recent Changes (last {since_days} days)", "",
            ]
            for rel_path in sorted(git_paths):
                full = ctx.vault / rel_path
                if not full.exists():
                    rlines.append(f"- {rel_path} (deleted)")
                    continue
                try:
                    content = full.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    rlines.append(f"- {rel_path}")
                    continue
                fm = parse_frontmatter(content)
                meta = _format_metadata(fm)
                if meta:
                    rlines.append(f"- {rel_path} [{meta}]")
                else:
                    rlines.append(f"- {rel_path}")

            output = "\n".join(rlines)
            return track(
                ctx, "vault_search", _truncate(output, max_lines), project,
            )

        # ── Ranked mode ──
        if ranked:
            if not query:
                return track(
                    ctx, "vault_search", "Query is required for ranked search.",
                )
            query_lower = query.lower()
            today = date.today()
            scored: list[tuple[float, str, str, list[str]]] = []

            for md_file in sorted(ctx.vault.rglob("*.md")):
                content = _safe_read(md_file)
                if content is None:
                    continue
                matching = [
                    ln.strip()
                    for ln in content.splitlines()
                    if query_lower in ln.lower()
                ]
                if not matching:
                    continue
                fm = parse_frontmatter(content)
                score = _score_file(len(matching), fm, today)
                rel = md_file.relative_to(ctx.vault).as_posix()
                meta = _format_metadata(fm)
                scored.append((score, rel, meta, matching))

            if not scored:
                return track(
                    ctx, "vault_search",
                    f"No matches found for '{query}'.",
                )

            scored.sort(key=lambda x: x[0], reverse=True)
            scored = scored[:max_results]

            results: list[str] = [
                f"# Ranked Search: '{query}'", "",
            ]
            for score, rel, meta, matching in scored:
                meta_part = f" [{meta}]" if meta else ""
                results.append(
                    f"### {rel} (score: {score:.1f}){meta_part}",
                )
                for line in matching[:5]:
                    results.append(f"  - {line}")

            output = "\n".join(results)
            return track(
                ctx, "vault_search", _truncate(output, max_lines),
            )

        # ── Standard search ──
        if not query:
            return track(
                ctx, "vault_search", "Query is required for search.",
            )

        if use_regex:
            if len(query) > 200:
                return track(
                    ctx, "vault_search",
                    "Regex pattern too long (max 200 chars).",
                )
            try:
                rx_pattern = re.compile(query, re.IGNORECASE)
            except re.error as exc:
                return track(
                    ctx, "vault_search",
                    f"Invalid regex '{query}': {exc}",
                )

        flat_results: list[str] = []
        query_lower = query.lower()
        has_filters = bool(type_filter or status_filter or tag_filter)

        for md_file in sorted(ctx.vault.rglob("*.md")):
            content = _safe_read(md_file)
            if content is None:
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
                    line.strip()
                    for line in content.splitlines()
                    if rx_pattern.search(line)
                ]
            else:
                matching_lines = [
                    line.strip()
                    for line in content.splitlines()
                    if query_lower in line.lower()
                ]
            if matching_lines:
                file_rel = md_file.relative_to(ctx.vault)
                meta_str = _format_metadata(fm)
                meta = f" [{meta_str}]" if meta_str else ""
                flat_results.append(f"### {file_rel}{meta}")
                for line in matching_lines[:5]:
                    flat_results.append(f"  - {line}")

        if not flat_results:
            return track(
                ctx, "vault_search", f"No matches found for '{query}'.",
            )

        output = f"# Search: '{query}'\n\n" + "\n".join(flat_results)
        return track(ctx, "vault_search", _truncate(output, max_lines))

    @mcp.tool(annotations=_READ_ONLY)
    def session_briefing(project: str) -> str:
        """Call at the start of every new session to load project context.

        Assembles active tasks, recent lessons, git activity, and project
        health into a single response — replaces 3-4 manual tool calls.

        Args:
            project: Project slug (directory under 10_projects/).
        """
        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(ctx, "session_briefing",
                         f"Project '{project}' not found.", project)
        project_dir, _ = resolved

        # Decay stale relevance scores at session start
        ctx.relevance.apply_decay()

        # Build sections as keyed blocks
        sections: dict[str, str] = {}

        # Tasks
        task_result = _resolve_file(ctx.vault, project, "tasks", "", ctx.scopes)
        if not isinstance(task_result, str):
            task_content = _safe_read(task_result)
            if task_content is not None:
                ctx.relevance.record_access(project, "tasks")
                body = _truncate(task_content, 50)
                sections["tasks"] = f"## Active Tasks\n{body}"

        # Lessons
        lessons_result = _resolve_file(
            ctx.vault, project, "lessons", "", ctx.scopes,
        )
        if not isinstance(lessons_result, str):
            lessons_content = _safe_read(lessons_result)
            if lessons_content is not None:
                ctx.relevance.record_access(project, "lessons")
                lines = lessons_content.splitlines()
                tail = lines[-30:] if len(lines) > 30 else lines
                sections["lessons"] = "## Recent Lessons\n" + "\n".join(tail)

        # Git activity (always shown, not ranked)
        git_block = "## Recent Vault Activity\n"
        git_block += _git_log(ctx.vault, 5) or "(no git history available)"

        # Health (always shown, not ranked)
        md_files = list(project_dir.rglob("*.md"))
        stale_threshold = date.today() - timedelta(days=ctx.stale_days)
        stale_count = len(count_stale(project_dir, stale_threshold))
        health_lines = [f"- Files: {len(md_files)}"]
        if stale_count:
            health_lines.append(f"- Stale: {stale_count}")
        health_block = "## Project Health\n" + "\n".join(health_lines)

        # Order rankable sections by relevance (adaptive)
        default_order = ["tasks", "lessons"]
        scores = ctx.relevance.get_scores(project)
        if scores:
            ranked_sections = sorted(
                sections.keys(), key=lambda s: scores.get(s, 0.0), reverse=True,
            )
        else:
            ranked_sections = [s for s in default_order if s in sections]

        # Assemble output: header → ranked sections → fixed sections
        parts: list[str] = [f"# Session Briefing — {project}", ""]
        for key in ranked_sections:
            parts.append(sections[key])
            parts.append("")
        parts.append(git_block)
        parts.append("")
        parts.append(health_block)

        return track(ctx, "session_briefing", "\n".join(parts), project)
