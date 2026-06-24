"""``hive service`` — install/manage the Phase C daemon as a per-user service.

The daemon ships on PyPI independently of any dotfiles, so this is fully
self-contained and cross-OS:

* **Linux** — a ``systemd --user`` unit, ``Restart=on-failure``, auto-started at
  login (``WantedBy=default.target``).
* **Windows** — a Task Scheduler task with a ``LogonTrigger`` + ``RestartOnFailure``
  (the per-user analogue of ``systemd --user``).
* **macOS** — a documented stub (launchd); a follow-up, not in the CI matrix.

The supervisor's restart policy is the consumer of the slice 1.3 exit-code
contract: a drift-triggered stop exits 75 and a crash exits non-zero → the
supervisor restarts into the new code; a clean stop / singleton-decline exits 0
→ no restart. Both ``Restart=on-failure`` and Task Scheduler ``RestartOnFailure``
express exactly "restart only when the last run failed".
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from hive.config import settings

_log = logging.getLogger(__name__)

SYSTEMD_UNIT_FILENAME = "hive.service"
WINDOWS_TASK_NAME = "HiveVaultDaemon"


# ── platform + path helpers (all overridable in tests) ───────────────────


def _platform() -> str:
    """The supervisor family for the current OS."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return sys.platform


def _config_root() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) if base else Path.home() / ".config"


def systemd_unit_path() -> Path:
    return _config_root() / "systemd" / "user" / SYSTEMD_UNIT_FILENAME


def _resolve_exec() -> str:
    """Absolute command the supervisor runs (the unit/task appends ``serve``).

    Prefers the installed ``hive`` console script; falls back to
    ``<python> -m hive.server`` when it is not on PATH.
    """
    found = shutil.which("hive")
    if found:
        return found
    return f"{sys.executable} -m hive.server"


def _current_windows_user() -> str:
    """The S4U principal account (``DOMAIN\\user`` or bare user), read from the
    environment at install time — the daemon runs as the installing user. S4U
    needs an explicit account; an empty result omits ``<UserId>`` and lets
    schtasks default to the current user."""
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain and user else user


# ── pure renderers (host-OS independent) ─────────────────────────────────


def render_systemd_unit(exec_start: str, *, vault: str) -> str:
    """A ``systemd --user`` unit for ``hive serve``.

    Bakes ``VAULT_PATH`` because ``systemd --user`` starts with a minimal
    environment, and pulls optional secrets from an owner-only env file (the
    leading ``-`` makes it optional) so nothing sensitive is written here.
    """
    env_file = _config_root() / "hive" / "hive.env"
    return (
        "[Unit]\n"
        "Description=Hive vault daemon (single-owner MCP over loopback)\n"
        "Documentation=https://github.com/mlorentedev/hive\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start} serve\n"
        f"Environment=VAULT_PATH={vault}\n"
        f"EnvironmentFile=-{env_file}\n"
        "Restart=on-failure\n"
        "RestartSec=1\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


_WINDOWS_TASK_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-16"?>\n'
    '<Task version="1.2" '
    'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
    "  <RegistrationInfo>\n"
    "    <Description>Hive vault daemon (single-owner MCP over loopback)"
    "</Description>\n"
    "  </RegistrationInfo>\n"
    "  <Triggers>\n"
    "    <LogonTrigger>\n"
    "      <Enabled>true</Enabled>\n"
    "    </LogonTrigger>\n"
    "  </Triggers>\n"
    "  <Principals>\n"
    '    <Principal id="Author">\n'
    "{principal_user}"
    "      <LogonType>S4U</LogonType>\n"
    "      <RunLevel>LeastPrivilege</RunLevel>\n"
    "    </Principal>\n"
    "  </Principals>\n"
    "  <Settings>\n"
    "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
    "    <StartWhenAvailable>true</StartWhenAvailable>\n"
    "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
    "    <RestartOnFailure>\n"
    "      <Interval>PT1M</Interval>\n"
    "      <Count>3</Count>\n"
    "    </RestartOnFailure>\n"
    "    <Enabled>true</Enabled>\n"
    "  </Settings>\n"
    '  <Actions Context="Author">\n'
    "    <Exec>\n"
    "      <Command>powershell.exe</Command>\n"
    "      <Arguments>{arguments}</Arguments>\n"
    "    </Exec>\n"
    "  </Actions>\n"
    "</Task>\n"
)


