---
tags: [spec, tasks]
created: "2026-09-30"
---

# Tasks - HIVE-456-stable-endpoint-server-identity

> TDD order: one task is one focused commit. Tick each task as you go. Tasks can be
> reordered while the spec is `draft`; they freeze once it moves to
> `implementing`.
>
> **Markers:** `[P]` means the task depends on no unchecked task. `[AC<n>]`
> maps the task to proposal criterion *n*.
>
> **Every test uses synthetic credentials.** It runs in a `tmp_path` state
> directory (`HIVE_DB_PATH=<tmp>/worker.db`) and on an ephemeral
> `HIVE_DAEMON_PORT`. No test reads the real state directory or prints a
> token.
>
> **Missing preconditions never pass.** A test whose precondition is absent
> (no second account, no `node`) calls `pytest.fail("missing evidence: …")`;
> it never `skip`s. The one exception is `HIVE_EVIDENCE_OPTIONAL=1`, which
> local development may set. CI and the `features.json` commands never set
> it. This is lesson 094: a verification command that selects zero tests
> must not read as green.
>
> **Module layout:**
> - `src/hive/_tls.py`: stdlib only (`ssl`, `hashlib`). Builds the pinned
>   client context and checks the fingerprint. Used by the relay, `hive
>   delegate`, and `hive service status`.
> - `src/hive/_identity.py`: the daemon side. Imports `cryptography`.
>   Generates, loads, validates, and rotates the key and certificate. Owns
>   the `identity.state` record.
> - `src/hive/_endpoint.py`: gains `identity_key_path()`,
>   `identity_cert_path()`, and `identity_state_path()`.

## Setup

- [x] Branch `feat/stable-endpoint-server-identity` from `master` (at `4eb25dd` or later)
- [x] `proposal.md` complete; open risks are environmental only (they cover where evidence runs, not the design)
- [x] `/spec check HIVE-456-stable-endpoint-server-identity` returns PASS

## Implementation — PR 1: TLS endpoint, pinned clients, plaintext-era rotation

- [x] [P] [AC6] `tests/test_identity.py::test_create_identity_material_properties`.
  `create_identity(key_path, cert_path)` writes the key and certificate. The
  certificate has ECDSA P-256, `CA:FALSE`, the single SAN `IP:127.0.0.1`,
  EKU `serverAuth`, and a validity of at most 398 days. Both files pass
  `_verify_owner_only`.
  Run `uv run pytest tests/test_identity.py -q`.
  Expected: FAIL with `ModuleNotFoundError: hive._identity`.
- [x] [AC6] Add `cryptography` to `pyproject.toml` `dependencies`. Implement
  `_identity.create_identity`, reusing `_daemon._create_token`'s atomic
  temp, enforce, verify, `os.replace` sequence; extract it into a shared
  `_owner_only.write_owner_only_atomic(path, data: bytes)`. Add the three path helpers
  to `_endpoint.py`. Run `uv run pytest tests/test_identity.py -q`.
  Expected: PASS.
- [x] [AC6] `test_create_identity_is_atomic_and_never_leaves_partial_files`.
  Inject a failure between the key write and the certificate write. Assert
  that no final file exists and no temporary file is left behind. Fix until
  PASS.
- [x] [AC5] `test_load_or_create_identity_reuses_existing_material`: two
  calls return the same SHA-256 fingerprint. Implement
  `load_or_create_identity()` together with the `identity.state` record.
- [x] [AC4] `test_first_tls_start_rotates_a_pre_tls_token_once`. Set up a
  state directory with a token and no record. `prepare_daemon_credentials()`
  rotates the token and writes the key, certificate, and record. A second
  call changes nothing (same token, same fingerprint). Implement it in
  `_daemon.py` and call it from `run_serve` in place of
  `load_or_create_token()`.
- [x] [P] [AC1] `tests/test_tls.py::test_pinned_context_trusts_only_the_owner_certificate`.
  The context built by `_tls.pinned_context(cert_pem)` completes a
  handshake against an in-test TLS server that presents the pinned
  certificate. Against a second self-signed certificate, it fails with
  `ssl.SSLCertVerificationError`. `_tls.fingerprint_matches(sock,
  expected)` compares the peer's DER SHA-256. Implement `_tls.py`.
