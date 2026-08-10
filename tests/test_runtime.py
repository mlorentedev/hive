"""Unit tests for the hive runtime layout — versioned install dirs + a
``current`` junction (HIVE-267 / ADR-015 mechanism A3).

Like ``hive service``, the OS-specific swap primitive (Windows ``mklink /J``) is
validated on real non-admin Windows hardware; these tests exercise the pure
layout/command renderers (host-independent) and the *repoint* orchestration with
a real POSIX symlink standing in for the junction on CI. That keeps the
rollback / no-corruption guarantee (acceptance criterion 3) exercised on every
push instead of only mocked — the junction and the symlink share the one
property that matters here: repointing touches only the reparse point, never the
locked target files that break ``uv tool upgrade`` (#267).
"""

from __future__ import annotations

import pytest

# ── layout paths (host-independent, env-overridable) ─────────────────────


def test_runtime_layout_is_versioned_under_an_overridable_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """hive owns its own install root on Windows (``%LOCALAPPDATA%\\hive\\runtime``)
    because ``uv tool`` always rewrites the same dir — no per-version layout. The
    root is env-overridable (``HIVE_RUNTIME_ROOT``) so the whole layout is
    testable off Windows, exactly like ``_service._config_root()``."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    assert _runtime.runtime_root() == tmp_path
    assert _runtime.versions_dir() == tmp_path / "versions"
    assert _runtime.version_path("1.41.6") == tmp_path / "versions" / "1.41.6"
    assert _runtime.current_link() == tmp_path / "current"


def test_junction_command_uses_mklink_slash_j_for_a_non_admin_swap() -> None:
    """A directory junction (``mklink /J``) is the C7-safe, non-admin swap
    primitive: unlike a symlink it needs no elevation / developer-mode, and it
    repoints the reparse point without ever touching the locked target files —
    the exact #267 failure mode, avoided. Spike-validated on real non-admin
    Windows (2026-06-24, verification.md)."""
    from pathlib import PureWindowsPath

    from hive._runtime import _junction_command

    link = PureWindowsPath(r"C:\Users\u\AppData\Local\hive\runtime\current")
    target = PureWindowsPath(r"C:\Users\u\AppData\Local\hive\runtime\versions\1.41.6")
    cmd = _junction_command(link, target)

    # `mklink` is a cmd builtin, so it must run via `cmd /c`; `/J` = directory
    # junction (no admin), NOT `/D` (symlink, needs elevation).
    assert cmd[:4] == ["cmd", "/c", "mklink", "/J"]
    assert cmd[4].endswith(r"runtime\current")  # link first (mklink LINK TARGET)
    assert cmd[5].endswith(r"versions\1.41.6")  # target second


# ── repoint orchestration (real symlink on POSIX; junction on Windows) ────


def _seed_version(runtime, version: str) -> None:
    """Create a fake installed version dir with a version marker file."""
    vdir = runtime.version_path(version)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "VERSION").write_text(version, encoding="utf-8")


def test_repoint_points_current_at_the_target_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """After a repoint, reads through ``current`` resolve to the new version's
    files (acceptance criterion 1: the swap leaves a valid, resolvable install)."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.6")
    _runtime.repoint("1.41.6")

    assert (_runtime.current_link() / "VERSION").read_text(encoding="utf-8") == "1.41.6"


def test_repoint_replaces_an_existing_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Repointing over a live ``current`` is the upgrade itself: the old version
    stays locked/running, the reparse point flips to the new one."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.5")
    _seed_version(_runtime, "1.41.6")
    _runtime.repoint("1.41.5")
    assert (_runtime.current_link() / "VERSION").read_text(encoding="utf-8") == "1.41.5"

    _runtime.repoint("1.41.6")  # repoint over an existing current
    assert (_runtime.current_link() / "VERSION").read_text(encoding="utf-8") == "1.41.6"


