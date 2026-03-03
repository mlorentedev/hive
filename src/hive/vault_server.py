"""Vault MCP Server — on-demand Obsidian vault access for Claude Code."""

from __future__ import annotations

import subprocess
from datetime import date, timedelta
from typing import TYPE_CHECKING

from fastmcp import FastMCP

if TYPE_CHECKING:
    from pathlib import Path

from hive.config import settings
from hive.frontmatter import (
    _TERMINAL_STATUSES,
    parse_date,
    parse_frontmatter,
    validate_frontmatter,
)

_VALID_OPERATIONS = {"append", "replace"}

SECTION_SHORTCUTS: dict[str, str] = {
    "context": "00-context.md",
    "tasks": "11-tasks.md",
    "roadmap": "10-roadmap.md",
    "lessons": "90-lessons.md",
}


def _resolve_project_dir(vault: Path, project: str) -> Path | None:
    """Resolve a project slug to its directory. '_meta' maps to 00_meta/."""
    d = vault / "00_meta" if project == "_meta" else vault / "10_projects" / project
    return d if d.is_dir() else None


def _truncate(text: str, max_lines: int) -> str:
    """Truncate text to max_lines, appending a notice if truncated."""
    if max_lines <= 0:
        return text
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    remaining = len(lines) - max_lines
    return "\n".join(lines[:max_lines]) + f"\n\n[... truncated, {remaining} more lines]"


def create_server(vault_path: Path | None = None) -> FastMCP:
    """Create and configure the Vault MCP server."""
    resolved_path = vault_path or settings.vault_path
    mcp = FastMCP("Hive Vault")

    @mcp.tool
    def vault_list_projects() -> str:
        """List all projects available in the Obsidian vault."""
        projects_dir = resolved_path / "10_projects"
        if not projects_dir.is_dir():
            return "No projects found — 10_projects/ directory does not exist."

        projects = sorted(d.name for d in projects_dir.iterdir() if d.is_dir())
        if not projects:
            return "No projects found in 10_projects/."

        lines = ["# Vault Projects", ""]
        for name in projects:
            project_dir = projects_dir / name
            sections = [
                s for s, filename in SECTION_SHORTCUTS.items() if (project_dir / filename).exists()
            ]
            md_count = len(list(project_dir.rglob("*.md")))
            lines.append(
                f"- **{name}** — {md_count} files, shortcuts: {', '.join(sections) or 'none'}"
            )

        return "\n".join(lines)

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
        project_dir = _resolve_project_dir(resolved_path, project)
        if project_dir is None:
            return f"Project '{project}' not found in vault."

        if path:
            filepath = project_dir / path
        else:
            filename = SECTION_SHORTCUTS.get(section)
            if filename is None:
                available = ", ".join(SECTION_SHORTCUTS)
                return f"Section '{section}' not found. Available shortcuts: {available}"
            filepath = project_dir / filename

        if not filepath.exists():
            target = path or section
            return f"'{target}' not found in project '{project}'."

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

        return _truncate(content, max_lines)

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
            return f"No matches found for '{query}'."

        output = f"# Search: '{query}'\n\n" + "\n".join(results)
        return _truncate(output, max_lines)

    @mcp.tool
    def vault_health() -> str:
        """Return health metrics for all vault projects."""
        projects_dir = resolved_path / "10_projects"
        if not projects_dir.is_dir():
            return "No projects found — 10_projects/ does not exist."

        projects = sorted(d for d in projects_dir.iterdir() if d.is_dir())
        if not projects:
            return "No projects found in vault."

        stale_threshold = date.today() - timedelta(days=180)
        lines = ["# Vault Health Report", ""]

        for project_dir in projects:
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
                s for s, fname in SECTION_SHORTCUTS.items() if not (project_dir / fname).exists()
            ]

            lines.append(f"## {project_dir.name}")
            lines.append(f"- Files: {len(md_files)}")
            lines.append(f"- Total lines: {total_lines}")
            if missing:
                lines.append(f"- Missing sections: {', '.join(missing)}")
            if stale_files:
                lines.append(f"- Stale files (>180d): {', '.join(sorted(stale_files))}")
            lines.append("")

        return "\n".join(lines)

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
            return (
                f"Invalid operation '{operation}'. "
                f"Valid operations: {', '.join(sorted(_VALID_OPERATIONS))}"
            )

        project_dir = resolved_path / "10_projects" / project
        if not project_dir.is_dir():
            return f"Project '{project}' not found in vault."

        filename = SECTION_SHORTCUTS.get(section)
        if filename is None:
            available = ", ".join(SECTION_SHORTCUTS)
            return f"Section '{section}' not found. Available: {available}"

        filepath = project_dir / filename

        if operation == "replace":
            error = validate_frontmatter(content)
            if error:
                return f"Frontmatter validation failed: {error}"

        if operation == "append":
            existing = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
            filepath.write_text(existing + content, encoding="utf-8")
        else:
            filepath.write_text(content, encoding="utf-8")

        rel = filepath.relative_to(resolved_path)
        _git_commit(resolved_path, rel, f"vault: update {project}/{section}")

        return f"Updated {project}/{section} ({operation})."

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
        project_dir = _resolve_project_dir(resolved_path, project)
        if project_dir is None:
            return f"Project '{project}' not found in vault."

        filepath = project_dir / path
        if filepath.exists():
            return f"File already exists: {path}. Use vault_update to modify it."

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

        return f"Created {project}/{path} (type: {doc_type})."

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


server = create_server()


def main() -> None:
    """Entry point for the hive-vault CLI command."""
    server.run()


if __name__ == "__main__":
    main()
