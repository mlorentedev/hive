"""HIVE-118 Phase C — cross-OS transport spike.

Confirms ADR-011 §2's transport choice — **loopback Streamable-HTTP + a
per-daemon bearer token** — by exercising the real round-trip end to end. This
is the gate that must pass on **Linux and Windows** before ADR-011 is accepted
and ``tasks.md`` is frozen.

What it proves (each an independent check):

1. **Native transport, cross-process.** A child process is the "daemon": it
   binds ``127.0.0.1:<port>`` with FastMCP's native Streamable-HTTP transport
   (zero custom transport code — no named-pipe handle terrain) and a thin
   ``fastmcp.Client`` in this process round-trips a real ``tools/call``.
2. **Bearer token admits.** The correct per-daemon token succeeds — the model
   that replaces ``0600`` Unix-socket ownership.
3. **No token is refused** (HTTP 401). A bare loopback port is reachable by any
   local process, so the unauthenticated request MUST be rejected.
4. **Wrong token is refused** (HTTP 401).
5. **Token file is owner-only.** POSIX: mode ``0600``. Windows: an inheritance-
   stripped ACL granting only the current user (``icacls``). The raw ACL is
   printed so a human can confirm it on the Windows run.

The token is handed to the daemon via the environment, never argv, so it does
not leak through ``ps`` / Task Manager — the real daemon keeps this by reading
the owner-only token file.

Run (same command on both OSes)::

    uv run python specs/HIVE-118-phase-c-daemon-model/spike/transport_spike.py

Exit ``0`` = every check passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import getpass
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
_IS_WINDOWS = os.name == "nt"


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

    outcome ∈ {"ok", "rejected"} — "rejected" means the daemon refused the
    request (the expected path for a missing/wrong token).
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
        return "rejected", f"{type(exc).__name__}: {exc}"[:100]


# ── owner-only token file (the OS-specific half) ─────────────────────────


def _current_user() -> str:
    return os.environ.get("USERNAME") or getpass.getuser()


def _secure_token_file(token: str) -> Path:
    """Write the token, then lock it to the current user only."""
    fd, name = tempfile.mkstemp(prefix="hive-spike-", suffix=".token")
    os.close(fd)
    path = Path(name)
    path.write_text(token, encoding="utf-8")
    if _IS_WINDOWS:
        # Strip inherited ACEs, grant full control to ONLY the current user.
        subprocess.run(  # noqa: S603,S607 — fixed args, spike only
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{_current_user()}:(F)"],
            check=False, capture_output=True, text=True,
        )
    else:
        path.chmod(0o600)
    return path


# Broad principals that must NOT have an ACE on an owner-only file. English +
# Spanish names (the two locales most likely on the validating machine).
_BROAD_PRINCIPALS = (
    "everyone", "todos", "users", "usuarios",
    "authenticated users", "usuarios autentificados",
)


def _verify_owner_only(path: Path) -> tuple[bool, str]:
    """POSIX: assert mode 0600. Windows: assert no broad principal in the ACL."""
    if not _IS_WINDOWS:
        mode = stat.S_IMODE(path.stat().st_mode)
        return mode == 0o600, f"mode={oct(mode)}"
    proc = subprocess.run(  # noqa: S603,S607 — fixed args, spike only
        ["icacls", str(path)], check=False, capture_output=True, text=True,
    )
    out = proc.stdout
    leaked = [p for p in _BROAD_PRINCIPALS if p in out.lower()]
    # Print the raw ACL so the human running the Windows spike can confirm it.
    print("  icacls:\n" + "\n".join(f"    {ln}" for ln in out.splitlines() if ln.strip()))
    return not leaked, f"broad_principals={leaked or 'none'}"


# ── driver ───────────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def _wait_ready(port: int, deadline_s: float = 20.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        with contextlib.suppress(OSError), socket.create_connection(
            (HOST, port), timeout=0.5,
        ):
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    token = secrets.token_urlsafe(32)
    port = _free_port()
    url = f"http://{HOST}:{port}{PATH}"
    checks: list[tuple[str, bool, str]] = []

    # Check 5: owner-only token file (independent of the server).
    token_file = _secure_token_file(token)
    ok, detail = _verify_owner_only(token_file)
    checks.append(("token file is owner-only (0600 / ACL)", ok, detail))

    # Capture child output so a failed start on Windows is diagnosable.
    child_log = Path(tempfile.gettempdir()) / f"hive-spike-daemon-{port}.log"
    env = {**os.environ, _TOKEN_ENV: token}
    with child_log.open("wb") as log:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, spike only
            [sys.executable, __file__, "--serve", str(port)],
            env=env, stdout=log, stderr=log,
        )
        try:
            if not _wait_ready(port):
                tail = child_log.read_text(errors="replace")[-800:]
                checks.append((
                    "daemon bound 127.0.0.1 loopback", False,
                    f"timeout; daemon log tail:\n{tail}",
                ))
                return _report(checks, proc, token_file, child_log)
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

    return _report(checks, proc, token_file, child_log)


def _report(
    checks: list[tuple[str, bool, str]],
    proc: subprocess.Popen[bytes],
    token_file: Path,
    child_log: Path,
) -> int:
    with contextlib.suppress(Exception):
        if proc.poll() is None:
            proc.kill()
    token_file.unlink(missing_ok=True)
    child_log.unlink(missing_ok=True)

    label = "Windows" if _IS_WINDOWS else "Linux/POSIX"
    print(f"\nHIVE-118 transport spike ({label}) — loopback Streamable-HTTP + token\n")
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
