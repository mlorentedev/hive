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


def test_render_windows_task_xml_uses_s4u_principal_and_supervisor_loop() -> None:
    """ADR-015: Task Scheduler's `<RestartOnFailure>` does NOT restart on an
    application's exit 75 (it reacts to the engine failing to launch), so it
    cannot map `systemd Restart=on-failure`. The action is instead an inline
    PowerShell supervisor loop that relaunches `hive serve` on a non-zero exit
    and stops on a clean exit 0. An S4U principal runs it in session 0 — no
    console window — non-elevated. Both validated on real Windows hardware
    (2026-06-04)."""
    from hive._service import render_windows_task_xml

    xml = render_windows_task_xml(r"C:\Users\u\hive.exe", user="WINBOX\\u")

    # Windowless, non-elevated: a session-0 S4U principal.
    assert "<LogonType>S4U</LogonType>" in xml
    assert "<UserId>WINBOX\\u</UserId>" in xml
    assert '<Actions Context="Author">' in xml
    # Restart mechanism = supervisor loop (the `<RestartOnFailure>` is kept only
    # as a secondary net for an engine-launch failure).
    assert "<Command>powershell.exe</Command>" in xml
    assert "while($true)" in xml
    assert r"'C:\Users\u\hive.exe' serve" in xml
    assert "if($LASTEXITCODE -eq 0){break}" in xml  # clean exit 0 → stop, no relaunch
    # LogonTrigger auto-starts at login; a daemon runs indefinitely.
    assert "<LogonTrigger>" in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml


def test_render_startup_vbs_launches_supervisor_windowless() -> None:
    """The Startup-folder fallback (#252) launches the daemon with no window:
    WScript.Shell.Run(..., 0, False) → hidden, non-blocking. It carries the same
    supervisor loop as the Scheduled Task and needs no admin (shell:startup)."""
    from hive._service import render_startup_vbs

    vbs = render_startup_vbs(r"C:\Users\u\hive.exe")

    assert 'CreateObject("WScript.Shell")' in vbs
    assert ".Run " in vbs
    assert ", 0, False" in vbs  # window style 0 (hidden), no wait
    assert "-WindowStyle Hidden" in vbs
    assert r"'C:\Users\u\hive.exe' serve" in vbs
    assert "while($true)" in vbs
    assert "if($LASTEXITCODE -eq 0){break}" in vbs  # clean exit 0 → stop


def test_supervisor_loop_is_shared_by_task_and_startup_vbs() -> None:
    """The relaunch loop is a single SSOT — the Scheduled Task XML and the
    Startup-folder VBS render the identical supervisor command (no duplication)."""
    from xml.sax.saxutils import escape

    from hive._service import (
        _supervisor_ps_command,
        render_startup_vbs,
        render_windows_task_xml,
    )

    loop = _supervisor_ps_command(r"C:\hive.exe", "serve")
    assert loop in render_startup_vbs(r"C:\hive.exe")
    # The task XML XML-escapes its arguments, so compare the escaped form.
    assert escape(loop) in render_windows_task_xml(r"C:\hive.exe")


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
    assert "<LogonType>S4U</LogonType>" in str(recorded["xml"])
    assert "while($true)" in str(recorded["xml"])


def test_install_windows_falls_back_to_startup_when_schtasks_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When Task Scheduler is policy-locked (schtasks fails), install falls back
    to a per-user Startup-folder launcher and reports success (#252 bug 2)."""
    import hive._service as svc

    vbs_path = tmp_path / "Startup" / "hive-serve.vbs"
    monkeypatch.setattr(svc, "_platform", lambda: "windows")
    monkeypatch.setattr(svc, "_resolve_exec", lambda: r"C:\hive.exe")
    monkeypatch.setattr(svc, "_schtasks_create", lambda *a, **k: 1)  # policy-locked
    monkeypatch.setattr(svc, "startup_vbs_path", lambda: vbs_path)

    rc = svc.install_service(enable=True)

    assert rc == 0
    assert vbs_path.exists()
    assert "serve" in vbs_path.read_text(encoding="utf-8")


def test_install_windows_fallback_failure_is_not_silent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If the Startup fallback can't be written either, install returns non-zero
    with an actionable message — never a silent success (the #246 lesson)."""
    import hive._service as svc

    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")  # mkdir under a file raises OSError
    vbs_path = blocker / "sub" / "hive-serve.vbs"
    monkeypatch.setattr(svc, "_platform", lambda: "windows")
    monkeypatch.setattr(svc, "_resolve_exec", lambda: r"C:\hive.exe")
    monkeypatch.setattr(svc, "_schtasks_create", lambda *a, **k: 1)
    monkeypatch.setattr(svc, "startup_vbs_path", lambda: vbs_path)

    rc = svc.install_service(enable=True)

    assert rc != 0
    assert not vbs_path.exists()
    assert "hive" in capsys.readouterr().err.lower()


def test_uninstall_windows_removes_startup_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Uninstall removes the Startup-folder launcher even when no Scheduled Task
    exists (only the fallback was installed) and still reports success."""
    import hive._service as svc

    vbs_path = tmp_path / "Startup" / "hive-serve.vbs"
    vbs_path.parent.mkdir(parents=True)
    vbs_path.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(svc, "_platform", lambda: "windows")
    monkeypatch.setattr(svc, "startup_vbs_path", lambda: vbs_path)
    monkeypatch.setattr(svc, "_run", lambda *a, **k: 1)  # no Scheduled Task to delete

    rc = svc.uninstall_service()

    assert rc == 0
    assert not vbs_path.exists()


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


# ── _run surfaces failures to the user (#252 bug 1) ──────────────────────


def test_run_prints_child_stderr_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """On a non-zero exit, _run prints the child's stderr to sys.stderr so the
    CLI user sees WHY it failed. Logging is unconfigured in the normal CLI case,
    so the _log.warning alone left `hive service install` exiting 1 with zero
    output (#252)."""
    import subprocess

    import hive._service as svc

    def _fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["schtasks"],
            returncode=1,
            stdout="",
            stderr="ERROR: Access is denied.\n",
        )

    monkeypatch.setattr(svc.subprocess, "run", _fake_run)
    rc = svc._run(["schtasks", "/Create"])
    assert rc == 1
    assert "Access is denied" in capsys.readouterr().err


def test_run_is_quiet_on_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A successful command prints nothing — no spurious stderr noise."""
    import subprocess

    import hive._service as svc

    def _fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["schtasks"], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(svc.subprocess, "run", _fake_run)
    rc = svc._run(["schtasks", "/Query"])
    assert rc == 0
    assert capsys.readouterr().err == ""
