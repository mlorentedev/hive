"""Tests for _match_and_replace cascading logic."""

from __future__ import annotations

from hive._helpers import _match_and_replace

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
        """Pass 2: old_text matches body but is ambiguous in full file."""
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
        old_text = "| A | B |\n|---|---|\n| 1 | 2 |"
        ok, new_content = _match_and_replace(
            content, old_text, "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |",
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
        """When old_text is close but not exact, error includes similarity %."""
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
