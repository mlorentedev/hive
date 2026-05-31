# HIVE-118 spikes

Runnable de-risks for ADR-011, each spawning a child "daemon" (FastMCP over
`127.0.0.1:<port>` with a `StaticTokenVerifier`) and driving thin
`fastmcp.Client`s against it. Shared plumbing (spawn / readiness / client /
cleanup / report) lives in `_spikelib.py`, which carries the Windows-robust
behaviour learned the hard way (report before cleanup; kill+wait before
unlinking the daemon log).

- **`transport_spike.py`** — transport + auth + owner-only-secret gate.
- **`load_spike.py`** — single-owner concurrency under parallel sessions.
- **`idempotency_spike.py`** — at-most-once writes via an idempotency key (§6.2).
- **`resilience_spike.py`** — crash-only durability: survives `SIGKILL`,
  reconnect, and a client disconnect mid-call (§4).
- **`robustness_spike.py`** — transport/auth edges: large payload, auth bypass
  attempts, port-in-use.

Run them all (same command shape, on **both** Linux and Windows):

```bash
for s in transport load idempotency resilience robustness; do
  uv run python specs/HIVE-118-phase-c-daemon-model/spike/${s}_spike.py
done
```

The **transport** spike is the gate that must pass on both OSes before ADR-011
is accepted and `tasks.md` is frozen; the others de-risk design decisions and
resilience claims before the daemon is built.

## transport_spike.py

```bash
uv run python specs/HIVE-118-phase-c-daemon-model/spike/transport_spike.py
```

Exit `0` = all checks pass. Cross-OS: the owner-only-token-file check uses POSIX
mode `0600` on Linux/macOS and an `icacls` owner-only ACL on Windows (printing
the raw ACL for human confirmation).

## What it asserts

| Check | Why it matters |
|---|---|
| token file is mode `0600` | owner-only secret — the POSIX gate ADR-011 folded into the token |
| daemon binds `127.0.0.1` loopback | native transport, no named-pipe handle terrain (the HIVE-116 class) |
| correct token round-trips `tools/call` | the happy path works cross-process |
| missing token is rejected (401) | a bare loopback port is reachable by any local process — it MUST be closed |
| wrong token is rejected (401) | token actually gates, not just presence of a header |

The token is passed to the daemon via the environment, never argv, so it does
not leak through `ps` — the real daemon keeps this by reading the `0600` token
file.

## load_spike.py

```bash
# defaults: 8 sessions × 25 calls, slow()=0.4s
uv run python specs/HIVE-118-phase-c-daemon-model/spike/load_spike.py [sessions] [calls] [slow_s]
```

Emulates N parallel client sessions against ONE daemon that owns a shared SQLite
DB (WAL + `busy_timeout`, mirroring ADR-009) — the inter-process contention
class HIVE-115/116 fought, here collapsed to a single owner. Asserts:

| Check | Why it matters |
|---|---|
| N sessions × M calls, zero failures | no `database is locked` / contention error under parallel load |
| no lost writes (rows == N×M) | the single owner serialized every concurrent writer correctly |
| no head-of-line blocking | a slow (`time.sleep`) call does not starve other sessions' `ping`s |
| latency tail bounded | reports p50/p95/p99 + throughput so a serialized-tail regression is visible |

## Status

Linux (2026-05-31), all green:

| Spike | Result | Highlights |
|---|---|---|
| transport | ✅ 5/5 | round-trip, missing/wrong-token 401, `0600`/ACL token file |
| load | ✅ 4/4 | 8×25 → 200/200, p99≈50 ms, HOL ping p95≈5 ms |
| idempotency | ✅ 3/3 | dup-key no-op, 12 concurrent dupes → 1 row |
| resilience | ✅ 4/4 | durable + integrity-clean after `SIGKILL`, reconnect, disconnect survival |
| robustness | ✅ 3/3 | 1 MB payload intact, auth unbypassable, port-in-use exits cleanly |

- **Windows:** ⏳ PENDING — run all five on a Windows host. For `transport_spike`,
  confirm loopback port binding, no blocking firewall prompt for a `127.0.0.1`
  listener, and the `icacls` owner-only ACL on the token file. That closes
  ADR-011's last residual; the rest confirm the design/resilience claims cross-OS.

> **Windows robustness (2026-05-31):** both spikes print their result *before*
> best-effort temp cleanup and suppress a locked-file unlink — Windows keeps the
> daemon's log handle open until it fully exits, which previously crashed the
> report. The load spike's tools are `async` and offload blocking work via
> `asyncio.to_thread` (hive's real git pattern), so a slow op does not
> head-of-line-block other sessions; a naive *sync* loop-blocking tool would,
> which is exactly the pitfall to avoid in the daemon.

> Scope: the spikes validate the transport + single-owner concurrency layer, not
> the real vault/git path — that is the post-build multi-client integration test
> in `tasks.md`. They de-risk the model before it is built.
