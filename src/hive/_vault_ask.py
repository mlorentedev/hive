"""vault_ask — optional semantic Q&A over the vault (HIVE-211).

Disabled unless an embeddings backend is configured (``HIVE_EMBED_BASE_URL``)
AND the optional ``[semantic]`` dependencies are installed. When disabled it
returns a clear "how to enable" message and never raises — base installs are
unaffected and no heavy dependency is imported at module load. The retrieval /
synthesis pipeline lands in a later change; this module ships the tool surface
plus the graceful-degradation gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hive._helpers import _READ_ONLY, track, wrap_sync_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from hive._context import ServerContext

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

_BACKEND_READY_PENDING_INDEX = (
    "vault_ask: embeddings backend configured. Semantic retrieval becomes "
    "available once the index pipeline ships (HIVE-211). For now, use "
    "vault_search for keyword / regex search."
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
    """Return a non-empty "how to enable" message when vault_ask is disabled,
    or ``""`` when a semantic backend is fully available."""
    if not ctx.embed_base_url:
        return _DISABLED_NO_BACKEND
    if not _semantic_extra_available():
        return _DISABLED_NO_EXTRA
    return ""


def register_vault_ask(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register the vault_ask tool on the MCP server."""

    @mcp.tool(annotations=_READ_ONLY)
    @wrap_sync_tool(ctx, "vault_ask")
    def vault_ask(question: str = "") -> str:
        """Ask a natural-language question; get a synthesized, source-cited
        answer grounded in your vault (semantic retrieval / RAG).

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
        return track(ctx, "vault_ask", _BACKEND_READY_PENDING_INDEX)
