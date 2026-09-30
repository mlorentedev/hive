"""Lightweight, fail-closed reads of the local daemon credential."""

from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes
from typing import TYPE_CHECKING

from hive._endpoint import current_user_identity

if TYPE_CHECKING:
    from pathlib import Path

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,}$")


class _Acl(ctypes.Structure):
    _fields_ = [
        ("revision", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte),
        ("size", wintypes.WORD),
        ("ace_count", wintypes.WORD),
        ("reserved2", wintypes.WORD),
    ]


def _windows_security_api() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.POINTER(ctypes.c_void_p)
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        pointer,
        pointer,
        pointer,
        pointer,
        pointer,
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, pointer]
    advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, pointer]
    advapi.GetAce.restype = wintypes.BOOL
    advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    advapi.EqualSid.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return advapi, kernel


def _windows_owner_only(path: Path, sid: str) -> bool:
    advapi, kernel = _windows_security_api()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    user = ctypes.c_void_p()
    try:
        status = advapi.GetNamedSecurityInfoW(
            str(path),
            1,
            5,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if status != 0 or not owner.value or not dacl.value:
            return False
        if not advapi.ConvertStringSidToSidW(sid, ctypes.byref(user)):
            return False
        if ctypes.cast(dacl, ctypes.POINTER(_Acl)).contents.ace_count != 1:
            return False
        ace = ctypes.c_void_p()
        if not advapi.GetAce(dacl, 0, ctypes.byref(ace)) or not ace.value:
            return False
        ace_type = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte)).contents.value
        return bool(
            ace_type == 0
            and advapi.EqualSid(owner, user)
            and advapi.EqualSid(ctypes.c_void_p(ace.value + 8), user)
        )
    finally:
        if user.value:
            kernel.LocalFree(user)
        if descriptor.value:
            kernel.LocalFree(descriptor)


def _verify_owner_only(path: Path) -> bool:
    """Verify that only the current user can read the daemon credential."""
    if os.name != "nt":
        stat_result = path.stat()
        getuid = getattr(os, "getuid", None)
        return (
            callable(getuid) and stat_result.st_uid == getuid() and stat_result.st_mode & 0o077 == 0
        )
    sid = current_user_identity().split(":", 1)[1]
    return _windows_owner_only(path, sid)


def _read_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"could not read daemon credential at {path}: {exc}") from exc
    if not _TOKEN_RE.fullmatch(token):
        raise RuntimeError(f"invalid daemon credential at {path}; reinstall or rotate it")
    if not _verify_owner_only(path):
        raise RuntimeError(f"daemon credential at {path} is not owner-only")
    return token
