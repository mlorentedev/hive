---
id: adr-013-write-idempotency-at-most-once
type: adr
status: active
created: "2026-06-02"
---

# ADR-013: Write Idempotency — At-Most-Once via Idempotency Key

## Status

Accepted — 2026-06-02. Ships with HIVE-118 Phase C (idempotency slice). Realizes ADR-011 §6.2.

## Context

ADR-011 §6.2 specified write idempotency as a Phase C requirement. The restart-on-upgrade spike (2026-06-02, `specs/HIVE-118-phase-c-daemon-model/spike/upgrade_spike.py`, 2/2) turned it from "nice to have" into a prerequisite:

- A daemon restart (in-place upgrade, supervised crash, mid-session swap) **cancels in-flight tool handlers mid-execution** — FastMCP's streamable-http session manager tears down the active request on lifespan shutdown. Neither the client ack nor the server-side write is guaranteed to complete. A true in-flight *drain* is **not achievable** over the transport (proven empirically).
- Therefore the only safe contract is: **a cut write must be safely retryable.** The retry comes from the client (auto-reconnect, later slice) or from the operator. Without an at-most-once primitive, that retry **duplicates** — and for `append` mode a content check cannot distinguish "already applied" from "two legitimately identical appends", so duplication is silent and unrecoverable.

Two designs were weighed (full discussion in `specs/.../verification.md` decisions log, 2026-06-02):

- **2a — at-most-once key.** `vault_write`/`vault_patch` carry an idempotency key; the owner claims it in a SQLite store with a `UNIQUE` column via `INSERT OR IGNORE` under the existing intra-process write lock. A key already present makes the call a no-op. Spike-proven cross-OS **3/3** (`idempotency_spike.py`), including 12 concurrent duplicates → one row.
- **2b — durable command journal.** Journal the write intent before the slow git commit, ack the client early, and let an async reconciler apply + replay idempotently on restart (the outbox / event-sourcing family).

Telemetry context: the Phase C latency-tail gate **does not fire** (lock contention ~0, WAL tiny, 0 timeouts); the load harness held **64 parallel sessions green** ("latency cost only"). hive is a single-owner, local, low-write-volume daemon.

## Decision

**Adopt 2a now.**

- `vault_write` and `vault_patch` gain an `idempotency_key` parameter (empty-string default — today's behaviour, fully backward compatible; `""` means "no idempotency", per the MCP schema rule that forbids `| None`).
- On a **non-empty** key the owner atomically **claims** it in a SQLite applied-key store (`UNIQUE` column + `INSERT OR IGNORE` under the process write lock). A newly-claimed key proceeds to the real write + git commit; an **already-present** key short-circuits to a no-op response (`(idempotent no-op — key already applied)`). This is **at-most-once** and, because it keys on the client token rather than content, it is **safe for `append` mode** by construction.
- The stdio fallback consults the **same** store path, so the guarantee holds whether the call lands on the daemon or the in-process fallback.

**Defer 2b** as the **documented evolution**, gated on EITHER (i) telemetry showing git-commit serialization as a real bottleneck, OR (ii) genuine concurrent write-heavy load arriving. Rationale:

- 2b optimises a bottleneck telemetry says **isn't firing** (YAGNI; the project's own recorded lesson is "re-measure before escalating an architecture phase").
- 2b changes **ACK semantics** (accepted ≠ committed) — an observable contract change needing bilingual docs — and adds a durable journal + reconciler + replay-on-startup.
- **Crucially, the public contract — the `idempotency_key` on the write tools — is identical in 2a and 2b.** 2b only changes *where and when* the keyed write is applied. So 2a → 2b is **additive, not a rewrite**: choosing 2a now costs zero future rework on the load-bearing primitive.

