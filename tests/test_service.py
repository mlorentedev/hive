"""Unit tests for `hive service` install/lifecycle (HIVE-118 / ADR-011).

The daemon ships on PyPI independently of any dotfiles, so `hive service`
must work cross-OS. These tests exercise the pure unit/task renderers (which
run identically on any host) and the platform-dispatching install/uninstall
paths with the supervisor invocation mocked — the real systemctl / schtasks
calls are validated by the cross-OS CI matrix (ubuntu + windows).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_service_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Every install reads the recorded exec prefix; no test may read or write
    the developer's own."""
    import hive._service as svc

    path = tmp_path / "service-config" / "service.json"
    monkeypatch.setattr(svc, "service_config_path", lambda: path)
    return path


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
    monkeypatch.setattr(svc, "_await_daemon", lambda deadline_s=30.0: "healthy")

    rc = svc.install_service(enable=True)

    assert rc == 0
    assert unit_path.exists()
    assert "Restart=on-failure" in unit_path.read_text(encoding="utf-8")
    assert ["daemon-reload"] in calls
    assert any("enable" in c for c in calls)


def test_install_systemd_whose_daemon_never_answers_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`enable --now` succeeding says systemd started a process, not that the
    daemon answers; install promises the latter (#479)."""
    import hive._service as svc

    monkeypatch.setattr(svc, "_platform", lambda: "linux")
    monkeypatch.setattr(svc, "systemd_unit_path", lambda: tmp_path / "hive.service")
    monkeypatch.setattr(svc, "_resolve_exec", lambda: "/usr/bin/hive")
    monkeypatch.setattr(svc, "_systemctl", lambda *a: 0)
    monkeypatch.setattr(svc, "_await_daemon", lambda deadline_s=30.0: "down")

    rc = svc.install_service(enable=True)

    assert rc != 0
    assert "the daemon is down" in capsys.readouterr().err


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
    runs: list[list[str]] = []
    monkeypatch.setattr(svc, "_run", lambda cmd, **_: runs.append(cmd) or 0)
    monkeypatch.setattr(svc, "_await_daemon", lambda deadline_s=30.0: "healthy")
    rc = svc.install_service(enable=True)

    assert rc == 0
    assert "<LogonTrigger>" in str(recorded["xml"])
    assert "<LogonType>S4U</LogonType>" in str(recorded["xml"])
    assert "while($true)" in str(recorded["xml"])
    # A second /Run while the daemon runs must not start a second instance.
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in str(recorded["xml"])
    # Parity with systemd's `enable --now`: the daemon runs before the next logon.
    assert runs == [["schtasks", "/Run", "/TN", svc.WINDOWS_TASK_NAME]]


def test_install_windows_that_cannot_start_the_task_is_not_silent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A registered task that does not start leaves every `hive client` failing
    until the next logon, so install says so and returns non-zero."""
    import hive._service as svc

    monkeypatch.setattr(svc, "_platform", lambda: "windows")
    monkeypatch.setattr(svc, "_resolve_exec", lambda: r"C:\hive.exe")
    monkeypatch.setattr(svc, "_schtasks_create", lambda *a, **k: 0)
    monkeypatch.setattr(svc, "_run", lambda cmd, **_: 1)
    waits: list[float] = []
    monkeypatch.setattr(
        svc, "_await_daemon", lambda deadline_s=30.0: waits.append(deadline_s) or "down"
    )

    rc = svc.install_service(enable=True)

    assert rc != 0
    assert "schtasks /Run" in capsys.readouterr().err
    assert waits == [0.0]  # nothing was started, so one probe, not the full wait


def test_install_windows_with_the_daemon_already_running_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-installing while the task runs: whatever /Run reports for a running
    instance, the daemon answers, and that is the verdict."""
    import hive._service as svc

    monkeypatch.setattr(svc, "_platform", lambda: "windows")
    monkeypatch.setattr(svc, "_resolve_exec", lambda: r"C:\hive.exe")
    monkeypatch.setattr(svc, "_schtasks_create", lambda *a, **k: 0)
    monkeypatch.setattr(svc, "_run", lambda cmd, **_: 1)
    monkeypatch.setattr(svc, "_await_daemon", lambda deadline_s=30.0: "healthy")

    assert svc.install_service(enable=True) == 0


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
    spawned: list[list[str]] = []
    monkeypatch.setattr(svc, "_spawn_detached", lambda cmd: spawned.append(cmd) or 0)
    monkeypatch.setattr(svc, "_await_daemon", lambda deadline_s=30.0: "healthy")

    rc = svc.install_service(enable=True)

    assert rc == 0
    assert vbs_path.exists()
    assert "serve" in vbs_path.read_text(encoding="utf-8")
    # Started now through the launcher logon will run, not left for the next logon.
    assert spawned == [["wscript.exe", str(vbs_path)]]


def test_install_windows_fallback_that_cannot_start_is_not_silent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import hive._service as svc

    vbs_path = tmp_path / "Startup" / "hive-serve.vbs"
    monkeypatch.setattr(svc, "_platform", lambda: "windows")
    monkeypatch.setattr(svc, "_resolve_exec", lambda: r"C:\hive.exe")
    monkeypatch.setattr(svc, "_schtasks_create", lambda *a, **k: 1)
    monkeypatch.setattr(svc, "startup_vbs_path", lambda: vbs_path)
    monkeypatch.setattr(svc, "_spawn_detached", lambda cmd: 1)

    rc = svc.install_service(enable=True)

    assert rc != 0
    assert vbs_path.exists()
    assert "the daemon is not started" in capsys.readouterr().err


def test_install_windows_fallback_whose_daemon_never_answers_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The launcher starting is not the daemon answering: a .vbs that runs and
    then fails (stale path, script error) must not read as a success."""
    import hive._service as svc

    vbs_path = tmp_path / "Startup" / "hive-serve.vbs"
    monkeypatch.setattr(svc, "_platform", lambda: "windows")
    monkeypatch.setattr(svc, "_resolve_exec", lambda: r"C:\hive.exe")
    monkeypatch.setattr(svc, "_schtasks_create", lambda *a, **k: 1)
    monkeypatch.setattr(svc, "startup_vbs_path", lambda: vbs_path)
    monkeypatch.setattr(svc, "_spawn_detached", lambda cmd: 0)
    monkeypatch.setattr(svc, "_await_daemon", lambda deadline_s=30.0: "down")

    rc = svc.install_service(enable=True)

    assert rc != 0
    assert "the daemon is down" in capsys.readouterr().err


def test_await_daemon_polls_until_the_daemon_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    import hive._service as svc

    states = iter(["down", "down", "healthy"])
    monkeypatch.setattr(svc, "daemon_state", lambda: next(states))
    monkeypatch.setattr(svc.time, "sleep", lambda _s: None)

    assert svc._await_daemon(30.0) == "healthy"


def test_await_daemon_with_no_deadline_probes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import hive._service as svc

    probes: list[str] = []
    monkeypatch.setattr(svc, "daemon_state", lambda: probes.append("p") or "down")

    assert svc._await_daemon(0.0) == "down"
    assert probes == ["p"]


def test_run_that_times_out_fails_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import subprocess

    import hive._service as svc

    def _hang(cmd: list[str], **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])  # type: ignore[arg-type]

    monkeypatch.setattr(svc.subprocess, "run", _hang)

    assert svc._run(["schtasks", "/Run"], timeout=10.0) == 1
    assert "did not finish within 10s" in capsys.readouterr().err


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


def test_install_on_an_unsupported_platform_is_not_silent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A platform with no supervisor must not silently no-op: install returns a
    non-zero code and names the manual step instead of pretending."""
    import hive._service as svc

    monkeypatch.setattr(svc, "_platform", lambda: "freebsd")
    monkeypatch.setattr(svc, "_resolve_exec", lambda: "/usr/local/bin/hive")
    rc = svc.install_service(enable=True)

    assert rc != 0
    assert "/usr/local/bin/hive serve" in capsys.readouterr().err


# ── macOS: launchd agent ──────────────────────────────────────────────────


def test_render_launchd_plist_encodes_the_exit75_restart_contract() -> None:
    """launchd's spelling of `Restart=on-failure`: RunAtLoad starts it, and
    KeepAlive {SuccessfulExit: false} restarts it only after a non-zero exit
    (drift 75 or crash), never after a clean stop / singleton-decline (0)."""
    import plistlib

    from hive._service import LAUNCHD_LABEL, render_launchd_plist

    plist = plistlib.loads(
        render_launchd_plist(
            ["/Users/u/.local/bin/hive"], vault="/Users/u/vault", log="/l/serve.log"
        )
    )

    assert plist["Label"] == LAUNCHD_LABEL
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["ThrottleInterval"] > 0
    assert plist["EnvironmentVariables"] == {"VAULT_PATH": "/Users/u/vault"}
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == "/l/serve.log"
    assert plist["ProgramArguments"][-2:] == ["/Users/u/.local/bin/hive", "serve"]


def test_render_launchd_plist_keeps_each_argument_its_own_element() -> None:
    """launchd never word-splits ProgramArguments: the module fallback joined
    into one element would be a daemon that never starts, and an interpreter
    path holding a space must stay one element."""
    import plistlib

    from hive._service import render_launchd_plist

    argv = ["/Applications/My Python/bin/python3", "-m", "hive.server"]
    plist = plistlib.loads(render_launchd_plist(argv, vault="/v", log="/l"))

    assert plist["ProgramArguments"][-4:] == [*argv, "serve"]


@pytest.mark.skipif(sys.platform == "win32", reason="runs the /bin/sh wrapper launchd runs")
def test_render_launchd_plist_sources_secrets_and_never_bakes_them(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Secrets come from the same optional, owner-only hive.env the systemd
    unit reads; the plist carries its path, never a value from it."""
    import plistlib
    import subprocess

    from hive._service import render_launchd_plist

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    env_file = tmp_path / "hive" / "hive.env"
    env_file.parent.mkdir()
    env_file.write_text("HIVE_WORKER_API_KEY=synthetic-not-a-secret\n", encoding="utf-8")

    raw = render_launchd_plist(["/usr/bin/env"], vault="/v", log="/l")
    assert b"synthetic-not-a-secret" not in raw

    # Run the wrapper as launchd would, with `env` standing in for the daemon:
    # the variable reaches the process, and a missing file is not an error.
    args = plistlib.loads(raw)["ProgramArguments"][:-1]
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    assert "HIVE_WORKER_API_KEY=synthetic-not-a-secret" in out
    env_file.unlink()
    assert subprocess.run(args, check=False, capture_output=True).returncode == 0


def _darwin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    loaded: bool,
    bootstrap_rc: int = 0,
    state: str = "healthy",
) -> tuple[list[list[str]], Path]:
    import hive._service as svc

    calls: list[list[str]] = []
    plist_path = tmp_path / "LaunchAgents" / f"{svc.LAUNCHD_LABEL}.plist"

    state_loaded = {"v": loaded}

    def _launchctl(*args: str, quiet: bool = False) -> int:
        calls.append(list(args))
        if args[0] == "bootout":
            state_loaded["v"] = False
        if args[0] == "print":
            return 0 if state_loaded["v"] else 113
        if args[0] == "bootstrap":
            return bootstrap_rc
        return 0

    monkeypatch.setattr(svc, "_platform", lambda: "darwin")
    monkeypatch.setattr(svc, "launchd_plist_path", lambda: plist_path)
    monkeypatch.setattr(svc, "launchd_log_path", lambda: tmp_path / "Logs" / "serve.log")
    monkeypatch.setattr(svc, "_resolve_argv", lambda: ["/usr/local/bin/hive"])
    monkeypatch.setattr(svc, "_launchd_domain", lambda: "gui/501")
    monkeypatch.setattr(svc, "_launchctl", _launchctl)
    monkeypatch.setattr(svc, "_await_daemon", lambda deadline_s=30.0: state)
    return calls, plist_path


def _verbs(calls: list[list[str]]) -> list[str]:
    return [c[0] for c in calls]


def test_install_launchd_loads_and_starts_the_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    calls, plist_path = _darwin(monkeypatch, tmp_path, loaded=False)

    rc = svc.install_service(enable=True)

    assert rc == 0
    assert plist_path.exists()
    assert (tmp_path / "Logs").is_dir()  # launchd does not create the log dir
    assert ["bootstrap", "gui/501", str(plist_path)] in calls
    assert "bootout" not in _verbs(calls)


def test_install_launchd_twice_leaves_the_running_daemon_alone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A repeated install with an unchanged plist reloads nothing, so a
    converge that runs it every time does not drop live sessions."""
    import hive._service as svc

    _, plist_path = _darwin(monkeypatch, tmp_path, loaded=False)
    assert svc.install_service(enable=True) == 0
    calls, _ = _darwin(monkeypatch, tmp_path, loaded=True)

    assert svc.install_service(enable=True) == 0
    assert "bootout" not in _verbs(calls)
    assert "bootstrap" not in _verbs(calls)
    # kickstart without -k: starts a stopped daemon, never kills a running one.
    assert ["kickstart", f"gui/501/{svc.LAUNCHD_LABEL}"] in calls


def test_install_launchd_reloads_a_changed_definition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    calls, plist_path = _darwin(monkeypatch, tmp_path, loaded=True)
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(b"an older definition")

    assert svc.install_service(enable=True) == 0
    verbs = _verbs(calls)
    # bootout returns before launchd finishes; bootstrap only once it is gone.
    assert verbs.index("bootout") < verbs.index("bootstrap")
    assert "print" in verbs[verbs.index("bootout") : verbs.index("bootstrap")]


def test_install_launchd_that_never_unloads_does_not_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bootstrap into a job still being torn down fails with 5: I/O error
    and leaves the daemon down; past the deadline install stops and says so."""
    import hive._service as svc

    calls: list[list[str]] = []
    plist_path = tmp_path / "LaunchAgents" / f"{svc.LAUNCHD_LABEL}.plist"
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(b"an older definition")
    monkeypatch.setattr(svc, "_platform", lambda: "darwin")
    monkeypatch.setattr(svc, "launchd_plist_path", lambda: plist_path)
    monkeypatch.setattr(svc, "launchd_log_path", lambda: tmp_path / "Logs" / "serve.log")
    monkeypatch.setattr(svc, "_resolve_argv", lambda: ["/usr/local/bin/hive"])
    monkeypatch.setattr(svc, "_launchd_domain", lambda: "gui/501")
    monkeypatch.setattr(svc, "_launchctl", lambda *a, quiet=False: calls.append(list(a)) or 0)
    monkeypatch.setattr(svc, "_UNLOAD_DEADLINE_S", 0.0)

    assert svc.install_service(enable=True) == 1
    assert "bootstrap" not in _verbs(calls)
    assert "did not unload" in capsys.readouterr().err
    # The old definition is back, so a re-run sees the change and reloads.
    assert plist_path.read_bytes() == b"an older definition"


def test_install_launchd_outside_a_console_session_says_why(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """From an SSH-only session the gui/<uid> domain does not exist and
    bootstrap fails; install must say so, not report success."""
    import hive._service as svc

    _darwin(monkeypatch, tmp_path, loaded=False, bootstrap_rc=5)

    assert svc.install_service(enable=True) == 5
    assert "console" in capsys.readouterr().err


def test_install_launchd_whose_daemon_never_answers_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import hive._service as svc

    _darwin(monkeypatch, tmp_path, loaded=False, state="down")

    assert svc.install_service(enable=True) == 1
    assert "the daemon is down" in capsys.readouterr().err


def test_install_launchd_no_enable_writes_the_agent_but_loads_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    calls, plist_path = _darwin(monkeypatch, tmp_path, loaded=False)

    assert svc.install_service(enable=False) == 0
    assert plist_path.exists()
    assert calls == []


def test_uninstall_launchd_unloads_and_removes_the_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    calls, plist_path = _darwin(monkeypatch, tmp_path, loaded=True)
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(b"x")

    assert svc.uninstall_service() == 0
    assert not plist_path.exists()
    assert ["bootout", f"gui/501/{svc.LAUNCHD_LABEL}"] in calls


def test_uninstall_launchd_that_never_unloads_keeps_the_agent_and_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import hive._service as svc

    plist_path = tmp_path / f"{svc.LAUNCHD_LABEL}.plist"
    plist_path.write_bytes(b"x")
    monkeypatch.setattr(svc, "_platform", lambda: "darwin")
    monkeypatch.setattr(svc, "launchd_plist_path", lambda: plist_path)
    monkeypatch.setattr(svc, "_launchd_domain", lambda: "gui/501")
    monkeypatch.setattr(svc, "_launchctl", lambda *a, quiet=False: 0)
    monkeypatch.setattr(svc, "_UNLOAD_DEADLINE_S", 0.0)

    assert svc.uninstall_service() == 1
    assert plist_path.exists()
    assert "still loaded" in capsys.readouterr().err


def test_uninstall_launchd_when_nothing_is_loaded_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    calls, _ = _darwin(monkeypatch, tmp_path, loaded=False)

    assert svc.uninstall_service() == 0
    assert "bootout" not in _verbs(calls)


def test_status_launchd_shows_the_agent_then_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hive._service as svc

    shown: list[list[str]] = []
    monkeypatch.setattr(svc, "_platform", lambda: "darwin")
    monkeypatch.setattr(svc, "_launchd_domain", lambda: "gui/501")
    monkeypatch.setattr(svc, "_run_passthrough", lambda cmd: shown.append(cmd) or 0)
    monkeypatch.setattr(svc, "daemon_state", lambda: "healthy")

    assert svc.service_status() == 0
    assert shown == [["launchctl", "print", f"gui/501/{svc.LAUNCHD_LABEL}"]]


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

    def _fake_install(*, enable: bool, **_: object) -> int:
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


# ── exec prefix: start the daemon through a launcher (#540) ──────────────

_PREFIX = ["dotf", "secrets", "run", "--only", "NAN_API_KEY", "--skip-locked", "--"]


def _launcher(tmp_path: Path, name: str = "dotf") -> str:
    """A real file standing in for the launcher, so resolution needs no PATH."""
    path = tmp_path / "bin" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    import hive._service as svc

    unit_path = tmp_path / "hive.service"
    monkeypatch.setattr(svc, "_platform", lambda: "linux")
    monkeypatch.setattr(svc, "systemd_unit_path", lambda: unit_path)
    monkeypatch.setattr(svc, "_resolve_exec", lambda: "/usr/bin/hive")
    monkeypatch.setattr(svc, "_systemctl", lambda *a: 0)
    monkeypatch.setattr(svc, "_await_daemon", lambda deadline_s=30.0: "healthy")
    return unit_path


def _exec_start(unit_path: Path) -> str:
    return next(
        line.removeprefix("ExecStart=")
        for line in unit_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("ExecStart=")
    )


def test_systemd_command_line_keeps_each_prefix_element_one_argument() -> None:
    """systemd splits ExecStart on whitespace and expands `%` and `$`, so an
    element holding any of them is quoted or escaped, never passed raw."""
    from hive._service import _systemd_command_line

    assert _systemd_command_line(["/a/dotf", "run", "--"]) == "/a/dotf run --"
    assert _systemd_command_line(["/My Tools/dotf", "100%", "$HOME"]) == (
        '"/My Tools/dotf" 100%% $$HOME'
    )


def test_supervisor_loop_runs_the_prefix_then_hive() -> None:
    """Each element is its own single-quoted PowerShell argument, so the loop
    still reads the exit code of the whole launch."""
    from hive._service import _supervisor_ps_command

    loop = _supervisor_ps_command(r"C:\hive.exe", "serve", prefix=[r"C:\dotf.exe", "it's", "--"])

    assert r"& 'C:\dotf.exe' 'it''s' '--' 'C:\hive.exe' serve;" in loop
    assert "if($LASTEXITCODE -eq 0){break}" in loop


def test_windows_task_and_startup_fallback_carry_the_same_prefixed_loop() -> None:
    from xml.sax.saxutils import escape

    from hive._service import _supervisor_ps_command, render_startup_vbs, render_windows_task_xml

    prefix = [r"C:\dotf.exe", "secrets", "run", "--"]
    loop = _supervisor_ps_command(r"C:\hive.exe", "serve", prefix=prefix)

    assert loop in render_startup_vbs(r"C:\hive.exe", prefix=prefix)
    assert escape(loop) in render_windows_task_xml(r"C:\hive.exe", prefix=prefix)


def test_install_records_the_prefix_and_runs_it_in_the_unit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _isolated_service_config: Path,
) -> None:
    import hive._service as svc

    unit_path = _linux(monkeypatch, tmp_path)
    dotf = _launcher(tmp_path)

    assert svc.install_service(enable=True, exec_prefix=[dotf, *_PREFIX[1:]]) == 0

    expected = svc._systemd_command_line([dotf, *_PREFIX[1:]])
    assert _exec_start(unit_path) == f"{expected} /usr/bin/hive serve"
    assert svc.load_exec_prefix() == [dotf, *_PREFIX[1:]]
    if sys.platform != "win32":
        assert _isolated_service_config.stat().st_mode & 0o777 == 0o600


def test_a_reinstall_without_the_flag_keeps_the_recorded_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The upgrade timer and setup scripts run a bare `hive service install`;
    the prefix must survive them, or the credential is lost on the next upgrade."""
    import hive._service as svc

    unit_path = _linux(monkeypatch, tmp_path)
    dotf = _launcher(tmp_path)
    svc.install_service(enable=True, exec_prefix=[dotf, "run", "--"])
    first = unit_path.read_text(encoding="utf-8")
    unit_path.unlink()

    assert svc.install_service(enable=True) == 0
    assert unit_path.read_text(encoding="utf-8") == first


def test_clearing_the_prefix_restores_the_plain_unit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _isolated_service_config: Path,
) -> None:
    import hive._service as svc

    unit_path = _linux(monkeypatch, tmp_path)
    svc.install_service(enable=True, exec_prefix=[_launcher(tmp_path), "--"])

    assert svc.install_service(enable=True, clear_exec_prefix=True) == 0
    assert _exec_start(unit_path) == "/usr/bin/hive serve"
    assert not _isolated_service_config.exists()


def test_a_launcher_that_moved_is_found_again_by_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    unit_path = _linux(monkeypatch, tmp_path)
    moved = _launcher(tmp_path / "new")
    monkeypatch.setattr(svc.shutil, "which", lambda name: moved if name == "dotf" else None)

    assert svc.install_service(enable=True, exec_prefix=["/gone/bin/dotf", "--"]) == 0
    assert (
        _exec_start(unit_path) == f"{svc._systemd_command_line([moved, '--'])} /usr/bin/hive serve"
    )
    assert svc.load_exec_prefix() == ["/gone/bin/dotf", "--"]  # recorded as given


def test_a_launcher_that_cannot_be_found_is_refused_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    _isolated_service_config: Path,
) -> None:
    import hive._service as svc

    unit_path = _linux(monkeypatch, tmp_path)
    monkeypatch.setattr(svc.shutil, "which", lambda name: None)

    rc = svc.install_service(enable=True, exec_prefix=["no-such-launcher", "--"])

    assert rc != 0
    assert "no-such-launcher" in capsys.readouterr().err
    assert not unit_path.exists()
    assert not _isolated_service_config.exists()


