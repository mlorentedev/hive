"""Owner-only file publication shared by the token and the TLS identity.

Every daemon secret follows one sequence (ADR-022): write a same-directory
candidate, enforce and verify owner-only permissions or ACLs, then publish it
with ``os.replace``. A failure at any step removes the candidate and raises, so
a reader never observes a partial or over-permissive file.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from pathlib import Path

from hive._credential import _verify_owner_only
from hive._endpoint import current_user_identity


def _run_icacls(path: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603,S607
            ["icacls", str(path), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001 — permission enforcement must fail closed
        return None


def enforce_owner_only(path: Path) -> None:
    """Apply owner-only permissions, raising when the OS cannot enforce them."""
    if os.name == "nt":
        sid = current_user_identity().split(":", 1)[1]
        result = _run_icacls(path, "/inheritance:r", "/grant:r", f"*{sid}:(F)")
        if result is None or result.returncode != 0:
            detail = "" if result is None else result.stderr.strip()
            raise RuntimeError(f"could not enforce owner-only daemon credential ACL: {detail}")
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
