---
id: FEAT-015-fts5-vault-search
type: proposal
status: active
created: "2026-08-17"
owner: manu
tags: [search, fts5, sqlite, mcp, optimization]
---

# Proposal: FEAT-015 — SQLite FTS5 Index & BM25 Ranking for `vault_search`

> **Issue:** [mlorentedev/hive#380](https://github.com/mlorentedev/hive/issues/380)  
> **Pattern Reference:** `00_meta/patterns/pattern-sqlite-fts5-search.md`

## 1. Why (Motivation & Value)

Currently, `vault_search` in `hive-vault` performs a linear sweep on every tool call using Python's `search_root.rglob("*.md")`, opening every single file from disk and running substring checks (`query_lower in ln.lower()`).
Across 1,440+ vault markdown files:
1. **High Latency & Disk I/O:** Every search incurs hundreds of filesystem read calls, taking 100–300ms instead of <2ms.
2. **Naive Substring Matching:** No tokenization, word boundary awareness, or stemming (e.g. searching "deploying" misses "deployment" and "deploys").
3. **No True BM25 Ranking:** Relevance scoring is arbitrary line-count based rather than term-frequency/inverse-document-frequency with field weighting.

By embedding a zero-dependency SQLite FTS5 index with WAL mode in `hive`, all AI agents (Claude, Hermes, Antigravity) get instant sub-millisecond searches with Porter stemming, diacritic normalization, and weighted field ranking (`title: 10.0`, `tags: 3.0`, `content: 1.0`).

## 2. What Changes

1. **New Module `src/hive/_fts.py`:**
   - Implements `VaultFTSIndex` backed by Python standard library `sqlite3` with FTS5.
   - Cache storage at `~/.cache/hive/fts/vault_<hash>.db` (or OS temp cache).
   - Fast incremental sync comparing `mtime` (syncs 1,400+ files in ~25ms on cold boot, <1ms on incremental).
   - Weighted BM25 query with Porter stemmer + unicode61 tokenizer.
   - Snippet extraction for highlighted match previews.
2. **Wire into `_vault_read.py`:**
   - `vault_search(ranked=True)` and general queries leverage FTS5 index.
   - Preserves all filters (`type_filter`, `status_filter`, `tag_filter`, `scope`, `project`, `limit`, `max_lines`).
   - Seamless fallback to flat search if FTS index cannot be opened.
3. **Incremental Index Hook:**
   - `vault_write` and `vault_patch` update the specific file record in FTS5 in-memory/on-disk.

## 3. Acceptance Criteria

- [ ] **AC1 — Sub-millisecond Search:** `vault_search(query="...", ranked=True)` returns results in <5ms on a 1,000+ note vault.
- [ ] **AC2 — Weighted BM25 Scoring:** Matches in `title` score higher than `tags`, which score higher than `body`.
- [ ] **AC3 — Porter Stemming & Diacritics:** Query "deploying" matches notes containing "deployment" or "deploy"; accented queries match unaccented tokens.
- [ ] **AC4 — Filter Fidelity:** `type_filter`, `status_filter`, `tag_filter`, and `scope` continue to filter results accurately.
- [ ] **AC5 — Incremental Auto-Sync:** Writing or patching a note via `vault_write` / `vault_patch` immediately updates the FTS index.
- [ ] **AC6 — Safe Fallback:** If SQLite FTS is disabled or errors, `vault_search` falls back to linear scan without crashing.
- [ ] **AC7 — Comprehensive Tests:** Full test suite in `tests/test_fts.py` with 100% pass rate.
