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
> A process impersonating the fixed port during downtime can capture a bearer
> which remains valid across ordinary daemon restarts.

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
hive service status          # supervisor's view (active/running)
```

- **Linux** writes `~/.config/systemd/user/hive.service`
  (`Restart=on-failure`, `WantedBy=default.target`), then
  `systemctl --user daemon-reload && enable --now`.
- **Windows** registers the `HiveVaultDaemon` Scheduled Task
  (`LogonTrigger` + `RestartOnFailure`).

Use `hive service install --no-enable` to write the unit/task without starting
it (staged setup).

### 3. Verify the daemon serves

```bash
# Linux state dir is ~/.local/share/hive (HIVE state dir = the SQLite DB parent)
PORT=$(cat ~/.local/share/hive/daemon.port)
TOKEN=$(cat ~/.local/share/hive/daemon.token)

curl -s "http://127.0.0.1:$PORT/health"                      # {"status":"ok","ready":true,...}
curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$PORT/status"   # 401 (token-gated)
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/status"  # 200 + metrics
```

`daemon.token` and `daemon.port` must be owner-only (`600`).

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
daemon through both stdio and direct HTTP using synthetic credentials; it
does **not** launch the Copilot CLI itself. Copilot also supports a direct HTTP
registration, but both transports send a bearer to a listener that has not
proven its identity; do not deploy either on untrusted multi-user hosts before
#456. Do not place the token in shell history or checked-in MCP configuration.
Copilot's MCP `timeout` setting is for tool calls; it does **not** extend the
separate initialize-handshake deadline (~4.1 seconds on the measured Windows
host). Do not treat an increased `timeout` as proof of a working cold start.

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
