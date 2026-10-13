"""``hive service`` — install/manage the Phase C daemon as a per-user service.

The daemon ships on PyPI independently of any dotfiles, so this is fully
self-contained and cross-OS:

* **Linux** — a ``systemd --user`` unit, ``Restart=on-failure``, auto-started at
  login (``WantedBy=default.target``).
* **Windows** — a Task Scheduler task with a ``LogonTrigger`` + ``RestartOnFailure``
  (the per-user analogue of ``systemd --user``).
* **macOS** — a launchd agent in ``~/Library/LaunchAgents`` with ``RunAtLoad``
  and ``KeepAlive {SuccessfulExit: false}``, loaded into the ``gui/<uid>``
  domain. macOS is not in the CI matrix; the renderer and the ``launchctl``
  sequence are unit-tested, and the real agent was verified on a Mac.

The supervisor's restart policy is the consumer of the slice 1.3 exit-code
contract: a drift-triggered stop exits 75 and a crash exits non-zero → the
supervisor restarts into the new code; a clean stop / singleton-decline exits 0
→ no restart. ``Restart=on-failure``, the Windows supervisor loop and launchd's
``KeepAlive {SuccessfulExit: false}`` all express exactly "restart only when the
last run failed".
"""

from __future__ import annotations

import contextlib
import logging
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

from hive._helpers import _subprocess_run_kwargs
from hive.config import settings

_log = logging.getLogger(__name__)

SYSTEMD_UNIT_FILENAME = "hive.service"
WINDOWS_TASK_NAME = "HiveVaultDaemon"
LAUNCHD_LABEL = "io.github.mlorentedev.hive"

# launchd's floor between two starts of a job that keeps exiting. A drift exit
# (75) is the common restart and should be quick; the default is 10s as well,
# set explicitly so the restart contract is read here, not recalled.
_LAUNCHD_THROTTLE_S = 10

# Bounded so a wedged launcher cannot hang `hive service install`; a healthy
# `--version` returns in milliseconds.
_PROBE_TIMEOUT_S = 10.0


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


def launchd_plist_path() -> Path:
    """launchd loads per-user agents from this directory only, so it is not
    under ``_config_root()`` like the systemd unit."""
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def launchd_log_path() -> Path:
    """Where launchd sends the daemon's stdout and stderr; without it a crash
    traceback goes nowhere."""
    return Path.home() / "Library" / "Logs" / "hive" / "serve.log"


def _current_layout_exec() -> str | None:
    """The launcher inside the A3 versioned layout, or ``None`` when absent.

    Resolving through the ``current`` junction rather than a concrete version
    dir is the property A3 exists for (HIVE-267): an upgrade repoints the
    junction and the registered command keeps working, unrewritten.
    """
    from hive import _runtime

    subdir, name = ("Scripts", "hive.exe") if sys.platform == "win32" else ("bin", "hive")
    launcher = _runtime.current_link() / subdir / name
    return str(launcher) if launcher.exists() else None


def _executes(command: str) -> bool:
    """Whether *command* actually starts.

    ``shutil.which`` only proves a file sits on PATH. An orphaned uv trampoline
    is present but dead (``failed to canonicalize script path``), and a
    present-but-broken binary is indistinguishable from a healthy one at the
    ``which`` level — registering it hands the supervisor a command that can
    never start (#328, observed in dotfiles#791).

    Broad ``Exception`` per AGENTS.md: on Windows a broken launcher surfaces as
    ``OSError`` variants, not ``CalledProcessError``. A probe that cannot answer
    means "not runnable", never a raised exception into the install path.
    """
    try:
        proc = subprocess.run(  # noqa: S603
            [command, "--version"],
            check=False,
            capture_output=True,
            timeout=_PROBE_TIMEOUT_S,
            **_subprocess_run_kwargs(),
        )
    except Exception:  # noqa: BLE001 — any failure to probe means "do not use it"
        return False
    return proc.returncode == 0


