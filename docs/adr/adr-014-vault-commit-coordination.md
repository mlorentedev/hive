---
id: adr-014-vault-commit-coordination
type: adr
status: active
created: "2026-06-02"
---

# ADR-014: Vault Commit Coordination — Single Deliberate Committer

## Status

Accepted — 2026-06-02. Resolves [#174](https://github.com/mlorentedev/hive/issues/174). Extends ADR-010 (external-committer coexistence).

## Context

The knowledge vault is committed **directly to `master`** (no PR flow). Two writers were acting on `master` concurrently and uncoordinated:

1. **Hive** — auto-commits a semantic patch on every `vault_write` / `vault_patch` (`vault: patch …`, `vault: capture_lesson …`). Hive **commits but does not push** (no `git push` / `pull` anywhere in `src/`).
2. **obsidian-git plugin** — auto-commits on a timer (`autoSaveInterval`, message `vault backup: …`) and reconciles with `pullBeforePush` using **merge**, then pushes.

Observed 3× in one session: semantic commits swept into `vault backup:` blobs, merge commits, and rejected (non-fast-forward) pushes. Root cause: two concurrent committers to one branch with no coordination, made worse by `autoCommitOnlyStaged: false` (the timer sweeps in-progress, unstaged work — including an agent's half-written change) and `pullBeforePush: merge` (merge commits + push contention).

Options weighed (issue #174):

- **A — event-driven, single deliberate committer (chosen).** Eliminate the second writer rather than orchestrate two: turn the obsidian-git timer off and let Hive be the single committer; the plugin (or the operator) pushes deliberately with `rebase`, not on a merge timer. *Boring tech: remove the race, don't manage it.*
- **D — lock-file coordination.** Teach obsidian-git to respect Hive's ADR-012 cooperative filelock. More robust but requires wrapping a third-party plugin whose timer we do not control; the failure mode (a swept unstaged change) is a *config* problem, not a locking one.

A key constraint shaped the decision: **Hive does not push.** So Hive cannot, by itself, produce a merge commit or a rejected push — those come from the external committer's timer + merge reconciliation. The fix is therefore primarily the external committer's **configuration**, which Hive cannot enforce, only surface.

## Decision

**Adopt A: Hive is the single deliberate committer; the obsidian-git auto-commit timer must be OFF, with `rebase` reconciliation, as a precondition.**

Split by who owns what:

- **Vault configuration (operator-applied, no code).** `autoSaveInterval: 0` (kill the timer → commits become event-driven, Hive-only) and `syncMethod: merge → rebase` (no reconciliation merge commits). This removes the second writer and the merge noise; it is the behavioural fix for the issue's acceptance criteria (no merge commits, no rejected pushes).
- **Hive-side (this change).** Hive cannot enforce the vault config, so it **makes the racy config visible**: `vault_health` emits a `warning` in its `## external_committer` block whenever `detect_obsidian_git` reports a non-zero `commitInterval` (the block only renders in that case), naming the concrete fix (`autoSaveInterval=0` + `syncMethod=rebase`). A racy config is surfaced, never silently tolerated.

Hive deliberately stays **commit-only** (no push): pushing remains the external committer's / operator's job. Introducing a Hive pusher (pull-rebase-push) was rejected — it adds network I/O and failure handling to the write path to solve a problem the vault config already removes.

## Consequences

- **Positive.** No merge commits and no rejected pushes once the timer is off + rebase is set (the config the warning prescribes). The single-committer invariant is simple and matches the daemon model's single-owner direction (ADR-011). `vault_health` now flags the misconfiguration proactively, so drift back into the racy state is caught.
- **Negative.** The behavioural guarantee depends on operator-applied vault config that Hive cannot enforce — Hive can only warn. If the operator re-enables the timer, the race returns (the warning will fire again).
- **Neutral.** No change to Hive's commit path; the only code change is the additive `vault_health` warning line.

## References

- Issue: [#174](https://github.com/mlorentedev/hive/issues/174)
- ADR-010: external-committer coexistence (the `commit=False` / detect-and-defer machinery this builds on)
- ADR-011: Phase C daemon model (single-owner direction)
- `detect_obsidian_git` (`src/hive/_helpers.py`) → `## external_committer` block (`src/hive/_vault_health.py`)
