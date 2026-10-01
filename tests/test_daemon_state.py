"""Persistent daemon credential and stable binding tests (HIVE-437)."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


def _patch_state_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import hive._daemon as daemon

    monkeypatch.setattr(daemon, "daemon_state_dir", lambda: tmp_path)
    monkeypatch.setattr(daemon, "token_file_path", lambda: tmp_path / "daemon.token")
    monkeypatch.setattr(daemon, "port_file_path", lambda: tmp_path / "daemon.port")
    monkeypatch.setattr(daemon, "lock_file_path", lambda: tmp_path / "daemon.lock")


def test_load_or_create_token_reuses_existing_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._credential as credential
    import hive._daemon as daemon
    import hive._owner_only as owner_only

    _patch_state_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(owner_only, "enforce_owner_only", lambda path: None)
    monkeypatch.setattr(owner_only, "_verify_owner_only", lambda path: True)
    monkeypatch.setattr(credential, "_verify_owner_only", lambda path: True)

    first = daemon.load_or_create_token()
    second = daemon.load_or_create_token()

    assert first == second
    assert (tmp_path / "daemon.token").read_text(encoding="utf-8") == first


def test_token_publication_is_atomic_and_cleans_failed_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._daemon as daemon
    import hive._owner_only as owner_only

    _patch_state_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(owner_only, "enforce_owner_only", lambda path: None)
    monkeypatch.setattr(owner_only, "_verify_owner_only", lambda path: False)

    with pytest.raises(RuntimeError, match="owner-only"):
        daemon.load_or_create_token()

    assert not (tmp_path / "daemon.token").exists()
    assert list(tmp_path.glob(".daemon.token.*")) == []


def test_existing_invalid_token_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._credential as credential
    import hive._daemon as daemon

    _patch_state_paths(monkeypatch, tmp_path)
    (tmp_path / "daemon.token").write_text("not a token", encoding="utf-8")
    monkeypatch.setattr(credential, "_verify_owner_only", lambda path: True)

    with pytest.raises(RuntimeError, match="invalid daemon credential"):
        daemon.load_or_create_token()


def test_existing_permission_invalid_token_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._credential as credential
    import hive._daemon as daemon

    _patch_state_paths(monkeypatch, tmp_path)
    (tmp_path / "daemon.token").write_text("a" * 43, encoding="utf-8")
    monkeypatch.setattr(credential, "_verify_owner_only", lambda path: False)

    with pytest.raises(RuntimeError, match="owner-only"):
        daemon.load_or_create_token()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL verification")
def test_windows_credential_verification_avoids_subprocesses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import hive._credential as credential
    from hive._daemon import _enforce_owner_only

    path = tmp_path / "synthetic.token"
    path.write_text("a" * 43, encoding="utf-8")
    _enforce_owner_only(path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("credential validation launched a subprocess"),
    )

    assert credential._verify_owner_only(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL verification")
def test_windows_credential_verification_rejects_an_extra_principal(tmp_path: Path) -> None:
    import hive._credential as credential
    from hive._daemon import _enforce_owner_only

    path = tmp_path / "synthetic.token"
    path.write_text("a" * 43, encoding="utf-8")
    _enforce_owner_only(path)
    assert not credential._windows_owner_only(path, "S-1-5-21-0-0-0-999")
    subprocess.run(
        ["icacls", str(path), "/grant", "*S-1-1-0:(R)"],
        check=True,
        capture_output=True,
    )

    assert not credential._verify_owner_only(path)


def test_run_serve_uses_stable_port_and_token_across_restarts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._credential as credential
    import hive._daemon as daemon
    import hive._owner_only as owner_only

    _patch_state_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "_startup_self_heal", lambda vault: None)
    monkeypatch.setattr(owner_only, "enforce_owner_only", lambda path: None)
    monkeypatch.setattr(owner_only, "_verify_owner_only", lambda path: True)
    monkeypatch.setattr(credential, "_verify_owner_only", lambda path: True)
    monkeypatch.setattr(daemon, "configured_daemon_port", lambda: 54282)
    monkeypatch.setattr(daemon, "_port_available", lambda host, port: True)
    served: list[tuple[int, str]] = []
    monkeypatch.setattr(
        daemon,
        "_serve_owned",
        lambda host, port, token: served.append((port, token)) or False,
    )

    assert daemon.run_serve() == 0
    assert daemon.run_serve() == 0

    assert [port for port, _token in served] == [54282, 54282]
    assert served[0][1] == served[1][1]
    assert (tmp_path / "daemon.port").read_text(encoding="utf-8") == "54282"


def test_run_serve_fails_closed_when_stable_port_is_occupied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import hive._daemon as daemon

    _patch_state_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "_startup_self_heal", lambda vault: None)
    monkeypatch.setattr(daemon, "configured_daemon_port", lambda: 54282)
    monkeypatch.setattr(daemon, "_port_available", lambda host, port: False)

    assert daemon.run_serve() != 0
    message = capsys.readouterr().err
    assert "54282" in message
    assert "HIVE_DAEMON_PORT" in message
    assert not (tmp_path / "daemon.port").exists()


@pytest.mark.parametrize("is_windows", [False, True])
def test_port_probe_only_reuses_address_on_posix(
    monkeypatch: pytest.MonkeyPatch, is_windows: bool
) -> None:
    import hive._daemon as daemon

    calls: list[tuple[object, ...]] = []

    class Probe:
        def __enter__(self) -> Probe:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def setsockopt(self, *args: object) -> None:
            calls.append(("reuse", *args))

        def bind(self, address: tuple[str, int]) -> None:
            calls.append(("bind", address))

    monkeypatch.setattr(daemon, "IS_WINDOWS", is_windows)
    monkeypatch.setattr(daemon.socket, "socket", lambda *args: Probe())

    assert daemon._port_available("127.0.0.1", 54282)
    expected = (
        [] if is_windows else [("reuse", daemon.socket.SOL_SOCKET, daemon.socket.SO_REUSEADDR, 1)]
    )
    assert calls == [*expected, ("bind", ("127.0.0.1", 54282))]
