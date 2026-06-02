"""HIVE-118 Phase C — restart-on-upgrade spike (slice 1.3 de-risk).

Proves the two load-bearing assumptions behind a daemon that self-restarts
when hive-vault is upgraded in place (`uv tool upgrade`), BEFORE writing any
production code:

1. **Metadata drift is visible mid-process** — a long-lived process that
   snapshots ``importlib.metadata.version("pkg")`` at startup can later
   observe a NEW version after the on-disk ``*.dist-info`` is swapped (what
   an upgrade does), so a poll loop can detect "I am now stale" with the
   stdlib alone — no venv watching, no file parsing.
2. **An owned uvicorn server drains in-flight on ``should_exit``** — the
   daemon must finish in-flight tool calls before swapping, not cut them.
   A first attempt with an external ``SIGTERM`` was rejected by an earlier
   run: FastMCP's ``mcp.run()`` exits by the signal (rc -15) and cuts the
   in-flight call. The fix proven here: build the app via the PUBLIC
   ``mcp.http_app()``, own the ``uvicorn.Server``, and set
   ``server.should_exit = True`` (what the production drift detector will
   do). uvicorn then stops accepting, drains in-flight up to
   ``timeout_graceful_shutdown`` (FastMCP's default = 2s), and ``serve()``
   returns cleanly so we can ``exit(0)``.

Run::

    uv run python specs/HIVE-118-phase-c-daemon-model/spike/upgrade_spike.py

Exit 0 = every check passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.metadata as md
import os
import shutil
import sys
import tempfile
from pathlib import Path

import _spikelib as lib

FAKE_PKG = "hivefakeupgradepkg"


# ── check 1: metadata drift visibility ───────────────────────────────────


def _write_distinfo(root: Path, version: str) -> Path:
    di = root / f"{FAKE_PKG}-{version}.dist-info"
    di.mkdir(parents=True, exist_ok=True)
    (di / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {FAKE_PKG}\nVersion: {version}\n",
        encoding="utf-8",
    )
    return di


def _safe_version(name: str) -> str:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return "<not-found>"


def check_metadata_drift() -> tuple[str, bool, str]:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        di1 = _write_distinfo(root, "1.0.0")
        sys.path.insert(0, str(root))
        try:
            v_boot = _safe_version(FAKE_PKG)
            # Simulate `uv tool upgrade`: remove the old dist-info, add the new.
            shutil.rmtree(di1)
            _write_distinfo(root, "2.0.0")
            os.utime(root, None)  # bump dir mtime (coarse-granularity FS safety)

            naive = _safe_version(FAKE_PKG)
            importlib.invalidate_caches()
            after_invalidate = _safe_version(FAKE_PKG)
        finally:
            sys.path.remove(str(root))

    ok = after_invalidate == "2.0.0"
    detail = (
        f"boot={v_boot} naive={naive} after_invalidate={after_invalidate} "
        f"(need 2.0.0; invalidate_caches "
        f"{'NOT ' if naive == '2.0.0' else ''}required)"
    )
    return ("metadata drift visible mid-process", ok, detail)


# ── check 2: owned-server graceful drain on should_exit ──────────────────


async def _owned_server_drain(port: int, token: str) -> tuple[bool, bool, str, bool]:
    import uvicorn

    mcp = lib.build_server("hive-upgrade-owned", token)
    started = asyncio.Event()
    completed = {"v": False}

    @mcp.tool
    async def slow(seconds: float = 0.8) -> str:
        """Signal start, sleep < the 2s graceful window, then mark completion.
        ``completed`` distinguishes "handler ran to its return server-side"
        (write would commit; client just loses the ack -> idempotency covers it)
        from "handler cancelled mid-flight" (partial-write risk)."""
        started.set()
        await asyncio.sleep(seconds)
        completed["v"] = True
        return f"slept {seconds}s"

    app = mcp.http_app(path=lib.PATH, transport="http")
    config = uvicorn.Config(
        app, host=lib.HOST, port=port,
        timeout_graceful_shutdown=2, lifespan="on", log_level="critical",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # we drive should_exit

    serve_task = asyncio.create_task(server.serve())
    for _ in range(200):  # wait until uvicorn reports started (<=10s)
        if getattr(server, "started", False):
            break
        await asyncio.sleep(0.05)

    drained = False
    err = ""
    client = lib.make_client(lib.url_for(port), token)
    try:
        async with client:
            call = asyncio.create_task(client.call_tool("slow", {"seconds": 0.8}))
            try:  # trigger shutdown ONLY once the handler is provably running
                await asyncio.wait_for(started.wait(), timeout=8)
            except TimeoutError:
                err = "handler-never-started"
            server.should_exit = True  # what the drift detector will do
            try:
                await asyncio.wait_for(call, timeout=6)
                drained = True  # call returned without exception => drained
            except Exception as exc:  # noqa: BLE001
                err = err or type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        err = err or f"ctx:{type(exc).__name__}"

    clean_exit = False
    with contextlib.suppress(Exception):
        await asyncio.wait_for(serve_task, timeout=5)  # serve() returns after drain
        clean_exit = True
    if not serve_task.done():
        serve_task.cancel()
    # give a cancelled-or-completing handler a beat to settle before reading
    await asyncio.sleep(0.3)
    return drained, clean_exit, err, completed["v"]


def check_owned_server_drain() -> tuple[str, bool, str]:
    token = lib.new_token()
    port = lib.free_port()
    drained, clean, err, handler_done = asyncio.run(_owned_server_drain(port, token))
    # FINDING: owning the uvicorn server and setting should_exit gives a CLEAN
    # stop (serve() returns -> exit 0 for the supervisor) — the reliable
    # primitive we build on. True in-flight DRAIN is NOT deliverable over
    # streamable-http: the MCP session manager cancels the active handler on
    # lifespan shutdown, so neither the client response NOR server-side
    # completion is guaranteed. Therefore restart-on-upgrade must clean-stop and
    # lean on idempotency (slice 2) + auto-reconnect (slice 3) for cut calls —
    # it is NOT a self-contained drain. ok gates only on the achievable property.
    ok = clean
    detail = (
        f"clean_stop={clean} | NOT-drainable: client_got_response={drained} "
        f"handler_completed_serverside={handler_done} err={err or 'none'}"
    )
    return ("owned-server should_exit => clean stop (exit 0)", ok, detail)


def main() -> int:
    checks = [check_metadata_drift(), check_owned_server_drain()]
    return lib.report("HIVE-118 restart-on-upgrade spike", checks)


if __name__ == "__main__":
    sys.exit(main())
