---
id: "HIVE-456-stable-endpoint-server-identity"
type: spec
status: draft # draft | implementing | verifying | archived
created: "2026-09-30"
issue: "mlorentedev/hive#456"   # repo#NNN — GitHub issue / Project item that tracks this spec
tags: [spec, proposal, security, tls, transport]
template_version: "1.0"
---

# HIVE-456-stable-endpoint-server-identity

> **Naming**: file lives at `<repo>/specs/HIVE-456-stable-endpoint-server-identity/proposal.md`. `HIVE-456-stable-endpoint-server-identity` is `AREA-NNN-slug` (e.g. `TOOL-001-secret-drift`).

## Why

<!-- from issue #456: BUG-003: Prevent bearer theft via spoofed stable daemon listener -->

The stable endpoint from #453 publishes a predictable per-user port and keeps
the bearer across restarts. While the daemon is down, another local account
can bind that port. Every Hive client then sends the reusable bearer to it in
plaintext on the first request, and the same listener also receives tool
arguments and can forge tool results. The affected clients are `hive client`,
`hive delegate`, and direct HTTP registrations. ADR-022 Amendment 1 (#460)
decides the fix. Until it ships, release 4.3.0 (#449) stays held.

## What

1. `hive serve` serves the stable endpoint over TLS 1.3 only. It uses a
   per-user ECDSA P-256 key and a self-signed `CA:FALSE` certificate whose only
   SAN is `IP:127.0.0.1`. Key and certificate are stored owner-only beside the
   token and created with the token's atomic generate, verify, publish
   sequence.
2. `hive client` and `hive delegate` verify that certificate on every TCP
   connection before they write a byte. Verification uses the certificate as
   the only trust anchor and also matches its SHA-256 fingerprint. A failure is
   reported as a possible impersonation, distinct from "daemon unavailable",
   and the report contains no credential.
3. A token that predates the identity key is rotated on the first TLS start
   and never after that. This closes the #453 plaintext exposure.
4. `hive service status` probes `/health` over pinned TLS and reports one of
   four states: healthy, unverified listener, down, or owner unknown. A port
   conflict at daemon startup names one of three owners: this account, another
   account, or owner could not be determined.
5. Startup regenerates an expired certificate and keeps the token. An
   over-permissive key fails closed until `hive service rotate-identity` runs,
   which rotates the key, certificate, and token. A missing or corrupt key or
   certificate fails closed until the same command regenerates it.

## Out of scope

- **Single-user plaintext mode** (ADR-022 Amendment 1, *Direct HTTP clients*).
  It is deferred to #462. The daemon is TLS-only, with no switch, and
  no plaintext-mode record is written or read.
- **Hive-owned registration reconciliation and the declarative registry.** No
  such registry exists yet; that work belongs to #176. Direct HTTP hosts are
  documented, not rewritten.
- **Release mechanics.** Release notes for #449 and registry metadata (#459)
  are handled at release time; this spec supplies the facts they must state.

## Risks / open questions

- **Resolved — TLS minimum on uvicorn:** `uvicorn.Config` exposes
  `ssl_certfile`/`ssl_keyfile` but no minimum version. The daemon calls
  `config.load()` itself and sets `config.ssl.minimum_version =
  TLSVersion.TLSv1_3` before serving. A test asserts that a TLS 1.2-only
  client is refused.
- **Resolved — delegate transport:** FastMCP's `StreamableHttpTransport`
  accepts `verify=ssl.SSLContext`, so `hive delegate` uses the same pinned
  context builder as the relay.
- **Resolved — relay import budget:** the relay imports only stdlib `ssl` and
  `hashlib`, never `cryptography`, so AC3 of HIVE-437 (sub-second initialize)
  keeps holding. Only the daemon and the rotation command import
  `cryptography`, which becomes a direct dependency in `pyproject.toml`.
- **Resolved — migration vs missing key:** a #453 state directory (token
  present, no key) and an identity whose key was later deleted look the same
  on disk. Generating the identity for the first time also writes an
  owner-only `identity.state` record. With no record, the daemon treats the
  start as a migration: it generates the key and certificate and rotates the
  token (AC4). With a record but no key or certificate, it fails closed and
  waits for `rotate-identity` (AC6).
- **Resolved — delegate pinning:** httpx cannot hook between the handshake and
  the request. So `hive delegate` relies on the trust anchor alone: an
  `SSLContext` whose only CA is the per-user `CA:FALSE` certificate. Any chain
  that verifies has to end at that anchor. OpenSSL refuses a `CA:FALSE`
  certificate as an issuer, and signing anything with it needs the owner-only
  key, so the only certificate that verifies is the anchor itself. The relay
  additionally compares the fingerprint.
- **Open — Windows second account in CI:** CI may not be able to create a
  second local account. If it cannot, AC2's cross-user half runs on the
  owner's baseline host and is recorded on #456 as manual evidence. A
  single-account proxy run never stands in for it.
- **Open — direct HTTP support claim per platform:** check 10 needs a real
  host on each platform. Until one passes, direct HTTP is documented as
  unverified on that platform.

## Acceptance criteria

The ADR-022 Amendment 1 check each criterion maps to is given in brackets.

- [ ] **AC1 — no secret to an impostor, relay and delegate** [checks 1, 4]:
  1. A plaintext impostor and a wrong-certificate TLS impostor sit on the
     configured port. `hive client` and `hive delegate` both abort in the
     handshake.
  2. The impostor records zero HTTP request bytes: no `Authorization` header
     and no body.
  3. The impostor also records an accepted connection and a TLS ClientHello,
     as the positive control.
  4. A token valid before the impostor appeared still works afterwards, which
     shows non-disclosure rather than revocation.
- [ ] **AC2 — cross-user** [check 3]: an impostor run under a second local
  non-admin account is refused by both clients. The daemon's start fails with
  "another account", or with "owner could not be determined" when the lookup
  is denied. After the impostor exits, the daemon starts and clients connect
  with no configuration change. Linux runs in CI where possible. On Windows
  this is manual evidence from the baseline host, recorded on #456.
- [ ] **AC3 — direct HTTP contract** [checks 2, 10]: a stand-in client that
  trusts only the per-user certificate refuses both impostors and talks to the
  daemon. A real Node host with `NODE_EXTRA_CA_CERTS` does the same on each
  platform where support is claimed. Copilot CLI on Windows is manual
  evidence, and a failure there does not block #449.
- [ ] **AC4 — plaintext-era replay and rotation** [checks 4, 7]:
  1. A state directory with a token and no identity key stands in for #453.
     A synthetic token captured through a plaintext relay gets 401 after the
     first TLS start, and the rotated token is accepted.
  2. A second TLS start rotates nothing.
- [ ] **AC5 — positive restart and latency** [check 5]: an ordinary restart
  keeps the key, certificate, fingerprint, and token. A cold `hive client`
  initialize over TLS stays sub-second, using the HIVE-437 AC3 measurement.
- [ ] **AC6 — identity material lifecycle** [check 6]:
  1. Creation is atomic.
  2. POSIX `0600` and the Windows owner-only ACL are verified, and the check
     fails closed.
  3. An expired certificate is regenerated at startup and the token is kept.
  4. An over-permissive key fails closed until `rotate-identity`, which
     rotates the key, certificate, and token.
  5. A missing or corrupt key or certificate fails closed until
     regeneration, which also rotates the token.
  6. After a rotation, the relay re-pins and the old certificate is refused.
- [ ] **AC7 — status and diagnostics** [check 8]: `hive service status`
  reports four distinct states: healthy, unverified listener, down, and owner
  unknown. It exits non-zero for every state except healthy.
- [ ] **AC8 — TLS-only listener** [invariant 8]: the stable port refuses
  plaintext HTTP and TLS 1.2. `/mcp`, `/status` and `/health` are served over
  the same TLS listener.
- [ ] **AC9 — no secret output** [check 9]: no token or private key material
  appears in stdout, stderr, logs, diagnostics, or exception text in any of the
  paths above.

## References

- Bitácora board: #456 (see the `issue:` frontmatter field)
- Related ADR: `docs/adr/adr-022-stable-local-mcp-endpoint.md`, Amendment 1
  (#460, follow-up #461)
- Prior spec: `specs/HIVE-437-stable-local-mcp-endpoint/` (stable endpoint,
  relay, persistent token)
- Lesson: `docs/lessons/lesson-101-a-stable-endpoint-must-prove-who-answers.md`
