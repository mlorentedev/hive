"""Server context — shared state for all MCP tool handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from hive._lesson_reinforcement import LessonReinforcementTracker
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
    stale_days: int
    openrouter_budget: float
    openrouter_paid_model: str
    tool_timeout: float

    def close(self) -> None:
        """Close all database connections held by this context."""
        self.tracker.close()
        self.budget.close()
        self.relevance.close()
        self.lessons.close()