- [x] [AC8] `tests/test_daemon.py::test_stable_port_serves_tls13_only`.
  Start a real isolated daemon. Then:
  - a plaintext `GET /health` gets no HTTP response;
  - a client capped at `TLSVersion.TLSv1_2` fails the handshake;
  - a pinned TLS 1.3 client gets `/health` 200, and `/status` 401 without a
    bearer.

  Implement it in `_serve_owned`: `ssl_certfile`/`ssl_keyfile`, then
  `config.load()` and `config.ssl.minimum_version = TLSVersion.TLSv1_3`.
- [x] [AC1] `tests/test_client.py::test_relay_refuses_impostors_before_sending_bytes`.
  An in-test impostor, either a plaintext listener or a TLS listener with a
  different certificate, records every byte it receives. Assert that:
  - the relay raises `ClientError` naming a possible impersonation;
  - the impostor saw an accepted connection, and its first byte is `0x16`
    (the positive control);
  - the capture contains no `Authorization` header, no `POST`, and no body.

  Expected: FAIL (the relay currently sends plaintext HTTP).
- [x] [AC1] Switch `HttpRelay._open` to `http.client.HTTPSConnection` with
  `_tls.pinned_context`. After `connect()`, check the fingerprint before
  `request()`. Map `ssl.SSLError` and a fingerprint mismatch to the
  impersonation `ClientError`, and keep the "daemon unavailable" error for
  `OSError`. The token is read once, as it is today. Run the test. Expected:
  PASS.
- [x] [AC1] `test_relay_accepts_the_old_token_after_an_impostor_round`
  (non-disclosure, not revocation): after the impostor test, the relay
  connected to the real daemon succeeds with the unchanged token.
- [x] [AC5] Extend
  `tests/test_client.py::test_client_entrypoint_does_not_import_the_server_stack`
  to also assert that `cryptography` is not in `sys.modules`. Update the
  existing relay tests (`test_relay_preserves_json_session_protocol_and_sse`
  and the tests after it) to serve their fixtures over TLS with a test
  certificate. Then run `uv run pytest tests/test_client.py -q`. Expected:
  PASS, including `test_client_initialize_response_arrives_within_one_second`.
- [x] [P] [AC1] `tests/test_delegate_deadline_and_route.py::test_delegate_refuses_impostor_listener`.
  Run the same two impostors against `_remote_client`. The impostor
  receives a ClientHello and no HTTP bytes, and dispatch fails without
  falling back to local dispatch on an identity failure. Implement
  `https://` plus `verify=_tls.pinned_context(cert)` in `_remote_client`.
- [x] [AC4] `tests/test_daemon.py::test_plaintext_era_token_capture_is_revoked_by_first_tls_start`.
  1. Build a #453-shaped state directory.
  2. Send the synthetic token through a plaintext stand-in for the #453 relay
     to a capturing impostor, and assert that the capture holds the token.
  3. Start the TLS daemon. The captured token gets 401, and the rotated token
     gets 200.
- [x] [AC3] `tests/test_daemon.py::test_direct_https_standin_trusts_only_owner_certificate`.
  An httpx client with `verify=<cert path>` initializes against the daemon
  and refuses both impostors.
  `test_direct_https_node_host` runs `node` with `NODE_EXTRA_CA_CERTS` and a
  `fetch` script. When `node` is absent it fails with "missing evidence"
  (see the preconditions rule above); it does not skip. In both tests the impostor must record
  an accepted connection and a ClientHello (positive control), so a client
  that never dialled the impostor port fails instead of passing.
- [ ] [AC9] Extend `tests/test_credential_never_emitted.py`: across daemon
  startup, relay errors, delegate errors, and impostor refusals, no token and
  no PEM `PRIVATE KEY` block appears in stdout, stderr, captured logs, or
  exception strings.
- [ ] Documentation: `docs/runbooks/daemon-activation.md` and the EN/ES
  `site/src/content/docs/{,es/}guides/daemon-mode.md`. Cover the HTTPS URL,
  `NODE_EXTRA_CA_CERTS` for direct hosts, the removal of `http://`
  registrations, and the security-hold wording, which stays until the
  evidence is complete.
- [ ] Run `make check`. Expected: lint, mypy, and the full test suite green.
  Open PR 1 as a draft, `Refs #456`.

## Implementation — PR 2: lifecycle, status probe, diagnostics

