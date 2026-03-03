"""Shared test fixtures for Hive test suite."""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def mock_vault(tmp_path: Path) -> Path:
    """Create a realistic vault structure for testing."""
    # ── 00_meta (cross-project knowledge) ──
    patterns = tmp_path / "00_meta" / "patterns"
    patterns.mkdir(parents=True)
    (patterns / "pattern-tdd.md").write_text(
        "---\nid: pattern-tdd\ntype: pattern\nstatus: active\n---\n\n"
        "# Pattern: Test-Driven Development\n\nAlways write tests first.\n"
    )
    (tmp_path / "00_meta" / "templates").mkdir(parents=True)

    # ── 10_projects/testproject ──
    project = tmp_path / "10_projects" / "testproject"
    project.mkdir(parents=True)

    (project / "00-context.md").write_text(
        "---\nid: testproject\ntype: project\nstatus: active\n---\n\n# Test Project\n"
    )
    (project / "11-tasks.md").write_text(
        "---\nid: testproject-tasks\ntype: project-tasks\nstatus: active\n---\n\n"
        "# Test: Active Backlog\n\n- [ ] Task one\n- [x] Task two\n"
    )
    (project / "90-lessons.md").write_text(
        "---\nid: testproject-lessons\ntype: lesson\nstatus: active\n---\n\n"
        "# Test: Lessons\n\n## Entry 1\nSome lesson.\n"
    )

    # Architecture subdirectory
    arch = project / "30-architecture"
    arch.mkdir()
    (arch / "adr-001-test.md").write_text(
        "---\nid: adr-001-test\ntype: adr\nstatus: accepted\n---\n\n"
        "# ADR-001: Test Decision\n\nWe decided to test everything.\n"
    )

    # Troubleshooting file (for filter tests)
    trouble = project / "50-troubleshooting"
    trouble.mkdir()
    (trouble / "timeout-fix.md").write_text(
        "---\nid: timeout-fix\ntype: troubleshooting\nstatus: active\n"
        "tags: [networking, timeout]\n---\n\n# Timeout Fix\n\nIncrease timeout to 30s.\n"
    )

    # Lesson with different tags
    (project / "91-extra-lesson.md").write_text(
        "---\nid: extra-lesson\ntype: lesson\nstatus: completed\n"
        "tags: [python]\n---\n\n# Extra Lesson\n\nPython is great.\n"
    )

    # Large document for summarize threshold testing (90 lines)
    large_lines = [
        "---",
        "id: large-doc",
        "type: lesson",
        "status: active",
        'created: "2026-01-15"',
        "tags: [python, architecture]",
        "---",
        "",
        "# Large Document for Testing",
        "",
    ]
    for i in range(1, 81):
        large_lines.append(f"Line {i}: This is content line number {i} of the large document.")
    (project / "92-large-doc.md").write_text("\n".join(large_lines) + "\n")

    return tmp_path


@pytest.fixture
def git_vault(mock_vault: Path) -> Path:
    """Create a mock vault that is also a git repo (for write operations)."""
    subprocess.run(["git", "init"], cwd=mock_vault, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=mock_vault,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=mock_vault,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=mock_vault, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=mock_vault,
        capture_output=True,
        check=True,
    )
    return mock_vault
