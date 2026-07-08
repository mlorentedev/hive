"""hive runtime layout — versioned install dirs + a ``current`` junction
(HIVE-267 / ADR-015 mechanism A3).

On Windows ``uv tool upgrade`` rewrites the one install dir *in place*; while the
Startup supervisor keeps the venv ``python.exe`` running it holds that dir
locked, so the upgrade corrupts the in-use install (#267 — ``Access is denied``).
A3 moves hive to owning its own layout instead: each version lives in its own dir
under ``versions/`` and a ``current`` junction — a reparse point, NOT a copy —
selects the active one. An upgrade builds the new version *beside* the old and
repoints the junction; the locked target files are never touched, so the swap
that breaks ``uv tool upgrade`` succeeds. Spike-validated on real non-admin
Windows (2026-06-24, ``specs/HIVE-267-upgrade-swap/verification.md``).

The layout root is env-overridable (``HIVE_RUNTIME_ROOT``) so the pure path logic
and the repoint orchestration are testable off Windows; the OS-specific swap
primitive (``mklink /J``) is validated by real hardware — mirroring how
``hive service`` treats schtasks/systemctl.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hive._helpers import _subprocess_run_kwargs

CURRENT_LINK_NAME = "current"
_STAGING_LINK_NAME = ".current.staging"


# ── layout paths (env-overridable, host-independent) ─────────────────────


def runtime_root() -> Path:
    """Root hive owns for the versioned layout.

    ``HIVE_RUNTIME_ROOT`` overrides (used in tests and to relocate the install).
    Default: ``%LOCALAPPDATA%\\hive\\runtime`` on Windows — the per-user,
    non-roaming store where an app owns its own binaries — and
    ``~/.local/share/hive/runtime`` elsewhere, matching config's other state
    paths (``db_path`` et al.).
    """
    override = os.environ.get("HIVE_RUNTIME_ROOT")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "hive" / "runtime"
    return Path.home() / ".local" / "share" / "hive" / "runtime"


def versions_dir() -> Path:
    """Parent of every installed version dir (``<root>/versions``)."""
    return runtime_root() / "versions"


def version_path(version: str) -> Path:
    """Install dir for a specific version (``<root>/versions/<version>``)."""
    return versions_dir() / version


def current_link() -> Path:
    """The ``current`` junction that selects the active version."""
    return runtime_root() / CURRENT_LINK_NAME


def _staging_link() -> Path:
    """Scratch reparse-point name used to stage a new junction before the flip."""
    return runtime_root() / _STAGING_LINK_NAME


# ── the OS-specific swap primitive (real on the host; mocked in unit tests) ──


def _junction_command(link: Path, target: Path) -> list[str]:
    """The ``mklink /J`` argv that creates directory junction *link* → *target*.

    ``mklink`` is a ``cmd`` builtin (no standalone exe), so it runs via
    ``cmd /c``. ``/J`` = directory junction: needs no admin / developer-mode
    (unlike ``/D`` symlinks), and repointing it never touches the target's files
    — the property that makes it immune to the in-use lock that breaks
    ``uv tool upgrade`` (#267)."""
    return ["cmd", "/c", "mklink", "/J", str(link), str(target)]


def _make_junction(link: Path, target: Path) -> None:
    """Create reparse point *link* pointing at directory *target*.

    Windows: a directory junction via ``mklink /J`` (no admin). Elsewhere (CI,
    POSIX dev): a directory symlink, which shares the one property the swap
    relies on — it redirects without copying the target. Raises ``OSError`` on
    failure so the caller's no-corruption path (criterion 3) can react."""
    if sys.platform == "win32":
        proc = subprocess.run(  # noqa: S603
            _junction_command(link, target),
            check=False,
            capture_output=True,
            text=True,
            **_subprocess_run_kwargs(),
        )
        if proc.returncode != 0:
            raise OSError(f"mklink /J failed (rc={proc.returncode}): {proc.stderr.strip()}")
    else:
        os.symlink(target, link, target_is_directory=True)


def _remove_link(link: Path) -> None:
    """Remove a reparse point WITHOUT deleting its target tree.

    On Windows ``os.rmdir`` on a junction drops only the reparse point (the
    locked target files are untouched — the whole point of A3). On POSIX
    ``unlink`` drops the symlink. A real directory sitting at *link* is refused,
    not silently nuked; a missing link is a no-op."""
    if link.is_symlink():
        link.unlink()
    elif link.is_junction():
        os.rmdir(link)
    elif link.exists():
        raise RuntimeError(
            f"refusing to repoint: {link} is a real directory, not a hive junction",
        )


# ── the swap orchestration ───────────────────────────────────────────────


def repoint(version: str) -> None:
    """Point ``current`` at an installed *version*, so that a failure never
    corrupts the running install (acceptance criterion 3).

    Primitives available:

    * ``version_path(version)`` — the target dir; guarded below to exist.
    * ``current_link()`` / ``_staging_link()`` — the live and scratch reparse
      points (siblings under ``runtime_root()``, so ``os.rename`` between them is
      a same-volume flip).
    * ``_make_junction(link, target)`` — create a reparse point. **FALLIBLE**:
      raises ``OSError``; this is the step that fails in the #267 scenario.
    * ``_remove_link(link)`` — drop a reparse point without touching its target.
    * ``os.rename(src, dst)`` — flip a staged link onto ``current`` (cheap, and
      effectively never fails once the junction exists).

    Correctness constraint (criterion 3): the FALLIBLE step (``_make_junction``)
    must run BEFORE ``current`` is disturbed, so a failure leaves the previous
    install still pointed-to. Clear any stale ``_staging_link()`` first (a prior
    crash may have left one). On failure, raise a ``RuntimeError`` that names the
    version and gives a WHY/FIX (``pattern-agent-oriented-errors``) — never let a
    bare ``OSError`` escape.
    """
    target = version_path(version)
    if not target.is_dir():
        raise RuntimeError(
            f"hive self-upgrade: version {version} is not installed "
            f"({target} does not exist). Build it into its own dir first, then repoint.",
        )
    # Stage the new junction under a scratch name FIRST — the fallible step — so a
    # failure here leaves `current` untouched (acceptance criterion 3). A prior
    # crash may have left a stale staging link; clear it before creating.
    staging = _staging_link()
    _remove_link(staging)
    try:
        _make_junction(staging, target)
    except OSError as exc:
        raise RuntimeError(
            f"hive self-upgrade: could not stage version {version} at {staging} "
            f"({exc}). The previous install is untouched — retry, or check disk "
            f"space / permissions.",
        ) from exc
    # `current` still points at the previous version; now flip. The sub-ms window
    # where `current` is absent (between remove and rename) is tolerated by the
    # supervisor's relaunch loop (verification.md); the fallible work is already
    # done, so this tail effectively never fails.
    _remove_link(current_link())
    os.rename(staging, current_link())
