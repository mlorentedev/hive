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

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from hive._helpers import _subprocess_run_kwargs

_log = logging.getLogger(__name__)

CURRENT_LINK_NAME = "current"
_STAGING_LINK_NAME = ".current.staging"

# The launcher a human's shell resolves (ADR-019). A `.cmd`, not a `.exe`,
# because hive has no compiler in the install path — and `.cmd` is on PATHEXT.
LAUNCHER_NAME = "hive.cmd"

# Windows semantics, asserted rather than inherited from the host. `prepend_user_path`
# writes a *Windows registry* value, so `;` and case-insensitivity apply no matter
# which OS the interpreter is on — using `os.pathsep`/`os.path.normcase` here would
# make the function behave one way in production and another under test.
_WINDOWS_PATH_SEP = ";"

# PyPI's per-project JSON API — ``info.version`` is the newest published release.
# Bounded so a wedged/slow PyPI fails fast instead of hanging the trigger; the
# caller always has the explicit-version escape hatch.
_PYPI_JSON_URL = "https://pypi.org/pypi/{package}/json"
_PYPI_TIMEOUT_S = 10.0


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


def launcher_dir() -> Path:
    """Directory hive owns for its ``PATH`` launcher — ``%LOCALAPPDATA%\\hive\\bin``.

    Derived from ``runtime_root().parent`` rather than reading ``LOCALAPPDATA``
    a second time, so the one existing ``HIVE_RUNTIME_ROOT`` seam relocates the
    launcher along with the versions it dispatches to. A test that overrides the
    root therefore cannot write a shim into the developer's real profile, and an
    install relocated off the default keeps its two halves together.

    ADR-019: hive owns this directory outright and never writes to ``~/.local/bin``,
    which uv owns. Joint ownership was rejected because ``PATHEXT`` resolves
    ``.EXE`` before ``.CMD`` — a ``hive.cmd`` written beside a leftover
    ``hive.exe`` would change nothing observable.
    """
    return runtime_root().parent / "bin"


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


# ── version lifecycle: build a fresh dir + GC unreferenced ones ──────────


