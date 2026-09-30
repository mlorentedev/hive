---
id: "HIVE-437-stable-local-mcp-endpoint"
type: spec
status: implementing # draft | implementing | verifying | archived
created: "2026-09-28"
issue: "mlorentedev/hive#437"   # repo#NNN — GitHub issue / Project item that tracks this spec
tags: [spec, proposal]
template_version: "1.0"
---

# HIVE-437-stable-local-mcp-endpoint

> **Naming**: file lives at `<repo>/specs/HIVE-437-stable-local-mcp-endpoint/proposal.md`. `HIVE-437-stable-local-mcp-endpoint` is `AREA-NNN-slug` (e.g. `TOOL-001-secret-drift`).

## Why

<!-- from issue #437: Hive stdio startup exceeds GitHub Copilot CLI initialize deadline -->

GitHub Copilot CLI closes a local stdio MCP process when initialization takes
about 4.1 seconds. Hive's current `hive client` starts through the full
`hive.server` import graph and measured 4.586 seconds even with a healthy daemon,
while direct daemon HTTP initialized in about 17 milliseconds. The direct HTTP
workaround is not durable because the daemon currently rotates both its port and
bearer token on every restart, leaving client configuration stale.

## What

1. `hive serve` binds a deterministic per-user loopback port by default, with a
   validated `HIVE_DAEMON_PORT` / `--port` override, and fails closed when the
   selected port belongs to another process.
2. The daemon reuses an atomically published, owner-only bearer token across
   ordinary restarts instead of generating a new credential each time.
3. The installed `hive client` entrypoint dispatches without importing
   `hive.server` or FastMCP and relays stdio JSON-RPC to the stable HTTP endpoint.
   It reports daemon or credential failure explicitly and never starts a second,
   unmanaged in-process server.

## Out of scope

- Service installation and cross-agent configuration rollout, owned by #176 and
  the dotfiles repository.
- Transactional runtime acquisition, provenance verification, rollback, and
  garbage collection, owned by #292 and #328 under ADR-022's amended contract.
- Explicit credential rotation and dual-token handoff; ordinary restart
  continuity is required here, while rotation needs its own CLI/API contract.
- Remote or multi-user daemon access, OAuth, Unix sockets, named pipes, or a
  fixed proxy in front of versioned daemon processes.

## Risks / open questions

- **Resolved — protocol relay:** Hive does not issue server-initiated requests,
  so the adapter can relay one stdio JSON-RPC message per Streamable HTTP POST,
  stream SSE `data:` frames when returned, retain legacy `Mcp-Session-Id`, and
  propagate the negotiated `MCP-Protocol-Version` without constructing a
  FastMCP proxy.
- **Resolved — deterministic identity:** the port formula uses `os.getuid()` on
  POSIX and the current process token's canonical numeric SID on Windows. A
  failure to obtain the platform identity is fatal rather than silently using a
  machine-wide fallback.
- **Resolved — credential integrity:** token creation uses a same-directory
  temporary file, permission/ACL enforcement and verification, and `os.replace`.
  Existing credentials are accepted only when non-empty and owner-only.
- **Resolved — migration:** `daemon.port` remains as temporary diagnostic
  metadata, but clients compute the endpoint independently and never use it as
  discovery state.

## Acceptance criteria

- [ ] **AC1 — deterministic endpoint:** the documented v1 formula returns the
  same port for representative POSIX UIDs and Windows SIDs, stays in
  `49152..65535`, honors a valid explicit override, and rejects invalid ports.
- [ ] **AC2 — restart-stable daemon:** two ordinary daemon starts for the same
  identity bind the same URL and reuse the same token; an occupied configured
  port produces a non-zero actionable failure without selecting another port.
- [ ] **AC3 — sub-second thin adapter:** the installed `hive client` path does
  not import `hive.server`, `fastmcp`, or `mcp`; against a healthy daemon its
  first initialize response completes in under one second on the supported
  Windows baseline.
- [ ] **AC4 — explicit failure:** missing, corrupt, permission-invalid, or
  unreachable daemon state makes `hive client` emit a JSON-RPC error or
  actionable stderr and exit non-zero; it never constructs an in-process Hive
  server.
- [ ] **AC5 — transport continuity:** the adapter forwards requests,
  notifications, JSON responses, SSE responses, protocol-version headers, and
  legacy session IDs without leaking the bearer token.
- [ ] **AC6 — secure persistent credential:** token creation/replacement is
  atomic, POSIX mode or Windows ACL verification is fail-closed, ordinary
  restarts preserve the token, and tests prove the token is absent from stdout,
  stderr, logs, and diagnostics.
- [ ] **AC7 — restart smoke:** a client configured once against the deterministic
  endpoint can list tools and call `vault_health` before and after a daemon
  restart without rewriting its URL.

## References

- Bitácora: #437
- Architecture: `docs/adr/adr-022-stable-local-mcp-endpoint.md`
- Existing daemon contract: `docs/adr/adr-011-phase-c-daemon-model.md`
- Service rollout: #176 / `docs/adr/adr-015-windows-daemon-supervision-upgrade.md`
- Runtime ownership: #328 / `docs/adr/adr-019-launcher-ownership.md`
- Upgrade policy: #292 / `docs/adr/adr-020-client-upgrade-policy.md`