def test_windows_refuses_a_prefix_its_loop_cannot_quote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A double quote would end the `-Command "..."` argument early, so the
    task would run something other than what was recorded."""
    import hive._service as svc

    registered: list[str] = []
    monkeypatch.setattr(svc, "_platform", lambda: "windows")
    monkeypatch.setattr(svc, "_resolve_exec", lambda: r"C:\hive.exe")
    monkeypatch.setattr(svc, "_schtasks_create", lambda name, *, xml: registered.append(xml) or 0)

    rc = svc.install_service(enable=True, exec_prefix=[_launcher(tmp_path), 'say "hi"'])

    assert rc != 0
    assert 'say "hi"' in capsys.readouterr().err
    assert registered == []


@pytest.mark.parametrize("smart", ["‘", "’", "‚", "‛"])
def test_windows_refuses_a_smart_quote_its_loop_would_read_as_a_delimiter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    smart: str,
) -> None:
    """PowerShell ends a single-quoted string on any of these, not only `'`."""
    import hive._service as svc

    monkeypatch.setattr(svc, "_platform", lambda: "windows")

    with pytest.raises(svc.ExecPrefixError):
        svc._resolve_exec_prefix([_launcher(tmp_path), f"it{smart}s"])


def test_a_relative_launcher_is_rendered_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`shutil.which` answers a path with a separator as given, and systemd
    refuses a relative executable, so the rendered launcher is made absolute."""
    import hive._service as svc

    _launcher(tmp_path)
    monkeypatch.chdir(tmp_path)
    relative = os.path.join("bin", "dotf")
    monkeypatch.setattr(svc.shutil, "which", lambda name: relative if name == relative else None)

    resolved = svc._resolve_exec_prefix([relative, "--"])

    assert Path(resolved[0]).is_absolute()
    assert resolved[0] == os.path.abspath(relative)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_launcher_found_by_name_keeps_its_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Homebrew or uv-tool launcher is a link to a versioned install; the
    definition must run the link, or the next upgrade breaks it."""
    import hive._service as svc

    target = _launcher(tmp_path / "real", name="dotf-1.2")
    link = tmp_path / "bin" / "dotf"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    monkeypatch.setattr(svc.shutil, "which", lambda name: str(link) if name == "dotf" else None)

    assert svc._resolve_exec_prefix(["dotf", "--"]) == [str(link), "--"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no execute bit")
def test_a_launcher_that_is_not_executable_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    launcher = _launcher(tmp_path)
    Path(launcher).chmod(0o644)
    monkeypatch.setattr(svc.shutil, "which", lambda name: None)

    with pytest.raises(svc.ExecPrefixError):
        svc._resolve_exec_prefix([launcher, "--"])


@pytest.mark.parametrize("bad", ["", "a\nb", "a\rb"])
def test_an_element_no_supervisor_can_carry_is_refused_on_every_platform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    _isolated_service_config: Path,
    bad: str,
) -> None:
    """An empty element is dropped by systemd and refused by the loader, so
    recording it would break every later install; a line break ends the unit's
    ExecStart line. Both are refused before anything is written."""
    import hive._service as svc

    unit_path = _linux(monkeypatch, tmp_path)

    assert svc.install_service(enable=True, exec_prefix=[_launcher(tmp_path), bad, "--"]) != 0
    assert "nothing was installed" in capsys.readouterr().err
    assert not unit_path.exists()
    assert not _isolated_service_config.exists()


def test_systemd_quotes_a_bare_semicolon() -> None:
    from hive._service import _systemd_command_line

    assert _systemd_command_line(["/a/dotf", ";", "--"]) == '/a/dotf ";" --'


def test_a_failed_record_write_keeps_the_previous_record(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_service_config: Path,
) -> None:
    """The record is replaced atomically: a write that fails part-way leaves the
    old one, never an empty file that blocks every later install."""
    import hive._service as svc

    svc.save_exec_prefix(["dotf", "run", "--"])

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(svc.json, "dump", _boom)
    with pytest.raises(OSError):
        svc.save_exec_prefix(["other", "--"])

    assert svc.load_exec_prefix() == ["dotf", "run", "--"]
    assert list(_isolated_service_config.parent.iterdir()) == [_isolated_service_config]


def test_an_unreadable_record_is_refused_and_named(
    monkeypatch: pytest.MonkeyPatch,
    _isolated_service_config: Path,
) -> None:
    import hive._service as svc

    _isolated_service_config.mkdir(parents=True)  # a directory where the file goes

    with pytest.raises(svc.ExecPrefixError, match="service.json"):
        svc.load_exec_prefix()


def test_install_launchd_runs_the_prefix_inside_the_env_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The prefix goes after the env file, so `exec "$@"` starts the prefix and
    hive.env keeps working beside it."""
    import plistlib

    import hive._service as svc

    _, plist_path = _darwin(monkeypatch, tmp_path, loaded=False)
    dotf = _launcher(tmp_path)

    assert svc.install_service(enable=True, exec_prefix=[dotf, "run", "--"]) == 0

    args = plistlib.loads(plist_path.read_bytes())["ProgramArguments"]
    assert args[:2] == ["/bin/sh", "-c"]
    assert args[-5:] == [dotf, "run", "--", "/usr/local/bin/hive", "serve"]


