"""Smoke tests — real HTTP calls to the configured worker + vault tool checks.

Run with:  pytest -m smoke -v
Requires:  a reachable OpenAI-compatible worker endpoint (HIVE_WORKER_BASE_URL)
           and its credential (HIVE_WORKER_API_KEY). Which provider serves it is
           a deployment choice; a launcher using its own variable name maps it
           onto HIVE_WORKER_API_KEY at injection time (#391).
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
from hive.clients import OpenAICompatibleClient
from hive.server import create_server

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.resources.resource import ResourceResult
    from fastmcp.tools import ToolResult

pytestmark = pytest.mark.smoke

# HIVE-384: the smoke tests run against the single NaN-backed worker.
# The credential is read under BOTH names the deployment uses — the prefixed one
# hive owns, and the registry name the launcher injects — so a smoke run works
# whether it is started by hand or through `dotf secrets run`.
WORKER_BASE_URL = os.environ.get("HIVE_WORKER_BASE_URL") or os.environ.get(
    "HIVE_EMBED_BASE_URL", ""
)
WORKER_MODEL = os.environ.get("HIVE_WORKER_MODEL", "")
WORKER_API_KEY = os.environ.get("HIVE_WORKER_API_KEY", "")

# Trivial prompt to keep latency and token usage minimal.
PING_PROMPT = "Reply with exactly one word: pong"


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


def _resource_text(result: ResourceResult) -> str:
    return str(result.contents[0].content)


def _worker_reachable() -> bool:
    """Probe the configured worker endpoint.

    Configuration alone is not enough to run these tests: the whole point of
    #384 is that a configured-but-unreachable worker looked healthy for an
    unknown length of time. So the skip condition is a probe, not a truthy
    env var.

    ``WORKER_MODEL`` is required rather than defaulted: hive names no provider,
    so it cannot know which model id any given endpoint serves (#391). A guessed
    default would turn "wrong provider configured" into a confusing inference
    failure instead of a skip.
    """
    if not WORKER_BASE_URL or not WORKER_API_KEY or not WORKER_MODEL:
        return False
    try:
        resp = httpx.get(
            f"{WORKER_BASE_URL}/models",
            headers={"Authorization": f"Bearer {WORKER_API_KEY}"},
            timeout=5,
        )
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False


skip_no_worker = pytest.mark.skipif(
    not _worker_reachable(),
    reason="worker endpoint not configured or not reachable",
)


# ── Fixtures ────────────────────────────────────────────────────────


def _init_git(path: Path) -> None:
    """Initialize a git repo with initial commit."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        capture_output=True,
        check=True,
    )


@pytest.fixture
def smoke_budget(tmp_path: Path) -> BudgetTracker:
    return BudgetTracker(db_path=str(tmp_path / "smoke.db"))


@pytest.fixture
def smoke_worker_client() -> OpenAICompatibleClient | None:
    if not WORKER_BASE_URL or not WORKER_API_KEY:
        return None
    return OpenAICompatibleClient(
        base_url=WORKER_BASE_URL,
        api_key=WORKER_API_KEY,
        default_model=WORKER_MODEL,
    )


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
        "---",
        "id: large-doc",
        "type: lesson",
        "status: active",
        'created: "2026-01-15"',
        "---",
        "",
        "# Large Document",
        "",
    ]
    for i in range(1, 101):
        large_lines.append(f"Line {i}: content.")
    (project / "92-large-doc.md").write_text("\n".join(large_lines) + "\n")

    _init_git(tmp_path)
    return tmp_path


@pytest.fixture
def server(
    smoke_budget: BudgetTracker,
    smoke_worker_client: OpenAICompatibleClient,
    smoke_vault: Path,
) -> FastMCP:
    return create_server(
        vault_path=smoke_vault,
        budget_tracker=smoke_budget,
        worker_client=smoke_worker_client,
    )


# ══════════════════════════════════════════════════════════════════════
# Phase B: Vault Tools (14 tools)
# ══════════════════════════════════════════════════════════════════════


