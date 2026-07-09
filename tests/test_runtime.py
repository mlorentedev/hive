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


# ── CLI wrapper: `hive self-upgrade <version>` ───────────────────────────


def test_cli_self_upgrade_requires_an_explicit_version() -> None:
    """The version-resolution contract (2026-07-09): an explicit ``<version>`` is
    REQUIRED — deterministic and network-free. A missing version is an argparse
    usage error (exit 2), never a silent no-op or an implicit 'latest'."""
    from hive import server

    with pytest.raises(SystemExit) as excinfo:
        server._run_self_upgrade([])
    assert excinfo.value.code == 2


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
