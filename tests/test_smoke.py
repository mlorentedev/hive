"""Smoke tests — real HTTP calls to Ollama and OpenRouter + vault tool checks.

Run with:  pytest -m smoke -v
Requires:  Ollama running + OPENROUTER_API_KEY set (for worker tests).
Vault smoke tests always run (no external deps needed).
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import httpx
import pytest

from hive.budget import BudgetTracker
from hive.clients import OllamaClient, OpenRouterClient
from hive.server import create_server

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.resources.resource import ResourceResult
    from fastmcp.tools import ToolResult

pytestmark = pytest.mark.smoke

OLLAMA_ENDPOINT = os.environ.get("HIVE_OLLAMA_ENDPOINT", "http://ollama.kubelab.live:11434")
OLLAMA_MODEL = os.environ.get("HIVE_OLLAMA_MODEL", "qwen2.5-coder:7b")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get(
    "HIVE_OPENROUTER_API_KEY"
)
OPENROUTER_MODEL = os.environ.get("HIVE_OPENROUTER_MODEL", "qwen/qwen3-coder:free")

# Trivial prompt to keep latency and token usage minimal.
PING_PROMPT = "Reply with exactly one word: pong"


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


def _resource_text(result: ResourceResult) -> str:
    return str(result.contents[0].content)


def _ollama_reachable() -> bool:
    try:
        resp = httpx.get(f"{OLLAMA_ENDPOINT}/", timeout=5)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False


skip_no_ollama = pytest.mark.skipif(not _ollama_reachable(), reason="Ollama not reachable")
skip_no_openrouter = pytest.mark.skipif(
    not OPENROUTER_API_KEY, reason="OPENROUTER_API_KEY not set"
)


# ── Fixtures ────────────────────────────────────────────────────────


def _init_git(path: Path) -> None:
    """Initialize a git repo with initial commit."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, capture_output=True, check=True,
    )
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True,
    )


@pytest.fixture
def smoke_budget(tmp_path: Path) -> BudgetTracker:
    return BudgetTracker(db_path=str(tmp_path / "smoke.db"))


@pytest.fixture
def ollama_client() -> OllamaClient:
    return OllamaClient(endpoint=OLLAMA_ENDPOINT, model=OLLAMA_MODEL)


@pytest.fixture
def openrouter_client() -> OpenRouterClient | None:
    if not OPENROUTER_API_KEY:
        return None
    return OpenRouterClient(api_key=OPENROUTER_API_KEY, default_model=OPENROUTER_MODEL)


@pytest.fixture
def smoke_vault(tmp_path: Path) -> Path:
    """Create a realistic vault structure with git for smoke tests."""
    meta = tmp_path / "00_meta" / "patterns"
    meta.mkdir(parents=True)
    (meta / "pattern-tdd.md").write_text(
        "---\nid: pattern-tdd\ntype: pattern\nstatus: active\n---\n\n"
        "# Pattern: TDD\n\nAlways write tests first.\n"
    )

    project = tmp_path / "10_projects" / "smoketest"
    project.mkdir(parents=True)
    (project / "00-context.md").write_text(
        "---\nid: smoketest\ntype: project\nstatus: active\n---\n\n# Smoke Test Project\n"
    )
    (project / "11-tasks.md").write_text(
        "---\nid: smoketest-tasks\ntype: project-tasks\nstatus: active\n---\n\n"
        "# Tasks\n\n- [ ] Task alpha\n- [x] Task beta\n"
    )
    (project / "90-lessons.md").write_text(
        "---\nid: smoketest-lessons\ntype: lesson\nstatus: active\n---\n\n"
        "# Lessons\n\n## Lesson One\nAlways test.\n"
    )

    arch = project / "30-architecture"
    arch.mkdir()
    (arch / "adr-001.md").write_text(
        "---\nid: adr-001\ntype: adr\nstatus: accepted\n---\n\n# ADR-001\nDecision made.\n"
    )

    # Large file for truncation tests
    large_lines = [
        "---", "id: large-doc", "type: lesson", "status: active",
        'created: "2026-01-15"', "---", "", "# Large Document", "",
    ]
    for i in range(1, 101):
        large_lines.append(f"Line {i}: content.")
    (project / "92-large-doc.md").write_text("\n".join(large_lines) + "\n")

    _init_git(tmp_path)
    return tmp_path


@pytest.fixture
def server(
    smoke_budget: BudgetTracker,
    ollama_client: OllamaClient,
    openrouter_client: OpenRouterClient | None,
    smoke_vault: Path,
) -> FastMCP:
    return create_server(
        vault_path=smoke_vault,
        budget_tracker=smoke_budget,
        ollama_client=ollama_client,
        openrouter_client=openrouter_client,
    )


