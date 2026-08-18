"""Server context — shared state for all MCP tool handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from hive._commit_queue import CommitReconciler
    from hive._fts import VaultFTSIndex
    from hive._idempotency import IdempotencyStore
    from hive._lesson_reinforcement import LessonReinforcementTracker
    from hive._lock_eviction import LockEvictionTracker
    from hive.budget import BudgetTracker
    from hive.clients import OllamaClient, OpenRouterClient
    from hive.relevance import RelevanceTracker
    from hive.usage import UsageTracker


@dataclass
class ServerContext:
    """Shared state passed to all tool handler functions."""

    vault: Path
    scopes: dict[str, str]
    tracker: UsageTracker
    budget: BudgetTracker
    ollama: OllamaClient
    openrouter: OpenRouterClient | None
    relevance: RelevanceTracker
    lessons: LessonReinforcementTracker
    lock_eviction: LockEvictionTracker
    idempotency: IdempotencyStore
    stale_days: int
    openrouter_budget: float
    openrouter_paid_model: str
    tool_timeout: float
    # HIVE-211: optional semantic backend for vault_ask (empty = disabled).
    embed_base_url: str = ""
    embed_model: str = ""
    embed_api_key: str = ""
    # HIVE-211 PR4: synthesis chat model (empty = retrieval-only, no LLM call).
    synth_model: str = ""
    # HIVE-211 PR5: called after a successful vault write with the written Path,
    # so vault_ask's index stays current without a full rebuild. None = disabled.
    index_update_hook: object = None
    started_at_iso: str = ""
    started_at_monotonic: float = 0.0
    reconciler: CommitReconciler | None = None
    # FEAT-015: SQLite FTS5 search index for sub-millisecond BM25 search.
    fts_index: VaultFTSIndex | None = None

    def close(self) -> None:
        """Close all database connections held by this context.

        The reconciler goes first and drains what it still holds, so a
        clean shutdown commits queued work instead of discarding it.
        """
        if self.reconciler is not None:
            self.reconciler.close()
        self.tracker.close()
        self.budget.close()
        self.relevance.close()
        self.lessons.close()
        self.lock_eviction.close()
        self.idempotency.close()
