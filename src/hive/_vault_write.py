"""Vault write operations — write and patch."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from hive._helpers import (
    _WRITE,
    SECTION_SHORTCUTS,
    WriteLockTimeout,
    _check_path_boundary,
    _git_commit,
    _git_commit_all,
    _is_external_committer_healthy,
    _make_frontmatter,
    _match_and_replace,
    _resolve_project_dir,
    _vault_guard,
    detect_obsidian_git,
    format_io_error,
    get_partial_state,
    mark_disk_write_done,
    project_not_found,
    schedule_async_hook,
    track,
    vault_write_lock,
    wrap_sync_tool,
)
from hive.frontmatter import validate_frontmatter

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP

    from hive._context import ServerContext

_WRITE_OPERATIONS = frozenset({"append", "replace", "create"})

_AUTO_DEFER_ENV = "HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER"


def _env_truthy(value: str | None) -> bool:
    """Permissive boolean parse for env-var opt-in.

    Recognised truthy values: ``true``, ``yes``, ``1``, ``on``
    (case-insensitive). Everything else is falsy (including unset).
    Strict by design: a typo like ``HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER=ture``
    is treated as falsy, not crashed.
    """
    if value is None:
        return False
    return value.strip().lower() in {"true", "yes", "1", "on"}


_DEFERRED_SUFFIX = " (deferred to obsidian-git; will be picked up on its next tick)"
_UNCOMMITTED_SUFFIX = " (uncommitted — call vault_commit to flush)"
# ADR-018: the path was handed to the reconciler, so a commit is owed and
# will arrive without the caller doing anything. Distinct from
# _UNCOMMITTED_SUFFIX, which promises the opposite — that nothing happens
# until vault_commit — and would be a false statement once a reconciler
# is running.
_QUEUED_SUFFIX = " (queued — commits on the next reconciler tick)"

# HIVE-116 AC-5: public contract for the partial-state response. Locked
# 2026-05-27 per ``specs/HIVE-116-stale-lock-after-deadline/proposal.md``
# R3 resolution. Downstream agents key off the stable substring
# ``"partial state — disk write succeeded"``; wording rotations may occur
# in later minor versions but that prefix MUST remain.
_PARTIAL_STATE_SUFFIX = (
    " (partial state — disk write succeeded, "
    "git commit killed by deadline; verify with vault_query "
    "before retrying)"
)


def _commit_status_suffix(
    requested_commit: bool,
    deferred: bool,
    *,
    deadline_killed: bool = False,
    queued: bool = False,
) -> str:
    """Format the trailing response suffix for write tools.

    Five states feed into one line; ``deadline_killed`` dominates:

    - ``deadline_killed=True`` (any other args) → partial-state suffix
      (HIVE-116 AC-5; disk write landed but git commit was killed by
      the deadline supervisor).
    - ``requested_commit=False, queued=True`` → the ADR-018 reconciler
      holds the path and commits it on its next tick.
    - ``requested_commit=False, queued=False`` → uncommitted, caller must
      ``vault_commit`` later. Reached only when no reconciler is running.
    - ``requested_commit=True, deferred=True`` → obsidian-git is healthy
      and will pick this up on its next tick.
    - ``requested_commit=True, deferred=False`` → hive committed inline;
      empty suffix.

    The ``queued`` split matters because the two non-committing states
    make opposite promises: one says a commit is already owed, the other
    says nothing will happen until the caller asks.
    """
    if deadline_killed:
        return _PARTIAL_STATE_SUFFIX
    if not requested_commit:
        return _QUEUED_SUFFIX if queued else _UNCOMMITTED_SUFFIX
    if deferred:
        return _DEFERRED_SUFFIX
    return ""


def _commit_or_queue(
    ctx: ServerContext,
    rel_paths: list[Path],
    message: str,
    *,
    commit: bool,
    should_defer: bool,
) -> bool:
    """Commit inline, hand the paths to the reconciler, or do neither.

    Returns ``True`` when the paths were queued, which the response suffix
    needs in order to tell the caller a commit is owed.

    Three outcomes, matching the three states the write path can be in:
    ``commit`` and no external committer → commit synchronously; ``commit``
    with a healthy obsidian-git → leave it to that committer (ADR-010);
    otherwise → queue, so the deferred write still reaches git without the
    caller having to call ``vault_commit``.
    """
    if commit and not should_defer:
        _git_commit(ctx.vault, rel_paths, message)
        return False
    if commit:
        return False
    if ctx.reconciler is None:
        return False
    for rel in rel_paths:
        ctx.reconciler.enqueue(rel)
    return True


def _was_deadline_killed() -> bool:
    """True when the partial-state CV records a deadline-driven git kill."""
    state = get_partial_state()
    return bool(state and state.get("deadline_killed"))


def _should_defer_to_external_committer(vault_path: Path) -> bool:
    """Composite predicate for HIVE-115 PR-4 cooperation with obsidian-git.

    Per the 2026-05-22 audit M4:

    .. code-block:: text

        defer ⇔ env "HIVE_AUTO_DEFER_TO_EXTERNAL_COMMITTER" == "true"
                AND detect_obsidian_git(vault) returns a non-None config
                AND (recent_commit ∨ empty_porcelain)

    Returns ``False`` when:

    - env var is unset, ``false``, or any non-truthy value (default)
    - obsidian-git plugin is not installed in the vault
    - obsidian-git is installed but the last commit is stale AND
      ``git status --porcelain`` is non-empty (committer is broken;
      fall back to hive's own commit path)

    Read-path predicate; no termination supervision needed.
    """
    if not _env_truthy(os.environ.get(_AUTO_DEFER_ENV)):
        return False
    config = detect_obsidian_git(vault_path)
    if config is None:
        return False
    interval = config.get("commit_interval", 0)
    if interval <= 0:
        return False
    return _is_external_committer_healthy(vault_path, interval)


_IDEMPOTENT_NOOP = "Idempotent no-op — idempotency_key already applied; no change written."


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
        commit: bool = False,
        idempotency_key: str = "",
    ) -> str:
        """Write to the vault: append, replace a section, or create a new file.

        Modes:
        - append/replace: Update a project section. Requires section.
        - create: Create a new file with auto-generated frontmatter. Requires
          path; doc_type defaults to "note". Inferred automatically when you
          pass a path with no section, so operation may be omitted.

        Args:
            project: Project slug or '_meta' for cross-project content.
            content: Markdown content to write (body only for create mode).
            operation: 'append', 'replace', or 'create'. Default 'append'.
                'create' is inferred when path is set and section is empty.
            section: Section shortcut (context, tasks, roadmap, lessons). For append/replace.
            path: Relative path for the file. Setting this with no section
                creates the file (create mode).
            doc_type: Document type for frontmatter (create mode). Optional;
                defaults to "note".
            commit: If True, commit synchronously before returning — the
                escape hatch for a caller that needs the commit to exist by
                the time the call ends. Defaults to False, which queues the
                path for the reconciler to commit on its next tick (a few
                seconds). Durability contract: the file is persisted to disk
                regardless; only the *commit* is deferred, so a crash before
                the next flush loses the commit, not the content.
            idempotency_key: Optional at-most-once token. If set, a retry with
                the same key is a no-op (safe for transparent retries after a
                daemon restart cuts an in-flight write — ADR-013). Empty
                (default) disables idempotency.
        """  # noqa: E501
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_write", guard, project)

        # #202 Bug 2: a default-op write that supplies a path but no section is
        # unambiguously a file create, not a section append — infer it so
        # vault_write(project, path=..., content=...) "just works".
        if operation == "append" and not section and path:
            operation = "create"

        if operation not in _WRITE_OPERATIONS:
            return track(
                ctx,
                "vault_write",
                (f"Invalid operation '{operation}'. Valid: {', '.join(sorted(_WRITE_OPERATIONS))}"),
                project,
            )

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(
                ctx,
                "vault_write",
                project_not_found(project),
                project,
            )
        project_dir, _ = resolved

        # ── Create mode ──
        if operation == "create":
            if not path:
                return track(
                    ctx,
                    "vault_write",
                    "Path is required for create operation.",
                    project,
                )
            doc_type = doc_type or "note"  # #202 Bug 2: doc_type optional, default note

            filepath = project_dir / path
            boundary_error = _check_path_boundary(filepath, ctx.vault)
            if boundary_error:
                return track(ctx, "vault_write", boundary_error, project)

            frontmatter = _make_frontmatter(filepath.stem, doc_type)

            try:
                with vault_write_lock(ctx.vault):
                    if idempotency_key and ctx.idempotency.is_applied(
                        idempotency_key,
                    ):
                        return track(
                            ctx,
                            "vault_write",
                            _IDEMPOTENT_NOOP,
                            project,
                        )
                    if filepath.exists():
                        return track(
                            ctx,
                            "vault_write",
                            f"File already exists: {path}. "
                            "Use vault_write(operation='replace') to modify.",
                            project,
                        )

                    try:
                        filepath.parent.mkdir(parents=True, exist_ok=True)
                        filepath.write_text(
                            frontmatter + content,
                            encoding="utf-8",
                        )
                    except OSError as exc:
                        return track(
                            ctx,
                            "vault_write",
                            format_io_error(exc, path, "create"),
                            project,
                        )
                    mark_disk_write_done()
                    if idempotency_key:
                        ctx.idempotency.claim(idempotency_key)

                    rel = filepath.relative_to(ctx.vault)
                    display = "00_meta" if project == "_meta" else project
                    should_defer = commit and _should_defer_to_external_committer(ctx.vault)
                    queued = _commit_or_queue(
                        ctx,
                        [rel],
                        f"vault: create {display}/{path}",
                        commit=commit,
                        should_defer=should_defer,
                    )
            except WriteLockTimeout as exc:
                return track(
                    ctx,
                    "vault_write",
                    f"Server busy — {exc.reason}. Retry shortly.",
                    project,
                )

            suffix = _commit_status_suffix(
                commit,
                should_defer,
                deadline_killed=_was_deadline_killed(),
                queued=queued,
            )
            schedule_async_hook(ctx.index_update_hook, filepath)
            return track(
                ctx,
                "vault_write",
                f"Created {project}/{path} (type: {doc_type}).{suffix}",
                project,
                path,
            )

        # ── Append / Replace mode ──
        if not section:
            return track(
                ctx,
                "vault_write",
                "Section is required for append/replace operations. To create a "
                "new file instead, pass a path (operation='create' is inferred "
                "from a path with no section).",
                project,
            )

        filename = SECTION_SHORTCUTS.get(section)
        if filename is None:
            available = ", ".join(SECTION_SHORTCUTS)
            return track(
                ctx,
                "vault_write",
                f"Section '{section}' not found. Available: {available}",
                project,
            )

        filepath = project_dir / filename

        if operation == "replace":
            error = validate_frontmatter(content)
            if error:
                return track(
                    ctx,
                    "vault_write",
                    f"Frontmatter validation failed: {error}",
                    project,
                )

        try:
            with vault_write_lock(ctx.vault):
                if idempotency_key and ctx.idempotency.is_applied(
                    idempotency_key,
                ):
                    return track(
                        ctx,
                        "vault_write",
                        _IDEMPOTENT_NOOP,
                        project,
                    )
                try:
                    if operation == "append":
                        existing = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
                        filepath.write_text(
                            existing + content,
                            encoding="utf-8",
                        )
                    else:
                        filepath.write_text(content, encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    return track(
                        ctx,
                        "vault_write",
                        format_io_error(exc, f"{project}/{section}", operation),
                        project,
                    )
                mark_disk_write_done()
                if idempotency_key:
                    ctx.idempotency.claim(idempotency_key)

                rel = filepath.relative_to(ctx.vault)
                should_defer = commit and _should_defer_to_external_committer(ctx.vault)
                queued = _commit_or_queue(
                    ctx,
                    [rel],
                    f"vault: update {project}/{section}",
                    commit=commit,
                    should_defer=should_defer,
                )
        except WriteLockTimeout as exc:
            return track(
                ctx,
                "vault_write",
                f"Server busy — {exc.reason}. Retry shortly.",
                project,
            )

        suffix = _commit_status_suffix(
            commit,
            should_defer,
            deadline_killed=_was_deadline_killed(),
            queued=queued,
        )
        schedule_async_hook(ctx.index_update_hook, filepath)
        return track(
            ctx,
            "vault_write",
            f"Updated {project}/{section} ({operation}).{suffix}",
            project,
            section,
        )

    @mcp.tool(annotations=_WRITE)
    @wrap_sync_tool(ctx, "vault_patch")
    def vault_patch(
        project: str,
        path: str,
        find: str = "",
        replace: str = "",
        patches: list[dict[str, str]] = [],  # noqa: B006
        commit: bool = False,
        old_string: str = "",
        new_string: str = "",
        idempotency_key: str = "",
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
                (Use `find`/`replace`, NOT `old_string`/`new_string` — those
                are accepted as aliases.)
            replace: Replacement text (single mode). Empty = not set.
            patches: List of {"find", "replace"} dicts (multi mode).
            commit: If True, commit synchronously before returning.
                Defaults to False, which queues the path for the reconciler.
                See ``vault_write`` docstring for the durability contract.
            old_string: Alias of `find` (#151). Prefer `find`.
            new_string: Alias of `replace` (#151). Prefer `replace`.
            idempotency_key: Optional at-most-once token. If set, a retry with
                the same key is a no-op after the first apply (ADR-013). Empty
                (default) disables idempotency.
        """
        find = find or old_string  # #151: accept Edit-tool param names as aliases
        replace = replace or new_string
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_patch", guard, project)

        has_single = bool(find) or bool(replace)
        has_multi = len(patches) > 0

        if has_single and has_multi:
            return track(
                ctx,
                "vault_patch",
                "Cannot mix find/replace with patches. Use one mode or the other.",
                project,
            )

        if has_single:
            if not find or not replace:
                return track(
                    ctx,
                    "vault_patch",
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
                ctx,
                "vault_patch",
                "Provide find/replace or a patches list.",
                project,
            )

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(ctx, "vault_patch", project_not_found(project), project)
        project_dir, _ = resolved

        filepath = project_dir / path
        boundary_error = _check_path_boundary(filepath, ctx.vault)
        if boundary_error:
            return track(ctx, "vault_patch", boundary_error, project)
        if not filepath.exists():
            return track(
                ctx, "vault_patch", f"File '{path}' not found in project '{project}'.", project
            )

        n = 0
        try:
            with vault_write_lock(ctx.vault):
                if idempotency_key and ctx.idempotency.is_applied(
                    idempotency_key,
                ):
                    return track(
                        ctx,
                        "vault_patch",
                        _IDEMPOTENT_NOOP,
                        project,
                    )
                try:
                    content = filepath.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    return track(
                        ctx,
                        "vault_patch",
                        format_io_error(exc, path, "read"),
                        project,
                    )

                # Validate and apply all patches on a working copy first
                working = content
                for i, patch in enumerate(patch_list, 1):
                    if "find" not in patch or "replace" not in patch:
                        label = f"patch {i}: " if len(patch_list) > 1 else ""
                        return track(
                            ctx,
                            "vault_patch",
                            f"{label}Each patch must have 'find' and 'replace' keys.",
                            project,
                        )
                    ok, result = _match_and_replace(
                        working,
                        patch["find"],
                        patch["replace"],
                    )
                    if not ok:
                        label = f"patch {i}: " if len(patch_list) > 1 else ""
                        return track(
                            ctx,
                            "vault_patch",
                            f"{label}{result}",
                            project,
                        )
                    working = result

                try:
                    filepath.write_text(working, encoding="utf-8")
                except OSError as exc:
                    return track(
                        ctx,
                        "vault_patch",
                        format_io_error(exc, path, "write"),
                        project,
                    )
                mark_disk_write_done()
                if idempotency_key:
                    ctx.idempotency.claim(idempotency_key)

                rel = filepath.relative_to(ctx.vault)
                n = len(patch_list)
                should_defer = commit and _should_defer_to_external_committer(ctx.vault)
                queued = _commit_or_queue(
                    ctx,
                    [rel],
                    f"vault: patch {project}/{path}",
                    commit=commit,
                    should_defer=should_defer,
                )
        except WriteLockTimeout as exc:
            return track(
                ctx,
                "vault_patch",
                f"Server busy — {exc.reason}. Retry shortly.",
                project,
            )

        noun = "patch" if n == 1 else "patches"
        suffix = _commit_status_suffix(
            commit,
            should_defer,
            deadline_killed=_was_deadline_killed(),
            queued=queued,
        )
        schedule_async_hook(ctx.index_update_hook, filepath)
        return track(
            ctx, "vault_patch", f"Applied {n} {noun} to {project}/{path}.{suffix}", project, path
        )

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
                This is the only parameter — there is no `project` argument;
                the commit spans the whole vault working tree.
        """
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_commit", guard)

        try:
            with vault_write_lock(ctx.vault):
                status, detail = _git_commit_all(ctx.vault, message)
        except WriteLockTimeout as exc:
            return track(
                ctx,
                "vault_commit",
                f"Server busy — {exc.reason}. Retry shortly.",
            )

        if status == "committed":
            return track(ctx, "vault_commit", f"Committed {detail}.")
        if status == "clean":
            return track(
                ctx,
                "vault_commit",
                "Working tree clean — nothing to commit.",
            )
        return track(ctx, "vault_commit", f"Commit failed: {detail}")

    @mcp.tool(annotations=_WRITE)
    @wrap_sync_tool(ctx, "vault_delete")
    def vault_delete(
        project: str,
        path: str,
        commit: bool = True,
        idempotency_key: str = "",
    ) -> str:
        """Delete a single file from the vault (destructive; recoverable via git).

        Removes one file and, by default, commits the deletion so it stays
        recoverable from git history (``git revert`` / ``git show``). Files
        only — directories are rejected. A non-existent path is an error,
        unless ``idempotency_key`` is set (then a retry against an already-gone
        file is a no-op success).

        Args:
            project: Project slug or '_meta' for cross-project content.
            path: Relative path to the file within the project.
            commit: If True (default), stage + commit the deletion. If False,
                unlink on disk but leave the removal staged for a later
                ``vault_commit`` (or obsidian-git). Same durability contract as
                ``vault_write``.
            idempotency_key: Optional at-most-once token. If set, a retry with
                the same key is a no-op after the first delete (ADR-013), which
                also makes deleting an already-removed file succeed. Empty
                (default) disables idempotency.
        """
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "vault_delete", guard, project)

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(ctx, "vault_delete", project_not_found(project), project)
        project_dir, _ = resolved

        filepath = project_dir / path
        boundary_error = _check_path_boundary(filepath, ctx.vault)
        if boundary_error:
            return track(ctx, "vault_delete", boundary_error, project)

        should_defer = False
        try:
            with vault_write_lock(ctx.vault):
                if idempotency_key and ctx.idempotency.is_applied(idempotency_key):
                    return track(ctx, "vault_delete", _IDEMPOTENT_NOOP, project)

                if not filepath.exists():
                    if idempotency_key:
                        ctx.idempotency.claim(idempotency_key)
                        return track(
                            ctx,
                            "vault_delete",
                            f"Already deleted: {project}/{path}.",
                            project,
                            path,
                        )
                    return track(
                        ctx,
                        "vault_delete",
                        f"File '{path}' not found in project '{project}'.",
                        project,
                    )

                if filepath.is_dir():
                    return track(
                        ctx,
                        "vault_delete",
                        f"'{path}' is a directory; vault_delete removes single files only.",
                        project,
                    )

                try:
                    filepath.unlink()
                except OSError as exc:
                    return track(
                        ctx,
                        "vault_delete",
                        format_io_error(exc, path, "delete"),
                        project,
                    )
                mark_disk_write_done()
                if idempotency_key:
                    ctx.idempotency.claim(idempotency_key)

                rel = filepath.relative_to(ctx.vault)
                should_defer = commit and _should_defer_to_external_committer(ctx.vault)
                # Deliberately NOT routed through the queue (ADR-018 §4).
                # A delete and a recreate inside one tick would collapse to
                # a single state, and git-recoverability is precisely the
                # guarantee this tool sells.
                if commit and not should_defer:
                    _git_commit(ctx.vault, [rel], f"vault: delete {project}/{path}")
        except WriteLockTimeout as exc:
            return track(
                ctx,
                "vault_delete",
                f"Server busy — {exc.reason}. Retry shortly.",
                project,
            )

        suffix = _commit_status_suffix(
            commit,
            should_defer,
            deadline_killed=_was_deadline_killed(),
        )
        return track(ctx, "vault_delete", f"Deleted {project}/{path}.{suffix}", project, path)
