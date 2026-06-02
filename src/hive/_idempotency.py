"""At-most-once write idempotency store — ADR-013 / ADR-011 §6.2.

A client idempotency key claimed here makes a retried ``vault_write`` /
``vault_patch`` a **no-op**, so a transparent retry after a daemon restart
cancelled an in-flight write (the restart-on-upgrade finding,
``spike/upgrade_spike.py``) cannot duplicate it. Critically this is safe for
``append`` mode, where a content check cannot tell "already applied" from "two
legitimately identical appends" — the guarantee is keyed on the client token,
not the payload. Spike-proven cross-OS 3/3 (``spike/idempotency_spike.py``).
"""

from __future__ import annotations

import datetime as _dt

from hive._sqlite_tracker import _SqliteTracker


class IdempotencyStore(_SqliteTracker):
    """Applied-key store: a ``UNIQUE`` key column claimed via INSERT OR IGNORE.

    ``claim`` is atomic even under concurrency — the PRIMARY KEY constraint
    makes N racing claims of one key yield exactly one ``True`` (the single
    owner's intra-process write lock serializes the side effect it guards).
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS applied_keys (
        idem_key TEXT PRIMARY KEY,
        applied_iso TEXT NOT NULL
    );
    """

    def is_applied(self, key: str) -> bool:
        """True if *key* was already recorded — a prior call applied its write.

        Checked at the top of the write critical section (under the single
        owner's write lock) so a retry short-circuits BEFORE validation that
        would behave differently the second time (a create whose file now
        exists, a patch whose ``find`` text is already gone).
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM applied_keys WHERE idem_key = ? LIMIT 1",
                (key,),
            )
            return cur.fetchone() is not None

    def claim(self, key: str) -> bool:
        """Atomically claim *key*.

        Returns ``True`` when NEWLY claimed (the caller should apply the write)
        and ``False`` when the key was already applied (the caller should
        no-op). ``rowcount == 1`` means the INSERT happened; ``0`` means
        OR IGNORE fired on the existing key.
        """
        iso = _dt.datetime.now(_dt.UTC).isoformat(timespec="microseconds")
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO applied_keys (idem_key, applied_iso) "
                "VALUES (?, ?)",
                (key, iso),
            )
            self._conn.commit()
        return cur.rowcount == 1

    def release(self, key: str) -> None:
        """Forget *key* so a future write carrying it applies again.

        The write path records a key only AFTER its disk write succeeds (it
        checks :meth:`is_applied` first), so it needs no compensation. This is
        the primitive for explicit retention/pruning of the applied-key set —
        the store-growth follow-up noted in ADR-013.
        """
        with self._lock:
            self._conn.execute(
                "DELETE FROM applied_keys WHERE idem_key = ?", (key,),
            )
            self._conn.commit()