def test_with_no_prefix_every_definition_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hive._service as svc

    unit_path = _linux(monkeypatch, tmp_path)
    svc.install_service(enable=True)
    assert unit_path.read_text(encoding="utf-8") == svc.render_systemd_unit(
        "/usr/bin/hive", vault=str(svc.settings.vault_path)
    )
    assert svc._supervisor_ps_command(r"C:\hive.exe", "serve", prefix=[]) == (
        svc._supervisor_ps_command(r"C:\hive.exe", "serve")
    )


def test_a_malformed_record_is_refused_and_named(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    _isolated_service_config: Path,
) -> None:
    import hive._service as svc

    unit_path = _linux(monkeypatch, tmp_path)
    _isolated_service_config.parent.mkdir(parents=True)
    _isolated_service_config.write_text('{"exec_prefix": "dotf run --"}', encoding="utf-8")

    assert svc.install_service(enable=True) != 0
    assert str(_isolated_service_config) in capsys.readouterr().err
    assert not unit_path.exists()


def test_status_names_the_recorded_prefix(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    _isolated_service_config: Path,
) -> None:
    import hive._service as svc

    monkeypatch.setattr(svc, "_platform", lambda: "linux")
    monkeypatch.setattr(svc, "_run_passthrough", lambda cmd: 0)
    monkeypatch.setattr(svc, "daemon_state", lambda: "healthy")

    svc.service_status()
    assert "exec prefix: none recorded" in capsys.readouterr().out

    svc.save_exec_prefix(["dotf", "run", "--"])
    svc.service_status()
    assert "exec prefix: dotf run --" in capsys.readouterr().out


