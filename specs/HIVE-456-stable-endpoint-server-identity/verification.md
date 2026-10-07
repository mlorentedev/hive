---
tags: [spec, verification, templates]
created: "2026-09-30"
---

# Verification - HIVE-456-stable-endpoint-server-identity

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior).

Linux evidence below was produced on 2026-09-30 on the owner's Linux host from
branch `feat/stable-endpoint-server-identity`, by running each `features.json`
command. Windows evidence and the cross-user check (AC2) are recorded on #456
when they run; nothing here stands in for them.

- [ ] AC1 -> commits `40cca9f` (relay), `00c13c9` (delegate), `6340c72`
  (non-disclosure) / tests `test_relay_refuses_impostors_before_sending_bytes`,
  `test_delegate_refuses_impostor_listener`,
  `test_delegate_probe_verifies_the_owner_unlike_an_impostor`,
  `test_relay_accepts_the_old_token_after_an_impostor_round` — Linux:
  `6 passed, 47 deselected`; Windows: pending
- [ ] AC2 -> commits `32c3e18` (diagnostic), `312b790` (cross-user test + CI
  job `cross_user_identity`) / tests
  `test_port_conflict_diagnostic_names_three_owners`,
  `test_cross_user_impostor_is_refused_and_named`,
  `test_owner_daemon_serves_once_the_impostor_is_gone` — Linux: diagnostic
  `3 passed`; the cross-user tests fail locally with "missing evidence" (no
  second account on the owner's host) and run only in the CI job, whose
  result is still pending. Their mechanics were dry-run as the same account
  (2 passed, holder reported as `this account`). Windows: pending (manual,
  `docs/runbooks/verify-server-identity-windows.md`)
- [ ] AC3 -> commit `6dcafb7` / tests
  `test_direct_https_standin_trusts_only_owner_certificate`,
  `test_direct_https_node_host` (Node v24.16.0, `NODE_EXTRA_CA_CERTS`) — Linux:
  `2 passed, 23 deselected`; Windows: pending (check 10, real host)
- [ ] AC4 -> commits `3807798`, `6340c72` / tests
  `test_first_tls_start_rotates_a_pre_tls_token_once`,
  `test_interrupted_first_tls_start_rotates_the_pre_tls_token_again`,
  `test_plaintext_era_token_capture_is_revoked_by_first_tls_start` — Linux:
  `3 passed, 27 deselected`; Windows: pending
- [ ] AC5 -> commits `4a8ec1c`, `3807798`, `3668501` / tests
  `test_load_or_create_identity_reuses_existing_material`,
  `test_run_serve_uses_stable_port_and_token_across_restarts`,
  `test_client_entrypoint_does_not_import_the_server_stack`,
  `test_client_initialize_response_arrives_within_one_second` — Linux:
  `5 passed, 26 deselected`; Windows: pending
- [ ] AC6 -> items 1-2 in commit `4a8ec1c`; items 3-5 in `0328ddc`
  (expiry, over-permissive, missing or corrupt) and `c08f4aa`
  (`rotate-identity`); item 6 in `811d77d` (re-pin) / tests in
  `tests/test_identity.py`, `tests/test_service.py -k rotate`,
  `test_relay_repins_after_rotation_and_refuses_old_certificate` — Linux:
  `14 passed, 45 deselected`; Windows: pending
- [ ] AC7 -> commit `d141038` / `test_status_reports_four_states` (healthy,
  wrong-certificate impostor, nothing listening, impostor with a denied owner
  lookup) — Linux: `4 passed, 27 deselected`; Windows: pending
- [ ] AC8 -> commit `40cca9f` / test `test_stable_port_serves_tls13_only`,
  mutation-checked (removing the TLS 1.3 minimum makes it fail) — Linux:
  `1 passed, 24 deselected`; Windows: pending
- [ ] AC9 -> commit `ef9f185` / `tests/test_credential_never_emitted.py`
  (`TestDaemonSecretsStayOutOfOutput`) — Linux: `10 passed`; Windows: pending

## Test status

- Test suite (PR 1): `make check` -> `1015 passed, 4 skipped, 54 deselected`,
  coverage 84%. The 4 skips are pre-existing Windows-only ACL tests.
- Test suite (PR 2): `make check` -> `1046 passed, 4 skipped, 56 deselected`,
  coverage 84%.
- Manual smoke test: `curl --cacert daemon.crt https://127.0.0.1:<port>/`
  returns 200 against a server holding the identity, and fails (exit 60)
  without `--cacert`; this backs the runbook commands.
- No regressions in existing test suite: yes

## Decisions made during implementation

- `identity.state` is written only after the pre-TLS token rotation. Writing
  it first would let a crash between the two leave the plaintext-era token
  live behind a record that says the migration happened.
- Record present but token missing: a fresh token is created. Deletion is not
  exposure, so this matches a first install rather than failing closed.
- `hive delegate` probes the stable port with the pinned context before
  opening a session. A listener that fails the proof is reported as a
  possible impersonation (`task_failed`) and the task is not run locally. This
  also applies to the owner's daemon if it closes the connection mid-shutdown
  during the probe; the relay reports that case the same way.
- The relay requires the certificate file to be owner-only, because it is the
  trust anchor.
- The TLS 1.2 refusal test offers every cipher. uvicorn's default cipher
  string shares none with Python's default client, which let the test pass
  without the TLS 1.3 minimum (lesson 102).
- An expired certificate is regenerated only once it has expired, with no
  early-renewal margin, as AC6 states. A daemon left running past expiry
  serves a certificate every client refuses; the relay, the delegate and
  `status` word that case as "expired certificate; restart the daemon"
  instead of "possible impersonation" (`0bd70c4`). The wording comes from
  OpenSSL's verify code 10, which shows the pinned certificate was presented,
  not that the listener holds its key, so the clients still refuse and send
  nothing.
- `rotate_identity` lives in `_daemon.py`, not `_identity.py` as `tasks.md`
  planned: it needs the singleton lock and the token writer, which live
  there. It refuses while the daemon runs (the daemon would keep serving the
  old identity from memory) and rotates the token before the identity, so an
  interrupted run leaves a start that still fails closed.
- The relay re-reads the token and certificate before every connection, not
  once after a failed proof as first built (see the `d1b0b7f` finding below).
  It repins only if either changed on disk, and fails closed if it cannot read
  them.
- `status` prints `unverified listener (held by this account|another account);
  possible impersonation` when the holder is known, and `unverified listener,
  owner unknown` when it is not. A verified daemon answering `/health` with
  anything but 200 is reported as `down`; the daemon always answers 200, so
  that only happens mid-shutdown. A malformed `HIVE_DAEMON_PORT` is a one-line
  error, exit 1.
- An independent adversarial review (a separate agent, before the PRs were
  opened) found no path that sends the bearer or a request to an unproven
  listener. Its findings and their disposition:
  - A relay started before `rotate-identity` kept trusting the rotated-away
    key until a proof failed; an attacker holding that key could read
    requests. Fixed in `d1b0b7f`: the relay re-reads its pin before every
    connection and fails closed if it cannot.
  - A listener that accepted TCP and then reset or stalled was `absent`
    (delegate ran locally) and status said `down`. Fixed in `3842aa4`: one
    shared proof, `unverified` after any accepted connection.
  - Mutants that survived (owner-only pin, key/certificate match, rotation
    order, token errors without a hint) are now guarded in `0328ddc`. The
    fingerprint comparison after the pinned handshake stays untested
    defence in depth: with a single `CA:FALSE` anchor no other certificate
    can verify, so no test can make it the deciding check.
  - The `/proc` holder lookup ignored the local address. Fixed in
    `f9e1eaf`, which also stops a test stub leaking through
    `hive._identity`'s import-time binding.
  - `NODE_EXTRA_CA_CERTS` adds to Node's roots instead of replacing them;
    documented in the activation runbook.
  - Open, ticketed as #465: no early renewal (a daemon started near expiry
    goes dark until restarted) and a one-minute `notBefore` backdate that a
    clock step can trip. Both fail closed with an explicit message.
  - Declined: AC9 coverage of `status` output. Status reads only the
    certificate, and the rotation output is already asserted free of the
    token and key.
- The AC2 `features.json` command now passes `-m 'crossuser or not
  crossuser'`. Without it the default `addopts` deselects the cross-user
  tests and the command would report only the diagnostic tests as green.

## Promotion candidates

Answer each line `yes: <path>`, naming the file you promoted, or `no: <reason>`. `dotf spec archive` refuses a line left unanswered, a `no` without a reason, and a `yes` whose file does not exist; a `00_meta/` path is looked up in the vault.

- [ ] Lesson for the repo's `docs/lessons/`? <yes: path / no: reason>
- [ ] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? <yes: path / no: reason>
- [ ] New pattern candidate for `00_meta/patterns/`? Only if this recurs in >1 project. <yes: path / no: reason>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-456-stable-endpoint-server-identity/` -> `specs/archive/HIVE-456-stable-endpoint-server-identity/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [ ] Promotions above executed (if any)