def test_failed_repoint_leaves_the_previous_current_intact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Acceptance criterion 3 — the anti-#267 guarantee. A swap that cannot
    complete must leave the PREVIOUS working install pointed-to (never absent or
    corrupted) and raise an actionable error, not a bare traceback."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.5")
    _seed_version(_runtime, "1.41.6")
    _runtime.repoint("1.41.5")  # working install at 1.41.5

    # The swap to the new version fails part-way (e.g. a transient OS error).
    def _boom(link, target) -> None:
        raise OSError("Access is denied (os error 5)")

    monkeypatch.setattr(_runtime, "_make_junction", _boom)

    with pytest.raises(RuntimeError) as excinfo:
        _runtime.repoint("1.41.6")

    # Previous install still resolvable — no corruption, no dead MCP.
    assert (_runtime.current_link() / "VERSION").read_text(encoding="utf-8") == "1.41.5"
    # Actionable WHY/FIX (pattern-agent-oriented-errors), not a bare OSError.
    message = str(excinfo.value)
    assert "1.41.6" in message


def test_repoint_rejects_a_version_that_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Repointing at a version dir that was never built is a caller error, caught
    loudly up front rather than leaving ``current`` dangling at a missing target."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    with pytest.raises(RuntimeError) as excinfo:
        _runtime.repoint("9.9.9")
    assert "9.9.9" in str(excinfo.value)


# ── version build + GC (uv mocked; real uv validated by the CI matrix) ────


def test_build_version_runs_uv_venv_then_a_pinned_pip_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A version is built into its OWN dir with uv exactly as the manual path
    would: ``uv venv <dir>`` then ``uv pip install --python <dir> pkg==<v>``. uv
    does the heavy lifting; hive only owns *where* it lands so the swap is a
    junction repoint (A3), never an in-place rewrite that corrupts a locked
    install (#267)."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    calls: list[list[str]] = []
    monkeypatch.setattr(_runtime, "_run_uv", lambda args: calls.append(args))

    result = _runtime.build_version("1.41.6")

    assert result == _runtime.version_path("1.41.6")
    # 1) a venv in the version's own dir — never the in-use one.
    assert calls[0][0] == "venv"
    assert str(_runtime.version_path("1.41.6")) in calls[0]
    # 2) the pinned package installed into THAT venv.
    assert calls[1][:2] == ["pip", "install"]
    assert "--python" in calls[1]
    assert "hive-vault==1.41.6" in calls[1]


def test_build_version_cleans_up_a_partial_dir_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """If the install fails part-way, the half-built dir must NOT survive to be
    repointed-at — a corrupt version dir is exactly the #267 failure mode. The
    error names the version and states the in-use install is untouched."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    def _uv(args: list[str]) -> None:
        if args[0] == "venv":
            _runtime.version_path("1.41.6").mkdir(parents=True)  # venv created
            return
        raise RuntimeError("uv pip install failed: no network")  # install fails

    monkeypatch.setattr(_runtime, "_run_uv", _uv)

    with pytest.raises(RuntimeError) as excinfo:
        _runtime.build_version("1.41.6")

    assert not _runtime.version_path("1.41.6").exists()
    assert "1.41.6" in str(excinfo.value)


def test_current_version_reads_the_active_junction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """``current_version()`` reports which version the junction selects (or
    ``None`` before anything is linked) — the GC read-side of the layout."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.6")
    assert _runtime.current_version() is None  # nothing linked yet

    _runtime.repoint("1.41.6")
    assert _runtime.current_version() == "1.41.6"


def test_remove_version_refuses_the_active_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """GC must never delete the version the junction currently points at — that
    would pull the running install out from under the daemon."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.6")
    _runtime.repoint("1.41.6")

    with pytest.raises(RuntimeError) as excinfo:
        _runtime.remove_version("1.41.6")
    assert "1.41.6" in str(excinfo.value)
    assert _runtime.version_path("1.41.6").is_dir()  # still there


def test_remove_version_drops_an_unreferenced_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """An old, no-longer-current version is GC'd once unreferenced."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.5")
    _seed_version(_runtime, "1.41.6")
    _runtime.repoint("1.41.6")

    assert _runtime.remove_version("1.41.5") is True
    assert not _runtime.version_path("1.41.5").exists()