def test_cli_exec_prefix_takes_everything_after_the_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prefix itself ends in `--`, so the CLI takes the rest of the line
    verbatim instead of letting argparse read it."""
    from hive import server

    seen: dict[str, object] = {}

    def _fake_install(**kwargs: object) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("hive._service.install_service", _fake_install)

    assert server._run_service(["install", "--exec-prefix", "--", *_PREFIX]) == 0
    assert seen == {"enable": True, "exec_prefix": _PREFIX, "clear_exec_prefix": False}

    assert server._run_service(["install", "--no-exec-prefix"]) == 0
    assert seen["exec_prefix"] is None
    assert seen["clear_exec_prefix"] is True


@pytest.mark.parametrize(
    "argv",
    [
        ["install", "--exec-prefix"],
        ["install", "--exec-prefix", "--"],
        ["install", "--exec-prefix", "dotf", "--"],
        ["install", "--no-exec-prefix", "--exec-prefix", "--", "dotf"],
        # Forms argparse would otherwise accept and silently drop the value of.
        ["install", "--exec-prefix=/bin/true"],
        ["install", "--exec", "/bin/true"],
        ["install", "--exec-pre=/bin/true"],
    ],
)
def test_cli_exec_prefix_misuse_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    from hive import server

    monkeypatch.setattr("hive._service.install_service", lambda **_: 0)

    with pytest.raises(SystemExit) as exc:
        server._run_service(argv)
    assert exc.value.code == 2


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


# ── _resolve_exec: what the supervisor is actually registered against (#328) ──


def _seed_layout(root: Path, version: str = "1.43.0", *, with_launcher: bool = True) -> Path:
    """Build a minimal A3 layout under *root* and return the launcher path.

    Uses `_runtime._make_junction` rather than `Path.symlink_to`: on Windows a
    symlink needs SeCreateSymbolicLinkPrivilege (WinError 1314) while a junction
    does not, which is the whole reason A3 uses `mklink /J`. Reusing the
    production seam keeps the fixture honest on both OSes.
    """
    import sys

    from hive import _runtime

    target = root / "versions" / version
    bindir = target / ("Scripts" if sys.platform == "win32" else "bin")
    bindir.mkdir(parents=True)
    launcher = bindir / ("hive.exe" if sys.platform == "win32" else "hive")
    if with_launcher:
        launcher.write_text("", encoding="utf-8")
    _runtime._make_junction(root / "current", target)
    return launcher


def test_resolve_exec_prefers_the_a3_layout_over_an_arbitrary_path_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The versioned layout is the install hive owns; a stray PATH hit is not.
    Resolving through `current` also means an upgrade repoints the junction and
    the registered command keeps working without being rewritten (#328)."""
    import hive._service as svc

    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    _seed_layout(tmp_path)
    monkeypatch.setattr(svc.shutil, "which", lambda _: "/somewhere/else/hive")

    resolved = svc._resolve_exec()

    # Through `current`, NOT the concrete versions/<v> dir: that indirection is
    # what lets an upgrade repoint the junction without rewriting the unit/task.
    assert str(tmp_path / "current") in resolved
    assert "versions" not in resolved
    assert "somewhere/else" not in resolved


