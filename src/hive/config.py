"""Hive configuration — pydantic-settings with env var overrides."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HiveSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HIVE_")

    vault_path: Path = Field(
        default=Path.home() / "Projects" / "knowledge",
        validation_alias=AliasChoices("HIVE_VAULT_PATH", "VAULT_PATH"),
    )
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("HIVE_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    )
    vault_scopes: dict[str, str] = Field(
        default={"projects": "10_projects", "meta": "00_meta", "work": "50_work"},
    )
    openrouter_budget: float = 1.0
    openrouter_model: str = "qwen/qwen3-coder:free"
    openrouter_paid_model: str = "qwen/qwen3-coder"
    db_path: str = str(Path.home() / ".local" / "share" / "hive" / "worker.db")
    relevance_db_path: str = str(
        Path.home() / ".local" / "share" / "hive" / "relevance.db",
    )
    lesson_db_path: str = str(
        Path.home() / ".local" / "share" / "hive" / "lesson_reinforcement.db",
    )
    stale_threshold_days: int = 180
    http_timeout: float = 60.0
    tool_timeout: float = 60.0
    relevance_alpha: float = 0.3
    relevance_decay: float = 0.9
    relevance_epsilon: float = 0.15
    log_path: str = str(Path.home() / ".local" / "share" / "hive" / "hive.log")
    log_level: str = "INFO"


settings = HiveSettings()
