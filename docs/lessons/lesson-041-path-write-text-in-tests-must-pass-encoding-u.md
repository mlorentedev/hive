---
id: lesson-041-path-write-text-in-tests-must-pass-encoding-u
type: lesson
status: active
created: "2026-05-21"
owner: manu
tags: [hive, lesson, testing, windows, encoding]
---

# Path.write_text in tests must pass encoding=utf-8 on Windows

**Context:** Re-running the full pytest suite on Windows during #109. `tests/test_server.py::TestVaultValidate::test_posix_class_in_heading_not_flagged` was already failing on master (unrelated to the identity block) — the test writes a markdown file with an em-dash and then calls `vault_health(checks=["links"])`, which routes the read through `_safe_read` (`f.read_text(encoding="utf-8")`).
**Problem:** Path.write_text(...) without an explicit encoding uses locale.getencoding() — cp1252 on Windows by default. cp1252 happily encodes the em-dash to byte 0x97. _safe_read then opens the file expecting UTF-8, sees the lone 0x97, raises UnicodeDecodeError, and the file is silently reported as `[error] ... File unreadable (I/O or encoding error)`. The test then sees `posix-heading.md` in the error message and false-positives — the actual POSIX-class regression check is masked.
**Solution:** Always pass `encoding="utf-8"` to Path.write_text (and read_text) in tests that put non-ASCII content into vault files. The production reader is hard-coded to UTF-8; writers must match. This bites on Windows only — Linux/macOS CI defaults to UTF-8 — so it's invisible until someone runs the suite locally on Windows.
**Tags:** `#testing` `#windows` `#encoding`