def test_remove_version_defers_when_the_dir_is_locked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A still-locked old version (its python.exe not yet released) is a DEFERRED
    GC, not a crash — return False so the caller can retry after the lock drops,
    exactly as the spike's 'GC old dir once unreferenced' step observed."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.5")
    _seed_version(_runtime, "1.41.6")
    _runtime.repoint("1.41.6")

    def _locked(_path) -> None:
        raise OSError("The process cannot access the file: it is being used")

    monkeypatch.setattr(_runtime.shutil, "rmtree", _locked)

    assert _runtime.remove_version("1.41.5") is False


# ── self_upgrade orchestration: build → repoint → GC (HIVE-267 / #292) ────


def test_self_upgrade_builds_repoints_then_gcs_the_old_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The end-to-end swap: build the new version beside the running one, flip
    ``current`` to it, then GC the now-unreferenced old version. Returns the
    PREVIOUS version so the CLI can report the transition."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.5")
    _runtime.repoint("1.41.5")  # currently running 1.41.5

    built: list[str] = []

    def _fake_build(version: str, *, package: str = "hive-vault"):
        _seed_version(_runtime, version)  # uv stands in — just land the dir
        built.append(version)
        return _runtime.version_path(version)

    monkeypatch.setattr(_runtime, "build_version", _fake_build)

    previous = _runtime.self_upgrade("1.41.6")

    assert previous == "1.41.5"
    assert built == ["1.41.6"]  # the new version was built, once
    assert _runtime.current_version() == "1.41.6"  # `current` flipped
    assert not _runtime.version_path("1.41.5").exists()  # old version GC'd


def test_self_upgrade_is_a_noop_when_already_on_the_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Re-running the upgrade for the version already selected must not rebuild
    or repoint — it is idempotent, so the unattended dotfiles trigger can fire
    it repeatedly without churn."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.6")
    _runtime.repoint("1.41.6")

    def _must_not_build(*_a: object, **_k: object):
        raise AssertionError("must not build when already on the target version")

    monkeypatch.setattr(_runtime, "build_version", _must_not_build)

    assert _runtime.self_upgrade("1.41.6") == "1.41.6"
    assert _runtime.current_version() == "1.41.6"


