# HIVE-118 spikes

Two runnable de-risks for ADR-011, both spawning a child "daemon" (FastMCP over
`127.0.0.1:<port>` with a `StaticTokenVerifier`) and driving thin
`fastmcp.Client`s against it:

- **`transport_spike.py`** — the transport + auth + owner-only-secret gate.
- **`load_spike.py`** — the single-owner concurrency model under parallel
  sessions.

The transport spike is the gate that must pass on **both** Linux and Windows
before ADR-011 is accepted and `tasks.md` is frozen.

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

- **transport_spike — Linux:** ✅ PASS `5/5, exit 0` (2026-05-31). See ADR-011 Status.
- **load_spike — Linux:** ✅ PASS `4/4` (2026-05-31). 8×25 → 200/200, p99≈66 ms,
  ~177 calls/s; 16×40 → 640/640, p99≈99 ms, ~295 calls/s, no HOL blocking.
- **Windows:** ⏳ PENDING — run **both** spikes on a Windows host. For
  `transport_spike`, confirm loopback port binding, no blocking firewall prompt
  for a `127.0.0.1` listener, and the `icacls` owner-only ACL on the token file.
  That closes ADR-011's last residual. (`load_spike` is OS-agnostic but worth a
  confirming Windows run.)

> Scope: the spikes validate the transport + single-owner concurrency layer, not
> the real vault/git path — that is the post-build multi-client integration test
> in `tasks.md`. They de-risk the model before it is built.
