---
id: "ADR-022-stable-local-mcp-endpoint"
type: adr
status: accepted
owner: manu
date: "2026-09-28"
issue: "mlorentedev/hive#437"
tags: [architecture, mcp, transport, windows, reliability, upgrades]
created: "2026-09-28"
---

# ADR-022: Stable Local MCP Endpoint and Transactional Self-Update

## Status

Accepted.

This decision amends ADR-011's daemon endpoint contract, completes the endpoint
ownership left open by ADR-015, and narrows ADR-020's prohibition on unattended
updates: compatible updates may auto-apply only through the transactional gates
defined here.

## Date

2026-09-28

## Context

Hive's single-owner daemon currently publishes a random loopback port and a new
bearer token on each start. That works only when every MCP host connects through
a client that discovers the current state on every connection.

The Windows deployment violates that assumption:

- GitHub Copilot CLI terminates a local stdio MCP process if initialization takes
  about 4.1 seconds. `hive client` took 4.586 seconds, while direct daemon HTTP
  initialized in about 17 milliseconds.
- Direct HTTP made Copilot usable, but its stored endpoint remained
  `127.0.0.1:49859` after the daemon restarted on `127.0.0.1:60412`.
- The daemon was healthy but unmanaged: the official `HiveVaultDaemon` task was
  absent, and the compatibility supervisor could exit without terminating its
  child.
- The installed runtime remained on 4.2.1 after 4.2.2 was published. Existing
  upgrade automation targets the retired uv-tool layout rather than Hive's
  versioned A3 runtime.
- Agent configurations have diverged between direct HTTP, `hive client`, and
  legacy `uvx hive-vault`.

MCP defines the HTTP endpoint and session protocol, but it does not standardize
local port discovery. A rotating endpoint therefore creates a Hive-specific
control-plane problem that every host must solve independently.

The required invariants are:

1. The supported connection is deterministic across Windows, Linux, and MCP
   hosts.
2. A healthy daemon initializes through any supported adapter in less than one
   second.
3. One supervisor owns the complete daemon process tree.
4. Restart and update do not require rewriting client configuration.
5. Compatible updates are side-by-side, verified, atomic, and reversible.
6. Credentials are never printed and remain readable only by the owner.
7. Install, reconcile, and upgrade operations converge to no change on a second
   run.

## Options Considered

### A. Fixed loopback endpoint with a lightweight stdio adapter

Run the daemon on a stable, configurable loopback port. Persist its bearer token
in an owner-only store. HTTP-capable hosts connect directly; stdio-only hosts use
a minimal proxy that does not import or construct the Hive server.

### B. Dynamic port with continuous client-config reconciliation

Keep the random port and rewrite every host's configuration whenever the daemon
restarts.

This was rejected because hosts do not share a configuration format or reload
behavior. It creates unavoidable stale windows and makes a process restart a
cross-application configuration transaction.

### C. Fixed local proxy with dynamic daemon backends

Keep a stable proxy on the public port and route to versioned daemon instances
on dynamic ports.

This was deferred. It enables zero-downtime blue/green swaps, but adds another
long-lived process and ownership boundary. Reconsider it if measured update
interruption exceeds five seconds or Hive must serve concurrent daemon versions.

### D. Stdio-only local servers

Return every host to a per-session stdio server.

This was rejected because it violates the single-owner model, repeats expensive
startup work, and cannot meet Copilot's measured initialization deadline
reliably.

## Decision

### Stable front door

Hive will expose one stable Streamable HTTP endpoint on `127.0.0.1` using a
documented default port and a configuration override. The steady-state daemon
MUST NOT silently select another port.

If the configured port is occupied by another process, startup fails closed with
an actionable diagnostic. The supervisor must not publish a replacement endpoint
or rewrite clients around the conflict.

The daemon bearer token persists across ordinary restarts in an owner-only store.
Token rotation is explicit or security-triggered, not coupled to process
lifetime. Hive continues to validate authentication and HTTP Origin and binds
only to loopback.

### Client contract

HTTP-capable hosts use the stable endpoint directly. Stdio-only hosts invoke
`hive client`, which is a thin adapter over the same endpoint.

