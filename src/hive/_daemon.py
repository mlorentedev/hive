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

import asyncio
import contextlib
import importlib
import importlib.metadata as metadata
import logging
import os
import secrets
import socket
import sys
from typing import TYPE_CHECKING

import filelock

from hive._credential import _read_token
from hive._endpoint import (
    DEFAULT_HOST,
    MCP_PATH,
    configured_daemon_port,
    daemon_state_dir,
    identity_cert_path,
    identity_key_path,
    identity_state_path,
    lock_file_path,
    port_file_path,
    token_file_path,
)
from hive._owner_only import enforce_owner_only as _enforce_owner_only
from hive._owner_only import write_owner_only_atomic
from hive.config import settings

if TYPE_CHECKING:
    from pathlib import Path

    import uvicorn
    from fastmcp.server.auth import AuthProvider

    from hive._identity import Identity

PACKAGE_NAME = "hive-vault"  # PyPI distribution name (the `hive` name was taken)
NOT_FOUND = "<not-found>"  # _current_version sentinel for the upgrade swap window
# EX_TEMPFAIL: a drift-triggered clean stop exits non-zero so a `Restart=on-failure`
# supervisor relaunches into the new code (decline + signal stops exit 0 instead).
EXIT_RESTART_ON_UPGRADE = 75
# Bound the graceful-shutdown wait: MCP streamable-http holds connections open, so
# an unbounded wait could hang the restart. The real in-flight drain is unreachable
# anyway (the spike found the handler is cancelled), so idempotency + auto-reconnect
# cover the cut call; this only caps how long we wait before letting serve() return.
GRACEFUL_SHUTDOWN_S = 2

_log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform == "win32"


def _create_token(path: Path) -> str:
    token = secrets.token_urlsafe(32)
    write_owner_only_atomic(path, token.encode("ascii"))
    return token


def load_or_create_token() -> str:
    """Reuse a valid credential, or atomically create it on first install."""
    path = token_file_path()
    if path.exists():
        return _read_token(path)
    return _create_token(path)


def prepare_daemon_credentials() -> tuple[str, Identity]:
    """Load the TLS identity and bearer, creating them on the first TLS start.

    Before the identity existed the daemon served plaintext HTTP (#453), so any
    token found then is presumed captured and is replaced, never reused. The
    identity record is written only after that rotation, so an interrupted
    first start rotates again instead of leaving the old token live
    (ADR-022 Amendment 1).
    """
    from hive._identity import load_or_create_identity

    token_path = token_file_path()
    rotated: list[str] = []

    def rotate_pre_tls_token() -> None:
        rotated.append(_create_token(token_path))

    identity = load_or_create_identity(
        identity_key_path(),
        identity_cert_path(),
        identity_state_path(),
        before_record=rotate_pre_tls_token,
    )
    # Record present but token gone: deletion is not exposure, so a fresh
    # token is created as on a first install.
    token = rotated[0] if rotated else load_or_create_token()
    return token, identity


def write_owner_only(path: Path, content: str) -> None:
    """Write non-secret daemon metadata with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _enforce_owner_only(path)


def _port_available(host: str, port: int) -> bool:
    """Return whether the configured rendezvous can be bound right now."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            if not IS_WINDOWS:
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            candidate.bind((host, port))
    except OSError:
        return False
    return True


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
    """Clear a stale ``.git/index.lock``, then *report* what is uncommitted.

    Two steps that look alike and are not. Clearing the lock is safe ONLY
    because the caller already holds the singleton ``daemon.lock`` (see
    :func:`_clear_stale_index_lock`). The report needs no such argument and
    takes no lock: **startup never commits, in either regime** (ADR-018 §3).

    An earlier draft had daemon-side recovery commit under ``daemon.lock``.
    Review overturned it: that lock excludes sibling *hives*, not a *human*
    with a half-edited note open in Obsidian, so recovery could not tell its
    own orphaned write from someone's work in progress. Distinguishing them
    needs provenance, and after a crash the queue that knew hive's paths is
    gone. Reporting is the only resolution that needs no provenance at all —
    a path is safe to report whoever wrote it — and it leaves daemon and
    non-daemon startup behaving identically, with nothing for a later reader
    to mistake for a bug.
    """
    _clear_stale_index_lock(vault)
    _report_uncommitted_paths(vault)


