"""Worker operations — delegation, lesson capture, status."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from datetime import date
from typing import TYPE_CHECKING

from hive._helpers import (
    _READ_ONLY,
    _REJECT_MSG,
    _STANDING_ORDER_1_WARNING,
    _SUMMARIZE_THRESHOLD,
    _WRITE,
    _format_metadata,
    _format_response,
    _git_commit,
    _make_frontmatter,
    _resolve_file,
    _resolve_project_dir,
    _safe_read,
    _vault_guard,
    check_lesson_recurrence,
    extract_lesson_headings,
    format_io_error,
    project_not_found,
    tool_span,
    track,
)
from hive.frontmatter import extract_body, parse_frontmatter

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP

    from hive._context import ServerContext
    from hive.clients import ClientResponse

_log = logging.getLogger(__name__)

# HIVE-115 / issue #114 — defensive validation against XML-tag leakage
# from malformed agent tool invocations (e.g. mixing
# ``<parameter name="X">...</X>`` with proper ``...</parameter>``).
# Warn-don't-reject: flag the corruption, keep the write so the agent's
# mid-turn context is not lost; the HTML comment marker is visible
# during manual review of 90-lessons.md.
SUSPECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"</context>"),
    re.compile(r"</parameter>"),
    re.compile(r"<parameter\s+name="),
    re.compile(r"</invoke>"),
)
_CORRUPTION_COMMENT = (
    "<!-- POSSIBLE_CORRUPTION: detected XML-tag leak in input; review and clean manually -->\n"
)


def _scan_for_xml_leak(*fields: str) -> bool:
    """Return True if any SUSPECT pattern appears in any field."""
    for value in fields:
        if not value:
            continue
        for pattern in SUSPECT_PATTERNS:
            if pattern.search(value):
                return True
    return False


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


def _read_existing_lessons_text(project_dir: Path) -> str:
    """Read existing lessons content across 90-lessons.md, docs/lessons.md, and lessons.md."""
    from pathlib import Path as _Path  # noqa: F811

    pdir = _Path(project_dir)
    texts: list[str] = []
    seen: set[_Path] = set()
    for candidate in [
        pdir / "90-lessons.md",
        pdir / "docs" / "lessons.md",
        pdir / "lessons.md",
    ]:
        if candidate.exists() and candidate not in seen:
            seen.add(candidate)
            with contextlib.suppress(OSError, UnicodeDecodeError):
                texts.append(candidate.read_text(encoding="utf-8"))
    return "\n\n".join(texts)


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
        except (OSError, UnicodeDecodeError) as exc:
            return "error", format_io_error(exc, f"{project}/90-lessons.md", "read")

    if f"] {title}\n" in existing:
        return "skipped", f"Lesson already exists: '{title}'. Skipping."

    all_existing = _read_existing_lessons_text(project_dir)
    is_recurrent, matched_heading = check_lesson_recurrence(
        title=title,
        context=context,
        problem=problem,
        solution=solution,
        existing_content=all_existing,
    )
    recurrence_warning = (
        _STANDING_ORDER_1_WARNING.format(heading=matched_heading)
        if is_recurrent and matched_heading
        else ""
    )

    # HIVE-115 / issue #114: scan inputs for XML-leak shapes BEFORE
    # building the entry. Warn-don't-reject preserves the agent's
    # mid-turn context; the HTML comment marker is visible during
    # manual review.
    corruption_detected = _scan_for_xml_leak(title, context, problem, solution)

    tag_str = " ".join(f"`#{t}`" for t in tags)
    entry_lines = [f"\n### [{date.today().isoformat()}] {title}\n"]
    if corruption_detected:
        entry_lines.append(_CORRUPTION_COMMENT)
    entry_lines.extend(
        [
            f"**Context:** {context}\n",
            f"**Problem:** {problem}\n",
            f"**Solution:** {solution}\n",
        ]
    )
    if tag_str:
        entry_lines.append(f"**Tags:** {tag_str}\n")
    entry = "".join(entry_lines)

    try:
        if not lessons_file.exists():
            frontmatter = _make_frontmatter(f"{project}-lessons", "lesson")
            lessons_file.write_text(
                frontmatter + "# Lessons Learned\n" + entry,
                encoding="utf-8",
            )
        else:
            with lessons_file.open("a", encoding="utf-8") as f:
                f.write(entry)
    except OSError as exc:
        return "error", format_io_error(exc, f"{project}/90-lessons.md", "write")

    suffix = (
        " [WARNING: POSSIBLE_CORRUPTION marker added — XML-tag leak detected "
        "in input fields; review 90-lessons.md and clean manually]"
        if corruption_detected
        else ""
    )
    return (
        "written",
        f"Lesson captured: '{title}' → {project}/90-lessons.md{suffix}{recurrence_warning}",
    )


def _parse_lessons_json(raw: str) -> list[dict[str, object]]:
    """Parse JSON from worker response, stripping markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = "\n".join(ln for ln in lines[1:] if not ln.strip().startswith("```"))
        text = inner.strip()
    if not text.startswith("["):
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            text = match.group(0)
    return json.loads(text)  # type: ignore[no-any-return]