def render_windows_task_xml(
    command: str,
    arguments: str = "serve",
    *,
    user: str = "",
) -> str:
    """A Task Scheduler definition for the Phase C daemon on Windows (ADR-015).

    Two Windows mechanisms replace ADR-011's POSIX assumptions, both validated
    on real hardware (2026-06-04):

    * **Supervisor loop (restart).** ``<RestartOnFailure>`` does NOT restart on
      an application's non-zero exit — it reacts to the engine failing to launch
      — so it cannot map ``systemd Restart=on-failure``. The action is instead an
      inline PowerShell loop that relaunches ``<command> serve`` while it exits
      non-zero and stops on a clean exit 0 (drift → 75 → relaunch onto new code;
      clean stop / singleton-decline → 0 → no relaunch). ``<RestartOnFailure>``
      is kept only as a secondary net for an engine-launch failure.
    * **S4U principal (windowless).** ``<LogonType>S4U</LogonType>`` runs the task
      in session 0 — no console window — at logon, no stored password, no admin
      (the per-user analogue of ``systemd --user``).
    """
    ps_arguments = (
        f"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
        f'-Command "{_supervisor_ps_command(command, arguments)}"'
    )
    principal_user = f"      <UserId>{escape(user)}</UserId>\n" if user else ""
    return _WINDOWS_TASK_TEMPLATE.format(
        principal_user=principal_user,
        arguments=escape(ps_arguments),
    )


def _supervisor_ps_command(command: str, arguments: str) -> str:
    """The PowerShell relaunch loop shared by the Scheduled Task and the
    Startup-folder fallback — a single SSOT for the restart contract: relaunch
    ``<command> <arguments>`` while it exits non-zero, stop on a clean exit 0
    (drift → 75 → relaunch onto new code; clean stop / singleton-decline → 0)."""
    return (
        f"while($true){{ & '{command}' {arguments}; "
        f"if($LASTEXITCODE -eq 0){{break}} Start-Sleep -Seconds 1 }}"
    )


def render_startup_vbs(command: str, arguments: str = "serve") -> str:
    """A windowless Startup-folder launcher for the daemon — the fallback used
    when Task Scheduler is policy-locked for a non-admin/domain user (#252).

    ``WScript.Shell.Run(cmd, 0, False)`` launches the PowerShell supervisor loop
    with a hidden window (style 0) and does not block — the per-user analogue of
    the S4U Scheduled Task, carrying the identical restart contract. It writes to
    ``shell:startup`` only, which a standard user can write, so it needs neither
    admin nor Task Scheduler.
    """
    ps = (
        f"powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
        f'-Command "{_supervisor_ps_command(command, arguments)}"'
    )
    # A double-quote inside a VBS string literal is escaped by doubling it.
    vbs_arg = ps.replace('"', '""')
    return f'Set sh = CreateObject("WScript.Shell")\r\nsh.Run "{vbs_arg}", 0, False\r\n'


def _startup_dir() -> Path:
    """The current user's Startup folder — writable without admin, unlike the
    policy-lockable Task Scheduler (``shell:startup``)."""
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_vbs_path() -> Path:
    """Path of the Startup-folder launcher used as the Task Scheduler fallback."""
    return _startup_dir() / "hive-serve.vbs"


# ── supervisor invocation (mocked in tests; real on the CI matrix) ───────


def _run(cmd: list[str]) -> int:
    """Run *cmd*, capturing output. Broad ``except`` per the cross-OS rule — a
    missing supervisor binary raises ``FileNotFoundError``, not a clean error."""
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)  # noqa: S603
    except Exception as exc:  # noqa: BLE001 — missing binary / OS quirks
        _log.warning("hive.service command failed: %s (%r)", cmd, exc)
        print(f"hive: command not available: {cmd[0]}", file=sys.stderr)
        return 1
    if proc.returncode != 0:
        _log.warning("hive.service %s rc=%s %s", cmd, proc.returncode, proc.stderr.strip())
    return proc.returncode


def _run_passthrough(cmd: list[str]) -> int:
    """Run *cmd* inheriting stdio so the user sees the supervisor's own output."""
    try:
        return subprocess.run(cmd, check=False).returncode  # noqa: S603
    except Exception as exc:  # noqa: BLE001 — missing binary / OS quirks
        print(f"hive: command not available: {cmd[0]} ({exc})", file=sys.stderr)
        return 1


def _systemctl(*args: str) -> int:
    return _run(["systemctl", "--user", *args])


def _schtasks_create(task_name: str, *, xml: str) -> int:
    """Register *xml* as Scheduled Task *task_name* (UTF-16, as schtasks wants)."""
    fd, name = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as handle:
            handle.write(xml)
        return _run(["schtasks", "/Create", "/TN", task_name, "/XML", name, "/F"])
    finally:
        with contextlib.suppress(OSError):
            os.unlink(name)


