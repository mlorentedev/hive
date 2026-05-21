"""Vault write operations — write and patch."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hive._helpers import (
    _WRITE,
    SECTION_SHORTCUTS,
    WriteLockTimeout,
    _check_path_boundary,
    _git_commit,
    _git_commit_all,
    _make_frontmatter,
    _match_and_replace,
    _resolve_project_dir,
    _vault_guard,
    format_io_error,
    project_not_found,
    track,
    vault_write_lock,
    wrap_sync_tool,
)
from hive.frontmatter import validate_frontmatter

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from hive._context import ServerContext

_WRITE_OPERATIONS = frozenset({"append", "replace", "create"})


def register_vault_write(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register vault write tools on the MCP server."""

    @mcp.tool(annotations=_WRITE)
    @wrap_sync_tool(ctx, "vault_write")
    def vault_write(
        project: str,
        content: str,
        operation: str = "append",
        section: str = "",
        path: str = "",
        doc_type: str = "",
        commit: bool = True,
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
            commit: If True (default), auto-commit to git. If False, write to
                disk but leave the file dirty so the caller can batch many
                writes into one commit via the ``vault_commit`` tool, or let
                obsidian-git's auto-commit pick it up. Durability contract:
                files are persisted to disk regardless; only the *commit* is
                deferred. A crash before the next flush loses the commit, not
                the file content.
        """  # noqa: E501
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_write", guard, project)

        if operation not in _WRITE_OPERATIONS:
            return track(ctx, "vault_write", (
                f"Invalid operation '{operation}'. "
                f"Valid: {', '.join(sorted(_WRITE_OPERATIONS))}"
            ), project)

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(
                ctx, "vault_write",
                project_not_found(project),
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

            frontmatter = _make_frontmatter(filepath.stem, doc_type)

            try:
                with vault_write_lock(ctx.vault):
                    if filepath.exists():
                        return track(
                            ctx, "vault_write",
                            f"File already exists: {path}. "
                            "Use vault_write(operation='replace') to modify.",
                            project,
                        )

                    try:
                        filepath.parent.mkdir(parents=True, exist_ok=True)
                        filepath.write_text(
                            frontmatter + content, encoding="utf-8",
                        )
                    except OSError as exc:
                        return track(
                            ctx, "vault_write",
                            format_io_error(exc, path, "create"),
                            project,
                        )

                    rel = filepath.relative_to(ctx.vault)
                    display = "00_meta" if project == "_meta" else project
                    if commit:
                        _git_commit(
                            ctx.vault, [rel],
                            f"vault: create {display}/{path}",
                        )
            except WriteLockTimeout as exc:
                return track(
                    ctx, "vault_write",
                    f"Server busy — {exc.reason}. Retry shortly.",
                    project,
                )

            suffix = "" if commit else " (uncommitted — call vault_commit to flush)"
            return track(
                ctx, "vault_write",
                f"Created {project}/{path} (type: {doc_type}).{suffix}",
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
            with vault_write_lock(ctx.vault):
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
                except (OSError, UnicodeDecodeError) as exc:
                    return track(
                        ctx, "vault_write",
                        format_io_error(exc, f"{project}/{section}", operation),
                        project,
                    )

                rel = filepath.relative_to(ctx.vault)
                if commit:
                    _git_commit(
                        ctx.vault, [rel],
                        f"vault: update {project}/{section}",
                    )
        except WriteLockTimeout as exc:
            return track(
                ctx, "vault_write",
                f"Server busy — {exc.reason}. Retry shortly.",
                project,
            )

        suffix = "" if commit else " (uncommitted — call vault_commit to flush)"
        return track(
            ctx, "vault_write",
            f"Updated {project}/{section} ({operation}).{suffix}",
            project, section,
        )

    @mcp.tool(annotations=_WRITE)
    @wrap_sync_tool(ctx, "vault_patch")
    def vault_patch(
        project: str,
        path: str,
        find: str = "",
        replace: str = "",
        patches: list[dict[str, str]] = [],  # noqa: B006
        commit: bool = True,
    ) -> str:
        """Surgical find-and-replace in a vault file with auto git commit.

        Supports single or multi-replacement. For a single replacement, provide
        ``find`` and ``replace``. For multiple replacements, provide ``patches``
        — a list of ``{"find": "...", "replace": "..."}`` dicts applied in
        sequence. Do not mix both modes.

        Each ``find`` value must appear exactly once in the file (after prior
        patches in the list have been applied). If any patch fails validation,
        no changes are written.

        Uses 3-pass cascading match: exact → body-only → whitespace-normalized.

        Args:
            project: Project slug or '_meta' for cross-project content.
            path: Relative path to the file within the project.
            find: Exact text to find (single mode). Empty = not set.
            replace: Replacement text (single mode). Empty = not set.
            patches: List of {"find", "replace"} dicts (multi mode).
            commit: If True (default), auto-commit. If False, write to disk
                without committing — useful for batching many patches into
                one ``vault_commit`` flush. See ``vault_write`` docstring for
                the durability contract.
        """
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_patch", guard, project)

        has_single = bool(find) or bool(replace)
        has_multi = len(patches) > 0

        if has_single and has_multi:
            return track(
                ctx, "vault_patch",
                "Cannot mix find/replace with patches. "
                "Use one mode or the other.",
                project,
            )

        if has_single:
            if not find or not replace:
                return track(
                    ctx, "vault_patch",
                    "Provide both find and replace for single replacement.",
                    project,
                )
            patch_list: list[dict[str, str]] = [
                {"find": find, "replace": replace},
            ]
        elif has_multi:
            patch_list = patches
        else:
            return track(
                ctx, "vault_patch",
                "Provide find/replace or a patches list.",
                project,
            )

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(ctx, "vault_patch",
                         project_not_found(project), project)
        project_dir, _ = resolved

        filepath = project_dir / path
        boundary_error = _check_path_boundary(filepath, ctx.vault)
        if boundary_error:
            return track(ctx, "vault_patch", boundary_error, project)
        if not filepath.exists():
            return track(ctx, "vault_patch",
                         f"File '{path}' not found in project '{project}'.",
                         project)

        n = 0
        try:
            with vault_write_lock(ctx.vault):
                try:
                    content = filepath.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    return track(
                        ctx, "vault_patch",
                        format_io_error(exc, path, "read"),
                        project,
                    )

                # Validate and apply all patches on a working copy first
                working = content
                for i, patch in enumerate(patch_list, 1):
                    if "find" not in patch or "replace" not in patch:
                        label = f"patch {i}: " if len(patch_list) > 1 else ""
                        return track(
                            ctx, "vault_patch",
                            f"{label}Each patch must have 'find' and "
                            f"'replace' keys.",
                            project,
                        )
                    ok, result = _match_and_replace(
                        working, patch["find"], patch["replace"],
                    )
                    if not ok:
                        label = f"patch {i}: " if len(patch_list) > 1 else ""
                        return track(
                            ctx, "vault_patch",
                            f"{label}{result}", project,
                        )
                    working = result

                try:
                    filepath.write_text(working, encoding="utf-8")
                except OSError as exc:
                    return track(
                        ctx, "vault_patch",
                        format_io_error(exc, path, "write"),
                        project,
                    )

                rel = filepath.relative_to(ctx.vault)
                n = len(patch_list)
                if commit:
                    _git_commit(
                        ctx.vault, [rel],
                        f"vault: patch {project}/{path}",
                    )
        except WriteLockTimeout as exc:
            return track(
                ctx, "vault_patch",
                f"Server busy — {exc.reason}. Retry shortly.",
                project,
            )

        noun = "patch" if n == 1 else "patches"
        suffix = "" if commit else " (uncommitted — call vault_commit to flush)"
        return track(ctx, "vault_patch",
                     f"Applied {n} {noun} to {project}/{path}.{suffix}",
                     project, path)

    @mcp.tool(annotations=_WRITE)
    @wrap_sync_tool(ctx, "vault_commit")
    def vault_commit(message: str = "") -> str:
        """Stage everything in the vault and create one commit.

        Companion to ``vault_write(commit=False)`` and
        ``vault_patch(commit=False)``: callers that opt out of per-write
        commits batch many writes and then flush with a single
        ``vault_commit`` call.

        Returns the new commit SHA on success, a clean-tree notice when
        there is nothing to commit, or a human-readable error.

        Args:
            message: Commit message. Empty defaults to "vault: batch update".
        """
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_commit", guard)

        try:
            with vault_write_lock(ctx.vault):
                status, detail = _git_commit_all(ctx.vault, message)
        except WriteLockTimeout as exc:
            return track(
                ctx, "vault_commit",
                f"Server busy — {exc.reason}. Retry shortly.",
            )

        if status == "committed":
            return track(ctx, "vault_commit", f"Committed {detail}.")
        if status == "clean":
            return track(
                ctx, "vault_commit",
                "Working tree clean — nothing to commit.",
            )
        return track(ctx, "vault_commit", f"Commit failed: {detail}")