def test_resolve_exec_rejects_a_path_hit_that_cannot_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An orphaned uv trampoline is present-but-dead: `which` finds it, running it
    fails. Registering it would give the supervisor a command that can never
    start — the #791 failure. It must fall through, not be selected."""
    import hive._service as svc

    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path / "absent"))
    dead = tmp_path / "hive.exe"
    dead.write_text("", encoding="utf-8")
    monkeypatch.setattr(svc.shutil, "which", lambda _: str(dead))
    monkeypatch.setattr(svc, "_executes", lambda _: False)

    resolved = svc._resolve_exec()

    assert str(dead) not in resolved
    assert "-m hive.server" in resolved


def test_resolve_exec_accepts_a_path_hit_that_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy console script on PATH stays the answer when there is no layout."""
    import hive._service as svc

    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path / "absent"))
    good = tmp_path / "hive"
    good.write_text("", encoding="utf-8")
    monkeypatch.setattr(svc.shutil, "which", lambda _: str(good))
    monkeypatch.setattr(svc, "_executes", lambda _: True)

    assert svc._resolve_exec() == str(good)


def test_resolve_exec_falls_back_to_module_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No layout and nothing on PATH: the module invocation always works because
    the interpreter running this code has hive importable."""
    import hive._service as svc

    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path / "absent"))
    monkeypatch.setattr(svc.shutil, "which", lambda _: None)

    assert "-m hive.server" in svc._resolve_exec()
    assert svc._resolve_argv()[1:] == ["-m", "hive.server"]


