---
title: "Stable local MCP endpoint: enterprise pattern audit"
author: "Copilot"
created: "2026-09-28T14:17:09-06:00"
status: complete
issue: "mlorentedev/hive#437"
adr: "../adr/adr-022-stable-local-mcp-endpoint.md"
---

# Stable Local MCP Endpoint: Enterprise Pattern Audit

## Question

Does ADR-022's selected architecture match established professional practice for
a per-user local daemon: a stable loopback endpoint, owner-only authentication,
a lightweight stdio adapter, one owning supervisor, reconciliation, and
health-gated side-by-side updates with rollback?

## Verdict

**Yes.** The design is a composite of established patterns used by local daemon,
plugin, service-supervision, and updater systems.

The important qualification is that the enterprise invariant is **stable
rendezvous**, not specifically **fixed numeric TCP port**. A Unix socket path,
Windows named pipe, or socket-activation unit can provide the same invariant.
For Hive, fixed loopback HTTP is the pragmatic realization because Copilot,
Claude, Pi, and OpenCode do not share support for platform-specific IPC.

Dynamic ports are also professional, but only when one parent or broker owns the
child lifecycle and distributes the address through a private handshake. Hive
has several independent MCP hosts and no universal broker, so a rotating port
turns every restart into a cross-application configuration transaction.

## Production Analogues

| System | Rendezvous | Lifecycle owner | Relevant lesson for Hive |
|---|---|---|---|
| Docker Engine | Stable Unix socket by default; configurable endpoint | systemd or Windows service manager | Endpoint is configuration, not per-restart discovery data |
| Podman service | Stable user/system Unix socket with systemd socket activation | systemd | Infrastructure can own the stable address while the daemon process remains disposable |
| HashiCorp go-plugin | Dynamic port disclosed through a stdout handshake | The parent host that spawned the plugin | Dynamic endpoints are safe when one broker owns both discovery and lifetime |
| systemd socket/service units | Fixed socket path or TCP listener bound before service startup | systemd/PID 1 | Stable rendezvous, process-tree ownership, restart policy, and readiness are separate contracts |
| Kubernetes Service/controller | Stable Service identity over ephemeral backends | Reconciliation controller | Names the idempotent observed-state-to-desired-state control loop |
| Language Server Protocol | Per-editor stdio process, no shared endpoint | The editor | Stdio is appropriate for cheap 1:1 processes, not an expensive shared single-owner daemon |
| TUF/Uptane | Not an endpoint system | Update client and signed metadata | Last-known-good, rollback resistance, and visible update failure are update invariants |
| Omaha | Client update check, staged apply, result reporting | Updater | Validates the check-stage-apply-verify-report shape used by ADR-022 |

## Named Patterns

### Stable rendezvous / stable front door

Clients connect to one durable address. The backing process may restart, but the
address is not rewritten on each process lifetime.

Examples include Docker's socket, Podman's socket-activated service, and
systemd's `ListenStream=` listener ownership.

### Brokered dynamic endpoint

An ephemeral address is returned through an out-of-band handshake to the same
parent that launched the child. HashiCorp go-plugin is the canonical comparison:
the host starts the plugin, reads its handshake, and owns reconnection state.

This pattern does not fit Hive's direct multi-host topology. Copilot, Claude, Pi,
and OpenCode are independent and do not share one broker or configuration reload
protocol.

### Supervisor and process-tree ownership

One supervisor owns every child in the daemon tree. A failed readiness deadline
must terminate that tree before retry or rollback. A surviving orphan is a
supervision failure, even if its process still answers.

### Reconciliation/controller loop

A condition-based, idempotent loop compares observed state with desired state and
converges only when they differ. "Process exists" is insufficient; Hive must
verify service registration, listener ownership, readiness, selected version,
and client-registration state it owns.

### Transactional A/B update and last-known-good

Build the candidate beside the active version, validate it independently,
atomically switch selection, perform a bounded post-switch readiness check, and
restore the previous selection on failure.

The update result must distinguish current, deferred, unavailable, failed, and
rolled-back states. A failed update cannot look like success.

### Readiness gate

Started is not ready. Traffic or client connection is allowed only after the
candidate actively proves readiness and its required MCP contract.

## Transport Tradeoffs

### Fixed loopback TCP

This is the most interoperable option for Hive's current clients. Every target
host can express an HTTP URL, whereas Unix socket and named-pipe support varies.

Loopback alone is not a security boundary. MCP requires Origin validation and
recommends localhost binding plus authentication. Hive therefore needs all
three, with credentials kept in an owner-only store and never printed.

### Unix socket or Windows named pipe

These are more idiomatic local-daemon transports because OS permissions or ACLs
protect the endpoint directly. They are good future adapters, but cannot replace
the HTTP rendezvous while target MCP hosts lack consistent IPC support.

Adding such adapters later is compatible with ADR-022 if they preserve stable
rendezvous and do not require clients to rediscover state after each restart.

### Dynamic port plus handshake

This is correct when one parent owns spawn, discovery, connection, and shutdown.
It is incorrect when several independent hosts must observe a file and rewrite
their own persistent configurations after every restart.

## Assessment of ADR-022

ADR-022 is sound. The research produced two documentation refinements:

1. State the invariant as **stable rendezvous** and describe the fixed loopback
   port as the interoperability-driven implementation for the current host
   matrix.
2. Cite MCP's transport security rules directly: validate Origin, bind to
   localhost, and authenticate connections.

No evidence contradicted the selected supervisor, reconciliation, readiness, or
transactional update design.

## Primary Sources

- [MCP Streamable HTTP transport and security](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [MCP authorization guidance](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
- [Docker contexts and endpoint configuration](https://docs.docker.com/engine/manage-resources/contexts/)
- [Docker daemon access protection](https://docs.docker.com/engine/security/protect-access/)
- [Docker daemon CLI reference](https://docs.docker.com/reference/cli/dockerd/)
- [Podman system service and socket activation](https://docs.podman.io/en/latest/markdown/podman-system-service.1.html)
- [HashiCorp go-plugin handshake internals](https://raw.githubusercontent.com/hashicorp/go-plugin/main/docs/internals.md)
- [HashiCorp go-plugin reattach model](https://raw.githubusercontent.com/hashicorp/go-plugin/main/README.md)
- [systemd socket units](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html)
- [systemd service supervision and readiness](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html#Restart=)
- [Kubernetes controllers](https://kubernetes.io/docs/concepts/architecture/controller/)
- [Kubernetes readiness and liveness probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
- [The Update Framework security model](https://theupdateframework.io/docs/security/)
- [Omaha update protocol](https://raw.githubusercontent.com/google/omaha/main/doc/ServerProtocolV3.md)
- [Language Server Protocol overview](https://microsoft.github.io/language-server-protocol/overviews/lsp/overview/)

## Confidence and Remaining Gap

Confidence is **high** for the stable-rendezvous verdict and for the condition
under which dynamic ports are safe. The sources are primary specifications and
production project documentation.

The remaining gap is a primary case study matching Hive's exact combination of
four independent MCP hosts plus one shared local daemon. No source is expected
to match that product topology exactly; the decision is supported by converging
patterns across the analogous systems above.
