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
  `test_relay_connection_failure_is_explicit_and_redacts_token` pass. A
  corrupt certificate body is covered since the archive review:
  `test_client_reports_a_corrupt_certificate_without_a_traceback` (startup)
  and `test_relay_fails_closed_when_the_rotated_certificate_is_corrupt`
  (mid-session rotation).
- [ ] AC5: `test_relay_preserves_json_session_protocol_and_sse`,
  `test_relay_adds_current_protocol_metadata_header`, and
  `test_relay_reinitializes_after_daemon_restart_loses_session` pass.
  `test_relay_rediscovers_after_daemon_restart_for_2026_protocol` also
  verifies rediscovery without `initialize` and preserves the protocol header.
  In-flight cancellation is not forwarded until the current HTTP call returns;
  the concurrent dispatch/abort contract is tracked in #454.
- [x] AC6: `tests/test_daemon_state.py` covers atomic token publication,
  rejection of invalid credentials and Windows ACLs, and restart reuse;
  `test_relay_connection_failure_is_explicit_and_redacts_token` checks an
  adapter failure, and `test_daemon.py` checks Hive logs for token leakage.
- [x] AC7: `test_client_stable_endpoint_restart_avoids_duplicate_write`
  passed with a single client configuration across daemon restarts; it now
  lists tools and calls `vault_health` both before and after the restart.

## Test status

- Feature selectors in `features.json`, earlier local Windows runs (superseded
  by the macOS archive run below): f1 13 passed, f2 8 passed, f4
  8 passed, f5 4 passed, f6 1 passed. The
  f3 selector passed 3 tests on a fresh isolated run. The `state` fields
  remain `pending`: the feature harness owns the terminal gate; measured
  results belong in this verification log, not a manually promoted state.
- `uv run ruff check src/ tests/`: passed.
- `uv run mypy --strict src/`: passed, 35 source files.
- `uv run python -m pytest tests/test_endpoint.py tests/test_daemon_state.py
  tests/test_client.py tests/test_delegate_deadline_and_route.py
  tests/test_delegate_review_findings.py -k
  'not initialize_response_arrives_within_one_second' -q`:
  56 passed, 1 deselected; the excluded test passed separately above.
- The broader regression run was interrupted to avoid a long, noisy Windows
  run. Full-suite CI has not yet reported on this branch.
- Reviewer follow-up: `test_relay_times_out_unresponsive_daemon_after_requested_deadline`
  and `test_relay_reports_timeout_while_reading_sse` guard finite read waits
  and explicit errors. In-flight cancellation remains deferred to #454;
  this is an AC5 gap, not a passed transport assertion.
- Manual smoke test: automated restart integration test passed; no separate
  interactive smoke was performed.
- No regressions in the targeted test suite: yes (56 passed); full suite:
  pending CI.

## Archive run, macOS (2026-10-14)

Run on the Mac from `origin/master` (`e6b29f09`) plus the `run_serve` split,
with `uv sync --all-extras`:

- Every `features.json` verification exits 0: f1 13 passed, f2 11 passed,
  f3 3 passed, f4 6 passed and 2 skipped, f5 13 passed, f6 1 passed.
- `uv run mypy src/`: no issues in 38 files. `uv run ruff check src/ tests/`
  and `uv run ruff format --check src/ tests/`: clean.
- Full suite: 1068 passed, 5 skipped. One test was deselected,
  `test_service.py::test_status_reports_four_states[impostor]`: macOS cannot
  name a listener's owner yet. That is hive#478, fixed by PR #495, and it is
  unrelated to this spec. CI on master (ubuntu, windows) is green.
- AC5 stays partial by decision: the relay keeps the session, protocol header
  and SSE contract, but in-flight cancellation is not forwarded until the
  current HTTP call returns. hive#454 (BUG-002) owns it and is open.

## Adversarial review before the archive (2026-10-14)