def test_executes_treats_any_probe_failure_as_not_runnable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AGENTS.md load-bearing rule: subprocess invocations catch broad Exception —
    on Windows a broken launcher raises OSError variants, not CalledProcessError.
    The probe must answer False, never propagate."""
    import hive._service as svc

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("the process cannot be accessed")

    monkeypatch.setattr(svc.subprocess, "run", _boom)

    assert svc._executes(str(tmp_path / "hive")) is False


def test_resolve_exec_ignores_a_layout_whose_launcher_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `current` junction pointing at a half-built version must not win — the
    layout is only preferable when it actually contains a launcher."""
    import hive._service as svc

    monkeypatch.setenv("HIVE_RUNTIME_ROOT", str(tmp_path))
    _seed_layout(tmp_path, with_launcher=False)
    monkeypatch.setattr(svc.shutil, "which", lambda _: None)

    assert "-m hive.server" in svc._resolve_exec()
    assert svc._resolve_argv()[1:] == ["-m", "hive.server"]


# ── rotate-identity (HIVE-456 AC6) ────────────────────────────────────────


def _initialized_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[str, str]:
    """A state dir after a first TLS start; returns (token, fingerprint)."""
    from hive._daemon import prepare_daemon_credentials

    monkeypatch.setenv("HIVE_DB_PATH", str(tmp_path / "hive.db"))
    token, identity = prepare_daemon_credentials()
    return token, identity.fingerprint


