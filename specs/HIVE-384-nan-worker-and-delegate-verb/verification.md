---
tags: [spec, verification, templates]
created: "2026-08-23"
---

# Verification - HIVE-384-nan-worker-and-delegate-verb

## Evidence

Map every acceptance criterion from `proposal.md` to concrete proof (commit hash, test name, or observed behavior).

> **Status as of 2026-08-23, after PR 2.** AC1–AC4 and AC7 carry evidence below. AC5 and AC6 were
> implemented by PR 1 (`#390`, released as 4.0.0) and their rows now name the tests that actually
> assert them — the commands recorded for them originally selected **zero** tests, which is why they
> are restated here rather than trusted.

**A finding about this file's own machinery, recorded because it is the point.** Four of the eight
`features.json` verification commands — AC5, AC6, AC7 and the smoke row — matched nothing when run.
A recorded proof that never executes is indistinguishable from one that passes, and 4.0.0 shipped
with those four criteria marked complete on that basis. AC7 turned out to have no test *at all*, not
merely a broken selector; writing it in PR 2 found two real leaks. Every command in `features.json`
was re-run under `--collect-only` and confirmed to select a non-zero number of tests.

- [x] AC1 (hive delegate honours the wire contract) -> `tests/test_delegate_verb.py::TestWireContract`,
      `::TestRequiredArguments` (10 tests). One JSON object on stdout, every log line on stderr,
      `--model` / `--timeout` / `--prompt` required, a non-positive timeout refused.
- [x] AC2 (exit codes separate pool-unavailable from task-failed) ->
      `tests/test_delegate_verb.py::TestExitCodesSeparateTheFailureClasses` +
      `tests/test_pool_classification.py` (15 tests). **This closed a live defect**: `clients.py`
      raised `RuntimeError` for every non-2xx, so a 429 classified as *task failed* and a dispatcher
      would have stopped its chain exactly where it should have advanced. 429/401/403 now raise
      `PoolUnavailableError`; 4xx-about-the-request and all 5xx stay task failures.
- [x] AC3 (timeout kills the worker and returns without waiting) ->
      `tests/test_delegate_deadline_and_route.py::TestTheDeadlineIsTheOneAskedFor` (4 tests).
      Asserted in both directions — a longer `--timeout` raises the 60s ambient ceiling, a shorter
      one cuts the call short — plus a wall-clock assertion that the return is inside the deadline
      rather than deadline + the 2s grace. **Mutation-checked**: pinning `deadline_s` to
      `ctx.tool_timeout` kills 2 of the 4.
- [x] AC4 (routes through the daemon, degrades honestly without one) ->
      `tests/test_delegate_deadline_and_route.py::TestDegradedIsReportedInBothDirections` (4 tests).
      Both states asserted, per the criterion's own wording, plus the two ways a daemon can be
      present and unusable: stale state files with nothing listening, and TCP accepted then session
      failed.
- [ ] AC5 (the worker reaches its configured endpoint) — **half proved, and the box says so.**
      `tests/test_config.py::TestWorkerSettings` (3 passed, PR 1) covers the settings and fallback.
      `tests/test_smoke.py::TestWorkerDispatch -m smoke` covers the live inference and **has never
      run**: 2 collected, 2 skipped, exit 0, because this machine configures no worker endpoint.
      Previously carried as `[x]` with that caveat in the same line — the text was honest and the
      checkbox was not, which is the half a reader scanning for green picks up. See Test status.
- [x] AC6 (the retired providers are gone from every surface) ->
      `tests/test_config.py::TestRetiredProviderSettings` + `tests/test_provider_neutrality.py`
      (23 tests). Widened by `#392`: the criterion said "gone from every surface" and the shipped
      code still named one provider in a hardcoded `provider_name` literal and in an env alias.
