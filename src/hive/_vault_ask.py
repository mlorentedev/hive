"""vault_ask — optional semantic Q&A over the vault (HIVE-211).

Disabled unless an embeddings backend is configured (``HIVE_EMBED_BASE_URL``)
AND the optional ``[semantic]`` dependencies are installed. When disabled it
returns a clear "how to enable" message and never raises — base installs are
unaffected and no heavy dependency is imported at module load.

PR3: the retrieval pipeline is live. vault_ask embeds the question, searches a
lazily-built VaultIndex (numpy cosine similarity, persisted as JSON + .npy),
and returns the top-5 most relevant vault sections with source citations.
Synthesis into a full cited answer lands in PR4.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from hive._helpers import _READ_ONLY, tool_span, track

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from hive._context import ServerContext
    from hive._semantic import Chunk, VaultIndex

_ENABLE_STEPS = (
    "To enable natural-language Q&A grounded in your vault:\n"
    "  1. Install the optional dependencies:\n"
    "       pip install 'hive-vault[semantic]'\n"
    "  2. Configure an OpenAI-compatible embeddings backend. Default (NaN):\n"
    "       HIVE_EMBED_BASE_URL=https://api.nan.builders/v1\n"
    "       HIVE_EMBED_MODEL=qwen3-embedding\n"
    "       HIVE_EMBED_API_KEY=<your-key>\n"
    "     Or a local, private Ollama (no data leaves your machine):\n"
    "       HIVE_EMBED_BASE_URL=http://localhost:11434/v1\n"
    "       HIVE_EMBED_MODEL=nomic-embed-text\n"
    "  3. Restart the server; the index builds lazily on first use.\n\n"
    "Meanwhile, use vault_search for lexical (keyword / regex) search."
)

_DISABLED_NO_BACKEND = (
    "vault_ask is disabled: no semantic backend is configured "
    "(HIVE_EMBED_BASE_URL is unset).\n\n" + _ENABLE_STEPS
)

_DISABLED_NO_EXTRA = (
    "vault_ask is disabled: a backend is configured (HIVE_EMBED_BASE_URL) but "
    "the optional dependencies are missing.\n"
    "Install them with:  pip install 'hive-vault[semantic]'\n"
    "then restart the server."
)


def _semantic_extra_available() -> bool:
    """True if the optional ``[semantic]`` dependencies import.

    Probes lazily so a base install (without the extra) imports this module
    fine and pays zero cost — nothing heavy is imported at module load.
    """
    try:
        import numpy  # noqa: F401  (declared by the [semantic] extra)
    except ImportError:
        return False
    return True


def _disabled_reason(ctx: ServerContext) -> str:
    """Non-empty "how to enable" message when vault_ask is disabled; empty when ready."""
    if not ctx.embed_base_url:
        return _DISABLED_NO_BACKEND
    if not _semantic_extra_available():
        return _DISABLED_NO_EXTRA
    return ""


# ── Seam functions: replaceable in tests without touching the real HTTP client ─


def _build_embed_client(base_url: str, api_key: str, model: str) -> object:
    """Build the OpenAICompatibleClient for embedding. Seam for test mocking."""
    from hive.clients import OpenAICompatibleClient

    return OpenAICompatibleClient(
        base_url=base_url,
        api_key=api_key,
        default_model=model,
        provider_name="embed",
    )


def _index_dir_for() -> Path:
    """Return the directory for persisted index files. Seam for test mocking."""
    from hive.config import settings

    return Path(settings.db_path).parent / "index"


# ── Result formatting ─────────────────────────────────────────────────────────


def _format_retrieval(question: str, results: list[tuple[Chunk, float]]) -> str:
    if not results:
        return (
            f"vault_ask: '{question}' — no relevant sections found in the vault.\n\n"
            "Consider rephrasing, or use vault_search for keyword / regex lookup."
        )
    lines = [f"# vault_ask: '{question}'\n"]
    for rank, (chunk, score) in enumerate(results, start=1):
        heading_label = f" — {chunk.heading!r}" if chunk.heading else ""
        lines.append(f"### {rank}. {chunk.source}{heading_label}  (score: {score:.2f})")
        preview = chunk.text[:500] + "…" if len(chunk.text) > 500 else chunk.text
        lines.append(f"> {preview}")
        lines.append("")
    return "\n".join(lines)


# ── Registration ──────────────────────────────────────────────────────────────


def register_vault_ask(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register the vault_ask tool on the MCP server."""

    # Closure-scoped state: each create_server() call gets its own VaultIndex.
    # Dict is used so the nonlocal assignment is avoided (mypy-friendly).
    _state: dict[str, VaultIndex | None] = {"index": None}
    _build_lock: asyncio.Lock = asyncio.Lock()

    async def _get_index() -> VaultIndex:
        if _state["index"] is not None:
            return _state["index"]
        async with _build_lock:
            if _state["index"] is not None:
                return _state["index"]
            from hive._semantic import VaultIndex

            embed_client = _build_embed_client(
                ctx.embed_base_url, ctx.embed_api_key, ctx.embed_model
            )
            idx: VaultIndex = VaultIndex(
                vault=ctx.vault,
                embed_client=embed_client,  # type: ignore[arg-type]
                model=ctx.embed_model,
                index_dir=_index_dir_for(),
            )
            _state["index"] = idx
        return _state["index"]  # type: ignore[return-value]

    @mcp.tool(annotations=_READ_ONLY)
    async def vault_ask(question: str = "") -> str:
        """Ask a natural-language question; get source-cited relevant vault sections
        (semantic retrieval / RAG).

        OPTIONAL — disabled by default. Requires the `[semantic]` extra plus an
        embeddings backend (`HIVE_EMBED_BASE_URL`); until then it returns a
        short how-to-enable message and never errors. For keyword / regex
        lookups use `vault_search` instead.

        Args:
            question: The natural-language question to answer. Use `question`,
                not `query` or `prompt`.
        """
        reason = _disabled_reason(ctx)
        if reason:
            return track(ctx, "vault_ask", reason)
        if not question.strip():
            return track(ctx, "vault_ask", "vault_ask: provide a non-empty question.")
        try:
            async with tool_span("vault_ask", ctx.tool_timeout):
                idx = await _get_index()
                results = await idx.search(question, top_k=5)
                return track(ctx, "vault_ask", _format_retrieval(question, results))
        except TimeoutError:
            return track(ctx, "vault_ask", "vault_ask: retrieval timed out. Try a simpler query.")
