---
id: "ADR-020-client-upgrade-policy"
type: adr
status: accepted
owner: manu
date: "2026-08-09"
issue: "hive#292"   # repo#NNN — GitHub issue / Project item that triggered this decision
tags: [architecture, decision, packaging, upgrade, mcp]
created: "2026-08-09"
---

# ADR-020: Client Upgrade Policy — Notify and Offer, Never Auto-Apply

## Status

Accepted (2026-08-09). Scopes what [#292](https://github.com/mlorentedev/hive/issues/292)'s
`self-upgrade` machinery is allowed to do unattended, and states the gate that
[ADR-015](adr-015-windows-daemon-supervision-upgrade.md)'s A3 mechanism does **not** by itself
lift. Cross-platform, unlike [ADR-019](adr-019-launcher-ownership.md).

## Date

2026-08-09

## Context

Nothing upgrades a hive client today unless a human types a command. The question that prompted
this decision was the obvious one — *why isn't the update automatic; won't users just never
update?* — and it deserves an answer that is written down, because the machinery to make it
automatic already exists and is one small step from firing.

The state of the world, read from the code rather than from the tickets:

- **POSIX** installs via `uv tool`, and upgrade is `uv tool upgrade hive-vault`, run by hand.
  ADR-019 deliberately keeps it that way: `~/.local/bin` is already on `PATH` and there is no
  in-use-file lock, so none of the Windows machinery is warranted.
- **Windows** owns its layout (A3): `self_upgrade` builds `versions/<v>` and flips a `current`
  junction. `hive self-upgrade [version]` is wired end to end at `server.py:759`. What is still
  missing is the `PATH` launcher — `specs/HIVE-328-runtime-launcher/` PR2 — so after a
  successful upgrade a human typing `hive` still reaches whatever uv left behind.
- **The running version already reaches the client in band.** `_vault_health` always emits
  `- version: <installed>` inside its `## server` identity block. What is absent is any
  comparison against what has been *published*.

The decisive fact is that **the automatic path is already half-built, and its remaining half
lives in another repo.** `_runtime.latest_version()` resolves PyPI's newest release so that a
bare `hive self-upgrade` needs no version argument, and its docstring names the consumer it was
written for: *"the unattended dotfiles trigger"*. So this ADR is not choosing whether to build
unattended upgrade. It is deciding whether to let the piece that already exists be pointed at a
timer.

Three positions were on the table:

- **(a) Notify only** — tell the user their client is behind; change nothing.
- **(b) Offer one explicit command** — `hive self-upgrade`, run deliberately.
- **(c) Auto-apply** — a background trigger keeps clients on the latest release.

## Decision

**Hive notifies and offers. It never applies an upgrade the user did not ask for.** Concretely:

1. **(a) is adopted.** Version drift is surfaced to the client in band, building on the
   `## server` block `vault_health` already emits. **Constraint, load-bearing:** whatever carries
   the notification must not put a PyPI round-trip on the health path. `vault_health` is
   contracted to be cheap enough to call often, and a network dependency there is the same defect
   shape [ADR-018](adr-018-asynchronous-commit-queue.md) has just finished removing from the
   write path.
2. **(b) is adopted and stays the only application path.** `hive self-upgrade [version]` is the
   one command that changes what is installed. HIVE-328 PR2's launcher is what makes its result
   reachable to a human on Windows; until then the upgrade succeeds but the shell cannot see it.
3. **(c) is gated, not rejected.** The gate is **tool-contract versioning** — see below. The gate
   binds the **unattended dotfiles trigger** as explicitly as it binds anything inside this repo:
   `latest_version()` may keep resolving PyPI's newest release for an operator who asks for it,
   but no timer, service, or hook may invoke `hive self-upgrade` on the user's behalf while the
   gate is closed. Without that clause the policy would be fiction, since the trigger is the
   precise mechanism by which (c) would arrive.

### Why (c) is gated

Three grounds, the first decisive.

1. **A breaking release reaches a live session with no negotiation.** Today, 2026-08-09, 3.0.0
   made `vault_delete(commit=False)` a hard error where it had been a success. An MCP client
   caches the tool list at connect time; swapping the server underneath a running agent changes
   behaviour for a caller that has already planned around the old contract, and there is no
   version handshake with which a client could decline. An upgrade that is safe for the *files*
   is not thereby safe for the *conversation*. Every breaking release hive has cut would have
   been delivered, unannounced, to every client mid-session.
2. **A3 exists to make upgrading safe, not to make it automatic.** Its origin (#267) is field
   evidence: on Windows `uv tool upgrade` corrupted the in-use install and took the MCP server
   down for an entire session. Reading "we can now upgrade without corrupting anything" as "so
   let it upgrade unattended" inverts the reason the mechanism was built. It removed a hazard
   from an upgrade the user chose; it did not argue for choosing on their behalf.
3. **The freshness signal is racy, and under a timer its safe failure is a silent one.** On
   2026-08-09 PyPI's JSON API served 3.0.0 several minutes before the `/simple/` index did.
   `latest_version()` reads the JSON API; `uv pip install` reads `/simple/`. That window fails
   *safe* — verified against the code, not assumed: `_run_uv` raises on uv's non-zero exit,
   `build_version` catches it, `shutil.rmtree`s the half-built directory and re-raises
   *"The in-use install is untouched"*, and `repoint()` is never reached, so `current` keeps
   selecting the working version. Safe, but nobody reads the stderr of a background timer: the
   machine would retry into the same error until the index caught up, with no operator aware that
   anything had been attempted.

**What would lift the gate** — stated so (c) is revisitable rather than permanently forbidden:

- the tool contract carries a version a client can read and pin against, so a breaking server can
  be declined rather than absorbed;
- the notify path has been in the field long enough to show whether drift is actually noticed —
  if (a) works, (c)'s remaining benefit is small;
- the freshness probe reads the same index the installer resolves from, closing ground 3.

## Rejected alternatives

### Auto-apply now (option c)

Rejected on the three grounds above. Ground 1 alone is sufficient: hive has shipped two breaking
majors in one day, and has no mechanism by which a client could notice, let alone refuse.

### Silence — notify nothing, offer nothing

Rejected because it is the current behaviour, and this ADR is being written from inside its
failure mode. A maintainer install sat on 1.43.1 while 3.0.0 was published, and that was noticed
only because somebody happened to look. A user with no notification has no way to distinguish
"my client is current" from "my client is three majors behind"; the absence of a signal reads
identically to a healthy state.

### Notify by polling PyPI from the health path

Rejected on shape, not on intent — the intent is adopted as (a). Making `vault_health` call PyPI
would put a network round-trip on a path contracted to be cheap and frequently called, to report
a condition that changes a few times a month, and would make health *reporting* fail whenever
PyPI is unreachable. That is precisely the coupling ADR-018 removed from the write path, and
reintroducing it one path over would be a poor trade.

## Consequences

### Positive

- A breaking release cannot change the tool contract underneath a running agent. The blast radius
  of a bad major stays bounded by how many people chose to install it.
- Upgrading remains an act with a human behind it, which is also what makes a rollback
  attributable: someone knows when the version changed.
- The gate is written with named lifting conditions, so (c) is a deferred decision rather than a
  refused one — and the first condition (tool-contract versioning) is independently worth having.
- (a) is cheap and reversible: it adds information and takes no action.

### Negative

- **Users still stay out of date until they act.** This ADR surfaces that failure mode rather than
  fixing it; (a) converts a silent problem into a visible one, which is progress but not a cure.
- Two install models continue to coexist — `uv tool` on POSIX, A3 plus launcher on Windows — with
  the divergence cost ADR-019 already accepted.
- Drift notification is only as useful as the client's willingness to surface it. Hive can put the
  fact in band; it cannot make an agent or a human read it.
- The unattended-trigger clause constrains a consumer in another repo, so the policy holds only as
  long as dotfiles respects it. It is a stated boundary, not an enforced one.

### Neutral

- `self_upgrade()`, `latest_version()`, `build_version()` and `_gc_other_versions()` are unchanged
  by this decision. What changes is who is allowed to call them, and when.
- ADR-019's ownership decision is untouched: this ADR says nothing about *where* a launcher lives,
  only about what may trigger an upgrade.
- POSIX keeps `uv tool upgrade hive-vault` as its upgrade path.

## References

- [#292](https://github.com/mlorentedev/hive/issues/292) — the `self-upgrade` end-to-end ticket
  whose unattended-trigger contract this ADR scopes
- [#328](https://github.com/mlorentedev/hive/issues/328) / `specs/HIVE-328-runtime-launcher/` —
  the launcher that makes option (b) reachable on Windows
- [#176](https://github.com/mlorentedev/hive/issues/176) — Phase C rollout, a downstream consumer
  of whatever upgrade policy is chosen
- [ADR-015](adr-015-windows-daemon-supervision-upgrade.md) — A3, and the #267 corruption that
  motivated it
- [ADR-018](adr-018-asynchronous-commit-queue.md) — the "no expensive dependency on a hot path"
  precedent that constrains option (a)
- [ADR-019](adr-019-launcher-ownership.md) — launcher ownership; Windows-only, and deliberately
  silent on upgrade policy
- `src/hive/_runtime.py` — `latest_version()`, `build_version()`, `self_upgrade()`
- `src/hive/_vault_health.py` — the `## server` identity block option (a) builds on