def _venv_python(venv_dir: Path) -> Path:
    """The interpreter inside a uv-built venv, for ``uv pip install --python``."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run_uv(args: list[str]) -> None:
    """Run ``uv <args>``, raising an actionable ``RuntimeError`` on failure.

    The single ``uv`` seam (mocked in unit tests; the real tool validated by the
    CI matrix), mirroring how ``_service`` treats schtasks/systemctl."""
    try:
        proc = subprocess.run(  # noqa: S603
            ["uv", *args],
            check=False,
            capture_output=True,
            text=True,
            **_subprocess_run_kwargs(),
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "hive self-upgrade: `uv` is not on PATH — install uv (astral.sh/uv) "
            "or run the upgrade manually.",
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"`uv {' '.join(args)}` failed (rc={proc.returncode}): {proc.stderr.strip()}",
        )


def build_version(version: str, *, package: str = "hive-vault") -> Path:
    """Build *version* into its own dir under ``versions/``, never touching the
    in-use install; return the new version dir.

    Uses uv exactly as the manual path would — ``uv venv <dir>`` then
    ``uv pip install --python <dir> <package>==<version>`` — so uv/pip do the
    heavy lifting and hive only owns *where* the version lands. A build that
    fails part-way is cleaned up: a half-built dir must never survive to be
    repointed-at (that corrupt-dir state is the #267 failure mode)."""
    target = version_path(version)
    if target.exists():
        raise RuntimeError(
            f"hive self-upgrade: version {version} is already built at {target}; "
            f"remove it before rebuilding.",
        )
    versions_dir().mkdir(parents=True, exist_ok=True)
    try:
        _run_uv(["venv", str(target)])
        _run_uv(["pip", "install", "--python", str(_venv_python(target)), f"{package}=={version}"])
    except RuntimeError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError(
            f"hive self-upgrade: could not build version {version} into {target} "
            f"({exc}). The in-use install is untouched.",
        ) from exc
    return target


def current_version() -> str | None:
    """The version the ``current`` junction selects, or ``None`` if unset.

    Resolves through the reparse point and returns the target dir's name (the
    version string) — the read side GC uses to avoid deleting the active one."""
    link = current_link()
    if not (link.is_symlink() or link.is_junction()):
        return None
    return link.resolve().name


def remove_version(version: str) -> bool:
    """GC an installed *version* dir; return whether it was actually removed.

    Refuses to remove the active version (that would pull the running install
    out from under the daemon). A still-locked dir — the old version whose
    ``python.exe`` the supervisor has not yet released — is a DEFERRED GC, not a
    crash: return ``False`` so the caller retries after the lock drops (the
    spike's 'GC old dir once unreferenced' step)."""
    if version == current_version():
        raise RuntimeError(
            f"hive self-upgrade: refusing to remove the active version {version}; "
            f"repoint to another version first.",
        )
    target = version_path(version)
    if not target.exists():
        return False
    try:
        shutil.rmtree(target)
    except OSError:
        return False  # locked / in use — deferred, retried once the lock releases
    return True


# ── resolve the target version from PyPI (the auto-latest path, #292) ─────


def latest_version(package: str = "hive-vault") -> str:
    """The newest *package* release published to PyPI (``info.version``).

    Lets the unattended dotfiles trigger call a bare ``hive self-upgrade`` without
    hard-coding a version number — the auto-latest follow-up to the explicit
    ``<version>`` contract (#292). The single network seam in this module (mocked
    in unit tests; real PyPI exercised only by the trigger), bounded by
    ``_PYPI_TIMEOUT_S`` so a wedged PyPI fails fast rather than hanging the
    timer. Every failure — timeout, network/HTTP error, or an unexpected payload
    shape — raises an actionable ``RuntimeError`` (``pattern-agent-oriented-errors``)
    that names the escape hatch (``hive self-upgrade <version>``), never a bare
    httpx traceback nor a bogus/empty version string that would repoint ``current``
    at nothing."""
    url = _PYPI_JSON_URL.format(package=package)
    try:
        response = httpx.get(url, timeout=_PYPI_TIMEOUT_S, follow_redirects=True)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        # Umbrella class (AGENTS.md load-bearing rule): a slow PyPI surfaces as
        # ReadTimeout, which ConnectTimeout alone would miss.
        raise RuntimeError(
            f"hive self-upgrade: timed out reaching PyPI to resolve the latest "
            f"{package} version ({exc}). Retry, or pin one: "
            f"`hive self-upgrade <version>`.",
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"hive self-upgrade: could not reach PyPI to resolve the latest "
            f"{package} version ({exc}). Check the network, or pin one: "
            f"`hive self-upgrade <version>`.",
        ) from exc
    try:
        version = response.json()["info"]["version"]
    except (ValueError, KeyError, TypeError) as exc:
        # ValueError covers a non-JSON body (json.JSONDecodeError); KeyError /
        # TypeError cover a renamed field or a non-dict payload.
        raise RuntimeError(
            f"hive self-upgrade: PyPI returned an unexpected payload for {package} "
            f"({exc}). Pin a version instead: `hive self-upgrade <version>`.",
        ) from exc
    if not version or not isinstance(version, str):
        raise RuntimeError(
            f"hive self-upgrade: PyPI reported no usable version for {package} "
            f"(got {version!r}). Pin one: `hive self-upgrade <version>`.",
        )
    return version


# ── the end-to-end upgrade: build → repoint → GC (HIVE-267 / #292) ───────


def _gc_other_versions(keep: str) -> list[str]:
    """GC every installed version except *keep*; return the ones whose GC was
    DEFERRED because they were still locked.

    The old version is exactly the one whose ``python.exe`` the supervisor has
    not yet released, so its ``remove_version`` returns ``False`` (deferred, not
    a crash). The leftover is cleaned by the next ``self_upgrade`` once the lock
    drops — the spike's 'GC old dir once unreferenced'. A missing/empty
    ``versions`` dir yields nothing to do."""
    root = versions_dir()
    if not root.is_dir():
        return []
    deferred: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name == keep:
            continue
        if not remove_version(child.name):
            deferred.append(child.name)
    return deferred


# ── the PATH launcher (HIVE-328 PR2 / ADR-019) ───────────────────────────


def _launcher_target() -> Path:
    """The executable the shim dispatches to, addressed *through* ``current``.

    Naming the junction rather than a concrete ``versions/<v>`` dir is the entire
    reason A3 exists: an upgrade repoints one link and every consumer follows,
    unrewritten. A shim naming the version would need regenerating on every
    upgrade and would be stale for the whole window in between.
    """
    return current_link() / "Scripts" / "hive.exe"


def render_launcher_script(target: Path) -> str:
    """The ``hive.cmd`` body — a pure renderer, so its contents are testable off
    Windows.

    Deliberately two lines. Nothing may follow the dispatch, because ``cmd``
    reports the *last* command's ``ERRORLEVEL`` and hive's exit codes are load
    bearing: the daemon returns 75 to ask its supervisor for a restart-on-upgrade,
    and a trailing ``echo`` here would overwrite that with 0 and strand the
    daemon on the old version.
    """
    return f'@echo off\r\n"{target}" %*\r\n'


def _windows_path_key(value: str) -> str:
    """Comparison key for a Windows ``PATH`` entry.

    Windows resolves paths case-insensitively and accepts either separator, so
    ``C:\\Hive\\Bin``, ``c:/hive/bin`` and ``c:\\hive\\bin\\`` are one directory
    and must deduplicate as one. Spelled out rather than delegated to
    ``os.path.normcase``, which is the identity function off Windows and would
    silently stop deduplicating under test.
    """
    return value.replace("/", "\\").rstrip("\\").lower()


def _read_user_path() -> str:
    """The User (``HKCU``) ``PATH``, or ``""`` when unset. A test seam: this is
    the only launcher code that cannot exist off Windows."""
    if sys.platform != "win32":  # pragma: no cover — stubbed in tests, unreachable in prod
        return ""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            value, _kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return ""
        return str(value)


def _write_user_path(value: str) -> None:
    """Replace the User ``PATH``.

    Written as ``REG_EXPAND_SZ``, never ``REG_SZ``: a user ``PATH`` routinely
    contains entries like ``%USERPROFILE%\\.local\\bin``, and storing it as a
    plain string freezes those percent-signs as literal text — silently breaking
    every unrelated entry that relied on expansion.
    """
    if sys.platform != "win32":  # pragma: no cover — stubbed in tests, unreachable in prod
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, value)


