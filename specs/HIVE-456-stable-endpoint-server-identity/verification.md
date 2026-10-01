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
- [ ] AC2 -> PR 2 (`tests/test_cross_user.py` does not exist yet; the command
  currently runs no tests and must not be read as green) — Linux: pending;
  Windows: pending (manual, baseline host)
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
- [ ] AC6 -> items 1-2 (atomic, owner-only) in commit `4a8ec1c` / tests in
  `tests/test_identity.py`; items 3-6 (expiry, over-permissive, missing or
  corrupt, re-pin) are PR 2 — Linux: `5 passed, 38 deselected` (items 1-2
  only); Windows: pending
- [ ] AC7 -> PR 2 (the command currently selects no tests) — Linux: pending;
  Windows: pending
- [ ] AC8 -> commit `40cca9f` / test `test_stable_port_serves_tls13_only`,
  mutation-checked (removing the TLS 1.3 minimum makes it fail) — Linux:
  `1 passed, 24 deselected`; Windows: pending
- [ ] AC9 -> commit `ef9f185` / `tests/test_credential_never_emitted.py`
  (`TestDaemonSecretsStayOutOfOutput`) — Linux: `10 passed`; Windows: pending

## Test status

- Test suite (PR 1): `make check` -> `1015 passed, 4 skipped, 54 deselected`,
  coverage 84%. The 4 skips are pre-existing Windows-only ACL tests.
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
