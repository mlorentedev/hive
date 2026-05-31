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
        # ``agents`` is intentionally LAST: auto-scan (plain project name)
        # resolves first-match over insertion order, so appending keeps an
        # 80_agents/ dir from shadowing an existing projects/work project.
        # Override the whole mapping via HIVE_VAULT_SCOPES (JSON).
        default={
            "projects": "10_projects",
            "meta": "00_meta",
            "work": "50_work",
            "agents": "80_agents",
        },
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
    # HIVE-115 / ADR-009: WAL checkpoint daemon thread interval.
    wal_checkpoint_interval_s: float = Field(default=30.0, gt=0.0, le=3600.0)
    # HIVE-115 / ADR-010: tunable lock acquire timeout (vault git operations).
    lock_timeout_s: int = Field(default=30, ge=1, le=600)
    # HIVE-116 / ADR-012: post-kill drain window before the supervisor evicts
    # the cached ``.git/hive.lock`` filelock from ``_GIT_FILELOCKS``. Window
    # must be long enough for the SIGKILL grace to land AND for the worker
    # thread to escape ``_filelock_with_telemetry``'s ``__exit__`` naturally
    # on the happy path. Default 5.0s matches HIVE_OUTBOX_TICK_S for symmetry.
    post_kill_drain_s: float = Field(default=5.0, ge=0.5, le=30.0)


settings = HiveSettings()
