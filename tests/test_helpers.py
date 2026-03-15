"""Tests for helper functions — _match_and_replace, _vault_guard, tool_span."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from hive._helpers import _match_and_replace, _vault_guard, tool_span

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
            content, "Status: active", "Status: done",
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
            content, find_text, "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |",
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
            content, "completely different text", "replacement",
        )
        assert not ok
        assert "not found" in msg.lower()

    def test_no_frontmatter_file(self) -> None:
        """Files without frontmatter still work (pass 1 or pass 3)."""
        content = "# Plain file\n\nHello world\n"
        ok, new_content = _match_and_replace(
            content, "Hello world", "Goodbye world",
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
            content, "Old content", "New content",
        )
        assert ok
        assert new_content.startswith(_FM)

    def test_whitespace_normalized_preserves_frontmatter(self) -> None:
        """Pass 3 replacement preserves frontmatter."""
        content = _FM + "Hello world  \n"
        ok, new_content = _match_and_replace(
            content, "Hello world", "Goodbye world",
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
