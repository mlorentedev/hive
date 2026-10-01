"""Stable local daemon endpoint primitives (ADR-022)."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import sys
from ctypes import wintypes
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
MCP_PATH = "/mcp"
DAEMON_PORT_ENV = "HIVE_DAEMON_PORT"
PRIVATE_PORT_MIN = 49152
PRIVATE_PORT_MAX = 65535
_PRIVATE_PORT_COUNT = PRIVATE_PORT_MAX - PRIVATE_PORT_MIN + 1
_PORT_NAMESPACE = b"hive-daemon-port-v1\0"
_SID_RE = re.compile(r"^S-\d+(?:-\d+)+$")
_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("user", _SidAndAttributes)]


def _last_windows_error() -> OSError:
    win_error = getattr(ctypes, "WinError")  # noqa: B009
    last_error = getattr(ctypes, "get_last_error")  # noqa: B009
    error = win_error(last_error())
    if not isinstance(error, OSError):
        raise RuntimeError("Windows API did not return an OS error")
    return error


def canonical_identity(
    *,
    platform: str,
    uid: int | None = None,
    sid: str = "",
) -> str:
    """Return ADR-022's canonical identity string."""
    if platform == "win32":
        if not _SID_RE.fullmatch(sid):
            raise RuntimeError("could not resolve a canonical Windows SID")
        return f"windows:{sid}"
    if uid is None or uid < 0:
        raise RuntimeError("could not resolve a non-negative POSIX UID")
    return f"posix:{uid}"


def _windows_sid() -> str:
    """Read the current process token's numeric SID through the Windows API."""
    win_dll = getattr(ctypes, "WinDLL")  # noqa: B009
    advapi32: ctypes.CDLL = win_dll("advapi32", use_last_error=True)
    kernel32: ctypes.CDLL = win_dll("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise _last_windows_error()
    try:
        return _sid_from_token(advapi32, kernel32, token)
    finally:
        kernel32.CloseHandle(token)


def _sid_from_token(
    advapi32: object,
    kernel32: object,
    token: wintypes.HANDLE,
) -> str:
    advapi32.GetTokenInformation.argtypes = [  # type: ignore[attr-defined]
        wintypes.HANDLE,
        ctypes.c_uint,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL  # type: ignore[attr-defined]
    needed = wintypes.DWORD()
    advapi32.GetTokenInformation(  # type: ignore[attr-defined]
        token,
        _TOKEN_USER_CLASS,
        None,
        0,
        ctypes.byref(needed),
    )
    if needed.value == 0:
        raise _last_windows_error()
    buffer = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetTokenInformation(  # type: ignore[attr-defined]
        token,
        _TOKEN_USER_CLASS,
        buffer,
        needed.value,
        ctypes.byref(needed),
    ):
        raise _last_windows_error()
    token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
    return _sid_to_string(advapi32, kernel32, token_user.user.sid)


def _sid_to_string(advapi32: object, kernel32: object, sid: int) -> str:
    advapi32.ConvertSidToStringSidW.argtypes = [  # type: ignore[attr-defined]
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL  # type: ignore[attr-defined]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]  # type: ignore[attr-defined]
    kernel32.LocalFree.restype = ctypes.c_void_p  # type: ignore[attr-defined]
    value = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(  # type: ignore[attr-defined]
        sid,
        ctypes.byref(value),
    ):
        raise _last_windows_error()
    try:
        result = value.value or ""
    finally:
        kernel32.LocalFree(value)  # type: ignore[attr-defined]
    if not _SID_RE.fullmatch(result):
        raise RuntimeError("Windows returned an invalid process SID")
    return result


def current_user_identity() -> str:
    """Resolve the current user without names or machine-specific aliases."""
    if sys.platform == "win32":
        return canonical_identity(platform=sys.platform, sid=_windows_sid())
    if not hasattr(os, "getuid"):
        raise RuntimeError(f"unsupported platform for daemon identity: {sys.platform}")
    return canonical_identity(platform=sys.platform, uid=os.getuid())


def port_for_identity(identity: str) -> int:
    """Map a canonical identity to ADR-022's versioned private-range port."""
    try:
        encoded = identity.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("daemon identity must be ASCII") from exc
    digest = hashlib.sha256(_PORT_NAMESPACE + encoded).digest()
    offset = int.from_bytes(digest[:2], byteorder="big") % _PRIVATE_PORT_COUNT
    return PRIVATE_PORT_MIN + offset


def configured_daemon_port(*, identity: str = "") -> int:
    """Return the explicit port override or the deterministic per-user default."""
    raw = os.environ.get(DAEMON_PORT_ENV, "").strip()
    if not raw:
        return port_for_identity(identity or current_user_identity())
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{DAEMON_PORT_ENV} must be an integer port, got {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{DAEMON_PORT_ENV} must be between 1 and 65535, got {port}")
    return port


def daemon_state_dir() -> Path:
    """State directory shared by the daemon and the stdlib-only client."""
    default_db = Path.home() / ".local" / "share" / "hive" / "worker.db"
    return Path(os.environ.get("HIVE_DB_PATH", str(default_db))).parent


def token_file_path() -> Path:
    return daemon_state_dir() / "daemon.token"


def port_file_path() -> Path:
    return daemon_state_dir() / "daemon.port"


def lock_file_path() -> Path:
    return daemon_state_dir() / "daemon.lock"


def identity_key_path() -> Path:
    return daemon_state_dir() / "daemon.key"


def identity_cert_path() -> Path:
    return daemon_state_dir() / "daemon.crt"


def identity_state_path() -> Path:
    """Marks that an identity was generated, so missing material is not a migration."""
    return daemon_state_dir() / "identity.state"