- [ ] [P] [AC6] `test_expired_certificate_is_regenerated_and_token_kept`. Use
  a certificate with `notAfter` in the past and the record present. Startup
  regenerates the key and certificate, the fingerprint changes, and the
  token is unchanged. Implement the check in `prepare_daemon_credentials`.
- [ ] [AC6] `test_over_permissive_key_fails_closed`. On POSIX use mode `0644`;
  on Windows, add an extra ACE with `icacls`. Startup exits non-zero with a
  message that names `rotate-identity` and contains no key material.
- [ ] [AC6] `test_missing_or_corrupt_identity_with_record_fails_closed`. Run
  it with the key deleted, the certificate deleted, and a truncated PEM. With
  the record present, every case fails closed. Without the record it is the
  migration case, already covered by the AC4 test.
- [ ] [AC6] `tests/test_service.py::test_rotate_identity_rotates_key_cert_and_token`.
  Implement `hive service rotate-identity` (a parser in `server.py`, the
  handler in `_identity.py`). It writes a new key, certificate, and token
  atomically, keeps the record, and exits 0. Its output names the new
  fingerprint only.
- [ ] [AC6] `test_relay_repins_after_rotation_and_refuses_old_certificate`.
  The relay re-reads the pin once after a verification failure and
  succeeds. A server that presents the old certificate is refused.
- [ ] [P] [AC7] `tests/test_service.py::test_status_reports_four_states`.
  `hive service status` always runs a pinned `GET /health`, whatever the OS
  or supervisor. It prints the supervisor passthrough (`systemctl` or
  `schtasks`) only where one exists. The final exit code comes from the
  probe. The four states are:

  | Scenario | Output | Exit |
  |---|---|---|
  | Healthy | `healthy` | 0 |
  | Wrong-certificate impostor | `unverified listener` | non-zero |
  | Nothing listening | `down` | non-zero |
  | Impostor whose owner lookup is denied (stubbed) | `unverified listener, owner unknown` | non-zero |

  Implement it in `_service.service_status`.
- [ ] [AC2] `tests/test_daemon_state.py::test_port_conflict_diagnostic_names_three_owners`.
  Use a stubbed owner lookup for three cases: the same UID or SID, a
  different one, and a denied lookup. The diagnostic reads `this account`,
  `another account`, and `owner could not be determined`, and never shows a
  name. Implement `_endpoint.port_holder()`. On Linux it reads the
  `/proc/net/tcp` LISTEN entry's UID. On Windows it uses
  `GetExtendedTcpTable` to get the owning PID, then the process token SID; a
  denied lookup is reported as unknown.
- [ ] [AC2] `tests/test_cross_user.py` (marker `crossuser`). It needs a
  second local account, named by `HIVE_CROSSUSER_ACCOUNT`; without it the
  test fails with "missing evidence". On Linux CI,
  add a job step that runs `sudo useradd hiveimpostor` and starts the
  impostor with `sudo -u hiveimpostor`. Assert:
  - both clients refuse the impostor;
  - the daemon's start reports `another account`;
  - after the impostor exits, the daemon starts and the clients connect.

  Write `docs/runbooks/verify-server-identity-windows.md` for the manual
  Windows run with a second local account, together with check 10 for
  Copilot CLI. The runbook states the expected outputs. A standard user
  cannot open another user's process token, so on Windows a real
  cross-user impostor normally yields `owner could not be determined` rather
  than `another account`. That result is correct, not a failure.
- [ ] Run `make check`. Expected: green. Open PR 2 as a draft, `Refs #456`.

## Closing

- [ ] Every acceptance criterion is covered by at least one test, and `/spec check` returns PASS.
- [ ] Every acceptance criterion has a `features.json` entry with a verification command that is not vacuous.
- [ ] Type checks and lint pass.
- [ ] The diff has no unrelated changes.
- [ ] `verification.md` is filled in with Linux CI evidence.
- [ ] Windows evidence for AC1 to AC9, run by the owner on the baseline host with `docs/runbooks/verify-server-identity-windows.md`, is recorded in the #456 table. Any missing item is listed as missing.
- [ ] An independent adversarial review (`/adversarial-review`), by a reviewer who is not the implementer, before archive.
- [ ] #449 is reconsidered only per the ADR-022 Amendment 1 release gate.
