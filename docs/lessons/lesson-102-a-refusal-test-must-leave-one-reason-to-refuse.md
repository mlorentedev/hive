---
id: lesson-102-a-refusal-test-must-leave-one-reason-to-refuse
type: lesson
status: active
created: "2026-09-30"
owner: manu
tags: [hive, lesson, testing, tls, mutation, security, HIVE-456]
---

# A refusal test must leave one reason to refuse

**Context:** ADR-022 Amendment 1 requires the stable port to accept TLS 1.3
only (#456). uvicorn has no minimum-version setting, so the daemon loads the
config and sets `config.ssl.minimum_version = TLSv1_3` itself.
`test_stable_port_serves_tls13_only` connects with a client capped at TLS 1.2
and expects the handshake to fail.

**Problem:** The test passed before the line it guards was written. With the
minimum removed, the handshake still failed, with `NO_SHARED_CIPHER` rather
than a protocol-version error. uvicorn's default cipher string is `"TLSv1"`.
After OpenSSL's security level filters it, it shares no TLS 1.2 cipher with
Python's default client. So the server refused TLS 1.2 by accident, and a
client offering legacy ciphers would have negotiated it. A test that expects
a refusal passes on any refusal, so it could not tell the guard from the
accident.

**Solution:**

- **Remove every other reason to refuse.** The TLS 1.2 client now offers
  every cipher (`ALL:@SECLEVEL=0`). With the minimum removed, that client
  completes the handshake and the test fails; with the minimum in place, it
  passes.
- **Do not pin the error text instead.** With the minimum set, the server
  sometimes closes the socket without sending an alert, so the client sees
  `UNEXPECTED_EOF_WHILE_READING`. Matching the message would be flaky. The
  mutation run is what proves the refusal comes from the version check.

**Why:** A test whose expected outcome is a refusal, an error or an
absence passes for every cause of that outcome. Before trusting it, delete the
line it guards and watch it fail. If it still passes, take away the other
causes until the guarded line is the only one left. This is the test-side form
of the positive control ADR-022 A1 already requires for impostor tests (an
impostor that never saw a ClientHello proves nothing).

**Tags:** `#testing` `#tls` `#mutation` `#security` `#HIVE-456`
