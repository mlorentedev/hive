# HIVE-118 transport spike

De-risks ADR-011 §2's transport choice — **loopback Streamable-HTTP + per-daemon
bearer token** — by exercising the real round-trip end to end. This is the gate
that must pass on **both** Linux and Windows before ADR-011 is accepted and
`tasks.md` is frozen.

## Run

```bash
uv run python specs/HIVE-118-phase-c-daemon-model/spike/transport_spike.py
```

Exit `0` = all checks pass. The script spawns a child "daemon" (FastMCP over
`127.0.0.1:<port>` with a `StaticTokenVerifier`), then drives a thin
`fastmcp.Client` against it.

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

## Status

- **Linux:** ✅ PASS — `5/5 checks, exit 0` (2026-05-31). See ADR-011 Status.
- **Windows:** ⏳ PENDING. Run the same command on a Windows host and confirm:
  port binding on loopback, no blocking firewall prompt for a `127.0.0.1`
  listener, and a `0600`-equivalent owner-only ACL on the token file
  (`icacls` check rather than POSIX mode). That result closes ADR-011's last
  residual.