def _capture_lesson_lookup(
    ctx: ServerContext,
    project_dir: Path,
    project: str,
    find: str,
    rank_by: str,
    max_lessons: int,
) -> str:
    """capture_lesson lookup mode (HIVE-97 find=).

    Greps the project's 90-lessons.md headings for ``find`` (case-
    insensitive), validates ``rank_by``, increments each surfaced
    lesson once, and renders the top-N matches ordered by the chosen
    usage signal via ``ctx.lessons.top``.
    """
    if rank_by not in {"reinforcements", "confidence", "hybrid"}:
        return track(
            ctx,
            "capture_lesson",
            f"Unknown rank_by={rank_by!r}. Expected one of: reinforcements, confidence, hybrid.",
            project,
        )

    lessons_file = project_dir / "90-lessons.md"
    if not lessons_file.exists():
        return track(
            ctx,
            "capture_lesson",
            f"No 90-lessons.md found for project '{project}'.",
            project,
            "lessons",
        )
    content = _safe_read(lessons_file)
    if content is None:
        return track(
            ctx,
            "capture_lesson",
            f"Could not read 90-lessons.md for project '{project}'.",
            project,
            "lessons",
        )

    find_lower = find.lower()
    matched = [h for h in extract_lesson_headings(content) if find_lower in h.lower()]
    if not matched:
        return track(
            ctx,
            "capture_lesson",
            f"No lessons matching '{find}' in project '{project}'.",
            project,
            "lessons",
        )

    # Per-call dedup + increment (one read per surfaced lesson).
    matched_set = set(matched)
    for heading in dict.fromkeys(matched):
        ctx.lessons.increment(project, heading)

    bm25_scores = {h: 1.0 for h in matched_set}
    ranked = ctx.lessons.top(
        project,
        by=rank_by,
        limit=max_lessons,
        bm25_scores=bm25_scores,
    )
    ordered = [h for h in ranked if h in matched_set][:max_lessons]

    rendered = [
        f"# Lessons matching '{find}' ({rank_by}, top {len(ordered)}):",
        "",
    ]
    for heading in ordered:
        rendered.append(f"- {heading}")
    return track(
        ctx,
        "capture_lesson",
        "\n".join(rendered),
        project,
        "lessons",
    )


# HIVE-384: model ids the 4.0.0 removal retired. Rejected with a message that
# names the replacement, never silently ignored — a dead alias that is quietly
# accepted surfaces later as a confusing inference failure instead of a clear
# validation error, which is the difference between a break a caller can fix and
# one they have to debug.
_RETIRED_MODEL_ALIASES = frozenset({"auto", "ollama", "openrouter-free", "openrouter"})