- [x] AC7 (the credential never appears in output) -> `tests/test_credential_never_emitted.py`
      (6 tests). **Written in PR 2; it did not exist.** Two real leaks found and fixed:
      `repr(HiveSettings())` rendered `worker_api_key='<the key>'` verbatim (a traceback and a debug
      log print that unbidden), and `_error_detail` relayed a provider's 401 body without redaction,
      which matters because some providers echo the `Authorization` header there. Both
      **mutation-checked**: removing either fix turns the tests red.

## Test status

- **Pre-change baseline, 2026-08-23** — `make check` on this branch before any source change:
  `897 passed, 2 skipped, 63 deselected in 241.15s, 85% coverage, exit 0`.
  Recorded so a later failure is attributable to the change rather than to the environment. The 63
  deselected are the `@pytest.mark.smoke` tests, excluded by `-m 'not smoke'`.
  *Caveat worth keeping:* the first attempt failed with 7 mypy errors about missing `types-psutil`
  stubs. That was a fresh worktree whose `.venv` was unsynced, **not** a repo defect —
  `types-psutil>=5.9.0` is declared in `pyproject.toml` and CI runs `uv run mypy src/`.
- **Post-change, 2026-08-23** — `ruff check`, `ruff format --check`, `mypy --strict` (32 modules)
  all clean; `pytest tests/` → `934 passed, 2 skipped, 55 deselected in 180.70s, exit 0`.
  Against the 895-passing baseline that is **+39 tests and zero regressions**. The 55 deselected are
  the `@pytest.mark.smoke` set, excluded by `-m 'not smoke'`.
- **Every `features.json` command re-run, 2026-08-25 on `66dbf17`** (#402), recording the **passed**
  count per row rather than a tick: f1 10, f2 15, f3 4, f4 4, f5 3, **f6 0** (2 collected, 2 skipped,
  exit 0), f7 23, f8 6. Passed and not collected on purpose — f6 collects two tests and proves
  nothing, so a collection count would have scored it as evidence.
  *This file's own machinery, recorded because it nearly failed silently:* at the 4.0.0 release
  commit `dff8af4` **all eight** of the scaffolded commands selected zero tests. They did not sneak
  through — pytest refused every one, six with exit `4` (unresolvable path or nodeid) and two with
  exit `5` (`-k` deselected everything) — and at that commit every row read `pending` and every box
  was unchecked, so nothing was ever falsely closed. The near miss is that `4092bf4` rewrote the
  commands to working ones **and** checked all seven boxes in one commit: nothing re-read them
  independently of the change that wrote them. See `docs/lessons/lesson-094`.
- **Mutation checks**, because passing tests are not evidence that a test would fail:
  - pinning `deadline_s` to `ctx.tool_timeout` (removing the AC3 override) → 2 of 4 AC3 tests fail.
  - removing the `_error_detail` redaction → the echoed-key test fails.
  - removing `repr=False` from `worker_api_key` → 2 AC7 tests fail.
  Each was reverted immediately and the suite re-confirmed green.
- **Manual smoke against a live endpoint: PENDING.** Required for AC5's inference row. It cannot run
  in `make check` because it needs a credential, and this machine has no worker endpoint configured
  (`HIVE_WORKER_BASE_URL` unset in `environment.d` and in the systemd unit). Stated as an open item
  rather than quietly omitted: **`dotfiles` CLI-042 AC6 cannot be re-checked until this runs**, and
  it needs the credential-delivery work that CLI-042's own PR E carries.
- No regressions in existing test suite: PENDING (compare against the baseline above)

## Decisions made during implementation

Brief log of non-obvious trade-offs or course corrections taken during the work. Routine choices belong in commit messages, not here.

-
-

## Promotion candidates

Before archiving, flag what (if anything) should be promoted to the vault. If all three are "no", archive in repo is the only persistence.

- [ ] Lesson for the repo's `docs/lessons/`? <yes / no - one line of what>
- [ ] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? <yes / no - one line of what>
- [ ] New pattern candidate for `00_meta/patterns/`? Only if this recurs in >1 project. <yes / no - one line>

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-384-nan-worker-and-delegate-verb/` -> `specs/archive/HIVE-384-nan-worker-and-delegate-verb/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [ ] Promotions above executed (if any)
