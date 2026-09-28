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
  initialized in about 17 milliseconds. Its MCP `timeout: 30000` setting does
  not extend this separate initialize-handshake deadline.
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

The broader industry invariant is a **stable rendezvous endpoint**, not
necessarily a fixed numeric TCP port. Docker, Podman, and systemd commonly use a
stable Unix socket or named pipe. Hive selects loopback HTTP because the target
MCP hosts share HTTP support but do not share support for platform-specific IPC.

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

### Stable rendezvous

Hive will expose one stable Streamable HTTP rendezvous endpoint on `127.0.0.1`
using a documented default port and a configuration override. The steady-state
daemon MUST NOT silently select another port.

The fixed loopback port is the cross-platform realization of the stable
rendezvous invariant for the current host matrix. A future Unix socket or
Windows named-pipe adapter is compatible with this decision if it preserves the
same stable address and does not reintroduce per-restart discovery.

The documented default is per-user, not machine-wide. Every implementation uses
the same versioned formula:

```text
identity = "posix:" + decimal_uid | "windows:" + canonical_sid
digest   = SHA-256("hive-daemon-port-v1\0" + identity)
port     = 49152 + (big_endian_uint16(digest[0:2]) mod 16384)
```

`decimal_uid` is base-10 with no leading zeros. `canonical_sid` is the numeric
`S-1-...` string returned for the process token, never an account or domain
name.

The range is the IANA dynamic/private range. The formula lets `hive service
status`, setup, and clients independently compute the same endpoint without a
runtime discovery file. The configuration override handles the residual hash
collision; it is not the primary means of avoiding multi-user conflict.

If the configured port is occupied by another process, startup fails closed with
an actionable diagnostic. The supervisor must not publish a replacement endpoint
or rewrite clients around the conflict.

The daemon bearer token persists across ordinary restarts in an owner-only store.
Creation and replacement are atomic: write a new file, enforce and verify
owner-only permissions or ACLs, then publish it. A permission failure is a
startup failure, not a warning.

Rotation is explicit or security-triggered, not coupled to process lifetime.
Before publishing the new token, Hive atomically persists a rotation record with
the old and new token identifiers and an absolute expiry timestamp. The daemon
accepts both tokens only while that persisted record is unexpired and Hive
atomically updates registrations it owns. Startup recovery reads the record
before accepting requests: at or after expiry it accepts only the new token and
removes the old credential, so a crash cannot extend the handoff window.

Missing or corrupt credentials or rotation state fail closed and require the
same generate, permission-verify, publish, and client-handoff sequence. Tokens
never appear in stdout, stderr, logs, or diagnostics.

Hive follows the MCP Streamable HTTP security requirements: validate the
`Origin` header, bind only to localhost, and authenticate every connection.

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

For stdio, credentials come from the process environment or an owner-only local
store rather than an OAuth negotiation, matching MCP's authorization guidance
for stdio transports.

All generated agent configurations derive from one declarative registry.
Reconciliation compares the effective command or URL, not mere entry presence.

ADR-019 launcher ownership and #328 executable resolution remain implementation
prerequisites, not work replaced by this decision. The downstream spec must
cover a launcher that resolves through `current`, verified executable-resolution
order, a fresh-shell `hive --version`, and service installation with a broken uv
trampoline present.

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
only when an acquisition trust profile is configured. The profile must bind the
exact version to an allow-listed package origin and verify the downloaded
artifact digest against authenticated release metadata or provenance. TLS,
readiness, and tool-catalog checks alone do not establish artifact authenticity.
If provenance or digest verification is unavailable, Hive remains notify-only
under ADR-020.

An eligible update uses this sequence:

1. Resolve an exact candidate version and verify its origin, digest, and
   provenance before executing any candidate code.
2. Build the candidate beside the active version and write an immutable manifest
   binding the directory to that version and digest.
3. Start it on an ephemeral validation port. A retry must re-verify the manifest,
   artifact digest, and validation checks; an existing directory is never
   accepted merely because it exists.
4. Require version, readiness, MCP initialize, and tool-catalog contract checks.
5. Persist an atomic transition record naming active, previous, and candidate
   runtimes.
6. Stop the active daemon and atomically repoint `current`.
7. Start the candidate on the stable endpoint.
8. Require post-switch readiness within a bounded deadline.
9. On failure or interrupted recovery, restore the recorded previous pointer and
   restart the previous version.
10. Retain the previous runtime until post-switch readiness succeeds and the
    rollback window closes; garbage collection cannot remove last-known-good
    before then.

Major-version changes remain explicit until their compatibility policy is
accepted separately. A failed update is visible and leaves the last known-good
runtime selected.

## Rationale

A stable loopback rendezvous removes the only state that currently forces every
MCP host to participate in daemon lifecycle management. The port is
configuration, not discovery data.

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

- Hive must reserve and document a default per-user port and its per-account
  derivation formula.
- A stable bearer token has a longer lifetime and requires explicit rotation.
- Secure rotation requires a bounded dual-token handoff and atomic client
  registration updates.
- Candidate validation and rollback add installer and integration-test
  complexity.
- Unattended updates require verifiable artifact provenance; deployments without
  it remain notify-only.
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
- Tests prove that retries revalidate an existing candidate, interrupted
  transitions restore the recorded previous runtime, and garbage collection
  cannot remove last-known-good before the rollback window closes.
- Tests prove that an unattended candidate with missing, mismatched, or
  untrusted provenance/digest is rejected before candidate code executes.
- Tests prove deterministic port parity across Python, PowerShell, and shell
  implementations for representative POSIX UIDs and Windows SIDs.
- Tests prove atomic credential creation, checked Windows ACLs and POSIX modes,
  cross-restart continuity, bounded dual-token rotation, old-token revocation,
  crash recovery before and after persisted expiry, and recovery from missing or
  corrupt credentials or rotation state.
- Tests prove no token appears in stdout, stderr, logs, or generated diagnostics.

## References

- #437 — Hive stdio startup exceeds GitHub Copilot CLI initialize deadline
- #328 — self-upgrade launcher and executable resolution
- ADR-011 — Phase C single-owner daemon model
- ADR-015 — Windows daemon supervision and auto-upgrade
- ADR-019 — launcher ownership on Windows
- ADR-020 — client upgrade policy
- [Enterprise pattern research](../research/copilot-20260928-141709-stable-local-mcp-endpoint.md)
- [MCP Streamable HTTP transport specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
