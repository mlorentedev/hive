---
id: "hive-437-stable-client-startup"
type: troubleshooting
status: active
severity: high
tags: [mcp, windows, startup, daemon, credential]
created: "2026-09-29"
owner: manu
---

# Stdio client misses the initialize deadline

## Symptoms

An MCP host closes `hive client` before its first `initialize` response, or
the client reports an unavailable daemon without starting a fallback server.
The fixed endpoint and token survive ordinary daemon restarts, but service
installation and client-registration rollout are separate work (#176).

## Diagnosis

Run the installed-entrypoint benchmark on the supported Windows Python 3.12
baseline with a healthy daemon:

```bash
uv run python -m pytest tests/test_client.py -k "initialize_response" -q
```

The test measures from process launch to the first JSON-RPC response, **not**
until the client exits after stdin closes. It checks five cold starts and
reports their timings on failure. If it fails, measure the cost of starting
an idle Python process on the same host before changing Hive: OS process
startup and antivirus scanning are part of the elapsed time. Repeat when
the machine is not saturated; a CPU-bound host is not a valid cold-start
baseline. Do not raise the MCP host's tool timeout to mask its separate
initialize deadline.

The old client imported `_daemon`, which imported `hive.config` and
`pydantic-settings` before it could answer. On Windows, reading the credential
then launched multiple `icacls` subprocesses to verify its ACL. The adapter
now reads through stdlib-only `_credential` without importing `_daemon` or
`subprocess`, and checks the file owner and sole allow ACE through Win32
security APIs in-process. A missing, corrupt, or permission-invalid token
fails explicitly; no unverified credential is used.

## Verification

```bash
uv run python -m pytest tests/test_endpoint.py tests/test_daemon_state.py tests/test_client.py -q
uv run python -m pytest tests/test_delegate_deadline_and_route.py tests/test_delegate_review_findings.py -q
```

The second command guards `hive delegate`: its independent pre-submission
fallback must still work after the stdio client's old proxy helpers are
removed. `hive client` itself never falls back to another vault owner.
