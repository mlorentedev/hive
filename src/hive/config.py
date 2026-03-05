"""Hive configuration — pydantic-settings with env var overrides."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HiveSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HIVE_")

    vault_path: Path = Path.home() / "Projects" / "knowledge"
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("HIVE_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    )
    vault_scopes: dict[str, str] = Field(
        default={"projects": "10_projects", "meta": "00_meta"},
    )
    openrouter_budget: float = 1.0
    openrouter_model: str = "qwen/qwen3-coder:free"
    openrouter_paid_model: str = "qwen/qwen3-coder"
    db_path: str = str(Path.home() / ".local" / "share" / "hive" / "worker.db")
    relevance_db_path: str = str(
        Path.home() / ".local" / "share" / "hive" / "relevance.db",
    )


settings = HiveSettings()