class TestVaultSmoke:
    """All vault tools work end-to-end."""

    # B1
    async def test_list_projects(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("vault_list", {}))
        assert "smoketest" in result

    # B2
    async def test_query_context(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_query",
                {"project": "smoketest", "section": "context"},
            )
        )
        assert "Smoke Test Project" in result

    async def test_query_tasks(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_query",
                {"project": "smoketest", "section": "tasks"},
            )
        )
        assert "Task alpha" in result

    async def test_query_lessons(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_query",
                {"project": "smoketest", "section": "lessons"},
            )
        )
        assert "Lesson One" in result

    # B3
    async def test_query_arbitrary_path(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_query",
                {"project": "smoketest", "path": "30-architecture/adr-001.md"},
            )
        )
        assert "ADR-001" in result

    # B4
    async def test_query_meta(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_query",
                {"project": "_meta", "path": "patterns/pattern-tdd.md"},
            )
        )
        assert "TDD" in result

    # B5
    async def test_query_max_lines(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_query",
                {"project": "smoketest", "path": "92-large-doc.md", "max_lines": 5},
            )
        )
        assert "truncated" in result.lower()

    # B6
    async def test_query_include_metadata(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_query",
                {"project": "smoketest", "section": "context", "include_metadata": True},
            )
        )
        assert "type=project" in result

    # B7
    async def test_query_project_not_found(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_query",
                {"project": "nonexistent"},
            )
        )
        assert "not found" in result.lower()

    # B8
    async def test_search_basic(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("vault_search", {"query": "Task alpha"}))
        assert "11-tasks.md" in result

    # B9
    async def test_search_with_filter(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_search",
                {"query": "Decision", "type_filter": "adr"},
            )
        )
        assert "adr-001" in result

    # B10
    async def test_search_regex(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_search",
                {"query": r"Line\s+\d+:", "use_regex": True},
            )
        )
        assert "92-large-doc.md" in result

    # B11
    async def test_search_no_results(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_search",
                {"query": "xyznonexistent999"},
            )
        )
        assert "no matches" in result.lower()

    # B12
    async def test_health(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("vault_health", {}))
        assert "smoketest" in result

    # B13
    async def test_update_append(self, server: FastMCP, smoke_vault: Path) -> None:
        result = _text(
            await server.call_tool(
                "vault_write",
                {
                    "project": "smoketest",
                    "section": "lessons",
                    "operation": "append",
                    "content": "\n## Lesson Two\nNew lesson.\n",
                },
            )
        )
        assert "updated" in result.lower()
        content = (smoke_vault / "10_projects" / "smoketest" / "90-lessons.md").read_text()
        assert "Lesson Two" in content

    # B14
    async def test_update_invalid_frontmatter(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_write",
                {
                    "project": "smoketest",
                    "section": "tasks",
                    "operation": "replace",
                    "content": "# No frontmatter\n",
                },
            )
        )
        assert "frontmatter" in result.lower()

    # B15
    async def test_create(self, server: FastMCP, smoke_vault: Path) -> None:
        result = _text(
            await server.call_tool(
                "vault_write",
                {
                    "project": "smoketest",
                    "path": "30-architecture/adr-test.md",
                    "content": "# Test ADR\n",
                    "doc_type": "adr",
                    "operation": "create",
                },
            )
        )
        assert "created" in result.lower()
        adr = smoke_vault / "10_projects" / "smoketest" / "30-architecture" / "adr-test.md"
        assert adr.exists()

    # B16
    async def test_create_duplicate(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_write",
                {
                    "project": "smoketest",
                    "path": "30-architecture/adr-001.md",
                    "content": "dup",
                    "doc_type": "adr",
                    "operation": "create",
                },
            )
        )
        assert "already exists" in result.lower()

    # B17
    async def test_list_files(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_list",
                {"project": "smoketest"},
            )
        )
        assert "00-context.md" in result
        assert "30-architecture/" in result

    # B18
    async def test_list_files_pattern(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_list",
                {"project": "smoketest", "pattern": "adr-*"},
            )
        )
        assert "adr-001.md" in result

    # B19
    async def test_patch(self, server: FastMCP, smoke_vault: Path) -> None:
        result = _text(
            await server.call_tool(
                "vault_patch",
                {
                    "project": "smoketest",
                    "path": "11-tasks.md",
                    "find": "- [ ] Task alpha",
                    "replace": "- [x] Task alpha",
                },
            )
        )
        assert "patch" in result.lower()
        content = (smoke_vault / "10_projects" / "smoketest" / "11-tasks.md").read_text()
        assert "- [x] Task alpha" in content

    # B20
    async def test_patch_ambiguous(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_patch",
                {
                    "project": "smoketest",
                    "path": "11-tasks.md",
                    "find": "Task",
                    "replace": "Item",
                },
            )
        )
        assert "ambiguous" in result.lower()

    # B21
    async def test_capture_lesson(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "capture_lesson",
                {
                    "project": "smoketest",
                    "title": "Smoke Lesson",
                    "context": "E2E test",
                    "problem": "Need to verify capture works",
                    "solution": "Call the tool",
                    "tags": ["smoke", "test"],
                },
            )
        )
        assert "captured" in result.lower()

    # B22
    async def test_capture_lesson_dedup(self, server: FastMCP) -> None:
        # First capture
        await server.call_tool(
            "capture_lesson",
            {
                "project": "smoketest",
                "title": "Dedup Lesson",
                "context": "test",
                "problem": "test",
                "solution": "test",
            },
        )
        # Second with same title
        result = _text(
            await server.call_tool(
                "capture_lesson",
                {
                    "project": "smoketest",
                    "title": "Dedup Lesson",
                    "context": "test2",
                    "problem": "test2",
                    "solution": "test2",
                },
            )
        )
        assert "already exists" in result.lower()

    # B23
    async def test_summarize_small_file(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "delegate_task",
                {"project": "smoketest", "section": "context"},
            )
        )
        assert "Smoke Test Project" in result

    # B24
    async def test_ranked_search(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_search",
                {"query": "Decision", "ranked": True},
            )
        )
        assert "adr-001" in result

    # B25
    async def test_session_briefing(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "session_briefing",
                {"project": "smoketest"},
            )
        )
        assert "Session Briefing" in result

    # B26
    async def test_vault_search_recent(self, server: FastMCP) -> None:
        result = _text(
            await server.call_tool(
                "vault_search",
                {"project": "smoketest", "since_days": 30},
            )
        )
        # Should return something (at least the recently created files)
        assert "smoketest" in result.lower() or "recent" in result.lower()

    # B27
    async def test_vault_health_usage(self, server: FastMCP) -> None:
        # Call a tool first to generate usage data
        await server.call_tool("vault_list", {})
        result = _text(
            await server.call_tool(
                "vault_health",
                {"include_usage": True},
            )
        )
        assert "vault_list" in result


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
        result = _text(await mcp.call_tool("vault_list", {}))
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
        result = _text(
            await server.call_tool(
                "vault_query",
                {"project": "smoketest", "path": "92-large-doc.md", "max_lines": 10},
            )
        )
        assert "truncated" in result.lower()
        lines_before_truncation = result.split("[...")[0].strip().splitlines()
        assert len(lines_before_truncation) == 10