The adapter must:

- avoid importing or constructing the FastMCP server;
- read credentials without printing them;
- return the first initialization response in less than one second on the
  supported Windows baseline;
- report daemon unavailability explicitly;
- never hide a broken daemon by starting an unmanaged competing owner.

All generated agent configurations derive from one declarative registry.
Reconciliation compares the effective command or URL, not mere entry presence.

### Supervisor and reconciliation

The installed service owns the complete process tree. A readiness timeout,
failed start, or failed upgrade must terminate that tree before retry or
rollback. Orphaned children are a service failure.

`hive service status` or its successor must verify, rather than infer:

- supervisor registration;
- selected runtime version;
- listener ownership on the configured port;
- `/health` readiness and reported version;
- client-registration drift where Hive owns that registration.

Reconciliation is condition-based and idempotent. A healthy aligned system is a
no-op; missing service registration, a dead listener, version mismatch, or
configuration drift is repaired or reported as a non-zero degraded state.

### Transactional self-update

Hive's versioned A3 runtime is the only supported Windows update target. Update
automation must not call `uv tool upgrade hive-vault`.

Compatible releases within the configured major-version channel may auto-apply
through this sequence:

1. Build the candidate beside the active version.
2. Start it on an ephemeral validation port.
3. Require version, readiness, MCP initialize, and tool-catalog contract checks.
4. Stop the active daemon and atomically repoint `current`.
5. Start the candidate on the stable endpoint.
6. Require post-switch readiness within a bounded deadline.
7. On failure, restore the previous pointer and restart the previous version.

Major-version changes remain explicit until their compatibility policy is
accepted separately. A failed update is visible and leaves the last known-good
runtime selected.

## Rationale

A fixed loopback front door removes the only state that currently forces every
MCP host to participate in daemon lifecycle management. The port is configuration,
not discovery data.

The design keeps the single-owner daemon and HTTP performance selected by
ADR-011 while preserving stdio interoperability through a genuinely lightweight
adapter. Side-by-side validation and rollback make self-update a deployment
transaction rather than an in-place mutation of a live Python environment.

## Consequences

### Positive

- Daemon restart no longer invalidates MCP host configuration.
- Copilot can use the fast direct HTTP path without a generated rotating URL.
- Claude, Pi, and other stdio-only hosts share the same daemon through one small
  adapter.
- Update failures preserve and restore the last known-good runtime.
- Service health becomes an end-to-end assertion rather than a process-exists
  check.
- Cross-agent behavior derives from one endpoint and registration contract.

### Negative

- Hive must reserve and document a default per-user port.
- A stable bearer token has a longer lifetime and requires explicit rotation.
- Candidate validation and rollback add installer and integration-test
  complexity.
- A fixed-port conflict stops the service instead of silently moving it.

### Neutral

- The daemon may still use ephemeral ports internally for candidate validation.
- A fixed proxy remains a future option if zero-downtime updates become a
  measured requirement.
- Existing dynamic `daemon.port` state may remain temporarily as migration
  metadata, but it is not the client configuration contract.

## Verification Requirements

- Windows and Linux integration tests cover install, restart, crash recovery,
  port conflict, candidate failure, rollback, and a second idempotent run.
- A cold `hive client` initialize against a healthy daemon completes in less
  than one second on the supported Windows baseline.
- A Copilot smoke test lists tools and calls `vault_health` through the stable
  endpoint.
- Tests prove that a readiness timeout terminates the spawned process tree.
- Tests prove that a failed candidate cannot change the selected runtime or
  leave client configuration pointing at a dead endpoint.
- Tests prove no token appears in stdout, stderr, logs, or generated diagnostics.

## References

- #437 — Hive stdio startup exceeds GitHub Copilot CLI initialize deadline
- #328 — self-upgrade launcher and executable resolution
- ADR-011 — Phase C single-owner daemon model
- ADR-015 — Windows daemon supervision and auto-upgrade
- ADR-019 — launcher ownership on Windows
- ADR-020 — client upgrade policy
- MCP Streamable HTTP transport specification
