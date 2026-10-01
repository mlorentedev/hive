---
id: "ADR-022-stable-local-mcp-endpoint"
type: adr
status: accepted
owner: manu
date: "2026-09-28"
issue: "mlorentedev/hive#437"
tags: [architecture, mcp, transport, windows, reliability, upgrades, security, tls]
created: "2026-09-28"
---

# ADR-022: Stable Local MCP Endpoint and Transactional Self-Update

## Status

Accepted.

This decision amends ADR-011's daemon endpoint contract, completes the endpoint
ownership left open by ADR-015, and narrows ADR-020's prohibition on unattended
updates: compatible updates may auto-apply only through the transactional gates
defined here.

**Amended 2026-09-30 ([#456](https://github.com/mlorentedev/hive/issues/456)).**
The stable endpoint serves TLS only, and no client sends the bearer or any tool
traffic before the server has proven its per-user identity on the same
connection. See [Amendment 1](#amendment-1-server-identity-before-secrets). It
replaces "Streamable HTTP" with "Streamable HTTP over TLS" wherever this
decision describes the stable endpoint. It also changes the direct HTTP client
contract and adds verification requirements, including a Windows cross-user
pass. That pass is required before the release hold on #449 is reconsidered.

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

## Amendment 1: Server identity before secrets

**Date:** 2026-09-30. **Issue:** [#456](https://github.com/mlorentedev/hive/issues/456).
**Status:** Accepted. The owner chose the direct HTTP policy and the relay
mechanism on 2026-09-30.

### Why the original decision is insufficient

The original decision applied MCP's "authenticate every connection"
requirement in one direction only: the server authenticates the client. Two
of its own choices made the other direction mandatory:

- The port formula is public, so any local account can compute another
  user's endpoint.
- The bearer survives restarts, so a captured token stays useful.

While the owner's daemon is down, another local account can bind that port.
The plaintext relay (`hive client`) and any direct HTTP registration then send
the reusable bearer to that listener on the first request. A second problem
does not depend on the bearer: the listener also receives tool arguments (for
example `vault_write` content) and can return forged tool results to the
agent. A synthetic reproduction is recorded on #456.

Failing closed on a port conflict (original decision) protects the vault from
a second owner. It does not protect clients from a listener that is not Hive.

### Threat model

- **In scope:** another non-administrator account on the same host. It can
  bind any unprivileged loopback port while the owner's daemon is not running.
  It cannot read the owner's state directory.
- **Out of scope:** processes running as the owner, which can already read
  the token; administrators and root; remote hosts, because the endpoint binds
  loopback only.
- **Accepted:** denial of service. A squatter can stop the daemon from
  starting. The original fail-closed rule stands, and the squatter must be
  made visible (see *Diagnostics* below).
- **Protected assets:** the bearer token, the confidentiality of tool
  arguments, and the integrity of tool results.

### Added invariant

8. No client sends a reusable secret or any MCP traffic to the stable endpoint
   until the server has proven possession of the owner's identity key **on
   that same connection**. The only exception is the single-user plaintext
   mode (see *Direct HTTP clients*): while it is active, its recorded owner
   acceptance waives this invariant on that host.

The words "same connection" rule out any proof that is separate from the
request. The relay currently opens a new TCP connection per request
(`HttpRelay._open`). If identity were proven on one connection and the request
sent on another, an impostor could bind the port in between. Identity is
therefore established in the handshake of each connection before any HTTP
byte is written.

### Decision

**TLS-only stable endpoint.** The daemon serves its stable endpoint only over
TLS 1.3 or later. No plaintext listener runs beside it, not even one that
redirects to TLS. The single-user plaintext mode under *Direct HTTP clients*
replaces TLS on a host; it never adds a second listener. All routes, including
`/mcp`, `/status` and `/health`, share the one TLS listener. The bearer is still required on every MCP and `/status`
request: TLS authenticates the server, and the bearer authenticates the
client.

**Per-user identity key and certificate.**

- Hive generates an ECDSA P-256 key and a self-signed end-entity certificate
  for each user.
- The certificate carries `basicConstraints CA:FALSE`, a single subject
  alternative name `IP:127.0.0.1`, extended key usage `serverAuth`, and a
  validity of at most 398 days. If a host trusts it, it can vouch for that one
  address and cannot sign other certificates.
- The key and the certificate live in the owner-only state store beside the
  token. Both use the token's atomic sequence: generate, enforce and verify
  owner-only permissions or ACLs, then publish. The certificate is public but
  must stay owner-writable only, because it is a trust anchor.
- Startup handles bad identity material in three ways. The key never
  appears in output, logs, or diagnostics.
  - **Expired certificate** (for example after the daemon was stopped for
    longer than the validity period): startup runs a certificate rotation
    (see *Rotation*). Expiry is not exposure, so the token is kept.
  - **Over-permissive key**: treated as suspected exposure. Startup fails
    closed until a key-exposure rotation runs, which also rotates the token.
  - **Missing or corrupt key or certificate**: startup fails closed. Recovery
    is an explicit regeneration through the same atomic sequence. The token
    is also rotated, because tampering cannot be ruled out.

**Relay (`hive client`).**

- The relay uses the stored certificate as its only trust anchor (stdlib
  `ssl`), checks the hostname `127.0.0.1`, and compares the peer certificate's
  SHA-256 fingerprint with the stored one. It ignores the system trust store.
- Every TCP connection completes this verification before the relay writes
  the request line, headers, or body. The relay may reuse a verified
  connection. It never writes to one that is not verified.
- A handshake failure aborts the request. The error names a possible
  impersonation, so it is distinct from "daemon unavailable", and contains no
  credential.
- The relay reads the pin from the owner-only store at startup and may re-read
  it once after a verification failure. That covers a rotation that happened
  while it was running. Re-reading the store does not trust the server: the
  store is the owner's own file.
- Before the first request, the relay still does nothing heavier than what is
  needed for initialize, so the sub-second requirement still applies with the
  TLS handshake included.

**Direct HTTP clients.**

- The supported direct URL is `https://127.0.0.1:<port>/mcp`.
- A host may use it only if it is verified to validate the server certificate
  against the per-user certificate. For Node-based hosts such as Copilot CLI,
  that means `NODE_EXTRA_CA_CERTS` pointing at the certificate file, so the
  trust applies only to the process that needs it. Hive does not add the
  certificate to an operating-system or user root store.
- Disabling verification is never supported. Examples: setting
  `NODE_TLS_REJECT_UNAUTHORIZED=0`, or an "insecure" flag.
- A host that cannot verify the certificate uses `hive client`.
- `http://` registrations are no longer part of the supported contract.
  Reconciliation of registrations that Hive owns rewrites them to the
  supported form or reports them as drift. Hive never generates them.
- **Single-user plaintext exception.** The endpoint is TLS-only by default.
  The only exception is a host-wide plaintext mode: an explicit owner risk
  acceptance for a single-user host, recorded in that host's managed
  configuration. Never a default.
  - It replaces TLS on the stable port; it does not add a second listener.
    The port serves either TLS or plaintext, never both.
  - While the mode is active, the relay and Hive-owned registrations use
    `http://`, reconciliation treats `http://` as conforming, and
    `hive service status` reports the endpoint as degraded ("plaintext,
    accepted by owner").
  - Turning the mode off is a plaintext-era exit: the first TLS start rotates
    the token (see *Rotation*).

**Supervisor, status, and diagnostics.**

- Readiness probes, restart-on-upgrade's wait-for-ready, and `hive service
  status` verify the pinned identity before trusting `/health`. `/health`
  keeps its unauthenticated, liveness-only payload, but over TLS.
- A listener that fails identity verification is reported as "endpoint held
  by an unverified process". That state is degraded and distinct from "daemon
  not running".
- While the single-user plaintext mode is active, there is no identity to
  verify. Probes and wait-for-ready check `/health` over plaintext and accept
  a healthy answer as ready. `hive service status` reports the degraded
  "plaintext, accepted by owner" state, not "unverified process", and still
  exits non-zero, so the accepted risk stays visible.
- When startup fails on a port conflict, the diagnostic says whether the
  holder belongs to the current account. If the OS denies the lookup, it says
  "another account". It never names the account, and it does not move the
  endpoint.
- Candidate validation on ephemeral ports (Transactional self-update) uses the
  same identity material, so a candidate never runs plaintext with the real
  token.

**Rotation.**

- **Certificate and key rotation** is explicit, is triggered by expiry (at
  most 30 days before `notAfter`), or is triggered by suspected key exposure.
  One listener presents one certificate, so there is no dual-certificate
  window. Rotation generates a new key and certificate atomically, then
  restarts the daemon on them.
- After a certificate rotation, the relay re-pins from the store. Direct hosts
  that reference the certificate *file* pick up the new certificate on their
  next start. Hive restarts or reports any registrations it owns that still
  hold the old certificate.
- **Key exposure implies token exposure.** Anyone holding the key could have
  impersonated the server and collected the bearer. A key-exposure rotation
  therefore also runs the original decision's token rotation.
- **Exposure through the plaintext era.** Any token ever sent to a plaintext
  stable endpoint is treated as exposed. That covers the #453 implementation
  and the single-user plaintext mode. Two signals arm the rotation. First, a
  token that already exists when the TLS identity key is first generated
  predates TLS; that covers #453, which wrote no record. Second, a plaintext
  start records "token exposed since last rotation" in the owner-only state.
  Any TLS start that finds either signal rotates the token, then clears the
  record. Further TLS starts rotate nothing until plaintext is used again, so
  every re-entry into plaintext mode triggers a new rotation.

### Compatibility

The change is breaking for every `http://` stable-endpoint registration and for
any client that disables certificate verification. To migrate, re-register the
host with the HTTPS URL and certificate trust, or switch it to `hive client`.
The plaintext stable endpoint (#453) is on `master` but in no release, because
#449 is held. The release that lifts that hold must contain this amendment's
implementation, and its release notes must state the break. Plaintext stable
endpoints are never published.

### Alternatives considered

- **Operating-system peer-credential check** (owner of the accepted socket: the
  Linux `/proc/net/tcp` UID, or Windows owning PID → SID). It only works for
  the relay, needs code for each platform, and does not protect traffic
  confidentiality. Rejected as the primary mechanism.
- **HMAC challenge-response per connection.** Also relay-only, and a custom
  protocol to maintain, with no confidentiality. Rejected.
- **Unix socket or Windows named pipe protected by OS ACLs.** This
  authenticates the server through the filesystem or pipe ACL, but the target
  MCP hosts do not share support for it. It remains a compatible future adapter
  under the original decision.
- **Plaintext with the risk accepted for single-user use.** This is Jupyter
  Server's model: a token over plaintext loopback. Rejected as a default,
  because Hive's Windows baseline is a multi-user host. It is kept only as an
  explicit per-host owner exception.
- **Trusting the certificate through the OS root store** (for example Windows
  `CurrentUser\Root`). This changes trust for every TLS client of that user and
  can prompt the user, so it is broader than one process needs. Rejected in
  favour of per-process trust.

### Rationale

- RFC 6750 §5.3 requires clients to send bearer tokens only over TLS and to
  validate the server certificate. A bearer sent in plaintext to an
  unauthenticated listener is exactly what that section forbids.
- Docker's daemon takes the same position. Its default is a
  permission-protected socket, and TCP exposure requires mutual TLS. Hive
  cannot use the socket with its host matrix, so the endpoint has to use TLS.
- Only TLS lets static-configuration HTTP hosts authenticate a server, so one
  mechanism covers both the relay and direct HTTP.
- `cryptography` is already resolved transitively, and uvicorn already
  supports TLS. The daemon imports `cryptography` directly to generate the key
  and certificate, so the implementation declares it as a direct dependency.
  The relay stays stdlib-only (`ssl`) and never imports `cryptography`, which
  protects its sub-second initialize.

### Additional verification requirements

All checks use **synthetic credentials only**: an isolated state directory, a
generated key, certificate and token, and an ephemeral or overridden port.
They never use a live vault credential. Every check runs on Linux and on
Windows unless marked otherwise.

1. **Spoofed listener, relay.** Put a plaintext impostor, and a TLS impostor
   with a different self-signed certificate, on the configured port. The relay
   aborts in the handshake. The impostor socket receives no HTTP request
   bytes, so it sees no `Authorization` header and no body. Assert this from
   the impostor's side.
2. **Spoofed listener, direct HTTP.** A client configured per the supported
   direct contract refuses both impostors: Node with `NODE_EXTRA_CA_CERTS`, or
   a stand-in with the same trust configuration. With the single-user
   plaintext mode off, reconciliation reports an `http://` registration as
   drift. With it on, `hive service status` reports the degraded plaintext
   state.
3. **Cross-user.** The impostor runs as a second local account without
   administrator rights while the daemon is down. Both clients refuse it. The
   daemon's start then fails closed, with a diagnostic that tells "another
   account" apart from "this account". Once the impostor exits, the legitimate
   daemon starts and both clients connect without any configuration change.
   On Windows this runs on the owner's baseline host. If CI cannot create a
   second account, record the gap instead of substituting a single-user proxy
   run.
4. **Post-restart replay.** Two parts, each with a token the test knows:
   - **Plaintext era.** A pre-amendment (#453) relay sends a synthetic token
     to a plaintext impostor, which captures it. The TLS-enabled daemon then
     starts on that state directory for the first time: the token exists and
     no identity key does. The captured token gets 401, and the rotated token
     is accepted.
   - **Amended clients.** The capture from checks 1 to 3 is asserted empty: it
     holds no token bytes and no body. Replaying the token that was valid
     before the impostor appeared still succeeds, which shows that the
     protection is non-disclosure, not revocation.
5. **Positive restart.** An ordinary restart keeps the key, certificate, pin,
   and token. A cold `hive client` initialize over TLS keeps the original
   sub-second requirement on the Windows baseline.
6. **Identity material.** Creation is atomic. POSIX modes and Windows ACLs are
   checked. An expired certificate is regenerated at startup and the token
   kept. An over-permissive key fails closed until a key-exposure rotation
   rotates the key, certificate, and token. A missing or corrupt key or
   certificate fails closed until explicit regeneration, which also rotates
   the token. After any rotation, the relay re-pins and the old certificate is
   refused.
7. **Plaintext-era rotation.** Both signals are tested. First, a pre-existing
   token with no identity key. Second, a recorded plaintext start. In each
   case the next TLS start rotates the token, and a second TLS start rotates
   nothing. Then re-enable
   plaintext mode, use it, and return to TLS: the token rotates again.
8. **Status and readiness.** Probes and `hive service status` reject a listener
   that fails identity verification and report it apart from "down".
9. **No secret output.** No token or private key material appears in stdout,
   stderr, logs, or diagnostics. This extends the original requirement.
10. **Copilot on Windows (owner's baseline host).** Copilot CLI accepts the
    per-user certificate through `NODE_EXTRA_CA_CERTS`, or a documented
    equivalent, and refuses an impostor. If it does not, direct HTTP is
    unsupported on Windows and Copilot uses `hive client`. That outcome does
    not block #449.

**Release gate.** The hold on #449 is reconsidered only after both of these:

- Checks 1 to 9 pass on Linux and on Windows. The Windows evidence, including
  check 3 on the owner's baseline host, is recorded on #456.
- Any check that the environment cannot run is listed there as missing
  evidence.

The stand-in client in check 2 proves Hive's side of the direct contract. It
does not prove that any real host honours it. So the support statement for a
real direct HTTP host on Windows rests on check 10 alone. Until check 10
passes on the baseline host with a real Copilot CLI, the release notes must
say that direct HTTP is unverified on Windows and that `hive client` is the
only supported Windows transport. Lifting the hold does not depend on check
10. The direct HTTP support statement does.

If checks 1 to 9 lack evidence on either platform, the hold stays in place
unless the owner records an explicit acceptance of the risk that names the
missing checks. Check 10 never holds it.

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
- #456 — bearer theft via a spoofed stable listener (Amendment 1)
- [RFC 6750 §5.3](https://www.rfc-editor.org/rfc/rfc6750#section-5.3) — bearer tokens require TLS and certificate validation
- [Docker: protect the daemon socket](https://docs.docker.com/engine/security/protect-access/) — socket by default, TLS for TCP
- [Jupyter Server security](https://jupyter-server.readthedocs.io/en/latest/operators/security.html) — the plaintext-loopback model not chosen
- [Node.js `NODE_EXTRA_CA_CERTS`](https://nodejs.org/api/cli.html#node_extra_ca_certsfile) — per-process trust for direct HTTP hosts
