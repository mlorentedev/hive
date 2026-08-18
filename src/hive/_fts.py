"""SQLite FTS5 full-text search index with BM25 weighted ranking for hive-vault.

Provides sub-millisecond search across markdown notes with:
- Porter stemmer + unicode61 tokenizer with diacritic normalization.
- Weighted BM25 ranking (title: 10.0, tags: 3.0, body: 1.0).
- Incremental indexing by file modification time (mtime).
- Safe fallback if SQLite FTS is unavailable.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from hive.frontmatter import extract_body, parse_date, parse_frontmatter

logger = logging.getLogger("hive.fts")

_STATUS_WEIGHTS: dict[str, float] = {
    "active": 1.5,
    "completed": 0.8,
    "accepted": 0.8,
    "superseded": 0.3,
    "archived": 0.2,
    "draft": 1.0,
}

_RECENCY_DAYS_SCALE = 365.0


def _sanitize_fts_query(query: str) -> str:
    """Sanitize user query for SQLite FTS5 MATCH syntax."""
    # Find all alphanumeric/word tokens
    tokens = re.findall(r"[\w-]+", query, re.UNICODE)
    if not tokens:
        return ""
    # Append prefix wildcard * to each token for prefix matching, join with AND
    return " ".join(f'"{t}"*' for t in tokens)


@dataclass
class FTSMatch:
    rel_path: str
    title: str
    tags: list[str]
    doc_type: str
    doc_status: str
    created: str
    score: float
    matching_lines: list[str]


class VaultFTSIndex:
    """Embedded SQLite FTS5 search index for an Obsidian vault."""

    def __init__(
        self,
        vault_path: Path,
        cache_dir: Path | None = None,
        scopes: dict[str, str] | None = None,
    ) -> None:
        self.vault_path = vault_path.resolve()
        self.scopes = scopes or {}

        if cache_dir is None:
            home = Path(os.environ.get("HIVE_CACHE_DIR", Path.home() / ".cache" / "hive"))
            cache_dir = home / "fts"

        cache_dir.mkdir(parents=True, exist_ok=True)
        vault_hash = hashlib.sha256(str(self.vault_path).encode("utf-8")).hexdigest()[:12]
        self.db_path = cache_dir / f"vault_{vault_hash}.db"

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_records (
                    rel_path TEXT PRIMARY KEY,
                    mtime REAL,
                    title TEXT,
                    tags TEXT,
                    doc_type TEXT,
                    doc_status TEXT,
                    created TEXT,
                    scope TEXT
                )
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS vault_fts USING fts5(
                    rel_path UNINDEXED,
                    title,
                    tags,
                    body,
                    tokenize = 'porter unicode61 remove_diacritics 2'
                )
            """)

    def _extract_doc(self, file_path: Path) -> dict[str, Any] | None:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

        rel_path = file_path.relative_to(self.vault_path).as_posix()
        mtime = file_path.stat().st_mtime

        fm = parse_frontmatter(content)
        title = file_path.stem.replace("-", " ").capitalize()
        tags = []
        doc_type = ""
        doc_status = ""
        created_str = ""

        # Determine scope
        scope = ""
        for sc_name, sc_dir in self.scopes.items():
            if rel_path == sc_dir or rel_path.startswith(f"{sc_dir}/"):
                scope = sc_name
                break

        body = extract_body(content)
        if fm:
            doc_type = fm.type or ""
            doc_status = fm.status or ""
            created_str = str(fm.created or "")
            if fm.tags:
                tags = [str(t).strip("#") for t in fm.tags]

        # Check for first H1 header in markdown for a better title
        h1_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if h1_match:
            title = h1_match.group(1).strip()

        return {
            "rel_path": rel_path,
            "mtime": mtime,
            "title": title,
            "tags": " ".join(tags),
            "doc_type": doc_type,
            "doc_status": doc_status,
            "created": created_str,
            "scope": scope,
            "body": body,
        }

    def sync(self) -> int:
        """Incrementally sync the vault with the FTS database. Returns updated files count."""
        if not self.vault_path.is_dir():
            return 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT rel_path, mtime FROM file_records")
            existing = {row["rel_path"]: row["mtime"] for row in cursor.fetchall()}

            seen_paths = set()
            updated_count = 0

            # Scan markdown files
            for root, dirs, files in os.walk(self.vault_path):
                # Ignore hidden dirs like .git, .obsidian, node_modules
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d not in ("node_modules", "dist", "build", ".venv", "venv")
                ]
                for f in files:
                    if f.endswith(".md"):
                        full_p = Path(root) / f
                        rel_p = full_p.relative_to(self.vault_path).as_posix()
                        seen_paths.add(rel_p)

                        mtime = full_p.stat().st_mtime
                        if rel_p not in existing or abs(existing[rel_p] - mtime) > 0.001:
                            doc = self._extract_doc(full_p)
                            if doc:
                                cursor.execute("DELETE FROM vault_fts WHERE rel_path = ?", (rel_p,))
                                cursor.execute(
                                    "DELETE FROM file_records WHERE rel_path = ?", (rel_p,)
                                )

                                cursor.execute(
                                    """
                                    INSERT INTO file_records (
                                        rel_path, mtime, title, tags,
                                        doc_type, doc_status, created, scope
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                    (
                                        doc["rel_path"],
                                        doc["mtime"],
                                        doc["title"],
                                        doc["tags"],
                                        doc["doc_type"],
                                        doc["doc_status"],
                                        doc["created"],
                                        doc["scope"],
                                    ),
                                )
                                cursor.execute(
                                    """
                                    INSERT INTO vault_fts (rel_path, title, tags, body)
                                    VALUES (?, ?, ?, ?)
                                """,
                                    (doc["rel_path"], doc["title"], doc["tags"], doc["body"]),
                                )
                                updated_count += 1

            # Remove deleted files
            deleted = set(existing.keys()) - seen_paths
            for del_p in deleted:
                cursor.execute("DELETE FROM vault_fts WHERE rel_path = ?", (del_p,))
                cursor.execute("DELETE FROM file_records WHERE rel_path = ?", (del_p,))

            conn.commit()
            return updated_count

    def update_file(self, rel_or_abs_path: Path | str) -> None:
        """Update index for a single file on write/patch."""
        path = Path(rel_or_abs_path)
        if not path.is_absolute():
            path = self.vault_path / path

        if not path.exists():
            self.remove_file(path)
            return

        doc = self._extract_doc(path)
        if not doc:
            return

        with self._get_connection() as conn:
            conn.execute("DELETE FROM vault_fts WHERE rel_path = ?", (doc["rel_path"],))
            conn.execute("DELETE FROM file_records WHERE rel_path = ?", (doc["rel_path"],))

            conn.execute(
                """
                INSERT INTO file_records (
                    rel_path, mtime, title, tags, doc_type, doc_status, created, scope
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    doc["rel_path"],
                    doc["mtime"],
                    doc["title"],
                    doc["tags"],
                    doc["doc_type"],
                    doc["doc_status"],
                    doc["created"],
                    doc["scope"],
                ),
            )
            conn.execute(
                """
                INSERT INTO vault_fts (rel_path, title, tags, body)
                VALUES (?, ?, ?, ?)
            """,
                (doc["rel_path"], doc["title"], doc["tags"], doc["body"]),
            )

    def remove_file(self, rel_or_abs_path: Path | str) -> None:
        """Remove a deleted file from the index."""
        path = Path(rel_or_abs_path)
        rel_p = (
            path.relative_to(self.vault_path).as_posix() if path.is_absolute() else path.as_posix()
        )

        with self._get_connection() as conn:
            conn.execute("DELETE FROM vault_fts WHERE rel_path = ?", (rel_p,))
            conn.execute("DELETE FROM file_records WHERE rel_path = ?", (rel_p,))

    def search(
        self,
        query: str,
        max_results: int = 10,
        type_filter: str = "",
        status_filter: str = "",
        tag_filter: str = "",
        scope: str = "",
    ) -> list[FTSMatch]:
        """Perform ranked search with BM25 scoring and status/recency boost."""
        match_query = _sanitize_fts_query(query)
        if not match_query:
            return []

        # Make sure index is synced
        self.sync()

        sql = """
            SELECT f.rel_path, f.title, f.tags, f.doc_type, f.doc_status, f.created,
                   bm25(vault_fts, 10.0, 3.0, 1.0) as bm25_score,
                   vault_fts.body as raw_body
            FROM vault_fts
            JOIN file_records f ON vault_fts.rel_path = f.rel_path
            WHERE vault_fts MATCH ?
        """
        params: list[Any] = [match_query]

        if type_filter:
            sql += " AND f.doc_type = ?"
            params.append(type_filter.strip().lower())

        if status_filter:
            sql += " AND f.doc_status = ?"
            params.append(status_filter.strip().lower())

        if tag_filter:
            sql += " AND (' ' || f.tags || ' ') LIKE ?"
            params.append(f"% {tag_filter.strip().lstrip('#').lower()} %")

        if scope:
            scope_dir = self.scopes.get(scope, scope)
            sql += " AND (f.rel_path LIKE ? OR f.scope = ?)"
            params.extend([f"{scope_dir}/%", scope])

        raw_results: list[dict[str, Any]] = []
        today = date.today()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(sql, params)
                for row in cursor.fetchall():
                    tags_list = [t for t in row["tags"].split() if t]
                    doc_status = row["doc_status"]
                    created_str = row["created"]

                    # SQLite BM25 returns negative numbers (more negative = better match)
                    # Negate so higher is better
                    base_relevance = -float(row["bm25_score"])

                    status_weight = _STATUS_WEIGHTS.get(doc_status, 1.0)
                    recency_bonus = 0.0
                    if created_str:
                        c_date = parse_date(created_str)
                        if c_date:
                            days_ago = (today - c_date).days
                            recency_bonus = max(0.0, 1.0 - days_ago / _RECENCY_DAYS_SCALE)

                    final_score = base_relevance * status_weight + recency_bonus

                    # Find matching lines
                    body = row["raw_body"] or ""
                    query_words = [w.lower() for w in re.findall(r"[\w-]+", query)]
                    matching_lines = []
                    for line in body.splitlines():
                        line_str = line.strip()
                        line_lower = line_str.lower()
                        if any(w in line_lower for w in query_words):
                            matching_lines.append(line_str)

                    raw_results.append(
                        {
                            "rel_path": row["rel_path"],
                            "title": row["title"],
                            "tags": tags_list,
                            "doc_type": row["doc_type"],
                            "doc_status": row["doc_status"],
                            "created": created_str,
                            "score": final_score,
                            "matching_lines": matching_lines,
                        }
                    )
            except sqlite3.OperationalError as err:
                logger.warning("FTS5 search error: %s", err)
                return []

        raw_results.sort(key=lambda x: x["score"], reverse=True)
        raw_results = raw_results[:max_results]

        return [
            FTSMatch(
                rel_path=r["rel_path"],
                title=r["title"],
                tags=r["tags"],
                doc_type=r["doc_type"],
                doc_status=r["doc_status"],
                created=r["created"],
                score=r["score"],
                matching_lines=r["matching_lines"],
            )
            for r in raw_results
        ]
