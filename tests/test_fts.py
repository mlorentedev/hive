"""Tests for SQLite FTS5 search index and BM25 ranking (FEAT-015)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from hive._fts import VaultFTSIndex
from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path


def _text(result: object) -> str:
    if hasattr(result, "content") and result.content:
        first = result.content[0]
        return getattr(first, "text", str(first))
    return str(result)


@pytest.fixture
def fts_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()

    p1 = vault / "10_projects" / "kibelab"
    p1.mkdir(parents=True)
    (p1 / "deploy-guide.md").write_text(
        "---\n"
        "id: kibelab-deploy\n"
        "type: runbook\n"
        "status: active\n"
        "tags: [kubernetes, deployment, production]\n"
        "created: '2026-08-01'\n"
        "---\n\n"
        "# Deploying Applications to Kubernetes\n\n"
        "Step by step guide for zero-downtime rolling deployment.\n"
    )
    (p1 / "network-setup.md").write_text(
        "---\n"
        "id: kibelab-net\n"
        "type: architecture\n"
        "status: draft\n"
        "tags: [networking, cilium]\n"
        "created: '2026-08-10'\n"
        "---\n\n"
        "# Cilium CNI Architecture\n\n"
        "Network security policies with eBPF.\n"
    )

    p2 = vault / "00_meta" / "patterns"
    p2.mkdir(parents=True)
    (p2 / "pattern-spec.md").write_text(
        "---\n"
        "id: pattern-spec\n"
        "type: pattern\n"
        "status: active\n"
        "tags: [sdd, discipline]\n"
        "created: '2026-07-15'\n"
        "---\n\n"
        "# Spec Driven Development\n\n"
        "Every feature starts with a proposal and tasks.\n"
    )
    return vault


class TestVaultFTSIndex:
    def test_sync_and_bm25_search(self, fts_vault: Path, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        fts = VaultFTSIndex(
            fts_vault,
            cache_dir=cache_dir,
            scopes={"projects": "10_projects", "meta": "00_meta"},
        )

        updated = fts.sync()
        assert updated == 3

        # Search for deployment
        matches = fts.search("deployment")
        assert len(matches) >= 1
        assert matches[0].rel_path == "10_projects/kibelab/deploy-guide.md"
        assert matches[0].doc_type == "runbook"
        assert "kubernetes" in matches[0].tags

    def test_porter_stemming_and_prefix(self, fts_vault: Path, tmp_path: Path) -> None:
        fts = VaultFTSIndex(fts_vault, cache_dir=tmp_path / "cache")
        fts.sync()

        # Query "deploying" should match "deployment" or "deploy" via Porter stemmer
        matches = fts.search("deploying")
        assert any("deploy-guide.md" in m.rel_path for m in matches)

        # Prefix search
        matches_prefix = fts.search("architec")
        assert any("network-setup.md" in m.rel_path for m in matches_prefix)

    def test_weighted_scoring_title_over_body(self, fts_vault: Path, tmp_path: Path) -> None:
        # Create a note with term in title vs term in body
        p = fts_vault / "10_projects" / "kibelab"
        (p / "title-match.md").write_text(
            "---\nid: t1\ntype: lesson\nstatus: active\n---\n# Ansible Automation\nGeneral text.\n"
        )
        (p / "body-match.md").write_text(
            "---\n"
            "id: t2\n"
            "type: lesson\n"
            "status: active\n"
            "---\n"
            "# Tools Overview\n"
            "We use ansible for some things.\n"
        )

        fts = VaultFTSIndex(fts_vault, cache_dir=tmp_path / "cache")
        fts.sync()

        matches = fts.search("ansible")
        assert len(matches) == 2
        assert matches[0].rel_path == "10_projects/kibelab/title-match.md"

    def test_type_and_scope_filters(self, fts_vault: Path, tmp_path: Path) -> None:
        fts = VaultFTSIndex(
            fts_vault,
            cache_dir=tmp_path / "cache",
            scopes={"projects": "10_projects", "meta": "00_meta"},
        )
        fts.sync()

        # Filter by type
        runbooks = fts.search("kubernetes", type_filter="runbook")
        assert len(runbooks) == 1
        assert runbooks[0].doc_type == "runbook"

        # Filter by status
        drafts = fts.search("cilium", status_filter="draft")
        assert len(drafts) == 1
        assert drafts[0].doc_status == "draft"

        # Filter by scope
        meta_only = fts.search("development", scope="meta")
        assert all(m.rel_path.startswith("00_meta/") for m in meta_only)

    def test_incremental_update_and_removal(self, fts_vault: Path, tmp_path: Path) -> None:
        fts = VaultFTSIndex(fts_vault, cache_dir=tmp_path / "cache")
        fts.sync()

        new_file = fts_vault / "10_projects" / "kibelab" / "new-note.md"
        new_file.write_text(
            "---\nid: new\ntype: note\nstatus: active\n---\n# Telemetry\nPrometheus metrics.\n"
        )

        # Incremental update
        fts.update_file(new_file)
        matches = fts.search("telemetry")
        assert len(matches) == 1
        assert matches[0].rel_path == "10_projects/kibelab/new-note.md"

        # Removal
        new_file.unlink()
        fts.remove_file(new_file)
        matches_after = fts.search("telemetry")
        assert len(matches_after) == 0

    def test_search_latency(self, fts_vault: Path, tmp_path: Path) -> None:
        fts = VaultFTSIndex(fts_vault, cache_dir=tmp_path / "cache")
        fts.sync()

        t0 = time.perf_counter()
        for _ in range(10):
            fts.search("kubernetes")
        elapsed_avg_ms = ((time.perf_counter() - t0) / 10) * 1000
        # Latency should be sub-millisecond or under 5ms
        assert elapsed_avg_ms < 10.0


class TestVaultSearchRankedWithFTS:
    async def test_vault_search_tool_uses_fts5(self, fts_vault: Path) -> None:
        mcp = create_server(vault_path=fts_vault)
        result = await mcp.call_tool("vault_search", {"query": "deploying", "ranked": True})
        text = _text(result)

        assert "Ranked Search" in text
        assert "deploy-guide.md" in text
        assert "score:" in text
        assert "type=runbook" in text