def _unsupported(action: str) -> int:
    print(
        f"hive service {action}: automatic setup is not yet supported on "
        f"{sys.platform}. On macOS, create a launchd agent (KeepAlive=true) "
        f"running `{_resolve_exec()} serve`. Tracked as a follow-up.",
        file=sys.stderr,
    )
    return 1


# ── public commands (dispatch on platform) ───────────────────────────────


def _install_systemd(*, enable: bool) -> int:
    path = systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_systemd_unit(_resolve_exec(), vault=str(settings.vault_path)),
        encoding="utf-8",
    )
    print(f"hive: wrote systemd unit {path}")
    if not enable:
        print(
            "hive: run `systemctl --user daemon-reload && "
            "systemctl --user enable --now hive.service` when ready",
        )
        return 0
    rc = _systemctl("daemon-reload")
    rc = _systemctl("enable", "--now", "hive.service") or rc
    if rc == 0:
        print("hive: service enabled and started (systemctl --user)")
    return rc


def _install_windows(*, enable: bool) -> int:
    xml = render_windows_task_xml(_resolve_exec(), user=_current_windows_user())
    if not enable:
        path = _config_root() / "hive" / f"{WINDOWS_TASK_NAME}.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(xml, encoding="utf-16")
        print(
            f"hive: wrote task definition {path}; register with "
            f'`schtasks /Create /TN {WINDOWS_TASK_NAME} /XML "{path}" /F`',
        )
        return 0
    rc = _schtasks_create(WINDOWS_TASK_NAME, xml=xml)
    if rc == 0:
        print(f"hive: registered scheduled task {WINDOWS_TASK_NAME}")
        return 0
    # Task Scheduler can be policy-locked for a non-admin/domain user; fall back
    # to a per-user Startup launcher that needs no admin (#252).
    return _install_windows_startup_fallback()


def _install_windows_startup_fallback() -> int:
    """Write a per-user Startup-folder launcher when Task Scheduler is locked.

    Returns 0 on success; on a write failure it prints an actionable message and
    returns non-zero — never a silent success that hides a broken install."""
    exe = _resolve_exec()
    path = startup_vbs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_startup_vbs(exe), encoding="utf-8")
    except OSError as exc:
        print(
            f"hive: Task Scheduler is unavailable and the Startup-folder fallback "
            f"could not be written ({exc}). Launch `{exe} serve` at logon yourself, "
            f"or retry `hive service install` as administrator.",
            file=sys.stderr,
        )
        return 1
    print(
        f"hive: Task Scheduler unavailable — installed a per-user Startup launcher "
        f"at {path}. The daemon starts at your next logon; run `{exe} serve` now to "
        f"start it this session.",
    )
    return 0


def install_service(*, enable: bool = True) -> int:
    """Install the daemon as a per-user service for the current OS."""
    plat = _platform()
    if plat == "linux":
        return _install_systemd(enable=enable)
    if plat == "windows":
        return _install_windows(enable=enable)
    return _unsupported("install")


def uninstall_service() -> int:
    """Remove the per-user service for the current OS."""
    plat = _platform()
    if plat == "linux":
        rc = _systemctl("disable", "--now", "hive.service")
        path = systemd_unit_path()
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:
                _log.warning("hive.service could not remove %s: %s", path, exc)
                return 1
        _systemctl("daemon-reload")
        print("hive: service removed")
        return rc if rc == 0 else 0
    if plat == "windows":
        rc = _run(["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"])
        # Also remove the Startup-folder fallback — it may be the only mechanism
        # installed (Task Scheduler was locked), so a failed task delete is fine.
        removed_fallback = _remove_startup_fallback()
        if rc == 0 or removed_fallback:
            print("hive: service removed")
            return 0
        return rc
    return _unsupported("uninstall")


def _remove_startup_fallback() -> bool:
    """Remove the Startup-folder launcher if present; True if it was removed."""
    path = startup_vbs_path()
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError as exc:
        _log.warning("hive.service could not remove %s: %s", path, exc)
        return False
    return True


def service_status() -> int:
    """Show the supervisor's view of the daemon for the current OS."""
    plat = _platform()
    if plat == "linux":
        return _run_passthrough(["systemctl", "--user", "status", "hive.service"])
    if plat == "windows":
        return _run_passthrough(["schtasks", "/Query", "/TN", WINDOWS_TASK_NAME, "/V"])
    return _unsupported("status")