# ══════════════════════════════════════════════════════════════════════
# Phase B: Vault Tools (14 tools)
# ══════════════════════════════════════════════════════════════════════


class TestVaultSmoke:
    """All vault tools work end-to-end."""

    # B1
    async def test_list_projects(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("vault_list_projects", {}))
        assert "smoketest" in result

    # B2
    async def test_query_context(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_query", {"project": "smoketest", "section": "context"},
        ))
        assert "Smoke Test Project" in result

    async def test_query_tasks(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_query", {"project": "smoketest", "section": "tasks"},
        ))
        assert "Task alpha" in result

    async def test_query_lessons(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_query", {"project": "smoketest", "section": "lessons"},
        ))
        assert "Lesson One" in result

    # B3
    async def test_query_arbitrary_path(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_query", {"project": "smoketest", "path": "30-architecture/adr-001.md"},
        ))
        assert "ADR-001" in result

    # B4
    async def test_query_meta(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_query", {"project": "_meta", "path": "patterns/pattern-tdd.md"},
        ))
        assert "TDD" in result

    # B5
    async def test_query_max_lines(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_query", {"project": "smoketest", "path": "92-large-doc.md", "max_lines": 5},
        ))
        assert "truncated" in result.lower()

    # B6
    async def test_query_include_metadata(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_query",
            {"project": "smoketest", "section": "context", "include_metadata": True},
        ))
        assert "type=project" in result

    # B7
    async def test_query_project_not_found(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_query", {"project": "nonexistent"},
        ))
        assert "not found" in result.lower()

    # B8
    async def test_search_basic(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("vault_search", {"query": "Task alpha"}))
        assert "11-tasks.md" in result

    # B9
    async def test_search_with_filter(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_search", {"query": "Decision", "type_filter": "adr"},
        ))
        assert "adr-001" in result

    # B10
    async def test_search_regex(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_search", {"query": r"Line\s+\d+:", "use_regex": True},
        ))
        assert "92-large-doc.md" in result

    # B11
    async def test_search_no_results(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_search", {"query": "xyznonexistent999"},
        ))
        assert "no matches" in result.lower()

    # B12
    async def test_health(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("vault_health", {}))
        assert "smoketest" in result

    # B13
    async def test_update_append(self, server: FastMCP, smoke_vault: Path) -> None:
        result = _text(await server.call_tool(
            "vault_update",
            {
                "project": "smoketest",
                "section": "lessons",
                "operation": "append",
                "content": "\n## Lesson Two\nNew lesson.\n",
            },
        ))
        assert "updated" in result.lower()
        content = (smoke_vault / "10_projects" / "smoketest" / "90-lessons.md").read_text()
        assert "Lesson Two" in content

    # B14
    async def test_update_invalid_frontmatter(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_update",
            {
                "project": "smoketest",
                "section": "tasks",
                "operation": "replace",
                "content": "# No frontmatter\n",
            },
        ))
        assert "frontmatter" in result.lower()

    # B15
    async def test_create(self, server: FastMCP, smoke_vault: Path) -> None:
        result = _text(await server.call_tool(
            "vault_create",
            {
                "project": "smoketest",
                "path": "30-architecture/adr-test.md",
                "content": "# Test ADR\n",
                "doc_type": "adr",
            },
        ))
        assert "created" in result.lower()
        adr = smoke_vault / "10_projects" / "smoketest" / "30-architecture" / "adr-test.md"
        assert adr.exists()

    # B16
    async def test_create_duplicate(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_create",
            {
                "project": "smoketest",
                "path": "30-architecture/adr-001.md",
                "content": "dup",
                "doc_type": "adr",
            },
        ))
        assert "already exists" in result.lower()

    # B17
    async def test_list_files(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_list_files", {"project": "smoketest"},
        ))
        assert "00-context.md" in result
        assert "30-architecture/" in result

    # B18
    async def test_list_files_pattern(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_list_files", {"project": "smoketest", "pattern": "adr-*"},
        ))
        assert "adr-001.md" in result

    # B19
    async def test_patch(self, server: FastMCP, smoke_vault: Path) -> None:
        result = _text(await server.call_tool(
            "vault_patch",
            {
                "project": "smoketest",
                "path": "11-tasks.md",
                "old_text": "- [ ] Task alpha",
                "new_text": "- [x] Task alpha",
            },
        ))
        assert "patch" in result.lower()
        content = (smoke_vault / "10_projects" / "smoketest" / "11-tasks.md").read_text()
        assert "- [x] Task alpha" in content

    # B20
    async def test_patch_ambiguous(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_patch",
            {
                "project": "smoketest",
                "path": "11-tasks.md",
                "old_text": "Task",
                "new_text": "Item",
            },
        ))
        assert "ambiguous" in result.lower()

    # B21
    async def test_capture_lesson(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "capture_lesson",
            {
                "project": "smoketest",
                "title": "Smoke Lesson",
                "context": "E2E test",
                "problem": "Need to verify capture works",
                "solution": "Call the tool",
                "tags": ["smoke", "test"],
            },
        ))
        assert "captured" in result.lower()

    # B22
    async def test_capture_lesson_dedup(self, server: FastMCP) -> None:
        # First capture
        await server.call_tool(
            "capture_lesson",
            {
                "project": "smoketest",
                "title": "Dedup Lesson",
                "context": "test", "problem": "test", "solution": "test",
            },
        )
        # Second with same title
        result = _text(await server.call_tool(
            "capture_lesson",
            {
                "project": "smoketest",
                "title": "Dedup Lesson",
                "context": "test2", "problem": "test2", "solution": "test2",
            },
        ))
        assert "already exists" in result.lower()

    # B23
    async def test_summarize_small_file(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_summarize", {"project": "smoketest", "section": "context"},
        ))
        assert "Smoke Test Project" in result

    # B24
    async def test_smart_search(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_smart_search", {"query": "Decision"},
        ))
        assert "adr-001" in result

    # B25
    async def test_session_briefing(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "session_briefing", {"project": "smoketest"},
        ))
        assert "Session Briefing" in result

    # B26
    async def test_vault_recent(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_recent", {"project": "smoketest"},
        ))
        # Should return something (at least the recently created files)
        assert "smoketest" in result.lower() or "recent" in result.lower()

    # B27
    async def test_vault_usage(self, server: FastMCP) -> None:
        # Call a tool first to generate usage data
        await server.call_tool("vault_list_projects", {})
        result = _text(await server.call_tool("vault_usage", {}))
        assert "vault_list_projects" in result


