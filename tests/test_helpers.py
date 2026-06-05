"""Tests for helper functions — _match_and_replace, _vault_guard, tool_span."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from hive._helpers import (
    _default_scopes,
    _git_commit,
    _match_and_replace,
    _resolve_project_dir,
    _strip_code,
    _vault_guard,
    find_lesson_heading,
    tool_span,
)

if TYPE_CHECKING:
    from pathlib import Path

_FM = "---\nid: test\ntype: note\nstatus: active\n---\n\n"


class TestMatchAndReplace:
    """Tests for _match_and_replace cascading logic."""

    def test_exact_match_full_file(self) -> None:
        """Pass 1: exact match on full content including frontmatter works."""
        content = _FM + "# Title\n\nHello world\n"
        ok, new_content = _match_and_replace(content, "Hello world", "Goodbye world")
        assert ok
        assert "Goodbye world" in new_content
        assert new_content.startswith("---")

    def test_exact_match_body_only(self) -> None:
        """Pass 2: find text matches body but is ambiguous in full file."""
        # "active" appears in frontmatter AND body — ambiguous in Pass 1,
        # but unique in Pass 2 (body-only)
        content = _FM + "# Title\n\nStatus: active\n"
        ok, new_content = _match_and_replace(
            content,
            "Status: active",
            "Status: done",
        )
        assert ok
        assert new_content.startswith("---")
        assert "Status: done" in new_content

    def test_whitespace_normalized_match(self) -> None:
        """Pass 3: trailing whitespace differences tolerated."""
        content = _FM + "# Title\n\n| A | B |   \n|---|---|\n| 1 | 2 |  \n"
        # LLM stripped trailing spaces
        find_text = "| A | B |\n|---|---|\n| 1 | 2 |"
        ok, new_content = _match_and_replace(
            content,
            find_text,
            "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |",
        )
        assert ok
        assert "| C |" in new_content

    def test_ambiguous_returns_error(self) -> None:
        """Ambiguous match (>1 occurrence) returns error tuple."""
        content = _FM + "word\nword\n"
        ok, msg = _match_and_replace(content, "word", "replacement")
        assert not ok
        assert "ambiguous" in msg.lower()

    def test_not_found_returns_diagnostic(self) -> None:
        """Total miss returns diagnostic."""
        content = _FM + "# Title\n\nHello world\n"
        ok, msg = _match_and_replace(
            content,
            "completely different text",
            "replacement",
        )
        assert not ok
        assert "not found" in msg.lower()

    def test_no_frontmatter_file(self) -> None:
        """Files without frontmatter still work (pass 1 or pass 3)."""
        content = "# Plain file\n\nHello world\n"
        ok, new_content = _match_and_replace(
            content,
            "Hello world",
            "Goodbye world",
        )
        assert ok
        assert "Goodbye world" in new_content

    def test_similarity_hint_on_close_miss(self) -> None:
        """When find text is close but not exact, error includes similarity %."""
        content = _FM + "Hello world\n"
        ok, msg = _match_and_replace(content, "Hello worlds", "replacement")
        assert not ok
        assert "%" in msg

    def test_frontmatter_preserved_after_body_replace(self) -> None:
        """Frontmatter is byte-identical after body replacement."""
        content = _FM + "# Title\n\nOld content\n"
        ok, new_content = _match_and_replace(
            content,
            "Old content",
            "New content",
        )
        assert ok
        assert new_content.startswith(_FM)

    def test_whitespace_normalized_preserves_frontmatter(self) -> None:
        """Pass 3 replacement preserves frontmatter."""
        content = _FM + "Hello world  \n"
        ok, new_content = _match_and_replace(
            content,
            "Hello world",
            "Goodbye world",
        )
        assert ok
        assert new_content.startswith("---")
        assert "Goodbye world" in new_content


class TestVaultGuard:
    """Tests for _vault_guard — returns error when vault dir missing."""

    def test_returns_empty_when_vault_exists(self, mock_vault: Path) -> None:
        ctx = MagicMock()
        ctx.vault = mock_vault
        assert _vault_guard(ctx) == ""

    def test_returns_error_when_vault_missing(self, tmp_path: Path) -> None:
        ctx = MagicMock()
        ctx.vault = tmp_path / "nonexistent"
        result = _vault_guard(ctx)
        assert "Vault not found" in result
        assert "nonexistent" in result

    def test_error_includes_setup_instructions(self, tmp_path: Path) -> None:
        ctx = MagicMock()
        ctx.vault = tmp_path / "nonexistent"
        result = _vault_guard(ctx)
        assert "VAULT_PATH" in result
        assert "claude mcp add" in result
        assert "gemini mcp add" in result

    def test_returns_error_when_vault_is_file(self, tmp_path: Path) -> None:
        fake = tmp_path / "not-a-dir"
        fake.write_text("oops")
        ctx = MagicMock()
        ctx.vault = fake
        assert "Vault not found" in _vault_guard(ctx)

    def test_error_names_canonical_env_var_and_env_block(self, tmp_path: Path) -> None:
        # #202 Bug 1: the message must name the canonical HIVE_VAULT_PATH and
        # show the generic env-block pattern used by config-file MCP clients
        # (Hermes, Cursor, Windsurf), not just the CLI `add` commands.
        ctx = MagicMock()
        ctx.vault = tmp_path / "nonexistent"
        result = _vault_guard(ctx)
        assert "HIVE_VAULT_PATH" in result
        assert "env:" in result


class TestToolSpan:
    """Tests for tool_span async context manager."""

    @pytest.mark.asyncio
    async def test_completes_within_timeout(self) -> None:
        """tool_span does not raise when body finishes before timeout."""
        async with tool_span("test_tool", 5.0):
            await asyncio.sleep(0.01)

    @pytest.mark.asyncio
    async def test_raises_timeout_error(self) -> None:
        """tool_span raises TimeoutError when body exceeds timeout."""
        with pytest.raises(TimeoutError):
            async with tool_span("test_tool", 0.05):
                await asyncio.sleep(999)

    @pytest.mark.asyncio
    async def test_timeout_error_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """tool_span logs a warning on timeout."""
        with pytest.raises(TimeoutError):
            async with tool_span("slow_tool", 0.05):
                await asyncio.sleep(999)
        assert any("slow_tool" in r.message and "timed out" in r.message for r in caplog.records)


class TestResolveProjectDir:
    """Tests for _resolve_project_dir with hierarchical scopes."""

    def test_flat_scope_resolves(self, mock_vault: Path) -> None:
        """Standard flat scope: 10_projects/testproject resolves."""
        result = _resolve_project_dir(mock_vault, "testproject", _default_scopes())
        assert result is not None
        assert result[0] == mock_vault / "10_projects" / "testproject"
        assert result[1] == "projects"

    def test_explicit_scope_flat(self, mock_vault: Path) -> None:
        """Explicit scope:slug resolves for flat scopes."""
        result = _resolve_project_dir(
            mock_vault,
            "projects:testproject",
            _default_scopes(),
        )
        assert result is not None
        assert result[0] == mock_vault / "10_projects" / "testproject"

    def test_hierarchical_scope_resolves_nested(self, tmp_path: Path) -> None:
        """BFS finds entity nested under a category in a hierarchical scope."""
        (tmp_path / "50_work" / "20-products" / "hydra3d-plus").mkdir(parents=True)
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:hydra3d-plus", scopes)
        assert result is not None
        assert result[0] == tmp_path / "50_work" / "20-products" / "hydra3d-plus"
        assert result[1] == "work"

    def test_hierarchical_bfs_shallowest_wins(self, tmp_path: Path) -> None:
        """When same name exists at multiple depths, shallowest wins (BFS)."""
        (tmp_path / "50_work" / "agents").mkdir(parents=True)
        (tmp_path / "50_work" / "30-clients" / "acme" / "agents").mkdir(parents=True)
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:agents", scopes)
        assert result is not None
        assert result[0] == tmp_path / "50_work" / "agents"

    def test_hierarchical_category_itself_resolves(self, tmp_path: Path) -> None:
        """Category directories (e.g. 12-tickets) are valid targets."""
        (tmp_path / "50_work" / "12-tickets" / "active").mkdir(parents=True)
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:12-tickets", scopes)
        assert result is not None
        assert result[0] == tmp_path / "50_work" / "12-tickets"

    def test_hierarchical_explicit_path_with_slash(self, tmp_path: Path) -> None:
        """Explicit category/entity path resolves directly."""
        (tmp_path / "50_work" / "20-products" / "hydra3d-plus").mkdir(parents=True)
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:20-products/hydra3d-plus", scopes)
        assert result is not None
        assert result[0] == tmp_path / "50_work" / "20-products" / "hydra3d-plus"

    def test_auto_scan_finds_in_hierarchical_scope(self, tmp_path: Path) -> None:
        """Auto-scan (no explicit scope) searches hierarchical scopes too."""
        (tmp_path / "10_projects").mkdir()
        (tmp_path / "50_work" / "20-products" / "hydra3d-plus").mkdir(parents=True)
        scopes = {"projects": "10_projects", "work": "50_work"}
        result = _resolve_project_dir(tmp_path, "hydra3d-plus", scopes)
        assert result is not None
        assert result[0] == tmp_path / "50_work" / "20-products" / "hydra3d-plus"
        assert result[1] == "work"

    def test_not_found_returns_none(self, tmp_path: Path) -> None:
        """Non-existent slug returns None."""
        (tmp_path / "50_work").mkdir()
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:nonexistent", scopes)
        assert result is None

    def test_boundary_escape_blocked(self, tmp_path: Path) -> None:
        """Path traversal in slug is blocked."""
        (tmp_path / "50_work").mkdir()
        scopes = {"work": "50_work"}
        result = _resolve_project_dir(tmp_path, "work:../../etc", scopes)
        assert result is None

    # ── agents scope (HIVE-120) ──

    def test_agents_scope_explicit_resolves(self, tmp_path: Path) -> None:
        """agents:Hermes-NaN resolves like any other flat scope."""
        (tmp_path / "80_agents" / "Hermes-NaN").mkdir(parents=True)
        scopes = {"agents": "80_agents"}
        result = _resolve_project_dir(tmp_path, "agents:Hermes-NaN", scopes)
        assert result is not None
        assert result[0] == tmp_path / "80_agents" / "Hermes-NaN"
        assert result[1] == "agents"

    def test_agents_scope_auto_scan_resolves(self, tmp_path: Path) -> None:
        """A plain agent name auto-scans into the agents scope."""
        (tmp_path / "10_projects").mkdir()
        (tmp_path / "80_agents" / "Hermes-NaN").mkdir(parents=True)
        scopes = {"projects": "10_projects", "agents": "80_agents"}
        result = _resolve_project_dir(tmp_path, "Hermes-NaN", scopes)
        assert result is not None
        assert result[0] == tmp_path / "80_agents" / "Hermes-NaN"
        assert result[1] == "agents"

    def test_agents_in_default_scopes(self, tmp_path: Path) -> None:
        """``_default_scopes()`` reads settings.vault_scopes (the SSOT) — which
        includes the agents scope — lazily, not from a stale literal. Callers
        pass it explicitly now that the resolver requires a scopes mapping."""
        assert "agents" in _default_scopes()
        (tmp_path / "80_agents" / "Hermes-NaN").mkdir(parents=True)
        result = _resolve_project_dir(
            tmp_path,
            "agents:Hermes-NaN",
            _default_scopes(),
        )
        assert result is not None
        assert result[0] == tmp_path / "80_agents" / "Hermes-NaN"
        assert result[1] == "agents"

    def test_scopes_argument_is_required(self, tmp_path: Path) -> None:
        """The scopes mapping is a required argument — there is no internal
        default fallback to drift untested (#159 item 1). The single default
        lives at the create_server() boundary."""
        with pytest.raises(TypeError):
            _resolve_project_dir(tmp_path, "anything")  # type: ignore[call-arg]

    def test_agents_appended_last_does_not_shadow(self, tmp_path: Path) -> None:
        """A name present in BOTH projects and agents resolves to projects:
        agents is appended last, so first-match auto-scan keeps prior
        behaviour (req #6, no regression)."""
        (tmp_path / "10_projects" / "shared").mkdir(parents=True)
        (tmp_path / "80_agents" / "shared").mkdir(parents=True)
        result = _resolve_project_dir(tmp_path, "shared", _default_scopes())
        assert result is not None
        assert result[1] == "projects"

    def test_agents_name_is_arbitrary(self, tmp_path: Path) -> None:
        """Agent names carry no format constraint — any valid dir name (mixed
        case, digits, hyphens, underscores, dots) resolves like any project."""
        scopes = {"agents": "80_agents"}
        for name in ("weather-bot", "local_runner", "Athena.v2", "GPT5x"):
            (tmp_path / "80_agents" / name).mkdir(parents=True)
            result = _resolve_project_dir(tmp_path, f"agents:{name}", scopes)
            assert result is not None, name
            assert result[0] == tmp_path / "80_agents" / name
            assert result[1] == "agents"

    def test_meta_unchanged(self, mock_vault: Path) -> None:
        """_meta still resolves to 00_meta scope root."""
        result = _resolve_project_dir(mock_vault, "_meta", _default_scopes())
        assert result is not None
        assert result[0] == mock_vault / "00_meta"
        assert result[1] == "meta"


class TestStripCode:
    """Tests for _strip_code — relocated from _vault_health (HIVE-97)."""

    def test_removes_fenced_block(self) -> None:
        text = "before\n```\ninside\n```\nafter\n"
        result = _strip_code(text)
        assert "inside" not in result
        assert "before" in result
        assert "after" in result

    def test_removes_tilde_fenced_block(self) -> None:
        text = "before\n~~~\ninside\n~~~\nafter\n"
        result = _strip_code(text)
        assert "inside" not in result

    def test_removes_inline_code(self) -> None:
        text = "use `[[wikilink]]` for links"
        result = _strip_code(text)
        assert "[[wikilink]]" not in result
        assert "use" in result

    def test_leaves_plain_markdown_intact(self) -> None:
        text = "# Title\n\n[[real link]] outside any code\n"
        result = _strip_code(text)
        assert "[[real link]]" in result


class TestFindLessonHeading:
    """Tests for find_lesson_heading — walk-back from a line_no to the
    nearest `### [YYYY-MM-DD] …` heading, code-block-aware (HIVE-97)."""

    def test_returns_heading_when_match_above(self) -> None:
        content = "### [2026-05-18] foo\n\nbody line A\nbody line B\n"
        # line 3 = "body line A"
        assert find_lesson_heading(content, 3) == "[2026-05-18] foo"

    def test_returns_heading_when_line_is_the_heading_itself(self) -> None:
        content = "### [2026-05-18] foo\nbody\n"
        assert find_lesson_heading(content, 1) == "[2026-05-18] foo"

    def test_returns_none_when_no_heading_above(self) -> None:
        content = "just some prose\nwith no heading\n"
        assert find_lesson_heading(content, 2) is None

    def test_clamps_when_line_no_past_eof(self) -> None:
        """line_no past EOF clamps to last line — degrade gracefully so
        callers with off-by-one match numbers still get the closest
        valid heading instead of dropping the increment."""
        content = "### [2026-05-18] foo\nbody\n"
        assert find_lesson_heading(content, 99) == "[2026-05-18] foo"

    def test_returns_none_past_eof_when_no_headings(self) -> None:
        """Clamping must NOT invent a heading where none exists."""
        content = "just prose\nno heading\n"
        assert find_lesson_heading(content, 99) is None

    def test_skips_heading_inside_fenced_codeblock(self) -> None:
        """Heading inside ``` ... ``` must NOT be returned."""
        content = (
            "intro\n"
            "```\n"  # line 2  fence open
            "### [2026-01-01] fake\n"  # line 3  fake heading
            "```\n"  # line 4  fence close
            "body\n"  # line 5  match line
        )
        assert find_lesson_heading(content, 5) is None

    def test_real_heading_wins_when_fake_heading_inside_codeblock(self) -> None:
        content = (
            "### [2026-05-18] real heading\n"  # line 1
            "intro\n"  # line 2
            "```\n"  # line 3
            "### [2026-01-01] fake\n"  # line 4
            "```\n"  # line 5
            "body\n"  # line 6  match line
        )
        assert find_lesson_heading(content, 6) == "[2026-05-18] real heading"

    def test_returns_most_recent_heading_when_multiple_above(self) -> None:
        content = (
            "### [2026-01-01] older\n"
            "body A\n"
            "### [2026-05-18] newer\n"
            "body B\n"
            "body C\n"  # line 5
        )
        assert find_lesson_heading(content, 5) == "[2026-05-18] newer"

    def test_ignores_malformed_heading_without_date(self) -> None:
        content = (
            "### no date here\n"  # line 1  malformed heading
            "body line\n"  # line 2
        )
        assert find_lesson_heading(content, 2) is None

    def test_ignores_h1_h2_h4_only_matches_h3(self) -> None:
        """Only `### ` (h3) headings count — h1/h2/h4 are not lessons."""
        content = (
            "# [2026-05-18] h1 fake\n"
            "## [2026-05-18] h2 fake\n"
            "#### [2026-05-18] h4 fake\n"
            "body line\n"
        )
        assert find_lesson_heading(content, 4) is None

    def test_tilde_fence_also_skipped(self) -> None:
        content = (
            "~~~\n"
            "### [2026-01-01] fake\n"
            "~~~\n"
            "body\n"  # line 4
        )
        assert find_lesson_heading(content, 4) is None


class TestGitCommitCoalesce:
    """Tests for the multi-path coalescer in _git_commit (HIVE-104 Fase A).

    The coalescer changes the signature from ``rel_path: Path`` to
    ``rel_paths: list[Path]`` so that a multi-write tool (vault_patch with
    N patches, capture_lesson batch with N lessons) can issue exactly one
    ``git add`` + one ``git commit`` instead of N each. Per-call cost drops
    from ~150ms*N to ~150ms total.
    """

    def test_coalesces_multi_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_git_commit(vault, [p1, p2, p3], msg)`` → 1 add + 1 commit.

        Post HIVE-115 PR-3: writes go through ``_run_git`` (Popen +
        registry), not ``subprocess.run``. Test monkeypatches the helper
        directly so the behaviour assertion ("one add, one commit") is
        decoupled from the underlying process API.
        """
        from pathlib import Path as _Path

        calls: list[list[str]] = []

        def fake_run_git(
            args: list[str],
            _vault: Path,
            *,
            registry: object = None,  # noqa: ARG001
        ) -> tuple[int, str, str]:
            calls.append(["git", *args])
            return 0, "", ""

        monkeypatch.setattr("hive._helpers._run_git", fake_run_git)

        _git_commit(
            tmp_path,
            [_Path("a.md"), _Path("b.md"), _Path("c.md")],
            "vault: batch update",
        )

        add_calls = [c for c in calls if c[:2] == ["git", "add"]]
        commit_calls = [c for c in calls if c[:2] == ["git", "commit"]]
        assert len(add_calls) == 1, f"expected 1 add, got {len(add_calls)}: {calls}"
        assert len(commit_calls) == 1, f"expected 1 commit, got {len(commit_calls)}: {calls}"
        assert add_calls[0] == ["git", "add", "a.md", "b.md", "c.md"]

    def test_noop_on_empty_paths(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty path list must skip the helper entirely (no git invocation)."""
        calls: list[list[str]] = []

        def fake_run_git(
            args: list[str],
            _vault: Path,
            *,
            registry: object = None,  # noqa: ARG001
        ) -> tuple[int, str, str]:
            calls.append(["git", *args])
            return 0, "", ""

        monkeypatch.setattr("hive._helpers._run_git", fake_run_git)

        _git_commit(tmp_path, [], "vault: noop")

        assert calls == [], f"expected no _run_git calls, got: {calls}"