An independent reviewer pass (Claude, same vendor as most of the
implementation, so informal: hive has no reviewer pool to sign one, see
dotfiles#2439) returned FAIL on one finding. The pass also confirmed that the
`run_serve` split is behaviour-preserving and that every touched production
function is under 40 lines (longest: `_windows_owner_only`, 37).

| # | Finding | Disposition |
|---|---|---|
| 1 | AC4: a `daemon.crt` with a valid header and a corrupt body made `hive client` exit with a raw `ssl.SSLError` traceback, at startup and after a rotation | **Fixed.** `_parse_pin` builds both the TLS context and the fingerprint, and turns `ssl.SSLError`/`ValueError` into a `ClientError` naming `hive service rotate-identity`. Construction and `_refresh_pin` both go through it, and `run_client` builds the relay inside its guarded block. The regression tests are parametrized over a body that is not DER and one that is not base64, because they fail at different layers. They were red before the fix, and the not-base64 rotation case was red after a first fix that guarded only the context. |
| 2 | AC5: cancellation waits behind the in-flight call | **Deferred**, already recorded above: hive#454, open. |
| 3 | SSE `data:` frames are buffered until the stream ends, not streamed as the proposal says | **Ticketed**: hive#499. |
| 4 | AC3's sub-second claim was measured on Windows only | **Accepted.** The macOS run passed the same test; the Windows caveat above stands. |
| 5 | AC2/AC7 are proven by composition: the restart test sets `HIVE_DAEMON_PORT`, so no test spans the uid-derived default port, a real bind and the client | **Accepted gap.** The default-port formula (f1), the bind policy (AC2 tests) and the restart through the client (f6) are each tested. A test spanning all three would bind a per-user port that a real daemon on the same host may hold. |
| 6 | AC6: the Windows ACL tests skip on macOS; the log-leak test does not assert a non-empty log | **Accepted.** The ACL tests ran on the Windows runs above and run in CI's windows leg. |
| 7 | Stale f2/f5 counts | **Fixed** in `features.json` evidence and the note above. |
| 8 | `_owner_only.py::_windows_set_owner_only` is over 40 lines | **Ticketed**: hive#500. The function predates this spec (#463, #485) and is outside its files; 63 production functions break `AGENTS.md`'s 40-line rule, and nothing enforces it. |
| 9 | The ACs say HTTP; the transport is pinned TLS 1.3 since #463 (ADR-022 A1) | **Recorded here.** Every "HTTP" in the ACs is now HTTPS with a pinned certificate. The behaviour each AC asks for is unchanged. |

Run after the fix: full suite 1072 passed and 5 skipped, with the same hive#478
test deselected; mypy and ruff are clean.

## Decisions made during implementation

Brief log of non-obvious trade-offs or course corrections taken during the work. Routine choices belong in commit messages, not here.

- The client reads the credential through stdlib-only `_credential` to avoid
  importing `_daemon`, settings, or subprocess before initialization; Windows
  ACL verification uses Win32 APIs rather than spawning `icacls`.
- `hive delegate` retains its separate pre-submission fallback behavior; the
  legacy `hive.server client` entrypoint propagates client failures. Neither
  behavior changes the client's fail-closed, single-daemon contract.
- The POSIX availability probe matches Uvicorn's `SO_REUSEADDR` bind policy,
  avoiding false occupied-port results on a quick restart. Uvicorn's final
  bind remains authoritative if another process races the probe; it reports
  an address-in-use error and does not fall back to another port.

## Promotion candidates

Answer each line `yes: <path>`, naming the file you promoted, or `no: <reason>`. `dotf spec archive` refuses a line left unanswered, a `no` without a reason, and a `yes` whose file does not exist; a `00_meta/` path is looked up in the vault.

- [x] Lesson for the repo's `docs/lessons/`? no: startup diagnosis is recorded
  in `docs/troubleshooting/stable-client-startup.md`; revisit after AC3.
- [x] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? no: ADR-022
  already owns the endpoint and adapter decisions.
- [x] New pattern candidate for `00_meta/patterns/`? Only if this recurs in >1 project. no: this is a Hive-specific client integration.

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-437-stable-local-mcp-endpoint/` -> `specs/archive/HIVE-437-stable-local-mcp-endpoint/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [ ] Promotions above executed (if any)
