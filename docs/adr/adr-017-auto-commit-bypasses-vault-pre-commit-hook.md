---
id: adr-017-auto-commit-bypasses-vault-pre-commit-hook
type: adr
status: accepted
created: "2026-06-19"
owner: manu
tags: [architecture, write-path, git, security, secret-scanning, performance]
---

# ADR-017: Hive auto-commits bypass the vault pre-commit hook; secret scanning moves push-side

## Status

**Accepted (2026-06-19).** Implements [#255](https://github.com/mlorentedev/hive/issues/255); pairs with the vault-side change [mlorentedev/knowledge#121](https://github.com/mlorentedev/knowledge/issues/121). Supersedes the implicit prior behaviour where `_git_commit` ran the target vault's `pre-commit` hooks on every machine commit.

## Context

Every vault write (`vault_write`, `vault_patch`, `capture_lesson`, and the deferred-flush `vault_commit`) auto-commits to git via `_git_commit` / `_git_commit_all` in `src/hive/_helpers.py`. Those calls ran `git commit` **without `--no-verify`**, so each machine commit fired the target vault's `pre-commit` hook chain.

On the maintainer's vault that chain is `gitleaks` (a full-diff secret scan) plus a `language: python` local hook (`forbid-hardcoded-vault-paths`, ADR-023 of the vault). Measured cost on Windows, warm, single process:

- `git commit` with hooks, 1 staged file: **~8.9 s**
- `git commit --no-verify`: **~0.5 s** (≈17× faster)

Under cold start (the `language: python` hook builds a venv; gitleaks fetches on first use) and **concurrency** — several machine committers (the Obsidian Git plugin's frequent "vault backup" commits, the `hermes-nan` agent, and Hive) serialise under the vault git filelock — this reaches Hive's **60 s tool deadline**. Production logs show **24 `deadline_exceeded` kills and 6 lock evictions**: `vault_write timed out after 60.0s → killed subprocess → lock_eviction`. The practical effect is that callers avoid `vault_write` / `capture_lesson` because writes hang 60–180 s — i.e. "agents use Hive less."

This was initially blamed on the Phase C daemon (ADR-011). That is refuted: the daemon never ran (install fails `schtasks … Access is denied`; ADR-015 documents the Windows daemon as broken-as-shipped), and the first commit timeout (2026-05-19) **predates** the daemon (2026-05-31). The bottleneck is the pre-commit hook, not the transport model.

A pre-commit hook is a **human-commit** safeguard. Hive is an automated committer; making its machine commits hostage to whatever (arbitrarily slow) hook a given vault installs is the wrong coupling — and it differs per deployment, so the behaviour is non-portable.

## Decision

1. Hive's auto-commits pass **`--no-verify`**, gated by a new setting `HiveSettings.git_commit_no_verify` (env `HIVE_GIT_COMMIT_NO_VERIFY`), **default `True`**. The two write-path commit callsites (`_git_commit`, `_git_commit_all`) build their argv through `_commit_args`, which inserts `--no-verify` when enabled.
2. Secret scanning **moves push-side**, not away. On the vault, `gitleaks` and the path-guard hook relocate from `pre-commit` to `pre-push` (`stages: [pre-push]`), and the existing CI workflow `vault-guard.yml` already runs `gitleaks` on every push/PR plus a weekly full-history sweep. Secrets are therefore still caught before they **leave the machine**.
3. Human commits are unaffected — the `pre-commit` stage still exists for the cheap hygiene hooks, and developers committing by hand keep full coverage.

## Consequences

**Positive**
- Vault writes drop from ~9–60 s to the git baseline (~0.5 s); the 60 s deadline-kill / lock-eviction failure mode disappears.
- Hive no longer depends on any particular vault's hook configuration — portable across vaults.
- One lever fixes the write-path latency for *every* machine committer once the vault hook is relocated (Obsidian Git, `hermes-nan`, Hive).

**Negative / accepted trade-offs**
- A secret can briefly land in **local** git history before a push. It is still caught at `pre-push` (once `pre-commit install --hook-type pre-push` is run) and by CI before reaching the remote — nothing secret leaves the machine uncaught. This is the deliberate trade-off.
- The `forbid-hardcoded-vault-paths` guard now runs only at push time; `vault-guard.yml` does not yet replicate it, so its local enforcement depends on the pre-push hook being installed. A server-side backstop for it is a follow-up.

## Alternatives considered

- **In-Hive pre-write secret scan** (Hive runs gitleaks on its own content before writing): keeps the net adjacent to the write but adds gitleaks as an operational dependency of Hive and re-introduces latency. Rejected in favour of push-side coverage (option b).
- **Make the vault hooks fast** (pre-warm venvs, drop the python hook): keeps per-commit scanning but is brittle on a cold box and does not remove the coupling.
- **Async / outbox commit** (decouple the commit from the tool response; scaffolding exists at `_helpers.py`): complementary, not a substitute — deferred as a separate change (R3).
