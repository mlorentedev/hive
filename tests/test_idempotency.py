"""Write idempotency — at-most-once via idempotency key (ADR-013 / §6.2).

Tool-level + unit coverage mirroring ``spike/idempotency_spike.py``: a repeated
key is a no-op (safe for ``append``), distinct keys both apply, an empty key
preserves today's behaviour, and concurrent duplicate claims dedupe to one.

The server is built with an in-memory ``IdempotencyStore`` so a key claimed in
one test never persists into another (a file-backed store would make a fixed
key "already applied" on the next run).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from hive._idempotency import IdempotencyStore
from hive.server import create_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP
    from fastmcp.tools import ToolResult


def _text(result: ToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


def _server(vault: Path) -> FastMCP:
    return create_server(
        vault_path=vault,
        idempotency_store=IdempotencyStore(":memory:"),
    )


async def _append(mcp: FastMCP, key: str, content: str) -> str:
    return _text(
        await mcp.call_tool(
            "vault_write",
            {
                "project": "testproject",
                "section": "context",
                "operation": "append",
                "content": content,
                "idempotency_key": key,
            },
        ),
    )


async def _context_body(mcp: FastMCP) -> str:
    return _text(
        await mcp.call_tool(
            "vault_query",
            {"project": "testproject", "section": "context"},
        ),
    )


async def test_repeated_key_append_is_noop(git_vault: Path) -> None:
    """The same key twice appends once — at-most-once, and append-safe."""
    mcp = _server(git_vault)
    first = await _append(mcp, "k1", "\nIDEM-ONCE\n")
    second = await _append(mcp, "k1", "\nIDEM-ONCE\n")

    assert "Updated" in first, f"first append did not apply: {first!r}"
    assert "no-op" in second.lower() and "already applied" in second.lower(), (
        f"retry was not flagged as an idempotent no-op: {second!r}"
    )
    body = await _context_body(mcp)
    assert body.count("IDEM-ONCE") == 1, f"expected one append, got: {body!r}"


async def test_distinct_keys_both_apply(git_vault: Path) -> None:
    """Different keys with identical content each write (keyed, not content-based)."""
    mcp = _server(git_vault)
    await _append(mcp, "k1", "\nDUP-CONTENT\n")
    await _append(mcp, "k2", "\nDUP-CONTENT\n")
    body = await _context_body(mcp)
    assert body.count("DUP-CONTENT") == 2, f"distinct keys did not both apply: {body!r}"


async def test_empty_key_preserves_at_least_once(git_vault: Path) -> None:
    """Empty key = today's behaviour: two identical appends both land."""
    mcp = _server(git_vault)
    await _append(mcp, "", "\nNO-KEY\n")
    await _append(mcp, "", "\nNO-KEY\n")
    body = await _context_body(mcp)
    assert body.count("NO-KEY") == 2, f"empty key changed behaviour: {body!r}"


async def test_patch_repeated_key_is_noop(git_vault: Path) -> None:
    """A retried patch with the same key is a no-op, not a 'find not found' error
    (the first apply consumed the target text)."""
    mcp = _server(git_vault)
    await mcp.call_tool(
        "vault_write",
        {
            "project": "testproject",
            "path": "90-notes.md",
            "content": "# Notes\n\nstatus: draft\n",
            "doc_type": "note",
            "operation": "create",
        },
    )
    args = {
        "project": "testproject",
        "path": "90-notes.md",
        "find": "status: draft",
        "replace": "status: final",
        "idempotency_key": "patch-k1",
    }
    first = _text(await mcp.call_tool("vault_patch", args))
    second = _text(await mcp.call_tool("vault_patch", args))

    assert "no-op" not in first.lower(), f"first patch should apply: {first!r}"
    assert "already applied" in second.lower(), f"retry was not a no-op: {second!r}"
    body = _text(
        await mcp.call_tool(
            "vault_query",
            {"project": "testproject", "path": "90-notes.md"},
        ),
    )
    assert "status: draft" not in body
    assert body.count("status: final") == 1, f"patch applied twice: {body!r}"


def test_claim_is_atomic_under_concurrency() -> None:
    """16 threads claiming one key -> exactly one wins (mirrors the spike)."""
    store = IdempotencyStore(":memory:")
    results: list[bool] = []
    guard = threading.Lock()

    def claim() -> None:
        ok = store.claim("racing-key")
        with guard:
            results.append(ok)

    threads = [threading.Thread(target=claim) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 1, f"expected exactly one claim to win: {results}"
    # release re-enables a fresh claim (the OSError-compensation path).
    store.release("racing-key")
    assert store.claim("racing-key") is True
    store.close()