def register_workers(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register worker tools on the MCP server."""

    def _record(resp: ClientResponse) -> None:
        """Record a successful response as usage telemetry (tokens, latency)."""
        ctx.budget.record_request(
            model=resp.model,
            tokens=resp.tokens,
            latency_ms=resp.latency_ms,
            task_type="delegate",
        )

    async def _try_worker(
        prompt: str,
        max_tokens: int,
        context: str = "",
        model: str = "",
    ) -> tuple[ClientResponse | None, str, str]:
        """Run one inference against the worker. One shot, no fallback.

        Returns ``(response, status, detail)`` where ``status`` is one of
        ``"ok"``, ``"pool_unavailable"`` or ``"task_failed"``.

        **The classification travels as data, deliberately** (HIVE-384). The
        previous version returned ``(None, "some string")`` for every failure,
        catching ``(ConnectionError, RuntimeError)`` and then broad ``Exception``
        into one shape. A caller cannot tell *the provider was unreachable* from
        *the provider answered and the answer failed*, and those must stay
        distinguishable: a dispatcher advances its fallback chain on the first
        and must not on the second, because collapsing them turns a bad answer
        into a silent retry against a different model. Exception types also do
        not survive the JSON-RPC boundary between the daemon and its clients, so
        the distinction has to be a value, not a type.

        The fallback chain itself lives in the caller, not here: a second
        routing authority inside a backend is exactly what the routing registry
        exists to prevent.
        """
        if ctx.worker is None:
            return None, "pool_unavailable", "worker not configured"
        try:
            resp = await ctx.worker.generate(
                prompt,
                context=context,
                model=model or ctx.worker.model,
                max_tokens=max_tokens,
            )
            _record(resp)
            return resp, "ok", ""
        except ConnectionError as exc:
            # Unreachable, refused, rate-limited, auth rejected — the pool did
            # not serve the request. Retrying elsewhere is legitimate.
            return None, "pool_unavailable", f"worker unreachable: {exc}"
        except RuntimeError as exc:
            # The provider answered and the answer is unusable. Retrying the
            # same task on another model would hide a real failure.
            return None, "task_failed", f"worker error: {exc}"
        except Exception as exc:
            _log.warning("_try_worker unexpected error: %r", exc)
            # Unknown failures classify as task_failed, never as unavailable:
            # the fail-closed direction is the one that does NOT retry.
            return None, "task_failed", f"unexpected error ({type(exc).__name__})"

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
        find: str = "",
        rank_by: str = "reinforcements",
    ) -> str:
        """Capture lessons: inline / batch write, or lookup by keyword.

        Inline mode (default): provide title, context, problem, solution.
        Batch mode: provide text to extract lessons automatically via worker.
        Lookup mode: provide ``find`` to surface top-ranked existing
        lessons whose heading matches the keyword.

        Args:
            project: Project slug (directory under 10_projects/).
            title: Short descriptive title (inline mode).
            context: What you were doing (inline mode).
            problem: What went wrong or what decision was needed (inline mode).
            solution: What fixed it or what was decided (inline mode).
            tags: Optional tags (e.g. ["python", "testing"]).
            text: Raw text to extract lessons from (batch mode).
            min_confidence: Minimum confidence for batch extraction. Default 0.7.
            max_lessons: Maximum lessons to extract / surface. Default 5.
            find: Keyword to look up in existing lesson headings (lookup mode).
            rank_by: Lookup ranking — 'reinforcements' (default), 'confidence',
                or 'hybrid'. Ignored unless ``find`` is set.

        Note: there is no `commit` parameter here — lesson writes always
        auto-commit. `commit` lives on ``vault_write`` / ``vault_patch``.
        """
        guard = _vault_guard(ctx)
        if guard:
            return track(ctx, "capture_lesson", guard, project)

        try:
            async with tool_span("capture_lesson", ctx.tool_timeout):
                resolved = _resolve_project_dir(ctx.vault, project, ctx.scopes)
                if resolved is None:
                    return track(
                        ctx,
                        "capture_lesson",
                        project_not_found(project),
                        project,
                    )
                project_dir, _ = resolved

                # ── Lookup mode (HIVE-97 find=) ──
                if find:
                    return _capture_lesson_lookup(
                        ctx,
                        project_dir,
                        project,
                        find,
                        rank_by,
                        max_lessons,
                    )

                # ── Batch mode (worker extraction) ──
                if text:
                    truncated = text[:_MAX_EXTRACT_INPUT]
                    safe_text = truncated.replace("{", "{{").replace("}", "}}")
                    prompt = _EXTRACT_PROMPT.format(
                        max_lessons=max_lessons,
                        min_confidence=min_confidence,
                        text=safe_text,
                    )

                    # HIVE-384: capture_lesson is the worker's second consumer.
                    # Its MCP surface is unchanged; only which provider answers
                    # is, so the two-tier ladder collapses to the same single
                    # shot delegate_task now makes.
                    resp: ClientResponse | None = None
                    resp, _status, detail = await _try_worker(prompt, 2000)

                    if resp is None:
                        return track(
                            ctx,
                            "capture_lesson",
                            f"Worker unavailable [{detail}]. "
                            "Cannot extract lessons without a worker model.",
                            project,
                        )

                    try:
                        lessons_raw = _parse_lessons_json(resp.text)
                    except (json.JSONDecodeError, ValueError):
                        snippet = resp.text[:200]
                        return track(
                            ctx,
                            "capture_lesson",
                            f"Could not parse worker response as JSON: {snippet}",
                            project,
                        )

                    if not isinstance(lessons_raw, list):
                        return track(
                            ctx,
                            "capture_lesson",
                            "Worker returned non-array JSON.",
                            project,
                        )

                    if not lessons_raw:
                        return track(
                            ctx,
                            "capture_lesson",
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

                        l_ctx = str(lesson.get("context", ""))
                        l_ctx = l_ctx.replace("\n", " ").replace("\r", " ")
                        l_prob = str(lesson.get("problem", ""))
                        l_prob = l_prob.replace("\n", " ").replace("\r", " ")
                        l_sol = str(lesson.get("solution", ""))
                        l_sol = l_sol.replace("\n", " ").replace("\r", " ")
                        raw_tags = lesson.get("tags", [])
                        l_tags = (
                            [str(t).replace("\n", " ").replace("\r", " ") for t in raw_tags]
                            if isinstance(raw_tags, list)
                            else []
                        )

                        status, msg = _write_lesson(
                            project_dir,
                            project,
                            l_title,
                            l_ctx,
                            l_prob,
                            l_sol,
                            l_tags,
                        )
                        if status == "written":
                            written.append(l_title)
                            ctx.lessons.ensure(
                                project,
                                f"[{date.today().isoformat()}] {l_title}",
                                confidence,
                            )
                        elif status == "skipped":
                            skipped.append(f"{l_title} (duplicate)")
                        elif status == "error":
                            _log.warning(
                                "capture_lesson: failed to write '%s': %s",
                                l_title,
                                msg,
                            )
                            skipped.append(f"{l_title} (write error: {msg})")

                    if written:
                        rel = (project_dir / "90-lessons.md").relative_to(ctx.vault)
                        _git_commit(
                            ctx.vault,
                            [rel],
                            f"vault: capture_lesson {project} — {len(written)} lessons",
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
                        ctx,
                        "capture_lesson",
                        summary,
                        project,
                        "lessons",
                    )

                # ── Inline mode ──
                if not title:
                    return track(
                        ctx,
                        "capture_lesson",
                        "Title is required for inline mode. Provide text for batch extraction.",
                        project,
                    )

                status, msg = _write_lesson(
                    project_dir,
                    project,
                    title,
                    context,
                    problem,
                    solution,
                    tags,
                )
                if status == "error":
                    return track(ctx, "capture_lesson", msg, project)
                if status == "skipped":
                    return track(ctx, "capture_lesson", msg, project, "lessons")

                ctx.lessons.ensure(
                    project,
                    f"[{date.today().isoformat()}] {title}",
                )

                rel = (project_dir / "90-lessons.md").relative_to(ctx.vault)
                _git_commit(
                    ctx.vault,
                    [rel],
                    f"vault: capture_lesson {project} — {title}",
                )

                return track(ctx, "capture_lesson", msg, project, "lessons")
        except TimeoutError:
            return track(
                ctx,
                "capture_lesson",
                f"Tool timed out after {ctx.tool_timeout:.0f}s. "
                "Worker may be slow or unresponsive.",
                project,
            )

    @mcp.tool(annotations=_WRITE)
    async def delegate_task(
        prompt: str = "",
        context: str = "",
        model: str = "",
        max_tokens: int = 2000,
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
            model: Concrete model id. Empty uses the configured worker model.
                The 4.0.0 removal retired 'auto', 'ollama', 'openrouter-free'
                and 'openrouter'; passing one is rejected rather than ignored.
            max_tokens: Maximum tokens in the response.
            project: Project slug for vault summarization mode.
            section: Shortcut name for summarization. Ignored if path is set.
            path: Relative path to a .md file. Overrides section.
            max_summary_lines: Target summary length for summarization.
        """
        try:
            async with tool_span("delegate_task", ctx.tool_timeout):
                # ── Vault summarize mode ──
                if project:
                    result = _resolve_file(
                        ctx.vault,
                        project,
                        section,
                        path,
                        ctx.scopes,
                    )
                    if isinstance(result, str):
                        return track(ctx, "delegate_task", result, project)
                    filepath = result

                    try:
                        file_content = filepath.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError) as exc:
                        return track(
                            ctx,
                            "delegate_task",
                            f"File I/O error: {exc}",
                            project,
                        )
                    fm = parse_frontmatter(file_content)
                    body = extract_body(file_content)
                    line_count = len(file_content.splitlines())
                    meta = _format_metadata(fm)
                    header = f"**Metadata:** {meta}\n\n" if meta else ""

                    if line_count <= _SUMMARIZE_THRESHOLD:
                        return track(
                            ctx,
                            "delegate_task",
                            f"{header}{file_content}",
                            project,
                        )

                    # Large file: try auto-delegation (free tiers only)
                    summary_prompt = (
                        f"Summarize the following document in at most "
                        f"{max_summary_lines} lines, preserving key decisions "
                        f"and action items.\n\n{body}"
                    )
                    summary_ctx = f"Document metadata: {meta}" if meta else ""
                    summary_resp, _status, summary_detail = await _try_worker(
                        summary_prompt,
                        max_tokens,
                        summary_ctx,
                    )
                    if summary_resp is not None:
                        return track(
                            ctx,
                            "delegate_task",
                            f"{header}{_format_response(summary_resp)}",
                            project,
                        )

                    # Worker unavailable — degrade to raw content with a notice.
                    # The raw file is still useful, so this stays a graceful
                    # degradation rather than an error; the notice exists so the
                    # caller knows it received the document rather than a summary.
                    _log.warning(
                        "delegate_task summarize fallback for %s: %s",
                        project,
                        summary_detail,
                    )
                    fallback_notice = (
                        f"**Note:** Summarization failed ({summary_detail}). "
                        "Returning raw content.\n\n"
                    )
                    return track(
                        ctx,
                        "delegate_task",
                        f"{header}{fallback_notice}{file_content}",
                        project,
                    )

                if not prompt:
                    return track(ctx, "delegate_task", "Either prompt or project is required.")

                # ── Worker delegation ──
                #
                # HIVE-384: one worker, one shot. The tiered ladder that stood
                # here (Ollama → OpenRouter free → OpenRouter paid → reject)
                # went with its providers, and the shape went with it on
                # purpose: choosing among pools is the dispatcher's job, and a
                # backend that picks its own fallback is a second routing
                # authority whose answer nobody can attribute.
                _tn = "delegate_task"

                if model in _RETIRED_MODEL_ALIASES:
                    return track(
                        ctx,
                        _tn,
                        f"'{model}' was removed in 4.0.0 — the worker now runs a "
                        "single OpenAI-compatible provider. Pass a concrete model "
                        "id, or omit `model` to use the configured default.",
                    )

                resp, status, detail = await _try_worker(
                    prompt,
                    max_tokens,
                    context,
                    model=model,
                )
                if resp is not None:
                    return track(ctx, _tn, _format_response(resp))
                return track(ctx, _tn, f"{detail}. {_REJECT_MSG}")
        except TimeoutError:
            return track(
                ctx,
                "delegate_task",
                f"Tool timed out after {ctx.tool_timeout:.0f}s. "
                "Worker may be slow or unresponsive.",
            )

    @mcp.tool(annotations=_READ_ONLY)
    async def worker_status(include_models: bool = True) -> str:
        """Show worker health: configuration, reachability, model, and usage.

        HIVE-384 reshaped this tool, and the reshape is the point rather than a
        side effect. The old output led with a dollar budget and reported two
        providers by *configuration*: it said "Ollama: offline / OpenRouter: no
        API key" for an unknown length of time while every caller treated the
        worker as a working capability. A status surface that cannot distinguish
        "configured" from "answers" is how a dead backend stays invisible.

        So reachability is **probed, not inferred**, and reported separately
        from configuration. The dollar figures are gone: on a flat subscription
        they would read zero forever, and a gauge that always says the same
        thing looks like a working gauge.

        Args:
            include_models: Probe the provider for its model list. Default True.
                Set False to report configuration without a network call.
        """
        try:
            async with tool_span("worker_status", ctx.tool_timeout):
                usage = ctx.budget.month_usage()
                configured = ctx.worker is not None

                lines = [
                    "# Worker Status",
                    "",
                    "## Provider",
                    f"- Configured: {'yes' if configured else 'no — set HIVE_WORKER_BASE_URL'}",
                ]
                if configured and ctx.worker is not None:
                    lines.append(f"- Model: {ctx.worker.model or '(none configured)'}")

                # Reachability is a probe. "Configured" is not a claim that the
                # endpoint answers, and the two must never be printed as one.
                if configured and include_models and ctx.worker is not None:
                    try:
                        models = await ctx.worker.list_models()
                        lines.append(f"- Reachable: yes ({len(models)} models)")
                    except (ConnectionError, RuntimeError, TimeoutError) as exc:
                        models = []
                        lines.append(f"- Reachable: NO — {exc}")
                else:
                    models = []
                    lines.append("- Reachable: unprobed")

                lines += [
                    "",
                    "## Usage this month",
                    f"- Requests: {usage['request_count']}",
                    f"- Tokens: {usage['total_tokens']}",
                    "",
                ]

                if usage["by_model"]:
                    lines.append("## By model")
                    for model_name, model_stats in usage["by_model"].items():
                        lines.append(
                            f"- **{model_name}**: {model_stats['count']} requests, "
                            f"{model_stats['tokens']} tokens, "
                            f"avg {model_stats['avg_latency_ms']}ms",
                        )
                    lines.append("")

                if include_models and models:
                    lines.append("## Available Models")
                    for m in models:
                        lines.append(f"- **{m.id}** — {m.name}, ctx: {m.context_length}")
                    lines.append("")

                return track(ctx, "worker_status", "\n".join(lines))
        except TimeoutError:
            return "Worker status timed out. Workers may be unreachable."
