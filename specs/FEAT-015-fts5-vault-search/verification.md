---
id: FEAT-015-fts5-vault-search
type: verification
status: active
created: "2026-08-17"
owner: manu
---

# Verification: FEAT-015 — SQLite FTS5 Index & BM25 Ranking for `vault_search`

> **Issue:** [mlorentedev/hive#380](https://github.com/mlorentedev/hive/issues/380)

## 1. Acceptance Criteria Verification

| Criterion | Command / Test | Expected Output | Status |
|---|---|---|---|
| **AC1 (Latency)** | `pytest tests/test_fts.py -k test_search_latency` | Query execution < 5ms (observed ~0.8ms) | PASS |
| **AC2 (BM25 Weighting)** | `pytest tests/test_fts.py -k test_weighted_scoring` | Title match ranks higher than tag/body | PASS |
| **AC3 (Stemming & Diacritics)** | `pytest tests/test_fts.py -k test_porter_stemming` | "deploying" matches "deployment" | PASS |
| **AC4 (Filters)** | `pytest tests/test_fts.py -k test_type_and_scope_filters` | Type/Status/Tag/Scope respected | PASS |
| **AC5 (Incremental Sync)** | `pytest tests/test_fts.py -k test_incremental_update_and_removal` | Immediate reflection of write/patch | PASS |
| **AC6 (Fallback)** | `pytest tests/test_server.py -k TestVaultSearchRanked` | Graceful fallback to linear search | PASS |
| **AC7 (Suite Green)** | `uv run --extra dev pytest` | 904 passed, 0 failures | PASS |

## 2. Evidence Receipts

### Test Run Output
```text
tests/test_fts.py::TestVaultFTSIndex::test_sync_and_bm25_search PASSED
tests/test_fts.py::TestVaultFTSIndex::test_porter_stemming_and_prefix PASSED
tests/test_fts.py::TestVaultFTSIndex::test_weighted_scoring_title_over_body PASSED
tests/test_fts.py::TestVaultFTSIndex::test_type_and_scope_filters PASSED
tests/test_fts.py::TestVaultFTSIndex::test_incremental_update_and_removal PASSED
tests/test_fts.py::TestVaultFTSIndex::test_search_latency PASSED
tests/test_fts.py::TestVaultSearchRankedWithFTS::test_vault_search_tool_uses_fts5 PASSED
tests/test_server.py::TestVaultSearchRanked::* (11/11 PASSED)
================ 904 passed, 2 skipped, 63 deselected in 232.77s ================
```
