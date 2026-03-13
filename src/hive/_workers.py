"""Worker operations — delegation, lesson capture, status."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import TYPE_CHECKING

from hive._helpers import (
    _READ_ONLY,
    _REJECT_MSG,
    _SUMMARIZE_THRESHOLD,
    _WRITE,
    _format_metadata,
    _format_response,
    _git_commit,
    _make_frontmatter,
    _resolve_file,
    _resolve_project_dir,
    _vault_guard,
    track,
)
from hive.frontmatter import extract_body, parse_frontmatter

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP

    from hive._context import ServerContext
    from hive.clients import ClientResponse

_log = logging.getLogger(__name__)

_EXTRACT_PROMPT = (
    "Extract key lessons from the following text. A lesson is a decision, "
    "bug root cause, or pattern choice worth remembering.\n\n"
    "For each lesson, provide a JSON object with:\n"
    '- "title": Short descriptive title (max 10 words)\n'
    '- "context": What was being done (1 sentence)\n'
    '- "problem": What went wrong or what decision was needed (1 sentence)\n'
    '- "solution": What fixed it or what was decided (1 sentence)\n'
    '- "tags": List of 1-3 relevant tags (lowercase, no #)\n'
    '- "confidence": 0.0-1.0 how confident this is a real, reusable lesson\n\n'
    "Return ONLY a JSON array. No markdown, no explanation. "
    "Max {max_lessons} lessons. "
    "Only include lessons with confidence > {min_confidence}.\n"
    "If no lessons found, return: []\n\n"
    "Text:\n---\n{text}\n---"
)

_MAX_EXTRACT_INPUT = 8000  # chars, safe for any worker model


def _write_lesson(
    project_dir: Path,
    project: str,
    title: str,
    context: str,
    problem: str,
    solution: str,
    tags: list[str],
) -> tuple[str, str]:
    """Write a single lesson to 90-lessons.md. Returns (status, message).

    Status is one of: 'written', 'skipped' (duplicate), 'error'.
    """
    from pathlib import Path as _Path  # noqa: F811

    lessons_file = _Path(project_dir) / "90-lessons.md"

    existing = ""
    if lessons_file.exists():
        try:
            existing = lessons_file.read_text(encoding="utf-8")
        except OSError as exc:
            return "error", f"File I/O error: {exc}"

    if f"] {title}\n" in existing:
        return "skipped", f"Lesson already exists: '{title}'. Skipping."

    tag_str = " ".join(f"`#{t}`" for t in tags)
    entry = (
        f"\n### [{date.today().isoformat()}] {title}\n"
        f"**Context:** {context}\n"
        f"**Problem:** {problem}\n"
        f"**Solution:** {solution}\n"
    )
    if tag_str:
        entry += f"**Tags:** {tag_str}\n"

    try:
        if not lessons_file.exists():
            frontmatter = _make_frontmatter(f"{project}-lessons", "lesson")
            lessons_file.write_text(
                frontmatter + "# Lessons Learned\n" + entry, encoding="utf-8",
            )
        else:
            with lessons_file.open("a", encoding="utf-8") as f:
                f.write(entry)
    except OSError as exc:
        return "error", f"File I/O error: {exc}"

    return "written", f"Lesson captured: '{title}' → {project}/90-lessons.md"


def _parse_lessons_json(raw: str) -> list[dict[str, object]]:
    """Parse JSON from worker response, stripping markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = "\n".join(
            ln for ln in lines[1:] if not ln.strip().startswith("```")
        )
        text = inner.strip()
    if not text.startswith("["):
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            text = match.group(0)
    return json.loads(text)  # type: ignore[no-any-return]


