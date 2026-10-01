---
id: lesson-101-a-stable-endpoint-must-prove-who-answers
type: lesson
status: active
created: "2026-09-30"
owner: manu
tags: [hive, lesson, security, transport, tls, threat-model, HIVE-456]
---

# A stable endpoint must prove who answers

**Context:** ADR-022 (#437) stopped MCP clients going stale after a daemon
restart. It did two things: it derived the loopback port from a public per-user
formula, and it kept the bearer token across restarts. It cited MCP's
Streamable HTTP rule to "authenticate every connection", and #453 shipped it.
After the merge, a security review found that the relay sends the bearer on the
first request to whatever is listening on that port (#456).

**Problem:** The original design hid two assumptions:

- **The port was hard to find.** The random port lived in a state file the
  owner wrote, so another local account had to guess it.
- **A captured token did not last.** The token changed on every start.

ADR-022 removed both, but the threat review did not change. "Authenticate every
connection" was read as "the server checks the client", the direction the code
already handled. While the owner's daemon is down, any other local account can
now bind the published port and collect a bearer that stays valid. It also
receives tool arguments and can return forged tool results. Nobody reviewing
the ADR asked what the client sends before it knows who answered.

**Solution:** ADR-022 Amendment 1 makes the stable endpoint TLS-only, with a
per-user self-signed `CA:FALSE` certificate:

- The relay pins that certificate on every TCP connection before it writes a
  byte.
- Direct HTTP hosts must verify the same certificate through per-process trust
  (`NODE_EXTRA_CA_CERTS`).
- `http://` registrations leave the supported contract.
- Any token that was ever sent in plaintext is rotated once.

The proof has to be per connection, because the relay opens a new connection
for each request. A handshake separate from the request would leave a gap for
an impostor to bind the port between the proof and the bearer.

**Why:** **Making an address predictable or a secret long-lived turns server
authentication from optional into mandatory.** Re-run the threat review
whenever either property changes. For each local endpoint, ask:

1. Who can bind this address while the owner is down?
2. What does the client send before it has verified who answered?
3. Is that verification tied to the same connection that carries the request?

A protocol's "authenticate every connection" usually means one direction. Check
which one it means. For HTTP clients with static configuration, TLS is the only
standard way to authenticate the server, so a mechanism that only the relay can
use leaves direct HTTP exposed.

**Tags:** `#security` `#transport` `#tls` `#threat-model` `#HIVE-456`
