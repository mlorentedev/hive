---
id: lesson-088-text-mode-silently-rewrites-the-bytes-you-ask
type: lesson
status: active
created: "2026-08-09"
owner: manu
tags: [hive, lesson, python, encoding, newlines, windows, cross-platform, idempotency, testing, HIVE-328]
---

# Text mode silently rewrites the bytes you asked for, in both directions

**Context:** HIVE-328 PR2 writes a `hive.cmd` shim into hive's own `bin` directory. It must be byte-stable across upgrades: the acceptance criterion is that a repeat install rewrites nothing, so repeated upgrades neither churn the file nor fail while another process holds it open.
**Problem:** The first implementation wrote it with `Path.write_text` and compared with `Path.read_text`, and the idempotency test failed on an mtime that kept moving. Text mode translates newlines on *both* sides, and the two translations do not cancel. The renderer emits `CRLF` because a `.cmd` needs it; **reading** with universal newlines turns that back into `\n`, so the comparison could never match and every upgrade rewrote the shim. That was the harmless half. The other half only appears on the platform this code actually runs on: **writing** with `newline=None` expands `\n` to `os.linesep`, which on Windows is `\r\n` — so the `\r\n` in the template would have been written as `\r\r\n`, a malformed batch file, on every Windows install. On Linux, where the test ran, `os.linesep` is `\n` and that half is invisible.
**Solution:** Made the shim a byte-exact artifact — `render_launcher_script(...).encode("utf-8")`, then `read_bytes`/`write_bytes` on both sides of the comparison. No translation in either direction, and the file on disk is exactly what the renderer returned.
**Why:** `write_text`/`read_text` look like byte operations and are not; they are a codec *and* a newline transformer, and the transformer is keyed off the host OS. That makes the bug maximally awkward: it is invisible on the development platform and corrupting on the target one, which is the inverse of what cross-platform testing usually catches. Whenever content is generated for another program to parse — a shim, a config file, a lockfile, anything with a required line ending — treat it as bytes and compare it as bytes. The generalisation worth carrying is that the *failing* assertion here was about mtime, a detail nobody would have written a test for on purpose; it was an idempotency check, and idempotency checks are unusually good at surfacing encoding bugs because they force you to compare what you wrote against what came back.
**Tags:** `#python` `#encoding` `#newlines` `#windows` `#cross-platform` `#idempotency` `#testing` `#HIVE-328`
