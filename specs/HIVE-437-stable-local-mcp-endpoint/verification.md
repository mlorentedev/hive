---
tags: [spec, verification, templates]
created: "2026-09-28"
---

# Verification - HIVE-437-stable-local-mcp-endpoint

## Evidence

- [x] AC1: `tests/test_endpoint.py` (13 passed) covers the v1 vectors, private
  range, overrides, and invalid input.
- [x] AC2: `test_run_serve_uses_stable_port_and_token_across_restarts` and
  `test_run_serve_fails_closed_when_stable_port_is_occupied` pass.
- [x] AC3: `uv run python -m pytest tests/test_client.py -k
  'entrypoint or console_scripts or initialize_response' -q` passed
  (3 passed, 8 deselected) on Windows; the installed-executable test launches
  five cold starts and asserts median <1 s and maximum <4.1 s. The host was
  around 84% busy at this run. Earlier runs failed intermittently under
  similar load; this result verifies the measured run, not guaranteed
  repeatability on every loaded host. The import-graph test also passed.
- [x] AC4: `test_client_without_credential_fails_without_starting_fallback`,
  `test_client_rejects_corrupt_or_permission_invalid_credential`, and
  `test_relay_connection_failure_is_explicit_and_redacts_token` pass.
- [x] AC5: `test_relay_preserves_json_session_protocol_and_sse`,
  `test_relay_adds_current_protocol_metadata_header`, and
  `test_relay_reinitializes_after_daemon_restart_loses_session` pass.
- [x] AC6: `tests/test_daemon_state.py` covers atomic token publication,
  rejection of invalid credentials and Windows ACLs, and restart reuse;
  `test_relay_connection_failure_is_explicit_and_redacts_token` checks an
  adapter failure, and `test_daemon.py` checks Hive logs for token leakage.
- [x] AC7: `test_client_stable_endpoint_restart_avoids_duplicate_write`
  passed with a single client configuration across daemon restarts.

## Test status

- Feature selectors in `features.json`: f1 13 passed, f2 8 passed, f4
  8 passed, f5 4 passed, f6 1 passed in earlier local Windows runs. The
  f3 selector passed 3 tests on a fresh isolated run.
- `uv run ruff check src/ tests/`: passed.
- `uv run mypy --strict src/`: passed, 35 source files.
- `uv run python -m pytest tests/test_endpoint.py tests/test_daemon_state.py
  tests/test_client.py tests/test_delegate_deadline_and_route.py
  tests/test_delegate_review_findings.py -k
  'not initialize_response_arrives_within_one_second' -q`:
  56 passed, 1 deselected; the excluded test passed separately above.
- The broader regression run was interrupted to avoid a long, noisy Windows
  run. Full-suite CI has not yet reported on this branch.
- Manual smoke test: automated restart integration test passed; no separate
  interactive smoke was performed.
- No regressions in the targeted test suite: yes (56 passed); full suite:
  pending CI.

## Decisions made during implementation

Brief log of non-obvious trade-offs or course corrections taken during the work. Routine choices belong in commit messages, not here.

- The client reads the credential through stdlib-only `_credential` to avoid
  importing `_daemon`, settings, or subprocess before initialization; Windows
  ACL verification uses Win32 APIs rather than spawning `icacls`.
- `hive delegate` retains its separate pre-submission fallback behavior; the
  legacy `hive.server client` entrypoint propagates client failures. Neither
  behavior changes the client's fail-closed, single-daemon contract.

## Promotion candidates

Answer each line `yes: <path>`, naming the file you promoted, or `no: <reason>`. `dotf spec archive` refuses a line left unanswered, a `no` without a reason, and a `yes` whose file does not exist; a `00_meta/` path is looked up in the vault.

- [ ] Lesson for the repo's `docs/lessons/`? no: startup diagnosis is recorded
  in `docs/troubleshooting/stable-client-startup.md`; revisit after AC3.
- [ ] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? no: ADR-022
  already owns the endpoint and adapter decisions.
- [ ] New pattern candidate for `00_meta/patterns/`? Only if this recurs in >1 project. no: this is a Hive-specific client integration.

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-437-stable-local-mcp-endpoint/` -> `specs/archive/HIVE-437-stable-local-mcp-endpoint/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [ ] Promotions above executed (if any)
