---
id: adr-016-cross-agent-memory-consolidation
type: adr
status: proposed
created: "2026-06-19"
---

# ADR-016: Cross-Agent Memory Consolidation — One Store (Vault), One API (Hive)

> **PROPOSED — ready for ratification; NOT merged.** Deliverable of a read-only architecture spike
> (`mlorentedev/memory-arch-spike`). The open placement question (2a vs 2b) was **resolved on
> 2026-06-19** by the daemon-regression diagnosis
> (`~/.local/share/hive/analysis-daemon-regression-and-intelligence.md`) → **2a (embed in hive)**.
> This ADR records the analysis and the resolved recommendation; final acceptance is by PR review.
> No hive code was changed by the spike.

## Status

Proposed — 2026-06-19 (open question resolved same day). Spike question: *how does the
cross-agent memory system consolidate into ONE thing?* **Placement resolved to 2a (embed in hive)**
by the daemon-regression diagnosis — see Decision §2. Awaiting PR ratification; not merged. Touches dotfiles
[#117](https://github.com/mlorentedev/dotfiles/issues/117),
[#439](https://github.com/mlorentedev/dotfiles/issues/439),
[#410](https://github.com/mlorentedev/dotfiles/issues/410),
[#405](https://github.com/mlorentedev/dotfiles/issues/405),
[#450](https://github.com/mlorentedev/dotfiles/issues/450) and hive
[#246](https://github.com/mlorentedev/hive/issues/246) / [#247](https://github.com/mlorentedev/hive/issues/247).

## Context

### The governing constraint: GUARD-001

`AGENTS.md` (dotfiles, "Neural Hive" protocol) states the keystone rule verbatim:

> **MEMORY SINGLE-SINK (GUARD-001):** The vault is the **only** sink for agent memory —
> `MEMORY.md`, `memory/`, and session handoffs/journals live there and nowhere else.
> **Hive is the memory API over the vault**: read and write memory through Hive, never by
> committing memory files into a code repo. A global `core.hooksPath` pre-commit guard rejects
> `MEMORY.md` / `memory/` in any non-vault repo, and `dotf init` bakes the matching `.gitignore`;
> never bypass it with `--no-verify` to sink memory into a repo.

GUARD-001 is not advisory — it is enforced by a pre-commit hook. Any consolidation that introduces
a durable store which is **not** the vault is, by definition, a GUARD-001 violation and must be
justified as a *deliberate amendment* of the standing order, not slipped in.

### The memory system is a constellation, not one thing

What the spike calls "the cross-agent memory system" is today four layers plus the plumbing that
wires them:

| Layer | Today | Store | Reach |
|---|---|---|---|
| **L0 — conversation flow / observations** | `claude-mem` (auto-capture via hooks) | claude-mem SQLite | **Claude only** |
| **L1 — session handoffs & per-project journals** | `/handoff` skill + `session-handoff.sh` hook | vault markdown (`10_projects/<p>/sessions/…`) | Claude now; cross-agent is the goal |
| **L2 — crystallized knowledge** (lessons, ADRs, patterns) | hive `capture_lesson` + reinforcement | vault markdown + SQLite side-table | via hive (any MCP client) |
| **Plumbing** | hooks, symlink resolver, path wiring | — | cross-OS, partial |

The relevant dotfiles work:

- **#117 (MEMORY-001)** — cross-agent session-handoff **markdown bridge** (L1). *Decided*; now
  targets the `dot` CLI thin-adapter, not per-agent shell scripts. Markdown-in-vault. No second store.
- **#410 (MEMORY-003)** — per-project session journal in each project's vault folder (L1).
  *Decided* (architecture corrected twice by the maintainer); remaining work is execution/backfill.
- **#405 (HARNESS-026, ADR-023)** — agnostic session-**start**: extract a session-brief core, Claude
  hook becomes a thin shim, file-based agents get a compiled brief block. *In progress.*
- **#439 (MEM-001 prestudy)** — unified cross-agent **conversation-flow** store (L0). *Undecided
  prestudy.* This is the **only** issue that names **engram** and proposes a SQLite/FTS5 second store.
  It flags the triple-store entropy risk itself and recommends "pilot before migrate." It also
  suggests harvesting engram's **What/Why/Where/Learned schema + topic-key upsert** into hive's
  `capture_lesson` — the single most direct "fold into hive" signal across all five issues.
- **#450 (HARNESS-025)** — hive integration **robustness** review (path resolution, daemon-env
  provisioning, observability, registration drift). *Open.* Its P1 (self-contained `hive service
  install` env injection) is already filed as **hive#247**; path-resolution hardening is **hive#246**.

### What #450 actually gates

#450 is about **which vault path the daemon resolves and how reliably**, summarized as the desired
precedence "env > config > default, fail-loud" (hive#246). It is **not** about memory write
durability or schemas. Its own stated goal is narrow: stop the #446 daemon-env workaround from
becoming permanent. **No #450 acceptance criterion declares a single-memory-store gate.**

But the dependency is real and asymmetric: *if hive becomes the single write path for all agent
memory, then a daemon that can silently resolve the wrong vault path (finding **a**) or whose
multiple registrations drift (finding **e**) silently corrupts the memory system's correctness.*
So #450 is a **soundness prerequisite/risk for Options 1 and 3, and irrelevant to Option 2.** This
reading is reinforced by a now-completed reliability investigation
(`~/.local/share/hive/analysis-daemon-regression-and-intelligence.md`, 2026-06-19): the write-path
hangs (`vault_commit timed out after 60s → killed subprocess → lock_eviction`; 24 deadline kills,
6 evictions) are **real but were misdiagnosed**. Root cause is **not** the daemon and **not** a
structural locking limit — it is hive's auto-commit running `git commit` **without `--no-verify`**
against the vault's slow pre-commit hook chain (gitleaks + a `language:python` venv-building hook),
which blows past the 60s deadline on Windows. The daemon was **never running** (100% `mode=fallback`;
Windows install fails *Access is denied*) and the hang **predates the daemon by 12 days** — so the
daemon-correlation is **refuted**. The fix is discrete and S-effort (hive auto-commit `--no-verify`,
with a compensating pre-write or push-side secret scan). Concentrating *all* memory writes onto that
path before that fix lands would still amplify a live fault.

### Hive already owns an "engram-shaped" trajectory

Engram (`github.com/Gentleman-Programming/engram`) is a Go binary that is simultaneously MCP server
+ HTTP API + CLI + TUI over **SQLite + FTS5**, agent-agnostic. Its differentiators are: agent-agnostic
reach, an indexed store, and a clean memory schema (What/Why/Where/Learned, topic-key upsert).

Crucially, hive is **not** greenfield against that shape:

- **HIVE-97** (lesson reinforcement) — hive already runs a WAL-mode SQLite **side-table** as a
  *derived* signal layer (reinforcement counts, decayed confidence), never the source of truth.
- **HIVE-211** (`vault_ask`, archived) — an **already-designed** optional semantic-retrieval layer:
  embeddings + vector index **persisted server-side in the daemon**, keyed to the embed-model id and
  **rebuilt on mismatch**. The vault markdown stays authoritative; the index is a rebuildable,
  opt-in accelerator.
- **ADR-005** (transport & scale) and **ADR-011** (daemon) already give hive a long-lived service
  with an HTTP path on the roadmap — the "service" half of engram's shape.

The discipline that makes this safe is explicit in hive's design language: **the index is a derived
artifact, never a second source of truth.** That is exactly the line between a healthy Option 3 and
a GUARD-001-violating Option 2.

### Prior art: fae-brain-spike (what it does and does not prove)

The `fae-brain-spike` validates an `eve` (agent runtime) → LiteLLM (provider proxy) → NaN plumbing
with durable resume, and a *planned* Hive MCP attachment for knowledge. It **does not implement a
memory store** — no SQLite/FTS5, no capture/index/retrieval. So it is prior art for *"heterogeneous
agents attach to hive over MCP,"* **not** for "an indexed memory service works here." It supports the
feasibility of a hive-centric hub; it says nothing in favour of engram-the-binary.

### Clarification (2026-06-19, post-spike): build, don't adopt

The maintainer's stance (recorded after the first draft): *engram is never adopted as a foreign
binary/dependency — we copy what serves us and build it into our own infra, possibly inside hive.
"Somos SRE y builders."* This **retires Option 2 as a contender** (no foreign store, no foreign API
surface) and collapses Options 1 and 3 into a single posture: *build the engram-shaped capability
ourselves.* The live decision is therefore no longer "adopt vs build" but **where the self-built,
engram-shaped indexed component lives** relative to hive.

It also sharpens the governing rule. The real constraints are two, and neither is "one process / one
store":

- **GUARD-001 constrains the API surface** — agents read/write memory *through hive*. A second
  *agent-facing* memory API would violate it.
- **SSOT constrains authority** — the vault markdown is the source of truth; any index is *derived
  and rebuildable*. A second *source of truth* would violate it.

A self-built sibling *service* that hive queries (and that holds no authoritative state) violates
**neither** — it is a second service, not a second API surface and not a second source of truth.
Consequently, the path-decoupling benefit previously credited only to Option 2 (an index at its own
stable path, independent of the vault-path mess) is now capturable **without** SSOT cost, in either
placement.

## Options considered

> The three options below are retained as the analysis of record. Per the clarification above,
> Option 2 (adopt engram-the-binary) is **off the table**; Options 1 and 3 merge into "build it
> ourselves," and the operative decision moves to **placement** (see *Real fork* after the table).

### Option 1 — Fold memory into hive

Make hive the single cross-agent memory API. The dotfiles capture plumbing (#117/#405/mirror) writes
**through** hive tools instead of writing markdown directly; claude-mem's L0 role is absorbed (a hive
`session_capture`) or explicitly bounded; engram's schema is harvested into `capture_lesson`.

- **Subsumes:** L1 + L2 fully; L0 if conversation-flow capture is added to hive. The capture hooks
  and symlink/path plumbing become hive concerns rather than per-agent shell scripts.
- **SSOT impact:** **Best.** Exactly GUARD-001 — one store (vault), one API (hive). No new store.
- **Migration cost:** Moderate. Reuses hive's existing vault write path + SQLite side-table infra;
  main new work is cross-agent capture + (optionally) L0 absorption and repointing dotfiles hooks.
- **Cross-OS / path impact:** **Does not fix** hive→vault path coupling — and makes it *more*
  load-bearing (all memory now depends on correct path resolution + daemon health).
- **Dependency on #450:** **High — gating.** Plus the suspected locking regression.

### Option 2 — Adopt engram

Run engram as the agent-agnostic L0 (and possibly L1) store; agents read/write it over MCP/HTTP.

- **Subsumes:** L0 across all agents (replaces claude-mem's Claude-only capture); gives FTS5 retrieval
  out of the box; serves heterogeneous agents (and eve, per fae-brain) directly.
- **SSOT impact:** **Worst.** Introduces a durable store that is **not the vault** → a second (or,
  alongside claude-mem, third) source of truth. Directly contradicts GUARD-001; #439 flags this
  entropy risk itself.
- **Migration cost:** Lowest to **stand up** (a working binary), highest to **integrate coherently**
  (boundaries, sync, dedup, authority) — plus a new Go runtime to operate cross-OS.
- **Cross-OS / path impact:** **Best on storage paths** — engram owns its own DB at a known path,
  sidestepping hive's vault-path coupling entirely for what it stores.
- **Dependency on #450:** **None / independent.** Can be piloted regardless of hive's robustness.

### Option 3 — Hybrid: hive adopts engram's shape

hive stays the single API over the vault, but matures into engram's *shape* internally: a service
(daemon, ADR-011/005) fronting an **indexed, derived store** (resume HIVE-211's vector index and/or
add FTS5), with engram's schema harvested into `capture_lesson`. The vault markdown remains
authoritative; the index is rebuildable.

- **Subsumes:** Everything Option 1 subsumes, plus engram's *design* (schema, topic-key upsert,
  indexed retrieval) — **without** engram's binary or a second source of truth.
- **SSOT impact:** **Good, if the derived-index discipline holds** (index rebuildable from the vault,
  never authoritative). HIVE-97 and HIVE-211 show hive already honours this line.
- **Migration cost:** Highest *engineering* cost, but **lower marginal cost than it appears** because
  HIVE-211 + HIVE-97 already exist — it is resumption/extension, not greenfield.
- **Cross-OS / path impact:** Same coupling as Option 1, with one mitigation: a path mistake corrupts
  a *rebuildable cache*, not the source.
- **Dependency on #450:** **High — gating** (same as Option 1), plus hive#246/#247 and the
  ADR-005 transport work.

### Comparison — original trilemma (superseded by the placement fork below)

| Dimension | **Opt 1 — Fold into hive** | **Opt 2 — Adopt engram** | **Opt 3 — Hybrid (hive ⊃ engram shape)** |
|---|---|---|---|
| **Subsumes** | L1+L2 (L0 if added); plumbing becomes hive's | L0 across agents; FTS5 retrieval; eve/HTTP clients | Opt 1 + engram's *design* (schema, upsert, index) |
| **Migration cost** | Moderate (reuses hive infra) | Low to stand up / high to integrate; + Go runtime | Highest eng. cost, but extends HIVE-211/97 (not greenfield) |
| **SSOT impact** | ✅ Best — pure GUARD-001 | ❌ Worst — 2nd/3rd source of truth | ✅ Good *if* index stays derived |
| **Cross-OS / path** | ❌ Doesn't fix coupling; makes it load-bearing | ✅ Best — own DB path, sidesteps coupling | ⚠️ Same coupling; mistake hits a rebuildable cache |
| **Dependency on #450** | 🔴 High — gating (+ locking regression) | 🟢 None — independent | 🔴 High — gating (+ #246/#247, ADR-005) |
| **GUARD-001** | Honours | Violates (needs amendment) | Honours if disciplined |

### Real fork (post-clarification): placement of the self-built, engram-shaped index

With "build it ourselves" assumed and engram-the-binary off the table, the operative decision is
**where the copied indexed component lives.** Both placements keep hive as the single agent-facing
API and the vault as the single source of truth, so both are SSOT-clean; the choice is operational.

| Dimension | **2a — Embedded in hive** | **2b — Sibling service (behind hive API)** | **Defer / harvest-only** |
|---|---|---|---|
| **What we build** | Resume HIVE-211 (+ optional FTS5) as a derived index inside hive | Copy engram's architecture as our own single-responsibility service hive queries | Only the Phase-0 schema harvest; no index yet |
| **SSOT / GUARD-001** | ✅ Best — one process, one API, derived index | ✅ Good — 2nd *service* not 2nd source/API; agents never hit it directly | ✅ Trivially clean |
| **Blast radius** | ❌ Couples indexing to a component **currently regressing on locking** | ✅ Best — index hang/corruption can't take down vault read/write | ✅ None added |
| **Dependency on #450 / locking** | 🔴 High — shares hive's write path + process | 🟡 Partial — own index path is decoupled; only vault write-through still gated | 🟢 None |
| **Cross-OS** | Inherits hive's (Python + daemon supervision, ADR-015) | New service on both OSes — but a Go binary is highly portable (a plus) | None added |
| **Language / tool fit** | Python, bolted onto existing tools | Free choice (Go, like engram) — SRE/builder win | n/a |
| **Cost** | Lowest marginal (HIVE-97/HIVE-211 exist) | Highest (new service + boundary + ops) | Minimal |

**Tie-breaker resolved (2026-06-19) → 2a.** The diagnosis came back: the regression is a *discrete,
fixable* write-path bug (auto-commit without `--no-verify` against a slow git hook), **not** a
structural limit in hive's daemon/process model. By the tie-breaker that points to **2a (embed)** —
the structural case that would have justified 2b (sibling) is **refuted**. One caveat carries
forward: 2a's *warm* index needs the hive daemon to actually run, and the daemon currently **never
runs on Windows** (ADR-015 upgrade-swap unresolved; install fails *Access is denied*). That argues
for **fixing the daemon supervision (ADR-015)** as a 2a prerequisite — not for building a second
service to dodge it.

## Decision (RECOMMENDED — pending human ratification)

**Build the engram-shaped capability ourselves; engram-the-binary is never a dependency or a sink.
The destination is unchanged — one agent-facing API (hive) over one source of truth (the vault),
per GUARD-001. The live decision is the *placement* of the self-built indexed component.** Concretely:

1. **Phase 0 (now, ungated, no placement question).** Harvest engram's **What/Why/Where/Learned
   schema + topic-key upsert** into `capture_lesson` (exactly the #439 suggestion). A schema change to
   an existing tool — SSOT-clean, no new store, no new service. The **one consolidation win with no
   gate**; do it first.

2. **Indexed retrieval component — placement RESOLVED → 2a (embed in hive).** The daemon-regression
   diagnosis (2026-06-19) refuted the structural-limit case that would have justified a sibling
   service, so the index is built **inside hive**: resume **HIVE-211** (vector index — note `vault_ask`
   is in fact already *shipped* under hive#228, disabled by default) and/or add FTS5 as a **derived,
   rebuildable** index. *Prerequisite:* a 2a warm index needs the hive daemon to run, currently
   blocked on **ADR-015** (Windows daemon supervision; install fails *Access is denied*). Fix the
   daemon supervision rather than build a sibling to avoid it. **2b (sibling service)** is retained
   only as a documented fallback should ADR-015 prove intractable.

3. **Gate the write-through, not the read-side index.** Routing all agent memory **writes through hive
   into the vault** (L1/L2) stays gated on **#450** — hive#247 (self-contained daemon env) + hive#246
   (path resolution, env > config > default, fail-loud) — **and** on the now-diagnosed write-path fix
   (hive auto-commit `--no-verify` + a compensating secret scan; a hive **reliability** concern,
   **not part of this memory spike** — to be filed as its own hive issue). A derived index with its
   **own** stable path is largely decoupled from #450, so the **read-side** retrieval over the
   *existing* vault can be stood up without waiting for the vault-path work. This is the engram-style
   path-decoupling benefit kept **without** SSOT cost.

4. **engram = code study, never infrastructure.** Run/read engram in a quarantined sandbox only, to
   mine its schema, FTS5 usage, and upsert ergonomics. Never a durable sink, never agent-facing
   (#439's "pilot before migrate / measure store-count cost").

### Phasing

- **Phase 0 (now, ungated):** schema + topic-key upsert into `capture_lesson`; optional engram
  code-study sandbox.
- **Diagnose (DONE 2026-06-19):** regression diagnosed as a discrete write-path git-hook latency bug
  (not daemon / not structural) → placement resolved to **2a**. Spin-out hive issues: (i) auto-commit
  `--no-verify` write-path fix; (ii) ADR-015 daemon supervision so a warm index can run.
- **Phase 1 (write-through, gated on #450 / hive#246+#247 / `--no-verify` write-path fix):** repoint
  cross-agent capture to write **through** hive; bound or absorb claude-mem's L0 role.
- **Phase 2 (read-side index, embed per 2a; gated on ADR-015 daemon for a warm shared index, else
  cold per-process rebuilds):** stand up the derived index for retrieval — **only if** measured
  retrieval quality demands it (HIVE-211's Stage-2 instrumentation gate; `vault_ask`/hive#228 already
  ships the disabled scaffolding).

## Consequences

- **Positive.** End-state is maximally SSOT-clean (one store, one API) and needs **no GUARD-001
  amendment.** Reuses hive's existing derived-store discipline (HIVE-97/HIVE-211) instead of taking on
  a second store or a Go runtime. The one immediately available win (schema harvest) is unblocked.
  Risk is sequenced behind the robustness work rather than piled on top of a regressing write path.
- **Negative.** The destination **does not** fix hive→vault path coupling — it makes correct path
  resolution and daemon health *more* load-bearing, so the #450/#246/#247 robustness work becomes a
  hard prerequisite, not a nicety. Highest engineering cost path for the indexed internals (though
  mitigated by the existing specs). Slower than simply running engram.
- **Neutral.** engram still contributes — as a *design reference* and a learning pilot — without
  becoming infrastructure. The dual-memory boundary (conversation flow vs crystallized) is preserved;
  it just resolves onto one API over time. fae-brain's "agents attach to hive over MCP" path remains
  valid and is reinforced.

### Rejected because

- **engram-the-binary as a dependency or sink** — a foreign agent-facing API + a non-vault source of
  truth violates both GUARD-001 and SSOT; #439 itself flags the entropy risk. Kept only as a
  quarantined code-study sandbox. (Copying its *design* into our own infra is the chosen path, not a
  rejected one.)
- **Doing the heavy fold now (ungated)** — would concentrate all memory writes on a path with an open
  robustness review (#450) and a suspected live locking regression; "remove the race, don't manage it"
  (ADR-014) argues for fixing the path *before* loading it.

## Open questions (for the ratification discussion)

1. **Placement — RESOLVED → 2a (embed).** (Was: 2a vs 2b; the 2026-06-19 diagnosis refuted the
   structural case that justified 2b.) Residual: confirm **ADR-015** daemon supervision is tractable
   on Windows; if not, reconsider 2b as the documented fallback.
2. **L0 ownership.** Does hive absorb conversation-flow capture (replacing claude-mem cross-agent), or
   does claude-mem stay as Claude-local L0 with hive owning L1+L2?
3. **Index type.** Resume HIVE-211's *vector* RAG, add engram-style *FTS5* lexical, or both?
   (HIVE-211 chose vector-only; engram is FTS5 — copying engram suggests reconsidering.)
4. **Gate definition.** The write-path `--no-verify` fix and ADR-015 daemon supervision are proposed as
   **separate hive issues** that Phase 1 / Phase 2 depend on (not folded into #450). Confirm that split.
5. **Code-study signal.** What concrete signal from the engram code-study decides "this component is
   worth copying" per piece (schema / upsert / FTS5 lifecycle)?

## References

- **GUARD-001** — `AGENTS.md` (dotfiles), "Neural Hive" protocol; Standing Orders #2/#3/#7 (SSOT).
- dotfiles issues: [#117](https://github.com/mlorentedev/dotfiles/issues/117) (MEMORY-001 session bridge),
  [#410](https://github.com/mlorentedev/dotfiles/issues/410) (MEMORY-003 per-project journal),
  [#405](https://github.com/mlorentedev/dotfiles/issues/405) (HARNESS-026 / ADR-023 agnostic session-start),
  [#439](https://github.com/mlorentedev/dotfiles/issues/439) (MEM-001 prestudy — engram candidate),
  [#450](https://github.com/mlorentedev/dotfiles/issues/450) (HARNESS-025 hive robustness),
  [#446](https://github.com/mlorentedev/dotfiles/issues/446) (HARNESS-024 path wiring),
  [#402](https://github.com/mlorentedev/dotfiles/issues/402) (MEMORY-002 symlink resolver).
- hive issues (by **number** — note dotfiles refs mis-label these as "HIVE-119"):
  [#246](https://github.com/mlorentedev/hive/issues/246) (path-resolution hardening),
  [#247](https://github.com/mlorentedev/hive/issues/247) (self-contained `hive service install` env).
- hive specs: `HIVE-97-lesson-reinforcement` (derived SQLite side-table),
  `HIVE-211-vault-ask-semantic` (archived — indexed semantic retrieval, derived index).
- hive ADRs: ADR-005 (transport & scale), ADR-011 (daemon model), ADR-012 (filelock eviction),
  ADR-014 (single deliberate committer), ADR-015 (Windows daemon supervision).
- engram: `github.com/Gentleman-Programming/engram` — Go, SQLite+FTS5, MCP+HTTP+CLI+TUI; schema
  What/Why/Where/Learned + topic-key upsert.
- Prior art: `fae-brain-spike` — eve + LiteLLM plumbing; Hive MCP attachment (knowledge read), no
  memory store implemented.
- Prestudy: `knowledge/10_projects/dotfiles/25-prestudy/2026-06-18-agent-memory-stack-engram-karpathy-headroom.md`.
- Diagnosis: `~/.local/share/hive/analysis-daemon-regression-and-intelligence.md` (2026-06-19) —
  write-path hang root-caused to auto-commit-without-`--no-verify` + slow vault pre-commit hook;
  daemon correlation refuted; placement resolved to 2a.
- Hive reliability prerequisites (separate from this spike, to be filed): auto-commit `--no-verify`
  write-path fix; **ADR-015** Windows daemon supervision (so a warm shared index can run);
  `vault_ask` already ships disabled under **hive#228**.
