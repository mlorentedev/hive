---
tags: [spec, verification, templates]
created: "2026-10-31"
---

# Verification - HIVE-540-service-exec-prefix

## Evidence

- [x] AC1 -> `test_systemd_command_line_keeps_each_prefix_element_one_argument`, `test_supervisor_loop_runs_the_prefix_then_hive`, `test_windows_task_and_startup_fallback_carry_the_same_prefixed_loop`, `test_install_records_the_prefix_and_runs_it_in_the_unit`, `test_install_launchd_runs_the_prefix_inside_the_env_wrapper`
- [x] AC2 -> `test_install_records_the_prefix_and_runs_it_in_the_unit` (mode 0600), `test_a_reinstall_without_the_flag_keeps_the_recorded_prefix`, `test_clearing_the_prefix_restores_the_plain_unit`, `test_a_launcher_that_moved_is_found_again_by_name`, `test_cli_exec_prefix_takes_everything_after_the_separator`
- [x] AC3 -> `test_a_launcher_that_cannot_be_found_is_refused_before_anything_is_written`, `test_windows_refuses_a_prefix_its_loop_cannot_quote`, `test_a_malformed_record_is_refused_and_named`, `test_cli_exec_prefix_misuse_is_refused` (4 cases)
- [x] AC4 -> `test_with_no_prefix_every_definition_is_unchanged`, plus every pre-existing renderer and install test passing unmodified
- [x] AC5 -> `test_status_names_the_recorded_prefix`

## Test status

- Test suite: `make check` -> ruff clean, mypy strict clean (38 files), 1154 passed, 5 skipped.
- The new tests ran red first (`AttributeError: ... has no attribute 'service_config_path'`).
- Not exercised on a real supervisor in this PR: the consumer (dotfiles) switches a host to a prefix in its follow-up, and verifies each one live there. Windows termination through a prefix is measured before any Windows host switches (proposal, Risks).
- No regressions in existing test suite: yes. One existing CLI test fake now accepts the new keyword arguments.

## Decisions made during implementation

- The CLI takes `--exec-prefix --` and then the rest of the line verbatim, instead of letting argparse parse it, because the prefix normally ends in its own `--`. The separator is required, so `--exec-prefix dotf --` is refused rather than guessed.
- The record keeps the launcher as given and resolves it at every install. A record that stored the resolved absolute path would go stale when the launcher moves, so the same reinstall that keeps the prefix would also start writing a definition that never runs.
- The unprefixed Windows loop is left byte-identical, including its single-string module fallback. Only the prefixed path quotes element by element.

## Promotion candidates

- [x] Lesson for the repo's `docs/lessons/`? yes: docs/lessons/lesson-114-a-consumer-wraps-the-daemon-by-declaring-not-by-patching.md
- [x] ADR-worthy decision for the repo's `docs/adr/adr-XXX.md`? no: an additive install option inside ADR-011/ADR-015's supervisor contract, which it keeps unchanged
- [x] New pattern candidate for `00_meta/patterns/`? no: one occurrence; the lesson records the rule

## Archive checklist

- [ ] `proposal.md` frontmatter set to `status: archived`
- [ ] Folder moved: `specs/HIVE-540-service-exec-prefix/` -> `specs/archive/HIVE-540-service-exec-prefix/`
- [ ] Bitácora board ticket for this spec moved to Done / closed with PR link (ADR-018)
- [ ] Promotions above executed (if any)