def register_workers(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register worker tools on the MCP server."""

    def _record(resp: ClientResponse) -> None:
        """Record a successful response in the budget tracker."""
        ctx.budget.record_request(
            model=resp.model,
            cost_usd=resp.cost_usd,
            tokens=resp.tokens,
            latency_ms=resp.latency_ms,
            task_type="delegate",
        )

    async def _try_worker(
        prompt: str,
        max_tokens: int,
        provider: str = "ollama",
        context: str = "",
        model: str = "",
    ) -> tuple[ClientResponse | None, str]:
        """Try to generate via a worker provider.

        Returns (response, "") on success or (None, error_detail) on failure.
        """
        try:
            if provider == "ollama":
                resp = await ctx.ollama.generate(
                    prompt, context=context, max_tokens=max_tokens,
                )
            elif ctx.openrouter is None:
                return None, "OpenRouter not configured"
            elif model:
                resp = await ctx.openrouter.generate(
                    prompt, context=context, model=model, max_tokens=max_tokens,
                )
            else:
                resp = await ctx.openrouter.generate(
                    prompt, context=context, max_tokens=max_tokens,
                )
            _record(resp)
            return resp, ""
        except (ConnectionError, RuntimeError) as exc:
            label = f"OpenRouter ({model})" if model else provider.capitalize()
            return None, f"{label}: {exc}"

    @mcp.tool(annotations=_WRITE)
    async def capture_lesson(
        project: str,
        title: str = "",
        context: str = "",
        problem: str = "",
        solution: str = "",
        tags: list[str] = [],  # noqa: B006
        text: str = "",
        min_confidence: float = 0.7,
        max_lessons: int = 5,
    ) -> str:
        """Capture lessons: inline (structured fields) or batch (raw text via worker).

        Inline mode (default): provide title, context, problem, solution.
        Batch mode: provide text to extract lessons automatically via worker.

        Args:
            project: Project slug (directory under 10_projects/).
            title: Short descriptive title (inline mode).
            context: What you were doing (inline mode).
            problem: What went wrong or what decision was needed (inline mode).
            solution: What fixed it or what was decided (inline mode).
            tags: Optional tags (e.g. ["python", "testing"]).
            text: Raw text to extract lessons from (batch mode).
            min_confidence: Minimum confidence for batch extraction. Default 0.7.
            max_lessons: Maximum lessons to extract in batch mode. Default 5.
        """
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "capture_lesson", guard, project)

        resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
        if resolved is None:
            return track(
                ctx, "capture_lesson",
                f"Project '{project}' not found in vault.",
                project,
            )
        project_dir, _ = resolved

        # ── Batch mode (worker extraction) ──
        if text:
            truncated = text[:_MAX_EXTRACT_INPUT]
            safe_text = truncated.replace("{", "{{").replace("}", "}}")
            prompt = _EXTRACT_PROMPT.format(
                max_lessons=max_lessons,
                min_confidence=min_confidence,
                text=safe_text,
            )

            errors: list[str] = []
            resp: ClientResponse | None = None

            if await ctx.ollama.is_available():
                resp, err = await _try_worker(prompt, 2000, "ollama")
                if resp is None:
                    errors.append(err)
            else:
                errors.append("Ollama: offline")

            if resp is None:
                resp, err = await _try_worker(
                    prompt, 2000, "openrouter",
                )
                if resp is None:
                    errors.append(err)

            if resp is None:
                reasons = (
                    "; ".join(errors)
                    if errors
                    else "no workers configured"
                )
                return track(
                    ctx, "capture_lesson",
                    f"All workers unavailable [{reasons}]. "
                    "Cannot extract lessons without a worker model.",
                    project,
                )

            try:
                lessons_raw = _parse_lessons_json(resp.text)
            except (json.JSONDecodeError, ValueError):
                snippet = resp.text[:200]
                return track(
                    ctx, "capture_lesson",
                    f"Could not parse worker response as JSON: "
                    f"{snippet}",
                    project,
                )

            if not isinstance(lessons_raw, list):
                return track(
                    ctx, "capture_lesson",
                    "Worker returned non-array JSON.",
                    project,
                )

            if not lessons_raw:
                return track(
                    ctx, "capture_lesson",
                    "No lessons found in text.",
                    project,
                )

            written: list[str] = []
            skipped: list[str] = []
            for lesson in lessons_raw[:max_lessons]:
                if not isinstance(lesson, dict):
                    continue
                raw_title = str(lesson.get("title", "")).strip()
                l_title = raw_title.replace("\n", " ").replace("\r", " ")
                if not l_title:
                    continue
                try:
                    confidence = float(
                        str(lesson.get("confidence", 0.5)),
                    )
                except (ValueError, TypeError):
                    confidence = 0.5
                if confidence < min_confidence:
                    skipped.append(
                        f"{l_title} (confidence {confidence:.1f})",
                    )
                    continue

                l_ctx = str(lesson.get("context", "")).replace("\n", " ").replace("\r", " ")
                l_prob = str(lesson.get("problem", "")).replace("\n", " ").replace("\r", " ")
                l_sol = str(lesson.get("solution", "")).replace("\n", " ").replace("\r", " ")
                raw_tags = lesson.get("tags", [])
                l_tags = [
                    str(t).replace("\n", " ").replace("\r", " ")
                    for t in raw_tags
                ] if isinstance(raw_tags, list) else []

                status, msg = _write_lesson(
                    project_dir, project,
                    l_title, l_ctx, l_prob, l_sol, l_tags,
                )
                if status == "written":
                    written.append(l_title)
                elif status == "skipped":
                    skipped.append(f"{l_title} (duplicate)")
                elif status == "error":
                    _log.warning(
                        "capture_lesson: failed to write '%s': %s",
                        l_title, msg,
                    )
                    skipped.append(f"{l_title} (write error: {msg})")

            if written:
                rel = (
                    project_dir / "90-lessons.md"
                ).relative_to(ctx.vault)
                _git_commit(
                    ctx.vault, rel,
                    f"vault: capture_lesson {project} "
                    f"— {len(written)} lessons",
                )

            parts: list[str] = []
            if written:
                titles = ", ".join(written)
                parts.append(
                    f"Extracted {len(written)} lessons: {titles}",
                )
            if skipped:
                skip_details = ", ".join(skipped)
                parts.append(f"Skipped {len(skipped)}: {skip_details}")
            if not written and not skipped:
                parts.append("No lessons found in text.")

            summary = ". ".join(parts) + "."
            return track(
                ctx, "capture_lesson", summary, project, "lessons",
            )

        # ── Inline mode ──
        if not title:
            return track(
                ctx, "capture_lesson",
                "Title is required for inline mode. "
                "Provide text for batch extraction.",
                project,
            )

        status, msg = _write_lesson(
            project_dir, project,
            title, context, problem, solution, tags,
        )
        if status == "error":
            return track(ctx, "capture_lesson", msg, project)
        if status == "skipped":
            return track(ctx, "capture_lesson", msg, project, "lessons")

        rel = (project_dir / "90-lessons.md").relative_to(ctx.vault)
        _git_commit(
            ctx.vault, rel,
            f"vault: capture_lesson {project} — {title}",
        )

        return track(ctx, "capture_lesson", msg, project, "lessons")

    @mcp.tool(annotations=_WRITE)
    async def delegate_task(
        prompt: str = "",
        context: str = "",
        model: str = "auto",
        max_tokens: int = 2000,
        max_cost_per_request: float = 0.0,
        project: str = "",
        section: str = "context",
        path: str = "",
        max_summary_lines: int = 20,
    ) -> str:
        """Offload work to a cheaper model or summarize vault files.

        When project is provided, reads a vault file. Small files (≤50 lines)
        are returned directly. Large files are auto-delegated to a worker for
        summarization — falls back to raw content if workers are unavailable.

        Args:
            prompt: The task description or code to process.
            context: Optional system context for the model.
            model: 'auto', 'ollama', 'openrouter-free', 'openrouter' (paid), or model ID.
            max_tokens: Maximum tokens in the response.
            max_cost_per_request: Max USD. 0 = free models only.
            project: Project slug for vault summarization mode.
            section: Shortcut name for summarization. Ignored if path is set.
            path: Relative path to a .md file. Overrides section.
            max_summary_lines: Target summary length for summarization.
        """
        # ── Vault summarize mode ──
        if project:
            result = _resolve_file(
                ctx.vault, project, section, path, ctx.scopes,
            )
            if isinstance(result, str):
                return track(ctx, "delegate_task", result, project)
            filepath = result

            try:
                file_content = filepath.read_text(encoding="utf-8")
            except OSError as exc:
                return track(
                    ctx, "delegate_task", f"File I/O error: {exc}", project,
                )
            fm = parse_frontmatter(file_content)
            body = extract_body(file_content)
            line_count = len(file_content.splitlines())
            meta = _format_metadata(fm)
            header = f"**Metadata:** {meta}\n\n" if meta else ""

            if line_count <= _SUMMARIZE_THRESHOLD:
                return track(
                    ctx, "delegate_task", f"{header}{file_content}", project,
                )

            # Large file: try auto-delegation (free tiers only)
            summary_prompt = (
                f"Summarize the following document in at most "
                f"{max_summary_lines} lines, preserving key decisions "
                f"and action items.\n\n{body}"
            )
            summary_ctx = f"Document metadata: {meta}" if meta else ""
            summary_resp: ClientResponse | None = None
            summary_errors: list[str] = []
            if await ctx.ollama.is_available():
                summary_resp, err = await _try_worker(
                    summary_prompt, max_tokens, "ollama", summary_ctx,
                )
                if err:
                    summary_errors.append(err)
            else:
                summary_errors.append("Ollama: offline")
            if summary_resp is None:
                summary_resp, err = await _try_worker(
                    summary_prompt, max_tokens, "openrouter", summary_ctx,
                )
                if err:
                    summary_errors.append(err)
            if summary_resp is not None:
                return track(
                    ctx, "delegate_task",
                    f"{header}{_format_response(summary_resp)}",
                    project,
                )
            # Workers unavailable — return raw content with notice
            reasons = "; ".join(summary_errors) if summary_errors else "no workers configured"
            _log.warning("delegate_task summarize fallback for %s: %s", project, reasons)
            fallback_notice = (
                f"**Note:** Summarization failed ({reasons}). "
                "Returning raw content.\n\n"
            )
            return track(
                ctx, "delegate_task", f"{header}{fallback_notice}{file_content}", project,
            )

        if not prompt:
            return track(ctx, "delegate_task", "Either prompt or project is required.")

        # ── Worker delegation ──
        _tn = "delegate_task"

        # Explicit routing
        if model == "ollama":
            resp, err = await _try_worker(prompt, max_tokens, "ollama", context)
            if resp:
                return track(ctx, _tn, _format_response(resp))
            return track(ctx, _tn, f"{err}. {_REJECT_MSG}")
        if model == "openrouter-free":
            resp, err = await _try_worker(prompt, max_tokens, "openrouter", context)
            if resp:
                return track(ctx, _tn, _format_response(resp))
            return track(ctx, _tn, f"{err}. {_REJECT_MSG}")
        if model == "openrouter":
            if not ctx.budget.can_spend(ctx.openrouter_budget, max_cost_per_request):
                return track(ctx, _tn, f"Monthly budget exhausted. {_REJECT_MSG}")
            resp, err = await _try_worker(
                prompt, max_tokens, "openrouter", context,
                model=ctx.openrouter_paid_model,
            )
            if resp:
                return track(ctx, _tn, _format_response(resp))
            return track(ctx, _tn, f"{err}. {_REJECT_MSG}")
        if model != "auto":
            resp, err = await _try_worker(
                prompt, max_tokens, "openrouter", context, model=model,
            )
            if resp:
                return track(ctx, _tn, _format_response(resp))
            return track(ctx, _tn, f"{err}. {_REJECT_MSG}")

        # Auto routing: Ollama → OpenRouter free → OpenRouter paid → reject
        errors: list[str] = []

        # Tier 1: Ollama
        if await ctx.ollama.is_available():
            resp, err = await _try_worker(prompt, max_tokens, "ollama", context)
            if resp is not None:
                return track(ctx, _tn, _format_response(resp))
            errors.append(err)
        else:
            errors.append("Ollama: offline")

        # Tier 2: OpenRouter free
        resp, err = await _try_worker(prompt, max_tokens, "openrouter", context)
        if resp is not None:
            return track(ctx, _tn, _format_response(resp))
        errors.append(err)

        # Tier 3: OpenRouter paid (only if max_cost > 0 and budget allows)
        if (
            max_cost_per_request > 0
            and ctx.openrouter is not None
            and ctx.budget.can_spend(ctx.openrouter_budget, max_cost_per_request)
        ):
            resp, err = await _try_worker(
                prompt, max_tokens, "openrouter", context,
                model=ctx.openrouter_paid_model,
            )
            if resp is not None:
                return track(ctx, _tn, _format_response(resp))
            errors.append(err)

        # All tiers exhausted
        reasons = "; ".join(errors)
        return track(ctx, _tn, f"All workers unavailable. [{reasons}]. {_REJECT_MSG}")

    @mcp.tool(annotations=_READ_ONLY)
    async def worker_status(include_models: bool = True) -> str:
        """Show worker health: budget, connectivity, available models, and usage stats.

        Args:
            include_models: Include available model list from all providers. Default True.
        """
        stats = ctx.budget.month_stats(ctx.openrouter_budget)
        ollama_up = await ctx.ollama.is_available()

        lines = [
            "# Worker Status",
            "",
            "## Budget",
            f"- Spent this month: ${stats['spent']:.2f}",
            f"- Remaining: ${stats['remaining']:.2f} / ${ctx.openrouter_budget:.1f}",
            f"- Requests: {stats['request_count']}",
            "",
            "## Connectivity",
            f"- Ollama: {'online' if ollama_up else 'offline / unavailable'}",
            f"- OpenRouter: {'configured' if ctx.openrouter is not None else 'no API key'}",
            "",
        ]

        if stats["by_model"]:
            lines.append("## Top Models")
            for model_name, model_stats in stats["by_model"].items():
                lines.append(
                    f"- **{model_name}**: {model_stats['count']} requests, "
                    f"${model_stats['total_cost']:.4f}, avg {model_stats['avg_latency_ms']}ms"
                )
            lines.append("")

        if include_models:
            lines.append("## Available Models")
            lines.append("")
            ollama_status = "online" if ollama_up else "offline / unavailable"
            lines.append(f"### Ollama ({ollama_status})")
            if ollama_up:
                lines.append(f"- **{ctx.ollama.model}** — local, free, no token limit")
            lines.append("")
            lines.append("### OpenRouter")
            if ctx.openrouter is not None:
                try:
                    models = await ctx.openrouter.list_models()
                    for m in models:
                        cost = "free" if m.is_free else f"${m.cost_per_million_input:.2f}/M in"
                        lines.append(f"- **{m.id}** — {m.name}, ctx: {m.context_length}, {cost}")
                except (ConnectionError, RuntimeError, TimeoutError) as exc:
                    lines.append(f"- Error fetching models: {exc}")
            else:
                lines.append("- No API key configured")

        return "\n".join(lines)
