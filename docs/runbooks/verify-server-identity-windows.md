---
id: verify-server-identity-windows
type: runbook
status: active
created: "2026-09-30"
owner: manu
---

# Hive: verify the daemon's server identity on Windows

> The manual Windows run of the #456 evidence (ADR-022 Amendment 1, checks 1
> to 10). Linux runs the same checks in CI; Windows needs a second local
> account and a real Copilot CLI, which CI does not have. Record every result,
> including the ones that could not run, on
> [#456](https://github.com/mlorentedev/hive/issues/456).

Use synthetic credentials only. Never paste a token, a key or the contents of
`daemon.key` into the issue, a terminal you share, or a screenshot.

## Prerequisites

- The owner's baseline host, logged in as the owner.
- A second **standard** local account (not an administrator), here
  `hiveimpostor`. Create it once from an elevated prompt:

  ```powershell
  net user hiveimpostor <a-throwaway-password> /add
  ```

- The branch under test installed for the owner (`hive --version`), and the
  same Python available to the second account (a system-wide install, or the
  `py` launcher).
- `HIVE_EVIDENCE_OPTIONAL` unset, so a missing tool fails instead of skipping.

## 1. Automated checks as the owner

From the checkout, as the owner:

```powershell
uv run pytest tests/test_client.py tests/test_delegate_deadline_and_route.py tests/test_daemon.py -k impostor -q   # check 1
uv run pytest tests/test_daemon.py -k direct_https -q                                                         # check 2 (needs node)
uv run pytest tests/test_identity.py tests/test_daemon.py -k "pre_tls_token or plaintext_era" -q              # check 4
uv run pytest tests/test_identity.py tests/test_client.py tests/test_daemon_state.py -k "reuses_existing or does_not_import or within_one_second or across_restarts" -q  # check 5
uv run pytest tests/test_identity.py tests/test_service.py tests/test_client.py -k "identity or rotate or repins" -q  # check 6
uv run pytest tests/test_service.py -k four_states -q                                                         # check 8
uv run pytest tests/test_daemon.py -k tls13_only -q                                                           # TLS 1.3 only
uv run pytest tests/test_credential_never_emitted.py -q                                                       # check 9
uv run pytest tests/test_daemon_state.py -k port_conflict_diagnostic -q                                       # diagnostic wording
```

Expected: every command passes. Paste each summary line (`N passed`) into #456.

## 2. Cross-user impostor (check 3)

1. As the owner, start the daemon once so the identity exists, then stop it:

   ```powershell
   hive service install
   hive service status        # final line: hive daemon: healthy
   schtasks /End /TN HiveVaultDaemon
   ```

2. Find the owner's stable port. It is derived from the owner's SID, so it is
   the same every time:

   ```powershell
   hive service status        # final line now: hive daemon: down
   Get-Content "$env:USERPROFILE\.local\share\hive\daemon.port"
   ```

3. Save this plaintext impostor where the other account can read it, for
   example `C:\Users\Public\impostor.py`. It prints the first bytes each
   connection sends:

   ```python
   import socket, sys
   listener = socket.create_server(("127.0.0.1", int(sys.argv[1])))
   print("listening", flush=True)
   while True:
       conn, _ = listener.accept()
       data = conn.recv(65536)
       conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
       print(data[:16].hex(), flush=True)
       conn.close()
   ```

   Run it **as the other account** in a second window:

   ```powershell
   runas /user:hiveimpostor "cmd /k py C:\Users\Public\impostor.py <PORT>"
   ```

4. As the owner, with the impostor listening:

   | Command | Expected |
   |---|---|
   | `hive service status` | final line `hive daemon: unverified listener, owner unknown` (or `… (held by another account) …`), exit 1 |
   | `hive serve` | exits 1: `stable daemon port <PORT> is already in use by owner could not be determined` (or `by another account`) |
   | an MCP initialize through `hive client` (start Copilot or Claude Code) | the client fails with `possible impersonation; nothing was sent` |
   | `hive delegate --model <m> --timeout 30 "ping"` | `task_failed`, detail names possible impersonation, not run locally |

   The impostor window must show only lines starting with `16` (a TLS
   ClientHello) and never a readable `POST` or `Authorization`.

   **`owner could not be determined` is the expected result on Windows**, not a
   failure: a standard user cannot open another user's process token, so the
   lookup is denied. `another account` appears only when the lookup is
   permitted (for example, run from an elevated prompt).

5. Close the impostor window. As the owner, restart the daemon and confirm it
   serves:

   ```powershell
   schtasks /Run /TN HiveVaultDaemon
   hive service status        # final line: hive daemon: healthy
   ```

## 3. Copilot CLI direct HTTPS (check 10)

1. Point Node at the owner's certificate for the Copilot process only (never
   a user or machine root store):

   ```powershell
   $env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.local\share\hive\daemon.crt"
   ```

   Then add a direct HTTP server named `hive-direct` to Copilot's user-scoped
   MCP configuration, following Copilot's own documentation for remote
   servers: URL `https://127.0.0.1:<PORT>/mcp` and an `Authorization: Bearer
   <token>` header. Edit the configuration file in an editor; do not pass the
   token on a command line, where it lands in shell history.

2. Start Copilot CLI and list the `hive-direct` tools. Expected: the tools
   load.

3. Stop the daemon, start the impostor from section 2 on the same port, and
   start Copilot CLI again. Expected: `hive-direct` fails to connect, and the
   impostor window shows only `16…` lines.

If Copilot does not honour `NODE_EXTRA_CA_CERTS`, record that direct HTTP is
unsupported on Windows and that Copilot uses `hive client`. That outcome does
not hold the release (ADR-022 Amendment 1, release gate).

## Cleanup

```powershell
# remove the hive-direct entry from Copilot's MCP configuration
net user hiveimpostor /delete
```

Then record on #456: the date, `hive --version`, the summary line of each
command in section 1, the four observed outcomes of section 2 step 4, and the
check 10 result.
