"""``hive serve`` — the Phase C single-owner daemon (ADR-011).

One long-lived process owns the vault git working tree and all SQLite trackers
and serves thin clients over loopback Streamable-HTTP gated by a per-daemon
bearer token. The transport + owner-only token model is the one validated
cross-OS by ``specs/HIVE-118-phase-c-daemon-model/spike/transport_spike.py``.

This first slice provides the entrypoint: write an owner-only token + port
state file, then run ``create_server()`` over HTTP with a token verifier. The
resilience/observability pillar (ADR-011 §4) lands in later slices.
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import filelock

from hive.config import settings

if TYPE_CHECKING:
    from fastmcp.server.auth import AuthProvider

DEFAULT_HOST = "127.0.0.1"
MCP_PATH = "/mcp"
TOKEN_FILENAME = "daemon.token"
PORT_FILENAME = "daemon.port"
LOCK_FILENAME = "daemon.lock"

_log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform == "win32"


def daemon_state_dir() -> Path:
    """Directory holding the token + port state files (beside the SQLite DBs)."""
    return Path(settings.db_path).parent


def token_file_path() -> Path:
    return daemon_state_dir() / TOKEN_FILENAME


def port_file_path() -> Path:
    return daemon_state_dir() / PORT_FILENAME


def lock_file_path() -> Path:
    return daemon_state_dir() / LOCK_FILENAME


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((DEFAULT_HOST, 0))
        return int(s.getsockname()[1])


def write_owner_only(path: Path, content: str) -> None:
    """Write *content* to *path* so only the current user can read it.

    POSIX: mode ``0600``. Windows: strip inherited ACEs and grant the current
    user only via ``icacls`` (a bare ``chmod`` cannot express an owner-only ACL
    on NTFS). Both forms are validated by the transport spike on each OS.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if os.name == "nt":
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        subprocess.run(  # noqa: S603,S607 — fixed args, owner derived from env
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(F)"],
            check=False, capture_output=True, text=True,
        )
    else:
        path.chmod(0o600)


def _token_verifier(token: str) -> AuthProvider:
    """A static bearer-token verifier admitting exactly *token* (ADR-011 §2)."""
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    return StaticTokenVerifier({token: {"client_id": "hive-daemon", "scopes": []}})


# ── Single-owner guard + startup self-heal (ADR-011 §1, §4) ──────────────


def _acquire_singleton_lock() -> filelock.BaseFileLock | None:
    """Non-blocking acquire of the per-state-dir singleton ``daemon.lock``.

    Returns the HELD lock (the caller keeps the reference alive for the whole
    process lifetime) or ``None`` when another daemon already owns this state
    dir. This closes the auto-port single-owner gap: two ``hive serve``
    invocations pick different free ports, so the OS port-in-use guard never
    fires, yet they collide here on one advisory lock. The lock is kernel-owned
    by fd, so a crashed daemon's lock frees automatically for the supervised
    restart — no stale lock blocks recovery.
    """
    lock_path = lock_file_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(lock_path))
    try:
        lock.acquire(timeout=0)
    except filelock.Timeout:
        return None
    return lock


def _pid_alive(pid: int) -> bool:
    """Best-effort: is *pid* a currently-live process?"""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        try:
            import psutil
        except ImportError:
            return False
        return psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    return True


def _index_lock_is_live(lock_path: Path) -> bool:
    """True only when the lock names a parseable, currently-alive PID.

    A real git ``index.lock`` holds the in-progress index (no PID) or is empty,
    so this returns ``False`` for it — correctly treating a leftover lock as
    stale. It returns ``True`` only in the narrow, defensive case where a tool
    wrote a live PID, sparing a live external committer mid-commit.
    """
    try:
        lines = lock_path.read_text(encoding="utf-8").strip().splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    if not lines:
        return False
    try:
        pid = int(lines[0].strip())
    except ValueError:
        return False
    return _pid_alive(pid)


def _startup_self_heal(vault: Path) -> None:
    """Clear a stale ``.git/index.lock`` left by a prior unclean exit.

    Safe ONLY because the caller already holds the singleton ``daemon.lock``:
    no sibling hive daemon owns this tree, and we have issued no git op of our
    own yet, so any pre-existing ``index.lock`` is from a dead prior run. A lock
    naming a live PID is spared. Stale 0-byte WAL files are handled separately
    by ``create_server`` via ``_clean_stale_wal_files``; non-empty WAL is never
    touched (SQLite recovers it).
    """
    lock_path = vault / ".git" / "index.lock"
    if not lock_path.exists():
        return
    if _index_lock_is_live(lock_path):
        _log.info(
            "hive.daemon.self_heal index.lock held by a live process; leaving %s",
            lock_path,
        )
        return
    try:
        lock_path.unlink()
    except OSError as exc:
        _log.warning("hive.daemon.self_heal could not clear index.lock: %s", exc)
        return
    _log.info("hive.daemon.self_heal cleared stale index.lock at %s", lock_path)


def run_serve(host: str = DEFAULT_HOST, port: int = 0) -> None:
    """Run hive as a single-owner daemon over loopback Streamable-HTTP + token.

    Generates a per-daemon token, publishes it (owner-only) and the chosen port
    to the state dir, then serves the real ``create_server()`` instance. A
    ``port`` of 0 picks a free loopback port.
    """
    from hive.server import create_server

    # Single-owner guard (ADR-011 §1). A second daemon — even on a different
    # auto-port — collides here and declines cleanly rather than double-owning
    # the SQLite DBs + git tree. Exit 0 keeps a no-op start from looping under
    # systemd `Restart=on-failure`; the WARNING + stderr line carry the "why".
    singleton = _acquire_singleton_lock()
    if singleton is None:
        _log.warning(
            "hive.daemon.singleton.declined state_dir=%s another daemon owns it",
            daemon_state_dir(),
        )
        print(
            "hive: another daemon already owns this state dir; declining to start",
            file=sys.stderr,
        )
        return

    try:
        # Single ownership is now proven, so clearing a prior crash's stale
        # locks cannot race a live sibling daemon (ADR-011 startup self-heal).
        _startup_self_heal(settings.vault_path)

        resolved_port = port or _free_port()
        token = secrets.token_urlsafe(32)
        # Each startup republishes a fresh token + port, overwriting any state a
        # prior daemon left behind. We deliberately do NOT clean these up on stop:
        # uvicorn's SIGTERM handling exits the process via the signal (rc -15),
        # bypassing `finally`/`atexit`, and SIGTERM is exactly how systemd / kill
        # stop the daemon. Stale state is benign — the client's TCP liveness probe
        # falls back (`daemon_unreachable`) and a restart overwrites it.
        write_owner_only(token_file_path(), token)
        write_owner_only(port_file_path(), str(resolved_port))

        server = create_server(auth=_token_verifier(token))
        server.run(
            transport="http", host=host, port=resolved_port,
            path=MCP_PATH, show_banner=False,
        )
    finally:
        singleton.release()
