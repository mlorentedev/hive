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
    monkeypatch.setattr(daemon, "identity_key_path", lambda: tmp_path / "daemon.key")
    monkeypatch.setattr(daemon, "identity_cert_path", lambda: tmp_path / "daemon.crt")
    monkeypatch.setattr(daemon, "identity_state_path", lambda: tmp_path / "identity.state")


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
    import hive._identity as identity
    import hive._owner_only as owner_only

    # hive._identity binds _verify_owner_only at import; importing it above,
    # before the patch, keeps a stub from leaking into later tests.
    _patch_state_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "_startup_self_heal", lambda vault: None)
    monkeypatch.setattr(owner_only, "enforce_owner_only", lambda path: None)
    monkeypatch.setattr(owner_only, "_verify_owner_only", lambda path: True)
    monkeypatch.setattr(credential, "_verify_owner_only", lambda path: True)
    monkeypatch.setattr(identity, "_verify_owner_only", lambda path: True)
    monkeypatch.setattr(daemon, "configured_daemon_port", lambda: 54282)
    monkeypatch.setattr(daemon, "_port_available", lambda host, port: True)
    served: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        daemon,
        "_serve_owned",
        lambda host, port, token, identity: (
            served.append((port, token, identity.fingerprint)) or False
        ),
    )

    assert daemon.run_serve() == 0
    assert daemon.run_serve() == 0

    assert [port for port, _token, _fp in served] == [54282, 54282]
    assert served[0][1:] == served[1][1:]
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


@pytest.mark.parametrize(
    ("holder", "expected"),
    [
        ("posix:1000", "this account"),
        ("posix:1001", "another account"),
        (PermissionError("access denied"), "owner could not be determined"),
    ],
)
def test_port_conflict_diagnostic_names_three_owners(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    holder: str | Exception,
    expected: str,
) -> None:
    import hive._daemon as daemon
    import hive._endpoint as endpoint

    def lookup(port: int) -> str:
        assert port == 54282
        if isinstance(holder, Exception):
            raise holder
        return holder

    _patch_state_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "_startup_self_heal", lambda vault: None)
    monkeypatch.setattr(daemon, "configured_daemon_port", lambda: 54282)
    monkeypatch.setattr(daemon, "_port_available", lambda host, port: False)
    monkeypatch.setattr(endpoint, "_listener_identity", lookup)
    monkeypatch.setattr(endpoint, "current_user_identity", lambda: "posix:1000")

    assert daemon.run_serve() != 0
    message = capsys.readouterr().err
    assert expected in message
    others = {"this account", "another account", "owner could not be determined"} - {expected}
    assert not any(other in message for other in others)
    # Only the class is shown, never who: no UID, SID or account name.
    assert "1000" not in message
    assert "1001" not in message
    assert os.environ.get("USER", "\0") not in message


def test_proc_lookup_ignores_a_listener_on_another_address(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only a listener that holds 127.0.0.1:<port> is the port's holder."""
    import hive._endpoint as endpoint

    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"
    row = "   0: {addr}:D4CA 00000000:0000 0A 00000000:00000000 00:00000000 00000000  {uid}\n"
    tcp = tmp_path / "tcp"
    # 10.11.12.13:54474 held by uid 1001 does not hold the loopback port.
    tcp.write_text(header + row.format(addr="0D0C0B0A", uid=1001), encoding="ascii")
    monkeypatch.setattr(endpoint, "_PROC_TCP_TABLES", (str(tcp),))
    assert endpoint._proc_listener_identity(54474) is None

    tcp.write_text(
        header + row.format(addr="0D0C0B0A", uid=1001) + row.format(addr="0100007F", uid=1000),
        encoding="ascii",
    )
    assert endpoint._proc_listener_identity(54474) == "posix:1000"

    tcp.write_text(header + row.format(addr="00000000", uid=1002), encoding="ascii")
    assert endpoint._proc_listener_identity(54474) == "posix:1002"


def test_windows_lookup_ignores_a_listener_on_another_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows lookup applies the same loopback-only rule as /proc."""
    from types import SimpleNamespace

    import psutil

    import hive._endpoint as endpoint

    def listener(ip: str, pid: int) -> SimpleNamespace:
        return SimpleNamespace(
            status=psutil.CONN_LISTEN, laddr=SimpleNamespace(ip=ip, port=54474), pid=pid
        )

    sids = {1001: "S-1-5-21-1-2-3-1001", 1000: "S-1-5-21-1-2-3-1000"}
    monkeypatch.setattr(endpoint, "_windows_process_sid", lambda pid: sids[pid])
    # 10.11.12.13:54474 held by another account does not hold the loopback port.
    monkeypatch.setattr(psutil, "net_connections", lambda kind: [listener("10.11.12.13", 1001)])
    assert endpoint._windows_listener_identity(54474) is None

    monkeypatch.setattr(
        psutil,
        "net_connections",
        lambda kind: [listener("10.11.12.13", 1001), listener("127.0.0.1", 1000)],
    )
    assert endpoint._windows_listener_identity(54474) == "windows:S-1-5-21-1-2-3-1000"
