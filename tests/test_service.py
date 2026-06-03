"""Unit tests for `hive service` install/lifecycle (HIVE-118 / ADR-011).

The daemon ships on PyPI independently of any dotfiles, so `hive service`
must work cross-OS. These tests exercise the pure unit/task renderers (which
run identically on any host) and the platform-dispatching install/uninstall
paths with the supervisor invocation mocked — the real systemctl / schtasks
calls are validated by the cross-OS CI matrix (ubuntu + windows).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


# ── pure renderers (host-OS independent) ─────────────────────────────────


def test_render_systemd_unit_encodes_the_exit75_restart_contract() -> None:
    """The systemd unit is the Linux consumer of the slice 1.3 exit-75 contract:
    `Restart=on-failure` restarts on a drift (exit 75) or crash but NOT on a
    clean stop / singleton-decline (exit 0). It auto-starts at login
    (`WantedBy=default.target`) and bakes VAULT_PATH because `systemd --user`
    starts with a minimal environment."""
    from hive._service import render_systemd_unit

    unit = render_systemd_unit("/home/u/.local/bin/hive", vault="/home/u/Projects/knowledge")

    assert "ExecStart=/home/u/.local/bin/hive serve" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit
    assert "Environment=VAULT_PATH=/home/u/Projects/knowledge" in unit
    # An optional (leading `-`) env file lets a user add secrets without baking.
    assert "EnvironmentFile=-" in unit


def test_render_windows_task_xml_has_logon_trigger_and_restart_on_failure() -> None:
    """The Windows Scheduled Task is the per-user analogue of `systemd --user`:
    a LogonTrigger auto-starts it at login and RestartOnFailure restarts it when
    the last run exits non-zero (exit 75 / crash) — the same contract as
    `Restart=on-failure`, with a non-zero exit mapping to a failed run."""
    from hive._service import render_windows_task_xml

    xml = render_windows_task_xml(r"C:\Users\u\hive.exe")

    assert "<LogonTrigger>" in xml
    assert "<RestartOnFailure>" in xml
    assert "<Command>C:\\Users\\u\\hive.exe</Command>" in xml
    assert "<Arguments>serve</Arguments>" in xml
    # A daemon runs indefinitely: no execution time limit.
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml


# ── platform-dispatching install / uninstall / status ────────────────────


def test_install_writes_systemd_unit_and_enables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    calls: list[list[str]] = []
    unit_path = tmp_path / "hive.service"
    monkeypatch.setattr(svc, "_platform", lambda: "linux")
    monkeypatch.setattr(svc, "systemd_unit_path", lambda: unit_path)
    monkeypatch.setattr(svc, "_resolve_exec", lambda: "/usr/bin/hive")
    monkeypatch.setattr(svc, "_systemctl", lambda *a: calls.append(list(a)) or 0)

    rc = svc.install_service(enable=True)

    assert rc == 0
    assert unit_path.exists()
    assert "Restart=on-failure" in unit_path.read_text(encoding="utf-8")
    assert ["daemon-reload"] in calls
    assert any("enable" in c for c in calls)


def test_install_no_enable_writes_unit_but_skips_systemctl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    calls: list[list[str]] = []
    unit_path = tmp_path / "hive.service"
    monkeypatch.setattr(svc, "_platform", lambda: "linux")
    monkeypatch.setattr(svc, "systemd_unit_path", lambda: unit_path)
    monkeypatch.setattr(svc, "_resolve_exec", lambda: "/usr/bin/hive")
    monkeypatch.setattr(svc, "_systemctl", lambda *a: calls.append(list(a)) or 0)

    rc = svc.install_service(enable=False)

    assert rc == 0
    assert unit_path.exists()
    assert calls == []  # --no-enable must not touch the running system


def test_install_windows_registers_scheduled_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hive._service as svc

    recorded: dict[str, object] = {}
    monkeypatch.setattr(svc, "_platform", lambda: "windows")
    monkeypatch.setattr(svc, "_resolve_exec", lambda: r"C:\hive.exe")

    def _fake_schtasks(*args: str, xml: str = "") -> int:
        recorded["args"] = args
        recorded["xml"] = xml
        return 0

    monkeypatch.setattr(svc, "_schtasks_create", _fake_schtasks)
    rc = svc.install_service(enable=True)

    assert rc == 0
    assert "<LogonTrigger>" in str(recorded["xml"])
    assert "<RestartOnFailure>" in str(recorded["xml"])


def test_install_macos_is_a_clear_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """launchd is a documented follow-up (not in the ubuntu+windows CI matrix):
    install must not silently no-op — it returns a non-zero code and prints the
    manual step rather than pretending to have installed anything."""
    import hive._service as svc

    monkeypatch.setattr(svc, "_platform", lambda: "darwin")
    rc = svc.install_service(enable=True)

    assert rc != 0


def test_uninstall_linux_removes_unit_and_disables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    calls: list[list[str]] = []
    unit_path = tmp_path / "hive.service"
    unit_path.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(svc, "_platform", lambda: "linux")
    monkeypatch.setattr(svc, "systemd_unit_path", lambda: unit_path)
    monkeypatch.setattr(svc, "_systemctl", lambda *a: calls.append(list(a)) or 0)

    rc = svc.uninstall_service()

    assert rc == 0
    assert not unit_path.exists()
    assert any("disable" in c for c in calls)


# ── CLI dispatch (`hive service ...`) ────────────────────────────────────


def test_cli_service_install_passes_no_enable_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`hive service install --no-enable` must reach install_service(enable=False)
    and return its exit code unchanged (the dotfiles installer reads it)."""
    from hive import server

    seen: dict[str, object] = {}

    def _fake_install(*, enable: bool) -> int:
        seen["enable"] = enable
        return 0

    monkeypatch.setattr("hive._service.install_service", _fake_install)
    rc = server._run_service(["install", "--no-enable"])

    assert rc == 0
    assert seen["enable"] is False


def test_cli_service_status_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    from hive import server

    monkeypatch.setattr("hive._service.service_status", lambda: 3)
    assert server._run_service(["status"]) == 3
