"""Shared test fixtures for Hive test suite."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from hive.budget import BudgetTracker
from hive.clients import OllamaClient, OpenRouterClient

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


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
def multi_scope_vault(mock_vault: Path) -> Path:
    """Extend mock_vault with a 50_work scope containing hierarchical structure."""
    # Direct child (flat entity, like 10_projects)
    company = mock_vault / "50_work" / "my-company"
    company.mkdir(parents=True)
    (company / "00-context.md").write_text(
        "---\nid: my-company\ntype: project\nstatus: active\n---\n\n"
        "# My Company\n\nProfessional project.\n"
    )
    (company / "11-tasks.md").write_text(
        "---\nid: my-company-tasks\ntype: project-tasks\nstatus: active\n---\n\n"
        "# My Company: Tasks\n\n- [ ] Ship feature\n"
    )
    (company / "90-lessons.md").write_text(
        "---\nid: my-company-lessons\ntype: lesson\nstatus: active\n---\n\n"
        "# My Company: Lessons\n\n## Entry 1\nDeploy on Fridays.\n"
    )

    # Nested entity under a category (hierarchical)
    hydra = mock_vault / "50_work" / "20-products" / "hydra3d-plus"
    hydra.mkdir(parents=True)
    (hydra / "00-context.md").write_text(
        "---\nid: hydra3d-plus\ntype: product\nstatus: active\n---\n\n"
        "# Hydra3D Plus\n\n3D camera product.\n"
    )

    # Another nested entity (different category)
    client = mock_vault / "50_work" / "30-clients" / "appliedmaterials"
    client.mkdir(parents=True)
    (client / "00-context.md").write_text(
        "---\nid: appliedmaterials\ntype: client\nstatus: active\n"
        "tags: [hydra3d-plus]\n---\n\n"
        "# Applied Materials\n\nKey client using Hydra3D Plus.\n"
    )

    return mock_vault


@pytest.fixture
def git_multi_scope_vault(multi_scope_vault: Path) -> Path:
    """Multi-scope vault that is also a git repo (for write operations)."""
    subprocess.run(["git", "init"], cwd=multi_scope_vault, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=multi_scope_vault,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=multi_scope_vault,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=multi_scope_vault, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=multi_scope_vault,
        capture_output=True,
        check=True,
    )
    return multi_scope_vault


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


@pytest.fixture
def budget() -> Generator[BudgetTracker, None, None]:
    """In-memory budget tracker for worker tests."""
    bt = BudgetTracker(db_path=":memory:")
    yield bt
    bt.close()


@pytest.fixture
def ollama() -> OllamaClient:
    """Ollama client for worker tests (methods are mocked per-test)."""
    return OllamaClient(endpoint="http://localhost:11434", model="qwen2.5-coder:7b")


@pytest.fixture
def openrouter() -> OpenRouterClient:
    """OpenRouter client for worker tests (methods are mocked per-test)."""
    return OpenRouterClient(api_key="sk-test", default_model="qwen/qwen3-coder:free")


@pytest.fixture(autouse=True)
def _reset_ghost_response_counter() -> Generator[None, None, None]:
    """Reset the module-level ghost-response counter around every test.

    Prevents leakage of the singleton state (``hive._compat.GHOST_RESPONSES``)
    across tests — a test that records a suppression must not affect a later
    test that asserts the absence of the ``ghost_responses`` block.
    """
    from hive import _compat as _hc

    _hc.GHOST_RESPONSES.reset()
    yield
    _hc.GHOST_RESPONSES.reset()
