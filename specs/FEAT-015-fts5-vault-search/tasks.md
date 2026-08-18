---
id: FEAT-015-fts5-vault-search
type: tasks
status: active
created: "2026-08-17"
owner: manu
---

# Tasks: FEAT-015 — SQLite FTS5 Index & BM25 Ranking for `vault_search`

> **Issue:** [mlorentedev/hive#380](https://github.com/mlorentedev/hive/issues/380)

## Phase 1: Core FTS5 Engine (`src/hive/_fts.py`)
- [x] Task 1.1: Implement `VaultFTSIndex` with schema creation (WAL mode, virtual table `vault_fts` using `porter unicode61`). [AC2, AC3]
- [x] Task 1.2: Implement `sync()` comparing file `mtime` for incremental updates and deletions. [AC5]
- [x] Task 1.3: Implement `search()` with BM25 weighted ranking (`title: 10.0`, `tags: 3.0`, `body: 1.0`) and snippet generation. [AC1, AC2, AC3, AC4]

## Phase 2: Tool Wiring & Incremental Hooks
- [x] Task 2.1: Wire `VaultFTSIndex` into `src/hive/_vault_read.py` (`vault_search` ranked mode). [AC1, AC4, AC6]
- [x] Task 2.2: Add incremental update calls to `src/hive/_vault_write.py` (`vault_write` and `vault_patch`). [AC5]
- [x] Task 2.3: Implement graceful error handling with automatic fallback to linear scan. [AC6]

## Phase 3: Testing & Verification
- [x] Task 3.1: Author unit test suite `tests/test_fts.py` covering schema, indexing, Porter stemming, diacritics, weighting, and filtering. [AC7]
- [x] Task 3.2: Verify full test suite passing (`uv run --extra dev pytest`). [AC7]
- [x] Task 3.3: Benchmark search performance on real vault (<5ms). [AC1]