def _broadcast_env_change() -> None:
    """Tell running processes the environment changed (``WM_SETTINGCHANGE``).

    Load bearing for ADR-019's accepted cost. New processes read ``PATH`` from
    the registry only if their *parent* has refreshed — without this broadcast
    Explorer keeps handing every newly-opened terminal its stale copy, and
    "open a new terminal" would not work until the next logout. Best-effort: a
    failed broadcast costs a logout, never a failed install.
    """
    if sys.platform != "win32":  # pragma: no cover — stubbed in tests, unreachable in prod
        return
    import ctypes

    hwnd_broadcast = 0xFFFF
    wm_settingchange = 0x1A
    smto_abortifhung = 0x0002
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            hwnd_broadcast, wm_settingchange, 0, "Environment", smto_abortifhung, 5000, None
        )
    except Exception:  # noqa: BLE001 — cosmetic refresh; never fail an install over it
        _log.debug("mcp.launcher.broadcast_failed", exc_info=True)


def prepend_user_path(directory: Path) -> bool:
    """Prepend *directory* to the User ``PATH``; return whether anything changed.

    **Prepend, never append** (AC11). The machine that motivated this work still
    has a dead ``hive.exe`` in ``~/.local/bin``, and under ``PATHEXT`` an appended
    entry would lose that lookup forever — ordering is the whole mechanism.

    **Idempotent** (AC8): an entry already present anywhere is left where it is.
    An upgrade runs this every time, so a version that re-prepended would grow
    the User ``PATH`` without bound, and a ``PATH`` that overruns its length
    ceiling is a far worse failure than a launcher that is merely second in line.
    """
    existing = [entry for entry in _read_user_path().split(_WINDOWS_PATH_SEP) if entry]
    wanted = _windows_path_key(str(directory))
    if any(_windows_path_key(entry) == wanted for entry in existing):
        return False
    _write_user_path(_WINDOWS_PATH_SEP.join([str(directory), *existing]))
    _broadcast_env_change()
    return True


def orphaned_launchers(skip: Path) -> list[str]:
    """Every ``hive`` on ``PATH`` that cannot start, excluding hive's own dir.

    ADR-019 decision 4 — **detect and warn, never delete**. Removing an orphaned
    uv trampoline is dotfiles#574's job; hive deleting another tool's artifact is
    a different class of act from installing its own, and repairing once would
    not stop the next ``uv tool install`` from recreating it.

    *skip* is hive's own launcher directory, excluded because probing the shim
    during a first install — or any window where ``current`` is mid-flip —
    legitimately fails, and reporting hive's own launcher as someone else's
    orphan would be actively misleading.
    """
    from hive._service import _executes

    owned = _windows_path_key(str(skip))
    found: list[str] = []
    seen: set[str] = set()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or _windows_path_key(entry) == owned:
            continue
        hit = shutil.which("hive", path=entry)
        if hit is None or hit in seen:
            continue
        seen.add(hit)
        if not _executes(hit):
            found.append(hit)
    return found


