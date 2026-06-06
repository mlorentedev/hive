"""Semantic retrieval engine for vault_ask (HIVE-211 PR3).

chunk_markdown  — hybrid chunker: structural by H1/H2/H3, then size-cap with overlap.
VaultIndex      — lazy-built, persisted embedding index backed by numpy + JSON.
                  Keyed on (vault, model): provider/dim switch forces a mismatch-rebuild.

Persistence format (two sibling files, no pickle):
  index.npy   — float32 numpy array (vectors), loaded with allow_pickle=False
  index.json  — metadata: version, vault, model, chunk list (source/heading/text/idx)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

    from hive.clients import OpenAICompatibleClient

_log = logging.getLogger(__name__)

_INDEX_VERSION = 1
_MAX_CHARS = 1_200  # ~300 tokens (4 chars/token heuristic)
_OVERLAP_CHARS = 200  # ~50 tokens sliding-window overlap
_EMBED_BATCH = 64  # max texts per /embeddings call
_HEADING_SPLIT_RE = re.compile(r"(^#{1,3}[ \t]+[^\n]+)", re.MULTILINE)


@dataclass
class Chunk:
    """A text chunk from a vault file, with its source and section heading."""

    text: str
    source: str  # vault-relative POSIX path
    heading: str = ""
    chunk_idx: int = 0


@dataclass
class _IndexData:
    version: int
    vault: str
    model: str
    chunks: list[Chunk]
    vectors: np.ndarray  # shape (N, D), float32, L2-normalised; saved as .npy (no pickle)


def _slide(text: str, max_chars: int, overlap: int) -> list[str]:
    """Sliding-window split, step = max_chars - overlap."""
    slices: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        slices.append(text[start:end])
        if end >= len(text):
            break
        start += max_chars - overlap
    return slices


def chunk_markdown(
    content: str,
    source: str,
    max_chars: int = _MAX_CHARS,
    overlap_chars: int = _OVERLAP_CHARS,
) -> list[Chunk]:
    """Hybrid chunker: split by H1/H2/H3 headings, then size-cap with overlap.

    Structural split keeps headings together with their bodies. Size-cap
    prevents any single chunk from overwhelming the embedding model's context.
    Overlap ensures continuity across adjacent windows in long sections.
    """
    parts = _HEADING_SPLIT_RE.split(content)
    # parts alternates: preamble, heading, body, heading, body, ...
    sections: list[tuple[str, str]] = []

    if parts[0].strip():
        sections.append(("", parts[0]))

    i = 1
    while i < len(parts):
        heading = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        section_text = f"{heading}\n{body}" if body.strip() else heading
        sections.append((heading, section_text.strip()))
        i += 2

    chunks: list[Chunk] = []
    idx = 0
    for heading, section_text in sections:
        if not section_text:
            continue
        if len(section_text) <= max_chars:
            chunks.append(Chunk(text=section_text, source=source, heading=heading, chunk_idx=idx))
            idx += 1
        else:
            for slice_text in _slide(section_text, max_chars, overlap_chars):
                chunks.append(Chunk(text=slice_text, source=source, heading=heading, chunk_idx=idx))
                idx += 1

    return chunks


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalise rows; returns float32 array (safe on zero vectors)."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return (vectors / norms).astype(np.float32)


class VaultIndex:
    """Lazy-built, persisted embedding index over a vault.

    asyncio.Lock ensures concurrent ensure_built() calls build the index
    exactly once even under parallel vault_ask requests. The index is
    persisted as a pickle keyed on (vault_hash, model_slug) so different
    embedding models have separate files and a model switch triggers a
    mismatch-rebuild rather than answering from a wrong-dimensioned index.
    """

    def __init__(
        self,
        vault: Path,
        embed_client: OpenAICompatibleClient,
        model: str,
        index_dir: Path,
    ) -> None:
        self._vault = vault
        self._embed_client = embed_client
        self._model = model
        self._index_dir = index_dir
        self._data: _IndexData | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    def _index_dir_path(self) -> Path:
        vault_hash = hashlib.sha256(str(self._vault).encode()).hexdigest()[:12]
        model_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", self._model)[:48]
        return self._index_dir / vault_hash / model_slug

    def _load(self) -> _IndexData | None:
        """Load index from disk (JSON meta + .npy vectors); return None on any mismatch."""
        d = self._index_dir_path()
        meta_path = d / "index.json"
        vecs_path = d / "index.npy"
        if not meta_path.exists() or not vecs_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            vectors: np.ndarray = np.load(str(vecs_path), allow_pickle=False)
        except Exception:
            return None
        if (
            meta.get("version") != _INDEX_VERSION
            or meta.get("vault") != str(self._vault)
            or meta.get("model") != self._model
        ):
            _log.info("vault_ask: stale index (version/vault/model mismatch) — rebuilding")
            return None
        chunks = [
            Chunk(
                text=c["text"],
                source=c["source"],
                heading=c.get("heading", ""),
                chunk_idx=c.get("chunk_idx", i),
            )
            for i, c in enumerate(meta.get("chunks", []))
        ]
        return _IndexData(_INDEX_VERSION, str(self._vault), self._model, chunks, vectors)

    async def _build(self) -> _IndexData:
        chunks: list[Chunk] = []
        for md_file in sorted(self._vault.rglob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            source = md_file.relative_to(self._vault).as_posix()
            chunks.extend(chunk_markdown(content, source))

        if not chunks:
            empty: np.ndarray = np.zeros((0, 1), dtype=np.float32)
            return _IndexData(_INDEX_VERSION, str(self._vault), self._model, [], empty)

        texts = [c.text for c in chunks]
        all_vecs: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            resp = await self._embed_client.embed(texts[i : i + _EMBED_BATCH], model=self._model)
            all_vecs.extend(resp.vectors)

        vectors = _l2_normalize(np.array(all_vecs, dtype=np.float32))
        data = _IndexData(_INDEX_VERSION, str(self._vault), self._model, chunks, vectors)

        d = self._index_dir_path()
        d.mkdir(parents=True, exist_ok=True)
        np.save(str(d / "index.npy"), vectors)
        meta = {
            "version": _INDEX_VERSION,
            "vault": str(self._vault),
            "model": self._model,
            "chunks": [
                {"text": c.text, "source": c.source, "heading": c.heading, "chunk_idx": c.chunk_idx}
                for c in chunks
            ],
        }
        (d / "index.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        _log.info("vault_ask: indexed %d chunks from %s", len(chunks), self._vault)
        return data

    async def ensure_built(self) -> None:
        """Load from disk if valid; build and persist otherwise."""
        if self._data is not None:
            return
        async with self._lock:
            if self._data is not None:  # re-check after lock
                return
            loaded = self._load()
            self._data = loaded if loaded is not None else await self._build()

    async def update_file(self, filepath: Path) -> None:
        """Incrementally re-embed a single file; no-op if the index is not built (AC4).

        Removes stale chunks for the given file, then re-embeds the current
        content (if the file still exists). Persists the updated index to disk.
        Safe to call via asyncio.create_task() — the lock prevents concurrent
        index corruption.
        """
        async with self._lock:
            if self._data is None:
                return  # index not built — zero overhead guaranteed

            source = filepath.relative_to(self._vault).as_posix()

            # Identify rows to keep (exclude the file being updated)
            keep_idx = [i for i, c in enumerate(self._data.chunks) if c.source != source]
            kept_chunks = [self._data.chunks[i] for i in keep_idx]
            kept_vectors = (
                self._data.vectors[np.array(keep_idx, dtype=np.intp)]
                if keep_idx
                else np.zeros((0, self._data.vectors.shape[-1]), dtype=np.float32)
            )

            # Re-embed if the file still exists
            new_chunks: list[Chunk] = []
            new_vectors: np.ndarray | None = None
            if filepath.exists():
                try:
                    content = filepath.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    pass
                else:
                    new_chunks = chunk_markdown(content, source)
                    if new_chunks:
                        texts = [c.text for c in new_chunks]
                        all_vecs: list[list[float]] = []
                        for i in range(0, len(texts), _EMBED_BATCH):
                            resp = await self._embed_client.embed(
                                texts[i : i + _EMBED_BATCH], model=self._model
                            )
                            all_vecs.extend(resp.vectors)
                        new_vectors = _l2_normalize(np.array(all_vecs, dtype=np.float32))

            # Merge kept + new
            all_chunks = kept_chunks + new_chunks
            if new_vectors is not None and new_vectors.shape[0] > 0:
                all_vectors = (
                    new_vectors
                    if kept_vectors.shape[0] == 0
                    else np.concatenate([kept_vectors, new_vectors], axis=0)
                )
            else:
                all_vectors = kept_vectors

            self._data = _IndexData(
                _INDEX_VERSION, str(self._vault), self._model, all_chunks, all_vectors
            )

            # Persist
            d = self._index_dir_path()
            d.mkdir(parents=True, exist_ok=True)
            np.save(str(d / "index.npy"), all_vectors)
            meta = {
                "version": _INDEX_VERSION,
                "vault": str(self._vault),
                "model": self._model,
                "chunks": [
                    {
                        "text": c.text,
                        "source": c.source,
                        "heading": c.heading,
                        "chunk_idx": c.chunk_idx,
                    }
                    for c in all_chunks
                ],
            }
            (d / "index.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            _log.info("vault_ask: updated %s → %d chunks remain", source, len(new_chunks))

    async def search(self, question: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
        """Return top-k chunks by cosine similarity to the embedded question."""
        await self.ensure_built()
        data = self._data
        if data is None or not data.chunks:
            return []

        resp = await self._embed_client.embed([question], model=self._model)
        q_vec = np.array(resp.vectors[0], dtype=np.float32)
        q_norm = float(np.linalg.norm(q_vec))
        if q_norm > 0.0:
            q_vec = q_vec / q_norm

        sims: np.ndarray = data.vectors @ q_vec
        k = min(top_k, len(data.chunks))
        top_idx = np.argpartition(sims, -k)[-k:]
        top_idx = top_idx[np.argsort(sims[top_idx])[::-1]]

        return [(data.chunks[int(i)], float(sims[i])) for i in top_idx]