def test_self_upgrade_skips_build_when_the_version_is_already_built(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A prior run that built the version but crashed before the flip must be
    retry-safe: the existing dir is reused (rebuilding would hit
    ``build_version``'s 'already built' guard and fail the retry)."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.5")
    _runtime.repoint("1.41.5")
    _seed_version(_runtime, "1.41.6")  # already built by a crashed prior run

    def _must_not_build(*_a: object, **_k: object):
        raise AssertionError("must not rebuild an already-built version dir")

    monkeypatch.setattr(_runtime, "build_version", _must_not_build)

    assert _runtime.self_upgrade("1.41.6") == "1.41.5"
    assert _runtime.current_version() == "1.41.6"


def test_self_upgrade_leaves_current_intact_when_the_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A build failure (no network, bad version) must never touch the running
    install: ``current`` still resolves to the previous version and the error
    propagates for the CLI to surface as a non-zero exit (criterion 3)."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.5")
    _runtime.repoint("1.41.5")

    def _boom(*_a: object, **_k: object):
        raise RuntimeError("could not build version 1.41.6: no network")

    monkeypatch.setattr(_runtime, "build_version", _boom)

    with pytest.raises(RuntimeError) as excinfo:
        _runtime.self_upgrade("1.41.6")

    assert "1.41.6" in str(excinfo.value)
    assert _runtime.current_version() == "1.41.5"  # untouched


def test_self_upgrade_defers_gc_of_a_locked_old_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The old version is exactly the one still locked (the supervisor has not
    released its ``python.exe``). Its GC must DEFER — the swap still succeeds and
    the locked dir survives to be cleaned by the next run, never crashing the
    upgrade."""
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    from hive import _runtime

    _seed_version(_runtime, "1.41.5")
    _runtime.repoint("1.41.5")

    def _fake_build(version: str, *, package: str = "hive-vault"):
        _seed_version(_runtime, version)
        return _runtime.version_path(version)

    monkeypatch.setattr(_runtime, "build_version", _fake_build)

    real_rmtree = _runtime.shutil.rmtree

    def _locked_rmtree(path, *a: object, **k: object) -> None:
        if path == _runtime.version_path("1.41.5"):
            raise OSError("being used by another process")
        real_rmtree(path, *a, **k)

    monkeypatch.setattr(_runtime.shutil, "rmtree", _locked_rmtree)

    previous = _runtime.self_upgrade("1.41.6")

    assert previous == "1.41.5"
    assert _runtime.current_version() == "1.41.6"  # swap still succeeded
    assert _runtime.version_path("1.41.5").exists()  # GC deferred, not crashed


# ── latest-version resolution from PyPI (httpx mocked; real PyPI never hit) ──


class _FakePyPIResponse:
    """Stand-in for ``httpx.Response``: just enough for ``latest_version()``."""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def test_latest_version_reads_info_version_from_pypis_json_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``hive self-upgrade`` with no version resolves the newest published release
    from PyPI's JSON API (``info.version``) so the unattended dotfiles trigger
    need not know the number — the point of the auto-latest follow-up (#292). The
    query is bounded by a timeout (never an unbounded network wait)."""
    from hive import _runtime

    seen: dict[str, object] = {}

    def _fake_get(url: str, **kwargs: object) -> _FakePyPIResponse:
        seen["url"] = url
        seen["timeout"] = kwargs.get("timeout")
        return _FakePyPIResponse({"info": {"version": "1.42.1"}})

    monkeypatch.setattr(_runtime.httpx, "get", _fake_get)

    assert _runtime.latest_version() == "1.42.1"
    assert "hive-vault" in str(seen["url"])  # queried the right distribution
    assert seen["timeout"]  # bounded — no unbounded hang on a wedged PyPI


def test_latest_version_raises_actionable_error_on_network_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PyPI outage/timeout must NOT escape as a bare httpx traceback: raise an
    actionable ``RuntimeError`` pointing at the explicit-version escape hatch —
    the same WHY/FIX contract as the rest of self-upgrade. Catching the
    ``TimeoutException`` umbrella (not ``ConnectTimeout`` alone) is a load-bearing
    rule (AGENTS.md): a slow PyPI surfaces as ``ReadTimeout``."""
    import httpx

    from hive import _runtime

    def _timeout(url: str, **kwargs: object) -> object:
        raise httpx.ReadTimeout("timed out")  # subclass of TimeoutException

    monkeypatch.setattr(_runtime.httpx, "get", _timeout)

    with pytest.raises(RuntimeError) as excinfo:
        _runtime.latest_version()
    message = str(excinfo.value)
    assert "PyPI" in message
    assert "hive self-upgrade <version>" in message  # the explicit-version fallback


def test_latest_version_raises_actionable_error_on_unexpected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyPI reachable but the JSON shape is not what we expect (renamed field, an
    HTML error page decoded as JSON, an empty version) — surface an actionable
    error rather than repointing ``current`` at a bogus/empty version string."""
    from hive import _runtime

    def _weird(url: str, **kwargs: object) -> _FakePyPIResponse:
        return _FakePyPIResponse({"info": {}})  # no 'version' key

    monkeypatch.setattr(_runtime.httpx, "get", _weird)

    with pytest.raises(RuntimeError) as excinfo:
        _runtime.latest_version()
    assert "hive-vault" in str(excinfo.value)


# ── CLI wrapper: `hive self-upgrade [version]` ───────────────────────────


def test_cli_self_upgrade_resolves_latest_from_pypi_when_version_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The version-resolution contract, updated (#292): an omitted version is NO
    LONGER a usage error — it resolves the latest release from PyPI and upgrades
    to it, so the unattended dotfiles trigger can call a bare ``hive
    self-upgrade``. An explicit version still wins (next test)."""
    from hive import server

    seen: dict[str, object] = {}
    monkeypatch.setattr("hive._runtime.latest_version", lambda: "1.42.1")

    def _fake(version: str) -> str:
        seen["version"] = version
        return "1.41.9"

    monkeypatch.setattr("hive._runtime.self_upgrade", _fake)

    assert server._run_self_upgrade([]) == 0
    assert seen["version"] == "1.42.1"  # resolved-latest flowed into the upgrade


def test_cli_self_upgrade_with_explicit_version_never_touches_pypi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pinned ``hive self-upgrade 1.41.6`` stays deterministic and network-free:
    ``latest_version()`` must not be called when the caller already named a
    version (auto-latest is strictly the omitted-arg path)."""
    from hive import server

    def _must_not_resolve() -> str:
        raise AssertionError("explicit version must not hit PyPI")

    monkeypatch.setattr("hive._runtime.latest_version", _must_not_resolve)
    monkeypatch.setattr("hive._runtime.self_upgrade", lambda version: "1.41.5")

    assert server._run_self_upgrade(["1.41.6"]) == 0


def test_cli_self_upgrade_dispatches_the_version_to_the_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``hive self-upgrade 1.41.6`` reaches ``_runtime.self_upgrade('1.41.6')``
    and returns exit 0 on success."""
    from hive import server

    seen: dict[str, object] = {}

    def _fake(version: str) -> str:
        seen["version"] = version
        return "1.41.5"

    monkeypatch.setattr("hive._runtime.self_upgrade", _fake)
    assert server._run_self_upgrade(["1.41.6"]) == 0
    assert seen["version"] == "1.41.6"


def test_cli_self_upgrade_surfaces_failure_as_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An orchestration failure is surfaced to the CLI user: the actionable
    error prints to stderr and the command exits non-zero (never a silent
    success — the #246/#252 lesson), so the dotfiles trigger sees the failure."""
    from hive import server

    def _boom(version: str) -> str:
        raise RuntimeError("could not build version 1.41.6: no network")

    monkeypatch.setattr("hive._runtime.self_upgrade", _boom)
    rc = server._run_self_upgrade(["1.41.6"])

    assert rc == 1
    assert "1.41.6" in capsys.readouterr().err


def test_cli_dispatch_routes_self_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """``hive self-upgrade …`` is wired into the top-level dispatcher, not just
    reachable via the private helper."""
    from hive import server

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "hive._runtime.self_upgrade",
        lambda version: seen.setdefault("version", version),
    )
    assert server._dispatch(["self-upgrade", "1.41.7"]) == 0
    assert seen["version"] == "1.41.7"


# ── the PATH launcher (HIVE-328 PR2 / ADR-019) ───────────────────────────
#
# AC7 ("`hive --version` works from a fresh shell after an upgrade") is the one
# criterion these cannot reach: it is a statement about the Windows shell's
# environment, and only real Windows hardware can answer it. What IS host-
# independent is every mechanism AC7 rests on — where the shim goes, what it
# contains, that it resolves through `current`, and how the PATH entry is
# written — so those are pinned here and AC7 stays a hardware task.


@pytest.fixture
def windows_launcher(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Force the Windows install path onto a POSIX test host.

    Three seams are stubbed, and each is a real boundary rather than a
    convenience: ``sys.platform`` selects the branch, and the two ``winreg``
    accessors are the only code in the launcher that cannot exist off Windows.
    Stubbing the registry with a dict keeps the *policy* — prepend, deduplicate,
    never append — under test on every push, leaving only the registry call
    itself for the hardware run.

    Yields the fake registry so a test can seed a pre-existing ``PATH`` and read
    back what the launcher wrote.
    """
    from hive import _runtime

    monkeypatch.setattr(_runtime.sys, "platform", "win32")
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path / "hive" / "runtime"))
    registry = {"Path": ""}
    monkeypatch.setattr(_runtime, "_read_user_path", lambda: registry["Path"])
    monkeypatch.setattr(_runtime, "_write_user_path", lambda value: registry.update(Path=value))
    monkeypatch.setattr(_runtime, "_broadcast_env_change", lambda: None)
    return registry


def test_launcher_lives_in_a_hive_owned_dir_beside_the_runtime(windows_launcher, tmp_path) -> None:
    """ADR-019 decision: hive owns ``%LOCALAPPDATA%\\hive\\bin``.

    Deriving it from ``runtime_root().parent`` rather than reading LOCALAPPDATA
    a second time means the existing ``HIVE_RUNTIME_ROOT`` seam relocates the
    launcher too — one override moves the whole install, so a test can never
    write a shim into the developer's real profile.
    """
    from hive import _runtime

    assert _runtime.launcher_dir() == tmp_path / "hive" / "bin"


def test_install_launcher_never_touches_local_bin(
    windows_launcher,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """AC9 — hive never writes to or deletes from ``~/.local/bin``.

    This is the boundary that *is* the decision: rejected option C had hive
    replacing a dead ``hive*`` in uv's directory, and ADR-019 ruled that out on
    the grounds that hive should not delete another tool's artifact. Asserted
    against a home directory seeded with exactly the orphan option C would have
    seized, so the test fails if that behaviour is ever reintroduced.
    """
    from hive import _runtime

    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    orphan = local_bin / "hive.exe"
    orphan.write_text("dead uv trampoline", encoding="utf-8")
    monkeypatch.setattr(_runtime.Path, "home", staticmethod(lambda: home))

    _runtime.install_launcher()

    assert orphan.read_text(encoding="utf-8") == "dead uv trampoline"
    assert sorted(p.name for p in local_bin.iterdir()) == ["hive.exe"]


def test_install_launcher_is_an_explicit_noop_off_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """ADR-019 is Windows-only, in as many words: on POSIX ``~/.local/bin`` is
    already on PATH and there is no in-use-file lock, so ``uv tool`` stays the
    install model. A launcher installed here would be a second, unowned way to
    resolve ``hive`` on a platform that never asked for one."""
    from hive import _runtime

    monkeypatch.setattr(_runtime.sys, "platform", "linux")
    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path / "runtime"))

    assert _runtime.install_launcher() is None
    assert not _runtime.launcher_dir().exists()


def test_launcher_script_dispatches_through_the_current_junction(windows_launcher) -> None:
    """The property A3 exists for: the shim names ``current``, not a concrete
    ``versions/<v>`` dir, so repointing the junction redirects the shim and an
    upgrade needs no launcher rewrite (AC8). A shim naming the version would
    have to be regenerated on every upgrade — and would be stale for the whole
    window between the repoint and that regeneration."""
    from hive import _runtime

    body = _runtime.render_launcher_script(_runtime._launcher_target())

    assert "current" in body
    assert "versions" not in body
    assert body.startswith("@echo off")
    # `%*` forwards every argument; without it `hive self-upgrade 3.0.0` would
    # reach the real executable as a bare `hive`.
    assert "%*" in body


def test_launcher_survives_a_repoint_without_being_rewritten(
    windows_launcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC8, stated as the observable rather than the mechanism: install, upgrade,
    and the shim on disk is byte-identical. This is what makes the launcher
    compose with A3 instead of becoming a second thing an upgrade must remember
    to update.

    The shim is installed under Windows semantics and the junction is then
    flipped under POSIX ones — the same split every repoint test in this file
    uses, because ``mklink`` is a ``cmd`` builtin that does not exist here while
    a symlink shares the one property the swap relies on (it redirects without
    copying).
    """
    from hive import _runtime

    shim = _runtime.install_launcher()
    assert shim is not None
    first = shim.read_bytes()

    # A repoint changes what `current` selects; it must not change the shim.
    monkeypatch.setattr(_runtime.sys, "platform", "linux")
    _runtime.version_path("3.0.0").mkdir(parents=True)
    _runtime.repoint("3.0.0")

    assert _runtime.current_version() == "3.0.0"
    assert shim.read_bytes() == first


def test_prepend_user_path_puts_hives_dir_first(windows_launcher) -> None:
    """AC11 — prepended, never appended.

    Ordering is the whole point on the machine that motivated this work: a stale
    ``~/.local/bin`` holding a dead ``hive.exe`` is still on PATH, and under
    PATHEXT an appended entry would lose to it forever. Prepending is what makes
    hive's launcher win a lookup that the orphan would otherwise answer.
    """
    from hive import _runtime

    windows_launcher["Path"] = r"C:\Users\u\.local\bin;C:\Windows\system32"
    bin_dir = _runtime.launcher_dir()

    assert _runtime.prepend_user_path(bin_dir) is True
    entries = windows_launcher["Path"].split(";")
    assert entries[0] == str(bin_dir)
    assert r"C:\Users\u\.local\bin" in entries  # nothing removed — detect, never delete


def test_prepend_user_path_is_idempotent(windows_launcher) -> None:
    """AC8 — repeated upgrades must not accumulate PATH fragments.

    An upgrade runs this every time, so a non-idempotent version would grow the
    User PATH without bound; PATH has a length ceiling, and a truncated PATH is
    a far worse failure than a missing launcher. Comparison is normcase'd
    because Windows paths are case-insensitive and a differently-cased duplicate
    is still a duplicate.
    """
    from hive import _runtime

    bin_dir = _runtime.launcher_dir()
    assert _runtime.prepend_user_path(bin_dir) is True
    after_first = windows_launcher["Path"]

    assert _runtime.prepend_user_path(bin_dir) is False
    assert windows_launcher["Path"] == after_first

    # Cased differently by a hand edit, but the same directory.
    windows_launcher["Path"] = str(bin_dir).upper() + r";C:\Windows"
    assert _runtime.prepend_user_path(bin_dir) is False


def test_install_launcher_rewrites_nothing_on_a_second_run(windows_launcher) -> None:
    """AC8 at the file level: an unchanged shim is left alone rather than
    rewritten. Rewriting an identical file would touch its mtime on every
    upgrade and, on Windows, can fail outright while another process holds the
    shim open."""
    from hive import _runtime

    shim = _runtime.install_launcher()
    assert shim is not None
    before = shim.stat().st_mtime_ns

    assert _runtime.install_launcher() == shim
    assert shim.stat().st_mtime_ns == before


# ── detect-and-warn on a foreign, dead `hive` (AC10 / ADR-019 decision 4) ──
#
# Deliberately NOT run under the forced-win32 fixture: the probe is real
# subprocess execution, so a genuine POSIX script proves the mechanism rather
# than a mock agreeing with itself. What is Windows-specific is which directory
# hive owns, not whether a `--version` probe can tell a live binary from a dead
# one.


def _fake_hive(directory, *, exit_code: int):
    """A real, executable ``hive`` that exits with *exit_code* — a live launcher
    at 0, an orphaned trampoline at anything else."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "hive"
    script.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_orphaned_launchers_reports_a_dead_hive_without_modifying_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """AC10 — report and name the repair; never touch it.

    Option C was rejected partly because hive deleting another tool's artifact
    is a different class of act from installing its own. Detection stays in
    scope, removal belongs to dotfiles#574, and this asserts the file is still
    there afterwards so the boundary is enforced by a test rather than by
    memory.
    """
    from hive import _runtime

    dead = _fake_hive(tmp_path / "orphan", exit_code=1)
    monkeypatch.setenv("PATH", str(tmp_path / "orphan"))

    found = _runtime.orphaned_launchers(skip=tmp_path / "bin")

    assert found == [str(dead)]
    assert dead.exists()  # detect, never delete


def test_orphaned_launchers_ignores_a_healthy_hive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A working ``hive`` on PATH is not an orphan. Warning about it would train
    the user to ignore the warning, which costs more than the warning buys."""
    from hive import _runtime

    _fake_hive(tmp_path / "good", exit_code=0)
    monkeypatch.setenv("PATH", str(tmp_path / "good"))

    assert _runtime.orphaned_launchers(skip=tmp_path / "bin") == []


def test_orphaned_launchers_skips_hives_own_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Hive's own shim must never be reported as an orphan.

    On Windows the shim is a ``.cmd`` dispatching through ``current``; during a
    first install, or any window where ``current`` is mid-flip, probing it can
    legitimately fail. Reporting hive's own launcher as an orphaned trampoline —
    and telling the user to run ``dotf doctor --fix`` on it — would be actively
    misleading, so the owned directory is excluded by construction.
    """
    from hive import _runtime

    _fake_hive(tmp_path / "bin", exit_code=1)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    assert _runtime.orphaned_launchers(skip=tmp_path / "bin") == []


def test_orphan_warning_names_the_path_and_the_repair_command() -> None:
    """AC10's other half: a report the reader can act on. Per
    ``pattern-agent-oriented-errors`` the message carries WHAT is broken (the
    concrete path, not "a launcher") and the FIX (``dotf doctor --fix``), since
    hive has deliberately given up the ability to repair it itself."""
    from hive import _runtime

    message = _runtime.render_orphan_warning([r"C:\Users\u\.local\bin\hive.exe"])

    assert r"C:\Users\u\.local\bin\hive.exe" in message
    assert "dotf doctor --fix" in message