def render_orphan_warning(paths: list[str]) -> str:
    """An actionable report for launchers hive has decided not to touch.

    Carries WHAT and the FIX (``pattern-agent-oriented-errors``): the concrete
    path, not "a launcher", and the command that clears it — because hive has
    deliberately given up the ability to clear it itself, so a message without
    the escape hatch would leave the reader stuck.
    """
    listed = ", ".join(paths)
    return (
        f"hive: found a `hive` on PATH that does not start: {listed}. "
        "This is typically an orphaned uv trampoline left by a removed "
        "`uv tool install`. Hive's own launcher is prepended, so a NEW shell "
        "resolves hive correctly, but the dead entry stays until it is removed. "
        "Hive does not delete files another tool created — run `dotf doctor --fix`."
    )


def install_launcher() -> Path | None:
    """Install the ``PATH`` launcher; return its path, or ``None`` off Windows.

    POSIX is an explicit no-op, not an oversight (ADR-019, "Why this is
    Windows-only"): ``~/.local/bin`` is already on ``PATH`` there and nothing
    locks an in-use file, so ``uv tool`` remains the install model. Writing a
    launcher anyway would add a second, unowned way to resolve ``hive`` on a
    platform that never asked for one.

    An unchanged shim is left untouched rather than rewritten — on Windows a
    rewrite can fail outright while another process holds the file open, which
    would turn a successful upgrade into a failed one for no gain.
    """
    if sys.platform != "win32":
        return None
    bin_dir = launcher_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / LAUNCHER_NAME
    # Bytes, not text, on both sides. The renderer emits CRLF because that is
    # what a `.cmd` needs, and text mode would mangle it in both directions:
    # writing translates `\n` to `os.linesep`, so on Windows the CRLF becomes
    # `\r\r\n`; reading translates it back to `\n`, so the idempotency check
    # would never match and every upgrade would rewrite the shim.
    body = render_launcher_script(_launcher_target()).encode("utf-8")
    if not shim.exists() or shim.read_bytes() != body:
        shim.write_bytes(body)
    prepend_user_path(bin_dir)
    return shim


def self_upgrade(version: str) -> str | None:
    """Upgrade the install to *version* without ever touching in-use files
    (HIVE-267 / ADR-015 mechanism A3); return the PREVIOUS version (``None`` on a
    first install), so the CLI can report the transition.

    Three steps, ordered so a failure never corrupts the running install:

    1. **build** *version* into its own dir beside the running one — skipped when
       the dir already exists, making a retry after a crashed prior run
       idempotent (a rebuild would hit ``build_version``'s 'already built' guard);
    2. **repoint** ``current`` at it (stage-then-flip: a failure here leaves the
       previous install pointed-to, criterion 3);
    3. **GC** the now-unreferenced old versions (a still-locked one defers).

    A no-op when *version* is already ``current``, so the unattended dotfiles
    trigger can fire repeatedly without churn. Does NOT restart the running
    daemon: the supervisor's exit-75 restart-on-upgrade contract relaunches
    ``hive serve`` through the freshly repointed ``current`` (see ``_service`` /
    ``_daemon``). ``build_version`` / ``repoint`` raise an actionable
    ``RuntimeError`` on failure; the CLI wrapper turns that into a non-zero exit.
    """
    previous = current_version()
    if version == previous:
        return previous  # already selected — idempotent no-op
    if not version_path(version).is_dir():
        build_version(version)  # raises RuntimeError on failure — current untouched
    repoint(version)  # stage-then-flip — raises on failure — previous stays pointed-to
    # The launcher is what makes a completed upgrade reachable to a human typing
    # `hive` (ADR-019). Deliberately NOT allowed to fail the upgrade: by this
    # point `current` already selects the new version and the supervisor will
    # restart into it, so a refused registry write or a locked shim costs PATH
    # convenience — rolling the swap back over it would trade a working upgrade
    # for a cosmetic one.
    try:
        install_launcher()
    except OSError as exc:
        _log.warning(
            "mcp.launcher.install_failed error=%s "
            "the upgrade succeeded and `current` selects %s; only the PATH shim is missing. "
            "Re-run `hive self-upgrade` once the cause is cleared.",
            exc,
            version,
        )
    _gc_other_versions(keep=version)
    return previous