def _clear_stale_index_lock(vault: Path) -> None:
    """Remove a ``.git/index.lock`` left behind by a prior unclean exit.

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


def _report_uncommitted_paths(vault: Path) -> None:
    """Log how many vault paths await a commit, and how long the oldest has.

    Count and age only — never the path list. A vault mid-reorganisation can
    carry hundreds of dirty files, and a boot log that enumerates them buys
    nothing the two numbers do not already say. ``vault_health`` is where an
    operator goes for the live figure (AC11); this is the boot-time snapshot.

    Nothing here may block or fail a start, so the import is local (keeping
    ``_daemon``'s import graph thin, as ``flush_paths`` does with
    ``_deadline``) and every failure degrades to a warning.
    """
    try:
        from hive._helpers import uncommitted_summary

        count, oldest_age_s = uncommitted_summary(vault)
    except Exception as exc:  # noqa: BLE001
        _log.warning("hive.daemon.self_heal uncommitted-path report failed: %s", exc)
        return
    age_text = "null" if oldest_age_s is None else f"{oldest_age_s:.1f}"
    _log.info(
        "hive.daemon.self_heal uncommitted_count=%s oldest_age_s=%s vault=%s "
        "(reported, never committed — ADR-018 §3)",
        "null" if count is None else count,
        age_text,
        vault,
    )


# ── Restart-on-upgrade: drift detection + cooperative stop (ADR-011 §1) ──


def _current_version(package: str = PACKAGE_NAME) -> str:
    """The installed version of *package*, or ``NOT_FOUND`` if unreadable.

    ``importlib.metadata`` reads the on-disk ``*.dist-info`` live, so a
    long-lived process sees the NEW version the instant ``uv tool upgrade``
    swaps it — no venv watching, stdlib only. ``invalidate_caches`` guards a
    stale finder cache on coarse-mtime filesystems. The brief swap window (old
    dist-info gone, new one not yet written) surfaces as ``PackageNotFoundError``
    → the sentinel, which ``_upgrade_detected`` treats as "not drift".
    """
    try:
        importlib.invalidate_caches()
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return NOT_FOUND


def _upgrade_detected(boot: str, current: str) -> bool:
    """True iff the installed version drifted from the boot snapshot in a way
    that warrants a supervised restart.

    Contract (pinned by ``test_upgrade_detected_predicate``):
    * A resolvable version DIFFERENT from ``boot`` is drift — an upgrade and a
      rollback both ship code the running process is not executing.
    * The ``NOT_FOUND`` sentinel (transient swap window, or never installed via
      metadata) is NEVER drift — a momentary unreadable version must not bounce
      a healthy daemon.
    """
    if NOT_FOUND in (boot, current):
        return False
    return current != boot


async def _watch_for_upgrade(
    uv_server: uvicorn.Server,
    *,
    boot: str,
    poll_s: float,
    package: str = PACKAGE_NAME,
) -> bool:
    """Poll the installed version; on drift, cooperatively stop *uv_server*.

    Returns True iff an in-place upgrade triggered the stop. Setting
    ``should_exit`` (vs sending a signal) lets uvicorn return from ``serve()``
    cleanly — the spike proved a signal cuts the in-flight handler (rc -15). A
    ``should_exit`` already set when we wake means an external stop
    (``systemctl stop``) won the race → report 'not drift' so ``run_serve``
    exits 0 and the supervisor does not restart a daemon asked to stop.
    """
    while not uv_server.should_exit:
        await asyncio.sleep(poll_s)
        if uv_server.should_exit:
            return False
        current = _current_version(package)
        if _upgrade_detected(boot, current):
            _log.warning(
                "hive.daemon.upgrade.detected boot=%s installed=%s; "
                "stopping for supervised restart",
                boot,
                current,
            )
            uv_server.should_exit = True
            return True
    return False


async def _serve_until_drift_or_signal(uv_server: uvicorn.Server) -> bool:
    """Run *uv_server* alongside a drift watcher; True iff an upgrade stopped it."""
    watcher = asyncio.create_task(
        _watch_for_upgrade(
            uv_server,
            boot=_current_version(),
            poll_s=settings.upgrade_poll_s,
        ),
    )
    try:
        await uv_server.serve()
    finally:
        drifted = (
            watcher.done()
            and not watcher.cancelled()
            and watcher.exception() is None
            and bool(watcher.result())
        )
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
    return drifted


def _serve_owned(host: str, port: int, token: str, identity: Identity) -> bool:
    """Own the ``uvicorn.Server`` so the drift watcher can clean-stop it.

    Built from the PUBLIC ``mcp.http_app()`` (the spike-validated seam) rather
    than ``mcp.run(transport="http")``, whose internal signal-only stop cuts
    in-flight calls. uvicorn's default signal handlers stay installed so
    ``systemctl stop`` (SIGTERM) still stops it gracefully. Returns True iff an
    in-place upgrade caused the stop.
    """
    import uvicorn

    from hive.server import create_server

    server = create_server(auth=_token_verifier(token))
    app = server.http_app(path=MCP_PATH, transport="http")
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        lifespan="on",
        log_level="warning",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
    )
    return asyncio.run(_serve_until_drift_or_signal(uvicorn.Server(config)))


def run_serve(host: str = DEFAULT_HOST, port: int = 0) -> int:
    """Run hive as a single-owner daemon over loopback Streamable-HTTP + token.

    Reuses one persistent owner-only token and binds the deterministic per-user
    port unless ``port`` explicitly overrides it.

    Returns a process exit code: ``EXIT_RESTART_ON_UPGRADE`` when an in-place
    package upgrade triggered a clean stop (so a ``Restart=on-failure``
    supervisor relaunches into the new code), else ``0`` — a signal-driven stop
    (``systemctl stop``) or a singleton-decline no-op.
    """
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
        return 0

    try:
        # Single ownership is now proven, so clearing a prior crash's stale
        # locks cannot race a live sibling daemon (ADR-011 startup self-heal).
        _startup_self_heal(settings.vault_path)

        resolved_port = port or configured_daemon_port()
        if not _port_available(host, resolved_port):
            print(
                f"hive: stable daemon port {resolved_port} is already in use; "
                "stop the conflicting process or set HIVE_DAEMON_PORT",
                file=sys.stderr,
            )
            return 1
        token, identity = prepare_daemon_credentials()
        # Retained temporarily as diagnostic migration metadata. Clients derive
        # the endpoint independently and never discover it through this file.
        write_owner_only(port_file_path(), str(resolved_port))

        drifted = _serve_owned(host, resolved_port, token, identity)
        return EXIT_RESTART_ON_UPGRADE if drifted else 0
    finally:
        singleton.release()
