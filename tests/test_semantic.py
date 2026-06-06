"""Unit tests for the semantic retrieval engine (HIVE-211 PR3 + PR5).

Tests cover:
  - chunk_markdown: hybrid chunker (structural + size-cap)
  - VaultIndex: build, persist, reload, mismatch-rebuild, search, update_file
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from hive._semantic import Chunk, VaultIndex, chunk_markdown
from hive.clients import EmbedResponse

if TYPE_CHECKING:
    from pathlib import Path


# ── helpers ──────────────────────────────────────────────────────────────────


def _fake_embed_client(vectors: list[list[float]]) -> object:
    """Return a mock client whose embed() returns the given vectors in order."""
    i = 0

    async def _embed(texts: list[str], model: str = "") -> EmbedResponse:
        nonlocal i
        batch = vectors[i : i + len(texts)]
        i += len(texts)
        return EmbedResponse(vectors=batch, model=model or "test-model", tokens=5, latency_ms=1)

    client = MagicMock()
    client.embed = _embed
    return client


def _unit_vectors(n: int, dim: int = 4) -> list[list[float]]:
    """n identical unit vectors [1, 0, 0, ...]."""
    v = [1.0] + [0.0] * (dim - 1)
    return [v] * n


# ── chunk_markdown ────────────────────────────────────────────────────────────


class TestChunkMarkdown:
    def test_short_content_is_single_chunk(self) -> None:
        chunks = chunk_markdown("# Hello\nshort body", source="a.md")
        assert len(chunks) == 1
        assert "Hello" in chunks[0].text

    def test_empty_content_returns_no_chunks(self) -> None:
        assert chunk_markdown("", source="a.md") == []
        assert chunk_markdown("   \n\n  ", source="a.md") == []

    def test_heading_splits_into_sections(self) -> None:
        content = "# Section A\nbody A\n\n## Section B\nbody B"
        chunks = chunk_markdown(content, source="a.md")
        assert len(chunks) == 2
        assert any("Section A" in c.text for c in chunks)
        assert any("Section B" in c.text for c in chunks)

    def test_preamble_before_first_heading(self) -> None:
        content = "Preamble text here.\n\n# First heading\nbody"
        chunks = chunk_markdown(content, source="a.md")
        assert len(chunks) == 2
        assert "Preamble" in chunks[0].text
        assert chunks[0].heading == ""

    def test_heading_attribute_set_correctly(self) -> None:
        content = "## My Section\nbody text"
        chunks = chunk_markdown(content, source="x.md")
        assert chunks[0].heading == "## My Section"

    def test_source_propagated(self) -> None:
        chunks = chunk_markdown("# H\nbody", source="my/file.md")
        assert all(c.source == "my/file.md" for c in chunks)

    def test_chunk_idx_increments_across_sections(self) -> None:
        content = "# A\nbody A\n# B\nbody B\n# C\nbody C"
        chunks = chunk_markdown(content, source="a.md")
        assert [c.chunk_idx for c in chunks] == list(range(len(chunks)))

    def test_size_cap_splits_large_section(self) -> None:
        body = "x" * 3600
        content = f"# Big\n{body}"
        chunks = chunk_markdown(content, source="a.md", max_chars=1200, overlap_chars=200)
        assert len(chunks) >= 3

    def test_overlap_produces_shared_content(self) -> None:
        # section = "# S\n" (4) + body (1796) = 1800 chars total
        # step = max_chars - overlap = 1200 - 400 = 800
        # chunk 0: 0:1200 | chunk 1: 800:1800 — exactly 2 windows
        body = "a" * 898 + "b" * 898
        content = f"# S\n{body}"
        chunks = chunk_markdown(content, source="a.md", max_chars=1200, overlap_chars=400)
        assert len(chunks) == 2
        # overlap: the tail of chunk 0 and the head of chunk 1 share content
        overlap_region = chunks[0].text[-400:]
        assert overlap_region in chunks[1].text


# ── VaultIndex ────────────────────────────────────────────────────────────────


class TestVaultIndex:
    async def test_build_indexes_all_md_files(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("# Alpha\nsome content about alpha")
        (vault / "b.md").write_text("# Beta\nsome content about beta")

        n_chunks = len(chunk_markdown("# Alpha\nsome content about alpha", "a.md")) + len(
            chunk_markdown("# Beta\nsome content about beta", "b.md")
        )
        client = _fake_embed_client(_unit_vectors(n_chunks + 1))  # +1 for the query
        idx = VaultIndex(vault, client, "test-model", tmp_path / "idx")  # type: ignore[arg-type]

        results = await idx.search("alpha", top_k=2)
        assert len(results) == 2
        assert all(isinstance(c, Chunk) for c, _ in results)
        assert all(isinstance(s, float) for _, s in results)

    async def test_search_top_k_capped_by_chunk_count(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "one.md").write_text("# One\nonly one chunk")

        n = len(chunk_markdown("# One\nonly one chunk", "one.md"))
        client = _fake_embed_client(_unit_vectors(n + 1))
        idx = VaultIndex(vault, client, "test-model", tmp_path / "idx")  # type: ignore[arg-type]

        results = await idx.search("query", top_k=99)
        assert len(results) == n  # capped at actual chunk count

    async def test_empty_vault_returns_empty_results(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        client = _fake_embed_client([])
        idx = VaultIndex(vault, client, "test-model", tmp_path / "idx")  # type: ignore[arg-type]

        results = await idx.search("anything")
        assert results == []

    async def test_persist_and_reload(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "c.md").write_text("# Content\nbody text")

        n = len(chunk_markdown("# Content\nbody text", "c.md"))
        client = _fake_embed_client(_unit_vectors(n + 2))  # extra for 2 searches
        idx_dir = tmp_path / "idx"

        idx1 = VaultIndex(vault, client, "test-model", idx_dir)  # type: ignore[arg-type]
        await idx1.ensure_built()
        d = idx1._index_dir_path()
        assert (d / "index.json").exists()
        assert (d / "index.npy").exists()

        # Second instance loads from disk — embed not called again for build
        client2 = _fake_embed_client(_unit_vectors(1))  # only query embed
        idx2 = VaultIndex(vault, client2, "test-model", idx_dir)  # type: ignore[arg-type]
        results = await idx2.search("content")
        assert len(results) > 0

    async def test_mismatch_on_model_triggers_rebuild(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "d.md").write_text("# D\nbody")

        n = len(chunk_markdown("# D\nbody", "d.md"))
        idx_dir = tmp_path / "idx"

        client_a = _fake_embed_client(_unit_vectors(n + 1))
        idx_a = VaultIndex(vault, client_a, "model-A", idx_dir)  # type: ignore[arg-type]
        await idx_a.ensure_built()

        call_count = 0

        async def counting_embed(texts: list[str], model: str = "") -> EmbedResponse:
            nonlocal call_count
            call_count += len(texts)
            return EmbedResponse(
                vectors=_unit_vectors(len(texts)),
                model=model or "model-B",
                tokens=1,
                latency_ms=1,
            )

        client_b = MagicMock()
        client_b.embed = counting_embed
        idx_b = VaultIndex(vault, client_b, "model-B", idx_dir)  # type: ignore[arg-type]
        await idx_b.ensure_built()
        assert call_count > 0  # rebuild happened (not zero calls)

    async def test_ensure_built_idempotent(self, tmp_path: Path) -> None:
        """Calling ensure_built twice only builds once."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "e.md").write_text("# E\nbody")

        build_calls = 0

        async def counting_embed(texts: list[str], model: str = "") -> EmbedResponse:
            nonlocal build_calls
            build_calls += len(texts)
            return EmbedResponse(
                vectors=_unit_vectors(len(texts)), model=model, tokens=1, latency_ms=1
            )

        client = MagicMock()
        client.embed = counting_embed
        idx = VaultIndex(vault, client, "m", tmp_path / "idx")  # type: ignore[arg-type]

        await idx.ensure_built()
        first_calls = build_calls
        await idx.ensure_built()  # no-op: already built
        assert build_calls == first_calls

    async def test_cosine_similarity_ordering(self, tmp_path: Path) -> None:  # noqa: E501 keep last VaultIndex test
        """Chunk with higher cosine similarity to query ranks first."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "f.md").write_text("# Relevant\nbody\n# Irrelevant\nbody2")

        # 2D vectors: relevant=[1,0], irrelevant=[0,1], query=[1,0]
        chunk_list = chunk_markdown("# Relevant\nbody\n# Irrelevant\nbody2", "f.md")
        assert len(chunk_list) == 2

        build_vecs = [[1.0, 0.0], [0.0, 1.0]]  # [relevant, irrelevant]
        query_vec = [[1.0, 0.0]]

        call_seq: list[list[list[float]]] = [build_vecs, query_vec]
        call_idx = 0

        async def seq_embed(texts: list[str], model: str = "") -> EmbedResponse:
            nonlocal call_idx
            vecs = call_seq[call_idx]
            call_idx += 1
            return EmbedResponse(vectors=vecs, model=model, tokens=1, latency_ms=1)

        client = MagicMock()
        client.embed = seq_embed
        idx = VaultIndex(vault, client, "m", tmp_path / "idx")  # type: ignore[arg-type]
        results = await idx.search("query", top_k=2)

        assert len(results) == 2
        top_chunk, top_score = results[0]
        assert top_chunk.heading == "# Relevant"
        assert top_score > results[1][1]


# ── PR5: incremental update ────────────────────────────────────────────────────


class TestVaultIndexUpdateFile:
    """VaultIndex.update_file() — incremental re-embed for a single file (AC4)."""

    async def test_update_file_adds_new_chunks(self, tmp_path: Path) -> None:
        """update_file() re-embeds the modified file and adds its chunks to the index."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("# A\nbody A")
        (vault / "b.md").write_text("# B\nbody B")

        # _fake_embed_client returns the MagicMock with embed already set — use directly
        client = _fake_embed_client(_unit_vectors(10))
        idx = VaultIndex(vault, client, "m", tmp_path / "idx")  # type: ignore[arg-type]
        await idx.ensure_built()
        sources_before = {c.source for c in idx._data.chunks}  # type: ignore[union-attr]
        assert "b.md" in sources_before

        # Overwrite b.md with new content; same client pool has remaining vectors
        (vault / "b.md").write_text("# B updated\nnew body B")
        await idx.update_file(vault / "b.md")

        sources_after = {c.source for c in idx._data.chunks}  # type: ignore[union-attr]
        assert "b.md" in sources_after
        headings = {c.heading for c in idx._data.chunks if c.source == "b.md"}  # type: ignore[union-attr]
        assert "# B updated" in headings

    async def test_update_file_removes_deleted_file(self, tmp_path: Path) -> None:
        """update_file() removes chunks for a deleted file from the index."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("# A\nbody A")
        (vault / "del.md").write_text("# Del\nwill be deleted")

        client = _fake_embed_client(_unit_vectors(10))
        idx = VaultIndex(vault, client, "m", tmp_path / "idx")  # type: ignore[arg-type]
        await idx.ensure_built()
        assert any(c.source == "del.md" for c in idx._data.chunks)  # type: ignore[union-attr]

        (vault / "del.md").unlink()
        await idx.update_file(vault / "del.md")

        assert not any(c.source == "del.md" for c in idx._data.chunks)  # type: ignore[union-attr]

    async def test_update_file_noop_when_index_not_built(self, tmp_path: Path) -> None:
        """update_file() is a no-op if the index has never been built (AC4 zero overhead)."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "x.md").write_text("# X\nbody")

        client = MagicMock()
        embed_called = []

        async def _track_embed(texts: list[str], model: str = "") -> EmbedResponse:
            embed_called.extend(texts)
            return EmbedResponse(
                vectors=_unit_vectors(len(texts)), model="m", tokens=1, latency_ms=1
            )

        client.embed = _track_embed
        idx = VaultIndex(vault, client, "m", tmp_path / "idx")  # type: ignore[arg-type]
        # Do NOT call ensure_built — index is None
        await idx.update_file(vault / "x.md")
        assert not embed_called, "update_file should not embed when index not built"