def test_rotate_identity_rotates_key_cert_and_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hive import server
    from hive._endpoint import identity_key_path, identity_state_path, token_file_path

    old_token, old_fingerprint = _initialized_state(monkeypatch, tmp_path)
    old_key = identity_key_path().read_bytes()

    assert server._run_service(["rotate-identity"]) == 0

    from hive._daemon import prepare_daemon_credentials

    new_token, identity = prepare_daemon_credentials()
    assert new_token != old_token
    assert token_file_path().read_text(encoding="ascii") == new_token
    assert identity.fingerprint != old_fingerprint
    assert identity_key_path().read_bytes() != old_key
    assert identity_state_path().exists()
    out = capsys.readouterr()
    emitted = out.out + out.err
    assert identity.fingerprint in emitted
    assert new_token not in emitted
    assert old_token not in emitted
    assert "PRIVATE KEY" not in emitted


def test_rotate_identity_repairs_an_over_permissive_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import os

    from hive import server
    from hive._daemon import prepare_daemon_credentials
    from hive._endpoint import identity_key_path
    from hive._identity import IdentityError

    _initialized_state(monkeypatch, tmp_path)
    if os.name == "nt":
        import subprocess

        subprocess.run(
            ["icacls", str(identity_key_path()), "/grant", "*S-1-1-0:(R)"],
            check=True,
            capture_output=True,
        )
    else:
        identity_key_path().chmod(0o644)
    try:
        prepare_daemon_credentials()
    except IdentityError:
        pass
    else:
        raise AssertionError("positive control: the damaged key must be refused first")

    assert server._run_service(["rotate-identity"]) == 0
    prepare_daemon_credentials()


def test_rotate_identity_refuses_while_the_daemon_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import filelock

    from hive import server
    from hive._endpoint import identity_cert_path, lock_file_path, token_file_path

    _initialized_state(monkeypatch, tmp_path)
    cert, token = identity_cert_path().read_bytes(), token_file_path().read_bytes()

    running = filelock.FileLock(str(lock_file_path()))
    running.acquire(timeout=0)
    try:
        assert server._run_service(["rotate-identity"]) != 0
    finally:
        running.release()

    assert identity_cert_path().read_bytes() == cert
    assert token_file_path().read_bytes() == token
    assert "stop the daemon" in capsys.readouterr().err


