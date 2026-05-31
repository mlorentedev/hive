"""HIVE-118 Phase C — Linux transport spike.

De-risks the **Linux half** of ADR-011's residual ``[MUST RESOLVE]``: confirm
the loopback Streamable-HTTP + per-daemon bearer-token round-trip that ADR-011
§2 adopts as the daemon transport. The Windows half (port binding under the
named-pipe-free HTTP path, firewall prompts, token-file ACL equivalent) is the
remaining gate and must be run on a Windows host.

What it proves on Linux (each an independent check):

1. **Native transport, cross-process.** A child process is the "daemon": it
   binds ``127.0.0.1:<port>`` with FastMCP's native Streamable-HTTP transport
   (zero custom transport code) and a thin ``fastmcp.Client`` in this process
   round-trips a real ``tools/call`` over HTTP.
2. **Bearer token admits.** A client presenting the correct per-daemon token
   succeeds — the model that replaces the ``0600`` Unix-socket ownership.
3. **No token is refused.** A bare loopback port is reachable by any local
   process, so the unauthenticated request MUST be rejected.
4. **Wrong token is refused.**
5. **Token file is owner-only.** The token is written with mode ``0600`` (the
   POSIX equivalent of the socket-ownership gate ADR-011 folded into the token)
   and the spike asserts the on-disk mode.

The token is handed to the daemon via the environment, never argv, so it does
not leak through ``ps`` — a property the real daemon keeps by reading the
``0600`` token file.

Run::

    uv run python specs/HIVE-118-phase-c-daemon-model/spike/transport_spike.py

Exit 0 = every check passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOST = "127.0.0.1"
PATH = "/mcp"
_TOKEN_ENV = "HIVE_SPIKE_TOKEN"


# ── daemon (child process) ───────────────────────────────────────────────


def _serve(token: str, port: int) -> None:
    """Run the spike 'daemon': FastMCP over loopback Streamable-HTTP + token."""
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    verifier = StaticTokenVerifier({token: {"client_id": "hive-spike", "scopes": []}})
    mcp: FastMCP = FastMCP("hive-spike-daemon", auth=verifier)

    @mcp.tool
    def ping() -> str:
        """Minimal round-trip probe — returns ``pong``."""
        return "pong"

    mcp.run(transport="http", host=HOST, port=port, path=PATH, show_banner=False)


# ── client (this process) ────────────────────────────────────────────────


async def _round_trip(url: str, token: str | None) -> tuple[str, str]:
    """Attempt one ``ping`` call. Returns (outcome, detail).

    outcome ∈ {"ok", "rejected", "error"} — "rejected" means the daemon
    refused the request (the expected path for a missing/wrong token).
    """
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = StreamableHttpTransport(url, headers=headers)
    try:
        async with Client(transport) as client:
            result = await client.call_tool("ping", {})
            return "ok", str(getattr(result, "data", result))
    except Exception as exc:  # noqa: BLE001 — spike classifies any refusal
        return "rejected", f"{type(exc).__name__}: {exc}"[:120]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def _wait_ready(port: int, deadline_s: float = 15.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        with contextlib.suppress(OSError), socket.create_connection(
            (HOST, port), timeout=0.5,
        ):
            return True
        time.sleep(0.1)
    return False


def _write_token_file(token: str) -> Path:
    """Write the token with mode 0600 (owner-only), mirroring the daemon."""
    fd, name = tempfile.mkstemp(prefix="hive-spike-", suffix=".token")
    os.close(fd)
    path = Path(name)
    path.write_text(token, encoding="utf-8")
    path.chmod(0o600)
    return path


def main() -> int:
    token = secrets.token_urlsafe(32)
    port = _free_port()
    url = f"http://{HOST}:{port}{PATH}"
    checks: list[tuple[str, bool, str]] = []

    # Check 5: token-file perms (independent of the server).
    token_file = _write_token_file(token)
    mode = stat.S_IMODE(token_file.stat().st_mode)
    checks.append(("token file is mode 0600", mode == 0o600, f"actual={oct(mode)}"))

    env = {**os.environ, _TOKEN_ENV: token}
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, spike only
        [sys.executable, __file__, "--serve", str(port)],
        env=env,
    )
    try:
        if not _wait_ready(port):
            checks.append(("daemon bound 127.0.0.1 loopback", False, "timeout"))
            return _report(checks, proc, token_file)
        checks.append(("daemon bound 127.0.0.1 loopback", True, f"port={port}"))

        t0 = time.monotonic()
        ok_outcome, ok_detail = asyncio.run(_round_trip(url, token))
        rtt_ms = (time.monotonic() - t0) * 1000
        checks.append((
            "correct token round-trips tools/call",
            ok_outcome == "ok" and "pong" in ok_detail,
            f"{ok_outcome} ({rtt_ms:.0f} ms): {ok_detail}",
        ))

        none_outcome, none_detail = asyncio.run(_round_trip(url, None))
        checks.append((
            "missing token is rejected",
            none_outcome == "rejected",
            f"{none_outcome}: {none_detail}",
        ))

        wrong_outcome, wrong_detail = asyncio.run(
            _round_trip(url, "not-the-real-token"),
        )
        checks.append((
            "wrong token is rejected",
            wrong_outcome == "rejected",
            f"{wrong_outcome}: {wrong_detail}",
        ))
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        token_file.unlink(missing_ok=True)

    return _report(checks, proc, token_file)


def _report(
    checks: list[tuple[str, bool, str]],
    proc: subprocess.Popen[bytes],
    token_file: Path,
) -> int:
    with contextlib.suppress(Exception):
        if proc.poll() is None:
            proc.kill()
    token_file.unlink(missing_ok=True)

    print("\nHIVE-118 Linux transport spike — loopback Streamable-HTTP + token\n")
    width = max(len(name) for name, _, _ in checks)
    all_ok = True
    for name, ok, detail in checks:
        all_ok &= ok
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name.ljust(width)}  {detail}")
    print(f"\n{'ALL PASS' if all_ok else 'FAILED'} — "
          f"{sum(ok for _, ok, _ in checks)}/{len(checks)} checks\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--serve":
        _serve(os.environ[_TOKEN_ENV], int(sys.argv[2]))
    else:
        raise SystemExit(main())