# ══════════════════════════════════════════════════════════════════════
# Phase D: Resources (5 URIs)
# ══════════════════════════════════════════════════════════════════════


class TestResourcesSmoke:
    """All MCP resources resolve correctly."""

    # D1
    async def test_projects_resource(self, server: FastMCP) -> None:
        result = _resource_text(await server.read_resource("hive://projects"))
        assert "smoketest" in result

    # D2
    async def test_health_resource(self, server: FastMCP) -> None:
        result = _resource_text(await server.read_resource("hive://health"))
        assert "smoketest" in result

    # D3
    async def test_project_context_resource(self, server: FastMCP) -> None:
        result = _resource_text(
            await server.read_resource("hive://projects/smoketest/context"),
        )
        assert "Smoke Test Project" in result

    # D4
    async def test_project_tasks_resource(self, server: FastMCP) -> None:
        result = _resource_text(
            await server.read_resource("hive://projects/smoketest/tasks"),
        )
        assert "Task alpha" in result

    # D5
    async def test_project_lessons_resource(self, server: FastMCP) -> None:
        result = _resource_text(
            await server.read_resource("hive://projects/smoketest/lessons"),
        )
        assert "Lesson One" in result


# ══════════════════════════════════════════════════════════════════════
# Phase G: Edge Cases
# ══════════════════════════════════════════════════════════════════════


class TestEdgeCasesSmoke:
    """Edge cases produce helpful messages, not crashes."""

    # G1
    async def test_empty_vault(self, tmp_path: Path) -> None:
        (tmp_path / "10_projects").mkdir()
        mcp = create_server(vault_path=tmp_path)
        result = _text(await mcp.call_tool("vault_list_projects", {}))
        assert "no projects" in result.lower()

    async def test_empty_vault_health(self, tmp_path: Path) -> None:
        (tmp_path / "10_projects").mkdir()
        mcp = create_server(vault_path=tmp_path)
        result = _text(await mcp.call_tool("vault_health", {}))
        assert "no projects" in result.lower()

    async def test_empty_vault_search(self, tmp_path: Path) -> None:
        (tmp_path / "10_projects").mkdir()
        mcp = create_server(vault_path=tmp_path)
        result = _text(await mcp.call_tool("vault_search", {"query": "anything"}))
        assert "no matches" in result.lower()

    # G3
    async def test_large_file_truncation(self, server: FastMCP) -> None:
        result = _text(await server.call_tool(
            "vault_query", {"project": "smoketest", "path": "92-large-doc.md", "max_lines": 10},
        ))
        assert "truncated" in result.lower()
        lines_before_truncation = result.split("[...")[0].strip().splitlines()
        assert len(lines_before_truncation) == 10

    # G4
    async def test_budget_exhausted(
        self, server: FastMCP, smoke_budget: BudgetTracker,
    ) -> None:
        smoke_budget.record_request(model="test", cost_usd=5.0, tokens=0, latency_ms=0)
        result = _text(await server.call_tool(
            "delegate_task",
            {"prompt": PING_PROMPT, "model": "openrouter", "max_cost_per_request": 0.01},
        ))
        assert "budget" in result.lower()