@skip_no_worker
class TestWorkerDispatch:
    """A real dispatch against the configured worker.

    This is the criterion the whole change exists to make passable: before
    #384 the worker reached zero models, so no smoke test of it could pass on
    any machine.
    """

    async def test_dispatch_returns_a_real_response(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("delegate_task", {"prompt": PING_PROMPT}))
        assert "Worker Response" in result
        assert WORKER_MODEL in result

    async def test_dispatch_records_usage(
        self, server: FastMCP, smoke_budget: BudgetTracker
    ) -> None:
        await server.call_tool("delegate_task", {"prompt": PING_PROMPT})
        usage = smoke_budget.month_usage()
        assert usage["request_count"] == 1
        assert usage["total_tokens"] > 0


class TestWorkerStatusSmoke:
    """Real status + model listing."""

    @skip_no_worker
    async def test_status_reports_reachable_against_a_real_endpoint(self, server: FastMCP) -> None:
        """The probe, exercised for real — the assertion CI cannot make.

        Unit tests can only prove the reachable/unreachable branches render
        differently. Only a live endpoint proves the probe actually reaches one.
        """
        result = _text(await server.call_tool("worker_status", {}))
        assert "Reachable: yes" in result

    @skip_no_worker
    async def test_status_shows_the_configured_model(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("worker_status", {}))
        assert WORKER_MODEL in result

    @skip_no_worker
    async def test_status_lists_worker_models(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("worker_status", {}))
        assert "Available Models" in result

    async def test_status_reports_usage_not_dollars(self, server: FastMCP) -> None:
        result = _text(await server.call_tool("worker_status", {}))
        assert "Usage this month" in result
        assert "$" not in result
