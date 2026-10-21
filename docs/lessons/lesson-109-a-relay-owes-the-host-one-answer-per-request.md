---
id: lesson-109-a-relay-owes-the-host-one-answer-per-request
type: lesson
status: active
created: "2026-10-21"
owner: manu
tags: [hive, lesson, client, sse, streaming, json-rpc, HIVE-499]
---

# A relay owes the host one answer per request

**Context:** `hive client` relays stdio to the daemon's streamable HTTP. It
read a whole SSE body and only then wrote its frames, so a long tool call's
`notifications/progress` reached the host only with the result (#499).

**Problem:** Streaming the frames fixes the delay, but then a frame can reach
the host before the stream fails. Two cases follow, and they need opposite
handling:

- The stream breaks after the answer has gone out. Writing the error frame,
  as the buffered code always did, would answer the request twice.
- The stream ends before the answer. Measured on macOS, a clean close and an
  RST behave the same way here: `HTTPResponse.readline()` returns `b""` and
  no exception is raised. The old code then returned only the progress frame,
  and **the host's request was never answered**. That bug was there before
  streaming.

A third trap: `_reinitialize` forwards an `initialize` the host never sent, so
it must not emit anything.

**Solution:** Emit each frame through a sink as it arrives. `run_client` gives
every message an `_Answer` sink. The sink writes each frame and clears `owed`
once a frame with the request's `id` carrying `result` or `error` has gone
out. If the request is still owed after `forward()` returns, the relay reports
it as unavailable. An error frame goes out only while the answer is owed.
`_reinitialize` uses the default no-op sink.

**Testing notes:**

- To prove streaming without timing, the handler writes frame 1, then waits on
  an `Event` that the test's sink sets, and records whether it was set before
  it writes frame 2. A buffering relay never sets it.
- To get an RST from a `BaseHTTPRequestHandler`, set `SO_LINGER` to 0 and
  close `rfile`, `wfile` and the connection. The file objects otherwise keep
  the socket open until the server's teardown, which sends a FIN first. Then
  replace the closed objects with `BytesIO`, because the teardown still
  flushes them.
