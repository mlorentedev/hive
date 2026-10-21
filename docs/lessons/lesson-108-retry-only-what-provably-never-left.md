---
id: lesson-108-retry-only-what-provably-never-left
type: lesson
status: active
created: "2026-10-21"
owner: manu
tags: [hive, lesson, client, retry, idempotency, testing, HIVE-487]
---

# Retry only what provably never left

**Context:** `hive client` exited for good on the first refused connect, so a
daemon restart (~30 s under `RestartSec=30`) cost the session its Hive tools
(#487). The fix waits for the daemon to come back.

**Problem:** The relay raised the same `ClientError("daemon unavailable")` from
three places: the connect, the request write, and the response read. Only the
first one is before anything reaches the daemon. Retrying the other two could
run a `vault_write` twice. Two more traps: `ssl.SSLError` is an `OSError`, so
an impostor or an expired pin lands in a broad `except OSError` unless the SSL
branch comes first. And a retry loop placed in `_connect` would also make
`close()`, which runs in `run_client`'s `finally`, hang for the whole window.

**Solution:** The exception type carries the safety fact.
`_RefusedBeforeSendError` is raised only by the handshake's `OSError` branch
and, since #513, its `ssl.SSLEOFError` branch. Only `forward()` retries it, on a fixed 1 s interval. The outage length is
known, and exponential backoff would overshoot the daemon's return.
Post-send failures raise the parent `DaemonUnavailableError`: answered,
survived, not retried. A mutation that retries post-send turns the
duplicate-write test red.

**Testing notes:** To hold a port without a race (#417), bind it and delay
`server_activate()`. A connect then fails before sending: *refused* on Linux,
a *timeout* on macOS (measured). Both are pre-send. `ThreadingHTTPServer.shutdown()`
blocks forever if `serve_forever()` never ran, so a red-phase fixture whose
server never came back hangs teardown unless it guards that.

**Follow-up (#513):** A listener that closes before presenting a certificate
is a restart, not an impersonation: only a ClientHello left. If it closed
after reading the ClientHello the client sees `SSLEOFError`, a subclass of
`SSLError`, so it needs its own branch ahead of the impostor branch. If it
closed with the ClientHello unread the kernel sends an RST, which surfaces as
a plain `OSError` and was already retried. A test that closes without reading
first therefore races between the two and can pass without the fix. Read the
ClientHello, then close. A plaintext impostor gives `WRONG_VERSION_NUMBER` and
a foreign certificate gives `CERTIFICATE_VERIFY_FAILED`. Both keep failing at
once.
