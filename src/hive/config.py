"""Hive configuration — pydantic-settings with env var overrides."""

import re
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# A leading drive letter (``C:``) marks a Windows absolute path; checked
# explicitly because ``str.startswith`` only catches the separator forms.
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


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
    # HIVE-118 / ADR-011: how often the daemon polls its own installed package
    # version to detect an in-place upgrade (`uv tool upgrade`). On drift it
    # clean-stops and exits non-zero so the supervisor restarts into new code.
    upgrade_poll_s: float = Field(default=30.0, gt=0.0, le=3600.0)

    @field_validator("vault_scopes")
    @classmethod
    def _validate_vault_scopes(cls, value: dict[str, str]) -> dict[str, str]:
        """Reject scope mappings that would misbehave at runtime (#159).

        A clear startup error beats undefined behaviour later:

        - Two scopes sharing a directory — auto-scan is first-match, so the
          second key would be silently unreachable.
        - A directory that is absolute or climbs out of the vault root — a
          scope dir must be a relative path *inside* the vault. The per-call
          path-boundary check would catch this lazily; this fails loudly once.
        """
        dirs = list(value.values())
        duplicates = sorted({d for d in dirs if dirs.count(d) > 1})
        if duplicates:
            raise ValueError(
                f"vault_scopes maps multiple scopes to the same directory: {duplicates}",
            )
        for key, raw in value.items():
            is_absolute = raw.startswith(("/", "\\")) or bool(_DRIVE_RE.match(raw))
            segments = re.split(r"[\\/]", raw)
            if not raw.strip() or is_absolute or ".." in segments:
                raise ValueError(
                    f"vault_scopes[{key!r}] must be a relative path inside the vault, got {raw!r}",
                )
        return value


settings = HiveSettings()