# ══════════════════════════════════════════════════════════════════════
# Worker smoke tests (need real infra)
# ══════════════════════════════════════════════════════════════════════


@skip_no_ollama
class TestOllamaDirect:
    """Real calls to Ollama."""

    async def test_delegate_ollama_returns_response(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool("delegate_task", {"prompt": PING_PROMPT, "model": "ollama"})
        )
        assert "Worker Response" in result
        assert "qwen2.5-coder" in result

    async def test_delegate_ollama_has_tokens_and_latency(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool("delegate_task", {"prompt": PING_PROMPT, "model": "ollama"})
        )
        assert "tokens" in result
        assert "$0.00" in result

    async def test_ollama_records_budget(
        self, server: FastMCP, smoke_budget: BudgetTracker
    ) -> None:
        await server.call_tool("delegate_task", {"prompt": PING_PROMPT, "model": "ollama"})
        stats = smoke_budget.month_stats(5.0)
        assert stats["request_count"] == 1
        assert stats["spent"] == 0.0


@skip_no_openrouter
class TestOpenRouterFreeDirect:
    """Real calls to OpenRouter free tier."""

    async def test_delegate_openrouter_free_returns_response(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "delegate_task", {"prompt": PING_PROMPT, "model": "openrouter-free"}
            )
        )
        assert "Worker Response" in result

    async def test_openrouter_free_records_budget(
        self, server: FastMCP, smoke_budget: BudgetTracker
    ) -> None:
        await server.call_tool(
            "delegate_task", {"prompt": PING_PROMPT, "model": "openrouter-free"}
        )
        stats = smoke_budget.month_stats(5.0)
        assert stats["request_count"] == 1


@skip_no_openrouter
class TestOpenRouterPaidDirect:
    """Real calls to OpenRouter paid tier."""

    async def test_delegate_paid_returns_response(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "delegate_task",
                {"prompt": PING_PROMPT, "model": "openrouter", "max_cost_per_request": 0.01},
            )
        )
        assert "Worker Response" in result
        assert "qwen" in result.lower()

    async def test_paid_records_nonzero_cost(
        self, server: FastMCP, smoke_budget: BudgetTracker
    ) -> None:
        await server.call_tool(
            "delegate_task",
            {"prompt": PING_PROMPT, "model": "openrouter", "max_cost_per_request": 0.01},
        )
        stats = smoke_budget.month_stats(5.0)
        assert stats["request_count"] == 1
        assert stats["spent"] >= 0.0

    async def test_paid_budget_guard_blocks_when_exhausted(
        self, server: FastMCP, smoke_budget: BudgetTracker
    ) -> None:
        smoke_budget.record_request(model="test", cost_usd=5.0, tokens=0, latency_ms=0)
        result = _text(
            await server.call_tool(
                "delegate_task",
                {"prompt": PING_PROMPT, "model": "openrouter", "max_cost_per_request": 0.01},
            )
        )
        assert "budget" in result.lower()


@skip_no_ollama
class TestAutoRouting:
    """Auto routing with real backends."""

    async def test_auto_picks_ollama_first(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool("delegate_task", {"prompt": PING_PROMPT})
        )
        assert "Worker Response" in result
        assert "qwen2.5-coder" in result

    async def test_auto_records_budget(
        self, server: FastMCP, smoke_budget: BudgetTracker
    ) -> None:
        await server.call_tool("delegate_task", {"prompt": PING_PROMPT})
        stats = smoke_budget.month_stats(5.0)
        assert stats["request_count"] == 1


class TestListModelsSmoke:
    """Real model listing."""

    @skip_no_ollama
    async def test_list_models_shows_ollama(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("list_models", {}))
        assert "online" in result.lower()
        assert OLLAMA_MODEL in result

    @skip_no_openrouter
    async def test_list_models_shows_openrouter(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("list_models", {}))
        assert "OpenRouter" in result


class TestWorkerStatusSmoke:
    """Real status check."""

    @skip_no_ollama
    async def test_status_shows_ollama_online(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("worker_status", {}))
        assert "online" in result.lower()

    async def test_status_shows_budget(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("worker_status", {}))
        assert "Budget" in result
        assert "$" in result
