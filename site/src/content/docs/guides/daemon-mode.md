---
title: Daemon Mode
description: Run Hive as a single-owner background daemon with auto-update.
---

By default Hive runs as a **per-session stdio server**: your MCP client spawns
`uvx hive-vault` on every session and tears it down when the session ends. That
is the simplest mode and needs nothing on this page.

**Daemon mode** is an optional upgrade. Instead of one server per session, a
single long-running `hive serve` process owns the vault, and each session
connects through a thin `hive client` shim that proxies to it. It is worth
enabling when you run several concurrent sessions on one machine, want the
single-owner guarantees of [ADR-011](https://github.com/mlorentedev/hive/blob/master/docs/adr/adr-011-phase-c-daemon-model.md),
or want clients to always pick up the latest published version automatically.

:::caution[Single-user hosts only until #456 is resolved]
The stable endpoint serves TLS with a per-user certificate that every Hive
client pins, so a listener impersonating the stable port during daemon
downtime fails the handshake before any bearer is sent. Do not deploy daemon
mode on untrusted multi-user hosts until [#456](https://github.com/mlorentedev/hive/issues/456)
is resolved: the Linux and Windows evidence, including the cross-user check,
is still being recorded. If the daemon is absent or unhealthy, `hive client` **fails
explicitly**; it never starts a competing in-process server.
:::

## The two processes

| Command | Role |
|---|---|
| `hive serve` | The daemon. Serves MCP over loopback streamable-HTTP on TLS 1.3 with a per-user certificate, bearer-token gated. One owner of the vault git + SQLite per machine (enforced by a singleton lock). |
| `hive client` | A thin stdio shim your MCP client launches. Proxies to a running `hive serve`; fails explicitly if none is reachable. |
| `hive service` | Installs/removes the OS supervisor that keeps `hive serve` running. See below. |

In daemon mode your MCP client is registered with `hive client` instead of
`uvx hive-vault` — the client process is cheap to spawn and adds no
vault-loading latency to session start.

## Enabling it

`hive service` wires `hive serve` into your OS supervisor so it starts on login
and restarts on failure. It is cross-platform: **systemd `--user`** on Linux,
**Task Scheduler** on Windows.

```bash
uv tool install --upgrade hive-vault   # needs hive-vault >= 1.32.0
hive service install                   # render unit/task, enable, start
hive service status                    # supervisor's view (active/running)
```

- **Linux** writes `~/.config/systemd/user/hive.service`
  (`Restart=on-failure`, `WantedBy=default.target`), then runs
  `systemctl --user daemon-reload && enable --now`.
- **Windows** registers the `HiveVaultDaemon` Scheduled Task
  (`LogonTrigger` + `RestartOnFailure`).

Use `hive service install --no-enable` to write the unit/task without starting
it, and `hive service uninstall` to stop and remove it.

The last step is to point your MCP client at the daemon — flip the `hive` entry
from `uvx hive-vault` to `hive client`. The full per-machine procedure
(verifying the daemon serves, the surgical `~/.claude.json` edit, and rollback)
lives in the [Daemon Activation runbook](https://github.com/mlorentedev/hive/blob/master/docs/runbooks/daemon-activation.md).

For **GitHub Copilot CLI** on a trusted single-user host, register
`copilot mcp add hive -- hive client` in user scope. Set `HIVE_DAEMON_PORT`
for both the daemon and client if you override the default. Disable an old
`hive-vault` entry after checking the new one; avoid two registered vault
owners. The automated
smoke in `tests/test_daemon.py` initializes in under 4.1 seconds, lists tools,
and calls `vault_health` over the real daemon via stdio and direct HTTPS; it
does not launch Copilot itself. Copilot documents MCP `timeout` for discovery
and tool calls, including its connection budget. Cold initialization was
terminated after ~4.1 seconds on the measured Windows host despite
`timeout: 30000`; that is an observation, not a documented universal deadline.
`hive client` is the recommended registration. A direct registration is
supported only as `https://127.0.0.1:<port>/mcp`, with the host verifying the
daemon certificate: for Node-based hosts such as Copilot CLI, set
`NODE_EXTRA_CA_CERTS=<state dir>/daemon.crt` in that host's environment. Never
disable verification (`NODE_TLS_REJECT_UNAUTHORIZED=0` or an "insecure"
flag); a host that cannot verify uses `hive client`. `http://` registrations
no longer work, because the daemon is TLS-only. Direct HTTPS is verified on
Linux; until the same check passes on Windows, use `hive client` there. Keep
the bearer out of config files and shell history.

## Auto-update: restart-on-upgrade

Once supervised, the daemon keeps itself current. It polls its own installed
version and, when a newer `hive-vault` is present on disk, **exits `75`
(`EX_TEMPFAIL`)** so the supervisor's `Restart=on-failure` policy relaunches it
into the new code. No manual restart, and no latency added to client startup.

```
Linux / macOS: uv tool upgrade hive-vault
Windows:       hive self-upgrade [version]
        │
        ▼
hive serve detects version drift  # within HIVE_UPGRADE_POLL_S
        │
        ▼
exit 75  ──►  supervisor restarts  ──►  daemon now runs the new version
```

So the upgrade *mechanism* lives in the package; **how often you upgrade is a
deployment policy** you own. Linux and macOS can use a periodic `uv tool upgrade
hive-vault` (for example, a systemd `--user` timer). On Windows, use
`hive self-upgrade` deliberately; it creates a versioned runtime and atomically
switches the active junction rather than replacing files that may be in use. The
daemon adopts the installed version on its next poll.

A graceful (signal) stop or a declined install exits `0`, which under
`Restart=on-failure` does **not** trigger a relaunch — only the drift path
returns the non-zero restart code. In-flight writes are safe across a restart
because writes are at-most-once idempotent
([ADR-013](https://github.com/mlorentedev/hive/blob/master/docs/adr/adr-013-write-idempotency-at-most-once.md)).

### Tuning the poll interval

| Variable | Default | Description |
|---|---|---|
| `HIVE_UPGRADE_POLL_S` | `30.0` | Seconds between version-drift checks in `hive serve`. Lower = the daemon adopts a new version sooner after an upgrade; higher = fewer `importlib.metadata` lookups. Validated `> 0` and `<= 3600`. |

Set it in the daemon's environment (on Linux, via the unit's
`Environment=` / `EnvironmentFile=`; on Windows, the user-profile environment
the Scheduled Task inherits).

## Verifying the daemon

The daemon writes its port, bearer token, TLS key and certificate to the Hive
state directory (owner-only, `600`). It serves TLS 1.3 only; trust its own
certificate, never the system store and never `-k`:

```bash
STATE=~/.local/share/hive
PORT=$(cat "$STATE/daemon.port")
CERT="$STATE/daemon.crt"

curl -s --cacert "$CERT" "https://127.0.0.1:$PORT/health"                     # {"status":"ok","ready":true,...}
curl -s --cacert "$CERT" -o /dev/null -w '%{http_code}\n' "https://127.0.0.1:$PORT/status"  # 401 (token-gated)
curl -s --cacert "$CERT" -H "Authorization: Bearer $(cat "$STATE/daemon.token")" \
  "https://127.0.0.1:$PORT/status"                                            # 200 + metrics
```

On the first start after upgrading from a plaintext daemon, the token is
rotated once, because it was sent in plaintext before. `hive client` picks up
the new token on its next start; direct registrations must be updated.

`/status` exposes `sessions_started`, `total_calls`, and the running version —
useful to confirm a restart-on-upgrade actually adopted the new code.

## When to stay on stdio

Daemon mode is opt-in. Stay on `uvx hive-vault` if you run a single session at
a time, want zero background processes, or are on a platform without a
supported supervisor — the per-session server is fully featured and uses the
same vault and worker code. You can switch later at any time with no data
migration: `hive client` fails explicitly without a daemon rather than silently
starting another vault owner.