**If 2b is later built**, two cross-OS constraints are fixed here so they are not re-derived: the journal MUST be **SQLite-backed** (a raw-file journal's `fsync` / locking / atomic-append semantics differ on Windows); and because Windows locks loaded native extensions (a running tool's `.pyd`/`.dll` cannot be replaced in place, unlike POSIX), 2b's restart should prefer a **supervisor-driven stop → upgrade → start** over daemon-side in-place self-detect, to avoid a divergent per-OS upgrade mechanism.

## Consequences

### Positive

- **Closes the restart-on-upgrade in-flight gap** (together with auto-reconnect): a cancelled/retried write is at-most-once, so a transparent retry is safe.
- **Small and spike-proven cross-OS** (3/3 Linux + Windows), reusing the established `_SqliteTracker` pattern (ADR-004 / ADR-012) and the single-owner write lock (ADR-011).
- **Backward compatible.** Empty key = today's behaviour exactly; no ACK-semantics change; no migration.
- **Captures 2b's intent durably without building it.** The identical public key keeps the evolution additive.

### Negative

- **Depends on the client supplying a stable key** (and, for the restart case, on auto-reconnect retrying it). A client that neither keys nor retries gets today's behaviour — no regression, but no new guarantee either.
- **The applied-key store grows** and needs a retention policy (TTL or row cap); unbounded growth is a slow leak. Tracked as a follow-up; the initial store may cap by age/count.
- **Not single-write crash-atomicity.** A crash *between* file-write and git-commit is handled by slice 1.2 startup self-heal + retry, not by 2a itself. 2a guarantees no *duplicate* apply, not an all-or-nothing single apply.
- **Cross-process at-most-once assumes the single-owner store** (ADR-011). Two owners against different store paths would each dedupe independently — out of scope (the daemon singleton lock from slice 1.2 enforces one owner).

## Alternatives considered

### A. 2b — durable command journal + idempotent replay

Journal intent before commit, ack early, async reconciler replays on restart. **Deferred** (see Decision). Right end-state for genuine write-heavy scale or a hard server-side-recovery requirement; over-engineered for hive's measured workload today, and additive on top of 2a when justified.

### B. Content-hash deduplication

Dedupe by hashing the write payload. **Rejected:** cannot distinguish "already applied" from "two legitimately identical appends" — the exact §6.2 failure mode for `append` mode.

### C. No idempotency (at-least-once + reconcile)

Rely on the client/operator to reconcile duplicates. **Rejected:** a transparent auto-reconnect retry would silently duplicate; unacceptable for a write tool.

## Implementation

`src/hive/_idempotency.py` (new):
- `IdempotencyStore(_SqliteTracker)` mirroring `_lock_eviction.py`. `claim(key) -> bool` returns `True` when newly claimed, `False` when the key was already applied — `INSERT OR IGNORE` under the tracker lock + `UNIQUE` column. DB at `~/.local/share/hive/idempotency.db`.

`src/hive/_vault_write.py`:
- `vault_write` / `vault_patch` gain `idempotency_key: str = ""`. Non-empty → `claim` before applying; an already-applied key short-circuits to a no-op response suffixed `(idempotent no-op — key already applied)`.

`src/hive/_context.py` + `src/hive/server.py`:
- `ServerContext.idempotency: IdempotencyStore`; `create_server` constructs it.

`tests/`:
- Tool-level tests mirroring the spike's three checks: sequential duplicate is a no-op (including `append` mode), distinct keys both apply, concurrent duplicates dedupe to one.

## References

- Spike: `specs/HIVE-118-phase-c-daemon-model/spike/idempotency_spike.py` — 3/3 Linux + Windows (the executable spec for this decision)
- Spike: `specs/HIVE-118-phase-c-daemon-model/spike/upgrade_spike.py` — the restart-on-upgrade finding that made this a prerequisite
- Design: ADR-011 §6.2 (the requirement this realizes); ADR-004 (SQLite tracker locking); ADR-012 (the `_SqliteTracker` pattern precedent)
- Discussion: `specs/HIVE-118-phase-c-daemon-model/verification.md` decisions log, 2026-06-02 (2a vs 2b, Windows portability, telemetry gate)
