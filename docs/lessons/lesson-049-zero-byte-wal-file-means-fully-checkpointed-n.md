---
id: lesson-049-zero-byte-wal-file-means-fully-checkpointed-n
type: lesson
status: active
created: "2026-05-27"
owner: manu
tags: [hive, lesson, sqlite, wal, debt]
---

# Zero-byte WAL file means fully checkpointed, not broken

**Context:** Debt triage session 2026-05-27: investigating ~/.local/share/hive/worker.db WAL sidecar observed as stale during HIVE-116 investigation.
**Problem:** worker.db had a 0-byte WAL file with mtime from Mar 10 (~2.5 months stale). Initial impression was that the WAL checkpoint wasn't running. This caused diagnostic confusion during HIVE-116 investigation.
**Solution:** A 0-byte .db-wal file means the WAL was FULLY checkpointed (success signal, not failure). SQLite leaves the empty WAL file behind after checkpoint. The old mtime just means no writes happened since then — normal for budget.db when OpenRouter isn't heavily used. Added _clean_stale_wal_files() at server startup in _helpers.py to auto-remove 0-byte WALs ≥30d stale, preventing future diagnostic confusion.
**Tags:** `#sqlite` `#wal` `#debt`