def _resolve_argv() -> list[str]:
    """Argument vector the supervisor runs (the unit/task/agent appends ``serve``).

    Resolution order, most-owned first:

    1. the A3 versioned layout, when hive owns one — it follows upgrades;
    2. a ``hive`` on PATH, but only once verified to start;
    3. ``<python> -m hive.server``, which works because this interpreter has
       hive importable by construction.

    A vector, not a string: launchd's ``ProgramArguments`` is never split by a
    shell, so the module fallback joined into one element is a daemon that
    never starts, and an interpreter path with a space cannot survive a split.
    """
    layout = _current_layout_exec()
    if layout:
        return [layout]
    found = shutil.which("hive")
    if found and _executes(found):
        return [found]
    return [sys.executable, "-m", "hive.server"]


def _resolve_exec() -> str:
    """``_resolve_argv`` as the command line systemd and Task Scheduler take."""
    return " ".join(_resolve_argv())


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


# Sources the optional env file, then execs the daemon, so the process launchd
# supervises is hive itself. `$0` is the env file and `$@` the daemon's argv.
_LAUNCHD_ENV_WRAPPER = 'set -a; if [ -r "$0" ]; then . "$0"; fi; set +a; exec "$@"'


def render_launchd_plist(argv: list[str], *, vault: str, log: str) -> bytes:
    """A launchd agent for ``hive serve``, the macOS analogue of the systemd unit.

    * ``RunAtLoad`` + ``KeepAlive {SuccessfulExit: false}`` is launchd's spelling
      of ``Restart=on-failure``: a drift (75) or a crash restarts the daemon, a
      clean stop or a singleton-decline (0) leaves it down.
    * ``VAULT_PATH`` is baked because launchd starts agents with a minimal
      environment. Secrets are not: the same optional, owner-only
      ``hive.env`` the systemd unit reads is sourced at start, so nothing
      sensitive is written to a plist in ``~/Library/LaunchAgents``.
    """
    env_file = _config_root() / "hive" / "hive.env"
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            "/bin/sh",
            "-c",
            _LAUNCHD_ENV_WRAPPER,
            str(env_file),
            *argv,
            "serve",
        ],
        "EnvironmentVariables": {"VAULT_PATH": vault},
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": _LAUNCHD_THROTTLE_S,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    return plistlib.dumps(plist)


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


