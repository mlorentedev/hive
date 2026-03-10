"""Vault write operations — write and patch."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hive._helpers import (
    _WRITE,
    SECTION_SHORTCUTS,
    _check_path_boundary,
    _git_commit,
    _make_frontmatter,
    _resolve_project_dir,
    track,
)
from hive.frontmatter import validate_frontmatter

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from hive._context import ServerContext

_WRITE_OPERATIONS = frozenset({"append", "replace", "create"})


def register_vault_write(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register vault write tools on the MCP server."""

    @mcp.tool(annotations=_WRITE)
    def vault_write(
        project: str,
        content: str,
        operation: str = "append",
        section: str = "",
        path: str = "",
        doc_type: str = "",
    ) -> str:
        """Write to the vault: append, replace a section, or create a new file.

        Modes:
        - append/replace: Update a project section. Requires section.
        - create: Create a new file with auto-generated frontmatter. Requires path and doc_type.

        Args:
            project: Project slug or '_meta' for cross-project content.
            content: Markdown content to write (body only for create mode).
            operation: 'append', 'replace', or 'create'. Default 'append'.
            section: Section shortcut (context, tasks, roadmap, lessons). For append/replace.
            path: Relative path for new file. For create mode.
            doc_type: Document type for frontmatter. For create mode.
        """  # noqa: E501
        if operation not in _WRITE_OPERATIONS:
            return track(ctx, "vault_write", (
                f"Invalid operation '{operation}'. "
                f"Valid: {', '.join(sorted(_WRITE_OPERATIONS))}"
            ), project)

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(
                ctx, "vault_write",
                f"Project '{project}' not found in vault.",
                project,
            )
        project_dir, _ = resolved

        # ── Create mode ──
        if operation == "create":
            if not path:
                return track(
                    ctx, "vault_write",
                    "Path is required for create operation.",
                    project,
                )
            if not doc_type:
                return track(
                    ctx, "vault_write",
                    "doc_type is required for create operation.",
                    project,
                )

            filepath = project_dir / path
            boundary_error = _check_path_boundary(filepath, ctx.vault)
            if boundary_error:
                return track(ctx, "vault_write", boundary_error, project)
            if filepath.exists():
                return track(
                    ctx, "vault_write",
                    f"File already exists: {path}. "
                    "Use vault_write(operation='replace') to modify.",
                    project,
                )

            frontmatter = _make_frontmatter(filepath.stem, doc_type)

            try:
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.write_text(
                    frontmatter + content, encoding="utf-8",
                )
            except OSError as exc:
                return track(
                    ctx, "vault_write", f"File I/O error: {exc}", project,
                )

            rel = filepath.relative_to(ctx.vault)
            display = "00_meta" if project == "_meta" else project
            _git_commit(
                ctx.vault, rel,
                f"vault: create {display}/{path}",
            )

            return track(
                ctx, "vault_write",
                f"Created {project}/{path} (type: {doc_type}).",
                project, path,
            )

        # ── Append / Replace mode ──
        if not section:
            return track(
                ctx, "vault_write",
                "Section is required for append/replace operations.",
                project,
            )

        filename = SECTION_SHORTCUTS.get(section)
        if filename is None:
            available = ", ".join(SECTION_SHORTCUTS)
            return track(
                ctx, "vault_write",
                f"Section '{section}' not found. Available: {available}",
                project,
            )

        filepath = project_dir / filename

        if operation == "replace":
            error = validate_frontmatter(content)
            if error:
                return track(
                    ctx, "vault_write",
                    f"Frontmatter validation failed: {error}",
                    project,
                )

        try:
            if operation == "append":
                existing = (
                    filepath.read_text(encoding="utf-8")
                    if filepath.exists()
                    else ""
                )
                filepath.write_text(
                    existing + content, encoding="utf-8",
                )
            else:
                filepath.write_text(content, encoding="utf-8")
        except OSError as exc:
            return track(
                ctx, "vault_write", f"File I/O error: {exc}", project,
            )

        rel = filepath.relative_to(ctx.vault)
        _git_commit(
            ctx.vault, rel, f"vault: update {project}/{section}",
        )

        return track(
            ctx, "vault_write",
            f"Updated {project}/{section} ({operation}).",
            project, section,
        )

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
            return track(
                ctx, "vault_patch",
                "Cannot mix old_text/new_text with patches. "
                "Use one mode or the other.",
                project,
            )

        if has_single:
            if not old_text or not new_text:
                return track(
                    ctx, "vault_patch",
                    "Provide both old_text and new_text for single replacement.",
                    project,
                )
            patch_list: list[dict[str, str]] = [
                {"old_text": old_text, "new_text": new_text},
            ]
        elif has_multi:
            patch_list = patches
        else:
            return track(
                ctx, "vault_patch",
                "Provide old_text/new_text or a patches list.",
                project,
            )

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(ctx, "vault_patch",
                         f"Project '{project}' not found in vault.", project)
        project_dir, _ = resolved

        filepath = project_dir / path
        boundary_error = _check_path_boundary(filepath, ctx.vault)
        if boundary_error:
            return track(ctx, "vault_patch", boundary_error, project)
        if not filepath.exists():
            return track(ctx, "vault_patch",
                         f"File '{path}' not found in project '{project}'.",
                         project)

        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            return track(ctx, "vault_patch",
                         f"File I/O error reading '{path}': {exc}", project)

        # Validate and apply all patches on a working copy first
        working = content
        for i, patch in enumerate(patch_list, 1):
            if "old_text" not in patch or "new_text" not in patch:
                label = f"patch {i}: " if len(patch_list) > 1 else ""
                return track(
                    ctx, "vault_patch",
                    f"{label}Each patch must have 'old_text' and 'new_text' keys.",
                    project,
                )
            old = patch["old_text"]
            new = patch["new_text"]
            count = working.count(old)

            if count == 0:
                label = f"patch {i}: " if len(patch_list) > 1 else ""
                return track(
                    ctx, "vault_patch",
                    f"{label}old_text not found in file '{path}'.",
                    project,
                )
            if count > 1:
                label = f"patch {i}: " if len(patch_list) > 1 else ""
                return track(
                    ctx, "vault_patch",
                    f"{label}Ambiguous: old_text appears {count} times "
                    f"in '{path}'. "
                    "Provide more context to make the match unique.",
                    project,
                )
            working = working.replace(old, new, 1)

        try:
            filepath.write_text(working, encoding="utf-8")
        except OSError as exc:
            return track(ctx, "vault_patch",
                         f"File I/O error writing '{path}': {exc}", project)

        rel = filepath.relative_to(ctx.vault)
        n = len(patch_list)
        _git_commit(ctx.vault, rel, f"vault: patch {project}/{path}")

        noun = "patch" if n == 1 else "patches"
        return track(ctx, "vault_patch",
                     f"Applied {n} {noun} to {project}/{path}.",
                     project, path)
