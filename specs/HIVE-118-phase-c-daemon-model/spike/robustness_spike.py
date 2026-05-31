"""HIVE-118 Phase C — transport robustness spike.

Hunts transport/auth bugs that only bite at the edges, cross-OS:

1. **Large payload round-trips intact** — a ~1 MB string (a realistic
   `vault_write` body) is echoed back with a matching SHA-256, proving the
   Streamable-HTTP path does not truncate or corrupt large bodies.
2. **Auth cannot be bypassed** — missing header, empty `Bearer `, and a wrong
   scheme (`Basic ...`) are ALL rejected; only the correct token succeeds.
3. **Port-in-use fails cleanly** — starting a daemon on an already-bound port
   makes the daemon process exit (it does not hang or silently mis-bind),
   so "a daemon is already running" is a clean, detectable condition.

Run::

    uv run python specs/HIVE-118-phase-c-daemon-model/spike/robustness_spike.py

Exit 0 = every check passed.
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import sys

import _spikelib as lib

PAYLOAD_BYTES = 1_000_000


def _serve(token: str, port: int) -> None:
    mcp = lib.build_server("hive-robustness-daemon", token)

    @mcp.tool
    async def echo(payload: str) -> dict:
        """Return the length + SHA-256 of the received payload (integrity check)."""
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return {"len": len(payload), "sha": digest}

    @mcp.tool
    async def ping() -> str:
        return "pong"

    lib.serve(mcp, port)


async def _try_headers(url: str, headers: dict[str, str] | None) -> str:
    """Attempt a ping with given raw headers. Returns 'ok' or 'rejected'."""
    try:
        async with lib.make_client(url, raw_headers=headers) as client:
            await client.call_tool("ping", {})
        return "ok"
    except Exception:  # noqa: BLE001 — spike classifies any refusal
        return "rejected"


async def _drive(url: str, token: str) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    payload = "x" * PAYLOAD_BYTES
    want_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    async with lib.make_client(url, token) as client:
        r = await client.call_tool("echo", {"payload": payload})
        data = dict(getattr(r, "data", r))
    checks.append((
        f"large payload ({PAYLOAD_BYTES // 1000} KB) round-trips intact",
        data.get("len") == PAYLOAD_BYTES and data.get("sha") == want_sha,
        f"len={data.get('len')}, sha_match={data.get('sha') == want_sha}",
    ))

    variants = {
        "no Authorization header": {},
        "empty Bearer token": {"Authorization": "Bearer "},
        "wrong scheme (Basic)": {"Authorization": "Basic Zm9vOmJhcg=="},
    }
    outcomes = {name: await _try_headers(url, h) for name, h in variants.items()}
    good = await _try_headers(url, {"Authorization": f"Bearer {token}"})
    checks.append((
        "auth cannot be bypassed (bad creds rejected, good accepted)",
        all(o == "rejected" for o in outcomes.values()) and good == "ok",
        f"{outcomes}, correct={good}",
    ))
    return checks


def _check_port_in_use(token: str) -> tuple[str, bool, str]:
    """Occupy a port, then assert a daemon launched on it exits (clean fail)."""
    occupier = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    occupier.bind((lib.HOST, 0))
    occupier.listen()
    port = int(occupier.getsockname()[1])
    proc, child_log = lib.spawn_daemon(__file__, port, {lib.TOKEN_ENV: token})
    try:
        exited = proc.wait(timeout=10) is not None
    except Exception:  # noqa: BLE001 — TimeoutExpired = still running
        exited = False
    finally:
        lib.cleanup(proc, child_log)
        occupier.close()
    return (
        "port-in-use fails cleanly (daemon exits, no hang)",
        exited,
        "daemon exited" if exited else "daemon still running after 10s",
    )


def main() -> int:
    token, port = lib.new_token(), lib.free_port()
    proc, child_log = lib.spawn_daemon(__file__, port, {lib.TOKEN_ENV: token})
    checks: list[tuple[str, bool, str]] = []
    try:
        if not lib.wait_ready(port):
            checks = [("daemon ready", False, lib.log_tail(child_log))]
        else:
            checks = asyncio.run(_drive(lib.url_for(port), token))
            checks.append(_check_port_in_use(token))
    finally:
        code = lib.report("HIVE-118 robustness spike — transport + auth edges", checks)
        lib.cleanup(proc, child_log)
    return code


if __name__ == "__main__":
    import os

    if len(sys.argv) >= 3 and sys.argv[1] == "--serve":
        _serve(os.environ[lib.TOKEN_ENV], int(sys.argv[2]))
    else:
        raise SystemExit(main())