# ── status probes the stable port over pinned TLS (HIVE-456 AC7) ──────────


def _healthy_daemon(identity_dir: Path) -> tuple[object, int]:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from hive._identity import load_identity
    from tests.impostor import server_context

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b'{"status":"ok"}' if self.path == "/health" else b""
            self.send_response(200 if self.path == "/health" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format_string: str, *args: object) -> None:
            return

    identity = load_identity(identity_dir / "daemon.key", identity_dir / "daemon.crt")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    server.socket = server_context(identity).wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.mark.parametrize("scenario", ["healthy", "impostor", "down", "owner_unknown"])
def test_status_reports_four_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
) -> None:
    import sys

    from hive import _endpoint, _service
    from hive._identity import create_identity
    from tests.impostor import Impostor

    _initialized_state(monkeypatch, tmp_path)
    supervisor: list[list[str]] = []
    monkeypatch.setattr(_service, "_run_passthrough", lambda cmd: supervisor.append(cmd) or 3)

    server = impostor = None
    if scenario == "healthy":
        server, port = _healthy_daemon(tmp_path)
    elif scenario == "down":
        port = _free_port()
    else:
        other = tmp_path / "other"
        other.mkdir()
        impostor = Impostor(create_identity(other / "daemon.key", other / "daemon.crt"))
        port = impostor.port
        if scenario == "owner_unknown":

            def denied(port: int) -> str:
                raise PermissionError("access denied")

            monkeypatch.setattr(_endpoint, "_listener_identity", denied)
    monkeypatch.setenv("HIVE_DAEMON_PORT", str(port))

    try:
        rc = _service.service_status()
    finally:
        if server is not None:
            server.shutdown()  # type: ignore[attr-defined]
            server.server_close()  # type: ignore[attr-defined]
        if impostor is not None:
            impostor.wait()

    out = capsys.readouterr().out
    state = out.strip().splitlines()[-1]
    expected = {
        "healthy": "healthy",
        "impostor": "unverified listener",
        "down": "down",
        "owner_unknown": "unverified listener, owner unknown",
    }[scenario]
    assert state.startswith(f"hive daemon: {expected}"), out
    assert (rc == 0) == (scenario == "healthy"), "the exit code comes from the probe"
    if scenario == "impostor":
        assert "owner unknown" not in state
        if sys.platform.startswith("linux"):
            assert "this account" in state, "the /proc lookup names the holder's account class"
    if impostor is not None:
        assert impostor.accepted.is_set()
        assert impostor.raw[:1] == b"\x16"
        assert impostor.decrypted == bytearray()
    if sys.platform.startswith("linux") or sys.platform == "win32":
        assert supervisor, "the supervisor view is still shown where one exists"


def test_status_names_an_expired_daemon_certificate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import datetime as dt

    from hive import _service
    from hive._endpoint import identity_cert_path, identity_key_path
    from hive._identity import create_identity
    from tests.impostor import Impostor

    _initialized_state(monkeypatch, tmp_path)
    long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(days=500)
    expired = create_identity(identity_key_path(), identity_cert_path(), now=long_ago)
    listener = Impostor(expired)
    monkeypatch.setenv("HIVE_DAEMON_PORT", str(listener.port))
    monkeypatch.setattr(_service, "_run_passthrough", lambda cmd: 0)

    rc = _service.service_status()
    listener.wait()

    assert rc != 0
    assert "hive daemon: down (the daemon's certificate expired" in capsys.readouterr().out


def test_status_reports_a_malformed_port_override_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hive import _service

    monkeypatch.setenv("HIVE_DAEMON_PORT", "not-a-port")
    monkeypatch.setattr(_service, "_run_passthrough", lambda cmd: 0)

    assert _service.service_status() != 0
    assert "HIVE_DAEMON_PORT" in capsys.readouterr().err


@pytest.mark.parametrize("reset", [True, False], ids=["reset", "stall"])
def test_status_reports_a_listener_that_never_proves_itself(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    reset: bool,
) -> None:
    from hive import _service
    from tests.impostor import SilentListener

    _initialized_state(monkeypatch, tmp_path)
    listener = SilentListener(reset=reset)
    monkeypatch.setenv("HIVE_DAEMON_PORT", str(listener.port))
    monkeypatch.setattr(_service, "_run_passthrough", lambda cmd: 0)

    rc = _service.service_status()
    listener.wait()

    assert listener.accepted.is_set()
    assert rc != 0
    assert "hive daemon: unverified listener" in capsys.readouterr().out


def test_rotate_identity_interrupted_after_the_token_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The token rotates first, so a crash mid-rotation never leaves it behind."""
    import hive._identity as identity_module
    from hive import server
    from hive._daemon import prepare_daemon_credentials
    from hive._endpoint import identity_key_path, token_file_path
    from hive._identity import IdentityError
    from tests.impostor import make_over_permissive

    old_token, _ = _initialized_state(monkeypatch, tmp_path)
    make_over_permissive(identity_key_path())

    def crash(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(identity_module, "create_identity", crash)
    with pytest.raises(OSError, match="disk full"):
        server._run_service(["rotate-identity"])

    assert token_file_path().read_text(encoding="ascii") != old_token
    with pytest.raises(IdentityError):
        prepare_daemon_credentials()
