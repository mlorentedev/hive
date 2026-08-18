"""Vault read operations — list, query, search, session briefing."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

from hive._helpers import (
    _READ_ONLY,
    META_SCOPE,
    SECTION_SHORTCUTS,
    _bounded_sync_call,
    _format_metadata,
    _git_log,
    _git_recent,
    _resolve_file,
    _resolve_project_dir,
    _safe_read,
    _score_file,
    _truncate,
    _vault_guard,
    count_stale_from,
    extract_lesson_headings,
    find_lesson_heading,
    format_io_error,
    project_not_found,
    scan_project,
    track,
    wrap_sync_tool,
)
from hive.frontmatter import extract_body, parse_date, parse_frontmatter

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from fastmcp import FastMCP

    from hive._context import ServerContext

# RelevanceTracker's methods share one un-timed threading.Lock (relevance.py);
# a stall inside it (SQLite lock contention, a slow disk) must not drag the
# whole briefing past the outer tool deadline — see #282 and the 2026-03-13
# lesson. Every relevance touch below is bounded to this and degrades to
# "skip it" rather than hang.
_RELEVANCE_TIMEOUT_S = 3.0


def _vault_search_by_rank(
    ctx: ServerContext,
    search_root: Path,
    query: str,
    rank_by: str,
    max_lines: int,
) -> str:
    """Lessons-only ranked search (HIVE-97).

    Filters matches to ``90-lessons.md`` files, walks back from each
    matching line to the nearest ``### [date]`` heading, increments
    each surfaced heading once (per-call dedup), then ranks via
    ``ctx.lessons.top(by=rank_by)``.
    """
    query_lower = query.lower()
    # (project_slug, heading) -> file_rel
    hits: dict[tuple[str, str], str] = {}
    bm25_per_project: dict[str, dict[str, float]] = {}

    for md_file in sorted(search_root.rglob("90-lessons.md")):
        content = _safe_read(md_file)
        if content is None:
            continue
        body = extract_body(content)
        rel = md_file.relative_to(ctx.vault).as_posix()
        rel_parts = md_file.relative_to(ctx.vault).parts
        # Layout: <scope>/<project_slug>/90-lessons.md (project lessons).
        if len(rel_parts) < 3:
            continue
        project_slug = rel_parts[-2]

        body_lines = body.splitlines()
        per_heading_hits: dict[str, int] = {}
        for idx, line in enumerate(body_lines, start=1):
            if query_lower not in line.lower():
                continue
            heading = find_lesson_heading(body, idx)
            if heading is None:
                continue
            per_heading_hits[heading] = per_heading_hits.get(heading, 0) + 1
            hits.setdefault((project_slug, heading), rel)

        if per_heading_hits:
            # Normalise raw hit counts to [0, 1] per project for hybrid blend.
            max_h = max(per_heading_hits.values())
            bm25_per_project[project_slug] = {h: cnt / max_h for h, cnt in per_heading_hits.items()}

    if not hits:
        return track(
            ctx,
            "vault_search",
            f"No lessons found for '{query}'.",
        )

    # Increment each surfaced heading once (per-tool-call dedup).
    for project_slug, heading in hits:
        ctx.lessons.increment(project_slug, heading)

    # Rank within each project, emit in project-then-rank order.
    rendered: list[str] = [f"# Lesson search ({rank_by}): '{query}'", ""]
    for project_slug in sorted({p for p, _ in hits}):
        relevant_headings = {h for p, h in hits if p == project_slug}
        bm25_scores = bm25_per_project.get(project_slug, {})
        ranked = ctx.lessons.top(
            project_slug,
            by=rank_by,
            limit=len(relevant_headings),
            bm25_scores=bm25_scores,
        )
        ordered = [h for h in ranked if h in relevant_headings]
        if not ordered:
            continue
        rel = hits[(project_slug, ordered[0])]
        rendered.append(f"### {rel}")
        for heading in ordered:
            rendered.append(f"  - {heading}")

    output = "\n".join(rendered)
    return track(ctx, "vault_search", _truncate(output, max_lines))


def list_projects_text(ctx: ServerContext) -> str:
    """Build project listing text (shared by resource and tool)."""
    lines = ["# Vault Projects", ""]
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
            found_any = True
            sections = [
                s for s, filename in SECTION_SHORTCUTS.items() if (project_dir / filename).exists()
            ]
            try:
                md_count = len(list(project_dir.rglob("*.md")))
            except OSError:
                md_count = 0
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
    @wrap_sync_tool(ctx, "vault_list")
    def vault_list(
        project: str = "",
        path: str = "",
        pattern: str = "",
        subpath: str = "",
    ) -> str:
        """List vault projects, or files within a project.

        When called without arguments, lists all available projects.
        When called with a project, lists files in that project directory.

        Args:
            project: Project slug. Empty = list all projects.
            path: Subdirectory within the project. Empty = project root.
                (Use `path`, not `subpath` — `subpath` is accepted as an alias.)
            pattern: Glob pattern to filter files (e.g. 'adr-*', '*.md').
            subpath: Alias of `path` (#151). Prefer `path`. Note: there is no
                `scope` parameter here — `scope` lives on `vault_search`.
        """
        path = path or subpath  # #151: accept `subpath` as alias of `path`
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_list", guard)

        if not project:
            return track(ctx, "vault_list", list_projects_text(ctx))

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(ctx, "vault_list", project_not_found(project), project)
        project_dir, _ = resolved

        from hive._helpers import _check_path_boundary

        target = project_dir / path if path else project_dir
        boundary_error = _check_path_boundary(target, ctx.vault)
        if boundary_error:
            return track(ctx, "vault_list", boundary_error, project)
        if not target.is_dir():
            return track(
                ctx, "vault_list", f"Path '{path}' not found in project '{project}'.", project
            )

        lines: list[str] = [
            f"# Files: {project}/{path}" if path else f"# Files: {project}",
            "",
        ]

        max_list_results = 500
        if pattern:
            if ".." in pattern:
                return track(
                    ctx,
                    "vault_list",
                    "Pattern must not contain '..'.",
                    project,
                )
            try:
                files = sorted(
                    f
                    for f in target.rglob(pattern)
                    if f.is_file() and _check_path_boundary(f, ctx.vault) is None
                )
            except OSError:
                return track(
                    ctx,
                    "vault_list",
                    f"Error reading directory for pattern '{pattern}'.",
                    project,
                )
            if not files:
                return track(
                    ctx,
                    "vault_list",
                    f"No files matching '{pattern}' in {project}/{path}.",
                    project,
                )
            for f in files[:max_list_results]:
                rel_f = f.relative_to(target)
                lines.append(f"- {rel_f}")
        else:
            try:
                entries = sorted(target.iterdir())
            except OSError:
                return track(
                    ctx,
                    "vault_list",
                    f"Error reading directory '{path or project}'.",
                    project,
                )
            for d in entries:
                if d.is_dir():
                    lines.append(f"- {d.name}/")
            for f in entries:
                if f.is_file():
                    lines.append(f"- {f.name}")

        return track(ctx, "vault_list", "\n".join(lines), project, path)

    @mcp.tool(annotations=_READ_ONLY)
    @wrap_sync_tool(ctx, "vault_query")
    def vault_query(
        project: str,
        section: str = "context",
        path: str = "",
        max_lines: int = 0,
        include_metadata: bool = False,
        identifier: str = "",
    ) -> str:
        """Read content from a vault project — use instead of direct filesystem access.

        Args:
            project: Project slug (directory under 10_projects/), or '_meta' for 00_meta/.
            section: Shortcut name (context, tasks, roadmap, lessons). Ignored if path is set.
            path: Relative path to a specific .md file within the project. Overrides section.
                (Use `path` for a file, not `identifier` — `identifier` is accepted as an alias.)
            max_lines: Maximum lines to return. 0 = unlimited.
            include_metadata: Prepend a structured metadata line from YAML frontmatter.
            identifier: Alias of `path` (#151). Prefer `path`; for a section
                shortcut use `section` instead.
        """
        path = path or identifier  # #151: accept `identifier` as alias of `path`
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_query", guard, project)

        resolved_section = path or section
        result = _resolve_file(ctx.vault, project, section, path, ctx.scopes)
        if isinstance(result, str):
            return track(ctx, "vault_query", result, project, resolved_section)
        filepath = result

        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return track(
                ctx,
                "vault_query",
                format_io_error(exc, resolved_section, "read"),
                project,
                resolved_section,
            )

        if include_metadata:
            fm = parse_frontmatter(content)
            meta = _format_metadata(fm)
            if meta:
                content = f"**Metadata:** {meta}\n\n{content}"

        truncated = _truncate(content, max_lines)

        if filepath.name == "90-lessons.md":
            for heading in dict.fromkeys(extract_lesson_headings(truncated)):
                ctx.lessons.increment(project, heading)

        return track(ctx, "vault_query", truncated, project, resolved_section)

    @mcp.tool(annotations=_READ_ONLY)
    @wrap_sync_tool(ctx, "vault_search")
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
        scope: str = "",
        rank_by: str = "bm25",
        regex: bool = False,
        limit: int = 0,
    ) -> str:
        """Search the vault: full-text, ranked, or recent changes.

        Default mode: flat full-text search across all vault files.
        Ranked mode (ranked=True): results scored by relevance.
        Recent mode (since_days>0): files changed in the last N days.
        rank_by mode (rank_by != 'bm25'): lessons-only, ranked by usage.

        Args:
            query: Text to search for (case-insensitive).
            max_lines: Maximum output lines. Default 500.
            type_filter: Only files whose frontmatter type matches.
            status_filter: Only files whose frontmatter status matches.
            tag_filter: Only files that have this frontmatter tag.
            use_regex: Treat query as regex. Default False. (Use `use_regex`,
                not `regex` — `regex` is accepted as an alias.)
            ranked: Score results by relevance. Default False.
            max_results: Max result files. Default 10. Caps the file count in
                all modes (flat, ranked, recent); in flat/recent the cap is by
                path order (alphabetical) — use ranked=True for relevance order.
            since_days: Show recent changes (0 = disabled). Default 0.
            project: Filter to this project (recent mode only).
            scope: Restrict search to a scope (e.g. 'work', 'projects'). Empty = all.
            rank_by: Lesson ranking ('bm25' default keeps current
                behaviour; 'reinforcements', 'confidence', 'hybrid' filter
                to 90-lessons.md only and rank by usage signal).
            regex: Alias of `use_regex` (#151). Prefer `use_regex`. To narrow
                by location use `scope` / `project`, not `path_filter` /
                `path_prefix`.
            limit: Alias of `max_results` (#202). Prefer `max_results`. When
                both are given the tighter (smaller) cap wins; 0 = unset.
        """
        use_regex = use_regex or regex  # #151: accept `regex` as alias of `use_regex`
        # #202: `limit` is an int alias of `max_results`; tightest cap wins when
        # both are set (a 0 on either side means "unset"). After this line
        # `max_results` is the single effective file cap for all three modes.
        if limit:
            max_results = min(limit, max_results) if max_results else limit
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_search", guard)

        if rank_by != "bm25" and rank_by not in {
            "reinforcements",
            "confidence",
            "hybrid",
        }:
            return track(
                ctx,
                "vault_search",
                f"Unknown rank_by={rank_by!r}. Expected one of: "
                f"bm25, reinforcements, confidence, hybrid.",
            )

        # ── Scope filter ──
        search_root = ctx.vault
        if scope:
            scope_dir_name = ctx.scopes.get(scope)
            if scope_dir_name is None:
                available = ", ".join(sorted(ctx.scopes.keys()))
                return track(
                    ctx,
                    "vault_search",
                    f"Unknown scope '{scope}'. Available: {available}",
                )
            search_root = ctx.vault / scope_dir_name
            if not search_root.is_dir():
                return track(
                    ctx,
                    "vault_search",
                    f"Scope directory '{scope_dir_name}' not found in vault.",
                )

        if since_days < 0:
            return track(
                ctx,
                "vault_search",
                "since_days must be a positive number.",
            )

        # ── Recent mode ──
        if since_days > 0:
            git_paths = set(_git_recent(ctx.vault, since_days))
            if scope and search_root != ctx.vault:
                scope_prefix = search_root.relative_to(ctx.vault).as_posix() + "/"
                git_paths = {p for p in git_paths if p.startswith(scope_prefix)}
            cutoff = date.today() - timedelta(days=since_days)
            for md_file in search_root.rglob("*.md"):
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
                    ctx.vault,
                    project,
                    ctx.scopes,
                )
                if resolved is not None:
                    prefix = resolved[0].relative_to(ctx.vault).as_posix() + "/"
                    git_paths = {p for p in git_paths if p.startswith(prefix)}
                else:
                    git_paths = set()

            if not git_paths:
                return track(
                    ctx,
                    "vault_search",
                    f"No changes found in the last {since_days} days.",
                    project,
                )

            rlines: list[str] = [
                f"# Recent Changes (last {since_days} days)",
                "",
            ]
            ordered_paths = sorted(git_paths)
            if max_results > 0:  # #202: limit/max_results caps recent mode too
                ordered_paths = ordered_paths[:max_results]
            for rel_path in ordered_paths:
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
                ctx,
                "vault_search",
                _truncate(output, max_lines),
                project,
            )

        # ── Ranked mode (SQLite FTS5 + BM25) ──
        if ranked:
            if not query:
                return track(
                    ctx,
                    "vault_search",
                    "Query is required for ranked search.",
                )

            # Try SQLite FTS5 search
            try:
                fts: VaultFTSIndex = getattr(ctx, "fts_index", None)
                if fts is None:
                    from hive._fts import VaultFTSIndex

                    fts = VaultFTSIndex(ctx.vault, scopes=ctx.scopes)
                    ctx.fts_index = fts

                fts_matches = fts.search(
                    query=query,
                    max_results=max_results,
                    type_filter=type_filter,
                    status_filter=status_filter,
                    tag_filter=tag_filter,
                    scope=scope,
                )
                if fts_matches:
                    results: list[str] = [
                        f"# Ranked Search: '{query}'",
                        "",
                    ]
                    for m in fts_matches:
                        meta_items = []
                        if m.doc_type:
                            meta_items.append(f"type={m.doc_type}")
                        if m.doc_status:
                            meta_items.append(f"status={m.doc_status}")
                        if m.tags:
                            meta_items.append(f"tags=[{', '.join(m.tags)}]")
                        if m.created:
                            meta_items.append(f"created={m.created}")
                        meta_str = ", ".join(meta_items)
                        meta_part = f" [{meta_str}]" if meta_str else ""

                        results.append(
                            f"### {m.rel_path} (score: {m.score:.1f}){meta_part}",
                        )
                        for line in m.matching_lines[:5]:
                            results.append(f"  - {line}")

                    output = "\n".join(results)
                    return track(
                        ctx,
                        "vault_search",
                        _truncate(output, max_lines),
                    )
            except Exception as fts_err:
                import logging

                logging.getLogger("hive.fts").warning("FTS5 ranked search fallback: %s", fts_err)

            # Linear fallback
            query_lower = query.lower()
            today = date.today()
            scored: list[tuple[float, str, str, list[str]]] = []

            for md_file in sorted(search_root.rglob("*.md")):
                content = _safe_read(md_file)
                if content is None:
                    continue
                body = extract_body(content)
                matching = [ln.strip() for ln in body.splitlines() if query_lower in ln.lower()]
                if not matching:
                    continue
                fm = parse_frontmatter(content)
                score = _score_file(len(matching), fm, today)
                rel = md_file.relative_to(ctx.vault).as_posix()
                meta = _format_metadata(fm)
                scored.append((score, rel, meta, matching))

            if not scored:
                return track(
                    ctx,
                    "vault_search",
                    f"No matches found for '{query}'.",
                )

            scored.sort(key=lambda x: x[0], reverse=True)
            scored = scored[:max_results]

            results = [
                f"# Ranked Search: '{query}'",
                "",
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
                ctx,
                "vault_search",
                _truncate(output, max_lines),
            )

        # ── rank_by mode (HIVE-97) — lessons-only ranked by usage signal ──
        if rank_by != "bm25":
            if not query:
                return track(
                    ctx,
                    "vault_search",
                    f"Query is required for rank_by={rank_by!r} search.",
                )
            return _vault_search_by_rank(
                ctx,
                search_root,
                query,
                rank_by,
                max_lines,
            )

        # ── Standard search ──
        if not query:
            return track(
                ctx,
                "vault_search",
                "Query is required for search.",
            )

        if use_regex:
            if len(query) > 200:
                return track(
                    ctx,
                    "vault_search",
                    "Regex pattern too long (max 200 chars).",
                )
            try:
                rx_pattern = re.compile(query, re.IGNORECASE)
            except re.error as exc:
                return track(
                    ctx,
                    "vault_search",
                    f"Invalid regex '{query}': {exc}",
                )

        flat_results: list[str] = []
        files_added = 0
        query_lower = query.lower()
        has_filters = bool(type_filter or status_filter or tag_filter)

        for md_file in sorted(search_root.rglob("*.md")):
            # #202: cap result files (alphabetical-first-N) in flat mode too,
            # mirroring ranked. max_results <= 0 means "no file cap".
            if max_results > 0 and files_added >= max_results:
                break
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

            body = extract_body(content)
            if use_regex:
                matching_lines = [
                    line.strip() for line in body.splitlines() if rx_pattern.search(line)
                ]
            else:
                matching_lines = [
                    line.strip() for line in body.splitlines() if query_lower in line.lower()
                ]
            if matching_lines:
                file_rel = md_file.relative_to(ctx.vault)
                meta_str = _format_metadata(fm)
                meta = f" [{meta_str}]" if meta_str else ""
                flat_results.append(f"### {file_rel}{meta}")
                for line in matching_lines[:5]:
                    flat_results.append(f"  - {line}")
                files_added += 1

        if not flat_results:
            return track(
                ctx,
                "vault_search",
                f"No matches found for '{query}'.",
            )

        output = f"# Search: '{query}'\n\n" + "\n".join(flat_results)
        return track(ctx, "vault_search", _truncate(output, max_lines))

    @mcp.tool(annotations=_READ_ONLY)
    @wrap_sync_tool(ctx, "session_briefing")
    def session_briefing(project: str = "") -> str:
        """Call at the start of every new session to load project context.

        Without a project, returns the available project list with a usage
        hint — discoverability parity with vault_health() and worker_status().
        With a project, assembles active tasks, recent lessons, git activity,
        and project health into a single response (replaces 3-4 manual calls).

        Args:
            project: Project slug (directory under 10_projects/). Empty =
                list available projects so the caller can pick one. This is the
                only parameter — there is no `days` argument (the briefing
                window is fixed).
        """
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "session_briefing", guard, project)

        if not project:
            listing = list_projects_text(ctx)
            hint = (
                "\n\n_Pass `project=<slug>` to load tasks, lessons, "
                "git activity, and health for one of the projects above._"
            )
            return track(ctx, "session_briefing", listing + hint)

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(ctx, "session_briefing", project_not_found(project), project)
        project_dir, _ = resolved

        relevance_degraded = False

        def _relevance[T](fn: Callable[[], T], label: str) -> T | None:
            """Bounded ``ctx.relevance`` touch (#282).

            Short-circuits once any prior touch this call has already
            timed out: every ``RelevanceTracker`` method serializes on the
            same lock, so once one is stuck the rest would just queue
            behind it — skip them outright instead of paying the timeout
            again for a call that cannot succeed either.
            """
            nonlocal relevance_degraded
            if relevance_degraded:
                return None
            ok, result = _bounded_sync_call(fn, _RELEVANCE_TIMEOUT_S, label=label)
            if not ok:
                relevance_degraded = True
                return None
            return result

        # Decay stale relevance scores at session start.
        _relevance(ctx.relevance.apply_decay, "apply_decay")

        # Build sections as keyed blocks
        sections: dict[str, str] = {}

        # Tasks
        task_result = _resolve_file(ctx.vault, project, "tasks", "", ctx.scopes)
        if not isinstance(task_result, str):
            task_content = _safe_read(task_result)
            if task_content is not None:
                _relevance(
                    lambda: ctx.relevance.record_access(project, "tasks"),
                    "record_access_tasks",
                )
                task_body = _truncate(extract_body(task_content), 50)
                sections["tasks"] = f"## Active Tasks\n{task_body}"

        # Lessons
        lessons_result = _resolve_file(
            ctx.vault,
            project,
            "lessons",
            "",
            ctx.scopes,
        )
        if not isinstance(lessons_result, str):
            lessons_content = _safe_read(lessons_result)
            if lessons_content is not None:
                _relevance(
                    lambda: ctx.relevance.record_access(project, "lessons"),
                    "record_access_lessons",
                )
                lesson_lines = extract_body(lessons_content).splitlines()
                tail = lesson_lines[-30:] if len(lesson_lines) > 30 else lesson_lines
                sections["lessons"] = "## Recent Lessons\n" + "\n".join(tail)

        # Git activity (always shown, not ranked)
        git_block = "## Recent Vault Activity\n"
        git_block += _git_log(ctx.vault, 5) or "(no git history available)"

        # Health (always shown, not ranked) — single project scan reused.
        md_files, _, frontmatters = scan_project(project_dir)
        stale_threshold = date.today() - timedelta(days=ctx.stale_days)
        stale_count = len(
            count_stale_from(
                project_dir,
                md_files,
                frontmatters,
                stale_threshold,
            )
        )
        health_lines = [f"- Files: {len(md_files)}"]
        if stale_count:
            health_lines.append(f"- Stale: {stale_count}")
        health_block = "## Project Health\n" + "\n".join(health_lines)

        # Order rankable sections by relevance (adaptive).
        default_order = ["tasks", "lessons"]
        scores = _relevance(lambda: ctx.relevance.get_scores(project), "get_scores")
        if scores:
            ranked_sections = sorted(
                sections.keys(),
                key=lambda s: scores.get(s, 0.0),
                reverse=True,
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
        if relevance_degraded:
            parts.append("")
            parts.append(
                "_(relevance tracking degraded — timed out after "
                f"{_RELEVANCE_TIMEOUT_S:.0f}s; showing default section order)_"
            )

        return track(ctx, "session_briefing", "\n".join(parts), project)
