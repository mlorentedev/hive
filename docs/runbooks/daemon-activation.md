---
id: daemon-activation
type: runbook
status: active
created: "2026-06-02"
owner: manu
---

# Hive: Daemon Activation (Phase C) — per machine

> Switch a machine from the per-session stdio MCP server (`uvx hive-vault`) to
> the single-owner daemon model: a supervised `hive serve` plus a thin
> `hive client` stdio shim that proxies to it.
>
> This is the **manual** procedure validated on one machine before the dotfiles
> rollout automates it (`setup-*.sh`). `hive client` fails explicitly when the
> daemon is unavailable; it never starts a second, unmanaged vault owner.
>
> **Security hold:** Do not deploy daemon mode on an untrusted multi-user host
> until [#456](https://github.com/mlorentedev/hive/issues/456) is resolved.
> The stable endpoint now serves TLS with a per-user certificate that every
> Hive client pins (ADR-022 Amendment 1), so a process impersonating the fixed
> port during downtime fails the handshake before any bearer is sent. The hold
> stays until the Linux and Windows evidence for #456, including the
> cross-user check, is recorded. The manual Windows run is
> [verify-server-identity-windows.md](verify-server-identity-windows.md).

## Prerequisites

- `hive-vault >= 1.32.0` published to PyPI (the version that ships
  `hive serve` / `hive client` / `hive service`). Check:
  `python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('https://pypi.org/pypi/hive-vault/json'))['info']['version'])"`
- `uv` on PATH.
- Linux: a running `systemd --user` instance (`systemctl --user is-system-running`
  returns `running` or `degraded`, and `XDG_RUNTIME_DIR` is set).
- Windows: Task Scheduler (always present); no admin required (per-user task).

## Steps

### 1. Install / upgrade the tool

```bash
uv tool install --upgrade hive-vault
hive service --help          # confirm the subcommand exists (>= 1.32.0)
```

The `hive` console script must resolve to the upgraded tool (`which hive` /
`where hive`).

On Windows, this bootstraps the managed installation. Use `hive self-upgrade
[version]` for later upgrades; the version is optional and defaults to the
latest PyPI release. It creates a versioned runtime and atomically switches the
active junction instead of replacing files in use. Open a new terminal after
the first managed upgrade so the launcher added to the user `PATH` is available.
On Linux and macOS, later upgrades use `uv tool upgrade hive-vault`.

### 2. Install + start the service

```bash
hive service install         # render unit/task, enable, start
hive service status          # supervisor's view, then the pinned /health probe
```

`hive service status` prints the supervisor's view where one exists, then a
final `hive daemon: <state>` line from a `GET /health` that trusts only the
daemon's certificate. Its exit code comes from that probe:

| Final line | Meaning | Exit |
|---|---|---|
| `healthy` | The owner's daemon answered | 0 |
| `unverified listener (held by …)` | Something holds the port without the daemon's certificate: possible impersonation | 1 |
| `unverified listener, owner unknown` | The same, and the holder's account could not be read | 1 |
| `down` | Nothing proved itself on the port | 1 |

`down (the daemon's certificate expired …)` means a daemon ran past its
certificate's validity; restarting it regenerates the certificate and keeps
the token.

- **Linux** writes `~/.config/systemd/user/hive.service`
  (`Restart=on-failure`, `WantedBy=default.target`), then
  `systemctl --user daemon-reload && enable --now`.
- **Windows** registers the `HiveVaultDaemon` Scheduled Task
  (`LogonTrigger` + `RestartOnFailure`) and starts it with `schtasks /Run`.
  Where Task Scheduler is locked, it writes a Startup-folder launcher and runs
  it.

On every OS, install then waits up to 30 s for the same pinned `/health` probe
and exits 1 with the daemon's state if it never reports `healthy`. A zero exit
means the daemon answers, not only that a supervisor was asked to start it.

Use `hive service install --no-enable` to write the unit/task without starting
it (staged setup).

### 3. Verify the daemon serves

The daemon serves TLS 1.3 only. Trust its own certificate, never the system
store and never `-k`:

```bash
# Linux state dir is ~/.local/share/hive (HIVE state dir = the SQLite DB parent)
STATE=~/.local/share/hive
PORT=$(cat "$STATE/daemon.port")
CERT="$STATE/daemon.crt"

curl -s --cacert "$CERT" "https://127.0.0.1:$PORT/health"                      # {"status":"ok","ready":true,...}
curl -s --cacert "$CERT" -o /dev/null -w '%{http_code}\n' "https://127.0.0.1:$PORT/status"   # 401 (token-gated)
curl -s --cacert "$CERT" -H "Authorization: Bearer $(cat "$STATE/daemon.token")" \
  "https://127.0.0.1:$PORT/status"                                             # 200 + metrics
```

`daemon.token`, `daemon.key`, `daemon.crt`, `identity.state` and `daemon.port`
must be owner-only (`600`). The certificate is public, but it is the trust
anchor every client pins, so nobody else may be able to replace it.

**Rotating the identity.** If the daemon refuses to start because
`daemon.key` is not owner-only, or because the identity files are missing or
corrupt, stop the daemon and run:

```bash
hive service rotate-identity   # new key, certificate and token; prints the new fingerprint
```

It refuses while the daemon is running. A running `hive client` re-reads the
new certificate and token on its next connection; direct HTTP registrations
must re-read `daemon.crt` and the token.

**Port held by someone else.** If `hive serve` reports that the stable port
is in use, the message says whether the holder is `this account`, `another
account`, or that the `owner could not be determined`. Another account holding
your port is the impersonation case this design refuses; the clients will not
talk to it.

**Upgrading from a plaintext daemon.** A daemon from before ADR-022
Amendment 1 served plaintext HTTP, so its token is presumed captured. The
first TLS start generates the identity and rotates the token once. `hive
client` and `hive delegate` read the new token and certificate on their next
start. Any direct HTTP registration holding a copy of the old token, or an
`http://` URL, stops working and must be re-registered as described in step 5.

### 4. Point the client at the daemon (`~/.claude.json`)

Flip the `hive` MCP entry from `uvx hive-vault` (stdio) to `hive client`
(daemon proxy). Edit the **single** field surgically and verify integrity —
`~/.claude.json` is prone to a truncation bug if rewritten carelessly
([anthropics/claude-code#59870](https://github.com/anthropics/claude-code/issues/59870)).

```bash
CJ="$HOME/.claude.json"
BK=$(mktemp); cp -f "$CJ" "$BK"; OLD=$(stat -c %s "$CJ")
TMP=$(mktemp)
jq '.mcpServers.hive.command = "hive" | .mcpServers.hive.args = ["client"]' "$CJ" > "$TMP"
NEW=$(stat -c %s "$TMP")
# Integrity gate: valid JSON AND not collapsed (block truncation, allow tiny shrink).
if python3 -c "import json; json.load(open('$TMP'))" && [ "$NEW" -ge $((OLD - 200)) ]; then
  mv "$TMP" "$CJ"; echo "flipped (backup: $BK)"
else
  echo "INTEGRITY FAIL — backup intact at $BK"; rm -f "$TMP"
fi
```

The flip affects the **next** Claude Code session, not the running one. The
new session connects through `hive client` → daemon; `/status`
`sessions_started` / `total_calls` climb as you use hive.

### 5. GitHub Copilot CLI registration

On a trusted single-user host, register the installed stdio adapter in
Copilot's user-scoped MCP configuration instead of launching `uvx`:

```bash
copilot mcp add hive -- hive client
```

If a `hive-vault` MCP entry already runs `uvx`, disable that entry after
confirming the new one works; do not leave two vault owners registered.
Ensure the installed `hive` command resolves to the same runtime as the
supervised daemon. With a custom daemon port, set `HIVE_DAEMON_PORT` to the
same value for Copilot's environment and the daemon. Start a new Copilot
session, then list Hive's tools and call `vault_health`. The integration smoke
`uv run python -m pytest tests/test_daemon.py -k copilot_daemon_mode -q`
initializes, lists tools, and calls `vault_health` against a real isolated
daemon through both stdio and direct HTTPS using synthetic credentials; it
does **not** launch the Copilot CLI itself.

`hive client` is the recommended registration. A direct registration is
supported only over HTTPS, with certificate verification scoped to the host
process:

- URL `https://127.0.0.1:<port>/mcp`; `http://` URLs no longer work, because
  the daemon is TLS-only.
- For Node-based hosts such as Copilot CLI, set
  `NODE_EXTRA_CA_CERTS=<state dir>/daemon.crt` in that host's environment.
  Node adds this certificate to its built-in roots rather than replacing
  them, so the host is less strict than `hive client`, which trusts the
  per-user certificate alone. No public CA may issue a certificate for
  `127.0.0.1`, which bounds the difference; prefer `hive client` where it
  matters.
  Do not add the certificate to an operating-system or user root store.
- Never disable verification (`NODE_TLS_REJECT_UNAUTHORIZED=0` or an
  "insecure" flag). A host that cannot verify the certificate uses
  `hive client`.
- `tests/test_daemon.py -k direct_https` checks Hive's side of this contract
  on Linux, with a stand-in client and a Node client. That is not a real MCP
  host: Hive claims direct HTTPS support on a platform only after a named MCP
  host passes ADR-022 Amendment 1 check 10 there (Copilot CLI on Windows).
  Until then direct HTTPS is unverified on that platform and `hive client` is
  the supported path.

Do not place the token in shell history or checked-in MCP configuration.
Copilot documents MCP `timeout` for tool discovery and tool calls, including
its connection budget. On the measured Windows host, cold stdio initialization
was terminated after ~4.1 seconds even with `timeout: 30000`. This is an
observation on that host, **not** a documented universal initialize deadline
or a guarantee about other versions. Measure a cold start; increasing
`timeout` alone did not resolve that host's startup failure.

## Rollback

```bash
hive service uninstall                 # stop + remove the daemon
cp "$BK" "$HOME/.claude.json"          # restore the uvx hive-vault entry
```

(`$BK` is the backup path printed in step 4. Without it, set the entry back to
`command: "uvx"`, `args: ["hive-vault"]`.)

## Notes

- **Coexistence during the switch.** A session already running `uvx hive-vault`
  and the new daemon both own the vault git + SQLite until that session ends.
  This is lock-protected (the `.git/hive.lock` filelock serialises commits; the
  SQLite trackers run WAL + `busy_timeout`), so it is safe but not the intended
  single-owner steady state. New sessions use `hive client` → one owner.
- **VAULT_PATH.** The Linux unit bakes `Environment=VAULT_PATH=` (systemd
  `--user` starts with a minimal environment) plus an optional
  `EnvironmentFile=-~/.config/hive/hive.env` for secrets. Windows Scheduled
  Tasks inherit the user-profile environment, so a user-level `VAULT_PATH` (or
  the default `~/Projects/knowledge`) is used as-is.
- **Restart-on-upgrade.** Once supervised, a platform-appropriate upgrade
  (`uv tool upgrade hive-vault` on Linux/macOS; `hive self-upgrade` on Windows)
  is picked up automatically: the daemon polls its installed version and, on
  drift, exits `75` so `Restart=on-failure` / `RestartOnFailure` relaunches into
  the new code. No manual restart needed.
- **Fleet rollout.** Repeat per machine, or use the dotfiles `setup-*.sh`
  automation, which flips `mcp-servers.json` (`uvx hive-vault` → `hive client`)
  and runs `hive service install` (defensively — skipped with a warning if the
  installed version predates `hive service`).
