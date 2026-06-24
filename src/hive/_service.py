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
    loop = (
        f"while($true){{ & '{command}' {arguments}; "
        f"if($LASTEXITCODE -eq 0){{break}} Start-Sleep -Seconds 1 }}"
    )
    ps_arguments = f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "{loop}"'
    principal_user = f"      <UserId>{escape(user)}</UserId>\n" if user else ""
    return _WINDOWS_TASK_TEMPLATE.format(
        principal_user=principal_user,
        arguments=escape(ps_arguments),
    )


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
        stderr = proc.stderr.strip()
        _log.warning("hive.service %s rc=%s %s", cmd, proc.returncode, stderr)
        # Logging is unconfigured in the normal CLI path, so the warning above
        # is invisible — surface the child's stderr so the user sees WHY it
        # failed instead of a bare non-zero exit (#252).
        if stderr:
            print(stderr, file=sys.stderr)
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
    return rc


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
        return _run(["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"])
    return _unsupported("uninstall")


def service_status() -> int:
    """Show the supervisor's view of the daemon for the current OS."""
    plat = _platform()
    if plat == "linux":
        return _run_passthrough(["systemctl", "--user", "status", "hive.service"])
    if plat == "windows":
        return _run_passthrough(["schtasks", "/Query", "/TN", WINDOWS_TASK_NAME, "/V"])
    return _unsupported("status")
