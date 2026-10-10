"""Owner-only file publication shared by the token and the TLS identity.

Every daemon secret follows one sequence (ADR-022): write a same-directory
candidate, enforce and verify owner-only permissions or ACLs, then publish it
with ``os.replace``. A failure at any step removes the candidate and raises, so
a reader never observes a partial or over-permissive file.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path

from hive._credential import _verify_owner_only
from hive._endpoint import _last_windows_error, current_user_identity

# SetNamedSecurityInfoW: SE_FILE_OBJECT, and the owner plus a protected DACL.
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x1
_DACL_SECURITY_INFORMATION = 0x4
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SDDL_REVISION_1 = 1


def _windows_set_owner_only(path: Path, sid: str) -> None:
    """Replace the owner and the whole DACL with one ACE for *sid*.

    One call sets the full descriptor, the ``chmod 0o600`` of Windows. Editing the
    existing DACL with icacls left two gaps (#484): an elevated token creates
    files owned by BUILTIN\\Administrators, and ``/inheritance:r`` drops only
    inherited ACEs, so the explicit ones a 0o700 directory hands its files survived.
    """
    win_dll = getattr(ctypes, "WinDLL")  # noqa: B009
    advapi: ctypes.CDLL = win_dll("advapi32", use_last_error=True)
    kernel: ctypes.CDLL = win_dll("kernel32", use_last_error=True)
    pointer = ctypes.POINTER(ctypes.c_void_p)
    bool_pointer = ctypes.POINTER(wintypes.BOOL)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        pointer,
        ctypes.c_void_p,
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.GetSecurityDescriptorOwner.argtypes = [ctypes.c_void_p, pointer, bool_pointer]
    advapi.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    advapi.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        bool_pointer,
        pointer,
        bool_pointer,
    ]
    advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p

    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{sid}D:P(A;;FA;;;{sid})", _SDDL_REVISION_1, ctypes.byref(descriptor), None
    ):
        raise _last_windows_error()
    try:
        owner, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        if not advapi.GetSecurityDescriptorOwner(
            descriptor, ctypes.byref(owner), ctypes.byref(defaulted)
        ):
            raise _last_windows_error()
        if not advapi.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        ):
            raise _last_windows_error()
        status = advapi.SetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION
            | _DACL_SECURITY_INFORMATION
            | _PROTECTED_DACL_SECURITY_INFORMATION,
            owner,
            None,
            dacl,
            None,
        )
        if status != 0:
            raise OSError(status, "SetNamedSecurityInfoW failed")
    finally:
        kernel.LocalFree(descriptor)


def enforce_owner_only(path: Path) -> None:
    """Apply owner-only permissions, raising when the OS cannot enforce them."""
    if os.name == "nt":
        sid = current_user_identity().split(":", 1)[1]
        try:
            _windows_set_owner_only(path, sid)
        except OSError as exc:  # permission enforcement must fail closed
            msg = f"could not enforce owner-only daemon credential ACL: {exc}"
            raise RuntimeError(msg) from exc
        return
    path.chmod(0o600)


def write_owner_only_atomic(path: Path, data: bytes) -> None:
    """Publish *data* at *path* only once it is verified owner-only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        enforce_owner_only(temp)
        if not _verify_owner_only(temp):
            raise RuntimeError(f"{path.name} candidate is not owner-only")
        os.replace(temp, path)
    finally:
        with contextlib.suppress(OSError):
            temp.unlink()
