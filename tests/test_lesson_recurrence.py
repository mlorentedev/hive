"""Unit and integration tests for lesson recurrence detection (Standing Order #1 guard)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hive._helpers import (
    check_lesson_recurrence,
    parse_existing_lessons,
)
from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path


def _text(result: object) -> str:
    """Extract string content from tool call result."""
    if isinstance(result, str):
        return result
    if hasattr(result, "content") and result.content:  # type: ignore[union-attr]
        item = result.content[0]  # type: ignore[union-attr]
        if hasattr(item, "text"):
            return str(item.text)
    return str(result)


class TestParseExistingLessons:
    """Tests for parse_existing_lessons helper."""

    def test_parse_standard_vault_lessons(self) -> None:
        content = """---
id: test-lessons
type: lesson
status: active
---

# Lessons Learned

### [2026-03-01] Validate YAML frontmatter
**Context:** Writing tests for vault_write.
**Problem:** Replace operation accepted invalid YAML frontmatter without check.
**Solution:** Added frontmatter schema validation before writing.
**Tags:** `#testing` `#yaml`

### [2026-03-02] Increase subprocess timeout on Windows
**Context:** Running git commands on slow CI machines.
**Problem:** Subprocess timed out after 10s.
**Solution:** Raised timeout to 30s.
"""
        lessons = parse_existing_lessons(content)
        assert len(lessons) == 2
        assert lessons[0]["title"] == "Validate YAML frontmatter"
        assert lessons[0]["heading"] == "[2026-03-01] Validate YAML frontmatter"
        assert "Replace operation accepted invalid YAML" in lessons[0]["problem"]
        assert "Added frontmatter schema validation" in lessons[0]["solution"]
        assert "Writing tests" in lessons[0]["context"]

        assert lessons[1]["title"] == "Increase subprocess timeout on Windows"
        assert "Subprocess timed out after 10s" in lessons[1]["problem"]

    def test_parse_docs_lessons_format(self) -> None:
        content = """# Hive: Lessons Learned

## Architecture Decisions

### 2026-03-01: Python over Go for MCP servers
- **Context:** Evaluated Go vs Python.
- **Decision:** Python FastMCP SDK.
- **Rationale:** MCP servers are I/O bound.

## Operational Lessons

### [2026-03-05] XML tag leak in input fields
**Context:** Parsing prompt inputs.
**Problem:** Model injected raw XML tags into markdown.
**Solution:** Added XML tag sanitization guard.
"""
        lessons = parse_existing_lessons(content)
        assert len(lessons) == 2
        assert lessons[0]["title"] == "Python over Go for MCP servers"
        assert "FastMCP" in lessons[0]["solution"]
        assert lessons[1]["title"] == "XML tag leak in input fields"
        assert "Model injected raw XML tags" in lessons[1]["problem"]

    def test_ignores_headings_in_code_blocks(self) -> None:
        content = """# Lessons Learned

### [2026-01-01] Real Lesson
**Problem:** Real problem.

```markdown
### [2026-01-02] Fake Lesson in Code Block
**Problem:** Should be ignored.
```
"""
        lessons = parse_existing_lessons(content)
        assert len(lessons) == 1
        assert lessons[0]["title"] == "Real Lesson"


class TestCheckLessonRecurrence:
    """Tests for check_lesson_recurrence similarity matching."""

    _EXISTING_CONTENT = """# Lessons Learned

### [2026-01-10] Always validate frontmatter before write
**Context:** Writing vault_write tests.
**Problem:** Replace operation accepted invalid YAML frontmatter without check.
**Solution:** Added frontmatter validation before write.

