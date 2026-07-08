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
