"""Temporary #484 diagnostic: print what the owner-only verify sees on Windows."""

import ctypes
import subprocess
import tempfile
from pathlib import Path

from hive._credential import _Acl, _windows_security_api
from hive._endpoint import current_user_identity
from hive._owner_only import enforce_owner_only

sid = current_user_identity().split(":", 1)[1]
print("current SID", sid)
print(subprocess.run(["whoami", "/user", "/groups"], capture_output=True, text=True).stdout)
path = Path(tempfile.mkdtemp()) / "probe.token"
path.write_text("x")
print("before:", subprocess.run(["icacls", str(path)], capture_output=True, text=True).stdout)
enforce_owner_only(path)
print("after:", subprocess.run(["icacls", str(path)], capture_output=True, text=True).stdout)
ps = f"(Get-Acl -LiteralPath '{path}').Owner; (Get-Acl -LiteralPath '{path}').Sddl"
print(subprocess.run(["pwsh", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout)

advapi, kernel = _windows_security_api()
owner, dacl, desc, user = (ctypes.c_void_p() for _ in range(4))
status = advapi.GetNamedSecurityInfoW(
    str(path), 1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(desc)
)
print("GetNamedSecurityInfoW status", status, "owner", bool(owner.value), "dacl", bool(dacl.value))
print("ConvertStringSidToSidW", advapi.ConvertStringSidToSidW(sid, ctypes.byref(user)))
print("ace_count", ctypes.cast(dacl, ctypes.POINTER(_Acl)).contents.ace_count)
ace = ctypes.c_void_p()
print("GetAce", advapi.GetAce(dacl, 0, ctypes.byref(ace)))
print("ace_type", ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte)).contents.value)
print("owner == user", advapi.EqualSid(owner, user))
print("ace sid == user", advapi.EqualSid(ctypes.c_void_p(ace.value + 8), user))