### [2026-02-15] Git command timeout on slow Windows CI
**Context:** Running test suite on Windows.
**Problem:** Subprocess git commit timed out after 10 seconds.
**Solution:** Raised timeout to 30s with retry.
"""

    def test_detects_recurrence_by_title_similarity(self) -> None:
        is_rec, heading = check_lesson_recurrence(
            title="Validate frontmatter before write",
            context="Testing replace operations",
            problem="Invalid YAML accepted",
            solution="Check schema first",
            existing_content=self._EXISTING_CONTENT,
        )
        assert is_rec is True
        assert heading is not None
        assert "validate frontmatter" in heading.lower()

    def test_detects_recurrence_by_problem_similarity(self) -> None:
        is_rec, heading = check_lesson_recurrence(
            title="Subprocess execution issue",
            context="Windows CI runners",
            problem="Subprocess git commit timed out after 10 seconds during commit",
            solution="Adjust timeout settings",
            existing_content=self._EXISTING_CONTENT,
        )
        assert is_rec is True
        assert heading is not None
        assert "Git command timeout" in heading

    def test_detects_recurrence_by_keyword_overlap(self) -> None:
        is_rec, heading = check_lesson_recurrence(
            title="YAML frontmatter replace operation error",
            context="vault operations",
            problem="Replace operation accepted invalid YAML frontmatter",
            solution="Sanitize before replace",
            existing_content=self._EXISTING_CONTENT,
        )
        assert is_rec is True
        assert heading is not None
        assert "validate frontmatter" in heading.lower()

    def test_no_recurrence_for_distinct_lesson(self) -> None:
        is_rec, heading = check_lesson_recurrence(
            title="SQLite outbox reconciler thread deadlock",
            context="Handling parallel tool writes",
            problem="Database lock contention during BEGIN IMMEDIATE transaction",
            solution="Add busy timeout and exponential backoff",
            existing_content=self._EXISTING_CONTENT,
        )
        assert is_rec is False
        assert heading is None

    def test_empty_content_returns_false(self) -> None:
        is_rec, heading = check_lesson_recurrence(
            title="Some lesson",
            problem="Some problem",
            existing_content="",
        )
        assert is_rec is False
        assert heading is None


class TestCaptureLessonRecurrenceIntegration:
    """Integration tests for capture_lesson MCP tool with recurrence guard."""

    async def test_capture_lesson_triggers_recurrence_warning(
        self,
        git_vault: Path,
    ) -> None:
        mcp = create_server(vault_path=git_vault)

        # First capture: records initial lesson
        res1 = await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Always validate YAML frontmatter before write",
                "context": "Writing vault_write handler",
                "problem": "Replace operation accepted invalid YAML frontmatter",
                "solution": "Added validation check before writing",
                "tags": ["vault", "yaml"],
            },
        )
        text1 = _text(res1)
        assert "captured" in text1.lower()
        assert "STANDING ORDER #1 WARNING" not in text1

        # Second capture: recurring failure mode
        res2 = await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Validate YAML frontmatter before write",
                "context": "vault_patch tests",
                "problem": "Replace operation accepted invalid YAML frontmatter without check",
                "solution": "Check schema first",
                "tags": ["vault"],
            },
        )
        text2 = _text(res2)
        assert "captured" in text2.lower()
        assert "STANDING ORDER #1 WARNING: RECURRENT LESSON DETECTED" in text2
        assert "Always validate YAML frontmatter before write" in text2
        assert "Automate, don't instruct" in text2

        # Verify entry was written to 90-lessons.md despite warning
        lessons = (git_vault / "10_projects" / "testproject" / "90-lessons.md").read_text(
            encoding="utf-8"
        )
        assert "Validate YAML frontmatter before write" in lessons

    async def test_capture_lesson_no_warning_when_distinct(
        self,
        git_vault: Path,
    ) -> None:
        mcp = create_server(vault_path=git_vault)

        # Capture first lesson
        await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Always validate YAML frontmatter",
                "context": "vault_write handler",
                "problem": "Invalid YAML was accepted",
                "solution": "Added schema validator",
                "tags": ["yaml"],
            },
        )

        # Capture completely unrelated lesson
        res = await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Increase git subprocess timeout on Windows",
                "context": "Running git commits in parallel",
                "problem": "Process timed out after 10 seconds",
                "solution": "Bumped timeout to 30 seconds",
                "tags": ["git", "windows"],
            },
        )
        text = _text(res)
        assert "captured" in text.lower()
        assert "STANDING ORDER #1 WARNING" not in text

    async def test_capture_lesson_recurrence_with_docs_lessons_md(
        self,
        git_vault: Path,
    ) -> None:
        project_dir = git_vault / "10_projects" / "testproject"
        docs_dir = project_dir / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "lessons.md").write_text(
            """# Project Lessons

### [2026-01-01] Subprocess deadlock under high concurrency
**Problem:** Subprocess pipes deadlocked when output buffer exceeded 64KB.
**Solution:** Use communicate() or async streaming.
""",
            encoding="utf-8",
        )

        mcp = create_server(vault_path=git_vault)
        res = await mcp.call_tool(
            "capture_lesson",
            {
                "project": "testproject",
                "title": "Subprocess pipe buffer deadlock under concurrency",
                "context": "Calling git log with large diffs",
                "problem": "Pipes deadlocked when stdout buffer was full",
                "solution": "Stream output asynchronously",
                "tags": ["subprocess"],
            },
        )
        text = _text(res)
        assert "captured" in text.lower()
        assert "STANDING ORDER #1 WARNING: RECURRENT LESSON DETECTED" in text
        assert "Subprocess deadlock under high concurrency" in text