def _run(cmd: list[str], *, timeout: float | None = None, quiet: bool = False) -> int:
    """Run *cmd*, capturing output. Broad ``except`` per the cross-OS rule — a
    missing supervisor binary raises ``FileNotFoundError``, not a clean error.

    *quiet* keeps a failure's stderr to the log, for a probe whose non-zero
    exit is an answer ("not loaded"), not an error to show the user."""
    try:
        proc = subprocess.run(  # noqa: S603
            cmd, check=False, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        print(f"hive: {cmd[0]} did not finish within {timeout:g}s", file=sys.stderr)
        return 1
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
        if stderr and not quiet:
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


def _launchd_domain() -> str:
    """The per-user GUI domain: it exists only while that user is logged in at
    the console, the condition under which an agent should run."""
    return f"gui/{os.getuid()}"


def _launchctl(*args: str, quiet: bool = False) -> int:
    return _run(["launchctl", *args], quiet=quiet)


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
        f"hive service {action}: no supervisor is supported on {sys.platform}. "
        f"Run `{_resolve_exec()} serve` under your platform's service manager, "
        "restarting it only when it exits non-zero.",
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
    if rc != 0:
        return rc
    state = _await_daemon()
    if state != "healthy":
        print(
            f"hive: service enabled, but the daemon is {state}; "
            "see `systemctl --user status hive.service`.",
            file=sys.stderr,
        )
        return 1
    print("hive: service enabled and started (systemctl --user)")
    return 0


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
        # Parity with systemd's `enable --now`: the LogonTrigger alone leaves the
        # daemon down until the next logon, so every `hive client` fails until
        # then. The verdict is the daemon answering, not /Run's exit code: a
        # daemon that already runs is a success whatever /Run reports.
        ran = _run(["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME], timeout=_PROBE_TIMEOUT_S)
        state = _await_daemon(_START_DEADLINE_S if ran == 0 else 0.0)
        if state != "healthy":
            print(
                f"hive: registered scheduled task {WINDOWS_TASK_NAME}, but the daemon "
                f"is {state}; run `schtasks /Run /TN {WINDOWS_TASK_NAME}`, then "
                "`hive service status`.",
                file=sys.stderr,
            )
            return 1
        print(f"hive: registered and started scheduled task {WINDOWS_TASK_NAME}")
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
    # Start it now through the same launcher logon will run, so this session
    # and the next one run one definition; the daemon's singleton lock keeps a
    # second start from becoming a second owner.
    spawned = _spawn_detached(["wscript.exe", str(path)]) == 0
    state = _await_daemon() if spawned else "not started"
    if state != "healthy":
        print(
            f"hive: installed a per-user Startup launcher at {path}, but the daemon "
            f"is {state}; the launcher runs again at your next logon, or run `{exe} serve`.",
            file=sys.stderr,
        )
        return 1
    print(
        f"hive: Task Scheduler unavailable — installed and started a per-user "
        f"Startup launcher at {path}.",
    )
    return 0


def _install_launchd(*, enable: bool) -> int:
    path = launchd_plist_path()
    log = launchd_log_path()
    plist = render_launchd_plist(_resolve_argv(), vault=str(settings.vault_path), log=str(log))
    previous = path.read_bytes() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    if previous != plist:
        path.write_bytes(plist)
        print(f"hive: wrote launchd agent {path}")
    domain = _launchd_domain()
    if not enable:
        print(f"hive: run `launchctl bootstrap {domain} {path}` when ready")
        return 0
    target = f"{domain}/{LAUNCHD_LABEL}"
    loaded = _launchctl("print", target, quiet=True) == 0
    if loaded and previous != plist:
        # launchd keeps the definition it loaded; a changed plist reaches it
        # only through a reload. An unchanged one is left running, so a
        # repeated install does not drop the daemon's live sessions.
        _launchctl("bootout", target, quiet=True)
        loaded = False
    if not loaded:
        rc = _launchctl("bootstrap", domain, str(path))
        if rc != 0:
            print(
                f"hive: launchctl could not load {path} into {domain}. The agent "
                "domain exists only for a user logged in at the Mac's console; "
                "from an SSH-only session, log in at the console and re-run "
                "`hive service install`.",
                file=sys.stderr,
            )
            return rc
    # Loaded but not running (it last exited 0, so KeepAlive left it down):
    # kickstart without -k starts it, and leaves a running daemon alone.
    _launchctl("kickstart", target, quiet=True)
    state = _await_daemon()
    if state != "healthy":
        print(
            f"hive: agent loaded, but the daemon is {state}; see "
            f"`launchctl print {target}` and {log}.",
            file=sys.stderr,
        )
        return 1
    print(f"hive: service loaded and started (launchd {target})")
    return 0


def _uninstall_launchd() -> int:
    _launchctl("bootout", f"{_launchd_domain()}/{LAUNCHD_LABEL}", quiet=True)
    path = launchd_plist_path()
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            _log.warning("hive.service could not remove %s: %s", path, exc)
            return 1
    print("hive: service removed")
    return 0


def _spawn_detached(cmd: list[str]) -> int:
    """Start *cmd* without waiting for it. Broad ``except`` per the cross-OS rule."""
    try:
        subprocess.Popen(  # noqa: S603
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception as exc:  # noqa: BLE001 — missing binary / OS quirks
        _log.warning("hive.service could not start %s (%r)", cmd, exc)
        return 1
    return 0


# Bounds the wait for a daemon install has just started: a cold Python start,
# the vault index and TLS identity load, with room for a slow first logon.
_START_DEADLINE_S = 30.0
_START_POLL_S = 0.5


def _await_daemon(deadline_s: float = _START_DEADLINE_S) -> str:
    """Probe until the daemon answers or *deadline_s* passes; return the last state.

    Install promises a daemon that answers, not a supervisor that was asked to
    start one, so every OS's install ends here. A zero deadline probes once.
    """
    end = time.monotonic() + deadline_s
    while True:
        state = daemon_state()
        if state == "healthy" or time.monotonic() >= end:
            return state
        time.sleep(_START_POLL_S)


def install_service(*, enable: bool = True) -> int:
    """Install the daemon as a per-user service for the current OS."""
    plat = _platform()
    if plat == "linux":
        return _install_systemd(enable=enable)
    if plat == "windows":
        return _install_windows(enable=enable)
    if plat == "darwin":
        return _install_launchd(enable=enable)
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
    if plat == "darwin":
        return _uninstall_launchd()
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


_HEALTH_TIMEOUT_S = 2.0


def _probe_health(host: str, port: int) -> str:
    """Ask the stable port for ``/health``, trusting only the owner's certificate.

    Returns one of ``healthy``, ``unverified``, ``expired``, ``down``. A listener
    that accepts the connection but does not prove the owner's certificate is
    ``unverified``: it is not the owner's daemon and is sent nothing beyond
    the handshake.
    """
    import http.client

    from hive._endpoint import identity_cert_path
    from hive._tls import load_pinned_certificate, prove_listener

    try:
        cert_pem = load_pinned_certificate(identity_cert_path())
    except RuntimeError:
        # Without the pin nothing can be proven; something listening is
        # therefore unverified, never assumed to be the daemon.
        try:
            with socket.create_connection((host, port), timeout=_HEALTH_TIMEOUT_S):
                return "unverified"
        except OSError:
            return "down"
    proof, tls = prove_listener(host, port, cert_pem, timeout=_HEALTH_TIMEOUT_S)
    if proof == "absent":
        return "down"
    if tls is None:
        return proof
    connection = http.client.HTTPSConnection(host, port, timeout=_HEALTH_TIMEOUT_S)
    connection.sock = tls
    try:
        connection.request("GET", "/health")
        # The owner's daemon always answers 200; anything else means it is
        # not serving (a shutdown in progress, for instance).
        return "healthy" if connection.getresponse().status == 200 else "down"
    except (OSError, http.client.HTTPException):
        return "down"
    finally:
        connection.close()


def daemon_state() -> str:
    """One of the four ``hive service status`` states (ADR-022 Amendment 1)."""
    from hive._endpoint import DEFAULT_HOST, configured_daemon_port, port_holder

    port = configured_daemon_port()
    probe = _probe_health(DEFAULT_HOST, port)
    if probe == "expired":
        # Clients refuse an expired certificate, so the daemon serves no one.
        return "down (the daemon's certificate expired; restart the daemon to regenerate it)"
    if probe != "unverified":
        return probe
    holder = port_holder(port)
    if holder == "owner could not be determined":
        return "unverified listener, owner unknown"
    return f"unverified listener (held by {holder}); possible impersonation"


def service_status() -> int:
    """Show the supervisor's view, then prove who answers on the stable port.

    The supervisor only says whether its own process runs; the exit code comes
    from the pinned ``/health`` probe, which runs on every OS.
    """
    plat = _platform()
    if plat == "linux":
        _run_passthrough(["systemctl", "--user", "status", "hive.service"])
    elif plat == "windows":
        _run_passthrough(["schtasks", "/Query", "/TN", WINDOWS_TASK_NAME, "/V"])
    elif plat == "darwin":
        _run_passthrough(["launchctl", "print", f"{_launchd_domain()}/{LAUNCHD_LABEL}"])
    try:
        state = daemon_state()
    except (RuntimeError, ValueError) as exc:
        print(f"hive: cannot probe the daemon: {exc}", file=sys.stderr)
        return 1
    print(f"hive daemon: {state}", flush=True)
    return 0 if state == "healthy" else 1
